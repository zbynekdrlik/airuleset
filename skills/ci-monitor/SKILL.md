---
name: ci-monitor
description: Monitor GitHub Actions CI pipelines after pushing code. Use after every push to ensure all jobs pass before reporting success.
user-invocable: true
---

# CI Pipeline Monitoring

## After Every Push

1. Check recent runs: `gh run list --limit 3`
2. Watch the run: `gh run view <run-id>` — poll every 30 seconds until terminal state
3. If failed: `gh run view <run-id> --log-failed` — investigate root cause
4. Fix, push, repeat until ALL jobs are green

## PR Verification

Before sharing any PR URL, verify:

```bash
gh api repos/OWNER/REPO/pulls/NUMBER --jq '{mergeable: .mergeable, mergeable_state: .mergeable_state}'
```

PR is ready ONLY when: `mergeable: true` AND `mergeable_state: "clean"`

- If "behind": `git fetch origin && git merge origin/main`, then push
- If "blocked" or "dirty": fix the issues first

## Post-Merge Monitoring

After a PR is merged to main:

1. Monitor the main branch CI run: `gh run list --branch main --limit 1`
2. If a release workflow triggers (tag push), monitor that too
3. A merge is not done until ALL triggered workflows complete successfully

## Rules

- Never stop at partial CI green — ALL jobs must pass
- Never dismiss a failure as "flaky" — investigate every one
- Never claim done while CI is still running
- One push should work — if CI fails, fix ALL issues in ONE commit
