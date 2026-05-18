### Autonomous Verification Directive

**You are responsible for verifying that your work ACTUALLY WORKS. Never ask the user to test, verify, take screenshots, or act as a tester.**

#### YOU have eyes — use them

You have Playwright, browser MCP tools, and SSH access. **NEVER ask the user what they see on a dashboard, UI, or page.** Open it yourself and look. The user is not your eyes.

Anti-patterns that violate this directive:

- "What exactly are you seeing on the dashboard?" → **WRONG.** Open the dashboard in Playwright and look.
- "Is the buffer duration dropping to zero?" → **WRONG.** Check it yourself.
- "Can you describe what happens when you click...?" → **WRONG.** Click it yourself with Playwright.
- "Can you send me a screenshot?" → **WRONG.** Take the screenshot yourself.
- "Should I open the dashboard in Playwright?" → **WRONG.** Don't ask permission to use your own tools. Just use them.
- "Let me know if you want me to verify with Playwright" → **WRONG.** Verification is not optional and not a proposal.

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

#### Hitting a blocker is NOT a hand-off trigger

When verification fails because of a tool error, auth failure, sandbox limit, missing credential, or unexpected server response — **THAT IS YOUR WORK**, not the user's. The agent's most frequent collapse pattern: hit a blocker → reach for the user as the path of least resistance → "can you test this on your end?"

**Wrong reactions to a blocker (all banned):**

- MCP returns `Authorization failed` → "please verify your token on the dashboard" → **WRONG.** Read the error. Re-fetch token. Check scopes. Run the same auth flow yourself via curl. Inspect server logs. The user doesn't have visibility into the failure; you do.
- Playwright times out on a selector → "could you click through the flow and tell me what you see?" → **WRONG.** Take a screenshot, dump the DOM snapshot, scroll, wait longer, try alternative selectors. The page state is in your hands.
- API returns 500 → "can you confirm it works in the prod UI?" → **WRONG.** Read the response body. Check the server logs (SSH if needed). Reproduce the exact request with curl. Find the root cause.
- Sandbox blocks an action (e.g. needs explicit approval) → "please run it locally and let me know" → **WRONG.** Ask user for the SPECIFIC approval ("approve this `gh auth login` once"), not for a test handoff.
- claude.ai / external service returns an opaque reference ID → "please report this to support, then test again" → **WRONG.** Simulate the flow end-to-end yourself with your test harness BEFORE asking the user to retry on the live target.

**Correct reaction protocol:**

1. **Read the actual error.** Full body, full stack trace, full reference ID. Do not paraphrase to the user — read it FIRST.
2. **Search for root cause.** Recent commits, recent config changes, server logs, the third-party service's status page. Often the cause is 1 file away.
3. **Build a local reproduction.** curl, a unit test, a Playwright script — whatever isolates the failure away from the live target.
4. **Fix locally, verify locally.** Then verify on the live target with the same flow.
5. **Only then escalate** — and only if the blocker requires user-only access (their personal token, their org admin permission, their browser session). Even then: ask for the SPECIFIC access, not a test handoff.

#### Banned hand-off phrases (intent — all rewordings apply)

These phrases all mean "user, you are my tester now" and are FORBIDDEN. The Stop hook blocks them:

- "Can you test it (on your end | in your browser | in production)?"
- "Please verify it works"
- "Let me know if it works / breaks / shows X"
- "Ping me when you've checked"
- "Tell me what you see"
- "Report back when X"
- "Could you click through the flow?"
- "Try it and tell me what happens"
- "Run it and confirm"
- "Test it in your browser / on your machine"
- "Next user test" (admission you're queueing them up)
- "Using you as tester" / "stop using you as tester" (you're already mid-violation)
- "I'll fix locally before next user test" (correct fix, wrong framing — there should be no "next user test" planned)
- "On your end" / "in your environment" (when about testing, not about user-only access)

The intent is banned: shifting verification from your tools to the user's eyes/clicks.

#### The single exception — true user-only access

If a flow legitimately requires the user's personal account, browser session, hardware, or admin permission YOU CANNOT obtain, state EXACTLY what you cannot verify and why:

```
UNVERIFIED: Cannot simulate the claude.ai OAuth flow — requires the user's authenticated browser session
against their actual claude.ai account. I have verified the MCP server returns valid tokens locally
(see test_oauth.py). Final end-to-end check needs user.
```

This is acceptable. "Can you test it on your end?" is not.
