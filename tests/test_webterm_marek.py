"""Tests for the MAREK developer gateway (#612 scope-add 2026-08-24) — the THIRD
per-developer webterm profile (marek.newlevel.media), on the same subdev box as
david.

The SECURITY-CRITICAL boundary this pins (#661 rework, owner ruling 2026-08-25 —
the old "single local attach" set was owner-REJECTED as incomplete; #787 added
montalu2-subdev to the set, mirroring montalu4-subdev exactly):
  * marek's connect allowlist is PHYSICALLY his SIX-member set {marek-subdev,
    montalu2-subdev, montalu4-subdev, dev1, dev2, forestshop} — his ttyd cannot
    resolve any OTHER fleet id (gk/montalu1,3,5-8/miva/simap/stepan/
    admin-forestshop-dev) NOR any DAVID id (david1..4/codex-bridge) → refused,
    never execed;
  * every ssh member uses the DEDICATED WEBTERM_MAREK_IDENTITY key (never the
    fleet gatekeeper key, never the sshpass shared-password branch) — the
    WEBTERM_DAVID_IDENTITY precedent; marek-subdev itself stays a keyless
    LOCAL attach;
  * a per-hostname Cloudflare Access realm (marek email only, deny-by-default);
  * the owner AND david profiles are byte-identical (marek is purely additive);
  * account-aware provisioning dispatch (marek@subdev -> marek, david1 -> david).

The subdev live systemd path is unverifiable from dev1, so the provisioner tests
pin RENDER + GATE + ARTIFACT correctness (systemctl mocked), exactly like the
david/owner provisioning tests.
"""
import contextlib
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402
import cli_webterm_marek as mk  # noqa: E402
import cli_webterm_lane as lane  # noqa: E402  (#665 shared provisioner shutil/subprocess seam)
import cli_webterm_profiles as p  # noqa: E402
import cli_webterm_access as access  # noqa: E402
import cli_webterm_tunnel as tun  # noqa: E402
import cli_filedrop_watchdog as fw  # noqa: E402
import cli_binary_installers as binstall  # noqa: E402


# The same controlled fleet fixture the profiles/webterm tests use, so the
# "no owner/david fleet id leaks into marek" assertion draws on a real fleet.
_FAKE_HOSTS = [
    {"name": "dev2", "host": "10.0.0.2", "user": "newlevel"},
    {"name": "gatekeeper", "host": "10.0.0.9", "user": "gatekeeper",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "david@subdev", "host": "10.0.0.5", "user": "david",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "montalu@subdev", "host": "10.0.0.5", "user": "montalu"},
]
_FAKE_AUTHORITY = {"david": "fork-no-merge", "montalu": "branch-merge",
                   "marek": "branch-merge"}


def _fleet_inventory():
    import airuleset
    with m.patch.object(airuleset, "REMOTE_HOSTS", _FAKE_HOSTS), \
            m.patch.object(airuleset, "AUTHORITY_BY_USER", _FAKE_AUTHORITY):
        return w.webterm_inventory()  # default profile == owner


class TestProfileForHostAccountAware(unittest.TestCase):
    def test_subdev_default_is_still_david(self):
        # Backward-compatible: the no-account call is unchanged (subdev -> david).
        self.assertEqual(p.profile_for_host("subdev"), p.DAVID)

    def test_subdev_marek_account_is_marek(self):
        self.assertEqual(p.profile_for_host("subdev", account="marek"), p.MAREK)

    def test_subdev_david_account_is_david(self):
        self.assertEqual(
            p.profile_for_host("subdev", account=p.DAVID_GATEWAY_USER), p.DAVID)

    def test_dev1_is_owner_regardless_of_account(self):
        self.assertEqual(p.profile_for_host("dev1", account="marek"), p.OWNER)

    def test_other_box_has_no_profile(self):
        self.assertIsNone(p.profile_for_host("dev2", account="marek"))


class TestMarekInventory(unittest.TestCase):
    # #661 rework (owner ruling 2026-08-25): the old single-member lock
    # (`test_exactly_one_local_member`) asserted the set the owner REJECTED as
    # incomplete — a requirement change, not a weakening: the new locks pin the
    # owner-defined set instead. #787 (2026-08-31) grew it to SIX members by
    # adding montalu2-subdev, mirroring the existing montalu4-subdev entry.
    def test_six_member_set_in_owner_order(self):
        inv = p.marek_inventory()
        self.assertEqual([e["id"] for e in inv],
                         ["marek-subdev", "montalu2-subdev", "montalu4-subdev",
                          "dev1", "dev2", "forestshop"])

    def test_montalu2_entry_is_loopback_ssh_with_dedicated_key(self):
        # #787: mirrors montalu4-subdev exactly except for the account name.
        e = next(x for x in p.marek_inventory() if x["id"] == "montalu2-subdev")
        self.assertFalse(e["local"])
        self.assertEqual(e["host"], "127.0.0.1")
        self.assertEqual(e["user"], "montalu2")
        self.assertEqual(e["identity"], p.WEBTERM_MAREK_IDENTITY)
        self.assertEqual(e["preferred"], "montalu2")
        self.assertIs(e.get("u_tenant"), True)

    def test_marek_entry_is_a_local_attach_with_no_ssh(self):
        # marek's own primary session stays what it was: the gateway runs AS
        # marek on subdev and attaches his LOCAL tmux — no ssh, no key.
        e = next(x for x in p.marek_inventory() if x["id"] == "marek-subdev")
        self.assertTrue(e["local"])
        self.assertIsNone(e["host"])
        self.assertIsNone(e["identity"])
        self.assertEqual(e["preferred"], "marek")
        self.assertEqual(e["user"], "marek")

    def test_montalu4_entry_is_loopback_ssh_with_dedicated_key(self):
        # His montalu stream — a local subdev unix account reached over loopback
        # with the dedicated key (the david1-4 shape).
        e = next(x for x in p.marek_inventory() if x["id"] == "montalu4-subdev")
        self.assertFalse(e["local"])
        self.assertEqual(e["host"], "127.0.0.1")
        self.assertEqual(e["user"], "montalu4")
        self.assertEqual(e["identity"], p.WEBTERM_MAREK_IDENTITY)
        self.assertEqual(e["preferred"], "montalu4")

    def test_dev1_and_dev2_entries_attach_the_marek_tmux_group(self):
        # marek-owned tmux sessions on the owner dev boxes: ssh newlevel@<box
        # tailscale IP> (subdev is on the tailnet), preferred tmux group
        # `marek` — the SAME owner-group mechanism (notify/statusbar session
        # grouping) the owner's own tabs use with `zbynek`, never a hardcoded
        # session list.
        inv = {x["id"]: x for x in p.marek_inventory()}
        d1, d2 = inv["dev1"], inv["dev2"]
        self.assertEqual(d1["host"], "100.104.8.125")   # dev1 tailscale IP
        self.assertEqual(d2["host"], "100.82.64.27")    # dev2 tailscale IP
        for e in (d1, d2):
            self.assertFalse(e["local"])
            self.assertEqual(e["user"], "newlevel")
            self.assertEqual(e["preferred"], "marek")
            self.assertEqual(e["identity"], p.WEBTERM_MAREK_IDENTITY)

    def test_forestshop_entry_is_admin_on_the_pinned_public_box(self):
        # Marek's forestshop VPS, handled like the owner's spinbike `sb` tab:
        # the box's PRINCIPAL account (`admin` — the forestshop-app deploy
        # account; #572 routes the box to marek's realm), NEVER `stepan`
        # (StepanDK's own isolated personal account — putting a third person's
        # account on Marek's dashboard would repeat the original #661 sin).
        e = next(x for x in p.marek_inventory() if x["id"] == "forestshop")
        self.assertFalse(e["local"])
        self.assertEqual(e["host"], "forestshop-dev.newlevel.media")
        self.assertEqual(e["user"], "admin")
        self.assertEqual(e["identity"], p.WEBTERM_MAREK_IDENTITY)
        self.assertEqual(e["preferred"], "marek")

    def test_forestshop_host_keys_match_the_fleet_pin(self):
        # #679/#680: a public-DNS target is verified STRICTLY against the
        # committed pin. The profiles leaf duplicates the literals (leaf
        # discipline: no airuleset/fleet import on the connect path) — this
        # drift-lock ties them to the ONE fleet source.
        import cli_fleet
        fleet_e = next(h for h in cli_fleet.REMOTE_HOSTS
                       if h["name"] == "admin@forestshop-dev")
        e = next(x for x in p.marek_inventory() if x["id"] == "forestshop")
        self.assertEqual(e["host"], fleet_e["host"])
        self.assertEqual(e["user"], fleet_e["user"])
        self.assertEqual(list(e["host_keys"]), list(fleet_e["host_keys"]))

    def test_every_ssh_entry_uses_the_dedicated_marek_identity(self):
        # NEVER identity=None on an ssh entry (that would take the sshpass
        # shared-password branch from marek's gateway) and NEVER the fleet
        # gatekeeper key (cross-stream escalation).
        self.assertNotIn("gatekeeper", p.WEBTERM_MAREK_IDENTITY)
        for e in p.marek_inventory():
            if e.get("local"):
                continue
            self.assertEqual(e["identity"], p.WEBTERM_MAREK_IDENTITY,
                             "ssh entry %r must use the dedicated key" % e["id"])

    def test_profile_inventory_and_webterm_inventory_agree(self):
        fleet = _fleet_inventory()
        self.assertEqual(p.profile_inventory(p.MAREK, fleet), p.marek_inventory())
        self.assertEqual(w.webterm_inventory(profile=p.MAREK),
                         p.marek_inventory())


class TestMarekConnectAllowlistScoped(unittest.TestCase):
    """The heart of the boundary: marek's ttyd child reads marek's inventory,
    so connect_main can ONLY resolve his own six-member set (#787 added
    montalu2-subdev) — never another stream's, another person's, or a david
    id. (#661 rework 2026-08-25: dev1/dev2 moved OUT of the foreign list —
    the owner explicitly granted marek his own dev1/dev2 session tabs, so
    those ids are now HIS lane entries.)"""

    def _marek_inv_file(self):
        d = tempfile.mkdtemp()
        f = Path(d) / "marek-inv.json"
        f.write_text(json.dumps(p.marek_inventory()), encoding="utf-8")
        return f

    def test_foreign_ids_are_refused_against_marek_inventory(self):
        f = self._marek_inv_file()
        for foreign in ("gatekeeper", "gk", "montalu-subdev", "montalu3-subdev",
                        "montalu5-subdev", "miva1-subdev", "simap1-subdev",
                        "spinbike-vps", "stepan-forestshop-dev",
                        "admin-forestshop-dev",
                        "david1", "david2", "david3", "david4", "codex-bridge"):
            with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                    m.patch.object(w.os, "execvp",
                                   side_effect=AssertionError("must not exec")) as ex:
                rc = w.connect_main([foreign])
            self.assertEqual(rc, 2, "foreign id %r must be refused" % foreign)
            ex.assert_not_called()

    def test_marek_id_execs_a_local_attach_no_ssh(self):
        f = self._marek_inv_file()
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["marek-subdev"])
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        # A local attach — never ssh, attaches marek's tmux group (never a
        # broader target). #736: marek's LOCAL subdev tmux is scope-detached
        # too (`systemd-run --user --scope ... sh -c <tmux>`), so its server
        # never lands in the subdev ttyd cgroup either.
        self.assertEqual(argv[0], "systemd-run")
        self.assertIn("--scope", argv)
        self.assertIn("sh", argv)
        self.assertNotIn("ssh", argv)
        self.assertIn("marek", " ".join(argv))

    def test_dev1_id_execs_ssh_with_dedicated_key_to_marek_group(self):
        f = self._marek_inv_file()
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["dev1"])
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("-i", argv)
        self.assertIn(os.path.expanduser(p.WEBTERM_MAREK_IDENTITY), argv)
        self.assertIn("newlevel@100.104.8.125", argv)
        self.assertNotIn("sshpass", argv)          # never the shared password
        self.assertIn("P=marek; ", " ".join(argv))  # his tmux group, not zbynek

    def test_forestshop_id_execs_ssh_as_admin_with_dedicated_key(self):
        f = self._marek_inv_file()
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["forestshop"])
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("admin@forestshop-dev.newlevel.media", argv)
        self.assertIn(os.path.expanduser(p.WEBTERM_MAREK_IDENTITY), argv)
        self.assertNotIn("sshpass", argv)

    def test_marek_allowed_ids_are_exactly_his_six(self):
        # #787: montalu2-subdev joined the set.
        fleet = _fleet_inventory()
        marek_ids = p.allowed_ids(p.MAREK, fleet)
        self.assertEqual(marek_ids, {"marek-subdev", "montalu2-subdev",
                                     "montalu4-subdev", "dev1",
                                     "dev2", "forestshop"})
        for foreign in ("gatekeeper", "montalu-subdev", "david1",
                        "codex-bridge", "stepan-forestshop-dev",
                        "admin-forestshop-dev"):
            self.assertNotIn(foreign, marek_ids)


class TestMarekAccessApp(unittest.TestCase):
    def test_marek_access_app_declared_with_owner_provided_email(self):
        spec = access.WEBTERM_ACCESS_APPS.get("marek")
        self.assertIsNotNone(spec, "WEBTERM_ACCESS_APPS['marek'] must be declared")
        self.assertEqual(spec["hostname"], "marek.newlevel.media")
        self.assertEqual(spec["allowed_emails"], ["drlik.marek@gmail.com"])

    def test_marek_policy_deny_by_default_one_include(self):
        spec = access.WEBTERM_ACCESS_APPS["marek"]
        pol = access.build_policy_payload(spec)
        self.assertEqual(pol["decision"], "allow")
        self.assertEqual(pol["include"],
                         [{"email": {"email": "drlik.marek@gmail.com"}}])


class TestMarekUnitRender(unittest.TestCase):
    def test_gateway_unit_binds_unix_socket_no_tcp_access_mode_no_credential(self):
        # #663: UNIX-socket origin in marek's runtime dir — NO TCP loopback surface
        # (this lane's NEW auth-less ttyd/header-forgeable gateway were the reach
        # #663's directional note flags as newly exposing marek's own account).
        unit = mk.render_marek_gateway_unit()
        exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
        self.assertIn("--socket %t/" + mk.WEBTERM_MAREK_GATEWAY_SOCK_BASENAME, exec_line)
        self.assertIn("--ttyd-socket %t/" + mk.WEBTERM_MAREK_TTYD_SOCK_BASENAME, exec_line)
        self.assertNotIn("--bind ", exec_line)
        self.assertNotIn("--port ", exec_line)
        self.assertNotIn("--ttyd-host", exec_line)
        self.assertNotIn("--ttyd-port", exec_line)
        # Cloudflare Access mode: NO password/credential.
        self.assertIn("--trust-access-header Cf-Access-Authenticated-User-Email",
                      exec_line)
        self.assertNotIn("--cred ", exec_line)

    def test_marek_ports_are_distinct_from_owner_and_david(self):
        import cli_webterm_david as d
        self.assertNotIn(mk.WEBTERM_MAREK_GATEWAY_PORT,
                         (w.WEBTERM_GATEWAY_PORT, d.WEBTERM_DAVID_GATEWAY_PORT))
        self.assertNotIn(mk.WEBTERM_MAREK_TTYD_PORT,
                         (w.WEBTERM_TTYD_PORT, d.WEBTERM_DAVID_TTYD_PORT))

    def test_gateway_after_points_at_marek_ttyd_unit(self):
        unit = mk.render_marek_gateway_unit()
        self.assertIn("webterm-marek-ttyd.service", unit)
        self.assertNotIn("network-online.target webterm-ttyd.service", unit)

    def test_ttyd_unit_execs_the_marek_launcher_with_path_env(self):
        unit = mk.render_marek_ttyd_unit()
        self.assertIn(str(mk.WEBTERM_MAREK_LAUNCH_PATH), unit)
        # #614 self-contained PATH so the no-sudo ~/.local/bin ttyd resolves.
        self.assertIn("Environment=PATH=%h/.local/bin:", unit)
        self.assertLess(unit.index("[Service]"), unit.index("Environment=PATH="))

    def test_tunnel_uuid_and_hostname_are_marek_specific(self):
        self.assertEqual(mk.WEBTERM_MAREK_TUNNEL_HOSTNAME, "marek.newlevel.media")
        # A SEPARATE tunnel — not david's UUID, and a DEDICATED config file.
        import cli_webterm_david as d
        self.assertNotEqual(mk.WEBTERM_MAREK_TUNNEL_UUID,
                            d.WEBTERM_DAVID_TUNNEL_UUID)
        self.assertNotEqual(mk.WEBTERM_MAREK_TUNNEL_CONFIG,
                            d.WEBTERM_DAVID_TUNNEL_CONFIG)


class TestMarekPrerequisiteGate(unittest.TestCase):
    def test_no_op_when_not_the_marek_account(self):
        with m.patch.object(fw, "_whoami", lambda: "david1"):
            ok, reason = mk.prerequisites_ready()
        self.assertFalse(ok)
        self.assertIn("gateway account", reason)

    def test_no_op_when_ttyd_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with m.patch.dict(os.environ, {"HOME": tmp}), \
                    m.patch.object(fw, "_whoami", lambda: mk.MAREK_GATEWAY_USER), \
                    m.patch.object(lane.shutil, "which", return_value=None):
                ok, reason = mk.prerequisites_ready()
        self.assertFalse(ok)
        self.assertIn("prerequisites missing", reason)

    def test_ready_when_ttyd_only_in_local_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".local" / "bin").mkdir(parents=True)
            ttyd = home / ".local" / "bin" / "ttyd"
            ttyd.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(ttyd, 0o755)
            with m.patch.dict(os.environ, {"HOME": str(home)}), \
                    m.patch.object(fw, "_whoami", lambda: mk.MAREK_GATEWAY_USER), \
                    m.patch.object(lane.shutil, "which", return_value=None):
                ok, reason = mk.prerequisites_ready()
        self.assertTrue(ok, reason)

    def test_setup_is_a_safe_noop_when_not_ready(self):
        with m.patch.object(fw, "_whoami", lambda: "montalu"), \
                m.patch.object(fw, "_run_systemctl",
                               side_effect=AssertionError("must not touch systemd")):
            self.assertFalse(mk.setup_webterm_marek_service())


class TestMarekArtifactsWrite(unittest.TestCase):
    def _isolate(self, stack, tmp):
        base = Path(tmp)
        claude = base / ".claude"
        stack.enter_context(m.patch.object(w, "CLAUDE_DIR", claude))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_INVENTORY_PATH",
            claude / "webterm-marek-inventory.json"))
        stack.enter_context(m.patch.object(mk, "WEBTERM_MAREK_DASH_DIR",
                                           claude / "webterm-marek-dash"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_DASH_INDEX",
            claude / "webterm-marek-dash" / "index.html"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_LAUNCH_PATH",
            claude / "airuleset-webterm-marek-ttyd.sh"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_SERVICE_DEST",
            base / "systemd" / "webterm-marek-ttyd.service"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_GATEWAY_SERVICE_DEST",
            base / "systemd" / "webterm-marek-gateway.service"))
        stack.enter_context(m.patch.object(tun, "WEBTERM_CLOUDFLARED_DIR",
                                           base / ".cloudflared"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_TUNNEL_CREDS",
            base / ".cloudflared" / (mk.WEBTERM_MAREK_TUNNEL_UUID + ".json")))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_TUNNEL_CONFIG",
            base / ".cloudflared" / "webterm-marek.yml"))
        stack.enter_context(m.patch.object(
            mk, "WEBTERM_MAREK_TUNNEL_SERVICE_DEST",
            base / "systemd" / "webterm-marek-tunnel.service"))
        return claude

    def test_write_artifacts_scoped_inventory_and_launcher(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            mk._write_marek_artifacts()
            inv = json.loads((claude / "webterm-marek-inventory.json")
                             .read_text(encoding="utf-8"))
            # #661 rework + #787: the written connect allowlist is the
            # six-member set (montalu2-subdev added).
            self.assertEqual([e["id"] for e in inv],
                             ["marek-subdev", "montalu2-subdev", "montalu4-subdev",
                              "dev1", "dev2", "forestshop"])
            launcher = (claude / "airuleset-webterm-marek-ttyd.sh").read_text(
                encoding="utf-8")
            self.assertIn("export WEBTERM_INVENTORY=", launcher)
            self.assertIn("webterm-marek-inventory.json", launcher)
            self.assertNotIn("--inventory", launcher)
            # #663: ttyd binds a UNIX socket in the account runtime dir, not a TCP port
            self.assertIn(mk.WEBTERM_MAREK_TTYD_SOCK_BASENAME, launcher)
            self.assertIn('-i "$SOCK"', launcher)
            self.assertNotIn("-p %d" % mk.WEBTERM_MAREK_TTYD_PORT, launcher)

    def test_full_setup_when_ready_provisions_and_enables(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            self._isolate(st, tmp)
            st.enter_context(m.patch.object(fw, "_whoami",
                                            lambda: mk.MAREK_GATEWAY_USER))
            st.enter_context(m.patch.object(lane.shutil, "which",
                                            return_value="/usr/bin/ttyd"))
            st.enter_context(m.patch.object(
                fw, "_run_systemctl",
                lambda args: (calls.append(args), (0, "", ""))[1]))
            st.enter_context(m.patch.object(lane.subprocess, "run",
                                            return_value=None))
            ok = mk.setup_webterm_marek_service()
        self.assertTrue(ok)
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("enable --now webterm-marek-gateway.service" in f
                            for f in flat))


class TestMarekLaneDashboardHuman(unittest.TestCase):
    """#661 rework: the marek lane render CONSUMES the declarative per-domain
    tab policy (LaneSpec.dashboard_human="marek" -> render_dashboard_html
    human="marek"), so WEBTERM_DASHBOARD_TABS['marek'] dictates tab order +
    exclusivity. The david lane keeps dashboard_human=None (its scoped
    inventory ids differ from the policy dict's fleet ids), so its render
    stays unfiltered — the #684 lock."""

    def test_marek_spec_declares_dashboard_human(self):
        self.assertEqual(mk._spec().dashboard_human, "marek")

    def test_david_spec_keeps_dashboard_human_none(self):
        import cli_webterm_david as d
        self.assertIsNone(d._spec().dashboard_human)


class TestMarekDispatch(unittest.TestCase):
    """maybe_setup_webterm dispatches by (nodename, account): marek@subdev ->
    the marek provisioner; david1@subdev -> the david provisioner; each self-
    gates so the non-matching one never touches systemd."""

    def test_marek_account_on_subdev_dispatches_marek(self):
        called = []
        with m.patch.object(w.os, "uname",
                            return_value=type("U", (), {"nodename": "subdev"})()), \
                m.patch("cli_filedrop_watchdog._whoami", lambda: "marek"), \
                m.patch.object(mk, "setup_webterm_marek_service",
                               lambda: called.append("marek") or True):
            w.maybe_setup_webterm()
        self.assertEqual(called, ["marek"])

    def test_david_account_on_subdev_does_not_dispatch_marek(self):
        called = []
        import cli_webterm_david as d
        with m.patch.object(w.os, "uname",
                            return_value=type("U", (), {"nodename": "subdev"})()), \
                m.patch("cli_filedrop_watchdog._whoami",
                        lambda: p.DAVID_GATEWAY_USER), \
                m.patch.object(d, "setup_webterm_david_service",
                               lambda: called.append("david") or True), \
                m.patch.object(mk, "setup_webterm_marek_service",
                               lambda: called.append("marek") or True):
            w.maybe_setup_webterm()
        self.assertEqual(called, ["david"])


class TestMarekTtydAutoInstall(unittest.TestCase):
    def test_installer_runs_before_the_prerequisite_gate(self):
        order = []

        def fake_gate():
            order.append("gate")
            return False, "not the gateway account"

        with m.patch.object(binstall, "ensure_ttyd_static_binary",
                            lambda *a, **k: order.append("install")), \
                m.patch.object(mk, "prerequisites_ready", side_effect=fake_gate):
            self.assertFalse(mk.setup_webterm_marek_service())
        self.assertEqual(order, ["install", "gate"])

    def test_installer_failure_never_breaks_setup(self):
        # Best-effort/non-fatal: a raise inside the installer must not crash the
        # never-raises setup — it just no-ops on the gate as usual, touching no
        # systemd (mirrors the david lane's lock, #612 R2 review).
        with m.patch.object(binstall, "ensure_ttyd_static_binary",
                            side_effect=RuntimeError("network down")), \
                m.patch.object(fw, "_whoami", lambda: "david1"), \
                m.patch.object(fw, "_run_systemctl",
                               side_effect=AssertionError("must not touch systemd")):
            self.assertFalse(mk.setup_webterm_marek_service())   # must not raise


if __name__ == "__main__":
    unittest.main()
