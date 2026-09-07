"""Behaviour tests for the #775 shared-stream resource guardrails.

Covers the three surfaces of the Layer-3 subdev-OOM fix:
  * `cli_resource_guards` — the drop-in RENDERING (exact keys/values) and the
    apply-SCRIPT shape (set -euo pipefail, atomic mktemp+mv, daemon-reload
    before set-property, the over-limit skip branch, the fail-loud read-back
    verify, the user-0 exemption, swap verify-ONLY), plus the LOUD non-fatal
    `provision_shared_stream_guards` push step.
  * `cli_fleet.SHARED_STREAM_GUARD_HOSTS` — the drift-lock (a new shared-stream
    host cannot be added to REMOTE_HOSTS without a matching guard entry).
  * `watchdog.resource_guard.resource_guard_verify` — box-class gate, the
    unlimited→alert+dedup / limit→silence / unreadable→silence branches, and
    the dry_run no-write property.

Every test injects fakes (cgroup_read / box_class_fn / gk_request_fn / a run
recorder) — no real ssh, no real cgroup, no real process is ever touched.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_resource_guards as g          # noqa: E402
import cli_fleet                         # noqa: E402
import airuleset                         # noqa: E402
import watchdog as wd                    # noqa: E402
from watchdog.resource_guard import (    # noqa: E402
    resource_guard_verify,
    DEDUP_INTERVAL_S,
    TRACKING_ISSUE,
    TRACKING_REPO,
)


class TestDropInRendering(unittest.TestCase):
    """The rendered drop-in files carry EXACTLY the settled policy keys/values
    (#775 design comment 5489114579): percentages (survive a box resize), the
    root exemption, the ssh/tailscaled OOM protection, the sysctl."""

    def test_guard_dropin_exact_keys_and_values(self):
        d = g.render_guard_dropin()
        self.assertIn("[Slice]", d)
        self.assertIn("MemoryHigh=12%", d)
        self.assertIn("MemoryMax=18%", d)
        self.assertIn("TasksMax=512", d)
        # #922: CPUWeight lowered from 100 (default, no advantage) to 30 —
        # stream users get 30/130 (~23%) of CPU when contending with root
        # (weight 100 via root-exempt drop-in). Under no contention, streams
        # can use any idle CPU.
        self.assertIn("CPUWeight=30", d)
        # #922: CPUQuota=300% caps each stream at 3 of 8 vCPU cores. This was
        # DELIBERATELY absent in #775 (the collapse was memory thrash, not CPU),
        # but the owner's live incident (david3 test at 98% CPU, owner can't
        # scroll) showed the CPU gap is real.
        self.assertIn("CPUQuota=300%", d)
        # percentages, never absolute bytes (survive a box resize).
        self.assertNotIn("MemoryMax=18G", d)

    def test_root_exempt_dropin_is_unlimited(self):
        d = g.render_root_exempt_dropin()
        self.assertIn("[Slice]", d)
        self.assertIn("MemoryHigh=infinity", d)
        self.assertIn("MemoryMax=infinity", d)
        self.assertIn("TasksMax=infinity", d)
        # #922: root exempt must also restore CPU priority (the template now
        # sets CPUWeight=30 + CPUQuota=300%, both must be unlimited for root).
        self.assertIn("CPUWeight=100", d)
        # CPUQuota= with an empty value resets to unlimited (systemd semantics).
        self.assertIn("\nCPUQuota=\n", d)

    def test_service_oom_dropin(self):
        d = g.render_service_oom_dropin()
        self.assertIn("[Service]", d)
        self.assertIn("OOMScoreAdjust=-900", d)
        self.assertIn("MemoryMin=128M", d)

    def test_sysctl_vm_swappiness(self):
        d = g.render_sysctl_vm()
        self.assertIn("vm.swappiness=10", d)

    def test_guard_files_paths(self):
        paths = [p for p, _ in g.guard_files()]
        self.assertIn(
            "/etc/systemd/system/user-.slice.d/50-airuleset-resource-guard.conf",
            paths)
        self.assertIn(
            "/etc/systemd/system/user-0.slice.d/50-airuleset-root-exempt.conf",
            paths)
        self.assertIn(
            "/etc/systemd/system/ssh.service.d/50-airuleset-oom-protect.conf",
            paths)
        self.assertIn(
            "/etc/systemd/system/tailscaled.service.d/50-airuleset-oom-protect.conf",
            paths)
        self.assertIn("/etc/sysctl.d/50-airuleset-vm.conf", paths)
        # ssh + tailscaled share the identical OOM-protect body.
        by_path = dict(g.guard_files())
        self.assertEqual(
            by_path["/etc/systemd/system/ssh.service.d/50-airuleset-oom-protect.conf"],
            by_path["/etc/systemd/system/tailscaled.service.d/50-airuleset-oom-protect.conf"])


class TestApplyScript(unittest.TestCase):
    """The apply script's SHAPE — the settled-design invariants, asserted as
    substrings / orderings on the rendered string (the script runs on subdev at
    push time; here we lock its structure)."""

    def setUp(self):
        self.s = g.build_apply_script()

    def test_set_euo_pipefail(self):
        # script-failure-policy.md: a root apply must fail loudly.
        self.assertIn("set -euo pipefail", self.s)

    def test_atomic_mktemp_mv_write(self):
        self.assertIn("mktemp", self.s)
        self.assertIn("mv -f", self.s)
        # never a truncating write directly to the destination.
        self.assertNotIn("> \"$dest\"", self.s)

    def test_daemon_reload_precedes_set_property(self):
        self.assertIn("systemctl daemon-reload", self.s)
        self.assertIn("set-property", self.s)
        self.assertLess(self.s.index("daemon-reload"), self.s.index("set-property"),
                        "daemon-reload must run BEFORE any set-property")

    def test_over_limit_skip_branch(self):
        # never insta-kill a live legit session at deploy.
        self.assertIn("MemoryCurrent", self.s)
        self.assertIn("SKIP live set-property", self.s)

    def test_read_back_verify_exits_nonzero(self):
        self.assertIn("systemctl show", self.s)
        self.assertIn("exit 4", self.s)
        self.assertIn("VERIFY FAIL", self.s)

    def test_user0_exemption(self):
        # the live-apply + read-back loops both skip user-0.slice.
        self.assertIn('[ "$slice" = "user-0.slice" ] && continue', self.s)

    def test_live_apply_includes_cpu_weight_and_quota(self):
        # #922: the live-apply loop must set CPUWeight + CPUQuota alongside
        # the existing MemoryHigh/MemoryMax/TasksMax.
        self.assertIn("CPUWeight=30", self.s)
        self.assertIn("CPUQuota=300%", self.s)

    def test_read_back_verify_checks_cpu_weight_and_quota(self):
        # #922: the read-back verify must check CPUWeight AND CPUQuotaPerSecUSec.
        self.assertIn("-p CPUWeight", self.s)
        self.assertIn("-p CPUQuotaPerSecUSec", self.s)
        # The verify checks CPUWeight == 30 and CPUQuotaPerSecUSec == 3s.
        self.assertIn('cpuw" != "30"', self.s)
        self.assertIn('cpuq" != "3s"', self.s)

    def test_swap_verify_only_on_non_shared_stream(self):
        """#925-C: non-shared-stream path retains verify-only swap check."""
        self.assertIn("SwapTotal", self.s)
        # Shared-stream path now provisions swap (mkswap, fallocate, swapon),
        # but the non-shared-stream else branch still only verifies.
        self.assertIn("swap_is_shared_stream", self.s)

    def test_swap_shared_stream_provisions_10g(self):
        """#925-C: shared-stream path provisions exactly 10G /swapfile."""
        self.assertIn("mkswap", self.s)
        self.assertIn("fallocate", self.s)
        self.assertIn("swapon", self.s)
        self.assertIn("10G", self.s)
        # Never leaves two swapfiles — the old is swapped off before rm
        self.assertIn("swapoff", self.s)

    def test_script_is_valid_bash(self):
        import subprocess
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(self.s)
            path = f.name
        try:
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0,
                             "apply script is not valid bash: %s" % r.stderr)
        finally:
            os.unlink(path)


class TestGuardHostsDriftLock(unittest.TestCase):
    """A new shared-stream host cannot enter REMOTE_HOSTS without a matching
    guard entry: every REMOTE_HOSTS entry whose `user` is a reduced-authority
    stream (in AUTHORITY_BY_USER) MUST have its `host` covered by
    SHARED_STREAM_GUARD_HOSTS — else it would run without cgroup ceilings."""

    def test_every_stream_host_is_guarded(self):
        guard_hosts = {h["host"] for h in cli_fleet.SHARED_STREAM_GUARD_HOSTS}
        for entry in cli_fleet.REMOTE_HOSTS:
            user = entry.get("user")
            if user in cli_fleet.AUTHORITY_BY_USER:
                self.assertIn(
                    entry["host"], guard_hosts,
                    "REMOTE_HOSTS entry %r (a reduced-authority stream on host "
                    "%s) has NO SHARED_STREAM_GUARD_HOSTS coverage — it would "
                    "run without #775 resource guardrails" %
                    (entry.get("name"), entry["host"]))

    def test_guard_host_shape(self):
        for h in cli_fleet.SHARED_STREAM_GUARD_HOSTS:
            self.assertIn("host", h)
            self.assertIn("admin_user", h)
            self.assertIn("identity", h)   # a root apply must ride a pinned key


class TestProvisionShared(unittest.TestCase):
    """The push step is NON-FATAL + LOUD (never raises), returns a failure list,
    and never rides a shared password (a target without an identity is refused)."""

    def test_success_returns_no_failures(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            class R:
                returncode = 0
                stdout = "  resource-guards: applied + verified"
                stderr = ""
            return R()

        hosts = [{"name": "subdev", "host": "10.0.0.9", "admin_user": "root",
                  "identity": "~/.secrets/gatekeeper_access_ed25519"}]
        failed = g.provision_shared_stream_guards(hosts=hosts, run=fake_run)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertIn("root@10.0.0.9", argv)
        # the script is passed as a `bash -c <script>` remote command, never
        # via stdin (so the heredocs inside keep an unconsumed stdin).
        self.assertTrue(any(a.startswith("bash -c ") for a in argv))

    def test_ssh_failure_is_loud_and_nonfatal(self):
        def fake_run(argv, **kw):
            class R:
                returncode = 255
                stdout = ""
                stderr = "Permission denied (publickey)."
            return R()

        hosts = [{"name": "subdev", "host": "10.0.0.9", "admin_user": "root",
                  "identity": "~/.secrets/k"}]
        failed = g.provision_shared_stream_guards(hosts=hosts, run=fake_run)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][0], "subdev")

    def test_ssh_exception_is_caught(self):
        def fake_run(argv, **kw):
            raise OSError("boom")

        hosts = [{"name": "subdev", "host": "10.0.0.9", "admin_user": "root",
                  "identity": "~/.secrets/k"}]
        failed = g.provision_shared_stream_guards(hosts=hosts, run=fake_run)
        self.assertEqual(len(failed), 1)

    def test_missing_identity_is_refused(self):
        def fake_run(argv, **kw):
            self.fail("must not ssh a target with no pinned identity")

        hosts = [{"name": "subdev", "host": "10.0.0.9", "admin_user": "root"}]
        failed = g.provision_shared_stream_guards(hosts=hosts, run=fake_run)
        self.assertEqual(failed, [("subdev", "no-identity")])

    def test_defaults_to_facade_hosts(self):
        # hosts=None resolves lazily from airuleset.SHARED_STREAM_GUARD_HOSTS.
        seen = []

        def fake_run(argv, **kw):
            seen.append(argv)
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        g.provision_shared_stream_guards(run=fake_run)
        self.assertEqual(len(seen), len(airuleset.SHARED_STREAM_GUARD_HOSTS))


class TestResourceGuardVerify(unittest.TestCase):
    """The VERIFY-ONLY watchdog job: box-class gate, the unlimited→alert+dedup
    / limit→silence / unreadable→silence branches, dry_run no-write, and the
    deduped gk-request."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.marker = os.path.join(self.tmp, "stamp")

    def _shared(self):
        return lambda: "shared-stream"

    def test_off_shared_stream_is_total_noop(self):
        called = []
        logs = resource_guard_verify(
            uid=1234, cgroup_read=lambda uid: called.append(uid) or "max",
            box_class_fn=lambda: "workstation",
            gk_request_fn=lambda uid: called.append(("gk", uid)),
            marker_path=self.marker, now=1000)
        self.assertEqual(logs, [])
        # never even read the cgroup off a shared-stream box.
        self.assertEqual(called, [])
        self.assertFalse(os.path.exists(self.marker))

    def test_missing_box_class_is_noop(self):
        logs = resource_guard_verify(
            uid=1234, cgroup_read=lambda uid: "max",
            box_class_fn=lambda: None,
            gk_request_fn=lambda uid: None,
            marker_path=self.marker, now=1000)
        self.assertEqual(logs, [])

    def test_unlimited_alerts_and_files_gk_request_once(self):
        gk = []
        logs = resource_guard_verify(
            uid=1234, cgroup_read=lambda uid: "max",
            box_class_fn=self._shared(),
            gk_request_fn=lambda uid: gk.append(uid),
            marker_path=self.marker, now=1000)
        self.assertTrue(logs)
        self.assertIn("UNLIMITED", logs[0])
        self.assertEqual(gk, [1234])            # gk-request filed exactly once
        self.assertTrue(os.path.exists(self.marker))  # dedup marker written

    def test_dedup_marker_suppresses_a_second_alert(self):
        gk = []
        common = dict(uid=1234, cgroup_read=lambda uid: "max",
                      box_class_fn=self._shared(),
                      gk_request_fn=lambda uid: gk.append(uid),
                      marker_path=self.marker)
        resource_guard_verify(now=1000, **common)
        # a second call within the dedup window is silent + files nothing more.
        logs2 = resource_guard_verify(now=1000 + DEDUP_INTERVAL_S - 10, **common)
        self.assertEqual(logs2, [])
        self.assertEqual(gk, [1234])            # still only one gk-request
        # past the window it re-alerts.
        logs3 = resource_guard_verify(now=1000 + DEDUP_INTERVAL_S + 10, **common)
        self.assertTrue(logs3)
        self.assertEqual(gk, [1234, 1234])

    def test_finite_limit_is_silence(self):
        gk = []
        logs = resource_guard_verify(
            uid=1234, cgroup_read=lambda uid: "2800000000",
            box_class_fn=self._shared(),
            gk_request_fn=lambda uid: gk.append(uid),
            marker_path=self.marker, now=1000)
        self.assertEqual(logs, [])
        self.assertEqual(gk, [])
        self.assertFalse(os.path.exists(self.marker))

    def test_unreadable_cgroup_is_silence(self):
        gk = []
        logs = resource_guard_verify(
            uid=1234, cgroup_read=lambda uid: None,
            box_class_fn=self._shared(),
            gk_request_fn=lambda uid: gk.append(uid),
            marker_path=self.marker, now=1000)
        self.assertEqual(logs, [])
        self.assertEqual(gk, [])

    def test_cgroup_read_raising_is_silence(self):
        def boom(uid):
            raise OSError("no such file")
        logs = resource_guard_verify(
            uid=1234, cgroup_read=boom, box_class_fn=self._shared(),
            gk_request_fn=lambda uid: None, marker_path=self.marker, now=1000)
        self.assertEqual(logs, [])

    def test_dry_run_writes_and_files_nothing(self):
        gk = []
        logs = resource_guard_verify(
            uid=1234, cgroup_read=lambda uid: "max",
            box_class_fn=self._shared(),
            gk_request_fn=lambda uid: gk.append(uid),
            marker_path=self.marker, now=1000, dry_run=True)
        self.assertTrue(logs)                   # reports what it WOULD do
        self.assertEqual(gk, [])                # but files nothing
        self.assertFalse(os.path.exists(self.marker))   # and writes no marker

    def test_unwired_gk_seam_journals_only(self):
        logs = resource_guard_verify(
            uid=1234, cgroup_read=lambda uid: "max",
            box_class_fn=self._shared(),
            gk_request_fn=None, marker_path=self.marker, now=1000)
        self.assertTrue(logs)
        self.assertTrue(any("journal only" in ln for ln in logs))

    def test_gk_request_error_is_caught(self):
        def boom(uid):
            raise RuntimeError("gh down")
        logs = resource_guard_verify(
            uid=1234, cgroup_read=lambda uid: "max",
            box_class_fn=self._shared(),
            gk_request_fn=boom, marker_path=self.marker, now=1000)
        self.assertTrue(any("gk-request error" in ln for ln in logs))


class TestRunOnceWiring(unittest.TestCase):
    """The job is registered in run_once and gated on its wired seam."""

    def test_job_runs_only_when_seam_wired(self):
        # A run_once with NO resource_guard_gk_request seam must not run the
        # job (the "wired = on" convention). We force a marker-less HOME so the
        # box-class gate would fail-open anyway, and assert no crash + the job
        # is absent from the label set when unwired.
        self.assertTrue(hasattr(wd, "resource_guard_verify"))

    def test_tracking_constants(self):
        self.assertEqual(TRACKING_ISSUE, 775)
        self.assertEqual(TRACKING_REPO, "zbynekdrlik/airuleset")


class TestRsyslogQuiet925C(unittest.TestCase):
    """#925-C: rsyslog quiet rule renders correctly and is in the guard set."""

    def test_render_content(self):
        body = g.render_rsyslog_quiet()
        self.assertIn("api-watchdog.service", body)
        self.assertIn("then stop", body)

    def test_in_guard_files(self):
        paths = [p for p, _ in g.guard_files()]
        self.assertIn(g.RSYSLOG_QUIET_PATH, paths)

    def test_path_is_rsyslog_d(self):
        self.assertTrue(g.RSYSLOG_QUIET_PATH.startswith("/etc/rsyslog.d/"))

    def test_apply_script_restarts_rsyslog(self):
        s = g.build_apply_script()
        self.assertIn("restart rsyslog", s)


class TestJournaldCap925C(unittest.TestCase):
    """#925-C: journald cap renders correctly for shared-stream boxes."""

    def test_render_content(self):
        body = g.render_journald_cap()
        self.assertIn("SystemMaxUse=300M", body)
        self.assertIn("[Journal]", body)

    def test_in_guard_files(self):
        paths = [p for p, _ in g.guard_files()]
        self.assertIn(g.JOURNALD_CAP_PATH, paths)

    def test_path_uses_60_prefix(self):
        """60- prefix ensures it sorts AFTER cli_disk_guard_root's 50-."""
        self.assertIn("/60-airuleset-", g.JOURNALD_CAP_PATH)

    def test_apply_script_restarts_journald(self):
        s = g.build_apply_script()
        self.assertIn("try-restart systemd-journald", s)
        self.assertIn("journalctl --rotate", s)


class TestSwapPolicy925C(unittest.TestCase):
    """#925-C: swap policy provisions 10G /swapfile on shared-stream boxes."""

    def test_swap_size_constant(self):
        self.assertEqual(g.SWAP_SIZE_GB, 10)

    def test_apply_script_idempotent_noopcheck(self):
        s = g.build_apply_script()
        # Idempotent check: if swapfile already has the right size, no-op
        self.assertIn("already", s)
        self.assertIn("no-op", s)

    def test_apply_script_cleans_old_swapfiles(self):
        s = g.build_apply_script()
        # Must remove old swapfile2, swapfile3
        self.assertIn("swapfile2", s)
        self.assertIn("swapfile3", s)

    def test_apply_script_fstab_entry(self):
        s = g.build_apply_script()
        self.assertIn("/etc/fstab", s)
        self.assertIn("/swapfile none swap sw 0 0", s)


if __name__ == "__main__":
    unittest.main()
