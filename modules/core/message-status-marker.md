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
- **`❓ NEEDS YOU` and continuing-to-work are MUTUALLY EXCLUSIVE.** `❓` means you STOP and the user's answer is the ONLY way forward — it pings their phone "your turn". If you can do ANY useful work without the answer (another ticket, another task), you are NOT blocked → end `⏳ WORKING` and DEFER the question: state it inline (no `❓` marker, so no phone ping) and keep going. **In a `/goal` / autopilot loop, a per-ticket question is ALWAYS deferred** — set that ticket aside (label `needs-decision` / `autopilot-skip`), keep working the rest, end `⏳ WORKING`; collect the deferred questions and raise them as ONE `❓ NEEDS YOU` only when the workable backlog is EXHAUSTED (you'd otherwise sit idle). NEVER end a turn `❓ NEEDS YOU` and then continue next turn — that pings the user "I'm waiting" while you've already moved on (the exact mislead this rule kills). The pending-ping hook suppresses a `❓` that co-occurs with "keep working / continuing / next ticket" language, but the marker must be right at the source.
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
