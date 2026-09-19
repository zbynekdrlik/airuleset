"""#1076 -- gates.meetinganalysis_stop + hooks/stop-check-meeting-analysis.sh.

The meeting-analysis AUTHORSHIP Stop gate (owner directive 2026-09-18): a
meeting-analysis completion report is BLOCKED unless every named, on-disk
deliverable (`screen_inventory.md` / `NOTES.md` / `MAPPING.md`) carries a
first-line `Analysed-by: main claude-fable-*` stamp AND the report carries the
`Analysed-by: main <model>` line. FAIL-OPEN for an unrelated report / an
unreadable deliverable path (a Stop hook must never wedge an unrelated report).

Design acceptance fixtures:
  - a report naming screen_inventory.md whose file lacks the stamp -> exit 2;
  - with the stamp `Analysed-by: main claude-fable-5-1` (+ the report line) -> exit 0;
  - an unreadable path -> exit 0 + journal.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import meetinganalysis_stop as ms  # noqa: E402

HOOK = REPO / "hooks" / "stop-check-meeting-analysis.sh"

STAMP = "Analysed-by: main claude-fable-5-1"


class _TmpBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _write(self, name, first_line):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(first_line + "\n# body\n...\n")
        return p


class TestDesignAcceptance(_TmpBase):
    def test_unstamped_screen_inventory_blocks(self):
        p = self._write("screen_inventory.md", "# Screen inventory")
        report = "## ✅ Work Complete\nmeeting analysis done. Deliverable: %s\n%s\n" % (p, STAMP)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "block")

    def test_stamped_screen_inventory_with_report_line_allows(self):
        p = self._write("screen_inventory.md", STAMP)
        report = "## ✅ Work Complete\nDeliverable: %s\n🧠 %s\n" % (p, STAMP)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "allow")

    def test_unreadable_path_journals_and_allows(self):
        report = ("## ✅ Work Complete\nmeeting analysis done, deliverable "
                  "/no/such/dir/screen_inventory.md\n%s\n" % STAMP)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "journal")


class TestEnforcement(_TmpBase):
    def test_stamped_file_but_report_missing_line_blocks(self):
        p = self._write("screen_inventory.md", STAMP)
        report = "## ✅ Work Complete\nmeeting analysis done. Deliverable: %s\n(forgot the line)\n" % p
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "block")

    def test_worker_stamp_blocks(self):
        p = self._write("MAPPING.md", "Analysed-by: worker claude-opus-4-8")
        report = "## ✅ Work Complete\nmeeting analysis: %s\n%s\n" % (p, STAMP)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "block")

    def test_all_three_stamped_allows(self):
        a = self._write("screen_inventory.md", STAMP)
        b = self._write("NOTES.md", STAMP)
        c = self._write("MAPPING.md", STAMP)
        report = "## ✅ Work Complete\nDeliverables: %s %s %s\n%s\n" % (a, b, c, STAMP)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "allow")

    def test_one_of_three_unstamped_blocks(self):
        a = self._write("screen_inventory.md", STAMP)
        b = self._write("NOTES.md", "# notes (no stamp)")
        report = "## ✅ Work Complete\nDeliverables: %s %s\n%s\n" % (a, b, STAMP)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "block")


class TestFailOpenScoping(_TmpBase):
    def test_unrelated_report_naming_notes_md_allows(self):
        # a NON-meeting report that happens to name a real NOTES.md must NOT be
        # blocked (fleet-wide false-positive guard).
        self._write("NOTES.md", "# project notes")
        report = "## ✅ Work Complete\nUpdated %s/NOTES.md in the docs.\n" % self.d
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "allow")

    def test_no_deliverable_named_allows(self):
        v, _ = ms.evaluate("## ✅ Work Complete\nmeeting analysis: transcript.txt done.\n", self.d)
        self.assertEqual(v, "allow")

    def test_empty_message_allows(self):
        v, _ = ms.evaluate("", self.d)
        self.assertEqual(v, "allow")

    def test_unexpanded_var_path_journals(self):
        report = "## ✅ Work Complete\nmeeting analysis, share $WORK/screen_inventory.md\n%s\n" % STAMP
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "journal")


class TestHook(_TmpBase):
    def _run_hook(self, msg):
        payload = json.dumps({"last_assistant_message": msg, "cwd": self.d})
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": self.d}
        r = subprocess.run(["bash", str(HOOK)], input=payload,
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stderr

    def test_hook_blocks_unstamped(self):
        p = self._write("screen_inventory.md", "# no stamp")
        rc, err = self._run_hook("## ✅ Work Complete\nmeeting analysis: %s\n%s\n" % (p, STAMP))
        self.assertEqual(rc, 2, err)

    def test_hook_allows_stamped_with_line(self):
        p = self._write("screen_inventory.md", STAMP)
        rc, err = self._run_hook("## ✅ Work Complete\nDeliverable %s\n🧠 %s\n" % (p, STAMP))
        self.assertEqual(rc, 0, err)

    def test_hook_allows_unrelated(self):
        rc, err = self._run_hook("## ✅ Work Complete\nfixed the parser bug.\n")
        self.assertEqual(rc, 0, err)


class TestWiring(unittest.TestCase):
    def test_registered_on_stop(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        cmds = []
        for block in cfg["hooks"]["Stop"]:
            cmds.extend(h.get("command", "") for h in block.get("hooks", []))
        self.assertIn("stop-check-meeting-analysis.sh", " ".join(cmds))


if __name__ == "__main__":
    unittest.main()
