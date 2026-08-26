"""#714 — job-20 partition-audit nudge: busy-pane gate + size cap.

Incident (owner, david2@subdev, 2026-08-26): the job-20 partition-audit nudge was
TYPED into the david2 prompt but NEVER submitted — it parked orphaned in the input
line. The session was mid-turn in the "✻ Waiting for 1 background agent to finish"
state, and the nudge itself was a WALL (full doctrine + a named list of 53 W
tickets) that collapses into a `[Pasted text]` placeholder the send/undo/janitor
machinery cannot recover.

Two independent defects, one RED file (root cause traced in the #714 design
comment):

1. NO BUSY-PANE GATE — `goal_ops_wait_recheck` delivered via `send_verified`
   with no check for the "Waiting for N background agents" state, so a submit
   into that transient state is swallowed and parks. The fix threads the pane
   `captured` into the orchestrator and DEFERS when
   `watchdog._BG_AGENTS_WAIT_RX` matches — NARROW to the Waiting line only, NOT
   the agent-strip `◯` rows (an armed autopilot loop ALWAYS carries those, so
   gating on them would starve the nudge forever).

2. UNBOUNDED NUDGE — `_nudge_text` enumerated every W member with per-member ages
   and stacked full-doctrine sub-clauses, thousands of chars. The fix makes it a
   compact TRIGGER (counts + commands + flag counts) hard-capped at
   `NUDGE_MAX_CHARS`; the members stay machine-readable via `slice-quals
   --ops-wait`, not in the keystroke payload.
"""
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from watchdog import ops_wait_recheck as owr  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    GOAL_ARMED_STRIP_CAP,
    _write_marker_transcript,
)

NOW = 1_800_000_000
CAD = 1000
DAY = 86400

# The incident pane state: an armed loop mid-turn, blocked on a background worker.
GOAL_ARMED_WAITING_CAP = (
    "● Predošlá práca hotová.\n"
    "✻ Waiting for 1 background agent to finish\n"
    "❯ \n"
    "  ctx ███░  caveman:lite  ◎ /goal active\n")


def _w_members(n, *, stale=False, gk=False, recheck=False, release_title=False):
    """`n` structured W member dicts (numbers 3498..3498+n-1), optionally all
    flagged — the incident scale for the size-cap worst case."""
    out = []
    for i in range(n):
        out.append({
            "number": 3498 + i,
            "stale": stale,
            "gk_handoff": gk,
            "release_recheck": recheck,
            "title": ("release 2.180 stage-3 gated tail" if release_title
                      else "klient nepotvrdil formular"),
        })
    return out


# --------------------------------------------------------------------------- #
# 1. Busy-pane detection helper (pure).
# --------------------------------------------------------------------------- #

class TestPaneBusyWaiting(unittest.TestCase):
    def test_waiting_line_is_busy(self):
        self.assertTrue(owr._pane_busy_waiting(GOAL_ARMED_WAITING_CAP))

    def test_plural_waiting_line_is_busy(self):
        self.assertTrue(owr._pane_busy_waiting(
            "❯ \n✻ Waiting for 3 background agents to finish\n"))

    def test_agent_strip_rows_are_NOT_busy(self):
        # The narrow gate: an armed loop with LIVE ◯ worker rows (but idle at ❯,
        # no Waiting line) is DELIVERABLE — gating on the strip would starve the
        # nudge on every autopilot box (Prístup 2, rejected).
        self.assertFalse(owr._pane_busy_waiting(GOAL_ARMED_STRIP_CAP))

    def test_clean_armed_prompt_is_not_busy(self):
        self.assertFalse(owr._pane_busy_waiting(GOAL_ARMED_CAP))

    def test_empty_and_none_fail_safe_false(self):
        self.assertFalse(owr._pane_busy_waiting(""))
        self.assertFalse(owr._pane_busy_waiting(None))


# --------------------------------------------------------------------------- #
# 2. Orchestrator busy-pane gate — defer, never park an orphan.
# --------------------------------------------------------------------------- #

class _OrchBase(unittest.TestCase):
    CWD = "/home/newlevel/devel/w714"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-714-orch")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, wrecs, fetch, tmux, *, captured=None, i_count=0, handled=None,
             state=None):
        return owr.goal_ops_wait_recheck(
            NOW, tmux, wrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
            False, handled if handled is not None else set(),
            ops_wait_fetch=fetch, state=state if state is not None else {},
            sleep_fn=lambda *a, **k: None, cadence=CAD, i_count=i_count,
            captured=captured)


class TestBusyPaneGate(_OrchBase):
    def test_waiting_pane_defers_no_keystroke(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        handled = set()
        logs = self._run(wrecs, lambda cwd: [41, 43], tmux,
                         captured=GOAL_ARMED_WAITING_CAP, handled=handled)
        self.assertEqual(tmux.typed_texts(), [],
                         "a Waiting-for-background-agent pane must NOT be typed "
                         "into (the nudge would park orphaned, #714)")
        self.assertTrue(any("busy" in ln for ln in logs))
        self.assertIsNone(wrecs[self.sid]["last_nudge"],
                          "a deferred nudge must NOT advance last_nudge (retry "
                          "next sweep)")
        self.assertNotIn(self.sid, handled)

    def test_non_waiting_pane_delivers(self):
        # The narrow gate must NOT defer a healthy armed-with-workers pane.
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        self._run(wrecs, lambda cwd: [41, 43], tmux,
                  captured=GOAL_ARMED_STRIP_CAP)
        self.assertTrue(any("stuck-check" in t for t in tmux.typed_texts()),
                        "an armed pane with ◯ workers (no Waiting line) must "
                        "still be nudged")
        self.assertEqual(wrecs[self.sid]["last_nudge"], NOW)


class TestBoundedRetry(_OrchBase):
    """#714 escalation (owner, 2026-08-26): a NON-busy pane that persistently
    SWALLOWS the submit must NOT be typed-and-failed every 60s sweep forever —
    the retry storm the owner hit ("neda sa tam teraz pracovat"). After
    MAX_SEND_FAILS consecutive undelivered sends the nudge backs off a full
    cadence instead of retrying every sweep."""

    def test_persistent_swallow_backs_off_after_max_fails(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        state = {}
        attempts = 0
        backed_off = False
        for _ in range(6):
            tmux = self._tmux(enters_swallowed=99)   # every submit swallowed
            logs = self._run(wrecs, lambda cwd: [41], tmux, state=state)
            if any("submit-unverified" in ln for ln in logs):
                attempts += 1
            if any("backing off" in ln for ln in logs):
                backed_off = True
        self.assertTrue(backed_off,
                        "a persistently-swallowing pane must BACK OFF, never "
                        "type-and-fail every 60s sweep (the retry storm, #714)")
        self.assertLessEqual(
            attempts, owr.MAX_SEND_FAILS,
            "bounded retry: at most MAX_SEND_FAILS type-and-fail attempts per "
            "cadence, not one per sweep forever — got %d" % attempts)
        self.assertEqual(wrecs[self.sid]["last_nudge"], NOW,
                         "the backoff advances last_nudge (waits a full cadence)")


# --------------------------------------------------------------------------- #
# 3. Size cap — a TRIGGER, never a wall of 53 tickets + full doctrine.
# --------------------------------------------------------------------------- #

class TestNudgeSizeCap(unittest.TestCase):
    def test_cap_constant_is_reasonable(self):
        self.assertTrue(isinstance(owr.NUDGE_MAX_CHARS, int))
        self.assertLessEqual(owr.NUDGE_MAX_CHARS, 900)

    def test_worst_case_incident_scale_nudge_is_capped(self):
        # I=41, W=53 members ALL flagged stale+gk+recheck+release-shaped, plus
        # release-landed + discuss-audit — the incident's maximal payload.
        members = _w_members(53, stale=True, gk=True, recheck=True,
                             release_title=True)
        t = owr._nudge_text(41, members, NOW,
                            release_landed=[m2["number"] for m2 in members],
                            discuss_audit=True)
        self.assertLessEqual(
            len(t), owr.NUDGE_MAX_CHARS,
            "the incident-scale nudge must be capped (was thousands of chars): "
            "got %d" % len(t))

    def test_nudge_is_a_summary_not_an_enumeration(self):
        # 53 W members -> the nudge must NOT list all 53 numbers (it points at
        # `slice-quals --ops-wait` instead). A count, not a wall.
        members = _w_members(53)
        t = owr._nudge_text(0, members, NOW)
        self.assertNotIn("#4990", t,
                         "the nudge must not enumerate the 53rd W member — the "
                         "members live in `slice-quals --ops-wait`, not the "
                         "keystroke (#714)")
        self.assertNotIn("#3550", t)
        # the count IS surfaced
        self.assertIn("53", t)
        self.assertIn("slice-quals --ops-wait", t)

    def test_still_carries_the_own_payload_prefix(self):
        # The `stuck-check: ` prefix must survive the rewrite (janitor reclaim +
        # machine-prompt exclusion key on it).
        t = owr._nudge_text(3, _w_members(2), NOW)
        self.assertTrue(t.startswith("stuck-check: "))
        self.assertIn("supervisor", t)          # label-ownership note preserved


if __name__ == "__main__":
    unittest.main()
