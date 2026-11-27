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


if __name__ == "__main__":
    unittest.main()
