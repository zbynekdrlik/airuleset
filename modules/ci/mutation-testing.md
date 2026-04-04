### Mutation Testing (Test Quality Gate)

**Line coverage proves tests execute code. Mutation testing proves tests VERIFY behavior.** A test suite with 100% coverage but 4% mutation score catches almost no real bugs.

#### What it does

Mutation testing changes code slightly (`>` to `>=`, removes a `return`, swaps `+` to `-`) and re-runs tests. If tests still pass after the mutation → **the test is weak** and doesn't actually verify that behavior.

#### When to add mutation testing to a project

Every Rust or TypeScript project with E2E tests SHOULD have mutation testing in CI. Add it when:
- Setting up a new project's CI pipeline
- A feature ships broken despite green CI (proves tests were shallow)

#### Rust: cargo-mutants

```yaml
# CI job — only test code changed in the PR
- name: Mutation testing
  run: |
    cargo install cargo-mutants
    git diff origin/main...HEAD > pr.diff
    cargo mutants --in-diff pr.diff --timeout 60
```

`cargo mutants` exits non-zero if ANY mutant survives (= weak test). `--in-diff` limits to PR changes only (fast).

#### TypeScript: StrykerJS

```json
{
  "mutate": ["src/**/*.ts", "!src/**/*.test.ts"],
  "thresholds": { "high": 80, "low": 60, "break": 50 }
}
```

`break: 50` means CI fails if mutation score drops below 50%.

#### The rule

If mutation testing reveals surviving mutants, the feature is NOT done. Write better assertions that catch the mutations, then push again.
