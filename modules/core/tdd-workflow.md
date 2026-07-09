### Test-Driven Development — Calibrated

**Context gate — related rules you MUST also apply:**
- `regression-test-first.md` — bug fixes: failing test committed BEFORE the fix (RED→GREEN order, hook-enforced)
- `e2e-real-user-testing.md` (path-scoped rule — auto-loads on E2E/Playwright files) — UI features need Playwright (browser), not curl
- `test-strictness.md` — no `#[ignore]`, no `skip`, no `assume()`, no mocked internal code
- `browser-console-zero-errors.md` — Playwright tests must assert zero console errors

**Tests are the agent's verification target: every change ships with tests that can FAIL on the behavior they claim to verify, in the same PR.**

#### Bug fixes — STRICT test-first (unchanged)

RED commit (failing test reproducing the bug) BEFORE GREEN commit (the fix), same PR, per `regression-test-first.md`. Verify the test fails before the fix and passes after. A bug fix without a reproducing test is a guess. This is the best-evidenced agentic practice — never relax it.

#### Features — tests mandatory, order flexible

1. Every feature plan MUST include tests for the feature — same PR, no exceptions.
2. Test-first is RECOMMENDED; implement-then-test within the same PR is acceptable for greenfield features.
3. Tests must verify BEHAVIOR and be able to fail: no tautologies, no assertion-free tests, no tests that never call the code under test.
4. **UI features:** Playwright E2E through the real browser (the user workflow) plus unit tests for business logic. API/curl-only coverage of a UI feature is NOT tested (`e2e-real-user-testing.md`).
5. After implementing: run ALL existing tests to catch regressions, then push and monitor CI.

#### Anti-cheat hardening (ALL test work)

- **No hard-coding test cases.** Never special-case test inputs in implementation code, never return memorized expected values. If a task seems impossible or unreasonable, SAY SO instead of gaming the test.
- **Tests are read-only during GREEN.** While making a failing test pass, existing test files are read-only — never edit, weaken, or delete a test to make it pass. A genuinely wrong test gets fixed in its OWN commit with stated justification.
- Applies to all rewordings and semantic equivalents (deleting asserts, loosening tolerances, marking flaky, swapping expected values).
