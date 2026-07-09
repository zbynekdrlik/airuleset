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

All mean "user, you are my tester now" and are FORBIDDEN — shifting verification from YOUR tools (Playwright / curl / SSH / MCP) to the user's eyes/clicks. Representative: "Can you test it on your end?", "Please verify it works", "Let me know if it works / breaks", "Tell me what you see", "Ping me when you've checked", "Report back when…", "Next user test", "Using you as tester", "I'll fix locally before next user test". This family is HARD-blocked at Stop by `stop-check-prose-violations.sh` (locked by the `TestTesterHandoffHook` tests) — with one escape: an explicit `UNVERIFIED:` line stating what you cannot test and why.

The hook is a backstop, not the whole rule — these semantic variants it does NOT reliably catch are equally banned and must be self-policed: "Try it and tell me what happens", "Run it and confirm", "Test it in your browser / on your machine", a bare "On your end" / "in your environment" (when about testing). The intent — not the exact wording — is banned; applies to all rewordings and semantic equivalents.

#### Before giving up — ASK FOR THE TOOL, not the test

The handoff banned above is "user, run this and tell me if it works". The **opposite** of that is also mandatory: **when you genuinely lack a tool, ask the user to give you that tool — do NOT silently give up and write `UNVERIFIED:`**.

Most blockers have a tool-shaped fix. The user has access to install / configure / share what you need. Ask for the SPECIFIC missing capability — never for a test.

**Tool requests you SHOULD make (correct hand-off direction):**

- "I don't have Playwright MCP installed in this session. Install `plugin:playwright` so I can drive the browser myself." → user installs → you test
- "MCP server `win-resolume` is unreachable. Restart it on the Windows host (or share the new host/port)." → user restarts → you test
- "I can't authenticate to claude.ai — the OAuth flow needs a real browser session against your account. Install Chrome DevTools MCP / Playwright with persistent profile, OR paste a session cookie / bearer token from your active session." → user provides credential → you test
- "I need read access to the production Postgres instance to verify the migration landed. Share connection string in 1Password / set `PROD_DB_URL` env / open SSH tunnel." → user provides → you test
- "I need a screenshot of the iOS Safari rendering — I only have Chromium. Install BrowserStack MCP, or share access to Sauce Labs / LambdaTest." → user provides → you test
- "I can't reach the staging Discord webhook. Either share the webhook URL or grant me access to the channel via the discord plugin." → user provides → you test
- "I need to run a desktop-session UI action on the Windows machine — schtasks /it requires an interactive session. Set up the win-mcp server (see winremote-setup) so I can drive the desktop directly." → user sets up → you test
- "I can't read the binary log from the hardware device — need vendor's USB driver. Install `<package>` on dev2, or share serial output via socat / picocom." → user provides → you test

**Correct request shape:**

> I need `<specific capability>` to verify `<specific flow>` myself. Options to give me that: (a) `<concrete option 1>`, (b) `<concrete option 2>`. Until then I cannot test `<flow>` end-to-end.

Notice what's NOT in that template: "could you test it instead?" The user provides the TOOL; you do the TEST.

**Wrong shape (still banned, even when blocker is real):**

- "I can't reach claude.ai. Could you test the flow and let me know?" — **WRONG.** Ask for the AUTH/SESSION/MCP, not the test.
- "MCP win-resolume is down. Want to verify it manually?" — **WRONG.** Ask for MCP restart, not manual verification.
- "Playwright isn't installed. Could you click through it?" — **WRONG.** Ask for Playwright install, not user clicks.
- "I don't have prod DB access. Please run this query and paste the result." — **WRONG.** Ask for prod DB credential / tunnel, not query results.

**The decision tree:**

```
Hit a blocker?
├── Can you debug it yourself with existing tools? → YES → debug it (do NOT mention to user)
├── Do you lack a specific tool/access/credential?
│   ├── YES → Ask for the TOOL/ACCESS/CREDENTIAL with concrete options
│   └── User provides → YOU test
└── Is it genuinely user-only (their personal account, their hardware in their hands)?
    └── State UNVERIFIED with specific reason. NEVER as default — only after exhausting tool-request path.
```

#### The single LAST-RESORT exception — true user-only access

After you've asked for the tool and the user confirms it's impossible to give you (their personal claude.ai account, their physical hardware, their org-restricted credential), state EXACTLY what you cannot verify and why:

```
UNVERIFIED: Cannot simulate the claude.ai OAuth flow — requires the user's authenticated browser session
against their actual claude.ai account. I have verified the MCP server returns valid tokens locally
(see test_oauth.py). Tool-request asked + rejected (user confirmed personal-account-only).
Final end-to-end check needs user.
```

This is acceptable AFTER tool-request was attempted. "Can you test it on your end?" — never. Skipping tool-request and going straight to `UNVERIFIED:` — wrong, ask first.
