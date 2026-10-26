"""#1228: a docs / chore / style / ci change implements no ticket, so it never
puts a ticket into the footer `M` (merged, not yet released).

presenter 13.10.2026: two design-doc commits on `dev` (`docs(mockups): …
(#850, #851)`, `docs(spec): … (#850 A + B)`) put two big, mostly unstarted
epics into M, and the owner saw `I 1` while three epics of work were ahead.
The three-branch PR path reads PR titles the same way, and its append-only
PR cache must re-judge entries written before the rule.
"""
import json
import os
import subprocess
import sys
import tempfile
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


def _commit(repo, subject, body=""):
    _git(repo, "commit", "--allow-empty", "-q", "-m",
         subject if not body else subject + "\n\n" + body)
    return _git(repo, "rev-parse", "HEAD")


def _two_branch(repo, commits):
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True,
                   env=_GENV, capture_output=True)
    _git(repo, "remote", "add", "origin", "https://github.com/o/r")
    _commit(repo, "chore: baseline")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "-b", "dev")
    for c in commits:
        _commit(repo, c[0], c[1] if len(c) > 1 else "")
    _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")


class TwoBranchDocsCommits(unittest.TestCase):

    def setUp(self):
        rs._reset_memo()

    def test_presenter_epics_stay_out_of_m(self):
        with TemporaryDirectory() as repo:
            _two_branch(repo, [
                ("docs(mockups): epic screens for both modes (#850, #851)",),
                ("docs(spec): epic spec (#850 A + B)",),
                ("fix: prod regression in the timer (#853)",)])
            got = rs.merged_unreleased_issues(repo)
        self.assertEqual(set(got), {853})

    def test_every_non_implementing_type_and_the_docs_marker(self):
        with TemporaryDirectory() as repo:
            _two_branch(repo, [
                ("chore(deps): bump tauri (#860)",),
                ("style: rustfmt (#861)",),
                ("ci: cache the cargo dir (#862)",),
                ("docs!: rewrite the README (#863)",),
                ("feat: user guide page (#864)", "[no-test: docs only]"),
                ("Docs(readme): capitalised type (#866)",),
                ("chore: wire up the follow-up", "Closes #1203"),
                ("docsify: a type that only starts with docs (#867)",),
                ("test(#870): [red] reproduce the crash",),
                ("refactor(#871): split the player module",)])
            got = rs.merged_unreleased_issues(repo)
        self.assertEqual(set(got), {867, 870, 871})


def _git_fn(ranges):
    def _f(root, rng):
        return list(ranges.get(rng, []))
    return _f


class ThreeBranchDocsPRs(unittest.TestCase):

    def setUp(self):
        rs._reset_memo()
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.cache = str(Path(d) / "pr-issues.json")

    def test_a_docs_pr_title_attributes_nothing(self):
        git = _git_fn({"origin/main..origin/develop": [
            ("a", "docs: spec for #900 (#950)"),
            ("b", "fix: real #901 (#951)"),
            ("c", "feat: guide #902 (#952)")]})
        meta = {950: ("docs: spec for #900", ""),
                951: ("fix: real #901", ""),
                952: ("feat: guide #902 [no-test: docs only]", "")}
        got = rs.merged_unreleased_issues(
            "/repo", git_fn=git, pr_meta_fn=lambda pr: meta.get(pr),
            cache_path=self.cache, slug="o/r")
        self.assertEqual(set(got), {901})


class LegacyCacheEntries(unittest.TestCase):
    """PR-cache entries written before #1228 carry no `v` and their old refs;
    they are re-judged from the introducing commit's local git message."""

    def setUp(self):
        rs._reset_memo()

    def _repo(self, repo):
        subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True,
                       env=_GENV, capture_output=True)
        docs = _commit(repo, "docs(#7421): gk Prevencia batch 15 (#7662)")
        fix = _commit(repo, "#7644 (M1): stream_merge_guard hardening (#7660)")
        merge = _commit(repo, "Merge pull request #7700 from s/prevencia",
                        "docs: prevencia notes\n\nmore text")
        return docs, fix, merge

    def _seed(self, cache, docs, fix, merge):
        Path(cache).write_text(json.dumps({
            "7662": {"issues": [7421], "oid": docs},
            "7660": {"issues": [7644], "oid": fix},
            "7700": {"issues": [7421], "oid": merge}}))

    def test_in_range_legacy_docs_entries_leave_m_and_are_rewritten(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            docs, fix, merge = self._repo(repo)
            cache = str(Path(d) / "pr-issues.json")
            self._seed(cache, docs, fix, merge)
            git = _git_fn({"origin/main..origin/develop": [
                (docs, "docs(#7421): gk Prevencia batch 15 (#7662)"),
                (fix, "#7644 (M1): stream_merge_guard hardening (#7660)"),
                (merge, "Merge pull request #7700 from s/prevencia")]})
            got = rs.merged_unreleased_issues(
                repo, git_fn=git, cache_path=cache, slug="o/r",
                pr_meta_fn=lambda pr: self.fail("a cached PR is never re-read"))
            saved = json.loads(Path(cache).read_text())
        self.assertEqual(set(got), {7644})
        self.assertEqual(saved["7662"]["issues"], [])
        self.assertEqual(saved["7700"]["issues"], [])
        self.assertEqual(saved["7660"]["issues"], [7644])

    def test_released_legacy_docs_entries_are_not_on_main(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            docs, fix, merge = self._repo(repo)
            cache = str(Path(d) / "pr-issues.json")
            self._seed(cache, docs, fix, merge)
            got = rs.merged_released_oids(
                repo, {7421, 7644}, is_ancestor_fn=lambda oid, root: True,
                cache_path=cache, slug="o/r")
        self.assertEqual(got, {7644: [fix]})


class ConsumerReadsTheSameSet(unittest.TestCase):
    """The quals commands, the footer and the dependency check all read
    `merged_unreleased_issues`; the quals reader shows the rule too."""

    def test_quals_merged_unreleased(self):
        import cli_quals_cmd
        rs._reset_memo()
        with TemporaryDirectory() as repo:
            _two_branch(repo, [("docs(spec): epic (#850)",),
                               ("fix: regression (#853)",)])
            got = cli_quals_cmd._merged_unreleased(repo)
        rs._reset_memo()
        self.assertEqual(set(got), {853})


class ReviewRound1(unittest.TestCase):
    """The two adversarial reviews of the first fix."""

    def setUp(self):
        rs._reset_memo()

    def test_an_open_docs_pr_still_links_its_ticket(self):
        # P and the C veto are NOT M: an open linked PR keeps today's
        # behaviour, so a docs PR in CI never turns a live fix into "close it".
        import cli_ticket_facts
        payload = {"data": {"repository": {"pullRequests": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"number": 8900, "isDraft": False, "body": "",
                       "title": "docs(#7421): gk Prevencia batch 16",
                       "commits": {"nodes": [{"commit": {
                           "statusCheckRollup": {"state": "PENDING"}}}]},
                       "closingIssuesReferences": {"nodes": []}}]}}}}
        self.assertEqual(cli_ticket_facts.open_pr_states(payload), {7421: True})

    def test_a_body_marker_never_drops_a_bundled_pr(self):
        git = _git_fn({"origin/main..origin/develop": [
            ("a", "Guard GPS float() cast (#2131) + fix stale doc (#2118) (#2157)")]})
        meta = {2157: ("Guard GPS float() cast (#2131) + fix stale doc (#2118)",
                       "Two commits.\n[no-test: docs only] for the doc one")}
        with TemporaryDirectory() as d:
            got = rs.merged_unreleased_issues(
                "/repo", git_fn=git, pr_meta_fn=lambda pr: meta.get(pr),
                cache_path=str(Path(d) / "c.json"), slug="o/r")
        self.assertEqual(set(got), {2131, 2118})

    def test_marker_and_scope_edges(self):
        with TemporaryDirectory() as repo:
            _two_branch(repo, [
                ("fix: wording (#5)", "[no-test: docstring wording]"),
                ("ci(x) more (#6): y",),
                ("fix: y (#7)", "[no-test: documentation only]")])
            got = rs.merged_unreleased_issues(repo)
        self.assertEqual(set(got), {5, 6})


class LegacyMigrationRound1(unittest.TestCase):
    """The migration is judged by the commit git CAN read, stamped only after
    a read, written back by both readers, and never re-judged after that."""

    def setUp(self):
        rs._reset_memo()
        import cli_commit_kind
        self.ck = cli_commit_kind
        self.reads = []
        real = cli_commit_kind._commit_message
        patcher = __import__("unittest.mock").mock.patch.object(
            cli_commit_kind, "_commit_message",
            side_effect=lambda root, oid: self.reads.append(oid) or real(root, oid))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _repo(self, repo):
        subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True,
                       env=_GENV, capture_output=True)
        return (_commit(repo, "docs(#7421): gk Prevencia batch 15 (#7662)"),
                _commit(repo, "#7644 (M1): hardening (#7660)"))

    def _sweep(self, repo, cache, docs, fix):
        git = _git_fn({"origin/main..origin/develop": [
            (docs, "docs(#7421): gk Prevencia batch 15 (#7662)"),
            (fix, "#7644 (M1): hardening (#7660)")]})
        rs._reset_memo()
        return rs.merged_unreleased_issues(
            repo, git_fn=git, cache_path=cache, slug="o/r",
            pr_meta_fn=lambda pr: self.fail("a cached PR is never re-read"))

    def test_judged_by_the_in_range_commit_and_never_twice(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            docs, fix = self._repo(repo)
            cache = str(Path(d) / "c.json")
            Path(cache).write_text(json.dumps({
                "7662": {"issues": [7421], "oid": "0" * 40},   # unreadable
                "7660": {"issues": [7644], "oid": fix}}))
            first = self._sweep(repo, cache, docs, fix)
            saved = json.loads(Path(cache).read_text())
            self.reads.clear()
            second = self._sweep(repo, cache, docs, fix)
        self.assertEqual(set(first), {7644})
        self.assertEqual(set(second), {7644})
        self.assertEqual({k: v.get("v") for k, v in saved.items()},
                         {"7662": 2, "7660": 2})
        self.assertEqual(self.reads, [], "a stamped entry is never re-judged")

    def test_a_fetched_entry_is_stamped_and_never_rejudged(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            docs, fix = self._repo(repo)
            cache = str(Path(d) / "c.json")
            meta = {7662: ("docs(#7421): gk Prevencia batch 15", ""),
                    7660: ("#7644 (M1): hardening", "")}
            git = _git_fn({"origin/main..origin/develop": [
                (docs, "docs(#7421): gk Prevencia batch 15 (#7662)"),
                (fix, "#7644 (M1): hardening (#7660)")]})
            for _ in range(2):
                rs._reset_memo()
                got = rs.merged_unreleased_issues(
                    repo, git_fn=git, cache_path=cache, slug="o/r",
                    pr_meta_fn=lambda pr: meta.get(pr))
            saved = json.loads(Path(cache).read_text())
        self.assertEqual(set(got), {7644})
        self.assertEqual({k: v.get("v") for k, v in saved.items()},
                         {"7662": 2, "7660": 2})
        self.assertEqual(self.reads, [], "a fetched entry needs no git read")

    def test_an_unreadable_commit_is_not_stamped(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            docs, fix = self._repo(repo)
            cache = str(Path(d) / "c.json")
            Path(cache).write_text(json.dumps({
                "7662": {"issues": [7421], "oid": "0" * 40}}))
            git = _git_fn({"origin/main..origin/develop": [
                ("1" * 40, "docs(#7421): gk Prevencia batch 15 (#7662)")]})
            got = rs.merged_unreleased_issues(
                repo, git_fn=git, cache_path=cache, slug="o/r",
                pr_meta_fn=lambda pr: None)
            saved = json.loads(Path(cache).read_text())
        self.assertEqual(set(got), {7421}, "unknown keeps the old refs")
        self.assertNotIn("v", saved["7662"], "retried next refresh")

    def test_released_legacy_entries_are_stamped_once(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            docs, fix = self._repo(repo)
            cache = str(Path(d) / "c.json")
            Path(cache).write_text(json.dumps({
                "7662": {"issues": [7421], "oid": docs},
                "7660": {"issues": [7644], "oid": fix}}))
            kw = dict(is_ancestor_fn=lambda oid, root: True, cache_path=cache,
                      slug="o/r")
            first = rs.merged_released_oids(repo, {7421, 7644}, **kw)
            self.reads.clear()
            second = rs.merged_released_oids(repo, {7421, 7644}, **kw)
            saved = json.loads(Path(cache).read_text())
        self.assertEqual(first, {7644: [fix]})
        self.assertEqual(second, {7644: [fix]})
        self.assertEqual(self.reads, [])
        self.assertEqual(saved["7662"], {"issues": [], "oid": docs, "v": 2})

    def test_a_malformed_oid_never_reaches_git(self):
        calls = []
        with __import__("unittest.mock").mock.patch.object(
                self.ck.subprocess, "run",
                side_effect=lambda *a, **k: calls.append(a) or None):
            for oid in (12345, "--output=/tmp/x", "abc", None):
                self.assertIsNone(
                    self.ck.introducing_commit_implements_nothing("/r", oid))
        self.assertEqual(calls, [])


class ReviewRound2(unittest.TestCase):
    """The delta review: the docs rule decides whether a ticket ENTERS M (or
    C), never whether another change is still pending (the C veto, #1141)."""

    def setUp(self):
        rs._reset_memo()

    def test_a_merged_docs_pr_still_keeps_a_live_fix_open(self):
        import cli_ticket_facts
        import cli_ticket_state as ts
        git = _git_fn({"origin/main..origin/develop": [
            ("a", "docs(#7421): gk Prevencia batch 15 (#7700)")]})
        meta = {7700: ("docs(#7421): gk Prevencia batch 15", "")}
        with TemporaryDirectory() as d:
            merged = rs.merged_unreleased_issues(
                "/repo", git_fn=git, pr_meta_fn=lambda pr: meta.get(pr),
                cache_path=str(Path(d) / "c.json"), slug="o/r")
        self.assertNotIn(7421, merged, "a docs PR never puts a ticket into M")
        facts = cli_ticket_facts._facts(merged, {}, {}, {7421: ts.RELEASED})
        bucket, reason = ts.classify({"labels": []}, facts.of(7421), ts.Box(),
                                     number=7421)
        self.assertNotEqual(bucket, "C", reason)

    def test_a_released_docs_oid_joins_only_an_implemented_tickets_list(self):
        with TemporaryDirectory() as repo, TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", "-b", "main", repo],
                           check=True, env=_GENV, capture_output=True)
            fix = _commit(repo, "#7644 (M1): hardening (#7660)")
            notes = _commit(repo, "docs(#7644): operator notes (#7701)")
            spec = _commit(repo, "docs(#7421): epic spec (#7662)")
            cache = str(Path(d) / "c.json")
            git = _git_fn({"origin/main..origin/develop": [
                (fix, "#7644 (M1): hardening (#7660)"),
                (notes, "docs(#7644): operator notes (#7701)"),
                (spec, "docs(#7421): epic spec (#7662)")]})
            meta = {7660: ("#7644 (M1): hardening", ""),
                    7701: ("docs(#7644): operator notes", ""),
                    7662: ("docs(#7421): epic spec", "")}
            rs.merged_unreleased_issues(repo, git_fn=git, cache_path=cache,
                                        slug="o/r",
                                        pr_meta_fn=lambda pr: meta.get(pr))
            got = rs.merged_released_oids(
                repo, {7644, 7421}, is_ancestor_fn=lambda oid, root: True,
                cache_path=cache, slug="o/r")
        self.assertEqual({k: set(v) for k, v in got.items()},
                         {7644: {fix, notes}},
                         "a released docs oid must be live too before C; a "
                         "docs-only ticket gets no on-main fact")

    def test_the_two_branch_set_names_the_docs_tickets(self):
        with TemporaryDirectory() as repo:
            _two_branch(repo, [("docs(spec): epic (#850)",),
                               ("fix: regression (#853)",)])
            got = rs.merged_unreleased_issues(repo)
        self.assertEqual(set(got), {853})
        self.assertEqual(set(getattr(got, "docs", ()) or ()), {850})


if __name__ == "__main__":
    unittest.main()
