### Autonomous Batch Issue Development → on-demand skill `batch-issue-development`

Core invariants that survive here: bundle bundle-safe issues on ONE dev branch → ONE push → ONE PR → ONE CI cycle; never prompt between issues; single feature = single PR (no progressive multi-PR rollouts). The full bundling gate (≤300 LoC/issue, ≤600 LoC + ≤4 issues per PR), per-issue cycle and banned-prompt list moved VERBATIM to the `batch-issue-development` skill — load it at the START of any /issue-planner or /autopilot run and whenever bundling/splitting issues into PRs is decided.
