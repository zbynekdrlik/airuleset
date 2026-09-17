"""Worker + supervisor bounce-label doctrine (#1056 L1 (e) / #1057 item 5).

RED-first lock: the autopilot-worker template must (1) carry the mandatory
gk-watch / disposition-each-id step, (2) NO LONGER carry the unconditional
reduced-authority bounce clear, and (3) say the composer/bot owns the labels;
the autopilot SKILL must note the blind-label-flip gate.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

WORKER = REPO_ROOT / "agents" / "autopilot-worker.md"
SKILL = REPO_ROOT / "skills" / "autopilot" / "SKILL.md"


class WorkerBounceDoctrine(unittest.TestCase):
    def setUp(self):
        self.worker = WORKER.read_text(encoding="utf-8")
        self.skill = SKILL.read_text(encoding="utf-8")

    def test_worker_has_gk_watch_step(self):
        self.assertIn("gk-watch", self.worker)
        self.assertIn("--issues <N>", self.worker)

    def test_worker_requires_dispositioning_each_id(self):
        self.assertIn("disposition each finding id", self.worker)

    def test_worker_forbids_hand_flipping_labels(self):
        self.assertIn("composer/bot owns those labels", self.worker)

    def test_worker_unconditional_reduced_authority_clear_removed(self):
        # the former reduced-authority hand-off auto-clear is gone
        self.assertNotIn("At the hand-off also clear the bounce lane best-effort",
                         self.worker)

    def test_skill_notes_blind_label_flip_gate(self):
        self.assertIn("block-blind-label-flip.sh", self.skill)
        self.assertIn("gk-watch --issues", self.skill)


if __name__ == "__main__":
    unittest.main()
