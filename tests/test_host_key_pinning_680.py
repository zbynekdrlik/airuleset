"""#680 — pin the ssh host key on the NON-push spinbike legs (webterm/burn/onboard).

#669 pinned the raw-public-IP `spinbike-vps` host key on the PUSH path only
(the deploy loop + the shared secret-delivery loop, via
`cli_remote.host_key_check_opts`). THREE other ssh-prefix builders still reach
the SAME public IP (`167.233.245.147`) with `-o StrictHostKeyChecking=no`, so a
first-contact MITM or a silently-rotated host key on those surfaces still hands
the connection to an attacker:

  - `cli_webterm._ssh_interactive_prefix` — the owner's INTERACTIVE webterm
    shell; worse, it also forces `-o UserKnownHostsFile=/dev/null` (no TOFU
    memory at all),
  - `cli_burn._remote_ssh_prefix` — incl. watchdog job 16's HOURLY automated
    fleet fetch,
  - `cli_onboard_exec._ssh_prefix` — `onboard --host spinbike-vps`.

The fix routes each builder through the SAME `cli_remote.host_key_check_opts`
helper + the SAME committed spinbike `host_keys` pin (never re-captured, never
duplicated): a PINNED host gets `StrictHostKeyChecking=yes` + a
`UserKnownHostsFile` pin (refuses unknown AND changed → ssh 255), while every
unpinned tailscale/subdev/gk host keeps the unchanged `=no` posture (webterm
additionally keeps its own `UserKnownHostsFile=/dev/null` for unpinned hosts).

RED lock: each leg builder, given a PINNED remote/entry, must build argv with
`StrictHostKeyChecking=yes` + a `UserKnownHostsFile`, NEVER `=no`; an UNPINNED
remote must keep `=no` (regression). Fails by assertion on the pre-fix `=no`
code. A real-fleet case proves the ACTUAL committed spinbike entry engages the
pin on every leg (so no key literal is duplicated — the one source is reused).
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset            # noqa: E402  facade (REMOTE_HOSTS / AUTHORITY_BY_USER)
import cli_burn             # noqa: E402
import cli_fleet            # noqa: E402
import cli_onboard_exec     # noqa: E402
import cli_webterm as w     # noqa: E402


# A pinned raw-public-IP fixture. The pin blob is deliberately SHORT (an <32-char
# base64 half) so `block-sensitive-staging.sh` never flags it — `_materialize`
# accepts any non-empty `<type> <blob>` line, so the value need not be a real key.
PINNED = {
    "name": "pin-vps", "host": "203.0.113.7", "user": "newlevel",
    "repo_path": "~/devel/airuleset", "identity": "~/.ssh/pin_vps",
    "host_keys": ["ssh-ed25519 AAAAfakepin"],
}
PINNED_NOIDENT = {
    "name": "pin-vps2", "host": "203.0.113.8", "user": "newlevel",
    "repo_path": "~/devel/airuleset",
    "host_keys": ["ssh-ed25519 AAAAfakepin2"],
}
# Unpinned private hosts (no `host_keys`) — must keep the `=no` posture.
PLAIN_IDENT = {
    "name": "gk", "host": "100.90.94.41", "user": "gatekeeper",
    "repo_path": "~/devel/airuleset", "identity": "~/.ssh/gk",
}
PLAIN_NOIDENT = {
    "name": "montalu1@subdev", "host": "100.118.174.27", "user": "montalu1",
    "repo_path": "~/devel/airuleset",
}


def _assert_pinned(t, argv):
    joined = " ".join(argv)
    t.assertIn("StrictHostKeyChecking=yes", argv,
               "a pinned leg must verify the host key strictly: %s" % joined)
    t.assertTrue(any(a.startswith("UserKnownHostsFile=") for a in argv),
                 "a pinned leg must point ssh at the committed pin: %s" % joined)
    t.assertNotIn("StrictHostKeyChecking=no", argv,
                  "a pinned leg must NOT keep the TOFU posture: %s" % joined)


def _assert_unpinned(t, argv):
    joined = " ".join(argv)
    t.assertIn("StrictHostKeyChecking=no", argv,
               "an unpinned private host must keep =no: %s" % joined)
    t.assertNotIn("StrictHostKeyChecking=yes", argv,
                  "an unpinned host must not be pinned: %s" % joined)


class TestBurnLegPin(unittest.TestCase):
    def test_pinned_identity_branch(self):
        _assert_pinned(self, cli_burn._remote_ssh_prefix(PINNED))

    def test_pinned_sshpass_branch(self):
        _assert_pinned(self, cli_burn._remote_ssh_prefix(PINNED_NOIDENT))

    def test_unpinned_identity_stays_no(self):
        _assert_unpinned(self, cli_burn._remote_ssh_prefix(PLAIN_IDENT))

    def test_unpinned_sshpass_stays_no(self):
        _assert_unpinned(self, cli_burn._remote_ssh_prefix(PLAIN_NOIDENT))

    def test_pinned_keeps_batchmode_hardening(self):
        # #342's BatchMode=yes retry-cap must survive the pin wiring.
        argv = cli_burn._remote_ssh_prefix(PINNED)
        self.assertIn("BatchMode=yes", argv)

    def test_pinned_sshpass_keeps_prompt_cap(self):
        argv = cli_burn._remote_ssh_prefix(PINNED_NOIDENT)
        self.assertIn("NumberOfPasswordPrompts=1", argv)


class TestOnboardLegPin(unittest.TestCase):
    def test_pinned(self):
        _assert_pinned(self, cli_onboard_exec._ssh_prefix(PINNED))

    def test_unpinned_stays_no(self):
        _assert_unpinned(self, cli_onboard_exec._ssh_prefix(PLAIN_NOIDENT))

    def test_pinned_keeps_batchmode_and_timeout(self):
        # The #583 "refuse on unreachable" hardening must survive the pin wiring.
        argv = cli_onboard_exec._ssh_prefix(PINNED)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ConnectTimeout=10", argv)


class TestWebtermLegPin(unittest.TestCase):
    def test_pinned_entry_engages_pin(self):
        entry = {"local": False, "user": "newlevel", "host": "203.0.113.7",
                 "identity": "~/.ssh/pin_vps",
                 "host_keys": ["ssh-ed25519 AAAAfakepin"]}
        _assert_pinned(self, w._ssh_interactive_prefix(entry))

    def test_pinned_entry_keeps_pty_and_timeout(self):
        entry = {"local": False, "user": "newlevel", "host": "203.0.113.7",
                 "identity": "~/.ssh/pin_vps",
                 "host_keys": ["ssh-ed25519 AAAAfakepin"]}
        prefix = w._ssh_interactive_prefix(entry)
        self.assertIn("-t", prefix)                      # force a PTY
        self.assertIn("ConnectTimeout=10", prefix)

    def test_unpinned_entry_keeps_no_and_devnull(self):
        # webterm's unpinned posture is preserved EXACTLY (=no + /dev/null) —
        # the interactive owner shell to a private host is unchanged.
        entry = {"local": False, "user": "montalu1", "host": "100.118.174.27",
                 "identity": None}
        prefix = w._ssh_interactive_prefix(entry)
        _assert_unpinned(self, prefix)
        self.assertIn("UserKnownHostsFile=/dev/null", prefix)

    def test_unpinned_entry_none_host_keys_is_unpinned(self):
        # An explicit `host_keys: None` is the same as absent (unpinned).
        entry = {"local": False, "user": "montalu1", "host": "100.118.174.27",
                 "identity": None, "host_keys": None}
        _assert_unpinned(self, w._ssh_interactive_prefix(entry))


class TestWebtermInventoryThreadsHostKeys(unittest.TestCase):
    def test_inventory_carries_host_keys_from_fleet(self):
        # The connect child can NOT import the fleet — the inventory JSON is its
        # allowlist, so the PUBLIC pin must be threaded through it at generation
        # time, and the argv built from that entry must engage the pin.
        hosts = [PINNED, PLAIN_NOIDENT]
        with m.patch.object(airuleset, "REMOTE_HOSTS", hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}):
            inv = {e["id"]: e for e in w.webterm_inventory()}
        self.assertEqual(inv["pin-vps"].get("host_keys"),
                         ["ssh-ed25519 AAAAfakepin"])
        _assert_pinned(self, w._ssh_interactive_prefix(inv["pin-vps"]))

    def test_inventory_unpinned_host_has_no_pin(self):
        hosts = [PINNED, PLAIN_NOIDENT]
        with m.patch.object(airuleset, "REMOTE_HOSTS", hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}):
            inv = {e["id"]: e for e in w.webterm_inventory()}
        # a plain host carries no pin, so its leg stays =no + /dev/null
        entry = inv["montalu1-subdev"]
        self.assertIsNone(entry.get("host_keys"))
        prefix = w._ssh_interactive_prefix(entry)
        _assert_unpinned(self, prefix)
        self.assertIn("UserKnownHostsFile=/dev/null", prefix)


class TestRealFleetSpinbikePinned(unittest.TestCase):
    """The ACTUAL committed spinbike `REMOTE_HOSTS` entry must engage the pin on
    every re-wired leg — proving no key literal is duplicated (the one committed
    source is reused). A real private target (dev2 tailscale) must stay `=no`."""

    def setUp(self):
        self.spin = next(h for h in cli_fleet.REMOTE_HOSTS
                         if h.get("name") == "spinbike-vps")
        self.dev2 = next(h for h in cli_fleet.REMOTE_HOSTS
                         if h.get("name") == "dev2")

    def test_spinbike_carries_committed_pin(self):
        self.assertTrue(self.spin.get("host_keys"),
                        "the committed spinbike pin (from #669) must exist")

    def test_burn_leg_pins_real_spinbike(self):
        _assert_pinned(self, cli_burn._remote_ssh_prefix(self.spin))

    def test_onboard_leg_pins_real_spinbike(self):
        _assert_pinned(self, cli_onboard_exec._ssh_prefix(self.spin))

    def test_webterm_inventory_pins_real_spinbike(self):
        inv = {e["id"]: e for e in w.webterm_inventory()}
        entry = next(e for e in inv.values()
                     if e.get("host") == "167.233.245.147")
        _assert_pinned(self, w._ssh_interactive_prefix(entry))

    def test_dev2_tailscale_leg_stays_no(self):
        _assert_unpinned(self, cli_burn._remote_ssh_prefix(self.dev2))
        _assert_unpinned(self, cli_onboard_exec._ssh_prefix(self.dev2))


if __name__ == "__main__":
    unittest.main()
