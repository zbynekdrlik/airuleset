---
name: regression-test-first
description: Every bug fix needs a RED-before-GREEN regression test in the correct commit order, in the same PR. Load before fixing any bug, and when writing the completion report for a bug-fix PR.
user-invocable: false
---

### Regression Test First — Bug Reports Are Missing-Test Reports

**Context gate — related rules you MUST also apply:**
- `tdd-workflow.md` — bug fixes: strict test-first (RED before GREEN); features: tests mandatory, order flexible
- `test-strictness.md` — no `#[ignore]`, no shallow tests, no mocks of internal code
- `e2e-real-user-testing.md` — UI bugs need Playwright tests, not curl
- `complete-planned-work.md` — bug fix without locking test = incomplete

**Every user-reported bug is proof that a test was missing.** Before fixing the bug, write a test that fails because of the bug. Then fix it. The test then locks the fix in place — the same regression cannot return without CI catching it.

#### The protocol — commit ORDER matters, not just presence

When you fix a bug, your git history MUST contain TWO commits in this order:

1. **RED commit (test first)** — adds a test that asserts the correct behavior. Run the test BEFORE the fix exists, confirm it FAILS. The test commit message starts with `test:` or includes `[red]`.
2. **GREEN commit (fix second)** — modifies source code to make the test pass. The fix commit message starts with `fix:` or includes `[green]` and references the issue (`Closes #N`).

Both commits ship in the SAME PR. Order is non-negotiable — `git log --oneline` must show test commit BEFORE fix commit. If they're in the wrong order, the test was written AFTER the fix → that test was never RED → it never proved it catches the bug.

#### Why this matters

A test written after the fix is a happy-path test. It exercises code that already works. It does NOT prove it would catch the bug if the bug returned. Six months later, a refactor reintroduces the same bug — and your "regression test" passes anyway because it was never tuned to fail on the bug condition.

A test written BEFORE the fix is a real regression guard. You watched it fail. You watched it pass. That test is now a permanent guard against the bug coming back.

#### What counts as a "bug fix" (triggers this rule)

- Commit message contains: `fix(`, `fix:`, `bug:`, `bugfix:`, `regression:`, `hotfix:`, `patch:`, `repair:`
- Commit body contains: `Closes #N`, `Fixes #N`, `Resolves #N` where the GitHub issue is labeled `bug` / `regression` / `defect`
- User explicitly described the work as fixing a bug, defect, or regression
- Issue title contains: `bug`, `broken`, `not working`, `regression`, `crashes`, `error`, `fails`

#### What's NOT a bug fix (this rule doesn't apply)

- New feature work — covered by general `tdd-workflow.md` (still need tests, just not "RED before fix" specifically)
- Refactors with no behavior change — covered by `architecture-first.md`
- Doc changes, comment fixes, cosmetic style — no test needed

#### `[no-test]` bypass — strict syntax + audit log

The pre-push hook accepts `[no-test: <reason>]` to skip the test gate. Bare `[no-test]` is no longer accepted. The reason MUST explain why a test is not feasible (typical valid reasons: "config-only change, no logic", "release tag", "auto-generated file"). Every skip is appended to `~/devel/airuleset/audits/no-test-skips.log` with timestamp + project + commit SHA + reason. Review the log periodically — if the same project keeps skipping, that's a TDD-discipline regression.

NEVER use `[no-test: …]` to skip a bug fix whose logic CAN be unit-tested. If a bug exists, a test exists that catches it. If you can't figure out how to write that test, stop and ask the user — don't bypass the gate.

**One sanctioned category: a CI-YAML conditional-logic fix** (a GitHub Actions `if:` / `needs.job.result` expression) has no realistic unit-testable surface outside a real workflow run — use `[no-test: ci-yaml conditional logic, not unit-testable outside a real run]` ONLY when the workflow file already carries its own self-check step (a `run:` block asserting the exact condition, so the regression guard IS running on every future push — just not as a `#[test]`/`def test_`-shaped unit test). This is deliberately a documented bypass category, not a new pattern-matcher: this repo has shipped a wrong CLASSIFIER fix three times already (#80, #91, #96) — widening the hook's own test-detection regex to recognize YAML self-checks would be a fourth surface for the same class of bug, for a case that already has a safe, honest, logged escape hatch (#41).

#### Completion-report evidence (required for bug-fix PRs)

When the PR fixes a bug, the completion report MUST include:

```
✅ Regression test: <test_path>:<line> — RED on <test_commit_sha>, GREEN on <fix_commit_sha>
```

Stop hook enforces this — completion reports for bug-fix PRs missing this line are blocked.

#### Anti-patterns (all rewordings apply)

- "Fixed the bug, added a quick test to confirm" — **WRONG.** Test must come FIRST. "Confirming a fix" is not regression-locking.
- "Wrote both in one commit" — **WRONG.** Order must be visible in git history. Two commits.
- "The existing test now exercises the fixed code path" — **WRONG.** That test passed before AND after the fix → it doesn't prove anything about the bug.
- "Test was too hard to write, just shipped the fix with `[no-test]`" — **WRONG.** A bug fix without a test is a guess. If the test is hard, ask for help.
- "Closes #N" with NO test changes in the diff — **WRONG.** The pre-push hook will block it. The stop hook will block the completion report.
- "We have integration coverage, no unit test needed" — **WRONG.** Write the most direct test that fails on the bug condition. Integration coverage that didn't catch the bug originally won't catch it next time either.

#### The principle

**Every recurring bug is a TDD failure that already happened.** The user's pain — "regressions still repeat, moving forward reliably is hard" — is direct evidence that bugs are being fixed without locking tests. RED-before-GREEN, in commit order, in the same PR, every time. No bypasses for "small" or "obvious" fixes — those are exactly the ones that come back.

Applies to all rewordings and semantic equivalents of the patterns above.
