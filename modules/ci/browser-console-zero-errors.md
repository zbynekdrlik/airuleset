### Browser Console: Zero Errors, Zero Warnings

**Every web app must have a clean browser console. Console errors and warnings are bugs — they must be caught in CI and during verification, not discovered by the user.**

#### Playwright E2E Tests (CI enforcement)

Every Playwright test file MUST collect and assert on console output. This is not optional — tests that don't check console are incomplete.

```typescript
test('feature works with clean console', async ({ page }) => {
  const consoleMessages: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      consoleMessages.push(`[${msg.type()}] ${msg.text()}`);
    }
  });

  // ... test logic ...

  // MUST be the last assertion in every test
  expect(consoleMessages).toEqual([]);
});
```

#### What counts as a failure

- **console.error** — always a bug. WASM panics, uncaught exceptions, failed assertions.
- **console.warn** — always a bug. Leptos warnings, deprecation notices, incorrect usage.

#### Post-Deploy Verification

When verifying a deployed app via Playwright, ALWAYS open the browser console and check for errors. A deployed app with console errors is a failed deployment, even if the UI appears to work.

#### The rule

**If the browser console has errors or warnings, the feature is broken — regardless of whether the UI looks correct.** A clean console is a hard requirement, not a nice-to-have.
