"""#1384 — cap cargo's parallel jobs per account (`cli_cargo_jobs`).

cargo's default `build.jobs` is `nproc` (16 on dev1), and one workspace build
started up to 16 rustc at 1-2 GB each; earlyoom then killed other projects'
work. `install` writes ONE marker line `jobs = min(4, nproc)` under `[build]`
in the account's `~/.cargo/config.toml`:

  * a key-level merge (tomllib) that keeps every other byte of the file;
  * an account's own `jobs` (no marker) is kept, a repo's own
    `.cargo/config.toml` is never touched (cargo gives it precedence);
  * anything the merge cannot do safely is refused with nothing written.

`RealCargo` proves with the REAL cargo binary that a non-interactive,
non-login shell (the shape of a Claude Bash call) honours the file. It needs
cargo on the box, so CI's python container deselects it
(`.github/box-bound-tests.txt`); it FAILS (never skips) on a box without cargo.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_cargo_jobs as cj  # noqa: E402


class TestPolicy(unittest.TestCase):
    def test_cap_is_four_or_the_core_count(self):
        self.assertEqual(cj.CARGO_JOBS_CAP, 4)
        self.assertEqual(cj.policy_jobs(16), 4)
        self.assertEqual(cj.policy_jobs(2), 2)
        self.assertEqual(cj.policy_jobs(0), 1)

    def test_managed_line_carries_the_marker(self):
        line = cj.managed_line(4)
        self.assertTrue(line.startswith("jobs = 4"))
        self.assertIn(cj.MARKER, line)
        self.assertEqual(tomllib.loads("[build]\n" + line + "\n"), {"build": {"jobs": 4}})


class TestPlanEdit(unittest.TestCase):
    def test_no_file_creates_one(self):
        new, status = cj.plan_edit(None, 4)
        self.assertEqual(status, "created")
        self.assertEqual(tomllib.loads(new), {"build": {"jobs": 4}})

    def test_appends_a_build_table_and_keeps_every_byte(self):
        old = '# my cargo config\n[net]\ngit-fetch-with-cli = true  # keep me\n'
        new, status = cj.plan_edit(old, 4)
        self.assertEqual(status, "set")
        self.assertTrue(new.startswith(old))
        self.assertEqual(tomllib.loads(new),
                         {"net": {"git-fetch-with-cli": True}, "build": {"jobs": 4}})

    def test_appends_after_a_file_without_trailing_newline(self):
        new, _ = cj.plan_edit('[net]\noffline = true', 4)
        self.assertEqual(tomllib.loads(new), {"net": {"offline": True}, "build": {"jobs": 4}})

    def test_inserts_under_an_existing_build_table(self):
        old = ('[build]  # mine\ntarget-dir = "/tmp/t"\n\n'
               '[target.x86_64-unknown-linux-gnu]\nlinker = "clang"\n')
        new, status = cj.plan_edit(old, 4)
        self.assertEqual(status, "set")
        self.assertEqual(new, old.replace("[build]  # mine\n",
                                          "[build]  # mine\n" + cj.managed_line(4) + "\n"))
        self.assertEqual(tomllib.loads(new)["build"], {"target-dir": "/tmp/t", "jobs": 4})

    def test_keeps_the_accounts_own_jobs(self):
        old = "[build]\njobs = 8\n"
        new, status = cj.plan_edit(old, 4)
        self.assertIsNone(new)
        self.assertIn("kept", status)
        self.assertIn("8", status)

    def test_keeps_an_own_dotted_jobs(self):
        new, status = cj.plan_edit("build.jobs = 2\n", 4)
        self.assertIsNone(new)
        self.assertIn("kept", status)

    def test_updates_its_own_line_and_nothing_else(self):
        old = "[env]\nA = \"1\"\n\n[build]\n" + cj.managed_line(16) + "\nrustflags = []\n"
        new, status = cj.plan_edit(old, 4)
        self.assertEqual(status, "set")
        self.assertEqual(new, old.replace(cj.managed_line(16), cj.managed_line(4)))

    def test_its_own_line_at_the_policy_value_is_unchanged(self):
        new, status = cj.plan_edit("[build]\n" + cj.managed_line(4) + "\n", 4)
        self.assertIsNone(new)
        self.assertEqual(status, "unchanged")

    def test_refuses_what_it_cannot_merge_safely(self):
        for old in ('[build\njobs = 1\n',                       # invalid TOML
                    'build = { target-dir = "t" }\n',           # inline table
                    'build.target-dir = "t"\n',                 # dotted keys, no header
                    '[env]\nX = """\n[build]\n"""\n',          # header inside a string
                    '[[build]]\nx = 1\n',                       # array of tables
                    '[env]\n' + cj.managed_line(4) + '\n'):     # managed line outside [build]
            new, status = cj.plan_edit(old, 4)
            self.assertIsNone(new, old)
            self.assertTrue(status.startswith("refused"), (old, status))


class TestEnsure(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def _with_cargo(self):
        b = self.home / ".cargo" / "bin"
        b.mkdir(parents=True)
        (b / "cargo").write_text("#!/bin/sh\n")
        (b / "cargo").chmod(0o755)

    def _ensure(self, **kw):
        kw.setdefault("cpu_count", 16)
        kw.setdefault("which", lambda name: None)
        kw.setdefault("shared_bin", str(self.home / "no-shared-rust"))
        return cj.ensure_cargo_jobs(home=self.home, **kw)

    def test_no_cargo_is_a_skip_and_writes_nothing(self):
        line = self._ensure()
        self.assertIn("skipped", line)
        self.assertFalse((self.home / ".cargo").exists())

    def test_the_shared_rust_bin_counts_as_cargo(self):
        shared = self.home / "opt-rust-bin"
        shared.mkdir()
        (shared / "cargo").write_text("#!/bin/sh\n")
        self._ensure(shared_bin=str(shared))
        cfg = self.home / ".cargo" / "config.toml"
        self.assertEqual(tomllib.loads(cfg.read_text()), {"build": {"jobs": 4}})

    def test_writes_then_is_idempotent(self):
        self._with_cargo()
        first = self._ensure()
        cfg = self.home / ".cargo" / "config.toml"
        self.assertIn("created", first)
        before = cfg.read_bytes()
        second = self._ensure()
        self.assertIn("unchanged", second)
        self.assertEqual(cfg.read_bytes(), before)
        self.assertEqual(list(cfg.parent.glob(".*tmp*")), [])

    def test_preserves_the_file_mode(self):
        self._with_cargo()
        cfg = self.home / ".cargo" / "config.toml"
        cfg.write_text("[net]\noffline = true\n")
        cfg.chmod(0o600)
        self._ensure()
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
        self.assertEqual(tomllib.loads(cfg.read_text())["build"], {"jobs": 4})

    def test_edits_the_legacy_config_cargo_reads(self):
        self._with_cargo()
        legacy = self.home / ".cargo" / "config"
        legacy.write_text("[net]\noffline = true\n")
        self._ensure()
        self.assertEqual(tomllib.loads(legacy.read_text())["build"], {"jobs": 4})
        self.assertFalse((self.home / ".cargo" / "config.toml").exists())

    def test_a_symlinked_config_is_left_alone(self):
        self._with_cargo()
        real = self.home / "dotfiles.toml"
        real.write_text("[net]\noffline = true\n")
        (self.home / ".cargo" / "config.toml").symlink_to(real)
        line = self._ensure()
        self.assertIn("WARN", line)
        self.assertEqual(real.read_text(), "[net]\noffline = true\n")

    def test_the_accounts_own_value_and_a_repo_config_stay_byte_identical(self):
        self._with_cargo()
        cfg = self.home / ".cargo" / "config.toml"
        cfg.write_text("[build]\njobs = 6\n")
        repo_cfg = self.home / "devel" / "r" / ".cargo" / "config.toml"
        repo_cfg.parent.mkdir(parents=True)
        repo_cfg.write_text("[build]\njobs = 3\n")
        line = self._ensure()
        self.assertIn("kept", line)
        self.assertEqual(cfg.read_text(), "[build]\njobs = 6\n")
        self.assertEqual(repo_cfg.read_text(), "[build]\njobs = 3\n")

    def test_an_unwritable_home_never_raises(self):
        not_a_dir = self.home / "file"
        not_a_dir.write_text("")
        line = cj.ensure_cargo_jobs(home=not_a_dir, cpu_count=16,
                                    which=lambda name: "/usr/bin/cargo")
        self.assertIn("WARN", line)


class TestInstallStep(unittest.TestCase):
    def test_inert_under_pytest_with_the_real_home(self):
        calls = []
        with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x"}), \
                mock.patch.object(cj, "ensure_cargo_jobs",
                                  side_effect=lambda *a, **k: calls.append(k) or "x"):
            cj.install_step()
        self.assertEqual(calls, [])

    def test_runs_with_an_explicit_home(self):
        calls = []
        with mock.patch.object(cj, "ensure_cargo_jobs",
                               side_effect=lambda **k: calls.append(k) or "set"):
            cj.install_step(home="/h")
        self.assertEqual(calls, [{"home": "/h"}])

    def test_cmd_install_runs_it(self):
        import inspect
        import airuleset
        self.assertIn('__import__("cli_cargo_jobs").install_step()',
                      inspect.getsource(airuleset.cmd_install))


def _find_cargo():
    """(cargo path, RUSTUP_HOME) on this box, read before any HOME override."""
    real_home = os.path.expanduser("~")
    cands = [("/opt/rust/cargo/bin/cargo", "/opt/rust/rustup"),
             (os.path.join(real_home, ".cargo/bin/cargo"),
              os.environ.get("RUSTUP_HOME") or os.path.join(real_home, ".rustup"))]
    which = shutil.which("cargo")
    if which:
        cands.append((which, os.environ.get("RUSTUP_HOME")
                      or os.path.join(real_home, ".rustup")))
    for cargo, rustup in cands:
        if os.access(cargo, os.X_OK) and os.path.isdir(rustup):
            return cargo, rustup
    return None, None


class RealCargo(unittest.TestCase):
    """The real cargo, in `bash --noprofile --norc -c` (no profile, no rc: the
    shape of a non-interactive Claude Bash call), reads the managed file."""

    def setUp(self):
        self.cargo, self.rustup = _find_cargo()
        if not self.cargo:
            self.fail("no cargo on this box: RealCargo is box-bound "
                      "(deselected in CI via .github/box-bound-tests.txt)")
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        b = self.home / ".cargo" / "bin"
        b.mkdir(parents=True)
        (b / "cargo").symlink_to(self.cargo)

    def _jobs(self, cwd):
        env = {"HOME": str(self.home), "RUSTUP_HOME": self.rustup,
               "RUSTC_BOOTSTRAP": "1",
               "PATH": os.path.dirname(self.cargo) + ":/usr/bin:/bin"}
        r = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c",
             "cargo -Zunstable-options config get build.jobs"],
            cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_a_non_login_shell_honours_the_managed_cap(self):
        cj.ensure_cargo_jobs(home=self.home, cpu_count=16, which=lambda n: None)
        proj = self.home / "devel" / "p"
        proj.mkdir(parents=True)
        self.assertEqual(self._jobs(proj), "build.jobs = 4")
        self.assertEqual(self._jobs("/"), "build.jobs = 4")   # outside the home too

    def test_a_repos_own_jobs_wins_and_is_never_touched(self):
        cj.ensure_cargo_jobs(home=self.home, cpu_count=16, which=lambda n: None)
        repo_cfg = self.home / "devel" / "r" / ".cargo" / "config.toml"
        repo_cfg.parent.mkdir(parents=True)
        repo_cfg.write_text("[build]\njobs = 3\n")
        self.assertEqual(self._jobs(repo_cfg.parent.parent), "build.jobs = 3")
        self.assertEqual(repo_cfg.read_text(), "[build]\njobs = 3\n")

    def test_an_accounts_own_jobs_is_kept(self):
        (self.home / ".cargo" / "config.toml").write_text("[build]\njobs = 6\n")
        cj.ensure_cargo_jobs(home=self.home, cpu_count=16, which=lambda n: None)
        self.assertEqual(self._jobs(self.home), "build.jobs = 6")


if __name__ == "__main__":
    unittest.main()
