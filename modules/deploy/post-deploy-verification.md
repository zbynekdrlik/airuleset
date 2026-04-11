### Post-Deploy Verification

**Context gate — related rules you MUST also apply:**
- `autonomous-verification.md` — YOU have Playwright; never ask the user to test or screenshot
- `e2e-real-user-testing.md` — functional verification must use browser, not curl
- `no-localhost-urls.md` — use the real IP, never localhost
- `approval-scope.md` — "merge it" ≠ "deploy to production"; ask separately

After every deployment, verify the target machine responds correctly before reporting success. **Verification has TWO mandatory layers — liveness AND functional.**

#### Layer 1: Liveness (necessary but NOT sufficient)

- Process running: `ssh USER@HOST "tasklist | findstr app"` or `pgrep`
- Health endpoint responds: `curl` or API call returns 200
- NEVER claim "deployed successfully" based on the deploy step exiting cleanly — the app may have crashed on startup

#### Layer 2: Functional — Playwright E2E against live system (MANDATORY)

**After deploy, you MUST open the deployed app in Playwright and test the SPECIFIC feature you changed:**

1. Navigate to the deployed app URL in Playwright (real IP, not localhost)
2. Click through the feature you implemented or fixed — the same workflow a user would
3. Verify the UI shows correct state (elements visible, values correct)
4. Verify the backend effect (data saved, action propagated to target system)
5. Check browser console for zero errors/warnings

**A deploy verified only with curl/health checks is NOT verified.** Curl proves the server is running. Playwright proves the feature works.

#### When a verification tool fails

- If ECONNREFUSED: try alternative tools (curl, MCP tools, SSH) before concluding unreachable
- If Playwright cannot reach the URL: check the deploy actually completed, check firewall/network
- If post-deploy verification fails: investigate and fix immediately — a deploy is not done until verified working
