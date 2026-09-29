"""#1177 review round — the ONE-client-draft-per-approval counter
(`gates/draftbundle.py`, driven by `hooks/stop-check-question-quality.sh`).

The fresh-context adversarial review of the first #1177 cut reproduced 32
shapes against the real hook (fixtures in tests/fixtures/draftbundle_1177/,
verbatim). `fb*` = a legitimate single-draft (or non-approval) turn that must
PASS; `st*` = a bundle of two client drafts that must be BLOCKED (exit 2 with
the split instruction). One deliberate exception: fb06 (two signed
alternative VERSIONS of one message) is blocked by design — the owner's rule
is one shown client text per question; the second version belongs in the
options as prose.

Also unit-tests the pure functions of gates.draftbundle (signature-line
detection, approval scope, per-draft counting) directly.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import new_hook_sid  # noqa: E402

GATE = ROOT / "hooks" / "stop-check-question-quality.sh"
FIX = Path(__file__).resolve().parent / "fixtures" / "draftbundle_1177"

# Deliberate: two signed versions of ONE message are still two shown texts.
BLOCKED_BY_DESIGN = {"fb06_two_versions_one_message.txt"}


def _expected_block(name):
    return name.startswith("st") or name in BLOCKED_BY_DESIGN


class FixtureShapes(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-1177db-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def _run(self, msg):
        sid = new_hook_sid(self, "test-1177-db", ["*test-1177-db-*"])
        payload = {"last_assistant_message": msg, "session_id": sid}
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(GATE)], input=json.dumps(payload),
                              text=True, capture_output=True, env=env)

    def test_fixture_set_present(self):
        self.assertGreaterEqual(len(list(FIX.glob("*.txt"))), 35)

    def test_every_fixture_behaves(self):
        for path in sorted(FIX.glob("*.txt")):
            with self.subTest(fixture=path.name):
                r = self._run(path.read_text(encoding="utf-8"))
                if _expected_block(path.name):
                    self.assertEqual(r.returncode, 2, (path.name, r.stdout, r.stderr))
                    self.assertIn("bundluje", r.stderr, (path.name, r.stderr))
                else:
                    self.assertEqual(r.returncode, 0, (path.name, r.stdout, r.stderr))
                    self.assertNotIn("bundluje", r.stderr, (path.name, r.stderr))
                    self.assertNotIn('"block"', r.stdout, (path.name, r.stdout))


class PureFunctions(unittest.TestCase):
    def setUp(self):
        from gates import draftbundle
        self.db = draftbundle

    def test_signature_only_lines(self):
        for line in ("ZbynekAI", "ZbynekAI 4", "> ZbynekAI 4", "— ZbynekAI 4",
                     "> *ZbynekAI 4*", "**ZbynekAI 1**", "ZbynekAI4", "ZbynekAI 4.",
                     "ZbynekAI (montalu4)", "> S pozdravom ZbynekAI 4.",
                     "    ZbynekAI 4.", "<p>ZbynekAI 4</p>"):
            with self.subTest(line=line):
                self.assertTrue(self.db.signature_count(line) == 1, line)

    def test_prose_mentioning_signature_is_not_a_signature(self):
        for line in ("- Poslať (odporúčam) — odíde s podpisom ZbynekAI 4",
                     "✅ Výstup: správa 1742 odoslaná, podpísaná ZbynekAI 4",
                     "Text úloha 638 (podpíšem ho ako ZbynekAI podľa dohody):"):
            with self.subTest(line=line):
                self.assertEqual(self.db.signature_count(line), 0, line)

    def test_html_line_with_two_paragraphs_counts_the_signature(self):
        self.assertEqual(
            self.db.signature_count("<p>Dobrý deň, montáž v piatok.</p><p>ZbynekAI 4</p>"), 1)

    def test_approval_scope(self):
        for q in ("môžem poslať Patrikovi tieto dve správy?",
                  "môžem Patrikovi odpovedať týmito textami?",
                  "môžem to dať klientovi takto?",
                  "súhlasíš s oboma textami vyššie?",
                  "mám to publikovať do Discuss?",
                  "schváliš odoslanie?"):
            with self.subTest(q=q):
                self.assertTrue(self.db.is_approval(q), q)
        for q in ("nasadiť poslednú verziu 0.4.2 na produkčný server teraz?",
                  "schválil to tester, nasadiť verziu 0.4.2?"):
            with self.subTest(q=q):
                self.assertFalse(self.db.is_approval(q), q)

    def test_counts_are_computed_from_the_scope_text(self):
        two = ("Úloha: „A 4\"\n\n> text a\n\nVlákno: „B 4\"\n\n> text b\n")
        self.assertEqual(self.db.analyse(two)["drafts"], 2)
        one = ("Úloha: „A 4\" — https://x/odoo/project/4/tasks/1\n"
               "Vlákno: „A 4\" — https://x/odoo/discuss?active_id=discuss.channel_2\n\n"
               "> text a\n> ZbynekAI 4\n")
        self.assertEqual(self.db.analyse(one)["drafts"], 1)


if __name__ == "__main__":
    unittest.main()
