"""subdev-handoff-match.sh — the canonical sub-dev hand-off marker matcher
(#21, #1500) that `skills/process-subdev/templates/subdev-handoff-label.yml`
invokes to decide whether a comment adds `ready-for-review` (and now also
`needs-gatekeeper` / clears `prio:bounce`).

#331: the matcher, as deployed, carried a live SIGPIPE/pipefail race — a
genuinely, correctly-anchored `READY-FOR-REVIEW:` marker on line 1 of a
large body was silently rejected, because the pre-fix `printf | grep -q`
pipeline drops the whole verdict to the writer's SIGPIPE exit code once
`grep -q` finds its match and stops reading. Corpus-replayed against every
comment on the 5 named odoo-erp tickets (#2987, #2878, #2895, #2886, #3237)
plus 30 more real issues via `gh api` (see the STILL-VALID comment on
airuleset#331) -- 0 false negatives among 22 real hand-off comments, because
none happened to be large enough to trigger the race deterministically;
the race itself was reproduced directly against the pre-fix script with a
synthetic 80 KB body (well under GitHub's real 65536-char comment cap),
10/10 runs. This file locks the fix (here-string matching) and the two new
GATEKEEPER-ACTION:/BOUNCE-RESOLVED: modes ported from a downstream
consumer's own independently-hardened copy.
"""

import subprocess
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
MATCHER = ROOT / "skills" / "process-subdev" / "templates" / "subdev-handoff-match.sh"


def run(body, mode=None):
    argv = ["bash", str(MATCHER)] + ([mode] if mode else [])
    r = subprocess.run(argv, input=body, capture_output=True, text=True,
                        timeout=30)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


class TestSigpipeRaceFix(TestCase):
    """The #331 core bug: a canonical, line-1 READY-FOR-REVIEW: marker in a
    large body must be detected deterministically, not lost to a
    printf|grep SIGPIPE race."""

    def _big_body(self, marker_line="READY-FOR-REVIEW: something"):
        # ~80 KB, well under GitHub's 65536-CHARACTER comment cap but far
        # past the 64 KiB pipe buffer that makes the pre-fix race
        # deterministic once grep -q exits after matching line 1.
        filler = ("x" * 100 + "\n") * 800
        return marker_line + "\n" + filler

    def test_line1_marker_in_a_large_body_matches_deterministically(self):
        body = self._big_body()
        for _ in range(5):
            rc, out, err = run(body)
            self.assertEqual(rc, 0, "rc=%r out=%r err=%r" % (rc, out, err))
            self.assertIn("READY-FOR-REVIEW", out)

    def test_gatekeeper_action_marker_in_a_large_body_matches_deterministically(self):
        body = self._big_body("GATEKEEPER-ACTION: unblock the docker socket")
        for _ in range(5):
            rc, out, err = run(body, mode="gatekeeper-action")
            self.assertEqual(rc, 0, "rc=%r out=%r err=%r" % (rc, out, err))

    def test_bounce_resolved_marker_in_a_large_body_matches_deterministically(self):
        body = self._big_body("BOUNCE-RESOLVED: fixed on #999")
        for _ in range(5):
            rc, out, err = run(body, mode="bounce-resolved")
            self.assertEqual(rc, 0, "rc=%r out=%r err=%r" % (rc, out, err))

    def test_the_matcher_never_uses_a_printf_grep_pipeline(self):
        # Structural lock -- the SAME regression class recurred repeatedly
        # in this repo's own hooks (#190/#124/#192); anchor on the SHAPE,
        # never the mere mention of "printf"/"grep" (both appear in the
        # header prose explaining the fix).
        src = MATCHER.read_text(encoding="utf-8")
        # No live statement pipes printf's output into grep.
        self.assertNotIn('printf \'%s\\n\' "$BODY" | grep', src)
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotRegex(
                stripped, r'printf\b.*\|\s*grep\b',
                "found a live printf|grep pipeline: %r" % line)


class TestBackwardCompatibleDefaultMode(TestCase):
    """No mode argument (the existing workflow step's call shape) must keep
    behaving exactly like the pre-#331 script for the original contract."""

    def test_line_start_marker_with_markdown_decoration(self):
        rc, out, _ = run("## READY-FOR-REVIEW: round 2 fixes\n\nmore text")
        self.assertEqual(rc, 0)

    def test_closing_phrase_still_matches(self):
        rc, out, _ = run("Some long readiness comment...\n\n"
                          "Ready for gatekeeper cross-fork review.")
        self.assertEqual(rc, 0)

    def test_mid_sentence_mention_is_not_a_handoff(self):
        rc, out, _ = run("**Po READY-FOR-REVIEW pokracujem hned.**")
        self.assertEqual(rc, 1)

    def test_blockquoted_marker_is_not_a_handoff(self):
        rc, out, _ = run("> READY-FOR-REVIEW: quoting someone else's marker")
        self.assertEqual(rc, 1)

    def test_gatekeeper_opening_never_matches_no_matter_the_content(self):
        rc, out, _ = run("**GATEKEEPER review — BOUNCE**\n\n"
                          "READY-FOR-REVIEW: quoting it for context\n"
                          "Ready for gatekeeper cross-fork review.")
        self.assertEqual(rc, 1)

    def test_a_hyphen_glued_gatekeeper_opening_is_still_excluded(self):
        # F1, adversarial review of #331: the exclusion used to be written
        # as "next char after GATEKEEPER is not a literal hyphen", so ANY
        # hyphenated opening (**GATEKEEPER-BOUNCE**, not just the one
        # legitimate **GATEKEEPER-ACTION:** marker) escaped it and would
        # re-add ready-for-review on a gatekeeper's own comment. Live-
        # reproduced against the pre-fix shipped code: rc=0 (bug).
        rc, out, _ = run("**GATEKEEPER-BOUNCE**\n"
                          "READY-FOR-REVIEW: x")
        self.assertEqual(rc, 1)


class TestGatekeeperActionMode(TestCase):
    def test_line_start_marker_matches(self):
        rc, out, _ = run("GATEKEEPER-ACTION: restart the docker socket "
                          "on erp-test-david", mode="gatekeeper-action")
        self.assertEqual(rc, 0)
        self.assertIn("gatekeeper-action", out)

    def test_markdown_decorated_marker_matches(self):
        rc, out, _ = run("**GATEKEEPER-ACTION:** need infra access",
                          mode="gatekeeper-action")
        self.assertEqual(rc, 0)

    def test_mid_sentence_mention_does_not_match(self):
        rc, out, _ = run("this is not a GATEKEEPER-ACTION: request, just "
                          "discussing the convention", mode="gatekeeper-action")
        self.assertEqual(rc, 1)

    def test_a_plain_readiness_comment_does_not_match_this_mode(self):
        # A genuine hand-off must NOT be swallowed by the gatekeeper-action
        # mode -- the whole point of #331's landmine #1 fix is that the two
        # signals stay disjoint.
        rc, out, _ = run("READY-FOR-REVIEW: feature complete\n\n"
                          "Ready for gatekeeper cross-fork review.",
                          mode="gatekeeper-action")
        self.assertEqual(rc, 1)

    def test_gatekeeper_opening_excludes_even_a_bolded_action_marker(self):
        rc, out, _ = run("**GATEKEEPER review — BOUNCE**\n\n"
                          "GATEKEEPER-ACTION: quoting for context",
                          mode="gatekeeper-action")
        self.assertEqual(rc, 1)

    def test_bolded_gatekeeper_action_marker_is_not_excluded_by_the_gatekeeper_check(self):
        # The refined boundary: **GATEKEEPER-ACTION:** starts with
        # "**GATEKEEPER-" (a HYPHEN right after GATEKEEPER) which must NOT
        # be caught by the **GATEKEEPER-prose exclusion (a real gatekeeper
        # opening is always "**GATEKEEPER " / "**GATEKEEPER:" / "**GATEKEEPER —",
        # never "GATEKEEPER-" glued on).
        rc, out, _ = run("**GATEKEEPER-ACTION:** blocked on infra access",
                          mode="gatekeeper-action")
        self.assertEqual(rc, 0)


class TestBounceResolvedMode(TestCase):
    def test_line_start_marker_matches(self):
        rc, out, _ = run("BOUNCE-RESOLVED: fixed the finding from #123",
                          mode="bounce-resolved")
        self.assertEqual(rc, 0)
        self.assertIn("bounce-resolved", out)

    def test_mid_sentence_mention_does_not_match(self):
        rc, out, _ = run("once this lands I will post BOUNCE-RESOLVED: here",
                          mode="bounce-resolved")
        self.assertEqual(rc, 1)

    def test_a_plain_readiness_comment_does_not_match_this_mode(self):
        rc, out, _ = run("READY-FOR-REVIEW: feature complete\n\n"
                          "Ready for gatekeeper cross-fork review.",
                          mode="bounce-resolved")
        self.assertEqual(rc, 1)


class TestUnknownMode(TestCase):
    def test_unknown_mode_is_a_caller_bug_not_a_match_result(self):
        rc, out, err = run("anything", mode="some-typo")
        self.assertEqual(rc, 2)
        self.assertIn("unknown mode", err)


class TestRealCorpusFixtures(TestCase):
    """Fixtures drawn verbatim (trimmed) from real odoo-erp comments
    examined for airuleset#331 -- a regression lock grounded in actual
    hand-off traffic, not just synthetic cases."""

    def test_a_real_heading_only_readiness_form_still_matches_via_the_closing_phrase(self):
        # odoo-erp#2878, comment 5220826xxx-ish -- the ticket's OWN cited
        # example ("## Ready - round 2 ...") is real, but the FULL comment
        # it belongs to also ends with the canonical closing phrase.
        body = (
            "## Ready — round 2 (gatekeeper bounce fixes)\n\n"
            "Addresses every finding from the BOUNCE review.\n\n"
            "Branch: kvaskodev:david/2878-crate-settled-distinction\n"
            "HEAD: 993460de (short form -- full sha stripped, not needed here)\n"
            "Verified-at-UTC: 2026-08-07T18:42Z\n\n"
            "Ready for gatekeeper cross-fork review.\n"
        )
        rc, out, _ = run(body)
        self.assertEqual(rc, 0)

    def test_a_line_start_gatekeeper_mention_past_line_one_does_not_exclude_a_real_marker(self):
        # F3, adversarial review of #331: the exclusion is deliberately
        # scoped to FIRST_LINE only, but no fixture covered the case that
        # PROVES the scoping matters -- a mutant swapping FIRST_LINE for
        # BODY in the exclusion grep passed the whole suite unnoticed.
        # A genuine hand-off that later quotes/references a gatekeeper
        # verdict on line >= 2 must still label.
        rc, out, _ = run("READY-FOR-REVIEW: done\n"
                          "**GATEKEEPER quoted verdict below")
        self.assertEqual(rc, 0)

    def test_a_real_gatekeeper_bounce_review_never_matches(self):
        body = (
            "## Gatekeeper review — BOUNCE\n\n"
            "Branch reviewed: kvaskodev:david/2878-crate-settled-distinction "
            "@ a245aff8 (matches declared HEAD; merges cleanly).\n"
        )
        rc, out, _ = run(body)
        self.assertEqual(rc, 1)


class TestHeadingStyleGatekeeperOpening(TestCase):
    """#340: the **GATEKEEPER-anchored exclusion misses a real markdown-
    HEADING gatekeeper opening ("## Gatekeeper review — BOUNCE", the real
    shape already locked as a fixture above from odoo-erp#2878). Live-
    reproduced against the pre-fix shipped code (post-#331 merge): a
    gatekeeper comment opening this way that also quotes/mentions a
    READY-FOR-REVIEW: line was NOT excluded -- rc=0 (bug)."""

    def test_the_ticket_s_own_repro_is_excluded(self):
        rc, out, _ = run("## Gatekeeper review — BOUNCE\n\n"
                          "READY-FOR-REVIEW: was claimed but incomplete\n")
        self.assertEqual(rc, 1)

    def test_heading_opening_excludes_a_quoted_gatekeeper_action_marker_too(self):
        rc, out, _ = run("## Gatekeeper review — BOUNCE\n\n"
                          "GATEKEEPER-ACTION: quoting for context",
                          mode="gatekeeper-action")
        self.assertEqual(rc, 1)

    def test_heading_opening_excludes_a_quoted_bounce_resolved_marker_too(self):
        rc, out, _ = run("## Gatekeeper review — BOUNCE\n\n"
                          "BOUNCE-RESOLVED: quoting for context",
                          mode="bounce-resolved")
        self.assertEqual(rc, 1)

    def test_a_deeper_heading_level_is_also_excluded(self):
        rc, out, _ = run("### Gatekeeper finding\n\n"
                          "READY-FOR-REVIEW: was claimed but incomplete\n")
        self.assertEqual(rc, 1)

    def test_a_heading_only_mentioning_gatekeeper_mid_title_is_not_excluded(self):
        # Negative control: "gatekeeper" as a topic word INSIDE a genuine
        # sub-dev heading (not the heading's own opening word, and lower-
        # case) must not be treated as the gatekeeper speaking -- otherwise
        # a real hand-off whose own heading happens to mention the word
        # would be silently swallowed.
        rc, out, _ = run("## Round 2 fixes for the gatekeeper bounce review\n\n"
                          "READY-FOR-REVIEW: done\n")
        self.assertEqual(rc, 0)

    def test_a_heading_decorated_all_caps_action_marker_still_matches(self):
        # Rejected-alternative lock: the new heading pattern matches only
        # the corpus's exact "Gatekeeper" (title case) shape, never bare
        # ALL-CAPS "GATEKEEPER" -- so a sub-dev's OWN legitimate
        # GATEKEEPER-ACTION: marker, heading-decorated per the markers'
        # own documented contract (# * _ - prefixes allowed), still works.
        rc, out, _ = run("# GATEKEEPER-ACTION: unblock the docker socket",
                          mode="gatekeeper-action")
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    main()
