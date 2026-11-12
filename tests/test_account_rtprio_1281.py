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
import cli_account_verify as verify  # noqa: E402


class TestValidation(unittest.TestCase):

    def test_range_edges(self):
        self.assertEqual(policy.validate_rtprio({"rtprio": 1}), [])
        self.assertEqual(policy.validate_rtprio({"rtprio": 49}), [])
        self.assertEqual(policy.validate_rtprio({}), [])

    def test_refused(self):
        for bad in (0, 50, 89, 99, -1, "50", True, 50.0, None):
            self.assertTrue(policy.validate_rtprio({"rtprio": bad}), repr(bad))

    def test_validate_account_uses_it(self):
        raw = dict(bootstrap.SERVICE_ACCOUNTS["dstack"], rtprio=95)
        self.assertTrue([e for e in bootstrap.validate_account("dstack", raw) if "rtprio" in e])


class TestDeclaration(unittest.TestCase):

    def test_dstack_declares_49_and_only_the_dante_nmcli_grant(self):
        # owner 10.11.2026: the one sudo grant is nmcli on dante-net / dante-vlan
        spec = bootstrap.account_spec("dstack")
        self.assertEqual(spec["rtprio"], 49)
        self.assertIs(spec["sudo"], True)
        # #1308 (owner 12.11.2026) adds the dante_guard nft + avahi lines,
        # pinned in test_dstack_dante_guard_sudo_1308; every OTHER line is nmcli.
        for cmd in spec["sudo_commands"]:
            if cmd.startswith(("/usr/sbin/nft ", "/usr/bin/systemctl ")):
                continue
            words = cmd.split(" ")
            self.assertEqual(words[:2], ["/usr/bin/nmcli", "con"], cmd)
            name = words[words.index("con-name") + 1] if words[2] == "add" else words[3]
            self.assertIn(name, ("dante-net", "dante-vlan"), cmd)
        self.assertEqual(bootstrap.validate_account("dstack", spec), [])


class TestRender(unittest.TestCase):

    def test_absent_key_removes_a_stale_file(self):
        step = hardening.render_rtprio_step("dasio", bootstrap.account_spec("dasio"))
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp, "90-dasio-rtprio.conf")
            stale.write_text("dasio - rtprio 49\n")
            script = "set -euo pipefail\nACCOUNT=dasio\n" + step.replace(hardening.LIMITS_DIR, tmp)
            subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
            self.assertFalse(stale.exists())
            subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)

    def test_a_string_never_reaches_the_script(self):
        with self.assertRaises(TypeError):
            hardening.render_rtprio_step("dstack", {"rtprio": "1; id"})

    def test_header_names_the_limit(self):
        self.assertIn("rtprio=49", bootstrap.render_root_bootstrap("dstack"))

    def test_rendered_step_writes_the_limit_line(self):
        step = hardening.render_rtprio_step("dstack", {"rtprio": 49})
        with tempfile.TemporaryDirectory() as tmp:
            script = "set -euo pipefail\nACCOUNT=dstack\n" + step.replace(hardening.LIMITS_DIR, tmp)
            subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
            written = Path(tmp, "90-dstack-rtprio.conf")
            self.assertEqual(written.read_text(), "dstack - rtprio 49\n")
            self.assertEqual(written.stat().st_mode & 0o777, 0o644)

    def test_step_sits_in_the_full_script_before_the_keys(self):
        script = bootstrap.render_root_bootstrap("dstack")
        self.assertLess(script.index("# 3c"), script.index("# 3d-rt."))
        self.assertLess(script.index("# 3d-rt."), script.index("/.ssh/authorized_keys"))


class TestVerify(unittest.TestCase):

    def test_declared_limit_must_match_the_login(self):
        probe = {"rtprio": {"hard": ["49"]}}
        self.assertTrue(verify._check_rtprio({"rtprio": 49}, probe)[0])
        self.assertFalse(verify._check_rtprio({"rtprio": 49}, {"rtprio": {"hard": ["0"]}})[0])
        self.assertFalse(verify._check_rtprio({}, probe)[0])
        self.assertFalse(verify._check_rtprio({"rtprio": 49}, {})[0])


if __name__ == "__main__":
    unittest.main()
