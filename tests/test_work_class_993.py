"""#993 round 2 — the PURE orchestration-classification module `cli_work_class`.

work_class(repo, labels), Depends-on: parsing + resolution, the dep-wait
predicate and the dispatchable predicate (deps-only since round 2b — the
class-based lane-serialisation helpers were removed). All pure +
dependency-injected; the gh/git IO lives in the callers.

Covers item 1 (work_class), item 6 (c) airuleset serial regardless of labels,
(d) missing labels → infra, (g) work_class unit table, and item 7's pure
dependency logic (parse, cross-repo form, cycle/unresolvable → wait).
"""

import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_work_class as wc  # noqa: E402


def _labels(*names):
    return [{"name": n} for n in names]


class TestWorkClass(TestCase):
    # (g) the work_class unit table
    def test_airuleset_repo_is_infra_regardless_of_labels(self):
        # (c) airuleset repo → serial regardless of labels
        self.assertEqual(wc.work_class("zbynekdrlik/airuleset", []), "infra")
        self.assertEqual(wc.work_class("zbynekdrlik/airuleset", _labels("bug")),
                         "infra")
        self.assertEqual(
            wc.work_class("zbynekdrlik/airuleset", _labels("independent")), "infra")

    def test_other_repo_infra_label_is_infra(self):
        self.assertEqual(wc.work_class("zbynekdrlik/odoo-erp", _labels("infra")),
                         "infra")

    def test_other_repo_architecture_rework_is_infra(self):
        self.assertEqual(
            wc.work_class("zbynekdrlik/odoo-erp", _labels("architecture-rework")),
            "infra")

    def test_other_repo_no_infra_label_is_independent(self):
        self.assertEqual(wc.work_class("zbynekdrlik/odoo-erp", _labels("bug")),
                         "independent")
        # genuinely unlabelled (empty list) on a non-airuleset repo → independent
        self.assertEqual(wc.work_class("zbynekdrlik/odoo-erp", []), "independent")

    def test_missing_labels_is_infra(self):
        # (d) missing/undeterminable labels → infra (fail-safe serial)
        self.assertEqual(wc.work_class("zbynekdrlik/odoo-erp", None), "infra")
        self.assertEqual(wc.work_class("zbynekdrlik/odoo-erp", "garbage"), "infra")

    def test_empty_repo_falls_back_to_label_read(self):
        # an unknown/empty repo is not airuleset → decided by labels
        self.assertEqual(wc.work_class("", _labels("infra")), "infra")
        self.assertEqual(wc.work_class(None, _labels("bug")), "independent")


class TestParseDependsOn(TestCase):
    def test_single_bare_ref(self):
        self.assertEqual(wc.parse_depends_on("Depends-on: #41"), ["#41"])

    def test_multiple_refs_comma_separated(self):
        self.assertEqual(wc.parse_depends_on("Depends-on: #41, #43"),
                         ["#41", "#43"])

    def test_cross_repo_ref(self):
        self.assertEqual(
            wc.parse_depends_on("Depends-on: owner/repo#7"),
            ["owner/repo#7"])

    def test_mixed_bare_and_cross_repo(self):
        self.assertEqual(
            wc.parse_depends_on("Depends-on: #41, owner/repo#7"),
            ["#41", "owner/repo#7"])

    def test_no_line_is_empty(self):
        self.assertEqual(wc.parse_depends_on("just some body text"), [])
        self.assertEqual(wc.parse_depends_on(""), [])
        self.assertEqual(wc.parse_depends_on(None), [])

    def test_line_anywhere_in_body(self):
        body = "## Title\n\nsome text\n\nDepends-on: #99\n\nmore text"
        self.assertEqual(wc.parse_depends_on(body), ["#99"])

    def test_last_line_wins_within_text(self):
        body = "Depends-on: #1\n\nlater\n\nDepends-on: #2, #3"
        self.assertEqual(wc.parse_depends_on(body), ["#2", "#3"])


class TestDependsOnRefs(TestCase):
    def test_body_line_used_when_no_comment(self):
        self.assertEqual(wc.depends_on_refs("Depends-on: #5", []), ["#5"])

    def test_last_supervisor_comment_wins_over_body(self):
        refs = wc.depends_on_refs(
            "Depends-on: #5",
            ["Depends-on: #6", "unrelated comment", "Depends-on: #7, #8"])
        self.assertEqual(refs, ["#7", "#8"])

    def test_comment_must_start_with_depends_on(self):
        # a comment merely MENTIONING Depends-on mid-body does not override
        refs = wc.depends_on_refs("Depends-on: #5",
                                  ["see the Depends-on: #6 note above"])
        self.assertEqual(refs, ["#5"])

    def test_no_body_no_comment(self):
        self.assertEqual(wc.depends_on_refs("", []), [])
        self.assertEqual(wc.depends_on_refs(None, None), [])

    def test_trusted_dict_comment_override_wins(self):
        # #993 review 6: a SUPERVISOR (trusted association) comment overrides.
        refs = wc.depends_on_refs(
            "Depends-on: #5",
            [{"body": "Depends-on: #7", "authorAssociation": "OWNER"}])
        self.assertEqual(refs, ["#7"])

    def test_low_trust_dict_comment_override_ignored(self):
        # a NONE/low-trust commenter must NOT be able to unblock a dep-wait
        # ticket — the body's Depends-on stands.
        refs = wc.depends_on_refs(
            "Depends-on: #5",
            [{"body": "Depends-on: #99999", "authorAssociation": "NONE"}])
        self.assertEqual(refs, ["#5"])

    def test_bare_string_comment_is_trusted_backcompat(self):
        refs = wc.depends_on_refs(
            "Depends-on: #5", ["Depends-on: #6"])
        self.assertEqual(refs, ["#6"])


class TestFetchMeta(TestCase):
    """#993 review 2 — the batched meta read (ONE gh issue list) that replaces
    per-row gh issue view for the workable set."""

    def _runner(self, payload):
        return lambda argv, _cwd: __import__("json").dumps(payload)

    def test_batched_meta_filters_to_wanted(self):
        payload = [
            {"number": 5, "body": "Depends-on: #4",
             "comments": [{"body": "hi", "authorAssociation": "OWNER"}]},
            {"number": 99, "body": "unrelated", "comments": []},
        ]
        m = wc.fetch_meta(["5"], self._runner(payload), "/root")
        self.assertIn(5, m)
        self.assertNotIn(99, m)
        self.assertEqual(m[5]["body"], "Depends-on: #4")
        self.assertEqual(m[5]["comments"][0]["authorAssociation"], "OWNER")

    def test_fetch_meta_none_on_failure(self):
        def boom(argv, _cwd):
            raise RuntimeError("gh down")
        self.assertIsNone(wc.fetch_meta(["5"], boom, "/root"))

    def test_fetch_meta_empty_numbers(self):
        self.assertEqual(wc.fetch_meta([], self._runner([]), "/root"), {})

    def test_dep_wait_map_uses_meta_no_per_row_gh(self):
        calls = []

        def runner(argv, _cwd):
            calls.append(argv)
            import json as _j
            # only the dep STATE view is allowed via runner when meta is used
            if "state" in " ".join(argv):
                return _j.dumps({"state": "OPEN"})
            return "{}"
        rows = {"5": {"createdAt": "2026-01-01T00:00:00Z", "labels": []}}
        meta = {5: {"body": "Depends-on: #4", "comments": []}}
        m = wc.dep_wait_map(rows, "o/r", runner, "/root", meta=meta)
        self.assertEqual(m, {"5": ["#4"]})
        # NO `gh issue view <n> --json body,comments` was made (meta provided)
        self.assertFalse(any("body,comments" in " ".join(a) for a in calls))


class TestNormalizeRef(TestCase):
    def test_bare_ref_uses_default_repo(self):
        self.assertEqual(wc.normalize_ref("#41", "zbynekdrlik/airuleset"),
                         ("zbynekdrlik/airuleset", 41))

    def test_cross_repo_ref_keeps_its_repo(self):
        self.assertEqual(wc.normalize_ref("owner/repo#7", "zbynekdrlik/airuleset"),
                         ("owner/repo", 7))

    def test_unparseable_ref_is_none(self):
        self.assertIsNone(wc.normalize_ref("garbage", "x/y"))


class TestDepWait(TestCase):
    def _state(self, mapping):
        return lambda repo, num: mapping.get((repo, num))

    def test_no_deps_not_wait(self):
        self.assertEqual(wc.dep_wait([], self._state({})), (False, []))

    def test_open_dep_is_wait(self):
        # (item 7) open dep → dep-wait
        deps = [("r", 1)]
        blocked, unsat = wc.dep_wait(deps, self._state({("r", 1): "OPEN"}))
        self.assertTrue(blocked)
        self.assertEqual(unsat, [("r", 1)])

    def test_closed_dep_is_dispatchable(self):
        # (item 7) closed dep → dispatchable
        deps = [("r", 1)]
        blocked, unsat = wc.dep_wait(deps, self._state({("r", 1): "CLOSED"}))
        self.assertFalse(blocked)
        self.assertEqual(unsat, [])

    def test_unresolvable_dep_is_wait(self):
        # (item 7) unresolvable (state None) → treated as OPEN → wait + printed
        deps = [("r", 1)]
        blocked, unsat = wc.dep_wait(deps, self._state({}))
        self.assertTrue(blocked)
        self.assertEqual(unsat, [("r", 1)])

    def test_any_open_among_closed_is_wait(self):
        deps = [("r", 1), ("r", 2)]
        blocked, unsat = wc.dep_wait(
            deps, self._state({("r", 1): "CLOSED", ("r", 2): "OPEN"}))
        self.assertTrue(blocked)
        self.assertEqual(unsat, [("r", 2)])

    def test_self_reference_cycle_is_wait(self):
        # (item 7) cycle → wait. A self-dep is a 1-cycle.
        deps = [("r", 5)]
        blocked, unsat = wc.dep_wait(deps, self._state({("r", 5): "OPEN"}),
                                     self_ref=("r", 5))
        self.assertTrue(blocked)
        self.assertEqual(unsat, [("r", 5)])


class TestDispatchable(TestCase):
    # #993 r2b: dispatchable = workable ∧ deps satisfied (deps-only; the
    # class-based infra-lane arg was removed).
    def test_deps_satisfied_is_dispatchable(self):
        self.assertTrue(wc.dispatchable(False))

    def test_dep_wait_never_dispatchable(self):
        self.assertFalse(wc.dispatchable(True))


if __name__ == "__main__":
    main()
