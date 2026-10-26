# Completion Report — History & Rationale (#859)

This file contains the evolution history and revision mechanics moved
VERBATIM from `modules/core/completion-report.md` during the context-diet
conversion (#859 batch 1). The template, hard rules, gates, and enforcement
stay always-on in the module.

---

## Compact-at-boundary mechanics

**#822 (2026-09-01), REVISED by #855 (2026-09-02) — under an ARMED `/goal` the drained-boundary `/compact` needs a BOUNDARY-HOLD turn, not a bare `⏳`; and the compact is TYPED at the next IDLE poll, never queued behind a running turn.** The goal Stop hook blocks every `✅` boundary, so a `/compact` typed into a running turn would only QUEUE — and #855 proved CC's type-ahead queue drain is NOT idempotent for a slash command (one queued `/compact` → two submits, the owner's double-compact). So the watchdog now types `/compact` ONLY into a genuinely idle pane; a running turn is refused (`skip:turn-running`). When `compact-request --self` prints its boundary-hold command (because the boundary compact could not run yet — `skip:turn-running`/`already-queued`), launch it as a tracked background Bash task — `sleep 45 && echo boundary-hold` via `run_in_background: true` — and end the turn `⏳ WORKING: boundary hold`. The accepted Stop leaves the pane idle for the whole 45 s sleep, so the next ~60 s watchdog sweep finds it idle and the `/compact` is typed into that idle prompt where it executes immediately, exactly once (`compact-request --status` then goes `PENDING` → `NONE`). Full mechanism + the live-verify-and-escalate caveat: `skills/autopilot/SKILL.md` Step 5. A served, non-`/goal` session (no armed goal) needs none of this — a bare `--self` at its report boundary is enough.

## Compact-request backstop history

**#411 (2026-08-13): a SECOND, MECHANICAL backstop now exists too — a genuine SAFETY NET, never a reason to skip calling `--self` yourself.** `hooks/stop-check-prose-violations.sh` (the SAME Stop hook that already validates the report's own structure) fires the identical `compact-request --record --session <sid> --cwd <cwd> --origin self-callback` call itself, best-effort and silent, the moment a turn's `## ✅ Work Complete` report clears every hard-violation check — closing the gap #400 reopened (a smaller/sonnet-tier stream session that reliably skips the prose-taught `--self` call now still gets recorded, PROVIDED the report itself is well-formed enough to pass the gate). This is strictly a backstop: it fires LATER than your own proactive call (only after the WHOLE Stop-hook evaluation runs, not the instant your turn ends) and a malformed report that gets BLOCKED never reaches it at all — so `--self` FIRST is still the primary mechanism, not an optional courtesy.

## Passive fallback retirement

**#400 (2026-08-12): there is no passive fallback any more if this call is skipped or fails.** `notify-compact-request.sh` (the Stop hook this used to describe as a fallback) is now a PERMANENT NO-OP — its text-sniffing `## ✅ Work Complete`/`✅ DONE:` channel is removed entirely, because repeated re-firing on every ordinary turn is what let a stale compact request keep looking "fresh" for 11+ hours in a live incident. Make the `--self` call proactively, don't rely on anything else to catch it — Claude Code's own native auto-compact still handles a genuinely full context, but the deliberate per-report boundary is only guaranteed by calling this yourself.

## Compact-at-boundary two NEVERs

**Two more NEVERs (2026-08-06 decision, `#228`):**
- **NEVER right after just answering a question.** The user replying to your `❓ NEEDS YOU` is a RESUMPTION point, not a completion boundary — that reply is what lets you continue the work you paused for, and compacting right then would discard the very question/answer context you just needed. Call `compact-request --self` only once the RESULTING work is itself reported done with its own genuine `## ✅ Work Complete` heading — never immediately after the answer arrives.
- **NEVER mid-work, and never when the only record of what you did is this conversation.** A clean report (no `❓`/`⏳`) proves the TURN is finished, not that the WORK is durable. If the plan, decision, or result a compaction would discard lives ONLY in this chat — not yet a commit, a merged PR, a ticket comment, or a file on disk — it is not a safe boundary; write it down first (`durable-decisions-to-tickets.md`), then compact.

## 2026-09-11 — #993/#992 area-review verdict line (`🏛 Architektúra:`)

Owner directives, verbatim:

- #993: "A tiez by som chcel aby strict podmienka na korkektnu architecturu/sota design mala najvyssiu prioritu a aby kazda zmena ktora sa ide aplikovat presla fable review ktory overi ze oblast ktorej sa zmena tika nie je patchwork bordel a nie len ta aktualne ktora sa deje ale oblast kodu ktrej sa to tyka a ak tento architecture reviewer prie na to ze znova niekde vznika patchwork miesto ktore len reaguje na poziadavky bez toho aby to bol premysleny koncept tak vytvory ticket na architektonicku prerabku danej oblasti"
- #992: "silny paralelizmus bez kontextu zmien vie sposobit brutalny treadmill problemov kedy sub agenti jeden druhemu sposobuju problemy a malo by to byt pravidlo ze niektore veci ako napr infra sa nesmu riesit paralelne — paralelne len oddelene nesuvisiace ulohy ale aj tie treba brat do kontroli ci su naozaj oddelene ... tieto veci potrebuju ist aj do airuleset na prerabku aby nebojovali nejake lokalne pravidla s airuleset pravidlami"

The compact `## ✅ Work Complete` template gained a mandatory `🏛 Architektúra: <oblasť> — OK | — REWORK #N (<názov>)` line (hook-enforced in `stop-check-prose-violations.sh`), stating the integration-time area-review verdict from the main-session (Fable) review of the whole AREA a change lands in (SKILL.md 'Main review gate' check (f)). A `REWORK` verdict files an `architecture-rework` ticket (highest lane priority, one open per area). Infra-unit reports also cite the NEXT-cycle flow metric — 'Fixed = metric, not merge' (#992). Companion doctrine: the infra-serial work class [SUPERSEDED r2b — the class-based live-infra-lane serialisation was removed; infra serialisation is now ROUTING via `--role` into the infra role, #993] + the pre-dispatch independence check (`lane-overlap` CLI + `block-dispatch-over-wdrain.sh` receipt gate).

## 2026-10-26 — #1137 (issue 1233) slim report: the pre-#1137 text, verbatim

The DEEP companion shrank to the four-line template + hard rules, and the
always-on module to one template line. The full pre-#1137 text of both, verbatim:

### `modules/core/completion-report.md` (pre-#1137)

### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — finish the job before reporting (no Remaining/Future/TODO sections)
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not liveness
- `e2e-real-user-testing.md` — E2E rows reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — auto-merge default: the report is sent AFTER merged + deployed + verified; manual-marker (`airuleset:merge=manual`) projects stop at the green PR with ❓

**MANDATORY compact template (~7 lines, #940):** `## ✅ Work Complete` heading + ONE audit-summary line (plan-check + review + requesting-code-review) + `✅ Výstup:` line + `🏛 Architektúra:` line + `🌐` URL (if UI) + Goal/What changed + `📔 Playbook:` + terminal marker. `✅ Výstup:` is ALWAYS present — concrete OBSERVED values, or `n/a — <prečo>`. `🏛 Architektúra: <oblasť> — OK|REWORK fixed|REWORK #N` = area-review verdict (#993). Hook-enforced (`stop-check-prose-violations.sh`).

The full template, hard rules, and enforcement details are in the situational companion `skills/completion-report-deep/DEEP.md` — loaded automatically on `plan-check`/`gh pr merge` commands. History + rationale: `.claude/rules-reference/completion-report-history.md` (#859).

### `skills/completion-report-deep/DEEP.md` (pre-#1137)

### Completion Report

**Context gate — related rules you MUST also apply:**
- `complete-planned-work.md` — finish the job before reporting (no Remaining/Future/TODO sections)
- `autonomous-verification.md` — ✅ means functional verification (clicked, confirmed), not liveness
- `e2e-real-user-testing.md` — E2E rows reference real Playwright tests, not API smokes
- `pr-merge-policy.md` — auto-merge default: the report is sent AFTER merged + deployed + verified; manual-marker (`airuleset:merge=manual`) projects stop at the green PR with ❓

**The completion report's audience is the USER, not you.** Terminal scrolls — only the LAST passage is visible without scrolling back. Audits at TOP, user-facing answers at BOTTOM. Send the report as the LAST thing in your message.

#### MANDATORY compact template (#940 — ~7 lines, not ~20)

```
## ✅ Work Complete

✅ /plan-check: N/N · /review: 0 🔴 0 🟡 0 🔵 · /requesting-code-review: 0 🔴 0 🟡 0 🔵
✅ Výstup: <konkrétne hodnoty z artefaktu> | n/a — <prečo>
🏛 Architektúra: <oblasť> — OK | <oblasť> — REWORK fixed | <oblasť> — REWORK #N (<názov>)
✅ Regression test: <test>:<line> — RED <sha>, GREEN <sha>  ← bug-fix PRs only; OMIT for non-bug
🌐 <url>                    ← user-clickable URL; one per env × surface; omit if no web UI
**Goal:** <1 sentence> — **What changed:** <1-2 sentences>
📔 Playbook: <captured | n/a>
✅ DONE: <one-line>
```

The audit-summary line packs plan-check + review + requesting-code-review into ONE line. Výstup stays its own `✅`-prefixed line (hook-enforced). Deploy evidence merges into Výstup (the deployed version IS the read-back value). CI is implicit (merged = CI green). Use ❌/⏳ if failed/in-progress — then NOT done.

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
- **Order matters.** Audits at TOP, Goal/What changed/URLs at BOTTOM. The user reads the bottom of the terminal first.
- **🌐 lines = USER-CLICKABLE web URLs only.** Backend/API URLs (`:8000`, `/api/`, `backend:`) go in the `✅ Výstup:` line as evidence context, never in 🌐. URLs in prose (`curl http://...`, `verified at https://...`) do NOT count.
- **Multi-env deploy ⇒ ≥2 🌐 lines** (one per env × user-facing surface). Read project CLAUDE.md `## Dashboards` / `## URLs` for declared URLs. If you cannot determine the URL set, ask via `❓ Question:` rather than ship a report missing URLs.
- **The 🌐/📱 requirement is "every user-facing artifact this work produced or affects" — the env×surface rule above is the deploy-shaped CASE of it, not the whole rule.** For a client-app project (a mobile/desktop app the user installs, not just a web dashboard) this means BOTH `🌐 Demo:` (the running app the user can click NOW) AND `📱 <platform>:` (the installable build — APK/IPA/signed binary) — on every ticket that touched the app, not only the ticket that happened to produce them. Both verified LIVE (HTTP 200 / a real, current download) before pasting, same no-dead-links discipline as any other 🌐 line — see `no-localhost-urls.md`.
- **📱 lines = the installable-build DOWNLOAD URL only — reserved exactly like 🌐, never a decorative "mobile" note in prose.** `📱 iOS: <url>` / `📱 <platform>: <url>` names the artifact link itself; a sentence merely mentioning mobile testing, an emulator, or a phone does NOT get a 📱-prefixed line just because it discusses mobile — put that in prose without the marker.
- **Never make the user search the transcript for an artifact URL.** If a link (demo, APK, dashboard, anything else) was produced earlier in THIS session and is still current, REPEAT it in the report — never a back-reference ("see above", "same URL as before", "unchanged from last ticket"). The report is self-contained; the user does not scroll back through the terminal to find it.
- **Goal + What changed = plain language.** Restate the user's ask in their words. NOT implementation jargon. If you cannot summarize in 1+2 sentences a non-engineer would understand, you don't understand the work yet.
- **Issue/PR refs MUST include titles.** `PR #54` / `Fixes #234` alone is wrong. `PR #54: Refactor driver.rs and add lyrics test` / `Fixes #234 (driver.rs over 1000-line cap)` is right. Apply everywhere — completion reports, plan steps, follow-up suggestions.
- **Questions MUST be marked with ❓** as the very LAST line. Trailing `?` without ❓ is banned. ONE decision only, shaped as the structured Slovak question block (`**Otázka — projekt …:**` briefing + options + the ❓ line — `user-questions-slovak.md`, hook-enforced). If you have nothing to ask, OMIT the line.
- **✅ means CONFIRMED WORKING.** ⏳ or ❌ on any line = NOT done; do not send the report yet.
- **No "Remaining / Future / TODO / Follow-up" sections** — that's incomplete work disguised as a deliverable. Genuinely-out-of-scope work is recorded on the current ticket (a `- [ ]` item) and cited; don't add it to the report.
- **🔵 review findings inside the diff = MUST FIX.** No skipping as "minor / stylistic / nice-to-have / out of scope / deferred". The audit line `0 🔴 0 🟡 0 🔵` is non-negotiable. A finding OUTSIDE the diff is fixed IN THE SAME BRANCH, not filed as a follow-up, UNLESS it genuinely clears the follow-up gate (`complete-planned-work.md`'s six criteria — >300 LoC, schema migration, API break, security boundary, cross-cutting, or a genuine user decision) — "it's technically outside the diff" is NOT itself a criterion. A finding in code ADJACENT to your diff (a file you already touched, or one the review flagged BECAUSE of your change) under ~100 LoC is DO NOW, same branch, same as any other small cleanup (`#311`: this exact loophole let review-finding follow-ups chain unboundedly — 7 tickets from one root cause). Only a finding that honestly meets one of the six criteria gets filed, and only with a `Scope-gate:` line naming which one.
- **`/requesting-code-review` MUST also pass clean.** `/review` is a fast first-pass; `superpowers:requesting-code-review` is the deep second-pass that historically catches issues `/review` misses. Both audit lines are required — but `/review` must NEVER be satisfied by literally invoking the built-in `Skill({skill: "review"})`/`code-review` platform skill (it is not airuleset's own, and it has spiraled into a disproportionate multi-agent fan-out, become cross-task addressable, and orphaned silently across a session-limit reset — `agents/autopilot-worker.md` CYCLE step 6, #363); self-apply the standards directly, or dispatch ONE fresh-context `general-purpose` subagent instead. Fix every 🔴/🟡/🔵 from BOTH; only then send the report. Skipping the deep pass to "save time" is banned — the user always runs it afterwards and the missed issues come back as rework.
- **localhost is banned in URLs** — see `no-localhost-urls.md`. Use real IPs. Verify each URL returns 200 before pasting.
- **Bug-fix PR ⇒ `✅ Regression test:` line is REQUIRED.** Triggered when the PR closes/fixes a `bug`-labeled issue, the title contains `fix`/`bugfix`/`hotfix`/`patch`/`regression`, or the work fixed a defect. The line MUST cite the test file path, line number, the test commit SHA (RED — test failing without the fix), and the fix commit SHA (GREEN — test passing with the fix). Stop hook blocks bug-fix reports missing this line. See `regression-test-first.md`.
- **`🏛 Architektúra:` is ALWAYS present — the integration-time area-review verdict (#993).** Each integrated change passes a main-session review of the whole AREA the change lands in — not just the diff — for a verdict: `🏛 Architektúra: <oblasť> — OK` when the area is a considered concept with one source of truth, or `— REWORK fixed` when it is layers of reactions to requests (patchwork) and the REWORK is fixed in the same change; only a separate, larger defect that cannot be fixed there becomes ONE `architecture-rework` ticket (one open per area) under the normal filing gate: `— REWORK #N (<názov>)`. The `<oblasť>` names the taxonomy area (airuleset: the `.claude/rules/internals-<area>.md` split — watchdog/hooks/notify/…/modules/skills/agents; other projects: the playbook `.claude/rules/<area>.md`, else the top-level directory the diff touches). **Infra-unit reports (CI workflows, hooks/gates, release chain, deploy tooling, runner pool, airuleset modules/skills/agents) additionally cite the NEXT-cycle FLOW METRIC** — "Fixed = metric, not merge" (#992): a merged PR is not the proof; the flow number from the following cycle is (deploy gap, gate false-positive count, rounds per hand-off). Hook-enforced (`stop-check-prose-violations.sh`): line missing = blocked.
- **`✅ Výstup:` is ALWAYS present — concrete OBSERVED values read back from the REAL artifact, or an explicit `n/a — <prečo>` (#446, montalu3 email incident 2026-08-13).** Work that produced or changed a user-facing OUTPUT artifact (email, document, render, UI screen, notification, report) must cite values you actually READ from that artifact — `✅ Výstup: email obj. 2041 — cena 12,50 €, mena CZK, zákaznícke číslo zvýraznené` — never "sent OK"/"delivered"/"doručené"/"funguje" (send/delivery is LIVENESS, not content: the montalu3 order-status emails went out with 0 € prices while only send/delivery was verified). Work with genuinely NO user-facing output states `✅ Výstup: n/a — <prečo>` explicitly — and an `n/a` in a report that lists any 🌐/📱 surface is a self-contradiction (that surface IS a user-facing output — read something from it: the rendered page, the UI values, the version label). SOTA/architecture verification is NOT re-stated here — it lives in the ticket's own `Architektúra:` design comment (#414). Hook-enforced (`stop-check-prose-violations.sh`): line missing, value-free (no digit/quoted value), bare `n/a` without a reason, or `n/a` alongside a 🌐/📱 line = blocked.

#### Pre-completion gate (run BEFORE writing the report)

1. Invoke `plan-check` skill — fix any `[ ]` NOT DONE items.
2. Apply `/review` standards (Correctness / Security / Performance / Maintainability / Style) — fix every 🔴 critical, 🟡 warning, AND 🔵 suggestion inside the diff. **Never invoke the built-in `Skill({skill: "review"})`/`code-review` tool for this** — it is a Claude Code platform skill this repo does not own, and it has proven to spiral into a disproportionate multi-agent fan-out, become cross-task addressable, and orphan silently across a session-limit reset (`agents/autopilot-worker.md` CYCLE step 6, #363). Self-apply the standards directly, or dispatch ONE self-contained fresh-context `general-purpose` subagent — never the built-in skill.
3. Invoke `superpowers:requesting-code-review` skill — the DEEP pass. Fix every 🔴/🟡/🔵 it surfaces. This historically catches issues `/review` misses; the user always runs it after the report, so skipping = guaranteed rework.
4. Read back the OUTPUT artifact the work produced/changed — open the real thing (the sent email from the DB, the rendered document, the live UI screen) and note the concrete values you SEE; that read-back is what the `✅ Výstup:` line cites (or establish honestly that no user-facing output exists → the explicit `n/a — <prečo>` form).
5. The audit-summary line + `✅ Výstup:` line MUST carry all FOUR tokens:
   - `/plan-check: N/N` (on the audit-summary line)
   - `/review:` with `0 🔴 0 🟡 0 🔵` (spaces between number and emoji — hook-enforced)
   - `/requesting-code-review:` with `0 🔴 0 🟡 0 🔵`
   - `✅ Výstup:` on its OWN `✅`-prefixed line (hook requires `✅` adjacent to `Výstup:`)

If ANY audit fails, you are NOT done — fix the findings, re-run, then send.

#### Length budget — ~7 lines (#940)

The whole report fits in ~7 lines (audit-summary + Vystup + optional regression + 🌐 + Goal/What changed + Playbook + marker). The diff is the evidence; the report is the summary. If you're writing more, you're over-explaining.

#### Enforcement

The Stop hook (`stop-check-prose-violations.sh`) BLOCKS completion reports missing required structure (Goal / What changed / plan-check / review lines, a missing or value-free `✅ Výstup:` line — including a bare `n/a` with no reason and an `n/a` alongside a 🌐/📱 surface —, wrong order, missing 🌐 for multi-env deploys, banned shortcut menus) and HARD-blocks a `🌐` or `📱` line pointing at localhost/127.0.0.1/0.0.0.0. When blocked, fix the report and resend in the same turn. The hook covers all detectable violations; trust it to catch your slips, but write the full template the first time so blocking is rare. It cannot mechanically check whether a client-app project's `🌐 Demo:`/`📱 <platform>:` lines are actually PRESENT — that obligation is yours to apply from the rule above.

#### Compaction — REMOVED (#1084, owner ROZHODNUTÉ 2026-09-19)

**Machine-triggered compacts are gone for good** ("už tie compacty vôbec nechcem … samotné deploye na targety by to mali zabezpečiť"). There is no callback mechanism, no request to record, no boundary-hold — no session ever types `/compact`. Claude Code's own threshold autocompact is the only compaction left, and a completed `## ✅ Work Complete` report is simply a natural safe point for it. Write the report and end the turn as normal; there is nothing compact-related to do. History + rationale — the removed compact-at-boundary machinery (#911/#822/#855/#411/#400/#228): `.claude/rules-reference/completion-report-history.md` (#859).

#### Rules summary

- Report at the END of your message, not the beginning.
- Compaction is native-autocompact only (#1084) — machine compacts are REMOVED; no manual call, no `/compact` ever typed.
- Use the FULL template; no prose substitutes.
- Audits at TOP, Goal / URLs / PR / Question at BOTTOM.
- Most important content goes LAST (terminal scrolls).
- One push to send → no retroactive corrections (the user already read it).
