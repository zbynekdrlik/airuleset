"""watchdog #35 — stash-around delivery + job 10 rework.

Issue #35 (2026-07-25): job 10's wedge ping fired ("v okne visí neodoslaný
text") for a text the user never saw, blocking delivery of a Discord reply
into a pane that held a FOREIGN draft. Two fixes, both empirically verified
against CC 2.1.220 (see the issue's evidence comments):

  1. `deliver_with_stash` — CC's native prompt stash (Ctrl+S) lets the
     watchdog park a foreign draft, deliver its own text, submit, and let
     the draft AUTO-RESTORE itself once the delivered turn completes. The
     protocol is fail-safe at every step (single-slot stash with a silent
     overwrite → NEVER stash over an occupied slot; every verify failure
     aborts to the pre-#35 pending/fallback path; a rapid double-Escape
     permanently deletes a draft → never sent).
  2. job 10 (`prompt_wedge_check`) reworked: recognizes job 7's own
     compose-reply text sitting in the box as MACHINE (auto-submits,
     never pings), only pings while the session is genuinely WAITING on
     the user (last assistant message contains "NEEDS YOU"), and adds a
     PER-PANE 24h cooldown so a re-wrap (which changes the draft's hash)
     can never re-spam the same pane.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

STASH_MARKER = "› stashed"          # "› stashed"

DRAFT_IDLE = "● turn done\n❯ nechaj tak\n  ctx ░░\n"
STASHED_BARE = "● turn done\n❯\xa0\n  ctx ░░  › stashed\n"
BARE_AFTER_SUBMIT = "● turn done\n❯\xa0\n  ctx ░░\n"
BUSY = "● Validate issue\n  ⎿ running…\n✳ Baking… (2m · esc to interrupt)\n"
DRAFT_WITH_SELECTED_STRIP = (
    "❯ nechaj tak\n"
    "────────────\n"
    "  ctx ░░\n"
    "  ↑/↓ to select · Enter to view\n"
    "\n"
    "❯ ● main\n"
    "  ◯ autopilot-worker doing stuff\n"
)
TEXT = "Odpoveď z Discordu ..."
TYPED_BOUNDARY = "● typing\n❯ " + TEXT + "\n  ctx ░░\n"


class _Recorder:
    """argv recorder whose capture-pane calls consume a fixed QUEUE (strict —
    exhausting it is a test-authoring bug, not fallback behavior)."""

    def __init__(self, captures=None):
        self.captures = list(captures or [])
        self.sent = []

    def __call__(self, argv, timeout=8):
        self.sent.append(argv)
        if "capture-pane" in " ".join(argv):
            return self.captures.pop(0)
        return ""

    def tails(self):
        return [a[-1] for a in self.sent]


def assert_no_double_escape(sent):
    tails = [a[-1] for a in sent if a]
    for a, b in zip(tails, tails[1:]):
        if a == "Escape" and b == "Escape":
            raise AssertionError("two consecutive Escape sends: %r" % sent)


class DeliverWithStashHappyPath(unittest.TestCase):
    def test_keystroke_order_is_stash_type_enter(self):
        run = _Recorder([STASHED_BARE, TYPED_BOUNDARY, BARE_AFTER_SUBMIT])
        ok = wd.deliver_with_stash("%1", TEXT, run, captured=DRAFT_IDLE)
        self.assertTrue(ok, run.sent)
        tails = run.tails()
        cs_i = tails.index("C-s")
        lit_i = next(i for i, a in enumerate(run.sent) if "-l" in a)
        enter_i = tails.index("Enter")
        self.assertLess(cs_i, lit_i, run.sent)
        self.assertLess(lit_i, enter_i, run.sent)
        assert_no_double_escape(run.sent)

    def test_escapes_the_strip_selection_first_when_present(self):
        run = _Recorder([STASHED_BARE, TYPED_BOUNDARY, BARE_AFTER_SUBMIT])
        ok = wd.deliver_with_stash("%1", TEXT, run,
                                   captured=DRAFT_WITH_SELECTED_STRIP)
        self.assertTrue(ok, run.sent)
        tails = run.tails()
        self.assertEqual(tails[0], "Escape", run.sent)
        self.assertEqual(tails.count("Escape"), 1, run.sent)
        assert_no_double_escape(run.sent)


class DeliverWithStashAborts(unittest.TestCase):
    def test_occupied_slot_aborts_with_zero_keystrokes(self):
        logs = []
        run = _Recorder([])
        ok = wd.deliver_with_stash("%1", TEXT, run, captured=STASHED_BARE, logs=logs)
        self.assertFalse(ok)
        self.assertEqual(run.sent, [], "must not stash over an occupied slot")
        self.assertTrue(logs, "abort path must log why")

    def test_bare_box_no_draft_aborts_with_zero_keystrokes(self):
        run = _Recorder([])
        ok = wd.deliver_with_stash("%1", TEXT, run, captured=BARE_AFTER_SUBMIT)
        self.assertFalse(ok)
        self.assertEqual(run.sent, [])

    def test_busy_pane_aborts_with_zero_keystrokes(self):
        run = _Recorder([])
        ok = wd.deliver_with_stash("%1", TEXT, run, captured=BUSY)
        self.assertFalse(ok)
        self.assertEqual(run.sent, [])

    def test_verify_bare_failed_aborts_before_any_typing(self):
        # the re-capture after C-s still shows the draft (stash didn't take)
        run = _Recorder([DRAFT_IDLE])
        logs = []
        ok = wd.deliver_with_stash("%1", TEXT, run, captured=DRAFT_IDLE, logs=logs)
        self.assertFalse(ok)
        self.assertFalse(any("-l" in a for a in run.sent),
                         "must never type after a failed stash verify: %r" % run.sent)
        self.assertTrue(logs)

    def test_type_verify_failed_sends_exactly_one_restoring_cs(self):
        wrong_boundary = "● x\n❯ totally unrelated text\n  ctx ░░\n"
        run = _Recorder([STASHED_BARE, wrong_boundary, DRAFT_IDLE])
        logs = []
        ok = wd.deliver_with_stash("%1", TEXT, run, captured=DRAFT_IDLE, logs=logs)
        self.assertFalse(ok)
        cs_count = sum(1 for a in run.sent if a and a[-1] == "C-s")
        self.assertEqual(cs_count, 2, run.sent)   # 1 stash + 1 restore
        self.assertFalse(any("Enter" in a for a in run.sent),
                         "must never submit text that failed its own verify: %r" % run.sent)
        self.assertTrue(logs)


class DeliverWithStashSwallowedSubmit(unittest.TestCase):
    def test_corrective_escape_enter_recovers(self):
        run = _Recorder([STASHED_BARE, TYPED_BOUNDARY, TYPED_BOUNDARY,
                         BARE_AFTER_SUBMIT])
        ok = wd.deliver_with_stash("%1", TEXT, run, captured=DRAFT_IDLE)
        self.assertTrue(ok, run.sent)
        tails = run.tails()
        enter_idxs = [i for i, t in enumerate(tails) if t == "Enter"]
        self.assertGreaterEqual(len(enter_idxs), 2, run.sent)
        self.assertEqual(tails[enter_idxs[-1] - 1], "Escape", run.sent)
        assert_no_double_escape(run.sent)

    def test_still_stuck_after_corrective_returns_false(self):
        run = _Recorder([STASHED_BARE, TYPED_BOUNDARY, TYPED_BOUNDARY,
                         TYPED_BOUNDARY])
        logs = []
        ok = wd.deliver_with_stash("%1", TEXT, run, captured=DRAFT_IDLE, logs=logs)
        self.assertFalse(ok, run.sent)
        assert_no_double_escape(run.sent)
        self.assertTrue(logs)


class DeliverDiscordRepliesStashIntegration(unittest.TestCase):
    """job 7's busy branch tries the stash-around delivery for a pane that is
    idle-with-a-FOREIGN-draft (not genuinely busy) before falling back to the
    pending/ticket-fallback path."""

    OWNER = "773451844110385193"

    def setUp(self):
        import notify
        from tempfile import TemporaryDirectory
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        self.env = {"DISCORD_BOT_TOKEN": "tok", "DISCORD_MENTION_ZBYNEK": self.OWNER}
        for tgt, val in [("_questions_path", lambda: self.qpath),
                         ("_read_env", lambda: dict(self.env))]:
            import unittest.mock as m
            p = m.patch.object(notify, tgt, val)
            p.start()
            self.addCleanup(p.stop)
        import unittest.mock as m
        r = m.patch.object(wd, "_react_ok", return_value=True)
        r.start()
        self.addCleanup(r.stop)
        notify.record_question("888001", "777001", "sid-abc", "/repo/x",
                               now=time.time(), path=self.qpath,
                               question="Ticket #99 - foreign draft test")
        self.notify = notify

    def _reply(self):
        return {"id": "repS", "author": {"id": self.OWNER},
                "message_reference": {"message_id": "888001"}, "content": "1"}

    def test_foreign_draft_pane_gets_delivered_via_stash(self):
        # compose_reply_prompt's rendered text is unknown ahead of time (it
        # embeds the question + timestamp) — script the typed-boundary capture
        # generically by echoing back whatever gets typed.
        state = {}
        seen = {}

        def run(argv, timeout=8):
            j = " ".join(argv)
            if "pane_in_mode" in j:
                return "0"
            if "-l" in argv:
                seen["typed"] = argv[-1]
                return ""
            if "capture-pane" in j:
                if seen.get("submitted"):
                    return BARE_AFTER_SUBMIT
                if "typed" in seen:
                    return "● typing\n❯ " + seen["typed"] + "\n  ctx ░░\n"
                return STASHED_BARE
            if argv[-1] == "Enter":
                seen["submitted"] = True
                return ""
            return ""
        wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", DRAFT_IDLE)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()])
        self.assertIn("repS", state.get("dreply_done", []))
        self.assertNotIn("888001", self.notify.load_questions(self.qpath))

    def test_dry_run_never_attempts_stash(self):
        state = {}
        run = _Recorder([])
        wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", DRAFT_IDLE)}, dry_run=True,
            discord_fetch=lambda ch, t: [self._reply()])
        self.assertEqual(run.sent, [])

    def test_genuinely_busy_pane_still_falls_through_to_pending(self):
        state = {}
        run = _Recorder([])
        logs = wd.deliver_discord_replies(
            time.time(), run, state, {"sid-abc": ("%1", BUSY)}, dry_run=False,
            discord_fetch=lambda ch, t: [self._reply()])
        self.assertTrue(any("busy" in ln for ln in logs), logs)
        self.assertNotIn("repS", state.get("dreply_done", []))


# --------------------------------------------------------------------------- #
# job 10 (prompt_wedge_check) rework — machine-tail recognition, waiting-gate,
# per-pane cooldown, and a location-carrying ping text.
# --------------------------------------------------------------------------- #

MACHINE_TAIL_PANE = ("✳ hotovo\n──── ultracode ─\n"
                     "❯\xa0Odpoveď z Discordu: bola ti položená otázka a text\n"
                     "────\n  ctx ██░░  caveman\n")
USER_DRAFT_PANE = ("✳ hotovo — súhrn turnu\n──── ultracode ─\n"
                   "❯\xa0nechať ako je\n────\n  ctx ██░░  caveman\n")


def _sweep(state, captured, send, now, tm=None, run=None, waiting=True):
    tmtime = tm if tm is not None else now - wd.PWEDGE_MIN_IDLE_S - 60
    return wd.prompt_wedge_check(now, state, "%2", captured, tmtime,
                                 "zbynek", "odoo-erp", send, run=run,
                                 waiting=waiting)


class _FakeSend:
    def __init__(self):
        self.calls = []

    def __call__(self, body, **kw):
        self.calls.append((body, kw))
        return "sent"


class MachineTailRecognition(unittest.TestCase):
    """Text job 7 itself typed (recorded in state['dreply_typed']) sitting
    stuck in the box is recognized as MACHINE — auto-submitted (Escape then
    Enter, never a bare Enter), never pinged."""

    def _run_recorder(self):
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            return "0" if "pane_in_mode" in " ".join(argv) else ""
        run.calls = calls
        return run

    def test_recognized_machine_text_submits_and_never_pings(self):
        full_text = "Odpoveď z Discordu: bola ti položená otázka — tu je odpoveď."
        state = {"dreply_typed": {"%2": {"tail": full_text[-160:], "ts": time.time()}}}
        captured = "✳ hotovo\n❯\xa0" + full_text + "\n  ctx ██░░  caveman\n"
        send = _FakeSend()
        run = self._run_recorder()
        now = time.time()
        _sweep(state, captured, send, now, run=run)
        logs = _sweep(state, captured, send, now + 70, run=run)
        tails = [a[-1] for a in run.calls]
        self.assertIn("Escape", tails, run.calls)
        self.assertIn("Enter", tails, run.calls)
        self.assertLess(tails.index("Escape"), tails.index("Enter"), run.calls)
        self.assertTrue(any("machine-nudge" in ln for ln in logs), logs)
        self.assertFalse(send.calls, "recognized machine text must never ping")


class WaitingGate(unittest.TestCase):
    """A parked USER draft pings NOTHING while the session is not genuinely
    waiting on the user — but state keeps tracking so a later waiting flip
    can still ping."""

    def test_non_waiting_session_never_pings(self):
        state, send, now = {}, _FakeSend(), time.time()
        for i in range(5):
            _sweep(state, USER_DRAFT_PANE, send, now + i * 70, waiting=False)
        self.assertEqual(send.calls, [])

    def test_waiting_flip_pings_after_tracked_while_not_waiting(self):
        state, send, now = {}, _FakeSend(), time.time()
        _sweep(state, USER_DRAFT_PANE, send, now, waiting=False)
        _sweep(state, USER_DRAFT_PANE, send, now + 70, waiting=False)
        self.assertEqual(send.calls, [])
        # session now genuinely waits on the user — same stable draft pings
        _sweep(state, USER_DRAFT_PANE, send, now + 140, waiting=True)
        self.assertEqual(len(send.calls), 1, send.calls)

    def test_waiting_session_pings_normally(self):
        state, send, now = {}, _FakeSend(), time.time()
        _sweep(state, USER_DRAFT_PANE, send, now, waiting=True)
        _sweep(state, USER_DRAFT_PANE, send, now + 70, waiting=True)
        self.assertEqual(len(send.calls), 1, send.calls)


class PerPaneCooldown(unittest.TestCase):
    """A re-wrap that changes the draft's HASH must not defeat the per-pane
    ping cooldown — the user's 'často mi chodí' spam."""

    def test_hash_churn_within_cooldown_never_repings(self):
        state, send, now = {}, _FakeSend(), time.time()
        _sweep(state, USER_DRAFT_PANE, send, now, waiting=True)
        _sweep(state, USER_DRAFT_PANE, send, now + 70, waiting=True)
        self.assertEqual(len(send.calls), 1, send.calls)
        changed = USER_DRAFT_PANE.replace("nechať ako je", "nechať tak ako je")
        _sweep(state, changed, send, now + 200, waiting=True)
        _sweep(state, changed, send, now + 270, waiting=True)
        self.assertEqual(len(send.calls), 1,
                         "a re-wrapped draft within 24h must NOT re-ping: %r" % send.calls)

    def test_a_fresh_pane_is_unaffected_by_another_panes_cooldown(self):
        state, send, now = {}, _FakeSend(), time.time()
        wd.prompt_wedge_check(now, state, "%A", USER_DRAFT_PANE,
                              now - wd.PWEDGE_MIN_IDLE_S - 60, "zbynek",
                              "odoo-erp", send, waiting=True)
        wd.prompt_wedge_check(now + 70, state, "%A", USER_DRAFT_PANE,
                              now - wd.PWEDGE_MIN_IDLE_S - 60, "zbynek",
                              "odoo-erp", send, waiting=True)
        self.assertEqual(len(send.calls), 1)
        wd.prompt_wedge_check(now, state, "%B", USER_DRAFT_PANE,
                              now - wd.PWEDGE_MIN_IDLE_S - 60, "zbynek",
                              "odoo-erp", send, waiting=True)
        logs = wd.prompt_wedge_check(now + 70, state, "%B", USER_DRAFT_PANE,
                                     now - wd.PWEDGE_MIN_IDLE_S - 60, "zbynek",
                                     "odoo-erp", send, waiting=True)
        self.assertEqual(len(send.calls), 2, logs)


class PingCarriesLocation(unittest.TestCase):
    def test_ping_text_carries_session_window_pane(self):
        state, send, now = {}, _FakeSend(), time.time()

        def run(argv, timeout=8):
            j = " ".join(argv)
            if "pane_in_mode" in j:
                return "0"
            if "session_name" in j:
                return "gk-master:1.2"
            return ""
        _sweep(state, USER_DRAFT_PANE, send, now, run=run, waiting=True)
        _sweep(state, USER_DRAFT_PANE, send, now + 70, run=run, waiting=True)
        self.assertEqual(len(send.calls), 1, send.calls)
        body = send.calls[0][0]
        self.assertIn("gk-master:1.2", body, body)

    def test_location_lookup_failure_never_blocks_the_ping(self):
        state, send, now = {}, _FakeSend(), time.time()

        def run(argv, timeout=8):
            if "pane_in_mode" in " ".join(argv):
                return "0"
            return ""     # no location resolvable
        _sweep(state, USER_DRAFT_PANE, send, now, run=run, waiting=True)
        _sweep(state, USER_DRAFT_PANE, send, now + 70, run=run, waiting=True)
        self.assertEqual(len(send.calls), 1, send.calls)


if __name__ == "__main__":
    unittest.main()
