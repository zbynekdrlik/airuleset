"""#1190 part 2: repo-scoped GitHub API tokens for project accounts, minted on
the controller by the `newlevel-project-accounts` App (ID 5131368).

A project account (fohmixer@dev1) had only a deploy key, so its Claude could
not merge a PR, watch CI or touch a ticket. The controller now mints a 1-hour
installation token limited to the account's ONE declared repo and delivers it
over the account's REMOTE_HOSTS ssh identity in the issue 888 token-file
contract; the account's gh reads it through an App shim chained under the
#1040 rate-guard wrapper.

Nothing here touches a real key, a real home or GitHub: the RSA key is a
throwaway `openssl genrsa` key in a temp dir, HTTP is a fake `urlopen`, ssh is
a fake `run` that executes the REMOTE command locally under a temp HOME (so
the delivery script really runs), and the real gh is a copy of `printenv`.

Covers the design acceptance:
  * the JWT header/claims are right and the openssl signature verifies;
  * the installation lookup names the repo; not installed -> a loud refusal;
  * the mint body restricts `repositories` to the one repo (and a token for
    any other scope is refused);
  * the token file layout, modes and sidecars match the issue 888 contract
    (`cli_app_token.read_app_slug` reads the slug back);
  * the token never reaches stdout/stderr, ssh argv or the state file;
  * the rendered App shim exports GH_TOKEN from `primary` and, chained by
    the rate-guard installer, gives `gh` the token with no exec loop.
"""
import http.client
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_accounts as accounts  # noqa: E402
import cli_app_token  # noqa: E402
import cli_gh_rate  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO = "zbynekdrlik/fohmixer"
GHS = "ghs" + "_"   # never a literal token prefix in a file (secret scan)
TOKEN = GHS + "Fake1190Token" * 3
INSTALLATION = 166394463
EXPIRES = "2026-09-30T12:00:00Z"
NOW = 1_790_000_000


def _b64d(part):
    import base64
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class _Keys:
    """A throwaway PKCS#1 key pair (the App key's format) in a temp dir."""

    def __init__(self, base):
        self.key = Path(base) / "app.pem"
        self.pub = Path(base) / "app.pub.pem"
        subprocess.run(["openssl", "genrsa", "-traditional", "-out",
                        str(self.key), "2048"], check=True,
                       capture_output=True)
        os.chmod(self.key, 0o600)
        subprocess.run(["openssl", "rsa", "-in", str(self.key), "-pubout",
                        "-out", str(self.pub)], check=True, capture_output=True)


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGitHub:
    """A fake `urlopen`: records every request, answers the two endpoints."""

    def __init__(self, installed=True, scoped_to=None, token=TOKEN):
        self.installed = installed
        self.scoped_to = scoped_to or [REPO]
        self.token = token
        self.requests = []
        self.ctypes = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode()) if req.data else None
        self.ctypes.append(req.get_header("Content-type"))
        self.requests.append({"method": req.get_method(), "url": req.full_url,
                              "auth": req.get_header("Authorization"),
                              "body": body})
        url = req.full_url
        if url == "https://api.github.com/repos/%s/installation" % REPO:
            if not self.installed:
                raise urllib.error.HTTPError(
                    url, 404, "Not Found", {},
                    io.BytesIO(b'{"message":"Not Found"}'))
            return _Resp(200, {"id": INSTALLATION, "app_slug": pgt.APP_SLUG})
        if url == ("https://api.github.com/app/installations/%d/access_tokens"
                   % INSTALLATION):
            return _Resp(201, {
                "token": self.token, "expires_at": EXPIRES,
                "permissions": {"contents": "write"},
                "repositories": [{"name": r.split("/")[1], "full_name": r}
                                 for r in self.scoped_to]})
        raise AssertionError("unexpected request %s %s" % (req.get_method(), url))


class FakeSsh:
    """A fake `run` for the ssh delivery: records argv + stdin, then runs the
    REMOTE command (the last argv element) locally in bash under `home`."""

    def __init__(self, home, rc=None):
        self.home = Path(home)
        self.calls = []
        self.rc = rc

    def __call__(self, argv, input=None, **kw):
        self.calls.append({"argv": list(argv), "input": input})
        if self.rc is not None:
            return subprocess.CompletedProcess(argv, self.rc, "", "boom")
        env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
        return subprocess.run(["bash", "-c", argv[-1]], input=input, env=env,
                              capture_output=True, text=True, timeout=30)


class _Base(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.keys = _Keys(self.base)
        self.home = self.base / "acct-home"
        self.home.mkdir()
        self.state = self.base / "state"

    def mint(self, account="fohmixer", gh=None, ssh=None, **kw):
        gh = gh if gh is not None else FakeGitHub()
        ssh = ssh if ssh is not None else FakeSsh(self.home)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = pgt.mint_account(account, key_path=str(self.keys.key), now=NOW,
                                  urlopen=gh, run=ssh, state_dir=self.state, **kw)
        return rc, out.getvalue(), err.getvalue(), gh, ssh


# --------------------------------------------------------------------------- #
# the App JWT
# --------------------------------------------------------------------------- #
class TestJwt(_Base):

    def test_header_claims_and_openssl_signature(self):
        jwt = pgt.build_jwt(str(self.keys.key), now=NOW)
        head, claims, sig = jwt.split(".")
        self.assertEqual(json.loads(_b64d(head)), {"alg": "RS256", "typ": "JWT"})
        c = json.loads(_b64d(claims))
        self.assertEqual(c, {"iat": NOW - 60, "exp": NOW + 540, "iss": 5131368})
        self.assertLessEqual(c["exp"] - c["iat"], 600)   # GitHub's 10-min cap
        sig_file = self.base / "sig"
        sig_file.write_bytes(_b64d(sig))
        r = subprocess.run(["openssl", "dgst", "-sha256", "-verify",
                            str(self.keys.pub), "-signature", str(sig_file)],
                           input=("%s.%s" % (head, claims)).encode(),
                           capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(b"Verified OK", r.stdout)

    def test_missing_key_refused(self):
        with self.assertRaises(pgt.MintError) as cm:
            pgt.build_jwt(str(self.base / "nope.pem"), now=NOW)
        self.assertIn("nope.pem", str(cm.exception))

    def test_group_or_world_readable_key_refused(self):
        os.chmod(self.keys.key, 0o640)
        with self.assertRaises(pgt.MintError) as cm:
            pgt.build_jwt(str(self.keys.key), now=NOW)
        self.assertIn("0600", str(cm.exception))


# --------------------------------------------------------------------------- #
# installation lookup + the repo-scoped mint
# --------------------------------------------------------------------------- #
class TestLookupAndMint(_Base):

    def test_installation_lookup_names_the_repo_with_the_jwt(self):
        gh = FakeGitHub()
        self.assertEqual(pgt.find_installation(REPO, "J.W.T", urlopen=gh),
                         INSTALLATION)
        self.assertEqual(gh.requests, [{
            "method": "GET", "auth": "Bearer J.W.T", "body": None,
            "url": "https://api.github.com/repos/%s/installation" % REPO}])

    def test_not_installed_is_a_loud_refusal(self):
        with self.assertRaises(pgt.MintError) as cm:
            pgt.find_installation(REPO, "J.W.T", urlopen=FakeGitHub(installed=False))
        msg = str(cm.exception)
        self.assertIn("not installed", msg)
        self.assertIn(REPO, msg)
        self.assertIn("https://github.com/apps/newlevel-project-accounts", msg)

    def test_mint_body_restricts_repositories_to_the_one_repo(self):
        gh = FakeGitHub()
        got = pgt.mint_token(INSTALLATION, REPO, "J.W.T", urlopen=gh)
        self.assertEqual(got, {"token": TOKEN, "expires_at": EXPIRES})
        self.assertEqual(gh.requests[-1]["method"], "POST")
        self.assertEqual(gh.requests[-1]["body"], {"repositories": ["fohmixer"]})
        self.assertEqual(gh.requests[-1]["auth"], "Bearer J.W.T")

    def test_a_token_scoped_to_anything_else_is_refused(self):
        for scope in (["zbynekdrlik/fohmixer", "zbynekdrlik/other"],
                      ["zbynekdrlik/other"]):
            with self.assertRaises(pgt.MintError, msg=scope):
                pgt.mint_token(INSTALLATION, REPO, "J.W.T",
                               urlopen=FakeGitHub(scoped_to=scope))

    def test_a_malformed_token_is_refused(self):
        for bad in ("", GHS + "ok\nsecond", GHS + "ok; rm -rf ~"):
            with self.assertRaises(pgt.MintError, msg=repr(bad)):
                pgt.mint_token(INSTALLATION, REPO, "J.W.T",
                               urlopen=FakeGitHub(token=bad))


# --------------------------------------------------------------------------- #
# delivery: the issue 888 token-file contract, run for real under a temp HOME
# --------------------------------------------------------------------------- #
class TestDelivery(_Base):

    def deliver(self, token=TOKEN, expires=EXPIRES):
        cmd = pgt.render_delivery_command(REPO, expires)
        return subprocess.run(["bash", "-c", cmd], input=token + "\n",
                              env={"HOME": str(self.home),
                                   "PATH": os.environ["PATH"]},
                              capture_output=True, text=True)

    def test_layout_modes_and_sidecars(self):
        r = self.deliver()
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.home / ".config" / "gh-app-tokens"
        tok = d / "zbynekdrlik__fohmixer"
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(tok.stat().st_mode), 0o600)
        self.assertEqual(tok.read_text().strip(), TOKEN)
        self.assertEqual((d / "zbynekdrlik__fohmixer.expires").read_text(),
                         EXPIRES + "\n")
        self.assertEqual((d / "zbynekdrlik__fohmixer.app").read_text(),
                         "newlevel-project-accounts\n")
        primary = d / "primary"
        self.assertTrue(primary.is_symlink())
        self.assertEqual(os.readlink(primary), str(tok))   # absolute, like 888
        self.assertEqual(cli_app_token.read_app_slug(d), pgt.APP_SLUG)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        # no temp file left behind
        self.assertEqual(sorted(p.name for p in d.iterdir()),
                         ["primary", "zbynekdrlik__fohmixer",
                          "zbynekdrlik__fohmixer.app",
                          "zbynekdrlik__fohmixer.expires"])

    def test_redelivery_replaces_the_token_and_keeps_primary(self):
        self.assertEqual(self.deliver().returncode, 0)
        r = self.deliver(token=GHS + "second", expires="2026-09-30T13:00:00Z")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.home / ".config" / "gh-app-tokens"
        self.assertEqual((d / "primary").read_text().strip(), GHS + "second")

    def test_empty_or_malformed_stdin_writes_nothing(self):
        for bad in ("", GHS + "x y"):
            r = self.deliver(token=bad)
            self.assertNotEqual(r.returncode, 0, repr(bad))
            self.assertFalse((self.home / ".config" / "gh-app-tokens"
                              / "primary").exists())

    def test_rejects_an_expires_value_that_is_not_iso(self):
        with self.assertRaises(pgt.MintError):
            pgt.render_delivery_command(REPO, "2026-09-30T12:00:00Z'; rm -rf ~")


# --------------------------------------------------------------------------- #
# the whole mint for one account
# --------------------------------------------------------------------------- #
class TestMintAccount(_Base):

    def test_mint_delivers_and_never_prints_the_token(self):
        rc, out, err, gh, ssh = self.mint()
        self.assertEqual(rc, 0, err)
        self.assertNotIn(TOKEN, out + err)
        self.assertEqual(len(ssh.calls), 1)
        argv = ssh.calls[0]["argv"]
        self.assertNotIn(TOKEN, " ".join(argv))        # stdin only, never argv
        self.assertEqual(ssh.calls[0]["input"].strip(), TOKEN)
        self.assertIn("fohmixer@100.104.8.125", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[argv.index("-i") + 1],
                         os.path.expanduser("~/.secrets/airuleset_push_ed25519"))
        self.assertEqual((self.home / ".config/gh-app-tokens/primary")
                         .read_text().strip(), TOKEN)
        state = json.loads((self.state / "fohmixer.json").read_text())
        self.assertTrue(state["ok"])
        self.assertEqual(state["expires_at"], EXPIRES)
        self.assertEqual(state["repo"], REPO)
        self.assertEqual(state["installation_id"], INSTALLATION)
        self.assertNotIn(TOKEN, json.dumps(state))
        self.assertIn(EXPIRES, out)

    def test_not_installed_refuses_loudly_and_delivers_nothing(self):
        rc, out, err, gh, ssh = self.mint(gh=FakeGitHub(installed=False))
        self.assertEqual(rc, 1)
        self.assertIn("not installed", err)
        self.assertEqual(ssh.calls, [])
        self.assertEqual([r["method"] for r in gh.requests], ["GET"])
        state = json.loads((self.state / "fohmixer.json").read_text())
        self.assertFalse(state["ok"])
        self.assertIn("not installed", state["error"])

    def test_failed_delivery_is_loud_and_keeps_the_last_good_expiry(self):
        self.assertEqual(self.mint()[0], 0)
        rc, out, err, gh, ssh = self.mint(ssh=FakeSsh(self.home, rc=1))
        self.assertEqual(rc, 1)
        self.assertNotIn(TOKEN, out + err)
        state = json.loads((self.state / "fohmixer.json").read_text())
        self.assertFalse(state["ok"])
        self.assertEqual(state["last_ok_expires_at"], EXPIRES)

    def test_dry_run_looks_up_but_neither_mints_nor_delivers(self):
        rc, out, err, gh, ssh = self.mint(dry_run=True)
        self.assertEqual(rc, 0, err)
        self.assertEqual([r["method"] for r in gh.requests], ["GET"])
        self.assertEqual(ssh.calls, [])
        self.assertFalse((self.state / "fohmixer.json").exists())
        self.assertIn(str(INSTALLATION), out)
        self.assertIn("fohmixer@dev1", out)

    def test_account_without_github_app_is_refused(self):
        rc, out, err, gh, ssh = self.mint(account="claudy")
        self.assertEqual(rc, 1)
        self.assertIn("github_app", err)
        self.assertEqual(gh.requests, [])

    def test_mint_all_covers_exactly_the_github_app_accounts(self):
        self.assertEqual(pgt.github_app_accounts(), ["fohmixer"])
        with mock.patch.object(pgt, "mint_account", return_value=0) as m:
            rc = pgt.cmd_project_gh_token(mock.Mock(action="mint", account=None,
                                                    all=True, dry_run=False,
                                                    key=None))
        self.assertEqual(rc, 0)
        self.assertEqual([c.args[0] for c in m.call_args_list], ["fohmixer"])

    def test_mint_all_fails_when_any_account_fails(self):
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS["camera-box"],
                             {"github_app": True}), \
                mock.patch.object(pgt, "mint_account", side_effect=[1, 0]) as m:
            rc = pgt.cmd_project_gh_token(mock.Mock(action="mint", account=None,
                                                    all=True, dry_run=False,
                                                    key=None))
        self.assertEqual(rc, 1)
        self.assertEqual(m.call_count, 2)   # one failure never skips the rest

    def test_cli_usage(self):
        for ns in (mock.Mock(action="mint", account=None, all=False),
                   mock.Mock(action="mint", account="fohmixer", all=True)):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(pgt.cmd_project_gh_token(ns), 2)
        self.assertIs(airuleset.SUBCOMMANDS["project-gh-token"],
                      pgt.cmd_project_gh_token)


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #
class TestDeclaration(unittest.TestCase):

    def test_fohmixer_declares_github_app(self):
        self.assertIs(bootstrap.SERVICE_ACCOUNTS["fohmixer"]["github_app"], True)
        self.assertEqual(bootstrap.validate_all(), {})

    def test_github_app_must_be_a_bool_and_needs_a_repo(self):
        spec = dict(bootstrap.SERVICE_ACCOUNTS["fohmixer"])
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {"projx": spec}):
            self.assertTrue(any("github_app" in e for e in bootstrap.validate_account(
                "projx", dict(spec, github_app="yes"))))
            no_repo = {k: v for k, v in spec.items()
                       if k not in ("repo", "project_dir", "tmux_session")}
            no_repo["webterm_sessions"] = {}
            self.assertTrue(any("github_app" in e for e in bootstrap.validate_account(
                "projx", no_repo)))


# --------------------------------------------------------------------------- #
# the consumer: the App shim, chained by the rate-guard installer
# --------------------------------------------------------------------------- #
class _ShimBase(_Base):
    """A temp account home with a delivered token, the rendered App shim at
    ~/.local/bin/gh-app-shim and a real gh BINARY (printenv) on PATH."""

    def setUp(self):
        super().setUp()
        self.bin = self.home / ".local" / "bin"
        self.bin.mkdir(parents=True)
        self.realbin = self.base / "realbin"
        self.realbin.mkdir()
        shutil.copy2(shutil.which("printenv"), self.realbin / "gh")
        self.env = {"HOME": str(self.home),
                    "PATH": "%s:%s:/usr/bin:/bin" % (self.bin, self.realbin),
                    cli_gh_rate.INTERNAL_ENV: "1"}
        cmd = pgt.render_delivery_command(REPO, "2999-01-01T00:00:00Z")
        subprocess.run(["bash", "-c", cmd], input=TOKEN + "\n", env=self.env,
                       check=True, capture_output=True, text=True)
        self.shim = self.bin / "gh-app-shim"
        self.shim.write_text(pgt.render_gh_app_shim())
        self.shim.chmod(0o755)

    def run_gh(self, path, *args):
        return subprocess.run([str(path), *args], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def _ensure(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
                redirect_stdout(io.StringIO()):
            return cli_gh_rate.ensure_gh_rate_wrapper(
                shim=str(self.bin / "gh"), upstream=str(self.bin / "gh-upstream"),
                python_exe=sys.executable,
                module=str(ROOT / "cli_gh_rate.py"), verbose=False)


class TestConsumerShim(_ShimBase):

    def test_shim_exports_the_primary_token_and_execs_the_real_gh(self):
        r = self.run_gh(self.shim, "GH_TOKEN")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), TOKEN)

    def test_shim_is_recognised_as_the_app_shim_never_as_our_wrapper(self):
        self.assertTrue(cli_gh_rate._is_app_token_shim(str(self.shim)))
        self.assertFalse(cli_gh_rate._is_our_wrapper(str(self.shim)))

    def test_shim_warns_on_an_expired_token(self):
        (self.home / ".config/gh-app-tokens/zbynekdrlik__fohmixer.expires"
         ).write_text("2000-01-01T00:00:00Z\n")
        r = self.run_gh(self.shim, "GH_TOKEN")
        self.assertIn("expired", r.stderr)
        self.assertIn("project-gh-token", r.stderr)
        self.assertNotIn(TOKEN, r.stderr)

    def test_shim_without_a_token_says_so(self):
        shutil.rmtree(self.home / ".config" / "gh-app-tokens")
        r = self.run_gh(self.shim, "GH_TOKEN")
        self.assertIn("project-gh-token mint", r.stderr)

    def test_fresh_account_install_chains_the_wrapper_over_the_app_shim(self):
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            status = self._ensure()
        self.assertIn("chain", status)
        text = (self.bin / "gh").read_text()
        self.assertIn("_UPSTREAM='%s'" % self.shim, text)
        self.assertIn("_OBSERVE=1", text)
        r = self.run_gh(self.bin / "gh", "GH_TOKEN")        # the full chain
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), TOKEN)
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            self.assertIn("already installed (chain", self._ensure())
        self.assertFalse((self.bin / "gh-upstream").exists())

    def test_wrap_in_place_account_also_chains(self):
        shutil.copy2(self.realbin / "gh", self.bin / "gh")      # a real gh binary
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            self._ensure()
        text = (self.bin / "gh").read_text()
        self.assertIn("_UPSTREAM='%s'" % self.shim, text)
        r = self.run_gh(self.bin / "gh", "GH_TOKEN")
        self.assertEqual(r.stdout.strip(), TOKEN, r.stderr)

    def test_no_exec_loop_when_the_wrapper_is_first_on_path(self):
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            self._ensure()
        # the shim itself must skip the #! wrapper at ~/.local/bin/gh
        r = self.run_gh(self.shim, "AIRULESET_GH_SHIM_DEPTH")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)   # unset: no hop
        r = self.run_gh(self.bin / "gh", "AIRULESET_GH_SHIM_DEPTH")
        self.assertEqual(r.stdout.strip(), "1")


# --------------------------------------------------------------------------- #
# the bootstrap renders the shim for github_app accounts only
# --------------------------------------------------------------------------- #
class TestBootstrapRender(unittest.TestCase):

    def _render(self, account):
        from cli_webterm_only import WEBTERM_CONTROLLER_LANE_PUBKEYS as lanes
        fake = "ssh-ed25519 " + "AAAA" + "X" * 64 + " webterm-timo-controller"
        with mock.patch.dict(lanes, {"timo": fake}):
            return bootstrap.render_root_bootstrap(account)

    def test_fohmixer_render_installs_the_app_shim_as_the_account(self):
        script = self._render("fohmixer")
        self.assertIn(pgt.render_gh_app_shim(), script)
        start = script.index("# 11. Project-account GitHub App token shim")
        end = script.index("GH_APP_SHIM_EOF\n", script.index("<< 'GH_APP_SHIM_EOF'")
                           + 20) + len("GH_APP_SHIM_EOF\n")
        step = script[start:end]
        install = step[:step.index("<< 'GH_APP_SHIM_EOF'")]
        self.assertIn('runuser -l "$ACCOUNT" -c', install)   # as the account
        self.assertIn(".local/bin/gh-app-shim", install)
        # #1051: the shim may READ gh-upstream (a wrap-in-place real binary),
        # but the step never INSTALLS into that slot
        self.assertNotIn("gh-upstream", install)
        r = subprocess.run(["bash", "-n"], input=script, text=True,
                           capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.run_step(step)

    def run_step(self, step):
        """Run the rendered step with a PATH `runuser` stub (`-l acct -c cmd`
        runs `cmd` under a temp HOME, no root): the shim lands 0755 and
        byte-identical, the token dir 0700."""
        with tempfile.TemporaryDirectory() as tmp:
            home, stub = Path(tmp) / "home", Path(tmp) / "bin"
            home.mkdir()
            stub.mkdir()
            (stub / "runuser").write_text(
                '#!/usr/bin/env bash\n[ "$1" = -l ] && [ "$3" = -c ] || exit 99\n'
                'HOME="$STEP_HOME" exec bash -c "$4"\n')
            (stub / "runuser").chmod(0o755)
            r = subprocess.run(["bash", "-c", "set -euo pipefail\nACCOUNT=projx\n"
                                + step], text=True, capture_output=True,
                               env={"PATH": "%s:%s" % (stub, os.environ["PATH"]),
                                    "HOME": str(home), "STEP_HOME": str(home)})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            shim = home / ".local/bin/gh-app-shim"
            self.assertEqual(shim.read_text(), pgt.render_gh_app_shim())
            self.assertEqual(stat.S_IMODE(shim.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((home / ".config/gh-app-tokens")
                                          .stat().st_mode), 0o700)

    def test_claudy_render_has_no_app_shim(self):
        self.assertNotIn("gh-app-shim", self._render("claudy"))


# --------------------------------------------------------------------------- #
# accounts status shows each account's token expiry
# --------------------------------------------------------------------------- #
class TestAccountsStatus(_Base):

    def status(self):
        out = io.StringIO()
        with mock.patch.object(pgt, "state_dir", return_value=self.state), \
                mock.patch.object(pgt.time, "time", return_value=NOW), \
                redirect_stdout(out):
            rc = accounts.cmd_accounts(mock.Mock(action="status", json=False,
                                                 registry=None, account=None,
                                                 from_dir=None, render=False,
                                                 apply=False))
        self.assertEqual(rc, 0)
        return out.getvalue()

    def test_never_minted(self):
        self.assertIn("gh-token: never minted", self.status())

    def test_expiry_after_a_mint(self):
        self.assertEqual(self.mint()[0], 0)
        self.assertIn("gh-token: expires %s" % EXPIRES, self.status())

    def test_last_failure_is_loud(self):
        self.mint(gh=FakeGitHub(installed=False))
        self.assertIn("gh-token: FAILED", self.status())


# --------------------------------------------------------------------------- #
# the controller timer
# --------------------------------------------------------------------------- #
class TestTimer(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.units = Path(self._tmp.name)
        self.calls = []

    def setup(self, box, user):
        def systemctl(args):
            self.calls.append(list(args))
            return 0, "", ""
        with redirect_stdout(io.StringIO()):
            return pgt.setup_timer(box_class_fn=lambda: box, user=user,
                                   systemctl=systemctl, unit_dir=self.units)

    def test_controller_installs_a_30_min_timer_for_mint_all(self):
        self.assertTrue(self.setup("controller", "airuleset"))
        service = (self.units / "project-gh-token.service").read_text()
        timer = (self.units / "project-gh-token.timer").read_text()
        self.assertIn("%s/airuleset.py project-gh-token mint --all" % ROOT, service)
        self.assertIn("Type=oneshot", service)
        self.assertIn("OnUnitActiveSec=30min", timer)
        self.assertIn(["daemon-reload"], self.calls)
        self.assertIn(["enable", "--now", "project-gh-token.timer"], self.calls)

    def test_any_other_box_or_account_installs_nothing(self):
        for box, user in (("workstation", "newlevel"), ("controller", "claudy"),
                          (None, "airuleset")):
            self.assertIsNone(self.setup(box, user), (box, user))
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertEqual(self.calls, [])



# --------------------------------------------------------------------------- #
# review round 1 (all fixed on the branch)
# --------------------------------------------------------------------------- #
ODOO_STYLE_SHIM = ("#!/usr/bin/env bash\n# odoo-erp issue 3281 App shim\n"
                   'export GH_TOKEN="$(gh-app-token)"\nexec gh "$@"\n')


class TestReviewChain(_ShimBase):
    """Cases 2/4 chain at once ONLY over the project shim: an odoo-style App
    shim at gh-app-shim re-resolves `gh` on PATH, and chaining under it in
    Case 2 left gh at exit 127 (the review's repro)."""

    def stage_odoo_shim(self):
        self.shim.write_text(ODOO_STYLE_SHIM)

    def test_recogniser(self):
        self.assertTrue(pgt.is_project_shim(str(self.shim)))
        self.stage_odoo_shim()
        self.assertFalse(pgt.is_project_shim(str(self.shim)))
        self.assertFalse(pgt.is_project_shim(str(self.base / "absent")))

    def test_odoo_style_shim_is_not_chained_on_a_fresh_account(self):
        self.stage_odoo_shim()
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            status = self._ensure()
        self.assertNotIn("chain", status)
        self.assertIn("_UPSTREAM='%s'" % (self.realbin / "gh"),
                      (self.bin / "gh").read_text())

    def test_odoo_style_shim_is_not_chained_on_wrap_in_place(self):
        self.stage_odoo_shim()
        shutil.copy2(self.realbin / "gh", self.bin / "gh")
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            self._ensure()
        self.assertIn("_UPSTREAM='%s'" % (self.bin / "gh-upstream"),
                      (self.bin / "gh").read_text())
        r = self.run_gh(self.bin / "gh", "HOME")          # gh still runs
        self.assertEqual(r.returncode, 0, r.stderr)


class TestReviewMint(_Base):

    def test_http_exception_is_recorded_not_raised(self):
        class Broken(FakeGitHub):
            def __call__(self, req, timeout=None):
                raise http.client.IncompleteRead(b"")
        rc, out, err, gh, ssh = self.mint(gh=Broken())
        self.assertEqual(rc, 1)
        self.assertIn("IncompleteRead", err)
        state = json.loads((self.state / "fohmixer.json").read_text())
        self.assertFalse(state["ok"])
        self.assertIn("IncompleteRead", state["error"])

    def test_an_unexpected_exception_is_recorded_by_class_only(self):
        class Boom(FakeGitHub):
            def __call__(self, req, timeout=None):
                raise RuntimeError("secret-looking text " + TOKEN)
        rc, out, err, gh, ssh = self.mint(gh=Boom())
        self.assertEqual(rc, 1)
        self.assertIn("unexpected RuntimeError", err)
        self.assertNotIn(TOKEN, out + err)
        self.assertNotIn(TOKEN, (self.state / "fohmixer.json").read_text())

    def test_a_target_without_a_pinned_identity_is_refused(self):
        remote = dict(pgt.account_target("fohmixer"))
        remote.pop("identity")
        with mock.patch.object(pgt, "account_target", return_value=remote):
            rc, out, err, gh, ssh = self.mint()
        self.assertEqual(rc, 1)
        self.assertEqual(ssh.calls, [])
        self.assertIn("refused-no-identity", err)

    def test_a_pending_target_is_refused(self):
        import cli_fleet
        entry = next(h for h in cli_fleet.REMOTE_HOSTS
                     if h["name"] == "fohmixer@dev1")
        with mock.patch.dict(entry, {"pending": True}):
            rc, out, err, gh, ssh = self.mint()
        self.assertEqual(rc, 1)
        self.assertIn("pending", err)
        self.assertEqual(gh.requests, [])

    def test_a_key_path_that_is_a_directory_is_refused(self):
        with self.assertRaises(pgt.MintError) as cm:
            pgt.build_jwt(str(self.base), now=NOW)
        self.assertIn("not a regular file", str(cm.exception))

    def test_the_mint_post_is_json(self):
        rc, out, err, gh, ssh = self.mint()
        self.assertEqual(rc, 0, err)
        self.assertEqual(gh.ctypes, [None, "application/json"])

    def test_a_failed_dry_run_records_nothing(self):
        rc, out, err, gh, ssh = self.mint(gh=FakeGitHub(installed=False),
                                          dry_run=True)
        self.assertEqual(rc, 1)
        self.assertFalse((self.state / "fohmixer.json").exists())

    def test_status_counts_minutes_both_ways(self):
        self.assertEqual(self.mint()[0], 0)       # expires EXPIRES
        exp = pgt.calendar.timegm(pgt.time.strptime(EXPIRES, "%Y-%m-%dT%H:%M:%SZ"))
        line = pgt.status_line("fohmixer", now=exp - 1800, directory=self.state)
        self.assertIn("(in 30 min)", line)
        line = pgt.status_line("fohmixer", now=exp + 600, directory=self.state)
        self.assertIn("(EXPIRED 10 min ago)", line)


class TestReviewVerify(unittest.TestCase):
    """`project-gh-token verify <acct>`: the go-live acceptance, run AS the
    account in a login shell (the #1183 gap the design names)."""

    def run_verify(self, stdout, rc=0):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, rc, stdout, "")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            got = pgt.verify_account("fohmixer", run=run)
        return got, calls, out.getvalue() + err.getvalue()

    def test_ok_when_gh_is_the_chain_and_answers_the_repo(self):
        got, calls, text = self.run_verify(
            "/home/fohmixer/.local/bin/gh\nzbynekdrlik/fohmixer\n")
        self.assertEqual(got, 0, text)
        argv = calls[0]
        self.assertIn("fohmixer@100.104.8.125", argv)
        self.assertIn("-i", argv)
        self.assertTrue(argv[-1].startswith("bash -lc "), argv[-1])
        self.assertIn("gh api repos/zbynekdrlik/fohmixer", argv[-1])

    def test_fails_on_another_repo_or_a_gh_off_the_chain(self):
        for stdout in ("/home/fohmixer/.local/bin/gh\nzbynekdrlik/other\n",
                       "/usr/bin/gh\nzbynekdrlik/fohmixer\n", ""):
            self.assertEqual(self.run_verify(stdout)[0], 1, stdout)
        self.assertEqual(self.run_verify(
            "/home/fohmixer/.local/bin/gh\nzbynekdrlik/fohmixer\n", rc=1)[0], 1)

    def test_cli_verify_needs_one_account(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(pgt.cmd_project_gh_token(
                mock.Mock(action="verify", account=None, all=False)), 2)
        with mock.patch.object(pgt, "verify_account", return_value=0) as v:
            self.assertEqual(pgt.cmd_project_gh_token(
                mock.Mock(action="verify", account="fohmixer", all=False)), 0)
        v.assert_called_once_with("fohmixer")


class TestReviewTimerWiring(unittest.TestCase):

    def test_pytest_guard_writes_no_real_unit(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pgt.Path, "home", return_value=Path(tmp)), \
                mock.patch("cli_filedrop_watchdog._run_systemctl",
                           side_effect=lambda a: calls.append(a) or (0, "", "")):
            got = pgt.setup_timer(box_class_fn=lambda: "controller",
                                  user="airuleset")
            self.assertIsNone(got)
            self.assertEqual(list(Path(tmp).rglob("*")), [])
        self.assertEqual(calls, [])

    def test_install_calls_the_timer_outside_the_watchdog_try(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("\n    maybe_setup_project_gh_token_timer()", src)


if __name__ == "__main__":
    unittest.main()
