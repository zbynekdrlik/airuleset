# shellcheck shell=bash
# SOURCED, never executed — airuleset #740. (No shebang: this is a library for
# the two hooks that fingerprint a delivered question into
# ~/.claude/notify-delivery.log; they already run under `set -euo pipefail`, so
# re-declaring shell options here would reach into every caller.)
#
# ONE definition of the delivery-log question fingerprint, shared by
# notify-discord-pending.sh (its send_q/_pending_log qhash) and
# stop-check-question-quality.sh (#740: the repeat-asked-question BLOCK reason)
# — so the two hooks compute the SAME qhash for the SAME delivered question, and
# a suppressed ping in one log lines up with a blocked repeat in the other,
# never two copy-pasted computations that could drift (the lib-presence.sh
# single-source pattern).
#
# _qhash <text>  ->  a short, stable hex fingerprint (sha1 where present, cksum
#                    portable fallback), first 8 hex chars. Used ONLY for log
#                    identity, never a decision.
_qhash() {
    printf '%s' "${1:-}" | { sha1sum 2>/dev/null || cksum; } \
        | tr -cd '0-9a-fA-F' | cut -c1-8
}
