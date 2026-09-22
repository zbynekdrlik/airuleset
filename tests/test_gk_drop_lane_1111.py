"""#1111: the gatekeeper box gets a registered controller-topology drop lane
(`drop-gk.newlevel.media`) so `secret show` / `secret request` / `upload` /
`share` on gk default to the public HTTPS URL instead of falling back to the
tailscale URL.

Root cause (main d64f14a4, live gk 22.9.2026): the `DROP_LANES` table has no
`("odoo-gatekeeper", "gatekeeper")` entry, so `drop_lane_for_account` → None on
gk and `resolve_public_lane_full` returns None — every URL producer falls back
to tailscale (the mixed-channel state the owner banned on his daily
secret-pickup box).

Approach 1 (chosen): a controller-topology lane, the david1–4 shape. gk rides
the controller multi-ingress tunnel; the `/s/` share path targets the
persistent filedrop service (100.90.94.41:8788, measured 22.9.), the rest the
ephemeral drop port (8876, next free). Access = the owner identity only.

Calibrated TDD, RED-first (Acceptance 1). Coverage:
  - drop_lane_for_account("odoo-gatekeeper","gatekeeper") returns the lane with
    every field the design fixes.
  - drop_ingress_rules_for_controller() emits the gk `/s/` rule BEFORE its drop
    rule, with the measured filedrop port + drop port.
  - a table-wide (fleet-wide) drop-port uniqueness lock — gk shares the
    controller tunnel with the subdev accounts, so a port collision would
    conflate two ingress origins; per-box uniqueness (already tested) is not
    enough.
  - resolve_public_lane_full with a matching marker on a faked
    odoo-gatekeeper/gatekeeper identity → ("drop-gk.newlevel.media", 8876,
    "100.90.94.41").
  - the Access spec for the gk host carries the owner identity ONLY.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as dg          # noqa: E402

GK_NODE = "odoo-gatekeeper"
GK_USER = "gatekeeper"
GK_HOST = "drop-gk.newlevel.media"
GK_DROP_PORT = 8876
GK_FILEDROP_PORT = 8788
GK_TAILSCALE = "100.90.94.41"


class TestGkLaneFields(unittest.TestCase):
    def setUp(self):
        self.lane = dg.drop_lane_for_account(GK_NODE, GK_USER)

    def test_lane_registered(self):
        self.assertIsNotNone(
            self.lane,
            "gk (%s@%s) must have a registered drop lane" % (GK_USER, GK_NODE))

    def test_host_flat_under_newlevel_media(self):
        self.assertEqual(self.lane.host, GK_HOST)
        # LOAD-BEARING: *.newlevel.media Universal SSL is one level.
        label = GK_HOST[: -len(".newlevel.media")]
        self.assertNotIn(".", label, "gk host must be a single flat label")

    def test_drop_port_is_next_free(self):
        self.assertEqual(self.lane.port, GK_DROP_PORT)

    def test_controller_topology_no_local_tunnel(self):
        self.assertEqual(self.lane.topology, "controller")
        self.assertIsNone(self.lane.tunnel_config)
        self.assertIsNone(self.lane.tunnel_service)
        self.assertFalse(self.lane.tunnel_system_unit)

    def test_rides_controller_tunnel(self):
        self.assertEqual(self.lane.tunnel_uuid, dg._CONTROLLER_TUNNEL_UUID)

    def test_origin_host_is_gk_tailscale(self):
        self.assertEqual(self.lane.origin_host, GK_TAILSCALE)

    def test_filedrop_port_measured(self):
        self.assertEqual(self.lane.filedrop_port, GK_FILEDROP_PORT)

    def test_access_gated(self):
        self.assertTrue(self.lane.access, "gk lane is Access-fronted")

    def test_no_gateway_account(self):
        # The controller renders the ingress; gk owns no shared local tunnel.
        self.assertIsNone(self.lane.gateway_account)


class TestGkControllerIngress(unittest.TestCase):
    def test_s_rule_before_drop_rule(self):
        rules = dg.drop_ingress_rules_for_controller()
        s_rule = (GK_HOST, "^/s/", "http://%s:%d" % (GK_TAILSCALE, GK_FILEDROP_PORT))
        drop_rule = (GK_HOST, "http://%s:%d" % (GK_TAILSCALE, GK_DROP_PORT))
        self.assertIn(s_rule, rules, "gk /s/ path rule missing")
        self.assertIn(drop_rule, rules, "gk drop rule missing")
        self.assertLess(rules.index(s_rule), rules.index(drop_rule),
                        "the /s/ rule must precede the no-path drop rule "
                        "(cloudflared matches top-to-bottom)")

    def test_no_ingress_conflict(self):
        # Must not raise ValueError for a duplicate/conflicting gk ingress.
        dg.drop_ingress_rules_for_controller()


class TestFleetWideDropPortUniqueness(unittest.TestCase):
    def test_drop_ports_unique_across_all_lanes(self):
        """gk shares the controller tunnel with the subdev accounts, so the
        drop `port` must be unique across the WHOLE table, not just per box —
        two lanes on the same tunnel with the same origin port would conflate
        their ingress origins."""
        ports = [lane.port for lane in dg.DROP_LANES.values()]
        dups = sorted({p for p in ports if ports.count(p) > 1})
        self.assertEqual(dups, [], "duplicate drop ports fleet-wide: %s" % dups)


class TestGkResolvePublicLaneFull(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")

    def test_resolve_with_matching_marker(self):
        dg.write_drop_marker(GK_HOST, GK_DROP_PORT, path=self.marker)
        got = dg.resolve_public_lane_full(
            marker_path=self.marker, nodename=GK_NODE, username=GK_USER)
        self.assertEqual(got, (GK_HOST, GK_DROP_PORT, GK_TAILSCALE))

    def test_no_marker_no_lane(self):
        got = dg.resolve_public_lane_full(
            marker_path=self.marker, nodename=GK_NODE, username=GK_USER)
        self.assertIsNone(got, "no marker → no public lane (fail-closed)")

    def test_stale_marker_refused(self):
        dg.write_drop_marker("drop-david.newlevel.media", 8870, path=self.marker)
        got = dg.resolve_public_lane_full(
            marker_path=self.marker, nodename=GK_NODE, username=GK_USER)
        self.assertIsNone(got, "a marker whose host disagrees is refused")


class TestGkAccessOwnerOnly(unittest.TestCase):
    def test_access_spec_owner_identity_only(self):
        spec = dg.DROP_ACCESS_APPS.get(GK_HOST)
        self.assertIsNotNone(spec, "gk Access lane needs a DROP_ACCESS_APPS spec")
        # gk is the owner's box: the Access include list is the OWNER only,
        # sourced the same way the owner-facing lanes source their include
        # (david1's include is [david@grena.sk, drlik.zbynek@gmail.com]; the
        # owner identity is drlik.zbynek@gmail.com — gk gets that one alone).
        owner = "drlik.zbynek@gmail.com"
        self.assertIn(owner, dg.DROP_ACCESS_APPS[dg.DROP_HOST_DAVID]["allowed_emails"],
                      "sanity: owner identity is the shared owner-lane include")
        self.assertEqual(spec["allowed_emails"], [owner],
                         "gk Access include = owner identity only")


if __name__ == "__main__":
    unittest.main()
