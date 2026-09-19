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
        # PR #5 → #105 (Closes); the bare "follow-up to #900" cross-reference is
        # NOT closed by the PR, so #900 must NOT be pulled into M (#1083 review
        # BLOCKER — a bare mention would hide an unrelated open ticket from I).
        # PR #7 → #107 (Fixes); the PR's own number is excluded.
        self.assertEqual(set(got), {105, 107})
        self.assertNotIn(900, set(got),
                         "a bare cross-reference must never enter M")
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


class RealOdooTitleMapping(unittest.TestCase):
    """#1083 REWORK (coordinator integration review) — on the real odoo-erp repo
    the ticket numbers live in the PR TITLE (no body closing keyword), and the
    squash subject appends the PR number as a TRAILING `(#N)`. Fixtures verbatim
    from the last merged-to-develop PRs the coordinator sampled."""

    def setUp(self):
        import tempfile
        rs._reset_memo()
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.cache = str(Path(d) / "pr-issues.json")

    def test_title_is_the_ticket_carrier(self):
        git = _git_fn_factory({
            "origin/main..origin/develop": [
                ("a", "docs(#7421): gk Prevencia batch 15 (#7662)"),
                ("b", "#7644 (M1): stream_merge_guard hardening (#7660)"),
                ("c", "#7581 (úloha 980) Čo objednať (#7606)"),
                ("d", "#7631 #7632 (M1): gate advisories (#7657)"),
            ],
            "origin/main..origin/staging": [],
        })
        # PR REST titles (the squash subject minus the trailing (#PRnum)).
        meta = {
            7662: ("docs(#7421): gk Prevencia batch 15", "bare refs: #1 #2 #3"),
            7660: ("#7644 (M1): stream_merge_guard hardening", "see #9000"),
            7606: ("#7581 (úloha 980) Čo objednať", ""),
            7657: ("#7631 #7632 (M1): gate advisories", "follow-up to #5"),
        }
        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=lambda pr: meta.get(pr),
            cache_path=self.cache, slug="o/r")
        # tickets from the TITLES (incl. the docs(#N): scope form + multi-ticket
        # "#7631 #7632"); "úloha 980" (no #) excluded; every BARE BODY cross-ref
        # (#1 #2 #3 / #9000 / #5) refused; the PR's own number excluded.
        self.assertEqual(set(got), {7421, 7644, 7581, 7631, 7632})

    def test_squash_pr_number_is_the_trailing_paren_not_the_scope(self):
        commits = rs._pr_introducing_commits(
            "/repo", _git_fn_factory({
                "origin/main..origin/develop": [
                    ("a", "docs(#7421): gk Prevencia batch 15 (#7662)")],
                "origin/main..origin/staging": []}))
        self.assertEqual(commits, {7662: "a"},
                         "the (#7421) scope must NOT be read as the PR number")

    def test_squash_regex_end_anchored(self):
        self.assertIsNone(rs._SQUASH_PR_RE.search("docs(#7421): mid-string"))
        self.assertEqual(rs._SQUASH_PR_RE.search("x (#7662)").group(1), "7662")

    def test_body_only_bare_refs_yield_no_issues(self):
        git = _git_fn_factory({
            "origin/main..origin/develop": [("a", "Refactor thing (#7700)")],
            "origin/main..origin/staging": []})
        meta = {7700: ("Refactor thing", "see #100, #200\nfollow-up to #300")}
        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=lambda pr: meta.get(pr),
            cache_path=self.cache, slug="o/r")
        self.assertEqual(set(got), set(),
                         "a title with no #N + only bare body cross-refs = no M")

    def test_revert_title_carries_no_ticket(self):
        # a Revert UNDOES the fix, so the reverted (still-open) ticket must STAY
        # in I, never be dropped into M (fail-safe direction; delta review #1083).
        self.assertEqual(
            rs._issue_refs('Revert "docs(#7421): gk Prevencia batch 15"', "",
                           7999), set())

    def test_cross_repo_title_ref_excluded(self):
        # a cross-repo `owner/repo#N` in a title is a reference, not an
        # implemented ticket (delta review #1083).
        self.assertEqual(
            rs._issue_refs("#7644 sync odoo/odoo#7421 upstream fix", "", 7660),
            {7644})

    def test_squash_trailing_text_subject_is_skipped(self):
        # a `(#N)` NOT at the end is not the PR number → the commit is skipped
        # (over-count I, never mis-parse; the end-anchor's fail-safe value).
        commits = rs._pr_introducing_commits(
            "/repo", _git_fn_factory({
                "origin/main..origin/develop": [
                    ("a", "Title (#7662) rebased onto develop")],
                "origin/main..origin/staging": []}))
        self.assertEqual(commits, {})


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
