"""#433 item G step 3 — `watchdog/pane_text.py` split.

The six pure captured-text scanners of the input box + footer region
(`_input_line_text`, `_classify_boundary`, `_above_input_box`,
`_above_box_scan`, `_pane_has_queued_compact`, `_trailing_bottom_chrome`) were
moved VERBATIM out of `watchdog/__init__.py` into `watchdog.pane_text`, then
re-exported IN PLACE by a positional facade import at the earliest removed
block's position. Unlike `transcripts.py`/`pane_classify.py` (leaves), this is
a BACK-REFERENCE module: the bodies call `__init__`-resident primitives
(`_find_input_box`, `_input_box_rows_raw`, `_find_input_box_from`,
`_is_bottom_chrome`, `_is_separator_line`) + the constant `_QUEUED_COMPACT_RX`,
plus two intra-module co-moved cross-calls, ALL written as call-time
`watchdog.<name>` (module top: `import watchdog`).

These tests lock the invariants that keep every existing `watchdog.<name>` seam
(stash / goal / job 1 / compact, hooks, and every monkeypatch in the suite)
working unchanged after the move:

  1. `import watchdog` stays clean in a FRESH subprocess, AND importing the
     `watchdog.pane_text` submodule FIRST initializes the package cleanly — the
     one circular-import surface (`import watchdog` inside a submodule imported
     by `__init__`'s own facade) resolves because every `watchdog.<name>`
     access is call-time, never at pane_text import time.
  2. Every moved name is the SAME object at `watchdog.<name>` and at
     `watchdog.pane_text.<name>` — the C2 re-export contract. A future edit that
     lets a name drift to a private copy breaks `is` and fails loudly (a dead
     `monkeypatch.setattr(watchdog, "<name>", ...)` seam is exactly this bug).
  3. The back-references actually RESOLVE at call time: a functional call
     through the co-moved cross-call chain + a `__init__`-resident helper would
     NameError the instant a `watchdog.` prefix were reverted to a bare name
     (the design's #1 hazard — a silent monkeypatch-seam bypass).
"""

import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.pane_text as pane_text  # noqa: E402

# The six functions moved into pane_text.py, in definition order. Kept
# explicit — a hand-maintained checklist the reviewer diffs against the facade
# import block in `__init__.py`.
MOVED_NAMES = [
    "_input_line_text",
    "_classify_boundary",
    "_above_input_box",
    "_above_box_scan",
    "_pane_has_queued_compact",
    "_trailing_bottom_chrome",
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

    def test_import_pane_text_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly — pane_text's `import watchdog` binds the partially-initialized
        # package object, and every `watchdog.<name>` access is call-time, so
        # no attribute is touched during import.
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.pane_text as p; print(p._trailing_bottom_chrome([]))"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(pane_text, name),
                                f"{name} missing from watchdog.pane_text")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(pane_text, name),
                              f"watchdog.{name} is not watchdog.pane_text.{name}")

    def test_pane_text_lives_in_its_own_module_file(self):
        self.assertTrue(pane_text.__file__.endswith("watchdog/pane_text.py"),
                        pane_text.__file__)

    def test_moved_functions_report_pane_text_as_their_module(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertEqual(getattr(watchdog, name).__module__,
                                 "watchdog.pane_text")


class BackReferencesResolveAtCallTime(unittest.TestCase):
    """A functional call through each back-reference class. Any of these would
    NameError if a `watchdog.` prefix in a moved body were reverted to a bare
    name — the C3 seam-preservation contract has teeth here, not just at the
    identity level."""

    def test_input_line_text_reaches_find_input_box(self):
        # bare box -> "" (a real, `❯`-shaped boundary with no draft)
        self.assertEqual(watchdog._input_line_text("hello\n❯ "), "")

    def test_classify_boundary_reaches_input_box_rows_raw(self):
        self.assertEqual(watchdog._classify_boundary("hello\n❯ "), ("input", ""))

    def test_above_input_box_reaches_is_bottom_chrome(self):
        self.assertEqual(watchdog._above_input_box("hello\n❯ "), "hello")

    def test_above_box_scan_chains_above_input_box_and_is_separator_line(self):
        queued, spinner = watchdog._above_box_scan("❯ queued cmd\n❯ ")
        self.assertEqual(queued, ["❯ queued cmd"])
        self.assertIsNone(spinner)

    def test_pane_has_queued_compact_chains_scan_and_queued_compact_rx(self):
        self.assertTrue(watchdog._pane_has_queued_compact("❯ /compact\n❯ "))
        self.assertFalse(watchdog._pane_has_queued_compact("❯ not a compact\n❯ "))

    def test_trailing_bottom_chrome_reaches_is_bottom_chrome(self):
        # a non-chrome tail line breaks the walk immediately -> empty run
        self.assertEqual(watchdog._trailing_bottom_chrome(["just some draft text"]), [])


class CoMovedCrossCallsGoThroughPackageSeam(unittest.TestCase):
    """The two INTRA-module co-moved cross-calls (`_above_box_scan` ->
    `_above_input_box`, `_pane_has_queued_compact` -> `_above_box_scan`) are
    written `watchdog.<name>`, NOT bare — so a `patch.object(watchdog, ...)`
    seam still reaches the caller after the move (C3). A bare name would
    resolve to the module-local co-moved function and pass every FUNCTIONAL
    test above while SILENTLY bypassing the patch (the design's #1 hazard) —
    only a patch-observing test has teeth here; these are those tests."""

    def test_pane_has_queued_compact_calls_above_box_scan_via_watchdog(self):
        # Patch the package seam; the caller must observe it. With a bare
        # intra-module call it would use the real (unpatched) scan and return
        # False for this non-compact input.
        with mock.patch.object(watchdog, "_above_box_scan",
                               return_value=(["❯ /compact"], None)):
            self.assertTrue(watchdog._pane_has_queued_compact("no compact here at all"))
        with mock.patch.object(watchdog, "_above_box_scan",
                               return_value=([], None)):
            self.assertFalse(watchdog._pane_has_queued_compact("❯ /compact\n❯ "))

    def test_above_box_scan_calls_above_input_box_via_watchdog(self):
        # Patch the package seam; the caller must observe it. A bare
        # intra-module call would use the real _above_input_box and never
        # surface this sentinel.
        with mock.patch.object(watchdog, "_above_input_box",
                               return_value="❯ SENTINEL-ROW"):
            queued, spinner = watchdog._above_box_scan("anything the real scan would parse")
        self.assertEqual(queued, ["❯ SENTINEL-ROW"])
        self.assertIsNone(spinner)


if __name__ == "__main__":
    unittest.main()
