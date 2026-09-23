"""#1115 slice F — every manual live-Cloudflare-write entry refuses a worktree,
and the #972 worktree predicate detects ANY linked worktree (a `.git` FILE),
not only a `.claude/worktrees/` path segment.

ABSOLUTE RULE (the incident this slice extends, comment 5787949642): NO real
Cloudflare API call, NO write path against real clients, NO ~/.secrets read.
Every test here uses injected FAKE clients / an injected is_worktree_fn seam /
patched token loaders that BLOW UP if reached — so a token read is a test
failure, proving the guard fires before any client build.

Two guards (Approach 1 (a)+(b) of design comment 5788357748):
  (a) cmd_drop_gateway --apply, cmd_webterm_access --apply, and
      cli_cloudflare_dns.ensure_managed_records(dry_run=False) each call the ONE
      shared refusal helper slice E added (re-used, moved to the pure leaf
      cli_drop_lanes and re-exported) BEFORE building any client: a loud line
      naming #1115/#972, zero API calls, a non-zero/falsy result. Dry-run/read
      paths are never refused.
  (b) airuleset._is_worktree_repo_dir also returns True when the repo toplevel's
      `.git` is a FILE (a git linked-worktree gitdir: pointer), so an out-of-tree
      `git worktree add /tmp/x` is caught too. The `.claude/worktrees/` segment
      rule is preserved.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset                          # noqa: E402
import cli_cloudflare_dns as dns          # noqa: E402
import cli_drop_gateway as dg             # noqa: E402
import cli_drop_golive as gl              # noqa: E402
import cli_drop_lanes as dl               # noqa: E402
import cli_webterm_access as acc          # noqa: E402
from test_drop_golive_1115 import FakeDnsTransport  # noqa: E402


def _args(**kw):
    return types.SimpleNamespace(**kw)


# --------------------------------------------------------------------------- #
# (a) shared refusal helper lives in the leaf and is re-exported (ONE object)
# --------------------------------------------------------------------------- #

class TestSharedRefusalHelperIsOneObject(unittest.TestCase):
    """The predicate + refusal helper are ONE definition in the pure leaf
    cli_drop_lanes, re-used (never copied) by cli_drop_golive (re-export) and by
    the three write entries."""

    def test_predicate_shared_identity(self):
        self.assertIs(gl._repo_is_worktree_checkout, dl._repo_is_worktree_checkout)

    def test_refusal_helper_shared_identity(self):
        self.assertIs(gl._refuse_worktree_live_write, dl._refuse_worktree_live_write)


# --------------------------------------------------------------------------- #
# (a) cmd_drop_gateway --apply refuses in a worktree
# --------------------------------------------------------------------------- #

class TestDropGatewayApplyRefusesInWorktree(unittest.TestCase):
    def test_apply_in_worktree_refuses_zero_writes(self):
        # A worktree --apply must refuse before any Access reconcile / marker
        # write. Patch both to blow up: if the guard fails to fire, the test
        # fails LOUD instead of silently writing.
        out = io.StringIO()
        with mock.patch.object(dg, "_reconcile_access",
                               side_effect=AssertionError("access reconciled")), \
             mock.patch.object(dg, "write_drop_marker",
                               side_effect=AssertionError("marker written")):
            with contextlib.redirect_stdout(out):
                rc = dg.cmd_drop_gateway(_args(
                    apply=True, _nodename="dev1", _username="newlevel",
                    _is_worktree_fn=lambda: True))
        self.assertEqual(rc, 1)
        text = out.getvalue()
        self.assertIn("REFUSING", text)
        self.assertIn("#1115", text)
        self.assertIn("#972", text)

    def test_dry_run_in_worktree_not_refused(self):
        # A dry-run makes no live write, so the guard must NOT fire even in a
        # worktree (the CI/test harness runs dry-runs from any checkout).
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = dg.cmd_drop_gateway(_args(
                apply=False, _nodename="dev1", _username="newlevel",
                _is_worktree_fn=lambda: True))
        self.assertEqual(rc, 0)
        self.assertNotIn("REFUSING", out.getvalue())


# --------------------------------------------------------------------------- #
# (a) cmd_webterm_access --apply refuses in a worktree
# --------------------------------------------------------------------------- #

class TestWebtermAccessApplyRefusesInWorktree(unittest.TestCase):
    def test_apply_in_worktree_refuses_before_token_read(self):
        # In a worktree, --apply must refuse BEFORE _load_token() — patch it to
        # blow up so any token read is a test failure (zero ~/.secrets read).
        out = io.StringIO()
        with mock.patch.object(acc, "_load_token",
                               side_effect=AssertionError("token was read")):
            with contextlib.redirect_stdout(out):
                rc = acc.cmd_webterm_access(_args(
                    apply=True, dry_run=False, profile=None,
                    _is_worktree_fn=lambda: True))
        self.assertEqual(rc, 1)
        text = out.getvalue()
        self.assertIn("REFUSING", text)
        self.assertIn("#1115", text)
        self.assertIn("#972", text)

    def test_dry_run_in_worktree_reaches_token_read_not_refused(self):
        # A dry-run still READS (GET /apps) to report create-vs-update, so the
        # guard must NOT fire — the code must get PAST the guard to the token
        # read. Patch _load_token to raise OSError so we observe the token path
        # was reached (a "cannot read token" line), NOT a refusal.
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(acc, "_load_token",
                               side_effect=OSError("sentinel-token-path")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = acc.cmd_webterm_access(_args(
                    apply=False, dry_run=True, profile=None,
                    _is_worktree_fn=lambda: True))
        self.assertEqual(rc, 1)
        self.assertNotIn("REFUSING", out.getvalue() + err.getvalue())
        self.assertIn("cannot read token", err.getvalue())


# --------------------------------------------------------------------------- #
# (a) cli_cloudflare_dns.ensure_managed_records refuses a live write in worktree
# --------------------------------------------------------------------------- #

class TestEnsureManagedRecordsRefusesInWorktree(unittest.TestCase):
    def test_live_write_in_worktree_refuses_before_token_read(self):
        # dry_run=False + client=None would build a real DnsClient. In a worktree
        # it must refuse BEFORE _load_token(). Patch it to blow up.
        with mock.patch.object(dns, "_load_token",
                               side_effect=AssertionError("token was read")):
            all_ok, results = dns.ensure_managed_records(
                dry_run=False, is_worktree_fn=lambda: True)
        self.assertFalse(all_ok)
        joined = " ".join((r.get("error") or "") for r in results)
        self.assertIn("worktree", joined)
        self.assertIn("#1115", joined)
        self.assertIn("#972", joined)

    def test_injected_client_not_guarded(self):
        # An INJECTED (fake) client makes no live write, so the worktree guard is
        # scoped to the real-client build and must NOT fire.
        t = FakeDnsTransport(records=[])
        fake = dns.DnsClient(token="dns-tok", transport=t)
        all_ok, results = dns.ensure_managed_records(
            dry_run=False, client=fake, is_worktree_fn=lambda: True)
        # the fake was exercised (records created), never refused
        self.assertTrue(any(r.get("action") for r in results))
        self.assertNotIn("worktree", " ".join(
            (r.get("error") or "") for r in results))

    def test_dry_run_in_worktree_not_refused(self):
        # dry_run=True builds a client for GETs only; the guard must not fire.
        # Patch _load_token to raise OSError so we prove the token path was
        # reached (not a worktree refusal).
        with mock.patch.object(dns, "_load_token",
                               side_effect=OSError("sentinel")):
            all_ok, results = dns.ensure_managed_records(
                dry_run=True, is_worktree_fn=lambda: True)
        self.assertFalse(all_ok)
        joined = " ".join((r.get("error") or "") for r in results)
        self.assertNotIn("worktree", joined)
        self.assertIn("token", joined)


# --------------------------------------------------------------------------- #
# (b) _is_worktree_repo_dir detects a .git FILE (linked worktree), any location
# --------------------------------------------------------------------------- #

class TestIsWorktreeRepoDirDotGitFile(unittest.TestCase):
    def test_dotgit_file_is_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").write_text(
                "gitdir: /somewhere/.git/worktrees/x\n", encoding="utf-8")
            self.assertTrue(airuleset._is_worktree_repo_dir(d))

    def test_dotgit_dir_is_not_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").mkdir()
            self.assertFalse(airuleset._is_worktree_repo_dir(d))

    def test_no_git_anywhere_is_not_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(airuleset._is_worktree_repo_dir(d))

    def test_walk_up_finds_ancestor_dotgit_file(self):
        # repo_dir is a SUBDIR; the .git FILE is on an ancestor (the toplevel).
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").write_text("gitdir: /x\n", encoding="utf-8")
            sub = Path(d) / "a" / "b"
            sub.mkdir(parents=True)
            self.assertTrue(airuleset._is_worktree_repo_dir(sub))

    def test_walk_up_ancestor_dotgit_dir_is_not_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").mkdir()
            sub = Path(d) / "a" / "b"
            sub.mkdir(parents=True)
            self.assertFalse(airuleset._is_worktree_repo_dir(sub))

    def test_segment_rule_preserved(self):
        # The pre-existing .claude/worktrees/ segment rule still fires (synthetic
        # path, no filesystem access needed / possible).
        p = Path("/home/user/devel/airuleset/.claude/worktrees/agent-abc")
        self.assertTrue(airuleset._is_worktree_repo_dir(p))

    def test_normal_synthetic_path_not_worktree(self):
        # A synthetic path with no .git on disk anywhere in its ancestry.
        p = Path("/home/user/devel/airuleset")
        self.assertFalse(airuleset._is_worktree_repo_dir(p))

    def test_real_git_worktree_add_is_detected(self):
        # Acceptance: a REAL `git worktree add` produces a .git FILE and must be
        # detected True; the main repo (a .git DIRECTORY) must be False.
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            env = dict(os.environ,
                       GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

            def _git(*a):
                subprocess.run(["git", *a], cwd=str(repo), env=env,
                               check=True, capture_output=True)

            _git("init", "-q")
            _git("commit", "-q", "--allow-empty", "-m", "init")
            wt = Path(d) / "wt"
            subprocess.run(
                ["git", "worktree", "add", "-q", str(wt)],
                cwd=str(repo), env=env, check=True, capture_output=True)
            self.assertTrue((wt / ".git").is_file())          # sanity: linked wt
            self.assertTrue(airuleset._is_worktree_repo_dir(wt))
            self.assertFalse(airuleset._is_worktree_repo_dir(repo))


if __name__ == "__main__":
    unittest.main()
