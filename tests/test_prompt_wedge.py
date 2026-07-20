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
