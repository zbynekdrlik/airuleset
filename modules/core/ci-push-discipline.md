### CI Push Discipline

**Every push to `dev` triggers a CI run that may take 15-20 minutes. Wasted CI runs cost time and delay feedback.**

Before pushing:

1. **Review ALL code holistically** — do not push one fix at a time. Read through your changes, check for formatting, lint issues, dead code, and test gaps. Batch everything into one clean commit.
2. **Check if CI is already running**: `gh run list --branch dev --status in_progress --limit 3`
3. If runs are in progress for old commits, they will be cancelled by your push (concurrency groups). This is expected — but only if you have genuinely new changes to push.

After pushing:

1. Verify stale runs were cancelled: `gh run list --branch dev --limit 5`
2. Identify the LATEST run (triggered by your push) and monitor ONLY that one.
3. If you see duplicate runs (push event + pull_request event), both must pass — monitor both.

**Anti-patterns to avoid:**

- Pushing 5 small "fix CI" commits in a row — each one cancels the previous run before it finishes.
- Spotting a typo mid-CI-run and immediately pushing a fix — wait for the run to fail first, then fix ALL issues at once.
- Claiming "CI keeps getting cancelled" when you are the one causing the cancellations by pushing.

**The rule: one push, one CI cycle, monitor to completion.** If CI fails, collect ALL failures, fix them in ONE commit, push ONCE, monitor again.
