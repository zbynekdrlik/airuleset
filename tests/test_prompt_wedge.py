"""Watchdog job 10 — queued-prompt-wedge detection (#20), PING-FIRST.

2026-07-20 incidents (gk, david, gk-master ×3): text sat in the input box —
a submitted-but-stuck queued prompt or an abandoned draft — while the session
idled for hours; nothing could be delivered (job 7's draft protection held)
and nobody was told. Job 10 detects a BYTE-identical input-box text across
>= PWEDGE_SWEEPS sweeps with a >= 30 min stale transcript and no live-work
signals, then sends ONE deduped Discord ping to the pane owner. Deliberately
NO auto-Enter on foreign text (the ticket's decision — a half-typed user
draft must never be submitted by a machine); job 7's own-text Enter-retry
covers the watchdog's own deliveries.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

DRAFT_PANE = ("✳ hotovo — súhrn turnu\n"
              "──── ultracode ─\n"
              "❯\xa0nechať ako je\n"
              "────\n"
              "  ctx ██░░  caveman\n")
EMPTY_PANE = DRAFT_PANE.replace("❯\xa0nechať ako je", "❯\xa0")
BUSY_DRAFT = DRAFT_PANE.replace("✳ hotovo — súhrn turnu",
                                "✳ Baking… (2m · esc to interrupt)")


class FakeSend:
    def __init__(self):
        self.calls = []

    def __call__(self, body, **kw):
        self.calls.append((body, kw))
        return "sent"


def sweep(state, captured, send, now, tm=None):
    tmtime = tm if tm is not None else now - wd.PWEDGE_MIN_IDLE_S - 60
    return wd.prompt_wedge_check(now, state, "%1", captured, tmtime,
                                 "zbynek", "odoo-erp", send)


class TestPromptWedge(unittest.TestCase):
    def test_frozen_draft_two_sweeps_pings_once(self):
        st, s, now = {}, FakeSend(), time.time()
        self.assertEqual(sweep(st, DRAFT_PANE, s, now), [])
        logs = sweep(st, DRAFT_PANE, s, now + 70)
        self.assertEqual(len(s.calls), 1, st)
        self.assertTrue(any("prompt-wedge" in ln for ln in logs), logs)
        sweep(st, DRAFT_PANE, s, now + 140)     # third sweep: no re-ping
        self.assertEqual(len(s.calls), 1)

    def test_changed_text_resets_the_counter(self):
        st, s, now = {}, FakeSend(), time.time()
        sweep(st, DRAFT_PANE, s, now)
        sweep(st, DRAFT_PANE.replace("nechať ako je", "iný text"), s, now + 70)
        self.assertFalse(s.calls)

    def test_empty_box_clears_state_and_never_pings(self):
        st = {"pwedge:%1": {"hash": "x", "n": 2, "pinged": False}}
        s = FakeSend()
        sweep(st, EMPTY_PANE, s, time.time())
        self.assertFalse(s.calls)
        self.assertNotIn("pwedge:%1", st)

    def test_live_work_signals_suppress(self):
        st, s, now = {}, FakeSend(), time.time()
        sweep(st, BUSY_DRAFT, s, now)
        sweep(st, BUSY_DRAFT, s, now + 70)
        self.assertFalse(s.calls)

    def test_fresh_transcript_suppresses(self):
        st, s, now = {}, FakeSend(), time.time()
        for i in range(3):
            sweep(st, DRAFT_PANE, s, now + i * 70, tm=now)
        self.assertFalse(s.calls)

    def test_ping_names_project_text_and_the_enter_action(self):
        st, s, now = {}, FakeSend(), time.time()
        sweep(st, DRAFT_PANE, s, now)
        sweep(st, DRAFT_PANE, s, now + 70)
        body = s.calls[0][0]
        self.assertIn("odoo-erp", body)
        self.assertIn("nechať ako je", body)
        self.assertIn("Enter", body)
        self.assertIn("dedup_key", s.calls[0][1])


if __name__ == "__main__":
    unittest.main()


MACHINE_PANE = ("✻ Waiting for 1 background agent to finish\n"
                "──── ultracode ─\n"
                "❯\xa0Priorita: prio:bounce #1896 - posledny blocker release\n"
                "────\n"
                "  ctx ██░░  caveman\n")


class TestMachineNudgeAutoSubmit(unittest.TestCase):
    """Recurring wedge (3× in 24 h): the gatekeeper's cross-stream nudge into
    the montalu pane loses its Enter and sits unsubmitted for hours. The text
    is MACHINE-authored with a canonical prefix (`Priorita: prio:bounce`) —
    submitting it is always the intent, so job 10 auto-Enters a frozen draft
    matching the prefix (>= 2 identical sweeps), even while the turn runs and
    the transcript is fresh. User text NEVER matches the prefix and keeps the
    ping-first handling."""

    def _run_recorder(self):
        calls = []

        def run(argv, timeout=8):
            calls.append(argv)
            if "pane_in_mode" in " ".join(argv):
                return "0"
            return ""
        run.calls = calls
        return run

    def test_frozen_machine_nudge_gets_entered(self):
        st, s = {}, FakeSend()
        now = time.time()
        run = self._run_recorder()
        wd.prompt_wedge_check(now, st, "%1", MACHINE_PANE, now, "zbynek",
                              "odoo", s, run=run)
        logs = wd.prompt_wedge_check(now + 70, st, "%1", MACHINE_PANE, now,
                                     "zbynek", "odoo", s, run=run)
        enters = [a for a in run.calls if a[-1] == "Enter"]
        self.assertEqual(len(enters), 1, run.calls)
        self.assertTrue(any("machine-nudge" in ln for ln in logs), logs)
        self.assertFalse(s.calls, "machine nudge submits, never pings")

    def test_single_sweep_machine_nudge_waits(self):
        st, s = {}, FakeSend()
        run = self._run_recorder()
        wd.prompt_wedge_check(time.time(), st, "%1", MACHINE_PANE,
                              time.time(), "zbynek", "odoo", s, run=run)
        self.assertFalse([a for a in run.calls if a[-1] == "Enter"])

    def test_protocol_declares_canonical_prefix(self):
        skill = (Path(__file__).resolve().parent.parent / "skills" /
                 "autopilot" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Priorita: prio:bounce", skill)
        self.assertIn("auto-submits", skill)
