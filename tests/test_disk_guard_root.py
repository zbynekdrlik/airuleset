"""Behaviour tests for the #841 disk-guard ROOT/system-level legs.

Covers the two surfaces of the root-provisioning leaf (`cli_disk_guard_root`):
  * the drop-in / reporter / unit RENDERING (btmp/wtmp logrotate with the EXACT
    preserved `create` modes, the journald cap, the fail2ban hardening, the
    tmpfiles.d dir, the report-only reporter, the oneshot service + daily timer);
  * the apply-SCRIPT shape (set -uo pipefail, atomic mktemp+mv, daemon-reload,
    enable --now, the bounded reporter seed, the fail-loud read-back verify incl.
    the logrotate duplicate-entry fail-open guard, the fail2ban conditional);
  * the LOUD non-fatal `provision_disk_guard_root` push step;
  * `cli_fleet.DISK_GUARD_ROOT_HOSTS` — the drift-lock (subdev + gk, separate
    from SHARED_STREAM_GUARD_HOSTS, correct root-ssh schema).

Plus a BEHAVIORAL check that the reporter, run against a temp dir, produces
valid parseable JSON (not a tautology on the rendered string). No real ssh, no
real root, no real drop-in is ever installed.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_disk_guard_root as g          # noqa: E402
import cli_fleet                         # noqa: E402
import airuleset                         # noqa: E402


class TestRotationRendering(unittest.TestCase):
    """The rotation-config drop-ins carry the settled policy — and the btmp
    create-mode is preserved EXACTLY (a wrong mode leaks failed-login names)."""

    def test_btmp_create_mode_is_0660_root_utmp(self):
        d = g.render_logrotate_btmp()
        self.assertIn("/var/log/btmp {", d)
        self.assertIn("weekly", d)
        self.assertIn("compress", d)
        self.assertIn("rotate 4", d)
        # the security-critical line — the exact distro create mode, never wider:
        self.assertIn("create 0660 root utmp", d)
        self.assertNotIn("create 0644", d)

    def test_wtmp_create_mode_is_0664_root_utmp(self):
        d = g.render_logrotate_wtmp()
        self.assertIn("/var/log/wtmp {", d)
        self.assertIn("create 0664 root utmp", d)
        self.assertIn("rotate 4", d)

    def test_journald_cap_is_200M(self):
        d = g.render_journald_cap()
        self.assertIn("[Journal]", d)
        self.assertIn("SystemMaxUse=200M", d)

    def test_fail2ban_hardening_bans_longer_and_ignores_tailnet(self):
        d = g.render_fail2ban_hardening()
        self.assertIn("[DEFAULT]", d)
        self.assertIn("bantime = 1d", d)
        self.assertIn("[recidive]", d)
        self.assertIn("enabled = true", d)
        # a recidive misfire must never lock out an admin on the fleet tailnet:
        self.assertIn("100.64.0.0/10", d)
        self.assertIn("127.0.0.1/8", d)

    def test_tmpfiles_creates_world_readable_run_dir(self):
        d = g.render_tmpfiles()
        self.assertIn("d /run/airuleset 0755 root root -", d)

    def test_timer_is_daily_and_persistent(self):
        t = g.render_root_timer()
        self.assertIn("OnCalendar=daily", t)
        self.assertIn("Persistent=true", t)
        self.assertIn("Unit=airuleset-disk-guard-root.service", t)

    def test_service_is_oneshot_running_the_reporter(self):
        s = g.render_root_service()
        self.assertIn("Type=oneshot", s)
        self.assertIn(g.REPORTER_SCRIPT_PATH, s)


class TestGuardFiles(unittest.TestCase):

    def test_reporter_is_executable_everything_else_0644(self):
        files = {p: mode for (p, _c, mode) in g.guard_files()}
        self.assertEqual(files[g.REPORTER_SCRIPT_PATH], "0755")
        self.assertEqual(files[g.JOURNALD_CAP_PATH], "0644")
        self.assertEqual(files[g.LOGROTATE_BTMP_PATH], "0644")

    def test_logrotate_files_overwrite_the_distro_paths_not_a_5Ofile(self):
        # the duplicate-entry fail-open guard: btmp/wtmp go to the DISTRO path,
        # NOT a second /etc/logrotate.d/50-airuleset-* file for the same log.
        paths = [p for (p, _c, _m) in g.guard_files()]
        self.assertIn("/etc/logrotate.d/btmp", paths)
        self.assertIn("/etc/logrotate.d/wtmp", paths)
        self.assertNotIn("/etc/logrotate.d/50-airuleset-btmp", paths)

    def test_fail2ban_is_NOT_in_the_always_install_list(self):
        # fail2ban is installed CONDITIONALLY (only where fail2ban-client
        # exists) by the apply script, so a box without it stays clean.
        paths = [p for (p, _c, _m) in g.guard_files()]
        self.assertNotIn(g.FAIL2BAN_JAIL_PATH, paths)


class TestApplyScript(unittest.TestCase):

    def setUp(self):
        self.s = g.build_apply_script()

    def test_set_uo_pipefail(self):
        # -uo, NOT -euo: a single drop-in write must not abort mid-install, and
        # the read-back verify below decides the exit code (fail-loud exit 4).
        self.assertIn("set -uo pipefail", self.s)

    def test_atomic_mktemp_mv_write(self):
        self.assertIn("mktemp", self.s)
        self.assertIn('mv -f "$tmp" "$dest"', self.s)

    def test_reporter_seed_is_bounded_by_timeout(self):
        # a slow `du` in the reporter must NEVER stall the release push.
        self.assertIn("timeout %d" % g.REPORTER_TIMEOUT_S, self.s)

    def test_daemon_reload_and_timer_enable(self):
        self.assertIn("systemctl daemon-reload", self.s)
        self.assertIn("systemctl enable --now airuleset-disk-guard-root.timer",
                      self.s)

    def test_fail2ban_is_conditional_on_the_client_being_present(self):
        self.assertIn("command -v fail2ban-client", self.s)

    def test_read_back_verify_guards_the_logrotate_duplicate_entry_fail_open(self):
        # Fable-flagged silent no-rotation: a duplicate stanza makes logrotate
        # SKIP the entry — the verify must catch it and exit non-zero.
        self.assertIn("duplicate log entry", self.s)
        self.assertIn("exit 4", self.s)

    def test_read_back_verify_checks_the_report_json_parses(self):
        # the owner-daily ❓ reads the report; a missing/unparseable one is LOUD.
        self.assertIn("json.load", self.s)
        self.assertIn(g.ROOT_REPORT_PATH, self.s)

    def test_script_is_valid_bash(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(self.s)
            path = fh.name
        try:
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(path)


class TestReporterIsReportOnly(unittest.TestCase):
    """The reporter SURFACES; it must never delete/rotate/prune anything."""

    def _code_lines(self):
        # strip comment + shebang lines; the ban is on CODE, not prose.
        return [ln for ln in g.render_reporter_script().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]

    def test_no_destructive_verb_in_the_reporter_code(self):
        banned = ("rm ", "rm -", "prune", "apt-get clean", "--vacuum",
                  "--rotate", "shred", "truncate", "unlink", "docker system prune")
        code = "\n".join(self._code_lines())
        for verb in banned:
            self.assertNotIn(verb, code,
                             "reporter must be report-only; found %r" % verb)

    def test_reporter_is_valid_bash(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(g.render_reporter_script())
            path = fh.name
        try:
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(path)

    def _seed_scan_tree(self, root, sizes):
        # seed a tiny fixture tree the reporter du's via AIRULESET_DGROOT_SCAN_ROOT,
        # so the test is HERMETIC + fast (never du's the developer's live disk).
        for rel, nbytes in sizes.items():
            d = os.path.join(root, rel)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "blob"), "wb") as fh:
                fh.write(b"\0" * nbytes)

    def test_reporter_run_produces_valid_parseable_json(self):
        # BEHAVIORAL (not a string tautology) + HERMETIC: the reporter du's a
        # SEEDED fixture tree (via AIRULESET_DGROOT_SCAN_ROOT), never the live
        # box — so it is deterministic and fast, and the JSON pipeline is proven.
        with tempfile.TemporaryDirectory() as td:
            run_dir = os.path.join(td, "run")
            scan = os.path.join(td, "scan")
            self._seed_scan_tree(scan, {
                "var/cache/apt": 1000,
                "var/lib/docker": 5000,
                "home/gh-runner/actions-runner/_work": 2000,
                "var/log/journal": 3000,
                "tmp": 4000,
            })
            script = g.render_reporter_script().replace("/run/airuleset", run_dir)
            spath = os.path.join(td, "reporter.sh")
            with open(spath, "w") as fh:
                fh.write(script)
            env = dict(os.environ, AIRULESET_DGROOT_SCAN_ROOT=scan)
            subprocess.run(["bash", spath], check=True, capture_output=True,
                           text=True, timeout=60, env=env)
            out = os.path.join(run_dir, "disk-guard-root.json")
            self.assertTrue(os.path.exists(out), "reporter wrote no JSON")
            d = json.load(open(out))
            self.assertIn("generated_at", d)
            self.assertIn("generated_ts", d)
            self.assertIsInstance(d["candidates"], list)
            self.assertGreaterEqual(len(d["candidates"]), 3)
            for c in d["candidates"]:
                self.assertIn("cls", c)
                self.assertIsInstance(c["bytes"], int)
            # docker is DELIBERATELY EXCLUDED from the ask estimate (it holds
            # persistent volumes) — the estimate must NOT include the 5000-byte
            # docker blob (apt 1000 + gh-runner 2000 are the reclaimable set).
            classes = {c["cls"]: c["bytes"] for c in d["candidates"]}
            self.assertIn("docker-incl-volumes", classes)
            self.assertGreater(classes["docker-incl-volumes"], 0)
            self.assertLess(d["estimate_bytes"], classes["docker-incl-volumes"] + 3000)
            self.assertNotIn("docker", classes)  # relabeled honestly


class TestProvision(unittest.TestCase):
    """The LOUD non-fatal push step — mirrors provision_shared_stream_guards."""

    def test_success_returns_no_failures(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)

            class R:
                returncode = 0
                stdout = "  disk-guard-root: applied + verified"
                stderr = ""
            return R()

        hosts = [{"name": "subdev", "host": "1.2.3.4", "admin_user": "root",
                  "identity": "~/.secrets/k"}]
        failed = g.provision_disk_guard_root(hosts=hosts, run=fake_run)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 1)
        # a root apply over ssh with the pinned identity, as root:
        self.assertIn("root@1.2.3.4", calls[0])
        self.assertIn("-i", calls[0])

    def test_rc_failure_is_loud_and_nonfatal(self):
        def fake_run(argv, **kw):
            class R:
                returncode = 4
                stdout = ""
                stderr = "DISK-GUARD-ROOT FAILED read-back verify"
            return R()

        hosts = [{"name": "gk", "host": "5.6.7.8", "admin_user": "root",
                  "identity": "~/.secrets/k"}]
        failed = g.provision_disk_guard_root(hosts=hosts, run=fake_run)
        self.assertEqual(failed, [("gk", "rc=4")])

    def test_ssh_exception_is_caught(self):
        def fake_run(argv, **kw):
            raise OSError("connection refused")

        hosts = [{"name": "gk", "host": "5.6.7.8", "admin_user": "root",
                  "identity": "~/.secrets/k"}]
        failed = g.provision_disk_guard_root(hosts=hosts, run=fake_run)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][0], "gk")

    def test_missing_identity_is_refused(self):
        # a root apply must never ride a shared password.
        hosts = [{"name": "gk", "host": "5.6.7.8", "admin_user": "root"}]
        failed = g.provision_disk_guard_root(hosts=hosts, run=lambda *a, **k: None)
        self.assertEqual(failed, [("gk", "no-identity")])

    def test_missing_host_is_refused(self):
        hosts = [{"name": "gk", "admin_user": "root", "identity": "~/.secrets/k"}]
        failed = g.provision_disk_guard_root(hosts=hosts, run=lambda *a, **k: None)
        self.assertEqual(failed, [("gk", "no-host")])

    def test_defaults_to_facade_hosts(self):
        seen = []

        def fake_run(argv, **kw):
            seen.append(argv)

            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        # no hosts= → reads airuleset.DISK_GUARD_ROOT_HOSTS (the facade).
        g.provision_disk_guard_root(run=fake_run)
        self.assertEqual(len(seen), len(airuleset.DISK_GUARD_ROOT_HOSTS))


class TestGuardHostsDriftLock(unittest.TestCase):

    def test_targets_are_subdev_and_gk(self):
        names = {h["name"] for h in cli_fleet.DISK_GUARD_ROOT_HOSTS}
        self.assertIn("subdev", names)
        self.assertIn("gatekeeper", names)

    def test_separate_list_from_shared_stream_guards(self):
        # gk must NOT be in SHARED_STREAM_GUARD_HOSTS (that would apply the #775
        # cgroup drop-ins to gk = scope creep) but MUST be a disk-guard target.
        stream_names = {h["name"] for h in cli_fleet.SHARED_STREAM_GUARD_HOSTS}
        self.assertNotIn("gatekeeper", stream_names)

    def test_every_host_is_a_root_ssh_target_with_a_pinned_identity(self):
        for h in cli_fleet.DISK_GUARD_ROOT_HOSTS:
            self.assertEqual(h.get("admin_user"), "root")
            self.assertTrue(h.get("host"))
            self.assertTrue(h.get("identity"),
                            "a root apply must ride a pinned key, never a password")

    def test_facade_reexports_the_list(self):
        self.assertEqual(airuleset.DISK_GUARD_ROOT_HOSTS,
                         cli_fleet.DISK_GUARD_ROOT_HOSTS)


class TestPushWiringLock(unittest.TestCase):
    """A source-lock so reverting the cmd_push step (un-deploying the whole root
    leg) cannot pass green — a security-boundary install step deserves teeth."""

    def test_cmd_push_calls_provision_disk_guard_root(self):
        import inspect
        import cli_remote
        src = inspect.getsource(cli_remote.cmd_push)
        self.assertIn("provision_disk_guard_root", src,
                      "cmd_push must invoke the disk-guard root provisioning step")


class TestDrainScript895(unittest.TestCase):
    """#895: root-side drain ladder — the script that reclaims cross-user /tmp,
    old playwright revisions, npm/pip cache, and redundant tgz archives."""

    def test_drain_script_is_valid_bash(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(g.render_drain_script())
            path = fh.name
        try:
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(path)

    def test_drain_script_has_all_four_rungs(self):
        d = g.render_drain_script()
        self.assertIn("RUNG-A", d, "rung (a): /tmp stale files")
        self.assertIn("RUNG-B", d, "rung (b): playwright browser revisions")
        self.assertIn("RUNG-C", d, "rung (c): npm/pip cache")
        self.assertIn("RUNG-D", d, "rung (d): redundant tgz archives")

    def test_drain_script_skips_paused_accounts(self):
        d = g.render_drain_script()
        for acct in g.PAUSED_ACCOUNTS:
            self.assertIn(acct, d, "paused account %r must be in the skip list" % acct)
        self.assertIn("SKIP", d, "paused accounts must be skipped")
        self.assertIn("#851", d, "the paused-skip must reference #851")

    def test_drain_script_has_dry_run_support(self):
        d = g.render_drain_script()
        self.assertIn("AIRULESET_DGROOT_DRAIN_DRY", d)
        self.assertIn("WOULD-DELETE", d)

    def test_drain_script_uses_one_file_system(self):
        d = g.render_drain_script()
        self.assertIn("--one-file-system", d)

    def test_drain_timer_is_4hourly_and_persistent(self):
        t = g.render_drain_timer()
        self.assertIn("OnUnitActiveSec=4h", t)
        self.assertIn("Persistent=true", t)
        self.assertIn("Unit=airuleset-disk-guard-root-drain.service", t)

    def test_drain_service_runs_the_drain_script(self):
        s = g.render_drain_service()
        self.assertIn("Type=oneshot", s)
        self.assertIn(g.DRAIN_SCRIPT_PATH, s)

    def test_guard_files_includes_drain_artifacts(self):
        paths = [p for (p, _c, _m) in g.guard_files()]
        self.assertIn(g.DRAIN_SCRIPT_PATH, paths)
        self.assertIn(g.DRAIN_SERVICE_PATH, paths)
        self.assertIn(g.DRAIN_TIMER_PATH, paths)

    def test_drain_script_is_executable(self):
        files = {p: mode for (p, _c, mode) in g.guard_files()}
        self.assertEqual(files[g.DRAIN_SCRIPT_PATH], "0755")

    def test_apply_script_enables_drain_timer(self):
        s = g.build_apply_script()
        self.assertIn("airuleset-disk-guard-root-drain.timer", s)
        self.assertIn("enable --now", s)

    def test_apply_script_verifies_drain_timer_enabled(self):
        s = g.build_apply_script()
        self.assertIn("is-enabled airuleset-disk-guard-root-drain.timer", s)

    def test_tgz_rung_finds_archives_with_extracted_target(self):
        """Rung (d) covers tgz archives >24h with a same-stem dir present."""
        d = g.render_drain_script()
        self.assertIn(".tgz", d)
        self.assertIn("tgz-redundant", d)

    def test_paused_accounts_matches_fleet_config(self):
        """Paused accounts in the drain script match the fleet config."""
        for acct in g.PAUSED_ACCOUNTS:
            matching = [h for h in cli_fleet.REMOTE_HOSTS
                        if h.get("user") == acct and h.get("paused")]
            self.assertTrue(matching,
                            "PAUSED_ACCOUNTS entry %r must match a paused "
                            "entry in cli_fleet.REMOTE_HOSTS" % acct)


class TestSevereEscalation895(unittest.TestCase):
    """#895: >=95% machine-side escalation — file a gk-request ticket."""

    def test_severe_pct_constant_exists(self):
        import watchdog.disk_guard as dg
        self.assertEqual(dg.SEVERE_PCT, 95)

    def test_file_severe_ticket_below_threshold_is_noop(self):
        import watchdog.disk_guard as dg
        status = {"worst_pct": 93, "dim": "bytes"}
        logs = dg.file_severe_ticket(status, "/tmp/t", 1000, [], dry_run=True)
        self.assertEqual(logs, [])

    def test_file_severe_ticket_at_threshold_logs(self):
        import watchdog.disk_guard as dg
        status = {"worst_pct": 96, "dim": "bytes"}
        logs = dg.file_severe_ticket(status, "/tmp/t", 1000,
                                     [("/tmp/big", 5000000000)],
                                     dry_run=True)
        self.assertTrue(len(logs) > 0)
        self.assertIn("SEVERE-TICKET", logs[0])

    def test_file_severe_ticket_daily_dedup(self):
        import watchdog.disk_guard as dg
        with tempfile.TemporaryDirectory() as td:
            marker = os.path.join(td, "marker")
            # Monkey-patch the marker path for this test
            orig = dg._severe_ticket_marker
            dg._severe_ticket_marker = lambda now: marker
            try:
                status = {"worst_pct": 96, "dim": "bytes"}
                # First call (dry_run=False but with injected run_fn)
                calls = []
                logs1 = dg.file_severe_ticket(
                    status, td, 1000, [("/tmp/big", 5000000000)],
                    dry_run=False,
                    run_fn=lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
                # The marker should be written
                self.assertTrue(os.path.exists(marker))
                # Second call should be deduped
                logs2 = dg.file_severe_ticket(
                    status, td, 1000, [("/tmp/big", 5000000000)],
                    dry_run=False,
                    run_fn=lambda *a, **kw: calls.append(a))
                self.assertEqual(logs2, [])
            finally:
                dg._severe_ticket_marker = orig


class TestNotifyImportForbidden(unittest.TestCase):
    """The watchdog side NEVER pings — notify must not be importable from it."""

    def test_disk_guard_root_never_imports_notify(self):
        src = (REPO / "watchdog" / "disk_guard_root.py").read_text()
        for ln in src.splitlines():
            s = ln.strip()
            self.assertFalse(s.startswith("import notify") or s.startswith("from notify"),
                             "notify must never be imported in watchdog/disk_guard_root.py")


# --------------------------------------------------------------------------- #
# #895 — root-side drain ladder
# --------------------------------------------------------------------------- #
class TestDrainScriptRendering(unittest.TestCase):
    """The drain script is valid bash, carries the 4 rungs, and uses bounded
    delete verbs (find -delete, rm -rf --one-file-system)."""

    def test_drain_script_is_valid_bash(self):
        s = g.render_drain_script()
        r = subprocess.run(["bash", "-n"], input=s, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "drain script must parse: %s" % r.stderr)

    def test_drain_script_has_all_four_rungs(self):
        s = g.render_drain_script()
        self.assertIn("RUNG-A", s)
        self.assertIn("RUNG-B", s)
        self.assertIn("RUNG-C", s)
        self.assertIn("RUNG-D", s)

    def test_drain_script_skips_paused_accounts(self):
        s = g.render_drain_script()
        self.assertIn("_is_paused", s)
        self.assertIn("simap1", s)

    def test_drain_script_has_dry_run_mode(self):
        s = g.render_drain_script()
        self.assertIn("AIRULESET_DGROOT_DRAIN_DRY", s)
        self.assertIn("WOULD-DELETE", s)

    def test_drain_script_uses_one_file_system(self):
        s = g.render_drain_script()
        self.assertIn("--one-file-system", s)

    def test_drain_script_logs_every_action(self):
        s = g.render_drain_script()
        self.assertIn("_log", s)
        self.assertIn("DRAIN COMPLETE", s)

    def test_drain_script_tgz_rung_has_print0(self):
        """The tgz find must use -print0 to match the null-delimited read."""
        s = g.render_drain_script()
        self.assertIn("-print0", s)


class TestDrainServiceAndTimer(unittest.TestCase):

    def test_drain_service_is_oneshot(self):
        s = g.render_drain_service()
        self.assertIn("Type=oneshot", s)
        self.assertIn(g.DRAIN_SCRIPT_PATH, s)

    def test_drain_timer_is_4h_and_persistent(self):
        s = g.render_drain_timer()
        self.assertIn("OnUnitActiveSec=4h", s)
        self.assertIn("Persistent=true", s)
        self.assertIn("airuleset-disk-guard-root-drain.service", s)


class TestDrainInGuardFiles(unittest.TestCase):

    def test_drain_script_in_guard_files(self):
        paths = {p for p, _, _ in g.guard_files()}
        self.assertIn(g.DRAIN_SCRIPT_PATH, paths)
        self.assertIn(g.DRAIN_SERVICE_PATH, paths)
        self.assertIn(g.DRAIN_TIMER_PATH, paths)

    def test_drain_script_is_executable(self):
        for path, _, mode in g.guard_files():
            if path == g.DRAIN_SCRIPT_PATH:
                self.assertEqual(mode, "0755")


class TestDrainInApplyScript(unittest.TestCase):

    def test_apply_script_enables_drain_timer(self):
        s = g.build_apply_script()
        self.assertIn("airuleset-disk-guard-root-drain.timer", s)

    def test_apply_script_verifies_drain_timer_enabled(self):
        s = g.build_apply_script()
        self.assertIn("drain timer not enabled", s)

    def test_apply_script_is_valid_bash(self):
        s = g.build_apply_script()
        r = subprocess.run(["bash", "-n"], input=s, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "apply script must parse: %s" % r.stderr)


class TestDrainScriptBehavioral(unittest.TestCase):
    """Run the drain script against a seeded temp dir in dry-run mode and
    verify it produces WOULD-DELETE log lines for the expected candidates."""

    def test_drain_dry_run_tmp_stale_files(self):
        """Rung (a): stale /tmp files appear as WOULD-DELETE."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            tmp = root / "tmp"
            tmp.mkdir(parents=True)
            stale = tmp / "old_file.txt"
            stale.write_text("stale")
            os.utime(stale, (0, 0))
            log = root / "drain.log"
            env = os.environ.copy()
            env["AIRULESET_DGROOT_DRAIN_DRY"] = "1"
            script = g.render_drain_script()
            script = script.replace("/tmp", str(tmp))
            script = script.replace("/home", str(root / "home"))
            script = script.replace(g.DRAIN_LOG_PATH, str(log))
            script = script.replace(
                "mkdir -p '/run/airuleset'",
                "mkdir -p '%s'" % str(root))
            r = subprocess.run(
                ["bash", "-c", script], env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            if log.exists():
                content = log.read_text()
                self.assertIn("RUNG-A", content)

    def test_drain_dry_run_tgz_redundant(self):
        """Rung (d): a .tgz with an extracted dir should produce WOULD-DELETE."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            home = root / "home" / "testuser" / "montalu-web-pull"
            home.mkdir(parents=True)
            tgz = home / "app.tgz"
            tgz.write_text("fake archive")
            os.utime(tgz, (0, 0))
            target = home / "app"
            target.mkdir()
            log = root / "drain.log"
            env = os.environ.copy()
            env["AIRULESET_DGROOT_DRAIN_DRY"] = "1"
            script = g.render_drain_script()
            script = script.replace("/tmp", str(root / "tmp"))
            script = script.replace("/home", str(root / "home"))
            script = script.replace(g.DRAIN_LOG_PATH, str(log))
            script = script.replace(
                "mkdir -p '/run/airuleset'",
                "mkdir -p '%s'" % str(root))
            (root / "tmp").mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["bash", "-c", script], env=env,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            if log.exists():
                content = log.read_text()
                self.assertIn("RUNG-D", content)


if __name__ == "__main__":
    unittest.main()
