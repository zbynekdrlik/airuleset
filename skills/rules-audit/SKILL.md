---
name: rules-audit
description: Periodic audit of airuleset modules, project CLAUDE.md files, and memory.md to find duplicates, bloat, contradictions, orphans, STALE MODEL REFERENCES (bumps to current Claude generation), and outdated guidance (websearches Anthropic best practices for rules to add/change/remove). Run monthly or when rule count/token budget grows or a new Claude model ships.
user-invocable: true
disable-model-invocation: true
---

# Rules & Memory Audit

Systematic review of airuleset + project rules + auto-memory to prevent bloat, contradictions, and orphaned content. Run when:
- Global CLAUDE.md resolved size grew >10% since the last audit (size = review-due trigger, NEVER a target — see mdreview)
- A project reports recurring rule non-compliance
- After adding 3+ new modules in a short period
- A new Claude model ships — bump stale version refs + re-scan best practices
- **IMMEDIATELY after ANY change to a global-policy module** (merge / deploy / approval / autopilot rules, or marker semantics) — MANDATORY, not monthly: reconcile every project's `CLAUDE.md` + stale per-repo artifacts so old local overrides don't silently fight the new global default (see §0). Skipping this is a serious mistake — it makes "projects don't obey the new policy".
- Monthly as periodic hygiene

## Scope

Audit these locations together:
1. `~/devel/airuleset/modules/` — all modules
2. `~/devel/airuleset/profiles/` — which modules are referenced
3. `~/.claude/CLAUDE.md` — the installed global config
4. **Each active project's `CLAUDE.md`** — project-specific overrides
5. `~/.claude/projects/*/memory/` — auto-memory directories

## Audit checklist

### 0. Local-override reconciliation (RUN FIRST after any global-policy change)

When a global module's policy changed (merge/deploy went auto-by-default, a marker was renamed/superseded, etc.), STALE local overrides silently fight the new default — the failure that makes "projects don't obey the new merge/deploy policy". Sweep BOTH machines (dev1 + dev2 via ssh):

```bash
# Stale / conflicting markers + ad-hoc gating in every project CLAUDE.md
for f in ~/devel/*/CLAUDE.md ~/devel/*/.claude/CLAUDE.md ~/devel/*/repo/CLAUDE.md; do
  grep -lniE "merge=manual|autopilot=auto-merge|ask.{0,20}(before|for).{0,20}(deploy|merge|prod)|manual (merge|deploy|approval)|deploy.{0,15}(needs|requires).{0,15}approval|prod.{0,15}approval" "$f" 2>/dev/null
done
# Stale per-repo artifacts from superseded designs (old autopilot fleet wrote .claude/loop.md)
ls ~/devel/*/.claude/loop.md 2>/dev/null
```

- [ ] Every SUPERSEDED marker removed (e.g. `airuleset:autopilot=auto-merge` — auto-merge is the default now, so the marker is cruft).
- [ ] Every AD-HOC local gate that contradicts the new global default removed. A project keeps a restriction ONLY via the CURRENT documented opt-out marker (e.g. `airuleset:merge=manual`), never via legacy prose.
- [ ] Stale per-repo artifacts from a superseded design deleted (e.g. `.claude/loop.md` from the old `/autopilot` fleet — the new autopilot doesn't use it).
- [ ] Do NOT git-commit changes into a repo whose autopilot/worker is mid-run — note it and clean once the run finishes, to avoid colliding with the live worker's tree. **"Mid-run" = an ACTIVE worker/session (`claude agents --json`), NOT a dirty git tree.** A shelved/parked project can sit dirty (`M CLAUDE.md`, uncommitted edits) for months with no active session — that is stale cruft, not live work. Verify activity from the agent list, never infer it from `git status`.

### 0b. CI mutation-budget audit (cross-repo — find "endless CI")

Global rule changes don't reach project CI YAML (it's the project's CODE, not governance). Periodically audit every repo's CI for mutation jobs that violate `mutation-testing.md` (unbounded / full-scope / high-timeout on the PR path = the "endless CI wait"). Scan BOTH machines:

```bash
for wf in $(find ~/devel -maxdepth 6 -path '*/.github/workflows/*' \( -name '*.yml' -o -name '*.yaml' \) -not -path '*/node_modules/*' 2>/dev/null); do
  grep -qiE 'cargo[ -]mutants|\bmutmut\b|stryker|\bmutation\b' "$wf" || continue
  echo "## $wf"; grep -nE 'mutation|mutants|stryker|timeout-minutes|push:|pull_request:|schedule:|in-diff|--shard|--workspace|incremental' "$wf" | grep -viE '^\s*#'
done
```

- [ ] Flag any mutation job that is on `push`/`pull_request` AND (full-scope — `--workspace` / no `--in-diff` / "entire codebase") OR `timeout-minutes` > 20.
- [ ] For each violation, **file an alignment issue on that repo** (diff-scoped ≤20-min PR gate + weekly async, or async-only if covering tests are too slow) — do NOT edit the project's CI yourself ([[feedback-stay-in-repo-lane]]). Precedents: codex-bridge#119, bakerion-ai#341, spinbike#102, songplayer#123, devbridge#62, reaperiem#200.
- [ ] Diff-scoped + ≤20-min + (weekly async or PR-only-with-skip) = compliant, no action.

### 1. Size metric (tracked, never a target)
- [ ] Resolved `~/.claude/CLAUDE.md` size (`wc -l` / `du -b`) — record it + Δ vs the previous audit. Review-due trigger + prioritizer (largest modules reviewed first), NOT a number to hit.
- [ ] Which 5 modules contribute most tokens? (`wc -l modules/*/*.md | sort -rn | head -10`) — review order, not a cut list
- [ ] Any SITUATIONAL module (fires only for one task-type/area)? Candidate for CONVERSION to skill / `paths:`-scoped rule / hook (mdreview axis 3) — never bare deletion
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

### 7. Model currency (CHECK EVERY RUN — models release often)
Rules are tuned per model generation. When a newer Claude ships, stale version strings and behavior notes mislead the agent.
- [ ] Read THIS session's `## Environment` block → note the live model (e.g. `Opus 4.8 (1M context)`, ID `claude-opus-4-8`)
- [ ] `grep -rn "4\.7\|4\.6\|Opus 4\|Sonnet 4\|Haiku 4\|claude-opus\|claude-sonnet\|claude-haiku" modules/` — list every hardcoded model reference
- [ ] Any reference older than the live model → STALE. Bump version strings AND any behavior notes that named the old gen (e.g. "4.7 literalism" → current gen)
- [ ] `model-awareness.md` + `claude-code-tooling.md` are the canonical model docs — they MUST name the current primary model and current default effort tier
- [ ] Don't invent behavior. If you can't confirm how the new model differs, keep the proven guidance and only update the version label — flag the behavior section for a websearch-backed rewrite (next section)

### 8. External best-practice scan (WEBSEARCH — REQUIRED)
The ruleset should track Anthropic's current guidance, not last quarter's. Use `WebSearch` (and `WebFetch` on official docs) every run:
- [ ] Search: `"Claude <current-model> prompt engineering best practices"`, `"Claude Code <current-year> new features hooks skills"`, `"Anthropic agent SDK rules best practices"`
- [ ] Prefer official sources: `docs.anthropic.com`, `anthropic.com/engineering`, Claude Code changelog/release notes
- [ ] For each finding, decide: (a) NEW rule worth adding, (b) EXISTING rule now outdated → change, (c) rule now OBSOLETE (the platform handles it natively) → remove
- [ ] Cross-check the live model: did the new generation change effort tiers, tool-use behavior, context window, or subagent model defaults? If yes → update `model-awareness.md`
- [ ] Cite each proposed change with its source URL in the punch list — no uncited "best practice" claims

## Process

1. **Read** `~/.claude/CLAUDE.md` and run `wc -l` on it + the target modules
2. **Find duplicates** — use `grep -l "<rule phrase>" modules/**/*.md` for suspected overlaps
3. **Check orphans** — diff `ls modules/*/*.md` against `profiles/universal.profile`
4. **Audit memory** — list each project's MEMORY.md, grep for rules that duplicate global modules
5. **Check model currency** — read the live model from this session's Environment block, grep modules for stale version strings (section 7), list every reference older than the live model
6. **Websearch best practices** — run the searches in section 8, fetch official docs, gather cited proposals for new/changed/removed rules
7. **Propose punch list** — concrete actions (delete X, merge Y+Z, trim lines A-B of file F, bump model refs, add/remove rule R per <source>)
8. **Apply changes** — commit per category (trim-bloat, dedupe, add-gates, model-bump, best-practice)
9. **Deploy** — `python3 airuleset.py push`

## Output format

Return a punch list ordered by impact:

```
## Rules Audit — YYYY-MM-DD

### Size
- Global CLAUDE.md: N lines / K KB (Δ vs last audit — metric, not a target)
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

### Model currency
- Live model: <e.g. Opus 4.8 (claude-opus-4-8)>
- Stale refs: model-awareness.md L3 "Opus 4.7", claude-code-tooling.md L11 "Opus 4.7" — action: bump to current gen
- Behavior notes naming old gen: <file:line> — action: relabel / websearch-backed rewrite

### Best practices (websearch — cited)
- <source URL>: <finding> — action: ADD rule / CHANGE module X / REMOVE module Y (now native)
- ...

### Recommended commits
1. "Trim bloat: modules A, B, C (save ~N lines)"
2. "Merge duplicates: X+Y into Z"
3. "Add context gates to module W"
4. "Clean orphan: delete modules/X.md"
5. "Bump model refs to <current gen>"
6. "Apply best-practice updates (<source>)"
```

## What NOT to do

- Don't delete modules that are actively enforcing a recurring problem — size is secondary to effectiveness
- Don't merge modules that cover distinct concerns even if they share phrasing (it hides the distinction)
- Don't trim rules that were added as responses to specific incidents — check `git log` first
- Don't delete memory entries that capture user preferences or feedback — they have different semantics from rules
