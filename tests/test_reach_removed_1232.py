"""#1232 — the project-account ssh reach enforcement is removed (owner ruling
14.10.2026: a network block is routed around through any declared host and
pushes a session to riskier detours).

Every root render now TEARS DOWN what an earlier render installed: the
per-uid ``inet airuleset_reach_<acct>`` nft table, its
``airuleset-reach-<acct>.service`` unit and ``/etc/airuleset/reach-<acct>.nft``.
A ``reach`` declaration is an unknown key, so stale data fails loudly.
"""
import io
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_hardening as hardening  # noqa: E402
import cli_accounts as accounts  # noqa: E402
import cli_webterm_only as wo  # noqa: E402

_FAKE_PUBKEY = "ssh-ed25519 " + "A" * 68 + " lane@test"


def _render(account):
    with mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS,
                         {"timo": _FAKE_PUBKEY}):
        return bootstrap.render_root_bootstrap(account)


def _step_3b(script):
    return script[script.index("# 3b."):script.index("# 3c.")]


class TestNoReachRule(unittest.TestCase):
    def test_no_account_renders_an_egress_rule(self):
        for account in bootstrap.SERVICE_ACCOUNTS:
            script = _render(account)
            self.assertNotIn("meta skuid", script, account)
            self.assertNotIn("REACH_EOF", script, account)
            self.assertNotIn("reach=", script, account)

    def test_every_account_renders_the_teardown(self):
        for account in bootstrap.SERVICE_ACCOUNTS:
            step = _step_3b(_render(account))
            self.assertIn("'%s'" % hardening.nft_table_name(account), step, account)
            self.assertIn("airuleset-reach-$ACCOUNT.service", step, account)
            self.assertIn("/etc/airuleset/reach-$ACCOUNT.nft", step, account)
        # the table name maps a dash to an underscore; pin the literal, or a
        # broken mapping would leave camera-box's real table in place
        self.assertIn("REACH_TABLE='airuleset_reach_camera_box'",
                      _step_3b(_render("camera-box")))

    def test_reach_is_an_unknown_key(self):
        spec = dict(bootstrap.SERVICE_ACCOUNTS["fohmixer"])
        for key, value in (("reach", ["dev2"]), ("reach_enforced", True),
                           ("reach_reason", "x")):
            errs = bootstrap.validate_account("fohmixer", {**spec, key: value})
            self.assertTrue(any(key in e for e in errs), (key, errs))

    def test_no_declaration_carries_reach(self):
        for account, spec in bootstrap.SERVICE_ACCOUNTS.items():
            self.assertFalse({"reach", "reach_enforced", "reach_reason"} & set(spec),
                             account)

    def test_status_shows_no_reach(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = accounts.cmd_accounts(mock.Mock(action="status", json=False,
                                                 registry=None))
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertNotIn("reach", out)
        self.assertRegex(out, r"fohmixer\s+host=dev1\s+sudo=no\s+secrets=")


class TestTeardownRuns(unittest.TestCase):
    """Run the rendered step 3b under `set -euo pipefail` with stub nft and
    systemctl on PATH and a fake /etc root, before and after a reach install."""

    def _run(self, installed):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(d)]))
        bin_ = d / "bin"
        bin_.mkdir()
        log = d / "calls.log"
        has_table = "1" if installed else "0"
        for name, body in (
            ("nft", 'echo "nft $*" >> "%s"\n'
                    'if [ "$1 $2" = "list table" ]; then exit $(( 1 - %s )); fi\n'
                    'exit 0\n' % (log, has_table)),
            ("systemctl", 'echo "systemctl $*" >> "%s"\nexit 0\n' % log)):
            p = bin_ / name
            p.write_text("#!/bin/bash\n" + body)
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        etc = d / "etc"
        (etc / "airuleset").mkdir(parents=True)
        (etc / "systemd" / "system").mkdir(parents=True)
        if installed:
            (etc / "airuleset" / "reach-fohmixer.nft").write_text("table\n")
            (etc / "systemd" / "system" / "airuleset-reach-fohmixer.service").write_text("[Unit]\n")
        step = _step_3b(_render("fohmixer"))
        step = step.replace("/etc/", str(etc) + "/")
        script = "set -euo pipefail\nACCOUNT=fohmixer\n" + step
        env = {**os.environ, "PATH": "%s:%s" % (bin_, os.environ["PATH"])}
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, env=env)
        calls = log.read_text() if log.exists() else ""
        return r, calls, etc

    def test_installed_reach_is_removed(self):
        r, calls, etc = self._run(installed=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(calls, r"nft delete table inet airuleset_reach_fohmixer")
        self.assertIn("systemctl disable --now airuleset-reach-fohmixer.service", calls)
        self.assertFalse((etc / "airuleset" / "reach-fohmixer.nft").exists())
        self.assertFalse(
            (etc / "systemd" / "system" / "airuleset-reach-fohmixer.service").exists())

    def test_nothing_installed_is_a_quiet_no_op(self):
        r, calls, _ = self._run(installed=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("delete table", calls)
        self.assertFalse(re.search(r"systemctl disable", calls))


if __name__ == "__main__":
    unittest.main()
