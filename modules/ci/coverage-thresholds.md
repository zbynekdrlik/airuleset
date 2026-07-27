### Coverage Thresholds → auto-loads full detail on CI/coverage config files (`rules/coverage-thresholds.md`)

Test coverage must not decrease — enforced by the in-CI `cargo llvm-cov --fail-under-lines` job, NOT codecov (codecov is an informational dashboard only; if used at all, set `comment: false`). Never remove tests to lower coverage; a PR that drops coverage without replacement tests is not mergeable.
