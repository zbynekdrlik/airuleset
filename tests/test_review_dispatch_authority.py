"""A review subagent must be dispatched read-only (#49, 2026-07-27).

Incident (parovanie-produktov, PR #228, 2026-07-25): a worker dispatched
`general-purpose` (tool list `*`) for a task whose prompt asked for a
verification REPORT -- no commits, no push. The subagent wrote 8 assert-free
scratch probe tests into the worktree, which the parent then swept into a
commit via `git add -A`, and committed on its own.

The ticket's headline ("the subagent merged the PR") was refuted on the ticket
by the supervisor: the merge was the SUPERVISOR's, inside `pr-merge-policy.md`
authority. So `block-subagent-merge.sh` is deliberately NOT built -- and could
not work anyway: at hook level a subagent-context `git commit` / `git push` /
`gh pr merge` is indistinguishable between an authorised `autopilot-worker`
(which legitimately does all three) and an unauthorised review agent. The
authority difference exists only at DISPATCH time -- which is exactly where the
harness already enforces it deterministically, via the agent type's tool list.

So the deterministic block already existed; what was missing was the
instruction to use it. Swept before writing anything: no surface in modules/,
skills/ or agents/ carried a least-authority rule, and a live run of
`inject-situational-rule.sh` on a real Agent payload showed `least` and
`git status` absent from the 4072 chars it injects.

The fix is content on the two surfaces that reach a dispatcher, both already
wired and proven:
  * the SKILL body -- auto-injected on EVERY `Agent` dispatch (#91 surface,
    `situational-triggers.conf:42`), i.e. at the decision point itself;
  * the always-on MODULE -- because the hook injects once per session per
    topic, so a risky dispatch late in a long session is not re-served, and
    #104/#105 proved a module body reaches a dispatched subagent while a skill
    body does not.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent

MODULE = "modules/core/subagent-type-discipline.md"
SKILL = "skills/subagent-type-discipline/SKILL.md"
CONF = "hooks/situational-triggers.conf"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestSkillCarriesTheLeastAuthorityRule(TestCase):
    """The skill body is what an Agent dispatch injects -- the decision point."""

    def test_review_dispatch_names_the_read_only_type(self):
        t = read(SKILL)
        self.assertIn("LEAST tool authority", t)
        self.assertIn("Explore", t)

    def test_rule_states_that_the_prompt_is_not_enforcement(self):
        """"please only report" is what failed in #49 -- say so explicitly."""
        t = read(SKILL).lower()
        self.assertIn("prompt is not enforcement", t)

    def test_rule_covers_the_scratch_files_a_subagent_leaves_behind(self):
        t = read(SKILL)
        self.assertIn("git status", t)
        self.assertIn("git add -A", t)

    def test_rule_cites_the_incident_it_came_from(self):
        t = read(SKILL)
        self.assertIn("#49", t)


class TestAlwaysOnStubCarriesTheNonNegotiable(TestCase):
    """The hook injects once per session per topic; the module every turn."""

    def test_stub_carries_the_least_authority_clause(self):
        self.assertIn("LEAST tool authority", read(MODULE))

    def test_stub_stays_a_stub(self):
        """Restoring the whole policy here would be the #91 mistake inverted."""
        self.assertLess(len(read(MODULE).splitlines()), 12)


class TestDeliverySurfaceIsWired(TestCase):
    """Locks that the rule actually loads, rather than merely existing."""

    def test_agent_dispatch_trigger_points_at_the_skill(self):
        rows = [r for r in read(CONF).splitlines()
                if r.strip() and not r.startswith("#")]
        agent_rows = [r for r in rows if r.split("\t")[1] == "Agent"]
        self.assertTrue(agent_rows, "no Agent trigger row")
        self.assertTrue(any(SKILL in r for r in agent_rows))

    def test_injected_body_at_an_agent_dispatch_contains_the_rule(self):
        """Behavioural: run the real hook with a real review-dispatch payload."""
        payload = json.dumps({
            "session_id": "review-authority-test",
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "general-purpose",
                "prompt": "review the diff and report findings only",
            },
        })
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                ["bash", str(ROOT / "hooks" / "inject-situational-rule.sh")],
                input=payload, capture_output=True, text=True,
                env=dict(os.environ, TMPDIR=tmp),
            )
        self.assertEqual(r.returncode, 0)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("LEAST tool authority", ctx)
        self.assertIn("git add -A", ctx)


if __name__ == "__main__":
    main()
