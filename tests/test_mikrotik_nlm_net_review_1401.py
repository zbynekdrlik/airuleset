"""#1401 security review of scripts/root/mikrotik-nlm-net, run through a
sandboxed copy (tests/_mikrotik_nlm_sandbox.py): every root-side action
reaches a stub, so the tests prove what root would run."""
import unittest

from _mikrotik_nlm_sandbox import Sandbox


class SandboxRuns(unittest.TestCase):

    def test_addr_and_fixed_profile_reach_their_stubs(self):
        sb = Sandbox(self)
        res = sb.run("addr", "add", "192.168.88.2/24")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ip addr add 192.168.88.2/24 dev enp39s0", sb.calls())
        res = sb.run("nm", "up", "dev1-lan")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(["connection", "up", "id", "dev1-lan"], sb.nm_calls())

    def test_netinstall_runs_the_staged_copy_with_the_forced_interface(self):
        sb = Sandbox(self)
        pkg = sb.dir / "routeros-7.20-arm64.npk"
        pkg.write_text("PKG")
        res = sb.run("netinstall", "-r", str(pkg))
        self.assertEqual(res.returncode, 0, res.stderr)
        (line,) = [c for c in sb.calls() if c.startswith("netinstall ")]
        self.assertTrue(line.startswith("netinstall -i enp39s0 -r %s/" % (sb.dir / "stage")), line)
        self.assertTrue(line.endswith("/routeros-7.20-arm64.npk"), line)
        self.assertIn("runuser mikrotik-nlm cat -- %s" % pkg, sb.calls())
        self.assertTrue(any(c.startswith("staged ") and c.endswith(" PKG") for c in sb.calls()))


class ExactListMatch(unittest.TestCase):
    """Review 1: a multi-word argument matched the space-joined allow-list."""

    def test_multi_word_arguments_exit_2(self):
        sb = Sandbox(self)
        for args in (("nm", "up", "dev1-lan dante-vlan"),
                     ("nm", "show", "dante-vlan router2-backup"),
                     ("nm", "modify", "dev1-lan", "ipv4.method ipv4.addresses", "manual"),
                     ("capture", "enp38s0 enp39s0", "5"),
                     ("capture", "enp38s0\tenp39s0", "5"),
                     ("nm", "up", " dev1-lan")):
            with self.subTest(args=args):
                self.assertEqual(sb.run(*args).returncode, 2)
        self.assertEqual(sb.nm_calls(), [])
        self.assertEqual(sb.calls(), [])


BAD_VALUES = {
    "ipv4.method": ["shared", "manual,auto", "", "Manual"],
    "ipv4.gateway": ["10.0.0.1,10.0.0.2", "10.0.0.256", "gw", "10.0.0.1/24"],
    "ipv4.dns": ["1.1.1.1 8.8.8.8", "1.1.1.1,x", "1.1.1.1,", ",1.1.1.1"],
    "ipv4.addresses": ["10.0.0.1", "10.0.0.1/24,10.0.0.2", "10.0.0.1/33"],
    "ipv4.routes": ["0.0.0.0/0 10.0.0.1", "10.0.0.0/0", "0.0.0.0/0",
                    "10.0.0.0/8,0.0.0.0/0 10.0.0.1", "10.0.0.0/8 10.0.0.1 10000",
                    "10.0.0.0/8 x", "10.0.0.0/8 10.0.0.1 10 20", "10.0.0.0"],
    "ipv4.route-metric": ["10000", "-1", "", "1e3"],
    "ipv4.never-default": ["true", "", "1"],
    "ipv4.ignore-auto-dns": ["1", "", "true"],
    "ipv6.method": ["manual", "shared", "", "dhcp"],
    "connection.autoconnect": ["", "on", "true"],
}
GOOD_VALUES = {
    "ipv4.method": ["manual", "auto", "disabled", "link-local"],
    "ipv4.gateway": ["", "192.168.88.1"],
    "ipv4.dns": ["", "1.1.1.1", "1.1.1.1,8.8.8.8"],
    "ipv4.addresses": ["", "192.168.88.2/24", "192.168.88.2/24,10.0.0.2/8"],
    "ipv4.routes": ["", "10.0.0.0/8 192.168.88.1 100,172.16.0.0/12",
                    "10.1.0.0/16 192.168.88.1", "10.2.0.0/16"],
    "ipv4.route-metric": ["0", "9999"],
    "ipv4.never-default": ["yes", "no"],
    "ipv4.ignore-auto-dns": ["yes", "no"],
    "ipv6.method": ["auto", "link-local", "ignore", "disabled"],
    "connection.autoconnect": ["yes", "no"],
}


class ModifyValuesPerKey(unittest.TestCase):
    """Review 2: one charset for every key let ipv4.method shared (NAT +
    DHCP server) or a 0.0.0.0/0 route through."""

    def test_bad_values_exit_2_before_anything_runs(self):
        sb = Sandbox(self)
        for key, values in BAD_VALUES.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertEqual(sb.run("nm", "modify", "dev1-lan", key, value).returncode, 2)
        self.assertEqual(sb.nm_calls(), [])

    def test_good_values_reach_nmcli(self):
        sb = Sandbox(self)
        for key, values in GOOD_VALUES.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    res = sb.run("nm", "modify", "dev1-lan", key, value)
                    self.assertEqual(res.returncode, 0, res.stderr)
                    self.assertEqual(sb.nm_calls()[-1],
                                     ["connection", "modify", "id", "dev1-lan", key, value])

    def test_every_allowed_key_has_a_rule(self):
        self.assertEqual(set(BAD_VALUES), set(GOOD_VALUES))
        self.assertEqual(len(GOOD_VALUES), 10)


if __name__ == "__main__":
    unittest.main()
