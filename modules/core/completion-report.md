### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — finish the job before reporting (no Remaining/Future/TODO sections)
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not liveness
- `e2e-real-user-testing.md` — E2E rows reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — green PR ≠ permission to merge; wait for explicit user instruction

**The completion report's audience is the USER, not you.** Terminal scrolls — only the LAST passage is visible without scrolling back. Audits at TOP, user-facing answers at BOTTOM. Send the report as the LAST thing in your message.

#### MANDATORY structure (use this EXACT template)

```
## ✅ Work Complete

**Audits & deploy:**
✅ CI: green
✅ /plan-check: N/N fulfilled
✅ /review: clean — 0 🔴 0 🟡 0 🔵
✅ Deploy: <user-visible behavior verified on the live target — include version label read from DOM>
✅ Regression test: <test_path>:<line> — RED on <test_sha>, GREEN on <fix_sha>   ← REQUIRED for bug-fix PRs (see regression-test-first.md); OMIT for non-bug PRs

**Plan steps:**           ← OPTIONAL: multi-step work only; terse user-visible one-liners
- <step 1>
- <step 2>

**E2E test coverage:**    ← OPTIONAL: only when this work ADDED new E2E tests
| Feature/Fix | E2E Test File | What It Verifies |
|---|---|---|
| <new feature> | <new test file> | <user workflow> |

---

**Goal:** <1 sentence — restate the user's ask in their words, no jargon>
**What changed:** <1-2 sentences — user-visible outcome in plain language>

🌐 Dev:  <url>          ← USER-CLICKABLE web URLs only (one per env × user-facing surface)
🌐 Prod: <url>          ← never list backend/API URLs

**[<project>] PR #<N>: <full PR title>**
<full PR URL> — mergeable, clean

❓ **Question:** <concise 1-2 sentence question>   ← only if you actually need an answer
```

Use ❌ instead of ✅ if something failed. Use ⏳ if still in progress — then you are NOT done; wait until everything is ✅ before sending.

#### Hard rules

- **FULL template every time.** Writing `## ✅ Work Complete` is a contract — every required field MUST appear. Prose substitutes ("STOP at green PR URL", "Awaiting merge", "Phase N gated") are banned. Any rewording of the same intent is also banned.
- **Order matters.** Audits at TOP, `---` separator, Goal/What changed/URLs/PR/Question at BOTTOM. The user reads the bottom of the terminal first.
- **🌐 lines = USER-CLICKABLE web URLs only.** Backend/API URLs (`:8000`, `/api/`, `backend:`) go in `✅ Deploy:` as evidence, never in 🌐. URLs in prose (`curl http://...`, `verified at https://...`) do NOT count.
- **Multi-env deploy ⇒ ≥2 🌐 lines** (one per env × user-facing surface). Read project CLAUDE.md `## Dashboards` / `## URLs` for declared URLs. If you cannot determine the URL set, ask via `❓ Question:` rather than ship a report missing URLs.
- **Goal + What changed = plain language.** Restate the user's ask in their words. NOT implementation jargon. If you cannot summarize in 1+2 sentences a non-engineer would understand, you don't understand the work yet.
- **Issue/PR refs MUST include titles.** `PR #54` / `Fixes #234` alone is wrong. `PR #54: Refactor driver.rs and add lyrics test` / `Fixes #234 (driver.rs over 1000-line cap)` is right. Apply everywhere — completion reports, plan steps, follow-up suggestions.
- **Questions MUST be marked with ❓** as the very LAST line. Trailing `?` without ❓ is banned. 1-2 sentences max. If you have nothing to ask, OMIT the line.
- **✅ means CONFIRMED WORKING.** ⏳ or ❌ on any line = NOT done; do not send the report yet.
- **No "Remaining / Future / TODO / Follow-up" sections** — that's incomplete work disguised as a deliverable. If you discover genuinely-out-of-scope work, file a GitHub issue with a clear title and reference it; don't add it to the report.
- **🔵 review findings inside the diff = MUST FIX.** No skipping as "minor / stylistic / nice-to-have / out of scope / deferred". The audit line `0 🔴 0 🟡 0 🔵` is non-negotiable. Only allowed exception: a 🔵 pointing at code OUTSIDE the diff → file a GitHub issue, reference it.
- **localhost is banned in URLs** — see `no-localhost-urls.md`. Use real IPs. Verify each URL returns 200 before pasting.
- **Bug-fix PR ⇒ `✅ Regression test:` line is REQUIRED.** Triggered when the PR closes/fixes a `bug`-labeled issue, the title contains `fix`/`bugfix`/`hotfix`/`patch`/`regression`, or the work fixed a defect. The line MUST cite the test file path, line number, the test commit SHA (RED — test failing without the fix), and the fix commit SHA (GREEN — test passing with the fix). Stop hook blocks bug-fix reports missing this line. See `regression-test-first.md`.

#### Pre-completion gate (run BEFORE writing the report)

1. Invoke `plan-check` skill — fix any `[ ]` NOT DONE items.
2. Apply `/review` standards (Correctness / Security / Performance / Maintainability / Style) — fix every 🔴 critical, 🟡 warning, AND 🔵 suggestion inside the diff.
3. Both audit lines (`✅ /plan-check: N/N fulfilled` and `✅ /review: clean — 0 🔴 0 🟡 0 🔵`) MUST appear in the audits block.

If either audit fails, you are NOT done — fix the findings, re-run, then send.

#### Length budget — ~20 lines

The whole report fits in ~20 lines (audits + optional plan steps + Goal + What changed + 🌐 + PR + maybe ❓). The diff is the evidence; the report is the summary. If you're writing more, you're over-explaining.

#### Enforcement

The Stop hook (`stop-check-prose-violations.sh`) BLOCKS completion reports missing required structure (Goal / What changed / plan-check / review lines, wrong order, missing 🌐 for multi-env deploys, banned shortcut menus). When blocked, fix the report and resend in the same turn. The hook covers all detectable violations; trust it to catch your slips, but write the full template the first time so blocking is rare.

#### Rules summary

- Report at the END of your message, not the beginning.
- Use the FULL template; no prose substitutes.
- Audits at TOP, Goal / URLs / PR / Question at BOTTOM.
- Most important content goes LAST (terminal scrolls).
- One push to send → no retroactive corrections (the user already read it).
