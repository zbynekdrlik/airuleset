"""Sudo-hosted claude panes (montalu-in-newlevel-tmux) — watchdog visibility.

2026-07-20 evening: montalu's /goal was never auto-armed — montalu's claude
runs INSIDE newlevel's tmux via `sudo su - montalu`, so montalu's own watchdog
cannot see the pane (foreign tmux) and newlevel's watchdog SKIPPED it because
pane_current_command is 'sudo', not 'claude'. Auto-arm for the montalu stream
was structurally impossible; the user had to arm by hand. Fix locked here:
list_claude_panes includes a sudo/su pane whose process tree hosts a claude,
reporting the hosted claude's REAL cwd (`_pane_hosted_claude_pid`/
`_hosted_claude_cwd`) — this half is generic pane-discovery infra, untouched
by #403.

The OLD second half — `goal_autoarm` reading the FOREIGN user's transcript
(sudo -n, via `_foreign_transcript_goal`) to reconstruct a `/goal` line the
pane's own viewport showed only WRAPPED/truncated — is REMOVED by #403,
along with both functions: the new callback-model arm delivery
(`watchdog/goal.py`'s `goal_sweep`/`deliver_goal`) never reads its payload
from the pane's viewport at all — the exact text is resolved once, at
`goal-arm --self` CLI time (`goal_template_for_authority`), and delivered
from the persisted `goal-requests.json` store. A wrapped/truncated fragment
in the viewport is therefore structurally impossible to arm FROM any more,
for a sudo-hosted pane or a plain one alike — there is nothing left for a
foreign-transcript reconstruction to recover.
"""

import sys
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


# TestForeignTranscriptGoal (`goal_autoarm` + `_foreign_transcript_goal`)
# REMOVED by #403 -- both functions are deleted wholesale (design comment
# item 8). The scenario it locked (a wrapped/truncated `/goal` fragment
# visible in a sudo-hosted pane's viewport, recovered via a sudo -n read of
# the foreign user's own transcript) cannot occur any more for ANY pane,
# hosted or not: the new callback-model arm delivery never reads its
# payload from a pane's viewport at all -- see the module docstring above.


if __name__ == "__main__":
    unittest.main()
