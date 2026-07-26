### Per-Project Playbook — Boundaries, Router, and Post-Ticket Mandate

Each project keeps a maintained **playbook**: path-scoped rules in the project's own `.claude/rules/`, indexed by a lean `## Playbook router` in the project `CLAUDE.md`.

#### Boundary table

| Belongs in | Content |
|---|---|
| `.claude/rules/<area>.md` + `paths:` frontmatter | Procedures, gotchas, non-obvious patterns for that area — **the default destination** |
| `.claude/skills/<area>` | Only a WORKFLOW a session will deliberately invoke by name (a skill body loads ONLY on an explicit `Skill` call) |
| Project `CLAUDE.md` | Router + the few rules that genuinely apply to EVERY ticket — it is re-read every session, so an area gotcha here is paid for by every project that never touches that area |
| Memory (local, NOT git) | User preferences, transient notes, AND all secrets/credentials |
| Global (`~/.claude/CLAUDE.md`) | Cross-project universal rules only — never project-specific procedures |

**Why `.claude/rules/` and not the other two (#91 measured, #93 fixed):** a `paths:`-scoped rule is injected automatically the moment a matching file is read, and costs nothing in a session that never touches those files. A skill body enters context ONLY when the model volunteers a `Skill` call — 32 of 53 skills had zero lifetime invocations, so knowledge parked there is effectively deleted. The project `CLAUDE.md` loads unconditionally, so appending to it every ticket grows the always-on prefix without bound (the #93 growth: ~10k tokens/day). The mandate to CAPTURE is unchanged; only the destination is.

**Secrets NEVER go into a skill or `CLAUDE.md`** — both are git-committed, memory is local. When a procedure that belongs in a skill has an embedded password / token / key / passphrase, STRIP the literal value (replace with `sshpass -p "$VAR"` / `<secret — GitHub secret X, not committed>`); the value stays in memory or a secure store. Grep every new/edited skill for secret patterns BEFORE committing or merging — a credential in a committed skill is a leak.

#### Routing rule

A `.claude/rules/<area>.md` arrives on its own when you touch a matching file — read it, don't re-derive it. For the remaining `.claude/skills/<area>` workflows, **load the matching skill FIRST** before working on that area.

#### Router template (add to project `CLAUDE.md`)

```markdown
## Playbook router
- <area> → `.claude/rules/<area>.md` (auto-loads on its `paths:`)
- build / deploy / release → load `.claude/skills/build-deploy` (invoke by name)
```

#### Post-ticket mandate

Run the `playbook-review` skill **po každom tickete**, before the completion report — it emits the required `📔 Playbook:` line and `stop-check-playbook-review.sh` enforces its presence. Both own the mandate; this module just points at it.

Applies to all rewordings and semantic equivalents.
