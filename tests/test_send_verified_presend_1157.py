"""#1157 slice 2 — which non-bare boxes the pre-send gate may clear (fakes).

`send_verified`'s pre-send gate used to abort on ANY non-bare box, so our own
leftover from before slice 1 (no `stranded_own` record, no janitor watch)
blocked every delivery forever. It now clears a box that PROVABLY holds only
our own machine text, on an idle pane with a readable box, and types nothing in
that call. These tests pin the proof from the owner-draft side: every shape
that is not provably ours is left untouched, with no keystroke at all.
The real-tmux render is covered by tests/test_send_verified_presend_tmux_1157.py.

Fakes only (no tmux, no gh, no Discord): the stateful `DeliverGoalFakeTmux`.
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402,F401
import watchdog as wd  # noqa: E402
from watchdog import send_outcome  # noqa: E402

from _goal_arm_helpers import DeliverGoalFakeTmux, GOAL_IDLE_CAP  # noqa: E402
from test_send_verified_undo_1157 import partition_batch_text  # noqa: E402

PID = "%9"
NOW = 1_000_000
CWD = "/home/gatekeeper/devel/odoo/odoo-erp-infra"
PLACEHOLDER = "[Pasted text #1 +42 lines]"
APPEND = " a este toto som dopisal ja"
NEXT = "nudge: [u-freshness] u-freshness: skontroluj U a zavri co je vybavene."


def _noop(*_a, **_k):
    return None


class _StashedFake(DeliverGoalFakeTmux):
    """The owner's own draft sits in the single stash slot (the marker shows)."""

    def _render(self):
        return super()._render() + "  › stashed\n"


class _AppendOnSecondCaptureFake(DeliverGoalFakeTmux):
    """The owner types behind the box between the gate's read and the clear."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._caps = 0

    def __call__(self, argv, timeout=8):
        if "capture-pane" in " ".join(argv):
            self._caps += 1
            if self._caps == 2:
                self.box += APPEND
        return super().__call__(argv, timeout)


class _AppendDuringClearFake(DeliverGoalFakeTmux):
    """The owner types behind the box right after the first backspace batch."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._bs = 0

    def __call__(self, argv, timeout=8):
        out = super().__call__(argv, timeout)
        if "send-keys" in argv and "BSpace" in argv:
            self._bs += 1
            if self._bs == 1:
                self.box += APPEND
        return out


class _StripSelectedFake(DeliverGoalFakeTmux):
    """The agent strip holds focus (`❯ ● main`): keys would go to the strip."""

    def _render(self):
        return super()._render() + "  ❯ ● main\n"


class _ChangesAfterGateFake(DeliverGoalFakeTmux):
    """From the SECOND capture on (after the gate's read) a spinner renders
    above the box (a turn started) or the stash marker shows."""

    def __init__(self, *a, extra="", **kw):
        super().__init__(*a, **kw)
        self._caps, self.extra = 0, extra

    def __call__(self, argv, timeout=8):
        if "capture-pane" in " ".join(argv):
            self._caps += 1
        return super().__call__(argv, timeout)

    def _render(self):
        base = super()._render()
        if self._caps < 2:
            return base
        if self.extra == "stash":
            return base + "  › stashed\n"
        return base.replace("● Hotovo.", "✳ Baking… (2m 30s · esc to interrupt)")


class _StuckBackspaceFake(DeliverGoalFakeTmux):
    """BSpace never lands: the clear cannot converge."""

    def __call__(self, argv, timeout=8):
        if "send-keys" in argv and "BSpace" in argv:
            self.sent.append(argv)
            return ""
        return super().__call__(argv, timeout)


class PreSendGate(unittest.TestCase):

    def _fake(self, box, cls=DeliverGoalFakeTmux):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        tp = Path(d.name) / "sess.jsonl"
        tp.write_text(json.dumps({"type": "assistant",
                                  "message": {"content": "x"}}) + "\n")
        return cls([(PID, "claude", CWD, "111")], GOAL_IDLE_CAP,
                   model_type=True, initial_box=box, wrap_width=60,
                   transcript_path=tp)

    def _send(self, fake, state, text=NEXT):
        logs = []
        res = wd.send_verified(PID, text, fake, fake.transcript_path,
                               sleep_fn=_noop, logs=logs, state=state,
                               nudge="u-freshness", now=NOW)
        return res, logs

    def _assert_held(self, fake, box, res, logs, reason):
        self.assertFalse(res, logs)
        self.assertEqual(getattr(res, "kind", None), "not-typed", logs)
        self.assertEqual(fake.keys(), [], "no keystroke: %r" % logs)
        self.assertEqual(fake.box, box, logs)
        self.assertTrue(any(reason in ln for ln in logs), logs)

    def test_legacy_own_batch_leftover_is_cleared_without_a_record(self):
        box = partition_batch_text()
        fake = self._fake(box)
        res, logs = self._send(fake, {})
        self.assertEqual(getattr(res, "kind", None), "not-typed", logs)
        self.assertEqual(fake.box, "", logs)
        self.assertTrue(any("pre-send: cleared stale own machine text (%d chars)"
                            % len(box) in ln for ln in logs), logs)
        self.assertEqual(fake.typed_texts(), [], "never typed in the same call")
        res2, logs2 = self._send(fake, {})
        self.assertTrue(res2, logs2)

    def test_owner_goal_draft_is_held(self):
        # `/goal ` is an own payload prefix the OWNER also types (the manual
        # arming flow): the prefix alone never licenses a clear here
        box = "/goal all tickets closed AND CI green -- or stop after 30 turns"
        fake = self._fake(box)
        res, logs = self._send(fake, {})
        self._assert_held(fake, box, res, logs,
                          "box holds a non-machine draft — held")

    def test_unknown_batch_category_is_held(self):
        box = "nudge: [moja-poznamka] toto som si sem napisal ja"
        fake = self._fake(box)
        res, logs = self._send(fake, {})
        self._assert_held(fake, box, res, logs,
                          "box holds a non-machine draft — held")

    def test_placeholder_without_our_watch_is_held(self):
        # a human's long paste collapses into the IDENTICAL placeholder
        fake = self._fake(PLACEHOLDER)
        res, logs = self._send(fake, {})
        self._assert_held(fake, PLACEHOLDER, res, logs,
                          "box holds a non-machine draft — held")

    def test_placeholder_is_held_even_under_our_own_watch(self):
        # every delivering caller stamps this pane's janitor watch right before
        # the send, so the watch proves nothing about a human paste's placeholder
        fake = self._fake(PLACEHOLDER)
        state = {}
        wd._janitor_mark_watch(state, PID, NOW)
        res, logs = self._send(fake, state)
        self._assert_held(fake, PLACEHOLDER, res, logs,
                          "box holds a non-machine draft — held")

    def test_stateless_caller_never_clears(self):
        # the owner's own Discord reply threads no state: no record, no not-own
        # mark to consult, so nothing may be cleared on shape alone
        box = partition_batch_text() + APPEND
        fake = self._fake(box)
        res, logs = self._send(fake, None)
        self._assert_held(fake, box, res, logs, "no state threaded — held")

    def test_short_draft_that_prefixes_the_record_is_held(self):
        # the owner cleared our stranded text and started typing: `nud` is a
        # prefix of our record, but far too short to be told from a draft
        text = partition_batch_text()
        state = {"stranded_own": {PID: {"ts": NOW - 60, "typed": text}}}
        fake = self._fake("nud")
        res, logs = self._send(fake, state)
        self._assert_held(fake, "nud", res, logs,
                          "box holds a non-machine draft — held")
        self.assertIn(PID, state["stranded_own"], "the record is kept")

    def test_long_remnant_of_the_record_is_cleared(self):
        text = partition_batch_text()
        state = {"stranded_own": {PID: {"ts": NOW - 60, "typed": text}}}
        fake = self._fake(text[:300])
        self._send(fake, state)
        self.assertEqual(fake.box, "")

    def test_owner_text_scrolled_above_our_nudge_is_never_deleted(self):
        # the box scrolled: its visible head row starts with our batch head, but
        # the owner's own line sits above it, off-screen. The clear must stop at
        # the proven visible text and never reach the hidden line.
        note = "toto je moja poznamka k tej sprave nizsie, neodstranovat"
        fake = self._fake(note + " " + partition_batch_text())
        rows = fake._render_wrapped().splitlines()[3:-2]
        self.assertTrue(rows[1].lstrip().startswith("nudge: [partition-audit] "),
                        rows[:2])
        fake.visible_rows = len(rows) - 1
        self.assertTrue(wd._input_box_head_text(fake._render()).startswith(
            "nudge: [partition-audit] "))
        state = {}
        res, logs = self._send(fake, state)
        self.assertFalse(res, logs)
        self.assertTrue(fake.box.startswith(note), fake.box)
        self.assertTrue(any("stopped clearing" in ln and "changed" in ln
                            for ln in logs), logs)
        self.assertIn(PID, state.get(send_outcome.NOT_OWN_KEY, {}))
        self._send(fake, state)                       # the next sweep holds too
        self.assertTrue(fake.box.startswith(note), fake.box)

    def test_words_typed_during_the_clear_stop_it(self):
        box = partition_batch_text()
        fake = self._fake(box, cls=_AppendDuringClearFake)
        state = {}
        res, logs = self._send(fake, state)
        self.assertFalse(res, logs)
        self.assertTrue(fake.box.endswith(APPEND), fake.box)
        self.assertIn(PID, state.get(send_outcome.NOT_OWN_KEY, {}))

    def test_selected_agent_strip_is_held(self):
        box = partition_batch_text()
        fake = self._fake(box, cls=_StripSelectedFake)
        res, logs = self._send(fake, {})
        self._assert_held(fake, box, res, logs, "agent strip selected — held")

    def test_turn_starting_after_the_gate_read_is_never_keyed(self):
        box = partition_batch_text()
        fake = self._fake(box, cls=_ChangesAfterGateFake)
        res, logs = self._send(fake, {})
        self._assert_held(fake, box, res, logs, "stopped clearing")
        self.assertTrue(any("(busy, 0 chars removed)" in ln for ln in logs), logs)

    def test_stash_marker_after_the_gate_read_is_never_keyed(self):
        box = partition_batch_text()
        fake = self._fake(box, cls=_ChangesAfterGateFake)
        fake.extra = "stash"
        res, logs = self._send(fake, {})
        self.assertEqual(fake.keys(), [], logs)
        self.assertTrue(any("(stash-occupied, 0 chars removed)" in ln
                            for ln in logs), logs)

    def test_a_clear_that_does_not_converge_is_not_logged_cleared(self):
        box = partition_batch_text()
        fake = self._fake(box, cls=_StuckBackspaceFake)
        res, logs = self._send(fake, {})
        self.assertFalse(res, logs)
        self.assertFalse(any("cleared stale own machine text" in ln
                             for ln in logs), logs)
        self.assertTrue(any("stopped clearing" in ln and "not-converged" in ln
                            for ln in logs), logs)

    def test_batch_head_row_ending_at_the_bracket_is_ours(self):
        # a narrow pane wraps right after `[partition-audit]`
        box = partition_batch_text()
        fake = self._fake(box)
        fake.wrap_width = 30
        self.assertEqual(wd._input_box_head_text(fake._render()),
                         "nudge: [partition-audit]")
        self._send(fake, {})
        self.assertEqual(fake.box, "")

    def test_janitor_honours_the_not_own_mark_despite_a_fresh_watch(self):
        # every delivering caller re-stamps the watch right before its send; a
        # not-own verdict must still keep the janitor's prefix clear away
        box = partition_batch_text() + APPEND
        fake = self._fake(box)
        state = {send_outcome.NOT_OWN_KEY: {PID: NOW - 60}}
        wd._janitor_mark_watch(state, PID, NOW)
        wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc", None,
                            False, _noop, state=state, now=NOW)
        self.assertEqual(fake.keys(), [])
        self.assertEqual(fake.box, box)
        # control: without the mark the same janitor call clears it
        state[send_outcome.NOT_OWN_KEY] = {}
        wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc", None,
                            False, _noop, state=state, now=NOW)
        self.assertEqual(fake.box, "")

    def test_janitor_seeing_a_bare_box_drops_the_mark(self):
        fake = self._fake("")
        state = {send_outcome.NOT_OWN_KEY: {PID: NOW - 60}}
        wd._janitor_recover(fake, {}, PID, CWD, fake._render(), "loc", None,
                            False, _noop, state=state, now=NOW)
        self.assertNotIn(PID, state[send_outcome.NOT_OWN_KEY])

    def test_scrolled_recorded_leftover_is_cleared_whole(self):
        # slice 1 recorded this stranded text; the box is SCROLLED, so as it
        # shrinks the window shifts to our own earlier rows. The record proves
        # the whole box: it is cleared like the janitor would, never marked
        # not-own and stranded half-way.
        text = partition_batch_text()
        state = {"stranded_own": {PID: {"ts": NOW - 60, "typed": text}}}
        fake = self._fake(text)
        fake.visible_rows = 3
        res, logs = self._send(fake, state)
        self.assertEqual(fake.box, "", logs)
        self.assertNotIn(PID, state.get(send_outcome.NOT_OWN_KEY, {}))
        self.assertNotIn(PID, state.get("stranded_own", {}))

    def test_one_clear_attempt_per_pane_per_episode(self):
        box = partition_batch_text()
        fake = self._fake(box, cls=_StuckBackspaceFake)
        state = {}
        self._send(fake, state)
        sent = len(fake.sent)
        res, logs = self._send(fake, state)
        self.assertEqual(len(fake.sent), sent, "no second clear this episode")
        self.assertTrue(any("already tried this episode" in ln for ln in logs),
                        logs)

    def test_a_box_longer_than_any_batch_is_held(self):
        box = "nudge: [partition-audit] " + "slovo " * 300
        fake = self._fake(box)
        res, logs = self._send(fake, {})
        self._assert_held(fake, box, res, logs,
                          "box holds a non-machine draft — held")

    def test_a_bare_box_leaves_no_not_own_mark(self):
        state = {"stranded_own": {PID: {"ts": NOW - 60, "typed": "abc"}},
                 send_outcome.NOT_OWN_KEY: {PID: NOW - 120}}
        fake = self._fake("")
        self.assertFalse(send_outcome.stranded_reclaimable(
            state, PID, fake._render(), NOW))
        self.assertNotIn(PID, state[send_outcome.NOT_OWN_KEY])
        self.assertNotIn(PID, state["stranded_own"])

    def test_recorded_stranded_text_is_cleared_and_the_record_dropped(self):
        text = partition_batch_text()
        state = {"stranded_own": {PID: {"ts": NOW - 60, "typed": text}}}
        fake = self._fake(text)
        self._send(fake, state)
        self.assertEqual(fake.box, "")
        self.assertNotIn(PID, state.get("stranded_own", {}))

    def test_recorded_text_with_a_human_append_is_held(self):
        # the record decides alone: our recorded text plus the owner's words is
        # NOT ours, even though its head still reads `nudge: [partition-audit]`
        text = partition_batch_text()
        state = {"stranded_own": {PID: {"ts": NOW - 60, "typed": text}},
                 "janitor_watch": {PID: NOW - 60}}
        fake = self._fake(text + APPEND)
        res, logs = self._send(fake, state)
        self._assert_held(fake, text + APPEND, res, logs,
                          "box holds a non-machine draft — held")
        self.assertNotIn(PID, state.get("janitor_watch", {}), state)
        # and the NEXT sweep (record gone) still never clears it
        res, logs = self._send(fake, state)
        self._assert_held(fake, text + APPEND, res, logs,
                          "box holds a non-machine draft — held")

    def test_a_slice1_not_own_verdict_blocks_the_prefix_clear(self):
        # slice 1's undo judged the box "not ours" (our text + the owner's
        # words): no record is written, the watch is dropped, and the pre-send
        # prefix check must not eat the owner's words on a later sweep
        text = partition_batch_text()
        state = {"janitor_watch": {PID: NOW - 60}}
        fake = self._fake(text + APPEND)
        jl, out = [], {}
        send_outcome.janitor_undo_if_own_stranded(PID, fake, text, "loc",
                                                  _noop, jl, out=out,
                                                  state=state)
        self.assertEqual(out["box"], "not-own", jl)
        res, logs = self._send(fake, state)
        self._assert_held(fake, text + APPEND, res, logs,
                          "box holds a non-machine draft — held")
        # once the owner empties the box, the mark is gone and delivery works
        fake.box = ""
        res, logs = self._send(fake, state)
        self.assertTrue(res, logs)
        self.assertNotIn(PID, state.get(send_outcome.NOT_OWN_KEY, {}), state)

    def test_a_dead_panes_not_own_mark_is_pruned(self):
        state = {send_outcome.NOT_OWN_KEY: {PID: NOW, "%1": NOW}}
        wd._janitor_prune_parks(state, ["%1"])
        self.assertEqual(state[send_outcome.NOT_OWN_KEY], {"%1": NOW})

    def test_occupied_stash_slot_is_held(self):
        box = partition_batch_text()
        fake = self._fake(box, cls=_StashedFake)
        res, logs = self._send(fake, {})
        self._assert_held(fake, box, res, logs, "stash slot occupied")

    def test_box_changed_before_the_clear_is_held(self):
        box = partition_batch_text()
        fake = self._fake(box, cls=_AppendOnSecondCaptureFake)
        res, logs = self._send(fake, {})
        self._assert_held(fake, box + APPEND, res, logs, "box changed")


if __name__ == "__main__":
    unittest.main()
