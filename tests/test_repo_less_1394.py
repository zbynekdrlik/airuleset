"""#1394 round 2: a REPO-LESS project account is declared explicitly
(`repo_less: True`), never inferred from a missing `repo`, and it still files
airuleset tickets natively (#1199, owner rule).

bazos (the owner's private sales data, never on GitHub) declares
`repo_less: True` + `github_app: True` and no `repo`: the controller minter
mints ONLY the airuleset issues-only token (plus the peer-repo issues tokens
of #1270), never a repo token and never the `primary` link;
`project-gh-token verify` proves "airuleset issues: write proven" and that the
account holds no repo token; `accounts verify` reads secret-sync as n/a only
for `repo_less: True`. claudy (a real repo, declares neither key) keeps FAILING
gh-token and secret-sync. The fakes are #1190's (test_project_gh_token_1190)."""
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_verify as verify  # noqa: E402
import cli_fleet  # noqa: E402
import cli_project_ci_sync as ci_sync  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402
import test_project_gh_token_1190 as base  # noqa: E402

ACCT = "bazos"
AIR_FILE = "zbynekdrlik__airuleset"
PEER = "zbynekdrlik/wall-songs"
PEER_TOKEN = base.GHS + "Fake1394PeerToken" * 3


def _with_pending(flag):
    return mock.patch.object(cli_fleet, "REMOTE_HOSTS", [
        dict(h, pending=flag) if h.get("user") == ACCT else h
        for h in cli_fleet.REMOTE_HOSTS])


def _live():
    """REMOTE_HOSTS with bazos past its root bootstrap (pending False; the
    real state since its flip on 23.11., kept explicit for these tests)."""
    return _with_pending(False)


def _pending():
    """REMOTE_HOSTS with bazos marked pending again (the state before its
    root bootstrap), so the skip paths keep their coverage."""
    return _with_pending(True)


class FakeGitHub(base.FakeGitHub):
    """#1190's fake, answering the installation lookup on airuleset (a
    repo-less account has no repo of its own to look it up on) and an
    issues-only mint for a peer repo."""

    def __call__(self, req, timeout=None):
        url = req.full_url
        if url == "https://api.github.com/repos/%s/installation" % pgt.AIRULESET_REPO:
            self.requests.append({"method": req.get_method(), "url": url,
                                  "auth": None, "body": None})
            return base._Resp(200, {"id": base.INSTALLATION, "app_slug": pgt.APP_SLUG})
        body = json.loads(req.data.decode()) if req.data else None
        if (body or {}).get("repositories") == [PEER.split("/")[1]]:
            self.requests.append({"method": req.get_method(), "url": url,
                                  "auth": None, "body": body})
            return base._Resp(201, {"token": PEER_TOKEN, "expires_at": base.AIR_EXPIRES,
                                    "permissions": base.AIR_PERMS,
                                    "repositories": [{"name": "wall-songs",
                                                      "full_name": PEER}]})
        return super().__call__(req, timeout)


class TestDeclaration(unittest.TestCase):

    def test_bazos_is_the_only_repo_less_account(self):
        self.assertEqual({a for a, raw in bootstrap.SERVICE_ACCOUNTS.items()
                          if raw.get("repo_less")}, {ACCT})

    def test_bazos_declares_repo_less_with_its_airuleset_token(self):
        raw = bootstrap.SERVICE_ACCOUNTS[ACCT]
        self.assertIs(raw.get("repo_less"), True)
        self.assertIs(raw.get("github_app"), True)
        for key in ("repo", "extra_repos", "repo_secrets", "code_only"):
            self.assertNotIn(key, raw)
        self.assertEqual(bootstrap.validate_account(ACCT, raw), [])
        self.assertIn(ACCT, pgt.github_app_accounts())

    def _errs(self, **changes):
        raw = dict(bootstrap.SERVICE_ACCOUNTS[ACCT], repo_less=True, github_app=True)
        raw.update(changes)
        for k in [k for k, v in raw.items() if v is None]:
            del raw[k]
        return " | ".join(bootstrap.validate_account(ACCT, raw))

    def test_repo_less_excludes_every_repo_key(self):
        for key, value in (("repo", "zbynekdrlik/bazos"), ("extra_repos", ["zbynekdrlik/x"]),
                           ("repo_secrets", []), ("code_only", True)):
            self.assertIn("repo_less: True excludes %s" % key,
                          self._errs(**{key: value}), key)

    def test_repo_less_needs_its_airuleset_token(self):
        self.assertIn("repo_less: True needs github_app: True", self._errs(github_app=None))

    def test_repo_less_is_true_or_absent(self):
        self.assertIn("repo_less must be True", self._errs(repo_less=False))

    def test_github_app_without_repo_still_needs_repo_less(self):
        errs = self._errs(repo_less=None)
        self.assertIn("github_app: True needs the repo", errs)


class TestClaudyStaysFailing(unittest.TestCase):
    """claudy has a real repo (zbynekdrlik/claudy) but declares neither repo
    nor github_app: a missing key must never turn its gate into a pass."""

    def test_gh_token_and_secret_sync_still_fail(self):
        raw = bootstrap.SERVICE_ACCOUNTS["claudy"]
        self.assertNotIn("repo_less", raw)
        rows = {n: (ok, why) for n, ok, why in verify.evaluate(
            "claudy", bootstrap.account_spec("claudy"), {}, gh_rc=None,
            claudy_read=lambda name: "")}
        self.assertEqual(rows["gh-token"][0], False)
        self.assertIn("no github_app declared", rows["gh-token"][1])
        self.assertEqual(rows["secret-sync"][0], False)


class TestAccountsVerify(unittest.TestCase):

    def _rows(self, gh_rc):
        return {n: (ok, why) for n, ok, why in verify.evaluate(
            ACCT, bootstrap.account_spec(ACCT), {}, gh_rc=gh_rc,
            claudy_read=lambda name: "")}

    def test_gh_token_is_the_real_verify(self):
        self.assertEqual(self._rows(0)["gh-token"], (True, "project-gh-token verify OK"))
        self.assertIs(self._rows(1)["gh-token"][0], False)

    def test_secret_sync_is_na_for_repo_less(self):
        ok, why = self._rows(0)["secret-sync"]
        self.assertTrue(ok)
        self.assertIn("n/a (repo_less", why)


class TestMint(base._Base):

    def tokdir(self):
        return self.home / ".config" / "gh-app-tokens"

    def mint_bazos(self, gh=None, **kw):
        with _live():
            return self.mint(ACCT, gh=gh or FakeGitHub(air="ok"), **kw)

    def test_only_the_airuleset_issues_token_is_minted(self):
        rc, out, err, gh, ssh = self.mint_bazos()
        self.assertEqual(rc, 0, err)
        posts = [r for r in gh.requests if r["method"] == "POST"]
        self.assertEqual([p["body"] for p in posts], [
            {"repositories": ["airuleset"], "permissions": base.AIR_PERMS}])
        self.assertEqual((self.tokdir() / AIR_FILE).read_text().strip(), base.AIR_TOKEN)
        self.assertFalse(os.path.lexists(self.tokdir() / "primary"))
        self.assertEqual(len(ssh.calls), 1)
        self.assertEqual(ssh.calls[0]["input"].strip(), base.AIR_TOKEN)
        self.assertNotIn(base.AIR_TOKEN, out + err)
        st = pgt.read_state(ACCT, self.state)
        self.assertIs(st["ok"], True)
        self.assertIs(st["repo_less"], True)
        self.assertEqual(st["scope"], [])
        self.assertIs(st["airuleset"]["ok"], True)
        self.assertNotIn(base.AIR_TOKEN, json.dumps(st))
        self.assertIn("airuleset issues: OK", pgt.airuleset_status_line(
            ACCT, now=base.NOW, directory=self.state))

    def test_peer_repos_get_their_issues_tokens(self):
        with mock.patch("cli_peer_channel.peer_repos", return_value=[PEER]):
            rc, out, err, gh, ssh = self.mint_bazos()
        self.assertEqual(rc, 0, err)
        posts = [r["body"]["repositories"] for r in gh.requests if r["method"] == "POST"]
        self.assertEqual(posts, [["airuleset"], ["wall-songs"]])
        self.assertEqual((self.tokdir() / "zbynekdrlik__wall-songs").read_text().strip(),
                         PEER_TOKEN)

    def test_no_airuleset_token_is_a_failure_for_a_repo_less_account(self):
        # It is the account's ONLY token: a skip leaves it without any credential.
        rc, out, err, gh, ssh = self.mint_bazos(gh=FakeGitHub(air=422))
        self.assertEqual(rc, 1)
        self.assertIn("SKIP", err)
        self.assertIs((pgt.read_state(ACCT, self.state) or {}).get("ok"), False)

    def test_dry_run_mints_nothing(self):
        rc, out, err, gh, ssh = self.mint_bazos(dry_run=True)
        self.assertEqual(rc, 0, err)
        self.assertIn(AIR_FILE, out)
        self.assertEqual([r for r in gh.requests if r["method"] == "POST"], [])
        self.assertEqual(ssh.calls, [])

    def test_the_pending_account_is_still_never_contacted(self):
        # mint <acct> refuses a pending account loudly (as for every account)
        with _pending():
            rc, out, err, gh, ssh = self.mint(ACCT, gh=FakeGitHub(air="ok"))
        self.assertEqual(rc, 1)
        self.assertIn("pending", err)
        self.assertEqual(ssh.calls, [])

    def test_the_installation_lookup_failure_is_recorded(self):
        class Down(FakeGitHub):
            def __call__(self, req, timeout=None):
                raise urllib.error.HTTPError(req.full_url, 500, "err", {}, io.BytesIO(b"{}"))
        rc, out, err, gh, ssh = self.mint_bazos(gh=Down())
        self.assertEqual(rc, 1)
        self.assertIs((pgt.read_state(ACCT, self.state) or {}).get("ok"), False)


NOT_LOGGED_IN = ("You are not logged into any GitHub hosts. To log in, run: "
                 "gh auth login\n")


def _probe(primary_rc=1, primary=(), issues=422, pulls=403,
           gh="/home/bazos/.local/bin/gh", primary_file_rc=1, auth=NOT_LOGGED_IN,
           auth_rc=1):
    """The repo-less verify probe's stdout: #1199's sections, plus (round 3)
    whether ~/.config/gh-app-tokens/primary exists and `gh auth status`."""
    return ("%s\n@@primary\n%s@@rc %d\n@@issues\ngh: probe (HTTP %s)\n@@rc 1\n"
            "@@pulls\ngh: probe (HTTP %s)\n@@rc 1\n@@primaryfile\n@@rc %d\n"
            "@@auth\n%s@@rc %d\n"
            % (gh, "".join(r + "\n" for r in primary), primary_rc, issues, pulls,
               primary_file_rc, auth, auth_rc))


class TestVerify(unittest.TestCase):

    def run_verify(self, stdout, rc=0):
        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, rc, stdout, "")
        out, err = io.StringIO(), io.StringIO()
        with _live(), redirect_stdout(out), redirect_stderr(err):
            got = pgt.verify_account(ACCT, run=run)
        return got, out.getvalue() + err.getvalue()

    def test_ok_proves_the_airuleset_write_and_no_repo_token(self):
        got, text = self.run_verify(_probe())
        self.assertEqual(got, 0, text)
        self.assertIn("airuleset issues: write proven", text)
        self.assertIn("no repo token", text)

    def test_a_repo_token_on_a_repo_less_account_fails(self):
        got, text = self.run_verify(_probe(primary_rc=0, primary=("zbynekdrlik/x",)))
        self.assertEqual(got, 1, text)

    def test_no_airuleset_write_fails(self):
        for code in (401, 403, 404):
            self.assertEqual(self.run_verify(_probe(issues=code))[0], 1, code)

    def test_gh_off_the_chain_or_a_failed_probe_fails(self):
        self.assertEqual(self.run_verify(_probe(gh="/usr/bin/gh"))[0], 1)
        self.assertEqual(self.run_verify(_probe(), rc=255)[0], 1)

    # round 3: "no repo token" is proven positively, never read from a failure
    def test_a_primary_token_file_fails(self):
        got, text = self.run_verify(_probe(primary_file_rc=0))
        self.assertEqual(got, 1, text)
        self.assertIn("primary", text)

    def test_a_stored_gh_login_fails(self):
        logged = "github.com\n  ✓ Logged in to github.com account someone (keyring)\n"
        got, text = self.run_verify(_probe(auth=logged, auth_rc=0))
        self.assertEqual(got, 1, text)
        self.assertIn("gh auth status", text)

    def test_missing_proof_sections_fail(self):
        # the #1199 probe alone (no primaryfile / auth sections) proves nothing
        stdout = _probe().split("@@primaryfile")[0]
        self.assertEqual(self.run_verify(stdout)[0], 1)
        got = self.run_verify(_probe(auth="", auth_rc=1))[0]   # rc 1 but no message
        self.assertEqual(got, 1)

    def test_the_probe_checks_the_file_and_the_stored_login(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, _probe(), "")
        with _live(), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            pgt.verify_account(ACCT, run=run)
        probe = calls[0][-1]
        self.assertIn(".config/gh-app-tokens/primary", probe)
        self.assertIn("gh auth status", probe)
        self.assertIn("-u GH_TOKEN", probe)


class TestRound3(base._Base):

    def mint_bazos(self, gh=None, **kw):
        with _live():
            return self.mint(ACCT, gh=gh or FakeGitHub(air="ok"), **kw)

    def test_a_failed_mint_keeps_the_last_good_expiry(self):
        self.assertEqual(self.mint_bazos()[0], 0)
        self.assertEqual(self.mint_bazos(gh=FakeGitHub(air=500))[0], 1)
        st = pgt.read_state(ACCT, self.state)
        self.assertIs(st["ok"], False)
        self.assertEqual(st.get("last_ok_expires_at"), base.AIR_EXPIRES)
        self.assertEqual(self.mint_bazos(gh=FakeGitHub(air=500))[0], 1)
        self.assertEqual(pgt.read_state(ACCT, self.state).get("last_ok_expires_at"),
                         base.AIR_EXPIRES)

    def test_peer_repos_never_mint_airuleset_twice(self):
        with mock.patch("cli_peer_channel.peer_repos", return_value=[pgt.AIRULESET_REPO]):
            rc, out, err, gh, ssh = self.mint_bazos()
        self.assertEqual(rc, 0, err)
        posts = [r for r in gh.requests if r["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(ssh.calls), 1)

    def test_a_wider_airuleset_token_is_refused(self):
        rc, out, err, gh, ssh = self.mint_bazos(gh=FakeGitHub(air="wide"))
        self.assertEqual(rc, 1)
        self.assertIn("refusing", err)
        self.assertFalse((self.home / ".config" / "gh-app-tokens" / AIR_FILE).exists())
        self.assertIs(pgt.read_state(ACCT, self.state)["ok"], False)

    def test_accounts_status_says_na_for_the_repo_token(self):
        self.assertEqual(self.mint_bazos()[0], 0)
        self.assertEqual(pgt.status_line(ACCT, now=base.NOW, directory=self.state),
                         "gh-token: n/a (repo_less, #1394)")
        self.assertIn("airuleset issues: OK", pgt.airuleset_status_line(
            ACCT, now=base.NOW, directory=self.state))

    def test_mint_all_skips_it_pending_then_mints_it_live(self):
        def cmd():
            out = io.StringIO()
            with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
                    mock.patch.object(ci_sync, "run_after_mint", return_value=0), \
                    redirect_stdout(out):
                rc = pgt.cmd_project_gh_token(mock.Mock(
                    action="mint", account=None, all=True, dry_run=False, key=None))
            return rc, [c.args[0] for c in m.call_args_list], out.getvalue()
        with _pending():
            rc, minted, out = cmd()
        self.assertEqual(rc, 0)
        self.assertNotIn(ACCT, minted)
        self.assertIn("project-gh-token: %s: SKIPPED — pending" % ACCT, out)
        with _live():
            rc, minted, out = cmd()
        self.assertEqual(rc, 0)
        self.assertIn(ACCT, minted)
        self.assertNotIn("%s: SKIPPED" % ACCT, out)

    def test_the_bootstrap_next_step_names_the_repo_less_gate(self):
        line = pgt.render_next_steps(bootstrap.account_spec(ACCT))
        self.assertIn("project-gh-token verify $ACCOUNT", line)
        self.assertIn("repo-less", line)
        self.assertNotIn("its repo token", line)
        self.assertIn("its repo token", pgt.render_next_steps(bootstrap.account_spec("fohmixer")))


class TestSecretSyncSkipsRepoLess(unittest.TestCase):

    def test_sync_all_never_asks_a_repo_less_account(self):
        with _live(), mock.patch.object(ci_sync, "sync_account", return_value=0) as s:
            rc = ci_sync.sync_all(run=lambda *a, **k: None)
        self.assertEqual(rc, 0)
        self.assertNotIn(ACCT, [c.args[0] for c in s.call_args_list])
        self.assertIn("fohmixer", [c.args[0] for c in s.call_args_list])


if __name__ == "__main__":
    unittest.main()
