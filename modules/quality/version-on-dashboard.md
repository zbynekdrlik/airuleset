### Version Display on Dashboard (MANDATORY for every web UI)

**Every web dashboard / frontend MUST display the deployed version visibly on every page. This applies to all projects with a UI — no exceptions, no "we'll add it later".**

#### Why this is non-negotiable

- Post-deploy verification depends on it. Without a visible version, "did my code go live?" is unanswerable from the live UI.
- When debugging in production, the version label tells you which commit's behavior the user saw.
- Frontend/backend drift is invisible without labels — a UI showing v1.0.5 against an API serving v1.0.7 is a real bug that ships silently.
- The user works across many projects and refreshes dashboards to verify deploys — the version is the single most important UI element for that workflow.

#### Required format

```
v<major>.<minor>.<patch>(-dev.<n>)?
```

Examples:
- `v1.0.97-dev.9` — dev build, 9th commit since v1.0.97
- `v1.0.97` — production release
- `v1.0.97-dev.9 (abc1234)` — with optional short SHA
- `v1.0.97-dev.9 (abc1234, 2026-04-27)` — with optional SHA + build date

Rules:
- Format minimum: `v<semver>`. Match `git describe --tags --dirty` style.
- **Visible without scrolling** — typical positions: sticky bottom-right footer, navbar/header, or a labeled "Version: vX.Y.Z" element. Pick one and put it there on EVERY route.
- **Build-time injection** — generated from `git describe` (or equivalent) at build time so the displayed version exactly matches the deployed binary. NEVER hardcode `0.1.0` or `unknown` as a placeholder.
- **Single source of truth** — the frontend label and backend `/api/version` (or equivalent) must report the same value, derived from the same git tag at the same build. If they can drift, they will drift.
- **No localhost / placeholder strings in production** — `v0.0.0-dev` shipping to prod is a deploy failure.

#### Verification (mandatory in post-deploy-verification)

After every deploy, the agent MUST:
1. Open the dashboard in Playwright (real browser, not curl).
2. Read the version label from the DOM.
3. Compare against the version that was just deployed (`git describe`, package.json, the backend `/api/version` endpoint).
4. If they don't match → the deploy failed silently. Investigate (cache, CDN, build skipped, wrong target) and fix before reporting done.

The completion-report `✅ Deploy:` line must include the version that's visible on the dashboard, e.g. `✅ Deploy: dev frontend shows v1.0.97-dev.9 (matches backend)`.

#### Foundation gate — file an issue if the project has no version display

If you start work on a web project and the dashboard does NOT have a version display, this is a foundation gap. **File it as the FIRST issue, before any feature work**:

```bash
gh issue create \
  --title "Add version display to dashboard (per version-on-dashboard.md)" \
  --body "The dashboard has no visible version label. Without it, post-deploy verification cannot confirm new code is live, frontend/backend drift becomes invisible, and production debugging loses an essential signal. Add v<semver> to footer or navbar, build-time injected from git describe, matching the deployed binary. Add a Playwright test asserting the label format. See ~/devel/airuleset/modules/quality/version-on-dashboard.md."
```

The `/issue-planner` skill checks for this on every web project — if missing, it blocks issue selection until the foundation issue is filed. See `issue-planner` skill, Step 1d.

#### E2E test required

Every web project MUST have a committed Playwright test asserting the version label exists, is visible, and matches the expected format:

```typescript
test('dashboard shows correctly formatted version label', async ({ page }) => {
  await page.goto('/');
  const version = await page.locator('[data-testid="version"]').textContent();
  expect(version).toMatch(/^v\d+\.\d+\.\d+(-dev\.\d+)?(\s\([0-9a-f]{7}(,\s\d{4}-\d{2}-\d{2})?\))?$/);
  // Optional: assert it matches the backend
  const apiVersion = await page.evaluate(() => fetch('/api/version').then(r => r.text()));
  expect(version).toContain(apiVersion.trim());
});
```

If the version label moves, gets broken, or stops matching the backend, this test catches it BEFORE deploy. Without this test, you have no E2E coverage on the most important UI element.

#### Anti-patterns

- "We'll add the version label after this feature ships" → **WRONG.** It's a foundation, not a feature. File the foundation issue first.
- Hardcoded `0.1.0` placeholder shipped to prod → **WRONG.** Build-time injection only.
- Version label only on `/about` page → **WRONG.** Every route, no scrolling needed.
- Frontend version label sourced separately from backend version → **WRONG.** Single git-tag source.
- "I deployed and curl returned 200, version verified" → **WRONG.** Curl proves the server runs. Open Playwright, read the DOM label, compare.
