"""#1113 — a machine `/goal` keystroke NEVER reaches an ACTIVE, armed loop, and
the janitor recognises + clears its OWN stranded payload even when the render is
truncated / grid-wrapped so its reconstruction is not a clean substring.

Owner regression (>10x reported, 22.9.2026): the watchdog typed a 3.7 kB `/goal`
payload into david1-3's LIVE, ARMED sessions (the #623 `stale-rearm` typing path
after the v0.1.399 template change), the submit was swallowed, a TRUNCATED
payload sat in their input boxes, and the janitor logged
`cleanup=declined (box not our own leftover)` because the wrapped 22-row box did
not reconstruct as a contiguous substring of the payload.

The fix (design-by main, Approach 1):
  1. The stale-rearm typing path is REMOVED - `goal_dark_watch`'s armed-True
     branch only OBSERVES a drift (`stale-drift ... waits for the next natural
     arm`), never records a request; a leftover on-disk `stale-rearm` request is
     dropped `drop:stale-rearm-retired`, never typed.
  2. `deliver_goal` refuses `drop:already-armed` for EVERY origin at an armed
     footer, before any keystroke.
  3. `_box_is_own_leftover` gains a provenance-backed proof (a live janitor mark
     + the box TAIL row is the payload tail + the whitespace-stripped box body is
     a substring of the payload) so `_log_arm_confirm_fail` and `_janitor_recover`
     CLEAR a truncated own payload (single/double Escape + backspace, NEVER a
     submit of the truncated text) instead of declining.
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
    GOAL_ARMED_CAP,
    GOAL_IDLE_CAP,
    _isolate_goal_state,
    _write_goal_marker,
    _write_marker_transcript,
)

# The REAL david1 payload - the shipped fork-no-merge /goal line (the exact
# template that stranded in david1-3). Ends `... never a compact HOLD.`.
_PAYLOAD = goal_registry.render_goal_line("fork-no-merge", "parallel", None)

# A stale (drifted) armed condition + the shipped template it drifted from, from
# the #623 signature (shared with test_goal_stale_rearm_623).
_SIG = "STOP CONDITIONS — the loop is DONE the moment EITHER holds"
_OLD_COND = (_SIG + ", both checkable from the transcript: (A) an OLDER wording "
             "of the stop conditions, from before the shipped template changed.")
_NEW_TEMPLATE = ("/goal " + _SIG + ", both checkable from the transcript: (A) the "
                 "NEW wording carrying the saturation clause: SATURATE parallel "
                 "isolation:worktree autopilot-worker lanes.")


def _grid_scroll_capture(payload, width=176, visible_rows=14):
    """Render `payload` the way the live david1 box captured: a CHARACTER-GRID
    wrap at `width` cols (a long backtick/path token is cut mid-token at the box
    edge - the `origin/main` -> `origin/ main` divergence), 2-space continuation
    indents, the box INTERNALLY SCROLLED so only the last `visible_rows` rows are
    shown with the prompt glyph re-pinned to the first visible (mid-payload) row,
    inside an 80-row capture with ordinary transcript rows above. The
    reconstruction of this box is NOT a contiguous substring of the payload."""
    rows, i, first = [], 0, True
    while i < len(payload):
        w = width if first else width - 2
        chunk = payload[i:i + w]
        rows.append(chunk if first else "  " + chunk)
        i += w
        first = False
    if len(rows) > visible_rows:
        rows = rows[-visible_rows:]
    rows[0] = "❯ " + rows[0].lstrip("❯  ")
    sep = "─" * width
    above = ["  transcript line %d" % k for k in range(60)]
    above += ["  I'll keep working through the assigned backlog now.", ""]
    box = [sep] + rows + [sep, "  ? for shortcuts  ◎ /goal active"]
    return "\n".join(above + box) + "\n"


def _clean_wrap_capture(payload, width=176, visible_rows=14):
    """A WORD-WRAPPED scrolled render (the #737 shape) - a clean substring."""
    words, rows, cur, first = payload.split(" "), [], "", True
    for word in words:
        avail = width if first else width - 2
        cand = (cur + " " + word) if cur else word
        if len(cand) <= avail:
            cur = cand
        else:
            rows.append(cur if first else "  " + cur)
            cur, first = word, False
    rows.append(cur if first else "  " + cur)
    if len(rows) > visible_rows:
        rows = rows[-visible_rows:]
    rows[0] = "❯ " + rows[0].lstrip("❯  ")
    sep = "─" * width
    return "\n".join(["  transcript"] * 40 + [sep] + rows
                     + [sep, "  ? for shortcuts  ◎ /goal active"]) + "\n"


class _GridScrollFake(DeliverGoalFakeTmux):
    """A fake `run` whose input box renders CHARACTER-GRID wrapped + scrolled
    (`_grid_scroll_capture`), so `_box_norm_from_capture` diverges from a clean
    substring exactly like the live david1 capture. BSpace trims `self.box`
    (inherited), so the janitor clear loop converges to a bare box."""

    def _render(self):
        if not self.box:
            return self._with_arm(self.captured)
        return _grid_scroll_capture(self.box)


class _CleanWrapFake(DeliverGoalFakeTmux):
    """A fake `run` whose input box renders a clean WORD-wrapped scroll
    (`_clean_wrap_capture`, the #737 shape) -- a contiguous substring of the
    payload, so `_box_is_own_leftover` proves ownership WITHOUT provenance and
    `_janitor_clear_box`'s backspace loop converges to a bare box (no 1-char
    grid-remainder artifacts). Used to prove the clear ACTUALLY empties the box
    (the convergence property the grid fixture's terminal-reflow model can't
    show)."""

    def _render(self):
        if not self.box:
            return self._with_arm(self.captured)
        return _clean_wrap_capture(self.box)


PANE = [("%9", "claude", "/home/newlevel/devel/armed1113", "111")]
CWD = "/home/newlevel/devel/armed1113"
PID = "%9"


# --------------------------------------------------------------------------- #
# (a) goal_dark_watch's armed-True branch OBSERVES a drift, never records/types.
# --------------------------------------------------------------------------- #
class TestArmedLoopNeverReArmed(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _sweep(self, sid, cond, state=None, now=100000):
        proj = self._dir()
        _write_marker_transcript(proj, CWD, sid)
        _write_goal_marker(proj, CWD, sid, "Goal set: " + cond, ts_epoch=500)
        tmux = DeliverGoalFakeTmux(PANE, GOAL_ARMED_CAP)
        reqs = self._dir() / "goal-requests.json"
        logs = goal.goal_dark_watch(
            now, run=tmux, send_fn=lambda mm, **k: None, projects_dir=proj,
            state={} if state is None else state, sleep_fn=lambda s: None,
            obligation_fn=lambda cwd: (7, 100000),
            rearm_fn=lambda cwd: (_NEW_TEMPLATE, "fork-no-merge"),
            requests_path=reqs, dry_run=False)
        return goal.load_goal_requests(reqs), tmux, logs

    def test_drift_observed_no_request_no_keystroke(self):
        reqs, tmux, logs = self._sweep("sess-drift-1", _OLD_COND)
        self.assertEqual(reqs, {},
                         "an ACTIVE armed loop is NEVER re-armed by a keystroke")
        self.assertEqual(tmux.sent, [], "the armed branch NEVER types")
        self.assertTrue(any("stale-drift" in ln
                            and "waits for the next natural arm" in ln
                            for ln in logs),
                        "a drift is OBSERVED, not acted on: %r" % logs)

    def test_drift_observation_deduped_per_template_version(self):
        state = {}
        r1, _, l1 = self._sweep("sess-drift-2", _OLD_COND, state=state)
        r2, _, l2 = self._sweep("sess-drift-2", _OLD_COND, state=state,
                                now=100200)
        self.assertTrue(any("stale-drift" in ln for ln in l1))
        self.assertFalse(any("stale-drift" in ln for ln in l2),
                         "the same drift is observed ONCE per template version")

    def test_current_condition_is_silent(self):
        cond = _NEW_TEMPLATE[len("/goal "):]
        reqs, _, logs = self._sweep("sess-cur", cond)
        self.assertEqual(reqs, {})
        self.assertFalse(any("stale-drift" in ln for ln in logs))

    def test_foreign_condition_is_never_touched(self):
        reqs, _, logs = self._sweep("sess-foreign", "fix the login bug and ship")
        self.assertEqual(reqs, {})
        self.assertFalse(any("stale-drift" in ln for ln in logs))


# --------------------------------------------------------------------------- #
# (b) deliver_goal refuses at an armed footer for EVERY origin; a leftover
#     stale-rearm request is dropped retired before any keystroke.
# --------------------------------------------------------------------------- #
class TestDeliverRefusesArmed(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _deliver(self, sid, origin, cap=GOAL_ARMED_CAP, cond=_OLD_COND):
        proj = self._dir()
        _write_marker_transcript(proj, CWD, sid)
        _write_goal_marker(proj, CWD, sid, "Goal set: " + cond, ts_epoch=500)
        tmux = DeliverGoalFakeTmux(PANE, cap, model_type=True)
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "test")):
            word = goal.deliver_goal(
                sid, CWD, _NEW_TEMPLATE, "fork-no-merge", run=tmux,
                projects_dir=proj, now=100000, request_ts=100000,
                sleep_fn=lambda s: None, origin=origin)
        return word, tmux

    def test_self_callback_drops_already_armed(self):
        word, tmux = self._deliver("sess-b-self", "self-callback")
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(tmux.sent, [], "no keystroke into an armed footer")

    def test_auth_rearm_drops_already_armed(self):
        word, tmux = self._deliver("sess-b-auth", "auth-rearm")
        self.assertEqual(word, "drop:already-armed")
        self.assertEqual(tmux.sent, [])

    def test_leftover_stale_rearm_request_dropped_retired(self):
        # A leftover on-disk stale-rearm request (recorded before the retire) is
        # dropped terminally BEFORE any pane work - even into an IDLE pane, so it
        # never types the payload it once would have.
        word, tmux = self._deliver("sess-b-stale", "stale-rearm", cap=GOAL_IDLE_CAP)
        self.assertEqual(word, "drop:stale-rearm-retired")
        self.assertEqual(tmux.sent, [], "a retired stale-rearm never types")

    def test_stale_rearm_retired_is_terminal(self):
        self.assertIn("drop:stale-rearm-retired", goal._GOAL_TERMINAL_WORDS)

    def test_stale_rearm_origin_left_the_typing_origins(self):
        self.assertNotIn(goal._GOAL_STALE_REARM_ORIGIN,
                         goal._GOAL_WATCHDOG_REARM_ORIGINS,
                         "the stale-rearm origin no longer types")
        self.assertNotIn(goal._GOAL_STALE_REARM_ORIGIN,
                         goal._GOAL_ATTEMPTS_STATE_KEYS)


# --------------------------------------------------------------------------- #
# (c)/(d) _log_arm_confirm_fail CLEARS a truncated own payload with provenance;
#         declines without it / on a foreign tail.
# --------------------------------------------------------------------------- #
class TestArmConfirmCleanupTruncated(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _run_cleanup(self, state, payload=_PAYLOAD):
        fake = _GridScrollFake(PANE, GOAL_ARMED_CAP, model_type=True)
        fake.box = payload
        now = 100000.0
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal._log_arm_confirm_fail(
                "sess-c", CWD, payload, PID, fake,
                sleep_fn=lambda s: None, state=state, now=now, tpath=None)
        return fake, Path(self.syncp).read_text(encoding="utf-8")

    def test_provenance_mark_clears_truncated_own_payload(self):
        # The david1 render: provenance mark fresh + the box tail is the payload
        # tail -> the truncated own payload is RECOGNISED (not declined) and the
        # CLEAR path runs, with clear keystrokes and NEVER an Enter/submit (the
        # whole harm). (`_janitor_clear_box`'s convergence-to-bare is its own
        # separately-tested property; a real terminal reflows without the 1-char
        # tail rows this synthetic grid produces, so we assert the load-bearing
        # #1113 properties -- recognition + never-submit -- not the fixture's
        # exact backspace count.)
        state = {"janitor_watch": {PID: 100000.0}}
        fake, log = self._run_cleanup(state)
        self.assertNotIn("cleanup=declined", log,
                         "a provenance-proven own leftover must not decline")
        self.assertIn("ARM-CONFIRM-CLEANUP", log,
                      "the clear path is reached")
        keys = [a[-1] for a in fake.sent]
        self.assertIn("Escape", keys, "the clear path Escapes")
        self.assertIn("BSpace", keys, "the clear path backspaces")
        self.assertNotIn("Enter", keys,
                         "NEVER submit a truncated payload (the whole harm)")

    def test_no_mark_declines(self):
        # Without the provenance mark, the truncated render is NOT proven ours ->
        # untouched (the fail-safe: no proof -> never act).
        fake, log = self._run_cleanup(state={})
        self.assertIn("cleanup=declined", log)
        keys = [a[-1] for a in fake.sent]
        self.assertNotIn("BSpace", keys, "no clear without provenance")

    def test_foreign_tail_declines_even_with_mark(self):
        # A foreign long draft with the provenance mark present: the tail is NOT
        # the payload tail and the body is not a substring -> untouched.
        foreign = ("Please review the following migration and confirm the "
                   "foreign keys cascade safely against the production orders "
                   "and invoice_line tables before I run it on the live server "
                   "because I am worried about the customer data integrity here "
                   "and whether the delete will orphan any rows in related "
                   "models that reference these records by id across modules.")
        state = {"janitor_watch": {PID: 100000.0}}
        fake = _GridScrollFake(PANE, GOAL_ARMED_CAP, model_type=True)
        fake.box = foreign
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal._log_arm_confirm_fail(
                "sess-d", CWD, _PAYLOAD, PID, fake,
                sleep_fn=lambda s: None, state=state, now=100000.0, tpath=None)
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("cleanup=declined", log,
                      "a foreign draft is NEVER cleared, even with a mark")
        self.assertNotIn("BSpace", [a[-1] for a in fake.sent])

    def test_clear_actually_empties_the_box_convergence(self):
        # r2 evidence-gap fix: prove the arm-confirm cleanup ACTUALLY empties the
        # box (`cleared=True`), not just that it is attempted. A clean word-wrap
        # own leftover converges within `_janitor_clear_box`'s iteration cap.
        fake = _CleanWrapFake(PANE, GOAL_ARMED_CAP, model_type=True)
        fake.box = _PAYLOAD[:1600]
        with m.patch.object(wd, "_draft_rescue_persist", return_value=None):
            goal._log_arm_confirm_fail(
                "sess-conv", CWD, _PAYLOAD[:1600], PID, fake,
                sleep_fn=lambda s: None,
                state={"janitor_watch": {PID: 100000.0}}, now=100000.0, tpath=None)
        log = Path(self.syncp).read_text(encoding="utf-8")
        self.assertIn("ARM-CONFIRM-CLEANUP", log)
        self.assertIn("cleared=True", log, "the box is actually cleared to bare")
        self.assertEqual(fake.box, "", "the box is actually emptied")
        self.assertNotIn("Enter", [a[-1] for a in fake.sent])


# --------------------------------------------------------------------------- #
# (e) _janitor_recover clears the truncated own payload on the next sweep.
# --------------------------------------------------------------------------- #
class TestJanitorRecoverTruncated(unittest.TestCase):
    def test_janitor_clears_truncated_own_payload_with_mark(self):
        # The janitor RECOGNISES the truncated own payload (provenance mark +
        # tail proof) and takes the CLEAR action -- proven by the draft-rescue
        # snapshot (`_janitor_recover` persists it ONLY on a non-pop recovery
        # action it decided to take) and the Escape keystroke, and NEVER an
        # Enter/submit. (Today it returned early as a foreign occupant -> no
        # draft-rescue, no keystroke. `_janitor_clear_box`'s convergence is a
        # separately-tested property; see the sibling arm-confirm test's note.)
        fake = _GridScrollFake(PANE, GOAL_ARMED_CAP, model_type=True)
        fake.box = _PAYLOAD
        captured = fake(["tmux", "capture-pane", "-p", "-t", PID])
        state = {"janitor_watch": {PID: 100000.0}}
        rec = {}
        logs = janitor._janitor_recover(
            fake, rec, PID, CWD, captured, "sess:0.0",
            send_fn=lambda *a, **k: None, dry_run=False,
            sleep_fn=lambda s: None, state=state, now=100000.0,
            own_payload=_PAYLOAD)
        self.assertTrue(any("draft-rescue" in ln for ln in logs),
                        "the janitor DECIDED its own leftover needs clearing "
                        "(no early foreign-untouched return): %r" % logs)
        self.assertIn("Escape", [a[-1] for a in fake.sent],
                      "the janitor takes the clear path")
        self.assertIn("BSpace", [a[-1] for a in fake.sent],
                      "the janitor backspaces the stranded payload")
        self.assertNotIn("Enter", [a[-1] for a in fake.sent],
                         "the janitor NEVER submits a truncated payload")

    def test_janitor_clear_actually_empties_the_box_convergence(self):
        # r2 evidence-gap fix: prove the clear ACTUALLY empties the box (not just
        # that it is attempted). A clean word-wrapped own leftover (the #737
        # shape) is a contiguous substring, so `_box_is_own_leftover` proves it
        # ours and `_janitor_clear_box`'s backspace loop converges to bare.
        fake = _CleanWrapFake(PANE, GOAL_ARMED_CAP, model_type=True)
        fake.box = _PAYLOAD[:1600]          # clean-wraps + clears within the cap
        captured = fake(["tmux", "capture-pane", "-p", "-t", PID])
        state = {"janitor_watch": {PID: 100000.0}}
        logs = janitor._janitor_recover(
            fake, {}, PID, CWD, captured, "sess:0.0",
            send_fn=lambda *a, **k: None, dry_run=False,
            sleep_fn=lambda s: None, state=state, now=100000.0,
            own_payload=_PAYLOAD[:1600])
        self.assertTrue(any(ln.startswith("RECOVERED (janitor)") for ln in logs),
                        "the janitor RECOVERED (cleared) the own leftover: %r" % logs)
        self.assertEqual(fake.box, "", "the box is actually emptied")
        self.assertNotIn("Enter", [a[-1] for a in fake.sent],
                         "never a submit, even on the converging path")

    def test_janitor_leaves_foreign_untouched(self):
        foreign = ("napis mi prosim zhrnutie stretnutia z minuleho tyzdna a "
                   "posli ho klientovi ked to bude hotove dakujem pekne za to "
                   "a este pridaj rozpocet na buduci mesiac ak stihnes vdaka.")
        fake = _GridScrollFake(PANE, GOAL_ARMED_CAP, model_type=True)
        fake.box = foreign
        captured = fake(["tmux", "capture-pane", "-p", "-t", PID])
        state = {"janitor_watch": {PID: 100000.0}}
        logs = janitor._janitor_recover(
            fake, {}, PID, CWD, captured, "sess:0.0",
            send_fn=lambda *a, **k: None, dry_run=False,
            sleep_fn=lambda s: None, state=state, now=100000.0,
            own_payload=_PAYLOAD)
        self.assertFalse(any(ln.startswith("RECOVERED") for ln in logs),
                         "a foreign draft is never cleared: %r" % logs)
        self.assertEqual(fake.sent, [],
                         "a foreign draft gets NO keystroke at all")


# --------------------------------------------------------------------------- #
# The pure proof: _box_is_own_leftover's provenance-backed test.
# --------------------------------------------------------------------------- #
class TestBoxIsOwnLeftoverProvenance(unittest.TestCase):
    def test_clean_substring_still_matches_without_provenance(self):
        # #737 word-wrapped scroll: a clean contiguous substring is proven ours
        # WITHOUT needing provenance (backward-compatible).
        cap = _clean_wrap_capture(_PAYLOAD)
        self.assertTrue(stash._box_is_own_leftover(
            cap, _PAYLOAD, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR))

    def test_truncated_render_needs_provenance(self):
        cap = _grid_scroll_capture(_PAYLOAD)
        # a grid-wrapped truncated render is NOT a clean substring...
        self.assertFalse(stash._box_is_own_leftover(
            cap, _PAYLOAD, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR),
            "the truncated render is not a contiguous substring")
        # ...but WITH provenance the tail + whitespace-stripped body prove it.
        self.assertTrue(stash._box_is_own_leftover(
            cap, _PAYLOAD, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR, provenance=True))

    def test_foreign_never_matches_even_with_provenance(self):
        foreign = ("this is a completely unrelated human draft that is long "
                   "enough to clear the minimum substring floor but shares no "
                   "contiguous run with the shipped goal template payload text "
                   "anywhere at all so it can never false-positive as ours ok.")
        cap = _grid_scroll_capture(foreign)
        self.assertFalse(stash._box_is_own_leftover(
            cap, _PAYLOAD, stash.GOAL_ARM_LEFTOVER_MIN_SUBSTR, provenance=True))


if __name__ == "__main__":
    unittest.main()
