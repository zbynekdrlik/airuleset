"""tests/test_post_release_1161.py -- #1161 part 2 (d): the post-release loop
metric behind `slice-quals --post-release` / `core-quals --post-release`.

A post-release loop is a ticket going BACK to work after it went live on PROD:
  * reopen   -- a `reopened` event after a live-on-PROD signal;
  * rework   -- a new hand-off after the deploy (a READY-FOR-REVIEW comment, a
                `ready-for-review` / `prio:bounce` label): the verify-on-copy
                bounce;
  * followup -- a NEW ticket citing an already-released ticket as defective
                (`Defect-of: #N`, or a defect word on the citing line).
Synthetic issue/timeline fixtures only; the gh seam is a fake runner.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import unittest
import unittest.mock as mk
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import airuleset  # noqa: E402
import cli_post_release as pr  # noqa: E402

SLUG = "zbynekdrlik/odoo-erp"
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc).timestamp()


def _ts(day, hour=10):
    return "2026-09-%02dT%02d:00:00Z" % (day, hour)


def lab(day, name, hour=10):
    return {"event": "labeled", "label": {"name": name},
            "created_at": _ts(day, hour)}


def com(day, body, hour=10):
    return {"event": "commented", "body": body, "created_at": _ts(day, hour)}


def reopen(day, hour=10):
    return {"event": "reopened", "created_at": _ts(day, hour)}


RFR = "READY-FOR-REVIEW: branch lane-x\nHEAD: abc12345"
LIVE = "Merged + deployed. Live on PROD 2.349 -> verify-on-copy"


class TestClassifyTimeline(unittest.TestCase):
    def test_new_handoff_after_deploy_is_one_rework(self):
        r = pr.classify_timeline([com(3, RFR), lab(5, "verify-on-copy"),
                                  com(6, RFR)])
        self.assertEqual((r["reopen"], r["rework"]), (0, 1))
        self.assertIsNotNone(r["released_at"])

    def test_handoffs_before_release_are_not_loops(self):
        r = pr.classify_timeline([com(3, RFR), lab(3, "prio:bounce"),
                                  com(4, RFR), lab(5, "verify-on-copy")])
        self.assertEqual((r["reopen"], r["rework"]), (0, 0))

    def test_reopen_after_live_comment_counts_once_per_cycle(self):
        # reopen then a new RFR is ONE loop (the reopen), not two.
        r = pr.classify_timeline([com(5, LIVE), reopen(6), com(7, RFR)])
        self.assertEqual((r["reopen"], r["rework"]), (1, 0))

    def test_two_release_cycles_count_two_loops(self):
        r = pr.classify_timeline([
            lab(5, "verify-on-copy"), lab(6, "prio:bounce"), com(6, RFR, 12),
            com(8, "PROD one-shot done for the re-key"), com(9, RFR)])
        self.assertEqual((r["reopen"], r["rework"]), (0, 2))

    def test_live_phrase_without_version_is_not_a_release(self):
        r = pr.classify_timeline([com(3, "reproduced it live on PROD today"),
                                  com(4, RFR)])
        self.assertIsNone(r["released_at"])
        self.assertEqual(r["rework"], 0)

    def test_needs_gatekeeper_after_release_is_a_close_request_not_a_loop(self):
        r = pr.classify_timeline([lab(5, "verify-on-copy"),
                                  lab(6, "needs-gatekeeper")])
        self.assertEqual(r["rework"], 0)

    def test_since_excludes_older_loops(self):
        since = datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp()
        r = pr.classify_timeline([lab(2, "verify-on-copy"), com(3, RFR),
                                  lab(5, "verify-on-copy"), com(8, RFR)],
                                 since_ts=since)
        self.assertEqual(r["rework"], 1)

    def test_garbage_events_are_ignored(self):
        r = pr.classify_timeline([None, "x", {"event": "labeled"},
                                  {"event": "reopened", "created_at": "bad"}])
        self.assertEqual((r["reopen"], r["rework"], r["released_at"]),
                         (0, 0, None))


def _issue(n, day, title="t", body="", labels=()):
    return {"number": n, "title": title, "body": body,
            "createdAt": _ts(day), "labels": [{"name": x} for x in labels]}


class TestFollowUps(unittest.TestCase):
    RELEASED = {100: datetime(2026, 9, 10, tzinfo=timezone.utc).timestamp()}

    def test_explicit_defect_of_counts(self):
        out = pr.follow_ups([_issue(200, 12, body="Defect-of: #100\nfix it")],
                            self.RELEASED)
        self.assertEqual(out, {100: [200]})

    def test_defect_word_on_the_citing_line_counts(self):
        out = pr.follow_ups(
            [_issue(201, 12, body="Context.\nAfter #100 shipped the stock "
                                  "is broken on 34 cards.")], self.RELEASED)
        self.assertEqual(out, {100: [201]})

    def test_defect_word_in_title_with_cite_in_title(self):
        out = pr.follow_ups([_issue(202, 12, title="Regression from #100")],
                            self.RELEASED)
        self.assertEqual(out, {100: [202]})

    def test_citation_without_defect_word_is_not_a_follow_up(self):
        out = pr.follow_ups([_issue(203, 12, body="Next phase after #100.\n"
                                                 "Some other bug elsewhere.")],
                            self.RELEASED)
        self.assertEqual(out, {})

    def test_citing_before_the_release_is_not_a_follow_up(self):
        out = pr.follow_ups([_issue(204, 8, body="Defect-of: #100")],
                            self.RELEASED)
        self.assertEqual(out, {})

    def test_citing_its_own_epic_or_itself_is_not_a_follow_up(self):
        released = dict(self.RELEASED)
        released[205] = released[100]
        out = pr.follow_ups([_issue(205, 12, body="Epic: #100\nbroken #205")],
                            released)
        self.assertEqual(out, {})

    def test_cross_repo_ref_is_not_a_local_cite(self):
        out = pr.follow_ups(
            [_issue(206, 12, body="broken like other/repo#100")], self.RELEASED)
        self.assertEqual(out, {})


def _population():
    issues = {
        1: _issue(1, 1, "Sklad epic", "Epic spec", ["stream:montalu1", "epic"]),
        2: _issue(2, 2, "Card names", "Epic: #1", ["stream:montalu1"]),
        3: _issue(3, 3, "Stock rewrite", "Epic: #1", ["stream:montalu1"]),
        4: _issue(4, 12, "Safety stock lost", "Defect-of: #2\nEpic: #1",
                  ["stream:montalu1"]),
        5: _issue(5, 4, "Voucher mail", "", ["stream:david2"]),
        6: _issue(6, 4, "Clean ticket", "", ["stream:david2"]),
    }
    timelines = {
        1: [],
        2: [com(4, RFR), lab(10, "verify-on-copy"), com(11, RFR)],
        3: [com(5, LIVE), reopen(12)],
        4: [com(13, RFR)],
        5: [lab(6, "verify-on-copy"), lab(7, "prio:bounce"), com(7, RFR, 12),
            lab(9, "verify-on-copy"), com(10, RFR)],
        6: [lab(6, "verify-on-copy")],
    }
    return issues, timelines


class TestComputeAndRender(unittest.TestCase):
    def test_rows_epics_streams_and_total(self):
        issues, timelines = _population()
        rows = pr.compute(issues, timelines)
        by = {r["number"]: r for r in rows}
        self.assertEqual((by[2]["rework"], by[2]["followup"], by[2]["loops"]),
                         (1, 1, 2))
        self.assertEqual(by[3]["reopen"], 1)
        self.assertEqual(by[5]["rework"], 2)
        self.assertEqual(by[6]["loops"], 0)
        self.assertEqual(by[2]["epic"], 1)
        self.assertEqual(by[1]["epic"], 1)          # the epic groups under itself
        self.assertIsNone(by[5]["epic"])
        self.assertEqual(by[5]["stream"], "david2")
        text = "\n".join(pr.render(rows, [], "fleet", "2026-08-27"))
        self.assertIn("#2\t#1\tmontalu1\t0\t1\t1\t2\tCard names", text)
        self.assertIn("#1\t4\t3", text)             # epic #1: 4 tickets, 3 loops
        self.assertIn("-\t2\t2", text)              # no-epic group
        self.assertIn("montalu1\t4\t3", text)       # 4 tickets, 3 loops
        self.assertIn("david2\t2\t2", text)
        self.assertTrue(text.rstrip().endswith("TOTAL\t5"))
        self.assertNotIn("#6\t", text)              # zero-loop rows are omitted

    def test_unreadable_timelines_degrade_total_to_unknown(self):
        issues, timelines = _population()
        del timelines[5]
        rows = pr.compute(issues, timelines)
        text = "\n".join(pr.render(rows, [5], "fleet", "2026-08-27"))
        self.assertIn("TOTAL\tunknown", text)
        self.assertIn("lower bound 3", text)
        self.assertIn("#5", text)


class _Seam:
    """A fake gh runner answering the population listing + per-ticket timelines."""

    def __init__(self, issues, timelines, fail=()):
        self.issues, self.timelines, self.fail = issues, timelines, set(fail)
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:3] == ["gh", "issue", "list"]:
            if "list" in self.fail:
                return 1, "", "GraphQL: API rate limit exceeded"
            return 0, json.dumps(list(self.issues.values())), ""
        m = re.match(r"^repos/[^/]+/[^/]+/issues/(\d+)/timeline$",
                     argv[2] if len(argv) > 2 else "")
        if argv[:2] == ["gh", "api"] and m:
            n = int(m.group(1))
            if n in self.fail:
                return 1, "", "HTTP 502"
            return 0, "\n".join(json.dumps(e) for e in self.timelines[n]), ""
        return 1, "", "unexpected %r" % (argv,)


class TestRun(unittest.TestCase):
    def _run(self, seam, quals=None, days=30):
        out = io.StringIO()
        rc = pr.run(quals, None, days=days, runner=seam, now=NOW, slug=SLUG,
                    out=out)
        return rc, out.getvalue()

    def test_fleet_read_prints_table_and_total(self):
        issues, timelines = _population()
        seam = _Seam(issues, timelines)
        rc, text = self._run(seam)
        self.assertEqual(rc, 0, text)
        self.assertIn("TOTAL\t5", text)
        lst = [c for c in seam.calls if c[:3] == ["gh", "issue", "list"]]
        self.assertEqual(len(lst), 1)
        self.assertIn("updated:>=2026-08-27", lst[0])
        self.assertIn("all", lst[0])

    def test_slice_read_unions_each_qual(self):
        issues, timelines = _population()
        seam = _Seam(issues, timelines)
        rc, _ = self._run(seam, quals=["label:stream:montalu1",
                                       "assignee:@me"], days=14)
        self.assertEqual(rc, 0)
        searches = [c[c.index("--search") + 1] for c in seam.calls
                    if c[:3] == ["gh", "issue", "list"]]
        self.assertEqual(searches, ["label:stream:montalu1 updated:>=2026-09-12",
                                    "assignee:@me updated:>=2026-09-12"])

    def test_listing_failure_is_unknown_never_zero(self):
        rc, text = self._run(_Seam({}, {}, fail=("list",)))
        self.assertEqual(rc, 1)
        self.assertIn("unknown", text)
        self.assertIn("rate limit", text)
        self.assertNotIn("TOTAL\t0", text)

    def test_timeline_failure_is_unknown(self):
        issues, timelines = _population()
        rc, text = self._run(_Seam(issues, timelines, fail=(5,)))
        self.assertEqual(rc, 1)
        self.assertIn("TOTAL\tunknown", text)


class TestCliWiring(unittest.TestCase):
    def test_flag_on_both_commands(self):
        for sub in ("slice-quals", "core-quals"):
            r = subprocess.run([sys.executable, "airuleset.py", sub, "--help"],
                               capture_output=True, text=True, timeout=30,
                               cwd=_REPO)
            self.assertIn("--post-release", r.stdout, sub)

    def test_core_quals_runs_the_fleet_view(self):
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"), \
                mk.patch("cli_post_release.run", return_value=0) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            airuleset.cmd_core_quals(mk.Mock(post_release=14, task_hygiene=False))
        self.assertIsNone(run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["days"], 14)

    def test_slice_quals_runs_the_slice_view_and_exits_on_unknown(self):
        with mk.patch.object(airuleset, "resolve_authority",
                             return_value="fork-no-merge"), \
                mk.patch.object(airuleset, "_current_user", return_value="m1"), \
                mk.patch.object(airuleset, "_slice_quals",
                                return_value=["label:stream:m1"]), \
                mk.patch("cli_post_release.run", return_value=1) as run, \
                self.assertRaises(SystemExit) as cm:
            airuleset.cmd_slice_quals(mk.Mock(post_release=30, task_hygiene=False))
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(run.call_args.args[0], ["label:stream:m1"])

    def test_mock_args_without_the_flag_do_not_trigger_it(self):
        with mk.patch.object(airuleset, "resolve_authority", return_value="full"), \
                mk.patch("cli_post_release.run") as run, \
                mk.patch.object(airuleset, "_obligation_quals",
                                return_value=["q"]), \
                contextlib.redirect_stdout(io.StringIO()):
            airuleset.cmd_core_quals(mk.Mock(
                count=False, list=False, waiting=False, ops_wait=False,
                audit=False, dep_wait=False, count_dispatchable=False,
                list_dispatchable=False, snapshot_json=False, conflicts=False,
                explain=False, task_hygiene=False))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
