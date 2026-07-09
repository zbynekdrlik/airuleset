#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — issue #13 sub-item 1.
# Blocks a NARROW, high-confidence subset of destructive commands aimed at a
# REMOTE host, per no-destructive-remote-actions.md's "requires approval
# EVERY TIME" list:
#   - HOST-level power-off: shutdown/reboot/halt/poweroff (or
#     `systemctl poweroff|reboot|halt`, `init 0|6`) run over ssh.
#   - Filesystem-root wipe: `rm -rf /` (or /*, ~, $HOME) — NOT any `rm -rf`,
#     only a catastrophic bare-root target — run over ssh. Windows
#     equivalents: `del /S /Q C:\`, `Remove-Item -Recurse -Force C:\`.
#   - SQL DROP TABLE/DATABASE/SCHEMA or TRUNCATE against a REMOTE database
#     (a DB client invocation inside an ssh remote command, or a direct
#     psql/mysql/mariadb call naming an explicit non-local -h/--host or a
#     connection URI with a non-local host).
#
# DELIBERATELY NOT covered (real FP corpus checked — see
# no-destructive-remote-actions.md's "NOT gated" section and the
# deploy-ssh skill's own sanctioned commands):
#   - `systemctl stop|start|restart SERVICE`, `taskkill /F /IM app.exe`,
#     `sc start|stop SERVICE` — the deploy flow's own restart-the-service-
#     being-deployed commands (approval-scope.md: NOT gated, it's the work).
#   - `rm -rf` on ANY non-root path (temp dirs, build dirs, old releases) —
#     routine over ssh; only a bare filesystem-root target is flagged.
#   - `DELETE FROM ...` — routine app-level cleanup (cache eviction, expired
#     sessions); far too common to gate without a WHERE-less-table heuristic
#     that would still misfire. Only DROP/TRUNCATE are covered.
#   - Local-only rm -rf / (no ssh wrapper, no remote DB host) — this module
#     is scoped to REMOTE actions; a fully local host-wipe is a different
#     (also real, but out of scope) risk.
#
# KNOWN GAPS (best-effort, not a full shell parser — same rigor level as
# block-history-rewrite.sh / block-sensitive-staging.sh):
#   - SQL piped via a separate command (`cat migration.sql | ssh host psql`,
#     heredoc-fed SQL) is not detected — the DROP/TRUNCATE text and the
#     psql/mysql invocation live in different pipeline segments.
#   - A destructive verb passed through more than one layer of indirection
#     (a wrapper script invoked over ssh that itself shuts down the host)
#     is invisible to argv-level matching.
#
# Bypass (rare, user-instructed only, logged): append
# '# airuleset:destructive-ok <reason>' to the command, or set
# AIRULESET_ALLOW_DESTRUCTIVE_REMOTE=1.
#
# Exit code 2 = block the tool call.

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
[ -z "$INPUT" ] && INPUT="$PAYLOAD"

[ -z "$INPUT" ] && exit 0

AUDIT_LOG="$HOME/devel/airuleset/audits/destructive-remote-bypasses.log"

# Bypass 1: explicit env opt-out.
if [ "${AIRULESET_ALLOW_DESTRUCTIVE_REMOTE:-}" = "1" ]; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    mkdir -p "$(dirname "$AUDIT_LOG")"
    echo "$(date -Iseconds)  project=$PROJECT  env-bypass  cmd=${INPUT}" >> "$AUDIT_LOG"
    exit 0
fi

# Bypass 2: inline '# airuleset:destructive-ok <reason>' trailing the command.
if echo "$INPUT" | grep -qE '#[[:space:]]*airuleset:destructive-ok'; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    mkdir -p "$(dirname "$AUDIT_LOG")"
    REASON=$(echo "$INPUT" | grep -oE '#[[:space:]]*airuleset:destructive-ok.*' | head -1)
    echo "$(date -Iseconds)  project=$PROJECT  inline-bypass  $REASON" >> "$AUDIT_LOG"
    exit 0
fi

VIOLATION=$(python3 - "$INPUT" <<'PYEOF'
import re
import shlex
import sys

cmd = sys.argv[1]


def split_segments(text):
    """Quote-AWARE split on shell separators (&&, ||, ;, |, &, newline).
    Unlike a plain regex split, this never splits INSIDE a quoted string —
    required because the interesting content here is usually the quoted
    remote-command string passed to ssh (which legitimately contains its
    own && / | chaining that must stay intact for re-parsing)."""
    segments = []
    buf = []
    i, n = 0, len(text)
    in_sq = in_dq = False
    while i < n:
        c = text[i]
        if in_sq:
            buf.append(c)
            if c == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            buf.append(c)
            if c == '\\' and i + 1 < n:
                buf.append(text[i + 1])
                i += 1
            elif c == '"':
                in_dq = False
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
        if text[i:i + 2] in ("&&", "||"):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in ";|&\n":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segments.append("".join(buf))
    return segments


def tokens_of(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def strip_prefix(tk):
    i = 0
    while i < len(tk) and tk[i] in ("sudo", "env", "time", "nice", "ionice"):
        i += 1
    return tk[i:]


SSH_VALUE_FLAGS = {"-i", "-o", "-p", "-l", "-F", "-J", "-L", "-R", "-D",
                    "-W", "-B", "-b", "-c", "-m", "-e", "-Q", "-S"}
HOST_POWER_CMDS = {"shutdown", "reboot", "halt", "poweroff",
                    "restart-computer", "stop-computer"}
SYSTEMCTL_POWER_SUBS = {"poweroff", "reboot", "halt"}
ROOT_TARGETS = {"/", "/*", "~", "$HOME"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
DB_CLIENTS = {"psql", "mysql", "mariadb"}


def remote_command_string(tk):
    """tk = tokens of an `ssh ...` (or `sshpass ... ssh ...`) segment.
    Best-effort: skip ssh/sshpass + their flags + the host, join the rest
    back into the remote-command text for re-parsing."""
    if tk and tk[0] == "sshpass":
        try:
            idx = tk.index("ssh")
            tk = tk[idx:]
        except ValueError:
            return ""
    if not tk or tk[0] != "ssh":
        return ""
    i = 1
    host = None
    rest = []
    while i < len(tk):
        t = tk[i]
        if host is None:
            if t.startswith("-"):
                if t in SSH_VALUE_FLAGS and i + 1 < len(tk):
                    i += 2
                    continue
                i += 1
                continue
            host = t
            i += 1
            continue
        rest.append(t)
        i += 1
    return " ".join(rest)


def is_host_power_off(tokens):
    rt = strip_prefix(tokens)
    if not rt:
        return False
    base = rt[0].rsplit("/", 1)[-1].lower()
    if base in HOST_POWER_CMDS:
        return True
    if base == "systemctl" and len(rt) > 1 and rt[1].lower() in SYSTEMCTL_POWER_SUBS:
        return True
    if base == "init" and len(rt) > 1 and rt[1] in ("0", "6"):
        return True
    return False


def is_root_wipe(tokens):
    if not tokens or tokens[0].rsplit("/", 1)[-1].lower() != "rm":
        return False
    flag_chars = "".join(t.lstrip("-") for t in tokens[1:]
                          if t.startswith("-") and not t.startswith("--"))
    long_flags = {t for t in tokens[1:] if t.startswith("--")}
    has_r = "r" in flag_chars.lower() or "--recursive" in long_flags
    has_f = "f" in flag_chars.lower() or "--force" in long_flags
    if not (has_r and has_f):
        return False
    args = [t for t in tokens[1:] if not t.startswith("-")]
    return any(a.rstrip("/") in ROOT_TARGETS or a == "/" for a in args)


WIN_DEL_ROOT = re.compile(r'(?i)\bdel\s+/S\s+/Q\s+[A-Za-z]:\\?\s*$')
WIN_REMOVE_ITEM_ROOT = re.compile(
    r'(?i)\bRemove-Item\b(?=.*-Recurse\b)(?=.*-Force\b).*\b[A-Za-z]:\\?\s*$'
)


def is_win_root_wipe(text):
    return bool(WIN_DEL_ROOT.search(text) or WIN_REMOVE_ITEM_ROOT.search(text))


SQL_DROP_RE = re.compile(r'(?i)\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE(\s+TABLE)?)\b')


def has_db_client(tokens):
    return any(t.rsplit("/", 1)[-1].lower() in DB_CLIENTS for t in tokens)


def has_remote_db_host(tokens):
    for i, t in enumerate(tokens):
        if t in ("-h", "--host", "-H") and i + 1 < len(tokens):
            if tokens[i + 1].lower() not in LOCAL_HOSTS:
                return True
        m = re.match(r'--host=(.+)', t)
        if m and m.group(1).lower() not in LOCAL_HOSTS:
            return True
    for t in tokens:
        m = re.match(r'(?i)^(postgres(?:ql)?|mysql)://([^/@]*@)?([^:/]+)', t)
        if m and m.group(3).lower() not in LOCAL_HOSTS:
            return True
    return False


def check_remote_segment(seg_text):
    """Checks that ONLY apply once we know we're inside a remote (ssh) context."""
    hits = []
    for inner in split_segments(seg_text):
        tk = strip_prefix(tokens_of(inner))
        if not tk:
            continue
        if is_host_power_off(tk):
            hits.append("remote HOST shutdown/reboot/halt/poweroff over ssh: "
                        + " ".join(tk[:3]))
        if is_root_wipe(tk):
            hits.append("rm -rf on filesystem root over ssh: " + " ".join(tk))
        if has_db_client(tk) and SQL_DROP_RE.search(inner):
            hits.append("SQL DROP/TRUNCATE against a DB client over ssh: "
                        + inner.strip()[:120])
    if is_win_root_wipe(seg_text):
        hits.append("Windows drive-root wipe over ssh: " + seg_text.strip()[:120])
    return hits


violations = []
for seg in split_segments(cmd):
    tk = strip_prefix(tokens_of(seg))
    if not tk:
        continue
    head = tk[0].rsplit("/", 1)[-1].lower()
    if head == "ssh" or (head == "sshpass" and "ssh" in tk):
        remote_text = remote_command_string(tk)
        if remote_text:
            violations.extend(check_remote_segment(remote_text))
    elif head in DB_CLIENTS:
        if has_remote_db_host(tk[1:]) and SQL_DROP_RE.search(seg):
            violations.append(head + " against an explicit remote host — "
                              "SQL DROP/TRUNCATE: " + seg.strip()[:120])

if violations:
    seen = list(dict.fromkeys(violations))
    print("\n".join(f"  {v}" for v in seen))
    sys.exit(2)
sys.exit(0)
PYEOF
) || RC=$?
RC=${RC:-0}

if [ "$RC" -ne 0 ]; then
    echo "" >&2
    echo "🚫 BLOCKED: destructive command aimed at a REMOTE host/database." >&2
    echo "" >&2
    echo "$VIOLATION" >&2
    echo "" >&2
    echo "  Per no-destructive-remote-actions.md: host shutdown/reboot, a" >&2
    echo "  filesystem-root wipe, and SQL DROP/TRUNCATE on a remote database" >&2
    echo "  ALWAYS need explicit user approval first. Ask the user, wait for" >&2
    echo "  an explicit yes, then re-run (or use the bypass once approved)." >&2
    echo "" >&2
    echo "  This does NOT block the sanctioned deploy flow: restarting the" >&2
    echo "  service you're deploying (systemctl stop/start/restart, taskkill" >&2
    echo "  /F, sc start/stop), or rm -rf on a non-root path (temp/build" >&2
    echo "  dirs) — those are approved per approval-scope.md." >&2
    echo "" >&2
    echo "  Bypass (rare, user-instructed only, logged): append" >&2
    echo "  '# airuleset:destructive-ok <reason>' to the command, or set" >&2
    echo "  AIRULESET_ALLOW_DESTRUCTIVE_REMOTE=1." >&2
    echo "" >&2
    exit 2
fi

exit 0
