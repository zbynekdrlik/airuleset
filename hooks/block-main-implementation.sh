#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Edit | Write | Bash) — airuleset #32, generalized by #54,
# generalized again by #66 to also guard Bash.
#
# A MAIN session (no agent_id) is a COORDINATOR, not an implementer — under
# TWO independent conditions, either one alone is enough to block:
#
# 1. FABLE MODEL (#32, unchanged): a main running on Fable re-reads the FULL
#    conversation at Fable prices every turn — an implementation loop there
#    (write code, run test, fix, repeat) is the single biggest burn the user
#    has (3 Max subscriptions exhausted; the presenter session implemented a
#    whole issue in its Fable main, 2026-07-24, despite the ADVISOR-shape
#    rule in prose).
# 2. ARMED /goal (#54, new): the autopilot contract is main = coordinator,
#    dispatched WORKER = implementer, on ANY model — Opus, Sonnet, whatever.
#    Measured live (david@subdev, odoo-erp transcript): a goal-armed Opus
#    main did 354 direct Edits + 56 Writes alongside 229 Agent dispatches;
#    context grew 0 -> 271K in ~7 minutes of inline work after a /compact.
#    That is the exact "main writes code instead of dispatching a worker"
#    complaint this hook now blocks regardless of model.
#
# 3. #66 — Bash is ALSO now guarded, not just Edit/Write. Measured
#    2026-07-26 (loop_health.py, gatekeeper session, 08:00 fleet-burn hour):
#    the goal-armed MAIN agent ran 1222 Bash calls vs only 97 subagent
#    dispatches in one hour, at a 212K-avg context — EVERY one of those
#    Bash calls re-sends the whole context, making raw Bash turn-count the
#    dominant remaining cost lever now that context SIZE itself is handled
#    (compaction, #65/#67/#69). Unlike Edit/Write (gated by payload SIZE),
#    Bash is gated by an ALLOW-LIST / BLOCK-LIST classification (see
#    `classify_bash()` below) — a short constant-output gh/git/airuleset/
#    tmux/systemctl coordination call always passes even while
#    goal-armed/Fable; a bulk read/search/build/test/log-scrape command is
#    blocked ONLY while goal-armed/Fable; anything matching NEITHER list is
#    ambiguous and stays ALLOWED (conservative by design — the user's
#    explicit instruction: never break a legitimate gh/git coordination
#    call the loop depends on; when in doubt, allow).
#
# So: a MAIN-session Edit/Write whose written content exceeds
# AIRULESET_FABLE_EDIT_MAX (~800 chars), OR a MAIN-session Bash command that
# classify_bash() marks BLOCK, is BLOCKED when EITHER Fable-main or
# goal-armed holds. Small edits and allow-listed/ambiguous Bash commands
# pass (oversight/coordination is legitimate). Subagents ALWAYS pass — a
# subagent's payload carries `agent_id`; execution is exactly what belongs
# there.
#
# Fable-model detection (unchanged from #32): the LAST real assistant
# entry's `"model"` in the transcript tail (the /model choice can change
# mid-session; see the KNOWN caveat below). Fail-open: unreadable transcript
# / unknown model / no jq → allow.
#
# Goal-armed detection (#54): reads the SESSION TRANSCRIPT, never a pane
# capture — a hook has no reliable pane access, only the payload's
# `transcript_path`. Claude Code itself writes a plain
# `<local-command-stdout>Goal set: ...` / `Goal cleared: ...` marker as a
# top-level "user"/"system" transcript entry whenever `/goal` arms or
# resolves/clears. The LATEST such marker in the file decides: "set" with no
# later "cleared" = armed. Deliberately restricted to TOP-LEVEL string
# content (`.message.content` for "user", `.content` for "system") — NEVER
# inside a `tool_result` array entry — so a session that greps or pastes
# ANOTHER session's transcript (containing the same marker text) is never
# mistaken for its OWN goal state. No byte/line bound: an armed goal can
# have been set arbitrarily far back in a long session, so the whole
# transcript is scanned (grep/jq are fast; correctness matters more than
# shaving a full-file scan here).
#
# KNOWN, NOT fixed here (#38): the Fable-model detection above can read a
# STALE model off the transcript tail right after a `/model` switch,
# causing a false Fable-block. The goal-armed path added by #54 is fully
# INDEPENDENT of model detection — it never reads or depends on `MODEL` — so
# it neither triggers nor worsens #38; a stale-model false-block is exactly
# as likely (no more, no less) as it was before this change. Same applies
# to the #66 Bash classification below — it reads only `.tool_input.command`.
#
# Bash classification failure mode (#66): if python3 is unavailable or the
# classifier itself errors, the hook FAILS OPEN (allow) rather than
# blocking — a malfunctioning speculative-cost-saving feature must never be
# the reason a running loop on one of 6 managed boxes stalls on a routine
# gh/git call.
#
# Bypass (rare, logged): touch /tmp/airuleset-main-exec-ok-<session_id>
# (generalized name). The original Fable-only marker
# /tmp/airuleset-fable-exec-ok-<session_id> is STILL honored for backward
# compatibility (nothing outside this hook + its own tests referenced the
# literal path, but an old habit or a stale note shouldn't silently stop
# working).
#
# #73 (gatekeeper measurement, 2026-07-26): after #66 shipped there was no
# way to answer "did the hook ever fire, on what" — only bypasses were
# logged. Every BLOCK (Bash AND Edit/Write) is now ALSO appended to its own
# log, /tmp/airuleset-main-exec-block.log (timestamp, session, tool, which
# rule matched — FABLE / GOAL_ARMED / FABLE+GOAL_ARMED, the classifier match
# or len=N, and the first ~120 chars of the command/file) — same
# append-only style as the bypass log, via `log_block()`.
#
# #73 ALSO closed three classifier holes where the command's first token
# was neither allow- nor block-listed, so it fell into "ambiguous -> allow"
# even though it was really a bulk read/search wrapped or hidden one level
# down: a for/while LOOP BODY (`for f in a b; do cat $f; done` — the `do`/
# `then`/`else`/`elif` leader is stripped so the body classifies exactly
# like a standalone command; the loop HEADER segment stays ambiguous on
# purpose), a `timeout N` / `nice [-n N]` PREFIX WRAPPER (its own flags and
# duration/niceness argument are skipped in `strip_prefix()`), and a
# `bash -c '...'` / `sh -c '...'` (also zsh/dash) SUB-SHELL (`classify()` is
# now recursive: it finds the wrapper's `-c`/`-Xc` flag and reclassifies the
# QUOTED script string itself). The non-negotiable regression guard is the
# CI-poll shape from ci-monitoring.md — `for i in $(seq 1 18); do gh run
# view <id> ...; sleep 30; done` — which must NEVER block just for being a
# loop; its body (`gh run view ...`) is already allow-listed once `do` is
# stripped.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")
[ -z "$AGENT_ID" ] || exit 0            # subagent — execution belongs there

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || echo "")

if [ "$TOOL_NAME" = "Bash" ]; then
    BASH_CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
    [ -n "$BASH_CMD" ] || exit 0
else
    LEN=$(echo "$INPUT" | jq -r \
        '(.tool_input.new_string // .tool_input.content // "") | length' \
        2>/dev/null || echo 0)
    MAX="${AIRULESET_FABLE_EDIT_MAX:-800}"
    [ "$LEN" -gt "$MAX" ] 2>/dev/null || exit 0     # surgical edit — oversight
fi

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
SESSION_ID=$(printf '%s' "$SESSION_ID" | tr -cd 'A-Za-z0-9_-')

BYPASS_MARK=""
if [ -e "/tmp/airuleset-main-exec-ok-${SESSION_ID:-unknown}" ]; then
    BYPASS_MARK="main-exec-ok"
elif [ -e "/tmp/airuleset-fable-exec-ok-${SESSION_ID:-unknown}" ]; then
    BYPASS_MARK="fable-exec-ok(legacy)"
fi
if [ -n "$BYPASS_MARK" ]; then
    echo "$(date -Is) main-exec bypass session=$SESSION_ID tool=$TOOL_NAME marker=$BYPASS_MARK" \
        >> /tmp/airuleset-main-exec-bypass.log 2>/dev/null || true
    exit 0
fi

TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
[ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ] || exit 0

# ---- condition 1: Fable-model main (#32) ----
# newest claude-* model in the transcript tail = the session's CURRENT model
MODEL=$(tail -c 400000 "$TRANSCRIPT" 2>/dev/null \
    | grep -oE '"model"[[:space:]]*:[[:space:]]*"claude-[a-z0-9.-]+"' \
    | tail -1 | grep -oE 'claude-[a-z0-9.-]+' || echo "")
IS_FABLE=0
case "$MODEL" in
    claude-fable-*) IS_FABLE=1 ;;
esac

# ---- condition 2: armed /goal main (#54) ----
GOAL_MARK=$(jq -r '
    if .type == "user" and (.message.content | type) == "string" then .message.content
    elif .type == "system" and (.content | type) == "string" then .content
    else empty end
' "$TRANSCRIPT" 2>/dev/null \
    | grep -oE '<local-command-stdout>Goal (set|cleared):' | tail -1 || echo "")
GOAL_ARMED=0
case "$GOAL_MARK" in
    *"Goal set:") GOAL_ARMED=1 ;;
esac

if [ "$IS_FABLE" != "1" ] && [ "$GOAL_ARMED" != "1" ]; then
    exit 0                               # neither condition holds — allow
fi

if [ "$GOAL_ARMED" = "1" ] && [ "$IS_FABLE" = "1" ]; then
    REASON="this MAIN session runs FABLE *and* has an ARMED /goal"
    RULE_TAG="FABLE+GOAL_ARMED"
elif [ "$GOAL_ARMED" = "1" ]; then
    REASON="this MAIN session has an ARMED /goal"
    RULE_TAG="GOAL_ARMED"
else
    REASON="this MAIN session runs FABLE"
    RULE_TAG="FABLE"
fi

# ---- #73: log EVERY block (not just bypasses) — same style/location as the
# bypass log, so "did the hook ever fire, on what" is answerable from a log
# instead of an unmeasurable correlation (the exact gap #73 was filed for).
log_block() {
    # $1 = match/detail string (classifier match for Bash, len=N for Edit/Write)
    local detail snippet
    detail="$1"
    snippet=$(printf '%s' "$2" | jq -Rr '.[0:120]' 2>/dev/null \
        || printf '%s' "$2" | cut -c1-120)
    printf '%s main-exec BLOCK session=%s tool=%s rule=%s match=%s cmd=%s\n' \
        "$(date -Is)" "$SESSION_ID" "$TOOL_NAME" "$RULE_TAG" "$detail" "$snippet" \
        >> /tmp/airuleset-main-exec-block.log 2>/dev/null || true
}

# ---- Bash path (#66): classify, don't size-gate ----
if [ "$TOOL_NAME" = "Bash" ]; then
    CLASS=$(python3 - "$BASH_CMD" <<'PYEOF'
import re
import shlex
import sys

cmd = sys.argv[1]

# Same rigor level as block-history-rewrite.sh: split on shell statement/pipe
# separators, quote-aware tokenization per segment. A segment that matches
# neither list is left ambiguous (never classified) — the caller treats an
# all-ambiguous command as ALLOW.
SEGMENTS_RE = re.compile(r'&&|\|\||[;&|]|\n')

ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

# #73: `do`/`then`/`else`/`elif` are the loop/conditional-BODY leaders left
# behind once a segment is split on `;` — stripping them means the body
# classifies exactly like a standalone command (never the whole loop
# blocked/allowed just for being a loop). `for`/`while` themselves are left
# alone — a loop HEADER segment (`for f in a b`) has no command to classify
# and correctly stays ambiguous.
LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")

# `bash -c '...'` / `sh -c '...'` (also zsh/dash) wrap a REAL command in a
# quoted script string — recurse into that string, never classify the
# wrapper's own literal tokens.
DASH_C_RE = re.compile(r'^-[A-Za-z]*c$')
SHELL_WRAPPERS = ("bash", "sh", "zsh", "dash")


def strip_unquoted_comment(text):
    in_sq = in_dq = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_sq:
            if c == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if c == '\\' and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_dq = False
            i += 1
            continue
        if c == "'":
            in_sq = True
            i += 1
            continue
        if c == '"':
            in_dq = True
            i += 1
            continue
        if c == '#':
            return text[:i]
        i += 1
    return text


def tokens_of(segment):
    segment = strip_unquoted_comment(segment)
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def strip_prefix(tk):
    i = 0
    while i < len(tk):
        t = tk[i]
        if t in ("sudo", "env") or t in LOOP_BODY_KEYWORDS or ASSIGN_RE.match(t):
            i += 1
            continue
        if t == "timeout":
            # `timeout [-s SIG] [-k DUR] N cmd...` — skip the wrapper's own
            # flags and its duration argument; what remains is the real cmd.
            i += 1
            while i < len(tk) and tk[i].startswith("-"):
                i += 1
            if i < len(tk):
                i += 1               # the duration itself (e.g. "60", "30s")
            continue
        if t == "nice":
            # `nice [-n N] cmd...` (or a bare `nice -10 cmd...`)
            i += 1
            while i < len(tk) and tk[i].startswith("-"):
                flag = tk[i]
                i += 1
                if flag in ("-n", "--adjustment") and i < len(tk) \
                        and not tk[i].startswith("-"):
                    i += 1
            continue
        break
    return tk[i:]


def shell_dash_c_script(tk):
    # returns the quoted script string of a `bash -c '...'`-shaped command,
    # or None if this isn't one.
    if not tk or tk[0] not in SHELL_WRAPPERS:
        return None
    for i in range(1, len(tk)):
        if tk[i] == "-c" or DASH_C_RE.match(tk[i]):
            return tk[i + 1] if i + 1 < len(tk) else None
    return None


def is_allowed_segment(tk):
    if not tk:
        return False
    if tk[0] == "gh":
        if len(tk) >= 3 and tk[1] == "pr" and tk[2] in ("view", "list"):
            return True
        if len(tk) >= 3 and tk[1] == "issue" and tk[2] in (
                "view", "list", "create", "comment", "edit", "close"):
            return True
        if len(tk) >= 3 and tk[1] == "run" and tk[2] in ("list", "view"):
            return True
        return False
    if tk[0] == "git":
        if len(tk) >= 2 and tk[1] in ("status", "rev-parse", "fetch"):
            return True
        if len(tk) >= 2 and tk[1] == "log" and "--oneline" in tk[2:]:
            rest = tk[2:]
            has_bound = any(re.match(r'^-\d+$', t) for t in rest) or \
                "-n" in rest
            if has_bound:
                return True
        return False
    if tk[0] in ("python3", "python"):
        return any("airuleset.py" in t for t in tk[1:])
    if tk[0] == "tmux":
        return True
    if tk[0] == "systemctl":
        return "--user" in tk[1:]
    return False


def is_blocked_segment(tk):
    if not tk:
        return False
    head = tk[0]
    if head in ("grep", "rg", "ag", "find"):
        return True
    if head in ("cat", "head", "tail", "sed", "awk"):
        return True
    if head == "pytest":
        return True
    if head in ("python3", "python") and "pytest" in tk[1:]:
        return True
    if head == "cargo" and len(tk) >= 2 and tk[1] in ("test", "build"):
        return True
    if head == "npm" and len(tk) >= 2:
        if tk[1] == "test":
            return True
        if tk[1] == "run" and len(tk) >= 3 and tk[2] in ("build", "lint", "test"):
            return True
    if head == "ruff" and len(tk) >= 2 and tk[1] == "check":
        return True
    if head == "eslint":
        return True
    if head == "go" and len(tk) >= 2 and tk[1] == "test":
        return True
    if head == "mvn" and "test" in tk[1:]:
        return True
    if head == "make" and len(tk) >= 2 and tk[1] in ("test", "build"):
        return True
    if head == "journalctl":
        return True
    if head == "docker" and len(tk) >= 2 and tk[1] == "logs":
        return True
    return False


def classify(text):
    for seg in SEGMENTS_RE.split(text):
        tk = strip_prefix(tokens_of(seg))
        script = shell_dash_c_script(tk)
        if script is not None:
            inner = classify(script)
            if inner:
                return inner
            continue
        if is_allowed_segment(tk):
            continue
        if is_blocked_segment(tk):
            return " ".join(tk[:3]) if tk else seg.strip()
    return None


blocked_reason = classify(cmd)

if blocked_reason:
    print(blocked_reason)
    sys.exit(2)
sys.exit(0)
PYEOF
    ) || CLASS_RC=$?
    CLASS_RC=${CLASS_RC:-0}

    if [ "$CLASS_RC" -ne 2 ]; then
        exit 0            # allow-listed, ambiguous, or classifier malfunction (fail-open)
    fi

    log_block "$CLASS" "$BASH_CMD"

    cat >&2 <<MSG
BLOCKED: ${REASON} and this Bash command ('${CLASS}...') is a BULK
read/search/build/test/log-scrape operation. Under the autopilot contract
the MAIN session COORDINATES — short gh/git/airuleset/tmux/systemctl calls
are fine, but reading/searching/building/testing belongs to a WORKER that
takes its own context with it (model-awareness.md ADVISOR shape; measured
2026-07-26: gatekeeper's main ran 1222 Bash calls vs 97 dispatches in one
hour, each re-sending the whole context — #66):

  • dispatch an Explore/general-purpose subagent (model: sonnet, low/medium
    effort for a mechanical read) and take back its CONCLUSION, not the raw
    dump — main-context-hygiene.md.
  • then act on the conclusion here — that is the coordinator's job.

Deliberate exception (logged): touch /tmp/airuleset-main-exec-ok-<session_id>
MSG
    exit 2
fi

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || echo "")
log_block "len=$LEN" "$FILE_PATH"

cat >&2 <<MSG
BLOCKED: ${REASON} and this ${LEN}-char write is IMPLEMENTATION work. Under
the autopilot contract the MAIN session COORDINATES — decisions, oversight,
short surgical edits (under ${MAX} chars) — a dispatched WORKER types settled
code (model-awareness.md ADVISOR shape; the /goal generalization is #54,
david@subdev inline-354-edits incident):

  • dispatch the implementation to a worker NOW — an Agent
    (subagent_type: general-purpose, model: sonnet, effort: high) whose
    prompt carries the FULL context you hold (files, decisions, exact
    diffs to make, test expectations) — "I have it in my head" is not a
    reason; the prompt is how the head is handed over. For issue-shaped
    work under an armed /goal use the autopilot-worker; for plan execution
    use superpowers:subagent-driven-development.
  • then REVIEW the worker's diff here — that is the coordinator's job.

Deliberate exception (logged): touch /tmp/airuleset-main-exec-ok-<session_id>
MSG
exit 2
