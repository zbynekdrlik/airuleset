"""#977 — ``stop-check-question-quality.sh`` must BLOCK a TEXT-approval ❓ block
that carries a share/file-drop URL WITHOUT an inline quoted draft (>= 2 ``> ``
lines).

Owner regression (miva1, 2026-09-10): a session asked for approval of a client
reply like this:

    Návrh textu: http://100.118.174.27:8795/QwyMS1ybzQ65aDuZ1Rbxog/accept-6726-draft.html
    ❓ NEEDS YOU: schvaľuješ text odpovede Alene k #6726 …

The owner's comment: "miesto toho aby si mi tu napísal text čo chceš poslať,
nikde neevidujem že by som definoval že texty majú chodiť do externej web!!!"

The fix extends Check 8 (approvebody) to also detect share/file-drop URLs as
pointers (per-uid share ports `:8788-:8819/`, ``//drop-`` host gateway),
and requires >= 2 ``> `` quoted lines (or >= 40 chars fenced block) as
satisfying inline evidence.

Three fixture classes:
  - RED: the miva1 block (share URL, no quoted block) -> must exit 2 (BLOCK)
  - GREEN-pass: same URL plus a 3-line ``> `` quote -> must PASS
  - GREEN-pass: a non-text ❓ with a share URL (e.g. a screenshot) -> must PASS
"""

import json
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"

_LQ = "„"  # „
_RQ = "“"  # "


class _HookCase(unittest.TestCase):
    """Feed the Stop hook a payload and read its block/pass verdict."""

    def _run(self, msg):
        sid = "qinline977-%s" % uuid.uuid4().hex[:12]
        for f in (
            "/tmp/airuleset-question-quality-block-" + sid,
            "/tmp/claude-discord-lastq-" + sid,
            "/tmp/claude-user-active-" + sid,
            "/tmp/claude-lastq-refs-" + sid,
        ):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, timeout=30)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _reason(self, r):
        if not self._blocked(r):
            return ""
        return json.loads(r.stdout)["reason"]


# ---------------------------------------------------------------------------
# RED fixture: the miva1 incident shape — MUST BLOCK
# ---------------------------------------------------------------------------

# The exact miva1 shape: a share URL, text-approval keywords, no quoted block.
MIVA1_SHARE_URL_NO_QUOTE = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre klienta MIVA):**"
    " Pripravil som odpoveď pre Alenu k objednávkovému portálu.\n"
    "Vlákno: " + _LQ + "Zákaznícky portál 3" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_290\n"
    "Odoo task: " + _LQ + "Portál" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/510\n"
    "\n"
    "Návrh textu: http://100.118.174.27:8795/QwyMS1ybzQ65aDuZ1Rbxog/"
    "accept-6726-draft.html\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schvaľuš text odpovede Alene k #6726?\n"
)

# Variant: drop-* host URL instead of :8795
DROP_HOST_URL_NO_QUOTE = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre klienta):**"
    " Návrh správy pre klienta.\n"
    "Vlákno: " + _LQ + "Objednávky 1" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_291\n"
    "Odoo task: " + _LQ + "Objednávky" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/511\n"
    "\n"
    "Text odpovede: https://drop-subdev.newlevel.media/abc123/reply.txt\n"
    "\n"
    "• Schváliť (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schvaľuš správu?\n"
)

# Variant: :8788 filedrop port
FILEDROP_8788_URL_NO_QUOTE = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre klienta):**"
    " Odpoveď klientovi.\n"
    "Vlákno: " + _LQ + "Výroba 2" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_295\n"
    "Odoo task: " + _LQ + "Výroba" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/520\n"
    "\n"
    "Návrh textu: http://100.104.8.125:8788/xyz/draft.txt\n"
    "\n"
    "• Poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schvaľuš odpoveď?\n"
)


# ---------------------------------------------------------------------------
# GREEN-pass: share URL + inline quoted block -> PASS
# ---------------------------------------------------------------------------

SHARE_URL_WITH_QUOTE = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre klienta MIVA):**"
    " Pripravil som odpoveď pre Alenu k objednávkovému portálu.\n"
    "Vlákno: " + _LQ + "Zákaznícky portál 3" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_290\n"
    "Odoo task: " + _LQ + "Portál" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/510\n"
    "\n"
    "Návrh textu (príloha: http://100.118.174.27:8795/QwyMS1ybzQ65aDuZ1Rbxog/"
    "accept-6726-draft.html):\n"
    "\n"
    "> Dobrý deň,\n"
    "> ďakujeme za Vašu objednávku. Potvrdenie Vám posielame v prílohe.\n"
    "> S pozdravom, ZbynekAI 1\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schvaľuš text odpovede Alene k #6726?\n"
)


# ---------------------------------------------------------------------------
# RED: 1-quote-line boundary — MUST BLOCK (pins the >= 2 threshold)
# ---------------------------------------------------------------------------

ONE_QUOTE_LINE_BLOCKS = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre klienta MIVA):**"
    " Odpoveď pre klienta.\n"
    "Vlákno: " + _LQ + "Zákaznícky portál 3" + _RQ + " "
    "— https://erp.montalu.cloud/odoo/discuss?active_id="
    "discuss.channel_290\n"
    "Odoo task: " + _LQ + "Portál" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/510\n"
    "\n"
    "Návrh textu (http://100.118.174.27:8795/abc/draft.html):\n"
    "\n"
    "> Dobrý deň, ZbynekAI 1\n"
    "\n"
    "• Schváliť a poslať (odporúčam)\n"
    "• Upraviť\n"
    "\n"
    "❓ NEEDS YOU: schvaľuš správu?\n"
)

# ---------------------------------------------------------------------------
# GREEN-pass: non-text ❓ with a share URL (e.g. screenshot) -> PASS
# This fixture carries an APPROVAL INTENT verb (schvaľuješ) to exercise
# the TEXT_APPROVAL_RX narrowing gate — a screenshot has no text/správ/odpove
# context so TEXT_APPROVAL_RX does NOT match.
# ---------------------------------------------------------------------------

SCREENSHOT_SHARE_URL = (
    "**Otázka — projekt odoo-erp (Odoo ERP pre klienta):**"
    " Na dashboarde vidno chybu v zobrazení grafu predajov.\n"
    "Odoo task: " + _LQ + "Dashboard graf" + _RQ + " "
    "(stage: V riešení) "
    "— https://erp.montalu.cloud/odoo/project/4/tasks/600\n"
    "\n"
    "Screenshot: http://100.118.174.27:8795/abc123/screenshot.png\n"
    "\n"
    "• Schváliť opravu grafu (odporúčam)\n"
    "• Nechať\n"
    "\n"
    "❓ NEEDS YOU: schvaľuješ opravu grafu?\n"
)


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestInlineDraftBlock(_HookCase):
    """The miva1 incident shape: share URL + text-approval keywords + no
    inline quoted block -> MUST BLOCK."""

    def test_miva1_share_url_blocks(self):
        r = self._run(MIVA1_SHARE_URL_NO_QUOTE)
        self.assertTrue(self._blocked(r),
                        "miva1 share URL with no inline quote should BLOCK")
        self.assertIn("#977", self._reason(r))

    def test_drop_host_url_blocks(self):
        r = self._run(DROP_HOST_URL_NO_QUOTE)
        self.assertTrue(self._blocked(r),
                        "drop-* host URL with no inline quote should BLOCK")

    def test_filedrop_8788_url_blocks(self):
        r = self._run(FILEDROP_8788_URL_NO_QUOTE)
        self.assertTrue(self._blocked(r),
                        ":8788 URL with no inline quote should BLOCK")

    def test_one_quote_line_blocks(self):
        """Pins the >= 2 threshold: 1 quote line is NOT enough."""
        r = self._run(ONE_QUOTE_LINE_BLOCKS)
        self.assertTrue(self._blocked(r),
                        "1 quote line with share URL should BLOCK")


class TestInlineDraftPass(_HookCase):
    """Compliant shapes: inline quoted block present or non-text question."""

    def test_share_url_with_quote_passes(self):
        r = self._run(SHARE_URL_WITH_QUOTE)
        self.assertFalse(self._blocked(r),
                         "share URL + 3-line > quote should PASS")

    def test_screenshot_share_url_passes(self):
        r = self._run(SCREENSHOT_SHARE_URL)
        self.assertFalse(self._blocked(r),
                         "non-text screenshot share URL should PASS")


class TestInlineDraftLock(unittest.TestCase):
    """Content-lock: the hook must contain the #977 share-URL pointer pattern."""

    def test_hook_has_share_url_pointer(self):
        src = HOOK.read_text()
        self.assertIn("POINTER_URL_RX", src)

    def test_hook_reason_mentions_977(self):
        src = HOOK.read_text()
        self.assertIn("#977", src)


if __name__ == "__main__":
    unittest.main()
