"""#1370 — venue-bypass sync + publish-app, with injected fake Cloudflare clients.

Owner 19.11.2026: the venue-LAN bypass is an opt-in PER APP that the owner
confirms; the IPs come from presenter's auto-synced bypass (presenter#977);
a published app = Access FIRST, then tunnel ingress, then a proxied CNAME.
"""
import copy
import io
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli_cloudflare_dns as dns            # noqa: E402
import cli_publish_app as pa                 # noqa: E402
import cli_tunnel_remote_config as trc       # noqa: E402
import cli_venue_bypass as vb                # noqa: E402
import cli_venue_bypass_apps as table        # noqa: E402
import cli_webterm_access as wa              # noqa: E402

NAME = table.BYPASS_POLICY_NAME
SNV, PP = "85.248.11.235/32", "95.102.87.189/32"
TUN = "e2332e11-6408-4568-a1ed-a097492a1356"


def _src(host, ips, name="presenter on-site network (no login)"):
    return {"id": "src-" + host, "domain": host, "policies": [
        {"id": "b-" + host, "name": name, "decision": "bypass", "precedence": 1,
         "include": [{"ip": {"ip": ip}} for ip in ips]},
        {"id": "a-" + host, "name": "presenter allowed users", "decision": "allow",
         "precedence": 2, "include": [{"email": {"email": "o@x"}}]}]}


def _target(host, ips=None, app_id=None):
    pols = [{"id": "allow-" + host, "name": "allowed", "decision": "allow",
             "precedence": 2, "include": [{"email": {"email": "o@x"}}]}]
    if ips is not None:
        pols.insert(0, {"id": "byp-" + host, "name": NAME, "decision": "bypass",
                        "precedence": 1, "include": [{"ip": {"ip": i}} for i in ips]})
    return {"id": app_id or "app-" + host, "domain": host, "policies": pols}


class FakeCF:
    """One fake for Access, Tunnel and DNS routes; records every write."""

    def __init__(self, apps, tunnel_remote=True, ingress=None, records=None,
                 list_status=200):
        self.apps = copy.deepcopy(apps)
        self.writes = []
        self.order = []
        self.tunnel_remote = tunnel_remote
        self.config = {"ingress": ingress if ingress is not None else
                       [{"hostname": "other.newlevel.media", "service": "http://a"},
                        {"service": "http_status:404"}],
                       "warp-routing": {"enabled": False}}
        self.records = records or {}
        self.list_status = list_status

    def __call__(self, method, path, body):
        if method != "GET":
            self.writes.append((method, path, copy.deepcopy(body)))
        if "/access/apps" in path:
            self.order.append("access")
            if method == "GET":
                if self.list_status != 200:
                    return self.list_status, {"success": False,
                                              "errors": [{"message": "boom"}]}
                return 200, {"success": True, "result": copy.deepcopy(self.apps),
                             "result_info": {"total_count": len(self.apps)}}
            if path.endswith("/access/apps") and method == "POST":
                app = dict(body, id="new-app")
                self.apps.append(app)
                return 200, {"success": True, "result": app}
            return 200, {"success": True, "result": {}}
        if "/cfd_tunnel/" in path:
            self.order.append("ingress")
            if path.endswith("/configurations"):
                if method == "GET":
                    return 200, {"success": True, "result": {"config": self.config,
                                                             "version": 3}}
                return 200, {"success": True, "result": {"version": 4}}
            return 200, {"success": True, "result": {"remote_config": self.tunnel_remote}}
        if path.startswith("/zones"):
            self.order.append("dns")
            if path.startswith("/zones?name="):
                return 200, {"success": True, "result": [{"id": "z1"}]}
            if method == "GET":
                rtype = path.rsplit("type=", 1)[1]
                rec = self.records.get(rtype)
                return 200, {"success": True, "result": [rec] if rec else []}
            return 200, {"success": True, "result": {"id": "r1"}}
        raise AssertionError("unexpected route %s %s" % (method, path))


def _access(fake):
    return wa.AccessClient("acc", token="t", transport=fake)


def _clients(fake):
    return (_access(fake), trc.TunnelConfigClient("acc", token="t", transport=fake),
            dns.DnsClient(token="t", transport=fake))


SOURCES = [_src("prsnv.newlevel.media", [SNV]), _src("prpp.newlevel.media", [PP]),
           {"id": "h", "domain": "prsnv.newlevel.media/healthz", "policies": [
               {"name": "healthz", "decision": "bypass", "include": [{"everyone": {}}]}]}]


class TestTable(unittest.TestCase):

    def test_table_is_the_two_shading_apps_each_with_a_confirmation(self):
        self.assertEqual(set(table.VENUE_BYPASS_APPS),
                         {"shading.newlevel.media", "shadingpp.newlevel.media"})
        for why in table.VENUE_BYPASS_APPS.values():
            self.assertIn("owner", why)
            self.assertIn("1370", why)

    def test_default_allow_list_is_the_claudy_pair(self):
        self.assertEqual(list(table.PUBLISH_ALLOWED_EMAILS),
                         wa.WEBTERM_ACCESS_APPS["claudy"]["allowed_emails"])

    def test_data_leaf_imports_nothing(self):
        src = (ROOT / "cli_venue_bypass_apps.py").read_text()
        self.assertNotIn("\nimport ", src)
        self.assertNotIn("\nfrom ", src)


class TestSync(unittest.TestCase):

    def _run(self, apps, dry_run=False, **kw):
        fake = FakeCF(apps, **kw)
        out = io.StringIO()
        rc = vb.sync(_access(fake), dry_run=dry_run, out=out)
        return rc, fake, out.getvalue()

    def test_stale_target_is_updated_to_the_source_union_and_allow_untouched(self):
        rc, fake, log = self._run(SOURCES + [
            _target("shading.newlevel.media", ["1.2.3.4/32"]),
            _target("shadingpp.newlevel.media", [SNV, PP])])
        self.assertEqual(rc, 0, log)
        self.assertEqual(len(fake.writes), 1)
        method, path, body = fake.writes[0]
        self.assertEqual(method, "PUT")
        self.assertTrue(path.endswith("/apps/app-shading.newlevel.media/policies/"
                                      "byp-shading.newlevel.media"))
        self.assertEqual(body["decision"], "bypass")
        self.assertEqual(body["name"], NAME)
        self.assertEqual([i["ip"]["ip"] for i in body["include"]], [SNV, PP])
        self.assertIn("1.2.3.4/32 -> %s, %s" % (SNV, PP), log)
        self.assertIn("unchanged", log)
        self.assertFalse(any("allow-" in p for _m, p, _b in fake.writes))

    def test_missing_bypass_is_created_on_a_free_precedence(self):
        rc, fake, log = self._run(SOURCES + [
            _target("shading.newlevel.media"),
            _target("shadingpp.newlevel.media", [SNV, PP])])
        self.assertEqual(rc, 0, log)
        (method, path, body), = fake.writes
        self.assertEqual(method, "POST")
        self.assertTrue(path.endswith("/apps/app-shading.newlevel.media/policies"))
        self.assertEqual(body["precedence"], 1)

    def test_dry_run_writes_nothing(self):
        rc, fake, log = self._run(SOURCES + [
            _target("shading.newlevel.media", ["1.2.3.4/32"]),
            _target("shadingpp.newlevel.media")], dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(fake.writes, [])
        self.assertIn("[would]", log)

    def test_source_without_bypass_fails_loud_and_writes_nothing(self):
        no_bypass = _src("prpp.newlevel.media", [PP])
        no_bypass["policies"] = no_bypass["policies"][1:]
        rc, fake, log = self._run([SOURCES[0], no_bypass,
                                   _target("shading.newlevel.media", ["1.2.3.4/32"]),
                                   _target("shadingpp.newlevel.media", [SNV])])
        self.assertEqual(rc, 1)
        self.assertEqual(fake.writes, [])
        self.assertIn("no bypass", log)
        self.assertIn("NOTHING written", log)

    def test_missing_source_app_fails_loud(self):
        rc, fake, log = self._run([SOURCES[0], _target("shading.newlevel.media", [SNV])])
        self.assertEqual(rc, 1)
        self.assertEqual(fake.writes, [])
        self.assertIn("prpp.newlevel.media not found", log)

    def test_unreadable_api_fails_loud_and_never_removes(self):
        rc, fake, log = self._run(SOURCES, list_status=403)
        self.assertEqual(rc, 1)
        self.assertEqual(fake.writes, [])
        self.assertIn("HTTP 403", log)

    def test_source_bypass_for_everyone_is_refused(self):
        bad = _src("prpp.newlevel.media", [PP])
        bad["policies"][0]["include"] = [{"everyone": {}}]
        rc, fake, log = self._run([SOURCES[0], bad,
                                   _target("shading.newlevel.media", [SNV])])
        self.assertEqual(rc, 1)
        self.assertEqual(fake.writes, [])

    def test_non_table_app_with_the_managed_name_is_reported_never_touched(self):
        rc, fake, log = self._run(SOURCES + [
            _target("shading.newlevel.media", [SNV, PP]),
            _target("shadingpp.newlevel.media", [SNV, PP]),
            _target("secret-app.newlevel.media", ["9.9.9.9/32"])])
        self.assertEqual(rc, 1)
        self.assertEqual(fake.writes, [])
        self.assertIn("[REPORT] secret-app.newlevel.media", log)
        self.assertIn("NOT in VENUE_BYPASS_APPS", log)

    def test_table_app_without_access_app_is_an_error(self):
        rc, fake, log = self._run(SOURCES + [_target("shading.newlevel.media", [SNV, PP])])
        self.assertEqual(rc, 1)
        self.assertIn("shadingpp.newlevel.media: no Access app", log)

    def test_paged_listing_is_refused(self):
        fake = FakeCF(SOURCES)
        orig = fake.__call__

        def paged(method, path, body):
            st, d = orig(method, path, body)
            if method == "GET" and "/access/apps" in path:
                d["result_info"] = {"total_count": 500}
            return st, d
        rc = vb.sync(_access(paged), dry_run=False, out=io.StringIO())
        self.assertEqual(rc, 1)
        self.assertEqual(fake.writes, [])


class TestCli(unittest.TestCase):

    def test_apply_from_a_worktree_is_refused_before_any_client(self):
        args = types.SimpleNamespace(apply=True, _is_worktree_fn=lambda: True)
        with mock.patch.object(vb, "load_client",
                               side_effect=AssertionError("token read")), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(vb.cmd_venue_bypass_sync(args), 1)

    def test_timer_only_on_the_controller_airuleset_account(self):
        calls = []

        def systemctl(argv):
            calls.append(argv)
            return 0, "", ""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(vb.setup_timer(box_class_fn=lambda: "dev", user="airuleset",
                                             systemctl=systemctl, unit_dir=d))
            self.assertIsNone(vb.setup_timer(box_class_fn=lambda: "controller",
                                             user="newlevel", systemctl=systemctl,
                                             unit_dir=d))
            self.assertEqual(calls, [])
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertTrue(vb.setup_timer(box_class_fn=lambda: "controller",
                                               user="airuleset", systemctl=systemctl,
                                               unit_dir=d))
            svc = (Path(d) / vb.SERVICE_UNIT).read_text()
            tmr = (Path(d) / vb.TIMER_UNIT).read_text()
        self.assertIn("venue-bypass-sync --apply", svc)
        self.assertIn("OnCalendar=*:20", tmr)
        self.assertIn(["enable", "--now", vb.TIMER_UNIT], calls)


class TestPublishApp(unittest.TestCase):

    def _publish(self, host, fake, **kw):
        a, t, d = _clients(fake)
        out = io.StringIO()
        rc = pa.publish(host, TUN, "https://127.0.0.1:443", access_client=a,
                        tunnel_client=t, dns_client=d, out=out, **kw)
        return rc, out.getvalue()

    def test_order_is_access_then_ingress_then_dns(self):
        fake = FakeCF(SOURCES)
        rc, log = self._publish("newapp.newlevel.media", fake, dry_run=False,
                                origin_server_name="newapp.newlevel.media")
        self.assertEqual(rc, 0, log)
        kinds = [p.split("/")[3] if "/accounts/" in p else "zones"
                 for _m, p, _b in fake.writes]
        self.assertEqual(kinds, ["access", "cfd_tunnel", "zones"])
        _m, _p, app = fake.writes[0]
        self.assertEqual(app["session_duration"], "730h")
        self.assertEqual([i["email"]["email"] for i in app["policies"][0]["include"]],
                         list(table.PUBLISH_ALLOWED_EMAILS))
        self.assertFalse(any(p["decision"] == "bypass" for p in app["policies"]))
        _m, _p, cfg = fake.writes[1]
        ing = cfg["config"]["ingress"]
        self.assertEqual(ing[-1], {"service": "http_status:404"})
        self.assertEqual(ing[-2], {"hostname": "newapp.newlevel.media",
                                   "service": "https://127.0.0.1:443",
                                   "originRequest": {"originServerName":
                                                     "newapp.newlevel.media"}})
        self.assertEqual(ing[0]["hostname"], "other.newlevel.media")
        self.assertEqual(cfg["config"]["warp-routing"], {"enabled": False})
        _m, _p, rec = fake.writes[2]
        self.assertEqual(rec["content"], TUN + ".cfargotunnel.com")
        self.assertTrue(rec["proxied"])

    def test_dry_run_writes_nothing(self):
        fake = FakeCF(SOURCES)
        rc, log = self._publish("newapp.newlevel.media", fake, dry_run=True)
        self.assertEqual(rc, 0, log)
        self.assertEqual(fake.writes, [])
        self.assertEqual(log.count("[would]"), 3)

    def test_venue_bypass_refused_without_table_membership(self):
        fake = FakeCF(SOURCES)
        rc, log = self._publish("newapp.newlevel.media", fake, dry_run=False,
                                venue_bypass=True)
        self.assertEqual(rc, 2)
        self.assertEqual(fake.writes, [])
        self.assertEqual(fake.order, [])
        self.assertIn("OWNER confirms", log)

    def test_cli_refuses_bypass_before_worktree_or_token(self):
        args = types.SimpleNamespace(host="newapp.newlevel.media", tunnel=TUN,
                                     origin="https://127.0.0.1:443", venue_bypass=True,
                                     apply=True, _is_worktree_fn=lambda: False)
        with mock.patch.object(pa, "_clients", side_effect=AssertionError("token")), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(pa.cmd_publish_app(args), 2)
        self.assertIn("VENUE_BYPASS_APPS", out.getvalue())

    def test_table_host_gets_the_bypass_from_the_source(self):
        fake = FakeCF(SOURCES + [_target("shading.newlevel.media")])
        rc, log = self._publish("shading.newlevel.media", fake, dry_run=False,
                                venue_bypass=True)
        self.assertEqual(rc, 0, log)
        posted = [b for m, p, b in fake.writes if p.endswith("/policies")]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["precedence"], 1)
        self.assertEqual([i["ip"]["ip"] for i in posted[0]["include"]], [SNV, PP])

    def test_new_app_with_bypass_puts_allow_second_and_bypass_first(self):
        fake = FakeCF(SOURCES)
        rc, log = self._publish("shading.newlevel.media", fake, dry_run=False,
                                venue_bypass=True)
        self.assertEqual(rc, 0, log)
        app = fake.writes[0][2]
        self.assertEqual(app["policies"][0]["precedence"], 2)
        posted = [b for m, p, b in fake.writes if p.endswith("/policies")]
        self.assertEqual([b["precedence"] for b in posted], [1])

    def test_locally_managed_tunnel_is_refused_and_dns_never_runs(self):
        fake = FakeCF(SOURCES, tunnel_remote=False)
        rc, log = self._publish("newapp.newlevel.media", fake, dry_run=False)
        self.assertEqual(rc, 1)
        self.assertNotIn("dns", fake.order)
        self.assertFalse(any("cfd_tunnel" in p for _m, p, _b in fake.writes))
        self.assertIn("LOCALLY managed", log)

    def test_existing_a_record_is_a_conflict(self):
        fake = FakeCF(SOURCES, records={"A": {"id": "a", "content": "10.77.9.202"}})
        rc, log = self._publish("newapp.newlevel.media", fake, dry_run=False)
        self.assertEqual(rc, 1)
        self.assertFalse(any(p.startswith("/zones") and m == "POST"
                             for m, p, _b in fake.writes))
        self.assertIn("10.77.9.202", log)

    def test_second_run_is_idempotent(self):
        host = "newapp.newlevel.media"
        fake = FakeCF(SOURCES + [_target(host)],
                      ingress=[{"hostname": host, "service": "https://127.0.0.1:443"},
                               {"service": "http_status:404"}],
                      records={"CNAME": {"id": "c", "content": TUN + ".cfargotunnel.com"}})
        rc, log = self._publish(host, fake, dry_run=False)
        self.assertEqual(rc, 0, log)
        self.assertEqual(fake.writes, [])

    def test_host_routed_elsewhere_is_refused(self):
        host = "newapp.newlevel.media"
        fake = FakeCF(SOURCES, ingress=[{"hostname": host, "service": "http://b"},
                                        {"service": "http_status:404"}])
        rc, log = self._publish(host, fake, dry_run=False)
        self.assertEqual(rc, 1)
        self.assertFalse(any("cfd_tunnel" in p for _m, p, _b in fake.writes))

    def test_validation(self):
        self.assertIn("ONE flat label", pa.validate("a.b.newlevel.media", TUN, "http://x"))
        self.assertIn("zone", pa.validate("a.example.com", TUN, "http://x"))
        self.assertIn("UUID", pa.validate("a.newlevel.media", "abc", "http://x"))
        self.assertIsNone(pa.validate("a.newlevel.media", TUN, "https://127.0.0.1:443"))


class TestGlobalRule(unittest.TestCase):
    """The rule reaches every session: one always-on pointer line + the full
    doctrine injected on the publish action (and a prompt asking for it)."""

    COMPANION = ROOT / "skills" / "cloudflare-api-tokens" / "published-apps.md"

    def _hook(self, payload):
        import json
        import subprocess
        import tempfile
        sys.path.insert(0, str(ROOT / "tests"))
        from _hook_state_cleanup import hermetic_hook_env
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = hermetic_hook_env(self, TMPDIR=tmp.name)
        r = subprocess.run(["bash", str(ROOT / "hooks" / "inject-situational-rule.sh")],
                           input=json.dumps(payload), capture_output=True, text=True,
                           env=env)
        out = r.stdout.strip()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def test_pointer_line_in_the_always_on_module(self):
        t = (ROOT / "modules" / "core" / "deliver-files-as-urls.md").read_text()
        line = [ln for ln in t.splitlines() if "publish-app" in ln]
        self.assertEqual(len(line), 1)
        for tok in ("tunnel", "Cloudflare Access", "OWNER confirms",
                    "never a router change", "ask airuleset, not the owner"):
            self.assertIn(tok, line[0])

    def test_companion_states_the_rule(self):
        body = " ".join(self.COMPANION.read_text().split())
        for phrase in ("Never a router change", "opt-in PER APP", "only the OWNER confirms",
                       "Default = Access only", "VENUE_BYPASS_APPS", "never asks the owner how",
                       "gh issue create -R zbynekdrlik/airuleset", "Access app FIRST"):
            self.assertIn(phrase, body)

    def test_publish_command_injects_the_companion(self):
        ctx = self._hook({"session_id": "s1370a", "tool_name": "Bash", "tool_input": {
            "command": "python3 airuleset.py publish-app x.newlevel.media --tunnel u "
                       "--origin https://127.0.0.1:443"}})
        self.assertIn("opt-in PER APP", ctx)

    def test_cofits_with_the_cloudflare_token_body(self):
        ctx = self._hook({"session_id": "s1370b", "tool_name": "Bash", "tool_input": {
            "command": "curl -s https://api.cloudflare.com/client/v4/zones; "
                       "python3 airuleset.py publish-app x.newlevel.media"}})
        self.assertIn("opt-in PER APP", ctx)
        self.assertIn("published-apps.md", ctx)
        self.assertIn("cloudflare-api-tokens/SKILL.md", ctx)

    def test_prompt_asking_for_access_injects(self):
        ctx = self._hook({"session_id": "s1370c", "hook_event_name": "UserPromptSubmit",
                          "prompt": "daj appku za Cloudflare Access a bez hesla v zbore"})
        self.assertIn("opt-in PER APP", ctx)

    def test_unrelated_command_does_not_inject(self):
        ctx = self._hook({"session_id": "s1370d", "tool_name": "Bash",
                          "tool_input": {"command": "python3 -m pytest tests/test_publish.py"}})
        self.assertNotIn("opt-in PER APP", ctx)


if __name__ == "__main__":
    unittest.main()
