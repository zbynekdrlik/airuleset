"""#1141 slice 3 — ONE route through `bucketize()` and the machine-fact buckets.

- `cli_ticket_state.bucketize(rows, facts, box)` is the ONE route (label
  partition → P/M/C facts → gk) behind the footer, `core-quals` and
  `slice-quals`; `_split_merged_unreleased` and its separate M veto are gone.
- P = an open linked PR whose checks are still running.
- M = merged to the integration branch but not on main (#1083), AND on main
  but not yet on PROD where the repo declares a deploy state.
- C = "done, close me": the fix is on main and deployed (or released, when
  the repo declares no deploy state), and no owner question is open.
- An owner question is decided first, so the M split needs no U veto.
- The facts are read on the footer REFRESH path only (one GraphQL query) and
  cached; the quals commands read the cache (zero gh). Unknown facts → the
  old behaviour, never a wrong number.

All facts come from fake adapters here — no live gh, no network.
"""

import inspect
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import cli_quals_cmd  # noqa: E402
import cli_ticket_state as ts  # noqa: E402
import statusbar  # noqa: E402


def _labels(*names):
    return [{"name": n} for n in names]


def _row(n, *names):
    return {"number": n, "title": "t%d" % n, "labels": _labels(*names)}


def _bucket(row, box=None, **facts):
    return ts.classify(row, ts.Facts(**facts), box or ts.Box())[0]


_OWN = "david1"
_SLICE = ts.Box(own_stream=_OWN)


class NewBuckets(unittest.TestCase):
    def test_p_and_c_are_buckets(self):
        self.assertIn("P", ts.BUCKETS)
        self.assertIn("C", ts.BUCKETS)

    def test_c_when_on_main_and_deployed(self):
        row = _row(1, "bug")
        self.assertEqual(_bucket(row, on_main=ts.DEPLOYED), "C")
        self.assertEqual(_bucket(row, on_main=ts.RELEASED), "C")
        reason = ts.classify(row, ts.Facts(on_main=ts.DEPLOYED), ts.Box())[1]
        self.assertIn("close", reason)

    def test_c_beats_ops_wait_and_hand_off_labels(self):
        # "waiting for the deploy" and a stale hand-off label: the fix is live
        for extra in ("ops-wait", "ready-for-review", "needs-gatekeeper",
                      "gk-processing"):
            self.assertEqual(_bucket(_row(1, extra), on_main=ts.DEPLOYED),
                             "C", extra)

    def test_an_owner_question_beats_c(self):
        for q in ("needs-answer", "needs-decision", "needs-owner-action",
                  "needs-acceptance"):
            self.assertEqual(_bucket(_row(1, q), on_main=ts.DEPLOYED), "U", q)

    def test_c_is_vetoed_by_rework_acceptance_and_verification(self):
        self.assertEqual(_bucket(_row(1, "prio:bounce"), on_main=ts.DEPLOYED),
                         "I")
        self.assertEqual(_bucket(_row(1, "needs-acceptance", "ops-wait"),
                                 on_main=ts.DEPLOYED), "W")
        self.assertEqual(_bucket(_row(1, "verify-on-copy"),
                                 on_main=ts.DEPLOYED), "I")
        reason = ts.classify(_row(1, "verify-on-copy"),
                             ts.Facts(on_main=ts.DEPLOYED), ts.Box())[1]
        self.assertIn("verify-on-copy", reason)

    def test_p_when_a_linked_pr_is_in_ci(self):
        self.assertEqual(_bucket(_row(1, "bug"), pipeline=True), "P")
        self.assertEqual(_bucket(_row(1, "ops-wait"), pipeline=True), "P")
        self.assertEqual(_bucket(_row(1, "needs-answer"), pipeline=True), "U")

    def test_p_before_gk_on_the_slice_box(self):
        row = _row(1, "stream:david1")
        self.assertEqual(_bucket(row, _SLICE, pipeline=True, handed=True),
                         "P")
        self.assertEqual(_bucket(row, _SLICE, handed=True), "gk")

    def test_on_main_not_deployed_is_M(self):
        row = _row(1, "bug")
        self.assertEqual(_bucket(row, on_main=ts.PENDING), "M")
        reason = ts.classify(row, ts.Facts(on_main=ts.PENDING), ts.Box())[1]
        self.assertIn("PROD", reason)
        self.assertEqual(_bucket(_row(1, "prio:bounce"), on_main=ts.PENDING),
                         "I")

    def test_released_stream_ticket_is_C_when_facts_are_known(self):
        row = _row(1, "stream:david1")
        self.assertEqual(_bucket(row, _SLICE, handed="released",
                                 on_main=ts.DEPLOYED), "C")
        # unknown facts: the #1009 route stays
        self.assertEqual(_bucket(row, _SLICE, handed="released"), "gk")


class MVetoRemoved(unittest.TestCase):
    """An owner question is decided first, so the M split has no U veto."""

    def test_sent_acceptance_merged_is_M(self):
        self.assertEqual(_bucket(_row(1, "needs-acceptance", "ops-wait"),
                                 merged=True), "M")

    def test_foreign_question_on_a_slice_box_merged_is_M(self):
        row = _row(1, "needs-answer", "stream:montalu1")
        self.assertEqual(_bucket(row, _SLICE), "I")          # #654
        self.assertEqual(_bucket(row, _SLICE, merged=True), "M")

    def test_own_question_merged_stays_U_and_bounce_stays_I(self):
        self.assertEqual(_bucket(_row(1, "needs-answer"), merged=True), "U")
        self.assertEqual(_bucket(_row(1, "prio:bounce"), merged=True), "I")

    def test_the_separate_m_veto_is_gone(self):
        self.assertFalse(hasattr(ts, "leaves_to_merged"))
        self.assertFalse(hasattr(cli_quals, "_split_merged_unreleased"))
        self.assertFalse(hasattr(airuleset, "_split_merged_unreleased"))


class Bucketize(unittest.TestCase):
    def test_every_row_lands_in_exactly_one_bucket(self):
        rows = {n: _row(n, *labels) for n, labels in enumerate((
            ("bug",), ("needs-answer",), ("ops-wait",), ("ready-for-review",),
            ("prio:bounce",), ("needs-answer", "stream:montalu1"),
            ("verify-on-copy",), ("bug",), ("bug",), ("bug",)), start=1)}
        facts = ts.TicketFacts(merged=frozenset({8}), pipeline=frozenset({9}),
                               on_main={10: ts.DEPLOYED, 3: ts.PENDING})
        for box in (ts.Box(), _SLICE):
            b = ts.bucketize(rows, facts, box)
            self.assertEqual(set(b), set(ts.BUCKETS) | {ts.HIDDEN})
            seen = [n for bucket in b.values() for n in bucket]
            self.assertEqual(sorted(seen), sorted(rows))
            for bucket, members in b.items():
                for n, row in members.items():
                    self.assertEqual(
                        ts.classify(row, facts.of(n), box)[0], bucket)
        b = ts.bucketize(rows, facts, ts.Box())
        self.assertEqual(set(b["M"]), {3, 8})
        self.assertEqual(set(b["P"]), {9})
        self.assertEqual(set(b["C"]), {10})

    def test_unknown_facts_are_the_old_label_partition(self):
        rows = {n: _row(n, *labels) for n, labels in enumerate((
            ("bug",), ("needs-answer",), ("ops-wait",)), start=1)}
        b = ts.bucketize(rows, ts.TicketFacts(), ts.Box())
        w, u, o = airuleset._partition_workable(rows)
        self.assertEqual((b["I"], b["U"], b["W"]), (w, u, o))
        self.assertEqual(b["P"], {})
        self.assertEqual(b["C"], {})

    def test_handed_rows_are_gk_on_the_slice_box_only(self):
        rows = {1: _row(1, "stream:david1"), 2: _row(2, "stream:david1")}
        facts = ts.TicketFacts(handed={1: True})
        self.assertEqual(set(ts.bucketize(rows, facts, _SLICE)["gk"]), {1})
        self.assertEqual(ts.bucketize(rows, facts, ts.Box())["gk"], {})

    def test_one_route_in_every_consumer(self):
        # the footer and both quals commands classify through the ONE route,
        # never a partition + a separate M split
        for fn in (airuleset.cmd_tickets_status, cli_quals_cmd.cmd_core_quals,
                   cli_quals_cmd.cmd_slice_quals):
            src = inspect.getsource(fn)
            self.assertNotIn("_partition_workable(", src, fn.__name__)
            self.assertNotIn("_split_merged_unreleased", src, fn.__name__)
            self.assertIn("cli_ticket_route", src, fn.__name__)


def _graphql(*prs):
    """A GraphQL payload for open PRs: (number, title, body, rollup, closes)."""
    nodes = []
    for number, title, body, state, closes in prs:
        nodes.append({
            "number": number, "title": title, "body": body,
            "closingIssuesReferences": {"nodes": [{"number": c}
                                                  for c in closes]},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": (
                {"state": state} if state else None)}}]}})
    return {"data": {"repository": {"pullRequests": {"nodes": nodes}}}}


class PipelineFact(unittest.TestCase):
    def setUp(self):
        import cli_ticket_facts
        self.f = cli_ticket_facts

    def test_running_checks_link_the_ticket(self):
        got = self.f.pipeline_numbers(_graphql(
            (50, "#5 fix the thing", "", "PENDING", ()),
            (51, "docs", "Closes #7", "EXPECTED", ()),
            (52, "other", "", "PENDING", (9,)),
            (53, "#11 green", "", "SUCCESS", ()),
            (54, "#12 red", "", "FAILURE", ()),
            (55, "follow-up to #13", "see #14", "PENDING", ()),
            (56, "#15 no checks", "", None, ())))
        self.assertEqual(got, frozenset({5, 7, 9, 13}))

    def test_an_unreadable_payload_is_unknown(self):
        for bad in (None, [], "garbage", {"data": None},
                    {"errors": [{"message": "x"}]}, {"data": {"repository":
                                                             None}}):
            self.assertIsNone(self.f.pipeline_numbers(bad), bad)

    def test_read_pipeline_is_one_graphql_call(self):
        calls = []

        def gh(args):
            calls.append(args)
            return json.dumps(_graphql((50, "#5 x", "", "PENDING", ())))
        self.assertEqual(self.f.read_pipeline("o/r", gh), frozenset({5}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["api", "graphql"])
        self.assertIsNone(self.f.read_pipeline("o/r", lambda a: ""))
        self.assertIsNone(self.f.read_pipeline("", gh))


class OnMainFact(unittest.TestCase):
    def setUp(self):
        import cli_ticket_facts
        self.f = cli_ticket_facts

    def _inst(self, main, *prods):
        return [{"instance": "i%d" % k, "main_version": main,
                 "prod_version": p} for k, p in enumerate(prods)]

    def test_no_deploy_declaration_means_released(self):
        self.assertEqual(self.f.on_main_states({1: "a"}, None, None),
                         {1: ts.RELEASED})

    def test_prod_at_main_means_every_ticket_deployed(self):
        got = self.f.on_main_states({1: "a", 2: "b"},
                                    self._inst("2.5.0", "2.5.0", "2.5"),
                                    lambda oid: self.fail("not needed"))
        self.assertEqual(got, {1: ts.DEPLOYED, 2: ts.DEPLOYED})

    def test_main_ahead_of_prod_is_decided_per_ticket(self):
        versions = {"a": "2.4.0", "b": "2.5.0", "c": None}
        got = self.f.on_main_states({1: "a", 2: "b", 3: "c"},
                                    self._inst("2.5.0", "2.4.0", "2.4.1"),
                                    versions.get)
        # 1 is on PROD, 2 waits for the deploy, 3 cannot be told → unknown
        self.assertEqual(got, {1: ts.DEPLOYED, 2: ts.PENDING})

    def test_an_unread_prod_version_is_unknown(self):
        self.assertEqual(self.f.on_main_states(
            {1: "a"}, self._inst("2.5.0", "2.5.0", None), lambda o: "1.0"),
            {})
        self.assertEqual(self.f.on_main_states({1: "a"}, [], lambda o: "1"),
                         {})


class FactsCache(unittest.TestCase):
    def setUp(self):
        import cli_ticket_facts
        self.f = cli_ticket_facts

    def _refresh(self, home, now, gh=None, deploy=None, released=None):
        calls = {"deploy": 0}

        def deploy_fn(root):
            calls["deploy"] += 1
            return deploy

        return calls, self.f.refresh(
            "/repo", "o/r", {1, 2, 3}, merged={4}, handed={2: True},
            home=home, now=now,
            gh_fn=gh or (lambda a: json.dumps(
                _graphql((50, "#1 x", "", "PENDING", ())))),
            released_fn=lambda root, nums, slug: released or {3: "abc"},
            deploy_fn=deploy_fn,
            version_at_fn=lambda root, vfile, oid: None)

    def test_refresh_writes_and_the_quals_path_reads_it(self):
        with TemporaryDirectory() as home:
            _calls, facts = self._refresh(home, 1000)
            self.assertEqual(facts.pipeline, frozenset({1}))
            self.assertEqual(facts.on_main, {3: ts.RELEASED})
            self.assertEqual(facts.of(4).merged, True)
            self.assertEqual(facts.of(2).handed, True)
            loaded = self.f.load("/repo", merged={4}, home=home, now=1100)
            self.assertEqual(loaded.pipeline, frozenset({1}))
            self.assertEqual(loaded.on_main, {3: ts.RELEASED})
            self.assertEqual(loaded.merged, frozenset({4}))

    def test_stale_or_missing_or_corrupt_cache_is_unknown(self):
        with TemporaryDirectory() as home:
            self.assertEqual(self.f.load("/repo", home=home).pipeline,
                             frozenset())
            self._refresh(home, 1000)
            stale = self.f.load("/repo", home=home,
                                now=1000 + self.f.FACTS_MAX_AGE_S + 1)
            self.assertEqual((stale.pipeline, stale.on_main),
                             (frozenset(), {}))
            self.f.cache_path("/repo", home).write_text("{nope")
            self.assertEqual(self.f.load("/repo", home=home, now=1000).on_main,
                             {})

    def test_a_gh_error_leaves_the_pipeline_unknown(self):
        with TemporaryDirectory() as home:
            _c, facts = self._refresh(home, 1000, gh=lambda a: "")
            self.assertEqual(facts.pipeline, frozenset())
            data = json.loads(self.f.cache_path("/repo", home).read_text())
            self.assertIsNone(data["pipeline"])

    def test_the_deploy_read_is_cached(self):
        inst = [{"instance": "x", "main_version": "1.0",
                 "prod_version": "1.0"}]
        deploy = {"instances": inst, "version_file": "VERSION"}
        with TemporaryDirectory() as home:
            c1, f1 = self._refresh(home, 1000, deploy=deploy)
            c2, f2 = self._refresh(home, 1000 + 60, deploy=deploy)
            self.assertEqual((c1["deploy"], c2["deploy"]), (1, 0))
            self.assertEqual(f2.on_main, {3: ts.DEPLOYED})
            c3, _ = self._refresh(home, 1000 + self.f.DEPLOY_TTL_S + 1,
                                  deploy=deploy)
            self.assertEqual(c3["deploy"], 1)

    def test_no_released_ticket_means_no_deploy_read(self):
        with TemporaryDirectory() as home:
            calls, facts = self._refresh(home, 1000, released={})
            self.assertEqual(calls["deploy"], 0)
            self.assertEqual(facts.on_main, {})


class FooterRender(unittest.TestCase):
    def _seg(self, **extra):
        with TemporaryDirectory() as home:
            cwd = "/x/repo"
            path = statusbar.cache_dir(home) / (statusbar.cwd_key(cwd)
                                                + ".json")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"ts": 10 ** 12, "open": 2,
                                        "root": "/x/repo", **extra}))
            return statusbar.tickets_segment(cwd, now=10 ** 12, home=home,
                                             spawn=False)

    def test_p_and_c_render_only_when_positive(self):
        seg = self._seg(pipeline=1, done=3, merged_unreleased=2)
        self.assertIn("· P 1", seg)
        self.assertIn("· C 3", seg)
        # lifecycle order: I · P · M · C
        self.assertLess(seg.index("· P 1"), seg.index("· M 2"))
        self.assertLess(seg.index("· M 2"), seg.index("· C 3"))
        seg0 = self._seg(pipeline=0, done=0)
        self.assertNotIn("· P", seg0)
        self.assertNotIn("· C", seg0)
        self.assertNotIn("· P", self._seg())          # a legacy cache

    def test_a_stale_refresh_carries_p_and_c_forward(self):
        entry = statusbar.carry_forward_stale(
            {"ts": 2, "open": None}, {"ts": 1, "open": 3, "pipeline": 1,
                                      "done": 2}, "gh failure")
        self.assertEqual((entry["pipeline"], entry["done"]), (1, 2))


class ExplainPandC(unittest.TestCase):
    def test_p_and_c_rows_print_with_reasons_and_totals(self):
        rows = {1: _row(1, "bug"), 2: _row(2, "bug"), 3: _row(3, "bug")}
        facts = ts.TicketFacts(pipeline=frozenset({1}),
                               on_main={2: ts.DEPLOYED})
        out = ts.explain_lines(ts.bucketize(rows, facts, ts.Box()), ts.Box(),
                               facts)
        by_num = {ln.split("\t")[0]: ln.split("\t")[1] for ln in out
                  if ln[0].isdigit()}
        self.assertEqual(by_num, {"1": "P", "2": "C", "3": "I"})
        self.assertNotIn("mismatch", "\n".join(out))
        self.assertEqual(out[-1], "# explain: I=1 M=0 U=0 W=0 gk=0 P=1 C=1")


_OBLIG = json.dumps([
    {"number": 1, "title": "plain", "labels": _labels("bug")},
    {"number": 2, "title": "in ci", "labels": _labels("bug")},
    {"number": 3, "title": "shipped", "labels": _labels("bug")},
])


class CliFacts(unittest.TestCase):
    """The footer refresh reads the facts (fake gh) and the quals command
    reads the cache the refresh wrote — zero gh for the facts there."""

    def _fake_gh(self, bindir, log):
        payload = json.dumps(_graphql((60, "#2 fix", "", "PENDING", ())))
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'echo "$*" >> %s\n' % log +
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
            "  *graphql*) echo '%s';;\n" % payload +
            '  *"--search label:autopilot-skip"*) echo 0;;\n'
            "  *) echo '%s';;\n" % _OBLIG +
            'esac\n')
        gh.chmod(0o755)

    def _mkrepo(self, repo):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
        subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True,
                       env=env, capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "--allow-empty", "-q",
                        "-m", "Merge pull request #9 from s/3-x"], check=True,
                       env=env, capture_output=True)
        subprocess.run(["git", "-C", repo, "update-ref",
                        "refs/remotes/origin/main", "HEAD"], check=True,
                       env=env, capture_output=True)
        return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              check=True, capture_output=True,
                              text=True).stdout.strip()

    def test_refresh_then_count_and_footer_agree(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            oid = self._mkrepo(repo)
            # PR #9 (fix for #3) was seen merged earlier; it is on main now
            cache = Path(home) / ".claude" / "tickets-status" / \
                "pr-issues-zbynekdrlik__demo.json"
            cache.parent.mkdir(parents=True)
            cache.write_text(json.dumps({"9": {"issues": [3], "oid": oid}}))
            log = str(Path(bindir) / "gh.log")
            self._fake_gh(bindir, log)
            env = {**os.environ, "HOME": home,
                   "PATH": f"{bindir}:{os.environ['PATH']}"}
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            entry = json.loads((statusbar.cache_dir(home)
                                / (statusbar.cwd_key(repo) + ".json"))
                               .read_text())
            self.assertEqual((entry["open"], entry.get("pipeline"),
                              entry.get("done")), (1, 1, 1), entry)
            self.assertEqual(entry.get("pipeline_numbers"), [2])
            self.assertEqual(entry.get("done_numbers"), [3])
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("· P 1", seg)
            self.assertIn("· C 1", seg)
            # the quals count reads the cached facts: same I, no graphql call
            Path(log).write_text("")
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals", "--count"],
                capture_output=True, text=True, env=env, cwd=repo)
            self.assertEqual(r.stdout.strip(), "1", r.stderr)
            self.assertNotIn("graphql", Path(log).read_text())
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals", "--explain"],
                capture_output=True, text=True, env=env, cwd=repo)
            self.assertIn("# explain: I=1 M=0 U=0 W=0 gk=0 P=1 C=1",
                          r.stdout.splitlines(), r.stdout + r.stderr)

    def test_without_the_facts_cache_the_count_is_the_old_one(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._mkrepo(repo)
            self._fake_gh(bindir, str(Path(bindir) / "gh.log"))
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals", "--count"],
                capture_output=True, text=True, cwd=repo,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.stdout.strip(), "3", r.stderr)


if __name__ == "__main__":
    unittest.main()
