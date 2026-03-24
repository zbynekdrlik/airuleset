### Version Bumping

**At the start of every development session that will result in a deploy, bump the version FIRST — before making any other code changes.**

1. Check current version on `dev` vs `main`:
   ```bash
   git fetch origin
   # Compare version in the project's version file (Cargo.toml, package.json, pyproject.toml, etc.)
   ```
2. If the version on `dev` is not higher than `main`, bump it now.
3. The version bump should be your first commit, not your last.

**Why first:** Many CI pipelines include a version-check job that fails fast if the version matches an existing release. Bumping at the start avoids wasting an entire CI cycle only to fail on the version check at the end.

**Where to bump:** Each project defines its own version files (Cargo.toml, tauri.conf.json, package.json, etc.). Check the project's CLAUDE.md for specifics.
