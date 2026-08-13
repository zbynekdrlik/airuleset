---
name: playbook-review
description: Review the project playbook after a ticket — before the completion report. Use after completing a ticket / before writing the completion report / to capture reusable procedure or gotcha / to update project playbook / per project-playbook-maintenance rule.
user-invocable: false
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
| Reusable HOW-TO, step sequence, gotcha, non-obvious pattern | `.claude/rules/<area>.md` in the project, with `paths:` frontmatter listing the files it applies to |
| A WORKFLOW a session will deliberately invoke by name | `.claude/skills/<area>/SKILL.md` in the project |
| Always-apply project rule (genuinely EVERY ticket, not just one area) | Project `CLAUDE.md` (rules section) — see the warning below |
| User preference, transient session note | Memory (ONLY if personal preference — **NEVER** a procedure) |
| Cross-project universal discipline | Global airuleset (out of scope — do not touch) |

**Default to `.claude/rules/<area>.md`, not the other two (#93).** A `paths:`-scoped rule is injected automatically the moment a matching file is read, and is free in every session that never touches those files. A skill body enters context ONLY when the model volunteers a `Skill` call, so a gotcha parked in a skill is usually never read (#91: 32 of 53 skills had zero lifetime invocations). And the project `CLAUDE.md` is re-read in **every session** of the project — an area gotcha appended there is an always-on cost paid by every ticket that has nothing to do with that area, which is exactly how this repo's own CLAUDE.md grew ~10k tokens/day. Send a finding to `CLAUDE.md` only if it genuinely governs every ticket; otherwise it is a `.claude/rules/` entry.

**Procedures NEVER go to memory.** A procedure that lands in memory is lost the next time a fresh context loads the project.

**Secrets NEVER go into a skill or CLAUDE.md — STRIP them.** Skills and CLAUDE.md are git-committed; memory is local. A procedure worth keeping (a deploy command, an API call) often has an embedded password / token / key / passphrase — when you move it into a skill, REPLACE the literal secret with a reference: `sshpass -p "$DEVICE_PW"`, `<API key — GitHub secret FB_APP_SECRET, not committed>`. The value stays in memory (local) or a secure store, never in a skill. **Secret-scrub gate (MANDATORY before any commit/merge):** grep every new/edited skill + CLAUDE.md for `sshpass -p '<value>'`, `password|passphrase|secret|token|api[_-]?key` followed by a literal value, and 20+ char hex/base64 blobs — if any literal secret remains, fix it FIRST. NEVER commit or merge a skill that contains a credential value.

### Writing to `.claude/rules/<area>.md` (the default)

- If the area rule already exists: append or update the relevant section.
- If the area is **new**: create `.claude/rules/<area>.md` with `paths:` frontmatter naming the files the knowledge applies to, AND add a line to the `## Playbook router` section of the project `CLAUDE.md`:
  ```markdown
  ---
  paths:
    - "src/importer/**"
    - "tests/test_importer*.py"
  ---
  ```
  ```
  - <area> → `.claude/rules/<area>.md` (auto-loads on its `paths:`)
  ```
- `paths:` globs are relative to the repo root for a project-scoped `.claude/rules/`. Scope them to the files the knowledge is genuinely about — a rule matching `**/*` is just the always-on file again under a different name.
- If `.claude/` is gitignored, a directory-pattern ignore cannot be negated from inside it: add `.claude/*` plus `!.claude/rules/` so the knowledge is committed and shared.
- Keep each file focused and scannable (a developer reads it in 30 seconds before starting work on that area).

## Step 3 — Prune and dedup

After writing: check everything you touched for bloat.

- Remove entries that duplicate what's already in the global CLAUDE.md rules (don't re-state global discipline locally).
- If an area gotcha is sitting in the project `CLAUDE.md` from an earlier ticket, MOVE it verbatim into the matching `.claude/rules/<area>.md`. The mandate adds every ticket and nothing ever removes, so this step is the only thing that keeps the always-on file from growing without bound (#93).
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
