# Rust + Windows deployment profile
# Extends universal with Rust-specific rules and Windows deploy modules.

@include universal.profile

# Additional modules for Rust + Windows projects
modules/ci/no-local-builds.md
modules/ci/security-audit.md
modules/deploy/ssh-deployment.md
modules/deploy/windows-desktop-session.md

# Rules (symlinked into project .claude/rules/)
rules/rust-strictness.md
rules/rust-no-local-builds.md
rules/windows-deploy.md
