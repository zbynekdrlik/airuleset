"""#1055 P2 wave 2 — TTLs (c), the queue-fetch collapse (d), and the registry
subprocess budget (e).

(c) TTLs justified by how fast the data changes: `cli_gh_rate.CACHE_TTL_S`
60->180 (an hourly bucket), `QUEUE_ARRIVAL_FETCH_TTL_S` 300->600 (label events
minutes apart), and `_watchdog_reopened_fetch` gated by a 900s state TTL in
`card_reconcile` (a reopen is a rare manual event; a 15-min-late marker clear
costs one delayed card).

(d) `_watchdog_queue_fetch`: 3 `gh issue list --label` queries -> ONE
`gh issue list --json number,labels` filtered locally, same sorted union.

(e) `_add(..., max_subprocess=None)`: the registry loop HOLDS a job with
`hold:budget (subprocess...)` when the sweep's subprocess count already met its
cap. No job deleted; default None = unbounded (behaviour-neutral).

RED on base: the TTL constants are still 60/300, `_watchdog_queue_fetch` makes 3
calls, there is no reopen TTL, and `_add` has no `max_subprocess`.
"""

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset                                  # noqa: E402
import cli_gh_rate                                # noqa: E402
import watchdog as wd                             # noqa: E402
import watchdog.cards as cards                    # noqa: E402
import watchdog.queue_arrival_recheck as qa       # noqa: E402
import notify                                     # noqa: E402


class TestTTLConstants(unittest.TestCase):
    def test_gh_rate_cache_ttl_is_180(self):
        self.assertEqual(cli_gh_rate.CACHE_TTL_S, 180)

    def test_queue_arrival_fetch_ttl_is_600(self):
        self.assertEqual(qa.QUEUE_ARRIVAL_FETCH_TTL_S, 600)

    def test_queue_arrival_ttl_env_override_still_works(self):
        import os
        with mock.patch.dict(os.environ,
                             {"AIRULESET_QUEUE_ARRIVAL_FETCH_TTL_S": "900"}):
            self.assertEqual(qa._fetch_ttl(), 900)


class TestQueueFetchCollapse(unittest.TestCase):
    FIXTURE = json.dumps([
        {"number": 5177, "labels": [{"name": "ready-for-review"}]},
        {"number": 42, "labels": [{"name": "needs-gatekeeper"}, {"name": "x"}]},
        {"number": 3073, "labels": [{"name": "prio:bounce"}]},
        {"number": 99, "labels": [{"name": "unrelated"}]},
        {"number": 42, "labels": [{"name": "ready-for-review"}]},  # dup number
    ])

    def _fetch(self, gh_out):
        with mock.patch.object(airuleset, "resolve_authority",
                               lambda cwd=None: "full"), \
             mock.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"):
            return airuleset._watchdog_queue_fetch("/r", gh_out=gh_out)

    def test_one_call_and_correct_union(self):
        calls = []

        def gh_out(*args, **kw):
            calls.append(args)
            return self.FIXTURE

        out = self._fetch(gh_out)
        self.assertEqual(out, [42, 3073, 5177])
        self.assertEqual(len(calls), 1, "collapsed to ONE gh issue list call")

    def test_non_full_authority_is_none(self):
        with mock.patch.object(airuleset, "resolve_authority",
                               lambda cwd=None: "fork-no-merge"), \
             mock.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"):
            self.assertIsNone(
                airuleset._watchdog_queue_fetch("/r", gh_out=lambda *a, **k: "[]"))

    def test_empty_stdout_is_empty_union(self):
        self.assertEqual(self._fetch(lambda *a, **k: ""), [])

    def test_bad_json_is_none(self):
        self.assertIsNone(self._fetch(lambda *a, **k: "not json"))


class TestReopenFetchTTL(unittest.TestCase):
    """`card_reconcile` calls `reopen_fetch` at most once per root per 900s."""

    def _git_run(self, argv, timeout=10):
        sub = argv[3:]  # strip `git -C <cwd>`
        if sub[:1] == ["rev-parse"] and "--show-toplevel" in sub:
            return "/repo\n"
        if sub[:1] == ["symbolic-ref"]:
            return "refs/remotes/origin/main\n"
        if sub[:1] == ["rev-list"]:
            return "0\n"
        return ""

    def _reconcile(self, state, now, reopen_calls):
        def reopen_fetch(root, candidates):
            reopen_calls.append((root, sorted(candidates)))
            return set()

        with mock.patch.object(notify, "repo_name_for",
                               lambda root: "parovanie-produktov"), \
             mock.patch.object(notify, "card_marker_numbers",
                               lambda name: {41}), \
             mock.patch.object(notify, "forget_marker", lambda key: None):
            return cards.card_reconcile(
                now, lambda *a, **k: "", state, {"sid": "/repo"},
                dry_run=True, git_run=self._git_run,
                card_probe=lambda root, base: None,
                marker_ok=lambda key: True,
                reopen_fetch=reopen_fetch)

    def test_reopen_fetch_gated_to_once_per_900s(self):
        state = {}
        calls = []
        self._reconcile(state, 1_000_000, calls)          # first: fetches
        self._reconcile(state, 1_000_000 + 100, calls)    # +100s: within TTL, skip
        self._reconcile(state, 1_000_000 + 400, calls)    # +400s: still within, skip
        self.assertEqual(len(calls), 1, "reopen_fetch held by the 900s TTL")
        self._reconcile(state, 1_000_000 + 1000, calls)   # +1000s: TTL elapsed
        self.assertEqual(len(calls), 2, "reopen_fetch fires again past 900s")


class TestSubprocessBudgetHold(unittest.TestCase):
    """(e) a registry job with `max_subprocess` set is HELD once the sweep's
    subprocess count has reached the cap; the default None never holds."""

    def _run_once(self, **kwargs):
        with TemporaryDirectory() as d:
            return list(wd.run_once(
                now=1000.0, dry_run=True, run=lambda *a, **k: "",
                send_fn=lambda *a, **k: None,
                projects_dir=Path(d) / "proj",
                state_path=str(Path(d) / "state.json"),
                **kwargs,
            ))

    def test_card_reconcile_held_when_over_subprocess_budget(self):
        calls = []

        def _cr(*a, **k):
            calls.append(1)
            return []

        with mock.patch.object(wd, "card_reconcile", _cr), \
             mock.patch.object(wd, "subprocess_stats",
                               lambda: {"n": 999, "wall": 0.0,
                                        "by_label": {}, "top": []}):
            logs = self._run_once(card_probe=lambda *a, **k: None)
        self.assertEqual(calls, [], "card_reconcile must be HELD over budget")
        self.assertTrue(
            any(ln.startswith("card_reconcile -> hold:budget (subprocess")
                for ln in logs), "\n".join(logs))

    def test_card_reconcile_runs_under_budget(self):
        calls = []

        def _cr(*a, **k):
            calls.append(1)
            return []

        with mock.patch.object(wd, "card_reconcile", _cr), \
             mock.patch.object(wd, "subprocess_stats",
                               lambda: {"n": 0, "wall": 0.0,
                                        "by_label": {}, "top": []}):
            self._run_once(card_probe=lambda *a, **k: None)
        self.assertEqual(calls, [1], "card_reconcile runs when under budget")


if __name__ == "__main__":
    unittest.main()
