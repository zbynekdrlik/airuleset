"""#1281: a project account may declare a real-time priority ceiling, and the
bootstrap writes exactly that pam_limits line. Absent key: nothing rendered."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_hardening as hardening  # noqa: E402
import cli_account_policy as policy  # noqa: E402


class TestValidation(unittest.TestCase):

    def test_range_edges(self):
        self.assertEqual(policy.validate_rtprio({"rtprio": 1}), [])
        self.assertEqual(policy.validate_rtprio({"rtprio": 89}), [])
        self.assertEqual(policy.validate_rtprio({}), [])

    def test_refused(self):
        for bad in (0, 90, 99, -1, "50", True, 50.0, None):
            self.assertTrue(policy.validate_rtprio({"rtprio": bad}), repr(bad))

    def test_validate_account_uses_it(self):
        raw = dict(bootstrap.SERVICE_ACCOUNTS["dstack"], rtprio=95)
        self.assertTrue([e for e in bootstrap.validate_account("dstack", raw) if "rtprio" in e])


class TestDeclaration(unittest.TestCase):

    def test_dstack_declares_50_and_no_sudo(self):
        spec = bootstrap.account_spec("dstack")
        self.assertEqual(spec["rtprio"], 50)
        self.assertIs(spec["sudo"], False)
        self.assertEqual(bootstrap.validate_account("dstack", spec), [])

    def test_only_dstack_declares_it(self):
        self.assertEqual([a for a, raw in bootstrap.SERVICE_ACCOUNTS.items() if "rtprio" in raw],
                         ["dstack"])


class TestRender(unittest.TestCase):

    def test_absent_key_renders_nothing(self):
        self.assertEqual(hardening.render_rtprio_step("dasio", bootstrap.account_spec("dasio")), "")

    def test_rendered_step_writes_the_limit_line(self):
        step = hardening.render_rtprio_step("dstack", {"rtprio": 50})
        with tempfile.TemporaryDirectory() as tmp:
            script = "set -euo pipefail\nACCOUNT=dstack\n" + step.replace(hardening.LIMITS_DIR, tmp)
            subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
            written = Path(tmp, "90-dstack-rtprio.conf")
            self.assertEqual(written.read_text(), "dstack - rtprio 50\n")
            self.assertEqual(written.stat().st_mode & 0o777, 0o644)

    def test_step_sits_in_the_full_script_before_the_keys(self):
        script = bootstrap.render_root_bootstrap("dstack")
        self.assertLess(script.index("# 3a-rt."), script.index("# 3b"))
        self.assertLess(script.index("# 3a-rt."), script.index("/.ssh/authorized_keys"))


if __name__ == "__main__":
    unittest.main()
