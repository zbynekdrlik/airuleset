### Completion Report

IMPORTANT: When work is complete, YOU MUST provide the completion report in EXACTLY this format as the **LAST thing in your message** (not the beginning — the user should not need to scroll up to find it).

#### Format (MANDATORY — use this EXACT structure, do not substitute your own)

```
## ✅ Work Complete

**Plan fulfillment:**
- [x] Step 1: description — done (evidence)
- [x] Step 2: description — done (evidence)
- [x] Step 3: description — done (evidence)

**E2E test coverage:**
| Feature/Fix | E2E Test File | What It Verifies |
|-------------|---------------|------------------|
| EQ control  | e2e/test_eq.ts | Drag slider → UI updates → REAPER confirms value |
| Solo button | e2e/test_solo.ts | Click solo → channel isolated → REAPER confirms |

✅ PR: <url> — mergeable, clean
✅ CI: green (N jobs)
✅ Deploy: verified on <target> (<what you confirmed>)
🌐 Dashboard: <url>
```

The **E2E test coverage table** is mandatory for any work that touches features or fixes. Each row must name the specific test file and describe what user workflow it exercises. If a feature has no E2E test row, it is not done.

Use ❌ instead of ✅ if something failed. Use ⏳ if still in progress (but then you are NOT done — do not send the report yet).

**Do NOT use a different format.** Do NOT skip the plan fulfillment checklist. Do NOT write a prose summary instead. Use the exact template above with emoji status indicators.

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
- **NEVER include a "Remaining" / "Future" / "TODO" / "Follow-up" section.** If something is remaining, you are not done. Go back and do it. A completion report with a "Remaining" section is a contradiction — it is an incomplete-work report, not a completion report.
