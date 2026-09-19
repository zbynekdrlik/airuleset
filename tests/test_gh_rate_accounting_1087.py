"""#1087 L1 items (a) + (c) — per-box `gh` call accounting + honest zero-budget
fast-fail line, both riding the #1040 rate-guard shim.

(a) every shim invocation appends one counter to ~/.claude/gh-rate/calls-
    <YYYY-MM-DD>.json keyed by (first two subcommand words, poller|human|write)
    under the hour — no argv beyond two words, no env, fail-open. `gh-rate --top`
    prints the day's top burners; the exhausted alert line carries the current
    hour's top-3; the `status` row carries the day's total.
(c) when the cached status shows remaining==0 for the resource a NON-POLLER read
    will use, the shim prints ONE honest stderr line before exec'ing the real gh
    (never blocks; absent for pollers/writes).

Hermetic: gh_rate_dir/status_path redirected to a tmp dir; no gh, no network.
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_gh_rate


# a fixed instant: 2026-09-19 14:xx local
_NOW = time.mktime(time.strptime("2026-09-19 14:05:00", "%Y-%m-%d %H:%M:%S"))


class _Tmp(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="ghrate-1087-")
        self._gd = cli_gh_rate.gh_rate_dir
        self._sp = cli_gh_rate.status_path
        cli_gh_rate.gh_rate_dir = lambda: self.tmp
        cli_gh_rate.status_path = lambda: os.path.join(self.tmp, "status.json")

    def tearDown(self):
        cli_gh_rate.gh_rate_dir = self._gd
        cli_gh_rate.status_path = self._sp


class ClassifyKind(unittest.TestCase):
    def test_internal_env_is_internal(self):
        self.assertEqual(
            cli_gh_rate.classify_kind(["api", "rate_limit"],
                                      {cli_gh_rate.INTERNAL_ENV: "1"}), "internal")

    def test_poller_env_is_poller(self):
        self.assertEqual(
            cli_gh_rate.classify_kind(["issue", "list", "--json", "number"],
                                      {cli_gh_rate.POLLER_ENV: "1"}), "poller")

    def test_read_shape_no_env_is_human(self):
        self.assertEqual(cli_gh_rate.classify_kind(["issue", "view", "5"], {}),
                         "human")

    def test_write_shape_is_write(self):
        self.assertEqual(cli_gh_rate.classify_kind(
            ["issue", "comment", "5", "--body", "hi"], {}), "write")
        self.assertEqual(cli_gh_rate.classify_kind(
            ["pr", "merge", "7"], {}), "write")
        self.assertEqual(cli_gh_rate.classify_kind(
            ["api", "repos/o/r/issues", "-X", "POST"], {}), "write")


class RecordCall(_Tmp):
    def test_counter_shape_and_increment(self):
        cli_gh_rate.record_call(["issue", "view", "5"], env={}, now=_NOW)
        cli_gh_rate.record_call(["issue", "view", "9"], env={}, now=_NOW)
        data = cli_gh_rate.load_calls(now=_NOW)
        # nested by hour; key = "issue view|human"; two views -> count 2; no argv
        # beyond the two subcommand words (numbers/flags never in the key)
        self.assertEqual(data, {"14": {"issue view|human": 2}})

    def test_internal_refresh_is_not_counted(self):
        cli_gh_rate.record_call(["api", "rate_limit"],
                                env={cli_gh_rate.INTERNAL_ENV: "1"}, now=_NOW)
        self.assertEqual(cli_gh_rate.load_calls(now=_NOW), {})

    def test_poller_and_write_classes_recorded(self):
        cli_gh_rate.record_call(["issue", "list", "--json", "number"],
                                env={cli_gh_rate.POLLER_ENV: "1"}, now=_NOW)
        cli_gh_rate.record_call(["pr", "merge", "7"], env={}, now=_NOW)
        data = cli_gh_rate.load_calls(now=_NOW)["14"]
        self.assertEqual(data.get("issue list|poller"), 1)
        self.assertEqual(data.get("pr merge|write"), 1)

    def test_fail_open_never_raises(self):
        cli_gh_rate.gh_rate_dir = lambda: "/proc/nonexistent/cannot/create"
        # must not raise, must not delay
        cli_gh_rate.record_call(["issue", "view", "5"], env={}, now=_NOW)


class TopAndTotals(_Tmp):
    def _seed(self):
        for _ in range(5):
            cli_gh_rate.record_call(["issue", "list", "--json", "n"],
                                    env={cli_gh_rate.POLLER_ENV: "1"}, now=_NOW)
        for _ in range(3):
            cli_gh_rate.record_call(["pr", "checks", "7"],
                                    env={cli_gh_rate.POLLER_ENV: "1"}, now=_NOW)
        cli_gh_rate.record_call(["pr", "merge", "7"], env={}, now=_NOW)

    def test_top_burners_sorted(self):
        self._seed()
        data = cli_gh_rate.load_calls(now=_NOW)
        top = cli_gh_rate.top_burners(data)
        self.assertEqual(top[0], ("issue list|poller", 5))
        self.assertEqual(top[1], ("pr checks|poller", 3))
        self.assertEqual(cli_gh_rate.day_total(data), 9)

    def test_hour_top3_line(self):
        self._seed()
        data = cli_gh_rate.load_calls(now=_NOW)
        top3 = cli_gh_rate.hour_top3(data, "14")
        self.assertEqual([k for k, _ in top3], ["issue list|poller",
                                                "pr checks|poller", "pr merge|write"])

    def test_alert_line_carries_top3(self):
        self._seed()
        status = {"resources": {"graphql": {"remaining": 100, "limit": 5000,
                                            "reset": int(_NOW) + 1800}}}
        line = cli_gh_rate.alert_line(
            "graphql", status, burners=cli_gh_rate.current_hour_burner_suffix(now=_NOW))
        self.assertIn("issue list|poller", line)
        self.assertIn("EXHAUSTED", line)

    def test_status_row_carries_day_total(self):
        self._seed()
        status = {"resources": {"core": {"remaining": 4000, "limit": 5000,
                                         "reset": int(_NOW)}}}
        row = cli_gh_rate.status_row(status, now=_NOW)
        self.assertIn("calls 9", row)


class ZeroBudgetLine(_Tmp):
    def _write_status(self, resources):
        with open(cli_gh_rate.status_path(), "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": _NOW, "resources": resources}, fh)

    def test_present_for_human_read_at_zero_core(self):
        self._write_status({"core": {"remaining": 0, "limit": 5000,
                                     "reset": int(_NOW) + 1800}})
        line = cli_gh_rate.zero_budget_line(["issue", "view", "5"], env={},
                                            now=_NOW)
        self.assertIn("budget exhausted", line)
        self.assertIn("core", line)

    def test_absent_when_budget_remains(self):
        self._write_status({"core": {"remaining": 100, "limit": 5000,
                                     "reset": int(_NOW)}})
        self.assertEqual(
            cli_gh_rate.zero_budget_line(["issue", "view", "5"], env={},
                                         now=_NOW), "")

    def test_absent_for_poller(self):
        self._write_status({"core": {"remaining": 0, "limit": 5000,
                                     "reset": int(_NOW)}})
        self.assertEqual(
            cli_gh_rate.zero_budget_line(["issue", "view", "5"],
                                         env={cli_gh_rate.POLLER_ENV: "1"},
                                         now=_NOW), "")

    def test_absent_for_write(self):
        self._write_status({"core": {"remaining": 0, "limit": 5000,
                                     "reset": int(_NOW)}})
        self.assertEqual(
            cli_gh_rate.zero_budget_line(["pr", "merge", "7"], env={},
                                         now=_NOW), "")

    def test_graphql_read_uses_graphql_budget(self):
        # a --json read is graphql; core at 0 but graphql fine -> no line
        self._write_status({"core": {"remaining": 0, "limit": 5000, "reset": 0},
                            "graphql": {"remaining": 500, "limit": 5000,
                                        "reset": 0}})
        self.assertEqual(
            cli_gh_rate.zero_budget_line(["issue", "list", "--json", "number"],
                                         env={}, now=_NOW), "")


if __name__ == "__main__":
    unittest.main()
