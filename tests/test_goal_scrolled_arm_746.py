"""#746 — a long `/goal` SCROLLS CC's input box, so the visible head row is
MID-payload and `_type_verify_class`'s head-is-prefix gate is structurally
unsatisfiable → a genuinely-landed /goal is misread CORRUPT → undo+retype →
give-up → the goal never arms (`skip:verify-failed` ×3 → `drop:attempt-cap`,
the live gk timeline). #720 routed the bare-box send through this head+tail
verify, so long-template arming has been broken fleet-wide since v0.1.84.

The fix (a) accepts a SCROLLED own-substring box as LANDED in `_type_verify_class`
ONLY when the caller has already proven the head landed (`allow_scrolled=True`),
and (b) makes `_type_literal_verified` two-phase for a scroll-length payload:
type a short FIRST chunk, settle-verify head-is-prefix on the still-UNSCROLLED
box (a cheap first-byte-swallow catch), then type the rest, then final verify
with `allow_scrolled=True`. The checkpoint is what keeps the #720 head-swallow
class closed — substring alone cannot tell scrolled-LANDED from head-SWALLOWED on
a long payload (the swallowed `/` is off-screen at the head).

These drive the SAME stateful fake tmux the goal tests use, now modelling a
SCROLLING box (`visible_rows`): the box shows only its last N wrapped rows once
it out-scrolls the visible height, exactly today's gk state.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import watchdog.goal as goal  # noqa: E402
from watchdog import stash  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_IDLE_CAP, _SwallowFirstCharFake,
)

PID = "%9"
WRAP = 60          # narrow, so the box wraps into many rows
VIS = 20           # visible box height: a payload wrapping past this SCROLLS


def _scrolled_goal():
    """A realistic ~3.3k-char full-authority /goal template — NON-periodic
    (numbered clauses) so the SCROLLED head row is genuinely mid-payload and
    head-is-prefix is False (a periodic payload falsely aligns the visible head
    to a `/goal` boundary — the repro trap). Starts `/goal ` so the fake arms on
    submit; no trailing whitespace (a real template never has one, and it would
    break the tail-suffix match)."""
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


class ScrolledLandedGoalArms(unittest.TestCase):
    """The #746 root case: a fully, correctly typed long /goal on a SCROLLED
    box must ARM, not be misread CORRUPT."""

    def test_payload_is_scroll_length_and_non_periodic(self):
        self.assertGreater(len(GOAL_SCROLLED), 3000)     # scrolls the box
        self.assertLess(len(GOAL_SCROLLED), 4000)        # under CC's ~4000 cap
        # non-periodic: the first ~120 chars appear exactly ONCE in the payload
        head = GOAL_SCROLLED[:120]
        self.assertEqual(GOAL_SCROLLED.count(head[40:120]), 1)

    def _fake(self, p):
        return DeliverGoalFakeTmux(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p, wrap_width=WRAP, visible_rows=VIS)

    def test_scrolled_goal_type_is_verified_landed(self):
        # UNIT: `_type_literal_verified` must return True for a clean type that
        # SCROLLS the box. Pre-fix: the scrolled final verify reads head-not-
        # prefix -> CORRUPT -> undo+retry -> give up -> False (RED).
        p = _tpath(self)
        tmux = self._fake(p)
        ok = stash._type_literal_verified(PID, tmux, GOAL_SCROLLED,
                                          sleep_fn=lambda *_a: None)
        self.assertTrue(
            ok, "a clean type that scrolled the box was misread not-landed")

    def test_scrolled_goal_arms_and_submits_byte_exact(self):
        # END-TO-END: `_send_goal_verified` must ARM the scrolled /goal and
        # submit it byte-exact. Pre-fix: type-verify aborts -> False, never
        # submitted (RED == the live #746 `skip:verify-failed`).
        p = _tpath(self)
        tmux = self._fake(p)
        ok = goal._send_goal_verified(PID, GOAL_SCROLLED, tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertTrue(ok, "a landed scrolled /goal must arm, not skip")
        self.assertIn(GOAL_SCROLLED, _submitted_turns(p))


class ScrolledHeadSwallowNeverJunkSubmitted(unittest.TestCase):
    """The #720 head-swallow class must stay CLOSED on a scrolled payload:
    substring acceptance alone cannot tell scrolled-landed from head-swallowed,
    so the checkpoint must catch the swallow at the first chunk and NEVER submit
    a ~3.3k-char junk prompt."""

    def _fake(self, p, swallow_budget):
        return _SwallowFirstCharFake(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p, wrap_width=WRAP, visible_rows=VIS,
            swallow_budget=swallow_budget)

    def test_recoverable_swallow_recovers_and_arms_no_junk(self):
        # swallow_budget=1: the checkpoint catches the swallowed head at the
        # FIRST chunk, undoes+retypes, and the clean retry arms. Every submitted
        # turn must start with `/goal` — never a head-swallowed `goal STOP...`.
        p = _tpath(self)
        tmux = self._fake(p, swallow_budget=1)
        ok = goal._send_goal_verified(PID, GOAL_SCROLLED, tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertTrue(ok, "a recoverable first-byte race must still arm")
        for turn in _submitted_turns(p):
            self.assertTrue(turn.startswith("/goal "),
                            "a head-swallowed junk prompt was SUBMITTED: %r"
                            % turn[:40])
        self.assertIn(GOAL_SCROLLED, _submitted_turns(p))

    def test_persistent_swallow_never_submits_anything(self):
        # swallow_budget=99: the checkpoint keeps reading CORRUPT, the retries
        # exhaust, and NOTHING is ever submitted (the load-bearing safety: a
        # head-swallowed long /goal is NEVER Enter'd, scrolled or not).
        p = _tpath(self)
        tmux = self._fake(p, swallow_budget=99)
        ok = goal._send_goal_verified(PID, GOAL_SCROLLED, tmux,
                                      captured=GOAL_IDLE_CAP,
                                      sleep_fn=lambda *_a: None, logs=[])
        self.assertFalse(ok)
        self.assertEqual(_submitted_turns(p), [],
                         "a head-swallowed scrolled /goal was SUBMITTED")


class TypeVerifyClassScrolledGating(unittest.TestCase):
    """The gating that keeps the substring-LANDED acceptance opt-in: a scrolled
    own box is CORRUPT under the default (allow_scrolled=False -- deliver_with_
    stash / stranded / nudge), LANDED only when the caller passed
    allow_scrolled=True (after its head-checkpoint)."""

    def _scrolled_cap(self):
        tmux = DeliverGoalFakeTmux(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            wrap_width=WRAP, visible_rows=VIS)
        for i in range(0, len(GOAL_SCROLLED), 120):
            tmux([("tmux"), "send-keys", "-t", PID, "-l", "--",
                  GOAL_SCROLLED[i:i + 120]])
        return tmux([("tmux"), "capture-pane", "-t", PID, "-p"])

    def test_scrolled_box_default_is_corrupt_true_is_landed(self):
        cap = self._scrolled_cap()
        # sanity: the visible head really is mid-payload (not a prefix)
        head = wd._input_box_head_text(cap)
        self.assertFalse(" ".join(GOAL_SCROLLED.split()).startswith(
            " ".join((head or "").split())))
        self.assertTrue(GOAL_SCROLLED.endswith(wd._input_line_text(cap)))
        self.assertEqual(
            stash._type_verify_class(PID, lambda *a, **k: cap, GOAL_SCROLLED,
                                     cap=cap),
            stash._TV_CORRUPT,
            "default (allow_scrolled=False) must stay strict for the stash/"
            "stranded/nudge callers")
        self.assertEqual(
            stash._type_verify_class(PID, lambda *a, **k: cap, GOAL_SCROLLED,
                                     cap=cap, allow_scrolled=True),
            stash._TV_LANDED,
            "a checkpointed caller must accept the scrolled own-substring")


# CC's 'paste again to expand' collapse hint -> `_type_verify_class` reads HOLD
# (an unreadable/collapsed box no `_undo_typed_text` may backspace).
COLLAPSE_CAP = ("● Hotovo.\n\n" + "─" * 40 + "\n❯ paste again to expand\n"
                + "─" * 40 + "\n  ctx ███░  caveman:lite\n")


class CheckpointHoldAbortsWithZeroFurtherKeystrokes(unittest.TestCase):
    """The two-phase checkpoint's HOLD branch: if the box goes unreadable /
    collapsed right after the FIRST chunk (a turn/dialog/collapse racing in),
    `_type_literal_verified` must abort with ZERO further keystrokes -- the
    remainder is NEVER typed and Enter is NEVER pressed (the #233/#322/#372
    'no keystrokes into a HOLD box' discipline the fix's docstring promises).
    Locks the branch Reviewer 2 found untested (mutating it to `pass` submits
    the remainder into an unreadable box)."""

    def test_checkpoint_hold_types_only_the_checkpoint_chunk_never_more(self):
        tmux = DeliverGoalFakeTmux(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            cap_seq=(COLLAPSE_CAP,))     # every capture reads collapsed -> HOLD
        ok = stash._type_literal_verified(PID, tmux, GOAL_SCROLLED,
                                          sleep_fn=lambda *_a: None)
        self.assertFalse(ok)
        self.assertEqual(
            tmux.typed_texts(),
            [GOAL_SCROLLED[:stash.GOAL_TYPE_CHECKPOINT_CHARS]],
            "the remainder was typed into a HOLD box after the checkpoint")
        self.assertNotIn("Enter", tmux.keys())


if __name__ == "__main__":
    unittest.main()
