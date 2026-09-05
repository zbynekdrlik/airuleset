"""Tests for the DOMINIKA observer gateway (#867 scope-add 2026-09-04) — the FOURTH
per-human webterm profile (dominika.newlevel.media), on the same subdev box as
david + marek.

The SECURITY-CRITICAL boundary this pins (owner request 2026-09-04: a new webterm
user `dominika` / nika.sarikova@gmail.com with OBSERVE access to montalu5 + miva1):
  * dominika's connect allowlist is PHYSICALLY her TWO-member set {montalu5-subdev,
    miva1-subdev} — her ttyd cannot resolve any OTHER fleet id (a bare gk/
    montalu1-4,6-8/simap/stepan/admin-forestshop-dev/spinbike/dev1/dev2), NOR any
    DAVID id (david1..4/codex-bridge), NOR any MAREK id (marek-subdev/forestshop)
    → refused, never execed;
  * every entry uses the DEDICATED WEBTERM_DOMINIKA_IDENTITY key (never the fleet
    gatekeeper key, never the sshpass shared-password branch); dominika has NO
    keyless local attach (unlike marek), so BOTH tabs are ssh;
  * both tabs are CROSS-TENANT OBSERVE (NO u_tenant → u_tenant_entries empty);
  * a per-hostname Cloudflare Access realm (dominika email only, deny-by-default);
  * the owner / david / marek profiles are byte-identical (dominika is additive);
  * account-aware provisioning dispatch (dominika@subdev -> dominika).

The subdev live systemd path is unverifiable from dev1, so the provisioner tests
pin RENDER + GATE + ARTIFACT correctness (systemctl mocked), exactly like the
david/marek/owner provisioning tests.
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
import cli_webterm_dominika as dn  # noqa: E402
import cli_webterm_lane as lane  # noqa: E402  (#665 shared provisioner shutil/subprocess seam)
import cli_webterm_profiles as p  # noqa: E402
import cli_webterm_access as access  # noqa: E402
import cli_webterm_tunnel as tun  # noqa: E402
import cli_filedrop_watchdog as fw  # noqa: E402
import cli_binary_installers as binstall  # noqa: E402


class TestProfileForHostAccountAware(unittest.TestCase):
    def test_subdev_dominika_account_is_dominika(self):
        self.assertEqual(p.profile_for_host("subdev", account="dominika"), p.DOMINIKA)

    def test_subdev_default_is_still_david(self):
        self.assertEqual(p.profile_for_host("subdev"), p.DAVID)

    def test_subdev_marek_account_resolves_marek(self):
        # #882 scope correction: marek lane RESTORED (observer, not dev stream).
        self.assertEqual(p.profile_for_host("subdev", account="marek"), p.MAREK)

    def test_dev1_is_owner_regardless_of_account(self):
        self.assertEqual(p.profile_for_host("dev1", account="dominika"), p.OWNER)

    def test_other_box_has_no_profile(self):
        self.assertIsNone(p.profile_for_host("dev2", account="dominika"))


class TestDominikaInventory(unittest.TestCase):
    def test_two_member_set_in_owner_order(self):
        inv = p.dominika_inventory()
        self.assertEqual([e["id"] for e in inv],
                         ["montalu5-subdev", "miva1-subdev"])

    def test_montalu5_entry_is_loopback_ssh_observe_no_u_tenant(self):
        e = next(x for x in p.dominika_inventory() if x["id"] == "montalu5-subdev")
        self.assertFalse(e["local"])
        self.assertEqual(e["host"], "127.0.0.1")
        self.assertEqual(e["user"], "montalu5")
        self.assertEqual(e["identity"], p.WEBTERM_DOMINIKA_IDENTITY)
        self.assertEqual(e["preferred"], "montalu5")
        # OBSERVE-only, CROSS-TENANT: never a within-tenant read.
        self.assertIsNot(e.get("u_tenant"), True)

    def test_miva1_entry_is_loopback_ssh_observe_no_u_tenant(self):
        e = next(x for x in p.dominika_inventory() if x["id"] == "miva1-subdev")
        self.assertFalse(e["local"])
        self.assertEqual(e["host"], "127.0.0.1")
        self.assertEqual(e["user"], "miva1")
        self.assertEqual(e["identity"], p.WEBTERM_DOMINIKA_IDENTITY)
        self.assertEqual(e["preferred"], "miva1")
        self.assertIsNot(e.get("u_tenant"), True)

    def test_dominika_has_no_local_attach(self):
        # Unlike marek (whose gateway runs as his own tmux group), dominika is a
        # pure observer — she has NO local attach; every tab is ssh.
        self.assertTrue(all(not e.get("local") for e in p.dominika_inventory()))

    def test_every_ssh_entry_uses_the_dedicated_dominika_identity(self):
        # NEVER identity=None on an ssh entry (that would take the sshpass
        # shared-password branch from dominika's gateway) and NEVER the fleet
        # gatekeeper key (cross-stream escalation).
        self.assertNotIn("gatekeeper", p.WEBTERM_DOMINIKA_IDENTITY)
        for e in p.dominika_inventory():
            self.assertFalse(e.get("local"))
            self.assertEqual(e["identity"], p.WEBTERM_DOMINIKA_IDENTITY,
                             "ssh entry %r must use the dedicated key" % e["id"])

    def test_u_tenant_entries_is_empty_observe_only(self):
        # #703: dominika collects NOTHING — both tabs are cross-tenant OBSERVE, so
        # her lane U-dot collector reads no tickets-status cache.
        self.assertEqual(p.u_tenant_entries(p.DOMINIKA), [])

    def test_profile_inventory_and_webterm_inventory_agree(self):
        self.assertEqual(p.profile_inventory(p.DOMINIKA, []), p.dominika_inventory())
        self.assertEqual(w.webterm_inventory(profile=p.DOMINIKA),
                         p.dominika_inventory())


class TestDominikaConnectAllowlistScoped(unittest.TestCase):
    """The heart of the boundary: dominika's ttyd child reads her inventory, so
    connect_main can ONLY resolve her own two-member set — never another stream's,
    another person's, a david id, a marek id, or an owner-realm box."""

    def _dominika_inv_file(self):
        d = tempfile.mkdtemp()
        f = Path(d) / "dominika-inv.json"
        f.write_text(json.dumps(p.dominika_inventory()), encoding="utf-8")
        return f

    def test_foreign_ids_are_refused_against_dominika_inventory(self):
        f = self._dominika_inv_file()
        for foreign in ("gk", "gatekeeper", "montalu-subdev", "montalu2-subdev",
                        "montalu4-subdev", "montalu6-subdev", "simap1-subdev",
                        "spinbike-vps", "dev1", "dev2", "forestshop",
                        "marek-subdev", "stepan-forestshop-dev",
                        "admin-forestshop-dev", "codex-bridge",
                        "david1", "david2", "david3", "david4"):
            with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                    m.patch.object(w.os, "execvp",
                                   side_effect=AssertionError("must not exec")) as ex:
                rc = w.connect_main([foreign])
            self.assertEqual(rc, 2, "foreign id %r must be refused" % foreign)
            ex.assert_not_called()

    def test_montalu5_id_execs_ssh_with_dedicated_key_to_montalu5_group(self):
        f = self._dominika_inv_file()
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["montalu5-subdev"])
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("-i", argv)
        self.assertIn(os.path.expanduser(p.WEBTERM_DOMINIKA_IDENTITY), argv)
        self.assertNotIn("sshpass", argv)          # never the shared password
        self.assertIn("P=montalu5; ", " ".join(argv))

    def test_miva1_id_execs_ssh_with_dedicated_key(self):
        f = self._dominika_inv_file()
        with m.patch.dict(os.environ, {"WEBTERM_INVENTORY": str(f)}), \
                m.patch.object(w.os, "execvp") as ex:
            w.connect_main(["miva1-subdev"])
        ex.assert_called_once()
        argv = ex.call_args[0][1]
        self.assertEqual(argv[0], "ssh")
        self.assertIn(os.path.expanduser(p.WEBTERM_DOMINIKA_IDENTITY), argv)
        self.assertNotIn("sshpass", argv)
        self.assertIn("P=miva1; ", " ".join(argv))

    def test_dominika_allowed_ids_are_exactly_her_two(self):
        ids = p.allowed_ids(p.DOMINIKA, [])
        self.assertEqual(ids, {"montalu5-subdev", "miva1-subdev"})
        for foreign in ("gatekeeper", "marek-subdev", "david1", "codex-bridge",
                        "dev1", "dev2", "forestshop"):
            self.assertNotIn(foreign, ids)


class TestDominikaFleetEntry(unittest.TestCase):
    """The REMOTE_HOSTS registration + classification (drift-locked to cli_fleet):
    a webterm OBSERVER, reduced fork-no-merge, NEVER full — an observe-only account
    must never merge/deploy/close."""

    def test_dominika_remote_host_registered_marek_shape(self):
        # #882: marek@subdev removed — compare against david1@subdev instead
        # (same subdev VPS, same operator gk_access key pattern)
        import cli_fleet
        e = next(h for h in cli_fleet.REMOTE_HOSTS if h["name"] == "dominika@subdev")
        david1 = next(h for h in cli_fleet.REMOTE_HOSTS if h["name"] == "david1@subdev")
        self.assertEqual(e["host"], david1["host"])         # same subdev VPS
        self.assertEqual(e["user"], "dominika")
        self.assertEqual(e["identity"], david1["identity"]) # operator gk_access key
        self.assertEqual(e["repo_path"], "~/devel/airuleset")

    def test_dominika_is_reduced_never_full(self):
        # An observe-only account must NOT hold full authority (merge/deploy/close).
        # The classify-all test forces every REMOTE_HOSTS user into exactly one
        # registry; dominika belongs in the reduced one, least-privilege.
        import cli_fleet
        self.assertEqual(cli_fleet.AUTHORITY_BY_USER.get("dominika"), "fork-no-merge")
        self.assertNotIn("dominika", cli_fleet.FULL_AUTHORITY_USERS)


class TestDominikaObserverExclusions(unittest.TestCase):
    """#867 review 🟡: dominika is in AUTHORITY_BY_USER ONLY for the classify-all
    gate — she is a webterm OBSERVER (cli_fleet.WEBTERM_OBSERVER_USERS), so every
    install/push-time STREAM-provisioning consumer that keys on AUTHORITY_BY_USER
    membership must EXCLUDE her: no auto-`claude` tmux session, no dev-env gap
    report, no Soniox key, no ssh-auto-attach/window-naming, no bounce/gkreq sweep."""

    def test_is_webterm_observer_and_subset_of_authority(self):
        import cli_fleet
        self.assertTrue(cli_fleet.is_webterm_observer("dominika"))
        self.assertFalse(cli_fleet.is_webterm_observer("montalu5"))
        # A strict SUBSET of AUTHORITY_BY_USER — an observer is a classified
        # reduced account minus stream provisioning, never a third authority tier.
        self.assertLessEqual(set(cli_fleet.WEBTERM_OBSERVER_USERS),
                             set(cli_fleet.AUTHORITY_BY_USER))

    def test_no_auto_claude_tmux_session(self):
        import airuleset
        calls = []
        # A real reduced stream WOULD proceed past the gate (reaches the tmux
        # probe); dominika must return None BEFORE touching `run`.
        rc = airuleset.ensure_stream_tmux_session(
            user="dominika", run=lambda *a, **k: calls.append(a))
        self.assertIsNone(rc)
        self.assertEqual(calls, [], "observer must never spawn/type into tmux")

    def test_no_dev_env_gap_report(self):
        import airuleset
        # Returns early (prints nothing) — never calls _stream_provisioning_gaps.
        with m.patch.object(airuleset, "_stream_provisioning_gaps",
                            side_effect=AssertionError("observer is not a dev stream")):
            self.assertIsNone(airuleset.report_stream_dev_env(user="dominika"))

    def test_not_a_single_session_box_user(self):
        import cli_bashrc_appliers as ba
        self.assertFalse(ba.is_single_session_box_user("dominika"))
        # non-vacuous: a real reduced stream on the same box IS one.
        self.assertTrue(ba.is_single_session_box_user("montalu5"))

    def test_excluded_from_reduced_stream_sweep(self):
        import watchdog
        self.assertNotIn("dominika", watchdog._REDUCED_STREAM_USERS)
        self.assertIn("montalu5", watchdog._REDUCED_STREAM_USERS)  # non-vacuous

    def test_soniox_key_not_delivered_to_observer(self):
        import airuleset
        import cli_remote
        by_name = {h["name"]: h for h in airuleset.REMOTE_HOSTS}
        hosts = [by_name["dominika@subdev"], by_name["montalu5@subdev"]]
        # Missing source → every TARGETED host is reported failed. So the failure
        # set names exactly the targets: montalu5 (a real stream) IN, dominika
        # (observer) OUT. run is never reached (source read fails first).
        failures = cli_remote.provision_subdev_soniox_key(
            hosts=hosts, run=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("must not ssh — source is missing")),
            source=Path("/nonexistent/soniox.env"))
        failed_names = {name for name, _reason in failures}
        self.assertIn("montalu5@subdev", failed_names)     # targeted (non-vacuous)
        self.assertNotIn("dominika@subdev", failed_names)  # observer excluded


class TestDominikaAccessApp(unittest.TestCase):
    def test_dominika_access_app_declared_with_owner_provided_email(self):
        spec = access.WEBTERM_ACCESS_APPS.get("dominika")
        self.assertIsNotNone(spec, "WEBTERM_ACCESS_APPS['dominika'] must be declared")
        self.assertEqual(spec["hostname"], "dominika.newlevel.media")
        self.assertEqual(spec["allowed_emails"], ["nika.sarikova@gmail.com"])

    def test_dominika_policy_deny_by_default_one_include(self):
        spec = access.WEBTERM_ACCESS_APPS["dominika"]
        pol = access.build_policy_payload(spec)
        self.assertEqual(pol["decision"], "allow")
        self.assertEqual(pol["include"],
                         [{"email": {"email": "nika.sarikova@gmail.com"}}])


class TestDominikaUnitRender(unittest.TestCase):
    def test_gateway_unit_binds_unix_socket_no_tcp_access_mode_no_credential(self):
        unit = dn.render_dominika_gateway_unit()
        exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
        self.assertIn("--socket %t/" + dn.WEBTERM_DOMINIKA_GATEWAY_SOCK_BASENAME, exec_line)
        self.assertIn("--ttyd-socket %t/" + dn.WEBTERM_DOMINIKA_TTYD_SOCK_BASENAME, exec_line)
        self.assertNotIn("--bind ", exec_line)
        self.assertNotIn("--port ", exec_line)
        self.assertNotIn("--ttyd-host", exec_line)
        self.assertNotIn("--ttyd-port", exec_line)
        self.assertIn("--trust-access-header Cf-Access-Authenticated-User-Email",
                      exec_line)
        self.assertNotIn("--cred ", exec_line)
        # #703: a lane unit carries --u-lane, NEVER the owner-only --u-collect.
        self.assertIn("--u-lane dominika", exec_line)
        self.assertNotIn("--u-collect", exec_line)

    def test_dominika_ports_are_distinct_from_owner_david_and_marek(self):
        import cli_webterm_david as d
        # #882: marek webterm module deleted — check david + owner only
        self.assertNotIn(dn.WEBTERM_DOMINIKA_GATEWAY_PORT,
                         (w.WEBTERM_GATEWAY_PORT, d.WEBTERM_DAVID_GATEWAY_PORT))
        self.assertNotIn(dn.WEBTERM_DOMINIKA_TTYD_PORT,
                         (w.WEBTERM_TTYD_PORT, d.WEBTERM_DAVID_TTYD_PORT))

    def test_gateway_after_points_at_dominika_ttyd_unit(self):
        unit = dn.render_dominika_gateway_unit()
        self.assertIn("webterm-dominika-ttyd.service", unit)
        self.assertNotIn("network-online.target webterm-ttyd.service", unit)

    def test_ttyd_unit_execs_the_dominika_launcher_with_path_env(self):
        unit = dn.render_dominika_ttyd_unit()
        self.assertIn(str(dn.WEBTERM_DOMINIKA_LAUNCH_PATH), unit)
        self.assertIn("Environment=PATH=%h/.local/bin:", unit)
        self.assertLess(unit.index("[Service]"), unit.index("Environment=PATH="))

    def test_tunnel_uuid_and_hostname_are_dominika_specific(self):
        self.assertEqual(dn.WEBTERM_DOMINIKA_TUNNEL_HOSTNAME, "dominika.newlevel.media")
        self.assertEqual(dn.WEBTERM_DOMINIKA_TUNNEL_UUID,
                         "7792f710-16fb-41da-b46d-1d7b1cd0f8a6")
        import cli_webterm_david as d
        # #882: marek webterm module deleted — check david only
        self.assertNotIn(dn.WEBTERM_DOMINIKA_TUNNEL_UUID,
                         (d.WEBTERM_DAVID_TUNNEL_UUID,))
        self.assertNotEqual(dn.WEBTERM_DOMINIKA_TUNNEL_CONFIG,
                            d.WEBTERM_DAVID_TUNNEL_CONFIG)


class TestDominikaPrerequisiteGate(unittest.TestCase):
    def test_no_op_when_not_the_dominika_account(self):
        with m.patch.object(fw, "_whoami", lambda: "david1"):
            ok, reason = dn.prerequisites_ready()
        self.assertFalse(ok)
        self.assertIn("gateway account", reason)

    def test_no_op_when_ttyd_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with m.patch.dict(os.environ, {"HOME": tmp}), \
                    m.patch.object(fw, "_whoami", lambda: dn.DOMINIKA_GATEWAY_USER), \
                    m.patch.object(lane.shutil, "which", return_value=None):
                ok, reason = dn.prerequisites_ready()
        self.assertFalse(ok)
        self.assertIn("prerequisites missing", reason)

    def test_ready_with_ttyd_and_no_key_required(self):
        # identity_key=None: the gate is READY with just ttyd — it does NOT wait for
        # the dedicated key (the two ssh tabs degrade visibly until the key lands,
        # #684 parity: a live lane's re-render must never no-op on a missing key).
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".local" / "bin").mkdir(parents=True)
            ttyd = home / ".local" / "bin" / "ttyd"
            ttyd.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(ttyd, 0o755)
            with m.patch.dict(os.environ, {"HOME": str(home)}), \
                    m.patch.object(fw, "_whoami", lambda: dn.DOMINIKA_GATEWAY_USER), \
                    m.patch.object(lane.shutil, "which", return_value=None):
                ok, reason = dn.prerequisites_ready()
        self.assertTrue(ok, reason)

    def test_spec_keeps_identity_key_none(self):
        self.assertIsNone(dn._spec().identity_key)

    def test_setup_is_a_safe_noop_when_not_ready(self):
        with m.patch.object(fw, "_whoami", lambda: "montalu5"), \
                m.patch.object(fw, "_run_systemctl",
                               side_effect=AssertionError("must not touch systemd")):
            self.assertFalse(dn.setup_webterm_dominika_service())


class TestDominikaArtifactsWrite(unittest.TestCase):
    def _isolate(self, stack, tmp):
        base = Path(tmp)
        claude = base / ".claude"
        stack.enter_context(m.patch.object(w, "CLAUDE_DIR", claude))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_INVENTORY_PATH",
            claude / "webterm-dominika-inventory.json"))
        stack.enter_context(m.patch.object(dn, "WEBTERM_DOMINIKA_DASH_DIR",
                                           claude / "webterm-dominika-dash"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_DASH_INDEX",
            claude / "webterm-dominika-dash" / "index.html"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_LAUNCH_PATH",
            claude / "airuleset-webterm-dominika-ttyd.sh"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_SERVICE_DEST",
            base / "systemd" / "webterm-dominika-ttyd.service"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_GATEWAY_SERVICE_DEST",
            base / "systemd" / "webterm-dominika-gateway.service"))
        stack.enter_context(m.patch.object(tun, "WEBTERM_CLOUDFLARED_DIR",
                                           base / ".cloudflared"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_TUNNEL_CREDS",
            base / ".cloudflared" / (dn.WEBTERM_DOMINIKA_TUNNEL_UUID + ".json")))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_TUNNEL_CONFIG",
            base / ".cloudflared" / "webterm-dominika.yml"))
        stack.enter_context(m.patch.object(
            dn, "WEBTERM_DOMINIKA_TUNNEL_SERVICE_DEST",
            base / "systemd" / "webterm-dominika-tunnel.service"))
        return claude

    def test_write_artifacts_scoped_inventory_and_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            claude = self._isolate(st, tmp)
            dn._write_dominika_artifacts()
            inv = json.loads((claude / "webterm-dominika-inventory.json")
                             .read_text(encoding="utf-8"))
            # The written connect allowlist is exactly the two-member observe set.
            self.assertEqual([e["id"] for e in inv],
                             ["montalu5-subdev", "miva1-subdev"])
            launcher = (claude / "airuleset-webterm-dominika-ttyd.sh").read_text(
                encoding="utf-8")
            self.assertIn("export WEBTERM_INVENTORY=", launcher)
            self.assertIn("webterm-dominika-inventory.json", launcher)
            self.assertNotIn("--inventory", launcher)
            self.assertIn(dn.WEBTERM_DOMINIKA_TTYD_SOCK_BASENAME, launcher)
            self.assertIn('-i "$SOCK"', launcher)
            # The dashboard lists exactly m5 + miva (the #592 alias source).
            html = (claude / "webterm-dominika-dash" / "index.html").read_text(
                encoding="utf-8")
            import re
            self.assertEqual(re.findall(r'<span class="al">([^<]+)</span>', html),
                             ["m5", "miva"])
            # #703 parity: write_artifacts enables the PER-TENANT lane poll
            # (lane_u_status=True), so the WRITTEN dash polls its OWN gateway's
            # /u-status — never the owner-only cross-tenant --u-collect (locked in
            # TestDominikaUnitRender). For dominika that scoped map is EMPTY
            # (u_tenant_entries("dominika") == [], both tabs cross-tenant OBSERVE),
            # so the poll shows no dots — a harmless on-but-empty poll, exactly the
            # david/marek lane shape.
            self.assertIn('"u_status": true', html)

    def test_full_setup_when_ready_provisions_and_enables(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            self._isolate(st, tmp)
            st.enter_context(m.patch.object(fw, "_whoami",
                                            lambda: dn.DOMINIKA_GATEWAY_USER))
            st.enter_context(m.patch.object(lane.shutil, "which",
                                            return_value="/usr/bin/ttyd"))
            st.enter_context(m.patch.object(
                fw, "_run_systemctl",
                lambda args: (calls.append(args), (0, "", ""))[1]))
            st.enter_context(m.patch.object(lane.subprocess, "run",
                                            return_value=None))
            ok = dn.setup_webterm_dominika_service()
        self.assertTrue(ok)
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("enable --now webterm-dominika-gateway.service" in f
                            for f in flat))


class TestDominikaLaneDashboardHuman(unittest.TestCase):
    def test_dominika_spec_declares_dashboard_human(self):
        self.assertEqual(dn._spec().dashboard_human, "dominika")


class TestDominikaDispatch(unittest.TestCase):
    def test_dominika_account_on_subdev_dispatches_dominika(self):
        called = []
        with m.patch.object(w.os, "uname",
                            return_value=type("U", (), {"nodename": "subdev"})()), \
                m.patch("cli_filedrop_watchdog._whoami", lambda: "dominika"), \
                m.patch.object(dn, "setup_webterm_dominika_service",
                               lambda: called.append("dominika") or True):
            w.maybe_setup_webterm()
        self.assertEqual(called, ["dominika"])

    def test_david_account_on_subdev_does_not_dispatch_dominika(self):
        called = []
        import cli_webterm_david as d
        with m.patch.object(w.os, "uname",
                            return_value=type("U", (), {"nodename": "subdev"})()), \
                m.patch("cli_filedrop_watchdog._whoami",
                        lambda: p.DAVID_GATEWAY_USER), \
                m.patch.object(d, "setup_webterm_david_service",
                               lambda: called.append("david") or True), \
                m.patch.object(dn, "setup_webterm_dominika_service",
                               lambda: called.append("dominika") or True):
            w.maybe_setup_webterm()
        self.assertEqual(called, ["david"])


class TestDominikaTtydAutoInstall(unittest.TestCase):
    def test_installer_runs_before_the_prerequisite_gate(self):
        order = []

        def fake_gate():
            order.append("gate")
            return False, "not the gateway account"

        with m.patch.object(binstall, "ensure_ttyd_static_binary",
                            lambda *a, **k: order.append("install")), \
                m.patch.object(dn, "prerequisites_ready", side_effect=fake_gate):
            self.assertFalse(dn.setup_webterm_dominika_service())
        self.assertEqual(order, ["install", "gate"])

    def test_installer_failure_never_breaks_setup(self):
        with m.patch.object(binstall, "ensure_ttyd_static_binary",
                            side_effect=RuntimeError("network down")), \
                m.patch.object(fw, "_whoami", lambda: "david1"), \
                m.patch.object(fw, "_run_systemctl",
                               side_effect=AssertionError("must not touch systemd")):
            self.assertFalse(dn.setup_webterm_dominika_service())   # must not raise


if __name__ == "__main__":
    unittest.main()
