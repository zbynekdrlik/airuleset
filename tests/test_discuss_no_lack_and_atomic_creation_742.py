"""#742 -- two client-Discuss doctrine additions to
skills/odoo-discuss-xmlrpc/handover-compose.md:

  1. A client message NEVER tells the client what WE lack (missing data,
     missing access, an unfinished step) -- it reports only what is
     delivered and working. Two legal paths when something is missing on
     our side: fix it first then report the completed result, or don't
     message yet. The one carve-out is a genuine REQUEST for something
     FROM the client, phrased as a concrete ask, never as a complaint
     about a gap.

  2. The #728 "one thread = one topic" atomicity doctrine covered a topic
     emerging INSIDE an already-open thread, but not the CREATION-time
     case: drafting a brand-new proposal that itself would need to cover
     more than one topic. This ticket closes that gap -- atomicity applies
     at creation too, not only to organic growth.

Both land in the SAME file that already owns every other client-facing
Discuss-message doctrine (#628 approval, #650/#657 thread reference, #696
future-promise ban, #702 mention-anchor, #727/#728 atomicity) -- this is a
DOCS-only ticket, so the test is a content-lock/grep-lock, following the
exact `_window()`-bounded pattern established by
test_discuss_thread_doctrine_728.py (a norm()-collapsed window from the
bullet's own unique start-anchor to the next `- **` bullet marker, so a
partial revert of the operative bullet -- not just a full deletion --
still fails the assertions inside the window).
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"


def read(p):
    return p.read_text(encoding="utf-8")


def norm(text):
    """Collapse whitespace runs (incl. a markdown line-wrap's newline+indent)
    to a single space, so a substring check survives prose re-wrapping."""
    return " ".join(text.split())


class _CompanionBase(TestCase):
    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _window(self, start_anchor):
        i = self.raw.index(start_anchor)
        j = self.raw.find("\n- **", i + len(start_anchor))
        if j == -1:
            j = len(self.raw)
        return norm(self.raw[i:j])


class TestNeverMessageWhatWeLack(_CompanionBase):
    """#742 part 1 -- a client message never surfaces our own internal gap."""

    START = "A client message NEVER tells the client what WE lack"

    def test_bullet_present_whole_file(self):
        self.assertIn("NEVER tells the client what WE lack", self.t)

    def test_ticket_cited(self):
        w = self._window(self.START)
        self.assertIn("airuleset #742", w)

    def test_banned_phrase_family_named(self):
        w = self._window(self.START)
        self.assertIn("Chýba nám X", w)
        self.assertIn("nemáme prístup k Y", w)
        self.assertIn("nevieme to overiť", w)
        self.assertIn("nestihli sme Z", w)

    def test_two_legal_paths_present(self):
        w = self._window(self.START)
        self.assertIn("FIX it first", w)
        self.assertIn("THEN message the client about the COMPLETED result", w)
        self.assertIn("DON'T message yet", w)

    def test_mirrors_696_past_events_rule(self):
        # must not be invented in isolation -- it explicitly parallels the
        # existing #696 verified-past-events doctrine already in this file.
        w = self._window(self.START)
        self.assertIn("mirroring the", w)
        self.assertIn("#696 verified-past-events rule above", w)

    def test_client_request_carveout_present(self):
        # a genuine ask FOR something from the client is NOT the banned
        # shape -- it must be phrased as a request, never a complaint.
        w = self._window(self.START)
        self.assertIn("legitimate exception is a genuine REQUEST", w)
        self.assertIn("Potrebovali by sme od vás X", w)
        self.assertIn("never as a complaint about what is", w)

    def test_no_hook_rationale_present(self):
        # the design decision (docs-only, no phrase-match hook) must be
        # traceable in the doctrine itself, not just in the PR/ticket.
        w = self._window(self.START)
        self.assertIn("JUDGMENT call on message CONTENT", w)
        self.assertIn("false-positive risk", w)
        self.assertIn("owner-approval gate", w)


class TestAtomicityAtCreation(_CompanionBase):
    """#742 part 2 -- #728's atomicity doctrine extended to thread CREATION,
    not just organic growth inside an already-open thread."""

    START = "Atomicity also applies at CREATION"

    def test_bullet_present_whole_file(self):
        self.assertIn("Atomicity also applies at CREATION", self.t)

    def test_ticket_cited(self):
        w = self._window(self.START)
        self.assertIn("airuleset #742", w)

    def test_references_728_as_the_organic_growth_half(self):
        w = self._window(self.START)
        self.assertIn("#728 above covers a topic that emerges INSIDE", w)

    def test_creation_time_split_has_teeth(self):
        w = self._window(self.START)
        self.assertIn("split it into SEPARATE threads from the start", w)
        self.assertIn("never bundle them into one opening message", w)
        self.assertIn("One thread = one topic is the rule at every point", w)

    def test_split_threads_keep_full_obligations(self):
        # never a shortcut: each split-out thread still needs its own name,
        # binding and approval -- not a lighter-weight path. The binding
        # clause is asserted on its own token (Discuss-thread:), not just
        # the surrounding "its own" prose, so a partial revert that drops
        # only the binding obligation still fails this test.
        w = self._window(self.START)
        self.assertIn("its own name", w)
        self.assertIn("Discuss-ticket:", w)
        self.assertIn("Discuss-thread:", w)
        self.assertIn("its own owner approval", w)
        self.assertIn("never a shortcut around any of those", w)


class TestPre742DoctrineStillIntact(_CompanionBase):
    """The pre-#742 #696 and #728 bullets must still be present unchanged --
    this ticket only ADDS new bullets, never edits the existing ones."""

    def test_696_future_promise_bullet_present(self):
        self.assertIn(
            "klientska správa sa NIKDY neodvoláva na to", self.t)
        self.assertIn("airuleset #696", self.t)

    def test_728_lifecycle_bullet_present(self):
        self.assertIn("One thread = one topic — now the WHOLE lifecycle", self.t)
        self.assertIn("airuleset #728", self.t)

    def test_728_new_topic_bullet_present(self):
        self.assertIn("A NEW topic a participant", self.t)
        self.assertIn("NEVER developed there", self.t)

    def test_closing_paragraph_survives(self):
        self.assertIn(
            "Every thread this file governs still follows the existing channel",
            self.t)
        self.assertIn("Ask the owner ONE decision at a time", self.t)


if __name__ == "__main__":
    main()
