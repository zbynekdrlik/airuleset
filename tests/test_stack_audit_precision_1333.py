"""#1333 audit precision: the three defects the first real
`onboard-project --audit` showed (21.11.2026), as reworked by the round-2
review (ROZHODNUTÉ: a deny-list, never a parsed allow-list; an audit's silent
false negative is worse than a visible false positive).

(a) Trunk / wasm-bindgen OUTPUT is no hand-written JS: a `.js` beside its
    same-stem `_bg.wasm`, or a Trunk hashed `<name>-<16 hex>.js` beside a
    `.wasm`. A gitignored tree (camera-box's pinned `shading-release/`) is
    never the project's code; tracked files count.
(b) Every hand-written JS / TS file counts (no page has to serve it: template
    `src`, `.rs` pages, bundler entries), minus the known non-UI categories:
    generated reports (`playwright-report`, `test-results`, `htmlcov`,
    `allure-report`, `storybook-static`, and `coverage` only with an lcov
    report), tooling (`scripts/`, `tools/`, `ops/`, `bin/`, `*.config.*`),
    Companion modules, vendored files. Inline `<script>` lines in `.html`
    count, minus HTML comments and non-JS `type=`; a > 64 KB line is skipped;
    a failed awk or a record without `n <int>` is unverified, never clean.
    The true positives stay: intercom `web/app.js` + `web/levels.js`, reaperiem
    `web/mixer.html`, an inline dashboard; dcontroller's Leptos crate is exempt.
(c) An account row is read AS its account (`cli_account_bootstrap.
    account_remote`): `newlevel` cannot read `/home/<account>`. The gate log is
    read the same way; a paused or pending account, or a declared one with no
    REMOTE_HOSTS entry, is unreachable and never read as the host login;
    legacy rows keep the host login.
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import cli_account_bootstrap  # noqa: E402
import cli_onboard as ob  # noqa: E402
import cli_onboard_exec  # noqa: E402
from test_onboard_project import FakeRunner, init_repo  # noqa: E402
from _onboard_gate_open import setUpModule, tearDownModule  # noqa: E402,F401  (#1184)

HOUSE = {"name": "app", "stack": "house"}
COMPANION_PKG = '{"name": "m", "dependencies": {"@companion-module/base": "1.13.6"}}'
LEPTOS_CARGO = '[package]\nname = "web"\n\n[dependencies]\nleptos = { version = "0.8" }\n'
AXUM_CARGO = '[package]\nname = "%s"\n\n[dependencies]\naxum = "0.8"\n'
# Every awk this box has (each gets its own PATH shim named `awk`; busybox
# dispatches on argv[0]).
AWKS = sorted({os.path.realpath(p) for p in (shutil.which(n) for n in (
    "awk", "mawk", "gawk", "nawk", "original-awk", "busybox")) if p})


def _put(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _page(*srcs, inline=0):
    """An .html page loading `srcs` by `<script src>`, plus `inline` lines of
    inline script."""
    body = "".join('  <script src="%s"></script>\n' % s for s in srcs)
    if inline:
        body += "  <script>\n" + "    tick();\n" * inline + "  </script>\n"
    return "<!DOCTYPE html>\n<html><body>\n%s</body></html>\n" % body


def _trunk_index(stem, inline):
    """camera-box's shading-release/v0.2.10 index.html shape: Trunk's
    modulepreload + inline module loader, then the page's own inline script."""
    return ('<!DOCTYPE html><html><head>\n'
            '<link rel="modulepreload" href="/%s.js" crossorigin="anonymous">\n'
            '    <script type="module">\n'
            "import init, * as bindings from '/%s.js';\n"
            "const wasm = await init({ module_or_path: '/%s_bg.wasm' });\n"
            "window.wasmBindings = bindings;\n"
            "    </script>\n    <script>\n%s    </script>\n"
            "</head><body></body></html>\n"
            % (stem, stem, stem, "  navigator.serviceWorker.register('/sw.js');\n" * inline))


def _deviation(files, git_files=None, ignore=None):
    """deviation() of a tree; with `git_files` the tree is a git checkout
    whose `.gitignore` is `ignore` and whose `git_files` are committed
    (force-added, so a tracked file under an ignored dir stays tracked)."""
    with tempfile.TemporaryDirectory() as d:
        for rel, text in files.items():
            _put(d, rel, text)
        if git_files is not None:
            init_repo(d)
            _put(d, ".gitignore", ignore or "")
            subprocess.run(["git", "-C", d, "add", "-f", ".gitignore", *git_files],
                           check=True)
            subprocess.run(["git", "-C", d, "commit", "-q", "-m", "fixture"], check=True)
        return ob._stack.deviation(d, run=FakeRunner())


def _js(items):
    return [ev for tech, ev in items if tech == "javascript"]


def _audit(files):
    with tempfile.TemporaryDirectory() as d:
        for rel, text in files.items():
            _put(d, rel, text)
        return ob._stack.audit_drift(d, run=FakeRunner(), claude_md="## Playbook router\n",
                                     entry=HOUSE)


@contextlib.contextmanager
def _awk_on_path(target=None, script=None):
    """PATH with a dir whose `awk` is `target` (a real awk) or a `script`."""
    with tempfile.TemporaryDirectory() as shim:
        awk = Path(shim) / "awk"
        if target:
            awk.symlink_to(target)
        else:
            awk.write_text("#!/bin/sh\n" + script, encoding="utf-8")
            awk.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": shim + os.pathsep + os.environ["PATH"]}):
            yield


class TestWasmBindgenOutput(unittest.TestCase):
    """(a) camera-box: Trunk / wasm-bindgen output is not hand-written JS."""

    def test_camera_box_trunk_bundles_are_output(self):
        files = {}
        for ver, h in (("v0.2.10", "a3a7412ddb48ffa2"), ("v0.2.11", "c69fc818b0d3d5ba"),
                       ("v0.2.12", "46b0c911f9ccbdaf"), ("v0.2.13", "86ef7353219f7ac4")):
            web = "shading-release/%s/bkshading-service/web/" % ver
            stem = "bkshading-panel-" + h
            files.update({web + stem + ".js": "export function x() {}\n" * 1237,
                          web + stem + "_bg.wasm": "\0asm",
                          web + "index.html": _trunk_index(stem, inline=20),
                          web + "sw.js": "self.x = 1;\n" * 17})
        self.assertEqual(_deviation(files), [])

    def test_only_a_bg_wasm_pair_or_a_hashed_name_is_output(self):
        files = {"web/engine.js": "x\n" * 300, "web/engine_bg.wasm": "\0asm",
                 "web/app.js": "x\n" * 250,
                 "web2/foo.js": "x\n" * 300, "web2/foo.wasm": "\0asm",
                 "web3/panel-0123456789abcdef.js": "x\n" * 300, "web3/other.wasm": "\0asm"}
        self.assertEqual(_js(_deviation(files)),
                         ["hand-written JS web UI: 550 lines in web/app.js, web2/foo.js"])

    def test_a_hashed_name_without_a_wasm_is_not_output(self):
        files = {"web/bundle-0123456789abcdef.js": "x\n" * 250}
        self.assertEqual(
            _js(_deviation(files)),
            ["hand-written JS web UI: 250 lines in web/bundle-0123456789abcdef.js"])

    def test_a_gitignored_release_bundle_is_not_the_project_s_code(self):
        # camera-box/.gitignore: `shading-release/` (the pinned shading release
        # assets); its v0.2.8 holds shading's OLD plain app.js
        old = "shading-release/v0.2.8/bkshading-service/web/"
        files = {old + "app.js": "x\n" * 1042, old + "index.html": _page("/app.js"),
                 "web/app.js": "x\n" * 250, "web/index.html": _page("app.js"),
                 "gen/app.js": "x\n" * 300, "gen/extra.js": "x\n" * 300,
                 "gen/index.html": _page("app.js", "extra.js")}
        items = _deviation(files, git_files=["web/app.js", "web/index.html",
                                             "gen/app.js", "gen/index.html"],
                           ignore="shading-release/\ngen/\n")
        self.assertEqual(_js(items),
                         ["hand-written JS web UI: 550 lines in gen/app.js, web/app.js"])


class TestDenyList(unittest.TestCase):
    """(b) every hand-written JS / TS file counts, minus known non-UI files."""

    def test_presenter_tooling_and_reports_are_no_web_ui(self):
        files = {"package.json": '{"devDependencies": {"@playwright/test": "^1.50"}}',
                 "playwright.config.ts": "x\n" * 80, "vite.config.mjs": "x\n" * 300,
                 "web/jest.config.cjs": "x\n" * 300,
                 "ops/companion/generate-profile.mjs": "x\n" * 300,
                 "ops/companion/presenter/package.json": COMPANION_PKG,
                 "ops/companion/presenter/index.js": "x\n" * 500,
                 "playwright-report/index.html": _page(inline=600),
                 "test-results/run-1/trace/index.html": _page(inline=300),
                 "htmlcov/index.html": _page("coverage_html.js", inline=300),
                 "htmlcov/coverage_html.js": "x\n" * 300,
                 "allure-report/app.js": "x\n" * 900,
                 "storybook-static/main.js": "x\n" * 900,
                 "scripts/dev/ai-eval/judge/tests_from_traces.js": "x\n" * 300,
                 "scripts/diag/verify_video_autoplay.mjs": "x\n" * 250,
                 "tools/gen.mjs": "x\n" * 500, "bin/serve.js": "x\n" * 300,
                 "crates/x/scripts/seed.ts": "x\n" * 300,
                 "crates/presenter-ui/Trunk.toml": "[build]\n",
                 "crates/presenter-ui/index.html": _page(inline=40)}
        self.assertEqual(_audit(files), [])

    def test_coverage_is_a_report_only_with_lcov(self):
        files = {"coverage/lcov-report/index.html": _page("prettify.js", inline=300),
                 "coverage/lcov-report/prettify.js": "x\n" * 300,
                 "web/coverage/lcov.info": "TN:\n", "web/coverage/x.js": "x\n" * 300,
                 "ui/coverage/index.html": _page("map.js"),
                 "ui/coverage/map.js": "x\n" * 250}
        self.assertEqual(_js(_deviation(files)),
                         ["hand-written JS web UI: 250 lines in ui/coverage/map.js"])

    def test_js_no_page_loads_still_counts(self):
        # review shapes: a bundler entry, a template-generated `src`, a page
        # held in a .rs / .j2 file
        files = {"src/main.ts": "x\n" * 150, "static/js/app.js": "x\n" * 120,
                 "templates/base.html.j2": '<script src="{{ url_for(\'static\') }}"></script>\n'}
        self.assertEqual(_js(_deviation(files)), [
            "hand-written JS web UI: 270 lines in src/main.ts, static/js/app.js"])

    def test_intercom_app_and_levels_stay_a_deviation(self):
        files = {"Cargo.toml": '[workspace]\nmembers = ["hub"]\n',
                 "hub/Cargo.toml": AXUM_CARGO % "intercom-hub",
                 "VENDORED.md": "# Vendored\n\n- `web/janus.js` (Janus 1.2, MIT)\n",
                 "web/index.html": _page("/janus.js", "/app.js"),
                 "web/levels.html": _page("/levels.js"),
                 "web/app.js": "x();\n" * 1298, "web/levels.js": "x();\n" * 204,
                 "web/janus.js": "x\n" * 3000, "web/sw.js": "x\n" * 40,
                 "tests/e2e/levels-env.js": "x\n" * 300, "tools/bench.mjs": "x\n" * 400}
        self.assertEqual(
            _js(_deviation(files)),
            ["hand-written JS web UI: 1502 lines in web/app.js, web/levels.js"])

    def test_the_true_positives_stay(self):
        files = {  # reaperiem: inline mixer pages; its Trunk iem-ui stays exempt
            "web/mixer.html": _page("/main.js", inline=906),
            "web/reaper_interface/iem_mixer.html": _page("/main.js", inline=279),
            "iem-mixer/iem-ui/Trunk.toml": "[build]\n",
            "iem-mixer/iem-ui/index.html": _page("talkback.js", inline=40),
            "iem-mixer/iem-ui/talkback.js": "x\n" * 300}
        self.assertEqual(_js(_deviation(files)), [
            "hand-written JS web UI: 1185 lines in web/mixer.html, "
            "web/reaper_interface/iem_mixer.html"])
        dashboard = {"crates/wb-service/Cargo.toml": AXUM_CARGO % "wb-service",
                     "crates/wb-service/static/index.html": _page(inline=250)}
        self.assertEqual(_js(_deviation(dashboard)), [
            "hand-written JS web UI: 250 lines in crates/wb-service/static/index.html"])
        dcontroller = {"crates/web/Cargo.toml": LEPTOS_CARGO,
                       "crates/web/index.html": _page("/charts.js", "/mapdrag.js", inline=4),
                       "crates/web/public/charts.js": "x\n" * 300,
                       "crates/web/public/mapdrag.js": "x\n" * 300}
        self.assertEqual(_deviation(dcontroller), [])


# An inline-script page: a commented-out script, JSON and importmap blocks, a
# Prettier-style multi-line <script> tag (120 JS lines), a src script, a plain
# inline script (100 lines + one > 64 KB line, skipped) and a one-liner.
INLINE_PAGE = ("<!DOCTYPE html>\n<html><head>\n<!-- <script>\nold();\n</script> -->\n"
               '<script type="importmap">\n{"imports": {}}\n</script>\n'
               "<SCRIPT type='application/ld+json'>\n{\"a\": 1}\n</SCRIPT>\n"
               '<script\n  type="module"\n  defer\n>\n' + "  a();\n" * 120 + "</script>\n"
               '<script src="/x.js"></script>\n<script>\n' + "  b();\n" * 100
               + "  var big = '" + "y" * 70000 + "';\n</script>\n"
               "<script>one();</script>\n</head></html>\n")


class TestInlineScript(unittest.TestCase):
    """(b) the inline count, under every awk this box has."""

    def test_inline_count_skips_comments_non_js_types_and_long_lines(self):
        self.assertTrue(AWKS)
        for awk in AWKS:
            with self.subTest(awk=awk), _awk_on_path(target=awk):
                self.assertEqual(_js(_deviation({"web/index.html": INLINE_PAGE})), [
                    "hand-written JS web UI: 220 lines in web/index.html"])

    def test_a_failed_count_is_unverified_never_clean(self):
        for script in ("exit 2\n", "exit 0\n", 'echo "n"\n'):
            with self.subTest(awk=script), _awk_on_path(script=script):
                with self.assertRaises(ob._stack.StackCheckUnverified):
                    _deviation({"web/index.html": _page(inline=300)})
                rows = _audit({"web/index.html": _page(inline=300)})
                self.assertEqual([r["kind"] for r in rows], ["unverified-stack"])


class IdentityRunner:
    """dev1 modelled by the local tmp tree: an ssh payload runs via `sh -c`
    AS the login it names; `newlevel` is refused on an account's checkout
    (`/home/<account>` is 0750), the account's own login reads it; the gate
    log answers only to its account."""

    def __init__(self, roots, gate_logs):
        self.roots = roots            # {account: its checkout root}
        self.gate_logs = gate_logs    # {"/home/<account>/.claude/stack-gate.log": text}
        self.calls = []               # (login user, remote command)

    def __call__(self, argv, **kw):
        if argv[:1] == ["gh"]:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        if argv[:1] != ["ssh"]:
            self.calls.append(("<local>", " ".join(argv)))
            return subprocess.run(argv, **kw)
        user, cmd = argv[-2].split("@")[0], argv[-1]
        self.calls.append((user, cmd))
        for path, text in self.gate_logs.items():
            if cmd == "cat " + path:
                owner = path.split("/")[2]
                return subprocess.CompletedProcess(argv, 0 if user == owner else 1,
                                                   text if user == owner else "", "")
        if any(root in cmd and user != acct for acct, root in self.roots.items()):
            return subprocess.CompletedProcess(argv, 1, "", "Permission denied")
        return subprocess.run(["sh", "-c", cmd], **kw)

    def logins_touching(self, needle):
        return {user for user, cmd in self.calls if needle in cmd}


class TestAccountIdentity(unittest.TestCase):
    """(c) the audit reads an account's checkout as that account."""

    def setUp(self):
        p = mock.patch("cli_onboard_exec._local_hostname", return_value="test-box-1333")
        p.start()
        self.addCleanup(p.stop)
        for acct in ("intercom", "iemmixer"):   # the fleet declares both on dev1
            self.assertEqual(cli_account_bootstrap.account_remote(acct)["name"],
                             acct + "@dev1")

    def _fleet(self, d):
        rows, roots = [], {}
        for name, acct in (("intercom", "intercom"), ("reaperiem", "iemmixer"),
                           ("camera-box", "newlevel"), ("bare", None)):
            root = str(Path(d) / ("checkout-" + name))
            Path(root).mkdir()
            init_repo(root, remote="https://github.com/zbynekdrlik/%s.git" % name)
            _put(root, "CLAUDE.md", "## Playbook router\n")
            row = {"name": name, "host": "dev1", "path": root, "default_branch": "main",
                   "stack": "house"}
            if acct:
                row["account"] = acct
            rows.append(row)
            if acct and acct != "newlevel":
                roots[acct] = root
        _put(roots["intercom"], "CLAUDE.md", "## Playbook router\n<!-- airuleset:"
             "stack-exception javascript web UI — zbynekdrlik/intercom#48 -->\n")
        _put(roots["intercom"], "web/index.html", _page("app.js"))
        _put(roots["intercom"], "web/app.js", "x();\n" * 300)
        reg = str(Path(d) / "projects-registry.json")
        ob.save_registry(reg, rows)
        log = ("2026-11-21T10:00:00Z ALLOW-MARKER `%s/web/package.json` framework=react "
               "claude_md=x\n" % roots["intercom"])
        return reg, roots, {"/home/intercom/.claude/stack-gate.log": log}

    def _audit_with(self, account_remote):
        with tempfile.TemporaryDirectory() as d, mock.patch(
                "cli_account_bootstrap.account_remote", side_effect=account_remote):
            reg, roots, logs = self._fleet(d)
            run = IdentityRunner(roots, logs)
            report = ob.audit_registry(reg, run=run, local_host="test-box-1333")
            touched = run.logins_touching(roots["intercom"])
        return report, run, touched

    def test_each_row_is_read_as_its_own_identity(self):
        with tempfile.TemporaryDirectory() as d:
            reg, roots, logs = self._fleet(d)
            run = IdentityRunner(roots, logs)
            report = ob.audit_registry(reg, run=run, local_host="test-box-1333")
            self.assertEqual(run.logins_touching(roots["intercom"]), {"intercom"})
            self.assertEqual(run.logins_touching(roots["iemmixer"]), {"iemmixer"})
            for legacy in ("camera-box", "bare"):
                self.assertEqual(run.logins_touching("checkout-" + legacy), {"newlevel"})
        for name in ("intercom", "reaperiem"):
            kinds = {r["kind"] for r in report[name]}
            self.assertFalse(kinds & {"missing-repo", "unreachable"}, report[name])
        self.assertIn("stack-exception", {r["kind"] for r in report["intercom"]})

    def test_the_gate_log_is_read_as_the_account(self):
        with tempfile.TemporaryDirectory() as d:
            reg, roots, logs = self._fleet(d)
            run = IdentityRunner(roots, logs)
            report = ob.audit_registry(reg, run=run, local_host="test-box-1333")
        self.assertIn(("intercom", "cat /home/intercom/.claude/stack-gate.log"), run.calls)
        uses = [r["detail"] for r in report["intercom"] if r["kind"] == "stack-exception-use"]
        self.assertEqual(len(uses), 1, report["intercom"])
        self.assertIn("framework=react", uses[0])

    def test_a_paused_or_pending_account_is_never_contacted(self):
        real = cli_account_bootstrap.account_remote
        for state, value in (("paused", "owner hold 21.11."), ("pending", True)):
            def flagged(acct, state=state, value=value):
                remote = real(acct)
                return dict(remote, **{state: value}) if acct == "intercom" else remote
            with self.subTest(state=state):
                report, run, touched = self._audit_with(flagged)
                self.assertEqual(touched, set())
                self.assertNotIn("intercom", {user for user, _ in run.calls})
                self.assertEqual([r["kind"] for r in report["intercom"]], ["unreachable"])
                self.assertIn(state, report["intercom"][0]["detail"])

    def test_a_declared_account_without_remote_hosts_is_unreachable(self):
        real = cli_account_bootstrap.account_remote
        report, run, touched = self._audit_with(
            lambda acct: None if acct == "intercom" else real(acct))
        self.assertEqual(touched, set())     # never read as the host login
        self.assertEqual([r["kind"] for r in report["intercom"]], ["unreachable"])
        self.assertIn("no REMOTE_HOSTS entry", report["intercom"][0]["detail"])
        self.assertNotIn("unreachable", {r["kind"] for r in report["reaperiem"]})

    def test_legacy_rows_keep_the_host_login(self):
        ident = cli_onboard_exec.account_identity
        self.assertEqual(ident("dev1", "intercom"), ("intercom@dev1", None))
        self.assertEqual(ident("dev1", "iemmixer"), ("iemmixer@dev1", None))
        for host, acct in (("dev1", None), ("dev1", "newlevel"), ("dev1", "no-such-acct"),
                           ("dev2", "intercom"), ("gk", "gatekeeper")):
            with self.subTest(host=host, acct=acct):
                self.assertEqual(ident(host, acct), (None, None))

    def test_single_path_audit_passes_the_row_s_account(self):
        seen = []

        def fake_audit(entry, host=None, run=None):
            seen.append(host)
            return []
        with tempfile.TemporaryDirectory() as d, mock.patch.object(
                ob, "audit_project", side_effect=fake_audit):
            reg = str(Path(d) / "projects-registry.json")
            root = str(Path(d) / "intercom")
            ob.save_registry(reg, [{"name": "intercom", "host": "dev1", "path": root,
                                    "account": "intercom", "stack": "house"}])
            args = types.SimpleNamespace(registry=reg, path=root, host=None, name=None,
                                         audit=True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ob.cmd_onboard_project(args), 0)
        self.assertEqual(seen, ["intercom@dev1"])


class TestDocs(unittest.TestCase):
    def test_rule_and_skill_name_the_deny_list(self):
        for path in ("rules/house-stack.md", "skills/onboard-project/SKILL.md"):
            text = (REPO / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                for needle in ("wasm-bindgen / Trunk output", "generated reports",
                               "tooling dirs", "gitignored", "non-JS `type=`"):
                    self.assertTrue(needle in text, needle)
                self.assertFalse("served by an HTML page" in text)
        skill = (REPO / "skills/onboard-project/SKILL.md").read_text(encoding="utf-8")
        for needle in ("as its own account", "no REMOTE_HOSTS entry"):
            self.assertTrue(needle in skill, needle)


if __name__ == "__main__":
    unittest.main()
