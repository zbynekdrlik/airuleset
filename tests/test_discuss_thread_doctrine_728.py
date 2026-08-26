"""#728 -- atomic client Discuss threads: a NEW topic surfacing inside an
EXISTING thread is never developed there. Owner directive (2026-08-26,
verbatim): "treba vlakna drzat maximalne atomicke a ak sa otvori nejaka nova
tema vo vlakne tak radsej vytvorit nove vlakno/ticket a spravu ktora temu
vyvolala prekopirovat, presunut do toho noveho vlakna".

Incident: the client thread "Etapy zakaziek vo vyrobe 1" (montalu PROD,
discuss.channel_257) grew to 36 messages across ~6 topics (etapy per typ
zakazky, odovzdavka pracovisk, automaticky posun etap, pavuk workflow,
tablety, vyber pily) plus a brand-new topic the CEO opened in it
(odsuhlasenie zamerania zakazníkom). The owner had to order a manual review
+ closure; precedent for the split-off handling is odoo-erp #5319 (new topic
from msg 1724252/1724253 split out with the triggering messages copied
across), closed via msg 1743448.

This is a DOCS-only ticket (skills/odoo-discuss-xmlrpc/handover-compose.md,
plus an optional pointer in SKILL.md's "Channel + recipients" section) --
so the test is a content-lock/grep-lock, not a behavioral RED->GREEN. Three
things are locked, per #498/#500 (a wrapped multi-line bullet needs a
norm()-collapsed WINDOW bound to its own start-anchor, not a per-line
_teeth mixin -- and the finder/anchor must be UNIQUE to the operative
bullet, never a nearby line that merely explains it):

  1. The lifecycle-widening bullet: "one thread = one topic" now covers the
     WHOLE lifecycle (every follow-up/reminder/reply), not just addressing
     at creation.
  2. The CORE new-topic rule: a new topic in an existing thread is NEVER
     developed there -- a new ticket now, a new thread (after owner
     name+text approval) once it reaches client comms, the triggering
     message COPIED/quoted WITH A CITATION (msg id + author + date), and
     the SAME `Discuss-thread: <channel-id>` binding key (#627) reused when
     the new ticket binds its new thread.
  3. A one-sentence cross-link to the EXISTING #627 closure doctrine
     already in the same file: a long/resolved/multi-topic thread is
     CLOSED, never left to grow forever.

Also asserted: the new-topic rule cannot be read as licensing an unapproved
client message -- it must reference the SAME per-message approval doctrine
already governing every other client-facing post in this file.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "skills" / "odoo-discuss-xmlrpc" / "handover-compose.md"
SKILL = ROOT / "skills" / "odoo-discuss-xmlrpc" / "SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


def norm(text):
    """Collapse whitespace runs (incl. a markdown line-wrap's newline+indent)
    to a single space, so a substring check survives prose re-wrapping --
    the established internals-tests.md pattern (single-line anchor + normalize)."""
    return " ".join(text.split())


class _CompanionBase(TestCase):
    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _window(self, start_anchor):
        """Bound a norm()-collapsed slice from *start_anchor* (must sit on
        ONE physical line, verified against the raw file) to the NEXT `- **`
        bullet marker -- so a PARTIAL revert of the operative bullet (not
        just a full deletion) still fails the assertions inside the window.
        Falls back to end-of-file when no later bullet exists (the CORE
        #728 bullet is the LAST `- **` item in the file, followed only by
        the plain closing paragraph)."""
        i = self.raw.index(start_anchor)
        j = self.raw.find("\n- **", i + len(start_anchor))
        if j == -1:
            j = len(self.raw)
        return norm(self.raw[i:j])


class TestLifecycleWideningBullet(_CompanionBase):
    """#728 part 1 -- "one thread = one topic" widened from addressing-only
    to the whole thread lifecycle."""

    START = "One thread = one topic — now the WHOLE lifecycle"

    def test_bullet_present_whole_file(self):
        # coarse full-deletion guard
        self.assertIn("One thread = one topic", self.t)

    def test_widening_has_teeth(self):
        w = self._window(self.START)
        self.assertIn("covers the thread's ENTIRE lifecycle", w)
        self.assertIn("every follow-up, reminder and reply", w)
        self.assertIn("EXISTING thread", w)
        self.assertIn("thread's OWN topic", w)

    def test_owner_directive_quoted_verbatim(self):
        w = self._window(self.START)
        self.assertIn("airuleset #728", w)
        self.assertIn("2026-08-26", w)
        self.assertIn("vlakna", w)
        self.assertIn("maximalne atomicke", w)
        self.assertIn("nove vlakno/ticket", w)
        self.assertIn("prekopirovat", w)

    def test_incident_named(self):
        w = self._window(self.START)
        self.assertIn("discuss.channel_257", w)
        self.assertIn("36 messages", w)
        self.assertIn("~6", w)


class TestNewTopicCoreRule(_CompanionBase):
    """#728 part 2 -- the CORE rule: a new topic opened inside an existing
    thread is split into its own ticket/thread with the triggering message
    copied across, citation attached, and the #627 binding key reused."""

    START = "A NEW topic a participant"

    def test_bullet_present_whole_file(self):
        self.assertIn("NEW topic", self.t)
        self.assertIn("NEVER developed there", self.t)

    def test_new_ticket_now_has_teeth(self):
        w = self._window(self.START)
        self.assertIn("NEVER developed there", w)
        self.assertIn("creates a NEW ticket immediately", w)

    def test_new_thread_gated_on_owner_approval(self):
        # the rule must NOT read as licensing an unapproved client message --
        # it must point at the SAME per-message approval doctrine already in
        # this file (the FIRST bullet), never a shortcut around it.
        w = self._window(self.START)
        self.assertIn("owner approves its exact name + text", w)
        self.assertIn("SAME per-message approval doctrine", w)
        self.assertIn("FIRST bullet of this file", w)
        self.assertIn("never an excuse to skip approval", w)

    def test_copy_with_citation_has_teeth(self):
        w = self._window(self.START)
        self.assertIn("COPIES/quotes the triggering message", w)
        self.assertIn("A CITATION", w)
        self.assertIn("msg id + author + date", w)
        self.assertIn("context is never torn away", w)

    def test_binding_key_reused_not_reinvented(self):
        w = self._window(self.START)
        self.assertIn("Discuss-thread: <channel-id>", w)
        self.assertIn("#627 closure doctrine above already uses", w)
        self.assertIn("never a second mechanism", w)

    def test_closure_cross_link_present(self):
        # #728 part 3 -- one sentence linking the EXISTING #627 closure
        # doctrine already in this file (never re-stating it).
        w = self._window(self.START)
        self.assertIn("CLOSED", w)
        self.assertIn("#627 bullet above", w)
        self.assertIn("never left to grow forever", w)

    def test_precedent_cited(self):
        w = self._window(self.START)
        self.assertIn("odoo-erp #5319", w)
        self.assertIn("1724252/1724253", w)
        self.assertIn("1743448", w)

    def test_does_not_restate_the_full_closure_bullet(self):
        # the cross-link must stay a POINTER (one sentence), never a second
        # copy of the #627 closing-note mechanics living two bullets above.
        w = self._window(self.START)
        self.assertNotIn("Dobrý deň / Ahoj", w)
        self.assertNotIn("Discuss-defer: siblings", w)

    def test_625_interaction_resolved_without_a_loophole(self):
        # Review finding (fresh-context adversarial pass): the #625 "react
        # to the client's previous answer FIRST" bullet can collide with
        # this one when the client's own last message IS the new topic.
        # The resolution must stay narrow (a brief APPROVED acknowledgement
        # in the existing thread, never developing the new topic there) and
        # must NOT read as an approval bypass.
        w = self._window(self.START)
        self.assertIn("#625", w)
        self.assertIn("brief APPROVED acknowledgement", w)
        self.assertIn("never developing the new topic itself", w)
        self.assertIn("needs the SAME owner approval as any other", w)


class TestClosureBulletStillPresentUnchanged(_CompanionBase):
    """The pre-existing #627 closure doctrine (lines ~247-273 pre-#728) must
    stay intact -- this ticket only ADDS a cross-link to it, never edits it."""

    def test_closure_bullet_present(self):
        self.assertIn(
            "A ticket that BOUND an Odoo Discuss thread may be CLOSED only "
            "after a closing note lands in that thread", self.t)
        self.assertIn("Discuss-closed: msg", self.t)
        self.assertIn("Discuss-defer: siblings", self.t)
        self.assertIn("airuleset #627", self.t)


class TestChannelPlacementParagraphSurvives(_CompanionBase):
    """The pre-#728 channel-placement guidance (sub-thread under the owner's
    named channel, ask one decision at a time) must still be present -- the
    expansion must not have silently dropped it.

    Review finding (fresh-context adversarial pass on this ticket): "sub-
    thread under the channel the owner named" now appears TWICE in the
    file -- once inside the lifecycle bullet's historical quote of the
    PRE-#728 wording, and once in this closing paragraph. A whole-file
    assertIn on that phrase alone would keep passing even if a partial
    edit dropped it from the closing paragraph (it would still match the
    quote). Fix: bound the window to the closing paragraph's OWN unique
    start anchor ("Every thread this file governs") through end-of-file,
    so the check can only be satisfied by the closing paragraph itself."""

    START = "Every thread this file governs"

    def _closing_window(self):
        i = self.raw.index(self.START)
        return norm(self.raw[i:])

    def test_channel_placement_kept(self):
        w = self._closing_window()
        self.assertIn("sub-thread under the channel the owner named", w)
        self.assertIn("never a new top-level channel or group chat", w)
        self.assertIn("Channel + recipients", w)

    def test_ask_one_decision_kept(self):
        w = self._closing_window()
        self.assertIn("Ask the owner ONE decision at a time", w)


if __name__ == "__main__":
    main()
