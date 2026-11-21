"""#1333 — the house-stack situational triggers (moved out of
test_house_stack_1333.py for the review-round-1 rework, ROZHODNUTÉ 2, 4, 8).

``hooks/situational-triggers.conf`` injects ``rules/house-stack.md`` once per
session at the moments a stack is chosen:

- Bash (RAW match): a ``gh issue comment|create`` / ``design-record`` / a
  heredoc, ``tee`` or redirect write carrying ``Architektúra:``; a scaffolder
  (``cargo new|init``, ``npm|pnpm|yarn|bun create|init``, ``npx create-*``).
  The pattern is anchored (``\\A``), so a 100 KB command is one linear scan.
- Write: a first app scaffold file (package.json, Cargo.toml, index.html,
  Trunk.toml, pyproject.toml, requirements.txt, go.mod), a ``*.js``/``*.ts``
  file, a design body file with ``Architektúra:``, or an English
  ``## Architecture`` spec/plan under ``docs/**/specs|plans``. Test, fixture,
  vendored and build-output paths (``cli_stack_scope.SKIP_DIRS``) and
  ``*.test.*`` / ``*.spec.*`` files are excluded.

Delivery honours the registry: a checkout whose registry ``stack`` is not
``house`` (odoo-erp, airuleset) gets nothing; an unregistered one does.
Every hook run uses a hermetic HOME and TMPDIR (#1046).
"""

import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

INJECTOR = REPO / "hooks" / "inject-situational-rule.sh"
CONF = REPO / "hooks" / "situational-triggers.conf"
STACK_MARK = "House Stack — the default for every NEW app"
INVESTIGATE_MARK = "skills/investigate-existing-first/SKILL.md"
DASHBOARD_MARK = "skills/version-on-dashboard/SKILL.md"


def _rows(topic):
    out = []
    for line in CONF.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            parts = [p for p in line.split("\t") if p != ""]
            if parts[0] == topic:
                out.append(parts)
    return out


def _fake_repo(root, url):
    root = Path(root)
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text('[remote "origin"]\n\turl = %s\n' % url)
    return root


class _Inject(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        # the injector's once-per-session markers live under TMPDIR
        self.env = hermetic_hook_env(self, TMPDIR=str(self.tmp))
        self.unreg = self.tmp / "brand-new"      # no git, no registry row
        self.unreg.mkdir()
        self.n = 0

    def _ctx(self, tool_input, tool="Bash", session=None, cwd=None):
        self.n += 1
        payload = json.dumps({
            "session_id": session or "hst1333-%s-%d" % (self.id()[-20:], self.n),
            "cwd": str(cwd or self.unreg), "tool_name": tool,
            "tool_input": tool_input})
        r = subprocess.run(["bash", str(INJECTOR)], input=payload,
                           capture_output=True, text=True, env=self.env,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def _write(self, path, content="x", **kw):
        return self._ctx({"file_path": str(path), "content": content},
                         tool="Write", **kw)


# --------------------------------------------------------------------------- #
# Carried over from the first cut.
# --------------------------------------------------------------------------- #
class TestDesignAndScaffoldTriggers(_Inject):
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

    def test_architektura_outside_a_write_stays_quiet(self):
        cmd = "grep -rn 'Architektúra:' modules/"
        self.assertNotIn(STACK_MARK, self._ctx({"command": cmd}))

    def test_design_body_file_written_for_dash_f_injects(self):
        ctx = self._write(self.tmp / "scratch" / "design-7.md",
                          "## Design\nArchitektúra: service + tray\n")
        self.assertIn(STACK_MARK, ctx)

    def test_first_scaffold_files_inject(self):
        for name in ("package.json", "Cargo.toml", "index.html", "Trunk.toml",
                     "pyproject.toml", "requirements.txt", "go.mod"):
            with self.subTest(name=name):
                self.assertIn(STACK_MARK, self._write(self.unreg / name), name)

    def test_member_crate_manifest_injects(self):
        self.assertIn(STACK_MARK,
                      self._write(self.unreg / "crates" / "app-ui" / "Cargo.toml"))

    def test_test_fixture_vendored_and_build_paths_stay_quiet(self):
        for rel in ("tests/fixtures/x/package.json", "node_modules/a/package.json",
                    "target/debug/build/x/Cargo.toml", "test/index.html",
                    "__tests__/app.js", "dist/app-ui.js", "vendor/x/go.mod",
                    "web/app.test.js", "src/view.spec.ts"):
            with self.subTest(rel=rel):
                self.assertNotIn(STACK_MARK, self._write(self.unreg / rel))

    def test_lookalike_names_and_edits_stay_quiet(self):
        for rel in ("my-package.json", "package.json.bak", "src/main.rs",
                    "notes/README.md"):
            with self.subTest(rel=rel):
                self.assertNotIn(STACK_MARK, self._write(self.unreg / rel))
        edit = self._ctx({"file_path": str(self.unreg / "package.json"),
                          "old_string": "a", "new_string": "b"}, tool="Edit")
        self.assertNotIn(STACK_MARK, edit)

    def test_injected_once_per_session(self):
        first = self._write(self.unreg / "package.json", "{}", session="hst-dedup")
        again = self._write(self.unreg / "Cargo.toml", "x", session="hst-dedup")
        self.assertIn(STACK_MARK, first)
        self.assertNotIn(STACK_MARK, again)

    def test_cargo_manifest_cofits_with_investigate_existing_first(self):
        ctx = self._write(self.unreg / "Cargo.toml", "[workspace]\n")
        self.assertIn(STACK_MARK, ctx)
        self.assertIn(INVESTIGATE_MARK, ctx)

    def test_index_html_cofits_with_version_on_dashboard(self):
        ctx = self._write(self.unreg / "index.html", "<html></html>")
        self.assertIn(STACK_MARK, ctx)
        self.assertIn(DASHBOARD_MARK, ctx)

    def test_house_stack_is_a_raw_match_topic(self):
        src = INJECTOR.read_text(encoding="utf-8")
        start = src.index("RAW_BASH_MATCH_TOPICS = {")
        self.assertIn('"house-stack"', src[start:src.index("}", start)])


# --------------------------------------------------------------------------- #
# Review round 1: ROZHODNUTÉ 2 (registry scope), 4 (plain JS), 8 (more moments).
# --------------------------------------------------------------------------- #
class TestRegistryScope(_Inject):
    def test_a_non_house_project_gets_no_injection(self):
        odoo = _fake_repo(self.tmp / "odoo-erp", "git@github.com:zbynekdrlik/odoo-erp.git")
        self.assertNotIn(STACK_MARK, self._write(odoo / "frontline" / "app" / "package.json",
                                                 cwd=odoo))
        cmd = "gh issue comment 9 --body 'Architektúra: a new module'"
        self.assertNotIn(STACK_MARK, self._ctx({"command": cmd}, cwd=odoo))

    def test_airuleset_itself_is_python_tooling(self):
        cmd = "gh issue comment 9 --body 'Architektúra: a new CLI leaf'"
        self.assertNotIn(STACK_MARK, self._ctx({"command": cmd}, cwd=REPO))

    def test_a_house_project_and_an_unregistered_repo_get_it(self):
        foh = _fake_repo(self.tmp / "fohmixer", "https://github.com/zbynekdrlik/fohmixer")
        self.assertIn(STACK_MARK, self._write(foh / "crates" / "x" / "Cargo.toml", cwd=foh))
        new = _fake_repo(self.tmp / "nova", "git@github.com:zbynekdrlik/nova.git")
        self.assertIn(STACK_MARK, self._write(new / "package.json", cwd=REPO))

    def test_the_written_file_decides_not_the_session_cwd(self):
        odoo = _fake_repo(self.tmp / "odoo-erp", "git@github.com:zbynekdrlik/odoo-erp.git")
        self.assertNotIn(STACK_MARK, self._write(odoo / "package.json", cwd=self.unreg))


class TestMoreStackMoments(_Inject):
    def test_design_record_and_bash_writes_with_architektura_inject(self):
        for cmd in (
                "python3 ~/devel/airuleset/airuleset.py design-record 12 "
                "--body 'Architektúra: axum + Leptos'",
                "cat > /tmp/x/design.md <<'EOF'\n## Design\nArchitektúra: x\nEOF",
                "printf 'Architektura: x\\n' | tee /tmp/x/d.md",
                "printf 'Architektúra: x' > /tmp/x/d.md"):
            with self.subTest(cmd=cmd[:40]):
                self.assertIn(STACK_MARK, self._ctx({"command": cmd}))

    def test_scaffolders_inject(self):
        for cmd in ("cargo new wall-songs", "cargo init --name x", "npm create vite@latest ui",
                    "npm init -y", "pnpm create svelte@latest", "yarn create next-app",
                    "bun create elysia app", "npx create-react-app ui",
                    "cd /tmp/x && npx create-next-app@latest web"):
            with self.subTest(cmd=cmd):
                self.assertIn(STACK_MARK, self._ctx({"command": cmd}))

    def test_ordinary_build_and_install_commands_stay_quiet(self):
        for cmd in ("cargo build --release", "npm install", "npm ci", "cargo test -p x",
                    "npx playwright test"):
            with self.subTest(cmd=cmd):
                self.assertNotIn(STACK_MARK, self._ctx({"command": cmd}))

    def test_plain_js_and_ts_files_inject(self):
        for rel in ("web/app.js", "static/main.mjs", "src/App.jsx", "src/main.ts",
                    "src/View.tsx", "lib/x.cjs"):
            with self.subTest(rel=rel):
                self.assertIn(STACK_MARK, self._write(self.unreg / rel))

    def test_english_architecture_spec_or_plan_injects(self):
        for rel in ("docs/superpowers/specs/2026-11-21-x-design.md",
                    "docs/plans/2026-11-21-x.md"):
            with self.subTest(rel=rel):
                self.assertIn(STACK_MARK, self._write(
                    self.unreg / rel, "# X\n\n## Architecture\n\nA service.\n"))
        self.assertNotIn(STACK_MARK, self._write(
            self.unreg / "docs/specs/x.md", "# X\n\n## Goals\n"))
        self.assertNotIn(STACK_MARK, self._write(
            self.unreg / "notes/x.md", "# X\n\n## Architecture\n"))


class TestPatternShape(unittest.TestCase):
    def test_bash_pattern_is_anchored_and_linear_on_100kb(self):
        bash = [r for r in _rows("house-stack") if r[1] == "Bash"][0][2]
        self.assertTrue(bash.startswith("(?s)\\A"), bash[:20])
        for cmd in ("gh issue comment 1 " + "x" * 100000,
                    "Architektúra: " + "y" * 100000,
                    "echo " + "a b c " * 17000):
            t0 = time.perf_counter()
            re.search(bash, cmd)
            self.assertLess(time.perf_counter() - t0, 0.1, cmd[:30])

    def test_write_pattern_is_linear_on_100kb_content(self):
        write = [r for r in _rows("house-stack") if r[1] == "Write"][0]
        hay = json.dumps({"file_path": "/r/docs/specs/x.md",
                          "content": "## Arch " * 14000}, ensure_ascii=False)
        t0 = time.perf_counter()
        re.search(write[2], hay)
        re.search(write[4], hay)
        self.assertLess(time.perf_counter() - t0, 0.1)

    def test_skip_dirs_are_the_gate_s(self):
        import cli_stack_scope
        write = [r for r in _rows("house-stack") if r[1] == "Write"][0]
        m = re.search(r"/\(\?:([^)]*)\)/", write[4])
        self.assertIsNotNone(m, write[4])
        self.assertEqual(set(m.group(1).split("|")), set(cli_stack_scope.SKIP_DIRS))


if __name__ == "__main__":
    unittest.main()
