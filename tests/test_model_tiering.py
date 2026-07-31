"""Locks the model tiering: Fable-on-HARD auto-escalation, budget-gated (2026-07-03).

History: the 2026-07-01 MAX-PERFORMANCE policy (Fable 5 on every judgment dispatch)
burned tokens brutally and kept tripping the usage limits mid-work — the user reverted
it 2026-07-02 to plain Opus+Sonnet with Fable as a rare manual/orchestrator exception.
On 2026-07-03 the user directed the middle tier: genuinely HARD tasks (architecture /
design synthesis, hard debugging, adversarial verify of critical changes, architectural
autopilot tickets) escalate to **Fable 5 AUTOMATICALLY** — but ONLY through the
**budget gate** (`airuleset.py fable-gate`, watchdog usage-cache based), so automatic
Fable can never again drain the weekly limits and stop the user's work.

These assertions prove: the auto-escalation is documented on every surface (module,
tooling, autopilot skill, worker), the gate is MANDATORY for every automatic Fable
dispatch, defaults stay Opus/Sonnet, and Fable-everywhere stays DORMANT.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# #92 item 2 (2026-07-26): the always-on module keeps ONLY what the model must
# ACT on (the lineup, the HARD criteria, the gate protocol, the advisor SHAPE).
# The justification layer it cannot act on — pricing, benchmark citations, the
# 2026-07-01/02/03 policy history, the DORMANT Fable-everywhere mode, and the
# hook-internals narrative — moved VERBATIM to the `fable-advisor` skill, which
# is where a session reasoning about tiers/costs actually loads it. Every lock
# below that pinned moved text now pins it at its NEW home; nothing was dropped.
ADVISOR = "skills/fable-advisor/SKILL.md"


class TestFableOnHardBudgetGated(TestCase):
    def test_model_awareness_active_policy_is_fable_on_hard(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn(
            "Fable 5 AUTO-escalates on genuinely HARD tasks, budget-gated (ACTIVE policy, 2026-07-03)", t)
        self.assertIn("reverted the 2026-07-01", read(ADVISOR))   # history preserved (moved home)
        self.assertIn('EXECUTION of settled, scoped code = Sonnet 5 (`model: "sonnet"`)', t)

    def test_hard_criteria_are_enumerated(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("HARD-task AUTO-escalation = Fable 5", t)
        # The four criteria families:
        self.assertIn("Architecture / design / synthesis of a genuinely COMPLEX or cross-cutting", t)
        # "multi-file" must NOT be a standalone sufficient condition (most features
        # are multi-file — that disjunct would escalate routine design work):
        self.assertIn('"Multi-file" alone is NOT the bar', t)
        self.assertIn("Hard debugging", t)
        self.assertIn("Adversarial final review / verify of a safety-critical", t)
        self.assertIn("autopilot ticket that is architectural / cross-cutting", t)
        # Routine work explicitly excluded + doubt resolves DOWN:
        self.assertIn("Routine work is NOT hard", t)
        self.assertIn("When unsure whether a task is hard → it is NOT; use Opus.", t)

    def test_gate_is_mandatory_for_automatic_fable(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("airuleset.py fable-gate", t)
        self.assertIn("Never skip the gate for an automatic escalation", t)
        # Gate semantics: once per hard task, CLOSED → opus, fail-safe on stale cache.
        self.assertIn("run the gate ONCE per hard task/batch", t)
        self.assertIn("missing/stale cache = CLOSED", t)

    def test_execution_never_escalates_to_fable(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("Execution does NOT escalate to Fable", t)

    def test_fable_everywhere_is_dormant_not_active(self):
        # The dormant policy is a RE-ACTIVATION switch only the user may flip —
        # nothing to act on every turn, so it lives in the advisor skill now.
        # The always-on module must NOT re-describe it (that was the point of
        # the move); the DORMANT record itself must survive verbatim.
        t = read("modules/core/model-awareness.md")
        a = read(ADVISOR)
        self.assertIn("Dormant — the Fable-everywhere MAX-PERFORMANCE mode", a)
        self.assertIn("re-activate ONLY on the user's explicit say-so", a)
        for txt in (t, a):
            self.assertNotIn("MAX-PERFORMANCE mode: Fable 5 everywhere judgment matters (ACTIVE", txt)
            self.assertNotIn("EVERY dispatch where judgment affects the outcome = Fable 5", txt)

    def test_workflow_stage_tiering_gates_fable_stages(self):
        t = read("modules/core/claude-code-tooling.md")
        self.assertIn("EXECUTION stages", t)
        self.assertIn("`opts.model: 'sonnet'` (= Sonnet 5)", t)
        # Hard judgment stages may take fable — but only pre-gated by the orchestrator
        # (Workflow scripts cannot exec, so the gate runs BEFORE authoring).
        self.assertIn("`opts.model: 'fable'` for the genuinely HARD judgment stages", t)
        self.assertIn("ONLY when the budget gate is OPEN", t)
        self.assertIn("BEFORE authoring the script", t)
        self.assertIn("never bake in an ungated Fable stage", t)

    def test_autopilot_worker_defaults_to_sonnet(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("model: sonnet", w.split("---")[1])   # frontmatter block
        self.assertIn("You run on Sonnet 5", w)
        self.assertNotIn("model: fable", w.split("---")[1])

    def test_autopilot_supervisor_escalates_hard_tickets_through_gate(self):
        s = read("skills/autopilot/SKILL.md")
        self.assertIn("Model = Sonnet 5 by default", s)
        self.assertIn("Fable through the budget gate", s)
        self.assertIn("fable-gate", s)
        self.assertIn("gate OPEN (exit 0) → dispatch `model: fable`", s)
        self.assertIn("gate CLOSED (exit 1)", s)
        # No ungated automatic fable dispatch:
        self.assertIn("Never dispatch an automatic `model: fable` without the gate check", s)
        self.assertNotIn("Model = Fable 5 by default", s)

    def test_worker_mid_ticket_hard_wall_climbs_opus_first(self):
        # A Sonnet worker's "first real attempt" is a SONNET attempt — ordinary bugs
        # clear that bar constantly. The ladder is Sonnet → OPUS → (gate) → Fable,
        # never Sonnet → Fable, and the tie-breaker must be present on this surface.
        w = read("agents/autopilot-worker.md")
        self.assertIn("fable-gate", w)
        self.assertIn("HARD wall mid-ticket", w)
        self.assertIn('FIRST at `model: "opus"`', w)
        self.assertIn("Opus rung comes\nBEFORE Fable", w.replace("\r\n", "\n"))
        self.assertIn("When unsure whether it is HARD → it is NOT; use Opus.", w)



class TestFableAdvisorShape(TestCase):
    """Locks the 2026-07-08 refinement: every automatic Fable escalation is an
    ADVISOR call (digest in, decision out, ONE call), never a worker — the form
    that keeps Fable-grade judgment without the 2026-07-01 Max-plan burn. Added
    when the user hit Opus-4.8 decision loops ("točíme sa dokolečka") and asked
    for the community Fable-orchestrator + Sonnet-executor pattern WITHOUT the
    token burn Fable-as-main caused."""

    def test_opus_circling_is_a_hard_criterion(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("Opus is CIRCLING", t)
        self.assertIn("third identical Opus lap", t)
        # the valve keys on observed behavior, not on degradation rumors
        self.assertIn("OBSERVED circling", t)

    def test_fable_escalation_is_advisor_shaped(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("SHAPE — Fable escalation = ADVISOR, never worker", t)
        self.assertIn("TIGHT digest", t)
        self.assertIn("ONE Fable call", t)
        self.assertIn("never dispatched as a long-lived worker session", t)
        # the main-session burn mechanism is documented so it never repeats
        self.assertIn("re-reads the full conversation context every turn", t)

    def test_workflow_fable_stages_are_advisor_shaped(self):
        t = read("modules/core/claude-code-tooling.md")
        self.assertIn("ADVISOR call: digest in, decision out", t)
        self.assertIn("grounds itself by re-reading the sources", t)


if __name__ == "__main__":
    main()


class TestSubdevReviewIsFableGated(TestCase):
    """User directive 2026-07-24: the gatekeeper's deep review of sub-dev
    submitted PRs/tickets/implementations is where MAXIMUM scrutiny belongs —
    the review/verdict stages run FABLE through the budget gate (CLOSED →
    Opus, never lower), grounding stays on cheap stages, and the tier never
    degrades across re-review iterations. Before this, /process-subdev named
    NO model at all — reviews silently ran on whatever the session/agent
    defaulted to."""

    def test_process_subdev_pins_the_review_tier(self):
        txt = (ROOT / "skills" / "process-subdev" / "SKILL.md").read_text()
        self.assertIn("fable-gate", txt)
        self.assertIn("model: fable", txt)
        self.assertIn("xhigh", txt)
        self.assertIn("never degrades", txt)
        # gate CLOSED → opus, never a cheaper tier for the verdict
        self.assertRegex(txt, r"CLOSED[^\n]*opus")

    def test_model_awareness_names_the_criterion(self):
        txt = (ROOT / "modules" / "core" / "model-awareness.md").read_text()
        self.assertIn("sub-dev", txt)
        self.assertRegex(txt, r"(?i)sub-dev[^\n]*(hand-off|review|submission)")

    def test_cross_stream_protocol_carries_the_tier(self):
        txt = (ROOT / "skills" / "autopilot" / "SKILL.md").read_text()
        self.assertRegex(txt, r"(?i)review[^\n]*fable[^\n]*gate|fable[^\n]*gate[^\n]*review")


class TestOpus5EraLineup(TestCase):
    """Locks the 2026-07-25 cost-fix rewrite: Opus 5 is the new default main +
    judgment tier (measured within 0.5% of Fable 5 on CursorBench 3.2 at HALF
    the price — https://www.anthropic.com/news/claude-opus-5). Fable 5 is NOT
    banned as a matter of principle: Fable-as-main was a deliberate WORKAROUND
    for the Opus 4.8 regression + Sonnet 5's coordinator gap, and Opus 5's
    arrival retires that workaround — closing the exact 76%-of-$13,600 burn
    the Fable-as-main workaround caused, without forbidding the user's own
    manual `/model` choice ("that's their call", unchanged). History
    (2026-07-01/02/03, the Opus-4.8-circling valve, the 2026-07-08 SHAPE
    refinement, the 2026-07-24 sub-dev review tier) must survive verbatim —
    this class only locks the NEW content. Ultracode stays ON by default
    (never made opt-in) — no assertion here touches it."""

    def test_main_session_default_recommendation_is_opus_5_not_a_fable_ban(self):
        t = read("modules/core/model-awareness.md")
        a = read(ADVISOR)
        # the pre-existing anchor phrase must survive byte-for-byte — this one
        # is ACTIONABLE (it governs every session's model handling), stays inline
        self.assertIn("MAIN interactive session runs whatever the user set via `/model`", t)
        # 2026-07-31 policy reversal (user-directed): the once-per-session
        # "recommend Opus 5" nudge is RETIRED — main is NEVER re-recommended
        # against, in either direction. The literal phrase "recommend Opus 5"
        # still survives (it names the retired line), so this stays a valid
        # substring check; the governing sentence is pinned separately below.
        self.assertIn("NEVER recommend switching it, in either direction", t)
        self.assertIn("recommend Opus 5", t)
        # WHY Opus 5 replaced the Fable-as-main workaround is justification, not
        # an instruction — it moved with the rest of the history.
        self.assertIn("Opus 5 retires that workaround", a)
        self.assertIn("Sonnet 5 could not reliably carry the coordinator role", a)
        # 2026-07-31: never a ban in EITHER direction — pins the new sentence
        # that replaced the retired "This is NOT a ban on Fable as main" line
        self.assertIn("their choice is final and never re-recommended against", t)
        for txt in (t, a):
            self.assertNotIn("Fable 5 is BANNED", txt)
            self.assertNotIn("Fable is forbidden", txt)
        # the manual-choice clause must survive verbatim (never gated)
        self.assertIn("user's own manual `/model` Fable is not gated", t)

    def test_opus_5_is_the_default_tier_with_cursorbench_citation(self):
        t = read("modules/core/model-awareness.md")
        a = read(ADVISOR)
        self.assertIn("**Opus 5**", t)
        self.assertIn("`claude-opus-5`", t)
        for txt in (t, a):
            self.assertNotIn("claude-opus-4-8", txt)
        # the benchmark citation justifies the tier; it is read when reasoning
        # about tiers/costs, not on every turn
        self.assertIn("within 0.5% of Fable 5 on CursorBench 3.2", a)
        self.assertIn("HALF the price", a)
        self.assertIn("https://www.anthropic.com/news/claude-opus-5", a)

    def test_thinking_defaults_and_fable_cannot_disable_it(self):
        a = read(ADVISOR)
        self.assertIn("Opus 5 ships thinking ON by default", a)
        self.assertIn("a change from 4.8, where it was opt-in", a)
        self.assertIn("Fable 5 cannot disable thinking at all", a)
        self.assertIn("structurally higher than Opus", a)

    def test_pricing_is_current(self):
        a = read(ADVISOR)
        self.assertIn("Fable 5 $10/$50", a)
        self.assertIn("Opus 5 $5/$25", a)
        self.assertIn("cache read $0.50, cache write $6.25 5-min / $10 1-hour", a)
        self.assertIn("Sonnet 5 $2/$10", a)
        self.assertIn("Haiku 4.5 $1/$5", a)

    def test_historical_incident_sections_survive_unchanged(self):
        # July-2026 community reports about Opus 4.8 degradation are a dated
        # historical record, NOT a stale current-tier reference — must stay
        # (at the advisor skill, which is the tier/cost reference surface).
        a = read(ADVISOR)
        self.assertIn("community reports of Opus 4.8 degradation", a)
        self.assertIn("no 4.8 model regression so far", a)
        self.assertIn("2026-07-01 Fable-everywhere mode burned tokens brutally", a)
        # the ACTIONABLE half of criterion 5 stays inline: the valve keys on
        # observed circling, never on assuming the model is broken
        t = read("modules/core/model-awareness.md")
        self.assertIn("OBSERVED circling", t)

    def test_shape_paragraph_cites_the_measured_burn(self):
        t = read("modules/core/model-awareness.md")
        a = read(ADVISOR)
        # the existing SHAPE anchors must survive INLINE — the shape is what a
        # dispatching session has to act on
        self.assertIn("SHAPE — Fable escalation = ADVISOR, never worker", t)
        self.assertIn("re-reads the full conversation context every turn", t)
        # the measured-evidence sentence is the JUSTIFICATION for that shape
        # (the deferred session-context-cost.md module is OUT of scope)
        self.assertIn(
            "Fable running as MAIN (not advisor) accounted for 76% of a ~$13,600 token spend", a)
        for txt in (t, a):
            self.assertNotIn("session-context-cost.md", txt)

    def test_header_says_opus_5(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("Model tiering — Opus 5 + Sonnet 5 default", t)
        self.assertIn("Opus 5 / Fable 5 behavior (primary + escalation)", t)
