### Autonomous Verification Directive

**You are responsible for verifying that your work ACTUALLY WORKS. Never ask the user to test, verify, take screenshots, or act as a tester.**

#### YOU have eyes — use them

You have Playwright, browser MCP tools, and SSH access. **NEVER ask the user what they see on a dashboard, UI, or page.** Open it yourself and look. The user is not your eyes.

Anti-patterns that violate this directive:

- "What exactly are you seeing on the dashboard?" → **WRONG.** Open the dashboard in Playwright and look.
- "Is the buffer duration dropping to zero?" → **WRONG.** Check it yourself.
- "Can you describe what happens when you click...?" → **WRONG.** Click it yourself with Playwright.
- "Can you send me a screenshot?" → **WRONG.** Take the screenshot yourself.

**If a dashboard URL exists, open it. If a UI exists, interact with it. If data is visible somewhere, read it. You have the tools. Use them.**

#### Liveness checks are NOT verification

Checking that a process is running, an API returns 200, or a page loads is NOT verification. These are liveness checks. They prove the app didn't crash — they do NOT prove your changes work.

**REAL verification means testing the actual user workflow end-to-end:**

- If you changed an EQ control → change a value via the UI/API, then read the value from the target system (e.g., REAPER) and confirm it changed.
- If you added a button → click it and verify the expected side effect happened.
- If you fixed a data flow → send data in, verify it arrives at the destination with correct values.
- If you modified a form → submit it and check the backend received the correct data.

#### Verification protocol

After CI deploys to a target machine:

1. **Liveness check** (necessary but not sufficient): process running, health endpoint responds, UI loads.
2. **Functional verification** (the actual test): exercise the SPECIFIC feature you changed. Write a value, read it back from the target system. Click a button, verify the effect. Change a setting, confirm it propagated.
3. **Visual verification**: open the dashboard/UI in Playwright, take a screenshot or read DOM values. Confirm the UI shows what it should.
4. **Report with evidence**: `VERIFIED: Changed EQ band 1 to +6dB via API, confirmed REAPER shows +6dB on track 3, dashboard shows +6dB on band 3 slider` — not just `VERIFIED: app is running`.
5. **Never use speculative language** — no "should work", "will probably", "might be". Only report what you observed with real values.

#### What "done and working" means

- A compiling program is not a working program.
- CI green is not deploy verified.
- App running is not feature working.
- Page loading is not functionality verified.
- Asking the user "what do you see?" means you didn't verify anything.
- **You must confirm the CHANGED FUNCTIONALITY works with real data, not just that the app is alive.**

If you cannot verify the actual functionality (e.g., no API to read back the value), state explicitly what you COULD NOT verify: `UNVERIFIED: Could not confirm EQ changes propagate to REAPER — no read-back API available. User must test manually.` This is infinitely better than falsely claiming "done, working, tested."
