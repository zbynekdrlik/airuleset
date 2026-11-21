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
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

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
        for needle in ("fohmixer `crates/fohmixer-ui`", "wall-bridge `wb-service`",
                       "wall-bridge `crates/wb-tray`", "songplayer `sp-gpu`",
                       "`wb-spout`", "`wb-scanout`", "Spout senders"):
            self.assertIn(needle, self.text, needle)

    def test_exception_needs_the_owner_marker(self):
        self.assertIn("Any other technology only as an owner-approved exception",
                      self.text)
        self.assertIn(MARKER, self.raw)
        self.assertIn("Never add the marker on your own judgement", self.text)

    def test_read_before_choosing_in_an_empty_repo(self):
        self.assertIn("Read this BEFORE choosing any technology, in an empty "
                      "repo too", self.text)

    # --- every binding statement of the old rust-web-stack rule ---------- #
    def test_web_ui_statements_survive_the_merge(self):
        for needle in (
                "Every web UI MUST use Leptos compiled to WASM, served by Axum",
                "No exception without the owner marker above",
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
        self.assertIn("The owner decides.", sec)

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
        self.assertIn("**House stack (#1333):** the default for every new app or "
                      "component is `~/.claude/rules/house-stack.md` — read it "
                      "before choosing", bullet)
        self.assertIn("a deviation needs `%s`" % MARKER, bullet)
        self.assertEqual(raw.count("house-stack.md"), 1)


# --------------------------------------------------------------------------- #
# 3) Situational triggers: the design comment and the first scaffold file.
# --------------------------------------------------------------------------- #
INJECTOR = REPO / "hooks" / "inject-situational-rule.sh"
STACK_MARK = "House Stack — Every App Is Built On It"
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


if __name__ == "__main__":
    unittest.main()
