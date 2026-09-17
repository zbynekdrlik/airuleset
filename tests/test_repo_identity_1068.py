"""#1068 — repo_identity resolves the GitHub repo NAME from the tickets-status
cache / git remote, NEVER the checkout directory basename, so watchdog Job 8's
bounce backstop (and the sibling cross-stream predicates) treat a client-named
odoo-erp checkout (montalu1: /home/montalu1/devel/odoo/odoo-slovnormal) as
cross-stream.

The bug: `_repo_in_cross_stream_flow` keyed membership on os.path.basename(root)
against {"odoo-erp"}, so every stream box whose checkout is client-named skipped
the ONE repo that is cross-stream (montalu1 96/96 sweeps, 2026-09-17). These
tests are hermetic: a fake `git_remote` runner stands in for the git read, and
the tickets-status cache is passed as a plain dict — no live tmux / gh / git.
"""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import subprocess_budget as sb


def _remote(url):
    """A `subprocess.run`-compatible fake for the `git config --get
    remote.origin.url` read: a CompletedProcess-like object carrying `url` on
    stdout (rc 0), or rc 1 + empty stdout when `url is None` (a checkout with no
    origin remote). Records every argv it is called with on `.calls`."""
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if url is None:
            return types.SimpleNamespace(returncode=1, stdout="")
        return types.SimpleNamespace(returncode=0, stdout=url + "\n")

    run.calls = calls
    return run


class RepoIdentity(unittest.TestCase):
    def setUp(self):
        sb.reset_subprocess_stats()
        sb.end_sweep_memo()                 # start with the per-sweep memo off
        self.addCleanup(sb.end_sweep_memo)
        self.addCleanup(sb.reset_subprocess_stats)

    # (a) cache hit — the slug the cache already knows, no subprocess
    def test_cache_resolves_slug_no_subprocess(self):
        root = "/home/montalu1/devel/odoo/odoo-slovnormal"
        got = wd.repo_identity(root, {root: "odoo-erp"},
                               git_remote=_remote(None))
        self.assertEqual(got, "odoo-erp")
        self.assertEqual(sb.subprocess_stats()["n"], 0)   # never shelled out

    def test_cache_value_owner_repo_is_reduced_to_repo_name(self):
        # a cache that ever stored owner/repo is still reduced to the repo name
        root = "/home/u/devel/odoo/odoo-slovnormal"
        self.assertEqual(
            wd.repo_identity(root, {root: "zbynekdrlik/odoo-erp"}), "odoo-erp")

    # (b) no cache entry → git remote, exactly ONE counted subprocess
    def test_git_remote_ssh_resolves_slug_one_subprocess(self):
        root = "/home/montalu1/devel/odoo/odoo-slovnormal"
        run = _remote("git@github.com:zbynekdrlik/odoo-erp.git")
        got = wd.repo_identity(root, {}, git_remote=run)
        self.assertEqual(got, "odoo-erp")
        self.assertEqual(sb.subprocess_stats()["n"], 1)   # one counted child
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(run.calls[0],
                         ["git", "-C", root, "config", "--get",
                          "remote.origin.url"])

    def test_git_remote_https_resolves_slug(self):
        got = wd.repo_identity(
            "/home/u/devel/x", {},
            git_remote=_remote("https://github.com/zbynekdrlik/odoo-erp"))
        self.assertEqual(got, "odoo-erp")

    def test_client_named_checkout_is_in_flow_via_repo_identity(self):
        # the exact live montalu1 case, end to end through the predicate
        root = "/home/montalu1/devel/odoo/odoo-slovnormal"
        slug = wd.repo_identity(root, {root: "odoo-erp"})
        self.assertTrue(wd._repo_in_cross_stream_flow(root, slug=slug))

    # (c) a genuinely different repo (odoo) → resolved, but not in flow
    def test_non_cross_stream_slug_not_in_flow(self):
        root = "/home/u/devel/odoo"
        slug = wd.repo_identity(root, {root: "odoo"})
        self.assertEqual(slug, "odoo")
        self.assertFalse(wd._repo_in_cross_stream_flow(root, slug=slug))

    # (d) unknown identity → None, not in flow, one journal line
    def test_unknown_identity_is_none_and_logs(self):
        root = "/home/u/devel/mystery"
        logs = []
        slug = wd.repo_identity(root, {}, git_remote=_remote(None), logs=logs)
        self.assertIsNone(slug)
        self.assertFalse(wd._repo_in_cross_stream_flow(root, slug=slug))
        self.assertIn("repo-identity: unknown for %s" % root, logs)

    def test_non_github_remote_is_unknown(self):
        got = wd.repo_identity(
            "/home/u/devel/x", {},
            git_remote=_remote("https://gitlab.com/foo/bar.git"))
        self.assertIsNone(got)

    def test_git_read_raising_is_unknown_not_crash(self):
        def boom(argv, **kw):
            raise OSError("git not found")
        got = wd.repo_identity("/home/u/devel/x", {}, git_remote=boom)
        self.assertIsNone(got)

    # per-sweep memo: the git read happens ONCE across repeated calls in a sweep
    def test_git_remote_is_memoized_within_a_sweep(self):
        root = "/home/montalu1/devel/odoo/odoo-slovnormal"
        run = _remote("git@github.com:zbynekdrlik/odoo-erp.git")
        sb.begin_sweep_memo()
        a = wd.repo_identity(root, {}, git_remote=run)
        b = wd.repo_identity(root, {}, git_remote=run)
        self.assertEqual((a, b), ("odoo-erp", "odoo-erp"))
        self.assertEqual(len(run.calls), 1)               # collapsed by the memo
        self.assertEqual(sb.subprocess_stats()["n"], 1)

    def test_outside_a_sweep_no_memo_caching(self):
        # memo inactive (default) → every call reads afresh, byte-identical to
        # the pre-#1055 world (subprocess_budget's own contract)
        root = "/home/u/devel/x"
        run = _remote("git@github.com:zbynekdrlik/odoo-erp.git")
        wd.repo_identity(root, {}, git_remote=run)
        wd.repo_identity(root, {}, git_remote=run)
        self.assertEqual(len(run.calls), 2)

    def test_empty_root_is_none(self):
        self.assertIsNone(wd.repo_identity("", {}))
        self.assertIsNone(wd.repo_identity(None, {}))

    def test_trailing_slash_root_matches_cache(self):
        root = "/home/u/devel/odoo/odoo-slovnormal"
        self.assertEqual(
            wd.repo_identity(root + "/", {root: "odoo-erp"}), "odoo-erp")

    def test_no_cache_map_falls_back_to_git(self):
        # cache_roots=None (the default) → straight to the git remote
        got = wd.repo_identity(
            "/home/u/devel/x", None,
            git_remote=_remote("git@github.com:zbynekdrlik/odoo-erp.git"))
        self.assertEqual(got, "odoo-erp")


if __name__ == "__main__":
    unittest.main()
