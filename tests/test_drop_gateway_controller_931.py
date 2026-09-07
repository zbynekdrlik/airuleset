"""Tests for #931: controller-ingress topology for drop-gateway.

The per-box david tunnel (1564fe31) was RETIRED in #870's controller
consolidation. David1-4 drop lanes now ride the controller's multi-ingress
tunnel (f85ea304), with topology="controller" and origin_host pointing at
subdev's tailscale IP. These tests verify:

1. The registry entries have the correct topology + controller tunnel UUID.
2. cmd_drop_gateway --apply for controller-topology writes the marker WITHOUT
   touching any tunnel config or restarting any tunnel service.
3. reconcile_drop_ingress_on_install for controller-topology is a benign no-op.
4. drop_ingress_rules_for_controller() returns correct ingress rules.
5. The controller tunnel UUID matches cli_webterm.py's CONTROLLER_TUNNEL_UUID.
"""
import os
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli_drop_gateway as dg  # noqa: E402


class TestControllerTopologyRegistry(unittest.TestCase):
    """#931: david1-4 lanes must have topology="controller" and reference the
    controller tunnel UUID, not the retired per-box david tunnel."""

    def test_david_lanes_have_controller_topology(self):
        for user in ("david1", "david2", "david3", "david4"):
            lane = dg.drop_lane_for_account("subdev", user)
            self.assertIsNotNone(lane, "no lane for subdev/%s" % user)
            self.assertEqual(lane.topology, "controller",
                             "subdev/%s must be controller topology" % user)
            self.assertIsNotNone(lane.origin_host,
                                "subdev/%s must have origin_host" % user)

    def test_david_lanes_reference_controller_tunnel(self):
        for user in ("david1", "david2", "david3", "david4"):
            lane = dg.drop_lane_for_account("subdev", user)
            self.assertEqual(lane.tunnel_uuid, dg._CONTROLLER_TUNNEL_UUID,
                             "subdev/%s tunnel UUID must be the controller's"
                             % user)

    def test_david_lanes_have_no_local_tunnel_config(self):
        for user in ("david1", "david2", "david3", "david4"):
            lane = dg.drop_lane_for_account("subdev", user)
            self.assertIsNone(lane.tunnel_config,
                              "subdev/%s must have no local tunnel config"
                              % user)
            self.assertIsNone(lane.tunnel_service,
                              "subdev/%s must have no local tunnel service"
                              % user)

    def test_controller_tunnel_uuid_matches_webterm(self):
        """The duplicated controller UUID must match cli_webterm.py's constant
        — a drift would route drop traffic to the wrong tunnel."""
        import cli_webterm
        self.assertEqual(dg._CONTROLLER_TUNNEL_UUID,
                         cli_webterm.CONTROLLER_TUNNEL_UUID,
                         "controller tunnel UUID drift between "
                         "cli_drop_gateway and cli_webterm")

    def test_spinbike_lane_is_still_local_topology(self):
        lane = dg.drop_lane_for_account("spinbike", "newlevel")
        self.assertEqual(lane.topology, "local")
        self.assertIsNone(lane.origin_host)
        self.assertIsNotNone(lane.tunnel_config)

    def test_retired_david_tunnel_uuid_not_referenced(self):
        """The retired david tunnel UUID must not appear in any lane."""
        retired = "1564fe31-a95f-4053-93d4-baff2b8a6e97"
        for (node, user), lane in dg.DROP_LANES.items():
            self.assertNotEqual(lane.tunnel_uuid, retired,
                                "%s@%s still references the retired david "
                                "tunnel" % (user, node))


def _args(**kw):
    return types.SimpleNamespace(**kw)


class TestCmdDropGatewayController(unittest.TestCase):
    """#931: cmd_drop_gateway for controller-topology lanes writes the marker
    WITHOUT touching any tunnel config or restarting any tunnel service."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")

    def test_dry_run_controller_topology(self):
        """Dry-run for a controller-topology lane prints the plan without
        writing anything."""
        from unittest import mock
        with mock.patch.object(dg, "_reconcile_access",
                               return_value=(True, "Access ok (dry-run)")):
            rc = dg.cmd_drop_gateway(_args(
                apply=False, _nodename="subdev", _username="david2",
                _marker_path=self.marker))
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.marker),
                         "dry-run must not write the marker")

    def test_apply_writes_marker_no_tunnel_restart(self):
        """Apply for a controller-topology lane writes the marker and does NOT
        attempt any tunnel restart (no local tunnel to restart)."""
        calls = []

        def spy_run(argv, **kw):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        from unittest import mock
        with mock.patch.object(dg, "_reconcile_access",
                               return_value=(True, "Access ok")):
            rc = dg.cmd_drop_gateway(_args(
                apply=True, _nodename="subdev", _username="david2",
                _marker_path=self.marker, _run=spy_run))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.marker),
                        "apply must write the marker")
        marker = dg.read_drop_marker(self.marker)
        self.assertEqual(marker, ("drop-subdev-david2.newlevel.media", 8871))
        # No systemctl restart attempted — no local tunnel.
        self.assertEqual(calls, [],
                         "controller-topology must not restart any tunnel")

    def test_apply_access_failure_blocks_marker(self):
        """A failing Access reconcile must block the marker for an
        access-gated controller-topology lane."""
        from unittest import mock
        with mock.patch.object(dg, "_reconcile_access",
                               return_value=(False, "Access ERROR: boom")):
            rc = dg.cmd_drop_gateway(_args(
                apply=True, _nodename="subdev", _username="david2",
                _marker_path=self.marker))
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.marker),
                         "failed Access must not write the marker")

    def test_apply_non_access_controller_lane_skips_access(self):
        """A controller-topology lane with access=False must succeed without
        any Access reconcile."""
        # Create a temporary non-access controller lane for testing.
        test_lane = dg.DropLane(
            host="drop-test.newlevel.media", port=9999,
            tunnel_uuid=dg._CONTROLLER_TUNNEL_UUID,
            tunnel_config=None, tunnel_service=None,
            tunnel_system_unit=False, access=False,
            topology="controller", origin_host="100.1.2.3")
        from unittest import mock
        with mock.patch.object(dg, "drop_lane_for_account",
                               return_value=test_lane):
            rc = dg.cmd_drop_gateway(_args(
                apply=True, _nodename="testbox", _username="testuser",
                _marker_path=self.marker))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(self.marker))


class TestReconcileControllerTopology(unittest.TestCase):
    """#931: reconcile_drop_ingress_on_install for controller-topology lanes
    is a benign no-op (the controller's install manages the ingress)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")

    def _run_recorder(self):
        calls = []

        def r(argv, **kw):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return calls, r

    def test_controller_lane_with_marker_is_benign_noop(self):
        """A controller-topology lane with a live marker is a benign no-op —
        no config read, no restart, returns True."""
        dg.write_drop_marker("drop-subdev-david2.newlevel.media", 8871,
                             path=self.marker)
        calls, r = self._run_recorder()
        self.assertTrue(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="subdev", marker_path=self.marker,
            username="david2"))
        self.assertEqual(calls, [],
                         "controller-topology must not restart anything")

    def test_controller_lane_stale_marker_is_healed(self):
        """A controller-topology lane with a stale marker (wrong host/port
        from before #931) must have the marker rewritten."""
        # Write a stale marker with the old david tunnel host.
        dg.write_drop_marker("drop-david.newlevel.media", 8828,
                             path=self.marker)
        calls, r = self._run_recorder()
        self.assertTrue(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="subdev", marker_path=self.marker,
            username="david2"))
        # The marker must now carry the correct host+port.
        marker = dg.read_drop_marker(self.marker)
        self.assertEqual(marker, ("drop-subdev-david2.newlevel.media", 8871),
                         "stale marker must be rewritten for david2")

    def test_controller_lane_correct_marker_unchanged(self):
        """A controller-topology lane with a correct marker is left alone."""
        dg.write_drop_marker("drop-subdev-david2.newlevel.media", 8871,
                             path=self.marker)
        calls, r = self._run_recorder()
        self.assertTrue(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="subdev", marker_path=self.marker,
            username="david2"))
        marker = dg.read_drop_marker(self.marker)
        self.assertEqual(marker, ("drop-subdev-david2.newlevel.media", 8871))

    def test_controller_lane_without_marker_is_noop(self):
        """A controller-topology lane that never went live (no marker) is a
        benign no-op."""
        calls, r = self._run_recorder()
        self.assertTrue(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="subdev", marker_path=self.marker,
            username="david2"))
        self.assertEqual(calls, [])


class TestResolvePublicLaneFull(unittest.TestCase):
    """#931 F1 fix: resolve_public_lane_full returns the correct bind IP —
    tailscale IP for controller topology, loopback for local topology."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")

    def test_controller_lane_returns_tailscale_bind_ip(self):
        """Controller-topology: the bind IP must be the origin_host (tailscale
        IP), NOT 127.0.0.1 — the controller's cloudflared proxies to this IP."""
        dg.write_drop_marker("drop-subdev-david2.newlevel.media", 8871,
                             path=self.marker)
        result = dg.resolve_public_lane_full(
            marker_path=self.marker, nodename="subdev", username="david2")
        self.assertIsNotNone(result)
        host, port, bind_ip = result
        self.assertEqual(host, "drop-subdev-david2.newlevel.media")
        self.assertEqual(port, 8871)
        self.assertEqual(bind_ip, "100.118.174.27",
                         "controller-topology bind IP must be the tailscale "
                         "IP, not loopback — the controller tunnel proxies "
                         "to this address")

    def test_local_lane_returns_loopback_bind_ip(self):
        """Local-topology: the bind IP must be 127.0.0.1 — the local tunnel
        proxies to loopback."""
        dg.write_drop_marker("drop-spinbike.newlevel.media", 8828,
                             path=self.marker)
        result = dg.resolve_public_lane_full(
            marker_path=self.marker, nodename="spinbike", username="newlevel")
        self.assertIsNotNone(result)
        host, port, bind_ip = result
        self.assertEqual(bind_ip, "127.0.0.1")

    def test_no_marker_returns_none(self):
        result = dg.resolve_public_lane_full(
            marker_path=self.marker, nodename="subdev", username="david2")
        self.assertIsNone(result)

    def test_backward_compat_resolve_public_lane_still_2tuple(self):
        """resolve_public_lane (the old 2-tuple API) still works for backward
        compatibility."""
        dg.write_drop_marker("drop-spinbike.newlevel.media", 8828,
                             path=self.marker)
        result = dg.resolve_public_lane(
            marker_path=self.marker, nodename="spinbike", username="newlevel")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)


class TestDropIngressRulesForController(unittest.TestCase):
    """#931: drop_ingress_rules_for_controller() returns correct ingress rules
    for the controller tunnel config."""

    def test_returns_david_lanes(self):
        rules = dg.drop_ingress_rules_for_controller()
        # Should contain entries for david1-4 (all controller-topology).
        hosts = [h for h, _s in rules]
        self.assertIn("drop-david.newlevel.media", hosts)
        self.assertIn("drop-subdev-david2.newlevel.media", hosts)
        self.assertIn("drop-subdev-david3.newlevel.media", hosts)
        self.assertIn("drop-subdev-david4.newlevel.media", hosts)

    def test_service_urls_point_at_tailscale(self):
        rules = dg.drop_ingress_rules_for_controller()
        for host, svc in rules:
            self.assertTrue(
                svc.startswith("http://100.118.174.27:"),
                "service URL %s for %s must point at subdev tailscale"
                % (svc, host))

    def test_ports_match_registry(self):
        rules = dg.drop_ingress_rules_for_controller()
        rule_map = {h: s for h, s in rules}
        for (node, user), lane in dg.DROP_LANES.items():
            if lane.topology != "controller":
                continue
            expected_svc = "http://%s:%d" % (lane.origin_host, lane.port)
            self.assertEqual(rule_map.get(lane.host), expected_svc,
                             "port mismatch for %s@%s" % (user, node))

    def test_does_not_include_local_topology_lanes(self):
        """Local-topology lanes (spinbike) must NOT appear in controller
        ingress rules."""
        rules = dg.drop_ingress_rules_for_controller()
        hosts = [h for h, _s in rules]
        self.assertNotIn("drop-spinbike.newlevel.media", hosts)

    def test_no_duplicate_rules(self):
        rules = dg.drop_ingress_rules_for_controller()
        self.assertEqual(len(rules), len(set(rules)),
                         "duplicate ingress rules")


class TestControllerWebterm931(unittest.TestCase):
    """#931: _setup_controller_webterm includes drop ingress rules in the
    controller tunnel config."""

    def test_controller_config_includes_drop_hostnames(self):
        """The rendered controller tunnel config must contain drop hostnames
        for controller-topology lanes."""
        import cli_webterm_tunnel as tun
        import cli_webterm

        # Build the same ingress rules _setup_controller_webterm builds.
        ingress_rules = []
        ingress_rules.extend(dg.drop_ingress_rules_for_controller())

        # Render with the controller tunnel UUID.
        config = tun.render_cloudflared_multi_ingress_config(
            cli_webterm.CONTROLLER_TUNNEL_UUID,
            "/fake/creds.json",
            ingress_rules)

        # Every controller-topology drop hostname must be present.
        for (node, user), lane in dg.DROP_LANES.items():
            if lane.topology != "controller":
                continue
            self.assertIn(
                "hostname: %s" % lane.host, config,
                "drop hostname %s missing from controller config" % lane.host)
            expected_svc = "http://%s:%d" % (lane.origin_host, lane.port)
            self.assertIn(expected_svc, config,
                          "service URL for %s missing from controller config"
                          % lane.host)

        # The catch-all 404 must be present.
        self.assertIn("http_status:404", config)


if __name__ == "__main__":
    unittest.main()
