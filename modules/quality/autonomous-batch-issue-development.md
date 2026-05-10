### Autonomous Batch Issue Development

**Context gate — related rules you MUST also apply:**
- `ask-before-assuming.md` — pre-answered questions table; spec/plan/proceed prompts are banned
- `complete-planned-work.md` — finish the entire batch, no stopping between issues
- `autonomous-quality-discipline.md` — keep working until ALL bundled issues green
- `pr-merge-policy.md` / `two-branch-workflow.md` — exactly ONE PR (dev→main) per batch
- `ci-push-discipline.md` — one push per batch, not one push per issue

**The user runs agentic development. Most GitHub issues are small, well-scoped, and solvable without user input. Bundle them. Iterate. Push once. ONE PR per batch — not one PR per issue.**

#### The model — batch by default

When `/issue-planner` runs:

1. Step 4 presents issues with **`multiSelect: true`** (always — no exceptions). Selecting a single issue is a deliberate user choice, not the default.
2. After selection, the agent processes ALL chosen issues on the SAME `dev` branch, in sequence:
   - Issue N: brainstorm → plan → implement → test → commit (locally, no push)
   - Issue N+1: same cycle, same branch, more commits
   - ... until all selected issues done
3. After ALL issues complete: ONE push, ONE CI cycle, ONE PR, ONE completion report.

#### Bundling gate — what's safe to batch

Bundle into one PR when EVERY selected issue meets ALL of these:

- Estimated change ≤ 300 LoC (rough — count touched lines across files)
- No DB schema change / migration
- No public API breaking change (HTTP routes, exported types, CLI flags)
- No security boundary modification (auth, permissions, secret handling)
- No cross-cutting refactor (rename across >5 files, dependency major bump, framework upgrade)
- Issues are independent (issue B does not depend on issue A's design choice being one specific way)

If ANY selected issue fails the gate → that issue gets its OWN PR, processed first or last (user's choice via AskUserQuestion). The remaining bundle-safe issues still go in one PR together.

#### Per-issue cycle inside the batch — chain without prompting

For each selected issue, run the full development cycle, but DO NOT pause between issues:

1. Read issue body + comments (`gh issue view <N>`)
2. Brainstorm approach (use `superpowers:brainstorming` if non-trivial)
3. Write plan (use `superpowers:writing-plans` if multi-step)
4. Implement + write tests (TDD per `tdd-workflow.md`)
5. Verify locally (lint, type-check; CI handles compilation per `no-local-builds.md`)
6. Commit with `Closes #<N>` in the message
7. **Immediately start the next issue.** No "ready for #N+1?" prompt. No "should I continue?" message. No completion summary between issues.

The `superpowers:brainstorming` and `superpowers:writing-plans` skills MAY ask design questions when scope is genuinely ambiguous — those are OK. They MUST NOT ask process questions ("approve before next issue?", "ready to start #N+2?") — those are pre-answered NO per `ask-before-assuming.md`.

#### When to interrupt the batch (rare)

Only stop mid-batch for these reasons:

- A selected issue turned out to violate the bundling gate after deeper investigation (schema change discovered, API break unavoidable). File a separate PR or AskUserQuestion to defer.
- Two issues produce a real design conflict (issue A's fix breaks issue B's expected state). AskUserQuestion to choose order or defer one.
- A destructive remote action is needed (per `no-destructive-remote-actions.md`).

NOT reasons to interrupt:

- "This issue's plan looks bigger than expected" → it either fits the gate or it doesn't; decide silently and proceed
- "Should I commit issue #N before starting #N+1?" → yes, always, every time, never ask
- "Want me to push now or after the next issue?" → push only after ALL selected issues done

#### Banned phrases (intent, not just exact wording)

These prompts violate the rule. NEVER write them or any rewording:

- "Ready for issue #N+1?"
- "Should I continue with the next issue?"
- "Approve before I start the next one?"
- "Issue #N done — proceed to #N+2?"
- "Want me to commit and move on, or pause first?"
- "Should I bundle these or do separate PRs?" (the gate decides — apply it, don't ask)
- "Should I push now (after issue 1) or wait for issue 2?"
- Any rewording that asks the user to gate continuation between bundled issues

The intent is banned: stopping between issues in an approved batch.

#### One PR, one CI cycle, one completion report

After all selected issues are committed locally:

1. Single `git push origin dev`
2. Monitor CI per `ci-monitoring.md` — one run, all jobs green
3. PR description lists ALL bundled issues (`Closes #12`, `Closes #15`, `Closes #18` on separate lines so GitHub auto-closes them on merge)
4. Single completion report per `completion-report.md` listing all bundled issues in the **Goal** / **What changed** lines and PR title (e.g. `Bundle: fix #12 + #15 + #18 — <one-line summary>`)
5. Wait for explicit user merge instruction per `pr-merge-policy.md`

#### Anti-patterns (all rewordings apply)

- Selecting one issue → finishing → pushing → opening PR → telling user "ready for next issue?" — **WRONG.** Multi-select up front, batch them.
- Pushing after each issue's commit — **WRONG.** Each push burns a CI cycle; bundle commits, push once.
- Asking "should I bundle?" — **WRONG.** The gate decides. Apply it silently.
- Splitting a 3-issue batch across 3 PRs because "review is easier" — **WRONG** unless one issue fails the bundling gate. Trivial parallel issues = one PR.
- Pausing between bundled issues to "let the user verify the first one" — **WRONG.** That's three CI cycles + three review rounds for work that fits in one.
- Stopping the batch when CI fails on issue #2's commit — **WRONG.** Investigate, fix, continue. CI failure inside a batch is the same as CI failure at the end — debug the root cause and keep going.

#### The principle

The user picked agentic development to AVOID being interrupted for trivial process gates. They explicitly want fewer PRs, fewer review cycles, fewer "ready to proceed?" pings. **Bundling small issues into one PR is the default model — splitting is the exception.** When in doubt, batch and proceed.

Applies to all rewordings and semantic equivalents of the patterns above.
