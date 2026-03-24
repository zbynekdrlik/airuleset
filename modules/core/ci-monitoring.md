### CI Pipeline Monitoring

**After every push, you MUST monitor CI until ALL jobs are GREEN.** Do not move on to other tasks or claim work is done while CI is running.

1. Check status: `gh run list --limit 3`
2. Watch the run: `gh run view <run-id>` (poll until terminal state)
3. If any job fails: `gh run view <run-id> --log-failed` — investigate and fix immediately
4. Push fixes and monitor again until green
5. After merge to main: monitor the main branch CI run until it completes successfully

**Never stop at partial CI green.** ALL jobs must pass — including deploy jobs — before reporting success. A merge is not done until the full pipeline is green.

**Never dismiss CI failures** as "flaky", "pre-existing", or "known issue". Every failure must be investigated and fixed.
