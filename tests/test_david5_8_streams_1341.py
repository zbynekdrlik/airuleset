"""#1341 (owner 16.11.2026): four more sub-dev streams david5-david8 on subdev,
the same shape as david2-david4 (#326 registration, #869 webterm-only, #934
webterm tab), with webterm tabs on the zbynek, marek and david dashboards.

One lock per registry a david stream needs, so a future family member that
misses one list fails here by name.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import airuleset
import cli_drop_gateway
import cli_fleet
import cli_odoo_board
import cli_webterm
import cli_webterm_only
import cli_webterm_profiles as p
import notify
import watchdog as wd

NEW = ("david5", "david6", "david7", "david8")
REPO = Path(airuleset.REPO_DIR)


class TestFleetRegistry(unittest.TestCase):
    def test_remote_hosts_entries(self):
        for u in NEW:
            hits = [r for r in cli_fleet.REMOTE_HOSTS if r["name"] == "%s@subdev" % u]
            self.assertEqual(len(hits), 1, u)
            e = hits[0]
            self.assertEqual(e["host"], "100.118.174.27")
            self.assertEqual(e["user"], u)
            self.assertEqual(e["identity"], "~/.secrets/gatekeeper_access_ed25519")
            self.assertEqual(e["windows"], [{"name": "d" + u[5:],
                                             "cwd": "~/devel/odoo/odoo-erp",
                                             "role": None}])

    def test_authority_and_derived_stream_sets(self):
        for u in NEW:
            self.assertEqual(cli_fleet.AUTHORITY_BY_USER[u], "fork-no-merge", u)
            self.assertIn(u, wd._REDUCED_STREAM_USERS)
            self.assertNotIn(u, cli_fleet.WEBTERM_OBSERVER_USERS)

    def test_webterm_only(self):
        for u in NEW:
            self.assertIn(u, cli_fleet.WEBTERM_ONLY_USERS)
            self.assertEqual(cli_webterm_only.SUBDEV_ACCOUNTS_FALLBACK[u],
                             "gatekeeper_access_ed25519")

    def test_notify_routes_to_david(self):
        for u in NEW:
            self.assertEqual(notify.STREAM_NOTIFY_OWNER[u], "david")
            self.assertEqual(notify.stream_redirect(u), "david")

    def test_no_client_board(self):
        for u in NEW:
            self.assertFalse(cli_odoo_board.board_enabled(u), u)


class TestControllerLaneKeys(unittest.TestCase):
    def test_each_dashboard_human_key_is_authorized(self):
        for human in ("zbynek", "david", "marek"):
            targets = cli_webterm_only._CONTROLLER_LANE_WEBTERM_ONLY_TARGETS[human]
            blob = cli_webterm_only.WEBTERM_CONTROLLER_LANE_PUBKEYS[human].split()[1]
            for u in NEW:
                self.assertIn(u, targets, (human, u))
                keys = cli_webterm_only.desired_keys_for_user(u)
                self.assertTrue(any(blob in str(k) for k in keys), (human, u))


class TestDashboards(unittest.TestCase):
    def _ids(self, inv):
        return {e["id"]: e for e in inv}

    def test_zbynek_tabs_follow_david4(self):
        tabs = cli_webterm.WEBTERM_DASHBOARD_TABS["zbynek"]
        i = tabs.index("david4-subdev")
        self.assertEqual(tabs[i + 1:i + 5], ["%s-subdev" % u for u in NEW])
        inv = self._ids(p.zbynek_inventory())
        for u in NEW:
            e = inv["%s-subdev" % u]
            self.assertEqual(e["user"], u)
            self.assertEqual(e["identity"], p.WEBTERM_ZBYNEK_IDENTITY)
            self.assertTrue(e["u_tenant"])

    def test_marek_observe_tabs(self):
        tabs = cli_webterm.WEBTERM_DASHBOARD_TABS["marek"]
        i = tabs.index("david3-subdev")
        self.assertEqual(tabs[i + 1:i + 5], ["%s-subdev" % u for u in NEW])
        inv = self._ids(p.marek_inventory())
        for u in NEW:
            e = inv["%s-subdev" % u]
            self.assertEqual(e["user"], u)
            self.assertEqual(e["identity"], p.WEBTERM_MAREK_IDENTITY)
            self.assertNotIn("u_tenant", e)   # David's stream, not marek's realm

    def test_david_tabs(self):
        self.assertEqual(cli_webterm.WEBTERM_DASHBOARD_TABS["david"][4:],
                         ["%s-subdev" % u for u in NEW])
        inv = self._ids(p.david_inventory())
        for u in NEW:
            self.assertEqual(inv[u]["user"], u)
            self.assertEqual(inv[u]["identity"], p.WEBTERM_DAVID_IDENTITY)

    def test_rendered_aliases(self):
        want = ["d5", "d6", "d7", "d8"]
        for human, inv in (("zbynek", p.zbynek_inventory()),
                           ("marek", p.marek_inventory())):
            html = cli_webterm.render_dashboard_html(
                inv, ttyd_base="/t", human=human, term_grid=(176, 51))
            aliases = re.findall(r'<span class="al">([^<]+)</span>', html)
            for a in want:
                self.assertIn(a, aliases, (human, a))


class TestDropLanes(unittest.TestCase):
    def test_fixed_ports_and_hosts(self):
        for n, u in enumerate(NEW):
            lane = cli_drop_gateway.DROP_LANES[("subdev", u)]
            self.assertEqual(lane.port, 8911 + n)
            self.assertEqual(lane.host, "drop-subdev-%s.newlevel.media" % u)
            self.assertTrue(lane.access)


class TestSshMisuseHook(unittest.TestCase):
    def _run(self, command):
        d = tempfile.mkdtemp()
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env = dict(os.environ, HOME=home)
        payload = json.dumps({"tool_input": {"command": command}})
        return subprocess.run(["bash", str(REPO / "hooks" / "block-subdev-ssh-misuse.sh")],
                              input=payload, text=True, capture_output=True,
                              cwd=d, timeout=30, env=env)

    def test_identity_required_and_accepted(self):
        for u in NEW:
            r = self._run('ssh %s@subdev "ls"' % u)
            self.assertEqual(r.returncode, 2, u + r.stdout)
            r = self._run('ssh -i ~/.secrets/gatekeeper_access_ed25519 %s@subdev "ls"' % u)
            self.assertEqual(r.returncode, 0, u + r.stdout + r.stderr)


class TestDoctrine(unittest.TestCase):
    def test_machine_identities_names_david1_8(self):
        src = (REPO / "modules" / "core" / "machine-identities.md").read_text(encoding="utf-8")
        self.assertIn("david1-8 + dominika = webterm-ONLY", src)


if __name__ == "__main__":
    unittest.main()
