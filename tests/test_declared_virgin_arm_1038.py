"""#1038 — post-reboot auto re-arm of /goal for DECLARED managed windows.

RETIRED by #1260 (owner 25.10.2026): the declared-window VIRGIN arm is gone — a
goal exists only because the owner typed `/autopilot` (on david3 it queued a
goal into a fresh `claude-new` session the owner started without one). The
virgin-arm tests moved out; `tests/test_no_unsolicited_goal_1260.py` locks the
reversal. What stays here is #1038's `status` surface (`goal:` row, variant
label, outside-pane resolver), which still serves owner-armed goals.

After a gk VPS reboot the declared windows (gk review, gk-infra, d3, the
controller's own) come back as FRESH claude sessions: new sid, new transcript,
no `Goal set:` marker, no pending goal-arm request. The #403 collapse deleted
the old "virgin candidate" heuristic, so `goal_sweep` (job 9) only DELIVERS
pending requests recorded by an explicit `/autopilot` or a watchdog re-arm of
an ALREADY-armed loop — a never-armed post-reboot window matches none, so
nothing arms and the owner digs up an obsolete goal text by hand. #1023 also
made goal delivery (`nudge="goal-sweep"`) a PRIORITY machine-nudge defaulting
OFF, so even a manual `/autopilot` on a declared window is suppressed unless
the owner staged it on.

RED against the pre-#1038 tree:
  (a) job 9 has no declared-window virgin-arm pre-scan → a fresh declared
      window is never armed.
  (b) same, with a recent human present → no hold path exists.
  (c) guard: a NON-declared pane is never virgin-armed (stays green).
  (d) `cli_concurrency.goal_status_row` / `goal_variant_label` do not exist.
  (e) even with a request, delivery via `goal-sweep` is suppressed at all-OFF —
      a declared window must arm through an ALWAYS-ON recovery nudge instead.

GREEN once the virgin scan + the always-on `goal-arm` recovery nudge + the
status surface land.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_concurrency  # noqa: E402
import cli_fleet  # noqa: E402

from tests._goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    GOAL_ARMED_CAP,
    GOAL_BUSY_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
)

# A gk review-window cwd (the fleet declares role="review", mode="parallel").
GK_REVIEW_CWD = "/home/gatekeeper/devel/odoo/odoo-erp"
SHORT_REVIEW_GOAL = "/goal STOP CONDITIONS review/parallel"


def _short_rearm(cwd):
    """A resolvable declared-window (text, authority) — short so the fake
    types it in one literal chunk."""
    return (SHORT_REVIEW_GOAL, "full")


def _declared_windows(cwd, role="review", mode="parallel"):
    return [{"name": "gk", "cwd": cwd, "role": role, "mode": mode}]


def _typed_goal(tmux):
    """The literal-type send-keys argv(s) that typed a `/goal ...` line."""
    return [a for a in tmux.sent
            if "-l" in a and isinstance(a[-1], str) and a[-1].startswith("/goal")]


class TestGoalStatusRow1038(unittest.TestCase):
    GK_REVIEW_CWD = GK_REVIEW_CWD

    def _win(self, role="review", mode="parallel"):
        return [{"name": "gk", "cwd": self.GK_REVIEW_CWD, "role": role, "mode": mode}]

    def test_variant_label(self):
        self.assertEqual(cli_concurrency.goal_variant_label("parallel", "review"),
                         "review/parallel")
        self.assertEqual(cli_concurrency.goal_variant_label("sequential", "infra"),
                         "infra/sequential")
        self.assertEqual(cli_concurrency.goal_variant_label("sequential", None),
                         "sequential")
        self.assertEqual(cli_concurrency.goal_variant_label("parallel", None),
                         "parallel")

    def test_armed_row_names_the_variant(self):
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = cli_concurrency.goal_status_row(self.GK_REVIEW_CWD, armed=True,
                                                  pending=False)
        self.assertIn("goal: armed", row)
        self.assertIn("review/parallel", row)

    def test_not_armed_row_points_to_autopilot(self):
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = cli_concurrency.goal_status_row(self.GK_REVIEW_CWD, armed=False,
                                                  pending=False)
        self.assertIn("NOT armed", row)
        self.assertIn("type /autopilot", row)

    def test_pending_row_reads_arming(self):
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=self._win("infra", "sequential")):
            row = cli_concurrency.goal_status_row(self.GK_REVIEW_CWD, armed=False,
                                                  pending=True)
        self.assertIn("arming", row)
        self.assertIn("infra/sequential", row)


class TestGoalRowOutsidePane1038(unittest.TestCase):
    """#1038 FOLLOW-UP -- `airuleset.py status` goal row is HONEST when run
    OUTSIDE a tmux pane (over ssh, no $TMUX_PANE).

    The first #1038 lane's `goal:` row resolved "this pane" via `$TMUX_PANE`
    (`resolve_self_pane`); over ssh that is unset, so `resolve_self_pane`
    returned `("","","")` and the row printed `NOT armed` WITHOUT ever reading
    a pane -- the honesty defect the supervisor found live (2026-09-15 17:36
    CEST: `NOT armed` over ssh while the watchdog read `armed=yes` for the same
    panes). Outside a pane the row must resolve the pane whose current path IS
    this cwd (realpath equality, the `is_exact_declared_window` semantics) and
    report ITS armed state; when no pane matches, `unmeasurable outside a pane`
    -- NEVER `NOT armed` without a real pane read.

    RED against the pre-fix tree:
      * `watchdog.compact.resolve_declared_window_pane` does not exist.
      * `airuleset.goal_status_probe` does not exist.
      * `cli_concurrency.goal_status_row` has no `pane_found` keyword (the
        `unmeasurable` branch), so a `pane_found=False` call is a TypeError.
    """
    GK_REVIEW_CWD = GK_REVIEW_CWD

    def setUp(self):
        # isolate the goal-requests store so the live 60s watchdog never races
        # this test's pending read, and a stray real request never leaks in.
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _win(self, role="review", mode="parallel"):
        return [{"name": "gk", "cwd": self.GK_REVIEW_CWD, "role": role, "mode": mode}]

    # ---- the pane-by-cwd resolver (watchdog.compact.resolve_declared_window_pane) ----

    def test_resolver_matches_cwd_pane_and_prefers_claude(self):
        from watchdog import compact
        # a node pane AND a claude pane at the SAME cwd -> the claude one wins.
        tmux = DeliverGoalFakeTmux(
            [("%7", "node", self.GK_REVIEW_CWD, "70"),
             ("%9", "claude", self.GK_REVIEW_CWD, "90")], GOAL_IDLE_CAP)
        pid, cwd, _sid = compact.resolve_declared_window_pane(
            self.GK_REVIEW_CWD, run=tmux)
        self.assertEqual(pid, "%9",
                         "the claude pane at the cwd must win over a node pane")

    def test_resolver_no_matching_pane_returns_empty(self):
        from watchdog import compact
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", "/some/other/path", "90")], GOAL_IDLE_CAP)
        pid, _cwd, _sid = compact.resolve_declared_window_pane(
            self.GK_REVIEW_CWD, run=tmux)
        self.assertEqual(pid, "",
                         "no pane at the cwd -> empty (the unmeasurable state)")

    def test_resolver_realpath_equality_not_containment(self):
        from watchdog import compact
        # a pane cd'd into a SUBDIR of the cwd is NOT a match (equality, not
        # containment): the resolver reports THE window, never a sub-pane.
        subdir = self.GK_REVIEW_CWD + "/addons"
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", subdir, "90")], GOAL_IDLE_CAP)
        pid, _cwd, _sid = compact.resolve_declared_window_pane(
            self.GK_REVIEW_CWD, run=tmux)
        self.assertEqual(pid, "", "a subdir pane must not match the window cwd")

    # ---- the composed status probe: the (a)/(b)/(c)/(d) cases ----

    def test_a_outside_pane_armed_window_reads_armed(self):
        import airuleset
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", self.GK_REVIEW_CWD, "90")], GOAL_ARMED_CAP)
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = airuleset.goal_status_probe(self.GK_REVIEW_CWD, run=tmux,
                                              pane_env="")
        self.assertIn("goal: armed", row)
        self.assertIn("review/parallel", row)
        self.assertNotIn("NOT armed", row)

    def test_b_outside_pane_dark_window_reads_not_armed(self):
        import airuleset
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", self.GK_REVIEW_CWD, "90")], GOAL_IDLE_CAP)
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = airuleset.goal_status_probe(self.GK_REVIEW_CWD, run=tmux,
                                              pane_env="")
        self.assertIn("NOT armed", row)
        self.assertIn("type /autopilot", row)
        self.assertIn("review/parallel", row)

    def test_c_outside_pane_no_pane_is_unmeasurable(self):
        import airuleset
        # a live claude pane exists, but at ANOTHER path -> no match for this
        # cwd -> unmeasurable, NEVER a NOT-armed verdict with no read.
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", "/some/other/path", "90")], GOAL_IDLE_CAP)
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = airuleset.goal_status_probe(self.GK_REVIEW_CWD, run=tmux,
                                              pane_env="")
        self.assertIn("unmeasurable", row)
        self.assertNotIn("NOT armed", row)
        self.assertIn("review/parallel", row)

    def test_d_inside_pane_uses_self_pane_and_skips_cwd_resolver(self):
        import airuleset
        from watchdog import compact
        # $TMUX_PANE set -> resolve_self_pane resolves %9; the cwd resolver must
        # NOT be consulted at all (byte-identical inside-a-pane behaviour).
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", self.GK_REVIEW_CWD, "90")], GOAL_ARMED_CAP)

        def _boom(*a, **k):
            raise AssertionError(
                "resolve_declared_window_pane must NOT run inside a pane")

        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()), \
             m.patch.object(compact, "resolve_declared_window_pane",
                            side_effect=_boom):
            row = airuleset.goal_status_probe(self.GK_REVIEW_CWD, run=tmux,
                                              pane_env="%9")
        self.assertIn("goal: armed", row)
        self.assertIn("review/parallel", row)

    def test_unmeasurable_branch_is_distinct_from_not_armed(self):
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = cli_concurrency.goal_status_row(
                self.GK_REVIEW_CWD, armed=None, pending=False, pane_found=False)
        self.assertIn("unmeasurable", row)
        self.assertNotIn("NOT armed", row)
        self.assertIn("review/parallel", row)

    def test_resolved_pane_undeterminable_is_never_not_armed(self):
        # #1038-review 🟡: a pane WAS resolved (pane_found=True) but its armed
        # state could not be read (pane_goal_armed None -> busy/scrolled/empty).
        # That is NOT a dark pane, so it must NEVER read NOT armed -- the same
        # honesty class #1038 targets, just reached via a busy resolved pane.
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = cli_concurrency.goal_status_row(
                self.GK_REVIEW_CWD, armed=None, pending=False, pane_found=True)
        self.assertIn("undeterminable", row)
        self.assertNotIn("NOT armed", row)
        self.assertIn("review/parallel", row)

    def test_undeterminable_pending_still_reads_arming(self):
        # a pending request wins over an undeterminable read (a request IS in
        # flight, so "arming" is the honest state, not "undeterminable").
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=self._win("infra", "sequential")):
            row = cli_concurrency.goal_status_row(
                self.GK_REVIEW_CWD, armed=None, pending=True, pane_found=True)
        self.assertIn("arming", row)
        self.assertNotIn("undeterminable", row)
        self.assertNotIn("NOT armed", row)

    def test_outside_pane_busy_window_is_undeterminable_not_not_armed(self):
        # the probe end-to-end: outside a pane, the cwd resolves a live claude
        # pane whose capture is BUSY (pane_goal_armed None) -> undeterminable,
        # never NOT armed (the #1038-review residual over ssh).
        import airuleset
        tmux = DeliverGoalFakeTmux(
            [("%9", "claude", self.GK_REVIEW_CWD, "90")], GOAL_BUSY_CAP)
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = airuleset.goal_status_probe(self.GK_REVIEW_CWD, run=tmux,
                                              pane_env="")
        self.assertIn("undeterminable", row)
        self.assertNotIn("NOT armed", row)
        self.assertIn("review/parallel", row)

    def test_pane_found_is_keyword_only(self):
        # #1038-review 2 🔵: pane_found was inserted between `pending` and
        # `window_name`, so a POSITIONAL 4th arg (a caller meaning window_name)
        # would silently misbind to pane_found. Keyword-only closes the trap:
        # a positional 4th arg must now raise TypeError.
        with self.assertRaises(TypeError):
            cli_concurrency.goal_status_row(self.GK_REVIEW_CWD, True, False, True)
        # the keyword form still works.
        with m.patch.object(cli_fleet, "box_windows", return_value=self._win()):
            row = cli_concurrency.goal_status_row(
                self.GK_REVIEW_CWD, armed=True, pending=False, pane_found=True)
        self.assertIn("goal: armed", row)


class TestStatusGoalRowIsReadOnly1038(unittest.TestCase):
    """#1038 FOLLOW-UP keystroke lock -- the outside-a-pane goal-row path makes
    ONLY read-only tmux calls (list-panes / capture-pane / display-message),
    NEVER send-keys. A `status` command that TYPED into a pane would be an
    unsolicited keystroke into a window the owner may be using -- structurally
    forbidden. Locked by source inspection so a future edit that reaches for
    a keystroke primitive on this read-only path fails loudly."""

    def _src(self, fn):
        import inspect
        return inspect.getsource(fn)

    def test_resolver_never_sends_keys(self):
        from watchdog import compact
        src = self._src(compact.resolve_declared_window_pane)
        for banned in ("send-keys", "send_continue", "send_verified",
                       "deliver_goal", "deliver_with_stash"):
            self.assertNotIn(banned, src,
                             "resolve_declared_window_pane must stay read-only "
                             "-- found %r" % banned)

    def test_probe_never_sends_keys(self):
        import airuleset
        src = self._src(airuleset.goal_status_probe)
        for banned in ("send-keys", "send_continue", "send_verified",
                       "deliver_goal", "deliver_with_stash"):
            self.assertNotIn(banned, src,
                             "goal_status_probe must stay read-only -- found %r"
                             % banned)


if __name__ == "__main__":
    unittest.main()
