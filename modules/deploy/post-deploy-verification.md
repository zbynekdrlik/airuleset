### Post-Deploy Verification → on-demand skill `post-deploy-verification`

The full three-layer protocol moved VERBATIM to the `post-deploy-verification` skill — load it after every deployment, before claiming it verified. Non-negotiable that survives here: verification has THREE mandatory layers — **liveness** (process/health-check), **version match** (read from the LIVE DOM, not curl), and **functional** (Playwright E2E on the SPECIFIC changed feature) — and it happens on the live/prod target without asking about events (`approval-scope.md`).
