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


ACTIONS = ({"up", "down", "modify", "delete"}, "reapply")


def _acted(calls):
    """nmcli calls that change state (not the read-only lookups)."""
    return [c for c in calls if (c[:1] == ["connection"] and c[1:2] and c[1] in ACTIONS[0])
            or c[:2] == ["device", ACTIONS[1]]]


class OwnedProfiles(unittest.TestCase):
    """Review 3: any connection NAMED mknlm-* was the account's to change or
    delete, also one somebody else created. Only UUIDs that add-ethernet
    created (a root-only list) count."""

    def _sb(self):
        sb = Sandbox(self, STATE_DIR=lambda d: d / "state-parent" / "mikrotik-nlm-net")
        self.state = sb.dir / "state-parent" / "mikrotik-nlm-net"
        return sb

    def test_add_records_the_uuid_and_the_lifecycle_uses_it(self):
        sb = self._sb()
        res = sb.run("nm", "add-ethernet", "mknlm-lab", "enp39s0")
        self.assertEqual(res.returncode, 0, res.stderr)
        (uuid,) = [u for u, n in sb.connections().items() if n == "mknlm-lab"]
        owned = self.state / "owned"
        self.assertEqual(owned.read_text().split(), [uuid])
        self.assertEqual(oct(owned.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(self.state.stat().st_mode & 0o777), "0o700")
        for args, want in ((("up", "mknlm-lab"), ["connection", "up", "uuid", uuid]),
                           (("down", "mknlm-lab"), ["connection", "down", "uuid", uuid]),
                           (("modify", "mknlm-lab", "ipv4.method", "manual"),
                            ["connection", "modify", "uuid", uuid, "ipv4.method", "manual"]),
                           (("reapply", "mknlm-lab"), ["device", "reapply", "enp39s0"])):
            with self.subTest(args=args):
                res = sb.run("nm", *args)
                self.assertEqual(res.returncode, 0, res.stderr)
                self.assertEqual(sb.nm_calls()[-1], want)
        res = sb.run("nm", "delete", "mknlm-lab")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(sb.nm_calls()[-1], ["connection", "delete", "uuid", uuid])
        self.assertEqual(owned.read_text().split(), [])
        self.assertNotIn(uuid, sb.connections())

    def test_a_foreign_mknlm_connection_is_refused(self):
        sb = self._sb()
        sb.set_connections({"11111111-2222-3333-4444-555555555555": "mknlm-foreign"})
        for args in (("up", "mknlm-foreign"), ("down", "mknlm-foreign"),
                     ("modify", "mknlm-foreign", "ipv4.method", "manual"),
                     ("reapply", "mknlm-foreign"), ("delete", "mknlm-foreign")):
            with self.subTest(args=args):
                self.assertEqual(sb.run("nm", *args).returncode, 1)
        self.assertEqual(_acted(sb.nm_calls()), [])
        self.assertIn("mknlm-foreign", sb.connections().values())

    def test_a_name_shared_with_a_foreign_connection_is_refused(self):
        sb = self._sb()
        self.assertEqual(sb.run("nm", "add-ethernet", "mknlm-lab", "enp39s0").returncode, 0)
        conns = sb.connections()
        conns["11111111-2222-3333-4444-555555555555"] = "mknlm-lab"
        sb.set_connections(conns)
        self.assertEqual(sb.run("nm", "up", "mknlm-lab").returncode, 1)
        self.assertEqual(_acted(sb.nm_calls()), [])

    def test_add_refuses_an_existing_name(self):
        sb = self._sb()
        sb.set_connections({"11111111-2222-3333-4444-555555555555": "mknlm-lab"})
        self.assertEqual(sb.run("nm", "add-ethernet", "mknlm-lab", "enp39s0").returncode, 1)
        self.assertEqual([c for c in sb.nm_calls() if c[:2] == ["connection", "add"]], [])
        self.assertFalse((self.state / "owned").exists() and (self.state / "owned").read_text().strip())

    def test_a_symlinked_state_dir_or_list_is_refused(self):
        sb = self._sb()
        target = sb.dir / "elsewhere"
        target.mkdir(mode=0o700)
        self.state.symlink_to(target)
        self.assertEqual(sb.run("nm", "add-ethernet", "mknlm-lab", "enp39s0").returncode, 1)
        self.state.unlink()
        self.state.mkdir(mode=0o700)
        (self.state / "owned").symlink_to(sb.dir / "list")
        self.assertEqual(sb.run("nm", "add-ethernet", "mknlm-lab", "enp39s0").returncode, 1)
        self.assertEqual([c for c in sb.nm_calls() if c[:2] == ["connection", "add"]], [])


class AddDefaults(unittest.TestCase):
    """Review 4: a bare `connection add` is ipv4 auto + autoconnect yes, so
    a new profile could grab enp39s0 and a default route on its own."""

    def test_add_ethernet_creates_an_inert_profile(self):
        sb = Sandbox(self, STATE_DIR=lambda d: d / "state-parent" / "mikrotik-nlm-net")
        res = sb.run("nm", "add-ethernet", "mknlm-lab", "enp39s0")
        self.assertEqual(res.returncode, 0, res.stderr)
        (add,) = [c for c in sb.nm_calls() if c[:2] == ["connection", "add"]]
        self.assertEqual(add, ["connection", "add", "type", "ethernet", "con-name", "mknlm-lab",
                               "ifname", "enp39s0", "ipv4.method", "disabled",
                               "ipv6.method", "link-local", "ipv4.never-default", "yes",
                               "connection.autoconnect", "no"])


class BoundedStaging(unittest.TestCase):
    """Review 5: staging read an unbounded file with no time limit, and the
    staged copy was used without a second look."""

    def _sb(self):
        return Sandbox(self, MAX_STAGE_BYTES=16, STAGE_TIMEOUT=2)

    def _pkg(self, sb, size):
        pkg = sb.dir / "routeros.npk"
        pkg.write_bytes(b"x" * size)
        return pkg

    def test_a_file_at_the_cap_is_staged(self):
        sb = self._sb()
        res = sb.run("netinstall", "-r", str(self._pkg(sb, 16)))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(any(c.startswith("staged ") and c.endswith(" " + "x" * 16) for c in sb.calls()))

    def test_a_file_over_the_cap_is_refused(self):
        sb = self._sb()
        for opt in ([], ["-s"]):
            with self.subTest(opt=opt):
                big = str(self._pkg(sb, 17))
                args = ["netinstall", "-r"] + (opt + [big] if opt else []) + [big]
                res = sb.run(*args)
                self.assertEqual(res.returncode, 1, res.stderr)
                self.assertIn("larger than", res.stderr)
        self.assertEqual([c for c in sb.calls() if c.startswith("netinstall ")], [])

    def test_a_read_that_hangs_is_cut_off(self):
        sb = self._sb()
        (sb.dir / "bin" / "runuser").write_text("#!/bin/bash\nexec sleep 30\n")
        res = sb.run("netinstall", "-r", str(self._pkg(sb, 4)))
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertEqual([c for c in sb.calls() if c.startswith("netinstall ")], [])

    def test_the_staged_copies_are_rechecked_before_use(self):
        src = (Sandbox(self).script).read_text()
        body = src[src.index("\nnetinstall)\n    [ ! -L"):]
        self.assertIn("timeout \"$STAGE_TIMEOUT\" runuser -u mikrotik-nlm -- cat --", body)
        self.assertLess(body.index("staged copy"), body.index('"$NETINSTALL" -i'))


class SafeLog(unittest.TestCase):
    """Review 6: root appended to $LOG without looking at it, so a symlink
    there (or a log someone else owns) made root write where it points."""

    def _sb(self, **extra):
        return Sandbox(self, STATE_DIR=lambda d: d / "state-parent" / "mikrotik-nlm-net", **extra)

    def _ip_calls(self, sb):
        return [c for c in sb.calls() if c.startswith("ip ")]

    def test_a_missing_log_is_created_root_only(self):
        sb = self._sb()
        self.assertFalse(sb.log.exists())
        res = sb.run("addr", "add", "192.168.88.2/24")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(oct(sb.log.stat().st_mode & 0o777), "0o600")
        self.assertIn("ip addr add 192.168.88.2/24 dev enp39s0", sb.log.read_text())

    def test_a_symlinked_log_is_refused_and_never_written(self):
        sb = self._sb()
        target = sb.dir / "victim"
        target.write_text("keep\n")
        sb.log.symlink_to(target)
        res = sb.run("addr", "add", "192.168.88.2/24")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(target.read_text(), "keep\n")
        self.assertEqual(self._ip_calls(sb), [])

    def test_a_log_owned_by_someone_else_or_not_a_file_is_refused(self):
        sb = self._sb(ROOT_UID=99999)
        sb.log.write_text("")
        self.assertEqual(sb.run("addr", "add", "192.168.88.2/24").returncode, 1)
        sb2 = self._sb()
        sb2.log.mkdir()
        self.assertEqual(sb2.run("addr", "add", "192.168.88.2/24").returncode, 1)
        self.assertEqual(self._ip_calls(sb) + self._ip_calls(sb2), [])

    def test_a_symlinked_state_dir_is_refused_for_every_action(self):
        sb = self._sb()
        (sb.dir / "elsewhere").mkdir()
        (sb.dir / "state-parent" / "mikrotik-nlm-net").symlink_to(sb.dir / "elsewhere")
        self.assertEqual(sb.run("addr", "add", "192.168.88.2/24").returncode, 1)
        self.assertEqual(self._ip_calls(sb), [])


if __name__ == "__main__":
    unittest.main()
