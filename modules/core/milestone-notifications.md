### Device Notifications — Mobile-App Model

<!-- History: .claude/rules-reference/milestone-notifications-history.md -->

**Discord pings only on a genuine `❓` question (IMMEDIATELY) or FULL completion (`✅`) — never on `⏳`, never on per-phase progress, 24/7.** Owner policy (#1137): **zbynek** gets only the `acctblock:` / `credential-dead:` / healthz-down alarms, everything else is logged `suppressed` (he reads the footer `U`); **david** gets `❓`, `✅`, the run-card and the hand-off (bounce / gk-req) pings. Delivery is automatic from your status marker — never hand-fire `reply` / `PushNotification`. Device content is Slovak: `✅ DONE:` one short line, `❓` the full question block.

**EXCEPTION — the per-ticket run-card** (`airuleset.py notify --run-card`, fired by the worker at merge) is ENFORCED (#134): non-zero exit on failure, `subagent-stop-check-run-card.sh`, job 25 reconcile; `suppressed` counts as delivered.

Mechanism, routing and anti-patterns: `notification-mechanics` skill + `skills/milestone-notifications-deep/DEEP.md` (auto-loads on `airuleset.py notify` / `notify-discord`).
