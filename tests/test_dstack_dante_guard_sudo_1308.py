"""#1308 (owner 12.11.2026, option 1): dstack on dev1 gets a narrow sudo for the
Dante-leg nftables filter and the avahi restart, on top of its nmcli lines.

The ruleset file is root-owned, so `nft -f` never reads a file dstack can
write (an `include` would read other accounts' files as root)."""
import unittest

import cli_account_bootstrap as bootstrap

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


if __name__ == "__main__":
    unittest.main()
