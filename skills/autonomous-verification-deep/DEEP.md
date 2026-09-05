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

**CONTENT read-back is MANDATORY and hook-enforced:** every `## ✅ Work Complete` report carries `✅ Výstup: <concrete observed values read back from the real artifact>` (or an explicit `n/a — <prečo>`); `stop-check-prose-violations.sh` blocks a report without it, with a value-free line, or with an `n/a` contradicting the report's own 🌐/📱 surface. See `completion-report.md` (Hard rules). History + incident (#446): `.claude/rules-reference/autonomous-verification-history.md` (#859).

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

#### "What's on PROD?" is a SELF-SERVICE question — never a first-choice UNVERIFIED / hand-off

"I can't see what's on PROD" is a BLOCKER with a specific correct reaction (the section above), NOT a genuine user-only wall — and it is the one blocker a stream / gatekeeper session most often surrenders to wrongly. For a prod-STATE READ (is user X in group Y, a row count, a config value, the content of a sent mail) there is a self-service answer wherever the project provides a prod-read path (below), so reaching for `UNVERIFIED: can't verify prod` or a "share prod access with me" hand-off as your FIRST move — without first trying that path — is the exact banned instinct this module exists to kill (#500 — a montalu2 stream twice wrote "membership on PROD I cannot verify", once after a single HTTP 500 whose body it never read, never once considering the fresh prod copy that exists for precisely this).

**Decision tree for a prod-STATE READ:**

1. **The stream's OWN direct read-only channel, where the project provides one** (e.g. odoo-erp montalu*: the read-only handover API account on the prod instance — `has_group` / `search_read`; a Money RO tunnel for Money). On ANY HTTP/API error, READ THE ERROR BODY and try a NARROWER method — never surrender after one 500 (a 500 on a broad `users` read is a per-field permission ceiling, not "prod is unreadable"; a `has_group` check on the ONE user still answers the question).
2. **A FRESH COPY of prod on your own box** — the universal fallback for ANY data / group / config / mail-content question, where the project provides the mechanism (odoo-erp: `REFRESH-DEV-BOX-FROM-PROD: <stream>` on the repo's tracking ticket → a fresh rsync/pg_dump of the CURRENT prod with full psql / odoo-shell access in ~20–40 min). It is a snapshot (minutes old): AUTHORITATIVE for STATE questions; channel 1 stays for second-live things.
3. **`UNVERIFIED` / a hand-off for a prod read is the LAST choice, never the first** — legitimate only after 1 AND 2 provably fail, which for a read is practically never.

This OVERRIDES the generic "ask for prod DB access / an SSH tunnel" tool-request below FOR any project that gives you a self-service prod-read path — you already HAVE the tool, so asking the user or gatekeeper for prod access is the hand-off this whole module bans, wearing a "prod" costume. (Where a project genuinely provides NO prod-read path at all, that tool-request stays correct.)

**A prod-STATE READ is NOT a genuinely-un-exercisable pre-prod CODE PATH.** A code PATH the pre-prod envs cannot exercise (a dead upstream feed, logic only a real prod event triggers) stays a legitimate `UNVERIFIED: <path>` the gatekeeper verifies on prod at release (`skills/process-subdev`). A prod-STATE READ has a self-service answer, so "can't verify prod state" written as a hand-off or a bounce is itself a FINDING, not an honest UNVERIFIED.

Mechanically gated on BOTH sides: `hooks/block-gk-request-without-selfservice.sh` BLOCKS a gk action request without a `Self-service-checked:` line; the OWNER-CHAT path is now ALSO hook-gated (#608) — `hooks/stop-check-prose-violations.sh` blocks an owner-facing "cannot verify on PROD" claim without a self-service attempt or explicit `UNVERIFIED:`. History + rationale — mechanization incidents (#516/#608): `.claude/rules-reference/autonomous-verification-history.md` (#859).

#### Banned hand-off phrases (intent — all rewordings apply)

All mean "user, you are my tester now" and are FORBIDDEN — shifting verification from YOUR tools (Playwright / curl / SSH / MCP) to the user's eyes/clicks. Representative: "Can you test it on your end?", "Please verify it works", "Let me know if it works / breaks", "Tell me what you see", "Ping me when you've checked", "Report back when…", "Next user test", "Using you as tester", "I'll fix locally before next user test". This family is HARD-blocked at Stop by `stop-check-prose-violations.sh` (locked by the `TestTesterHandoffHook` tests) — with one escape: an explicit `UNVERIFIED:` line stating what you cannot test and why.

The hook is a backstop, not the whole rule — these semantic variants it does NOT reliably catch are equally banned and must be self-policed: "Try it and tell me what happens", "Run it and confirm", "Test it in your browser / on your machine", a bare "On your end" / "in your environment" (when about testing). The intent — not the exact wording — is banned; applies to all rewordings and semantic equivalents.

#### Mobile-app projects — the emulator/adb IS your Playwright

For a mobile-app project, an Android emulator (`adb`) or iOS simulator IS the Playwright-equivalent — verify ON THE EMULATOR yourself. The user's real device may appear ONLY as a FINAL ACCEPTANCE step, once emulator-side verification is green — NEVER as an iterative debug channel. "Install the new build and tell me if it crashes now" / "try again on your phone" is banned in all rewordings. History + incident: `.claude/rules-reference/autonomous-verification-history.md` (#859).

#### A missing UTILITY is YOURS to INSTALL — immediately, never worked around

When work hits a missing locally-installable dependency — `command not found`, `ModuleNotFoundError` / `ImportError` — **INSTALL it NOW** (`sudo -n apt-get install -y <pkg>`, `pip install`, `npm i -g`), then re-run the command.

- **BANNED: switching to a degraded workaround instead of installing.** Install the missing piece; the workaround costs more than the install every time.
- **BANNED: burning turns diagnosing "broken" behavior that is just an uninstalled utility.**
- **No sudo on a restricted box** → file via `python3 ~/devel/airuleset/airuleset.py gk-request --title "..."`. New runtime deps go into `RUNTIME_DEPS` in airuleset.py.
- **EXCEPTION — a heavy build toolchain is NEVER installed on a shared-stream box** (subdev); run on **dev2** instead; see `no-local-builds.md`. History + incidents: `.claude/rules-reference/autonomous-verification-history.md` (#859).

Applies to all rewordings and semantic equivalents — any "X doesn't work here because Y isn't installed, so I'll do Z instead" is this violation.

#### Before giving up — ASK FOR THE TOOL, not the test

The handoff banned above is "user, run this and tell me if it works". The **opposite** of that is also mandatory: **when you genuinely lack a tool, ask the user to give you that tool — do NOT silently give up and write `UNVERIFIED:`**.

Most blockers have a tool-shaped fix. The user has access to install / configure / share what you need. Ask for the SPECIFIC missing capability — never for a test.

**Tool requests you SHOULD make (correct hand-off direction):**

- "I don't have Playwright MCP installed in this session. Install `plugin:playwright` so I can drive the browser myself." → user installs → you test. **Note (#542, reverses #415):** Playwright is INSTALLED and force-ENABLED on every managed box, in EVERY managed project — a fresh session anywhere drives the browser with NO per-project opt-in and NO user step. So "nemám playwright / it's not installed here" is essentially never truthfully sayable in a managed project; if a `browser_*` call genuinely fails, treat it as a real blocker to debug yourself (browser cache, MCP server) — never as "the plugin is off in this project". (The browser is lazy: the always-on node MCP server is cheap, Chrome spawns only on the first browser call, so a browser-free project never carries a resident Chrome — availability everywhere costs almost nothing.)
- "MCP server `win-resolume` is unreachable. Restart it on the Windows host (or share the new host/port)." → user restarts → you test
- "I can't authenticate to claude.ai — the OAuth flow needs a real browser session against your account. Install Chrome DevTools MCP / Playwright with persistent profile, OR paste a session cookie / bearer token from your active session." → user provides credential → you test
- "I need read access to the production Postgres instance to verify the migration landed. Share connection string in 1Password / set `PROD_DB_URL` env / open SSH tunnel." → user provides → you test — **but if the project already gives you a self-service prod-read path (see "What's on PROD?" above — a direct read-only channel or `REFRESH-DEV-BOX-FROM-PROD`), use THAT and do not ask.**
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
