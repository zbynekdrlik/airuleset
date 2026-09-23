"""#1115 slice B — controller-side drop-lane go-live reconcile (cli_drop_golive).

All network + ssh is FAKED (injectable DNS/Access transports, a fake fleet entry,
in-memory drop-lane dicts) — NO real Cloudflare API call, NO ssh, NO --apply. The
worktree lane makes no live change; the supervisor does the live go-live.

Covers ACCEPTANCE B:
  - a missing CNAME is created (proxied, correct cfargotunnel content);
  - an existing IDENTICAL CNAME is untouched (unchanged, no POST);
  - a CNAME pointing ELSEWHERE is a LOUD conflict, UNTOUCHED (no POST/PUT);
  - an Access app is created/updated with the right include list;
  - an idempotent re-run is a no-op (unchanged + no writes);
  - a 4xx is a LOUD summary line and the reconcile continues (all_ok False,
    other lanes still processed);
  - the go-live cache holds only LIVE lanes; the marker snippet is emitted on the
    target over the (faked) deploy session ONLY for a live lane; a token value
    NEVER appears in any output.
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_golive as gl            # noqa: E402
import cli_drop_gateway as dg           # noqa: E402
import cli_webterm_access as acc        # noqa: E402


CONTROLLER_UUID = "f85ea304-920b-4ba4-96bc-a68001ce6fb4"
ZONE = "newlevel.media"


def _lane(host, port, *, access=True, topology="controller",
          origin="100.64.0.1", uuid=CONTROLLER_UUID, filedrop_port=8790):
    return dg.DropLane(
        host=host, port=port, tunnel_uuid=uuid,
        tunnel_config=None, tunnel_service=None, tunnel_system_unit=False,
        access=access, gateway_account=None, topology=topology,
        origin_host=origin, filedrop_port=filedrop_port)


def _access_spec(host, emails):
    return {"hostname": host, "name": "drop — %s" % host,
            "allowed_emails": list(emails), "session_duration": "24h"}


class FakeDnsTransport:
    """Records calls; seeds an existing DNS record set. `zone_id`=None -> zone
    not found. Each seeded record is `{id, content, ...}`."""

    def __init__(self, zone_id="zone-1", records=None, fail_write=False):
        self.calls = []
        self.bodies = []
        self._zone_id = zone_id
        self._records = records or []
        self._fail_write = fail_write

    def __call__(self, method, path, body):
        self.calls.append((method, path))
        self.bodies.append(body)
        if method == "GET" and "/zones?" in path:
            if self._zone_id:
                return 200, {"success": True, "result": [{"id": self._zone_id}]}
            return 200, {"success": True, "result": []}
        if method == "GET" and "/dns_records?" in path:
            return 200, {"success": True, "result": list(self._records)}
        if method == "POST" and "/dns_records" in path:
            if self._fail_write:
                return 422, {"success": False,
                             "errors": [{"message": "create failed"}]}
            return 201, {"success": True, "result": {"id": "new-rec"}}
        if method == "PUT" and "/dns_records/" in path:
            return 200, {"success": True, "result": {"id": "x"}}
        return 200, {"success": True, "result": {}}

    def methods(self):
        return [m for (m, _p) in self.calls]


class FakeAccessTransport:
    """cli_webterm_access transport: GET /apps seeds `apps`; POST/PUT succeed
    unless `fail_write`."""

    def __init__(self, apps=None, fail_write=False):
        self.calls = []
        self._apps = apps or []
        self._fail_write = fail_write

    def __call__(self, method, path, body):
        self.calls.append((method, path))
        base = path.split("?", 1)[0]
        if method == "GET" and base.endswith("/apps"):
            return 200, {"success": True, "result": self._apps}
        if method == "POST" and base.endswith("/apps"):
            if self._fail_write:
                return 422, {"success": False,
                             "errors": [{"message": "create app failed"}]}
            return 201, {"success": True, "result": {"id": "new-app"}}
        if method == "PUT" and "/apps/" in base:
            return 200, {"success": True, "result": {"id": base.rsplit("/", 1)[-1]}}
        return 200, {"success": True, "result": {}}


def _dns(records=None, zone_id="zone-1", fail_write=False):
    import cli_cloudflare_dns as dns
    t = FakeDnsTransport(zone_id=zone_id, records=records, fail_write=fail_write)
    return dns.DnsClient(token="dns-tok", transport=t), t


def _acc(apps=None, fail_write=False):
    t = FakeAccessTransport(apps=apps, fail_write=fail_write)
    return acc.AccessClient("acct", token="acc-tok", transport=t), t


# --------------------------------------------------------------------------- #
# DNS create-only
# --------------------------------------------------------------------------- #

class TestCnameCreateOnly(unittest.TestCase):
    def test_missing_cname_created(self):
        client, t = _dns(records=[])
        r = gl.ensure_cname_create_only(
            client, ZONE, "drop-x.newlevel.media",
            gl._cname_content(CONTROLLER_UUID), "airuleset #1115", dry_run=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "created")
        self.assertIn("POST", t.methods())
        # proxied, correct content, no ttl on a proxied CNAME.
        post_body = next(b for (m, _p), b in zip(t.calls, t.bodies) if m == "POST")
        self.assertTrue(post_body["proxied"])
        self.assertEqual(post_body["content"],
                         CONTROLLER_UUID + ".cfargotunnel.com")
        self.assertNotIn("ttl", post_body)

    def test_existing_identical_untouched(self):
        existing = [{"id": "r1", "content": CONTROLLER_UUID + ".cfargotunnel.com",
                     "proxied": True}]
        client, t = _dns(records=existing)
        r = gl.ensure_cname_create_only(
            client, ZONE, "drop-x.newlevel.media",
            gl._cname_content(CONTROLLER_UUID), "c", dry_run=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "unchanged")
        self.assertNotIn("POST", t.methods())
        self.assertNotIn("PUT", t.methods())

    def test_cname_pointing_elsewhere_is_loud_and_untouched(self):
        existing = [{"id": "r1", "content": "someone-else.cfargotunnel.com",
                     "proxied": True}]
        client, t = _dns(records=existing)
        r = gl.ensure_cname_create_only(
            client, ZONE, "drop-x.newlevel.media",
            gl._cname_content(CONTROLLER_UUID), "c", dry_run=False)
        self.assertFalse(r["ok"])
        self.assertEqual(r["action"], "conflict")
        self.assertIn("UNTOUCHED", r["error"])
        # NEVER overwrite/delete an unknown record.
        self.assertNotIn("POST", t.methods())
        self.assertNotIn("PUT", t.methods())

    def test_dry_run_would_create_no_write(self):
        client, t = _dns(records=[])
        r = gl.ensure_cname_create_only(
            client, ZONE, "drop-x.newlevel.media",
            gl._cname_content(CONTROLLER_UUID), "c", dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "would_create")
        self.assertNotIn("POST", t.methods())

    def test_4xx_create_is_loud_not_ok(self):
        client, _t = _dns(records=[], fail_write=True)
        r = gl.ensure_cname_create_only(
            client, ZONE, "drop-x.newlevel.media",
            gl._cname_content(CONTROLLER_UUID), "c", dry_run=False)
        self.assertFalse(r["ok"])
        self.assertIn("422", r["error"])


# --------------------------------------------------------------------------- #
# Access per-lane
# --------------------------------------------------------------------------- #

class TestAccessPerLane(unittest.TestCase):
    def test_token_only_lane_no_access(self):
        lane = _lane("drop-tok.newlevel.media", 8901, access=False)
        ok, action, _msg = gl.reconcile_access_for_lane(
            lane, dry_run=False, access_client=object())
        self.assertTrue(ok)
        self.assertEqual(action, "none")

    def test_access_lane_created_with_right_include(self):
        lane = _lane("drop-a.newlevel.media", 8902, access=True)
        specs = {lane.host: _access_spec(lane.host, ["a@x.sk", "b@x.sk"])}
        client, t = _acc(apps=[])
        ok, action, msg = gl.reconcile_access_for_lane(
            lane, dry_run=False, access_client=client, access_specs=specs)
        self.assertTrue(ok)
        self.assertEqual(action, "ok")
        self.assertIn(("POST", "/accounts/acct/access/apps"), t.calls)
        # the include list is exactly the spec's e-mails
        payload = acc.build_app_payload(specs[lane.host])
        includes = payload["policies"][0]["include"]
        self.assertEqual([i["email"]["email"] for i in includes],
                         ["a@x.sk", "b@x.sk"])

    def test_access_lane_updated_when_present(self):
        lane = _lane("drop-a.newlevel.media", 8902, access=True)
        specs = {lane.host: _access_spec(lane.host, ["a@x.sk"])}
        client, t = _acc(apps=[{"id": "app1", "domain": lane.host}])
        ok, _action, _msg = gl.reconcile_access_for_lane(
            lane, dry_run=False, access_client=client, access_specs=specs)
        self.assertTrue(ok)
        self.assertIn(("PUT", "/accounts/acct/access/apps/app1"), t.calls)

    def test_access_lane_without_spec_is_fail_closed(self):
        lane = _lane("drop-nospec.newlevel.media", 8903, access=True)
        ok, action, msg = gl.reconcile_access_for_lane(
            lane, dry_run=False, access_client=object(), access_specs={})
        self.assertFalse(ok)
        self.assertEqual(action, "no-spec")
        self.assertIn("fail-closed", msg)

    def test_access_4xx_is_loud(self):
        lane = _lane("drop-a.newlevel.media", 8902, access=True)
        specs = {lane.host: _access_spec(lane.host, ["a@x.sk"])}
        client, _t = _acc(apps=[], fail_write=True)
        ok, action, msg = gl.reconcile_access_for_lane(
            lane, dry_run=False, access_client=client, access_specs=specs)
        self.assertFalse(ok)
        self.assertEqual(action, "error")
        self.assertIn("Access ERROR", msg)


# --------------------------------------------------------------------------- #
# whole reconcile + go-live cache
# --------------------------------------------------------------------------- #

class TestReconcileDropLanes(unittest.TestCase):
    def setUp(self):
        # one access lane (with spec), one token-only lane, one LOCAL lane (skip)
        self.access_lane = _lane("drop-a.newlevel.media", 8902, access=True)
        self.tok_lane = _lane("drop-b.newlevel.media", 8903, access=False)
        self.local_lane = _lane("drop-local.newlevel.media", 8904,
                                topology="local", origin=None)
        self.lanes = {
            ("boxa", "u1"): self.access_lane,
            ("boxb", "u2"): self.tok_lane,
            ("boxc", "u3"): self.local_lane,
        }
        self.specs = {self.access_lane.host:
                      _access_spec(self.access_lane.host, ["a@x.sk"])}
        self._home = tempfile.mkdtemp()
        self._orig_cache = gl.GOLIVE_CACHE
        gl.GOLIVE_CACHE = Path(self._home) / "drop-golive.json"

    def tearDown(self):
        gl.GOLIVE_CACHE = self._orig_cache

    def test_local_lane_skipped(self):
        dns_c, dns_t = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        _ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c, out=out)
        hosts = {r["host"] for r in results}
        self.assertNotIn(self.local_lane.host, hosts)   # local topology skipped
        self.assertEqual(hosts, {self.access_lane.host, self.tok_lane.host})

    def test_both_controller_lanes_go_live(self):
        dns_c, _dt = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c, out=out)
        self.assertTrue(all_ok)
        self.assertEqual(live, {self.access_lane.host: 8902,
                                self.tok_lane.host: 8903})
        # cache written with only the live lanes
        cached = json.loads(gl.GOLIVE_CACHE.read_text())
        self.assertEqual(cached, {self.access_lane.host: 8902,
                                  self.tok_lane.host: 8903})

    def test_idempotent_rerun_is_noop(self):
        existing = [{"id": "r", "content": CONTROLLER_UUID + ".cfargotunnel.com",
                     "proxied": True}]
        dns_c, dns_t = _dns(records=existing)
        acc_c, _at = _acc(apps=[{"id": "app1", "domain": self.access_lane.host}])
        out = io.StringIO()
        all_ok, results, _live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c, out=out)
        self.assertTrue(all_ok)
        self.assertNotIn("POST", dns_t.methods())        # no DNS create
        self.assertNotIn("PUT", dns_t.methods())         # never a DNS overwrite
        actions = {r["host"]: r["dns_action"] for r in results}
        self.assertEqual(actions[self.access_lane.host], "unchanged")

    def test_dns_4xx_is_loud_and_reconcile_continues(self):
        # DNS create fails for BOTH lanes; the reconcile still processes both and
        # reports LOUD, all_ok False, no lane live.
        dns_c, _dt = _dns(records=[], fail_write=True)
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c, out=out)
        self.assertFalse(all_ok)
        self.assertEqual(live, {})
        self.assertEqual(len(results), 2)                # both still processed
        text = out.getvalue()
        self.assertIn("DNS/Access FAILED", text)
        # a failed run writes an EMPTY live cache (fail-closed).
        self.assertEqual(json.loads(gl.GOLIVE_CACHE.read_text()), {})

    def test_access_lane_without_spec_is_pending_no_dns_no_failure(self):
        # no spec for the access lane -> PENDING go-live (fail-closed): not live,
        # NO DNS CNAME created (the #983 RED-1 exposure fix), and NOT a failure
        # (all_ok stays True — the missing spec is an owner go-live data step,
        # not a reconcile error, so it must not spam a FAILED line every push).
        # ONLY the no-spec access lane, so any POST would be its (forbidden) CNAME.
        dns_c, dns_t = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes={("boxa", "u1"): self.access_lane},
            access_specs={}, dns_client=dns_c, access_client=acc_c, out=out)
        self.assertTrue(all_ok)                            # pending != failure
        self.assertNotIn(self.access_lane.host, live)      # fail-closed, not live
        # CRITICAL: NO DNS record is created for the no-spec access lane — a
        # proxied CNAME with no Access app would be publicly UNPROTECTED.
        self.assertNotIn("POST", dns_t.methods())
        row = next(r for r in results if r["host"] == self.access_lane.host)
        self.assertTrue(row["pending"])
        self.assertEqual(row["dns_action"], "skipped")
        self.assertIn("PENDING go-live", out.getvalue())

    def test_access_gate_precedes_dns_when_access_fails(self):
        # an access lane WITH a spec whose Access reconcile FAILS must NOT get a
        # DNS CNAME (Access-before-DNS ordering, fail-closed) and IS a failure.
        dns_c, dns_t = _dns(records=[])
        acc_c, _at = _acc(apps=[], fail_write=True)        # Access create 4xx
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes={("boxa", "u1"): self.access_lane},
            access_specs=self.specs, dns_client=dns_c, access_client=acc_c, out=out)
        self.assertFalse(all_ok)
        self.assertEqual(live, {})
        self.assertNotIn("POST", dns_t.methods())          # DNS never created
        row = results[0]
        self.assertEqual(row["dns_action"], "skipped (access gate)")
        self.assertIn("DNS NOT created", out.getvalue())

    def test_transport_exception_on_one_lane_does_not_abort_the_rest(self):
        # a URLError-class exception from the DNS transport for the FIRST lane
        # must be a LOUD per-lane failure, not abort go-live for the others.
        import cli_cloudflare_dns as dns

        class BoomThenOk:
            def __init__(self):
                self.n = 0

            def __call__(self, method, path, body):
                # zone lookup ok; first record GET raises, rest behave.
                if method == "GET" and "/zones?" in path:
                    return 200, {"success": True, "result": [{"id": "z"}]}
                if method == "GET" and "/dns_records?" in path:
                    self.n += 1
                    if self.n == 1:
                        raise dns.urllib.error.URLError("tunnel down")
                    return 200, {"success": True, "result": []}
                if method == "POST":
                    return 201, {"success": True, "result": {"id": "r"}}
                return 200, {"success": True, "result": {}}

        dns_c = dns.DnsClient(token="t", transport=BoomThenOk())
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, live = gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c, out=out)
        self.assertFalse(all_ok)                           # the boom lane failed
        self.assertEqual(len(results), 2)                  # BOTH lanes processed
        self.assertIn("URLError", out.getvalue())

    def test_dry_run_writes_no_cache_and_no_api_writes(self):
        dns_c, dns_t = _dns(records=[])
        acc_c, acc_t = _acc(apps=[])
        out = io.StringIO()
        gl.reconcile_drop_lanes(
            dry_run=True, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c, out=out)
        self.assertNotIn("POST", dns_t.methods())
        self.assertFalse([c for c in acc_t.calls if c[0] in ("POST", "PUT")])
        self.assertFalse(gl.GOLIVE_CACHE.exists())        # no cache in dry-run

    def test_no_token_ever_in_output(self):
        dns_c, _dt = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        gl.reconcile_drop_lanes(
            dry_run=False, drop_lanes=self.lanes, access_specs=self.specs,
            dns_client=dns_c, access_client=acc_c, out=out)
        text = out.getvalue()
        self.assertNotIn("dns-tok", text)
        self.assertNotIn("acc-tok", text)


# --------------------------------------------------------------------------- #
# go-live cache read/write robustness
# --------------------------------------------------------------------------- #

class TestGoliveCache(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.mkdtemp()
        self.p = Path(self._d) / "drop-golive.json"

    def test_roundtrip(self):
        gl.write_golive_live_hosts({"h1": 8901, "h2": 8902}, path=self.p)
        self.assertEqual(gl.read_golive_live_hosts(path=self.p),
                         {"h1": 8901, "h2": 8902})

    def test_missing_is_empty(self):
        self.assertEqual(gl.read_golive_live_hosts(path=self.p), {})

    def test_malformed_is_empty(self):
        self.p.write_text("{ not json")
        self.assertEqual(gl.read_golive_live_hosts(path=self.p), {})

    def test_non_int_port_skipped(self):
        self.p.write_text(json.dumps({"h1": "x", "h2": 8902}))
        self.assertEqual(gl.read_golive_live_hosts(path=self.p), {"h2": 8902})


# --------------------------------------------------------------------------- #
# marker snippet (push-side)
# --------------------------------------------------------------------------- #

class TestMarkerSnippet(unittest.TestCase):
    def setUp(self):
        self.lane = _lane("drop-a.newlevel.media", 8902)
        self.lanes = {("boxa", "u1"): self.lane}
        # patch _nodename_for_entry so the fake entry resolves to ("boxa","u1")
        self._orig = gl._nodename_for_entry
        gl._nodename_for_entry = lambda remote: remote.get("_node", "boxa")

    def tearDown(self):
        gl._nodename_for_entry = self._orig

    def test_live_lane_emits_marker_snippet(self):
        entry = {"_node": "boxa", "user": "u1"}
        snip = gl.marker_snippet_for_entry(
            entry, {self.lane.host: 8902}, drop_lanes=self.lanes)
        self.assertIn("write_drop_marker", snip)
        self.assertIn(self.lane.host, snip)
        self.assertIn("8902", snip)
        self.assertIn("|| true", snip)          # exit-0
        self.assertNotIn("exit ", snip)         # exit-free (no `exit` statement)

    def test_non_live_lane_emits_nothing(self):
        entry = {"_node": "boxa", "user": "u1"}
        self.assertEqual(
            gl.marker_snippet_for_entry(entry, {}, drop_lanes=self.lanes), "")

    def test_no_lane_for_entry_emits_nothing(self):
        entry = {"_node": "boxUNKNOWN", "user": "zz"}
        gl._nodename_for_entry = lambda remote: "boxUNKNOWN"
        self.assertEqual(
            gl.marker_snippet_for_entry(entry, {self.lane.host: 8902},
                                        drop_lanes=self.lanes), "")


# --------------------------------------------------------------------------- #
# real registry — the controller lanes are reconciled (mechanism smoke)
# --------------------------------------------------------------------------- #

class TestRealRegistry(unittest.TestCase):
    def test_real_controller_lanes_are_reconciled(self):
        # gk + david1-4 are real controller-topology lanes with real Access
        # specs -> a dry-run attempts DNS + Access for each, none raises.
        dns_c, _dt = _dns(records=[])
        acc_c, _at = _acc(apps=[])
        out = io.StringIO()
        all_ok, results, _live = gl.reconcile_drop_lanes(
            dry_run=True, dns_client=dns_c, access_client=acc_c, out=out)
        hosts = {r["host"] for r in results}
        self.assertIn("drop-gk.newlevel.media", hosts)
        self.assertIn("drop-david.newlevel.media", hosts)
        # every reconciled lane is controller-topology
        for (n, u), lane in gl._controller_lanes(dg.DROP_LANES):
            self.assertEqual(lane.topology, "controller")
            self.assertTrue(lane.origin_host)


if __name__ == "__main__":
    unittest.main()
