"""#1317: dev1 moved to dstack's generic Dante-leg filter (dstack #264, owner
13.11.2026). dstack's scoped sudo follows it: the -D form loads the root-owned
/etc/dstack/dante-leg.nft into table inet dstack_dante_leg. `=` is escaped for
sudoers; the -f file still gets the root-owned file + parents check."""
import unittest

import cli_account_bootstrap as bootstrap
import cli_account_hardening as hardening

LEG = [
    "/usr/sbin/nft -D dante_leg\\=enp38s0.70 -f /etc/dstack/dante-leg.nft",
    "/usr/sbin/nft list table inet dstack_dante_leg",
    "/usr/sbin/nft delete table inet dstack_dante_leg",
    "/usr/bin/systemctl restart avahi-daemon",
]


class TestDanteLegSudo(unittest.TestCase):
    def setUp(self):
        self.cmds = bootstrap.account_spec("dstack")["sudo_commands"]

    def test_grants_the_leg_lines(self):
        for line in LEG:
            self.assertIn(line, self.cmds)

    def test_old_guard_lines_gone(self):
        self.assertFalse([c for c in self.cmds if "dante_guard" in c or "dante-guard" in c])

    def test_nothing_else_beyond_nmcli(self):
        rest = [c for c in self.cmds if not c.startswith("/usr/bin/nmcli con ")]
        self.assertEqual(sorted(rest), sorted(LEG))

    def test_declaration_valid(self):
        self.assertEqual(bootstrap.validate_account("dstack", bootstrap.account_spec("dstack")), [])


class TestFileCheck(unittest.TestCase):
    def test_d_form_file_is_checked(self):
        out = hardening.render_sudo_file_checks(LEG)
        self.assertIn("for P in /etc/dstack/dante-leg.nft; do", out)

    def test_rendered_step_checks_the_file(self):
        out = hardening.render_sudo_step("dstack", bootstrap.account_spec("dstack"))
        self.assertIn("for P in /etc/dstack/dante-leg.nft; do", out)

    def test_f_not_last_is_not_a_file(self):
        self.assertEqual(hardening.render_sudo_file_checks(
            ["/usr/sbin/nft -f /etc/x.nft -c"]), "")
