"""#1333 — the house stack reaches the session at the moment a stack is chosen.

Owner, 21.11.2026: new projects keep starting on ad-hoc technology and must be
rebuilt. Root cause: the stack lived only in two ``paths:``-scoped rules
(``*.rs`` / ``Cargo.toml``), which never load in an empty repo or a JS project,
and one of them was not even in the install profile. This file locks the four
non-hook surfaces of the fix:

1. ONE rule body, ``rules/house-stack.md``, replacing the two old rules.
2. The always-on pointer in ``modules/quality/architecture-first.md``.
3. Situational trigger rows (design comment, first app scaffold file).
4. ``onboard-project``: ``## Stack`` seed + ``stack-unpinned`` /
   ``stack-deviation`` audit.

The PreToolUse frontend-framework gate has its own file
(``test_frontend_gate_1333.py``).
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

import airuleset  # noqa: E402
import cli_onboard as ob  # noqa: E402
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)
from test_onboard_project import FakeRunner, init_repo  # noqa: E402
from test_onboard_tray_1198 import SshShellRunner  # noqa: E402

RULE = REPO / "rules" / "house-stack.md"
OLD_NAMES = ("rust-web-" + "stack", "rust-web-" + "tray")
MARKER = "<!-- airuleset:stack-exception <what> — <owner ref> -->"


def _norm(text):
    return " ".join(text.split())


# --------------------------------------------------------------------------- #
# 1) One rule body replaces the two old rules.
# --------------------------------------------------------------------------- #
class TestHouseStackRule(unittest.TestCase):
    def setUp(self):
        self.raw = RULE.read_text(encoding="utf-8")
        self.text = _norm(self.raw)

    def test_old_rule_files_are_gone(self):
        for name in OLD_NAMES:
            self.assertFalse((REPO / "rules" / (name + ".md")).exists(), name)

    def test_no_reference_to_the_old_rules_remains(self):
        hits = []
        for dirpath, dirnames, filenames in os.walk(REPO):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "worktrees", "__pycache__",
                                        "node_modules", "target")]
            for fn in filenames:
                p = Path(dirpath) / fn
                if p.suffix not in (".md", ".py", ".sh", ".profile", ".conf",
                                    ".json", ".toml"):
                    continue
                try:
                    body = p.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for name in OLD_NAMES:
                    if name + ".md" in body:
                        hits.append("%s: %s" % (p.relative_to(REPO), name))
        self.assertEqual(hits, [])

    def test_rule_is_installed_through_the_universal_profile(self):
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        _modules, rules = airuleset.categorize_entries(entries)
        self.assertIn("rules/house-stack.md", rules)
        for name in OLD_NAMES:
            self.assertNotIn("rules/%s.md" % name, rules)

    def test_paths_cover_rust_and_web_files(self):
        self.assertTrue(self.raw.startswith("---\npaths:\n"))
        head = self.raw.split("---", 2)[1]
        for glob in ('"**/*.rs"', '"**/Cargo.toml"', '"**/Trunk.toml"',
                     '"**/index.html"', '"**/crates/**"', '"**/src/**/*.rs"'):
            self.assertIn(glob, head)

    def test_every_layer_is_pinned(self):
        for needle in (
                "Rust Cargo workspace", "`crates/<prefix>-*`",
                "axum + tokio", "`GET /api/status`", "WebSocket",
                "Leptos (latest 0.8.x) CSR → WASM", "built by Trunk", "PWA",
                "version visible on the dashboard",
                "`tray-icon` in a `<prefix>-tray` crate",
                "`windows` crate Direct3D 11", "Spout / NDI in and out",
                "paced on vblank",
                "`cargo build --release --target x86_64-pc-windows-gnu`",
                "system-wide", "scheduled tasks",
                "`C:\\ProgramData\\<App>\\logs`"):
            self.assertIn(needle, self.text, needle)

    def test_reference_projects_are_named(self):
        for needle in ("fohmixer `crates/fohmixer-ui`",
                       "fohmixer `fohmixer-hub`, songplayer `sp-server`",
                       "wall-bridge `crates/wb-tray`", "songplayer `sp-gpu`",
                       "`wb-spout`", "`wb-scanout`", "Spout senders",
                       "Tauri 2: fohmixer, iemmixer, songplayer"):
            self.assertIn(needle, self.text, needle)

    def test_references_are_shapes_never_old_versions(self):
        self.assertIn("**never copy a reference project's older version**",
                      self.text)
        self.assertIn("Known deviation, never a reference: wall-bridge's dashboard "
                      "(a static `index.html` from `wb-service`, no WebSocket)",
                      self.text)

    # --- review round 1, ROZHODNUTÉ 1 + 5 ---------------------------------- #
    def test_scope_is_a_new_app_and_existing_projects_keep_their_stack(self):
        for needle in (
                "**A NEW standalone app or deployable (a new repo, or a new "
                "deployable inside a house project) is built on the house stack",
                "**An existing project follows its established stack:** its "
                "platform is the `stack` field of its row in airuleset's "
                "`projects-registry.json`",
                "set by airuleset only, never by a project session"):
            self.assertIn(needle, self.text, needle)

    def test_every_row_says_when_it_applies(self):
        rows = [ln for ln in self.raw.splitlines() if ln.startswith("| ")]
        self.assertEqual(rows[0].split("|")[2].strip(), "Applies when")
        applies = {r.split("|")[1].strip(): r.split("|")[2].strip()
                   for r in rows[2:]}
        self.assertEqual(applies["Tray"], "a Windows desktop app with a UI or video")
        self.assertEqual(applies["Install"], "a Windows desktop app with a UI or video")
        self.assertEqual(applies["Graphics engine"], "a Windows app that renders video")
        self.assertIn("A Linux service is the Service + Web UI rows.", self.text)

    def test_both_tray_variants_are_accepted(self):
        self.assertIn("service-only app: `tray-icon` in a `<prefix>-tray` crate; "
                      "app with its own desktop window: the Tauri 2 tray", self.text)
        self.assertIn("Both variants are accepted.", self.text)

    def test_windows_release_builds_come_from_ci(self):
        self.assertIn("release artefacts come from the CI Windows runner; the local "
                      "check is the dev1 cross-build", self.text)

    def test_stack_section_only_restates_house_rows(self):
        self.assertIn("A project `## Stack` section may only restate house rows; "
                      "other technology in it needs the marker too.", self.text)

    def test_exception_needs_the_owner_marker(self):
        self.assertIn("Any other technology in a house project only as an "
                      "owner-approved exception", self.text)
        self.assertIn(MARKER, self.raw)
        self.assertIn("where `<owner ref>` is the `#N` or GitHub URL in which the "
                      "owner approved it", self.text)
        self.assertIn("Never add the marker on your own judgement", self.text)

    def test_read_before_choosing_in_an_empty_repo(self):
        self.assertIn("Read this BEFORE choosing any technology, in an empty "
                      "repo too", self.text)

    # --- every binding statement of the old rust-web-stack rule ---------- #
    def test_web_ui_statements_survive_the_merge(self):
        for needle in (
                "Every web UI of a house app MUST use Leptos compiled to WASM, "
                "served by Axum",
                "No exception without the owner marker above",
                "a clearly better option goes through Stack evolution, never a "
                "silent switch",
                "`trunk build --release` → static `dist/`",
                "**SSR** (cargo-leptos on Axum) only when the project "
                "explicitly requires server functions",
                "If you think \"it would be easier without WASM\" — STOP. "
                "That thought is wrong."):
            self.assertIn(needle, self.text, needle)

    def test_forbidden_alternatives_survive_the_merge(self):
        start = self.text.index("**FORBIDDEN** for a web UI")
        window = self.text[start:start + 500]
        for needle in ("plain HTML/JS/CSS", "React / Vue / Svelte / Angular",
                       "Yew / Dioxus / Sycamore", "Tera, Askama, Maud",
                       "htmx"):
            self.assertIn(needle, window, needle)

    def test_three_test_tiers_survive_the_merge(self):
        start = self.text.index("Tests, all three tiers")
        window = self.text[start:start + 400]
        for needle in ("`cargo test`", "`wasm-bindgen-test`", "Playwright",
                       "zero browser-console errors"):
            self.assertIn(needle, window, needle)


    # --- owner 21.11.: the stack is a living default, never a freeze ------ #
    def test_every_table_row_carries_a_last_reviewed_cell(self):
        rows = [ln for ln in self.raw.splitlines() if ln.startswith("| ")]
        self.assertEqual(rows[0].split("|")[-2].strip(), "Last reviewed")
        body = rows[1:]
        self.assertEqual(len(body), 7, body)
        for row in body:
            cell = row.split("|")[-2].strip()
            self.assertRegex(cell, r"^\d{1,2}\.\d{1,2}\.\d{4}", row)

    def _evolution(self):
        start = self.text.index("#### Stack evolution")
        return self.text[start:self.text.index("#### Web UI", start)]

    def test_evolution_checks_read_sources_at_every_design_step(self):
        sec = self._evolution()
        self.assertIn("At every design step (`Architektúra:`)**, for each layer "
                      "the work touches", sec)
        self.assertIn("from READ sources (release notes, the crate's "
                      "docs/source, benchmarks — never memory)", sec)

    def test_evolution_never_silent_either_way(self):
        sec = self._evolution()
        self.assertIn("never silently use it, never silently stay on the "
                      "weaker one", sec)
        self.assertIn("(what, why better for this need, cost of moving, "
                      "affected projects)", sec)
        self.assertIn("The owner decides; until then build on the current row.", sec)

    def test_evolution_proposal_reaches_the_owner(self):
        sec = self._evolution()
        self.assertIn("label that ticket `needs-decision` (it reaches the owner's "
                      "`U`)", sec)
        self.assertIn("the ONE canonical airuleset ticket labelled `stack-proposal`",
                      sec)
        self.assertIn("an app-token stream files through `python3 "
                      "~/devel/airuleset/airuleset.py gk-request --repo "
                      "zbynekdrlik/airuleset`", sec)

    def test_evolution_records_both_outcomes(self):
        sec = self._evolution()
        self.assertIn("airuleset updates the row and its \"Last reviewed\" "
                      "cell", sec)
        self.assertIn("each affected project gets its own upgrade ticket", sec)
        self.assertIn("one line under **Considered** below with the reason; "
                      "never re-raise it without new evidence", sec)
        self.assertIn("**Considered (rejected, with reason):**", sec)


# --------------------------------------------------------------------------- #
# 2) The always-on pointer: inside the framework-first bullet, one sentence.
# --------------------------------------------------------------------------- #
class TestArchitectureFirstPointer(unittest.TestCase):
    def test_pointer_sits_in_the_framework_first_bullet(self):
        raw = (REPO / "modules" / "quality" / "architecture-first.md").read_text(
            encoding="utf-8")
        bullet = next(ln for ln in raw.splitlines()
                      if ln.startswith("- **Framework-first for new components"))
        self.assertIn("**House stack (#1333):** a NEW standalone app/deployable "
                      "defaults to `~/.claude/rules/house-stack.md` (read it before "
                      "choosing; deviation only with its owner-approved "
                      "stack-exception marker)", bullet)
        self.assertIn("an existing project keeps its stack (`stack` in airuleset's "
                      "`projects-registry.json`)", bullet)
        self.assertEqual(raw.count("house-stack.md"), 1)


# --------------------------------------------------------------------------- #
# 3) Situational triggers: the design comment and the first scaffold file.
# --------------------------------------------------------------------------- #
INJECTOR = REPO / "hooks" / "inject-situational-rule.sh"
STACK_MARK = "House Stack — the default for every NEW app"
INVESTIGATE_MARK = "skills/investigate-existing-first/SKILL.md"
DASHBOARD_MARK = "skills/version-on-dashboard/SKILL.md"


class TestHouseStackTriggers(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        # the injector's once-per-session markers live under TMPDIR
        self.env = hermetic_hook_env(self, TMPDIR=td.name)
        self.n = 0

    def _ctx(self, tool_input, tool="Bash", session=None):
        self.n += 1
        payload = json.dumps({
            "session_id": session or "hs1333-%s-%d" % (self.id()[-20:], self.n),
            "tool_name": tool, "tool_input": tool_input})
        r = subprocess.run(["bash", str(INJECTOR)], input=payload,
                           capture_output=True, text=True, env=self.env,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def _write(self, path, content="x"):
        return self._ctx({"file_path": path, "content": content}, tool="Write")

    def test_heredoc_design_comment_injects(self):
        cmd = ("gh issue comment 7 -R zbynekdrlik/wall-songs --body-file - <<'EOF'\n"
               "## Design\nArchitektúra: one axum service + Leptos UI\nEOF")
        self.assertIn(STACK_MARK, self._ctx({"command": cmd}))

    def test_inline_issue_create_with_architektura_injects(self):
        cmd = 'gh issue create --title "x" --body "Architektura: a new tool"'
        self.assertIn(STACK_MARK, self._ctx({"command": cmd}))

    def test_issue_comment_without_a_design_section_stays_quiet(self):
        cmd = 'gh issue comment 7 --body "status update, CI green"'
        self.assertNotIn(STACK_MARK, self._ctx({"command": cmd}))

    def test_architektura_outside_a_gh_issue_command_stays_quiet(self):
        cmd = "grep -rn 'Architektúra:' modules/"
        self.assertNotIn(STACK_MARK, self._ctx({"command": cmd}))

    def test_design_body_file_written_for_dash_f_injects(self):
        ctx = self._write("/tmp/scratch/design-7.md",
                          "## Design\nArchitektúra: service + tray\n")
        self.assertIn(STACK_MARK, ctx)

    def test_first_scaffold_files_inject(self):
        for name in ("package.json", "Cargo.toml", "index.html", "Trunk.toml",
                     "pyproject.toml", "requirements.txt"):
            with self.subTest(name=name):
                ctx = self._write("/home/u/devel/new-app/" + name)
                self.assertIn(STACK_MARK, ctx, name)

    def test_member_crate_manifest_injects(self):
        self.assertIn(STACK_MARK,
                      self._write("/home/u/devel/app/crates/app-ui/Cargo.toml"))

    def test_test_fixture_and_vendored_paths_stay_quiet(self):
        for path in ("/r/tests/fixtures/x/package.json", "/r/node_modules/a/package.json",
                     "/r/target/debug/build/x/Cargo.toml", "/r/test/index.html"):
            with self.subTest(path=path):
                self.assertNotIn(STACK_MARK, self._write(path))

    def test_lookalike_names_and_edits_stay_quiet(self):
        for path in ("/r/my-package.json", "/r/package.json.bak", "/r/src/main.rs"):
            with self.subTest(path=path):
                self.assertNotIn(STACK_MARK, self._write(path))
        edit = self._ctx({"file_path": "/r/package.json", "old_string": "a",
                          "new_string": "b"}, tool="Edit")
        self.assertNotIn(STACK_MARK, edit)

    def test_injected_once_per_session(self):
        sid = "hs1333-dedup"
        first = self._ctx({"file_path": "/r/b/package.json", "content": "{}"},
                          tool="Write", session=sid)
        again = self._ctx({"file_path": "/r/b/Cargo.toml", "content": "x"},
                          tool="Write", session=sid)
        self.assertIn(STACK_MARK, first)
        self.assertNotIn(STACK_MARK, again)

    def test_cargo_manifest_cofits_with_investigate_existing_first(self):
        ctx = self._write("/home/u/devel/new-app/Cargo.toml", "[workspace]\n")
        self.assertIn(STACK_MARK, ctx)
        self.assertIn(INVESTIGATE_MARK, ctx)

    def test_index_html_cofits_with_version_on_dashboard(self):
        ctx = self._write("/home/u/devel/new-app/index.html", "<html></html>")
        self.assertIn(STACK_MARK, ctx)
        self.assertIn(DASHBOARD_MARK, ctx)

    def test_house_stack_is_a_raw_match_topic(self):
        src = INJECTOR.read_text(encoding="utf-8")
        start = src.index("RAW_BASH_MATCH_TOPICS = {")
        self.assertIn('"house-stack"', src[start:src.index("}", start)])


# --------------------------------------------------------------------------- #
# 4) onboard-project: the `## Stack` seed and the stack audit.
# --------------------------------------------------------------------------- #
VALID_MARKER = ("<!-- airuleset:stack-exception Python CLI — "
                "owner zbynekdrlik/x#5 -->")
REACT_PKG = '{"name": "panel", "dependencies": {"react": "^18", "react-dom": "^18"}}'


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

    def test_seed_layers_match_the_rule_table(self):
        def layers(text):
            rows = [ln for ln in text.splitlines() if ln.startswith("| ")]
            return [r.split("|")[1].strip() for r in rows[1:]]
        self.assertEqual(layers(ob._stack.STACK_SECTION),
                         layers(RULE.read_text(encoding="utf-8")))

    def test_a_seeded_project_is_pinned_and_the_template_is_no_exception(self):
        seed = ob._claude_md_skeleton("demo")
        self.assertTrue(ob._stack.has_stack_section(seed))
        self.assertFalse(ob._stack.has_stack_exception(seed))
        with tempfile.TemporaryDirectory() as d:
            rows = ob._stack.audit_drift(d, run=FakeRunner(), claude_md=seed)
        self.assertEqual(rows, [])


class TestStackExceptionMarker(unittest.TestCase):
    def test_marker_with_what_and_owner_ref_counts(self):
        for text in (VALID_MARKER,
                     "<!-- airuleset:stack-exception Odoo module – owner 1.2.2026 -->",
                     "x\n<!--airuleset:stack-exception n8n flow - owner ticket 9 -->\n"):
            with self.subTest(text=text):
                self.assertTrue(ob._stack.has_stack_exception(text))

    def test_incomplete_markers_are_no_exception(self):
        for text in ("<!-- airuleset:stack-exception -->",
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
    def _audit(self, files, claude_md="## Playbook router\n", run=None):
        with tempfile.TemporaryDirectory() as d:
            for rel, text in files.items():
                _put(d, rel, text)
            return ob._stack.audit_drift(d, run=run or FakeRunner(),
                                         claude_md=claude_md)

    def test_claude_md_without_stack_section_is_unpinned(self):
        rows = self._audit({})
        self.assertEqual(_kinds(rows), {"stack-unpinned"})
        self.assertIn("rules/house-stack.md", rows[0]["detail"])

    def test_stack_section_pins_it(self):
        self.assertEqual(self._audit({}, claude_md="## Stack\n\nRust.\n"), [])

    def test_absent_claude_md_is_not_double_reported(self):
        self.assertEqual(self._audit({}, claude_md=None), [])

    def test_react_package_json_is_a_deviation(self):
        rows = self._audit({"web/package.json": REACT_PKG},
                           claude_md="## Stack\n")
        self.assertEqual(_kinds(rows), {"stack-deviation"})
        self.assertIn("web/package.json depends on react, react-dom",
                      rows[0]["detail"])

    def test_every_dependency_table_and_framework_counts(self):
        for table, dep in (("devDependencies", "vue"), ("dependencies", "svelte"),
                           ("peerDependencies", "@angular/core"),
                           ("dependencies", "preact"), ("dependencies", "solid-js")):
            with self.subTest(dep=dep):
                pkg = json.dumps({table: {dep: "1"}})
                rows = self._audit({"package.json": pkg}, claude_md="## Stack\n")
                self.assertIn("stack-deviation", _kinds(rows))

    def test_marker_silences_unpinned_and_deviation(self):
        rows = self._audit({"package.json": REACT_PKG},
                           claude_md="## Playbook router\n" + VALID_MARKER)
        self.assertEqual(rows, [])

    def test_playwright_only_package_json_is_on_the_stack(self):
        pkg = '{"devDependencies": {"@playwright/test": "^1.50"}}'
        self.assertEqual(self._audit({"e2e/package.json": pkg},
                                     claude_md="## Stack\n"), [])

    def test_node_modules_is_never_read(self):
        self.assertEqual(self._audit({"node_modules/react/package.json": REACT_PKG},
                                     claude_md="## Stack\n"), [])

    def test_hand_written_web_js_app_is_a_deviation(self):
        rows = self._audit({"web/app.js": "x();\n" * 250}, claude_md="## Stack\n")
        self.assertEqual(_kinds(rows), {"stack-deviation"})
        self.assertIn("hand-written JS web UI: 250 lines in web/app.js",
                      rows[0]["detail"])

    def test_leptos_panel_glue_is_no_deviation(self):
        files = {"panel/sw.js": "self.addEventListener('fetch', f);\n" * 300,
                 "static/sw.js": "x\n" * 300,
                 "static/chart.min.js": "x\n" * 900,
                 "crates/app-ui/dist/app-ui.js": "x\n" * 900,
                 "static/helper.js": "x\n" * 40}
        self.assertEqual(self._audit(files, claude_md="## Stack\n"), [])

    def test_unparseable_package_json_warns_and_is_skipped(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rows = self._audit({"package.json": "{nope"}, claude_md="## Stack\n")
        self.assertEqual(rows, [])
        self.assertIn("does not parse", err.getvalue())

    def test_failed_read_is_unverified_never_clean(self):
        def dead_find(argv, **kw):
            return subprocess.CompletedProcess(argv, 2, "", "find: boom")
        rows = self._audit({}, claude_md="## Stack\n", run=dead_find)
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

    def test_audit_project_reports_the_stack_rows(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(d, remote="https://github.com/zbynekdrlik/panel.git")
            _put(d, "CLAUDE.md", "## Playbook router\n")
            _put(d, "web/package.json", REACT_PKG)
            drift = ob.audit_project({"name": "panel", "host": "dev1", "path": d,
                                      "default_branch": "main"})
        self.assertTrue({"stack-unpinned", "stack-deviation"} <= _kinds(drift))

    def test_onboard_skill_documents_the_stack_rows(self):
        skill = (REPO / "skills" / "onboard-project" / "SKILL.md").read_text(
            encoding="utf-8")
        for needle in ("`## Stack`", "stack-unpinned", "stack-deviation",
                       "airuleset:stack-exception"):
            self.assertIn(needle, skill)


if __name__ == "__main__":
    unittest.main()
