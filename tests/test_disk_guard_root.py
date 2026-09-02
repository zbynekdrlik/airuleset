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

    def test_reporter_run_produces_valid_parseable_json(self):
        # BEHAVIORAL (not a string tautology): run the reporter against a temp
        # dir (redirecting the hardcoded /run/airuleset) and load the JSON it
        # writes — proving the du/JSON pipeline actually works.
        with tempfile.TemporaryDirectory() as td:
            script = g.render_reporter_script().replace("/run/airuleset", td)
            spath = os.path.join(td, "reporter.sh")
            with open(spath, "w") as fh:
                fh.write(script)
            subprocess.run(["bash", spath], check=True, capture_output=True,
                           text=True, timeout=120)
            out = os.path.join(td, "disk-guard-root.json")
            self.assertTrue(os.path.exists(out), "reporter wrote no JSON")
            d = json.load(open(out))
            self.assertIn("generated_at", d)
            self.assertIn("generated_ts", d)
            self.assertIn("estimate_bytes", d)
            self.assertIsInstance(d["candidates"], list)
            self.assertGreaterEqual(len(d["candidates"]), 3)
            for c in d["candidates"]:
                self.assertIn("cls", c)
                self.assertIsInstance(c["bytes"], int)


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


if __name__ == "__main__":
    unittest.main()
