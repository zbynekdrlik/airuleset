"""Locks the autopilot question-asking policy + the main-context-hygiene module.

User instruction (2026-06-29): in an /autopilot or /goal loop a genuine per-ticket
question is ASKED the moment the ticket needs it (the worker holds the ticket's
context, the loop waits), NOT deferred to a never-reached "backlog exhausted" —
EXCEPT the sleep window 00:00-06:00 Europe/Bratislava. And a new general rule
mandates delegating heavy reading to subagents to keep the main thread thin.

These asserts guard against a regression that silently reinstates the old
"defer the per-ticket question and keep grinding" default.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestQuestionPolicy(TestCase):
    def test_marker_rule_asks_inline_not_deferred(self):
        t = read("modules/core/message-status-marker.md")
        self.assertIn("Europe/Bratislava", t)
        self.assertIn("ASKED THE MOMENT", t)
        # Lock the sleep-window hours so a silent window change (e.g. 22:00-08:00) trips.
        self.assertIn("00..05", t)
        # The old defer-by-default clause must be gone.
        self.assertNotIn("a per-ticket question is ALWAYS deferred", t)

    def test_autopilot_skill_sleep_window_and_ask_now(self):
        t = read("skills/autopilot/SKILL.md")
        self.assertIn("Europe/Bratislava", t)
        self.assertIn("ASK NOW and HOLD", t)
        # Lock the hour boundaries: defer 00..05, ask from 06:00.
        self.assertIn("00..05", t)
        self.assertIn("06:00", t)
        # The old "(b) Defer it + keep working" default must be gone.
        self.assertNotIn("Defer it + keep working", t)
        # The explicit ban on the rationalization that recurred live must stay.
        self.assertIn("loop nemá stáť na čakaní", t)

    def test_worker_asks_the_moment(self):
        t = read("agents/autopilot-worker.md")
        self.assertIn("ASK THE MOMENT", t)
        self.assertIn("Europe/Bratislava", t)
        self.assertIn("00..05", t)

    def test_main_context_hygiene_module_exists_and_wired(self):
        mod = ROOT / "modules" / "core" / "main-context-hygiene.md"
        self.assertTrue(mod.is_file(), "main-context-hygiene.md must exist")
        self.assertIn("Delegate Heavy Reading to Subagents", mod.read_text(encoding="utf-8"))
        self.assertIn("modules/core/main-context-hygiene.md", read("profiles/universal.profile"))


if __name__ == "__main__":
    main()
