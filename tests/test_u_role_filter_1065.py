"""#1065 — the footer/quals `U` (owner-court) is partitioned by window role
EXACTLY like `I` and `W`. REVERSES the #1025/#1045 "U is never role-dropped"
choice.

Owner ruling (issue #1065, 20.9.2026, verbatim): "dlho hlaseny bug. U 1 v gk
nie je gk ale gk infra stale ma to pletie ze obydva hlasia pocet otazok aj ked
sa tykaju druheho!!" — an `infra`-labelled owner question must show ONLY in the
INFRA window; every other owner question ONLY in the FLOW (review) window. Live
proof on the ticket: both gk caches showed `U=1 [7720]` while odoo-erp#7720 is
`infra`+`needs-answer` (the INFRA session's question).

Design (Design-by: main claude-fable-5-1, Approach 1): `_role_filter_footer`
applies the SAME `_apply_role_filter` predicate to `waiting` (the
needs-answer/needs-decision/queued-needs-acceptance set) as to `workable` (I) and
`ops_wait` (W): `infra` → INFRA window only, everything else → FLOW window only;
no role → unchanged. `cmd_core_quals`/`cmd_slice_quals` `--waiting` and the
`U N?` step-by-step flow (which reads the per-cwd cache) read the filtered set.

Invariant: on a two-window box U(FLOW) + U(INFRA) == U(unfiltered) — every
question belongs to exactly ONE window; nothing is lost.

RED-first: against the #1025/#1045 code, `_role_filter_footer` returns `waiting`
UNFILTERED for both roles, so `test_review_role_drops_infra_from_U` and the
invariant/`--waiting` cases below FAIL.
"""
import contextlib
import io
import json
import sys
import time
import unittest.mock as m
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import airuleset  # noqa: E402
import cli_quals_cmd  # noqa: E402
import cli_concurrency  # noqa: E402
import statusbar  # noqa: E402

ODOO = "zbynekdrlik/odoo-erp"


def _rows(*specs):
    return {k: {"createdAt": "2026-01-01T00:00:00Z", "title": str(k),
                "labels": [{"name": n} for n in labels]}
            for k, labels in specs}


# --------------------------------------------------------------------------- #
# _role_filter_footer — U now narrows by role like I/W.
# --------------------------------------------------------------------------- #
class TestFooterURoleFiltered(TestCase):
    def setUp(self):
        # I: infra "b" + review "a"; U: infra "d" + review "e"; W: infra "f" +
        # review "g" — so the role filter is meaningful in BOTH directions.
        self.workable = _rows(("a", []), ("b", ["infra"]))
        self.waiting = _rows(("d", ["infra", "needs-answer"]),
                             ("e", ["needs-decision"]))
        self.ops = _rows(("f", ["infra", "ops-wait"]), ("g", ["ops-wait"]))

    def _run(self, role):
        with m.patch.object(cli_concurrency, "resolve_role", return_value=role), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            return airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")

    def test_review_role_drops_infra_from_U(self):
        w, wa, o = self._run("review")
        self.assertEqual(set(w), {"a"})       # I filtered (unchanged)
        self.assertEqual(set(wa), {"e"})      # #1065: U now filtered — infra "d" DROPPED
        self.assertEqual(set(o), {"g"})       # W filtered (unchanged, #1045)

    def test_infra_role_keeps_only_infra_in_U(self):
        w, wa, o = self._run("infra")
        self.assertEqual(set(w), {"b"})       # I filtered (unchanged)
        self.assertEqual(set(wa), {"d"})      # #1065: U now filtered — only infra "d"
        self.assertEqual(set(o), {"f"})       # W filtered (unchanged, #1045)

    def test_role_none_returns_U_unchanged(self):
        # single-window boxes / streams: no role → no filter (byte-identical).
        with m.patch.object(cli_concurrency, "resolve_role", return_value=None):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual((w, wa, o), (self.workable, self.waiting, self.ops))

    def test_invariant_flow_plus_infra_equals_unfiltered(self):
        # U(FLOW) + U(INFRA) == U(unfiltered): every question in exactly one window.
        _, u_review, _ = self._run("review")
        _, u_infra, _ = self._run("infra")
        self.assertEqual(set(u_review) | set(u_infra), set(self.waiting))
        self.assertEqual(set(u_review) & set(u_infra), set())

    def test_unresolvable_slug_degrades_to_unfiltered(self):
        # _apply_role_filter sys.exit(1) on empty slug — footer must NOT crash;
        # U (like I/W) degrades to unfiltered (fail-safe over-count).
        with m.patch.object(cli_concurrency, "resolve_role", return_value="infra"), \
             m.patch.object(airuleset, "_repo_slug", return_value=""):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(wa, self.waiting)   # unfiltered, no SystemExit escaped


# --------------------------------------------------------------------------- #
# cmd_core_quals --waiting — the U N? / --waiting reader reads the filtered set.
# --------------------------------------------------------------------------- #
CORE_ROWS = {
    102: ["infra", "needs-answer"],      # infra   U (the odoo-erp#7720 shape)
    103: ["needs-decision"],             # review  U
    106: ["infra", "needs-owner-action"],  # infra U (owner action, #601)
}


def _core_gh(rows):
    def gh(*a, **k):
        args = [str(x) for x in a]
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        if "-label:stream:" not in search:
            return "[]"
        return json.dumps(
            [{"number": n, "title": "t%d" % n,
              "createdAt": "2026-09-%02dT00:00:00Z" % ((n % 27) + 1),
              "labels": [{"name": x} for x in labels]}
             for n, labels in rows.items()])
    return gh


def _numbers(out):
    nums = set()
    for line in out.splitlines():
        head = line.split("\t", 1)[0].strip()
        if head.isdigit():
            nums.add(int(head))
    return nums


def _drive_core_waiting(role):
    args = dict(count=False, list=False, waiting=True, ops_wait=False,
                audit=False, dep_wait=False, count_dispatchable=False,
                extra=None, role=role)
    buf = io.StringIO()
    empty9 = (set(),) * 9
    with m.patch.object(airuleset, "resolve_authority", return_value="full"), \
         m.patch.object(airuleset, "_repo_slug", return_value=ODOO), \
         m.patch.object(airuleset, "_repo_root", return_value="/root"), \
         m.patch.object(airuleset, "_gh_out", side_effect=_core_gh(CORE_ROWS)), \
         m.patch.object(cli_quals_cmd, "_ops_wait_flag_sets", return_value=empty9), \
         m.patch.object(cli_quals_cmd, "_ops_wait_summary_line", return_value=""), \
         m.patch.object(cli_quals_cmd, "_queued_acceptance_numbers", return_value=set()), \
         m.patch.object(cli_quals_cmd, "_waiting_ping_entries", return_value=[]), \
         m.patch.object(airuleset, "_no_question_flagged", return_value=set()):
        with contextlib.redirect_stdout(buf):
            airuleset.cmd_core_quals(m.Mock(**args))
    return _numbers(buf.getvalue())


class TestCoreQualsWaitingRoleFiltered(TestCase):
    def test_waiting_review_role_shows_only_non_infra_U(self):
        # infra 102/106 DROPPED from the FLOW window; only review 103 remains.
        self.assertEqual(_drive_core_waiting("review"), {103})

    def test_waiting_infra_role_shows_only_infra_U(self):
        self.assertEqual(_drive_core_waiting("infra"), {102, 106})

    def test_waiting_invariant_sum(self):
        self.assertEqual(_drive_core_waiting("review") | _drive_core_waiting("infra"),
                         {102, 103, 106})


# --------------------------------------------------------------------------- #
# slice-quals --waiting — reduced-authority CLI honours --role for U too.
# --------------------------------------------------------------------------- #
SLICE_ROWS = {
    202: {"number": 202, "title": "t202", "createdAt": "2026-09-03T00:00:00Z",
          "labels": [{"name": "infra"}, {"name": "needs-answer"}]},
    203: {"number": 203, "title": "t203", "createdAt": "2026-09-04T00:00:00Z",
          "labels": [{"name": "needs-decision"}]},
}


def _drive_slice_waiting(role):
    args = dict(count=False, list=False, waiting=True, ops_wait=False,
                audit=False, dep_wait=False, count_dispatchable=False,
                bounces=False, extra=None, role=role)
    buf = io.StringIO()
    empty9 = (set(),) * 9
    with m.patch.object(airuleset, "resolve_authority", return_value="branch-merge"), \
         m.patch.object(airuleset, "_current_user", return_value="montalu"), \
         m.patch.object(airuleset, "_slice_quals", return_value=["label:stream:montalu"]), \
         m.patch.object(airuleset, "_repo_slug", return_value=ODOO), \
         m.patch.object(airuleset, "_repo_root", return_value="/root"), \
         m.patch.object(airuleset, "_slice_mine_and_handed",
                        return_value=(SLICE_ROWS, {}, False)), \
         m.patch.object(cli_quals_cmd, "_ops_wait_flag_sets", return_value=empty9), \
         m.patch.object(cli_quals_cmd, "_ops_wait_summary_line", return_value=""), \
         m.patch.object(cli_quals_cmd, "_queued_acceptance_numbers", return_value=set()), \
         m.patch.object(cli_quals_cmd, "_waiting_ping_entries", return_value=[]), \
         m.patch.object(airuleset, "_no_question_flagged", return_value=set()), \
         m.patch.object(airuleset, "_question_map_u_supplement", return_value={}):
        with contextlib.redirect_stdout(buf):
            airuleset.cmd_slice_quals(m.Mock(**args))
    return _numbers(buf.getvalue())


class TestSliceQualsWaitingRoleFiltered(TestCase):
    def test_slice_waiting_review_role(self):
        self.assertEqual(_drive_slice_waiting("review"), {203})

    def test_slice_waiting_infra_role(self):
        self.assertEqual(_drive_slice_waiting("infra"), {202})


# --------------------------------------------------------------------------- #
# statusbar renders `U N` per window from the per-cwd filtered cache.
# --------------------------------------------------------------------------- #
class TestStatusbarPerWindowU(TestCase):
    def test_cache_partitioned_u_read_per_window(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            d = statusbar.cache_dir(home)
            d.mkdir(parents=True, exist_ok=True)
            # FLOW cache: infra 7720 dropped → U 0; INFRA cache: U 1 [7720].
            flow = {"ts": int(time.time()), "root": "/flow",
                    "user_waiting": 0, "user_waiting_numbers": []}
            infra = {"ts": int(time.time()), "root": "/infra",
                     "user_waiting": 1, "user_waiting_numbers": [7720]}
            (d / (statusbar.cwd_key("/flow") + ".json")).write_text(json.dumps(flow))
            (d / (statusbar.cwd_key("/infra") + ".json")).write_text(json.dumps(infra))
            _, uw_flow, _, _, _ = statusbar.obligation_partition("/flow", home=home)
            _, uw_infra, _, _, _ = statusbar.obligation_partition("/infra", home=home)
            self.assertEqual(uw_flow, 0)
            self.assertEqual(uw_infra, 1)
            nums_flow, _ = statusbar.user_waiting_numbers("/flow", home=home)
            nums_infra, _ = statusbar.user_waiting_numbers("/infra", home=home)
            self.assertEqual(nums_flow, set())
            self.assertEqual(nums_infra, {7720})


# --------------------------------------------------------------------------- #
# vocabulary lock — the module sentence documents the #1065 reversal.
# --------------------------------------------------------------------------- #
class TestVocabularyLock(TestCase):
    MODULE = REPO / "modules" / "core" / "statusline-vocabulary.md"

    def setUp(self):
        self.text = self.MODULE.read_text(encoding="utf-8")

    def test_new_sentence_present(self):
        self.assertIn(
            "The `--role` slice narrows `I`, `W` AND `U` — a question shows in "
            "ONE window whose role owns it (`infra` → INFRA), never both "
            "(#1065).", self.text)

    def test_old_sentence_gone(self):
        self.assertNotIn("narrows `I` and `W`, not `U`", self.text)
        self.assertNotIn("never role-dropped (#1025/#1045)", self.text)


if __name__ == "__main__":
    main()
