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


if __name__ == "__main__":
    unittest.main()
