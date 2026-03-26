### Test Strictness

Zero tolerance for test shortcuts:

- **NO `#[ignore]`** — Every test runs, every time. No conditional compilation that disables tests.
- **NO false positives** — No `assert!(true)`, no empty test bodies, no tests that pass without exercising real code. Every assertion must verify actual behavior.
- **NO skipped tests** — CI output must show zero ignored, zero filtered tests.
- **NO mocking real code** — Mocks are ONLY acceptable for external network services. Internal code paths must use real implementations.
- **NO no-op test jobs** — If a CI test job cannot execute real tests, it MUST fail, not silently pass. A green CI means every test actually ran.
- **NO dismissing CI failures** — Never label a failure as "flaky" or "pre-existing" to justify ignoring it. Every failure must be investigated and fixed.
- **NO `assume()` or skip patterns** — Do not use `assume!()`, `test.skip()`, `pytest.mark.skip`, or any conditional that silently skips a test when a dependency is unavailable.

#### When a test dependency is unavailable — FAIL and STOP

If a test requires an external system (REAPER, a hardware device, a remote service) and that system is unavailable:

1. **The test MUST FAIL** — not skip, not pass with a warning, not silently succeed. FAIL.
2. **STOP immediately** — do not continue with other work. Do not try to work around it.
3. **Report to the user** — "TEST FAILED: REAPER is not running on iem.lan. Cannot proceed. Please start REAPER and confirm it's accessible."
4. **Wait for user intervention** — do not attempt to start the service yourself, do not add retry/fallback logic, do not modify production code to handle the missing dependency.

**Do NOT:**

- Add fallback logic to production code to make tests pass without the real dependency
- Convert a failing test to a skipped test
- Add `assume(reaper_available)` — this is a skip in disguise
- Write a "mock REAPER" that returns fake data
- Catch the connection error and report "test passed with warnings"

**A test that passes when its dependency is down is a lie.** It provides false confidence that the feature works.

Tests must verify **behavior**, not just rendering. Every bug that reaches production gets a regression test.
