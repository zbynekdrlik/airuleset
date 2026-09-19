"""#1083 — cli_release_state: the footer `M` set (merged into develop/staging,
not yet main) + the `--audit` release-hygiene set (fix reached main, still open),
both derived from GIT (ground truth), hermetic over injected git/PR seams.

RED-first: on the base tree `cli_release_state` does not exist.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_release_state as rs


def _git_fn_factory(by_range):
    """Return a fake `git_fn(root, rng) -> [(oid, subj)]` from a
    {range_suffix: [(oid, subj), ...]} map. A range not in the map yields []
    (a missing origin/<branch> ref — the two-branch case)."""
    def _git(root, rng):
        for suffix, rows in by_range.items():
            if rng.endswith(suffix):
                return list(rows)
        return []
    return _git


class MergedUnreleased(unittest.TestCase):
    def setUp(self):
        import tempfile
        rs._reset_memo()
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.cache = str(Path(d) / "pr-issues.json")

    def test_merge_and_squash_subjects_map_to_issues(self):
        git = _git_fn_factory({
            "origin/main..origin/develop": [
                ("aaa", "Merge pull request #5 from stream/5-fix"),
                ("bbb", "Refactor the widget (#7)"),
                ("ccc", "just a plain commit, no PR ref"),
            ],
            "origin/main..origin/staging": [],
        })
        meta = {5: ("PR five", "Closes #105\nfollow-up to #900"),
                7: ("PR seven (#7)", "Fixes #107")}
        calls = []

        def pr_meta(pr):
            calls.append(pr)
            return meta.get(pr)

        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=pr_meta,
            cache_path=self.cache, slug="o/r")
        # PR #5 → #105 + #900 (bare ref kept); PR #7 → #107. The PR's own
        # number is excluded (#7 must not appear from "PR seven (#7)").
        self.assertEqual(set(got), {105, 900, 107})
        self.assertEqual(sorted(calls), [5, 7])

    def test_cache_hit_makes_zero_pr_reads(self):
        git = _git_fn_factory({
            "origin/main..origin/develop": [
                ("aaa", "Merge pull request #5 from stream/5-fix")],
            "origin/main..origin/staging": [],
        })
        meta = {5: ("t", "Closes #105")}
        calls = []

        def pr_meta(pr):
            calls.append(pr)
            return meta.get(pr)

        first = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=pr_meta,
            cache_path=self.cache, slug="o/r")
        self.assertEqual(set(first), {105})
        self.assertEqual(calls, [5])
        # Second call: the merged PR is already cached (its issue refs never
        # change), so NO further PR read is made.
        second = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=pr_meta,
            cache_path=self.cache, slug="o/r")
        self.assertEqual(set(second), {105})
        self.assertEqual(calls, [5], "cache hit must issue zero new PR reads")

    def test_two_branch_repo_no_develop_is_empty(self):
        git = _git_fn_factory({})   # every range -> [] (no origin/develop)
        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=lambda pr: ("x", "Closes #1"),
            cache_path=self.cache, slug="o/r")
        self.assertEqual(set(got), set())

    def test_rest_failure_leaves_pr_uncached_and_ticket_out_of_M(self):
        git = _git_fn_factory({
            "origin/main..origin/develop": [
                ("aaa", "Merge pull request #5 from stream/5-fix")],
            "origin/main..origin/staging": [],
        })
        calls = []

        def pr_meta(pr):
            calls.append(pr)
            return None   # REST failed

        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=pr_meta,
            cache_path=self.cache, slug="o/r")
        self.assertEqual(set(got), set(),
                         "a PR whose REST read failed contributes no issues "
                         "(its ticket stays in I — never falsely dropped to M)")
        # And it must be RETRIED (not cached as empty).
        again = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=pr_meta,
            cache_path=self.cache, slug="o/r")
        self.assertEqual(set(again), set())
        self.assertEqual(calls, [5, 5], "a failed PR read must be retried")

    def test_staging_range_also_contributes(self):
        git = _git_fn_factory({
            "origin/main..origin/develop": [
                ("aaa", "Merge pull request #5 from s/5")],
            "origin/main..origin/staging": [
                ("ddd", "Merge pull request #8 from s/8")],
        })
        meta = {5: ("t", "Closes #105"), 8: ("t", "Closes #108")}
        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=lambda pr: meta.get(pr),
            cache_path=self.cache, slug="o/r")
        self.assertEqual(set(got), {105, 108})


class MergedReleasedStillOpen(unittest.TestCase):
    def setUp(self):
        import tempfile
        rs._reset_memo()
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))
        self.cache = str(Path(self.d) / "pr-issues.json")

    def _seed_cache(self):
        # Populate the cache via merged_unreleased_issues (both PRs currently in
        # develop..), so each carries its introducing-commit oid.
        git = _git_fn_factory({
            "origin/main..origin/develop": [
                ("oid_released", "Merge pull request #5 from s/5"),
                ("oid_unreleased", "Merge pull request #6 from s/6"),
            ],
            "origin/main..origin/staging": [],
        })
        meta = {5: ("t", "Closes #105"), 6: ("t", "Closes #106")}
        rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=lambda pr: meta.get(pr),
            cache_path=self.cache, slug="o/r")

    def test_released_and_open_is_flagged(self):
        self._seed_cache()

        def is_anc(oid, root):
            return oid == "oid_released"   # PR #5 reached main, #6 has not

        # #105 (PR #5) is released AND still open -> a hygiene defect.
        got = rs.merged_released_still_open(
            "/repo", open_numbers={105, 106, 999},
            is_ancestor_fn=is_anc, cache_path=self.cache, slug="o/r")
        self.assertEqual(got, [105])

    def test_released_but_closed_is_not_flagged(self):
        self._seed_cache()

        def is_anc(oid, root):
            return True   # both released

        # #105 released but NOT in open set -> not flagged (it closed correctly).
        got = rs.merged_released_still_open(
            "/repo", open_numbers={106}, is_ancestor_fn=is_anc,
            cache_path=self.cache, slug="o/r")
        self.assertEqual(got, [106])

    def test_no_cache_is_empty(self):
        got = rs.merged_released_still_open(
            "/repo", open_numbers={1, 2}, is_ancestor_fn=lambda o, r: True,
            cache_path=str(Path(self.d) / "absent.json"), slug="o/r")
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
