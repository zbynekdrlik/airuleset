"""#1192 review round — the gaps a fresh adversarial review found in the
public-only contract (owner ROZHODNUTÉ 30.9.2026: no tailscale/LAN link by
default, a dead public link is never handed out).

  - `secret request --replace` must refuse BEFORE it cancels a pending request;
  - `share` on an Access lane (edge 302) must probe the bypassed token-free
    path — since #1238 `/healthz/s`, routed by the SAME rule pair as `/s/`:
    only THIS account's filedrop answering 204 + its port prints the URL;
    a 502 (the #1238 dead-URL case), 530, 302, 403 or no answer -> no URL;
  - `share` refuses before it copies anything, and never prints a private
    base URL in its diagnostics;
  - a `/healthz` bypass exception never un-lives the lane;
  - a bypass host must be a strict bare hostname (no wildcard, no port);
  - the dead `channel_fallback_line` voice is gone, the phrase is public.
All network and Cloudflare traffic is faked.
"""
import contextlib
import io
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import cli_drop_gateway as dg
import cli_drop_golive as gl
import cli_drop_lanes as dl
import cli_filedrop_watchdog as fw
import cli_vault
import cli_webterm_access as acc
import filedrop
from filedrop import server as fserver
from filedrop import share as fshare

HOST = "drop-subdev-david4.newlevel.media"
LANE = (HOST, 8873, "100.118.174.27")


def url_lines(text):
    return [ln for ln in text.splitlines() if "://" in ln]


class TestRequestReplaceRefusesFirst(unittest.TestCase):
    def test_no_lane_never_cancels_the_pending_request(self):
        st = mock.MagicMock()
        st.DEFAULT_ENDPOINT_TTL_S = 300
        st.DEFAULT_KEEP_S = 3600
        st.state.return_value = "pending"
        args = types.SimpleNamespace(
            name="A", cmd=[], ttl=30, keep=None, port=None, allow_plain=False,
            public=False, private=False, replace=True, persist=None,
            persist_map=None)
        with mock.patch.object(filedrop, "vault", st, create=True), \
             mock.patch.object(cli_vault, "_secret_request_names", lambda a: ["A"]), \
             mock.patch.object(cli_vault, "_secret_parse_persist_map",
                               lambda a, n: {}), \
             mock.patch.object(cli_vault, "_secret_public_lane",
                               lambda a: (None, None, None)), \
             mock.patch.object(dl, "delivery_channel",
                               lambda *a, **k: (None, dl.CHANNEL_NO_LANE)), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit) as cm:
            cli_vault._secret_request(args)
        self.assertEqual(cm.exception.code, 1)
        st.stop_endpoint.assert_not_called()
        st.forget.assert_not_called()


class _ShareHarness(unittest.TestCase):
    LOCAL_URL = "http://100.64.0.1:8790/TOKTOKTOKTOKTOKTOK12/rec.wav"

    def run_share(self, lane, codes, *, local_live=True):
        """`codes` maps "/s/" to its status and "/healthz/s" (#1238, the /s/
        route probe) to `(status, filedrop id header)`."""
        self.shared = []
        self.probed = []

        def status(url, timeout=3):
            self.probed.append(url)
            return codes["/s/"]

        def probe(url, timeout=3):
            self.probed.append(url)
            return codes.get("/healthz/s", (None, None))

        def fake_share(path, base_dir=None):
            self.shared.append(path)
            return self.LOCAL_URL, Path("/tmp/x/TOK/rec.wav")

        with mock.patch.object(fshare, "share", fake_share), \
             mock.patch.object(filedrop, "advertise_urls",
                               lambda port=None, path="": [self.LOCAL_URL]), \
             mock.patch.object(fw, "_filedrop_is_live",
                               lambda u, timeout=2: local_live), \
             mock.patch.object(fw, "_wait_filedrop_live", lambda u: False), \
             mock.patch.object(fw, "_restart_filedrop_service", lambda: None), \
             mock.patch.object(dg, "resolve_public_lane_full", lambda *a, **k: lane), \
             mock.patch.object(dg, "drop_lane_for_account",
                               lambda *a, **k: types.SimpleNamespace(access=True)), \
             mock.patch.object(dl, "delivery_channel",
                               lambda *a, **k: (None, dl.CHANNEL_NO_LANE)), \
             mock.patch.object(fw, "_public_share_status", status), \
             mock.patch.object(fw, "_public_share_probe", probe, create=True):
            out, err = io.StringIO(), io.StringIO()
            self.exit_code = None
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    fw.cmd_share(types.SimpleNamespace(path="/tmp/rec.wav",
                                                       private=False))
                except SystemExit as e:
                    self.exit_code = e.code
            return out.getvalue(), err.getvalue()


class TestShareAccessLane(_ShareHarness):
    """#1238 supersedes the #1192 `/healthz` verdict for `share`: `/healthz`
    is served by the no-path rule's EPHEMERAL endpoint, never by the `/s/`
    origin, so no code there proves the `/s/` route. Only the bypassed
    `/healthz/s` (the same rule pair as `/s/`) answered 204 by THIS account's
    filedrop (its instance id, port 8790 = the shared file's local port) prints
    the URL."""
    ROUTE = "https://%s/healthz/s" % HOST

    def refused(self, route):
        out, err = self.run_share(LANE, {"/s/": 302, "/healthz/s": route})
        self.assertEqual(self.exit_code, 1, out)
        self.assertEqual(url_lines(out), [], out)
        self.assertIn("--private", err)
        self.assertIn(self.ROUTE, self.probed)
        self.assertNotIn("NOT verified", err)
        return err

    def test_edge_302_with_dead_tunnel_prints_no_url(self):
        self.assertIn("1033", self.refused((530, None)))

    def test_edge_302_no_answer_on_the_route_probe_prints_no_url(self):
        self.refused((None, None))

    def test_a_502_from_the_endpoint_rule_prints_no_url(self):
        # the #1238 case: no /s/ rule yet, /healthz/s falls to the no-path rule
        # (the ephemeral endpoint port, nothing listening) -> 502. It used to be
        # read as "tunnel verified" and a dead URL was printed.
        self.assertIn("/s/", self.refused((502, None)))

    def test_route_probe_still_behind_access_prints_no_url(self):
        self.refused((302, None))      # its /healthz bypass is not applied

    def test_edge_only_403_on_the_route_probe_prints_no_url(self):
        self.refused((403, None))      # a WAF answer is the edge, not the route

    def test_a_sibling_accounts_filedrop_is_not_this_route(self):
        sibling = fserver.instance_id(8796)
        self.assertIn(sibling, self.refused((204, sibling)))

    def test_another_boxs_filedrop_on_the_same_port_is_not_this_route(self):
        # review round 1: 8788 serves on several boxes, so the id is not the port
        self.refused((204, "0123456789abcdef"))

    def test_a_204_without_the_id_header_is_not_a_filedrop(self):
        self.refused((204, None))

    def test_a_404_names_a_filedrop_that_predates_the_probe(self):
        # a box whose filedrop.service still runs pre-#1238 code answers 404
        self.assertIn("filedrop.service", self.refused((404, None)))

    def test_a_missing_rule_names_both_lane_kinds_fix(self):
        err = self.refused((502, None))
        self.assertIn("push", err)
        self.assertIn("drop-gateway --apply", err)

    def test_this_accounts_filedrop_on_the_route_prints_the_url(self):
        out, err = self.run_share(LANE, {"/s/": 302, "/healthz/s":
                                         (204, fserver.instance_id(8790))})
        self.assertIsNone(self.exit_code)
        lines = url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith(
            "https://%s/s/TOKTOKTOKTOKTOKTOK12/rec.wav " % HOST), lines)
        self.assertNotIn("NOT verified", err)

    def test_token_only_200_needs_no_route_probe(self):
        out, _err = self.run_share(LANE, {"/s/": 200, "/healthz/s": (530, None)})
        self.assertEqual(len(url_lines(out)), 1, out)
        self.assertNotIn(self.ROUTE, self.probed)


class TestShareSideEffects(_ShareHarness):
    def test_no_lane_refuses_before_copying_the_file(self):
        _out, _err = self.run_share(None, {"/s/": 200, "/healthz": 204})
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(self.shared, [])

    def test_down_diagnostic_names_no_private_url(self):
        _out, err = self.run_share(LANE, {"/s/": 200, "/healthz": 204},
                                   local_live=False)
        self.assertEqual(self.exit_code, 1)
        self.assertIn("file-drop", err)
        self.assertNotIn("http://", err)


class TestBypassRobustness(unittest.TestCase):
    SPEC = {"hostname": HOST, "name": "drop — david4",
            "allowed_emails": ["drlik.zbynek@gmail.com"], "session_duration": "720h"}

    def test_transport_exception_on_the_bypass_keeps_the_lane(self):
        apps = []

        def transport(method, path, body):
            if method == "GET":
                return 200, {"success": True, "result": list(apps)}
            if body and body.get("domain", "").endswith("/healthz"):
                raise urllib.error.URLError("timed out")
            apps.append(dict(body, id="a1"))
            return 201, {"success": True, "result": apps[-1]}

        lane = dg.DropLane(
            host=HOST, port=8873, tunnel_uuid="u", tunnel_config=None,
            tunnel_service=None, tunnel_system_unit=False, access=True,
            gateway_account=None, topology="controller", origin_host="100.64.0.1",
            filedrop_port=8790)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ok, _a, msg = gl.reconcile_access_for_lane(
                lane, dry_run=False,
                access_client=acc.AccessClient("acct", token="t", transport=transport),
                access_specs={HOST: self.SPEC})
        self.assertTrue(ok, msg)
        self.assertIn("healthz bypass FAILED", msg)
        self.assertIn("URLError", msg)
        self.assertIn("healthz bypass FAILED", err.getvalue())

    def test_bypass_host_must_be_a_strict_hostname(self):
        for bad in ("*.newlevel.media", "*", HOST + ":8443", "Drop_x.newlevel.media",
                    "-bad.newlevel.media", "DROP.newlevel.media", HOST + "/x"):
            with self.assertRaises(ValueError, msg=bad):
                gl.healthz_bypass_payload(bad)
        self.assertEqual(gl.healthz_bypass_payload(HOST)["domain"], HOST + "/healthz")


class TestFallbackVoiceRetired(unittest.TestCase):
    def test_dead_fallback_line_is_gone_and_the_phrase_is_public(self):
        self.assertFalse(hasattr(dl, "channel_fallback_line"))
        self.assertEqual(dl.fallback_phrase(dl.CHANNEL_MARKER_ABSENT),
                         "go-live marker absent")
        self.assertEqual(dl.fallback_phrase("error:Boom"), "error:Boom")


if __name__ == "__main__":
    unittest.main()
