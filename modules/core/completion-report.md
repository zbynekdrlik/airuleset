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
✅ /review: clean — 0 🔴 0 🟡 0 🔵
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

🌐 Dev:  <url>      ← USER-CLICKABLE web URLs only (one per environment / per UI surface)
🌐 Prod: <url>      ← never list backend/API URLs — those are evidence, not actions

**[<project>] PR #<N>: <full PR title>**
<full PR URL> — mergeable, clean

❓ **Question:** <concise 1-2 sentence question>   ← only if you actually need an answer
```

Use ❌ instead of ✅ if something failed. Use ⏳ if still in progress (then you are NOT done — wait until everything is ✅).

#### Why this order

The terminal shows the LAST lines of output. The user reads upward only if needed. Put what they need at the bottom:
1. Did it work? → Goal + What changed (plain language)
2. Where do I verify? → 🌐 dashboard URLs (every env × every service)
3. Where do I click to merge? → PR URL
4. Anything I need to decide? → ❓ Question (clearly marked)

Audits and technical detail go ABOVE the `---` separator. They prove correctness but the user already trusts you to run them — they're context, not the headline.

#### Dashboards & URLs — list EVERY clickable URL

The user works remotely and copies URLs from the terminal. The 🌐 list is for URLs the USER would click in a browser — frontend dashboards, admin panels, marketing pages. **Backend / API URLs do NOT belong in this list** — they're for agent verification (curl them as evidence in the `✅ Deploy:` line), not human use.

**MANDATORY** — list every USER-CLICKABLE web URL relevant to the work, each on its own `🌐` line. For a project with both dev and prod environments, that's usually 2 URLs:

```
🌐 Dev:  http://10.77.8.134:3000
🌐 Prod: https://app.example.com
```

If the project has multiple user-facing UI surfaces (e.g. a customer dashboard AND a separate admin panel AND a marketing site), list each one for each environment:

```
🌐 Dev dashboard:  http://10.77.8.134:3000
🌐 Dev admin:      http://10.77.8.134:3000/admin
🌐 Prod dashboard: https://app.example.com
🌐 Prod admin:     https://app.example.com/admin
```

The bar is: would the user open this URL in a browser to verify the deploy? If yes → list it. If it's only useful via curl/API client → don't list it.

**Where to find the URL set:** before sending the report, read the project's CLAUDE.md for a `## Dashboards` or `## URLs` section. If it exists, list ALL declared **user-facing** URLs — do not pick a subset, do not include API endpoints from the same section. If no section exists, list at minimum: every environment you deployed to × every user-facing UI surface you touched. If you cannot determine the URL set, ask the user with `❓ Question:` rather than ship a report missing URLs.

**Backend evidence belongs in the `✅ Deploy:` line, not the 🌐 list:**

- RIGHT: `✅ Deploy: dev backend redeployed; v1.0.97-dev.9 verified via /api/version` + `🌐 Dev: http://10.77.8.134:3000` (one user URL, backend mentioned only as evidence)
- WRONG: `🌐 Dev backend: http://10.77.8.134:8000/api/system/info` (the user has no reason to click an API endpoint — that's noise)

**Anti-patterns:**

- Listing `🌐 ... backend:` or `🌐 ... API:` URLs → **WRONG.** Those waste user space and aren't human-clickable. Backend URLs go in `✅ Deploy:` as verification evidence only.
- Single `🌐 Dashboard: <url>` line when both dev and prod environments exist → **WRONG.** List both.
- URL in prose like `curl http://...` or `verified at https://...` → **WRONG.** Inline URLs are evidence, not clickable actions. If it's user-facing, add a separate `🌐` line.
- Skipping URLs because "the user already knows them" → **WRONG.** They manage many projects in parallel — they don't remember which IP/port goes with which project.
- Mentioning a UI/dashboard deploy in `✅ Deploy:` without a corresponding `🌐` line → **WRONG.** If you say you verified the dashboard, paste its URL.

**localhost is banned** — see `no-localhost-urls.md`. Use the real IP. Verify each URL returns 200 before pasting (a stale URL is worse than no URL).

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
3. **Address EVERY finding — fix and re-run.** For every NOT DONE item, every 🔴 critical, every 🟡 warning, AND every 🔵 suggestion that lives inside this PR's diff: write a fix, commit, push, monitor CI. Re-run both audits. Repeat until both come back clean — `0 🔴 0 🟡 0 🔵`. **The user wants the highest-quality code possible. Do NOT skip 🔵 findings as "minor", "stylistic", "nice-to-have", "low priority", or "out of scope".** That loophole is closed.
   - The ONLY allowed exception: a 🔵 finding that points at code OUTSIDE this PR's diff (e.g., the reviewer noticed an unrelated module could be refactored). For that case, file a GitHub issue with a clear title (per the Issue/PR title rule) and reference the issue number in the report. NEVER silently skip a 🔵 finding inside the diff.
   - Banned phrases (intent, not just wording): "🔵 deferred", "🔵 out of scope", "🔵 left for next session/PR", "🔵 are minor — leaving them", "🔵 stylistic — skip", "🔵 nice-to-have — defer", "won't address the suggestions", "blue findings can wait". If you're tempted to write any of these — STOP and fix the finding instead.

Both audit lines (`✅ /plan-check: N/N fulfilled` and `✅ /review: clean — 0 🔴 0 🟡 0 🔵`) MUST appear in the audits block. The 🔵 counter is non-negotiable — `0 🔴 0 🟡` without 🔵 is a failed audit. If they don't, you are NOT done — run the gate, fix the findings, then send the report.

#### Length budget — ~20 lines

The whole report fits in ~20 lines (audits block + optional plan steps + Goal + What changed + 🌐 URLs + PR URL + maybe one question). If you're writing more, you're over-explaining. The diff has the technical detail; the report is a summary.

#### Full template every time — no truncation, no prose substitutes

**If you write `## ✅ Work Complete` ANYWHERE in your message, you MUST include EVERY required field.** No "I'll skip Goal because the user already knows", no "the audit lines are obvious so I'll abbreviate", no prose-style shorthand instead of the structured fields. The template is one-shot — write all of it the first time.

**Banned shortcuts (intent — any rewording counts):**
- `STOP at green PR URL. Awaiting your "merge it" per pr-merge-policy.` → **WRONG.** That's prose shorthand for what the template specifies. The template puts the PR URL on its own line; the "awaiting merge" is implicit (your job ends at green PR per `pr-merge-policy.md`).
- `Phase 2 (...) remains gated on Phase 1 merge per the original plan.` → **WRONG.** That's a "Future / Remaining" mention disguised as plan continuity. If the user originally agreed to a multi-phase plan, the next phase is the next session's prompt — do NOT explain the gating in this report. See `complete-planned-work.md`.
- `## ✅ Work Complete` followed by 3 status lines and a PR URL → **WRONG.** Missing Goal, What changed, /review, 🌐 URLs. The header is a contract — using it commits you to the full template.
- A free-form summary that mentions "PR is mergeable", "all checks green", "ready to merge" without the structured template → **WRONG.** Those phrases trigger the template — use it.

**The Stop hook fires when these are missing — but you should never need the warning. Write the full template the first time.** The warning lands AFTER your message is in front of the user, who has already read the incomplete report. The first impression cannot be undone by a next-turn correction.

#### Pre-send checklist (read silently before you submit)

Before sending any message that includes `## ✅ Work Complete` or any phrase like "PR is ready", "mergeable, clean", "awaiting your merge it", run this checklist:

- [ ] `## ✅ Work Complete` header present
- [ ] `**Audits & deploy:**` block with all 4 lines: `✅ CI`, `✅ /plan-check: N/N`, `✅ /review: clean — 0 🔴 0 🟡 0 🔵`, `✅ Deploy: <verified behavior>`
- [ ] `---` separator after the audits block
- [ ] `**Goal:**` line (1 sentence, plain language, the user's ask restated)
- [ ] `**What changed:**` line (1-2 sentences, user-visible outcome)
- [ ] `🌐` URL lines (every env × every service touched; read project CLAUDE.md for declared URLs)
- [ ] `**[<project>] PR #<N>: <title>**` line (project name + number + actual title, never bare `#N`)
- [ ] PR URL on its own line, followed by `— mergeable, clean`
- [ ] `❓ **Question:**` line if (and only if) you need an answer
- [ ] No trailing prose after the report — the PR URL or ❓ Question is the LAST line of the message
- [ ] No "Phase N remains gated" / "next session" / "Remaining" / "Future" sections anywhere

If any box is unchecked, fix it before sending. If you cannot fill a box (e.g. don't know URLs), STOP and either find the answer (read CLAUDE.md, gh pr view) or ask the user with `❓ Question:`.

#### Rules

- Report at the END of your message, not the beginning.
- One or two sentences of preamble before the report is fine — a full narrative is not.
- Never send a partial report ("CI still running" means you are not done).
- Never include a "Remaining / Future / TODO / Follow-up" section — that's incomplete work disguised as a deliverable (see `complete-planned-work.md`).
- Use the FULL template every time. The header `## ✅ Work Complete` is a contract — using it commits you to every required field.
- No trailing prose after the report. The PR URL or ❓ Question is the LAST line.
- Most important content goes at the BOTTOM (Goal, What changed, PR URL, Question). Audit lines go at the TOP. The terminal scrolls — write for what's visible last.
- Questions for the user: `❓` marker, 1-2 sentences, very last line. No buried prose questions.
