"""#1115 slice D — Access specs for every GENERATED drop lane (owner-only).

The slice-B controller reconcile leaves every `access=True` lane with no
`DROP_ACCESS_APPS` spec PENDING go-live (fail-closed: no DNS, no marker). Slice D
adds one owner-only spec per generated access lane, derived by ONE helper from the
generated lane list — never hand-typed per account (decision 5787094428). The
hand-authored specs (gk, david1, david2-4, dominika) stay authoritative and
byte-identical.

All offline: enumerates the real registry + a faked controller reconcile
(injectable DNS/Access transports). NO Cloudflare API, NO ssh, NO --apply.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as dg           # noqa: E402
import cli_drop_lanes as dl             # noqa: E402
import cli_drop_golive as gl            # noqa: E402

from test_drop_golive_1115 import _dns, _acc   # noqa: E402


OWNER = "drlik.zbynek@gmail.com"

# The hand-authored specs that MUST survive the merge byte-for-byte (the lock).
_HAND_AUTHORED = {
    "drop-david.newlevel.media": {
        "hostname": "drop-david.newlevel.media",
        "name": "drop — david1",
        "allowed_emails": ["david@grena.sk", OWNER],
        "session_duration": "24h",
    },
    "drop-subdev-david2.newlevel.media": {
        "hostname": "drop-subdev-david2.newlevel.media",
        "name": "drop — david2",
        "allowed_emails": ["david@grena.sk", OWNER],
        "session_duration": "24h",
    },
    "drop-subdev-david3.newlevel.media": {
        "hostname": "drop-subdev-david3.newlevel.media",
        "name": "drop — david3",
        "allowed_emails": ["david@grena.sk", OWNER],
        "session_duration": "24h",
    },
    "drop-subdev-david4.newlevel.media": {
        "hostname": "drop-subdev-david4.newlevel.media",
        "name": "drop — david4",
        "allowed_emails": ["david@grena.sk", OWNER],
        "session_duration": "24h",
    },
    "drop-subdev-dominika.newlevel.media": {
        "hostname": "drop-subdev-dominika.newlevel.media",
        "name": "drop — dominika",
        "allowed_emails": ["dominika@grena.sk", OWNER],
        "session_duration": "24h",
    },
    "drop-gk.newlevel.media": {
        "hostname": "drop-gk.newlevel.media",
        "name": "drop — gatekeeper",
        "allowed_emails": [OWNER],
        "session_duration": "24h",
    },
}


class TestGeneratedAccessSpecHelper(unittest.TestCase):
    """The pure helper, exercised on synthetic lanes (no registry coupling)."""

    def _lane(self, host, access=True):
        return dg.DropLane(
            host=host, port=8900, tunnel_uuid=None, tunnel_config=None,
            tunnel_service=None, tunnel_system_unit=False, access=access,
            gateway_account=None, topology="controller", origin_host="100.64.0.1")

    def test_owner_only_include_for_each_access_lane(self):
        lanes = {("n", "u"): self._lane("drop-n.newlevel.media")}
        specs = dl.generated_access_specs(lanes, {}, [OWNER])
        self.assertEqual(list(specs), ["drop-n.newlevel.media"])
        self.assertEqual(specs["drop-n.newlevel.media"]["allowed_emails"], [OWNER])
        self.assertEqual(specs["drop-n.newlevel.media"]["session_duration"], "24h")

    def test_spec_shape_matches_hand_authored(self):
        lanes = {("n", "u"): self._lane("drop-n.newlevel.media")}
        spec = dl.generated_access_specs(lanes, {}, [OWNER])["drop-n.newlevel.media"]
        self.assertEqual(set(spec), {"hostname", "name", "allowed_emails",
                                     "session_duration"})
        self.assertEqual(spec["hostname"], "drop-n.newlevel.media")

    def test_token_only_lane_gets_no_spec(self):
        lanes = {("n", "u"): self._lane("drop-n.newlevel.media", access=False)}
        self.assertEqual(dl.generated_access_specs(lanes, {}, [OWNER]), {})

    def test_existing_host_is_skipped(self):
        host = "drop-gk.newlevel.media"
        lanes = {("n", "u"): self._lane(host)}
        specs = dl.generated_access_specs(lanes, {host: {"x": 1}}, [OWNER])
        self.assertNotIn(host, specs)      # hand-authored wins

    def test_name_derivation(self):
        cases = {
            "drop-airuleset.newlevel.media": "drop — airuleset",
            "drop-dev1.newlevel.media": "drop — dev1",
            "drop-subdev-miva1.newlevel.media": "drop — subdev-miva1",
            "drop-subdev-montalu8.newlevel.media": "drop — subdev-montalu8",
        }
        lanes = {(h, ""): self._lane(h) for h in cases}
        specs = dl.generated_access_specs(lanes, {}, [OWNER])
        for host, name in cases.items():
            self.assertEqual(specs[host]["name"], name)

    def test_include_is_copied_not_shared(self):
        # each spec must own its own list (a later mutation of one never bleeds).
        lanes = {("a", ""): self._lane("drop-a.newlevel.media"),
                 ("b", ""): self._lane("drop-b.newlevel.media")}
        specs = dl.generated_access_specs(lanes, {}, [OWNER])
        specs["drop-a.newlevel.media"]["allowed_emails"].append("x@y.z")
        self.assertEqual(specs["drop-b.newlevel.media"]["allowed_emails"], [OWNER])


class TestRegistryAllAccessLanesSpecced(unittest.TestCase):
    """The real registry: every access lane resolves to a spec after the merge."""

    def test_every_access_lane_has_a_spec(self):
        missing = [lane.host for lane in dg.DROP_LANES.values()
                   if lane.access and lane.host not in dg.DROP_ACCESS_APPS]
        self.assertEqual(missing, [], "access lanes without a DROP_ACCESS_APPS spec")

    def test_generated_specs_are_owner_only(self):
        # every access lane NOT hand-authored must carry the owner-ONLY include.
        for lane in dg.DROP_LANES.values():
            if not lane.access or lane.host in _HAND_AUTHORED:
                continue
            spec = dg.DROP_ACCESS_APPS[lane.host]
            self.assertEqual(spec["allowed_emails"], [OWNER],
                             "%s must be owner-only" % lane.host)

    def test_hand_authored_specs_are_byte_identical(self):
        for host, expected in _HAND_AUTHORED.items():
            self.assertEqual(dg.DROP_ACCESS_APPS.get(host), expected,
                             "hand-authored spec for %s changed" % host)

    def test_the_twelve_controller_pending_lanes_are_now_specced(self):
        for host in (
                "drop-airuleset.newlevel.media", "drop-dev1.newlevel.media",
                "drop-dev2.newlevel.media", "drop-subdev-miva1.newlevel.media",
                "drop-subdev-montalu1.newlevel.media",
                "drop-subdev-montalu8.newlevel.media"):
            self.assertIn(host, dg.DROP_ACCESS_APPS)
            self.assertEqual(dg.DROP_ACCESS_APPS[host]["allowed_emails"], [OWNER])


class TestControllerReconcileZeroPending(unittest.TestCase):
    """The design's live-shape acceptance, faked: a controller reconcile dry-run
    on the REAL registry + REAL specs reports 0 PENDING."""

    def test_dry_run_reports_zero_pending(self):
        import io
        dns_c, _dt = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        all_ok, results, _live = gl.reconcile_drop_lanes(
            dry_run=True, drop_lanes=dg.DROP_LANES,
            access_specs=dg.DROP_ACCESS_APPS,
            dns_client=dns_c, access_client=acc_c, out=io.StringIO())
        pending = [r["host"] for r in results if r.get("pending")]
        self.assertEqual(pending, [], "controller lanes still PENDING go-live")


if __name__ == "__main__":
    unittest.main()
