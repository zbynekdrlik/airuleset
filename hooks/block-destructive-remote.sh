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
# --- secret-bearing file / process-environment reads over ssh (#373) ------
# ALSO blocks a HIGH-CONFIDENCE transcript-leak shape inside the remote
# command string: cat/less/head/tail/grep of a secret-pattern file
# (.env / mcp.env / *.env.<suffix> minus .example|.sample|.template|.dist,
# a /secrets/ path component, *credential*/*secret* in the basename,
# *token* as a name component, *.pem/*.key) whose stdout is NOT redirected
# away with `>`/`>>`/`&>`; or a BARE printenv/env dump (no specific var
# name — a genuine full-environment dump, however wrapped, e.g. `docker
# compose exec -T svc printenv`). This is the exact odoo-erp#3161 +
# odoo-erp#3493 incident shape (`ssh box "cat mcp.env"`,
# `ssh box "docker compose exec -T mcp printenv | grep -E '^MCP_'"` — a
# PREFIX grep still prints full KEY=value lines, not narrow enough).
#
# ALLOWED (deliberately, not gaps):
#   - the sanctioned pipe-to-remote provisioning flow, `cat <secretfile> |
#     ssh host "cat > remote-path"` — the local cat is outside the ssh
#     wrapper entirely (untouched by this check); the remote side is a
#     stdout-REDIRECTED write, never a read whose output prints.
#   - a `> file`/`>> file`/`&> file` redirect on ANY of the above — stdout
#     never reaches the transcript regardless of content.
#   - a named single var (`printenv PATH`) or `env` used as the standard
#     "run with this var set" prefix (`env FOO=bar cmd`) — neither is a
#     full-environment dump.
#   - a `.env.example`/`.sample`/`.template`/`.dist` template file (mirrors
#     block-sensitive-staging.sh's Gate 1 allowlist).
#   - a `grep -c/-q/-l/-L` against a secret file — never prints matched
#     content.
#   - anything narrowed to presence/length only — `awk`/`wc`/`sha1sum`/
#     `sha256sum`/`sha512sum`/`md5sum` appearing in the SAME pipeline
#     sub-segment as a Category-A read (`cat file | sha256sum`), or
#     ANYWHERE in the whole remote command for Category B (a bare env
#     dump — needed so the gatekeeper-session-ops skill's own recommended
#     `... printenv | awk -F= '{print $1": len="length($2)}'` pattern,
#     which spans two pipeline sub-segments, still allows).
#   - `airuleset.py secret exec ...` — not cat/printenv/grep-shaped at all.
#
# Scoped to ssh-remote-command text ONLY (both real incidents are ssh-
# shaped) — a bare LOCAL `cat ~/.env` with no ssh wrapper is NOT covered;
# see the #373 design comment for why (a materially larger FP surface,
# not the incident class this ticket documents).
#
# KNOWN GAPS (adversarial review, #373 — same best-effort rigor as the
# rest of this hook, not a full shell parser):
#   - `docker compose exec -T svc cat/printenv` detection searches for the
#     command name ANYWHERE in the segment's tokens, so a flag VALUE that
#     happens to equal a bare `cat`/`printenv` token could theoretically
#     false-positive (contrived, not observed).
#   - Category B's (bare env dump) narrowing exemption is still WHOLE-
#     command-scoped (see above) — unlike Category A, which was tightened
#     to per-segment after live-reproducing that a benign `wc`/`awk`
#     ANYWHERE in an &&/;-joined command exempted an unrelated genuine
#     `cat mcp.env` read (#373-review MINOR). Category B keeps the wider
#     scope because its own legitimate flow needs it (see above); a
#     `wc -l x && printenv` would still slip Category B. Not a security
#     boundary; a mechanical backstop against the reflexive, ordinary-
#     looking shape that caused both real incidents.
#   - a hostile `awk '{print}'` that just echoes everything would still be
#     exempted by the narrowing check either way — it keys on the LITERAL
#     word, not on what the command actually does.
#   - `is_secret_path()`'s `credential`/`secret`/`token` substring/name-
#     component match (mirrors block-sensitive-staging.sh's own already-
#     accepted Gate-1 convention) over-blocks a genuinely non-secret file
#     whose name merely CONTAINS one of those words (e.g. a source file
#     `secrets_manager.py`, a log `token-service.log`, an AWS IAM
#     `credential-report.csv`) — loud (exit 2), one-step recoverable via
#     `# airuleset:secret-read-ok`, not tightened further per FREEZE
#     (no live incident of this specific false positive).
#   - grep's PATTERN argument (first non-flag token) is assumed to be a
#     search pattern, not a file — `grep -f patternfile secretfile` is not
#     specially handled.
#   - alternative read commands not in the enumerated set (od/xxd/base64/
#     dd/strings/hexdump/perl/python3 -c/`$(cat file)` substitution/
#     `find -exec cat {} \;`/`export -p`/`set`/`declare -x`/
#     `/proc/*/environ`) are NOT detected — a different, non-reflexive
#     habit than the cat/printenv incident class this ticket documents,
#     deliberately out of scope (this hook has never claimed to be a full
#     shell-semantics parser).
#
# Bypass (rare, user-instructed only, logged), NARROWER than the bypass
# below — suppresses ONLY this category, never the host-power-off/root-
# wipe/SQL-drop/GUI-hazard checks above: append
# '# airuleset:secret-read-ok <reason>' to the command.
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

# Bypass 3: inline '# airuleset:secret-read-ok <reason>' (#373) — NARROWER
# than Bypass 2 above: this only suppresses the secret-bearing-file-read /
# bare-env-dump checks below, never the host-power-off/root-wipe/SQL-drop/
# GUI-hazard checks — so it does NOT exit 0 here; it only sets a flag the
# python check reads. Same quote-stripping discipline as Bypass 2 (the
# marker must be OUTSIDE any quoted string).
SECRET_READ_BYPASS_REASON=$(printf '%s' "$INPUT" | python3 -c 'import re,sys
cmd=sys.stdin.read()
SQ=chr(39)
DQ=chr(34)
unquoted=re.sub(SQ+"[^"+SQ+"]*"+SQ, "", cmd)     # strip '"'"'...'"'"' spans
unquoted=re.sub(DQ+"[^"+DQ+"]*"+DQ, "", unquoted)  # strip "..." spans
m=None
for mm in re.finditer(r"#[ \t]*airuleset:secret-read-ok[ \t]+([^\n]+)", unquoted):
    m=mm
if m:
    print(m.group(1).rstrip())
' 2>/dev/null || echo "")

SECRET_READ_BYPASS=0
if [ -n "$SECRET_READ_BYPASS_REASON" ]; then
    SECRET_READ_BYPASS=1
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    mkdir -p "$(dirname "$AUDIT_LOG")"
    echo "$(date -Iseconds)  project=$PROJECT  secret-read-bypass  # airuleset:secret-read-ok $SECRET_READ_BYPASS_REASON" >> "$AUDIT_LOG"
fi

VIOLATION=$(python3 - "$INPUT" "${HOOK_CWD:-$PWD}" "$SECRET_READ_BYPASS" <<'PYEOF'
import json
import os
import re
import shlex
import sys

cmd = sys.argv[1]
SECRET_READ_BYPASS = len(sys.argv) > 3 and sys.argv[3] == "1"

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


# --- secret-bearing file / process-environment reads over ssh (#373) ------
ENV_TEMPLATE_SUFFIXES = ('.example', '.sample', '.template', '.dist')
TOKEN_NAME_RE = re.compile(r'(?i)(^|[_.\-])token([_.\-]|$)')
SECRETS_DIR_RE = re.compile(r'(?i)(^|/)\.?secrets?(/|$)')


def is_secret_path(token):
    """High-confidence secret-bearing-file heuristic — basename/path match
    only (there is no file to read from a PreToolUse hook). Mirrors
    block-sensitive-staging.sh's Gate 1 filename patterns (same repo,
    already-vetted convention), generalized from leading-dot-only (.env)
    to basename-anywhere (mcp.env) per #373's own real incident filename."""
    p = token.strip("'\"")
    if not p or p.startswith('-'):
        return False
    p = p.rstrip('/')
    if not p:
        return False
    base = p.rsplit('/', 1)[-1].lower()
    if not base:
        return False
    if base.endswith(ENV_TEMPLATE_SUFFIXES):
        return False
    if base == '.env' or base.endswith('.env') or re.search(r'\.env\.[^./]+$', base):
        return True
    if SECRETS_DIR_RE.search(p):
        return True
    if 'credential' in base or 'secret' in base:
        return True
    if TOKEN_NAME_RE.search(base):
        return True
    if base.endswith('.pem') or base.endswith('.key'):
        return True
    return False


STDOUT_REDIRECT_TOKENS = {'>', '1>', '>>', '1>>', '&>', '&>>', '>&'}
READ_FILE_CMDS = {'cat', 'less', 'more', 'head', 'tail'}
GREP_CMDS = {'grep', 'egrep', 'fgrep', 'zgrep'}
GREP_SAFE_FLAGS = {'-c', '--count', '-q', '--quiet', '-l', '--files-with-matches',
                    '-L', '--files-without-match'}
SAFE_NARROW_RE = re.compile(r'\b(?:awk|wc|sha1sum|sha256sum|sha512sum|md5sum)\b')


def _cmd_read_targets(args):
    """args = tokens AFTER a command name. Returns (read_targets,
    stdout_redirected). read_targets are files the command actually reads
    (positional args, plus a `<` input-redirect target); stdout_redirected
    means a `>`/`>>`/`&>` sent stdout to a file — nothing reaches the
    transcript regardless of content, so callers must skip flagging then."""
    targets = []
    redirected = False
    i, n = 0, len(args)
    while i < n:
        t = args[i]
        if t in STDOUT_REDIRECT_TOKENS:
            redirected = True
            i += 2
            continue
        if t == '<':
            if i + 1 < n:
                targets.append(args[i + 1])
            i += 2
            continue
        if re.match(r'^2>>?$', t):
            i += 2
            continue
        if not t.startswith('-'):
            targets.append(t)
        i += 1
    return targets, redirected


def _leaky_file_reads(tokens):
    """Scan one pipeline sub-segment's tokens for a cat/less/head/tail/grep
    call whose FILE argument matches a secret-file pattern with nothing
    redirecting its stdout away from the transcript. Searches for the
    command name ANYWHERE in tokens (not just position 0) so a wrapper
    like `docker compose exec -T svc cat file` is still caught — the
    gatekeeper/subdev flows #373 documents routinely wrap reads this way."""
    hits = []
    for i, t in enumerate(tokens):
        name = t.rsplit('/', 1)[-1].lower()
        if name in READ_FILE_CMDS:
            targets, redirected = _cmd_read_targets(tokens[i + 1:])
            if redirected:
                continue
            for tgt in targets:
                if is_secret_path(tgt):
                    hits.append(
                        name + " of a secret-bearing file over ssh — its "
                        "content leaks into the transcript: "
                        + " ".join(tokens[i:])[:120]
                    )
                    break
        elif name in GREP_CMDS:
            rest = tokens[i + 1:]
            if any(f in GREP_SAFE_FLAGS for f in rest):
                continue
            targets, redirected = _cmd_read_targets(rest)
            if redirected or not targets:
                continue
            # first non-flag positional arg is the PATTERN, not a file —
            # only the remaining ones are files grep will actually read.
            for tgt in targets[1:]:
                if is_secret_path(tgt):
                    hits.append(
                        name + " against a secret-bearing file over ssh — "
                        "the matching line (incl. its value) leaks into "
                        "the transcript: " + " ".join(tokens[i:])[:120]
                    )
                    break
    return hits


def _bare_env_dump(seg_text):
    """Category B: a BARE printenv/env invocation (no specific var-name
    argument — a genuine full-environment dump), e.g. wrapped in `docker
    compose exec -T svc printenv`. A named var (`printenv PATH`) or `env`
    used as a command-prefix (`env FOO=bar cmd`) is NOT a dump and is left
    alone — this is #373's own incident shape (`docker compose exec -T mcp
    printenv | grep -E '^MCP_'`): even a downstream grep still prints full
    KEY=value lines (still a leak) unless narrowed to presence/length only,
    so ANY bare dump is flagged regardless of what follows it (narrowing
    is checked once, by the caller, over the whole seg_text)."""
    for inner in split_segments(seg_text):
        tk = tokens_of(inner)
        for i, t in enumerate(tk):
            name = t.rsplit('/', 1)[-1].lower()
            if name in ('printenv', 'env'):
                rest = tk[i + 1:]
                # any NON-flag token after the command name means a
                # specific var name (printenv PATH) or a command to run
                # under a modified env (env FOO=bar cmd) — not a bare dump.
                if any(not r.startswith('-') for r in rest):
                    continue
                return True
    return False


def check_remote_segment(seg_text):
    """Checks that ONLY apply once we know we're inside a remote (ssh) context."""
    hits = []
    # #373-review MINOR: Category A's narrowing is scoped to THIS
    # pipeline sub-segment only, never the whole remote command — a
    # benign `wc`/`awk` in an UNRELATED &&/;-joined segment (e.g.
    # `wc -l mcp.env && cat mcp.env`) must never exempt a genuine `cat
    # mcp.env` read elsewhere in the same command; that would defeat the
    # exact incident shape this ticket exists to block. A real narrowing
    # consumer for a direct read (`cat file | sha256sum`) sits in the
    # SAME segment text as the read (awk operating on the file directly
    # isn't in READ_FILE_CMDS/GREP_CMDS at all, so it never needed the
    # wider scope). Category B keeps whole-command narrowing — see below.
    narrowed_whole = bool(SAFE_NARROW_RE.search(seg_text))
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
        if not SECRET_READ_BYPASS and not SAFE_NARROW_RE.search(inner):
            hits.extend(_leaky_file_reads(tk))
    # Category B (bare env dump) keeps WHOLE-command narrowing, on
    # purpose: its own legitimate narrow flow (`printenv | awk ...`,
    # tested) spans TWO pipeline sub-segments — narrowing this per-
    # segment would false-block that documented ALLOW case. KNOWN GAP
    # (same #373-review finding, deliberately left open here): an
    # unrelated wc/awk/sha*sum ANYWHERE in the remote command still
    # exempts a genuine bare env dump too — a mechanical backstop, not a
    # security boundary (this file's own long-standing framing).
    if not SECRET_READ_BYPASS and not narrowed_whole and _bare_env_dump(seg_text):
        hits.append(
            "bare printenv/env dump over ssh — every var VALUE leaks into "
            "the transcript: " + seg_text.strip()[:120]
        )
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
        # matches the FIXED phrase every secret-read/env-dump hit shares
        # (#373) — never a bare "leaks" substring a decoy comment could
        # coincidentally embed.
        *"leaks into the transcript"*)
            echo "  Secret-bearing read over ssh (#373): a Bash tool call's" >&2
            echo "  stdout is captured VERBATIM into the session transcript —" >&2
            echo "  it survives compaction and cannot be revoked. Use a" >&2
            echo '  presence/length-only check instead, e.g.:' >&2
            echo '    ssh host "awk -F= '"'"'{print \$1\": len=\"length(\$2)}'"'"' <file>"' >&2
            echo "  or a single named var via 'airuleset.py secret exec'." >&2
            echo "  Writing content INTO a remote destination (\`cat" >&2
            echo "  secretfile | ssh host \"cat > remote-file\"\`) is NOT" >&2
            echo "  blocked — only a read whose output would print." >&2
            echo "" >&2
            ;;
    esac
    echo "  Bypass (rare, user-instructed only, logged): append" >&2
    echo "  '# airuleset:destructive-ok <reason>' to the command, or set" >&2
    echo "  AIRULESET_ALLOW_DESTRUCTIVE_REMOTE=1. For the secret-read" >&2
    echo "  category ONLY, the narrower '# airuleset:secret-read-ok" >&2
    echo "  <reason>' marker also works." >&2
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
