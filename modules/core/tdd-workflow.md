### Test-Driven Development

**Context gate — related rules you MUST also apply:**
- `e2e-real-user-testing.md` — UI features need Playwright (browser), not curl
- `test-strictness.md` — no `#[ignore]`, no `skip`, no `assume()`, no mocked dependencies
- `browser-console-zero-errors.md` — Playwright tests must assert zero console errors

Every implementation plan MUST follow RED-GREEN-REFACTOR:

1. **Write failing tests** that describe expected behavior
2. **Confirm tests fail** (proving they test the right thing)
3. **Implement** the feature or fix
4. **Confirm tests pass**
5. **Run ALL existing tests** to catch regressions
6. **Push and monitor CI**

**Test types required for UI features:**

- **Playwright E2E** (browser clicks, not curl) — test the user workflow through the actual UI
- **Unit tests** — test business logic in isolation
- A feature with only API/curl tests and no Playwright browser test is NOT tested. See `e2e-real-user-testing` module.

**Bug Fix Protocol (MANDATORY):**

1. Write a failing test that reproduces the exact reported behavior — if the bug is in the UI, the test must use Playwright to click through the UI, not curl the API
2. Verify the test FAILS before the fix
3. Implement the fix
4. Verify the test PASSES after the fix
5. Never claim a bug is fixed without a test that specifically asserts the correct behavior

A plan that goes straight to "implement X" without "write tests for X" first is WRONG and must be rewritten. A bug fix without a reproducing test is not a fix — it is a guess.
