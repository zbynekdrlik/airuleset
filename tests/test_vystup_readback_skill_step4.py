"""#450 (F3 from the #446 Výstup-line ticket): the mandatory `✅ Výstup:`
output-content read-back was merged into three homes -- completion-report.md's
template, stop-check-prose-violations.sh's enforcement, and
agents/autopilot-worker.md CYCLE step 8 -- but CYCLE step 8 is the
SERIAL-FALLBACK deploy path. On the DEFAULT worktree-fleet dispatch (#317) the
workers stop at CYCLE step 4 with no deploy, and it is the SUPERVISOR's own
Step 4 round-integration in skills/autopilot/SKILL.md that deploys, verifies
and fires the per-member run-cards. So the SUPERVISOR's Step 4 must carry the
SAME content read-back sibling, or the montalu3 class (send/delivery verified,
rendered content never read) survives untouched on the dominant path.

Locks: the read-back mirror lives in Step 4's own numbered item (the one that
fires the per-member run-cards), it is stated BEFORE the "Fire the per-ticket
run-card yourself" sentence (the observed values must exist at
card-composition time), and it says the load-bearing things -- read back the
real OUTPUT artifact, concrete observed values, send/delivery/liveness alone is
not verification, the values feed the round report's `✅ Výstup:` line, and the
card fires only AFTER the read-back. Scoped to the run-card firing item, never
the whole file, so a future edit cannot silently drop it (the same
vacuous-whole-section trap #432/#435's own lock classes already guard against).
"""
import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL_DOC = ROOT / "skills" / "autopilot" / "SKILL.md"


def _step4_integration_item(text):
    """SKILL.md's Step 3 top-level numbered item `4. **Once EVERY worker in the
    round has returned ...` -- from its own line up to (not including) the next
    top-level numbered item (`^[0-9]+\\. `). This is the item that owns the
    fleet/worktree round integration, the deploy-verify and the per-member
    run-card firing, so it is where the read-back mirror belongs."""
    m = re.search(r"(?m)^4\.\s+\*\*Once EVERY worker in the round has returned\b.*?$", text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"(?m)^[0-9]+\.\s+\*\*", rest)
    return rest[:nxt.start()] if nxt else rest


def _flat(region):
    """Strip leading `>`/whitespace blockquote markers and collapse every
    markdown line-wrap to single spaces -- the read-back mirror legitimately
    wraps across several `>` continuation lines, so a raw substring check
    against the un-flattened region would fail on CORRECT prose (the recurring
    markdown-wrap trap this repo documents)."""
    stripped = re.sub(r"(?m)^[ \t>]*", "", region)
    return " ".join(stripped.split())


class TestVystupReadbackInSupervisorStep4(TestCase):
    def setUp(self):
        self.item = _step4_integration_item(SKILL_DOC.read_text(encoding="utf-8"))
        self.assertTrue(
            self.item.strip(),
            "SKILL.md must have a Step 4 `4. **Once EVERY worker in the round "
            "has returned` numbered item")
        self.flat = _flat(self.item)
        self.assertIn(
            "Fire the per-ticket run-card yourself", self.flat,
            "the Step 4 item must still fire the per-member run-cards -- the "
            "read-back mirror sits alongside that firing")

    def test_reads_back_the_real_output_artifact(self):
        self.assertRegex(
            self.flat, r"(?i)read[ -]?back",
            "Step 4 must instruct reading back the member's actual OUTPUT "
            "artifact before composing its card -- not send/delivery alone")

    def test_requires_concrete_observed_values(self):
        self.assertRegex(
            self.flat, r"(?i)observed value",
            "the read-back must record CONCRETE observed values, mirroring "
            "CYCLE step 8's own wording")

    def test_rejects_liveness_alone(self):
        self.assertRegex(
            self.flat, r"(?i)send/delivery",
            "Step 4 must say send/delivery/liveness alone is NOT verification "
            "-- the montalu3 0 € email class")

    def test_cites_the_montalu3_incident(self):
        self.assertIn(
            "montalu3", self.flat,
            "the mirror must cite the montalu3 incident, exactly as CYCLE step "
            "8 does, so the reasoning traces to the failure it prevents")

    def test_feeds_the_vystup_line(self):
        self.assertIn(
            "Výstup", self.flat,
            "the observed values must be stated to feed the round report's "
            "mandatory `✅ Výstup:` line")

    def test_card_fires_only_after_the_readback(self):
        self.assertRegex(
            self.flat, r"(?i)after this read[ -]?back",
            "the run-card must fire only AFTER the read-back -- the observed "
            "values have to exist at card-composition time")

    def test_readback_precedes_the_card_firing(self):
        rb = re.search(r"(?i)read[ -]?back", self.flat)
        self.assertIsNotNone(
            rb, "no read-back phrase in Step 4 at all — the mirror is missing")
        fire = self.flat.index("Fire the per-ticket run-card yourself")
        self.assertLess(
            rb.start(), fire,
            "the read-back mirror must be stated BEFORE the run-card firing, "
            "so the observed values exist when the card is composed")

    def test_cites_the_450_ticket(self):
        self.assertIn(
            "#450", self.flat,
            "the mirror must cite #450 so the reasoning traces back to the "
            "ticket this guidance exists for")


if __name__ == "__main__":
    main()
