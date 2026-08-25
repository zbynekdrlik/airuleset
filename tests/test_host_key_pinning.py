"""#669 — pin the ssh host key for raw-public-IP owner_vps targets.

The deploy loop (`_deploy_to_all_remotes`) and the shared secret-delivery loop
(`_deliver_secret_to_hosts`) both used a hardcoded `-o StrictHostKeyChecking=no`
for EVERY target — a TOFU posture that accepts an unknown AND a silently changed
host key. That was harmless for the private tailscale/subdev fleet, but
`spinbike-vps` (`167.233.245.147`) is the first managed target reached over the
public internet by raw IP, where a first-contact MITM or a rotated host key on
the deploy leg would hand the pushed code (and, before its removal, an owner
secret) to an attacker.

The fix pins a committed PUBLIC host key on such a target and switches its ssh
legs to `-o UserKnownHostsFile=<pin> -o GlobalKnownHostsFile=/dev/null
-o StrictHostKeyChecking=yes` (refuses unknown AND changed → a MITM/rotated key
hard-fails, ssh exit 255), while every unpinned tailscale/subdev host keeps the
current `=no` posture unchanged.

RED lock (this module): a deploy ssh leg to a PINNED host must use
`StrictHostKeyChecking=yes` + a `UserKnownHostsFile`, NEVER `=no`; an UNPINNED
host must keep `=no` (regression). Fails by assertion on the pre-fix `=no` code.
"""
import unittest
import unittest.mock as m

import airuleset
import cli_remote


def _fake_run(calls):
    def run(cmd, *a, **k):
        calls.append(list(cmd))
        return m.Mock(returncode=0, stdout="ok", stderr="")
    return run


# A raw-public-IP target carrying a committed PUBLIC host-key pin (`host_keys`)
# and a pinned identity — deliberately NOT owner_vps, so the deploy loop touches
# neither the soniox nor (pre-removal) the headless-token delivery phase: the
# pin decision is keyed on `host_keys` presence alone, independent of owner_vps.
PINNED = {
    "name": "pin-vps", "host": "203.0.113.7", "user": "newlevel",
    "repo_path": "~/devel/airuleset", "identity": "~/.ssh/pin_vps",
    "host_keys": [
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExampleEx",
    ],
}
# A normal tailscale target — no `host_keys`, must keep the unchanged `=no`.
PLAIN = {
    "name": "dev2", "host": "100.82.64.27", "user": "newlevel",
    "repo_path": "~/devel/airuleset",
}


def _deploy_argv_for(calls, host_ip):
    """The single deploy-loop ssh argv that targets `host_ip` (the `git pull &&
    install` connection), never the ruff/test/git-push subprocess calls."""
    deploy = [c for c in calls
              if any("python3 airuleset.py install" in str(a) for a in c)
              and any(host_ip in str(a) for a in c)]
    assert len(deploy) == 1, "expected exactly one deploy leg to %s, got %d" % (
        host_ip, len(deploy))
    return deploy[0]


class TestDeployLegPinsPublicIpHost(unittest.TestCase):
    def _run_push(self):
        calls = []
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=_fake_run(calls)), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", [PINNED, PLAIN]), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}), \
                m.patch.object(cli_remote, "_ssh_control_dir_for_push",
                               return_value=None):
            airuleset.cmd_push(args)
        return calls

    def test_pinned_host_deploy_leg_uses_strict_yes_not_no(self):
        calls = self._run_push()
        argv = _deploy_argv_for(calls, "203.0.113.7")
        joined = " ".join(argv)
        self.assertIn("StrictHostKeyChecking=yes", argv,
                      "a pinned owner_vps deploy leg must verify the host key "
                      "strictly, never TOFU — argv was: %s" % joined)
        self.assertTrue(any(a.startswith("UserKnownHostsFile=") for a in argv),
                        "a pinned deploy leg must point ssh at the committed "
                        "known_hosts pin — argv was: %s" % joined)
        self.assertNotIn("StrictHostKeyChecking=no", argv,
                         "a pinned deploy leg must NOT keep the TOFU posture — "
                         "argv was: %s" % joined)

    def test_unpinned_tailscale_host_deploy_leg_keeps_strict_no(self):
        # Regression: pinning scope is the raw-public-IP owner_vps class ONLY.
        calls = self._run_push()
        argv = _deploy_argv_for(calls, "100.82.64.27")
        self.assertIn("StrictHostKeyChecking=no", argv,
                      "an unpinned tailscale target must keep its unchanged "
                      "TOFU-none posture — argv was: %s" % " ".join(argv))
        self.assertNotIn("StrictHostKeyChecking=yes", argv)


class TestHostKeyCheckOpts(unittest.TestCase):
    def test_pinned_host_gets_strict_yes_and_known_hosts(self):
        opts = cli_remote.host_key_check_opts(PINNED)
        self.assertIn("StrictHostKeyChecking=yes", opts)
        self.assertIn("GlobalKnownHostsFile=/dev/null", opts)
        self.assertIn("UpdateHostKeys=no", opts)   # pin can't drift post-auth
        self.assertTrue(any(o.startswith("UserKnownHostsFile=") for o in opts))
        self.assertNotIn("StrictHostKeyChecking=no", opts)

    def test_unpinned_host_keeps_strict_no(self):
        # Regression: an ordinary tailscale/subdev host (absent host_keys) is
        # unchanged.
        self.assertEqual(cli_remote.host_key_check_opts(PLAIN),
                         ["-o", "StrictHostKeyChecking=no"])

    def test_present_but_empty_host_keys_raises_never_downgrades(self):
        # #669 review 🔵-2 (fail-closed): only an ABSENT host_keys is unpinned;
        # a host someone MEANT to pin (key present) but whose list is empty/blank
        # must RAISE, never silently downgrade to =no (e.g. keys commented out
        # mid-rotation).
        for empty in ([], ["   ", ""]):
            with self.assertRaises(RuntimeError):
                cli_remote.host_key_check_opts(
                    {"host": "203.0.113.9", "host_keys": empty})


class TestMaterializePinnedKnownHosts(unittest.TestCase):
    GENUINE_ED25519 = (
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIJ4gdjBncONNRHmRw+W8hNFBDkkvEORFWLBxXUWS2r7g")

    def test_materialized_file_pins_genuine_key_keyed_to_address(self):
        path = cli_remote._materialize_pinned_known_hosts(
            "167.233.245.147", [self.GENUINE_ED25519])
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        # keyed to the exact address ssh connects to, genuine key verbatim
        self.assertIn("167.233.245.147 " + self.GENUINE_ED25519, body)

    def test_fail_closed_empty_pin_raises(self):
        # A pinned host with an empty/blank pin must RAISE, never silently
        # produce a file that would let ssh fall back to acceptance.
        with self.assertRaises(RuntimeError):
            cli_remote._materialize_pinned_known_hosts("x.x.x.x", [])
        with self.assertRaises(RuntimeError):
            cli_remote._materialize_pinned_known_hosts("x.x.x.x", ["   ", ""])


class TestRealFleetPinScope(unittest.TestCase):
    """Prove the pin engages for the REAL fleet TODAY, and ONLY for the
    raw-public-IP owner_vps class — not a hand-built fixture."""

    def test_real_spinbike_entry_is_pinned_and_strict(self):
        sb = [h for h in airuleset.REMOTE_HOSTS if h.get("name") == "spinbike-vps"]
        self.assertEqual(len(sb), 1)
        self.assertTrue(sb[0].get("host_keys"),
                        "spinbike-vps must carry a committed host-key pin")
        self.assertIn("StrictHostKeyChecking=yes",
                      cli_remote.host_key_check_opts(sb[0]))

    def test_private_tailscale_hosts_stay_unpinned(self):
        # Regression: a private tailscale target (100.64/10) must NEVER be
        # pinned — pinning is for public-internet-reachable hosts only, and a
        # tailscale host has no committed pin. (This deliberately does NOT
        # forbid pinning OTHER public-IP hosts in a follow-up — #669 review
        # 🟡-1: forestshop-dev is the same threat class and is tracked to be
        # pinned separately; the earlier blanket "only spinbike may ever be
        # pinned" assertion cemented that gap and was removed.)
        for h in airuleset.REMOTE_HOSTS:
            if h.get("host", "").startswith("100."):
                self.assertIsNone(
                    h.get("host_keys"),
                    "%s is a private tailscale host — must not carry a pin"
                    % h.get("name"))
                self.assertEqual(cli_remote.host_key_check_opts(h),
                                 ["-o", "StrictHostKeyChecking=no"])

    def test_spinbike_is_the_only_currently_pinned_host(self):
        # Documents CURRENT fleet state (not a forbid-future assertion): today
        # only spinbike-vps carries a committed pin. A NEW pin (e.g. the
        # forestshop-dev follow-up) updates this expected set.
        pinned = sorted(h["name"] for h in airuleset.REMOTE_HOSTS
                        if h.get("host_keys"))
        self.assertEqual(pinned, ["spinbike-vps"])


class TestSecretDeliveryLegPinsPinnedHost(unittest.TestCase):
    def test_deliver_secret_to_a_pinned_host_uses_strict_yes(self):
        # The shared secret-delivery loop (#659) must pin too, so a future
        # owner-secret leg to a pinned host is never TOFU.
        calls = []

        def run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="", stderr="")
        failed = cli_remote._deliver_secret_to_hosts(
            [PINNED], "v", "cat > ~/x", "secret", run)
        self.assertEqual(failed, [])
        argv = calls[0]
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertTrue(any(a.startswith("UserKnownHostsFile=") for a in argv))
        self.assertNotIn("StrictHostKeyChecking=no", argv)


class TestRealForestshopDevPinned(unittest.TestCase):
    """#679: forestshop-dev is a PUBLIC-DNS target (forestshop-dev.newlevel.media
    = 178.105.89.168, no tailscale) on the SHARED-PASSWORD branch — its two
    REMOTE_HOSTS entries carry no `identity`, so the deploy loop takes the
    no-identity `sshpass -p newlevel` branch. It is the same public-internet
    threat class as spinbike (#669) and WORSE: an un-pinned =no leg hands the
    fleet-shared password to whatever key answers, so a MITM on the path to this
    public DNS host (or a DNS hijack) gets any key accepted AND the password.
    Both entries must carry a committed pin and verify strictly. RED on the
    pre-#679 fleet data (neither entry has `host_keys`)."""

    def _forestshop_entries(self):
        return [h for h in airuleset.REMOTE_HOSTS
                if h.get("host") == "forestshop-dev.newlevel.media"]

    def test_both_forestshop_dev_accounts_present(self):
        names = sorted(h["name"] for h in self._forestshop_entries())
        self.assertEqual(
            names, ["admin@forestshop-dev", "stepan@forestshop-dev"],
            "both documented forestshop-dev accounts must be in the fleet")

    def test_both_forestshop_dev_entries_pinned_and_strict(self):
        entries = self._forestshop_entries()
        self.assertEqual(len(entries), 2)
        for h in entries:
            self.assertTrue(
                h.get("host_keys"),
                "%s (public DNS, shared-password branch) must carry a committed "
                "host-key pin — else a MITM gets any key AND the shared "
                "password" % h["name"])
            opts = cli_remote.host_key_check_opts(h)
            self.assertIn("StrictHostKeyChecking=yes", opts,
                          "%s must verify its host key strictly" % h["name"])
            self.assertTrue(
                any(o.startswith("UserKnownHostsFile=") for o in opts),
                "%s must point ssh at the committed pin" % h["name"])
            self.assertNotIn("StrictHostKeyChecking=no", opts,
                             "%s must not keep the TOFU posture" % h["name"])

    def test_both_forestshop_dev_entries_share_one_pin(self):
        # Same physical box (same DNS name) -> the two accounts pin the SAME
        # keys; a divergence would mean one account trusts a key the other
        # rejects.
        pins = [tuple(h.get("host_keys") or ())
                for h in self._forestshop_entries()]
        self.assertEqual(len(pins), 2)
        self.assertTrue(pins[0], "forestshop-dev must carry a non-empty pin")
        self.assertEqual(pins[0], pins[1],
                         "both forestshop-dev accounts pin the same host keys")


class TestPinFilePathContentAddressed(unittest.TestCase):
    """#680 fold-in (routed through this lane — the shared helper lives in
    cli_remote.py, which #680 is banned from). A PINNED webterm connect child
    ends in os.execvp, so Python's atexit never fires and the RANDOM
    `mkstemp` pin file leaked one /tmp file per pinned connect. The fix makes
    the pin path DETERMINISTIC + content-addressed, so the file count is bounded
    to O(#distinct pins) regardless of atexit. RED on the random-mkstemp code
    (two 'fresh processes' -> two different paths)."""

    ED = ("ssh-ed25519 "
          "AAAAC3NzaC1lZDI1NTE5AAAAIJ4gdjBncONNRHmRw+W8hNFBDkkvEORFWLBxXUWS2r7g")
    ED2 = ("ssh-ed25519 "
           "AAAAC3NzaC1lZDI1NTE5AAAAIF0hQYw2+OticG0PVhzzDeJzghERkK7g+WkqpDihlbiI")

    def _fresh_process_materialize(self, addr, keys):
        # Simulate a brand-new process (a fork/exec webterm child): clear
        # cli_remote's in-process cache so the path is derived purely from the
        # pin content, exactly as a fresh interpreter would.
        cli_remote._PINNED_KNOWN_HOSTS_FILES.clear()
        return cli_remote._materialize_pinned_known_hosts(addr, keys)

    def test_same_pin_across_processes_yields_same_path(self):
        p1 = self._fresh_process_materialize("203.0.113.7", [self.ED])
        p2 = self._fresh_process_materialize("203.0.113.7", [self.ED])
        self.assertEqual(
            p1, p2,
            "the pin file path must be deterministic (content-addressed) so the "
            "atexit-less os.execvp webterm path cannot leak one /tmp file per "
            "pinned connect")

    def test_distinct_pins_yield_distinct_paths(self):
        p1 = self._fresh_process_materialize("203.0.113.7", [self.ED])
        p2 = self._fresh_process_materialize("203.0.113.7", [self.ED2])
        self.assertNotEqual(
            p1, p2, "different pinned keys for one addr must map to different "
            "files (content-addressed)")
        p3 = self._fresh_process_materialize("198.51.100.9", [self.ED])
        self.assertNotEqual(
            p1, p3, "different addresses must map to different files")

    def test_materialized_file_content_survives_the_deterministic_path(self):
        # The deterministic path must still hold the genuine key keyed to the
        # address (the #669 property is unchanged by the naming fix).
        path = self._fresh_process_materialize("203.0.113.7", [self.ED])
        with open(path, encoding="utf-8") as fh:
            self.assertIn("203.0.113.7 " + self.ED, fh.read())


if __name__ == "__main__":
    unittest.main()
