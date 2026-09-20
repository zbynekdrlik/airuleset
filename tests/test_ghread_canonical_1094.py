"""#1094 — the open-issue snapshot must read the CANONICAL repo, never a fork;
an issues-disabled snapshot is never authoritative.

`gates.ghread` gains:
  * `canonical_slug(cwd)` — one shared, fork-aware resolver: the remote marked
    `gh-resolved = base` (what `gh repo set-default` writes) wins; else an
    `upstream` remote; else `origin`. LOCAL git only, no network. `resolve_slug`
    stays as a back-compat alias.
  * `list_open_issues_cached` gains an AUTHORITY check: it reads `GET /repos/
    <slug>` through the ETag cache and, when the repo is a fork OR has issues
    disabled, redoes the listing against `parent.full_name`; a repo that still
    has issues disabled (no issue-hosting parent) returns
    `(None, "gate-unavailable: issues disabled on <slug>")` — an issues-disabled
    repo can NEVER produce an authoritative empty snapshot.

Hermetic: a fake gh/git runner returns (rc, stdout, stderr) per argv; the ETag
cache is redirected to a tmp dir via AIRULESET_GH_ETAG_DIR. No network, no gh.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import ghread


def _inc(status, etag=None, body="", reason="OK"):
    """A `gh api --include` raw stdout: status line + headers + blank + body."""
    lines = ["HTTP/2.0 %d %s" % (status, reason),
             "Content-Type: application/json; charset=utf-8"]
    if etag is not None:
        lines.append("Etag: %s" % etag)
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def _issue(n, labels=None, pr=False):
    it = {"number": n, "title": "issue %d" % n,
          "created_at": "2026-09-01T00:00:00Z",
          "updated_at": "2026-09-02T00:00:00Z",
          "labels": [{"name": nm} for nm in (labels or [])],
          "assignees": [], "user": {"login": "someone"}}
    if pr:
        it["pull_request"] = {"url": "https://x/pulls/%d" % n}
    return it


class CanonicalSlug(unittest.TestCase):
    """`canonical_slug` resolves the way `gh` does, with LOCAL git only."""

    def _runner(self, gh_resolved=None, upstream=None, origin=None):
        """Fake git runner. `gh_resolved` is the `--get-regexp` stdout (or None
        -> rc1); `upstream`/`origin` are remote get-url stdouts (or None -> rc1)."""
        def runner(argv):
            joined = " ".join(argv)
            if "get-regexp" in joined:
                return (0, gh_resolved, "") if gh_resolved else (1, "", "")
            if "get-url" in joined and argv[-1] == "upstream":
                return (0, upstream, "") if upstream else (1, "", "no upstream")
            if "get-url" in joined and argv[-1] == "origin":
                return (0, origin, "") if origin else (1, "", "no origin")
            return (1, "", "unexpected %s" % joined)
        return runner

    def test_gh_resolved_base_marker_wins(self):
        # `gh repo set-default upstream` writes `remote.upstream.gh-resolved base`.
        r = self._runner(
            gh_resolved="remote.upstream.gh-resolved base",
            upstream="git@github.com:canon/erp.git",
            origin="git@github.com:fork/erp.git")
        self.assertEqual(ghread.canonical_slug("/repo", runner=r), "canon/erp")

    def test_upstream_wins_when_no_gh_resolved_marker(self):
        r = self._runner(gh_resolved=None,
                         upstream="https://github.com/canon/erp",
                         origin="https://github.com/fork/erp")
        self.assertEqual(ghread.canonical_slug("/repo", runner=r), "canon/erp")

    def test_origin_when_no_upstream(self):
        r = self._runner(gh_resolved=None, upstream=None,
                         origin="git@github.com:owner/name.git")
        self.assertEqual(ghread.canonical_slug("/repo", runner=r), "owner/name")

    def test_none_when_no_remotes(self):
        r = self._runner(gh_resolved=None, upstream=None, origin=None)
        self.assertIsNone(ghread.canonical_slug("/repo", runner=r))

    def test_gh_resolved_owner_repo_value_is_used(self):
        # gh writes `remote.<name>.gh-resolved = owner/repo` when the base repo is
        # NOT a local remote (a fork whose parent has no remote); that value IS
        # gh's canonical answer and must win over origin (#1094 review — otherwise
        # the fork slug leaks to cli_release_state, which has no authority check).
        r = self._runner(
            gh_resolved="remote.origin.gh-resolved zbynekdrlik/odoo-erp",
            upstream=None,
            origin="git@github.com:kvaskodev/odoo-erp.git")
        self.assertEqual(ghread.canonical_slug("/repo", runner=r),
                         "zbynekdrlik/odoo-erp")

    def test_gh_resolved_host_owner_repo_value_is_used(self):
        # gh may record the host too: `remote.origin.gh-resolved =
        # github.com/owner/repo` -> the last two path segments are the slug.
        r = self._runner(
            gh_resolved="remote.origin.gh-resolved github.com/zbynekdrlik/odoo-erp",
            origin="git@github.com:kvaskodev/odoo-erp.git")
        self.assertEqual(ghread.canonical_slug("/repo", runner=r),
                         "zbynekdrlik/odoo-erp")

    def test_resolve_slug_is_backcompat_alias(self):
        # the old name resolves canonically now (a fork with upstream -> upstream)
        r = self._runner(gh_resolved=None,
                         upstream="git@github.com:canon/erp.git",
                         origin="git@github.com:fork/erp.git")
        self.assertEqual(ghread.resolve_slug("/repo", runner=r), "canon/erp")


class _EtagTmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.mkdtemp(prefix="ghcanon-1094-")
        self._prev = os.environ.get("AIRULESET_GH_ETAG_DIR")
        os.environ["AIRULESET_GH_ETAG_DIR"] = self._d

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AIRULESET_GH_ETAG_DIR", None)
        else:
            os.environ["AIRULESET_GH_ETAG_DIR"] = self._prev


class ListOpenIssuesAuthority(_EtagTmp):
    def test_fork_snapshot_redone_against_parent(self):
        seen = []

        def runner(argv):
            url = argv[-1]
            seen.append(url)
            if url == "repos/fork/erp":
                return 0, _inc(200, etag='W/"m1"', body=json.dumps(
                    {"fork": True, "has_issues": False,
                     "parent": {"full_name": "canon/erp"}})), ""
            if url == "repos/canon/erp":
                return 0, _inc(200, etag='W/"m2"', body=json.dumps(
                    {"fork": False, "has_issues": True})), ""
            if url.startswith("repos/canon/erp/issues"):
                return 0, _inc(200, etag='W/"p"',
                               body=json.dumps([_issue(105)])), ""
            if url.startswith("repos/fork/erp/issues"):
                return 0, _inc(200, etag='W/"f"', body=json.dumps([])), ""
            return 1, "", "unexpected %s" % url

        rows, err = ghread.list_open_issues_cached("fork/erp", runner=runner)
        self.assertIsNone(err)
        self.assertEqual({r["number"] for r in rows}, {105},
                         "rows must come from the parent, not the empty fork")
        self.assertFalse([u for u in seen if u.startswith("repos/fork/erp/issues")],
                         "the fork's issues endpoint must NEVER be listed: %r" % seen)

    def test_issues_disabled_no_parent_is_gate_unavailable(self):
        def runner(argv):
            url = argv[-1]
            if url == "repos/o/r":
                return 0, _inc(200, body=json.dumps(
                    {"fork": False, "has_issues": False, "parent": None})), ""
            return 1, "", "the issues endpoint must not be read: %s" % url

        rows, err = ghread.list_open_issues_cached("o/r", runner=runner)
        self.assertIsNone(rows, "an issues-disabled repo is never authoritative")
        self.assertTrue(err.startswith(ghread.GATE_UNAVAILABLE_PREFIX))
        self.assertIn("issues disabled", err)

    def test_fork_whose_parent_also_has_issues_disabled_is_gate_unavailable(self):
        def runner(argv):
            url = argv[-1]
            if url == "repos/fork/erp":
                return 0, _inc(200, body=json.dumps(
                    {"fork": True, "has_issues": False,
                     "parent": {"full_name": "canon/erp"}})), ""
            if url == "repos/canon/erp":
                return 0, _inc(200, body=json.dumps(
                    {"fork": False, "has_issues": False})), ""
            # the fork's disabled issues endpoint answers 200 [] (the real bug):
            # without the authority check this empty listing is wrongly accepted.
            if url.startswith("repos/fork/erp/issues"):
                return 0, _inc(200, etag='W/"f"', body=json.dumps([])), ""
            return 1, "", "unexpected %s" % url

        rows, err = ghread.list_open_issues_cached("fork/erp", runner=runner)
        self.assertIsNone(rows,
                          "a fork whose parent also disables issues is never "
                          "authoritative — the empty 200 [] must be rejected")
        self.assertTrue(err.startswith(ghread.GATE_UNAVAILABLE_PREFIX))

    def test_canonical_repo_lists_normally(self):
        def runner(argv):
            url = argv[-1]
            if url == "repos/o/r":
                return 0, _inc(200, etag='W/"m"', body=json.dumps(
                    {"fork": False, "has_issues": True})), ""
            if url.startswith("repos/o/r/issues"):
                return 0, _inc(200, etag='W/"p"', body=json.dumps(
                    [_issue(1), _issue(2, pr=True), _issue(3)])), ""
            return 1, "", "unexpected %s" % url

        rows, err = ghread.list_open_issues_cached("o/r", runner=runner)
        self.assertIsNone(err)
        self.assertEqual({r["number"] for r in rows}, {1, 3})  # PR #2 dropped

    def test_unreadable_meta_fails_open_to_the_given_slug(self):
        # a meta read hiccup must NOT degrade a working listing (fail-open).
        def runner(argv):
            url = argv[-1]
            if url == "repos/o/r":
                return 1, "", "gh: transient meta error"     # both paths fail
            if url.startswith("repos/o/r/issues"):
                return 0, _inc(200, etag='W/"p"',
                               body=json.dumps([_issue(9)])), ""
            return 1, "", "unexpected %s" % url

        rows, err = ghread.list_open_issues_cached("o/r", runner=runner)
        self.assertIsNone(err)
        self.assertEqual({r["number"] for r in rows}, {9})


class DavidShapeEndToEnd(_EtagTmp):
    """The full david1 chain: a fork clone (origin = the fork, issues disabled,
    NO upstream remote) — `canonical_slug` returns the fork, then the authority
    check in `list_open_issues_cached` redirects to the parent and the footer's
    rows come from the parent, never the empty fork."""

    def test_fork_clone_counts_come_from_parent(self):
        seen = []

        def runner(argv):
            joined = " ".join(argv)
            # canonical_slug local git reads: no gh-resolved, no upstream remote
            if "get-regexp" in joined:
                return 1, "", ""
            if "get-url" in joined and argv[-1] == "upstream":
                return 1, "", "no such remote 'upstream'"
            if "get-url" in joined and argv[-1] == "origin":
                return 0, "git@github.com:kvaskodev/odoo-erp.git", ""
            # gh api reads:
            url = argv[-1]
            seen.append(url)
            if url == "repos/kvaskodev/odoo-erp":
                return 0, _inc(200, etag='W/"m1"', body=json.dumps(
                    {"fork": True, "has_issues": False,
                     "parent": {"full_name": "zbynekdrlik/odoo-erp"}})), ""
            if url == "repos/zbynekdrlik/odoo-erp":
                return 0, _inc(200, etag='W/"m2"', body=json.dumps(
                    {"fork": False, "has_issues": True})), ""
            if url.startswith("repos/kvaskodev/odoo-erp/issues"):
                return 0, _inc(200, etag='W/"f"', body=json.dumps([])), ""
            if url.startswith("repos/zbynekdrlik/odoo-erp/issues"):
                return 0, _inc(200, etag='W/"p"', body=json.dumps(
                    [_issue(1, labels=["stream:david1"]),
                     _issue(2, labels=["needs-gatekeeper"])])), ""
            return 1, "", "unexpected %s" % url

        slug = ghread.canonical_slug("/repo", runner=runner)
        self.assertEqual(slug, "kvaskodev/odoo-erp")   # fork, no upstream remote
        rows, err = ghread.list_open_issues_cached(slug, runner=runner)
        self.assertIsNone(err)
        self.assertEqual({r["number"] for r in rows}, {1, 2},
                         "the fork clone's footer rows come from the parent")
        self.assertFalse(
            [u for u in seen if u.startswith("repos/kvaskodev/odoo-erp/issues")],
            "the disabled fork's empty issues list must never be accepted")


if __name__ == "__main__":
    unittest.main()
