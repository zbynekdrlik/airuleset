"""#1141 slice 1 — ONE total classifier `cli_ticket_state.classify(row, facts,
box) -> (bucket, reason)`, `_partition_workable` a thin wrapper over it with
ZERO behaviour change, and an `--explain` surface that prints every counted
ticket with its bucket, its ONE reason and a `conflict:` line for a
contradictory label set.

The parity oracle below is a FROZEN copy of the pre-#1141 `_partition_workable`
and `_split_merged_unreleased` (cli_quals.py @ 7513c2c8). It must never be
"updated to match" the new code — slice 1's whole promise is that no number
moves, and this copy is what proves it over every label combination the
partition reads, both box kinds and own vs foreign stream.
"""

import itertools
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import cli_ticket_state  # noqa: E402


def _labels(*names):
    return [{"name": n} for n in names]


# --- FROZEN pre-#1141 oracle (verbatim logic, cli_quals.py @ 7513c2c8) -------

def _legacy_partition_workable(rows, own_stream=None):
    workable, user_waiting, ops_wait = {}, {}, {}
    for number, row in rows.items():
        labels = row.get("labels") if isinstance(row, dict) else None
        if airuleset._row_is_user_waiting(labels):
            reason = airuleset._user_waiting_reason(labels)
            owner = airuleset._stream_owner_of(labels)
            if reason != "acceptance" and owner and owner != (own_stream or ""):
                workable[number] = row
            elif reason == "acceptance" and airuleset._row_is_ops_wait(labels):
                ops_wait[number] = row
            else:
                user_waiting[number] = row
        elif airuleset._row_is_ops_wait(labels):
            names = {(lb or {}).get("name") for lb in (labels or [])
                     if isinstance(lb, dict)}
            if ("prio:bounce" in names
                    or any(ml in names for ml in
                           (cli_quals.MAINTAINER_ACTION_LABELS
                            + cli_quals.SUBDEV_ACTION_LABELS))):
                workable[number] = row
            else:
                ops_wait[number] = row
        else:
            workable[number] = row
    return workable, user_waiting, ops_wait


def _legacy_split_merged_unreleased(workable, ops_wait, merged_numbers):
    merged_set = {int(n) for n in (merged_numbers or [])}
    if not merged_set:
        return dict(workable), dict(ops_wait), {}
    merged, new_workable, new_ops_wait = {}, {}, {}
    for bucket, keep in ((workable, new_workable), (ops_wait, new_ops_wait)):
        for number, row in bucket.items():
            labels = row.get("labels") if isinstance(row, dict) else None
            names = {(lb or {}).get("name") for lb in (labels or [])
                     if isinstance(lb, dict)}
            if (int(number) in merged_set
                    and not airuleset._row_is_user_waiting(labels)
                    and "prio:bounce" not in names):
                merged[number] = row
            else:
                keep[number] = row
    return new_workable, new_ops_wait, merged


# --- The fixture matrix -------------------------------------------------------

# Every label `_partition_workable` / `_split_merged_unreleased` reads.
_PARTITION_LABELS = (
    "needs-answer", "needs-decision", "needs-acceptance", "needs-owner-action",
    "ops-wait", "prio:bounce", "needs-gatekeeper", "ready-for-review",
    "gk-processing", "verify-on-copy",
)
_OWN = "david1"
# none / own stream / own stream by its legacy alias / foreign stream /
# stream:core (the full box's own marker, never a reduced stream).
_STREAM_VARIANTS = ((), ("stream:david1",), ("stream:david",),
                    ("stream:montalu1",), ("stream:core",))
_BOXES = (None, _OWN)   # full-authority box, reduced-authority slice box


def _matrix_rows():
    rows, n = {}, 0
    for k in range(len(_PARTITION_LABELS) + 1):
        for combo in itertools.combinations(_PARTITION_LABELS, k):
            for stream in _STREAM_VARIANTS:
                n += 1
                rows[n] = {"number": n, "title": "t%d" % n,
                           "labels": _labels("bug", *(combo + stream))}
    # malformed shapes — the unreadable→workable safe side must hold too
    for bad in (None, "garbage", [None, "x", {"name": "ops-wait"}],
                [{"nope": 1}], []):
        n += 1
        rows[n] = {"number": n, "labels": bad}
    n += 1
    rows[n] = "not-a-dict-row"
    return rows


def _bucket_of(number, workable, waiting, ops_wait):
    return ("I" if number in workable else "U" if number in waiting
            else "W" if number in ops_wait else None)


class ClassifierParity(unittest.TestCase):
    """The OLD partition and the NEW classifier agree on EVERY row."""

    @classmethod
    def setUpClass(cls):
        cls.rows = _matrix_rows()

    def test_matrix_is_wide(self):
        # 2^10 label subsets × 5 stream variants + malformed rows.
        self.assertGreater(len(self.rows), 5000)

    def test_classify_matches_the_frozen_partition_on_both_boxes(self):
        for own in _BOXES:
            legacy = _legacy_partition_workable(self.rows, own_stream=own)
            box = cli_ticket_state.Box(own_stream=own)
            diffs = []
            for number, row in self.rows.items():
                want = _bucket_of(number, *legacy)
                got, _reason = cli_ticket_state.classify(row, None, box)
                if got != want:
                    diffs.append((own, number, row, want, got))
            self.assertEqual(diffs[:5], [], "%d parity breaks" % len(diffs))

    def test_partition_workable_is_byte_identical_to_the_frozen_one(self):
        for own in _BOXES:
            self.assertEqual(
                airuleset._partition_workable(self.rows, own_stream=own),
                _legacy_partition_workable(self.rows, own_stream=own))
        # the default (no own_stream) is the full-authority box
        self.assertEqual(airuleset._partition_workable(self.rows),
                         _legacy_partition_workable(self.rows))

    def test_merged_step_matches_the_frozen_split(self):
        rows = {n: r for n, r in self.rows.items() if isinstance(r, dict)}
        merged_all = set(rows)
        for own in _BOXES:
            w, u, o = _legacy_partition_workable(rows, own_stream=own)
            lw, lo, lm = _legacy_split_merged_unreleased(w, o, merged_all)
            self.assertEqual(
                airuleset._split_merged_unreleased(w, o, merged_all),
                (lw, lo, lm))
            box = cli_ticket_state.Box(own_stream=own)
            facts = cli_ticket_state.Facts(merged=True)
            for number, row in rows.items():
                want = ("M" if number in lm else "I" if number in lw
                        else "W" if number in lo else "U")
                got, _ = cli_ticket_state.classify(row, facts, box)
                self.assertEqual(got, want, (own, row))


class ClassifierTotality(unittest.TestCase):
    """Every row gets EXACTLY one bucket and ONE one-line reason."""

    def test_every_row_one_bucket_one_reason(self):
        rows = _matrix_rows()
        for own in _BOXES:
            box = cli_ticket_state.Box(own_stream=own)
            for facts in (None, cli_ticket_state.Facts(),
                          cli_ticket_state.Facts(merged=True),
                          cli_ticket_state.Facts(handed=True),
                          cli_ticket_state.Facts(merged=True, handed=True)):
                for row in rows.values():
                    bucket, reason = cli_ticket_state.classify(row, facts, box)
                    self.assertIn(bucket, cli_ticket_state.BUCKETS)
                    self.assertTrue(reason and reason.strip(), row)
                    self.assertNotIn("\n", reason)

    def test_partition_is_disjoint_and_covers_every_row(self):
        rows = _matrix_rows()
        for own in _BOXES:
            w, u, o = airuleset._partition_workable(rows, own_stream=own)
            self.assertEqual(len(w) + len(u) + len(o), len(rows))
            self.assertEqual(set(w) | set(u) | set(o), set(rows))

    def test_gk_only_for_a_handed_workable_row_on_a_slice_box(self):
        handed = cli_ticket_state.Facts(handed=True)
        slice_box = cli_ticket_state.Box(own_stream=_OWN)
        row = {"labels": _labels("stream:david1", "ready-for-review")}
        self.assertEqual(cli_ticket_state.classify(row, handed, slice_box)[0],
                         "gk")
        # the full box actions a hand-off itself: it stays I there
        self.assertEqual(cli_ticket_state.classify(
            row, handed, cli_ticket_state.Box())[0], "I")
        # a handed row parked on the owner stays U (counted in U, never gk)
        parked = {"labels": _labels("stream:david1", "ready-for-review",
                                    "needs-answer")}
        self.assertEqual(cli_ticket_state.classify(
            parked, handed, slice_box)[0], "U")
        # M wins over gk (the footer splits M out before counting gk)
        both = cli_ticket_state.Facts(merged=True, handed=True)
        self.assertEqual(cli_ticket_state.classify(row, both, slice_box)[0],
                         "M")


class Conflicts(unittest.TestCase):
    """A contradictory label set is REPORTED, never re-classified."""

    def test_the_four_observed_contradictions_are_named(self):
        cases = {
            ("needs-answer", "gk-processing"): "needs-answer",
            ("ops-wait", "ready-for-review"): "ops-wait",
            ("verify-on-copy", "needs-gatekeeper"): "verify-on-copy",
            ("ops-wait", "needs-answer"): "ops-wait",
        }
        for combo, needle in cases.items():
            lines = cli_ticket_state.conflicts(_labels(*combo))
            self.assertTrue(lines, combo)
            self.assertTrue(any(needle in ln and combo[1] in ln
                                for ln in lines), (combo, lines))

    def test_consistent_label_sets_report_nothing(self):
        for combo in ((), ("needs-answer",), ("ops-wait",),
                      ("ready-for-review",), ("prio:bounce", "ops-wait"),
                      ("needs-acceptance", "ops-wait"),
                      ("needs-acceptance", "ready-for-review")):
            self.assertEqual(cli_ticket_state.conflicts(_labels(*combo)), [],
                             combo)

    def test_conflict_does_not_change_the_bucket(self):
        row = {"labels": _labels("needs-answer", "gk-processing")}
        self.assertEqual(
            cli_ticket_state.classify(row, None, cli_ticket_state.Box())[0],
            _bucket_of(1, *_legacy_partition_workable({1: row})))


class ExplainOutput(unittest.TestCase):
    """`explain_lines` prints bucket + reason per row, a `conflict:` line under
    a contradictory row, and a totals line equal to the bucket sizes."""

    def test_rows_reasons_conflict_and_totals(self):
        rows = {
            5: {"number": 5, "title": "plain bug", "labels": _labels("bug")},
            7: {"number": 7, "title": "asks owner",
                "labels": _labels("needs-answer", "gk-processing")},
            9: {"number": 9, "title": "parked", "labels": _labels("ops-wait")},
        }
        w, u, o = airuleset._partition_workable(rows)
        out = cli_ticket_state.explain_lines(
            {"I": w, "M": {}, "U": u, "W": o, "gk": {}},
            cli_ticket_state.Box())
        text = "\n".join(out)
        row7 = [ln for ln in out if ln.startswith("7\t")]
        self.assertEqual(len(row7), 1, text)
        self.assertEqual(row7[0].split("\t")[1], "U")
        self.assertIn("needs-answer", row7[0].split("\t")[2])
        after7 = out[out.index(row7[0]) + 1]
        self.assertTrue(after7.startswith("  conflict: "), text)
        self.assertIn("gk-processing", after7)
        self.assertIn("\tI\t", [ln for ln in out if ln.startswith("5\t")][0])
        self.assertIn("\tW\t", [ln for ln in out if ln.startswith("9\t")][0])
        self.assertEqual(out[-1], "# explain: I=1 M=0 U=1 W=1 gk=0")
        self.assertNotIn("mismatch", text)


# --- End-to-end: the real CLI surfaces ---------------------------------------

_OBLIG = json.dumps([
    {"number": 1, "title": "plain", "labels": _labels("bug")},
    {"number": 2, "title": "q", "labels": _labels("needs-answer",
                                                   "gk-processing")},
    {"number": 3, "title": "wait", "labels": _labels("ops-wait")},
])


class ExplainCli(unittest.TestCase):
    def _fake_gh(self, bindir):
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
            '  *"--search label:autopilot-skip"*) echo 0;;\n'
            "  *) echo '%s';;\n" % _OBLIG +
            'esac\n')
        gh.chmod(0o755)

    def _run(self, argv):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            return subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py")]
                + argv + (["--cwd", repo] if argv[0] == "tickets-status"
                          else []),
                capture_output=True, text=True, cwd=repo,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})

    def _assert_explained(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        by_num = {ln.split("\t")[0]: ln.split("\t")[1] for ln in lines
                  if ln and ln[0].isdigit()}
        self.assertEqual(by_num, {"1": "I", "2": "U", "3": "W"}, r.stdout)
        self.assertTrue(any(ln.startswith("  conflict: ") for ln in lines),
                        r.stdout)
        self.assertIn("# explain: I=1 M=0 U=1 W=1 gk=0", lines)

    def test_core_quals_explain(self):
        self._assert_explained(self._run(["core-quals", "--explain"]))

    def test_core_quals_count_matches_explain(self):
        r = self._run(["core-quals", "--count"])
        self.assertEqual(r.stdout.strip(), "1", r.stderr)

    def test_tickets_status_explain(self):
        self._assert_explained(self._run(["tickets-status", "--explain"]))


class ExplainSliceCli(unittest.TestCase):
    """The reduced-authority mirror: `slice-quals --explain` shows the sub-dev
    `gk` bucket (a handed-off ticket) next to I and U, and its I total equals
    `slice-quals --count`."""

    _MARKER = "<!-- airuleset:authority=fork-no-merge -->"

    def _fake_gh(self, bindir):
        gh = Path(bindir) / "gh"
        A = json.dumps([
            {"number": 1, "title": "own bug", "labels": _labels("bug")},
            {"number": 4, "title": "q", "labels": _labels("needs-answer")},
            {"number": 6, "title": "handed",
             "labels": _labels("ready-for-review")}])
        B = json.dumps([{"number": 2, "title": "d",
                         "labels": _labels("needs-decision")}])
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
            '  */comments*) echo "[]";;\n'
            '  *assignee:@me*) echo \'%s\';;\n' % A +
            '  *author:@me*)   echo \'%s\';;\n' % B +
            '  *label:stream:*) echo "[]";;\n'
            '  *) echo "kvaskodev";;\n'
            'esac\n')
        gh.chmod(0o755)

    def _run(self, *argv):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(self._MARKER + "\n")
            self._fake_gh(bindir)
            return subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "slice-quals"] + list(argv),
                capture_output=True, text=True, cwd=repo,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})

    def test_slice_quals_explain(self):
        r = self._run("--explain")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        by_num = {ln.split("\t")[0]: ln.split("\t")[1] for ln in lines
                  if ln and ln[0].isdigit()}
        self.assertEqual(by_num, {"1": "I", "2": "U", "4": "U", "6": "gk"},
                         r.stdout)
        self.assertIn("# explain: I=1 M=0 U=2 W=0 gk=1", lines)
        self.assertEqual(self._run("--count").stdout.strip(), "1")

    def test_explain_refuses_extra(self):
        r = self._run("--explain", "--extra", "label:prio:bounce")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--explain", r.stderr)


if __name__ == "__main__":
    unittest.main()
