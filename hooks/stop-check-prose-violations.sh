#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Blocks on HARD violations (missing required completion-report fields)
# via {"decision":"block",...} JSON output, with retry limit (max 2 per session)
# to avoid runaway loops if a violation is genuinely unfixable.
# Warns on SOFT violations (banned phrases, prose questions) via stderr.

command -v jq &>/dev/null || exit 0

# #1073 — repo root for the `python3 -m gates.navody` dispatch (the #1042 block
# now delegates the client-guide live-link check to gates.navody). hooks/ live
# directly under the repo root.
_PROSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
_PROSE_REPO_ROOT="$(dirname "$_PROSE_DIR")"

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
    echo "VIOLATION: You asked 'subagent or inline' / 'two execution options' / 'dispatch now or skim' in prose at the end of your message. This is a pre-answered question — decide inline vs subagent yourself and proceed. The pre-ask-auto-answer hook blocks the structured AskUserQuestion form; writing the same question in prose is the same violation. Rewrite this message: cut the question entirely, and proceed. See ask-before-assuming.md pre-answered table." >&2
    add_hard "Pre-answered prose question: subagent-vs-inline / two execution options / dispatch-now-or-skim"
fi

# Check for an OWNER-FACING BARE Odoo-Discuss thread id (#657). The owner
# manages many client Discuss threads and cannot decode a bare internal channel
# id ("vlakno 288") — the SAME rule as "#N always carries its title"
# (issue-reference-context.md). #650 already HARD-blocks a missing thread NAME,
# but ONLY on ❓ CLIENT-POSTING approval questions (stop-check-question-quality.sh
# Check 6); this is the WIDER owner-facing surface the owner's actual complaint
# lives on — status / narration, not a question (montalu3, 2026-08-24: "co ja mam
# akoze robit s 'vlakno 288'?!"). It also requires the THREAD's own clickable
# deep URL (#595 generalized), the form the exemption below keys on.
#
# Design (see the #657 design comment): three-stage gate, so an ordinary
# sentence NEVER trips it —
#   (1) ANCHOR gate: run ONLY when the message names the Odoo-DISCUSS context by
#       a token an ordinary sentence does not carry — the module name `Discuss`
#       (matched case-SENSITIVE + word-boundaried `\bDiscuss\b`: the Odoo product
#       is Title-case, so the English verb "discuss" / its inflections
#       "discussion"/"discussed" AND "Discord" never anchor — #657 review 🟡), the
#       model `discuss.channel`, the XML-RPC method `message_post`, or the deep-URL
#       param `active_id`. Deliberately NOT bare "odoo" (Odoo's OWN "Sales Channel
#       01" vocabulary would over-anchor). A concurrency "vlákno 12", a Wi-Fi
#       "kanál 36", or a Sales-Channel note carries no such token → not considered.
#   (2) EXEMPTION: the canonical clickable deep URL discuss.channel_<N> present →
#       the owner can click through → COMPLIANT, no violation (the URL itself is
#       never mistaken for a bare id — the bare shapes below require a SPACE/colon
#       before the number, the URL uses `channel_<N>`). Checked on raw $MSG so a
#       backticked/quoted URL still exempts.
#   (3) BARE-shape: a thread word (vlákn*/kanál*/channel) IMMEDIATELY followed by
#       a bare 2+digit number, or the `ch<NN>` shorthand. The tight adjacency
#       window (only whitespace/`:`/`#`/`-` between the word and the number) means
#       a QUOTED thread NAME after the word (vlákno „Zakaznicky portal 3") never
#       matches — the good form passes. The 2+digit floor keeps single-digit
#       concurrency threads ("vlákno 2") out. Run on $MSG_BARE — $MSG_MENTION
#       (strip_mentions: ASCII quotes/backticks/fenced) with Slovak GUILLEMET
#       spans („…") ALSO stripped, so a message merely QUOTING the banned form —
#       a completion report / playbook capture citing „vlákno 288" or `vlákno 288`
#       — is NOT gated (#657 review 🔴; guillemets are the doctrine's OWN
#       delimiter). Incriminating half mention-stripped, exemption on raw $MSG,
#       same split the credential check ~330 lines below uses.
# LC_ALL=C.UTF-8 per the repo's #319 diacritic-safe convention; msg_has reads via
# a here-string, never a `printf|grep -q` pipe (#292).
#
# Accepted residuals (word-family heuristic, not a parser — a genuine occurrence
# outside these families needs its own follow-up, never a blanket rewrite; the
# doctrine in issue-reference-context.md covers all rewordings universally):
# (1) a TERSE owner-facing status with a bare "vlákno 288" and NO Discuss token at
#     all slips the anchor gate (safe UNDER-block; doctrine still governs it);
# (2) a 2-digit concurrency "vlákno 12" INSIDE a Discuss-context message would
#     trip (rare); (3) message-level exemption — a message that references thread
#     A by its deep URL but sloppily names thread B by bare id passes (the same
#     coarseness the "#N carries its title" doctrine accepts); (4) `channel`/
#     `kanál`+NN INSIDE a message that ALSO says "Discuss" trips even when it means
#     an Odoo "Sales Channel" (rare — the discuss anchor keeps a Sales-Channel-only
#     note out); (5) natural Slovak separators break adjacency and ESCAPE —
#     "vlákno č. 288", "vlákno číslo 288", "vlákna s ID 288" (safe UNDER-block), and
#     a trailing UNIT ("vlákno 288-znakový", "kanál 36 GHz") false-fires because the
#     number is merely adjacent (limited over-block; doctrine governs); (6) the
#     plural-genitive "vlákien" is outside the `vl[áa]kn` stem (uncommon); (7) the
#     URL-EXEMPTION deliberately uses msg_has (fail-OPEN on a grep error → "URL
#     present" → EXEMPT), the SAFE direction for an EXONERATING check — an
#     intentional divergence from the #194 msg_missing (fail-CLOSED) convention for
#     INCRIMINATING patterns; a shared grep failure trips the URL check to EXEMPT
#     before the BARE grep runs, so do NOT "fix" this to fail-closed.
# \bDiscuss\b is case-SENSITIVE (grep -qE, no -i) so the English verb/inflections
# and "Discord" never anchor (#657 review 🟡); the other tokens are exact literals.
ODOO_ANCHOR_RX='\bDiscuss\b|discuss\.channel|message_post|active_id'
DISCUSS_ANCHOR=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qE "$ODOO_ANCHOR_RX" && echo 1 || echo 0)
if [ "$DISCUSS_ANCHOR" = "1" ]; then
    # EXEMPT the moment the canonical clickable deep URL is present anywhere
    # (raw $MSG so a backticked URL still exempts).
    HAS_DEEP_URL=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qiE 'discuss\.channel_[0-9]' && echo 1 || echo 0)
    if [ "$HAS_DEEP_URL" = "0" ]; then
        # A thread word tightly adjacent to a bare 2+digit number, or `ch<NN>`.
        # Separators between word and number are ONLY [[:space:]:#-] — a letter
        # (a quoted NAME) breaks the match, so the good form passes. `channel_<N>`
        # (the URL) never matches (`_` is not a separator); a left word-boundary on
        # `channel` keeps "subchannel 288" out (#657 review 🔵). Run on $MSG_BARE:
        # $MSG_MENTION with Slovak guillemet spans („…") also stripped, so a
        # message merely QUOTING the banned form is exempted (see stage 3 above).
        MSG_BARE=$(LC_ALL=C.UTF-8 sed -E 's/„[^„]*[“”"]/ /g' <<<"$MSG_MENTION" 2>/dev/null) || MSG_BARE="$MSG_MENTION"
        BARE_THREAD_RX='(vl[áa]kn[a-z]*|kan[áa]l[a-z]*|(^|[^[:alnum:]])channel)[[:space:]:#-]*[0-9]{2,}|(^|[^[:alnum:]])ch[0-9]{2,}'
        BARE_THREAD=$(LC_ALL=C.UTF-8 msg_has "$MSG_BARE" -qiE "$BARE_THREAD_RX" && echo 1 || echo 0)
        if [ "$BARE_THREAD" = "1" ]; then
            echo "VIOLATION: Your message names an Odoo Discuss thread by a BARE id (e.g. „vlákno 288\") with no clickable deep URL. The owner manages many client threads and cannot decode a bare channel id — the same rule as '#N always carries its title'. Name the thread AND give its clickable deep URL, e.g.: vlákno „<presný názov N>\" — https://<instancia>/odoo/discuss?active_id=discuss.channel_<N>. See modules/core/issue-reference-context.md + skills/odoo-client-messaging/handover-compose.md (#657 — extends #650 name-in-question + #595 deep-link URLs)." >&2
            add_hard "Owner-facing Odoo Discuss thread referenced by a BARE id with no clickable deep URL (discuss.channel_<N>) — give the thread name + its deep URL (#657)."
        fi
    fi
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

# Slovak tester-handoff family (autonomous-verification.md, #424 — the
# mobile-app case: montalu3 was made to hand-install an APK on their OWN
# phone 10x over 2h, while an agreed emulator sat unused the whole time;
# the agent later admitted it never needed the human at all). SAME
# recurrence chain as #319 (dispatch-now-or-hold, admin-merge,
# merge-despite): the English tester-handoff block right above this one is
# proven English-only (#95/#316's own audit criterion) — a genuinely
# natural Slovak rendering of the SAME banned intent, verified LIVE against
# the un-patched hook (see #424's own STEP 0 comment), was not blocked by
# it. Word-family shapes (deliberately families, never literal strings),
# mirroring #319's own established pattern shape verbatim: bounded `.{0,N}`
# windows, `\b` anchors on plain diacritic alternation inside bracket
# classes (never embedded lookaheads — ERE has none), and LC_ALL=C.UTF-8
# forced on the one grep call that needs it (`\b` next to a diacritic is
# itself locale-dependent under a bare C/POSIX locale — #316-review's own
# CRITICAL finding, reproduced and forced here the same way). Newlines are
# flattened to a single space first, so a hard-wrapped rendering of the
# same banned intent cannot escape via a line break.
#
#   1. install(+confirm/works): nainštaluj(eš)?/inštaluj(eš)? near a
#      confirm-verb (potvrď/potvrdíš/povedz/povieš/napíš/napíšeš/
#      daj/dáš (mi) vedieť/overíš) — OR the confirm-verb standing ALONE
#      near an outcome word (funguje/ "či...ide"), with no install context
#      required. This second, install-free trigger is the direct Slovak
#      counterpart of the English hook's own install-free "let me know
#      if...works" pattern, not new scope.
#   1b. over-či (#424-review C1/C2): "over(,)? (si)? či ..." — a STANDALONE
#      trigger, no WORKS word required — this is what catches the
#      incident's OWN literal quote "over či ti to ide" (which names no
#      install verb AND no unambiguous outcome word). Deliberately does
#      NOT accept bare "over" or 3rd-person "overí" anywhere else (see the
#      #424-review note below — both are common false-positive magnets:
#      bare "over" collides with the English word "over" and with
#      "Coverage je over 90%"; 3rd-person "overí" is how an agent
#      routinely describes ITS OWN verification, e.g. "test overí, že X
#      funguje", which must never block).
#   2. try-on-device: vyskúšaj/vyskúšaš/otestuj/otestuješ near
#      telefón/mobil/zariaden- (either order).
#   2b. modal-request (#424-review M2 — the direct Slovak counterpart of
#      the English hook's OWN primary "(can|could|would) you...test"
#      shape, not new scope): môžeš/vieš near an install/try infinitive
#      (vyskúšať/otestovať/nainštalovať/inštalovať) — either order.
#   3. write-what-you-see: napíš/napíšeš/povedz/povieš near "čo vidíš"
#      (either order).
#   4. when-you-verify-continue: overíš/vyskúšaj/vyskúšaš near a
#      1st-person continue-verb (pokračuj-/spravím/urobím). Deliberately
#      2nd-person-future "overíš" only (not bare "over"/"overí" — same
#      C1 reasoning as shape 1b).
#
# The imperative/2nd-person-future stems this keys on (nainštaluj-,
# otestuj-, overíš, nap[íi][šs](e[šs])?, ...) are grammatically DISTINCT
# Slovak stems from the 1st-person PAST-TENSE reporting forms (nainštalova
# -l, otestova-l, overi-l, napísa-l) — not merely a different suffix on
# the identical stem — so the trailing `\b` boundary check structurally
# excludes "nainštaloval som APK na emulátore a otestoval" (the agent's
# own past-tense report of testing on the emulator ITSELF) with no
# special-casing needed. Same mechanism separates a genuine 2nd-person
# nudge ("napíšeš") from the agent's own 1st-person offer ("napíšem") —
# Slovak's 1st/2nd-person present-tense endings diverge in exactly the
# way this regex's optional suffix groups require, and separates 3rd-
# person "overí"/"potvrdí" (the agent describing what a TEST/CI/watchdog
# does) from 2nd-person "overíš"/"potvrdíš" (a direct nudge to the human).
# Escape is IDENTICAL to the English branch above, reused verbatim (never
# duplicated): an explicit `UNVERIFIED:` line disarms it.
#
# #424-review (adversarial, CRITICAL x2 + MAJOR x2): the FIRST cut let
# bare "over"/3rd-person "overí" stand for both CONFIRM and VERIFY, and
# let bare `\bide\b` stand for WORKS with no anchor — both are real,
# live-triggered false-positive magnets (English "over", "Coverage …
# over 90%", "test overí, že X funguje" — the repo's OWN mandated design-
# comment phrasing, and the "IDE" acronym matching `\bide\b`
# case-insensitively). Fixed by (a) restricting OVER/VERIFY to the
# unambiguous 2nd-person-future "overíš" plus the standalone "over(,)?
# (si)? či" idiom (shape 1b, above) — bare "over" alone, or "overí" with
# no following "či", is REFUSED; (b) requiring "ide" (as a WORKS word,
# never bare `\bide\b`) to sit in the fixed phrase "či (ti)? to ide" —
# "to" MANDATORY, not merely "či...ide" within a window, since "ide o X"
# (an unrelated, extremely common idiom meaning "this concerns/is about
# X" — this repo's own mandated Slovak question template routinely says
# "napíš/povedz mi, či ide o produkčnú databázu") has NOTHING between
# "ide" and "o" and would otherwise collide; genuine "does it work"
# phrasing always includes "to" ("či to ide"/"či ti to ide"), so requiring
# it closes the idiom collision without narrowing real coverage, and
# spelling "či"/"ci" WITHOUT the usual ASCII-fallback alternation for
# THIS one word specifically — `či`, never `[čc]i` — because the bare-c
# fallback collides with this repo's own extremely common "CI"
# (continuous-integration) acronym under case-insensitive matching
# ("CI teraz ide zelené" must never block); (c) the real 2nd-person-
# future "dáš (mi) vedieť" (long á) was missing — only the ASCII-degraded
# hybrid "daš" matched, so the incident's own most natural confirm idiom
# escaped; fixed by accepting daj/dáš/daš uniformly. Also added the
# modal-request shape (2b) the review flagged as materially defeating the
# ticket's real-world purpose by omission.
#
# Accepted residuals (documented, not chased, per #319's own precedent —
# this is a covered WORD-FAMILY, never a claim of blanket Slovak
# coverage): a decoy mention inside an INTERPRETER heredoc body still
# executes (the same residual already documented for this whole file); a
# genuinely exotic synonym verb outside this word-family list (e.g. "skús"
# for "try", or an outcome word other than funguje/ide such as "beží",
# "padá", "dopadlo") can still slip through; the formal register (vykanie
# — "Nainštalujte si...", "Dajte mi vedieť...") is not covered, only the
# informal 2nd-person-singular this repo's own real prose exclusively
# uses; a 2nd-person-PAST interrogative ("Nainštaloval si už...?") is not
# covered; a genuinely-sanctioned FINAL-acceptance nudge sent only AFTER
# a real green agent-side/emulator verification (#424-review M3) still
# trips this detector exactly like a premature one would — the stderr
# message below gives NO rewording escape for this specific case (its
# only named exit is `UNVERIFIED:`, which would MISSTATE an already-
# verified result, so it is not the right escape either); this is the
# SAME structural tension the pre-existing English branch above already
# has, not a new one #424 introduces, and it is left unresolved here
# for the identical reason (round-2 review MINOR-2: corrected this
# paragraph, which previously overclaimed a rewording mitigation the
# message text does not actually contain). Proximity-only matching (no real
# grammatical parsing — ERE cannot do that) can, rarely, pair two
# unrelated clauses that happen to share window-adjacent trigger words
# (e.g. a conditional "ak povieš áno, nasadím verziu, ktorá funguje aj
# offline" is not tester-handoff at all) or match an imperative verb
# quoted inside this repo's OWN documentation/playbook prose describing a
# verification STEP rather than addressing a human — both are the same
# known limitation #319's own detectors already carry.
#
# #424-review round 2 (adversarial, MAJOR x1 + MINOR x2, fixed here):
#   - MAJOR-2: SK_TH_WORKS_RX required "to" immediately before "ide" with
#     no room for an adverb — "či to UŽ ide"/"...TERAZ ide" ("does it
#     work NOW", the incident's own iterative-retry idiom) escaped.
#     Fixed by allowing an optional "už"/"teraz" between "to" and "ide";
#     "to" itself stays mandatory, so the C2 "ide o X" idiom discriminator
#     is untouched.
#   - MINOR-1: the modal+infinitive shape (2b) live-false-positived on an
#     epistemic "vieš, že..." ("do you know that...", not a request) and
#     on "netreba <infinitive>" ("X is not needed", the opposite of a
#     request). Since ERE has no lookaround, the modal shape is now
#     evaluated as its OWN separate check (SK_TH_MODAL_ALT below) with an
#     explicit AND-NOT exclusion (SK_TH_MODAL_SAFE_ALT) — scoped so it can
#     only ever suppress the MODAL branch itself, never a genuine
#     violation from one of the other shapes (those are still checked by
#     the unmodified SK_TH_MAIN_ALT, independent of this exclusion).
#   - MINOR-3: shape 2's try/device window (40 chars) missed a relative
#     clause between the two words ("vyskúšaj tú novú verziu, ktorú som ti
#     práve poslal, na telefóne") — widened to 60, matching the window
#     size shape 4 already uses.
SK_TH_INSTALL_RX="\b(nain[šs]taluj|in[šs]taluj)(e[šs])?\b"
SK_TH_CI_RX="či"
SK_TH_OVER_CI_RX="\bover,?[[:space:]]+(si[[:space:]]+)?${SK_TH_CI_RX}\b"
SK_TH_CONFIRM_RX="\b(potvr[ďd]|potvrd[íi][šs]|povedz|povie[šs]|nap[íi][šs](e[šs])?|(daj|d[aá][šs])[[:space:]]+(mi[[:space:]]+)?vedie[ťt]|over[íi][šs])\b"
SK_TH_WORKS_RX="(\bfunguj|\b${SK_TH_CI_RX}\b[[:space:]]+(ti[[:space:]]+)?to([[:space:]]+(u[žz]|teraz))?[[:space:]]+ide\b)"
SK_TH_TRY_RX="\b(vysk[úu][šs]a[jš]|otestuj(e[šs])?)\b"
SK_TH_DEVICE_RX="(telef[óo]n|mobil|zariaden)"
SK_TH_MODAL_RX="\b(m[ôo][žz]e[šs]|vie[šs])\b"
SK_TH_MODALVERB_RX="\b(vysk[úu][šs]a[ťt]|otestova[ťt]|nain[šs]talova[ťt]|in[šs]talova[ťt])\b"
SK_TH_MODAL_EPISTEMIC_RX="\b(m[ôo][žz]e[šs]|vie[šs]),?[[:space:]]+[žz]e\b"
SK_TH_MODALVERB_UNNEEDED_RX="netreba[[:space:]]+(vysk[úu][šs]a[ťt]|otestova[ťt]|nain[šs]talova[ťt]|in[šs]talova[ťt])\b"
SK_TH_WRITESAY_RX="\b(nap[íi][šs](e[šs])?|povedz|povie[šs])\b"
SK_TH_SEE_RX="čo[[:space:]]+vid[íi][šs]"
SK_TH_VERIFY_RX="\b(over[íi][šs]|vysk[úu][šs]a[jš])\b"
SK_TH_CONTINUE_RX="\b(pokra[čc]uj|sprav[íi]m|urob[íi]m)"
SK_TH_FLAT=$(tr '\n' ' ' <<<"$MSG_MENTION") || SK_TH_FLAT="$MSG_MENTION"
SK_TH_MAIN_ALT="(${SK_TH_INSTALL_RX}.{0,100}${SK_TH_CONFIRM_RX})|(${SK_TH_CONFIRM_RX}.{0,50}${SK_TH_WORKS_RX}|${SK_TH_WORKS_RX}.{0,50}${SK_TH_CONFIRM_RX})|${SK_TH_OVER_CI_RX}|(${SK_TH_TRY_RX}.{0,60}${SK_TH_DEVICE_RX}|${SK_TH_DEVICE_RX}.{0,60}${SK_TH_TRY_RX})|(${SK_TH_WRITESAY_RX}.{0,30}${SK_TH_SEE_RX}|${SK_TH_SEE_RX}.{0,30}${SK_TH_WRITESAY_RX})|(${SK_TH_VERIFY_RX}.{0,60}${SK_TH_CONTINUE_RX})"
SK_TH_MODAL_ALT="(${SK_TH_MODAL_RX}.{0,40}${SK_TH_MODALVERB_RX}|${SK_TH_MODALVERB_RX}.{0,40}${SK_TH_MODAL_RX})"
SK_TH_MODAL_SAFE_ALT="${SK_TH_MODAL_EPISTEMIC_RX}|${SK_TH_MODALVERB_UNNEEDED_RX}"
if LC_ALL=C.UTF-8 msg_has "$SK_TH_FLAT" -qiE "$SK_TH_MAIN_ALT" || \
    { LC_ALL=C.UTF-8 msg_has "$SK_TH_FLAT" -qiE "$SK_TH_MODAL_ALT" && \
      LC_ALL=C.UTF-8 msg_missing "$SK_TH_FLAT" -qiE "$SK_TH_MODAL_SAFE_ALT"; }; then
    if msg_missing "$MSG" -qE "UNVERIFIED:"; then
        echo "VIOLATION: Odovzdal si verifikáciu človeku po slovensky ('nainštaluj si APK a potvrď, či funguje', 'vyskúšaj to na telefóne', 'napíš, čo vidíš', 'over či ti to ide') — presne trieda 'tester-handoff' z autonomous-verification.md, len v jazyku ktorý anglický regex nezachytáva. Používateľ NIKDY nie je tvoj tester. Pre mobilné-appky projekty je emulátor/adb ekvivalent Playwrightu — over si to SÁM na emulátore, zabuduj si diagnostické zasielanie sám. Používateľovo zariadenie smie prísť najviac ako FINÁLNA akceptácia PO zelenej agent-side verifikácii, NIKDY ako iteratívny debug kanál ('skús to znova, nová verzia'). Ak si toto naozaj nevieš overiť sám (chýba ti nástroj/prístup), najprv o ten nástroj POŽIADAJ (nikdy o test) — a až potom, ak naozaj neexistuje, napíš 'UNVERIFIED: <čo nejde overiť> — <prečo>'." >&2
        echo "" >&2
        echo "  Decision tree (rovnaký ako anglická vetva vyššie):" >&2
        echo "    1. Vieš to overiť existujúcimi nástrojmi (emulátor/adb, curl, MCP)? → OVER TO SÁM." >&2
        echo "    2. Chýba ti nástroj/prístup? → POŽIADAJ O NÁSTROJ, nie o test." >&2
        echo "    3. Je to naozaj len na fyzickom zariadení používateľa? → AŽ POTOM: UNVERIFIED: <čo> — <prečo>." >&2
        echo "" >&2
        echo "  See autonomous-verification.md → 'Nainštaluj si APK a povedz či funguje' (mobile-app anti-pattern)." >&2
        add_hard "Slovak tester-handoff phrase (mobile-app user-as-tester) — over si to sám na emulátore, alebo si vypýtaj nástroj. Or write 'UNVERIFIED:' after attempting tool-request."
    fi
fi

# #1101 — disowning items of the footer `I` obligation set without moving the
# label the partition reads. The gk FLOW session repeatedly told the owner its
# footer `I 24` items were "gk-infra / stream / not mine" while they were its
# OWN gk-processing pickups (0 carried `infra`). The obligation column
# (`action-only` = a stream hand-off THIS box must review → merge →
# release/return) is still THIS box's `I`; the ONLY correct act on an item
# believed to be someone else's is the LABEL move the partition reads
# (`infra` → INFRA window, `prio:bounce` → back to the stream,
# `needs-answer`/`needs-decision` → U, `ops-wait` → W) — never a status line
# that explains the number away. Same shape as the tester-handoff block above:
# a msg_has trigger (footer/obligation context AND a disowning phrase) → a
# msg_missing exoneration (a label-move token in the SAME turn, checked as
# MISSING per #195 — a backticked `--add-label …` counts as evidence the label
# moved) → VIOLATION + legend. The Slovak family runs on a newline-flattened
# MSG_MENTION under LC_ALL=C.UTF-8 (diacritic char-classes), so a hard-wrapped
# disowning line can't escape; a strip_mentions failure already records
# UNDETERMINABLE upstream and falls back to raw MSG, and a clean (non-disowning)
# message never matches, so a strip failure never fabricates a block.
I_NOT_MINE_FLAT=$(tr '\n' ' ' <<<"$MSG_MENTION") || I_NOT_MINE_FLAT="$MSG_MENTION"
# The bare `I <n>` footer form REQUIRES a space (`[[:space:]]+`, never `*`) AND
# a non-digit/non-`%` char after the digits, so neither an `i18n`/`i7` token
# (case-insensitive, no space) NOR a plain-English "I 100% confirm …" is read as
# the footer count "I 24" (#1101 reviews A+B, MEDIUM: "Fixed the i18n bug … not
# mine to edit" and a DB-migration report "I 100% confirm … not mine to drop"
# were false-blocked). The footer renders "I 24" WITH a space; the other footer
# tokens (footer/core-quals/položky I/v I/…) carry the real incident phrasings
# regardless.
I_FOOTER_CTX_RX="\bI[[:space:]]+[0-9]+([^0-9%]|$)|polo[žz]k\w*[[:space:]]+I\b|\bv[[:space:]]+I\b|footer|p[äa]ti[čc]k|paticka|core-quals|slice-quals|obligation"
I_DISOWN_RX="(s[úu]|patria|patr[íi])[[:space:]]+(gk-)?infra|patria[[:space:]]+(streamu|subdevu|streamom|in[ée]mu|streamy)|nie[[:space:]]+s[úu][[:space:]]+(moje|jeho|na[šs]e)|ni[čc][[:space:]]+spolo[čc]n|nem[áa][[:space:]].{0,15}spolo[čc]n|[čc]ak[áa](j[úu])?[[:space:]]+na[[:space:]]+stream|not[[:space:]]+(mine|ours|my[[:space:]]+tickets)|belong(s)?[[:space:]]+to[[:space:]]+(the[[:space:]]+)?(infra|stream|subdev)"
# Leading `(` (never a bare `--…`) so grep never misreads the pattern as an
# option — a pattern arg starting with `-` errors (rc>=2 → read as MISSING →
# a false block).
I_LABELMOVE_RX="(--add-label[[:space:]]+(infra|prio:bounce|needs-answer|needs-decision|ops-wait)|label\w*[[:space:]]+(infra|prio:bounce|needs-answer|needs-decision|ops-wait)\b.{0,30}(prida|nastav|presun|added|set|moved)|(prida|nastav|presun|added|set|moved)\w*.{0,30}label\w*[[:space:]]+(infra|prio:bounce|needs-answer|needs-decision|ops-wait))"
if LC_ALL=C.UTF-8 msg_has "$I_NOT_MINE_FLAT" -qiE "$I_FOOTER_CTX_RX" && \
   LC_ALL=C.UTF-8 msg_has "$I_NOT_MINE_FLAT" -qiE "$I_DISOWN_RX"; then
    # EXONERATION checked as MISSING on raw MSG (a backticked `--add-label …`
    # still counts as the label move) — #195: an unsubstantiated exemption must
    # never disarm an established violation. The exoneration is DELIBERATELY
    # narrow to the label-move vocabulary (`--add-label …` / `label … presun…`)
    # — a colloquial "bounced it" without naming the label does NOT exonerate,
    # because the whole point is to make the box MOVE (or cite) the label the
    # partition reads, not merely assert it acted (#1101 review B, accepted).
    if LC_ALL=C.UTF-8 msg_missing "$MSG" -qiE "$I_LABELMOVE_RX"; then
        echo "VIOLATION: You explained items of the footer \`I\` obligation set away as 'infra / gk-infra / belong to the stream / subdevu / not mine / waiting on streams' WITHOUT moving a label. The footer counts THIS box's obligation, and an \`action-only\` row is a stream hand-off YOU must review → merge → release → return/close — still YOUR \`I\`, never 'theirs' (\`gk-processing\` = you already picked it up). If you genuinely believe an item is someone else's, the ONLY correct act is the LABEL move the partition reads — never a status line that narrates the number away." >&2
        echo "" >&2
        echo "  Route by LABEL, then the item leaves your I truthfully:" >&2
        echo "    • infra-class            → \`gh issue edit <N> --add-label infra\`      (→ the INFRA window)" >&2
        echo "    • belongs to the stream  → \`gh issue edit <N> --add-label prio:bounce\` (→ back to the stream, leaves your I)" >&2
        echo "    • a question for the owner→ \`--add-label needs-answer\` / \`needs-decision\` (→ U)" >&2
        echo "    • blocked on a 3rd party → \`--add-label ops-wait\`                       (→ W)" >&2
        echo "" >&2
        echo "  Otherwise ACT on it (review → merge → release → return/close). See statusline-vocabulary.md (the I bullet) + skills/statusline-vocabulary-deep/DEEP-1.md (reading \`--list\`)." >&2
        add_hard "Disowned items of the footer I obligation set ('are infra / belong to the stream / not mine') without moving a label — an action-only row is a stream hand-off THIS box must review → merge → release/return, still YOUR I; the ONLY correct act on an item you believe is someone else's is the LABEL move the partition reads: infra → INFRA window, prio:bounce → back to the stream, needs-answer/needs-decision → U, ops-wait → W. Move the label (or act), never explain the number away."
    fi
fi

# Check for a DIRECT request that the user PASTE a credential VALUE into chat
# (#152 point 3, user-decided 2026-08-08: mechanically enforce via THIS
# existing hook — FREEZE forbids a new hook file). receive-files-via-upload-
# url.md already bans this in prose ("send me the API key here", "paste the
# token", "čo je to heslo?") and documents the real channel (`secret
# request`/`secret exec`) — this makes the ban mechanical.
#
# Scoped NARROW on purpose per the user's own decision ("úzke vzory priamej
# žiadosti... minimum falošných poplachov je výslovná podmienka rozhodnutia").
# A fresh-context adversarial review of the first cut found the DOMINANT
# false-positive class: verb+noun proximity ALONE, with no requirement that
# the destination is chat/the assistant, hard-blocks routine third-person
# technical prose ("The client must send the token in the Authorization
# header.", "Paste the token into the GitHub Secrets UI.", "...give each
# stage a token budget of ~50k."). The fix: an INCRIMINATING match now also
# requires a CHAT-DIRECTED marker present in the message ("me"/"here"/"to
# me"/"in chat" — English; "mi"/"sem"/"chatu"/"chate" — Slovak), computed
# as its own boolean and ANDed in bash (never folded into one giant regex —
# mirrors this file's own established HAS_BOXDRAW/HAS_LAYOUT_KW/
# NO_COMPANION_URL shape a few checks above). Every one of the review's own
# false-positive sentences names a DIFFERENT destination (a header, a
# Secrets UI, a budget, a browser) and so carries none of these markers.
# The genuine interrogative form ("what's the password?"/"aké je heslo?")
# needs no destination marker — a direct question already expects the
# answer typed back in the SAME chat, which is exactly the risk.
#
# The interrogative branch requires a LITERAL trailing "?" (not merely
# optional) and is ANCHORED at the end of its own line, so a policy/format
# question ("what's the password REQUIREMENT") or a declarative sentence
# that happens to end in the noun with no question mark ("...see what's in
# the token.") do not collide — both were review findings.
#
# The noun family is deliberately narrow: password / API key / token /
# connection string / (plural) credentials — the module's own enumerated
# list, plus "credentials" (only the plural, only paired with the same
# destination-marker gate above) per the review's own finding that a bare
# "share the login credentials" request is a common, low-ambiguity real
# shape worth covering. "secret" / singular "credential" / "PAT" remain
# REJECTED: common, ambiguous English words/acronyms ("give him a pat on
# the back", "share the secret of your success") a proximity match cannot
# tell apart from a genuine request, and minimising false positives is the
# explicit condition of this decision.
#
# Genuinely natural SLOVAK coverage, not just the English loanword form
# (#316/#319's own lesson: an English-only regex is blind on every away/
# stream box, since every real question this repo ships is Slovak) —
# verified empirically against dedicated Slovak fixtures. LC_ALL=C.UTF-8 is
# forced on every Slovak grep call: \b next to a diacritic is itself
# locale-dependent under a bare C/POSIX locale (the SAME gotcha
# SK_DISPATCH_RX/SK_APPROVAL_RX/SK_MERGE_FLAT already hit in this file), and
# converting bracket classes to plain alternation does NOT close it by
# itself (#316's own reproduction) — only a real UTF-8 locale does. The verb
# "daj" (give) was DROPPED from the Slovak list — the review found it
# collides with the extremely common idiom "daj mi vedieť" ("let me know"),
# which pairs "daj" with the dative "mi" (a destination marker!) for a
# reason that has nothing to do with a credential, and the ticket's own
# four named Slovak examples never used "daj" anyway.
#
# Escape: the SAME message referencing the sanctioned channel (`secret
# request`/`secret exec`) is the CORRECT shape and must never be blocked —
# checked on raw MSG (not MSG_MENTION), since the reference is routinely
# inside backticks and mention-stripping would delete it before the escape
# could ever be seen. It is an EXONERATING signal, so an unanswerable check
# denies it (msg_missing's own fail-closed direction, #194's taxonomy).
#
# The main verb+noun pattern runs on MSG_MENTION (mention-stripped) — a
# message merely QUOTING the banned phrase (documenting the rule, explaining
# what NOT to say) is a MENTION, not a bare offer, and must not be gated.
#
# Accepted residuals (narrow-on-purpose, matching this file's own established
# convention elsewhere — all FALSE NEGATIVES, never false positives, per
# review): a request verb/noun split across more than ~20 characters or a
# sentence boundary; a credential name given only as a backtick-quoted
# env-var identifier ("paste your `STRIPE_API_KEY` here") — mention-stripping
# removes the backtick span entirely, along with the only word that would
# have identified it as a credential; a message that rationalises the
# request by NAMING the (allegedly broken) secret-request channel while
# still asking for the value in the same breath — the escape is a raw
# substring match, by design, and does not try to tell a genuine escape
# from a self-serving one; Slovak inflections outside the declared forms
# ("zadaj heslo", "heslá", "heslom"); a synonym verb/noun outside the
# declared word families (e.g. "forward me the key", "odošli mi heslo").
CRED_VERB_RX="(paste|send|share|type|enter|copy|give)"
CRED_NOUN_RX="(password(s)?|api[ _-]?key(s)?|token(s)?|connection[ _-]?string(s)?|credentials)"
CRED_DEST_RX="\b(me|here|to me|in (the )?chat|into (the )?chat)\b"
CRED_ESCAPE_MISSING=$(msg_missing "$MSG" -qiE "secret request|secret exec|airuleset\.py secret" && echo 1 || echo 0)
CRED_VN_MATCH=$(msg_has "$MSG_MENTION" -qiE "\b${CRED_VERB_RX}\b.{0,20}\b${CRED_NOUN_RX}\b|\b${CRED_NOUN_RX}\b.{0,20}\b${CRED_VERB_RX}\b" && echo 1 || echo 0)
CRED_HAS_DEST=$(msg_has "$MSG_MENTION" -qiE "$CRED_DEST_RX" && echo 1 || echo 0)
CRED_INTERROG_MATCH=$(msg_has "$MSG_MENTION" -qiE "\bwhat('s| is)\b.{0,15}\b${CRED_NOUN_RX}\b\?[[:space:]]*$" && echo 1 || echo 0)
if [ "$CRED_ESCAPE_MISSING" = "1" ] && { { [ "$CRED_VN_MATCH" = "1" ] && [ "$CRED_HAS_DEST" = "1" ]; } || [ "$CRED_INTERROG_MATCH" = "1" ]; }; then
    echo "VIOLATION: You asked the user to paste/send a credential VALUE directly into chat (password / API key / token / connection string / credentials). A value typed into chat writes into the transcript FOREVER — it survives compaction and cannot be revoked. Use 'python3 ~/devel/airuleset/airuleset.py secret request <NAME>' (hand the user the printed URL, they paste it from their own browser) or 'secret exec <NAME> -- <cmd>' to hand it to a process without ever seeing the value. See receive-files-via-upload-url.md's credentials section." >&2
    add_hard "Direct credential-value request in chat — use 'airuleset.py secret request'/'secret exec', never ask the user to paste it here"
fi

# Same shape, stated in genuinely natural SLOVAK (no English loanword
# needed) — see the block comment above for the full rationale.
SK_CRED_VERB_RX="(po[sš]li|nap[íi][sš]|vlo[zž]|zdie[ľl]aj|skop[íi]ruj)"
SK_CRED_NOUN_RX="(hesl[oau]|token(u|om)?|api[[:space:]]+k[ľl][uú][čc][a]?|prihlasovacie[[:space:]]+[uú]daje)"
SK_CRED_DEST_RX="\b(mi|sem|chatu|chate)\b"
SK_CRED_INTERROG_RX="(ak[eé] je|[cč]o je( to)?)"
SK_CRED_VN_MATCH=$(LC_ALL=C.UTF-8 msg_has "$MSG_MENTION" -qiE "\b${SK_CRED_VERB_RX}\b.{0,20}\b${SK_CRED_NOUN_RX}\b|\b${SK_CRED_NOUN_RX}\b.{0,20}\b${SK_CRED_VERB_RX}\b" && echo 1 || echo 0)
SK_CRED_HAS_DEST=$(LC_ALL=C.UTF-8 msg_has "$MSG_MENTION" -qiE "$SK_CRED_DEST_RX" && echo 1 || echo 0)
SK_CRED_INTERROG_MATCH=$(LC_ALL=C.UTF-8 msg_has "$MSG_MENTION" -qiE "\b${SK_CRED_INTERROG_RX}\b.{0,15}\b${SK_CRED_NOUN_RX}\b\?[[:space:]]*$" && echo 1 || echo 0)
if [ "$CRED_ESCAPE_MISSING" = "1" ] && { { [ "$SK_CRED_VN_MATCH" = "1" ] && [ "$SK_CRED_HAS_DEST" = "1" ]; } || [ "$SK_CRED_INTERROG_MATCH" = "1" ]; }; then
    echo "VIOLATION: Požiadal si používateľa, aby vložil hodnotu credentialu (heslo / API kľúč / token / prihlasovacie údaje) priamo do chatu. Hodnota napísaná do chatu sa navždy zapíše do transkriptu — prežije kompakciu a nedá sa odvolať. Použi 'python3 ~/devel/airuleset/airuleset.py secret request <NAME>' (pošli používateľovi vypísanú URL, hodnotu vloží z vlastného prehliadača) alebo 'secret exec <NAME> -- <cmd>' na odovzdanie hodnoty procesu bez toho, aby si ju niekedy videl. Pozri sekciu o credentialoch v receive-files-via-upload-url.md." >&2
    add_hard "Priama žiadosť o hodnotu credentialu v chate (SK) — použi 'airuleset.py secret request'/'secret exec', nikdy nežiadaj vloženie sem"
fi

# Check for a `secret show` one-shot URL delivered WITHOUT a ❓ marker
# (#879, owner directive 2026-09-05). The one-shot URL is perishable
# (default TTL 600 s) — delivering it as a bare report line is silent
# loss for an away owner (it scrolls off, expires, and the credential
# is never picked up). The ❓ marker routes it to the phone/footer U.
# 3-conjunct, fail-safe PASS: block only when ALL of:
#   (1) endpoint-ttl=<N>s AND jednorazov — the show-only discriminator
#       (cli_vault.py:584; `secret request` prints keep= instead);
#   (2) an https?:// URL anywhere in the message;
#   (3) NO ❓ NEEDS YOU / ❓ ASKED marker.
# Any conjunct absent → pass. Conjunct (2) keeps doctrine/hook-editing
# sessions that merely QUOTE the string from tripping (when no URL
# is present). Accepted residual: a report that quotes the CONCRETE
# digit form (endpoint-ttl=600s + jednorazové) AND carries a URL
# (PR/globe line) over-blocks — remedy: quote as endpoint-ttl=<N>s
# in doctrine prose. Over-block direction, safe for an irreversible
# loss gate (#634).
SHOW_TTL=$(msg_has "$MSG" -qiE "endpoint-ttl=[0-9]+s" && echo 1 || echo 0)
SHOW_ONESHOT=$(msg_has "$MSG" -qi "jednorazov" && echo 1 || echo 0)
SHOW_HAS_URL=$(msg_has "$MSG" -qiE "https?://" && echo 1 || echo 0)
SHOW_HAS_Q=$(msg_has "$MSG" -qiE "❓ NEEDS YOU|❓ ASKED" && echo 1 || echo 0)
if [ "$SHOW_TTL" = "1" ] && [ "$SHOW_ONESHOT" = "1" ] && [ "$SHOW_HAS_URL" = "1" ] && [ "$SHOW_HAS_Q" = "0" ]; then
    echo "VIOLATION: A \`secret show\` one-shot URL was delivered WITHOUT a ❓ marker. An away owner will never see it (it scrolls off, the TTL expires, the credential is lost). Deliver the URL inside a self-contained \`**Otázka — projekt …:**\` block ending \`❓ NEEDS YOU:\` (or \`❓ ASKED:\` if continuing other work) and label the ticket \`needs-owner-action\`. See receive-files-via-upload-url.md §'Delivering a CREDENTIAL TO the Owner' (#879)." >&2
    add_hard "One-shot credential URL (secret show) delivered without ❓ marker — use the ❓/needs-owner-action delivery path (#879)"
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

# #606 — owner-facing PILE of per-ticket asks in ONE turn (U tickets must be
# delivered STEP-BY-STEP, one full `**Otázka — projekt …:**` block at a time,
# never a summary list). Owner directive (2026-08-21): "nikdy nemam dostavat
# sumarne informacie u vsetkych U vzdy musis ist step by step". The doctrine
# lives in the always-on modules (user-questions-slovak.md +
# statusline-vocabulary.md U bullet) + the autopilot skill's #527 bullet; this
# is the mechanical backstop for the RELIABLY-detectable subset only.
#
# Signature (narrow ON PURPOSE — the ticket explicitly warns against false
# positives on legitimate STATUS REPORTS): the message is an owner-facing
# QUESTION turn (carries a `❓ NEEDS YOU`/`❓ ASKED` marker — reuse the
# HAS_QMARKER already computed for the SK-approval check above) AND it packs
# 3+ physical LINES that each carry a `#N …?` per-ticket ask (a `#N` followed
# within 120 chars, same line, by a `?`). A completion report ends with `✅`
# or `❓ Question:` (never NEEDS YOU/ASKED) and its `Closes #N` lines carry no
# `?`, so it never trips; a single compliant one-ticket question block has at
# most one `#N …?` line. `msg_count` counts MATCHING LINES (grep -c), and its
# `?` (undeterminable) verdict is SKIPPED here — a false-block on a legit
# status report is a real harm, so this incriminating count fails OPEN on an
# unanswerable grep rather than manufacturing a block (unlike the fail-CLOSED
# banned-phrase checks, which are cheap to reword). MSG_MENTION so a quoted /
# fenced `#N …?` example (this repo's own docs) is never counted.
#
# Accepted residuals (documented, not chased, per #319's own precedent): a
# pile crammed onto FEWER than 3 physical lines (all tickets on one line)
# escapes — the dominant incident shape is "one line each" (a bullet list);
# a per-line count naturally handles that shape and NOT the rarer crammed one.
# A pure SUMMARY status list with NO per-ticket `?` and NO ❓ marker is
# indistinguishable from a legitimate backlog enumeration and is left to the
# doctrine (and to message-status-marker.md's own marker gate) rather than a
# false-positive-prone heuristic. Non-distinct tickets (the SAME `#N …?` on 3
# lines) still count 3 — still a "pile" shape, so this is intended, not a gap.
if [ "$HAS_QMARKER" = "1" ]; then
    U_ASK_LINES=$(msg_count "$MSG_MENTION" -iE '#[0-9]+.{0,120}\?')
    case "$U_ASK_LINES" in
        '' | *[!0-9]*) : ;;  # '?' (undeterminable) or non-numeric — fail OPEN, no block
        *)
            if [ "$U_ASK_LINES" -ge 3 ]; then
                echo "VIOLATION: Doručil si OWNEROVI zhrnutý ZOZNAM ${U_ASK_LINES}+ tiketov, kde každý nesie vlastnú otázku (#N …?) — presne to, čo owner zakázal (2026-08-21): 'nikdy nemam dostavat sumarne informacie u vsetkych U, vzdy musis ist step by step'. Owner NEVIE dekódovať ticket-po-tikete otázky zo stlačeného zoznamu. Každý U člen sa doručuje ako VLASTNÝ celý, self-contained blok '**Otázka — projekt …:**' (2-4 vety úvod čo tá vec JE + prečo čaká na neho, možnosti s dôsledkami, JEDNA rozhodovacia ❓ linka), PO JEDNOM, na ďalší až keď je predošlý zodpovedaný. Raw '--waiting' tabuľka je LEN machine context, owenerovi sa nikdy nerenderuje. Prepíš správu: pošli len PRVÝ U člen ako celý blok. See user-questions-slovak.md ('Ask in SMALL parts') + statusline-vocabulary.md (U bullet) + skills/autopilot/SKILL.md (#527)." >&2
                add_hard "Owner-facing pile of ${U_ASK_LINES}+ per-ticket asks (#N …?) in one question turn — deliver U members STEP-BY-STEP, one full '**Otázka — projekt …:**' block at a time, never a summary list (#606)"
            fi
            ;;
    esac
fi

# #608 — owner-chat PROD-READ CAPITULATION: a claim to the owner that prod
# state cannot be read/verified/seen, with NO evidence of a self-service
# attempt in the SAME message. Third recurrence of the #500 class (montalu3
# 2026-08-21: told the owner "nevie na prode zistiť" though it had
# REFRESH-DEV-BOX-FROM-PROD). #516 gated only the `gk-request` FILING path
# (block-gk-request-without-selfservice.sh); the OWNER-CHAT path had no gate.
# Doctrine (the decision tree: 1. RO channel → 2. fresh prod copy →
# 3. UNVERIFIED last) stays in autonomous-verification.md; this makes the
# owner-chat claim mechanical.
#
# Shape mirrors the sibling tester-handoff detector above VERBATIM (#319
# methodology): newlines flattened to `_FLAT`, LC_ALL=C.UTF-8 forced on every
# grep (`\b` next to a diacritic is locale-dependent — this repo's own twice-
# documented gotcha), bounded `.{0,N}` windows, plain diacritic alternation in
# bracket classes (no lookaround — ERE has none). Two-boolean AND on the SK
# branch (CLUSTER + READ-CONTEXT) mirrors the credential detector's own
# established anti-false-positive shape, so a bare "neviem, na prode to
# necháme?" (a decision, no read verb) never fires. Escape is DISARMED — same
# as the tester-handoff branch — when the message carries self-service
# evidence (REFRESH-DEV-BOX-FROM-PROD / a fresh prod copy / Self-service-
# checked: / an RO-channel read / has_group/search_read) OR an explicit
# `UNVERIFIED:` line.
#
#   SK: a "can't" negation (nevie(m)/nedá/nedokáže) NEAR a PROD signal
#       (prod/prode/produkci*/produkčn*), AND a verify/read verb
#       (zisti/overi/pozri/vidieť/skontrolova/potvrdi/preveri/dohľada) near
#       PROD — a two-boolean AND (CLUSTER + VERIFY-CONTEXT). #608-review
#       CRITICAL x2: an earlier draft had a THIRD branch (see-negation near a
#       DATA noun) that flooded false positives (the ubiquitous "nevidím
#       dôvod" + any prod + a data-noun SUBSTRING `stav`⊂`nastaviť` etc.); it
#       was dropped — but round-2 found `nevidím`/`nevidno` were STILL in the
#       CANT set, so "Na prode nevidím žiadny problém … idem overiť na dev"
#       (a benign success report) still blocked via the verify branch. So the
#       SEE stem is now removed from CANT ENTIRELY: a "can't SEE" claim is a
#       documented residual (a governance gate fails toward NOT blocking); the
#       real incident carried "neviem/nedá … zistiť/overiť/potvrdiť", still
#       caught.
#   EN: (can't/cannot/unable to/no way to) + (verify/check/read/confirm/
#       inspect/determine) + prod, in the realistic orderings — plus the
#       specific "(can't) \bsee\b … what's on prod" idiom (bare "see" is a
#       metaphor magnet — "can't see why", `foresee` — so it counts ONLY
#       inside that literal on-prod idiom with a word boundary, never in the
#       verb set, and only for the "what's on prod" shape, never a bare
#       "…on prod"). `access` was DROPPED from the verb set (#608-review
#       MAJOR: "customers cannot access production" is an outage report, not a
#       capitulation).
#   Escape: the doctrine's OWN self-service method NAMES disarm — a fresh prod
#       copy (`REFRESH-DEV-BOX-FROM-PROD` / `čerstv* kópi` / `fresh cop|prod`),
#       the read-only handover account (`read-only handover|account|api` /
#       `RO kanál|channel|handover` / `has_group` / `search_read`), an explicit
#       `Self-service-checked:` line, or a bare `UNVERIFIED:`. #608-review
#       MAJOR: the bare `read-only`/`fresh cop` tokens were narrowed to these
#       method-name shapes so a capitulation that merely NAMES the RO channel
#       it gave up on ("RO read-only kanál vrátil 500") is NOT disarmed.
#
# Accepted residuals (documented, not chased, per #319): a PURE "can't see"
# claim ("na prode nevidím kontrolné kópie mailov") is NOT blocked (SEE dropped
# from CANT); an exotic synonym verb (beží/padá/dostal) can slip; "can't read
# the config/logs on prod" pairs read+prod on a non-state read whose subject is
# a script/user, not the agent (fail-closed, low harm — reading logs/config on
# prod is common enough that this over-blocks occasionally; the escape disarms
# it once self-service is attempted); an UNRELATED `UNVERIFIED:`/`has_group`
# mention disarms a real capitulation (message-scoped escape, same as the
# tester-handoff sibling); an owner-facing single-decision question that lists
# 3+ context tickets each ending a clause with "?" is treated as a per-ticket
# PILE by the #606 detector below, which is intended per the owner's "never a
# U summary" directive; the formal register (vykanie) and a decoy inside an
# interpreter heredoc body carry the same limitations every #319 detector in
# this file already has.
PC_CANT_SK='\b(nevie[mš]?|nevedia|nedok[áa][žz]e[mš]?|ned[áa])\b'
PC_VERIFY_SK='\b(zisti[ťt]|zist[íi][mš]?|overi[ťt]|over[íi][mš]?|pozrie[ťt]|pozri[mš]?|skontrolova[ťt]|vidie[ťt]|potvrdi[ťt]|potvrd[íi][mš]?|preveri[ťt]|doh[ľl]ada[ťt])\b'
PC_PROD_SK='\b(prode?|produkci[a-z]*|produk[čc]n[a-z]*)\b'
PC_SK_A="(${PC_CANT_SK}).{0,40}(${PC_PROD_SK})|(${PC_PROD_SK}).{0,40}(${PC_CANT_SK})"
PC_SK_B="(${PC_VERIFY_SK}).{0,60}(${PC_PROD_SK})|(${PC_PROD_SK}).{0,60}(${PC_VERIFY_SK})"
PC_CANT_EN="\b(can'?t|cannot|can[[:space:]]+not|could[[:space:]]?n'?t|couldn'?t|unable[[:space:]]+to|no[[:space:]]+way[[:space:]]+to|have[[:space:]]+no[[:space:]]+way)\b"
PC_VERIFY_EN='\b(verif[a-z]*|check[a-z]*|read|confirm[a-z]*|inspect[a-z]*|determine[a-z]*)\b'
PC_PROD_EN='\b(prod|production)\b'
PC_EN_MAIN="(${PC_CANT_EN}).{0,25}(${PC_VERIFY_EN}).{0,50}${PC_PROD_EN}|${PC_PROD_EN}.{0,50}(${PC_CANT_EN}).{0,25}(${PC_VERIFY_EN})|(${PC_VERIFY_EN}).{0,25}(${PC_CANT_EN}).{0,50}${PC_PROD_EN}|${PC_PROD_EN}.{0,50}(${PC_VERIFY_EN}).{0,25}(${PC_CANT_EN})"
PC_EN_IDIOM="(${PC_CANT_EN}).{0,20}\bsee\b.{0,15}what'?s[[:space:]]+on[[:space:]]+prod"
PC_ESCAPE='REFRESH-DEV-BOX-FROM-PROD|[čc]erstv[a-záäéíóôúý]*[[:space:]]+k[óo]pi|fresh[[:space:]]+(cop|prod)|read-only[[:space:]]+(handover|account|api)|Self-service-checked|has_group|search_read|RO[[:space:] -]?(kan[áa]l|channel|tunel|tunnel|handover)|UNVERIFIED:'
PC_FLAT=$(tr '\n' ' ' <<<"$MSG_MENTION") || PC_FLAT="$MSG_MENTION"
PC_MATCH=0
if LC_ALL=C.UTF-8 msg_has "$PC_FLAT" -qiE "$PC_SK_A" && \
    LC_ALL=C.UTF-8 msg_has "$PC_FLAT" -qiE "$PC_SK_B"; then
    PC_MATCH=1
fi
if [ "$PC_MATCH" = "0" ]; then
    if LC_ALL=C.UTF-8 msg_has "$PC_FLAT" -qiE "$PC_EN_MAIN" || \
        LC_ALL=C.UTF-8 msg_has "$PC_FLAT" -qiE "$PC_EN_IDIOM"; then
        PC_MATCH=1
    fi
fi
if [ "$PC_MATCH" = "1" ]; then
    # Exonerating: self-service evidence / UNVERIFIED present -> DISARM.
    # msg_missing (unknown -> deny the exemption, fail-closed) matches this
    # file's #194 taxonomy for an EXONERATING pattern, same as the
    # tester-handoff branch's own UNVERIFIED escape. On PC_FLAT (not
    # MSG_MENTION) so a hard-wrapped self-service phrase still disarms
    # (#608-review MINOR #11).
    if LC_ALL=C.UTF-8 msg_missing "$PC_FLAT" -qiE "$PC_ESCAPE"; then
        echo "VIOLATION: Povedal si OWNEROVI, že sa niečo na PRODE nedá zistiť/overiť/pozrieť/nevidíš — bez akéhokoľvek dôkazu, že si skúsil self-service cestu. Toto je tretí výskyt triedy #500 (montalu3 2026-08-21). 'Neviem na prode X zistiť' NIE JE poctivé UNVERIFIED pre prod-STATE READ — má self-service odpoveď. Decision tree (autonomous-verification.md → 'What''s on PROD?'): 1. vlastný read-only kanál (RO handover účet, has_group/search_read — pri 500 ČÍTAJ telo a skús užšiu metódu, nikdy sa nevzdaj po jednom 500); 2. ČERSTVÁ KÓPIA produ na vlastnom boxe (REFRESH-DEV-BOX-FROM-PROD → priame čítanie mail_mail/mail_message/DB, ~20-40 min); 3. UNVERIFIED až posledné, a LEN po vyčerpaní 1 aj 2. Sprav to a napíš čo si zistil — alebo, ak si cesty naozaj vyčerpal, napíš 'UNVERIFIED: <čo> — <vyčerpaná self-service cesta>'." >&2
        echo "" >&2
        echo "  See autonomous-verification.md → '\"What's on PROD?\" is a SELF-SERVICE question' (decision tree)." >&2
        add_hard "Owner-chat prod-read capitulation ('neviem na prode zistiť/overiť' / 'cannot verify on PROD') with NO self-service evidence — try the RO channel, then a fresh prod copy (REFRESH-DEV-BOX-FROM-PROD); UNVERIFIED only after both. Or write 'UNVERIFIED:' with the exhausted self-service path. (#608 / #500 class)"
    fi
fi

# #631 — owner-chat CLOUDFLARE CREDENTIAL-INVALID claim with NO capability
# probe. The owner deleted his master Cloudflare token from Bitwarden because a
# session declared it invalid, and Cloudflare never shows a token value twice —
# so it is gone. The claim was wrong: `GET /user/tokens/verify` returns
# `Invalid API Token` for account-owned `cfat_` tokens BY DESIGN. The knowledge
# (skills/cloudflare-api-tokens §0/§2) existed and did not reach the moment of
# decision. This makes the owner-chat claim mechanical — the structural sibling
# of the #608 prod-read capitulation gate above.
#
# MATCH runs on CF_FLAT (the MENTION-stripped message, MSG_MENTION) so a message
# that merely QUOTES the error string in backticks/double-quotes (`Invalid API
# Token`) or discusses the endpoint in the abstract has the cluster STRIPPED and
# does NOT fire — the #96 use-vs-mention discipline and the ticket's own boundary.
# ESCAPE runs on CF_RAW_FLAT (the RAW message) because the probe that disarms is
# a curl that normally lives inside a code fence / quoted string that MSG_MENTION
# would strip — the deliberate difference from #608 (whose escape tokens are
# prose markers). Acceptance (b): "the same message carrying a probe passes".
#
# The MATCH requires a CLOUDFLARE-QUALIFIED credential (CF_QCRED — a cloudflare/
# cfat/wrangler SIGNAL adjacent, within 30 chars, to a CREDENTIAL noun, OR the
# self-qualifying `cfat` prefix) within 60 chars of an INVALID/non-functional
# VERDICT. Tying the cloudflare context DIRECTLY to the credential (not two
# independent "cloudflare-anywhere AND invalid-somewhere" booleans) is what
# stops the cross-service / unrelated-topic over-block a fresh-context
# adversarial review demonstrated (#631 review r2 MAJOR-1: "the GitHub token is
# invalid … separately, the Cloudflare DNS is set up" must NOT block; "moved the
# site behind Cloudflare … the sort key is invalid" must NOT block).
#   CF_QCRED  : cfat | (cloudflare|cfat|wrangler).{0,30}(token|kľúč|credential|
#               api-key) | the reverse ordering.
#   CF_INVALID: SK neplatn*/nefunk*/nefunguje/nefungoval*; EN invalid/not valid/
#               isn't valid/doesn't work/not working/won't work/no longer works.
#               DELIBERATELY EXCLUDES revoked/rejected/expired/odmiet/zrušen/
#               zamietnut/expirovan/vypršal/mŕtv — those are action- or
#               state-overloaded and fire in BENIGN hygiene/rotation/success
#               reports ("I revoked the old token", "the old token was expired,
#               I rotated it", "Cloudflare rejected the request, fixed the
#               scope") — #631 review r2 MAJOR-3. The verify-endpoint LIE this
#               gate targets produces "invalid" / "doesn't work" / "neplatný" /
#               "nefunguje", which is exactly the retained set.
#   Escape    : a REAL capability probe — `…/v4/zones`, an
#               `accounts/{id}/tokens/verify`, the phrase "capability probe" — or
#               an explicit `UNVERIFIED:`. DELIBERATELY NOT `/user/tokens/verify`
#               (treating that endpoint's answer as a verdict IS the error): the
#               `accounts/` prefix + the `[^/]` class in CF_PROBE cannot span the
#               `/user/` segment, so the trap endpoint can never disarm.
#
# Accepted residuals (documented, not chased, per #319 — honesty bar, r2 MINOR):
#   OVER-block (fail-safe — a credential whose false-negative is irreversible
#   fails toward MORE scrutiny; cheap fix is backtick / add a probe / UNVERIFIED):
#     - a bare-prose ABSTRACT discussion that does NOT backtick its error string
#       ("the verify endpoint returns invalid for cfat tokens by design");
#     - a "not invalid" double negative (contains the `invalid` substring);
#     - a cloudflare SIGNAL that happens to sit within 30 chars of an unrelated
#       credential noun that itself sits within 60 of an unrelated `invalid`
#       (tighter than a decoupled match, but still possible in one dense clause);
#     - the escape is message-scoped, so an UNRELATED `v4/zones`/`UNVERIFIED:`
#       mention disarms a real claim (same as the #608 sibling).
#   UNDER-block (misses a real claim — cheap to write the same claim plainly):
#     - the credential noun or the cloudflare signal QUOTED inside backticks /
#       double-quotes is stripped, so `The \`Cloudflare token\` is invalid` slips
#       (the inherent use-vs-mention cost; the bare-prose incident is caught);
#     - a single-quoted error string is NOT stripped by strip_mentions (it strips
#       only backticks/double-quotes/fences), so a single-quoted mention can
#       over-block — inherited, not #631-specific;
#     - a verdict more than 60 chars from the qualified credential.
CF_SIG='cloudflare|cfat|\bwrangler\b'
CF_CREDNOUN='token[a-z]*|k[ľl][úu][čc][a-z]*|credential[a-z]*|api[[:space:]-]*key[a-z]*'
CF_QCRED="cfat|(${CF_SIG}).{0,30}(${CF_CREDNOUN})|(${CF_CREDNOUN}).{0,30}(${CF_SIG})"
CF_INVALID_SK='neplatn[a-z]*|nefunk[čc]n[a-z]*|nefunguje|nefungoval[a-z]*'
CF_INVALID_EN='invalid|not[[:space:]]+valid|isn.?t[[:space:]]+valid|does[[:space:]]?n.?t[[:space:]]+work|do[[:space:]]+not[[:space:]]+work|not[[:space:]]+working|won.?t[[:space:]]+work|no[[:space:]]+longer[[:space:]]+works?'
CF_INVALID="(${CF_INVALID_SK})|(${CF_INVALID_EN})"
CF_MATCH_RE="((${CF_INVALID}).{0,60}(${CF_QCRED}))|((${CF_QCRED}).{0,60}(${CF_INVALID}))"
CF_PROBE='v4/zones|accounts/[a-zA-Z0-9._-]+/tokens/verify|capabilit[a-z]*[[:space:]]+probe|UNVERIFIED:'
# #634 -- NARRATION-CONTEXT disarm. The #631 MATCH fires on the invalid-credential
# CLUSTER whether the message ASSERTS the verdict live ("Cloudflare token je
# neplatný, vygeneruj nový") or merely DESCRIBES/QUOTES it (a summary of what the
# gate does, an incident post-mortem, a playbook lesson) -- so a retrospective
# sentence about THIS very gate over-blocked the supervisor twice, and would recur
# in every future summary/playbook/report on the topic. #631's own comment already
# lists this bare-prose case as an accepted OVER-block residual; #634 closes it.
#
# The fix RHYMES with #631: #631 refused to fire on two loose booleans and tied
# the cloudflare SIGNAL to the credential via ADJACENCY; here a NARRATIVE FRAME
# adjacent (within 64 chars, the #631 60-char scale) to the cluster DISARMS -- the
# message is describing/quoting the claim, not asserting a live verdict. Run on
# CF_FLAT (the SAME mention-stripped flattened text the cluster is matched on) so
# the adjacency is measured against the cluster's own position; via msg_missing
# (UNKNOWN -> "missing" -> proceed to BLOCK), a grep error fails toward MORE
# scrutiny -- the correct fail-safe for a gate guarding an irreversible loss.
#
# A "frame" is deliberately a FRAMING CONSTRUCT, never a bare topic noun -- a
# genuine live verdict-to-owner essentially never carries one adjacent to the
# cluster, whereas "gate"/"incident"/"hook" ALONE routinely sit near unrelated
# prose (an adversarial live claim that mentions unrelated gate/incident/hook work
# must still block). The five families:
#   NF_FRAMENOUN: the cluster is the OBJECT of a describing/quoting frame
#                 ("správa, ktorá" / "message that|which" / "tvrdenie" /
#                 "a claim that" / "the claim" / "prose that").
#   NF_SUBJDECL : a subject NOUN declares it ("session tvrdila" / "správa vyhlási"
#                 / "a session declared") -- session|správa within 20 of a
#                 declare verb.
#   NF_CONDREL  : a conditional/relative frame governs a declare verb ("keď ...
#                 vyhlási" / "when a message says" / "that ... claims"). \bif\b /
#                 \bwhen\b anchored so "verify"/"modify" never match; the trap
#                 endpoint /user/tokens/verify carries NO declare verb, so the
#                 #631 incident case still blocks.
#   NF_GATEACT  : a gate/hook/detector that BLOCKS/PREVENTS/FIRES/CATCHES ("brána
#                 blokuje", "gate blocks", "detektor zablokuje", "brána chytí") --
#                 the bare noun is folded into a compound with a block verb, so
#                 "git hook na commit" / "leak detector" (no block verb) never
#                 disarm.
#   NF_TOPIC    : narration-specific dev-process TOPIC words that ~never sit near a
#                 live token verdict (playbook / over-block / stop-check /
#                 post-mortem / retrospective). Bare "incident"/"hook"/"gate" and a
#                 bare "#631"/"#634" ticket ref are DELIBERATELY EXCLUDED (common
#                 words / a live claim may sit next to the ticket it works on) --
#                 no real narration needs them standalone (each also carries a
#                 frame/subject/topic marker).
#
# Accepted residuals (documented, not chased, per #319 -- honesty bar):
#   OVER-block (fail-safe -- the credential's false-negative is irreversible;
#   cheap fix is backtick / add a probe / reword):
#     - narration whose only marker sits > 64 chars from the cluster;
#     - a narration marker QUOTED inside backticks / double quotes is stripped by
#       strip_mentions (inherited #96 use-vs-mention cost);
#     - a gate word paired with an unusual block verb outside the compound's set.
#   UNDER-block (misses a real claim -- the disarm is a FRAMING construct, so this
#   needs an adversarially-shaped verdict):
#     - a live verdict that puts a framing construct within 64 chars of the cluster
#       (e.g. "the gate blocks it and the Cloudflare token is invalid") disarms --
#       rare, and the author has to construct it; the message-scoped alternative
#       (rejected) would under-block on any unrelated gate/incident mention.
NF_FRAMENOUN='spr[aá]v.{0,3}[[:space:]](ktor|[žz]e)|message.{0,3}[[:space:]](that|which)|tvrdeni|a[[:space:]]claim[[:space:]]that|the[[:space:]]claim|prose[[:space:]]that'
NF_SUBJDECL='(session|spr[aá]v).{0,20}(tvrd|vyhl[aá]s|declar)'
NF_TOPIC='playbook|over-?block|stop-check|post-?mortem|retrospekt|retrospective|\blekcia\b|\blesson\b'
CF_NARR_SIG="${NF_FRAMENOUN}|${NF_SUBJDECL}|${NF_TOPIC}"
CF_NARR_ADJ_RE="((${CF_NARR_SIG}).{0,64}(${CF_MATCH_RE}))|((${CF_MATCH_RE}).{0,64}(${CF_NARR_SIG}))"
# #634-review -- LIVE CREDENTIAL-ACTION override. Both fresh-context reviews found
# the narration disarm fail-UNSAFE: a live verdict-to-owner naturally carries a
# frame word ("Per the playbook, the token is invalid, regenerate it"; "the
# endpoint says the token is invalid, vygeneruj nový") near the cluster, so the
# disarm let a real unprobed claim -- incl. the exact incident relay -- pass. The
# clean separator BOTH reviews converged on: a live verdict ASKS THE OWNER TO ACT
# ON THE CREDENTIAL (regenerate / make a new one / need a new one / vygeneruj /
# treba nový) -- the precise directive that made the owner delete his token --
# while narration ABOUT the gate/incident/playbook never does. So a CF_ACTION
# directive DISARMS THE DISARM: present -> BLOCK regardless of any narration frame.
# Message-scoped (a directive ANYWHERE re-blocks -- the fail-safe direction for an
# irreversible loss) and checked on CF_RAW_FLAT so a directive stays visible even
# if oddly quoted. DELIBERATELY directive-only (regenerate/create-new/need-new
# imperatives), NOT bare "new token" nor a PAST-tense report ("owner deleted it",
# "vygeneroval som nový") -- a completed-action recount is narration, not a live ask.
# Also from review: NF_CONDREL (bare when/if/ktorá + declare verb) and NF_GATEACT
# ("gate blocks"/"hook fires") were DROPPED -- they matched the endpoint-relay
# incident ("endpoint tvrdí, že ... neplatný") and live deploy status ("the hook
# blocks the release because ... invalid"); no narration fixture needs them (each
# is also carried by a frame-noun/subject-declare/topic marker), so dropping them
# removes an under-block surface at zero cost to the passing set.
#
# Accepted residuals (documented, not chased, per #319 -- fail-safe = OVER-block):
#   - imperative-less narration-framed live-ish verdicts ("this session declares
#     the token invalid" with NO directive) still disarm -- genuinely ambiguous
#     English; the actionable (harmful) form is caught by CF_ACTION;
#   - near-neighbour narration verbs a supervisor might write (prevents / message
#     saying|claiming / reported / uviedla|oznámila|povedala / a bare "nikdy
#     nevyhlás ..." with no Lekcia/playbook word) still OVER-block -- the safe
#     direction, cheap to reword/backtick, NOT the core recurrence the ticket
#     targets (summary / incident post-mortem / playbook lesson / gate description
#     all pass);
#   - a narration that QUOTES a remediation directive ("... regenerate it AFTER a
#     probe") over-blocks via CF_ACTION -- fail-safe, cheap to backtick.
CF_ACTION_RE='regenerat[a-z]*|re-?generate|\brotate\b|\brevoke\b|re-?issue[a-z]*|make[[:space:]]+a[[:space:]]+new|create[[:space:]]+a[[:space:]]+new|generate[[:space:]]+a[[:space:]]+new|issue[[:space:]]+a[[:space:]]+new|get[[:space:]]+a[[:space:]]+new|need[[:space:]]+a[[:space:]]+(new|fresh)|ask[[:space:]]+(you[[:space:]]+)?for[[:space:]]+a[[:space:]]+new|vygeneruj[a-z]*|vygenerova[ťt]|potrebujem[[:space:]]+nov[a-z]*|treba[[:space:]]+nov[a-z]*|treba[[:space:]].{0,15}vygenerova|sprav[[:space:]]+nov[a-z]*|vytvor[[:space:]]+nov[a-z]*|vyrob[[:space:]]+nov[a-z]*|rotuj[a-z]*'
CF_FLAT=$(tr '\n' ' ' <<<"$MSG_MENTION") || CF_FLAT="$MSG_MENTION"
CF_RAW_FLAT=$(tr '\n' ' ' <<<"$MSG") || CF_RAW_FLAT="$MSG"
CF_MATCH=0
# Cheap pre-gate: only a cloudflare-mentioning (mention-stripped) message pays
# the full match. CF_MATCH_RE already requires a cloudflare-qualified credential,
# so this only short-circuits the common non-cloudflare message.
if LC_ALL=C.UTF-8 msg_has "$CF_FLAT" -qiE "$CF_SIG"; then
    if LC_ALL=C.UTF-8 msg_has "$CF_FLAT" -qiE "$CF_MATCH_RE"; then
        CF_MATCH=1
    fi
fi
if [ "$CF_MATCH" = "1" ]; then
    # Exonerating: a capability probe / UNVERIFIED in the message -> DISARM.
    # msg_missing (unknown -> deny the exemption, fail-closed) is the same #194
    # taxonomy the #608/tester-handoff escapes use. On CF_RAW_FLAT so a
    # code-fenced curl probe still disarms (acceptance b). #634 adds a narration
    # disarm on CF_FLAT (a NARRATIVE FRAME adjacent to the cluster -- the message
    # DESCRIBES/QUOTES the claim), OVERRIDDEN by the #634-review CF_ACTION guard: a
    # live credential-action directive (regenerate/vygeneruj/treba nový/...) means
    # a real ASK on the owner, so it re-BLOCKS even when a narration frame is
    # present. BLOCK when: no probe AND (no narration frame OR a live-action
    # directive). Every msg_* here fails toward BLOCK on an UNKNOWN grep
    # (msg_missing UNKNOWN->"missing", msg_has UNKNOWN->"present"), the correct
    # fail-safe for a gate guarding an irreversible credential loss.
    CF_DO_BLOCK=0
    if LC_ALL=C.UTF-8 msg_missing "$CF_RAW_FLAT" -qiE "$CF_PROBE"; then
        CF_DO_BLOCK=1
        if LC_ALL=C.UTF-8 msg_missing "$CF_FLAT" -qiE "$CF_NARR_ADJ_RE"; then
            : # no narration frame -> stays a BLOCK
        elif LC_ALL=C.UTF-8 msg_has "$CF_RAW_FLAT" -qiE "$CF_ACTION_RE"; then
            : # narration frame present BUT a live credential-action directive -> stays a BLOCK
        else
            CF_DO_BLOCK=0 # narration frame, no directive -> narration, DISARM
        fi
    fi
    if [ "$CF_DO_BLOCK" = "1" ]; then
        echo "VIOLATION: Vyhlásil si Cloudflare credential (token/kľúč) za neplatný/nefunkčný v správe pre OWNERA — BEZ doloženého CAPABILITY PROBE. Presne takto sa dnes STRATIL master token: owner ho zmazal z Bitwardenu na základe tohto tvrdenia a Cloudflare hodnotu tokenu už nikdy nezobrazí. \`GET /user/tokens/verify\` vracia \`Invalid API Token\` pre účtovo-viazané \`cfat_\` tokeny BY DESIGN — jeho odpoveď NIE JE verdikt (brať ju ako verdikt JE tá chyba). Než vyhlásiš token za neplatný, MUSÍŠ v tej istej správe ukázať CAPABILITY PROBE proti reálnemu zdroju: \`GET https://api.cloudflare.com/client/v4/zones\` (success:true + zóny = token funguje) ALEBO \`GET /accounts/<account_id>/tokens/verify\`. Ak probe naozaj zlyhal, ukáž ho + jeho výstup; ak ho nevieš spustiť, napíš \`UNVERIFIED: <čo> — <čo si skúsil>\`. NIKDY neber odpoveď z \`/user/tokens/verify\` ako verdikt." >&2
        echo "" >&2
        echo "  See skills/cloudflare-api-tokens §0/§2 (the verify endpoint LIES for cfat_ tokens; the /zones capability probe is the only verdict)." >&2
        add_hard "Owner-chat Cloudflare credential-invalid claim ('token je neplatný/nefunguje' / 'the Cloudflare token is invalid') with NO capability probe — show a GET /zones (or /accounts/<account_id>/tokens/verify) probe + its output, or write 'UNVERIFIED:'. NEVER treat /user/tokens/verify as a verdict (it returns Invalid API Token for cfat_ tokens BY DESIGN). (#631 / #500 class — the owner lost his master token this way)"
    fi
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
# Goal/outcome pair: `**Úloha:**`/`**Riešenie:**` (#1137) — only as a BOLD
# label at line start, so a ❓ message that merely says "Úloha: …" is never
# forced into the template — or the old `**Goal:**`/`**What changed:**`.
# Alternation, never a bracket class, for the multibyte letters (a [] class
# matches per BYTE under a bare C locale).
GOAL_RX='^[[:space:]]*\*\*(ú|u)loha:?\*\*|\*\*goal:?\*\*|^goal:'
OUTCOME_RX='^[[:space:]]*\*\*rie(š|s)enie:?\*\*|\*\*(what changed|outcome):?\*\*|^(what changed|outcome):'
HAS_GOAL_AND_OUTCOME=0
if LC_ALL=C.UTF-8 msg_has "$MSG" -qiE "$GOAL_RX" && LC_ALL=C.UTF-8 msg_has "$MSG" -qiE "$OUTCOME_RX"; then
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
    echo "VIOLATION: Your message is a completion report but does NOT start with the canonical heading '## ✅ Work Complete' — a prose summary bypasses the verification gates. Rewrite it with the template (completion-report.md):" >&2
    echo "    ## ✅ Work Complete" >&2
    echo "    **Úloha:** <čo sa chcelo>" >&2
    echo "    **Riešenie:** <ako sa to vyriešilo>  🌐 <url ak má práca UI>" >&2
    echo "    ✅ Overené: plan N/N · review 0 🔴 0 🟡 0 🔵 · deep 0 🔴 0 🟡 0 🔵 · arch <oblasť> OK · Výstup: <hodnoty | n/a — prečo> · playbook: <lekcia | none>" >&2
    echo "    ✅ DONE: <jeden riadok>" >&2
    echo "  Hand-off streams: Riešenie names the READY-FOR-REVIEW hand-off (+ --handoff karta)." >&2
    add_hard "Prose completion report missing canonical '## ✅ Work Complete' heading — use the full template, not a prose summary"
fi

# Completion-report fields (#1137, issue 1233: the owner reads only what the task
# was and how it was solved, so the report is four lines; every gate behind it
# stays). ONE `✅ Overené:` line carries the verification tokens, separated by ` · `:
#   plan N/N · review 0 🔴 0 🟡 0 🔵 · deep 0 🔴 0 🟡 0 🔵 · arch <area> OK|REWORK fixed|REWORK #N
#   · Výstup: <observed values | n/a — why> · playbook: <lesson | none>
# The LAST such line (optional `- ` / `> ` / `**` prefix) is the report's own, so a
# template quoted above it is never read. A token the line CARRIES is judged on
# its own segment only; the pre-#1137 forms (the `/plan-check` audit line,
# `✅ Výstup:`, `🏛 Architektúra:`, `📔 Playbook:`, `**Goal:**`/`**What changed:**`)
# answer only for a token the Overené line does not carry, and never read an
# Overené line (no borrowing the deep pass's zeros for a dishonest review). The
# presence probes are REQUIRED-FIELD msg_has (fail OPEN, #194); the Výstup
# substance probes fire only on positively obtained text. LC_ALL=C.UTF-8 wherever
# a pattern carries a diacritic (#316).
if [ "$IS_COMPLETION" = "1" ]; then
    OVR_RX='✅[[:space:]]*\**[[:space:]]*(overen(é|e)|verified)[[:space:]]*\**[[:space:]]*:'
    OVR_LINE_RX="^[[:space:]]*((>|[-*+][[:space:]])[[:space:]]*)*(\*\*)?[[:space:]]*${OVR_RX}"
    OVR_LINE=""
    if _OVR_LINES=$(LC_ALL=C.UTF-8 msg_lines "$MSG" -iE "$OVR_LINE_RX"); then
        OVR_LINE=$(tail -1 <<<"$_OVR_LINES")
    fi
    # The old-form probes read the message WITHOUT any Overené line.
    MSG_OLD="$MSG"
    if [ -n "$OVR_LINE" ]; then
        _MO_RC=0
        MSG_OLD=$(LC_ALL=C.UTF-8 grep -viE "$OVR_LINE_RX" <<<"$MSG" 2>/dev/null) || _MO_RC=$?
        [ "$_MO_RC" -ge 2 ] && MSG_OLD="$MSG"
    fi
    # Segments of the Overené line: the label stripped, split on the literal ` · `.
    OVR_SEGS=()
    if [ -n "$OVR_LINE" ]; then
        _OB=$(LC_ALL=C.UTF-8 sed -E "s/^.*${OVR_RX}//I" <<<"$OVR_LINE" 2>/dev/null) || _OB="$OVR_LINE"
        while [[ "$_OB" == *" · "* ]]; do
            OVR_SEGS+=("${_OB%%" · "*}")
            _OB="${_OB#*" · "}"
        done
        OVR_SEGS+=("$_OB")
    fi
    # _ovr_seg <token-ERE> — prints the first segment STARTING with the token
    # (leading/trailing blanks and `*` trimmed); rc 1 = the line does not carry it.
    _ovr_seg() {
        local s
        [ "${#OVR_SEGS[@]}" -gt 0 ] || return 1
        for s in "${OVR_SEGS[@]}"; do
            s="${s#"${s%%[![:space:]*]*}"}"
            s="${s%"${s##*[![:space:]*]}"}"
            if LC_ALL=C.UTF-8 msg_has "$s" -qiE "^($1)"; then
                printf '%s' "$s"
                return 0
            fi
        done
        return 1
    }
    # _c3_exact <segment> <token-ERE> — the counters right after the token are
    # exactly `0 🔴 0 🟡 0 🔵` and no further counter follows them.
    _c3_exact() {
        LC_ALL=C.UTF-8 msg_has "$1" -qiE "^(${2})${_C3}" || return 1
        local rest="${1#*🔵}"
        [[ "$rest" != *🔴* && "$rest" != *🟡* && "$rest" != *🔵* ]]
    }
    _C3='[[:space:]]*:?[[:space:]]*0[[:space:]]*🔴[[:space:]]*0[[:space:]]*🟡[[:space:]]*0[[:space:]]*🔵'
    HAS_GOAL=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qiE "$GOAL_RX" && echo 1 || echo 0)
    HAS_OUTCOME=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qiE "$OUTCOME_RX" && echo 1 || echo 0)

    # plan N/N — N must equal M.
    if _S=$(_ovr_seg '/?plan(.?check)?\b'); then
        HAS_PLAN_CHECK=0
        _NM=$(sed -nE 's#^/?[Pp][Ll][Aa][Nn]([^[:space:]0-9]*)?:?[[:space:]]+([0-9]+)[[:space:]]*/[[:space:]]*([0-9]+).*$#\2 \3#p' <<<"$_S" 2>/dev/null || true)
        _N="${_NM% *}"
        _M="${_NM#* }"
        if [ -n "$_NM" ] && [ "$((10#$_N))" -eq "$((10#$_M))" ]; then
            HAS_PLAN_CHECK=1
        fi
    else
        # Old forms. Bounded/anchored as before.
        HAS_PLAN_CHECK=$(msg_has "$MSG_OLD" -qiE "/plan.?check|plan-check.*(fulfilled|passed|clean|complete)|✅.*plan.?check" && echo 1 || echo 0)
    fi
    if _S=$(_ovr_seg '/?review\b'); then
        HAS_REVIEW=$(_c3_exact "$_S" '/?review' && echo 1 || echo 0)
    else
        # HAS_REVIEW's gaps are BOUNDED (`.{0,300}`, #194: an unbounded
        # `.*A.*B.*C` hits PCRE's backtracking limit on a crafted line);
        # `(?<!code-)` keeps /requesting-code-review out of the /review probe.
        HAS_REVIEW=$(msg_has "$MSG_OLD" -qP '(?<!code-)/review[: ].{0,300}0 🔴.{0,300}0 🟡.{0,300}0 🔵|(?<!code-)/review[: ].{0,300}all (findings|issues|items).{0,300}addressed|(?<!code-)/review[: ].{0,300}addressed in commit' && echo 1 || echo 0)
    fi
    if _S=$(_ovr_seg '/?deep\b'); then
        HAS_RCR=$(_c3_exact "$_S" '/?deep' && echo 1 || echo 0)
    else
        HAS_RCR=$(msg_has "$MSG_OLD" -qiE "requesting.?code.?review.*0 🔴.*0 🟡.*0 🔵|requesting.?code.?review.*all (findings|issues|items).*addressed|requesting.?code.?review.*addressed in commit|✅.*superpowers:requesting.*0 🔴.*0 🟡.*0 🔵|✅.*request.?code.?review.*0 🔴.*0 🟡.*0 🔵|✅.*code.?review.*\(deep\).*0 🔴.*0 🟡.*0 🔵" && echo 1 || echo 0)
    fi
    # arch <area> OK | REWORK fixed | REWORK #N (an optional `(topic)` after #N);
    # a negated `NOT OK` / `nie OK` is no verdict.
    if _S=$(_ovr_seg 'arch\b'); then
        HAS_ARCH=0
        if LC_ALL=C.UTF-8 msg_has "$_S" -qE '^[Aa][Rr][Cc][Hh][[:space:]]+[^[:space:]].*[[:space:]](OK|REWORK[[:space:]]+fixed|REWORK[[:space:]]+#[0-9]+([[:space:]]*\(.*\))?)$' \
           && ! LC_ALL=C.UTF-8 msg_has "$_S" -qiE '[[:space:]](not|nie|no|non|ne)[[:space:]]+OK$'; then
            HAS_ARCH=1
        fi
    else
        # #993-review: U+1F3DB is TEXT-default, so it often arrives with VS16 (U+FE0F).
        _ARCH_VS16=$'\xef\xb8\x8f'
        ARCH_RX='🏛('"$_ARCH_VS16"')?[[:space:]]*\**[[:space:]]*(architekt(ú|u)ra|architecture)[[:space:]]*\**[[:space:]]*:'
        HAS_ARCH=$(LC_ALL=C.UTF-8 msg_has "$MSG_OLD" -qiE "$ARCH_RX" && echo 1 || echo 0)
    fi
    # Playbook proof (folded in from the retired stop-check-playbook-review.sh,
    # same scope: heading reports only).
    HAS_PLAYBOOK=1
    if [ "$IS_COMPLETION_HEADING" = "1" ]; then
        if _ovr_seg '/?playbook[[:space:]]*:' >/dev/null; then
            HAS_PLAYBOOK=1
        else
            HAS_PLAYBOOK=$(msg_has "$MSG_OLD" -qE "📔 Playbook:" && echo 1 || echo 0)
        fi
    fi

    # Výstup (#446, montalu3 0 € emails): the value after `Výstup:` up to the
    # next ` · ` (its own segment), else the old `✅ Výstup:` line (line-anchored
    # first, so a backticked MENTION of the template earlier is not judged).
    VYSTUP_RX='(v(ý|y)stup|output)[[:space:]]*\**[[:space:]]*:'
    OLD_VYSTUP_RX="✅[[:space:]]*\**[[:space:]]*${VYSTUP_RX}"
    # VYSTUP_VAL stays EMPTY when the value could not be read (the substance
    # probes then skip); a value read back as empty becomes " " (value-free).
    VYSTUP_VAL=""
    HAS_VYSTUP=0
    if _S=$(_ovr_seg "$VYSTUP_RX"); then
        HAS_VYSTUP=1
        if _VV=$(LC_ALL=C.UTF-8 sed -E "s/^${VYSTUP_RX}[[:space:]]*//I" <<<"$_S" 2>/dev/null); then
            VYSTUP_VAL="${_VV:- }"
        fi
    else
        HAS_VYSTUP=$(LC_ALL=C.UTF-8 msg_has "$MSG_OLD" -qiE "$OLD_VYSTUP_RX" && echo 1 || echo 0)
        _VL=""
        if _VLS=$(LC_ALL=C.UTF-8 msg_lines "$MSG_OLD" -iE "^[[:space:]]*${OLD_VYSTUP_RX}"); then _VL=$(head -1 <<<"$_VLS"); fi
        if [ -z "$_VL" ] && _VLS=$(LC_ALL=C.UTF-8 msg_lines "$MSG_OLD" -iE "$OLD_VYSTUP_RX"); then _VL=$(head -1 <<<"$_VLS"); fi
        if [ -n "$_VL" ] && _VV=$(LC_ALL=C.UTF-8 sed -E "s/^.*${OLD_VYSTUP_RX}//I" <<<"$_VL" 2>/dev/null); then
            VYSTUP_VAL="${_VV:- }"
        fi
    fi

    _MISS=()
    [ "$HAS_GOAL" = "1" ] || _MISS+=("**Úloha:** (what was asked, plain language)")
    [ "$HAS_OUTCOME" = "1" ] || _MISS+=("**Riešenie:** (how it was solved)")
    [ "$HAS_PLAN_CHECK" = "1" ] || _MISS+=("plan N/N (run the plan-check skill, fix every NOT DONE item)")
    [ "$HAS_REVIEW" = "1" ] || _MISS+=("review 0 🔴 0 🟡 0 🔵 (fix every /review finding, then report the post-fix zeros)")
    [ "$HAS_RCR" = "1" ] || _MISS+=("deep 0 🔴 0 🟡 0 🔵 (superpowers:requesting-code-review, every finding fixed)")
    [ "$HAS_ARCH" = "1" ] || _MISS+=("arch <oblasť> OK / REWORK fixed / REWORK #N (area-review verdict, #993; old form 🏛 Architektúra: <oblasť> — REWORK fixed)")
    [ "$HAS_VYSTUP" = "1" ] || _MISS+=("Výstup: <values read back from the real artifact> or n/a — <why> (#446)")
    [ "$HAS_PLAYBOOK" = "1" ] || _MISS+=("playbook: <lesson | none> (run the playbook-review skill; a lesson goes to the project's .claude/rules/<area>.md; 📔 Playbook: also accepted)")
    if [ "${#_MISS[@]}" -gt 0 ]; then
        echo "VIOLATION: Work Complete report is missing required fields. Template (completion-report.md):" >&2
        echo "    **Úloha:** <čo sa chcelo>  /  **Riešenie:** <ako sa to vyriešilo>  🌐 <url ak UI>" >&2
        echo "    ✅ Overené: plan N/N · review 0 🔴 0 🟡 0 🔵 · deep 0 🔴 0 🟡 0 🔵 · arch <oblasť> OK · Výstup: <hodnoty | n/a — prečo> · playbook: <lekcia | none>" >&2
        for _m in "${_MISS[@]}"; do
            echo "  - MISSING: $_m" >&2
            add_hard "Missing $_m"
        done
    fi

    if [ -n "$VYSTUP_VAL" ]; then
        if LC_ALL=C.UTF-8 msg_has "$VYSTUP_VAL" -qiE "^[[:space:]]*n/a[[:space:]]*(—|–|-)+[[:space:]]*[^[:space:]]"; then
            # 'n/a' claims no user-facing output; a 🌐/📱 URL in the same report
            # contradicts it. Fires only on a POSITIVE sighting (msg_missing's
            # unknown skips).
            if ! msg_missing "$MSG" -qE "^[[:space:]]*(🌐|📱)|[[:space:]](🌐|📱)[[:space:]]*https?://"; then
                echo "VIOLATION: 'Výstup: n/a' contradicts this report's own 🌐/📱 URL — the work HAS a user-facing output. Open it and cite values read from it (page content, UI values, the version label)." >&2
                add_hard "Výstup: n/a while the report lists a 🌐/📱 user-facing surface — read real observed values from that live surface instead"
            fi
        elif ! LC_ALL=C.UTF-8 msg_has "$VYSTUP_VAL" -qE '[0-9]|"|„|“|”|«|»|‚|‘|’|'\'; then
            # Value floor: a real read-back carries a digit (price, order number,
            # version, count) or a quoted span; 'odoslané OK' carries neither. A
            # bare 'n/a' with no reason lands here too.
            echo "VIOLATION: The Výstup value carries no concrete observed value — cite what you actually SAW in the real artifact (a number or a quoted span, on the same line), or write 'Výstup: n/a — <prečo>'. Send/delivery/'funguje' is liveness, not content." >&2
            add_hard "Výstup value is value-free — cite concrete observed values (numbers/quoted text) read from the real artifact, or an explicit 'n/a — <prečo>'"
        fi
    fi
fi

# HARD: a UI deploy with no clickable 🌐 URL (restored by the #1137 review, owner
# rule #595: a message that names something openable carries its URL). Armed only
# by a completion report or an explicit `✅ Deploy:` line, so casual "deployed to
# dev1+dev2" chat never trips it; `nasaden` covers the Slovak report. The 🌐 URL
# may sit inline on the Riešenie line. An unanswerable count (`?`) or an
# unanswerable UI probe never accuses (#194). The multi-environment ">= 2 🌐
# lines" variant stays removed (#1137).
HAS_DEPLOY_LINE=$(msg_has "$MSG" -qE "✅ Deploy:" && echo 1 || echo 0)
if { [ "$IS_COMPLETION" = "1" ] || [ "$HAS_DEPLOY_LINE" = "1" ]; } \
   && LC_ALL=C.UTF-8 msg_has "$MSG" -qiE "✅ Deploy:|deploy.*(verified|complete|done|success|redeploy|auto.?redeploy)|verified.*deploy|deployed.*(to|successfully)|nasaden"; then
    GLOBE_COUNT=$(msg_count "$MSG" -E "🌐.*https?://")
    if [ "$GLOBE_COUNT" != "?" ] && [ "$GLOBE_COUNT" -lt 1 ] \
       && ! msg_missing "$MSG" -qiE "frontend|dashboard|\bui\b|web app|browser|admin panel"; then
        echo "VIOLATION: The report deploys a UI (frontend / dashboard / web app) but carries no clickable 🌐 URL. Add '🌐 <url>' for the user-facing page (on the Riešenie line or its own line) — never a backend/API URL, never localhost. See completion-report.md and no-localhost-urls.md." >&2
        add_hard "UI deploy with no 🌐 URL line — add the user-clickable 🌐 <url> of the deployed page (owner rule #595)"
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
            echo "VIOLATION: Completion report contains a deferral phrase ('deferred', 'root-cause fix later', 'will be addressed in follow-up', 'remains outstanding', 'workaround for now', 'patched around', 'this PR doesn't fix...', 'punted to...') but NO EXPLICIT tracking-issue reference. The current PR's own #N in the title does NOT count — the user needs proof the DEFERRED work was recorded (a '- [ ]' item / comment on the current ticket, or a separate defect ticket)." >&2
            echo "" >&2
            echo "  Per complete-planned-work.md, any deferred work MUST be recorded on a ticket BEFORE sending the completion report, and the report MUST cite it explicitly:" >&2
            echo "    • 'Filed as #<N>: <title>'" >&2
            echo "    • 'Tracked as #<N>'" >&2
            echo "    • 'Root-cause fix tracked in #<N>'" >&2
            echo "    • 'Address in #<N>'" >&2
            echo "" >&2
            echo "  Without that, the deferred work is permanently lost (the ghost-deferral failure mode)." >&2
            echo "" >&2
            echo "  Fix NOW (#1259: one owner request = one ticket):" >&2
            echo "    1. Record it on the CURRENT ticket (a '- [ ]' item via gh issue edit, or a comment) and cite 'Recorded on #<N>', OR" >&2
            echo "    2. only for a separate defect outside the request: gh issue create, then 'Filed as #<returned-N>: <title>'" >&2
            echo "" >&2
            echo "  See skills/complete-planned-work-deep/DEEP.md → 'CRITICAL — deferral phrases MUST cite the ticket number'." >&2
            add_hard "Deferral phrase in completion report without explicit 'Filed as #N' / 'Tracked as #N' reference"
        fi
    fi
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

# #916 — Odoo posting READ-BACK evidence check. When the assistant's message
# REPORTS having posted to Odoo chatter/Discuss (a past-tense posting verb near
# an Odoo posting term, outside quotes/code), it must carry read-back evidence
# that the posted messages have no escaped HTML tags (the "0 escaped messages"
# evidence line). The #915 PreToolUse hook guards the posting ACTION; this
# guards the REPORTING.
#
# The Stop hook reads ONLY `last_assistant_message` text — it has NO access to
# tool calls or tool results. A fully reliable turn-shape detector for "this
# turn performed Odoo message posting" does NOT exist in this information space.
# This check is NARROW on purpose: it fires only when the message explicitly
# reports a posting action, so a message merely DISCUSSING the hook, the
# doctrine, or the concept of message_post is not gated (MSG_MENTION strips
# backticked/quoted/fenced spans first).
#
# 3-conjunct, fail-safe PASS (any absent -> pass):
#   (1) POSTING ACTION: a past-tense verb (posted/sent/odoslal/napisal/created)
#       near (.{0,60}) an Odoo posting term (chatter, message_post,
#       project.task, discuss.channel) — MSG_MENTION.
#   (2) EVIDENCE MISSING: no read-back proof in the message (0 escaped,
#       no escaped, body_is_html verified/True, read-back, airuleset:html-ok).
#   (3) NO ESCAPE: no UNVERIFIED: line.
#
# Accepted residuals: (1) a turn that posts without describing it is NOT caught
# (the #915 PreToolUse hook is the guard); (2) a prose description of posting
# (not actually posting) can false-fire if outside quotes/code — strip_mentions
# mitigates; (3) evidence tokens are intentionally loose.
ODOO_POST_VERB_RX='\b(posted|sent|wrote|created)\b'
ODOO_POST_TERM_RX='(chatter|message_post|project\.task|discuss\.channel)'
ODOO_POST_SK_VERB_RX='\b(odoslal|napisal|odoslane|vytvoril)\b'
ODOO_POST_ACTION=$(LC_ALL=C.UTF-8 msg_has "$MSG_MENTION" -qiE "${ODOO_POST_VERB_RX}.{0,60}${ODOO_POST_TERM_RX}|${ODOO_POST_TERM_RX}.{0,60}${ODOO_POST_VERB_RX}|${ODOO_POST_SK_VERB_RX}.{0,60}${ODOO_POST_TERM_RX}|${ODOO_POST_TERM_RX}.{0,60}${ODOO_POST_SK_VERB_RX}" && echo 1 || echo 0)
if [ "$ODOO_POST_ACTION" = "1" ]; then
    ODOO_RB_EVIDENCE=$(msg_has "$MSG" -qiE '0 escaped|escaped.{0,20}\b0\b|no escaped|body_is_html.{0,20}(verified|True|checked|confirmed)|read.back|airuleset:html-ok' && echo 1 || echo 0)
    ODOO_RB_UNVERIFIED=$(msg_has "$MSG" -qE 'UNVERIFIED:' && echo 1 || echo 0)
    if [ "$ODOO_RB_EVIDENCE" = "0" ] && [ "$ODOO_RB_UNVERIFIED" = "0" ]; then
        echo "VIOLATION: Your message reports having posted to Odoo (chatter/Discuss/message_post) but carries no read-back evidence that the posted messages have no escaped HTML tags. After posting via message_post, read back the posted messages and verify that 0 contain raw '<' characters (escaped HTML). Include the evidence line in your message, e.g.: 'Read-back: 0 escaped messages.' or 'body_is_html=True verified.' The #915 PreToolUse hook guards the posting action; this check ensures you VERIFIED the result. If you cannot read back, write 'UNVERIFIED: <why>'. See airuleset #915/#916." >&2
        add_hard "Odoo posting report without read-back evidence (0 escaped messages / body_is_html verified) — verify posted messages have no escaped HTML (#916)"
    fi
fi

# #978 — Ack-reaction evidence check. When the assistant's message reports
# processing a NEW client message in an Odoo Discuss thread (a client wrote /
# sent something, a remark, a question — detected by Slovak verbs: klient
# napísal/poslal, <name> poslal/a do vlákna, pripomienk near klient/vlákno),
# the message MUST carry an `Ack-reaction:` evidence line (msg id + emoji, or
# `pending — <reason>`). Without it, the stream processed a client message
# with NO visible acknowledgement — the exact regression the owner reported
# (#978, miva1 2026-09-10).
#
# NARROW by design: only fires when the turn (a) has an Odoo anchor
# (Discuss/vlákn/message_post/active_id), AND (b) mentions a NEW client message
# (active-voice verbs near client/thread context in $MSG_MENTION — fenced/
# backtick/quoted spans stripped), never on a turn that merely recaps an OLD
# message, discusses doctrine in a code block, or operates outside Odoo.
# Negatives tested: old-message recap, code-block mentions, non-Odoo prose,
# diacritic spellings under C locale.
ACK_REACTION_MENTION=""
ACK_REACTION_UNKNOWN=0
# Stage 0: Odoo anchor gate — only fire in an Odoo/Discuss context (MEDIUM-3
# review finding: without this, "TCP klient poslal SYN" blocks fleet-wide).
# Reuses the same ODOO_ANCHOR_RX already computed at line ~496 for #657.
_ACK_HAS_ODOO=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qE "$ODOO_ANCHOR_RX|vl[áa]kn" && echo 1 || echo 0)
if [ "$_ACK_HAS_ODOO" = "1" ]; then
    # Stage 1: does the message mention processing a NEW client message?
    # The CLIENT must be the grammatical SUBJECT (nominative), not the
    # object (dative "klientovi", instrumental "klientom") — the
    # assistant's own first-person send ("poslal som klientovi") must NOT
    # match (#978 fix-forward, 657 false-positive).
    # Two arms (ERE, run on $MSG_MENTION — fenced/backtick/quoted spans
    # stripped; HIGH-2 fix; LC_ALL=C.UTF-8 for diacritics, HIGH-1 fix):
    #   A) klient/zákazník in NOMINATIVE (≤2 words) before napísal/poslal
    #   C) Capitalised Slovak name (≤2 words) before verb, message-noun
    #      within 60 chars (pripomienk/otázk/požiadav/správ/súbor/do vlákn)
    # First-person EXCLUSION: any candidate line with \bsom\b within 12
    # chars of the verb is dropped (catches "poslal som", "som poslal",
    # "napísal som").
    #
    # Accepted residuals (#978 fix-forward): a third-person sentence where
    # the subject is neither klient/zákazník NOR a capitalised name will
    # not fire — acceptable (very rare in stream prose).
    _ACK_CLIENT_MSG=""
    if ! _ACK_LINES=$(LC_ALL=C.UTF-8 msg_lines "$MSG_MENTION" -iE "(klient(ka)?|z[aá]kazn[ií](k|[čc]k)a?)\b.{0,20}(nap[ií]sala?|poslala?)\b|[[:upper:]][[:lower:]]{2,}.{0,20}(nap[ií]sala?|poslala?)\b.{0,60}(pripomienk|ot[áa]zk|po[žz]iadav|spr[áa]v[ua]|s[úu]bor|do vl[áa]kn)"); then
        _ACK_LINES=""
    fi
    if [ -n "$_ACK_LINES" ]; then
        # Exclude first-person lines: "som" within 12 chars of the verb
        _ACK_CLIENT_MSG=$(printf '%s\n' "$_ACK_LINES" | LC_ALL=C.UTF-8 grep -ivE "(nap[ií]sala?|poslala?).{0,12}\bsom\b|\bsom\b.{0,12}(nap[ií]sala?|poslala?)" || true)
    fi
    if [ -n "$_ACK_CLIENT_MSG" ]; then
        # Stage 2: does the message carry an Ack-reaction evidence line?
        # Exoneration on raw $MSG (an Ack-reaction: inside a code block still counts).
        if ! msg_has "$MSG" -iE "Ack-reaction:"; then
            ACK_REACTION_MENTION="1"
        fi
    fi
fi
if [ "$ACK_REACTION_MENTION" = "1" ]; then
    echo "VIOLATION: Your message reports processing a NEW client message in a Discuss thread but carries no Ack-reaction: evidence line. The moment you read a new client message, add the fleet ack reaction — the WORKER 👷 by default (ack_reaction_emoji config key; the legacy 👀 stays selectable) — via message_reaction_guarded(message_id, \"👷\", \"add\") — or, if the method is not yet released on this instance, record 'Ack-reaction: pending — <reason>'. Include the evidence line (any configured ack emoji is accepted). See skills/odoo-client-messaging/ack-reaction.md (#978/#1027/#1033)." >&2
    add_hard "New client message processed without Ack-reaction: evidence line — add 👷 reaction or record pending (#978/#1027)"
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
# #1018 — Verifikácia handover-note SHAPE check. When the assistant's message
# REPORTS moving a client task to the "awaiting client verification" stage
# (Verifikácia / Na overenie / Čaká) and posting the handover note, that note MUST
# carry the four sections Čo / Kde / Čo skúsiť / stačí 👍 (client-board-tasks.md
# rule 3). The stage alternation COUPLES to the per-board profile table
# (client-board-tasks.md / client-board-stages.md): Verifikácia on montalu, Na
# overenie on slovnormal, Čaká on miva (odoo-erp #7101) — add a board's
# awaiting-verification stage here when its profile row is added (#1093).
# Same information space as the #916/#978 self-report checks (last_assistant_message
# only): it fires ONLY on a self-reported Verifikácia post and requires the
# distinctive section markers (Kde + Čo skúsiť + stačí 👍) to be evidenced in the
# message, or an UNVERIFIED: escape. NARROW on purpose: an Odoo/task anchor AND a
# past-tense move/post verb within 60 chars of the stage name — a message that
# merely MENTIONS the stage (e.g. "next I'll move it to Verifikácia") is never
# gated. Fail-safe like its siblings: an unknown SECTION check resolves to
# "present" (msg_has UNKNOWN->0), so a grep meltdown never fabricates a violation.
# CAPITALIZED proper stage name only (#1018 review-1 F4): the board STAGE is the
# proper noun "Verifikácia" / "Na overenie" — a lowercase "na overenie" is the
# ordinary Slovak phrase "for checking" ("poslal na overenie správnosti"), which
# must NOT be gated. So the stage match is CASE-SENSITIVE (requires the capital),
# and the verbs carry explicit-case classes so the proximity check can run
# case-sensitively without missing a sentence-initial or lowercase verb.
# The miva stage "Čaká" needs MORE than case (#1093 review): "čaká" is the everyday
# verb "waits" and it is ALSO capitalised at the start of a sentence ("Čaká na
# klienta" / "Čaká sa na odpoveď" / "Čaká, kým klient potvrdí"), so a bare
# capital-token match would false-block ordinary status prose fleet-wide (the very
# class Approach 3 was rejected for). The miva stage therefore requires a
# DIRECTIONAL MOVE CUE immediately before it — "do "/"do stavu "/"do stage "/"do
# fázy "/"→ "/"-> " — which a stage-move report always carries ("presunul som do
# Čaká") and the verb forms never do; a trailing `\b` still keeps it off longer
# words ("Čakať" / "Čakáreň"). The cue itself is anchored at a word boundary
# (`(^|[^[:alpha:]])`) so a word ENDING in "do" ("todo"/"kedo Čaká") is not misread
# as the cue. Accepted residual: a real Čaká handover phrased with no move cue is
# not shape-checked (safe direction — never a false block).
VERIF_STAGE_RX='(Verifik[áa]ci|Na overeni|(^|[^[:alpha:]])(do +|→ *|-> *)(stavu? +|stage +|f[áa]z[ye] +)?Čak[áa]\b)'
# PAST-TENSE only: a future-tense mention ("presuniem ... do Verifikácia" = "I
# WILL move it") must NOT be gated — so the SK stems are the past-participle
# forms (presunul/posunul/…), never the bare present/future stem.
VERIF_VERB_RX='\b([Pp]osted|[Mm]oved|[Ss]ent)\b|([Pp]resunul|[Pp]osunul|[Oo]doslal|[Pp]ostol|[Nn]ap[ií]sal|[Pp]ridal)\w*'
VERIF_REPORT=0
if [ "$(LC_ALL=C.UTF-8 msg_has "$MSG" -qiE "${ODOO_ANCHOR_RX}|project\.task|[úu]loh" && echo 1 || echo 0)" = "1" ]; then
    if [ "$(LC_ALL=C.UTF-8 msg_has "$MSG_MENTION" -qE "(${VERIF_VERB_RX}).{0,60}${VERIF_STAGE_RX}|${VERIF_STAGE_RX}.{0,60}(${VERIF_VERB_RX})" && echo 1 || echo 0)" = "1" ]; then
        VERIF_REPORT=1
    fi
fi
if [ "$VERIF_REPORT" = "1" ]; then
    VERIF_UNVERIFIED=$(msg_has "$MSG" -qE 'UNVERIFIED:' && echo 1 || echo 0)
    VERIF_KDE=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qiE '\bKde\b' && echo 1 || echo 0)
    VERIF_SKUSIT=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qiE '[Čč]o sk[úu]si' && echo 1 || echo 0)
    VERIF_STACI=$(msg_has "$MSG" -qE '👍' && echo 1 || echo 0)
    if [ "$VERIF_UNVERIFIED" = "0" ] && { [ "$VERIF_KDE" = "0" ] || [ "$VERIF_SKUSIT" = "0" ] || [ "$VERIF_STACI" = "0" ]; }; then
        echo "VIOLATION: Your message reports moving a client task to Verifikácia / Na overenie and posting the handover note, but the note is missing the mandatory sections. A Verifikácia handover note MUST carry all four, in order: 'Čo' (what was delivered), 'Kde' (menu path + a functional https:// deep link, verified 200), 'Čo skúsiť' (what to try), and the literal closing line 'stačí 👍'. Include the note's four sections in your message (or write 'UNVERIFIED: <why>'). See skills/odoo-client-messaging/client-board-stages.md rule 3 (#1018)." >&2
        add_hard "Verifikácia handover note reported without the Čo/Kde/Čo skúsiť/stačí 👍 sections (#1018)"
    fi
fi

# #1179/#1180 — a client handover on a project.task is ONE step (`odoo_post.py
# --handover`, odoo-erp#8606: post + assignee + move). The #1018 check above
# reads only the final prose; the incidents (montalu1 28.9., montalu4 29.9.)
# posted handovers and never moved the tasks, reporting nothing. gates.handover
# reads THIS TURN's tool calls from transcript_path (see its docstring). Cheap
# prefilter: the gate runs only when the transcript tail carries a poster call or
# an Acceptance-thread line (process substitution, NOT `tail | grep -q` — under
# pipefail a SIGPIPE'd tail would read as "no match"). rc 2 = block, any other
# non-zero = infra hiccup → fail OPEN (never a fabricated block).
HANDOVER_TP=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
if [ -n "$HANDOVER_TP" ] && [ -f "$HANDOVER_TP" ] \
        && LC_ALL=C grep -qE 'odoo_post|post-message|Acceptance-thread' <(tail -c 4000000 "$HANDOVER_TP" 2>/dev/null); then
    HANDOVER_RC=0
    HANDOVER_REASON=$(printf '%s' "$INPUT" | env PYTHONPATH="${_PROSE_REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m gates.handover 2>&1) || HANDOVER_RC=$?
    if [ "$HANDOVER_RC" = "2" ]; then
        printf '%s\n' "$HANDOVER_REASON" >&2
        add_hard "$(printf '%s' "$HANDOVER_REASON" | tr '\n' ' ')"
    elif [ "$HANDOVER_RC" != "0" ]; then
        printf 'stop-check-prose-violations: handover gate infra rc=%s — fail-open (#1180)\n' "$HANDOVER_RC" >&2
    fi
fi

# #1042/#1073 — client-guide (Návody) link on a client-acceptance hand-off.
# Owner directive (montalu6, 15.9.2026): every client handover delivers ONE
# startup „Introduction / Začíname" page IN the product (the Návody section),
# and the acceptance hand-off LINKS it. #1073 (owner 18.9.2026: „návody buduj a
# udržiavaj a nech to je airuleset pravidlo") REPLACES the round-1 word check
# (https:// + a Návody keyword) — which could not tell a live guide from a
# fabricated URL and so forced a dead link on a tenant with no guide (#1072) —
# with a per-tenant live-link gate: the FIRE detectors below still classify the
# hand-off shape, but the SATISFIER is now `gates.navody` (a live-link + fact
# check; see the block body). FIRE detectors read MSG_MENTION (a
# backticked/quoted MENTION of the rule never fires — the #96 use-vs-mention
# split, so a message DESCRIBING this rule or a code-fenced example is not gated);
# the satisfier + bypass read the raw MSG (a link in a code span still counts).
# FAIL-OPEN at the NET level like #1018: msg_has resolves an UNKNOWN grep to
# MATCH(1), so a grep meltdown reads the satisfier as PRESENT → no block is
# fabricated. Narrow on purpose (the acceptance/label shape, not any question).
INTRO_FIRE=0
# Trigger A — a ❓ acceptance block: a genuine question turn (❓ + ASKED/NEEDS
# YOU) that PROPOSES a client acceptance message. #1042 review-1 🔴 + review-2 🔴:
# `❓`/`NEEDS YOU` are in EVERY question, and an acceptance/client WORD ("odovzdať
# súbor klientovi", "vygeneruje akceptačnú správu", a `needs-acceptance` label
# discussion) can appear in an ordinary DESIGN question — so keying on words
# false-blocks design questions. The discriminating signal is the ACT of
# proposing a client message, which per handover-compose carries a STRUCTURAL
# marker a design question never has: the mandatory stream identity SIGNATURE
# (`ZbynekAI/MarekAI <N>`, the LAST line of every client message) OR the target
# client thread DEEP URL (`discuss.channel_<N>`) alongside an acceptance cue.
# These are read from the RAW `$MSG` (a real proposal may present the body in a
# ``` fence or `>` quote — the signature/URL must survive stripping); the ❓
# marker is read from MSG_MENTION so a fully-fenced EXAMPLE of the rule (with no
# live status marker) never fires (#96 use-vs-mention). Bare feature-word
# questions (no signature, no thread URL) no longer fire.
INTRO_QMARK=$(LC_ALL=C.UTF-8 msg_has "$MSG_MENTION" -qE '❓' && echo 1 || echo 0)
INTRO_ASKED=$(LC_ALL=C.UTF-8 msg_has "$MSG_MENTION" -qiE 'ASKED|NEEDS[[:space:]]+YOU' && echo 1 || echo 0)
INTRO_SIG=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qE '(Zbynek|Marek)AI[[:space:]]*[0-9]' && echo 1 || echo 0)
INTRO_THREAD=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qE 'discuss\.channel_[0-9]' && echo 1 || echo 0)
INTRO_ACCCUE=$(LC_ALL=C.UTF-8 msg_has "$MSG" -qiE 'needs-acceptance|akceptačn|acceptance|odovzd' && echo 1 || echo 0)
# PROPOSAL = a proposed client message (signature) OR a targeted client thread
# (deep URL) with acceptance context. A design question about a feature/label
# carries neither.
if [ "$INTRO_QMARK" = "1" ] && [ "$INTRO_ASKED" = "1" ] \
   && { [ "$INTRO_SIG" = "1" ] || { [ "$INTRO_THREAD" = "1" ] && [ "$INTRO_ACCCUE" = "1" ]; }; }; then
    INTRO_FIRE=1
fi
# Trigger B — a needs-acceptance labelling ACTION narrated in prose (a label
# verb near the label, or the gh command); a backticked example is stripped by
# MSG_MENTION and does not fire. #1042-review-1 🔵: the verb→label window is 120
# (a deep URL can sit between "označil" and "needs-acceptance"); `.`-bounded.
INTRO_LABEL=$(LC_ALL=C.UTF-8 msg_has "$MSG_MENTION" -qiE '(--?add-label|add_label|pridal|ozna[čc]il|nastav|[šs]t[íi]tk|labell?ed).{0,120}needs-acceptance|needs-acceptance.{0,40}(--?add-label|add_label)|gh[[:space:]]+issue[[:space:]]+edit.{0,80}needs-acceptance' && echo 1 || echo 0)
if [ "$INTRO_LABEL" = "1" ]; then INTRO_FIRE=1; fi
if [ "$INTRO_FIRE" = "1" ]; then
    INTRO_BYPASS=$(msg_has "$MSG" -qF 'airuleset:intro-link-ok' && echo 1 || echo 0)
    if [ "$INTRO_BYPASS" = "0" ]; then
        # #1073 (owner ruling 18.9.2026, montalu1: „návody buduj a udržiavaj a
        # nech to je airuleset pravidlo") — REPLACE the #1042 word check
        # (https:// + a Návody keyword) with the per-tenant live-link gate. The
        # word check could not tell a live guide from a fabricated URL, so on a
        # tenant with no guide it forced a dead `/odoo/knowledge` link into a
        # client message (#1072). gates.navody reads the per-tenant `navody_url`
        # fact from `.claude/streams/<stream>.md` + the message/cwd from the
        # SAME Stop payload (piped on stdin) and blocks unless the message
        # carries a LIVE guide deep-link (or, for a `NONE — #N` fact, the guide
        # ticket reference); an UNKNOWN fact FAILS CLOSED with the fix named, so
        # while the gate runs a fabricated/dead link cannot pass. exit 2 =
        # block, exit 0 = allow, any other rc = infra failure (python/curl
        # hiccup) -> fail OPEN (never fabricate a block on an infra error). The
        # bypass
        # `# airuleset:intro-link-ok <dôvod>` (checked above) still exempts an
        # API-only feature with no product page.
        NAVODY_RC=0
        NAVODY_REASON=$(printf '%s' "$INPUT" | env PYTHONPATH="${_PROSE_REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m gates.navody 2>&1) || NAVODY_RC=$?
        if [ "$NAVODY_RC" = "2" ]; then
            printf '%s\n' "$NAVODY_REASON" >&2
            add_hard "client-acceptance hand-off: client-guide (Návody) live-link/fact check failed (#1073)"
        elif [ "$NAVODY_RC" != "0" ]; then
            printf 'stop-check-prose-violations: navody gate infra rc=%s — fail-open (#1073)\n' "$NAVODY_RC" >&2
        fi
    fi
fi

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

# #1084 — the `## Work Complete` boundary no longer records a `/compact` request
# here. Machine-triggered compacts are REMOVED for good (owner ROZHODNUTÉ
# 2026-09-19): nothing mechanical records or delivers a `/compact` any more —
# Claude Code's own threshold autocompact is the only compaction left. This was
# the SOLE mechanical `/compact` recorder (#411); it is deleted with the rest of
# the callback-compact machinery.

exit 0
