"""#1328: devbridge's trunk tools system-wide (wasm-bindgen-cli + a pinned
binaryen wasm-opt), the weekly /opt/rust stable update, and the #1325 virsh
sudo lines. Rendered bash is EXECUTED under a fake root with PATH stubs."""
import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_hardening as hardening  # noqa: E402
import cli_project_toolchain as tc  # noqa: E402

FAKE_WASM_OPT = '#!/bin/sh\necho "wasm-opt version 131 (version_131)"\n'


def _bash(script, env, cwd):
    return subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n" + script],
                          text=True, capture_output=True, env=env, cwd=cwd, timeout=60)


class Grammar(unittest.TestCase):

    def test_parse(self):
        self.assertEqual(tc.parse_tool("binaryen:version_131")[0], "binaryen")
        for bad in ("binaryen:131", "binaryen:version_x", "binaryen:version_1;id"):
            with self.assertRaises(ValueError):
                tc.parse_tool(bad)

    def test_unpinned_version_is_refused(self):
        errs = tc.validate_tools({"tools": ["binaryen:version_999"]})
        self.assertTrue([e for e in errs if "no pinned digest" in e], errs)

    def test_one_version_per_account_and_per_host(self):
        with mock.patch.dict(tc.BINARYEN_SHA256, {"version_130": {"x86_64": "0" * 64}}):
            self.assertTrue(tc.validate_tools(
                {"tools": ["binaryen:version_130", "binaryen:version_131"]}))
            other = {"x": {"host": "dev1", "tools": ["binaryen:version_130"]}}
            errs = tc.validate_tools({"host": "dev1", "tools": ["binaryen:version_131"]},
                                     "me", other)
            self.assertTrue([e for e in errs if "conflicts with x" in e], errs)
            self.assertEqual(tc.validate_tools(
                {"host": "dev2", "tools": ["binaryen:version_131"]}, "me", other), [])

    def test_plan(self):
        self.assertEqual(tc.plan({"tools": ["binaryen:version_131"]})["binaryen"],
                         ["version_131"])
        self.assertEqual(tc.plan({"tools": []})["binaryen"], [])
        self.assertNotIn("wasm-opt", tc.render_system_step({"tools": []}))


class BinaryenInstallRuns(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tc-1328-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = FAKE_WASM_OPT.encode()
            info = tarfile.TarInfo("binaryen-version_131/bin/wasm-opt")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
        self.tgz = self.tmp / "release.tgz"
        self.tgz.write_bytes(buf.getvalue())
        self.digest = hashlib.sha256(buf.getvalue()).hexdigest()
        stub = self.tmp / "bin"
        stub.mkdir()
        (stub / "curl").write_text(
            '#!/usr/bin/env bash\necho "curl $*" >> "$LOG"\n'
            'out=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
            'cp "$TGZ" "$out"\n')
        (stub / "curl").chmod(0o755)
        self.env = {"PATH": "%s:/usr/bin:/bin" % stub, "LOG": str(self.tmp / "log"),
                    "TGZ": str(self.tgz), "HOME": str(self.tmp), "TMPDIR": str(self.tmp)}
        self.root, self.bin = self.tmp / "opt/binaryen", self.tmp / "usr/local/bin"
        self.bin.mkdir(parents=True)

    def run_step(self, digest):
        with mock.patch.dict(tc.BINARYEN_SHA256, {"version_131": {"x86_64": digest}}):
            script = tc._binaryen_lines(["version_131"], str(self.root), str(self.bin))
        return _bash(script, self.env, str(self.tmp))

    def test_installs_verifies_links_then_is_idempotent(self):
        r = self.run_step(self.digest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        link = self.bin / "wasm-opt"
        self.assertEqual(os.readlink(link), str(self.root / "version_131/bin/wasm-opt"))
        self.assertIn("(version_131)", r.stdout)
        self.assertIn("binaryen-version_131-x86_64-linux.tar.gz", (self.tmp / "log").read_text())
        (self.tmp / "log").write_text("")
        r = self.run_step(self.digest)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / "log").read_text(), "")      # no download again

    def test_a_wrong_digest_installs_nothing(self):
        r = self.run_step("1" * 64)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.bin / "wasm-opt").exists())
        self.assertFalse((self.root / "version_131/bin/wasm-opt").exists())


class RustStableUpdate(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tc-1328-rust-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        self.root = self.tmp / "opt/rust"
        (self.root / "cargo/bin").mkdir(parents=True)
        (self.root / "cargo/bin/rustup").write_text(
            '#!/usr/bin/env bash\necho "rustup $*" >> "$LOG"\n'
            '[ "$1" = run ] && echo "rustc 1.1$(wc -l < "$LOG").0"\nexit 0\n')
        (self.root / "cargo/bin/rustup").chmod(0o777)
        self.proc = self.tmp / "proc"
        self.proc.mkdir()
        self.lock = self.tmp / "rust.lock"
        files = tc.render_rust_update_files(rust_root=str(self.root), lock=str(self.lock),
                                            sbin_dir=str(self.tmp))
        self.script = self.tmp / "update"
        self.script.write_text(files["script"])
        self.script.chmod(0o755)
        self.files = files
        self.env = {"PATH": "/usr/bin:/bin", "LOG": str(self.tmp / "log"),
                    "AIRULESET_RUST_PROC": str(self.proc), "AIRULESET_RUST_WAIT_STEP": "1",
                    "AIRULESET_RUST_MAX_WAIT": "2"}

    def run_update(self):
        return subprocess.run([str(self.script)], text=True, capture_output=True,
                              env=self.env, timeout=60)

    def fake_process(self, pid, exe):
        d = self.proc / str(pid)
        d.mkdir()
        os.symlink(exe, d / "exe")

    def test_updates_stable_and_makes_the_tree_read_only(self):
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        log = (self.tmp / "log").read_text()
        self.assertIn("rustup update stable --no-self-update", log)
        self.assertIn("stable rustc", r.stdout)
        mode = (self.root / "cargo/bin/rustup").stat().st_mode & 0o777
        self.assertEqual(mode & 0o022, 0)

    def test_a_running_stable_build_defers_then_skips(self):
        self.fake_process(4242, "%s/rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/rustc"
                          % self.root)
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("a stable build is running (4242", r.stdout)
        self.assertIn("SKIPPED", r.stderr)
        self.assertNotIn("update stable", (self.tmp / "log").read_text()
                         if (self.tmp / "log").exists() else "")

    def test_a_pinned_toolchain_build_does_not_block(self):
        self.fake_process(77, "%s/rustup/toolchains/1.98.1-x86_64-unknown-linux-gnu/bin/rustc"
                          % self.root)
        self.fake_process(78, "%s/cargo/bin/rust-analyzer" % self.root)
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("update stable", (self.tmp / "log").read_text())

    def test_a_held_lock_defers_then_skips(self):
        holder = subprocess.Popen(["flock", str(self.lock), "sleep", "30"])
        self.addCleanup(holder.kill)
        for _ in range(50):
            probe = subprocess.run(["flock", "-n", str(self.lock), "true"])
            if probe.returncode != 0:
                break
            subprocess.run(["sleep", "0.1"])
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("holds", r.stdout)
        self.assertIn("SKIPPED", r.stderr)

    def test_the_lock_is_root_only(self):
        """Review A: /run/lock is world-writable; a user could pre-create the
        lock or hold flock on a 0644 file and stall every bootstrap."""
        self.assertFalse(tc.RUST_LOCK.startswith(("/run/lock/", "/tmp/", "/var/tmp/")))
        self.assertEqual(os.path.dirname(tc.RUST_LOCK), "/run")
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.lock.stat().st_mode & 0o777, 0o600)
        step = tc.render_system_step({"tools": []})
        self.assertIn("umask 077\nexec 8>%s\numask 022\n" % tc.RUST_LOCK, step)

    def test_a_replaced_binary_still_counts_as_a_build(self):
        """Review B: /proc shows `<path> (deleted)` once rustup replaced it."""
        self.fake_process(4243, "%s/rustup/toolchains/stable-x86_64-unknown-linux-gnu/bin/"
                          "cargo (deleted)" % self.root)
        r = self.run_update()
        self.assertIn("a stable build is running (4243", r.stdout)
        self.assertIn("SKIPPED", r.stderr)

    def test_the_build_check_is_repeated_under_the_lock(self):
        script = self.files["script"]
        locked = script.index("elif flock -n 9; then")
        self.assertLess(locked, script.index("busy=$(building) || break"))
        self.assertIn("flock -u 9", script)

    def test_the_timer_part_runs_after_playwright(self):
        """Review B: a systemctl failure must not cost the browsers part."""
        step = tc.render_system_step({"tools": []})
        self.assertLess(step.index("# shared Playwright browsers"),
                        step.index("# weekly /opt/rust stable update"))

    def test_units(self):
        timer, service = self.files["timer"], self.files["service"]
        self.assertIn("OnCalendar=Mon *-*-* 04:30:00", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertIn("ExecStart=%s/%s" % (self.tmp, tc.RUST_UPDATE), service)
        self.assertIn("Type=oneshot", service)
        self.assertEqual(subprocess.run(["bash", "-n"], input=self.files["script"],
                                        text=True).returncode, 0)

    def test_step12_takes_the_same_lock_before_rustup(self):
        step = tc.render_system_step({"tools": []})
        self.assertLess(step.index("flock -w 1800 8"), step.index("toolchain install"))
        self.assertIn("exec 8>%s" % tc.RUST_LOCK, step)
        self.assertIn('exec 9>"%s"' % tc.RUST_LOCK, tc.render_rust_update_files()["script"])
        self.assertIn("systemctl enable --now %s.timer" % tc.RUST_UPDATE, step)


class Devbridge(unittest.TestCase):

    def test_declares_the_trunk_tools(self):
        tools = bootstrap.account_spec("devbridge")["tools"]
        self.assertIn("cargo:wasm-bindgen-cli@0.2.117", tools)
        self.assertIn("binaryen:version_131", tools)

    def test_exact_virsh_sudo_lines(self):
        spec = bootstrap.account_spec("devbridge")
        self.assertIs(spec["sudo"], True)
        self.assertEqual(spec["sudo_commands"], [
            "/usr/bin/virsh start devbridge-win",
            "/usr/bin/virsh shutdown devbridge-win",
            "/usr/bin/virsh domstate devbridge-win"])
        self.assertNotIn("\n", spec["sudo_reason"])
        self.assertEqual([hardening.sudo_command_problem(c) for c in spec["sudo_commands"]],
                         [None, None, None])
        self.assertEqual(bootstrap.validate_account(
            "devbridge", bootstrap.SERVICE_ACCOUNTS["devbridge"]), [])

    def test_render_carries_the_rule(self):
        script = bootstrap.render_root_bootstrap("devbridge")
        self.assertIn("NOPASSWD:NOEXEC: /usr/bin/virsh start devbridge-win, "
                      "/usr/bin/virsh shutdown devbridge-win, "
                      "/usr/bin/virsh domstate devbridge-win", script)


if __name__ == "__main__":
    unittest.main()
