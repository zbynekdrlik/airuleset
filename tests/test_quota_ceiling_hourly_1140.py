"""#1140 part D — ceilings follow usage HOURLY and are bounded by the filesystem.

The daily refresh (part A+B) re-counts usage and re-applies ceilings once a
day, so between two recomputes an account has only 20 % headroom (montalu1
wrote 3+ GB of work-product snapshots in one day → EDQUOT again). Part D adds
a cheap hourly root oneshot that re-runs ONLY the ONE
``_render_quota_limits_block()`` (no quotaoff / quotacheck / quotaon) under the
shared quota lock, and bounds the hard limit to ``used + 50 % of the
filesystem's available space`` so one account is never granted the whole disk.

Every test renders text or runs the rendered bash against STUB binaries in a
temp dir — never a real quota tool, never root.
"""
import fcntl
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_resource_guards as crg  # noqa: E402
import cli_resource_guards_quota as crgq  # noqa: E402

G = 1024 * 1024  # 1 GiB in KiB


class TestCeilingRenderers(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(crgq.QUOTA_CEILING_SCRIPT_PATH,
                         "/usr/local/lib/airuleset/quota-ceiling.sh")
        self.assertEqual(crgq.QUOTA_CEILING_SERVICE_PATH,
                         "/etc/systemd/system/airuleset-quota-ceiling.service")
        self.assertEqual(crgq.QUOTA_CEILING_TIMER_PATH,
                         "/etc/systemd/system/airuleset-quota-ceiling.timer")

    def test_reexported_by_cli_resource_guards(self):
        for name in ("QUOTA_CEILING_SCRIPT_PATH", "QUOTA_CEILING_SERVICE_PATH",
                     "QUOTA_CEILING_TIMER_PATH", "render_quota_ceiling_script",
                     "render_quota_ceiling_service", "render_quota_ceiling_timer"):
            self.assertIs(getattr(crg, name), getattr(crgq, name), name)

    def test_timer_is_hourly_and_persistent(self):
        tmr = crg.render_quota_ceiling_timer()
        self.assertIn("OnCalendar=hourly", tmr)
        self.assertIn("Persistent=true", tmr)
        self.assertIn("Unit=airuleset-quota-ceiling.service", tmr)
        self.assertIn("WantedBy=timers.target", tmr)

    def test_service_shape(self):
        svc = crg.render_quota_ceiling_service()
        self.assertIn("Type=oneshot", svc)
        self.assertIn("ConditionPathExists=/aquota.user", svc)
        self.assertIn("After=airuleset-quota.service", svc)
        self.assertIn("ExecStart=/bin/bash %s" % crg.QUOTA_CEILING_SCRIPT_PATH, svc)
        # nothing is switched off, so nothing must be switched back on
        self.assertNotIn("quotaon", svc)

    def test_service_waits_out_a_same_minute_daily_refresh(self):
        """hourly and daily both elapse at 00:00 — ordering the ceiling job
        after the refresh job makes systemd queue it instead of racing the
        lock for the whole recount."""
        svc = crg.render_quota_ceiling_service()
        self.assertIn("After=airuleset-quota-refresh.service", svc)

    def test_service_timeout_exceeds_the_lock_wait(self):
        svc = crg.render_quota_ceiling_service()
        line = [ln for ln in svc.splitlines() if ln.startswith("TimeoutStartSec=")]
        self.assertEqual(len(line), 1, svc)
        self.assertGreater(int(line[0].split("=", 1)[1]), crg.QUOTA_LOCK_WAIT_S)

    def test_script_runs_only_the_limits_block_under_the_lock(self):
        s = crg.render_quota_ceiling_script()
        self.assertTrue(s.startswith("#!/bin/bash\n"), s[:40])
        self.assertIn("set -euo pipefail", s)
        self.assertIn(crg._render_quota_limits_block(), s)
        self.assertIn(crg.QUOTA_LOCK_PATH, s)
        self.assertLess(s.index("flock -w"),
                        s.index("per-user usage-aware quota limits"))
        self.assertIn("exit 4", s)
        # cheap: NEVER an accounting toggle or a filesystem walk
        for banned in ("quotacheck", "quotaoff", "quotaon"):
            self.assertNotIn(banned, s, banned)

    def test_script_exits_nonzero_on_qfail(self):
        s = crg.render_quota_ceiling_script()
        tail = s[s.index("per-user usage-aware quota limits"):]
        self.assertIn('"$qfail" -ne 0', tail)

    def test_limits_block_reads_filesystem_avail(self):
        blk = crg._render_quota_limits_block()
        self.assertIn("df --output=avail -k /", blk)

    def test_bash_syntax(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.sh")
            with open(p, "w") as f:
                f.write(crg.render_quota_ceiling_script())
            r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


class TestCeilingInstallWiring(unittest.TestCase):
    def test_guard_files_install_ceiling_trio(self):
        files = dict(crg.guard_files())
        self.assertEqual(files[crg.QUOTA_CEILING_SCRIPT_PATH],
                         crg.render_quota_ceiling_script())
        self.assertEqual(files[crg.QUOTA_CEILING_SERVICE_PATH],
                         crg.render_quota_ceiling_service())
        self.assertEqual(files[crg.QUOTA_CEILING_TIMER_PATH],
                         crg.render_quota_ceiling_timer())

    def test_units_are_written_before_daemon_reload(self):
        script = crg.build_apply_script()
        reload_at = script.index("systemctl daemon-reload")
        for path in (crg.QUOTA_CEILING_SCRIPT_PATH, crg.QUOTA_CEILING_SERVICE_PATH,
                     crg.QUOTA_CEILING_TIMER_PATH):
            self.assertLess(script.index("_install %s" % path), reload_at, path)

    def test_apply_enables_ceiling_timer_with_readback(self):
        blk = crg._render_quota_apply_block()
        self.assertIn("systemctl enable --now airuleset-quota-ceiling.timer", blk)
        self.assertIn("systemctl is-enabled airuleset-quota-ceiling.timer", blk)
        self.assertIn("QUOTA VERIFY FAIL: ceiling timer", blk)


class TestCeilingScriptExecution(unittest.TestCase):
    """Run the rendered hourly script under STATEFUL stubs: setquota records
    the limits it was given and repquota reports them back, so the script's
    own read-back verify is exercised against the value it computed."""

    def _setup(self, td, used_kib, avail_kib="default", df_rc=0,
               with_aquota=True, df_warn=False):
        log = os.path.join(td, "calls.log")
        state = os.path.join(td, "limits")
        bindir = os.path.join(td, "bin")
        os.makedirs(bindir)
        home = os.path.join(td, "home", "u1", ".claude")
        os.makedirs(home)
        with open(os.path.join(home, "airuleset-box-class"), "w") as f:
            f.write("shared-stream\n")
        if avail_kib == "default":
            avail_kib = 1024 * G  # 1 TiB free: the bound never bites
        stubs = {
            "setquota": ('echo "setquota $*" >> %s\n'
                         '[ "$1" = -u ] && echo "$3 $4" > %s\nexit 0' % (log, state)),
            "repquota": ('lim=$(cat %s 2>/dev/null || echo "0 0")\n'
                         'echo "u1  --  %s $lim  0 0 0"' % (state, used_kib)),
            # df_warn: a harmless stderr line AFTER the value (rc 0)
            "df": ('[ %d -eq 0 ] || { echo "df: /: I/O error" >&2; exit %d; }\n'
                   'echo "  Avail"; echo "  %s"\n%s'
                   % (df_rc, df_rc, avail_kib,
                      'echo "df: /snap/x: Permission denied" >&2' if df_warn else '')),
            "systemctl": 'echo "systemctl $*" >> %s' % log,
            "quotaon": 'echo "quotaon $*" >> %s' % log,
            "quotaoff": 'echo "quotaoff $*" >> %s' % log,
            "quotacheck": 'echo "quotacheck $*" >> %s' % log,
        }
        for name, body in stubs.items():
            p = os.path.join(bindir, name)
            with open(p, "w") as f:
                f.write("#!/usr/bin/env bash\n" + body + "\n")
            os.chmod(p, 0o755)
        aq = os.path.join(td, "aquota.user")
        if with_aquota:
            open(aq, "w").close()
        self.lock = os.path.join(td, "quota.lock")
        script = crg.render_quota_ceiling_script()
        script = script.replace("/aquota.user", aq)
        script = script.replace("for home in /home/*", "for home in %s/home/*" % td)
        script = script.replace(crg.QUOTA_LOCK_PATH, self.lock)
        script = script.replace("flock -w %d" % crg.QUOTA_LOCK_WAIT_S, "flock -w 1")
        path = os.path.join(td, "ceiling.sh")
        with open(path, "w") as f:
            f.write(script)
        env = dict(os.environ)
        env["PATH"] = bindir + ":" + env.get("PATH", "")
        return path, env, log

    def _run(self, path, env):
        return subprocess.run(["bash", path], env=env, capture_output=True,
                              text=True, timeout=30)

    def _setquota_user_calls(self, log):
        if not os.path.exists(log):
            return []
        return [ln for ln in open(log).read().splitlines()
                if ln.startswith("setquota -u ")]

    def test_normal_case_hard_is_used_times_1_2(self):
        used = 20 * G
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, used)
            r = self._run(path, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            hard = (used * 120 + 99) // 100
            soft = (used * 110 + 99) // 100
            self.assertEqual(self._setquota_user_calls(log),
                             ["setquota -u u1 %d %d 0 0 /" % (soft, hard)])
            self.assertIn("u1 verified", r.stdout)

    def test_fs_bounded_hard_is_used_plus_half_avail(self):
        used, avail = 20 * G, 2 * G
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, used, avail_kib=avail)
            r = self._run(path, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            bound = used + avail * 50 // 100          # 21G < used*1.2 = 24G
            # soft (used*1.1 = 22G) is clamped to never exceed hard
            self.assertEqual(self._setquota_user_calls(log),
                             ["setquota -u u1 %d %d 0 0 /" % (bound, bound)])
            self.assertIn("u1 verified", r.stdout)

    def test_fs_bound_caps_the_floor_too(self):
        """min(max(10G, used*1.2), used + avail/2) — a nearly full disk wins
        over the fleet floor (the box-level disk guard is the backstop)."""
        used, avail = 1 * G, 4 * G
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, used, avail_kib=avail)
            r = self._run(path, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            bound = used + avail // 2
            self.assertEqual(self._setquota_user_calls(log),
                             ["setquota -u u1 %d %d 0 0 /" % (bound, bound)])

    def test_bound_above_the_formula_changes_nothing(self):
        used, avail = 20 * G, 10 * G              # bound 25G > 24G
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, used, avail_kib=avail)
            r = self._run(path, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            hard = (used * 120 + 99) // 100
            soft = (used * 110 + 99) // 100
            self.assertEqual(self._setquota_user_calls(log),
                             ["setquota -u u1 %d %d 0 0 /" % (soft, hard)])

    def test_df_failure_is_loud_and_keeps_the_unbounded_ceiling(self):
        """An unreadable fs size never skips the ceiling (a stale, lower
        ceiling IS the EDQUOT incident) — it applies the unbounded formula and
        fails the unit LOUD."""
        used = 20 * G
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, used, df_rc=1)
            r = self._run(path, env)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("df", r.stderr)
            hard = (used * 120 + 99) // 100
            soft = (used * 110 + 99) // 100
            self.assertEqual(self._setquota_user_calls(log),
                             ["setquota -u u1 %d %d 0 0 /" % (soft, hard)])

    def test_non_numeric_df_output_is_treated_as_unreadable(self):
        used = 20 * G
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, used, avail_kib="-")
            r = self._run(path, env)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            hard = (used * 120 + 99) // 100
            self.assertTrue(self._setquota_user_calls(log)[0].endswith(
                " %d 0 0 /" % hard), self._setquota_user_calls(log))

    def test_full_disk_never_turns_into_no_limit(self):
        """setquota reads 0 as NO limit — used=0 on a full disk gets 1 KiB."""
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, 0, avail_kib=0)
            r = self._run(path, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self._setquota_user_calls(log),
                             ["setquota -u u1 1 1 0 0 /"])

    def test_df_stderr_warning_does_not_void_the_bound(self):
        """review: only df's stdout is parsed — a warning on stderr (rc 0)
        must not throw the fs bound away."""
        used, avail = 20 * G, 2 * G
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, used, avail_kib=avail, df_warn=True)
            r = self._run(path, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            bound = used + avail // 2
            self.assertEqual(self._setquota_user_calls(log),
                             ["setquota -u u1 %d %d 0 0 /" % (bound, bound)])

    def test_leading_zero_avail_is_base_10(self):
        """review: bash reads 08 as octal and errors — force base 10."""
        used = 20 * G
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, used, avail_kib="0%d" % (2 * G))
            r = self._run(path, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            bound = used + G
            self.assertEqual(self._setquota_user_calls(log),
                             ["setquota -u u1 %d %d 0 0 /" % (bound, bound)])

    def test_non_numeric_usage_is_loud_and_skips_the_user(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, "12x4")
            r = self._run(path, env)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("non-numeric usage", r.stderr)
            self.assertEqual(self._setquota_user_calls(log), [])

    def test_never_toggles_quota_or_recounts(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, 5 * G)
            r = self._run(path, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            calls = [c.split()[0] for c in open(log).read().splitlines()]
            for banned in ("quotaon", "quotaoff", "quotacheck"):
                self.assertNotIn(banned, calls)

    def test_lock_held_touches_nothing_exit_4(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, 5 * G)
            with open(self.lock, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                r = self._run(path, env)
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(log), "ran under a held quota lock")
            self.assertIn("lock", r.stderr)

    def test_missing_quota_file_exits_3(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log = self._setup(td, 5 * G, with_aquota=False)
            r = self._run(path, env)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(log))


if __name__ == "__main__":
    unittest.main()
