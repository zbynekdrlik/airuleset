"""#1115 Slice C — the ONE delivery-channel resolver every user-facing URL
producer asks, the single labelled fallback line, and the report-only
`public_url_channel` conformance fact.

Owner ruling 22.9.2026: every user-facing URL must offer the public Cloudflare
channel first; a tailscale/LAN URL is only ever an EXPLICIT, labelled fallback,
and a box that can only serve private URLs is flagged (conformance) before the
owner meets it. Acceptance C: each producer prints the public line first or the
labelled fallback line with the RIGHT reason (fixtures for live / no-lane /
pending / marker-absent / unreachable); conformance records ok/fallback/broken;
no token or secret value appears in any output.
"""
import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import cli_drop_lanes as dl  # noqa: E402


def _lane(host="drop-x.newlevel.media", port=8877, access=True):
    return types.SimpleNamespace(host=host, port=port, access=access)


# ---------------------------------------------------------------------------
# delivery_channel — the five states, resolved through injected gateway seams
# ---------------------------------------------------------------------------
class TestDeliveryChannelStates(unittest.TestCase):
    HOST = "drop-x.newlevel.media"

    def _call(self, *, lane, resolve_ret, access_specs, probe=None):
        return dl.delivery_channel(
            lane_lookup=lambda n, u: lane,
            resolve=lambda **k: resolve_ret,
            access_specs=access_specs,
            probe=probe,
        )

    def test_live(self):
        base, reason = self._call(
            lane=_lane(self.HOST, access=True),
            resolve_ret=(self.HOST, 8877, "127.0.0.1"),
            access_specs={self.HOST: {"hostname": self.HOST}})
        self.assertEqual(reason, dl.CHANNEL_LIVE)
        self.assertEqual(base, "https://" + self.HOST)

    def test_live_token_only_lane_needs_no_access_spec(self):
        base, reason = self._call(
            lane=_lane(self.HOST, access=False),
            resolve_ret=(self.HOST, 8877, "127.0.0.1"),
            access_specs={})
        self.assertEqual(reason, dl.CHANNEL_LIVE)
        self.assertEqual(base, "https://" + self.HOST)

    def test_no_lane(self):
        base, reason = self._call(lane=None, resolve_ret=None, access_specs={})
        self.assertIsNone(base)
        self.assertEqual(reason, dl.CHANNEL_NO_LANE)

    def test_pending_access_lane_without_spec(self):
        # A registered ACCESS lane with no DROP_ACCESS_APPS spec can never go
        # live (Slice-B reconcile fail-closed) -> PENDING, distinct from a plain
        # missing marker. resolve is NOT consulted (short-circuits before it).
        sentinel = {"called": False}

        def _resolve(**k):
            sentinel["called"] = True
            return (self.HOST, 8877, "127.0.0.1")

        base, reason = dl.delivery_channel(
            lane_lookup=lambda n, u: _lane(self.HOST, access=True),
            resolve=_resolve, access_specs={})
        self.assertIsNone(base)
        self.assertEqual(reason, dl.CHANNEL_PENDING)
        self.assertFalse(sentinel["called"])

    def test_marker_absent(self):
        base, reason = self._call(
            lane=_lane(self.HOST, access=True),
            resolve_ret=None,
            access_specs={self.HOST: {"hostname": self.HOST}})
        self.assertIsNone(base)
        self.assertEqual(reason, dl.CHANNEL_MARKER_ABSENT)

    def test_unreachable_when_probe_fails(self):
        base, reason = self._call(
            lane=_lane(self.HOST, access=False),
            resolve_ret=(self.HOST, 8877, "127.0.0.1"),
            access_specs={}, probe=lambda host: False)
        self.assertIsNone(base)
        self.assertEqual(reason, dl.CHANNEL_UNREACHABLE)

    def test_live_when_probe_passes(self):
        seen = {}

        def _probe(host):
            seen["host"] = host
            return True

        base, reason = self._call(
            lane=_lane(self.HOST, access=False),
            resolve_ret=(self.HOST, 8877, "127.0.0.1"),
            access_specs={}, probe=_probe)
        self.assertEqual(reason, dl.CHANNEL_LIVE)
        self.assertEqual(seen["host"], self.HOST)

    def test_fail_safe_on_seam_error(self):
        # #1115 slice D: a gateway seam that raises degrades to (None, error:...)
        # — never a raise, and never no-lane (which hid a real breakage behind a
        # benign "no lane" on the conformance surface, slice-C review B).
        def _boom(*a, **k):
            raise RuntimeError("gateway broken")

        base, reason = dl.delivery_channel(
            lane_lookup=_boom, resolve=lambda **k: None, access_specs={})
        self.assertIsNone(base)
        self.assertTrue(reason == dl.CHANNEL_ERROR
                        or reason.startswith(dl.CHANNEL_ERROR + ":"))
        self.assertNotEqual(reason, dl.CHANNEL_NO_LANE)

    def test_fail_safe_reason_carries_exception_class_name(self):
        class WeirdGatewayError(Exception):
            pass

        def _boom(*a, **k):
            raise WeirdGatewayError("some detail")

        base, reason = dl.delivery_channel(
            lane_lookup=_boom, resolve=lambda **k: None, access_specs={})
        self.assertIsNone(base)
        self.assertEqual(reason, "%s:WeirdGatewayError" % dl.CHANNEL_ERROR)

    def test_fail_safe_never_leaks_the_exception_message(self):
        # The message can carry a path / host / credential in the wild; the reason
        # must expose the exception CLASS only, never the message text.
        leaky = "connection refused to internal-host-42 with detail-marker-xyz"

        def _boom(*a, **k):
            raise RuntimeError(leaky)

        base, reason = dl.delivery_channel(
            lane_lookup=_boom, resolve=lambda **k: None, access_specs={})
        self.assertIsNone(base)
        self.assertNotIn("internal-host-42", reason)
        self.assertNotIn("detail-marker-xyz", reason)


# ---------------------------------------------------------------------------
# public_url_channel_fact — ok / fallback:<reason> / broken:<code>
# ---------------------------------------------------------------------------
class TestPublicUrlChannelFact(unittest.TestCase):
    HOST = "drop-x.newlevel.media"

    def _live_channel(self):
        return mock.patch.object(
            dl, "delivery_channel",
            lambda **k: ("https://" + self.HOST, dl.CHANNEL_LIVE))

    def test_ok_on_200(self):
        with self._live_channel():
            self.assertEqual(dl.public_url_channel_fact(probe=lambda u: 200), "ok")

    def test_ok_on_302_access(self):
        with self._live_channel():
            self.assertEqual(dl.public_url_channel_fact(probe=lambda u: 302), "ok")

    def test_probe_hits_s_path(self):
        seen = {}

        def _probe(u):
            seen["url"] = u
            return 200
        with self._live_channel():
            dl.public_url_channel_fact(probe=_probe)
        self.assertEqual(seen["url"], "https://%s/s/" % self.HOST)

    def test_ok_on_404_reachable(self):
        # A bare token-less /s/ 404s at the origin on a token-only lane — the
        # origin ANSWERED, so the channel is reachable = ok (slice-C review).
        with self._live_channel():
            self.assertEqual(dl.public_url_channel_fact(probe=lambda u: 404), "ok")

    def test_broken_on_5xx(self):
        with self._live_channel():
            self.assertEqual(dl.public_url_channel_fact(probe=lambda u: 502),
                             "broken:502")

    def test_broken_unreachable_on_none(self):
        with self._live_channel():
            self.assertEqual(dl.public_url_channel_fact(probe=lambda u: None),
                             "broken:unreachable")

    def test_fallback_no_lane(self):
        with mock.patch.object(dl, "delivery_channel",
                               lambda **k: (None, dl.CHANNEL_NO_LANE)):
            self.assertEqual(dl.public_url_channel_fact(probe=lambda u: 200),
                             "fallback:no-lane")

    def test_fallback_pending(self):
        with mock.patch.object(dl, "delivery_channel",
                               lambda **k: (None, dl.CHANNEL_PENDING)):
            self.assertEqual(dl.public_url_channel_fact(), "fallback:pending")

    def test_fallback_marker_absent(self):
        with mock.patch.object(dl, "delivery_channel",
                               lambda **k: (None, dl.CHANNEL_MARKER_ABSENT)):
            self.assertEqual(dl.public_url_channel_fact(), "fallback:marker-absent")

    def test_broken_error_on_resolver_exception(self):
        # #1115 slice D: the fail-safe error family is a BREAKAGE, not a benign
        # fallback — conformance renders exactly "broken:error" (the class name
        # in the reason is collapsed away here so the surface stays one token).
        for reason in (dl.CHANNEL_ERROR, dl.CHANNEL_ERROR + ":RuntimeError"):
            with mock.patch.object(dl, "delivery_channel",
                                   lambda r=reason, **k: (None, r)):
                self.assertEqual(dl.public_url_channel_fact(), "broken:error")


# ---------------------------------------------------------------------------
# Conformance: the report-only public_url_channel fact + status-row suffix
# ---------------------------------------------------------------------------
class TestConformancePublicUrlChannel(unittest.TestCase):
    def _run(self, fact):
        from watchdog import conformance
        state = {}
        with tempfile.TemporaryDirectory() as td:
            cm = Path(td) / "CLAUDE.md"
            cm.write_text("x", encoding="utf-8")
            conformance.run_conformance_check(
                now=1000.0, state=state, dry_run=False,
                repo_root=td, claude_md_path=str(cm),
                baseline_path=str(Path(td) / "baseline.json"),
                git_run=lambda args, cwd, **k: (0, "aaaa1111"),
                timer_check=lambda *a, **k: "active",
                is_target_check=lambda: False,
                symlink_scan=lambda: [],
                doctrine_scan=lambda: {"high": 0, "medium": 0},
                root_guard_provisioned_fn=lambda: True,
                bashrc_drift_fn=lambda: 0,
                public_url_channel_fn=lambda: fact,
            )
        return state

    def test_records_ok(self):
        state = self._run("ok")
        self.assertEqual(state.get("public_url_channel"), "ok")

    def test_records_fallback(self):
        state = self._run("fallback:no-lane")
        self.assertEqual(state.get("public_url_channel"), "fallback:no-lane")

    def test_records_broken(self):
        state = self._run("broken:502")
        self.assertEqual(state.get("public_url_channel"), "broken:502")

    def test_broken_leaf_degrades_to_unknown_not_raise(self):
        def _boom():
            raise RuntimeError("leaf broken")
        # Run inline so the raising fn is used.
        from watchdog import conformance
        st = {}
        with tempfile.TemporaryDirectory() as td:
            cm = Path(td) / "CLAUDE.md"
            cm.write_text("x", encoding="utf-8")
            conformance.run_conformance_check(
                now=1000.0, state=st, dry_run=False, repo_root=td,
                claude_md_path=str(cm),
                baseline_path=str(Path(td) / "b.json"),
                git_run=lambda args, cwd, **k: (0, "aaaa1111"),
                timer_check=lambda *a, **k: "active",
                is_target_check=lambda: False, symlink_scan=lambda: [],
                doctrine_scan=lambda: {"high": 0, "medium": 0},
                root_guard_provisioned_fn=lambda: True,
                bashrc_drift_fn=lambda: 0,
                public_url_channel_fn=_boom)
        self.assertNotIn("public_url_channel", st)   # degraded, never recorded/raised

    def test_status_row_suffix_only_when_not_ok(self):
        from watchdog.conformance import conformance_status_row
        ok_row = conformance_status_row(
            {"conformance_last_check": 1, "public_url_channel": "ok"})
        self.assertNotIn("public-url-channel", ok_row)
        bad_row = conformance_status_row(
            {"conformance_last_check": 1, "public_url_channel": "fallback:no-lane"})
        self.assertIn("public-url-channel: fallback:no-lane", bad_row)
        self.assertIn(" · ", bad_row)


# ---------------------------------------------------------------------------
# Producer wiring: with no public lane cmd_upload / _secret_show / _secret_request
# print NO URL, name the reason + --private, exit 1 (#1192 owner decision 30.9.:
# the old private fallback printed tailscale URLs by default). cmd_share is
# covered by test_share_public_lane_1114.
# ---------------------------------------------------------------------------
class TestUploadFallbackLabelled(unittest.TestCase):
    def _run(self, reason):
        import airuleset
        import cli_drop_gateway as dg
        with mock.patch.object(dg, "resolve_public_lane_full", lambda *a, **k: None), \
             mock.patch.object(dl, "delivery_channel", lambda **k: (None, reason)), \
             mock.patch("filedrop.bind_ips", lambda: ["100.64.0.1"]), \
             mock.patch.object(airuleset, "_pick_free_port", lambda ips, rng: 8801), \
             mock.patch("subprocess.Popen"), \
             mock.patch("urllib.request.urlopen") as uo:
            uo.return_value = types.SimpleNamespace(status=200)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                 self.assertRaises(SystemExit) as cm:
                airuleset.cmd_upload(types.SimpleNamespace(dir=None, ttl=10, port=None))
            self.assertEqual(cm.exception.code, 1)
            return out.getvalue(), err.getvalue()

    def test_no_lane(self):
        out, err = self._run(dl.CHANNEL_NO_LANE)
        self.assertNotIn("100.64.0.1", out)              # no private URL by default
        self.assertIn("upload: !!! ŽIADNA URL", err)
        self.assertIn("no public lane on this box: no lane for this account", err)
        self.assertIn("--private", err)

    def test_pending(self):
        _out, err = self._run(dl.CHANNEL_PENDING)
        self.assertIn("no public lane on this box: lane pending", err)


class TestSecretShowFallbackLabelled(unittest.TestCase):
    """_secret_show: no public lane -> NO URL, ONE loud reason line naming
    --private, exit 1 (#1192), and NEVER a token/secret value in the output."""

    def _run(self, reason):
        import cli_vault
        import filedrop
        with tempfile.TemporaryDirectory() as td:
            st = mock.MagicMock()
            st.DEFAULT_ENDPOINT_TTL_S = 300
            st.log_path.return_value = Path(td) / "vault.log"
            opener = types.SimpleNamespace(
                open=lambda u, timeout=2: types.SimpleNamespace(status=200))
            with mock.patch.object(filedrop, "bind_ips", lambda: ["100.64.0.1"]), \
                 mock.patch.object(filedrop, "vault", st, create=True), \
                 mock.patch.object(cli_vault, "_secret_show_source",
                                   lambda args, s: ("env", "X", "X")), \
                 mock.patch.object(cli_vault, "_secret_bindable", lambda ip: True), \
                 mock.patch.object(cli_vault, "_secret_select_ips",
                                   lambda ips, allow_plain=False: (["100.64.0.1"], [])), \
                 mock.patch.object(cli_vault, "_secret_public_lane",
                                   lambda args: (None, None, None)), \
                 mock.patch.object(dl, "delivery_channel",
                                   lambda **k: (None, reason)), \
                 mock.patch.object(cli_vault, "_pick_free_port",
                                   lambda ips, rng: 8851), \
                 mock.patch.object(cli_vault, "_secret_probe_urls",
                                   lambda ips, port: ["http://100.64.0.1:8851/h"]), \
                 mock.patch.object(cli_vault, "_secret_opener", lambda: opener), \
                 mock.patch.object(cli_vault, "_secret_health_url",
                                   lambda ip, port: "http://%s:%d/h" % (ip, port)), \
                 mock.patch.object(cli_vault, "_secret_url_line",
                                   lambda ip, port, token, iface=None:
                                   "http://%s:%d/PRIVATE/" % (ip, port)), \
                 mock.patch("subprocess.Popen",
                            return_value=types.SimpleNamespace(pid=4321,
                                                               poll=lambda: None)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                     self.assertRaises(SystemExit) as cm:
                    cli_vault._secret_show(types.SimpleNamespace(
                        name="X", file=None, cmd=[], ttl=30, port=None,
                        allow_plain=False, public=False))
                self.assertEqual(cm.exception.code, 1)
                return out.getvalue(), err.getvalue()

    def test_marker_absent(self):
        out, err = self._run(dl.CHANNEL_MARKER_ABSENT)
        self.assertNotIn("http://", out)                # no private URL by default
        self.assertIn("secret show: !!! ŽIADNA URL", err)
        self.assertIn("no public lane on this box: go-live marker absent", err)
        self.assertIn("--private", err)

    def test_no_secret_value_leaks(self):
        out, err = self._run(dl.CHANNEL_NO_LANE)
        combined = out + err
        self.assertIn("no public lane on this box: no lane for this account", err)
        self.assertNotIn("AIRULESET_VAULT_TOKEN", combined)


class TestSecretRequestFallbackLabelled(unittest.TestCase):
    """_secret_request: no public lane -> NO URL, ONE loud reason line
    (prog='secret'), exit 1 (#1192), and NEVER a token/secret value in the output."""

    def _run(self, reason):
        import cli_vault
        import filedrop
        with tempfile.TemporaryDirectory() as td:
            st = mock.MagicMock()
            st.DEFAULT_ENDPOINT_TTL_S = 300
            st.DEFAULT_KEEP_S = 3600
            st.state.return_value = "absent"
            st.check_name.return_value = None
            st.register_request.return_value = "NONCEVALUE"
            st.log_path.return_value = Path(td) / "vault.log"
            opener = types.SimpleNamespace(
                open=lambda u, timeout=2: types.SimpleNamespace(status=200))
            with mock.patch.object(filedrop, "bind_ips", lambda: ["100.64.0.1"]), \
                 mock.patch.object(filedrop, "vault", st, create=True), \
                 mock.patch.object(cli_vault, "_secret_request_names",
                                   lambda args: ["X"]), \
                 mock.patch.object(cli_vault, "_secret_parse_persist_map",
                                   lambda args, names: {}), \
                 mock.patch.object(cli_vault, "_secret_bindable", lambda ip: True), \
                 mock.patch.object(cli_vault, "_secret_select_ips",
                                   lambda ips, allow_plain=False: (["100.64.0.1"], [])), \
                 mock.patch.object(cli_vault, "_secret_public_lane",
                                   lambda args: (None, None, None)), \
                 mock.patch.object(dl, "delivery_channel",
                                   lambda **k: (None, reason)), \
                 mock.patch.object(cli_vault, "_pick_free_port",
                                   lambda ips, rng: 8831), \
                 mock.patch.object(cli_vault, "_secret_probe_urls",
                                   lambda ips, port: ["http://100.64.0.1:8831/h"]), \
                 mock.patch.object(cli_vault, "_secret_opener", lambda: opener), \
                 mock.patch.object(cli_vault, "_secret_health_url",
                                   lambda ip, port: "http://%s:%d/h" % (ip, port)), \
                 mock.patch.object(cli_vault, "_secret_url_line",
                                   lambda ip, port, token, iface=None:
                                   "http://%s:%d/PRIVATE/" % (ip, port)), \
                 mock.patch("subprocess.Popen",
                            return_value=types.SimpleNamespace(pid=4322)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                     self.assertRaises(SystemExit) as cm:
                    cli_vault._secret_request(types.SimpleNamespace(
                        name="X", cmd=[], ttl=30, keep=None, port=None,
                        allow_plain=False, public=False, replace=False,
                        persist=None, persist_map=None))
                self.assertEqual(cm.exception.code, 1)
                self.st = st
                return out.getvalue(), err.getvalue()

    def test_pending(self):
        out, err = self._run(dl.CHANNEL_PENDING)
        self.assertNotIn("http://", out)                # no private URL by default
        self.assertIn("secret: !!! ŽIADNA URL", err)
        self.assertIn("no public lane on this box: lane pending", err)
        self.st.register_request.assert_not_called()   # refused before registering

    def test_no_secret_value_leaks(self):
        out, err = self._run(dl.CHANNEL_NO_LANE)
        combined = out + err
        self.assertIn("secret: !!! ŽIADNA URL", err)
        self.assertNotIn("AIRULESET_VAULT_TOKEN", combined)
        self.assertNotIn("NONCEVALUE", combined)


if __name__ == "__main__":
    unittest.main()
