#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Blocks on HARD violations (missing required completion-report fields)
# via {"decision":"block",...} JSON output, with retry limit (max 2 per session)
# to avoid runaway loops if a violation is genuinely unfixable.
# Warns on SOFT violations (banned phrases, prose questions) via stderr.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")

# #198 — absence must stay DETECTABLE. The old `// "unknown"` fallback turned
# every invocation that could not identify itself into the SAME retry-counter
# key, i.e. one shared, never-expiring per-BOX bucket. An empty value here is
# resolved to "no retry state" further down, never to a bucket.
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || echo "")
[ -z "$MSG" ] && exit 0

# #96: use vs MENTION — third occurrence of the exact classifier-blindness
# class as #80 (a heredoc BODY read as a command) and #91 (a comment body
# DESCRIBING a trigger table read as the trigger itself). Right after #92
# turned the quality-bypass family HARD, this gate blocked a status message
# that merely REFERRED to the rule — it enumerated the newly-banned phrases
# while describing what the hook now blocks; no bypass was offered to
# anyone. A MENTION carries a stable structural signal a genuine bare-
# sentence OFFER never has: the phrase sits in backticks, a fenced code
# block, or a quoted span. Strip those spans BEFORE the phrase-matching
# checks below run (never the structural/report-shape checks — those are
# about PRESENCE/ABSENCE of report fields, not about a banned phrase, so
# they keep reading the raw MSG unchanged). GREEN must never open a hole:
# a genuine bare, unquoted offer keeps matching because it carries none of
# these signals — verified in tests/test_quality_bypass_gate.py against
# every existing unambiguous-bypass case.
# #195 — UNDET_FILE and record_undet (the diagnostic-accumulator mechanism
# explained in full a little further down, right before _msg_grep_rc) are
# pulled UP ahead of strip_mentions(), out of their historical position ~80
# lines later in this file, purely so a strip_mentions() failure can report
# through the SAME channel the msg_has/msg_missing framework already uses —
# no new mechanism, just an earlier definition point.
UNDET_FILE=$(mktemp "${TMPDIR:-/tmp}/airuleset-prose-undet.XXXXXX" 2>/dev/null) || UNDET_FILE=""
if [ -n "$UNDET_FILE" ]; then
    trap 'rm -f "$UNDET_FILE"' EXIT
fi

# Announce + record a question a check could not answer (grep erroring,
# further down, or here — strip_mentions() itself failing to run). Callers
# run inside `$( )`, where a variable assignment would not survive the
# subshell — hence a file.
record_undet() {
    echo "stop-check-prose-violations: check exited $1 for [$2] — this check is UNDETERMINABLE" >&2
    if [ -n "$UNDET_FILE" ]; then
        printf '%s\n' "$2" >>"$UNDET_FILE" 2>/dev/null || true
    fi
}

strip_mentions() {
    # #195 — the message is passed by a FILE, never argv. This used to be
    # `python3 - "$1" <<'PYEOF'`, handing the WHOLE message to execve as a
    # SINGLE argv entry; Linux's MAX_ARG_STRLEN caps that at 131072 bytes
    # (measured: exec succeeds at 131,000 bytes, E2BIG at 140,000), so any
    # message past it made python3 never start — and the caller's bare
    # `|| printf` fallback then silently substituted the RAW, UNSTRIPPED
    # message, so a MENTION (backticked/quoted/fenced) read exactly like a
    # bare offer. A file has no such ceiling; this is the same fix
    # hooks/lib-poll-payload.sh (#124) already shipped for an identical
    # failure class in a sibling hook.
    # #195-review — a symlink-safe scratch DIR (this repo's own established
    # residual: SIGKILL between mktemp and the `rm -f` below still leaks the
    # file, the same untrappable class this repo already accepts for other
    # scratch temp files; an EXIT trap would need to bake the path in via
    # DOUBLE-quote expansion at registration time, since a `local` var is
    # unbound by the time a trap fires after the function has returned —
    # verified live — and that reopens a quote-injection surface via a
    # hostile TMPDIR for a residual with zero observed leaks on any normal
    # exit path, so it stays documented rather than "fixed").
    local _text="$1" _f _rc=0
    _f=$(mktemp "${TMPDIR:-/tmp}/airuleset-prose-mention.XXXXXX" 2>/dev/null) || return 1
    # #195-review — braced so a REDIRECTION failure (not a printf failure)
    # is also caught by `2>/dev/null`: bash sets up `>"$_f"` before it sets
    # up `2>/dev/null`, so an unbraced `cmd >"$_f" 2>/dev/null` still prints
    # a bare redirection error to the CALLER's live stderr when `>"$_f"`
    # itself fails — the same shape this file's own #196 fix already
    # corrected for the retry-counter write, reproduced here on review.
    { printf '%s' "$_text" >"$_f"; } 2>/dev/null || { rm -f "$_f" 2>/dev/null || true; return 1; }
    python3 - "$_f" <<'PYEOF' || _rc=$?
import re
import sys

# #195-review — newline="" disables universal-newline translation.
# sys.argv[1] (the old input path) never ran through Python's text-mode I/O
# layer at all, so a lone \r or a \r\n pair survived untouched; the default
# newline=None here would silently translate both to \n, splitting a bare
# offer straddling a lone \r across what grep sees as two lines and
# un-blocking it — reproduced against the real hook, fixed by this one flag.
with open(sys.argv[1], "r", encoding="utf-8", errors="surrogateescape",
          newline="") as fh:
    text = fh.read()
text = re.sub(r"```.*?```", " ", text, flags=re.S)   # fenced code block
text = re.sub(r"`[^`]*`", " ", text)                  # backtick span
text = re.sub(r'"[^"]*"', " ", text)                  # double-quoted span
# NOTE: deliberately no single-quote (') stripping — English contractions
# ("it's", "won't", "I'll") make a bare apostrophe pair unreliable as a
# quote delimiter (a real regression caught in review: "It's mergeable but
# I won't claim it's clean." has 3 apostrophes, and a naive '...' strip ate
# the middle of the sentence). Backticks + double quotes + fenced code are
# the reliable mention signals; that is already enough for every real
# mention shape seen (#96).
sys.stdout.write(text)
PYEOF
    rm -f "$_f" 2>/dev/null || true
    return "$_rc"
}
# #195 — a strip that genuinely cannot run is an unresolvable EXONERATING
# signal (a MENTION exempts a phrase from being read as a bare offer): per
# this file's own #194 taxonomy an unsubstantiated exemption is DENIED, so
# the fallback stays the raw, unstripped message — but record_undet() now
# runs FIRST, so the note travels on the block reason instead of the failure
# vanishing silently the way a bare `|| printf` fallback used to.
#
# #195-review — the note's own WORDING must not blame size: #194 argued this
# fail-closed direction is acceptable because a wrong-closed block is
# ACTIONABLE ("shortening removes the cause as well as the symptom"). Once
# the message is read via a file, size can no longer BE the cause — the only
# residual failures here are environmental (mktemp/write/python3 itself), so
# the note says so explicitly instead of pointing the agent at a "repair"
# that would not help. (An unwritable TMPDIR also breaks UNDET_FILE's own
# mktemp a few lines above, so on THAT specific failure the note itself is
# unavailable too — the fail DIRECTION still stays correct, only the
# diagnostic degrades, exactly like every other UNDET_FILE-dependent note in
# this file; see TestAnUnwritableTmpdirDoesNotInvertTheFailDirection.)
MSG_MENTION=$(strip_mentions "$MSG") || {
    # #195-review — $? is captured FIRST, before any other statement (even a
    # plain assignment) runs and overwrites it — the same "bookkeeping must
    # not run before the verdict" ordering this file's own #196 fix already
    # established elsewhere; a `_NOTE=...` assignment placed ahead of this
    # read a strip failure would report as "check exited 0" instead of the
    # real code (caught live while verifying this exact fix, before it ever
    # reached a commit).
    _RC=$?
    _NOTE="strip_mentions (mention-strip) failed for the message — NOT a size problem (${#MSG} characters, well within any limit); the cause is environmental (mktemp/write/python3)"
    record_undet "$_RC" "$_NOTE"
    MSG_MENTION="$MSG"
}

# #190 — every check below asks "does this text contain X". It must be
# answered from the TEXT. It used to be answered by a pipeline's exit status:
#
#     HAS_REVIEW=$(echo "$MSG" | grep -qP '<pattern>' && echo 1 || echo 0)
#
# `grep -q` exits at its FIRST match without draining stdin, so under this
# script's own `set -euo pipefail` the `echo` writer is killed by SIGPIPE and
# the pipeline reports the WRITER's 141 instead of grep's verdict — which
# `&& echo 1 || echo 0` then collapses into 0, the value meaning "absent".
# The verdict therefore depended on process scheduling, not on the message.
#
# Measured on dev1 (GNU grep 3.11): rc=141 captured directly on a ~350-byte
# message under CPU saturation, and 100% reproducible once the message passes
# the 64 KiB pipe buffer — a 140 KB byte-for-byte correct completion report
# drew FIVE false violations (including "missing canonical heading" on a
# message whose first line IS that heading), while in the other direction
# `merge despite the failing check` stopped being blocked at all. That
# fail-OPEN half is the worse one: the guard silently does not guard.
#
# A here-string has no concurrent writer process, so the race cannot exist.
#
# #194 — the ERROR case needed the same treatment and did not get it. A grep
# exit >= 2 (unusable pattern, resource failure, PCRE backtracking limit) means
# THE QUESTION WAS NOT ANSWERED. Returning 1 there — "the pattern is not in the
# message" — is a fabricated verdict of exactly the same class as the 141 -> 0
# collapse above, and for a presence-triggered gate it meant no violation was
# ever added, so the hook exited CLEAN while a merge bypass shipped.
#
# The unknown is therefore resolved HERE, where it arises, and the CALLER picks
# the direction by picking which question to ask:
#
#     msg_has     "does the message CONTAIN this?"  unknown -> YES, it does
#     msg_missing "does the message LACK this?"     unknown -> YES, it lacks it
#
# An undeterminable check answers YES to whatever it was asked, so the VERB at
# each call site IS that site's declaration of its own fail direction:
#
#   * INCRIMINATING pattern (a banned phrase) -> msg_has -> the gate FIRES.
#     FAIL CLOSED. Wrong-open ships a merge bypass silently and is
#     unrecoverable; wrong-closed tells the agent to trim the message, which is
#     actionable AND removes the trigger, because the trigger is message size.
#   * EXONERATING pattern (a match would SUPPRESS an established violation)
#     -> msg_missing -> the exemption is DENIED. FAIL CLOSED. An unsubstantiated
#     exemption must never disarm a gate.
#   * REQUIRED REPORT FIELD (is the mandated line present?) -> msg_has -> no
#     violation is asserted. FAIL OPEN, deliberately: #190 measured that
#     "missing canonical heading", on a message whose first line IS that
#     heading, is an accusation the agent cannot act on — it burns the retry
#     budget and is then overridden by Claude Code's own consecutive-block cap.
#
# Because every unknown is resolved at its own site, nothing downstream needs to
# ask "did ANY check error" — so one check's failure can no longer reach another
# check's verdict, and the global suppression that used to do exactly that is
# gone. UNDET_FILE survives PURELY as a diagnostic accumulator: nothing gates on
# it, so a failed `mktemp` now costs a NOTE and can no longer invert the fail
# direction.
#
# #195 — UNDET_FILE and record_undet() are DEFINED earlier now (right before
# strip_mentions(), which needs them too), not here. This is still the one
# place their behaviour is explained in full.

# The one place grep is asked anything. 0 = match, 1 = no match, 2 = UNKNOWN.
# Never a writer process, so #190's SIGPIPE race cannot exist.
_msg_grep_rc() {
    local text="$1"
    shift
    local rc=0
    grep "$@" >/dev/null 2>&1 <<<"$text" || rc=$?
    if [ "$rc" -ge 2 ]; then
        record_undet "$rc" "$*"
        return 2
    fi
    return "$rc"
}

# msg_has <text> <grep-args...> — 0 when <text> matches. UNKNOWN -> 0.
msg_has() {
    local rc=0
    _msg_grep_rc "$@" || rc=$?
    if [ "$rc" -ge 2 ]; then
        return 0
    fi
    return "$rc"
}

# msg_missing <text> <grep-args...> — 0 when <text> does NOT match. UNKNOWN -> 0.
msg_missing() {
    local rc=0
    _msg_grep_rc "$@" || rc=$?
    if [ "$rc" -ge 2 ]; then
        return 0
    fi
    if [ "$rc" = "0" ]; then
        return 1
    fi
    return 0
}

# msg_count <text> <grep-args...> — prints the match count, or `?` when the
# question could not be answered. A count feeds REQUIRED-FIELD checks ("does the
# report carry enough 🌐 lines"), so the caller must SKIP on `?` rather than
# treat an unknown as zero and manufacture an accusation.
msg_count() {
    local text="$1"
    shift
    local out="" rc=0
    out=$(grep -c "$@" <<<"$text" 2>/dev/null) || rc=$?
    if [ "$rc" -ge 2 ]; then
        record_undet "$rc" "-c $*"
        printf '?\n'
        return 0
    fi
    printf '%s\n' "${out:-0}"
}

# msg_lines <text> <grep-args...> — prints matching lines, returns 1 when the
# question could not be answered, so a caller can tell "no match" from "could
# not tell" instead of reading an empty string as both.
msg_lines() {
    local text="$1"
    shift
    local out="" rc=0
    out=$(grep "$@" <<<"$text" 2>/dev/null) || rc=$?
    if [ "$rc" -ge 2 ]; then
        record_undet "$rc" "$*"
        return 1
    fi
    printf '%s' "$out"
    return 0
}

# msg_line_no <text> <grep-args...> — 1-based line number of the first match,
# or empty for "no match" AND for "could not tell". Feeds only the SOFT ordering
# warning, so an unknown stays silent rather than manufacturing an order
# violation out of a line number the hook never obtained.
msg_line_no() {
    local text="$1"
    shift
    local out="" rc=0
    out=$(grep -m1 -n "$@" <<<"$text" 2>/dev/null) || rc=$?
    if [ "$rc" -ge 2 ]; then
        record_undet "$rc" "-m1 -n $*"
        return 0
    fi
    printf '%s' "${out%%:*}"
}

# MSG_NOGOAL: MSG minus printed /goal TEMPLATE lines. The autopilot /goal
# templates are sanctioned machinery text and legitimately contain phrases the
# prose checks below hunt ("start…run…immediately…or…check" tripped the
# dispatch-or-hold regex once the review-watch clauses landed — every
# /autopilot arm message then hard-looped on this hook; montalu, 2026-07-20).
# Question/pause checks run on MSG_NOGOAL; report-structure checks keep MSG.
# The exemption EXONERATES, so an unanswerable filter must not grant it: fall
# back to the full MSG rather than to an empty string that matches nothing.
_NOGOAL_RC=0
MSG_NOGOAL=$(grep -v '^[[:space:]]*/goal ' <<<"$MSG") || _NOGOAL_RC=$?
if [ "$_NOGOAL_RC" -ge 2 ]; then
    record_undet "$_NOGOAL_RC" "-v ^[[:space:]]*/goal "
    MSG_NOGOAL="$MSG"
fi
# #195 — same fallback shape as MSG_MENTION above: record_undet() runs before
# the raw-text fallback, so a strip failure on this call site leaves a note
# too, instead of silently disarming the design-review gate's exemption.
MSG_NOGOAL_MENTION=$(strip_mentions "$MSG_NOGOAL") || {
    # #195-review — same $?-first ordering as MSG_MENTION above.
    _RC=$?
    _NOTE="strip_mentions (mention-strip) failed for the NOGOAL message — NOT a size problem (${#MSG_NOGOAL} characters, well within any limit); the cause is environmental (mktemp/write/python3)"
    record_undet "$_RC" "$_NOTE"
    MSG_NOGOAL_MENTION="$MSG_NOGOAL"
}

# HARD violations collected here trigger {"decision":"block"} response.
# SOFT violations go to stderr as warnings. Both can fire in the same hook run.
HARD_VIOLATIONS=""
add_hard() { HARD_VIOLATIONS="${HARD_VIOLATIONS}- $1\n"; }

# Retry limiter: max 5 blocks per session to avoid runaway loops.
# Was 2; bumped to 5 because completion-report violations are deterministically
# fixable and agent needs more room to iterate before the hook gives up.
# State stored in /tmp under per-session counter file.
#
# #196/#198 — everything below is BOOKKEEPING, and bookkeeping must never be
# able to suppress the verdict it is bookkeeping for. It used to, three ways:
# a counter read with no shape guard (non-numeric content made `[` exit 2, the
# `&&` chain false, and the whole block branch unreachable — rc 0, no block, no
# complaint); a write that ran BEFORE the verdict under `set -e` (fixed at the
# foot of this file); and a key that fell back to the literal "unknown", i.e.
# ONE per-BOX bucket that nothing ever expired. Five session-id-less calls
# disarmed the gate for the life of the box's /tmp — and did, on dev1, where
# the independent verification of #194 consequently read a shipped, correct,
# deployed fix as broken at every payload size.
#
# The rule, applied wherever an unknown arises: THE THROTTLE IS USED ONLY WHEN
# THIS INVOCATION'S OWN RETRY STATE IS POSITIVELY ESTABLISHED. No id, an unsafe
# id, a counter that is not digits, a counter of unknown or stale age — each is
# NO state, so RETRIES stays 0 and the verdict goes out. That is the settled
# fail direction for throttle state: never suppress a verdict.
#
# Losing the throttle is a degradation, not a runaway: Claude Code's own
# CLAUDE_CODE_STOP_HOOK_BLOCK_CAP (default 8) overrides ANY blocking Stop hook
# after 8 consecutive blocking Stops, so this counter is a courtesy throttle
# and never the loop's only bound.
MAX_RETRIES=5
# A counter older than this cannot belong to a live block loop — consecutive
# blocks are one message rewrite apart, seconds to minutes. It has to expire
# even though the key is per-session, because `claude -c` REUSES a session id
# across a restart, so the key does NOT die with the session.
RETRY_TTL_S=3600
RETRIES=0
RETRY_FILE=""

# The id is VALIDATED, never mangled. A sanitiser (`tr -c 'A-Za-z0-9' _`) is
# many-to-one, so two distinct hostile ids collapse onto ONE key — #198's
# shared bucket again, with a new spelling. An id that is not already a safe
# path component simply gets no state, which also keeps `/`, `..` and every
# shell metacharacter out of the path by construction rather than by escaping.
RETRY_KEY=""
case "$SESSION_ID" in
    "" | unknown) RETRY_KEY="" ;;             # unidentifiable — no state
    .* | *[!A-Za-z0-9._-]*) RETRY_KEY="" ;;   # not a safe path component
    *) RETRY_KEY="$SESSION_ID" ;;
esac
if [ -n "$RETRY_KEY" ] && [ "${#RETRY_KEY}" -le 200 ]; then
    RETRY_FILE="/tmp/airuleset-stop-block-${RETRY_KEY}"
fi

# This invocation's established retry count, or 0 meaning "there is no state".
# Every guard is a POSITIVE requirement and every failure prints 0, so anything
# unestablished lets the verdict out. It PRINTS rather than assigns, which keeps
# the caller's `set -e` out of reach of everything in here.
_retry_count_of() {
    local _f="$1" _sz _v _now _mt
    # A plain file, not a symlink, and OURS. `-f` alone follows a symlink, and
    # this path is world-plantable: /tmp is sticky and shared with foreign uids
    # by design here, while live session ids are readable straight out of /tmp
    # from this repo's own markers. A FIFO is the sharpest of the three — `cat`
    # would block on it until the harness kills the hook, so no verdict is ever
    # printed, which is precisely the fail-open being removed.
    [ -f "$_f" ] && [ ! -L "$_f" ] && [ -O "$_f" ] || { echo 0; return 0; }
    # This hook writes exactly one ASCII digit and a newline, because
    # MAX_RETRIES is a single digit (locked by a test). Anything larger is not
    # ours to interpret, and two shapes are actively dangerous: a long run of
    # digits makes `[ -lt ]` exit 2 — #196's own defect, reached by a different
    # byte sequence — and a NUL, which bash strips out of a command
    # substitution, can splice two digits into a number past the cap.
    _sz=$(stat -c %s "$_f" 2>/dev/null || echo "")
    case "$_sz" in 1 | 2) : ;; *) echo 0; return 0 ;; esac
    # Braces: the "ignored null byte in input" warning comes from the SHELL
    # performing the substitution, so a `2>` inside it is already too late.
    { _v=$(cat "$_f" 2>/dev/null || echo 0); } 2>/dev/null
    # Literal alternatives, never a RANGE or a character class: `[!0-9]` is
    # locale-collated and does NOT reject U+FF13, which then reaches `[ -lt ]`
    # as a non-integer. Verified on this box.
    case "$_v" in 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9) : ;; *) echo 0; return 0 ;; esac
    # An age we can establish, that is neither stale NOR IN THE FUTURE. Without
    # the upper bound a negative difference passes every `-le` test, so one
    # backward clock step (an NTP correction, a wrong RTC at boot, a restored
    # snapshot) would make every existing counter permanently un-expirable —
    # #198's immortal bucket, rebuilt.
    _now=$(date +%s 2>/dev/null || echo "")
    _mt=$(stat -c %Y "$_f" 2>/dev/null || echo "")
    case "$_now" in "" | *[!0123456789]*) echo 0; return 0 ;; esac
    case "$_mt" in "" | *[!0123456789]*) echo 0; return 0 ;; esac
    [ "$_mt" -le "$_now" ] || { echo 0; return 0; }
    [ "$((_now - _mt))" -le "$RETRY_TTL_S" ] || { echo 0; return 0; }
    echo "$_v"
}

if [ -n "$RETRY_FILE" ]; then
    RETRIES=$(_retry_count_of "$RETRY_FILE")
fi

# Check for subagent vs inline prose question (HARD block — repeat offender pattern).
if msg_has "$MSG_MENTION" -qiE "subagent.?driven.*inline|two execution options|which (approach|execution)|subagent or (sequential|inline)|inline execution.*subagent|subagent.*inline execution|dispatch now or skim|dispatch now or hold|dispatch now or pause|dispatch.*subagents?.*or (hold|skim|pause|wait|review)"; then
    echo "VIOLATION: You asked 'subagent or inline' / 'two execution options' / 'dispatch now or skim' in prose at the end of your message. This is a pre-answered question — always use subagent-driven, dispatch immediately. The pre-ask-auto-answer hook blocks the structured AskUserQuestion form; writing the same question in prose is the same violation. Rewrite this message: cut the question entirely, and proceed with subagent-driven dispatch. See ask-before-assuming.md pre-answered table." >&2
    add_hard "Pre-answered prose question: subagent-vs-inline / two execution options / dispatch-now-or-skim"
fi

# Check for visual companion prose question
if msg_has "$MSG" -qiE "want to try.*(visual|mockup|browser)|easier to explain.*browser|visual companion"; then
    echo "VIOLATION: You offered visual companion in prose. This is a pre-answered question — always yes. Next time, just use it without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Detect ASCII-art / box-drawing UI layout mockup paired with layout/position keywords.
# When agent draws UI layout in terminal text, visual companion MUST be used instead.
# The brainstorming skill's "use terminal for conceptual questions" escape DOES NOT apply
# to layout/position/component-placement questions — those are always visual.
HAS_BOXDRAW=$(msg_has "$MSG" -qE "[┌┐└┘─│├┤┬┴┼╔╗╚╝═║█▓▒░▀▄■□]{3,}" && echo 1 || echo 0)
HAS_LAYOUT_KW=$(msg_has "$MSG" -qiE "\b(header|footer|navbar|sidebar|toolbar|titlebar|status.?bar|menu.?bar|top border|bottom border|top.right|top.left|bottom.right|bottom.left|version label|version display|logo placement|page header|page footer|presenter (panel|view|placement)|top of (the )?(page|screen|window|view|border)|bottom of (the )?(page|screen|window|view|border)|above (the )?(header|footer|button|panel)|below (the )?(header|footer|button|panel)|position (of|the)|place (the )?[a-z]+ (on|in|at)|move (the )?[a-z]+ to|layout option|wizard step|dashboard layout|side.by.side layout|column layout|grid layout|component placement|fixed (top|bottom|header|footer)|sticky (top|header|footer))\b" && echo 1 || echo 0)
# A live companion URL EXONERATES (the mockup is already in a browser), so ask
# whether it is MISSING — an unanswerable check must not grant the exemption.
NO_COMPANION_URL=$(msg_missing "$MSG" -qE "http://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+|visual companion (live|running|started|at)|start-server\.sh" && echo 1 || echo 0)
if [ "$HAS_LAYOUT_KW" = "1" ] && [ "$HAS_BOXDRAW" = "1" ] && [ "$NO_COMPANION_URL" = "1" ]; then
    echo "VIOLATION: You drew a UI layout in ASCII / box-drawing text-art for a LAYOUT/POSITION question. The user has explicitly stated terminal ASCII art is UNREADABLE for visual design decisions, causing repeated wrong iterations. Visual companion is MANDATORY for layout/position/UI-design questions — not optional." >&2
    echo "" >&2
    echo "  Start it NOW:" >&2
    echo "    bash ~/.claude/plugins/cache/claude-plugins-official/superpowers/*/skills/brainstorming/scripts/start-server.sh --project-dir <project-root>" >&2
    echo "  Then render mockups via the visual companion API and post the http://<ip>:<port> URL for the user." >&2
    echo "" >&2
    echo "  Banned: ASCII art layouts (┌─┐│└┘ etc.), text-mockup grids, '+--+' boxes for ANY layout/position question." >&2
    echo "  Allowed terminal output: prose descriptions, code snippets, data tables (without layout keywords)." >&2
    echo "" >&2
    echo "  The brainstorming skill's 'decide per question — terminal for conceptual, browser for visual' escape DOES NOT apply to layout/position/component-placement questions. Those are ALWAYS visual." >&2
    echo "  See ask-before-assuming.md pre-answered table (visual companion row)." >&2
    add_hard "ASCII-art / box-drawing UI layout mockup for layout question — start visual companion, render mockups in browser"
fi

# Check for tester-handoff prose (HARD block per autonomous-verification.md).
# The user is NEVER the agent's tester. Hand-off phrases shift verification from agent's
# tools (Playwright / curl / SSH / MCP) to the user's eyes/clicks — banned.
# Escape: if the message contains "UNVERIFIED:" explicitly stating WHAT cannot be tested
# and WHY (true user-only access), allow it — that is the documented exception.
if msg_has "$MSG_MENTION" -qiE "(can|could|would) you (please )?(test|verify|confirm|try|click|reproduce|reload|refresh)( it| this| that| the| in| on)|please (test|verify|confirm|reproduce|try it|try this|click it|click this|reload it|reload this|refresh it|refresh this)|let me know (if|when|whether)[^.]{0,80}(works|breaks|fails|shows|renders|appears|crashes|errors|loads|is correct|is right|you see)|(tell|show) me what you see|ping me (when|if|once|after)|report back (when|if|what|with|after)|\bnext user test\b|us(ed|ing) you as( a| the| my)? tester|act(ing|s)? as( a| the| my)? tester|(test|verify|try|run|click|check|reproduce|exercise) (it|this|the [a-z]+) on your end|on your end[,. ]+(test|verify|check|click|try|run|please|and let me know)|on your end and let me know|in your (browser|terminal|environment|local|machine)[, ]+(test|verify|check|click|try|run)|you'?ll need to (test|verify|click|try|reproduce)|\bbefore (the |any )?next user test\b|stop using you as tester|going to simulate.*myself|fix locally before (next |the )?(user )?test"; then
    # Allow if explicitly marked UNVERIFIED with a reason (the documented
    # exception). It EXONERATES, so ask whether it is MISSING — an
    # unsubstantiated exemption must never disarm an established violation.
    if msg_missing "$MSG" -qE "UNVERIFIED:"; then
        echo "VIOLATION: You handed verification to the user ('please test', 'let me know if it works', 'ping me when', 'tell me what you see', 'on your end', 'next user test', 'using you as tester', etc.). The user is NEVER your tester. You have Playwright, curl, SSH, MCP tools, your own test harness — use them. A blocker (MCP auth failure, timeout, 500 error, opaque reference ID) is YOUR work to debug, not a hand-off trigger." >&2
        echo "" >&2
        echo "  Decision tree:" >&2
        echo "    1. Can you debug with existing tools? → DEBUG IT YOURSELF, do not mention the blocker to user" >&2
        echo "       (read full error, search root cause, build local repro, fix locally, verify locally)" >&2
        echo "    2. Do you LACK a tool/access/credential to verify? → ASK FOR THE TOOL, not the test:" >&2
        echo "       • 'Install Playwright MCP / Chrome DevTools MCP so I can drive the browser myself'" >&2
        echo "       • 'Restart MCP server <name> on host <X>, or share new host/port'" >&2
        echo "       • 'Share session cookie / bearer token / webhook URL so I can call <service> myself'" >&2
        echo "       • 'Open SSH tunnel / set <ENV_VAR> so I can reach <resource>'" >&2
        echo "       • 'Install BrowserStack/Sauce MCP for iOS Safari rendering'" >&2
        echo "       • 'Set up win-mcp on the Windows host so I can drive the desktop session'" >&2
        echo "       The user provides the TOOL — YOU run the test." >&2
        echo "    3. Is the blocker GENUINELY user-only (their personal account, their physical hardware)?" >&2
        echo "       → ONLY AFTER asking for tool and user confirms impossible:" >&2
        echo "         UNVERIFIED: <what cannot be tested> — <why no tool exists>" >&2
        echo "         (state user-only reason + that tool-request was attempted)" >&2
        echo "" >&2
        echo "  Banned shapes (still — even when blocker is real):" >&2
        echo "    • 'I can't reach X. Could you test it?' — WRONG. Ask for AUTH/MCP, not test." >&2
        echo "    • 'MCP is down. Want to verify manually?' — WRONG. Ask for MCP restart, not manual verify." >&2
        echo "    • 'Playwright not installed. Could you click through?' — WRONG. Ask for install." >&2
        echo "" >&2
        echo "  See autonomous-verification.md → 'Before giving up — ASK FOR THE TOOL, not the test'." >&2
        add_hard "Tester-handoff phrase (user-as-tester) — ask for the TOOL/ACCESS/MCP, not for the test. Or write 'UNVERIFIED:' after attempting tool-request."
    fi
fi

# Check for "say go / ready to proceed" prose questions
if msg_has "$MSG_NOGOAL" -qiE "say.?go|shall (i|we) proceed|if good.?say|ready when you are|ready for.?next|ready to execute"; then
    echo "VIOLATION: You asked the user to 'say go' or confirm proceed in prose. The plan is approved — chain directly to the next step without asking. See ask-before-assuming.md pre-answered table." >&2
fi

# Check for spec/plan/design review handoff prose, including
# "Does this design look right? If yes, I'll commit/write/spec ..."
# AND "dispatch via subagent now, or hold for your review of the plan"
if msg_has "$MSG_NOGOAL_MENTION" -qiE "review the (spec|plan|design|brainstorm|approach)|let me know.*(any )?changes?|before (i|we) hand.?off|before (handing|moving).?(off|on)|hand.?off to writing.?plans|any (changes?|edits?|tweaks?) before|(does|is) (this|the) (design|spec|plan|approach|architecture|interface|api|schema|model|structure|layout|flow) (look|seem|sound) (right|good|ok|fine|correct|reasonable)|if (yes|good|ok|approved),? .*(write|create|commit|push|save|file|spec|generate|hand.?off|proceed)|(approve|approved|sign.?off|sign off|green.?light) (this|the) (design|spec|plan|approach|architecture)|(dispatch|kick.?off|launch|start|begin|fire|trigger).*(subagent|implement|impl|task|work|run).*(now|immediately).*(or|vs).*(hold|wait|pause|review|stop|skim|check)|(hold|wait|pause).*(for|on).*(your|user) review|(go|proceed|now).*(or|vs).*review (first|the plan)|pre.implementation.*(pause|skim|review|check)|(skim|review).*(plan|spec).*before.*(dispatch|kick.?off|launch|implement)"; then
    echo "VIOLATION: You stopped to ask 'does this design look right?' / 'if yes I'll commit' / 'dispatch now or hold for review' / 'review the spec' / 'dispatch now or skim plan first'. These are all pre-answered — always proceed autonomously. The user approved the workflow when they invoked brainstorming/spec-writing. Rewrite this message: cut the question, commit / dispatch / chain to next step directly. See ask-before-assuming.md pre-answered table." >&2
    add_hard "Pre-answered prose question: spec/plan/design review handoff or pre-implementation pause"
fi

# Check for the SAME "dispatch subagents now, or hold for plan review"
# pause, stated in SLOVAK (#319). ask-before-assuming.md's own row for
# this shape ("Plan committed locally as <sha>. Dispatch all tasks via
# subagent-driven-development now, or hold for your review of the plan
# first?") stayed OUT of the pre-answered table on the reasoning that its
# Slovak rendering already blocks — but #316's own audit of that claim
# only ever tested a fixture that RETAINS the literal English loanword
# "subagent-driven-development"; a genuinely natural Slovak rendering,
# with the loanword replaced by ordinary Slovak words, was NOT blocked by
# ANY hook (#319). Unlike #316's spec/plan-approval detector below, this
# shape has NO legitimate fork to protect (the row's own guidance: "the
# review-first branch is banned for ALL plan sizes"), so no bullet-option
# exemption is needed — the check mirrors the SCOPE of the English clause
# it is a sibling of (immediately above), not #316's different-shaped
# exemption machinery. Four required tokens: a Slovak "start" verb near a
# "now" word (either order), "alebo"/"či", a Slovak "wait" verb, and a
# plán/kontrola/review word nearby the wait verb.
#
# #319-review CRITICAL (reproduced live, adversarial review): the first
# cut only matched the infinitive verb forms from the worker's OWN
# fixture ("spustiť"/"rozbehnúť"/"počkať") and the bare nominative
# "plán"/"kontrolu" — every 1st-person conjugation this repo's own model
# question (user-questions-slovak.md's "počkám") and every declined form
# of plán/kontrola ("plánu", "kontroly") fell straight through, which is
# #319's OWN bug class reproduced one level down (coverage proven only
# against a convenient fixture). Widened to also match: 1st-person
# dispatch verbs (spustím/rozbehnem/začnem), 1st-person wait verbs
# (počkám/počkáme/čakám/čakáme), and the genitive/instrumental/locative
# declensions of plán/kontrola a natural Slovak question actually
# produces ("kontrolu plánu", "schválenie plánu").
#
# #319-review MAJOR (reproduced live): grep is LINE-oriented (no `.`
# crosses a `\n`), so the MANDATED multi-line bullet-option question
# shape (user-questions-slovak.md's own template, hook-enforced by
# stop-check-question-quality.sh's Check 4) — where the two branches sit
# on SEPARATE lines — sailed through untouched even though this banned
# shape has NO legitimate fork at all. Flattened to a single line
# (newlines -> spaces) before matching, purely for this check's own
# bounded-window scan — a strict superset of the un-flattened match set,
# since nothing that matched before stops matching after a `\n` becomes
# one space.
#
# Proven against negative-control fixtures that each isolate exactly ONE
# required token (an unrelated "started tests, waiting for results"
# sentence with none of the tokens; a live-hardware-timing question with
# the now+dispatch+alebo+hold shape but no plán/kontrola word nearby; an
# already-legitimate design question; a genuine bulleted design fork with
# real consequences) — see issue #319 comment.
#
# Accepted residuals (#319-review, not chased — narrow-on-purpose): the
# "now" word "okamžite" (not in SK_NOW_RX); the "now" word omitted
# entirely; the two branches stated in reversed order ("počkať... alebo
# spustiť hneď..."); other dispatch-verb synonyms (púšťam/štartujem/
# dispatchnem) or hold-verb synonyms not in the declared list.
SK_DISPATCH_RX="(spust[ií][ťtm]|rozbehn[úu][ťt]|rozbehnem|za[čc]nem|zača[ťt])"
SK_NOW_RX="(hne[ďd]|teraz|ihne[ďd])"
SK_HOLD_RX="(poč(ka[ťtm]|kaj|k[áa]m(e)?)|čaka[ťtm]|čak[áa]m(e)?)"
SK_PLANWORD_RX="(pl[áa]n(u|om|e|y|ov)?|kontrol[uyae]|review)"
SK_DISPATCH_FLAT=$(tr '\n' ' ' <<<"$MSG_NOGOAL_MENTION") || SK_DISPATCH_FLAT="$MSG_NOGOAL_MENTION"
# #316-review CRITICAL (reproduced again here, live): `\b` immediately
# adjacent to a diacritic is itself locale-dependent under a bare
# C/POSIX locale (no LANG/LC_ALL set) — rewriting to plain alternation
# (already done above; no embedded bracket classes with diacritics) does
# NOT fix it by itself. Forcing LC_ALL=C.UTF-8 on just this one grep call
# is the verified fix (present on every managed box's glibc); a plain
# `VAR=val funcname` prefix scopes the override to this command only.
if LC_ALL=C.UTF-8 msg_has "$SK_DISPATCH_FLAT" -qiE \
    "(\b${SK_NOW_RX}\b.{0,40}\b${SK_DISPATCH_RX}\b|\b${SK_DISPATCH_RX}\b.{0,40}\b${SK_NOW_RX}\b).{0,60}\b(alebo|či)\b.{0,60}\b${SK_HOLD_RX}\b.{0,40}\b${SK_PLANWORD_RX}\b"; then
    echo "VIOLATION: Spýtal si sa po slovensky, či máš spustiť prácu/podúlohy hneď, alebo počkať na kontrolu plánu — presne trieda 'dispatch now or hold for review' z ask-before-assuming.md, len v jazyku ktorý anglický regex nezachytáva. Toto je PRE-ANSWERED: vetva 'počkať na review' je zakázaná pre plány akejkoľvek veľkosti. Ak chce používateľ prerušiť, urobí to sám. Prepíš správu: vynechaj otázku, spusti hneď. (Ak si túto vetu iba CITUJEŠ alebo VYSVETĽUJEŠ — napr. pri opise tohto pravidla — obal ju do úvodzoviek alebo spätných apostrofov, inak ju gate prečíta ako reálnu otázku.) See ask-before-assuming.md pre-answered table." >&2
    add_hard "Pre-answered Slovak prose question: dispatch-now-or-hold-for-review (spustiť/rozbehnúť/začať + hneď/teraz/ihneď + alebo/či + počkať + plán/kontrolu/review)"
fi

# Check for the SAME spec/plan-approval pause, stated in SLOVAK (#316).
# Every question this repo ships to a real user is written in Slovak
# (user-questions-slovak.md) — an English-only regex for this class is
# BLIND on every stream/away box, and montalu2 proved it live: "schvaľuješ
# zapísaný design spec?" sailed through this gate untouched and the user
# had to answer it himself. Scoped NARROWLY on purpose, four required
# structural signals:
#   1. a Slovak APPROVAL VERB attached to a design-artifact NOUN — the
#      exact verb/noun families the incident named, either word order —
#      found ON THE ❓ MARKER LINE ITSELF (#316-review finding: scanning
#      the whole message let an UNRELATED approval mention anywhere
#      combine with an UNRELATED real ❓ question elsewhere and false-
#      block it; the real incident's own verb+noun sat directly on the
#      marker line, so anchoring there closes the gap with no loss);
#   2. the ❓ NEEDS YOU / ❓ ASKED marker this repo's own question
#      convention always carries on a real question turn — QLINE_ALL
#      non-empty IS this signal now, since (1) already requires it;
#   3. no bullet-option lines present ANYWHERE in the message — the SAME
#      shape stop-check-question-quality.sh's own Check 4 mandates on a
#      genuine design fork ("Odrážky s možnosťami sú POVINNÉ"), including
#      its NUMBERED-list form (`1.`/`2.`) — the regex is copied from
#      Check 4 verbatim (#316-review) so the two gates cannot drift apart
#      again, exactly the drift that let a numbered fork false-block;
#   4. the user is NOT PRESENT — Check 4 itself disables ALL shape
#      enforcement for a present user (`stop-check-question-quality.sh`'s
#      own "PRESENT USER → no shape enforcement", the camera-box "Hruza"
#      incident, 2026-07-05); this check's own safety argument rests on
#      Check 4, so it must be scoped to the SAME population Check 4
#      actually enforces on (#316-review), or the guarantee is false for
#      half the population.
# A genuine design fork (choice between options, real consequences) is
# exempt by construction even if it happens to use the SAME verb ("Ktorý
# návrh schvaľuješ — A alebo B?" with real `• `/`1.` option lines passes;
# a bare "schvaľuješ zapísaný spec?" gate with no options does not). The
# banned shape is specifically "approve my already-written artifact
# before I continue", never a real fork.
#
# Accepted residuals (#316-review, not chased — narrow-on-purpose):
# verb/noun on DIFFERENT lines (grep is line-oriented, `.{0,40}` cannot
# span a newline); a gap over 40 chars; "súhlasíš s návrhom?" (a genuinely
# common phrasing, no verb in the declared list); noun-less phrasings
# ("čakám na tvoje schválenie", "potrebujem tvoje potvrdenie"); an
# unrelated bullet/numbered line ANYWHERE in the message granting the
# exemption to an otherwise-real violation (message-scoped on purpose,
# matching NO_COMPANION_URL's own established scope above).
SK_APPROVAL_RX="\b(schva[ľl]uje[sš]|schv[áa]li[sš]|ods[úu]hlas[íi][sš]|potvrd[íi][sš]|odobr[íi][sš])\b"
SK_ARTIFACT_RX="\b([sš]pec(u|om|ifik[áa]ci[a-záäéíóôúýčďľňšťž]*)?|pl[áa]n(u|om)?|n[áa]vrh(u|om)?|dizajn(u|om)?|design)\b"
# EVERY ❓ marker line — MSG_MENTION so a mere quoted/backticked mention of
# the marker text is never read as a real question turn. A here-string, never
# a pipe (test_prose_gate_pipeline_race.py's own structural lock forbids
# feeding grep from a process at all, #190/#194) — `grep <pattern> <<<"$var"`
# has no writer PROCESS to race. #316-review MINOR: this used to keep only
# the LAST marker line (`tail -1`) before scanning it for verb+noun — a
# montalu2-shaped question BURIED under a later, unrelated ❓ line escaped
# detection entirely. `grep -E` (no `-z`) matches per LINE, never across a
# newline, so handing the WHOLE multi-line QLINE_ALL to msg_has below is
# exactly equivalent to "does ANY marker line contain verb+noun" with zero
# cross-line false-positive risk — no narrower `tail -1` selection needed.
_QLINE_RC=0
QLINE_ALL=$(grep -iE '❓[[:space:]]*\**[[:space:]]*(NEEDS[[:space:]]+YOU|ASKED)[[:space:]]*\**[[:space:]]*:' <<<"$MSG_MENTION") || _QLINE_RC=$?
if [ "$_QLINE_RC" -ge 2 ]; then
    # #316-review MINOR: this used to collapse a genuine grep ERROR into the
    # same "" as a real no-match, with no record_undet call at all — the
    # exact fabricated 141->0 verdict shape #194 removed, one level up (a
    # SELECTOR, not a pattern). Per #194's own taxonomy an unanswerable
    # SELECTOR must WIDEN the scope for the pattern that reads it, never
    # shrink to "absent": fall back to the whole message (still able to
    # fire) and assume a marker is present, rather than silently disarming
    # this check for every message from here on.
    record_undet "$_QLINE_RC" "marker-line grep (SK approval-pause, #316)"
    QLINE_ALL="$MSG_MENTION"
    HAS_QMARKER=1
else
    if [ -n "$QLINE_ALL" ]; then HAS_QMARKER=1; else HAS_QMARKER=0; fi
fi
# #316-review CRITICAL: SK_APPROVAL_RX/SK_ARTIFACT_RX embed diacritics inside
# bracket classes (`[ľl]`, `[sš]`, `[áa]`, `[íi]`, `[úu]`) with `\b` anchors
# immediately adjacent — both are locale-dependent under a bare C/POSIX
# locale (no LANG/LC_ALL set), the exact gotcha stop-check-question-
# quality.sh and notify-discord-pending.sh already document twice in this
# repo. Reproduced live: under LC_ALL=C every diacritic verb spelling MISSES
# (schvaľuješ/schváliš/odsúhlasíš/potvrdíš/odobríš all fail to match) and
# rewriting the classes as plain alternation does NOT fix it either — `\b`
# next to a multibyte character is itself locale-dependent, in both the
# miss AND the false-positive direction. Forcing a UTF-8 locale on just this
# ONE grep call is the actual, verified fix (C.UTF-8 is present on every
# managed box's glibc); a plain `VAR=val funcname` prefix exports the
# override for the whole function call, including the `grep` subprocess it
# execs, and scopes to ONLY this command (never leaking into the following
# `&& echo 1 || echo 0`).
HAS_SK_APPROVAL=$(LC_ALL=C.UTF-8 msg_has "$QLINE_ALL" -qiE "${SK_APPROVAL_RX}.{0,40}${SK_ARTIFACT_RX}|${SK_ARTIFACT_RX}.{0,40}${SK_APPROVAL_RX}" && echo 1 || echo 0)
# The exemption (real options offered) EXONERATES, so ask whether it is
# MISSING — an unanswerable check must not grant the exemption (same
# fail-closed shape as NO_COMPANION_URL above). MSG_MENTION, not raw MSG:
# a fenced code block (e.g. a quoted diff with `- old line`) must not
# grant the exemption just by containing a leading `- `.
NO_OPTION_BULLETS=$(msg_missing "$MSG_MENTION" -qE '^[[:space:]]*((•|-)[[:space:]]|[0-9]+[.)][[:space:]])' && echo 1 || echo 0)
# Presence gate, mirroring stop-check-question-quality.sh's own ACTIVE
# marker check verbatim (signal 4 above). RETRY_KEY is already validated
# safe-path-component-or-empty by the retry-throttle block further up —
# an empty/unsafe id simply means this check can never find a marker, so
# it degrades to "not present" (checked normally), never to a false skip.
IS_PRESENT=0
if [ -n "$RETRY_KEY" ]; then
    SK_ACTIVE_MARKER="/tmp/claude-user-active-${RETRY_KEY}"
    if [ -f "$SK_ACTIVE_MARKER" ]; then
        _SK_AM=$(stat -c %Y "$SK_ACTIVE_MARKER" 2>/dev/null || echo 0)
        _SK_NOW=$(date +%s 2>/dev/null || echo 0)
        case "$_SK_AM" in "" | *[!0123456789]*) _SK_AM=0 ;; esac
        case "$_SK_NOW" in "" | *[!0123456789]*) _SK_NOW=0 ;; esac
        if [ "$_SK_AM" -le "$_SK_NOW" ] && [ "$((_SK_NOW - _SK_AM))" -lt 600 ]; then
            IS_PRESENT=1
        fi
    fi
fi
if [ "$HAS_SK_APPROVAL" = "1" ] && [ "$HAS_QMARKER" = "1" ] && [ "$NO_OPTION_BULLETS" = "1" ] && [ "$IS_PRESENT" = "0" ]; then
    echo "VIOLATION: Spýtal si sa po slovensky 'schvaľuješ zapísaný spec/plán/návrh?' — presne trieda 'spec/plan/design review handoff' z ask-before-assuming.md, len v jazyku ktorý anglický regex nezachytáva. Toto je PRE-ANSWERED: napíš rozhodnutie na ticket ('spec committed, pokračujem') a pokračuj na implementačný plán, nečakaj na schválenie. Genuine dizajnová rozvetvená otázka (voľba medzi možnosťami s reálnymi dôsledkami, s odrážkami • alebo číslovaním 1./2. pre každú možnosť) je vítaná a nie je toto — ale 'schváľ mi hotový spec, inak nepokračujem' bez ponúknutých alternatív áno. Prepíš správu: vynechaj otázku, zapíš rozhodnutie a pokračuj. See ask-before-assuming.md pre-answered table." >&2
    add_hard "Pre-answered Slovak prose question: spec/plan/design approval pause (schvaľuješ/schváliš/odsúhlasíš/potvrdíš/odobríš + spec/plán/návrh/dizajn)"
fi

# === Unified completion-report detection ===
# Agents sometimes write prose completion reports without the canonical heading,
# silently bypassing every audit check below (slovnormal-mcp session shipped
# a report with NO heading → all audits skipped → user saw missing /requesting-code-review,
# missing 🌐, missing /plan-check). Fix: detect completion-report INTENT via signals
# even when the heading is absent, then force the agent to use the full template.
#
# Signal-based detection (any-of, combined with PR URL present):
#   - "awaiting merge" / "awaiting your merge" / "awaiting merge it"
#   - "mergeable, clean" / "mergeable=MERGEABLE" / "mergeStateStatus=CLEAN"
#   - "all N/N checks green" / "all checks green"
#   - "ready to merge"
#   - "Plan steps (N/N done)"
#   - "Work Complete" anywhere in message (catches lowercase / no heading prefix)
#   - Both **Goal:** AND **What changed:** present (clear completion-report markers in prose form)
IS_COMPLETION_HEADING=$(msg_has "$MSG" -qE "^## ✅ Work Complete|^✅ Work Complete" && echo 1 || echo 0)
HAS_PR_URL=$(msg_has "$MSG" -qE "https?://github\.com/[^[:space:]]+/pull/[0-9]+" && echo 1 || echo 0)
HAS_COMPLETION_PHRASE=$(msg_has "$MSG" -qiE "awaiting[^.]{0,40}(merge|your merge|merge it)|mergeable[, ]+clean|all [0-9]+/[0-9]+ checks (are )?(green|passing)|all checks (are )?green|mergeStateStatus=CLEAN|mergeable=MERGEABLE|ready to merge|Plan steps \([0-9]+/[0-9]+ done\)|✅ Work Complete|work complete[: ]|per pr-merge-policy|merged (to|into) (main|master)|auto-?merged" && echo 1 || echo 0)
HAS_GOAL_AND_OUTCOME=0
if msg_has "$MSG" -qiE "\*\*Goal:?\*\*|^Goal:" && msg_has "$MSG" -qiE "\*\*What changed:?\*\*|\*\*Outcome:?\*\*|^What changed:|^Outcome:"; then
    HAS_GOAL_AND_OUTCOME=1
fi

IS_COMPLETION_SIGNAL=0
if [ "$HAS_PR_URL" = "1" ] && { [ "$HAS_COMPLETION_PHRASE" = "1" ] || [ "$HAS_GOAL_AND_OUTCOME" = "1" ]; }; then
    IS_COMPLETION_SIGNAL=1
fi

# PR-LESS ticket completion (the david@gk blind spot, 2026-07-11): a fork-no-merge /
# hand-off stream NEVER produces a PR URL, so the PR-anchored route above never fired
# there and bare '✅ DONE: #1400 a #1408 hotové' one-liners sailed through — the user
# never saw a proper Work Complete report on that box. A ✅ DONE marker line that
# names ticket(s) #N together with done-vocab (SK/EN), or is paired with a
# READY-FOR-REVIEW hand-off, IS a ticket completion — same template obligations.
# (A conversational '✅ DONE: odpovedané na otázku o #123' has no done-vocab → clean.)
DONE_LINE=""
if _DONE_LINES=$(msg_lines "$MSG" -E "^✅ DONE:"); then
    DONE_LINE=$(tail -1 <<<"$_DONE_LINES")
fi
if [ "$IS_COMPLETION_SIGNAL" = "0" ] && [ -n "$DONE_LINE" ]; then
    if msg_has "$DONE_LINE" -qE "#[0-9]+" \
       && msg_has "$DONE_LINE" -qiE "hotov|opraven|zavret|uzavret|vyrie[sš]en|dokon[cč]en|implementovan|nasaden|zmerg|zl[uú][cč]en|odovzdan|merged|deployed|fixed|closed|resolved|implemented|shipped|handed.?off"; then
        IS_COMPLETION_SIGNAL=1
    elif msg_has "$MSG" -qiE "READY-FOR-REVIEW|odovzdan[eéáý]?[^.]{0,40}review|ready for (the )?(gatekeeper|maintainer) review"; then
        IS_COMPLETION_SIGNAL=1
    fi
fi

# A message that ends still-working (⏳ marker) is not a completion report — the signal
# route must not force the template mid-loop (e.g. fleet merged #N, dispatching the next).
# The marker EXONERATES (it switches the audits OFF), so the question asked is
# whether it is MISSING: an unanswerable check must not skip the audit block.
if [ "$IS_COMPLETION_SIGNAL" = "1" ] && ! msg_missing "$MSG" -q "⏳"; then
    IS_COMPLETION_SIGNAL=0
fi

IS_COMPLETION=0
if [ "$IS_COMPLETION_HEADING" = "1" ] || [ "$IS_COMPLETION_SIGNAL" = "1" ]; then
    IS_COMPLETION=1
fi

# HARD: completion-report intent detected but canonical heading missing.
# This is the slovnormal-mcp failure mode — prose report bypassed all audits.
if [ "$IS_COMPLETION_SIGNAL" = "1" ] && [ "$IS_COMPLETION_HEADING" = "0" ]; then
    echo "VIOLATION: Your message is a completion report (PR URL + completion-signal phrase or Goal/What changed prose) but does NOT start with the canonical heading '## ✅ Work Complete'. completion-report.md MANDATES the FULL template every time — heading + audits block + --- separator + Goal + What changed + 🌐 URLs + PR title/URL. Prose substitutes ('PR clean. 8/8 checks green. mergeable=MERGEABLE...') are BANNED — they bypass the audit gates the user relies on (per slovnormal-mcp PR #9 incident: missing /requesting-code-review, missing 🌐 URLs, missing /plan-check all slipped through because no heading was present)." >&2
    echo "" >&2
    echo "  Rewrite the message NOW using the EXACT template:" >&2
    echo "" >&2
    echo "    ## ✅ Work Complete" >&2
    echo "" >&2
    echo "    **Audits & deploy:**" >&2
    echo "    ✅ CI: green" >&2
    echo "    ✅ /plan-check: N/N fulfilled" >&2
    echo "    ✅ /review: clean — 0 🔴 0 🟡 0 🔵" >&2
    echo "    ✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵" >&2
    echo "    ✅ Deploy: <verified behavior on live target>   (omit if no deploy)" >&2
    echo "    ✅ Regression test: <path>:<line> — RED <sha>, GREEN <sha>   (bug-fix only)" >&2
    echo "" >&2
    echo "    ---" >&2
    echo "" >&2
    echo "    **Goal:** <user's ask in plain language>" >&2
    echo "    **What changed:** <user-visible outcome, 1-2 sentences>" >&2
    echo "" >&2
    echo "    🌐 Dev:  <url>" >&2
    echo "    🌐 Prod: <url>" >&2
    echo "" >&2
    echo "    **[<project>] PR #N: <full title>**" >&2
    echo "    <full PR URL> — merged <sha> (default-auto) / mergeable, clean (manual-marker)" >&2
    echo "" >&2
    echo "  FORK-NO-MERGE / hand-off stream (no PR/merge/deploy exists): keep the heading +" >&2
    echo "  audits + Goal/What changed, and replace the Deploy/🌐/PR lines with the hand-off:" >&2
    echo "    ✅ Lokálne overenie: <tests+lint result on the fork branch>" >&2
    echo "    ✅ Hand-off: READY-FOR-REVIEW komentár na #N (<topic>) + --handoff karta" >&2
    echo "" >&2
    echo "  See completion-report.md → 'MANDATORY structure (use this EXACT template)'." >&2
    add_hard "Prose completion report missing canonical '## ✅ Work Complete' heading — use the full template, not a prose summary"
fi

# Check completion report has Goal + What changed + plan-check + /review lines
if [ "$IS_COMPLETION" = "1" ]; then
    HAS_GOAL=$(msg_has "$MSG" -qiE "\*\*Goal:?\*\*|^Goal:" && echo 1 || echo 0)
    HAS_OUTCOME=$(msg_has "$MSG" -qiE "\*\*What changed:?\*\*|\*\*Outcome:?\*\*|^What changed:|^Outcome:" && echo 1 || echo 0)
    HAS_PLAN_CHECK=$(msg_has "$MSG" -qiE "/plan.?check|plan-check.*(fulfilled|passed|clean|complete)|✅.*plan.?check" && echo 1 || echo 0)
    # /review audit must include all THREE counters (🔴 🟡 🔵) — no skipping minor findings.
    # Accept either explicit "0 🔴 0 🟡 0 🔵" or "all findings addressed" with 🔵 mentioned.
    # MUST disambiguate from /requesting-code-review which contains "/review" as substring.
    # Use perl negative lookbehind: /review preceded by NOT "code-" (rules out requesting-code-review).
    #
    # #194 — the gaps are BOUNDED (`.{0,300}`), not `.*`. An unbounded `.*A.*B.*C`
    # explores a split-point space quadratic in the number of candidate `A`s, and
    # message text alone could drive it past PCRE's backtracking limit: measured on
    # GNU grep 3.11, a `/review: ` line carrying ~5000 repeats of `0 🔴` (30 KB)
    # exits 2 in 0.11 s, while the same size of neutral filler answers cleanly.
    # That removed the TRIGGER as well as the amplifier. The bound is also the
    # tighter reading of the rule: an audit line is a short fixed template, and an
    # unbounded gap would happily match `/review:` at the head of a 300 KB line and
    # its counters at the tail.
    #
    # The bound is sized by what REAL reports look like, never by speed: with
    # grep's required-literal pre-filter defeated (decoys placed before the
    # anchor), every candidate from 120 to 1000 answers a 1 MB adversarial line
    # in under 0.35 s, while the unbounded form errors at 30 KB. 120 was too
    # tight and rejected two ordinary shapes outright — a verbose standards
    # sentence (gap 169) and a found-and-fixed summary (gap 161) — which turned
    # the bound into a new way to report a line that is PRESENT as MISSING, an
    # accusation the agent cannot act on because nothing names length. 300
    # clears the longest real shape measured by ~1.8x. Note the twin HAS_RCR
    # probe below is unbounded ERE (a DFA — no backtracking limit to hit), so a
    # bound tight enough to reject a /review line passes its identical twin,
    # which makes the resulting block undiagnosable from its own reason.
    HAS_REVIEW=$(msg_has "$MSG" -qP '(?<!code-)/review[: ].{0,300}0 🔴.{0,300}0 🟡.{0,300}0 🔵|(?<!code-)/review[: ].{0,300}all (findings|issues|items).{0,300}addressed|(?<!code-)/review[: ].{0,300}addressed in commit' && echo 1 || echo 0)
    # requesting-code-review (superpowers skill, deep pass) — must also pass clean.
    # Distinguish from /review by requiring the literal token "requesting-code-review" or "request.*code.?review" or "superpowers:requesting".
    HAS_RCR=$(msg_has "$MSG" -qiE "requesting.?code.?review.*0 🔴.*0 🟡.*0 🔵|requesting.?code.?review.*all (findings|issues|items).*addressed|requesting.?code.?review.*addressed in commit|✅.*requesting.?code.?review.*0 🔴.*0 🟡.*0 🔵|✅.*superpowers:requesting.*0 🔴.*0 🟡.*0 🔵|✅.*request.?code.?review.*0 🔴.*0 🟡.*0 🔵|✅.*code.?review.*\(deep\).*0 🔴.*0 🟡.*0 🔵" && echo 1 || echo 0)
    if [ "$HAS_GOAL" = "0" ] || [ "$HAS_OUTCOME" = "0" ] || [ "$HAS_PLAN_CHECK" = "0" ] || [ "$HAS_REVIEW" = "0" ] || [ "$HAS_RCR" = "0" ]; then
        echo "VIOLATION: Work Complete report is missing required lines. completion-report.md MANDATES this structure (audits at TOP, Goal/What changed/PR URL at BOTTOM — terminal scrolls, last lines are what the user sees):" >&2
        [ "$HAS_GOAL" = "0" ] && { echo "  - MISSING: '**Goal:** <1 sentence restating the user's ask in plain language>' — placed at the bottom, after audits." >&2; add_hard "Missing **Goal:** line"; }
        [ "$HAS_OUTCOME" = "0" ] && { echo "  - MISSING: '**What changed:** <1-2 sentences in user-visible language>' — placed at the bottom, after audits." >&2; add_hard "Missing **What changed:** line"; }
        [ "$HAS_PLAN_CHECK" = "0" ] && { echo "  - MISSING: '✅ /plan-check: N/N fulfilled' — invoke the plan-check skill, fix any NOT DONE items, then add the line." >&2; add_hard "Missing ✅ /plan-check audit line"; }
        [ "$HAS_REVIEW" = "0" ] && { echo "  - MISSING: '✅ /review: clean — 0 🔴 0 🟡 0 🔵 (or addressed in commit <sha>)' — apply /review standards (Correctness/Security/Performance/Maintainability/Style), fix every 🔴 critical, 🟡 warning, AND 🔵 suggestion inside the diff. The 🔵 counter is required — '0 🔴 0 🟡' alone is incomplete (no skipping minor findings). Then add the line." >&2; add_hard "Missing ✅ /review audit line with 0 🔴 0 🟡 0 🔵"; }
        [ "$HAS_RCR" = "0" ] && { echo "  - MISSING: '✅ /requesting-code-review: clean — 0 🔴 0 🟡 0 🔵 (or addressed in commit <sha>)' — invoke the superpowers:requesting-code-review skill (the DEEP pass), fix every 🔴/🟡/🔵 it surfaces, then add the line. The user ALWAYS runs this after the completion report and it catches issues that /review misses — skipping = guaranteed rework. Both /review AND /requesting-code-review are required." >&2; add_hard "Missing ✅ /requesting-code-review audit line with 0 🔴 0 🟡 0 🔵"; }
        echo "See completion-report.md for the exact template." >&2
    fi

    # Bug-fix PRs MUST include a regression-test evidence line.
    # Triggered when the report mentions: Closes/Fixes/Resolves #N, or fix:/bug:/regression: in title,
    # or PR title starts with "fix" / contains "bugfix" / "hotfix" / "patch".
    IS_BUGFIX_REPORT=0
    if msg_has "$MSG" -qiE '(closes|fixes|resolves)\s+#[0-9]+'; then IS_BUGFIX_REPORT=1; fi
    if msg_has "$MSG" -qiE 'PR.*:.*\b(fix|bugfix|hotfix|patch|regression|repair)\b|^(fix|bugfix|hotfix|patch|regression):'; then IS_BUGFIX_REPORT=1; fi
    if msg_has "$MSG" -qiE '\b(bug fix|bug-fix|regression fix|fixed (the )?(bug|regression|issue|defect))\b'; then IS_BUGFIX_REPORT=1; fi

    if [ "$IS_BUGFIX_REPORT" = "1" ]; then
        # Required line format examples:
        #   ✅ Regression test: tests/foo_test.rs:42 — RED on a1b2c3d, GREEN on e4f5g6h
        #   ✅ Regression test: e2e/login.spec.ts:15 — failed before fix (a1b2c3d), passes after fix (e4f5g6h)
        HAS_REGRESSION=$(msg_has "$MSG" -qiE '✅\s*regression test:.*[a-f0-9]{7}' && echo 1 || echo 0)
        if [ "$HAS_REGRESSION" = "0" ]; then
            echo "VIOLATION: Bug-fix completion report missing the '✅ Regression test:' evidence line. Per regression-test-first.md, every bug fix needs a test commit BEFORE the fix commit, and the report must cite both SHAs:" >&2
            echo "  Required line:" >&2
            echo "    ✅ Regression test: <test_path>:<line> — RED on <test_sha>, GREEN on <fix_sha>" >&2
            echo "  Or:" >&2
            echo "    ✅ Regression test: <test_path>:<line> — failed before fix (<test_sha>), passes after fix (<fix_sha>)" >&2
            echo "  See regression-test-first.md and completion-report.md." >&2
            add_hard "Missing ✅ Regression test: <path>:<line> — RED <sha>, GREEN <sha> line on bug-fix PR"
        fi
    fi

    # Check ORDER: Goal/What changed must appear AFTER audit lines (audits at top, Goal at bottom)
    # Routed through msg_line_no so an errored lookup is RECORDED rather than
    # reading as "no Goal line" (#194 companion 5). An unknown line number stays
    # empty and the ORDER check below simply does not fire — this warning is
    # SOFT, and manufacturing an ordering claim from a number the hook never
    # obtained is the one thing it must not do.
    GOAL_LINE=$(msg_line_no "$MSG" -E "\*\*Goal:?\*\*")
    AUDIT_LINE=$(msg_line_no "$MSG" -E "✅.*(/plan.?check|review.*clean|review.*0 🔴)")
    if [ -n "$GOAL_LINE" ] && [ -n "$AUDIT_LINE" ] && [ "$GOAL_LINE" -lt "$AUDIT_LINE" ]; then
        echo "VIOLATION: 'Goal' line appears BEFORE the audit lines. Wrong order. The terminal scrolls — the user only sees the LAST visible passage without scrolling back. Put audits/CI/plan-check/review at the TOP, then a '---' separator, then Goal + What changed + PR URL + ❓Question at the BOTTOM. See completion-report.md → 'Why this order'." >&2
    fi

    # Check trailing question is clearly marked with ❓
    LAST_CHAR=$(echo "$MSG" | tr -d '[:space:]' | tail -c 1)
    # The ❓ marker EXONERATES a trailing "?", so ask whether it is MISSING.
    if [ "$LAST_CHAR" = "?" ] && msg_missing "$MSG" -qE "❓"; then
        echo "VIOLATION: Your message ends with '?' but no ❓ marker is present. Questions must be clearly marked so the user spots them in the terminal scroll — they can't tell a question from a status line at a glance. Use '❓ **Question:** <concise 1-2 sentence question>' as the very last line. If it isn't actually a question for the user, rephrase as a statement. See completion-report.md → 'Pending question'." >&2
    fi
fi

# Check bare PR/issue numbers without titles — ALL messages (not just completion reports).
# Per issue-reference-context.md: the user manages many projects, does NOT keep tickets
# open, and cannot decode a bare '#N' by number. EVERY reference the user reads — status
# updates, milestone pings, mid-work narration, "filed as", "closes", plan steps,
# completion reports — must carry the title/topic next to the number.
# Soft warning (stderr, not a hard block): the rule does the enforcing; this catches slips
# without trapping the agent on edge cases (e.g. a 'Closes #N' inside a commit-message block,
# which is exempt git syntax).
# Right: 'PR #54: <title>' / '#42 (karaoke sanitizer)' / 'Closes #234 (driver.rs cap)'.
BARE_REF=0
# "issue|PR|pull request|pull #N" NOT immediately followed by ':' or ' (' (a title/topic).
if msg_has "$MSG" -qPi "\b(issue|PR|pull request|pull) #[0-9]+(?! *[:(])(?![0-9])"; then BARE_REF=1; fi
# action-verb "#N" (closes/fixes/resolves/filed/tracked/see/blocked by/depends on/addressed in)
# NOT followed by ' (' (a parenthetical topic).
if msg_has "$MSG" -qPi "\b(closes|fixes|resolves|fixed|filed as|filed:|tracked as|tracking|see|blocked by|depends on|address(ed)? in) #[0-9]+(?! *\()(?![0-9])"; then BARE_REF=1; fi
if [ "$BARE_REF" = "1" ]; then
    echo "VIOLATION (soft): Bare issue/PR number without its title/topic. The user does NOT keep tickets open and cannot decode '#N' by number — this applies to EVERY message, not just completion reports." >&2
    echo "  - WRONG: 'PR #54 — mergeable, clean' / 'Fixes #234' / 'Working on #42' / 'See #91'" >&2
    echo "  - RIGHT: 'PR #54: Refactor driver.rs and add lyrics test' / 'Fixes #234 (driver.rs over 1000-line cap)' / 'Working on #42 (karaoke sanitizer)' / 'See #91 (NDI rebind)'" >&2
    echo "Add the title/topic next to the number — copy it from 'gh issue view N' / 'gh pr view N'. Commit-message 'Closes #N' is exempt (git syntax). See issue-reference-context.md." >&2
fi

# Check for follow-up issue filings in completion reports.
# Per complete-planned-work.md "Follow-up gate", same-PR small cleanups (enum migration,
# type tightening, magic-number extraction, <100 LoC same-file polish) MUST land in the
# current PR — NOT in a follow-up issue. Follow-ups are reserved for genuinely
# out-of-scope work that fails the bundling gate (>300 LoC, schema change, API break,
# security boundary, cross-cut refactor).
if [ "$IS_COMPLETION" = "1" ]; then
    if msg_has "$MSG" -qiE "follow.?up (filed|issue|tracked|created|opened|logged)[:= ]+#[0-9]+|filed (as|under) #[0-9]+ for (next|follow.?up|separate|dedicated)|tracked (in|as) #[0-9]+ (as|for) (separate|follow.?up|next|dedicated)|(will|to) address.*(in (a )?(next|follow.?up|dedicated|separate) pr|in (the )?next session)|(opened|created) #[0-9]+ (for|to track) (the )?(follow.?up|cleanup|tidy|polish|migration|refactor|migrate)"; then
        echo "VIOLATION: You filed a follow-up issue from a completion report. Per complete-planned-work.md 'Follow-up gate', same-PR small cleanups (<100 LoC, same-file polish, enum migration, type tightening, magic-number extraction, missing test on touched path) MUST land in the CURRENT PR — not a follow-up. Follow-ups are reserved for work that FAILS the bundling gate (>300 LoC, DB schema change, API break, security boundary, cross-cut refactor). If the discovered task does NOT meet one of those criteria, close the follow-up issue and add a commit to THIS PR. See complete-planned-work.md → 'Follow-up gate' and ask-before-assuming.md pre-answered table." >&2
    fi
fi

# Check for "ghost deferral" — completion report mentions deferred work but no #N issue reference.
# Per complete-planned-work.md, ANY deferral phrase in a completion report MUST cite a filed issue
# number. Without #N, the deferred work is permanently lost.
if [ "$IS_COMPLETION" = "1" ]; then
    # Detect deferral phrases (broad — many rewordings)
    DEFER_HIT=0
    if msg_has "$MSG_MENTION" -qiE "\b(is |has been |will be |to be )?deferred\b|\bdefer(ring|ral)\b|root.?cause (fix|repair) (is )?(later|deferred|for later|not yet|in follow.?up|next pr|next session)|(actual|real) (fix|root.?cause) (is )?(later|deferred|coming|in follow.?up|for follow.?up|next pr|next session|not yet)|(will|to) be addressed (in (the )?(next pr|next session|follow.?up|dedicated pr|future))|(remains|still) (outstanding|unresolved|pending|to.?be.?done|to.?fix)|this pr (does ?n'?t|doesn'?t|will not|won.?t) (fix|address|resolve|close|complete) (the |that )?(root.?cause|actual|underlying|real)|(not yet|won.?t be) fix(ed|ing) (in|until) (this pr|next session|follow.?up)|patch(ed)? around|workaround for now|temporary (fix|patch|band.?aid)|placeholder until|stub until|leave[sd]? broken|stays broken|known (issue|broken)|moves? to a (next|future|separate|dedicated) pr|punt(ed|ing)? (to|until|for)"; then
        DEFER_HIT=1
    fi
    if [ "$DEFER_HIT" = "1" ]; then
        # Require an EXPLICIT tracking-issue reference. A bare PR title with #N does NOT count
        # (PR #195 in the title doesn't prove the deferred work was filed). Require:
        #   "Filed as #N" / "Filed: #N" / "Tracked as #N" / "Tracking issue #N" /
        #   "Issue #N" / "Tracker: #N" / "TODO #N" / "filed under #N" / etc.
        # A tracking reference EXONERATES the deferral, so ask whether it is
        # MISSING — an unanswerable check must not grant that exemption.
        if msg_missing "$MSG" -qiE "\b(filed|tracked|tracking|tracker|opened|created|logged|recorded)\b[^.]{0,60}#[0-9]+|issue\s+#[0-9]+\b|todo[: ]+#[0-9]+|see\s+#[0-9]+|follow.?up\s+(in|at|as)\s+#[0-9]+|deferred[^.]{0,60}#[0-9]+|root.?cause[^.]{0,60}#[0-9]+|address(ed)?\s+(in|by|via)\s+#[0-9]+"; then
            echo "VIOLATION: Completion report contains a deferral phrase ('deferred', 'root-cause fix later', 'will be addressed in follow-up', 'remains outstanding', 'workaround for now', 'patched around', 'this PR doesn't fix...', 'punted to...') but NO EXPLICIT tracking-issue reference. The current PR's own #N in the title does NOT count — the user needs proof the DEFERRED work was filed as its own tracked issue." >&2
            echo "" >&2
            echo "  Per complete-planned-work.md, any deferred work MUST be filed as a tracked GitHub issue BEFORE sending the completion report, and the report MUST cite it explicitly:" >&2
            echo "    • 'Filed as #<N>: <title>'" >&2
            echo "    • 'Tracked as #<N>'" >&2
            echo "    • 'Root-cause fix tracked in #<N>'" >&2
            echo "    • 'Address in #<N>'" >&2
            echo "" >&2
            echo "  Without that, the deferred work is permanently lost (the ghost-deferral failure mode)." >&2
            echo "" >&2
            echo "  Fix NOW:" >&2
            echo "    1. gh issue create --title 'TODO: <description of deferred work>' --body '<context>'" >&2
            echo "    2. Add a line to the completion report: 'Filed as #<returned-N>: <title>'" >&2
            echo "" >&2
            echo "  See complete-planned-work.md → 'CRITICAL — deferral phrases MUST cite the issue number'." >&2
            add_hard "Deferral phrase in completion report without explicit 'Filed as #N' / 'Tracked as #N' reference"
        fi
    fi
fi

# Check for "skip 🔵 review findings" / "🔵 deferred / out of scope / minor" patterns.
# The user wants every review finding fixed inside the diff — no skipping minor issues.
if msg_has "$MSG" -qiE "🔵.*(defer|skip|out of scope|not address|leave (it|them|for|to)|next (session|pr|commit)|not blocking|low.priority|nice.?to.?have|stylistic|cosmetic|address later|address next)|(defer|skip|leave|ignore).*🔵|out of scope.*(suggestion|🔵|stylistic|nit|nice.?to.?have|minor finding)|(suggestions?|minor findings?|🔵 findings?).*(defer|skip|out of scope|leave|next session|next pr|won.?t address|will not address|not addressing|can wait|low.priority|address later|address next)|(won.?t|will not|not) address(ing)?.*(suggestion|🔵|minor finding)"; then
    echo "VIOLATION: You're skipping or deferring 🔵 (suggestion) review findings. The user wants the highest-quality code possible — fix EVERY review finding inside this PR's diff, including 🔵. Phrases like '🔵 deferred', '🔵 out of scope', '🔵 minor — leaving them', '🔵 stylistic — skip', '🔵 nice-to-have — defer', or 'won't address the suggestions' are banned. The ONLY allowed exception is a 🔵 finding that points at code OUTSIDE the diff — for that, file a GitHub issue with a title and reference it. NEVER silently skip a 🔵 inside the diff. See completion-report.md → 'Pre-completion gate'." >&2
fi

# Check for quality-bypass shortcut menus or "your call" delegation.
#
# Split by AMBIGUITY (#92 item 4). Until then the whole family only printed to
# stderr, which a non-blocking Stop hook never feeds back to the model — so the
# module's "HARD-blocked at Stop" claim was false and nothing was corrected.
#
# HARD — each shape names a merge/gate bypass explicitly; no innocent reading.
if msg_has "$MSG_MENTION" -qiE "admin.?merge|merge --admin|--admin.*merge|bypass.*(branch.?protection|gate)|merge.*despite|merge.*broken.*(code|ci)|close.*pr.*roll.*into|roll.*into.*next.*pr|stop.*runner.*(to|so).*merge|realistic options.*[12]\.|investigate.*(or|vs).*merge|merge.*(or|vs).*investigate|functionally ready|essentially (clean|ready|mergeable)|good enough to merge|won.?t claim.*clean|UNSTABLE.*merge|merge.*UNSTABLE|informational (check|failure).*(merge|skip|ignore)|advisory only.*(merge|skip|ignore)|project precedent.*merg|previous pr.*merged.*same"; then
    echo "VIOLATION: You offered a quality-bypass shortcut (admin-merge / bypass branch protection / close PR and roll into the next one / 'merge despite' / 'functionally ready' / 'good enough to merge' / 'UNSTABLE but merge anyway' / 'informational check, merge it' / 'project precedent'). These are NEVER options. A failing gate or UNSTABLE state = fix the root cause, autonomously. Hours of overnight agentic work require autonomous decisions. The user wants the harder, correct path EVERY time — never the cheaper/quicker shortcut. See autonomous-quality-discipline.md, pr-merge-policy.md, ask-before-assuming.md." >&2
    add_hard "Quality-bypass shortcut offered (admin-merge / merge despite / functionally ready / UNSTABLE-but-merge / informational-check dismissal) — fix the gate instead"
fi

# Check for the SAME "merge despite the failing check" / quality-bypass
# shortcut, stated in SLOVAK (#319). Two of ask-before-assuming.md's rows
# for this shape ("Realistic options: admin-merge / close PR / stop
# runner" and "Should I merge despite the failing check? / admin-merge?")
# stayed OUT of the pre-answered table on the reasoning that their Slovak
# rendering already blocks — but #316's own audit of that claim only ever
# tested a fixture that RETAINS the literal English loanword
# "admin-merge"; a genuinely natural Slovak rendering, with the loanword
# replaced by ordinary Slovak words, was NOT blocked by ANY hook (#319).
# One required pair: a Slovak "merge" verb near a Slovak "despite" word,
# either order — narrow and high-signal on purpose (the SAME rigor level
# as the English sibling immediately above, whose own "merge.*despite"
# alternative is an equally bare 2-word requirement with no PR/CI-context
# anchor — this is an accepted, already-shipped trade-off in this file,
# not a NEW one).
#
# #319-review CRITICAL (reproduced live, adversarial review): the first
# cut's merge-verb regex only matched the `-ova-` stem
# (zlúčiť/zmergovať/zmerguj) — the worker's OWN fixture happened to use
# exactly that stem, but this repo's own real prose (completion-report.md,
# the run-card phrases in airuleset.py, even this file's OWN sibling test
# fixtures) overwhelmingly writes the native `-n-` stem instead
# ("zmergnutý", "mergnutí") — #319's own bug class (coverage proven only
# against a convenient fixture) reproduced one level down. Widened to a
# STEM match (zlúči/zmerg/mergn, left `\b` only — these are prefixes that
# continue with many suffixes: zlúčiť/zlúčim, zmergovať/zmergnúť/
# zmergnutie/zmerguj, mergnúť/mergnutie) rather than enumerating every
# conjugated form. Also widened "despite" beyond the single word "napriek"
# — Slovak splits the English concept "despite" across several equally
# common connectors ("hoci", "aj keď", "i keď"), unlike English's single
# lexeme, so a one-word anchor covered only a narrow slice of real usage.
#
# #319-review MAJOR (reproduced live): same line-oriented grep limitation
# as the dispatch-now-or-hold check above — flattened to a single line
# (newlines -> spaces) for the SAME reason (a strict superset of the
# un-flattened match set), so a multi-line rendering of this shape is not
# missed either.
#
# Proven against negative-control fixtures where a "despite" word appears
# without a nearby merge-verb (an unrelated deploy-status sentence), and
# where a merge-verb appears without a nearby "despite" word — see issue
# #319 comment. Accepted residual, same shape as the pre-existing English
# "merge.*despite" alternative right above: an honest sentence NARRATING
# or CITING this exact rule (e.g. "we must never merge X despite a
# failing check") still trips the detector — quoting/backtick-wrapping
# the citation is the established escape hatch (MSG_MENTION strips it).
SK_MERGE_RX="\b(zl[úu]či|zmerg|mergn)"
SK_DESPITE_RX="\b(napriek|hoci|aj ke[ďd]|i ke[ďd])\b"
SK_MERGE_FLAT=$(tr '\n' ' ' <<<"$MSG_MENTION") || SK_MERGE_FLAT="$MSG_MENTION"
# #316-review CRITICAL (reproduced again here, live): `\b` immediately
# adjacent to a diacritic is itself locale-dependent under a bare
# C/POSIX locale — forcing LC_ALL=C.UTF-8 on just this one grep call is
# the verified fix, scoped to this command only via a plain
# `VAR=val funcname` prefix. See the SAME finding on the Slovak
# dispatch-now-or-hold check above for the full explanation.
if LC_ALL=C.UTF-8 msg_has "$SK_MERGE_FLAT" -qiE \
    "${SK_MERGE_RX}.{0,60}${SK_DESPITE_RX}|${SK_DESPITE_RX}.{0,60}${SK_MERGE_RX}"; then
    echo "VIOLATION: Ponúkol si po slovensky quality-bypass skratku ('zlúčiť/zmergovať/mergnúť napriek/hoci zlyhaniu') — presne trieda 'merge despite the failing check' z ask-before-assuming.md, len v jazyku ktorý anglický regex nezachytáva. Toto je PRE-ANSWERED: zlyhávajúca kontrola = over branch protection sa nedá obísť. Preskúmaj skutočnú príčinu a oprav ju. NIKDY nenavrhuj admin-merge ani obídenie kontroly. (Ak túto vetu iba CITUJEŠ alebo VYSVETĽUJEŠ — napr. pri opise tohto pravidla — obal ju do úvodzoviek alebo spätných apostrofov, inak ju gate prečíta ako reálny návrh.) See autonomous-quality-discipline.md, ask-before-assuming.md." >&2
    add_hard "Pre-answered Slovak prose question: quality-bypass shortcut / merge despite (zlúčiť/zmergovať/mergnúť + napriek/hoci/aj keď) — fix the gate instead"
fi

# SOFT — bare delegation phrases carry real non-bypass uses ("the cheaper
# option is a smaller VM"), so they warn without gating an honest message.
if msg_has "$MSG" -qiE "your call|cheaper option|quicker option|easier path|you decide(.*merge)?|your decision|up to you.*merge"; then
    echo "VIOLATION: You shifted a decision back to the user ('your call' / 'you decide' / 'cheaper / quicker option' / 'easier path'). When the goals already determine the answer, make the call yourself and keep working. Genuine product/scope decisions the user has a stake in are still theirs — everything else is yours. See autonomous-quality-discipline.md, ask-before-assuming.md." >&2
fi

# Detect "STOP at green PR URL" / "Awaiting your merge it" / "Phase N remains gated" prose
# These are template-bypass shorthands. If they appear, the message must use the full template.
if msg_has "$MSG" -qiE "STOP at (the )?green pr|stop at green pr url|stop at green-pr"; then
    if [ "$IS_COMPLETION_HEADING" = "0" ]; then
        echo "VIOLATION: 'STOP at green PR URL' is template-bypass prose. Any 'we're done, PR is ready, awaiting merge' message MUST use the full Completion Report template (## ✅ Work Complete with audits, Goal, What changed, 🌐 URLs, PR title/URL). Replace the prose with the template. See completion-report.md → 'Full template every time'." >&2
    fi
fi

# Detect "Phase N remains gated on Phase M merge" / "Phase N is gated on" / "next phase awaits"
# This is a "Remaining/Future" mention disguised as plan continuity — banned per complete-planned-work.md.
if msg_has "$MSG" -qiE "phase [0-9]+ (remains|is) gated on|phase [0-9]+ awaits|phase [0-9]+ blocked on .*(merge|phase)|next phase (awaits|gated|blocked)|gated on phase [0-9]+ merge"; then
    echo "VIOLATION: 'Phase N remains gated on Phase M' is a 'Remaining / Future' mention disguised as plan continuity. complete-planned-work.md and completion-report.md ban these in the report. The next phase is the next session's prompt — do NOT explain gating here. Cut the line. See completion-report.md → 'Banned shortcuts'." >&2
fi

# Check for PR completion message missing the PR URL
# Signal: completion language about a PR but no https://github.com/.../pull/N URL anywhere in message
if msg_has "$MSG" -qiE "awaiting (your|merge)|pr (is )?(ready|mergeable)|mergeable[, ]+(clean|all)|all checks (are )?green|ready to merge|per pr-merge-policy|awaiting.*\"merge it\""; then
    # The PR URL EXONERATES the announcement, so ask whether it is MISSING.
    if msg_missing "$MSG" -qE "https?://github\.com/[^[:space:]]+/pull/[0-9]+"; then
        echo "VIOLATION: You announced PR completion ('mergeable clean', 'awaiting merge', 'all checks green', etc.) without providing the PR URL. completion-report.md and pr-merge-policy.md MANDATE the PR URL on the completion line: '✅ PR: <https://github.com/.../pull/N> — mergeable, clean'. Always paste the full URL — the user works remotely and cannot click 'PR #11'. Use the EXACT completion-report.md template, not a prose summary." >&2
    fi
fi

# Check deploy verification has 🌐 URL lines for USER-CLICKABLE web URLs.
# Multi-environment (dev+prod / dev+staging / prod+staging) ⇒ require ≥2 🌐 lines.
# Single UI deploy ⇒ require ≥1 🌐 line.
# 🌐 lines list USER-clickable URLs (frontend / dashboard / admin) — NEVER backend/API URLs.
# Backend URLs are agent verification evidence, not human clickables.
# Gate: only fire on COMPLETION REPORTS or messages with explicit `✅ Deploy:` line.
# Casual "deployed to dev1+dev2" mentions (admin chitchat) must NOT trigger this rule.
HAS_DEPLOY_LINE=$(msg_has "$MSG" -qE "✅ Deploy:" && echo 1 || echo 0)
if { [ "$IS_COMPLETION" = "1" ] || [ "$HAS_DEPLOY_LINE" = "1" ]; } && msg_has "$MSG" -qiE "✅ Deploy:|deploy.*(verified|complete|done|success|redeploy|auto.?redeploy)|verified.*deploy|deployed.*(to|successfully)"; then
    # "Does the report carry enough 🌐 lines" is a REQUIRED-FIELD question, so an
    # unanswerable count must NOT read as zero and become an accusation — it
    # prints `?` and both branches below are skipped.
    GLOBE_COUNT=$(msg_count "$MSG" -E "🌐.*https?://")

    # Anti-pattern: 🌐 line listing a backend/API URL — clutters the user's clickable list.
    GLOBE_HAS_BACKEND=$(msg_has "$MSG" -qiE "🌐.*(backend|/api/|api[: ]|:8000|:8080|:5000|api endpoint|api server)" && echo 1 || echo 0)

    HAS_DEV=$(msg_has "$MSG" -qiE "\bdev\b|\bdevelopment\b" && echo 1 || echo 0)
    HAS_PROD=$(msg_has "$MSG" -qiE "\bprod\b|\bproduction\b" && echo 1 || echo 0)
    HAS_STAGING=$(msg_has "$MSG" -qiE "\bstaging\b|\bstage\b" && echo 1 || echo 0)
    HAS_UI=$(msg_has "$MSG" -qiE "frontend|dashboard|\bui\b|web app|browser|admin panel" && echo 1 || echo 0)

    MULTI_ENV=0
    [ "$HAS_DEV" = "1" ] && [ "$HAS_PROD" = "1" ] && MULTI_ENV=1
    [ "$HAS_DEV" = "1" ] && [ "$HAS_STAGING" = "1" ] && MULTI_ENV=1
    [ "$HAS_PROD" = "1" ] && [ "$HAS_STAGING" = "1" ] && MULTI_ENV=1

    if [ "$GLOBE_HAS_BACKEND" = "1" ]; then
        echo "VIOLATION: A 🌐 URL line lists a backend/API URL (matched ':8000' / ':8080' / '/api/' / 'backend:' / 'api:'). The user reads the 🌐 list to click in a browser — backend URLs are noise there. Backend evidence belongs in '✅ Deploy:' (e.g. 'dev backend serves v1.0.97-dev.9 via /api/version'), NOT in the 🌐 list. Remove backend/API entries from 🌐. See completion-report.md → 'Dashboards & URLs'." >&2
    fi

    if [ "$GLOBE_COUNT" = "?" ]; then
        :  # unknown count — see msg_count; never accuse on a number we don't have
    elif [ "$MULTI_ENV" = "1" ] && [ "$GLOBE_COUNT" -lt 2 ]; then
        echo "VIOLATION: Deploy mentions multiple environments (dev/staging/prod) but the report has only $GLOBE_COUNT clickable 🌐 URL line(s). List every USER-CLICKABLE web URL on its own '🌐 <env>: <url>' line — typically one per environment. Read the project's CLAUDE.md '## Dashboards' / '## URLs' section. Do NOT list backend/API URLs — only user-facing browser URLs. URLs in prose ('curl http://...') do NOT count. See completion-report.md → 'Dashboards & URLs'." >&2
        add_hard "Multi-env deploy with <2 🌐 URL lines"
    elif [ "$HAS_UI" = "1" ] && [ "$GLOBE_COUNT" -lt 1 ]; then
        echo "VIOLATION: Deploy mentions a UI/frontend/dashboard but the report has no clickable 🌐 URL line. The user cannot click URLs buried in prose. Add at least one '🌐 <env>: <url>' line for the user-facing dashboard (NOT backend/API). See completion-report.md → 'Dashboards & URLs', and no-localhost-urls.md." >&2
        add_hard "UI deploy with no 🌐 URL line"
    fi
fi

# Check for a localhost/127.0.0.1/0.0.0.0 URL on a 🌐 (or 📱) line — issue #13
# sub-item 3, widened by #265 to the sibling artifact marker completion-
# report.md now teaches for client-app installable builds (APK/IPA/signed
# binary). Scoped ONLY to lines carrying one of these two markers (never the
# whole message) — both markers are used EXCLUSIVELY for "USER-CLICKABLE (or
# user-downloadable) URL being presented right now" per completion-report.md,
# so this has near-zero FP risk: a code block or prose paragraph discussing
# "the dev server runs on localhost:5173" is never touched, only an actual
# 🌐/📱-prefixed URL line. Not gated on IS_COMPLETION — a mid-work "here's the
# preview: 🌐 http://localhost:3000" is exactly the no-localhost-urls.md
# violation ("the user works remotely and cannot open localhost on their own
# machine"), completion report or not — and no-localhost-urls.md's own scope
# ("This applies to ALL URLs") is why 📱 gets the identical treatment the
# moment the module names it, not a narrower one. HARD block: no-localhost-
# urls.md documents no legitimate exception for presenting one.
# Two stages, both on here-strings, and they are NOT the same kind of question.
# Stage 1 is a SELECTOR — it only chooses which lines stage 2 reads — so it is
# neither incriminating nor exonerating, and an unanswerable selector must WIDEN
# the scope and let the real pattern decide, exactly as the /goal filter above
# falls back to the full MSG. Resolving a selector as a VERDICT accuses a message
# that carries no 🌐/📱 line at all, prints an EMPTY offending-lines block, and
# attaches a note blaming message size that points the wrong way. Stage 2 IS the
# incriminating pattern, so its own unknown resolves as "present" and the gate
# fires — it simply cannot quote the lines it never obtained.
GLOBE_LOCALHOST=""
GLOBE_LOCALHOST_UNKNOWN=0
if ! _GLOBE_LINES=$(msg_lines "$MSG" -E "🌐|📱"); then
    _GLOBE_LINES="$MSG"
fi
if ! GLOBE_LOCALHOST=$(msg_lines "$_GLOBE_LINES" -iE "localhost|127\.0\.0\.1|0\.0\.0\.0"); then
    GLOBE_LOCALHOST=""
    GLOBE_LOCALHOST_UNKNOWN=1
fi
if [ -n "$GLOBE_LOCALHOST" ] || [ "$GLOBE_LOCALHOST_UNKNOWN" = "1" ]; then
    echo "VIOLATION: A 🌐/📱 URL line points at localhost/127.0.0.1/0.0.0.0. The user works remotely and cannot open a localhost URL on their own machine. Use the machine's real LAN/tailscale IP instead (\`hostname -I\`), and verify it returns 200 before presenting it. See no-localhost-urls.md." >&2
    echo "  Offending line(s):" >&2
    echo "$GLOBE_LOCALHOST" | sed 's/^/    /' >&2
    add_hard "🌐/📱 URL line points at localhost/127.0.0.1/0.0.0.0 — use the real LAN IP"
fi

# #194 — the global suppression that used to sit here is GONE. It asked "did ANY
# check error" and, on yes, discarded EVERY hard violation, including ones
# decided by a different pattern on a different regex engine that never errored:
# a grep error on check A silently deleted a verdict from check B. Its only job
# was to undo the fabricated "absent" that `msg_has` used to return, and that no
# longer exists — every unknown is now resolved at its own call site, in the
# direction that site declares. There is nothing left to correct.
#
# What survives is a NOTE. Companion 2: this hook is non-blocking on stderr, so
# the per-check UNDETERMINABLE diagnostic reaches an operator reading a journal
# but never the model. The note therefore travels on the block REASON, which
# does — so a listed violation resting on a check the hook could not evaluate is
# visible to the agent that has to act on it.
UNDET_NOTE=""
if [ -n "$UNDET_FILE" ] && [ -s "$UNDET_FILE" ]; then
    UNDET_N=$(wc -l <"$UNDET_FILE" | tr -d ' ')
    UNDET_NOTE="\n\nNOTE: ${UNDET_N} check(s) were UNDETERMINABLE (grep errored — see this hook's stderr for which). An undeterminable check is resolved AGAINST the message, so a violation listed above may rest on a check that could not be evaluated. The usual cause is an oversized or pathological message: shortening it removes the cause as well as the symptom."
fi

# Final: if HARD violations found AND retry budget not exhausted, output JSON to block Stop.
# Per Claude Code hooks docs: {"decision":"block","reason":"..."} prevents Claude from stopping.
# Retry limit prevents loops if a violation is genuinely unfixable in this session.
#
# #196 — the ORDER below is load-bearing. The bookkeeping used to run FIRST,
# so under this script's own `set -euo pipefail` a failed redirect (an
# unwritable counter, a counter path that cannot be built) exited the shell
# before the verdict existed: rc 1, no JSON, and a quality-bypass offer shipped.
# The verdict now goes to stdout before anything else is attempted, and nothing
# below the `jq` can unsay what it printed.
if [ -n "$HARD_VIOLATIONS" ]; then
    if [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
        REASON="Hard violations detected in your message:\n${HARD_VIOLATIONS}\nFix the message (rewrite or trim the offending content) and resend in this turn. See ask-before-assuming.md (pre-answered questions) and completion-report.md (report template) for details.${UNDET_NOTE}"
        jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
        # Bookkeeping, and it decides NOTHING: it runs after the verdict is
        # already on stdout, only when a key was established, and it cannot
        # fail the hook.
        if [ -n "$RETRY_FILE" ]; then
            # Write through an unpredictable temp file and RENAME over the key,
            # never a bare `>` at the key itself: `>` FOLLOWS a symlink, and the
            # key is world-plantable on a sticky /tmp shared with foreign uids,
            # so a bare redirect is a same-uid truncation primitive aimed at
            # whatever someone else points it at. `mv` replaces the NAME, so a
            # planted symlink — or a planted file holding a value that would
            # disarm the gate — is displaced by our own 0600 file instead of
            # being written through, which also self-heals the key.
            #
            # Braces, not `… 2>/dev/null` on the redirect itself: a REDIRECTION
            # failure is reported by the shell before that `2>` is in effect, so
            # the naive form still prints "Permission denied" into the turn and
            # reads like a hook malfunction. A counter that cannot be written
            # just does not advance — at most one extra retry, never a warning
            # every turn.
            RETRY_TMP=$(mktemp "/tmp/airuleset-stop-tmp.XXXXXXXX" 2>/dev/null || echo "")
            if [ -n "$RETRY_TMP" ]; then
                { echo "$((RETRIES+1))" > "$RETRY_TMP" \
                    && mv -f "$RETRY_TMP" "$RETRY_FILE"; } 2>/dev/null || true
                rm -f "$RETRY_TMP" 2>/dev/null || true
            fi
        fi
        exit 0
    fi

    # Cap exhausted: a REAL violation is being let through. SAY SO. The empty
    # stdout this used to produce is indistinguishable from a clean message for
    # every caller, which is exactly how a poisoned counter made an independent
    # verification report a shipped, correct, deployed fix as broken (#198).
    # stderr reaches an operator's journal; `systemMessage` is a documented,
    # DECISION-FREE field, so it warns the user without re-blocking — blocking
    # here would be the runaway the cap exists to stop, and Claude Code's own
    # CLAUDE_CODE_STOP_HOOK_BLOCK_CAP would override it anyway.
    CAP_MSG="airuleset prose gate: retry cap (${MAX_RETRIES}) exhausted for this session — Stop allowed with UNFIXED hard violations:\n${HARD_VIOLATIONS}"
    printf '%b\n' "$CAP_MSG" >&2
    jq -n --arg m "$CAP_MSG" '{systemMessage: $m}'
    exit 0
fi

# No hard violations — clear the counter so the next block loop starts fresh.
# `rm` used to be the command after the final `&&` of a `[ … ] && rm` list, so
# an unremovable counter was a `set -e` exit: a hook ERROR reported for a
# message that was perfectly clean.
if [ -n "$RETRY_FILE" ]; then
    rm -f "$RETRY_FILE" 2>/dev/null || true
fi
exit 0
