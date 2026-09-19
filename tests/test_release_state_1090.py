"""#1090 — the footer `M` sweep (cli_release_state) must be BOUNDED, FORK-AWARE
and QUOTA-SAFE.

The #1083 machinery walks EVERY PR in `origin/main..origin/develop` and fetches
each with one `gh api repos/<slug>/pulls/<N>`, saving the cache only after the
WHOLE loop. On the david1-4 fork clones (`origin` = the stale fork, 3 800+ PRs
in range) each 120 s statusline refresh fired thousands of REST calls, none of
which ever reached the cache write, exhausting the shared stream App budget
within minutes of every hourly reset (incident 2026-09-19).

Four guards (Approach 1, main's decided design):
  (a) canonical range   — a fork clone reads `upstream/*` (the canonical repo),
                          not the fork's stale `origin/*`; absent canonical refs
                          hide `M` with a journal reason, zero REST.
  (b) 60-PR cap         — more than MERGED_UNRELEASED_MAX_PRS in range -> `M`
                          hidden with a journal line, ZERO REST.
  (c) 15-meta budget    — at most MERGED_UNRELEASED_META_BUDGET new PR metas per
      + incremental cache  refresh, the cache saved after EVERY new entry so a
                          killed refresh keeps its progress.
  (d) quota stop        — a 403/429/rate-limit from `gh api` yields the QUOTA
                          sentinel (distinct from None); the loop stops at once.

RED-first: on the base tree none of the four guards exist — the cap makes 3 900
calls, the fork reads `origin/*`, the budget is unbounded, a quota keeps hammering.
"""
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_release_state as rs


def _recording_git_fn(by_range, queried):
    """A fake `git_fn(root, rng) -> [(oid, subj)]` that ALSO records every range
    string it is asked for into `queried`, so a test can prove which branch
    prefix (origin vs upstream) the sweep actually read."""
    def _git(root, rng):
        queried.append(rng)
        for suffix, rows in by_range.items():
            if rng.endswith(suffix):
                return list(rows)
        return []
    return _git


def _remote_fn(mapping):
    """A fake `remote_fn(root, name) -> slug|None` from a {name: slug} map."""
    return lambda root, name: mapping.get(name)


def _ref_exists_fn(present):
    """A fake `ref_exists_fn(root, ref) -> bool` from a set of present refs."""
    return lambda root, ref: ref in present


class CanonicalRange(unittest.TestCase):
    """(a) — a fork clone reads the CANONICAL repo's branches (`upstream/*`),
    never the fork's stale `origin/*`."""

    def setUp(self):
        import tempfile
        rs._reset_memo()
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.cache = str(Path(d) / "pr-issues.json")

    def test_fork_with_upstream_refs_reads_upstream_not_origin(self):
        # origin = the stale fork, upstream = the canonical slug (what
        # `gh repo view` returns and the caller passes as `slug`).
        queried = []
        git = _recording_git_fn({
            "upstream/main..upstream/develop": [
                ("aaa", "Merge pull request #5 from stream/5-fix")],
            "upstream/main..upstream/staging": [],
            # a DIFFERENT PR sits in the fork's stale origin range — it must
            # NEVER be read.
            "origin/main..origin/develop": [
                ("zzz", "Merge pull request #999 from fork/stale")],
            "origin/main..origin/staging": [],
        }, queried)
        meta = {5: ("t", "Closes #105"), 999: ("t", "Closes #900")}
        calls = []

        def pr_meta(pr):
            calls.append(pr)
            return meta.get(pr)

        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=pr_meta, cache_path=self.cache,
            slug="canon/erp",
            remote_fn=_remote_fn({"origin": "fork/erp", "upstream": "canon/erp"}),
            ref_exists_fn=_ref_exists_fn({"upstream/main", "upstream/develop"}))
        self.assertEqual(set(got), {105},
                         "the canonical (upstream) range must drive M")
        self.assertNotIn(900, set(got),
                         "the fork's stale origin range must NOT contribute")
        self.assertEqual(calls, [5], "only the upstream PR is read")
        self.assertFalse([r for r in queried if "origin/" in r],
                         "a fork clone must issue ZERO reads against origin/*: "
                         "%r" % queried)

    def test_fork_without_upstream_refs_hides_M_with_reason_zero_rest(self):
        queried = []
        git = _recording_git_fn({
            "origin/main..origin/develop": [
                ("zzz", "Merge pull request #999 from fork/stale")],
        }, queried)
        calls = []

        def pr_meta(pr):
            calls.append(pr)
            return ("t", "Closes #900")

        buf = io.StringIO()
        with redirect_stderr(buf):
            got = rs.merged_unreleased_issues(
                "/repo", git_fn=git, pr_meta_fn=pr_meta, cache_path=self.cache,
                slug="canon/erp",
                remote_fn=_remote_fn({"origin": "fork/erp",
                                      "upstream": "canon/erp"}),
                ref_exists_fn=_ref_exists_fn(set()))   # upstream never fetched
        self.assertEqual(set(got), set(), "M is hidden on a ref-less fork clone")
        self.assertEqual(calls, [], "ZERO REST when canonical refs are absent")
        self.assertIn("fork clone without canonical refs", buf.getvalue())
        self.assertIn("M hidden", buf.getvalue())

    def test_canonical_clone_keeps_origin(self):
        # origin IS the canonical slug -> unchanged origin behaviour.
        queried = []
        git = _recording_git_fn({
            "origin/main..origin/develop": [
                ("aaa", "Merge pull request #5 from s/5")],
            "origin/main..origin/staging": [],
        }, queried)
        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=lambda pr: ("t", "Closes #105"),
            cache_path=self.cache, slug="canon/erp",
            remote_fn=_remote_fn({"origin": "canon/erp"}))
        self.assertEqual(set(got), {105})
        self.assertTrue(any("origin/" in r for r in queried))
        self.assertFalse([r for r in queried if "upstream/" in r])


class RangeCap(unittest.TestCase):
    """(b) — a range past MERGED_UNRELEASED_MAX_PRS hides M with ZERO REST."""

    def setUp(self):
        import tempfile
        rs._reset_memo()
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.cache = str(Path(d) / "pr-issues.json")

    def test_giant_range_makes_zero_meta_calls_and_journals_the_count(self):
        rows = [("oid%d" % n, "Merge pull request #%d from s/%d" % (n, n))
                for n in range(1, 3901)]   # 3 900 PRs — the incident's scale
        git = _recording_git_fn(
            {"origin/main..origin/develop": rows,
             "origin/main..origin/staging": []}, [])
        calls = []

        def pr_meta(pr):
            calls.append(pr)
            return ("t", "Closes #%d" % (100000 + pr))

        buf = io.StringIO()
        with redirect_stderr(buf):
            got = rs.merged_unreleased_issues(
                "/repo", git_fn=git, pr_meta_fn=pr_meta, cache_path=self.cache,
                slug="o/r", remote_fn=_remote_fn({"origin": "o/r"}))
        self.assertEqual(set(got), set(), "M is hidden past the cap")
        self.assertEqual(calls, [], "a capped range must make ZERO REST calls")
        self.assertIn("3900", buf.getvalue())
        self.assertIn("M hidden", buf.getvalue())

    def test_at_cap_still_processes(self):
        # exactly MERGED_UNRELEASED_MAX_PRS is NOT over the cap.
        n_at = rs.MERGED_UNRELEASED_MAX_PRS
        rows = [("oid%d" % n, "Merge pull request #%d from s/%d" % (n, n))
                for n in range(1, n_at + 1)]
        git = _recording_git_fn(
            {"origin/main..origin/develop": rows,
             "origin/main..origin/staging": []}, [])
        # more PRs than the budget, so only the budget's worth are fetched, but
        # the cap must NOT hide the set.
        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git,
            pr_meta_fn=lambda pr: ("t", "Closes #%d" % (100000 + pr)),
            cache_path=self.cache, slug="o/r",
            remote_fn=_remote_fn({"origin": "o/r"}))
        self.assertTrue(len(got) >= 1, "a range at the cap is not hidden")


class MetaBudgetAndIncrementalCache(unittest.TestCase):
    """(c) — at most MERGED_UNRELEASED_META_BUDGET new metas per refresh, cache
    saved after EVERY new entry."""

    def setUp(self):
        import tempfile
        rs._reset_memo()
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.cache = str(Path(d) / "pr-issues.json")

    def _make(self, n_prs):
        rows = [("oid%d" % n, "Merge pull request #%d from s/%d" % (n, n))
                for n in range(1, n_prs + 1)]
        return _recording_git_fn(
            {"origin/main..origin/develop": rows,
             "origin/main..origin/staging": []}, [])

    def test_budget_caps_new_metas_and_fills_over_refreshes(self):
        budget = rs.MERGED_UNRELEASED_META_BUDGET
        git = self._make(40)
        calls = []
        observed = []   # cache size seen at the START of each fetch

        def pr_meta(pr):
            observed.append(len(rs._load_cache(self.cache)))
            calls.append(pr)
            return ("t", "Closes #%d" % (1000 + pr))

        # Refresh 1: exactly `budget` new metas, `budget` cache entries.
        got1 = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=pr_meta, cache_path=self.cache,
            slug="o/r", remote_fn=_remote_fn({"origin": "o/r"}))
        self.assertEqual(len(calls), budget,
                         "at most the budget of new metas per refresh")
        self.assertEqual(len(rs._load_cache(self.cache)), budget,
                         "the budget's worth is cached after one refresh")
        self.assertEqual(set(got1), {1000 + n for n in range(1, budget + 1)})
        # Incremental save: each fetch saw the cache one bigger than the last
        # (0, 1, 2, ..., budget-1) -> the cache was written after EVERY entry,
        # not only at the end of the loop.
        self.assertEqual(observed, list(range(budget)),
                         "cache must be saved after EVERY new entry: %r"
                         % observed)

        # Refresh 2: the first `budget` are cached (no reads), the next `budget`
        # are fetched -> 2*budget cache entries.
        rs._reset_memo()
        calls.clear()
        observed.clear()
        got2 = rs.merged_unreleased_issues(
            "/repo", git_fn=self._make(40), pr_meta_fn=pr_meta,
            cache_path=self.cache, slug="o/r",
            remote_fn=_remote_fn({"origin": "o/r"}))
        self.assertEqual(len(calls), budget,
                         "the second refresh fetches the next budget's worth")
        self.assertEqual(len(rs._load_cache(self.cache)), 2 * budget)
        self.assertEqual(set(got2), {1000 + n for n in range(1, 2 * budget + 1)})

    def test_warm_cache_makes_zero_reads(self):
        # once every PR is cached, a refresh makes no reads (unchanged #1083).
        git = self._make(3)
        calls = []

        def pr_meta(pr):
            calls.append(pr)
            return ("t", "Closes #%d" % (1000 + pr))

        rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=pr_meta, cache_path=self.cache,
            slug="o/r", remote_fn=_remote_fn({"origin": "o/r"}))
        self.assertEqual(sorted(calls), [1, 2, 3])
        rs._reset_memo()
        calls.clear()
        again = rs.merged_unreleased_issues(
            "/repo", git_fn=self._make(3), pr_meta_fn=pr_meta,
            cache_path=self.cache, slug="o/r",
            remote_fn=_remote_fn({"origin": "o/r"}))
        self.assertEqual(calls, [], "a fully warm cache issues zero reads")
        self.assertEqual(set(again), {1001, 1002, 1003})


class QuotaStop(unittest.TestCase):
    """(d) — a QUOTA sentinel from the meta fn stops the loop at once."""

    def setUp(self):
        import tempfile
        rs._reset_memo()
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.cache = str(Path(d) / "pr-issues.json")

    def test_quota_sentinel_breaks_the_loop(self):
        rows = [("oid%d" % n, "Merge pull request #%d from s/%d" % (n, n))
                for n in range(1, 6)]   # 5 PRs, under the cap
        git = _recording_git_fn(
            {"origin/main..origin/develop": rows,
             "origin/main..origin/staging": []}, [])
        calls = []

        def pr_meta(pr):
            calls.append(pr)
            if pr == 3:
                return rs.QUOTA          # gh reported a 403/429
            return ("t", "Closes #%d" % (1000 + pr))

        buf = io.StringIO()
        with redirect_stderr(buf):
            got = rs.merged_unreleased_issues(
                "/repo", git_fn=git, pr_meta_fn=pr_meta, cache_path=self.cache,
                slug="o/r", remote_fn=_remote_fn({"origin": "o/r"}))
        self.assertEqual(calls, [1, 2, 3],
                         "the loop must stop the instant quota is hit")
        self.assertEqual(len(rs._load_cache(self.cache)), 2,
                         "the two PRs read before quota are cached")
        self.assertEqual(set(got), {1001, 1002})
        self.assertIn("quota", buf.getvalue().lower())

    def test_default_meta_fn_maps_rate_limit_stderr_to_quota(self):
        # the real `_default_pr_meta_fn` over a fake `subprocess.run`: a
        # rate-limit / 403 / 429 body -> QUOTA; a plain failure -> None
        # (unchanged skip-and-retry); success -> (title, body).
        class _R:
            def __init__(self, rc, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        def fake_run(argv, **kw):
            pr = int(argv[2].rsplit("/", 1)[1])   # repos/o/r/pulls/<N>
            if pr == 1:
                return _R(1, "", "API rate limit exceeded (HTTP 403)")
            if pr == 2:
                return _R(1, "", "You have exceeded a secondary rate limit")
            if pr == 3:
                return _R(1, "", "fatal: pull request not found")
            if pr == 4:
                return _R(1, "", "gh: HTTP 429: Too Many Requests")
            return _R(0, '{"title":"t","body":"Closes #9"}', "")

        orig = rs.subprocess.run
        rs.subprocess.run = fake_run
        self.addCleanup(lambda: setattr(rs.subprocess, "run", orig))
        meta = rs._default_pr_meta_fn("o/r")
        self.assertIs(meta(1), rs.QUOTA, "rate limit -> QUOTA")
        self.assertIs(meta(2), rs.QUOTA, "secondary rate -> QUOTA")
        self.assertIsNone(meta(3), "a plain failure stays None (retry)")
        self.assertIs(meta(4), rs.QUOTA, "429 -> QUOTA")
        self.assertEqual(meta(5), ("t", "Closes #9"))


class ExistingBehaviourPreserved(unittest.TestCase):
    """The #1083 contract still holds through the new code path when no fork /
    cap / quota is in play (a focused smoke; the full #1083 suite runs too)."""

    def setUp(self):
        import tempfile
        rs._reset_memo()
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.cache = str(Path(d) / "pr-issues.json")

    def test_slug_none_uses_origin_and_local_remote(self):
        # slug None + no remote_fn -> canonical unknown -> origin path (the
        # cli_quals_cmd `--count`/`--audit` shape, unchanged).
        queried = []
        git = _recording_git_fn({
            "origin/main..origin/develop": [
                ("aaa", "Merge pull request #5 from s/5")],
            "origin/main..origin/staging": [],
        }, queried)
        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=lambda pr: ("t", "Closes #105"),
            cache_path=self.cache)
        self.assertEqual(set(got), {105})
        self.assertTrue(any("origin/" in r for r in queried))


if __name__ == "__main__":
    unittest.main()
