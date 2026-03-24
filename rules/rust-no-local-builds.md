---
paths:
  - "*.rs"
---

### Rust: CI-Only Compilation

**Do NOT run Rust builds locally.** All compilation happens on GitHub Actions:

- No `cargo build`, `cargo test`, `cargo clippy` locally.
- No `trunk build`, `trunk serve`, `cargo tauri dev/build` locally.
- Use `cargo check` for quick syntax verification only.
- Push to `dev` and let CI handle builds, tests, and releases.
- Review CI output via `gh run view` for errors.
