"""#1333 — a PreToolUse Write|Edit gate refuses a package.json that brings a JS
UI framework into a project without an owner-approved stack exception.

The house stack's web UI is Leptos/WASM (``rules/house-stack.md``). The one
mechanically checkable moment a session leaves it is the package.json that
adds react / vue / svelte / angular / preact / solid-js (or their meta
frameworks). ``hooks/block-frontend-framework.sh`` -> ``gates.housestack``
refuses that write unless the project's CLAUDE.md (any level up to the git
root, or ``.claude/CLAUDE.md``) carries
``<!-- airuleset:stack-exception <what> — <owner ref> -->``. A package.json
that already had the framework is never blocked for unrelated edits (only an
ADDED framework is), and an unreadable payload or file fails OPEN with a
logged reason, like the sibling Write|Edit gates.

Every hook run uses a hermetic HOME (#1046) so the audit log lands in a temp
dir, never the box's real ``~/.claude``.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

HOOK = REPO / "hooks" / "block-frontend-framework.sh"
MARKER = "<!-- airuleset:stack-exception React admin panel — owner zbynekdrlik/x#5 -->"
REACT = {"name": "panel", "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"}}
PLAYWRIGHT = {"name": "e2e", "devDependencies": {"@playwright/test": "^1.50.0"}}


class _GateCase(unittest.TestCase):
    def setUp(self):
        self.env = hermetic_hook_env(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proj = Path(tmp.name) / "proj"
        (self.proj / ".git").mkdir(parents=True)
        (self.proj / "CLAUDE.md").write_text("# proj\n\n## Playbook router\n",
                                             encoding="utf-8")

    def _run(self, tool_input, tool="Write", raw=None):
        payload = raw if raw is not None else json.dumps({
            "session_id": "fg1333", "tool_name": tool,
            "cwd": str(self.proj), "tool_input": tool_input})
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, env=self.env,
                              timeout=30)

    def _write(self, rel, doc):
        content = doc if isinstance(doc, str) else json.dumps(doc, indent=2)
        return self._run({"file_path": str(self.proj / rel), "content": content})

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


class TestHookWiring(unittest.TestCase):
    def test_hook_exists_and_is_executable(self):
        self.assertTrue(HOOK.exists(), "missing hook %s" % HOOK)
        self.assertTrue(os.access(HOOK, os.X_OK), "hook must be executable")

    def test_hook_runs_the_gate_module_with_dash_p(self):
        src = HOOK.read_text(encoding="utf-8")
        self.assertIn("python3 -P -m gates.housestack", src)
        self.assertIn("set -euo pipefail", src)
        self.assertIn("Dry-run:", src)

    def test_registered_on_write_and_edit(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text(encoding="utf-8"))
        wired = {"Write": False, "Edit": False}
        for block in cfg["hooks"]["PreToolUse"]:
            if block.get("matcher") in wired:
                for h in block.get("hooks", []):
                    if "block-frontend-framework.sh" in h.get("command", ""):
                        wired[block["matcher"]] = True
                        self.assertLessEqual(h.get("timeout", 0), 10)
        self.assertEqual(wired, {"Write": True, "Edit": True})


class TestBlocks(_GateCase):
    def test_new_react_package_json_is_refused(self):
        r = self._write("web/package.json", REACT)
        self.assertBlocked(r, "react", "react-dom", "house-stack.md",
                           "airuleset:stack-exception", "owner")
        self.assertIn("BLOCK", self._log())

    def test_every_framework_and_dependency_table_is_refused(self):
        for table, dep in (("dependencies", "vue"), ("devDependencies", "svelte"),
                           ("peerDependencies", "@angular/core"),
                           ("dependencies", "angular"), ("dependencies", "preact"),
                           ("dependencies", "solid-js"), ("dependencies", "next")):
            with self.subTest(dep=dep):
                self.assertBlocked(self._write("ui/package.json", {table: {dep: "1"}}),
                                   dep)

    def test_adding_a_second_framework_to_an_existing_app_is_refused(self):
        (self.proj / "package.json").write_text(json.dumps(REACT), encoding="utf-8")
        doc = dict(REACT, dependencies=dict(REACT["dependencies"], vue="^3"))
        r = self._write("package.json", doc)
        self.assertBlocked(r, "vue")
        self.assertNotIn("react-dom", r.stderr.split("adds")[1].split("\n")[0])

    def test_edit_that_adds_react_is_refused(self):
        (self.proj / "package.json").write_text(
            json.dumps(PLAYWRIGHT, indent=2), encoding="utf-8")
        r = self._edit("package.json", '"@playwright/test": "^1.50.0"',
                       '"@playwright/test": "^1.50.0", "react": "^18"')
        self.assertBlocked(r, "react")

    def test_marker_text_without_owner_ref_does_not_open_the_gate(self):
        (self.proj / "CLAUDE.md").write_text(
            "<!-- airuleset:stack-exception react -->\n", encoding="utf-8")
        self.assertBlocked(self._write("package.json", REACT), "react")


class TestAllows(_GateCase):
    def test_marker_in_the_project_claude_md_allows(self):
        (self.proj / "CLAUDE.md").write_text("# p\n" + MARKER + "\n", encoding="utf-8")
        self.assertAllowed(self._write("web/package.json", REACT))

    def test_marker_in_a_nested_or_dot_claude_md_allows(self):
        for rel in ("web/CLAUDE.md", ".claude/CLAUDE.md"):
            with self.subTest(rel=rel):
                p = self.proj / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(MARKER, encoding="utf-8")
                self.assertAllowed(self._write("web/package.json", REACT))
                p.unlink()

    def test_marker_above_the_git_root_does_not_count(self):
        (self.proj.parent / "CLAUDE.md").write_text(MARKER, encoding="utf-8")
        self.assertBlocked(self._write("package.json", REACT), "react")

    def test_house_stack_e2e_package_json_is_allowed(self):
        self.assertAllowed(self._write("e2e/package.json", PLAYWRIGHT))

    def test_unrelated_rewrite_of_an_existing_react_app_is_allowed(self):
        (self.proj / "package.json").write_text(json.dumps(REACT), encoding="utf-8")
        self.assertAllowed(self._write("package.json", dict(REACT, version="2.0.0")))
        self.assertAllowed(self._edit("package.json", '"^18.3.1"', '"^18.3.2"'))

    def test_vendored_and_fixture_paths_are_allowed(self):
        for rel in ("node_modules/react/package.json",
                    "tests/fixtures/app/package.json", "vendor/x/package.json"):
            with self.subTest(rel=rel):
                self.assertAllowed(self._write(rel, REACT))

    def test_other_files_are_never_judged(self):
        self.assertAllowed(self._write("web/App.jsx", "import React from 'react'"))
        self.assertAllowed(self._write("package.json.bak", REACT))


class TestFailOpen(_GateCase):
    def test_unreadable_payload_allows_with_a_logged_reason(self):
        r = self._run(None, raw="{not json")
        self.assertAllowed(r)
        self.assertIn("fail-open", r.stderr)
        self.assertIn("unreadable payload", self._log())

    def test_unparseable_package_json_allows_with_a_logged_reason(self):
        r = self._write("package.json", "{ \"dependencies\": { \"react\": ")
        self.assertAllowed(r)
        self.assertIn("unparseable", self._log())

    def test_edit_of_an_unreadable_file_allows_with_a_logged_reason(self):
        r = self._edit("missing/package.json", "a", '"react": "1"')
        self.assertAllowed(r)
        self.assertIn("cannot read", self._log())


if __name__ == "__main__":
    unittest.main()
