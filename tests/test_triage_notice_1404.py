"""#1404 item 3: the W / I re-triage notice for EVERY live project session.

Owner 28.11.2026: W tickets rot because only an ARMED /goal pane (Job 20) ever
re-checks them, and owner steps written as prose stay in I. `watchdog/
triage_notice.py` names, at most once per 3 days per project and only when
something qualifies, the plain ops-wait tickets whose wait is unbacked (3 d /
a passed target) and the I tickets whose newest own comment waits on a person
or an on-site slot with no U label. Machine nudge kind `w-triage`, shared
idle-pane gates, ONE batched read only when a notice could go now.
"""
import inspect
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import cli_fleet  # noqa: E402
import cli_ticket_route  # noqa: E402
import statusbar  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import triage_notice as tn  # noqa: E402

DAY = 86400
NOW = 1795860000.0                       # 2026-11-28T09:20:00Z
ME = "newlevel-project-accounts"
GOOD = "Waiting-on: Hetzner support — reply on ticket 4711 — read the ticket"
FOH = ("26.11: the planned 09:00 CET slot is off. Waiting for the next FOH slot: "
       "dasio 0.1.176 on band-asio-dante.")


def _c(iso, body, login=ME):
    return {"createdAt": iso, "author": {"login": login}, "body": body}


def _row(labels, comments, state="OPEN"):
    return {"state": state, "labels": list(labels), "comments": comments,
            "total": len(comments)}


ROWS = {
    10: _row(["ops-wait"], [_c("2026-11-20T08:00:00Z", GOOD, login="relay")]),   # 8 d old
    11: _row(["ops-wait"], [_c("2026-11-27T08:00:00Z", GOOD, login="relay")]),   # fresh
    12: _row(["ops-wait"], [_c("2026-11-27T08:00:00Z", GOOD, login="relay"),
                            _c("2026-11-21T08:00:00Z",
                               "Ops-wait-target: Hetzner reply by 2026-11-25")]),  # passed
    13: _row(["ops-wait", "needs-owner-action"], []),                             # U already
    127: _row([], [_c("2026-11-26T07:03:29Z", FOH)]),                             # U?
    128: _row(["needs-owner-action"], [_c("2026-11-26T07:03:29Z", FOH)]),         # U already
    129: _row([], [_c("2026-11-20T07:00:00Z", FOH),
                   _c("2026-11-27T07:00:00Z", "Measured again, 0 late packets.")]),
    130: _row([], [_c("2026-11-26T07:00:00Z", FOH, login="someone-else")]),
    131: _row([], [_c("2026-11-26T07:00:00Z", FOH)], state="CLOSED"),
}
I_NUMS = {127, 128, 129, 130, 131}


class Candidates(unittest.TestCase):
    def test_the_two_rot_shapes(self):
        w, u = tn.candidates(ROWS, I_NUMS, ME, NOW)
        self.assertEqual([n for n, _ in w], [10, 12])
        self.assertEqual([n for n, _ in u], [127])
        self.assertIn("2026-11-25", dict(w)[12])

    def test_no_own_login_never_says_u(self):
        _w, u = tn.candidates(ROWS, I_NUMS, None, NOW)
        self.assertEqual(u, [])

    def test_a_long_thread_w_is_unknown(self):
        row = dict(ROWS[10], total=tn.COMMENT_CAP + 1)
        w, _u = tn.candidates({10: row}, set(), ME, NOW)
        self.assertEqual(w, [])

    def test_a_w_number_outside_i_is_not_read_as_i(self):
        _w, u = tn.candidates({127: ROWS[127]}, set(), ME, NOW)
        self.assertEqual(u, [])


class Text(unittest.TestCase):
    def test_short_and_names_the_exits(self):
        text = tn.notice_text([(n, "x") for n in range(1, 999)],
                              [(n, "y") for n in range(1000, 1999)])
        self.assertLessEqual(len(text), tn.NUDGE_MAX_CHARS)
        for token in ("#1,", "#1000", "more", "needs-owner-action", "needs-answer",
                      "Waiting-on:", "Ops-wait-target:", "U?"):
            self.assertIn(token, text)
        self.assertTrue(text.startswith("w-triage: "))

    def test_one_side_only(self):
        text = tn.notice_text([], [(127, "y")])
        self.assertIn("#127", text)
        self.assertNotIn("W with", text)


class _Job(unittest.TestCase):
    def setUp(self):
        self.reads, self.sent, self.marks = [], [], []
        self.scope_map = {"/d/dasio": ("/d/dasio", I_NUMS, {10, 11, 12, 13})}
        self.ready_ok = True
        self.is_armed = False
        self.kind_on = True
        self.rows = ROWS

    def _run(self, state, now=NOW, dry_run=False, panes=(("%1", "/d/dasio"),)):
        def fetch(root, numbers):
            self.reads.append((root, list(numbers)))
            return self.rows

        def deliver(pid, tpath, text):
            self.sent.append((pid, text))
            return True
        return tn.triage_job(
            now, state, list(panes), scope=self.scope_map.get,
            ready=lambda pid, cwd: (self.ready_ok, "" if self.ready_ok else "busy-pane",
                                    "sid-" + pid, "/t/" + pid + ".jsonl"),
            armed=lambda tpath: self.is_armed, fetch=fetch, self_login=lambda: ME,
            deliver=deliver, mark_sent=lambda st, sid, kind, t: self.marks.append((sid, kind)),
            nudges_enabled=lambda kind: self.kind_on and kind == tn.NUDGE_KIND,
            dry_run=dry_run)


class Job(_Job):
    def test_tells_once_then_holds_three_days(self):
        state = {}
        logs = self._run(state)
        self.assertEqual(len(self.sent), 1, logs)
        self.assertIn("#10", self.sent[0][1])
        self.assertIn("#127", self.sent[0][1])
        self.assertEqual(self.marks, [("sid-%1", "w-triage")])
        self.assertEqual(state[tn.STATE_KEY]["/d/dasio"], {"at": NOW})
        self._run(state, now=NOW + 3 * DAY - 60)
        self.assertEqual(len(self.reads), 1)            # no second read inside 3 days
        self._run(state, now=NOW + 3 * DAY + 60)
        self.assertEqual(len(self.reads), 2)

    def test_kind_off_reads_nothing(self):
        self.kind_on = False
        logs = self._run({})
        self.assertEqual((self.reads, self.sent), ([], []))
        self.assertIn("kind-off", logs[0])

    def test_an_armed_goal_pane_is_left_to_job_20(self):
        self.is_armed = True
        logs = self._run({})
        self.assertEqual((self.reads, self.sent), ([], []))
        self.assertIn("armed-goal", logs[0])

    def test_a_busy_pane_reads_nothing(self):
        self.ready_ok = False
        self._run({})
        self.assertEqual(self.reads, [])

    def test_nothing_to_triage_is_rechecked_daily(self):
        self.rows = {11: ROWS[11]}
        state = {}
        self._run(state)
        self.assertEqual(self.sent, [])
        self.assertEqual(state[tn.STATE_KEY]["/d/dasio"], {"checked": NOW})
        self._run(state, now=NOW + DAY - 60)
        self.assertEqual(len(self.reads), 1)
        self._run(state, now=NOW + DAY + 60)
        self.assertEqual(len(self.reads), 2)

    def test_a_failed_read_is_retried_hourly(self):
        self.rows = None
        state = {}
        self._run(state)
        self.assertEqual(state[tn.STATE_KEY]["/d/dasio"], {"failed": NOW})
        self._run(state, now=NOW + 1800)
        self.assertEqual(len(self.reads), 1)

    def test_dry_run_types_and_records_nothing(self):
        state = {}
        logs = self._run(state, dry_run=True)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.marks, [])
        self.assertEqual(state, {})
        self.assertIn("would tell", " ".join(logs))

    def test_one_read_per_sweep(self):
        self.scope_map["/d/other"] = ("/d/other", {5}, set())
        self._run({}, panes=(("%1", "/d/dasio"), ("%2", "/d/other")))
        self.assertEqual(len(self.reads), 1)

    def test_no_numbers_no_work(self):
        self.scope_map = {"/d/dasio": ("/d/dasio", set(), set())}
        self.assertEqual(self._run({}), [])
        self.assertEqual(self.reads, [])


class Scope(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tn1404-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = os.path.join(self.home, "devel", "dasio")
        os.makedirs(os.path.join(self.repo, ".git"))

    def _cache(self, path, **extra):
        d = statusbar.cache_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        body = {"ts": NOW - 600, "root": self.repo, "open_numbers": [127, 129],
                "ops_wait_numbers": [10]}
        body.update(extra)
        (d / (statusbar.cwd_key(path) + ".json")).write_text(json.dumps(body))

    def test_reads_the_checkout_cache_from_a_subdir(self):
        self._cache(self.repo)
        sub = os.path.join(self.repo, "src")
        os.makedirs(sub)
        self.assertEqual(tn.cache_of(sub, home=self.home, now=NOW),
                         (self.repo, {127, 129}, {10}))

    def test_stale_failed_or_lane_is_none(self):
        self._cache(self.repo, ts=NOW - 2 * DAY)
        self.assertIsNone(tn.cache_of(self.repo, home=self.home, now=NOW))
        self._cache(self.repo, stale_since=NOW - 60)
        self.assertIsNone(tn.cache_of(self.repo, home=self.home, now=NOW))
        lane = os.path.join(self.repo, ".claude", "worktrees", "agent-x")
        os.makedirs(lane)
        self._cache(lane)
        self.assertIsNone(tn.cache_of(lane, home=self.home, now=NOW))

    def test_the_footer_records_the_w_numbers(self):
        entry = {}
        buckets = {b: {} for b in ("I", "U", "W", "M", "P", "C", "gk", "HIDDEN")}
        buckets["I"] = {127: {}}
        buckets["W"] = {10: {}, 11: {}}
        cli_ticket_route.record(entry, buckets)
        self.assertEqual(entry.get("ops_wait_numbers"), [10, 11])


def _runner(gql):
    calls = []

    def run(argv):
        calls.append(argv)
        joined = " ".join(argv)
        if argv[0] == "gh":
            return 0, json.dumps(gql), ""
        if "gh-resolved" in joined or "upstream" in joined:
            return 1, "", ""
        return 0, "git@github.com:zbynekdrlik/dasio.git\n", ""
    return run, calls


class Fetch(unittest.TestCase):
    def test_alias_query_parses_rows(self):
        gql = {"data": {"rateLimit": {"cost": 1}, "repository": {
            "i127": {"state": "OPEN", "labels": {"nodes": [{"name": "bug"}]},
                     "comments": {"totalCount": 1, "nodes": [_c("2026-11-26T07:03:29Z", FOH)]}},
            "i10": None}}}
        run, calls = _runner(gql)
        rows = tn.fetch_rows("/d/dasio", [10, 127], runner=run)
        self.assertEqual(sorted(rows), [127])
        self.assertEqual(rows[127]["labels"], ["bug"])
        gh = [a for a in calls if a[0] == "gh"]
        self.assertEqual(len(gh), 1)
        self.assertIn("owner=zbynekdrlik", gh[0])
        self.assertIn("i127: issue(number: 127)", " ".join(gh[0]))

    def test_errors_are_unknown(self):
        run, _ = _runner({"errors": [{"message": "x"}]})
        self.assertIsNone(tn.fetch_rows("/d/dasio", [1], runner=run))

    def test_chunks_bound_the_request(self):
        run, calls = _runner({"data": {"repository": {}}})
        tn.fetch_rows("/d/dasio", list(range(1, 2 * tn.CHUNK + 2)), runner=run)
        self.assertEqual(len([a for a in calls if a[0] == "gh"]), 3)

    def test_the_real_read_refuses_under_pytest(self):
        self.assertIsNone(tn.fetch_rows("/d/dasio", [1]))


class Wiring(unittest.TestCase):
    def test_a_machine_kind_on_every_profile(self):
        self.assertIn(tn.NUDGE_KIND, wd.MACHINE_NUDGE_KINDS)
        for name, kinds in cli_fleet.NUDGE_PROFILES.items():
            self.assertIn(tn.NUDGE_KIND, kinds, name)

    def test_run_once_registers_the_job(self):
        src = inspect.getsource(wd.run_once)
        self.assertIn('_add("triage_notice", lambda: triage_notice_enabled and not box_paused', src)
        param = inspect.signature(wd.run_once).parameters["triage_notice_enabled"]
        self.assertIs(param.default, False)

    def test_the_real_poll_enables_it(self):
        import airuleset
        self.assertIn("triage_notice_enabled=True", inspect.getsource(airuleset.cmd_watchdog))


if __name__ == "__main__":
    unittest.main()
