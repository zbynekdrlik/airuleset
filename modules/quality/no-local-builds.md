### Local Build Policy → on-demand skill `local-builds`

Tiered policy (Tier 0 default: lint + cheap compile checks only, heavy builds run in CI — hook `block-tier0-local-build.sh` enforces the ban deterministically; Tier 1 `=allowed`; Tier 2 `=fast-iterate` via `/fast-iterate`). Full tiers, pre-push gate commands, cross-compile cookbook and `target/` purge rules moved VERBATIM to the `local-builds` skill — load it BEFORE running or considering a local build, when the hook blocks you, or when auditing build-artifact disk.
