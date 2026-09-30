"""#1198 — every Rust app with a web UI ships a tray icon (owner 30.9.).

Two halves, both tested here:

1. The convention is a path-scoped rule, ``rules/rust-web-tray.md``. It is
   installed the same way as the other ``rules/*.md`` files: through a
   ``profiles/universal.profile`` line that ``symlink_global_rules`` links
   into ``~/.claude/rules/``.
2. ``onboard-project``'s foundation-gap step detects "Rust + web UI" (an HTTP
   server dependency plus web assets). When no tray dependency or tray crate
   exists, it files the tray foundation ticket the same way as the
   version-label gap. ``--audit`` reports it as ``missing-tray`` drift.

Fixtures are real Cargo trees in a tmp dir. ``gh`` goes through the same
offline ``FakeRunner`` as test_onboard_project.py, so no real ticket is ever
filed.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))   # tests/ helpers (#1184)

import airuleset  # noqa: E402
import cli_onboard as ob  # noqa: E402
from _onboard_gate_open import setUpModule, tearDownModule  # noqa: E402,F401
from test_onboard_project import FakeRunner, init_repo  # noqa: E402

RULE = REPO / "rules" / "rust-web-tray.md"

AXUM_PKG = """[package]
name = "fohmixer"
version = "0.1.0"
edition = "2021"

[dependencies]
axum = "0.8"
tokio = { version = "1", features = ["full"] }
"""


def _gh_no_issues(existing_titles=()):
    """gh handler: `issue list` returns the given titles, anything else rc 0."""
    def handler(argv):
        if "list" in argv:
            data = [{"number": i + 1, "title": t}
                    for i, t in enumerate(existing_titles)]
            return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    return handler


def _write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _rust_web_single(root, extra_deps=""):
    """A single-crate Rust app: axum + a static/index.html web UI."""
    _write(root, "Cargo.toml", AXUM_PKG + extra_deps)
    _write(root, "src/main.rs", "fn main() {}\n")
    _write(root, "static/index.html", "<html></html>\n")


def _rust_web_workspace(root, with_tray_crate=False):
    """A workspace: crates/server (axum) + crates/web (Trunk UI)."""
    members = '"crates/server", "crates/web"'
    if with_tray_crate:
        members += ', "crates/fohmixer-tray"'
    # axum lives ONLY in crates/server, so the gap proves crates/* is read.
    _write(root, "Cargo.toml", "[workspace]\nmembers = [%s]\n"
           "resolver = \"2\"\n" % members)
    _write(root, "crates/server/Cargo.toml",
           "[package]\nname = \"fohmixer-server\"\nversion = \"0.1.0\"\n\n"
           "[dependencies]\naxum = \"0.8\"\n")
    _write(root, "crates/web/Cargo.toml",
           "[package]\nname = \"fohmixer-web\"\nversion = \"0.1.0\"\n\n"
           "[dependencies]\nleptos = \"0.8\"\n")
    _write(root, "crates/web/Trunk.toml", "[build]\n")
    _write(root, "crates/web/index.html", "<html></html>\n")
    if with_tray_crate:
        _write(root, "crates/fohmixer-tray/Cargo.toml",
               "[package]\nname = \"fohmixer-tray\"\nversion = \"0.1.0\"\n\n"
               "[dependencies]\nreqwest = \"0.12\"\n")


def _foundation(root, existing=()):
    run = FakeRunner(gh_handler=_gh_no_issues(existing))
    step = ob.step_foundation_tickets(str(root), "fohmixer", run=run,
                                      dry_run=True)
    return step, run


# --------------------------------------------------------------------------- #
# 1) The rule file and its install wiring.
# --------------------------------------------------------------------------- #
class TestRustWebTrayRule(unittest.TestCase):
    def test_rule_file_exists_with_rust_paths(self):
        self.assertTrue(RULE.exists(), "rules/rust-web-tray.md not shipped")
        text = RULE.read_text()
        self.assertTrue(text.startswith("---\npaths:\n"), text[:80])
        head = text.split("---", 2)[1]
        for glob in ('"**/Cargo.toml"', '"**/src/**/*.rs"', '"**/crates/**"'):
            self.assertIn(glob, head)

    def test_rule_states_the_convention(self):
        text = RULE.read_text()
        for needle in ("tooltip", "Open", "Copy URL", "Exit", "logon",
                       "crates/iem-tray"):
            self.assertIn(needle, text, needle)
        # Exit ends the tray only, never the service.
        self.assertIn("tray only", text)

    def test_rule_is_in_universal_profile(self):
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        _modules, rules = airuleset.categorize_entries(entries)
        self.assertIn("rules/rust-web-tray.md", rules)

    def test_rule_gets_installed_as_a_symlink(self):
        with TemporaryDirectory() as d:
            claude_dir = Path(d)
            airuleset.symlink_global_rules(["rules/rust-web-tray.md"],
                                           claude_dir, airuleset.REPO_DIR)
            link = claude_dir / "rules" / "rust-web-tray.md"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), RULE.resolve())


# --------------------------------------------------------------------------- #
# 2) The foundation-gap step files the tray ticket for a Rust web app.
# --------------------------------------------------------------------------- #
class TestTrayFoundationGap(unittest.TestCase):
    def test_rust_axum_web_without_tray_files_tray_gap(self):
        with TemporaryDirectory() as d:
            _rust_web_single(d)
            step, run = _foundation(d)
            self.assertEqual(step["status"], "would-apply")
            self.assertIn("tray", step["detail"])
            # dry-run never files anything
            self.assertEqual(run.gh_matching("issue", "create"), [])

    def test_same_project_with_tray_icon_dependency_has_no_tray_gap(self):
        with TemporaryDirectory() as d:
            _rust_web_single(d, extra_deps='tray-icon = "0.19"\n')
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_tray_dependency_in_target_table_counts(self):
        with TemporaryDirectory() as d:
            _rust_web_single(d, extra_deps=(
                "\n[target.'cfg(windows)'.dependencies]\n"
                'tray-item = "0.10"\n'))
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_ksni_dependency_table_form_counts(self):
        with TemporaryDirectory() as d:
            _rust_web_single(d, extra_deps='\n[dependencies.ksni]\nversion = "0.3"\n')
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_non_web_rust_crate_has_no_tray_gap(self):
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", "[package]\nname = \"cli\"\n\n"
                   "[dependencies]\nserde = \"1\"\n")
            _write(d, "src/main.rs", "fn main() {}\n")
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_http_server_without_web_assets_has_no_tray_gap(self):
        # an API-only service (no web UI) is not a "Rust + web UI" app
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", AXUM_PKG)
            _write(d, "src/main.rs", "fn main() {}\n")
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_embedded_assets_dependency_counts_as_web_ui(self):
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", AXUM_PKG + 'rust-embed = "8"\n')
            _write(d, "src/main.rs", "fn main() {}\n")
            step, _run = _foundation(d)
            self.assertIn("tray", step["detail"])

    def test_node_project_has_no_tray_gap(self):
        with TemporaryDirectory() as d:
            _write(d, "package.json", "{}\n")
            _write(d, "static/index.html", "<html></html>\n")
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_workspace_crates_are_read(self):
        with TemporaryDirectory() as d:
            _rust_web_workspace(d)
            step, _run = _foundation(d)
            self.assertIn("tray", step["detail"])

    def test_workspace_with_a_tray_crate_has_no_gap(self):
        with TemporaryDirectory() as d:
            _rust_web_workspace(d, with_tray_crate=True)
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_non_dry_run_files_the_tray_ticket(self):
        with TemporaryDirectory() as d:
            _rust_web_single(d)
            run = FakeRunner(gh_handler=_gh_no_issues())
            step = ob.step_foundation_tickets(d, "fohmixer", run=run)
            self.assertEqual(step["status"], "applied")
            created = run.gh_matching("issue", "create",
                                      ob.FOUNDATION_TRAY_TITLE)
            self.assertEqual(len(created), 1)
            body = created[0][created[0].index("--body") + 1]
            self.assertIn("rules/rust-web-tray.md", body)
            self.assertIn("Scope-gate:", body)

    def test_existing_tray_ticket_is_not_refiled(self):
        with TemporaryDirectory() as d:
            _rust_web_single(d)
            step, run = _foundation(d, existing=(ob.FOUNDATION_TRAY_TITLE,
                                                 ob.FOUNDATION_CI_TITLE))
            self.assertEqual(step["status"], "satisfied")
            self.assertIn("tray", step["detail"])
            self.assertEqual(run.gh_matching("issue", "create"), [])


# --------------------------------------------------------------------------- #
# 3) --audit reports the missing tray.
# --------------------------------------------------------------------------- #
class TestTrayAudit(unittest.TestCase):
    def _entry(self, repo):
        return {"name": "fohmixer", "host": "dev1", "path": str(repo),
                "branch_model": "2-branch", "default_branch": "main",
                "work_branch": "dev"}

    def test_audit_reports_missing_tray(self):
        with TemporaryDirectory() as d:
            init_repo(d, remote="https://github.com/zbynekdrlik/fohmixer.git")
            _rust_web_workspace(d)
            drift = ob.audit_project(self._entry(d))
            kinds = {x["kind"] for x in drift}
            self.assertIn("missing-tray", kinds)

    def test_audit_quiet_when_tray_present(self):
        with TemporaryDirectory() as d:
            init_repo(d, remote="https://github.com/zbynekdrlik/fohmixer.git")
            _rust_web_workspace(d, with_tray_crate=True)
            drift = ob.audit_project(self._entry(d))
            kinds = {x["kind"] for x in drift}
            self.assertNotIn("missing-tray", kinds)



# --------------------------------------------------------------------------- #
# 4) Review round 1: real fleet layouts, wider name sets, the remote path.
# --------------------------------------------------------------------------- #
def _songplayer_like(root, tauri_features='["tray-icon"]'):
    """songplayer / restreamer layout: axum in crates/sp-server, a Trunk UI in
    sp-ui/, and the tray in src-tauri/ (outside crates/, excluded from the
    workspace) as the tauri `tray-icon` feature."""
    _write(root, "Cargo.toml", "[workspace]\nmembers = [\"crates/sp-server\"]\n"
           "exclude = [\"src-tauri\"]\n")
    _write(root, "crates/sp-server/Cargo.toml",
           "[package]\nname = \"sp-server\"\n\n[dependencies]\naxum = \"0.8\"\n")
    _write(root, "sp-ui/Trunk.toml", "[build]\n")
    _write(root, "sp-ui/index.html", "<html></html>\n")
    _write(root, "src-tauri/Cargo.toml",
           "[package]\nname = \"songplayer\"\n\n[dependencies]\n"
           "tauri = { version = \"2\", features = %s }\n" % tauri_features)


class TestTrayReviewRound1(unittest.TestCase):
    def test_tauri_tray_icon_feature_outside_crates_counts(self):
        with TemporaryDirectory() as d:
            _songplayer_like(d)
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_tauri_v1_system_tray_feature_counts(self):
        with TemporaryDirectory() as d:
            _songplayer_like(d, tauri_features='["system-tray"]')
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_tauri_without_tray_feature_is_still_a_gap(self):
        with TemporaryDirectory() as d:
            _songplayer_like(d, tauri_features='["devtools"]')
            step, _run = _foundation(d)
            self.assertIn("tray", step["detail"])

    def test_workspace_member_outside_crates_is_read(self):
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", "[workspace]\nmembers = [\"server\"]\n")
            _write(d, "server/Cargo.toml", "[package]\nname = \"server\"\n\n"
                   "[dependencies]\nsalvo = \"0.70\"\n")
            _write(d, "server/static/app.js", "//\n")
            step, _run = _foundation(d)
            self.assertIn("tray", step["detail"])

    def test_tray_crate_outside_crates_counts(self):
        with TemporaryDirectory() as d:
            _rust_web_workspace(d)
            _write(d, "apps/fohmixer-tray/Cargo.toml",
                   "[package]\nname = \"fohmixer-tray\"\n")
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_more_tray_crates_count(self):
        for dep in ("trayicon", "systray"):
            with self.subTest(dep=dep), TemporaryDirectory() as d:
                _rust_web_single(d, extra_deps='%s = "0.1"\n' % dep)
                step, _run = _foundation(d)
                self.assertNotIn("tray", step["detail"])

    def test_more_http_servers_count(self):
        for dep in ("axum-server", "salvo", "tide", "ntex"):
            with self.subTest(dep=dep), TemporaryDirectory() as d:
                _write(d, "Cargo.toml", "[package]\nname = \"x\"\n\n"
                       "[dependencies]\n%s = \"1\"\n" % dep)
                _write(d, "static/index.html", "<html></html>\n")
                step, _run = _foundation(d)
                self.assertIn("tray", step["detail"])

    def test_web_assets_at_depth_four_are_found(self):
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", "[workspace]\nmembers = [\"crates/*\"]\n")
            _write(d, "crates/srv/Cargo.toml", "[package]\nname = \"srv\"\n\n"
                   "[dependencies]\naxum = \"0.8\"\n")
            _write(d, "crates/x-web/assets/index.html", "<html></html>\n")
            step, _run = _foundation(d)
            self.assertIn("tray", step["detail"])

    def test_build_output_never_counts_as_a_manifest(self):
        with TemporaryDirectory() as d:
            _rust_web_single(d)
            # a vendored/built tray crate under target/ is not the project's tray
            _write(d, "target/foo-tray/Cargo.toml", "[package]\nname = \"foo-tray\"\n")
            step, _run = _foundation(d)
            self.assertIn("tray", step["detail"])


class SshShellRunner:
    """A remote box modeled by the local tmp tree: every ssh payload runs via
    `sh -c` locally; any NON-ssh, non-gh call is recorded as a local access."""

    def __init__(self):
        self.ssh_calls, self.local_calls = [], []

    def __call__(self, argv, **kw):
        if argv and argv[0] == "gh":
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        if argv and argv[0] == "ssh":
            self.ssh_calls.append(argv[-1])
            return subprocess.run(["sh", "-c", argv[-1]], **kw)
        self.local_calls.append(list(argv))
        return subprocess.run(argv, **kw)


class TestTrayRemote(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        p = mock.patch("cli_onboard_exec._local_hostname",
                       return_value="test-box-1198")
        p.start()
        self.addCleanup(p.stop)

    def test_remote_check_reads_over_ssh_only_and_batches(self):
        with TemporaryDirectory() as d:
            _rust_web_workspace(d)
            for i in range(5):
                _write(d, "crates/extra%d/Cargo.toml" % i,
                       "[package]\nname = \"extra%d\"\n" % i)
            run = SshShellRunner()
            reason = ob._tray.rust_web_tray_gap(d, host="dev2", run=run)
            self.assertTrue(reason)
            self.assertIn("axum", reason)
            self.assertEqual(run.local_calls, [])
            # manifests in one call + the asset probe: never one call per crate
            self.assertLessEqual(len(run.ssh_calls), 2, run.ssh_calls)

    def test_remote_non_rust_project_costs_one_call(self):
        with TemporaryDirectory() as d:
            _write(d, "package.json", "{}\n")
            run = SshShellRunner()
            self.assertIsNone(ob._tray.rust_web_tray_gap(d, host="dev2", run=run))
            self.assertEqual(run.local_calls, [])
            self.assertEqual(len(run.ssh_calls), 1, run.ssh_calls)

    def test_ssh_failure_is_loud_not_silent(self):
        # an unreachable box must never read as "not a Rust app" in silence
        import contextlib
        import io

        def dead_ssh(argv, **kw):
            if argv and argv[0] == "ssh":
                return subprocess.CompletedProcess(argv, 255, "",
                                                   "ssh: connect refused")
            raise AssertionError("local call %r" % (argv,))

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(ob._tray.rust_web_tray_gap(
                "/srv/fohmixer", host="dev2", run=dead_ssh))
        self.assertIn("tray check", err.getvalue())
        self.assertIn("255", err.getvalue())


# --------------------------------------------------------------------------- #
# 5) Review round 2: source/doc dirs, scan scope, onboarding == audit, rule.
# --------------------------------------------------------------------------- #
class TestTrayReviewRound2(unittest.TestCase):
    def test_rust_source_module_dirs_are_not_web_assets(self):
        # an API-only axum service with src/web + src/ui handler modules
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", AXUM_PKG)
            _write(d, "src/main.rs", "mod web; mod ui;\nfn main() {}\n")
            _write(d, "src/web/mod.rs", "\n")
            _write(d, "src/ui/mod.rs", "\n")
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_doc_output_is_not_a_web_ui(self):
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", AXUM_PKG)
            _write(d, "src/main.rs", "fn main() {}\n")
            _write(d, "docs/index.html", "<html></html>\n")
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_example_crate_does_not_make_a_library_a_web_app(self):
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", "[package]\nname = \"lib\"\n\n"
                   "[dependencies]\nserde = \"1\"\n")
            _write(d, "examples/demo/Cargo.toml", "[package]\nname = \"demo\"\n\n"
                   "[dependencies]\naxum = \"0.8\"\n")
            _write(d, "examples/demo/static/index.html", "<html></html>\n")
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_hidden_dir_copies_are_never_read(self):
        # a stale .claude/worktrees/<id> checkout must not lend its deps
        with TemporaryDirectory() as d:
            _write(d, "Cargo.toml", "[package]\nname = \"lib\"\n\n"
                   "[dependencies]\nserde = \"1\"\n")
            _write(d, "static/app.css", "\n")
            _write(d, ".claude/worktrees/x/Cargo.toml", AXUM_PKG)
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_tauri_tray_four_levels_deep_counts(self):
        with TemporaryDirectory() as d:
            _rust_web_workspace(d)
            _write(d, "apps/desktop/src-tauri/Cargo.toml",
                   "[package]\nname = \"desk\"\n\n[dependencies]\n"
                   "tauri = { version = \"2\", features = [\"tray-icon\"] }\n")
            step, _run = _foundation(d)
            self.assertNotIn("tray", step["detail"])

    def test_onboarding_and_audit_agree_without_a_root_manifest(self):
        with TemporaryDirectory() as d:
            init_repo(d, remote="https://github.com/zbynekdrlik/fohmixer.git")
            _write(d, "package.json", "{}\n")
            _write(d, "server/Cargo.toml", "[package]\nname = \"srv\"\n\n"
                   "[dependencies]\naxum = \"0.8\"\n")
            _write(d, "public/index.html", "<html></html>\n")
            step, _run = _foundation(d)
            self.assertIn("tray", step["detail"])
            drift = ob.audit_project({"name": "fohmixer", "host": "dev1",
                                      "path": d, "default_branch": "main"})
            self.assertIn("missing-tray", {x["kind"] for x in drift})

    def test_rule_names_every_accepted_tray_shape(self):
        text = RULE.read_text()
        for name in ("tray-icon", "tray-item", "ksni", "trayicon", "systray",
                     "system-tray", "tauri"):
            self.assertIn(name, text, name)


if __name__ == "__main__":
    unittest.main()
