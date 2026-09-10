"""Tests for cli_cloudflare_dns.py (#983).

All network is faked via injectable transport — no real Cloudflare API calls.
Covers: ensure_record (absent->POST, present-identical->no-op,
present-different->PUT, dry-run), ensure_managed_records, MANAGED_RECORDS
registry, CONTROLLER_DNS_NAMES parity, and the claudy ingress rule +
Access app entry.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_cloudflare_dns as dns  # noqa: E402


ZONE_ID = "zone-abc"


class FakeTransport:
    """Records calls and returns canned responses."""

    def __init__(self, zone_id=ZONE_ID, records=None, fail_write=False):
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
                return 200, {"success": True,
                             "result": [{"id": self._zone_id}]}
            return 200, {"success": True, "result": []}
        if method == "GET" and "/dns_records?" in path:
            return 200, {"success": True, "result": self._records}
        if method == "POST" and "/dns_records" in path:
            if self._fail_write:
                return 422, {"success": False,
                             "errors": [{"message": "create failed"}]}
            return 201, {"success": True,
                         "result": {"id": "new-rec-id"}}
        if method == "PUT" and "/dns_records/" in path:
            if self._fail_write:
                return 400, {"success": False,
                             "errors": [{"message": "update failed"}]}
            rec_id = path.rsplit("/", 1)[-1]
            return 200, {"success": True,
                         "result": {"id": rec_id}}
        return 200, {"success": True, "result": {}}

    def methods(self):
        return [m for (m, _) in self.calls]


class TestEnsureRecordAbsent(unittest.TestCase):
    """Record does not exist -> create."""

    def test_creates_record_when_absent(self):
        t = FakeTransport(records=[])
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "example.com", "test.example.com",
                              "CNAME", "target.example.com", True,
                              dry_run=False)
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual(r["action"], "created")
        self.assertEqual(r["record_id"], "new-rec-id")
        self.assertIn("POST", t.methods())

    def test_dry_run_does_not_create(self):
        t = FakeTransport(records=[])
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "example.com", "test.example.com",
                              "CNAME", "target.example.com", True,
                              dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "would_create")
        # Only GET calls (zone + records lookup), never POST.
        self.assertNotIn("POST", t.methods())


class TestEnsureRecordPresent(unittest.TestCase):
    """Record exists and matches -> no-op."""

    def test_no_op_when_identical(self):
        existing = [{"id": "rec-1", "content": "target.example.com",
                     "proxied": True}]
        t = FakeTransport(records=existing)
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "example.com", "test.example.com",
                              "CNAME", "target.example.com", True,
                              dry_run=False)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "unchanged")
        self.assertEqual(r["record_id"], "rec-1")
        # No POST or PUT — idempotent.
        self.assertNotIn("POST", t.methods())
        self.assertNotIn("PUT", t.methods())


class TestEnsureRecordDiffers(unittest.TestCase):
    """Record exists but content/proxied differs -> update (PUT)."""

    def test_updates_when_content_differs(self):
        existing = [{"id": "rec-1", "content": "old-target.example.com",
                     "proxied": True}]
        t = FakeTransport(records=existing)
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "example.com", "test.example.com",
                              "CNAME", "new-target.example.com", True,
                              dry_run=False)
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual(r["action"], "updated")
        self.assertIn("PUT", t.methods())

    def test_updates_when_proxied_differs(self):
        existing = [{"id": "rec-1", "content": "target.example.com",
                     "proxied": False}]
        t = FakeTransport(records=existing)
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "example.com", "test.example.com",
                              "CNAME", "target.example.com", True,
                              dry_run=False)
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual(r["action"], "updated")

    def test_dry_run_would_update(self):
        existing = [{"id": "rec-1", "content": "old.example.com",
                     "proxied": True}]
        t = FakeTransport(records=existing)
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "example.com", "test.example.com",
                              "CNAME", "new.example.com", True,
                              dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "would_update")
        self.assertNotIn("PUT", t.methods())


class TestEnsureRecordErrors(unittest.TestCase):
    def test_zone_not_found(self):
        t = FakeTransport(zone_id=None)
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "nope.com", "x.nope.com",
                              "A", "1.2.3.4", False, dry_run=False)
        self.assertFalse(r["ok"])
        self.assertIn("cannot find zone", r["error"])

    def test_create_failure_is_loud(self):
        t = FakeTransport(records=[], fail_write=True)
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "example.com", "test.example.com",
                              "CNAME", "target.example.com", True,
                              dry_run=False)
        self.assertFalse(r["ok"])
        self.assertIn("create record failed", r["error"])

    def test_update_failure_is_loud(self):
        existing = [{"id": "rec-1", "content": "old.example.com",
                     "proxied": True}]
        t = FakeTransport(records=existing, fail_write=True)
        client = dns.DnsClient(token="tok", transport=t)
        r = dns.ensure_record(client, "example.com", "test.example.com",
                              "CNAME", "new.example.com", True,
                              dry_run=False)
        self.assertFalse(r["ok"])
        self.assertIn("update record failed", r["error"])


class TestManagedRecords(unittest.TestCase):
    """MANAGED_RECORDS registry checks."""

    def test_claudy_cname_in_managed(self):
        names = [r["name"] for r in dns.MANAGED_RECORDS]
        self.assertIn("claudy.newlevel.media", names)
        claudy = [r for r in dns.MANAGED_RECORDS
                  if r["name"] == "claudy.newlevel.media"][0]
        self.assertEqual(claudy["type"], "CNAME")
        self.assertTrue(claudy["proxied"])
        self.assertIn("cfargotunnel.com", claudy["content"])

    def test_ar_a_record_in_managed(self):
        names = [r["name"] for r in dns.MANAGED_RECORDS]
        self.assertIn("ar.newlevel.media", names)
        ar = [r for r in dns.MANAGED_RECORDS
              if r["name"] == "ar.newlevel.media"][0]
        self.assertEqual(ar["type"], "A")
        self.assertEqual(ar["content"], "100.101.214.103")
        self.assertFalse(ar["proxied"])

    def test_ensure_managed_records_token_missing(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "no-token")
            ok, results = dns.ensure_managed_records(
                dry_run=True, token_path=path)
            self.assertFalse(ok)
            self.assertTrue(results)
            self.assertIn("cannot read DNS token", results[0]["error"])


class TestControllerDnsNames(unittest.TestCase):
    """CONTROLLER_DNS_NAMES in cli_disk_guard_root.py includes claudy."""

    def test_claudy_in_controller_dns_names(self):
        from cli_disk_guard_root import CONTROLLER_DNS_NAMES
        self.assertIn("claudy.newlevel.media", CONTROLLER_DNS_NAMES)


class TestClaudyIngressRule(unittest.TestCase):
    """The claudy ingress rule appears in the rendered config."""

    def test_claudy_in_rendered_config(self):
        import cli_webterm_tunnel as tun
        rules = [("claudy.newlevel.media", "http://100.101.214.103:8791")]
        config = tun.render_cloudflared_multi_ingress_config(
            "test-uuid", "/creds.json", rules)
        self.assertIn("claudy.newlevel.media", config)
        self.assertIn("http://100.101.214.103:8791", config)

    def test_claudy_ingress_is_idempotent(self):
        """Rendering twice with the same rule produces identical output."""
        import cli_webterm_tunnel as tun
        rules = [("claudy.newlevel.media", "http://100.101.214.103:8791")]
        c1 = tun.render_cloudflared_multi_ingress_config(
            "test-uuid", "/creds.json", rules)
        c2 = tun.render_cloudflared_multi_ingress_config(
            "test-uuid", "/creds.json", rules)
        self.assertEqual(c1, c2)


class TestClaudyAccessApp(unittest.TestCase):
    """The claudy Access app entry exists with exactly the two emails."""

    def test_claudy_app_exists(self):
        import cli_webterm_access as acc
        self.assertIn("claudy", acc.WEBTERM_ACCESS_APPS)

    def test_claudy_app_has_two_emails(self):
        import cli_webterm_access as acc
        claudy = acc.WEBTERM_ACCESS_APPS["claudy"]
        self.assertEqual(claudy["hostname"], "claudy.newlevel.media")
        emails = claudy["allowed_emails"]
        self.assertEqual(len(emails), 2)
        self.assertIn("drlik.zbynek@gmail.com", emails)
        self.assertIn("drlik.marek@gmail.com", emails)

    def test_claudy_app_session_duration(self):
        import cli_webterm_access as acc
        claudy = acc.WEBTERM_ACCESS_APPS["claudy"]
        self.assertEqual(claudy["session_duration"], "720h")

    def test_claudy_app_payload_is_self_hosted(self):
        import cli_webterm_access as acc
        claudy = acc.WEBTERM_ACCESS_APPS["claudy"]
        p = acc.build_app_payload(claudy)
        self.assertEqual(p["type"], "self_hosted")
        self.assertEqual(p["domain"], "claudy.newlevel.media")
        self.assertEqual(len(p["policies"]), 1)
        includes = p["policies"][0]["include"]
        self.assertEqual(len(includes), 2)


class TestTokenNotPrinted(unittest.TestCase):
    """The DNS token value is never printed or logged."""

    def test_token_path_never_has_value_in_source(self):
        src = Path(__file__).resolve().parent.parent / "cli_cloudflare_dns.py"
        text = src.read_text()
        # The token is read from a file and handed to the Authorization header.
        # It must never appear in a print/sys.stdout/sys.stderr call.
        import re
        for m in re.finditer(r'print\(.*token.*\)', text, re.IGNORECASE):
            self.fail("Token value might be printed: %s" % m.group())


if __name__ == "__main__":
    unittest.main()
