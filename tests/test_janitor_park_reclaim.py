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
        self.assertEqual(logs, [])
        self.assertNotIn(PID, state.get("stash_parks", {}))   # backstop cleared it
        self.assertFalse(any(a[1] == "send-keys" for a in run.state["sent"]))


class MarkerGoneBackstop(unittest.TestCase):
    """A recorded park whose slot is no longer occupied is stale -> cleared,
    so an age-unbounded record can never license action on a LATER human
    stash that merely reuses the same pane."""

    def test_record_cleared_when_slot_no_longer_occupied(self):
        bare_no_stash = "● Hotovo.\n❯ \n  ctx ███░  ◎ /goal active\n"
        state = {"stash_parks": {PID: NOW - 40 * 3600}}
        logs, _rec, _run = _recover(state, captured=bare_no_stash, dry_run=False)
        self.assertEqual(logs, [])
        self.assertNotIn(PID, state.get("stash_parks", {}))

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


if __name__ == "__main__":
    unittest.main()
