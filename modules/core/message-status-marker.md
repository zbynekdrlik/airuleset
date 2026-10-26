### Message Status Marker — Every Message Ends With ❓ / ⏳ / ✅

<!-- History: .claude/rules-reference/message-status-marker-history.md -->

**End EVERY message with exactly ONE status marker, on its own line, as the very last line:**

- `❓ NEEDS YOU: <question / decision>` — you are BLOCKED: nothing else is workable without the answer, so you stop and wait.
- `⏳ WORKING: <what runs>` — something GENUINELY still in flight will re-invoke you (background Bash, a CI poll, a dispatched subagent or workflow), with a bounded re-check armed (`verify-launched-work-liveness.md`). Never `✅` while anything runs.
- `✅ DONE: <one-line outcome>` — nothing runs, you are idle; a `## ✅ Work Complete` report counts. Inside an ARMED `/goal` a finished, verified unit ends `✅ DONE` — the footer's `◎ /goal` says more work follows, never a `⏳` placeholder.

Hooks: `stop-check-status-marker.sh` (missing or contradicting marker), `stop-check-question-quality.sh` (question shape, re-emission).

**Questions:**

- A question is ASKED THE MOMENT it arises, 24/7 — no night gating; blocked = ask, never idle-park. A genuine question ALWAYS pings the phone, owner-scoped (#710): david = Discord ping; zbynek = footer `U N` + webterm (the suppression is logged, never silent). The session discipline is the same for every owner.
- Two honest forms: BLOCK with `❓ NEEDS YOU` as the last line, or ask-and-continue — a `❓ ASKED: <q>` body line + `⏳ WORKING` last line, the question tracked on its ticket (`needs-answer` label + comment), then other answer-independent work. Never a buried question (moving on without asking and tracking it), never `❓ NEEDS YOU` while moving on.
- The question is self-contained Slovak (`user-questions-slovak.md`): `**Otázka — projekt …:**` head, briefing, options, ONE `❓` decision line.
- It is emitted exactly ONCE. Re-poked while STILL blocked on the SAME unanswered question, with no user input since: ONLY the bare `❓ NEEDS YOU: <same text>` line, byte-identical (the `/goal` stop condition keys on it); every other re-emission is hook-blocked. Any OTHER conversation happened since the question was last asked: ask it NANOVO A CELÁ — the full block again.
- Never rely on an `AskUserQuestion` dialog for an away user (it auto-continues after ~60 s); the `❓` marker waits UNLIMITED.
- Never reproach the user for an unanswered question („čakajú na tvoje odpovede") or stop because of it.
