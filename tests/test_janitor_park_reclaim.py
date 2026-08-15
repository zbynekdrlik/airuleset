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


if __name__ == "__main__":
    unittest.main()
