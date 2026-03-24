### CI Pipeline Monitoring

**After every push, you MUST monitor CI until ALL jobs reach a terminal state.** Do not move on to other tasks or claim work is done while CI is running.

1. Check status: `gh run list --limit 3`
2. Watch the run: `gh run view <run-id>` (poll until terminal state — success or failure)
3. If any job fails: `gh run view <run-id> --log-failed` — investigate and fix immediately
4. Push fixes and monitor again until green
5. After merge to main: monitor the main branch CI run AND any release/deploy workflows until they complete

**ALL jobs must pass — not just lint and test.** Deploy jobs, e2e jobs, release jobs — everything in the pipeline must be green. If a deploy job is "skipped" or still running, you are NOT done. If a job shows as green but others are still pending, you are NOT done. Wait for the entire workflow run to reach a terminal state.

**Never stop at partial green.** Celebrating "lint and tests pass!" while the deploy job is failing or pending is a critical error.

**Never dismiss CI failures** as "flaky", "pre-existing", or "known issue". Every failure must be investigated and fixed.
