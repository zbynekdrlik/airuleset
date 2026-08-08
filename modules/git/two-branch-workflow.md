### Two-Branch Workflow

**Exactly two branches: `main` (production) and `dev` (development).** No feature branches, no fix branches, no release branches.

- All work happens on `dev`. Commit directly to `dev`.
- Open a PR from `dev` to `main` when ready to release.
- No direct pushes to `main` — all changes go through a PR merge from `dev`.
- No squash merge, no rebase merge — merge commits only.
- If a branch other than `main` and `dev` exists, it is a mistake and should be deleted.
- Background daemon sessions (agent view / `claude --bg`) must work directly on `dev`: set the repo's `worktree.bgIsolation: "none"` so no `.claude/worktrees/` branches appear. These SHARE the tree, so dispatch for them stays serial — one active worker per repo — because that constraint was always about the SHARED checkout, never about the repo generally.
- **In-session `autopilot-worker` dispatch is different (#317, 2026-08-08): parallel DISPATCH via `isolation: "worktree"` is the `autopilot` skill's DEFAULT.** Each dispatched worker gets its own worktree checkout sharing only `.git`, so several workers can build independent branches concurrently without colliding on `dev` — the collision risk that forces the serial rule above only exists for a SHARED tree. What stays strictly serial either way is INTEGRATION: the supervisor merges one worktree branch at a time, runs ONE test/CI cycle, and pushes ONCE per round — never in parallel. Serial single-worker dispatch (no `isolation:`) remains the documented fallback when worktree isolation is unavailable. See the `autopilot` skill for the full round-dispatch / serial-integration protocol.

Note: Some projects use `master` instead of `main`. Follow the existing convention in each project. Some projects (e.g., Odoo) use a 3-branch model (develop/staging/main) — check the project CLAUDE.md for branch policy overrides.
