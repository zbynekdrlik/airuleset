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


if __name__ == "__main__":
    unittest.main()
