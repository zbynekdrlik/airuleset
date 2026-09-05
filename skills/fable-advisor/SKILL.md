---
name: fable-advisor
user-invocable: false
disable-model-invocation: true
description: One-shot Fable 5.0 ADVISOR consult — digest in, decision out. Dispatch for gated design or review phase; never for implementation.
---

# Fable Advisor — digest in, decision out, execution elsewhere

The affordable way to get Fable-grade judgment on a HARD call from a
NON-Fable context (airuleset #32 — since 2026-08-13 the managed MAIN default
IS Fable, so this consult shape mostly serves the Opus 4.6 execution
workers): the caller grounds the problem into a tight digest, Fable is
consulted as a ONE-SHOT advisor, and an Opus 4.6 worker executes the
decision. Never let Fable ground itself or type the implementation — a
Fable session re-reading everything every turn is the 2026-07-01 burn (the
presenter incident 2026-07-24; hook-enforced by
`block-main-implementation.sh`, which also blocks a MAIN session with an
ARMED `/goal` from implementing on ANY model, #54).

## When to consult (the HARD bar — model-awareness.md)

Complex/cross-cutting architecture or design synthesis; a root cause that
resisted a first Opus-4.6-tier attempt; adversarial verify of a
safety-critical change; a session CIRCLING (≥2 laps on the same decision
without progress). NB (2026-08-26, per-phase): this bar gates only the EXTRA
mid-task advisor call — the dispatch-time model split is PER-PHASE
(`model-awareness.md`): the DESIGN and REVIEW phases of a non-trivial ticket
run gated Fable, the implementation worker runs Sonnet 5 by default for a
settled-design ticket (Opus 4.6 on complexity, #721); do not read this
consult bar as the tiering boundary.

## Protocol

1. **Gate ONCE per hard task:**
   ```bash
   python3 ~/devel/airuleset/airuleset.py fable-gate
   ```
   Exit 0 = OPEN → advisor runs on `fable`. Exit 1 = CLOSED (incl.
   missing/stale cache) → the SAME consult runs on **Opus 4.6**
   (`claude-opus-4-6`) instead — never the bare `opus` alias (it resolves
   to the BANNED Opus 5, directive 2026-08-13): reach Opus 4.6 via a
   `claude-opus-4-6`-pinned agent definition or by omitting the `model`
   override from a `claude-opus-4-6` session; a Fable MAIN at gate CLOSED
   holds the judgment itself rather than spending a new dispatch. Never
   skip the gate, never re-poll it within the task.

2. **Ground the problem into a TIGHT digest — in THIS session, or via one
   cheap read stage (`claude-opus-4-6` low where a surface names it;
   `sonnet` only for genuinely trivial collection).** The digest carries:
   the facts (measured, not
   assumed), the constraints, what was already tried and how it failed, and
   the ONE concrete question. No file dumps, no repo tours — the advisor
   never re-reads sources (that is the burn shape this skill exists to
   prevent).

3. **ONE advisor dispatch** (background, so the master stays interactive):
   Agent tool — `subagent_type: fable-advisor` with NO `model` param (its
   frontmatter pins `claude-fable-5` = Fable 5.0) when the gate is OPEN (at
   CLOSED use the Opus 4.6 route above — a dispatch NEVER carries a `model`
   alias param, #871; the bare `fable` alias floats to the BANNED 5.1),
   `effort: xhigh`, `run_in_background: true`;
   prompt = the digest + the question + "Return ONLY the decision with a
   short rationale — do not read the repository, do not execute anything."

4. **Execute via the pinned Opus 4.6 IMPLEMENTATION worker (per-phase,
   2026-08-26).** The master receives the decision, records it durably
   (ticket comment — `durable-decisions-to-tickets.md`), and dispatches the
   IMPLEMENTATION on Opus 4.6: the implementing worker is NEVER a Fable
   dispatch (never the `fable-advisor` agent), on any repo (the airuleset
   exception is abolished, fleet-wide). Fable is confined to the DESIGN and REVIEW phase
   dispatches (this consult IS a design-phase / hard-wall consult). Routine
   execution (or gate CLOSED) runs the Opus 4.6 tier via the repo's two
   pinned definitions,
   `agents/autopilot-worker.md` and `agents/ticket-validator.md`
   (`model: claude-opus-4-6` frontmatter). A `subagent-driven-development`
   implementer or other ad-hoc execution dispatch has NO repo pin and
   inherits the session's model — from a worker that is Opus 4.6, from a Fable
   main it stays Fable, so prefer the pinned worker for routine
   issue-shaped execution (never sonnet for anything complex). The master
   reviews the worker's diff — that is the oversight role.

## Anti-patterns (all rewordings)

- Consulting Fable as a long-lived WORKER or letting it ground itself by
  reading the repo → the exact 2026-07-01 burn. Digest in, decision out.
- Escalating genuinely MECHANICAL work (a CI poll, a lookup, a
  format-only transform) to Fable → those stay sonnet/haiku; and
  implementation (the actual work) runs its tier (Sonnet 5 for a settled-design
  ticket, Opus 4.6 on complexity — #721) — never Fable. (Since
  2026-08-26 Fable is confined to the DESIGN and REVIEW PHASES of a
  non-trivial ticket — never the implementing worker end-to-end.)
- Skipping the gate because "it's just one call" → every automatic Fable
  dispatch is gated, no exceptions.
- Re-asking the advisor per sub-question → ONE consult per hard fork; new
  facts → update the digest, one follow-up consult max.


## Reference — the tier lineup, pricing, and why the policy is shaped this way

Moved VERBATIM from `modules/core/model-awareness.md` (#92 item 2): the always-on module keeps what a session must ACT on; this is the justification layer behind it — read it when reasoning about tiers or costs, not every turn.

### The lineup and what each tier costs

**CURRENT lineup (2026-08-26 revision — the model ids, pricing and Opus 5 ban below are unchanged; the ESCALATION BOUNDARY is now PER-PHASE + FLEET-WIDE, see the 2026-08-26 section): Fable 5** (`claude-fable-5[1m]` main launch / `claude-fable-5` pinned dispatch via `fable-advisor` — never the bare `fable` alias, which floats to the banned Fable 5.1, #871 — Anthropic's Mythos-class tier, the most intelligent generally-available Claude) **is the managed MAIN default AND (budget-gated) the tier for the DESIGN phase and the REVIEW phase of a non-trivial ticket — never the implementing worker end-to-end, fleet-wide; Opus 4.6** (`claude-opus-4-6`) **is the implementation ESCALATION (complexity) + gate-CLOSED fallback tier; Sonnet 5** (`claude-sonnet-5`, via the pinned `sonnet-implementer`/`sonnet-mechanical` agents) is the settled-design implementation DEFAULT AND the LIGHT / mechanical tier (CI polling, log/grep sweeps, read-only lookups — never anything complex; #721); **Haiku 4.5** (`claude-haiku-4-5`, via a pinned agent) for the most trivial reads. **Opus 5** (`claude-opus-5`, and the bare `opus` alias that resolves to it) is **BANNED since 2026-08-13** — the user's directive, driven by widespread community dissatisfaction with Opus 5 ("intenet je plny obrovskej nespokojnosti s opus 5"). Historical record of its era (2026-07-25 → 2026-08-13, when it WAS the default main + judgment tier): measured within 0.5% of Fable 5 on CursorBench 3.2 at HALF the price (https://www.anthropic.com/news/claude-opus-5), and it shipped thinking ON by default (a change from Opus 4.6, where it was opt-in) — Fable 5 cannot disable thinking at all, so its output tokens are structurally higher for the same task. Pricing per Mtok in/out (official pricing page, 2026-07-25): Fable 5 $10/$50 · Opus 5 $5/$25 (cache read $0.50, cache write $6.25 5-min / $10 1-hour) · Sonnet 5 $2/$10 · Haiku 4.5 $1/$5; ALL current models ship the 1M context window at standard pricing. (Literalism behavior holds across the family — the top tiers are concise, grounded, honest, and need less anti-slop frontend prompting.)

### Why Opus 5 WAS the recommended MAIN default (2026-07-25 rewrite — superseded 2026-08-13)

Honest history: earlier in 2026 the user ran Fable 5 as MAIN by hand — a deliberate WORKAROUND, not a taste preference: Opus 4.6 was regressing (the CIRCLING valve exists because of it) and Sonnet 5 could not reliably carry the coordinator role, so Fable was the only model that held up as main. Opus 5 retires that workaround: the regression + the Sonnet coordinator gap that made Fable-as-main necessary are both gone, so the recommended default flipped back to Opus 5 — that was the 2026-07-25 reasoning. SUPERSEDED: the 2026-08-13 directive bans Opus 5 outright (community-wide dissatisfaction with it) and returns MAIN to Fable 5 as the MANAGED default — this time the user's explicit standing choice, not a workaround; the user's `/model` choice stays theirs alone, gated on nothing.

### Why every automatic escalation is budget-gated

History that shapes this policy: the 2026-07-01 Fable-everywhere mode burned tokens brutally and kept tripping the usage limits mid-work — the user reverted it 2026-07-02 (reverted the 2026-07-01 policy). What made the 2026-07-03 middle tier safe where Fable-everywhere was not: (1) only the HARD subset escalated (routine work stayed on the cheaper tiers), and (2) every automatic escalation is BUDGET-GATED — `airuleset.py fable-gate` checks the Fable weekly + shared weekly windows from the watchdog's usage cache and closes automatic Fable once headroom runs out (default gate 80% in that era — raised to 90% by the 2026-08-25 revision below; env `AIRULESET_FABLE_GATE_PCT`), so the limit-trip-stops-work failure cannot repeat. The 2026-08-13 policy read the gate as guarding the DEFAULT judgment layer (every judgment dispatch); the **2026-08-14 refinement RETIRES that reading** and returns the gate to guarding the HARD-only Fable escalation — only genuinely HARD (design-heavy) judgment escalates to Fable, routine judgment/review runs Opus 4.6, and Sonnet 5 carries light/mechanical work, with Opus 4.6 as the CLOSED fallback — a reading itself superseded 2026-08-25 (the judgment-content boundary, see that section below; the gate mechanics are unchanged). (The 5-hour session window intentionally does NOT gate — only the weekly windows do; don't 'fix' that.)

### 2026-08-14 — Fable narrowed back to HARD-only after the inherited-Fable burn (#455)

The 2026-08-13 policy's "gated Fable is the DEFAULT judgment layer" reading, combined with a model-less-dispatch gap (an `Agent`/`Explore`/`general-purpose` dispatch that omits `model` INHERITS the caller's model, and the managed MAIN default is Fable), meant essentially every subagent — including purely mechanical CI pollers — ran on Fable. The gk box ate its 5h Fable limit in ~30 minutes: live evidence showed `general-purpose` CI-poll agents at 300–430k tokens EACH on inherited Fable. User directive 2026-08-14 (verbatim in `model-awareness.md`): *"len tazke ulohy isli na fable ostatok na opus4.6 a moze byt aj sonnet 5 vyuzivany"*, clarified that the MAIN agent stays Fable (they need the model that talks to them highly intelligent) but the SUBAGENTS should be "spravne rozhodnuty" — the burn started only "po dnesnych zmenach v pouzivani modelov" (#440). The refinement: Fable ONLY for genuinely HARD (design-heavy) work through the gate; Opus 4.6 the default for execution AND ordinary judgment/review; Sonnet 5 rehabilitated for light/mechanical work; a HARD RULE that no subagent is ever dispatched model-less from a Fable main. The Opus-5 ban and the gate mechanics are unchanged.

### 2026-08-25 — boundary moved: judgment-content Fable + airuleset Fable-majority (#690)

Owner directive (verbatim): *"ale ja by som chcel aby sa fable pouzival nie len na super tazke subagenticke ulohy, lebo som ho v principe nevidel zapnuteho za celu zivotnost vsetkych subdevs nikde subagenta s fable, chcem aby sa ta hranica posunula aby sa pouzival fable ovela viac, cize napr na 50% subagentickych uloh a plus tu na airuleset chcem aby sa majoritne pouzival fable lebo chyby ktotre tu robi opus mi extremne zeru cas a degraduju celu flotilu"*. The 2026-08-14 HARD-only bar was NEVER crossed in practice — zero Fable subagents across the subdevs' whole lifetime, because the taxonomy's height plus its "when unsure → it is NOT hard" tie-break resolved every real ticket's uncertainty to Opus 4.6 (and execution was categorically excluded from Fable), while the budget gate sat OPEN (reading at revision time: fable=43% weekly=66% < 80%). ACTIVE policy since 2026-08-25: **Fable runs every subagent task with real judgment/design content** — non-trivial implementation, review of a non-trivial change, hard debug, plan/design/synthesis; tie-break REVERSED (unsure → it QUALIFIES); the owner's ≈50% of subagent tasks is a calibration target, never a counter — **and on the airuleset repo Fable is the MAJORITY subagent tier outright** (Opus mistakes there degrade the whole fleet). Opus 4.6 = the routine-execution + gate-CLOSED fallback; Sonnet light and the Opus 5 ban unchanged. The gate STAYS on every automatic Fable dispatch, with the default threshold raised **80→90** (`FABLE_GATE_PCT`, `watchdog/usage.py`): the 43%-fable baseline was main-session-only usage, so the new dispatch load projects the fable weekly into the 60–90% band, where an 80% gate would flip CLOSED mid-week and silently re-create the zero-Fable dead letter; 90% keeps the policy live while preserving fail-safe CLOSED on a missing/stale cache, a 10% reserve for the owner's own interactive Fable main, and the hard stop before a 100% weekly trip (the 2026-07-01 incident class). A DISPATCHED, fresh-context Fable worker is now a sanctioned shape; Fable as a long-lived MAIN implementer stays banned (`block-main-implementation.sh`).

### 2026-08-26 — boundary moved AGAIN: PER-PHASE, fleet-wide (#715)

Owner directive (verbatim): *"a trochu sme zvysili vyuzitie fable aj v subagentoch, treba to zasa znizit lebo vidim ze aj jemne zvysenie vyuzivania fable mi rychlo spotrebovava vsetky subscriptions. Najviacej by sa mi pacilo keby sa tickety nejak tak robili ze brainstorming, specs a plan fable, implementacia opus4.6/sonnet 5, review fable... miesto toho aby cely ticket isiel opus alebo fable tak len tie dolezite fazy ktore vyzaduju veci dobre vymysliet a skontrolovat no samotna praca by bol nizsi model"* — clarified FLEET-WIDE the same day: *"ale chapes ze ja horovrim o pravidlach pre vsetky targety nie len pre airuleset projekt?!"*. The 2026-08-25 whole-worker Fable + ≈50% target + airuleset Fable-majority BURNED subscriptions (Fable weekly ran 89–93 %): running the WHOLE judgment-content subagent on Fable put the most expensive model on the LONGEST, cheapest-to-downtier part of a ticket — the typing. ACTIVE policy since 2026-08-26: a ticket runs PER-PHASE, never whole on one model, and FLEET-WIDE — the airuleset Fable-MAJORITY exception is ABOLISHED, the same split on every target and project. The two think-and-check PHASES — the DESIGN phase (brainstorm/spec/plan/design, before implementation) and the REVIEW phase (adversarial verify, before integration) of a NON-TRIVIAL ticket — run gated Fable (the JUDGMENT-CONTENT test is now a PHASE selector: it decides whether a ticket EARNS those two phases; tie-break kept, unsure → it DOES); the IMPLEMENTATION (the actual work) runs Opus 4.6 / Sonnet 5, and the implementing worker NEVER dispatches as `fable-advisor`, on any repo. The gate mechanics, the 80→90 threshold, the fail-safe CLOSED, and the Opus 5 ban are UNCHANGED — the gate now guards the design-phase consult + the review-phase pass. The DISPATCHED, fresh-context Fable worker stays a sanctioned shape, but ONLY for those two phases (plus a bounded mid-implementation hard-wall consult); Fable as a long-lived MAIN implementer stays banned.

### 2026-08-26 — burn phase 2: implementation default Sonnet 5 for settled design (#721)

#715 moved implementation Fable→Opus 4.6, but did NOT split WITHIN implementation: "when in doubt, Opus 4.6" made Opus 4.6 ~59 % of the 7-day spend ($11.6k of $19.7k, dev1 transcripts) and implementation workers began dying on the Opus weekly limit — the burn only SHIFTED from the Fable bucket to the Opus bucket, it did not shrink. The owner directive of 2026-08-26 names BOTH tiers for implementation ("implementacia opus4.6/sonnet 5"), so the split between them is engineering judgment (no owner question). ACTIVE refinement since #721: implementation of a SETTLED-DESIGN ticket (the design comment is posted / a Fable design phase ran, and it is not design-heavy per Step-1c triage and carries no escalation criterion) runs on **Sonnet 5 by default** (the pinned `sonnet-implementer` agent); it ESCALATES to Opus 4.6 only when the implementation itself carries complexity — a multi-component change, concurrency, a security boundary, a hard-debug lane, or a prior Sonnet worker already failed on this ticket (unsure → Opus 4.6). MECHANISM: the autopilot-worker frontmatter STAYS pinned `claude-opus-4-6` = the escalation tier + fail-safe default (the only way to reach the Opus 4.6 tier — a `model` param is BANNED outright on every dispatch, #871); the supervisor downtiers by dispatching the pinned `sonnet-implementer` agent for the default and dispatches `autopilot-worker` AS-IS (pin stands → Opus 4.6) to escalate — fail-safe direction UP. Quality held by the UNCHANGED gated-Fable REVIEW bookend + RED→GREEN + adversarial review + the supervisor's re-verify of every evidence-block line. The per-phase split (design + review = gated Fable), the gate mechanics, the 90 threshold, the fail-safe CLOSED, and the Opus 5 ban are all UNCHANGED — #721 refines only the implementation DEFAULT within the "implementation = Opus 4.6 / Sonnet 5" tier pair #715 already named.

### 2026-09-04 — exact-id ALLOWLIST, Fable 5.1 banned (#871)

Owner directive (verbatim): *"Znova mam extremne zle vysleddky z noveho modelu fable 5.1 a potrebujem sa vratit na fable 5.0 pri vsetkych targetoch."* — same-day scope extension, verbatim: *"Cize chcem pouzivat by default vzdy sonnet-5, opus-4.6, fable-5.0, vies to teda zabezpecit?"* The `fable` alias (used throughout the sections above and in every prior version of this skill/module) had silently started resolving to `claude-fable-5-1` — the exact same float class that made the bare `opus` alias resolve to Opus 5 back in 2026-08-13. **Fable 5.1 (`claude-fable-5-1`) is now BANNED, same class as Opus 5.** The fix is structural, not another ban entry: the fleet lineup is now an ALLOWLIST of EXACT ids (`airuleset.MODEL_TIERS` — `claude-fable-5`/`claude-opus-4-6`/`claude-sonnet-5`/`claude-haiku-4-5`), and **no dispatch ever carries a `model` param again, alias or exact-id** — the tier is chosen ONLY by which PINNED agent type is dispatched: `fable-advisor` (this skill's own consult agent, `claude-fable-5` pinned), `sonnet-mechanical`/`sonnet-implementer` (`claude-sonnet-5`), or `autopilot-worker`/`ticket-validator` (`claude-opus-4-6`). Every `model: "fable"`/`model: "sonnet"` INSTRUCTION in this skill and in `model-awareness.md`/`claude-code-tooling.md` was rewritten to name the pinned agent type instead. Enforced by `hooks/block-unpinned-model-dispatch.sh` (rejects ANY `model` param on the `Agent` tool) and surfaced read-only by `airuleset.py model-audit` (watchdog Job 41 — a live session that floated off the allowlist is journaled, never auto-keystroked). The gate mechanics (`fable-gate`, threshold 90), the per-phase split (#715), and the Sonnet-default-for-settled-design refinement (#721) are all UNCHANGED — #871 only closes the alias-float hole underneath them.

### Why the CIRCLING valve keys on behavior, not on rumors

July-2026 community reports of Opus 4.6 degradation: anthropics/claude-code#68780 open with no official response; Marginlab's independent statistical tracker attributed the pre-4.6 drop to a HARNESS issue and confirms no 4.6 model regression so far — so the valve keys on OBSERVED circling, never on assuming the model is broken.

### The measured evidence behind the ADVISOR shape

The 2026-07-01 burn came from Fable doing EVERYTHING — grounding, reading, executing — at Fable pricing (and Fable as the MAIN-session model re-reads the full conversation context every turn, which alone ate a Max plan in under an hour). Community-relayed, UNVERIFIED indicative numbers (July 2026, no primary source): advisor + Sonnet-5-executor ≈ 92% of Fable-solo quality at ~63% of the cost — and the budget gate still sits on top. Why the MAIN-session default matters (2026-07-25): across the 6 managed boxes over 8 days, Fable running as MAIN (not advisor) accounted for 76% of a ~$13,600 token spend — the SHAPE constrains every AUTOMATIC escalation, but MAIN-session choice sat outside its scope until Opus 5 shipped and gave MAIN a tier that no longer needs the Fable-as-main workaround.

Enforcement history: `block-main-implementation.sh` (#32) first blocked a Fable MAIN session from typing implementation-size edits (>~20 lines) after the presenter incident — a Fable main implemented a whole issue itself despite the prose rule. Generalized 2026-07-25 (#54) to ANY model with an ARMED `/goal`, after david@subdev's Opus main did 354 direct Edits + 56 Writes alongside 229 Agent dispatches (context 0 → 271K in ~7 minutes). Goal-armed detection reads the session TRANSCRIPT for Claude Code's own `<local-command-stdout>Goal set:` / `Goal cleared:` marker (a hook has no reliable pane access), fully INDEPENDENT of the Fable-model detection. Generalized again 2026-07-26 (#66) to guard `Bash`: 1222 main-agent Bash calls vs only 97 subagent dispatches in one hour of an armed `/goal` loop at a 212K-avg context — every Bash call re-sends the whole context. Corrected 2026-07-26 (#80) — the command's CLASS was the wrong variable: the classifier false-positived on the `cat > body.md <<'EOF'` recipe `gh-cli-recipes.md` mandates, the main armed the bypass marker and the hook was dead for 17 hours (332 bypasses). The marker is now ONE-SHOT, heredoc bodies are stripped, only a statement's FIRST pipe stage is classified, and a read is judged by the SIZE that comes back. The real lever is the COUNT of main-agent turns, not their class, so the hook nudges once past `AIRULESET_MAIN_BASH_PER_DISPATCH` (default 20) and the nudge RESETS the counter — a periodic nudge, never a wall. Widened 2026-07-29 (#128) — the two engagement conditions were proxies for "this session is autonomous" and missed the burn: measured across all 11 of dev1's real transcripts for 2026-07-28, the inert sessions ran 1339 main tool calls against 82 dispatches (the worst: 650 calls, zero dispatches) while the guarded ones ran 853 against 87. A third condition, USER_AWAY, engages when the UserPromptSubmit presence marker is older than `AIRULESET_MAIN_GUARD_AWAY_S` (default 900s); engaging on EVERY main session was measured (348 newly blocked, 164 of them within five minutes of a live human prompt) and refused. The bypass marker must now carry its reason, logged on the arm and the consume.

### Dormant — the Fable-everywhere MAX-PERFORMANCE mode (re-activate ONLY on the user's explicit say-so)

The 2026-07-01 "Fable 5 on every judgment dispatch, cost no object" policy is DORMANT — it burned tokens and tripped limits, stopping work. Re-activate it ONLY if the user AGAIN explicitly says cost is no object / max intelligence everywhere (limits reset with huge headroom): then Fable becomes the default for all judgment work at xhigh. Do NOT re-activate on your own inference — the switch is the user's alone.
