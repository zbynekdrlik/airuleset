"""#1399 (owner 28.11.2026: "hnijuci m23" on david3) — the PROD version is read,
and the main version is the canonical one.

Live: every david3 M ticket was `on_main: pending` because (1) the odoo-erp
`deploy_state` version sources were `TODO-PROVISIONING` (prod_version None),
(2) `read_main_version` read the fork's stale `origin/main` (2.327.0, canonical
2.666.0). The PROD read is `/json/2/ir.module.module/search_read` with the
stream's own handover bearer key (odoo-erp scripts/prod_facts.py); an instance
whose key is not on this box is not judged here.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli_ticket_facts as f                 # noqa: E402
import cli_ticket_state as ts                # noqa: E402
from watchdog import deploy_state as ds      # noqa: E402


class _Resp:
    def __init__(self, body, status=200):
        self.status, self._b = status, body.encode()

    def read(self, n=-1):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _key(tmp, value="k-1"):
    p = Path(tmp) / "slovnormal-handover-api-key.env"
    p.write_text(value + "\n")
    return str(p)


class Json2Source(unittest.TestCase):

    def test_reads_company_base_latest_version_with_the_bearer_key(self):
        seen = []

        def opener(req, timeout=None):
            seen.append(req)
            return _Resp(json.dumps([{"id": 54, "latest_version": "19.0.2.666.0"}]))
        with tempfile.TemporaryDirectory() as tmp:
            vs = {"type": "odoo_json2", "url": "https://erp.example.sk",
                  "key_file": _key(tmp)}
            self.assertEqual(ds.read_prod_version(vs, opener=opener), "2.666.0")
        req = seen[0]
        self.assertEqual(req.full_url,
                         "https://erp.example.sk/json/2/ir.module.module/search_read")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "bearer k-1")
        body = json.loads(req.data)
        self.assertEqual(body["domain"], [["name", "=", "company_base"]])
        self.assertEqual(body["fields"], ["latest_version"])

    def test_missing_key_is_not_configured_and_sends_nothing(self):
        def opener(req, timeout=None):
            raise AssertionError("no request without a key")
        vs = {"type": "odoo_json2", "url": "https://x",
              "key_file": "/nonexistent/k.env"}
        self.assertFalse(ds.source_configured(vs))
        self.assertIsNone(ds.read_prod_version(vs, opener=opener))

    def test_a_failed_or_empty_read_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            vs = {"type": "odoo_json2", "url": "https://x", "key_file": _key(tmp)}
            self.assertTrue(ds.source_configured(vs))
            for body in ("[]", "not json", json.dumps([{"latest_version": False}])):
                with self.subTest(body=body):
                    self.assertIsNone(ds.read_prod_version(
                        vs, opener=lambda r, timeout=None, b=body: _Resp(b)))

            def boom(req, timeout=None):
                raise OSError("401")
            self.assertIsNone(ds.read_prod_version(vs, opener=boom))

    def test_key_file_tilde_expands(self):
        vs = {"type": "odoo_json2", "url": "https://x",
              "key_file": "~/does-not-exist-1399.env"}
        self.assertEqual(ds._key_path(vs),
                         os.path.expanduser("~/does-not-exist-1399.env"))

    def test_registry_declares_json2_for_every_odoo_erp_instance(self):
        reg = json.loads((ROOT / "projects-registry.json").read_text())
        rows = reg if isinstance(reg, list) else reg["projects"]
        row = next(r for r in rows if r.get("name") == "odoo-erp")
        insts = row["deploy_state"]["instances"]
        self.assertEqual(sorted(i["name"] for i in insts),
                         ["miva", "montalu", "slovnormal"])
        for i in insts:
            vs = i["version_source"]
            self.assertEqual(vs["type"], "odoo_json2")
            self.assertTrue(vs["url"].startswith("https://erp."))
            self.assertEqual(vs["key_file"],
                             "~/.secrets/%s-handover-api-key.env" % i["name"])


class FetchMarksUnconfigured(unittest.TestCase):

    def test_unconfigured_instance_row_and_main_ref_passed(self):
        decl = {"main_version_file": "v.py", "instances": [
            {"name": "a", "version_source": {"type": "odoo_json2", "url": "https://a",
                                             "key_file": "/nonexistent/a.env"}}]}
        refs = []
        orig_decl, orig_main = ds.deploy_declaration, ds.read_main_version
        ds.deploy_declaration = lambda cwd, registry_path=None: (decl, True)
        ds.read_main_version = lambda cwd, vf, ref="origin/main": refs.append(ref) or "2.0.0"
        try:
            rows = ds.fetch_deploy_state("/x", main_ref="upstream/main")
        finally:
            ds.deploy_declaration, ds.read_main_version = orig_decl, orig_main
        self.assertEqual(refs, ["upstream/main"])
        self.assertEqual(rows[0]["configured"], False)
        self.assertIsNone(rows[0]["prod_version"])


class OnMainSkipsUnconfigured(unittest.TestCase):

    def _row(self, prod, configured=True):
        return {"main_version": "2.666.0", "prod_version": prod,
                "configured": configured}

    def test_only_the_configured_instance_is_judged(self):
        got = f.on_main_states({1: "a"}, [self._row("2.666.0"),
                                          self._row(None, False),
                                          self._row(None, False)], lambda o: None)
        self.assertEqual(got, {1: ts.DEPLOYED})

    def test_a_configured_unreadable_instance_keeps_pending(self):
        got = f.on_main_states({1: "a"}, [self._row("2.666.0"), self._row(None)],
                               lambda o: None)
        self.assertEqual(got, {1: ts.PENDING})

    def test_nothing_configured_is_pending(self):
        got = f.on_main_states({1: "a"}, [self._row(None, False)], lambda o: None)
        self.assertEqual(got, {1: ts.PENDING})


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x"))


class CanonicalMainVersion(unittest.TestCase):

    def test_fork_reads_upstream_main_not_the_stale_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init", "-q", "-b", "main")
            (repo / "v.py").write_text("{'version': '19.0.2.327.0'}\n")
            _git(repo, "add", "v.py")
            _git(repo, "commit", "-qm", "old")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            (repo / "v.py").write_text("{'version': '19.0.2.666.0'}\n")
            _git(repo, "commit", "-qam", "new")
            _git(repo, "update-ref", "refs/remotes/upstream/main", "HEAD")
            self.assertEqual(ds.read_main_version(repo, "v.py"), "2.327.0")
            self.assertEqual(ds.read_main_version(repo, "v.py", ref="upstream/main"),
                             "2.666.0")

    def test_default_deploy_passes_the_canonical_main_ref(self):
        import cli_release_backfill as bf
        seen = {}
        orig = (ds.deploy_declaration, ds.fetch_deploy_state, bf.canonical_main_ref)
        ds.deploy_declaration = lambda root, slug=None: (
            {"main_version_file": "v.py", "instances": [{"name": "a"}]}, True)

        def fetch(root, main_ref="origin/main"):
            seen["ref"] = main_ref
            return [{"instance": "a", "main_version": "1", "prod_version": "1"}]
        ds.fetch_deploy_state = fetch
        bf.canonical_main_ref = lambda root, slug: "upstream/main"
        try:
            out = f._default_deploy("/r", "o/r")
        finally:
            ds.deploy_declaration, ds.fetch_deploy_state, bf.canonical_main_ref = orig
        self.assertEqual(seen["ref"], "upstream/main")
        self.assertEqual(out["main_ref"], "upstream/main")

    def test_fork_without_canonical_refs_reads_no_main_version(self):
        import cli_release_backfill as bf
        orig = (ds.deploy_declaration, bf.canonical_main_ref)
        ds.deploy_declaration = lambda root, slug=None: (
            {"main_version_file": "v.py", "instances": [{"name": "a"}]}, True)
        bf.canonical_main_ref = lambda root, slug: None
        try:
            out = f._default_deploy("/r", "o/r")
        finally:
            ds.deploy_declaration, bf.canonical_main_ref = orig
        self.assertEqual(out["instances"], [])



class ReviewRound1(unittest.TestCase):

    def test_a_redirect_never_carries_the_key_to_another_host(self):
        import http.server
        import threading
        hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append((self.server.server_port, self.headers.get("Authorization")))
                if self.server.server_port == first.server_port:
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:%d/x" % second.server_port)
                    self.end_headers()
                    return
                body = json.dumps([{"latest_version": "19.0.9.9.9"}]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST

            def log_message(self, *a):
                pass
        first = http.server.HTTPServer(("127.0.0.1", 0), H)
        second = http.server.HTTPServer(("127.0.0.1", 0), H)
        for srv in (first, second):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                vs = {"type": "odoo_json2", "key_file": _key(tmp, "secret-k"),
                      "url": "http://127.0.0.1:%d" % first.server_port}
                self.assertIsNone(ds.read_prod_version(vs, timeout=5))
        finally:
            first.shutdown()
            second.shutdown()
        self.assertEqual([port for port, _a in hits], [first.server_port])

    def test_no_slug_reads_no_main_version(self):
        orig = ds.deploy_declaration
        ds.deploy_declaration = lambda root, slug=None: (
            {"main_version_file": "v.py", "instances": [{"name": "a"}]}, True)
        try:
            out = f._default_deploy("/r", None)
        finally:
            ds.deploy_declaration = orig
        self.assertEqual(out["instances"], [])


class PrReadWithoutChecks(unittest.TestCase):
    """Live david3 28.11.: the stream App token gets FORBIDDEN on
    `statusCheckRollup`, gh exits 1, so the whole PR read was None and no
    ticket could ever reach C. The PR list itself is readable."""

    def _payload(self, with_checks):
        head = {"committedDate": "2026-11-28T00:00:00Z"}
        if with_checks:
            head["statusCheckRollup"] = {"state": "PENDING"}
        return json.dumps({"data": {"repository": {"pullRequests": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"number": 7, "title": "fix #41", "body": "", "isDraft": False,
                       "closingIssuesReferences": {"nodes": [{"number": 42}]},
                       "commits": {"nodes": [{"commit": head}]}}]}},
            "rateLimit": {"cost": 1, "remaining": 4000}}})

    def test_forbidden_checks_fall_back_to_the_pr_list(self):
        calls = []

        def gh(args):
            q = next(a for a in args if a.startswith("query="))
            calls.append("statusCheckRollup" in q)
            return "" if "statusCheckRollup" in q else self._payload(False)
        got = f.read_prs("o/r", gh, now=0)
        self.assertEqual(got, {41: False, 42: False})
        self.assertEqual(calls, [True, False])

    def test_a_readable_full_query_is_not_retried(self):
        calls = []

        def gh(args):
            calls.append(1)
            return self._payload(True)
        self.assertEqual(f.read_prs("o/r", gh, now=1795800000), {41: True, 42: True})
        self.assertEqual(len(calls), 1)

    def test_both_failing_is_unknown(self):
        self.assertIsNone(f.read_prs("o/r", lambda a: "", now=0))

if __name__ == "__main__":
    unittest.main()
