### Post-Deploy Verification

**Context gate — related rules you MUST also apply:**
- `autonomous-verification.md` — YOU have Playwright; never ask the user to test or screenshot
- `e2e-real-user-testing.md` — functional verification must use browser, not curl
- `no-localhost-urls.md` — use the real IP, never localhost
- `approval-scope.md` — "merge it" ≠ "deploy to production"; ask separately
- `version-on-dashboard.md` — every web dashboard MUST show the deployed version label; verification reads it from the live DOM

After every deployment, verify the target machine responds correctly before reporting success. **Verification has THREE mandatory layers — liveness, version match, AND functional.**

#### Layer 1: Liveness (necessary but NOT sufficient)

- Process running: `ssh USER@HOST "tasklist | findstr app"` or `pgrep`
- Health endpoint responds: `curl` or API call returns 200
- NEVER claim "deployed successfully" based on the deploy step exiting cleanly — the app may have crashed on startup

#### Layer 2: Version match — read from the live DOM (MANDATORY for web UIs)

After liveness passes and BEFORE functional E2E, confirm the deployed version is actually live:

1. Open the dashboard in Playwright (real browser, not curl)
2. Read the version label from the DOM (e.g. `[data-testid="version"]`, footer, navbar)
3. Compare against the version that was just deployed (`git describe`, `package.json`, or the backend `/api/version`)
4. Frontend AND backend versions must match — they share a single git-tag source per `version-on-dashboard.md`
5. If they don't match → the deploy failed silently. Causes to investigate: CDN cache, build skipped, wrong target host, stale service worker. Fix before claiming done.

**Anti-patterns:**
- "curl returned the new version, deploy verified" → **WRONG.** Curl hits the API; the user sees the DOM. Read the DOM.
- "Frontend shows v1.0.5, backend serves v1.0.7 — close enough" → **WRONG.** That's frontend/backend drift; investigate.
- "Dashboard has no version label" → **WRONG.** That's a foundation gap. File the foundation issue per `version-on-dashboard.md` before further work.

The completion-report `✅ Deploy:` line MUST include the version read from the DOM, e.g. `✅ Deploy: dev frontend shows v1.0.97-dev.9 (matches backend /api/version)`.

#### Layer 3: Functional — Playwright E2E against live system (MANDATORY)

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
