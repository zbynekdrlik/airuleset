"""#1201: project accounts share ONE system toolchain, installed by the root
bootstrap, and their shells (tmux included) start as LOGIN shells.

Owner, 30.9.2026: the fohmixer project account had installed its own rustup,
Playwright browsers and pipx ruff (~2 GB) on a disk at 90 %, because the #1184
bootstrap provisioned no toolchain. Covers:
  * the ``tools`` declaration grammar + validator (fail-closed, cross-account
    pipx pin conflicts on one host);
  * the plan (baseline ``PROJECT_TOOLCHAIN`` + fohmixer's repo pins);
  * the rendered root bootstrap: steps 8b/8c before the checkout and the tmux
    session, the system installs, the login tmux pane, ``accounts verify`` as
    the LAST next step;
  * the rendered bash RUN under a fake root with PATH stubs (pipx / curl /
    rustup / npx), twice (idempotent), and failing LOUD when an install fails;
  * the env file and the account rc wiring, proven by a real ``bash -lc``.
"""
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_playwright_mcp as pw  # noqa: E402
import cli_project_toolchain as tc  # noqa: E402
import cli_webterm_only as wo  # noqa: E402

_FAKE_TIMO = "ssh-ed25519 " + "AAAA" + "X" * 64 + " webterm-timo-controller"


def _render(account):
    with mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS, {"timo": _FAKE_TIMO}):
        return bootstrap.render_root_bootstrap(account)


def _spec(**over):
    spec = {"host": "dev1", "sudo": False, "reach": [], "secrets": [],
            "webterm_sessions": {}}
    spec.update(over)
    return spec


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #
class TestToolsGrammar(unittest.TestCase):

    def test_shipped_table_is_clean(self):
        self.assertEqual(bootstrap.validate_all(), {})

    def test_fohmixer_declares_its_repo_pins(self):
        tools = bootstrap.account_spec("fohmixer")["tools"]
        for t in ("rust-toolchain:1.98.1", "rust-component:llvm-tools-preview",
                  "rust-target:wasm32-unknown-unknown", "pipx:ruff==0.16.2",
                  "playwright:1.58.2/chromium", "playwright:1.58.2/webkit"):
            self.assertIn(t, tools)

    def test_every_baseline_entry_parses(self):
        for t in tc.PROJECT_TOOLCHAIN:
            tc.parse_tool(t)

    def test_bad_entries_are_refused(self):
        for bad in ("pipx:ruff; rm -rf /", "pipx:", "rust-toolchain:../x",
                    "rust-toolchain:Stable", "rust-component:a b",
                    "rust-target:$(id)", "playwright:1.2/chrome",
                    "playwright:latest/chromium", "playwright:1.58.2",
                    "apt:curl", "ruff", 7, "pipx:ruff==0.1\n"):
            errs = bootstrap.validate_account(
                "projx", _spec(tools=["pipx:black", bad]))
            self.assertTrue(any("tool" in e for e in errs), (bad, errs))

    def test_tools_must_be_a_list_without_duplicates(self):
        self.assertIn("tools must be a list",
                      bootstrap.validate_account("projx", _spec(tools="pipx:x")))
        errs = bootstrap.validate_account("projx", _spec(tools=["pipx:x", "pipx:x"]))
        self.assertTrue(any("twice" in e for e in errs), errs)

    def test_two_accounts_on_one_host_cannot_pin_one_venv_differently(self):
        other = _spec(tools=["pipx:ruff==0.1.0"])
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {"projx": other}):
            errs = bootstrap.validate_all()
        self.assertIn("projx", errs)
        self.assertTrue(any("fohmixer" in e and "ruff" in e for e in errs["projx"]))
        self.assertTrue(any("projx" in e for e in errs["fohmixer"]))

    def test_the_same_pin_on_another_host_is_fine(self):
        other = _spec(host="dev2", tools=["pipx:ruff==0.1.0"])
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {"projx": other}):
            self.assertEqual(bootstrap.validate_all(), {})


class TestPlan(unittest.TestCase):

    def test_baseline_for_an_account_without_tools(self):
        p = tc.plan(_spec())
        self.assertEqual(p["pipx"], [("ruff", None)])
        self.assertEqual(p["rust"], [("stable", ["rustfmt", "clippy"], [])])
        self.assertEqual(p["playwright"], [(pw.PLAYWRIGHT_PW_VERSION, ["chromium"])])

    def test_fohmixer_pins_ride_on_top_of_the_baseline(self):
        p = tc.plan(bootstrap.account_spec("fohmixer"))
        self.assertEqual(p["pipx"], [("ruff", "0.16.2")])
        self.assertEqual(p["rust"], [
            ("stable", ["rustfmt", "clippy"], []),
            ("1.98.1", ["rustfmt", "clippy", "llvm-tools-preview"],
             ["wasm32-unknown-unknown"])])
        self.assertIn(("1.58.2", ["chromium", "webkit"]), p["playwright"])
        self.assertIn((pw.PLAYWRIGHT_PW_VERSION, ["chromium"]), p["playwright"])

    def test_components_without_a_toolchain_go_to_stable(self):
        p = tc.plan(_spec(tools=["rust-component:rust-src",
                                 "rust-target:wasm32-unknown-unknown"]))
        self.assertEqual(p["rust"], [("stable", ["rustfmt", "clippy", "rust-src"],
                                      ["wasm32-unknown-unknown"])])


# --------------------------------------------------------------------------- #
# the rendered root bootstrap
# --------------------------------------------------------------------------- #
class TestBootstrapRender(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.script = _render("fohmixer")

    def test_script_is_valid_bash(self):
        for account in ("fohmixer", "claudy", "camera-box"):
            r = subprocess.run(["bash", "-n"], input=_render(account), text=True,
                               capture_output=True)
            self.assertEqual(r.returncode, 0, (account, r.stderr))

    def test_toolchain_steps_precede_the_checkout_and_the_tmux_session(self):
        s = self.script
        self.assertLess(s.index("# 8b. Shared project toolchain"),
                        s.index("# 8c. Shell env of the shared toolchain"))
        self.assertLess(s.index("# 8c. Shell env"), s.index("# 9. Project checkout"))
        self.assertLess(s.index("# 8c. Shell env"), s.index("# 10. Project tmux"))

    def test_system_installs_are_rendered(self):
        s = self.script
        for needle in (
                "PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin",
                "pipx install ruff==0.16.2",
                "RUSTUP_HOME=/opt/rust/rustup CARGO_HOME=/opt/rust/cargo",
                "https://sh.rustup.rs",
                "--no-modify-path",
                "rustup toolchain install 1.98.1 --no-self-update --profile minimal"
                " --component rustfmt --component clippy --component "
                "llvm-tools-preview --target wasm32-unknown-unknown",
                "rustup default stable",
                "chmod -R a+rX,go-w /opt/rust",
                "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright npx -y playwright@1.58.2"
                " install --with-deps chromium webkit",
                "npx -y playwright@%s install --with-deps chromium"
                % pw.PLAYWRIGHT_PW_VERSION,
                "chromium_headless_shell-%s/INSTALLATION_COMPLETE"
                % pw.PLAYWRIGHT_CHROMIUM_BUILD,
                "mv -f \"$t\" /etc/airuleset/project-toolchain.sh"):
            self.assertIn(needle, s)

    def test_never_a_per_account_install_path(self):
        step = self.script[self.script.index("# 8b."):self.script.index("# 8c.")]
        for bad in ("~/.cargo", "$HOME/.cargo", "~/.rustup", "$HOME/.rustup",
                    ".cache/ms-playwright", "--user"):
            self.assertNotIn(bad, step)

    def test_every_project_account_gets_the_baseline(self):
        s = _render("claudy")
        self.assertIn("# 8b. Shared project toolchain", s)
        self.assertIn("pipx install ruff", s)
        self.assertIn("rustup toolchain install stable", s)

    def test_tmux_session_starts_a_login_shell(self):
        step = self.script[self.script.index("# 10. Project tmux"):
                           self.script.index("# 11.")]
        self.assertIn("tmux new-session -d -s fohmixer", step)
        self.assertIn("exec bash -l", step)

    def test_accounts_verify_is_the_last_next_step(self):
        lines = [ln for ln in self.script.splitlines() if ln.startswith("echo ")]
        self.assertIn("accounts verify $ACCOUNT", lines[-1])
        self.assertIn("REQUIRED", lines[-1])
        self.assertIn("accounts verify $ACCOUNT", _render("claudy").splitlines()[-1])


# --------------------------------------------------------------------------- #
# the rendered bash, RUN under a fake root
# --------------------------------------------------------------------------- #
_PIPX_STUB = """#!/usr/bin/env bash
# fake pipx: `list --json` reports $STATE/<pkg> as installed versions
echo "pipx $*" >> "$LOG"
if [ "$1" = list ]; then
  printf '{"venvs": {'; sep=""
  for f in "$STATE"/*; do [ -e "$f" ] || continue
    printf '%s"%s": {"metadata": {"main_package": {"package_version": "%s"}}}' \\
      "$sep" "$(basename "$f")" "$(cat "$f")"; sep=", "; done
  echo '}}'; exit 0
fi
spec="${@: -1}"; pkg="${spec%%==*}"; ver="${spec#*==}"; [ "$ver" = "$spec" ] && ver=9.9.9
mkdir -p "$STATE"; echo "$ver" > "$STATE/$pkg"
mkdir -p "$PIPX_BIN_DIR"; touch "$PIPX_BIN_DIR/$pkg"
"""

_CURL_STUB = """#!/usr/bin/env bash
# fake curl: the downloaded rustup-init creates a logging rustup proxy
echo "curl $*" >> "$LOG"
out=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done
cat > "$out" << 'EOF'
echo "rustup-init $*" >> "$LOG"
mkdir -p "$CARGO_HOME/bin"
printf '#!/usr/bin/env bash\\necho "rustup $*" >> "$LOG"\\n' > "$CARGO_HOME/bin/rustup"
chmod 0755 "$CARGO_HOME/bin/rustup"
EOF
"""

_NPX_STUB = """#!/usr/bin/env bash
# fake npx: `playwright install` fills $PLAYWRIGHT_BROWSERS_PATH
echo "npx PWB=$PLAYWRIGHT_BROWSERS_PATH $*" >> "$LOG"
[ -n "${NPX_FAIL:-}" ] && exit 1
d="$PLAYWRIGHT_BROWSERS_PATH/chromium_headless_shell-%s"
mkdir -p "$d"; touch "$d/INSTALLATION_COMPLETE"
""" % pw.PLAYWRIGHT_CHROMIUM_BUILD


class TestSystemStepRuns(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tc-1201-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        stub = self.tmp / "bin"
        stub.mkdir()
        for name, body in (("pipx", _PIPX_STUB), ("curl", _CURL_STUB),
                           ("npx", _NPX_STUB)):
            (stub / name).write_text(body)
            (stub / name).chmod(0o755)
        self.log = self.tmp / "log"
        self.root = self.tmp / "root"
        self.step = tc.render_system_step(
            bootstrap.account_spec("fohmixer"),
            rust_root=str(self.root / "opt/rust"),
            pipx_home=str(self.root / "opt/pipx"),
            pipx_bin=str(self.root / "usr/local/bin"),
            pw_dir=str(self.root / "opt/ms-playwright"),
            env_file=str(self.root / "etc/airuleset/project-toolchain.sh"))
        self.env = {"PATH": "%s:/usr/bin:/bin" % stub, "LOG": str(self.log),
                    "STATE": str(self.tmp / "pipx-state"), "HOME": str(self.tmp)}

    def _run(self, **extra):
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + self.step],
                              text=True, capture_output=True,
                              env=dict(self.env, **extra))

    def test_installs_everything_once_then_is_idempotent(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = self.log.read_text()
        self.assertIn("pipx install ruff==0.16.2", log)
        self.assertEqual(log.count("rustup-init -y --no-modify-path"), 1)
        self.assertIn("rustup toolchain install 1.98.1", log)
        self.assertIn("rustup target add --toolchain 1.98.1 wasm32-unknown-unknown", log)
        self.assertIn("npx PWB=%s -y playwright@1.58.2 install --with-deps chromium "
                      "webkit" % (self.root / "opt/ms-playwright"), log)
        env_file = self.root / "etc/airuleset/project-toolchain.sh"
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o644)
        self.assertIn("RUSTUP_HOME=%s" % (self.root / "opt/rust/rustup"),
                      env_file.read_text())
        self.log.write_text("")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        again = self.log.read_text()
        self.assertNotIn("rustup-init", again)          # rustup kept
        self.assertNotIn("pipx install", again)         # the pinned version holds
        self.assertIn("pipx ruff: 0.16.2", r.stdout)

    def test_a_drifted_pin_is_reinstalled(self):
        state = self.tmp / "pipx-state"
        state.mkdir()
        (state / "ruff").write_text("0.15.0\n")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pipx install --force ruff==0.16.2", self.log.read_text())

    def test_a_failed_install_fails_the_script(self):
        r = self._run(NPX_FAIL="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / "etc/airuleset/project-toolchain.sh").exists())


# --------------------------------------------------------------------------- #
# the account's shells
# --------------------------------------------------------------------------- #
class TestAccountShells(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tc-home-1201-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env_file = self.tmp / "project-toolchain.sh"
        self.env_file.write_text(tc.render_env_file(rust_bin="/opt/rust/cargo/bin"))

    def _bash(self, *args, env=None):
        return subprocess.run(["bash", *args], text=True, capture_output=True,
                              env=env or {"HOME": str(self.home),
                                          "PATH": "/usr/bin:/bin"})

    def test_env_file_puts_local_bin_first_and_is_idempotent(self):
        r = self._bash("-c", ". %s; . %s; echo \"$PATH|$RUSTUP_HOME|"
                       "$PLAYWRIGHT_BROWSERS_PATH\"" % (self.env_file, self.env_file))
        path, rustup_home, pwb = r.stdout.strip().split("|")
        parts = path.split(":")
        self.assertEqual(parts[:2], [str(self.home / ".local/bin"),
                                     "/opt/rust/cargo/bin"])
        self.assertEqual(len(parts), len(set(parts)))
        self.assertEqual(rustup_home, tc.RUSTUP_HOME)
        self.assertEqual(pwb, tc.PLAYWRIGHT_DIR)

    def test_env_file_is_posix_sh(self):
        r = subprocess.run(["sh", "-n", str(self.env_file)], capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_rc_wiring_makes_a_login_shell_find_local_bin(self):
        body = tc.render_account_body(env_file=str(self.env_file))
        for _ in range(2):   # idempotent
            r = self._bash("-c", body)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.home / ".local/bin").is_dir())
        for rc in (".profile", ".bashrc"):
            text = (self.home / rc).read_text()
            self.assertEqual(text.count(tc.RC_MARK_START), 1, rc)
            self.assertIn(str(self.env_file), text)
        # `bash -lc` (a login, NON-interactive shell: the verify probe, ssh) and
        # an interactive non-login shell both get ~/.local/bin
        login = self._bash("-lc", "echo $PATH")
        self.assertIn(str(self.home / ".local/bin"), login.stdout.split(":")[0])
        inter = self._bash("-ic", "echo $PATH")
        self.assertIn(str(self.home / ".local/bin"), inter.stdout)

    def test_an_existing_bash_profile_is_wired_too(self):
        (self.home / ".bash_profile").write_text("# user file\n")
        r = self._bash("-c", tc.render_account_body(env_file=str(self.env_file)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(tc.RC_MARK_START, (self.home / ".bash_profile").read_text())

    def test_the_rendered_step_runs_as_the_account(self):
        step = tc.render_account_env_step(bootstrap.account_spec("fohmixer"))
        self.assertIn('runuser -l "$ACCOUNT" -c', step)
        self.assertIn(tc.ENV_FILE, step)


if __name__ == "__main__":
    unittest.main()
