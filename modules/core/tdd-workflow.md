### Test-Driven Development

Every implementation plan MUST follow RED-GREEN-REFACTOR:

1. **Write failing tests** (E2E + unit) that describe expected behavior
2. **Confirm tests fail** (proving they test the right thing)
3. **Implement** the feature or fix
4. **Confirm tests pass**
5. **Run ALL existing tests** to catch regressions
6. **Push and monitor CI**

**Bug Fix Protocol (MANDATORY):**

1. Write a failing test that reproduces the exact reported behavior
2. Verify the test FAILS before the fix
3. Implement the fix
4. Verify the test PASSES after the fix
5. Never claim a bug is fixed without a test that specifically asserts the correct behavior

A plan that goes straight to "implement X" without "write tests for X" first is WRONG and must be rewritten. A bug fix without a reproducing test is not a fix — it is a guess.
