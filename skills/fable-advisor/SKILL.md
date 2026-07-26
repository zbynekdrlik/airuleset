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
`block-main-implementation.sh`, which also blocks a MAIN session with an
ARMED `/goal` from implementing on ANY model, #54).

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


## Reference — the tier lineup, pricing, and why the policy is shaped this way

Moved VERBATIM from `modules/core/model-awareness.md` (#92 item 2): the always-on module keeps what a session must ACT on; this is the justification layer behind it — read it when reasoning about tiers or costs, not every turn.

### The lineup and what each tier costs

**Opus 5** (`opus`, `claude-opus-5`) is the default main + judgment tier: measured within 0.5% of Fable 5 on CursorBench 3.2 at HALF the price (https://www.anthropic.com/news/claude-opus-5), and Opus 5 ships thinking ON by default (a change from 4.8, where it was opt-in) — Fable 5 cannot disable thinking at all, so its output tokens are structurally higher than Opus's for the same task. Dispatched EXECUTION defaults to **Sonnet 5** (`sonnet`, `claude-sonnet-5`, full effort range `low`–`max`; trails Opus on agentic-coding benchmarks but at a fraction of the cost). Above Opus (barely, per CursorBench) sits **Fable 5** (`claude-fable-5`, the `fable` alias — Anthropic's Mythos-class tier, the most intelligent generally-available Claude, Claude 5 family; a first-class subagent `model` value): genuinely HARD tasks escalate to it AUTOMATICALLY through the budget gate — it burns tokens fast, so it is never a blanket default and every automatic use is gated on weekly-limit headroom. Haiku 4.5 (`haiku`) for the most trivial reads. Pricing per Mtok in/out (official pricing page, 2026-07-25): Fable 5 $10/$50 · Opus 5 $5/$25 (cache read $0.50, cache write $6.25 5-min / $10 1-hour) · Sonnet 5 $2/$10 · Haiku 4.5 $1/$5; ALL current models ship the 1M context window at standard pricing. (Literalism behavior holds across the family; Fable 5, Opus 5 and Sonnet 5 are concise, grounded, honest, and need less anti-slop frontend prompting.)

### Why Opus 5 is the recommended MAIN default (2026-07-25 rewrite)

Honest history: earlier in 2026 the user ran Fable 5 as MAIN by hand — a deliberate WORKAROUND, not a taste preference: Opus 4.8 was regressing (the CIRCLING valve exists because of it) and Sonnet 5 could not reliably carry the coordinator role, so Fable was the only model that held up as main. Opus 5 retires that workaround: the regression + the Sonnet coordinator gap that made Fable-as-main necessary are both gone, so the recommended default flips back to Opus 5. This is NOT a ban on Fable as main — the user's `/model` choice stays theirs alone, gated on nothing.

### Why every automatic escalation is budget-gated

History that shapes this policy: the 2026-07-01 Fable-everywhere mode burned tokens brutally and kept tripping the usage limits mid-work — the user reverted it 2026-07-02 (reverted the 2026-07-01 policy). What makes the 2026-07-03 middle tier safe where Fable-everywhere was not: (1) only the HARD subset escalates (routine work stays Opus/Sonnet), and (2) every automatic escalation is BUDGET-GATED — `airuleset.py fable-gate` checks the Fable weekly + shared weekly windows from the watchdog's usage cache and closes automatic Fable once headroom runs out (default gate 80%, env `AIRULESET_FABLE_GATE_PCT`), so the limit-trip-stops-work failure cannot repeat. (The 5-hour session window intentionally does NOT gate — only the weekly windows do; don't 'fix' that.)

### Why the CIRCLING valve keys on behavior, not on rumors

July-2026 community reports of Opus 4.8 degradation: anthropics/claude-code#68780 open with no official response; Marginlab's independent statistical tracker attributed the pre-4.8 drop to a HARNESS issue and confirms no 4.8 model regression so far — so the valve keys on OBSERVED circling, never on assuming the model is broken.

### The measured evidence behind the ADVISOR shape

The 2026-07-01 burn came from Fable doing EVERYTHING — grounding, reading, executing — at Fable pricing (and Fable as the MAIN-session model re-reads the full conversation context every turn, which alone ate a Max plan in under an hour). Community-relayed, UNVERIFIED indicative numbers (July 2026, no primary source): advisor + Sonnet-5-executor ≈ 92% of Fable-solo quality at ~63% of the cost — and the budget gate still sits on top. Why the MAIN-session default matters (2026-07-25): across the 6 managed boxes over 8 days, Fable running as MAIN (not advisor) accounted for 76% of a ~$13,600 token spend — the SHAPE constrains every AUTOMATIC escalation, but MAIN-session choice sat outside its scope until Opus 5 shipped and gave MAIN a tier that no longer needs the Fable-as-main workaround.

Enforcement history: `block-main-implementation.sh` (#32) first blocked a Fable MAIN session from typing implementation-size edits (>~20 lines) after the presenter incident — a Fable main implemented a whole issue itself despite the prose rule. Generalized 2026-07-25 (#54) to ANY model with an ARMED `/goal`, after david@subdev's Opus main did 354 direct Edits + 56 Writes alongside 229 Agent dispatches (context 0 → 271K in ~7 minutes). Goal-armed detection reads the session TRANSCRIPT for Claude Code's own `<local-command-stdout>Goal set:` / `Goal cleared:` marker (a hook has no reliable pane access), fully INDEPENDENT of the Fable-model detection. Generalized again 2026-07-26 (#66) to guard `Bash`: 1222 main-agent Bash calls vs only 97 subagent dispatches in one hour of an armed `/goal` loop at a 212K-avg context — every Bash call re-sends the whole context. Corrected 2026-07-26 (#80) — the command's CLASS was the wrong variable: the classifier false-positived on the `cat > body.md <<'EOF'` recipe `gh-cli-recipes.md` mandates, the main armed the bypass marker and the hook was dead for 17 hours (332 bypasses). The marker is now ONE-SHOT, heredoc bodies are stripped, only a statement's FIRST pipe stage is classified, and a read is judged by the SIZE that comes back. The real lever is the COUNT of main-agent turns, not their class, so the hook nudges once past `AIRULESET_MAIN_BASH_PER_DISPATCH` (default 20) and the nudge RESETS the counter — a periodic nudge, never a wall.

### Dormant — the Fable-everywhere MAX-PERFORMANCE mode (re-activate ONLY on the user's explicit say-so)

The 2026-07-01 "Fable 5 on every judgment dispatch, cost no object" policy is DORMANT — it burned tokens and tripped limits, stopping work. Re-activate it ONLY if the user AGAIN explicitly says cost is no object / max intelligence everywhere (limits reset with huge headroom): then Fable becomes the default for all judgment work at xhigh. Do NOT re-activate on your own inference — the switch is the user's alone.
