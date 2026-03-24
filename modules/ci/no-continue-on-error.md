### No continue-on-error in CI

**Every CI step must be binary: succeed and continue, or fail and stop the build.**

- `continue-on-error: true` is FORBIDDEN without explicit written user approval.
- No steps that "check" something but always pass regardless of the result.
- No informational-only CI steps. If a check cannot be made reliable, remove the step entirely rather than hiding the gap behind a fake green checkmark.
- Enforced by test-integrity checks in CI.
