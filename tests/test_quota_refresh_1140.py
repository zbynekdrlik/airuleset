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
        """ONE renderer — no second copy of the usage-aware ceiling math."""
        src = open(crg.__file__, encoding="utf-8").read()
        self.assertEqual(src.count("used_kib * 120 + 99"), 1)

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

    def _setup(self, tmpdir, qoff_rc=0, qc_rc=0, qc_sleep=0):
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
            "quotaon": 'echo "quotaon $*" >> %s; exit 0' % log,
            "setquota": 'echo "setquota $*" >> %s; exit 0' % log,
            "repquota": 'echo "u1  --  1048576  8388608 10485760  0 0 0"',
            "systemctl": "exit 0",
            "nice": 'shift; exec "$@"',
            "ionice": 'shift; exec "$@"',
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

    def test_bash_syntax(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.sh")
            with open(p, "w") as f:
                f.write(crg.render_quota_refresh_script())
            r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
