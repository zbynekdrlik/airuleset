---
paths:
  - "*.rs"
  - "Cargo.toml"
---

### Rust Code Quality

- `cargo fmt --all -- --check` — enforced in CI, zero tolerance for formatting issues.
- `cargo clippy --workspace --all-targets -- -D warnings` — no warnings allowed.
- **NEVER use `#[allow(dead_code)]`** — if code is not used, remove it entirely.
- No `todo!()` or `unimplemented!()` in production code.
- Max 1000 lines per `.rs` file.
- Prefer `thiserror` for library crates, `anyhow` for application crates.
