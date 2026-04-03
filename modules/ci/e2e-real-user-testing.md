### E2E Tests Must Simulate Real Users (MANDATORY)

**An E2E test that calls an API with curl and checks for 200 is NOT an E2E test. It is an API smoke test. Real E2E tests open the browser and interact with the UI the way a user does.**

#### Playwright must be installed — no excuses

If Playwright is not installed, **install it immediately** (`npm init -y && npm install -D @playwright/test && npx playwright install chromium`). "Playwright isn't installed" is NOT a reason to write curl tests instead.

#### The Rule

If a feature has a UI, its E2E test MUST:

1. **Open the page in Playwright** (a real browser, not curl)
2. **Interact with UI elements** — click buttons, drag sliders, type in inputs, select options
3. **Verify the visible result** — text changed, element appeared, value updated in the UI
4. **Verify the backend effect** — the action propagated to the target system (API, database, REAPER, etc.)

A curl call that returns 200 proves the server is running. It does NOT prove the UI works, the button is clickable, the slider drags, or the value reaches the target.

#### "The test would take too long" is NOT a valid excuse

**A 15-minute CI run is ALWAYS cheaper than a week of back-and-forth with the user acting as your tester.**

**The comprehensive E2E test is not optional overhead — it is the primary deliverable.** The feature is not done until the E2E test proves it works by clicking through it like a user.

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
