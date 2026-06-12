### Two-Branch Workflow

**Exactly two branches: `main` (production) and `dev` (development).** No feature branches, no fix branches, no release branches.

- All work happens on `dev`. Commit directly to `dev`.
- Open a PR from `dev` to `main` when ready to release.
- No direct pushes to `main` — all changes go through a PR merge from `dev`.
- No squash merge, no rebase merge — merge commits only.
- If a branch other than `main` and `dev` exists, it is a mistake and should be deleted.
- Background daemon sessions (agent view / `claude --bg`) must work directly on `dev`: set the repo's `worktree.bgIsolation: "none"` so no `.claude/worktrees/` branches appear, and dispatch serially — one active worker per repo (see the `autopilot` skill).

Note: Some projects use `master` instead of `main`. Follow the existing convention in each project. Some projects (e.g., Odoo) use a 3-branch model (develop/staging/main) — check the project CLAUDE.md for branch policy overrides.
