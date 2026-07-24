---
name: fable-advisor
description: One-shot Fable ADVISOR consult for a genuinely HARD decision — the master session (any model) grounds the problem into a tight digest, checks the budget gate, dispatches ONE Fable call (digest in → decision out) and hands execution to a Sonnet worker. Load when a hard design fork / root-cause dead-end / safety-critical verdict needs top-tier judgment WITHOUT running the whole session on Fable.
---

# Fable Advisor — digest in, decision out, execution elsewhere

The affordable way to get Fable-grade judgment (airuleset #32): the MASTER
session stays on a cheap model (Opus default), Fable is consulted as a
ONE-SHOT advisor for the genuinely hard call, and a Sonnet worker executes
the decision. Never run the implementation loop on Fable — a Fable main
re-reads the whole conversation every turn (the 2026-07-01 burn; the
presenter incident 2026-07-24; hook-enforced by
`block-fable-main-implementation.sh`).

## When to consult (the HARD bar — model-awareness.md)

Complex/cross-cutting architecture or design synthesis; a root cause that
resisted an Opus-tier attempt; adversarial verify of a safety-critical
change; an Opus session CIRCLING (≥2 laps on the same decision without
progress). When unsure whether it is hard → it is NOT; stay on Opus and do
not consult.

## Protocol

1. **Gate ONCE per hard task:**
   ```bash
   python3 ~/devel/airuleset/airuleset.py fable-gate
   ```
   Exit 0 = OPEN → advisor runs on `fable`. Exit 1 = CLOSED (incl.
   missing/stale cache) → the SAME consult runs on `opus` instead — never
   skip the gate, never re-poll it within the task.

2. **Ground the problem into a TIGHT digest — in THIS session, or via one
   cheap `sonnet` read stage.** The digest carries: the facts (measured, not
   assumed), the constraints, what was already tried and how it failed, and
   the ONE concrete question. No file dumps, no repo tours — the advisor
   never re-reads sources (that is the burn shape this skill exists to
   prevent).

3. **ONE advisor dispatch** (background, so the master stays interactive):
   Agent tool — `subagent_type: general-purpose`, `model: fable` (or `opus`
   when the gate is CLOSED), `effort: xhigh`, `run_in_background: true`;
   prompt = the digest + the question + "Return ONLY the decision with a
   short rationale — do not read the repository, do not execute anything."

4. **Execute via a Sonnet worker.** The master receives the decision,
   records it durably (ticket comment — `durable-decisions-to-tickets.md`),
   and dispatches execution to `model: sonnet` at `high`/`xhigh` (or the
   proper mechanism: autopilot-worker / subagent-driven-development). The
   master reviews the worker's diff — that is the oversight role.

## Anti-patterns (all rewordings)

- Consulting Fable as a long-lived WORKER or letting it ground itself by
  reading the repo → the exact 2026-07-01 burn. Digest in, decision out.
- Escalating routine work ("this feature is non-trivial") → routine stays
  Opus/Sonnet; the HARD criteria above are the whole list.
- Skipping the gate because "it's just one call" → every automatic Fable
  dispatch is gated, no exceptions.
- Re-asking the advisor per sub-question → ONE consult per hard fork; new
  facts → update the digest, one follow-up consult max.
