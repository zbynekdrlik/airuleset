"""#1341 incident (16.11.2026): a pending account must never be ssh'd by webterm.

The first #1341 merge declared david5-david8 with `"pending": True` in
`cli_fleet.REMOTE_HOSTS`, yet the controller kept ssh-ing them ('Invalid user
david5..8' on subdev) and fail2ban banned the controller. `_deployable_hosts`
honours `pending`/`paused`, but the webterm inventories are declarative leaves
(`cli_webterm_profiles.*_inventory()`) that never read REMOTE_HOSTS, so every
webterm ssh path — the U-dot collectors the gateways respawn while a dashboard
is open, the tab connect allowlist, the lane inventory JSON — reached the
missing accounts.

These tests mark an account pending (or paused) through a REMOTE_HOSTS patch
and assert no webterm entry for it survives.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cli_fleet
import cli_webterm as w
import cli_webterm_profiles as p

SUBDEV = "100.118.174.27"


def _flagged(user, **flag):
    """REMOTE_HOSTS with `user@subdev` carrying `flag` (pending/paused)."""
    hosts = copy.deepcopy(cli_fleet.REMOTE_HOSTS)
    hits = [h for h in hosts if h.get("user") == user and h.get("host") == SUBDEV]
    assert len(hits) == 1, (user, hits)
    hits[0].update(flag)
    return hosts


def _users(entries):
    return {e.get("user") for e in entries}


class TestPendingAccountLeavesWebterm(unittest.TestCase):
    def test_pending_account_absent_from_every_profile_inventory(self):
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", _flagged("david5", pending=True)):
            for prof in (p.OWNER, p.DAVID, p.MAREK):
                users = _users(w.webterm_inventory(prof))
                self.assertNotIn("david5", users, prof)
                self.assertIn("david6", users, prof)   # only the flagged one goes

    def test_paused_account_absent_too(self):
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS",
                               _flagged("david6", paused="test 17.11.2026")):
            self.assertNotIn("david6", _users(w.webterm_inventory(p.OWNER)))

    def test_owner_u_collector_never_reads_a_pending_account(self):
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", _flagged("david7", pending=True)):
            self.assertNotIn("david7", _users(w._owner_u_entries()))

    def test_lane_u_collector_never_reads_a_pending_account(self):
        seen = []
        out = Path(tempfile.mkdtemp()) / "u.json"
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", _flagged("david8", pending=True)), \
                mock.patch.object(w, "collect_fleet_u",
                                  lambda entries: seen.extend(entries) or {}), \
                mock.patch.object(w, "webterm_lane_u_status_path",
                                  lambda prof: out):
            w.cmd_webterm_u_collect(["--lane", p.DAVID])
        self.assertTrue(seen)
        self.assertNotIn("david8", _users(seen))

    def test_unflagged_accounts_are_untouched(self):
        # Review: hermetic, a REMOTE_HOSTS copy with every flag stripped.
        clean = [{k: v for k, v in h.items() if k not in ("pending", "paused")}
                 for h in cli_fleet.REMOTE_HOSTS]
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", clean):
            self.assertEqual(w.webterm_inventory(p.OWNER), p.zbynek_inventory())

    def test_lane_on_subdev_uses_loopback_and_still_filters(self):
        # Review: a lane hosted ON subdev reaches its accounts at 127.0.0.1,
        # never the REMOTE_HOSTS tailscale address.
        lanes = dict(p.LANE_HOST, david="subdev")
        with mock.patch.object(p, "LANE_HOST", lanes), \
                mock.patch.object(cli_fleet, "REMOTE_HOSTS", _flagged("david5", pending=True)):
            inv = w.webterm_inventory(p.DAVID)
        self.assertEqual({e["host"] for e in inv if e.get("user") == "david6"},
                         {p.SUBDEV_LOCAL})
        self.assertNotIn("david5", _users(inv))


class TestOtherFleetLoopsSkipPending(unittest.TestCase):
    """Review: the hand-run fleet loops that walk REMOTE_HOSTS themselves."""

    def test_nudges_fleet_never_runs_on_a_pending_account(self):
        import airuleset
        seen = []
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", _flagged("david5", pending=True)):
            airuleset._nudges_fleet("status", runner=lambda e: seen.append(e) or ("", 1))
        self.assertTrue(seen)
        self.assertNotIn("david5", _users(seen))

    def test_key_rotation_never_targets_a_pending_account(self):
        # A paused account stays a target on purpose: every rotation phase
        # skips it live and records key debt (#851); a pending one has no key.
        import cli_key_rotation
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", _flagged("david5", pending=True)):
            users = {e.get("user") for _k, e, _v in cli_key_rotation._rotation_targets()}
        self.assertIn("david7", users)
        self.assertNotIn("david5", users)


if __name__ == "__main__":
    unittest.main()
