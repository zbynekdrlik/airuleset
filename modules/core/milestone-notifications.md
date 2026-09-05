### Device Notifications — Mobile-App Model: Only When ASKING or FULLY DONE

**Context gate — related rules you MUST also apply:**
- `message-status-marker.md` — every message ends with ❓ / ⏳ / ✅; the device ping forwards the ❓ / ✅ content
- `autopilot` skill — the ONE per-ticket device card (the EXCEPTION below) is fired by the worker directly at merge
- `notification-mechanics` skill — the retired api-error/limit/burn alert-class suppression (#546), per-owner thread + `DISCORD_MIRROR` routing, and the per-ticket card's field-by-field composition moved there VERBATIM — load it before authoring/debugging the notify hooks or watchdog

**The device (Discord / phone) is notified ONLY when Claude genuinely ASKS (`❓ NEEDS YOU`) or has FULLY completed (`✅ DONE`) — never on `⏳ WORKING`, never on routine per-phase progress. 24/7 — no night/day difference (#791).** The mechanism is AUTOMATIC via three hooks (`notify-discord-pending.sh`, `notify-discord.sh`, `notify-discord-send.sh`) — do NOT hand-fire per-phase pings.

**Device content = Slovak, phone-readable.** `✅ DONE:` = ONE short Slovak line. `❓` = the FULL `**Otázka — projekt …:**` question block (hook-enforced `stop-check-question-quality.sh`).

**Per-ticket completion card (the ONE sanctioned per-ticket hand-fire):** `airuleset.py notify --run-card`, ENFORCED not advisory (#134) — `notify --run-card` exits non-zero on failure; `subagent-stop-check-run-card.sh` blocks a worker; watchdog job 25 reconciles.

The full mechanism detail, per-ticket done-state goal-guard, API-error retirement (#546), per-phase suppression, owner-thread routing + mirrors, and anti-patterns are in the situational companion `skills/milestone-notifications-deep/DEEP.md` — loaded automatically on `airuleset.py notify`/`notify-discord` commands. History + rationale: `.claude/rules-reference/milestone-notifications-history.md` (#859).
