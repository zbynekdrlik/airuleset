"""#1036 — read-only Odoo JSON-2 client + per-box config contract.

The FIRST Odoo read client in airuleset. Locks the client's read-only method
allowlist, the JSON-2 request shape (`POST {base}/json/2/{model}/{method}`,
`Authorization: Bearer <key>`), structured errors, the key never leaking into
repr/logs, and the `~/.claude/odoo-task-tracking.json` config contract. Every
test injects a FAKE transport — NEVER a real network call to any client
instance (hard rule of this lane).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_odoo_ro as ro  # noqa: E402


class FakeTransport:
    """Records every request and returns a canned (status, body) per call.

    `responses` is a list consumed in order; each entry is either a dict/list
    (JSON-encoded, HTTP 200) or an (status, bytes) tuple for an explicit shape.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": dict(headers),
                           "timeout": timeout})
        item = self._responses.pop(0)
        if isinstance(item, tuple):
            return item
        return 200, json.dumps(item).encode("utf-8")


class TestClientRequestShape(unittest.TestCase):
    def _client(self, responses):
        self.transport = FakeTransport(responses)
        return ro.OdooReadOnlyClient(
            "https://erp.example.cloud/", "SECRET-KEY-XYZ",
            transport=self.transport)

    def test_search_read_builds_json2_url_and_bearer(self):
        c = self._client([[{"id": 1, "name": "t"}]])
        rows = c.search_read("project.task", [["id", "=", 1]],
                             fields=["id", "name"], limit=5, order="id desc")
        self.assertEqual(rows, [{"id": 1, "name": "t"}])
        call = self.transport.calls[0]
        self.assertEqual(call["url"],
                         "https://erp.example.cloud/json/2/project.task/search_read")
        self.assertEqual(call["headers"].get("Authorization"), "Bearer SECRET-KEY-XYZ")
        body = json.loads(call["data"].decode("utf-8"))
        self.assertEqual(body["domain"], [["id", "=", 1]])
        self.assertEqual(body["fields"], ["id", "name"])
        self.assertEqual(body["limit"], 5)
        self.assertEqual(body["order"], "id desc")

    def test_search_count_returns_int(self):
        c = self._client([7])
        n = c.search_count("project.task", [["stage_id", "=", 2880]])
        self.assertEqual(n, 7)
        self.assertTrue(self.transport.calls[0]["url"].endswith("/search_count"))

    def test_read_by_ids(self):
        c = self._client([[{"id": 3, "name": "x"}]])
        rows = c.read("mail.message", [3], fields=["id", "name"])
        self.assertEqual(rows, [{"id": 3, "name": "x"}])
        body = json.loads(self.transport.calls[0]["data"].decode("utf-8"))
        self.assertEqual(body["ids"], [3])

    def test_write_method_is_refused(self):
        c = self._client([True])
        # a mutating method must never be dispatchable through this client
        with self.assertRaises(ro.OdooError):
            c._call("project.task", "write", ids=[1], vals={"name": "x"})
        # nothing was sent
        self.assertEqual(self.transport.calls, [])

    def test_guarded_reaction_method_is_allowed(self):
        # message_reactions_guarded is a READ-ONLY guarded server method (#784):
        # it must dispatch (the raw mail.message.reaction model 403s by design).
        c = self._client([[{"content": "👷", "partner_id": [17244, "Z"]}]])
        out = c.call("mail.message", "message_reactions_guarded", ids=[5])
        self.assertEqual(out, [{"content": "👷", "partner_id": [17244, "Z"]}])
        self.assertTrue(self.transport.calls[0]["url"]
                        .endswith("/mail.message/message_reactions_guarded"))

    def test_http_error_raises_structured(self):
        c = self._client([(500, b"boom")])
        with self.assertRaises(ro.OdooError):
            c.search_read("project.task", [])

    def test_bad_json_raises_structured(self):
        c = self._client([(200, b"not json")])
        with self.assertRaises(ro.OdooError):
            c.search_read("project.task", [])


class TestKeyNeverLeaks(unittest.TestCase):
    def test_repr_and_str_hide_the_key(self):
        c = ro.OdooReadOnlyClient("https://erp.example.cloud", "TOP-SECRET-123",
                                  transport=FakeTransport([]))
        self.assertNotIn("TOP-SECRET-123", repr(c))
        self.assertNotIn("TOP-SECRET-123", str(c))

    def test_error_message_does_not_carry_the_key(self):
        c = ro.OdooReadOnlyClient("https://erp.example.cloud", "TOP-SECRET-123",
                                  transport=FakeTransport([(500, b"err")]))
        try:
            c.search_read("project.task", [])
        except ro.OdooError as e:
            self.assertNotIn("TOP-SECRET-123", str(e))
        else:
            self.fail("expected OdooError")


class TestConfigContract(unittest.TestCase):
    def _write(self, text):
        d = tempfile.mkdtemp(prefix="i1036-cfg-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        p = os.path.join(d, "odoo-task-tracking.json")
        with open(p, "w", encoding="utf-8") as h:
            h.write(text)
        return p

    def test_load_absent_returns_none(self):
        self.assertIsNone(ro.load_config("/no/such/path/x.json"))

    def test_load_corrupt_returns_none(self):
        p = self._write("{ this is not json")
        self.assertIsNone(ro.load_config(p))

    def test_hash_commented_template_parses(self):
        # the template is written with #-line comments and must still parse
        tmpl = ro.config_template()
        self.assertIn("#", tmpl)
        p = self._write(tmpl)
        cfg = ro.load_config(p)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["instance_url"], "https://erp.montalu.cloud")
        self.assertEqual(cfg["project_ids"], [1])
        self.assertEqual(cfg["stage_ids"]["verifikacia"], 2880)
        self.assertEqual(cfg["stage_ids"]["realizacia"], 2879)
        self.assertEqual(cfg["stage_ids"]["potrebuje_ujasnit"], 3350)
        self.assertEqual(cfg["stage_ids"]["hotovo"], 2881)
        self.assertIn(17244, cfg["stream_partner_ids"])

    def test_template_is_valid_config(self):
        p = self._write(ro.config_template())
        cfg = ro.load_config(p)
        ok, missing = ro.config_valid(cfg)
        self.assertTrue(ok, "template must be a valid config, missing: %r" % missing)

    def test_config_valid_reports_missing(self):
        ok, missing = ro.config_valid({"instance_url": "https://x"})
        self.assertFalse(ok)
        self.assertIn("project_ids", missing)
        self.assertIn("stage_ids", missing)

    def test_client_confirm_days_default(self):
        p = self._write(ro.config_template())
        cfg = ro.load_config(p)
        self.assertEqual(ro.client_confirm_days(cfg), 3)
        self.assertEqual(ro.client_confirm_days({"client_confirm_days": 5}), 5)

    def test_urls_with_double_slash_survive_hash_stripping(self):
        # a #-line-comment stripper must not corrupt an https:// value
        p = self._write('# comment line\n{"instance_url": "https://a.b/x"}\n')
        cfg = ro.load_config(p)
        self.assertEqual(cfg["instance_url"], "https://a.b/x")


class TestReadApiKey(unittest.TestCase):
    def _env(self, text):
        d = tempfile.mkdtemp(prefix="i1036-env-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        p = os.path.join(d, "odoo.env")
        with open(p, "w", encoding="utf-8") as h:
            h.write(text)
        return p

    def test_reads_named_var(self):
        p = self._env("# comment\nODOO_API_KEY=abc123\nOTHER=zzz\n")
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), "abc123")

    def test_export_prefix_and_quotes_tolerated(self):
        p = self._env('export ODOO_API_KEY="q u o t e d"\n')
        self.assertEqual(ro.read_api_key(p, "ODOO_API_KEY"), "q u o t e d")

    def test_missing_var_raises(self):
        p = self._env("SOMETHING=else\n")
        with self.assertRaises(ro.OdooError):
            ro.read_api_key(p, "ODOO_API_KEY")

    def test_missing_file_raises(self):
        with self.assertRaises(ro.OdooError):
            ro.read_api_key("/no/such/env/file.env", "ODOO_API_KEY")


if __name__ == "__main__":
    unittest.main()
