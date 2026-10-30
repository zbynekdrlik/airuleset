"""#1270 (owner 30.10.2026: "vytvorenie ticketu pre druhy projekt uz nie je
mozne? ... to sa mi nie uplne zda"): a project account files issues on the repos
of its linked peers. An issues-only token per peer repo, routed by gh-app-shim;
contents stay on the account's own repo."""
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_peer_channel as cpc  # noqa: E402
import cli_project_gh_token as tok  # noqa: E402
import peer_channel  # noqa: E402

LINKS = {"hosts": {"dev1": {"links": [["a", "b"], ["a", "c"], ["a", "notes"], ["x", "y"]]}}}
REGISTRY = [
    {"name": "a", "host": "dev1", "account": "a", "path": "/home/a/devel/a"},
    {"name": "b", "host": "dev1", "account": "b", "path": "/home/b/devel/b"},
    {"name": "c", "host": "dev1", "account": "newlevel", "path": "~/devel/c"},
    {"name": "notes", "host": "dev1", "account": "newlevel", "path": "~/devel/notes",
     "overrides": ["no-git"]},
    {"name": "x", "host": "dev1", "account": "x", "path": "/home/x/devel/x"},
    {"name": "y", "host": "dev1", "account": "y", "path": "/home/y/devel/y"},
]


class TestPeerRepos(unittest.TestCase):

    def test_linked_repos_only_and_never_a_project_with_no_repo(self):
        self.assertEqual(cpc.peer_repos("a", LINKS, REGISTRY),
                         ["zbynekdrlik/b", "zbynekdrlik/c"])

    def test_an_account_with_no_link_gets_none(self):
        self.assertEqual(cpc.peer_repos("nobody", LINKS, REGISTRY), [])

    def test_the_live_declaration_gives_dante_stack_songplayer(self):
        self.assertIn("zbynekdrlik/songplayer", cpc.peer_repos("dante-stack"))


class TestMintPeers(unittest.TestCase):

    def _mint(self, fail=()):
        calls = []

        def fake_mint(installation, repo, jwt, *, permissions=None, extra=(), urlopen=None):
            calls.append((repo, permissions))
            if repo in fail:
                raise tok.MintError("nope", status=422)
            return {"token": "t", "expires_at": "2030-01-01T00:00:00Z"}

        delivered = []

        def fake_deliver(remote, repo, token, expires, *, run=None, primary=True):
            delivered.append((repo, primary))

        remote = {"name": "a@dev1", "user": "a", "host": "h"}
        with mock.patch.object(tok, "mint_token", fake_mint), \
                mock.patch.object(tok, "deliver", fake_deliver):
            out = [tok.mint_issues_token("a", "zbynekdrlik/a", 1, "jwt", remote, 0, target=r)
                   for r in ("zbynekdrlik/b", "zbynekdrlik/c")]
        return calls, delivered, out

    def test_each_peer_repo_gets_an_issues_only_token(self):
        calls, delivered, out = self._mint()
        self.assertEqual([c[1] for c in calls], [tok.AIRULESET_PERMISSIONS] * 2)
        self.assertEqual(delivered, [("zbynekdrlik/b", False), ("zbynekdrlik/c", False)])
        self.assertEqual([rc for rc, _ in out], [0, 0])

    def test_a_peer_outside_the_app_skips_loud_and_keeps_the_others(self):
        calls, delivered, out = self._mint(fail=("zbynekdrlik/c",))
        self.assertEqual(delivered, [("zbynekdrlik/b", False)])
        self.assertEqual([rc for rc, _ in out], [0, 0])
        self.assertTrue(out[1][1]["skipped"])


class TestShimRouting(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)
        self.tokens = Path(self.home, ".config/gh-app-tokens")
        self.tokens.mkdir(parents=True)
        for name in ("primary", "zbynekdrlik__airuleset", "zbynekdrlik__songplayer"):
            (self.tokens / name).write_text("tok-%s\n" % name)
        self.bin = Path(self.home, "bin")
        self.bin.mkdir()
        shutil.copy("/usr/bin/env", self.bin / "gh")    # a real binary, runs the subcommand
        for sub in ("issue", "api"):
            s = self.bin / sub
            s.write_text('#!/bin/bash\necho "seen $GH_TOKEN"\n')
            s.chmod(s.stat().st_mode | stat.S_IEXEC)
        self.shim = Path(self.home, "shim")
        self.shim.write_text(tok.render_gh_app_shim())

    def _seen(self, *argv):
        path = "%s:/usr/bin:/bin" % self.bin
        r = subprocess.run(["bash", str(self.shim), *argv], capture_output=True, text=True,
                           env={"HOME": self.home, "PATH": path})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_a_linked_repo_uses_its_issues_token(self):
        want = "seen tok-zbynekdrlik__songplayer"
        self.assertEqual(self._seen("issue", "comment", "285", "-R", "zbynekdrlik/songplayer"), want)
        self.assertEqual(self._seen("issue", "view", "--repo=zbynekdrlik/songplayer"), want)
        self.assertEqual(
            self._seen("issue", "view", "https://github.com/zbynekdrlik/songplayer/issues/285"), want)
        self.assertEqual(self._seen("api", "repos/zbynekdrlik/songplayer/issues/285"), want)

    def test_a_repo_with_no_token_stays_on_primary(self):
        self.assertEqual(self._seen("issue", "list", "-R", "zbynekdrlik/own"), "seen tok-primary")

    def test_airuleset_still_routes(self):
        self.assertEqual(self._seen("issue", "list", "-R", "zbynekdrlik/airuleset"),
                         "seen tok-zbynekdrlik__airuleset")

    def test_a_crafted_name_never_leaves_the_token_dir(self):
        Path(self.home, ".config/secret").write_text("nope\n")
        self.assertEqual(self._seen("issue", "list", "-R", "zbynekdrlik/../../secret"),
                         "seen tok-primary")

    def test_body_text_never_routes(self):
        self.assertEqual(self._seen("issue", "create", "--body",
                                    "https://github.com/zbynekdrlik/songplayer/issues/1"),
                         "seen tok-primary")


class TestPeersTool(unittest.TestCase):

    def test_a_session_checks_peers_before_saying_not_linked(self):
        tool = next(t for t in peer_channel.TOOLS if t["name"] == "peers")
        self.assertIn("before saying a project is not linked", tool["description"])


if __name__ == "__main__":
    unittest.main()
