### Coverage Thresholds

**Context gate — related rules you MUST also apply:**
- `test-strictness.md` — no skipped tests, no mocks of internal code
- `mutation-testing.md` — coverage proves execution, mutation testing proves verification

Test coverage must not decrease. Coverage is enforced in CI — not optional, not advisory.

#### Rust projects: cargo-llvm-cov (MANDATORY)

Every Rust project CI MUST include a coverage job using `cargo-llvm-cov`:

```yaml
- name: Coverage
  run: |
    cargo install cargo-llvm-cov
    cargo llvm-cov nextest --fail-under-lines ${{ vars.COVERAGE_THRESHOLD || '60' }}
```

- `--fail-under-lines` — CI fails if line coverage drops below threshold
- Default threshold: 60%. Set per-project via GitHub Actions variable `COVERAGE_THRESHOLD`
- New code must have ≥80% coverage. The threshold ratchets up over time — never down.
- Output LCOV for CI dashboards: `cargo llvm-cov nextest --lcov --output-path lcov.info`

#### When adding coverage to a project for the first time

1. Run `cargo llvm-cov nextest` locally to see current coverage
2. Set `COVERAGE_THRESHOLD` to current coverage minus 2% (give room for fluctuation)
3. Add the CI job
4. Ratchet up the threshold as tests improve — never lower it

#### Rules

- Never remove tests to lower coverage. Replace obsolete tests with better ones.
- Maximum allowed drop per PR: 1%.
- A PR that lowers coverage without adding replacement tests is NOT mergeable.
