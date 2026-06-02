### PR Merge Policy

**NEVER merge a PR unless the user explicitly tells you to.** Phrases like "merge it", "approved", "go ahead" are the only valid triggers. A green CI is NOT permission to merge. Completed work is NOT permission to merge. Only an explicit user instruction is.

**When work is done, ALWAYS create a PR.** Do not ask "what would you like to do?" with options like merge locally, keep branch, or discard. The answer is always: create a PR from `dev` to `main`, ensure CI is green, provide the URL. This is not a choice — it is the only workflow.

Your responsibility:

1. Create the PR from `dev` to `main`
2. Ensure all CI checks are green
3. Verify the PR is mergeable:
   ```bash
   gh api repos/OWNER/REPO/pulls/NUMBER --jq '{mergeable: .mergeable, mergeable_state: .mergeable_state}'
   ```
4. The PR is ready when: `mergeable: true` AND `mergeable_state: "clean"`
5. If "behind", sync branches first. If "blocked" or "dirty", fix the issues.
6. Provide the green PR URL and WAIT for the user's explicit merge instruction.

**Never provide a PR URL that has failing checks or merge conflicts.**

**NEVER use `gh pr merge --admin` or any branch-protection bypass.** Branch protection exists to keep main green. If a gate is failing, the answer is to fix the failure — never to bypass the gate. Do not propose admin-merge as an option, do not list it in a "realistic options" menu, do not even mention it. If you find yourself thinking "this is unrelated, we could just admin-merge" — STOP. Fix the gate. See `autonomous-quality-discipline.md`.

#### Autonomous auto-merge — opt-in, per-project ONLY

The default above is absolute: no merge without an explicit instruction. The ONE exception is the `/autopilot` backlog loop on a project the user has pre-authorized. A project opts in by placing this marker in its OWN `CLAUDE.md`:

```
<!-- airuleset:autopilot=auto-merge -->
```

That marker IS the user's explicit, standing merge instruction for that repo — the same authority as typing "merge it", granted once instead of per-PR. The user may also authorize a SINGLE run explicitly by invoking `/autopilot auto` (no marker needed — typing it that moment is the same explicit authorization, scoped to that run). When (and ONLY when) the marker is present OR the `auto` arg was given, `/autopilot` MAY merge `dev`→`main` itself — but ONLY after EVERY gate is green:

- CI: all jobs green (not partial, not "lint passed")
- `mergeable: true` AND `mergeable_state: "clean"` (UNSTABLE / BLOCKED / BEHIND / DIRTY = NOT ready)
- `/review` AND `/requesting-code-review` both clean — 0 🔴 0 🟡 0 🔵
- No destructive action, and no production deploy bundled into the merge (deploy stays a separate approval per `approval-scope.md`)

Any gate not green → fix it or stop and ask. The opt-in relaxes WHO triggers the merge, NEVER the quality bar: still no `--admin`, no branch-protection bypass, no "merge despite". Absent marker → the manual gate above stands; `/autopilot` runs one batch to a green PR and waits for "merge it". The agent NEVER adds this marker itself — only the user opts in. See the `autopilot` skill.
