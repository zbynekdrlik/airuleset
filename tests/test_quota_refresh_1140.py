"""#1140 parts A + B — quota accounting stays TRUE and ceilings follow usage.

A: the boot unit runs ``quotacheck -u -m /`` (NO ``-c`` — ``-c`` recreates the
   quota file WITHOUT limits, the 24.9 live incident) after the journaled
   remount and before ``quotaon``; a daily root refresh re-runs it.
B: the SAME ``_render_quota_limits_block()`` (one renderer, two callers: the
   push apply and the refresh) re-applies the usage-aware ceilings daily.

Every test here renders text or runs the rendered bash against STUB binaries
in a temp dir — never a real quota/setquota/quotacheck, never root.
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_resource_guards as crg  # noqa: E402

_QC_RE = re.compile(r"\bquotacheck((?:\s+-[A-Za-z]+)*)")


def _quotacheck_flag_sets(text):
    """Every ``quotacheck`` invocation in ``text`` → its short-flag letters."""
    out = []
    for m in _QC_RE.finditer(text):
        letters = "".join(tok.lstrip("-") for tok in m.group(1).split())
        out.append((m.start(), letters))
    return out


class TestBootUnitQuotacheck(unittest.TestCase):
    def test_unit_runs_quotacheck_without_c_between_remount_and_quotaon(self):
        unit = crg.render_quota_unit()
        lines = [ln for ln in unit.splitlines() if ln.startswith("Exec")]
        idx = {k: next((i for i, ln in enumerate(lines) if k in ln), None)
               for k in ("remount", "quotacheck", "quotaon")}
        self.assertIsNotNone(idx["quotacheck"], "unit never runs quotacheck")
        self.assertLess(idx["remount"], idx["quotacheck"])
        self.assertLess(idx["quotacheck"], idx["quotaon"])
        qc = lines[idx["quotacheck"]]
        self.assertTrue(qc.startswith("ExecStartPre=-"),
                        "a failed quotacheck must never block quotaon: %r" % qc)
        self.assertIn("quotacheck -u -m /", qc)
        self.assertIn("timeout", qc, "boot quotacheck must be bounded (Before=ssh)")

    def test_unit_never_carries_c_flag(self):
        for _pos, letters in _quotacheck_flag_sets(crg.render_quota_unit()):
            self.assertNotIn("c", letters)


class TestRefreshRenderers(unittest.TestCase):
    def test_refresh_script_quotaoff_quotacheck_quotaon_then_limits(self):
        s = crg.render_quota_refresh_script()
        i_off = s.index("quotaoff -u /")
        i_qc = s.index("quotacheck -u -m /")
        i_on = s.index("quotaon -u /", i_qc)
        i_lim = s.index("per-user usage-aware quota limits")
        self.assertLess(i_off, i_qc)
        self.assertLess(i_qc, i_on)
        self.assertLess(i_on, i_lim)

    def test_refresh_script_embeds_the_one_limits_renderer(self):
        self.assertIn(crg._render_quota_limits_block(),
                      crg.render_quota_refresh_script())

    def test_limits_math_exists_exactly_once_in_source(self):
        """ONE renderer — no second copy of the usage-aware ceiling math
        anywhere in the repo's (non-test) Python source."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        hits = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in ("tests", "worktrees", ".git", "__pycache__")]
            for fn in filenames:
                if fn.endswith(".py"):
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                        hits += [fn] * f.read().count("used_kib * 120 + 99")
        self.assertEqual(hits, ["cli_resource_guards_quota.py"])

    def test_refresh_script_has_exit_trap_reenabling_quota(self):
        s = crg.render_quota_refresh_script()
        self.assertRegex(s, r"trap '[^']*quotaon -u /[^']*' EXIT")
        # the trap is armed BEFORE quota is ever switched off
        self.assertLess(s.index("EXIT"), s.index("quotaoff -u /"))

    def test_refresh_script_never_carries_c_flag(self):
        flags = _quotacheck_flag_sets(crg.render_quota_refresh_script())
        self.assertTrue(flags)
        for _pos, letters in flags:
            self.assertNotIn("c", letters)

    def test_service_and_timer(self):
        svc = crg.render_quota_refresh_service()
        self.assertIn("Type=oneshot", svc)
        self.assertIn("ExecStart=/bin/bash %s" % crg.QUOTA_REFRESH_SCRIPT_PATH, svc)
        tmr = crg.render_quota_refresh_timer()
        self.assertIn("OnCalendar=daily", tmr)
        self.assertIn("Persistent=true", tmr)
        self.assertIn("Unit=airuleset-quota-refresh.service", tmr)
        self.assertIn("WantedBy=timers.target", tmr)

    def test_guard_files_install_refresh_trio(self):
        files = dict(crg.guard_files())
        self.assertEqual(files[crg.QUOTA_REFRESH_SCRIPT_PATH],
                         crg.render_quota_refresh_script())
        self.assertEqual(files[crg.QUOTA_REFRESH_SERVICE_PATH],
                         crg.render_quota_refresh_service())
        self.assertEqual(files[crg.QUOTA_REFRESH_TIMER_PATH],
                         crg.render_quota_refresh_timer())

    def test_refresh_service_orders_after_boot_unit_and_re_enables_on_kill(self):
        """review: a Persistent catch-up at boot must not race the boot unit's
        quotacheck, and a SIGKILL (no EXIT trap) must not leave quota off."""
        svc = crg.render_quota_refresh_service()
        self.assertIn("After=airuleset-quota.service", svc)
        self.assertIn("ExecStopPost=-/sbin/quotaon -u /", svc)

    def test_refresh_and_apply_share_one_lock(self):
        """review: a push apply must never quotaon under a running recount."""
        s = crg.render_quota_refresh_script()
        blk = crg._render_quota_apply_block()
        for text in (s, blk):
            self.assertIn(crg.QUOTA_LOCK_PATH, text)
            self.assertIn("flock -w", text)
        self.assertLess(s.index("flock -w"), s.index("quotaoff -u /"))
        self.assertLess(s.index("flock -w"), s.index("EXIT"))

    def test_refresh_quotacheck_is_best_effort_io_not_idle(self):
        """review: the idle IO class can stretch the quota-off window."""
        s = crg.render_quota_refresh_script()
        self.assertIn("ionice -c2 -n7", s)
        self.assertNotIn("ionice -c3", s)

    def test_apply_hands_the_off_recount_to_the_refresh_service(self):
        """review 3: an inline recount (up to 1800 s) would outlive the push's
        180 s ssh timeout; the apply queues the ONE refresh path instead."""
        blk = crg._render_quota_apply_block()
        self.assertIn("systemctl start --no-block airuleset-quota-refresh.service", blk)
        flags = [f for _p, f in _quotacheck_flag_sets(blk)]
        self.assertNotIn("um", flags, "the push apply must not recount inline")

    def test_stoppost_quotaon_takes_the_lock(self):
        """review 3: ExecStopPost after a busy-lock exit must never quotaon
        under the apply's running work — it takes the same lock, bounded."""
        svc = crg.render_quota_refresh_service()
        line = [ln for ln in svc.splitlines() if ln.startswith("ExecStopPost=")]
        self.assertEqual(len(line), 1, svc)
        self.assertIn("flock -w", line[0])
        self.assertIn(crg.QUOTA_LOCK_PATH, line[0])
        self.assertTrue(line[0].endswith("/sbin/quotaon -u /"), line[0])

    def test_apply_enables_timer_with_readback(self):
        blk = crg._render_quota_apply_block()
        self.assertIn("systemctl enable --now airuleset-quota-refresh.timer", blk)
        self.assertIn("systemctl is-enabled airuleset-quota-refresh.timer", blk)


class TestNoQuotacheckCreateOnExistingFile(unittest.TestCase):
    """LOCK: ``-c`` recreates the quota file WITHOUT limits (24.9 incident).
    The ONLY tolerated ``-c`` is the first-install bootstrap, directly under the
    ``[ ! -f /aquota.user ]`` guard (no file ⇒ no limits to lose)."""

    def test_every_c_flag_sits_under_the_absent_file_guard(self):
        script = crg.build_apply_script()
        lines = script.splitlines()
        seen_c = 0
        for i, ln in enumerate(lines):
            for _pos, letters in _quotacheck_flag_sets(ln):
                if "c" in letters:
                    seen_c += 1
                    self.assertIn("[ ! -f /aquota.user ]", lines[i - 1],
                                  "quotacheck -c outside the absent-file guard: %r" % ln)
        self.assertLessEqual(seen_c, 1)


class TestRefreshScriptExecution(unittest.TestCase):
    """Run the rendered refresh script under stub binaries."""

    def _setup(self, tmpdir, qoff_rc=0, qc_rc=0, qc_sleep=0, state="on",
               qon_rc=0, sq_rc=0, applied_hard=10485760):
        log = os.path.join(tmpdir, "calls.log")
        bindir = os.path.join(tmpdir, "bin")
        os.makedirs(bindir)
        home = os.path.join(tmpdir, "home", "u1")
        os.makedirs(os.path.join(home, ".claude"))
        with open(os.path.join(home, ".claude", "airuleset-box-class"), "w") as f:
            f.write("shared-stream\n")
        marker = os.path.join(tmpdir, "qc-started")
        stubs = {
            "quotaoff": 'echo "quotaoff $*" >> %s; exit %d' % (log, qoff_rc),
            "quotacheck": ('echo "quotacheck $*" >> %s; touch %s; sleep %d; exit %d'
                           % (log, marker, qc_sleep, qc_rc)),
            # `quotaon -pu /` is the read-only state probe — answered, not logged
            "quotaon": ('if [ "$1" = -pu ]; then echo "user quota on / is %s"; exit 0; fi\n'
                        'echo "quotaon $*" >> %s\n'
                        '[ %d -eq 0 ] || { echo "quotaon: some failure" >&2; exit %d; }'
                        % (state, log, qon_rc, qon_rc)),
            "setquota": 'echo "setquota $*" >> %s; exit %d' % (log, sq_rc),
            "repquota": 'echo "u1  --  1048576  8388608 %d  0 0 0"' % applied_hard,
            "systemctl": "exit 0",
            "nice": 'while [ "${1#-}" != "$1" ]; do shift; done; exec "$@"',
            "ionice": 'while [ "${1#-}" != "$1" ]; do shift; done; exec "$@"',
            "timeout": 'shift; exec "$@"',
        }
        for name, body in stubs.items():
            p = os.path.join(bindir, name)
            with open(p, "w") as f:
                f.write("#!/usr/bin/env bash\n" + body + "\n")
            os.chmod(p, 0o755)
        aq = os.path.join(tmpdir, "aquota.user")
        open(aq, "w").close()
        script = crg.render_quota_refresh_script()
        script = script.replace("/aquota.user", aq)
        script = script.replace("for home in /home/*", "for home in %s/home/*" % tmpdir)
        self.lock = os.path.join(tmpdir, "quota.lock")
        script = script.replace(crg.QUOTA_LOCK_PATH, self.lock)
        script = script.replace("flock -w %d" % crg.QUOTA_LOCK_WAIT_S, "flock -w 1")
        path = os.path.join(tmpdir, "refresh.sh")
        with open(path, "w") as f:
            f.write(script)
        env = dict(os.environ)
        env["PATH"] = bindir + ":" + env.get("PATH", "")
        return path, env, log, marker

    def _calls(self, log):
        return open(log).read().splitlines() if os.path.exists(log) else []

    def test_happy_path_order(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td)
            r = subprocess.run(["bash", path], env=env, capture_output=True,
                               text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            calls = [c.split()[0] for c in self._calls(log)]
            self.assertEqual(calls[:3], ["quotaoff", "quotacheck", "quotaon"])
            self.assertIn("setquota", calls)
            self.assertIn("quotacheck -u -m /", self._calls(log)[1])

    def test_quotacheck_failure_still_reenables_quota_and_applies_limits(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td, qc_rc=1)
            r = subprocess.run(["bash", path], env=env, capture_output=True,
                               text=True, timeout=30)
            calls = [c.split()[0] for c in self._calls(log)]
            self.assertIn("quotaon", calls)
            self.assertIn("setquota", calls)
            self.assertNotEqual(r.returncode, 0, "a failed quotacheck must be LOUD")
            self.assertIn("quotacheck", r.stderr)

    def test_quotaoff_failure_skips_quotacheck(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td, qoff_rc=1)
            r = subprocess.run(["bash", path], env=env, capture_output=True,
                               text=True, timeout=30)
            calls = [c.split()[0] for c in self._calls(log)]
            self.assertNotIn("quotacheck", calls)
            self.assertIn("quotaon", calls)
            self.assertNotEqual(r.returncode, 0)

    def test_quota_already_off_is_recounted_without_quotaoff(self):
        """Boot unit failed → quota OFF: the recount is needed most, and
        quotaoff would fail there — never let that skip the quotacheck."""
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td, qoff_rc=1, state="off")
            r = subprocess.run(["bash", path], env=env, capture_output=True,
                               text=True, timeout=30)
            calls = [c.split()[0] for c in self._calls(log)]
            self.assertEqual(calls[:2], ["quotacheck", "quotaon"], calls)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_sigterm_mid_quotacheck_still_reenables_quota(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log, marker = self._setup(td, qc_sleep=2)
            # a parent that ignores SIGTERM (a push-gate environment) would make
            # bash unable to trap it — reset it for the child (internals-tests).
            p = subprocess.Popen(
                ["bash", path], env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
                preexec_fn=lambda: signal.signal(signal.SIGTERM, signal.SIG_DFL))
            deadline = time.time() + 15
            while not os.path.exists(marker) and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(os.path.exists(marker), "quotacheck stub never started")
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=30)
            calls = [c.split()[0] for c in self._calls(log)]
            self.assertIn("quotaon", calls,
                          "SIGTERM mid-refresh left quota OFF: %r" % calls)
            self.assertNotEqual(p.returncode, 0)

    def _run(self, path, env):
        return subprocess.run(["bash", path], env=env, capture_output=True,
                              text=True, timeout=30)

    def test_lock_held_touches_nothing_and_is_loud(self):
        import fcntl
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td)
            with open(self.lock, "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                r = self._run(path, env)
            self.assertEqual(self._calls(log), [], "ran under a held quota lock")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("lock", r.stderr)

    def test_quotaon_failure_is_loud(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td, qon_rc=1)
            r = self._run(path, env)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_setquota_failure_is_loud(self):
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td, sq_rc=1)
            r = self._run(path, env)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_skipped_user_is_loud_even_when_the_readback_passes(self):
        """repquota fails for the APPLY pass, then works for the read-back: no
        ceiling was refreshed, and the old limits must not read as success."""
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td)
            n = os.path.join(td, "rq.count")
            with open(os.path.join(td, "bin", "repquota"), "w") as f:
                f.write("#!/usr/bin/env bash\n"
                        "c=$(cat %s 2>/dev/null || echo 0); echo $((c+1)) > %s\n"
                        "[ \"$c\" -eq 0 ] && { echo boom >&2; exit 1; }\n"
                        "echo 'u1  --  1048576  8388608 10485760  0 0 0'\n" % (n, n))
            r = self._run(path, env)
            self.assertNotIn("setquota -u", " ".join(self._calls(log)))
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_readback_of_a_stale_ceiling_is_loud(self):
        """the applied hard limit must equal the one computed, not merely be set."""
        with tempfile.TemporaryDirectory() as td:
            path, env, log, _m = self._setup(td, applied_hard=9000000)
            r = self._run(path, env)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("QUOTA VERIFY FAIL", r.stderr)

    def test_bash_syntax(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.sh")
            with open(p, "w") as f:
                f.write(crg.render_quota_refresh_script())
            r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


class TestApplyBlockExecution(unittest.TestCase):
    """The push-time apply block: shares the refresh lock; recounts when off."""

    def _run(self, td, state, hold_lock=False):
        import fcntl
        log = os.path.join(td, "calls.log")
        bindir = os.path.join(td, "bin")
        os.makedirs(bindir)
        home = os.path.join(td, "home", "u1", ".claude")
        os.makedirs(home)
        with open(os.path.join(home, "airuleset-box-class"), "w") as f:
            f.write("shared-stream\n")
        stubs = {
            "findmnt": 'case "$2" in SOURCE) echo /dev/sda1;; FSTYPE) echo ext4;; esac',
            "modprobe": "exit 0",
            "mount": 'echo "mount $*" >> %s' % log,
            "quotacheck": 'echo "quotacheck $*" >> %s' % log,
            "quotaon": ('if [ "$1" = -pu ]; then echo "user quota on / is %s"; exit 0; fi\n'
                        'echo "quotaon $*" >> %s' % (state, log)),
            "setquota": 'echo "setquota $*" >> %s' % log,
            "repquota": 'echo "u1  --  1048576  8388608 10485760  0 0 0"',
            "systemctl": ('echo "systemctl $*" >> %s\n'
                          'case "$1" in is-enabled) echo enabled;; esac' % log),
            "apt-get": "exit 0",
        }
        for name, body in stubs.items():
            fp = os.path.join(bindir, name)
            with open(fp, "w") as f:
                f.write("#!/usr/bin/env bash\n" + body + "\n")
            os.chmod(fp, 0o755)
        aq = os.path.join(td, "aquota.user")
        open(aq, "w").close()
        lock = os.path.join(td, "quota.lock")
        blk = crg._render_quota_apply_block()
        blk = blk.replace("/etc/modules-load.d/airuleset-quota.conf",
                          os.path.join(td, "mod.conf"))
        blk = blk.replace("for home in /home/*", "for home in %s/home/*" % td)
        blk = blk.replace("/aquota.user", aq).replace(crg.QUOTA_LOCK_PATH, lock)
        blk = blk.replace("flock -w %d" % crg.QUOTA_APPLY_LOCK_WAIT_S, "flock -w 1")
        path = os.path.join(td, "apply.sh")
        with open(path, "w") as f:
            f.write("#!/usr/bin/env bash\nset -euo pipefail\n" + blk + "\n"
                    '[ -e /proc/$$/fd/9 ] || echo FD9-CLOSED\n')
        env = dict(os.environ)
        env["PATH"] = bindir + ":" + env.get("PATH", "")
        with open(lock, "w") as held:
            if hold_lock:
                fcntl.flock(held, fcntl.LOCK_EX)
            r = subprocess.run(["bash", path], env=env, capture_output=True,
                               text=True, timeout=30)
        calls = open(log).read().splitlines() if os.path.exists(log) else []
        return r, calls

    def test_quota_off_queues_the_refresh_after_releasing_the_lock(self):
        with tempfile.TemporaryDirectory() as td:
            r, calls = self._run(td, "off")
            self.assertNotIn("quotacheck -u -m /", calls, "inline recount in a push")
            self.assertIn("systemctl start --no-block airuleset-quota-refresh.service",
                          calls, r.stdout + r.stderr)
            self.assertIn("FD9-CLOSED", r.stdout, "the quota lock fd leaked past the block")

    def test_quota_on_does_not_queue_a_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            r, calls = self._run(td, "on")
            self.assertFalse([c for c in calls if "start --no-block" in c], calls)
            self.assertIn("FD9-CLOSED", r.stdout)

    def test_held_lock_skips_the_quota_section_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            r, calls = self._run(td, "on", hold_lock=True)
            self.assertEqual([c for c in calls if c.startswith("setquota")], [],
                             "applied limits under a held quota lock")
            self.assertIn("lock", r.stderr)


if __name__ == "__main__":
    unittest.main()
