### Questions To The User — Slovak, Human, No Jargon

**Context gate — related rules you MUST also apply:**
- `ask-before-assuming.md` — WHEN to ask (this rule is HOW to phrase it)
- `message-status-marker.md` — the `❓ NEEDS YOU` line is already Slovak; this extends it to the whole `AskUserQuestion` dialog
- `issue-reference-context.md` — a `#N` carries its topic; here ALSO in plain words
- `skills/user-questions-slovak` — template + worked examples; auto-loads on `AskUserQuestion`, load it yourself for a prose `❓` question

**Every message to the owner is SLOVAK** — all prose and status lines, not only questions (#1379, `stop-check-owner-language.sh`). English only in code, commands, paths, exact error strings, the marker keywords (`NEEDS YOU`, `ASKED`, `WORKING`, `DONE`, `Work Complete`) and text persisted for other readers (tickets, commits, peer messages). Questions: plain, human, every option too.

**Self-contained — write EVERY question for someone with ZERO context.** Opens with a 2-4 sentence plain-Slovak briefing: which project + what it is, what was happening, explain EVERY cross-reference, THEN the decision + options with `(odporúčam)`.

**`❓` block shape (hook-enforced `stop-check-question-quality.sh`):** opens with `**Otázka — projekt …:**`, carries bullet/numbered options, ends with exactly ONE `❓` decision line.

**Re-asking** (verbatim re-poke vs NANOVO A CELÁ) and owner-scoped delivery: `message-status-marker.md`.

**"U N?" STATUS query → step-by-step flow** (hook-backstopped `stop-check-prose-violations.sh`), never a raw `--waiting` table (that is MACHINE context) or summary list (#606).

History + rationale: `.claude/rules-reference/user-questions-slovak-history.md` (#859).
