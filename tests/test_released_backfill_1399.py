"""#1399 (owner 26.11.2026, "dokolečka sa pýtam prečo je tak vysoké I v d3"): after
the david3 token got `pull_requests:read` the footer still showed `I 70` with
`C 0`, although 40 of its 54 slice tickets had a merged PR on main.

1. The PR cache is filled only from PRs seen in `main..develop|staging`. Every PR
   that went develop -> staging -> main while the token could not read PRs is
   outside that range for good, so its ticket never reached `C`.
2. `merged_released_oids` tested ancestry against `origin/main`; on a fork clone
   that is the fork's stale branch, the release lands on `upstream/main`.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_release_state as rs  # noqa: E402

_GENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, env=_GENV,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo, subject):
    _git(repo, "commit", "--allow-empty", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _released_fork(repo):
    """A fork clone: origin = the fork (stale main), upstream = canonical. PR #101
    (ticket 55) is already released: develop, staging and main all hold it."""
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True,
                   env=_GENV, capture_output=True)
    _git(repo, "remote", "add", "origin", "git@github-fork:fork/r.git")
    _git(repo, "remote", "add", "upstream", "git@github-upstream:o/r.git")
    _commit(repo, "chore: baseline")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")      # stale fork main
    fix = _commit(repo, "Merge pull request #101 from dev/feature")
    _commit(repo, "Merge pull request #102 from o/staging")             # the release PR
    for br in ("main", "develop", "staging"):
        _git(repo, "update-ref", "refs/remotes/upstream/%s" % br, "HEAD")
    return fix


META = {101: ("feat(#55): scanner adds the item", ""),
        102: ("Release 19.0.2.652.0 (staging -> main)", "")}


class ReleasedBeforeObserved(unittest.TestCase):

    def setUp(self):
        rs._reset_memo()

    def test_a_released_unobserved_pr_reaches_c(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            fix = _released_fork(repo)
            cache = str(Path(d) / "pr-issues.json")
            m = rs.merged_unreleased_issues(repo, pr_meta_fn=META.get,
                                            cache_path=cache, slug="o/r")
            self.assertEqual(set(m), set(), "released work is not M")
            saved = json.loads(Path(cache).read_text())
            self.assertEqual(saved["101"]["issues"], [55])
            self.assertEqual(saved["101"]["oid"], fix)
            got = rs.merged_released_oids(repo, {55}, cache_path=cache, slug="o/r")
        self.assertEqual(got, {55: [fix]})

    def test_backfill_spends_only_the_meta_budget(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            _released_fork(repo)
            for n in range(200, 200 + rs.MERGED_UNRELEASED_META_BUDGET + 5):
                _commit(repo, "Merge pull request #%d from dev/x" % n)
            for br in ("main", "develop", "staging"):
                _git(repo, "update-ref", "refs/remotes/upstream/%s" % br, "HEAD")
            asked = []
            rs.merged_unreleased_issues(
                repo, pr_meta_fn=lambda pr: asked.append(pr) or ("chore: x", ""),
                cache_path=str(Path(d) / "c.json"), slug="o/r")
        self.assertEqual(len(asked), rs.MERGED_UNRELEASED_META_BUDGET)
        self.assertEqual(asked[0], 200 + rs.MERGED_UNRELEASED_META_BUDGET + 4,
                         "newest released PR first")

    def test_an_unreadable_pr_is_not_asked_again(self):
        # review: a `(#N)` that is no PR (pulls/N 404) must not eat the budget forever
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            _released_fork(repo)
            cache = str(Path(d) / "c.json")
            asked = []

            def meta(pr):
                asked.append(pr)
                return META.get(pr) if pr == 101 else None
            for _ in range(2):
                rs._reset_memo()
                rs.merged_unreleased_issues(repo, pr_meta_fn=meta, cache_path=cache, slug="o/r")
        self.assertEqual(sorted(asked), [101, 102], "each released PR is read once")

    def test_plain_commits_do_not_push_a_pr_out_of_the_window(self):
        # live david3: 1500 commits reached back only to 19.11., the delivered PRs
        # of early November sat below it. The window counts PR merges only.
        import cli_release_backfill as bf
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            fix = _released_fork(repo)
            for k in range(12):
                _commit(repo, "fix: plain commit %d" % k)
            for br in ("main", "develop", "staging"):
                _git(repo, "update-ref", "refs/remotes/upstream/%s" % br, "HEAD")
            cache = str(Path(d) / "c.json")
            old = bf.BACKFILL_COMMITS
            bf.BACKFILL_COMMITS = 5
            try:
                rs.merged_unreleased_issues(repo, pr_meta_fn=META.get, cache_path=cache,
                                            slug="o/r")
            finally:
                bf.BACKFILL_COMMITS = old
            got = rs.merged_released_oids(repo, {55}, cache_path=cache, slug="o/r")
        self.assertEqual(got, {55: [fix]})

    def test_a_finished_backfill_skips_the_scan_until_main_moves(self):
        import cli_release_backfill as bf
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            _released_fork(repo)
            cache = str(Path(d) / "c.json")
            rs.merged_unreleased_issues(repo, pr_meta_fn=META.get, cache_path=cache, slug="o/r")
            scans = []
            real = bf._default_git_backfill

            def bf_git(root, ref, limit):
                scans.append(ref)
                return real(root, ref, limit)
            self.assertIsNone(bf.backfill(repo, "upstream", rs._load_cache(cache), cache,
                                          META.get, 15, git_fn=bf_git))
            self.assertEqual(scans, [], "main unchanged since the finished pass: no scan")
            _commit(repo, "Merge pull request #103 from dev/y")
            _git(repo, "update-ref", "refs/remotes/upstream/main", "HEAD")
            bf.backfill(repo, "upstream", rs._load_cache(cache), cache, META.get, 15,
                        git_fn=bf_git)
            self.assertEqual(scans, ["upstream/main"])


class ForkMainIsUpstream(unittest.TestCase):

    def test_cached_pr_on_upstream_main_is_released_on_a_fork(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            fix = _released_fork(repo)
            cache = Path(d) / "pr-issues.json"
            cache.write_text(json.dumps({"101": {"issues": [55], "oid": fix, "v": rs._CACHE_V,
                                                 "docs": False}}))
            got = rs.merged_released_oids(repo, {55}, cache_path=str(cache), slug="o/r")
        self.assertEqual(got, {55: [fix]})


if __name__ == "__main__":
    unittest.main()
