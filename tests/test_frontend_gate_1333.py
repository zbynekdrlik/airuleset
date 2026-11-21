"""#1333 — the PreToolUse Write|Edit gate refuses a package.json that brings a
JS UI framework family NEW to a house repo without its owner-approved marker.

``hooks/block-frontend-framework.sh`` -> ``gates.housestack`` judges the
``package.json`` CONTENT of a Write, or of an Edit (an empty ``old_string`` is
a whole new file). Since review round 3 (ROZHODNUTÉ A) it never parses Bash
command text: the PostToolUse state check (``test_frontend_state_1333.py``)
covers package managers. What it judges:

- frameworks are FAMILIES (react-dom / next -> react, nuxt -> vue,
  @sveltejs/kit -> svelte, ...); only a family no other package.json of the git
  repo already carries blocks;
- only in a house project: the registry ``stack`` of the file's checkout, or,
  for a path outside any repo, of the session cwd (round 3 B); an unregistered
  repo is house;
- one marker per technology (round 3 E): ``<!-- airuleset:stack-exception
  <what> — <#N or GitHub URL> -->`` covers only the technology ``<what>``
  names; every marker-allowed write is logged as ALLOW-MARKER;
- fail OPEN with a logged reason: an unreadable payload, an unreadable or
  unparseable existing package.json, unparseable new content, an unreadable
  CLAUDE.md, an Edit whose ``old_string`` is not in the file (CRLF);
- an allowed write shows no fail-open or crash text (round 3 I).

Checkouts are REAL git repos (``_stack_testlib``); every hook run uses a
hermetic HOME (#1046).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)
from _stack_testlib import git, git_repo  # noqa: E402

HOOK = REPO / "hooks" / "block-frontend-framework.sh"
MARKER = "<!-- airuleset:stack-exception React admin panel — owner zbynekdrlik/x#5 -->"
REACT = {"name": "panel", "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"}}
PLAYWRIGHT = {"name": "e2e", "devDependencies": {"@playwright/test": "^1.50.0"}}
_NOT_CLEAN = ("fail-open", "Traceback", "classifier exited", "Error")


class _GateCase(unittest.TestCase):
    def setUp(self):
        self.env = hermetic_hook_env(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.proj = git_repo(self.tmp / "proj")          # unregistered = house
        (self.proj / "CLAUDE.md").write_text("# proj\n\n## Playbook router\n",
                                             encoding="utf-8")

    def _run(self, tool_input, tool="Write", raw=None, cwd=None):
        payload = raw if raw is not None else json.dumps({
            "session_id": "fg1333", "tool_name": tool,
            "cwd": str(cwd or self.proj), "tool_input": tool_input})
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, env=self.env,
                              timeout=30)

    def _write(self, rel, doc, cwd=None, root=None):
        content = doc if isinstance(doc, str) else json.dumps(doc, indent=2)
        return self._run({"file_path": str((root or self.proj) / rel),
                          "content": content}, cwd=cwd)

    def _edit(self, rel, old, new):
        return self._run({"file_path": str(self.proj / rel), "old_string": old,
                          "new_string": new}, tool="Edit")

    def _log(self):
        p = Path(self.env["HOME"]) / ".claude" / "stack-gate.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def assertBlocked(self, r, *needles):
        self.assertEqual(r.returncode, 2, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        for n in needles:
            self.assertIn(n, r.stderr)
        self.assertNotIn("BLOCKED", r.stdout)

    def assertAllowed(self, r):
        self.assertEqual(r.returncode, 0, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        for n in _NOT_CLEAN:
            self.assertNotIn(n, r.stderr)

    def assertFailOpen(self, r, logged):
        self.assertEqual(r.returncode, 0, "stdout=%r stderr=%r" % (r.stdout, r.stderr))
        self.assertIn("fail-open", r.stderr)
        self.assertIn(logged, self._log())


class TestHookWiring(unittest.TestCase):
    def test_hook_exists_and_is_executable(self):
        self.assertTrue(HOOK.exists(), "missing hook %s" % HOOK)
        self.assertTrue(os.access(HOOK, os.X_OK), "hook must be executable")

    def test_hook_runs_the_gate_module_with_dash_p(self):
        src = HOOK.read_text(encoding="utf-8")
        self.assertIn("python3 -P -m gates.housestack", src)
        self.assertIn("set -euo pipefail", src)
        self.assertIn("Dry-run:", src)

    def test_registered_on_write_and_edit_only(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text(encoding="utf-8"))
        wired = sorted(b.get("matcher") for b in cfg["hooks"]["PreToolUse"]
                       for h in b.get("hooks", [])
                       if "block-frontend-framework.sh" in h.get("command", ""))
        self.assertEqual(wired, ["Edit", "Write"])

    def test_the_gate_parses_no_command_text(self):
        # round 3 A: state is checked after the command, never parsed before it
        src = (REPO / "gates" / "housestack.py").read_text(encoding="utf-8")
        for gone in ("def classify_bash", "def bash_frameworks", "shlex", "npx"):
            self.assertNotIn(gone, src)


class TestBlocks(_GateCase):
    def test_new_react_package_json_is_refused(self):
        r = self._write("web/package.json", REACT)
        self.assertBlocked(r, "react", "house-stack.md", "airuleset:stack-exception",
                           "owner")
        self.assertIn("BLOCK", self._log())

    def test_every_family_and_dependency_table_is_refused(self):
        for table, dep, family in (("dependencies", "vue", "vue"),
                                   ("devDependencies", "svelte", "svelte"),
                                   ("peerDependencies", "@angular/core", "angular"),
                                   ("dependencies", "preact", "preact"),
                                   ("dependencies", "solid-js", "solid"),
                                   ("dependencies", "next", "react"),
                                   ("dependencies", "nuxt", "vue"),
                                   ("dependencies", "@sveltejs/kit", "svelte"),
                                   ("dependencies", "lit", "lit"),
                                   ("dependencies", "alpinejs", "alpine"),
                                   ("dependencies", "htmx.org", "htmx"),
                                   ("dependencies", "astro", "astro"),
                                   ("dependencies", "@builder.io/qwik", "qwik"),
                                   ("dependencies", "ember-source", "ember")):
            with self.subTest(dep=dep):
                self.assertBlocked(self._write("ui/package.json", {table: {dep: "1"}}),
                                   family)

    def test_npm_alias_and_bom_are_seen_through(self):
        self.assertBlocked(self._write("a/package.json",
                                       {"dependencies": {"ui": "npm:react@18"}}), "react")
        self.assertBlocked(self._write("b/package.json", "﻿" + json.dumps(REACT)),
                           "react")

    def test_edit_with_an_empty_old_string_creates_and_is_judged(self):
        self.assertBlocked(self._edit("ui/package.json", "", json.dumps(REACT)), "react")

    def test_edit_that_adds_react_is_refused(self):
        (self.proj / "package.json").write_text(json.dumps(PLAYWRIGHT, indent=2),
                                                encoding="utf-8")
        r = self._edit("package.json", '"@playwright/test": "^1.50.0"',
                       '"@playwright/test": "^1.50.0", "react": "^18"')
        self.assertBlocked(r, "react")

    def test_adding_a_second_family_to_an_existing_app_is_refused(self):
        (self.proj / "package.json").write_text(json.dumps(REACT), encoding="utf-8")
        doc = dict(REACT, dependencies=dict(REACT["dependencies"], vue="^3"))
        r = self._write("package.json", doc)
        self.assertBlocked(r, "adds the JS UI framework(s) vue, new to this repo")

    def test_a_framework_only_in_a_skipped_or_hidden_dir_is_still_new(self):
        for rel in ("node_modules/x/package.json", ".claude/worktrees/w/package.json",
                    "e2e/package.json"):
            p = self.proj / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(REACT), encoding="utf-8")
        self.assertBlocked(self._write("web/package.json", REACT), "react")


class TestAllows(_GateCase):
    def test_house_stack_e2e_package_json_is_allowed(self):
        self.assertAllowed(self._write("tools/package.json", PLAYWRIGHT))

    def test_a_family_already_in_the_repo_is_not_new(self):
        p = self.proj / "frontline" / "apps" / "a" / "package.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"dependencies": {"react": "18"}}), encoding="utf-8")
        # next / react-dom are the react family: not new
        self.assertAllowed(self._write("frontline/apps/b/package.json",
                                       {"dependencies": {"next": "15", "react-dom": "18"}}))
        self.assertBlocked(self._write("frontline/apps/c/package.json",
                                       {"dependencies": {"vue": "3"}}), "vue")

    def test_unrelated_rewrite_of_an_existing_react_app_is_allowed(self):
        (self.proj / "package.json").write_text(json.dumps(REACT), encoding="utf-8")
        self.assertAllowed(self._write("package.json", dict(REACT, version="2.0.0")))
        self.assertAllowed(self._edit("package.json", '"^18.3.1"', '"^18.3.2"'))

    def test_skip_dirs_are_never_judged(self):
        import cli_stack_scope
        from gates import housestack
        self.assertIs(housestack.SKIP_DIRS, cli_stack_scope.SKIP_DIRS)
        self.assertIn("e2e", cli_stack_scope.SKIP_DIRS)
        for rel in ("node_modules/react/package.json", "tests/fixtures/app/package.json",
                    "vendor/x/package.json", "dist/package.json", "e2e/package.json",
                    "testdata/x/package.json", "__tests__/package.json"):
            with self.subTest(rel=rel):
                self.assertAllowed(self._write(rel, REACT))

    def test_other_files_are_never_judged(self):
        self.assertAllowed(self._write("web/App.jsx", "import React from 'react'"))
        self.assertAllowed(self._write("package.json.bak", REACT))


class TestMarkers(_GateCase):
    def _claude(self, text, rel="CLAUDE.md"):
        p = self.proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_a_marker_naming_the_family_allows_and_is_logged(self):
        self._claude("# p\n" + MARKER + "\n")
        self.assertAllowed(self._write("web/package.json", REACT))
        self.assertIn("ALLOW-MARKER", self._log())
        self.assertIn("react", self._log())

    def test_one_marker_covers_only_its_own_technology(self):
        self._claude("<!-- airuleset:stack-exception Vue admin — owner o/x#9 -->\n")
        self.assertBlocked(self._write("web/package.json", REACT), "react")
        self.assertAllowed(self._write("w2/package.json", {"dependencies": {"nuxt": "3"}}))

    def test_a_marker_naming_a_meta_framework_covers_its_family(self):
        self._claude("<!-- airuleset:stack-exception Next.js site — "
                     "https://github.com/o/x/issues/5 -->\n")
        self.assertAllowed(self._write("web/package.json", REACT))

    def test_only_the_root_claude_md_marker_counts(self):
        # round 4 (item 6): as the rule says, the project CLAUDE.md at the root
        for rel in ("web/CLAUDE.md", ".claude/CLAUDE.md"):
            with self.subTest(rel=rel):
                self._claude(MARKER, rel)
                self.assertBlocked(self._write("web/package.json", REACT), "react")
                (self.proj / rel).unlink()

    def test_marker_above_the_git_root_does_not_count(self):
        (self.proj.parent / "CLAUDE.md").write_text(MARKER, encoding="utf-8")
        self.assertBlocked(self._write("package.json", REACT), "react")

    def test_marker_without_an_issue_or_url_is_no_exception(self):
        self._claude("<!-- airuleset:stack-exception React panel — owner approved -->")
        self.assertBlocked(self._write("package.json", REACT), "react")
        self._claude("<!-- airuleset:stack-exception react -->\n")
        self.assertBlocked(self._write("package.json", REACT), "react")


class TestScope(_GateCase):
    def test_a_non_house_project_is_never_judged(self):
        git(self.proj, "remote", "add", "origin", "git@github.com:zbynekdrlik/odoo-erp.git")
        self.assertAllowed(self._write("frontline/apps/new/package.json", REACT))

    def test_outside_any_repo_the_session_cwd_decides(self):
        # round 3 B: the scratchpad, where every `-F` body file goes
        odoo = git_repo(self.tmp / "odoo", "git@github.com:zbynekdrlik/odoo-erp.git")
        outside = self.tmp / "scratch"
        self.assertAllowed(self._write("x/package.json", REACT, cwd=odoo, root=outside))
        self.assertBlocked(self._write("y/package.json", REACT, cwd=self.proj,
                                       root=outside), "react")
        self.assertBlocked(self._write("z/package.json", REACT, cwd=self.tmp / "nowhere",
                                       root=outside), "react")

    def test_outside_any_repo_the_cwd_repo_s_frameworks_and_wording(self):
        # round 4 (item 3)
        app = self.proj / "apps" / "a" / "package.json"
        app.parent.mkdir(parents=True)
        app.write_text(json.dumps(REACT), encoding="utf-8")
        outside = self.tmp / "scratch"
        self.assertAllowed(self._write("x/package.json", REACT, root=outside))
        r = self._write("y/package.json", {"dependencies": {"vue": "3"}}, root=outside)
        self.assertBlocked(r, "vue", "new to the session's repo")

    def test_the_existing_set_is_the_git_file_set(self):
        # round 4 (item 3): `git ls-files -co --exclude-standard`, no own walk
        (self.proj / ".gitignore").write_text("build-out/\n", encoding="utf-8")
        ignored = self.proj / "build-out" / "package.json"
        ignored.parent.mkdir()
        ignored.write_text(json.dumps(REACT), encoding="utf-8")
        self.assertBlocked(self._write("web/package.json", REACT), "react")
        import cli_stack_scope
        from gates import housestack, housestate
        self.assertIs(housestack.repo_package_files, housestate.repo_package_files)
        self.assertIs(housestack.SCAN_DEPTH, cli_stack_scope.SCAN_DEPTH)

    def test_unreadable_registry_skips_with_a_log_line(self):
        # round 4 (item 4): unknown scope is never house
        from unittest import mock
        from gates import housestack
        with mock.patch.dict(os.environ, {"HOME": self.env["HOME"]}), \
                mock.patch.object(housestack, "REGISTRY", str(self.tmp / "missing.json")):
            reason = housestack.classify(
                "Write", {"file_path": str(self.proj / "package.json"),
                          "content": json.dumps(REACT)}, str(self.proj))
        self.assertEqual(reason, "")
        self.assertIn("registry unreadable", self._log())


class TestFailOpen(_GateCase):
    def test_unreadable_payload(self):
        self.assertFailOpen(self._run(None, raw="{not json"), "unreadable payload")

    def test_unparseable_new_content(self):
        self.assertFailOpen(self._write("package.json", '{ "dependencies": { "react": '),
                            "unparseable")

    def test_unparseable_existing_package_json(self):
        (self.proj / "package.json").write_text('{"name": "x",', encoding="utf-8")
        self.assertFailOpen(self._write("package.json", REACT), "unparseable existing")

    def test_edit_of_a_missing_file(self):
        self.assertFailOpen(self._edit("missing/package.json", "a", '"react": "1"'),
                            "cannot read")

    def test_edit_whose_old_string_is_not_in_the_file(self):
        # round 3 I: a CRLF or stale old_string fails open WITH a log line. The
        # file is read with universal newlines, so an LF old_string on a CRLF
        # file is still judged; a CRLF old_string never matches that text.
        (self.proj / "package.json").write_bytes(
            b'{\r\n  "name": "x",\r\n  "dependencies": {}\r\n}\r\n')
        r = self._edit("package.json", '"name": "x",\r\n  "dependencies": {}',
                       '"name": "x",\r\n  "dependencies": {"react": "18"}')
        self.assertFailOpen(r, "old_string not found")
        r = self._edit("package.json", '"name": "stale"', '"name": "y"')
        self.assertFailOpen(r, "old_string not found")
        r = self._edit("package.json", '"name": "x",\n  "dependencies": {}',
                       '"name": "x",\n  "dependencies": {"react": "18"}')
        self.assertBlocked(r, "react")

    def test_unreadable_claude_md(self):
        (self.proj / "CLAUDE.md").unlink()
        (self.proj / "CLAUDE.md").mkdir()          # exists, cannot be read
        self.assertFailOpen(self._write("package.json", REACT), "cannot read")


if __name__ == "__main__":
    unittest.main()
