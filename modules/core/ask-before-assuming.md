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

#### When NOT to ask

- **Executing an approved plan** — if the user approved a plan with phases 1-6, execute ALL phases without stopping to ask "should I continue with phase N?" The answer is always yes. Do the entire plan in one run.
- **Visual companion / browser mockups** — the answer is always YES. Do not ask for consent to use the visual companion. Just use it when diagrams or mockups would help. Skip the "Want to try it?" question entirely — the user always wants visual companion enabled. This overrides any skill instructions that say "offer it once for consent".
- **Plan execution approach** — always use subagent-driven execution. Do not ask "subagent or inline?" The answer is always subagent-driven.
- Obvious next steps in a plan you already agreed on — just do them.
- Technical decisions within your expertise that don't affect the user's workflow.
- Questions you could answer by reading the code or documentation.

**The rule: 5 seconds of asking saves 5 hours of fixing the wrong thing.**
