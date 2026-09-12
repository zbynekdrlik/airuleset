#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — issue #981.
#
# Owner hard rule (2026-09-10, verbatim): "daj si tvrde pravidlo ze ja od teba
# ocakavam maintenance riesenie ktore udrziava obsadenost trvalo a nie rychle
# zasahy ktore nieco dobabru!!!"
#
# Incident: the supervisor's manual gk drain (`find /tmp/claude-1000 -mmin +720
# -exec rm -rf`) over ssh deleted the gk session's LIVE scratchpad with 16
# running waiter scripts. Earlier manual drains (#966 gk 96%, #965 subdev) were
# the same shape — a one-off delete over ssh with no liveness proof.
#
# Blocks ssh / sudo-to-another-user commands whose remote/elevated part contains
# a DELETE shape against session/cache/scratch/swap/container targets:
#   - Recursive rm (rm -rf, rm -r) on /tmp/claude-*, ~/.claude, .cache, etc.
#   - find … -delete / find … -exec rm
#   - swapoff
#   - Container image rm/prune (ctr/docker/podman)
#   - pkill/kill targeting another user's processes
# against these paths:
#   /tmp/claude-*, ~/.claude, /home/<u>/.claude, actions-runner*/(_work|externals*),
#   .cache, /var/cache, /var/lib/containerd, /var/lib/docker, /swapfile
#
# NOT blocked: local deletes in the repo/worktree/own scratchpad; ssh read-only
# (du/ls/df/find -print); airuleset.py push/install; git worktree remove.
#
# Standing durable path: `python3 ~/devel/airuleset/airuleset.py
# sweep-claude-scratch` / `sweep-stray-tmp` on the box (or the watchdog Job 40
# disk-guard drain / a disk-guard rung ticket).
#
# Bypass: '# airuleset:manual-drain-ok <owner order ref>' (ref required, logged).
# Exit code 2 = block the tool call.
#
# Dry-run (#963): echo '{"tool_input":{"command":"<cmd>"}}' | bash hooks/block-manual-remote-drain.sh; echo "exit=$?"

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
[ -z "$INPUT" ] && INPUT="$PAYLOAD"

[ -z "$INPUT" ] && exit 0

AUDIT_LOG="$HOME/devel/airuleset/audits/manual-drain-bypasses.log"

# Bypass: inline '# airuleset:manual-drain-ok <owner order ref>' trailing the
# command. Quoted spans stripped first (same pattern as block-destructive-
# remote.sh / block-subdev-ssh-misuse.sh).
BYPASS_REASON=$(printf '%s' "$INPUT" | python3 -c 'import re,sys
cmd=sys.stdin.read()
SQ=chr(39)
DQ=chr(34)
unquoted=re.sub(SQ+"[^"+SQ+"]*"+SQ, "", cmd)
unquoted=re.sub(DQ+"[^"+DQ+"]*"+DQ, "", unquoted)
m=None
for mm in re.finditer(r"#[ \t]*airuleset:manual-drain-ok[ \t]+([^\n]+)", unquoted):
    m=mm
if m:
    print(m.group(1).rstrip())
' 2>/dev/null || echo "")

if [ -n "$BYPASS_REASON" ]; then
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
    mkdir -p "$(dirname "$AUDIT_LOG")"
    echo "$(date -Iseconds)  project=$PROJECT  inline-bypass  # airuleset:manual-drain-ok $BYPASS_REASON" >> "$AUDIT_LOG"
    exit 0
fi

VIOLATION=$(python3 - "$INPUT" <<'PYEOF'
import re
import shlex
import sys

cmd = sys.argv[1]

# Targets: paths whose remote deletion is the manual drain shape
DRAIN_TARGETS = [
    r'/tmp/claude[_-]',
    r'~/.claude\b',
    r'/home/[^/]+/\.claude\b',
    r'\$HOME/\.claude\b',
    r'actions-runner',
    r'~/\.cache\b',
    r'/home/[^/]+/\.cache\b',
    r'\$HOME/\.cache\b',
    r'/root/\.cache\b',
    r'/var/cache\b',
    r'/var/lib/containerd\b',
    r'/var/lib/docker\b',
    r'/swapfile\b',
]
DRAIN_TARGET_RE = re.compile('|'.join(DRAIN_TARGETS), re.IGNORECASE)

# Delete shapes that indicate a drain operation
DELETE_SHAPES = [
    # rm with -r or -rf (recursive delete)
    r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+|--recursive\b)',
    # find with -delete
    r'\bfind\b[^;|&]*-delete\b',
    # find with -exec rm
    r'\bfind\b[^;|&]*-exec\s+rm\b',
    # xargs rm (piped from find)
    r'\bxargs\b[^;|&]*\brm\b',
    # swapoff (no target needed — always a drain)
    r'\bswapoff\b',
    # container image removal / system prune (not bare `docker rm` = deploy)
    r'\b(ctr|docker|podman|nerdctl)\s+[a-z]*\s*(rmi|prune)\b',
    r'\b(ctr|docker|podman|nerdctl)\s+(image|images|system)\s+(rm|rmi|prune)\b',
    # pkill / kill of another user's processes (requires drain target path)
    r'\b(pkill|killall)\b',
]
DELETE_SHAPE_RE = re.compile('|'.join(DELETE_SHAPES), re.IGNORECASE)


def split_segments(text):
    """Quote-aware split on shell separators — same as block-destructive-
    remote.sh / block-subdev-ssh-misuse.sh."""
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
    """Strip env assignments + wrappers (sudo/env/time/nice/ionice)."""
    i = 0
    while i < len(tk) and (tk[i] in ("sudo", "env", "time", "nice", "ionice")
                            or ASSIGN_RE.match(tk[i])):
        i += 1
    return tk[i:]


def _has_sudo_other_user(raw_tk):
    """True if the raw tokens contain sudo with a -u flag (to run as
    another user). Checks both fused (-nu) and separate (-u) forms."""
    found_sudo = False
    for t in raw_tk:
        low = t.rsplit("/", 1)[-1].lower()
        if low == "sudo":
            found_sudo = True
            continue
        if found_sudo:
            # -u or a fused flag containing u (e.g. -nu, -Hu)
            if t == "-u" or (t.startswith("-") and not t.startswith("--")
                            and "u" in t[1:]):
                return True
            # Stop scanning at the subcommand (a non-flag token after sudo
            # flags that isn't a value for -u/-g/-C/-p etc.)
    return False


def is_ssh_drain(segment):
    """Check if this segment is an ssh command with a drain shape in its
    remote command."""
    raw_tk = tokens_of(segment)
    tk = strip_prefix(raw_tk)
    if not tk:
        return None

    head = tk[0].rsplit("/", 1)[-1].lower()

    # Container prune/rmi and swapoff need no target path match — they are
    # always a drain shape regardless of target.
    NO_TARGET_RE = re.compile(
        r'\b(ctr|docker|podman|nerdctl)\s+[a-z]*\s*(rmi|prune)\b'
        r'|\b(ctr|docker|podman|nerdctl)\s+(image|images|system)\s+(rm|rmi|prune)\b'
        r'|\bswapoff\b'
        r'|\b(pkill|killall)\s+-u\b',
        re.IGNORECASE)

    def _is_drain(seg_text):
        """True if the text contains a drain shape + matching target, or a
        no-target drain (container prune, swapoff)."""
        if NO_TARGET_RE.search(seg_text):
            return True
        if DELETE_SHAPE_RE.search(seg_text) and DRAIN_TARGET_RE.search(seg_text):
            return True
        return False

    # Shape 1: ssh <target> '<remote command with drain>'
    if head == "ssh":
        seg_text = segment.strip()
        if _is_drain(seg_text):
            return "ssh remote drain: delete/prune/swapoff shape on a managed target"
        return None

    # Shape 2: sshpass -p ... ssh ...
    if head == "sshpass":
        for j in range(1, len(tk)):
            if tk[j].rsplit("/", 1)[-1].lower() == "ssh":
                seg_text = segment.strip()
                if _is_drain(seg_text):
                    return "ssh remote drain: delete/prune/swapoff shape on a managed target"
                return None
        return None

    # Shape 3: sudo -u <other-user> or sudo ... -H -u <user> with a drain
    # shape. The drain may be inside a nested `bash -lc '...'` argument.
    # Check against raw tokens (before strip_prefix consumed "sudo").
    if _has_sudo_other_user(raw_tk):
        seg_text = segment.strip()
        if _is_drain(seg_text):
            return "sudo-to-other-user drain: delete/prune/swapoff shape on a managed target"
        return None

    return None


violations = []
for seg in split_segments(cmd):
    v = is_ssh_drain(seg)
    if v:
        violations.append(v)

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
    echo "🚫 BLOCKED: manual drain/cleanup on a remote target." >&2
    echo "" >&2
    echo "$VIOLATION" >&2
    echo "" >&2
    echo "  Owner rule (2026-09-10): disk/memory/process maintenance on a managed" >&2
    echo "  target is delivered ONLY as a durable mechanism (a disk-guard rung with" >&2
    echo "  a liveness proof), never as a one-off manual delete/kill/swapoff over ssh." >&2
    echo "" >&2
    echo "  Standing durable path:" >&2
    echo "    python3 ~/devel/airuleset/airuleset.py sweep-claude-scratch" >&2
    echo "    python3 ~/devel/airuleset/airuleset.py sweep-stray-tmp" >&2
    echo "  (or the watchdog Job 40 disk-guard drain; run ON the box, or file a disk-guard rung ticket)." >&2
    echo "" >&2
    echo "  Bypass (owner order only, logged):" >&2
    echo "    # airuleset:manual-drain-ok <owner order ref>" >&2
    echo "" >&2
    exit 2
elif [ "$RC" -ne 0 ]; then
    echo "" >&2
    echo "🚫 BLOCKED (fail-closed): block-manual-remote-drain.sh internal error" >&2
    echo "  — python3 exited $RC instead of running the check." >&2
    echo "$VIOLATION" >&2
    echo "" >&2
    exit 2
fi

exit 0
