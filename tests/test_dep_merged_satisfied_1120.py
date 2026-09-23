"""#1120 (b) — a `Depends-on: #N` reference counts as SATISFIED when #N is
CLOSED **or** when #N is in the repo's merged-unreleased set (the `M` bucket;
the ONE #1083/#1112 derivation). A cross-stream adoption ticket therefore
becomes dispatchable the moment the mechanism MERGES, not days later at
release/close.

Root cause (design 5791161529): `cli_work_class.dep_wait` satisfied a dep only
on CLOSED, so the odoo-erp #7892 adoption sat dep-wait on #7883 until #7883
CLOSED (post-release). One shared predicate (`dep_wait`, given a `merged_fn`)
fixes all three consumers (`dep_wait_map` / `classify_number` /
`resolve_issue_deps`). Fail-safe unchanged: an unresolvable dep still blocks;
`merged_fn=None` (default) preserves today's CLOSED-only behaviour.
"""

import json
import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_work_class as wc  # noqa: E402

SLUG = "zbynekdrlik/odoo-erp"


def _state(states):
    def state_fn(repo, num):
        return states.get((repo, num))
    return state_fn


def _merged(*pairs):
    """merged_fn satisfying exactly the given (repo, num) pairs."""
    s = set(pairs)

    def fn(repo, num):
        return (repo, num) in s
    return fn


def _never_merged(repo, num):
    return False


class TestDepWaitMergedSatisfied(TestCase):
    """The ONE shared predicate: dep_wait, given a merged_fn."""

    def test_open_dep_in_merged_set_is_satisfied(self):
        # #7892 depends on #7883; #7883 is OPEN but merged into integration.
        deps = [(SLUG, 7883)]
        merged_fn = _merged((SLUG, 7883))
        blocked, unsat = wc.dep_wait(
            deps, _state({(SLUG, 7883): "OPEN"}), merged_fn=merged_fn)
        self.assertFalse(blocked, "an OPEN dep in the merged-unreleased set "
                                  "must count as satisfied")
        self.assertEqual(unsat, [])

    def test_open_dep_not_merged_is_dep_wait(self):
        deps = [(SLUG, 7883)]
        merged_fn = _never_merged
        blocked, unsat = wc.dep_wait(
            deps, _state({(SLUG, 7883): "OPEN"}), merged_fn=merged_fn)
        self.assertTrue(blocked, "an OPEN, un-merged dep is still dep-wait")
        self.assertEqual(unsat, [(SLUG, 7883)])

    def test_closed_dep_is_satisfied_as_today(self):
        deps = [(SLUG, 7883)]
        merged_fn = _never_merged
        blocked, _ = wc.dep_wait(
            deps, _state({(SLUG, 7883): "CLOSED"}), merged_fn=merged_fn)
        self.assertFalse(blocked)

    def test_unresolvable_dep_still_blocks(self):
        # state_fn → None AND not merged → fail-safe blocked.
        deps = [(SLUG, 9999)]
        merged_fn = _never_merged
        blocked, unsat = wc.dep_wait(deps, _state({}), merged_fn=merged_fn)
        self.assertTrue(blocked)
        self.assertEqual(unsat, [(SLUG, 9999)])

    def test_merged_fn_none_is_closed_only_behaviour(self):
        deps = [(SLUG, 7883)]
        # OPEN + no merged_fn → blocked (today's behaviour preserved).
        blocked, _ = wc.dep_wait(deps, _state({(SLUG, 7883): "OPEN"}))
        self.assertTrue(blocked)

    def test_mixed_one_merged_one_open(self):
        deps = [(SLUG, 7883), (SLUG, 8000)]
        merged_fn = _merged((SLUG, 7883))
        blocked, unsat = wc.dep_wait(
            deps,
            _state({(SLUG, 7883): "OPEN", (SLUG, 8000): "OPEN"}),
            merged_fn=merged_fn)
        self.assertTrue(blocked)
        self.assertEqual(unsat, [(SLUG, 8000)])


class _MergedRunner:
    """runner(argv, cwd) — per-row `gh issue view` bodies + states."""

    def __init__(self, bodies=None, states=None):
        self.bodies = bodies or {}
        self.states = states or {}

    def __call__(self, argv, cwd):
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
            repo = argv[argv.index("-R") + 1] if "-R" in argv else None
            st = self.states.get((repo, n)) or self.states.get((None, n))
            return json.dumps({"state": st}) if st else "{}"
        return "{}"


class TestDepWaitMapMergedPassthrough(TestCase):
    """dep_wait_map threads merged_fn to dep_wait: an OPEN dep in the merged
    set drops the row out of the dep-wait map."""

    def test_row_with_merged_dep_is_dispatchable(self):
        rows = {7892: {"createdAt": "2026-01-01T00:00:00Z",
                       "title": "adopt", "labels": []}}
        runner = _MergedRunner(
            bodies={7892: ("Depends-on: #7883", [])},
            states={(SLUG, 7883): "OPEN"})
        merged_fn = _merged((SLUG, 7883))
        m = wc.dep_wait_map(rows, SLUG, runner, "/root", merged_fn=merged_fn)
        self.assertNotIn(7892, m, "an adoption row whose mechanism is merged "
                                  "must be dispatchable, not dep-wait")

    def test_row_with_open_unmerged_dep_is_dep_wait(self):
        rows = {7892: {"createdAt": "2026-01-01T00:00:00Z",
                       "title": "adopt", "labels": []}}
        runner = _MergedRunner(
            bodies={7892: ("Depends-on: #7883", [])},
            states={(SLUG, 7883): "OPEN"})
        merged_fn = _never_merged
        m = wc.dep_wait_map(rows, SLUG, runner, "/root", merged_fn=merged_fn)
        self.assertIn(7892, m)


class TestResolveIssueDepsMerged(TestCase):
    def test_merged_dep_is_satisfied(self):
        runner = _MergedRunner(
            bodies={7892: ("Depends-on: #7883", [])},
            states={(SLUG, 7883): "OPEN"})
        merged_fn = _merged((SLUG, 7883))
        self.assertEqual(
            wc.resolve_issue_deps([7892], SLUG, runner, "/root",
                                  merged_fn=merged_fn),
            "satisfied")


class TestMergedUnreleasedFnFactory(TestCase):
    """The ONE factory that builds merged_fn from the ONE M-bucket derivation
    (cli_release_state.merged_unreleased_issues), LOCAL-repo-scoped."""

    def test_factory_true_for_same_repo_member(self):
        import unittest.mock as mock
        with mock.patch(
                "cli_release_state.merged_unreleased_issues",
                return_value=frozenset({7883})):
            fn = wc.merged_unreleased_fn(SLUG, "/root")
        self.assertTrue(fn(SLUG, 7883))
        self.assertFalse(fn(SLUG, 9999))

    def test_factory_false_for_cross_repo(self):
        import unittest.mock as mock
        with mock.patch(
                "cli_release_state.merged_unreleased_issues",
                return_value=frozenset({7883})):
            fn = wc.merged_unreleased_fn(SLUG, "/root")
        # A cross-repo dep is never satisfied by the LOCAL merged set.
        self.assertFalse(fn("zbynekdrlik/airuleset", 7883))

    def test_factory_fail_safe_empty_on_error(self):
        import unittest.mock as mock
        with mock.patch(
                "cli_release_state.merged_unreleased_issues",
                side_effect=RuntimeError("boom")):
            fn = wc.merged_unreleased_fn(SLUG, "/root")
        self.assertFalse(fn(SLUG, 7883))


if __name__ == "__main__":
    main()
