"""#1333 — the house stack reaches the session at the moment a stack is chosen.

Owner, 21.11.2026: new projects keep starting on ad-hoc technology and must be
rebuilt. Root cause: the stack lived only in two ``paths:``-scoped rules
(``*.rs`` / ``Cargo.toml``), which never load in an empty repo or a JS project,
and one of them was not even in the install profile. This file locks the four
non-hook surfaces of the fix:

1. ONE rule body, ``rules/house-stack.md``, replacing the two old rules.
2. The always-on pointer in ``modules/quality/architecture-first.md``.
3. Situational trigger rows: ``test_house_stack_triggers_1333.py``.
4. ``onboard-project`` seed + audit: ``test_house_stack_audit_1333.py``.

The PreToolUse frontend-framework gate has its own file
(``test_frontend_gate_1333.py``).
"""

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import airuleset  # noqa: E402

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
        globs = [ln.strip()[2:] for ln in head.splitlines() if ln.strip().startswith("- ")]
        # round 3/4 (I, 8): manifests only; index.html / *.rs / crates/** loaded the
        # whole rule on any read in a Rust or web project
        self.assertEqual(globs, ['"**/Cargo.toml"', '"**/Trunk.toml"'])

    def test_every_layer_is_pinned(self):
        for needle in (
                "Rust Cargo workspace", "`crates/<prefix>-*`",
                "axum + tokio", "`GET /api/status`", "WebSocket",
                "Leptos (latest 0.8.x) CSR → WASM", "built by Trunk", "PWA",
                "version visible on the dashboard",
                "`tray-icon` in a `<prefix>-tray` crate",
                "`windows` crate Direct3D 11", "NDI between PCs, Spout inside one PC",
                "paced on vblank",
                "`cargo build --release --target x86_64-pc-windows-gnu`",
                "system-wide", "scheduled tasks",
                "`C:\\ProgramData\\<App>\\logs`"):
            self.assertIn(needle, self.text, needle)

    def test_reference_projects_are_named(self):
        for needle in ("fohmixer `crates/fohmixer-ui`",
                       "songplayer `sp-server` (`/api/v1/status` with the version, "
                       "WebSocket); fohmixer `fohmixer-hub` (`/api/version`, WebSocket)",
                       "wall-bridge `crates/wb-tray` (tray-icon, Windows); intercom "
                       "`tray` (tray-icon, Linux / gtk); fohmixer `crates/fohmixer-tray` "
                       "(Tauri 2 tray, no window)",
                       "songplayer `sp-gpu`", "`wb-spout`", "`wb-scanout`",
                       "Spout senders"):
            self.assertIn(needle, self.text, needle)
        self.assertNotIn("iemmixer", self.text)

    def test_web_ui_row_carries_the_customizable_ui_pattern(self):
        self.assertIn("where the UI shows controls / panels the user arranges, a "
                      "customizable UI (ARRANGE: reorder + hide/show, saved "
                      "server-side)", self.text)
        self.assertIn("(ARRANGE: `crates/fohmixer-ui/src/behave/arrange_drag.rs` + "
                      "`behave/arrange_edit.rs`)", self.text)

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
                "set by airuleset only, never by a project session",
                "A new repo whose kind matches ANY existing platform id follows "
                "that platform, and airuleset records its `stack` right after "
                "onboarding.",
                "Until a deviating project's migration ticket lands, keep maintaining "
                "the deviating component; new features go on the house stack or need "
                "the marker."):
            self.assertIn(needle, self.text, needle)

    def test_every_row_says_when_it_applies(self):
        rows = [ln for ln in self.raw.splitlines() if ln.startswith("| ")]
        self.assertEqual(rows[0].split("|")[2].strip(), "Applies when")
        applies = {r.split("|")[1].strip(): r.split("|")[2].strip()
                   for r in rows[2:]}
        self.assertEqual(applies["Tray"], "a desktop app (Windows or Linux)")
        self.assertEqual(applies["Service"], "an app that runs as a service or serves "
                         "a UI / API (not a CLI or library)")
        self.assertEqual(applies["Install"], "a Windows desktop app with a UI or video")
        self.assertEqual(applies["Graphics engine"], "a Windows app that renders video")
        self.assertIn("A headless Linux service is the Workspace + Service + Web UI "
                      "rows and opts "
                      "out of the tray with `<!-- airuleset:tray=n/a <reason> -->`.",
                      self.text)

    def test_both_tray_variants_are_accepted(self):
        self.assertIn("`tray-icon` in a `<prefix>-tray` crate, or the Tauri 2 tray "
                      "(no window needed)", self.text)
        self.assertIn("runs on a desktop machine (Windows or Linux)", self.text)
        self.assertIn("Both variants are accepted.", self.text)

    def test_windows_release_builds_come_from_ci(self):
        self.assertIn("release artefacts come from the CI Windows runner; the local "
                      "check is the dev1 cross-build", self.text)

    def test_stack_section_only_restates_house_rows(self):
        self.assertIn("A project `## Stack` section may only restate house rows; "
                      "other technology in its Technology column needs the marker "
                      "too.", self.text)

    def test_one_marker_per_technology(self):
        self.assertIn("**One marker per technology:** `<what>` names it (React, "
                      "askama, plain JavaScript, …), and a marker covers only the "
                      "technology it names.", self.text)

    def test_enforcement_text_matches_the_gates(self):
        for needle in ("a PreToolUse gate refuses a `package.json` Write or Edit",
                       "after a package-manager Bash call (success or failure) a "
                       "PostToolUse check compares the repo's `package.json` files "
                       "with the default branch (the merge-base with `origin/HEAD`: "
                       "committing does not make a framework allowed)",
                       "Both honour only the repo-root `CLAUDE.md` marker.",
                       "a template / UI crate (askama, tera, maud, yew, dioxus, "
                       "sycamore, actix-web, warp, rocket, egui, iced, slint)",
                       "hand-written JS (incl. inline `<script>`; vendored files and a "
                       "framework app's own sources excluded) outside a `Trunk.toml` "
                       "dir or a leptos / wasm-bindgen crate",
                       "It lists every marker and the gate's `ALLOW-MARKER` lines"):
            self.assertIn(needle, self.text, needle)

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
                "**Simplicity alone is no reason to leave WASM; a genuinely better "
                "option goes through Stack evolution.**"):
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
        self.assertIn("The design ticket links it and keeps working on the current "
                      "row; the owner decides.", sec)

    def test_evolution_proposal_reaches_the_owner(self):
        sec = self._evolution()
        self.assertIn("on the ONE canonical airuleset ticket labelled "
                      "`stack-proposal` + `needs-decision` (it reaches the owner's "
                      "`U`", sec)
        self.assertNotIn("label that ticket", sec)
        for needle in ("`gh issue create -R zbynekdrlik/airuleset -l stack-proposal "
                       "-l needs-decision`", "`Scope-gate: needs-user-decision`",
                       "`Dedup-checked: label stack-proposal`",
                       "`Requested by <owner>/<repo>#N`",
                       "then `peer_send` it to airuleset"):
            self.assertIn(needle, sec)
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
        self.assertIn("**House stack (#1333):** a NEW standalone app/deployable (a "
                      "new repo, or a new deployable inside a house project) defaults "
                      "to `~/.claude/rules/house-stack.md` (read it first; a deviation "
                      "needs its owner-approved marker)", bullet)
        self.assertIn("an existing project keeps its stack (the `stack` of its "
                      "airuleset registry row)", bullet)
        self.assertEqual(raw.count("house-stack.md"), 1)


if __name__ == "__main__":
    unittest.main()
