"""#799 — standing template grant for the two mechanical client-message types.

Owner directive (2026-09-01, montalu1): a delivered+reminded client thread that
stays silent must be closed tacitly — but the two MECHANICAL messages that flow
carries (the final reminder + the closing note) then stand in the #628
per-message owner-approval queue, so the hook blocked even an owner-ordered
closing note (thread 271). Fix: the owner approves each stream's TEMPLATE once
(a STANDING grant), and those two message types cite

    airuleset:owner-approved template:final-reminder <ref>
    airuleset:owner-approved template:closing-note  <ref>

instead of a per-message approval. The grant is scoped to EXACTLY the two
sanctioned mechanical types — an UNsanctioned `template:<other>` does NOT grant
approval (still blocks), so the standing grant can never be widened to arbitrary
client messages. A free-form per-message `airuleset:owner-approved <ref>` is
unchanged.

RED against the pre-#799 tree: on HEAD `approval_present` accepted ANY non-empty
ref (marker + horizontal-ws + one non-ws char), so `template:new-feature`
PASSED — this file's `test_unsanctioned_template_type_does_not_grant` is the
RED->GREEN pair.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discuss_thread_guard as g  # noqa: E402

WORD = "airuleset:owner-approved"

# A signed, bound message_post carrying ONLY a template grant (no per-message
# approval), for the end-to-end evaluate check. montalu2 is a stream user.
def _mp(marker):
    return ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
            '[cid],{"body":"<p>Ahoj, vlákno uzatváram — ďakujeme.</p>'
            '<p>ZbynekAI 2</p>"})  # Discuss-ticket: #799  # ' + marker)


class TestSanctionedTemplateTypes(unittest.TestCase):
    """The two mechanical types grant approval (GREEN both before and after —
    positive locks that pin the sanctioned set)."""

    def test_closing_note_grants(self):
        self.assertTrue(g.approval_present(
            "# " + WORD + " template:closing-note owner odsúhlasil šablónu 2026-09-01"))

    def test_final_reminder_grants(self):
        self.assertTrue(g.approval_present(
            "# " + WORD + " template:final-reminder owner odsúhlasil šablónu 2026-09-01"))

    def test_sanctioned_type_alone_is_the_reference(self):
        # the type token itself is the falsifiable reference to the standing
        # template — a trailing ref is optional.
        self.assertTrue(g.approval_present("# " + WORD + " template:closing-note"))

    def test_sanctioned_set_is_exactly_two(self):
        self.assertEqual(
            tuple(sorted(g.SANCTIONED_TEMPLATE_TYPES)),
            ("closing-note", "final-reminder"))


class TestUnsanctionedTemplateBlocks(unittest.TestCase):
    """The RED→GREEN core: an unsanctioned `template:<other>` must NOT grant."""

    def test_unsanctioned_template_type_does_not_grant(self):
        # HEAD accepted this (any non-empty ref); the fix must BLOCK it.
        self.assertFalse(g.approval_present(
            "# " + WORD + " template:new-feature owner said ok"))

    def test_empty_template_type_does_not_grant(self):
        # `template:` with the type on a later token (a space) is not a glued
        # sanctioned type -> no grant.
        self.assertFalse(g.approval_present("# " + WORD + " template: closing-note"))

    def test_near_miss_type_does_not_grant(self):
        self.assertFalse(g.approval_present("# " + WORD + " template:closing-notes"))
        self.assertFalse(g.approval_present("# " + WORD + " template:closing-note-v2"))


class TestFreeFormApprovalUnchanged(unittest.TestCase):
    """The free-form per-message approval path is byte-behaviour-identical."""

    def test_freeform_ref_still_grants(self):
        self.assertTrue(g.approval_present(
            "# " + WORD + " owner odsúhlasil znenie 2026-09-01"))

    def test_bare_marker_still_blocks(self):
        self.assertFalse(g.approval_present("# " + WORD))
        self.assertFalse(g.approval_present("# " + WORD + "   "))
        self.assertFalse(g.approval_present("# " + WORD + "\n"))
        self.assertFalse(g.approval_present("# " + WORD + "\nowner ok"))

    def test_word_template_without_colon_is_freeform(self):
        # "template updated by owner" — first token is `template` (no colon),
        # so it is a normal free-form ref and MUST still grant.
        self.assertTrue(g.approval_present("# " + WORD + " template updated by owner"))

    def test_any_valid_marker_among_several_grants(self):
        # a bad template marker followed by a good free-form marker still passes.
        self.assertTrue(g.approval_present(
            "# " + WORD + " template:new-feature\n# " + WORD + " owner ok"))


class TestEvaluateEndToEnd(unittest.TestCase):
    def test_message_post_with_sanctioned_template_passes(self):
        self.assertIsNone(g.evaluate_message_post_approval(
            _mp(WORD + " template:closing-note ref"), "montalu2"))

    def test_message_post_with_unsanctioned_template_is_a_violation(self):
        v = g.evaluate_message_post_approval(
            _mp(WORD + " template:new-feature ref"), "montalu2")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "2")


if __name__ == "__main__":
    unittest.main()
