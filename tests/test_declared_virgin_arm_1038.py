"""#1038 — post-reboot auto re-arm of /goal for DECLARED managed windows.

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
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
import cli_concurrency  # noqa: E402
import cli_fleet  # noqa: E402

from tests._goal_arm_helpers import (  # noqa: E402
    GOAL_IDLE_CAP,
    GOAL_ARMED_CAP,
    GOAL_BUSY_CAP,
    DeliverGoalFakeTmux,
    _write_marker_transcript,
    _write_goal_marker,
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


class TestDeclaredVirginArm1038(unittest.TestCase):
    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _proj(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _reqp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "goal-requests.json")

    def test_a_virgin_declared_window_is_armed_once(self):
        proj = self._proj()
        sid = "sess-virgin-1"
        _write_marker_transcript(proj, GK_REVIEW_CWD, sid)   # no goal marker → virgin
        reqp = self._reqp()
        tmux = DeliverGoalFakeTmux([("%9", "claude", GK_REVIEW_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=_declared_windows(GK_REVIEW_CWD)), \
             m.patch.object(goal, "_default_rearm_fn", side_effect=_short_rearm):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=reqp, state={},
                                   sleep_fn=lambda s: None)
        self.assertTrue(_typed_goal(tmux),
                        "expected a /goal type into the virgin declared window; "
                        "logs=%r sent=%r" % (logs, tmux.sent))
        # a "sent" request is cleared
        self.assertEqual(goal.load_goal_requests(reqp), {})
        # SECOND sweep: the pane now reads armed (footer glyph) → the scan's
        # `armed is not False` skip fires → NO re-type (the request also cleared).
        tmux.sent.clear()
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=_declared_windows(GK_REVIEW_CWD)), \
             m.patch.object(goal, "_default_rearm_fn", side_effect=_short_rearm):
            goal.goal_sweep(2100, run=tmux, projects_dir=proj,
                            requests_path=reqp, state={}, sleep_fn=lambda s: None)
        self.assertEqual(_typed_goal(tmux), [],
                         "must not re-type an already-armed declared window (floor)")

    def test_b_recent_human_holds_the_virgin_arm(self):
        proj = self._proj()
        sid = "sess-virgin-2"
        _write_marker_transcript(proj, GK_REVIEW_CWD, sid)
        reqp = self._reqp()
        tmux = DeliverGoalFakeTmux([("%9", "claude", GK_REVIEW_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=_declared_windows(GK_REVIEW_CWD)), \
             m.patch.object(goal, "_default_rearm_fn", side_effect=_short_rearm), \
             m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(True, "presence marker 5s old")):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=reqp, state={},
                                   sleep_fn=lambda s: None)
        self.assertEqual(_typed_goal(tmux), [],
                         "a recent human must HOLD the virgin arm — no keystroke")
        self.assertTrue(any("recent-human" in ln for ln in logs),
                        "expected a recent-human hold line; logs=%r" % (logs,))
        # the request stays PENDING for a later idle sweep (never dropped).
        self.assertIn(sid, goal.load_goal_requests(reqp))

    def test_d_user_cleared_declared_window_is_never_re_armed(self):
        # #170 — a declared window the owner explicitly `/goal clear`ed carries a
        # `Goal cleared:` marker; the virgin scan must NOT re-arm it.
        proj = self._proj()
        sid = "sess-cleared"
        _write_marker_transcript(proj, GK_REVIEW_CWD, sid)
        _write_goal_marker(proj, GK_REVIEW_CWD, sid, "Goal cleared: (user)")
        reqp = self._reqp()
        tmux = DeliverGoalFakeTmux([("%9", "claude", GK_REVIEW_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=_declared_windows(GK_REVIEW_CWD)), \
             m.patch.object(goal, "_default_rearm_fn", side_effect=_short_rearm):
            goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                            requests_path=reqp, state={}, sleep_fn=lambda s: None)
        self.assertEqual(_typed_goal(tmux), [],
                         "a user-cleared declared window must never be re-armed (#170)")
        self.assertEqual(goal.load_goal_requests(reqp), {})

    def test_e_undeterminable_marker_past_cap_is_never_virgin_armed(self):
        # #170/#1038-review — seed_goal_marker returns unknown-past-cap when a
        # marker (arm OR clear) MAY sit deeper than the 32 MB seed cap: the scan
        # must treat it as UNDETERMINABLE (skip), never a fabricated virgin arm.
        proj = self._proj()
        sid = "sess-pastcap"
        _write_marker_transcript(proj, GK_REVIEW_CWD, sid)
        reqp = self._reqp()
        tmux = DeliverGoalFakeTmux([("%9", "claude", GK_REVIEW_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=_declared_windows(GK_REVIEW_CWD)), \
             m.patch.object(goal, "_default_rearm_fn", side_effect=_short_rearm), \
             m.patch.object(wd, "seed_goal_marker",
                            return_value=(0, None, "unknown-past-cap")):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=reqp, state={},
                                   sleep_fn=lambda s: None)
        self.assertEqual(_typed_goal(tmux), [],
                         "an unknown-past-cap (undeterminable) marker must never be virgin-armed")
        self.assertEqual(goal.load_goal_requests(reqp), {})
        self.assertTrue(any("skip:marker-unknown-past-cap" in ln for ln in logs),
                        "expected an observability line for the undeterminable skip; logs=%r" % (logs,))

    def test_f_subdir_of_declared_window_is_not_virgin_armed(self):
        # #1038-review — a pane cd'd into a SUBDIRECTORY of a declared window
        # (a worktree, a human ad-hoc sub-session) matches by CONTAINMENT
        # (resolve_concurrency source=="role") but is NOT the window itself, so
        # it must NEVER be given an unsolicited /goal — only THE window's own pane.
        subdir = GK_REVIEW_CWD + "/addons/some_module"
        proj = self._proj()
        sid = "sess-subdir"
        _write_marker_transcript(proj, subdir, sid)
        reqp = self._reqp()
        tmux = DeliverGoalFakeTmux([("%9", "claude", subdir, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=_declared_windows(GK_REVIEW_CWD)), \
             m.patch.object(goal, "_default_rearm_fn", side_effect=_short_rearm):
            # sanity: containment DOES classify it as the declared window's mode
            import cli_concurrency as _cc
            self.assertEqual(_cc.resolve_concurrency(subdir)[2], "role")
            self.assertFalse(_cc.is_exact_declared_window(subdir))
            goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                            requests_path=reqp, state={}, sleep_fn=lambda s: None)
        self.assertEqual(_typed_goal(tmux), [],
                         "a subdir of a declared window must never be virgin-armed")
        self.assertEqual(goal.load_goal_requests(reqp), {})

    def test_c_non_declared_pane_is_never_virgin_armed(self):
        proj = self._proj()
        sid = "sess-nondeclared"
        _write_marker_transcript(proj, GK_REVIEW_CWD, sid)
        reqp = self._reqp()
        tmux = DeliverGoalFakeTmux([("%9", "claude", GK_REVIEW_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        # box_windows returns [] → resolve_concurrency source != "role"
        with m.patch.object(cli_fleet, "box_windows", return_value=[]), \
             m.patch.object(goal, "_default_rearm_fn", side_effect=_short_rearm):
            goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                            requests_path=reqp, state={}, sleep_fn=lambda s: None)
        self.assertEqual(_typed_goal(tmux), [],
                         "a non-declared pane must never be virgin-armed")
        self.assertEqual(goal.load_goal_requests(reqp), {})


class TestVirginArmAlwaysOn1038(unittest.TestCase):
    """The declared-window virgin arm must NOT be suppressed by the #1023
    machine-nudge OFF switch: `goal-sweep` (a PRIORITY machine nudge) defaults
    OFF, but a declared managed window is a session-revival surface, so its arm
    keystroke rides an ALWAYS-ON recovery nudge (`goal-arm`)."""

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)

    def _proj(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _reqp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return str(Path(d.name) / "goal-requests.json")

    @staticmethod
    def _only_recovery_on(kind=None, home=None):
        # Simulate every PRIORITY machine nudge OFF, recovery nudges always-on.
        return kind in wd.RECOVERY_NUDGE_KINDS

    def test_declared_window_arms_with_all_machine_nudges_off(self):
        proj = self._proj()
        sid = "sess-virgin-alloff"
        _write_marker_transcript(proj, GK_REVIEW_CWD, sid)
        reqp = self._reqp()
        tmux = DeliverGoalFakeTmux([("%9", "claude", GK_REVIEW_CWD, "111")],
                                   GOAL_IDLE_CAP, model_type=True)
        with m.patch.object(cli_fleet, "box_windows",
                            return_value=_declared_windows(GK_REVIEW_CWD)), \
             m.patch.object(goal, "_default_rearm_fn", side_effect=_short_rearm), \
             m.patch.object(wd, "nudges_enabled",
                            side_effect=self._only_recovery_on):
            logs = goal.goal_sweep(2000, run=tmux, projects_dir=proj,
                                   requests_path=reqp, state={},
                                   sleep_fn=lambda s: None)
        self.assertTrue(
            _typed_goal(tmux),
            "a declared window must arm even with every machine nudge OFF "
            "(goal-arm is an always-on recovery nudge); logs=%r sent=%r"
            % (logs, tmux.sent))


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
