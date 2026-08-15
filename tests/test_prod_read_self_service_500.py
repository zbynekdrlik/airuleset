"""#500 — "what's on PROD?" is a SELF-SERVICE question, never a first-choice
UNVERIFIED / hand-off for a prod-STATE READ.

Origin: streams (subdev + gatekeeper) repeatedly wrote "neviem overiť PROD" /
UNVERIFIED / GATEKEEPER-ACTION for a prod READ, though every stream has a
self-service path to any prod fact — the read-only channel + a FRESH COPY of
prod (`REFRESH-DEV-BOX-FROM-PROD: <stream>`, #473). Live incident: odoo-erp
#3997, montalu2, 2026-08-15 — the session twice wrote "membership on PROD I
cannot verify", once after a single HTTP 500 whose body it never read, never
once considering the fresh prod copy that exists for exactly this.

These lock the doctrine on the THREE surfaces those sessions actually LOAD (a
surface they never load is a dead letter, #91): the always-on
`autonomous-verification.md` (the topic owner — loaded by every stream worker
AND the gatekeeper), the `autopilot-worker` agent def (every dispatched
worker), and the `process-subdev` gatekeeper skill.

Teeth per #498 ("a window that also carries the why-prose has no teeth against
a partial revert of the operative line"): each operative fact is asserted on
its OWN line, found by a token UNIQUE to that line — never a coarse window
alone. `_teeth` requires ONE physical line to carry the finder AND every
co-token, so reverting that line to the bug removes the finder+co-tokens
together and the assertion fails. Whole-file assertions stay too (they catch a
FULL deletion). Each teeth was mutation-verified by hand (cp-backup / revert the
operative line / confirm the test FAILS / cp-restore, never `git checkout` on
uncommitted work, #488).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTOVERIFY = ROOT / "modules" / "core" / "autonomous-verification.md"
WORKER = ROOT / "agents" / "autopilot-worker.md"
PROCESS_SUBDEV = ROOT / "skills" / "process-subdev" / "SKILL.md"

# the repo-parameterised fresh-copy trigger the whole doctrine points at (#473);
# its presence on a surface is what makes the self-service answer concrete rather
# than an abstract "verify it yourself".
REFRESH_TRIGGER = "REFRESH-DEV-BOX-FROM-PROD"


def read(p):
    return p.read_text(encoding="utf-8")


def norm(s):
    return " ".join(s.split())


class _TeethMixin:
    def _teeth(self, text, finder, *cotokens):
        """Assert ONE physical line carries `finder` AND every co-token — the
        #498-safe operative-line lock. Reverting that line to the bug drops the
        finder+co-tokens together, so this fails; a why-prose line elsewhere
        that merely mentions `finder` cannot satisfy it without the co-tokens."""
        lines = [ln for ln in text.splitlines() if finder in ln]
        self.assertTrue(
            lines, "finder %r not found (operative line reverted?)" % finder)
        self.assertTrue(
            any(all(c in ln for c in cotokens) for ln in lines),
            "no single line carries %r AND all of %r" % (finder, cotokens))


class TestAutonomousVerificationDoctrine(_TeethMixin, unittest.TestCase):
    def setUp(self):
        self.t = read(AUTOVERIFY)
        self.n = norm(self.t)

    def test_subsection_header_present(self):
        # the doctrine has its own canonical home in the topic-owning module
        self.assertIn("SELF-SERVICE question", self.t)
        self.assertIn("self-service", self.n.lower())

    def test_fresh_copy_fallback_names_the_trigger(self):
        # teeth: the universal-fallback line carries the concrete refresh trigger
        self._teeth(self.t, "universal fallback", REFRESH_TRIGGER)
        self.assertIn(REFRESH_TRIGGER, self.t)

    def test_unverified_for_a_read_is_the_last_choice(self):
        # teeth: UNVERIFIED / hand-off for a prod read is LAST, never first
        self._teeth(self.t, "LAST choice", "UNVERIFIED", "prod read")

    def test_read_is_not_an_un_exercisable_code_path(self):
        # teeth: the distinction line — a "can't verify prod state" hand-off is
        # itself a FINDING, split from a legitimate un-exercisable code PATH
        self._teeth(self.t, "itself a FINDING", "CODE PATH", "self-service")


class TestAutopilotWorkerPointer(_TeethMixin, unittest.TestCase):
    def setUp(self):
        self.t = read(WORKER)

    def test_step0_points_at_the_self_service_prod_read_doctrine(self):
        # teeth: the STEP-0 pointer line carries the trigger, the prod-STATE
        # READ framing, and a pointer back to the owning module
        self._teeth(
            self.t, "Can't verify what's on PROD",
            REFRESH_TRIGGER, "prod-STATE READ", "autonomous-verification.md")

    def test_trigger_present_on_the_worker_surface(self):
        self.assertIn(REFRESH_TRIGGER, self.t)


class TestProcessSubdevReadVsPathSplit(unittest.TestCase):
    def setUp(self):
        self.t = read(PROCESS_SUBDEV)

    def _bullet(self):
        i = self.t.index("Unverifiable-pre-prod paths")
        j = self.t.index("Cross-instance blast radius", i)
        return norm(self.t[i:j])

    def test_bullet_splits_code_path_from_prod_state_read(self):
        b = self._bullet()
        # the new distinction: an un-exercisable CODE PATH stays UNVERIFIED, but
        # a prod-STATE READ is answerable by a fresh copy → not unverifiable
        self.assertIn("CODE PATH", b)
        self.assertIn("prod-STATE READ", b)
        self.assertIn(REFRESH_TRIGGER, b)

    def test_cant_verify_prod_state_handoff_is_a_finding(self):
        b = self._bullet()
        self.assertIn("FINDING", b)

    def test_trigger_present_on_the_gatekeeper_surface(self):
        self.assertIn(REFRESH_TRIGGER, self.t)


if __name__ == "__main__":
    unittest.main()
