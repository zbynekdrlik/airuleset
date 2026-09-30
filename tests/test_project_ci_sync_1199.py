"""#1199 follow-up: a project account updates its OWN repo's CI secrets.

The project App token has no Secrets permission (fohmixer's DENYLIST had to be
uploaded by hand from the controller, 30.9.). Approach 1 of the follow-up
design:

  * ``SERVICE_ACCOUNTS[acct].repo_secrets`` is the allow-list of secret names
    the controller may set on the account's declared repo (fohmixer:
    ``["DENYLIST"]``), validated by the account-policy validator;
  * the project session files an issue on its OWN repo labelled
    ``secret-sync:<NAME>`` whose body carries ONE ``File: <path under the
    account home>`` line;
  * the controller's ``project-gh-token mint --all`` timer run then (leaf
    ``cli_project_ci_sync``) lists those open issues with the owner gh,
    reads the file over the account's REMOTE_HOSTS ssh identity, pipes it into
    ``gh secret set <NAME> -R <repo>`` on stdin, comments name + UTC time and
    closes the issue. Anything off (name not allow-listed, another repo, a
    path escaping home or holding ``..``, an empty file, more than one File
    line) is a refusal comment + close, and nothing is set;
  * the value never appears in argv, stdout/stderr, a comment or an exception.

Everything is faked: ``FakeWorld`` is the ``run`` for BOTH the owner gh (issue
list / secret set / issue close, recorded) and the ssh read (the REMOTE
command runs locally in bash under a temp HOME, bytes in, bytes out). No real
gh, no real ssh, no network.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_policy as policy  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402

REPO = "zbynekdrlik/fohmixer"
NOW = 1_790_000_000
NOW_ISO = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW))
# a multi-line secret whose every line is distinctive, so a leak of ANY line
# anywhere is caught
VALUE = (b"private-denylist-line-" + b"alpha-7Q\n"
         b"private-denylist-line-" + b"bravo-3Z\n")
VALUE_LINES = [ln for ln in VALUE.decode().splitlines() if ln]
REL = "devel/fohmixer/.denylist.local"


def _sync():
    import cli_project_ci_sync
    return cli_project_ci_sync


def _issue(number=7, label="secret-sync:DENYLIST", body=None, url=None,
           extra_labels=()):
    labels = [{"name": n} for n in ((label,) if label else ()) + tuple(extra_labels)]
    return {"number": number, "title": "secret-sync %s" % label,
            "body": "Please sync.\nFile: %s\n" % REL if body is None else body,
            "labels": labels,
            "url": url or "https://github.com/%s/issues/%d" % (REPO, number)}


class FakeWorld:
    """The controller's `run`: the owner gh (recorded, canned) and the ssh
    read (the remote command runs locally under ``home``)."""

    def __init__(self, home, issues=(), *, ssh_rc=None, set_rc=0,
                 set_stderr=b"", list_rc=0):
        self.home = Path(home)
        self.issues = list(issues)
        self.ssh_rc = ssh_rc
        self.set_rc = set_rc
        self.set_stderr = set_stderr
        self.list_rc = list_rc
        self.calls = []
        self.secrets = {}
        self.closed = []

    def _out(self, argv, rc, out, err, kw):
        if kw.get("text"):
            out = out.decode() if isinstance(out, bytes) else out
            err = err.decode() if isinstance(err, bytes) else err
        else:
            out = out.encode() if isinstance(out, str) else out
            err = err.encode() if isinstance(err, str) else err
        return subprocess.CompletedProcess(argv, rc, out, err)

    def __call__(self, argv, input=None, **kw):
        self.calls.append({"argv": list(argv), "input": input, "kw": dict(kw)})
        if argv[0] == "ssh":
            if self.ssh_rc is not None:
                return self._out(argv, self.ssh_rc, b"",
                                 b"ssh: connect to host: Connection refused", kw)
            env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "")}
            r = subprocess.run(["bash", "-c", argv[-1]], input=b"", env=env,
                               capture_output=True, timeout=30)
            return self._out(argv, r.returncode, r.stdout, r.stderr, kw)
        if argv[:3] == ["gh", "issue", "list"]:
            return self._out(argv, self.list_rc, json.dumps(self.issues)
                             if self.list_rc == 0 else "", "boom" if self.list_rc
                             else "", kw)
        if argv[:3] == ["gh", "secret", "set"]:
            if self.set_rc == 0:
                self.secrets[argv[3]] = input
            return self._out(argv, self.set_rc, b"", self.set_stderr, kw)
        if argv[:3] == ["gh", "issue", "close"]:
            self.closed.append(list(argv))
            return self._out(argv, 0, "", "", kw)
        raise AssertionError("unexpected command %r" % (argv,))

    def of(self, *prefix):
        return [c for c in self.calls if c["argv"][:len(prefix)] == list(prefix)]


class _Base(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "acct-home"
        (self.home / "devel" / "fohmixer").mkdir(parents=True)
        (self.home / REL).write_bytes(VALUE)

    def sync(self, world, dry_run=False, account="fohmixer"):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = _sync().sync_account(account, run=world, now=NOW, dry_run=dry_run)
        return rc, out.getvalue(), err.getvalue()

    def assertNoLeak(self, world, *texts):
        """The value is in NO argv, NO text output, NO comment — only on the
        stdin of `gh secret set`."""
        for c in world.calls:
            blob = " ".join(c["argv"])
            for ln in VALUE_LINES:
                self.assertNotIn(ln, blob, c["argv"][:4])
            if c["argv"][:3] != ["gh", "secret", "set"] and c["input"]:
                raw = c["input"] if isinstance(c["input"], bytes) else c["input"].encode()
                for ln in VALUE_LINES:
                    self.assertNotIn(ln.encode(), raw)
        for t in texts:
            for ln in VALUE_LINES:
                self.assertNotIn(ln, t)

    def closing(self, world):
        self.assertEqual(len(world.closed), 1, world.closed)
        argv = world.closed[0]
        self.assertEqual(argv[3:6], ["7", "-R", REPO])
        comment = argv[argv.index("--comment") + 1]
        reason = argv[argv.index("--reason") + 1]
        return reason, comment


# --------------------------------------------------------------------------- #
# the declaration: repo_secrets is an allow-list, validated
# --------------------------------------------------------------------------- #
class TestDeclaration(unittest.TestCase):

    def test_fohmixer_allows_exactly_denylist(self):
        self.assertEqual(bootstrap.SERVICE_ACCOUNTS["fohmixer"]["repo_secrets"],
                         ["DENYLIST"])
        self.assertEqual(bootstrap.validate_all(), {})

    def test_bad_repo_secrets_are_refused(self):
        base = dict(bootstrap.SERVICE_ACCOUNTS["fohmixer"])
        for bad, needle in ((["denylist"], "secret name"),
                            (["GITHUB_TOKEN"], "GITHUB_"),
                            (["1ABC"], "secret name"),
                            (["A B"], "secret name"),
                            ("DENYLIST", "must be a list"),
                            (["DENYLIST", "DENYLIST"], "duplicate")):
            errs = bootstrap.validate_account("fohmixer", dict(base, repo_secrets=bad))
            self.assertTrue(any(needle in e for e in errs), (bad, errs))

    def test_repo_secrets_need_github_app(self):
        raw = dict(bootstrap.SERVICE_ACCOUNTS["fohmixer"])
        raw.pop("github_app")
        errs = bootstrap.validate_account("fohmixer",
                                          dict(raw, repo_secrets=["DENYLIST"]))
        self.assertTrue(any("repo_secrets" in e and "github_app" in e
                            for e in errs), errs)

    def test_the_validator_lives_in_the_policy_leaf(self):
        self.assertEqual(policy.validate_github_app(
            {"github_app": True, "repo": REPO, "repo_secrets": ["OK_NAME"]}), [])
        self.assertTrue(policy.validate_github_app(
            {"github_app": True, "repo": REPO, "repo_secrets": ["bad"]}))


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #
class TestSync(_Base):

    def test_allow_listed_request_is_set_from_the_account_file_on_stdin(self):
        world = FakeWorld(self.home, [_issue()])
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 0, err)
        sets = world.of("gh", "secret", "set")
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0]["argv"], ["gh", "secret", "set", "DENYLIST",
                                           "-R", REPO])
        self.assertEqual(world.secrets["DENYLIST"], VALUE)
        # the read went over the account's pinned ssh identity
        ssh = world.of("ssh")
        self.assertEqual(len(ssh), 1)
        self.assertIn("fohmixer@100.104.8.125", ssh[0]["argv"])
        self.assertIn("BatchMode=yes", ssh[0]["argv"])
        reason, comment = self.closing(world)
        self.assertEqual(reason, "completed")
        self.assertIn("DENYLIST", comment)
        self.assertIn(NOW_ISO, comment)
        self.assertNoLeak(world, out, err, comment)

    def test_the_issue_list_is_the_declared_repo_open_issues(self):
        world = FakeWorld(self.home, [])
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 0, err)
        lst = world.of("gh", "issue", "list")
        self.assertEqual(len(lst), 1)
        argv = lst[0]["argv"]
        self.assertEqual(argv[argv.index("-R") + 1], REPO)
        self.assertEqual(argv[argv.index("--state") + 1], "open")
        self.assertEqual(world.of("ssh"), [])

    def test_issues_without_a_secret_sync_label_are_ignored(self):
        world = FakeWorld(self.home, [_issue(label=None, extra_labels=("bug",)),
                                      _issue(label="secret-syncDENYLIST")])
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 0, err)
        self.assertEqual(world.of("ssh"), [])
        self.assertEqual(world.closed, [])

    def test_tilde_and_absolute_home_paths_are_accepted(self):
        for path in ("~/" + REL, "/home/fohmixer/" + REL):
            world = FakeWorld(self.home, [_issue(body="File: %s\n" % path)])
            rc, out, err = self.sync(world)
            self.assertEqual(rc, 0, (path, err))
            self.assertEqual(world.secrets.get("DENYLIST"), VALUE, path)

    def test_dry_run_reads_sets_and_closes_nothing(self):
        world = FakeWorld(self.home, [_issue()])
        rc, out, err = self.sync(world, dry_run=True)
        self.assertEqual(rc, 0, err)
        self.assertEqual(world.of("ssh"), [])
        self.assertEqual(world.of("gh", "secret", "set"), [])
        self.assertEqual(world.closed, [])
        self.assertIn("DRY RUN", out)
        self.assertIn("DENYLIST", out)


# --------------------------------------------------------------------------- #
# refusals: a comment + close, nothing set
# --------------------------------------------------------------------------- #
class TestRefusals(_Base):

    def refused(self, issue, needle, *, setup=None, read=False):
        if setup:
            setup()
        world = FakeWorld(self.home, [issue])
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 0, err)   # a bad request is not a controller failure
        self.assertEqual(world.of("gh", "secret", "set"), [])
        self.assertEqual(world.secrets, {})
        if not read:
            self.assertEqual(world.of("ssh"), [], "refused before any read")
        reason, comment = self.closing(world)
        self.assertEqual(reason, "not planned")
        self.assertIn("REFUSED", comment)
        self.assertIn(needle, comment)
        self.assertIn("Nothing was set", comment)
        self.assertNoLeak(world, out, err, comment)
        return comment

    def test_name_not_allow_listed(self):
        self.refused(_issue(label="secret-sync:OTHER_SECRET"), "OTHER_SECRET")

    def test_malformed_name(self):
        self.refused(_issue(label="secret-sync:deny list"), "not a GitHub secret name")

    def test_two_secret_sync_labels(self):
        self.refused(_issue(extra_labels=("secret-sync:OTHER",)), "one secret-sync")

    def test_another_repo(self):
        self.refused(_issue(body="Repo: zbynekdrlik/other\nFile: %s\n" % REL),
                     "zbynekdrlik/other")

    def test_path_outside_home(self):
        self.refused(_issue(body="File: /etc/passwd\n"), "/etc/passwd")

    def test_path_with_dotdot(self):
        self.refused(_issue(body="File: devel/../../other/x\n"), "..")

    def test_more_than_one_file_line(self):
        self.refused(_issue(body="File: %s\nFile: devel/x\n" % REL), "exactly one")

    def test_no_file_line(self):
        self.refused(_issue(body="sync please\n"), "exactly one")

    def test_protected_dir(self):
        self.refused(_issue(body="File: .claude/.credentials.json\n"), ".claude")

    def test_symlink_into_a_protected_dir(self):
        def setup():
            (self.home / ".ssh").mkdir()
            (self.home / ".ssh" / "id_ed25519").write_bytes(VALUE)
            os.symlink(self.home / ".ssh" / "id_ed25519",
                       self.home / "devel" / "innocent")
        self.refused(_issue(body="File: devel/innocent\n"), ".ssh",
                     setup=setup, read=True)

    def test_symlink_out_of_home(self):
        def setup():
            outside = Path(self._tmp.name) / "outside"
            outside.write_bytes(VALUE)
            os.symlink(outside, self.home / "devel" / "escape")
        self.refused(_issue(body="File: devel/escape\n"), "outside",
                     setup=setup, read=True)

    def test_empty_file(self):
        def setup():
            (self.home / "devel" / "empty").write_bytes(b"")
        self.refused(_issue(body="File: devel/empty\n"), "empty",
                     setup=setup, read=True)

    def test_missing_file(self):
        self.refused(_issue(body="File: devel/nope\n"), "no such file", read=True)

    def test_a_directory(self):
        self.refused(_issue(body="File: devel/fohmixer\n"), "regular file", read=True)


# --------------------------------------------------------------------------- #
# failures: loud, rc 1, the issue stays OPEN for the next run
# --------------------------------------------------------------------------- #
class TestFailures(_Base):

    def test_ssh_failure_leaves_the_issue_open(self):
        world = FakeWorld(self.home, [_issue()], ssh_rc=255)
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", err)
        self.assertEqual(world.of("gh", "secret", "set"), [])
        self.assertEqual(world.closed, [])

    def test_gh_secret_set_failure_is_scrubbed_and_leaves_it_open(self):
        world = FakeWorld(self.home, [_issue()], set_rc=1,
                          set_stderr=b"HTTP 403: " + VALUE)
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", err)
        self.assertIn("DENYLIST", err)
        self.assertEqual(world.closed, [])
        self.assertNoLeak(world, out, err)

    def test_issue_list_failure_is_loud(self):
        world = FakeWorld(self.home, [], list_rc=1)
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", err)

    def test_an_issue_from_another_repo_in_the_listing_is_never_touched(self):
        world = FakeWorld(self.home, [_issue(
            url="https://github.com/zbynekdrlik/other/issues/7")])
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 1)
        self.assertEqual(world.closed, [])
        self.assertEqual(world.of("ssh"), [])

    def test_one_bad_request_never_skips_the_next(self):
        world = FakeWorld(self.home, [_issue(number=7, body="File: devel/nope\n"),
                                      _issue(number=8)])
        rc, out, err = self.sync(world)
        self.assertEqual(rc, 0, err)
        self.assertEqual(world.secrets.get("DENYLIST"), VALUE)
        self.assertEqual(len(world.closed), 2)

    def test_an_exception_never_carries_the_value(self):
        def boom(argv, input=None, **kw):
            if argv[:3] == ["gh", "secret", "set"]:
                raise subprocess.TimeoutExpired(argv, 60, output=input)
            return world(argv, input=input, **kw)
        world = FakeWorld(self.home, [_issue()])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = _sync().sync_account("fohmixer", run=boom, now=NOW)
        self.assertEqual(rc, 1)
        self.assertIn("TimeoutExpired", err.getvalue())
        self.assertNoLeak(world, out.getvalue(), err.getvalue())

    def test_an_undeclared_account_is_refused(self):
        world = FakeWorld(self.home, [_issue()])
        rc, out, err = self.sync(world, account="claudy")
        self.assertEqual(rc, 1)
        self.assertIn("github_app", err)
        self.assertEqual(world.calls, [])


# --------------------------------------------------------------------------- #
# wiring: the existing mint --all timer run, never a new unit
# --------------------------------------------------------------------------- #
class TestWiring(unittest.TestCase):

    def ns(self, **kw):
        base = dict(action="mint", account=None, all=True, dry_run=False, key=None)
        base.update(kw)
        return mock.Mock(**base)

    def test_mint_all_runs_the_sync_after_every_mint(self):
        order = []
        with mock.patch.object(pgt, "mint_account",
                               side_effect=lambda a, **k: order.append("mint") or 0), \
                mock.patch.object(_sync(), "sync_all",
                                  side_effect=lambda **k: order.append("sync") or 0) as s:
            rc = pgt.cmd_project_gh_token(self.ns())
        self.assertEqual(rc, 0)
        self.assertEqual(order, ["mint", "sync"])
        self.assertEqual(s.call_args.kwargs.get("dry_run"), False)

    def test_a_sync_crash_is_loud_but_never_breaks_the_mint(self):
        err = io.StringIO()
        with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
                mock.patch.object(_sync(), "sync_all",
                                  side_effect=RuntimeError("x")), \
                redirect_stderr(err):
            rc = pgt.cmd_project_gh_token(self.ns())
        self.assertEqual(m.call_count, 1)
        self.assertEqual(rc, 1)
        self.assertIn("secret-sync", err.getvalue())

    def test_a_single_account_mint_does_not_sync(self):
        with mock.patch.object(pgt, "mint_account", return_value=0), \
                mock.patch.object(_sync(), "sync_all") as s:
            pgt.cmd_project_gh_token(self.ns(account="fohmixer", all=False))
        s.assert_not_called()

    def test_sync_secrets_action(self):
        with mock.patch.object(_sync(), "sync_account", return_value=0) as s:
            rc = pgt.cmd_project_gh_token(self.ns(action="sync-secrets",
                                                  account="fohmixer", all=False,
                                                  dry_run=True))
        self.assertEqual(rc, 0)
        self.assertEqual(s.call_args.args[0], "fohmixer")
        self.assertIs(s.call_args.kwargs.get("dry_run"), True)
        with mock.patch.object(_sync(), "sync_all", return_value=0) as s:
            self.assertEqual(pgt.cmd_project_gh_token(
                self.ns(action="sync-secrets")), 0)
        s.assert_called_once()

    def test_the_service_unit_is_unchanged(self):
        self.assertIn("project-gh-token mint --all", pgt._SERVICE)

    def test_sync_all_under_pytest_never_runs_real_gh(self):
        with mock.patch("subprocess.run") as real:
            rc = _sync().sync_all()
        self.assertEqual(rc, 0)
        real.assert_not_called()

    def test_sync_all_covers_the_github_app_accounts(self):
        with mock.patch.object(_sync(), "sync_account", return_value=0) as s:
            _sync().sync_all(run=lambda *a, **k: None)
        self.assertEqual([c.args[0] for c in s.call_args_list], ["fohmixer"])


# --------------------------------------------------------------------------- #
# the how-to a project session reads
# --------------------------------------------------------------------------- #
class TestHowto(unittest.TestCase):

    def test_bootstrap_next_steps_explain_the_request(self):
        spec = bootstrap.account_spec("fohmixer")
        steps = pgt.render_next_steps(spec)
        for needle in ("secret-sync:DENYLIST", "File:", "gh issue create",
                       "gh label create", REPO):
            self.assertIn(needle, steps)

    def test_no_howto_without_repo_secrets(self):
        spec = dict(bootstrap.account_spec("fohmixer"))
        spec.pop("repo_secrets", None)
        self.assertNotIn("secret-sync", pgt.render_next_steps(spec))

    def test_rendered_next_steps_are_valid_bash(self):
        spec = bootstrap.account_spec("fohmixer")
        r = subprocess.run(["bash", "-n"], input=pgt.render_next_steps(spec),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
