"""#1318 review (15.11.2026): the size cap against a target REAL Cargo wrote.

Synthetic fixtures cannot prove that a capped target still builds. This builds a
crate with a build script (``include!`` of a generated file, the shape that broke when
a fingerprint survived a half-deleted ``out/``) in two feature sets, ages every file,
caps the target hard, then builds again with Cargo. Needs cargo (``/opt/rust`` on the
fleet boxes); it FAILS without it, so the CI container lists it as box-bound."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from watchdog import disk_guard_target_cap as tc

DAY = 86400
CARGO_TOML = """[package]
name = "capdemo"
version = "0.1.0"
edition = "2021"

[features]
extra = []
"""
BUILD_RS = """use std::{env, fs, path::Path};
fn main() {
    let extra = env::var("CARGO_FEATURE_EXTRA").is_ok();
    let out = Path::new(&env::var("OUT_DIR").unwrap()).join("gen.rs");
    fs::write(out, format!("pub const EXTRA: bool = {};", extra)).unwrap();
}
"""
MAIN_RS = """include!(concat!(env!("OUT_DIR"), "/gen.rs"));
fn main() { println!("extra={}", EXTRA); }
"""


def _cargo_env():
    cargo = shutil.which("cargo")
    env = dict(os.environ)
    if cargo is None and os.path.exists("/opt/rust/cargo/bin/cargo"):
        cargo = "/opt/rust/cargo/bin/cargo"
        env.setdefault("RUSTUP_HOME", "/opt/rust/rustup")
    if cargo is not None:      # rustup's cargo proxy finds rustc on PATH
        env["PATH"] = os.path.dirname(cargo) + os.pathsep + env.get("PATH", "")
    return cargo, env


class TestRealCargo(unittest.TestCase):
    def setUp(self):
        self.cargo, self.env = _cargo_env()
        if self.cargo is None:
            self.fail("cargo not found (PATH or /opt/rust): this test needs a real Cargo")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "capdemo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "Cargo.toml").write_text(CARGO_TOML)
        (self.root / "build.rs").write_text(BUILD_RS)
        (self.root / "src" / "main.rs").write_text(MAIN_RS)
        self.env["CARGO_HOME"] = str(Path(tmp.name) / "cargo-home")
        self.target = self.root / "target"

    def cargo_run(self, *args):
        r = subprocess.run([self.cargo, "run", "--offline", "-q", *args], cwd=self.root,
                           env=self.env, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def age_everything(self, days):
        t = time.time() - days * DAY
        for root, dirs, files in os.walk(self.target, topdown=False):
            for name in files + dirs:
                os.utime(os.path.join(root, name), (t, t), follow_symlinks=False)

    def test_a_capped_target_still_builds_both_feature_sets(self):
        self.assertEqual(self.cargo_run(), "extra=false")
        self.assertEqual(self.cargo_run("--features", "extra"), "extra=true")
        self.age_everything(3)
        units = tc.collect(str(self.target))
        self.assertGreaterEqual(len([u for u in units if not u.incremental]), 4)
        notes = []
        row = tc.enforce_target(str(self.target), 1, time.time(), {},
                                lambda *a: notes.append(a))
        self.assertTrue(any(a[0] == "CAP-REMOVE" for a in notes), notes)
        self.assertIn(row["state"], ("over", "reduced"))
        self.assertEqual(self.cargo_run(), "extra=false")
        self.assertEqual(self.cargo_run("--features", "extra"), "extra=true")

    def test_a_cut_after_the_fingerprint_leaves_a_unit_cargo_rebuilds(self):
        self.assertEqual(self.cargo_run(), "extra=false")
        self.assertEqual(self.cargo_run("--features", "extra"), "extra=true")
        self.age_everything(3)
        real = tc.cs._remove

        def killed_after_fingerprint(path):
            if "fingerprint" not in path:
                raise OSError(4, "killed mid-unit")
            return real(path)
        with mock.patch.object(tc.cs, "_remove", side_effect=killed_after_fingerprint):
            tc.enforce_target(str(self.target), 1, time.time(), {}, lambda *a: None)
        self.assertEqual(self.cargo_run(), "extra=false")
        self.assertEqual(self.cargo_run("--features", "extra"), "extra=true")


if __name__ == "__main__":
    unittest.main()
