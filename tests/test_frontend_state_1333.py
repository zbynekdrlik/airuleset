"""#1333 review round 3 (ROZHODNUTÉ A) — a STATE check after Bash, never a
command-text parser.

Two review rounds bypassed the PreToolUse Bash parser a dozen ways (`#`
comments, `npm --prefix web i react`, `pnpm -C`, `yarn workspace`, subshells,
`npm exec`, `npm pkg set`, `jq > package.json`, ...) — the #1220 lesson:
never guard STATE by parsing commands. ``hooks/check-frontend-framework-
state.sh`` -> ``gates.housestate`` runs PostToolUse(Bash). When the command
text mentions a JS package manager or scaffolder (a cheap prefilter; a false
positive only costs one scan) in a house repo, it compares the repo's
``package.json`` files (working tree + untracked, git-aware, ``SKIP_DIRS`` and
hidden dirs left out) with HEAD, and blocks (exit 2, reason on stderr, the
PostToolUse channel to the model) for any framework FAMILY present now but in
no HEAD package.json, unless that family's owner-approved marker exists.

Checkouts are real git repos (``_stack_testlib``); hermetic HOME (#1046).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)
from _stack_testlib import commit_all, git, git_repo  # noqa: E402

HOOK = REPO / "hooks" / "check-frontend-framework-state.sh"
MARKER = "<!-- airuleset:stack-exception React admin panel — owner zbynekdrlik/x#5 -->"
PLAYWRIGHT = {"name": "e2e", "devDependencies": {"@playwright/test": "^1.50.0"}}


def _pkg(path, deps):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "x", "dependencies": deps}, indent=2),
                    encoding="utf-8")


class _StateCase(unittest.TestCase):
    def setUp(self):
        self.env = hermetic_hook_env(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.proj = git_repo(self.tmp / "proj")          # unregistered = house
        (self.proj / "CLAUDE.md").write_text("# proj\n", encoding="utf-8")
        _pkg(self.proj / "tools" / "package.json", {"@playwright/test": "1"})
        commit_all(self.proj, "base")

    def _post(self, command, cwd=None):
        payload = json.dumps({
            "session_id": "fs1333", "hook_event_name": "PostToolUse",
            "tool_name": "Bash", "cwd": str(cwd or self.proj),
            "tool_input": {"command": command},
            "tool_response": {"stdout": "added 3 packages", "stderr": ""}})
        return subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                              text=True, env=self.env, timeout=30)

    def _log(self):
        p = Path(self.env["HOME"]) / ".claude" / "stack-gate.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def assertBlocked(self, r, *needles):
        self.assertEqual(r.returncode, 2, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        for n in needles:
            self.assertIn(n, r.stderr)

    def assertClean(self, r):
        self.assertEqual(r.returncode, 0, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        for n in ("fail-open", "Traceback", "exited", "BLOCKED"):
            self.assertNotIn(n, r.stderr)


class TestWiring(unittest.TestCase):
    def test_hook_exists_runs_the_gate_with_dash_p_and_is_post_bash(self):
        self.assertTrue(HOOK.exists(), HOOK)
        src = HOOK.read_text(encoding="utf-8")
        self.assertIn("python3 -P -m gates.housestate", src)
        self.assertIn("set -euo pipefail", src)
        self.assertIn("Dry-run:", src)
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text(encoding="utf-8"))
        wired = [(ev, b.get("matcher")) for ev, blocks in cfg["hooks"].items()
                 for b in blocks for h in b.get("hooks", [])
                 if "check-frontend-framework-state.sh" in h.get("command", "")]
        self.assertEqual(wired, [("PostToolUse", "Bash")])


class TestStateBlocks(_StateCase):
    def test_a_framework_added_to_a_tracked_package_json(self):
        _pkg(self.proj / "tools" / "package.json", {"@playwright/test": "1", "react": "18"})
        r = self._post("npm --prefix tools i react")
        self.assertBlocked(r, "react", "tools/package.json", "house-stack.md",
                           "airuleset:stack-exception")
        self.assertIn("BLOCK", self._log())

    def test_an_untracked_scaffold(self):
        _pkg(self.proj / "ui" / "package.json", {"vue": "3"})
        self.assertBlocked(self._post("npm create vue@latest ui"), "vue", "ui/package.json")

    def test_any_command_shape_that_mentions_a_package_manager(self):
        for cmd in ("(cd web && npm i react)", "pnpm -C web add react  # x",
                    "yarn workspace web add react", "npm pkg set dependencies.react=18",
                    "jq '.dependencies.react=\"18\"' package.json > p && mv p package.json"):
            with self.subTest(cmd=cmd):
                _pkg(self.proj / "web" / "package.json", {"react": "18"})
                self.assertBlocked(self._post(cmd), "react")

    def test_a_repo_without_commits(self):
        fresh = git_repo(self.tmp / "fresh")
        _pkg(fresh / "package.json", {"next": "15"})
        self.assertBlocked(self._post("npx create-next-app@latest .", cwd=fresh), "react")

    def test_a_new_family_next_to_an_existing_one(self):
        _pkg(self.proj / "a" / "package.json", {"react": "18"})
        commit_all(self.proj, "react app")
        _pkg(self.proj / "b" / "package.json", {"vue": "3"})
        r = self._post("cd b && npm i vue")
        self.assertBlocked(r, "vue")
        self.assertNotIn("react", r.stderr.split("framework(s)")[1].splitlines()[0])


class TestStateAllows(_StateCase):
    def test_a_family_already_at_head(self):
        _pkg(self.proj / "a" / "package.json", {"react": "18"})
        commit_all(self.proj, "react app")
        _pkg(self.proj / "a" / "package.json", {"react": "18", "react-dom": "18",
                                                "next": "15"})
        self.assertClean(self._post("npm i react-dom next"))

    def test_a_command_that_names_no_package_manager_is_not_checked(self):
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        self.assertClean(self._post("git status"))
        self.assertEqual(self._log(), "")

    def test_skipped_and_hidden_dirs_never_count(self):
        for rel in ("node_modules/react/package.json", "e2e/package.json",
                    "tests/fixtures/x/package.json", ".cache/x/package.json"):
            _pkg(self.proj / rel, {"react": "18"})
        self.assertClean(self._post("npm install"))

    def test_a_non_house_project_is_never_checked(self):
        git(self.proj, "remote", "add", "origin", "git@github.com:zbynekdrlik/odoo-erp.git")
        _pkg(self.proj / "frontline" / "apps" / "new" / "package.json", {"react": "18"})
        self.assertClean(self._post("npm i react"))

    def test_outside_any_repo_nothing_is_checked(self):
        bare = self.tmp / "plain"
        _pkg(bare / "package.json", {"react": "18"})
        self.assertClean(self._post("npm i react", cwd=bare))

    def test_the_family_marker_allows_and_is_logged(self):
        (self.proj / "CLAUDE.md").write_text(MARKER, encoding="utf-8")
        _pkg(self.proj / "web" / "package.json", {"react": "18", "next": "15"})
        self.assertClean(self._post("npm i react next"))
        self.assertIn("ALLOW-MARKER", self._log())

    def test_a_marker_for_another_technology_does_not(self):
        (self.proj / "CLAUDE.md").write_text(
            "<!-- airuleset:stack-exception Vue admin — owner o/x#9 -->", encoding="utf-8")
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        self.assertBlocked(self._post("npm i react"), "react")


if __name__ == "__main__":
    unittest.main()
