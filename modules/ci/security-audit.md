### Security Audit

CI MUST include a security audit step that blocks on any advisory:

- **Rust:** `cargo audit --deny warnings` or `cargo deny check`
- **Node.js:** `npm audit` with appropriate severity threshold
- **Python:** `pip-audit` or `safety check`

Known transitive dependency advisories may be explicitly ignored with `--ignore RUSTSEC-XXXX` after review. When adding ignores:

- Document WHY the advisory does not affect your code
- NEW advisories will still fail CI — only reviewed and documented ones are ignored
- Never weaken audit checks. If a check fails, fix the dependency, do not disable the check.
