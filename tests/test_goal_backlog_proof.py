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


def commands_of(template):
    """The two `gh` proof commands the template names, in backticks."""
    spans = re.findall(r"`([^`]+)`", template)
    issue = [s for s in spans if s.startswith("gh issue list")]
    run = [s for s in spans if s.startswith("gh run list")]
    return issue, run


def _output_after(message, command):
    """The first non-blank line following a line that quotes `command`.

    This is exactly what a transcript-only evaluator can do: find the command
    in the turn and read what it printed.
    """
    lines = message.splitlines()
    for i, line in enumerate(lines):
        if command in line:
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return nxt.strip().strip("`")
    return None


def backlog_empty_holds(message, template):
    """Decide (B) the way the shipped template instructs.

    Returns (holds, reason). Every ambiguity returns False — CONTINUE is the
    only safe answer, and there is no third one.
    """
    if not any(ln.strip().startswith(MARKER) for ln in message.splitlines()):
        return False, "no %s line in the turn" % MARKER

    issue_cmds, run_cmds = commands_of(template)
    if not issue_cmds or not run_cmds:
        return False, "the template names no proof commands"

    count = _output_after(message, issue_cmds[0])
    if count is None:
        return False, "no pasted output for the open-issue count"
    if count != "0":
        return False, "open-issue count reads %r, not 0" % count

    conclusion = _output_after(message, run_cmds[0])
    if conclusion is None:
        return False, "no pasted output for the branch CI conclusion"
    if conclusion != "success":
        return False, "CI conclusion reads %r, not success" % conclusion

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


def _proof_block(count="0", conclusion="success", template=None):
    issue_cmds, run_cmds = commands_of(template)
    return "```\n$ %s\n%s\n$ %s\n%s\n```\n" % (
        issue_cmds[0], count, run_cmds[0], conclusion)


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

    def test_proof_commands_print_a_single_unmistakable_token(self):
        """A bare `gh issue list` prints NOTHING when the backlog is empty —
        an empty result is not pasteable evidence. The counted form prints a
        literal 0, which is."""
        for line in goal_lines():
            issue_cmds, run_cmds = commands_of(line)
            self.assertTrue(issue_cmds, "no gh issue list command named")
            self.assertTrue(run_cmds, "no gh run list command named")
            self.assertIn("--jq", issue_cmds[0])
            self.assertIn("--jq", run_cmds[0])


class TestTheTurnThatActuallyStoppedTheLoop(TestCase):
    """Acceptance: demonstrated against the real message, not argued."""

    def setUp(self):
        self.full = goal_lines()[0]

    def test_the_real_camera_box_turn_does_not_satisfy_backlog_empty(self):
        holds, reason = backlog_empty_holds(CAMERA_BOX_TURN, self.full)
        self.assertFalse(holds, "the turn that lost 6 hours would stop again")
        self.assertIn(MARKER, reason)

    def test_an_ordinary_per_ticket_done_turn_means_continue(self):
        holds, _ = backlog_empty_holds(PER_TICKET_DONE_TURN, self.full)
        self.assertFalse(holds)

    def test_a_genuinely_empty_backlog_does_satisfy_it(self):
        turn = (_proof_block(template=self.full)
                + MARKER + " 0 open, main green\n"
                + "✅ DONE: backlog prázdny\n")
        holds, reason = backlog_empty_holds(turn, self.full)
        self.assertTrue(holds, reason)

    def test_the_marker_without_any_pasted_output_means_continue(self):
        """Claiming the new marker is no better than claiming the old one."""
        turn = MARKER + " 0 open\n✅ DONE: hotovo\n"
        holds, reason = backlog_empty_holds(turn, self.full)
        self.assertFalse(holds)
        self.assertIn("no pasted output", reason)

    def test_a_nonzero_open_count_means_continue(self):
        turn = (_proof_block(count="7", template=self.full)
                + MARKER + " 0 open\n✅ DONE: hotovo\n")
        holds, reason = backlog_empty_holds(turn, self.full)
        self.assertFalse(holds)
        self.assertIn("not 0", reason)

    def test_a_red_main_ci_means_continue(self):
        turn = (_proof_block(conclusion="failure", template=self.full)
                + MARKER + " 0 open\n✅ DONE: hotovo\n")
        holds, reason = backlog_empty_holds(turn, self.full)
        self.assertFalse(holds)
        self.assertIn("not success", reason)

    def test_the_issue_count_alone_is_not_enough(self):
        issue_cmds, _ = commands_of(self.full)
        turn = ("```\n$ %s\n0\n```\n" % issue_cmds[0]
                + MARKER + " 0 open\n✅ DONE: hotovo\n")
        holds, reason = backlog_empty_holds(turn, self.full)
        self.assertFalse(holds)
        self.assertIn("branch CI", reason)


class TestReducedAuthorityTemplatesToo(TestCase):
    """branch-merge and fork-no-merge carry the same defect and the same fix."""

    def test_both_reduced_templates_demand_the_same_marker(self):
        for line in goal_lines()[1:]:
            self.assertIn(MARKER, line)

    def test_the_camera_box_shape_fails_under_every_profile(self):
        for line in goal_lines():
            holds, _ = backlog_empty_holds(CAMERA_BOX_TURN, line)
            self.assertFalse(holds)

    def test_reduced_templates_still_scope_the_count_to_my_own_slice(self):
        for line in goal_lines()[1:]:
            issue_cmds, _ = commands_of(line)
            self.assertTrue(issue_cmds)
            self.assertIn("--assignee @me", issue_cmds[0])


if __name__ == "__main__":
    main()
