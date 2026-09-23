"""#1113 RECURRENCE (23.9.2026) — a watchdog `dark-rearm` typed a `/goal` into an
ACTIVE, armed david1 session, the submit failed, and the janitor refused to clear
its own leftover.

Owner-escalated regression (reported >10x). Live evidence (david1 goal-sync.log,
2026-09-23T02:17:36Z): `SKIP verify-failed` -> `ARM-CONFIRM-FAIL ... armed=False
box=other` -> `cleanup=declined (box not our own leftover)`; the typed origin was
`dark-rearm`. At the SAME instant the watchdog's STRUCTURED state said the session
WAS armed (`goal_mark[sid].mark.state == "set"`, one-glance `armed=yes
src=goal_mark`). The pane render read dark (the `◎ /goal` glyph scrolled off behind
the stranded draft) while the structured truth was armed.

The fix (design-by main, Approach 1 (a)+(b)+(c), issue #1113 comment 5790100100):
  (a) `deliver_goal` refuses EVERY watchdog-originated re-arm origin with ZERO
      keystrokes (`refuse:structured-armed`) while the transcript-derived
      `goal_mark` state for the sid is "set" — the pane render can NEVER authorise
      typing over the structured truth. A virgin arm proceeds only when NO
      goal_mark record exists.
  (b) `_box_is_own_leftover` recognises the box as OUR leftover when it is a
      verbatim run of ANY rendered `/goal` template variant (goal_registry), NOT
      only the single request payload, and WITHOUT provenance — verbatim template
      text is un-forgeable by a human draft.
  (c) A provenance-free template clear runs at MOST ONCE per episode (a lock), and
      the pane-busy-draft skip is kept (never re-typed over).
"""

import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd
from watchdog import goal, stash, janitor
import goal_registry

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_marker_transcript,
)

from test_never_type_into_armed_1113 import (  # noqa: E402
    _grid_scroll_capture,
    _GridScrollFake,
)

# The real shipped fork-no-merge /goal line (the david1 payload) and a DIFFERENT
# variant (full parallel) — the recurrence is that the box held one variant while
# the janitor was handed another.
_FORK = goal_registry.render_goal_line("fork-no-merge", "parallel", None)
_FULL = goal_registry.render_goal_line("full", "parallel", None)

PANE = [("%9", "claude", "/home/newlevel/devel/rec1113", "111")]
CWD = "/home/newlevel/devel/rec1113"
PID = "%9"


class _StickyGridFake(_GridScrollFake):
    """A grid-scrolled fake whose box NEVER shrinks on BSpace — the clear never
    converges, so a re-attempt every sweep would keep Escaping the pane. Used to
    prove the (c) once-per-episode lock stops the second attempt."""

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if self.model_type and "send-keys" in j:
            self.sent.append(argv)
            return ""            # record keystrokes, never mutate the box
        return super().__call__(argv, timeout)


# --------------------------------------------------------------------------- #
# (a) deliver_goal refuses EVERY watchdog re-arm origin when goal_mark is "set",
#     even if the PANE render reads dark; a virgin arm (no goal_mark) proceeds.
# --------------------------------------------------------------------------- #
class TestStructuredArmedRefusal(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        # Isolate STATE_PATH so `_structured_goal_mark_state`'s persisted-goal_mark
        # fallback reads an EMPTY fixture file, never the box's real
        # ~/.claude/api-watchdog-state.json (the #732/#548 hermeticity discipline).
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        sp = Path(d.name) / "api-watchdog-state.json"
        sp.write_text("{}", encoding="utf-8")
        p = m.patch.object(wd, "STATE_PATH", sp)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _deliver(self, sid, origin, goal_mark_state="set"):
        proj = self._dir()
        _write_marker_transcript(proj, CWD, sid)
        # The PANE renders DARK (no `◎ /goal`) — the exact 02:17 mis-read.
        tmux = DeliverGoalFakeTmux(PANE, GOAL_IDLE_CAP, model_type=True)
        state = {}
        if goal_mark_state is not None:
            state["goal_mark"] = {sid: {"mark": {"state": goal_mark_state}}}
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            word = goal.deliver_goal(
                sid, CWD, _FORK, "fork-no-merge", run=tmux,
                projects_dir=proj, now=100000, request_ts=100000,
                state=state, sleep_fn=lambda s: None, origin=origin)
        return word, tmux, Path(self.syncp).read_text(encoding="utf-8")

    def test_every_watchdog_origin_refused_when_goal_mark_set(self):
        for origin in goal._GOAL_WATCHDOG_REARM_ORIGINS:
            with self.subTest(origin=origin):
                word, tmux, log = self._deliver("rec-a-%s" % origin, origin)
                self.assertEqual(
                    word, "drop:already-armed",
                    "origin %s must be refused at a structured-armed goal_mark"
                    % origin)
                self.assertEqual(
                    tmux.sent, [],
                    "ZERO keystrokes into a structured-armed loop (origin %s)"
                    % origin)
                self.assertIn(
                    "refuse:structured-armed", log,
                    "the refusal writes the refuse:structured-armed line "
                    "(origin %s)" % origin)

    def test_pane_render_dark_never_overrides_structured_set(self):
        # The whole recurrence: the PANE reads dark (GOAL_IDLE_CAP has no
        # `◎ /goal`) yet goal_mark is set -> refuse, never type.
        self.assertIs(wd.pane_goal_armed(GOAL_IDLE_CAP), False,
                      "the fixture pane render is dark (the 02:17 mis-read)")
        word, tmux, _log = self._deliver("rec-a-darkpane", "dark-rearm")
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(tmux.sent, [])

    def test_virgin_arm_proceeds_when_no_goal_mark_record(self):
        # A declared-virgin arm legitimately needs NO prior goal: with NO
        # goal_mark record the structured gate does NOT refuse -> the delivery
        # proceeds to a keystroke (arm the fresh window).
        word, tmux, log = self._deliver(
            "rec-a-virgin", "declared-virgin", goal_mark_state=None)
        self.assertNotIn("refuse:structured-armed", log,
                         "no goal_mark record -> the structured gate is silent")
        self.assertTrue(tmux.sent,
                        "a virgin arm proceeds past the structured gate")

    def test_cleared_goal_mark_does_not_refuse(self):
        # A "cleared" mark (e.g. CC's transient-auth clear) is NOT armed, so the
        # structured gate must not refuse (auth-rearm etc. legitimately re-arm a
        # cleared loop).
        word, tmux, log = self._deliver(
            "rec-a-cleared", "dark-rearm", goal_mark_state="cleared")
        self.assertNotIn("refuse:structured-armed", log)


# --------------------------------------------------------------------------- #
# (b) the janitor recognises our OWN leftover against ANY template variant,
#     without provenance (the expired-mark / different-variant recurrence).
# --------------------------------------------------------------------------- #
class TestTemplateVariantRecognition(unittest.TestCase):
    def test_registry_lists_every_variant(self):
        variants = goal_registry.all_goal_line_variants()
        self.assertIn(_FORK, variants)
        self.assertIn(_FULL, variants)
        self.assertTrue(all(v.startswith("/goal ") for v in variants))

    def test_every_locked_variant_is_recognised(self):
        # Completeness lock (review 🟡): EVERY (authority, mode, role) the
        # `goal-inventory --check` set locks — `variant_specs()` PLUS the
        # gk-full-only review variant — must be in `all_goal_line_variants()`.
        # A shipped/armable variant dropped from this set silently reintroduces
        # the exact recurrence for that variant (the janitor reverts to
        # `cleanup=declined` on its own leftover). No dupes; all `/goal `-lines.
        variants = goal_registry.all_goal_line_variants()
        specs = list(goal_registry.variant_specs()) + [("full", "parallel",
                                                        "review")]
        for authority, mode, role in specs:
            line = goal_registry.render_goal_line(authority, mode, role)
            self.assertIn(line, variants,
                          "variant %s/%s/%s missing from all_goal_line_variants"
                          % (authority, mode, role))
        self.assertEqual(len(variants), len(set(variants)), "no duplicates")

    def test_grid_box_matches_a_template_variant_without_provenance(self):
        # The exact david1 render: a grid-wrapped, scrolled fork-no-merge tail.
        cap = _grid_scroll_capture(_FORK)
        # NOT a clean substring, NO provenance, NO match_templates -> not proven.
        self.assertFalse(stash._box_is_own_leftover(
            cap, _FORK, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR))
        # WITH match_templates -> the verbatim template proves it, no provenance.
        self.assertTrue(stash._box_is_own_leftover(
            cap, None, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR, match_templates=True))

    def test_matches_even_when_payload_is_a_different_variant(self):
        # The recurrence: the box holds fork-no-merge, the request payload handed
        # in is a DIFFERENT variant (full) -> the payload proof misses but the
        # template-variant proof recognises it.
        cap = _grid_scroll_capture(_FORK)
        self.assertTrue(stash._box_is_own_leftover(
            cap, _FULL, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR,
            provenance=False, match_templates=True))

    def test_foreign_draft_never_matches_a_template(self):
        foreign = ("this is a completely unrelated human draft that is more than "
                   "long enough to clear the minimum substring floor but shares "
                   "no contiguous run with any shipped goal template variant text "
                   "anywhere at all so it can never false-positive as ours okay.")
        cap = _grid_scroll_capture(foreign)
        self.assertFalse(stash._box_is_own_leftover(
            cap, None, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR, match_templates=True))


# --------------------------------------------------------------------------- #
# (b) the janitor CLEARS a template box whose provenance mark has EXPIRED.
# --------------------------------------------------------------------------- #
class TestJanitorClearsExpiredProvenanceTemplate(unittest.TestCase):
    def test_janitor_clears_template_box_without_a_watch_mark(self):
        # The 6h-stranded recurrence: `_janitor_recover` runs on a LATER sweep
        # after the janitor_watch mark expired (state has NO janitor_watch), so
        # the pre-fix provenance gate `return logs` (declined). The verbatim
        # template proof now licenses the clear.
        fake = _GridScrollFake(PANE, "", model_type=True)
        fake.box = _FORK
        captured = fake(["tmux", "capture-pane", "-p", "-t", PID])
        state = {}          # NO janitor_watch -> provenance expired
        logs = janitor._janitor_recover(
            fake, {}, PID, CWD, captured, "sess:0.0",
            send_fn=lambda *a, **k: None, dry_run=False,
            sleep_fn=lambda s: None, state=state, now=100000.0,
            own_payload=_FORK)
        self.assertTrue(any("draft-rescue" in ln for ln in logs),
                        "the janitor DECIDED to clear its own template leftover "
                        "even with an expired mark: %r" % logs)
        self.assertIn("Escape", [a[-1] for a in fake.sent],
                      "the janitor takes the clear path")
        self.assertNotIn("Enter", [a[-1] for a in fake.sent],
                         "NEVER submit a stranded payload")


# --------------------------------------------------------------------------- #
# (c) a provenance-free template clear runs at most ONCE per episode.
# --------------------------------------------------------------------------- #
class TestTemplateClearOncePerEpisode(unittest.TestCase):
    def test_second_sweep_does_not_reattempt_a_non_converging_clear(self):
        state = {}          # no provenance -> template-only clear path
        # First sweep: the (non-converging) box is our template -> clear attempted.
        fake1 = _StickyGridFake(PANE, "", model_type=True)
        fake1.box = _FORK
        cap1 = _grid_scroll_capture(_FORK)
        logs1 = janitor._janitor_recover(
            fake1, {}, PID, CWD, cap1, "sess:0.0",
            send_fn=lambda *a, **k: None, dry_run=False,
            sleep_fn=lambda s: None, state=state, now=100000.0,
            own_payload=_FORK)
        self.assertTrue(fake1.sent,
                        "the first template clear is attempted: %r" % logs1)
        # Second sweep, same episode, box still stranded (non-converged): the lock
        # must skip -> ZERO keystrokes, a template-clear-locked decision line.
        fake2 = _StickyGridFake(PANE, "", model_type=True)
        fake2.box = _FORK
        cap2 = _grid_scroll_capture(_FORK)
        logs2 = janitor._janitor_recover(
            fake2, {}, PID, CWD, cap2, "sess:0.0",
            send_fn=lambda *a, **k: None, dry_run=False,
            sleep_fn=lambda s: None, state=state, now=100000.0 + 60,
            own_payload=_FORK)
        self.assertEqual(fake2.sent, [],
                         "the second sweep does NOT re-attempt the clear: %r"
                         % logs2)
        self.assertTrue(any("template-clear-locked" in ln for ln in logs2),
                        "the once-per-episode lock is logged: %r" % logs2)

    def test_provenance_backed_clear_is_not_locked(self):
        # With a fresh janitor_watch (real provenance), the clear is NOT gated by
        # the template-clear lock — the lock only guards the provenance-FREE path.
        state = {"janitor_watch": {PID: 100000.0}}
        fake = _StickyGridFake(PANE, "", model_type=True)
        fake.box = _FORK
        cap = _grid_scroll_capture(_FORK)
        logs = janitor._janitor_recover(
            fake, {}, PID, CWD, cap, "sess:0.0",
            send_fn=lambda *a, **k: None, dry_run=False,
            sleep_fn=lambda s: None, state=state, now=100000.0,
            own_payload=_FORK)
        self.assertTrue(fake.sent,
                        "a provenance-backed clear is attempted: %r" % logs)
        self.assertFalse(any("template-clear-locked" in ln for ln in logs),
                         "the lock does not gate the provenance path")


if __name__ == "__main__":
    unittest.main()
