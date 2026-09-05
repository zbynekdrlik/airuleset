### Autonomous Verification Directive

**You are responsible for verifying that your work ACTUALLY WORKS. Never ask the user to test, verify, take screenshots, or act as a tester.**

**CONTENT read-back is MANDATORY and hook-enforced:** every `## ✅ Work Complete` report carries `✅ Výstup: <concrete observed values read back from the real artifact>` (or an explicit `n/a — <prečo>`); `stop-check-prose-violations.sh` blocks a report without it. See `completion-report.md` (Hard rules).

**Banned hand-off phrases** — all mean "user, you are my tester now" and are HARD-blocked at Stop by `stop-check-prose-violations.sh`: "Can you test it on your end?", "Please verify it works", "Let me know if it works / breaks", "Tell me what you see". One escape: an explicit `UNVERIFIED:` line stating what you cannot test and why.

**"What's on PROD?" is a SELF-SERVICE question** — exhaust the self-service prod-read paths FIRST (the stream's direct read-only channel; a FRESH COPY of prod via `REFRESH-DEV-BOX-FROM-PROD`). `UNVERIFIED` / hand-off for a prod read is the LAST choice, never the first. Hook-gated on BOTH sides (`block-gk-request-without-selfservice.sh`, `stop-check-prose-violations.sh`).

**Hitting a blocker is NOT a hand-off trigger** — debug it yourself with existing tools; ask for the TOOL/ACCESS, never for a test.

The full verification protocol, anti-patterns, blocker reaction protocol, prod-read decision tree, mobile-app emulator rule, missing-utility install rule, and tool-request shape are in the situational companion `skills/autonomous-verification-deep/DEEP.md` — loaded automatically on `REFRESH-DEV-BOX-FROM-PROD`/`browser_`/`playwright` commands. History + incidents: `.claude/rules-reference/autonomous-verification-history.md` (#859).
