"""#501 — recognize the watchdog's OWN previously-swallowed nudge sitting as a
draft in a supervisor pane and FINISH it by SUBMITTING the existing draft in
place, transcript-verified, instead of stashing around it and retyping (which
aborts forever against the persistent swallow that stranded it — the live
cam-box zbynek-4:0.0 incident: `stash-abort 1/5 -> backoff -> give-up`, the
nudge never delivered).

Two new pieces, tested here:
  * `watchdog._own_nudge_submit_prefix` — the STRICT SUBSET of
    `_JANITOR_OWN_PREFIXES` a human PROVABLY never types (`lane-check: ` /
    `bounce-backstop: ` / `gk-request backstop: `), safe to submit on content
    alone. Deliberately EXCLUDES the human-typeable `/goal `/`/compact`.
  * `watchdog.submit_own_draft_verified` — submit an EXISTING recognized-own
    draft (Enter, then a NEW `user` turn carrying the prefix must appear in the
    transcript; one corrective Escape+Enter on a #36 swallow; never a blind
    Enter, never a re-type, never a backspace of a draft we did not type).

Plus the lane-guard branching in `goal_lane_occupancy_nudge`: an own-prefix
draft is submitted in place; a FOREIGN draft keeps today's `deliver_with_stash`
behavior BYTE-FOR-BYTE (HARD CONSTRAINT a — the foreign-draft protection is
never weakened).
"""

import json
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_ARMED_CAP, _write_marker_transcript, _encode,
)

PID = "%9"


def _tails(sent):
    return [a[-1] for a in sent if a]


def _no_double_escape(sent):
    tails = _tails(sent)
    for a, b in zip(tails, tails[1:]):
        if a == "Escape" and b == "Escape":
            raise AssertionError("two consecutive Escape sends: %r" % sent)


# A realistic OWN lane-check nudge (starts with the unambiguous machine prefix
# `lane-check: `) — the exact class the live cam-box draft was.
OWN_LANE = goal.GOAL_LANE_NUDGE_TEXT % (5, 0)
OWN_BOUNCE = wd.BOUNCE_NUDGE % ("#12, #13", "camera-box")
OWN_GKREQ = wd.GKREQ_NUDGE % ("#40", "odoo-erp")


# --------------------------------------------------------------------------- #
# 1. `_own_nudge_submit_prefix` — the recognition fingerprint.
# --------------------------------------------------------------------------- #

class OwnNudgeSubmitPrefix(unittest.TestCase):
    def test_matches_the_three_unambiguous_machine_prefixes(self):
        self.assertEqual(wd._own_nudge_submit_prefix(OWN_LANE), "lane-check: ")
        self.assertEqual(
            wd._own_nudge_submit_prefix(goal.GOAL_LANE_UNDERSAT_NUDGE_TEXT
                                        % (2, 5, 9, 1)), "lane-check: ")
        self.assertEqual(wd._own_nudge_submit_prefix(OWN_BOUNCE),
                         "bounce-backstop: ")
        self.assertEqual(wd._own_nudge_submit_prefix(OWN_GKREQ),
                         "gk-request backstop: ")

    def test_excludes_the_human_typeable_slash_prefixes(self):
        # `/goal `/`/compact` ARE members of `_JANITOR_OWN_PREFIXES` but a human
        # composes them via the documented manual flows — content is NOT proof
        # of ownership, so they must NEVER be auto-submitted on content alone.
        self.assertIsNone(wd._own_nudge_submit_prefix("/goal do X until done"))
        self.assertIsNone(wd._own_nudge_submit_prefix("/compact"))

    def test_returns_none_for_a_foreign_draft(self):
        self.assertIsNone(wd._own_nudge_submit_prefix("rozpisany user draft"))
        self.assertIsNone(wd._own_nudge_submit_prefix(""))
        self.assertIsNone(wd._own_nudge_submit_prefix(None))

    def test_submit_set_is_a_strict_subset_of_the_janitor_prefixes(self):
        # Every submit-safe prefix is a recognized OWN payload; the two
        # human-typeable ones are deliberately absent from the submit set.
        for p in wd._OWN_NUDGE_SUBMIT_PREFIXES:
            self.assertIn(p, wd._JANITOR_OWN_PREFIXES, p)
        self.assertNotIn("/goal ", wd._OWN_NUDGE_SUBMIT_PREFIXES)
        self.assertNotIn("/compact", wd._OWN_NUDGE_SUBMIT_PREFIXES)


# --------------------------------------------------------------------------- #
# 2. `submit_own_draft_verified` — submit an existing recognized-own draft.
# --------------------------------------------------------------------------- #

class SubmitOwnDraftVerified(unittest.TestCase):
    def _tpath(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "sess.jsonl"
        p.write_text(json.dumps(
            {"type": "assistant", "message": {"content": "predošlá práca"}}) + "\n")
        return p

    def _fake(self, initial_box, tpath, enters_swallowed=0):
        return DeliverGoalFakeTmux([(PID, "claude", "/x", "111")], GOAL_ARMED_CAP,
                                   model_type=True, transcript_path=tpath,
                                   enters_swallowed=enters_swallowed,
                                   initial_box=initial_box)

    def test_own_draft_is_submitted_and_confirmed(self):
        p = self._tpath()
        tmux = self._fake(OWN_LANE, p)
        logs = []
        ok = wd.submit_own_draft_verified(PID, OWN_LANE, tmux, p,
                                          sleep_fn=lambda s: None, logs=logs)
        self.assertTrue(ok, logs)
        # box was submitted (cleared) and NO re-type ever happened
        self.assertEqual(tmux.box, "")
        self.assertFalse(any("-l" in a for a in tmux.sent), tmux.sent)
        # a real user turn carrying the prefix landed
        self.assertIn("lane-check: ", p.read_text())
        self.assertTrue(any("submit-own delivered" in ln for ln in logs), logs)

    def test_swallowed_submit_leaves_the_own_draft_untouched(self):
        p = self._tpath()
        tmux = self._fake(OWN_LANE, p, enters_swallowed=99)
        logs = []
        ok = wd.submit_own_draft_verified(PID, OWN_LANE, tmux, p,
                                          sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok, logs)
        # the own draft is LEFT EXACTLY as-is — never backspaced/re-typed
        self.assertEqual(tmux.box, OWN_LANE)
        self.assertFalse(any("BSpace" in " ".join(a) for a in tmux.sent), tmux.sent)
        self.assertFalse(any("-l" in a for a in tmux.sent), tmux.sent)
        _no_double_escape(tmux.sent)
        self.assertTrue(any("unconfirmed" in ln for ln in logs), logs)

    def test_corrective_escape_enter_recovers_a_single_swallow(self):
        p = self._tpath()
        tmux = self._fake(OWN_LANE, p, enters_swallowed=1)
        logs = []
        ok = wd.submit_own_draft_verified(PID, OWN_LANE, tmux, p,
                                          sleep_fn=lambda s: None, logs=logs)
        self.assertTrue(ok, logs)
        self.assertEqual(tmux.box, "")
        _no_double_escape(tmux.sent)
        # exactly ONE corrective Escape was used
        self.assertEqual(_tails(tmux.sent).count("Escape"), 1, tmux.sent)

    def test_foreign_draft_is_refused_with_no_keystroke(self):
        # HARD CONSTRAINT a — a draft that is NOT an unambiguous own nudge is
        # NEVER Entered (it may be the user's parked draft).
        p = self._tpath()
        tmux = self._fake("rozpisany user draft", p)
        logs = []
        ok = wd.submit_own_draft_verified(PID, "rozpisany user draft", tmux, p,
                                          sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])       # no Enter, no Escape, nothing

    def test_missing_tpath_refuses(self):
        tmux = self._fake(OWN_LANE, None)
        ok = wd.submit_own_draft_verified(PID, OWN_LANE, tmux, None,
                                          sleep_fn=lambda s: None, logs=[])
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])

    def test_box_no_longer_holds_the_draft_is_refused(self):
        # The pre-send capture shows a BARE box (the draft was submitted/cleared
        # by something else since the caller's own check) — never Enter blind.
        p = self._tpath()
        tmux = self._fake("", p)              # box raced empty
        ok = wd.submit_own_draft_verified(PID, OWN_LANE, tmux, p,
                                          sleep_fn=lambda s: None, logs=[])
        self.assertFalse(ok)
        self.assertEqual(tmux.sent, [])


# --------------------------------------------------------------------------- #
# 3. lane-guard branching — own-prefix draft submitted in place; a foreign
#    draft keeps today's deliver_with_stash behavior byte-for-byte.
# --------------------------------------------------------------------------- #

class LaneGuardOwnDraft(unittest.TestCase):
    CWD = "/home/newlevel/devel/lanenudge-own"
    SID = "sess-lane-own-1"

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _run(self, initial_box, rec, state, enters_swallowed=0,
             stash_return=False):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        tpath = proj / _encode(self.CWD) / (self.SID + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath,
                                   enters_swallowed=enters_swallowed,
                                   initial_box=initial_box)
        captured = tmux._render()
        now = 100000
        tmtime = now - goal.GOAL_LANE_IDLE_S - 100
        with m.patch("airuleset.resolve_authority", return_value="full"), \
             m.patch.object(wd, "deliver_with_stash",
                            return_value=stash_return) as dws:
            logs, owns = goal.goal_lane_occupancy_nudge(
                now, tmux, rec, self.SID, self.CWD, "111", captured, tpath,
                tmtime, "loc", None, False, None, proj,
                backlog_fetch=lambda cwd: 5, state=state,
                sleep_fn=lambda s: None)
        return logs, owns, tmux, dws, tpath

    def test_swallowed_own_nudge_draft_is_submitted_in_place(self):
        # #501 RED — the held draft is our OWN lane-check nudge; the guard must
        # SUBMIT it in place (transcript-verified) and NEVER route it through
        # the foreign `deliver_with_stash` stash-around path.
        rec, state = {}, {}
        logs, owns, tmux, dws, tpath = self._run(OWN_LANE, rec, state)
        self.assertTrue(owns)
        self.assertTrue(any("lane-occupancy nudge (own-submit)" in ln
                            for ln in logs), logs)
        self.assertEqual(rec.get("ln"), 1)
        self.assertNotIn("lna", rec)
        dws.assert_not_called()               # foreign stash path NOT taken
        self.assertEqual(tmux.box, "")        # draft submitted (box cleared)
        self.assertIn("lane-check: ", tpath.read_text())

    def test_own_draft_submit_failure_advances_the_abort_streak(self):
        # A recognized own draft that will not submit-verify is a genuinely
        # wedged pane — advance the SAME lna streak + backoff park the foreign
        # abort uses (so it still reaches the give-up ping), never consume the
        # nudge budget, and NEVER backspace the own draft.
        rec, state = {}, {}
        logs, owns, tmux, dws, tpath = self._run(OWN_LANE, rec, state,
                                                 enters_swallowed=99)
        self.assertTrue(owns)
        self.assertFalse(any("lane-occupancy nudge (own-submit)" in ln
                             for ln in logs), logs)
        self.assertTrue(any("own-draft submit-unverified" in ln
                            for ln in logs), logs)
        self.assertNotIn("ln", rec)
        self.assertEqual(rec.get("lna"), 1)
        self.assertIn("lnpark", rec)
        dws.assert_not_called()
        self.assertEqual(tmux.box, OWN_LANE)  # own draft left in place
        self.assertFalse(any("BSpace" in " ".join(a) for a in tmux.sent),
                         tmux.sent)

    def test_foreign_draft_keeps_todays_deliver_with_stash_behavior(self):
        # HARD CONSTRAINT a — a FOREIGN (unrecognized) draft is byte-for-byte
        # today's path: deliver_with_stash, NEVER submit_own_draft_verified.
        rec, state = {}, {}
        with m.patch.object(wd, "submit_own_draft_verified") as sov:
            logs, owns, tmux, dws, tpath = self._run("rozpisany user draft",
                                                     rec, state,
                                                     stash_return=True)
        self.assertTrue(owns)
        dws.assert_called_once()
        sov.assert_not_called()
        self.assertEqual(rec.get("ln"), 1)    # stash succeeded -> nudge booked


if __name__ == "__main__":
    unittest.main()
