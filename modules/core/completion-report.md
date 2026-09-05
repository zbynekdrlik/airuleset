### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — finish the job before reporting (no Remaining/Future/TODO sections)
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not liveness
- `e2e-real-user-testing.md` — E2E rows reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — auto-merge default: the report is sent AFTER merged + deployed + verified; manual-marker (`airuleset:merge=manual`) projects stop at the green PR with ❓

**MANDATORY template:** `## ✅ Work Complete` → Audits block (CI / plan-check / review / requesting-code-review / Deploy / Regression test / Výstup) → `---` → Goal / What changed / 🌐 URLs / PR line / ❓ Question. Use ❌/⏳ if failed/in-progress — then NOT done. Audits at TOP, user-facing answers at BOTTOM.

**`✅ Výstup:` is ALWAYS present** — concrete OBSERVED values read back from the REAL artifact, or an explicit `n/a — <prečo>`. Hook-enforced (`stop-check-prose-violations.sh`): line missing, value-free, or `n/a` alongside a 🌐/📱 line = blocked.

**`❓` questions shaped as the structured Slovak block** (`**Otázka — projekt …:**` briefing + options + the ❓ line — `user-questions-slovak.md`, hook-enforced `stop-check-question-quality.sh`).

**Compact at your own boundary:** call `compact-request --self` FIRST, BEFORE writing the report text — never after.

The full template skeleton, hard rules, pre-completion gate, reduced-authority variant, enforcement details, and compact-at-boundary mechanics are in the situational companion `skills/completion-report-deep/DEEP.md` — loaded automatically on `compact-request`/`plan-check`/`gh pr merge` commands. History + rationale: `.claude/rules-reference/completion-report-history.md` (#859).
