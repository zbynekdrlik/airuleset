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

#### Single feature = single PR (NEVER propose progressive multi-PR rollouts)

A single feature is ONE PR. Schema + module + route + UI + tests + verification all ship together. The user runs MVP-stage projects (`mvp-philosophy.md`) — there is no enterprise change-management process to satisfy.

**BANNED rollout pattern (the "progressive split"):**

```
PR1: migration v16 + new column + admin toggle. NO route yet. Ship + verify.
PR2: backend module behind disabled flag. NO real device. Ship + verify.
PR3: route + UI + E2E. Enable in prod.
PR4: enable feature for first batch of users.
```

This is WRONG. It is four review cycles, four CI runs, four merge approvals, four deploys for one feature. The user explicitly rejected this pattern. **Combine into ONE PR with all code changes.** Production rollout (env vars, user enablement, allow-list) is configuration, NOT a code PR.

**Banned justifications (intent — all rewordings apply):**

- "Each PR is independently revertable" — `git revert` works on any commit; you don't need separate PRs for revertability
- "Ship and verify schema first" — CI verifies schema in test env; production deploy verifies it on prod; one PR is enough
- "Behind a disabled flag for safety" — flags are config, set them after merge
- "Stage-and-verify" / "deploy in phases" / "incremental delivery" / "progressive rollout" — none of these justify splitting code PRs for an MVP feature
- "Easier to review in smaller pieces" — for trivial code, ONE PR with a clear diff is easier than 4 PRs with cross-PR context
- "Reduces blast radius" — feature flags + dev/main two-branch already handle blast radius; multiple PRs do not

**Acceptable splits (rare, real reasons only):**

- **Live-DB schema migration that requires backfill** before code reads the new column. The migration PR ships first, the backfill runs, then the code-using-column PR ships. This applies ONLY when backfill is non-trivial AND the project has real production data on a live DB. NOT for MVP projects with no users yet.
- **Third-party prod-credential rotation** that must propagate before code uses it. Config PR (env vars in GitHub Secrets) lands first, code PR follows. Even here, the code PR is ONE PR — not "module PR" + "route PR" + "enable PR".
- **Genuinely independent issues from `/issue-planner`** that happen to have ≥1 fail the bundling gate (per the gate criteria above).

**Default decision:** schema + module + route + UI + tests + E2E = ONE PR. If you find yourself drawing a 4-PR rollout diagram for a single feature, STOP — collapse it.

#### Banned phrases (single-feature multi-PR rollout)

NEVER write any of these or rewordings when designing a single feature's delivery:

- "Rollout plan: PR1 schema, PR2 module, PR3 route, PR4 enable"
- "Three PRs for code, one config PR"
- "Each PR independently revertable / mergeable / reviewable"
- "Ship and verify before next PR"
- "Stage-and-verify rollout"
- "Phased deployment" / "phase 1 / phase 2 / phase 3 PRs"
- "Behind a disabled flag, enable in a follow-up PR"
- Any rewording that splits a single feature's code into multiple sequential PRs

The intent is banned: turning one feature into a queue of PRs that block on user merges.

#### The principle

The user picked agentic development to AVOID being interrupted for trivial process gates. They explicitly want fewer PRs, fewer review cycles, fewer "ready to proceed?" pings. **One feature = one PR. Many small issues = one bundled PR. Splitting is the exception, never the default.** When in doubt, combine and proceed.

Applies to all rewordings and semantic equivalents of the patterns above.
