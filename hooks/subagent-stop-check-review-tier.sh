#!/usr/bin/env bash
set -euo pipefail

# Hook: SubagentStop — #876 review-tier consistency gate. A worktree-mode
# autopilot-worker's LANE-RETURN (or full-flow merged return) MUST carry a
# `reviewed-by-tier:` line naming the tier that produced the review
# (claude-fable-5 | claude-opus-4-6), consistent with the gate state and the
# dispatch observable in its own transcript.
#
# Stage 1 (message only): a COMPLETED return with no `reviewed-by-tier:` line
#   => BLOCK. Skips blocked/incomplete returns (ISOLATION FAILED, UNVERIFIED,
#   question markers) and obsolete/dropped issues.
#
# Stage 2 (transcript consistency): walks the transcript for `fable-gate` calls
#   and `fable-advisor` Agent dispatches. Table:
#   - OPEN + claude-fable-5 line + fable-advisor dispatch => pass
#   - claude-fable-5 line with NO fable-advisor dispatch => BLOCK (fabricated)
#   - OPEN + claude-opus-4-6 without trivial-diff => BLOCK (downtier)
#   - CLOSED + claude-opus-4-6 => pass
#   - trivial-diff declaration, no gate call => pass
#   - unreadable transcript + line present => pass (fail-open) + log
#   - fable dispatch at CLOSED => pass + log (over-spend, not a lie)
#
# Once per (session, repo#issue) — non-wedging, like lane-return/design gates.
# Fail-safe exit 0 on ambiguity.

command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0

_field() { printf '%s' "$INPUT" | jq -r "$1" 2>/dev/null || echo ""; }

AGENT_TYPE=$(_field '.agent_type // empty')
[ "$AGENT_TYPE" = "autopilot-worker" ] || exit 0

SID=$(_field '.session_id // empty')
CWD=$(_field '.cwd // empty')
MSG=$(_field '.last_assistant_message // empty')
[ -n "$SID" ] || exit 0
[ -n "$CWD" ] || exit 0
[ -n "$MSG" ] || exit 0

TRANSCRIPT=$(_field '.agent_transcript_path // .transcript_path // empty')

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"

# Stage 1 + 2 in one python call via ARGV (no heredoc-stdin trap).
RESULT=$(python3 - "$REPO_ROOT" "$CWD" "$MSG" "$TRANSCRIPT" <<'PYEOF' 2>/dev/null || true
import json
import os
import re
import sys

sys.path.insert(0, sys.argv[1])
try:
    import design_gate as dg
    import notify
except Exception:
    sys.exit(0)

cwd, msg, transcript_path = sys.argv[2], sys.argv[3], sys.argv[4]

# --- STAGE 1: message-only gate ---

# Skip incomplete/blocked returns.
# The evidence block's own `unverified: none` line must NOT trip this skip.
# Match a REAL UNVERIFIED declaration: uppercase, followed by a non-"none" reason.
_SKIP_RE = re.compile(
    r"ISOLATION FAILED"
    r"|^\s*UNVERIFIED\s*:\s*(?!none\s*$)"
    r"|❓\s*(?:NEEDS YOU|ASKED)",
    re.M)
if _SKIP_RE.search(msg):
    sys.exit(0)

# Detect a COMPLETED return — either a worktree branch claim or a merged claim.
ev = notify.parse_worker_evidence(msg)
_BRANCH_LINE_RE = re.compile(r"^\s*branch\s*:(.*)$", re.I | re.M)
has_worktree_branch = any(
    re.search(r"worktree-(?:agent|issue)-\w+|worktree-\S+", m.group(1))
    for m in _BRANCH_LINE_RE.finditer(msg))
is_completed = has_worktree_branch or ev["merged"]
if not is_completed:
    sys.exit(0)

# Extract the reviewed-by-tier line.
_TIER_LINE_RE = re.compile(
    r"^\s*reviewed-by-tier\s*:\s*(.*)$", re.I | re.M)
tier_match = _TIER_LINE_RE.search(msg)

if not tier_match:
    # Stage 1 BLOCK: completed return with no reviewed-by-tier line.
    # Collect issue refs for once-per state.
    issues = []
    seen = set()
    for line_re in (re.compile(r"^\s*issues\s*:(.*)$", re.I | re.M),
                    re.compile(r"^\s*issue_state\s*:(.*)$", re.I | re.M)):
        for m in line_re.finditer(msg):
            for n in dg.issue_refs(m.group(1)):
                if n not in seen:
                    seen.add(n)
                    issues.append(n)
        if issues:
            break
    if not issues:
        sys.exit(0)

    repo = notify.resolve_repo_key(cwd, msg=msg)
    if not repo:
        sys.exit(0)

    # Exclude obsolete/dropped issues.
    _EXCLUDE_RE = re.compile(
        r"^\s*(?:obsolete_(?:closed|handed_off)|dropped)\s*:(.*)$",
        re.I | re.M)
    excluded = set()
    for m in _EXCLUDE_RE.finditer(msg):
        excluded.update(dg.issue_refs(m.group(1)))

    active = [n for n in issues if n not in excluded]
    if not active:
        sys.exit(0)

    # Print repo + issues for bash once-per guard.
    print("BLOCK_MISSING")
    print(repo)
    for n in active:
        print(n)
    sys.exit(0)

# --- STAGE 2: transcript consistency ---
tier_text = tier_match.group(1).strip()
parts = tier_text.split()
tier_val = parts[0] if parts else ""
has_trivial = "trivial-diff" in tier_text.lower()

# Validate tier value.
try:
    from airuleset import REVIEWED_BY_TIER_VALUES
    valid_tiers = REVIEWED_BY_TIER_VALUES
except Exception:
    valid_tiers = {"claude-fable-5", "claude-opus-4-6"}

if tier_val not in valid_tiers:
    # Invalid tier value — pass (fail-open, the handoff CLI validates).
    sys.exit(0)

# Read transcript.
gate_open = None  # None = no gate call found
has_fable_dispatch = False

if not transcript_path or not os.path.isfile(transcript_path):
    # Unreadable transcript + line present => pass + log.
    print("PASS_NO_TRANSCRIPT")
    sys.exit(0)

try:
    with open(transcript_path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            msg_content = rec.get("message", {}).get("content", [])
            if not isinstance(msg_content, list):
                continue

            for block in msg_content:
                if not isinstance(block, dict):
                    continue

                # Check tool_use blocks.
                if block.get("type") == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})

                    # Detect fable-gate Bash command.
                    if name == "Bash":
                        cmd = inp.get("command", "")
                        if "fable-gate" in cmd:
                            # Will be resolved by the paired tool_result.
                            pass

                    # Detect fable-advisor Agent dispatch.
                    if name == "Agent":
                        st = inp.get("subagent_type", "")
                        if st == "fable-advisor":
                            has_fable_dispatch = True

                # Check tool_result blocks for fable-gate output.
                if block.get("type") == "tool_result":
                    content_val = block.get("content", "")
                    if isinstance(content_val, list):
                        content_val = " ".join(
                            c.get("text", "") for c in content_val
                            if isinstance(c, dict))
                    if isinstance(content_val, str) and "fable-gate" in str(block.get("tool_use_id", "")):
                        # Heuristic — check the content.
                        pass

            # Also check for fable-gate results in tool_result messages.
            if rec.get("type") == "user":
                for block in msg_content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        text = ""
                        c = block.get("content", "")
                        if isinstance(c, str):
                            text = c
                        elif isinstance(c, list):
                            text = " ".join(
                                x.get("text", "") for x in c
                                if isinstance(x, dict))
                        if "OPEN" in text and "fable" in text.lower():
                            gate_open = True
                        elif "CLOSED" in text and "fable" in text.lower():
                            if gate_open is None:
                                gate_open = False

except Exception:
    # Unreadable transcript — pass (fail-open).
    print("PASS_NO_TRANSCRIPT")
    sys.exit(0)

# Also scan for fable-gate in assistant Bash commands and their results.
# Re-scan more simply: look for fable-gate command + OPEN/CLOSED in results.
try:
    with open(transcript_path, "r", errors="replace") as f:
        content = f.read()
    if "fable-gate" in content:
        # Look for OPEN or CLOSED tokens near fable-gate context.
        if re.search(r"OPEN\b", content) and gate_open is None:
            gate_open = True
        if gate_open is None and re.search(r"CLOSED\b", content):
            gate_open = False
except Exception:
    pass

# Consistency table:
if tier_val == "claude-fable-5":
    if not has_fable_dispatch:
        # Fable tier claimed but no fable-advisor dispatch — BLOCK.
        print("BLOCK_NO_DISPATCH")
        sys.exit(0)
    # Fable tier + dispatch = consistent, pass.
    sys.exit(0)

if tier_val == "claude-opus-4-6":
    if has_trivial:
        # Trivial-diff declaration — pass regardless of gate state.
        sys.exit(0)
    if gate_open is True:
        # Gate OPEN + opus tier without trivial-diff — BLOCK (downtier).
        print("BLOCK_DOWNTIER")
        sys.exit(0)
    if gate_open is False:
        # Gate CLOSED + opus — pass.
        sys.exit(0)
    if gate_open is None:
        # No gate call observed + opus without trivial — pass (fail-open).
        # Could be a resumed lane whose gate call was in the dead transcript.
        print("PASS_NO_GATE")
        sys.exit(0)

# Anything else — pass (fail-open).
sys.exit(0)
PYEOF
)
[ -n "$RESULT" ] || exit 0

VERDICT=$(printf '%s\n' "$RESULT" | sed -n '1p')

case "$VERDICT" in
    PASS_NO_TRANSCRIPT)
        # Log the fail-open decision.
        printf '[review-tier] line present, transcript unreadable — pass (fail-open)\n' >&2
        exit 0
        ;;
    PASS_NO_GATE)
        # Log: no gate call observed, opus without trivial — pass (fail-open).
        printf '[review-tier] opus tier, no gate call in transcript — pass (fail-open, possible resumed lane)\n' >&2
        exit 0
        ;;
    BLOCK_NO_DISPATCH)
        REASON="reviewed-by-tier claims claude-fable-5 but NO fable-advisor Agent dispatch
found in the transcript (#876). A claude-fable-5 review tier requires a real
fable-advisor dispatch — an in-context self-review cannot claim the Fable tier.

Fix: run fable-gate, dispatch fable-advisor (OPEN) or the model-less Opus consult
(CLOSED), paste the verdict into the Self-review table, then add/correct the
reviewed-by-tier line and repost LANE-RETURN."
        jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
        exit 0
        ;;
    BLOCK_DOWNTIER)
        REASON="reviewed-by-tier claims claude-opus-4-6 but fable-gate was OPEN in the
transcript (#876). A non-trivial diff with gate OPEN must dispatch fable-advisor
and record reviewed-by-tier: claude-fable-5 — or declare trivial-diff explicitly.

Fix: dispatch fable-advisor for the review (gate is OPEN), paste the verdict,
update reviewed-by-tier to claude-fable-5 gate:OPEN, and repost LANE-RETURN.
(If the diff is genuinely trivial, add the trivial-diff marker.)"
        jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
        exit 0
        ;;
    BLOCK_MISSING)
        # Stage 1: no reviewed-by-tier line at all.
        REPO=$(printf '%s\n' "$RESULT" | sed -n '2p')
        ITEMS=$(printf '%s\n' "$RESULT" | sed -n '3,$p')
        [ -n "$REPO" ] && [ -n "$ITEMS" ] || exit 0

        # Once per (session, repo#issue).
        STATE="/tmp/airuleset-reviewtier-$(printf '%s' "$SID" | tr -cd 'A-Za-z0-9._-')"
        SEEN=$(cat "$STATE" 2>/dev/null || echo "")
        FRESH=""
        NEW_SEEN="$SEEN"
        while IFS= read -r n; do
            [ -n "$n" ] || continue
            case " $SEEN " in *" ${REPO}#${n} "*) continue ;; esac
            FRESH="${FRESH}${n}
"
            NEW_SEEN="${NEW_SEEN}${NEW_SEEN:+ }${REPO}#${n}"
        done <<EOF
$ITEMS
EOF
        [ -n "$FRESH" ] || exit 0
        printf '%s' "$NEW_SEEN" > "$STATE" 2>/dev/null || true

        LINES=""
        for n in $FRESH; do
            LINES="${LINES}  #${n}
"
        done

        REASON="Completed return has NO reviewed-by-tier line (#876). Every worktree-mode
and full-flow return MUST carry:

  reviewed-by-tier: claude-fable-5|claude-opus-4-6 [trivial-diff] gate:<OPEN|CLOSED|n/a>

Missing for:
${LINES}
Fix: run fable-gate, dispatch fable-advisor (OPEN) or the model-less Opus consult
(CLOSED), paste the verdict into the Self-review table, then add the
reviewed-by-tier line and repost LANE-RETURN.

You are blocked once per issue; if the review genuinely cannot be dispatched,
report that and stop."
        jq -n --arg r "$REASON" '{"decision":"block","reason":$r}'
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
