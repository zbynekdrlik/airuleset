"""#446 — the mandatory `✅ Výstup:` content-verification line in every
`## ✅ Work Complete` report, hook-enforced in the completion branch of
``hooks/stop-check-prose-violations.sh``.

Incident (montalu3/varos, 2026-08-13): order-status notification emails
shipped with 0 € prices everywhere, currency ignored — the session verified
ONLY send/delivery, never the RENDERED content, although it could read the
sent mail from the DB itself. The discipline ("liveness is not verification,
cite real values") existed as prose in ``autonomous-verification.md`` and
provably failed; the report had no mechanical line forcing an output-content
read-back the way ``✅ Regression test:`` forces RED/GREEN SHAs.

RED (proven against 7a8f2e5): a montalu3-shaped report — fully compliant per
the pre-#446 checks, Deploy line saying "odoslanie aj doručenie overené",
NO output line at all — passed the gate clean (``rc=0 blocked=False``).
The enforcement tests below fail on that hook; the control tests pass both
before and after, locking the no-false-block direction.

The contract under test (see the #446 design comment):

* the line is UNCONDITIONAL in every completion report (heading, signal and
  PR-less routes alike) — either concrete observed values read back from the
  real artifact, or an explicit ``n/a — <prečo>``;
* the value floor is mechanical: at least one digit OR a quoted span (a real
  read-back — price, order number, version, quoted subject — essentially
  always carries one; "odoslané OK" carries neither);
* a bare ``n/a`` with no reason is vacuous, same as "sent OK";
* ``n/a`` in a report that itself lists a 🌐/📱 user-clickable surface is a
  self-contradiction — the surface IS a user-facing output;
* fail directions follow the hook's own #194 taxonomy: required-field probes
  fail OPEN (an unevaluable check never becomes an accusation — covered for
  this line too by the grep-stub tests in
  ``tests/test_prose_gate_pipeline_race.py`` /
  ``tests/test_prose_gate_undeterminable.py`` via their ``Missing`` filters).

Hermetic: fresh session id + counter cleanup per run, and ``HOME`` pointed at
a scratch dir so the hook's clean-report ``compact-request --record`` tail can
never touch the real ``~/.claude``.
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

# The montalu3 incident shape: a deploy-verified report whose Deploy line
# proves send/delivery ONLY. Everything the pre-#446 gate requires is present
# and correct — the ONLY variable across the tests below is the Výstup line.
_HEAD = ("## ✅ Work Complete\n\n"
         "**Audits & deploy:**\n"
         "✅ CI: green\n"
         "✅ /plan-check: 4/4 fulfilled\n"
         "✅ /review: clean — 0 %s 0 %s 0 %s\n"
         "✅ /requesting-code-review: clean — 0 %s 0 %s 0 %s\n"
         "✅ Deploy: notifikačné emaily o stave objednávky nasadené — "
         "odoslanie aj doručenie overené\n" % (RED, YELLOW, BLUE,
                                               RED, YELLOW, BLUE))
_TAIL_GLOBE = ("\n---\n\n"
               "**Goal:** Zákazník má dostať email pri zmene stavu objednávky.\n"
               "**What changed:** Pri zmene stavu objednávky sa zákazníkovi "
               "odošle email s cenou a menou.\n\n"
               "%s Prod: https://varos.example.com/shop\n\n"
               "**[odoo-erp] PR #2101: Order status notification emails**\n"
               "https://github.com/zbynekdrlik/odoo-erp/pull/2101 — merged 1a2b3c4\n"
               % GLOBE)

# The fork-shaped PR-less report proven clean end-to-end by
# tests/test_prose_gate_pipeline_race.py — no PR URL, no Deploy, no 🌐 line.
_FORK_HEAD = ("## ✅ Work Complete\n\n"
              "**Audits & deploy:**\n"
              "✅ /plan-check: 3/3 fulfilled\n"
              "✅ /review: clean — 0 %s 0 %s 0 %s\n"
              "✅ /requesting-code-review: clean — 0 %s 0 %s 0 %s\n"
              "✅ Lokálne overenie: testy + lint zelené (fork vetva david/kiosk)\n"
              "✅ Hand-off: READY-FOR-REVIEW komentár na #1393 (kiosk) + karta\n"
              % (RED, YELLOW, BLUE, RED, YELLOW, BLUE))
_FORK_TAIL = ("\n---\n\n"
              "**Goal:** Dochádzkový kiosk pre výrobu.\n"
              "**What changed:** Kiosk beží na erp-test-david.\n\n"
              "✅ DONE: #1393 (kiosk) odovzdané na review, nič ďalšie nečaká.")

GOOD_VYSTUP = ("✅ Výstup: email obj. #2041 — cena 12,50 €, mena CZK pri CZ "
               "objednávke, zákaznícke číslo 2041 zvýraznené v hlavičke\n")


def _report(vystup=None):
    """The montalu3-shaped deploy report, with an optional Výstup line."""
    return _HEAD + (vystup or "") + _TAIL_GLOBE


def _fork_report(vystup=None):
    """The PR-less hand-off report (no 🌐/📱 surface anywhere)."""
    return _FORK_HEAD + (vystup or "") + _FORK_TAIL


class _HookCase(unittest.TestCase):

    def setUp(self):
        # Hermetic HOME: a clean completion report reaching the hook's tail
        # fires `compact-request --record`, which must never write into the
        # real ~/.claude from a test run.
        self._home = tempfile.TemporaryDirectory(prefix="airuleset-446-home-")
        self.addCleanup(self._home.cleanup)
        self.env = {**os.environ, "HOME": self._home.name}

    def _run(self, msg):
        sid = "outline-%s" % uuid.uuid4().hex[:12]
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

    def assertVystupViolation(self, r, why):
        self.assertTrue(self._blocked(r), "%s — no block at all. stdout=%r stderr=%r"
                        % (why, r.stdout[:200], r.stderr.strip()[-300:]))
        self.assertTrue(any("Výstup" in v for v in self._violations(r)),
                        "%s — blocked, but not for the Výstup line: %s"
                        % (why, self._violations(r)))
        self.assertEqual(r.returncode, 0,
                         "%s — blocked but exited non-zero (reads as a hook "
                         "ERROR). stderr=%r" % (why, r.stderr.strip()[-300:]))

    def assertClean(self, r, why):
        self.assertEqual(r.returncode, 0, "%s — rc=%s stderr=%r"
                         % (why, r.returncode, r.stderr.strip()[-300:]))
        self.assertFalse(self._blocked(r), "%s — falsely blocked: %s"
                         % (why, self._violations(r)))


# --------------------------------------------------------------------------- #
# Enforcement — RED against the pre-#446 hook
# --------------------------------------------------------------------------- #

class TestOutputLineIsRequired(_HookCase):

    def test_montalu3_shaped_report_without_the_line_is_blocked(self):
        """The incident verbatim: send/delivery verified, rendered content
        never read back, no output line — must block, naming the line."""
        r = self._run(_report(vystup=None))
        self.assertVystupViolation(r, "the montalu3 shape (no Výstup line)")

    def test_prless_handoff_report_without_the_line_is_blocked(self):
        """The PR-less route is the SAME obligation — the incident session was
        a stream/served session, not a PR-producing worker."""
        r = self._run(_fork_report(vystup=None))
        self.assertVystupViolation(r, "a PR-less hand-off report (no Výstup line)")

    def test_value_free_line_is_blocked(self):
        """'odoslané OK' — no digit, no quoted span, not n/a: liveness prose
        dressed as the line. The floor requires an actually-observed value."""
        r = self._run(_report(vystup="✅ Výstup: odoslané OK, doručené\n"))
        self.assertVystupViolation(r, "a value-free 'sent OK' Výstup line")

    def test_bare_na_without_reason_is_blocked(self):
        r = self._run(_report(vystup="✅ Výstup: n/a\n"))
        self.assertVystupViolation(r, "bare 'n/a' with no reason")

    def test_na_alongside_a_globe_surface_is_blocked(self):
        """A report that lists a 🌐 user-clickable surface while claiming the
        work has no user-facing output contradicts itself — read something
        back from that surface instead."""
        r = self._run(_report(
            vystup="✅ Výstup: n/a — táto zmena nemá user-facing výstup\n"))
        self.assertVystupViolation(r, "n/a in a report carrying a 🌐 surface")


# --------------------------------------------------------------------------- #
# Controls — pass BEFORE and AFTER; the no-false-block direction
# --------------------------------------------------------------------------- #

class TestLegitimateReportsAreNotBlocked(_HookCase):

    def test_concrete_observed_values_pass(self):
        """The exact counter-example the ticket mandates: price, currency,
        order number read back from the real rendered email."""
        r = self._run(_report(vystup=GOOD_VYSTUP))
        self.assertClean(r, "a report citing concrete observed values")

    def test_na_with_reason_and_no_surface_passes(self):
        """Genuinely output-less work (an internal hook change) states the
        explicit n/a with a reason — and lists no user-facing surface."""
        r = self._run(_fork_report(
            vystup="✅ Výstup: n/a — čisto interná zmena hooku, "
                   "žiadny user-facing artefakt\n"))
        self.assertClean(r, "an explicit n/a-with-reason on surface-less work")

    def test_quoted_observation_without_digits_passes(self):
        """The floor is digit OR quoted span — a textual read-back (quoted
        heading observed in the artifact) is a real observation too."""
        r = self._run(_report(
            vystup="✅ Výstup: PDF faktúra — hlavička obsahuje „Montalu "
                   "s.r.o.“, pečiatka a podpis viditeľné\n"))
        self.assertClean(r, "a quoted no-digit observation")

    def test_a_working_message_is_untouched(self):
        r = self._run("⏳ WORKING: bežím na #446 (Výstup line), "
                      "nothing needed from you.")
        self.assertClean(r, "an ordinary ⏳ WORKING progress message")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
