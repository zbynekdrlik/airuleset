"""#1183: the `fohmixer@dev1` project account + webterm tabs for the owner, Marek
and Timo (the first consumer of the #1184 per-project account policy).

The boundary this pins (owner request 2026-09-29: "nech ma len z dev1 pristup na
fohmixer tmux projekt" + ROZHODNUTÉ: one account per PROJECT, tabs for owner,
Marek AND Timo):

  * the NEW `timo` lane has exactly ONE tab — `fohmixer` (fohmixer@dev1 over
    tailscale, the dedicated timo lane key, the shared project session). Its
    connect allowlist can never resolve any other fleet id;
  * Timo has NO ssh identity, NO unix account, NO password — webterm-only
    (#869): his whole authorization is the Cloudflare Access allow-list
    `timotej.kam@gmail.com` (deny-by-default);
  * the lane is born on the CONTROLLER (#870 F4c topology) behind the ONE
    shared controller tunnel; DNS is a #983 managed CNAME gated on the Access
    app;
  * the owner and Marek dashboards gain a `fohmixer` tab (their own dedicated
    lane keys);
  * every fohmixer tab is drift-locked to the ONE declaration
    (cli_account_bootstrap.SERVICE_ACCOUNTS["fohmixer"]).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_cloudflare_dns as dns  # noqa: E402
import cli_fleet  # noqa: E402
import cli_webterm as w  # noqa: E402
import cli_webterm_access as access  # noqa: E402
import cli_webterm_profiles as p  # noqa: E402


def _fohmixer(inv):
    return [e for e in inv if e["id"] == "fohmixer"]


class TestTimoInventory(unittest.TestCase):

    def test_exactly_one_tab(self):
        self.assertEqual([e["id"] for e in p.timo_inventory()], ["fohmixer"])

    def test_the_tab_is_fohmixer_at_dev1(self):
        (e,) = p.timo_inventory()
        self.assertFalse(e["local"])
        self.assertEqual(e["host"], p.FOHMIXER_HOST)
        self.assertEqual(e["user"], "fohmixer")
        self.assertEqual(e["identity"], p.WEBTERM_TIMO_IDENTITY)
        self.assertEqual(e["preferred"], "fohmixer")
        self.assertEqual(e["start_dir_chain"], ["devel/fohmixer"])
        # cross-tenant OBSERVE shape: never a U-status read (#703)
        self.assertIsNot(e.get("u_tenant"), True)

    def test_dedicated_identity_never_a_fleet_key(self):
        self.assertEqual(p.WEBTERM_TIMO_IDENTITY, "~/.secrets/webterm_timo_ed25519")
        for bad in ("gatekeeper", "push", "id_ed25519"):
            self.assertNotIn(bad, p.WEBTERM_TIMO_IDENTITY)

    def test_profile_routing(self):
        self.assertEqual(p.profile_inventory(p.TIMO, []), p.timo_inventory())
        self.assertEqual(w.webterm_inventory(profile=p.TIMO), p.timo_inventory())
        self.assertEqual(p.allowed_ids(p.TIMO, []), {"fohmixer"})
        self.assertEqual(p.u_tenant_entries(p.TIMO), [])

    def test_connect_allowlist_refuses_every_other_id(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "timo-inv.json"
            f.write_text(json.dumps(p.timo_inventory()), encoding="utf-8")
            for foreign in ("ar", "claudy", "dev1", "dev2", "gatekeeper",
                            "montalu1-subdev", "miva1-subdev", "david1-subdev",
                            "spinbike-vps", "forestshop"):
                with mock.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                        mock.patch.object(w.os, "execvp") as ex:
                    rc = w.connect_main([foreign])
                self.assertEqual(rc, 2, foreign)
                ex.assert_not_called()

    def test_fohmixer_execs_ssh_with_the_dedicated_timo_key(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "timo-inv.json"
            f.write_text(json.dumps(p.timo_inventory()), encoding="utf-8")
            with mock.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                    mock.patch.object(w.os, "execvp") as ex:
                w.connect_main(["fohmixer"])
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn(os.path.expanduser(p.WEBTERM_TIMO_IDENTITY), argv)
        self.assertNotIn("sshpass", argv)
        self.assertIn("fohmixer@" + p.FOHMIXER_HOST, argv)
        self.assertIn("P=fohmixer; ", " ".join(argv))


class TestTimoDashboardAndLane(unittest.TestCase):

    def test_dashboard_tabs(self):
        self.assertEqual(w.WEBTERM_DASHBOARD_TABS["timo"], ["fohmixer"])

    def test_lane_is_hosted_on_the_controller(self):
        self.assertEqual(p.LANE_HOST["timo"], "controller")
        self.assertEqual(w._HUMAN_TO_MODULE["timo"], "cli_webterm_timo")
        self.assertIn(p.TIMO, p.profile_for_host_set("controller"))

    def test_spec_is_a_shared_tunnel_observer_lane(self):
        import cli_webterm_timo as t
        spec = t._spec()
        self.assertEqual(spec.name, "timo")
        self.assertEqual(spec.profile, p.TIMO)
        self.assertTrue(spec.shared_tunnel)
        self.assertIsNone(spec.identity_key)
        self.assertEqual(spec.dashboard_human, "timo")
        self.assertEqual(spec.tunnel_hostname, "timo.newlevel.media")
        self.assertIn("_TIMO_GO_LIVE", dir(t))

    def test_fresh_ports_and_sockets(self):
        import importlib
        import cli_webterm_timo as t
        mine = t._spec()
        for human, mod_name in w._HUMAN_TO_MODULE.items():
            if human == "timo":
                continue
            other = importlib.import_module(mod_name)._spec()
            self.assertNotEqual(mine.ttyd_port, other.ttyd_port, human)
            self.assertNotEqual(mine.gateway_port, other.gateway_port, human)
            self.assertNotEqual(mine.gateway_sock_basename,
                                other.gateway_sock_basename, human)
            self.assertNotEqual(mine.ttyd_sock_basename,
                                other.ttyd_sock_basename, human)
            self.assertNotEqual(mine.gateway_service_name,
                                other.gateway_service_name, human)

    def test_go_live_is_a_checklist(self):
        import cli_webterm_timo as t
        for needle in ("webterm_timo_ed25519", "timotej.kam@gmail.com",
                       "account-bootstrap --render fohmixer",
                       "WEBTERM_CONTROLLER_LANE_PUBKEYS"):
            self.assertIn(needle, t._TIMO_GO_LIVE)


class TestTimoHasNoSshIdentity(unittest.TestCase):
    """Webterm-only (#869): Timo gets no account, no key, no password."""

    def test_no_fleet_account(self):
        users = {h["user"] for h in cli_fleet.REMOTE_HOSTS}
        self.assertNotIn("timo", users)
        self.assertNotIn("timo", cli_fleet.AUTHORITY_BY_USER)
        self.assertNotIn("timo", cli_fleet.FULL_AUTHORITY_USERS)

    def test_no_owner_key(self):
        from cli_owner_keys import OWNER_PUBKEYS
        self.assertFalse(any("timo" in k for k in OWNER_PUBKEYS))

    def test_access_allow_list_is_exactly_timo(self):
        app = access.WEBTERM_ACCESS_APPS["timo"]
        self.assertEqual(app["hostname"], "timo.newlevel.media")
        self.assertEqual(app["allowed_emails"], ["timotej.kam@gmail.com"])

    def test_timo_email_is_on_no_other_app(self):
        for name, app in access.WEBTERM_ACCESS_APPS.items():
            if name != "timo":
                self.assertNotIn("timotej.kam@gmail.com", app["allowed_emails"],
                                 name)


class TestTimoDns(unittest.TestCase):

    def test_managed_cname_to_the_controller_tunnel_gated_on_access(self):
        rec = [r for r in dns.MANAGED_RECORDS
               if r["name"] == "timo.newlevel.media"]
        self.assertEqual(len(rec), 1)
        r = rec[0]
        self.assertEqual(r["type"], "CNAME")
        self.assertTrue(r["proxied"])
        self.assertEqual(r["content"],
                         w.CONTROLLER_TUNNEL_UUID + ".cfargotunnel.com")
        self.assertEqual(r["requires_access_hostname"], "timo.newlevel.media")


class TestOwnerAndMarekFohmixerTabs(unittest.TestCase):

    def test_owner_dashboard_has_fohmixer(self):
        self.assertIn("fohmixer", w.WEBTERM_DASHBOARD_TABS["zbynek"])
        (e,) = _fohmixer(p.zbynek_inventory())
        self.assertEqual(e["identity"], p.WEBTERM_ZBYNEK_IDENTITY)
        self.assertEqual(e["user"], "fohmixer")
        self.assertEqual(e["host"], p.FOHMIXER_HOST)
        self.assertNotIn("collect_identity", e)
        self.assertIsNot(e.get("u_tenant"), True)

    def test_marek_dashboard_has_fohmixer(self):
        self.assertIn("fohmixer", w.WEBTERM_DASHBOARD_TABS["marek"])
        (e,) = _fohmixer(p.marek_inventory())
        self.assertEqual(e["identity"], p.WEBTERM_MAREK_IDENTITY)
        self.assertEqual(e["user"], "fohmixer")

    def test_fohmixer_tab_follows_claudy(self):
        for human in ("zbynek", "marek"):
            tabs = w.WEBTERM_DASHBOARD_TABS[human]
            self.assertEqual(tabs.index("fohmixer"), tabs.index("claudy") + 1,
                             human)

    def test_david_and_dominika_get_no_fohmixer_tab(self):
        self.assertEqual(_fohmixer(p.david_inventory()), [])
        self.assertEqual(_fohmixer(p.dominika_inventory()), [])


class TestDeclarationDriftLock(unittest.TestCase):
    """Every fohmixer tab == the ONE declaration (host, user, humans, session)."""

    INVENTORIES = {
        "zbynek": p.zbynek_inventory,
        "marek": p.marek_inventory,
        "timo": p.timo_inventory,
        "david": p.david_inventory,
        "dominika": p.dominika_inventory,
    }

    def test_humans_with_a_tab_equal_the_declared_humans(self):
        spec = bootstrap.account_spec("fohmixer")
        with_tab = {h for h, inv in self.INVENTORIES.items() if _fohmixer(inv())}
        self.assertEqual(with_tab, set(spec["webterm_sessions"]))

    def test_every_tab_matches_the_declared_session(self):
        spec = bootstrap.account_spec("fohmixer")
        for human, sess in spec["webterm_sessions"].items():
            (e,) = _fohmixer(self.INVENTORIES[human]())
            self.assertEqual(e["preferred"], sess["preferred"], human)
            self.assertEqual(e["start_dir_chain"], sess["start_dir_chain"], human)
            self.assertEqual(e["user"], "fohmixer", human)

    def test_host_matches_the_fleet_dev1(self):
        dev1 = next(h for h in cli_fleet.REMOTE_HOSTS if h["name"] == "dev1")
        self.assertEqual(p.FOHMIXER_HOST, dev1["host"])
        self.assertEqual(bootstrap.account_spec("fohmixer")["host"], "dev1")


if __name__ == "__main__":
    unittest.main()
