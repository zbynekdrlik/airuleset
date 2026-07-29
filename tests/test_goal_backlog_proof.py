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

PROOF_SPEC = {
    FULL: [("gh issue list", "0"), ("gh run list", "success")],
    BRANCH_MERGE: [("gh issue list", "0"), ("gh run list", "success"),
                   ("git merge-base", "RELEASED")],
    FORK_NO_MERGE: [("gh issue list", "0"), ("git merge-base", "RELEASED")],
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
        turn = (_proof_block(**{"gh issue list": "7"})
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
        turn = ("```\n$ gh issue list ...\n0\n```\n"
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
        for profile in (BRANCH_MERGE, FORK_NO_MERGE):
            cmds = declared_commands(goal_lines()[profile], "gh issue list")
            self.assertTrue(cmds)
            self.assertIn("--assignee @me", cmds[0])

    def test_a_reduced_stream_must_also_prove_the_work_was_RELEASED(self):
        """The 2026-07-20 incident: tickets closed, prod got nothing. An empty
        slice with the release still pending is review-watch, not done."""
        for profile in (BRANCH_MERGE, FORK_NO_MERGE):
            turn = (_proof_block(profile, **{"git merge-base": ""})
                    + MARKER + " 0 open\n✅ DONE: hotovo\n")
            holds, reason = backlog_empty_holds(turn, profile)
            self.assertFalse(holds)
            self.assertIn("git merge-base", reason)


if __name__ == "__main__":
    main()
