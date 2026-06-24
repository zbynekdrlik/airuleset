### Per-Project Playbook — Boundaries, Router, and Post-Ticket Mandate

Each project keeps a maintained **playbook**: on-demand skills in the project's own `.claude/skills/`, indexed by a lean `## Playbook router` in the project `CLAUDE.md`.

#### Boundary table

| Belongs in | Content |
|---|---|
| `.claude/skills/<area>` | Procedures, gotchas, non-obvious patterns for that area |
| Project `CLAUDE.md` | Router (which skill to load for which area) + always-rules |
| Memory (local, NOT git) | User preferences, transient notes, AND all secrets/credentials |
| Global (`~/.claude/CLAUDE.md`) | Cross-project universal rules only — never project-specific procedures |

**Secrets NEVER go into a skill or `CLAUDE.md`** — both are git-committed, memory is local. When a procedure that belongs in a skill has an embedded password / token / key / passphrase, STRIP the literal value (replace with `sshpass -p "$VAR"` / `<secret — GitHub secret X, not committed>`); the value stays in memory or a secure store. Grep every new/edited skill for secret patterns BEFORE committing or merging — a credential in a committed skill is a leak.

#### Routing rule

Before working on any area covered by the playbook, **load the matching skill FIRST** — do not re-derive what the playbook already knows.

#### Router template (add to project `CLAUDE.md`)

```markdown
## Playbook router
Load the matching skill BEFORE working on that area (don't re-derive):
- build / deploy / release → load `.claude/skills/build-deploy`
- <area> → load `.claude/skills/<area>`
```

#### Post-ticket mandate

After **po každom tickete** run the `playbook-review` skill before the completion report; the report MUST carry a `📔 Playbook:` line stating whether the playbook was updated and what (or "no update needed").

Applies to all rewordings and semantic equivalents.
