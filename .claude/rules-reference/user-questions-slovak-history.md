### User Questions — Slovak: History + Rationale

This file carries the deep prose from `modules/core/user-questions-slovak.md` that was
moved out of the always-on prefix during the #859 context diet (batch 4b, 2026-09-05).
The enforcement-core stub stays always-on; this history file preserves the full
original text verbatim. The hook-enforced template, worked examples, and structural
rules live in `skills/user-questions-slovak/SKILL.md` (auto-loaded on AskUserQuestion).

#### Original deep content (verbatim from pre-#859 module)

**The rule:**

- **Slovak — the whole thing.** Question + every option label + every option description. The ONLY English kept is the status keyword `NEEDS YOU` / `DONE` (the hooks key on it); everything the user READS to decide is Slovak.
- **Plain + human.** Explain in everyday words: (1) what is going on, (2) why you're asking / what is blocked, (3) what each choice means IN PRACTICE and its consequence (time, risk). A non-engineer must understand it on a phone with no terminal context.
- **Translate the jargon — do NOT paste it.** A raw issue number, gate name, infra term, class/exception name means nothing to the user. Say what it IS for the project. Keep a `#N` for reference (per `issue-reference-context.md`) but ALWAYS put the plain-language meaning right next to it.
- **Options = short Slovak label + plain consequence.** Each option's description says plainly what happens if chosen and the trade-off — not the technical mechanism. Lead with your recommendation and mark it `(odporúčam)`.

**Self-contained — write EVERY question for someone with ZERO context (the #1 repeated complaint):**

**The user is NOT at the terminal and has NOT read the history you printed while working.** They see ONLY the question — on a phone, cold, maybe days later, across many different projects. So the question must carry ALL the context needed to understand and decide FROM ITS OWN TEXT ALONE. Never assume the user knows what the ticket is, what you were doing, or how two things relate.

Every question OPENS with a 2-4 sentence plain-Slovak briefing, in this order:
1. **Which project + what it is** — one plain phrase: "V projekte camera-box (ovládanie kamier a OBS pre kostolný živý prenos)...". Never just "#137".
2. **What was happening / what led here** — the situation in everyday words: what you were doing, what happened, why a decision is now needed.
3. **Explain EVERY cross-reference** — any OTHER project, ticket, or component you name MUST be explained: what it IS *and* why it is relevant HERE. A bare "restreamer #255 uz to opravil" is BANNED — the user has no idea camera-box and restreamer are connected, or that restreamer even touches OBS. Spell out the link in plain words.
4. **THEN the decision** + each option's real-world consequence (time, risk), recommendation marked `(odporucam)`.

**The gate — apply to EVERY question before sending:** "Could a person who has NOT watched this terminal for a week, and does NOT know this project's internals, understand this question and decide — from the question text ALONE?" If no, it is BANNED; add the missing context and re-write. "It's all in the scrollback / I explained it earlier" is exactly the excuse this kills — the user does NOT read the scrollback and is not watching 24/7.

**Away-user delivery — the `❓` TEXT marker, never the 60-second dialog:**

Deliver a genuine away-user question as the `❓ NEEDS YOU:` / `❓ ASKED:` TEXT marker (Slovak, self-contained per above) — it has NO timeout at all (waits UNLIMITED, however long you need). The 60-second `AskUserQuestion` dialog auto-continues unanswered for an away user, so a timed-out dialog is NOT an answer — re-deliver the SAME question as the `❓` marker instead. **Owner-scoped DELIVERY (#710, 2026-08-26):** which SURFACE the `❓` marker reaches is owner-scoped — for owner **david** it is the Discord phone ping; for owners **zbynek** and **marek** it is the footer `U N` + webterm (they take questions in-session, no phone ping — the Discord delivery is SUPPRESSED for them, logged not silent). This scopes only the delivery channel: you STILL write the self-contained `❓` marker exactly as here (the away-user re-ask discipline, the two branches, the hook-enforced shape are all unchanged for every owner). The delivered block MUST open with `**Otazka — projekt ...:**`, carry bullet/numbered options, and end with exactly ONE `❓` decision line — hook-enforced (`stop-check-question-quality.sh`) whenever the user is away and this is a FIRST ask (or a re-ask after real conversation happened, per below). **A genuine byte-identical re-poke of an already-delivered, still-unanswered question is the ONE exception: write only the bare `❓ NEEDS YOU: <text>` marker line, never the full block** (#740 — see below). Full template, both re-ask branches, and worked examples live in `skills/user-questions-slovak` — load it before composing a question whenever in doubt.

**Tickets + scope — explain each, ask in SMALL parts:**

Every ticket you mention gets a short plain-Slovak explanation of what it is ABOUT — NEVER a bare number or range. Ask in SMALL parts, one decision at a time, iterate — never one sweeping answer to a pile of many different tickets. Full worked examples (the jargon to plain rewrite, the ticket-explaining rule, the small-parts rationale) live in `skills/user-questions-slovak`.

**A "U N?" / "otazky na mna?" STATUS query is answered by STARTING this step-by-step flow — never by rendering the raw `--waiting` table or a summary list of all U members (#606, owner directive 2026-08-21: "nikdy nemam dostavat sumarne informacie u vsetkych U vzdy musis ist step by step").** The `--waiting` table (`core-quals`/`slice-quals --waiting`) is MACHINE context — the owner cannot decode ticket-by-ticket asks from a compressed list. Answer it by delivering the FIRST U member as its OWN full `**Otazka — projekt ...:**` block (2-4 vety uvod co ta vec JE + preco caka na neho, moznosti s dosledkami, jedna `❓` rozhodovacia linka), one at a time, moving to the next only after the current one is answered. Hook-backstopped: `stop-check-prose-violations.sh` blocks an owner-facing turn that piles 3+ `#N ...?` per-ticket asks into one question turn.
