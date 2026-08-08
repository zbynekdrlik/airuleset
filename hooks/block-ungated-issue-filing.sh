#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — issue #137 ("File-vs-fix asymmetry").
#
# stop-check-untracked-work.sh (Stop, every message) HARD-blocks a message that
# dismisses discovered work without filing it — filing has teeth. The opposite
# direction — complete-planned-work.md's Follow-up gate, "a <100-LoC cleanup in
# a file your PR already touches gets FIXED NOW in this PR, not filed" — has
# none: it is prose only. Measured leak (21-day investigation, #137): 7 of a
# 25-issue real sample violate the gate WITH THE VIOLATION CONFESSED in the
# issue body itself (camera-box #846, #843; odoo-erp #2388 all say so in
# plain text). A hook cannot mechanically verify "is this fix genuinely
# <100 LoC" — the fix doesn't exist yet when the issue is filed — so this
# hook does the only thing a hook CAN do: force an explicit, LOGGED,
# falsifiable claim instead of letting the silent default (just file it) win
# by default. It converts a silent leak into either an in-PR fix or an
# affirmative claim on the record; it does NOT and CANNOT verify the claim is
# true. That is the honest limit, stated here rather than hidden.
#
# GATE: `gh issue create` / `gh api .../issues` (POST) is allowed only when
# the issue BODY contains a line `Scope-gate: <criterion>` naming one of the
# bundling gate's own established exemptions:
#   >300-loc | schema-migration | api-break | security-boundary |
#   cross-cutting | needs-user-decision
# plus the two legitimate NON-discovery filing modes:
#   planned-work   — converged-plan decomposition (durable-decisions-to-tickets.md)
#   user-request   — the user explicitly asked for this ticket
#
# CHAIN-DEPTH CAP (#311, added after odoo-erp's #3035→#3220→#3224→#3250→
# #3251→#3252→#3258 seven-ticket review-finding chain — each hop honestly
# satisfied its OWN Scope-gate criterion, which is exactly why a per-issue
# criterion alone cannot stop this): a new issue naming its own PARENT as a
# "follow-up" (`#N follow-up`, the exact phrasing real chain members already
# use) is ALSO BLOCKED when that PARENT is itself such a follow-up — a
# depth-2 review-finding chain — regardless of whether a Scope-gate
# criterion is present. Detected via a cheap text match plus ONE bounded
# `gh issue view` call on the named parent; a lookup failure degrades to
# "cannot verify chain depth" and never blocks on its own.
#
# BODY RESOLUTION (same shape as block-gh-invalid-json-flag.sh's heredoc
# handling, extended to CAPTURE the body instead of discarding it):
#   - `-F <file>` / `--body-file <file>` where <file> was just written by a
#     `cat > <file> <<'EOF' ... EOF` earlier in the SAME command — the
#     heredoc body IS the issue body (the standard gh-cli-recipes.md pattern).
#   - `-F -` / `--body-file -` attached directly to the gh invocation's own
#     heredoc (`gh issue create -F - <<'EOF' ... EOF`).
#   - `-F <file>` referencing a file NOT written in this command — read it
#     from disk (a pre-existing body file). Unreadable -> unresolved.
#   - `--body "<literal>"` / `--body=<literal>` inline text.
#   - Nothing resolvable -> unresolved. An unresolved body BLOCKS
#     conservatively (get this wrong toward strict, per no-dropped-work.md:
#     a false block costs one line, a false pass costs nothing).
#
# Every PASS and BLOCK is logged to ~/.claude/scope-gate.log (repo, title,
# criterion, session) — the log is what keeps a false criterion auditable.
#
# Bypass (rare, logged): `# airuleset:scope-gate-ok <reason>` anywhere in the
# command text.
#
# KNOWN LIMIT: cannot verify the criterion is TRUE, only that it was
# affirmatively claimed. See modules/quality/no-dropped-work.md — filing
# must never become impossible; this hook's job is to stop the SILENT
# default, not to add a second judgment layer on top of the model's own.

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$CMD" ] && exit 0

# Cheap pre-filter: only classify commands that could plausibly contain a
# gh issue-creation call at all.
case "$CMD" in
    *"issue create"*) ;;
    *"gh api"*"issues"*) ;;
    *) exit 0 ;;
esac

# Deliberate bypass for a genuine edge.
case "$CMD" in *"airuleset:scope-gate-ok"*) exit 0 ;; esac

LOG="$HOME/.claude/scope-gate.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

# python3 - "$CMD" <<'PYEOF' (argv, never a pipe into the heredoc's own
# stdin — see the repo's own #96 gotcha).
RC=0
OUT=$(python3 - "$CMD" "$SID" "$(pwd)" <<'PYEOF' 2>/dev/null
import json
import os
import re
import shlex
import subprocess
import sys

cmd = sys.argv[1]
sid = sys.argv[2]
cwd = sys.argv[3]

ALLOWED = {
    ">300-loc", "schema-migration", "api-break", "security-boundary",
    "cross-cutting", "needs-user-decision", "planned-work", "user-request",
}

HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\s*$")
CATFILE_RE = re.compile(r'^\s*cat\s*>>?\s*([^\s<>&;|]+)')

lines = cmd.split("\n")
n = len(lines)

# ---- pass 1: locate every heredoc, capture its body, and note whether its
# trigger line looks like `cat > FILE <<DELIM` (file-attached) or is bare
# (direct-attached to whatever command precedes it on that same line).
file_bodies = {}     # filename -> body text
direct_bodies = {}   # delim -> body text (heredoc with no `cat >` in front)
skeleton_lines = list(lines)  # heredoc BODY lines blanked out below

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
    # blank the body span out of the skeleton (keep the trigger + closing
    # lines so segment classification still sees the command + the `<<DELIM`
    # marker for direct-attach resolution).
    for k in range(i + 1, min(j + 1, n)):
        skeleton_lines[k] = ""
    i = j + 1

skeleton = "\n".join(skeleton_lines)

# ---- pass 2: segment the skeleton exactly like block-gh-invalid-json-flag.sh
# (same shape, deliberately reused rather than reinvented — see that hook's
# #85 note).
ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")


def split_top_level(text):
    """Split on &&/||/;/&/|/newline, but QUOTE-AWARE — a `;`/`|` sitting
    inside a real issue TITLE (free text; real corpus example: camera-box
    #827's title literally contains one) must never be treated as a command
    separator. block-gh-invalid-json-flag.sh's plain regex split accepts
    this as a known limitation for ITS narrower job; this hook's whole
    purpose is reading a real title/body, so the same shortcut would
    silently false-block real, legitimate filings."""
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
    """Return the value for --flag VAL / --flag=VAL / -F VAL, or None."""
    for idx, t in enumerate(tk):
        for name in names:
            if t == name and idx + 1 < len(tk):
                return tk[idx + 1]
            if t.startswith(name + "="):
                return t[len(name) + 1:]
    return None


def is_issue_create(tk):
    return len(tk) >= 3 and tk[0] == "gh" and tk[1] == "issue" and tk[2] == "create"


def is_api_issues_post(tk):
    if not tk or tk[0] != "gh" or "api" not in tk[:2]:
        return False
    has_issues = any("issues" in t for t in tk)
    has_post = False
    for idx, t in enumerate(tk):
        if t in ("-X", "--method") and idx + 1 < len(tk) and tk[idx + 1].upper() == "POST":
            has_post = True
        if re.match(r'^-X\s*POST$', t, re.I) or t.upper() in ("-XPOST",):
            has_post = True
    return has_issues and has_post


def resolve_body(tk, seg_line, is_api):
    if is_api:
        # `gh api` overloads -f/-F for arbitrary key=value FIELDS (typed
        # vs raw), unlike `gh issue create`'s -F <FILE PATH>. Find a
        # `body=<value>` field among -f/-F/--field/--raw-field tokens.
        for idx, t in enumerate(tk):
            if t in ("-f", "-F", "--field", "--raw-field") and idx + 1 < len(tk):
                v = tk[idx + 1]
                if v.startswith("body="):
                    return v[len("body="):]
            elif t.startswith(("-f", "-F", "--field=", "--raw-field=")) and "=" in t:
                # -fbody=x / --field=body=x shapes — best-effort only.
                pass
        return None
    bf = flag_value(tk, ("-F", "--body-file"))
    if bf is not None:
        if bf == "-":
            m = HEREDOC_RE.search(seg_line.rstrip())
            if m and m.group(2) in direct_bodies:
                return direct_bodies[m.group(2)]
            return None
        if bf in file_bodies:
            return file_bodies[bf]
        try:
            path = bf if os.path.isabs(bf) else os.path.join(cwd, bf)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None
    inline = flag_value(tk, ("--body",))
    if inline is not None:
        return inline
    return None


CRITERION_RE = re.compile(r'(?m)^\s*Scope-gate:\s*(\S+)')

# #311 -- chain-depth cap. A review-finding follow-up NAMES its own PARENT
# issue as a "follow-up" -- confirmed to be the naming convention every real
# chain member in the odoo-erp scope-gate.log corpus independently converged
# on ("(#3224 follow-up)", "cross-screen half of #3224 is still open"), so
# detecting THIS phrasing needs no new discipline for workers to adopt, only
# a mechanical check on what they already write.
FOLLOWUP_RE = re.compile(
    r'#(\d+)[^\n]{0,20}\bfollow[- ]?up\b|\bfollow[- ]?up\b[^\n]{0,20}#(\d+)',
    re.I)


def _chain_parent(text):
    m = FOLLOWUP_RE.search(text or "")
    if not m:
        return None
    return m.group(1) or m.group(2)


def _gh_view_text(parent, cwd):
    """title + "\\n" + body of issue `parent`, or None on ANY failure
    (offline, no `gh` auth, the issue genuinely doesn't exist, `gh` not on
    PATH). A failure here degrades the chain-depth check to "cannot
    verify" -- it must NEVER block on its own; the existing Scope-gate
    criterion still decides, exactly as before this ticket."""
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(parent), "--json", "title,body"],
            capture_output=True, text=True, timeout=8, cwd=cwd)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or "{}")
        return (data.get("title") or "") + "\n" + (data.get("body") or "")
    except Exception:
        return None


results = []  # (verdict, title, criterion_or_none)

for seg in split_top_level(skeleton):
    if not seg.strip():
        continue
    tk = strip_prefix(tokens_of(seg))
    api_call = is_api_issues_post(tk)
    if not (is_issue_create(tk) or api_call):
        continue
    title = flag_value(tk, ("-t", "--title"))
    if title is None and api_call:
        for idx, t in enumerate(tk):
            if t in ("-f", "-F", "--field", "--raw-field") and idx + 1 < len(tk) \
                    and tk[idx + 1].startswith("title="):
                title = tk[idx + 1][len("title="):]
                break
    title = title or "(no title)"
    body = resolve_body(tk, seg, api_call)
    crit = None
    if body:
        m = CRITERION_RE.search(body)
        if m:
            crit = m.group(1)
    # #311 -- a review-finding follow-up whose own PARENT is ITSELF such a
    # follow-up is a depth-2 review-finding chain -- a self-reinforcing
    # sequence the follow-up gate's PER-ISSUE criterion cannot see, since
    # each individual hop can honestly claim its own criterion. Cheap text
    # match first; the ONE bounded `gh` call only fires once a candidate
    # parent is actually named.
    chain_capped = False
    parent = _chain_parent((title or "") + "\n" + (body or ""))
    if parent:
        parent_text = _gh_view_text(parent, cwd)
        if parent_text is not None and _chain_parent(parent_text):
            chain_capped = True
    if chain_capped:
        results.append(("BLOCK", title, "chain-depth-cap"))
    elif crit and crit.lower() in ALLOWED:
        results.append(("PASS", title, crit))
    else:
        results.append(("BLOCK", title, crit))

if not results:
    sys.exit(0)

repo = os.path.basename(cwd.rstrip("/"))
try:
    out = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                          capture_output=True, text=True, timeout=3)
    url = (out.stdout or "").strip()
    m = re.search(r'[:/]([^/]+/[^/]+?)(\.git)?$', url)
    if m:
        repo = m.group(1)
except Exception:
    pass

blocked = [r for r in results if r[0] == "BLOCK"]
for verdict, title, crit in results:
    # bash's `read` with IFS=<tab> still treats tab as "IFS whitespace" and
    # COLLAPSES consecutive delimiters, silently swallowing an empty field
    # (discovered live testing this hook) -- never emit an empty field.
    print("%s\t%s\t%s\t%s\t%s" % (verdict, repo, title, crit or "none", sid))

sys.exit(2 if blocked else 0)
PYEOF
) || RC=$?

if [ -n "$OUT" ]; then
    while IFS=$'\t' read -r VERDICT REPO TITLE CRIT LOGSID; do
        [ -z "$VERDICT" ] && continue
        echo "$(date -Iseconds)  verdict=$VERDICT  repo=$REPO  title=\"$TITLE\"  criterion=${CRIT:-none}  session=$LOGSID" >> "$LOG" 2>/dev/null || true
    done <<< "$OUT"
fi

if [ "$RC" -eq 2 ]; then
    cat >&2 <<'MSG'
🚫 BLOCKED: filing this issue.

Either (a) no valid `Scope-gate:` line, or (b) this issue's own PARENT (the
"#N follow-up" it names) is ITSELF a review-finding follow-up -- a depth-2
review-finding chain (#311: adversarial-review findings that keep spawning
follow-up tickets of follow-up tickets, unbounded — a criterion honestly
satisfied at each individual hop does not fix this).

Per complete-planned-work.md's Follow-up gate: a discovered cleanup under
~100 LoC in a file your current work already touches gets FIXED NOW in this
PR/session — it does NOT get filed as a follow-up issue, EVEN when it
technically sits outside your diff. Filing is for GENUINELY out-of-scope
work only, and a review finding that would spawn a SECOND generation of
follow-up must be fixed in THIS branch instead, never filed as a third
ticket.

Fix NOW — one of:
  1. Fix it in this session/PR instead of filing it (mandatory once the
     chain-depth cap above applies), OR
  2. Add a line to the issue body naming why it is genuinely out of scope:
       Scope-gate: <criterion>
     where <criterion> is one of:
       >300-loc | schema-migration | api-break | security-boundary |
       cross-cutting | needs-user-decision | planned-work | user-request

This is a LOGGED, falsifiable claim (~/.claude/scope-gate.log) — it does not
verify the criterion is true, only that you affirmatively claimed one instead
of silently filing. See modules/quality/complete-planned-work.md and
modules/quality/no-dropped-work.md. Genuine bypass: append
`# airuleset:scope-gate-ok <reason>` to the command.
MSG
    exit 2
fi

exit 0
