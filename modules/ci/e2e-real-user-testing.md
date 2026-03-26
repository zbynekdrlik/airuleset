### E2E Tests Must Simulate Real Users (MANDATORY)

**An E2E test that calls an API with curl and checks for 200 is NOT an E2E test. It is an API smoke test. Real E2E tests open the browser and interact with the UI the way a user does.**

#### Why this matters — the cost of shallow tests

When you skip comprehensive E2E testing and report "all green", the user finds the bug in seconds of clicking. Then you spend DAYS in a loop:

1. User reports bug → you "fix" it → you skip real E2E again → user finds it's still broken
2. This repeats 3-5 times over a week

A comprehensive E2E test that takes 10 minutes in CI would have caught the bug in ONE run. You would have known your fix was wrong and kept iterating AUTOMATICALLY without wasting the user's time.

**A 15-minute CI run is ALWAYS cheaper than a week of back-and-forth with the user acting as your tester.**

#### The Rule

If a feature has a UI, its E2E test MUST:

1. **Open the page in Playwright** (a real browser, not curl)
2. **Interact with UI elements** — click buttons, drag sliders, type in inputs, select options
3. **Verify the visible result** — text changed, element appeared, value updated in the UI
4. **Verify the backend effect** — the action propagated to the target system (API, database, REAPER, etc.)

A curl call that returns 200 proves the server is running. It does NOT prove the UI works, the button is clickable, the slider drags, or the value reaches the target.

#### "The test would take too long" is NOT a valid excuse

If you find yourself thinking:

- "This E2E test would make CI take 15 minutes" → **GOOD. Write it anyway.** 15 minutes of CI is nothing compared to days of user testing.
- "I'll write a quick API check instead of a full Playwright flow" → **WRONG.** That's the shortcut that causes week-long debugging loops.
- "The feature is simple, a curl test is enough" → **WRONG.** If it has a UI, test the UI. Simple features have UI bugs too.
- "I'll verify manually with Playwright after deploy instead of writing a CI test" → **WRONG.** One-time checks don't catch regressions. Write the permanent test.

**The comprehensive E2E test is not optional overhead — it is the primary deliverable.** The feature is not done until the E2E test proves it works by clicking through it like a user.

#### What a comprehensive E2E test looks like

```
// Test: EQ changes propagate to REAPER
// This test takes 2 minutes. That's fine. It catches real bugs.

1. Navigate to mixer page
2. Locate the EQ section for the target channel
3. Drag band 3 gain slider to +6dB
4. Assert: UI shows +6dB on the slider label
5. Assert: API GET /eq returns band 3 gain = +6dB
6. Assert: REAPER HTTP API shows gn=0.440000 on the target track
7. Drag band 3 gain slider back to 0dB
8. Assert: all three (UI, API, REAPER) show 0dB
9. Repeat for at least one other control (e.g., frequency, Q)
```

This is what "tested" means. Not `curl /api/eq → 200`.

#### Every UI feature MUST have a permanent Playwright CI test

- The test must be committed to the repository, not just run once manually.
- It runs on every push in CI — regressions are caught automatically.
- One-time manual Playwright verification is NOT a substitute for a permanent CI test.
- The CI test must exercise the SAME user workflow you would verify manually: navigate, click, type, assert.
- **If the user can find a bug in 5 seconds of clicking that your test doesn't catch, your test is incomplete.**

#### When curl/API tests ARE appropriate

- Pure API endpoints with no UI (health checks, data APIs, WebSocket protocols)
- Backend integration tests (database, external service calls)
- These are unit/integration tests, NOT E2E tests. Do not label them as E2E.

#### Post-deploy verification

After CI deploys, verification must ALSO use Playwright against the live system:

1. Open the deployed app URL in Playwright
2. Click through the feature you changed
3. Verify the UI and backend effect
4. Report with evidence from what Playwright observed, not from curl

**If you find yourself writing `curl` where Playwright should be — STOP. You are taking a shortcut that will pass CI but fail when the user clicks the same button.**
