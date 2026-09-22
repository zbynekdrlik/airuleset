"""#1069 -- gates.testskips SANCTIONED allow-list for the ONE Playwright
literal odoo-erp's contract permits: a TOP-LEVEL (column 0)
``test.skip`` whose first argument is exactly the bare call ``notApplicable()``
followed by a reason (see ``.claude/rules/gk-review-lenses.md`` §test-integrity,
odoo-erp CI job ``No Skip Validation`` for placement). Everything else — the
same shape INDENTED inside a body, a truthy/condition first arg, a compound
first arg, a missing reason, the it-skip / pytest-mark-skip shapes — stays
blocked.

The skip literals themselves live in ``tests/fixtures/testskips_1069/*.txt``
(a non-code extension, so gates.testskips never scans THEM); this test file
therefore carries no contiguous skip literal of its own and rides the gate
cleanly. Each case builds a real temp git repo (base commit + ``origin/main``
ref + a second commit appending the fixture to a genuinely test-named code
file), then drives the real ``hooks/block-test-skips.sh`` against a
``git push origin main`` payload — the same subprocess seam
``TestBlockTestSkipsHook`` uses in test_airuleset.py.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "hooks" / "block-test-skips.sh"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "testskips_1069"


def _fixture(name):
    return (FIXTURES / name).read_text()


class TestSanctionedTestSkip(unittest.TestCase):
    def _run(self, command, cwd):
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        # isolate HOME so the bypass/audit log write never touches the real one
        env["HOME"] = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, env["HOME"], ignore_errors=True)
        return subprocess.run(
            ["bash", str(HOOK)], input=payload, text=True,
            capture_output=True, cwd=cwd, timeout=60, env=env)

    def _repo(self, added_content, rel_path):
        """Temp git repo: empty base commit of ``rel_path`` (a scanned,
        test-named code file), ``origin/main`` pinned to it, then a second
        commit APPENDING ``added_content`` — the outgoing diff this push
        carries."""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        def g(*a):
            return subprocess.run(["git", *a], cwd=root,
                                  capture_output=True, text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t")
        g("config", "user.name", "t")
        full = os.path.join(root, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write("")
        g("add", rel_path)
        g("commit", "-qm", "base")
        g("update-ref", "refs/remotes/origin/main",
          g("rev-parse", "HEAD").stdout.strip())
        with open(full, "a") as fh:
            fh.write(added_content)
        g("add", rel_path)
        g("commit", "-qm", "test: add coverage")
        return root

    def _push(self, fixture_name, rel_path):
        root = self._repo(_fixture(fixture_name), rel_path)
        return self._run("git push origin main", root)

    # --- ALLOW: the ONE sanctioned top-level literal --------------------

    def test_allows_toplevel_single_quote(self):
        r = self._push("allow_toplevel_single.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_toplevel_double_quote(self):
        r = self._push("allow_toplevel_double.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_allows_toplevel_template_literal_reason(self):
        r = self._push("allow_toplevel_template.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- BLOCK: every other shape --------------------------------------

    def test_blocks_truthy_first_arg(self):
        r = self._push("block_true.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_condition_first_arg(self):
        r = self._push("block_negation.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_sanctioned_shape_when_indented_in_body(self):
        # identical call, but INDENTED inside a test( body -> not top-level
        # -> must block (this is the exact security hole the ticket exists
        # to prevent: a real skip hiding behind the sanctioned literal).
        r = self._push("block_indented.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_compound_first_arg(self):
        r = self._push("block_compound.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_notapplicable_without_reason(self):
        r = self._push("block_noreason.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_it_skip_shape(self):
        r = self._push("block_it_skip.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_pytest_mark_skip_shape(self):
        r = self._push("block_pytest.txt", "tests/test_addon.py")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_mixed_diff_sanctioned_plus_real_skip_still_blocks(self):
        # a sanctioned top-level literal AND a real truthy skip in the same
        # diff: the sanctioned line is dropped, the real one still blocks.
        r = self._push("mixed.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_real_skip_appended_on_same_physical_line(self):
        # adversarial-review BLOCKER: `git diff -U0` yields one added line per
        # PHYSICAL line, so a real truthy skip call appended AFTER the
        # sanctioned prefix on the SAME line must NOT sail through. This is the
        # horizontal-axis twin of the indented (vertical-axis) hole.
        r = self._push("block_sameline_append.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_it_skip_appended_on_same_physical_line(self):
        # same-line append of a DIFFERENT banned shape (it.skip) after the
        # sanctioned prefix — must still block.
        r = self._push("block_sameline_it_skip.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    def test_blocks_empty_reason_comma_only(self):
        # the sanctioned prefix with a comma but an EMPTY reason (no string);
        # the contract requires a real reason, so it must block.
        r = self._push("block_empty_reason.txt", "tests/e2e/money.spec.ts")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stdout + r.stderr)

    # --- the BLOCK text names the sanctioned exception -----------------

    def test_block_text_names_the_sanctioned_exception(self):
        r = self._push("block_true.txt", "tests/e2e/money.spec.ts")
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 2, out)
        self.assertIn("notApplicable", out)
        self.assertIn("sanctioned exception", out)

    def test_wired_hook_exists_and_parses(self):
        # sanity: the hook script itself is syntactically valid bash.
        r = subprocess.run(["bash", "-n", str(HOOK)],
                           capture_output=True, text=True, env=hermetic_hook_env(self))
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
