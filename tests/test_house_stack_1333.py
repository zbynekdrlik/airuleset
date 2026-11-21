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

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

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
                "No exceptions",
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


if __name__ == "__main__":
    unittest.main()
