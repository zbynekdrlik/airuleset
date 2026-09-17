"""#1055 P3 — api-watchdog adaptive cadence ("calm sweep" mode).

The 60s timer fired `run_once` in FULL every tick on every box: the gh/git/tmux
fan-out ran even when nothing had changed. P3 keeps the timer at 60s but decides
in STATE whether a sweep is FULL (today's behaviour) or CALM — a CALM sweep runs
the cheap local pane loop (jobs 1/1b/6 resume, always) + the recovery class
(compact_sweep, parked_wake_job, deliver_pending_done, deliver_discord_replies
while a ❓ waits, cleanup_stale_exec_markers, _owner_kill_switch_notice) and
skips every other registry job. A FULL sweep is forced by `sweep_urgent` (an
active stall/park/goal-lane state entry, a pending /compact request, or
transcript activity) OR when the last full sweep is ≥ SWEEP_CALM_S old.

These are the RED lock tests for design (a)-(f). On the pre-P3 tree none of
`sweep_urgent`, `SWEEP_CALM_S`, the `calm_ok` registry field, the `sweep: calm/
full` journal lines, `COMPACT_PENDING_HOLD_S`, or the timer `RandomizedDelaySec`
exist, so every case here fails.

Reconciliation note (see the ticket's Anchors-confirmed comment): design (a)
lists the ✅-pending glob and an unanswered ❓ as `sweep: full` reasons, but the
(f) lock tests require the ✅-pending file to be DELIVERED ON A CALM sweep
(deliver_pending_done, which deletes it on delivery) and the ❓ to KEEP
deliver_discord_replies RUNNING (not force a full sweep — a box idling on a ❓
must stay calm to meet the CPU acceptance). So `sweep_urgent` forces full only on
the ACTIVE signals; the two delivery signals are handled by their calm_ok
recovery jobs on the calm sweep.
"""

import ast
import inspect
import json
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd            # noqa: E402
import watchdog.compact as wd_compact  # noqa: E402


# --------------------------------------------------------------------------- #
# (a) sweep_urgent — pure, all-local predicate
# --------------------------------------------------------------------------- #
class TestSweepUrgentPredicate(unittest.TestCase):
    """`sweep_urgent(state, pane_stamps, stored_stamps, *, compact_pending)`
    returns a short reason string (=> FULL) or "" (=> may be calm). Pure."""

    def test_nothing_is_not_urgent(self):
        self.assertEqual(wd.sweep_urgent({}, {}, {}, compact_pending=False), "")

    def test_compact_pending_forces_full(self):
        self.assertTrue(wd.sweep_urgent({}, {}, {}, compact_pending=True))

    def test_active_api_error_entry_forces_full(self):
        # a bare-UUID api-error episode (has a "hash")
        state = {"9a8b7c6d-0000-4000-8000-000000000000":
                 {"hash": "abc", "first_seen": 1, "nudges": [1], "escalated": False}}
        self.assertTrue(wd.sweep_urgent(state, {}, {}, compact_pending=False))

    def test_a_bare_named_store_is_not_an_api_error_entry(self):
        # a NAMED job store (not a UUID, no hash) must NOT read as a stall
        state = {"backlog_cache": {"x": 1}, "usage": {"y": 2}, "dreply_z": {}}
        self.assertEqual(wd.sweep_urgent(state, {}, {}, compact_pending=False), "")

    def test_sesslimit_entry_forces_full(self):
        state = {"sesslimit:9a8b7c6d": {"resets_at": None, "first_seen": 1}}
        self.assertTrue(wd.sweep_urgent(state, {}, {}, compact_pending=False))

    def test_parked_wake_forces_full(self):
        state = {"parked_wake": {"sid": {"email": "a@b", "first_seen": 1}}}
        self.assertTrue(wd.sweep_urgent(state, {}, {}, compact_pending=False))

    def test_empty_parked_wake_is_not_urgent(self):
        self.assertEqual(wd.sweep_urgent({"parked_wake": {}}, {}, {},
                                         compact_pending=False), "")

    def test_goal_lane_stall_forces_full(self):
        state = {"goal_lane": {"sid": {"soa": 2, "soa_ts": 100}}}
        self.assertTrue(wd.sweep_urgent(state, {}, {}, compact_pending=False))

    def test_goal_lane_without_stall_is_not_urgent(self):
        state = {"goal_lane": {"sid": {"soa": 0, "lts": 100}}}
        self.assertEqual(wd.sweep_urgent(state, {}, {}, compact_pending=False), "")

    def test_transcript_activity_forces_full(self):
        # a session whose current stamp differs from the stored one = it wrote
        stored = {"sid": [111, 10]}
        cur = {"sid": [222, 20]}
        self.assertTrue(wd.sweep_urgent({}, cur, stored, compact_pending=False))

    def test_new_pane_is_activity(self):
        # a session seen this sweep but not last sweep = a new session
        self.assertTrue(wd.sweep_urgent({}, {"sid": [111, 10]}, {},
                                        compact_pending=False))

    def test_unchanged_stamp_is_not_activity(self):
        stamps = {"sid": [111, 10]}
        self.assertEqual(wd.sweep_urgent({}, dict(stamps), dict(stamps),
                                         compact_pending=False), "")

    def test_disappeared_pane_is_not_activity(self):
        # a session gone this sweep is NOT activity (handled by cleanup)
        self.assertEqual(wd.sweep_urgent({}, {}, {"sid": [111, 10]},
                                         compact_pending=False), "")


# --------------------------------------------------------------------------- #
# (b)+(c) calm vs full sweep in run_once
# --------------------------------------------------------------------------- #
def _idle_run(argv, timeout=8):
    """A fake tmux runner for a box with NO claude panes."""
    return ""


def _run(now, state_path, run=_idle_run, **kw):
    with TemporaryDirectory() as d:
        return list(wd.run_once(
            now=now, dry_run=True, run=run,
            send_fn=lambda *a, **k: None,
            projects_dir=Path(d) / "proj",
            state_path=str(state_path),
            **kw))


class TestCalmVsFullSweep(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state_path = Path(self._td.name) / "state.json"

    def _sweep_line(self, logs):
        for ln in logs:
            if ln.startswith("sweep: calm") or ln.startswith("sweep: full"):
                return ln
        return None

    def test_bootstrap_first_sweep_is_full(self):
        logs = _run(1000.0, self.state_path)
        line = self._sweep_line(logs)
        self.assertIsNotNone(line, "run_once must journal a sweep: line: %r" % logs)
        self.assertTrue(line.startswith("sweep: full"), line)

    def test_second_idle_sweep_is_calm(self):
        _run(1000.0, self.state_path)                 # bootstrap -> full
        logs = _run(1060.0, self.state_path)          # +60s, idle -> calm
        line = self._sweep_line(logs)
        self.assertTrue(line and line.startswith("sweep: calm"), "%r / %r"
                        % (line, logs))

    def test_sweep_cadence_stamp_is_written(self):
        _run(1000.0, self.state_path)
        st = json.loads(self.state_path.read_text())
        self.assertIn("sweep_cadence", st)
        self.assertEqual(st["sweep_cadence"]["last_full"], 1000.0)

    def test_idle_box_one_full_per_five_minutes(self):
        # sweeps at 60s cadence over a 5-min window: exactly one FULL, rest calm
        fulls = calms = 0
        base = 5000.0
        for i in range(6):                            # t = 0,60,120,180,240,300
            logs = _run(base + i * 60, self.state_path)
            line = self._sweep_line(logs)
            if line.startswith("sweep: full"):
                fulls += 1
            elif line.startswith("sweep: calm"):
                calms += 1
        # t=0 bootstrap full, t=60..240 calm (4), t=300 cadence full
        self.assertEqual(fulls, 2, "one bootstrap + one 5-min cadence full")
        self.assertGreaterEqual(calms, 4, "≥4 calm sweeps per 5 min on an idle box")

    def test_future_skewed_last_full_is_not_calm(self):
        # a stored last_full AHEAD of `now` (a backward clock jump, or a synthetic
        # now against a persisted real-epoch stamp) must FAIL TOWARD FULL, never
        # silently calm-skip the heavy jobs.
        self.state_path.write_text(json.dumps({
            "sweep_cadence": {"last_full": 5000.0, "pane_stamps": {}}}))
        logs = _run(1.0, self.state_path)   # now << last_full -> negative elapsed
        line = self._sweep_line(logs)
        self.assertTrue(line and line.startswith("sweep: full"), "%r" % logs)
        self.assertIn("clock-skew", line)

    def test_pending_compact_forces_full(self):
        _run(1000.0, self.state_path)                 # bootstrap
        creqp = Path(self._td.name) / "compact-requests.json"
        wd_compact.record_compact_request("sid-x", "/repo", now=1055,
                                           path=str(creqp), origin="self-callback")
        logs = _run(1060.0, self.state_path, compact_requests_path=str(creqp))
        line = self._sweep_line(logs)
        self.assertTrue(line and line.startswith("sweep: full"), "%r" % logs)
        self.assertIn("compact", line)

    def test_calm_skips_heavy_jobs_runs_recovery(self):
        # card_reconcile (heavy, not calm_ok) must be HELD skip:calm; a recovery
        # job (deliver_pending_done, calm_ok) must still run.
        _run(1000.0, self.state_path)                 # bootstrap -> full
        heavy = []
        recovery = []

        def _cr(*a, **k):
            heavy.append(1)
            return []

        def _dpd(*a, **k):
            recovery.append(1)
            return []

        with mock.patch.object(wd, "card_reconcile", _cr), \
             mock.patch.object(wd, "deliver_pending_done", _dpd):
            logs = _run(1060.0, self.state_path, card_probe=lambda *a, **k: None)
        self.assertTrue(any(ln.startswith("sweep: calm") for ln in logs), logs)
        self.assertEqual(heavy, [], "card_reconcile must be skipped on a calm sweep")
        self.assertEqual(recovery, [1], "deliver_pending_done must run on a calm sweep")

    def test_discord_replies_runs_on_every_calm_sweep_and_self_limits(self):
        # #1055 P3 review-B finding 1: deliver_discord_replies is calm_ok and runs
        # on EVERY calm sweep when discord_fetch is wired (it routes ❓ answers,
        # #298 completion-card replies, and #449 remembered channels — not just
        # the ❓ map), relying on its OWN early-return to self-limit. run_once must
        # NOT re-gate it on the ❓ map (that dropped card-replies on idle boxes).
        _run(1000.0, self.state_path)
        ran = []

        def _ddr(*a, **k):
            ran.append(1)
            return []

        # NO ❓ map given, but discord_fetch wired -> it STILL runs on the calm
        # sweep (its own guard decides whether to actually poll the network).
        with mock.patch.object(wd, "deliver_discord_replies", _ddr):
            logs = _run(1060.0, self.state_path, discord_fetch=lambda *a, **k: [])
        self.assertTrue(any(ln.startswith("sweep: calm") for ln in logs), logs)
        self.assertEqual(ran, [1],
                         "deliver_discord_replies must run on a calm sweep whenever "
                         "discord_fetch is wired (it self-limits internally)")

        # without discord_fetch its own gate keeps it off (calm or full).
        ran2 = []

        def _ddr2(*a, **k):
            ran2.append(1)
            return []

        with mock.patch.object(wd, "deliver_discord_replies", _ddr2):
            logs = _run(1120.0, self.state_path)
        self.assertTrue(any(ln.startswith("sweep: calm") for ln in logs), logs)
        self.assertEqual(ran2, [], "deliver_discord_replies is gated off without discord_fetch")

    def test_calm_sweep_runs_far_fewer_jobs_and_under_five_runner_calls(self):
        # (f) two teeth: (1) a calm sweep runs STRICTLY FEWER registry jobs than a
        # full sweep — the heavy jobs are skipped (a "job start:" line is emitted
        # per job that is NOT calm-skipped, so this count IS the executed-job set;
        # a calm mode that skipped zero jobs would make the counts equal → RED);
        # (2) on a byte-identical idle box a calm sweep issues ≤5 runner calls (the
        # design's headline bound — the injected `run` is the tmux family the P2
        # subprocess counter instruments in production; the gh/git-heavy families
        # live in the skipped registry jobs, which (1) proves are gone).
        full_calls = []
        logs_full = _run(1000.0, self.state_path,
                         run=lambda *a, **k: (full_calls.append(1), "")[1])
        self.assertTrue(any(ln.startswith("sweep: full") for ln in logs_full), logs_full)
        full_jobs = [ln for ln in logs_full if ln.startswith("job start:")]

        calm_calls = []
        logs_calm = _run(1060.0, self.state_path,
                         run=lambda *a, **k: (calm_calls.append(1), "")[1])
        self.assertTrue(any(ln.startswith("sweep: calm") for ln in logs_calm), logs_calm)
        calm_jobs = [ln for ln in logs_calm if ln.startswith("job start:")]

        self.assertLess(len(calm_jobs), len(full_jobs),
                        "a calm sweep must run strictly fewer registry jobs than a "
                        "full sweep (calm=%d full=%d)" % (len(calm_jobs), len(full_jobs)))
        self.assertLessEqual(len(calm_jobs), 6,
                             "only the <=6 calm_ok recovery jobs may run on a calm sweep")
        self.assertLessEqual(len(calm_calls), 5,
                             "a calm idle sweep must issue <=5 runner calls, got %d: %r"
                             % (len(calm_calls), calm_calls))


# --------------------------------------------------------------------------- #
# (f)-T1 — a stalled api-error pane is resumed even on a CALM sweep
# --------------------------------------------------------------------------- #
def _assistant_api_error(text):
    return {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _typing_send_verified(pid, text, run=None, tpath=None, sleep_fn=None,
                          logs=None, user_authored=False, nudge=None, state=None):
    run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    return True


class TestResumeStillFiresOnCalmSweep(unittest.TestCase):
    CWD = "/home/newlevel/devel/camera-box"
    PANE = "%7"
    SID = "9a8b7c6d-0000-4000-8000-000000000602"
    IDLE = "● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n"

    def setUp(self):
        self._sv = mock.patch.object(wd, "send_verified", _typing_send_verified)
        self._sv.start()
        self.addCleanup(self._sv.stop)

    def test_401_on_a_calm_sweep_still_resumes(self):
        now = 1_800_000_000.0
        with TemporaryDirectory() as d:
            proj = Path(d) / "projects"
            enc = wd.encode_project_dir(self.CWD)
            (proj / enc).mkdir(parents=True)
            tpath = proj / enc / (self.SID + ".jsonl")
            with open(tpath, "w") as f:
                f.write(json.dumps(_assistant_api_error(
                    "API Error: 529 overloaded — retrying")) + "\n")
            # a STALLED session does not write, so its mtime is stable: pre-seed
            # the cadence stamp to match so there is NO transcript activity, plus
            # a recent last_full — the sweep is CALM, yet the pane loop must still
            # resume the api-error pane.
            os.utime(tpath, (now - 700, now - 700))
            stt = tpath.stat()
            state_path = Path(d) / "state.json"
            state_path.write_text(json.dumps({
                "sweep_cadence": {
                    "last_full": now - 60,
                    "pane_stamps": {self.SID: [stt.st_mtime_ns, stt.st_size]},
                }}))
            keys = []

            def fake_run(argv, timeout=8):
                j = " ".join(argv)
                if "list-panes" in j:
                    return "%s\tclaude\t%s\n" % (self.PANE, self.CWD)
                if "display-message" in j:
                    if "pane_in_mode" in j:
                        return "0"
                    if "session_group" in j or argv[-1] == "#S":
                        return "zbynek"
                    return ""
                if "capture-pane" in j:
                    return self.IDLE
                if "send-keys" in j:
                    keys.append(argv)
                    return ""
                return ""

            logs = list(wd.run_once(
                now=now, dry_run=False, run=fake_run, send_fn=lambda *a, **k: None,
                projects_dir=proj, state_path=state_path,
                pending_prefix=str(Path(d) / "pending-"),
                grace=300, interval=300, max_nudges=3))

        self.assertTrue(any(ln.startswith("sweep: calm") for ln in logs),
                        "expected a CALM sweep: %r" % logs)
        typed = [a[-1] for a in keys if "send-keys" in " ".join(a) and "-l" in a]
        self.assertIn(wd.NUDGE_TEXT, typed,
                      "the api-error resume must fire even on a calm sweep: %r" % keys)


# --------------------------------------------------------------------------- #
# (c) the calm_ok registry set is pinned (static, ast over run_once source)
# --------------------------------------------------------------------------- #
def _calm_ok_labels():
    """Labels of `_add(...)` calls in run_once carrying `calm_ok=True`."""
    tree = ast.parse(inspect.getsource(wd.run_once))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_add" and node.args):
            first = node.args[0]
            label = first.value if isinstance(first, ast.Constant) else None
            if not isinstance(label, str):
                continue
            for kw in node.keywords:
                if kw.arg == "calm_ok":
                    v = getattr(kw.value, "value", None)
                    if v is True:
                        out.append(label)
    return out


class TestCalmOkRegistrySet(unittest.TestCase):
    # The ≤60s recovery class (P3 root-cause) minus the pane-loop resume:
    # RECOVERY_NUDGE_KINDS producers among registry jobs (compact_sweep->compact,
    # parked_wake_job->wake-parked) + the two file-driven deliveries + the two
    # local hygiene/notice jobs. goal-arm/goal-disarm producers tolerate minutes
    # and are NOT calm_ok.
    EXPECTED = {
        "compact_sweep",
        "parked_wake_job",
        "deliver_pending_done",
        "deliver_discord_replies",
        "cleanup_stale_exec_markers",
        "_owner_kill_switch_notice",
    }

    def test_calm_ok_set_is_exactly_the_recovery_class(self):
        got = set(_calm_ok_labels())
        self.assertEqual(got, self.EXPECTED,
                         "the calm_ok=True set drifted from the P3 recovery class")

    def test_heavy_jobs_are_not_calm_ok(self):
        got = set(_calm_ok_labels())
        for lbl in ("card_reconcile", "goal_lane_sweep", "goal_sweep",
                    "goal_dark_watch", "goal_question_repoke_watch",
                    "delivery_stall_watch", "resource_guard_verify"):
            self.assertNotIn(lbl, got, "%s must NOT be calm_ok" % lbl)


# --------------------------------------------------------------------------- #
# (d) COMPACT_PENDING_HOLD is seconds, cadence-independent
# --------------------------------------------------------------------------- #
class TestCompactPendingHoldSeconds(unittest.TestCase):
    def test_seconds_constant_exists_and_is_120(self):
        self.assertEqual(wd_compact.COMPACT_PENDING_HOLD_S, 120)

    def test_sweeps_count_constant_is_gone(self):
        self.assertFalse(hasattr(wd_compact, "COMPACT_PENDING_HOLD_SWEEPS"),
                         "the sweep-count bound must be replaced by seconds (#1055 P3 d)")
        # the nominal-interval constant only ever fed the old sweep-count bound;
        # it has no other consumer, so it is removed too (mvp-philosophy).
        self.assertFalse(hasattr(wd_compact, "COMPACT_SWEEP_INTERVAL_S"),
                         "the dead nominal-interval constant must be removed (#1055 P3 d)")

    def test_hold_behaviour_preserved_at_120s(self):
        with TemporaryDirectory() as d:
            p = str(Path(d) / "creq.json")
            wd_compact.record_compact_request("sid", "/r", now=1000,
                                              path=p, origin="self-callback")
            # 30s < 120s -> hold; 200s > 120s -> no hold (byte-identical to the
            # old 2×60s bound)
            self.assertTrue(wd_compact.pending_compact_hold("sid", 1030, path=p))
            self.assertFalse(wd_compact.pending_compact_hold("sid", 1200, path=p))


# --------------------------------------------------------------------------- #
# (e) timer template spreads the fleet over the minute
# --------------------------------------------------------------------------- #
class TestTimerRandomizedDelay(unittest.TestCase):
    TMPL = REPO / "settings" / "api-watchdog.timer.template"

    def test_randomized_delay_present(self):
        t = self.TMPL.read_text()
        self.assertIn("RandomizedDelaySec=20s", t)

    def test_keeps_cadence_and_accuracy(self):
        t = self.TMPL.read_text()
        self.assertIn("OnUnitActiveSec=60s", t)
        self.assertIn("AccuracySec=10s", t)

    def test_cli_config_validator_stays_green(self):
        import cli_config
        errs = [e for e in cli_config._validate_watchdog()
                if "timer template" in e]
        self.assertEqual(errs, [], "timer template validation must stay green: %r" % errs)


if __name__ == "__main__":
    unittest.main()
