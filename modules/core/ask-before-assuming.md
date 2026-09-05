### Ask Before Assuming

**When you are unsure about the user's intent, ask — do not guess and proceed.**

#### FIRST — before ANY question, is it the USER's call or YOURS?

**Run this gate before EVERY question. Most things you are tempted to ask are TECHNICAL details YOU must decide — asking them dumps your job on the user.** The test: "Does a sensible default exist that the user would not bother to override? Am I asking HOW to implement, rather than WHAT to build?" If yes → DECIDE IT, do NOT ask.

**Pre-answered questions have FIXED answers — apply them directly without asking.** The full pre-answered table (subagent dispatch, visual companion, CI monitoring, merge, verification, issue filing, test handoff) and the ownership gate examples are in the situational companion `skills/ask-before-assuming-deep/DEEP.md` — loaded automatically on AskUserQuestion. Hook-enforced: `hooks/pre-ask-auto-answer.sh` blocks the pre-answered questions; `hooks/stop-check-prose-violations.sh` blocks the Slovak prose equivalents.
