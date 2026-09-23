"""#1115 Slice A — the drop-lane registry is GENERATED from the fleet, the
persistent filedrop port comes from a push-measured controller cache, and the
persistent filedrop service also binds loopback.

Design comment 5784187008 (Approach 1, Slice A). Acceptance A:
  - every non-paused REMOTE_HOSTS account has a lane; simap1 (paused) excluded;
  - the 8 hand-authored seed lanes are preserved BYTE-FOR-BYTE (a lock test
    against a literal snapshot — no hostname/port/origin churn);
  - drop ports are unique fleet-wide;
  - drop_ingress_rules_for_controller() reads the push-measured filedrop-port
    cache (~/.claude/drop-lanes.json) when present, falls back to the in-code
    DropLane.filedrop_port otherwise;
  - the persistent filedrop service ALSO binds 127.0.0.1, while the user-facing
    advertise URLs never gain a loopback entry.
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as dg          # noqa: E402
import cli_drop_lanes as dl            # noqa: E402 — the #1115 registry/cache leaf
import cli_fleet                       # noqa: E402
import filedrop                        # noqa: E402


# A literal SNAPSHOT of the 8 hand-authored lanes as they stood at #1115. The
# lock test asserts build_drop_lanes reproduces each field EXACTLY — a future
# accidental edit to _SEED_DROP_LANES (a churned hostname/port/origin) is caught.
_SEED_SNAPSHOT = {
    ("spinbike", "newlevel"): dict(
        host="drop-spinbike.newlevel.media", port=8828, access=False,
        topology="local", origin_host=None, filedrop_port=None,
        tunnel_system_unit=True, gateway_account=None),
    ("subdev", "david1"): dict(
        host="drop-david.newlevel.media", port=8870, access=True,
        topology="controller", origin_host="100.118.174.27", filedrop_port=8790,
        tunnel_system_unit=False, gateway_account="david1"),
    ("subdev", "david2"): dict(
        host="drop-subdev-david2.newlevel.media", port=8871, access=True,
        topology="controller", origin_host="100.118.174.27", filedrop_port=8796,
        tunnel_system_unit=False, gateway_account="david1"),
    ("subdev", "david3"): dict(
        host="drop-subdev-david3.newlevel.media", port=8872, access=True,
        topology="controller", origin_host="100.118.174.27", filedrop_port=8797,
        tunnel_system_unit=False, gateway_account="david1"),
    ("subdev", "david4"): dict(
        host="drop-subdev-david4.newlevel.media", port=8873, access=True,
        topology="controller", origin_host="100.118.174.27", filedrop_port=8798,
        tunnel_system_unit=False, gateway_account="david1"),
    ("subdev", "marek"): dict(
        host="drop-subdev-marek.newlevel.media", port=8874, access=False,
        topology="local", origin_host=None, filedrop_port=None,
        tunnel_system_unit=False, gateway_account="marek"),
    ("subdev", "dominika"): dict(
        host="drop-subdev-dominika.newlevel.media", port=8875, access=True,
        topology="local", origin_host=None, filedrop_port=8804,
        tunnel_system_unit=False, gateway_account="dominika"),
    ("odoo-gatekeeper", "gatekeeper"): dict(
        host="drop-gk.newlevel.media", port=8876, access=True,
        topology="controller", origin_host="100.90.94.41", filedrop_port=8788,
        tunnel_system_unit=False, gateway_account=None),
}


class TestSeedLanesPreservedByteForByte(unittest.TestCase):
    """The 8 hand-authored lanes must survive build_drop_lanes UNCHANGED."""

    def test_every_seed_lane_matches_snapshot(self):
        lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        for key, want in _SEED_SNAPSHOT.items():
            self.assertIn(key, lanes, "seed lane %s vanished" % (key,))
            lane = lanes[key]
            for field, val in want.items():
                self.assertEqual(getattr(lane, field), val,
                                 "seed lane %s.%s churned: %r != %r"
                                 % (key, field, getattr(lane, field), val))

    def test_seed_tunnel_uuids_unchanged(self):
        lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        self.assertEqual(lanes[("subdev", "david1")].tunnel_uuid,
                         dg._CONTROLLER_TUNNEL_UUID)
        self.assertEqual(lanes[("spinbike", "newlevel")].tunnel_uuid,
                         "4093c494-b31d-4eb7-8fcb-6c5948f5d4b2")
        self.assertEqual(lanes[("subdev", "marek")].tunnel_uuid,
                         "1e9555d1-4d19-4e86-8064-361506fbc2cd")


class TestEveryFleetAccountHasALane(unittest.TestCase):
    def test_every_non_paused_account_has_a_lane(self):
        lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        for entry in cli_fleet.REMOTE_HOSTS:
            if cli_fleet.is_paused(entry):
                continue
            key = (dg._nodename_for_entry(entry), entry["user"])
            self.assertIn(key, lanes,
                          "non-paused account %s@%s has no drop lane"
                          % (entry["user"], key[0]))

    def test_paused_simap1_excluded(self):
        lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        self.assertNotIn(("subdev", "simap1"), lanes,
                         "paused simap1 must have no lane")

    def test_the_14_previously_missing_accounts_now_have_lanes(self):
        lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        # controller is keyed by its REAL nodename "airuleset" (not the
        # "@controller" label) — review fix so drop_lane_for_account resolves.
        for key in [("dev1", "newlevel"), ("dev2", "newlevel"),
                    ("airuleset", "claudy"), ("forestshop-dev", "admin"),
                    ("forestshop-dev", "stepan"), ("subdev", "miva1"),
                    ("subdev", "montalu1"), ("subdev", "montalu8")]:
            self.assertIn(key, lanes, "%s should now have a lane" % (key,))


class TestDropPortsUniqueFleetWide(unittest.TestCase):
    def test_generated_ports_unique(self):
        lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        ports = [lane.port for lane in lanes.values()]
        dups = sorted({p for p in ports if ports.count(p) > 1})
        self.assertEqual(dups, [], "duplicate drop ports fleet-wide: %s" % dups)

    def test_generated_ports_in_range(self):
        lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        for key, lane in lanes.items():
            if key in _SEED_SNAPSHOT:
                continue  # seed 8828 is grandfathered below the range
            self.assertTrue(dg.DROP_PORT_BASE <= lane.port <= dg.DROP_PORT_MAX,
                            "generated %s port %d out of range %d-%d"
                            % (key, lane.port, dg.DROP_PORT_BASE, dg.DROP_PORT_MAX))


class TestGeneratedLaneFields(unittest.TestCase):
    def setUp(self):
        self.lanes = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)

    def test_subdev_generated_lane_rides_controller_tunnel(self):
        lane = self.lanes[("subdev", "montalu1")]
        self.assertEqual(lane.topology, "controller")
        self.assertEqual(lane.origin_host, "100.118.174.27")
        self.assertEqual(lane.tunnel_uuid, dg._CONTROLLER_TUNNEL_UUID)
        self.assertIsNone(lane.tunnel_config)
        self.assertIsNone(lane.filedrop_port)  # measured via cache, not in code

    def test_shared_box_hostname_carries_the_account(self):
        # subdev + forestshop-dev host >1 account -> drop-<box>-<user>.
        self.assertEqual(self.lanes[("subdev", "montalu1")].host,
                         "drop-subdev-montalu1.newlevel.media")
        self.assertEqual(self.lanes[("forestshop-dev", "admin")].host,
                         "drop-forestshop-dev-admin.newlevel.media")

    def test_single_account_box_hostname_omits_the_account(self):
        # dev1/dev2 each host ONE non-paused account -> drop-<box>. The
        # controller box's real nodename is "airuleset".
        self.assertEqual(self.lanes[("dev1", "newlevel")].host,
                         "drop-dev1.newlevel.media")
        self.assertEqual(self.lanes[("dev2", "newlevel")].host,
                         "drop-dev2.newlevel.media")
        self.assertEqual(self.lanes[("airuleset", "claudy")].host,
                         "drop-airuleset.newlevel.media")

    def test_generated_hosts_are_flat_single_level(self):
        for key, lane in self.lanes.items():
            if key in _SEED_SNAPSHOT:
                continue
            label = lane.host[: -len(".newlevel.media")]
            self.assertNotIn(".", label,
                             "%s multi-level — no *.newlevel.media cert" % lane.host)

    def test_origin_host_is_the_entry_tailscale(self):
        self.assertEqual(self.lanes[("dev1", "newlevel")].origin_host,
                         "100.104.8.125")
        self.assertEqual(self.lanes[("dev2", "newlevel")].origin_host,
                         "100.82.64.27")

    def test_no_generated_controller_lane_has_a_nontailscale_origin(self):
        # Review fix: a controller-topology origin MUST be a tailscale IP (the
        # drop hop stays on the tailnet). A public-only box (forestshop) would
        # otherwise proxy over the public internet in cleartext.
        for key, lane in self.lanes.items():
            if lane.topology == "controller" and lane.origin_host:
                self.assertTrue(dg._is_tailscale_host(lane.origin_host),
                                "%s controller origin %s is not tailscale"
                                % (key, lane.origin_host))

    def test_public_only_box_is_local_placeholder_not_controller(self):
        # forestshop-dev has a PUBLIC FQDN host -> a local-topology placeholder
        # (no controller origin, not rendered into the controller ingress).
        for user in ("admin", "stepan"):
            lane = self.lanes[("forestshop-dev", user)]
            self.assertEqual(lane.topology, "local")
            self.assertIsNone(lane.origin_host)
            self.assertIsNone(lane.tunnel_uuid)

    def test_forestshop_lanes_absent_from_controller_ingress(self):
        hosts = [r[0] for r in dg.drop_ingress_rules_for_controller(cache={})]
        self.assertNotIn("drop-forestshop-dev-admin.newlevel.media", hosts)
        self.assertNotIn("drop-forestshop-dev-stepan.newlevel.media", hosts)

    def test_is_tailscale_host(self):
        self.assertTrue(dg._is_tailscale_host("100.118.174.27"))
        self.assertTrue(dg._is_tailscale_host("100.64.0.1"))
        self.assertFalse(dg._is_tailscale_host("forestshop-dev.newlevel.media"))
        self.assertFalse(dg._is_tailscale_host("178.105.89.168"))
        self.assertFalse(dg._is_tailscale_host("10.77.0.1"))
        self.assertFalse(dg._is_tailscale_host(None))


class TestBuildDropLanesSynthetic(unittest.TestCase):
    def test_drop_block_override_wins(self):
        hosts = [{"name": "box9", "user": "u9", "host": "100.9.9.9",
                  "drop": {"host": "drop-custom.newlevel.media", "port": 8895,
                           "access": False}}]
        lanes = dg.build_drop_lanes(hosts)
        lane = lanes[("box9", "u9")]
        self.assertEqual(lane.host, "drop-custom.newlevel.media")
        self.assertEqual(lane.port, 8895)
        self.assertFalse(lane.access)

    def test_next_free_port_fallback_for_untabled_account(self):
        # An account with no seed, no drop block and no _GENERATED_DROP_PORTS
        # entry still gets a unique in-range port (deterministic next-free).
        hosts = [{"name": "novel-box", "user": "novel", "host": "100.7.7.7"}]
        lanes = dg.build_drop_lanes(hosts)
        lane = lanes[("novel-box", "novel")]
        self.assertTrue(dg.DROP_PORT_BASE <= lane.port <= dg.DROP_PORT_MAX)

    def test_paused_entry_excluded(self):
        hosts = [{"name": "p@subdev", "user": "p", "host": "100.1.1.1",
                  "paused": "owner test"}]
        lanes = dg.build_drop_lanes(hosts)
        self.assertNotIn(("subdev", "p"), lanes)

    def test_generation_is_deterministic(self):
        a = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        b = dg.build_drop_lanes(cli_fleet.REMOTE_HOSTS)
        self.assertEqual({k: v.port for k, v in a.items()},
                         {k: v.port for k, v in b.items()})


class TestNodenameDerivation(unittest.TestCase):
    def test_irregular_boxes_overridden(self):
        self.assertEqual(
            dg._nodename_for_entry({"name": "gatekeeper"}), "odoo-gatekeeper")
        self.assertEqual(
            dg._nodename_for_entry({"name": "spinbike-vps"}), "spinbike")

    def test_controller_maps_to_real_hostname_airuleset(self):
        # Review fix: the controller box's uname -n is `airuleset`, not the
        # `@controller` label — the key must match so resolution + cache agree.
        self.assertEqual(
            dg._nodename_for_entry({"name": "claudy@controller"}), "airuleset")

    def test_at_form_and_bare_form(self):
        self.assertEqual(
            dg._nodename_for_entry({"name": "montalu1@subdev"}), "subdev")
        self.assertEqual(dg._nodename_for_entry({"name": "dev1"}), "dev1")


class TestGracefulDegradation(unittest.TestCase):
    """Review fix: build_drop_lanes runs at IMPORT (imported by airuleset.py), so
    a port collision / range exhaustion must LOG + SKIP, never raise (a raise
    would crash the whole CLI + statusline + watchdog)."""

    def test_range_exhaustion_skips_not_raises(self):
        # More untabled accounts than the range can hold -> the overflow accounts
        # are skipped with a warning; build_drop_lanes never raises.
        span = dg.DROP_PORT_MAX - dg.DROP_PORT_BASE + 1
        hosts = [{"name": "x%d@subdev" % i, "user": "x%d" % i,
                  "host": "100.118.174.27"} for i in range(span + 5)]
        lanes = dg.build_drop_lanes(hosts)  # must NOT raise
        ports = [lane.port for lane in lanes.values()]
        self.assertEqual(len(ports), len(set(ports)), "ports still unique")
        # at least the seed lanes survive; some synthetic accounts are dropped.
        self.assertGreaterEqual(len(lanes), len(_SEED_SNAPSHOT))

    def test_duplicate_drop_block_port_skips_not_raises(self):
        # Two accounts forcing the SAME drop-block port -> the second is skipped
        # with a warning, not a raise.
        hosts = [
            {"name": "a@subdev", "user": "a", "host": "100.118.174.27",
             "drop": {"port": 8895}},
            {"name": "b@subdev", "user": "b", "host": "100.118.174.27",
             "drop": {"port": 8895}},
        ]
        lanes = dg.build_drop_lanes(hosts)  # must NOT raise
        got = [k for k in (("subdev", "a"), ("subdev", "b")) if k in lanes]
        self.assertEqual(len(got), 1, "exactly one of the colliding lanes kept")


class TestControllerIngressReadsCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "drop-lanes.json")

    def test_fallback_uses_in_code_filedrop_port(self):
        # empty cache -> the /s/ rule uses the in-code david1 filedrop_port 8790.
        rules = dg.drop_ingress_rules_for_controller(cache={})
        s_rule = ("drop-david.newlevel.media", "^/s/",
                  "http://100.118.174.27:8790")
        self.assertIn(s_rule, rules)

    def test_cache_overrides_in_code_port(self):
        dg.write_drop_lanes_cache({"subdev/david1": 9711}, path=self.cache)
        cache = dg.read_drop_lanes_cache(path=self.cache)
        rules = dg.drop_ingress_rules_for_controller(cache=cache)
        self.assertIn(("drop-david.newlevel.media", "^/s/",
                       "http://100.118.174.27:9711"), rules)
        self.assertNotIn(("drop-david.newlevel.media", "^/s/",
                          "http://100.118.174.27:8790"), rules)

    def test_default_cache_path_read_when_present(self):
        # cache=None -> the leaf reads DROP_LANES_CACHE; patch the LEAF's constant
        # (the read happens in cli_drop_lanes after the #993 split — the gateway
        # re-export is a separate binding).
        dg.write_drop_lanes_cache({"subdev/david1": 9712}, path=self.cache)
        with mock.patch.object(dl, "DROP_LANES_CACHE", Path(self.cache)):
            rules = dg.drop_ingress_rules_for_controller()
        self.assertIn(("drop-david.newlevel.media", "^/s/",
                       "http://100.118.174.27:9712"), rules)


class TestCacheReadWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "drop-lanes.json")

    def test_round_trip(self):
        dg.write_drop_lanes_cache({"subdev/miva1": 8811, "dev1/newlevel": 8788},
                                  path=self.cache)
        self.assertEqual(
            dg.read_drop_lanes_cache(path=self.cache),
            {"subdev/miva1": 8811, "dev1/newlevel": 8788})

    def test_missing_file_is_empty(self):
        self.assertEqual(
            dg.read_drop_lanes_cache(path=os.path.join(self.tmp, "nope.json")),
            {})

    def test_malformed_json_is_empty(self):
        Path(self.cache).write_text("{not json")
        self.assertEqual(dg.read_drop_lanes_cache(path=self.cache), {})

    def test_non_int_port_skipped(self):
        Path(self.cache).write_text('{"a/b": "x", "c/d": 8790}')
        self.assertEqual(dg.read_drop_lanes_cache(path=self.cache),
                         {"c/d": 8790})

    def test_write_creates_parent_dir(self):
        nested = os.path.join(self.tmp, "a", "b", "drop-lanes.json")
        dg.write_drop_lanes_cache({"x/y": 8790}, path=nested)
        self.assertTrue(os.path.exists(nested))

    def test_cache_key(self):
        self.assertEqual(dg.drop_lanes_cache_key("subdev", "montalu1"),
                         "subdev/montalu1")


class TestFiledropPortProbe(unittest.TestCase):
    def test_probe_snippet_runs_in_shell_and_parses(self):
        import subprocess
        snippet = dg.filedrop_port_probe_snippet()
        for shell in ("bash", "dash", "sh"):
            r = subprocess.run([shell, "-c", snippet],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (shell, r.stderr))
            port = dg.parse_filedrop_port(r.stdout)
            self.assertIsInstance(port, int, "%s produced no port: %r"
                                  % (shell, r.stdout))

    def test_probe_is_exit_free(self):
        # It must contain no `exit` TOKEN (that would end the remote sh before
        # the later gh/playwright post-checks). Word-boundary, not substring.
        self.assertIsNone(re.search(r"\bexit\b",
                                    dg.filedrop_port_probe_snippet()))

    def test_probe_emits_only_the_port_not_uname(self):
        # The key comes from the fleet entry (deploy loop), never `uname -n`.
        self.assertNotIn("uname", dg.filedrop_port_probe_snippet())
        self.assertNotIn("id -un", dg.filedrop_port_probe_snippet())

    def test_parse_ignores_noise_and_malformed(self):
        blob = ("some install output\n"
                "AIRULESET-FILEDROP-PORT 8811\n"
                "AIRULESET-FILEDROP-PORT bad extra\n"
                "AIRULESET-FILEDROP-PORT notanint\n"
                "unrelated\n")
        self.assertEqual(dg.parse_filedrop_port(blob), 8811)

    def test_parse_empty(self):
        self.assertIsNone(dg.parse_filedrop_port(""))
        self.assertIsNone(dg.parse_filedrop_port(None))


class TestSplitImportBothOrders(unittest.TestCase):
    """#1115 (#993 split): cli_drop_lanes is a true leaf (imports only cli_fleet,
    never cli_drop_gateway), so importing EITHER module first must succeed with no
    cross-import cycle. A fresh subprocess per order (a stale sys.modules would
    mask a cycle)."""

    def _fresh_import(self, first):
        import subprocess
        code = ("import %s as a; import cli_drop_gateway as g; "
                "import cli_drop_lanes as l; "
                "assert len(g.DROP_LANES) == len(l.build_drop_lanes("
                "  __import__('cli_fleet').REMOTE_HOSTS, seed=g._SEED_DROP_LANES,"
                "  drop_lane_cls=g.DropLane,"
                "  controller_tunnel_uuid=g._CONTROLLER_TUNNEL_UUID,"
                "  port_base=g.DROP_PORT_BASE, port_max=g.DROP_PORT_MAX)); "
                "print('OK', len(g.DROP_LANES))" % first)
        r = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         "import-%s-first failed: %s" % (first, r.stderr))
        self.assertIn("OK", r.stdout)

    def test_gateway_first(self):
        self._fresh_import("cli_drop_gateway")

    def test_leaf_first(self):
        self._fresh_import("cli_drop_lanes")

    def test_leaf_does_not_import_gateway(self):
        # the leaf must never import cli_drop_gateway AT MODULE LEVEL (that is the
        # load-time cycle: cli_drop_gateway imports THIS at its top). A
        # FUNCTION-LOCAL lazy import is cycle-safe (resolved at call time, after
        # both modules are fully loaded — proven by test_gateway_first /
        # test_leaf_first) and is the sanctioned pattern `delivery_channel` uses
        # (#1115 slice C), so scan only the MODULE-LEVEL statements
        # (`ast.parse(src).body`), never `ast.walk` which also catches nested
        # function-local imports.
        import ast
        src = (Path(__file__).resolve().parent.parent / "cli_drop_lanes.py").read_text()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Import):
                self.assertNotIn("cli_drop_gateway", [a.name for a in node.names])
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "cli_drop_gateway")


class TestLoopbackBind(unittest.TestCase):
    def test_with_loopback_appends_once(self):
        self.assertEqual(filedrop.with_loopback(["100.1.1.1", "10.77.0.1"]),
                         ["100.1.1.1", "10.77.0.1", "127.0.0.1"])
        self.assertEqual(filedrop.with_loopback(["127.0.0.1"]), ["127.0.0.1"])

    def test_persistent_unit_binds_loopback(self):
        import cli_filedrop_watchdog as fw
        with mock.patch.object(fw, "filedrop_bind_ips",
                               return_value=["100.1.1.1", "10.77.0.1"]):
            unit = fw._render_filedrop_unit(8788)
        self.assertIn("127.0.0.1", unit,
                      "persistent filedrop service must bind loopback (#1115)")
        # the seam back-ref still lands, and the private IPs are still bound.
        self.assertIn("100.1.1.1", unit)

    def test_advertise_urls_never_gain_loopback(self):
        # user-facing URLs stay byte-identical (no useless 127.0.0.1 entry).
        with mock.patch.object(filedrop, "bind_ips",
                               return_value=["100.1.1.1", "10.77.0.1"]):
            urls = filedrop.advertise_urls(port=8788, path="tok/")
        self.assertNotIn("127.0.0.1", " ".join(urls))
        self.assertEqual(len(urls), 2)


if __name__ == "__main__":
    unittest.main()
