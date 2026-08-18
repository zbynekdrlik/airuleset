"""No git-tracked file may carry committed merge-conflict markers.

Live incident (2026-08-18, found while merging #534): the #517-#519 integration
wave committed UNRESOLVED conflict markers (`<<<<<<< HEAD` ... `>>>>>>>
worktree-agent-a1a283de...`) into `.claude/rules-reference/internals-archive.md`
on main, and nothing caught it — the archive is deliberately outside the rules
auto-load set, so no session ever tripped over the garbage until the next merge
touched the same region. This guard scans every tracked text file for
line-start conflict markers so an unresolved merge can never land silently
again. Quoting a marker in a lesson is still fine — indent it (the scan only
matches at column 0).
"""

import os
import subprocess
import tempfile
from unittest import TestCase, main

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MARKS = ("<<<<<<< ", ">>>>>>> ")


def offending_lines(path):
    """(lineno, prefix) for each line-start conflict marker; [] for binary/unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (UnicodeDecodeError, OSError):
        return []
    return [
        (n, ln[:60])
        for n, ln in enumerate(text.splitlines(), 1)
        if ln.startswith(MARKS)
    ]


class NoCommittedConflictMarkers(TestCase):
    def test_no_tracked_file_carries_conflict_markers(self):
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        bad = {}
        for rel in tracked:
            hits = offending_lines(os.path.join(REPO, rel))
            if hits:
                bad[rel] = hits
        self.assertEqual(
            bad, {}, f"committed merge-conflict markers found: {bad}"
        )

    def test_scanner_has_teeth(self):
        # feed the scanner a synthetic conflicted file — proves the guard
        # actually detects both marker shapes at column 0 and ignores an
        # indented (quoted) marker.
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False
        ) as f:
            f.write("ok\n<<<<<<< HEAD\nmid\n>>>>>>> some-branch\n  <<<<<<< quoted\n")
            p = f.name
        try:
            self.assertEqual([n for n, _ in offending_lines(p)], [2, 4])
        finally:
            os.unlink(p)


if __name__ == "__main__":
    main()
