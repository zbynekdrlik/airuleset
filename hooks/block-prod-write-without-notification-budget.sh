#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — issue #979.
# Blocks PROD Odoo DATA WRITE shapes unless a notification-budget file
# exists for the target prod host and today's date.
#
# WRITE shapes detected (owner hard rule 2026-09-10, odoo-erp #6084):
#   - ssh to a `*-prod` alias / host with a data-mutating remote command
#     (docker compose exec … odoo shell/python3, psql INSERT/UPDATE,
#     python3 -c with xmlrpc/execute_kw, or an import/load script).
#   - curl/wget POST to `/json/2/<model>/create` or `/json/2/<model>/write`
#     against a `*-prod` host.
#   - psql INSERT/UPDATE with -h/--host naming a `*-prod` host.
#   - docker compose exec … odoo shell/python3 inside an ssh to a prod host.
#
# READ shapes are ALWAYS allowed (untouched):
#   - SELECT, search_read, read, search_count, name_search, fields_get.
#
# OUT OF SCOPE (documented, not a gap):
#   - HTTP/local-xmlrpc writes to non-`*-prod` hostnames (e.g.
#     erp.montalu.cloud) — those use a FQDN, not an ssh alias; the
#     odoo-erp lane's own preflight gate covers them.
#   - DELETE/unlink — intentionally excluded; the notification-storm risk
#     is on CREATE/WRITE paths, not deletions.
#
# Prod-host detection rule: any host/alias whose name ends in `-prod`
# followed by end-of-string or a dot (the Odoo ssh alias convention:
# montalu-prod, miva-prod). Does NOT match `-prod-copy` (a copy host).
#
# Budget file host key = the EXACT ssh alias / URL hostname as typed
# (user@montalu-prod → montalu-prod; montalu-prod.example.com stays FQDN).
# The odoo-erp lane's `prod_write_preflight.py` must write the same key.
#
# Budget file contract:
#   ~/.claude/prod-write-budget/<host>-<YYYYMMDD>.json
#   Required keys: mail_mail, mail_notification, mail_message, mail_activity
#   (expected deltas, integer values).
#   Required: copy_run (non-empty string — the reference to the copy run
#   where these deltas were computed).
#
# Bypass: `# airuleset:prod-write-ok <budget-file-path>` trailing the
# command — the path argument is MANDATORY (a bare marker without a path
# is rejected). Logged to audits/prod-write-budget-bypasses.log.
#
# Exit code 2 = block the tool call.

# ---------- input parsing (same shape as block-destructive-remote.sh) -----

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
[ -z "$INPUT" ] && INPUT="$PAYLOAD"

[ -z "$INPUT" ] && exit 0

AUDIT_LOG="$HOME/devel/airuleset/audits/prod-write-budget-bypasses.log"

# ---------- bypass: # airuleset:prod-write-ok <budget-file-path> ----------
# The marker must be OUTSIDE any quoted string (same quote-stripping as
# block-destructive-remote.sh). The path argument is MANDATORY.

BYPASS_PATH=$(printf '%s' "$INPUT" | python3 -c 'import re,sys
cmd=sys.stdin.read()
SQ=chr(39)
DQ=chr(34)
unquoted=re.sub(SQ+"[^"+SQ+"]*"+SQ, "", cmd)
unquoted=re.sub(DQ+"[^"+DQ+"]*"+DQ, "", unquoted)
m=None
for mm in re.finditer(r"#[ \t]*airuleset:prod-write-ok[ \t]+(\S+)", unquoted):
    m=mm
if m:
    print(m.group(1).rstrip())
' 2>/dev/null || echo "")

if [ -n "$BYPASS_PATH" ]; then
    # Validate that the bypass path points to a real, valid budget file
    BYPASS_VALID=$(python3 -c "
import json, sys, os
p = os.path.expanduser(sys.argv[1])
if not os.path.exists(p):
    print('INVALID: file does not exist: ' + p)
    sys.exit(0)
try:
    data = json.load(open(p))
except Exception as e:
    print('INVALID: malformed JSON: ' + str(e))
    sys.exit(0)
for k in ('mail_mail','mail_notification','mail_message','mail_activity'):
    if k not in data:
        print('INVALID: missing key ' + k)
        sys.exit(0)
cr = data.get('copy_run','')
if not cr or not str(cr).strip():
    print('INVALID: empty copy_run')
    sys.exit(0)
print('OK')
" "$BYPASS_PATH" 2>/dev/null || echo "INVALID: python3 error")

    if [ "$BYPASS_VALID" = "OK" ]; then
        PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
        mkdir -p "$(dirname "$AUDIT_LOG")"
        echo "$(date -Iseconds)  project=$PROJECT  inline-bypass  # airuleset:prod-write-ok $BYPASS_PATH" >> "$AUDIT_LOG"
        exit 0
    else
        # The path was given but is not a valid budget file — do NOT bypass,
        # fall through to the normal classification (which will block if needed).
        true
    fi
fi

# ---------- main classifier (python3) ------------------------------------

VIOLATION=$(python3 - "$INPUT" <<'PYEOF'
import re
import sys
import json
import os
from datetime import date

cmd = sys.argv[1]
if not cmd.strip():
    sys.exit(0)

# ---- helpers ----

def is_prod_host(host):
    """True if the host/alias ends with -prod (the Odoo ssh alias convention).
    Does NOT match -prod-copy or -prod-staging (those are copy/staging hosts)."""
    return bool(re.search(r'-prod(\.|$)', host, re.IGNORECASE))

def _split_segments(cmd):
    """Split a command on unquoted &&, ;, |, and newlines into segments.
    Same concept as block-destructive-remote.sh's split_segments."""
    segments = []
    cur = []
    in_sq = False
    in_dq = False
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if ch == "'" and not in_dq:
            in_sq = not in_sq
            cur.append(ch)
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
            cur.append(ch)
        elif not in_sq and not in_dq:
            if ch == '&' and i + 1 < len(cmd) and cmd[i + 1] == '&':
                segments.append(''.join(cur))
                cur = []
                i += 2
                continue
            elif ch in (';', '|', '\n'):
                segments.append(''.join(cur))
                cur = []
            else:
                cur.append(ch)
        else:
            cur.append(ch)
        i += 1
    if cur:
        segments.append(''.join(cur))
    return [s.strip() for s in segments if s.strip()]

def _strip_prefix(segment):
    """Strip sudo, env VAR=val, sshpass, time, nice, timeout, cd ... &&
    prefixes from a segment. Same concept as block-destructive-remote.sh's
    strip_prefix."""
    s = segment.strip()
    changed = True
    while changed:
        changed = False
        for pat in (r'^sudo\s+', r'^env\s+\S+=\S+\s+', r'^sshpass\s+\S+\s+',
                    r'^time\s+', r'^nice\s+(?:-n\s+\d+\s+)?',
                    r'^ionice\s+(?:-c\s+\d+\s+)?', r'^timeout\s+\d+\s+',
                    r'^command\s+', r'^nohup\s+'):
            m = re.match(pat, s)
            if m:
                s = s[m.end():].strip()
                changed = True
        # Strip leading VAR=val tokens
        m = re.match(r'^[A-Za-z_][A-Za-z_0-9]*=\S*\s+', s)
        if m:
            s = s[m.end():].strip()
            changed = True
    return s

def extract_ssh_target(segment):
    """Extract the ssh target host from a SINGLE segment (already prefix-
    stripped). Returns (host, remote_cmd) or (None, None)."""
    c = segment.strip()
    m = re.match(r'ssh\s+', c)
    if not m:
        return None, None
    rest = c[m.end():]
    tokens = _shell_split(rest)
    i = 0
    host = None
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith('-'):
            if tok in ('-i', '-o', '-p', '-l', '-F', '-J', '-W', '-w',
                       '-b', '-c', '-D', '-e', '-I', '-L', '-m', '-O',
                       '-Q', '-R', '-S', '-E'):
                i += 2
                continue
            i += 1
            continue
        host = tok
        i += 1
        break
    if not host:
        return None, None
    if '@' in host:
        host = host.split('@', 1)[1]
    remote_cmd = ' '.join(tokens[i:])
    if remote_cmd.startswith('"') and remote_cmd.endswith('"'):
        remote_cmd = remote_cmd[1:-1]
    elif remote_cmd.startswith("'") and remote_cmd.endswith("'"):
        remote_cmd = remote_cmd[1:-1]
    return host, remote_cmd

def _shell_split(s):
    """Simple quote-aware tokeniser: splits on unquoted whitespace, keeps
    quoted segments as single tokens (with quotes preserved)."""
    tokens = []
    cur = []
    in_sq = False
    in_dq = False
    for ch in s:
        if ch == "'" and not in_dq:
            in_sq = not in_sq
            cur.append(ch)
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
            cur.append(ch)
        elif ch in (' ', '\t') and not in_sq and not in_dq:
            if cur:
                tokens.append(''.join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append(''.join(cur))
    return tokens

def has_write_shape(text):
    """True if the text contains a WRITE shape."""
    t = text.lower()
    if re.search(r'docker\s+compose\s+exec\b.*\bodoo\s+(shell|python3?)\b', t):
        return True
    if re.search(r'execute_kw\b.*\b(create|write|unlink)\b', t):
        return True
    if re.search(r'\bpsql\b', t) and re.search(r'\b(INSERT|UPDATE)\b', text, re.IGNORECASE):
        return True
    # python3 with a data-mutating Odoo method — but NOT bare 'import sys'
    if re.search(r'\bpython3?\b.*\b(create|write|approve|confirm)\b', t):
        return True
    # Script names suggesting data import/load — but only .py/.sh scripts
    # that START with the import/load verb (not any script with 'import' in it)
    if re.search(r'\b(import|load|migrate|seed|sync)_[\w]*\.(py|sh)\b', t):
        return True
    return False

def has_read_shape(text):
    """True if the text contains a READ shape."""
    t = text.lower()
    if re.search(r'\bpsql\b', t) and re.search(r'\bSELECT\b', text, re.IGNORECASE):
        return True
    if re.search(r'\b(search_read|fields_get|search_count|name_search)\b', t):
        return True
    if re.search(r'execute_kw\b.*\b(read|search|search_read|fields_get)\b', t):
        return True
    return False

def is_write_not_read(text):
    """True if the text has a write shape AND does NOT consist solely of
    reads. A script that does BOTH read and write is a write (MAJOR 2 fix:
    the read classifier must NOT short-circuit a write)."""
    if not has_write_shape(text):
        return False
    # If it has ONLY read shapes and no write shapes in the Odoo/psql sense,
    # it's a read. But we already know has_write_shape is True, so it's a write.
    return True

def is_pure_read(text):
    """True if the command has ONLY read shapes and NO write shapes."""
    if has_write_shape(text):
        return False
    if has_read_shape(text):
        return True
    # psql SELECT-only
    t = text.lower()
    if re.search(r'\bpsql\b', t) and re.search(r'\bSELECT\b', text, re.IGNORECASE) \
       and not re.search(r'\b(INSERT|UPDATE|DELETE)\b', text, re.IGNORECASE):
        return True
    return False

def extract_curl_prod_host(cmd):
    """If the command is a curl/wget to /json/2/<model>/create|write
    against a *-prod host, return the host. Else None."""
    m = re.search(
        r'(?:curl|wget)\b.*https?://([^\s/:]+)'
        r'[^\s]*/json(?:rpc)?/2/[^\s/]+/(?:create|write)',
        cmd, re.IGNORECASE
    )
    if m:
        host = m.group(1)
        if is_prod_host(host):
            return host
    return None

def extract_psql_host(cmd):
    """Extract the host from a psql -h/--host command."""
    m = re.search(r'\bpsql\b.*(?:-h\s+|--host[=\s]+)(\S+)', cmd)
    if m:
        return m.group(1)
    return None

def check_budget_file(host):
    """Check if a valid budget file exists for this host and today.
    Returns (ok, reason)."""
    today = date.today().strftime('%Y%m%d')
    host_clean = re.sub(r'[:\.]$', '', host).strip()
    budget_dir = os.path.expanduser('~/.claude/prod-write-budget')
    budget_path = os.path.join(budget_dir, f'{host_clean}-{today}.json')
    if not os.path.exists(budget_path):
        return False, f'no budget file for host={host_clean} date={today} (expected {budget_path})'
    try:
        with open(budget_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return False, f'budget file {budget_path} is malformed: {e}'
    required_keys = ['mail_mail', 'mail_notification', 'mail_message', 'mail_activity']
    missing = [k for k in required_keys if k not in data]
    if missing:
        return False, f'budget file {budget_path} missing keys: {", ".join(missing)}'
    copy_run = data.get('copy_run', '')
    if not copy_run or not str(copy_run).strip():
        return False, f'budget file {budget_path} has empty copy_run reference'
    return True, ''

# ---- main classification ----

violations = []

# Split on unquoted && ; | newlines, strip prefixes per segment
for segment in _split_segments(cmd):
    seg = _strip_prefix(segment)

    # Shape 1: ssh to a *-prod host with a write command
    ssh_target, remote_cmd = extract_ssh_target(seg)
    if ssh_target and is_prod_host(ssh_target):
        if remote_cmd and not is_pure_read(remote_cmd) and is_write_not_read(remote_cmd):
            ok, reason = check_budget_file(ssh_target)
            if not ok:
                violations.append(f'PROD write via ssh to {ssh_target}: {reason}')

    # Shape 2: curl/wget POST to /json/2/<model>/create|write on a *-prod host
    curl_host = extract_curl_prod_host(seg)
    if curl_host:
        ok, reason = check_budget_file(curl_host)
        if not ok:
            violations.append(f'PROD write via HTTP to {curl_host}: {reason}')

    # Shape 3: psql INSERT/UPDATE with -h *-prod
    psql_host = extract_psql_host(seg)
    if psql_host and is_prod_host(psql_host):
        if re.search(r'\b(INSERT|UPDATE)\b', seg, re.IGNORECASE):
            ok, reason = check_budget_file(psql_host)
            if not ok:
                violations.append(f'PROD write via psql to {psql_host}: {reason}')

if violations:
    print('\n'.join(violations))
    sys.exit(2)

sys.exit(0)
PYEOF
) || RC=$?
RC=${RC:-0}

if [ "$RC" -eq 2 ]; then
    echo "" >&2
    echo "BLOCKED: PROD data write without a notification budget." >&2
    echo "" >&2
    echo "  $VIOLATION" >&2
    echo "" >&2
    echo "  Every PROD data change is a potential notification storm from record two." >&2
    echo "  Compute the budget on the COPY first:" >&2
    echo "    ~/.claude/prod-write-budget/<host>-<YYYYMMDD>.json" >&2
    echo "  with keys: mail_mail, mail_notification, mail_message, mail_activity + copy_run." >&2
    echo "" >&2
    echo "  Bypass (logged): append '# airuleset:prod-write-ok <budget-file-path>'" >&2
    echo "" >&2
    exit 2
elif [ "$RC" -ne 0 ]; then
    echo "" >&2
    echo "BLOCKED (fail-closed): block-prod-write-without-notification-budget.sh" >&2
    echo "  internal error — python3 exited $RC instead of running the check." >&2
    echo "  $VIOLATION" >&2
    echo "" >&2
    exit 2
fi

exit 0
