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
import time
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

    def test_c_beats_ops_wait(self):
        # "waiting for the deploy": the fix is live now
        self.assertEqual(_bucket(_row(1, "ops-wait"), on_main=ts.DEPLOYED), "C")

    def test_a_hand_off_label_keeps_c_open(self):
        # review round 1: a fork-no-merge hand-off of a SECOND fix has no PR,
        # so the hand-off label is the only sign; C would hide it from the
        # gatekeeper's I (the #943 class)
        for extra in ("ready-for-review", "needs-gatekeeper", "gk-processing"):
            got = ts.classify(_row(1, extra), ts.Facts(on_main=ts.DEPLOYED),
                              ts.Box())
            self.assertEqual(got[0], "I", extra)
            self.assertIn(extra, got[1])

    def test_another_pending_fix_keeps_c_open(self):
        # review round 1: one fix is live, another is merged-not-released, in
        # CI, or an open PR (any state) — the ticket is not done
        row = _row(1, "bug")
        self.assertEqual(_bucket(row, on_main=ts.DEPLOYED, merged=True), "M")
        self.assertEqual(_bucket(row, on_main=ts.DEPLOYED, pipeline=True), "P")
        self.assertEqual(_bucket(row, on_main=ts.DEPLOYED, open_pr=True), "I")
        self.assertIn("open", ts.classify(
            row, ts.Facts(on_main=ts.DEPLOYED, open_pr=True), ts.Box())[1])

    def test_unreadable_labels_never_read_as_done(self):
        # review round 1: the C vetoes cannot be checked on unreadable labels
        for row in ({"labels": None}, {"labels": "garbage"}, "not-a-row"):
            self.assertNotEqual(
                ts.classify(row, ts.Facts(on_main=ts.RELEASED), ts.Box())[0],
                "C", row)

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


_NOW = 1790244000.0                       # 2026-09-24T10:00:00Z
_FRESH = "2026-09-24T09:30:00Z"           # a head commit 30 min old


def _graphql(*prs):
    """A GraphQL payload for open PRs: (number, title, body, rollup, closes)
    plus optional (draft, head committedDate)."""
    nodes = []
    for number, title, body, state, closes, *rest in prs:
        draft = rest[0] if rest else False
        date = rest[1] if len(rest) > 1 else _FRESH
        nodes.append({
            "number": number, "title": title, "body": body, "isDraft": draft,
            "closingIssuesReferences": {"nodes": [
                {"number": c, "repository": {"nameWithOwner": "o/r"}}
                if isinstance(c, int) else
                {"number": c[0], "repository": {"nameWithOwner": c[1]}}
                for c in closes]},
            "commits": {"nodes": [{"commit": {"committedDate": date,
                                              "statusCheckRollup": (
                {"state": state} if state else None)}}]}})
    return {"data": {"repository": {"pullRequests": {"nodes": nodes}}}}


def _reasons(numbers=(), reopened=()):
    """A stateReason GraphQL answer: iN aliases, REOPENED for `reopened`."""
    return {"data": {"repository": {
        "i%d" % n: {"stateReason": "REOPENED" if n in reopened else None}
        for n in numbers}}}


def _gh(pr_payload, numbers=(), reopened=()):
    """gh_fn answering the PR query and the stateReason query (ruling 1)."""
    def run(args):
        if any("stateReason" in a for a in args):
            return json.dumps(_reasons(numbers, reopened))
        return json.dumps(pr_payload)
    return run


class PipelineFact(unittest.TestCase):
    def setUp(self):
        import cli_ticket_facts
        self.f = cli_ticket_facts

    def test_running_checks_link_the_ticket(self):
        got = self.f.pipeline_numbers(_graphql(
            (50, "#5 fix the thing", "", "PENDING", ()),
            (51, "docs", "Closes #7", "PENDING", ()),
            (52, "other", "", "PENDING", (9,)),
            (53, "#11 green", "", "SUCCESS", ()),
            (54, "#12 red", "", "FAILURE", ()),
            (55, "follow-up to #13", "see #14", "PENDING", ()),
            (56, "#15 no checks", "", None, ())), now=_NOW)
        self.assertEqual(got, frozenset({5, 7, 9, 13}))

    def test_expected_draft_and_stuck_checks_are_not_p(self):
        # review round 1: EXPECTED (a required check that never reported) and
        # a head commit older than PIPELINE_MAX_AGE_S are stuck, not "in CI";
        # a draft is still someone's work in progress
        old = "2026-09-24T01:00:00Z"
        payload = _graphql((60, "#20 expected", "", "EXPECTED", ()),
                           (61, "#21 draft", "", "PENDING", (), True),
                           (62, "#22 stuck", "", "PENDING", (), False, old),
                           (63, "#23 no date", "", "PENDING", (), False, None))
        self.assertEqual(self.f.pipeline_numbers(payload, now=_NOW),
                         frozenset({23}))
        # every linked open PR, in any state, is an open PR (the C veto)
        self.assertEqual(set(self.f.open_pr_states(payload, now=_NOW)),
                         {20, 21, 22, 23})

    def test_an_unreadable_payload_is_unknown(self):
        for bad in (None, [], "garbage", {"data": None},
                    {"errors": [{"message": "x"}]}, {"data": {"repository":
                                                             None}}):
            self.assertIsNone(self.f.pipeline_numbers(bad), bad)
            self.assertIsNone(self.f.open_pr_states(bad), bad)

    def test_read_prs_is_one_graphql_call(self):
        calls = []

        def gh(args):
            calls.append(args)
            return json.dumps(_graphql((50, "#5 x", "", "PENDING", ()),
                                       (51, "#6 y", "", "FAILURE", ())))
        self.assertEqual(self.f.read_prs("o/r", gh, now=_NOW),
                         {5: True, 6: False})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["api", "graphql"])
        self.assertIsNone(self.f.read_prs("o/r", lambda a: ""))
        self.assertIsNone(self.f.read_prs("", gh))


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

        def deploy_fn(root, slug):
            calls["deploy"] += 1
            return deploy

        return calls, self.f.refresh(
            "/repo", "o/r", {1, 2, 3}, merged={4}, handed={2: True},
            home=home, now=now,
            gh_fn=gh or _gh(_graphql((50, "#1 x", "", "PENDING", ())),
                            numbers=(3,)),
            released_fn=lambda root, nums, slug: (
                {3: "abc"} if released is None else released),
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
        payload = json.dumps(_graphql((60, "#2 fix", "", "PENDING", (), False,
                                       time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                     time.gmtime()))))
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'echo "$*" >> %s\n' % log +
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
            "  *stateReason*) echo '%s';;\n" % json.dumps(_reasons((3,))) +
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
            # ROZHODNUTÉ ruling 2: C ("done, close me") is owed, so it counts
            # in --count and the /goal stop-proof: I 1 + C 1 (P stays out)
            self.assertEqual(r.stdout.strip(), "2", r.stderr)
            self.assertNotIn("graphql", Path(log).read_text())
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals", "--explain"],
                capture_output=True, text=True, env=env, cwd=repo)
            self.assertIn("# explain: I=1 M=0 U=0 W=0 gk=0 P=1 C=1",
                          r.stdout.splitlines(), r.stdout + r.stderr)
            # review round 1: C left I, so --list names it (someone closes it)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals", "--list"],
                capture_output=True, text=True, env=env, cwd=repo)
            done = [ln for ln in r.stdout.splitlines() if ln.startswith("3\t")]
            self.assertEqual(len(done), 1, r.stdout + r.stderr)
            self.assertEqual(done[0].split("\t")[2], "released", done)

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


def _git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    return subprocess.run(["git", "-C", repo, *args], check=True, env=env,
                          capture_output=True, text=True).stdout.strip()


class ReviewRound1(unittest.TestCase):
    """Review round 1 (two adversarial reviews): the facts must never read a
    ticket as done too early, and a broken read must never crash a count."""

    def setUp(self):
        import cli_ticket_facts
        self.f = cli_ticket_facts

    def test_bump_at_cut_repo_reads_the_release_version(self):
        # odoo-erp bumps its version only at the release cut: the version at
        # the fix commit is the PREVIOUS release, so it must be read at the
        # first main commit that contains the fix (the release merge)
        with TemporaryDirectory() as repo:
            _git(repo, "init", "-q", "-b", "main")
            Path(repo, "VERSION").write_text("1.0.0\n")
            _git(repo, "add", "VERSION")
            _git(repo, "commit", "-q", "-m", "base 1.0.0")
            _git(repo, "checkout", "-q", "-b", "develop")
            Path(repo, "fix.txt").write_text("x\n")
            _git(repo, "add", "fix.txt")
            _git(repo, "commit", "-q", "-m", "fix #7")
            fix = _git(repo, "rev-parse", "HEAD")
            Path(repo, "VERSION").write_text("1.1.0\n")
            _git(repo, "commit", "-q", "-am", "chore(release): cut 1.1.0")
            _git(repo, "checkout", "-q", "main")
            _git(repo, "merge", "-q", "--no-ff", "-m", "release 1.1.0",
                 "develop")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            self.assertEqual(
                self.f._default_version_at(repo, "VERSION", fix), "1.1.0")
            # PROD still on 1.0.0 → the fix waits for the deploy (M), not C
            self.assertEqual(self.f.on_main_states(
                {7: fix}, [{"main_version": "1.1.0", "prod_version": "1.0.0"}],
                lambda oid: self.f._default_version_at(repo, "VERSION", oid)),
                {7: ts.PENDING})

    def test_a_fix_on_the_main_chain_reads_its_own_version(self):
        # a two-branch / direct-to-main fix: a later bump on main must not be
        # read as the version that shipped it
        with TemporaryDirectory() as repo:
            _git(repo, "init", "-q", "-b", "main")
            Path(repo, "VERSION").write_text("2.0.0\n")
            _git(repo, "add", "VERSION")
            _git(repo, "commit", "-q", "-m", "fix #9 at 2.0.0")
            fix = _git(repo, "rev-parse", "HEAD")
            Path(repo, "VERSION").write_text("2.1.0\n")
            _git(repo, "commit", "-q", "-am", "bump 2.1.0")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            # review round 2: a fix ON the main chain may have shipped in a
            # later fast-forwarded release (bump-at-cut): its version cannot
            # be told from the chain, so it is unknown, never read at the fix
            self.assertIsNone(
                self.f._default_version_at(repo, "VERSION", fix))

    def test_an_oid_that_is_not_a_hash_is_never_passed_to_git(self):
        for bad in ("--output=/tmp/x", "HEAD", "", None, "abc g"):
            self.assertIsNone(self.f._default_version_at("/", "VERSION", bad))

    def test_a_partial_or_unreadable_deploy_read_is_unknown(self):
        from unittest import mock
        from watchdog import deploy_state as ds
        decl = {"main_version_file": "V", "instances": [
            {"name": "a"}, {"name": "b"}, {"name": "c"}]}
        two = [{"main_version": "1", "prod_version": "1"}] * 2
        with mock.patch.object(ds, "deploy_declaration",
                               return_value=(decl, True)), \
                mock.patch.object(ds, "fetch_deploy_state", return_value=two):
            got = self.f._default_deploy("/r", "o/r")
        self.assertEqual(got["instances"], [])        # fewer than declared
        with mock.patch.object(ds, "deploy_declaration",
                               return_value=(None, False)):
            got = self.f._default_deploy("/r", "o/r")
        self.assertEqual(got, {"instances": [], "version_file": None})
        self.assertEqual(self.f.on_main_states({1: "a"}, got["instances"],
                                               lambda o: "1"), {})

    def test_the_declaration_matches_the_canonical_slug(self):
        from watchdog import deploy_state as ds
        with TemporaryDirectory() as tmp:
            reg = Path(tmp, "projects-registry.json")
            reg.write_text(json.dumps([{"path": "/elsewhere",
                                        "github_repo": "Owner/Repo",
                                        "deploy_state": {"instances": []}}]))
            self.assertEqual(ds.deploy_declaration(tmp, str(reg), "owner/repo"),
                             ({"instances": []}, True))
            self.assertEqual(ds.deploy_declaration(tmp, str(reg), "a/b"),
                             (None, True))
            self.assertEqual(ds.deploy_declaration(
                tmp, str(Path(tmp, "missing.json"))), (None, False))

    def test_open_prs_flow_through_the_cache(self):
        with TemporaryDirectory() as home:
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            payload = json.dumps(_graphql(
                (50, "#1 x", "", "FAILURE", ()),
                (51, "#2 y", "", "PENDING", (), False, stamp)))

            def gh(args):
                return payload
            facts = self.f.refresh("/repo", "o/r", {1, 2, 3}, home=home,
                                   gh_fn=gh, released_fn=lambda *a: {},
                                   deploy_fn=lambda r, s: None)
            self.assertEqual((facts.pipeline, facts.open_pr),
                             (frozenset({2}), frozenset({1, 2})))
            loaded = self.f.load("/repo", home=home)
            self.assertEqual(loaded.open_pr, frozenset({1, 2}))

    def test_a_corrupt_cache_never_crashes_a_count(self):
        with TemporaryDirectory() as home:
            path = self.f.cache_path("/repo", home)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "ts": 1000, "pipeline": [True, 5, "6"], "open_pr": [False, 5],
                "reopened": [],
                "on_main": {"\u00b2": "deployed", "7": "deployed",
                            "8": "bogus"}}))
            facts = self.f.load("/repo", home=home, now=1000)
        self.assertEqual(facts.pipeline, frozenset({5}))
        self.assertEqual(facts.open_pr, frozenset({5}))
        self.assertEqual(facts.on_main, {7: ts.DEPLOYED})

    def test_a_failed_refresh_forgets_the_old_facts(self):
        from unittest import mock
        import cli_ticket_route
        with TemporaryDirectory() as home:
            path = self.f.cache_path("/repo", home)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"ts": time.time(), "pipeline": [1],
                                        "on_main": {}}))
            with mock.patch.object(self.f, "refresh",
                                   side_effect=RuntimeError("boom")), \
                    mock.patch.object(self.f.statusbar, "cache_dir",
                                      return_value=path.parent):
                b, facts = cli_ticket_route.footer(
                    {1: _row(1, "bug")}, "/repo", "o/r", ())
            self.assertFalse(path.exists())
            self.assertEqual(set(b["I"]), {1})
            self.assertEqual(facts.pipeline, frozenset())


class ReviewRound2(unittest.TestCase):
    """Review round 2: an UNKNOWN fact must never let C through."""

    def setUp(self):
        import cli_ticket_facts
        self.f = cli_ticket_facts

    def _released(self, home, gh, numbers=(5,), now=None):
        return self.f.refresh("/repo", "o/r", set(numbers), home=home, now=now,
                              gh_fn=gh, deploy_fn=lambda r, s: None,
                              released_fn=lambda *a: {5: ["abc1234"]})

    def test_a_failed_pr_read_never_gives_c(self):
        with TemporaryDirectory() as home:
            facts = self._released(home, lambda a: "")
            self.assertNotEqual(
                ts.classify(_row(5, "bug"), facts.of(5), ts.Box())[0], "C")
            loaded = self.f.load("/repo", home=home)
            self.assertNotEqual(
                ts.classify(_row(5, "bug"), loaded.of(5), ts.Box())[0], "C")

    def test_a_reused_pr_read_covers_a_new_ticket(self):
        with TemporaryDirectory() as home:
            gh = _gh(_graphql((90, "#6 still open", "", "FAILURE", ())),
                     numbers=(5, 6))
            self._released(home, gh, numbers=(5,), now=1000)
            facts = self.f.refresh(
                "/repo", "o/r", {5, 6}, home=home, now=1030, gh_fn=gh,
                deploy_fn=lambda r, s: None,
                released_fn=lambda *a: {6: ["abc1234"]})
            self.assertNotEqual(
                ts.classify(_row(6, "bug"), facts.of(6), ts.Box())[0], "C")

    def test_a_fix_not_on_main_reads_no_version(self):
        with TemporaryDirectory() as repo:
            _git(repo, "init", "-q", "-b", "main")
            Path(repo, "VERSION").write_text("1.0.0\n")
            _git(repo, "add", "VERSION")
            _git(repo, "commit", "-q", "-m", "base")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            _git(repo, "checkout", "-q", "-b", "side")
            _git(repo, "commit", "-q", "--allow-empty", "-m", "fix #3")
            fix = _git(repo, "rev-parse", "HEAD")
            _git(repo, "checkout", "-q", "main")
            _git(repo, "commit", "-q", "--allow-empty", "-m", "later")
            _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            self.assertIsNone(self.f._default_version_at(repo, "VERSION", fix))

    def test_a_cross_repo_closing_ref_and_a_partial_page(self):
        payload = _graphql((70, "other", "", "PENDING", ((12, "x/other"),)))
        self.assertEqual(self.f.open_pr_states(payload, now=_NOW, slug="o/r"),
                         {})
        payload["data"]["repository"]["pullRequests"]["pageInfo"] = {
            "hasNextPage": True}
        self.assertIsNone(self.f.open_pr_states(payload, now=_NOW,
                                                slug="o/r"))


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
