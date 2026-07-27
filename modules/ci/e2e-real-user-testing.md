### E2E Tests Must Simulate Real Users → auto-loads full detail on Playwright test files (`rules/e2e-real-user-testing.md`)

An E2E test that curls an API and checks for 200 is NOT an E2E test — it's an API smoke test. A UI feature's E2E test MUST open the page in a real browser, interact with the UI (click/drag/type), and verify both the visible result and the backend effect. Every shipped UI feature gets its own Playwright test.
