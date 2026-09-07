### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — finish the job before reporting (no Remaining/Future/TODO sections)
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not liveness
- `e2e-real-user-testing.md` — E2E rows reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — auto-merge default: the report is sent AFTER merged + deployed + verified; manual-marker (`airuleset:merge=manual`) projects stop at the green PR with ❓

**MANDATORY compact template (~7 lines, #940):** `## ✅ Work Complete` heading + ONE audit-summary line (plan-check + review + requesting-code-review) + `✅ Výstup:` line + `🌐` URL (if UI) + Goal/What changed + `📔 Playbook:` + terminal marker. All quality gates stay mechanically enforced. `✅ Výstup:` is ALWAYS present — concrete OBSERVED values, or `n/a — <prečo>`. Hook-enforced (`stop-check-prose-violations.sh`).

The full template, hard rules, and enforcement details are in the situational companion `skills/completion-report-deep/DEEP.md` — loaded automatically on `compact-request`/`plan-check`/`gh pr merge` commands. History + rationale: `.claude/rules-reference/completion-report-history.md` (#859).
