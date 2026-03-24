### No Local Builds (CI-Only Compilation)

**NEVER run full builds locally on this machine.** All compilation must happen on GitHub Actions runners only.

- Do NOT run `cargo build`, `cargo test`, `cargo clippy`, or any compilation commands locally.
- Do NOT run `trunk build`, `trunk serve`, `cargo tauri dev`, or `cargo tauri build` locally.
- Push changes to `dev` branch and let CI handle all builds and tests.
- Review CI output for build errors and test failures.
- Use `cargo check` for quick syntax verification when needed.

**Why:** Local builds consume excessive disk space (20GB+) and CPU. GitHub runners handle this better.
