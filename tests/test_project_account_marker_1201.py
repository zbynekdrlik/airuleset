"""#1201 follow-up: a "project account" is declared AND bootstrapped.

``SERVICE_ACCOUNTS`` membership alone is not enough. A declared account that
the root render has not adopted (claudy@controller, which manages fleet OAuth;
camera-box@dev1 before its migration) keeps the legacy behaviour. Push 0.1.517
failed claudy's Playwright postcheck because membership alone refused its
per-user chromium. The marker ``/etc/airuleset/project-accounts/<account>`` is
what the root render writes when it adopts the account.
"""
import json
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import airuleset  # noqa: E402,F401  (facade import order, as the 1199 tests)
import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_hardening as hardening  # noqa: E402
import cli_playwright_mcp as pw  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402
import cli_project_toolchain as tc  # noqa: E402
import _project_markers as markers  # noqa: E402

HOOK = ROOT / "hooks" / "block-project-account-local-installs.sh"
ME = pwd.getpwuid(os.getuid()).pw_name


class TestPredicate(unittest.TestCase):

    def test_declared_and_bootstrapped_is_a_project_account(self):
        d = markers.seed(self, ["fohmixer"])
        self.assertTrue(tc.is_project_account("fohmixer"))
        self.assertTrue(tc.is_project_account("fohmixer", marker_dir=d))

    def test_declared_without_its_marker_is_not(self):
        markers.seed(self, [])
        for acct in bootstrap.SERVICE_ACCOUNTS:
            self.assertFalse(tc.is_project_account(acct), acct)

    def test_a_stray_marker_never_makes_an_undeclared_account_one(self):
        markers.seed(self, ["newlevel", "montalu1"])
        self.assertFalse(tc.is_project_account("newlevel"))
        self.assertFalse(tc.is_project_account("montalu1"))

    def test_the_default_dir_is_the_root_render_marker_dir(self):
        self.assertEqual(hardening.MARKER_DIR, "/etc/airuleset/project-accounts")


class TestPlaywrightRefusal(unittest.TestCase):
    """The per-user chromium refusal follows the marker."""

    def test_a_declared_legacy_account_is_not_refused(self):
        markers.seed(self, [])
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {ME: {}}):
            self.assertFalse(tc.runs_as_project_account())
            self.assertFalse(pw._is_project_account())

    def test_a_bootstrapped_account_is_refused(self):
        markers.seed(self, [ME])
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {ME: {}}):
            self.assertTrue(pw._is_project_account())


class TestGkRequest(unittest.TestCase):
    """gk-request and the disk-guard filer follow the marker."""

    def test_claudy_without_its_marker_keeps_the_legacy_relay(self):
        markers.seed(self, [])
        self.assertFalse(pgt.is_project_account("claudy", {}))
        self.assertIsNone(pgt.gk_request_refusal("claudy", {}))

    def test_a_bootstrapped_account_refuses(self):
        markers.seed(self, ["fohmixer"])
        self.assertTrue(pgt.is_project_account("fohmixer", {}))
        self.assertIn("gh issue create -R zbynekdrlik/airuleset",
                      pgt.gk_request_refusal("fohmixer", {}))


def run_hook(cmd, user, marker_names):
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "id"
        stub.write_text("#!/usr/bin/env bash\necho %s\n" % user)
        stub.chmod(0o755)
        mdir = Path(tmp) / "markers"
        mdir.mkdir()
        markers.write_markers(str(mdir), marker_names)
        return subprocess.run(
            ["bash", str(HOOK)], input=json.dumps({"tool_input": {"command": cmd}}),
            text=True, capture_output=True,
            env={"PATH": "%s:/usr/bin:/bin" % tmp, "HOME": tmp,
                 "PYTEST_CURRENT_TEST": "marker", markers.ENV: str(mdir)})


class TestInstallHook(unittest.TestCase):

    def test_a_declared_legacy_account_may_install(self):
        r = run_hook("cargo install x", "claudy", [])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_bootstrapped_account_is_blocked(self):
        r = run_hook("cargo install x", "claudy", ["claudy"])
        self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main()
