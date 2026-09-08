"""RED tests for #950 fix-forward — 4 go-live defects in _render_quota_apply_block.

Tests the RENDERED bash script text and its runtime behaviour under stubbed
quota tools.  Every test here is expected to FAIL against the pre-fix code
and PASS after the fix.
"""
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
        """No non-comment `| grep -q` on quota commands in the block
        (the script runs under set -euo pipefail via the wrapper)."""
        script = self._script()
        for line in script.split('\n'):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if re.search(r'\|\s*grep\s+-q', stripped):
                # grep -q on airuleset-box-class is fine (pipefail safe:
                # the file exists and is small, so grep exit = the result)
                if 'shared-stream' in stripped:
                    continue
                self.fail(
                    "pipefail-unsafe `| grep -q` on a quota command: %s"
                    % stripped)

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
                # OK inside a $(...2>&1...) capture
                if '2>&1' in stripped:
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

    def _patch_script_for_test(self, block, tmpdir):
        """Replace /etc/modules-load.d and /home/* with writable temp
        paths so the script runs without root."""
        fake_etc = os.path.join(tmpdir, 'etc', 'modules-load.d')
        os.makedirs(fake_etc, exist_ok=True)
        patched = block.replace(
            '/etc/modules-load.d/airuleset-quota.conf',
            os.path.join(fake_etc, 'airuleset-quota.conf'))
        patched = patched.replace(
            'for home in /home/*',
            'for home in %s/home/*' % tmpdir)
        return patched

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

            patched = self._patch_script_for_test(block, tmpdir)

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
            result = self._run_block(patched, bin_dir)
            combined = result.stdout + result.stderr
            self.assertIn("already", combined.lower(),
                          "Already-on branch not taken. "
                          "stdout=%s stderr=%s rc=%d"
                          % (result.stdout, result.stderr,
                             result.returncode))

    def test_usage_aware_above_target(self):
        """When usage exceeds QUOTA_HARD_KIB, setquota must get a raised
        ceiling (not the bare constant) and warn about drain."""
        from cli_resource_guards import _render_quota_apply_block
        block = _render_quota_apply_block()

        with tempfile.TemporaryDirectory() as tmpdir:
            home = os.path.join(tmpdir, 'home', 'biguser')
            os.makedirs(os.path.join(home, '.claude'))
            with open(os.path.join(home, '.claude',
                                   'airuleset-box-class'), 'w') as f:
                f.write('shared-stream\n')

            patched = self._patch_script_for_test(block, tmpdir)
            used_kib = 20971520  # 20G
            call_log = os.path.join(tmpdir, 'sq.log')
            expected_hard = (used_kib * 120 + 99) // 100
            expected_soft = (used_kib * 110 + 99) // 100

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
                'setquota': (
                    'echo "SETQUOTA: $*" >> "%s"\nexit 0' % call_log
                ),
                'repquota': (
                    'echo "biguser   -- %d  %d %d'
                    '              0     0     0"'
                    % (used_kib, expected_soft, expected_hard)
                ),
                'systemctl': 'exit 0',
                'apt-get': 'exit 0',
                'mount': 'exit 0',
                'quotacheck': 'exit 0',
            }
            bin_dir = self._make_stubs(tmpdir, stubs)
            result = self._run_block(patched, bin_dir)
            combined = result.stdout + result.stderr
            # Must warn about above-target usage
            self.assertIn("drain required", combined,
                          "No drain-required warning. "
                          "stdout=%s stderr=%s"
                          % (result.stdout, result.stderr))
            # R1: assert actual setquota arguments
            self.assertTrue(os.path.exists(call_log),
                            "setquota was never called")
            calls = open(call_log).read()
            # Must contain the user-specific per-user call
            self.assertIn("-u biguser", calls,
                          "setquota not called for biguser")
            # The hard limit must be the usage-aware value, not the constant
            expected_sq = "-u biguser %d %d 0 0" % (
                expected_soft, expected_hard)
            self.assertIn(expected_sq, calls,
                          "setquota did not use usage-aware limits. "
                          "Expected: %s, Got: %s"
                          % (expected_sq, calls))

    def test_usage_aware_zero_usage_gets_target(self):
        """When usage is zero (new account), setquota must apply the
        fleet target constants."""
        from cli_resource_guards import (_render_quota_apply_block,
                                         QUOTA_HARD_KIB, QUOTA_SOFT_KIB)
        block = _render_quota_apply_block()

        with tempfile.TemporaryDirectory() as tmpdir:
            home = os.path.join(tmpdir, 'home', 'newuser')
            os.makedirs(os.path.join(home, '.claude'))
            with open(os.path.join(home, '.claude',
                                   'airuleset-box-class'), 'w') as f:
                f.write('shared-stream\n')

            patched = self._patch_script_for_test(block, tmpdir)
            call_log = os.path.join(tmpdir, 'sq.log')

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
                'setquota': (
                    'echo "SETQUOTA: $*" >> "%s"\nexit 0' % call_log
                ),
                'repquota': (
                    'echo "newuser   --  0  %d %d'
                    '              0     0     0"'
                    % (QUOTA_SOFT_KIB, QUOTA_HARD_KIB)
                ),
                'systemctl': 'exit 0',
                'apt-get': 'exit 0',
                'mount': 'exit 0',
                'quotacheck': 'exit 0',
            }
            bin_dir = self._make_stubs(tmpdir, stubs)
            result = self._run_block(patched, bin_dir)
            # Must NOT warn about drain (usage is zero)
            combined = result.stdout + result.stderr
            self.assertNotIn("drain required", combined,
                             "False drain-required for zero-usage user")
            # Must apply fleet targets
            self.assertTrue(os.path.exists(call_log),
                            "setquota was never called")
            calls = open(call_log).read()
            expected_sq = "-u newuser %d %d 0 0" % (
                QUOTA_SOFT_KIB, QUOTA_HARD_KIB)
            self.assertIn(expected_sq, calls,
                          "setquota did not use fleet targets for zero-usage. "
                          "Expected: %s, Got: %s"
                          % (expected_sq, calls))


    # -- Y6: repquota failure must never be treated as zero usage --

    def test_repquota_failure_skips_user(self):
        """When repquota exits non-zero, the script must warn LOUDLY and
        skip setquota for that user rather than assuming 0 usage."""
        from cli_resource_guards import _render_quota_apply_block
        block = _render_quota_apply_block()

        with tempfile.TemporaryDirectory() as tmpdir:
            home = os.path.join(tmpdir, 'home', 'testuser')
            os.makedirs(os.path.join(home, '.claude'))
            with open(os.path.join(home, '.claude',
                                   'airuleset-box-class'), 'w') as f:
                f.write('shared-stream\n')

            patched = self._patch_script_for_test(block, tmpdir)
            call_log = os.path.join(tmpdir, 'sq.log')

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
                'setquota': (
                    'echo "SETQUOTA: $*" >> "%s"\nexit 0' % call_log
                ),
                'repquota': 'echo "repquota: cannot find /" >&2\nexit 1',
                'systemctl': 'exit 0',
                'apt-get': 'exit 0',
                'mount': 'exit 0',
                'quotacheck': 'exit 0',
            }
            bin_dir = self._make_stubs(tmpdir, stubs)
            result = self._run_block(patched, bin_dir)
            combined = result.stdout + result.stderr
            self.assertIn(
                "repquota gave no usage for testuser", combined,
                "No loud repquota-failure warning. stdout=%s stderr=%s"
                % (result.stdout, result.stderr))
            self.assertIn("skipping setquota for testuser", combined)
            # Per-user setquota must NEVER be called for this user
            if os.path.exists(call_log):
                calls = open(call_log).read()
                self.assertNotIn(
                    "-u testuser", calls,
                    "setquota was called for testuser despite repquota"
                    " failure. Got: %s" % calls)


class TestKmodBlockExecution(unittest.TestCase):
    """#950 Y3: modprobe-fail must trigger the apt-get fallback, then retry
    modprobe -- executed under stubbed tools, not just static-checked."""

    def test_modprobe_failure_triggers_install_then_retries(self):
        from cli_resource_guards import _render_quota_kmod_block
        block = _render_quota_kmod_block()

        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = os.path.join(tmpdir, 'bin')
            os.makedirs(bin_dir)
            counter = os.path.join(tmpdir, 'modprobe.count')
            apt_log = os.path.join(tmpdir, 'apt.log')
            load_d = os.path.join(tmpdir, 'etc', 'modules-load.d')
            os.makedirs(load_d)

            def stub(name, content):
                path = os.path.join(bin_dir, name)
                with open(path, 'w') as f:
                    f.write('#!/usr/bin/env bash\n' + content + '\n')
                os.chmod(path, 0o755)

            stub('uname', 'echo "5.15.0-generic"')
            # Fails on call 1 (recorded via a counter file), succeeds on call 2
            stub('modprobe', (
                'n=$(cat "%s" 2>/dev/null || echo 0); n=$((n+1))\n'
                'echo "$n" > "%s"\n'
                '[ "$n" -ge 2 ]' % (counter, counter)
            ))
            stub('apt-get', 'echo "APT: $*" >> "%s"\nexit 0' % apt_log)

            patched = block.replace(
                '/etc/modules-load.d/airuleset-quota.conf',
                os.path.join(load_d, 'airuleset-quota.conf'))
            script = '#!/usr/bin/env bash\nset -euo pipefail\n' + patched + '\n'
            env = dict(os.environ)
            env['PATH'] = bin_dir + ':' + env.get('PATH', '')
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sh',
                                             delete=False) as f:
                f.write(script)
                f.flush()
                try:
                    result = subprocess.run(
                        ['bash', f.name], capture_output=True, text=True,
                        timeout=10, env=env)
                finally:
                    os.unlink(f.name)

            self.assertEqual(
                result.returncode, 0,
                "kmod block failed: stdout=%s stderr=%s"
                % (result.stdout, result.stderr))
            self.assertTrue(os.path.exists(counter), "modprobe never ran")
            self.assertEqual(
                open(counter).read().strip(), "2",
                "modprobe must run exactly twice (initial fail, retry)")
            self.assertTrue(os.path.exists(apt_log), "apt-get never called")
            apt_calls = open(apt_log).read()
            self.assertIn("linux-modules-extra-5.15.0-generic", apt_calls)
            self.assertIn("linux-image-extra-virtual", apt_calls)


if __name__ == "__main__":
    unittest.main()
