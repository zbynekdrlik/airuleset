### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — finish the job before reporting (no Remaining/Future/TODO sections)
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not liveness
- `e2e-real-user-testing.md` — E2E rows reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — auto-merge default: the report is sent AFTER merged + deployed + verified; manual-marker (`airuleset:merge=manual`) projects stop at the green PR with ❓

**The completion report's audience is the USER, not you.** Terminal scrolls — only the LAST passage is visible without scrolling back. Audits at TOP, user-facing answers at BOTTOM. Send the report as the LAST thing in your message.

#### MANDATORY structure (use this EXACT template)

```
## ✅ Work Complete

**Audits & deploy:**
✅ CI: green
✅ /plan-check: N/N fulfilled
✅ /review: clean — 0 🔴 0 🟡 0 🔵
✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵 (or addressed in commit <sha>)
✅ Deploy: <user-visible behavior verified on the live target — include version label read from DOM>
✅ Regression test: <test_path>:<line> — RED on <test_sha>, GREEN on <fix_sha>   ← REQUIRED for bug-fix PRs (see regression-test-first.md); OMIT for non-bug PRs
✅ Výstup: <konkrétne hodnoty ODČÍTANÉ z reálneho artefaktu — email/dokument/render/UI/notifikácia> | n/a — <prečo>   ← ALWAYS required (see Hard rules); never "sent OK"

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
🌐 Demo: <url>          ← client-app projects: the running demo the user can click NOW — every ticket that touched the app
📱 APK:  <url>          ← client-app projects: the installable build (APK/IPA/signed binary) — same rule, every ticket

**[<project>] PR #<N>: <full PR title>**
<full PR URL> — merged <merge-sha>        ← default-auto; manual-marker projects: `— mergeable, clean` + end with ❓ approve merge

❓ **Question:** <concise 1-2 sentence question>   ← only if you actually need an answer
```

Use ❌ instead of ✅ if something failed. Use ⏳ if still in progress — then you are NOT done; wait until everything is ✅ before sending.

#### Reduced-authority (fork-no-merge / branch-merge) variant — SAME template, hand-off lines instead of merge/deploy

A stream with `airuleset.py authority` != full has no PR-to-main / merge / deploy — but the report obligations are IDENTICAL (heading + audits + `---` + Goal + What changed; the PR-less gate in `stop-check-prose-violations.sh` enforces it — a bare `✅ DONE: #N hotové` is blocked, the david@gk failure 2026-07-11). Replace only the flow-shaped lines:

```
✅ Lokálne overenie: <tests + lint result on the fork/integration branch>
✅ Hand-off: READY-FOR-REVIEW komentár na #N (<topic>) + --handoff karta   ← fork-no-merge AND branch-merge (SAME hand-off comment convention for both, posted once the profile's own end-point is reached — branch-merge: after its integration-branch merge; fork-no-merge: after the fork branch push, no merge exists there at all; repo automation labels it `ready-for-review`; NEVER a self-close)
✅ PR: #M do <integration branch> zmergnutý <sha>                          ← branch-merge (ends there; ticket stays OPEN — gatekeeper closes it only after the full `/process-subdev` release pipeline, #349; EXCEPT on odoo-erp, where after the gk review-verdict + every queue label dropped the delivering STREAM self-closes with an evidence `--comment`, odoo-erp#5378 / #756)
```

No 🌐/Deploy lines (nothing deployed by this stream); Goal + What changed stay mandatory and plain-language — and so does `✅ Výstup:` (the artifact read-back runs on the local/integration environment: the rendered email from the test DB, the local UI screen, the generated document).
**`branch-merge` NEVER omits the Hand-off line** — merging into the project's INTEGRATION
branch does NOT auto-close the ticket (that branch is not the repo's default branch, so
GitHub's `Closes #N` never fires there), and skipping the hand-off comment leaves the
ticket invisible to `/process-subdev`'s queue (#349, the montalu3 regression: three
tickets were self-closed with no hand-off at all and sat neither queued nor reviewed).

#### Hard rules

- **FULL template every time.** Writing `## ✅ Work Complete` is a contract — every required field MUST appear. Prose substitutes ("STOP at green PR URL", "Awaiting merge", "Phase N gated") are banned. Any rewording of the same intent is also banned.
- **Order matters.** Audits at TOP, `---` separator, Goal/What changed/URLs/PR/Question at BOTTOM. The user reads the bottom of the terminal first.
- **🌐 lines = USER-CLICKABLE web URLs only.** Backend/API URLs (`:8000`, `/api/`, `backend:`) go in `✅ Deploy:` as evidence, never in 🌐. URLs in prose (`curl http://...`, `verified at https://...`) do NOT count.
- **Multi-env deploy ⇒ ≥2 🌐 lines** (one per env × user-facing surface). Read project CLAUDE.md `## Dashboards` / `## URLs` for declared URLs. If you cannot determine the URL set, ask via `❓ Question:` rather than ship a report missing URLs.
- **The 🌐/📱 requirement is "every user-facing artifact this work produced or affects" — the env×surface rule above is the deploy-shaped CASE of it, not the whole rule.** For a client-app project (a mobile/desktop app the user installs, not just a web dashboard) this means BOTH `🌐 Demo:` (the running app the user can click NOW) AND `📱 <platform>:` (the installable build — APK/IPA/signed binary) — on EVERY ticket that touched the app, not only the ticket that happened to produce them. Both verified LIVE (HTTP 200 / a real, current download) before pasting, same no-dead-links discipline as any other 🌐 line — see `no-localhost-urls.md`.
- **📱 lines = the installable-build DOWNLOAD URL only — reserved exactly like 🌐, never a decorative "mobile" note in prose.** `📱 iOS: <url>` / `📱 <platform>: <url>` names the artifact link itself; a sentence merely mentioning mobile testing, an emulator, or a phone does NOT get a 📱-prefixed line just because it discusses mobile — put that in prose without the marker.
- **Never make the user search the transcript for an artifact URL.** If a link (demo, APK, dashboard, anything else) was produced earlier in THIS session and is still current, REPEAT it in the report — never a back-reference ("see above", "same URL as before", "unchanged from last ticket"). The report is self-contained; the user does not scroll back through the terminal to find it.
- **Goal + What changed = plain language.** Restate the user's ask in their words. NOT implementation jargon. If you cannot summarize in 1+2 sentences a non-engineer would understand, you don't understand the work yet.
- **Issue/PR refs MUST include titles.** `PR #54` / `Fixes #234` alone is wrong. `PR #54: Refactor driver.rs and add lyrics test` / `Fixes #234 (driver.rs over 1000-line cap)` is right. Apply everywhere — completion reports, plan steps, follow-up suggestions.
- **Questions MUST be marked with ❓** as the very LAST line. Trailing `?` without ❓ is banned. ONE decision only, shaped as the structured Slovak question block (`**Otázka — projekt …:**` briefing + options + the ❓ line — `user-questions-slovak.md`, hook-enforced). If you have nothing to ask, OMIT the line.
- **✅ means CONFIRMED WORKING.** ⏳ or ❌ on any line = NOT done; do not send the report yet.
- **No "Remaining / Future / TODO / Follow-up" sections** — that's incomplete work disguised as a deliverable. If you discover genuinely-out-of-scope work, file a GitHub issue with a clear title and reference it; don't add it to the report.
- **🔵 review findings inside the diff = MUST FIX.** No skipping as "minor / stylistic / nice-to-have / out of scope / deferred". The audit line `0 🔴 0 🟡 0 🔵` is non-negotiable. A finding OUTSIDE the diff is fixed IN THE SAME BRANCH, not filed as a follow-up, UNLESS it genuinely clears the follow-up gate (`complete-planned-work.md`'s six criteria — >300 LoC, schema migration, API break, security boundary, cross-cutting, or a genuine user decision) — "it's technically outside the diff" is NOT itself a criterion. A finding in code ADJACENT to your diff (a file you already touched, or one the review flagged BECAUSE of your change) under ~100 LoC is DO NOW, same branch, same as any other small cleanup (`#311`: this exact loophole let review-finding follow-ups chain unboundedly — 7 tickets from one root cause). Only a finding that honestly meets one of the six criteria gets filed, and only with a `Scope-gate:` line naming which one.
- **`/requesting-code-review` MUST also pass clean.** `/review` is a fast first-pass; `superpowers:requesting-code-review` is the deep second-pass that historically catches issues `/review` misses. Both audit lines are required — but `/review` must NEVER be satisfied by literally invoking the built-in `Skill({skill: "review"})`/`code-review` platform skill (it is not airuleset's own, and it has spiraled into a disproportionate multi-agent fan-out, become cross-task addressable, and orphaned silently across a session-limit reset — `agents/autopilot-worker.md` CYCLE step 6, #363); self-apply the standards directly, or dispatch ONE fresh-context `general-purpose` subagent instead. Fix every 🔴/🟡/🔵 from BOTH; only then send the report. Skipping the deep pass to "save time" is banned — the user always runs it afterwards and the missed issues come back as rework.
- **localhost is banned in URLs** — see `no-localhost-urls.md`. Use real IPs. Verify each URL returns 200 before pasting.
- **Bug-fix PR ⇒ `✅ Regression test:` line is REQUIRED.** Triggered when the PR closes/fixes a `bug`-labeled issue, the title contains `fix`/`bugfix`/`hotfix`/`patch`/`regression`, or the work fixed a defect. The line MUST cite the test file path, line number, the test commit SHA (RED — test failing without the fix), and the fix commit SHA (GREEN — test passing with the fix). Stop hook blocks bug-fix reports missing this line. See `regression-test-first.md`.
- **`✅ Výstup:` is ALWAYS present — concrete OBSERVED values read back from the REAL artifact, or an explicit `n/a — <prečo>` (#446, montalu3 email incident 2026-08-13).** Work that produced or changed a user-facing OUTPUT artifact (email, document, render, UI screen, notification, report) must cite values you actually READ from that artifact — `✅ Výstup: email obj. 2041 — cena 12,50 €, mena CZK, zákaznícke číslo zvýraznené` — never "sent OK"/"delivered"/"doručené"/"funguje" (send/delivery is LIVENESS, not content: the montalu3 order-status emails went out with 0 € prices while only send/delivery was verified). Work with genuinely NO user-facing output states `✅ Výstup: n/a — <prečo>` explicitly — and an `n/a` in a report that lists any 🌐/📱 surface is a self-contradiction (that surface IS a user-facing output — read something from it: the rendered page, the UI values, the version label). SOTA/architecture verification is NOT re-stated here — it lives in the ticket's own `Architektúra:` design comment (#414). Hook-enforced (`stop-check-prose-violations.sh`): line missing, value-free (no digit/quoted value), bare `n/a` without a reason, or `n/a` alongside a 🌐/📱 line = blocked.

#### Pre-completion gate (run BEFORE writing the report)

1. Invoke `plan-check` skill — fix any `[ ]` NOT DONE items.
2. Apply `/review` standards (Correctness / Security / Performance / Maintainability / Style) — fix every 🔴 critical, 🟡 warning, AND 🔵 suggestion inside the diff. **Never invoke the built-in `Skill({skill: "review"})`/`code-review` tool for this** — it is a Claude Code platform skill this repo does not own, and it has proven to spiral into a disproportionate multi-agent fan-out, become cross-task addressable, and orphan silently across a session-limit reset (`agents/autopilot-worker.md` CYCLE step 6, #363). Self-apply the standards directly, or dispatch ONE self-contained fresh-context `general-purpose` subagent — never the built-in skill.
3. Invoke `superpowers:requesting-code-review` skill — the DEEP pass. Fix every 🔴/🟡/🔵 it surfaces. This historically catches issues `/review` misses; the user always runs it after the report, so skipping = guaranteed rework.
4. Read back the OUTPUT artifact the work produced/changed — open the real thing (the sent email from the DB, the rendered document, the live UI screen) and note the concrete values you SEE; that read-back is what the `✅ Výstup:` line cites (or establish honestly that no user-facing output exists → the explicit `n/a — <prečo>` form).
5. All FOUR audit lines MUST appear in the audits block:
   - `✅ /plan-check: N/N fulfilled`
   - `✅ /review: clean — 0 🔴 0 🟡 0 🔵`
   - `✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵`
   - `✅ Výstup: <konkrétne pozorované hodnoty> | n/a — <prečo>`

If ANY audit fails, you are NOT done — fix the findings, re-run, then send.

#### Length budget — ~20 lines

The whole report fits in ~20 lines (audits + optional plan steps + Goal + What changed + 🌐 + PR + maybe ❓). The diff is the evidence; the report is the summary. If you're writing more, you're over-explaining.

#### Enforcement

The Stop hook (`stop-check-prose-violations.sh`) BLOCKS completion reports missing required structure (Goal / What changed / plan-check / review lines, a missing or value-free `✅ Výstup:` line — including a bare `n/a` with no reason and an `n/a` alongside a 🌐/📱 surface —, wrong order, missing 🌐 for multi-env deploys, banned shortcut menus) and HARD-blocks a `🌐` or `📱` line pointing at localhost/127.0.0.1/0.0.0.0. When blocked, fix the report and resend in the same turn. The hook covers all detectable violations; trust it to catch your slips, but write the full template the first time so blocking is rare. It cannot mechanically check whether a client-app project's `🌐 Demo:`/`📱 <platform>:` lines are actually PRESENT — that obligation is yours to apply from the rule above.

#### Compact at your own boundary — served (non-worker) sessions too (#228)

A genuinely-complete `## ✅ Work Complete` report is your own task boundary too — exactly like the autopilot-worker's per-ticket completion (`skills/autopilot/SKILL.md` already teaches the `/goal` loop to call this at each ticket's own boundary). This applies to a served, interactive session — one NOT running an armed `/goal` autopilot loop, which already knows this — because the mechanism itself needed no widening for it: `airuleset.py compact-request --self` (`#225`) is already agent-type-agnostic, resolving the calling pane from `$TMUX_PANE` and that pane's own active transcript regardless of who calls it. What was missing was only the teaching for a served session to reach for it at its own natural boundary.

**Call `compact-request --self` FIRST, as its own tool call, BEFORE writing the report text — never after.** The moment you have internally confirmed the task is genuinely done (about to send `## ✅ Work Complete`, not `⏳`/`❓`), run `python3 ~/devel/airuleset/airuleset.py compact-request --self` — it returns PROMPTLY (a single bounded wait of a couple of seconds at most, never a multi-second hold; if the pane is not safe to type into right now, the request is simply left pending for the periodic sweep — see `watchdog/compact.py`'s own module docstring, #402) — THEN write the report as your turn's actual final content. Calling it AFTER the report would risk the report no longer being your last assistant message, which is what the phone ping and the report-structure gate both key on — putting the call first avoids that question entirely. A non-zero exit just means this session isn't in a recognized tmux pane; ignore it and write the report as normal.

**#400 (2026-08-12): there is no passive fallback any more if this call is skipped or fails.** `notify-compact-request.sh` (the Stop hook this used to describe as a fallback) is now a PERMANENT NO-OP — its text-sniffing `## ✅ Work Complete`/`✅ DONE:` channel is removed entirely, because repeated re-firing on every ordinary turn is what let a stale compact request keep looking "fresh" for 11+ hours in a live incident. Make the `--self` call proactively, don't rely on anything else to catch it — Claude Code's own native auto-compact still handles a genuinely full context, but the deliberate per-report boundary is only guaranteed by calling this yourself.

**#822 (2026-09-01), REVISED by #855 (2026-09-02) — under an ARMED `/goal` the drained-boundary `/compact` needs a BOUNDARY-HOLD turn, not a bare `⏳`; and the compact is TYPED at the next IDLE poll, never queued behind a running turn.** The goal Stop hook blocks every `✅` boundary, so a `/compact` typed into a running turn would only QUEUE — and #855 proved CC's type-ahead queue drain is NOT idempotent for a slash command (one queued `/compact` → two submits, the owner's double-compact). So the watchdog now types `/compact` ONLY into a genuinely idle pane; a running turn is refused (`skip:turn-running`). When `compact-request --self` prints its boundary-hold command (because the boundary compact could not run yet — `skip:turn-running`/`already-queued`), launch it as a tracked background Bash task — `sleep 45 && echo boundary-hold` via `run_in_background: true` — and end the turn `⏳ WORKING: boundary hold`. The accepted Stop leaves the pane idle for the whole 45 s sleep, so the next ~60 s watchdog sweep finds it idle and the `/compact` is typed into that idle prompt where it executes immediately, exactly once (`compact-request --status` then goes `PENDING` → `NONE`). Full mechanism + the live-verify-and-escalate caveat: `skills/autopilot/SKILL.md` Step 5. A served, non-`/goal` session (no armed goal) needs none of this — a bare `--self` at its report boundary is enough.

**#411 (2026-08-13): a SECOND, MECHANICAL backstop now exists too — a genuine SAFETY NET, never a reason to skip calling `--self` yourself.** `hooks/stop-check-prose-violations.sh` (the SAME Stop hook that already validates the report's own structure) fires the identical `compact-request --record --session <sid> --cwd <cwd> --origin self-callback` call itself, best-effort and silent, the moment a turn's `## ✅ Work Complete` report clears every hard-violation check — closing the gap #400 reopened (a smaller/sonnet-tier stream session that reliably skips the prose-taught `--self` call now still gets recorded, PROVIDED the report itself is well-formed enough to pass the gate). This is strictly a backstop: it fires LATER than your own proactive call (only after the WHOLE Stop-hook evaluation runs, not the instant your turn ends) and a malformed report that gets BLOCKED never reaches it at all — so `--self` FIRST is still the primary mechanism, not an optional courtesy.

**Only the FULL `## ✅ Work Complete` heading counts as this trigger — never a bare `✅ DONE:` line.** A bare `✅ DONE:` (per `message-status-marker.md`) can mean nothing more than "this turn is finished, nothing is running", including a trivial one-line answer. `--self` records under the same proven-boundary origin the worker's own per-ticket boundary uses, which deliberately SKIPS the `#99` no-work and `#48` small-context substantiality gates (`watchdog/__init__.py`'s own `#126` comment: "the boundary is the TICKET, not the size of its diff") — correct for a real completed task with its own audits and deliverable, but it would compact on every trivial exchange if triggered off the lighter marker too. Restricting the trigger to the full heading is what keeps that exemption safe.

**Two more NEVERs (2026-08-06 decision, `#228`):**
- **NEVER right after just answering a question.** The user replying to your `❓ NEEDS YOU` is a RESUMPTION point, not a completion boundary — that reply is what lets you continue the work you paused for, and compacting right then would discard the very question/answer context you just needed. Call `compact-request --self` only once the RESULTING work is itself reported done with its own genuine `## ✅ Work Complete` heading — never immediately after the answer arrives.
- **NEVER mid-work, and never when the only record of what you did is this conversation.** A clean report (no `❓`/`⏳`) proves the TURN is finished, not that the WORK is durable. If the plan, decision, or result a compaction would discard lives ONLY in this chat — not yet a commit, a merged PR, a ticket comment, or a file on disk — it is not a safe boundary; write it down first (`durable-decisions-to-tickets.md`), then compact.

#### Rules summary

- Report at the END of your message, not the beginning.
- Served session, genuinely done → `compact-request --self` BEFORE the report text, never after (see `#### Compact at your own boundary` above).
- Use the FULL template; no prose substitutes.
- Audits at TOP, Goal / URLs / PR / Question at BOTTOM.
- Most important content goes LAST (terminal scrolls).
- One push to send → no retroactive corrections (the user already read it).
