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

    def test_env_step_precedes_the_session_and_installs_run_last(self):
        """The env wiring lands before the tmux session; the downloads (which
        fail loud on a box without pipx/npx) run after the checkout, the tmux
        session and the gh shim, so those never depend on them."""
        s = self.script
        env = s.index("# 8b. Shell env of the shared toolchain")
        self.assertLess(env, s.index("# 9. Project checkout"))
        self.assertLess(env, s.index("# 10. Project tmux"))
        installs = s.index("# 12. Shared project toolchain")
        self.assertLess(s.index("# 11. Project-account GitHub App token shim"), installs)
        self.assertLess(installs, s.index("=== Read-back ==="))

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
                "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright PLAYWRIGHT_SKIP_BROWSER_GC=1"
                " npx -y playwright@1.58.2 install --with-deps chromium webkit",
                "npx -y playwright@%s install --with-deps chromium"
                % pw.PLAYWRIGHT_PW_VERSION,
                "chmod -R a+rX,go-w /opt/ms-playwright",
                "mv -f \"$t\" /etc/airuleset/project-toolchain.sh"):
            self.assertIn(needle, s)
        for marker in pw.pinned_build_markers():   # the resolver's own predicate
            self.assertIn("/opt/ms-playwright/%s" % marker, s)

    def test_root_parts_run_from_root_dir_with_isolated_python(self):
        """#1190 lesson: root never runs code from the invoker's cwd."""
        steps = self.script[self.script.index("# 8b."):]
        parts = steps.count("\n(\numask 022\n")
        self.assertEqual(parts, 4)
        self.assertEqual(steps.count("\n(\numask 022\ncd /\n"), parts)
        self.assertIn("python3 -I -c", steps)
        self.assertNotRegex(steps, r"python3 -c")

    def test_never_a_per_account_install_path(self):
        step = self.script[self.script.index("# 12."):self.script.index("=== Read-back")]
        for bad in ("~/.cargo", "$HOME/.cargo", "~/.rustup", "$HOME/.rustup",
                    ".cache/ms-playwright", "--user"):
            self.assertNotIn(bad, step)

    def test_every_project_account_gets_the_baseline(self):
        s = _render("claudy")
        self.assertIn("# 12. Shared project toolchain", s)
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

# The fake rustup-init leaves its tree world-writable, like a sloppy installer
# would; the step's chmod must take that back.
_CURL_STUB = """#!/usr/bin/env bash
# fake curl: the downloaded rustup-init creates a logging rustup proxy
echo "curl $*" >> "$LOG"
out=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done
cat > "$out" << 'EOF'
echo "rustup-init $*" >> "$LOG"
mkdir -p "$CARGO_HOME/bin"
printf '#!/usr/bin/env bash\\necho "rustup $*" >> "$LOG"\\n' > "$CARGO_HOME/bin/rustup"
chmod 0777 "$CARGO_HOME/bin/rustup" "$CARGO_HOME/bin"
EOF
"""

_NPX_STUB = """#!/usr/bin/env bash
# fake npx: `playwright install` fills $PLAYWRIGHT_BROWSERS_PATH (world-writable)
echo "npx PWB=$PLAYWRIGHT_BROWSERS_PATH GC=${PLAYWRIGHT_SKIP_BROWSER_GC:-} $*" >> "$LOG"
echo "npx cwd=$PWD" >> "$LOG"
[ -n "${NPX_FAIL:-}" ] && exit 1
for n in chromium-%(b)s chromium_headless_shell-%(b)s; do
  [ -n "${NPX_HALF:-}" ] && [ "$n" = chromium_headless_shell-%(b)s ] && continue
  mkdir -p "$PLAYWRIGHT_BROWSERS_PATH/$n"; touch "$PLAYWRIGHT_BROWSERS_PATH/$n/INSTALLATION_COMPLETE"
  chmod 0777 "$PLAYWRIGHT_BROWSERS_PATH/$n"
done
""" % {"b": pw.PLAYWRIGHT_CHROMIUM_BUILD}


class TestSystemStepRuns(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tc-1201-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.stub = self.tmp / "bin"
        self.stub.mkdir()
        for name, body in (("pipx", _PIPX_STUB), ("curl", _CURL_STUB),
                           ("npx", _NPX_STUB)):
            (self.stub / name).write_text(body)
            (self.stub / name).chmod(0o755)
        self.log = self.tmp / "log"
        self.root = self.tmp / "root"
        self.scratch = self.tmp / "tmpdir"
        self.scratch.mkdir()
        self.step = tc.render_system_step(
            bootstrap.account_spec("fohmixer"),
            rust_root=str(self.root / "opt/rust"),
            pipx_home=str(self.root / "opt/pipx"),
            pipx_bin=str(self.root / "usr/local/bin"),
            pw_dir=str(self.root / "opt/ms-playwright"))
        self.env = {"PATH": "%s:/usr/bin:/bin" % self.stub, "LOG": str(self.log),
                    "STATE": str(self.tmp / "pipx-state"), "HOME": str(self.tmp),
                    "TMPDIR": str(self.scratch)}

    def _run(self, path=None, **extra):
        env = dict(self.env, **extra)
        if path:
            env["PATH"] = path
        return subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n" + self.step],
                              text=True, capture_output=True, env=env,
                              cwd=str(self.tmp))

    def test_installs_everything_once_then_is_idempotent(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = self.log.read_text()
        self.assertIn("pipx install ruff==0.16.2", log)
        self.assertEqual(log.count("rustup-init -y --no-modify-path"), 1)
        self.assertIn("rustup toolchain install 1.98.1", log)
        self.assertIn("rustup target add --toolchain 1.98.1 wasm32-unknown-unknown", log)
        self.assertIn("npx PWB=%s GC=1 -y playwright@1.58.2 install --with-deps "
                      "chromium webkit" % (self.root / "opt/ms-playwright"), log)
        self.assertIn("npx cwd=/\n", log)             # never the invoker's cwd
        self.log.write_text("")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        again = self.log.read_text()
        self.assertNotIn("rustup-init", again)          # rustup kept
        self.assertNotIn("pipx install", again)         # the pinned version holds
        self.assertIn("pipx ruff: 0.16.2", r.stdout)

    def test_the_shared_trees_end_read_only_for_accounts(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for tree in ("opt/rust", "opt/ms-playwright"):
            for path in [self.root / tree, *(self.root / tree).rglob("*")]:
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode & 0o022, 0, (path, oct(mode)))
                self.assertEqual(mode & 0o004, 0o004, (path, oct(mode)))

    def test_the_rustup_installer_is_removed(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(list(self.scratch.iterdir()), [])

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

    def test_a_half_chromium_build_fails_the_script(self):
        r = self._run(NPX_HALF="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no complete pinned chromium build", r.stderr)

    def test_a_box_without_pipx_fails_loud(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        for name in ("curl", "npx"):
            (bare / name).symlink_to(self.stub / name)
        r = self._run(path=str(bare))     # nothing else: no pipx anywhere
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pipx is missing", r.stderr)


class TestEnvStepRuns(unittest.TestCase):
    """Step 8b as rendered: the root env file, then the account's rc wiring
    through a PATH `runuser` stub (runs the body under a temp HOME)."""

    def test_writes_the_env_file_and_wires_the_account(self):
        tmp = Path(tempfile.mkdtemp(prefix="tc-env-1201-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)])
        home, stub = tmp / "home", tmp / "bin"
        home.mkdir()
        stub.mkdir()
        (stub / "runuser").write_text(
            '#!/usr/bin/env bash\n[ "$1" = -l ] && [ "$3" = -c ] || exit 99\n'
            'HOME="$STEP_HOME" exec bash -c "$4"\n')
        (stub / "runuser").chmod(0o755)
        env_file = tmp / "etc/airuleset/project-toolchain.sh"
        step = tc.render_account_env_step(bootstrap.account_spec("fohmixer"),
                                          env_file=str(env_file))
        r = subprocess.run(["bash", "-c", "set -euo pipefail\nACCOUNT=projx\n" + step],
                           text=True, capture_output=True,
                           env={"PATH": "%s:/usr/bin:/bin" % stub, "HOME": str(tmp),
                                "STEP_HOME": str(home)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o644)
        self.assertEqual(env_file.read_text(), tc.render_env_file())
        self.assertIn(str(env_file), (home / ".profile").read_text())


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


class TestNoPerAccountBrowsers(unittest.TestCase):
    """A project account's own `airuleset.py install` never downloads a
    per-user chromium when /opt lacks the pinned build (a pin bump): that is
    the duplication the owner forbade; it asks for the root bootstrap instead."""

    def _ensure(self, project_account):
        cache = Path(tempfile.mkdtemp(prefix="tc-pw-1201-")) / ".cache" / "ms-playwright"
        self.addCleanup(subprocess.run, ["rm", "-rf", str(cache.parent.parent)])
        calls = []
        with mock.patch("subprocess.run", side_effect=lambda *a, **k: calls.append(a)
                        or subprocess.CompletedProcess(a, 1, "", "")), \
                mock.patch.object(pw.shutil, "which", return_value="/usr/bin/npx"), \
                mock.patch("sys.stderr") as err:
            pw.ensure_playwright_browsers(cache_dir=cache, project_account=project_account,
                                          sleep=lambda s: None, sudo_ok=lambda: False,
                                          probe_rc=lambda p: None)
        return calls, "".join(c.args[0] for c in err.write.call_args_list)

    def test_a_project_account_refuses_the_per_user_install(self):
        calls, err = self._ensure(True)
        self.assertEqual(calls, [])
        self.assertIn("re-run the root account bootstrap", err)

    def test_any_other_account_still_installs(self):
        calls, _ = self._ensure(False)
        self.assertTrue(any("install" in c[0] for c in calls), calls)

    def test_the_default_reads_the_declaration(self):
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {user: {}}):
            self.assertTrue(pw._is_project_account())
        self.assertEqual(pw._is_project_account(), user in bootstrap.SERVICE_ACCOUNTS)


if __name__ == "__main__":
    unittest.main()
