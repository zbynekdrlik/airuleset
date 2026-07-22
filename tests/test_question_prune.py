"""Terminal-answered ❓ pruning — the 'otazky' badge must be trustworthy.

2026-07-22 complaint: the statusline badge counted 14 machine-global questions
in a project with zero pending — and most of them were already answered by the
user TYPING DIRECTLY into the asking session (the map only dropped entries on
the Discord-reply route or at the 24h TTL). prune_answered_questions drops an
entry the moment its session's transcript shows a HUMAN prompt newer than the
❓ — machine-typed prompts (watchdog nudges/deliveries, auto-armed /goal,
harness task-notifications, slash-command echoes) and tool_result entries must
never count as an answer.
"""

import json
import sys
import time
import unittest
import unittest.mock as m
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify
import watchdog as wd

CWD = "/home/x/devel/demo"
SID = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def _user(epoch, content):
    return {"type": "user", "timestamp": _iso(epoch),
            "message": {"role": "user", "content": content}}


class PruneAnsweredQuestions(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.qpath = str(Path(self.tmp.name) / "q.json")
        p = m.patch.object(notify, "_questions_path", lambda: self.qpath)
        p.start()
        self.addCleanup(p.stop)
        self.projects = Path(self.tmp.name) / "projects"
        self.now = time.time()
        self.qts = self.now - 3600                     # the ❓ pinged 1h ago

    def _record(self):
        notify.record_question("888001", "777001", SID, CWD, now=self.qts,
                               path=self.qpath, question="Ticket #9 — ako?")

    def _transcript(self, entries):
        d = self.projects / wd.encode_project_dir(CWD)
        d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n")

    def _prune(self):
        return wd.prune_answered_questions(self.now,
                                           projects_dir=str(self.projects))

    def test_human_prompt_after_question_prunes_the_entry(self):
        self._record()
        self._transcript([_user(self.qts + 600, "nejake otazky na mna?")])
        logs = self._prune()
        self.assertTrue(any("pruned" in ln for ln in logs), logs)
        self.assertNotIn("888001", notify.load_questions(self.qpath))

    def test_human_prompt_before_question_keeps_it(self):
        self._record()
        self._transcript([_user(self.qts - 600, "sprav to takto")])
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_machine_prompts_never_count_as_answers(self):
        self._record()
        self._transcript([
            _user(self.qts + 100, "continue"),
            _user(self.qts + 200, "stuck-check: tvrdíš ⏳ WORKING ale ..."),
            _user(self.qts + 300, "Priorita: prio:bounce #12 — rieš"),
            _user(self.qts + 400, "/goal MASTER LOOP — ..."),
            _user(self.qts + 500, "Odpoveď z Discordu: 2026-07-22 ..."),
            _user(self.qts + 600, "<task-notification>\n<task-id>x</task-id>"),
            _user(self.qts + 650, "<command-name>/compact</command-name>"),
            _user(self.qts + 700, [{"type": "tool_result", "content": "ok"}]),
        ])
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))

    def test_prompt_within_grace_window_keeps_it(self):
        # never race the ❓ turn's own machinery — 30s grace
        self._record()
        self._transcript([_user(self.qts + 10, "ano")])
        self.assertEqual(self._prune(), [])

    def test_missing_transcript_keeps_the_entry(self):
        self._record()
        self.assertEqual(self._prune(), [])
        self.assertIn("888001", notify.load_questions(self.qpath))


if __name__ == "__main__":
    unittest.main()
