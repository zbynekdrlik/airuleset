"""Behaviour test for hooks/block-root-recursive-grep.sh (#776, Layer 1).

The hook BLOCKS a recursive grep/ugrep whose search ROOT is `/` or another huge
non-repo root (`/home`, `~`, `$HOME`, a top-level system dir) — exactly the
shape that spawns a runaway shadow-ugrep — while leaving scoped greps, non-
recursive greps, and text merely MENTIONING the shape untouched. FAIL-OPEN.
"""

import json
import subprocess
from pathlib import Path
from unittest import TestCase, main

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-root-recursive-grep.sh"


def run(cmd):
    payload = json.dumps({"tool_input": {"command": cmd}})
    return subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True
    )


class TestBlockRootRecursiveGrep(TestCase):
    def assertBlocked(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK for: {cmd}\nstderr={r.stderr}")
        # the reason points at the Grep tool / a scoped root
        self.assertIn("Grep tool", r.stderr)

    def assertAllowed(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 0,
                         f"expected ALLOW for: {cmd}\nstderr={r.stderr}")

    # ---- BLOCKED: recursive grep on a huge root -------------------------
    def test_blocks_grep_rn_root(self):
        self.assertBlocked("grep -rn foo /")

    def test_blocks_grep_capital_R_root(self):
        self.assertBlocked("grep -R foo /")

    def test_blocks_grep_long_recursive_root(self):
        self.assertBlocked("grep --recursive foo /")

    def test_blocks_grep_rn_home_dir(self):
        self.assertBlocked("grep -rn foo /home")

    def test_blocks_grep_rn_home_user(self):
        self.assertBlocked("grep -rn foo /home/newlevel")

    def test_blocks_grep_rn_tilde(self):
        self.assertBlocked("grep -rn foo ~")

    def test_blocks_grep_rn_dollar_home(self):
        self.assertBlocked("grep -rn foo $HOME")

    def test_blocks_grep_rn_usr(self):
        self.assertBlocked("grep -rn foo /usr")

    def test_blocks_grep_rn_etc_trailing_slash(self):
        self.assertBlocked("grep -rn foo /etc/")

    def test_blocks_ugrep_recursive_root(self):
        self.assertBlocked("ugrep -r pattern /")

    def test_blocks_rgrep_root(self):
        self.assertBlocked("rgrep -n foo /")

    def test_blocks_grep_e_pattern_then_root(self):
        # -e supplies the pattern, so the only positional is the PATH
        self.assertBlocked("grep -rn -e foo /")

    def test_blocks_chained_root_grep(self):
        self.assertBlocked("cd /tmp && grep -rn needle /")

    def test_blocks_grep_root_slash_bundled_flags(self):
        self.assertBlocked("grep -rIn foo /")

    def test_blocks_bare_home_shortcut_braces(self):
        self.assertBlocked("grep -rn foo ${HOME}")

    # ---- ALLOWED: scoped / non-recursive / mentions ---------------------
    def test_allows_scoped_dot(self):
        self.assertAllowed("grep -rn foo .")

    def test_allows_scoped_subdir(self):
        self.assertAllowed("grep -rn foo ./src")

    def test_allows_scoped_repo_path_under_home(self):
        # 2+ components under /home is a repo checkout, not a home root
        self.assertAllowed("grep -rn foo /home/newlevel/devel/airuleset")

    def test_allows_recursive_no_path(self):
        # recurses the cwd/repo — the common legitimate case
        self.assertAllowed("grep -rn foo")

    def test_allows_non_recursive_root(self):
        # not recursive: `grep foo /` just errors "Is a directory", no runaway
        self.assertAllowed("grep foo /")

    def test_allows_grep_specific_file(self):
        self.assertAllowed("grep -n foo /etc/hosts")

    def test_allows_pattern_that_looks_like_root(self):
        # searching for the literal pattern "/" inside the cwd is fine
        self.assertAllowed("grep -rn / .")

    def test_allows_text_merely_mentioning_the_shape(self):
        self.assertAllowed('git commit -m "note: never run grep -rn x / again"')

    def test_allows_heredoc_body_mentioning_the_shape(self):
        self.assertAllowed(
            "cat > body.md <<'EOF'\nWe must avoid grep -rn foo / forever.\nEOF")

    def test_bypass_marker_allows(self):
        self.assertAllowed("grep -rn foo /  # airuleset:root-grep-ok debugging")

    def test_empty_command_allows(self):
        r = run("")
        self.assertEqual(r.returncode, 0)

    # ---- #776 review regressions -----------------------------------------
    def test_blocks_quoted_alternation_pattern(self):
        # #776 review 🟡: `|` inside the quoted PATTERN must not split the
        # segment and let the root scan slip.
        self.assertBlocked('grep -rEn "foo|bar" /')

    def test_blocks_quoted_semicolon_pattern(self):
        self.assertBlocked('grep -rn "a;b" /')

    def test_allows_commit_message_quoting_the_shape_after_semicolon(self):
        # #776 review 🟡: a `;` inside a quoted commit message must NOT be a
        # segment split that false-blocks the mention.
        self.assertAllowed('git commit -m "fix x; grep -rn y /home z"')

    def test_blocks_color_flag_without_equals(self):
        # #776 review 🟡: --color takes an OPTIONAL =WHEN, never space-sep, so
        # it must NOT swallow the pattern and let `/` through.
        self.assertBlocked("grep -r --color pattern /")

    def test_blocks_root_glob(self):
        self.assertBlocked("grep -rn foo /*")

    def test_blocks_home_glob(self):
        self.assertBlocked("grep -rn foo /home/*")

    def test_blocks_nohup_prefix(self):
        self.assertBlocked("nohup grep -rn foo /")

    def test_blocks_command_prefix(self):
        self.assertBlocked("command grep -rn foo /")

    def test_blocks_timeout_prefix_with_flags(self):
        self.assertBlocked("timeout -k 5 30 grep -rn foo /")

    def test_blocks_stdbuf_prefix(self):
        self.assertBlocked("stdbuf -o0 grep -rn foo /")

    def test_blocks_d_recurse_short(self):
        self.assertBlocked("grep -d recurse foo /")

    def test_blocks_directories_recurse_long(self):
        self.assertBlocked("grep --directories=recurse foo /")

    def test_allows_attached_e_value_that_looks_recursive(self):
        # #776 review 🔵: `-er` is `-e` with pattern value `r` (NOT recursive).
        self.assertAllowed("grep -er foo /")

    def test_bypass_marker_as_pattern_does_not_disarm(self):
        # #776 review 🔵: the marker only bypasses AFTER a `#`, not as a
        # quoted grep pattern.
        self.assertBlocked('grep -rn "airuleset:root-grep-ok" /')


if __name__ == "__main__":
    main()
