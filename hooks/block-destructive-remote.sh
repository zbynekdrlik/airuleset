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
# --- win-* MCP vs ssh (issue #249) --------------------------------------
# ALSO hard-blocks a GUI-session-dependent Windows command (MainWindow*,
# EnumWindows, SendKeys, Start-Process, or a screenshot API mention) sent
# over ssh, but ONLY inside a project whose .mcp.json declares a `win-*`
# MCP server. Session 0 (an ssh shell on Windows) structurally cannot see a
# session-1 desktop window via EnumWindows — a MainWindowTitle probe over
# ssh reads empty on a perfectly healthy box, which is exactly what failed
# camera-box PR #989's rig-health gate 3x (issuecomment-5191660073). The
# sanctioned `schtasks ... /it` interactive-session bridge the
# windows-remote-gui skill teaches is exempted when present in the SAME
# remote command. This is a project-scoped gate (never a blanket ssh ban —
# a project with no win-* MCP server is untouched); NOT scoped to the exact
# declared host, since `.mcp.json`'s mcpServers schema has no standardized
# host field to extract reliably (see #249's design comment for why exact
# host resolution was rejected). Every OTHER ssh call reaching this
# classifier inside a win-* MCP project is never BLOCKED — only an
# ssh-invoked CLI/headless command (camera-box #701/#703's own decode)
# gets a non-blocking `additionalContext` reminder of the two-context
# litmus test; a pure file-copy transport (`scp`/`rsync`) stays fully
# silent.
#
# KNOWN GAPS in this extension (adversarial review, #249 — best-effort,
# same rigor level as the rest of this hook, not a full shell parser):
#   - Requires the literal `win-` PREFIX on an mcpServers key — a
#     project naming its server `windows-obs` (off the `win-*`/`mcp__win-*`
#     ecosystem convention every real project uses) is not gated.
#   - A quote-spliced atom (`'Main'"'"'WindowTitle'`) or one hidden behind
#     `bash -c "..."` indirection is invisible — the argv parser doesn't
#     reassemble splices or recurse into a nested shell, same limitation
#     already stated above for the destructive-verb checks.
#   - `Start-Process` matches a hyphen-delimited SUBSTRING too (e.g.
#     `start-process-manager`) — `\b` anchors on the hyphen. Implausible
#     over ssh to a Windows box; not worth a narrower pattern.
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

# #249: the win-* MCP gate needs the TOOL's own cwd (a project can genuinely
# differ from wherever this hook process's own $PWD happens to be) — prefer
# the JSON payload's `.cwd`, same fallback shape block-tier0-local-build.sh
# already uses (`dir="${CWD:-$PWD}"`), never trust bash's own cwd alone.
HOOK_CWD=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("cwd","") or "")
except Exception: pass' 2>/dev/null || echo "")

AUDIT_LOG="$HOME/devel/airuleset/audits/destructive-remote-bypasses.log"

# Bypass 1: explicit env opt-out.
if [ "${AIRULESET_ALLOW_DESTRUCTIVE_REMOTE:-}" = "1" ]; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    mkdir -p "$(dirname "$AUDIT_LOG")"
    echo "$(date -Iseconds)  project=$PROJECT  env-bypass  cmd=${INPUT}" >> "$AUDIT_LOG"
    exit 0
fi

# Bypass 2: inline '# airuleset:destructive-ok <reason>' trailing the command.
# The marker must be OUTSIDE any quoted string — a real bash `#` only starts
# a comment when it is not inside quotes, so quoted spans are stripped
# FIRST. Without this, the marker text merely being MENTIONED inside an
# unrelated quoted string (documentation, an echo) would bypass the ENTIRE
# check, including a genuinely dangerous UNRELATED command elsewhere on the
# same line — same class of bug already fixed in block-sensitive-staging.sh
# (d1fde9b).
BYPASS_REASON=$(printf '%s' "$INPUT" | python3 -c 'import re,sys
cmd=sys.stdin.read()
SQ=chr(39)
DQ=chr(34)
unquoted=re.sub(SQ+"[^"+SQ+"]*"+SQ, "", cmd)     # strip '"'"'...'"'"' spans
unquoted=re.sub(DQ+"[^"+DQ+"]*"+DQ, "", unquoted)  # strip "..." spans
m=None
for mm in re.finditer(r"#[ \t]*airuleset:destructive-ok[ \t]+([^\n]+)", unquoted):
    m=mm
if m:
    print(m.group(1).rstrip())
' 2>/dev/null || echo "")

if [ -n "$BYPASS_REASON" ]; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    mkdir -p "$(dirname "$AUDIT_LOG")"
    echo "$(date -Iseconds)  project=$PROJECT  inline-bypass  # airuleset:destructive-ok $BYPASS_REASON" >> "$AUDIT_LOG"
    exit 0
fi

VIOLATION=$(python3 - "$INPUT" "${HOOK_CWD:-$PWD}" <<'PYEOF'
import json
import os
import re
import shlex
import sys

cmd = sys.argv[1]

# --- win-* MCP vs ssh (issue #249): project-scoped GUI-hazard gate --------
GUI_HAZARD_RE = re.compile(
    r"(?i)\b(MainWindow\w*|EnumWindows|SendKeys|Start-Process"
    r"|CopyFromScreen|System\.Windows\.Forms|System\.Drawing\.Bitmap)\b"
)
SCHTASKS_BRIDGE_RE = re.compile(r"(?i)\bschtasks\b.*(/it\b|/interactive\b)")
WIN_MCP_SERVER_RE = re.compile(r"(?i)^win-")


MCP_JSON_MAX_BYTES = 65536  # a real .mcp.json is tiny; this only bounds a hostile/huge one


def _win_mcp_active(cwd):
    """True iff <cwd>/.mcp.json declares >=1 `win-*` mcpServers entry.

    Best-effort, project-scoped ONLY (never a blanket ssh ban) — a missing
    or malformed .mcp.json (or one with no win-* key) means "not gated",
    never a guess in either direction. Deliberately does NOT try to
    resolve which HOST each win-* server targets: the mcpServers schema
    has no standardized host field, and a wrong extraction would fail in
    the dangerous direction (narrowing the block off the real host). See
    #249's design comment for the full reasoning.

    Runs on EVERY Bash tool call in a project (not just ssh ones), so the
    read must be BOUNDED and REFUSE a symlink (#249 adversarial-review
    finding 1, live-triggered): a `.mcp.json` symlinked to `/dev/zero` (or
    any endless-read device) made the OLD `open(path).read()` shape hang
    every single Bash command in that cwd, not just an ssh one — a boxes
    hosting foreign uids by design makes a planted/hostile `.mcp.json`
    realistic, not hypothetical. O_NOFOLLOW refuses the symlink outright
    (a legitimate symlinked config just reads as "not gated" — same fail
    direction as any other unreadable file); the bounded os.read() closes
    the slower sibling case (a genuinely huge REGULAR file) the same way.
    """
    path = os.path.join(cwd, ".mcp.json")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except Exception:
        return False
    try:
        raw = os.read(fd, MCP_JSON_MAX_BYTES + 1)
    except Exception:
        return False
    finally:
        os.close(fd)
    if len(raw) > MCP_JSON_MAX_BYTES:
        return False
    try:
        cfg = json.loads(raw.decode("utf-8"))
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    return any(WIN_MCP_SERVER_RE.match(str(name)) for name in servers)


WIN_MCP_ACTIVE = _win_mcp_active(sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else os.getcwd())
warnings = []


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


ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def strip_prefix(tk):
    # drop a leading sudo/env/time/nice/ionice runner AND any leading
    # `VAR=val` environment-assignment token(s) — `FOO=1 ssh host reboot`
    # must be detected exactly like `ssh host reboot`, not hidden by the
    # assignment prefix.
    i = 0
    while i < len(tk) and (tk[i] in ("sudo", "env", "time", "nice", "ionice")
                            or ASSIGN_RE.match(tk[i])):
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
    # NOTE: -H is deliberately NOT a host flag here — in psql, -H means
    # "HTML output" (mysql's -H doesn't exist either); only -h/--host name
    # a remote host. Treating -H as a host flag made a purely LOCAL
    # `psql -H -c 'DROP TABLE ...'` false-positive as a remote DROP.
    for i, t in enumerate(tokens):
        if t in ("-h", "--host") and i + 1 < len(tokens):
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
    if WIN_MCP_ACTIVE:
        atom = GUI_HAZARD_RE.search(seg_text)
        if atom and not SCHTASKS_BRIDGE_RE.search(seg_text):
            hits.append(
                "GUI-session-dependent command over ssh in a win-* MCP "
                "project (" + atom.group(0) + "): " + seg_text.strip()[:120]
            )
        elif not atom:
            # some OTHER ssh use in a win-* MCP project — never a block,
            # just the litmus-test reminder (fired once, on the ALLOW path).
            warnings.append(seg_text.strip()[:120])
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
if warnings:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                "Windows remote over ssh in a win-* MCP project — the "
                "two-context litmus test: desktop-dependent (a GUI window, "
                "a screenshot, anything session-1-only)? use the mcp__win-* "
                "tools (or the windows-remote-gui skill's schtasks .../it "
                "bridge) — session 0 (ssh) cannot see the desktop. File "
                "copy / a CLI exe / a process-or-port-registry query? ssh "
                "is fine, proceed."
            ),
        }
    }))
sys.exit(0)
PYEOF
) || RC=$?
RC=${RC:-0}

if [ "$RC" -eq 2 ]; then
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
    case "$VIOLATION" in
        # matches the FIXED prefix this hook itself generates for the new
        # hazard (never a bare "GUI-session-dependent" substring, which a
        # decoy comment inside an UNRELATED command's own echoed text could
        # coincidentally embed — #249 adversarial-review finding 4).
        *"GUI-session-dependent command over ssh in a win-* MCP project"*)
            echo "  Two-context rule (Windows / win-* MCP): session 0 (an ssh" >&2
            echo "  shell on Windows) structurally cannot see a session-1" >&2
            echo "  desktop window — a MainWindowTitle/EnumWindows probe over" >&2
            echo "  ssh reads EMPTY on a perfectly HEALTHY box, not a broken" >&2
            echo "  one. Use the mcp__win-* tools instead (or the" >&2
            echo "  windows-remote-gui skill's schtasks .../it interactive" >&2
            echo "  bridge for a genuine GUI launch) — never an ssh probe." >&2
            echo "" >&2
            ;;
    esac
    echo "  Bypass (rare, user-instructed only, logged): append" >&2
    echo "  '# airuleset:destructive-ok <reason>' to the command, or set" >&2
    echo "  AIRULESET_ALLOW_DESTRUCTIVE_REMOTE=1." >&2
    echo "" >&2
    exit 2
elif [ "$RC" -ne 0 ]; then
    # A non-2 nonzero exit means the CHECK ITSELF malfunctioned (missing
    # python3, an internal bug) — never a real destructive-command
    # violation. Fail CLOSED but say so HONESTLY instead of reusing the
    # "BLOCKED: destructive command" message with an empty reason.
    echo "" >&2
    echo "🚫 BLOCKED (fail-closed): block-destructive-remote.sh internal error" >&2
    echo "  — python3 exited $RC instead of running the check." >&2
    echo "$VIOLATION" >&2
    echo "" >&2
    echo "  This is a HOOK MALFUNCTION, not necessarily a real violation —" >&2
    echo "  investigate and fix the hook (or install python3) before retrying." >&2
    echo "" >&2
    exit 2
fi

# RC == 0 (allow). $VIOLATION may carry a non-blocking win-* MCP litmus-test
# reminder (JSON additionalContext, issue #249) — empty on the ordinary
# no-op-nothing-detected path, so this never prints a stray blank line.
if [ -n "$VIOLATION" ]; then
    printf '%s\n' "$VIOLATION"
fi
exit 0
