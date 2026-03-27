### Complete Planned Work

**Before reporting "done", go back and re-read your plan. Check every step. Did you actually do it?**

#### Never stop while work is unfinished

**You do not decide when to stop. The work decides.** The work is done when:

- ALL plan steps are completed
- CI is green (ALL jobs, including deploy)
- PR is mergeable and clean
- Post-deploy verification confirms the feature works

If ANY of these are not true, you are NOT done. Keep working.

**Banned stopping phrases (including all rewordings):**

- "I'll fix this in the next session" → **NO.** Fix it NOW.
- "Remaining for future iteration" → **NO.** This is the same as "next session" with different words. Do it NOW.
- "The remaining issue is..." → **NO.** If there's a remaining issue, it's YOUR issue. Fix it.
- "This is a good stopping point" → **NO.** The only good stopping point is a green PR with verified deployment.
- "I'll continue this later" → **NO.** Continue it now.
- "The core fix is done, the test issue is separate" → **NO.** If the test fails, the fix is not done.
- "Planned but requires..." → **NO.** If it was in the plan, it requires doing. Do it.
- "Out of scope for this PR" → **NO.** If it was in YOUR plan, it is in scope.
- Any section labeled "Remaining", "Future", "TODO", "Follow-up" in a completion report → **NO.** These are admissions of incomplete work disguised as deliverables.

**The user asked you to do a job. Do the entire job. Do not invent stopping points.**

#### Self-audit before completion

When you believe work is finished, BEFORE sending the completion report:

1. **Re-read the original user prompt** — what did they ask for? Did you deliver ALL of it?
2. **Re-read your plan** — go through every numbered step. For each step, ask: "Did I do this? Where is the evidence (commit, test, file)?"
3. **Check CI** — is it green? ALL jobs? Including deploy?
4. **Check PR** — is it mergeable? No conflicts? No failing checks?
5. **If ANY step was skipped** — do it now, before reporting. Do not ask the user if the skip is acceptable. Do not rationalize why the step was unnecessary. Just do it.

#### Common skipped steps (be honest with yourself)

- "Write failing tests first" → skipped, went straight to implementation
- "Write E2E Playwright test" → wrote a curl smoke test instead, or skipped entirely
- "Verify on deployed system" → checked API health, didn't click through the UI
- "Fix the failing CI job" → declared it a "known issue" and stopped
- "Run ALL existing tests" → only ran the new tests

#### The rule

**A plan is a contract. If you wrote a plan with 6 steps and only did 4, you are not done — you are 67% done.** Complete the remaining steps before reporting. If a step turns out to be unnecessary, explain why in the completion report — do not silently skip it.

**Never report "done" and then admit steps were skipped only when the user asks.** That means you knew you weren't done and reported falsely.

**Never invent a reason to stop when CI is red or a test is failing.** A red CI means you have more work to do, not that you've reached a stopping point.
