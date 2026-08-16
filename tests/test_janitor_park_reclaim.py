"""#488 — the janitor reclaims a genuinely-ours parked stash after ANY delay.

A stash `deliver_with_stash` parked and then aborted before popping back sits
in the single slot indefinitely. The #372 janitor's reclaim was gated by
`_janitor_watch_seen`, a generic delivery-attempt mark bounded to 6h
(`JANITOR_WATCH_MAX_AGE_S`) — but a parked draft persists far longer (the gk
supervisor pane ran `◎ /goal active (1d)`), so the mark ages past 6h and the
janitor refuses forever. #488 adds a DURABLE, park-specific provenance record
(`state['stash_parks'][pid]`) that gates the STASH reclaim age-unbounded,
while a human's own stash — never recorded by us — is still never reclaimed.

This file locks the READ side (`_janitor_recover`) and the marker-gone
backstop; the WRITE side + helpers are locked in the GREEN commit's own
additions below.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal
# #506 — reuse the SAME validated wrapped-box renderer the #193/#501 tests use
# (BOX_WIDTH 176, the real dev1 pane width; glyph on the HEAD row, greedy word
# wrap, continuation rows indented) instead of copying it — one implementation.
from test_wrapped_draft import render_box

# Real own-nudge payloads that register in `_JANITOR_OWN_PREFIXES`. Both are
# 400-700c and WRAP at 176 col, so their leading prefix (`lane-check: ` /
# `stuck-check: `) sits on the box HEAD row, ABSENT from the wrapped TAIL — the
# #506 dead-branch input (`_janitor_recover` read the tail).
OWN_LANE_NUDGE = goal.GOAL_LANE_NUDGE_TEXT % (5, 0)      # 691c, "lane-check: "
OWN_STUCK_NUDGE = wd.WORKING_NUDGE_TEXT                  # 431c, "stuck-check: "
# A long FOREIGN draft that WRAPS but does NOT start with any own prefix — the
# invariant control (head-read must not widen what the janitor claims as ours).
FOREIGN_LONG = ("toto je moja vlastná dlhá rozpísaná správa ktorú práve píšem "
                "do prompta a vôbec nechcem aby mi ju ktokoľvek zmazal ani "
                "reklamoval lebo je to čisto môj vlastný text bez akéhokoľvek "
                "strojového prefixu na začiatku riadka wrapuje sa cez viac "
                "riadkov ale ostáva mojou vlastnou správou od prvého po posledné slovo")

# gk-shaped capture: box bare (`_input_line_text` == ""), single stash slot
# occupied, a goal armed (1d) — the exact live shape #488 reproduced.
GK_STASHED_BARE = "\n".join([
    "  earlier output",
    "",
    "%s · ◎ /goal active (1d)" % wd.STASH_MARKER,
    "",
    "❯ ",
    "",
])

PID = "%9"
CWD = "/home/newlevel/devel/parktest"
LOC = "gk:0.9"
NOW = 2_000_000.0


def _mkrun(after_pop=None):
    """A recording fake `run`. capture-pane returns the gk snapshot until a
    `C-s` is sent, then `after_pop` (the restored draft) when given."""
    st = {"popped": False, "sent": []}

    def run(argv, timeout=8):
        st["sent"].append(argv)
        if len(argv) > 1 and argv[1] == "send-keys":
            if "C-s" in argv:
                st["popped"] = True
            return ""
        if len(argv) > 1 and argv[1] == "capture-pane":
            if st["popped"] and after_pop is not None:
                return after_pop
            return GK_STASHED_BARE
        if len(argv) > 1 and argv[1] == "display-message":
            return LOC
        return ""

    run.state = st
    return run


def _recover(state, captured=GK_STASHED_BARE, dry_run=True, run=None, now=NOW):
    run = run or _mkrun()
    rec = {}
    logs = wd._janitor_recover(run, rec, PID, CWD, captured, LOC,
                               send_fn=None, dry_run=dry_run,
                               sleep_fn=lambda *a, **k: None,
                               state=state, now=now)
    return logs, rec, run


class GkParkReclaimedAgeUnbounded(unittest.TestCase):
    """RED (#488): a genuinely-ours park is not reclaimed once the 6h generic
    mark expires or is lost — even though a durable park record proves it is
    ours."""

    def test_park_record_reclaims_after_generic_mark_expired(self):
        # The exact gk gap: our park recorded, generic mark ~25h old (goal ran
        # 1d) -> current code refuses at the 6h gate; the fix reclaims via the
        # durable, age-unbounded park record.
        state = {"stash_parks": {PID: NOW - 25 * 3600},
                 "janitor_watch": {PID: NOW - 25 * 3600}}
        logs, _rec, _run = _recover(state)
        self.assertTrue(any("would attempt pop" in ln for ln in logs), logs)

    def test_park_record_reclaims_with_no_generic_mark(self):
        # State lost on deploy -> only the durable park record remains, and it
        # alone must license the reclaim (age-unbounded).
        state = {"stash_parks": {PID: NOW - 40 * 3600}}
        logs, _rec, _run = _recover(state)
        self.assertTrue(any("would attempt pop" in ln for ln in logs), logs)


class HumanDraftNeverReclaimed(unittest.TestCase):
    """The ticket's hard requirement: a draft with NO durable park record of
    ours (a human's own stash, or any pane we never left a park on) is never
    reclaimed via the age-unbounded path."""

    def test_no_record_and_expired_mark_refuses(self):
        # A human parked their own draft (occupied + box bare); no park
        # record of ours, and the generic mark is stale/absent -> refused.
        for state in ({"janitor_watch": {PID: NOW - 25 * 3600}}, {}):
            logs, _rec, run = _recover(state)
            self.assertEqual(logs, [], state)
            # never sent a keystroke, never even resolved a location
            self.assertFalse(any(a[1] == "send-keys" for a in run.state["sent"]))

    def test_park_record_does_not_bypass_the_foreign_occupant_gate(self):
        # Even WITH a park record + a fresh mark, an occupied slot whose
        # visible box holds FOREIGN content (not our recognizable stuck
        # shape) is refused -- the record makes provenance age-unbounded, it
        # never bypasses the destructive clear-and-pop content-shape gate.
        foreign = GK_STASHED_BARE.replace("❯ ", "❯ moja vlastna rozpisana sprava")
        state = {"stash_parks": {PID: NOW - 40 * 3600},
                 "janitor_watch": {PID: NOW - 60}}
        logs, _rec, run = _recover(state, captured=foreign)
        self.assertEqual(logs, [])
        self.assertFalse(any(a[1] == "send-keys" for a in run.state["sent"]))

    def test_park_record_does_not_enable_the_no_stash_clear_action(self):
        # A box directly holding our own stuck content but NO stash slot
        # (the job-14 `/compact` clear shape) still requires the 6h generic
        # mark -- a park record (marker-gone -> stale, cleared) must not
        # license the destructive box-clear on its own.
        own_no_stash = "● Hotovo.\n❯ /goal STOP keď je CI zelené\n  ctx ███░\n"
        state = {"stash_parks": {PID: NOW - 40 * 3600}}   # no generic mark
        logs, _rec, run = _recover(state, captured=own_no_stash, dry_run=False)
        # The stale record is journalled + cleared by the marker-gone backstop
        # (slot not occupied), and NO destructive box-clear is attempted (that
        # action needs the 6h generic mark, which the park record never
        # substitutes for).
        self.assertNotIn(PID, state.get("stash_parks", {}))   # backstop cleared it
        self.assertTrue(any("stale park record cleared" in ln for ln in logs), logs)
        self.assertFalse(any("would attempt" in ln for ln in logs), logs)
        self.assertFalse(any(a[1] == "send-keys" for a in run.state["sent"]))


class MarkerGoneBackstop(unittest.TestCase):
    """A recorded park whose slot is no longer occupied is stale -> cleared,
    so an age-unbounded record can never license action on a LATER human
    stash that merely reuses the same pane."""

    def test_record_cleared_when_slot_no_longer_occupied(self):
        bare_no_stash = "● Hotovo.\n❯ \n  ctx ███░  ◎ /goal active\n"
        state = {"stash_parks": {PID: NOW - 40 * 3600}}
        logs, _rec, _run = _recover(state, captured=bare_no_stash, dry_run=False)
        self.assertNotIn(PID, state.get("stash_parks", {}))
        self.assertTrue(any("stale park record cleared" in ln for ln in logs), logs)

    def test_backstop_does_not_mutate_state_on_dry_run(self):
        bare_no_stash = "● Hotovo.\n❯ \n  ctx ███░  ◎ /goal active\n"
        state = {"stash_parks": {PID: NOW - 40 * 3600}}
        _recover(state, captured=bare_no_stash, dry_run=True)
        self.assertIn(PID, state.get("stash_parks", {}))   # dry run touches nothing


class ReclaimClearsTheRecord(unittest.TestCase):
    def test_successful_pop_recovers_and_clears_the_park_record(self):
        after_pop = "● Hotovo.\n❯ obnoveny parkovany draft\n  ctx ███░\n"
        run = _mkrun(after_pop=after_pop)
        state = {"stash_parks": {PID: NOW - 40 * 3600}}
        logs, rec, _ = _recover(state, dry_run=False, run=run)
        self.assertTrue(any("RECOVERED (janitor)" in ln for ln in logs), logs)
        self.assertNotIn(PID, state.get("stash_parks", {}))
        self.assertTrue(any("C-s" in a for a in run.state["sent"]))
        self.assertIs(rec.get("janitor_pinged"), False)


class ParkRecordHelpers(unittest.TestCase):
    def test_record_seen_clear_roundtrip(self):
        state = {}
        self.assertFalse(wd._janitor_park_seen(state, PID))
        wd._janitor_park_record(state, PID, 123.0)
        self.assertEqual(state["stash_parks"][PID], 123.0)
        self.assertTrue(wd._janitor_park_seen(state, PID))
        wd._janitor_clear_park(state, PID)
        self.assertFalse(wd._janitor_park_seen(state, PID))

    def test_seen_is_age_unbounded(self):
        # ANY age reads as seen -- the whole point vs the 6h generic mark.
        state = {"stash_parks": {PID: NOW - 999 * 24 * 3600}}
        self.assertTrue(wd._janitor_park_seen(state, PID))

    def test_seen_type_checked_like_the_generic_mark(self):
        for bad in (True, False, "x", None, [1]):
            self.assertFalse(wd._janitor_park_seen({"stash_parks": {PID: bad}}, PID))

    def test_helpers_are_none_state_safe(self):
        # No crash, no state created -- mirrors _janitor_mark_watch/_clear_watch.
        wd._janitor_park_record(None, PID, NOW)
        wd._janitor_clear_park(None, PID)
        self.assertFalse(wd._janitor_park_seen(None, PID))
        # clear on a missing key is a no-op, not a KeyError
        wd._janitor_clear_park({}, PID)


class PruneParksHelper(unittest.TestCase):
    """#488 review-1: the age-unbounded record must not orphan forever for a
    pane that no longer exists (restores #372's 'no stale provenance forever'
    bound the age-unboundedness removed)."""

    def test_prunes_records_for_dead_panes_keeps_live(self):
        state = {"stash_parks": {"%1": 100.0, "%9": 200.0, "%3": 300.0}}
        wd._janitor_prune_parks(state, ["%1", "%3"])   # %9 is gone
        self.assertEqual(set(state["stash_parks"]), {"%1", "%3"})

    def test_empty_live_set_prunes_nothing_failsafe(self):
        # A failed `tmux list-panes` read yields no ids -> must NOT wipe valid
        # fresh records (that would silently defeat a genuine reclaim).
        state = {"stash_parks": {"%1": 100.0}}
        wd._janitor_prune_parks(state, [])
        self.assertEqual(set(state["stash_parks"]), {"%1"})
        wd._janitor_prune_parks(state, None)
        self.assertEqual(set(state["stash_parks"]), {"%1"})

    def test_none_state_and_no_parks_are_no_ops(self):
        wd._janitor_prune_parks(None, ["%1"])          # no crash
        state = {}
        wd._janitor_prune_parks(state, ["%1"])          # no parks key -> no-op
        self.assertNotIn("stash_parks", state)          # never creates it


class PruneWiredIntoDarkWatch(unittest.TestCase):
    """The GC actually runs in goal_dark_watch's sweep — the per-pane
    marker-gone backstop only sees panes still in the candidate set, so a
    record for a pane that LEFT it is only ever reclaimed by this prune."""

    @staticmethod
    def _run(live):
        # Returns `live` ONLY for the prune's own `-F #{pane_id}` call;
        # everything else (incl. _reconcile_candidate_panes' richer tab format)
        # gets "" -> zero candidate panes -> the sweep loop is skipped.
        def run(argv, timeout=8):
            if argv[:2] == ["tmux", "list-panes"] and argv[-1] == "#{pane_id}":
                return live
            return ""
        return run

    def test_dark_watch_prunes_a_dead_pane_park_record(self):
        state = {"stash_parks": {"%1": 100.0, "%9": 200.0}}   # %9 dead
        goal.goal_dark_watch(1000, run=self._run("%1\n%2\n"), state=state,
                             dry_run=False, sleep_fn=lambda *a, **k: None)
        self.assertIn("%1", state["stash_parks"])
        self.assertNotIn("%9", state["stash_parks"])

    def test_dark_watch_dry_run_does_not_prune(self):
        state = {"stash_parks": {"%9": 200.0}}                 # %9 dead
        goal.goal_dark_watch(1000, run=self._run("%1\n"), state=state,
                             dry_run=True, sleep_fn=lambda *a, **k: None)
        self.assertIn("%9", state["stash_parks"])   # dry-run mutates nothing


class WrappedOwnResidueReclaim(unittest.TestCase):
    """#506 (RED): a WRAPPED own-nudge residue occupying the box (with the
    stash slot occupied) must be recognized as OURS and reclaimed
    (clear-and-pop). `_janitor_recover` read the box TAIL (`_input_line_text`,
    the #193 `endswith` swallowed-tail contract), but every real own nudge
    (289-720c) WRAPS at a live pane width, so its `lane-check: `/`stuck-check: `
    prefix is on the HEAD row and ABSENT from the tail —
    `_looks_like_own_stuck_content(tail)` is False and the reclaim never fired.
    The exact tail-vs-head bug #501 fixed for the lane-guard submit path
    (`_input_box_head_text`), one path over. Mutation: revert the janitor to the
    tail-read and these two tests fail."""

    def _wrapped_occupied(self, payload):
        # occupied stash slot + a WRAPPED own residue in the box
        cap = render_box(payload, stashed=True)
        self.assertIn(wd.STASH_MARKER, cap)                 # stash occupied
        self.assertIs(wd._find_input_box(cap)[2], True)     # box genuinely wraps
        return cap

    @staticmethod
    def _fresh_provenance():
        # a fresh generic delivery-attempt mark (<6h) — the janitor's provenance
        return {"janitor_watch": {PID: NOW - 60}}

    def test_wrapped_lane_check_residue_is_reclaimed(self):
        cap = self._wrapped_occupied(OWN_LANE_NUDGE)
        logs, _rec, _run = _recover(self._fresh_provenance(), captured=cap)
        self.assertTrue(
            any("would attempt clear-and-pop" in ln for ln in logs),
            "a WRAPPED own lane-check residue must be reclaimed, not read as a "
            "foreign occupant — the janitor must read the box HEAD, not the "
            "wrapped tail: %r" % logs)

    def test_wrapped_stuck_check_residue_is_reclaimed(self):
        cap = self._wrapped_occupied(OWN_STUCK_NUDGE)
        logs, _rec, _run = _recover(self._fresh_provenance(), captured=cap)
        self.assertTrue(
            any("would attempt clear-and-pop" in ln for ln in logs),
            "a WRAPPED own stuck-check residue must be reclaimed (janitor "
            "head-read): %r" % logs)


if __name__ == "__main__":
    unittest.main()
