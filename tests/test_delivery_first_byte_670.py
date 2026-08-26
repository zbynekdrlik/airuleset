"""#670 — swallowed FIRST character on the shared keystroke delivery path.

The owner logged the lane-check nudge arriving as "ane-check…" (first char 'l'
dropped) — the classic send-keys first-byte race, where CC's input reader drops
the opening keystroke of a burst. `_typed_landed`'s TAIL `endswith` verify is
HEAD-BLIND ("ane-check…" IS a suffix of "lane-check…"), so the corrupted prompt
was submitted. The fix adds a shared head-inclusive read-back + bounded
undo/retry (`_type_literal_verified`) so the delivered prompt is byte-exact or
the caller aborts — never a submitted "ane-check".

These tests drive the SAME stateful fake tmux the goal tests use, extended to
model a first-char swallow (drop the first char of a fresh type's first burst),
so the whole type-verify-submit protocol runs end to end.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_IDLE_CAP, _SwallowFirstCharFake,
)

PID = "%9"
# A realistic wrapped nudge: >200 chars (so `_type_literal` CHUNKS) and it
# WRAPS at the fake's width, so head and tail rows DIFFER — the shape a real
# 550–720-char lane-check nudge always takes, and the ONLY shape where a
# dropped FIRST char is invisible to a tail-only verify.
TEXT = ("lane-check: backlog=22 OTVORENYCH tiketov (nie vsetky musia byt hned "
        "dispatchnute), no bezi len 1 z cielovych 5 lan — ak sa da paralelne, "
        "rozbehni dalsie worktree lany; ak nie, vysvetli preco. pozri SKILL "
        "autopilot pre fleet dispatch a bundling gate detaily tu.")
WRAP = 60


def _tpath():
    d = TemporaryDirectory()
    p = Path(d.name) / "sess.jsonl"
    p.write_text(json.dumps(
        {"type": "assistant", "message": {"content": "predosla praca"}}) + "\n")
    return d, p


def _submitted_turns(p):
    """Every `user` turn the fake recorded (i.e. every prompt SUBMITTED)."""
    out = []
    for ln in p.read_text().splitlines():
        e = json.loads(ln)
        if e.get("type") == "user":
            out.append(e["message"]["content"])
    return out


class SendVerifiedNeverSubmitsSwallowedHead(unittest.TestCase):
    def _fake(self, p, swallow_budget):
        return _SwallowFirstCharFake(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            transcript_path=p, wrap_width=WRAP, swallow_budget=swallow_budget)

    def test_swallowed_first_char_is_never_submitted(self):
        # The load-bearing invariant: a head-swallowed prompt must NEVER be
        # submitted. Before the fix, the tail-only verify passes "ane-check…"
        # and Enter submits it (a corrupted user turn); after the fix the
        # head-inclusive verified-retry re-types byte-exact first.
        d, p = _tpath()
        self.addCleanup(d.cleanup)
        tmux = self._fake(p, swallow_budget=1)
        wd.send_verified(PID, TEXT, run=tmux, tpath=p, sleep_fn=lambda *_: None,
                         logs=[])
        for turn in _submitted_turns(p):
            self.assertFalse(
                turn.startswith("ane-check"),
                "a head-swallowed prompt was SUBMITTED: %r" % turn[:30])

    def test_recovered_delivery_submits_byte_exact(self):
        # A single-swallow race is RECOVERED: the retry lands byte-exact and
        # THAT is what gets submitted + transcript-confirmed.
        d, p = _tpath()
        self.addCleanup(d.cleanup)
        tmux = self._fake(p, swallow_budget=1)
        ok = wd.send_verified(PID, TEXT, run=tmux, tpath=p,
                              sleep_fn=lambda *_: None, logs=[])
        self.assertTrue(ok, "a recoverable first-byte race must still deliver")
        self.assertIn(TEXT, _submitted_turns(p))


class TypeLiteralVerifiedContract(unittest.TestCase):
    def _fake(self, swallow_budget):
        return _SwallowFirstCharFake(
            [(PID, "sess", "1234", "1234")], GOAL_IDLE_CAP, model_type=True,
            wrap_width=WRAP, swallow_budget=swallow_budget)

    def test_clean_type_lands_byte_exact_true(self):
        tmux = self._fake(swallow_budget=0)
        ok = wd._type_literal_verified(PID, tmux, TEXT, sleep_fn=lambda *_: None)
        self.assertTrue(ok)
        self.assertEqual(tmux.box, TEXT)

    def test_single_swallow_retries_to_byte_exact_true(self):
        tmux = self._fake(swallow_budget=1)
        ok = wd._type_literal_verified(PID, tmux, TEXT, sleep_fn=lambda *_: None)
        self.assertTrue(ok, "one swallow must be undone + re-typed byte-exact")
        self.assertEqual(tmux.box, TEXT)

    def test_persistent_swallow_returns_false_and_leaves_box_bare(self):
        # Every fresh type swallows -> after the bounded retries it gives up
        # with False (the caller aborts, never submits). A CORRUPT give-up backs
        # our own text off, so the box is left BARE -- never a stranded
        # 'ane-check...' (#670-review R2 / 🔵6).
        tmux = self._fake(swallow_budget=99)
        ok = wd._type_literal_verified(PID, tmux, TEXT, sleep_fn=lambda *_: None)
        self.assertFalse(ok)
        self.assertEqual(tmux.box, "")


class _RunningTurnFake(DeliverGoalFakeTmux):
    """After a type, `capture-pane` shows a RUNNING-TURN frame with NO input box
    (`_input_line_text` -> None) -- a turn/dialog started mid-type. Records every
    keystroke so the test can prove NONE were sent into the unreadable pane."""

    def __call__(self, argv, timeout=8):
        if "capture-pane" in " ".join(argv):
            return "● Pracujem na tom…\n  (esc na prerušenie)\n  ctx ███░\n"
        return super().__call__(argv, timeout)


class _CollapsedPasteFake(DeliverGoalFakeTmux):
    """After a type, `capture-pane` shows CC's 'paste again to expand' collapse
    hint (#322) -- a box `_undo_typed_text` must never backspace."""

    def __call__(self, argv, timeout=8):
        if "capture-pane" in " ".join(argv):
            return "● hotovo\n❯ paste again to expand\n  ctx ███░\n"
        return super().__call__(argv, timeout)


class HoldBoxesGetNoKeystrokes(unittest.TestCase):
    # #670-review R2: an UNREADABLE or COLLAPSED box (HOLD) must return False
    # WITHOUT any keystroke -- never a blind backspace into a pane we cannot read
    # (#233) or a collapsed buffer (#322/#372). The old always-undo did the wrong
    # thing here.
    def _bspaces(self, tmux):
        return [a for a in tmux.sent
                if len(a) > 4 and all(k == "BSpace" for k in a[4:])]

    def test_unreadable_box_aborts_with_no_keystrokes(self):
        tmux = _RunningTurnFake([(PID, "s", "1", "1")], GOAL_IDLE_CAP,
                                model_type=True, wrap_width=WRAP)
        ok = wd._type_literal_verified(PID, tmux, TEXT, sleep_fn=lambda *_: None)
        self.assertFalse(ok)
        self.assertEqual(self._bspaces(tmux), [],
                         "no backspaces may be sent into an unreadable pane")

    def test_collapsed_paste_aborts_with_no_keystrokes(self):
        tmux = _CollapsedPasteFake([(PID, "s", "1", "1")], GOAL_IDLE_CAP,
                                   model_type=True, wrap_width=WRAP)
        ok = wd._type_literal_verified(PID, tmux, TEXT, sleep_fn=lambda *_: None)
        self.assertFalse(ok)
        self.assertEqual(self._bspaces(tmux), [],
                         "no backspaces may be sent into a collapsed buffer")


if __name__ == "__main__":
    unittest.main()
