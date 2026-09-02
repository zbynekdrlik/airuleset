#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Edit | Write | Bash) — airuleset #32, generalized by #54,
# generalized again by #66 to also guard Bash.
#
# A MAIN session (no agent_id) is a COORDINATOR, not an implementer — under
# THREE independent conditions, any one alone is enough to block (the third
# is #128; see the USER_AWAY block below for the measurement that added it):
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
# classify_bash() marks BLOCK, is BLOCKED when ANY of Fable-main, goal-armed
# or user-away (#128) holds. Small edits and allow-listed/ambiguous Bash commands
# pass (oversight/coordination is legitimate). Subagents ALWAYS pass — a
# subagent's payload carries `agent_id`; execution is exactly what belongs
# there.
#
# Fable-model detection (#32, staleness fixed by #38): the LAST top-level
# assistant entry's `"model"`, overridden by a later `/model` switch marker
# when one exists (see the detailed comment at the parser below). Fail-open:
# unreadable transcript / unknown model / no jq / no python3 → allow.
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
# FIXED (#38): the Fable-model detection above used to read a STALE model
# off the raw transcript tail right after a `/model` switch (the switch
# marker lands before the first new-model assistant entry is flushed),
# causing a false Fable-block in one direction and a missed Fable-block in
# the other. See the parser comment below for the fix and its residual gap.
# The goal-armed path added by #54 stays fully INDEPENDENT of model
# detection — it never reads or depends on `MODEL` — so it neither
# triggered nor worsened #38 in either state. Same applies to the #66 Bash
# classification below — it reads only `.tool_input.command`.
#
# Bash classification failure mode (#66): if python3 is unavailable or the
# classifier itself errors, the hook FAILS OPEN (allow) rather than
# blocking — a malfunctioning speculative-cost-saving feature must never be
# the reason a running loop on one of 6 managed boxes stalls on a routine
# gh/git call.
#
# Bypass (rare, logged, ONE-SHOT since #80; since #819 consumed when the
# command actually RUNS — a PostToolUse consumer, post-consume-main-exec-
# marker.sh — not the moment this guard allows it, so a sibling-hook block
# never strands the one-shot; and since #128 the marker must CARRY the
# reason, which is logged, an empty one being refused and cleared):
#   echo "<why this one call must run here>" > /tmp/airuleset-main-exec-ok-<session_id>
# Accepted residuals of the deferred consume (all bounded, fail-safe toward
# CONSUME): (a) N PARALLEL guarded calls in one turn all see the marker before
# the first one's PostToolUse fires, so one marker exempts that whole parallel
# batch (bounded to one turn, all of them RUN); (b) a user Ctrl+C mid-Bash may
# skip PostToolUse, leaving the marker one call longer; (c) a benign non-arming
# call between a sibling-blocked impl call and its retry consumes the marker
# early (one extra re-echo).
# (generalized name). The original Fable-only marker
# /tmp/airuleset-fable-exec-ok-<session_id> is STILL honored for backward
# compatibility (nothing outside this hook + its own tests referenced the
# literal path, but an old habit or a stale note shouldn't silently stop
# working).
#
# #73 (gatekeeper measurement, 2026-07-26): after #66 shipped there was no
# way to answer "did the hook ever fire, on what" — only bypasses were
# logged. Every BLOCK (Bash AND Edit/Write) is now ALSO appended to its own
# log, /tmp/airuleset-main-exec-block-<uid>.log (#492 per-user; timestamp,
# session, tool, which
# rule matched — FABLE / GOAL_ARMED / USER_AWAY, joined by '+' when more
# than one holds — the classifier match
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
# Bash — the block log (now `/tmp/airuleset-main-exec-block-<uid>.log`, #492)
# never even got created, which
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
# #80 also RE-TUNED the block-list itself, by replaying ALL 687 of gk's real
# main-agent Bash commands from 2026-07-26 through this hook and reading
# every block. Three false-positive classes were found that way and are now
# guarded by tests: an output REDUCER after a pipe (`gh pr merge ... 2>&1 |
# tail -2` — only a statement's FIRST pipe stage is classified now), a
# BOUNDED peek (`head -5 /tmp/out`, `tail -3 SKILL.md`, `sed -n '250,260p'`
# — judge the SIZE that comes back, not the head token; the bound is
# AIRULESET_PEEK_MAX_LINES, default 50), and an ASSERTION (`grep -c`,
# `grep -q` return one number / nothing). Block rate on that corpus went
# 18.6% -> 14.1%, and what remains is genuinely main reading source files
# and logs into its own context — exactly what should be dispatched.
#
# The ticket's direction 1 (">N gh calls per TURN → batch them") was
# REFUTED by the same measurement and deliberately NOT built: 687 of 687
# main turns carried exactly ONE Bash call, so a per-turn counter could
# never fire. Batching pressure lives in the nudge's message instead.
#
# #178 (user decision, 2026-07-31, option 1): the classifier above judges
# an operation by its CLASS (bulk read/search/build/test vs. coordination),
# but a genuinely SMALL, bounded operation is cheap regardless of class —
# and the user's standing directive is that small bounded operations run
# on MAIN regardless of model; only genuinely large sweeps and repo
# implementation stay blocked. Production evidence, all same day: five
# false blocks in one session — a `cat` of a 20-line config file, a
# 7-pattern `grep` sweep over `tests/`, and two ~1KB scratchpad writes —
# each one a bounded, harmless read/write that had no business being
# gated. This also matches Anthropic's own Opus 5 prompting guidance,
# which advises against delegating small, cheap operations to a subagent
# when the calling agent can just do them directly — dispatch overhead is
# for genuinely bulk or unbounded work, not for reading one small file.
#
# Two additions, both size-based rather than class-based:
#   1. Edit/Write to a `/tmp/` scratchpad path, or a path matching
#      `~/.claude/projects/*/memory/*` (a memory note), is coordinator
#      BOOKKEEPING — exempt from AIRULESET_FABLE_EDIT_MAX, up to a SIZE CAP
#      (below), regardless of which arming condition holds. A repo file is
#      NOT exempt and keeps the existing threshold.
#   2. In the Bash classifier, `cat`/`grep`/`rg`/`ag` targeting ONLY
#      explicit, EXISTING, non-glob file(s) whose combined size is under
#      AIRULESET_MAIN_READ_MAX_BYTES (default 131072) are NOT blocked —
#      the size that comes back is what determines context cost, not the
#      command name. A glob, a nonexistent path, an oversize file, a
#      recursive/`-f`/`-d` form, or any ONE bad token in a multi-file read
#      still blocks exactly as before — this is deliberately what keeps
#      every pre-existing fake-filename test fixture blocked unchanged.
#
# FIXED (fresh-context adversarial review of the #178 diff, same day): three
# real holes in the first cut, all closed here.
#   a) PATH TRAVERSAL defeated bullet 1 entirely — a `file_path` of
#      `/tmp/../home/.../PWNED.py` string-matches `/tmp/*` and a 5000-char
#      Write exited 0 straight into the repo tree (reproduced live). Any
#      `file_path` containing `..` now gets NO bookkeeping exemption at
#      all — it falls through to the ordinary AIRULESET_FABLE_EDIT_MAX
#      threshold, fail-closed. A legitimate scratchpad/memory path never
#      contains `..`, so nothing real is lost.
#   b) UNBOUNDED bookkeeping writes let a main session stage an arbitrarily
#      large implementation to `/tmp` and `cp` it into the repo (`cp` is
#      ambiguous -> allow in the Bash classifier, and this fix deliberately
#      does NOT add `cp` gating — that is a materially different, more
#      invasive change than the ticket asked for, and would false-block
#      routine copies). So bullet 1's exemption is now SIZE-CAPPED at
#      AIRULESET_MAIN_READ_MAX_BYTES too (same env as bullet 2, default
#      131072 — ~100x the production false blocks this was built for,
#      ~1 KB scratchpad notes) — a non-numeric length gets NO exemption,
#      fail-closed. The residual — staging up to that cap in `/tmp` then
#      `cp`-ing it in — is accepted, bounded by the same 128 KB cap.
#   c) AGGREGATE-SIZE bypass in bullet 2 — N `cat`/`grep` segments chained
#      with `;`/`&&`, each just under the per-file cap, summed to 1.2 MB in
#      one command (10 x 120000-byte `cat`s, reproduced live). The
#      exemption now draws from ONE shared per-command budget
#      (`READ_BUDGET`, seeded at AIRULESET_MAIN_READ_MAX_BYTES and consumed,
#      never refunded, by every segment that draws from it) — the WHOLE
#      command's aggregate small-file exemption is bounded to the same cap,
#      not each segment independently.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")

# #835: extract a payload field via jq, with a FORK-FREE bash fallback used ONLY
# when the jq SPAWN transiently FAILS. Under fleet-wide fork pressure (the whole
# test suite + concurrent worker lanes + the supervisor, all one uid) an
# `echo "$INPUT" | jq` can fail to fork and exit non-zero; the old
# `|| echo "<default>"` then SILENTLY mis-scoped every per-session marker/pending
# to the literal default ("unknown"), so a valid one-shot bypass marker went
# UN-honored — the OneShotBypass80 `-n auto` flake (both routing shapes proven:
# a session_id / tool_name / command extraction failure each strand the marker).
# A bash `[[ =~ ]]` cannot itself fail to fork, so on a jq failure the field is
# re-derived structurally. jq stays PRIMARY (its result is used byte-for-byte
# whenever it succeeds — ZERO behaviour change on the normal path), and the regex
# fallback is correct for CC's well-formed hook payload, degrading to the SAME
# default jq would have on a genuinely-absent field. Residual: an exotic payload
# whose value contains the field pattern before the real field, or a command
# whose JSON escapes the fallback leaves un-decoded (the pattern-matching
# consumers tolerate it — it runs only on a jq failure).
_AI_SID_RE='"session_id"[[:space:]]*:[[:space:]]*"([^"]*)"'
_AI_TOOL_RE='"tool_name"[[:space:]]*:[[:space:]]*"([^"]*)"'
_AI_AGENT_RE='"agent_id"[[:space:]]*:[[:space:]]*"([^"]*)"'
_AI_CMD_RE='"command"[[:space:]]*:[[:space:]]*"((\\.|[^"\\])*)"'
_ai_field() {   # $1 = jq filter, $2 = default, $3 = fallback ERE (capture grp 1)
    local _out
    if _out=$(printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null); then
        printf '%s' "$_out"                     # jq succeeded — use it verbatim
    elif [[ "$INPUT" =~ $3 ]]; then
        printf '%s' "${BASH_REMATCH[1]}"        # jq spawn failed — fork-free fallback
    else
        printf '%s' "$2"
    fi
}

AGENT_ID=$(_ai_field '.agent_id // empty' '' "$_AI_AGENT_RE")
[ -z "$AGENT_ID" ] || exit 0            # subagent — execution belongs there

TOOL_NAME=$(_ai_field '.tool_name // empty' '' "$_AI_TOOL_RE")

RAW_SID=$(_ai_field '.session_id // "unknown"' 'unknown' "$_AI_SID_RE")
RAW_SID=$(printf '%s' "$RAW_SID" | tr -cd 'A-Za-z0-9_-')
RUN_FILE="/tmp/airuleset-main-bash-run-${RAW_SID:-unknown}"

# #492: per-USER audit/bypass log paths. A FIXED /tmp name is owned by the
# FIRST user to create it on a shared box (subdev: montalu2-8, david, marek,
# simap, miva1); every OTHER user's `>>` append then fails EACCES, and (see
# the brace-group at each write site) that error LEAKS to stderr as a
# `PreToolUse hook error` on every block. The ${EUID} suffix gives each user
# its own file, which still ACCUMULATES across that user's sessions — what
# these "did it fire, on what" logs want; a per-SESSION suffix would fragment
# them. ${EUID} is a bash builtin, always set; id -u is the fallback for a
# non-bash re-exec. Same class as odoo-erp #115 (the shared upload-log). The
# per-uid name is still predictable in sticky /tmp, so a hostile local user
# could pre-create it unwritable — but that only silences a victim's own
# telemetry (the brace-group below keeps it leak-free either way), never a
# concern on these trusted dev boxes and no worse than the old fixed name.
# #732: the block/bypass logs are the ONLY cross-SESSION-shared artifacts this
# hook writes — everything else (bypass markers, run counter, presence marker)
# is SID-keyed and thus unique per session. During the airuleset push gate the
# fail-closed test suite runs on the LIVE dev box, where concurrent worker lanes
# + the supervisor session (all the SAME uid) genuinely arm/consume bypass
# markers, appending to these SAME per-uid logs mid-suite — so a test that
# counts WHOLE-FILE log lines miscounts (the v0.1.88 gate "2 != 1" false
# push-block, 2026-08-26). AIRULESET_MAIN_EXEC_LOG_DIR lets a test redirect BOTH
# logs into an isolated dir it owns (the per-uid suffix is preserved), so no
# concurrent real fleet session — which never sets this var — can touch the file
# the test reads. FAIL-SAFE: unset / empty / not-a-directory / not-WRITABLE /
# root-`/` (its trailing slash strips to the empty string) ALL fall back to the
# current /tmp path BYTE-FOR-BYTE, so real (non-test) invocations are unchanged
# and a bad override never silently sends the audit trail to an unwritable dir.
# An `if` condition's failure never trips `set -e`; the `:-` handles `set -u`.
_EXEC_LOG_DIR="/tmp"
_EXEC_LOG_OVR="${AIRULESET_MAIN_EXEC_LOG_DIR:-}"
_EXEC_LOG_OVR="${_EXEC_LOG_OVR%/}"          # strip a trailing slash ("/" -> "")
if [ -n "$_EXEC_LOG_OVR" ] && [ -d "$_EXEC_LOG_OVR" ] \
   && [ -w "$_EXEC_LOG_OVR" ]; then
    _EXEC_LOG_DIR="$_EXEC_LOG_OVR"
fi
BLOCK_LOG="${_EXEC_LOG_DIR}/airuleset-main-exec-block-${EUID:-$(id -u)}.log"
BYPASS_LOG="${_EXEC_LOG_DIR}/airuleset-main-exec-bypass-${EUID:-$(id -u)}.log"

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
    BASH_CMD=$(_ai_field '.tool_input.command // empty' '' "$_AI_CMD_RE")  # #835 fork-free fallback
    [ -n "$BASH_CMD" ] || exit 0

    # #174: manual pane revival must deliver only 'continue' -- never text
    # captured from the SAME pane. UNCONDITIONAL (not gated by Fable/goal-
    # armed/away, no bypass marker): a live incident (2026-07-29, see
    # .claude/rules-reference/internals-archive.md) showed the main session read a
    # STALLED pane's stale input-box draft off its screen and typed it back
    # as if it were a real pending prompt -- `continue` was never sent at
    # all. This is a CORRECTNESS/SAFETY concern (what gets typed into
    # another session), not a cost-control one, so it applies regardless of
    # this session's own Fable/goal/away state, and there is no legitimate
    # reason to bypass it (the sanctioned way to deliver an answer to
    # another session is notify/deliver_discord_replies, never an ad-hoc
    # manual send-keys of something just read). This catches the narrow,
    # MECHANICALLY-SAFE slice of the mistake: a command that CAPTURES a
    # pane's content (`tmux capture-pane`/`display-message`, via a shell
    # variable or an inline command substitution) and then feeds that SAME
    # value into a `tmux send-keys` payload, in ONE command line -- zero
    # false positives on a deliberately hand-composed literal, since that
    # never involves a capture call at all. It cannot see the CROSS-turn
    # shape (read the pane in one tool call, compose the send-keys command
    # from memory in a LATER one) -- there is no mechanical fix for that;
    # the rule file is what covers it.
    SENDKEYS_VERDICT=$(python3 - "$BASH_CMD" 2>/dev/null <<'PYEOF'
import re
import shlex
import sys

cmd = sys.argv[1]


def top_segments(text):
    # good enough to find every assignment/command in a compound line --
    # the same boundary set this hook already treats as command edges
    # elsewhere (classify_bash's own segment split).
    return re.split(r'&&|\|\||;|\n', text)


CAPTURE_RE = re.compile(
    r'\$\(\s*tmux\s+(?:capture-pane|display-message)\b[^)]*\)')

captured_vars = set()      # names of vars assigned FROM a pane-capture call
inline_hit = False         # a capture call sits directly inside a send-keys segment

for seg in top_segments(cmd):
    seg = seg.strip()
    m = re.match(r'^(\w+)=(.*)$', seg)
    if m and CAPTURE_RE.search(m.group(2)):
        captured_vars.add(m.group(1))
    if CAPTURE_RE.search(seg) and re.search(r'\bsend-keys\b', seg):
        inline_hit = True

if inline_hit:
    print("UNSAFE:inline-capture-into-send-keys")
    sys.exit(0)

for seg in top_segments(cmd):
    try:
        tk = shlex.split(seg)
    except ValueError:
        continue
    if "tmux" not in tk or "send-keys" not in tk:
        continue
    for var in captured_vars:
        if ("$" + var) in seg or ("${" + var + "}") in seg:
            print("UNSAFE:%s" % var)
            sys.exit(0)
print("SAFE")
PYEOF
) || SENDKEYS_VERDICT=""
    case "$SENDKEYS_VERDICT" in
        UNSAFE:*)
            echo "BLOCKED: this MAIN session's command captures a pane's content (tmux capture-pane/display-message) and feeds it straight back into \`tmux send-keys\` -- the exact \"read the screen, type it back as a real prompt\" mistake #174 is about. Manual pane revival may ONLY ever deliver the literal 'continue' -- a genuinely deliberate, hand-composed literal is fine (this only blocks a value that came FROM a pane capture in this SAME command). See .claude/rules-reference/internals-archive.md." >&2
            exit 2
            ;;
    esac

    # #80: ARMING the escape hatch is never blocked and never counted —
    # otherwise the per-dispatch cap below could sit in front of the only
    # documented way out of it, which would be exactly the dead end the
    # ticket forbids. Deliberately narrow: the command must START with
    # `touch` and name one of the two marker paths.
    # #128: arming now means WRITING A REASON into the marker, so the
    # redirect forms (`echo "<reason>" > …`, `printf … > …`, `cat > … <<EOF`)
    # are exempt alongside the historical `touch`. The arm's own command
    # line carries the reason, so logging its first 120 chars makes the
    # audit readable from the arm side too, not only from the consume.
    NORM_CMD=$(printf '%s' "$BASH_CMD" | tr -s ' \t' ' ' | sed 's/^ //')
    case "$NORM_CMD" in
        touch\ *airuleset-main-exec-ok-*|touch\ *airuleset-fable-exec-ok-*|\
        echo\ *airuleset-main-exec-ok-*|echo\ *airuleset-fable-exec-ok-*|\
        printf\ *airuleset-main-exec-ok-*|printf\ *airuleset-fable-exec-ok-*|\
        cat\ *airuleset-main-exec-ok-*|cat\ *airuleset-fable-exec-ok-*)
            # #819: consumption is DEFERRED to PostToolUse via a pending flag
            # (see the marker block below). A sibling-blocked call leaves a
            # STALE pending behind; a re-echo (this arming command) then RUNS,
            # so its OWN PostToolUse would consume the freshly-armed marker
            # unless we clear that stale pending here first. The arming echo
            # never reaches the marker block, so it never sets a pending of
            # its own — clearing here only removes a stranded one.
            rm -f "/tmp/airuleset-main-exec-pending-${RAW_SID:-unknown}" \
                2>/dev/null || true
            ARM_SNIP=$(printf '%s' "$NORM_CMD" | jq -Rr '.[0:120]' 2>/dev/null \
                || echo "")
            { echo "$(date -Is) main-exec bypass-arm session=$RAW_SID cmd=$ARM_SNIP" \
                >> "$BYPASS_LOG"; } 2>/dev/null || true
            exit 0
            ;;
    esac
else
    # #178: a /tmp scratchpad file or a ~/.claude/projects/*/memory/ note
    # is coordinator BOOKKEEPING, never implementation — exempt from the
    # AIRULESET_FABLE_EDIT_MAX threshold, checked BEFORE the ordinary
    # length gate below. The hook only STRING-MATCHES the path; no
    # filesystem check. Repo files keep the existing threshold unchanged.
    # #640: ~/.claude/work-products/** joins the SAME size-capped,
    # traversal-guarded branch — a DURABLE, sweep-safe home for a large
    # main-session WORK-PRODUCT (an unapproved client draft, a generated
    # document, a recipe). It is NOT under /tmp, so neither the fleet
    # subdev-disk-hygiene.sh nor airuleset's own sweep_claude_scratch (both
    # /tmp-scoped) deletes it. WHY the pattern is $HOME-ANCHORED
    # (`"$HOME"/.claude/work-products/*`), not a bare glob (#640 review):
    # this exemption must reach ONLY the ONE home-dir work-products dir, and
    # NEITHER of the two repo-subdir shapes that would reopen the #178
    # stage-implementation-into-the-tree hole — a bare `*/work-products/*`
    # would match `<repo>/work-products/x.py`, AND an unanchored
    # `*/.claude/work-products/*` would match `<repo>/.claude/work-products/
    # x.py` (every managed repo carries a project-local `.claude/`). Anchoring
    # to the literal $HOME (quoted → matched literally; the running user's own
    # home per box, so still home-agnostic across dev1/dev2/subdev) excludes
    # both: a repo lives under $HOME/devel/<repo>/, never at $HOME/.claude/.
    FILE_PATH_EARLY=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' \
        2>/dev/null || echo "")
    LEN=$(echo "$INPUT" | jq -r \
        '(.tool_input.new_string // .tool_input.content // "") | length' \
        2>/dev/null || echo 0)
    BOOKKEEPING_READ_MAX="${AIRULESET_MAIN_READ_MAX_BYTES:-131072}"
    case "$BOOKKEEPING_READ_MAX" in
        ''|*[!0-9]*) BOOKKEEPING_READ_MAX=131072 ;;
    esac
    case "$FILE_PATH_EARLY" in
        # fresh-context #178 review: path traversal (`..` anywhere) NEVER
        # gets the bookkeeping exemption — a `/tmp/../<repo>/x.py`-shaped
        # path string-matches `/tmp/*` but resolves outside it, so it falls
        # straight through to the ordinary threshold below, fail-closed.
        *..*) : ;;
        /tmp/*|*/.claude/projects/*/memory/*|"$HOME"/.claude/work-products/*)
            # size-capped, not unlimited (#178 review) — a non-numeric LEN
            # gets no exemption either, fail-closed to the ordinary check.
            [ "$LEN" -le "$BOOKKEEPING_READ_MAX" ] 2>/dev/null && exit 0
            ;;
    esac
    MAX="${AIRULESET_FABLE_EDIT_MAX:-800}"
    [ "$LEN" -gt "$MAX" ] 2>/dev/null || exit 0     # surgical edit — oversight
fi

SESSION_ID="${RAW_SID:-unknown}"

# #80: the marker is ONE-SHOT — honoring it CONSUMES it. "Deliberate
# exception" means one deliberate action, not "disable the guard for the
# rest of the session" (gk, 2026-07-26: one touch at 01:24 → 332 unguarded
# calls). Re-touching always works, so the escape hatch never dead-ends;
# the cost of abuse just grows with the abuse instead of being paid once.
#
# #819: the consumption POINT moved. This hook ALLOWING a call is NOT the
# same as the command RUNNING — a LATER sibling PreToolUse hook (block-local-
# poll-repeat #119, block-ci-poll-repeat, block-history-rewrite, …) can still
# exit 2 on the same call, so the command never runs. Consuming the marker
# here would then strand the one-shot on a call that never executed, forcing
# a needless re-echo (the #819 bug). So on the VALID-reason path this hook
# now DEFERS: it leaves the marker and writes a session-scoped pending flag
# (/tmp/airuleset-main-exec-pending-<sid>); the PostToolUse consumer
# (post-consume-main-exec-marker.sh) deletes marker + pending ONLY after the
# tool actually RAN (PostToolUse fires after a tool that ran — including a
# Bash command that exited non-zero — and NOT for a call a PreToolUse hook
# denied, exactly the wanted behaviour). The REFUSE paths (bad reason / jq
# failure) still delete the marker here in PreToolUse, since the tool is then
# blocked and no PostToolUse fires.
# Fail-safe: if in doubt whether the command ran, CONSUME — a marker that
# survives too long is the security regression; an extra re-echo is the mild
# cost. Residuals, all bounded to ONE extra call in the SAFE (over-survive-
# briefly) direction: a user Ctrl+C mid-Bash may skip PostToolUse; and whether
# PostToolUse fires for a tool CC treats as a hard ERROR (e.g. a failed Edit)
# is not independently verified here — if it does not, that edit's marker
# survives one call. Both self-heal on the next executed guarded call.
BYPASS_MARK=""
BYPASS_FILE=""
if [ -e "/tmp/airuleset-main-exec-ok-${SESSION_ID:-unknown}" ]; then
    BYPASS_MARK="main-exec-ok"
    BYPASS_FILE="/tmp/airuleset-main-exec-ok-${SESSION_ID:-unknown}"
elif [ -e "/tmp/airuleset-fable-exec-ok-${SESSION_ID:-unknown}" ]; then
    BYPASS_MARK="fable-exec-ok(legacy)"
    BYPASS_FILE="/tmp/airuleset-fable-exec-ok-${SESSION_ID:-unknown}"
fi
# #128: the marker must CARRY its reason. What the ticket read as abuse
# ("187 bypasses in one session") is a PRE-#80 artifact — 186 of those 193
# log lines predate 7bcbafe (2026-07-26T20:25:17), when one `touch`
# disabled the hook for the rest of the session; after it the same session
# armed the marker 4 times in 2.5 days, each paired 1:1 with a consume, and
# nothing automated arms it at all (watchdog job 22 only ever DELETES a
# stale one). So the marker is already exceptional in fact. What it was
# NOT is auditable: the arm line recorded no reason, and the six real arms
# had one — it lived in the tool description and an `echo`, never in the
# log. So a marker with no readable reason is REFUSED (and cleared, so a
# throwaway one cannot linger and cannot be re-consumed), and the reason
# reaches the log on both the arm and the consume. Legacy empty markers
# stop working; that is self-correcting, since the very next block message
# states the reason-carrying form.
BYPASS_MIN_REASON="${AIRULESET_MAIN_EXEC_REASON_MIN:-8}"
case "$BYPASS_MIN_REASON" in ''|*[!0-9]*) BYPASS_MIN_REASON=8 ;; esac
if [ -n "$BYPASS_MARK" ]; then
    # one log line per bypass: newlines and CRs flattened, remaining control
    # bytes DELETED (a NUL would make bash warn on stdout capture, and raw
    # C0 bytes in an append-only log are unreadable — the delete set spares
    # every byte >= 0x80, so a Slovak reason survives intact), length bounded
    # by a UTF-8-safe codepoint slice (never `cut -c`, which counts bytes).
    #
    # #180: the jq slice used to be the LAST stage of one long pipe collapsed
    # by `|| echo ""` — indistinguishable from a genuinely empty/short
    # reason. Both readings already fail CLOSED here (an unrecognised
    # reason REFUSES the bypass, so implementation stays blocked either
    # way), but a real jq hiccup was silently reported as "no reason,
    # cleared" — misleading whoever reads the log. Capture jq's OWN exit
    # status on its own line so the log can say which one actually
    # happened.
    # (adversarial review, batch-3 #172/#183/#180/#174): this pipe used to
    # be part of the SAME `|| echo ""` fallback as the jq slice below it —
    # splitting them apart for the #180 fix removed that safety net from
    # THIS half. Under `set -euo pipefail`, a read failure here (e.g. a
    # TOCTOU race where $BYPASS_FILE vanishes between the earlier `[ -e ]`
    # check and this read — confirmed reproducible: `tr ... < <missing
    # file>` aborts the WHOLE hook via set -e, with no block message and no
    # controlled exit code, before ever reaching the jq-failure handling
    # this fix exists to add) must fall back to empty, not crash the hook.
    BYPASS_CLEAN=$(tr '\n\r\t' '   ' < "$BYPASS_FILE" 2>/dev/null \
        | tr -d '\000-\010\013\014\016-\037\177' \
        | sed 's/  */ /g; s/^ //; s/ $//') || BYPASS_CLEAN=""
    BYPASS_JQ_RC=0
    BYPASS_REASON=$(printf '%s' "$BYPASS_CLEAN" | jq -Rrs '.[0:200]' 2>/dev/null) \
        || BYPASS_JQ_RC=$?
    BYPASS_REASON=$(printf '%s' "$BYPASS_REASON" | sed 's/^ *//; s/ *$//')
    # #819: the marker is NO LONGER deleted unconditionally here. The VALID-
    # reason path DEFERS to PostToolUse (writes a pending flag, keeps the
    # marker); only the two REFUSE paths clear the bad marker in PreToolUse.
    BYPASS_PENDING="/tmp/airuleset-main-exec-pending-${SESSION_ID:-unknown}"
    if [ "$BYPASS_JQ_RC" -ne 0 ]; then
        # refuse: clear the bad marker AND any stranded pending (a stale
        # pending must never phantom-consume the NEXT armed marker).
        rm -f "$BYPASS_FILE" "$BYPASS_PENDING" 2>/dev/null || true
        { echo "$(date -Is) main-exec bypass refused session=$SESSION_ID tool=$TOOL_NAME marker=$BYPASS_MARK (reason extraction FAILED jq_rc=$BYPASS_JQ_RC, cleared)" \
            >> "$BYPASS_LOG"; } 2>/dev/null || true
    elif [ "${#BYPASS_REASON}" -ge "$BYPASS_MIN_REASON" ]; then
        # DEFER consumption to PostToolUse: leave the marker, drop a pending
        # flag so post-consume-main-exec-marker.sh consumes it once the tool
        # RAN. The reason rides in the pending file for the consumer's log.
        #
        # #819 review (CRITICAL): if the pending WRITE fails (ENOSPC / inode
        # exhaustion — a real fleet condition, dev1 hit 714k inodes — or an
        # unwritable path), fall back to consuming the marker HERE, in
        # PreToolUse. Otherwise the call would be allowed with no pending, the
        # consumer would no-op, and the marker would survive EVERY subsequent
        # executed call — the pre-#80 session-wide kill switch resurrected
        # under disk pressure. `rm` needs no free space, so the fail-safe
        # direction (consume when in doubt) always holds.
        if { printf '%s\n' "$BYPASS_REASON" > "$BYPASS_PENDING"; } 2>/dev/null; then
            { echo "$(date -Is) main-exec bypass session=$SESSION_ID tool=$TOOL_NAME marker=$BYPASS_MARK (allowed, deferred consume pending post-exec) reason=$BYPASS_REASON" \
                >> "$BYPASS_LOG"; } 2>/dev/null || true
        else
            rm -f "$BYPASS_FILE" 2>/dev/null || true      # write failed → consume now (fail-safe)
            { echo "$(date -Is) main-exec bypass session=$SESSION_ID tool=$TOOL_NAME marker=$BYPASS_MARK (allowed, pending-write FAILED, consumed in PreToolUse) reason=$BYPASS_REASON" \
                >> "$BYPASS_LOG"; } 2>/dev/null || true
        fi
        exit 0
    else
        # refuse: clear the bad marker AND any stranded pending.
        rm -f "$BYPASS_FILE" "$BYPASS_PENDING" 2>/dev/null || true
        { echo "$(date -Is) main-exec bypass refused session=$SESSION_ID tool=$TOOL_NAME marker=$BYPASS_MARK (no reason, cleared)" \
            >> "$BYPASS_LOG"; } 2>/dev/null || true
    fi
fi

TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")
[ -n "$TRANSCRIPT" ] && [ -r "$TRANSCRIPT" ] || exit 0

# ---- condition 1: Fable-model main (#32, staleness fixed by #38) ----
# The transcript LAGS the live turn: at PreToolUse the CURRENT turn's
# assistant entry is not flushed yet, so the newest model in the file is the
# PREVIOUS turn's. Replaying the real 2026-07-25 incident prefix (session
# 2d02a127, lines 38913-38935) pins the window exactly — the /model switch
# marker landed at 38915, the first Opus assistant entry only at 38932, and
# the Write blocked at 38934 read `claude-fable-5` off the tail.
#
# So the tail's newest model is NOT authoritative on its own. Two corrections:
#   a) take the model from a PARSED top-level assistant entry (`.message.model`
#      on `.type == "assistant"`), never a raw grep — the same structural
#      discipline the goal-armed detector below already uses, and it keeps a
#      nested/quoted foreign transcript out of the decision by construction;
#   b) if CC's own `/model` stdout marker (`Set model to <X>`, a top-level
#      user/system string) appears AFTER that entry, THAT marker decides:
#      it names Fable -> Fable, anything else -> not Fable.
# Fail-open is unchanged in every unreadable/undecidable case.
#
# Residual, NOT fixable from the transcript: a session RESUMED onto a different
# model writes no marker at all (observed 2026-07-26, 19:04 in the same file),
# so its first turn still reads the pre-resume model. The hook payload cannot
# help — CC 2.1.220 builds it as {session_id, transcript_path, cwd, prompt_id,
# permission_mode, agent_id, agent_type, effort} with NO model field (the
# `model` object in the bundle's schema docs belongs to the STATUSLINE input,
# not to hooks), which is why #38's suggested payload-first fix is not
# available. The one-shot bypass marker covers that residual case.
MODEL=$(python3 - "$TRANSCRIPT" <<'PYEOF' 2>/dev/null || echo ""
import json
import re
import sys

TAIL = 2_000_000

try:
    with open(sys.argv[1], "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - TAIL))
        blob = fh.read()
except Exception:
    sys.exit(0)

lines = blob.split(b"\n")
if size > TAIL and lines:
    lines = lines[1:]          # drop the partial first line of the window

model = ""
for raw in lines:
    raw = raw.strip()
    if not raw:
        continue
    try:
        d = json.loads(raw)
    except Exception:
        continue
    if not isinstance(d, dict) or d.get("isSidechain"):
        continue
    t = d.get("type")
    if t == "assistant":
        msg = d.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("model"), str):
            model = msg["model"]
        continue
    # CC's own `/model` stdout, top-level string content only (a tool_result's
    # content is always an array, so a quoted foreign marker cannot reach here)
    if t == "user":
        c = (d.get("message") or {}).get("content")
    elif t == "system":
        c = d.get("content")
    else:
        continue
    if not isinstance(c, str) or "Set model to" not in c:
        continue
    picked = re.sub(r"\x1b\[[0-9;]*m", "", c).split("Set model to", 1)[1]
    model = "claude-fable-5" if re.search(r"fable", picked, re.I) else ""

print(model)
PYEOF
)
IS_FABLE=0
case "$MODEL" in
    claude-fable-*) IS_FABLE=1 ;;
esac

# ---- condition 3: the user is AWAY (#128) ----
# Conditions 1 and 2 are both PROXIES for "this session is running
# autonomously", and neither is the cost driver — every main turn re-sends
# the whole context whether or not a goal is armed. Measured on dev1 for
# 2026-07-28, top-level entries of all 11 real transcripts: the sessions
# this hook engages on ran 853 main tool calls against 87 dispatches, the
# sessions it was INERT on ran 1339 against 82 — and the worst session of
# the day (varos-eft5000: 650 main calls, ZERO dispatches, 52 Edit/Writes
# over the threshold) was one of the inert ones. So the engagement
# condition was missing the burn.
#
# The obvious repair — engage on EVERY main session — is refused on the
# same measurement. Replaying every real main-agent Bash command of that
# day through this hook, it newly blocks 348 calls, 164 of them within five
# minutes of a live human prompt: an attended session losing routine calls
# mid-conversation, which is a regression, not a fix (same lesson as
# stop-check-question-quality.sh's presence carve-out — hard-gating a live
# dialog printed hook errors into the user's chat, camera-box 2026-07-05).
# Gating on AWAY instead newly blocks 103 and touches ZERO attended calls,
# because the burn is concentrated in away time (190 of varos-eft5000's 497
# guardable calls are >15 min after any human prompt).
#
# AWAY is read from the presence marker clear-question-dedup.sh stamps on
# UserPromptSubmit — the same mechanism stop-check-question-quality.sh
# already uses, and precise where the transcript is not: a goal re-poke or
# hook feedback does NOT fire UserPromptSubmit, so an autonomous loop never
# looks present. Fail-open in every unprovable case: NO marker (never
# stamped, or /tmp cleared under a long-running session) means not provably
# away, so nothing new engages. AIRULESET_MAIN_GUARD_AWAY_S=0 disables this
# condition entirely; a garbage value falls back to the default.
#
# This condition is OR'd with the two below it and replaces neither: a
# Fable main and a goal-armed main stay engaged exactly as before, attended
# or not.
AWAY=0
AWAY_S="${AIRULESET_MAIN_GUARD_AWAY_S:-900}"
case "$AWAY_S" in ''|*[!0-9]*) AWAY_S=900 ;; esac
ACTIVE_MARK="/tmp/claude-user-active-${SESSION_ID:-unknown}"
if [ "$AWAY_S" -gt 0 ] && [ -f "$ACTIVE_MARK" ]; then
    ACTIVE_AT=$(stat -c %Y "$ACTIVE_MARK" 2>/dev/null || echo 0)
    case "$ACTIVE_AT" in ''|*[!0-9]*) ACTIVE_AT=0 ;; esac
    if [ "$ACTIVE_AT" -gt 0 ] \
       && [ "$(( $(date +%s) - ACTIVE_AT ))" -ge "$AWAY_S" ]; then
        AWAY=1
    fi
fi

# ---- condition 2: armed /goal main (#54) ----
# #180: the whole block/allow decision used to hinge on ONE jq call — a
# transient jq hiccup (fault-injected via a stub-on-PATH: 0 of 8 attempts
# reproduced it live, but a stub jq confirms the mechanism) made the
# combined `jq | grep | tail` pipe fail with `pipefail` set, collapsing
# through `|| echo ""` into GOAL_MARK="" -> GOAL_ARMED=0 -> the guard
# reads "not armed" and ALLOWS — the exact silent stop-guarding this
# ticket is about. The fix separates jq's OWN exit status (GOAL_JQ_RC)
# from the ROUTINE "no goal marker in this transcript" case (jq succeeds,
# grep legitimately finds nothing — `pipefail` reports THAT as non-zero
# too, so `$?` on the combined pipe can never distinguish them) by
# capturing jq's output on its own line first, only THEN piping it
# through grep/tail (which is allowed to find nothing).
GOAL_JQ_RC=0
GOAL_JQ_OUT=$(jq -r '
    if .type == "user" and (.message.content | type) == "string" then .message.content
    elif .type == "system" and (.content | type) == "string" then .content
    else empty end
' "$TRANSCRIPT" 2>/dev/null) || GOAL_JQ_RC=$?
GOAL_MARK=$(printf '%s\n' "$GOAL_JQ_OUT" \
    | grep -oE '<local-command-stdout>Goal (set|cleared):' | tail -1 || true)
GOAL_ARMED=0
GOAL_UNKNOWN=0
if [ "$GOAL_JQ_RC" -ne 0 ]; then
    # Could not decide -> fail CLOSED (block, and say why), never silently
    # allow. This is a NAMED distinct state (GOAL_UNKNOWN), not folded into
    # GOAL_ARMED, so the block message never claims a goal is armed when
    # the truth is "the transcript could not be read".
    GOAL_UNKNOWN=1
else
    case "$GOAL_MARK" in
        *"Goal set:") GOAL_ARMED=1 ;;
    esac
fi

if [ "$IS_FABLE" != "1" ] && [ "$GOAL_ARMED" != "1" ] \
   && [ "$AWAY" != "1" ] && [ "$GOAL_UNKNOWN" != "1" ]; then
    exit 0                               # no condition holds — allow
fi

# One tag per condition that HOLDS, joined by '+' — a block log line has to
# say which rule engaged, and now more than one can.
RULE_TAG=""
REASON=""
[ "$IS_FABLE" = "1" ] && { RULE_TAG="FABLE"; REASON="runs FABLE"; }
if [ "$GOAL_ARMED" = "1" ]; then
    RULE_TAG="${RULE_TAG:+$RULE_TAG+}GOAL_ARMED"
    REASON="${REASON:+$REASON and }has an ARMED /goal"
fi
if [ "$GOAL_UNKNOWN" = "1" ]; then
    RULE_TAG="${RULE_TAG:+$RULE_TAG+}GOAL_UNKNOWN"
    REASON="${REASON:+$REASON and }could not determine whether a /goal is armed (transcript read failed — failing closed)"
fi
if [ "$AWAY" = "1" ]; then
    RULE_TAG="${RULE_TAG:+$RULE_TAG+}USER_AWAY"
    REASON="${REASON:+$REASON and }is running unattended (user AWAY — no prompt for ${AWAY_S}s)"
fi
REASON="this MAIN session $REASON"

# ---- #73: log EVERY block (not just bypasses) — same style/location as the
# bypass log, so "did the hook ever fire, on what" is answerable from a log
# instead of an unmeasurable correlation (the exact gap #73 was filed for).
log_block() {
    # $1 = match/detail string (classifier match for Bash, len=N for Edit/Write)
    local detail snippet
    detail="$1"
    snippet=$(printf '%s' "$2" | jq -Rr '.[0:120]' 2>/dev/null \
        || printf '%s' "$2" | cut -c1-120)
    { printf '%s main-exec BLOCK session=%s tool=%s rule=%s match=%s cmd=%s\n' \
        "$(date -Is)" "$SESSION_ID" "$TOOL_NAME" "$RULE_TAG" "$detail" "$snippet" \
        >> "$BLOCK_LOG"; } 2>/dev/null || true
}

# ---- Bash path (#66): classify, don't size-gate ----
if [ "$TOOL_NAME" = "Bash" ]; then
    # #178: the payload's cwd is what makes a RELATIVE file argument
    # resolvable for the small-file read exemption below; empty when the
    # payload doesn't carry one (the classifier then refuses any relative
    # path rather than guessing).
    CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
    CLASS=$(python3 - "$BASH_CMD" "$CWD" <<'PYEOF'
import os
import re
import shlex
import sys

cmd = sys.argv[1]
cwd = sys.argv[2] if len(sys.argv) > 2 else ""

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


# ---- #88: splice out bash LINE CONTINUATIONS before segmentation. A
# backslash immediately followed by a newline joins the two physical lines
# into ONE logical line in bash (`curl ... |\` + newline + `  grep ...` is a
# single pipe, not two commands) -- but STATEMENTS_RE below splits on a bare
# "\n" with no notion of this, so the continued line (typically a pipe's
# REDUCER stage, e.g. the version-extracting `grep` after a health-check
# `curl`) becomes its own top-level statement and misclassifies as a bulk
# read. Reproduces both inside a `$( … )` command substitution (the real gk
# health-check that was blocked by mistake) and at plain top level -- the
# root cause is line-continuation handling, not `$( … )` specifically.
# Quote-aware (mirrors `strip_unquoted_comment`'s tracking): a
# backslash-newline INSIDE single quotes is two literal characters in bash,
# not a continuation, and must be left alone.
def join_line_continuations(text):
    out = []
    in_sq = in_dq = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_sq:
            if c == "'":
                in_sq = False
            out.append(c)
            i += 1
            continue
        if in_dq:
            if c == "\\" and i + 1 < n:
                out.append(c)
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_dq = False
            out.append(c)
            i += 1
            continue
        if c == "'":
            in_sq = True
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_dq = True
            out.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 2                # splice out the backslash + newline
            continue
        out.append(c)
        i += 1
    return "".join(out)


cmd = strip_heredocs(cmd)
cmd = join_line_continuations(cmd)

# Same rigor level as block-history-rewrite.sh: split on shell statement/pipe
# separators, quote-aware tokenization per segment. A segment that matches
# neither list is left ambiguous (never classified) — the caller treats an
# all-ambiguous command as ALLOW.
# #80: statements and PIPE STAGES are different things. A statement
# (`;`, `&&`, `||`, `&`, newline) starts a fresh command whose own first
# stage decides what gets read. A downstream PIPE stage reads STDIN — and is
# usually an output REDUCER (`... 2>&1 | tail -2`, `... | awk ... | uniq -c`,
# the two most common shapes in gk's whole transcript), which makes what the
# model sees SMALLER, never bigger. Classifying a reducer as a bulk read was
# a false positive on the loop's most-used idiom. So: split into statements,
# then classify only each statement's FIRST pipe stage. `cat file | grep x`
# still blocks — its first stage IS the bulk read.
STATEMENTS_RE = re.compile(r'&&|\|\||[;&]|\n')
SEGMENTS_RE = STATEMENTS_RE          # kept: same shape name used elsewhere

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


# #80: `head`/`tail` are inherently BOUNDED (10 lines by default) and are the
# loop's normal "did my write land / what did that command print" peek
# (`... > /tmp/mt.out; head -5 /tmp/mt.out`). What makes a read expensive is
# the SIZE that comes back, so judge the BOUND, not the head token. A peek up
# to PEEK_MAX lines passes; a bigger -n, an unbounded `-n +N` (line N to EOF)
# or a `-c` byte dump is still a bulk read. `cat`/`sed`/`awk`/`grep` unchanged.
try:
    PEEK_MAX = int(os.environ.get("AIRULESET_PEEK_MAX_LINES") or 50)
except ValueError:
    PEEK_MAX = 50


def is_bounded_peek(tk):
    n = None
    i = 1
    while i < len(tk):
        t = tk[i]
        if t == "-c" or t.startswith("--bytes"):
            return False                      # byte dump, not a line peek
        if t == "-n" and i + 1 < len(tk):
            n = tk[i + 1]
            i += 2
            continue
        if t.startswith("-n"):
            n = t[2:]
        elif re.match(r'^-\d+$', t):
            n = t[1:]
        elif t.startswith("--lines="):
            n = t.split("=", 1)[1]
        i += 1
    if n is None:
        return True                           # default 10 lines — bounded
    if n.startswith("+"):
        return False                          # `tail -n +N` runs to EOF
    try:
        return int(n) <= PEEK_MAX
    except ValueError:
        return False


# #80: `sed -n 'A,Bp'` is a line SLICE — bounded exactly like `head -N`, so
# judge its WIDTH. A slice up to PEEK_MAX lines is a peek; a wider one, a
# pattern range (`/re/,/re/p` — unbounded by construction), or any non-`-n`
# usage is not. `sed -i` edits a file and prints nothing — never a read.
_SED_RANGE_RE = re.compile(r'^(\d+),(\d+)p$')
_SED_SINGLE_RE = re.compile(r'^\d+p$')


def is_bounded_sed(tk):
    if any(t == "-i" or t.startswith("-i") for t in tk[1:]):
        return True                           # in-place edit, prints nothing
    if not any(t == "-n" for t in tk[1:]):
        return False
    for t in tk[1:]:
        if t.startswith("-"):
            continue
        m = _SED_RANGE_RE.match(t)
        if m:
            return (int(m.group(2)) - int(m.group(1)) + 1) <= PEEK_MAX
        if _SED_SINGLE_RE.match(t):
            return True
    return False


# #178: a genuinely small, EXPLICIT, EXISTING file read is a bounded read —
# size is what determines context cost, not the command name. A glob is
# never provably bounded (the shell could expand it to anything), a
# relative path with no known cwd cannot be resolved safely, and a
# nonexistent/unstatable/oversize path is refused — which is exactly what
# keeps every pre-existing fake-filename fixture in this repo's own test
# suite BLOCKED unchanged.
try:
    READ_MAX_BYTES = int(os.environ.get("AIRULESET_MAIN_READ_MAX_BYTES") or 131072)
except ValueError:
    READ_MAX_BYTES = 131072

# fresh-context #178 review: N small-file reads chained in ONE command
# (`;`/`&&`), each individually under READ_MAX_BYTES, must not sum to
# something huge (reproduced live: 10 x 120000-byte `cat`s, 1.2 MB total,
# all ten independently under the per-segment cap). A SHARED per-command
# budget, seeded once per classify() invocation (i.e. once per Bash tool
# call) and CONSUMED — never refunded — by every segment that draws from
# it, bounds the WHOLE command's aggregate small-file exemption to
# READ_MAX_BYTES, not each segment independently.
READ_BUDGET = [READ_MAX_BYTES]


def small_files(paths, base_cwd):
    total = 0
    for p in paths:
        if any(c in p for c in "*?["):
            return False                      # a glob is never provably bounded
        expanded = os.path.expanduser(p)
        if not os.path.isabs(expanded):
            if not base_cwd:
                return False                   # relative + no known cwd -> refuse
            expanded = os.path.join(base_cwd, expanded)
        try:
            if not os.path.isfile(expanded):
                return False
            total += os.path.getsize(expanded)
        except OSError:
            return False
    if total > READ_BUDGET[0]:
        return False
    READ_BUDGET[0] -= total
    return True


def cat_files(tk):
    # #178: skip only the LEADING run of flag-shaped tokens (`cat -n f`) —
    # everything from the first non-flag token onward is a file argument.
    i = 1
    while i < len(tk) and tk[i].startswith("-"):
        i += 1
    return tk[i:]


def is_blocked_segment(tk):
    if not tk:
        return False
    if redirects_stdout_to_file(tk):
        return False
    head = tk[0]
    if head in ("grep", "rg", "ag"):
        # `-c` (count) / `-q` (quiet) return one number / nothing — an
        # ASSERTION ("did my write land"), not a read. Everything else
        # (notably `grep -rn`) really does dump matches.
        #
        # #128: real commands write the COMBINED short-flag form
        # (`grep -cE '^(FAILED|ERROR)' /tmp/full10.log`, `grep -rc`), which
        # a whole-token comparison never recognised. A short-flag CLUSTER
        # (`-[A-Za-z]+`, so `-C3`/`--color` are excluded) containing `c` or
        # `q` is the same assertion — case matters, since `-C` is CONTEXT
        # and dumps matches with surrounding lines.
        if any(
            t in ("-c", "-q", "--count", "--quiet", "--silent")
            or (re.match(r"^-[A-Za-z]+$", t)
                and ("c" in t[1:] or "q" in t[1:]))
            for t in tk[1:]):
            return False
        # #178: a recursive / directory / pattern-from-file form is never
        # a small explicit-file read — keep it blocked outright rather
        # than letting it fall through to the file-size exemption below.
        # `--dereference-recursive` (review finding) is the long alias of
        # `-R` — spelled out explicitly rather than surviving only by
        # accident via the downstream isfile() refusal on a directory arg.
        if any(
            t in ("-r", "-R", "--recursive", "--dereference-recursive",
                  "-f", "-d")
            or (re.match(r"^-[A-Za-z]+$", t)
                and ("r" in t[1:] or "R" in t[1:]))
            for t in tk[1:]):
            return True
        # #178: otherwise the FIRST non-flag token is the pattern and the
        # rest are files — unless `-e` supplies the pattern separately (its
        # argument is indistinguishable from a positional token here), in
        # which case every non-flag token is treated as a file. A small,
        # explicit, EXISTING file read is a bounded read, not a bulk one.
        non_flags = [t for t in tk[1:] if not t.startswith("-")]
        has_e = any(t == "-e" or t.startswith("--regexp") for t in tk[1:])
        files = non_flags if has_e else non_flags[1:]
        if files and small_files(files, cwd):
            return False
        return True
    if head == "find":
        return True
    if head in ("head", "tail"):
        return not is_bounded_peek(tk)
    if head == "sed":
        return not is_bounded_sed(tk)
    if head == "awk":
        return True
    if head == "cat":
        # #178: a small, explicit, EXISTING file read is bounded — judge
        # the SIZE that comes back, not the command name.
        files = cat_files(tk)
        if files and small_files(files, cwd):
            return False
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


def first_pipe_stage(statement):
    # `|` inside quotes is not a pipe — tokenize, then cut at the first bare
    # `|` token (shlex keeps an unquoted `|` as its own token only when it is
    # space-separated, so also split a glued `a|b` conservatively).
    parts = statement.split("|")
    if len(parts) == 1:
        return statement
    head = parts[0]
    # an unbalanced quote in the head means the `|` was quoted — keep it all
    if head.count("'") % 2 or head.count('"') % 2:
        return statement
    return head


def classify(text):
    for statement in STATEMENTS_RE.split(text):
        seg = first_pipe_stage(statement)
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

Deliberate exception (one-shot — consumed when the command actually RUNS, not
when this hook allows it; logged, and it must SAY WHY):
  echo "<why this one call must run here>" > /tmp/airuleset-main-exec-ok-${SESSION_ID}
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

Deliberate exception (one-shot — consumed when the command actually RUNS, not
when this hook allows it; logged, and it must SAY WHY):
  echo "<why this one call must run here>" > /tmp/airuleset-main-exec-ok-${SESSION_ID}
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
  • a /tmp scratchpad file, a ~/.claude/projects/*/memory/ note, or a
    durable WORK-PRODUCT under ~/.claude/work-products/ (a draft, a
    generated document, a recipe — #640) is exempt from this threshold up
    to AIRULESET_MAIN_READ_MAX_BYTES (default 128 KB, #178) — this file is
    none of those, is over that cap, or its path contains ".." (never
    exempt, #178 review).

Deliberate exception (one-shot — consumed when the command actually RUNS, not
when this hook allows it; logged, and it must SAY WHY):
  echo "<why this one call must run here>" > /tmp/airuleset-main-exec-ok-${SESSION_ID}
MSG
exit 2
