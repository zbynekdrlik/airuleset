#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — airuleset #118.
#
# A REPEATED foreground CI poll loop is hard-blocked; the FIRST one is free.
#
# Live burn (2026-07-28, restreamer pane zbynek-0:0): a session watching one
# ~2-hour CI run chained ~13 nine-minute FOREGROUND poll turns, each turn
# re-sending a ~170K-token context for one line of status. It did this AFTER
# a /compact and AFTER re-reading ci-monitoring.md — prose had just been read
# and was still lost. #107 and #110 were both rewrites of that same rule and
# both were post-dated by the same failure, so the repo's own intake gate
# applies: a mechanically checkable rule belongs in a hook, not in an
# always-on paragraph.
#
# WHY THE FIRST LOOP STAYS FREE (decision on #118, option A). Wait length is
# not knowable at decision time — that is precisely why every rule asking the
# model to PREDICT it has failed. But a first loop that returned non-terminal
# is MEASURED proof the wait is long, and a genuinely short wait ends terminal
# inside loop 1 and never reaches this hook at all. The burn was loops 2-13,
# never loop 1. Blocking exactly at loop 2 mechanizes the escalation inference
# the prose could not make the model perform.
#
# WHY THE BLOCK MESSAGE CARRIES THE WHOLE COMPLIANT COMMAND. Diagnosis (a) on
# the ticket: the model reaches for the first concrete artefact it can see.
# That is exploited here rather than fought — the ready-to-paste background
# waiter, run-id already substituted, IS the message. A block that says only
# "no" is what produces evasion.
#
# SUBAGENTS — the invariant this hook must not break. The background waiter is
# BROKEN inside a subagent: one with no pending FOREGROUND tool call is
# returned as "completed" and TERMINATES, so the detached task's completion
# fires to the PARENT and the worker silently dies (~40% of autopilot-worker
# failures; block-subagent-bg-ci-poll.sh and subagent-stop-check-bg-work.sh
# exist to force foreground waits there). So in subagent context this hook
# NEVER prints the background waiter — the message carries only "hand the
# run-id back to the supervisor and RETURN", which is the long-wait contract
# ci-monitoring.md already gives workers. Detection is the payload's
# `agent_id`, the same signal block-subagent-bg-ci-poll.sh uses; it is
# empirically present, not assumed (the live ledger files
# /tmp/airuleset-bgtasks-<session>-<agent> carry real agent ids, which only
# happens when agent_id was non-empty in a real subagent payload). Belt and
# braces regardless: the MAIN-context message still carries a one-line "if you
# are a subagent, do NOT run the above" branch, so a detection miss cannot
# route a worker onto the fatal path.
#
# DETECTOR: reused verbatim from #111 — a `sleep` inside a `do`…`done` body,
# matched on a normalized single-space token stream (793 of 77,354 real
# commands; retains 39 of the 41 polls the harness actually killed). No third
# detector is written here, and the heredoc residual is deliberately not
# chased: that is #112, closed won't-fix with numbers.
#
# NARROWED BY A CI-WAIT SIGNATURE. The #111 shape alone also matches wait
# loops that have nothing to do with CI (waiting on a build artefact, a
# container health check, an rsync retry). Blocking the second of those would
# stop real work — on these boxes the working tree is production — so the hook
# only engages when the command also carries a CI-wait signature (`gh run
# view|watch|list`, `gh pr checks`), the same narrowness
# block-subagent-bg-ci-poll.sh already uses. This also matches the decision's
# own example of the fallback bucket (`gh run list`-driven loops — still CI).
#
# KEY: session_id + run-id. Run-id is the first 8+ digit token in a command
# that already carries a `gh run` signature (GitHub run ids are 10-11 digits,
# so `--limit 3` and `sleep 30` cannot collide). Per-run keying is deliberate:
# a push -> fix -> push cycle gets a fresh free loop per NEW run, because each
# new run is legitimately a new short wait. No id extractable -> a generic
# per-session CI bucket, which decays after AIRULESET_CIPOLL_GENERIC_TTL_S
# (default 1800s) so two CI waits an hour apart are not treated as one burst,
# while the observed 9-minutes-apart chain still collides.
#
# NEVER BLOCKED, in any state: a `run_in_background` waiter (the compliant
# path). Plus the logged inline escape hatch `# airuleset:poll-ok <reason>`.
# A single `gh run view <id>` status check is free for the first
# AIRULESET_CIPOLL_ONESHOT_FREE (default 2) touches per (session, run-id) and
# DECAYS after AIRULESET_CIPOLL_ONESHOT_TTL_S (default 1800s) of no further
# touches on that key — see the #210 oneshot throttle below. It is not a
# literal "any time, forever" exemption: repeating the SAME bare status check
# many times in a tight window is exactly the #210 burn shape and is bounded
# like everything else, then forgiven once the window genuinely passes.
#
# EVASION is bounded by logging, not by pattern whack-a-mole (which would be
# prose accumulation in hook form). Every block, every post-block poll burst
# and every bypass is appended to a log in the state dir for later corpus
# review — bypass-log-first diagnosis, per this repo's playbook. The observed
# failure mode is defaulting, not adversarial circumvention, and a blocked
# model has already been handed the correct command.
#
# #127 (closed, signature UNCHANGED): does `gh pr view <N> --json …
# statusCheckRollup…` belong in this signature too? Measured (2026-07-29,
# 8,231 transcripts / 258,724 commands): 52 such loops exist, none of which
# match the token set above, so they never reach this hook — and 14 of them
# already repeat and are already blocked, correctly, by block-local-poll-
# repeat.sh's generic bucket. Tested widening THIS regex empirically, not by
# inspection: PR numbers here (112-704) never reach the 8-digit RUN_ID floor,
# so every one would fall into the GENERIC bucket below, whose compliant
# command (`RID=$(gh run list -L 1 --json databaseId …)`) picks the single
# most recent run in the WHOLE repo — not the run behind the polled PR. That
# is a wrong-run waiter, worse than the sibling hook's honestly-scoped
# message. The generic bucket's 1800s TTL would also intermittently reset
# "first loop free" for these naturally slower-cadence waits, weakening a
# guard that today never decays. Split stands; see #127 for the full replay.
#
# #210: a BARE one-shot poll is the SAME burn wearing a different shape. The
# #111/#118 loop detector only sees a `do...sleep...done` body — a worker
# that just calls `gh run view <id> --json status,conclusion` once per TURN,
# with no sleep and no loop, is invisible to it. Production burn (2026-08-01):
# 260 of 474 `gh run view` calls across 107 subagent transcripts were exactly
# this — non-loop, one-shot, each its own turn — and the worst dispatch did
# 157 of them, one run polled across 35 separate turns. Fix: count bare
# STATUS-POLL one-shots (`--json status`, `--json status,conclusion` either
# order, or no `--json` at all) per (session, run-id), reusing the same
# STATE_DIR the loop mechanism already uses. The first TWO stay free — a
# single one-shot repeat is not by itself proof of a long wait the way loop 1
# returning non-terminal is — the 3rd+ hard-blocks with the SAME two-shape
# message the loop block already prints (bounded foreground loop for a short
# wait, background waiter for a long one, subagent hand-back either way).
# Deliberately narrow: `--log` / `--log-failed` (reading WHY a run failed)
# and any `--json` value naming a field other than status/conclusion are
# never counted, and neither is a command carrying TWO `gh run view`
# invocations (the #118-documented post-mortem shape) — both are the actual
# debugging work, not a wasted poll. A `gh run view` with NO run-id argument
# at all (defaults to the latest run on the current branch) is counted too,
# under the SAME per-session generic bucket the loop mechanism already uses
# for a key-less command — it used to evade the counter entirely, since the
# whole block below used to be gated on a non-empty run-id.
#
# Review follow-up (same day): the oneshot counter had NO decay (a 3rd+
# block on a key stayed blocked on every later call, forever — contradicting
# the "no dead end" invariant above) and its block wrote the SAME shared
# BLOCKED_FILE the #118 loop carve-out gates on, so a few oneshot blocks could
# permanently consume a run-id's free-first-loop pass before any loop ever
# ran. Fixed by giving the oneshot mechanism its OWN state file (a sliding
# TTL counter — AIRULESET_CIPOLL_ONESHOT_TTL_S, default 1800s, refreshed on
# every touch and reset to a fresh count once it has genuinely gone stale)
# that only LOOP_BLOCKED_FILE (written on a loop or burst block, never an
# oneshot block) can consume the loop carve-out.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -n "$CMD" ] || exit 0

# ---- never block the compliant path -----------------------------------
BG=$(echo "$INPUT" | jq -r '.tool_input.run_in_background // false' 2>/dev/null || echo "false")
[ "$BG" = "true" ] && exit 0

# ---- #124: payload is not control flow ---------------------------------
# Everything below reads $CMD as if every byte of it were something the shell
# RUNS. It is not: a heredoc body or a quoted --body argument is cargo. Left
# unstripped, a doc write QUOTING the sanctioned waiter carries `gh run view`
# (CI signature), is not mutating-exempt (`cat >` is not in the list) and
# carries `do … sleep … done` — so the SECOND such write in a session was a
# hard exit 2 on a command that polls nothing. Stripping HERE, before the
# signature gate, is what puts it out of contract entirely rather than merely
# allowing it: an allowed doc write would still consume the one free loop a
# real wait is owed. The stripper is narrow (write-then-run, interpreter
# bodies and quoted `bash -c` loops all survive) and fails open.
# Sourced DEFENSIVELY: under `set -e` a failed source exits the hook non-zero,
# which the harness reports as a hook ERROR on every Bash tool call across all
# managed boxes. Every other dependency here (jq, python3, md5sum, the state
# dir) is fail-open; this must be too.
_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/lib-poll-payload.sh"
if [ -r "$_LIB" ]; then . "$_LIB" 2>/dev/null || true; fi
if command -v poll_payload_strip >/dev/null 2>&1 \
        && poll_payload_carrier "$CMD"; then
    CMD=$(poll_payload_strip "$CMD")
fi

STATE_DIR="${AIRULESET_CIPOLL_STATE_DIR:-/tmp}"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
# #492: the accumulating .log names are FIXED — on a shared box (default
# STATE_DIR=/tmp) the first user owns each, so every other user's append
# fails EACCES and leaks to stderr. The per-session STATE files below are
# already ${SID}-${KEY}-unique and never collide; only the logs need the
# per-user suffix. ${EUID} is a bash builtin; id -u is the non-bash fallback.
LOG_UID="${EUID:-$(id -u)}"

# `tr` flattens newlines so a signature split across continuation lines still
# matches — grep is line-scoped otherwise.
FLAT=$(printf '%s' "$CMD" | tr '\n' ' ')

# ---- CI-wait signature: everything else is out of contract -------------
printf '%s' "$FLAT" \
    | grep -qE 'gh[[:space:]]+run[[:space:]]+(view|watch|list)|gh[[:space:]]+pr[[:space:]]+checks' \
    || exit 0

RAW_SID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SID=$(printf '%s' "$RAW_SID" | tr -cd 'A-Za-z0-9_-')
SID="${SID:-unknown}"
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")

# ---- run-id -> state key ------------------------------------------------
RUN_ID=$(printf '%s' "$FLAT" | grep -oE '(^|[^0-9])[0-9]{8,}([^0-9]|$)' \
    | grep -oE '[0-9]{8,}' | head -1 || true)
if [ -n "$RUN_ID" ]; then
    KEY="run-$RUN_ID"
else
    KEY="generic"
fi

FIRST_FILE="$STATE_DIR/airuleset-cipoll-first-${SID}-${KEY}"
BLOCKED_FILE="$STATE_DIR/airuleset-cipoll-blocked-${SID}-${KEY}"
# LOOP_BLOCKED_FILE is DELIBERATELY separate from BLOCKED_FILE: it is what the
# #118 "loop 1 is free" carve-out gates on, and it must only ever be set by an
# actual LOOP or BURST block — never by a oneshot block (review follow-up on
# #210: a oneshot block used to write BLOCKED_FILE too, so 3 oneshots
# permanently poisoned the free-first-loop carve-out for a run-id no loop had
# ever polled). BLOCKED_FILE itself stays general-purpose (postblock corpus
# logging + the burst backstop below), unaffected by this split.
LOOP_BLOCKED_FILE="$STATE_DIR/airuleset-cipoll-loopblocked-${SID}-${KEY}"
BLOCK_LOG="$STATE_DIR/airuleset-cipoll-block-${LOG_UID}.log"
POSTBLOCK_LOG="$STATE_DIR/airuleset-cipoll-postblock-${LOG_UID}.log"
BYPASS_LOG="$STATE_DIR/airuleset-cipoll-bypass-${LOG_UID}.log"

SNIPPET=$(printf '%s' "$FLAT" | cut -c1-160)

# ---- corpus review: every poll touch on an already-blocked key ----------
if [ -e "$BLOCKED_FILE" ]; then
    { printf '%s cipoll POST-BLOCK session=%s key=%s agent=%s cmd=%s\n' \
        "$(date -Is)" "$SID" "$KEY" "${AGENT_ID:-main}" "$SNIPPET" \
        >> "$POSTBLOCK_LOG"; } 2>/dev/null || true
fi

# ---- logged escape hatch (never a dead end) ----------------------------
if printf '%s' "$FLAT" | grep -qE '#[[:space:]]*airuleset:poll-ok'; then
    REASON=$(printf '%s' "$FLAT" | sed -n 's/.*#[[:space:]]*airuleset:poll-ok[[:space:]]*//p' | cut -c1-160)
    { printf '%s cipoll BYPASS session=%s key=%s reason=%s\n' \
        "$(date -Is)" "$SID" "$KEY" "$REASON" \
        >> "$BYPASS_LOG"; } 2>/dev/null || true
    exit 0
fi

EXEMPT_LOG="$STATE_DIR/airuleset-cipoll-exempt-${LOG_UID}.log"
log_exempt() {
    { printf '%s cipoll EXEMPT session=%s key=%s why=%s cmd=%s\n' \
        "$(date -Is)" "$SID" "$KEY" "$1" "$SNIPPET" \
        >> "$EXEMPT_LOG"; } 2>/dev/null || true
}

# ---- narrowing 1: a command that MUTATES is not primarily a wait -------
# Corpus replay (#118): 29 of 863 blocks carried a merge/push/create with the
# wait merely bolted on the tail (`gh pr merge 312 --merge && … until run=…;
# do sleep 15; done`). Blocking one of those blocks the MERGE, not a poll —
# and on these boxes the working tree is production. It also cannot be the
# burn shape: you merge once, and the repeat polls that follow it are pure
# waits, still caught. Logged so the corpus can show if it is ever abused.
if printf '%s' "$FLAT" | grep -qE '(^|[^A-Za-z0-9_-])(git[[:space:]]+(push|commit|merge|tag)|gh[[:space:]]+pr[[:space:]]+(merge|create)|gh[[:space:]]+issue[[:space:]]+(create|close|comment|edit)|rsync|scp|systemctl[[:space:]]+(restart|start|stop))([^A-Za-z0-9_-]|$)'; then
    log_exempt "mutating-action"
    exit 0
fi

# ---- shape: the #111 loop detector, verbatim ---------------------------
IS_LOOP=0
if printf ' %s ' "$CMD" | tr -c 'A-Za-z0-9_' ' ' | tr -s ' ' \
        | grep -qE ' do( .*)? sleep( .*)? done '; then
    IS_LOOP=1
fi

# ---- bounded backstop: only ever AFTER a real block on this key --------
# A long `sleep` before the status read is the same wait written without a
# loop (`sleep 300 && gh run view <id>`), so it gets the same block.
#
# It keys on the SLEEP ALONE. A first draft also treated "two or more
# `gh run view` in one command" as a wait, and the stage-2 replay (all 56,038
# commands of the 185 blocked sessions) showed why that is wrong: of 139
# backstop firings only 27 came from a long sleep, and all 112 others were
# post-mortems — `gh run view <id> --json jobs --jq 'select(.conclusion==
# "failure")'` followed by `gh run view <id> --log-failed`. That is reading
# WHY CI failed, i.e. the actual work, and blocking it would strand the
# session on a red run. Density measures verbosity, not waiting.
#
# A SINGLE status check stays free in every state (3,684 of them in those same
# sessions, none blocked) — the model must always be able to learn the run
# finished, so this hook can never dead-end a session.
IS_BURST=0
if [ -e "$BLOCKED_FILE" ] && [ "$IS_LOOP" = "0" ]; then
    MIN_BURST="${AIRULESET_CIPOLL_BURST_SLEEP_S:-60}"
    case "$MIN_BURST" in ''|*[!0-9]*) MIN_BURST=60 ;; esac
    LONG_SLEEP=$(printf '%s' "$FLAT" \
        | grep -oE '(^|[^A-Za-z0-9_-])sleep[[:space:]]+[0-9]+' \
        | grep -oE '[0-9]+$' | sort -rn | head -1 || true)
    if [ -n "$LONG_SLEEP" ] && [ "$LONG_SLEEP" -ge "$MIN_BURST" ]; then
        IS_BURST=1
    fi
fi

# ---- #210: bare one-shot status-poll counting --------------------------
# Engages for a RUN-KEYED command (KEY="run-<id>") AND for the generic bucket
# (KEY="generic") — a bare `gh run view` with NO run-id argument at all
# defaults to the latest run on the current branch and is a real status-poll
# shape too; it used to evade this counter entirely because this whole block
# was gated on a non-empty $RUN_ID. Both share the SAME per-session generic
# bucket the loop mechanism already keys its own generic state on — a
# different file prefix (oneshot- vs first-/blocked-), so no collision. A
# loop-shaped command is governed by IS_LOOP above, never here.
IS_ONESHOT_BLOCK=0
if [ "$IS_LOOP" = "0" ]; then
    # `|| true`: under `set -e` a failing command substitution used in a
    # plain assignment kills the whole hook (this repo's own documented
    # gotcha) — a command with ZERO "gh run view" occurrences (e.g. "gh run
    # list ..." or "gh run watch <id>") makes the grep exit non-zero, and
    # this branch is now reached for every non-loop CI-wait command, not just
    # run-id-bearing ones (#210 review follow-up widened it to the generic
    # bucket too).
    VIEW_COUNT=$(grep -oE 'gh[[:space:]]+run[[:space:]]+view' <<<"$FLAT" \
        | wc -l | tr -d ' ' || true)
    case "$VIEW_COUNT" in ''|*[!0-9]*) VIEW_COUNT=0 ;; esac
    HAS_LOG=0
    if grep -qE -- '--log(-failed)?([[:space:]]|=|$)' <<<"$FLAT"; then
        HAS_LOG=1
    fi
    HAS_JSON=0
    if grep -qE -- '--json' <<<"$FLAT"; then
        HAS_JSON=1
    fi
    JSON_STATUS_OK=0
    if grep -qE -- \
        '--json[[:space:]]+"?(status,conclusion|conclusion,status|status)(,jobs)?"?([[:space:]"'"'"'|]|$)' \
        <<<"$FLAT"; then
        JSON_STATUS_OK=1
    fi
    VIEW_ANCHOR_OK=0
    if [ -n "$RUN_ID" ]; then
        # the view command must be watching THIS specific run
        if grep -qE "gh[[:space:]]+run[[:space:]]+view[[:space:]]+${RUN_ID}([[:space:]]|\$)" \
                <<<"$FLAT"; then
            VIEW_ANCHOR_OK=1
        fi
    else
        # no numeric run-id anywhere in the command (a real one would already
        # have been extracted as $RUN_ID above) — `view` immediately followed
        # by a flag, or by nothing at all, is the bare "latest run" shape.
        if grep -qE "gh[[:space:]]+run[[:space:]]+view([[:space:]]+-|[[:space:]]*\$)" \
                <<<"$FLAT"; then
            VIEW_ANCHOR_OK=1
        fi
    fi
    STATUS_SHAPE=0
    if [ "$VIEW_COUNT" = "1" ] && [ "$HAS_LOG" = "0" ] \
        && { [ "$HAS_JSON" = "0" ] || [ "$JSON_STATUS_OK" = "1" ]; } \
        && [ "$VIEW_ANCHOR_OK" = "1" ]; then
        STATUS_SHAPE=1
    fi
    if [ "$STATUS_SHAPE" = "1" ]; then
        ONESHOT_FREE="${AIRULESET_CIPOLL_ONESHOT_FREE:-2}"
        case "$ONESHOT_FREE" in ''|*[!0-9]*) ONESHOT_FREE=2 ;; esac
        ONESHOT_TTL="${AIRULESET_CIPOLL_ONESHOT_TTL_S:-1800}"
        case "$ONESHOT_TTL" in ''|*[!0-9]*) ONESHOT_TTL=1800 ;; esac
        ONESHOT_FILE="$STATE_DIR/airuleset-cipoll-oneshot-${SID}-${KEY}"
        # A sliding TTL window, mirroring the generic loop bucket's own decay
        # style: no dead end for the promised single-status-check escape — a
        # run-id whose oneshots have gone quiet for ONESHOT_TTL genuinely
        # stopped being a rapid-fire burn, so the next touch starts fresh.
        OS_COUNT=0
        if [ -e "$ONESHOT_FILE" ]; then
            NOW=$(date +%s)
            MTIME=$(stat -c %Y "$ONESHOT_FILE" 2>/dev/null || echo "$NOW")
            if [ $((NOW - MTIME)) -lt "$ONESHOT_TTL" ]; then
                OS_COUNT=$(cat "$ONESHOT_FILE" 2>/dev/null || echo 0)
                case "$OS_COUNT" in ''|*[!0-9]*) OS_COUNT=0 ;; esac
            fi
        fi
        OS_COUNT=$((OS_COUNT + 1))
        printf '%s' "$OS_COUNT" > "$ONESHOT_FILE" 2>/dev/null || true
        if [ "$OS_COUNT" -gt "$ONESHOT_FREE" ]; then
            IS_ONESHOT_BLOCK=1
        fi
    fi
fi

if [ "$IS_LOOP" = "0" ] && [ "$IS_BURST" = "0" ] && [ "$IS_ONESHOT_BLOCK" = "0" ]; then
    exit 0
fi

# ---- narrowing 2: the generic bucket only ever blocks a LONG wait ------
# With no run-id there is nothing to prove two loops are the SAME wait. Corpus
# replay (#118): 31 generic blocks were `gh run list`-driven "wait for the new
# run to APPEAR" loops (sleep 8-15, seconds to a minute) — and five consecutive
# ones in one restreamer session were five DIFFERENT PRs, i.e. five legitimate
# first waits. So the generic bucket additionally requires a long sleep
# interval; an unmeasurable one (`sleep "$T"`) is treated as short, because
# this bucket must never be the thing that stops real work. A run-KEYED repeat
# is unaffected at any interval — the id already proves it is the same run.
# NEVER applied to a oneshot block: a bare status-poll one-shot has no sleep
# at all by definition, and it is already bounded by its own count+TTL above.
if [ "$KEY" = "generic" ] && [ "$IS_ONESHOT_BLOCK" = "0" ]; then
    MIN_SLEEP="${AIRULESET_CIPOLL_GENERIC_MIN_SLEEP_S:-20}"
    case "$MIN_SLEEP" in ''|*[!0-9]*) MIN_SLEEP=20 ;; esac
    MAX_SLEEP=$(printf '%s' "$FLAT" \
        | grep -oE '(^|[^A-Za-z0-9_-])sleep[[:space:]]+[0-9]+' \
        | grep -oE '[0-9]+$' | sort -rn | head -1 || true)
    if [ -z "$MAX_SLEEP" ] || [ "$MAX_SLEEP" -lt "$MIN_SLEEP" ]; then
        log_exempt "generic-short-wait(sleep=${MAX_SLEEP:-none})"
        exit 0
    fi
fi

# ---- the carve-out: loop 1 per key is free -----------------------------
# Gated on LOOP_BLOCKED_FILE, never the general BLOCKED_FILE — a oneshot
# block must never consume this (review follow-up on #210, see the top-of
# -file note and the LOOP_BLOCKED_FILE declaration above).
if [ "$IS_LOOP" = "1" ] && [ ! -e "$LOOP_BLOCKED_FILE" ]; then
    FRESH=1
    if [ -e "$FIRST_FILE" ]; then
        FRESH=0
        # The generic bucket (no run-id) decays, so two unrelated CI waits an
        # hour apart are not one burst; a run-keyed bucket never does, because
        # re-polling the SAME run later is still the same long wait.
        if [ "$KEY" = "generic" ]; then
            TTL="${AIRULESET_CIPOLL_GENERIC_TTL_S:-1800}"
            case "$TTL" in ''|*[!0-9]*) TTL=1800 ;; esac
            NOW=$(date +%s)
            MTIME=$(stat -c %Y "$FIRST_FILE" 2>/dev/null || echo "$NOW")
            [ $((NOW - MTIME)) -ge "$TTL" ] && FRESH=1
        fi
    fi
    if [ "$FRESH" = "1" ]; then
        : > "$FIRST_FILE" 2>/dev/null || true
        exit 0
    fi
fi

# ---- BLOCK -------------------------------------------------------------
: > "$BLOCKED_FILE" 2>/dev/null || true
BLOCK_SHAPE="burst"
if [ "$IS_LOOP" = "1" ]; then
    BLOCK_SHAPE="loop"
elif [ "$IS_ONESHOT_BLOCK" = "1" ]; then
    BLOCK_SHAPE="oneshot"
fi
# Only a LOOP or BURST block may consume the #118 free-first-loop carve-out —
# a oneshot block never does (that is the whole point of the split state).
if [ "$BLOCK_SHAPE" != "oneshot" ]; then
    : > "$LOOP_BLOCKED_FILE" 2>/dev/null || true
fi
{ printf '%s cipoll BLOCK session=%s key=%s agent=%s shape=%s cmd=%s\n' \
    "$(date -Is)" "$SID" "$KEY" "${AGENT_ID:-main}" \
    "$BLOCK_SHAPE" "$SNIPPET" \
    >> "$BLOCK_LOG"; } 2>/dev/null || true

# The compliant command must be paste-ready. With a run-id, substitute it. In
# the generic bucket there is none, so the waiter resolves it itself with the
# same `gh run list` the blocked loop was already using — still zero-thought,
# never a `<placeholder>` the model has to stop and fill in.
NL=$'\n'
if [ -n "$RUN_ID" ]; then
    ID_FOR_MSG="$RUN_ID"
    WAITER_TARGET="$RUN_ID"
    PRELUDE=""
else
    ID_FOR_MSG="the run you are watching"
    WAITER_TARGET="\$RID"
    PRELUDE="RID=\$(gh run list -L 1 --json databaseId --jq '.[0].databaseId')${NL}  "
fi

if [ "$IS_ONESHOT_BLOCK" = "1" ]; then
    # #210: N one-shot polls have already gone by, one per TURN, with no
    # loop's non-terminal-return evidence that the wait is long — so the
    # message offers BOTH shapes and lets the caller pick, rather than
    # assuming (as the loop-repeat message can) that the wait is long.
    if [ -n "$AGENT_ID" ]; then
        MSG=$(cat <<'ONESHOT_SUBMSG'
BLOCKED (airuleset #210): this is the 3rd+ ONE-SHOT foreground CI status poll
for __RUNID__ in this session — each one its OWN turn, re-sending your whole
context for a single line of status (production burn: one dispatch did 157
one-shot `gh run view` calls across 35 separate turns polling ONE run).

You are a SUBAGENT. Do NOT launch a background waiter — a subagent with no
pending foreground tool call is returned as "completed" and TERMINATES, so the
poll's completion would fire to your parent and your work would silently die.

Do this instead, now:

  • Wait is probably SHORT: run ONE bounded foreground loop in a single Bash
    call (covers up to ~9 minutes — see ci-monitoring.md's "Foreground
    bounded poll loop").
  • Wait is LONG / multi-stage: report the run-id and current stage in your
    FINAL message and RETURN. The supervisor owns long waits and will
    re-dispatch a fresh worker for the next stage.

This exact check is what just got blocked — it decays: once a short quiet
period passes with no further polls on this run, a plain status check works
again on its own:
  gh run view __RUNID__ --json status,conclusion
Deliberate exception (logged): append `# airuleset:poll-ok <reason>`.
ONESHOT_SUBMSG
)
    else
        MSG=$(cat <<'ONESHOT_MAINMSG'
BLOCKED (airuleset #210): this is the 3rd+ ONE-SHOT foreground CI status poll
for __RUNID__ in this session — each one its OWN turn, re-sending your whole
context for a single line of status (production burn: one dispatch did 157
one-shot `gh run view` calls across 35 separate turns polling ONE run).

Pick ONE of these and stick to it — never keep issuing bare one-shot polls:

  • SHORT wait — ONE bounded foreground loop in a single Bash call:

  DEADLINE=$((SECONDS + ${AIRULESET_POLL_BUDGET_S:-540}))
  for i in $(seq 1 18); do
    s=$(gh run view __RUNID__ --json status,conclusion,jobs --jq 'if .status=="completed" then "TERMINAL "+.status+" "+(.conclusion//"") elif ([.jobs[]?|select(.conclusion=="failure" or .conclusion=="timed_out")]|length)>0 then "JOBFAIL "+([.jobs[]?|select(.conclusion=="failure" or .conclusion=="timed_out")]|map(.name)|join(", ")) else "PENDING "+.status end')
    case "$s" in
      "TERMINAL "*) echo "TERMINAL: ${s#TERMINAL }"; break;;
      "JOBFAIL "*) echo "JOB FAILED (run still in progress): ${s#JOBFAIL }"; break;;
    esac
    if [ "$SECONDS" -ge "$DEADLINE" ]; then echo "POLL BUDGET REACHED"; break; fi
    sleep 30
  done

  • LONG wait — ONE background waiter, `run_in_background: true`:

  timeout "${AIRULESET_LONG_POLL_BUDGET_S:-10800}" bash -c 'while :; do
    s=$(gh run view __TARGET__ --json status,conclusion,jobs --jq "if .status==\"completed\" then \"TERMINAL \"+.status+\" \"+(.conclusion//\"\") elif ([.jobs[]?|select(.conclusion==\"failure\" or .conclusion==\"timed_out\")]|length)>0 then \"JOBFAIL \"+([.jobs[]?|select(.conclusion==\"failure\" or .conclusion==\"timed_out\")]|map(.name)|join(\", \")) else \"PENDING \"+.status end" 2>/dev/null) || s="ERROR"
    case "$s" in
      "TERMINAL "*) echo "TERMINAL: ${s#TERMINAL }"; exit 0 ;;
      "JOBFAIL "*) echo "JOB FAILED (run still in progress): ${s#JOBFAIL }"; exit 0 ;;
    esac
    sleep 60
  done'

If you are a SUBAGENT (autopilot-worker or any dispatched worker): do NOT run
the background waiter. Backgrounding a wait TERMINATES you. Report the run-id
+ current stage in your final message and RETURN — the supervisor owns it.

This exact check is what just got blocked — it decays: once a short quiet
period passes with no further polls on this run, a plain status check works
again on its own:
  gh run view __RUNID__ --json status,conclusion
Deliberate exception (logged): append `# airuleset:poll-ok <reason>`.
ONESHOT_MAINMSG
)
    fi
elif [ -n "$AGENT_ID" ]; then
    # SUBAGENT: never offer a background waiter — launching one ends this
    # worker's life mid-CI. The long-wait contract is to hand back and return.
    MSG=$(cat <<'SUBMSG'
BLOCKED (airuleset #118): this is a REPEAT foreground CI poll for __RUNID__ in
this session. Loop 1 already came back non-terminal — that is measured proof
the wait is LONG, and chaining 9-minute foreground polls re-sends your whole
context once per poll (the 2026-07-28 burn: ~13 such turns on one run).

You are a SUBAGENT. Do NOT launch a background waiter — a subagent with no
pending foreground tool call is returned as "completed" and TERMINATES, so the
poll's completion would fire to your parent and your work would silently die.

Do this instead, now:

  • report the run-id and the current stage in your FINAL message and RETURN.
    The supervisor owns long / multi-stage waits (ci-monitoring.md); it will
    re-dispatch a fresh worker for the next stage.

Still allowed right now: ONE plain status check —
  gh run view __RUNID__ --json status,conclusion
(repeating that single check many times throttles too, then decays after a
short quiet period — see #210 — it is never a permanent dead end)
Deliberate exception (logged): append `# airuleset:poll-ok <reason>`.
SUBMSG
)
else
    MSG=$(cat <<'MAINMSG'
BLOCKED (airuleset #118): this is a REPEAT foreground CI poll for __RUNID__ in
this session. Loop 1 already came back non-terminal — that is measured proof
the wait is LONG. Every further 9-minute foreground poll is another TURN that
re-sends your entire context for one line of status (the 2026-07-28 burn: ~13
such turns on a single 2-hour run).

Run THIS instead — ONE background waiter, `run_in_background: true`, which
blocks to a terminal state and wakes you exactly once:

  __PRELUDE__timeout "${AIRULESET_LONG_POLL_BUDGET_S:-10800}" bash -c 'while :; do
    s=$(gh run view __TARGET__ --json status,conclusion,jobs --jq "if .status==\"completed\" then \"TERMINAL \"+.status+\" \"+(.conclusion//\"\") elif ([.jobs[]?|select(.conclusion==\"failure\" or .conclusion==\"timed_out\")]|length)>0 then \"JOBFAIL \"+([.jobs[]?|select(.conclusion==\"failure\" or .conclusion==\"timed_out\")]|map(.name)|join(\", \")) else \"PENDING \"+.status end" 2>/dev/null) || s="ERROR"
    case "$s" in
      "TERMINAL "*) echo "TERMINAL: ${s#TERMINAL }"; exit 0 ;;
      "JOBFAIL "*) echo "JOB FAILED (run still in progress): ${s#JOBFAIL }"; exit 0 ;;
    esac
    sleep 60
  done'

On your NEXT turn, re-derive status from the durable resource (`gh run view
__TARGET__`) rather than trusting silence, and relaunch ONE waiter if the old
one is gone — a compaction boundary can orphan the notification handle.

If you are a SUBAGENT (autopilot-worker or any dispatched worker): do NOT run
the above. Backgrounding a wait TERMINATES you. Report the run-id + current
stage in your final message and RETURN — the supervisor owns the wait.

Still allowed right now: ONE plain status check —
  gh run view __RUNID__ --json status,conclusion
(repeating that single check many times throttles too, then decays after a
short quiet period — see #210 — it is never a permanent dead end)
A bare foreground `sleep` is separately blocked by the harness, so it is not a
way around this. Deliberate exception (logged): append
`# airuleset:poll-ok <reason>`.
MAINMSG
)
fi

MSG=${MSG//__RUNID__/$ID_FOR_MSG}
MSG=${MSG//__TARGET__/$WAITER_TARGET}
MSG=${MSG//__PRELUDE__/$PRELUDE}
printf '%s\n' "$MSG" >&2
exit 2
