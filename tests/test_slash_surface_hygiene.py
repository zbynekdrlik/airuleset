# tests/test_slash_surface_hygiene.py — #447 slash-command surface hygiene.
#
# RED→GREEN lock for the /exit → /playbook-review mis-trigger mitigation.
# playbook-review is an agent-driven post-ticket mandate (Skill tool call
# after every ticket) the user never deliberately types — transcript sweep
# 2026-08-13: 392 model-side Skill invocations vs 3 user-typed ones — one
# proven accidental by the user's own words in the spinbike transcript
# ("to sa len omylom stlacilo namiesto /exit"), a second strongly implied
# by the real /exit typed 15 s after it (camera-box). Claude Code's
# picker executes the HIGHLIGHTED row on Enter with undocumented ranking
# and known mis-selection bugs (anthropics/claude-code #11431, #26307,
# #41828) — "exit" shares no fuzzy subsequence with "playbook-review" (no
# "x" anywhere in name or description), so the only robust mitigation is
# not being in the picker at all: `user-invocable: false` (documented
# frontmatter field, code.claude.com/docs/en/skills.md) hides the skill
# from the user picker while keeping model Skill-tool invocation. The
# post-ticket mandate machinery is untouched: stop-check-playbook-review.sh
# greps the 📔 Playbook: line in the completion report, never the command
# name (locked by tests/test_playbook.py).
import sys
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

REPO = airuleset.REPO_DIR


def _frontmatter(skill):
    """The YAML frontmatter block (between the first two --- fences)."""
    text = (REPO / "skills" / skill / "SKILL.md").read_text()
    assert text.startswith("---"), f"{skill}: SKILL.md missing frontmatter fence"
    return text.split("---", 2)[1]


class TestPlaybookReviewHiddenFromPicker(TestCase):
    def test_playbook_review_is_not_user_invocable(self):
        # The mitigation itself: hidden from the slash picker on every box,
        # so /exit can never mis-select it again regardless of how the
        # picker ranks/highlights entries.
        self.assertIn("user-invocable: false", _frontmatter("playbook-review"),
                      "playbook-review must be hidden from the slash picker "
                      "(#447 — /exit kept mis-triggering it)")

    def test_playbook_review_keeps_model_invocation(self):
        # The post-ticket mandate is a MODEL Skill call — hiding from the
        # user picker must never disable model invocation.
        self.assertNotIn("disable-model-invocation", _frontmatter("playbook-review"),
                         "playbook-review must stay model-invocable — the "
                         "post-ticket mandate runs via the Skill tool")

    def test_playbook_review_still_deploys_everywhere(self):
        # Hidden ≠ removed: the mandate applies to every stream, so the
        # skill stays in SKILL_NAMES and outside every scoping exclusion.
        self.assertIn("playbook-review", airuleset.SKILL_NAMES)
        self.assertNotIn("playbook-review", airuleset.SKILLS_MAINTAINER_ONLY)
        self.assertNotIn("playbook-review", airuleset.SKILLS_FULL_AUTHORITY_ONLY)

    def test_playbook_cleanup_stays_user_invocable(self):
        # The DELIBERATE user-facing entry point to playbook work keeps its
        # slash command — only the agent-only mandate skill hides.
        self.assertIn("user-invocable: true", _frontmatter("playbook-cleanup"))


class TestMdreviewCommandSurfaceAxis(TestCase):
    def _skill_text(self):
        return (REPO / "skills" / "mdreview" / "SKILL.md").read_text()

    def _b2_section(self):
        t = self._skill_text()
        # Anchor on the HEADING, not a bare "B2" — an earlier prose mention of
        # "B2" elsewhere in the file must not silently widen this slice
        # (review finding F2: a widened slice lets the anchors below match
        # outside the real section and the lock degrades without failing).
        self.assertIn("### B2", t, "mdreview Step B missing the B2 slash-surface sub-step")
        start = t.index("### B2")
        end = t.find("## Step C", start)
        self.assertGreater(end, start, "B2 must live inside Step B, before Step C")
        return t[start:end]

    def test_step_b_carries_the_command_surface_audit(self):
        # #447 item 2: command ergonomics + collisions + quality-command
        # coverage is a standing audit axis of every /mdreview run.
        b2 = self._b2_section()
        self.assertIn("quality-command coverage", b2)
        self.assertIn("user-invocable: false", b2)  # the hide-agent-only-skills check
        low = b2.lower()
        for needle in ("collision", "built-in"):
            self.assertIn(needle, low,
                          f"B2 audit must cover slash-command {needle} checks")

    def test_findings_route_through_step_f(self):
        # Never auto-applied: B2 verdicts go through the existing Step F
        # review-loop like every other mdreview finding.
        self.assertIn("Step F", self._b2_section(),
                      "B2 findings must route through the Step F review-loop")
