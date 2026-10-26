### Device Notifications — Mobile-App Model: Only When ASKING or FULLY DONE

**Context gate — related rules you MUST also apply:**
- `message-status-marker.md` — every message ends with ❓ / ⏳ / ✅; the device ping forwards the ❓ / ✅ content
- `autopilot` skill — the ONE per-ticket device card (the EXCEPTION below) is fired by the worker directly at merge
- `notification-mechanics` skill — alert-class suppression (#546), per-owner routing + mirrors, the per-ticket card's composition — load it before authoring/debugging the notify hooks or watchdog

**The device (Discord / phone) is notified ONLY when Claude genuinely ASKS (`❓ NEEDS YOU`) or has FULLY completed (`✅ DONE`) — never on `⏳ WORKING`, never on routine per-phase progress. 24/7 — no night/day difference (#791).** ❓ pings IMMEDIATELY, ✅ only at FULL completion. Owner **zbynek** (#1137): only `acctblock:`/`credential-dead:`/healthz-down alarms reach Discord, everything else is logged `suppressed`. Do NOT call the discord `reply` tool or `PushNotification` to announce milestones. The mechanism is AUTOMATIC via three hooks (`notify-discord-pending.sh`, `notify-discord.sh`, `notify-discord-send.sh`) — do NOT hand-fire per-phase pings.

**Device content = Slovak, phone-readable.** `✅ DONE:` = ONE short Slovak line. `❓` = the FULL `**Otázka — projekt …:**` question block (hook-enforced `stop-check-question-quality.sh`).

**Per-ticket completion card (the ONE sanctioned per-ticket hand-fire):** `airuleset.py notify --run-card`, ENFORCED (#134) — `notify --run-card` exits non-zero on failure; `subagent-stop-check-run-card.sh` blocks a worker; job 25 reconciles; `suppressed` counts as delivered.

Mechanism detail, the done-state goal-guard and anti-patterns are in the situational companion `skills/milestone-notifications-deep/DEEP.md` — loaded automatically on `airuleset.py notify`/`notify-discord` commands. History + rationale: `.claude/rules-reference/milestone-notifications-history.md` (#859).
