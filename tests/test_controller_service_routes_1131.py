"""#1131 — controller-fronted SERVICE routes (first: backup.newlevel.media, the
fleet-backup dashboard), Access-first like the drop lanes.

ONE declared table, ``cli_drop_lanes.CONTROLLER_SERVICE_ROUTES``
(``{hostname, origin, name, allowed_emails, session_duration}``), is read by:
  - the controller ingress render (``cli_webterm._setup_controller_webterm`` ->
    ``controller-webterm.yml``) -> one ``hostname -> origin`` rule per COMPLETE
    route;
  - the go-live reconcile (``cli_drop_golive.reconcile_service_routes``) ->
    Access app FIRST, then a create-only proxied CNAME to the controller
    tunnel; an INCOMPLETE spec is PENDING (no Access write, no CNAME).

SECURITY BOUNDARY: every Cloudflare seam here is a FAKE (injected DNS + Access
transports that record calls). NO live API call, NO ~/.secrets read, NO ssh,
NO DNS write — the supervisor does the live apply at push (incident 5787949642:
a lane once created live CNAMEs; the worktree refusal is locked below too).
"""
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as dg           # noqa: E402
import cli_drop_golive as gl            # noqa: E402
import cli_drop_lanes as dl             # noqa: E402
import cli_webterm_access as acc        # noqa: E402
import cli_webterm_tunnel as tun        # noqa: E402

CONTROLLER_UUID = "f85ea304-920b-4ba4-96bc-a68001ce6fb4"
BACKUP_HOST = "backup.newlevel.media"
BACKUP_ORIGIN = "http://100.104.8.125:8795"
OWNER = "drlik.zbynek@gmail.com"


def _route(**over):
    r = {"hostname": "svc.newlevel.media", "origin": "http://100.64.0.9:9000",
         "name": "svc dashboard", "allowed_emails": [OWNER],
         "session_duration": "720h"}
    r.update(over)
    return r


class _Log:
    """One shared, ordered event log across BOTH fake transports, so the test
    can prove Access was written BEFORE the DNS CNAME."""

    def __init__(self):
        self.events = []


class FakeDns:
    def __init__(self, log, records=None):
        self.log = log
        self.calls = []
        self.bodies = []
        self._records = records or []

    def __call__(self, method, path, body):
        self.calls.append((method, path))
        self.bodies.append(body)
        self.log.events.append(("dns", method, path))
        if method == "GET" and "/zones?" in path:
            return 200, {"success": True, "result": [{"id": "zone-1"}]}
        if method == "GET" and "/dns_records?" in path:
            return 200, {"success": True, "result": list(self._records)}
        if method == "POST":
            return 201, {"success": True, "result": {"id": "new-rec"}}
        return 200, {"success": True, "result": {}}


class FakeAccess:
    def __init__(self, log, fail_write=False):
        self.log = log
        self.calls = []
        self.bodies = []
        self._fail = fail_write

    def __call__(self, method, path, body):
        self.calls.append((method, path))
        self.bodies.append(body)
        self.log.events.append(("access", method, path))
        base = path.split("?", 1)[0]
        if method == "GET" and base.endswith("/apps"):
            return 200, {"success": True, "result": []}
        if method == "POST" and base.endswith("/apps"):
            if self._fail:
                return 422, {"success": False,
                             "errors": [{"message": "create app failed"}]}
            return 201, {"success": True, "result": {"id": "new-app"}}
        return 200, {"success": True, "result": {}}


def _clients(fail_access=False, records=None):
    import cli_cloudflare_dns as dns
    log = _Log()
    dt = FakeDns(log, records=records)
    at = FakeAccess(log, fail_write=fail_access)
    return (dns.DnsClient(token="dns-tok", transport=dt),
            acc.AccessClient("acct", token="acc-tok", transport=at), dt, at, log)


def _never_worktree():
    return False


# --------------------------------------------------------------------------- #
# the declared table
# --------------------------------------------------------------------------- #

class TestServiceRouteTable(unittest.TestCase):
    def _backup(self):
        rows = [r for r in dl.CONTROLLER_SERVICE_ROUTES
                if r["hostname"] == BACKUP_HOST]
        self.assertEqual(len(rows), 1, "exactly one backup.newlevel.media route")
        return rows[0]

    def test_backup_route_origin(self):
        self.assertEqual(self._backup()["origin"], BACKUP_ORIGIN)

    def test_backup_route_is_owner_only_720h(self):
        r = self._backup()
        self.assertEqual(list(r["allowed_emails"]), [OWNER])
        self.assertEqual(r["session_duration"], "720h")
        # REUSES the ONE drop-lane session value (never a second literal).
        self.assertEqual(r["session_duration"], dg.DROP_ACCESS_SESSION)
        self.assertIs(dl.DROP_ACCESS_SESSION, dg.DROP_ACCESS_SESSION)

    def test_backup_route_is_complete(self):
        self.assertTrue(dl.service_route_complete(self._backup()))

    def test_route_hosts_never_collide_with_other_controller_hosts(self):
        """A duplicate hostname would be SHADOWED by the earlier ingress rule
        (cloudflared matches top-to-bottom) — never let a route alias a drop
        lane, a webterm host or claudy."""
        taken = {lane.host for lane in dg.DROP_LANES.values()}
        taken |= {s["hostname"] for s in acc.WEBTERM_ACCESS_APPS.values()}
        hosts = [r["hostname"] for r in dl.CONTROLLER_SERVICE_ROUTES]
        self.assertEqual(len(hosts), len(set(hosts)), "duplicate route host")
        self.assertFalse(set(hosts) & taken, "route host collides: %s"
                         % sorted(set(hosts) & taken))

    def test_incomplete_specs_are_not_complete(self):
        for bad in (_route(allowed_emails=[]), _route(allowed_emails=None),
                    _route(allowed_emails=[""]), _route(origin=""),
                    _route(origin="100.64.0.9:9000"), _route(name=""),
                    _route(session_duration=""), _route(hostname="")):
            self.assertFalse(dl.service_route_complete(bad), bad)
        partial = _route()
        del partial["allowed_emails"]
        self.assertFalse(dl.service_route_complete(partial))

    def test_every_declared_route_is_complete(self):
        """A typo in a future row must fail here, never silently turn PENDING
        with all_ok=True (review finding)."""
        for r in dl.CONTROLLER_SERVICE_ROUTES:
            self.assertTrue(dl.service_route_complete(r), r)

    def test_hostname_must_be_one_label_under_the_zone(self):
        """The Universal SSL wildcard covers ONE label under newlevel.media, and
        the hostname is written into the tunnel YAML, so it must be a plain
        lowercase DNS name (review finding)."""
        for bad in ("backup.example.com", "a.b.newlevel.media", "newlevel.media",
                    "Backup.newlevel.media", "bad host.newlevel.media",
                    "x.newlevel.media\n  - hostname: evil", "-x.newlevel.media"):
            self.assertFalse(dl.service_route_complete(_route(hostname=bad)), bad)
        self.assertTrue(dl.service_route_complete(
            _route(hostname="fleet-backup2.newlevel.media")))

    def test_allowed_emails_must_look_like_emails(self):
        self.assertFalse(dl.service_route_complete(
            _route(allowed_emails=["drlik.zbynek"])))
        self.assertFalse(dl.service_route_complete(
            _route(allowed_emails=[OWNER, "nobody"])))

    def test_controller_tunnel_uuid_single_value(self):
        """The CNAME targets cli_drop_gateway's UUID while the ingress lives on
        cli_webterm's tunnel. The two copies must never drift."""
        import cli_webterm
        self.assertEqual(dg._CONTROLLER_TUNNEL_UUID,
                         cli_webterm.CONTROLLER_TUNNEL_UUID)

    def test_access_spec_shape(self):
        spec = dl.service_route_access_spec(self._backup())
        self.assertEqual(spec, {"hostname": BACKUP_HOST,
                                "name": self._backup()["name"],
                                "allowed_emails": [OWNER],
                                "session_duration": "720h"})


# --------------------------------------------------------------------------- #
# controller ingress render
# --------------------------------------------------------------------------- #

class TestServiceRouteIngress(unittest.TestCase):
    def test_default_rules_contain_backup(self):
        self.assertIn((BACKUP_HOST, BACKUP_ORIGIN),
                      dl.controller_service_ingress_rules())

    def test_incomplete_route_gets_no_ingress_rule(self):
        rules = dl.controller_service_ingress_rules(
            [_route(), _route(hostname="pending.newlevel.media",
                              allowed_emails=[])])
        hosts = [r[0] for r in rules]
        self.assertIn("svc.newlevel.media", hosts)
        self.assertNotIn("pending.newlevel.media", hosts)

    def test_conflicting_duplicate_route_raises(self):
        with self.assertRaises(ValueError):
            dl.controller_service_ingress_rules(
                [_route(), _route(origin="http://100.64.0.9:9001")])

    def test_rendered_controller_config_contains_backup(self):
        """The REAL _setup_controller_webterm render path emits the route into
        controller-webterm.yml. Every side effect is stubbed: no hosted lane is
        provisioned, no port is measured, the tunnel provision is captured (no
        write, no systemctl) and the go-live reconcile is a mock (no API)."""
        import cli_webterm
        captured = {}

        def fake_provision(creds, bin_, cfg_path, config_text, *a, **k):
            captured["config"] = config_text
            return True

        with mock.patch.object(cli_webterm.profiles, "LANE_HOST", {}), \
                mock.patch.object(dl, "harvest_local_controller_filedrop_port",
                                  return_value=None), \
                mock.patch.object(tun, "_provision_managed_tunnel",
                                  side_effect=fake_provision), \
                mock.patch.object(gl, "reconcile_and_report",
                                  return_value=True) as rec:
            cli_webterm._setup_controller_webterm()
        rec.assert_called_once()
        cfg = captured["config"]
        self.assertIn("  - hostname: %s\n    service: %s\n"
                      % (BACKUP_HOST, BACKUP_ORIGIN), cfg)
        # still before the catch-all 404
        self.assertLess(cfg.index(BACKUP_HOST), cfg.index("http_status:404"))


# --------------------------------------------------------------------------- #
# go-live reconcile — Access FIRST, then CNAME; PENDING when incomplete
# --------------------------------------------------------------------------- #

class TestReconcileServiceRoutes(unittest.TestCase):
    def _run(self, routes, fail_access=False, dry_run=False, records=None):
        dns_c, acc_c, dt, at, log = _clients(fail_access=fail_access,
                                            records=records)
        out = io.StringIO()
        ok, results = gl.reconcile_service_routes(
            dry_run=dry_run, routes=routes, dns_client=dns_c,
            access_client=acc_c, tunnel_uuid=CONTROLLER_UUID, out=out,
            is_worktree_fn=_never_worktree)
        return ok, results, dt, at, log, out.getvalue()

    def test_access_created_before_cname(self):
        ok, results, dt, at, log, _ = self._run([_route()])
        self.assertTrue(ok)
        writes = [(src, m) for (src, m, _p) in log.events if m in ("POST", "PUT")]
        self.assertEqual(writes, [("access", "POST"), ("dns", "POST")],
                         "Access app must be written BEFORE the CNAME")
        self.assertEqual(results[0]["dns_action"], "created")
        self.assertTrue(results[0]["live"])

    def test_cname_targets_controller_tunnel_proxied(self):
        _ok, _r, dt, _at, _log, _ = self._run([_route()])
        body = next(b for (m, _p), b in zip(dt.calls, dt.bodies) if m == "POST")
        self.assertEqual(body["name"], "svc.newlevel.media")
        self.assertEqual(body["content"], CONTROLLER_UUID + ".cfargotunnel.com")
        self.assertTrue(body["proxied"])

    def test_backup_access_payload_owner_only_720h(self):
        _ok, _r, _dt, at, _log, _ = self._run(
            [r for r in dl.CONTROLLER_SERVICE_ROUTES if r["hostname"] == BACKUP_HOST])
        body = next(b for (m, _p), b in zip(at.calls, at.bodies) if m == "POST")
        self.assertEqual(body["domain"], BACKUP_HOST)
        self.assertEqual(body["session_duration"], "720h")
        self.assertEqual(body["policies"][0]["include"],
                         [{"email": {"email": OWNER}}])

    def test_incomplete_spec_is_pending_no_cname_no_access(self):
        ok, results, dt, at, _log, out = self._run(
            [_route(hostname="pending.newlevel.media", allowed_emails=[])])
        self.assertTrue(ok, "PENDING is not a failure")
        self.assertTrue(results[0]["pending"])
        self.assertFalse(results[0]["live"])
        self.assertEqual(results[0]["dns_action"], "skipped")
        self.assertNotIn("POST", [m for (m, _p) in dt.calls])
        self.assertNotIn("PUT", [m for (m, _p) in dt.calls])
        self.assertEqual(at.calls, [], "no Access call for a PENDING route")
        self.assertIn("PENDING", out)
        self.assertIn("pending.newlevel.media", out)

    def test_access_failure_skips_cname(self):
        ok, results, dt, _at, _log, out = self._run([_route()], fail_access=True)
        self.assertFalse(ok)
        self.assertFalse(results[0]["live"])
        self.assertNotIn("POST", [m for (m, _p) in dt.calls])
        self.assertIn("FAILED", out)

    def test_one_failing_route_does_not_stop_the_next(self):
        dns_c, acc_c, dt, at, log = _clients()
        out = io.StringIO()
        with mock.patch.object(gl, "ensure_cname_create_only",
                               side_effect=[OSError("boom"),
                                            {"ok": True, "action": "created",
                                             "record_id": "r", "error": None}]):
            ok, results = gl.reconcile_service_routes(
                dry_run=False, routes=[_route(), _route(hostname="b.newlevel.media")],
                dns_client=dns_c, access_client=acc_c, tunnel_uuid=CONTROLLER_UUID,
                out=out, is_worktree_fn=_never_worktree)
        self.assertFalse(ok)
        self.assertFalse(results[0]["live"])
        self.assertTrue(results[1]["live"])

    def test_dry_run_writes_nothing(self):
        ok, results, dt, at, _log, _ = self._run([_route()], dry_run=True)
        self.assertTrue(ok)
        self.assertEqual([m for (m, _p) in dt.calls if m != "GET"], [])
        self.assertEqual([m for (m, _p) in at.calls if m != "GET"], [])
        self.assertEqual(results[0]["dns_action"], "would_create")

    def test_live_apply_from_worktree_refused_with_zero_calls(self):
        dns_c, acc_c, dt, at, _log = _clients()
        out = io.StringIO()
        ok, results = gl.reconcile_service_routes(
            dry_run=False, routes=[_route()], dns_client=dns_c,
            access_client=acc_c, tunnel_uuid=CONTROLLER_UUID, out=out,
            is_worktree_fn=lambda: True)
        self.assertFalse(ok)
        self.assertEqual(results, [])
        self.assertEqual(dt.calls, [])
        self.assertEqual(at.calls, [])
        self.assertIn("REFUSING", out.getvalue())

    def test_token_values_never_printed(self):
        _ok, _r, _dt, _at, _log, out = self._run([_route()])
        self.assertNotIn("dns-tok", out)
        self.assertNotIn("acc-tok", out)


class TestReconcileAndReportWiring(unittest.TestCase):
    def test_controller_install_reconciles_service_routes(self):
        """The controller install entry runs the service-route reconcile next to
        the drop-lane one (both mocked — no API)."""
        fake_pw = mock.Mock(pw_name="airuleset")
        with mock.patch("watchdog.reaper.default_box_class",
                        return_value="controller"), \
                mock.patch("pwd.getpwuid", return_value=fake_pw), \
                mock.patch.object(gl, "reconcile_drop_lanes",
                                  return_value=(True, [], {})), \
                mock.patch.object(gl, "_write_local_controller_marker"), \
                mock.patch.object(gl, "reconcile_service_routes",
                                  return_value=(False, [])) as svc:
            ok = gl.reconcile_and_report(dry_run=False, out=io.StringIO())
        svc.assert_called_once()
        self.assertFalse(svc.call_args.kwargs.get("dry_run", True))
        self.assertFalse(ok, "a failed service route makes the report not-ok")


if __name__ == "__main__":
    unittest.main()
