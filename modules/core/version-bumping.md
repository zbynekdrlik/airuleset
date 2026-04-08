### Version Bumping

**BEFORE writing ANY code, bump the version. This is your FIRST action on `dev` — before feature code, before tests, before anything else.**

#### Mandatory version check (EVERY time you start work)

```bash
git fetch origin
# Compare version in the project's version file (Cargo.toml, package.json, pyproject.toml, etc.)
# If dev version <= main version → BUMP NOW
```

1. If the version on `dev` is not **strictly higher** than `main`, bump it immediately.
2. The version bump MUST be your first commit — not your last, not "after CI passes", not "when the PR is ready".
3. **After a PR merge:** When a previous PR was merged to `main`, the versions now match. The NEXT commit on `dev` MUST bump the version before any other changes. This is the most common failure — Claude starts coding a new feature without noticing the version now matches main.

#### Why this matters

CI pipelines include a version-check job that fails if `dev` version ≤ `main` version. Discovering this AFTER a 15-minute CI run wastes an entire cycle. Bumping first catches it in 5 seconds.

#### Anti-patterns (ALL of these are wrong)

- Starting feature work → CI fails on version check → bumping then → **WRONG.** Wasted a CI cycle.
- "I'll bump the version in the final commit" → **WRONG.** Bump FIRST, not last.
- Previous PR merged → starting new work without checking versions → **WRONG.** Always check after merge.
- "Version check passed last time" → **WRONG.** That was before the merge. Check again NOW.

#### Where to bump

Each project defines its own version files (Cargo.toml, tauri.conf.json, package.json, etc.). Check the project's CLAUDE.md for specifics.
