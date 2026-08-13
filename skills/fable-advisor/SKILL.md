---
name: fable-advisor
description: One-shot Fable ADVISOR consult for a genuinely HARD decision — the master session (any model) grounds the problem into a tight digest, checks the budget gate, dispatches ONE Fable call (digest in → decision out) and hands execution to an Opus 4.8 worker. Load when a hard design fork / root-cause dead-end / safety-critical verdict needs top-tier judgment WITHOUT re-grounding the whole problem on Fable.
---

# Fable Advisor — digest in, decision out, execution elsewhere

The affordable way to get Fable-grade judgment on a HARD call from a
NON-Fable context (airuleset #32 — since 2026-08-13 the managed MAIN default
IS Fable, so this consult shape mostly serves the Opus 4.8 execution
workers): the caller grounds the problem into a tight digest, Fable is
consulted as a ONE-SHOT advisor, and an Opus 4.8 worker executes the
decision. Never let Fable ground itself or type the implementation — a
Fable session re-reading everything every turn is the 2026-07-01 burn (the
presenter incident 2026-07-24; hook-enforced by
`block-main-implementation.sh`, which also blocks a MAIN session with an
ARMED `/goal` from implementing on ANY model, #54).

## When to consult (the HARD bar — model-awareness.md)

Complex/cross-cutting architecture or design synthesis; a root cause that
resisted a first Opus-4.8-tier attempt; adversarial verify of a
safety-critical change; a session CIRCLING (≥2 laps on the same decision
without progress). When unsure whether it is hard → it is NOT; stay at your
current tier and do not consult.

## Protocol

1. **Gate ONCE per hard task:**
   ```bash
   python3 ~/devel/airuleset/airuleset.py fable-gate
   ```
   Exit 0 = OPEN → advisor runs on `fable`. Exit 1 = CLOSED (incl.
   missing/stale cache) → the SAME consult runs on **Opus 4.8**
   (`claude-opus-4-8`) instead — never the bare `opus` alias (it resolves
   to the BANNED Opus 5, directive 2026-08-13): reach 4.8 via a
   `claude-opus-4-8`-pinned agent definition or by omitting the `model`
   override from a `claude-opus-4-8` session; a Fable MAIN at gate CLOSED
   holds the judgment itself rather than spending a new dispatch. Never
   skip the gate, never re-poll it within the task.

2. **Ground the problem into a TIGHT digest — in THIS session, or via one
   cheap read stage (`claude-opus-4-8` low where a surface names it;
   `sonnet` only for genuinely trivial collection).** The digest carries:
   the facts (measured, not
   assumed), the constraints, what was already tried and how it failed, and
   the ONE concrete question. No file dumps, no repo tours — the advisor
   never re-reads sources (that is the burn shape this skill exists to
   prevent).

3. **ONE advisor dispatch** (background, so the master stays interactive):
   Agent tool — `subagent_type: general-purpose`, `model: fable` when the
   gate is OPEN (at CLOSED use the Opus 4.8 route above — the bare alias is
   BANNED), `effort: xhigh`, `run_in_background: true`;
   prompt = the digest + the question + "Return ONLY the decision with a
   short rationale — do not read the repository, do not execute anything."

4. **Execute via an Opus 4.8 worker.** The master receives the decision,
   records it durably (ticket comment — `durable-decisions-to-tickets.md`),
   and dispatches execution on the Opus 4.8 tier — the repo's two pinned
   definitions are `agents/autopilot-worker.md` and
   `agents/ticket-validator.md` (`model: claude-opus-4-8` frontmatter); a
   `subagent-driven-development` implementer or other ad-hoc execution
   dispatch has NO repo pin and inherits the session's model — from a
   worker that is 4.8, from a Fable main it stays Fable, so prefer the
   pinned worker for issue-shaped execution (never sonnet for anything
   complex). The master reviews the worker's diff — that is the oversight
   role.

## Anti-patterns (all rewordings)

- Consulting Fable as a long-lived WORKER or letting it ground itself by
  reading the repo → the exact 2026-07-01 burn. Digest in, decision out.
- Escalating routine work ("this feature is non-trivial") → routine
  execution stays Opus 4.8; the HARD criteria above are the whole list.
- Skipping the gate because "it's just one call" → every automatic Fable
  dispatch is gated, no exceptions.
- Re-asking the advisor per sub-question → ONE consult per hard fork; new
  facts → update the digest, one follow-up consult max.


## Reference — the tier lineup, pricing, and why the policy is shaped this way

Moved VERBATIM from `modules/core/model-awareness.md` (#92 item 2): the always-on module keeps what a session must ACT on; this is the justification layer behind it — read it when reasoning about tiers or costs, not every turn.

### The lineup and what each tier costs

**CURRENT lineup (2026-08-13): Fable 5** (`claude-fable-5[1m]`, the `fable` alias — Anthropic's Mythos-class tier, the most intelligent generally-available Claude; a first-class subagent `model` value) **is the managed MAIN default AND the budget-gated judgment tier; Opus 4.8** (`claude-opus-4-8`) **is the execution + gate-CLOSED tier; Sonnet 5** (`sonnet`) only for genuinely trivial mechanical lookups; **Haiku 4.5** (`haiku`) for the most trivial reads. **Opus 5** (`claude-opus-5`, and the bare `opus` alias that resolves to it) is **BANNED since 2026-08-13** — the user's directive, driven by widespread community dissatisfaction with Opus 5 ("intenet je plny obrovskej nespokojnosti s opus 5"). Historical record of its era (2026-07-25 → 2026-08-13, when it WAS the default main + judgment tier): measured within 0.5% of Fable 5 on CursorBench 3.2 at HALF the price (https://www.anthropic.com/news/claude-opus-5), and it shipped thinking ON by default (a change from 4.8, where it was opt-in) — Fable 5 cannot disable thinking at all, so its output tokens are structurally higher for the same task. Pricing per Mtok in/out (official pricing page, 2026-07-25): Fable 5 $10/$50 · Opus 5 $5/$25 (cache read $0.50, cache write $6.25 5-min / $10 1-hour) · Sonnet 5 $2/$10 · Haiku 4.5 $1/$5; ALL current models ship the 1M context window at standard pricing. (Literalism behavior holds across the family — the top tiers are concise, grounded, honest, and need less anti-slop frontend prompting.)

### Why Opus 5 WAS the recommended MAIN default (2026-07-25 rewrite — superseded 2026-08-13)

Honest history: earlier in 2026 the user ran Fable 5 as MAIN by hand — a deliberate WORKAROUND, not a taste preference: Opus 4.8 was regressing (the CIRCLING valve exists because of it) and Sonnet 5 could not reliably carry the coordinator role, so Fable was the only model that held up as main. Opus 5 retires that workaround: the regression + the Sonnet coordinator gap that made Fable-as-main necessary are both gone, so the recommended default flipped back to Opus 5 — that was the 2026-07-25 reasoning. SUPERSEDED: the 2026-08-13 directive bans Opus 5 outright (community-wide dissatisfaction with it) and returns MAIN to Fable 5 as the MANAGED default — this time the user's explicit standing choice, not a workaround; the user's `/model` choice stays theirs alone, gated on nothing.

### Why every automatic escalation is budget-gated

History that shapes this policy: the 2026-07-01 Fable-everywhere mode burned tokens brutally and kept tripping the usage limits mid-work — the user reverted it 2026-07-02 (reverted the 2026-07-01 policy). What made the 2026-07-03 middle tier safe where Fable-everywhere was not: (1) only the HARD subset escalated (routine work stayed on the cheaper tiers), and (2) every automatic escalation is BUDGET-GATED — `airuleset.py fable-gate` checks the Fable weekly + shared weekly windows from the watchdog's usage cache and closes automatic Fable once headroom runs out (default gate 80%, env `AIRULESET_FABLE_GATE_PCT`), so the limit-trip-stops-work failure cannot repeat. Under the 2026-08-13 policy the gate carries MORE load, not less: it now guards the DEFAULT judgment layer (every judgment dispatch, not just a HARD subset), with Opus 4.8 as the CLOSED fallback — the gate is what keeps the ban affordable. (The 5-hour session window intentionally does NOT gate — only the weekly windows do; don't 'fix' that.)

### Why the CIRCLING valve keys on behavior, not on rumors

July-2026 community reports of Opus 4.8 degradation: anthropics/claude-code#68780 open with no official response; Marginlab's independent statistical tracker attributed the pre-4.8 drop to a HARNESS issue and confirms no 4.8 model regression so far — so the valve keys on OBSERVED circling, never on assuming the model is broken.

### The measured evidence behind the ADVISOR shape

The 2026-07-01 burn came from Fable doing EVERYTHING — grounding, reading, executing — at Fable pricing (and Fable as the MAIN-session model re-reads the full conversation context every turn, which alone ate a Max plan in under an hour). Community-relayed, UNVERIFIED indicative numbers (July 2026, no primary source): advisor + Sonnet-5-executor ≈ 92% of Fable-solo quality at ~63% of the cost — and the budget gate still sits on top. Why the MAIN-session default matters (2026-07-25): across the 6 managed boxes over 8 days, Fable running as MAIN (not advisor) accounted for 76% of a ~$13,600 token spend — the SHAPE constrains every AUTOMATIC escalation, but MAIN-session choice sat outside its scope until Opus 5 shipped and gave MAIN a tier that no longer needs the Fable-as-main workaround.

Enforcement history: `block-main-implementation.sh` (#32) first blocked a Fable MAIN session from typing implementation-size edits (>~20 lines) after the presenter incident — a Fable main implemented a whole issue itself despite the prose rule. Generalized 2026-07-25 (#54) to ANY model with an ARMED `/goal`, after david@subdev's Opus main did 354 direct Edits + 56 Writes alongside 229 Agent dispatches (context 0 → 271K in ~7 minutes). Goal-armed detection reads the session TRANSCRIPT for Claude Code's own `<local-command-stdout>Goal set:` / `Goal cleared:` marker (a hook has no reliable pane access), fully INDEPENDENT of the Fable-model detection. Generalized again 2026-07-26 (#66) to guard `Bash`: 1222 main-agent Bash calls vs only 97 subagent dispatches in one hour of an armed `/goal` loop at a 212K-avg context — every Bash call re-sends the whole context. Corrected 2026-07-26 (#80) — the command's CLASS was the wrong variable: the classifier false-positived on the `cat > body.md <<'EOF'` recipe `gh-cli-recipes.md` mandates, the main armed the bypass marker and the hook was dead for 17 hours (332 bypasses). The marker is now ONE-SHOT, heredoc bodies are stripped, only a statement's FIRST pipe stage is classified, and a read is judged by the SIZE that comes back. The real lever is the COUNT of main-agent turns, not their class, so the hook nudges once past `AIRULESET_MAIN_BASH_PER_DISPATCH` (default 20) and the nudge RESETS the counter — a periodic nudge, never a wall. Widened 2026-07-29 (#128) — the two engagement conditions were proxies for "this session is autonomous" and missed the burn: measured across all 11 of dev1's real transcripts for 2026-07-28, the inert sessions ran 1339 main tool calls against 82 dispatches (the worst: 650 calls, zero dispatches) while the guarded ones ran 853 against 87. A third condition, USER_AWAY, engages when the UserPromptSubmit presence marker is older than `AIRULESET_MAIN_GUARD_AWAY_S` (default 900s); engaging on EVERY main session was measured (348 newly blocked, 164 of them within five minutes of a live human prompt) and refused. The bypass marker must now carry its reason, logged on the arm and the consume.

### Dormant — the Fable-everywhere MAX-PERFORMANCE mode (re-activate ONLY on the user's explicit say-so)

The 2026-07-01 "Fable 5 on every judgment dispatch, cost no object" policy is DORMANT — it burned tokens and tripped limits, stopping work. Re-activate it ONLY if the user AGAIN explicitly says cost is no object / max intelligence everywhere (limits reset with huge headroom): then Fable becomes the default for all judgment work at xhigh. Do NOT re-activate on your own inference — the switch is the user's alone.
