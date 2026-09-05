#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — issue #51.
#
# Incident (2026-07-25, TWICE in one day): ad-hoc ssh probes against the
# subdev VPS with GUESSED identities (default key as newlevel@/root@, a bare
# `ssh subdev` implying the current shell user) each tripped fail2ban and
# banned dev1's source IP for 1h+ on ALL interfaces (tailscale AND public),
# breaking every subdev deploy target until the ban expired. The prose
# dev-rule ("NEVER probe subdev ssh with guessed users/keys") did not stop a
# SECOND occurrence the same day — this is the mechanical backstop.
#
# Trigger: the command is an ssh/scp/rsync/sftp invocation (directly, or
# sshpass-wrapped) whose target host is one of the subdev VPS's known
# addresses (MagicDNS name, public FQDN, tailscale IP, public IP).
#
# ALLOW-list — mirrors airuleset.py's REMOTE_HOSTS for the montalu family
# montalu1/2/3/4/5/6/7/8 (airuleset#251 + airuleset#378 + #537 base rename
# montalu->montalu1)/marek/david1 (+ its family david2/3/4, airuleset#326 +
# #537 base rename david->david1)/simap1/miva1 (airuleset#300 — the single
# source of truth for THOSE fifteen identities), PLUS one identity
# REMOTE_HOSTS structurally cannot express (#68): REMOTE_HOSTS is dev1's own
# OUTBOUND push-target list, but the gatekeeper VPS reaches subdev INBOUND
# from ITS OWN box as root, via a locally-deployed ~/.ssh/config `Host
# subdev` stanza — not something dev1 ever pushes to.
#   - montalu[12345678]@<subdev> — no identity requirement (default key AND
#                          the sshpass -p path are both legitimate per
#                          REMOTE_HOSTS — the montalu family has no
#                          `identity` entry; montalu1 is the renamed base
#                          (#537, was `montalu`), montalu2/3/4 are three MORE
#                          full parallel montalu streams (airuleset#251),
#                          montalu5/6/7/8 are FOUR MORE (airuleset#378,
#                          odoo-erp#3642)).
#   - marek@<subdev>     — ONLY with -i .../gatekeeper_access_ed25519.
#   - david[1234]@<subdev> — ONLY with -i .../gatekeeper_access_ed25519
#                          (david1 is the renamed base (#537, was `david` —
#                          the OS account `david` is GONE), david2/3/4 are its
#                          family, airuleset#326 — three MORE parallel david
#                          streams, additional capacity for the same external
#                          developer, so they share the same operator
#                          identity requirement).
#   - simap1@<subdev>     — ONLY with -i .../gatekeeper_access_ed25519
#                          (airuleset#143 — simap's authorized_keys are the
#                          SAME operator keys as marek, so it shares marek's
#                          identity requirement, not montalu's default-key
#                          path).
#   - miva1@<subdev>     — ONLY with -i .../gatekeeper_access_ed25519
#                          (airuleset#300 — phase-1 isolated, same shape as
#                          simap, so it shares the identical identity
#                          requirement, not montalu's default-key path).
#   - root@<subdev>      — ONLY with -i .../subdev_admin (#68, gatekeeper
#                          VPS's own admin identity) — explicit on the
#                          command line, OR implicit via a bare `ssh subdev`
#                          whose LOCAL ~/.ssh/config `Host subdev` stanza
#                          itself resolves to User root + that identity
#                          (read at hook-execution time, never guessed).
# BLOCK everything else, in particular:
#   - no user at all UNLESS the local ~/.ssh/config resolves it to the
#     sanctioned root+subdev_admin case above.
#   - any user other than montalu[12345678]/marek/david[1234]/simap1/miva1/root
#     (newlevel, gatekeeper,...).
#   - marek/david[1234]/simap1/miva1 WITHOUT the gatekeeper_access_ed25519 identity.
#   - root WITHOUT the subdev_admin identity.
#
# A non-subdev target (dev2, gatekeeper, anything else) is completely
# untouched by this hook — it only ever looks at the 4 subdev addresses.
#
# KNOWN GAPS (best-effort, not a full shell parser — same rigor level as
# block-destructive-remote.sh / block-sensitive-staging.sh):
#   - An identity supplied only inside an rsync `-e "ssh -i ..."` transport
#     string is best-effort recovered (regex over that token's text), not a
#     full re-parse of the embedded command.
#   - A destination reached through a wrapper script (that itself shells out
#     to ssh) is invisible to argv-level matching.
#
# Bypass (rare, user-instructed only, logged): append
# '# airuleset:subdev-ssh-ok <reason>' to the command — the marker must be
# OUTSIDE any quoted string (quoted spans are stripped before the marker
# search, so the marker merely being MENTIONED inside a commit-message body
# or an echo does not bypass a real violation elsewhere on the same line —
# same class of fix as block-destructive-remote.sh / block-sensitive-
# staging.sh, test_block_staged_content_values.py).
#
# Exit code 2 = block the tool call.

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
[ -z "$INPUT" ] && INPUT="$PAYLOAD"

[ -z "$INPUT" ] && exit 0

AUDIT_LOG="$HOME/devel/airuleset/audits/subdev-ssh-bypasses.log"

# Bypass: inline '# airuleset:subdev-ssh-ok <reason>' trailing the command.
# Quoted spans are stripped FIRST (a real bash `#` only starts a comment
# outside quotes) — see the header comment above.
BYPASS_REASON=$(printf '%s' "$INPUT" | python3 -c 'import re,sys
cmd=sys.stdin.read()
SQ=chr(39)
DQ=chr(34)
unquoted=re.sub(SQ+"[^"+SQ+"]*"+SQ, "", cmd)     # strip '"'"'...'"'"' spans
unquoted=re.sub(DQ+"[^"+DQ+"]*"+DQ, "", unquoted)  # strip "..." spans
m=None
for mm in re.finditer(r"#[ \t]*airuleset:subdev-ssh-ok[ \t]+([^\n]+)", unquoted):
    m=mm
if m:
    print(m.group(1).rstrip())
' 2>/dev/null || echo "")

if [ -n "$BYPASS_REASON" ]; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    mkdir -p "$(dirname "$AUDIT_LOG")"
    echo "$(date -Iseconds)  project=$PROJECT  inline-bypass  # airuleset:subdev-ssh-ok $BYPASS_REASON" >> "$AUDIT_LOG"
    exit 0
fi

VIOLATION=$(python3 - "$INPUT" <<'PYEOF'
import os
import re
import shlex
import sys

cmd = sys.argv[1]

# The 4 known addresses of the subdev VPS (machine-identities.md) — MagicDNS
# name, public FQDN, tailscale IP, public IP. Exact match only, never a
# substring (so e.g. "subdev-scratch" does NOT match "subdev").
SUBDEV_ADDRS = {"subdev", "subdev.newlevel.media",
                "100.118.174.27", "116.203.108.177"}
SSH_LIKE = {"ssh", "scp", "rsync", "sftp"}
GATEKEEPER_KEY_BASENAME = "gatekeeper_access_ed25519"
# The gatekeeper VPS's OWN sanctioned admin identity for reaching subdev as
# root (#68) — its deployed ~/.ssh/config carries `Host subdev { User root;
# IdentityFile ~/.ssh/subdev_admin; IdentitiesOnly yes }`, the normal path
# `process-subdev`'s bounce-nudge uses. Distinct from GATEKEEPER_KEY_BASENAME
# (the marek/david1-family operator identity) — root@subdev uses its OWN key,
# never that one.
SUBDEV_ADMIN_KEY_BASENAME = "subdev_admin"

# --- #869: read the subdev accounts conf rendered by cmd_install ---
# Format: user<TAB>key-basename per line.  Falls back to hardcoded when
# the conf is absent (fail-safe: never "allow all").
_SUBDEV_CONF = os.path.expanduser("~/.claude/airuleset-subdev-accounts.conf")
_SUBDEV_ACCOUNTS = {}  # user -> key-basename
try:
    if os.path.isfile(_SUBDEV_CONF):
        with open(_SUBDEV_CONF, "r") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#"):
                    continue
                _parts = _line.split("\t", 1)
                if len(_parts) == 2:
                    _SUBDEV_ACCOUNTS[_parts[0]] = _parts[1]
except OSError:
    pass

# Hardcoded fallback — MUST match SUBDEV_ACCOUNTS_FALLBACK in
# cli_webterm_only.py (test-locked). Used when conf absent AND as the
# CEILING: conf can only NARROW (match/subset), never WIDEN (#869 review).
_SUBDEV_ACCOUNTS_HARDCODED = {
    "montalu1": "default", "montalu2": "default",
    "montalu3": "default", "montalu4": "default",
    "montalu5": "default", "montalu6": "default",
    "montalu7": "default", "montalu8": "default",
    # marek REMOVED (#882, 2026-09-05: stream decommissioned)
    "david1": "gatekeeper_access_ed25519",
    "david2": "gatekeeper_access_ed25519",
    "david3": "gatekeeper_access_ed25519",
    "david4": "gatekeeper_access_ed25519",
    "dominika": "gatekeeper_access_ed25519",
    "simap1": "gatekeeper_access_ed25519",
    "miva1": "gatekeeper_access_ed25519",
}
if not _SUBDEV_ACCOUNTS:
    _SUBDEV_ACCOUNTS = dict(_SUBDEV_ACCOUNTS_HARDCODED)
else:
    # INTERSECT with hardcoded: conf can only match/narrow, never widen.
    # A conf user NOT in hardcoded is dropped. A conf key_req weaker than
    # hardcoded (default < gatekeeper) is overridden to the hardcoded req.
    _safe = {}
    for _u, _k in _SUBDEV_ACCOUNTS.items():
        if _u not in _SUBDEV_ACCOUNTS_HARDCODED:
            continue  # conf cannot ADD users
        _hk = _SUBDEV_ACCOUNTS_HARDCODED[_u]
        # "default" is weaker than any named key — take the STRONGER
        if _k == "default" and _hk != "default":
            _safe[_u] = _hk  # override conf's weak "default"
        else:
            _safe[_u] = _k
    _SUBDEV_ACCOUNTS = _safe

ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

# ssh/scp/rsync/sftp flags that take a SEPARATE-token value (so the walk that
# looks for the positional target/host skips the value too, not just the
# flag). A FUSED flag+value (`-i/path`, `-p2222`) is already a single token
# and needs no special handling here.
VALUE_FLAGS = {"-i", "-o", "-p", "-l", "-F", "-J", "-L", "-R", "-D",
              "-W", "-B", "-b", "-c", "-m", "-e", "-Q", "-S", "-P"}


def split_segments(text):
    """Quote-AWARE split on shell separators (&&, ||, ;, |, &, newline) —
    never splits INSIDE a quoted string (mirrors block-destructive-
    remote.sh's identical helper)."""
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
    while i < len(tk) and (tk[i] in ("sudo", "env", "time", "nice", "ionice")
                            or ASSIGN_RE.match(tk[i])):
        i += 1
    return tk[i:]


def identity_values(tokens):
    """Every -i / fused -i<path> value among `tokens`, PLUS a best-effort
    recovery of an identity mentioned inside an embedded `-e "ssh -i ..."`
    rsync transport string (see the KNOWN GAPS header comment)."""
    out = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "-i" and i + 1 < len(tokens):
            out.append(tokens[i + 1])
            i += 2
            continue
        if t.startswith("-i") and len(t) > 2 and not t.startswith("--"):
            out.append(t[2:])
            i += 1
            continue
        m = re.search(r'-i\s+(\S+)', t)
        if m and "ssh" in t:
            out.append(m.group(1))
        i += 1
    return out


def has_identity(tokens, basename):
    for v in identity_values(tokens):
        v = v.strip().strip('"').strip("'").rstrip("/")
        if os.path.basename(v) == basename:
            return True
    return False


def has_gatekeeper_key(tokens):
    return has_identity(tokens, GATEKEEPER_KEY_BASENAME)


def _ssh_config_path():
    home = os.environ.get("HOME", "")
    return os.path.join(home, ".ssh", "config") if home else ""


def _resolve_ssh_config_host(alias):
    """Best-effort ~/.ssh/config lookup (#68) for an EXACT `Host <alias>`
    stanza — no wildcard/Match/Include support, same best-effort rigor the
    file's own KNOWN GAPS header already declares for the rsync `-e` case.
    Returns (user_or_None, identityfile_or_None) from the block whose `Host`
    line is LITERALLY just `alias` (a multi-pattern or globbed Host line is
    never treated as a match). (None, None) on no config / no match /
    unreadable file. Read at HOOK-EXECUTION time (never baked into the
    allow-list) so it reflects whatever is actually deployed on the box the
    command runs from — dev1 has no such `Host subdev` stanza, so this never
    affects the existing dev1 behavior."""
    path = _ssh_config_path()
    if not path or not os.path.isfile(path):
        return None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None, None
    in_block = False
    user = None
    identity = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, val = parts[0].lower(), parts[1].strip()
        if key == "host":
            in_block = (val.split() == [alias])
            if in_block:
                user = None
                identity = None
            continue
        if not in_block:
            continue
        if key == "user":
            user = val
        elif key == "identityfile":
            identity = val
    return user, identity


def resolve_target(host_tok):
    """[user@]host -> (user_or_None, host)."""
    if "@" in host_tok:
        user, _, host = host_tok.rpartition("@")
        return user, host
    return None, host_tok


def positional_target(tk):
    """tk = tokens STARTING AT the command name (ssh/sftp). Walks past
    flags to the first non-flag token — the ssh/sftp positional
    [user@]host target. A trailing ':...' (sftp allows a starting remote
    path directly on the host token) is stripped."""
    i = 1
    while i < len(tk):
        t = tk[i]
        if t.startswith("-") and t != "-":
            if t in VALUE_FLAGS and i + 1 < len(tk):
                i += 2
                continue
            i += 1
            continue
        host_tok = t.split(":", 1)[0]
        return resolve_target(host_tok)
    return None, None


REMOTE_SPEC_RE = re.compile(r'^(?:([^@:\s]+)@)?([^@:\s]+):')


def remote_spec_targets(tk):
    """scp/rsync/sftp positional [user@]host:path args — ANY token
    matching the shape, not just the first/last (scp -3 has two)."""
    out = []
    for t in tk[1:]:
        if t.startswith("-"):
            continue
        m = REMOTE_SPEC_RE.match(t)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def check_target(user, host, tokens, label):
    if host.lower() not in SUBDEV_ADDRS:
        return None
    if user is None:
        # #68: a bare `ssh subdev` with no explicit user relies entirely on
        # the box's own ~/.ssh/config `Host subdev` stanza (the real
        # process-subdev nudge shape) — allow it ONLY when that stanza
        # itself resolves to root + the subdev_admin identity, never as a
        # blanket "no user = fine".
        cfg_user, cfg_identity = _resolve_ssh_config_host(host)
        if (cfg_user == "root" and cfg_identity and
                os.path.basename(cfg_identity.strip().strip('"').strip("'")
                                 .rstrip("/")) == SUBDEV_ADMIN_KEY_BASENAME):
            return None
        return ("%s to subdev with NO user specified (implicit current "
                "shell user) — must be a registered subdev account "
                "(see ~/.claude/airuleset-subdev-accounts.conf)"
                % label)
    if user == "root":
        # #68: the gatekeeper VPS's own sanctioned root@subdev identity.
        if has_identity(tokens, SUBDEV_ADMIN_KEY_BASENAME):
            return None
        return ("%s as root@subdev without -i .../%s"
                % (label, SUBDEV_ADMIN_KEY_BASENAME))
    # #869: derive the allow-list from the conf (or hardcoded fallback)
    if user not in _SUBDEV_ACCOUNTS:
        return "%s as unauthorized user '%s'@subdev" % (label, user)
    key_req = _SUBDEV_ACCOUNTS[user]
    if key_req == "default":
        # montalu family — no identity requirement
        return None
    # Requires the gatekeeper key (marek/david1-4/dominika/simap1/miva1)
    if has_gatekeeper_key(tokens):
        return None
    return ("%s as %s@subdev without -i .../%s"
            % (label, user, GATEKEEPER_KEY_BASENAME))


def check_segment(segment):
    tk = strip_prefix(tokens_of(segment))
    if not tk:
        return []
    head = tk[0].rsplit("/", 1)[-1].lower()
    rest = tk
    if head == "sshpass":
        idx = None
        for j in range(1, len(tk)):
            if tk[j].rsplit("/", 1)[-1].lower() in SSH_LIKE:
                idx = j
                break
        if idx is None:
            return []
        rest = tk[idx:]
        head = rest[0].rsplit("/", 1)[-1].lower()
    if head not in SSH_LIKE:
        return []

    violations = []
    if head == "ssh":
        user, host = positional_target(rest)
        if host:
            v = check_target(user, host, rest, "ssh")
            if v:
                violations.append(v)
        return violations

    # scp / rsync / sftp: every [user@]host:path positional arg first...
    specs = remote_spec_targets(rest)
    for user, host in specs:
        v = check_target(user, host, rest, head)
        if v:
            violations.append(v)
    # ...and sftp ALSO allows a bare `sftp [flags] [user@]host` (no colon).
    if head == "sftp" and not specs:
        user, host = positional_target(rest)
        if host:
            v = check_target(user, host, rest, "sftp")
            if v:
                violations.append(v)
    return violations


violations = []
for seg in split_segments(cmd):
    violations.extend(check_segment(seg))

if violations:
    seen = list(dict.fromkeys(violations))
    print("\n".join("  " + v for v in seen))
    sys.exit(2)
sys.exit(0)
PYEOF
) || RC=$?
RC=${RC:-0}

if [ "$RC" -eq 2 ]; then
    echo "" >&2
    echo "🚫 BLOCKED: ssh/scp/rsync/sftp to the subdev VPS with the WRONG identity." >&2
    echo "" >&2
    echo "$VIOLATION" >&2
    echo "" >&2
    echo "  The subdev VPS runs fail2ban — a wrong-user or wrong-key attempt" >&2
    echo "  bans dev1's source IP for 1h+ on ALL interfaces (tailscale AND" >&2
    echo "  public), breaking every subdev push target. Never guess." >&2
    echo "" >&2
    echo "  Allowed identities (per airuleset.py REMOTE_HOSTS — the single" >&2
    echo "  source of truth, read it before any ad-hoc subdev ssh):" >&2
    echo "    montalu[12345678]@subdev — default key OR sshpass -p" >&2
    echo "    marek@subdev  -i ~/.secrets/gatekeeper_access_ed25519" >&2
    echo "    david[1234]@subdev  -i ~/.secrets/gatekeeper_access_ed25519" >&2
    echo "    simap1@subdev  -i ~/.secrets/gatekeeper_access_ed25519" >&2
    echo "    miva1@subdev  -i ~/.secrets/gatekeeper_access_ed25519" >&2
    echo "    root@subdev   -i ~/.ssh/subdev_admin (gatekeeper VPS only," >&2
    echo "                  explicit or via its own ~/.ssh/config Host subdev)" >&2
    echo "" >&2
    echo "  If dev1 is CURRENTLY banned, do NOT retry/probe — wait for the ban" >&2
    echo "  to expire (verify from another vantage, e.g. gk, before assuming" >&2
    echo "  an outage)." >&2
    echo "" >&2
    echo "  Bypass (rare, user-instructed only, logged): append" >&2
    echo "  '# airuleset:subdev-ssh-ok <reason>' to the command." >&2
    echo "" >&2
    exit 2
elif [ "$RC" -ne 0 ]; then
    # A non-2 nonzero exit means the CHECK ITSELF malfunctioned (missing
    # python3, an internal bug) — never a real violation. Fail CLOSED but
    # say so honestly.
    echo "" >&2
    echo "🚫 BLOCKED (fail-closed): block-subdev-ssh-misuse.sh internal error" >&2
    echo "  — python3 exited $RC instead of running the check." >&2
    echo "$VIOLATION" >&2
    echo "" >&2
    echo "  This is a HOOK MALFUNCTION, not necessarily a real violation —" >&2
    echo "  investigate and fix the hook (or install python3) before retrying." >&2
    echo "" >&2
    exit 2
fi

exit 0
