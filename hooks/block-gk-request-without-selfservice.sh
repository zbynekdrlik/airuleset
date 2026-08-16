#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — issue #516 ("Subdev prod-READ úlohy pre gk").
#
# The owner's repeated, angry complaint: a sub-dev stream keeps filing the
# GATEKEEPER action requests for things it can answer ITSELF from its own
# fresh PROD copy (a prod-STATE READ — a group membership, a row count, a
# config value, sent-mail content), the gatekeeper works them, and gk is
# overloaded by work the sub-dev could have done with zero gk involvement.
# The live incident: odoo-erp #3316, mail-flow diagnostics filed to gk though
# fully readable from the sub-dev's own `REFRESH-DEV-BOX-FROM-PROD` psql copy.
#
# The prose rule that should have caught this (autonomous-verification.md's
# "What's on PROD? is a SELF-SERVICE question" doctrine) EXISTS and REPEATEDLY
# FAILS — it is just one more sentence a pressured model skips. So this hook
# does the ONLY thing a hook CAN do, the EXACT shape block-ungated-issue-
# filing.sh (#137 Scope-gate, #329 Dedup-checked) already proved for filing:
# it forces a LOGGED, FALSIFIABLE claim line before the escalation goes
# through, instead of letting the silent default (just file it to gk) win. It
# does NOT and CANNOT verify the claim is true — it converts a silent gk
# escalation into an affirmative, on-the-record claim that self-service was
# tried and a genuine LIVE PROD intervention is what remains. That is the
# honest limit, stated here rather than hidden (same as #137).
#
# GATE: a gk ACTION request — `airuleset.py gk-request`, OR a raw `gh` command
# that adds the `needs-gatekeeper` label, OR a comment/issue whose body carries
# a `GATEKEEPER-ACTION:` line — is allowed only when the request BODY (the
# --comment/--body/--body-file text, or the heredoc) contains a line
#   Self-service-checked: <what I tried myself (RO channel / fresh prod copy)
#                          and what LIVE PROD intervention I need from gk>
#
# SCOPE — two deliberate exclusions, so the gate hits ONLY the reported case:
#  1. ONLY a REDUCED-authority sub-dev stream account is gated
#     (`airuleset.resolve_authority(cwd) != "full"`, imported directly from the
#     sibling airuleset.py — the SAME single-source-of-truth #390's stream-
#     routing gate uses). A full-authority box (the maintainer / gatekeeper,
#     which ADDS `needs-gatekeeper` on pickup and files its own test requests)
#     has no sub-dev "self-service prod copy" premise and is never gated —
#     degrade-to-allow on ANY authority-resolution failure, never a false
#     block (same bias as #390).
#  2. An ordinary CODE-REVIEW hand-off is NEVER touched: a request that ALSO
#     carries `ready-for-review` / a `stream:<x>` label, or whose body opens a
#     line with `READY-FOR-REVIEW:`, is the carve-out review hand-off (rule 8
#     of the cross-stream protocol: `needs-gatekeeper` + `stream:<user>` =
#     REVIEW queue, told apart from a bare action request by the `stream:`
#     label) and is skipped. `airuleset.py gk-request` never carries either, so
#     it is always an action request.
#
# BODY RESOLUTION mirrors block-ungated-issue-filing.sh (heredoc capture +
# --body/--body-file/-F). An UNRESOLVED body BLOCKS conservatively (get this
# wrong toward strict, per no-dropped-work.md's own bias: a false block costs
# one line, a false pass costs the exact gk overload this hook exists to stop).
#
# Every PASS and BLOCK is logged to ~/.claude/selfservice-gate.log.
#
# Bypass (rare, logged): `# airuleset:selfservice-ok <reason>` anywhere in the
# command — for the genuine edge where the escalation legitimately carries no
# body (e.g. a follow-up label tweak on an already-explained request).

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$CMD" ] && exit 0

# Cheap pre-filter: only classify commands that could plausibly be a gk action
# request at all.
case "$CMD" in
    *"gk-request"*) ;;
    *"needs-gatekeeper"*) ;;
    *"GATEKEEPER-ACTION"*) ;;
    *) exit 0 ;;
esac

# Deliberate bypass for a genuine edge.
case "$CMD" in *"airuleset:selfservice-ok"*) exit 0 ;; esac

LOG="$HOME/.claude/selfservice-gate.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

# This hook's own checkout root (the airuleset repo containing this script's
# sibling airuleset.py), resolved the same way block-ungated-issue-filing.sh
# resolves REPO_ROOT_DIR — passed into python so the authority gate can import
# airuleset.py directly (single source of truth for resolve_authority). Empty
# on any resolution failure -> the gate degrades to "cannot verify authority",
# which SKIPS (never blocks — a box where we cannot even resolve authority is
# not provably a reduced sub-dev stream).
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || HOOK_DIR=""
REPO_ROOT_DIR=""
[ -n "$HOOK_DIR" ] && REPO_ROOT_DIR="$(dirname "$HOOK_DIR")"

RC=0
OUT=$(python3 - "$CMD" "$SID" "$(pwd)" "$REPO_ROOT_DIR" <<'PYEOF' 2>/dev/null
import os
import re
import shlex
import sys

cmd = sys.argv[1]
sid = sys.argv[2]
cwd = sys.argv[3]
repo_dir = sys.argv[4] if len(sys.argv) > 4 else ""

# --- authority gate: engage ONLY for a reduced sub-dev stream account. A
# full-authority box (maintainer/gatekeeper) or an unresolvable authority is
# NOT gated (degrade-to-allow, #390's bias). Import airuleset.py directly (the
# same single-source-of-truth resolve_authority() `airuleset.py authority`
# itself calls); ANY failure -> skip the gate entirely.
def _reduced_authority():
    try:
        if repo_dir and repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)
        import airuleset as _ar
        profile = _ar.resolve_authority(cwd)
        return profile is not None and profile != "full"
    except Exception:
        return None       # unresolvable -> caller skips


reduced = _reduced_authority()
if not reduced:
    # full-authority, or authority unresolvable -> not gated.
    sys.exit(0)

# The falsifiable claim marker. Matched ANYWHERE in the body (not just at a
# line start) so an inline `--comment "... Self-service-checked: ..."` counts
# exactly like an own-line marker in a multi-line --body-file; case-insensitive
# to avoid a false block on a lower-cased marker. A non-whitespace char must
# follow the colon, so a bare empty `Self-service-checked:` never passes.
SELFSERVICE_RE = re.compile(r'Self-service-checked:\s*\S', re.IGNORECASE)
GK_ACTION_RE = re.compile(r'(?m)^\s*GATEKEEPER-ACTION:')
READY_REVIEW_RE = re.compile(r'(?m)^\s*READY-FOR-REVIEW:')
STREAM_LABEL_RE = re.compile(r'^stream:[A-Za-z0-9_-]+$', re.I)

HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\s*$")
CATFILE_RE = re.compile(r'^\s*cat\s*>>?\s*([^\s<>&;|]+)')

lines = cmd.split("\n")
n = len(lines)

# ---- pass 1: capture heredoc bodies (same shape as block-ungated-issue-
# filing.sh), and blank the body span out of the skeleton used for segmenting.
file_bodies = {}
direct_bodies = {}
skeleton_lines = list(lines)
i = 0
while i < n:
    line = lines[i]
    mm = HEREDOC_RE.search(line.rstrip())
    if not mm:
        i += 1
        continue
    delim = mm.group(2)
    strip_leading = "<<-" in line
    body = []
    j = i + 1
    while j < n:
        check = lines[j].lstrip("\t") if strip_leading else lines[j]
        if check == delim:
            break
        body.append(lines[j])
        j += 1
    body_text = "\n".join(body)
    fm = CATFILE_RE.match(line)
    if fm:
        file_bodies[fm.group(1)] = body_text
    else:
        direct_bodies[delim] = body_text
    for k in range(i + 1, min(j + 1, n)):
        skeleton_lines[k] = ""
    i = j + 1

skeleton = "\n".join(skeleton_lines)

ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")


def split_top_level(text):
    """Quote-aware split on &&/||/;/&/|/newline (a `;`/`|` inside a real
    issue TITLE/body must never be a command separator)."""
    segs, buf, i, n, quote = [], [], 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(text[i + 1])
            i += 2
            continue
        if text[i:i + 2] in ("&&", "||"):
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "&", "|", "\n"):
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return segs


def tokens_of(segment):
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def strip_prefix(tk):
    idx = 0
    while idx < len(tk):
        t = tk[idx]
        if t in ("sudo", "env") or t in LOOP_BODY_KEYWORDS or ASSIGN_RE.match(t):
            idx += 1
            continue
        break
    return tk[idx:]


def flag_value(tk, names):
    for idx, t in enumerate(tk):
        for name in names:
            if t == name and idx + 1 < len(tk):
                return tk[idx + 1]
            if t.startswith(name + "="):
                return t[len(name) + 1:]
    return None


def all_flag_values(tk, names):
    """Every value given via any of `names` (repeatable `-l`/`--add-label`),
    comma-list aware — so `-l a,b` and `-l a -l b` both yield [a, b]."""
    out = []
    for idx, t in enumerate(tk):
        val = None
        for name in names:
            if t == name and idx + 1 < len(tk):
                val = tk[idx + 1]
            elif t.startswith(name + "="):
                val = t[len(name) + 1:]
        if val is None:
            continue
        for piece in val.split(","):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def resolve_body(tk, seg_line):
    """Body text for this segment: --body-file/-F (heredoc or disk),
    --body/--comment inline. None when nothing resolvable."""
    bf = flag_value(tk, ("-F", "--body-file"))
    if bf is not None:
        if bf == "-":
            m = HEREDOC_RE.search(seg_line.rstrip())
            if m and m.group(2) in direct_bodies:
                return direct_bodies[m.group(2)]
            return None
        if bf in file_bodies:
            return file_bodies[bf]
        path = bf if os.path.isabs(bf) else os.path.join(cwd, bf)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None
    inline = flag_value(tk, ("--body", "--comment"))
    if inline is not None:
        return inline
    return None


def is_gk_request(tk):
    """`airuleset(.py) gk-request` — the primary CLI channel."""
    if "gk-request" not in tk:
        return False
    return any(t == "airuleset" or t.endswith("airuleset.py") for t in tk)


def is_gh_issue_cmd(tk):
    return len(tk) >= 3 and tk[0] == "gh" and tk[1] == "issue" \
        and tk[2] in ("create", "edit", "comment")


results = []   # (verdict, kind, reason)

for seg in split_top_level(skeleton):
    if not seg.strip():
        continue
    tk = strip_prefix(tokens_of(seg))
    if not tk:
        continue

    gkreq = is_gk_request(tk)
    gh_issue = is_gh_issue_cmd(tk)
    if not (gkreq or gh_issue):
        continue

    body = resolve_body(tk, seg)
    labels = all_flag_values(tk, ("-l", "--label", "--add-label"))
    label_set = set(labels)

    # --- is THIS segment a gk ACTION request that must carry the line?
    if gkreq:
        kind = "gk-request"
        action_request = True
        review_handoff = False    # gk-request never does a review hand-off
    else:
        adds_needs_gk = "needs-gatekeeper" in label_set
        body_gk_action = bool(body and GK_ACTION_RE.search(body))
        action_request = adds_needs_gk or body_gk_action
        if not action_request:
            continue
        # rule-8 discriminator: a CODE-REVIEW hand-off (never touched).
        adds_ready = "ready-for-review" in label_set
        adds_stream = any(STREAM_LABEL_RE.match(x) for x in label_set)
        body_ready = bool(body and READY_REVIEW_RE.search(body))
        review_handoff = adds_ready or adds_stream or body_ready
        kind = "gh-needs-gatekeeper"

    if review_handoff:
        continue                  # review hand-off — never gated (rule 8)

    # --- the falsifiable claim: a Self-service-checked line in the body.
    if body and SELFSERVICE_RE.search(body):
        results.append(("PASS", kind, "self-service-line-present"))
    elif body is None:
        results.append(("BLOCK", kind, "no-body (escalation carries no "
                        "Self-service-checked claim)"))
    else:
        results.append(("BLOCK", kind, "missing Self-service-checked line"))

if not results:
    sys.exit(0)

has_block = any(r[0] == "BLOCK" for r in results)
for verdict, kind, reason in results:
    log_verdict = "NOTFILED" if (has_block and verdict == "PASS") else verdict
    print("%s\t%s\t%s\t%s" % (log_verdict, kind, reason, sid))

sys.exit(2 if has_block else 0)
PYEOF
) || RC=$?

SUMMARY=""
if [ -n "$OUT" ]; then
    while IFS=$'\t' read -r VERDICT KIND REASON LOGSID; do
        [ -z "$VERDICT" ] && continue
        echo "$(date -Iseconds)  verdict=$VERDICT  kind=$KIND  session=$LOGSID  reason=\"$REASON\"" >> "$LOG" 2>/dev/null || true
        if [ "$VERDICT" = "BLOCK" ]; then
            SUMMARY="${SUMMARY}  - ${KIND}: ${REASON}
"
        fi
    done <<< "$OUT"
fi

if [ "$RC" -eq 2 ]; then
    if [ -n "$SUMMARY" ]; then
        printf '🚫 BLOCKED — gk action request without a Self-service-checked line:\n%s\n' "$SUMMARY" >&2
    fi
    cat >&2 <<'MSG'
A prod-STATE READ (a group membership, a row count, a config value, sent-mail
content) is a SELF-SERVICE question — NOT a gatekeeper action. Before asking the
gatekeeper to act, you MUST first try the self-service prod-read paths yourself,
then state on the request what you tried and what LIVE PROD intervention (if any)
genuinely remains for gk:

  Self-service-checked: tried <RO handover channel has_group/search_read | a
    fresh `REFRESH-DEV-BOX-FROM-PROD: <stream>` copy with full psql> — <result>;
    the LIVE PROD intervention I still need from gk is <restart the stuck
    outgoing queue | install <pkg> in RUNTIME_DEPS | ...>.

If it turns out you need NOTHING live from gk (a pure read), do NOT file it —
read it yourself from your fresh PROD copy. See modules/core/autonomous-
verification.md's "What's on PROD? is a SELF-SERVICE question" doctrine
(decision tree: 1. the stream's direct read-only channel, 2. REFRESH-DEV-BOX-
FROM-PROD, 3. a gk hand-off only for a genuine LIVE intervention).

This is a LOGGED, falsifiable claim (~/.claude/selfservice-gate.log) — it does
not verify the claim is true, only that you affirmatively made it instead of
silently escalating a self-serviceable read to the gatekeeper. A genuine
intervention request passes by truthfully filling the line. Genuine bypass:
append `# airuleset:selfservice-ok <reason>` to the command.
MSG
    exit 2
fi

exit 0
