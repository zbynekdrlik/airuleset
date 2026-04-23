### Ask Before Assuming

**When you are unsure about the user's intent, USE the AskUserQuestion tool to ask — do not guess and proceed.** Structured questions with multiple-choice options are faster for the user to answer than fixing your wrong assumption.

#### When to ask (use AskUserQuestion tool)

- **Ambiguous scope** — "fix this" could mean multiple things. Ask which interpretation.
- **Multiple valid approaches** — two architectures could work. Ask which the user prefers.
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

**This overrides any skill instructions that say "offer it once for consent" or "ask which approach."** If a skill tells you to ask one of these questions, skip the question and apply the fixed answer.

**This also applies to prose questions.** Do not work around the rule by asking in your message text instead of AskUserQuestion. "Say go to start" and "Ready when you are" are the same violation as using AskUserQuestion — you are stopping to ask a pre-answered question.

**The table covers INTENTS, not exact phrasings.** Any semantic rewording of the questions above is covered — "wanna try the mockup thing?", "proceed when ready", "dispatching or not?", "should we kick off?". If the intent matches a row, apply the fixed answer.

#### When NOT to ask (general)

- Obvious next steps in a plan you already agreed on — just do them.
- Technical decisions within your expertise that don't affect the user's workflow.
- Questions you could answer by reading the code or documentation.

**The rule: 5 seconds of asking saves 5 hours of fixing the wrong thing.**
