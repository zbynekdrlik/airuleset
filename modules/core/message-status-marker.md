### Message Status Marker — Every Message Ends With ❓ / ⏳ / ✅

**Context gate — related rules you MUST also apply:**
- `completion-report.md` — the ✅ Work Complete report IS a done-state; its ❓ Question line is the question marker
- `milestone-notifications.md` — the device (Discord/phone) is pinged ONLY on `❓` / `✅` (mobile-app model); the marker IS what fires it, so the `❓`/`✅` content must be a Slovak, short, phone-readable line
- `autonomous-verification.md` — ⏳ WORKING means YOU keep working, never "user, go check it"
- `verify-launched-work-liveness.md` — a `⏳ WORKING` turn must have a bounded re-check ARMED (a dead background job sends no "done"); `⏳` is a promise to re-verify on a cadence, not a blind indefinite wait

**The user must NEVER have to guess whether you are asking them something, working in the background, or done. End EVERY message with exactly ONE status marker, on its own line, as the VERY LAST line:**

- `❓ NEEDS YOU: <the question / decision / approval, 1-2 sentences>` — you cannot proceed without the user. This is the ONLY marker that means "your turn".
- `⏳ WORKING: <what is running> — nothing needed from you, I'll report when it's done` — a background task (background Bash, CI monitor, long build, a loop you'll continue, **OR a dispatched async subagent / Agent / workflow that will re-invoke you when it finishes**) is running and you will keep going autonomously.
- `✅ DONE: <one-line outcome>` — the turn's work is finished, nothing is running, you are idle and awaiting the next instruction.

#### Rules

- Exactly ONE marker. On its own line. The very last line of the message (terminal scrolls — the last line is what the user sees).
- A background task is running → ⏳ WORKING, **NEVER** ✅ DONE. ✅ means nothing is running. Claiming "done" while something runs is the exact mislead this rule kills. **This includes a dispatched async subagent / Agent / workflow / background job that will RE-INVOKE you when it finishes** — if anything will wake this session back up other than the user, you are NOT idle-done → `⏳ WORKING`. (A session that says `✅ DONE` while a background task is still in flight, then "continues on its own" when that task re-fires, confuses the user into thinking something injected input — see `verify-launched-work-liveness.md`.)
- The only thing left is the user's decision (merge, approve, pick an option) → ❓ NEEDS YOU, not a vague "standing by".
- **`❓ NEEDS YOU` means you STOP — the user's answer is the ONLY way forward (it pings their phone "your turn").** **In a `/goal` / autopilot loop, a genuine per-ticket question is ASKED THE MOMENT the ticket needs it — NOT deferred** (the user's explicit, hard instruction: they WANT the per-ticket questions — answering them is their job; a deferred question loses the ticket's built-up context and leaves important tickets forever unsolved, because a never-empty backlog means the old "raise it only when the backlog is EXHAUSTED" trigger never fires). When the ticket can't proceed without the answer, ASK NOW: the background worker surfaces the question and HOLDS the ticket's full context (paused, alive), and you end the turn `❓ NEEDS YOU` (Slovak, the real decision) — the phone pings immediately. The loop STOPS on that ticket and resumes the instant the user answers. Do NOT label it `needs-decision` / `autopilot-skip` and grind on to bury the question; do NOT wait for "backlog exhausted". **The ONE exception — the sleep window 00:00–05:59 Europe/Bratislava** (the user is asleep; check `TZ=Europe/Bratislava date +%H` → hour `00..05`): during it ONLY, DEFER — queue the question (label `needs-decision`, leave the ticket open), keep working, end `⏳ WORKING`, and raise the queued questions as a `❓ NEEDS YOU` once the window ends (after 06:00) or the user is next active. NEVER end a turn `❓ NEEDS YOU` and then continue to a DIFFERENT ticket next turn — that pings "I'm waiting" while you've already moved on. So a waking-hours per-ticket question turn is `❓` ALONE (no continuing language — you ARE waiting); a sleep-window turn is `⏳` + the deferred question stated inline. The pending-ping hook suppresses a `❓` that co-occurs with "keep working / continuing / next ticket" language — which stays correct: only the sleep-window deferral carries continuing language, and it correctly uses `⏳`, not `❓`.
- "Standing by", "waiting on", "let me know", "your go", "should I", "no merge without your go" are AMBIGUOUS alone — they MUST carry a ❓ (if it's a question for the user) or ⏳ (if you're waiting on a background result, not on the user).
- A completion report's `## ✅ Work Complete` heading counts as the ✅ DONE marker — and in a manual-marker (`airuleset:merge=manual`) project, where the report waits on the user's merge, end it with `❓ NEEDS YOU: approve merge?` instead. Default-auto projects merge first and report ✅ DONE (`pr-merge-policy.md`).
- **The `❓` / `✅` content is forwarded to the device** (Discord/phone) when the user is idle — it is the ONLY thing that pings them (`milestone-notifications.md`, mobile-app model). So write that content in **Slovak, short (one line), self-contained** — readable on a phone with no terminal context. Keep the English keyword (`NEEDS YOU` / `DONE`); the text after the colon is Slovak. `⏳ WORKING` is NEVER forwarded (working ≠ asking ≠ done).

#### Anti-pattern (this exact message misled the user)

> "Standing by for the mutation result, then the final green-PR report. No merge without your go."

Done? Working? Asking? Unmarked → the user cannot tell. **WRONG.**

Correct, when a background run is in progress:

> ⏳ WORKING: mutation run in progress — I'll report the green-PR result when it lands. Nothing needed from you.

Correct, default-auto project after the gates went green (merge is YOURS to do, not a question):

> ✅ DONE: PR #5 merged to main (a1b2c3d), v1.2.3 deployed and verified on the dashboard.

Correct, manual-marker project, truly idle and the only open item is the merge:

> ❓ NEEDS YOU: PR #5 is green (lint/test/build/coverage/security/mutation all pass) — approve merge?

#### Banned (the Stop hook `stop-check-status-marker.sh` blocks these)

- Ending a message with background / "in progress" / "still running" / "monitoring" / "will report" language but NO ⏳ marker.
- Asking the user anything (a question, "should I", "your go", "merge it?", a trailing `?`) with NO ❓ marker.
- Claiming progress / completion (done, fixed, pushed, deployed, merged) with NO marker at all.

The intent is banned: leaving the user to guess your state. Applies to all messages and all rewordings.
