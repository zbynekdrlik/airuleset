"""#654 — a FOREIGN `stream:<user>` needs-answer/needs-decision/needs-owner-action
row must NEVER enter a full-authority box's `U` bucket, even when it also carries
an active gk queue label (`needs-gatekeeper`/`ready-for-review`).

ROZHODNUTÉ (supervisor on #654, from the owner's own 3× corrections 2026-08-23/24
— "gk nedoručuje streamove otázky"): STREAM OWNERSHIP ALWAYS WINS for U routing.
A `stream:<user>` row routes to the full-authority box's workable `I` as
`action-only` (the safe over-count direction per #589/#636 — the gatekeeper can
still act its review lane on it); the owner-facing question is delivered by the
owning stream's OWN box (its own `U`), never gk's.

Live symptom: gk (odoo-erp) footer `U 1` counted odoo-erp 4607, a
`stream:david` + `needs-gatekeeper` + `needs-decision` ticket pulled into the gk
obligation set via `_obligation_quals()`'s `label:needs-gatekeeper` UNION arm,
then routed to `user_waiting` by the label-only `_partition_workable`.

These tests lock:
  1. the ownership-aware `_partition_workable(rows, own_stream=...)` routing
     (foreign stream → workable; stream:core / own stream → U unchanged);
  2. the workable foreign row renders `action-only` (display consistency);
  3. `core-quals --count`/`--waiting`/`--list` end-to-end with the EXACT 4607
     label set;
  4. the reduced-authority slice mirror stays untouched — a stream box counting
     its OWN needs-decision into its OWN U still works.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


def _labels(*names):
    return [{"name": n} for n in names]


# The EXACT live label set from odoo-erp 4607 (the ticket's own evidence).
_FOUR607 = _labels("bug", "tenant:slovnormal", "stream:david",
                   "needs-gatekeeper", "needs-decision")


class PartitionOwnershipCarveout(unittest.TestCase):
    """Pure `_partition_workable` routing — no gh. `own_stream=None` is the
    full-authority box (owns no stream); `own_stream=<canonical user>` is a
    reduced-authority slice box."""

    def test_foreign_stream_userwaiting_row_routes_to_workable_on_full_box(self):
        # The 4607 case: stream:david + needs-gatekeeper + needs-decision on the
        # gk box (own_stream=None) → workable I (action-only), NEVER gk U.
        rows = {4607: {"number": 4607, "labels": _FOUR607}}
        workable, waiting, ops_wait = airuleset._partition_workable(
            rows, own_stream=None)
        self.assertIn(4607, workable,
                      "foreign stream:david row must be workable I, not gk U")
        self.assertNotIn(4607, waiting,
                         "foreign stream:david row must NOT be in gk U")
        self.assertNotIn(4607, ops_wait)

    def test_default_own_stream_is_full_box_semantics(self):
        # No own_stream arg → default None → full-authority box: a foreign row
        # is workable, never U. Locks the default so a bare footer/core call
        # (which passes no own_stream) gets the carve-out.
        rows = {4607: {"number": 4607, "labels": _FOUR607}}
        workable, waiting, _ = airuleset._partition_workable(rows)
        self.assertIn(4607, workable)
        self.assertNotIn(4607, waiting)

    def test_stream_core_and_bare_userwaiting_rows_stay_in_U_on_full_box(self):
        # stream:core is the full-authority box's OWN work marker (#578, NOT a
        # reduced-authority stream), and a bare (no stream) needs-decision is
        # gk's own question to the owner — both STAY in gk U. The carve-out is
        # scoped to FOREIGN reduced-authority streams only.
        rows = {
            10: {"number": 10, "labels": _labels("stream:core", "needs-decision")},
            11: {"number": 11, "labels": _labels("needs-answer")},
        }
        workable, waiting, _ = airuleset._partition_workable(rows, own_stream=None)
        self.assertEqual(set(waiting), {10, 11},
                         "stream:core + bare user-waiting rows stay in gk U")
        self.assertEqual(set(workable), set())

    def test_own_stream_userwaiting_row_stays_in_U_on_slice_box(self):
        # A reduced-authority slice box (own_stream = its canonical user) counts
        # its OWN stream:<me> needs-decision into its OWN U — unchanged. Both the
        # legacy alias label (stream:david) and the canonical (stream:david1)
        # resolve to own_stream=david1 → owner == own_stream → NOT foreign → U.
        for lbl in ("stream:david", "stream:david1"):
            rows = {7: {"number": 7, "labels": _labels(lbl, "needs-decision")}}
            workable, waiting, _ = airuleset._partition_workable(
                rows, own_stream="david1")
            self.assertIn(7, waiting,
                          "%s: a slice box's OWN needs-decision stays in its U" % lbl)
            self.assertNotIn(7, workable, lbl)

    def test_foreign_row_routes_to_workable_on_another_slice_box(self):
        # A row owned by ANOTHER stream, seen by a different slice box
        # (own_stream=montalu1), is foreign → workable action-only, never
        # montalu's U — montalu cannot field david's owner-question.
        rows = {7: {"number": 7, "labels": _labels("stream:david", "needs-decision")}}
        workable, waiting, _ = airuleset._partition_workable(
            rows, own_stream="montalu1")
        self.assertIn(7, workable)
        self.assertNotIn(7, waiting)

    def test_workable_foreign_row_renders_action_only(self):
        # The foreign row landing in workable must render `action-only` in the
        # --list/--audit column, so the gatekeeper never writes its code.
        import cli_quals_cmd
        self.assertEqual(
            cli_quals_cmd._row_action({"number": 4607, "labels": _FOUR607},
                                      own_stream=None),
            airuleset.ROW_ACTION_ONLY)

    def test_carveout_does_not_disturb_ops_wait_or_plain_rows(self):
        # Scope guard: the carve-out fires ONLY inside the user-waiting branch.
        # A foreign stream row that is ops-wait (NOT user-waiting) still goes to
        # W; a plain foreign stream row (no queue label) is workable already;
        # an own-stream acceptance+ops-wait still routes to W on its slice box.
        rows = {
            20: {"number": 20, "labels": _labels("stream:david", "ops-wait")},
            21: {"number": 21, "labels": _labels("stream:david", "bug")},
        }
        workable, waiting, ops_wait = airuleset._partition_workable(
            rows, own_stream=None)
        self.assertEqual(set(ops_wait), {20}, "foreign ops-wait row stays in W")
        self.assertEqual(set(workable), {21})
        self.assertEqual(set(waiting), set())
        # own-stream acceptance thread sent (needs-acceptance + ops-wait) → W,
        # on its own slice box (own_stream=david1) — acceptance→W path intact.
        rows2 = {22: {"number": 22,
                      "labels": _labels("stream:david", "needs-acceptance", "ops-wait")}}
        _, waiting2, ops2 = airuleset._partition_workable(rows2, own_stream="david1")
        self.assertEqual(set(ops2), {22})
        self.assertEqual(set(waiting2), set())


# --- End-to-end core-quals harness (mirrors test_user_waiting_split.py) -------

def _run_quals(subcmd, flag, repo, home, bindir, marker=None):
    if marker is not None:
        Path(repo, "CLAUDE.md").write_text(marker + "\n")
    return subprocess.run(
        [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"), subcmd, flag],
        capture_output=True, text=True, cwd=repo,
        env={**os.environ, "HOME": home, "PATH": f"{bindir}:{os.environ['PATH']}"})


# Obligation rows: #4607 foreign (stream:david + needs-gatekeeper + needs-decision)
# → workable I as action-only; #1 plain workable; #2 stream:core+decision and #3
# bare needs-answer → gk's own U.
_OBLIG = json.dumps([
    {"number": 4607, "labels": _labels("bug", "tenant:slovnormal", "stream:david",
                                        "needs-gatekeeper", "needs-decision")},
    {"number": 1, "labels": _labels("bug")},
    {"number": 2, "labels": _labels("stream:core", "needs-decision")},
    {"number": 3, "labels": _labels("needs-answer")},
])


class CoreQualsExcludesForeignStreamFromU(unittest.TestCase):
    """Full-authority /goal stop-proof: a foreign stream:<user> user-waiting row
    counts as WORKABLE (I, action-only), never `U` — while gk's OWN
    stream:core / bare user-waiting rows still surface as `U`."""

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

    def test_count_counts_foreign_stream_row_as_workable(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("core-quals", "--count", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "2",
                             "workable I = {4607 (foreign, action-only), 1}; "
                             "#2 stream:core + #3 needs-answer are gk's own U")

    def test_waiting_lists_only_gk_own_U_not_the_foreign_row(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines() if ln.strip()}
            self.assertEqual(nums, {"2", "3"},
                             "--waiting lists ONLY gk's own U (stream:core + bare), "
                             "NEVER the foreign stream:david row 4607")

    def test_list_includes_foreign_row_as_action_only(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("core-quals", "--list", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            by_num = {}
            for ln in r.stdout.splitlines():
                if not ln.strip():
                    continue
                parts = ln.split("\t")
                by_num[parts[0]] = parts[2]   # field 2 = action column
            self.assertEqual(set(by_num), {"4607", "1"},
                             "--list is workable-only, so len(--list) == --count")
            self.assertEqual(by_num["4607"], airuleset.ROW_ACTION_ONLY,
                             "foreign stream:david row must render action-only")
            self.assertEqual(by_num["1"], airuleset.ROW_IMPLEMENT)


if __name__ == "__main__":
    unittest.main()
