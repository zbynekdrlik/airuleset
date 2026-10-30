"""#1270: an account may declare `extra_repos`: repositories its ONE token also
covers (iemmixer + its private ops repo, dante-stack + dantesync). The scope
check stays exact: a token wider or narrower than the declaration is refused."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402

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
        self.assertEqual(bootstrap.account_spec("dante-stack")["extra_repos"],
                         ["zbynekdrlik/dantesync"])

    def test_a_bad_extra_repo_is_refused(self):
        spec = dict(bootstrap.account_spec("iemmixer"), extra_repos=["no-slash"])
        self.assertTrue(bootstrap.validate_account("iemmixer", spec))


if __name__ == "__main__":
    unittest.main()
