"""#515 — mechanical U-label lifecycle (watchdog/u_labels.py + the job-7
`_delivered` capture + job 32 wiring).

Proves: a needs-answer/needs-decision label whose question the owner already
ANSWERED on Discord is captured at delivery and mechanically cleared once the
asking session moved past the `❓` — never while it is still pending (re-ask /
`❓` tail), never needs-acceptance, never another box's ticket.
"""
import json
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                       # noqa: E402
import watchdog as wd               # noqa: E402
import watchdog.u_labels as ul      # noqa: E402


IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"


def _write_transcript(projects_dir, cwd, sid, marker_text):
    """Write a one-entry transcript whose last assistant message ends in
    `marker_text`, at the exact path `_transcript_for_session` resolves."""
    d = Path(projects_dir) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    p.write_text(json.dumps({"type": "assistant",
                             "message": {"content": marker_text}}) + "\n",
                 encoding="utf-8")
    return p


class FirstTicketNum(unittest.TestCase):
    def test_first_number_only(self):
        self.assertEqual(ul._first_ticket_num("Design #4199 vs #4200?"), 4199)

    def test_none_when_absent(self):
        self.assertIsNone(ul._first_ticket_num("no ticket here"))
        self.assertIsNone(ul._first_ticket_num(None))
        self.assertIsNone(ul._first_ticket_num(123))


class CaptureAnsweredTicket(unittest.TestCase):
    def test_captures_session_cwd_num(self):
        state = {}
        ul.capture_answered_ticket(
            state, {"session": "s1", "cwd": "/repo",
                    "question": "Otázka k #4199 — schváliš?"}, 1000)
        recs = state[ul.U_RECONCILE_STATE_KEY]
        self.assertIn("s1:4199", recs)
        self.assertEqual(recs["s1:4199"],
                         {"num": 4199, "cwd": "/repo", "session": "s1", "ts": 1000})

    def test_no_ticket_number_is_a_noop(self):
        state = {}
        ul.capture_answered_ticket(
            state, {"session": "s1", "cwd": "/repo", "question": "no number"}, 1)
        self.assertNotIn(ul.U_RECONCILE_STATE_KEY, state)

    def test_missing_session_or_cwd_is_a_noop(self):
        state = {}
        ul.capture_answered_ticket(
            state, {"session": "", "cwd": "/repo", "question": "#7"}, 1)
        ul.capture_answered_ticket(
            state, {"session": "s", "cwd": "", "question": "#7"}, 1)
        self.assertNotIn(ul.U_RECONCILE_STATE_KEY, state)

    def test_bounded_to_max(self):
        state = {}
        for i in range(ul.U_RECONCILE_MAX + 25):
            ul.capture_answered_ticket(
                state, {"session": "s%d" % i, "cwd": "/r",
                        "question": "#%d" % (i + 1)}, i)
        self.assertLessEqual(len(state[ul.U_RECONCILE_STATE_KEY]),
                             ul.U_RECONCILE_MAX)


class UReconcileDecide(unittest.TestCase):
    def test_reask_keeps(self):
        self.assertEqual(ul._u_reconcile_decide(True, "⏳", 0)[0], "keep-reask")

    def test_question_tail_keeps(self):
        self.assertEqual(ul._u_reconcile_decide(False, "❓", 0)[0], "keep-parked")

    def test_moved_on_clears(self):
        for tail in ("⏳", "✅", ""):
            self.assertEqual(ul._u_reconcile_decide(False, tail, 0)[0], "clear",
                             "tail=%r must clear" % tail)

    def test_over_ttl_drops_stale(self):
        # a MOVED-ON session (⏳) still CLEARS regardless of age — it never
        # lingers, so the reaper never applies to it.
        self.assertEqual(
            ul._u_reconcile_decide(False, "⏳", ul.U_RECONCILE_TTL_S + 1)[0],
            "clear")
        # only a capture stuck behind a still-pending `❓` past the TTL is
        # reaped (stop tracking; the label stays — the question IS pending).
        self.assertEqual(
            ul._u_reconcile_decide(False, "❓", ul.U_RECONCILE_TTL_S + 1)[0],
            "drop-stale")
        # a `❓` still WITHIN the TTL is kept-parked.
        self.assertEqual(ul._u_reconcile_decide(False, "❓", 5)[0], "keep-parked")


class ClearOwnerQuestionLabels(unittest.TestCase):
    def _run_maker(self, view_stdout, view_rc=0, edit_rc=0):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[1] == "issue" and argv[2] == "view":
                return m.Mock(returncode=view_rc, stdout=view_stdout)
            return m.Mock(returncode=edit_rc, stdout="")
        return run, calls

    def test_removes_present_owner_labels_only(self):
        run, calls = self._run_maker(json.dumps(
            {"state": "OPEN",
             "labels": [{"name": "needs-answer"}, {"name": "bug"},
                        {"name": "needs-acceptance"}]}))
        removed = ul._clear_owner_question_labels("/r", 7, run=run)
        self.assertEqual(removed, ["needs-answer"])
        edit = [c for c in calls if c[2] == "edit"][0]
        self.assertIn("--remove-label", edit)
        self.assertIn("needs-answer", edit)
        # NEVER touch needs-acceptance (the #526 W lane) or an unrelated label.
        self.assertNotIn("needs-acceptance", edit)
        self.assertNotIn("bug", edit)

    def test_removes_both_owner_labels(self):
        run, calls = self._run_maker(json.dumps(
            {"state": "OPEN",
             "labels": [{"name": "needs-answer"}, {"name": "needs-decision"}]}))
        removed = ul._clear_owner_question_labels("/r", 7, run=run)
        self.assertEqual(set(removed), {"needs-answer", "needs-decision"})

    def test_closed_ticket_returns_empty_no_edit(self):
        run, calls = self._run_maker(json.dumps(
            {"state": "CLOSED", "labels": [{"name": "needs-answer"}]}))
        self.assertEqual(ul._clear_owner_question_labels("/r", 7, run=run), [])
        self.assertFalse([c for c in calls if c[2] == "edit"])

    def test_no_owner_label_returns_empty(self):
        run, _ = self._run_maker(json.dumps(
            {"state": "OPEN", "labels": [{"name": "bug"}]}))
        self.assertEqual(ul._clear_owner_question_labels("/r", 7, run=run), [])

    def test_view_error_returns_none(self):
        run, _ = self._run_maker("", view_rc=1)
        self.assertIsNone(ul._clear_owner_question_labels("/r", 7, run=run))

    def test_edit_error_returns_none(self):
        run, _ = self._run_maker(json.dumps(
            {"state": "OPEN", "labels": [{"name": "needs-answer"}]}), edit_rc=1)
        self.assertIsNone(ul._clear_owner_question_labels("/r", 7, run=run))


class ReconcileULabels(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = str(Path(self.tmp.name) / "projects")
        self.cwd = "/home/x/repo"
        self.sid = "sid-aaa"
        # hermetic, EMPTY question map (no re-ask by default)
        self.qpath = str(Path(self.tmp.name) / "discord-questions.json")
        p = m.patch.object(notify, "_questions_path", lambda: self.qpath)
        p.start()
        self.addCleanup(p.stop)
        self.cleared = []
        self.state = {ul.U_RECONCILE_STATE_KEY: {
            "%s:4199" % self.sid: {"num": 4199, "cwd": self.cwd,
                                   "session": self.sid, "ts": 1000}}}

    def _clear_fn(self, ret=None):
        def fn(cwd, num):
            self.cleared.append((cwd, num))
            return ["needs-answer"] if ret is None else ret
        return fn

    def _run(self, now=2000, dry_run=False, clear_ret=None):
        return ul.reconcile_u_labels(
            now, self.state, dry_run=dry_run, projects_dir=self.proj,
            clear_fn=self._clear_fn(clear_ret))

    def test_keeps_while_tail_is_question(self):
        _write_transcript(self.proj, self.cwd, self.sid, "❓ NEEDS YOU: čo?")
        logs = self._run()
        self.assertEqual(self.cleared, [])
        # capture is KEPT (not popped) while the session is still parked on ❓
        self.assertIn("%s:4199" % self.sid,
                      self.state.get(ul.U_RECONCILE_STATE_KEY, {}))
        self.assertTrue(any("keep" in ln and "#4199" in ln for ln in logs), logs)

    def test_clears_once_session_moved_on(self):
        _write_transcript(self.proj, self.cwd, self.sid, "⏳ WORKING: ďalej")
        logs = self._run()
        self.assertEqual(self.cleared, [(self.cwd, 4199)])
        # capture popped after a successful clear
        self.assertNotIn("%s:4199" % self.sid,
                         self.state.get(ul.U_RECONCILE_STATE_KEY, {}))
        self.assertTrue(any("CLEARED" in ln and "#4199" in ln for ln in logs), logs)

    def test_no_transcript_clears(self):
        # session ended (no transcript) -> not visibly parked -> answered -> clear
        logs = self._run()
        self.assertEqual(self.cleared, [(self.cwd, 4199)])
        self.assertTrue(any("CLEARED" in ln for ln in logs), logs)

    def test_reask_keeps_even_with_moved_on_tail(self):
        _write_transcript(self.proj, self.cwd, self.sid, "⏳ WORKING")
        # a FRESH main-map entry re-references #4199 for this session
        notify.record_question("900001", "700", self.sid, self.cwd,
                               now=1500, path=self.qpath,
                               question="Znova k #4199?")
        logs = self._run()
        self.assertEqual(self.cleared, [], "a live re-ask must keep the label")
        self.assertTrue(any("keep" in ln for ln in logs), logs)

    def test_over_ttl_drops_without_gh(self):
        _write_transcript(self.proj, self.cwd, self.sid, "❓ still parked")
        logs = self._run(now=1000 + ul.U_RECONCILE_TTL_S + 5)
        self.assertEqual(self.cleared, [], "drop-stale never calls gh")
        self.assertNotIn("%s:4199" % self.sid,
                         self.state.get(ul.U_RECONCILE_STATE_KEY, {}))
        self.assertTrue(any("drop-stale" in ln for ln in logs), logs)

    def test_malformed_capture_is_reaped(self):
        self.state[ul.U_RECONCILE_STATE_KEY]["bad"] = {"num": "x"}
        self.state[ul.U_RECONCILE_STATE_KEY]["bad2"] = "not-a-dict"
        _write_transcript(self.proj, self.cwd, self.sid, "⏳")
        self._run()
        recs = self.state.get(ul.U_RECONCILE_STATE_KEY, {})
        self.assertNotIn("bad", recs)
        self.assertNotIn("bad2", recs)

    def test_gh_unmeasurable_keeps_capture(self):
        # clear_fn returning None (gh failed / unmeasurable) -> keep + retry
        _write_transcript(self.proj, self.cwd, self.sid, "⏳")
        logs = ul.reconcile_u_labels(
            2000, self.state, projects_dir=self.proj,
            clear_fn=lambda c, n: None)
        self.assertIn("%s:4199" % self.sid,
                      self.state.get(ul.U_RECONCILE_STATE_KEY, {}))
        self.assertTrue(any("clear-retry" in ln for ln in logs), logs)

    def test_dry_run_does_not_clear_or_pop(self):
        _write_transcript(self.proj, self.cwd, self.sid, "⏳")
        logs = self._run(dry_run=True)
        self.assertEqual(self.cleared, [])
        self.assertIn("%s:4199" % self.sid,
                      self.state.get(ul.U_RECONCILE_STATE_KEY, {}))
        self.assertTrue(any("dry-run" in ln for ln in logs), logs)

    def test_gated_off_when_no_clear_fn(self):
        self.assertEqual(
            ul.reconcile_u_labels(2000, self.state, projects_dir=self.proj,
                                  clear_fn=None),
            [])


class DeliveredCaptureIntegration(unittest.TestCase):
    """The job-7 `_delivered` capture writes a reconcile record for a delivered
    Discord answer whose question names a ticket."""
    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "discord-questions.json")
        self.cpath = str(Path(self.tmp.name) / "discord-cards.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER,
                    "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "777001"}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_cards_path", lambda: self.cpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        # send_verified typing-fake so the delivery lands without real tmux.
        sv = m.patch.object(wd, "send_verified",
                            lambda pid, text, run=None, tpath=None,
                            sleep_fn=None, logs=None: True)
        sv.start()
        self.addCleanup(sv.stop)
        self.sent = []

    def _run(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "pane_in_mode" in j:
            return "0"
        if "capture-pane" in j:
            return IDLE
        return ""

    def _reply(self, content="ok"):
        return {"id": "rep1", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": content}

    def test_capture_written_on_delivery(self):
        notify.record_question("888001", "777001", "sid-abc", "/home/x/repo",
                               now=time.time(), path=self.qpath,
                               question="Design k #4199 — schváliš?")
        state = {}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {"sid-abc": ("%1", IDLE)},
            dry_run=False,
            discord_fetch=lambda ch, tok: [self._reply()])
        self.assertIn("sid-abc:4199", state.get(ul.U_RECONCILE_STATE_KEY, {}))

    def test_dry_run_writes_no_capture(self):
        notify.record_question("888001", "777001", "sid-abc", "/home/x/repo",
                               now=time.time(), path=self.qpath,
                               question="#4199?")
        state = {}
        wd.deliver_discord_replies(
            time.time(), self._run, state, {"sid-abc": ("%1", IDLE)},
            dry_run=True,
            discord_fetch=lambda ch, tok: [self._reply()])
        self.assertNotIn(ul.U_RECONCILE_STATE_KEY, state)


class RunOnceWiring(unittest.TestCase):
    def test_job32_registered_and_gated_on_clear_fn(self):
        # with u_reconcile_clear wired, the job runs; without it, it is skipped.
        seen = {}

        def fake_reconcile(now, state, **kw):
            seen["called"] = True
            return ["u-label test"]

        with m.patch.object(wd, "reconcile_u_labels", fake_reconcile):
            with m.patch.object(wd, "list_claude_panes", lambda run=None, **k: []):
                wd.run_once(now=1.0, dry_run=True, run=lambda *a, **k: "",
                            u_reconcile_clear=lambda c, n: [])
        self.assertTrue(seen.get("called"), "job 32 must run when clear_fn wired")

    def test_job32_skipped_without_clear_fn(self):
        seen = {}

        def fake_reconcile(now, state, **kw):
            seen["called"] = True
            return []

        with m.patch.object(wd, "reconcile_u_labels", fake_reconcile):
            with m.patch.object(wd, "list_claude_panes", lambda run=None, **k: []):
                wd.run_once(now=1.0, dry_run=True, run=lambda *a, **k: "")
        self.assertNotIn("called", seen)


if __name__ == "__main__":
    unittest.main()
