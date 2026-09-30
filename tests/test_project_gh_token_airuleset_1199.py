"""#1199: project accounts file airuleset tickets natively.

A #1184 project account (fohmixer@dev1) holds ONE App token, scoped to its own
repo, so `gh issue create -R zbynekdrlik/airuleset` failed there, and the
only way across was `gk-request`, the Odoo-stream relay (needs-gatekeeper).
The fix (Approach 1 of the design comment):

  * the controller minter ALSO mints, from the same installation, a token for
    `repositories: ["airuleset"]` with ONLY `issues: write` + `metadata: read`,
    delivered as `~/.config/gh-app-tokens/zbynekdrlik__airuleset` (never
    `primary`); airuleset missing from the installation (404/422) is ONE loud
    skip line that neither blocks the project token nor changes the exit code;
  * `gh-app-shim` picks that token when argv targets zbynekdrlik/airuleset
    (`-R x`, `--repo x`, `--repo=x`, a `gh api repos/zbynekdrlik/airuleset…`
    path) and `primary` otherwise, with no exec loop (#1051) and no token on
    any output;
  * `gk-request` on a project account refuses with a pointer to the native
    `gh issue create -R zbynekdrlik/airuleset`, labelling nothing;
  * `accounts status` shows `airuleset issues: OK | MISSING (<why>)` next to
    the project token's expiry;
  * `project-gh-token verify` (the go-live gate) proves, AS the account, that
    the airuleset token can WRITE issues, without creating one, and the
    bootstrap's printed next steps require it (owner escalation on #1199).

Everything is faked the #1190 way (see test_project_gh_token_1190): a
throwaway openssl key, a fake `urlopen`, a fake ssh `run` that executes the
remote command under a temp HOME. The "real gh" of the shim tests is a copy
of `/usr/bin/env` (a real binary, so the shim's `_is_real` accepts it): env
execs the gh SUBCOMMAND (`issue`, `api`, `pr`) as a PATH script that prints
the GH_TOKEN it received.
"""
import io
import os
import shutil
import stat
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_accounts as accounts  # noqa: E402
import cli_app_token  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402
import test_project_gh_token_1190 as base  # noqa: E402

AIR = base.AIR_REPO
AIR_FILE = "zbynekdrlik__airuleset"
TOKEN, AIR_TOKEN = base.TOKEN, base.AIR_TOKEN


def _air_posts(gh):
    return [r for r in gh.requests if r["method"] == "POST"
            and (r["body"] or {}).get("repositories") == ["airuleset"]]


# --------------------------------------------------------------------------- #
# the second, issues-only mint
# --------------------------------------------------------------------------- #
class TestAirMint(base._Base):

    def tokdir(self):
        return self.home / ".config" / "gh-app-tokens"

    def test_the_second_mint_is_airuleset_issues_only(self):
        rc, out, err, gh, ssh = self.mint(gh=base.FakeGitHub(air="ok"))
        self.assertEqual(rc, 0, err)
        posts = [r for r in gh.requests if r["method"] == "POST"]
        self.assertEqual(posts[0]["body"], {"repositories": ["fohmixer"]})
        self.assertEqual(posts[1]["body"], {
            "repositories": ["airuleset"],
            "permissions": {"issues": "write", "metadata": "read"}})
        self.assertEqual(posts[1]["url"], posts[0]["url"])   # same installation

    def test_the_airuleset_token_lands_beside_primary_never_as_it(self):
        rc, out, err, gh, ssh = self.mint(gh=base.FakeGitHub(air="ok"))
        self.assertEqual(rc, 0, err)
        d = self.tokdir()
        tok = d / AIR_FILE
        self.assertEqual(tok.read_text().strip(), AIR_TOKEN)
        self.assertEqual(stat.S_IMODE(tok.stat().st_mode), 0o600)
        self.assertEqual((d / (AIR_FILE + ".expires")).read_text(),
                         base.AIR_EXPIRES + "\n")
        self.assertEqual((d / (AIR_FILE + ".app")).read_text(), pgt.APP_SLUG + "\n")
        self.assertEqual(os.readlink(d / "primary"), str(d / "zbynekdrlik__fohmixer"))
        self.assertEqual((d / "primary").read_text().strip(), TOKEN)
        self.assertEqual(cli_app_token.read_app_slug(d), pgt.APP_SLUG)
        self.assertEqual(len(ssh.calls), 2)
        for call in ssh.calls:                       # stdin only, never argv
            self.assertNotIn(AIR_TOKEN, " ".join(call["argv"]))
        self.assertEqual(ssh.calls[1]["input"].strip(), AIR_TOKEN)
        state = (self.state / "fohmixer.json").read_text()
        self.assertNotIn(AIR_TOKEN, out + err + state)
        self.assertIn(base.AIR_EXPIRES, state)

    def test_airuleset_missing_from_the_installation_is_one_loud_skip(self):
        for status in (404, 422):
            with self.subTest(status):
                shutil.rmtree(self.tokdir(), ignore_errors=True)
                rc, out, err, gh, ssh = self.mint(gh=base.FakeGitHub(air=status))
                self.assertEqual(rc, 0, err)                  # exit code unaffected
                self.assertEqual((self.tokdir() / "primary").read_text().strip(),
                                 TOKEN)                       # project token delivered
                self.assertEqual(len(ssh.calls), 1)
                self.assertFalse((self.tokdir() / AIR_FILE).exists())
                lines = [ln for ln in err.splitlines() if AIR in ln]
                self.assertEqual(len(lines), 1, err)
                self.assertIn("SKIP", lines[0])
                self.assertIn("https://github.com/settings/installations/%d"
                              % base.INSTALLATION, lines[0])
                self.assertIn("HTTP %d" % status, lines[0])

    def test_a_wider_or_multi_repo_airuleset_token_is_refused(self):
        for air in ("wide", "scope"):
            with self.subTest(air):
                shutil.rmtree(self.tokdir(), ignore_errors=True)
                rc, out, err, gh, ssh = self.mint(gh=base.FakeGitHub(air=air))
                self.assertEqual(rc, 1)
                self.assertIn("refusing", err)
                self.assertFalse((self.tokdir() / AIR_FILE).exists())
                self.assertEqual((self.tokdir() / "primary").read_text().strip(),
                                 TOKEN)
                self.assertNotIn(AIR_TOKEN, out + err)

    def test_another_airuleset_mint_failure_fails_loudly(self):
        rc, out, err, gh, ssh = self.mint(gh=base.FakeGitHub(air=500))
        self.assertEqual(rc, 1)
        self.assertIn("HTTP 500", err)
        self.assertEqual((self.tokdir() / "primary").read_text().strip(), TOKEN)

    def test_dry_run_names_the_airuleset_token_and_mints_nothing(self):
        rc, out, err, gh, ssh = self.mint(dry_run=True)
        self.assertEqual(rc, 0, err)
        self.assertIn(AIR_FILE, out)
        self.assertEqual(_air_posts(gh), [])
        self.assertEqual(ssh.calls, [])

    def test_airuleset_delivery_command_never_touches_primary(self):
        cmd = pgt.render_delivery_command(AIR, base.AIR_EXPIRES, primary=False)
        self.assertNotIn("primary", cmd)
        self.assertIn(AIR_FILE, cmd)


# --------------------------------------------------------------------------- #
# the shim: the airuleset token for an airuleset target, primary otherwise
# --------------------------------------------------------------------------- #
_SUBCMD = ('#!/usr/bin/env bash\n'
           'echo "seen ${GH_TOKEN:-} ${AIRULESET_GH_SHIM_DEPTH:-}"\n')


class TestAirShim(base._ShimBase):

    def setUp(self):
        super().setUp()
        (self.realbin / "gh").unlink()
        shutil.copy2(shutil.which("env"), self.realbin / "gh")    # a real binary
        for sub in ("issue", "api", "pr"):
            (self.realbin / sub).write_text(_SUBCMD)
            (self.realbin / sub).chmod(0o755)
        cmd = pgt.render_delivery_command(AIR, "2999-01-01T00:00:00Z", primary=False)
        subprocess.run(["bash", "-c", cmd], input=AIR_TOKEN + "\n", env=self.env,
                       check=True, capture_output=True, text=True)

    def token_for(self, *args, path=None):
        r = self.run_gh(path or self.shim, *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for tok in (TOKEN, AIR_TOKEN):
            self.assertNotIn(tok, r.stderr)
        return r.stdout.split()[1]

    def test_airuleset_targets_get_the_airuleset_token(self):
        for argv in (("issue", "create", "-R", AIR, "--title", "x"),
                     ("issue", "create", "--repo", AIR),
                     ("issue", "create", "--repo=" + AIR),
                     ("issue", "list", "-R", "ZbynekDrlik/Airuleset"),
                     ("issue", "list", "-R", "https://github.com/zbynekdrlik/airuleset"),
                     ("api", "repos/zbynekdrlik/airuleset/issues"),
                     ("api", "/repos/zbynekdrlik/airuleset/issues"),
                     ("api", "-X", "POST", "repos/zbynekdrlik/airuleset/issues",
                      "-f", "title=x"),
                     ("api", "repos/zbynekdrlik/airuleset")):
            with self.subTest(argv):
                self.assertEqual(self.token_for(*argv), AIR_TOKEN)

    def test_everything_else_gets_primary(self):
        for argv in (("issue", "list"),
                     ("issue", "list", "-R", base.REPO),
                     ("issue", "list", "-R", "zbynekdrlik/airuleset-fork"),
                     ("issue", "list", "-R", "someone/airuleset"),
                     ("api", "repos/zbynekdrlik/airuleset-x/issues"),
                     ("api", "repos/zbynekdrlik/fohmixer/pulls"),
                     ("api", "installation/repositories"),
                     ("issue", "create", "--body", "repos/zbynekdrlik/airuleset/x"),
                     ("issue", "create", "--title", AIR),
                     ("pr", "list", "--search", AIR)):
            with self.subTest(argv):
                self.assertEqual(self.token_for(*argv), TOKEN)

    def test_missing_airuleset_token_is_said_never_silent(self):
        (self.home / ".config/gh-app-tokens" / AIR_FILE).unlink()
        r = self.run_gh(self.shim, "issue", "create", "-R", AIR)
        self.assertIn("#1199", r.stderr)
        self.assertIn(AIR_FILE, r.stderr)
        self.assertNotIn(TOKEN, r.stderr)

    def test_an_expired_airuleset_token_warns(self):
        (self.home / ".config/gh-app-tokens" / (AIR_FILE + ".expires")
         ).write_text("2000-01-01T00:00:00Z\n")
        r = self.run_gh(self.shim, "issue", "create", "-R", AIR)
        self.assertIn("expired", r.stderr)
        self.assertNotIn(AIR_TOKEN, r.stderr)
        self.assertNotIn("expired", self.run_gh(self.shim, "issue", "list").stderr)

    def test_the_full_chain_routes_and_never_loops(self):
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            self._ensure()
        gh = self.bin / "gh"
        self.assertIn("_UPSTREAM='%s'" % self.shim, gh.read_text())
        r = self.run_gh(gh, "issue", "create", "-R", AIR)
        self.assertEqual(r.stdout.split(), ["seen", AIR_TOKEN, "1"],
                         r.stderr)
        r = self.run_gh(gh, "issue", "list")
        self.assertEqual(r.stdout.split(), ["seen", TOKEN, "1"], r.stderr)

    def test_the_bootstrap_renders_this_shim(self):
        self.assertIn(AIR_FILE, pgt.render_gh_app_shim())
        self.assertIn(pgt.render_gh_app_shim(),
                      base.TestBootstrapRender._render(None, "fohmixer"))


# --------------------------------------------------------------------------- #
# gk-request refuses on a project account
# --------------------------------------------------------------------------- #
class TestGkRequestRefusal(unittest.TestCase):

    def run_gk(self, user, **kw):
        args = mock.Mock(**dict(dict(repo=AIR, issue=None, title="x", body=None,
                                     body_file=None, comment=None), **kw))
        calls = []

        def run(argv, **_kw):
            calls.append(list(argv))
            return subprocess.CompletedProcess(
                argv, 0, "https://github.com/%s/issues/7\n" % AIR, "")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(airuleset, "_current_user", return_value=user), \
                mock.patch("subprocess.run", side_effect=run), \
                redirect_stdout(out), redirect_stderr(err):
            rc = airuleset.cmd_gk_request(args)
        return rc, calls, out.getvalue(), err.getvalue()

    def test_project_account_refuses_and_labels_nothing(self):
        for kw in ({}, {"issue": 5, "title": None}):
            with self.subTest(kw):
                rc, calls, out, err = self.run_gk("fohmixer", **kw)
                self.assertNotEqual(rc, 0)
                self.assertEqual(calls, [])                    # no gh at all
                lines = (out + err).strip().splitlines()
                self.assertEqual(len(lines), 1, out + err)
                self.assertIn("gh issue create -R zbynekdrlik/airuleset", lines[0])
                self.assertNotIn("needs-gatekeeper", out)

    def test_every_declared_project_account_refuses(self):
        for acct in bootstrap.SERVICE_ACCOUNTS:
            self.assertNotEqual(self.run_gk(acct)[0], 0, acct)

    def test_odoo_streams_and_the_owner_box_still_relay(self):
        for user in ("montalu1", "david1", "newlevel"):
            with self.subTest(user):
                rc, calls, out, err = self.run_gk(user)
                self.assertEqual(rc, 0, out + err)
                self.assertIn(["gh", "issue", "edit", "7", "--add-label",
                               "needs-gatekeeper", "-R", AIR], calls)


# --------------------------------------------------------------------------- #
# accounts status: both expiries + the airuleset issues verdict
# --------------------------------------------------------------------------- #
class TestAirStatus(base._Base):

    def status(self, now=base.NOW):
        out = io.StringIO()
        with mock.patch.object(pgt, "state_dir", return_value=self.state), \
                mock.patch.object(pgt.time, "time", return_value=now), \
                redirect_stdout(out):
            rc = accounts.cmd_accounts(mock.Mock(action="status", json=False,
                                                 registry=None, account=None,
                                                 from_dir=None, render=False,
                                                 apply=False))
        self.assertEqual(rc, 0)
        return out.getvalue()

    def test_never_minted_is_missing(self):
        self.assertIn("airuleset issues: MISSING (never minted", self.status())

    def test_ok_shows_both_expiries(self):
        self.assertEqual(self.mint(gh=base.FakeGitHub(air="ok"))[0], 0)
        text = self.status()
        self.assertIn("gh-token: expires %s" % base.EXPIRES, text)
        self.assertIn("airuleset issues: OK — token expires %s" % base.AIR_EXPIRES,
                      text)

    def test_skipped_names_the_owner_action(self):
        self.mint(gh=base.FakeGitHub(air=422))
        text = self.status()
        self.assertIn("airuleset issues: MISSING (", text)
        self.assertIn("not in the App installation", text)
        self.assertIn("gh-token: expires %s" % base.EXPIRES, text)

    def test_an_expired_token_is_missing(self):
        self.mint(gh=base.FakeGitHub(air="ok"))
        self.assertIn("airuleset issues: MISSING (token expired",
                      self.status(now=base.NOW + 10 ** 8))

    def test_a_failed_airuleset_mint_is_missing(self):
        self.mint(gh=base.FakeGitHub(air=500))
        self.assertIn("airuleset issues: MISSING (FAILED", self.status())

    def test_the_json_record_carries_the_airuleset_state(self):
        self.mint(gh=base.FakeGitHub(air="ok"))
        with mock.patch.object(pgt, "state_dir", return_value=self.state):
            data = accounts._status(None)
        rec = data["project_accounts"]["fohmixer"]["gh_token"]["airuleset"]
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["expires_at"], base.AIR_EXPIRES)


# --------------------------------------------------------------------------- #
# verify: the go-live gate proves airuleset issues WRITE without a side effect
# --------------------------------------------------------------------------- #
class TestVerifyAirWrite(unittest.TestCase):

    run_verify = base.TestReviewVerify.run_verify

    def probe(self):
        calls = self.run_verify(base.verify_out())[1]
        import shlex
        return shlex.split(calls[0][-1])[2]

    def test_ok_needs_422_on_issues_and_a_refused_pulls_control(self):
        got, calls, text = self.run_verify(base.verify_out())
        self.assertEqual(got, 0, text)
        self.assertIn("airuleset issues", text)

    def test_no_issues_write_fails_the_gate(self):
        for code in (401, 403, 404):
            got, calls, text = self.run_verify(base.verify_out(issues=code))
            self.assertEqual(got, 1, code)
            self.assertIn("airuleset issues", text)
            self.assertIn("HTTP %d" % code, text)

    def test_a_non_discriminating_probe_fails_the_gate(self):
        got, calls, text = self.run_verify(base.verify_out(pulls=422))
        self.assertEqual(got, 1, text)
        self.assertIn("pulls", text)

    def test_a_probe_without_the_airuleset_sections_fails(self):
        got = self.run_verify("/home/fohmixer/.local/bin/gh\n@@primary\n%s\n@@rc 0\n"
                              % base.REPO)[0]
        self.assertEqual(got, 1)

    def test_the_probe_is_side_effect_free(self):
        probe = self.probe()
        self.assertIn("repos/zbynekdrlik/airuleset/issues", probe)
        self.assertIn("repos/zbynekdrlik/airuleset/pulls", probe)
        # no `title` (issues) and no `head`/`base` (pulls): GitHub can only
        # answer 422 or refuse, so nothing is ever created
        for field in ("title", "head=", "base="):
            self.assertNotIn(field, probe)
        self.assertIn("gh api installation/repositories", probe)


# --------------------------------------------------------------------------- #
# go-live: the bootstrap's printed next steps require verify
# --------------------------------------------------------------------------- #
class TestGoLiveStep(unittest.TestCase):

    def test_github_app_account_next_steps_require_verify(self):
        script = base.TestBootstrapRender._render(None, "fohmixer")
        steps = script[script.index("=== Next steps"):]
        self.assertIn("project-gh-token verify", steps)
        self.assertIn("project-gh-token mint", steps)
        self.assertIn("REQUIRED", steps)

    def test_other_accounts_have_no_verify_step(self):
        self.assertNotIn("project-gh-token verify",
                         base.TestBootstrapRender._render(None, "claudy"))


if __name__ == "__main__":
    unittest.main()
