"""Question-block extraction must survive gawk AND carry the úvod.

Incident (david@gk, 2026-07-09 00:44): the ❓ ping reached Dávid's phone as ONE
bare line — no briefing, no options — even though the session wrote a fully
template-compliant question (**Otázka — projekt …:** + options + decision).
Two independent causes:

1. The gatekeeper box's awk is GAWK 5.2.1 (dev boxes run mawk). In a UTF-8
   locale gawk REJECTS the `[\\200-\\277]` bracket ("Invalid collation
   character") — extract_block crashed, payload came back EMPTY, and send_q
   fell back to the bare marker line. Fix: those awk programs run LC_ALL=C
   (bytes on both awks; the cplen() byte-minus-continuations trick then works
   everywhere).
2. The context pull took exactly ONE paragraph above a bare marker, so a
   question written as briefing / options / decision SEPARATED BY BLANK LINES
   lost its úvod even where awk worked. Fix: keep pulling paragraphs upward
   (max 3, overall cap) and stop once the paragraph carrying the
   `Otázka — projekt` briefing head is included.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "hooks" / "notify-discord-pending.sh"
GATE = ROOT / "hooks" / "stop-check-question-quality.sh"

# The real 0:44 message shape (condensed): analysis prose, then the template
# written as THREE paragraphs separated by blank lines.
DAVID_MSG = """\
Analýza hotová — všetky tri časti sú realizovateľné natívne v Odoo.

Zdroje: OCA/shift-planning a Odoo 19 Planning docs.

**Otázka — projekt slovnormal Odoo (dochádzkový kiosk):** Podľa analýzy sú všetky 3 časti realizovateľné natívne v Odoo, na tej istej kiosk URL. Potvrď rozsah nech spravím kompletný dizajn:

- **Všetko 3 natívne (odporúčam)** — dochádzka + voľno + zmeny, jeden zdroj pravdy.
- **Zatiaľ dochádzka + voľno** — zmeny nechať na zmeny.grena.sk, doplniť neskôr.

❓ NEEDS YOU: ktorý rozsah ideme dizajnovať — všetko 3, alebo zatiaľ dochádzka + voľno?
"""


def run_extract_block(msg):
    """Run the pending hook's ACTUAL extract_block on msg (marker = last line)."""
    func = _sed_range(PENDING, "extract_block")
    script = (
        "#!/bin/bash\nMSG=$(cat \"$1\")\n" + func + "\n"
        "N=$(printf '%s\\n' \"$MSG\" | grep -nvE '^[[:space:]]*$' "
        "| tail -1 | cut -d: -f1)\n"
        "extract_block \"$N\"\n")
    with tempfile.TemporaryDirectory() as d:
        sh = Path(d) / "run.sh"
        sh.write_text(script)
        mf = Path(d) / "msg.txt"
        mf.write_text(msg)
        r = subprocess.run(["bash", str(sh), str(mf)],
                           capture_output=True, text=True)
        return r.stdout, r.stderr


def _sed_range(path, func_name):
    text = path.read_text()
    m = re.search(r"^%s\(\) \{.*?^\}$" % re.escape(func_name),
                  text, re.M | re.S)
    assert m, f"{func_name} not found in {path.name}"
    return m.group(0)


class TestOctalAwkRunsUnderCLocale(TestCase):
    """Every awk program using the `[\\200-\\277]` byte class MUST be invoked
    as `LC_ALL=C awk` — gawk (the gatekeeper box's awk) fatally rejects the
    octal bracket in a UTF-8 locale, killing the whole extraction."""

    def _check(self, path):
        lines = path.read_text().splitlines()
        octal_idx = [i for i, l in enumerate(lines) if "\\200" in l]
        self.assertTrue(octal_idx, f"no octal class in {path.name}?")
        for i in octal_idx:
            # find the awk invocation opening this program (search upward)
            for j in range(i, max(-1, i - 15), -1):
                if re.search(r"\bawk\b", lines[j]):
                    self.assertIn("LC_ALL=C", lines[j],
                                  f"{path.name}:{j + 1} awk with octal class "
                                  "not forced to the C locale — gawk dies")
                    break
            else:
                self.fail(f"{path.name}:{i + 1} could not locate awk invocation")

    def test_pending_hook(self):
        self._check(PENDING)

    def test_quality_gate(self):
        self._check(GATE)


class TestBlockCarriesWholeQuestion(TestCase):
    def test_briefing_options_and_decision_all_present(self):
        out, err = run_extract_block(DAVID_MSG)
        self.assertEqual(err.strip(), "")
        self.assertIn("Otázka — projekt slovnormal Odoo", out)   # úvod
        self.assertIn("Všetko 3 natívne (odporúčam)", out)       # options
        self.assertIn("❓ NEEDS YOU: ktorý rozsah", out)          # decision

    def test_pull_stops_at_briefing_never_grabs_transcript_prose(self):
        out, _ = run_extract_block(DAVID_MSG)
        self.assertNotIn("OCA/shift-planning", out)   # paragraph above briefing
        self.assertNotIn("Analýza hotová", out)

    def test_bare_marker_with_one_context_paragraph_still_works(self):
        # the original codex-bridge shape: short marker + one paragraph above
        msg = ("Projekt X: PR #5 je zelené, čakám na schválenie merge.\n\n"
               "❓ NEEDS YOU: schváliš merge PR #5?\n")
        out, err = run_extract_block(msg)
        self.assertEqual(err.strip(), "")
        self.assertIn("PR #5 je zelené", out)
        self.assertIn("schváliš merge", out)

    def test_contiguous_template_block_passes_through_whole(self):
        msg = ("**Otázka — projekt iem (mixovanie zvuku):** PR #5 je celé "
               "zelené a projekt má manuálny merge marker.\n"
               "• schváliť (odporúčam) — nasadí sa hneď\n"
               "• počkať — nič sa nedeje\n"
               "❓ NEEDS YOU: schváliš merge PR #5?\n")
        out, err = run_extract_block(msg)
        self.assertEqual(err.strip(), "")
        self.assertIn("Otázka — projekt iem", out)
        self.assertIn("schváliš merge", out)


if __name__ == "__main__":
    main()
