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
from _stack_testlib import add_origin, commit_all, git, git_repo  # noqa: E402

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

    def _post(self, command, cwd=None, event="PostToolUse",
              response=None):
        payload = json.dumps({
            "session_id": "fs1333", "hook_event_name": event,
            "tool_name": "Bash", "cwd": str(cwd or self.proj),
            "tool_input": {"command": command},
            "tool_response": response or {"stdout": "added 3 packages", "stderr": ""}})
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
        wired = sorted((ev, b.get("matcher")) for ev, blocks in cfg["hooks"].items()
                       for b in blocks for h in b.get("hooks", [])
                       if "check-frontend-framework-state.sh" in h.get("command", ""))
        # round 4 (item 1): a failing npm/npx call leaves state too
        self.assertEqual(wired, [("PostToolUse", "Bash"), ("PostToolUseFailure", "Bash")])

    def test_the_prefilter_reads_the_command_field_only(self):
        # round 4 (item 4): the tool output never triggers the scan
        src = HOOK.read_text(encoding="utf-8")
        self.assertIn(".tool_input.command", src)
        self.assertIn('case "$CMD" in', src)
        self.assertNotIn('case "$PAYLOAD" in', src)


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


class TestRoundFour(_StateCase):
    def test_a_failing_command_is_checked_too(self):
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        r = self._post("npm i react && npm run build", event="PostToolUseFailure",
                       response={"error": "npm ERR! build failed"})
        self.assertBlocked(r, "react")

    def test_committing_in_the_same_call_does_not_hide_the_framework(self):
        add_origin(self.proj)
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        commit_all(self.proj, "add react")
        r = self._post("npm i react && git add -A && git commit -m ui")
        self.assertBlocked(r, "react", "committing does not make it allowed")

    def test_the_upstream_is_the_fallback_baseline(self):
        add_origin(self.proj, set_head=False)
        git(self.proj, "branch", "-q", "--set-upstream-to", "origin/main")
        _pkg(self.proj / "web" / "package.json", {"vue": "3"})
        commit_all(self.proj, "add vue")
        self.assertBlocked(self._post("npm i vue && git commit -am ui"), "vue")

    def test_without_a_remote_the_baseline_is_head(self):
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        commit_all(self.proj, "react app")
        self.assertClean(self._post("npm i react-dom"))

    def test_the_message_names_the_uninstall_and_the_history(self):
        _pkg(self.proj / "web" / "package.json", {"react": "18", "react-dom": "18"})
        r = self._post("npm i react-dom")
        self.assertBlocked(r, "present since an earlier change", "npm uninstall react react-dom",
                           "remove the scaffold dir")

    def test_an_unparseable_baseline_package_json_fails_open_with_a_log_line(self):
        (self.proj / "tools" / "package.json").write_text('{"name": ', encoding="utf-8")
        commit_all(self.proj, "broken")
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        r = self._post("npm i react")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("fail-open", r.stderr)
        self.assertIn("unparseable baseline", self._log())

    def test_the_baseline_is_read_only_when_a_changed_file_names_a_framework(self):
        (self.proj / "tools" / "package.json").write_text('{"name": ', encoding="utf-8")
        commit_all(self.proj, "broken")
        _pkg(self.proj / "web" / "package.json", {"lodash": "4"})
        self.assertClean(self._post("npm i lodash"))
        self.assertNotIn("FAIL-OPEN", self._log())

    def test_a_non_repo_house_cwd_gets_a_shallow_scan(self):
        plain = self.tmp / "plain"
        _pkg(plain / "ui" / "package.json", {"svelte": "5"})
        self.assertBlocked(self._post("npm create svelte@latest ui", cwd=plain), "svelte")
        deep = self.tmp / "deep"
        _pkg(deep / "a" / "b" / "c" / "package.json", {"react": "18"})
        self.assertClean(self._post("npm i", cwd=deep))

    def test_a_submodule_of_a_platform_project_is_never_checked(self):
        from _stack_testlib import add_submodule
        widget = git_repo(self.tmp / "widget-src", "git@github.com:o/widget.git",
                          commit=True)
        odoo = git_repo(self.tmp / "odoo", "git@github.com:zbynekdrlik/odoo-erp.git",
                        commit=True)
        sub = add_submodule(odoo, widget, "widget")
        _pkg(sub / "package.json", {"react": "18"})
        self.assertClean(self._post("npm i react", cwd=sub))

    def test_unreadable_registry_skips_with_a_log_line(self):
        import os
        from unittest import mock
        sys.path.insert(0, str(REPO))
        from gates import housestate
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        with mock.patch.dict(os.environ, {"HOME": self.env["HOME"]}), \
                mock.patch.object(housestate, "REGISTRY", str(self.tmp / "missing.json")):
            self.assertEqual(housestate.check("npm i react", str(self.proj)), "")
        self.assertIn("registry unreadable", self._log())


class TestRoundFive(_StateCase):
    def test_a_non_git_parent_never_judges_its_repo_subdirs(self):
        # 🔴 the real dev1 `~/devel` shape: platform repos under a non-git parent
        devel = self.tmp / "devel"
        shop = git_repo(devel / "storefront",
                        "git@github.com:zbynekdrlik/automatizacie-montalu.git")
        _pkg(shop / "package.json", {"@sveltejs/kit": "2", "svelte": "5"})
        site = git_repo(devel / "site", "git@github.com:o/brand-site.git")
        _pkg(site / "package.json", {"astro": "4"})
        self.assertClean(self._post("npm i", cwd=devel))
        _pkg(devel / "plain-ui" / "package.json", {"react": "18"})
        r = self._post("npm i", cwd=devel)
        self.assertBlocked(r, "react")
        self.assertNotIn("svelte", r.stderr)
        self.assertNotIn("astro", r.stderr)

    def test_a_git_failure_fails_open_and_skips_the_shallow_scan(self):
        broken = self.tmp / "broken"
        (broken / ".git").mkdir(parents=True)        # git cannot read it
        _pkg(broken / "sub" / "package.json", {"react": "18"})
        for cwd in (broken, broken / "sub"):
            with self.subTest(cwd=cwd.name):
                r = self._post("npm i react", cwd=cwd)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("fail-open", r.stderr)
        self.assertIn("git failed", self._log())

    def test_origin_main_then_origin_master_are_the_baseline_before_upstream(self):
        add_origin(self.proj, set_head=False)        # origin/main, no origin/HEAD
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        commit_all(self.proj, "add react")
        self.assertBlocked(self._post("npm i react && git commit -am x"),
                           "absent from origin/main")
        old = git_repo(self.tmp / "old", commit=True, branch="master")
        _pkg(old / "tools" / "package.json", {"@playwright/test": "1"})
        commit_all(old, "base")
        add_origin(old, set_head=False, branch="master")
        _pkg(old / "web" / "package.json", {"vue": "3"})
        commit_all(old, "add vue")
        self.assertBlocked(self._post("npm i vue", cwd=old), "absent from origin/master")

    def test_a_head_fallback_is_logged_and_worded(self):
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        r = self._post("npm i react")
        self.assertBlocked(r, "absent from HEAD")
        self.assertIn("BASELINE HEAD", self._log())

    def test_an_unparseable_changed_package_json_is_logged(self):
        (self.proj / "web").mkdir()
        (self.proj / "web" / "package.json").write_text('{"name": ', encoding="utf-8")
        _pkg(self.proj / "ui" / "package.json", {"react": "18"})
        self.assertBlocked(self._post("npm i react"), "react")
        self.assertIn("unparseable changed web/package.json", self._log())

    def test_one_git_layout_per_run_and_no_diff_rehash(self):
        import shutil
        real_git = shutil.which("git")
        stub = self.tmp / "stub-bin"
        stub.mkdir()
        calls = self.tmp / "git-calls"
        (stub / "git").write_text('#!/bin/sh\necho "$*" >> %s\nexec %s "$@"\n'
                                  % (calls, real_git))
        (stub / "git").chmod(0o755)
        self.env["PATH"] = "%s:%s" % (stub, self.env.get("PATH", ""))
        _pkg(self.proj / "web" / "package.json", {"react": "18"})
        self.assertBlocked(self._post("npm i react"), "react")
        lines = calls.read_text().splitlines()
        self.assertEqual(sum("--show-toplevel" in ln for ln in lines), 1, lines)
        self.assertFalse([ln for ln in lines if " diff " in " %s " % ln], lines)
        self.assertTrue([ln for ln in lines if "*package.json" in ln], lines)

    def test_one_deadline_below_the_hook_timeouts(self):
        import cli_stack_scope
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text(encoding="utf-8"))
        timeouts = [h.get("timeout", 60) for blocks in cfg["hooks"].values() for b in blocks
                    for h in b.get("hooks", [])
                    if "frontend-framework" in h.get("command", "")]
        self.assertTrue(timeouts)
        self.assertLess(cli_stack_scope.HOOK_DEADLINE_S, min(timeouts))


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
