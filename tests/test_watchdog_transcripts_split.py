"""#433 item G step 1 — `watchdog/transcripts.py` split.

The pure transcript / session-file readers (widest-fan-in leaf helpers:
`project_label` ×15, `find_active_transcript`, the marker/error/context
scanners, subagent liveness, the text-emitted tool-call stall detector) were
moved VERBATIM out of `watchdog/__init__.py` into `watchdog.transcripts`, then
re-exported IN PLACE by a positional facade import. These tests lock the two
invariants that keep every existing `watchdog.<name>` seam
(goal / compact / cross_stream / janitor, hooks, and every monkeypatch in the
suite) working unchanged after the move:

  1. `import watchdog` stays clean in a FRESH subprocess — no circular-import
     breakage from `transcripts.py`'s `from watchdog import _SENTINELS` (bound
     in `__init__` above the facade-import position) or from any consumer
     submodule.
  2. Every moved name is the SAME object at `watchdog.<name>` and at
     `watchdog.transcripts.<name>` — the C2 re-export contract. If a future
     edit lets a name drift to a private copy, `watchdog.<name> is
     watchdog.transcripts.<name>` breaks and this test fails loudly (a dead
     `monkeypatch.setattr(watchdog, "<name>", ...)` seam is exactly this bug).
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog  # noqa: E402
import watchdog.transcripts as transcripts  # noqa: E402

# Every name moved into transcripts.py (18 functions + 7 module-level
# constants / regexes). Kept explicit — a hand-maintained list is the point:
# if a name is added to or dropped from the move, this list is the checklist
# the reviewer diffs against the facade import block in `__init__.py`.
MOVED_NAMES = [
    # functions (definition order)
    "encode_project_dir",
    "find_active_transcript",
    "_iter_jsonl_tail",
    "_entry_text",
    "transcript_last_error",
    "transcript_current_context",
    "transcript_last_marker",
    "transcript_last_marker_line",
    "transcript_last_assistant_text",
    "subagent_active",
    "_count_live_subagents",
    "newest_subagent_transcript",
    "_entry_has_tool_use",
    "_ends_with_toolcall",
    "transcript_text_toolcall_stall",
    "_hash",
    "_stream_user",
    "project_label",
    # module-level constants / regexes (definition order)
    "_MARKER_RX",
    "SUBAGENT_MAX_AGE_SECONDS",
    "SUBAGENT_NUDGE_STATE_TTL_SECONDS",
    "_TEXTCALL_RX",
    "_TOOLCALL_BLOCK_RX",
    "_TOOLCALL_MARKUP_AFTER_RX",
    "_GENERIC_DIRS",
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

    def test_import_transcripts_submodule_directly(self):
        # Importing the submodule first must still initialize the package
        # cleanly (parent-package init runs first, binding `_SENTINELS`).
        r = subprocess.run(
            [sys.executable, "-c",
             "import watchdog.transcripts as t; print(t.project_label('/x/repo'))"],
            capture_output=True, text=True, cwd=str(REPO),
        )
        self.assertEqual(r.returncode, 0, msg=f"stderr:\n{r.stderr}")
        self.assertEqual(r.stderr.strip(), "")


class ReExportIdentity(unittest.TestCase):
    def test_every_moved_name_is_reexported_with_object_identity(self):
        for name in MOVED_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(transcripts, name),
                                f"{name} missing from watchdog.transcripts")
                self.assertTrue(hasattr(watchdog, name),
                                f"{name} not re-exported into watchdog namespace")
                self.assertIs(getattr(watchdog, name), getattr(transcripts, name),
                              f"watchdog.{name} is not watchdog.transcripts.{name}")

    def test_transcripts_lives_in_its_own_module_file(self):
        self.assertTrue(transcripts.__file__.endswith("watchdog/transcripts.py"),
                        transcripts.__file__)

    def test_sentinels_stayed_in_init_and_resolves_from_transcripts(self):
        # `_SENTINELS` was NOT moved (it belongs to a later step); transcripts.py
        # imports it from the package at module top. Both must see one object.
        self.assertIs(transcripts._SENTINELS, watchdog._SENTINELS)
        self.assertEqual(watchdog._SENTINELS, {"", "No response requested."})


if __name__ == "__main__":
    unittest.main()
