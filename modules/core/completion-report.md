### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — no "Remaining/Future/TODO" sections; finish the job before reporting
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not just liveness
- `e2e-real-user-testing.md` — E2E table rows must reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — green PR ≠ permission to merge; wait for explicit user instruction

**The completion report's audience is the USER, not you.** The user manages many projects in parallel and reads the report to learn (a) what they asked for, (b) what changed in plain language, (c) where to click to merge. They do NOT need a technical recap — the diff has the detail. Lead with the user-facing answer. Send the report as the LAST thing in your message.

#### Format (MANDATORY — use this EXACT structure)

```
## ✅ Work Complete

**Goal:** <1 sentence — restate the user's ask in their words, no jargon>
**What changed:** <1-2 sentences — user-visible outcome in plain language>

**[<project>] PR #<N>: <full PR title>**
<full PR URL> — mergeable, clean

✅ CI: green | ✅ /plan-check: N/N fulfilled | ✅ /review: clean — 0 🔴 0 🟡
✅ Deploy: <what you verified works on the live target>
🌐 Dashboard: <url>     ← only if a deployed UI exists
```

Use ❌ instead of ✅ if something failed. Use ⏳ if still in progress (but then you are NOT done — wait until everything is ✅).

#### Length budget — ~15 lines

The whole report fits in ~15 lines. If you're writing more, you're over-explaining. The diff has the technical detail; the report is a summary. Save the deep dive for when the user asks "explain what you did". Multi-paragraph plan dumps, file paths, commit SHAs, CI run IDs, and line counts do NOT belong in the headline report.

#### Goal & What changed — MANDATORY top lines

Every report MUST start with these two lines. They are the only part the user reads carefully — get them right.

- **Goal** — restate what the user asked for, in plain language. Avoid implementation jargon.
  - WRONG: `driver.rs at 999/1000 → split tests`
  - RIGHT: `Get songplayer's driver.rs back under the 1000-line cap and add a regression test for the lyrics-error path.`
- **What changed** — describe the user-visible outcome, NOT the technical mechanism.
  - WRONG: `split tests via #[path], driver.rs 999 → 512 lines, tightened 4 field visibilities`
  - RIGHT: `Driver source is back under cap; lyrics editor now logs a warning instead of silently misbehaving when JSON is malformed.`

If you cannot summarize the work in 1+2 sentences a non-engineer would understand, you don't understand the work yet — re-read the original prompt before writing the report.

#### Issue / PR references — ALWAYS include the title

The user manages many active projects in parallel. They cannot remember what `#234` or `#54` means in a given repo. Every issue/PR reference — in the report, in chat, anywhere — MUST include a short title.

- WRONG: `Fixes #54` / `PR #54 — mergeable, clean` / `Closes #234`
- RIGHT: `Fixes #54 (driver.rs over 1000-line cap)` / `PR #54: Refactor driver.rs and add lyrics error-path test`

This is non-negotiable. Bare issue numbers force the user to context-switch into GitHub to decode them. Apply this everywhere — completion reports, plan steps, follow-up suggestions, schedule offers.

#### URL hygiene

**ALWAYS paste the full clickable URL** — never just `PR #11` or `pull/11`. The user works remotely and copies URLs into a browser.

- `✅ PR #11 — mergeable clean` → **WRONG.** Missing the URL.
- `Awaiting your "merge it" per pr-merge-policy` (no URL anywhere in message) → **WRONG.** Always include the full `https://` PR URL.
- Prose summary instead of the template above → **WRONG.** Use the EXACT template.

Dashboard URL: real IP, not localhost (see `no-localhost-urls.md`). Verify it returns 200 before pasting.

#### ✅ means CONFIRMED WORKING

Do not use ✅ on a line with caveats or "will pass when...":

- `✅ PR: url — created, CI runners stuck` → **WRONG.** That's ⏳ or ❌, not ✅.
- `✅ CI: Tier 1 green` + `⏳ CI: PR run stuck` → **WRONG.** If ANY CI is not green, the line is ⏳.
- **If you have ANY ⏳ or ❌ line, do NOT send the report.** Wait until everything is ✅, then send.

#### Optional: E2E test coverage

Only include an E2E table when this work ADDED new E2E tests. List ONLY the new tests — do NOT include rows like `(unchanged)`, `previously listed coverage`, or `(still green)`. Those are noise that buries the signal.

| Feature/Fix | E2E Test File | What It Verifies |
|-------------|---------------|------------------|
| Lyrics error path | tests_play_video.rs::malformed_lyrics_warns | Invalid JSON → warning banner shown, no crash |

E2E table validation:
- Each NEW feature/fix in this PR must have its own row
- The test file MUST exist in the repo (committed)
- "What It Verifies" must describe a SPECIFIC user workflow (click X → see Y → backend confirms Z)
- Generic tests like "page loads" or "API returns 200" do NOT count
- **If a new feature in this PR has no E2E test → you are not done. Write the test first.**

If no new E2E tests in this work (e.g., a pure refactor or doc change), OMIT the table entirely.

#### Optional: Plan fulfillment list

For simple work (one logical change), the Goal + What changed lines are enough — skip plan fulfillment.

For multi-step work where the user benefits from seeing each step tracked, expand BELOW the audit lines as a terse list — one line per step in user-visible language, no evidence dumps:

```
**Plan steps:**
- Refactored driver.rs to 512 lines (was at the 999/1000-line cap)
- Added regression test for malformed-lyrics warning path
- Tightened field visibility per review feedback
```

WRONG (technical, evidence-heavy):
- `driver.rs at 999/1000 → split tests via #[path]. driver.rs: 999 → 512 lines`
- `cached_position_ms doc spells out the ~500ms staleness bound`
- `CI green on both push (24958805416) and pull_request (24958806142) runs`

RIGHT (user-visible, terse):
- `Refactored driver.rs back under the 1000-line cap`
- `Documented the ~500ms staleness window on cached_position_ms`
- `CI green on push and PR runs`

The diff is the evidence. Don't paste run IDs, line counts, or file:line refs in the headline report.

#### Pre-completion gate (MANDATORY — run BEFORE writing the report)

Run two self-audits autonomously and fix every finding before the report. Do not wait for the user to remind you.

1. **Plan-fulfillment audit** — invoke the `plan-check` skill (`Skill(skill: "plan-check")`). It audits whether the original prompt + plan were 100% fulfilled. If any item comes back `[ ]` NOT DONE, complete it. Don't rationalize "out of scope".
2. **Code-review pass** — apply `/review` standards (Correctness, Security, Performance, Maintainability, Style). Standards live in `~/.claude/plugins/marketplaces/claude-workflow/commands/review.md`. Output 🔴 critical / 🟡 warnings / 🔵 suggestions.
3. **Address findings — fix and re-run.** For every NOT DONE / 🔴 / 🟡: write a fix, commit, push, monitor CI. Re-run both audits. Repeat until both come back clean. 🔵 suggestions can be deferred only if explicitly out of scope.

Both audit lines (`✅ /plan-check: N/N fulfilled` and `✅ /review: clean — 0 🔴 0 🟡`) MUST appear in the report. If they don't, you are NOT done — run the gate, fix the findings, then send the report.

#### Rules

- Report at the END of your message, not the beginning.
- One or two sentences of preamble before the report is fine — a full narrative is not.
- Never send a partial report ("CI still running" means you are not done).
- Never include a "Remaining / Future / TODO / Follow-up" section — that's incomplete work disguised as a deliverable (see `complete-planned-work.md`).
- The report is for the USER. The Goal + What changed lines and the PR URL are the parts they read. Audit lines are the bare-minimum technical proof — anything beyond that is opt-in.
