### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — finish the job before reporting
- `autonomous-verification.md` — ✅ means functional verification, not liveness
- `pr-merge-policy.md` — report AFTER merged + deployed + verified; manual-marker projects stop at the green PR with ❓

**MANDATORY 4-line template (#1137):** `## ✅ Work Complete` · `**Úloha:**` what was asked · `**Riešenie:**` how it was solved (+ `🌐 <url>` if UI) · `✅ Overené: plan N/N · review 0 🔴 0 🟡 0 🔵 · deep 0 🔴 0 🟡 0 🔵 · arch <oblasť> OK|REWORK fixed|REWORK #N · Výstup: <observed values | n/a — why> · playbook: <lesson | none>` · terminal marker. Hook-enforced (`stop-check-prose-violations.sh`); hard rules in `skills/completion-report-deep/DEEP.md` (auto-loads on `plan-check`/`gh pr merge`).
