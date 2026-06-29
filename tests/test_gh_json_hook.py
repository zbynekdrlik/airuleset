"""Behaviour test for hooks/block-gh-invalid-json-flag.sh.

The hook hard-blocks `--json` on gh WRITE subcommands (create/edit/comment/...),
which have no such flag, while leaving the READ subcommands (list/view) and the
correct -F/--body-file recipe untouched. Locks the "fifth attempt" loop shut.
"""

import json
import subprocess
from pathlib import Path
from unittest import TestCase, main

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-gh-invalid-json-flag.sh"


def run(cmd):
    payload = json.dumps({"tool_input": {"command": cmd}})
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True
    )


class TestGhJsonHook(TestCase):
    def assertBlocked(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2, f"expected BLOCK for: {cmd}\nstderr={r.stderr}")
        self.assertIn("--json", r.stderr)  # explains the problem

    def assertAllowed(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 0, f"expected ALLOW for: {cmd}\nstderr={r.stderr}")

    def test_blocks_issue_create_json(self):
        self.assertBlocked("gh issue create --title T --body B --json number")

    def test_blocks_pr_create_json(self):
        self.assertBlocked("gh pr create -t T -F body.md --json")

    def test_blocks_issue_edit_json(self):
        self.assertBlocked("gh issue edit 5 --json state")

    def test_allows_issue_list_json(self):
        self.assertAllowed("gh issue list --json number,title")

    def test_allows_issue_view_json(self):
        self.assertAllowed("gh issue view 5 --json state,url")

    def test_allows_correct_create_recipe(self):
        self.assertAllowed('gh issue create -t "T" -F body.md -l bug')

    def test_allows_text_merely_mentioning_the_flag(self):
        # A commit message / echo that mentions the flag inside quotes is not a
        # real flag position — must NOT block.
        self.assertAllowed('git commit -m "note: gh issue create --json was wrong"')

    def test_bypass_marker_allows(self):
        self.assertAllowed("gh issue create -t T -F body.md --json number # airuleset:gh-ok")

    def test_unrelated_command_allowed(self):
        self.assertAllowed("ls -la && echo done")


if __name__ == "__main__":
    main()
