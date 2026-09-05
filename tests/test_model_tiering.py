"""Locks the model tiering: Opus 5 BANNED; PER-PHASE Fable design + review.

History: 2026-07-03 middle tier (Opus 5 + Sonnet 5 default) -> 2026-08-13
(Opus 5 banned outright, gated Fable the default judgment layer) ->
2026-08-14 refinement #455 (Fable narrowed to HARD-only after the
inherited-Fable burn; Opus 4.6 default) -> 2026-08-25 revision #690 (the
HARD-only boundary produced ZERO Fable subagent dispatches, so it moved to
"Fable for every judgment-content task + airuleset Fable-MAJORITY + ~50%
fleet target") -> **2026-08-26 revision #715**: that whole-worker Fable +
~50% + airuleset-majority burned subscriptions (Fable weekly 89-93%), so the
boundary moves AGAIN, to a PER-PHASE split, FLEET-WIDE. The lineup these
assertions lock:

  main session managed default = Fable 5 (claude-fable-5-1[1m], MANAGED_MODEL)
  design + review PHASES       = Fable 5 through the budget gate, FLEET-WIDE
                                 (the JUDGMENT-CONTENT test is now a PHASE
                                 selector: a non-trivial ticket gets a Fable
                                 DESIGN phase + a Fable REVIEW phase; tie-break
                                 unsure -> it DOES -> gets those phases)
  implementation (the work)    = Sonnet 5 default for a SETTLED-DESIGN ticket
                                 (dispatched model:"sonnet"); Opus 4.6 pinned
                                 worker on complexity (multi-component /
                                 concurrency / security-boundary / hard-debug /
                                 prior-Sonnet-failure), the frontmatter pin
                                 reached AS-IS; Sonnet 5 also for mechanical --
                                 the implementing worker NEVER carries a
                                 model:"fable" override, on ANY repo (#721
                                 refined the default; #715 the per-phase split)
  routine fallback / CLOSED    = Opus 4.6 via agent-definition frontmatter /
                                 Workflow opts.model full id / inheritance
  mechanical / read-only       = sonnet low (haiku most-trivial) -- unchanged
  airuleset-majority exception = ABOLISHED (2026-08-26): the per-phase split
                                 is fleet-wide, airuleset is not special
  Opus 5 (claude-opus-5, and the bare `opus` alias that resolves to it)
                               = BANNED on every dispatch surface (grep-gated)

The budget gate (airuleset.py fable-gate) guards EVERY automatic Fable
dispatch; its default threshold is 90 (raised from 80 by #690 so the new
usage level actually passes -- an 80% gate would dead-letter the policy
mid-week); fail-safe CLOSED on missing/stale cache is UNCHANGED. CLOSED
falls back to Opus 4.6, never lower and never the banned Opus 5. The
2026-07-01/02/03 history and the 2026-07-25 Opus-5-era records stay
preserved VERBATIM in the fable-advisor skill -- locked below too.
"""

import re
from pathlib import Path
from unittest import TestCase, main

from watchdog.usage import FABLE_GATE_PCT

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


ADVISOR = "skills/fable-advisor/SKILL.md"
# #859 batch 4a: model-awareness re-tiered — stub + companion = the full
# governance text these locks assert on (the stub alone is enforcement-core).
MODULE_STUB = "modules/core/model-awareness.md"
MODULE_DEEP = "skills/model-awareness-deep/DEEP.md"
MODULE = MODULE_STUB  # back-compat alias; tests that need the full text read both
TOOLING = "modules/core/claude-code-tooling.md"
TOOLING_WF = "skills/claude-code-workflows/DEEP.md"  # #859 batch 3: Workflow detail


def _full_module():
    """Return stub + companion combined (the full governance text)."""
    return read(MODULE_STUB) + "\n" + read(MODULE_DEEP)


class TestOpus5BanLineup(TestCase):
    """The 2026-08-13 directive bans Opus 5 everywhere and rewrites the
    lineup -- these lock the NEW ACTIVE policy in the always-on module."""

    @staticmethod
    def _impl_bullet_line(t):
        """The #721 IMPLEMENTATION bullet is ONE unwrapped physical markdown
        line -- return it so escalation-criteria/default assertions can be
        anchored to it (real teeth vs a partial revert), never satisfied by a
        pre-existing occurrence of the same token elsewhere in the module."""
        for ln in t.splitlines():
            if ln.startswith(
                    "- **IMPLEMENTATION (the actual work) = Sonnet 5 by DEFAULT"):
                return ln
        return ""

    def test_model_awareness_active_policy_header(self):
        t = _full_module()
        # #715 revision (2026-08-26): PER-PHASE tiering, FLEET-WIDE — the
        # design + review PHASES run gated Fable, the implementation worker
        # runs Opus 4.6 / Sonnet 5 and NEVER Fable. Header asserted as
        # single-line fragments (the wrap-trap this file documents).
        self.assertIn(
            "Model tiering — PER-PHASE (fleet-wide): design + review = Fable "
            "(gated); implementation = Sonnet 5 default / Opus 4.6 on complexity", t)
        self.assertIn(
            "Opus 5 BANNED (ACTIVE policy, 2026-08-26 — revises 2026-08-25 "
            "Fable-majority)", t)
        # the 2026-08-25 header (Fable judgment-content + airuleset-majority)
        # is retired as the LIVE header:
        self.assertNotIn(
            "Model tiering — Fable 5 for judgment-content work; "
            "airuleset subagents Fable-MAJORITY; Opus 4.6 routine fallback", t)
        # the 2026-08-14 header (Fable HARD-only) stays retired:
        self.assertNotIn(
            "Model tiering — Opus 4.6 default; Fable 5 for HARD work only; "
            "Sonnet 5 for light work", t)
        # the 2026-08-13 header (Fable as the default judgment tier) stays retired:
        self.assertNotIn(
            "Model tiering — Fable 5 judgment + Opus 4.6 execution; "
            "Opus 5 BANNED (ACTIVE policy, 2026-08-13 — replaces 2026-07-03)", t)
        self.assertNotIn("Opus 5 + Sonnet 5 default; Fable 5 AUTO-escalates", t)

    def test_directive_recorded_verbatim(self):
        # #859: verbatim quotes moved to the history file; check the union
        t = _full_module() + "\n" + read(".claude/rules-reference/model-awareness-history.md")
        # 2026-08-13 directives stay as history:
        self.assertIn("intenet je plny obrovskej nespokojnosti s opus 5", t)
        self.assertIn("hlavne nie ze pouzijes sonnet na zlozite veci", t)
        # #455 -- the two 2026-08-14 refinement directives, verbatim:
        self.assertIn(
            "len tazke ulohy isli na fable ostatok na opus4.8 a moze byt "
            "aj sonnet 5 vyuzivany", t)
        self.assertIn(
            "len ja dlhodobo pouzivam hlavny agent fable lebo potrebujem "
            "aby ten co komunikuje so mnou bol vysoko inteligentny", t)
        # #690 -- the 2026-08-25 revision directive, verbatim (single-line
        # fragments of the one physical quote line):
        self.assertIn(
            "chcem aby sa ta hranica posunula aby sa pouzival fable ovela viac", t)
        self.assertIn("na airuleset chcem aby sa majoritne pouzival fable", t)
        # #715 -- the 2026-08-26 per-phase directive, verbatim:
        self.assertIn(
            "Najviacej by sa mi pacilo keby sa tickety nejak tak robili ze "
            "brainstorming, specs a plan fable, implementacia opus4.8/sonnet 5, "
            "review fable", t)
        self.assertIn(
            "len tie dolezite fazy ktore vyzaduju veci dobre vymysliet a "
            "skontrolovat no samotna praca by bol nizsi model", t)
        # #715 -- the fleet-wide scope clarification, verbatim:
        self.assertIn(
            "ja horovrim o pravidlach pre vsetky targety nie len pre airuleset "
            "projekt", t)

    def test_judgment_content_is_the_phase_selector(self):
        # #715: the JUDGMENT-CONTENT test no longer decides whether a whole
        # subagent runs Fable -- it is now a PHASE selector: it decides whether
        # a ticket is non-trivial enough to warrant the two Fable PHASES (the
        # DESIGN phase before implementation, the REVIEW phase before
        # integration). The implementing worker is NEVER Fable. Tie-break kept
        # (unsure -> it DOES -> gets the Fable design + review phases).
        t = _full_module()
        self.assertIn("JUDGMENT-CONTENT test", t)
        self.assertIn("PHASE selector", t)
        self.assertIn("the DESIGN phase", t)
        self.assertIn("the REVIEW phase", t)
        self.assertIn("Non-trivial implementation", t)
        self.assertIn("Review of a non-trivial change", t)
        self.assertIn("Hard debugging", t)
        self.assertIn(
            "when unsure whether a task carries judgment content → it DOES", t)
        # the implementing worker never runs Fable, on any repo:
        self.assertIn("never the implementing worker end-to-end", t)
        # the old always-against-Fable tie-break must be gone as LIVE policy:
        self.assertNotIn(
            "When unsure whether it is design-heavy → it is NOT", t)

    def test_airuleset_exception_abolished_fleet_wide(self):
        # #715: the 2026-08-25 "airuleset repo = Fable-MAJORITY subagent
        # dispatch" exception is ABOLISHED -- the per-phase split is fleet-wide,
        # the same on every target and project, and there is NO repo where the
        # implementing worker runs Fable. "Fable-MAJORITY" survives ONLY as
        # dated history (do not assert its absence -- it is preserved), so lock
        # the LIVE fleet-wide statement + the abolition, and assert the old
        # airuleset-majority BULLET phrasing is gone as live policy.
        t = _full_module()
        self.assertIn("FLEET-WIDE", t)
        self.assertRegex(
            t, r"airuleset[^\n]*Fable-MAJORITY[^\n]*exception is ABOLISHED")
        self.assertIn("no repo where the implementing worker runs Fable", t)
        # the old live airuleset-majority bullet phrase is gone:
        self.assertNotIn(
            'carries an explicit `model: "fable"` when the gate is OPEN', t)

    def test_gate_threshold_default_is_90(self):
        # #690: threshold raised 80 -> 90 so the new usage level actually
        # passes (at the observed baseline fable=43%/weekly=66% the new
        # dispatch load projects into the 60-90% band, which an 80% gate
        # would dead-letter mid-week). Fail-safe CLOSED semantics are locked
        # separately in test_usage_cache.py and must NOT change.
        self.assertEqual(FABLE_GATE_PCT, 90)
        self.assertIn("raised 80→90", _full_module())

    def test_opus_5_is_banned_and_alias_named(self):
        t = _full_module()
        self.assertIn("BANNED", t)
        self.assertIn("`claude-opus-5`", t)
        # the alias must be named as resolving to the banned model, so no
        # rule ever reaches for the bare `opus` alias again:
        self.assertIn("`opus` alias", t)

    def test_implementation_tier_default_sonnet_escalate_opus_4_8(self):
        # #721 (burn phase 2 after #715): the IMPLEMENTATION phase defaults to
        # Sonnet 5 for a SETTLED-DESIGN ticket; it escalates to Opus 4.6 only
        # when the implementation itself carries complexity (named, testable
        # criteria). It STILL never runs Fable -- the implementing worker never
        # carries a model:"fable" override, on any repo. #715's flat
        # "implementation = Opus 4.6" default is retired.
        t = _full_module()
        self.assertIn(
            "IMPLEMENTATION (the actual work) = Sonnet 5 by DEFAULT for a "
            "settled-design ticket", t)
        # the no-Fable-on-worker invariant (from #715, #871 rephrasing) STAYS:
        self.assertIn(
            "implementing worker NEVER dispatches as `fable-advisor`", t)
        # #498/#500 TEETH: the five escalation criteria + the sonnet default
        # must all co-occur ON the IMPLEMENTATION bullet's OWN physical line
        # (it is a single unwrapped markdown bullet), so a partial revert of
        # that clause -- leaving "concurrency"/the sonnet-implementer mention
        # elsewhere in the module -- is caught, not just a full deletion.
        impl_line = self._impl_bullet_line(t)
        for tok in ("multi-component change", "concurrency", "security boundary",
                    "hard-debug lane",
                    "prior Sonnet worker already failed on this ticket",
                    "`sonnet-implementer`", "when in doubt, Opus 4.6"):
            self.assertIn(
                tok, impl_line,
                "escalation/default token missing from the IMPLEMENTATION "
                "bullet line: %r" % tok)
        # #715's flat "implementation = Opus 4.6" default is retired as the
        # LIVE default (the whole point of #721):
        self.assertNotIn(
            "IMPLEMENTATION (the actual work) = Opus 4.6 (`claude-opus-4-6`)", t)
        # the old banned Sonnet-execution phrasing stays banned:
        self.assertNotIn("EXECUTION of settled, scoped code = Sonnet 5", t)

    def test_implementation_tier_mechanism_frontmatter_pin_is_escalation(self):
        # #721 mechanism (the design decision this ticket owns): the
        # autopilot-worker frontmatter STAYS pinned claude-opus-4-6 = the
        # escalation tier AND the fail-safe default. The supervisor downtiers
        # to Sonnet 5 with an explicit model:"sonnet" for a settled-design
        # ticket; it OMITS the param (pin stands -> Opus 4.6) to escalate.
        # This is the only mechanically-enforceable shape: the opus alias is
        # banned and 4.6 has NO param alias, so 4.6 can ONLY be reached by the
        # pin (dispatch AS-IS), never by a param -- which also makes the
        # fail-safe direction UP (forget-to-classify -> Opus 4.6, never lower).
        t = _full_module()
        # anchored to the IMPLEMENTATION bullet's own line for teeth (#498):
        impl_line = self._impl_bullet_line(t)
        self.assertIn("dispatching the pinned `sonnet-implementer` agent", impl_line)
        self.assertIn("escalation tier", impl_line)
        self.assertIn("fail-safe default", impl_line)
        self.assertIn(
            "frontmatter stays pinned `model: claude-opus-4-6`", impl_line)

    def test_sonnet_never_complex(self):
        t = _full_module()
        self.assertIn("Sonnet 5 is never used for anything complex", t)
        self.assertIn("when in doubt, Opus 4.6", t)

    def test_gate_guards_every_automatic_fable_dispatch(self):
        # #715 (2026-08-26): the gate guards EVERY automatic Fable dispatch --
        # now the design-phase consult + the review-phase pass. Older gate-role
        # phrasings stay retired.
        t = _full_module()
        self.assertIn("airuleset.py fable-gate", t)
        self.assertIn("guards EVERY automatic Fable dispatch", t)
        self.assertNotIn("guards the HARD-only Fable escalation", t)
        self.assertNotIn("guards the DEFAULT judgment layer", t)
        self.assertIn("ONCE per qualifying Fable phase-dispatch", t)
        self.assertIn("missing/stale cache = CLOSED", t)
        self.assertIn("Never skip the gate", t)
        # CLOSED falls back to Opus 4.6, never the banned alias:
        self.assertRegex(t, r"CLOSED[^\n]*claude-opus-4-6")

    def test_opus_4_8_reach_mechanics_documented(self):
        # The per-dispatch `model` param takes only aliases (live-verified,
        # ROZHODNUTE mapping mechanics), so the module must document the
        # REAL mechanisms: agent-definition frontmatter full id, Workflow
        # opts.model full name, or inheritance -- never the `opus` alias.
        t = _full_module()
        self.assertIn("model: claude-opus-4-6", t)
        self.assertIn("opts.model: 'claude-opus-4-6'", t)
        self.assertIn("never the `opus` alias", t)

    def test_design_heavy_taxonomy_survives(self):
        # #690: the HARD criteria no longer SELECT the model (the
        # judgment-content test does) -- they still classify design DEPTH
        # (the autopilot skill's design-triage step reuses exactly this
        # taxonomy), so the enumeration must survive, demoted explicitly.
        t = _full_module()
        self.assertIn("no longer the Fable selector", t)
        self.assertIn(
            "Architecture / design / synthesis of a genuinely COMPLEX or cross-cutting", t)
        self.assertIn('"Multi-file" alone is NOT the bar', t)
        self.assertIn("Hard debugging", t)
        self.assertIn("Adversarial final review / verify of a safety-critical", t)
        self.assertIn("autopilot ticket that is architectural / cross-cutting", t)
        self.assertRegex(t, r"(?i)sub-dev[^\n]*(hand-off|review|submission)")

    def test_circling_valve_survives_without_opus_5(self):
        t = _full_module()
        self.assertIn("CIRCLING", t)
        self.assertIn("OBSERVED circling", t)

    def test_fable_consult_shape_survives(self):
        t = _full_module()
        self.assertIn("digest in, decision out", t)
        self.assertIn("re-reads the full conversation context every turn", t)
        # #690 (2026-08-25): a DISPATCHED, fresh-context Fable worker is a
        # sanctioned shape now (the 2026-08-14 HARD-only sanction is
        # retired); Fable as a long-lived MAIN implementer stays banned.
        self.assertIn(
            "a DISPATCHED, fresh-context Fable worker is a sanctioned shape", t)
        self.assertNotIn("sanctioned ONLY for genuinely HARD", t)

    def test_behavior_header_is_fable_and_opus_4_8(self):
        t = _full_module()
        self.assertIn("Fable 5 / Opus 4.6 behavior", t)
        self.assertNotIn("Opus 5 / Fable 5 behavior (primary + escalation)", t)

    def test_main_session_clauses_survive(self):
        t = _full_module()
        self.assertIn(
            "MAIN interactive session runs whatever the user set via `/model`", t)
        self.assertIn("NEVER recommend switching it, in either direction", t)

    def test_sonnet_5_rehabilitated_for_light_work(self):
        # #455 (2026-08-14): Sonnet 5 is now the LIGHT / mechanical tier
        # ("moze byt aj sonnet 5 vyuzivany") -- an explicit dispatch target,
        # not merely a fallback for when 4.6 is unreachable.
        t = _full_module()
        self.assertIn("2026-08-14 refinement explicitly REHABILITATED", t)
        self.assertIn("dispatch the pinned **`sonnet-mechanical`** agent at `low`", t)
        self.assertIn("moze byt aj sonnet 5 vyuzivany", t)
        # still never for anything complex:
        self.assertIn("Sonnet 5 is never used for anything complex", t)

    def test_hard_rule_never_model_less_dispatch(self):
        # #455 (2026-08-14): the load-bearing HARD RULE -- a model-less
        # subagent dispatch inherits the Fable main, which is the burn.
        t = _full_module()
        self.assertIn(
            "NEVER dispatch a subagent without an EXPLICIT PINNED AGENT TYPE", t)
        self.assertIn("INHERITS the caller's model", t)
        self.assertIn("silently runs the SUBAGENT on Fable", t)
        self.assertIn("inherited Fable", t)
        # the HARD RULE bullet names its own lock (same line):
        self.assertRegex(t, r"HARD RULE[^\n]*lock-tested")


class TestWorkflowStageTiering(TestCase):
    """claude-code-tooling.md's per-stage mirror of the same policy."""

    def test_judgment_stages_gate_fable_closed_opus_4_8(self):
        t = read(TOOLING_WF)  # #859 batch 3: moved to companion
        self.assertIn("`opts.model: 'claude-fable-5-1'`", t)
        self.assertIn("ONLY when the budget gate is OPEN", t)
        # #715 (2026-08-26): the Fable stages are the DESIGN + REVIEW (+
        # synthesis / adversarial-verify) PHASES -- an IMPLEMENTATION/EXECUTION
        # stage NEVER runs Fable, even if it feels like it carries judgment.
        self.assertIn("DESIGN / SYNTHESIS / REVIEW / adversarial-VERIFY", t)
        self.assertNotIn("ONLY for the HARD subset", t)
        self.assertIn("BEFORE authoring the script", t)
        self.assertIn("never bake in an ungated Fable stage", t)
        self.assertRegex(t, r"CLOSED[^\n]*claude-opus-4-6")

    def test_execution_stages_default_sonnet_escalate_opus_4_8(self):
        # #721: a Workflow ROUTINE EXECUTION stage (a settled plan / the actual
        # work) defaults to Sonnet 5 and escalates to Opus 4.6 on complexity --
        # the same settled-vs-complex split as the autopilot worker. It still
        # NEVER runs Fable (the per-phase invariant from #715 stays). #715's
        # flat "execution stage = claude-opus-4-6" default is retired.
        t = read(TOOLING_WF)  # #859 batch 3: moved to companion
        self.assertIn("EXECUTION stages", t)
        self.assertIn(
            "code transforms/migrations → `opts.model: 'claude-sonnet-5'`", t)
        self.assertIn("ESCALATE the stage to `opts.model: 'claude-opus-4-6'`", t)
        # the flat opus-4.6 execution default is retired:
        self.assertNotIn(
            "code transforms/migrations → `opts.model: 'claude-opus-4-6'`", t)
        # the per-phase Fable invariant survives (execution never runs Fable):
        self.assertIn("NEVER runs Fable", t)

    def test_no_opus_alias_stage(self):
        self.assertNotIn("opts.model: 'opus'", read(TOOLING))

    def test_advisor_digest_shape_survives(self):
        t = read(TOOLING_WF)  # #859 batch 3: moved to companion
        self.assertIn("ADVISOR call: digest in, decision out", t)
        self.assertIn("grounds itself by re-reading the sources", t)


class TestDispatchSurfacesRewritten(TestCase):
    """Every dispatch-instructing surface names the new lineup."""

    def test_autopilot_worker_frontmatter_pin_and_default_sonnet(self):
        # #871: a `model` param is now BLOCKED outright on every dispatch, so
        # the old "supervisor downtiers autopilot-worker to Sonnet via a model
        # override" mechanism is impossible -- the frontmatter pin STAYS
        # claude-opus-4-6 and the worker ALWAYS runs at that tier now; for an
        # ordinary settled-design ticket the supervisor instead dispatches the
        # SEPARATE `sonnet-implementer` agent type (a different pinned
        # definition, claude-sonnet-5).
        w = read("agents/autopilot-worker.md")
        fm = w.split("---")[1]
        self.assertIn("model: claude-opus-4-6", fm)   # pin stays 4.6
        self.assertNotIn("model: sonnet", fm)          # never in frontmatter
        self.assertIn("You run on the pinned `claude-opus-4-6`", w)
        norm = " ".join(w.split())
        self.assertIn("ALWAYS", norm)
        self.assertIn(
            "the supervisor dispatches the pinned `sonnet-implementer` agent "
            "instead of you", norm)
        # the old, now-impossible "downtier via model param" body claim is retired:
        self.assertNotIn("You run on Sonnet 5 by DEFAULT", w)
        self.assertNotIn('the supervisor passes `model: "sonnet"`', w)
        self.assertNotIn("You run on Opus 4.6", w)

    def test_ticket_validator_pinned_to_opus_4_8(self):
        v = read("agents/ticket-validator.md")
        fm = v.split("---")[1]
        self.assertIn("model: claude-opus-4-6", fm)
        self.assertNotIn("model: sonnet", fm)

    def test_worker_hard_wall_ladder_is_gated_fable(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("fable-gate", w)
        self.assertIn("HARD wall mid-ticket", w)
        self.assertNotIn('FIRST at `model: "opus"`', w)
        # #455 (2026-08-14): `model: "opus"` may appear ONLY as a trap warning
        # (a "NEVER do this", on a BANNED line -- the gk incident), never as a
        # dispatch instruction. Mirrors the grep-gate's own ban-prose logic.
        for ln in w.splitlines():
            if 'model: "opus"' in ln:
                self.assertIn(
                    "BANNED", ln,
                    "model: opus appears outside a BANNED trap-warning line: %r"
                    % ln[:80])

    def test_autopilot_supervisor_dispatch_model_rule(self):
        # #871: fleet-wide per-phase dispatch, now on an EXACT-ID ALLOWLIST --
        # no dispatch ever carries a `model` param at all. The supervisor
        # dispatches the implementation WORKER by AGENT TYPE
        # (`sonnet-implementer` settled-design / `autopilot-worker` complex);
        # the DESIGN consult (Step 1c) and the REVIEW pass dispatch the
        # pinned `fable-advisor` agent. No airuleset-majority exception.
        s = read("skills/autopilot/SKILL.md")
        self.assertIn("PER-PHASE", s)
        self.assertIn(
            "gate OPEN (exit 0) → dispatch the pinned `fable-advisor` agent "
            "for that PHASE", s)
        self.assertIn("gate CLOSED", s)
        self.assertIn(
            "Never dispatch the `fable-advisor` agent without the gate check", s)
        # the implementation worker is dispatched by AGENT TYPE, never Fable:
        self.assertIn("IMPLEMENTATION worker", s)
        self.assertIn(
            "the implementation worker NEVER dispatches as `fable-advisor`", s)
        self.assertNotIn("Model = Opus 4.6 by default", s)
        self.assertNotIn("Model = Sonnet 5 by default", s)
        self.assertNotIn('`model: "opus"`', s)
        self.assertNotIn("`model: opus`", s)
        self.assertNotIn("`model: fable`", s)
        self.assertNotIn('`model: "sonnet"`', s)
        # #721/#871: the implementation worker defaults to `sonnet-implementer`
        # (Sonnet 5) for a settled-design ticket, escalating to `autopilot-
        # worker` (Opus 4.6, frontmatter pin) for complexity -- chosen by
        # WHICH AGENT TYPE is dispatched, never a param.
        self.assertIn('`subagent_type: "sonnet-implementer"`', s)
        self.assertIn("Sonnet 5 by default", s)
        self.assertIn("settled-design", s)
        self.assertIn("prior Sonnet worker already failed on this ticket", s)

    def test_fable_advisor_gate_closed_falls_back_to_opus_4_8(self):
        a = read(ADVISOR)
        self.assertRegex(a, r"CLOSED[^\n]*(Opus 4\.6|claude-opus-4-6)")
        self.assertNotIn("runs on `opus` instead", a)
        self.assertNotIn("(or `opus`", a)
        # execution hand-off is Opus 4.6 now, not sonnet:
        self.assertNotIn("dispatches execution to `model: sonnet`", a)

    def test_process_subdev_review_closed_tier_is_opus_4_8(self):
        txt = read("skills/process-subdev/SKILL.md")
        self.assertIn("fable-gate", txt)
        self.assertIn("`fable-advisor` agent", txt)
        self.assertIn("xhigh", txt)
        self.assertIn("never degrades", txt)
        self.assertRegex(txt, r"CLOSED[^\n]*claude-opus-4-6")

    def test_cross_stream_protocol_closed_tier_is_opus_4_8(self):
        txt = read("skills/autopilot/references/cross-stream-protocol.md")
        self.assertRegex(txt, r"CLOSED[^\n]*claude-opus-4-6")

    def test_autopilot_worker_review_stage_explicit_model_mandate(self):
        # #871: since the worker ALWAYS runs on its pinned claude-opus-4-6 now
        # (a model param can no longer downtier it), CYCLE step 6's review
        # dispatch simplifies -- a model-LESS review sub-dispatch always
        # inherits claude-opus-4-6 (the fallback tier already), so there is no
        # more "your own running tier" branching to document.
        w = read("agents/autopilot-worker.md")
        norm = " ".join(w.split())
        self.assertIn("MODEL for the review dispatch", norm)
        self.assertIn(
            "review of a NON-TRIVIAL change is judgment-content work", norm)
        self.assertIn(
            "a model-LESS review sub-dispatch simply inherits your own "
            "`claude-opus-4-6`", norm)
        self.assertIn("which IS the fallback tier", norm)
        # the 2026-08-14 HARD-only review rule is retired:
        self.assertNotIn("the DEFAULT is NO `model` override", norm)
        self.assertNotIn(
            "Escalate the review to Fable ONLY when the TICKET itself "
            "genuinely meets the design-heavy taxonomy", norm)
        # the old dual-tier "Sonnet-dispatched worker" branch is retired --
        # the worker no longer has a Sonnet-dispatch shape at all:
        self.assertNotIn(
            "Your OWN model-less dispatch is SAFE ONLY when you RUN on "
            "`claude-opus-4-6`", norm)
        self.assertNotIn(
            '`model: "sonnet"`-dispatched worker\'s model-less dispatch inherits '
            "SONNET", norm)
        # mechanical sub-dispatches carry the pinned sonnet-mechanical agent,
        # never model-less, and never a model param:
        self.assertIn("dispatches the pinned `sonnet-mechanical` agent", norm)
        self.assertIn("never model-less, and never a `model` param", norm)

    def test_opus_alias_trap_warning_present(self):
        # #455 addendum -- LIVE gk incident 2026-08-14: a naive "explicit
        # model" reading pushed `model: "opus"` (the alias for the 4.6 tier),
        # which resolves to BANNED Opus 5 AND overrode the frontmatter pin. The
        # trap-warning must sit next to the explicit-model mandate on EVERY
        # surface that carries it.
        # #859 batch 4a: MODULE is the stub; use full module for assertions
        for path, text in (
            (MODULE, _full_module()),
            (TOOLING, read(TOOLING)),
            ("agents/autopilot-worker.md", read("agents/autopilot-worker.md")),
        ):
            norm = " ".join(text.split())
            self.assertIn("live gk incident 2026-08-14", norm,
                          "%s: missing gk-incident citation" % path)
            self.assertIn('model: "opus"', norm, "%s: missing the opus trap" % path)
            self.assertIn("BANNED Opus 5", norm,
                          "%s: missing banned-Opus-5 warning" % path)
        # model-awareness spells out the exact resolution: no param at all,
        # for ANY tier, since #871:
        m = " ".join(_full_module().split())
        self.assertIn('Passing `model: "opus"` was NEVER correct', m)
        self.assertIn(
            "the ONLY way to reach ANY tier is dispatching the named PINNED "
            "AGENT TYPE", m)


class TestAdvisorHistoryPreserved(TestCase):
    """The dated policy history stays VERBATIM in the fable-advisor skill --
    the ban rewrites the ACTIVE lineup, never the record of how we got
    here (the 2026-07-01 burn, the 2026-07-25 Opus-5-era rationale)."""

    def test_burn_and_revert_history_survive(self):
        a = read(ADVISOR)
        self.assertIn("reverted the 2026-07-01", a)
        self.assertIn("2026-07-01 Fable-everywhere mode burned tokens brutally", a)
        self.assertIn(
            "Fable running as MAIN (not advisor) accounted for 76% of a ~$13,600 token spend", a)

    def test_dormant_max_performance_record_survives(self):
        a = read(ADVISOR)
        self.assertIn("Dormant — the Fable-everywhere MAX-PERFORMANCE mode", a)
        self.assertIn("re-activate ONLY on the user's explicit say-so", a)

    def test_opus_5_era_rationale_survives_as_history(self):
        a = read(ADVISOR)
        self.assertIn("Opus 5 retires that workaround", a)
        self.assertIn("Sonnet 5 could not reliably carry the coordinator role", a)
        self.assertIn("within 0.5% of Fable 5 on CursorBench 3.2", a)
        self.assertIn("https://www.anthropic.com/news/claude-opus-5", a)
        self.assertIn("community reports of Opus 4.6 degradation", a)
        self.assertIn("no 4.6 model regression so far", a)

    def test_opus_5_era_section_is_marked_superseded(self):
        a = read(ADVISOR)
        self.assertIn("superseded 2026-08-13", a)

    def test_pricing_table_survives_dated(self):
        a = read(ADVISOR)
        self.assertIn("Fable 5 $10/$50", a)
        self.assertIn("Opus 5 $5/$25", a)
        self.assertIn("Sonnet 5 $2/$10", a)
        self.assertIn("Haiku 4.5 $1/$5", a)

    def test_2026_08_25_revision_recorded(self):
        # #690: the advisor's history must cite the 2026-08-25 revision --
        # judgment-content boundary + airuleset Fable-majority + gate 80->90 --
        # as HISTORY (superseded by #715, but the record stays).
        a = read(ADVISOR)
        self.assertIn("2026-08-25", a)
        self.assertIn("majoritne pouzival fable", a)
        self.assertIn("80→90", a)

    def test_2026_08_26_revision_recorded(self):
        # #715: the advisor history records the per-phase revision (design +
        # review = Fable, implementation = Opus 4.6/Sonnet, fleet-wide, the
        # airuleset-majority exception abolished).
        a = read(ADVISOR)
        self.assertIn("2026-08-26", a)
        self.assertIn("per-phase", a.lower())
        self.assertIn("review fable", a)


class TestOpus5GrepGate(TestCase):
    """The banned model id (and the `opus` alias as a dispatch VALUE) must
    not appear on ANY dispatch surface. Allowlist: the ban prose itself
    (lines carrying BANNED/banned/zakazan), and the dated benchmark URL in
    the advisor's history section. Historical narrative homes (docs/,
    .claude/, watchdog/'s REMOVED-jobs incident notes) and tests/ (this
    gate's own home + fixtures) are outside the scanned dispatch set.

    Honestly-stated residuals (adversarial review, #440): (a) the
    ban-prose exemption is LINE-scoped — a code line whose trailing
    comment merely mentions "banned" would pass; accepted, the gate locks
    against DRIFT, not adversaries. (b) `.claude/` is excluded as a
    historical-narrative home, but `.claude/rules/` also holds REACHABLE
    path-scoped rules — those were swept and fixed manually in #440's
    review pass; a future reintroduction there is not mechanically
    caught. (c) the CLOSED-fallback patterns cover the lowercase alias
    and the capitalized bare "Opus" (not followed by "4"); an exotic
    rendering (e.g. "Opus five") is out of scope."""

    SCAN = ("airuleset.py", "statusbar.py", "burn", "notify", "filedrop",
            "agents", "skills", "modules", "hooks", "rules", "profiles",
            "settings", "scripts", "CLAUDE.md")
    EXTS = {".py", ".md", ".sh", ".json", ".profile", ".conf"}

    def _files(self):
        for top in self.SCAN:
            p = ROOT / top
            if p.is_file():
                yield p
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and (f.suffix in self.EXTS or not f.suffix):
                        yield f

    @staticmethod
    def _line_is_ban_prose(line):
        return ("BANNED" in line or "banned" in line
                or "zakázan" in line or "zakazan" in line
                or "retired" in line or "superseded" in line
                or "anthropic.com/news/claude-opus-5" in line)

    def test_claude_opus_5_absent_from_dispatch_surfaces(self):
        violations = []
        for f in self._files():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "claude-opus-5" not in line:
                    continue
                if self._line_is_ban_prose(line):
                    continue
                violations.append(
                    "%s:%d: %s" % (f.relative_to(ROOT), i, line.strip()[:100]))
        self.assertEqual(violations, [])

    def test_opus_alias_never_a_dispatch_value(self):
        # `model: "opus"` / `model: 'opus'` / frontmatter `model: opus` /
        # `opts.model: 'opus'` all resolve to the banned Opus 5 on the
        # Anthropic API. A gate-CLOSED fallback naming the bare alias
        # (`CLOSED ... opus`) is the other real shape the ban must catch;
        # `claude-opus-4-6` never matches either pattern (the `-` fails
        # the boundary), and ban prose avoids the literal shapes.
        alias = re.compile(r"""model:\s*["']?opus["']?(?![\w-])""")
        closed_fallback = re.compile(r"CLOSED[^\n]{0,60}(?<![\w-])`?opus`?(?![\w-])")
        # capitalized bare-"Opus" fallback ("CLOSED → Opus") — the exact OLD
        # model-awareness wording; "Opus 4.6"/"Opus 4" stays legal via the
        # negative lookahead, and `claude-opus-4-6` never matches \bOpus\b.
        closed_capital = re.compile(r"CLOSED[^\n]{0,60}\bOpus\b(?!\s*4)")
        violations = []
        for f in self._files():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if (alias.search(line) or closed_fallback.search(line)
                        or closed_capital.search(line)):
                    if self._line_is_ban_prose(line):
                        continue
                    violations.append(
                        "%s:%d: %s" % (f.relative_to(ROOT), i, line.strip()[:100]))
        self.assertEqual(violations, [])

    def _grep(self, needle):
        """Every non-ban-prose line across the scanned surface containing
        `needle` -- shared helper for the #871 exact-id-allowlist extensions
        below (superseded ids + bare alias `model:` params must never appear
        as a LIVE dispatch value, only inside ban-prose naming them)."""
        violations = []
        for f in self._files():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if needle not in line:
                    continue
                if self._line_is_ban_prose(line):
                    continue
                violations.append(
                    "%s:%d: %s" % (f.relative_to(ROOT), i, line.strip()[:100]))
        return violations

    def test_claude_opus_4_8_absent_from_dispatch_surfaces(self):
        # #871: claude-opus-4-8 is the SUPERSEDED predecessor id (renamed to
        # claude-opus-4-6, the current MODEL_TIERS entry) -- it must never
        # reappear as a live pin/dispatch value.
        self.assertEqual(self._grep("claude-opus-4-8"), [])

    def test_claude_fable_5_0_absent_from_dispatch_surfaces(self):
        # #894 (revises #871): Fable 5.0 (claude-fable-5) is retired from the
        # lineup — it must not appear as a live pin/dispatch value anywhere.
        # Fable 5.1 (claude-fable-5-1) is the current tier.
        import re
        bare_50_re = re.compile(r"claude-fable-5(?!-)")
        violations = []
        for f in self._files():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if not bare_50_re.search(line):
                    continue
                if self._line_is_ban_prose(line):
                    continue
                violations.append(
                    "%s:%d: %s" % (f.relative_to(ROOT), i,
                                   line.strip()[:100]))
        self.assertEqual(violations, [])

    def test_no_bare_alias_model_param_any_family(self):
        # #871: a dispatch NEVER carries a `model` param at all now -- the
        # opus-alias-only check above predates the exact-id allowlist; this
        # extends the SAME shape to the other three families (fable/sonnet/
        # haiku), since a bare alias floats to whatever ships next in its
        # family exactly like `opus` floated to Opus 5 (#871 architecture).
        bare_alias = re.compile(
            r"""model:\s*["']?(fable|sonnet|haiku)["']?(?![\w-])""")
        opts_alias = re.compile(
            r"""opts\.model:\s*['"](fable|sonnet|haiku|opus)['"]""")
        violations = []
        for f in self._files():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if not (bare_alias.search(line) or opts_alias.search(line)):
                    continue
                if self._line_is_ban_prose(line):
                    continue
                violations.append(
                    "%s:%d: %s" % (f.relative_to(ROOT), i, line.strip()[:100]))
        self.assertEqual(violations, [])


class TestUltracodeStandingDefault(TestCase):
    """#751 (owner directive 2026-08-30, verbatim: "Chcel by som este aby sa
    claude v targetoch nespustali s zapnutym ultracode ale s effort high"):
    REVERSES the LAUNCH-FLAG half of #445 — managed sessions no longer launch
    with ultracode and the effort baseline drops `xhigh` → `high`. ONLY the
    launch flags reversed; the max-acceleration doctrine (worktree fleet,
    disjoint lanes, serial-only integration) and per-phase model tiering are
    UNCHANGED. The settings/launcher halves are locked in tests/test_airuleset.py;
    these lock the policy PROSE."""

    def test_tooling_records_the_2026_08_30_reversal_verbatim(self):
        # #859: verbatim quote moved to the history file; check the union
        t = read(TOOLING) + "\n" + read(".claude/rules-reference/claude-code-tooling-history.md")
        self.assertIn(
            "Chcel by som este aby sa claude v targetoch nespustali s zapnutym "
            "ultracode ale s effort high", t)
        self.assertIn("NO LONGER a managed launch flag", t)
        # the #445 standing-default claim must be gone:
        self.assertNotIn("Ultracode is the STANDING DEFAULT on every managed session", t)
        self.assertNotIn("managed STANDING DEFAULT", t)

    def test_stop_and_ask_step_is_retired(self):
        t = read(TOOLING)
        self.assertNotIn("STOP and ASK for ultracode", t)
        self.assertNotIn("The agent CANNOT enable ultracode itself", t)

    def test_max_acceleration_doctrine_is_explicit(self):
        # UNCHANGED by #751 — only the launch flags reversed, not the doctrine.
        t = read(TOOLING_WF)  # #859 batch 3: moved to companion
        self.assertIn("disjoint lanes", t)
        self.assertIn("integration strictly serial", t)
        self.assertIn("single-worker only when the task genuinely cannot parallelize", t)

    def test_module_effort_baseline_is_high_no_ultracode(self):
        m = _full_module()
        self.assertIn(
            "managed MAIN-session baseline is `high` with NO standing ultracode "
            "launch flag", m)
        self.assertNotIn("managed MAIN-session baseline is `xhigh` + standing ultracode", m)


if __name__ == "__main__":
    main()
