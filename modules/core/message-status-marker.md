### Message Status Marker — Every Message Ends With ❓ / ⏳ / ✅

**Context gate — related rules you MUST also apply:**
- `completion-report.md` — the ✅ Work Complete report IS a done-state; its ❓ Question line is the question marker
- `milestone-notifications.md` — ping on phase achieved; the marker is the per-message version of the same "tell the user the state" discipline
- `autonomous-verification.md` — ⏳ WORKING means YOU keep working, never "user, go check it"

**The user must NEVER have to guess whether you are asking them something, working in the background, or done. End EVERY message with exactly ONE status marker, on its own line, as the VERY LAST line:**

- `❓ NEEDS YOU: <the question / decision / approval, 1-2 sentences>` — you cannot proceed without the user. This is the ONLY marker that means "your turn".
- `⏳ WORKING: <what is running> — nothing needed from you, I'll report when it's done` — a background task (background Bash, CI monitor, long build, a loop you'll continue) is running and you will keep going autonomously.
- `✅ DONE: <one-line outcome>` — the turn's work is finished, nothing is running, you are idle and awaiting the next instruction.

#### Rules

- Exactly ONE marker. On its own line. The very last line of the message (terminal scrolls — the last line is what the user sees).
- A background task is running → ⏳ WORKING, **NEVER** ✅ DONE. ✅ means nothing is running. Claiming "done" while something runs is the exact mislead this rule kills.
- The only thing left is the user's decision (merge, approve, pick an option) → ❓ NEEDS YOU, not a vague "standing by".
- "Standing by", "waiting on", "let me know", "your go", "should I", "no merge without your go" are AMBIGUOUS alone — they MUST carry a ❓ (if it's a question for the user) or ⏳ (if you're waiting on a background result, not on the user).
- A completion report's `## ✅ Work Complete` heading counts as the ✅ DONE marker — and in a manual-marker (`airuleset:merge=manual`) project, where the report waits on the user's merge, end it with `❓ NEEDS YOU: approve merge?` instead. Default-auto projects merge first and report ✅ DONE (`pr-merge-policy.md`).

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
