"""(B) must be PROVEN in the turn, never claimed in prose (#159).

Overnight 2026-07-28/29 two boxes declared "Goal achieved" and stopped with
129 and 72 tickets open, losing ~6 h of unattended work. Neither hit stop
condition (A) — neither ended on an unanswered `❓ NEEDS YOU`. Both satisfied
(B) BACKLOG DONE, and both were wrong.

The `/goal` evaluator reads ONLY the conversation transcript; it cannot run
`gh`. The old (B) said the backlog was "proven by `gh issue list …` showing
none remain" — to a transcript-only reader that is a DESCRIPTION of what would
be true, not a PRECONDITION on the transcript, and nothing said what to do when
the proof was absent. So absence fell through to judgement, and judgement read
a confident completion report as completion.

The same template also MANDATES `✅ DONE:` as the per-ticket CONTINUE
terminator, so the strongest signal in the turn means "keep going" and the
evaluator had to separate it from a STOP by context alone.

The fix inverts the burden of proof: (B) is a PRESENCE test on the final turn
(a `🏁 BACKLOG EMPTY:` line plus the pasted OUTPUT of two `gh` commands whose
zero-state is a single unmistakable token), and every ambiguity resolves to
CONTINUE.

Why a text-level fix is sound here and was not in #134: the discriminating
question is which way NON-COMPLIANCE fails. In #134 the absent action produced
SILENCE. Here the absent action produces CONTINUE — the loop keeps working the
backlog. Missing evidence costs turns, never a silent stop.

The decision function below takes every discriminator FROM the shipped
template, so it cannot drift from the text it is testing.
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
SKILL = "skills/autopilot/SKILL.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def goal_lines():
    lines = re.findall(r"^/goal STOP CONDITIONS.*$", read(SKILL), re.MULTILINE)
    return lines


# --------------------------------------------------------------------------- #
# Discriminators, read out of the shipped template — never hardcoded here.
# --------------------------------------------------------------------------- #

MARKER = "🏁 BACKLOG EMPTY:"

# What each authority profile must prove, as (stable command prefix, the exact
# token its output must print). This is a SPECIFICATION, deliberately stated
# here rather than scraped: the profiles differ in what they are even able to
# prove. A fork-no-merge stream owns no branch CI, so demanding a `gh run list`
# from it would be incoherent — its second proof is that its merged work has
# actually reached origin/main.
#
# The prefix is what locates the command in a real turn; the template must
# declare the full command (verified separately), but the branch-merge one
# carries an `<integration>` placeholder the worker substitutes, so an exact
# match against the template text could never find it in the transcript.
FULL, BRANCH_MERGE, FORK_NO_MERGE = 0, 1, 2

# The reduced-authority profiles' FIRST proof is `airuleset.py slice-quals
# --count` (#181), never a hand-rolled `gh issue list --assignee @me` — a
# shared-gh-account stream box (montalu/marek/simap) makes `@me` resolve to
# the maintainer, so that key silently prints 0 with real work open.
SLICE_QUALS_COUNT = "python3 ~/devel/airuleset/airuleset.py slice-quals --count"

# The full-authority profile's FIRST proof is `airuleset.py core-quals
# --count` (#181 I4, round 2) — never a bare whole-repo `gh issue list`. The
# footer and the Discord card already scope their own counts to the CORE
# slice (#164: every sub-dev stream's own stream:<user>-labeled tickets
# excluded, since a core/gatekeeper box is forbidden from working them); a
# whole-repo stop-proof could never legitimately reach 0 while any stream
# still had open work.
CORE_QUALS_COUNT = "python3 ~/devel/airuleset/airuleset.py core-quals --count"

PROOF_SPEC = {
    FULL: [(CORE_QUALS_COUNT, "0"), ("gh run list", "success")],
    BRANCH_MERGE: [(SLICE_QUALS_COUNT, "0"), ("gh run list", "success"),
                   ("git merge-base", "RELEASED")],
    FORK_NO_MERGE: [(SLICE_QUALS_COUNT, "0"), ("git merge-base", "RELEASED")],
}


def declared_commands(template, prefix):
    """Backtick-quoted commands the template names, matching `prefix`."""
    spans = re.findall(r"`([^`]+)`", template)
    return [s for s in spans if s.startswith(prefix)]


def _output_after(message, prefix):
    """The first non-blank line following a line that quotes the command.

    This is exactly what a transcript-only evaluator can do: find the command
    in the turn and read what it printed.
    """
    lines = message.splitlines()
    for i, line in enumerate(lines):
        if prefix in line:
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return nxt.strip().strip("`")
    return None


def backlog_empty_holds(message, profile=FULL):
    """Decide (B) the way the shipped template instructs.

    Returns (holds, reason). Every ambiguity returns False — CONTINUE is the
    only safe answer, and there is no third one.
    """
    if not any(ln.strip().startswith(MARKER) for ln in message.splitlines()):
        return False, "no %s line in the turn" % MARKER

    for prefix, expected in PROOF_SPEC[profile]:
        got = _output_after(message, prefix)
        if got is None:
            return False, "no pasted output for %r" % prefix
        if got != expected:
            return False, "%r printed %r, not %r" % (prefix, got, expected)

    return True, "proven in this turn"


# --------------------------------------------------------------------------- #
# Fixtures. The camera-box turn is the REAL final assistant message, verbatim
# from the live transcript
# (~/.claude/projects/-home-newlevel-devel-camera-box/
#  90bc51f3-acd5-4a3b-bdef-5ad6364d6e1d.jsonl line 136553,
#  2026-07-28T23:32:16.620Z) — not a paraphrase of it.
#
# Note what this turn actually SAYS: it FILED a new open issue in this very
# message ("Založené ako #859") and narrates unfinished work. Even the prose
# carried the counter-evidence. It stopped anyway — which is the strongest
# possible argument that (B) must not be decided by reading prose at all.
# --------------------------------------------------------------------------- #

CAMERA_BOX_TURN = """\
**Slučka sa zavrela na rigu.** −295.12 ms → **−7.56 ms** za jeden cyklus, a A/V brána preklopila na zelenú:

| kamera | offset | mad | pass |
|---|---|---|---|
| cam1 | −6.03 ms | 17.11 | ✅ |
| cam2 | −9.08 ms | 9.07 | ✅ |
| cam3 | −0.49 ms | 13.22 | ✅ |
| cam4 | −9.21 ms | 5.68 | ✅ |

`all_cambox_av_sync.gate_pass = true` (tolerancia ±20 ms). Gate si sám zmeral rozladenie, zložil ho do jedného čísla, nasadil na stream box s overením spätným čítaním, a ďalší beh prišiel v tolerancii. Žiadna obsluha, žiadny ručný gombík. Potom spravil ešte malú dorovnávku 915 → 923 ms, čo je normálne správanie konvergujúcej slučky.

**Čo to nedokazuje:** sú to dva behy, nie séria — ukazuje to konvergenciu z ~295 ms chyby, nie stabilitu cez veľa cyklov.

**Verdikt už nepadá na A/V.** Jediný červený člen zostal continuity: **48 udalostí, všetko duplikáty, nula medzier**. Založené ako **#859**.

✅ DONE: #856 dokázané naživo — gate si rig zarovnal sám, A/V brána zelená
"""

# An ordinary per-ticket completion report: merged PR, green CI, verified
# deploy, a wall of ✅ rows, terminating in ✅ DONE. This is what EVERY
# successful ticket turn looks like. It must always mean CONTINUE.
PER_TICKET_DONE_TURN = """\
## ✅ Work Complete

✅ CI: green
✅ /review: clean — 0 🔴 0 🟡 0 🔵
✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵
✅ Deploy: v1.4.2 read from the live dashboard DOM, feature exercised

**[proj] PR #77: Fix the importer retry**
https://github.com/o/r/pull/77 — merged abc1234

✅ DONE: #76 hotové — importer už pri výpadku nepadá
"""


def _proof_block(profile=FULL, **override):
    """A turn's evidence block, built from the profile's own proof spec."""
    out = ["```"]
    for prefix, expected in PROOF_SPEC[profile]:
        out.append("$ %s ..." % prefix)
        out.append(override.get(prefix, expected))
    out.append("```")
    return "\n".join(out) + "\n"


class TestTemplatesRequireProofNotProse(TestCase):
    """Every authority profile's (B) must be a presence test, not a judgement."""

    def test_there_are_still_three_templates(self):
        self.assertEqual(len(goal_lines()), 3)

    def test_every_template_requires_the_backlog_empty_marker(self):
        for line in goal_lines():
            self.assertIn(MARKER, line)

    def test_every_template_says_done_alone_never_satisfies_it(self):
        for line in goal_lines():
            self.assertRegex(
                line, r"`✅ DONE:` NEVER satisfies \(B\)")

    def test_every_template_requires_the_output_to_be_pasted(self):
        for line in goal_lines():
            self.assertIn("pasted OUTPUT", line)

    def test_every_template_falls_back_to_continue_when_it_cannot_tell(self):
        for line in goal_lines():
            self.assertIn("CONTINUE", line)
            self.assertIn("no third answer", line)

    def test_every_template_says_how_to_tell_real_from_claimed(self):
        for line in goal_lines():
            self.assertIn("HOW TO TELL A REAL COMPLETION", line)

    def test_every_template_tells_the_worker_to_produce_the_proof(self):
        for line in goal_lines():
            self.assertIn("PRODUCE THE PROOF", line)

    def test_every_profile_declares_the_commands_its_proof_spec_needs(self):
        for profile, line in enumerate(goal_lines()):
            for prefix, _ in PROOF_SPEC[profile]:
                self.assertTrue(
                    declared_commands(line, prefix),
                    "profile %d names no %r command" % (profile, prefix))

    def test_gh_proof_commands_print_a_single_unmistakable_token(self):
        """A bare `gh issue list` prints NOTHING when the backlog is empty —
        an empty result is not pasteable evidence, which is precisely how a
        blank turn could pass for a proof. The counted `--jq` form prints a
        literal 0, which IS evidence.

        Only that a COUNTED form is declared is required; the templates also
        MENTION the bare form to explain why it is unusable, and a mention is
        not a declaration.
        """
        for profile, line in enumerate(goal_lines()):
            for prefix, _ in PROOF_SPEC[profile]:
                if not prefix.startswith("gh"):
                    continue
                self.assertTrue(
                    any("--jq" in c for c in declared_commands(line, prefix)),
                    "profile %d declares no counted %r" % (profile, prefix))

    def test_the_template_states_the_exact_token_each_proof_must_print(self):
        """Binds the tokens the decision procedure compares against to the
        shipped text, so the two cannot drift apart."""
        for profile, line in enumerate(goal_lines()):
            for _, expected in PROOF_SPEC[profile]:
                self.assertIn("printing exactly `%s`" % expected, line)

    def test_full_authority_proof_is_core_scoped_not_whole_repo(self):
        # #181 I4 (round 2): the footer/card already scope their own counts
        # to the CORE slice (#164) — a core/gatekeeper box is FORBIDDEN from
        # working sub-dev-owned stream:<user> tickets, so a whole-repo
        # stop-proof could never legitimately reach 0 while any stream
        # still had open work. The FULL template's own proof must use the
        # SAME core scope as the other two counters, not a bare
        # repo-wide count.
        line = goal_lines()[FULL]
        self.assertIn("core-quals --count", line)
        self.assertNotIn(
            '`gh issue list --state open --search "-label:autopilot-skip" '
            "--json number --jq 'length'`", line)


class TestTheTurnThatActuallyStoppedTheLoop(TestCase):
    """Acceptance: demonstrated against the real message, not argued."""

    def test_the_real_camera_box_turn_does_not_satisfy_backlog_empty(self):
        holds, reason = backlog_empty_holds(CAMERA_BOX_TURN)
        self.assertFalse(holds, "the turn that lost 6 hours would stop again")
        self.assertIn(MARKER, reason)

    def test_an_ordinary_per_ticket_done_turn_means_continue(self):
        holds, _ = backlog_empty_holds(PER_TICKET_DONE_TURN)
        self.assertFalse(holds)

    def test_a_genuinely_empty_backlog_does_satisfy_it(self):
        turn = (_proof_block()
                + MARKER + " 0 open, main green\n"
                + "✅ DONE: backlog prázdny\n")
        holds, reason = backlog_empty_holds(turn)
        self.assertTrue(holds, reason)

    def test_the_marker_without_any_pasted_output_means_continue(self):
        """Claiming the new marker is no better than claiming the old one."""
        turn = MARKER + " 0 open\n✅ DONE: hotovo\n"
        holds, reason = backlog_empty_holds(turn)
        self.assertFalse(holds)
        self.assertIn("no pasted output", reason)

    def test_a_nonzero_open_count_means_continue(self):
        turn = (_proof_block(**{CORE_QUALS_COUNT: "7"})
                + MARKER + " 0 open\n✅ DONE: hotovo\n")
        holds, reason = backlog_empty_holds(turn)
        self.assertFalse(holds)
        self.assertIn("'7'", reason)

    def test_a_red_main_ci_means_continue(self):
        turn = (_proof_block(**{"gh run list": "failure"})
                + MARKER + " 0 open\n✅ DONE: hotovo\n")
        holds, reason = backlog_empty_holds(turn)
        self.assertFalse(holds)
        self.assertIn("'failure'", reason)

    def test_the_issue_count_alone_is_not_enough(self):
        turn = ("```\n$ %s ...\n0\n```\n" % CORE_QUALS_COUNT
                + MARKER + " 0 open\n✅ DONE: hotovo\n")
        holds, reason = backlog_empty_holds(turn)
        self.assertFalse(holds)
        self.assertIn("gh run list", reason)


class TestReducedAuthorityTemplatesToo(TestCase):
    """branch-merge and fork-no-merge carry the same defect and the same fix."""

    def test_both_reduced_templates_demand_the_same_marker(self):
        for line in goal_lines()[1:]:
            self.assertIn(MARKER, line)

    def test_the_camera_box_shape_fails_under_every_profile(self):
        for profile in PROOF_SPEC:
            holds, _ = backlog_empty_holds(CAMERA_BOX_TURN, profile)
            self.assertFalse(holds)

    def test_reduced_templates_still_scope_the_count_to_my_own_slice(self):
        # #181: the scoping is `slice-quals --count` (THE one definition of
        # "my slice", shared with the footer) — never a hand-rolled
        # `--assignee @me`, which silently prints 0 on a shared-gh-account
        # stream box (montalu/marek/simap).
        for profile in (BRANCH_MERGE, FORK_NO_MERGE):
            cmds = declared_commands(goal_lines()[profile], "python3")
            self.assertTrue(cmds)
            self.assertIn("slice-quals --count", cmds[0])

    def test_assignee_at_me_is_absent_from_the_reduced_proof_commands(self):
        # #181's own suggested regression lock: the literal string that
        # caused the false stop must never reappear in the ARMED template.
        for line in goal_lines()[1:]:
            self.assertNotIn("--assignee @me", line)

    def test_a_reduced_stream_must_also_prove_the_work_was_RELEASED(self):
        """The 2026-07-20 incident: tickets closed, prod got nothing. An empty
        slice with the release still pending is review-watch, not done."""
        for profile in (BRANCH_MERGE, FORK_NO_MERGE):
            turn = (_proof_block(profile, **{"git merge-base": ""})
                    + MARKER + " 0 open\n✅ DONE: hotovo\n")
            holds, reason = backlog_empty_holds(turn, profile)
            self.assertFalse(holds)
            self.assertIn("git merge-base", reason)


class TestTheFullProofCountsWhatOnlyThisBoxCanAction(TestCase):
    """#181 round 3, CRITICAL. Round 2 made the FULL stop-proof count the
    CORE slice — the FOOTER's *display* partition — and thereby stopped
    counting the tickets a full-authority box is the ONLY one able to move.

    Live on zbynekdrlik/odoo-erp 2026-07-30: 83 open non-skip, 40 core, and
    #2396/#2377 carry `stream:montalu` AND `needs-gatekeeper`, so they are
    invisible to a core-only count while being actionable by nobody else.
    The gatekeeper closes its 40, the proof prints 0, the loop stops, and
    those tickets stay blocked on the box that just stopped.

    The three places in this skill that contradicted the round-2 proof are
    reconciled here in the same change, so SELECTION and the STOP-PROOF can
    never again disagree about what "done" means on a full-authority box."""

    def test_the_full_template_no_longer_claims_it_never_touches_stream_work(self):
        # The prose was false as written: for fork-no-merge streams the
        # maintainer MUST touch those tickets (review, merge, close), and for
        # `needs-gatekeeper` the maintainer is the ONLY one who can.
        line = goal_lines()[FULL]
        self.assertNotIn("I never touch those, their own box works them", line)

    def test_the_full_template_scopes_the_proof_to_the_obligation_set(self):
        line = goal_lines()[FULL]
        self.assertIn("OBLIGED to action", line)
        for label in ("needs-gatekeeper", "prio:bounce"):
            self.assertIn(label, line)

    def test_the_full_template_says_a_stream_ticket_is_actioned_not_implemented(self):
        # Counting a stream ticket must not turn into WORKING it — that is
        # the other half of the same reconciliation.
        line = goal_lines()[FULL]
        self.assertIn("NOT mine to implement", line)

    def test_the_full_template_finally_carries_a_review_watch_clause(self):
        # It was the only one of the three without one, so nothing kept the
        # gatekeeper's loop alive while the other side held the ball
        # (cross-stream protocol rule 4).
        line = goal_lines()[FULL]
        self.assertIn("REVIEW-WATCH", line)

    def test_the_backlog_scope_bullet_no_longer_contradicts_the_proof(self):
        # `autopilot-skip is the ONLY exclusion` read as a whole-repo
        # selection, which disagreed with a core-scoped stop-proof.
        t = read(SKILL)
        i = t.index("**Backlog scope")
        bullet = t[i:i + 1200]
        self.assertIn("needs-gatekeeper", bullet)
        self.assertIn("core-quals --list", bullet)

    def test_cross_stream_rule_four_records_the_mechanical_hold(self):
        t = read(SKILL)
        i = t.index("Neither side ever \"finishes\" while the other")
        window = t[i:i + 900]
        self.assertIn("core-quals", window)
        self.assertIn("needs-gatekeeper", window)


class TheTemplatesMustFitClaudeCodesGoalCap(TestCase):
    """A template Claude Code REFUSES protects nothing (#169).

    `/goal` caps its condition at 4000 characters and rejects anything longer
    outright — the loop is never armed, so every stop condition above is moot.
    #159 added the counted proof, the 🏁 line and the release-containment
    command to all three variants and pushed each of them past the cap in one
    commit (3158/3355/3738 -> 4903/4943/5435), which took the whole mechanism
    down on every box until a human tried to arm one and read the refusal.

    The failure is SILENT by construction: nothing in this repo issues the
    arm, so no test, hook or sweep observes the rejection. That is why the cap
    is asserted here rather than left to review — it is the only thing standing
    between a future edit and the same outage.
    """

    CAP = 4000

    def test_every_template_is_within_the_cap(self):
        over = [(i, len(line)) for i, line in enumerate(goal_lines())
                if len(line) > self.CAP]
        self.assertEqual(
            over, [],
            "templates over Claude Code's %d-char /goal cap (index, length): %r"
            % (self.CAP, over))

    def test_there_are_still_three_templates_to_check(self):
        """Guards the assertion above against silently measuring nothing."""
        self.assertEqual(len(goal_lines()), 3)


class TheTemplatesDeclareTheQuestionTimeoutEscapeHatch(TestCase):
    """#161 — condition (A)'s "NEVER continue me past an unanswered
    `❓ NEEDS YOU`" is no longer an UNCONDITIONAL permanent stop: a
    `question-timeout:` nudge (watchdog job 20, `_goal_question_park_nudge`,
    ~30 min unanswered) is the ONE sanctioned exception, and every template
    must say so — never re-print the question, never bare "continue".

    Kept as its OWN class (never folded into the cap-only test above) so a
    future edit that shrinks a template back under budget by silently
    dropping this clause fails LOUDLY here, not just on the cap."""

    def test_every_template_declares_the_escape_hatch(self):
        for i, line in enumerate(goal_lines()):
            self.assertIn("question-timeout:", line,
                          "template %d missing the #161 escape hatch" % i)

    def test_the_escape_hatch_still_forbids_a_bare_continue(self):
        for line in goal_lines():
            self.assertIn("NEVER continue me past an unanswered", line)

    def test_with_the_escape_hatch_every_template_still_fits_the_cap(self):
        # the companion check to TheTemplatesMustFitClaudeCodesGoalCap —
        # named here so a future reader sees WHY this specific addition is
        # measured, not just that some generic cap exists.
        over = [(i, len(line)) for i, line in enumerate(goal_lines())
                if len(line) > 4000]
        self.assertEqual(over, [])


if __name__ == "__main__":
    main()


class TestTheMeasurementIsStatedOnceInLabelledUnits(TestCase):
    """#181 round 4, item 7. Two documents recorded the same measurement of the
    same repo on the same date differently — and two of the numbers were not
    even the same QUANTITY: the skill's `13` was obligation-MINUS-core while
    the log's `54` was the obligation TOTAL, which is exactly how a reader
    concludes the two contradict each other.

    Locking the SHAPE rather than the values: whichever numbers are current,
    the measurement must name all three populations with their unit words, in
    one place, so `outside-core` can never again be read as `obligation`."""

    TRIPLE = re.compile(r"\d+ open non-skip / \d+ core / \d+ obligation")

    def _assert_triple(self, rel):
        """Compact on failure — never dump a 2400-line document into the
        assertion message (the repo's own lock-test trap)."""
        self.assertTrue(
            self.TRIPLE.search(read(rel)),
            "%s states no `<N> open non-skip / <N> core / <N> obligation` "
            "measurement" % rel)

    def test_the_skill_states_the_triple_in_labelled_units(self):
        self._assert_triple(SKILL)

    def test_the_autopilot_log_states_it_in_the_same_units(self):
        self._assert_triple("docs/autopilot-log.md")

    def test_the_old_unlabelled_phrasings_are_gone_from_the_skill(self):
        t = read(SKILL)
        for stale in ("83 open non-skip, 40 core", "13 tickets"):
            self.assertNotIn(
                stale, t,
                "the skill still carries the unlabelled phrasing %r" % stale)


class TestFullAuthorityTemplateCallsTheSelfCallback(TestCase):
    """#225 — the full-authority template's own STOP-CONDITIONS text is what
    survives compaction and governs every turn of an armed /goal loop, so
    that is where a session actually learns to hold the boundary open
    itself instead of just relying on the passive Stop-hook/job-14 chain.

    Scoped to FULL authority only when #225 shipped it — the two
    reduced-authority templates were within 55-85 chars of the 4000-char cap
    and needed a dedicated trim pass to find room first (#227). That pass
    reclaimed room by cutting text that was 100% duplicated across all three
    templates (the clause-A "re-prints this /goal line" parenthetical, kept
    verbatim in FULL, and the bounce-lane restatement, whose unabbreviated
    form already lives in the skill body's own Step 3.1) — never any
    enforcement-bearing text."""

    def test_every_template_calls_compact_request_self(self):
        for line in goal_lines():
            self.assertIn("compact-request --self", line)

    def test_every_template_says_to_hold_before_dispatching(self):
        for idx, line in enumerate(goal_lines()):
            marker = ("the next issue" if idx == FULL
                      else "the next assigned issue")
            self.assertIn("do NOT dispatch %s in the same turn" % marker,
                          line)
            # the new instruction must come BEFORE the existing hold, not
            # after (calling --self only makes sense before moving on)
            self.assertLess(line.index("compact-request --self"),
                            line.index("do NOT dispatch %s" % marker))


class TestClauseARearmHintIsFullAuthorityOnlyByDesign(TestCase):
    """#227 review finding 10: the ONE lock that should have guarded the
    clause-A "re-prints this /goal line" parenthetical
    (`test_goal_line_rearm_after_answer`, tests/test_airuleset.py:2114)
    checks the WHOLE skill file, not per template — so it stayed green
    while the trim pass silently dropped the hint from two of three
    templates. That trim was a deliberate, budget-driven call (verified:
    it fits back into branch-merge at 3893+93=3986 but NOT fork-no-merge
    at 3922+93=4015, over the 4000-char cap) and re-arming is
    machine-backstopped regardless (watchdog jobs 9/20 re-print/re-arm the
    template independent of this prose) — so this locks the asymmetry as
    an intentional, tested design decision instead of an accidental gap."""

    REARM_HINT = ("Claude resolves that ticket and re-prints this /goal "
                  "line if issues remain")

    def test_full_authority_carries_the_rearm_hint(self):
        self.assertIn(self.REARM_HINT, goal_lines()[FULL])

    def test_the_two_reduced_authority_templates_deliberately_do_not(self):
        for idx in (BRANCH_MERGE, FORK_NO_MERGE):
            self.assertNotIn(self.REARM_HINT, goal_lines()[idx])
