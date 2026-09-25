"""#1157 — a verify-failed type is ALWAYS undone (or reported stranded), its
outcome is logged truthfully, and the long wrapped nudge verifies.

Incident (gk, 25.9.2026, window gk-infra = pane zbynek:1.0): the ~720-char
job-20 partition-audit batch nudge sat UNSENT in the input box from 18:26 for
4 h. The watchdog journal read `send-verified abort: type not head+tail-verified,
not submitted` then `batch-nudge zbynek:1.0 -> deferred (not typed: box
busy/raced)`, and afterwards `send-verified abort: box not bare pre-send` every
minute. Three defects:

* `send_verified` never undid text a verify-failed type left behind (a HOLD
  verdict withholds every keystroke; a non-converging undo leaves text);
* the batch / gkreq callers logged `not typed` / `submit-unverified` although a
  type happened;
* a long nudge in a SHORT pane never verifies: Claude Code scrolls the input box
  (live-measured, CC 2.1.281: width 176, height 12-16 shows 3 of 4 rows), the
  first visible row carries the `❯` glyph mid-payload, so head-is-prefix is
  false for a perfectly typed nudge. The #746 scrolled acceptance was gated to
  payloads >= 1000 chars. The same render on a REAL private tmux server is
  covered by tests/test_send_verified_tmux_1157.py.

Fakes only here (no tmux, no gh, no Discord): the stateful
`DeliverGoalFakeTmux` model already renders the wrapped and the scrolled box.
"""
import json
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402,F401
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from watchdog import nudge_gate  # noqa: E402
from watchdog import ops_wait_recheck as owr  # noqa: E402
from watchdog import send_outcome  # noqa: E402
from watchdog import session_status as ss  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_ARMED_CAP, GOAL_IDLE_CAP,
    _write_marker_transcript,
)

PID = "%9"
NOW = 1_000_000
CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"
# A render with NO locatable input box (a turn / dialog frame): the verifier
# reads it as HOLD and withholds every keystroke.
UNREADABLE = "● Hotovo.\n"


def partition_batch_text():
    """The incident's payload shape, rebuilt from the REAL job-20 renderer: the
    partition-audit nudge (I=7, W=12 parked, deploy-window + release-landed
    flags) wrapped by the #923 batch composer -> 722 chars."""
    w = [{"number": n, "labels": ["ops-wait"]} for n in range(100, 112)]
    t = owr._nudge_text(7, w, deploy_window=[1], release_landed=[5, 6, 7, 8])
    text, _inc = nudge_gate.compose_batch([("partition-audit", t)],
                                          max_chars=nudge_gate.BATCH_MAX_CHARS)
    return text


class _HoldAfterTypeFake(DeliverGoalFakeTmux):
    """After the first literal type lands, the next `holds` captures read
    UNREADABLE (no input box), then the real box again. `holds=1` models a
    one-frame render blip during the settle poll: the verifier concludes HOLD
    and withholds every keystroke, so pre-#1157 the typed text stays."""

    def __init__(self, *a, holds=1, **kw):
        super().__init__(*a, **kw)
        self.holds = holds
        self._typed = False

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "send-keys" in j and "-l" in argv:
            self._typed = True
        if "capture-pane" in j and self._typed and self.holds > 0:
            self.holds -= 1
            return UNREADABLE
        return super().__call__(argv, timeout)


class _DropRestFirstByteFake(DeliverGoalFakeTmux):
    """Drops the first byte of the FIRST chunk typed into a non-empty box (the
    rest after the head checkpoint) once: the #670 race on a later burst."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.dropped = 0

    def __call__(self, argv, timeout=8):
        if "send-keys" in " ".join(argv) and "-l" in argv and self.box \
                and not self.dropped:
            self.sent.append(argv)
            self.dropped += 1
            self.box += argv[-1][1:]
            return ""
        return super().__call__(argv, timeout)


class _SpinnerAfterTypeFake(_HoldAfterTypeFake):
    """After the type, a RUNNING-turn spinner renders above the box (a turn
    started under the send), so the box is readable but the pane is busy."""

    SPINNER = "✳ Baking… (2m 30s · esc to interrupt)"

    def _render(self):
        base = super()._render()
        if not self._typed:
            return base
        lines = base.split("\n")
        at = next((i for i, ln in enumerate(lines)
                   if ln.startswith("❯") or ln.startswith("─")), 1)
        lines.insert(at, self.SPINNER)     # the row right above the box
        return "\n".join(lines)


class _StashedFake(DeliverGoalFakeTmux):
    """The owner's own draft sits in the single stash slot (the marker shows)."""

    def _render(self):
        return super()._render() + "  › stashed\n"


class _HumanAppendFake(_HoldAfterTypeFake):
    """During the one-frame blip after our type, the owner types behind it."""

    APPEND = " a este toto som dopisal ja"

    def __call__(self, argv, timeout=8):
        if ("capture-pane" in " ".join(argv) and self._typed and self.holds > 0):
            self.box += self.APPEND
        return super().__call__(argv, timeout)


def _tpath(testcase):
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    p = Path(d.name) / "sess.jsonl"
    p.write_text(json.dumps({"type": "assistant",
                             "message": {"content": "predosla praca"}}) + "\n")
    return p


def _kind(outcome):
    return getattr(outcome, "kind", None)


def _noop(*_a, **_k):
    return None


class VerifyFailedTypeIsUndone(unittest.TestCase):
    """(a) + (b): a type that landed but failed verification is backed out by
    the ONE own-provenance janitor undo, and the outcome says so."""

    def test_verify_failed_type_is_undone_and_logged(self):
        text = partition_batch_text()
        fake = _HoldAfterTypeFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                  model_type=True, holds=1,
                                  transcript_path=_tpath(self))
        logs = []
        res = wd.send_verified(PID, text, fake, fake.transcript_path,
                               sleep_fn=_noop, logs=logs,
                               nudge="partition-audit")
        self.assertFalse(res, logs)
        self.assertEqual(fake.box, "",
                         "a verify-failed type must never leave own text: %r"
                         % logs)
        self.assertTrue(any("typed then undone" in ln for ln in logs), logs)
        self.assertEqual(_kind(res), "typed-undone", logs)
        self.assertNotIn("Enter", fake.keys(), "never a submit after a failed verify")

    def test_unreadable_box_is_never_keyed_and_reported_stranded(self):
        # The box stays unreadable (the h=10 overflow render): no undo keystroke
        # may go into a box we cannot read; the outcome is `typed-stranded`, the
        # janitor watch is armed and the exact text recorded for its retry.
        text = partition_batch_text()
        fake = _HoldAfterTypeFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                  model_type=True, holds=10_000,
                                  transcript_path=_tpath(self))
        logs, state = [], {}
        res = wd.send_verified(PID, text, fake, fake.transcript_path,
                               sleep_fn=_noop, logs=logs, state=state,
                               nudge="partition-audit", now=NOW)
        self.assertFalse(res)
        self.assertEqual(_kind(res), "typed-stranded", logs)
        last_type = max(i for i, a in enumerate(fake.sent) if "-l" in a)
        after = [a[-1] for a in fake.sent[last_type + 1:]]
        self.assertEqual(after, [], "no keystroke into an unreadable box: %r" % after)
        self.assertTrue(any("undo withheld (unreadable)" in ln for ln in logs), logs)
        self.assertEqual(state.get("janitor_watch", {}).get(PID), NOW, state)
        self.assertEqual(state["stranded_own"][PID]["typed"], text, state)
        # a bare-box leak never becomes a stash park record (#488: that record
        # would license popping a stash slot the owner may hold)
        self.assertNotIn(PID, state.get("stash_parks", {}), state)
        # a typed attempt books the per-pane typing budget (#1092 (c))
        self.assertEqual(len(nudge_gate._pane_attempts(state, PID, NOW)), 1, state)

    def test_busy_pane_after_a_failed_verify_gets_no_escape(self):
        # A turn started under the send: the box still shows our text but a
        # spinner runs above it. The clear's Escape would interrupt the turn
        # (#233/#1104), so no keystroke; the text is recorded for the janitor.
        text = partition_batch_text()
        fake = _SpinnerAfterTypeFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                     model_type=True, holds=1,
                                     transcript_path=_tpath(self))
        logs, state = [], {}
        res = wd.send_verified(PID, text, fake, fake.transcript_path,
                               sleep_fn=_noop, logs=logs, state=state,
                               nudge="partition-audit", now=NOW)
        self.assertEqual(_kind(res), "typed-stranded", logs)
        last_type = max(i for i, a in enumerate(fake.sent) if "-l" in a)
        self.assertEqual([a[-1] for a in fake.sent[last_type + 1:]], [], logs)
        self.assertTrue(any("turn running under the box" in ln for ln in logs), logs)
        self.assertEqual(state["stranded_own"][PID]["typed"], text, state)
        # the NEXT sweep's janitor (watch armed, own `nudge:` head) must not
        # Escape the still-running turn either
        sent_before, box_before = len(fake.sent), fake.box
        self.assertTrue(box_before)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            jlogs = wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc",
                                        _noop, False, _noop, state=state,
                                        now=NOW + 60, own_payload=None)
        self.assertEqual(fake.sent[sent_before:], [], jlogs)
        self.assertTrue(any("hold:busy" in ln for ln in jlogs), jlogs)
        self.assertEqual(fake.box, box_before)

    def test_stateless_caller_is_told_nothing_was_recorded(self):
        fake = _HoldAfterTypeFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                  model_type=True, holds=10_000,
                                  transcript_path=_tpath(self))
        logs = []
        res = wd.send_verified(PID, partition_batch_text(), fake,
                               fake.transcript_path, sleep_fn=_noop, logs=logs,
                               nudge="partition-audit")
        self.assertEqual(_kind(res), "typed-stranded", logs)
        self.assertTrue(any("no state threaded, nothing recorded" in ln
                            for ln in logs), logs)

    def test_an_off_flip_after_the_head_chunk_is_journalled(self):
        # #994/#1002: the rest chunk suppressed mid-delivery writes the kill
        # switch journal line into the caller's logs (threaded per chunk).
        flips = iter([True] + [False] * 200)
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=_tpath(self))
        logs, text = [], partition_batch_text()
        with m.patch.object(wd, "nudges_enabled", lambda kind=None: next(flips)):
            res = wd.send_verified(PID, text, fake, fake.transcript_path,
                                   sleep_fn=_noop, logs=logs,
                                   nudge="partition-audit")
        self.assertFalse(res)
        rest = text[120:150]           # the first suppressed chunk is the REST
        self.assertTrue(any(ln.startswith("nudges OFF: suppressed") and rest in ln
                            for ln in logs), logs)
        self.assertNotIn("Enter", fake.keys())

    def test_a_suppressed_type_is_not_typed_and_never_undone(self):
        # Kill switch OFF: nothing typed -> `not-typed`, and no undo keystroke.
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=_tpath(self))
        logs = []
        with m.patch.object(wd, "nudges_enabled", lambda kind=None: False):
            res = wd.send_verified(PID, partition_batch_text(), fake,
                                   fake.transcript_path, sleep_fn=_noop,
                                   logs=logs, nudge="partition-audit")
        self.assertFalse(res)
        self.assertEqual(_kind(res), "not-typed", logs)
        self.assertEqual(fake.sent, [], "zero keystrokes at OFF")

    def test_a_busy_box_is_not_typed(self):
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=_tpath(self),
                                   initial_box="rozpisany draft")
        logs = []
        res = wd.send_verified(PID, partition_batch_text(), fake,
                               fake.transcript_path, sleep_fn=_noop, logs=logs,
                               nudge="partition-audit")
        self.assertFalse(res)
        self.assertEqual(_kind(res), "not-typed", logs)
        self.assertEqual(fake.box, "rozpisany draft")

    def test_submitted_outcome_keeps_the_old_truthiness(self):
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=_tpath(self))
        res = wd.send_verified(PID, "nudge: [x] kratky text", fake,
                               fake.transcript_path, sleep_fn=_noop, logs=[],
                               nudge="partition-audit")
        self.assertTrue(res)
        self.assertEqual(res, True)
        self.assertEqual(_kind(res), "submitted")


class ForeignDraftNeverCleared(unittest.TestCase):
    """The undo clears only a box that PROVABLY holds our own text."""

    def _fake(self, box):
        return DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, initial_box=box)

    def test_foreign_draft_is_left_untouched(self):
        fake = self._fake("moja vlastna poznamka k tiketu, nic spolocne s nudge")
        logs = []
        cleared = goal._janitor_undo_if_own_stranded(
            PID, fake, partition_batch_text(), "loc", _noop, logs)
        self.assertFalse(cleared)
        self.assertNotIn("BSpace", fake.keys())
        self.assertNotIn("Escape", fake.keys())

    def test_own_prefix_followed_by_a_human_append_is_left_untouched(self):
        # A human typed behind our stranded nudge: the box is no longer a
        # contiguous run of OUR text, so the whole-box clear would eat the
        # human's words. The own `nudge:` head prefix alone is not proof.
        own = partition_batch_text()
        fake = self._fake(own + " a este toto som dopisal ja")
        logs = []
        cleared = goal._janitor_undo_if_own_stranded(
            PID, fake, own, "loc", _noop, logs)
        self.assertFalse(cleared, logs)
        self.assertNotIn("BSpace", fake.keys())
        self.assertIn("a este toto som dopisal ja", fake.box)


class UndoRecognition(unittest.TestCase):
    """The busy and ownership branches of the ONE undo."""

    def test_waiting_for_background_agents_is_busy(self):
        cap = ("● Hotovo.\n✻ Waiting for 2 background agents to finish\n"
               "❯ nudge: [x] our text\n  ctx ███░  caveman:lite\n")
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], cap)
        logs, out = [], {}
        cleared = goal._janitor_undo_if_own_stranded(
            PID, fake, "nudge: [x] our text", "loc", _noop, logs, out=out)
        self.assertFalse(cleared)
        self.assertEqual(out["box"], "busy", logs)
        self.assertEqual(fake.sent, [])

    def test_collapsed_placeholder_is_ours(self):
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True,
                                   initial_box="[Pasted text #1 +3 lines]")
        logs = []
        cleared = goal._janitor_undo_if_own_stranded(
            PID, fake, partition_batch_text(), "loc", _noop, logs)
        self.assertTrue(cleared, logs)
        self.assertEqual(fake.box, "")


class UndoRecognitionMore(unittest.TestCase):
    def test_a_short_payload_without_prefix_is_undone(self):
        # `continue` (the resume nudge) is 8 chars with no own prefix: a box that
        # is exactly our text is ours at any length.
        fake = _HoldAfterTypeFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                  model_type=True, holds=1,
                                  transcript_path=_tpath(self))
        logs = []
        res = wd.send_verified(PID, "continue", fake, fake.transcript_path,
                               sleep_fn=_noop, logs=logs, nudge="resume")
        self.assertEqual(_kind(res), "typed-undone", logs)
        self.assertEqual(fake.box, "")

    def test_spinner_with_a_tip_row_above_a_wrapped_box_is_busy(self):
        text = partition_batch_text()
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, initial_box=text,
                                   wrap_width=176, visible_rows=3)
        lines = fake._render().split("\n")
        at = next(i for i, ln in enumerate(lines) if ln.startswith("─"))
        lines[at:at] = ["✳ Baking… (2m 30s · esc to interrupt)",
                        "  ⎿  Tip: press ctrl+b to run in the background"]
        self.assertTrue(send_outcome._pane_busy("\n".join(lines)))

    def test_the_one_undo_drops_the_watch_on_a_not_own_box(self):
        # the batch caller's `unconfirmed` path: the pre-marked watch must not
        # license a later own-prefix clear of the owner's appended words.
        own = partition_batch_text()
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True,
                                   initial_box=own + " a este toto som dopisal ja")
        state = {}
        wd._janitor_mark_watch(state, PID, NOW)
        cleared = goal._janitor_undo_if_own_stranded(
            PID, fake, own, "loc", _noop, [], state=state)
        self.assertFalse(cleared)
        self.assertNotIn(PID, state.get("janitor_watch", {}))


class HumanAppendIsNeverClearedLater(unittest.TestCase):
    """A human typing behind our stranded text makes the box not ours: the
    undo leaves it, arms NOTHING, and the next sweep's janitor leaves it too."""

    def test_append_is_left_now_and_on_the_next_sweep(self):
        text = partition_batch_text()
        fake = _HumanAppendFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                model_type=True, holds=1,
                                transcript_path=_tpath(self))
        logs, state = [], {}
        wd._janitor_mark_watch(state, PID, NOW)   # every production caller does
        res = wd.send_verified(PID, text, fake, fake.transcript_path,
                               sleep_fn=_noop, logs=logs, state=state,
                               nudge="partition-audit", now=NOW)
        self.assertEqual(_kind(res), "typed-stranded", logs)
        self.assertTrue(fake.box.endswith(_HumanAppendFake.APPEND))
        self.assertNotIn(PID, state.get("janitor_watch", {}), state)
        self.assertNotIn(PID, state.get("stranded_own", {}), state)
        before = fake.box
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc", _noop,
                                False, _noop, state=state, now=NOW + 60,
                                own_payload=None)
        self.assertEqual(fake.box, before)


class LongWrappedNudgeVerifies(unittest.TestCase):
    """(c): the partition-audit batch nudge in a pane whose input box shows
    only 3 of its wrapped rows (the scrolled render measured live)."""

    def test_scrolled_long_nudge_is_submitted(self):
        text = partition_batch_text()
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, transcript_path=_tpath(self),
                                   wrap_width=176, visible_rows=3)
        logs = []
        res = wd.send_verified(PID, text, fake, fake.transcript_path,
                               sleep_fn=_noop, logs=logs,
                               nudge="partition-audit")
        self.assertTrue(res, logs)
        turns = [json.loads(ln) for ln in
                 fake.transcript_path.read_text().splitlines()]
        self.assertEqual(turns[-1]["message"]["content"], text)
        self.assertEqual(fake.box, "")

    def test_head_swallow_on_a_scrolled_nudge_is_caught_and_retyped(self):
        # #670 stays: a first-byte swallow on the head chunk is caught by the
        # checkpoint (head-is-prefix on the still-unscrolled box) -> undone +
        # retyped; the submitted prompt is the exact text.
        from _goal_arm_helpers import _SwallowFirstCharFake
        text = partition_batch_text()
        fake = _SwallowFirstCharFake([(PID, "claude", CWD, "111")],
                                     GOAL_IDLE_CAP, model_type=True,
                                     transcript_path=_tpath(self),
                                     wrap_width=176, visible_rows=3,
                                     swallow_budget=1)
        res = wd.send_verified(PID, text, fake, fake.transcript_path,
                               sleep_fn=_noop, logs=[], nudge="partition-audit")
        self.assertTrue(res)
        self.assertEqual(fake.swallow_budget, 0, "the swallow really happened")
        turns = [json.loads(ln) for ln in
                 fake.transcript_path.read_text().splitlines()]
        self.assertEqual(turns[-1]["message"]["content"], text)

    def test_a_byte_lost_in_the_rows_a_scrolled_box_hides_is_caught(self):
        # #1157 review: the checkpoint proves the head, the final verify sees
        # only the last 3 rows. A chunk whose first byte is dropped lands in the
        # rows the box later hides; the per-chunk verify catches it while it is
        # still on screen, so the corrupted prompt is never submitted.
        text = partition_batch_text()
        fake = _DropRestFirstByteFake([(PID, "claude", CWD, "111")],
                                      GOAL_IDLE_CAP, model_type=True,
                                      transcript_path=_tpath(self),
                                      wrap_width=176, visible_rows=3)
        res = wd.send_verified(PID, text, fake, fake.transcript_path,
                               sleep_fn=_noop, logs=[], nudge="partition-audit")
        self.assertTrue(res)
        self.assertEqual(fake.dropped, 1)
        turns = [json.loads(ln) for ln in
                 fake.transcript_path.read_text().splitlines()]
        self.assertEqual(turns[-1]["message"]["content"], text)


class ScrolledProvenanceIsSendPathOnly(unittest.TestCase):
    """The whitespace-insensitive scrolled acceptance (`provenance`) belongs to
    the send path, whose every byte was proven per chunk. The goal arm and the
    stash route keep the strict #737 substring proof (a dropped space inside a
    visible row stays CORRUPT there)."""

    def _grid_cap(self, text, cols=40, rows=4):
        # a character-grid wrap (hard breaks mid-token) of the payload's tail
        grid = [text[i:i + cols] for i in range(0, len(text), cols)][-rows:]
        body = ["❯\xa0" + grid[0]] + ["  " + r for r in grid[1:]]
        return "\n".join(["● Hotovo.", "─" * 60] + body
                         + ["─" * 60, "  ctx ███░"]) + "\n"

    def test_default_is_strict_and_the_send_path_accepts(self):
        from watchdog import stash
        text = partition_batch_text()
        cap = self._grid_cap(text)
        self.assertEqual(stash._type_verify_class(
            PID, None, text, cap=cap, allow_scrolled=True), stash._TV_CORRUPT)
        self.assertEqual(stash._type_verify_class(
            PID, None, text, cap=cap, allow_scrolled=True, provenance=True),
            stash._TV_LANDED)


class NudgeAlphabetRoundTrips(unittest.TestCase):
    """The per-chunk verify compares rows exactly, so a nudge must not carry a
    code point a terminal cell does not round-trip (a variation selector or a
    zero-width joiner would read as a lost byte, a systematic CORRUPT)."""

    def test_partition_audit_nudge_with_every_clause(self):
        w = [{"number": n, "labels": ["ops-wait"]} for n in range(100, 112)]
        t = owr._nudge_text(7, w, discuss_audit=True, unpark_audit_n=3,
                            deploy_window=[1], deploy_miss=[2],
                            stagnation_count=9, release_landed=[5, 6, 7, 8])
        for bad in ("\ufe0f", "\u200d", "\u200b"):
            self.assertNotIn(bad, t)


class JanitorReclaimsARecordedStrandedPayload(unittest.TestCase):
    """A `typed-stranded` outcome records the exact text; the next sweep's
    janitor recognises that text in ANY pane render, scrolled included."""

    def _fake(self, box):
        return DeliverGoalFakeTmux([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                   model_type=True, initial_box=box,
                                   wrap_width=176, visible_rows=3)

    def test_scrolled_recorded_own_payload_is_cleared(self):
        text = partition_batch_text()
        fake = self._fake(text)
        state = {}
        wd._janitor_mark_watch(state, PID, NOW)
        send_outcome.record_stranded(state, PID, text, NOW)
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            jlogs = wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc",
                                        _noop, False, _noop, state=state,
                                        now=NOW + 60, own_payload=None)
        self.assertEqual(fake.box, "", jlogs)
        self.assertTrue(any("RECOVERED (janitor)" in ln for ln in jlogs), jlogs)
        self.assertNotIn(PID, state.get("stranded_own", {}), state)

    def test_record_is_dropped_once_the_text_is_gone(self):
        text = partition_batch_text()
        fake = self._fake("")               # the owner cleared it by hand
        state = {}
        send_outcome.record_stranded(state, PID, text, NOW)
        wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc", _noop,
                            False, _noop, state=state, now=NOW + 60,
                            own_payload=None)
        self.assertNotIn(PID, state.get("stranded_own", {}), state)
        self.assertEqual(fake.sent, [])

    def test_record_alone_clears_once_per_episode(self):
        # the 6 h watch has expired: the record alone licenses the clear, but at
        # most once per episode (a non-converging box is never re-Escaped).
        text = partition_batch_text()
        fake = self._fake(text)
        state = {}
        send_outcome.record_stranded(state, PID, text, NOW)
        from watchdog import janitor as _jan
        with m.patch.object(_jan, "_janitor_clear_box", return_value=False), \
                m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            first = wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc",
                                        _noop, False, _noop, state=state,
                                        now=NOW + 60, own_payload=None)
            second = wd._janitor_recover(fake, {}, PID, CWD, fake._render(),
                                         "loc", _noop, False, _noop, state=state,
                                         now=NOW + 120, own_payload=None)
        self.assertTrue(any("ESCALATED (janitor)" in ln for ln in first), first)
        self.assertTrue(any("clear-locked" in ln for ln in second), second)

    def test_an_occupied_stash_slot_is_never_popped_on_a_record(self):
        text = partition_batch_text()
        fake = _StashedFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                            model_type=True, initial_box=text,
                            wrap_width=176, visible_rows=3)
        state = {}
        send_outcome.record_stranded(state, PID, text, NOW)
        wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc", _noop,
                            False, _noop, state=state, now=NOW + 60,
                            own_payload=None)
        self.assertEqual(fake.sent, [])
        self.assertIn(PID, state["stranded_own"], "record kept for later")

    def test_record_lifecycle(self):
        text = partition_batch_text()
        busy = _SpinnerAfterTypeFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                     model_type=True, initial_box=text,
                                     wrap_width=176, visible_rows=3)
        busy._typed = True
        state = {}
        send_outcome.record_stranded(state, PID, text, NOW)
        # busy pane: cannot judge, record KEPT
        self.assertFalse(send_outcome.stranded_reclaimable(
            state, PID, busy._render(), NOW + 60))
        self.assertIn(PID, state["stranded_own"])
        # dry run never mutates, even when the text is gone
        bare = self._fake("")
        self.assertFalse(send_outcome.stranded_reclaimable(
            state, PID, bare._render(), NOW + 60, dry_run=True))
        self.assertIn(PID, state["stranded_own"])
        # a head-anchored PREFIX of the record is a partly-undone remnant: ours
        prefix = self._fake(text[:300])
        self.assertTrue(send_outcome.stranded_reclaimable(
            state, PID, prefix._render(), NOW + 60))
        # our text PLUS a human append is not ours: record AND watch dropped
        wd._janitor_mark_watch(state, PID, NOW)
        mixed = self._fake(text + " a este toto som dopisal ja")
        self.assertIs(send_outcome.stranded_reclaimable(
            state, PID, mixed._render(), NOW + 60), False)
        self.assertNotIn(PID, state["stranded_own"])
        self.assertNotIn(PID, state.get("janitor_watch", {}))
        # TTL: a day-old record is dropped unread
        send_outcome.record_stranded(state, PID, text, NOW)
        own = self._fake(text)
        self.assertFalse(send_outcome.stranded_reclaimable(
            state, PID, own._render(), NOW + send_outcome.STRANDED_TTL_S + 1))
        self.assertNotIn(PID, state["stranded_own"])
        # dead panes are pruned with the park records
        send_outcome.record_stranded(state, "%77", text, NOW)
        wd._janitor_prune_parks(state, ["%9"])
        self.assertNotIn("%77", state["stranded_own"])

    def test_a_recorded_pane_with_a_later_human_append_is_never_cleared(self):
        # typed-stranded (busy), the turn ended, the owner typed behind our text
        # before the next sweep: the record says the box is no longer ours, so
        # even the watched own-prefix generic clear must not run.
        text = partition_batch_text()
        fake = self._fake(text + " a este toto som dopisal ja")
        state = {}
        wd._janitor_mark_watch(state, PID, NOW)
        send_outcome.record_stranded(state, PID, text, NOW)
        before = fake.box
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc", _noop,
                                False, _noop, state=state, now=NOW + 60,
                                own_payload=None)
        self.assertEqual(fake.box, before)
        self.assertEqual(fake.sent, [])

    def test_a_busy_hold_never_burns_the_once_per_episode_lock(self):
        # a provenance-free template leftover on a BUSY pane: held with no key,
        # and the one-per-episode clear slot is NOT consumed by the hold.
        import goal_registry
        tmpl = list(goal_registry.all_goal_line_variants())[0]
        busy = _SpinnerAfterTypeFake([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                                     model_type=True, initial_box=tmpl,
                                     wrap_width=176, visible_rows=3)
        busy._typed = True
        state = {}
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            jlogs = wd._janitor_recover(busy, {}, PID, CWD, busy._render(), "loc",
                                        _noop, False, _noop, state=state,
                                        now=NOW + 60, own_payload=None)
        self.assertTrue(any("hold:busy" in ln for ln in jlogs), jlogs)
        self.assertEqual(busy.sent, [])
        self.assertNotIn(PID, state.get("goal_template_clear", {}), state)

    def test_foreign_draft_with_a_record_is_left_untouched(self):
        text = partition_batch_text()
        fake = self._fake("moja vlastna dlha poznamka k tiketu ktoru som napisal "
                          "sam a nema nic spolocne s watchdog nudge textom")
        state = {}
        wd._janitor_mark_watch(state, PID, NOW)
        send_outcome.record_stranded(state, PID, text, NOW)
        before = fake.box
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc", _noop,
                                False, _noop, state=state, now=NOW + 60,
                                own_payload=None)
        self.assertEqual(fake.box, before)
        self.assertNotIn("BSpace", fake.keys())


class _GkreqPaneFake:
    """One idle claude pane at `root` with a modelled input box (the
    `_CrossStreamFakeTmux` shape plus typing): `-l` appends, `BSpace` trims, and
    the first capture after the type reads UNREADABLE (a one-frame blip)."""

    IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"

    def __init__(self, root):
        self.root, self.box, self.sent, self.blip = root, "", [], 1

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "%1\tclaude\t" + self.root
        if "display" in j:
            return "0"
        if "send-keys" in j:
            self.sent.append(argv)
            if "-l" in argv:
                self.box += argv[-1]
            elif argv[-1] == "BSpace":
                self.box = self.box[:-sum(1 for k in argv if k == "BSpace")]
            return ""
        if "capture-pane" in j:
            if self.box and self.blip:
                self.blip -= 1
                return UNREADABLE
            shown = ("❯\xa0" + self.box) if self.box else "❯ "
            return self.IDLE.replace("❯ ", shown)
        return ""


class TruthfulCallerVerbs(unittest.TestCase):
    """(b): the batch-nudge and gkreq journal verbs name the real outcome."""

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.projp = Path(self._proj.name)
        self.tpath = _write_marker_transcript(self.projp, CWD, "sess-1157")
        self.sid = self.tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(self.tpath, (old, old))

    def _batch_sweep(self, fake, state):
        pth = ss.status_path(self.sid)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps(
            {"schema": 1, "sid": self.sid, "kind": "main", "last_turn": "stop",
             "ts": NOW, "cwd": CWD, "marker": "working", "goal_armed": True}),
            encoding="utf-8")
        state.setdefault("goal_mark", {})[self.sid] = {
            "off": 0, "mark": {"state": "set", "ts": NOW}}
        rec = {"id": 6883, "kind": "ticket", "num": 6883,
               "permalink": "https://x/6883", "tag": "infra"}
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("cli_concurrency.resolve_mode", return_value="sequential"), \
                m.patch.object(wd, "_owner_disabled", return_value=False), \
                m.patch.object(wd, "nudges_enabled",
                               lambda kind=None: kind == "queue-arrival"):
            return goal.goal_lane_sweep(
                NOW, run=fake, projects_dir=self.projp, state=state,
                dry_run=False, handled=set(), backlog_fetch=lambda cwd: 0,
                queue_fetch=None,
                infra_queue_fetch=lambda cwd: [
                    {"id": 1, "kind": "ticket", "num": 1,
                     "permalink": "https://x/1", "tag": "infra"}, rec],
                resolve_role_fn=lambda cwd: "infra", sleep_fn=_noop)

    def test_batch_verb_says_typed_then_undone_not_not_typed(self):
        fake = _HoldAfterTypeFake([(PID, "claude", CWD, "111")], GOAL_ARMED_CAP,
                                  model_type=True, holds=1,
                                  transcript_path=self.tpath)
        state = {"queue_arrival": {self.sid: {"base": [1],
                                              "first_seen": NOW - 86400}}}
        logs = self._batch_sweep(fake, state)
        lines = [ln for ln in logs if ln.startswith("batch-nudge")]
        self.assertTrue(lines, logs)
        self.assertFalse(any("not typed" in ln for ln in lines), lines)
        self.assertTrue(any("typed-undone" in ln for ln in lines), lines)
        self.assertEqual(fake.box, "")
        # a typed attempt stamps the per-kind floor (never re-typed next sweep)
        self.assertEqual(state["nudge_cadence"][self.sid]["queue-arrival"], NOW,
                         state.get("nudge_cadence"))

    def test_not_typed_never_counts_toward_the_give_up_ping(self):
        from watchdog import cross_stream
        store, logs, pings = {}, [], []
        for _ in range(5):
            cross_stream._handle_unverified_nudge(
                store, "odoo-erp", "#7887", "gkreq",
                lambda *a, **k: pings.append(a) or "sent", lambda: None, False,
                NOW, logs, "give up", outcome=send_outcome.OUT_NOT_TYPED)
        self.assertEqual(store.get("vfail", {}), {}, store)
        self.assertEqual(pings, [])
        self.assertTrue(all("not-typed, streak unchanged" in ln for ln in logs), logs)

    def test_gkreq_verb_names_the_outcome(self):
        import time as _t
        from test_send_verified_adoption import _write_transcript
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = str(Path(tmp.name) / "devel" / "odoo-erp")
        Path(root).mkdir(parents=True)
        projects = Path(tmp.name) / "projects"
        _write_transcript(projects, root)
        fake = _GkreqPaneFake(root)
        with m.patch.object(wd, "_gkreq_supervisor_root", lambda cwd: cwd):
            logs = wd.gk_request_backstop(
                _t.time(), fake, {}, lambda *a, **k: "sent", home=tmp.name,
                gh_fetch=lambda r: [7887], projects_dir=projects, sleep_fn=_noop)
        failed = [ln for ln in logs if "gkreq-nudge-failed" in ln]
        self.assertTrue(failed, logs)
        self.assertTrue(all("typed-undone" in ln for ln in failed), failed)
        self.assertEqual(fake.box, "", logs)

if __name__ == "__main__":
    unittest.main()
