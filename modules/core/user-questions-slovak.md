### Questions To The User — Slovak, Human, No Jargon

**Context gate — related rules you MUST also apply:**
- `ask-before-assuming.md` — WHEN to ask (this rule is HOW to phrase it)
- `message-status-marker.md` — the `❓ NEEDS YOU` line is already Slovak; this extends the SAME to the whole `AskUserQuestion` dialog
- `issue-reference-context.md` — a `#N` always carries its topic; here you ALSO explain it in plain words
- `skills/user-questions-slovak` — the full hook-enforced template, both re-ask branches, and every worked example; auto-loads the moment you call `AskUserQuestion`, and load it explicitly for a prose `❓` question too, since that path never triggers the auto-load

**Every question to the user MUST be in SLOVAK, plain, human language.** Question + every option label + every option description. The ONLY English kept is the status keyword `NEEDS YOU` / `DONE` (the hooks key on it).

**Self-contained — write EVERY question for someone with ZERO context.** Opens with a 2-4 sentence plain-Slovak briefing: which project + what it is, what was happening, explain EVERY cross-reference, THEN the decision + options with `(odporúčam)`.

**`❓` block shape (hook-enforced `stop-check-question-quality.sh`):** opens with `**Otázka — projekt …:**`, carries bullet/numbered options, ends with exactly ONE `❓` decision line.

**Re-asking** (a bare verbatim re-poke vs NANOVO A CELÁ after any conversation) and owner-scoped delivery: `message-status-marker.md`.

**"U N?" STATUS query → step-by-step flow** (hook-backstopped `stop-check-prose-violations.sh`), never a raw `--waiting` table (that is MACHINE context) or summary list (#606).

History + rationale: `.claude/rules-reference/user-questions-slovak-history.md` (#859).
