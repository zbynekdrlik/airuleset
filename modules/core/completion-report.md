### Completion Report

When work is complete, provide the completion report as the **LAST thing in your message** (not the beginning — the user should not need to scroll up to find it).

#### Format

```
## ✅ Work Complete

**Plan fulfillment:**
- [x] Step 1: description — done (evidence)
- [x] Step 2: description — done (evidence)
- [x] Step 3: description — done (evidence)

✅ PR: <url> — mergeable, clean
✅ CI: green (N jobs)
✅ Deploy: verified on <target> (<what you confirmed>)
🌐 Dashboard: <url>
```

Use ❌ instead of ✅ if something failed. Use ⏳ if still in progress (but then you are NOT done — do not send the report yet).

#### Plan fulfillment checklist

Before sending the report, re-read your plan and the original user prompt. Mark each step:

- `[x]` — done, with evidence (commit hash, test file, verification output)
- `[ ]` — skipped, with explicit reason

**If any step is marked `[ ]` SKIPPED — go back and do it before sending the report.** The only acceptable skip reasons are: "user explicitly told me to skip this" or "turned out to be impossible because [specific technical reason]."

"I forgot" and "ran out of time" are not valid skip reasons — they mean you are not done.

#### Rules

- **Report goes at the END of your message**, not the beginning.
- **Never claim done without this report.**
- **Never send a partial report** with "CI is still running" — that means you are not done yet.
- **Never omit the plan fulfillment checklist** — this is how you and the user verify nothing was skipped.
- **If all steps are not `[x]`, you are not done.** Go back and complete them.
