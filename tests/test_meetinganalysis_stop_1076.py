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
import re
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


class TestReviewFixes(_TmpBase):
    """#1076 review fixes."""

    def test_transcript_txt_plus_notes_md_unrelated_report_allows(self):
        # B 🔴: a transcription/podcast project's report mentions transcript.txt
        # AND names a real unstamped NOTES.md -> must NOT be blocked (the generic
        # transcript.txt/frames_kept tokens were removed from _MEETING_REPORT_RE).
        self._write("NOTES.md", "# project notes")
        report = ("## ✅ Work Complete\nRegenerated transcript.txt and updated "
                  "%s/NOTES.md in the docs.\n" % self.d)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "allow")

    def test_video_notes_plus_mapping_md_unrelated_report_allows(self):
        self._write("MAPPING.md", "# route mapping")
        report = ("## ✅ Work Complete\nProcessed the video-notes and wrote "
                  "%s/MAPPING.md.\n" % self.d)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "allow")

    def test_main_unknown_stamp_journals_not_blocks(self):
        # A/F5: a `main unknown` stamp is a main session whose model was
        # unreadable -> fail-OPEN (journal), never a worker -> never block.
        p = self._write("screen_inventory.md", "Analysed-by: main unknown")
        report = "## ✅ Work Complete\nmeeting analysis: %s\nAnalysed-by: main unknown\n" % p
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "journal")

    def test_family_derived_from_managed_model(self):
        # A/F7: the accepted family is derived from airuleset.MANAGED_MODEL, and
        # the live MANAGED_MODEL must itself be in that family (drift-lock).
        import airuleset
        fam = ms._expected_family()
        mm = re.sub(r"\[[^\]]*\]$", "", airuleset.MANAGED_MODEL.strip().lower())
        self.assertTrue(mm.startswith(fam),
                        "MANAGED_MODEL %r not in derived family %r" % (mm, fam))
        # a deliverable stamped with the live managed model must pass
        p = self._write("screen_inventory.md", "Analysed-by: main %s" % airuleset.MANAGED_MODEL)
        report = "## ✅ Work Complete\nmeeting analysis: %s\nAnalysed-by: main %s\n" % (p, airuleset.MANAGED_MODEL)
        v, _ = ms.evaluate(report, self.d)
        self.assertEqual(v, "allow")


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
