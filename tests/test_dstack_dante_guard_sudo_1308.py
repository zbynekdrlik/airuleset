"""#1308 (owner 12.11.2026, option 1): dstack on dev1 gets a narrow sudo for the
Dante-leg nftables filter and the avahi restart, on top of its nmcli lines.

The ruleset file is root-owned, so `nft -f` never reads a file dstack can
write (an `include` would read other accounts' files as root)."""
import os
import subprocess
import tempfile
import unittest

import cli_account_bootstrap as bootstrap
import cli_account_hardening as hardening

GUARD = [
    "/usr/sbin/nft -f /etc/nftables.d/dante-guard.nft",
    "/usr/sbin/nft list table inet dante_guard",
    "/usr/sbin/nft delete table inet dante_guard",
    "/usr/bin/systemctl restart avahi-daemon",
]


class TestDanteGuardSudo(unittest.TestCase):

    def setUp(self):
        self.cmds = bootstrap.account_spec("dstack")["sudo_commands"]

    def test_grants_exactly_the_guard_lines(self):
        for line in GUARD:
            self.assertIn(line, self.cmds)

    def test_nothing_else_beyond_nmcli(self):
        rest = [c for c in self.cmds if not c.startswith("/usr/bin/nmcli con ")]
        self.assertEqual(sorted(rest), sorted(GUARD))

    def test_no_bare_or_cups_or_zerotier(self):
        for c in self.cmds:
            self.assertNotEqual(c.split(" ")[0], c)  # always with arguments
            self.assertNotIn("cups", c)
            self.assertNotIn("zerotier", c)

    def test_declaration_still_valid(self):
        spec = bootstrap.account_spec("dstack")
        self.assertEqual(bootstrap.validate_account("dstack", spec), [])


class TestRender(unittest.TestCase):

    def setUp(self):
        self.out = hardening.render_sudo_step("dstack", bootstrap.account_spec("dstack"))

    def test_rule_is_noexec(self):
        self.assertIn("dstack ALL=(root) NOPASSWD:NOEXEC: ", self.out)

    def test_ruleset_file_is_checked(self):
        self.assertIn("for P in /etc/nftables.d/dante-guard.nft; do", self.out)

    def _run_checks(self, owner_of, dir_owner=None):
        snippet = hardening.render_sudo_file_checks(GUARD)
        with tempfile.TemporaryDirectory() as tmp:
            stub = os.path.join(tmp, "stat")
            with open(stub, "w") as fh:
                fh.write('#!/bin/bash\nif [ "${@: -1}" = /etc/nftables.d ]; '
                         'then echo "%s"; else echo "%s"; fi\n'
                         % (dir_owner or owner_of, owner_of))
            os.chmod(stub, 0o755)
            env = dict(os.environ, PATH=tmp + ":" + os.environ["PATH"])
            return subprocess.run(["bash", "-c", "set -euo pipefail\n" + snippet],
                                  env=env, capture_output=True, text=True)

    def test_root_owned_passes(self):
        self.assertEqual(self._run_checks("root -rw-r--r--").returncode, 0)

    def test_account_owned_or_writable_fails(self):
        self.assertEqual(self._run_checks("dstack -rw-r--r--").returncode, 1)
        self.assertEqual(self._run_checks("root -rw-rw-r--").returncode, 1)

    def test_writable_parent_dir_fails(self):
        r = self._run_checks("root -rw-r--r--", dir_owner="dstack drwxr-xr-x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("/etc/nftables.d ", r.stderr)

    def test_relative_path_fails_fast(self):
        snippet = hardening.render_sudo_file_checks(["/usr/sbin/nft -f x.nft"])
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + snippet],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not absolute", r.stderr)

    def test_symlink_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.symlink("/etc/hostname", os.path.join(tmp, "g.nft"))
            snippet = hardening.render_sudo_file_checks(
                ["/usr/sbin/nft -f %s/g.nft" % tmp])
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + snippet],
                               capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 1)
        self.assertIn("is a symlink", r.stderr)

    def test_missing_file_fails(self):
        snippet = hardening.render_sudo_file_checks(["/usr/sbin/nft -f /nonexistent/g.nft"])
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + snippet],
                           capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)

    def test_nmcli_fields_flag_is_not_a_file(self):
        self.assertEqual(hardening.render_sudo_file_checks(
            ["/usr/bin/nmcli -f name con show"]), "")

    def test_no_dash_f_renders_nothing(self):
        self.assertEqual(hardening.render_sudo_file_checks(["/usr/bin/nmcli con up x"]), "")


if __name__ == "__main__":
    unittest.main()
