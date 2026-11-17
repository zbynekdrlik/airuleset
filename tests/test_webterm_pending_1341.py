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

    def test_live_accounts_are_untouched(self):
        # No flag anywhere for david1-8 today: the filter is a no-op.
        inv = w.webterm_inventory(p.OWNER)
        self.assertEqual(inv, p.zbynek_inventory())


if __name__ == "__main__":
    unittest.main()
