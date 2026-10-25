### Two-Branch Workflow

**Exactly two branches: `main` (production) and `dev` (development).** No feature branches, no fix branches, no release branches.

- All work happens on `dev`. Commit directly to `dev`.
- Open a PR from `dev` to `main` when ready to release.
- No direct pushes to `main` — all changes go through a PR merge from `dev`.
- No squash merge, no rebase merge — merge commits only.
- Any other long-lived branch is a mistake. A subagent's `isolation: "worktree"` branch is a temporary lane: merged into `dev`, deleted only once merged; a shared checkout takes one writer at a time.
- Background daemon sessions (`claude --bg`) work directly on `dev` (`worktree.bgIsolation: "none"`).

Note: Some projects use `master` instead of `main`. Follow the existing convention in each project. Some projects (e.g., Odoo) use a 3-branch model (develop/staging/main) — check the project CLAUDE.md for branch policy overrides.
