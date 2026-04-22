### E2E Tests Must Simulate Real Users (MANDATORY)

**An E2E test that curls an API and checks for 200 is NOT an E2E test — it's an API smoke test.** Real E2E opens a browser and interacts with the UI like a user.

#### Install Playwright if missing

```bash
npm init -y && npm install -D @playwright/test && npx playwright install chromium
```

"Playwright isn't installed" is NOT a reason to write curl tests.

#### The rule

A UI feature's E2E test MUST:
1. Open the page in Playwright (real browser, not curl)
2. Interact with UI elements (click, drag, type, select)
3. Verify the visible result (text changed, element appeared, value updated)
4. Verify the backend effect (DB updated, API state propagated, target system confirmed)

A 200 response proves the server runs. It does NOT prove the button works, the slider drags, or the value reaches the target.

#### Per-feature, committed, in CI

- Each feature gets its OWN Playwright test exercising THAT feature — not a shared "dashboard loads" test.
- Must be committed to the repo and run on every push in CI.
- A 15-min CI run is cheaper than a week of you acting as tester.

Examples:
- playlist sync → test syncs, verifies songs in UI
- play/pause → test clicks play, verifies state change
- settings form → test edits, saves, reloads, verifies persistence

**"Dashboard loads" is a liveness test, not a feature test.** Every shipped feature needs its own.

#### Post-deploy verification

After CI deploys, open the live app in Playwright, click through the changed feature, verify UI + backend. Report what Playwright observed, not what curl returned.

#### When curl/API tests ARE appropriate

Pure API endpoints (health checks, WebSocket protocols) and backend integration tests. Label them as unit/integration tests, NOT E2E.

**If you're writing curl where Playwright should be — STOP. Any shortcut that passes CI but fails a real user click is not a test.**
