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

    # ---- #66: heredoc BODY content must never be scanned ----
    #
    # Filing airuleset#66 itself hit this: `gh issue create -F body.md` is
    # the CORRECT recipe, but the ticket BODY (written via a heredoc into
    # body.md as part of the same Bash command) documents gh recipes that
    # mention `--json` on the READ subcommands. The hook must only scan the
    # actual command tokens, never the heredoc payload.

    def test_heredoc_body_mentioning_json_recipe_does_not_block_create(self):
        cmd = (
            "cat > /tmp/body.md <<'EOF2'\n"
            "Recipe: gh issue create -F body.md\n"
            "Read fields: gh issue view $num --json number,title\n"
            "EOF2\n"
            'gh issue create -t "Title" -F /tmp/body.md -l bug'
        )
        self.assertAllowed(cmd)

    def test_heredoc_body_containing_literal_json_flag_text_does_not_block(self):
        # the body documents the BANNED flag verbatim (e.g. a gh-cli-recipes.md
        # excerpt) — still just heredoc payload, never a real flag position.
        cmd = (
            "cat > /tmp/body.md <<'EOF3'\n"
            "Never do `gh issue create --json number` — it has no --json flag.\n"
            "EOF3\n"
            "gh issue create -t T -F /tmp/body.md"
        )
        self.assertAllowed(cmd)

    def test_real_json_flag_after_heredoc_is_still_blocked(self):
        # the heredoc-stripping must not swallow a GENUINE violation that
        # follows it on the actual command line.
        cmd = (
            "cat > /tmp/body.md <<'EOF4'\n"
            "just a normal ticket body, nothing special here\n"
            "EOF4\n"
            "gh issue create -t T -F /tmp/body.md --json number"
        )
        self.assertBlocked(cmd)

    def test_unterminated_heredoc_fails_safe_to_original_scan(self):
        # malformed input (no closing delimiter) must not crash the hook;
        # falling back to scanning everything (still allowed here, since no
        # real --json flag exists on an actual command line) is acceptable.
        cmd = "cat > /tmp/body.md <<'EOF5'\nno closing delimiter below\n"
        r = run(cmd)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    main()
