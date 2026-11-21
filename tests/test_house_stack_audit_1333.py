"""#1333 — onboard-project's house-stack seed and audit (review rounds 1 + 3).

- Seed (round 3 H): a NEW house-candidate project's CLAUDE.md gets a `## Stack`
  section GENERATED from the rule's table rows (Layer | Applies when |
  Technology), never a hand copy; a Python / Node repo never a Rust pin.
- Audit (`cli_onboard_stack.audit_drift`, fed the registry row):
  `stack-unpinned` = a registry row without a known `stack` (an unregistered
  path says so); `stack-deviation` = only for `house` rows, one row per
  technology without its marker:
  * a JS-framework family in a package.json;
  * a template / UI crate (askama, tera, maud, yew, dioxus, sycamore) (round 3 C);
  * hand-written JS, exempt only in a dir with a `Trunk.toml` or in a crate that
    depends on leptos / wasm-bindgen (round 3 C: intercom's root-workspace
    `web/app.js` is a deviation, dcontroller's `crates/web/public` is not);
  * other technology in the `## Stack` TABLE's Technology cells, negated
    clauses ignored (round 3 D: wall-songs' "No … Electron" is clean).
- One marker per technology (round 3 E); `--audit` lists every marker and the
  gate's ALLOW-MARKER lines as notes that never count as drift.
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
REACT_MARKER = "<!-- airuleset:stack-exception React admin panel — owner zbynekdrlik/x#5 -->"
VALID_MARKER = ("<!-- airuleset:stack-exception Python CLI — "
                "owner zbynekdrlik/x#5 -->")
REACT_PKG = '{"name": "panel", "dependencies": {"react": "^18", "react-dom": "^18"}}'
HOUSE = {"name": "app", "stack": "house"}
LEPTOS_CARGO = '[package]\nname = "web"\n\n[dependencies]\nleptos = { version = "0.8" }\n'
PLAIN_CRATE = '[package]\nname = "x-ui"\n\n[dependencies]\nserde = "1"\n'
# wall-songs CLAUDE.md @23a3cf5, its `## Stack` section verbatim (rows trimmed)
WALL_SONGS_STACK = """## Stack — the house stack, BINDING (owner 21.11.2026)
Owner: "musi to byt podobny projekt ako ine nase rust projekty". Do not pick another technology.

| Layer | Technology |
|---|---|
| Language / layout | Rust, one Cargo workspace with `crates/<prefix>-*` (core, engine, service, tray, …) |
| Graphics engine | Native, low latency: `windows` crate (0.58, as the siblings) Direct3D 11 render/compositor on the GPU; video in/out over Spout (and NDI where needed), as songplayer `sp-gpu` and wall-bridge `wb-spout`; frames paced to the output's vblank like wall-bridge `wb-scanout`. No browser/Electron/web canvas renderer, no game engine. |
| Service / API | `axum` + `tokio`, status at `/api/status`, WebSocket for live control |
| Web UI | Leptos (CSR) compiled to WASM with Trunk, served by the axum service, version shown in the UI |
| CI / tests | GitHub Actions: fmt + clippy `-D warnings`, unit tests, Windows build, Playwright test of the web UI |

## Open at project start
"""


def _put(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _kinds(rows):
    return {r["kind"] for r in rows}


def _drift(rows):
    return {r["kind"] for r in rows} - set(ob._stack.NOTE_KINDS)


def _rule_rows():
    rows = [ln for ln in RULE.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| ")]
    return [[c.strip() for c in r.split("|")[1:4]] for r in rows[2:]]


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

    def test_the_seed_is_generated_from_the_rule_rows(self):
        rows = [ln for ln in ob._stack.STACK_SECTION.splitlines() if ln.startswith("| ")]
        self.assertEqual(rows[0], "| Layer | Applies when | Technology |")
        seeded = [[c.strip() for c in r.split("|")[1:4]] for r in rows[2:]]
        self.assertEqual(seeded, _rule_rows())

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

    def test_a_marker_covers_only_the_technology_it_names(self):
        self.assertTrue(ob._stack.exception_covers(REACT_MARKER, "react"))
        self.assertFalse(ob._stack.exception_covers(REACT_MARKER, "askama"))
        both = REACT_MARKER + "\n<!-- airuleset:stack-exception askama mails — #7 -->"
        self.assertTrue(ob._stack.exception_covers(both, "askama"))

    def test_marker_regex_is_linear_on_many_unclosed_markers(self):
        import time
        text = "<!-- airuleset:stack-exception what — ref " * 20000
        t0 = time.monotonic()
        self.assertFalse(ob._stack.has_stack_exception(text))
        self.assertLess(time.monotonic() - t0, 2.0)


class TestStackAudit(unittest.TestCase):
    def _audit(self, files, claude_md="## Playbook router\n", run=None, entry=HOUSE,
               gate_log=None):
        with tempfile.TemporaryDirectory() as d:
            for rel, text in files.items():
                _put(d, rel, text)
            log = None
            if gate_log is not None:
                log = Path(d) / ".gatelog"
                log.write_text(gate_log.replace("<ROOT>", d), encoding="utf-8")
            return ob._stack.audit_drift(d, run=run or FakeRunner(),
                                         claude_md=claude_md, entry=entry,
                                         gate_log=str(log) if log else None)

    # --- stack-unpinned: the registry row, not the CLAUDE.md ------------- #
    def test_a_row_without_a_stack_is_unpinned_whatever_the_claude_md_says(self):
        for cm in ("## Playbook router\n", "## Stack\n\nRust.\n"):
            with self.subTest(cm=cm):
                rows = self._audit({}, claude_md=cm, entry={"name": "fohmixer"})
                self.assertEqual(_kinds(rows), {"stack-unpinned"})
                self.assertIn("row has no `stack`", rows[0]["detail"])

    def test_an_unregistered_path_says_so(self):
        rows = self._audit({}, entry={"name": "not-a-registry-row"})
        self.assertEqual(_kinds(rows), {"stack-unpinned"})
        self.assertIn("not in projects-registry.json", rows[0]["detail"])

    def test_an_unknown_stack_id_is_unpinned(self):
        rows = self._audit({}, entry={"name": "fohmixer", "stack": "rails"})
        self.assertEqual(_kinds(rows), {"stack-unpinned"})
        self.assertIn("rails", rows[0]["detail"])

    def test_a_platform_project_is_never_a_deviation(self):
        for stack in ("odoo", "python-tooling", "sveltekit"):
            with self.subTest(stack=stack):
                self.assertEqual(self._audit({"web/package.json": REACT_PKG},
                                             entry={"name": "x", "stack": stack}), [])

    def test_a_house_project_without_a_stack_section_is_fine(self):
        self.assertEqual(self._audit({}), [])

    # --- frameworks and crates -------------------------------------------- #
    def test_react_package_json_is_a_deviation(self):
        rows = self._audit({"web/package.json": REACT_PKG})
        self.assertEqual(_kinds(rows), {"stack-deviation"})
        self.assertEqual(len(rows), 1)
        self.assertIn("react: web/package.json", rows[0]["detail"])

    def test_every_dependency_table_and_family_counts(self):
        for table, dep, fam in (("devDependencies", "vue", "vue"),
                                ("dependencies", "svelte", "svelte"),
                                ("peerDependencies", "@angular/core", "angular"),
                                ("dependencies", "lit", "lit"),
                                ("dependencies", "solid-js", "solid")):
            with self.subTest(dep=dep):
                rows = self._audit({"package.json": json.dumps({table: {dep: "1"}})})
                self.assertIn("%s: package.json" % fam, rows[0]["detail"])

    def test_forbidden_template_and_ui_crates(self):
        for crate in ("askama", "tera", "maud", "yew", "dioxus", "sycamore"):
            with self.subTest(crate=crate):
                manifest = '[package]\nname = "x"\n\n[dependencies]\n%s = "1"\n' % crate
                rows = self._audit({"crates/x/Cargo.toml": manifest})
                self.assertEqual(_kinds(rows), {"stack-deviation"})
                self.assertIn("%s: crates/x/Cargo.toml" % crate, rows[0]["detail"])

    def test_one_marker_silences_only_its_own_technology(self):
        files = {"package.json": REACT_PKG,
                 "Cargo.toml": '[package]\nname = "x"\n\n[dependencies]\naskama = "0.12"\n'}
        rows = self._audit(files, claude_md="## Playbook router\n" + REACT_MARKER)
        self.assertEqual(_drift(rows), {"stack-deviation"})
        details = " ".join(r["detail"] for r in rows if r["kind"] == "stack-deviation")
        self.assertIn("askama", details)
        self.assertNotIn("react:", details)

    # --- hand-written JS (round 3 C) -------------------------------------- #
    def test_intercom_root_workspace_web_app_is_a_deviation(self):
        files = {"Cargo.toml": '[workspace]\nmembers = ["hub", "vban"]\n',
                 "hub/Cargo.toml": '[package]\nname = "intercom-hub"\n\n'
                                   '[dependencies]\naxum = "0.8"\n',
                 "tray/Cargo.toml": '[package]\nname = "intercom-tray"\n\n'
                                    '[dependencies]\ntray-icon = "0.21"\n',
                 "web/app.js": "x();\n" * 1298, "web/sw.js": "x\n" * 40}
        rows = self._audit(files)
        self.assertEqual(_kinds(rows), {"stack-deviation"})
        self.assertIn("javascript: hand-written JS web UI: 1298 lines in web/app.js",
                      rows[0]["detail"])

    def test_js_in_a_trunk_dir_or_a_leptos_or_wasm_bindgen_crate_is_exempt(self):
        files = {"panel/Trunk.toml": "[build]\n", "panel/static/interop.js": "x\n" * 300,
                 "crates/web/Cargo.toml": LEPTOS_CARGO,
                 "crates/web/public/charts.js": "x\n" * 300,    # dcontroller
                 "crates/web/public/mapdrag.js": "x\n" * 300,
                 "crates/glue/Cargo.toml": '[package]\nname = "glue"\n\n'
                                           '[dependencies]\nwasm-bindgen = "0.2"\n',
                 "crates/glue/static/bridge.js": "x\n" * 300}
        self.assertEqual(self._audit(files), [])

    def test_js_in_a_plain_crate_is_not_exempt(self):
        files = {"crates/x-ui/Cargo.toml": PLAIN_CRATE,
                 "crates/x-ui/public/helpers.js": "x\n" * 300}
        rows = self._audit(files)
        self.assertEqual(_kinds(rows), {"stack-deviation"})

    def test_hand_written_web_js_app_is_a_deviation(self):
        rows = self._audit({"web/app.js": "x();\n" * 250})
        self.assertIn("hand-written JS web UI: 250 lines in web/app.js",
                      rows[0]["detail"])

    def test_test_files_and_glue_never_count(self):
        files = {"web/app.test.js": "x\n" * 300, "web/view.spec.ts": "x\n" * 300,
                 "web/__tests__/a.js": "x\n" * 300, "e2e/static/x.js": "x\n" * 300,
                 "static/sw.js": "x\n" * 300, "static/chart.min.js": "x\n" * 900,
                 "crates/app-ui/dist/app-ui.js": "x\n" * 900,
                 "web/helper.js": "x\n" * 40}
        self.assertEqual(self._audit(files), [])

    # --- the `## Stack` table (round 3 D) -------------------------------- #
    def test_wall_songs_stack_section_is_clean(self):
        self.assertEqual(self._audit({}, claude_md=WALL_SONGS_STACK), [])

    def test_only_the_technology_cells_of_the_stack_table_count(self):
        cm = ("## Stack\n\nWe once tried React and FastAPI; never again.\n\n"
              "| Layer | Technology |\n|---|---|\n| Web UI | React 18 + Vite |\n"
              "| API | FastAPI; no Django |\n| Desktop | .NET 8 tray, ASP.NET Core |\n\n"
              "## Playbook router\n")
        rows = self._audit({}, claude_md=cm)
        self.assertEqual(_drift(rows), {"stack-deviation"})
        details = " ".join(r["detail"] for r in rows)
        for name in ("react", "fastapi", ".net", "asp.net"):
            self.assertIn(name, details.lower())
        self.assertNotIn("django", details.lower())

    def test_a_stack_section_restating_house_rows_is_fine(self):
        cm = ob._stack.STACK_SECTION + "## Playbook router\n"
        self.assertEqual(self._audit({}, claude_md=cm), [])

    # --- markers and the gate log are listed (round 3 E) ----------------- #
    def test_markers_and_allow_marker_lines_are_listed_as_notes(self):
        log = ("1 ALLOW-MARKER `<ROOT>/web/package.json` framework=react claude_md=x\n"
               "2 ALLOW-MARKER `/elsewhere/package.json` framework=vue claude_md=y\n"
               "3 BLOCK Write <ROOT>/package.json\n")
        rows = self._audit({"web/package.json": REACT_PKG},
                           claude_md="## Playbook router\n" + REACT_MARKER, gate_log=log)
        self.assertEqual(_drift(rows), set())
        notes = [r for r in rows if r["kind"] in ob._stack.NOTE_KINDS]
        self.assertIn("React admin panel — owner zbynekdrlik/x#5",
                      [r["detail"] for r in notes if r["kind"] == "stack-exception"])
        uses = [r["detail"] for r in notes if r["kind"] == "stack-exception-use"]
        self.assertEqual(len(uses), 1)
        self.assertIn("framework=react", uses[0])

    def test_notes_never_flip_the_audit_to_drift(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ob._print_audit({"app": [{"kind": "stack-exception", "detail": "React — #5"}]})
        self.assertIn("no drift across 1 project", out.getvalue())
        self.assertIn("React — #5", out.getvalue())

    # --- reading the target ------------------------------------------------ #
    def test_playwright_only_package_json_is_on_the_stack(self):
        pkg = '{"devDependencies": {"@playwright/test": "^1.50"}}'
        self.assertEqual(self._audit({"tools/package.json": pkg}), [])

    def test_node_modules_is_never_read(self):
        self.assertEqual(self._audit({"node_modules/react/package.json": REACT_PKG}), [])

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
            items = ob._stack.deviation(d, host="dev2", run=run)
        self.assertIn("react", [tech for tech, _ in items])
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
                       "unverified-stack", "stack-exception", "airuleset:stack-exception",
                       "`stack` field"):
            self.assertIn(needle, skill)


if __name__ == "__main__":
    unittest.main()
