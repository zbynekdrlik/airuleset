"""Sudo-hosted stream sessions get Discord replies via the HOST watchdog.

2026-07-21 incident: the user's Discord answer «1» to the montalu #1638
question was ✅-acked and never appeared in the montalu claude session.
montalu's claude runs INSIDE newlevel's tmux (`sudo su - montalu` window):
montalu's own watchdog matched the reply but has NO tmux server (no pane —
keystroke delivery impossible), while newlevel's watchdog saw the pane but not
montalu's question map. The session was invisible to BOTH sides.

Fix locked here: the HOST watchdog (whose tmux owns the pane) merges hosted
users' question maps, types the answer into the hosted pane, and drops the
delivered question from the FOREIGN map (so the hosted user's own watchdog
never re-handles it). A box with NO pane for a session defers its
ticket-fallback (DREPLY_NOPANE_FALLBACK_S, longer than the busy deadline) so
the host wins the delivery race and a double gh comment cannot happen.
"""

import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify
import watchdog as wd

IDLE = "● done\n❯\xa0\n  ctx ███░  caveman\n"
RUNNING_DRAFT = ("✻ Waiting for 2 background agents to finish\n"
                 "──────────── ultracode ─\n"
                 "❯\xa0nech to tak\n"
                 "────────────\n"
                 "  ctx ██░░  caveman\n")


class ScriptedPaneRun:
    def __init__(self, captures):
        self.captures = list(captures)
        self.sent = []

    def __call__(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "pane_in_mode" in j:
            return "0"
        if "capture-pane" in j:
            return self.captures.pop(0) if len(self.captures) > 1 else self.captures[0]
        return ""


class _Base(unittest.TestCase):
    OWNER = "773451844110385193"
    F_CWD = "/home/montalu/devel/odoo/odoo-slovnormal"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")   # OWN map (empty)
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        r = m.patch.object(wd, "_react_ok", return_value=True)
        r.start()
        self.addCleanup(r.stop)
        self.dropped = []
        self.fmap = {"888001": {
            "session": "sid-m", "cwd": self.F_CWD, "channel": "777001",
            "ts": time.time(),
            "question": "**Otázka — odoo-erp (montalu):** Ticket #1638 — výdajky?"}}

    def _foreign_load(self, user):
        return dict(self.fmap) if user == "montalu" else {}

    def _foreign_drop(self, user, qid):
        self.dropped.append((user, qid))
        return True

    def _reply(self, rid="repH"):
        return {"id": rid, "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": "1"}


class HostedQuestionDelivery(_Base):
    def test_foreign_question_typed_into_hosted_pane(self):
        run = ScriptedPaneRun([IDLE, IDLE])
        state = {}
        logs = wd.deliver_discord_replies(
            time.time(), run, state, {"sid-m": ("%7", IDLE)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()],
            hosted_users={"sid-m": "montalu"},
            foreign_questions=self._foreign_load,
            foreign_drop=self._foreign_drop)
        typed = [a[-1] for a in run.sent if "-l" in a]
        self.assertTrue(any("Odpoveď z Discordu" in t for t in typed),
                        (typed, logs))
        # delivered → dropped from the FOREIGN map, never the own one
        self.assertEqual(self.dropped, [("montalu", "888001")])
        self.assertIn("repH", state["dreply_done"])

    def test_foreign_fallback_runs_gh_as_the_foreign_user(self):
        gh_calls = []

        def gh(cwd, num, text, user=None):
            gh_calls.append((cwd, num, user))
            return True

        now = time.time()
        state = {"dreply_blocked":
                 {"repH": now - wd.DREPLY_TICKET_FALLBACK_S - 5}}
        run = ScriptedPaneRun([RUNNING_DRAFT])
        wd.deliver_discord_replies(
            now, run, state, {"sid-m": ("%7", RUNNING_DRAFT)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()], gh_comment=gh,
            hosted_users={"sid-m": "montalu"},
            foreign_questions=self._foreign_load,
            foreign_drop=self._foreign_drop)
        self.assertEqual(gh_calls, [(self.F_CWD, "1638", "montalu")])
        self.assertEqual(self.dropped, [("montalu", "888001")])


class NoPaneDefersToTheHost(unittest.TestCase):
    """A box with NO pane for the asking session may not be the pane's HOST —
    its ticket-fallback waits DREPLY_NOPANE_FALLBACK_S (longer than the busy
    deadline) so the host watchdog delivers by keystroke first."""
    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        r = m.patch.object(wd, "_react_ok", return_value=True)
        r.start()
        self.addCleanup(r.stop)
        notify.record_question("888001", "777001", "sid-gone", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #77 — pokračovať?")
        self.gh_calls = []

    def _gh(self, cwd, num, text, user=None):
        self.gh_calls.append(num)
        return True

    def _reply(self):
        return {"id": "repN", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": "2"}

    def _cycle(self, age):
        state = {"dreply_blocked": {"repN": time.time() - age}}
        wd.deliver_discord_replies(
            time.time(), ScriptedPaneRun([""]), state, {}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()], gh_comment=self._gh)
        return state

    def test_nopane_deadline_is_meaningfully_longer_than_busy(self):
        self.assertGreaterEqual(wd.DREPLY_NOPANE_FALLBACK_S,
                                wd.DREPLY_TICKET_FALLBACK_S * 3)

    def test_no_pane_at_busy_deadline_stays_pending(self):
        self._cycle(wd.DREPLY_TICKET_FALLBACK_S + 5)
        self.assertEqual(self.gh_calls, [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_no_pane_past_nopane_deadline_falls_back(self):
        self._cycle(wd.DREPLY_NOPANE_FALLBACK_S + 5)
        self.assertEqual(self.gh_calls, ["77"])


class DryRunNeverMutatesTheRealMap(unittest.TestCase):
    """`--dry-run` is a DIAGNOSTIC — during today's investigation a dry-run
    cycle would have REALLY dropped the pending question from the live map
    (delivery simulated, side effect real), silently losing the answer."""
    OWNER = "773451844110385193"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #5 — otázka?")

    def test_dry_run_delivery_keeps_the_question_on_disk(self):
        reply = {"id": "repDRY", "author": {"id": self.OWNER},
                 "message_reference": {"message_id": "888001"}, "content": "1"}
        state = {}
        wd.deliver_discord_replies(
            time.time(), ScriptedPaneRun([IDLE]), state,
            {"sid-abc": ("%1", IDLE)}, dry_run=True,
            discord_fetch=lambda ch, t: [reply])
        self.assertIn("888001", notify.load_questions(self.qpath),
                      "dry-run must never mutate the real question map")
        self.assertIn("repDRY", state.get("dreply_done", []))


class RunOnceHostedWiring(unittest.TestCase):
    """run_once binds a sudo-hosted pane (foreign HOME cwd, no local
    transcript) to its FOREIGN session id and job 7 delivers into it."""
    OWNER = "773451844110385193"
    F_CWD = "/home/montalu/devel/odoo/odoo-slovnormal"

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        self.dropped = []
        fmap = {"888001": {"session": "sid-m", "cwd": self.F_CWD,
                           "channel": "777001", "ts": time.time(),
                           "question": "Ticket #1638 — výdajky?"}}
        for tgt, val in [
                ("_react_ok", lambda *a, **k: True),
                ("list_claude_panes", lambda run=None: [("%7", self.F_CWD)]),
                ("_foreign_session_info",
                 lambda user, cwd: ("sid-m", time.time() - 5)),
                ("_foreign_questions", lambda user: dict(fmap)),
                ("_foreign_drop_question",
                 lambda user, qid: self.dropped.append((user, qid)) or True)]:
            p = m.patch.object(wd, tgt, val)
            p.start()
            self.addCleanup(p.stop)

    def test_reply_reaches_the_hosted_pane_through_run_once(self):
        run = ScriptedPaneRun([IDLE])
        sp = Path(self.tmp.name) / "state.json"
        reply = {"id": "repR", "author": {"id": self.OWNER},
                 "message_reference": {"message_id": "888001"}, "content": "1"}
        wd.run_once(now=time.time(), dry_run=False, run=run,
                    send_fn=lambda *a, **k: None,
                    projects_dir=str(Path(self.tmp.name) / "projects"),
                    state_path=str(sp),
                    discord_fetch=lambda ch, t: [reply])
        typed = [a[-1] for a in run.sent if "-l" in a]
        self.assertTrue(any("Odpoveď z Discordu" in t for t in typed), typed)
        self.assertEqual(self.dropped, [("montalu", "888001")])


if __name__ == "__main__":
    unittest.main()
