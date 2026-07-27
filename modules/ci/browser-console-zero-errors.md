### Browser Console: Zero Errors, Zero Warnings → auto-loads full detail on Playwright test files (`rules/browser-console-zero-errors.md`)

Every web app must have a clean browser console — `console.error`/`console.warn` are always bugs. Every Playwright test MUST collect console output and assert it's empty as its last assertion. A deployed app with console errors is a failed deployment, even if the UI looks correct.
