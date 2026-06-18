### PR Merge Policy — Auto-Merge by Default

**Context gate — related rules you MUST also apply:**
- `autonomous-quality-discipline.md` — gates are absolute; no bypass, no "merge despite"
- `approval-scope.md` — deploy follows merge automatically; destructive actions still always ask
- `post-deploy-verification.md` — verify after merge. `milestone-notifications.md` — the device pings AUTOMATICALLY only on ❓/final-✅ (mobile model); do NOT hand-fire a per-merge `reply`/`PushNotification`
- `two-branch-workflow.md` — the flow this policy governs: dev→main PRs in the user's repos

**DEFAULT: when every gate is green, MERGE — do not ask.** Waiting for a merge confirmation on a fully green PR is wasted time. When work is done:

1. Create the PR from `dev` to `main`.
2. Drive ALL gates green:
   - CI: every job green (not partial)
   - `mergeable: true` AND `mergeable_state: "clean"` (UNSTABLE / BLOCKED / BEHIND / DIRTY = NOT ready):
     ```bash
     gh api repos/OWNER/REPO/pulls/NUMBER --jq '{mergeable: .mergeable, mergeable_state: .mergeable_state}'
     ```
   - `/review` AND `/requesting-code-review` both clean — 0 🔴 0 🟡 0 🔵
   - Bug-fix PR → regression-test evidence (RED/GREEN SHAs) per `regression-test-first.md`
3. **Merge it yourself** (merge commit — no squash, no rebase). Monitor main CI + any deploy workflow to terminal state.
4. Deploy pipelines triggered by the merge run automatically — verify per `post-deploy-verification.md` (version read from the live DOM).
5. Write the completion report stating merged + deployed + verified, ending with the `✅ DONE` marker (Slovak, short) — the device ping fires AUTOMATICALLY from that marker when the user is idle (`milestone-notifications.md`, mobile model). Do NOT hand-fire a per-merge `reply`/`PushNotification` ping.

An in-the-moment user instruction ("don't merge yet", "hold this one") always overrides the default for that PR.

#### Per-project opt-out — manual merge marker

A project opts OUT of auto-merge by placing this marker in its own `CLAUDE.md`:

```
<!-- airuleset:merge=manual -->
```

Marker present → manual mode: stop at the green PR, provide the URL, end with `❓ NEEDS YOU: approve merge?`, and wait for the explicit instruction ("merge it", "approved"). The marker covers merge AND the deploy that follows it. Only the USER adds or removes this marker — never the agent. The old opt-in marker `airuleset:autopilot=auto-merge` is superseded (auto is now the default); remove it when touching a CLAUDE.md that still carries one.

#### Scope — what auto-merge covers

- Covers: the agent's own dev→main workflow PRs in the user's repos (the two-branch flow).
- Does NOT cover: foreign/third-party repos, PRs the agent didn't drive, release tags to external registries, anything outside the two-branch flow → ask first.
- Destructive actions and destructive DB ops are NEVER part of a merge (`no-destructive-remote-actions.md`, `database-migrations.md`).

#### The bar never moves (unchanged absolutes)

Auto-merge changes WHO pulls the trigger when everything is green — NEVER the bar:

- **Never merge with ANY gate red.** A failing gate = fix the root cause. Not "informational", not "advisory", not "flaky".
- **NEVER `gh pr merge --admin` or any branch-protection bypass.** If you're thinking "unrelated failure, admin-merge it" — STOP. Fix the gate. Do not propose it, do not mention it.
- **Never report "done" for a PR that has failing checks or conflicts.**
- UNSTABLE ≠ clean. "Functionally ready" ≠ ready. Applies to all rewordings and semantic equivalents.
