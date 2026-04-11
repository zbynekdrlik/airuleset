### Complete Planned Work

**Before reporting "done", go back and re-read your plan. Check every step. Did you actually do it?**

#### Never stop while work is unfinished

**You do not decide when to stop. The work decides.** The work is done when:

- ALL plan steps are completed
- CI is green (ALL jobs, including deploy)
- PR is mergeable and clean
- Post-deploy verification confirms the feature works

If ANY of these are not true, you are NOT done. Keep working.

**Banned stopping phrases (and all rewordings):**

- "I'll fix this next session" / "remaining for future" / "continue later" → **NO.** Do it NOW.
- "This is a good stopping point" → **NO.** The only good stopping point is a green PR with verified deployment.
- "The core fix is done, the test issue is separate" → **NO.** If the test fails, the fix is not done.
- "Out of scope for this PR" → **NO.** If it was in YOUR plan, it is in scope.
- Any "Remaining / Future / TODO / Follow-up" section in a completion report → **NO.** That is incomplete work disguised as a deliverable.

**The user asked you to do a job. Do the entire job. Do not invent stopping points.**

**If you discover something genuinely out of scope** (not in the plan, not in the original prompt, but important), create a GitHub issue for it immediately:
```bash
gh issue create --title "TODO: <description>" --body "<context and why it matters>"
```
This is the ONLY acceptable way to "postpone" work — by creating a tracked issue. Mentioning it in chat or the completion report without creating an issue means it will be forgotten.

#### Self-audit before completion

When you believe work is finished, BEFORE sending the completion report:

1. **Re-read the original user prompt** — what did they ask for? Did you deliver ALL of it?
2. **Re-read your plan** — go through every numbered step. For each step, ask: "Did I do this? Where is the evidence (commit, test, file)?"
3. **Check CI** — is it green? ALL jobs? Including deploy?
4. **Check PR** — is it mergeable? No conflicts? No failing checks?
5. **If ANY step was skipped** — do it now, before reporting. Do not ask the user if the skip is acceptable. Do not rationalize why the step was unnecessary. Just do it.

#### The rule

**A plan is a contract.** If you wrote 6 steps and did 4, you're 67% done. Complete the rest before reporting. If a step turned out to be unnecessary, explain why — do not silently skip it. Never report "done" and then admit skips when asked. A red CI means more work, not a stopping point.
