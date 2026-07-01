"""Locks the MAX-PERFORMANCE model tiering (2026-07-01).

Fable 5 (`claude-fable-5`, Mythos-class, above Opus) became available and the user's
limits reset (multiple accounts, huge token headroom). The user's explicit directive:
optimize for MAXIMUM intelligence and task-solving performance, NOT token cost. This
supersedes the 2026-06-30 `opusplan` economy split (Opus plans / Sonnet executes),
which is retained ONLY as a dormant fallback the USER can re-activate.

The new lineup: Fable 5 on every dispatch where judgment shapes the outcome (plan,
design, review, verify, EXECUTE, debug — incl. the autopilot-worker) at xhigh;
Sonnet/Haiku only for purely mechanical lookups (model tier can't improve a grep);
Opus 4.8 as the availability fallback. Redundancy stays banned — max-performance
buys depth, never N copies of the same read.

`fable` verified real before adoption: `claude-fable-5` present in the CC 2.1.198
bundle, `fable` in the Agent tool's model enum, and a live `model: "fable"` subagent
dispatch returned successfully (2026-07-01).

These assertions prove the flip landed (and the economy split is demoted, not active).
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestFable5MaxPerformanceTiering(TestCase):
    def test_model_awareness_adopts_max_performance_policy(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("claude-fable-5", t)
        self.assertIn("MAX-PERFORMANCE mode: Fable 5 everywhere judgment matters", t)
        # Execution is explicitly on Fable now, not only the bookends.
        self.assertIn('EVERY dispatch where judgment affects the outcome = Fable 5 (`model: "fable"`)', t)
        # Tie-breaker inverted from the economy split.
        self.assertIn("when in ANY doubt whether judgment touches the outcome → Fable", t)
        # Opus is the availability fallback, never a silent downgrade to Sonnet.
        self.assertIn("Opus 4.8 = the fallback tier", t)
        # Redundancy stays banned under every policy.
        self.assertIn("Redundancy is still waste, not rigor", t)

    def test_opusplan_split_is_dormant_not_active(self):
        t = read("modules/core/model-awareness.md")
        self.assertIn("Dormant fallback — the `opusplan` economy split", t)
        self.assertIn("re-activate ONLY on the user's say-so", t)
        # The old ACTIVE-policy heading must be gone.
        self.assertNotIn("Opus PLANS + REVIEWS, Sonnet 5 EXECUTES (the `opusplan` split)", t)
        self.assertNotIn("EXECUTION of settled, scoped code = Sonnet 5", t)

    def test_workflow_stage_tiering_routes_judgment_to_fable(self):
        t = read("modules/core/claude-code-tooling.md")
        self.assertIn("opts.model: 'fable'", t)
        # Judgment-on-cheap-tier is now the named tiering MISS.
        self.assertIn("putting a JUDGMENT stage (design, review, verify, implement, debug) on `sonnet`/`haiku`", t)
        # Mechanical stages stay light for latency, not cost.
        self.assertIn("extra intelligence cannot improve a lookup", t)
        # The old sonnet-executes stage routing must be gone.
        self.assertNotIn("`opts.model: 'sonnet'` (= Sonnet 5)", t)

    def test_autopilot_worker_defaults_to_fable(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("model: fable", w)
        self.assertIn("You run on Fable 5", w)
        self.assertNotIn("model: sonnet", w.split("---")[1])  # frontmatter block

    def test_autopilot_supervisor_dispatches_fable_every_ticket(self):
        s = read("skills/autopilot/SKILL.md")
        self.assertIn("Model = Fable 5 by default", s)
        # No per-ticket model triage; downgrade only on the user's economy switch.
        self.assertIn("never on your own inference of spend", s)
        self.assertNotIn("Model = Sonnet 5 by default", s)


if __name__ == "__main__":
    main()
