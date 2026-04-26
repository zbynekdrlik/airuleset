### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — no "Remaining/Future/TODO" sections; finish the job before reporting
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not just liveness
- `e2e-real-user-testing.md` — E2E table rows must reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — green PR ≠ permission to merge; wait for explicit user instruction

IMPORTANT: When work is complete, YOU MUST provide the completion report in EXACTLY this format as the **LAST thing in your message** (not the beginning — the user should not need to scroll up to find it).

#### Format (MANDATORY — use this EXACT structure, do not substitute your own)

```
## ✅ Work Complete

**Plan fulfillment:**
- [x] Step 1: description — done (evidence)
- [x] Step 2: description — done (evidence)
- [x] Step 3: description — done (evidence)

**E2E test coverage:**
| Feature/Fix | E2E Test File | What It Verifies |
|-------------|---------------|------------------|
| EQ control  | e2e/test_eq.ts | Drag slider → UI updates → REAPER confirms value |
| Solo button | e2e/test_solo.ts | Click solo → channel isolated → REAPER confirms |

✅ PR: <url> — mergeable, clean
✅ CI: green (N jobs)
✅ Deploy: verified on <target> (<what you confirmed>)
✅ /plan-check: N/N fulfilled (no NOT DONE items)
✅ /review: clean — 0 🔴 0 🟡 (or addressed in commit <sha>)
🌐 Dashboard: <url>
```

The **E2E test coverage table** is mandatory for any work that touches features or fixes. Each row must name the specific test file and describe what user workflow it exercises. If a feature has no E2E test row, it is not done.

**E2E table validation rules:**
- Each feature/fix you implemented MUST have its own row — not shared with other features
- The test file MUST exist in the repo (committed, not just run once)
- "What It Verifies" must describe a SPECIFIC user workflow (click X → see Y → backend confirms Z)
- Generic tests like "page loads" or "API returns 200" do NOT count as feature E2E coverage
- **If you cannot fill a row with a specific Playwright test file → you are not done. Write the test first.**

Use ❌ instead of ✅ if something failed. Use ⏳ if still in progress (but then you are NOT done — do not send the report yet).

**✅ means CONFIRMED WORKING. Do not use ✅ on a line that has caveats, excuses, or "will pass when...":**
- `✅ PR: url — created, CI runners stuck` → **WRONG.** That's not ✅, that's ⏳ or ❌.
- `✅ CI: Tier 1 green` + `⏳ CI: PR run stuck` → **WRONG.** If ANY CI is not green, the whole CI line is ⏳.
- **If you have ANY ⏳ or ❌ line, do NOT send the report.** Wait until everything is ✅, then send.

**ALWAYS paste the full clickable URL — never just `PR #11` or `pull/11`.** The user works remotely and copies URLs into a browser. Anti-patterns:
- `✅ PR #11 — mergeable clean` → **WRONG.** Missing the URL. Write `✅ PR: https://github.com/owner/repo/pull/11 — mergeable, clean`.
- `Awaiting your "merge it" per pr-merge-policy` (no URL anywhere in message) → **WRONG.** Always include the full `https://` PR URL.
- Prose summary instead of the template above → **WRONG.** Use the EXACT template — emoji status lines, full URLs, no substitution.

This applies to ALL completion-style messages, including interrupted or paused work. If you tell the user a PR is ready, the message MUST contain the clickable PR URL. If you announce a deployed dashboard, the message MUST contain the clickable dashboard URL (real IP, not localhost — see `no-localhost-urls.md`).

#### Pre-completion gate (MANDATORY — run BEFORE writing the report)

**You MUST run two self-audits autonomously and fix every finding before sending the completion report. Do not wait for the user to remind you.**

**1. Plan-fulfillment audit — invoke the `plan-check` skill.** Use the Skill tool: `Skill(skill: "plan-check")`. The skill audits whether the original prompt + your plan were 100% fulfilled. If any item comes back `[ ]` NOT DONE, go complete it. Do not rationalize "out of scope" or "next session". See `complete-planned-work.md`.

**2. Code-review pass — apply `/review` standards.** Run a strict code-review pass on the diff. The standards live in `~/.claude/plugins/marketplaces/claude-workflow/commands/review.md`: Correctness, Security, Performance, Maintainability, Style. Output 🔴 critical / 🟡 warnings / 🔵 suggestions. If `/review` is available as a slash command, invoke it; otherwise apply the standards inline. Either way, the diff MUST be reviewed before completion.

**3. Address findings — fix and re-run.** For every NOT DONE item, every 🔴 critical, every 🟡 warning: write a fix, commit, push, monitor CI. Then re-run plan-check and the review pass. Repeat until both come back clean. 🔵 suggestions can be deferred only if explicitly out of scope.

The completion report must include both audit lines:
- `✅ /plan-check: N/N fulfilled (no NOT DONE items)`
- `✅ /review: clean — 0 🔴 0 🟡 (or addressed in commit <sha>)`

If the report does not contain these two lines, you are NOT done. Run the gate, fix the findings, then send the report.

#### Plan fulfillment checklist

Before sending, re-read your plan and the original prompt. Mark each step:
- `[x]` — done, with evidence (commit hash, test file, verification output)
- `[ ]` — only if user explicitly told you to skip OR technically impossible (with reason). "Forgot" and "ran out of time" are NOT valid skips — go back and do it.

#### Rules

- Report at the END of your message, not the beginning.
- One or two sentences of preamble before the report is fine — a full narrative is not.
- Never send a partial report ("CI still running" means you are not done).
- Never include a "Remaining / Future / TODO / Follow-up" section — that's incomplete work disguised as a deliverable (see `complete-planned-work.md`).
