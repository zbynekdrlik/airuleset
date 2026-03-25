### E2E Tests Must Simulate Real Users (MANDATORY)

**An E2E test that calls an API with curl and checks for 200 is NOT an E2E test. It is an API smoke test. Real E2E tests open the browser and interact with the UI the way a user does.**

#### The Rule

If a feature has a UI, its E2E test MUST:

1. **Open the page in Playwright** (a real browser, not curl)
2. **Interact with UI elements** — click buttons, drag sliders, type in inputs, select options
3. **Verify the visible result** — text changed, element appeared, value updated in the UI
4. **Verify the backend effect** — the action propagated to the target system (API, database, REAPER, etc.)

A curl call that returns 200 proves the server is running. It does NOT prove the UI works, the button is clickable, the slider drags, or the value reaches the target.

#### What counts as a real E2E test

```
GOOD: Open mixer page → drag EQ band 3 gain slider to +6dB → verify UI shows +6dB →
      verify REAPER track shows gn=+6dB → drag back to 0dB → verify reset

BAD:  POST /api/eq {band: 3, gain: 6} → assert 200 → call it "E2E tested"
```

```
GOOD: Open settings page → toggle "enable streaming" → verify toggle is ON →
      verify stream process started on target machine

BAD:  GET /api/settings → assert JSON contains streaming=true → call it "verified"
```

#### Every UI feature MUST have a permanent Playwright CI test

- The test must be committed to the repository, not just run once manually.
- It runs on every push in CI — regressions are caught automatically.
- One-time manual Playwright verification is NOT a substitute for a permanent CI test.
- The CI test must exercise the SAME user workflow you would verify manually: navigate, click, type, assert.

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
