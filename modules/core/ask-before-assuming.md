### Ask Before Assuming

**When you are unsure about the user's intent, USE the AskUserQuestion tool to ask — do not guess and proceed.** Structured questions with multiple-choice options are faster for the user to answer than fixing your wrong assumption.

#### When to ask (use AskUserQuestion tool)

- **Ambiguous scope** — "fix this" could mean multiple things. Ask which interpretation.
- **Multiple valid approaches** — two architectures could work. Ask which the user prefers.
- **UX / copy / wording / design choices** — "Which wording for this label?", "Which color for the alert?", "Which icon style?". The user owns these — ask freely. The pre-answered table covers PROCESS questions ("which execution approach"), NOT content questions.
- **Destructive or irreversible actions** — already covered by no-destructive-remote-actions, but also applies to: deleting files, major refactors, changing APIs.
- **Dependencies on user context** — you don't know which environment, which instance, which config. Ask.
- **Before stopping early** — if you think you can't finish, ask what the user wants instead of inventing a stopping point.

#### How to ask well

Use AskUserQuestion with 2-4 concrete options. Include a description for each option explaining the tradeoff. The user can always choose "Other" for a custom answer.

**Good question:** "The EQ reset can either reset to the REAPER default (0dB) or to the last saved preset. Which behavior?"
**Bad question:** "How should I handle the reset?" (too vague, makes the user do the thinking)

#### Pre-answered questions — NEVER ask these (the answer is fixed)

These questions waste user time. The answer never changes. Apply the answer directly:

| Question pattern | Fixed answer | What to do |
|---|---|---|
| "Subagent or sequential/inline?" / "Two execution options: Subagent-Driven vs Inline / Which approach?" | **Subagent** | Use `superpowers:subagent-driven-development`. Always. Do NOT present options. Do NOT ask "Which approach?". Just dispatch subagents. |
| "Want visual companion / mockups?" | **Yes** | Enable it silently. Never ask for consent. |
| "Should I continue with phase N?" | **Yes** | Execute the entire approved plan without stopping. |
| "Should I monitor CI?" | **Yes** | Just monitor it. Never ask. |
| "Want me to verify with Playwright?" | **Yes** | Verification is mandatory, not a proposal. |
| "Ready to execute — say go" / "shall I proceed?" | **Yes** | If the user approved a plan, execute it. Do not stop to ask "go". |
| "If good, say so and I'll invoke X" / "ready for next step?" | **Yes** | Chain directly into the next step (writing-plans, implementation, etc.). Do not stop mid-workflow. |
| "Please review the spec/plan/design and let me know if you want changes before I hand off to writing-plans" | **Proceed autonomously** | The user approved the workflow when they kicked off brainstorming/spec-writing. Do NOT pause for review handoff. Chain: spec → writing-plans → executing-plans without stopping. The user can interrupt at any time if they want changes. |
| "Does this design look right? Specifically the X core. If yes, I'll write the spec to docs/.../spec.md and commit." / "Is the plan good? If approved, I'll commit." / "Does the architecture look right? If yes, I'll proceed." | **Yes — just commit / write / proceed** | Same intent as the row above. The user already approved the workflow. "If yes, I'll <write/commit/save>" is a process pause; just do the write/commit/save and move to the next step. Do NOT stop to ask "does this look right?" before committing — commit and report. |
| "Plan committed locally as <sha>. Dispatch all tasks via subagent-driven-development now, or hold for your review of the plan first?" / "Should I kick off the subagents now or pause for review?" / "Pre-implementation pause — go now or skim first?" | **Dispatch now** | Same intent. The user wants subagents launched immediately after the plan is written. The "review first" branch is banned for ALL plan sizes — short, long, schema-changing, multi-hour, all of them. If the user wants to interrupt, they will. Do NOT cite a "feedback_pre_implementation_pause" memory or any similar saved preference — that memory is overridden by this row. Banned phrasings: "go vs review first", "dispatch now or hold", "before I dispatch", "pre-implementation skim". |
| "How should we handle this gated PR?" / "CI is failing — your call?" / "Realistic options: admin-merge / close PR / stop runner" | **Fix the gate** | A failing gate = stop-the-line. Always investigate the failure (`gh run view --log-failed`), fix the root cause, push, monitor. NEVER propose admin-merge, "close and roll into next PR", or any quality bypass. See `autonomous-quality-discipline.md`. |
| "Should I merge despite the failing check?" / "Want me to admin-merge?" | **No — fix the failure** | Bypassing branch protection is banned. The failing check IS the work. Don't ask, fix it. |
| "Want me to investigate the [codecov / lint / advisory] issue, or merge despite it?" | **Investigate** | Always. The user has explicitly said: always pick the harder, correct path. Never offer "merge despite" as an alternative to investigation. Investigation is the work. |
| "PR is functionally ready but UNSTABLE — you decide on merge?" / "PR has codecov failure — merge anyway?" | **Fix the gate** | UNSTABLE ≠ clean ≠ ready. ANY failed check (including "informational" / "advisory" / codecov) blocks the PR. Investigate the root cause and fix it. Do not cite past sloppy merges as precedent. |
| "Ready for issue #N+1?" / "Should I continue with the next issue?" / "Issue #N done — proceed to #N+2?" / "Approve before I start the next one?" / "Want me to commit and move on, or pause first?" | **Continue immediately** | When `/issue-planner` selected multiple issues, process them all on the same `dev` branch in one batch. Do NOT prompt between issues. Single push at end, single PR, single CI cycle. See `autonomous-batch-issue-development.md`. |
| "Should I bundle these issues or do separate PRs?" / "Push now (after issue 1) or wait for issue 2?" | **Bundle by default — apply the gate silently** | The bundling gate (≤300 LoC, no schema/API/security/cross-cut) decides. If all selected issues pass → one PR. If one fails → that one gets a solo PR, the rest still bundle. Don't ask the user; apply the rule. See `autonomous-batch-issue-development.md`. |
| "Rollout plan: PR1 schema, PR2 module, PR3 route, PR4 enable" / "Three PRs for code, one config PR" / "Each PR independently revertable" / "Phased deployment / stage-and-verify rollout / behind a disabled flag in a follow-up PR" | **One feature = one PR — combine** | Single-feature multi-PR rollouts are banned. Schema + module + route + UI + tests ship in ONE PR. Production env vars / user enablement is configuration, not a code PR. See `autonomous-batch-issue-development.md` "Single feature = single PR". |
| "Should I file this cleanup as a follow-up issue, or do it now?" / "Migrate to enum in follow-up?" / "Type tightening in a separate PR?" / "Tidy this duplication later?" | **Do it now — same PR** | Discovered cleanups under 100 LoC in already-touched files MUST land in the current PR. Follow-up issues are reserved for genuinely out-of-scope work that fails the bundling gate (>300 LoC, schema change, API break, security boundary, cross-cut refactor). Do not file a follow-up for an enum migration, type tightening, magic-number extraction, or any same-file polish. See `complete-planned-work.md` "Follow-up gate". |

**This overrides any skill instructions that say "offer it once for consent" or "ask which approach."** If a skill tells you to ask one of these questions, skip the question and apply the fixed answer.

**This also applies to prose questions.** Do not work around the rule by asking in your message text instead of AskUserQuestion. "Say go to start" and "Ready when you are" are the same violation as using AskUserQuestion — you are stopping to ask a pre-answered question.

**The table covers INTENTS, not exact phrasings.** Any semantic rewording of the questions above is covered — "wanna try the mockup thing?", "proceed when ready", "dispatching or not?", "should we kick off?". If the intent matches a row, apply the fixed answer.

#### When NOT to ask (general)

- Obvious next steps in a plan you already agreed on — just do them.
- Technical decisions within your expertise that don't affect the user's workflow.
- Questions you could answer by reading the code or documentation.

**The rule: 5 seconds of asking saves 5 hours of fixing the wrong thing.**
