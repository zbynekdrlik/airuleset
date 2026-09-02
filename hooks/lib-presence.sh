# SOURCED, never executed — airuleset #842. (No shebang: this is a library for
# the two hooks that gate on "the owner is not at the terminal"; they already
# run under `set -euo pipefail`, so re-declaring shell options here would reach
# into every caller.)
#
# ONE definition of the UNATTENDED/away signal, shared by
# block-main-implementation.sh (its away condition) and
# block-ungated-issue-filing.sh (the #842 net-drain ratchet + presence gates) —
# extracted from block-main-implementation.sh's own inline mtime comparison so
# the 900s presence read has a single source, never two copy-pasted checks that
# could drift.
#
# airuleset_presence_is_away <session_id>
#   returns 0  = AWAY / UNATTENDED — the presence marker for <session_id> is at
#                least AIRULESET_MAIN_GUARD_AWAY_S seconds old.
#   returns 1  = PRESENT / attended — INCLUDING when the marker is ABSENT or
#                unreadable (fail-OPEN: never manufacture an unattended verdict
#                from an unmeasurable presence state; block-main-implementation.sh
#                documents "/tmp cleared under a long-running session" as an
#                accepted fail-open, and the ratchet inherits it — flipping the
#                direction would make a tmpfiles cleanup hard-block ALL filing).
#
# The marker is /tmp/claude-user-active-<session_id>, touched on
# UserPromptSubmit by clear-question-dedup.sh (a goal re-poke or hook feedback
# does NOT fire UserPromptSubmit, so an autonomous loop legitimately looks away
# after the threshold). Threshold: AIRULESET_MAIN_GUARD_AWAY_S (default 900); 0
# disables the signal (always PRESENT); a garbage value falls back to 900.
airuleset_presence_is_away() {
    local _sid _away_s _mark _active_at _now
    _sid="${1:-}"
    _away_s="${AIRULESET_MAIN_GUARD_AWAY_S:-900}"
    case "$_away_s" in ''|*[!0-9]*) _away_s=900 ;; esac
    [ "$_away_s" -gt 0 ] || return 1
    [ -n "$_sid" ] || return 1
    _mark="/tmp/claude-user-active-${_sid}"
    [ -f "$_mark" ] || return 1
    _active_at=$(stat -c %Y "$_mark" 2>/dev/null || echo 0)
    case "$_active_at" in ''|*[!0-9]*) _active_at=0 ;; esac
    [ "$_active_at" -gt 0 ] || return 1
    _now=$(date +%s)
    [ "$(( _now - _active_at ))" -ge "$_away_s" ] && return 0
    return 1
}
