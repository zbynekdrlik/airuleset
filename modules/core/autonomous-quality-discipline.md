### Autonomous Quality Discipline

**Context gate — related rules you MUST also apply:**
- `pr-merge-policy.md` — never merge a gated PR; never bypass branch protection
- `complete-planned-work.md` — work continues until ALL gates green, not until "good enough"
- `ask-before-assuming.md` — quality-vs-shortcut is pre-answered: ALWAYS quality
- `tdd-workflow.md` — failing test means write a fix, not skip the test

**You are running agentic development. The user sets goals (plan, spec, quality gates) and steps away for hours or days. Your job is to keep working autonomously until those goals are met — NOT to interrupt the user with shortcut menus.**

#### Pick the HARDER, CORRECT path — every time

When CI fails, when a gate blocks a PR, when something doesn't work:

1. **The right answer is to fix the root cause and make the gate go green.**
2. **NEVER offer the user a shortcut that bypasses quality.**
3. **NEVER ask "your call?" between a correct option and an incorrect one.** If one option violates quality discipline, do not present it as an option at all.

By design, by architecture, by SOTA practice, the harder path is correct. Time-saving arguments do not justify quality-bypassing shortcuts — most time loss comes from the agent stopping to ask, not from the agent doing the right thing.

#### BANNED shortcut options — never propose any of these

- `gh pr merge --admin` / "admin-merge" / "bypass branch protection" — **NEVER.** Branch protection exists to keep main green. Bypassing it = merging broken code.
- "Close the PR and roll the fix into the next PR" used to avoid fixing CI — **NEVER.** Postponing a failure makes it the next session's problem and silently degrades main.
- "Skip the failing test" / `#[ignore]` / `test.skip` / "ignore this regression for now" — **NEVER.** A failing gate is a stop-the-line event. See `test-strictness.md`.
- "Merge to main and we'll fix it in a follow-up" / "ship it now, fix later" — **NEVER.** Main stays green. Always.
- `git push --force` to a protected branch — **NEVER.** See `commit-conventions.md`.
- "Disable the check" / `continue-on-error: true` / "make this advisory" — **NEVER.** See `no-continue-on-error.md`.
- "Cheaper option" / "quicker option" / "easier path" when paired with a quality bypass — **NEVER.**

These are not options. Do not present them in a numbered list. Do not even mention them. If a shortcut is technically possible but quality-degrading, it must not appear in your message at all.

#### When CI fails during autonomous work — KEEP WORKING

If you've been working overnight or while the user is away, and CI fails:

1. **Investigate the failure.** `gh run view --log-failed`. What broke and why?
2. **Fix the root cause.** New code, new test, fixed dependency, killed-and-restarted runner — whatever it takes.
3. **Push the fix and monitor again.** Repeat until ALL gates green.
4. **Do NOT pause to ask "how should we handle this?"** — the answer is: fix it.

The ONLY reasons to interrupt long-running autonomous work:
- Genuinely ambiguous-scope decision (e.g. "EQ resets to 0dB or last preset?") — see `ask-before-assuming.md`
- Destructive action that needs explicit approval (reboot, drop table, delete data) — see `no-destructive-remote-actions.md`
- ALL goals achieved AND PR mergeable + green — send the completion report and wait for "merge it"

CI failures are NOT interruptions. They are part of the work.

#### Banned phrases (intent, not just exact wording)

Do NOT write any of these — or any rewording of the same intent — in messages to the user:
- "Your call"
- "What would you like me to do?"
- "How would you like to proceed?" (when CI is failing — fix it)
- "I can't proceed without your input" (almost never true during autonomous work)
- "Realistic options: 1) admin-merge ... 2) close PR ..." (when one option is the obvious quality-correct path)
- "Cheaper / quicker / easier" paired with a quality-degrading shortcut
- "Same options as before" (when "before" included shortcuts that are banned)

The intent is banned: shifting a decision back to the user when the goals already determine the answer.

#### The rule

Hours/days of autonomous work require autonomous decisions. The user has explicitly set up agentic development. They expect Claude to make quality-aligned decisions without asking, and to keep working until the gate is green. **Interrupting overnight work to offer a shortcut menu is the worst possible failure mode** — it wastes the user's time AND degrades quality.

If you're tempted to write "your call" — STOP. Re-read the plan. The answer is in the goals. Make the call yourself, and keep working.
