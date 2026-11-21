"""#1333 — onboard-project's house-stack seed and audit (moved out of
test_house_stack_1333.py for the review-round-1 rework, ROZHODNUTÉ 1, 4, 7).

- Seed: a NEW project's CLAUDE.md gets the `## Stack` section only when it is
  a house candidate (Rust, or nothing detected yet); a Python / Node repo
  never gets a Rust pin.
- Audit (`cli_onboard_stack.audit_drift`, fed the registry row):
  `stack-unpinned` = a registry row without a (known) `stack`;
  `stack-deviation` = only for `house` rows: a JS-framework package.json, a
  hand-written JS web UI (not beside a Cargo.toml / Trunk.toml, not in a
  Leptos project, test files never counted), or non-house technology named
  in `## Stack`; an owner-approved marker silences it.
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

import cli_onboard as ob  # noqa: E402
from test_onboard_project import FakeRunner, init_repo  # noqa: E402
from test_onboard_tray_1198 import SshShellRunner  # noqa: E402

RULE = REPO / "rules" / "house-stack.md"
MARKER = "<!-- airuleset:stack-exception <what> — <owner ref> -->"
VALID_MARKER = ("<!-- airuleset:stack-exception Python CLI — "
                "owner zbynekdrlik/x#5 -->")
REACT_PKG = '{"name": "panel", "dependencies": {"react": "^18", "react-dom": "^18"}}'
HOUSE = {"name": "app", "stack": "house"}
LEPTOS_CARGO = '[package]\nname = "ui"\n\n[dependencies]\nleptos = { version = "0.8" }\n'


def _put(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _kinds(rows):
    return {r["kind"] for r in rows}


class TestStackSeed(unittest.TestCase):
    def test_new_project_claude_md_gets_the_stack_section(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(d)
            step = ob.step_claude_md(d, "demo", run=FakeRunner())
            self.assertEqual(step["status"], "applied")
            text = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(ob._stack.STACK_SECTION, text)
        self.assertLess(text.index("## Stack"), text.index("## Playbook router"))
        self.assertIn("`~/.claude/rules/house-stack.md`", text)
        self.assertIn(MARKER, text)

    def test_a_python_or_node_repo_gets_no_rust_pin(self):
        for stack in ("python", "node"):
            with self.subTest(stack=stack), tempfile.TemporaryDirectory() as d:
                init_repo(d)
                ob.step_claude_md(d, "demo", run=FakeRunner(), stack=stack)
                text = (Path(d) / "CLAUDE.md").read_text(encoding="utf-8")
                self.assertNotIn("## Stack", text)
                self.assertIn("## Playbook router", text)

    def test_onboard_project_passes_the_detected_stack(self):
        import inspect
        src = inspect.getsource(ob.onboard_project)
        self.assertIn("step_claude_md(target_path, name, host, run, dry_run, stack)", src)

    def test_seed_layers_match_the_rule_table(self):
        def layers(text):
            rows = [ln for ln in text.splitlines() if ln.startswith("| ")]
            return [r.split("|")[1].strip() for r in rows[1:]]
        self.assertEqual(layers(ob._stack.STACK_SECTION),
                         layers(RULE.read_text(encoding="utf-8")))

    def test_the_seed_template_is_no_exception_and_no_deviation(self):
        seed = ob._claude_md_skeleton("demo")
        self.assertFalse(ob._stack.has_stack_exception(seed))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ob._stack.audit_drift(d, run=FakeRunner(), claude_md=seed,
                                                   entry=HOUSE), [])


class TestStackExceptionMarker(unittest.TestCase):
    def test_marker_with_what_and_owner_ref_counts(self):
        for text in (VALID_MARKER,
                     "<!-- airuleset:stack-exception Odoo module – owner o/odoo#12 -->",
                     "x\n<!--airuleset:stack-exception n8n - https://github.com/o/x/issues/9 -->\n"):
            with self.subTest(text=text):
                self.assertTrue(ob._stack.has_stack_exception(text))

    def test_incomplete_markers_are_no_exception(self):
        for text in ("<!-- airuleset:stack-exception -->",
                     "<!-- airuleset:stack-exception react — owner said ok -->",
                     "<!-- airuleset:stack-exception react — owner 1.2.2026 -->",
                     "<!-- airuleset:stack-exception python -->",
                     "<!-- airuleset:stack-exception — owner ref -->",
                     "<!-- airuleset:stack-exception python — -->",
                     MARKER,
                     "<!-- airuleset:stack-exception python — owner\nref -->",
                     "<!-- airuleset:stack-exception-ok python — owner -->"):
            with self.subTest(text=text):
                self.assertFalse(ob._stack.has_stack_exception(text))

    def test_marker_regex_is_linear_on_many_unclosed_markers(self):
        import time
        text = "<!-- airuleset:stack-exception what — ref " * 20000
        t0 = time.monotonic()
        self.assertFalse(ob._stack.has_stack_exception(text))
        self.assertLess(time.monotonic() - t0, 2.0)


class TestStackAudit(unittest.TestCase):
    def _audit(self, files, claude_md="## Playbook router\n", run=None, entry=HOUSE):
        with tempfile.TemporaryDirectory() as d:
            for rel, text in files.items():
                _put(d, rel, text)
            return ob._stack.audit_drift(d, run=run or FakeRunner(),
                                         claude_md=claude_md, entry=entry)

    # --- stack-unpinned: the registry row, not the CLAUDE.md ------------- #
    def test_a_row_without_a_stack_is_unpinned_whatever_the_claude_md_says(self):
        for cm in ("## Playbook router\n", "## Stack\n\nRust.\n"):
            with self.subTest(cm=cm):
                rows = self._audit({}, claude_md=cm, entry={"name": "app"})
                self.assertEqual(_kinds(rows), {"stack-unpinned"})
                self.assertIn("projects-registry.json", rows[0]["detail"])

    def test_an_unknown_stack_id_is_unpinned(self):
        rows = self._audit({}, entry={"name": "app", "stack": "rails"})
        self.assertEqual(_kinds(rows), {"stack-unpinned"})
        self.assertIn("rails", rows[0]["detail"])

    def test_a_platform_project_is_never_a_deviation(self):
        for stack in ("odoo", "python-tooling", "sveltekit"):
            with self.subTest(stack=stack):
                self.assertEqual(self._audit({"web/package.json": REACT_PKG},
                                             entry={"name": "x", "stack": stack}), [])

    def test_a_house_project_without_a_stack_section_is_fine(self):
        self.assertEqual(self._audit({}), [])

    # --- stack-deviation: house projects only ---------------------------- #
    def test_react_package_json_is_a_deviation(self):
        rows = self._audit({"web/package.json": REACT_PKG})
        self.assertEqual(_kinds(rows), {"stack-deviation"})
        self.assertIn("web/package.json depends on react, react-dom",
                      rows[0]["detail"])

    def test_every_dependency_table_and_framework_counts(self):
        for table, dep in (("devDependencies", "vue"), ("dependencies", "svelte"),
                           ("peerDependencies", "@angular/core"),
                           ("dependencies", "lit"), ("dependencies", "solid-js")):
            with self.subTest(dep=dep):
                pkg = json.dumps({table: {dep: "1"}})
                self.assertIn("stack-deviation",
                              _kinds(self._audit({"package.json": pkg})))

    def test_a_stack_section_naming_other_technology_is_a_deviation(self):
        cm = "## Stack\n\n- UI: React 18 + Vite\n- API: FastAPI\n\n## Playbook router\n"
        rows = self._audit({}, claude_md=cm)
        self.assertEqual(_kinds(rows), {"stack-deviation"})
        self.assertIn("react", rows[0]["detail"].lower())
        self.assertIn("fastapi", rows[0]["detail"].lower())

    def test_a_stack_section_restating_house_rows_is_fine(self):
        cm = ob._stack.STACK_SECTION + "## Playbook router\n"
        self.assertEqual(self._audit({}, claude_md=cm), [])

    def test_marker_silences_the_deviation(self):
        cm = "## Stack\n\nReact panel.\n" + VALID_MARKER
        self.assertEqual(self._audit({"package.json": REACT_PKG}, claude_md=cm), [])

    def test_playwright_only_package_json_is_on_the_stack(self):
        pkg = '{"devDependencies": {"@playwright/test": "^1.50"}}'
        self.assertEqual(self._audit({"e2e/package.json": pkg}), [])

    def test_node_modules_is_never_read(self):
        self.assertEqual(self._audit({"node_modules/react/package.json": REACT_PKG}), [])

    def test_hand_written_web_js_app_is_a_deviation(self):
        rows = self._audit({"web/app.js": "x();\n" * 250})
        self.assertEqual(_kinds(rows), {"stack-deviation"})
        self.assertIn("hand-written JS web UI: 250 lines in web/app.js",
                      rows[0]["detail"])

    def test_js_beside_a_cargo_or_trunk_manifest_is_no_deviation(self):
        files = {"crates/x-ui/Cargo.toml": '[package]\nname = "x-ui"\n',
                 "crates/x-ui/public/helpers.js": "x\n" * 300,
                 "panel/Trunk.toml": "[build]\n",
                 "panel/static/interop.js": "x\n" * 300}
        self.assertEqual(self._audit(files), [])

    def test_js_in_a_leptos_project_is_no_deviation(self):
        # dcontroller: Leptos with JS helpers in public/
        files = {"crates/ui/Cargo.toml": LEPTOS_CARGO,
                 "public/helpers.js": "x\n" * 300, "public/audio.js": "x\n" * 300}
        self.assertEqual(self._audit(files), [])

    def test_test_files_never_count_toward_the_js_threshold(self):
        files = {"web/app.test.js": "x\n" * 300, "web/view.spec.ts": "x\n" * 300,
                 "web/__tests__/a.js": "x\n" * 300, "web/helper.js": "x\n" * 40}
        self.assertEqual(self._audit(files), [])

    def test_leptos_panel_glue_is_no_deviation(self):
        files = {"panel/sw.js": "self.addEventListener('fetch', f);\n" * 300,
                 "static/sw.js": "x\n" * 300,
                 "static/chart.min.js": "x\n" * 900,
                 "crates/app-ui/dist/app-ui.js": "x\n" * 900,
                 "static/helper.js": "x\n" * 40}
        self.assertEqual(self._audit(files), [])

    def test_unparseable_package_json_warns_and_is_skipped(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows = self._audit({"package.json": "{nope"})
        self.assertEqual(rows, [])
        self.assertIn("does not parse", err.getvalue())

    def test_failed_read_is_unverified_never_clean(self):
        def dead_find(argv, **kw):
            return subprocess.CompletedProcess(argv, 2, "", "find: boom")
        rows = self._audit({}, run=dead_find)
        self.assertEqual(_kinds(rows), {"unverified-stack"})

    def test_remote_check_is_one_ssh_call(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch(
                "cli_onboard_exec._local_hostname", return_value="test-box-1333"):
            _put(d, "web/package.json", REACT_PKG)
            run = SshShellRunner()
            reason = ob._stack.deviation(d, host="dev2", run=run)
        self.assertIn("react", reason)
        self.assertEqual(run.local_calls, [])
        self.assertEqual(len(run.ssh_calls), 1, run.ssh_calls)

    def test_audit_project_reads_the_registry_row(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(d, remote="https://github.com/zbynekdrlik/panel.git")
            _put(d, "CLAUDE.md", "## Playbook router\n")
            _put(d, "web/package.json", REACT_PKG)
            base = {"name": "panel", "host": "dev1", "path": d, "default_branch": "main"}
            house = _kinds(ob.audit_project(dict(base, stack="house")))
            bare = _kinds(ob.audit_project(base))
            odoo = _kinds(ob.audit_project(dict(base, stack="odoo")))
        self.assertIn("stack-deviation", house)
        self.assertNotIn("stack-unpinned", house)
        self.assertEqual({"stack-unpinned"}, bare & {"stack-unpinned", "stack-deviation"})
        self.assertFalse(odoo & {"stack-unpinned", "stack-deviation"})

    def test_onboard_skill_documents_the_stack_rows(self):
        skill = (REPO / "skills" / "onboard-project" / "SKILL.md").read_text(
            encoding="utf-8")
        for needle in ("`## Stack`", "stack-unpinned", "stack-deviation",
                       "airuleset:stack-exception", "`stack` field"):
            self.assertIn(needle, skill)


if __name__ == "__main__":
    unittest.main()
