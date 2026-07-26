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
# Bypass (rare, logged, ONE-SHOT since #80 — honoring the marker DELETES it,
# so one marker exempts exactly one call): touch
# /tmp/airuleset-main-exec-ok-<session_id>
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
#
# #80 (gatekeeper measurement, 2026-07-26) — the classifier's own FALSE
# POSITIVE is what disabled all of #66. `cat > body.md <<'EOF' ... EOF` (the
# recipe gh-cli-recipes.md MANDATES for every issue/PR body, `-F body.md`,
# never an inline `--body`) classified as a BULK READ on its `cat` head
# token. gk's main hit it 58× in a day, armed the bypass marker at 01:24,
# and the hook was dead for the next 17 hours (332 bypass lines, 304 of them
# Bash — `/tmp/airuleset-main-exec-block.log` never even got created, which
# is the observation #80 was filed on). Two fixes in the classifier:
#   • heredoc BODIES are stripped BEFORE segmentation (`strip_heredocs()`,
#     the same shape block-gh-invalid-json-flag.sh already uses) — a body is
#     payload text and routinely CONTAINS command-shaped prose;
#   • a segment whose STDOUT is redirected to a FILE returns nothing to the
#     model, so it is not the context cost this hook guards — it is a WRITE
#     and it passes (`>`, `>>`, `1>`; `2>`/`>&` are NOT stdout-to-file and
#     exempt nothing).
# A genuine bulk read whose output DOES come back (`cat file`, `sed -n
# '1,900p' file`, `grep -rn x .`) is unchanged — still blocked.
#
# #80 also changed WHAT is measured. The command's CLASS was the wrong
# variable: every main turn re-sends the whole 256-363K context, so a
# `gh issue view` costs the same as a `grep`, and gk's ratio after #66 was
# UNCHANGED (main Bash 687 : Agent 22 = 31:1 on 2026-07-26, ten hours with
# zero dispatches, runs of up to 119 Bash calls between two dispatches).
# The lever is the COUNT of main-agent Bash turns. So on top of the
# classification above there is now a per-dispatch COUNTER
# (/tmp/airuleset-main-bash-run-<session_id>): every allow-listed/ambiguous
# main Bash call in a goal-armed/Fable session increments it, a DISPATCH
# (PreToolUse Agent/Task/Workflow — the hook is wired on those matchers too,
# exact tool names, never a regex that could silently never match) deletes
# it, and passing AIRULESET_MAIN_BASH_PER_DISPATCH (default 20, 0 = off)
# blocks ONCE with batching/dispatch instructions.
#
# That nudge RESETS the counter on purpose: #80's acceptance forbids any
# block that could genuinely stop the loop, so this is at most one block per
# N calls and NEVER two in a row — re-running the same command immediately
# after a nudge passes. Arming the bypass marker (`touch ...-exec-ok-<sid>`)
# is never counted and never blocked, or the cap would sit in front of the
# only documented way out of it.
#
# The ticket's direction 1 (">N gh calls per TURN → batch them") was
# REFUTED by the same measurement and deliberately NOT built: 687 of 687
# main turns carried exactly ONE Bash call, so a per-turn counter could
# never fire. Batching pressure lives in the nudge's message instead.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")
[ -z "$AGENT_ID" ] || exit 0            # subagent — execution belongs there

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || echo "")

RAW_SID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
RAW_SID=$(printf '%s' "$RAW_SID" | tr -cd 'A-Za-z0-9_-')
RUN_FILE="/tmp/airuleset-main-bash-run-${RAW_SID:-unknown}"

# ---- #80: a DISPATCH resets the per-dispatch Bash counter ----
# This is the whole point of the counter: it measures main-agent Bash calls
# since the main last delegated. Handled before ANY transcript work — a
# reset must be as cheap as possible and must happen regardless of model /
# goal state (a session that arms a goal later starts from a clean count).
case "$TOOL_NAME" in
    Agent|Task|Workflow)
        rm -f "$RUN_FILE" 2>/dev/null || true
        exit 0
        ;;
esac

if [ "$TOOL_NAME" = "Bash" ]; then
    BASH_CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
    [ -n "$BASH_CMD" ] || exit 0
    # #80: ARMING the escape hatch is never blocked and never counted —
    # otherwise the per-dispatch cap below could sit in front of the only
    # documented way out of it, which would be exactly the dead end the
    # ticket forbids. Deliberately narrow: the command must START with
    # `touch` and name one of the two marker paths.
    case "$(printf '%s' "$BASH_CMD" | tr -s ' \t' ' ' | sed 's/^ //')" in
        touch\ *airuleset-main-exec-ok-*|touch\ *airuleset-fable-exec-ok-*)
            echo "$(date -Is) main-exec bypass-arm session=$RAW_SID" \
                >> /tmp/airuleset-main-exec-bypass.log 2>/dev/null || true
            exit 0
            ;;
    esac
else
    LEN=$(echo "$INPUT" | jq -r \
        '(.tool_input.new_string // .tool_input.content // "") | length' \
        2>/dev/null || echo 0)
    MAX="${AIRULESET_FABLE_EDIT_MAX:-800}"
    [ "$LEN" -gt "$MAX" ] 2>/dev/null || exit 0     # surgical edit — oversight
fi

SESSION_ID="${RAW_SID:-unknown}"

# #80: the marker is ONE-SHOT — honoring it CONSUMES it. "Deliberate
# exception" means one deliberate action, not "disable the guard for the
# rest of the session" (gk, 2026-07-26: one touch at 01:24 → 332 unguarded
# calls). Re-touching always works, so the escape hatch never dead-ends;
# the cost of abuse just grows with the abuse instead of being paid once.
BYPASS_MARK=""
BYPASS_FILE=""
if [ -e "/tmp/airuleset-main-exec-ok-${SESSION_ID:-unknown}" ]; then
    BYPASS_MARK="main-exec-ok"
    BYPASS_FILE="/tmp/airuleset-main-exec-ok-${SESSION_ID:-unknown}"
elif [ -e "/tmp/airuleset-fable-exec-ok-${SESSION_ID:-unknown}" ]; then
    BYPASS_MARK="fable-exec-ok(legacy)"
    BYPASS_FILE="/tmp/airuleset-fable-exec-ok-${SESSION_ID:-unknown}"
fi
if [ -n "$BYPASS_MARK" ]; then
    rm -f "$BYPASS_FILE" 2>/dev/null || true
    echo "$(date -Is) main-exec bypass session=$SESSION_ID tool=$TOOL_NAME marker=$BYPASS_MARK (consumed)" \
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

# ---- #80: strip heredoc BODIES before anything else. A heredoc body is
# PAYLOAD text (a PR/issue body, a playbook section), never command tokens —
# and it routinely CONTAINS command-shaped prose ("reproduce with: grep -rn
# ... | head"). Segmenting on "\n" without this makes every such line a
# "command" and blocks the mandated `gh ... -F body.md` recipe. Same
# `strip_heredocs` shape block-gh-invalid-json-flag.sh already uses (one
# parser shape in this repo, never a second invented one); an unterminated
# heredoc falls through with the body left in place rather than crashing.
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def strip_heredocs(text):
    lines = text.split("\n")
    out = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        mm = _HEREDOC_RE.search(line)
        out.append(line)
        i += 1
        if not mm:
            continue
        delim = mm.group(2)
        strip_leading = "<<-" in line
        while i < n:
            body_line = lines[i]
            check = body_line.lstrip("\t") if strip_leading else body_line
            i += 1
            if check == delim:
                break
    return "\n".join(out)


cmd = strip_heredocs(cmd)

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


# #80: a segment whose STDOUT goes to a FILE returns nothing to the model,
# so it is not the context cost this hook guards — it is a WRITE, and it
# passes (`cat > body.md`, `grep ... > /tmp/out`). `2>...` is a STDERR
# redirect (the extremely common `gh ... 2>/dev/null`) and must NOT exempt
# anything.
def redirects_stdout_to_file(tk):
    for t in tk[1:]:
        if t.startswith("2>") or t.startswith(">&"):
            continue          # stderr redirect / fd dup — not a file write
        if t.startswith(">") or t.startswith("1>"):
            return True
    return False


def is_blocked_segment(tk):
    if not tk:
        return False
    if redirects_stdout_to_file(tk):
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
        # ---- #80: the command's CLASS is not the lever — the COUNT is. ----
        # Every main turn re-sends the whole context, so a `gh issue view` is
        # as expensive as a `grep`; #66 optimised the wrong variable and gk's
        # ratio was unchanged (Bash 687 : Agent 22 = 31:1, runs of up to 119
        # calls with no dispatch). Count main-agent Bash calls SINCE THE LAST
        # DISPATCH and nudge at the cap.
        #
        # The nudge RESETS the counter — deliberately. The ticket's own
        # acceptance forbids any block that could actually stop the loop, so
        # this is a periodic nudge (at most one block per N calls, never two
        # in a row), never a wall. Worst case, if the dispatch reset above
        # never fired at all, the loop still runs — one instructive block
        # every N calls.
        CAP="${AIRULESET_MAIN_BASH_PER_DISPATCH:-20}"
        case "$CAP" in ''|*[!0-9]*) CAP=20 ;; esac
        [ "$CAP" -eq 0 ] && exit 0

        RUN_N=$(cat "$RUN_FILE" 2>/dev/null || echo 0)
        case "$RUN_N" in ''|*[!0-9]*) RUN_N=0 ;; esac
        RUN_N=$((RUN_N + 1))

        if [ "$RUN_N" -le "$CAP" ]; then
            echo "$RUN_N" > "$RUN_FILE" 2>/dev/null || true
            exit 0
        fi

        rm -f "$RUN_FILE" 2>/dev/null || true      # never two blocks in a row
        log_block "per-dispatch=$RUN_N/$CAP" "$BASH_CMD"

        cat >&2 <<MSG
BLOCKED: ${REASON}, and this is main-agent Bash call #${RUN_N} since the last
DISPATCH (cap ${CAP}). The command itself is fine — the COUNT is the problem.
Every main-agent turn re-sends the WHOLE context, so a \`gh issue view\` at a
300K context costs the same as a \`grep\`; measured 2026-07-26 on gatekeeper,
the main agent ran 687 Bash calls against 22 dispatches (31:1) — that ratio,
not any single command, is the burn (#80).

Do ONE of these, then continue:

  • DISPATCH the state-gathering. Anything that is not a single fact — "what
    is the state of these five tickets", "why did that run fail", "read this
    file/log" — goes to an Agent (subagent_type: Explore or general-purpose,
    model: sonnet, effort: low/medium for a mechanical read). It brings back
    a CONCLUSION; you keep coordinating (main-context-hygiene.md). A
    dispatch resets this counter immediately.
  • BATCH the remaining reads into ONE call. Five \`gh issue view\` calls are
    one \`gh issue list --json number,title,state,labels\`; a poll is ONE
    bounded loop, not one call per tick (ci-monitoring.md).

This is a nudge, not a wall: the counter is already reset, so re-running
this exact command right now will pass. Ignoring the nudge is what the
measurement will show.

Deliberate exception (one-shot, logged): touch /tmp/airuleset-main-exec-ok-${SESSION_ID}
MSG
        exit 2
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
