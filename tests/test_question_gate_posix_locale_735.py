"""#735 — `stop-check-question-quality.sh` must not use a POSIX bracket
expression containing multibyte UTF-8 characters WITHOUT forcing a UTF-8-aware
locale on the grep call — under a POSIX/C-locale calling environment (LC_ALL
unset, LC_CTYPE=POSIX; live on david1@subdev, odoo-erp session 185e027a) grep
splits a multibyte bracket class into raw bytes instead of matching whole
characters, so the class silently stops matching.

Three checks in this hook carried the bug (same root cause, same file, no
LC_ALL forced on any of the three greps):

  * Check 1 (briefing head, `Ot[áa]zka...[—–-]...`) — the miss BLOCKS every
    fully compliant `**Otázka — projekt …:**` head. Fail-safe direction
    (over-block), but a genuine 3-retry loop to the retry cap on a box that
    happens to run POSIX locale (the reported incident: david1@subdev).
  * Check 2 (pile detector, `ktor[éú]ko[ľl]vek`) — the miss means a genuine
    multi-question pile SILENTLY passes undetected on a POSIX-locale box.
    Fail-UNSAFE direction (#514: a quality gate must never silently waive).
  * Check 5 (history-allusion, `p[ýy]tal...sk[ôo]r...`) — same fail-unsafe
    miss for a lazy "pýtal som sa skôr" reference instead of restating the
    question in full.

Fix (see the #735 design comment): force `LC_ALL=C.UTF-8` on each of the
three grep invocations, mirroring Check 6's already-established convention
(lines 368-377 of the same file) rather than rewriting to byte-octal
alternations.

These tests run the REAL hook via subprocess with `LC_ALL=POSIX` injected
into the environment — the exact calling-environment shape from the
incident — and assert the hook's actual `{"decision":"block"}` verdict, not
a reimplementation of its regexes.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"

HEAD = ("**Otázka — projekt airuleset (nástroj na správu Claude Code "
        "pravidiel):** Testujem ticket #735 pod POSIX locale prostredím. "
        "Potrebujem rozhodnutie o ďalšom postupe.\n\n")
OPTIONS = ("• Možnosť A (odporúčam) — pokračuje sa hneď\n"
           "• Možnosť B — počká sa na review\n\n")
MARKER = "❓ NEEDS YOU: ktorú možnosť zvoliť?\n"


class _HookCase(TestCase):
    """Runs the real hook via subprocess. `posix_locale=True` reproduces the
    incident's calling environment (LC_ALL=POSIX, no UTF-8 locale anywhere in
    the env); `posix_locale=False` is the box's normal locale, used as a
    same-message control to prove the bug is locale-specific, not a general
    regex defect."""

    def _run(self, msg, posix_locale=True):
        sid = "qg735-%s" % uuid.uuid4().hex[:12]
        for f in (
            "/tmp/airuleset-question-quality-block-" + sid,
            "/tmp/claude-discord-lastq-" + sid,
            "/tmp/claude-user-active-" + sid,
        ):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        env = os.environ.copy()
        if posix_locale:
            env["LC_ALL"] = "POSIX"
            env["LANG"] = "POSIX"
            env["LC_CTYPE"] = "POSIX"
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, timeout=30, env=env)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _reason_tag(self, r):
        """The VIOLATION tag, recovered from the hook's REASON string via the
        same case labels the hook itself uses (briefing/pile/briefwall/
        options/reference/thread) — never a re-derivation of the regex, only
        a label lookup on the hook's own emitted text."""
        if not self._blocked(r):
            return None
        reason = json.loads(r.stdout)["reason"]
        if "has no briefing" in reason:
            return "briefing"
        if "MULTIPLE decisions" in reason:
            return "pile"
        if "wall of text" in reason:
            return "briefwall"
        if "no option bullets" in reason:
            return "options"
        if "references an OLD question" in reason:
            return "reference"
        if "does NOT name the exact target thread" in reason:
            return "thread"
        return "unknown: %s" % reason


class TestCheck1DiacriticHeadUnderPosixLocale(_HookCase):
    """The ticket's headline repro: a byte-for-byte compliant question block
    must NOT be blocked just because the calling shell's locale is POSIX."""

    def test_compliant_head_is_not_falsely_blocked_under_posix_locale(self):
        msg = HEAD + OPTIONS + MARKER
        r = self._run(msg, posix_locale=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            self._blocked(r),
            "a fully compliant '**Otázka — projekt …:**' head was falsely "
            "blocked under LC_ALL=POSIX: %s" % self._reason_tag(r))

    def test_same_message_already_passes_under_the_box_default_locale(self):
        """Control — proves the bug is locale-specific: the identical
        message, run under the box's normal (UTF-8) locale, has never been
        blocked."""
        msg = HEAD + OPTIONS + MARKER
        r = self._run(msg, posix_locale=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self._blocked(r), self._reason_tag(r))


class TestCheck2PileDetectorUnderPosixLocale(_HookCase):
    """Fail-UNSAFE direction: under POSIX locale, a genuine multi-question
    pile must still be CAUGHT, not silently waved through once Check 1 no
    longer false-blocks it first."""

    def test_pile_is_still_caught_under_posix_locale(self):
        msg = (HEAD +
               "Odpovedz na ktorékoľvek z troch: (1) prvá otázka? "
               "(2) druhá otázka?\n\n" + MARKER)
        r = self._run(msg, posix_locale=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            self._blocked(r),
            "a genuine multi-question pile was NOT blocked at all under "
            "LC_ALL=POSIX")
        self.assertEqual(
            "pile", self._reason_tag(r),
            "the pile should be caught by Check 2 (pile) — got %r instead "
            "(Check 1's own locale bug masking Check 2's)"
            % self._reason_tag(r))


class TestCheck5HistoryAllusionUnderPosixLocale(_HookCase):
    """Fail-UNSAFE direction: under POSIX locale, a lazy history-allusion
    reference must still be CAUGHT."""

    def test_allusion_is_still_caught_under_posix_locale(self):
        msg = (HEAD +
               "Ako som sa už pýtal skôr, potrebujem tvoje rozhodnutie.\n\n" +
               OPTIONS + MARKER)
        r = self._run(msg, posix_locale=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            self._blocked(r),
            "a lazy history-allusion reference was NOT blocked at all "
            "under LC_ALL=POSIX")
        self.assertEqual(
            "reference", self._reason_tag(r),
            "the allusion should be caught by Check 5 (reference) — got "
            "%r instead (Check 1's own locale bug masking Check 5's)"
            % self._reason_tag(r))


if __name__ == "__main__":
    main()
