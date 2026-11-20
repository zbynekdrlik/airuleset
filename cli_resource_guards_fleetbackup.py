"""#1387 — read-only ``fleetbackup`` access on subdev: ``~/uploads`` and
``~/.claude/work-products`` of every shared-stream account, nothing else.

Owner decision 20.11.2026 (airuleset#1380): fleet-backup (dev1) backs up those
two directories of every subdev account. The box holds external developers'
homes and its root is reachable only through the gk VPS, so the dev1 key gets
NO sudo there (the controller's #1351 model is declined): a ``--system``
account whose key runs only a read-only rrsync rooted at ``/home``, plus POSIX
ACLs.

Decided design (issue comments 7040909733, 7040933347, 7040947380) and the
review decisions (rounds 1-3 on the ticket), each the more restrictive pick:
  * ``u:fleetbackup:--x`` on the home and on ``.claude``; ``r-x`` on the dirs
    of the two trees (plus a default ACL with ``g::---,o::---``), ``r--`` on
    their files WITHOUT touching a file's mask (a 0600 file stays closed and is
    only COUNTED, never chmod-ed; its owner includes it with ``chmod g+r``).
  * Every OTHER child of the home and of ``.claude`` carries a named
    ``u:fleetbackup:---``; a default ``d:u:fleetbackup:---,d:g::---,d:o::---``
    on the home and ``.claude`` makes every NEW child born closed. The two trees
    are pre-created as the account.
  * Every grant runs AS the account (``runuser -u <u> -- setfacl -P``), timed,
    size-capped, its output read from a file. Root only ever adds a ``---``
    deny with ``setfacl -P -n``: deny-only never changes anybody's permissions.
  * The forced command is an allow-list wrapper (printable ASCII only; only a
    sender pull of paths inside the two trees) in front of ``rrsync -ro
    /home``; the key carries an ``expiry-time`` every clean refresh renews.
  * The same idempotent refresh runs from the hourly quota-ceiling oneshot,
    and every push queues that unit; it takes its own root-only lock and keeps
    to a time budget.

Mechanism: ONE root-owned installed script (``FLEETBACKUP_ACL_SCRIPT_PATH``,
written by ``cli_resource_guards.guard_files()``) with the modes ``refresh``
(root), ``apply`` (as the account), ``check`` (as fleetbackup: the read-back is
the kernel's own access check, ``os.access``) and ``records``. The refresh
owns the login: it writes the key only after a clean identity check and a
read-back with no unresolved leak. It withdraws the login (account expired with
its own marker value, key removed, sessions killed, read back) only when
something OTHER than an account's own data stays reachable: what an account
does to its own home fails that account alone. Pure renderers + constants,
stdlib only.
"""
import shlex

from cli_controller_fleetbackup import MANAGER_FROM, MANAGER_KEY

FLEETBACKUP_USER = "fleetbackup"
FLEETBACKUP_HOME = "/var/lib/fleetbackup"     # outside /home: no quota loop, no rrsync view
FLEETBACKUP_SHELL = "/bin/sh"                 # sshd runs the forced command via the login shell
FLEETBACKUP_GECOS = "fleet-backup read-only rrsync (airuleset#1387)"
RRSYNC_PATH = "/usr/bin/rrsync"
RRSYNC_ROOT = "/home"
FLEETBACKUP_ACL_SCRIPT_PATH = "/usr/local/lib/airuleset/fleetbackup-acl.sh"
FLEETBACKUP_RRSYNC_WRAPPER_PATH = "/usr/local/lib/airuleset/fleetbackup-rrsync"
# Its own lock, created 0600 (a world-readable lock file can be flock-ed by any
# local user, which would stall every refresh until the key lapsed).
FLEETBACKUP_LOCK_PATH = "/run/airuleset-fleetbackup.lock"
FLEETBACKUP_LOCK_WAIT_S = 60
FLEETBACKUP_LOCK_BUSY_RC = 75
# The refresh's own time budget: past it, every remaining home is closed by
# root (fast) and not granted this run, so stalling tenants can never stop the
# key renewal. The push never waits for it: it queues the hourly unit.
FLEETBACKUP_BUDGET_S = 1500
# Time the hourly ceiling unit grants the refresh: its budget plus the root
# closes of the homes left when it runs out.
FLEETBACKUP_REFRESH_BUDGET_S = FLEETBACKUP_BUDGET_S + 300
# One account-side run (a tenant can STOP its own process), one full read-back,
# and a quick one (the pre-refresh siblings, a single closed home); all are
# also cut to what is left of the budget. Worst case per hostile tenant:
# 60 + 120 + 300 + 60 s, so one tenant cannot use up the budget.
FLEETBACKUP_ACCOUNT_TIMEOUT_S = 120
FLEETBACKUP_CHECK_TIMEOUT_S = 300
FLEETBACKUP_QUICK_CHECK_TIMEOUT_S = 60
# The unit that carries the hourly refresh; the push queues it.
FLEETBACKUP_REFRESH_UNIT = "airuleset-quota-ceiling.service"
# What an account-side run may write to root's output file (KiB).
FLEETBACKUP_OUTPUT_CAP_KIB = 10240
# The key stops working this long after the last clean refresh (hourly), so a
# refresh that stops running ends the login by itself; 3 h tolerates one
# skipped hourly run.
FLEETBACKUP_KEY_TTL_H = 3
# Our withdrawal's account expiry (days since the epoch). Not 1, the admin's
# documented `usermod --expiredate 1` lock, which a refresh must never lift.
FLEETBACKUP_WITHDRAW_EXPIRY = "2"
# The account-side and fleetbackup-side runs see no root environment.
FLEETBACKUP_RUN_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
FLEETBACKUP_TREES = ("uploads", ".claude/work-products")
# rsync options the wrapper refuses outright: the symlink-following ones (rrsync
# -ro still accepts --copy-unsafe-links) and --files-from, whose names rrsync
# never checks (the sender then follows a symlink in the middle of each name).
FLEETBACKUP_REFUSED_OPTIONS = ("--files-from", "--copy-unsafe-links", "--copy-links",
                               "--copy-dirlinks", "--keep-dirlinks")
# The deferred exit closing build_apply_script(): the fleetbackup leg ran in its
# own subshell earlier, so its failure never skipped any other guard step.
FLEETBACKUP_DEFERRED_EXIT = (
    '# #1387: the fleetbackup leg\'s exit, deferred until every other step ran\n'
    '[ "${fb_rc:-0}" -eq 0 ] || exit "$fb_rc"')


def authorized_key_line(wrapper=FLEETBACKUP_RRSYNC_WRAPPER_PATH):
    """fleetbackup's ``authorized_keys`` line on subdev without its expiry (the
    refresh prepends ``expiry-time=``): dev1 only, ``restrict`` (no pty /
    forwarding / rc), and the forced read-only rrsync wrapper."""
    return ('from="%s",restrict,command="/bin/sh %s" %s fleet-backup-manager@dev1'
            % (MANAGER_FROM, wrapper, MANAGER_KEY))


def render_fleetbackup_rrsync_wrapper(rrsync=RRSYNC_PATH, root=RRSYNC_ROOT):
    """The forced command, an ALLOW-list. First the whole command must be
    ``[A-Za-z0-9 ._/=,:+-]`` only: rrsync splits on every Python ``\\s`` (VT,
    FF, CR, 0x1c-0x1f, NBSP …) and honours backslashes, sh splits on space,
    tab and newline only, so any other byte lets the two read different words
    (round 3: ``\\r--files-from=-`` served /etc). The set holds no glob,
    quote or expansion character, so the unquoted split below is plain and
    rrsync reads the same words. ``LC_ALL=C``: sshd's default ``AcceptEnv
    LANG LC_*`` lets the client pick the locale the pattern runs in. Then:
    ``rsync --server --sender``, options minus ``FLEETBACKUP_REFUSED_OPTIONS``,
    ``.``, and one or more paths that each lie inside ``<account>/uploads`` or
    ``<account>/.claude/work-products`` (no ``..``). The client's text is
    never evaluated."""
    refused = "|".join("%s|%s=*" % (o, o) for o in FLEETBACKUP_REFUSED_OPTIONS)
    return (
        "#!/bin/sh\n"
        "# Managed by airuleset (#1387) — fleetbackup's forced command: only a\n"
        "# read of a path inside <account>/uploads or <account>/.claude/work-products\n"
        "# reaches rrsync. rrsync -ro alone still served /etc through a planted\n"
        "# symlink (--copy-unsafe-links, --files-from, a VT/CR word split).\n"
        "fb_refuse() { echo \"fleetbackup: refused ($1)\" >&2; exit 1; }\n"
        "LC_ALL=C; export LC_ALL\n"
        'case "${SSH_ORIGINAL_COMMAND:-}" in\n'
        "    *[!A-Za-z0-9\\ ._/=,:+-]*) fb_refuse \"a character outside the allowed set\" ;;\n"
        "esac\n"
        "set -- ${SSH_ORIGINAL_COMMAND:-}\n"
        '[ "${1:-}" = rsync ] && [ "${2:-}" = --server ] && [ "${3:-}" = --sender ] \\\n'
        '    || fb_refuse "not a read-only rsync pull"\n'
        "paths=0\n"
        "dot=0\n"
        "n=0\n"
        'for a in "$@"; do\n'
        "    n=$((n + 1))\n"
        '    [ "$n" -gt 3 ] || continue\n'
        '    if [ "$dot" -eq 0 ]; then\n'
        '        case "$a" in\n'
        "            .) dot=1 ;;\n"
        '            %s) fb_refuse "option $a" ;;\n'
        "            -*) ;;\n"
        '            *) fb_refuse "argument before the transfer root" ;;\n'
        "        esac\n"
        "        continue\n"
        "    fi\n"
        '    case "$a" in */../*|*/..|../*|..) fb_refuse "a .. path" ;; esac\n'
        '    acct=${a%%%%/*}\n'
        '    rest=${a#*/}\n'
        '    case "$acct" in ""|*[!a-z0-9_-]*) fb_refuse "not an account path" ;; esac\n'
        '    [ "$rest" != "$a" ] || fb_refuse "not inside a backup tree"\n'
        '    case "$rest" in\n'
        "        uploads|uploads/*|.claude/work-products|.claude/work-products/*) ;;\n"
        '        *) fb_refuse "not inside a backup tree" ;;\n'
        "    esac\n"
        "    paths=$((paths + 1))\n"
        "done\n"
        '[ "$dot" -eq 1 ] && [ "$paths" -ge 1 ] || fb_refuse "no path"\n'
        "exec %s -ro %s\n"
        % (refused, shlex.quote(rrsync), shlex.quote(root))
    )


def _passwd_signature(home=FLEETBACKUP_HOME, shell=FLEETBACKUP_SHELL):
    """``<home>:<shell>`` — how an account OUR useradd made is recognised; a
    fleetbackup account with any other signature is foreign and never touched."""
    return "%s:%s" % (home, shell)


def _paused_accounts():
    """#851: accounts the owner froze are never granted (one derivation, the
    disk guard's)."""
    from cli_disk_guard_root import _paused_accounts as paused
    return paused()


# The read-back AS fleetbackup: NUL records <kind> <mode> <path> on stdin, the
# kernel's own answer per path (os.access with fleetbackup's ids). Python, not
# a bash `read -d ''` loop, which managed 1.7 MB/s (round 3).
_CHECK_PY = r'''
import os, sys
fields = sys.stdin.buffer.read().split(b"\0")
tail = fields.pop()              # after the last NUL: empty for a whole record stream
if tail:
    fields.append(tail)
leak = miss = ok = priv = shown = 0
def show(tag, p):
    global shown
    if shown < 20:
        sys.stderr.write("  ⚠ FLEETBACKUP-READBACK %s: %r\n" % (tag, p))
    shown += 1
def can(p, m):
    try:
        return os.access(p, m)
    except (OSError, ValueError):
        return False
for i in range(0, len(fields), 3):
    rec = fields[i:i + 3]
    if len(rec) < 3:
        miss += 1; show("MISS bad record", rec[0]); break
    kind, mode, p = rec
    if kind == b"C":
        if can(p, os.R_OK) or can(p, os.W_OK) or can(p, os.X_OK):
            leak += 1; show("LEAK reachable", p)
    elif kind == b"T":
        if can(p, os.R_OK) or can(p, os.W_OK):
            leak += 1; show("LEAK listable", p)
    elif kind == b"D":
        if can(p, os.R_OK) and can(p, os.X_OK):
            ok += 1
        else:
            miss += 1; show("MISS dir", p)
    elif kind == b"F":
        try:
            m = int(mode, 8)
        except ValueError:
            miss += 1; show("MISS bad mode", p); continue
        if can(p, os.R_OK):
            ok += 1
        elif m & 0o040:
            miss += 1; show("MISS file", p)
        else:
            priv += 1
    else:
        miss += 1; show("MISS bad record", kind)
print("leak=%d miss=%d readable=%d private=%d" % (leak, miss, ok, priv))
'''

# The installed script. `@@NAME@@` placeholders are filled (shell-quoted) by
# render_fleetbackup_acl_script(); everything else is literal bash.
_ACL_SCRIPT_TEMPLATE = r'''#!/bin/bash
# Managed by airuleset (#1387) — read-only fleetbackup access to ~/uploads and
# ~/.claude/work-products of every shared-stream account, nothing else.
# Run as root by the push and by the hourly quota-ceiling unit, under its own
# lock. Every grant runs AS the account (`apply`); root only adds `---` denies;
# the read-back runs AS fleetbackup (`check`).
# Modes: refresh (default, root) | apply <fb> <home> grant|hold | check | records <home> [siblings]
set -euo pipefail
FB=@@USER@@
FB_SIG=@@SIG@@
FB_SSH=@@SSH@@
FB_KEY_UID=@@KEY_UID@@
FB_KEY=@@KEY@@
FB_KEY_TTL_H=@@KEY_TTL_H@@
FB_ROOT=@@ROOT@@
FB_PATH=@@RUN_PATH@@
FB_LOCK=@@LOCK@@
FB_LOCK_WAIT=@@LOCK_WAIT@@
FB_BUSY_RC=@@BUSY_RC@@
FB_SKIP=@@SKIP@@
FB_ACCOUNT_TIMEOUT=@@ACCOUNT_TIMEOUT@@
FB_CHECK_TIMEOUT=@@CHECK_TIMEOUT@@
FB_OUTPUT_CAP_KIB=@@OUTPUT_CAP@@
FB_WITHDRAW_EXPIRY=@@WITHDRAW_EXPIRY@@
FB_QUICK_CHECK=@@QUICK_CHECK@@
FB_BUDGET=@@BUDGET@@
FB_MARK="${FB_SSH%/*}/.airuleset-withdrawn"   # our own withdrawal, never an admin's lock
FB_SELF=$(readlink -f -- "$0")   # absolute: the refresh cd's to / before calling itself
FB_T0=$(date +%s)
FB_CHECK_PY=@@CHECK_PY@@
# getfacl -c output(s) -> "ok <owning-group effective perms>" | "refuse" (a named
# entry for anyone but fleetbackup: recalculating the mask would widen it);
# scope=access ignores default entries (a recalculation that touches only the
# access ACL cannot widen them)
FB_ACL_AWK='
BEGIN { foreign = 0; g = "" }
{
    split($0, f, ":")
    if (f[1] == "default") { if (scope == "access") next; t = f[2]; who = f[3] } else { t = f[1]; who = f[2] }
    if ((t == "user" || t == "group") && who != "" && !(t == "user" && who == fb)) foreign = 1
    if (f[1] == "group" && f[2] == "") {
        g = substr(f[3], 1, 3)
        if (match($0, /#effective:[-r][-w][-x]/)) g = substr($0, RSTART + 11, 3)
    }
}
END { if (foreign || g !~ /^[-r][-w][-x]$/) print "refuse"; else print "ok " g }'
# getfacl -p dump of tree FILES -> report every file whose fleetbackup entry is
# `---` (it arrived closed: born under the closed home default or moved in);
# the grant then gives it `r--` and its OWN mask decides, like any 0600 file
FB_CLOSED_AWK='
/^# file: / { file = substr($0, 9); next }
$0 ~ ("^user:" fb ":---") {
    closed++
    if (closed <= 5) printf "  \342\232\240 FLEETBACKUP: %s arrived closed; it is granted now, and its own mode decides (chmod g+r includes a private one)\n", file
}
END { if (closed > 0) printf "  \342\232\240 FLEETBACKUP: %d file(s) in a backup tree arrived closed\n", closed }'

fb_say() { printf '  fleetbackup: %s\n' "$*"; }
fb_warn() { printf '  ⚠ FLEETBACKUP: %s\n' "$*" >&2; }
fb_realdir() { [ -d "$1" ] && [ ! -L "$1" ]; }
fb_twice() { "$@" || "$@"; }   # one fresh re-scan when a file vanished mid-run
fb_claude_traversed() { fb_realdir "$1/.claude" && fb_realdir "$1/.claude/work-products"; }
fb_left() { echo $(( FB_BUDGET - ($(date +%s) - FB_T0) )); }   # seconds left of the budget

fb_home_kids() { # $1 home, rest: find action — every child of the home but a granted tree
    local h="$1" keep=(-name uploads)
    shift
    if fb_claude_traversed "$h"; then keep+=(-o -name .claude); fi
    find "$h" -ignore_readdir_race -mindepth 1 -maxdepth 1 \( \( "${keep[@]}" \) -type d \) -prune \
        -o ! -type l "$@"
}

fb_claude_kids() { # $1 home, rest: find action — every child of .claude but work-products
    local h="$1"
    shift
    if fb_realdir "$h/.claude"; then
        find "$h/.claude" -ignore_readdir_race -mindepth 1 -maxdepth 1 \( -name work-products -type d \) -prune \
            -o ! -type l "$@"
    fi
}

# ---------------------------------------------------------------- account side
fb_traverse() { # $1 fb, $2 dir: u:<fb>:--x, the owning group keeps exactly its effective perms
    local acl v
    acl=$(getfacl -p -c -- "$2") || return 1
    v=$(printf '%s\n' "$acl" | awk -v fb="$1" -v scope=access "$FB_ACL_AWK") || return 1
    case "$v" in
        "ok "???) setfacl -P -m "g::${v#ok },u:$1:--x" -- "$2" ;;
        *) fb_warn "$(printf '%q' "$2") carries a named ACL entry for another user or group — not opened (a mask change would widen it)"
           return 1 ;;
    esac
}

fb_tree() { # $1 fb, $2 tree root -> 0 granted | 1 granted with failures | 2 refused
    local v rc=0
    v=$({ find "$2" -ignore_readdir_race -xdev -type d -exec getfacl -p -c -- {} + 2>/dev/null || true; } \
        | awk -v fb="$1" -v scope=all "$FB_ACL_AWK") || v=refuse
    case "$v" in
        "ok "???) ;;
        *) fb_warn "a dir under $(printf '%q' "$2") carries a named ACL entry for another user or group — tree not granted"
           return 2 ;;
    esac
    # g::--- first: a recalculated mask must never re-widen a group the owner closed
    fb_twice find "$2" -ignore_readdir_race -xdev -type d \
        -exec setfacl -P -m "g::---,u:$1:r-x,d:u:$1:r-x,d:g::---,d:o::---" -- {} + || rc=1
    { find "$2" -ignore_readdir_race -xdev -type f -exec getfacl -p -- {} + 2>/dev/null || true; } \
        | awk -v fb="$1" "$FB_CLOSED_AWK" >&2 || true   # report only
    # files: r--, mask untouched (-n); -P never follows a file swapped for a symlink
    fb_twice find "$2" -ignore_readdir_race -xdev -type f -exec setfacl -P -n -m "u:$1:r--" -- {} + || rc=1
    return "$rc"
}

fb_close() { # $1 fb, rest: paths — a named deny, mask untouched
    local fb="$1"
    shift
    setfacl -P -n -m "u:$fb:---" -- "$@"
}

fb_close_kids() { # $1 fb, $2 home: every child the account owns and no tree
    { fb_home_kids "$2" -user "$(id -un)" -print0; fb_claude_kids "$2" -user "$(id -un)" -print0; } \
        | xargs -0 -r setfacl -P -n -m "u:$1:---" --
}

fb_apply() { # as the account: $1 fb, $2 home, $3 grant|hold
    local fb="$1" h="$2" mode="$3" rc=0 t granted=0
    cd /
    # every new child of the home and .claude is born closed (rename-over included);
    # -n: an existing default mask is never recalculated (no default entry widens)
    setfacl -P -n -d -m "u:$fb:---,g::---,o::---" -- "$h" || rc=1
    if fb_realdir "$h/.claude"; then
        setfacl -P -n -d -m "u:$fb:---,g::---,o::---" -- "$h/.claude" || rc=1
    fi
    if [ "$mode" = grant ]; then   # pre-create the trees: never born under the closed default
        if [ ! -e "$h/uploads" ] && [ ! -L "$h/uploads" ]; then mkdir -m 0700 -- "$h/uploads" || true; fi
        if fb_realdir "$h/.claude" && [ ! -e "$h/.claude/work-products" ] && [ ! -L "$h/.claude/work-products" ]; then
            mkdir -m 0700 -- "$h/.claude/work-products" || true
        fi
    fi
    if ! fb_twice fb_close_kids "$fb" "$h"; then
        fb_warn "$(id -un): a child of the home could not be closed — home HELD"
        mode=hold; rc=1
    fi
    if [ "$mode" = grant ] && fb_realdir "$h/uploads"; then
        t=0; fb_tree "$fb" "$h/uploads" || t=$?
        [ "$t" -eq 0 ] || rc=1
        [ "$t" -eq 2 ] || granted=1
    fi
    if [ "$mode" = grant ] && fb_claude_traversed "$h"; then
        t=0; fb_tree "$fb" "$h/.claude/work-products" || t=$?
        [ "$t" -eq 0 ] || rc=1
        if [ "$t" -ne 2 ] && fb_traverse "$fb" "$h/.claude"; then
            granted=1
        else
            rc=1; fb_close "$fb" "$h/.claude" || rc=1
        fi
    elif fb_realdir "$h/.claude"; then
        fb_close "$fb" "$h/.claude" || rc=1   # held, or not traversed: closed like any other child
    fi
    if [ "$mode" = grant ] && [ "$granted" -eq 1 ]; then
        fb_traverse "$fb" "$h" || { rc=1; fb_close "$fb" "$h" || rc=1; }
    else
        fb_close "$fb" "$h" || rc=1
    fi
    return "$rc"
}

# ------------------------------------------------------------ fleetbackup side
fb_check() { cd /; exec python3 -I -S -c "$FB_CHECK_PY"; }   # NUL records on stdin

# ------------------------------------------------------------------- root side
fb_tree_records() {
    find "$1" -ignore_readdir_race -xdev \( -type d -printf 'D\0%m\0%p\0' \) -o \( -type f -printf 'F\0%m\0%p\0' \)
}

fb_records() { # $1 home [siblings]: what fleetbackup may and may not reach, as NUL records
    local h="$1" scope="${2:-all}" rc=0
    printf 'T\0-\0%s\0' "$h"
    fb_home_kids "$h" -printf 'C\0%m\0%p\0' || rc=1
    if fb_claude_traversed "$h"; then printf 'T\0-\0%s\0' "$h/.claude"; fi
    fb_claude_kids "$h" -printf 'C\0%m\0%p\0' || rc=1
    if [ "$scope" = all ]; then
        if fb_realdir "$h/uploads"; then fb_tree_records "$h/uploads" || rc=1; fi
        if fb_claude_traversed "$h"; then fb_tree_records "$h/.claude/work-products" || rc=1; fi
    fi
    return "$rc"   # an incomplete record stream must never pass as a clean read-back
}

fb_cut() { # $1 a timeout, $2 a floor: the timeout, cut to what is left of the budget
    local left
    left=$(fb_left)
    [ "$left" -ge "$2" ] || left=$2
    if [ "$left" -lt "$1" ]; then echo "$left"; else echo "$1"; fi
}

fb_as_fb() { # $1 a timeout, rest: a mode of this script AS fleetbackup, no root environment.
    #          Only root can signal fleetbackup's processes, so a check keeps a 30 s floor
    #          past the budget: a single-path check must never read as "unverifiable".
    local t
    t=$(fb_cut "$1" 30)
    shift
    timeout -k 10 "$t" runuser -u "$FB" -- env -i PATH="$FB_PATH" LC_ALL=C \
        /bin/bash "$FB_SELF" "$@" 8>&- 9>&-
}

fb_prefix() { # account-side output can never forge a line or a terminal escape
    LC_ALL=C sed -u 's/[^[:print:]]/?/g; s/^/    ['"$1"'] /' >&2
}

fb_run_account() { # $1 account, rest: a mode of this script AS the account — timed (the
    #                  account can stop its own process), size-capped, output read from a file
    local u="$1" out rc=0 t size
    shift
    out=$(mktemp) || return 1
    t=$(fb_cut "$FB_ACCOUNT_TIMEOUT" 1)
    ( ulimit -f "$FB_OUTPUT_CAP_KIB"   # KiB: a tenant cannot fill root's disk through us
      exec timeout -k 10 "$t" runuser -u "$u" -- env -i PATH="$FB_PATH" LC_ALL=C \
          /bin/bash "$FB_SELF" "$@" </dev/null >"$out" 2>&1 8>&- 9>&- ) || rc=$?
    { head -c 32768 -- "$out" | head -n 200 | fb_prefix "$u"; } || true
    size=$(stat -c %s -- "$out" 2>/dev/null || echo 0)
    rm -f -- "$out"
    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then fb_warn "$u: the account-side run timed out after ${t}s"; fi
    if [ "$size" -ge $(( FB_OUTPUT_CAP_KIB * 1024 )) ]; then
        fb_warn "$u: the account-side run wrote too much output — cut at ${FB_OUTPUT_CAP_KIB} KiB"
        [ "$rc" -ne 0 ] || rc=1
    fi
    return "$rc"
}

fb_sum() { # stdin: a read-back summary -> "leak miss readable private", or "1 0 0 0" when unparseable
    local s l m r p
    s=$(cat)
    case "$s" in
        "leak="*" miss="*" readable="*" private="*) read -r l m r p <<<"$s"
            l=${l#leak=}; m=${m#miss=}; r=${r#readable=}; p=${p#private=}
            case "$l:$m:$r:$p" in
                *[!0-9:]*|*::*|:*|*:) ;;
                *) echo "$l $m $r $p"; return 0 ;;
            esac ;;
    esac
    echo "1 0 0 0"
    return 1
}

fb_closed() { # $1 path -> 0 the read-back proves no access | 1 it proves access | 2 unverifiable
    local s
    s=$(printf 'C\0-\0%s\0' "$1" | fb_as_fb "$FB_QUICK_CHECK" check) || return 2
    case "$s" in
        "leak=0 "*) return 0 ;;
        "leak="[1-9]*) return 1 ;;
    esac
    return 2
}

fb_root_deny() { # $1 path: a deny-only ACL as root — -n, `---`: nobody's permissions change
    setfacl -P -n -m "u:$FB:---" -- "$1"
}

fb_owner() { # $1 path: its owner's name, "" when unknown
    stat -c %U -- "$1" 2>/dev/null || true
}

fb_access_entry() { # $1 path: fleetbackup's ACCESS entry perms ("" when none; default entries ignored)
    getfacl -p -c -- "$1" 2>/dev/null | awk -F: -v fb="$FB" '$1 == "user" && $2 == fb { print substr($3, 1, 3) }'
}

fb_close_home() { # $1 account, $2 home, $3 1 when a child the account does not own may be
    #               open -> 1 closed, or open only by the account's own doing | 2 not
    local u="$1" h="$2" foreign="${3:-0}" c=0 owner
    owner=$(fb_owner "$h")            # read first: no account can chown
    fb_root_deny "$h" || true         # /home/<u> is a root-owned name (no swap); the read-back decides
    fb_closed "$h" || c=$?
    if [ "$c" -eq 0 ]; then return 1; fi
    if [ "$c" -eq 2 ]; then fb_warn "$u: the read-back of the closed home did not run — UNRESOLVED"; return 2; fi
    # Only the owner can change its inode's ACL after root's deny, or fill its xattr
    # space so the deny fails: an inode the account owns, holding only what it owns,
    # is its own doing — its backup fails, nobody else's (round 3: never withdraw for it)
    if [ "$foreign" -eq 0 ] && [ "$owner" = "$u" ]; then
        fb_warn "$u: its home stays open by its own doing — failed for $u only"
        return 1
    fi
    fb_warn "$u: home still reachable after root closed it — UNRESOLVED"
    return 2
}

fb_account() { # $1 account, $2 home -> 0 ok | 1 failed, nothing of ours open | 2 open and not closable
    #              | 3 not a managed home | 4 paused by policy (3 and 4: the caller treats
    #              the home like any non-stream entry; only 3 is a failure)
    local u="$1" h="$2" mode=grant rc=0 out p l m r v foreign=0 cr=0
    case " $FB_SKIP " in *" $u "*) fb_say "$u: paused (#851) — not granted"; return 4 ;; esac
    if [ -L "$h" ]; then fb_warn "$u: home $(printf '%q' "$h") is a symlink — not granted"; return 3; fi
    if [ "$(getent passwd "$u" | cut -d: -f6)" != "$h" ]; then
        fb_warn "$u: $(printf '%q' "$h") is not the home of an account named $u — not granted"; return 3
    fi
    if [ "$(fb_left)" -le 0 ]; then
        fb_warn "$u: over the refresh budget (${FB_BUDGET}s) — home closed, not granted this run"
        fb_close_home "$u" "$h" 0 || cr=$?
        return "$cr"   # 1 closed (this account failed) | 2 not closable
    fi
    # visibility: how much was open BEFORE this refresh (moved-in children, owner edits)
    out=$(fb_records "$h" siblings | fb_as_fb "$FB_QUICK_CHECK" check) || out=""
    if v=$(fb_sum <<<"$out"); then
        read -r l _ <<<"$v"
        if [ "$l" -ne 0 ]; then fb_warn "$u: $l child(ren) were reachable before this refresh — closing them"; fi
    else
        fb_warn "$u: the pre-refresh read-back did not run"
    fi
    # a child the account does not own: root adds the deny
    while IFS= read -r -d '' p; do
        if ! fb_root_deny "$p"; then
            fb_warn "$u: could not close $(printf '%q' "$p") (not owned by $u) — home HELD"
            mode=hold; rc=1; foreign=1
        fi
    done < <({ fb_home_kids "$h" ! -user "$u" -print0; fb_claude_kids "$h" ! -user "$u" -print0; })
    if ! fb_run_account "$u" apply "$FB" "$h" "$mode"; then
        fb_warn "$u: the account-side apply reported a failure (above)"; rc=1
    fi
    for p in uploads .claude .claude/work-products; do
        if { [ -e "$h/$p" ] || [ -L "$h/$p" ]; } && ! fb_realdir "$h/$p"; then
            fb_warn "$u: $(printf '%q' "$h/$p") is not a plain directory — its tree is not granted"; rc=1
        fi
    done
    out=$(fb_records "$h" | fb_as_fb "$FB_CHECK_TIMEOUT" check) || out=""
    if ! v=$(fb_sum <<<"$out"); then fb_warn "$u: the read-back did not run or was unreadable — treated as open"; fi
    read -r l m r p <<<"$v"
    if [ "$l" -ne 0 ]; then
        fb_warn "$u: $l path(s) reachable that must not be — home closed to $FB"
        fb_close_home "$u" "$h" "$foreign" || cr=$?
        return "$cr"   # 1 closed, or open only by its own doing | 2 not closable
    fi
    if [ "$m" -ne 0 ]; then fb_warn "$u: $m path(s) under the two dirs are not readable (listed above)"; rc=1; fi
    fb_say "$u: readable=$r private=$p (files without group read stay closed, counted)"
    return "$rc"
}

fb_foreign_homes() { # every non-stream entry of FB_ROOT gets the deny (root may: only root writes /home)
    local p cur owner v l list all theirs rc=0 failed=0
    list=$(mktemp) && all=$(mktemp) && theirs=$(mktemp) || { fb_warn "mktemp failed — treated as open"; return 2; }
    if ! find "$FB_ROOT" -ignore_readdir_race -mindepth 1 -maxdepth 1 ! -type l -print0 >"$list"; then
        fb_warn "could not list $FB_ROOT — treated as open"; rc=2
    fi
    while [ "$rc" -eq 0 ] && IFS= read -r -d '' p; do
        [ -z "${FB_STREAMS[${p##*/}]+x}" ] || continue
        printf 'C\0-\0%s\0' "$p" >>"$all"
        owner=$(fb_owner "$p")
        # a non-root owner alone can reopen its inode (or make our deny fail); an entry
        # owned by root, or by nobody known, open after our deny is unexplained
        if [ -z "$owner" ] || [ "$owner" = root ]; then printf 'C\0-\0%s\0' "$p" >>"$theirs"; fi
        cur=$(fb_access_entry "$p") || cur=""
        [ "$cur" != "---" ] || continue
        if fb_root_deny "$p"; then
            fb_say "closed $(printf '%q' "$p") (not a managed shared-stream home)"
        else
            failed=1; fb_warn "could not close $(printf '%q' "$p") (owner: ${owner:-?})"
        fi
    done <"$list"
    if [ "$rc" -eq 0 ]; then
        if ! v=$(fb_as_fb "$FB_CHECK_TIMEOUT" check <"$all" | fb_sum); then
            fb_warn "the read-back of $FB_ROOT did not run — treated as open"; rc=2
        else
            read -r l _ <<<"$v"
            rc=$failed
            if [ "$l" -ne 0 ]; then
                l=0
                if [ -s "$theirs" ]; then
                    v=$(fb_as_fb "$FB_QUICK_CHECK" check <"$theirs" | fb_sum) || v="1 0 0 0"
                    read -r l _ <<<"$v"
                fi
                if [ "$l" -eq 0 ]; then
                    fb_warn "a non-stream entry of $FB_ROOT is open only through its owner's own doing"; rc=1
                else
                    fb_warn "a root-owned entry of $FB_ROOT is still reachable — UNRESOLVED"; rc=2
                fi
            fi
        fi
    fi
    rm -f -- "$list" "$all" "$theirs"
    return "$rc"
}

fb_rootdir() { # a real dir owned by FB_KEY_UID, not writable by group or others
    [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -c %u -- "$1")" = "$FB_KEY_UID" ] \
        && [ $(( 8#$(stat -c %a -- "$1") & 8#022 )) -eq 0 ]
}

fb_identity() { # fleetbackup is exactly the locked, group-less, sudo-less account it must be
    local pw out ok=0
    pw=$(passwd -S "$FB" 2>/dev/null | awk '{print $2}') || pw=""
    if [ "$pw" != L ]; then fb_warn "the password of $FB is not locked (passwd -S: ${pw:-?})"; ok=1; fi
    if [ "$(id -G "$FB" 2>/dev/null | wc -w)" -ne 1 ] || [ "$(id -gn "$FB" 2>/dev/null)" != "$FB" ]; then
        fb_warn "$FB is not in exactly its own group (id -Gn: $(id -Gn "$FB" 2>/dev/null || echo '?'))"; ok=1
    fi
    out=$(LC_ALL=C sudo -n -l -U "$FB" 2>&1) || true
    case "$out" in
        *"is not allowed to run sudo"*) ;;
        *) fb_warn "$FB may have sudo rules — sudo -l: ${out%%$'\n'*}"; ok=1 ;;
    esac
    if ! fb_rootdir "${FB_SSH%/*}" || ! fb_rootdir "$FB_SSH"; then
        fb_warn "$FB_SSH or its parent is not a root-owned, non-writable dir"; ok=1
    fi
    if [ -e "$FB_SSH/authorized_keys2" ] || [ -L "$FB_SSH/authorized_keys2" ]; then
        fb_warn "$FB_SSH/authorized_keys2 exists — an unmanaged second key file"; ok=1
    fi
    return "$ok"
}

fb_key_write() { # $1 the whole authorized_keys content (one line)
    local k="$FB_SSH/authorized_keys" t
    if ! fb_rootdir "${FB_SSH%/*}" || ! fb_rootdir "$FB_SSH"; then
        fb_warn "$FB_SSH or its parent is not a root-owned, non-writable dir — key file untouched"
        return 1
    fi
    if [ -f "$k" ] && [ ! -L "$k" ] && [ "$(stat -c '%u %a' -- "$k")" = "$FB_KEY_UID 644" ] \
            && [ "$(cat -- "$k")" = "$1" ]; then
        return 0
    fi
    t=$(mktemp "$FB_SSH/.authorized_keys.XXXXXX") || return 1
    # -T: a directory planted as authorized_keys never swallows the key file
    if printf '%s\n' "$1" >"$t" && chmod 0644 "$t" && mv -fT -- "$t" "$k"; then
        return 0
    fi
    rm -f -- "$t"
    return 1
}

fb_expiry() { getent shadow "$FB" | cut -d: -f8; }

fb_withdraw() { # -> 0 only when the read-back proves fleetbackup has no login left
    local exp before
    before=$(fb_expiry) || before=""
    if [ -z "$before" ]; then
        # our own value (not an admin's `1`) + marker: only that is ever lifted again
        if chage -E "$FB_WITHDRAW_EXPIRY" "$FB" >/dev/null 2>&1; then
            if fb_rootdir "${FB_SSH%/*}"; then printf '%s\n' "$FB_WITHDRAW_EXPIRY" >"$FB_MARK"; fi
        else
            fb_warn "chage -E $FB_WITHDRAW_EXPIRY $FB failed"
        fi
    fi
    if fb_rootdir "${FB_SSH%/*}" && fb_rootdir "$FB_SSH"; then
        rm -f -- "$FB_SSH/authorized_keys" "$FB_SSH/authorized_keys2"
    fi
    pkill -KILL -u "$FB" >/dev/null 2>&1 || true
    exp=$(fb_expiry) || exp=""
    if { [ -n "$exp" ] && [ "$exp" -le $(( $(date +%s) / 86400 )) ] 2>/dev/null; } \
            || { [ ! -e "$FB_SSH/authorized_keys" ] && [ ! -L "$FB_SSH/authorized_keys" ] \
                 && [ ! -e "$FB_SSH/authorized_keys2" ] && [ ! -L "$FB_SSH/authorized_keys2" ]; }; then
        fb_warn "login WITHDRAWN (account expiry: ${exp:-none}) — a clean refresh restores it"
        return 0
    fi
    fb_warn "WITHDRAWAL FAILED — the fleetbackup key may still log in"
    return 1
}

fb_restore() { # the key with a fresh expiry-time FIRST, then lift OUR expiry (never an admin's)
    local exp until
    until=$(date -u -d "+$FB_KEY_TTL_H hours" +%Y%m%d%H%M) || return 1
    fb_key_write "expiry-time=\"${until}Z\",$FB_KEY" || return 1
    exp=$(fb_expiry) || exp=""
    if [ -f "$FB_MARK" ] && [ ! -L "$FB_MARK" ]; then
        if [ "$exp" = "$FB_WITHDRAW_EXPIRY" ]; then
            chage -E -1 "$FB" >/dev/null 2>&1 || { fb_warn "could not lift the account expiry"; return 1; }
        fi
        rm -f -- "$FB_MARK"   # ours is gone (lifted now, or changed by someone since): forget it
        exp=$(fb_expiry) || exp=""
    fi
    if [ -n "$exp" ]; then fb_warn "$FB has an account expiry set by someone else ($exp) — left as it is"; fi
    fb_say "key valid until ${until}Z (UTC)"
}

fb_lock() { # the refresh lock on fd 8: root-only (a readable lock file can be flock-ed by anyone)
    local um
    um=$(umask)
    umask 077
    if ! { exec 8>>"$FB_LOCK"; } 2>/dev/null; then
        umask "$um"; fb_warn "cannot open the refresh lock $FB_LOCK"; return 1
    fi
    umask "$um"
    chmod 0600 -- "$FB_LOCK" 2>/dev/null || true
    if [ "$(stat -c '%u %a' -- "$FB_LOCK")" != "$FB_KEY_UID 600" ]; then
        fb_warn "the refresh lock $FB_LOCK is not root-only"; return 1
    fi
    if ! flock -w "$FB_LOCK_WAIT" 8; then
        fb_warn "refresh lock $FB_LOCK busy — refresh skipped"; return "$FB_BUSY_RC"
    fi
}

fb_refresh() { # -> 0 ok | 1 failed, nothing of ours open | 2 login withdrawn | 3 withdrawal failed | busy rc
    local sig rc=0 r t home u m withdrawn=0
    local -A FB_STREAMS=()
    cd /
    sig=$(getent passwd "$FB" | cut -d: -f6,7) || sig=""
    if [ "$sig" != "$FB_SIG" ]; then
        fb_say "no airuleset $FB account on this box — ACL refresh skipped"
        return 0
    fi
    r=0; fb_lock || r=$?
    [ "$r" -eq 0 ] || return "$r"
    for t in setfacl getfacl runuser timeout python3 chage; do
        if ! command -v "$t" >/dev/null 2>&1; then
            fb_warn "$t missing (setfacl/getfacl come from the acl package) — nothing refreshed"
            fb_withdraw && return 2
            return 3
        fi
    done
    if ! fb_identity; then fb_withdraw && return 2; return 3; fi
    for home in "$FB_ROOT"/*; do
        [ -d "$home" ] || continue
        u=${home##*/}
        m="$home/.claude/airuleset-box-class"
        [ -f "$m" ] && [ ! -L "$m" ] || continue   # an account-owned file: never a FIFO or a link
        case "$(timeout 5 head -c 256 -- "$m" 2>/dev/null || true)" in
            *shared-stream*) ;;
            *) continue ;;
        esac
        r=0; fb_account "$u" "$home" || r=$?
        [ "$r" -eq 0 ] || [ "$r" -eq 4 ] || rc=1
        # a refused or paused home is not managed: fb_foreign_homes closes it
        [ "$r" -ge 3 ] || FB_STREAMS[$u]=1
        if [ "$r" -eq 2 ] && [ "$withdrawn" -eq 0 ]; then
            withdrawn=1; fb_withdraw || withdrawn=2
        fi
    done
    r=0; fb_foreign_homes || r=$?
    [ "$r" -eq 0 ] || rc=1
    if [ "$r" -eq 2 ] && [ "$withdrawn" -eq 0 ]; then withdrawn=1; fb_withdraw || withdrawn=2; fi
    if [ "$withdrawn" -eq 1 ]; then return 2; fi
    if [ "$withdrawn" -eq 2 ]; then return 3; fi
    fb_restore || rc=1
    fb_say "ACL refresh + read-back done (${#FB_STREAMS[@]} accounts, rc=$rc)"
    return "$rc"
}

case "${1:-refresh}" in
    refresh) rc=0; fb_refresh || rc=$?; exit "$rc" ;;
    apply) shift; fb_apply "$@" ;;
    check) fb_check ;;
    records) fb_records "$2" "${3:-all}" ;;
    *) echo "usage: $FB_SELF [refresh | apply <fb> <home> grant|hold | check | records <home> [siblings]]" >&2
       exit 64 ;;
esac
'''


def render_fleetbackup_acl_script(user=FLEETBACKUP_USER, home=FLEETBACKUP_HOME,
                                  shell=FLEETBACKUP_SHELL, home_root=RRSYNC_ROOT,
                                  key_uid=0, key_line=None, lock=FLEETBACKUP_LOCK_PATH,
                                  lock_wait_s=FLEETBACKUP_LOCK_WAIT_S, paused=None,
                                  account_timeout_s=FLEETBACKUP_ACCOUNT_TIMEOUT_S,
                                  budget_s=FLEETBACKUP_BUDGET_S):
    """The installed ``fleetbackup-acl.sh`` (see the module docstring). The
    keyword arguments exist for the tests (a temp home root, a non-root owner
    uid for the key dir and lock, a temp lock, short timeouts); production
    uses the defaults."""
    if paused is None:
        paused = _paused_accounts()
    values = {
        "USER": user,
        "SIG": _passwd_signature(home, shell),
        "SSH": home.rstrip("/") + "/.ssh",
        "KEY_UID": str(key_uid),
        "KEY": key_line if key_line is not None else authorized_key_line(),
        "KEY_TTL_H": str(FLEETBACKUP_KEY_TTL_H),
        "ROOT": home_root,
        "RUN_PATH": FLEETBACKUP_RUN_PATH,
        "LOCK": lock,
        "LOCK_WAIT": str(lock_wait_s),
        "BUSY_RC": str(FLEETBACKUP_LOCK_BUSY_RC),
        "SKIP": " ".join(paused),
        "ACCOUNT_TIMEOUT": str(account_timeout_s),
        "CHECK_TIMEOUT": str(FLEETBACKUP_CHECK_TIMEOUT_S),
        "QUICK_CHECK": str(FLEETBACKUP_QUICK_CHECK_TIMEOUT_S),
        "OUTPUT_CAP": str(FLEETBACKUP_OUTPUT_CAP_KIB),
        "WITHDRAW_EXPIRY": FLEETBACKUP_WITHDRAW_EXPIRY,
        "BUDGET": str(budget_s),
        "CHECK_PY": _CHECK_PY,
    }
    out = _ACL_SCRIPT_TEMPLATE
    for name, value in values.items():
        out = out.replace("@@%s@@" % name, shlex.quote(value))
    assert "@@" not in out, "unfilled placeholder in the fleetbackup ACL script"
    return out


def render_fleetbackup_push_block(acl_script=FLEETBACKUP_ACL_SCRIPT_PATH,
                                  wrapper=FLEETBACKUP_RRSYNC_WRAPPER_PATH,
                                  user=FLEETBACKUP_USER, home=FLEETBACKUP_HOME,
                                  shell=FLEETBACKUP_SHELL, rrsync=RRSYNC_PATH,
                                  unit=FLEETBACKUP_REFRESH_UNIT, aquota="/aquota.user"):
    """The push leg (root, inside ``build_apply_script()``): its OWN subshell
    with ``set -e`` effective, so a failure never skips a later guard step; the
    status lands in ``fb_rc`` and ``FLEETBACKUP_DEFERRED_EXIT`` exits with it at
    the very end. Checks rrsync, the wrapper, the installed script and the acl
    tools (fail loud, nothing is apt-installed), creates the account (never
    adopts a foreign one), locks its password, makes its home and ``.ssh``
    root-owned, then QUEUES the refresh in the hourly unit (``systemctl start
    --no-block``, the #1140 recount precedent): a refresh walks every
    account's two trees and must never ride the push's 180 s ssh call. That
    unit only runs with quota provisioned, so a missing ``/aquota.user`` fails
    LOUD here (fleetbackup would never get a key)."""
    q = shlex.quote
    return (
        '# --- #1387: read-only fleetbackup login (own subshell; exit deferred) ---\n'
        'set +e\n'
        '(\n'
        '    set -euo pipefail\n'
        '    fb_die() { printf \'  ⚠ FLEETBACKUP: %%s\\n\' "$*" >&2; exit 1; }\n'
        '    [ -x %(rrsync)s ] || fb_die "%(rrsync_raw)s missing (rsync >= 3.2.4 ships it) — nothing changed"\n'
        '    [ -f %(wrapper)s ] || fb_die "the forced-command wrapper %(wrapper_raw)s is missing — nothing changed"\n'
        '    [ -f %(script)s ] || fb_die "%(script_raw)s is missing — nothing changed"\n'
        '    for t in setfacl getfacl; do\n'
        '        command -v "$t" >/dev/null 2>&1 || fb_die "$t missing (install the acl package) — nothing changed"\n'
        '    done\n'
        '    sig=$(getent passwd %(user)s | cut -d: -f6,7) || sig=""\n'
        '    if [ -z "$sig" ]; then\n'
        '        useradd --system --home-dir %(home)s --no-create-home --shell %(shell)s \\\n'
        '            --comment %(gecos)s %(user)s || fb_die "useradd %(user_raw)s failed"\n'
        '        echo "  fleetbackup: created the system account %(user_raw)s"\n'
        '        sig=$(getent passwd %(user)s | cut -d: -f6,7) || sig=""\n'
        '    fi\n'
        '    [ "$sig" = %(sig)s ] || fb_die "an existing %(user_raw)s account is not airuleset\'s ($sig) — left untouched"\n'
        '    pw=$(passwd -S %(user)s | awk \'{print $2}\') || pw=""\n'
        '    if [ "$pw" != L ]; then usermod -L %(user)s || fb_die "usermod -L %(user_raw)s failed"; fi\n'
        '    for d in %(home)s %(ssh)s; do\n'
        '        if [ -L "$d" ] || { [ -e "$d" ] && [ ! -d "$d" ]; }; then fb_die "$d is not a plain directory — refusing"; fi\n'
        '        install -d -o root -g root -m 0755 "$d" || fb_die "install -d $d failed"\n'
        '    done\n'
        '    [ -f %(aquota)s ] || fb_die "quota is not provisioned (%(aquota_raw)s missing): %(unit_raw)s, which runs the ACL refresh, cannot start — %(user_raw)s gets no key"\n'
        '    systemctl start --no-block %(unit)s || fb_die "could not queue %(unit_raw)s (the ACL refresh + read-back)"\n'
        '    echo "  fleetbackup: ACL refresh + read-back queued in %(unit_raw)s (journalctl -u %(unit_raw)s)"\n'
        ')\n'
        'fb_rc=$?\n'
        'set -e\n'
        'if [ "$fb_rc" -ne 0 ]; then\n'
        '    echo "  ⚠ FLEETBACKUP FAILED rc=$fb_rc — every other guard step still runs; exit deferred to the end" >&2\n'
        'fi'
        % {"rrsync": q(rrsync), "rrsync_raw": rrsync, "wrapper": q(wrapper), "wrapper_raw": wrapper,
           "script": q(acl_script), "script_raw": acl_script,
           "user": q(user), "user_raw": user, "home": q(home), "shell": q(shell),
           "gecos": q(FLEETBACKUP_GECOS), "sig": q(_passwd_signature(home, shell)),
           "ssh": q(home.rstrip("/") + "/.ssh"), "aquota": q(aquota), "aquota_raw": aquota,
           "unit": q(unit), "unit_raw": unit}
    )


def render_fleetbackup_hourly_call(acl_script=FLEETBACKUP_ACL_SCRIPT_PATH):
    """The hourly quota-ceiling script's call, AFTER its limits block: it
    releases the quota lock (fd 9) first, because the refresh takes its own and
    may walk large trees. A busy refresh lock means another refresh (a manual
    run) holds it: LOUD, not a failure. Leaves the status in ``fb_rc``."""
    q = shlex.quote(acl_script)
    return (
        '# #1387: the fleetbackup ACL refresh (hourly, and queued by every push),\n'
        '# with its own lock — the quota lock is released first: no quota writer\n'
        '# ever waits on a tree walk\n'
        'exec 9>&-\n'
        'fb_rc=0\n'
        'if [ -f %s ]; then\n'
        '    /bin/bash %s || fb_rc=$?\n'
        'else\n'
        '    echo "  ⚠ quota-ceiling: %s missing — fleetbackup ACL refresh not run" >&2\n'
        'fi\n'
        'if [ "$fb_rc" -eq %d ]; then\n'
        '    echo "  ⚠ quota-ceiling: fleetbackup refresh lock busy (another refresh is running)" >&2\n'
        '    fb_rc=0\n'
        'fi\n'
        % (q, q, acl_script, FLEETBACKUP_LOCK_BUSY_RC)
    )
