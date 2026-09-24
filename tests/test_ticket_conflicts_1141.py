"""#1141 slice 4 — contradictory label sets are REPORTED and COUNTED, never
silent.

`cli_ticket_state.CONFLICT_PAIRS` is the declared table of contradictory
label pairs; `conflicts(labels, box, facts)` names each one a row carries,
with the bucket and reason `classify()` gives that row (derived, never a
second precedence). `explain_lines` prints one `conflict: #N <a>+<b> →
<bucket> (<why>)` line per conflict AFTER the row list; its totals line gains
`count=<I+C>` always and `conflicts=<N>` only when N > 0.
`cli_ticket_route.record` writes the footer cache fields `conflicts` +
`conflicts_numbers` (no footer segment). `core-quals --conflicts` /
`slice-quals --conflicts` print the conflict lines only.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_ticket_route  # noqa: E402
import cli_ticket_state as ts  # noqa: E402


def _labels(*names):
    return [{"name": n} for n in names]


def _row(number, *names, title="t"):
    return {"number": number, "title": title, "labels": _labels(*names)}


# One representative label set per declared pair (written from the design
# comment, not from the code): the pair name -> the labels.
_PAIR_CASES = {
    "question+handoff": ("needs-answer", "gk-processing"),
    "ops-wait+handoff": ("ops-wait", "ready-for-review"),
    "verify+handoff": ("verify-on-copy", "needs-gatekeeper"),
    "ops-wait+question": ("ops-wait", "needs-decision"),
    "bounce+handoff": ("prio:bounce", "ready-for-review"),
}


class ConflictTable(unittest.TestCase):
    def test_the_table_declares_exactly_the_five_design_pairs(self):
        self.assertEqual({p.name for p in ts.CONFLICT_PAIRS},
                         set(_PAIR_CASES))

    def test_each_pair_yields_one_conflict_with_the_classify_winner(self):
        for name, combo in _PAIR_CASES.items():
            with self.subTest(pair=name):
                labels = _labels(*combo)
                got = ts.conflicts(labels)
                self.assertEqual([c.name for c in got], [name])
                bucket, why = ts.classify({"labels": labels}, None, ts.Box())
                self.assertEqual((got[0].bucket, got[0].why), (bucket, why))
                self.assertEqual(set(got[0].labels), set(combo))

    def test_the_winner_follows_the_box_and_the_facts(self):
        # a FOREIGN owner question on the full box is HIDDEN (#1141 slice 2):
        # the conflict names the verdict classify() gives, not a fixed one
        foreign = _labels("stream:montalu", "needs-answer", "gk-processing")
        got = ts.conflicts(foreign)
        self.assertEqual(got[0].bucket,
                         ts.classify({"labels": foreign}, None, ts.Box())[0])
        # facts move a hand-off row to P: the conflict follows
        labels = _labels("ops-wait", "ready-for-review")
        got = ts.conflicts(labels, ts.Box(), ts.Facts(pipeline=True))
        self.assertEqual(got[0].bucket, "P")

    def test_an_unsent_acceptance_is_an_owner_question(self):
        got = ts.conflicts(_labels("needs-acceptance", "gk-processing"))
        self.assertEqual([c.name for c in got], ["question+handoff"])
        self.assertEqual(got[0].bucket, "U")

    def test_one_row_can_carry_several_conflicts(self):
        got = ts.conflicts(_labels("ops-wait", "needs-answer",
                                   "ready-for-review"))
        self.assertEqual({c.name for c in got},
                         {"question+handoff", "ops-wait+handoff",
                          "ops-wait+question"})
        self.assertEqual({c.bucket for c in got}, {"U"})

    def test_clean_label_sets_yield_none(self):
        for combo in ((), ("bug",), ("needs-answer",), ("ops-wait",),
                      ("ready-for-review",), ("verify-on-copy",),
                      ("prio:bounce",), ("prio:bounce", "ops-wait"),
                      ("needs-acceptance", "ops-wait"),
                      ("needs-acceptance", "prio:bounce")):
            with self.subTest(combo=combo):
                self.assertEqual(ts.conflicts(_labels(*combo)), [])
        self.assertEqual(ts.conflicts(None), [])
        self.assertEqual(ts.conflicts("junk"), [])

    def test_the_line_shape(self):
        c = ts.conflicts(_labels("needs-answer", "gk-processing"))[0]
        self.assertEqual(c.line(7), "conflict: #7 needs-answer+gk-processing"
                                    " → %s (%s)" % (c.bucket, c.why))


class ExplainConflicts(unittest.TestCase):
    def _explain(self, rows, facts=None, **kw):
        facts = facts or ts.TicketFacts()
        return ts.explain_lines(ts.bucketize(rows, facts, ts.Box()),
                                ts.Box(), facts, **kw)

    def test_conflict_lines_follow_the_row_list(self):
        rows = {5: _row(5, "bug"), 7: _row(7, "needs-answer", "gk-processing"),
                9: _row(9, "prio:bounce", "ready-for-review")}
        out = self._explain(rows)
        last_row = max(i for i, ln in enumerate(out) if ln[0].isdigit())
        conflict = [i for i, ln in enumerate(out)
                    if ln.startswith("conflict: ")]
        self.assertEqual(len(conflict), 2, out)
        self.assertTrue(all(i > last_row for i in conflict), out)
        by_num = {out[i].split()[1]: out[i] for i in conflict}
        self.assertTrue(by_num["#7"].startswith(
            "conflict: #7 needs-answer+gk-processing → U ("), out)
        self.assertTrue(by_num["#9"].startswith(
            "conflict: #9 prio:bounce+ready-for-review → I ("), out)
        self.assertEqual(out[-1],
                         "# explain: I=2 M=0 U=1 W=0 gk=0 count=2 conflicts=2")

    def test_clean_rows_print_no_conflict_and_no_conflicts_field(self):
        out = self._explain({1: _row(1, "bug"), 2: _row(2, "ops-wait")})
        self.assertFalse(any(ln.startswith("conflict") for ln in out), out)
        self.assertEqual(out[-1], "# explain: I=1 M=0 U=0 W=1 gk=0 count=1")

    def test_count_is_i_plus_c_rows_without_the_footer_extras(self):
        rows = {1: _row(1, "bug"), 2: _row(2, "bug"), 3: _row(3, "bug")}
        facts = ts.TicketFacts(pipeline=frozenset({1}),
                               on_main={2: ts.DEPLOYED})
        out = self._explain(rows, facts,
                            extras=[("I", 4, "task-hygiene A", "-")])
        # I=1 row + 4 extras is the display total; count = I rows + C = 2,
        # the `--count` number (cli_ticket_route.count)
        self.assertEqual(out[-1],
                         "# explain: I=5 M=0 U=0 W=0 gk=0 P=1 C=1 count=2")

    def test_a_hidden_row_conflict_is_listed_and_counted(self):
        rows = {8: _row(8, "stream:montalu", "needs-answer", "gk-processing")}
        out = self._explain(rows)
        line = [ln for ln in out if ln.startswith("conflict: #8 ")]
        self.assertEqual(len(line), 1, out)
        bucket = ts.bucketize(rows, ts.TicketFacts(), ts.Box())
        where = [b for b, r in bucket.items() if 8 in r][0]
        self.assertIn("→ %s (" % where, line[0])
        self.assertTrue(out[-1].endswith(" conflicts=1"), out)

    def test_conflict_lines_equal_the_explain_conflict_lines(self):
        rows = {7: _row(7, "needs-answer", "gk-processing"),
                3: _row(3, "ops-wait", "needs-answer", "ready-for-review")}
        facts = ts.TicketFacts()
        buckets = ts.bucketize(rows, facts, ts.Box())
        only = ts.conflict_lines(buckets, ts.Box(), facts)
        out = ts.explain_lines(buckets, ts.Box(), facts)
        self.assertEqual(only, [ln for ln in out
                                if ln.startswith("conflict: ")])
        self.assertEqual(len(only), 4)
        self.assertTrue(out[-1].endswith(" conflicts=4"), out)


class CacheFields(unittest.TestCase):
    def test_record_writes_the_conflict_fields(self):
        rows = {7: _row(7, "needs-answer", "gk-processing"),
                3: _row(3, "ops-wait", "needs-answer", "ready-for-review"),
                1: _row(1, "bug"),
                8: _row(8, "stream:montalu", "needs-answer", "gk-processing")}
        entry = {}
        cli_ticket_route.record(entry, ts.bucketize(rows, ts.TicketFacts(),
                                                    ts.Box()))
        self.assertEqual(entry["conflicts"], 5)
        self.assertEqual(entry["conflicts_numbers"], [3, 7, 8])
        # the M / P / C fields are still written
        self.assertEqual(entry["done"], 0)

    def test_record_writes_zero_on_a_clean_set(self):
        entry = {}
        cli_ticket_route.record(entry, ts.bucketize(
            {1: _row(1, "bug")}, ts.TicketFacts(), ts.Box()))
        self.assertEqual((entry["conflicts"], entry["conflicts_numbers"]),
                         (0, []))


_OBLIG = json.dumps([
    _row(1, "bug", title="plain"),
    _row(2, "needs-answer", "gk-processing", title="q"),
    _row(3, "ops-wait", title="wait"),
    _row(4, "prio:bounce", "ready-for-review", title="bounce"),
])


class ConflictsCli(unittest.TestCase):
    def _run(self, *argv):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            gh = Path(bindir) / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
                '  *"--search label:autopilot-skip"*) echo 0;;\n'
                "  *) echo '%s';;\n" % _OBLIG +
                'esac\n')
            gh.chmod(0o755)
            return subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals"] + list(argv),
                capture_output=True, text=True, cwd=repo,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})

    def test_core_quals_conflicts_prints_only_the_conflict_lines(self):
        r = self._run("--conflicts")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(len(lines), 2, r.stdout)
        self.assertTrue(all(ln.startswith("conflict: #") for ln in lines),
                        r.stdout)
        self.assertTrue(lines[0].startswith(
            "conflict: #2 needs-answer+gk-processing → U ("), r.stdout)
        self.assertTrue(lines[1].startswith(
            "conflict: #4 prio:bounce+ready-for-review → I ("), r.stdout)

    def test_explain_counts_match_the_conflicts_flag(self):
        r = self._run("--explain")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# explain: I=2 M=0 U=1 W=1 gk=0 count=2 conflicts=2",
                      r.stdout.splitlines())
        self.assertEqual(self._run("--count").stdout.strip(), "2")

    def test_conflicts_refuses_extra(self):
        r = self._run("--conflicts", "--extra", "label:prio:bounce")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--conflicts", r.stderr)


class SliceConflictsCli(unittest.TestCase):
    _MARKER = "<!-- airuleset:authority=fork-no-merge -->"

    def test_slice_quals_conflicts(self):
        A = json.dumps([_row(1, "bug"),
                        _row(4, "needs-answer", "ready-for-review")])
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(self._MARKER + "\n")
            gh = Path(bindir) / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
                '  */comments*) echo "[]";;\n'
                '  *assignee:@me*) echo \'%s\';;\n' % A +
                '  *author:@me*)   echo "[]";;\n'
                '  *label:stream:*) echo "[]";;\n'
                '  *) echo "kvaskodev";;\n'
                'esac\n')
            gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "slice-quals", "--conflicts"],
                capture_output=True, text=True, cwd=repo,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 1, r.stdout)
        self.assertTrue(r.stdout.startswith(
            "conflict: #4 needs-answer+ready-for-review → U ("), r.stdout)


if __name__ == "__main__":
    unittest.main()
