"""#993 round 2 item 7 — dependency ordering in the quals CLI + item 3's
dispatchable-count derivation.

`cli_work_class.dep_wait_map` resolves each workable row's `Depends-on:` (batched
`fetch_meta` or per-row, injected runner) and returns the dep-wait rows + their
blocking refs; `dispatchable_numbers` (pure) applies `workable ∧ ¬dep-wait ∧
(independent ∨ ¬live-infra-lane)`; `live_infra_lane` maps live lanes → issue
classes. `_print_issue_rows` stamps the `dep-wait:#N` action column.

Covers item 7 (open dep → excluded, closed dep → dispatchable, chain A→B→C only
A, cross-repo form, cycle → wait + printed) and item 3's count/reason.
"""

import io
import sys
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_work_class as wc  # noqa: E402
import cli_quals_cmd  # noqa: E402

SLUG = "zbynekdrlik/odoo-erp"


def _row(created="2026-01-01T00:00:00Z", title="t", labels=None):
    return {"createdAt": created, "title": title,
            "labels": [{"name": n} for n in (labels or [])]}


class _FakeRunner:
    """runner(argv, cwd) — canned gh JSON. `bodies` maps issue# → (body, [comments]);
    `states` maps (repo, num) → state string."""

    def __init__(self, bodies=None, states=None):
        self.bodies = bodies or {}
        self.states = states or {}
        self.calls = []

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        # gh issue view <n> --json body,comments  [-R repo]
        # gh issue view <n> --json state          [-R repo]
        try:
            n = int(argv[argv.index("view") + 1])
        except (ValueError, IndexError):
            return ""
        joined = " ".join(argv)
        if "body,comments" in joined:
            body, comments = self.bodies.get(n, ("", []))
            return json.dumps({"body": body,
                               "comments": [{"body": c} for c in comments]})
        if "state" in joined:
            repo = None
            if "-R" in argv:
                repo = argv[argv.index("-R") + 1]
            st = self.states.get((repo, n)) or self.states.get((None, n))
            return json.dumps({"state": st}) if st else "{}"
        return "{}"


class TestDepWaitMap(TestCase):
    def test_open_dep_is_dep_wait(self):
        rows = {"5": _row()}
        runner = _FakeRunner(bodies={5: ("Depends-on: #4", [])},
                             states={(SLUG, 4): "OPEN"})
        m = wc.dep_wait_map(rows, SLUG, runner, "/root")
        self.assertIn("5", m)
        self.assertEqual(m["5"], ["#4"])

    def test_closed_dep_is_not_dep_wait(self):
        rows = {"5": _row()}
        runner = _FakeRunner(bodies={5: ("Depends-on: #4", [])},
                             states={(SLUG, 4): "CLOSED"})
        m = wc.dep_wait_map(rows, SLUG, runner, "/root")
        self.assertNotIn("5", m)

    def test_no_depends_on_is_not_dep_wait(self):
        rows = {"5": _row()}
        runner = _FakeRunner(bodies={5: ("just a normal body", [])})
        m = wc.dep_wait_map(rows, SLUG, runner, "/root")
        self.assertEqual(m, {})

    def test_chain_A_B_C_only_A_dispatchable(self):
        # A(1) no deps; B(2) depends on A; C(3) depends on B; A open, B open
        rows = {"1": _row(), "2": _row(), "3": _row()}
        runner = _FakeRunner(
            bodies={1: ("no deps", []),
                    2: ("Depends-on: #1", []),
                    3: ("Depends-on: #2", [])},
            states={(SLUG, 1): "OPEN", (SLUG, 2): "OPEN"})
        m = wc.dep_wait_map(rows, SLUG, runner, "/root")
        self.assertNotIn("1", m)      # A dispatchable
        self.assertIn("2", m)         # B waits on A
        self.assertIn("3", m)         # C waits on B

    def test_cross_repo_ref_form(self):
        rows = {"5": _row()}
        runner = _FakeRunner(
            bodies={5: ("Depends-on: owner/other#7", [])},
            states={("owner/other", 7): "OPEN"})
        m = wc.dep_wait_map(rows, SLUG, runner, "/root")
        self.assertIn("5", m)
        self.assertEqual(m["5"], ["owner/other#7"])

    def test_cycle_is_wait_and_printed(self):
        # self-dependency (1-cycle): #5 Depends-on #5, itself open → wait
        rows = {"5": _row()}
        runner = _FakeRunner(bodies={5: ("Depends-on: #5", [])},
                             states={(SLUG, 5): "OPEN"})
        m = wc.dep_wait_map(rows, SLUG, runner, "/root")
        self.assertIn("5", m)
        self.assertEqual(m["5"], ["#5"])

    def test_unresolvable_dep_is_wait(self):
        rows = {"5": _row()}
        runner = _FakeRunner(bodies={5: ("Depends-on: #4", [])}, states={})
        m = wc.dep_wait_map(rows, SLUG, runner, "/root")
        self.assertIn("5", m)


class TestDispatchableNumbers(TestCase):
    def test_dep_wait_excluded_from_candidates(self):
        rows = {"1": _row(), "2": _row()}
        d, reason = wc.dispatchable_numbers(
            rows, SLUG, {"2": ["#1"]}, infra_lane_live=False)
        self.assertEqual(d, {"1"})
        self.assertIsNone(reason)

    def test_infra_excluded_when_lane_live(self):
        rows = {"1": _row(labels=["infra"]), "2": _row(labels=["bug"])}
        d, reason = wc.dispatchable_numbers(
            rows, SLUG, {}, infra_lane_live=True)
        self.assertEqual(d, {"2"})     # independent stays; infra held
        self.assertIsNone(reason)

    def test_infra_dispatchable_when_no_lane(self):
        rows = {"1": _row(labels=["infra"])}
        d, reason = wc.dispatchable_numbers(
            rows, SLUG, {}, infra_lane_live=False)
        self.assertEqual(d, {"1"})

    def test_airuleset_repo_serial_reason(self):
        rows = {"1": _row(labels=["bug"])}
        d, reason = wc.dispatchable_numbers(
            rows, "zbynekdrlik/airuleset", {}, infra_lane_live=True)
        self.assertEqual(d, set())
        self.assertEqual(reason, "infra-serial")

    def test_dep_wait_only_reason(self):
        rows = {"1": _row(labels=["bug"])}
        d, reason = wc.dispatchable_numbers(
            rows, SLUG, {"1": ["#9"]}, infra_lane_live=False)
        self.assertEqual(d, set())
        self.assertEqual(reason, "dep-wait")


class TestLiveInfraLane(TestCase):
    def test_no_lanes_is_false(self):
        self.assertFalse(
            wc.live_infra_lane(SLUG, None, "/root",
                                       gather_fn=lambda root: []))

    def test_airuleset_any_lane_is_infra(self):
        lanes = [{"ref": "worktree-x", "issues": [42]}]
        self.assertTrue(
            wc.live_infra_lane("zbynekdrlik/airuleset", None, "/root",
                                       gather_fn=lambda root: lanes))

    def test_unresolvable_lane_is_infra_failsafe(self):
        lanes = [{"ref": "worktree-x", "issues": []}]
        self.assertTrue(
            wc.live_infra_lane(SLUG, None, "/root",
                                       gather_fn=lambda root: lanes))

    def test_independent_lane_only_is_not_infra(self):
        lanes = [{"ref": "worktree-x", "issues": [7]}]
        runner = _FakeRunner()
        # #7 has no infra label → independent
        def labels_fn(n, runner_, root):
            return [{"name": "bug"}]
        self.assertFalse(
            wc.live_infra_lane(SLUG, runner, "/root",
                                       gather_fn=lambda root: lanes,
                                       labels_fn=labels_fn))


class TestListActionColumn(TestCase):
    def _emit(self, rows, dep_wait_map):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(rows, dep_wait_map=dep_wait_map)
        return buf.getvalue().splitlines()

    def test_dep_wait_row_shows_dep_wait_action(self):
        rows = {"5": _row(title="blocked")}
        lines = self._emit(rows, {"5": ["#4"]})
        # number<TAB>createdAt<TAB>action<TAB>title
        fields = lines[0].split("\t")
        self.assertEqual(fields[0], "5")
        self.assertEqual(fields[2], "dep-wait:#4")

    def test_non_dep_wait_row_unchanged(self):
        rows = {"5": _row(title="free")}
        lines = self._emit(rows, {})
        fields = lines[0].split("\t")
        self.assertIn(fields[2], ("implement", "action-only"))


class TestWatchdogSeams(TestCase):
    """#993 review 8 — the two production watchdog seams: the dispatchable-count
    fetch (protocol + fail-safe) and the queue-classify factory (fail-safe)."""

    def _fetch(self, stdout, rc=0):
        import unittest.mock as mk
        import airuleset

        class _CP:
            returncode = rc
            def __init__(s):
                s.stdout = stdout
        with mk.patch("airuleset._repo_root", return_value="/root"), \
             mk.patch("airuleset.resolve_authority", return_value="full"), \
             mk.patch("subprocess.run", return_value=_CP()):
            return airuleset._watchdog_dispatchable_fetch("/root")

    def test_count_and_reason_parsed(self):
        self.assertEqual(self._fetch("0\nreason:infra-serial\n"),
                         [{"count": 0, "reason": "infra-serial"}])
        self.assertEqual(self._fetch("3\n"), [{"count": 3, "reason": None}])

    def test_unmeasurable_is_none(self):
        self.assertIsNone(self._fetch("unmeasurable\n"))

    def test_nonzero_rc_is_none(self):
        self.assertIsNone(self._fetch("5\n", rc=1))

    def test_queue_classify_non_full_is_none(self):
        import unittest.mock as mk
        import airuleset
        with mk.patch("airuleset._repo_root", return_value="/root"), \
             mk.patch("airuleset.resolve_authority", return_value="fork-no-merge"):
            self.assertIsNone(airuleset._watchdog_queue_classify("/root"))

    def test_queue_classify_full_returns_callable_failsafe(self):
        import unittest.mock as mk
        import airuleset
        with mk.patch("airuleset._repo_root", return_value="/root"), \
             mk.patch("airuleset.resolve_authority", return_value="full"), \
             mk.patch("airuleset._repo_slug", return_value="o/r"), \
             mk.patch("cli_work_class.live_infra_lane", return_value=False), \
             mk.patch("cli_work_class.classify_number", side_effect=RuntimeError):
            fn = airuleset._watchdog_queue_classify("/root")
            self.assertTrue(callable(fn))
            # a classify error → fail-safe infra-serial (HOLD, never a spurious
            # parallel infra dispatch).
            self.assertEqual(fn(5), "infra-serial")

    def test_queue_classify_full_dispatchable_passthrough(self):
        import unittest.mock as mk
        import airuleset
        with mk.patch("airuleset._repo_root", return_value="/root"), \
             mk.patch("airuleset.resolve_authority", return_value="full"), \
             mk.patch("airuleset._repo_slug", return_value="o/r"), \
             mk.patch("cli_work_class.live_infra_lane", return_value=False), \
             mk.patch("cli_work_class.classify_number", return_value="dispatchable"):
            fn = airuleset._watchdog_queue_classify("/root")
            self.assertEqual(fn(7), "dispatchable")


if __name__ == "__main__":
    main()
