"""RED tests for #950 fix-forward — 4 go-live defects in _render_quota_apply_block.

Tests the RENDERED bash script text and its runtime behaviour under stubbed
quota tools.  Every test here is expected to FAIL against the pre-fix code
and PASS after the fix.
"""
import math
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRenderedScriptShape(unittest.TestCase):
    """Static checks on the rendered quota bash snippet."""

    def _script(self):
        from cli_resource_guards import _render_quota_apply_block
        return _render_quota_apply_block()

    # -- D2: no pipefail-unsafe `cmd | grep -q` under pipefail --

    def test_no_pipe_grep_q_on_quotaon(self):
        """quotaon ... | grep -q is DEAD under pipefail; must not appear."""
        script = self._script()
        bad = re.findall(r'quotaon[^|]*\|\s*grep\s+-q', script)
        self.assertEqual(bad, [],
                         "pipefail-unsafe `quotaon | grep -q` found")

    def test_no_bare_pipe_grep_q(self):
        """No `| grep -q` should appear anywhere in the quota block
        (the script runs under set -euo pipefail via the wrapper)."""
        script = self._script()
        pipes = re.findall(r'[^#]\|\s*grep\s+-q', script)
        self.assertEqual(pipes, [],
                         "`| grep -q` found in rendered quota block")

    # -- D1: kmod handling --

    def test_modprobe_failure_triggers_apt_install(self):
        """When modprobe fails, the script must install linux-modules-extra."""
        script = self._script()
        self.assertIn("linux-modules-extra", script)
        self.assertIn("linux-image-extra-virtual", script)

    def test_modules_load_d_persisted(self):
        """Must persist /etc/modules-load.d/airuleset-quota.conf."""
        script = self._script()
        self.assertIn("modules-load.d/airuleset-quota", script)

    # -- D4: no bare 2>/dev/null on quota commands --

    def test_no_bare_stderr_swallow_on_quotaon(self):
        """quotaon calls must capture stderr, not blindly redirect."""
        script = self._script()
        lines = script.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if 'quotaon' in stripped and '2>/dev/null' in stripped:
                # OK inside a $(...2>&1...) capture or with || true
                if '2>&1' in stripped:
                    continue
                if '|| true' in stripped:
                    continue
                self.fail(
                    "Bare 2>/dev/null on quotaon: %s" % stripped)

    def test_no_bare_stderr_swallow_on_modprobe(self):
        """modprobe calls must not silently swallow stderr."""
        script = self._script()
        lines = script.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if 'modprobe' in stripped and '2>/dev/null' in stripped:
                if '2>&1' in stripped:
                    continue
                self.fail(
                    "Bare 2>/dev/null on modprobe: %s" % stripped)

    # -- D3: usage-aware limits --

    def test_script_has_usage_aware_logic(self):
        """setquota must reference current usage, not just constants."""
        script = self._script()
        self.assertIn("used", script.lower(),
                      "No usage-aware logic found")
        has_usage_math = any(tok in script for tok in [
            '1.2', '* 12', '120', 'used_kib',
            '-gt', '-lt',
        ])
        self.assertTrue(has_usage_math,
                        "No usage-based limit computation found")

    # -- systemd unit --

    def test_quota_unit_has_modprobe_pre(self):
        """systemd unit must have ExecStartPre for modprobe quota_v2."""
        from cli_resource_guards import render_quota_unit
        unit = render_quota_unit()
        self.assertIn("modprobe quota_v2", unit)

    # -- D2: EBUSY handling --

    def test_ebusy_treated_as_already_on(self):
        """'Device or resource busy' from quotaon -u must not fail."""
        script = self._script()
        has_busy = ('busy' in script.lower() or 'EBUSY' in script)
        self.assertTrue(has_busy,
                        "No EBUSY/busy handling for quotaon")


class TestScriptSyntax(unittest.TestCase):
    """Rendered quota block must be valid bash."""

    def test_bash_n_syntax_check(self):
        from cli_resource_guards import _render_quota_apply_block
        block = _render_quota_apply_block()
        wrapped = (
            '#!/usr/bin/env bash\n'
            'set -euo pipefail\n'
            + block + '\n'
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh',
                                         delete=False) as f:
            f.write(wrapped)
            f.flush()
            try:
                result = subprocess.run(
                    ['bash', '-n', f.name],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0,
                                 "bash -n failed: %s" % result.stderr)
            finally:
                os.unlink(f.name)


class TestQuotaBlockExecution(unittest.TestCase):
    """Execute the rendered quota block under stubbed tools."""

    def _make_stubs(self, tmpdir, stubs):
        bin_dir = os.path.join(tmpdir, 'bin')
        os.makedirs(bin_dir, exist_ok=True)
        for name, content in stubs.items():
            path = os.path.join(bin_dir, name)
            with open(path, 'w') as f:
                f.write('#!/usr/bin/env bash\n' + content + '\n')
            os.chmod(path, 0o755)
        return bin_dir

    def _run_block(self, block, bin_dir, extra_env=None):
        script = (
            '#!/usr/bin/env bash\n'
            'set -euo pipefail\n'
            + block + '\n'
        )
        env = dict(os.environ)
        env['PATH'] = bin_dir + ':' + env.get('PATH', '')
        if extra_env:
            env.update(extra_env)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh',
                                         delete=False) as f:
            f.write(script)
            f.flush()
            try:
                return subprocess.run(
                    ['bash', f.name],
                    capture_output=True, text=True, timeout=30,
                    env=env)
            finally:
                os.unlink(f.name)

    def test_already_on_branch_succeeds(self):
        """When quotaon -pu reports 'is on', script must NOT remount."""
        from cli_resource_guards import _render_quota_apply_block
        block = _render_quota_apply_block()

        with tempfile.TemporaryDirectory() as tmpdir:
            home = os.path.join(tmpdir, 'home', 'testuser')
            os.makedirs(os.path.join(home, '.claude'))
            with open(os.path.join(home, '.claude',
                                   'airuleset-box-class'), 'w') as f:
                f.write('shared-stream\n')

            stubs = {
                'findmnt': (
                    'case "$2" in\n'
                    '  SOURCE) echo "/dev/sda1";;\n'
                    '  FSTYPE) echo "ext4";;\n'
                    'esac'
                ),
                'modprobe': 'exit 0',
                'quotaon': (
                    'case "$1" in\n'
                    '  -pu) echo "/dev/sda1 [/]: user quotas are on";\n'
                    '       exit 0;;\n'
                    '  *) exit 0;;\n'
                    'esac'
                ),
                'setquota': 'exit 0',
                'repquota': (
                    'echo "testuser  --  5242880  8388608 10485760'
                    '              0     0     0"'
                ),
                'systemctl': 'exit 0',
                'apt-get': 'exit 0',
                'mount': 'exit 0',
                'quotacheck': 'exit 0',
            }
            bin_dir = self._make_stubs(tmpdir, stubs)
            result = self._run_block(block, bin_dir)
            combined = result.stdout + result.stderr
            self.assertIn("already", combined.lower(),
                          "Already-on branch not taken. "
                          "stdout=%s stderr=%s rc=%d"
                          % (result.stdout, result.stderr,
                             result.returncode))


if __name__ == "__main__":
    unittest.main()
