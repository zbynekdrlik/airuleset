# SOURCED, never executed — airuleset #124. (No shebang: this file is a library
# for the poll-loop detectors, which already run under `set -euo pipefail`;
# re-declaring shell options here would silently reach into every caller.)
#
# One classifier shared by every poll-loop detector (nudge-poll-loop-timeout.sh,
# block-ci-poll-repeat.sh, block-local-poll-repeat.sh): given a Bash command,
# return the part of it that is CONTROL FLOW, with provably-inert PAYLOAD blanked
# out. A private copy in any consumer is how the three would drift apart.
#
# WHY. Both shipped detectors match a normalised token stream of the WHOLE
# command (`tr -c 'A-Za-z0-9_' ' '`), which has no notion of what the shell
# executes and what it merely CARRIES. So a ticket body, playbook entry or
# completion report that QUOTES a poll loop — and this repo now writes a lot of
# those — reads as a poll loop. Measured live on the shipped code: a heredoc
# write quoting the sanctioned waiter, with no `gh issue comment` in the call,
# was a hard `exit 2` from block-ci-poll-repeat.sh on its SECOND occurrence in a
# session. Same failure class as #80 (`grep -c` read as a bulk read) and #111.
#
# WHY NOT JUST STRIP EVERY HEREDOC BODY — the fix #112 closed won't-fix, and it
# was right to. Re-measured over 8,107 transcripts / 251,551 Bash commands: of
# 147 foreground loop-matches that vanish when EVERY body is stripped, 75 are
# LIVE — 55 are `cat > x.sh <<EOF … EOF` followed by RUNNING that script in the
# same command, 20 are interpreter bodies (`python3 - <<PY`, `ssh host 'bash -s'
# <<EOF`). Blanket stripping deletes the majority. #112 also killed its own
# proposed discriminator ("strip unless the heredoc's owner is a shell or
# interpreter") on exactly that class: `cat` is not an interpreter, yet those 55
# bodies run.
#
# WHAT #112 SAID A CORRECT CLASSIFIER WOULD NEED, and dismissed as too hard:
# "follow the written PATH to a later execution". Within ONE command that is
# neither hard nor a heuristic, and it is this whole fix. A body is blanked only
# when ALL THREE hold:
#   1. its owner line names no interpreter (covers `python3 - <<PY`, `ssh host
#      'bash -s' <<EOF`, sudo/env/xargs/eval wrappers),
#   2. it is a plain redirect to a LITERAL file path (a `$VAR` target cannot be
#      resolved, so it counts as live), and
#   3. every later mention of that path in the same command's SHELL-VISIBLE text
#      is the argument of a text-file flag.
# Anything it cannot prove inert is left alone.
#
# THE REAPPEARANCE RULE IS DELIBERATELY NOT AN ENUMERATION OF RUNNERS. A first
# draft asked "is this file executed here?" by listing `bash <path>`, `./path`,
# `chmod +x` — and called `sshpass ssh host 'bash -s' < /tmp/x.sh` inert, three
# real corpus specimens that ran 119 s, 135 s and 183 s, because that shape names
# no interpreter beside the path. A rule whose safety depends on having listed
# every way a file can be executed is the wrong shape; this one needs no list.
#
# EVERY EDIT IS POSITIONAL, NEVER BY VALUE. Three review-found ways a
# value-based `str.replace` deletes live control flow, each reproduced:
#   • a short flag value that is also a loop token — `until grep -F "done" log;
#     do sleep 30; done` lost its own `done` keyword and went silent;
#   • two heredocs with byte-identical bodies (document the script, then write
#     and run it) — one verdict blanked both;
#   • a here-STRING (`<<<'x'`) matching the heredoc pattern, whose never-closed
#     "body" swallowed the rest of the command.
# So bodies are blanked by LINE INDEX, quoted arguments by CHARACTER OFFSET, and
# the heredoc pattern carries a `(?<!<)` guard.
#
# QUOTED ARGUMENTS are scoped to the flag's OWN command segment. Stripping
# quoted spans wholesale would silence `bash -c 'while :; do …; sleep 60; done'`
# — the sanctioned waiter itself — so `-c` is absent from the flag list; and
# `-m`/`-t` are honoured only in a segment that names `git`/`gh` and names no
# interpreter, because `ssh -t '<loop>'` EXECUTES its argument remotely and a
# whole-command git/gh test licensed stripping it whenever an unrelated
# `git status` appeared elsewhere.
#
# FAIL-OPEN: any error returns the command UNCHANGED, so a broken stripper can
# only ever cost a false nudge, never a missed real loop. Callers must source
# this file defensively (`[ -r "$LIB" ] && . "$LIB"`) — under `set -e` a failed
# source exits the hook non-zero, which the harness reports as a hook ERROR on
# every Bash call.

# The command is passed by FILE, not argv: a single argv entry is capped by
# Linux MAX_ARG_STRLEN (measured: exec succeeds at 131,000 bytes, E2BIG at
# 140,000), and past that the strip would fail open and #124 would reproduce
# precisely for the LARGEST documentation writes — the class this exists for.
poll_payload_strip() {
    local _f _out
    _f=$(mktemp 2>/dev/null) || { printf '%s' "$1"; return 0; }
    if ! printf '%s' "$1" > "$_f" 2>/dev/null; then
        rm -f "$_f" 2>/dev/null || true
        printf '%s' "$1"
        return 0
    fi
    _out=$(python3 "$(dirname "${BASH_SOURCE[0]}")/lib_poll_payload.py" "$_f" 2>/dev/null) || _out=""
    rm -f "$_f" 2>/dev/null || true
    if [ -n "$_out" ]; then printf '%s' "$_out"; else printf '%s' "$1"; fi
}

# Cheap pre-gate: only pay for the stripper when the command actually carries
# something payload-shaped. Every Bash tool call goes through the detectors, so
# an unconditional python3 fork would be a real cost for nothing.
poll_payload_carrier() {
    case "$1" in
        *'<<'*|*--body*|*--notes*|*--message*|*--title*) return 0 ;;
        *' -b '*|*' -F '*|*' -m '*|*' -t '*) return 0 ;;
        *) return 1 ;;
    esac
}
