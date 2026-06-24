---
name: playbook-cleanup
description: One-time per-project consolidation — consolidate / tidy project knowledge / one-time playbook cleanup. Moves scattered procedures/gotchas into the right stores and produces a before/after summary.
user-invocable: true
---

# Playbook Cleanup

A one-time consolidation skill for a project whose knowledge is scattered across memory,
`CLAUDE.md`, and ad-hoc notes. Run it once to bring the project into the boundary-correct shape
described by `project-playbook-maintenance.md`; thereafter use `playbook-review` after every ticket.

## Step 0 — check `dev` is clean FIRST (don't ride an unrelated PR)

In the two-branch model there is exactly ONE `dev`→`main` PR at a time, so anything you commit to
`dev` rides whatever PR is already open. Before consolidating:

```bash
git -C <repo> fetch origin
git -C <repo> rev-list --count origin/main..origin/dev   # how far dev is ahead
gh pr list --repo <owner/name> --base main --head dev --state open
```

- **`dev` clean (0 ahead, no open PR):** ideal — the cleanup becomes its OWN dedicated `dev`→`main` PR.
- **`dev` has unmerged feature work / an open PR:** the cleanup WILL ride that PR. Do NOT silently
  mix it in. Either (a) wait for the open PR to merge, then run on a clean `dev`; or (b) proceed only
  if you keep the cleanup as a SINGLE config-only commit (no source files) AND update the open PR's
  description to note it now also carries the playbook consolidation. Surface it to the user — never
  bury a consolidation inside an unrelated PR undescribed.

## Step 1 — Audit all three stores

Read everything currently held for this project:

1. **Memory** — `~/.claude/projects/<proj>/memory/` files: list every item (auto-memory entries, manual notes, procedure-like content).
2. **Project `CLAUDE.md`** — read the full file; note line count, sections present, anything beyond global `@import` lines and a `## Playbook router` block.
3. **Existing project skills** — scan `.claude/skills/*/SKILL.md` in the project; note what areas are already covered.

Record counts: number of memory files / entries, `CLAUDE.md` line count, existing skill files.

## Step 2 — Route each item to the correct store

Apply the routing rule to every item found in Step 1:

| Item type | Correct destination |
|---|---|
| Reusable procedure, step sequence, gotcha, non-obvious pattern | `.claude/skills/<area>/SKILL.md` in the project |
| Always-apply project rule (every ticket, not area-specific) | Project `CLAUDE.md` — rules section only |
| User preference, transient note, session-specific observation | memory — ONLY for genuine personal prefs |
| Cross-project universal discipline | Global airuleset — out of scope, do NOT touch |

**Procedures NEVER live in memory.** Any memory entry that is actually a procedure (how-to, command sequence, API gotcha) gets **moved** to `.claude/skills/<area>/SKILL.md` and **deleted** from memory.

**Order matters — never lose knowledge:** write the procedure into `.claude/skills/<area>/SKILL.md` FIRST and confirm it's preserved there; delete the memory entry ONLY after that. Never delete-then-recreate.

### Writing / updating `.claude/skills/<area>/SKILL.md`

- Existing area skill → append or update the relevant section.
- New area → create `.claude/skills/<area>/SKILL.md` AND add one line to the `## Playbook router` section of the project `CLAUDE.md`:
  ```
  - <area> → load `.claude/skills/<area>`
  ```

### Shrinking `CLAUDE.md`

The project `CLAUDE.md` should contain ONLY:

1. Global `@import` lines (e.g. `@~/devel/airuleset/modules/…`).
2. A `## Playbook router` block (≤ ~10 lines — one line per active skill area).
3. Always-apply project rules that must fire on EVERY ticket (not area procedures).

Move everything else to the appropriate skill file. Delete duplicates of global rules.

## Step 3 — Dedup and trim the router

After moving items:

- Remove entries that restate global airuleset discipline.
- Consolidate duplicate advice.
- Keep the `## Playbook router` ≤ ~10 lines.

## Step 4 — Commit via the project's two-branch flow

This cleanup touches only agent-config files (`CLAUDE.md`, `.claude/skills/`, memory) — it is
**in-lane agent-config work**, not project source. Commit it on `dev` and open a PR to `main`
following the project's normal two-branch workflow.

Example commit message:
```
chore(playbook): consolidate scattered knowledge into skill files
```

## Step 5 — Output the before/after summary

Emit a summary so the consolidation is visible:

```
## Playbook cleanup — before/after

| Store | Before | After |
|---|---|---|
| memory entries | N | M |
| .claude/skills/ files | A | B |
| CLAUDE.md lines | X | Y (delta: -Z) |

Skills created: <list>
Items moved from memory → skills: <list>
Items moved from CLAUDE.md → skills: <list>
router updated: <yes/no, new entries>
```

The `before/after` counts are the primary audit signal — a successful cleanup shows fewer memory
procedure entries, more skill files, and a shorter `CLAUDE.md`.
