"""#1332 round-2 review findings: the empty-first-line box beyond job 1.

Both round-2 reviews: a dry run still wrote the wedge episode through
`reclaimed`, the #271 draft-rescue snapshot was empty for the owner's words
under an empty first line, a SUCCESSFUL line-break clear kept the episode lock
for 30 minutes, a line-break box blocked every delivery outside job 1 (the
bare-box send and the stash route), and job 10 / job 6 read the new shape with
their own head logic (job 6 would submit our stranded pointer as the owner's
prompt)."""
import json
import os
import sys
import time
from pathlib import Path
from unittest import TestCase
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog  # noqa: E402
from test_box_first_line_empty_1332 import FOOT, LONE_NL, PTR_BOX, SEP, _BoxPane  # noqa: E402
from test_send_verified_adoption import (  # noqa: E402
    _RunOnceAdoptHarness, _assistant_row)

OWNER_CAP = "\n".join(["● ok", "", SEP, "❯\xa0", "  my owner words on line two", SEP,
                       FOOT]) + "\n"


def _pane(box, tmp):
    tp = Path(tmp) / "s.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
    return _BoxPane("%1\tclaude\t/x\n", box, str(tp))


class RoundTwo(TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_a_dry_run_through_reclaimed_writes_no_state(self):
        from watchdog import apierr_box
        state = {}
        with m.patch.object(watchdog, "capture_pane", lambda *a, **k: LONE_NL):
            apierr_box.reclaimed("%1", lambda *a, **k: "", lambda s: None, state, "sid",
                                 time.time(), 10000, 300, "p", [], lambda *a, **k: "sent",
                                 None, True, str(Path(self.tmp) / "st.json"))
        self.assertEqual(state, {})
        self.assertFalse((Path(self.tmp) / "recovery-stuck").exists())

    def test_the_owners_words_under_an_empty_first_line_are_rescued(self):
        from watchdog import stash
        self.assertEqual(stash._draft_rescue_text(OWNER_CAP), "my owner words on line two")

    def test_a_successful_line_break_clear_does_not_lock_the_next_episode(self):
        from watchdog import apierr_box
        fake = _pane("\n", self.tmp)
        state, now = {}, time.time()
        self.assertEqual(apierr_box.reclaim("%1", fake, lambda s: None, state, now,
                                            False, [], "p"), "cleared")
        fake.box = "\n"                       # a fresh line break, later
        self.assertEqual(apierr_box.reclaim("%1", fake, lambda s: None, state, now + 600,
                                            False, [], "p"), "cleared")

    def test_a_clear_that_never_converges_is_tried_once_per_episode(self):
        from watchdog import apierr_box
        sent, verdicts, now, state = [], [], time.time(), {}
        with m.patch.object(watchdog, "keys", lambda pid, *k, **kw: sent.append(k) or True), \
                m.patch.object(watchdog, "capture_pane", lambda *a, **k: LONE_NL):
            for i in range(3):
                verdicts.append(apierr_box.reclaim("%1", lambda *a, **k: "",
                                                   lambda s: None, state, now + 70 * i,
                                                   False, [], "p"))
        self.assertEqual(verdicts, ["held", "held", "held"])
        self.assertEqual(len(sent), watchdog.JANITOR_CLEAR_MAX_ITER,
                         "one full attempt in the first sweep, none after")

    # Round 3 (both reviews) reversed round 2's "every caller clears a line-break
    # box": that clear typed into a box we did not write for kinds the owner
    # switched OFF and outside any recovery. Only the recovery resume paths
    # (job 1, credential-dead, job 6) clear it; every other delivery HOLDS.
    def test_a_bare_box_send_holds_on_a_line_break_box(self):
        fake = _pane("\n", self.tmp)
        logs = []
        first = watchdog.send_verified("%1", "a machine nudge", fake, fake.tpath,
                                       sleep_fn=lambda s: None, logs=logs,
                                       nudge="bounce", state={})
        self.assertFalse(first)
        self.assertEqual(fake.box, "\n", logs)
        self.assertEqual(fake.sent, [], "no key into a box we did not type")

    def test_the_stash_route_holds_on_a_line_break_box_without_a_dead_ctrl_s(self):
        fake = _pane("\n", self.tmp)
        logs = []
        ok = watchdog.deliver_with_stash("%1", "continue", fake, logs=logs,
                                         sleep_fn=lambda s: None, state={})
        self.assertFalse(ok)
        self.assertEqual(fake.box, "\n", logs)
        self.assertEqual(fake.sent, [], logs)

    def test_a_successful_own_text_clear_ends_the_episode_lock(self):
        from watchdog import send_outcome
        ptr_file = Path(self.tmp) / "nudges" / "resume-261111183944-c447.md"
        ptr_file.parent.mkdir()
        ptr_file.write_text("x", encoding="utf-8")
        os.chmod(ptr_file, 0o600)
        ptr = "nudge: [resume] rotacia — celý text: " + str(ptr_file)
        state, now = {}, time.time()
        with m.patch.dict(os.environ, {"AIRULESET_NUDGE_FILE_DIR": str(ptr_file.parent)}):
            for i in range(2):
                fake = _pane("\n" + ptr, self.tmp)
                verdict = send_outcome.presend_reclaim(
                    "%1", fake, fake.render(), lambda s: None, state, now + 600 * i, [].append)
                self.assertTrue(verdict.startswith("pre-send: cleared"), verdict)

    def test_job10_reads_our_pointer_under_an_empty_first_line_as_ours(self):
        st, pings = {}, []
        for i in range(watchdog.PWEDGE_SWEEPS + 2):
            watchdog.prompt_wedge_check(1_000_000 + 70 * i, st, "%1", PTR_BOX, 0,
                                        "zbynek", "p", lambda *a, **k: pings.append(a))
        self.assertFalse(any("NEODOSLAN" in str(p) for p in pings), pings)

    def test_job10_does_not_track_a_line_break_box_as_a_user_draft(self):
        st, pings = {}, []
        for i in range(3):
            watchdog.prompt_wedge_check(1_000_000 + 70 * i, st, "%1", LONE_NL, 0,
                                        "zbynek", "p", lambda *a, **k: pings.append(a))
        self.assertEqual(pings, [])
        self.assertNotIn("pwedge:%1", st)


class JobSixOnTheNewShape(TestCase):
    """Job 6 (session-limit resume) must never submit our stranded pointer as
    the owner's prompt, nor a box that holds only line breaks."""

    BANNER = ("❯ continue\n  ⎿  You've hit your session limit · resets 6:10pm "
              "(Europe/Prague)\n     /usage-credits to finish what you're working on.\n\n")

    def _two_sweeps(self, box_rows, after=(), **extra):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        import tempfile
        nudges = Path(tempfile.mkdtemp())
        f = nudges / "resume-261111183944-c447.md"
        f.write_text("x", encoding="utf-8")
        os.chmod(f, 0o600)
        p = m.patch.dict(os.environ, {"AIRULESET_NUDGE_FILE_DIR": str(nudges)})
        p.start()
        self.addCleanup(p.stop)
        q = m.patch.object(watchdog, "JANITOR_CLEAR_SETTLE_S", 0)   # a static capture
        q.start()                                                   # never converges
        self.addCleanup(q.stop)
        rows = [r.replace("{F}", str(f)) for r in box_rows]
        cap = self.BANNER + "\n".join([SEP] + rows + [SEP] + list(after) + [FOOT]) + "\n"
        now = datetime(2026, 7, 1, 18, 15, tzinfo=ZoneInfo("Europe/Bratislava")).timestamp()
        sess = {"resets_at": now - 300, "pinged": True, "continued": False,
                "first_seen": int(now - 3600), "last_seen": int(now - 60)}
        sess.update(extra)
        h = _RunOnceAdoptHarness(self, [_assistant_row("pracujem…")], mtime_age=60,
                                 capture=cap, seed_state={"sesslimit:" + h_sid(): sess},
                                 now=now)
        out = h.run(sv_result=True)
        h.now = now + 400
        out2 = h.run(sv_result=True)
        return out[0] + out2[0], out[1] + out2[1], out[2] + out2[2]

    def test_our_stranded_pointer_is_never_submitted_as_the_owners_prompt(self):
        logs, sent, keys = self._two_sweeps(
            ["❯\xa0", "  nudge: [resume] rotacia — celý text: {F}"])
        self.assertFalse(any("user draft" in ln for ln in logs), logs)
        self.assertFalse(any("rozpísaný prompt" in b for b in sent), sent)

    def test_a_line_break_box_is_never_submitted_as_a_draft(self):
        logs, sent, keys = self._two_sweeps(["❯\xa0", ""])
        self.assertFalse(any("user draft" in ln for ln in logs), logs)
        self.assertFalse(any("rozpísaný prompt" in b for b in sent), sent)

    def test_a_reclaim_that_keeps_holding_reaches_the_give_up(self):
        logs, sent, keys = self._two_sweeps(
            ["❯\xa0", "  nudge: [resume] rotacia — celý text: {F}"],
            swallow_fails=watchdog.SESSLIMIT_MAX_TRIES - 1)
        self.assertTrue(any("nepodarilo obnoviť" in b for b in sent), (sent, logs))

    def test_our_pointer_behind_an_occupied_stash_slot_is_never_your_draft(self):
        logs, sent, keys = self._two_sweeps(
            ["❯\xa0", "  nudge: [resume] rotacia — celý text: {F}"],
            after=["  › stashed · ctrl+s to restore"])
        self.assertFalse(any("user draft" in ln for ln in logs), logs)
        self.assertFalse(any("rozpísaný prompt" in b for b in sent), sent)


class RoundFour(TestCase):
    """Round 4 (both reviews): the owner-stop latch must stop the recovery
    reclaim's BOOKKEEPING too, a refused attempt must not use up the episode,
    a line-break hold must not claim "our own text", and the owner's own
    Discord reply must not wait forever on a line-break box."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_a_latched_pane_books_nothing_on_the_credential_dead_path(self):
        from watchdog import owner_stop
        st, pings = {}, []
        with m.patch.object(owner_stop, "pane_latch", lambda pid: {"ts": 1}), \
                m.patch.object(watchdog, "capture_pane", lambda *a, **k: LONE_NL), \
                m.patch.object(watchdog, "keys", lambda *a, **k: False):
            for i in range(3):
                watchdog._handle_authdead_episode(
                    st, "sid", lambda: 2000.0, 3000.0 + 70 * i, 10000, "obs", "%0",
                    "/devel/obs", "/x.jsonl", LONE_NL, True, lambda *a, **k: "",
                    lambda *a, **k: pings.append(a), None, lambda s: None, False, [],
                    str(Path(self.tmp) / "st.json"), 1000.0, 1000.0)
        self.assertEqual(pings, [])
        self.assertNotIn("apierr-busypane:sid", st)
        self.assertFalse(st.get("linebreak_clear"))

    def test_a_refused_attempt_does_not_use_up_the_episode(self):
        from watchdog import apierr_box
        fake = _pane("\n", self.tmp)
        state, now = {}, time.time()
        with m.patch.object(watchdog, "keys", lambda *a, **k: False):
            apierr_box.reclaim("%1", fake, lambda s: None, state, now, False, [], "p")
        self.assertEqual(apierr_box.reclaim("%1", fake, lambda s: None, state, now + 70,
                                            False, [], "p"), "cleared")

    def test_a_line_break_hold_never_claims_our_own_text(self):
        from watchdog import apierr_box
        pings = []
        with m.patch.object(watchdog, "capture_pane", lambda *a, **k: LONE_NL), \
                m.patch.object(watchdog, "keys", lambda *a, **k: True):
            apierr_box.reclaimed("%1", lambda *a, **k: "", lambda s: None, {}, "sid",
                                 time.time(), 10000, 300, "p", [],
                                 lambda *a, **k: pings.append(a), None, False,
                                 str(Path(self.tmp) / "st.json"))
        self.assertEqual(len(pings), 1)
        self.assertNotIn("vlastný text", pings[0][0])
        self.assertIn("prázdne riadky", pings[0][0])

    def test_the_owners_discord_reply_clears_a_line_break_box(self):
        fake = _pane("\n", self.tmp)
        logs, state = [], {}
        ok = watchdog.deliver_with_stash("%1", "moja odpoveď", fake, logs=logs,
                                         sleep_fn=lambda s: None, state=state,
                                         user_authored=True)
        self.assertFalse(ok, "nothing is delivered in the clearing call")
        self.assertEqual(fake.box, "", logs)
        self.assertFalse(any("C-s" in a for a in fake.sent), logs)
        sent = watchdog.send_verified("%1", "moja odpoveď", fake, fake.tpath,
                                      sleep_fn=lambda s: None, logs=logs,
                                      user_authored=True, state=state)
        self.assertTrue(sent, logs)                     # the next sweep delivers it
        self.assertEqual(fake.submitted, ["moja odpoveď"])


class RoundFive(TestCase):
    """Round 5 (both reviews): a busy hold is no wedge and no give-up attempt,
    a hold that sent no key never uses up the episode, a latched pane still
    takes the owner's own reply, and a latch hold is journalled."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_a_busy_first_pass_does_not_use_up_the_line_break_episode(self):
        from watchdog import apierr_box
        fake = _pane("\n", self.tmp)
        state, now = {}, time.time()
        with m.patch.object(apierr_box, "_clear_line_breaks",
                            lambda *a, **k: ("busy", 0)):
            apierr_box.clear_line_breaks_once("%1", fake, lambda s: None, state, now)
        self.assertEqual(apierr_box.reclaim("%1", fake, lambda s: None, state, now + 70,
                                            False, [], "p"), "cleared")

    def test_the_owners_reply_clears_a_line_break_box_on_a_latched_pane(self):
        from watchdog import owner_stop
        fake = _pane("\n", self.tmp)
        with m.patch.object(owner_stop, "pane_latch", lambda pid: {"ts": 1}):
            watchdog.deliver_with_stash("%1", "moja odpoveď", fake, logs=[],
                                        sleep_fn=lambda s: None, state={},
                                        user_authored=True)
        self.assertEqual(fake.box, "")

    def test_a_busy_pane_holding_our_pointer_books_nothing(self):
        from watchdog import apierr_box, send_outcome
        state, pings = {}, []
        with m.patch.object(watchdog, "capture_pane", lambda *a, **k: PTR_BOX), \
                m.patch.object(send_outcome, "presend_reclaim",
                               lambda *a, **k: "turn running under the box — held"), \
                m.patch.object(send_outcome, "_presend_own", lambda *a, **k: "shape"):
            ok = apierr_box.reclaimed("%1", lambda *a, **k: "", lambda s: None, state,
                                      "sid", time.time(), 10000, 300, "p", [],
                                      lambda *a, **k: pings.append(a), None, False,
                                      str(Path(self.tmp) / "st.json"))
        self.assertTrue(ok, "never handed to the draft path")
        self.assertEqual(pings, [])
        self.assertNotIn("apierr-busypane:sid", state)

    def test_a_latch_hold_is_journalled(self):
        from watchdog import apierr_box, owner_stop
        logs = []
        with m.patch.object(owner_stop, "pane_latch", lambda pid: {"ts": 1}), \
                m.patch.object(watchdog, "capture_pane", lambda *a, **k: LONE_NL):
            apierr_box.reclaim("%1", lambda *a, **k: "", lambda s: None, {}, time.time(),
                               False, logs, "p")
        self.assertTrue(any("latch" in ln for ln in logs), logs)


def h_sid():
    return _RunOnceAdoptHarness.SID
