"""#804 mode-5 -- direct unit coverage for the PURE resurrect primitives
(`watchdog/resurrect.py`). The goal_lane_sweep integration tests exercise only
two `decide()` branches (mode-5 disabled + mode-4 veto); these lock every branch
+ the cadence / launch-stage / pane-find primitives so a mutant in any single
one is caught here rather than surviving a green integration suite."""
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import resurrect  # noqa: E402


class TestResurrectDue(unittest.TestCase):
    def test_never_attempted_is_due_now(self):
        due, wait = resurrect.due({}, 1000)
        self.assertTrue(due)
        self.assertIsNone(wait)

    def test_within_cadence_window_defers_with_countdown(self):
        entry = {"rgts": 1000}
        due, wait = resurrect.due(entry, 1000 + 600)   # 10 min into 30-min window
        self.assertFalse(due)
        self.assertEqual(wait, resurrect.RESURRECT_CADENCE_S - 600)

    def test_past_cadence_window_is_due(self):
        entry = {"rgts": 1000}
        due, _ = resurrect.due(entry, 1000 + resurrect.RESURRECT_CADENCE_S + 1)
        self.assertTrue(due)

    def test_non_dict_entry_never_due(self):
        due, wait = resurrect.due("nope", 1000)
        self.assertFalse(due)
        self.assertIsNone(wait)


class TestResurrectLaunchCmd(unittest.TestCase):
    def test_no_fails_uses_continue(self):
        self.assertEqual(resurrect.launch_cmd({}), "claude --continue")

    def test_below_max_fails_still_continue(self):
        self.assertEqual(
            resurrect.launch_cmd({"rfails": resurrect.RESURRECT_MAX_FAILS - 1}),
            "claude --continue")

    def test_at_max_fails_falls_back_to_fresh_claude(self):
        # #805 interface: a ballooned-context session that dies on --continue
        # must not livelock; after RESURRECT_MAX_FAILS it starts fresh.
        self.assertEqual(
            resurrect.launch_cmd({"rfails": resurrect.RESURRECT_MAX_FAILS}),
            "claude")

    def test_non_dict_entry_uses_continue(self):
        self.assertEqual(resurrect.launch_cmd("nope"), "claude --continue")


class TestResurrectActionEnabled(unittest.TestCase):
    def _enabled_with(self, value):
        env = {} if value is None else {"AIRULESET_RESURRECT_ACTION": value}
        with m.patch.dict(os.environ, env, clear=False):
            if value is None:
                os.environ.pop("AIRULESET_RESURRECT_ACTION", None)
            return resurrect.action_enabled()

    def test_default_off(self):
        self.assertFalse(self._enabled_with(None))

    def test_empty_off(self):
        self.assertFalse(self._enabled_with(""))

    def test_falsey_words_off(self):
        for v in ("0", "false", "no", "off", "nope"):
            self.assertFalse(self._enabled_with(v), v)

    def test_truthy_words_on(self):
        for v in ("1", "true", "yes", "on", "TRUE", " On "):
            self.assertTrue(self._enabled_with(v), v)


class _Run:
    """A minimal fake `run` that answers `list-panes -a` from a given pane list
    and records send-keys argv."""
    def __init__(self, panes):
        self.panes = panes          # (pane_id, cmd, cwd) triples
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "\n".join("%s\t%s\t%s" % t for t in self.panes)
        if "send-keys" in j:
            self.sent.append(argv)
        return ""


class TestResurrectFindPane(unittest.TestCase):
    CWD = "/home/newlevel/devel/deadstream"

    def test_bare_shell_exact_cwd_matches(self):
        run = _Run([("%bash", "bash", self.CWD)])
        self.assertEqual(resurrect.find_pane(self.CWD, run), "%bash")

    def test_login_shell_dash_prefix_matches(self):
        run = _Run([("%x", "-bash", self.CWD)])
        self.assertEqual(resurrect.find_pane(self.CWD, run), "%x")

    def test_non_shell_foreground_command_is_not_a_target(self):
        # a human's process running in the pane -> never a relaunch target.
        run = _Run([("%v", "vim", self.CWD)])
        self.assertIsNone(resurrect.find_pane(self.CWD, run))

    def test_wrong_cwd_does_not_match(self):
        run = _Run([("%bash", "bash", "/somewhere/else")])
        self.assertIsNone(resurrect.find_pane(self.CWD, run))

    def test_none_run_or_cwd_is_none(self):
        self.assertIsNone(resurrect.find_pane(self.CWD, None))
        self.assertIsNone(resurrect.find_pane("", _Run([])))

    def test_first_match_wins(self):
        run = _Run([("%a", "bash", self.CWD), ("%b", "bash", self.CWD)])
        self.assertEqual(resurrect.find_pane(self.CWD, run), "%a")


class TestResurrectDecide(unittest.TestCase):
    LOC = "deadstream"

    def _decide(self, pane="%bash", human=False, reason="", enabled=True,
                dry_run=False, entry=None):
        return resurrect.decide(entry or {}, self.LOC, pane, human, reason,
                                enabled, dry_run)

    def test_no_pane_skips_no_relaunch_pane_and_does_not_act(self):
        log, act = self._decide(pane=None)
        self.assertFalse(act)
        self.assertIn("resurrect deadstream -> skip:no-relaunch-pane", log)

    def test_recent_human_hard_veto_wins_over_enabled(self):
        log, act = self._decide(human=True, reason="client input 12s",
                                enabled=True)
        self.assertFalse(act)
        self.assertIn("skip:recent-human HARD veto", log)
        self.assertIn("client input 12s", log)

    def test_disabled_flag_would_relaunch_no_act(self):
        log, act = self._decide(enabled=False)
        self.assertFalse(act)
        self.assertIn("would relaunch", log)
        self.assertIn("disabled", log)

    def test_dry_run_would_relaunch_no_act(self):
        log, act = self._decide(enabled=True, dry_run=True)
        self.assertFalse(act)
        self.assertIn("would relaunch", log)
        self.assertIn("dry-run", log)

    def test_enabled_live_no_human_acts_and_is_unconfirmed(self):
        log, act = self._decide(enabled=True, dry_run=False)
        self.assertTrue(act)
        self.assertIn("relaunching", log)
        self.assertIn("delivered_unconfirmed", log)
        # the staged launch command is named in the line.
        self.assertIn("claude --continue", log)

    def test_act_line_reflects_the_805_fresh_fallback(self):
        log, act = self._decide(enabled=True,
                                entry={"rfails": resurrect.RESURRECT_MAX_FAILS})
        self.assertTrue(act)
        self.assertIn("relaunching (claude;", log)   # fresh claude, not --continue


class TestResurrectRelaunch(unittest.TestCase):
    def test_fires_send_keys_with_cmd_and_enter(self):
        run = _Run([])
        ok = resurrect.relaunch("%bash", "claude --continue", run)
        self.assertTrue(ok)
        self.assertEqual(
            run.sent,
            [["tmux", "send-keys", "-t", "%bash", "claude --continue", "Enter"]])

    def test_missing_pane_cmd_or_run_is_a_noop_false(self):
        run = _Run([])
        self.assertFalse(resurrect.relaunch(None, "claude", run))
        self.assertFalse(resurrect.relaunch("%bash", "", run))
        self.assertFalse(resurrect.relaunch("%bash", "claude", None))
        self.assertEqual(run.sent, [])

    def test_raising_run_is_swallowed_false(self):
        def boom(*a, **k):
            raise RuntimeError("tmux gone")
        self.assertFalse(resurrect.relaunch("%bash", "claude", boom))


if __name__ == "__main__":
    unittest.main()
