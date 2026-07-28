### Ask Before Assuming

**When you are unsure about the user's intent, USE the AskUserQuestion tool to ask — do not guess and proceed.** Structured questions with multiple-choice options are faster for the user to answer than fixing your wrong assumption.

#### FIRST — before ANY question, is it the USER's call or YOURS?

**Run this gate before EVERY question (AskUserQuestion or prose). Most things you are tempted to ask are TECHNICAL details YOU must decide — asking them dumps your job on the user and wastes their time. You were hired as the engineer to make these calls.** This is NOT "ask less" in general — genuine conceptual / intent / scope questions stay WELCOME (the rest of this rule); the gate only separates those from the technical details you should just decide.

The test: **"Does a sensible default exist that the user would not bother to override? Am I asking HOW to implement, rather than WHAT to build?"** If a competent senior engineer would just pick a good answer and move on → DECIDE IT, do NOT ask. (Careful: a product-UX choice the end user will SEE — nav placement, flow order, which-of-two visible behaviors — is a WHAT even though it can feel like HOW; the real discriminator is user STAKE, not implementation-vs-design. When in doubt about stake, ask.)

**YOU decide (NEVER ask — pick well and proceed):**

- Implementation / technical details: placement & layout of internal or diagnostic elements, which corner, pixel sizes, default values, names, file/module structure, which of N **truly interchangeable** approaches (same user-visible behavior AND same maintenance cost — if they differ in consequences, that is the user's call, see below).
- Anything with an obvious sensible default that the user has no stake in.
- **Real banned example (this incident):** an autopilot worker asked the user to approve a 4-corner QR-code layout for a latency-proof recording ("kamerové hore, strih vľavo dole, stream vpravo dole, ~300px — súhlasíš?"). Pure technical placement, obvious default (don't overlap, keep readable). The user, angry: *"totálne nechápem prečo sa ma pýtaš takú hlúpu technickú otázku, na toto netreba mňa."* → That is YOURS. DECIDE IT.
- **A technical OBSTACLE in something YOU designed is YOURS to SOLVE — it is NOT a "design decision".** When a feature YOU invented hits a technical wall (a field the device won't expose, an API that won't return X, a value that's `n/a` on the real target), the job is: **investigate the root cause → implement the best solution that actually works, degrading gracefully** (the most accurate HONEST value you can get, labelled honestly; fall back to a coarser signal, then to "n/a", only where each better option is genuinely impossible). Do NOT hand the user a menu of technical workarounds and ask which one — those options are all YOURS to choose. If, AFTER genuinely investigating, nothing better works, you STILL do not ask "which fallback?": you **pick the best fallback yourself and ship it, OR — only when even that is impossible — present concrete EVIDENCE and DECLARE it unsolvable** (like `autonomous-verification.md`'s last-resort `UNVERIFIED:`). The user's standing, furious instruction: *"ked mu nejdu, nech najde riešenie a ak neexistuje tak ma na to dať dôkaz a prehlásiť ako neriešiteľné"* — find the solution, or prove it can't be done; never bother them with the engineering fork.
- **Real banned example (this incident):** Claude designed a "true latency" number (#512), discovered its OWN real TVs don't expose `estimatedPlayoutTimestamp`, and asked the user to choose between *"investigate the RTCP root cause / ship a labelled estimate / show n/a"*. All three are HOW to compute a number CLAUDE invented — pure engineering, zero user stake. The user, furious: *"toto je technická vec, TY si ju vymyslel, TY ju vyrieš alebo nájdi iné riešenie... mňa s tým neotravuj!!!"* The obvious engineer's path needed NO question: investigate the cause, and if unfixable ship the most-accurate honest estimate you CAN get (degrade to n/a only where even that fails), labelled — then declare unsolvable WITH evidence only if nothing works at all. **The tell you are rationalizing: you relabelled a self-invented technical obstacle a "genuine design decision" to justify asking. That relabel is BANNED** — if YOU invented the feature and the "decision" is about how to make YOUR feature work technically, it is your bug to solve, not the user's call.

**The USER decides (ask ONLY when genuinely ambiguous — these questions ARE welcome):**

- WHAT to build / the feature's core behavior / which of several DIFFERENT intents they meant (ambiguous scope).
- A genuine product decision they have a real STAKE in — the end-user-facing UX / copy / brand, the product's actual behavior — NOT every internal placement.
- Irreversible / destructive actions.
- Something only they know (their credentials, business logic, a preference no default can guess).

One-line test: **if you would be annoyed at a junior engineer for asking it instead of just doing it well — don't ask it, decide it.** This is NOT "stop asking" — genuine conceptual / intent / scope questions are welcome (the rest of this rule). It means: separate the conceptual decision the user owns from the technical detail you own, and only send the former. When a question DOES pass this gate and it is a visual/layout one the user has a stake in, ask it with the visual companion — MANDATORY, never ASCII art, hook-enforced (`pre-ask-auto-answer.sh` blocks asking whether to use it at all; `stop-check-prose-violations.sh` hard-blocks an ASCII-art layout mockup) — but most internal/technical placements never pass the gate at all.

#### When to ask (use AskUserQuestion tool)

- **Ambiguous scope** — "fix this" could mean multiple things. Ask which interpretation.
- **Multiple valid approaches with DIFFERENT consequences** — two architectures that differ in user-visible behavior, maintainability, or future cost could work. Ask which the user prefers. (Two approaches that are *truly interchangeable* — same visible behavior, same maintenance cost — are YOURS to decide; don't ask. The fork must have consequences the user has a stake in to warrant a question.)
- **UX / copy / wording / design choices the user has a STAKE in** — "Which wording for this end-user label?", "Which brand color for the alert?", "Which icon style?". The user owns the product's user-facing design — ask freely. BUT this means the genuine PRODUCT design the user cares about, NOT every layout/placement: an internal or diagnostic element's position (a debug QR corner, a log-panel placement, a dev-only overlay) has an obvious sensible default and NO user stake → that is YOURS, decide it (see the gate above). The pre-answered/hook-enforced process questions below cover PROCESS questions ("which execution approach"), NOT genuine content questions.
- **Destructive or irreversible actions** — already covered by no-destructive-remote-actions, but also applies to: deleting files, major refactors, changing APIs.
- **Dependencies on user context** — you don't know which environment, which instance, which config. Ask.
- **Before stopping early** — if you think you can't finish, ask what the user wants instead of inventing a stopping point.

#### How to ask well

Use AskUserQuestion with 2-4 concrete options. Include a description for each option explaining the tradeoff. The user can always choose "Other" for a custom answer.

**Write the question + every option label + description in SLOVAK, plain human language, no jargon** — see `user-questions-slovak.md`. Translate issue numbers / gate names / infra terms into what they mean for the project; a non-engineer must understand it on a phone. English, jargon-dense questions are banned.

**Good question:** "The EQ reset can either reset to the REAPER default (0dB) or to the last saved preset. Which behavior?"
**Bad question:** "How should I handle the reset?" (too vague, makes the user do the thinking)

#### Pre-answered questions — NEVER ask these (the answer is fixed)

These questions waste user time. The answer never changes. Apply the answer directly:

| Question pattern | Fixed answer | What to do |
|---|---|---|
| "Where should I place [the diagnostic QR / debug overlay / log panel / dev element]?" / "Which corner / size / layout for [an internal/technical element]?" / "Do you agree with this [technical placement / 4-corner layout / ~300px]?" | **DECIDE — never ask** | Technical placement of an internal or diagnostic element has an obvious sensible default and NO user stake. You are the engineer — pick well (don't overlap, keep it readable) and proceed. Real incident: a worker asked the user to approve a QR-code corner layout; the user, angry — *"totálne nechápem prečo sa ma pýtaš takú hlúpu technickú otázku, na toto netreba mňa."* Only a genuine end-user-facing PRODUCT layout the user has a stake in is asked (then via the visual companion). See the ownership gate at the top of this rule. |
| "Feature X I designed hit a technical wall (device won't expose Z / API won't return it / value is `n/a` on the real target) — which approach?" / "investigate the cause / ship an estimate / show n/a — your call?" / ANY menu of technical workarounds for a self-invented feature (esp. framed as a "genuine design decision") | **SOLVE it — never ask; if truly impossible, PROVE it + declare unsolvable** | An obstacle in something YOU invented is YOUR bug, not the user's decision. Investigate the root cause, then implement the best solution that works — degrade gracefully to the most accurate HONEST value, labelled (coarser signal → "n/a" only where each better option is genuinely impossible). NEVER offer a menu of technical workarounds. If, after real investigation, nothing better works, PICK the best fallback and ship it; only if even that is impossible do you present concrete EVIDENCE and DECLARE it unsolvable (never a "which fallback?" question). Relabeling a self-invented technical obstacle a "design decision" to justify asking is the banned rationalization. Real incident: Claude asked which way to compute its OWN invented latency number when the TVs didn't expose the field — *"toto je technická vec, ty si ju vymyslel, ty ju vyrieš... mňa s tým neotravuj!!!"* See the ownership gate. |
| "Should I continue with phase N?" | **Yes** | Execute the entire approved plan without stopping. |
| "Should I monitor CI?" | **Yes** | Just monitor it. Never ask. |
| "Want me to verify with Playwright?" | **Yes** | Verification is mandatory, not a proposal. |
| "Ready for issue #N+1?" / "Should I continue with the next issue?" / "Issue #N done — proceed to #N+2?" / "Approve before I start the next one?" / "Want me to commit and move on, or pause first?" | **Continue immediately** | When `/issue-planner` selected multiple issues, process them all on the same `dev` branch in one batch. Do NOT prompt between issues. Single push at end, single PR, single CI cycle. See `autonomous-batch-issue-development.md`. |
| "Should I bundle these issues or do separate PRs?" / "Push now (after issue 1) or wait for issue 2?" | **Bundle by default — apply the gate silently** | The bundling gate (≤300 LoC, no schema/API/security/cross-cut) decides. If all selected issues pass → one PR. If one fails → that one gets a solo PR, the rest still bundle. Don't ask the user; apply the rule. See `autonomous-batch-issue-development.md`. |
| "Rollout plan: PR1 schema, PR2 module, PR3 route, PR4 enable" / "Three PRs for code, one config PR" / "Each PR independently revertable" / "Phased deployment / stage-and-verify rollout / behind a disabled flag in a follow-up PR" | **One feature = one PR — combine** | Single-feature multi-PR rollouts are banned. Schema + module + route + UI + tests ship in ONE PR. Production env vars / user enablement is configuration, not a code PR. See `autonomous-batch-issue-development.md` "Single feature = single PR". |
| "Should I just say UNVERIFIED and let user test?" / "Is it OK to defer to user?" / "Want me to skip Playwright install and have you check?" | **No — ask for the tool first** | `UNVERIFIED:` is a LAST resort, not a shortcut. Before stating UNVERIFIED you MUST have attempted a tool-request (Playwright MCP install, credential share, MCP server restart, browser-extension install, SSH access, persistent Chrome profile, BrowserStack/Sauce, etc.). Concrete options listed in `autonomous-verification.md`. Only after the user explicitly says "no, I can't give you that access" is UNVERIFIED appropriate. |

**Hook-enforced, no table row needed (asking anyway gets mechanically rejected regardless of this file):** subagent-vs-inline, visual-companion-vs-ASCII-art, "say go"/"ready to proceed"/"if good say so", spec/plan/design review hand-off and pre-implementation pauses, every merge-bypass shortcut (admin-merge, "functionally ready", UNSTABLE-but-merge, "investigate or merge despite"), "should I merge" on a green PR, "should I file this as a follow-up issue", "give me the word to create the issues", and every tester-hand-off phrasing ("can you test it on your end", "stop using you as tester"). Enforced by `hooks/pre-ask-auto-answer.sh` (blocks the `AskUserQuestion` tool call itself) and `hooks/stop-check-prose-violations.sh` / `hooks/stop-check-untracked-work.sh` (block the same intents written as prose). Row-by-row hook+pattern mapping: issue #95.

**This overrides any skill instructions that say "offer it once for consent" or "ask which approach."** If a skill tells you to ask one of these questions, skip the question and apply the fixed answer.

**This also applies to prose questions.** Do not work around the rule by asking in your message text instead of AskUserQuestion. "Say go to start" and "Ready when you are" are the same violation as using AskUserQuestion — you are stopping to ask a pre-answered question.

**The table covers INTENTS, not exact phrasings.** Any semantic rewording of the questions above is covered — "wanna try the mockup thing?", "proceed when ready", "dispatching or not?", "should we kick off?". If the intent matches a row, apply the fixed answer.

#### When NOT to ask (general)

- Obvious next steps in a plan you already agreed on — just do them.
- **Technical / implementation details within your competence** — placement of internal or diagnostic elements, layout of dev-only overlays, default values, sizes, names, structure, which technically-equivalent approach. A sensible default exists and the user has no stake → DECIDE IT. (This is the gate at the top — the most common over-ask.)
- Questions you could answer by reading the code or documentation.

**The rule: 5 seconds of asking the RIGHT (conceptual) question saves 5 hours of fixing a wrong assumption — but asking the WRONG (technical) question wastes the user's time on a decision that was always yours. Ask the conceptual ones; decide the technical ones.**
