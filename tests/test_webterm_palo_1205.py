"""#1205: the `palo` webterm lane — palo.newlevel.media, ONE tab: montalu6@subdev.

Owner request 2026-09-30, verbatim: "Potrebujem vytvorit novy webterm, bude pre
palo@montalu.sk a bude tam len montalu6". The boundary this pins:

  * the NEW `palo` lane has exactly ONE tab — `montalu6-subdev` (montalu6@subdev
    over the tailnet, the dedicated palo lane key, the montalu6 session). Its
    connect allowlist can never resolve any other fleet id;
  * the tab is the SAME session + start dir the owner's montalu6 tab attaches
    (one `_remote_command` render), but WITHOUT the owner-only fields
    (`u_tenant`, `collect_identity` = the owner's push key);
  * Palo has NO ssh identity, NO unix account, NO password — webterm-only
    (#869): his whole authorization is the Cloudflare Access allow-list
    `palo@montalu.sk` (deny-by-default);
  * the lane is born on the CONTROLLER behind the ONE shared tunnel (the
    ingress rule is derived from the lane spec); DNS is a #983 managed CNAME
    gated on the Access app;
  * the lane key reaches montalu6 ONLY as the `restrict,pty,command="…"` line
    the owner's montalu6 line already has — nothing broader — and the render
    REFUSES until the key is minted at go-live;
  * the timo lane (#1183) moved onto the shared controller-lane helper with a
    value-identical spec.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_cloudflare_dns as dns  # noqa: E402
import cli_fleet  # noqa: E402
import cli_webterm as w  # noqa: E402
import cli_webterm_access as access  # noqa: E402
import cli_webterm_lane as lane  # noqa: E402
import cli_webterm_only as wo  # noqa: E402
import cli_webterm_profiles as p  # noqa: E402

PALO_EMAIL = "palo@montalu.sk"
# A fake lane pubkey for the pre-go-live render (the real one is minted at
# go-live). Built at runtime and digit-free past the type, so no secret scanner
# mistakes it for key material (#961 follow-on lesson).
_FAKE_BLOB = "AAAA" + "P" * 64
_FAKE_PALO = "ssh-ed25519 " + _FAKE_BLOB + " webterm-palo-controller"


def _owner_m6():
    (e,) = [e for e in p.zbynek_inventory() if e["id"] == "montalu6-subdev"]
    return e


def _with_palo_key():
    return mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS,
                           {"palo": _FAKE_PALO})


class TestPaloInventory(unittest.TestCase):

    def test_exactly_one_tab(self):
        self.assertEqual([e["id"] for e in p.palo_inventory()],
                         ["montalu6-subdev"])

    def test_the_tab_is_montalu6_at_subdev_over_the_tailnet(self):
        (e,) = p.palo_inventory()
        self.assertFalse(e["local"])
        self.assertEqual(e["host"], p.SUBDEV_TAILSCALE_HOST)
        self.assertEqual(e["user"], "montalu6")
        self.assertEqual(e["preferred"], "montalu6")
        self.assertEqual(e["identity"], p.WEBTERM_PALO_IDENTITY)

    def test_no_owner_only_field(self):
        # u_tenant = a within-tenant U read (#703); collect_identity = the
        # OWNER's push key. Neither may ever sit in palo's inventory.
        (e,) = p.palo_inventory()
        self.assertIsNot(e.get("u_tenant"), True)
        self.assertNotIn("collect_identity", e)
        self.assertNotIn("host_keys", e)

    def test_same_tab_shape_as_the_owner_montalu6_tab(self):
        (e,) = p.palo_inventory()
        owner = _owner_m6()
        for key in ("id", "label", "kind", "local", "host", "user", "preferred"):
            self.assertEqual(e[key], owner[key], key)
        self.assertEqual(e.get("start_dir_chain"), owner.get("start_dir_chain"))

    def test_same_session_and_start_dir_as_the_owner_tab(self):
        (e,) = p.palo_inventory()
        owner = _owner_m6()
        mine = w._remote_command(e["preferred"], e.get("start_dir_chain"),
                                 user=e["user"])
        theirs = w._remote_command(owner["preferred"],
                                   owner.get("start_dir_chain"),
                                   user=owner["user"])
        self.assertEqual(mine, theirs)
        self.assertTrue(mine.startswith("P=montalu6; "))

    def test_dedicated_identity_never_a_fleet_or_other_lane_key(self):
        self.assertEqual(p.WEBTERM_PALO_IDENTITY,
                         "~/.secrets/webterm_palo_ed25519")
        for bad in ("gatekeeper", "push", "id_ed25519"):
            self.assertNotIn(bad, p.WEBTERM_PALO_IDENTITY)
        for other in (p.WEBTERM_ZBYNEK_IDENTITY, p.WEBTERM_TIMO_IDENTITY,
                      p.WEBTERM_MAREK_IDENTITY, p.WEBTERM_DOMINIKA_IDENTITY,
                      p.WEBTERM_DAVID_IDENTITY, p.ZBYNEK_COLLECT_IDENTITY):
            self.assertNotEqual(p.WEBTERM_PALO_IDENTITY, other)

    def test_profile_routing(self):
        self.assertEqual(p.PALO, "palo")
        self.assertEqual(p.profile_inventory(p.PALO, []), p.palo_inventory())
        self.assertEqual(w.webterm_inventory(profile=p.PALO), p.palo_inventory())
        self.assertEqual(p.allowed_ids(p.PALO, []), {"montalu6-subdev"})
        self.assertEqual(p.u_tenant_entries(p.PALO), [])

    def _inv_file(self, d):
        f = Path(d) / "palo-inv.json"
        f.write_text(json.dumps(p.palo_inventory()), encoding="utf-8")
        return f

    def test_connect_allowlist_refuses_every_other_id(self):
        with tempfile.TemporaryDirectory() as d:
            f = self._inv_file(d)
            for foreign in ("ar", "claudy", "fohmixer", "dev1", "dev2",
                            "gatekeeper", "montalu1-subdev", "montalu5-subdev",
                            "montalu7-subdev", "montalu8-subdev", "miva1-subdev",
                            "david1-subdev", "spinbike-vps", "forestshop",
                            "montalu6"):
                with mock.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                        mock.patch.object(w.os, "execvp") as ex:
                    rc = w.connect_main([foreign])
                self.assertEqual(rc, 2, foreign)
                ex.assert_not_called()

    def test_montalu6_execs_ssh_with_the_dedicated_palo_key(self):
        with tempfile.TemporaryDirectory() as d:
            f = self._inv_file(d)
            with mock.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                    mock.patch.object(w.os, "execvp") as ex:
                w.connect_main(["montalu6-subdev"])
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn(os.path.expanduser(p.WEBTERM_PALO_IDENTITY), argv)
        self.assertNotIn("sshpass", argv)
        self.assertIn("montalu6@" + p.SUBDEV_TAILSCALE_HOST, argv)
        self.assertIn("P=montalu6; ", " ".join(argv))


class TestPaloLane(unittest.TestCase):

    def test_dashboard_tabs(self):
        self.assertEqual(w.WEBTERM_DASHBOARD_TABS["palo"], ["montalu6-subdev"])

    def test_owner_marek_and_timo_tab_lists_unchanged(self):
        self.assertIn("montalu6-subdev", w.WEBTERM_DASHBOARD_TABS["zbynek"])
        self.assertNotIn("montalu6-subdev", w.WEBTERM_DASHBOARD_TABS["marek"])
        self.assertEqual(w.WEBTERM_DASHBOARD_TABS["timo"], ["fohmixer"])

    def test_lane_is_hosted_on_the_controller(self):
        self.assertEqual(p.LANE_HOST["palo"], "controller")
        self.assertEqual(w._HUMAN_TO_MODULE["palo"], "cli_webterm_palo")
        self.assertIn(p.PALO, p.profile_for_host_set("controller"))
        self.assertEqual(p.profile_for_host("subdev", "palo"), None)

    def test_spec_is_a_shared_tunnel_observer_lane(self):
        import cli_webterm_palo as m
        spec = m._spec()
        self.assertEqual(spec.name, "palo")
        self.assertEqual(spec.profile, p.PALO)
        self.assertEqual(spec.gateway_user, "palo")
        self.assertTrue(spec.shared_tunnel)
        self.assertIsNone(spec.identity_key)
        self.assertIsNone(spec.collector_mode)
        self.assertEqual(spec.dashboard_human, "palo")
        self.assertEqual(spec.tunnel_hostname, "palo.newlevel.media")
        self.assertEqual(spec.tunnel_hostname,
                         access.WEBTERM_ACCESS_APPS["palo"]["hostname"])
        self.assertTrue(hasattr(m, "setup_webterm_palo_service"))

    def test_fresh_ports_and_sockets(self):
        import importlib
        import cli_webterm_palo as m
        mine = m._spec()
        for human, mod_name in w._HUMAN_TO_MODULE.items():
            if human == "palo":
                continue
            other = importlib.import_module(mod_name)._spec()
            for field in ("ttyd_port", "gateway_port", "gateway_sock_basename",
                          "ttyd_sock_basename", "gateway_service_name",
                          "ttyd_service_name", "inventory_path", "dash_dir",
                          "launch_path", "tunnel_hostname"):
                self.assertNotEqual(getattr(mine, field), getattr(other, field),
                                    "%s vs %s" % (field, human))

    def test_controller_tunnel_ingress_carries_palo(self):
        """The REAL `_setup_controller_webterm` render derives palo's ingress
        rule from its spec (hostname -> the gateway UNIX socket). Every side
        effect is stubbed: the lane provision, the port harvest, the tunnel
        write and the go-live reconcile."""
        import cli_drop_golive as gl
        import cli_drop_lanes as dl
        import cli_webterm_palo as m
        import cli_webterm_tunnel as tun
        captured = {}

        def fake_provision(creds, bin_, cfg_path, config_text, *a, **k):
            captured["config"] = config_text
            return True

        with mock.patch.object(w.profiles, "LANE_HOST", {"palo": "controller"}), \
                mock.patch.object(m, "setup_webterm_palo_service") as prov, \
                mock.patch.object(dl, "harvest_local_controller_filedrop_port",
                                  return_value=None), \
                mock.patch.object(tun, "_provision_managed_tunnel",
                                  side_effect=fake_provision), \
                mock.patch.object(gl, "reconcile_and_report", return_value=True):
            w._setup_controller_webterm()
        prov.assert_called_once()
        sock = w.webterm_runtime_socket_abs(m._spec().gateway_sock_basename)
        self.assertIn("  - hostname: palo.newlevel.media\n    service: unix:%s\n"
                      % sock, captured["config"])

    def test_go_live_is_a_checklist(self):
        import cli_webterm_palo as m
        for needle in ("webterm_palo_ed25519", PALO_EMAIL,
                       "WEBTERM_CONTROLLER_LANE_PUBKEYS",
                       "forced-command-install", "montalu6",
                       "webterm-access --apply --profile palo"):
            self.assertIn(needle, m._PALO_GO_LIVE)


class TestPaloForcedCommand(unittest.TestCase):
    """The lane key on montalu6 = the owner's montalu6 forced command, no more."""

    def test_refuses_until_the_key_is_minted(self):
        import cli_webterm_palo as m
        with mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS, {}, clear=False):
            wo.WEBTERM_CONTROLLER_LANE_PUBKEYS.pop("palo", None)
            with self.assertRaises(ValueError) as cm:
                m.render_forced_command_install()
        self.assertIn("webterm_palo_ed25519", str(cm.exception))

    def test_line_is_the_owner_montalu6_forced_command(self):
        import cli_webterm_palo as m
        with _with_palo_key():
            line = m.forced_command_key_line()
        ak = wo.parse_authorized_key(line)
        self.assertEqual(ak.blob, _FAKE_BLOB)
        self.assertEqual(ak.comment, "webterm-palo-controller")
        owner = wo.parse_authorized_key(wo._controller_lane_key_line(
            "montalu6", wo.WEBTERM_CONTROLLER_LANE_PUBKEYS["zbynek"]))
        self.assertEqual(ak.options, owner.options)
        self.assertTrue(ak.options.startswith('restrict,pty,command="P=montalu6; '))

    def test_nothing_broader_than_the_attach(self):
        import cli_webterm_palo as m
        with _with_palo_key():
            opts = wo.parse_authorized_key(m.forced_command_key_line()).options
        head = opts.split('command="', 1)[0]
        self.assertEqual(head, "restrict,pty,")
        for widen in ("port-forwarding", "agent-forwarding", "x11-forwarding",
                      "permitopen", "permitlisten", "user-rc", "environment="):
            self.assertNotIn(widen, opts)

    def test_install_script_writes_exactly_one_key(self):
        import cli_webterm_palo as m
        with _with_palo_key():
            script = m.render_forced_command_install()
        self.assertEqual(script.count("ssh-ed25519"), 1)
        self.assertIn(_FAKE_BLOB, script)
        self.assertNotIn(wo.parse_authorized_key(
            wo.WEBTERM_CONTROLLER_LANE_PUBKEYS["zbynek"]).blob, script)
        self.assertTrue(script.startswith("set -euo pipefail\n"))

    def _run(self, script, home):
        return subprocess.run(["bash", "-c", script], env={
            "HOME": str(home), "PATH": os.environ.get("PATH", "")},
            capture_output=True, text=True, timeout=30)

    def test_install_appends_beside_the_existing_keys_then_no_ops(self):
        import cli_session_cwd
        import cli_webterm_palo as m
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".ssh").mkdir()
            ak = home / ".ssh" / "authorized_keys"
            owner_line = wo._controller_lane_key_line(
                "montalu6", wo.WEBTERM_CONTROLLER_LANE_PUBKEYS["zbynek"])
            foreign = "ssh-ed25519 " + "AAAA" + "Q" * 64 + " someone-else"
            ak.write_text(owner_line + "\n" + foreign + "\n")
            with _with_palo_key():
                script = m.render_forced_command_install()
                first = self._run(script, home)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertIn("appended key", first.stdout)
                after = ak.read_text().splitlines()
                self.assertEqual(after[:2], [owner_line, foreign])
                self.assertEqual(after[2], m.forced_command_key_line())
                self.assertEqual(len(after), 3)
                second = self._run(script, home)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertIn("no-op", second.stdout)
                self.assertEqual(ak.read_text().splitlines(), after)
                # the #1202 status check agrees with the baked line
                self.assertEqual(cli_session_cwd.stale_forced_commands(
                    "montalu6", ak.read_text()), [])

    def test_palo_key_reaches_no_other_account(self):
        import cli_account_bootstrap as bootstrap
        with _with_palo_key():
            for user in sorted(cli_fleet.WEBTERM_ONLY_USERS):
                keys = wo.desired_keys_for_user(user)
                self.assertFalse(any(_FAKE_BLOB in k for k in keys), user)
            for account in bootstrap.SERVICE_ACCOUNTS:
                self.assertNotIn("palo",
                                 bootstrap.account_spec(account)["webterm_sessions"],
                                 account)

    def test_slot_is_absent_until_go_live(self):
        # The timo precedent (#1183): the pubkey enters the table at go-live,
        # never as an empty/placeholder value (a blob=None entry would make the
        # #1202 stale check match malformed lines).
        pub = wo.WEBTERM_CONTROLLER_LANE_PUBKEYS.get("palo")
        if pub is not None:
            self.assertTrue(pub.startswith("ssh-ed25519 "))
            self.assertIn("webterm-palo-controller", pub)


class TestPaloInstallCli(unittest.TestCase):
    """`python3 cli_webterm_palo.py forced-command-install` = go-live step 2."""

    def _main(self, argv):
        import io
        import cli_webterm_palo as m
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err):
            rc = m.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_prints_the_install_script(self):
        import cli_webterm_palo as m
        with _with_palo_key():
            rc, out, _err = self._main(["forced-command-install"])
            self.assertEqual(out, m.render_forced_command_install())
        self.assertEqual(rc, 0)

    def test_fails_loud_without_the_key(self):
        with mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS, {}):
            wo.WEBTERM_CONTROLLER_LANE_PUBKEYS.pop("palo", None)
            rc, out, err = self._main(["forced-command-install"])
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("webterm_palo_ed25519", err)

    def test_usage(self):
        for argv in ([], ["install"], ["forced-command-install", "x"]):
            self.assertEqual(self._main(argv)[0], 2, argv)


class TestPaloNoIdentity(unittest.TestCase):
    """Webterm-only (#869): Palo gets no account, no key, no password."""

    def test_no_fleet_account(self):
        users = {h["user"] for h in cli_fleet.REMOTE_HOSTS}
        self.assertNotIn("palo", users)
        self.assertNotIn("palo", cli_fleet.AUTHORITY_BY_USER)
        self.assertNotIn("palo", cli_fleet.FULL_AUTHORITY_USERS)
        self.assertNotIn("palo", cli_fleet.WEBTERM_ONLY_USERS)

    def test_no_owner_key(self):
        from cli_owner_keys import OWNER_PUBKEYS
        self.assertFalse(any("palo" in k for k in OWNER_PUBKEYS))

    def test_lane_key_privilege_row(self):
        import cli_privileges as priv
        (row,) = [r for r in priv.PRIVILEGES if r.name == "webterm_palo_ed25519"]
        self.assertEqual(row.local_path, "~/.secrets/webterm_palo_ed25519")
        self.assertIn("montalu6", row.reach)


class TestPaloAccessDns(unittest.TestCase):

    def test_access_allow_list_is_exactly_palo(self):
        app = access.WEBTERM_ACCESS_APPS["palo"]
        self.assertEqual(app["hostname"], "palo.newlevel.media")
        self.assertEqual(app["allowed_emails"], [PALO_EMAIL])
        self.assertEqual(app["session_duration"], "720h")

    def test_palo_email_is_on_no_other_webterm_app(self):
        for name, app in access.WEBTERM_ACCESS_APPS.items():
            if name != "palo":
                self.assertNotIn(PALO_EMAIL, app["allowed_emails"], name)

    def test_palo_is_a_drop_reader_of_exactly_montalu6(self):
        # #1115: whoever the webterm lets open an account passes its drop lane.
        import cli_drop_gateway as dg
        rows = [(emails, [e["id"] for e in inv]) for emails, inv
                in dg._webterm_readers() if PALO_EMAIL in emails]
        self.assertEqual(rows, [([PALO_EMAIL], ["montalu6-subdev"])])
        m6 = "drop-subdev-montalu6.newlevel.media"
        self.assertEqual(dg.DROP_ACCESS_APPS[m6]["allowed_emails"],
                         ["drlik.zbynek@gmail.com", PALO_EMAIL])
        for host, spec in dg.DROP_ACCESS_APPS.items():
            if host != m6:
                self.assertNotIn(PALO_EMAIL, spec["allowed_emails"], host)

    def test_managed_cname_to_the_controller_tunnel_gated_on_access(self):
        (r,) = [r for r in dns.MANAGED_RECORDS
                if r["name"] == "palo.newlevel.media"]
        self.assertEqual(r["type"], "CNAME")
        self.assertTrue(r["proxied"])
        self.assertEqual(r["content"],
                         w.CONTROLLER_TUNNEL_UUID + ".cfargotunnel.com")
        self.assertEqual(r["requires_access_hostname"], "palo.newlevel.media")


class TestOtherInventoriesUnchanged(unittest.TestCase):
    """zbynek/marek/timo keep their exact tab sets (owner scope, #1205)."""

    def test_ids(self):
        self.assertEqual([e["id"] for e in p.timo_inventory()], ["fohmixer"])
        self.assertNotIn("montalu6-subdev",
                         [e["id"] for e in p.marek_inventory()])
        m6 = _owner_m6()
        self.assertEqual(m6["identity"], p.WEBTERM_ZBYNEK_IDENTITY)
        self.assertIs(m6["u_tenant"], True)
        self.assertEqual(m6["collect_identity"], p.ZBYNEK_COLLECT_IDENTITY)


class TestTimoSpecStable(unittest.TestCase):
    """The shared controller-lane helper must leave timo's spec value-identical
    (a characterization lock: it holds before AND after the extraction)."""

    def test_every_field(self):
        import cli_webterm_timo as t
        s = t._spec()
        home = s.tunnel_creds.parent.parent
        units = home / ".config" / "systemd" / "user"
        self.assertEqual(s.name, "timo")
        self.assertEqual(s.gateway_user, "timo")
        self.assertEqual(s.profile, p.TIMO)
        self.assertEqual(s.bind, "127.0.0.1")
        self.assertEqual((s.ttyd_port, s.gateway_port), (7687, 8085))
        self.assertEqual(s.gateway_sock_basename, "webterm-timo-gateway.sock")
        self.assertEqual(s.ttyd_sock_basename, "webterm-timo-ttyd.sock")
        self.assertEqual(s.inventory_path, w.CLAUDE_DIR / "webterm-timo-inventory.json")
        self.assertEqual(s.dash_dir, w.CLAUDE_DIR / "webterm-timo-dash")
        self.assertEqual(s.dash_index, s.dash_dir / "index.html")
        self.assertEqual(s.launch_path, w.CLAUDE_DIR / "airuleset-webterm-timo-ttyd.sh")
        self.assertEqual(s.ttyd_service_dest, units / "webterm-timo-ttyd.service")
        self.assertEqual(s.gateway_service_dest, units / "webterm-timo-gateway.service")
        self.assertEqual(s.ttyd_service_name, "webterm-timo-ttyd.service")
        self.assertEqual(s.gateway_service_name, "webterm-timo-gateway.service")
        self.assertEqual(s.tunnel_uuid, "00000000-0000-0000-0000-000000000000")
        self.assertEqual(s.tunnel_creds, home / ".cloudflared" / "dummy-timo.json")
        self.assertEqual(s.tunnel_config, home / ".cloudflared" / "dummy-timo.yml")
        self.assertEqual(s.tunnel_service_dest, units / "dummy-timo-tunnel.service")
        self.assertEqual(s.tunnel_service_name, "dummy-timo-tunnel.service")
        self.assertEqual(s.tunnel_hostname, "timo.newlevel.media")
        self.assertEqual(s.cloudflared_bin,
                         str(home / ".local" / "bin" / "cloudflared"))
        self.assertEqual(s.creds_absent_hint, "")
        self.assertEqual(s.go_live, t._TIMO_GO_LIVE)
        self.assertEqual(s.label, "(controller timo)")
        self.assertEqual(s.log_prefix, "webterm(timo)")
        self.assertIsNone(s.identity_key)
        self.assertIsNone(s.retire_credential_path)
        self.assertEqual(s.dashboard_human, "timo")
        self.assertIsNone(s.collector_mode)
        self.assertTrue(s.shared_tunnel)
        self.assertEqual(s.unit_note, lane.render_lane_unit_note(
            name_upper="TIMO", name_lower="timo",
            account_suffix=" (airuleset account, controller)",
            runtime_owner="airuleset's", tunnel_adjective="the SHARED controller",
            hostname="timo.newlevel.media",
            scoped_inventory="Scoped inventory (#1183, owner request 2026-09-29):\n"
                             "# ONE tab — the fohmixer@dev1 project account, via\n"
                             "# the dedicated webterm_timo lane key (a forced-\n"
                             "# command line on fohmixer only). Timo has no\n"
                             "# account, key or password anywhere."))

    def test_the_helper_derives_every_name_from_the_lane_name(self):
        s = lane.controller_lane_spec(
            "zeta", profile="zeta", gateway_user="zeta", ttyd_port=1,
            gateway_port=2, scoped_inventory="x", go_live="y")
        self.assertEqual(s.tunnel_hostname, "zeta.newlevel.media")
        self.assertEqual(s.gateway_service_name, "webterm-zeta-gateway.service")
        self.assertEqual(s.dashboard_human, "zeta")
        self.assertTrue(s.shared_tunnel)
        self.assertIsNone(s.identity_key)


if __name__ == "__main__":
    unittest.main()
