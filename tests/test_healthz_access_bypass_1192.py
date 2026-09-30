"""#1192 scope addition — a Cloudflare Access BYPASS for exactly `/healthz` on
every drop-lane Access app.

Owner ROZHODNUTÉ (30.9.2026, answer "1"): YES to the token-free "I am alive"
check. On an Access-gated drop lane the edge answers 302 (the login redirect)
BEFORE the tunnel, so a dead tunnel looked alive and the public-link probe could
not tell. A path-scoped Access application `<drop-host>/healthz` with ONE bypass
policy lets the probe reach the endpoint's own `/healthz` (204 live, 530/1033
dead). The main app and its Allow policy are untouched; the bypass is applied by
the same drop-lane Access reconcile, idempotently.

All Cloudflare traffic goes through a FAKE, stateful transport — no network.
"""
import unittest
from unittest import mock

import cli_drop_gateway as dg
import cli_drop_golive as gl
import cli_webterm_access as acc

HOST = "drop-subdev-david4.newlevel.media"
SPEC = {"hostname": HOST, "name": "drop — david4",
        "allowed_emails": ["david@grena.sk", "drlik.zbynek@gmail.com"],
        "session_duration": "720h"}


class StatefulAccess:
    """GET /apps lists the apps created so far; POST adds one; PUT replaces."""

    def __init__(self, apps=None):
        self.apps = list(apps or [])
        self.calls = []
        self.bodies = []

    def __call__(self, method, path, body):
        self.calls.append((method, path.split("?", 1)[0]))
        self.bodies.append((method, body))
        base = path.split("?", 1)[0]
        if method == "GET" and base.endswith("/apps"):
            return 200, {"success": True, "result": list(self.apps)}
        if method == "POST" and base.endswith("/apps"):
            app = dict(body, id="app-%d" % (len(self.apps) + 1))
            self.apps.append(app)
            return 201, {"success": True, "result": app}
        if method == "PUT" and "/apps/" in base:
            app_id = base.rsplit("/", 1)[-1]
            self.apps = [dict(body, id=app_id) if a["id"] == app_id else a
                         for a in self.apps]
            return 200, {"success": True, "result": dict(body, id=app_id)}
        return 404, {"success": False, "errors": [{"message": "unexpected"}]}


def _lane(access=True):
    return dg.DropLane(
        host=HOST, port=8873, tunnel_uuid="u", tunnel_config=None,
        tunnel_service=None, tunnel_system_unit=False, access=access,
        gateway_account=None, topology="controller", origin_host="100.64.0.1",
        filedrop_port=8790)


def _client(transport):
    return acc.AccessClient("acct", token="tok", transport=transport)


class TestBypassPayload(unittest.TestCase):
    def test_path_scoped_single_bypass_policy_for_everyone(self):
        p = gl.healthz_bypass_payload(HOST)
        self.assertEqual(p["domain"], HOST + "/healthz")
        self.assertEqual(p["type"], "self_hosted")
        self.assertEqual(len(p["policies"]), 1)
        pol = p["policies"][0]
        self.assertEqual(pol["decision"], "bypass")
        self.assertEqual(pol["include"], [{"everyone": {}}])

    def test_never_a_whole_host_bypass(self):
        for bad in ("", HOST + "/x", "a b"):
            with self.assertRaises(ValueError):
                gl.healthz_bypass_payload(bad)


class TestReconcileLane(unittest.TestCase):
    def reconcile(self, transport, dry_run=False, lane=None):
        return gl.reconcile_access_for_lane(
            lane or _lane(), dry_run=dry_run, access_client=_client(transport),
            access_specs={HOST: SPEC})

    def test_creates_main_app_and_separate_bypass_app(self):
        t = StatefulAccess()
        ok, _action, msg = self.reconcile(t)
        self.assertTrue(ok, msg)
        by_domain = {a["domain"]: a for a in t.apps}
        self.assertEqual(set(by_domain), {HOST, HOST + "/healthz"})
        # the main app keeps its Allow policy, byte-identical to the builder
        self.assertEqual({k: v for k, v in by_domain[HOST].items() if k != "id"},
                         acc.build_app_payload(SPEC))
        self.assertEqual(by_domain[HOST]["policies"][0]["decision"], "allow")
        self.assertEqual(by_domain[HOST + "/healthz"]["policies"][0]["decision"],
                         "bypass")
        self.assertIn("healthz", msg)

    def test_second_run_is_idempotent_no_duplicate_apps(self):
        t = StatefulAccess()
        self.reconcile(t)
        before = len(t.apps)
        t.calls.clear()
        ok, _a, msg = self.reconcile(t)
        self.assertTrue(ok, msg)
        self.assertEqual(len(t.apps), before)
        self.assertNotIn("POST", [m for m, _p in t.calls])

    def test_dry_run_only_reads(self):
        t = StatefulAccess()
        ok, _a, msg = self.reconcile(t, dry_run=True)
        self.assertTrue(ok, msg)
        self.assertEqual({m for m, _p in t.calls}, {"GET"})
        self.assertIn("healthz", msg)

    def test_token_only_lane_touches_nothing(self):
        t = StatefulAccess()
        ok, action, _m = self.reconcile(t, lane=_lane(access=False))
        self.assertTrue(ok)
        self.assertEqual(action, "none")
        self.assertEqual(t.calls, [])

    def test_bypass_write_failure_is_loud_but_keeps_the_lane(self):
        t = StatefulAccess()

        def failing(method, path, body):
            if method == "POST" and body and body.get("domain", "").endswith("/healthz"):
                return 403, {"success": False, "errors": [{"message": "denied"}]}
            return t(method, path, body)

        ok, _a, msg = gl.reconcile_access_for_lane(
            _lane(), dry_run=False, access_client=_client(failing),
            access_specs={HOST: SPEC})
        self.assertTrue(ok, msg)                    # Access itself is in place
        self.assertIn("healthz bypass FAILED", msg)
        self.assertEqual([a["domain"] for a in t.apps], [HOST])


class TestGatewayUsesTheOneReconcile(unittest.TestCase):
    def test_local_reconcile_delegates_to_the_lane_reconcile(self):
        seen = []

        def fake(lane, dry_run=True, access_client=None, access_specs=None,
                 is_worktree_fn=None):
            seen.append((lane.host, dry_run))
            return True, "ok", "Access applied; healthz bypass created"

        with mock.patch.object(gl, "reconcile_access_for_lane", fake):
            ok, msg = dg._reconcile_access(_lane(), dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(seen, [(HOST, True)])
        self.assertIn("healthz", msg)


if __name__ == "__main__":
    unittest.main()
