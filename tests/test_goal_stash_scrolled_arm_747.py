"""#747 — the #746 scrolled-box head-checkpoint, extended to the STASH route.

#746 fixed the BARE-box goal-arm send (`_send_goal_verified` ->
`_type_literal_verified`, the two-phase head-checkpoint + gated `allow_scrolled`).
The STASH route -- `deliver_with_stash`'s bare (PARKED/NOOP) branch, used when a
FOREIGN draft is present at arm time -- still verified its typed `/goal` with
`_type_verify_landed` at the DEFAULT `allow_scrolled=False`. So a scroll-length
`/goal` typed via the stash route SCROLLS the box, reads head-not-prefix ->
`_TV_CORRUPT` -> `stash-abort: type-verify-failed` -> aborts forever (the live
airuleset loop, sid 2d02a127..., 2026-08-30: `SKIP stash-abort` x2, no
`SEND typed`).

The fix gives `deliver_with_stash`'s bare branch its OWN two-phase head-checkpoint
(the SHARED `_type_two_phase_head_checkpoint`, also used by `_type_literal_verified`)
so it can pass `allow_scrolled=True` to the final verify SAFELY: the checkpoint
proves the leading `/` landed BEFORE the box scrolls, so a head-SWALLOWED long
`/goal` (identical own-substring tail) can never slip to a junk submit (#720).

The PARKED-branch pop/restore (`_undo_and_release_slot` with `parked=True`) is
covered by tests/test_stash_delivery.py / test_stash_unconditional.py; the
stateful scrolling fake models the NOOP bare branch (a foreign draft that turned
out to be a ghost -- the same two-phase typing), which is where the misread lives.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from watchdog import stash  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_IDLE_CAP, _SwallowFirstCharFake,
)

PID = "%9"
WRAP = 60          # narrow, so the box wraps into many rows
VIS = 20           # visible box height: a payload wrapping past this SCROLLS


def _scrolled_goal():
    """A realistic ~3.3k-char full-authority /goal template -- NON-periodic
    (numbered clauses) so the SCROLLED head row is genuinely mid-payload and
    head-is-prefix is False. Starts `/goal ` so the fake arms on submit; no
    trailing whitespace (a real template never has one; it would break the
    tail-suffix match)."""
    body = " ".join(
        "clause-%02d work the backlog one ticket at a time until every workable "
        "issue %02d is done, never stop early, and surface every per-ticket "
        "question the moment it arises" % (i, i)
        for i in range(22))
    return ("/goal STOP CONDITIONS: all named issues closed AND CI all-green "
            "AND the PR mergeable and clean; " + body).strip()


GOAL_SCROLLED = _scrolled_goal()


def _tpath(testcase):
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    p = Path(d.name) / "sess.jsonl"
    p.write_text(json.dumps(
        {"type": "assistant", "message": {"content": "predosla praca"}}) + "\n")
    return p


def _submitted_turns(p):
    out = []
    for ln in p.read_text().splitlines():
        e = json.loads(ln)
        if e.get("type") == "user":
            out.append(e["message"]["content"])
    return out


class ScrolledGoalViaStashNoopBranchDelivers(unittest.TestCase):
    """The #747 root case: a fully, correctly typed long /goal delivered via the
    stash route's bare (NOOP) branch on a SCROLLED box must DELIVER + submit, not
    be misread CORRUPT and abort."""

    def test_payload_scrolls_the_box(self):
        self.assertGreater(len(GOAL_SCROLLED), 3000)     # scrolls the box
        self.assertLess(len(GOAL_SCROLLED), 4000)        # under CC's ~4000 cap

    def _fake(self, p):
        return DeliverGoalFakeTmux(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p, wrap_width=WRAP, visible_rows=VIS)

    def test_scrolled_goal_via_stash_delivers_and_submits_byte_exact(self):
        # END-TO-END: `deliver_with_stash` NOOP branch on a scrolling box.
        # Pre-fix: the bare-branch verify runs allow_scrolled=False -> the scrolled
        # box reads CORRUPT -> stash-abort: type-verify-failed -> False, never
        # submitted (RED == the live #747 `SKIP stash-abort`).
        p = _tpath(self)
        tmux = self._fake(p)
        logs = []
        ok = stash.deliver_with_stash(PID, GOAL_SCROLLED, tmux,
                                      captured=GOAL_IDLE_CAP, logs=logs,
                                      sleep_fn=lambda *_a: None)
        self.assertTrue(
            ok, "a scrolled /goal via the stash NOOP branch was misread "
                "not-landed: %r" % logs)
        self.assertIn(GOAL_SCROLLED, _submitted_turns(p))


class StashHeadSwallowNeverJunkSubmitted(unittest.TestCase):
    """The #720 head-swallow class must stay CLOSED on the stash route too:
    substring acceptance alone cannot tell scrolled-landed from head-swallowed,
    so the checkpoint must catch the swallow at the first chunk and the stash
    route must NEVER submit a ~3.3k-char junk prompt (it aborts to next sweep --
    DETECTION only, no retype)."""

    def _fake(self, p, swallow_budget):
        return _SwallowFirstCharFake(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p, wrap_width=WRAP, visible_rows=VIS,
            swallow_budget=swallow_budget)

    def test_swallowed_head_aborts_and_submits_nothing(self):
        p = _tpath(self)
        tmux = self._fake(p, swallow_budget=1)
        logs = []
        ok = stash.deliver_with_stash(PID, GOAL_SCROLLED, tmux,
                                      captured=GOAL_IDLE_CAP, logs=logs,
                                      sleep_fn=lambda *_a: None)
        self.assertFalse(ok, "a head-swallowed /goal must abort, never deliver")
        self.assertEqual(_submitted_turns(p), [],
                         "a head-swallowed scrolled /goal was SUBMITTED")
        self.assertNotIn("Enter", tmux.keys(),
                         "Enter was pressed after a head-swallow")
        # the CORRUPT recovery ran: the head chunk was backed off (bare-verified
        # box, so len-based backspaces are safe).
        self.assertIn("BSpace", tmux.keys())

    def test_persistent_swallow_never_submits(self):
        p = _tpath(self)
        tmux = self._fake(p, swallow_budget=99)
        ok = stash.deliver_with_stash(PID, GOAL_SCROLLED, tmux,
                                      captured=GOAL_IDLE_CAP, logs=[],
                                      sleep_fn=lambda *_a: None)
        self.assertFalse(ok)
        self.assertEqual(_submitted_turns(p), [])


# CC's 'paste again to expand' collapse hint -> HOLD (unreadable/collapsed box).
COLLAPSE_CAP = ("● Hotovo.\n\n" + "─" * 40 + "\n❯ paste again to expand\n"
                + "─" * 40 + "\n  ctx ███░  caveman:lite\n")
BARE_CAP = "● turn done\n❯\xa0\n  ctx ░░\n"


class StashCheckpointHoldAbortsWithZeroFurtherKeystrokes(unittest.TestCase):
    """The two-phase checkpoint's HOLD branch on the stash route: if the box goes
    unreadable/collapsed right after the FIRST chunk, `deliver_with_stash` must
    abort with ZERO further keystrokes -- the remainder is NEVER typed, Enter is
    NEVER pressed, and NO undo backspaces are sprayed at an unreadable box (the
    #233/#322/#372 'no keystrokes into a HOLD box' discipline). The park record
    (#488) + janitor reclaim the (possibly) parked draft."""

    def test_checkpoint_hold_types_only_the_checkpoint_chunk_never_more(self):
        # cap[0] (bare) -> _await_stash_settled = NOOP; cap[1+] (collapsed) ->
        # the head-checkpoint settle-verify = HOLD.
        tmux = DeliverGoalFakeTmux(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            cap_seq=(BARE_CAP, COLLAPSE_CAP))
        logs = []
        ok = stash.deliver_with_stash(PID, GOAL_SCROLLED, tmux,
                                      captured=GOAL_IDLE_CAP, logs=logs,
                                      sleep_fn=lambda *_a: None)
        self.assertFalse(ok)
        self.assertEqual(
            tmux.typed_texts(),
            [GOAL_SCROLLED[:stash.GOAL_TYPE_CHECKPOINT_CHARS]],
            "the remainder was typed after a HOLD checkpoint: %r"
            % tmux.typed_texts())
        self.assertNotIn("Enter", tmux.keys())
        self.assertNotIn("BSpace", tmux.keys(),
                         "backspaces were sprayed at a HOLD (unreadable) box")


class ShortPayloadStashPathUnchanged(unittest.TestCase):
    """A SHORT (non-scroll-length) payload never two-phases and never scrolls, so
    the stash bare branch stays byte-identical to pre-#747 (head visible ->
    head-is-prefix, single `_type_literal`, single-capture verify)."""

    def test_short_payload_delivers_single_phase(self):
        p = _tpath(self)
        short = "/goal do the one small thing and stop"
        tmux = DeliverGoalFakeTmux(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p)     # no wrap -> single-row, never scrolls
        ok = stash.deliver_with_stash(PID, short, tmux, captured=GOAL_IDLE_CAP,
                                      logs=[], sleep_fn=lambda *_a: None)
        self.assertTrue(ok)
        self.assertIn(short, _submitted_turns(p))
        # short payload: the checkpoint is skipped -> the whole text typed in ONE
        # `-l` burst (never split into a 120-char chunk + remainder).
        self.assertEqual(tmux.typed_texts(), [short])


if __name__ == "__main__":
    unittest.main()
