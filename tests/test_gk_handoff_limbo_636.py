"""#636 — post-release limbo: a ticket carrying BOTH a gk hand-off label
(`needs-gatekeeper`/`ready-for-review`) AND `ops-wait` is a CONTRADICTION — the
stale `ops-wait` label routes it to the W bucket (`_partition_workable`), which
HIDES the gk hand-off from `gk N` (stream) and the gk box's actionable I. The
gatekeeper is not a third party (parallel to #601's owner ruling), so a ticket
blocked on a gk ACTION belongs in the gk hand-off lane, never W.

This is surfaced by a FALSIFIABLE, LABEL-ONLY `gk-handoff!` flag on
`core-quals`/`slice-quals --ops-wait` (a faithful mirror of the #570 `stale!`
flag — no gh, no timeline, no heuristic) that NAMES the contradictory members so
the running loop drops the `ops-wait` (→ the ticket enters gk N / the gk box's
I). These tests lock:
  1. the pure label-only decider `_gk_handoff_ops_wait_flagged` — the
     contradiction / plain-ops-wait / unreadable-labels branches;
  2. `_print_issue_rows` appends ` gk-handoff!` in the reason field (the same
     mechanism as `stale!`/`no-question!`);
  3. `_watchdog_ops_wait_fetch` parses `gk-handoff!` → `member["gk_handoff"]`;
  4. the job-20 nudge NAMES the gk-handoff members with the doctrine action
     (drop ops-wait — it is a gk hand-off, not a third-party wait).
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_quals
import cli_quals_cmd
import watchdog.ops_wait_recheck as owr


def _row(num, *label_names):
    return {num: {"number": num, "title": "t%d" % num,
                  "createdAt": "2026-01-01T00:00:00Z",
                  "labels": [{"name": n} for n in label_names]}}


class GkHandoffDecider(unittest.TestCase):
    """`_gk_handoff_ops_wait_flagged(rows)` — pure label check, no gh."""

    def test_needs_gatekeeper_plus_ops_wait_is_flagged(self):
        rows = _row(3108, "ops-wait", "needs-gatekeeper", "stream:montalu2")
        self.assertEqual(cli_quals._gk_handoff_ops_wait_flagged(rows), {3108})

    def test_ready_for_review_plus_ops_wait_is_flagged(self):
        rows = _row(4600, "ops-wait", "ready-for-review")
        self.assertEqual(cli_quals._gk_handoff_ops_wait_flagged(rows), {4600})

    def test_plain_ops_wait_is_not_flagged(self):
        rows = _row(50, "ops-wait", "needs-acceptance")
        self.assertEqual(cli_quals._gk_handoff_ops_wait_flagged(rows), set())

    def test_gk_label_without_ops_wait_is_not_flagged(self):
        # standalone-correct: the contradiction needs BOTH labels present.
        rows = _row(51, "needs-gatekeeper")
        self.assertEqual(cli_quals._gk_handoff_ops_wait_flagged(rows), set())

    def test_unreadable_labels_never_flagged(self):
        # fail-safe / never-false-accuse (#539 bias): missing/malformed labels.
        rows = {60: {"number": 60}, 61: {"number": 61, "labels": None},
                62: {"number": 62, "labels": [None, "x"]}}
        self.assertEqual(cli_quals._gk_handoff_ops_wait_flagged(rows), set())

    def test_reexported_from_airuleset(self):
        self.assertIs(airuleset._gk_handoff_ops_wait_flagged,
                      cli_quals._gk_handoff_ops_wait_flagged)


class PrintRowsGkHandoffFlag(unittest.TestCase):
    """`_print_issue_rows(..., gk_handoff_numbers=...)` appends ` gk-handoff!`."""

    def test_flag_appended_to_reason_field(self):
        import io
        import contextlib
        rows = {}
        rows.update(_row(41, "ops-wait", "needs-gatekeeper"))
        rows.update(_row(43, "ops-wait"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(
                rows, own_stream=None, reason_fn=airuleset._ops_wait_reason,
                gk_handoff_numbers={41})
        lines = {ln.split("\t", 1)[0]: ln for ln in buf.getvalue().splitlines()}
        self.assertIn("gk-handoff!", lines["41"].split("\t")[3])
        self.assertNotIn("gk-handoff!", lines["43"].split("\t")[3])

    def test_coexists_with_stale_flag(self):
        import io
        import contextlib
        rows = _row(41, "ops-wait", "needs-gatekeeper")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(
                rows, own_stream=None, reason_fn=airuleset._ops_wait_reason,
                stale_numbers={41}, gk_handoff_numbers={41})
        reason = buf.getvalue().splitlines()[0].split("\t")[3]
        self.assertIn("stale!", reason)
        self.assertIn("gk-handoff!", reason)


class WatchdogFetchParsesGkHandoff(unittest.TestCase):
    """`_watchdog_ops_wait_fetch` returns `[{number, stale, gk_handoff}]`."""

    def test_parses_gk_handoff_marker(self):
        out = ("41\t2026-01-01T00:00:00Z\taction-only\tops-wait gk-handoff!\tt\n"
               "43\t2026-01-01T00:00:00Z\taction-only\tops-wait\tt\n")
        cp = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch("subprocess.run", return_value=cp), \
                mock.patch.object(airuleset, "_repo_root", lambda cwd=None: "/r"), \
                mock.patch.object(airuleset, "resolve_authority",
                                  lambda cwd=None: "full"):
            members = airuleset._watchdog_ops_wait_fetch("/r")
        by_num = {m["number"]: m for m in members}
        self.assertTrue(by_num[41]["gk_handoff"])
        self.assertFalse(by_num[43]["gk_handoff"])


class NudgeNamesGkHandoffMembers(unittest.TestCase):
    """The job-20 nudge NAMES the gk-handoff W members with the doctrine action
    (drop ops-wait — it is a gk hand-off, not a third-party wait, #636)."""

    def test_gk_handoff_members_named_with_action(self):
        members = [{"number": 4600, "stale": False, "gk_handoff": True},
                   {"number": 43, "stale": False, "gk_handoff": False}]
        t = owr._nudge_text(None, members, now=1000.0,
                            w_seen={"4600": 1000.0, "43": 1000.0})
        self.assertIn("#4600", t)
        self.assertIn("gk-handoff", t.lower())
        self.assertIn("needs-gatekeeper", t)   # the doctrine target lane

    def test_no_gk_handoff_clause_when_none_flagged(self):
        members = [{"number": 43, "stale": False, "gk_handoff": False}]
        t = owr._nudge_text(None, members, now=1000.0, w_seen={"43": 1000.0})
        self.assertNotIn("gk-handoff", t.lower())

    def test_legacy_int_members_have_no_gk_handoff_clause(self):
        t = owr._nudge_text(None, [41, 43], now=1000.0,
                            w_seen={"41": 1000.0, "43": 1000.0})
        self.assertNotIn("gk-handoff", t.lower())


if __name__ == "__main__":
    unittest.main()
