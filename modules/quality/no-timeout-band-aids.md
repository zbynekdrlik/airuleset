### Never Increase Timeouts Without Investigation

**When something times out that previously worked, the timeout is not the problem. Something changed. Investigate.**

#### The anti-pattern

1. Deploy worked yesterday with a 30s timeout
2. Today it times out at 30s
3. Claude increases timeout to 60s → pushes → hopes it works
4. Still times out → increases to 120s → pushes again
5. Days of wasted CI runs, when the real issue was a regression in startup logic

#### The correct approach

When you see a timeout failure:

1. **STOP.** Do not touch the timeout value.
2. **Ask: what changed?** Compare your recent commits against the last known working state. `git diff` the relevant code.
3. **Investigate the root cause:** Why is the operation slower? Is it a new database migration? A missing index? A startup dependency that now blocks? A regression in your code?
4. **Fix the root cause.** The timeout should not need to change if you fix what actually broke.
5. **Only increase a timeout if** you have confirmed the operation legitimately takes longer now (e.g., the database grew, a new required step was added) AND you have documented why.

#### This applies to ALL "make it work by loosening constraints" patterns

- Timeout too short → **investigate why it's slow**, don't increase timeout
- Test flaky → **investigate why it flakes**, don't add retries
- API returns error → **investigate the error**, don't add try/catch and swallow it
- CI step fails intermittently → **investigate the intermittency**, don't add `continue-on-error`
- Connection refused → **investigate why the service isn't ready**, don't add sleep+retry

**Loosening a constraint to make a failure disappear is not a fix. It is hiding the problem.**
