"""#1362 — on the controller MACHINE nothing of the file-drop listens on tailscale.

Tailscale's ts-input chain accepts tailscale0 before ufw, so a firewall rule
cannot close a tailscale bind (measured 18.11.). The controller's drop lanes
are fronted by cloudflared on the SAME box, so their origin is loopback, and
the filedrop servers bind loopback only. Both read ONE predicate: the box is
the controller node (`CONTROLLER_NODENAME`). The box-class marker is per
account (claudy reads `workstation`), so it is NOT that predicate.
"""
import io
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as dg  # noqa: E402
import cli_drop_lanes as dl  # noqa: E402
import cli_filedrop_watchdog as fw  # noqa: E402
import cli_vault_delivery as vd  # noqa: E402
import filedrop  # noqa: E402

CONTROLLER_TS = "100.101.214.103"
_TS_PAIRS = [(CONTROLLER_TS, "tailscale0"), ("10.77.9.5", "eth0")]


def _uname(node):
    return types.SimpleNamespace(nodename=node, sysname="Linux", release="",
                                 version="", machine="x86_64")


def _controller_lanes():
    return {k: lane for k, lane in dg.DROP_LANES.items()
            if k[0] == dl.CONTROLLER_NODENAME}


class _RealNodename(unittest.TestCase):
    """The real-nodename path: never let an ambient FILEDROP_NODENAME decide."""

    def setUp(self):
        env = mock.patch.dict("os.environ")
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FILEDROP_NODENAME", None)


class TestBindIps(_RealNodename):
    def test_nodename_seam_overrides_the_real_nodename(self):
        with mock.patch("os.uname", return_value=_uname("airuleset")), \
                mock.patch.dict("os.environ", {"FILEDROP_NODENAME": "dev1"}):
            self.assertFalse(filedrop.is_controller_box())
        with mock.patch("os.uname", return_value=_uname("dev1")), \
                mock.patch.dict("os.environ", {"FILEDROP_NODENAME": "airuleset"}):
            self.assertTrue(filedrop.is_controller_box())

    def test_controller_binds_loopback_only(self):
        with mock.patch("os.uname", return_value=_uname("airuleset")), \
                mock.patch.object(filedrop, "_iface_ips", return_value=_TS_PAIRS):
            self.assertEqual(filedrop.bind_ips(), ["127.0.0.1"])
            self.assertEqual(filedrop.host_ip(), "127.0.0.1")

    def test_other_box_unchanged(self):
        with mock.patch("os.uname", return_value=_uname("dev1")), \
                mock.patch.object(filedrop, "_iface_ips", return_value=_TS_PAIRS):
            self.assertEqual(filedrop.bind_ips(), [CONTROLLER_TS, "10.77.9.5"])

    def test_controller_unit_render_binds_loopback_only(self):
        with mock.patch("os.uname", return_value=_uname("airuleset")), \
                mock.patch.object(filedrop, "_iface_ips", return_value=_TS_PAIRS), \
                mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("FILEDROP_HOST", None)
            unit = fw._render_filedrop_unit(port=8788)
        self.assertNotIn(CONTROLLER_TS, unit)
        self.assertIn("127.0.0.1", unit)

    def test_predicate_is_the_drop_lane_node(self):
        # ONE predicate: the bind gate and the lane origin key on the same node.
        self.assertEqual(filedrop.CONTROLLER_NODENAME, dl.CONTROLLER_NODENAME)


class TestControllerLaneOrigin(unittest.TestCase):
    def test_every_controller_lane_origin_is_loopback(self):
        lanes = _controller_lanes()
        self.assertGreaterEqual(len(lanes), 2)          # airuleset + claudy
        for key, lane in lanes.items():
            self.assertEqual(lane.origin_host, "127.0.0.1", key)

    def test_other_lanes_keep_their_tailscale_origin(self):
        for key, lane in dg.DROP_LANES.items():
            if key[0] == dl.CONTROLLER_NODENAME or lane.topology != "controller":
                continue
            if lane.origin_host:
                self.assertNotEqual(lane.origin_host, "127.0.0.1", key)
                self.assertTrue(lane.origin_host.startswith("100."), key)

    def test_ingress_never_targets_the_controller_tailscale_ip(self):
        ports = {drop_lane.host: drop_lane for drop_lane in _controller_lanes().values()}
        cache = {dl.drop_lanes_cache_key(*k): 8788 + i
                 for i, k in enumerate(sorted(_controller_lanes()))}
        rules = dl.drop_ingress_rules_for_controller(dg.DROP_LANES, cache=cache)
        mine = [r for r in rules if r[0] in ports]
        self.assertTrue(mine)
        # /healthz/s + /s/ + catch-all for each controller lane
        self.assertEqual(len(mine), 3 * len(ports))
        for rule in mine:
            self.assertTrue(rule[-1].startswith("http://127.0.0.1:"), rule)
        for rule in rules:
            self.assertNotIn(CONTROLLER_TS, rule[-1], rule)

    def test_resolve_public_lane_full_binds_loopback_for_claudy(self):
        lane = dg.DROP_LANES[(dl.CONTROLLER_NODENAME, "claudy")]
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "marker"
            dg.write_drop_marker(lane.host, lane.port, path=marker)
            got = dg.resolve_public_lane_full(
                marker_path=marker, nodename=dl.CONTROLLER_NODENAME,
                username="claudy")
        self.assertEqual(got, (lane.host, lane.port, "127.0.0.1"))

    def test_loopback_origin_public_bind_stays_loopback(self):
        lane = dg.DROP_LANES[(dl.CONTROLLER_NODENAME, "airuleset")]
        ips = vd.public_bind_ips("127.0.0.1", [CONTROLLER_TS],
                                 lane_lookup=lambda: lane)
        self.assertEqual(ips, ["127.0.0.1"])


class TestPrivateRefusedOnController(_RealNodename):
    def test_select_lane_private_refuses_on_controller(self):
        lane = ("drop-controller.newlevel.media", 8891, "127.0.0.1")
        err = io.StringIO()
        with mock.patch("os.uname", return_value=_uname("airuleset")), \
                mock.patch.object(dg, "resolve_public_lane_full", return_value=lane), \
                redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            vd.select_lane("upload", lane, True)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("--private", err.getvalue())
        self.assertIn("drop-controller.newlevel.media", err.getvalue())

    def test_select_lane_private_unchanged_elsewhere(self):
        with mock.patch("os.uname", return_value=_uname("dev1")):
            self.assertEqual(vd.select_lane("upload", None, True),
                             (None, None, None))

    def test_share_private_refuses_before_copy_on_controller(self):
        args = types.SimpleNamespace(path="/nonexistent/x.txt", private=True)
        err = io.StringIO()
        with mock.patch("os.uname", return_value=_uname("airuleset")), \
                mock.patch.object(dg, "resolve_public_lane_full",
                                  return_value=("drop-controller.newlevel.media",
                                                8891, "127.0.0.1")), \
                mock.patch("filedrop.share.share",
                           side_effect=AssertionError("copied")), \
                redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            fw.cmd_share(args)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("drop-controller.newlevel.media", err.getvalue())


if __name__ == "__main__":
    unittest.main()
