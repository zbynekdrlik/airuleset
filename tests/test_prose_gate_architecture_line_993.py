"""#993 — the mandatory `🏛 Architektúra:` area-review verdict line in every
`## ✅ Work Complete` report, hook-enforced in the completion branch of
``hooks/stop-check-prose-violations.sh`` (extending the existing gate, no new
hook).

Owner directive (2026-09-11): every integrated change passes a main-session
(Fable) AREA review with a binary verdict, and that verdict must be VISIBLE in
the report. The compact template (#940) gains one line:

    🏛 Architektúra: <oblasť> — OK
    🏛 Architektúra: <oblasť> — REWORK #N (<názov>)

Same UNCONDITIONAL / fail-OPEN discipline as the `✅ Výstup:` line (#446).
"""

import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"

RED = "\U0001f534"
YELLOW = "\U0001f7e1"
BLUE = "\U0001f535"
GLOBE = "\U0001f310"
ARCH = "\U0001f3db"  # 🏛

_HEAD = ("## ✅ Work Complete\n\n"
         "**Audits & deploy:**\n"
         "✅ CI: green\n"
         "✅ /plan-check: 4/4 fulfilled\n"
         "✅ /review: clean — 0 %s 0 %s 0 %s\n"
         "✅ /requesting-code-review: clean — 0 %s 0 %s 0 %s\n"
         "✅ Výstup: n/a — interná zmena hooku, žiadny user-facing artefakt\n"
         % (RED, YELLOW, BLUE, RED, YELLOW, BLUE))
_TAIL = ("\n---\n\n"
         "**Goal:** Prerobiť oblasť autopilot orchestrácie.\n"
         "**What changed:** Pridaný area-review gate.\n\n"
         "**[airuleset] PR #993: area review gate**\n"
         "https://github.com/zbynekdrlik/airuleset/pull/993 — merged 1a2b3c4\n")


def _report(arch_line=None):
    return _HEAD + (arch_line or "") + _TAIL


class _HookCase(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory(prefix="airuleset-993arch-home-")
        self.addCleanup(self._home.cleanup)
        self.env = {**os.environ, "HOME": self._home.name}

    def _run(self, msg):
        sid = "arch993-%s" % uuid.uuid4().hex[:12]
        self.addCleanup(
            lambda: Path("/tmp/airuleset-stop-block-%s" % sid).unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, env=self.env, timeout=300)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _violations(self, r):
        if not self._blocked(r):
            return []
        reason = json.loads(r.stdout)["reason"].replace("\\n", "\n")
        return [ln.strip() for ln in reason.splitlines() if ln.startswith("- ")]

    def assertArchViolation(self, r):
        self.assertTrue(self._blocked(r),
                        "expected a block. stdout=%r stderr=%r"
                        % (r.stdout[:200], r.stderr.strip()[-300:]))
        self.assertTrue(any("Architekt" in v or "Architect" in v
                            for v in self._violations(r)),
                        "blocked, but not for the Architektúra line: %s"
                        % self._violations(r))
        self.assertEqual(r.returncode, 0, r.stderr.strip()[-300:])

    def assertClean(self, r):
        self.assertEqual(r.returncode, 0, r.stderr.strip()[-300:])
        self.assertFalse(self._blocked(r),
                         "falsely blocked: %s" % self._violations(r))


class TestArchitectureLineRequired(_HookCase):
    def test_report_without_arch_line_is_blocked(self):
        self.assertArchViolation(self._run(_report(arch_line=None)))

    def test_ok_verdict_passes(self):
        self.assertClean(self._run(_report(
            "%s Architektúra: autopilot orchestrácia — OK\n" % ARCH)))

    def test_rework_verdict_passes(self):
        self.assertClean(self._run(_report(
            "%s Architektúra: watchdog — REWORK #1001 (watchdog state rework)\n"
            % ARCH)))

    def test_vs16_emoji_presentation_glyph_is_accepted(self):
        # #993-review 🟡: 🏛️ (U+1F3DB U+FE0F) is the common emoji-presentation
        # form models emit — it must not false-block a correct report.
        self.assertClean(self._run(_report(
            "\U0001f3db️ Architektúra: autopilot orchestrácia — OK\n")))


if __name__ == "__main__":
    unittest.main()
