"""#1203 — watchdog Job 54 `model_restore`: a pane whose transcript shows a Claude
Code model FALLBACK gets `/model <MANAGED_MODEL>` typed through verified
delivery (always-on `model-restore` recovery kind), one resume line once the
switch is recorded, and ONE re-review ticket after the next reply is confirmed on
the managed model. Every dependency is injected: no real pane, no real gh.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import model_fallback as mf  # noqa: E402
from watchdog import idle_pane, model_restore as mr, nudge_gate, tmux_io  # noqa: E402
from watchdog.send_outcome import SendOutcome  # noqa: E402

MANAGED = "claude-opus-5-5[1m]"
NOW = 1_800_000_000


def _assistant(model, content=None, uuid="a", ts="2026-09-28T14:51:50Z"):
    return {"type": "assistant", "uuid": uuid, "timestamp": ts,
            "message": {"model": model, "content": content or [{"type": "text", "text": "x"}]}}


def _marker(uuid="m1", ts="2026-09-28T14:51:50Z"):
    return _assistant("claude-opus-4-8", [{"type": "fallback",
                                           "from": {"model": "claude-opus-5-5"},
                                           "to": {"model": "claude-opus-4-8"}}], uuid, ts)


def _model_cmd(args=MANAGED):
    return {"type": "user", "message": {"content": "<command-name>/model</command-name>\n"
            "<command-message>model</command-message>\n<command-args>%s</command-args>" % args}}


class Harness:
    def __init__(self, testcase):
        self.dir = tempfile.mkdtemp(prefix="mr1203-")
        testcase.addCleanup(shutil.rmtree, self.dir, True)
        self.tpath = os.path.join(self.dir, "sid1.jsonl")
        self.cwd = os.path.join(self.dir, "proj")
        os.makedirs(self.cwd)
        self.ready_ok = True
        self.ready_calls = []
        self.typed = []
        self.filed = []
        self.outcome = SendOutcome("submitted")
        self.file_result = (True, "https://github.com/o/r/issues/7", "")

    def write(self, entries):
        with open(self.tpath, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def append(self, entries):
        with open(self.tpath, "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def ready(self, pid, cwd, live_ok):
        self.ready_calls.append(live_ok)
        return (self.ready_ok, "" if self.ready_ok else "busy-pane", "sid1", self.tpath)

    def deliver(self, pid, tpath, text):
        self.typed.append(text)
        return self.outcome

    def file_ticket(self, rec, managed, now):
        self.filed.append(dict(rec))
        return self.file_result

    def sweep(self, state, now, dry_run=False, panes=None):
        return mr.restore_job(
            now, state, panes if panes is not None else [("%1", self.cwd)],
            managed=MANAGED, find_transcript=lambda cwd, ex=None: self.tpath,
            read_state=mf.tail_state, verdict=mf.verdict, short_name=mf.short_name,
            ready=self.ready, deliver=self.deliver, file_ticket=self.file_ticket,
            outcome_kind=idle_pane.outcome_kind, delivered=idle_pane.DELIVERED,
            typed_not_delivered=idle_pane.TYPED_NOT_DELIVERED, dry_run=dry_run,
            handled=set())


class RecoveryKind(unittest.TestCase):
    def test_model_restore_is_an_always_on_recovery_kind(self):
        self.assertIn("model-restore", tmux_io.RECOVERY_NUDGE_KINDS)
        self.assertEqual(tmux_io.RECOVERY_NUDGE_KINDS, nudge_gate.RECOVERY_NUDGE_KINDS)
        self.assertNotIn("model-restore", tmux_io.MACHINE_NUDGE_KINDS)
        self.assertEqual(mr.NUDGE_KIND, "model-restore")

    def test_never_withheld_by_the_kind_switch(self):
        env = dict(os.environ)
        env.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
        home = tempfile.mkdtemp(prefix="mr1203-home-")
        self.addCleanup(shutil.rmtree, home, True)
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(tmux_io.nudges_enabled("model-restore", home=home))

    def test_cadence_gate_never_holds_it(self):
        st = {}
        nudge_gate.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertTrue(nudge_gate.gate_ok(st, "s", "model-restore", NOW + 1))


class TypeModelCommand(unittest.TestCase):
    def setUp(self):
        self.h = Harness(self)

    def test_fallback_types_model_command_once_ignoring_turn_liveness(self):
        self.h.write([_assistant("claude-opus-5-5"), _marker()])
        state = {}
        logs = self.h.sweep(state, NOW)
        self.assertEqual(self.h.typed, ["/model " + MANAGED])
        self.assertEqual(self.h.ready_calls, [True])    # live_ok: the gate blocks tools
        self.assertTrue(any("FALLBACK claude-opus-5-5 -> claude-opus-4-8" in ln for ln in logs), logs)
        rec = state["model_restore"]["sid1"]
        self.assertEqual(rec["attempts"], 1)
        self.assertEqual(rec["since"], "2026-09-28T14:51:50Z")
        self.assertTrue(rec["since_exact"])

    def test_busy_pane_holds_and_types_nothing(self):
        self.h.write([_marker()])
        self.h.ready_ok = False
        state = {}
        logs = self.h.sweep(state, NOW)
        self.assertEqual(self.h.typed, [])
        self.assertTrue(any("hold:busy-pane" in ln for ln in logs), logs)
        self.assertEqual(state["model_restore"]["sid1"]["attempts"], 0)

    def test_retry_spacing_then_attempt_cap_gives_up_once(self):
        self.h.write([_marker()])
        state = {}
        self.h.sweep(state, NOW)
        logs = self.h.sweep(state, NOW + 10)
        self.assertEqual(len(self.h.typed), 1)
        self.assertTrue(any("hold:retry" in ln for ln in logs), logs)
        self.h.sweep(state, NOW + mr.RETRY_S)
        self.h.sweep(state, NOW + 2 * mr.RETRY_S)
        self.assertEqual(len(self.h.typed), mr.MAX_ATTEMPTS)
        logs = self.h.sweep(state, NOW + 3 * mr.RETRY_S)
        self.assertTrue(any("GAVE UP" in ln for ln in logs), logs)
        logs = self.h.sweep(state, NOW + 4 * mr.RETRY_S)
        self.assertFalse(any("GAVE UP" in ln for ln in logs), logs)
        self.assertEqual(len(self.h.typed), mr.MAX_ATTEMPTS)

    def test_not_typed_outcome_does_not_count_an_attempt(self):
        self.h.write([_marker()])
        self.h.outcome = SendOutcome("not-typed")
        state = {}
        self.h.sweep(state, NOW)
        self.assertEqual(state["model_restore"]["sid1"]["attempts"], 0)

    def test_non_claude_model_is_never_touched(self):
        self.h.write([_assistant("impl-main")])
        logs = self.h.sweep({}, NOW)
        self.assertEqual(self.h.typed, [])
        self.assertTrue(any("never touched" in ln for ln in logs), logs)

    def test_healthy_pane_is_left_alone(self):
        self.h.write([_assistant("claude-opus-5-5")])
        state = {}
        self.assertEqual(self.h.sweep(state, NOW), [])
        self.assertEqual(self.h.typed, [])
        self.assertEqual(state.get("model_restore"), {})

    def test_dry_run_types_files_and_persists_nothing(self):
        self.h.write([_marker()])
        state = {}
        logs = self.h.sweep(state, NOW, dry_run=True)
        self.assertEqual(self.h.typed, [])
        self.assertNotIn("model_restore", state)
        self.assertTrue(any("[dry-run]" in ln for ln in logs), logs)


class ResumeConfirmAndTicket(unittest.TestCase):
    def setUp(self):
        self.h = Harness(self)
        self.h.write([_marker()])
        self.state = {}
        self.h.sweep(self.state, NOW)                  # types /model
        self.h.append([_model_cmd()])                  # CC recorded the switch

    def test_resume_line_after_grace_only_into_a_quiet_pane(self):
        logs = self.h.sweep(self.state, NOW + 60)
        self.assertTrue(any("awaiting the first reply" in ln for ln in logs), logs)
        self.h.sweep(self.state, NOW + 60 + 10)
        self.assertEqual(len(self.h.typed), 1)          # grace not over yet
        self.h.sweep(self.state, NOW + 60 + mr.RESUME_GRACE_S)
        self.assertEqual(len(self.h.typed), 2)
        self.assertIn("opus5.5", self.h.typed[1])
        self.assertEqual(self.h.ready_calls[-1], False)  # full gate set incl. liveness
        self.h.sweep(self.state, NOW + 60 + 3 * mr.RESUME_GRACE_S)
        self.assertEqual(len(self.h.typed), 2)          # one resume line per episode

    def test_confirmed_restore_files_one_ticket(self):
        self.h.append([_assistant("claude-opus-5-5", uuid="new")])
        logs = self.h.sweep(self.state, NOW + 300)
        self.assertTrue(any("RESTORED" in ln for ln in logs), logs)
        self.assertTrue(any("re-review ticket filed" in ln for ln in logs), logs)
        self.assertEqual(len(self.h.filed), 1)
        self.assertEqual(self.h.filed[0]["to"], "claude-opus-4-8")
        self.h.sweep(self.state, NOW + 400)
        self.assertEqual(len(self.h.filed), 1)          # at most one per fallback

    def test_failed_filing_is_retried_bounded(self):
        self.h.file_result = (False, None, "gh down")
        self.h.append([_assistant("claude-opus-5-5", uuid="new")])
        for i in range(mr.FILE_MAX_TRIES + 2):
            self.h.sweep(self.state, NOW + 300 + i)
        self.assertEqual(len(self.h.filed), mr.FILE_MAX_TRIES)

    def test_reply_still_on_fallback_retypes_model(self):
        self.h.append([_assistant("claude-opus-4-8", uuid="again")])
        self.h.sweep(self.state, NOW + mr.RETRY_S)
        self.assertEqual(self.h.typed, ["/model " + MANAGED] * 2)

    def test_new_fallback_after_restore_is_a_new_episode(self):
        self.h.append([_assistant("claude-opus-5-5", uuid="new")])
        self.h.sweep(self.state, NOW + 300)
        self.h.append([_marker(uuid="m2", ts="2026-09-30T10:00:00Z")])
        logs = self.h.sweep(self.state, NOW + 400)
        rec = self.state["model_restore"]["sid1"]
        self.assertIsNone(rec["restored_at"])
        self.assertEqual(rec["since"], "2026-09-30T10:00:00Z")
        self.assertEqual(self.h.typed[-1], "/model " + MANAGED)
        self.assertTrue(any("FALLBACK" in ln for ln in logs), logs)


class TicketComposition(unittest.TestCase):
    REC = {"from": "claude-opus-5-5", "to": "claude-opus-4-8",
           "since": "2026-09-28T14:51:50Z", "since_exact": True,
           "restored_at": NOW, "cwd": "/repo", "episode": "marker:m1", "proven": True}

    def test_body_names_window_models_and_commit_range(self):
        title, body = mr.compose_ticket(self.REC, MANAGED, NOW,
                                        [("aaa111", "first"), ("bbb222", "second")])
        self.assertIn("claude-opus-4-8", title)
        self.assertIn("2026-09-28T14:51:50Z", title)
        self.assertIn("from `claude-opus-5-5` to `claude-opus-4-8`", body)
        self.assertIn("oldest `aaa111`, newest `bbb222`", body)
        self.assertNotIn("^..", body)                  # never a cross-branch range claim
        self.assertIn("- `bbb222` second", body)
        self.assertIn("airuleset#1203", body)
        self.assertIn(mr.EPISODE_TAG + "marker:m1", body)   # the dedup search key

    def test_inexact_start_is_labelled(self):
        rec = dict(self.REC, since_exact=False)
        _t, body = mr.compose_ticket(rec, MANAGED, NOW, [])
        self.assertIn("first seen by the watchdog", body)
        self.assertIn("none", body)

    def test_file_ticket_runs_git_log_and_native_gh_in_the_pane_cwd(self):
        d = tempfile.mkdtemp(prefix="mr1203-cwd-")
        self.addCleanup(shutil.rmtree, d, True)
        calls = []

        class R:
            def __init__(self, rc, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        def fake(argv, **kw):
            calls.append((argv, kw.get("cwd")))
            if argv[0] == "git":
                return R(0, "aaa111\tfirst\nbbb222\tsecond\n")
            if argv[:3] == ["gh", "issue", "list"]:
                return R(0, "")                    # no earlier filing of this episode
            return R(0, "https://github.com/o/r/issues/9\n")

        ok, ref, err = mr.file_ticket(dict(self.REC, cwd=d), MANAGED, NOW, sub_run=fake)
        self.assertTrue(ok, err)
        self.assertEqual(ref, "https://github.com/o/r/issues/9")
        self.assertEqual(calls[0][0][:3], ["git", "log", "HEAD"])
        self.assertIn("--since=2026-09-28T14:51:50Z", calls[0][0])
        self.assertEqual(calls[1][0][:3], ["gh", "issue", "list"])   # dedup search first
        self.assertIn('"marker:m1" in:body', calls[1][0])
        self.assertEqual(calls[2][0][:3], ["gh", "issue", "create"])
        self.assertNotIn("-R", calls[2][0])            # the pane repo's native gh
        self.assertEqual({c[1] for c in calls}, {d})
        body = calls[2][0][calls[2][0].index("--body") + 1]
        self.assertIn("oldest `aaa111`, newest `bbb222`", body)

    def test_file_ticket_reuses_an_already_filed_episode(self):
        d = tempfile.mkdtemp(prefix="mr1203-cwd-")
        self.addCleanup(shutil.rmtree, d, True)
        calls = []

        class R:
            def __init__(self, rc, out=""):
                self.returncode, self.stdout, self.stderr = rc, out, ""

        def fake(argv, **kw):
            calls.append(argv)
            if argv[:3] == ["gh", "issue", "list"]:
                return R(0, "https://github.com/o/r/issues/5\n")
            return R(0, "")

        ok, ref, _err = mr.file_ticket(dict(self.REC, cwd=d), MANAGED, NOW, sub_run=fake)
        self.assertTrue(ok)
        self.assertEqual(ref, "https://github.com/o/r/issues/5")
        self.assertFalse(any(a[:3] == ["gh", "issue", "create"] for a in calls))

    def test_file_ticket_reports_gh_failure(self):
        d = tempfile.mkdtemp(prefix="mr1203-cwd-")
        self.addCleanup(shutil.rmtree, d, True)

        class R:
            returncode, stdout, stderr = 1, "", "no repo"

        ok, ref, err = mr.file_ticket(dict(self.REC, cwd=d), MANAGED, NOW,
                                      sub_run=lambda argv, **kw: R())
        self.assertFalse(ok)
        self.assertIn("no repo", err)

    def test_gone_checkout_is_not_filed(self):
        ok, _ref, err = mr.file_ticket(dict(self.REC, cwd="/nonexistent/x"), MANAGED, NOW)
        self.assertFalse(ok)
        self.assertIn("gone", err)


class ProofAndImplementer(unittest.TestCase):
    """#1203 review: a ticket (and the word "fallback") needs a marker; the
    #1060 implementer window is never typed into; a not-typed resume keeps its
    slot."""

    def setUp(self):
        self.h = Harness(self)

    def _sweep(self, state, now, **kw):
        return mr.restore_job(
            now, state, [("%1", self.h.cwd)], managed=MANAGED,
            find_transcript=lambda cwd, ex=None: self.h.tpath, read_state=mf.tail_state,
            verdict=mf.verdict, short_name=mf.short_name, ready=self.h.ready,
            deliver=self.h.deliver, file_ticket=self.h.file_ticket,
            outcome_kind=idle_pane.outcome_kind, delivered=idle_pane.DELIVERED,
            typed_not_delivered=idle_pane.TYPED_NOT_DELIVERED, handled=set(), **kw)

    def test_off_lineup_restores_but_never_files(self):
        self.h.write([_assistant("claude-fable-5-1")])
        state = {}
        logs = self._sweep(state, NOW, find_marker=lambda t, m=None: None)
        self.assertEqual(self.h.typed, ["/model " + MANAGED])
        self.assertTrue(any("OFF-LINEUP runs claude-fable-5-1" in ln for ln in logs), logs)
        self.assertFalse(any("FALLBACK" in ln for ln in logs), logs)
        self.h.append([_model_cmd(), _assistant("claude-opus-5-5", uuid="n")])
        logs = self._sweep(state, NOW + 300)
        self.assertTrue(any("off-lineup" in ln for ln in logs), logs)
        self.assertEqual(self.h.filed, [])

    def test_deep_marker_proves_an_old_fallback(self):
        self.h.write([_assistant("claude-opus-4-8", uuid="late")])
        deep = {"from": "claude-opus-5-5", "to": "claude-opus-4-8", "uuid": "old",
                "timestamp": "2026-09-28T14:51:50Z"}
        state = {}
        self._sweep(state, NOW, find_marker=lambda t, m=None: deep)
        rec = state["model_restore"]["sid1"]
        self.assertTrue(rec["proven"])
        self.assertEqual(rec["episode"], "marker:old")
        self.assertEqual(rec["since"], "2026-09-28T14:51:50Z")

    def test_implementer_session_and_pane_are_never_touched(self):
        self.h.write([_marker()])
        logs = self._sweep({}, NOW, implementer_sid=lambda: "sid1")
        self.assertEqual(self.h.typed, [])
        self.assertTrue(any("implementer session" in ln for ln in logs), logs)
        logs = self._sweep({}, NOW, implementer_sid=lambda: "other",
                           pane_is_implementer=lambda pid: True)
        self.assertEqual(self.h.typed, [])
        self.assertTrue(any("implementer pane" in ln for ln in logs), logs)
        logs = self._sweep({}, NOW, implementer_sid=lambda: "other",
                           pane_is_implementer=lambda pid: None)
        self.assertEqual(self.h.typed, [])
        self.assertTrue(any("role unknown" in ln for ln in logs), logs)
        self._sweep({}, NOW, implementer_sid=lambda: "other",
                    pane_is_implementer=lambda pid: False)
        self.assertEqual(self.h.typed, ["/model " + MANAGED])

    def test_not_typed_resume_keeps_the_slot(self):
        self.h.write([_marker()])
        state = {}
        self._sweep(state, NOW)
        self.h.append([_model_cmd()])
        self._sweep(state, NOW + 60)
        self.h.outcome = SendOutcome("not-typed")
        self._sweep(state, NOW + 60 + mr.RESUME_GRACE_S)
        self.assertIsNone(state["model_restore"]["sid1"]["resumed_at"])
        self.h.outcome = SendOutcome("submitted")
        self._sweep(state, NOW + 60 + 2 * mr.RESUME_GRACE_S)
        self.assertIsNotNone(state["model_restore"]["sid1"]["resumed_at"])

    def test_state_is_written_through_before_a_crash(self):
        self.h.write([_marker()])
        state = {}

        def boom(rec, managed, now):
            raise KeyboardInterrupt  # a killed sweep, past the typing

        with self.assertRaises(KeyboardInterrupt):
            self.h.file_ticket = boom
            self._sweep(state, NOW)
            self.h.append([_model_cmd(), _assistant("claude-opus-5-5", uuid="n")])
            self._sweep(state, NOW + 300)
        self.assertEqual(state["model_restore"]["sid1"]["attempts"], 1)
        self.assertEqual(state["model_restore"]["sid1"]["file_tries"], 1)


class RoundTwo(unittest.TestCase):
    """#1203 review round 2."""

    def setUp(self):
        self.h = Harness(self)

    def _sweep(self, state, now, panes=None, **kw):
        return mr.restore_job(
            now, state, panes or [("%1", self.h.cwd)], managed=MANAGED,
            find_transcript=lambda cwd, ex=None: self.h.tpath, read_state=mf.tail_state,
            verdict=mf.verdict, short_name=mf.short_name, ready=self.h.ready,
            deliver=self.h.deliver, file_ticket=self.h.file_ticket,
            outcome_kind=idle_pane.outcome_kind, delivered=idle_pane.DELIVERED,
            typed_not_delivered=idle_pane.TYPED_NOT_DELIVERED, handled=set(), **kw)

    def test_an_implementer_pane_never_takes_the_main_panes_slot(self):
        self.h.write([_marker()])
        logs = self._sweep({}, NOW, panes=[("%impl", self.h.cwd), ("%main", self.h.cwd)],
                           implementer_sid=lambda: "impl-sid",
                           pane_is_implementer=lambda pid: pid == "%impl")
        self.assertEqual(self.h.typed, ["/model " + MANAGED])
        self.assertTrue(any("%impl implementer pane" in ln for ln in logs), logs)

    def test_the_deep_scan_is_told_the_managed_model(self):
        self.h.write([_assistant("claude-opus-4-8")])
        asked = []
        self._sweep({}, NOW, find_marker=lambda t, m=None: asked.append(m))
        self.assertEqual(asked, [MANAGED])

    def test_no_managed_reply_in_reach_is_no_restore(self):
        self.h.write([_marker()])
        state = {}
        self._sweep(state, NOW)
        self.h.write([{"type": "user", "message": {"content": "hi"}}])  # no reply in the tail
        logs = self._sweep(state, NOW + 300)
        self.assertIsNone(state["model_restore"]["sid1"]["restored_at"])
        self.assertEqual(self.h.filed, [])
        self.assertFalse(any("RESTORED" in ln for ln in logs), logs)

    def test_a_restored_off_lineup_episode_is_done(self):
        self.assertTrue(mr._done({"restored_at": NOW, "proven": False}))
        self.assertFalse(mr._done({"restored_at": NOW, "proven": True, "file_tries": 1}))

    def test_newest_transcript_skips_the_implementer_session(self):
        import watchdog
        d = tempfile.mkdtemp(prefix="mr1203-proj-")
        self.addCleanup(shutil.rmtree, d, True)
        main, impl = os.path.join(d, "main.jsonl"), os.path.join(d, "impl.jsonl")
        for i, p in enumerate((main, impl)):
            with open(p, "w") as fh:
                fh.write("{}\n")
            os.utime(p, (NOW + i, NOW + i))          # the implementer's is newest
        with unittest.mock.patch.object(watchdog, "find_active_transcript",
                                        lambda pd, cwd: (impl, NOW + 1)):
            self.assertEqual(mr.newest_transcript("/pd", "/cwd", "impl"), main)
            self.assertEqual(mr.newest_transcript("/pd", "/cwd", None), impl)


class RunJobWiring(unittest.TestCase):
    def test_holds_on_a_short_budget(self):
        logs = mr.run_job(NOW, {}, [("%1", "/x")], run=None, sleep_fn=None,
                          projects_dir="/nonexistent", budget_left=lambda: 5)
        self.assertEqual(logs, ["model-restore: hold:budget (5s left)"])

    def test_turn_liveness_is_skipped_only_for_the_model_command(self):
        # the design exception: /model ignores the #1110 transcript liveness (a
        # blocked /goal loop never goes quiet), the resume line keeps it.
        seen = {}

        def fake_restore_job(now, state, panes, **kw):
            seen["ready"] = kw["ready"]
            return []

        captured = []

        def fake_pane_ready(pid, cwd, kind, **deps):
            captured.append(deps["turn_live"]("/t.jsonl"))
            return (True, "", "sid", "/t.jsonl")

        fake_deps = {"turn_live": lambda tpath: True}
        with unittest.mock.patch.object(mr, "restore_job", fake_restore_job), \
                unittest.mock.patch.object(idle_pane, "production_deps",
                                           lambda *a, **k: (fake_deps, None, None)), \
                unittest.mock.patch.object(idle_pane, "pane_ready", fake_pane_ready):
            mr.run_job(NOW, {}, [], run=None, sleep_fn=None, projects_dir="/x")
            seen["ready"]("%1", "/cwd", True)
            seen["ready"]("%1", "/cwd", False)
        self.assertEqual(captured, [False, True])

    def test_registered_in_run_once_behind_its_flag(self):
        import inspect
        import watchdog
        src = inspect.getsource(watchdog.run_once)
        self.assertIn('_add("model_restore", lambda: model_restore_enabled and not box_paused', src)
        self.assertIn("model_restore_enabled=False", src)
        cli = (REPO / "airuleset.py").read_text(encoding="utf-8")
        self.assertIn("model_restore_enabled=True", cli)


if __name__ == "__main__":
    unittest.main()
