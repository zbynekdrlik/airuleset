"""#363 -- the review skill (@code-review) spirals into a disproportionate
multi-agent fan-out, is cross-ticket/cross-session addressable, and orphans
silently across a session-limit reset.

Root cause (filed from live #354 worktree experience, re-verified against
current HEAD before this fix): `agents/autopilot-worker.md` -- the ONE
always-present system prompt every autopilot-worker gets, regardless of what
an ad-hoc supervisor dispatch prompt happens to spell out -- told CYCLE step
6 to make "/review" clean with ZERO guidance on the DISPATCH MECHANISM. No
`SKILL.md` for "review"/"code-review" exists anywhere on this machine (it is
a Claude Code PLATFORM skill airuleset does not own); invoking it via
`Skill({skill: "review"})` launches a "forked execution" background agent
that inherits the calling session's full context (the same class of hazard
`subagent-continuation.md` already documents for `fork` dispatches), which
is what produced the 10-sub-agent fan-out, the cross-ticket addressability,
and -- combined with the ALREADY-DOCUMENTED async-dispatch fragility class
(`ci-monitoring.md`/`verify-launched-work-liveness.md`) -- the silent
orphaning across a session-limit reset.

The fix is airuleset-side STEERING, never a change to the built-in skill
itself (out of reach): forbid `Skill({skill: "review"})` /
`Skill({skill: "code-review"})` for CYCLE step 6, and mandate the ONE
self-contained fresh-context `general-purpose` subagent dispatch shape that
#353/#358/#359/#361/#362 already used successfully in practice (confirmed
via `gh issue view --json comments` on all five before writing this fix).

This file locks the prose fix across the three surfaces that shared the
same ambiguous "/review is a skill to run" wording: the worker's own
system prompt (`agents/autopilot-worker.md`), the supervisor-facing skill
(`skills/autopilot/SKILL.md`), and the shared completion-report rule
(`modules/core/completion-report.md`) that any served session (not just an
autopilot worker) also reads.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_MD = ROOT / "agents" / "autopilot-worker.md"
SKILL_MD = ROOT / "skills" / "autopilot" / "SKILL.md"
COMPLETION_REPORT_MD = ROOT / "modules" / "core" / "completion-report.md"

# The exact audit-line strings several other tests already lock byte-for-byte
# (test_prose_gate_undeterminable.py, test_prose_gate_retry_state.py,
# test_prose_gate_pipeline_race.py, test_goal_backlog_proof.py,
# test_design_gate.py, test_airuleset.py) -- this fix must NEVER touch them,
# it only clarifies the DISPATCH MECHANISM that satisfies them.
LOCKED_AUDIT_LINES = (
    "✅ /review: clean — 0 🔴 0 🟡 0 🔵",
    "✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵",
)

# The literal invocation shape that must never be recommended anywhere in
# these three files -- proven live (#354) to spiral into a "forked
# execution" background agent inheriting the caller's full context.
BANNED_INVOCATIONS = (
    'Skill({skill: "review"})',
    'Skill({skill: "code-review"})',
)


def _norm(text):
    """Collapse markdown line-wrapping/indentation so a phrase spanning a
    wrap still matches a substring search -- the established technique this
    repo's own tests already use for prose locks that survive re-wrapping.
    """
    return " ".join(text.split())


class TestLockedAuditLinesUntouched(unittest.TestCase):
    """The fix must never alter what the audit lines LOOK like -- only how
    a worker satisfies them. Several OTHER tests already lock these strings
    byte-for-byte across hook-facing files; re-asserting them here catches
    an accidental edit to the shared vocabulary early, in the file that
    motivated the whole review-dispatch rewrite.
    """

    def test_completion_report_still_carries_the_locked_review_audit_line(self):
        text = COMPLETION_REPORT_MD.read_text(encoding="utf-8")
        for line in LOCKED_AUDIT_LINES:
            self.assertIn(line, text,
                          "the #363 fix must never change the audit-line "
                          "wording several other tests lock byte-for-byte")


class TestAutopilotWorkerForbidsTheBuiltinReviewSkill(unittest.TestCase):
    """`agents/autopilot-worker.md` is the ONE surface every autopilot
    worker unconditionally receives as its own system prompt -- the fix has
    to land there, not merely in an ad-hoc supervisor dispatch prompt.
    """

    def setUp(self):
        self.text = WORKER_MD.read_text(encoding="utf-8")

    def test_never_mentions_the_banned_invocations_approvingly(self):
        # The banned shapes may be QUOTED as the thing being forbidden, but
        # the file must say so explicitly right next to each mention -- a
        # bare mention with no "never"/"forbidden"/"banned" nearby would
        # read as an example to imitate, not a warning.
        for shape in BANNED_INVOCATIONS:
            self.assertIn(shape, self.text,
                          "the worker prompt must name the exact banned "
                          "invocation shape %r so a worker can recognise "
                          "and avoid it" % shape)
            idx = self.text.index(shape)
            window = _norm(self.text[max(0, idx - 400):idx + 100])
            self.assertRegex(
                window,
                r"(?i)\bnever\b.*\binvoke\b|\bnever\b.*" + re.escape(shape[:20]),
                "the banned shape %r appears with no clear 'never invoke' "
                "warning nearby -- it would read as an example to follow" % shape)

    def test_cites_363_next_to_the_ban(self):
        idx = self.text.index(BANNED_INVOCATIONS[0])
        window = self.text[max(0, idx - 600):idx + 1200]
        self.assertIn("#363", window,
                       "the ban on the built-in review skill must cite #363 "
                       "so a future reader can find the incident that "
                       "motivated it")

    def test_mandates_a_single_fresh_context_general_purpose_dispatch(self):
        window = _norm(self.text)
        self.assertRegex(
            window,
            r"ONE self-contained,?\s*fresh-context `general-purpose` subagent dispatch",
            "the worker prompt must mandate the ONE proven dispatch shape "
            "(general-purpose subagent, fresh context, self-contained) as "
            "the replacement for the banned built-in skill")

    def test_cites_the_tickets_that_already_proved_the_shape_works(self):
        window = _norm(self.text)
        for n in ("#353", "#358", "#359", "#361", "#362"):
            self.assertIn(n, window,
                          "the worker prompt should cite %s as evidence the "
                          "single fresh-context dispatch shape already "
                          "worked in practice, not just assert it" % n)

    def test_explains_the_builtin_skill_is_not_airulesets_own(self):
        window = _norm(self.text)
        self.assertRegex(
            window,
            r"Claude Code PLATFORM skill|platform skill.*airuleset does not own"
            r"|airuleset does not own",
            "the worker prompt should explain WHY it steers away instead of "
            "fixing the skill: it is a Claude Code platform skill, not "
            "something this repo can alter")

    def test_worktree_mode_stop_point_also_points_at_the_ban(self):
        # #354's own incident happened INSIDE a worktree-dispatched worker --
        # the worktree-mode STOP POINT paragraph (which some workers read as
        # their own terminal instruction, never reaching CYCLE step 6's full
        # text) must not silently omit this.
        marker = "Worktree-mode STOP POINT"
        self.assertIn(marker, self.text)
        idx = self.text.index(marker)
        window = _norm(self.text[idx:idx + 1200])
        self.assertIn("#363", window,
                       "the worktree-mode STOP POINT paragraph must point "
                       "at the #363 ban too -- it is exactly the surface "
                       "the original incident ran on")


class TestAutopilotSkillDoesNotImplyLiteralSkillReruns(unittest.TestCase):
    """`skills/autopilot/SKILL.md` is the supervisor-facing side. Its own
    "Run BOTH skills" / "never re-running the skills yourself" wording used
    the same ambiguous language that misled #354's worker -- the supervisor
    doc needs its own pointer so nobody reading it assumes "/review" names
    a literal skill to invoke.
    """

    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")

    def test_worktree_fleet_mode_note_points_at_363(self):
        marker = "Worktree/fleet mode"
        self.assertIn(marker, self.text)
        idx = self.text.index(marker)
        window = _norm(self.text[idx:idx + 900])
        self.assertIn("#363", window,
                       "the worktree/fleet-mode note repeats the "
                       "'/review' + '/requesting-code-review' phrase and "
                       "must point at #363's dispatch-shape guidance")

    def test_round_completion_relay_note_points_at_363(self):
        marker = "never re-running the"
        self.assertIn(marker, self.text,
                       "expected the round-completion relay sentence to "
                       "still exist near the audit lines")
        idx = self.text.index(marker)
        window = _norm(self.text[max(0, idx - 200):idx + 400])
        self.assertIn("#363", window,
                       "the round-completion relay note ('never re-running "
                       "the ... yourself') must clarify this was never a "
                       "literal Skill re-invocation either, per #363")


class TestCompletionReportForbidsTheBuiltinReviewSkill(unittest.TestCase):
    """`modules/core/completion-report.md` is loaded by EVERY session, not
    just autopilot workers -- its own "Apply /review standards" / "Invoke
    ... skill" / "Run BOTH skills" language is the same root ambiguity and
    needed the identical clarification.
    """

    def setUp(self):
        self.text = COMPLETION_REPORT_MD.read_text(encoding="utf-8")

    def test_pre_completion_gate_forbids_the_builtin_skill(self):
        marker = "Apply `/review` standards"
        self.assertIn(marker, self.text)
        idx = self.text.index(marker)
        window = _norm(self.text[idx:idx + 700])
        self.assertIn("#363", window,
                       "the pre-completion gate's /review step must point "
                       "at #363 and forbid the built-in skill invocation")
        self.assertRegex(
            window, r"[Nn]ever invoke",
            "the pre-completion gate's /review step must explicitly say "
            "never to invoke the built-in skill")

    def test_requesting_code_review_bullet_no_longer_says_run_both_skills(self):
        marker = "MUST also pass clean"
        self.assertIn(marker, self.text)
        idx = self.text.index(marker)
        window = _norm(self.text[idx:idx + 600])
        self.assertNotIn("Run BOTH skills", window,
                          "the old 'Run BOTH skills' phrasing implied "
                          "'/review' names a literal skill to invoke -- it "
                          "must be reworded")
        self.assertIn("#363", window,
                       "the bullet explaining /review vs "
                       "/requesting-code-review must point at #363")


if __name__ == "__main__":
    unittest.main()
