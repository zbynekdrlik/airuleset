### Completion Report

When work is complete, always provide a structured status block:

```
PR: <url> | CI: green | Deploy: verified | Dashboard: <url>

Plan fulfillment:
- [x] Step 1: description — done (commit abc123)
- [x] Step 2: description — done (test in e2e/test_feature.ts)
- [x] Step 3: description — done
- [ ] Step 4: description — SKIPPED: reason
```

#### Required fields

- **PR** — the mergeable PR URL (must be green and clean)
- **CI** — status of all CI jobs (must all be green)
- **Deploy** — whether the deployed app was verified with Playwright on the target machine
- **Dashboard** — the URL where the user can see the deployed result (if applicable)
- **Plan fulfillment** — checklist of every planned step with done/skipped status

#### Plan fulfillment checklist

Before sending the report, re-read your plan and the original user prompt. Mark each step:

- `[x]` — done, with evidence (commit hash, test file, verification output)
- `[ ]` — skipped, with explicit reason

**If any step is marked `[ ]` SKIPPED — go back and do it before sending the report.** The only acceptable skip reasons are: "user explicitly told me to skip this" or "turned out to be impossible because [specific technical reason]."

"I forgot" and "ran out of time" are not valid skip reasons — they mean you are not done.

#### Rules

- **Never claim "done" without this report.**
- **Never send a partial report** with "CI is still running" — that means you are not done yet.
- **Never omit the plan fulfillment checklist** — this is how you and the user verify nothing was skipped.
- **If all steps are not `[x]`, you are not done.** Go back and complete them.
