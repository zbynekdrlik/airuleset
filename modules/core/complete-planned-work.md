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

**Intent, not wording.** The examples above are representative. ANY phrase that shifts incomplete work to a later time, a different session, or a new issue — under any wording — is banned. If you're about to say something that means "I'll do this later," STOP and do it now.

**If you discover something genuinely out of scope** (not in the plan, not in the original prompt, but important), create a GitHub issue for it immediately:
```bash
gh issue create --title "TODO: <description>" --body "<context and why it matters>"
```
This is the ONLY acceptable way to "postpone" work — by creating a tracked issue. Mentioning it in chat or the completion report without creating an issue means it will be forgotten.

#### Follow-up gate — "genuinely out of scope" has STRICT criteria

Before filing a follow-up issue, apply the bundling gate (per `autonomous-batch-issue-development.md`). A discovered task qualifies for follow-up ONLY when ALL of these are true:

- Estimated change > 300 LoC, OR
- Requires DB schema change / migration, OR
- Public API breaking change, OR
- Security boundary modification, OR
- Cross-cutting refactor (rename across >5 files, dep major bump, framework upgrade), OR
- Genuine design dependency on a separate decision the user must make

If NONE of these apply → the cleanup is small and MUST land in the CURRENT PR, not a follow-up issue. File it, fix it, ship it in the same PR.

**Things that DO NOT qualify as "genuinely out of scope" (must be done in current PR):**

- "Migrate string to enum" / "tighten type" / "use typed constant instead of literal" — same file, <50 LoC = DO NOW
- "Extract magic number to constant" — 5-line change = DO NOW
- "Rename variable for clarity" — single file = DO NOW
- "Add missing test for the path I just touched" — required anyway per `regression-test-first.md` = DO NOW
- "Replace inline literal with imported type" — trivial = DO NOW
- "Tidy up the duplicated condition I noticed" — single function = DO NOW
- "Add docstring/comment where I changed code" — trivial = DO NOW
- Any change <100 LoC that touches files already in the current diff — DO NOW

**Banned justifications (intent — all rewordings apply):**

- "Out of scope for this PR" applied to a <100 LoC same-file cleanup — **WRONG**
- "Keeping this PR focused" used to dump trivial polish into a follow-up — **WRONG**
- "Easier to review separately" for a 10-line type tightening — **WRONG**
- "File a follow-up for the enum migration" when the enum touches 1-2 files — **WRONG**
- "I'll do it in the next PR" — **WRONG.** Add a commit to THIS PR.

The follow-up gate exists for REAL out-of-scope work (schema migrations, framework upgrades, multi-day refactors), NOT for small cleanups the agent noticed while touching the file. Filing a follow-up issue for a 20-line refactor wastes a PR cycle, a CI cycle, and a review round.

**Banned phrases (intent, all rewordings):**

- "Follow-up filed: #N — <small cleanup>"
- "Filed as #N for next PR"
- "Tracked in #N as separate work"
- "Will address in dedicated PR"
- Any phrasing that defers a sub-300-LoC discovered cleanup to a new issue

The intent is banned: punting small same-PR work to a follow-up to send a completion report sooner.

#### Self-audit before completion

When you believe work is finished, BEFORE sending the completion report:

1. **Re-read the original user prompt** — what did they ask for? Did you deliver ALL of it?
2. **Re-read your plan** — go through every numbered step. For each step, ask: "Did I do this? Where is the evidence (commit, test, file)?"
3. **Check CI** — is it green? ALL jobs? Including deploy?
4. **Check PR** — is it mergeable? No conflicts? No failing checks?
5. **If ANY step was skipped** — do it now, before reporting. Do not ask the user if the skip is acceptable. Do not rationalize why the step was unnecessary. Just do it.

#### The rule

**A plan is a contract.** If you wrote 6 steps and did 4, you're 67% done. Complete the rest before reporting. If a step turned out to be unnecessary, explain why — do not silently skip it. Never report "done" and then admit skips when asked. A red CI means more work, not a stopping point.
