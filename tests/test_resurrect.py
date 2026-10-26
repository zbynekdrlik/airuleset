"""`watchdog/resurrect.py` keeps only the bare-shell predicate
`pane_is_bare_idle` (the credential-dead relaunch reads it: a pane at a bare
shell is a session the owner stopped, never touched). The issue 804 mode-5
relaunch action it used to serve was deleted with the issue 1257 owner-stop
latch (B9 fix round): every relaunchable pane is a bare shell, which latches.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import resurrect  # noqa: E402


class _Run:
    """A fake `run` answering `capture-pane` with a fixed capture string."""
    def __init__(self, capture=""):
        self.capture = capture

    def __call__(self, argv, timeout=8):
        return self.capture if "capture-pane" in " ".join(argv) else ""


class TestResurrectPaneIsBareIdle(unittest.TestCase):
    def _bare(self, capture):
        return resurrect.pane_is_bare_idle("%p", _Run(capture))

    def test_bare_bash_prompt_is_idle(self):
        self.assertTrue(self._bare("out line\nnewlevel@dev1:~/devel/x$ "))

    def test_themed_prompt_is_idle(self):
        self.assertTrue(self._bare("some output\n❯ "))

    def test_typed_stale_command_is_not_bare(self):
        # the owner's hardest hole: a stale half-typed command -> `claude
        # --continue` would be APPENDED and executed.
        self.assertFalse(self._bare("newlevel@dev1:~/x$ rm -rf /tmp/junk "))

    def test_running_process_output_is_not_bare(self):
        self.assertFalse(self._bare("Downloading package 5 of 12 ... 45%"))

    def test_read_style_prompt_without_terminator_is_not_bare(self):
        self.assertFalse(self._bare("Enter your name: "))

    def test_ps2_continuation_gt_is_not_bare(self):
        # `>` is deliberately not a terminator (PS2 / redirect).
        self.assertFalse(self._bare("> "))

    def test_empty_or_missing_capture_is_not_bare(self):
        self.assertFalse(self._bare(""))
        self.assertFalse(resurrect.pane_is_bare_idle("%p", None))
        self.assertFalse(resurrect.pane_is_bare_idle("", _Run()))

    def test_raising_run_is_not_bare(self):
        def boom(*a, **k):
            raise RuntimeError("tmux gone")
        self.assertFalse(resurrect.pane_is_bare_idle("%p", boom))


if __name__ == "__main__":
    unittest.main()
