"""#1115 slice G — a drop lane for the LOCAL controller account (airuleset@airuleset).

The controller box runs ``push`` + the owner's supervisor session as the unix
account ``airuleset``, which is NOT a REMOTE_HOSTS deploy target, so
``build_drop_lanes`` never generated its lane and ``share``/``upload``/``secret``
from the supervisor session printed private (tailscale) URLs only. Slice G adds
the lane (a DISTINCT hostname, controller topology, the controller's own tailscale
origin, an owner-only Access spec derived by the slice-D helper), writes its
go-live marker on the controller install's LOCAL leg ONLY when the lane is LIVE
(the same fail-closed rule as slice B's ssh leg), and harvests its persistent
filedrop port locally into the same cache slice A uses.

Every Cloudflare write stays behind the slice E/F worktree refusal — FAKE clients
only, NO live API, NO ~/.secrets read.

Acceptance G:
  - the registry has a lane for the LOCAL controller account, with a distinct
    hostname (not claudy's drop-airuleset) and an owner-only Access spec;
  - claudy's lane and every existing lane are byte-identical (nothing else added);
  - the local marker is written ONLY for a live lane (fake clients,
    is_worktree_fn=False for the write-path test);
  - public_url_channel_fact() on a faked controller returns "ok";
  - the worktree refusal still blocks the live write.
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as dg           # noqa: E402
import cli_drop_golive as gl            # noqa: E402
import cli_drop_lanes as dl             # noqa: E402
import cli_fleet                        # noqa: E402

# Reuse the slice-B fake DNS/Access clients (no network, no ssh, no --apply).
from test_drop_golive_1115 import _dns, _acc  # noqa: E402

OWNER = "drlik.zbynek@gmail.com"
CONTROLLER_KEY = ("airuleset", "airuleset")
CONTROLLER_HOST = "drop-controller.newlevel.media"
CONTROLLER_ORIGIN = "100.101.214.103"
ZONE = "newlevel.media"


# --------------------------------------------------------------------------- #
# the lane is in the generated registry
# --------------------------------------------------------------------------- #

class TestLocalControllerLanePresent(unittest.TestCase):
    def setUp(self):
        self.lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)

    def test_lane_exists_with_distinct_hostname(self):
        self.assertIn(CONTROLLER_KEY, self.lanes,
                      "the LOCAL controller account has no drop lane")
        lane = self.lanes[CONTROLLER_KEY]
        self.assertEqual(lane.host, CONTROLLER_HOST)
        # MUST NOT reuse claudy's drop-airuleset lane.
        self.assertNotEqual(lane.host, self.lanes[("airuleset", "claudy")].host)

    def test_lane_fields(self):
        lane = self.lanes[CONTROLLER_KEY]
        self.assertEqual(lane.topology, "controller")
        self.assertEqual(lane.origin_host, CONTROLLER_ORIGIN)
        self.assertTrue(dg._is_tailscale_host(lane.origin_host))
        self.assertEqual(lane.tunnel_uuid, dg._CONTROLLER_TUNNEL_UUID)
        self.assertTrue(lane.access)
        self.assertIsNone(lane.filedrop_port)   # cache-provided, like all generated
        self.assertIsNone(lane.tunnel_config)
        self.assertIsNone(lane.tunnel_service)

    def test_origin_is_the_controller_fleet_entry_not_a_hand_literal(self):
        # The origin equals the tailscale host on the controller's OWN fleet entry
        # (the claudy@controller service account), never a separate literal.
        entry = next(e for e in cli_fleet.REMOTE_HOSTS
                     if dl._nodename_for_entry(e) == "airuleset")
        self.assertEqual(self.lanes[CONTROLLER_KEY].origin_host, entry["host"])

    def test_port_distinct_and_in_range(self):
        lane = self.lanes[CONTROLLER_KEY]
        ports = [ln.port for k, ln in self.lanes.items() if k != CONTROLLER_KEY]
        self.assertNotIn(lane.port, ports, "controller-local port collides")
        self.assertTrue(dg.DROP_PORT_BASE <= lane.port <= dg.DROP_PORT_MAX)


# --------------------------------------------------------------------------- #
# every existing lane byte-identical; only the one key added
# --------------------------------------------------------------------------- #

class TestExistingLanesByteIdentical(unittest.TestCase):
    def setUp(self):
        self.lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)

    def test_only_the_controller_local_key_is_added(self):
        fleet_keys = set()
        for e in cli_fleet.REMOTE_HOSTS:
            if cli_fleet.is_paused(e):
                continue
            fleet_keys.add((dl._nodename_for_entry(e), e.get("user", "")))
        expected = fleet_keys | set(dg._SEED_DROP_LANES) | {CONTROLLER_KEY}
        self.assertEqual(set(self.lanes), expected,
                         "build_drop_lanes added/removed a key beyond the "
                         "controller-local lane")

    def test_claudy_lane_byte_identical(self):
        cl = self.lanes[("airuleset", "claudy")]
        self.assertEqual(cl.host, "drop-airuleset.newlevel.media")
        self.assertEqual(cl.port, 8877)
        self.assertEqual(cl.topology, "controller")
        self.assertEqual(cl.origin_host, "100.101.214.103")
        self.assertTrue(cl.access)
        self.assertIsNone(cl.filedrop_port)

    def test_representative_generated_lanes_unchanged(self):
        self.assertEqual(self.lanes[("dev1", "newlevel")].host,
                         "drop-dev1.newlevel.media")
        self.assertEqual(self.lanes[("subdev", "montalu1")].host,
                         "drop-subdev-montalu1.newlevel.media")

    def test_all_ports_unique_fleet_wide(self):
        ports = [ln.port for ln in self.lanes.values()]
        self.assertEqual(len(ports), len(set(ports)),
                         "duplicate drop port fleet-wide after slice G")


# --------------------------------------------------------------------------- #
# owner-only Access spec via the slice-D helper
# --------------------------------------------------------------------------- #

class TestOwnerOnlyAccessSpec(unittest.TestCase):
    def test_spec_present_owner_only(self):
        spec = dg.DROP_ACCESS_APPS.get(CONTROLLER_HOST)
        self.assertIsNotNone(spec, "controller-local lane has no Access spec")
        self.assertEqual(spec["allowed_emails"], [OWNER])
        self.assertEqual(spec["name"], "drop — controller")
        self.assertEqual(spec["session_duration"], "24h")
        self.assertEqual(spec["hostname"], CONTROLLER_HOST)

    def test_spec_shape_matches_hand_authored(self):
        spec = dg.DROP_ACCESS_APPS[CONTROLLER_HOST]
        self.assertEqual(set(spec),
                         {"hostname", "name", "allowed_emails", "session_duration"})


# --------------------------------------------------------------------------- #
# LOCAL marker write — only for a live lane
# --------------------------------------------------------------------------- #

class TestLocalMarkerWrite(unittest.TestCase):
    def _lane(self):
        return dg.DROP_LANES[CONTROLLER_KEY]

    def test_written_when_live(self):
        calls = []
        gl._write_local_controller_marker(
            {CONTROLLER_HOST: 8891},
            drop_lanes={CONTROLLER_KEY: self._lane()},
            write_marker=lambda h, p: calls.append((h, p)),
            out=io.StringIO())
        self.assertEqual(calls, [(CONTROLLER_HOST, 8891)])

    def test_not_written_when_not_live(self):
        calls = []
        gl._write_local_controller_marker(
            {},   # no live lane
            drop_lanes={CONTROLLER_KEY: self._lane()},
            write_marker=lambda h, p: calls.append((h, p)),
            out=io.StringIO())
        self.assertEqual(calls, [])

    def test_not_written_when_lane_absent(self):
        calls = []
        gl._write_local_controller_marker(
            {CONTROLLER_HOST: 8891},
            drop_lanes={},   # no controller-local lane
            write_marker=lambda h, p: calls.append((h, p)),
            out=io.StringIO())
        self.assertEqual(calls, [])

    def test_marker_port_is_the_registry_port(self):
        # The marker records the LANE's registry port (the value reconcile put in
        # `live`), never a different port — mirrors slice B's ssh leg.
        calls = []
        gl._write_local_controller_marker(
            {CONTROLLER_HOST: 9999},
            drop_lanes={CONTROLLER_KEY: self._lane()},
            write_marker=lambda h, p: calls.append((h, p)),
            out=io.StringIO())
        self.assertEqual(calls, [(CONTROLLER_HOST, 9999)])


# --------------------------------------------------------------------------- #
# write-path: fake clients make the lane live, is_worktree_fn=False → marker
# --------------------------------------------------------------------------- #

class TestReconcileMakesControllerLocalLive(unittest.TestCase):
    def _drop_lanes(self):
        return {CONTROLLER_KEY: dg.DROP_LANES[CONTROLLER_KEY]}

    def _specs(self):
        return {CONTROLLER_HOST: {
            "hostname": CONTROLLER_HOST, "name": "drop — controller",
            "allowed_emails": [OWNER], "session_duration": "24h"}}

    def test_live_reconcile_marks_controller_local_live_then_marker(self):
        dns_c, _dt = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self._drop_lanes(),
            access_specs=self._specs(), dns_client=dns_c, access_client=acc_c,
            is_worktree_fn=lambda: False, out=out)
        self.assertTrue(all_ok)
        self.assertEqual(live, {CONTROLLER_HOST: dg.DROP_LANES[CONTROLLER_KEY].port})
        # feed the produced `live` to the LOCAL marker leg
        calls = []
        gl._write_local_controller_marker(
            live, drop_lanes=self._drop_lanes(),
            write_marker=lambda h, p: calls.append((h, p)), out=out)
        self.assertEqual(
            calls, [(CONTROLLER_HOST, dg.DROP_LANES[CONTROLLER_KEY].port)])


# --------------------------------------------------------------------------- #
# worktree refusal still blocks the live write
# --------------------------------------------------------------------------- #

class TestWorktreeRefusalStillBlocks(unittest.TestCase):
    def test_worktree_refuses_live_reconcile_no_api_no_marker(self):
        dns_c, dt = _dns(records=[])
        acc_c, at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False,
            drop_lanes={CONTROLLER_KEY: dg.DROP_LANES[CONTROLLER_KEY]},
            access_specs={CONTROLLER_HOST: {
                "hostname": CONTROLLER_HOST, "name": "drop — controller",
                "allowed_emails": [OWNER], "session_duration": "24h"}},
            dns_client=dns_c, access_client=acc_c,
            is_worktree_fn=lambda: True, out=out)
        self.assertFalse(all_ok)
        self.assertEqual((results, live), ([], {}))
        self.assertEqual(dt.calls, [], "no DNS API call from a worktree")
        self.assertEqual(at.calls, [], "no Access API call from a worktree")
        self.assertIn("REFUSING", out.getvalue())
        # the marker leg then writes nothing (empty live)
        calls = []
        gl._write_local_controller_marker(
            live, drop_lanes={CONTROLLER_KEY: dg.DROP_LANES[CONTROLLER_KEY]},
            write_marker=lambda h, p: calls.append((h, p)), out=out)
        self.assertEqual(calls, [])


# --------------------------------------------------------------------------- #
# public_url_channel_fact on a faked controller → ok
# --------------------------------------------------------------------------- #

class TestPublicChannelFactOnController(unittest.TestCase):
    def _marker(self, d):
        lane = dg.DROP_LANES[CONTROLLER_KEY]
        p = Path(d) / "airuleset-drop.conf"
        p.write_text("host=%s\nport=%d\n" % (lane.host, lane.port))
        return str(p)

    def test_ok_when_live_and_probe_answers(self):
        with tempfile.TemporaryDirectory() as d:
            marker = self._marker(d)
            for code in (200, 302, 404):
                got = dl.public_url_channel_fact(
                    marker_path=marker, nodename="airuleset",
                    username="airuleset", probe=lambda url, c=code: c)
                self.assertEqual(got, "ok", "code %d should read ok" % code)

    def test_probe_hits_the_controller_s_path(self):
        with tempfile.TemporaryDirectory() as d:
            marker = self._marker(d)
            seen = {}
            dl.public_url_channel_fact(
                marker_path=marker, nodename="airuleset", username="airuleset",
                probe=lambda url: seen.setdefault("url", url) and 200 or 200)
            self.assertEqual(seen["url"], "https://%s/s/" % CONTROLLER_HOST)

    def test_marker_absent_is_fallback_not_ok(self):
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "nope.conf")
            got = dl.public_url_channel_fact(
                marker_path=missing, nodename="airuleset", username="airuleset",
                probe=lambda url: 200)
            self.assertEqual(got, "fallback:marker-absent")


# --------------------------------------------------------------------------- #
# local filedrop-port harvest (same cache as slice A)
# --------------------------------------------------------------------------- #

class TestLocalFiledropPortHarvest(unittest.TestCase):
    def test_harvest_writes_the_controller_key_into_the_cache(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "drop-lanes.json"
            with mock.patch.object(dl, "DROP_LANES_CACHE", cache):
                got = dl.harvest_local_controller_filedrop_port(
                    measure_fn=lambda: 8790,
                    drop_lanes={CONTROLLER_KEY: dg.DROP_LANES[CONTROLLER_KEY]})
            self.assertEqual(got, 8790)
            data = json.loads(cache.read_text())
            self.assertEqual(data.get("airuleset/airuleset"), 8790)

    def test_harvest_noop_when_measure_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "drop-lanes.json"
            with mock.patch.object(dl, "DROP_LANES_CACHE", cache):
                got = dl.harvest_local_controller_filedrop_port(
                    measure_fn=lambda: None,
                    drop_lanes={CONTROLLER_KEY: dg.DROP_LANES[CONTROLLER_KEY]})
            self.assertIsNone(got)
            self.assertFalse(cache.exists(), "no cache write on a None port")

    def test_harvest_noop_when_lane_absent(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "drop-lanes.json"
            with mock.patch.object(dl, "DROP_LANES_CACHE", cache):
                got = dl.harvest_local_controller_filedrop_port(
                    measure_fn=lambda: 8790, drop_lanes={})
            self.assertIsNone(got)
            self.assertFalse(cache.exists())


if __name__ == "__main__":
    unittest.main()
