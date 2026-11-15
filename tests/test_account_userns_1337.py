"""#1337: one declared project account gets unprivileged user namespaces on a host
whose ``kernel.apparmor_restrict_unprivileged_userns`` stays 1. AppArmor attaches
a profile by PATH, so the grant is a root-owned copy of ``unshare`` in a dir only
that account's group reaches, plus an ``unconfined`` + ``userns`` profile on it."""
import os
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

# install/chown as non-root: drop the owner flags; apparmor_parser: log argv.
_STUBS = r'''
install() {
  local a=()
  while [ $# -gt 0 ]; do
    case "$1" in -o|-g) shift 2 ;; *) a+=("$1"); shift ;; esac
  done
  command install "${a[@]}"
}
apparmor_parser() { printf '%s\n' "$*" >> "$AA_LOG"; }
'''


def _run(step, tmp, account="dcontroller"):
    """Run a rendered step under ``set -euo pipefail`` with every root path
    moved under ``tmp``. Returns the apparmor_parser argv log."""
    log = Path(tmp, "aa.log")
    log.touch()
    body = (step.replace(hardening.USERNS_DIR, str(Path(tmp, "userns")))
                .replace(hardening.APPARMOR_DIR, str(Path(tmp, "apparmor.d"))))
    Path(tmp, "apparmor.d").mkdir(exist_ok=True)
    script = "set -euo pipefail\nACCOUNT=%s\nAA_LOG=%s\n%s%s" % (account, log, _STUBS, body)
    subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
    return log.read_text().splitlines()


class TestValidation(unittest.TestCase):

    def test_bool_only(self):
        self.assertEqual(policy.validate_userns({}), [])
        self.assertEqual(policy.validate_userns({"userns": True}), [])
        self.assertEqual(policy.validate_userns({"userns": False}), [])
        for bad in (1, 0, "yes", "True", None, [True]):
            self.assertTrue(policy.validate_userns({"userns": bad}), repr(bad))

    def test_validate_account_uses_it(self):
        raw = dict(bootstrap.SERVICE_ACCOUNTS["dcontroller"], userns="yes")
        self.assertTrue([e for e in bootstrap.validate_account("dcontroller", raw)
                         if "userns" in e])

    def test_key_is_allowed(self):
        raw = dict(bootstrap.SERVICE_ACCOUNTS["dasio"], userns=True)
        self.assertEqual(bootstrap.validate_account("dasio", raw), [])


class TestDeclaration(unittest.TestCase):

    def test_dcontroller_declares_it(self):
        spec = bootstrap.account_spec("dcontroller")
        self.assertIs(spec["userns"], True)
        self.assertEqual(bootstrap.validate_account("dcontroller", spec), [])

    def test_no_other_account_declares_it(self):
        others = [a for a, raw in bootstrap.SERVICE_ACCOUNTS.items()
                  if raw.get("userns") and a != "dcontroller"]
        self.assertEqual(others, [])


class TestRender(unittest.TestCase):

    def test_declared_text(self):
        step = hardening.render_userns_step("dcontroller", {"userns": True})
        d = "/usr/local/lib/airuleset/userns/dcontroller"
        self.assertIn('install -d -o root -g dcontroller -m 0750 "%s"' % d, step)
        self.assertIn('/usr/bin/unshare', step)
        self.assertIn('cmp -s', step)
        self.assertIn('apparmor_parser -r "/etc/apparmor.d/airuleset-userns-dcontroller"', step)
        profile = hardening.userns_profile("dcontroller")
        self.assertIn("abi <abi/4.0>,", profile)
        self.assertIn("include <tunables/global>", profile)
        self.assertIn("profile airuleset-userns-dcontroller %s/unshare flags=(unconfined) {" % d,
                      profile)
        self.assertIn("  userns,", profile)
        self.assertIn("include if exists <local/airuleset-userns-dcontroller>", profile)

    def test_declared_run_installs_copy_and_profile(self):
        step = hardening.render_userns_step("dcontroller", {"userns": True})
        with tempfile.TemporaryDirectory() as tmp:
            log = _run(step, tmp)
            d = Path(tmp, "userns", "dcontroller")
            copy = d / "unshare"
            self.assertEqual(d.stat().st_mode & 0o777, 0o750)
            self.assertEqual(copy.stat().st_mode & 0o777, 0o755)
            self.assertEqual(copy.read_bytes(), Path("/usr/bin/unshare").read_bytes())
            prof = Path(tmp, "apparmor.d", "airuleset-userns-dcontroller")
            self.assertIn("flags=(unconfined)", prof.read_text())
            self.assertEqual(prof.stat().st_mode & 0o777, 0o644)
            self.assertEqual(log, ["-r %s" % prof])
            # a stale copy is refreshed; a second run is idempotent
            copy.write_bytes(b"old")
            _run(step, tmp)
            self.assertEqual(copy.read_bytes(), Path("/usr/bin/unshare").read_bytes())

    def test_undeclared_run_removes_everything(self):
        on = hardening.render_userns_step("dcontroller", {"userns": True})
        for spec in ({}, {"userns": False}):
            off = hardening.render_userns_step("dcontroller", spec)
            self.assertNotIn("apparmor_parser -r", off)
            with tempfile.TemporaryDirectory() as tmp:
                _run(on, tmp)
                prof = Path(tmp, "apparmor.d", "airuleset-userns-dcontroller")
                log = _run(off, tmp)
                self.assertEqual(log, ["-r %s" % prof, "-R %s" % prof])
                self.assertFalse(prof.exists())
                self.assertFalse(Path(tmp, "userns", "dcontroller").exists())
                log = _run(off, tmp)     # nothing left: no -R, no error
                self.assertEqual(log, ["-r %s" % prof, "-R %s" % prof])

    def test_wired_into_the_full_script_before_the_keys(self):
        script = bootstrap.render_root_bootstrap("dcontroller")
        self.assertIn("apparmor_parser -r", script)
        self.assertLess(script.index("# 3d-rt."), script.index("# 3d-ns."))
        self.assertLess(script.index("# 3d-ns."), script.index("/.ssh/authorized_keys"))
        self.assertNotIn("apparmor_parser -r", bootstrap.render_root_bootstrap("dasio"))
        self.assertIn("# 3d-ns.", bootstrap.render_root_bootstrap("dasio"))


class TestVerify(unittest.TestCase):

    def test_check_listed(self):
        self.assertIn("userns", verify.CHECKS)

    def test_declared_needs_a_working_unshare(self):
        ok = {"userns": {"present": ["yes"], "rc": ["0"]}}
        self.assertTrue(verify._check_userns({"userns": True}, ok)[0])
        self.assertFalse(verify._check_userns(
            {"userns": True}, {"userns": {"present": ["yes"], "rc": ["1"]}})[0])
        self.assertFalse(verify._check_userns(
            {"userns": True}, {"userns": {"present": ["no"], "rc": ["none"]}})[0])
        self.assertFalse(verify._check_userns({"userns": True}, {})[0])

    def test_undeclared_needs_the_dir_gone(self):
        self.assertTrue(verify._check_userns(
            {}, {"userns": {"present": ["no"], "rc": ["none"]}})[0])
        self.assertFalse(verify._check_userns(
            {"userns": False}, {"userns": {"present": ["yes"], "rc": ["0"]}})[0])

    def test_probe_section(self):
        probe = verify.render_probe("dcontroller", bootstrap.account_spec("dcontroller"))
        self.assertIn("echo @@userns", probe)
        self.assertIn("/usr/local/lib/airuleset/userns/dcontroller", probe)
        self.assertIn("--user --map-root-user --net true", probe)

    def test_probe_runs_and_parses(self):
        probe = verify.render_probe("dcontroller", bootstrap.account_spec("dcontroller"))
        start = probe.index("echo @@userns")
        snippet = probe[start:probe.index("echo @@", start + 1)]
        with tempfile.TemporaryDirectory() as tmp:
            run = snippet.replace(hardening.USERNS_DIR, tmp)
            os.mkdir(os.path.join(tmp, "dcontroller"))
            s = verify.parse_probe(subprocess.run(["bash", "-c", run], capture_output=True,
                                                  text=True, check=True).stdout)
            self.assertEqual((s["userns"]["present"], s["userns"]["rc"]), (["yes"], ["none"]))
            self.assertFalse(verify._check_userns({"userns": True}, s)[0])
            os.rmdir(os.path.join(tmp, "dcontroller"))
            s = verify.parse_probe(subprocess.run(["bash", "-c", run], capture_output=True,
                                                  text=True, check=True).stdout)
            self.assertEqual(s["userns"]["present"], ["no"])
            self.assertTrue(verify._check_userns({}, s)[0])


if __name__ == "__main__":
    unittest.main()
