"""#1333 audit precision: the three defects the first real
`onboard-project --audit` showed (21.11.2026).

(a) Trunk / wasm-bindgen OUTPUT is no hand-written JS: a `.js` beside its
    `<stem>_bg.wasm` / `<stem>.wasm`, or a Trunk hashed `<name>-<16 hex>.js`
    beside a `.wasm`, and the loader page of a Trunk dist dir (camera-box's
    `shading-release/v0.2.1x/bkshading-service/web/`). A gitignored tree (a
    pinned release bundle) is never the project's code; tracked files count.
(b) JS is a web UI only where an HTML page serves it: a `<script src>`, an
    import in the page's inline script, and the modules those import. Tooling
    (`ops/companion/generate-profile.mjs`, `scripts/dev/...`) and generated
    reports (`playwright-report`, `test-results`, `coverage`, `.nyc_output`)
    never count (presenter). The true positives stay: intercom's served
    `web/app.js` + `web/levels.js`, reaperiem's inline `web/mixer.html`, the
    wall-bridge inline dashboard; dcontroller's Leptos `public/*.js` stay exempt.
(c) An account row is read AS its account (`cli_account_bootstrap.
    account_remote`): `newlevel` cannot read `/home/<account>`, so every
    account row read `missing-repo`. The gate log is read the same way; a
    paused account is never contacted; legacy rows keep the host login.
"""

import contextlib
import io
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


def _put(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _page(*srcs, inline=0):
    """An .html page serving `srcs` by `<script src>`, plus `inline` lines of
    inline script."""
    body = "".join('  <script src="%s"></script>\n' % s for s in srcs)
    if inline:
        body += "  <script>\n" + "    tick();\n" * inline + "  </script>\n"
    return "<!DOCTYPE html>\n<html><body>\n%s</body></html>\n" % body


def _trunk_index(stem, inline=60):
    """camera-box's shading-release/v0.2.10 index.html shape: Trunk's
    modulepreload + inline module loader, then the page's own inline script."""
    return ('<!DOCTYPE html><html><head>\n'
            '<link rel="modulepreload" href="/%s.js" crossorigin="anonymous">\n'
            '<link rel="preload" href="/%s_bg.wasm" as="fetch" type="application/wasm">\n'
            '    <script type="module">\n'
            "import init, * as bindings from '/%s.js';\n"
            "const wasm = await init({ module_or_path: '/%s_bg.wasm' });\n"
            "window.wasmBindings = bindings;\n"
            "    </script>\n    <script>\n%s    </script>\n"
            "</head><body></body></html>\n"
            % (stem, stem, stem, stem, "  navigator.serviceWorker.register('/sw.js');\n"
               * inline))


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


class TestWasmBindgenOutput(unittest.TestCase):
    """(a) camera-box: Trunk / wasm-bindgen output is not hand-written JS."""

    def test_camera_box_trunk_release_dirs_are_output(self):
        files = {}
        for ver, h in (("v0.2.10", "a3a7412ddb48ffa2"), ("v0.2.11", "c69fc818b0d3d5ba"),
                       ("v0.2.12", "46b0c911f9ccbdaf"), ("v0.2.13", "86ef7353219f7ac4")):
            web = "shading-release/%s/bkshading-service/web/" % ver
            stem = "bkshading-panel-" + h
            files.update({web + stem + ".js": "export function x() {}\n" * 1237,
                          web + stem + "_bg.wasm": "\0asm",
                          web + "index.html": _trunk_index(stem),
                          web + "sw.js": "self.x = 1;\n" * 17})
        self.assertEqual(_deviation(files), [])

    def test_a_wasm_bindgen_pair_is_output_but_its_neighbour_is_not(self):
        files = {"web/engine.js": "x\n" * 300, "web/engine_bg.wasm": "\0asm",
                 "web/app.js": "x\n" * 250,
                 "web/index.html": _page("engine.js", "app.js"),
                 "web2/foo.js": "x\n" * 300, "web2/foo.wasm": "\0asm",
                 "web2/index.html": _page("foo.js")}
        self.assertEqual(_js(_deviation(files)),
                         ["hand-written JS web UI: 250 lines in web/app.js"])

    def test_a_hashed_name_without_a_wasm_is_not_output(self):
        files = {"web/bundle-0123456789abcdef.js": "x\n" * 250,
                 "web/index.html": _page("/bundle-0123456789abcdef.js")}
        self.assertEqual(
            _js(_deviation(files)),
            ["hand-written JS web UI: 250 lines in web/bundle-0123456789abcdef.js"])

    def test_a_gitignored_release_bundle_is_not_the_project_s_code(self):
        # camera-box/.gitignore: `shading-release/` (the pinned shading release
        # assets); its v0.2.8 holds shading's OLD plain app.js, served by its page
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


class TestServedWebUi(unittest.TestCase):
    """(b) JS counts only where an HTML page serves it."""

    def test_presenter_tooling_and_reports_are_no_web_ui(self):
        files = {"package.json": '{"devDependencies": {"@playwright/test": "^1.50"}}',
                 "playwright.config.ts": "x\n" * 80,
                 "ops/companion/generate-profile.mjs": "x\n" * 300,
                 "ops/companion/presenter/package.json": COMPANION_PKG,
                 "ops/companion/presenter/index.js": "x\n" * 500,
                 "playwright-report/index.html": _page(inline=600),
                 "test-results/run-1/trace/index.html": _page(inline=300),
                 "coverage/lcov-report/index.html": _page("prettify.js", inline=300),
                 "coverage/lcov-report/prettify.js": "x\n" * 300,
                 "scripts/dev/ai-eval/judge/tests_from_traces.js": "x\n" * 300,
                 "scripts/diag/verify_video_autoplay.mjs": "x\n" * 250,
                 "crates/presenter-ui/Trunk.toml": "[build]\n",
                 "crates/presenter-ui/index.html": _page(inline=40)}
        self.assertEqual(_audit(files), [])

    def test_nyc_output_is_pruned(self):
        self.assertEqual(_deviation({"web/.nyc_output/index.html": _page(inline=400)}), [])

    def test_intercom_served_app_and_levels_stay_a_deviation(self):
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
        wall_bridge = {"crates/wb-service/Cargo.toml": AXUM_CARGO % "wb-service",
                       "crates/wb-service/static/index.html": _page(inline=250)}
        self.assertEqual(_js(_deviation(wall_bridge)), [
            "hand-written JS web UI: 250 lines in crates/wb-service/static/index.html"])
        dcontroller = {"crates/web/Cargo.toml": LEPTOS_CARGO,
                       "crates/web/index.html": _page("/charts.js", "/mapdrag.js", inline=4),
                       "crates/web/public/charts.js": "x\n" * 300,
                       "crates/web/public/mapdrag.js": "x\n" * 300}
        self.assertEqual(_deviation(dcontroller), [])

    def test_modules_a_served_script_imports_count(self):
        files = {"web/index.html": '<script type="module" src="./main.js"></script>\n',
                 "web/main.js": ("import { start } from './ui/view.js';\n"
                                 "import './ui/boot';\nimport { h } from 'preact';\n"
                                 + "start();\n" * 17),
                 "web/ui/view.js": ('import { fmt } from "../lib/util";\n'
                                    + "x\n" * 249),
                 "web/ui/boot.ts": "x\n" * 30, "web/lib/util.js": "x\n" * 20,
                 "web/lib/unused.js": "x\n" * 400, "tools/gen.mjs": "x\n" * 500}
        self.assertEqual(_js(_deviation(files)), [
            "hand-written JS web UI: 320 lines in web/lib/util.js, web/main.js, "
            "web/ui/boot.ts …"])

    def test_inline_imports_and_root_absolute_refs_resolve(self):
        files = {"app2/index.html": ("<script type=\"module\">import { boot } from "
                                     "'/js/app.js'; boot();</script>\n"),
                 "app2/js/app.js": "x\n" * 260, "app2/js/old.js": "x\n" * 300,
                 "site/pages/a.html": ('<script src=/assets/app.js?v=3></script>\n'
                                       '<script src="https://cdn.example/x.js"></script>\n'),
                 "site/assets/app.js": "x\n" * 250}
        self.assertEqual(_js(_deviation(files)), [
            "hand-written JS web UI: 510 lines in app2/js/app.js, site/assets/app.js"])


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

    def test_a_paused_account_is_never_contacted(self):
        real = cli_account_bootstrap.account_remote

        def paused(acct):
            remote = real(acct)
            return dict(remote, paused="owner hold 21.11.") if acct == "intercom" else remote
        with tempfile.TemporaryDirectory() as d, mock.patch(
                "cli_account_bootstrap.account_remote", side_effect=paused):
            reg, roots, logs = self._fleet(d)
            run = IdentityRunner(roots, logs)
            report = ob.audit_registry(reg, run=run, local_host="test-box-1333")
            self.assertEqual(run.logins_touching(roots["intercom"]), set())
        self.assertNotIn("intercom", {user for user, _ in run.calls})
        self.assertEqual([r["kind"] for r in report["intercom"]], ["unreachable"])
        self.assertIn("paused", report["intercom"][0]["detail"])

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
    def test_rule_and_skill_name_the_served_page_rule(self):
        for path in ("rules/house-stack.md", "skills/onboard-project/SKILL.md"):
            text = (REPO / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                for needle in ("served by an HTML page", "wasm-bindgen / Trunk output",
                               "gitignored"):
                    self.assertTrue(needle in text, needle)
        skill = (REPO / "skills/onboard-project/SKILL.md").read_text(encoding="utf-8")
        self.assertTrue("as its own account" in skill)


if __name__ == "__main__":
    unittest.main()
