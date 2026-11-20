"""#1384 — the workstation profile of the #775 resource guardrails (dev1).

dev1 runs ~20 project accounts plus the owner's own sessions, and none of its
`user-<uid>.slice` had a ceiling: earlyoom killed processes box-wide instead
(camera-box's release measurement, its rig-lease-server). The fix reuses the
SAME `cli_resource_guards` machinery with a second profile:

  * the `user-.slice` template sets `MemoryHigh=20%` and an explicit
    `MemoryMax=infinity` — a throttle, never a cgroup kill (owner constraint:
    an owner session or a running Claude must never be cgroup-killed);
  * root stays exempt, ssh + tailscaled keep the OOM protection;
  * nothing subdev-only rides along (no swappiness file, no journald cap, no
    quota, no rsyslog, no Playwright install, no swap resize);
  * the push reaches dev1 the #1223 way: `newlevel` + `sudo -n bash -c`.

Execution tests run the rendered live-apply + read-back blocks against a PATH
`systemctl` stub (no real systemd, no real ssh, nothing on a real box).
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_fleet                 # noqa: E402
import cli_resource_guards as g  # noqa: E402


def _memtotal_bytes():
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    raise AssertionError("no MemTotal in /proc/meminfo")


class TestWorkstationDropIns(unittest.TestCase):
    def test_template_throttles_and_never_kills(self):
        body = g.render_workstation_guard_dropin()
        keys = dict(ln.split("=", 1) for ln in body.splitlines()
                    if "=" in ln and not ln.startswith("#"))
        self.assertEqual(keys, {"MemoryHigh": "%d%%" % g.WORKSTATION_MEMORY_HIGH_PCT,
                                "MemoryMax": "infinity"})
        self.assertEqual(g.WORKSTATION_MEMORY_HIGH_PCT, 20)
        self.assertIn("[Slice]", body)

    def test_root_exempt_matches_the_template_keys(self):
        body = g.render_workstation_root_exempt_dropin()
        keys = dict(ln.split("=", 1) for ln in body.splitlines()
                    if "=" in ln and not ln.startswith("#"))
        self.assertEqual(keys, {"MemoryHigh": "infinity", "MemoryMax": "infinity"})

    def test_files_are_the_template_root_and_oom_only(self):
        files = dict(g.workstation_guard_files())
        self.assertEqual(set(files), {g.GUARD_DROPIN_PATH, g.ROOT_EXEMPT_PATH,
                                      g.SSH_OOM_PATH, g.TAILSCALED_OOM_PATH})
        self.assertEqual(files[g.SSH_OOM_PATH], g.render_service_oom_dropin())
        self.assertEqual(files[g.TAILSCALED_OOM_PATH], g.render_service_oom_dropin())
        self.assertEqual(files[g.GUARD_DROPIN_PATH], g.render_workstation_guard_dropin())


class TestWorkstationApplyScript(unittest.TestCase):
    def setUp(self):
        self.s = g.build_workstation_apply_script()

    def test_valid_bash_and_strict(self):
        self.assertTrue(self.s.startswith("set -euo pipefail\n"))
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(self.s)
        self.addCleanup(os.unlink, f.name)
        r = subprocess.run(["bash", "-n", f.name], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_installs_every_file_atomically_then_reloads(self):
        for path, _ in g.workstation_guard_files():
            self.assertIn("_install %s <<'%s'" % (path, g._HEREDOC_MARK), self.s)
        self.assertIn('mv -f "$tmp" "$dest"', self.s)
        self.assertLess(self.s.index("systemctl daemon-reload"),
                        self.s.index("set-property"))

    def test_nothing_subdev_only_rides_along(self):
        for banned in ("MemoryMax=%d%%" % g.MEMORY_MAX_PCT, "CPUQuota", "TasksMax",
                       g.SYSCTL_VM_PATH, "sysctl --system", g.JOURNALD_CAP_PATH,
                       "journalctl --rotate", g.RSYSLOG_QUIET_PATH, "setquota",
                       "playwright", "swapoff", "mkswap", "fallocate", "/etc/fstab",
                       "50-fairness"):
            self.assertNotIn(banned, self.s, banned)

    def test_swap_and_swappiness_are_reported(self):
        self.assertIn("/proc/sys/vm/swappiness", self.s)
        self.assertIn("SwapTotal", self.s)


class _Stubbed(unittest.TestCase):
    """Run a rendered block with a PATH `systemctl` stub. `slices` maps a slice
    name to its (MemoryHigh, MemoryMax) as `systemctl show` would print them."""

    def _run(self, block, slices):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", d]))
        log = os.path.join(d, "calls.log")
        lines = ["#!/bin/bash", 'echo "$*" >> %s' % log,
                 'if [ "$1" = list-units ]; then']
        lines += ['  echo "%s loaded active active Slice"' % s for s in slices]
        lines += ["  exit 0", "fi",
                  'if [ "$1" = show ]; then',
                  '  prop="$3"; unit="$5"', '  case "$unit:$prop" in']
        for s, (high, mx) in slices.items():
            lines.append('    %s:MemoryHigh) echo "%s" ;;' % (s, high))
            lines.append('    %s:MemoryMax) echo "%s" ;;' % (s, mx))
        lines += ['    *) echo "" ;;', "  esac", "  exit 0", "fi", "exit 0"]
        stub = os.path.join(d, "systemctl")
        with open(stub, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(stub, 0o755)
        script = "set -euo pipefail\n" + block
        env = dict(os.environ, PATH=d + ":" + os.environ["PATH"])
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env=env, timeout=30)
        calls = open(log).read() if os.path.exists(log) else ""
        return r, calls


class TestWorkstationLiveApplyAndReadBack(_Stubbed):
    def _block(self):
        return (g._render_workstation_expect_block() + "\n"
                + g._render_workstation_live_apply_block() + "\n"
                + g._render_workstation_readback_block())

    def _high(self):
        return str(_memtotal_bytes() * g.WORKSTATION_MEMORY_HIGH_PCT // 100)

    def test_applied_slices_verify(self):
        r, calls = self._run(self._block(), {
            "user-0.slice": ("infinity", "infinity"),
            "user-1000.slice": (self._high(), "infinity"),
            "user-1002.slice": (self._high(), "infinity")})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("set-property --runtime user-1002.slice MemoryHigh=20%", calls)
        self.assertIn("set-property --runtime user-1000.slice MemoryHigh=20%", calls)
        self.assertNotIn("user-0.slice MemoryHigh", calls)
        self.assertNotIn("MemoryMax=", calls)   # the live apply never sets a kill ceiling

    def test_a_finite_memorymax_fails_loud(self):
        r, _ = self._run(self._block(), {
            "user-1002.slice": (self._high(), str(_memtotal_bytes() * 40 // 100))})
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("MemoryMax", r.stderr)

    def test_an_unlimited_memoryhigh_fails_loud(self):
        r, _ = self._run(self._block(), {"user-1002.slice": ("infinity", "infinity")})
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)

    def test_a_memoryhigh_out_of_band_fails_loud(self):
        r, _ = self._run(self._block(), {
            "user-1002.slice": (str(_memtotal_bytes() * 90 // 100), "infinity")})
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)


class TestWorkstationHosts(unittest.TestCase):
    def test_dev1_only_via_the_1223_sudo_path(self):
        hosts = cli_fleet.WORKSTATION_GUARD_HOSTS
        self.assertEqual([h["name"] for h in hosts], ["dev1"])
        h = hosts[0]
        dg = {e["name"]: e for e in cli_fleet.DISK_GUARD_ROOT_HOSTS}["dev1"]
        self.assertEqual(h["host"], dg["host"])
        self.assertTrue(h["host"].startswith("100."))   # tailscale, never a LAN ip
        self.assertEqual(h["admin_user"], "newlevel")
        self.assertIs(h["sudo"], True)
        self.assertEqual(h["identity"], dg["identity"])

    def test_disjoint_from_the_stream_guard_hosts(self):
        ws = {h["host"] for h in cli_fleet.WORKSTATION_GUARD_HOSTS}
        st = {h["host"] for h in cli_fleet.SHARED_STREAM_GUARD_HOSTS}
        self.assertEqual(ws & st, set())


class _Rec:
    def __init__(self, rc=0):
        self.calls = []
        self.rc = rc

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc = self.rc

        class R:
            returncode = rc
            stdout = "  resource-guards: applied + verified"
            stderr = ""
        return R()


class TestProvisionWorkstation(unittest.TestCase):
    HOST = {"name": "dev1", "host": "10.0.0.7", "admin_user": "newlevel",
            "sudo": True, "identity": "~/.secrets/k"}

    def test_reaches_the_account_with_sudo_n_and_the_workstation_script(self):
        rec = _Rec()
        self.assertEqual(g.provision_workstation_guards(hosts=[self.HOST], run=rec), [])
        self.assertEqual(len(rec.calls), 1)
        argv = rec.calls[0]
        self.assertIn("newlevel@10.0.0.7", argv)
        remote = argv[-1]
        self.assertTrue(remote.startswith("sudo -n bash -c "), remote[:40])
        self.assertIn("MemoryMax=infinity", remote)
        self.assertNotIn("MemoryMax=%d%%" % g.MEMORY_MAX_PCT, remote)

    def test_failure_is_loud_and_non_fatal(self):
        rec = _Rec(rc=4)
        self.assertEqual(g.provision_workstation_guards(hosts=[self.HOST], run=rec),
                         [("dev1", "rc=4")])

    def test_defaults_to_the_fleet_list(self):
        rec = _Rec()
        g.provision_workstation_guards(run=rec)
        self.assertEqual([c[-2] for c in rec.calls],
                         ["%s@%s" % (h["admin_user"], h["host"])
                          for h in cli_fleet.WORKSTATION_GUARD_HOSTS])

    def test_inert_with_the_real_runner_under_pytest(self):
        with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x"}), \
                mock.patch("subprocess.run", side_effect=AssertionError("real ssh")):
            self.assertEqual(g.provision_workstation_guards(), [])

    def test_stream_hosts_keep_the_root_shape(self):
        rec = _Rec()
        g.provision_shared_stream_guards(
            hosts=[{"name": "subdev", "host": "10.0.0.9", "admin_user": "root",
                    "identity": "~/.secrets/k"}], run=rec)
        remote = rec.calls[0][-1]
        self.assertTrue(remote.startswith("bash -c "), remote[:40])
        self.assertIn("MemoryMax=%d%%" % g.MEMORY_MAX_PCT, remote)


class TestPushWiring(unittest.TestCase):
    def test_cmd_push_applies_the_workstation_guards(self):
        import inspect
        import cli_remote
        src = inspect.getsource(cli_remote.cmd_push)
        self.assertIn("provision_workstation_guards()", src)
        self.assertLess(src.index("provision_shared_stream_guards()"),
                        src.index("provision_workstation_guards()"))


if __name__ == "__main__":
    unittest.main()
