### Test Strictness

Zero tolerance for test shortcuts:

- **NO `#[ignore]`** — Every test runs, every time. No conditional compilation that disables tests.
- **NO false positives** — No `assert!(true)`, no empty test bodies, no tests that pass without exercising real code. Every assertion must verify actual behavior.
- **NO skipped tests** — CI output must show zero ignored, zero filtered tests.
- **NO mocking real code** — Mocks are ONLY acceptable for external network services. Internal code paths must use real implementations.
- **NO no-op test jobs** — If a CI test job cannot execute real tests, it MUST fail, not silently pass. A green CI means every test actually ran.
- **NO dismissing CI failures** — Never label a failure as "flaky" or "pre-existing" to justify ignoring it. Every failure must be investigated and fixed.

Tests must verify **behavior**, not just rendering. Every bug that reaches production gets a regression test.
