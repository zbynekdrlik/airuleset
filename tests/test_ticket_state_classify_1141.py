"""#1141 slice 1 — ONE total classifier `cli_ticket_state.classify(row, facts,
box) -> (bucket, reason)`, `_partition_workable` a thin wrapper over it with
ZERO behaviour change, and an `--explain` surface that prints every counted
ticket with its bucket, its ONE reason and a `conflict:` line for a
contradictory label set.

The parity oracle below is a FROZEN copy of the pre-#1141 `_partition_workable`
and `_split_merged_unreleased` (cli_quals.py @ origin/main 7575f56f,
byte-identical at 7513c2c8). It must never be
"updated to match" the new code — slice 1's whole promise is that no number
moves, and this copy is what proves it over every label combination the
partition reads, both box kinds and own vs foreign stream.

#1141 slice 2 moved EXACTLY two precedences, by the owner's ruling in the
#1141 design comment (live cases odoo-erp 8058 and 8180):
  "An owner question beats any hand-off label" and "On the full-authority box
  a FOREIGN stream's question is hidden (it counts in that stream's U), which
  reverses #654 for questions."
The frozen copy stays verbatim. `_slice2_move` lists the moved cases on top
of it, written from the ruling (not from the new code), so every OTHER label
combination is still held at slice-1 parity.
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


# --- FROZEN pre-#1141 oracle (verbatim logic, cli_quals.py @ 7575f56f) ---

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


_HANDOFF = ("needs-gatekeeper", "ready-for-review", "gk-processing")


def _slice2_move(row, own_stream):
    """The slice-2 verdict for a row the ruling MOVES, or None (slice-1 parity).

    - Rule 1: an UNSENT needs-acceptance (no ops-wait, no prio:bounce) that a
      hand-off label used to override is now an owner question.
    - Rule 2: on the full-authority box (own_stream None) a FOREIGN stream's
      owner question (answer/decision/action, or an unsent acceptance) is
      hidden. A sent acceptance (ops-wait) is not a question.
    - On a reduced-authority box a foreign row keeps its slice-1 route; an own
      (or unowned) rule-1 row goes to U."""
    labels = row.get("labels") if isinstance(row, dict) else None
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    old_uw = airuleset._row_is_user_waiting(labels)
    sent = (old_uw and airuleset._user_waiting_reason(labels) == "acceptance"
            and airuleset._row_is_ops_wait(labels))
    unsent_over_handoff = (
        not old_uw and "needs-acceptance" in names
        and any(h in names for h in _HANDOFF)
        and "prio:bounce" not in names and "ops-wait" not in names)
    question = (old_uw and not sent) or unsent_over_handoff
    owner = airuleset._stream_owner_of(labels)
    foreign = bool(owner) and owner != (own_stream or "")
    if own_stream is None and foreign and question:
        return cli_ticket_state.HIDDEN
    if unsent_over_handoff and not foreign:
        return "U"
    return None


def _expected_partition(rows, own_stream=None):
    """The frozen partition with the slice-2 moves applied (hidden = dropped)."""
    w, u, o = _legacy_partition_workable(rows, own_stream=own_stream)
    for number, row in rows.items():
        move = _slice2_move(row, own_stream)
        if move is None:
            continue
        for bucket in (w, u, o):
            bucket.pop(number, None)
        if move == "U":
            u[number] = row
    return w, u, o


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
            else "W" if number in ops_wait else cli_ticket_state.HIDDEN)


class ClassifierParity(unittest.TestCase):
    """The OLD partition (plus the listed slice-2 moves) and the NEW
    classifier agree on EVERY row."""

    @classmethod
    def setUpClass(cls):
        cls.rows = _matrix_rows()

    def test_matrix_is_wide(self):
        # 2^10 label subsets × 5 stream variants + malformed rows.
        self.assertGreater(len(self.rows), 5000)

    def test_slice2_moves_only_the_ruled_cases(self):
        # every moved row is one of the two ruled shapes, both kinds occur,
        # and the rest of the matrix is untouched (the parity tests below)
        moved = {own: [n for n, r in self.rows.items()
                       if _slice2_move(r, own) is not None]
                 for own in _BOXES}
        self.assertTrue(moved[None] and moved[_OWN])
        hidden = [n for n in moved[None]
                  if _slice2_move(self.rows[n], None)
                  == cli_ticket_state.HIDDEN]
        self.assertTrue(hidden)
        # the slice box never hides anything
        self.assertTrue(all(_slice2_move(self.rows[n], _OWN) == "U"
                            for n in moved[_OWN]))

    def test_classify_matches_the_frozen_partition_on_both_boxes(self):
        for own in _BOXES:
            legacy = _expected_partition(self.rows, own_stream=own)
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
                _expected_partition(self.rows, own_stream=own))
        # the default (no own_stream) is the full-authority box
        self.assertEqual(airuleset._partition_workable(self.rows),
                         _expected_partition(self.rows))

    def test_merged_step_matches_the_frozen_split(self):
        rows = {n: r for n, r in self.rows.items() if isinstance(r, dict)}
        merged_all = set(rows)
        for own in _BOXES:
            w, u, o = _expected_partition(rows, own_stream=own)
            lw, lo, lm = _legacy_split_merged_unreleased(w, o, merged_all)
            self.assertEqual(
                airuleset._split_merged_unreleased(w, o, merged_all),
                (lw, lo, lm))
            box = cli_ticket_state.Box(own_stream=own)
            facts = cli_ticket_state.Facts(merged=True)
            for number, row in rows.items():
                want = ("M" if number in lm else "I" if number in lw
                        else "W" if number in lo else "U" if number in u
                        else cli_ticket_state.HIDDEN)
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
                    self.assertIn(bucket, cli_ticket_state.BUCKETS
                                  + (cli_ticket_state.HIDDEN,))
                    self.assertTrue(reason and reason.strip(), row)
                    self.assertNotIn("\n", reason)

    def test_partition_is_disjoint_and_covers_every_row(self):
        # every row is in exactly one of I/U/W, or HIDDEN (slice 2: counted
        # on its owning stream's box) — never lost, never double-counted
        rows = _matrix_rows()
        for own in _BOXES:
            box = cli_ticket_state.Box(own_stream=own)
            hidden = {n for n, r in rows.items()
                      if cli_ticket_state.classify(r, None, box)[0]
                      == cli_ticket_state.HIDDEN}
            w, u, o = airuleset._partition_workable(rows, own_stream=own)
            self.assertEqual(len(w) + len(u) + len(o) + len(hidden),
                             len(rows))
            self.assertEqual(set(w) | set(u) | set(o) | hidden, set(rows))

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
        r = self._run(["tickets-status", "--explain"])
        self._assert_explained(r)
        # the footer's own cached numbers are shown next to the totals
        self.assertIn("# footer cache: none for this cwd", r.stdout)


class ExplainLinesReviewFixes(unittest.TestCase):
    """Review round 1: M rows, the mismatch canary, footer extras, TSV-safe
    titles, and the role filter applied to gk/M on the slice path."""

    def test_merged_row_is_M_with_its_reason(self):
        rows = {4: {"number": 4, "title": "m", "labels": _labels("bug")}}
        w, _u, o = airuleset._partition_workable(rows)
        w, o, m = airuleset._split_merged_unreleased(w, o, {4})
        out = cli_ticket_state.explain_lines(
            {"I": w, "M": m, "U": {}, "W": o, "gk": {}},
            cli_ticket_state.Box(), merged={4})
        self.assertTrue(out[0].startswith("4\tM\tfix merged"), out)
        self.assertEqual(out[-1], "# explain: I=0 M=1 U=0 W=0 gk=0")

    def test_counted_elsewhere_prints_a_mismatch_line(self):
        row = {"number": 8, "labels": _labels("needs-answer")}
        out = cli_ticket_state.explain_lines(
            {"I": {8: row}}, cli_ticket_state.Box())
        self.assertTrue(out[1].startswith("  mismatch: classify() says U"),
                        out)

    def test_extras_add_to_the_totals_and_titles_stay_one_cell(self):
        rows = {3: {"number": 3, "title": "a\tb\nc", "labels": []}}
        out = cli_ticket_state.explain_lines(
            {"I": rows}, cli_ticket_state.Box(),
            extras=[("U", 1, "ticketless question ping", "q?"),
                    ("I", 2, "task-hygiene A", "-")])
        self.assertEqual(out[0].split("\t")[3], "a b c")
        self.assertEqual(len(out[0].split("\t")), 4)
        self.assertIn("-\tU\tticketless question ping\tq?", out)
        self.assertEqual(out[-1], "# explain: I=3 M=0 U=1 W=0 gk=0")

    def test_slice_explain_role_filters_gk_and_M_like_the_footer(self):
        from unittest import mock
        import cli_quals_cmd
        import cli_ticket_explain
        kept = {1: {"number": 1, "labels": _labels("ready-for-review")}}
        other = {2: {"number": 2, "labels": _labels("infra",
                                                   "ready-for-review")}}
        merged = {5: {"number": 5, "labels": _labels("infra")}}
        seen = []

        def fake_filter(rows, root, role, slug=None):
            seen.append(set(rows))
            return {n: r for n, r in rows.items()
                    if "infra" not in cli_ticket_state._names(r["labels"])}

        with mock.patch.object(cli_quals_cmd, "_apply_role_filter",
                               fake_filter), \
                mock.patch.object(cli_quals, "_question_map_u_supplement",
                                  return_value={}), \
                mock.patch.object(cli_ticket_explain, "_footer_extras",
                                  return_value=[]), \
                mock.patch("builtins.print") as fake_print:
            cli_ticket_explain.explain_slice(
                None, "/nonexistent", _OWN, "review", "o/r",
                rows={**kept, **other}, handed={1: True, 2: True},
                workable={**kept, **other}, unhandled={}, waiting={},
                ops_wait={}, merged_rows=merged, merged_set={5})
        printed = [c.args[0] for c in fake_print.call_args_list]
        self.assertIn("# explain: I=0 M=0 U=0 W=0 gk=1", printed)
        self.assertIn({1, 2}, seen)
        self.assertIn({5}, seen)


class ExplainReviewRound2(unittest.TestCase):
    """Review round 2: released gk reason, the footer extras keyed on the RAW
    session cwd, and a junk cache timestamp."""

    def test_released_row_is_gk_with_its_own_reason(self):
        row = {"labels": _labels("stream:david1")}
        got = cli_ticket_state.classify(
            row, cli_ticket_state.Facts(handed="released"),
            cli_ticket_state.Box(own_stream=_OWN))
        self.assertEqual(got[0], "gk")
        self.assertIn("released", got[1])

    def test_footer_extras_key_on_the_raw_session_cwd(self):
        from unittest import mock
        import cli_quals_cmd
        import cli_ticket_explain
        import statusbar
        asked = []

        def fake_pings(cwd=None):
            asked.append(cwd)
            return [{"question": "which\tone?"}]

        def fake_core(ns):
            cli_ticket_explain._emit({"I": {}, "U": {}}, cli_ticket_state.Box(),
                                     ())

        with TemporaryDirectory() as repo, \
                mock.patch.object(cli_quals_cmd, "_waiting_ping_entries",
                                  fake_pings), \
                mock.patch.object(statusbar, "task_hygiene_a_count",
                                  return_value=2), \
                mock.patch.object(cli_quals_cmd, "cmd_core_quals", fake_core), \
                mock.patch.object(airuleset, "_repo_root", return_value=repo), \
                mock.patch.object(airuleset, "resolve_authority",
                                  return_value="full"), \
                mock.patch("builtins.print") as fake_print:
            raw = repo + "/"          # a non-canonical spelling of the cwd
            cli_ticket_explain.explain_footer(raw)
        printed = [c.args[0] for c in fake_print.call_args_list]
        self.assertEqual(asked, [raw])
        self.assertIsNone(cli_ticket_explain._footer_cwd)
        self.assertIn("-\tU\tticketless question ping, no ticket to label "
                      "(#512)\twhich one?", printed)
        self.assertIn("# explain: I=2 M=0 U=1 W=0 gk=0", printed)

    def test_junk_cache_timestamp_never_crashes(self):
        from unittest import mock
        import cli_ticket_explain
        import statusbar
        with mock.patch.object(statusbar, "_load",
                               return_value={"open": 1, "ts": "abc"}):
            line = cli_ticket_explain._footer_cache_line("/x")
        self.assertTrue(line.startswith("# footer cache (age ?): open=1"),
                        line)


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
