"""#433 item G step 2 — `watchdog/tmux_io.py` split.

The tmux I/O shims + pane keystroke helpers (the only impure part of the
watchdog: socket-recovery + sudo-hosted-pane discovery in
`list_claude_panes`, the `capture_pane`/`pane_in_mode`/`pane_owner` reads, the
`send_continue`/`send_selfcheck`/`send_subagent_nudge` keystroke senders, and
the shared dying-subagent nudge logic) were moved VERBATIM out of
`watchdog/__init__.py` into `watchdog.tmux_io`, then re-exported IN PLACE by a
positional facade import. These tests lock the two invariants that keep every
existing `watchdog.<name>` seam (goal / compact / cross_stream / janitor,
hooks, and every `patch.object(watchdog, ...)` / `patch.object(wd, ...)`
monkeypatch in the suite) working unchanged after the move:

  1. `import watchdog` stays clean in a FRESH subprocess — the module is a
     back-reference module (`import watchdog` at its top + call-time
     `watchdog.<name>(...)` in bodies), so a circular-import regression would
     surface here immediately.
  2. Every moved name is the SAME object at `watchdog.<name>` and at
     `watchdog.tmux_io.<name>` — the C2 re-export contract. A future edit that
     lets a name drift to a private copy breaks `watchdog.<name> is
     watchdog.tmux_io.<name>` and this test fails loudly (a dead
     `patch.object(wd, "capture_pane", ...)`-class seam is exactly this bug).
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.tmux_io as tmux_io  # noqa: E402

# Every name moved into tmux_io.py (18 functions, definition order). Kept
# explicit — a hand-maintained list is the point: if a name is added to or
# dropped from the move, this list is the checklist the reviewer diffs against
# the facade import block in `__init__.py`. No constants/regexes were moved
# (NUDGE_TEXT / WORKING_NUDGE_TEXT stay in `__init__`, imported by tmux_io.py).
MOVED_NAMES = [
    "_default_run",
    "_proc_read",
    "_pane_hosted_claude_pid",
    "_hosted_claude_cwd",
    "_tmux_default_socket_path",
    "_tmux_socket_missing",
    "_tmux_server_pids",
    "_tmux_socket_recover",
    "list_claude_panes",
    "pane_in_mode",
    "capture_pane",
    "pane_owner",
    "_strip_selected",
    "send_continue",
    "send_selfcheck",
    "send_subagent_nudge",
    "_subagent_transcript_unsalvageable",
    "_nudge_dying_subagent",
]


class FreshSubprocessImportIsClean(unittest.TestCase):
    def test_import_watchdog_in_fresh_process(self):
        r = subprocess.run(
            [sys.executable, "-c", "import watchdog; print('ok')"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stdout.strip(), "ok")
        self.assertEqual(r.stderr.strip(), "")

    def test_import_tmux_io_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly (parent-package init runs first, binding NUDGE_TEXT /
        # WORKING_NUDGE_TEXT above the facade-import position).
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.tmux_io as t; print(t._tmux_default_socket_path())"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")
        self.assertTrue(r.stdout.strip().endswith("/default"), r.stdout)


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(tmux_io, name),
                                f"{name} missing from watchdog.tmux_io")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(tmux_io, name),
                              f"watchdog.{name} is not watchdog.tmux_io.{name}")

    def test_tmux_io_lives_in_its_own_module_file(self):
        self.assertTrue(tmux_io.__file__.endswith("watchdog/tmux_io.py"),
                        tmux_io.__file__)

    def test_nudge_text_constants_stayed_in_init_and_resolve_from_tmux_io(self):
        # NUDGE_TEXT / WORKING_NUDGE_TEXT were NOT moved (they belong to a later
        # step); tmux_io.py imports them from the package at module top.
        # NUDGE_TEXT is send_continue's def-time default, so the from-import is
        # load-bearing — both must see one object.
        self.assertIs(tmux_io.NUDGE_TEXT, watchdog.NUDGE_TEXT)
        self.assertIs(tmux_io.WORKING_NUDGE_TEXT, watchdog.WORKING_NUDGE_TEXT)
        self.assertEqual(watchdog.NUDGE_TEXT, "continue")

    def test_back_reference_seams_use_the_package_namespace(self):
        # The three heaviest monkeypatch seams (capture_pane / pane_in_mode /
        # send_continue) plus the C5-patched _default_run / sudo-host helpers are
        # called through the package namespace inside tmux_io.py, so a
        # patch.object(wd, "<name>", ...) seam stays effective. Guard the exact
        # bypass the move could reintroduce: a bare intra-module call to a
        # patched name.
        src = Path(tmux_io.__file__).read_text()
        for name in ("_default_run", "capture_pane", "pane_in_mode",
                     "send_continue", "_pane_hosted_claude_pid",
                     "_hosted_claude_cwd"):
            with self.subTest(name=name):
                self.assertIn(f"watchdog.{name}", src,
                              f"{name} must be reached via the package namespace")


if __name__ == "__main__":
    unittest.main()
