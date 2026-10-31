"""#1270: an account may declare `extra_repos`: repositories its ONE token also
covers (iemmixer + its private ops repo, dstack + dantesync). The scope
check stays exact: a token wider or narrower than the declaration is refused."""
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402
from test_project_gh_token_1190 import REPO, FakeGitHub, TestReviewVerify, _Base, verify_out  # noqa: E402

TOKEN = "ghs" + "_" + "a" * 36
EXPIRES = "2026-10-30T18:00:00Z"


class _Resp:
    def __init__(self, status, data):
        self.status, self._b = status, json.dumps(data).encode()

    def read(self):
        return self._b

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(names, sent):
    def fake(req, timeout=None):
        sent.append(json.loads(req.data))
        return _Resp(201, {"token": TOKEN, "expires_at": EXPIRES, "permissions": {"contents": "write"},
                           "repositories": [{"full_name": "o/" + n} for n in names]})
    return fake


class TestExtraRepos(unittest.TestCase):

    def test_token_names_every_declared_repo(self):
        sent = []
        out = pgt.mint_token(1, "o/a", "jwt", extra=["o/b"], urlopen=_urlopen(["a", "b"], sent))
        self.assertEqual(out["token"], TOKEN)
        self.assertEqual(sent[0]["repositories"], ["a", "b"])

    def test_a_narrower_token_is_refused(self):
        with self.assertRaises(pgt.MintError):
            pgt.mint_token(1, "o/a", "jwt", extra=["o/b"], urlopen=_urlopen(["a"], []))

    def test_without_extras_the_scope_stays_one_repo(self):
        with self.assertRaises(pgt.MintError):
            pgt.mint_token(1, "o/a", "jwt", urlopen=_urlopen(["a", "b"], []))

    def test_the_declarations(self):
        self.assertEqual(bootstrap.account_spec("iemmixer")["extra_repos"],
                         ["zbynekdrlik/iemmixer-ops"])
        self.assertEqual(bootstrap.account_spec("dstack")["extra_repos"],
                         ["zbynekdrlik/dantesync"])

    def test_a_bad_extra_repo_is_refused(self):
        spec = dict(bootstrap.account_spec("iemmixer"), extra_repos=["no-slash"])
        self.assertTrue(bootstrap.validate_account("iemmixer", spec))


EXTRA = "zbynekdrlik/fohmixer-ops"


class _ExtraGitHub(FakeGitHub):
    """The App misses the extra repo unless `covered`."""

    def __init__(self, covered=True, **kw):
        super().__init__(scoped_to=[REPO, EXTRA] if covered else [REPO], **kw)
        self.covered = covered

    def __call__(self, req, timeout=None):
        url = req.full_url
        if url.endswith("/repos/%s/installation" % EXTRA):
            self.requests.append({"method": "GET", "url": url, "auth": None, "body": None})
            if not self.covered:
                raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO(b"{}"))
            return super().__call__(type(req)(url.replace(EXTRA, REPO), headers=req.headers))
        body = json.loads(req.data.decode()) if req.data else {}
        if not self.covered and "fohmixer-ops" in (body.get("repositories") or []):
            raise urllib.error.HTTPError(url, 422, "bad", {}, io.BytesIO(
                b'{"message":"not accessible to the parent installation"}'))
        return super().__call__(req, timeout)


class _WithExtra(_Base):

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(bootstrap.SERVICE_ACCOUNTS["fohmixer"], {"extra_repos": [EXTRA]})
        p.start()
        self.addCleanup(p.stop)


class TestMintAccountExtras(_WithExtra):

    def test_the_declared_extra_reaches_the_mint(self):
        rc, out, err, gh, _ssh = self.mint(gh=_ExtraGitHub())
        self.assertEqual(rc, 0, err)
        bodies = [r["body"] for r in gh.requests if r["body"]]
        self.assertIn(["fohmixer", "fohmixer-ops"], [b["repositories"] for b in bodies])
        self.assertIn("fohmixer-ops", pgt.status_line("fohmixer", directory=self.state))

    def test_a_missing_extra_never_kills_the_primary_token(self):
        rc, out, err, gh, ssh = self.mint(gh=_ExtraGitHub(covered=False))
        self.assertEqual(rc, 0, err)
        self.assertTrue(ssh.calls)                         # the primary was delivered
        self.assertIn(["fohmixer"], [r["body"]["repositories"] for r in gh.requests if r["body"]])
        text = out + err
        self.assertIn("SKIPPED", text)
        self.assertIn(EXTRA, text)
        self.assertIn("OWNER ACTION", text)
        self.assertIn("SKIPPED", pgt.status_line("fohmixer", directory=self.state))

    def test_the_dry_run_names_the_scope_and_checks_the_extra(self):
        rc, out, err, gh, _ssh = self.mint(gh=_ExtraGitHub(), dry_run=True)
        self.assertEqual(rc, 0, err)
        self.assertIn(EXTRA, out)
        self.assertNotIn("that repo only", out)
        self.assertTrue(any(EXTRA in r["url"] for r in gh.requests))

    def test_the_dry_run_fails_on_an_uncovered_extra(self):
        rc, _out, err, _gh, _ssh = self.mint(gh=_ExtraGitHub(covered=False), dry_run=True)
        self.assertEqual(rc, 1)
        self.assertIn(EXTRA, err)


class TestVerifyExtras(unittest.TestCase):
    run_verify = TestReviewVerify.run_verify

    def setUp(self):
        p = mock.patch.dict(bootstrap.SERVICE_ACCOUNTS["fohmixer"], {"extra_repos": [EXTRA]})
        p.start()
        self.addCleanup(p.stop)

    def test_the_full_set_passes_and_is_named(self):
        got, _calls, text = self.run_verify(verify_out(repos=(REPO, EXTRA)))
        self.assertEqual(got, 0, text)
        self.assertIn(EXTRA, text)

    def test_a_token_without_the_extra_fails_and_says_what_it_wants(self):
        got, _calls, text = self.run_verify(verify_out())
        self.assertEqual(got, 1)
        self.assertIn(EXTRA, text)


class TestExtraPolicy(unittest.TestCase):

    def refused(self, *extras):
        spec = dict(bootstrap.account_spec("iemmixer"), extra_repos=list(extras))
        return bootstrap.validate_account("iemmixer", spec)

    def test_a_widening_extra_is_refused(self):
        for bad in ("zbynekdrlik/airuleset", "zbynekdrlik/iemmixer", "zbynekdrlik/fohmixer",
                    "someone/else", "zbynekdrlik/\u011b\u0161\u010d"):
            self.assertTrue(self.refused(bad), bad)

    def test_a_duplicate_is_refused(self):
        self.assertTrue(self.refused("zbynekdrlik/x", "zbynekdrlik/X"))

    def test_it_must_be_a_list(self):
        spec = dict(bootstrap.account_spec("iemmixer"), extra_repos="zbynekdrlik/x")
        self.assertTrue(bootstrap.validate_account("iemmixer", spec))

    def test_mint_refuses_airuleset_as_an_extra(self):
        with self.assertRaises(pgt.MintError):
            pgt.mint_token(1, "o/a", "jwt", extra=[pgt.AIRULESET_REPO],
                           urlopen=_urlopen(["a", "airuleset"], []))


if __name__ == "__main__":
    unittest.main()
