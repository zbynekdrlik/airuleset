"""Sudo-hosted claude panes (montalu-in-newlevel-tmux) — watchdog visibility.

2026-07-20 evening: montalu's /goal was never auto-armed — montalu's claude
runs INSIDE newlevel's tmux via `sudo su - montalu`, so montalu's own watchdog
cannot see the pane (foreign tmux) and newlevel's watchdog SKIPPED it because
pane_current_command is 'sudo', not 'claude'. Auto-arm for the montalu stream
was structurally impossible; the user had to arm by hand. Fixes locked here:
list_claude_panes includes a sudo/su pane whose process tree hosts a claude;
goal_autoarm reads the FOREIGN user's transcript (sudo -n) for the full goal
bytes when the local projects dir has none.
"""

import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd


class FakeTmux:
    def __init__(self, panes_line, captured=""):
        self.panes_line = panes_line
        self.captured = captured
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            return self.panes_line
        if "capture-pane" in j:
            return self.captured
        if "display" in j:
            return "0"
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class TestSudoPaneVisibility(unittest.TestCase):
    SUDO_LINE = "%7\tsudo\t/home/newlevel/devel/odoo\t8901"
    CLAUDE_LINE = "%1\tclaude\t/home/x/devel/demo\t4321"

    def test_sudo_pane_hosting_claude_is_listed_with_real_cwd(self):
        # tmux reports the SUDO root's cwd (/home/newlevel/...) — the entry
        # must carry the hosted claude's REAL cwd instead
        with m.patch.object(wd, "_pane_hosted_claude_pid", return_value="999"), \
             m.patch.object(wd, "_hosted_claude_cwd",
                            return_value="/home/montalu/devel/odoo") as hc:
            res = wd.list_claude_panes(FakeTmux(self.SUDO_LINE))
        self.assertEqual(res, [("%7", "/home/montalu/devel/odoo")])
        hc.assert_called_once_with("999", "/home/newlevel/devel/odoo")

    def test_sudo_pane_without_claude_is_skipped(self):
        with m.patch.object(wd, "_pane_hosted_claude_pid", return_value=None):
            res = wd.list_claude_panes(FakeTmux(self.SUDO_LINE))
        self.assertEqual(res, [])

    def test_plain_claude_pane_unchanged(self):
        res = wd.list_claude_panes(FakeTmux(self.CLAUDE_LINE))
        self.assertEqual(res, [("%1", "/home/x/devel/demo")])


class TestForeignTranscriptGoal(unittest.TestCase):
    FULL_GOAL = ("/goal STOP CONDITIONS — the loop is DONE ... montalu backlog "
                 "empty AND main green ... never park silently.")
    FRAG = "/goal STOP CONDITIONS — the loop is DONE ... montalu"
    WRAPPED_PANE = (
        "● autopilot pripravený.\n"
        + FRAG + "\n"
        "  backlog empty AND main green ... continuation line\n"
        "**Otázka — projekt odoo (Money→Odoo):** autopilot pripravený.\n"
        "• Vlož /goal riadok vyššie (odporúčam) — loop sa rozbehne\n"
        "❓ NEEDS YOU: vlož /goal riadok vyššie a autopilot sa rozbehne\n"
        "❯ \n  ctx ███░  caveman\n")

    def test_wrapped_goal_on_foreign_cwd_arms_from_sudo_transcript(self):
        tmux = FakeTmux("%7\tsudo\t/home/newlevel/devel/odoo\t8901",
                        self.WRAPPED_PANE)
        with m.patch.object(wd, "_pane_hosted_claude_pid", return_value="999"), \
             m.patch.object(wd, "_hosted_claude_cwd",
                            return_value="/home/montalu/devel/odoo"), \
             m.patch.object(wd, "_foreign_transcript_goal",
                            return_value=self.FULL_GOAL) as fg:
            wd.goal_autoarm(time.time(), tmux, {},
                            projects_dir="/nonexistent-projects")
        self.assertEqual(tmux.typed(), [self.FULL_GOAL])
        fg.assert_called_once_with("/home/montalu/devel/odoo")

    def test_wrapped_goal_foreign_lookup_fails_never_arms_fragment(self):
        tmux = FakeTmux("%7\tsudo\t/home/newlevel/devel/odoo\t8901",
                        self.WRAPPED_PANE)
        with m.patch.object(wd, "_pane_hosted_claude_pid", return_value="999"), \
             m.patch.object(wd, "_hosted_claude_cwd",
                            return_value="/home/montalu/devel/odoo"), \
             m.patch.object(wd, "_foreign_transcript_goal", return_value=None):
            logs = wd.goal_autoarm(time.time(), tmux, {},
                                   projects_dir="/nonexistent-projects")
        self.assertFalse(tmux.typed())
        self.assertTrue(any("wrap" in ln.lower() for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
