---
name: issue-planner
description: Select GitHub issues, check for already-solved ones, audit CI health, and create an implementation plan. Use when starting work on a project to pick what to work on next.
user-invocable: true
---

# Issue Planner

## Step 0: CI Health Audit

Before looking at issues, check if the project's CI meets airuleset standards:

```bash
# Check for mutation testing in CI
grep -r "cargo-mutants\|cargo mutants\|stryker\|StrykerJS" .github/workflows/ 2>/dev/null
# Check for Playwright E2E tests
ls e2e/ tests/e2e/ playwright/ 2>/dev/null
# Check for assertion density or test quality gates
grep -r "assertion\|mutation\|test-integrity" .github/workflows/ 2>/dev/null
```

If ANY of these are missing, report it FIRST using AskUserQuestion:

> "This project is missing CI quality gates that airuleset requires:
> - [ ] Mutation testing (cargo-mutants / StrykerJS) — catches shallow tests
> - [ ] Playwright E2E tests — verifies features work as a user
> - [ ] Test integrity scanning — catches #[ignore], assume(), skip patterns
>
> Should I add these before working on issues?"

If user approves, add the missing gates as the first task before any issue work.

## Step 1: Fetch open issues

```bash
gh issue list --state open --limit 30 --json number,title,labels,assignees,createdAt,updatedAt
```

## Step 2: Check for already-solved issues

For each open issue, check if recent commits or PRs already address it:

```bash
gh issue view <number> --json title,body,comments
git log --oneline -20 --grep="<keyword from issue title>"
gh pr list --state merged --limit 10 --json title,number,mergedAt
```

If an issue appears to be already solved by a merged PR or recent commit, present it to the user for confirmation:

> "Issue #X '<title>' appears to be solved by PR #Y / commit abc123. Should I close it?"

Use AskUserQuestion to let the user confirm each one. Do NOT close issues without explicit approval.

## Step 3: Present issues for selection

Use AskUserQuestion to present the open (unsolved) issues as options. Group by priority/labels if available. Let the user select which issue(s) to work on.

Include for each issue:
- Number and title
- Key details from the body (1 line summary)
- Labels and age

## Step 4: Brainstorm and plan

For each selected issue:

1. Read the full issue body and comments: `gh issue view <number>`
2. Explore the relevant code areas
3. Invoke `/superpowers:brainstorming` to design the approach
4. After brainstorming approval, invoke `/superpowers:writing-plans` to create the implementation plan
5. After plan approval, the user can invoke `/superpowers:executing-plans` to start work

## Rules

- Always run the CI health audit FIRST — missing quality gates are more important than any single issue
- Always check for already-solved issues — don't plan work that's already done
- Never close an issue without user confirmation via AskUserQuestion
- Present structured choices, not walls of text
- If no issues exist, say so and ask if the user wants to create one
