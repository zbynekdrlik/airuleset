---
name: rules-audit
description: Periodic audit of airuleset modules, project CLAUDE.md files, and memory.md to find duplicates, bloat, contradictions, and orphans. Run monthly or when rule count/token budget grows.
user-invocable: true
disable-model-invocation: true
---

# Rules & Memory Audit

Systematic review of airuleset + project rules + auto-memory to prevent bloat, contradictions, and orphaned content. Run when:
- Global CLAUDE.md resolved size grows (target <400 lines / <30 KB)
- A project reports recurring rule non-compliance
- After adding 3+ new modules in a short period
- Monthly as periodic hygiene

## Scope

Audit these locations together:
1. `~/devel/airuleset/modules/` — all modules
2. `~/devel/airuleset/profiles/` — which modules are referenced
3. `~/.claude/CLAUDE.md` — the installed global config
4. **Each active project's `CLAUDE.md`** — project-specific overrides
5. `~/.claude/projects/*/memory/` — auto-memory directories

## Audit checklist

### 1. Size budget
- [ ] Resolved `~/.claude/CLAUDE.md` size (`wc -l` / `du -b`). Target: <400 lines, <30 KB
- [ ] Which 5 modules contribute most tokens? (`wc -l modules/*/*.md | sort -rn | head -10`)
- [ ] Any module >60 lines? Candidate for trimming
- [ ] Total module count vs. universal.profile count — orphans?

### 2. Duplicates & overlap
- [ ] Two modules cover the same topic → merge or cross-reference
- [ ] Same rule appears in global module AND project CLAUDE.md → delete from project
- [ ] Memory entry duplicates a CLAUDE.md rule → delete the memory entry
- [ ] "How to ask" / "Anti-patterns" sections repeated across modules → consolidate

### 3. Orphans
- [ ] Modules in `modules/` not listed in any profile → either add to profile or delete
- [ ] Memory files in `~/.claude/projects/*/memory/` older than 3 months without updates → review for deletion
- [ ] Skills in `skills/` not used in 3 months → candidate for removal

### 4. Contradictions
- [ ] Global rule says X, project CLAUDE.md overrides to Y — is the override still needed?
- [ ] Two modules give conflicting guidance (e.g., "always ask" vs "never ask")
- [ ] Memory says "user prefers X" but CLAUDE.md mandates Y

### 5. Context gate coverage
High-traffic modules should have "Context gate — related rules" pointers at the top:
- [ ] `completion-report.md` — points to complete-planned-work, autonomous-verification, e2e
- [ ] `tdd-workflow.md` — points to e2e-real-user-testing, test-strictness
- [ ] `ci-push-discipline.md` — points to ci-monitoring, version-bumping
- [ ] `post-deploy-verification.md` — points to autonomous-verification, e2e, no-localhost-urls
- [ ] Any newly added cross-cutting module

### 6. Memory hygiene
Per `~/.claude/projects/<project>/memory/MEMORY.md`:
- [ ] MEMORY.md index under 200 lines (only first 200 load into context)
- [ ] Topic files that duplicate CLAUDE.md rules → delete (CLAUDE.md wins)
- [ ] Stale project memories (projects no longer active) → delete

## Process

1. **Read** `~/.claude/CLAUDE.md` and run `wc -l` on it + the target modules
2. **Find duplicates** — use `grep -l "<rule phrase>" modules/**/*.md` for suspected overlaps
3. **Check orphans** — diff `ls modules/*/*.md` against `profiles/universal.profile`
4. **Audit memory** — list each project's MEMORY.md, grep for rules that duplicate global modules
5. **Propose punch list** — concrete actions (delete X, merge Y+Z, trim lines A-B of file F)
6. **Apply changes** — commit per category (trim-bloat, dedupe, add-gates)
7. **Deploy** — `python3 airuleset.py push`

## Output format

Return a punch list ordered by impact:

```
## Rules Audit — YYYY-MM-DD

### Size
- Global CLAUDE.md: N lines / K KB (target <400/<30)
- Top bloat: module1 (L lines), module2 (L lines), ...

### Duplicates
- module A section X duplicates module B section Y — action: delete from A
- ...

### Orphans
- modules/X.md not in any profile — action: delete OR add to Y profile
- ...

### Memory overlap
- project P memory file M duplicates global rule R — action: delete memory file
- ...

### Recommended commits
1. "Trim bloat: modules A, B, C (save ~N lines)"
2. "Merge duplicates: X+Y into Z"
3. "Add context gates to module W"
4. "Clean orphan: delete modules/X.md"
```

## What NOT to do

- Don't delete modules that are actively enforcing a recurring problem — size is secondary to effectiveness
- Don't merge modules that cover distinct concerns even if they share phrasing (it hides the distinction)
- Don't trim rules that were added as responses to specific incidents — check `git log` first
- Don't delete memory entries that capture user preferences or feedback — they have different semantics from rules
