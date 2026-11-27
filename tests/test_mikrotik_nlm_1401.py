"""#1401 (design 27.11.2026): the SNV/PZ MikroTik network work leaves the shared
`newlevel@dev1` account for its own project account `mikrotik-nlm` on dev1.

Its root needs (tcpdump, NetworkManager profiles, an address on enp39s0, a rare
netinstall) go through ONE root-owned tool `/usr/local/sbin/mikrotik-nlm-net`
(the #1394 bazos-usbdisk ARGS pattern), never raw `sudo nmcli` / `sudo tcpdump`:
tcpdump `-z` runs a command as root and nmcli's full surface is root too.
Declared pending first; flipped live 27.11. after the root bootstrap on dev1,
with the owner + marek tab after bazos, the old newlevel `mikrotik` registry
row renamed onto the account (LEGACY_CEILING 19 -> 18) and every dev1 peer
link of `mikrotik` renamed with its topic. The pending path keeps its
coverage through a REMOTE_HOSTS patch.
"""
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import cli_account_bootstrap as bootstrap
import cli_account_hardening as hardening
import cli_account_policy as policy
import cli_accounts
import cli_drop_lanes as drop
import cli_fleet
import cli_peer_channel as cpc
import cli_project_ci_sync as ci_sync
import cli_project_gh_token as pgt
import cli_webterm as w
import cli_webterm_profiles as profiles
from cli_aliases import short_target_alias

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "root" / "mikrotik-nlm-net"
ACCT = "mikrotik-nlm"
ENTRY = "/usr/local/sbin/mikrotik-nlm-net ARGS"


class Declaration(unittest.TestCase):

    def setUp(self):
        self.spec = bootstrap.account_spec(ACCT)

    def test_valid_on_dev1_with_only_the_net_tool(self):
        self.assertEqual(bootstrap.validate_account(ACCT, self.spec), [])
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["sudo"], "commands")
        self.assertEqual(self.spec["sudo_commands"], [ENTRY])
        self.assertIn("#1401", self.spec["sudo_reason"])
        self.assertEqual(self.spec["system_packages"], ["tcpdump"])
        for grant in ("devices", "rtprio", "userns", "nic_noqueue"):
            self.assertFalse(self.spec.get(grant), grant)

    def test_policy_constant_is_the_declared_sudo(self):
        for key, value in policy.MIKROTIK_NLM_NET_SUDO.items():
            self.assertEqual(self.spec[key], value, key)

    def test_repo_secrets_and_project(self):
        self.assertEqual(self.spec["repo"], "zbynekdrlik/network")
        self.assertIs(self.spec["github_app"], True)
        self.assertEqual(self.spec["repo_secrets"], [])
        self.assertEqual(self.spec["secrets"], ["pp-mikrotik-ro"])
        self.assertEqual(self.spec["project_dir"], "devel/mikrotik")
        self.assertEqual(self.spec["tmux_session"], "mikrotik-nlm")
        self.assertEqual(self.spec["webterm_sessions"], {
            h: {"preferred": "mikrotik-nlm", "start_dir_chain": ["devel/mikrotik"]}
            for h in ("zbynek", "marek")})

    def test_sudoers_line_passes_argv(self):
        out = hardening.render_sudo_step(ACCT, self.spec)
        self.assertIn("mikrotik-nlm ALL=(root) NOPASSWD: /usr/local/sbin/mikrotik-nlm-net\n", out)
        self.assertNotIn('mikrotik-nlm-net ""', out)
        # The one rule line: never a raw nmcli/tcpdump grant (the reason comment
        # may name the tools it wraps).
        rules = [ln for ln in out.splitlines() if "NOPASSWD:" in ln]
        self.assertEqual(rules, ["mikrotik-nlm ALL=(root) NOPASSWD: /usr/local/sbin/mikrotik-nlm-net"])

    def test_script_path_is_not_claimed_by_mikrotik_pz(self):
        # mikrotik-pz must never get mikrotik-nlm's tool (longest-name ownership).
        spec = {"sudo": "commands", "sudo_reason": "x", "sudo_commands": [ENTRY]}
        self.assertTrue(policy.validate_sudo(spec, "mikrotik-pz",
                                             declared=bootstrap.SERVICE_ACCOUNTS))


INFRA_TOPIC = ("airuleset infra: accounts, permissions, peer links, tooling. Ask or "
               "report here; airuleset decides and a ticket alone wakes no session")


def _pending():
    """REMOTE_HOSTS with mikrotik-nlm marked pending again (the state before
    its root bootstrap), so the skip paths keep their coverage."""
    return mock.patch.object(cli_fleet, "REMOTE_HOSTS", [
        dict(h, pending=True) if h.get("user") == ACCT else h
        for h in cli_fleet.REMOTE_HOSTS])


def _mint_all():
    out = io.StringIO()
    with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
            mock.patch.object(ci_sync, "run_after_mint", return_value=0), \
            redirect_stdout(out):
        rc = pgt.cmd_project_gh_token(mock.Mock(
            action="mint", account=None, all=True, dry_run=False, key=None))
    with mock.patch.object(ci_sync, "sync_account", return_value=0) as s:
        ci_sync.sync_all(run=lambda *a, **k: None)
    return rc, [c.args[0] for c in m.call_args_list], out.getvalue(), \
        [c.args[0] for c in s.call_args_list]


class LiveAccount(unittest.TestCase):
    """Live 27.11.: root bootstrap ran on dev1 (uid 1023, sudo -l lists only
    the tool), airuleset installed as the account, checkout copied."""

    def test_live_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "mikrotik-nlm@dev1")
        self.assertEqual(entry["host"], "100.104.8.125")
        self.assertIs(entry["pending"], False)
        self.assertFalse(bootstrap.is_pending(ACCT))
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)

    def test_mint_all_and_secret_sync_reach_it_live(self):
        rc, minted, out, synced = _mint_all()
        self.assertEqual(rc, 0)
        self.assertIn(ACCT, minted)
        self.assertNotIn("%s: SKIPPED" % ACCT, out)
        self.assertIn(ACCT, synced)
        self.assertIn(ACCT, cpc.accounts_for_host("dev1"))

    def test_unattended_paths_skip_it_while_pending(self):
        with _pending():
            self.assertTrue(bootstrap.is_pending(ACCT))
            rc, minted, out, synced = _mint_all()
            accts = cpc.accounts_for_host("dev1")
        self.assertEqual(rc, 0)
        self.assertIn("project-gh-token: %s: SKIPPED — pending" % ACCT, out)
        self.assertNotIn(ACCT, minted)
        self.assertNotIn(ACCT, synced)
        self.assertNotIn(ACCT, accts)

    def test_the_tab_follows_bazos_for_the_owner_and_marek_only(self):
        for human in ("zbynek", "marek"):
            tabs = w.WEBTERM_DASHBOARD_TABS[human]
            self.assertEqual(tabs.index(ACCT), tabs.index("bazos") + 1)
        for human in ("david", "dominika", "timo"):
            self.assertNotIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            (entry,) = [e for e in inv if e["id"] == ACCT]
            self.assertEqual(entry["user"], ACCT)

    def test_alias_does_not_read_as_the_snv_project(self):
        # The 8-char cut "mikrotik" was the SNV project's old name.
        self.assertEqual(short_target_alias(ACCT, "mikrotik-nlm-dev1"), "mt-nlm")

    def test_drop_port_is_fixed_and_free(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8919)
        self.assertEqual(list(drop._GENERATED_DROP_PORTS.values()).count(8919), 1)
        self.assertNotIn(8919, drop._RESERVED_PORTS)

    def test_registry_row_moved_to_the_account(self):
        import json
        reg = json.loads((ROOT / "projects-registry.json").read_text(encoding="utf-8"))
        self.assertEqual([r for r in reg if r["name"] == "mikrotik"], [])
        (row,) = [r for r in reg if r["name"] == ACCT]
        self.assertEqual(row, {
            "name": ACCT, "host": "dev1", "path": "/home/mikrotik-nlm/devel/mikrotik",
            "branch_model": "2-branch", "default_branch": "main", "work_branch": "dev",
            "overrides": ["no-git"], "onboarded": "2026-03-20", "account": ACCT,
            "stack": "config"})
        self.assertFalse(cli_accounts.is_legacy_account(ACCT))
        self.assertNotIn(ACCT, [r["name"] for r in cli_accounts.legacy_inventory(reg)])
        self.assertEqual(len(cli_accounts.legacy_inventory(reg)), cli_accounts.LEGACY_CEILING)
        self.assertEqual(cli_accounts.LEGACY_CEILING, 18)

    def test_peer_links_renamed_with_their_topics(self):
        import json
        links = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))
        dev1 = links["hosts"]["dev1"]
        pairs = sorted(sorted(p) for p in dev1["links"] if ACCT in p)
        self.assertEqual(pairs, [["airuleset", ACCT], ["camera-box", ACCT], ["dasio", ACCT],
                                 ["dstack", ACCT], ["fleet-backup", ACCT], ["iemmixer", ACCT]])
        self.assertEqual(dev1["topics"]["airuleset|mikrotik-nlm"], INFRA_TOPIC)
        text = json.dumps(links)
        self.assertNotRegex(text, r'(?<![\w-])mikrotik(?![\w-])')
        self.assertIn("mikrotik-nlm owns the devices and their credentials",
                      dev1["topics"]["camera-box|mikrotik-nlm"])
        self.assertEqual(cpc.validate_links(cpc.load_links(), cpc.load_registry()), [])


class Script(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.npk = d / "routeros-7.20-arm64.npk"
        cls.npk.write_bytes(b"npk")
        cls.txt = d / "routeros.txt"
        cls.txt.write_bytes(b"x")
        cls.link = d / "link.npk"
        cls.link.symlink_to(cls.npk)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                              text=True, env=dict(os.environ))

    def test_parses_and_is_executable(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_bad_input_exits_2(self):
        npk = str(self.npk)
        for args in (
            (), ("shell",), ("capture",),
            ("capture", "eth0", "10"),                       # interface not allow-listed
            ("capture", "enp38s0", "0"), ("capture", "enp38s0", "601"),
            ("capture", "enp38s0", "1e3"), ("capture", "enp38s0", "-5"),
            ("capture", "enp38s0", "10", "-z", "/tmp/x"),     # tcpdump flag injection
            ("capture", "enp38s0", "10", "port", "53", "-w", "/etc/x"),
            ("capture", "enp38s0", "10", "--relinquish-privileges=root"),
            ("nm", "up", "wifi-home"),                        # profile not allow-listed
            ("nm", "show", "mknlm-"), ("nm", "down", "mknlm-A"),
            ("nm", "up"), ("nm", "frobnicate", "dev1-lan"),
            ("nm", "modify", "dev1-lan", "802-3-ethernet.mac-address", "x"),
            ("nm", "modify", "dev1-lan", "+ipv4.addresses", "10.0.0.1/24"),
            ("nm", "modify", "dev1-lan", "ipv4.dns", "1.1.1.1;reboot"),
            ("nm", "modify", "dev1-lan", "ipv4.dns", "$(id)"),
            ("nm", "modify", "dev1-lan", "ipv4.dns", "x" * 201),
            ("nm", "modify", "dev1-lan", "ipv4.method"),
            ("nm", "add-ethernet", "lab", "enp39s0"),         # name outside mknlm-*
            ("nm", "add-ethernet", "mknlm-lab", "enp38s0"),
            ("nm", "add-ethernet", "mknlm-Lab", "enp39s0"),
            ("nm", "delete", "dev1-lan"),
            ("nm", "disconnect", "enp38s0"),
            ("addr", "add", "192.168.88.2"), ("addr", "add", "192.168.88.256/24"),
            ("addr", "add", "192.168.88.2/33"), ("addr", "del", "fe80::1/64"),
            ("addr", "flush", "192.168.88.2/24"),
            ("netinstall",),
            ("netinstall", "-i", "enp38s0", npk),             # interface is forced
            ("netinstall", "-r", "--mac", "aa:bb:cc:dd:ee:ff", npk),  # extra flag
            ("netinstall", "-r", "-v", npk),
            ("netinstall", "-a", "300.1.1.1", npk),
            ("netinstall", "-r", "-r", npk),
            ("netinstall", "-r", str(self.link)),             # symlink package
            ("netinstall", "-r", str(self.txt)),              # not .npk
            ("netinstall", "-r", str(self.npk.parent / "missing.npk")),
            ("netinstall", "-r"),                             # no package
            ("netinstall", "-s", str(self.link), npk),
        ):
            with self.subTest(args=args):
                self.assertEqual(self._run(*args).returncode, 2, args)

    def test_good_input_reaches_the_root_check(self):
        # As a non-root user, valid argv passes validation and stops at exit 1.
        if os.geteuid() == 0:
            self.skipTest("the root check passes as root")
        npk = str(self.npk)
        for args in (
            ("capture", "enp38s0.70", "30", "udp", "port", "319", "or", "port", "320"),
            ("capture", "enp39s0", "600"),
            ("nm", "show"), ("nm", "show", "dante-vlan"),
            ("nm", "up", "router2-backup"), ("nm", "down", "mknlm-pz-lab"),
            ("nm", "reapply", "dev1-lan"),
            ("nm", "modify", "mikrotik-pz-setup", "ipv4.addresses", "192.168.88.2/24"),
            ("nm", "modify", "dev1-lan", "ipv4.gateway", ""),
            ("nm", "add-ethernet", "mknlm-pz-lab", "enp39s0"),
            ("nm", "delete", "mknlm-pz-lab"), ("nm", "disconnect", "enp39s0"),
            ("addr", "add", "192.168.88.2/24"), ("addr", "del", "10.0.0.1/32"),
            ("netinstall", "-r", "-a", "192.168.88.3", npk),
            ("netinstall", "-e", "-b", npk),
        ):
            with self.subTest(args=args):
                res = self._run(*args)
                self.assertEqual(res.returncode, 1, (args, res.stderr))
                self.assertIn("run it with sudo", res.stderr)

    def test_guards_present(self):
        src = SCRIPT.read_text()
        for needle in ("set -euo pipefail", "export PATH=/usr/sbin:/usr/bin:/sbin:/bin",
                       "/var/log/mikrotik-nlm-net.log", "/opt/mikrotik-nlm/netinstall-cli",
                       'tcpdump -i "$IFACE" -U -w - --', "connection modify id",
                       "device reapply", "runuser -u mikrotik-nlm"):
            with self.subTest(needle=needle):
                self.assertIn(needle, src)

    def test_argv_is_validated_before_the_root_check(self):
        src = SCRIPT.read_text()
        root_check = src.index('[ "$(id -u)" -eq 0 ]')
        for needle in ("bad interface", "bad profile", "bad key", "bad value",
                       "bad package"):
            with self.subTest(needle=needle):
                self.assertLess(src.index(needle), root_check)


if __name__ == "__main__":
    unittest.main()
