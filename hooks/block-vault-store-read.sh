#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — issue #153 finding 1.
#
# The credential store (`~/.claude/secrets/<NAME>.secret`, the `airuleset.py
# secret` channel from #144) is not read by hand. `secret exec` hands the value
# to a child process with fd 1/2 captured and filtered; every other way of
# getting at the file puts the value into the session transcript
# (`~/.claude/projects/**/*.jsonl`), where it survives compaction and cannot be
# revoked — the one outcome the whole channel exists to prevent.
#
# Before this hook that guarantee was VOLUNTARY: the store is 0600 owned by the
# very uid the agent's Bash runs as, and nothing gated it, so it held only for
# as long as the agent chose `secret exec` over `cat`. A guarantee that defers
# to an unenforced action is a silence generator (the run-card lesson, #134).
# This is the artifact it rests on instead.
#
# WHAT IS MATCHED — the RAW command text, not parsed argv. Every interesting
# evasion hides the path inside a quoted string where token parsing cannot see
# it: `python3 -c 'open("…/DB.secret").read()'`, `< …/DB.secret`,
# `$(<…/DB.secret)`. Two patterns:
#   A. a store-dir reference — `.claude/secrets` however it is spelled
#      (`~/`, `$HOME/`, an absolute path, or bare);
#   B. any `<stem>.secret` filename — this channel's own extension, so a
#      relative read after a `cd` into the store is still caught.
# The command is then split into segments (quote-aware, and command
# substitutions `$(...)` / backticks become their OWN segments so a read
# nested inside an allowlisted head — `ls "$(cat …/DB.secret)"` — is still
# seen). Any segment referencing the store is DENIED unless its head command is
# provably metadata-only.
#
# DENY-BY-DEFAULT, deliberately. The alternative — blocklisting reader commands
# (cat/less/head/xxd/base64/…) — is an enumeration of the vocabulary, and one
# unlisted reader (`bat`, `nl`, `tac`, a future pager) walks straight through
# with no signal. Here an unanticipated reader fails CLOSED.
#
# WRITES are blocked too: hand-writing a value into the store means typing the
# credential into a shell command, which is the same leak from the other side.
# Use `secret request` — the user posts it from their own browser.
#
# ALLOWED (metadata only — no content, no relocation): ls, stat, file, test,
# du, rm, shred, chmod, chown, touch, cd, pushd. Plus the whole sanctioned CLI
# surface (`airuleset.py secret request|status|list|exec|forget|purge`), which
# never names a store path on the command line at all.
#
# BYPASS — env only, and always logged:
#   AIRULESET_ALLOW_VAULT_READ=1   -> audits/vault-store-reads.log
# There is DELIBERATELY no inline `# airuleset:*-ok <reason>` marker, unlike
# every other hook in this repo. A marker the agent can append to its own
# command is exactly the voluntary compliance this hook exists to remove; the
# env bypass is for the USER. No legitimate need is lost: `secret exec` covers
# use, `secret list`/`status` metadata, `secret forget` deletion.
#
# KNOWN GAPS — stated because the point of this ticket is not to imply a
# stronger guarantee than is delivered:
#   - This is a GUARDRAIL, not a security boundary. The agent's uid holds
#     `NOPASSWD: ALL` sudo on these boxes (verified on dev1), so no store
#     location is unreadable to it and no `sudo`-based separation is
#     achievable. What this delivers is: the unsafe path is refused by
#     default, so the leak cannot happen by reflex, and any deliberate
#     circumvention leaves a greppable artifact (an audit line, or the
#     removal itself in git).
#   - A hook is configuration. Anything that edits settings.json, unregisters
#     this hook, or reaches the file outside the Bash tool is outside its
#     reach by construction.
#   - A path computed at runtime rather than written literally
#     (`python3 -c "import pathlib; open(pathlib.Path.home()/'.claude'/'secrets'/n)"`,
#     a variable assembled from parts, a path read out of another file) does
#     not match either pattern — text matching cannot see it.
#   - Not a shell parser: process substitution, `xargs` fed from a file list,
#     and a wrapper script that does the read internally are invisible.
#
# Exit code 2 = block the tool call.

# Read the payload with a SHELL BUILTIN, not `cat`: the fail-closed branch
# below has to work even when PATH is broken, and reading stdin through an
# external binary would make a missing PATH look like "no payload" (allow)
# instead of "cannot check" (block).
PAYLOAD=""
line=""
while IFS= read -r line || [ -n "$line" ]; do
    PAYLOAD+="$line"$'\n'
    line=""
done
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"

# Nothing to inspect at all is not a violation — the hook simply has no input.
[ -z "${PAYLOAD//[$'\n\t ']/}" ] && exit 0

fail_closed() {
    echo "" >&2
    echo "🚫 BLOCKED (fail-closed): block-vault-store-read.sh could not run its check." >&2
    echo "  $1" >&2
    echo "" >&2
    echo "  This is a HOOK MALFUNCTION, not necessarily a real violation — but a" >&2
    echo "  guard that cannot run must not silently open the credential store." >&2
    echo "  Investigate and fix the hook (or install python3) before retrying." >&2
    echo "" >&2
    exit 2
}

command -v python3 >/dev/null 2>&1 || fail_closed "python3 is not available."

# The payload travels in ARGV, never on stdin: the heredoc below IS this
# process's stdin (it carries the script), so a piped payload would arrive
# empty and every check would silently pass.
VIOLATION=$(python3 - "$PAYLOAD" <<'PYEOF'
import json
import re
import shlex
import sys

raw = sys.argv[1]
try:
    cmd = json.loads(raw).get("tool_input", {}).get("command", "") or ""
except Exception:
    cmd = raw          # not JSON: inspect what we were given rather than skip

if not cmd.strip():
    sys.exit(0)

# A. the store directory, however it is spelled.
STORE_DIR_RE = re.compile(r"\.claude/+secrets(?![A-Za-z0-9_-])")
# B. a value file by name. The stem must start with an alnum/underscore, so a
# regex or glob fragment (`"\.secret\b"`, `(".secret",`) is not a match.
VALUE_FILE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.secret(?![A-Za-z0-9_-])")

# Heads that cannot move or reveal the CONTENT of a file.
ALLOW_HEADS = {"ls", "stat", "file", "test", "[", "du", "rm", "shred",
               "chmod", "chown", "touch", "cd", "pushd"}
PREFIXES = {"sudo", "env", "time", "nice", "ionice", "command", "builtin", "exec"}
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def split_segments(text):
    """Quote-aware split on shell separators.

    Command substitutions become their OWN segments — `$(` and backticks are
    separators even inside double quotes, where the shell really does expand
    them — so a read nested inside an allowlisted head is not laundered by it.
    Inside SINGLE quotes nothing is a separator, which keeps a `python3 -c
    '...'` body intact as one segment headed by python3.
    """
    segs, buf = [], []
    i, n = 0, len(text)
    in_sq = in_dq = False
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if in_sq:
            if c == "'":
                in_sq = False
            buf.append(c)
            i += 1
            continue
        if two == "$(":
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c == "`":
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        if in_dq:
            if c == "\\" and i + 1 < n:
                buf.append(c)
                buf.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_dq = False
            buf.append(c)
            i += 1
            continue
        if c == "'":
            in_sq = True
            buf.append(c)
            i += 1
            continue
        if c == '"':
            in_dq = True
            buf.append(c)
            i += 1
            continue
        if two in ("&&", "||"):
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in ";|&\n()":
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return segs


def head_of(segment):
    try:
        tk = shlex.split(segment)
    except ValueError:
        tk = segment.split()
    i = 0
    while i < len(tk) and (tk[i] in PREFIXES or ASSIGN_RE.match(tk[i])):
        i += 1
    tk = tk[i:]
    if not tk:
        return None
    return tk[0].rsplit("/", 1)[-1].lower()


def references_store(segment):
    m = STORE_DIR_RE.search(segment) or VALUE_FILE_RE.search(segment)
    return m.group(0) if m else None


hits = []
for seg in split_segments(cmd):
    ref = references_store(seg)
    if not ref:
        continue
    head = head_of(seg)
    if head in ALLOW_HEADS:
        continue
    hits.append("%s  ->  %s" % (head or "(redirection/substitution)",
                                seg.strip()[:120]))

if hits:
    print("\n".join("  " + h for h in dict.fromkeys(hits)))
    sys.exit(2)
sys.exit(0)
PYEOF
) && RC=0 || RC=$?

if [ "$RC" -eq 0 ]; then
    exit 0
fi

if [ "$RC" -ne 2 ]; then
    fail_closed "python3 exited $RC instead of running the check. $VIOLATION"
fi

# --- a real hit ------------------------------------------------------------
CMD_TEXT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
raw=sys.stdin.read()
try: print(json.loads(raw).get("tool_input",{}).get("command","") or "")
except Exception: print(raw)' 2>/dev/null || echo "")

if [ "${AIRULESET_ALLOW_VAULT_READ:-}" = "1" ]; then
    AUDIT_LOG="${AIRULESET_VAULT_READ_AUDIT:-$HOME/devel/airuleset/audits/vault-store-reads.log}"
    mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true
    {
        echo "$(date -Iseconds 2>/dev/null || echo unknown)  env-bypass  cmd=${CMD_TEXT}"
    } >> "$AUDIT_LOG" 2>/dev/null || true
    exit 0
fi

echo "" >&2
echo "🚫 BLOCKED: the credential store is not read (or written) by hand." >&2
echo "" >&2
echo "$VIOLATION" >&2
echo "" >&2
echo "  A value read this way lands in the session transcript, survives" >&2
echo "  compaction, and cannot be revoked — the exact leak the credential" >&2
echo "  channel exists to prevent." >&2
echo "" >&2
echo "  Use the value WITHOUT seeing it:" >&2
echo "    python3 ~/devel/airuleset/airuleset.py secret exec <NAME> -- <cmd>" >&2
echo "  It hands the value to the child through the environment (or --stdin)," >&2
echo "  captures fd 1/2 and filters the value out of them." >&2
echo "" >&2
echo "  Metadata, without the value:  secret list  /  secret status <NAME>" >&2
echo "  Remove it:                    secret forget <NAME>" >&2
echo "  Get a NEW value from the user: secret request <NAME>  (never ask in chat)" >&2
echo "" >&2
echo "  HONEST LIMIT: this is a GUARDRAIL, not a security boundary. The agent's" >&2
echo "  uid holds NOPASSWD sudo on these boxes, so no store location is beyond" >&2
echo "  its reach; what this guarantees is that the unsafe path is refused by" >&2
echo "  default and that circumventing it leaves an artifact." >&2
echo "" >&2
echo "  Bypass (user-instructed only, logged): AIRULESET_ALLOW_VAULT_READ=1." >&2
echo "  There is deliberately no inline marker — see the hook's header." >&2
echo "" >&2
exit 2
