"""#855 — double `/compact`: #822 delivered through CC's non-idempotent
type-ahead queue (one queued `/compact` -> two submits).

Fix (design points 1-5): type `/compact` ONLY into an idle pane —
- a running turn (the existing `_classify_boundary`->busy classifier) is refused
  with `skip:turn-running`, NO keystroke, record left PENDING (re-polled);
- `QUEUED` is unreachable by construction, and a residual-race `queued` is
  treated DEFENSIVELY as a real send (writes `compact-delivered`, arms the veto);
- a NEW 120s `recently-compacted` veto (reading `compact-delivered.json`, NOT
  superseded by the drained-boundary origin) blocks any 2nd `/compact` within
  2 min of a delivered one;
- `skip:turn-running` prints the boundary-hold hint (the session must produce
  the accepted Stop that yields the idle window the next sweep types into);
- doctrine: completion-report.md #822 paragraph reworded (typed at idle, not
  drained from the queue).
"""

import sys
import json
import time
import types
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import compact  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMPLETION = ROOT / "modules" / "core" / "completion-report.md"
INTERNALS = ROOT / ".claude" / "rules" / "internals-watchdog.md"

CB_IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
CB_BUSY = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
           "  ctx ███░  caveman:lite\n")


def _isolate(tc):
    d = TemporaryDirectory()
    tc.addCleanup(d.cleanup)
    reqp = Path(d.name) / "compact-requests.json"
    delp = Path(d.name) / "compact-delivered.json"
    syncp = Path(d.name) / "compact-sync.log"
    queuedp = Path(d.name) / "compact-queued.json"
    for name, path in (("compact_requests_path", reqp),
                       ("compact_delivered_path", delp),
                       ("compact_sync_log_path", syncp),
                       ("compact_queued_path", queuedp)):
        p = m.patch.object(compact, name, return_value=path)
        p.start()
        tc.addCleanup(p.stop)
    return reqp, delp, syncp


def _marker(base, cwd, sid):
    d = Path(base) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    p.write_text(json.dumps(
        {"type": "assistant", "message": {"id": "m1", "content": ""}}) + "\n")
    return p


class _StaticRun:
    """Serves ONE pane and a static capture; records every send-keys argv."""

    def __init__(self, cwd, capture):
        self.cwd = cwd
        self.capture = capture
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "%9\tclaude\t" + self.cwd + "\t111"
        if "display-message" in j:
            return "0" if argv[-1] == "#{pane_in_mode}" else "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            return ""
        if "capture-pane" in j:
            return self.capture
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class _SubmitThenBusyRun:
    """BARE (idle) until the `/compact` Enter lands, then renders `busy` (a
    running-turn spinner) — models the residual t0->t+1s queue race that
    `_compact_post_send_classify` classifies `queued`."""

    def __init__(self, cwd, busy):
        self.cwd = cwd
        self.busy = busy
        self.submitted = False
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "%9\tclaude\t" + self.cwd + "\t111"
        if "display-message" in j:
            return "0" if argv[-1] == "#{pane_in_mode}" else "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            if argv[-1] == "Enter":
                self.submitted = True
            return ""
        if "capture-pane" in j:
            return self.busy if self.submitted else CB_IDLE
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


CWD = "/home/newlevel/devel/dc855"
SID = "sess-dc855"


class TestRunningTurnRefused(unittest.TestCase):
    """Point 1 — a running turn is refused with `skip:turn-running`, NO
    keystroke, record left pending (never queued behind the turn)."""

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate(self)
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, capture, origin="self-callback", now=None):
        proj = self._dir()
        _marker(proj, CWD, SID)
        run = _StaticRun(CWD, capture)
        word = compact.deliver_compact(
            SID, CWD, origin=origin, run=run, projects_dir=proj,
            delivered_path=self.delp, now=now if now is not None else time.time())
        return word, run

    def test_running_turn_is_skip_turn_running(self):
        word, run = self._go(CB_BUSY)
        self.assertEqual(word, "skip:turn-running")

    def test_running_turn_types_nothing(self):
        # The lock: no code path types COMPACT_TEXT while the classifier is busy.
        word, run = self._go(CB_BUSY)
        self.assertEqual(run.sent, [])
        self.assertNotIn("/compact", run.typed())

    def test_skip_turn_running_is_not_terminal(self):
        # A refused running-turn record stays PENDING so the next idle sweep
        # types it — it must NOT be a terminal disposition word.
        self.assertNotIn("skip:turn-running", compact._COMPACT_TERMINAL_WORDS)

    def test_skip_turn_running_hold_extends(self):
        # A held boundary that reads a running turn refreshes ts (#741) so it
        # never ages out of the 30-min cap while it waits for an idle window.
        self.assertIn("skip:turn-running", compact._COMPACT_HOLD_EXTEND_WORDS)

    def test_skip_turn_running_prints_boundary_hold_hint(self):
        # The session must produce the accepted Stop (boundary hold) that yields
        # the idle window the next sweep types into.
        self.assertIn("skip:turn-running", compact._COMPACT_HOLD_HINT_WORDS)

    def test_sweep_hold_extends_a_running_turn(self):
        proj = self._dir()
        _marker(proj, CWD, SID)
        T = 1_000_000.0
        compact.record_compact_request(SID, CWD, now=T, path=self.reqp,
                                       origin="self-callback")
        run = _StaticRun(CWD, CB_BUSY)
        logs = compact.compact_sweep(T + 5 * 60, run=run, projects_dir=proj,
                                     requests_path=self.reqp,
                                     delivered_path=self.delp)
        self.assertTrue(any("skip:turn-running" in ln for ln in logs), logs)
        self.assertEqual(
            compact.load_compact_requests(self.reqp)[SID]["ts"], int(T + 5 * 60),
            "a running turn must hold-extend the boundary (#741/#855)")
        self.assertEqual(run.sent, [], "no keystroke into a running turn")


class TestRecentlyCompactedVeto(unittest.TestCase):
    """Point 3 — a 120s `recently-compacted` veto blocks a 2nd `/compact`
    within 2 min of a delivered one, even for a drained-boundary origin."""

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate(self)
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _go(self, origin, now):
        proj = self._dir()
        _marker(proj, CWD, SID)
        run = _StaticRun(CWD, CB_IDLE)
        word = compact.deliver_compact(
            SID, CWD, origin=origin, run=run, projects_dir=proj,
            delivered_path=self.delp, now=now)
        return word, run

    def test_veto_blocks_second_within_120s_self_callback(self):
        now = time.time()
        compact.mark_compact_delivery_ts(SID, now=now - 60, path=self.delp)
        word, run = self._go("self-callback", now)
        self.assertEqual(word, "skip:recently-compacted")
        self.assertEqual(run.sent, [], "no 2nd /compact within 2 min")

    def test_veto_not_superseded_by_boundary_origin(self):
        # The drained-boundary (`self-callback`) origin supersedes the 30-min
        # cooldown (#805) — but NOT this 120s anti-double veto.
        now = time.time()
        compact.mark_compact_delivery_ts(SID, now=now - 30, path=self.delp)
        word, run = self._go("self-callback", now)
        self.assertEqual(word, "skip:recently-compacted")

    def test_veto_lifts_after_120s(self):
        # 200s after a delivery: the 120s veto is clear; the 30-min cooldown is
        # superseded by the drained-boundary origin -> the compact is delivered.
        now = time.time()
        compact.mark_compact_delivery_ts(SID, now=now - 200, path=self.delp)
        word, run = self._go("self-callback", now)
        self.assertEqual(word, "sent")
        self.assertIn("/compact", run.typed())

    def test_no_prior_delivery_is_not_vetoed(self):
        now = time.time()
        word, run = self._go("self-callback", now)
        self.assertEqual(word, "sent")


class TestDefensiveQueued(unittest.TestCase):
    """Point 1 defensive branch — if a residual race still produces a `queued`
    post-send classification, the queued `/compact` WILL drain, so it is treated
    as a real send: `compact-delivered` is written (arming the 120s veto) and
    the word is `sent`, never a false `queued` that leaves no cooldown."""

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate(self)
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_queued_race_is_treated_as_sent(self):
        proj = self._dir()
        _marker(proj, CWD, SID)
        run = _SubmitThenBusyRun(CWD, CB_BUSY)
        word = compact.deliver_compact(
            SID, CWD, origin="self-callback", run=run, projects_dir=proj,
            delivered_path=self.delp, now=time.time())
        self.assertEqual(word, "sent")

    def test_queued_race_starts_the_cooldown(self):
        proj = self._dir()
        _marker(proj, CWD, SID)
        now = time.time()
        run = _SubmitThenBusyRun(CWD, CB_BUSY)
        compact.deliver_compact(
            SID, CWD, origin="self-callback", run=run, projects_dir=proj,
            delivered_path=self.delp, now=now)
        # arming the 120s veto for real: a delivery ts was recorded.
        self.assertTrue(compact.compact_delivery_in_cooldown(
            SID, now + 1, path=self.delp))

    def test_queued_race_logs_defensively(self):
        proj = self._dir()
        _marker(proj, CWD, SID)
        run = _SubmitThenBusyRun(CWD, CB_BUSY)
        compact.deliver_compact(
            SID, CWD, origin="self-callback", run=run, projects_dir=proj,
            delivered_path=self.delp, now=time.time())
        self.assertIn("QUEUED-DEFENSIVE", self.syncp.read_text())

    def test_deliver_compact_never_returns_queued(self):
        # QUEUED is unreachable as a `deliver_compact` disposition under #855.
        proj = self._dir()
        _marker(proj, CWD, SID)
        run = _SubmitThenBusyRun(CWD, CB_BUSY)
        word = compact.deliver_compact(
            SID, CWD, origin="self-callback", run=run, projects_dir=proj,
            delivered_path=self.delp, now=time.time())
        self.assertNotEqual(word, "queued")

    def test_defensive_path_never_marks_queued_since(self):
        # 🔵5 absence-lock: the defensive queued->sent path treats it as a real
        # send (compact-delivered), so it must NEVER write the queued-since store
        # (there is nothing to drain via the boundary-hold hint). A mutant
        # re-adding `mark_compact_queued_ts` on this path fails this.
        proj = self._dir()
        _marker(proj, CWD, SID)
        run = _SubmitThenBusyRun(CWD, CB_BUSY)
        compact.deliver_compact(
            SID, CWD, origin="self-callback", run=run, projects_dir=proj,
            delivered_path=self.delp, now=time.time())
        self.assertIsNone(compact.compact_queued_since(SID))


def _args(**kw):
    kw.setdefault("self", False)
    kw.setdefault("record", False)
    kw.setdefault("status", False)
    kw.setdefault("session", "")
    kw.setdefault("cwd", "")
    kw.setdefault("origin", "")
    return types.SimpleNamespace(**kw)


class TestSelfPrintsHintOnTurnRunning(unittest.TestCase):
    """`compact-request --self` prints the boundary-hold command when the
    boundary `/compact` was refused because a turn is running."""

    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, fn in (("compact_requests_path", "r.json"),
                         ("compact_delivered_path", "d.json"),
                         ("compact_sync_log_path", "s.log"),
                         ("compact_queued_path", "q.json")):
            p = m.patch.object(compact, name, return_value=Path(d.name) / fn)
            p.start()
            self.addCleanup(p.stop)

    def _self_output(self, deliver_word):
        with m.patch.object(compact, "resolve_self_pane",
                            return_value=("%3", "/cwd", "sess-bh")):
            with m.patch.object(compact, "deliver_compact",
                                return_value=deliver_word):
                buf = []
                with m.patch("sys.stdout") as out:
                    out.write = lambda s: buf.append(s)
                    with m.patch("time.sleep", lambda *a, **k: None):
                        airuleset.cmd_compact_request(_args(self=True))
        return "".join(buf)

    def test_self_prints_the_command_on_turn_running(self):
        out = self._self_output("skip:turn-running")
        self.assertTrue(out.startswith("skip:turn-running"), out)
        self.assertIn("sleep 45 && echo boundary-hold", out)
        self.assertIn("run_in_background", out)

    def test_hint_is_conditional_on_an_armed_goal(self):
        # 🟡3: `--self` runs mid-turn so `skip:turn-running` fires for EVERY
        # session — the hint must tell a served, non-/goal session it may IGNORE
        # it (it does not need the boundary hold; the sweep delivers once its
        # turn ends and the pane goes idle).
        out = self._self_output("skip:turn-running")
        self.assertIn("ARMED", out)
        self.assertIn("IGNORE", out)


class TestDoctrine(unittest.TestCase):
    def test_completion_report_822_reworded_typed_at_idle(self):
        t = COMPLETION.read_text(encoding="utf-8")
        idx = t.find("#822")
        self.assertGreater(idx, 0)
        window = t[idx:idx + 900]
        # the #822 paragraph now says the /compact is TYPED at the next idle
        # poll, not "drained" from CC's type-ahead queue.
        self.assertIn("idle", window)
        self.assertNotIn("accepted Stop drains it", window)

    def test_internals_watchdog_has_855_lesson(self):
        t = INTERNALS.read_text(encoding="utf-8")
        self.assertIn("#855", t)


if __name__ == "__main__":
    unittest.main()
