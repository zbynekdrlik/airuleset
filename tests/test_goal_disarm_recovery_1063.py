"""#1063 — the `/goal clear` question-repoke disarm (#522, Job 33) is a RECOVERY
nudge, exempt from the #1023 per-kind kill switch / per-kind floor / total cap.

Root cause the fix removes: `_deliver_goal_clear` delivered `/goal clear` through
`_send_goal_verified`'s DEFAULT `nudge="goal-sweep"` — a MACHINE nudge kind that
the #1023 per-kind staging has OFF on every box (dev1/dev2 0/13). So the #522
backstop could not fire ANYWHERE since 2026-09-14, and the camera-box re-poke
storm ran 07:00–09:45 CEST until the owner typed `/goal clear` by hand.

RED against the pre-#1063 tree:
  * `TestDisarmDeliversWithMachineKindsOff` — with the real per-kind predicate
    live (bypass popped) and EVERY machine kind OFF, a 5-streak re-poke leaves
    `/goal clear` UN-typed and journals `nudges OFF: suppressed goal-sweep ...`.
  * `TestRecoveryPathNudgeGuard` — `_deliver_goal_clear` threads NO explicit
    recovery `nudge=`, so the static guard's non-empty check fails.
  * `TestDisarmJournalHonesty` — a suppressed disarm is journalled as the
    misleading `disarm delivery FAILED (skip:verify-failed)`.

GREEN once `goal-disarm` joins `RECOVERY_NUDGE_KINDS` (tmux_io + nudge_gate
mirror), `_deliver_goal_clear` sends `nudge="goal-disarm"`, and the failure
branch journals the honest `disarm suppressed: nudges OFF for kind goal-disarm`.
"""

import ast
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
from watchdog import goal, nudge_gate  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    GOAL_ARMED_CAP,
    DeliverGoalFakeTmux,
    _isolate_goal_state,
)
from test_goal_question_repoke import _repoke_entries, _write_entries  # noqa: E402


def _redirect_home(testcase):
    """Redirect `~` to a fresh empty temp HOME for the duration of the test, so
    every `~/.claude` read/write in the code under test (the #1023 nudges-kinds
    state, `_owner_disabled`'s watchdog-disable-goal flag, any log) is hermetic
    and never touches — or reads — the real box (#1028 lane guidance: an earlier
    lane wrote into the real design-by-gate.log). Returns the temp home path."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    home = d.name
    exp = m.patch("os.path.expanduser",
                  side_effect=lambda p: p.replace("~", home, 1))
    exp.start()
    testcase.addCleanup(exp.stop)
    return home


# --------------------------------------------------------------------------- #
# Item 1/2 — the disarm is a RECOVERY nudge and delivers with every machine
# kind OFF (the REAL per-kind predicate live, total cap closed).
# --------------------------------------------------------------------------- #
class TestDisarmDeliversWithMachineKindsOff(unittest.TestCase):
    CWD = "/home/newlevel/devel/qrepoke"

    def setUp(self):
        _isolate_goal_state(self)

    def _hermetic_home(self):
        """A fresh empty HOME so `nudges_enabled`/`_owner_disabled` read a box
        with NO staged machine kinds and NO owner-disable flag — the #1023
        all-OFF default. Redirect `~` there and POP the conftest bypass so the
        REAL per-kind predicate runs (mirrors test_nudges_per_kind_switch_1023's
        expanduser+_no_bypass pattern; hermetic per the #1028 lane guidance)."""
        home = _redirect_home(self)
        envp = m.patch.dict(os.environ)
        envp.start()
        self.addCleanup(envp.stop)
        os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
        return home

    def _armed_tmux(self, tpath):
        return DeliverGoalFakeTmux(
            [("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP,
            model_type=True, transcript_path=str(tpath))

    def test_five_streak_disarms_even_with_every_machine_kind_off(self):
        home = self._hermetic_home()
        # sanity: the real predicate reads all machine kinds OFF here.
        self.assertEqual(wd.nudges_on_kinds(home), set())
        self.assertFalse(wd.nudges_enabled("goal-sweep", home=home))
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        sid = "sess-disarm-machineoff"
        tpath = _write_entries(Path(proj.name), self.CWD, sid, _repoke_entries(5))
        tmux = self._armed_tmux(tpath)
        # A priority nudge marked 1 min ago -> the cross-kind total cap is CLOSED
        # (belt-and-suspenders: the disarm is exempt from it as a recovery kind;
        # the #522 path does not consult gate_ok today, so this cannot mask a
        # regression that later routes it through the cadence gate).
        now = 100000.0
        state = {"nudge_cadence": {sid: {"queue-arrival": now - 60}}}
        logs = goal.goal_question_repoke_watch(
            now, run=tmux, state=state, projects_dir=Path(proj.name),
            sleep_fn=lambda s: None, human_ts_fn=lambda tp: None)
        self.assertIn("/goal clear", tmux.typed_texts(),
                      "the #522 disarm must fire even with every machine kind "
                      "OFF; logs=%r" % logs)
        self.assertIn(sid, state.get("goal_disarmed_q", {}),
                      "a landed disarm must write the goal_disarmed_q veto")
        self.assertFalse(
            any("nudges OFF: suppressed" in ln and "goal-disarm" in ln
                for ln in logs),
            "no kill-switch suppression line may name goal-disarm; logs=%r" % logs)
        self.assertFalse(
            any("nudges OFF: suppressed" in ln for ln in logs),
            "the recovery disarm must not be kill-switch suppressed at all; "
            "logs=%r" % logs)
        self.assertTrue(any("DISARMED" in ln for ln in logs), logs)

    def test_goal_disarm_is_a_recovery_kind_in_both_modules(self):
        self.assertIn("goal-disarm", wd.RECOVERY_NUDGE_KINDS)
        self.assertIn("goal-disarm", nudge_gate.RECOVERY_NUDGE_KINDS)
        self.assertIn("goal-disarm", wd.ALL_NUDGE_KINDS)
        self.assertNotIn("goal-disarm", wd.MACHINE_NUDGE_KINDS)
        # drift-lock: the two sets stay identical (the #1023/#1038 mirror rule).
        self.assertEqual(wd.RECOVERY_NUDGE_KINDS, nudge_gate.RECOVERY_NUDGE_KINDS)

    def test_recovery_predicate_is_always_on_for_goal_disarm(self):
        home = self._hermetic_home()
        # even with the file absent (all machine kinds off) the recovery kind
        # short-circuits to always-on, never reading the staged set.
        self.assertTrue(wd.nudges_enabled("goal-disarm", home=home))
        self.assertTrue(nudge_gate.gate_ok({}, "any-sid", "goal-disarm", 100.0))


# --------------------------------------------------------------------------- #
# Item 3 — static guard: each PINNED recovery-path delivery threads a `nudge=`
# that resolves to a RECOVERY_NUDGE_KINDS member, so a future recovery action
# cannot be silently staged off again (the exact bug: relying on the machine
# default). KNOWN LIMITATION (both #1063 reviewers): this is a CURATED allowlist,
# not an exhaustive scan — the AST cannot infer "this function is a recovery
# delivery", so a NEW recovery helper that relies on a machine default is caught
# ONLY once it is added below. Pin every new recovery-delivery helper here at
# creation. Renaming/removing a pinned function is still caught (assertIsNotNone).
# --------------------------------------------------------------------------- #
# The PINNED recovery-delivery call-site list (design item 3): file -> the
# functions whose keystroke/send delivery is a session revival, never a prompt.
RECOVERY_DELIVERY_SITES = {
    "watchdog/goal.py": ["_deliver_goal_clear"],
    "watchdog/compact.py": ["_compact_submit_verified"],
    "watchdog/__init__.py": ["_send_stuckcheck_verified"],
    "watchdog/parked_wake.py": ["deliver_wake"],
}


def _module_str_constants(tree):
    """Top-level `NAME = "literal"` string constants (e.g. WAKE_PARKED_NUDGE),
    for resolving a `nudge=NAME` reference to its value."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _find_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def _func_param_nudge_default(fn):
    """The string default of a `nudge` parameter, if any (a signature default
    is real nudge evidence — the kind actually delivered when the caller omits
    it)."""
    args = fn.args
    # positional-or-keyword defaults align to the tail of args.args
    for arg, default in zip(reversed(args.args),
                            reversed(args.defaults or [])):
        if arg.arg == "nudge" and isinstance(default, ast.Constant) \
                and isinstance(default.value, str):
            return default.value
    for arg, default in zip(args.kwonlyargs, args.kw_defaults or []):
        if arg.arg == "nudge" and isinstance(default, ast.Constant) \
                and isinstance(default.value, str):
            return default.value
    return None


def _recovery_kinds_threaded(fn, mod_consts):
    """The set of nudge kinds `fn` delivers with, resolvable statically, plus
    the set of `nudge=` references it could NOT resolve (a bare local var)."""
    kinds, unresolved = set(), set()
    param_default = _func_param_nudge_default(fn)
    if param_default is not None:
        kinds.add(param_default)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "nudge":
                continue
            v = kw.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                kinds.add(v.value)
            elif isinstance(v, ast.Name):
                if v.id == "nudge" and param_default is not None:
                    kinds.add(param_default)      # the function's own param
                elif v.id in mod_consts:
                    kinds.add(mod_consts[v.id])    # module const (WAKE_PARKED_NUDGE)
                else:
                    unresolved.add(v.id)
    return kinds, unresolved


class TestRecoveryPathNudgeGuard(unittest.TestCase):
    def test_every_pinned_recovery_delivery_threads_a_recovery_kind(self):
        for rel, funcs in RECOVERY_DELIVERY_SITES.items():
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
            mod_consts = _module_str_constants(tree)
            for name in funcs:
                fn = _find_func(tree, name)
                self.assertIsNotNone(fn, "%s: pinned recovery function %s not "
                                     "found" % (rel, name))
                kinds, unresolved = _recovery_kinds_threaded(fn, mod_consts)
                self.assertFalse(
                    unresolved,
                    "%s.%s threads an unresolvable nudge=%r — pin it to a "
                    "literal or a module constant" % (rel, name, unresolved))
                self.assertTrue(
                    kinds,
                    "%s.%s delivers with NO explicit recovery nudge= (it relies "
                    "on a machine default — the #1063 bug). Thread an explicit "
                    "RECOVERY_NUDGE_KINDS kind." % (rel, name))
                self.assertTrue(
                    kinds <= set(wd.RECOVERY_NUDGE_KINDS),
                    "%s.%s threads non-recovery nudge kind(s) %r — a recovery "
                    "delivery must never be staged off (#1063)"
                    % (rel, name, kinds - set(wd.RECOVERY_NUDGE_KINDS)))

    def test_deliver_goal_clear_explicitly_names_goal_disarm(self):
        tree = ast.parse((REPO / "watchdog/goal.py").read_text(encoding="utf-8"))
        fn = _find_func(tree, "_deliver_goal_clear")
        kinds, _ = _recovery_kinds_threaded(fn, _module_str_constants(tree))
        self.assertEqual(kinds, {"goal-disarm"},
                         "_deliver_goal_clear must deliver with nudge='goal-disarm'")


# --------------------------------------------------------------------------- #
# Item 4 — journal honesty: a disarm SUPPRESSED by the kill switch must be
# journalled as the honest `disarm suppressed: nudges OFF for kind <k>`, not the
# misleading `disarm delivery FAILED (skip:verify-failed)`.
# --------------------------------------------------------------------------- #
class TestDisarmJournalHonesty(unittest.TestCase):
    CWD = "/home/newlevel/devel/qrepoke"

    def setUp(self):
        _isolate_goal_state(self)
        # Hermetic HOME so `_owner_disabled`'s ~/.claude/watchdog-disable-goal
        # read can never disable the job from the real box's state, and nothing
        # in the sweep touches the real ~/.claude (#1028 lane guidance).
        _redirect_home(self)

    def _armed_tmux(self, tpath):
        return DeliverGoalFakeTmux(
            [("%9", "claude", self.CWD, "111")], GOAL_ARMED_CAP,
            model_type=True, transcript_path=str(tpath))

    def test_suppressed_disarm_is_journalled_honestly(self):
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        sid = "sess-disarm-honest"
        tpath = _write_entries(Path(proj.name), self.CWD, sid, _repoke_entries(5))
        tmux = self._armed_tmux(tpath)
        # Force the kill switch to suppress goal-disarm (a hypothetical future
        # mis-staging) so the failure branch runs and must journal honestly.
        with m.patch.object(wd, "nudges_enabled",
                            side_effect=lambda kind=None, home=None: False):
            logs = goal.goal_question_repoke_watch(
                100000.0, run=tmux, state={}, projects_dir=Path(proj.name),
                sleep_fn=lambda s: None, human_ts_fn=lambda tp: None)
        self.assertNotIn("/goal clear", tmux.typed_texts())
        self.assertTrue(
            any("disarm suppressed: nudges OFF for kind goal-disarm" in ln
                for ln in logs),
            "a kill-switch-suppressed disarm must journal the honest reason; "
            "logs=%r" % logs)
        self.assertFalse(
            any("disarm delivery FAILED (skip:verify-failed)" in ln
                for ln in logs),
            "the misleading skip:verify-failed line must NOT be used when the "
            "real cause is the kill switch; logs=%r" % logs)

    def test_genuine_verify_failure_still_reads_FAILED(self):
        # When the disarm IS enabled (the real world) but the keystroke genuinely
        # fails to verify, the honest branch must NOT fire — it stays the generic
        # FAILED line (a real verify failure is not a kill-switch suppression).
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        sid = "sess-disarm-realfail"
        tpath = _write_entries(Path(proj.name), self.CWD, sid, _repoke_entries(5))
        tmux = self._armed_tmux(tpath)
        with m.patch.object(wd, "nudges_enabled",
                            side_effect=lambda kind=None, home=None: True), \
                m.patch.object(goal, "_deliver_goal_clear",
                               return_value="skip:verify-failed"):
            logs = goal.goal_question_repoke_watch(
                100000.0, run=tmux, state={}, projects_dir=Path(proj.name),
                sleep_fn=lambda s: None, human_ts_fn=lambda tp: None)
        self.assertTrue(
            any("disarm delivery FAILED (skip:verify-failed)" in ln
                for ln in logs),
            "a genuine verify failure keeps the generic FAILED line; logs=%r"
            % logs)
        self.assertFalse(
            any("disarm suppressed: nudges OFF" in ln for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
