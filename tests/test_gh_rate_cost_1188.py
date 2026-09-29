"""#1188 — the gh rate-guard records GraphQL COST, not only call counts.

Origin: 29.9.2026, the gk identity ran out of GraphQL three times. GitHub bills
GraphQL per query COST, and the #1087 accounting counted CALLS (~380 calls in
the exhausted hour cannot explain ~4750 points), so the EXHAUSTED line could
not name the spender. Locked here:

- every airuleset-owned GraphQL query asks for `rateLimit { cost remaining }`
  and its consumer records the cost under its label (`q:<label>`) in the daily
  `gql-cost-<day>.json` next to the call counts;
- a foreign/raw GraphQL-shaped call opens a sampling window; at most once per
  minute the free GraphQL `rateLimit` object is read and the graphql `used` delta (minus
  the exact owned cost) is attributed pro rata to the window's call shapes
  (`~<shape>`, approximate);
- the EXHAUSTED line ranks the top spenders by COST;
- the two nested queries are capped by COST (measured with `rateLimit(dryRun:
  true)` on odoo-erp: a connection costs its PARENTS' page size / 100, so the
  PR query pages at <= 60 PRs for 1 point, and the prefetch amortises its flat
  1 point per page over >= 25 issues) with results unchanged;
- the footer refresher's gh subprocesses carry `AIRULESET_GH_POLLER=1`.

Hermetic: gh_rate_dir redirected to a tmp dir; every gh / subprocess is faked.
"""
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import airuleset  # noqa: E402
import cli_gh_rate  # noqa: E402
import cli_gh_rate_cost  # noqa: E402
import cli_quals  # noqa: E402
import cli_ticket_facts  # noqa: E402
import statusbar  # noqa: E402
from ticket_facts_testlib import _NOW, _graphql, _reasons  # noqa: E402

_T0 = time.mktime(time.strptime("2026-09-29 14:05:00", "%Y-%m-%d %H:%M:%S"))
_RATE_RE = re.compile(r"rateLimit\s*\{\s*cost\s+remaining\s*\}")


def _with_cost(payload, cost):
    out = json.loads(json.dumps(payload))
    out.setdefault("data", {})["rateLimit"] = {"cost": cost, "remaining": 4000}
    return out


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ghrate-1188-")
        p = mock.patch.object(cli_gh_rate, "gh_rate_dir", lambda: self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def costs(self, now=None):
        return dict(cli_gh_rate_cost.top_spenders(
            cli_gh_rate_cost.load_costs(now), limit=50))


class RecordQueryCost(_Tmp):
    def test_cost_lands_under_its_label(self):
        cli_gh_rate_cost.record_query_cost(
            "ops-wait-prefetch", {"data": {"rateLimit": {"cost": 3}}}, now=_T0)
        cli_gh_rate_cost.record_query_cost(
            "ops-wait-prefetch", {"data": {"rateLimit": {"cost": 2}}}, now=_T0)
        data = cli_gh_rate_cost.load_costs(_T0)
        self.assertEqual(data["14"]["q:ops-wait-prefetch"], {"cost": 5, "n": 2})
        path = cli_gh_rate_cost.cost_path(_T0)
        self.assertEqual(os.path.dirname(path), self.tmp)   # next to calls-*.json
        self.assertTrue(os.path.basename(path).startswith("gql-cost-2026-09-29"))

    def test_payload_without_cost_records_nothing(self):
        for bad in (None, {}, {"data": None}, {"data": {"rateLimit": None}},
                    {"data": {"rateLimit": {"cost": "x"}}},
                    {"data": {"rateLimit": {"cost": True}}}):
            self.assertIsNone(cli_gh_rate_cost.record_query_cost("x", bad, now=_T0))
        self.assertFalse(os.path.exists(cli_gh_rate_cost.cost_path(_T0)))


class OwnedQueriesCarryAndRecordCost(_Tmp):
    """Every airuleset-owned GraphQL query asks for its cost and records it."""

    def test_static_queries_ask_for_cost(self):
        for name, query in (
                ("_PR_QUERY", cli_ticket_facts._PR_QUERY),
                ("_OPS_WAIT_PREFETCH_GQL", cli_quals._OPS_WAIT_PREFETCH_GQL),
                ("_CLOSED_FETCH_GRAPHQL", airuleset._CLOSED_FETCH_GRAPHQL)):
            self.assertRegex(query, _RATE_RE, name)

    def test_read_prs_records_ticket_facts_prs(self):
        calls = []

        def gh(args):
            calls.append(args)
            return json.dumps(_with_cost(
                _graphql((50, "#5 x", "", "PENDING", ())), 1))
        self.assertEqual(cli_ticket_facts.read_prs("o/r", gh, now=_NOW),
                         {5: True})
        self.assertEqual(self.costs()["q:ticket-facts-prs"], 1)

    def test_read_reopened_records_its_label(self):
        seen = []

        def gh(args):
            seen.extend(args)
            return json.dumps(_with_cost(_reasons((5,), (5,)), 1))
        self.assertEqual(cli_ticket_facts.read_reopened("o/r", gh, [5]),
                         frozenset({5}))
        self.assertTrue(any(_RATE_RE.search(a) for a in seen))
        self.assertEqual(self.costs()["q:ticket-facts-reopened"], 1)

    def test_ops_wait_prefetch_records_every_page(self):
        pages = {None: ("c1", True, [1]), "c1": (None, False, [2])}

        def gh_out(*args, **kw):
            cursor = next((a[len("cursor="):] for a in args
                           if a.startswith("cursor=")), None)
            end, more, nums = pages[cursor]
            return json.dumps({"data": {
                "search": {"pageInfo": {"hasNextPage": more, "endCursor": end},
                           "nodes": [{"number": n, "comments": {
                               "totalCount": 0, "nodes": []}} for n in nums]},
                "rateLimit": {"cost": 1, "remaining": 4000}}})
        from gates import ghread
        with mock.patch.object(airuleset, "_gh_out", gh_out), \
                mock.patch.object(ghread, "canonical_slug", lambda r: "o/r"):
            out = cli_quals._ops_wait_prefetch_comments(["q"], "/r")
        self.assertEqual(sorted(out), [1, 2])
        self.assertEqual(self.costs()["q:ops-wait-prefetch"], 2)

    def test_last_origin_owner_records_its_label(self):
        seen = []

        def gh_out(*args, **kw):
            seen.extend(args)
            return json.dumps({"data": {
                "repository": {"i5": {"timelineItems": {"nodes": []}}},
                "rateLimit": {"cost": 1, "remaining": 4000}}})
        with mock.patch.object(airuleset, "_gh_out", gh_out):
            cli_quals._last_origin_owner([5], cwd="/r")
        self.assertTrue(any(_RATE_RE.search(a) for a in seen))
        self.assertEqual(self.costs()["q:origin-owner"], 1)

    def test_closed_fetch_records_its_label(self):
        body = {"data": {"repository": {"issues": {"nodes": []}},
                         "rateLimit": {"cost": 1, "remaining": 4000}}}
        out = mock.Mock(returncode=0, stdout=json.dumps(body))
        with mock.patch("subprocess.run", return_value=out):
            self.assertEqual(airuleset._watchdog_closed_fetch("/r", 0), {})
        self.assertEqual(self.costs()["q:closed-fetch"], 1)

    def test_every_owned_graphql_call_site_records_cost(self):
        """A new owned `gh api graphql` query must record its cost too."""
        call = re.compile(r"""["']api["'],\s*["']graphql["']""")
        skip = {"cli_gh_rate_graphql.py"}   # the free rateLimit-object reader
        offenders = []
        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT)
            if rel.parts[0] in ("tests", ".claude", ".git") or rel.name in skip:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            calls = len(call.findall(text))
            records = len(re.findall(r"record_query_cost\(", text))
            if calls > records:   # one recorder per owned call site
                offenders.append("%s (%d calls, %d records)"
                                 % (rel, calls, records))
        self.assertEqual(offenders, [])


class ExhaustedLineRanksByCost(_Tmp):
    def _seed(self):
        poll = {cli_gh_rate.POLLER_ENV: "1"}
        for _ in range(9):                       # the most CALLS …
            cli_gh_rate.record_call(["issue", "list", "--json", "number"],
                                    env=poll, now=_T0)
        cli_gh_rate.record_call(["api", "graphql", "-f", "query={x}"],
                                env=poll, now=_T0)
        # … but the most COST sits elsewhere
        cli_gh_rate_cost.record_query_cost(
            "ticket-facts-prs", {"data": {"rateLimit": {"cost": 4}}}, now=_T0)
        cli_gh_rate_cost.record_query_cost(
            "ops-wait-prefetch", {"data": {"rateLimit": {"cost": 40}}}, now=_T0)

    def test_burners_rank_cost_first(self):
        self._seed()
        status = {"resources": {"graphql": {"remaining": 10, "limit": 5000,
                                            "reset": int(_T0) + 1800}}}
        line = cli_gh_rate.alert_line(
            "graphql", status, burners=cli_gh_rate.exhausted_burners(now=_T0))
        self.assertIn("top cost:", line)
        a = line.index("q:ops-wait-prefetch 40pt")
        b = line.index("q:ticket-facts-prs 4pt")
        self.assertLess(a, b)
        self.assertLess(b, line.index("top: "))  # calls stay as context, after

    def test_record_alerts_journal_line_ranks_by_cost(self):
        self._seed()
        status = {"resources": {"graphql": {"remaining": 10, "limit": 5000,
                                            "reset": int(_T0) + 1800}},
                  "_pending_alerts": ["graphql"]}
        with mock.patch.object(cli_gh_rate_cost.time, "time", lambda: _T0), \
                mock.patch.object(cli_gh_rate.time, "time", lambda: _T0):
            cli_gh_rate.record_alerts(status)
        with open(cli_gh_rate.journal_path(), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("top cost: q:ops-wait-prefetch 40pt", text)

    def test_sampled_residual_never_displaces_an_exact_spender(self):
        # review 1188 🔵: a `~` row is an identity-wide residual; exact q:
        # rows rank first, the residual is its own labelled group.
        self._seed()
        data = {"14": {"~issue list|poller": {"cost": 900, "n": 9},
                       "~unattributed": {"cost": 500, "n": 0}}}
        cli_gh_rate_cost.locked_json_update(
            cli_gh_rate_cost.cost_path(_T0),
            lambda d: d.setdefault("14", {}).update(data["14"]))
        line = cli_gh_rate_cost.current_hour_cost_suffix(_T0)
        self.assertIn("top cost: q:ops-wait-prefetch 40pt, "
                      "q:ticket-facts-prs 4pt", line)
        self.assertIn("≈residual: ~issue list|poller ≈900pt, "
                      "~unattributed ≈500pt", line)
        self.assertLess(line.index("q:ticket-facts-prs"),
                        line.index("≈residual:"))

    def test_gh_rate_top_prints_costs(self):
        self._seed()

        class A:
            top = True
            day = "2026-09-29"
            limit = 15
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_gh_rate._cmd_gh_rate_top(A())
        self.assertIn("q:ops-wait-prefetch", buf.getvalue())


class _FakeRateLimit:
    """A fake `subprocess.run` answering the authoritative GraphQL rateLimit
    OBJECT read. Not REST `gh api rate_limit`: measured live 2026-09-29, the
    REST graphql bucket said used 18 while the object said used 777 (the #1052
    mis-report), so a REST delta would attribute nothing."""

    def __init__(self):
        self.calls = 0
        self.remaining = 4000
        self.reset = int(_T0) + 3000

    def __call__(self, argv, **kw):
        self.calls += 1
        assert argv[1:3] == ["api", "graphql"], argv
        assert "rateLimit" in argv[-1], argv
        assert kw["env"][cli_gh_rate.INTERNAL_ENV] == "1"   # never counted
        reset_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.reset))
        return mock.Mock(returncode=0, stdout=json.dumps({"data": {"rateLimit": {
            "limit": 5000, "remaining": self.remaining, "resetAt": reset_at,
            "used": 5000 - self.remaining}}}))


class SampledAttribution(_Tmp):
    """Foreign/raw GraphQL-shaped calls: the rateLimit-object `used` delta is
    attributed to the window's call shapes (approximate, `~` keys)."""

    ISSUE_LIST = ["issue", "list", "--json", "number"]
    RAW_GQL = ["api", "graphql", "-f", "query={viewer{login}}"]
    PR_VIEW = ["pr", "view", "5", "--json", "title"]

    def setUp(self):
        super().setUp()   # a plain (PAT) box, whatever box runs the suite
        p = mock.patch.object(cli_gh_rate, "is_app_shim_box", lambda: False)
        p.start()
        self.addCleanup(p.stop)

    def note(self, argv, kind, now, run):
        return cli_gh_rate_cost.note_call(argv, kind=kind, now=now, run=run,
                                          real_gh="/fake/gh")

    def test_first_sample_is_a_baseline_then_once_per_minute(self):
        fr = _FakeRateLimit()
        self.assertEqual(self.note(self.ISSUE_LIST, "poller", _T0, fr), {})
        self.assertEqual(fr.calls, 1)
        self.note(self.RAW_GQL, "poller", _T0 + 10, fr)
        self.note(self.ISSUE_LIST, "poller", _T0 + 20, fr)
        self.assertEqual(fr.calls, 1)            # not due yet: no fetch
        fr.remaining -= 60                       # 60 points spent meanwhile
        est = self.note(self.PR_VIEW, "human", _T0 + 70, fr)
        self.assertEqual(fr.calls, 2)
        self.assertEqual(est, {"api graphql|poller": 20.0,
                               "issue list|poller": 20.0,
                               "pr view|human": 20.0})
        costs = self.costs(_T0)
        self.assertAlmostEqual(costs["~api graphql|poller"], 20.0)

    def test_owned_exact_cost_is_subtracted(self):
        fr = _FakeRateLimit()
        self.note(self.ISSUE_LIST, "poller", _T0, fr)
        cli_gh_rate_cost.record_query_cost(
            "ops-wait-prefetch", {"data": {"rateLimit": {"cost": 30}}},
            now=_T0 + 5)
        self.note(self.RAW_GQL, "poller", _T0 + 10, fr)
        fr.remaining -= 50
        est = self.note(self.ISSUE_LIST, "poller", _T0 + 61, fr)
        self.assertEqual(est, {"api graphql|poller": 10.0,
                               "issue list|poller": 10.0})

    def test_new_reset_window_is_booked_unattributed(self):
        # review 1188 🟡: after a reset the spend since the reset spans
        # time this box never windowed — never blame this box's few calls.
        fr = _FakeRateLimit()
        self.note(self.ISSUE_LIST, "poller", _T0, fr)
        fr.reset += 3600
        fr.remaining = 4990                      # 10 used in the new window
        est = self.note(self.ISSUE_LIST, "poller", _T0 + 61, fr)
        self.assertEqual(est, {"unattributed": 10.0})
        costs = self.costs(_T0)
        self.assertAlmostEqual(costs["~unattributed"], 10.0)
        self.assertNotIn("~issue list|poller", costs)

    def test_idle_gap_is_booked_unattributed(self):
        # review 1188 🟡: 45 idle minutes while OTHER boxes spent 2500 — one
        # sporadic human call must not be named as the spender.
        fr = _FakeRateLimit()
        self.note(self.ISSUE_LIST, "poller", _T0, fr)
        fr.remaining -= 2500
        est = self.note(["issue", "view", "5"], "human", _T0 + 45 * 60, fr)
        self.assertEqual(est, {"unattributed": 2500.0})
        self.assertNotIn("~issue view|human", self.costs(_T0))

    def test_self_reporting_owned_calls_stay_out_of_the_window(self):
        # review 1188 🔴: owned queries go through the shim too; they are
        # counted exactly (q:), so the residual must land on the foreign call.
        fr = _FakeRateLimit()
        self.note(self.ISSUE_LIST, "poller", _T0, fr)
        owned = ["api", "graphql", "-f",
                 "query={ search { x } rateLimit { cost remaining } }"]
        compact = ["api", "graphql", "-f", "query={a rateLimit{cost remaining}}"]
        for i in range(20):
            self.assertIsNone(self.note(owned if i % 2 else compact, "poller",
                                        _T0 + 1 + i, fr))
            cli_gh_rate_cost.record_query_cost(
                "ops-wait-prefetch", {"data": {"rateLimit": {"cost": 1}}},
                now=_T0 + 1 + i)
        fr.remaining -= 120                      # 20 owned + 100 foreign
        est = self.note(["issue", "list", "--json", "n"], "human", _T0 + 61, fr)
        self.assertEqual(est, {"issue list|human": 100.0})

    def test_rest_core_call_is_not_windowed(self):
        fr = _FakeRateLimit()
        self.assertIsNone(self.note(["api", "repos/o/r/issues"], "poller",
                                    _T0, fr))
        self.assertIsNone(self.note(["run", "list"], "poller", _T0, fr))
        self.assertEqual(fr.calls, 0)

    def test_app_token_box_never_samples(self):
        fr = _FakeRateLimit()
        with mock.patch.object(cli_gh_rate, "is_app_shim_box", lambda: True):
            self.assertIsNone(self.note(self.ISSUE_LIST, "poller", _T0, fr))
        self.assertEqual(fr.calls, 0)

    def test_fetch_failure_is_fail_open(self):
        def boom(argv, **kw):
            raise OSError("no gh")
        self.assertIsNone(self.note(self.ISSUE_LIST, "poller", _T0, boom))

    def test_record_main_feeds_the_sampler(self):
        with mock.patch.object(cli_gh_rate_cost, "note_call") as nc:
            cli_gh_rate._record_main(["--record", "--"] + self.ISSUE_LIST)
        nc.assert_called_once()
        self.assertEqual(nc.call_args[0][0], self.ISSUE_LIST)


class CappedByCost(_Tmp):   # _Tmp: read_prs records cost (#1136 hermeticity)
    """dryRun on odoo-erp (2026-09-29): `_PR_QUERY` cost 2 at first:100 and 1
    at first:<=60; the prefetch costs 1 per page for any first <= 99."""

    def test_pr_query_pages_at_cost_one(self):
        m = re.search(r"pullRequests\(([^)]*)\)", cli_ticket_facts._PR_QUERY)
        first = int(re.search(r"first:\s*(\d+)", m.group(1)).group(1))
        self.assertLessEqual(first, 60)
        self.assertIn("after:$cursor", m.group(1).replace(" ", ""))
        self.assertIn("CREATED_AT", m.group(1))   # stable order across pages
        self.assertIn("endCursor", cli_ticket_facts._PR_QUERY)

    def _pages(self, *pages):
        calls = []

        def gh(args):
            calls.append(args)
            idx = len(calls) - 1
            nodes, more = pages[idx]
            payload = _graphql(*nodes)
            payload["data"]["repository"]["pullRequests"]["pageInfo"] = {
                "hasNextPage": more, "endCursor": "c%d" % (idx + 1)}
            return json.dumps(payload)
        return gh, calls

    def test_read_prs_merges_two_pages(self):
        gh, calls = self._pages(([(50, "#5 x", "", "PENDING", ())], True),
                                ([(51, "#6 y", "", "FAILURE", (7,))], False))
        self.assertEqual(cli_ticket_facts.read_prs("o/r", gh, now=_NOW),
                         {5: True, 6: False, 7: False})
        self.assertEqual(len(calls), 2)
        self.assertIn("cursor=c1", calls[1])

    def test_more_than_two_pages_is_unknown_as_before(self):
        gh, calls = self._pages(([(50, "#5 x", "", "PENDING", ())], True),
                                ([(51, "#6 y", "", "PENDING", ())], True),
                                ([(52, "#7 z", "", "PENDING", ())], False))
        self.assertIsNone(cli_ticket_facts.read_prs("o/r", gh, now=_NOW))
        self.assertEqual(len(calls), 2)

    def test_next_page_without_cursor_is_unknown(self):
        def gh(args):
            payload = _graphql((50, "#5 x", "", "PENDING", ()))
            payload["data"]["repository"]["pullRequests"]["pageInfo"] = {
                "hasNextPage": True, "endCursor": None}
            return json.dumps(payload)
        self.assertIsNone(cli_ticket_facts.read_prs("o/r", gh, now=_NOW))

    def test_prefetch_page_amortises_the_flat_page_cost(self):
        size = cli_quals.OPS_WAIT_PREFETCH_PAGE_SIZE
        self.assertGreaterEqual(size, 25)
        self.assertLessEqual(size, 99)
        self.assertIn("first: %d," % size, cli_quals._OPS_WAIT_PREFETCH_GQL)
        # the per-issue comment window is unchanged (identical results)
        self.assertIn("comments(last: 100)", cli_quals._OPS_WAIT_PREFETCH_GQL)


class RefreshBudgetReadsTheObject(_Tmp):
    """review 1188 🟡: the #370 footer floor read only REST `rate_limit`,
    blind to GraphQL-endpoint spend; the POLLER-marked refresh had no #1041
    hold. Both now read the shim's cached status (zero gh calls)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(cli_gh_rate, "status_path",
                              lambda: os.path.join(self.tmp, "status.json"))
        p.start()
        self.addCleanup(p.stop)

    def _cache(self, age, **resources):
        with open(cli_gh_rate.status_path(), "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": time.time() - age,
                       "resources": resources}, fh)

    @staticmethod
    def _rest(remaining):
        return lambda *a, **k: json.dumps({"resources": {"graphql": {
            "remaining": remaining, "limit": 5000}}})

    def test_floor_takes_the_lower_cached_object_reading(self):
        self._cache(10, graphql={"remaining": 150, "limit": 5000,
                                 "source": "graphql-object"})
        self.assertEqual(airuleset._graphql_budget_ok(
            1000, runner=self._rest(4982)), (False, 150))

    def test_floor_ignores_a_stale_or_rest_sourced_cache(self):
        self._cache(3600, graphql={"remaining": 150, "limit": 5000,
                                   "source": "graphql-object"})
        self.assertEqual(airuleset._graphql_budget_ok(
            1000, runner=self._rest(4982)), (True, 4982))
        self._cache(10, graphql={"remaining": 150, "limit": 5000,
                                 "source": "rest"})
        self.assertEqual(airuleset._graphql_budget_ok(
            1000, runner=self._rest(4982)), (True, 4982))

    def test_poller_refresh_holds_on_a_low_cached_budget(self):
        self._cache(10, core={"remaining": 100, "limit": 5000})
        with mock.patch.dict(os.environ, {"AIRULESET_GH_POLLER": "1"}):
            self.assertTrue(airuleset._gh_poller_hold())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_GH_POLLER", None)
            self.assertFalse(airuleset._gh_poller_hold())   # a human: never

    def test_poller_hold_is_fail_open(self):
        with mock.patch.dict(os.environ, {"AIRULESET_GH_POLLER": "1"}):
            self.assertFalse(airuleset._gh_poller_hold())      # no cache
            self._cache(3600, core={"remaining": 100, "limit": 5000})
            self.assertFalse(airuleset._gh_poller_hold())      # stale cache

    def test_refresh_serves_stale_cache_while_held(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_tickets_status)
        self.assertIn("_gh_poller_hold()", src)
        self.assertLess(src.index("_gh_poller_hold()"),
                        src.index('"repo", "view"'))


class PollersCarryPollerEnv(unittest.TestCase):
    def test_footer_refresh_spawn_is_a_poller(self):
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(statusbar.subprocess, "Popen") as popen:
            os.environ.pop("AIRULESET_GH_POLLER", None)
            statusbar._spawn_refresh("/some/repo", home=home)
            env = popen.call_args.kwargs.get("env")
            self.assertIsNotNone(env)
            self.assertEqual(env.get("AIRULESET_GH_POLLER"), "1")
            # the human session's own env is never marked
            self.assertNotIn("AIRULESET_GH_POLLER", os.environ)

    def test_watchdog_entry_marks_its_process(self):
        import inspect
        self.assertIn('os.environ["AIRULESET_GH_POLLER"] = "1"',
                      inspect.getsource(airuleset.cmd_watchdog))


if __name__ == "__main__":
    unittest.main()
