### Complete Planned Work

**Before reporting "done", go back and re-read your plan. Check every step. Did you actually do it?**

#### Self-audit before completion

When you believe work is finished, BEFORE sending the completion report:

1. **Re-read the original user prompt** — what did they ask for? Did you deliver ALL of it?
2. **Re-read your plan** — go through every numbered step. For each step, ask: "Did I do this? Where is the evidence (commit, test, file)?"
3. **Check for skipped steps** — TDD plans have test-writing steps BEFORE implementation steps. Did you write the tests, or did you skip straight to code?
4. **If ANY step was skipped** — do it now, before reporting. Do not ask the user if the skip is acceptable. Do not rationalize why the step was unnecessary. Just do it.

#### Common skipped steps (be honest with yourself)

- "Write failing tests first" → skipped, went straight to implementation
- "Write E2E Playwright test" → wrote a curl smoke test instead, or skipped entirely
- "Verify on deployed system" → checked API health, didn't click through the UI
- "Update documentation" → forgot
- "Run ALL existing tests" → only ran the new tests

#### The rule

**A plan is a contract. If you wrote a plan with 6 steps and only did 4, you are not done — you are 67% done.** Complete the remaining steps before reporting. If a step turns out to be unnecessary, explain why in the completion report — do not silently skip it.

**Never report "done" and then admit steps were skipped only when the user asks.** That means you knew you weren't done and reported falsely.
