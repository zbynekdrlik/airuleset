"""#566 — the goal-arm delivery must FINISH or CLEAN UP its OWN stranded
payload, never leave an unsent/half-typed `/goal` in the box until the owner
presses Enter.

Three fail-safe-by-INACTION branches strand the machinery's own text:
`stash-abort: slot occupied` (livelock until LAPSE), the truncated-type
`append-unprovable` park, and an unconfirmed submit. Recovery was outsourced to
a SEPARATE janitor sweep (`goal_dark_watch` job 20) whose budget-deferred cadence
today fired only AFTER the request had already lapsed (montalu3 2026-08-19: 28×
`skip:stash-abort` over ~30 min, then LAPSE, then RECOVERED).

The fix orders OWNED recovery from the GOAL path (job 9, not budget-deferred),
coupled to the janitor's EXISTING ownership proof (`_janitor_watch_seen` /
`_janitor_park_seen` provenance + own-content shape), behind the recent-human
gate:

  (a) a box already holding our OWN swallowed COMPLETE `/goal` is SUBMITTED in
      place (never re-stashed-and-retyped, the #501 lane-nudge lesson one payload
      class over);
  (b) >= GOAL_STASH_ABORT_LIVELOCK consecutive `stash-abort: slot occupied`
      orders `_janitor_recover` (deterministic resolution / one loud ping);
  (c) a foreign draft is left COMPLETELY untouched (protection regression lock);
  (d) a recent-human pane vetoes every recovery keystroke.
"""
import json
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal
from _goal_arm_helpers import (DeliverGoalFakeTmux, GOAL_IDLE_CAP,
                               _isolate_goal_state, _write_marker_transcript)

CWD = "/home/newlevel/devel/stranded566"
SID = "sess-stranded-566"
# a SHORT /goal (< pane width) so the fake's single-line box render holds it
# whole -- head == tail == the literal payload, the completeness proof's
# simplest shape.
GOAL_TEXT = "/goal work the backlog until every issue is closed, or stop after 80 turns"

# a NON-armed idle template with an OCCUPIED single stash slot -- deliver_with_stash
# aborts `slot occupied` here, and the box (rendered from `initial_box`) may hold a
# draft on top of it.
IDLE_STASHED_CAP = ("● Predošlá práca hotová.\n❯ \n"
                    "  ctx ███░  caveman:lite  %s\n" % wd.STASH_MARKER)


def _proj_with_transcript(testcase, human_prompt=None):
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    proj = Path(d.name)
    tpath = _write_marker_transcript(proj, CWD, SID)
    if human_prompt is not None:
        iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user",
                                "message": {"content": human_prompt},
                                "timestamp": iso}) + "\n")
    return proj, tpath


class OwnSwallowedGoalSubmittedInPlace(unittest.TestCase):
    """(a) — the box already holds our OWN COMPLETE `/goal` (a prior attempt
    typed it, the Enter was swallowed). Recovery COMPLETES the submit in place;
    it must NEVER re-stash (`C-s`) our own `/goal` into the single slot and abort
    forever. RED: today `deliver_goal` routes a box-with-draft straight to
    `deliver_with_stash`, which sends `C-s` and re-types."""

    def setUp(self):
        _isolate_goal_state(self)

    def test_swallowed_own_goal_is_submitted_not_restashed(self):
        proj, tpath = _proj_with_transcript(self)
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", CWD, "111")], GOAL_IDLE_CAP, model_type=True,
            initial_box=GOAL_TEXT, transcript_path=str(tpath))
        word = goal.deliver_goal(SID, CWD, GOAL_TEXT, "full", run=tmux,
                                 projects_dir=proj, now=2_000_000.0, state={},
                                 request_ts=2_000_000.0,
                                 sleep_fn=lambda *a, **k: None)
        self.assertEqual(word, "sent",
                         "a swallowed own /goal must be submitted in place: %r"
                         % tmux.keys())
        self.assertNotIn("C-s", tmux.keys(),
                         "submit-in-place must NEVER stash our own /goal into "
                         "the single slot (re-stash livelock): %r" % tmux.keys())


class StashAbortSlotOccupiedLivelockResolved(unittest.TestCase):
    """(b) — >= GOAL_STASH_ABORT_LIVELOCK consecutive `stash-abort: slot
    occupied` from the goal path orders `_janitor_recover` (deterministic
    resolution), never infinite identical aborts until LAPSE. RED: today
    `goal_sweep` never calls the janitor at all."""

    def setUp(self):
        self.reqp, _ = _isolate_goal_state(self)

    def _stash_slot_occupied(self, pid, text, run, **kw):
        lg = kw.get("logs")
        if isinstance(lg, list):
            lg.append("stash-abort: slot occupied")
        return False

    def test_livelock_orders_janitor_recovery_after_n_sweeps(self):
        proj, _ = _proj_with_transcript(self)
        goal.record_goal_request(SID, CWD, GOAL_TEXT, "full",
                                 now=3_000_000.0, path=self.reqp)
        # a foreign draft (not our own /goal) so case (a) never fires; the stash
        # is mocked as permanently `slot occupied`.
        cap = "● Hotovo.\n❯ rozpisany cudzi draft\n  ctx ░░  %s\n" % wd.STASH_MARKER
        calls = []

        def _spy_recover(*a, **kw):
            calls.append(len(calls) + 1)
            return ["RECOVERED (janitor) sess:0.0 -> stuck own delivery cleared"]

        state = {}
        with m.patch.object(wd, "deliver_with_stash",
                            side_effect=self._stash_slot_occupied), \
             m.patch.object(wd, "_janitor_recover", side_effect=_spy_recover):
            for i in range(goal.GOAL_STASH_ABORT_LIVELOCK):
                tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], cap)
                goal.goal_sweep(3_000_100.0 + i, run=tmux, projects_dir=proj,
                                requests_path=self.reqp, state=state,
                                sleep_fn=lambda *a, **k: None)
                if i < goal.GOAL_STASH_ABORT_LIVELOCK - 1:
                    self.assertEqual(calls, [],
                                     "recovery must DEBOUNCE, not fire on sweep "
                                     "%d of %d" % (i + 1,
                                                   goal.GOAL_STASH_ABORT_LIVELOCK))
        self.assertTrue(calls,
                        "after %d consecutive slot-occupied aborts the goal path "
                        "must ORDER janitor recovery, not lapse in silence"
                        % goal.GOAL_STASH_ABORT_LIVELOCK)


class ForeignDraftNeverTouchedByRecovery(unittest.TestCase):
    """(c) protection regression lock — a genuinely FOREIGN draft occupying the
    box (with the stash slot occupied) is left COMPLETELY untouched even once the
    livelock recovery is ordered: zero destructive keystrokes. Passes against the
    correct fix (the janitor's own-content gate refuses a foreign occupant) and
    fails against any fix that clears/pops a foreign draft."""

    def setUp(self):
        self.reqp, _ = _isolate_goal_state(self)

    def test_foreign_occupant_gets_no_destructive_keystroke(self):
        proj, _ = _proj_with_transcript(self)
        goal.record_goal_request(SID, CWD, GOAL_TEXT, "full",
                                 now=4_000_000.0, path=self.reqp)
        foreign = ("moja vlastna dlha rozpisana sprava ktoru vobec nechcem "
                   "aby mi ktokolvek zmazal alebo submitol je to cisto moj text")
        cap = ("● Hotovo.\n❯ %s\n  ctx ░░  caveman:lite  %s\n"
               % (foreign, wd.STASH_MARKER))
        state = {}
        for i in range(goal.GOAL_STASH_ABORT_LIVELOCK + 1):
            tmux = DeliverGoalFakeTmux([("%9", "claude", CWD, "111")], cap)
            goal.goal_sweep(4_000_100.0 + i, run=tmux, projects_dir=proj,
                            requests_path=self.reqp, state=state,
                            sleep_fn=lambda *a, **k: None)
            destructive = [a for a in tmux.sent
                           if len(a) > 1 and a[1] == "send-keys"
                           and ("C-s" in a or any(k == "BSpace" for k in a[4:]))]
            self.assertEqual(destructive, [],
                             "a FOREIGN draft must never receive a clear/pop "
                             "keystroke (sweep %d): %r" % (i + 1, destructive))


class RecentHumanVetoesRecovery(unittest.TestCase):
    """(d) — a pane a human JUST touched vetoes the recovery keystroke. With a
    recent human prompt in the transcript, the case-(a) submit-in-place is NOT
    taken (it falls back to the ordinary stash path). Fails against a fix that
    submits our own /goal ignoring the recent-human gate."""

    def setUp(self):
        _isolate_goal_state(self)

    def test_recent_human_blocks_submit_in_place(self):
        proj, tpath = _proj_with_transcript(self, human_prompt="ako to ide?")
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", CWD, "111")], GOAL_IDLE_CAP, model_type=True,
            initial_box=GOAL_TEXT, transcript_path=str(tpath))
        now = time.time()      # the human prompt above is stamped ~now
        goal.deliver_goal(SID, CWD, GOAL_TEXT, "full", run=tmux,
                          projects_dir=proj, now=now, state={},
                          request_ts=now, sleep_fn=lambda *a, **k: None)
        # the recovery must have been vetoed -> the ordinary stash path ran
        # instead (it stashes, i.e. sends C-s), never a bare submit-in-place.
        self.assertIn("C-s", tmux.keys(),
                      "recent-human must VETO submit-in-place and fall back to "
                      "the ordinary stash path: %r" % tmux.keys())


class SubmitOwnGoalPrimitive(unittest.TestCase):
    """Direct lock on the new `submit_own_goal_verified` primitive: a COMPLETE
    own /goal is submitted (transcript-confirmed); a TRUNCATED one is NEVER
    submitted (#36); a foreign draft is refused with zero keystrokes."""

    def _fake(self, box, transcript_path=None, enters_swallowed=0):
        return DeliverGoalFakeTmux(
            [("%9", "claude", CWD, "111")], GOAL_IDLE_CAP, model_type=True,
            initial_box=box, transcript_path=transcript_path,
            enters_swallowed=enters_swallowed)

    def test_complete_own_goal_submitted(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        tpath = Path(d.name) / "t.jsonl"
        tpath.write_text("")
        tmux = self._fake(GOAL_TEXT, transcript_path=str(tpath))
        ok = wd.submit_own_goal_verified("%9", GOAL_TEXT, run=tmux,
                                         tpath=str(tpath),
                                         sleep_fn=lambda *a, **k: None)
        self.assertTrue(ok)
        self.assertIn("Enter", tmux.keys())
        self.assertNotIn("C-s", tmux.keys())

    def test_truncated_goal_never_submitted(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        tpath = Path(d.name) / "t.jsonl"
        tpath.write_text("")
        # the box holds only a PREFIX of the expected payload (a truncated type)
        tmux = self._fake(GOAL_TEXT[:30], transcript_path=str(tpath))
        ok = wd.submit_own_goal_verified("%9", GOAL_TEXT, run=tmux,
                                         tpath=str(tpath),
                                         sleep_fn=lambda *a, **k: None)
        self.assertFalse(ok)
        self.assertNotIn("Enter", tmux.keys(),
                         "a truncated /goal must NEVER be submitted (#36)")

    def test_foreign_draft_refused_zero_keystrokes(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        tpath = Path(d.name) / "t.jsonl"
        tpath.write_text("")
        tmux = self._fake("nechat ako je moj vlastny draft",
                          transcript_path=str(tpath))
        ok = wd.submit_own_goal_verified("%9", GOAL_TEXT, run=tmux,
                                         tpath=str(tpath),
                                         sleep_fn=lambda *a, **k: None)
        self.assertFalse(ok)
        self.assertEqual([a for a in tmux.sent
                          if len(a) > 1 and a[1] == "send-keys"], [],
                         "a foreign draft must receive ZERO keystrokes")


if __name__ == "__main__":
    unittest.main()
