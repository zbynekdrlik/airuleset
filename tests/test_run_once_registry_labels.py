"""#433 item G step 16 — the run_once standalone-job REGISTRY structure lock.

Step 16 re-expressed run_once's post-loop standalone if-chain as an
ORDER-PRESERVING `(label, gate, invoke, err)` registry executed by a single
loop. `tests/test_run_once_characterization.py` pins the runtime BEHAVIOUR
(order / gates / isolation / dry-run / accumulation) via recording stubs; this
file is the complementary STRUCTURE lock the design asked for — "a light test
asserting every docstring job number 8-29 appears as a registry label" —
plus two guards that keep the registry honest against silent drift:

  * the registry's standalone labels are EXACTLY the CANONICAL_SWEEP standalone
    set, in order (no job dropped / added / reordered);
  * the one non-job `_owner_kill_switch_notice` entry stays positioned exactly
    where the old if-chain emitted its DISABLED lines (log order byte-identical);
  * every LIVE docstring job number 8-29 (minus the pane-loop wedge, job 10)
    maps to a registry label present in run_once — driven by the docstring
    itself, so adding a new live job 8-29 fails this test until it is also
    registered.

Uses `unittest.TestCase` (not bare `def test_*`) so the
`python -m unittest discover -s tests` push gate runs every case.
"""

import inspect
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd            # noqa: E402


def _registry_labels():
    """The `_add("<label>", ...)` labels in run_once, in source (== execution)
    order. Every `_add(` call opens with its label string on the same line."""
    src = inspect.getsource(wd.run_once)
    return re.findall(r'_add\(\s*"([^"]+)"', src)


# The 22 standalone entries in CANONICAL_SWEEP order (the step-15 contract, i.e.
# everything the sweep runs AFTER the pane loop's `list_claude_panes` entry).
# Jobs 3/5/7 + their #368 extension come first, then jobs 8->30 in
# literal call order. (The #461 owner-decision digest entry was RETIRED by
# #707 — the whole message class is abolished, its producer is a no-op
# tombstone and the wiring is gone.)
EXPECTED_STANDALONE = [
    "check_usage",                      # (3)
    "deliver_pending_done",             # (5)
    "deliver_discord_replies",          # (7)
    "prune_answered_questions",         # (7, terminal-answer prune)
    "reping_stale_questions",           # (7 / #368)
    "bounce_backstop",                  # (8)
    "gk_request_backstop",              # (11)
    "burn_snapshot_job",                # (13)
    "compact_sweep",                    # (14)
    "fleet_burn_job",                   # (16)
    "burn_alert_job",                   # (19)
    "goal_sweep",                       # (9)
    "goal_dark_watch",                  # (20)
    "goal_question_repoke_watch",       # (33) — #522 stuck-❓ disarm
    "goal_lane_sweep",                  # (20)
    "long_turn_watch",                  # (21)
    "delivery_stall_watch",             # (24)
    "card_reconcile",                   # (25)
    "net_drift_alarm",                  # (27)
    "stuck_main_sweep",                 # (28)
    "cleanup_stale_exec_markers",       # (22)
    "vault_purge_job",                  # (29)
    "wip_ref_sweep",                    # (30) — #504 orphaned wip-ref reclaimer
    "gk_selfservice_bounce",            # (31) — #516 gk self-service auto-bounce
    "reconcile_u_labels",               # (32) — #515 mechanical U-label lifecycle
    "conformance_check",                # (34) — #535 per-box conformance check
    "conformance_heartbeat_check",      # (35) — #543 central dead-box detector
    "gk_orphan_marker_sweep",           # (36) — #551 orphaned gk hand-off marker
    "shadow_ugrep_reaper",              # (37) — #776 runaway shadow-ugrep reaper
    "heavy_build_reaper",               # (38) — #778 heavy-build-toolchain reaper
]

# The one non-job registry entry: emits the owner kill-switch DISABLED lines at
# the exact position the old if-chain did (after job 11, before job 13).
NOTICE = "_owner_kill_switch_notice"

# Every LIVE docstring job number 8-29 -> the registry label(s) implementing it.
# Job 10 (QUEUED-PROMPT-WEDGE / prompt_wedge_check) runs in the per-transcript
# PANE LOOP, NOT the standalone registry, so it is deliberately excluded. Job 20
# has two halves. This map is the docstring<->registry tie; the test below drives
# it FROM the docstring so it cannot silently omit a newly-added live job.
LIVE_STANDALONE_JOB_LABELS = {
    8: ["bounce_backstop"],
    9: ["goal_sweep"],
    11: ["gk_request_backstop"],
    13: ["burn_snapshot_job"],
    14: ["compact_sweep"],
    16: ["fleet_burn_job"],
    19: ["burn_alert_job"],
    20: ["goal_dark_watch", "goal_lane_sweep"],
    21: ["long_turn_watch"],
    22: ["cleanup_stale_exec_markers"],
    24: ["delivery_stall_watch"],
    25: ["card_reconcile"],
    27: ["net_drift_alarm"],
    28: ["stuck_main_sweep"],
    29: ["vault_purge_job"],
}
PANE_LOOP_JOBS_8_TO_29 = {10}   # documented non-standalone job in that range


def _live_docstring_jobs_8_to_29():
    """Job numbers 8-29 whose run_once-docstring paragraph is LIVE (not REMOVED)."""
    doc = wd.run_once.__doc__
    live = set()
    for n in range(8, 30):
        m = re.search(r"\(%d\)(.*?)(?=\(\d+[a-z]?\)|Returns a list)" % n, doc, re.S)
        if not m:
            continue
        if "REMOVED" not in " ".join(m.group(1).split()):
            live.add(n)
    return live


class TestRegistryStandaloneCompleteness(unittest.TestCase):
    def test_registry_labels_match_canonical_order_minus_the_notice(self):
        labels = _registry_labels()
        jobs = [x for x in labels if x != NOTICE]
        self.assertEqual(
            jobs, EXPECTED_STANDALONE,
            "the registry's standalone labels (order-preserving) must equal the "
            "CANONICAL_SWEEP standalone set — a mismatch means a job was dropped, "
            "added, or reordered")

    def test_every_label_appears_exactly_once(self):
        labels = _registry_labels()
        self.assertEqual(len(labels), len(set(labels)),
                         "registry labels must be unique")


class TestKillSwitchNoticePosition(unittest.TestCase):
    def test_notice_present_and_between_gk_request_and_burn_snapshot(self):
        labels = _registry_labels()
        self.assertIn(NOTICE, labels, "the kill-switch notice entry is missing")
        i = labels.index(NOTICE)
        self.assertEqual(labels[i - 1], "gk_request_backstop")
        self.assertEqual(labels[i + 1], "burn_snapshot_job")


class TestDocstringJobsAreRegistered(unittest.TestCase):
    """The design's explicit ask, driven FROM the docstring so it stays honest."""

    def test_every_live_docstring_job_8_to_29_maps_to_registry_labels(self):
        labels = set(_registry_labels())
        live = _live_docstring_jobs_8_to_29()
        # every live standalone job number (minus the pane-loop wedge) is mapped
        for n in sorted(live - PANE_LOOP_JOBS_8_TO_29):
            with self.subTest(job=n):
                self.assertIn(
                    n, LIVE_STANDALONE_JOB_LABELS,
                    "docstring job (%d) is LIVE and standalone but has no "
                    "registry-label mapping — add it to the registry + this map" % n)
                for lbl in LIVE_STANDALONE_JOB_LABELS[n]:
                    self.assertIn(
                        lbl, labels,
                        "job (%d)'s label %r is not a registry label" % (n, lbl))
        # and the map never claims a job the docstring marks REMOVED / absent
        for n in LIVE_STANDALONE_JOB_LABELS:
            with self.subTest(mapped=n):
                self.assertIn(
                    n, live,
                    "map claims job (%d) is live-standalone but the docstring "
                    "does not (REMOVED, or renumbered)" % n)


if __name__ == "__main__":
    unittest.main()
