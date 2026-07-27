### No continue-on-error in CI → auto-loads full detail on `.github/workflows/*.yml` (`rules/no-continue-on-error.md`)

Every CI step must be binary: succeed and continue, or fail and stop the build. `continue-on-error: true` is FORBIDDEN without explicit written user approval. No informational-only steps that "check" something but always pass — if a check can't be made reliable, remove it rather than hide the gap behind a fake green checkmark.
