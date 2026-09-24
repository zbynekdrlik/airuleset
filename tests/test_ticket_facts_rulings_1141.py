"""#1141 slice 3 — the ROZHODNUTÉ rulings on the slice-3 open points (split out of
test_ticket_facts_1141.py at the 1000-line cap):
1. a reopened ticket never reads C (one batched stateReason read, never reused);
2. C counts in --count / the /goal stop-proof, P does not;
3. --explain states when the M set is partial.

All facts come from fake adapters — no live gh, no network.
"""

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
import cli_ticket_state as ts  # noqa: E402
import statusbar  # noqa: E402
from ticket_facts_testlib import (  # noqa: E402
    _graphql, _labels, _reasons, _row)


class Rulings(unittest.TestCase):
    """The ROZHODNUTÉ rulings on the slice-3 open points."""

    def setUp(self):
        import cli_ticket_facts
        self.f = cli_ticket_facts

    # ruling 1: a reopened ticket never reads C
    def test_reopened_blocks_c_and_explain_names_it(self):
        got = ts.classify(_row(5, "bug"),
                          ts.Facts(on_main=ts.RELEASED, reopened=True), ts.Box())
        self.assertEqual(got[0], "I")
        self.assertIn("REOPENED", got[1])

    def test_read_reopened_is_one_batched_call(self):
        calls = []

        def gh(args):
            calls.append(args)
            return json.dumps(_reasons((5, 6), reopened=(6,)))
        self.assertEqual(self.f.read_reopened("o/r", gh, [5, 6]),
                         frozenset({6}))
        self.assertEqual(len(calls), 1)
        query = " ".join(calls[0])
        self.assertIn("i5:issue(number:5)", query)
        self.assertIn("i6:issue(number:6)", query)
        self.assertIsNone(self.f.read_reopened("o/r", lambda a: "", [5]))
        self.assertIsNone(self.f.read_reopened(
            "o/r", lambda a: json.dumps(_reasons((5,))), [5, 6]))

    def test_refresh_reads_reopened_only_for_live_candidates(self):
        seen = []

        def gh(args):
            seen.append(" ".join(args))
            if "stateReason" in seen[-1]:
                return json.dumps(_reasons((5,), reopened=(5,)))
            return json.dumps(_graphql())
        with TemporaryDirectory() as home:
            facts = self.f.refresh("/repo", "o/r", {5, 6}, home=home, gh_fn=gh,
                                   deploy_fn=lambda r, s: None,
                                   released_fn=lambda *a: {5: ["abc1234"]})
            self.assertEqual(ts.classify(_row(5, "bug"), facts.of(5),
                                         ts.Box())[0], "I")
            self.assertEqual(sum("stateReason" in q for q in seen), 1)
            self.assertEqual(ts.classify(
                _row(5, "bug"), self.f.load("/repo", home=home).of(5),
                ts.Box())[0], "I")
            seen.clear()
            self.f.refresh("/repo", "o/r", {6}, home=home, gh_fn=gh,
                           deploy_fn=lambda r, s: None,
                           released_fn=lambda *a: {})
            self.assertFalse(any("stateReason" in q for q in seen))

    def test_an_unreadable_state_reason_never_gives_c(self):
        def gh(args):
            if any("stateReason" in a for a in args):
                return ""
            return json.dumps(_graphql())
        with TemporaryDirectory() as home:
            facts = self.f.refresh("/repo", "o/r", {5}, home=home, gh_fn=gh,
                                   deploy_fn=lambda r, s: None,
                                   released_fn=lambda *a: {5: ["abc1234"]})
            self.assertNotEqual(ts.classify(_row(5, "bug"), facts.of(5),
                                            ts.Box())[0], "C")

    # ruling 2: C counts in --count and the /goal stop-proof, P does not
    def test_the_stop_proof_count_is_i_plus_c(self):
        import cli_ticket_route
        b = {b: {} for b in ts.BUCKETS}
        b["I"], b["C"], b["P"] = {1: {}}, {2: {}, 3: {}}, {4: {}}
        self.assertEqual(cli_ticket_route.count(b), 3)

    def test_slice_quals_count_includes_c(self):
        marker = "<!-- airuleset:authority=fork-no-merge -->"
        rows = json.dumps([{"number": 1, "title": "own bug",
                            "labels": _labels("bug")}])
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(marker + "\n")
            gh = Path(bindir) / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  */comments*|*/timeline*) echo "[]";;\n'
                "  *assignee:@me*) echo '%s';;\n" % rows +
                '  *author:@me*|*label:stream:*) echo "[]";;\n'
                '  *) echo "kvaskodev";;\n'
                'esac\n')
            gh.chmod(0o755)
            env = {**os.environ, "HOME": home,
                   "PATH": f"{bindir}:{os.environ['PATH']}"}
            argv = [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                    "slice-quals", "--count"]
            before = subprocess.run(argv, capture_output=True, text=True,
                                    cwd=repo, env=env)
            self.assertEqual(before.stdout.strip(), "1", before.stderr)
            root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                  cwd=repo, capture_output=True, text=True
                                  ).stdout.strip()
            cache = self.f.cache_path(root, home)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"ts": time.time(), "open_pr": [],
                                         "pipeline": [], "reopened": [],
                                         "on_main": {"1": "released"}}))
            after = subprocess.run(argv, capture_output=True, text=True,
                                   cwd=repo, env=env)
            # #1 is now C (done, close me): still owed, still counted
            self.assertEqual(after.stdout.strip(), "1", after.stderr)
            explain = subprocess.run(argv[:-1] + ["--explain"],
                                     capture_output=True, text=True,
                                     cwd=repo, env=env)
            self.assertIn("1\tC\t", explain.stdout, explain.stderr)

    def test_the_footer_obligation_count_adds_c(self):
        with TemporaryDirectory() as home:
            path = statusbar.cache_dir(home) / (statusbar.cwd_key("/r")
                                                + ".json")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"ts": 5, "open": 2, "done": 3,
                                        "pipeline": 4}))
            self.assertEqual(statusbar.obligation_count("/r", home=home),
                             (5, 5))

    def test_the_snapshot_open_count_adds_c(self):
        import contextlib
        import io
        import cli_quals_snapshot
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_snapshot.emit_snapshot_json(
                {1: {}}, {}, "/r", [], None, lambda *a: None,
                lambda rows, root: (len(rows), ""), owed={7: {}, 8: {}})
        out = json.loads(buf.getvalue())
        self.assertEqual(out["open_count"], 3)
        self.assertEqual(out["dispatchable_count"], 1)

    # ruling 3: --explain says the M set is partial when the sweep truncated
    def test_a_quota_hit_marks_the_m_set_partial(self):
        import cli_release_state as rs
        rs._reset_memo()

        def git(root, rng):
            if rng.endswith("origin/main..origin/develop"):
                return [("aaa", "Merge pull request #5 from s/5-x")]
            return []
        with TemporaryDirectory() as tmp:
            rs.merged_unreleased_issues(
                "/repo", git_fn=git, pr_meta_fn=lambda pr: rs.QUOTA,
                cache_path=str(Path(tmp, "c.json")), slug="o/r")
        self.assertIn("quota", rs.merged_unreleased_partial("/repo"))

    def test_explain_prints_the_partial_m_note(self):
        facts = ts.TicketFacts(m_note="gh quota hit")
        out = ts.explain_lines({"I": {1: _row(1, "bug")}}, ts.Box(), facts)
        note = [ln for ln in out if "M set is partial" in ln]
        self.assertEqual(len(note), 1, out)
        self.assertIn("gh quota hit", note[0])
        self.assertTrue(out[-1].startswith("# explain: "))
        self.assertFalse(any("partial" in ln for ln in ts.explain_lines(
            {"I": {1: _row(1, "bug")}}, ts.Box(), ts.TicketFacts())))


class RulingsReview(unittest.TestCase):
    """The re-review of the ruling delta: no reuse of a reopen answer, and
    every ruling-3 path wired end to end."""

    def setUp(self):
        import cli_ticket_facts
        self.f = cli_ticket_facts

    def test_a_reopen_inside_a_minute_is_seen_at_the_next_refresh(self):
        state = {"reopened": ()}
        asked = []

        def gh(args):
            if any("stateReason" in a for a in args):
                asked.append(1)
                return json.dumps(_reasons((5,), state["reopened"]))
            return json.dumps(_graphql())
        with TemporaryDirectory() as home:
            def run(now):
                return self.f.refresh(
                    "/repo", "o/r", {5}, home=home, now=now, gh_fn=gh,
                    deploy_fn=lambda r, s: None,
                    released_fn=lambda *a: {5: ["abc1234"]})
            self.assertEqual(ts.classify(_row(5, "bug"), run(1000).of(5),
                                         ts.Box())[0], "C")
            state["reopened"] = (5,)          # the self-close guard reopened it
            self.assertEqual(ts.classify(_row(5, "bug"), run(1010).of(5),
                                         ts.Box())[0], "I")
            self.assertEqual(len(asked), 2)   # never a reused answer

    def test_an_old_cache_without_the_reopen_fact_is_no_c(self):
        with TemporaryDirectory() as home:
            path = self.f.cache_path("/repo", home)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"ts": 1000, "open_pr": [],
                                        "pipeline": [],
                                        "on_main": {"5": "released"}}))
            facts = self.f.load("/repo", home=home, now=1000)
        self.assertEqual(facts.on_main, {})

    def test_the_quals_route_carries_the_partial_m_note(self):
        from unittest import mock
        import cli_quals_cmd
        import cli_release_state as rs
        import cli_ticket_route
        with mock.patch.object(cli_quals_cmd, "_merged_unreleased",
                               return_value=frozenset()), \
                mock.patch.object(rs, "merged_unreleased_partial",
                                  return_value="gh quota hit"):
            b, facts = cli_ticket_route.quals({1: _row(1, "bug")}, "/none",
                                              ts.Box())
        self.assertEqual(facts.m_note, "gh quota hit")
        self.assertTrue(any("M set is partial" in ln
                            for ln in ts.explain_lines(b, ts.Box(), facts)))

    def _sweep(self, pr_count, meta):
        import cli_release_state as rs
        rs._reset_memo()

        def git(root, rng):
            if rng.endswith("origin/main..origin/develop"):
                return [("o%d" % n, "Merge pull request #%d from s/x" % n)
                        for n in range(1, pr_count + 1)]
            return []
        with TemporaryDirectory() as tmp:
            rs.merged_unreleased_issues(
                "/repo", git_fn=git, pr_meta_fn=meta,
                cache_path=str(Path(tmp, "c.json")), slug="o/r")
        return rs.merged_unreleased_partial("/repo")

    def test_every_truncation_path_leaves_a_note(self):
        import cli_release_state as rs
        budget = rs.MERGED_UNRELEASED_META_BUDGET
        self.assertIn("not read yet", self._sweep(
            budget + 2, lambda pr: ("t", "Closes #%d" % (pr + 100))))
        self.assertIn("not read yet", self._sweep(2, lambda pr: None))
        self.assertIn("M hidden", self._sweep(
            rs.MERGED_UNRELEASED_MAX_PRS + 1, lambda pr: ("t", "")))
        self.assertEqual(self._sweep(2, lambda pr: ("t", "")), "")


if __name__ == "__main__":
    unittest.main()
