"""Locks the fleet-dispatch concurrency/rate-limit doctrine added by #332.

Incident (2026-08-08, this repo's own dogfooding): the #317/#325 worker-only
cap ("3-5 parallel workers") never bounded the read-only `ticket-validator`
dispatches Step 1b fires per batch member -- both draw from the SAME
server-side rate limit. Kolo 2 (4 workers, no validator burst) ran clean;
Kolo 3 (5 workers + 13 concurrent validators = 18 total agents) had 3 killed
by a server-side rate limit within minutes. #332 adds: a TOTAL
concurrent-agent cap (workers + validators + any helper subagent combined)
of 8, staggered into waves above that; a dead-validator-never-blocks-the-
round rule; a worktree-mode dead-worker branch-resume protocol; and a
"when fleet dispatch pays off" doctrine paragraph for long-CI repos.

Deliberately prose-only (FREEZE: no new hook/watchdog job) -- these are grep
locks on `skills/autopilot/SKILL.md` and `skills/autopilot-master/SKILL.md`,
using the SAME window+normalize technique `tests/test_bounce_lane.py`'s
`TestBounceMeansOneThingInAllThreeHomes._window()` already established,
because this repo's own playbook documents (repeatedly) that a markdown
line-wrap silently breaks a naive `assertIn` on freshly-added prose.
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
AUTOPILOT = "skills/autopilot/SKILL.md"
MASTER = "skills/autopilot-master/SKILL.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def window(text, start, end):
    """Whitespace-collapsed slice between two anchors -- a sentence hard-
    wrapped by markdown across two lines is still the same statement, and
    an assertion that fails on the wrap instead of the claim locks nothing
    (see the repo's own playbook: this exact trap has recurred at least
    three times in this file's history, #307/#317/#325)."""
    i = text.index(start)
    j = text.index(end, i)
    return " ".join(text[i:j].split())


class TestTotalConcurrentAgentCap(TestCase):
    """The cap covers workers + validators combined, not workers alone."""

    def test_the_cap_section_exists_and_states_eight(self):
        t = read(AUTOPILOT)
        i = t.index("**Total concurrent agent cap")
        w = window(t, "**Total concurrent agent cap", "**Serial fallback")
        self.assertIn("at 8.", w)
        self.assertGreater(i, t.index("**Concurrency cap + serialize-on-overlap"),
                            "the total cap must follow the worker-only cap, "
                            "not precede it")

    def test_the_cap_cites_the_real_measured_incident(self):
        w = window(read(AUTOPILOT), "**Total concurrent agent cap",
                   "**Serial fallback")
        self.assertIn("18 total agents", w)
        self.assertIn("3 of them", w)
        self.assertIn("rate limit", w.lower())
        self.assertIn("2026-08-08", w)

    def test_the_cap_names_stagger_into_waves(self):
        w = window(read(AUTOPILOT), "**Total concurrent agent cap",
                   "**Serial fallback")
        self.assertIn("waves", w.lower())

    def test_the_cap_covers_validators_not_just_workers(self):
        w = window(read(AUTOPILOT), "**Total concurrent agent cap",
                   "**Serial fallback")
        self.assertIn("validator", w.lower())
        self.assertIn("workers", w.lower())


class TestStep1b_WaveDispatchAndDeadValidatorNeverBlocks(TestCase):
    def test_step_1b_points_at_the_total_cap(self):
        t = read(AUTOPILOT)
        w = window(t, "1b. **VALIDATE EACH batch member FIRST",
                   "Branch")
        self.assertIn("TOTAL concurrent agent cap", w)
        self.assertIn("staggered", w.lower())

    def test_a_dead_validator_is_never_re_dispatched_or_blocking(self):
        t = read(AUTOPILOT)
        w = window(t, "1b. **VALIDATE EACH batch member FIRST",
                   "Branch")
        self.assertIn("NEVER re-dispatched", w)
        self.assertIn("NEVER blocks the round", w)
        self.assertIn("Step 0", w)


class TestWorktreeDeadWorkerBranchResume(TestCase):
    def test_the_paragraph_exists_right_after_the_sendmessage_callout(self):
        t = read(AUTOPILOT)
        i = t.index("Worktree/fleet mode: a dead worker's branch is NOT "
                    "self-discovering")
        j = t.index("Prefer durable-state resumption over `SendMessage`")
        self.assertGreater(i, j, "the worktree addendum must follow the "
                            "existing serial-mode resumption callout")

    def test_it_names_the_stray_branch_discovery_command(self):
        t = read(AUTOPILOT)
        w = window(t, "Worktree/fleet mode: a dead worker's branch",
                   "Step 4 integration simply waits")
        self.assertIn("git branch --list", w)
        self.assertIn("worktree-agent-*", w)

    def test_it_says_commits_are_named_explicitly_to_the_replacement(self):
        t = read(AUTOPILOT)
        w = window(t, "Worktree/fleet mode: a dead worker's branch",
                   "Step 4 integration simply waits")
        self.assertIn("Resume from existing branch", w)
        self.assertIn("nothing lost, nothing duplicated", w)

    def test_the_serial_mode_death_rule_now_names_rate_limit_too(self):
        t = read(AUTOPILOT)
        w = window(t, "It continues from there instead of redoing",
                   "Worktree/fleet mode: a dead worker's branch")
        self.assertIn("rate limit", w.lower())


class TestFleetPaysOffDoctrine(TestCase):
    def test_the_paragraph_follows_the_repo_flow_policy(self):
        t = read(AUTOPILOT)
        i = t.index("**When fleet dispatch pays off")
        j = t.index("**Repo-flow policy")
        self.assertGreater(i, j)

    def test_it_states_the_ci_cost_is_paid_once_either_way(self):
        w = window(read(AUTOPILOT), "**When fleet dispatch pays off",
                   "1. **Per round SLOT")
        self.assertIn("paid exactly once", w)
        self.assertIn("ci-monitoring.md", w)

    def test_it_names_the_one_real_dont_bother_case(self):
        w = window(read(AUTOPILOT), "**When fleet dispatch pays off",
                   "1. **Per round SLOT")
        self.assertIn("single workable candidate", w)


class TestAutopilotMasterPointsAtTheCanonicalHome(TestCase):
    """autopilot-master's own rule: 'never re-derive or fork their content
    here' -- so this file gets a POINTER, not a duplicated explanation."""

    def test_the_collision_guards_bullet_points_at_the_autopilot_skill(self):
        t = read(MASTER)
        w = window(t, "**Collision guards:**", "Single-lane commands")
        self.assertIn("8", w)
        self.assertIn("Total concurrent agent cap", w)
        self.assertIn("never re-derive it here", w)

    def test_the_goal_master_loop_template_is_completely_untouched(self):
        """Regression guard for the design's own rejected alternative: the
        `/goal MASTER LOOP` line must still carry its ORIGINAL "capped 3-5"
        wording verbatim -- if this fails, someone edited the capped
        template instead of the prose body, which is exactly what #332's
        design comment rejected (measured headroom + the #169 precedent)."""
        t = read(MASTER)
        lines = re.findall(r"^/goal MASTER LOOP.*$", t, re.MULTILINE)
        self.assertEqual(len(lines), 1)
        self.assertIn("capped 3-5", lines[0])
        self.assertLessEqual(len(lines[0]), 4000)


class TestAccountWideCapScopeNotPerRound(TestCase):
    """Review finding (#332, MAJOR-1): the cap's ROOT CAUSE is stated as
    account-wide, but the original RULE sentence said "cap a round's TOTAL" --
    read literally, a master loop running LANE 1 (process-subdev review
    dispatches) alongside LANE 3 (an 8-agent round), or several autopilot
    instances on different repos under the SAME account, could each stay
    individually "8-compliant" while recreating the measured-fatal 18-agent
    regime account-wide. The rule sentence itself must say the cap applies
    across everything concurrently running, not per round."""

    def test_the_rule_sentence_says_account_wide_not_per_round(self):
        w = window(read(AUTOPILOT), "**Total concurrent agent cap",
                   "**Serial fallback")
        self.assertIn("at 8.", w)
        self.assertIn("ACCOUNT-WIDE", w)
        self.assertIn("never", w.lower())
        self.assertIn("per round", w.lower())

    def test_master_collision_guards_says_across_lanes_and_repos_not_per_round(self):
        w = window(read(MASTER), "**Collision guards:**", "Single-lane commands")
        self.assertIn("8", w)
        self.assertIn("not per round", w.lower())


class TestDeadWorkerBranchMappingIsFollowable(TestCase):
    """Review finding (#332, MAJOR-2): the bare `git branch --list
    'worktree-agent-*'` / `git log --all --grep` recipe returns unmapped
    candidates on a real repo (67 stray branches, no issue in the name;
    `git log` prints no branch names at all) -- the recipe must include the
    actual mapping step."""

    def test_it_names_the_contains_mapping_step(self):
        t = read(AUTOPILOT)
        w = window(t, "Worktree/fleet mode: a dead worker's branch",
                   "Step 4 integration simply waits")
        self.assertIn("git branch --contains", w)

    def test_it_names_the_deterministic_agentid_branch_convention(self):
        t = read(AUTOPILOT)
        w = window(t, "Worktree/fleet mode: a dead worker's branch",
                   "Step 4 integration simply waits")
        self.assertIn("worktree-agent-<agentId>", w)

    def test_it_warns_against_guessing_from_the_bare_list(self):
        t = read(AUTOPILOT)
        w = window(t, "Worktree/fleet mode: a dead worker's branch",
                   "Step 4 integration simply waits")
        self.assertIn("do not guess", w.lower())


class TestMeasurementClaimsAreAccurate(TestCase):
    """Review findings (#332, MINOR/TRIGGERED): the doctrine's own
    measurement claims must match what issue #332 actually recorded --
    Kolo 2 had a (benign, resolved) merge conflict, not "zero issues"; only
    4 workers is a confirmed-clean data point, 5 workers only ever ran
    combined with the failing validator burst."""

    def test_kolo_2_claim_does_not_overstate_zero_issues(self):
        w = window(read(AUTOPILOT), "**Total concurrent agent cap", "**Serial fallback")
        self.assertIn("no rate-limit kills", w.lower())
        self.assertNotIn("ran clean with zero issues", w)

    def test_the_five_worker_band_is_not_claimed_clean(self):
        w = window(read(AUTOPILOT), "**Total concurrent agent cap", "**Serial fallback")
        self.assertNotIn("(4–5 workers, no validator burst)", w)
        self.assertNotIn("(4-5 workers, no validator burst)", w)


class TheGoalTemplatesStillFitTheCap(TestCase):
    """Companion to `tests/test_goal_backlog_proof.py`'s own cap lock --
    re-asserted here, scoped to THIS ticket's own claim that it touched no
    template string at all."""

    CAP = 4000

    def test_every_stop_conditions_variant_is_still_within_the_cap(self):
        t = read(AUTOPILOT)
        lines = re.findall(r"^/goal STOP CONDITIONS.*$", t, re.MULTILINE)
        self.assertEqual(len(lines), 3)
        over = [(i, len(line)) for i, line in enumerate(lines)
                if len(line) > self.CAP]
        self.assertEqual(over, [])


if __name__ == "__main__":
    main()
