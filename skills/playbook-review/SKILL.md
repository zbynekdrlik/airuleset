---
name: playbook-review
description: Review the project playbook after a ticket — before the completion report. Use after completing a ticket / before writing the completion report / to capture reusable procedure or gotcha / to update project playbook / per project-playbook-maintenance rule.
user-invocable: true
---

# Playbook Review

Run this skill AFTER finishing a ticket's implementation and BEFORE writing the completion report.

## Step 1 — Reflect on the ticket diff + session

Scan the git diff, the approach taken, and any blockers hit. Look for:

1. **Reusable procedure or gotcha** — a non-obvious command sequence, a tricky API behavior, a pattern that will recur, a pitfall that cost time
2. **Stale or wrong existing playbook entry** — something in `.claude/skills/` or the `## Playbook router` that no longer matches reality
3. **Long-way-now-figured-out** — did you spend time re-deriving something the playbook should have told you? That gap is the next entry

If genuinely nothing new (the ticket was pure logic with no tooling/process insight), note that and skip to Step 4.

## Step 2 — Route each finding to the right store

Apply the routing rule to every finding:

| Finding type | Destination |
|---|---|
| Reusable HOW-TO, step sequence, gotcha, non-obvious pattern | `.claude/skills/<area>/SKILL.md` in the project |
| Always-apply project rule (applies to every ticket, not just one area) | Project `CLAUDE.md` (rules section) |
| User preference, transient session note | Memory (ONLY if personal preference — **NEVER** a procedure) |
| Cross-project universal discipline | Global airuleset (out of scope — do not touch) |

**Procedures NEVER go to memory.** A procedure that lands in memory is lost the next time a fresh context loads the project.

### Writing to `.claude/skills/<area>/SKILL.md`

- If the area skill already exists: append or update the relevant section.
- If the area is **new**: create `.claude/skills/<area>/SKILL.md` AND add a line to the `## Playbook router` section of the project `CLAUDE.md`:
  ```
  - <area> → load `.claude/skills/<area>`
  ```
- Keep each skill file focused and scannable (a developer reads it in 30 seconds before starting work on that area).

## Step 3 — Prune and dedup

After writing: check everything you touched for bloat.

- Remove entries that duplicate what's already in the global CLAUDE.md rules (don't re-state global discipline locally).
- Consolidate duplicate advice (same gotcha stated twice in different words → pick the clearer one).
- Keep the `## Playbook router` section ≤ ~10 lines. If it's growing beyond that, the router is becoming documentation — trim to the active areas only.
- In-repo edits (skill files, CLAUDE.md) ride the ticket's PR so the learning is visible in the diff.

## Step 4 — Emit the completion-report line

Add **exactly one** `📔 Playbook:` line to the completion report (in the `**Audits & deploy:**` block or immediately before it):

```
📔 Playbook: <1–2 lines — what was learned, what was updated, which skill file was touched>
```

If nothing was found in Step 1:

```
📔 Playbook: nič nové — review ran, no reusable procedure or stale entry found
```

The line is **mandatory** — the completion-report gate checks for it. "I skipped the review" is not acceptable; "review ran, nothing new" is.
