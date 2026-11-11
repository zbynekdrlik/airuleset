#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# Enforces no-dropped-work.md: any work IDENTIFIED but not completed this session
# MUST be recorded (a `- [ ]` line on its ticket, or its own linked ticket when it is
# a separate deliverable, #1297 -> cite #N)
# before stopping. Unlike the completion-report ghost-deferral check (which only fires on
# completion reports), this fires on EVERY message — the user's three loss patterns
# happen mid-session, not in the final report:
#   1. Decomposition-shedding — high-level prompt split, some parts silently dropped.
#   2. Review findings acknowledged but neither fixed nor recorded.
#   3. "Pre-existing / known / unrelated / out-of-scope" dismissals during testing.
#   4. Drafting an issue backlog and ASKING permission instead of recording it.
# HARD-blocks via {"decision":"block"} with a per-session retry cap (avoids loops
# when a mention is genuinely meta / unfileable).

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

# --- #1053 verify-on-copy Stop gate (exit-2 + stderr, the deny-stderr contract) ---
# A turn ending ✅ DONE / ⏳ WORKING with a FRESH ~/.claude/verify-on-copy/
# status.json (written by the footer refresh) carrying overdue tickets — a
# verify-on-copy hand-back older than 24 h with NO Verified-on-copy: comment — is
# blocked: the gatekeeper deployed and returned the ticket for verification on a
# fresh PROD copy, and the stream must run REFRESH-DEV-BOX-FROM-PROD, verify, and
# post the evidence. Fail-OPEN when the status file is absent/stale (a dead
# writer). Dry-runnable per #963
# (reads only $MSG + the status file, no side effects).
if echo "$MSG" | grep -qE '✅ DONE|⏳ WORKING'; then
    VOC_DIR="${HOME}/.claude/verify-on-copy"
    VOC_RETRY="/tmp/airuleset-verify-on-copy-block-${SESSION_ID}"
    VOC_RETRIES=$(cat "$VOC_RETRY" 2>/dev/null || echo 0)
    VOC_MAX=3
    # python ALWAYS exits 0 (prints the reason lines when it should block, empty
    # otherwise) so `set -euo pipefail` never aborts on a non-zero substitution
    # (#979); `|| true` is a second belt. Aggregates the FRESH overdue set across
    # EVERY per-cwd status file (#1053 review 🟡 — a single global file was
    # clobbered across repos on a multi-repo account, silently defeating the
    # gate); a stale/absent dir fails OPEN.
    VOC_REASON=$(python3 - "$VOC_DIR" <<'PY' || true
import glob
import json
import os
import sys
import time

STALE = 3 * 3600
now = time.time()
seen = {}
try:
    files = sorted(glob.glob(os.path.join(sys.argv[1], "*.json")))
except OSError:
    sys.exit(0)                     # unreadable dir → fail open
for path in files:
    try:
        with open(path, encoding="utf-8") as h:
            st = json.load(h)
    except (OSError, ValueError):
        continue
    if not isinstance(st, dict):
        continue
    ts = st.get("ts")
    if not isinstance(ts, (int, float)) or (now - ts) > STALE:
        continue                    # dead writer / stale repo → skip
    for r in (st.get("overdue") or []):
        if isinstance(r, dict) and r.get("number") is not None:
            seen[r["number"]] = r
if not seen:
    sys.exit(0)
overdue = [seen[n] for n in sorted(seen)]
items = ["#%s %s (%sh)" % (r.get("number"), (r.get("title") or "")[:40],
                           r.get("age_h")) for r in overdue[:8]]
print("verify-on-copy > 24 h bez Verified-on-copy: (%d): %s"
      % (len(overdue), ", ".join(items)))
sys.exit(0)
PY
)
    if [ -n "$VOC_REASON" ] && [ "$VOC_RETRIES" -lt "$VOC_MAX" ]; then
        echo "$((VOC_RETRIES + 1))" > "$VOC_RETRY"
        {
            echo "🚫 BLOCKED (verify-on-copy #1053): gk nasadil zmenu — over ju na čerstvej kópii PROD."
            echo "$VOC_REASON"
            echo "Akcia: REFRESH-DEV-BOX-FROM-PROD: <stream>, over feature na kópii, "
            echo "potom komentár: Verified-on-copy: refresh <id> at <ISO-UTC> — <čo si overil>."
        } >&2
        exit 2
    fi
    # clean (no overdue obligation) → reset the retry counter for next time
    [ -z "$VOC_REASON" ] && rm -f "$VOC_RETRY"
fi

RETRY_FILE="/tmp/airuleset-untracked-work-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3

# --- Escape: proof the work was FILED as a tracked issue, or is being FIXED now ---
# A bare PR-title "#N" must NOT escape (it doesn't prove the dismissed work was filed).
# Require a filing verb adjacent to a #N, an explicit "issue #N", a gh issue create
# command, or an issues/N URL. Plus a narrow fix-now allowance.
ESCAPE=0
if echo "$MSG" | grep -qiE "(filed|filing|file it|tracked|tracking|tracker|logged|opened|created|recorded)[^.]{0,40}#[0-9]+|- \[ \][^.]{0,40}#[0-9]+|gh issue create|/issues/[0-9]+|issue\s+#[0-9]+|todo[: ]+#[0-9]+|tracked (in|as|under)\s+#[0-9]+|address(ed)?\s+(in|by|via)\s+#[0-9]+|(fixing|fix) (it|this|that)?\s*(now|immediately|in this (pr|commit|session))|let me fix|i'?ll fix (it|this|that)( now)?|fixing it now|\b(fixed|resolved|patched|corrected|repaired)\b"; then
    ESCAPE=1
fi

VIOLATION=""

# --- Group 1: dismissal of a discovered problem ---
# pre-existing / known / unrelated / out-of-scope used to justify NOT acting.
if [ "$ESCAPE" = "0" ] && echo "$MSG" | grep -qiE "pre.?existing|already (broken|failing|present|there) before|known (issue|bug|problem|failure|limitation|defect)|not (related|relevant) to (my|this|the) (change|pr|fix|work|task|commit)|unrelated to (my|this|the) (change|pr|fix|work|task|commit|edit)|(this|that|it) (is|was|'s) (a )?(separate|different|pre.?existing) (issue|problem|bug|failure)|\b(separate|different) (issue|problem|bug|concern|failure)\b|out of scope (for|of|here)|outside (the |this )?scope"; then
    VIOLATION="dismissal"
fi

# --- Group 2: leftover sub-work from a decomposed request, dropped without filing ---
if [ -z "$VIOLATION" ] && [ "$ESCAPE" = "0" ] && echo "$MSG" | grep -qiE "remaining (parts|items|tasks|work|pieces|sub.?tasks|features|requests|steps|asks)|the (other|rest of the) (parts|items|tasks|requests|features|asks|pieces)|(other|remaining) (parts|items|pieces) of (your|the) (request|prompt|ask)|the rest (can|will|should|could) (wait|follow|come later|be (done|handled|addressed|tackled|implemented) later)|(handled|did|implemented|finished|completed|covered) (only|just) (part|some|one|a (few|couple)) of (what|your|the)"; then
    VIOLATION="leftover"
fi

# --- Group 3: drafting an issue backlog but ASKING permission to create it ---
# A drafted backlog that was never recorded on the ticket is dropped work. Uses a filing-ONLY
# escape (not the fix-inclusive global ESCAPE): a "fixed X" verb must not excuse a
# never-filed backlog.
ISSUE_FILED=0
if echo "$MSG" | grep -qiE "gh issue create|/issues/[0-9]+|\b(filed|created|opened|logged|recorded)\b[^.]{0,40}#[0-9]+|filed as #[0-9]+|- \[ \][^.]{0,40}#[0-9]+"; then
    ISSUE_FILED=1
fi
if [ -z "$VIOLATION" ] && [ "$ISSUE_FILED" = "0" ] \
    && echo "$MSG" | grep -qiE "\b(create|creating|file|filing|open|opening|add|adding)\b[^.]{0,30}\b(issue|issues|backlog|ticket|tickets)\b|\b(issue|issues|backlog|ticket|tickets)\b[^.]{0,30}\b(create|file|open|add)\b" \
    && echo "$MSG" | grep -qiE "give (me )?the word|say the word|just say|tell me to (hold|go|create|proceed)|or hold\b|hold off|ready to (create|file|open|add|proceed|go)|should i (create|file|open|add|proceed|go ahead)|want me to (create|file|open|add)|shall i (create|file|open|add)|once you (confirm|give|say|approve)|when you (confirm|give|say|approve)|let me know (if|when|whether|to)|approve (the )?(creation|backlog|issues|tickets)|your (go|word|approval|call) (to|before|on)|awaiting your (go|word|approval|confirmation)|no merge without your"; then
    VIOLATION="ask_to_file"
fi

if [ -n "$VIOLATION" ] && [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    echo "$((RETRIES+1))" > "$RETRY_FILE"
    case "$VIOLATION" in
        dismissal)
            REASON="You dismissed a discovered problem as 'pre-existing' / 'known issue' / 'unrelated to this change' / 'out of scope' WITHOUT either fixing it now or filing it as its own ticket. Per no-dropped-work.md, a problem you NOTICE for the first time is one you just DISCOVERED — 'pre-existing' describes its age, not its tracking status. A failing test / warning / breakage you spot during testing is a bug you must capture, or it stays unsolved forever and the user rediscovers it every session.\n\nFix NOW (one of):\n  1. Fix it in this session (then say so explicitly), OR\n  2. file it as its OWN ticket (a defect outside the change in hand, #1297): gh issue create with '<exact error/test name/warning, where it happens, why it matters>', 'Part of #<current>' and 'Scope-gate: separate-work', add '- [ ] #<new>' to the current ticket's description — then cite it ('Filed as #N').\n\n'pre-existing / known / unrelated / out of scope' is allowed ONLY with the #N next to it. See no-dropped-work.md (failure mode 3)." ;;
        leftover)
            REASON="Your message indicates leftover sub-work from the user's request ('remaining parts', 'the rest can wait', 'handled only part of...') being dropped WITHOUT being recorded. Per no-dropped-work.md (#1297), EVERY part you do not complete this session MUST be recorded BEFORE you stop — otherwise the user has to re-explain work they already asked for.\n\nFix NOW: a step of the same deliverable becomes a '- [ ] <part>' line in the ticket's description (gh issue edit <N> --body-file ...), then say 'Recorded on #N: <parts>'; a separate deliverable gets its own ticket linked both ways ('Part of #N' + '- [ ] #<new>'), then 'Filed as #M'. Never one issue per plan step. There is no 'do it later' fate — only do-now or record-now. See no-dropped-work.md (failure mode 1)." ;;
        ask_to_file)
            REASON="You drafted/proposed GitHub issues but asked the user for PERMISSION to create them ('give the word and I'll create the issues', 'ready to create the backlog?', 'should I file these or hold?', 'once you confirm I'll file them'). no-dropped-work.md: identified work is recorded in the same turn; a drafted backlog you only asked about is DROPPED work, and the user then has to re-prompt.\n\nFix NOW, without asking: steps of one deliverable become '- [ ]' lines in its ticket's description (gh issue edit) -> 'Recorded on #N'; each separate deliverable is filed as its own linked ticket ('Filed as #N: <title>', #1297) — never one ticket per plan step. If a step's DESIGN is ambiguous, record it anyway and note the open question on the ticket. See no-dropped-work.md 'Prepared != filed' and ask-before-assuming.md (issue-creation row)." ;;
    esac
    jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
    exit 0
fi

# No violation (or retry budget exhausted) — let Stop succeed; clear counter on clean stop.
[ -z "$VIOLATION" ] && rm -f "$RETRY_FILE"
exit 0
