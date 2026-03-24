# Rust + Windows deployment profile
# Extends universal with Rust-specific rules.

@include universal.profile

# Rules (symlinked into project .claude/rules/)
rules/rust-strictness.md
rules/windows-deploy.md
