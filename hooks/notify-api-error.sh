#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — RETIRED to a documented NO-OP (#546, owner directive 2026-08-18).
#
# This hook USED to fire ONE Discord ping when a turn ended on a Claude Code API
# error (the `isApiErrorMessage` transcript flag). The owner directive (issue
# #546, comment 5333914691, verbatim: "zaspamovat ma do discordu je ciste
# kontraproduktivne … stara sa teraz o limit a subscription iny project") makes
# the whole api-error PING class counterproductive: airuleset's ONLY job on an
# api-error is the watchdog's SILENT `continue` auto-resume (untouched, #175).
# So this Stop channel is retired — it no longer computes a project label, calls
# the notify api-error path, or posts anything to Discord.
#
# Defense in depth: even if a stale/foreign caller still reaches the notify
# api-error path, `notify.send()` itself now suppresses every `apierr*` dedup
# key (`SUPPRESSED_ALERT_PREFIXES`, #546), so no api-error ping can escape from
# EITHER source.
#
# Kept WIRED (settings/hooks.json untouched) as a documented no-op — unwiring it
# would touch the install/settings-merge machinery for zero behavioural gain.
# Drain stdin (so Claude Code's payload write never hits SIGPIPE), then exit 0
# silently so it never interferes with the other Stop gates.

cat >/dev/null 2>&1 || true
exit 0
