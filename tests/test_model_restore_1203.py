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
            managed=MANAGED, find_transcript=lambda cwd: self.tpath,
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
           "restored_at": NOW, "cwd": "/repo"}

    def test_body_names_window_models_and_commit_range(self):
        title, body = mr.compose_ticket(self.REC, MANAGED, NOW,
                                        [("aaa111", "first"), ("bbb222", "second")])
        self.assertIn("claude-opus-4-8", title)
        self.assertIn("2026-09-28T14:51:50Z", title)
        self.assertIn("from `claude-opus-5-5` to `claude-opus-4-8`", body)
        self.assertIn("range `aaa111^..bbb222`", body)
        self.assertIn("- `bbb222` second", body)
        self.assertIn("airuleset#1203", body)

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
            return R(0, "https://github.com/o/r/issues/9\n")

        ok, ref, err = mr.file_ticket(dict(self.REC, cwd=d), MANAGED, NOW, sub_run=fake)
        self.assertTrue(ok, err)
        self.assertEqual(ref, "https://github.com/o/r/issues/9")
        self.assertEqual(calls[0][0][:3], ["git", "log", "--branches"])
        self.assertIn("--since=2026-09-28T14:51:50Z", calls[0][0])
        self.assertEqual(calls[1][0][:3], ["gh", "issue", "create"])
        self.assertNotIn("-R", calls[1][0])            # the pane repo's native gh
        self.assertEqual({c[1] for c in calls}, {d})
        body = calls[1][0][calls[1][0].index("--body") + 1]
        self.assertIn("range `aaa111^..bbb222`", body)

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


class RunJobWiring(unittest.TestCase):
    def test_holds_on_a_short_budget(self):
        logs = mr.run_job(NOW, {}, [("%1", "/x")], run=None, sleep_fn=None,
                          projects_dir="/nonexistent", budget_left=lambda: 5)
        self.assertEqual(logs, ["model-restore: hold:budget (5s left)"])

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
