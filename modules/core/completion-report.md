### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — no "Remaining/Future/TODO" sections; finish the job before reporting
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not just liveness
- `e2e-real-user-testing.md` — E2E table rows must reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — green PR ≠ permission to merge; wait for explicit user instruction

**The completion report's audience is the USER, not you.** They read the terminal, where output scrolls and only the LAST passage is visible without scrolling back. That changes the layout: the most important content (Goal, outcome, PR URL, any pending question) must come at the END, after the audit/technical lines. Send the report as the LAST thing in your message.

#### Format (MANDATORY — use this EXACT structure, do not substitute your own)

```
## ✅ Work Complete

**Audits & deploy:**
✅ CI: green
✅ /plan-check: N/N fulfilled
✅ /review: clean — 0 🔴 0 🟡
✅ Deploy: <user-visible behavior you verified on the live target>

**Plan steps:**           ← OPTIONAL — only for multi-step work, terse one-liners
- <user-visible step 1>
- <user-visible step 2>

**E2E test coverage:**    ← OPTIONAL — only when this work ADDED new E2E tests
| Feature/Fix | E2E Test File | What It Verifies |
|---|---|---|
| <new feature> | <new test file> | <user workflow> |

---

**Goal:** <1 sentence — restate the user's ask in their words, no jargon>
**What changed:** <1-2 sentences — user-visible outcome in plain language>

**[<project>] PR #<N>: <full PR title>**
<full PR URL> — mergeable, clean
🌐 Dashboard: <url>       ← only if a deployed UI exists

❓ **Question:** <concise 1-2 sentence question>   ← only if you actually need an answer
```

Use ❌ instead of ✅ if something failed. Use ⏳ if still in progress (then you are NOT done — wait until everything is ✅).

#### Why this order

The terminal shows the LAST lines of output. The user reads upward only if needed. Put what they need at the bottom:
1. Did it work? → Goal + What changed (plain language)
2. Where do I click? → PR URL
3. Anything I need to decide? → ❓ Question (clearly marked)

Audits and technical detail go ABOVE the `---` separator. They prove correctness but the user already trusts you to run them — they're context, not the headline.

#### Goal & What changed — MANDATORY (placed at the bottom)

These are the only lines the user reads carefully. Get them right.

- **Goal** — restate what the user asked for, in plain language. Avoid implementation jargon.
  - WRONG: `driver.rs at 999/1000 → split tests`
  - RIGHT: `Get songplayer's driver.rs back under the 1000-line cap and add a regression test for the lyrics-error path.`
- **What changed** — describe the user-visible outcome, NOT the technical mechanism.
  - WRONG: `split tests via #[path], driver.rs 999 → 512 lines, tightened 4 field visibilities`
  - RIGHT: `Driver source is back under cap; lyrics editor now logs a warning instead of silently misbehaving on malformed JSON.`

If you cannot summarize the work in 1+2 sentences a non-engineer would understand, you don't understand the work yet — re-read the original prompt before writing the report.

#### Issue / PR references — ALWAYS include the title

The user manages many active projects in parallel. They cannot remember what `#234` or `#54` means in a given repo. Every issue/PR reference — anywhere in the message — MUST include a short title.

- WRONG: `Fixes #54` / `PR #54 — mergeable, clean` / `Closes #234`
- RIGHT: `Fixes #54 (driver.rs over 1000-line cap)` / `PR #54: Refactor driver.rs and add lyrics error-path test`

Bare numbers force the user to context-switch into GitHub to decode them. Apply this everywhere — completion reports, plan steps, follow-up suggestions.

#### Pending question — clearly marked, concise

If your report includes a question for the user, mark it with `❓ **Question:**` on its own line at the very END (last line of the message). Rules:

- Use the `❓` emoji marker — non-negotiable. Do not bury questions in prose.
- 1-2 sentences max. If you need more, you haven't framed the question tightly enough.
- ONE question. Multiple decisions = `❓ **Decisions needed:**` then a numbered list (still 1 line each).
- Do NOT ask pre-answered questions (see `ask-before-assuming.md`). If the answer is fixed, apply it — don't ask.
- If you have nothing to ask, OMIT the line entirely. Do not write `❓ Question: none` or similar filler.

WRONG (buried): `All weaknesses from the review addressed. Awaiting your "merge it".`
RIGHT (marked): `❓ **Question:** Merge to main now, or wait for the dev2 verification you mentioned earlier?`

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

#### E2E test coverage (optional — new tests only)

Only include the E2E table when this work ADDED new E2E tests. List ONLY the new tests — do NOT include rows like `(unchanged)`, `previously listed coverage`, or `(still green)`. Those are noise that buries the signal.

E2E table validation:
- Each NEW feature/fix in this PR must have its own row
- The test file MUST exist in the repo (committed)
- "What It Verifies" must describe a SPECIFIC user workflow (click X → see Y → backend confirms Z)
- Generic tests like "page loads" or "API returns 200" do NOT count
- **If a new feature in this PR has no E2E test → you are not done. Write the test first.**

If no new E2E tests in this work (e.g., a pure refactor or doc change), OMIT the table entirely.

#### Plan steps (optional — multi-step work only)

For simple work (one logical change), the Goal + What changed lines are enough — skip plan steps.

For multi-step work, expand ABOVE the `---` separator as a terse list — one line per step in user-visible language, no evidence dumps:

```
**Plan steps:**
- Refactored driver.rs back under the 1000-line cap
- Added regression test for malformed-lyrics warning path
- Tightened field visibility per review feedback
```

WRONG (technical, evidence-heavy):
- `driver.rs at 999/1000 → split tests via #[path]. driver.rs: 999 → 512 lines`
- `CI green on both push (24958805416) and pull_request (24958806142) runs`

RIGHT (user-visible, terse):
- `Refactored driver.rs back under the 1000-line cap`
- `CI green on push and PR runs`

The diff is the evidence. Don't paste run IDs, line counts, or file:line refs in the report.

#### Pre-completion gate (MANDATORY — run BEFORE writing the report)

Run two self-audits autonomously and fix every finding before the report. Do not wait for the user to remind you.

1. **Plan-fulfillment audit** — invoke the `plan-check` skill (`Skill(skill: "plan-check")`). It audits whether the original prompt + plan were 100% fulfilled. If any item comes back `[ ]` NOT DONE, complete it. Don't rationalize "out of scope".
2. **Code-review pass** — apply `/review` standards (Correctness, Security, Performance, Maintainability, Style). Standards live in `~/.claude/plugins/marketplaces/claude-workflow/commands/review.md`. Output 🔴 critical / 🟡 warnings / 🔵 suggestions.
3. **Address findings — fix and re-run.** For every NOT DONE / 🔴 / 🟡: write a fix, commit, push, monitor CI. Re-run both audits. Repeat until both come back clean. 🔵 suggestions can be deferred only if explicitly out of scope.

Both audit lines (`✅ /plan-check: N/N fulfilled` and `✅ /review: clean — 0 🔴 0 🟡`) MUST appear in the audits block. If they don't, you are NOT done — run the gate, fix the findings, then send the report.

#### Length budget — ~20 lines

The whole report fits in ~20 lines (audits block + optional plan steps + Goal + What changed + PR URL + maybe one question). If you're writing more, you're over-explaining. The diff has the technical detail; the report is a summary.

#### Rules

- Report at the END of your message, not the beginning.
- One or two sentences of preamble before the report is fine — a full narrative is not.
- Never send a partial report ("CI still running" means you are not done).
- Never include a "Remaining / Future / TODO / Follow-up" section — that's incomplete work disguised as a deliverable (see `complete-planned-work.md`).
- Most important content goes at the BOTTOM (Goal, What changed, PR URL, Question). Audit lines go at the TOP. The terminal scrolls — write for what's visible last.
- Questions for the user: `❓` marker, 1-2 sentences, very last line. No buried prose questions.
