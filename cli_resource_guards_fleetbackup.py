"""#1387 — read-only ``fleetbackup`` access on subdev: ``~/uploads`` and
``~/.claude/work-products`` of every shared-stream account, nothing else.

Owner decision 20.11.2026 (airuleset#1380): fleet-backup (dev1) backs up those
two directories of every subdev account. The box holds external developers'
homes and its root is reachable only through the gk VPS, so the dev1 key gets
NO sudo there (the controller's #1351 model is declined): a ``--system``
account whose key runs only ``rrsync -ro /home``, plus POSIX ACLs.

Decided design (issue comments 7040909733, 7040933347, 7040947380):
  * ``u:fleetbackup:--x`` on the home and on ``.claude``; ``r-x`` on the dirs
    and ``r--`` (``-n``: the file's own mask is kept, so a 0600 file stays
    closed and is only COUNTED, never chmod-ed) on the files of the two trees,
    plus a default ACL with explicit ``g::---,o::---`` on their dirs. No
    default ACL on the home or ``.claude`` (it would override every umask).
  * Traversal makes every OTHER child reachable through ACL_OTHER, so each one
    (home and ``.claude``) gets a named ``u:fleetbackup:---``.
  * Every setfacl runs AS the account (``runuser -u <u>``), never as root: root
    acting on user-owned paths would follow a swapped symlink.
  * The same idempotent refresh runs at push and from the hourly quota-ceiling
    oneshot, under the quota lock.

Mechanism: ONE root-owned installed script (``FLEETBACKUP_ACL_SCRIPT_PATH``,
written by ``cli_resource_guards.guard_files()``) with four modes — the root
``refresh``, ``apply`` (as the account), ``check`` (as fleetbackup: the
read-back is the kernel's own access check) and ``records``. The refresh owns
``authorized_keys``: it writes the key line only after a clean identity check
and a read-back with no unresolved leak, otherwise it withdraws the key, so a
failure is fail-closed. Pure renderers + constants, stdlib only.
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
# The push waits this long for the quota lock (the hourly unit may hold it); a
# busy lock exits FLEETBACKUP_LOCK_BUSY_RC and the hourly run applies it.
FLEETBACKUP_LOCK_WAIT_S = 60
FLEETBACKUP_LOCK_BUSY_RC = 75
# The account-side and fleetbackup-side runs see no root environment.
FLEETBACKUP_RUN_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
FLEETBACKUP_TREES = ("uploads", ".claude/work-products")
# The deferred exit closing build_apply_script(): the fleetbackup leg ran in its
# own subshell earlier, so its failure never skipped any other guard step.
FLEETBACKUP_DEFERRED_EXIT = (
    '# #1387: the fleetbackup leg\'s exit, deferred until every other step ran\n'
    '[ "${fb_rc:-0}" -eq 0 ] || exit "$fb_rc"')


def authorized_key_line():
    """The ONE line of fleetbackup's ``authorized_keys`` on subdev: dev1 only,
    ``restrict`` (no pty/forwarding/rc), and a forced read-only rrsync."""
    return ('from="%s",restrict,command="%s -ro %s" %s fleet-backup-manager@dev1'
            % (MANAGER_FROM, RRSYNC_PATH, RRSYNC_ROOT, MANAGER_KEY))


def _passwd_signature(home=FLEETBACKUP_HOME, shell=FLEETBACKUP_SHELL):
    """``<home>:<shell>`` — how an account OUR useradd made is recognised; a
    fleetbackup account with any other signature is foreign and never touched."""
    return "%s:%s" % (home, shell)


# The installed script. `@@NAME@@` placeholders are filled (shell-quoted) by
# render_fleetbackup_acl_script(); everything else is literal bash.
_ACL_SCRIPT_TEMPLATE = r'''#!/bin/bash
# Managed by airuleset (#1387) — read-only fleetbackup access to ~/uploads and
# ~/.claude/work-products of every shared-stream account, nothing else.
# Run as root by the push (under the quota lock) and by the hourly
# quota-ceiling unit (which holds that lock). Every setfacl runs AS the
# account (`apply`), never as root; the read-back runs AS fleetbackup (`check`).
# Modes: refresh (default, root) | apply <fb> <home> grant|hold | check | records <home>
set -euo pipefail
FB=@@USER@@
FB_SIG=@@SIG@@
FB_SSH=@@SSH@@
FB_KEY_UID=@@KEY_UID@@
FB_KEY=@@KEY@@
FB_ROOT=@@ROOT@@
FB_PATH=@@RUN_PATH@@
FB_SELF=$(readlink -f -- "$0")   # absolute: the refresh cd's to / before calling itself
FB_SHOW_MAX=20
FB_WITHDRAWN='# airuleset 1387: key withdrawn — the fleetbackup read-back found access it must not have; a clean refresh restores it'
# getfacl -c output(s) -> "ok <owning-group effective perms>" | "refuse" (a named
# entry for anyone but fleetbackup: recalculating the mask would widen it)
FB_ACL_AWK='
BEGIN { foreign = 0; g = "" }
{
    split($0, f, ":")
    if (f[1] == "default") { t = f[2]; who = f[3] } else { t = f[1]; who = f[2] }
    if ((t == "user" || t == "group") && who != "" && !(t == "user" && who == fb)) foreign = 1
    if (f[1] == "group" && f[2] == "") {
        g = substr(f[3], 1, 3)
        if (match($0, /#effective:[-r][-w][-x]/)) g = substr($0, RSTART + 11, 3)
    }
}
END { if (foreign || g !~ /^[-r][-w][-x]$/) print "refuse"; else print "ok " g }'

fb_say() { printf '  fleetbackup: %s\n' "$*"; }
fb_warn() { printf '  ⚠ FLEETBACKUP: %s\n' "$*" >&2; }
fb_realdir() { [ -d "$1" ] && [ ! -L "$1" ]; }

# ---------------------------------------------------------------- account side
fb_traverse() { # $1 fb, $2 dir: u:<fb>:--x, the owning group keeps exactly its effective perms
    local acl v
    acl=$(getfacl -p -c -- "$2") || return 1
    v=$(printf '%s\n' "$acl" | awk -v fb="$1" "$FB_ACL_AWK") || return 1
    case "$v" in
        "ok "???) setfacl -m "g::${v#ok },u:$1:--x" -- "$2" ;;
        *) fb_warn "$(printf '%q' "$2") carries a named ACL entry for another user or group — not opened (a mask change would widen it)"
           return 1 ;;
    esac
}

fb_tree() { # $1 fb, $2 tree root: dirs r-x + default ACL, files r-- without touching their mask
    local v rc=0
    v=$({ find "$2" -xdev -type d -exec getfacl -p -c -- {} + 2>/dev/null || true; } \
        | awk -v fb="$1" "$FB_ACL_AWK") || v=refuse
    case "$v" in
        "ok "???) ;;
        *) fb_warn "a dir under $(printf '%q' "$2") carries a named ACL entry for another user or group — tree not granted"
           return 1 ;;
    esac
    # g::--- first: a recalculated mask must never re-widen a group the owner closed
    find "$2" -xdev -type d -exec setfacl -m "g::---,u:$1:r-x,d:u:$1:r-x,d:g::---,d:o::---" -- {} + || rc=1
    find "$2" -xdev -type f -exec setfacl -n -m "u:$1:r--" -- {} + || rc=1
    return "$rc"
}

fb_apply() { # as the account: $1 fb, $2 home, $3 grant|hold
    local fb="$1" h="$2" mode="$3" rc=0 granted=0
    cd /
    # every other child of the home and of .claude: a named deny, mask untouched
    find "$h" -mindepth 1 -maxdepth 1 \( \( -name .claude -o -name uploads \) -type d \) -prune \
        -o ! -type l -exec setfacl -n -m "u:$fb:---" -- {} + || rc=1
    if fb_realdir "$h/.claude"; then
        find "$h/.claude" -mindepth 1 -maxdepth 1 \( -name work-products -type d \) -prune \
            -o ! -type l -exec setfacl -n -m "u:$fb:---" -- {} + || rc=1
    fi
    if [ "$mode" != grant ]; then
        setfacl -n -m "u:$fb:---" -- "$h" || rc=1
        return "$rc"
    fi
    if fb_realdir "$h/uploads"; then
        fb_tree "$fb" "$h/uploads" || rc=1
        granted=1
    fi
    if fb_realdir "$h/.claude" && fb_realdir "$h/.claude/work-products"; then
        if fb_traverse "$fb" "$h/.claude"; then
            fb_tree "$fb" "$h/.claude/work-products" || rc=1
            granted=1
        else
            rc=1
        fi
    fi
    if [ "$granted" -eq 1 ]; then
        fb_traverse "$fb" "$h" || rc=1
    fi
    return "$rc"
}

# ------------------------------------------------------------ fleetbackup side
fb_show() {
    if [ "$FB_SHOWN" -lt "$FB_SHOW_MAX" ]; then
        printf '  ⚠ FLEETBACKUP-READBACK %s: %q\n' "$1" "$2" >&2
    fi
    FB_SHOWN=$((FB_SHOWN + 1))
}

fb_check() { # as fleetbackup: NUL records <kind> <mode> <path> on stdin
    local kind mode p leak=0 miss=0 ok=0 priv=0
    FB_SHOWN=0
    cd /
    while IFS= read -r -d '' kind && IFS= read -r -d '' mode && IFS= read -r -d '' p; do
        case "$kind" in
            C)  if [ -r "$p" ] || [ -w "$p" ] || [ -x "$p" ]; then
                    leak=$((leak + 1)); fb_show "LEAK reachable" "$p"
                fi ;;
            T)  if [ -r "$p" ] || [ -w "$p" ]; then
                    leak=$((leak + 1)); fb_show "LEAK listable" "$p"
                fi ;;
            D)  if [ -r "$p" ] && [ -x "$p" ]; then
                    ok=$((ok + 1))
                else
                    miss=$((miss + 1)); fb_show "MISS dir" "$p"
                fi ;;
            F)  case "$mode" in
                    ''|*[!0-7]*) miss=$((miss + 1)); fb_show "MISS bad mode" "$p" ;;
                    *)  if [ -r "$p" ]; then
                            ok=$((ok + 1))
                        elif [ $(( (8#$mode >> 3) & 4 )) -ne 0 ]; then
                            miss=$((miss + 1)); fb_show "MISS file" "$p"
                        else
                            priv=$((priv + 1))
                        fi ;;
                esac ;;
            *)  miss=$((miss + 1)); fb_show "MISS bad record" "$kind" ;;
        esac
    done
    echo "leak=$leak miss=$miss readable=$ok private=$priv"
}

# ------------------------------------------------------------------- root side
fb_tree_records() {
    find "$1" -xdev \( -type d -printf 'D\0%m\0%p\0' \) -o \( -type f -printf 'F\0%m\0%p\0' \)
}

fb_records() { # $1 home: what fleetbackup may and may not reach, as NUL records
    local h="$1"
    printf 'T\0-\0%s\0' "$h"
    find "$h" -mindepth 1 -maxdepth 1 \( \( -name .claude -o -name uploads \) -type d \) -prune \
        -o ! -type l -printf 'C\0%m\0%p\0'
    if fb_realdir "$h/.claude"; then
        printf 'T\0-\0%s\0' "$h/.claude"
        find "$h/.claude" -mindepth 1 -maxdepth 1 \( -name work-products -type d \) -prune \
            -o ! -type l -printf 'C\0%m\0%p\0'
    fi
    if fb_realdir "$h/uploads"; then fb_tree_records "$h/uploads"; fi
    if fb_realdir "$h/.claude" && fb_realdir "$h/.claude/work-products"; then
        fb_tree_records "$h/.claude/work-products"
    fi
}

fb_as() { # $1 user, rest: a mode of this script, run with no root environment
    local u="$1"; shift
    runuser -u "$u" -- env -i PATH="$FB_PATH" LC_ALL=C /bin/bash "$FB_SELF" "$@" 9>&-
}

fb_closed() { # $1 path: 0 only when fleetbackup has no access to it at all
    local s
    s=$(printf 'C\0-\0%s\0' "$1" | fb_as "$FB" check) || return 1
    [ "${s%% *}" = "leak=0" ]
}

fb_unclosable() { # $1 account, $2 home: children the account cannot close but others can reach
    { find "$2" -mindepth 1 -maxdepth 1 \( \( -name .claude -o -name uploads \) -type d \) -prune \
          -o ! -type l ! -user "$1" -perm /o=rwx -print0
      if fb_realdir "$2/.claude"; then
          find "$2/.claude" -mindepth 1 -maxdepth 1 \( -name work-products -type d \) -prune \
              -o ! -type l ! -user "$1" -perm /o=rwx -print0
      fi
    } | while IFS= read -r -d '' p; do printf '%q ' "$p"; done
}

fb_account() { # $1 account, $2 home -> 0 ok | 1 failed, nothing open | 2 open and not closable
    #              | 3 not a managed home (the caller treats it like any non-stream entry)
    local u="$1" h="$2" mode=grant rc=0 held sum l m r p
    if [ -L "$h" ]; then
        fb_warn "$u: home $(printf '%q' "$h") is a symlink — not granted"; return 3
    fi
    if [ "$(getent passwd "$u" | cut -d: -f6)" != "$h" ]; then
        fb_warn "$u: $(printf '%q' "$h") is not the home of an account named $u — not granted"; return 3
    fi
    held=$(fb_unclosable "$u" "$h") || held="(unreadable)"
    if [ -n "$held" ]; then
        fb_warn "$u: not owned by $u and open to others, so $u cannot close it: $held— traversal HELD"
        mode=hold; rc=1
    fi
    if ! fb_as "$u" apply "$FB" "$h" "$mode" </dev/null; then
        fb_warn "$u: the account-side apply reported a failure (above)"; rc=1
    fi
    for p in uploads .claude/work-products; do
        if { [ -e "$h/$p" ] || [ -L "$h/$p" ]; } && ! { fb_realdir "$h/.claude" && fb_realdir "$h/$p"; }; then
            fb_warn "$u: $(printf '%q' "$h/$p") is not a plain directory — not granted"; rc=1
        fi
    done
    sum=$(fb_records "$h" | fb_as "$FB" check) || sum=""
    case "$sum" in
        "leak="*" miss="*" readable="*" private="*) ;;
        *) fb_warn "$u: the read-back did not run — treated as open"
           sum="leak=1 miss=0 readable=0 private=0" ;;
    esac
    read -r l m r p <<<"$sum"
    l=${l#leak=}; m=${m#miss=}; r=${r#readable=}; p=${p#private=}
    case "$l:$m:$r:$p" in
        *[!0-9:]*|*::*|:*|*:) fb_warn "$u: unparseable read-back '$sum' — treated as open"; l=1 ;;
    esac
    if [ "$l" -ne 0 ]; then
        fb_warn "$u: $l path(s) reachable that must not be — home closed to $FB"
        fb_as "$u" apply "$FB" "$h" hold </dev/null || true
        if ! fb_closed "$h"; then
            fb_warn "$u: home still reachable after closing it — UNRESOLVED"; return 2
        fi
        return 1
    fi
    if [ "$m" -ne 0 ]; then
        fb_warn "$u: $m path(s) under the two dirs are not readable (listed above)"; rc=1
    fi
    fb_say "$u: readable=$r private=$p (files without group read stay closed, counted)"
    return "$rc"
}

fb_foreign_homes() { # every non-stream entry of FB_ROOT: closed when open, then read back
    local owner p cur sum rc=0
    while IFS= read -r -d '' owner && IFS= read -r -d '' p; do
        [ -z "${FB_STREAMS[${p##*/}]+x}" ] || continue
        cur=$(getfacl -p -c -- "$p" 2>/dev/null) || cur=""
        case "$cur" in *"user:$FB:---"*) continue ;; esac
        if fb_as "$owner" acl-deny "$FB" "$p" </dev/null; then
            fb_say "closed $(printf '%q' "$p") (not a shared-stream home, open to others)"
        else
            fb_warn "could not close $(printf '%q' "$p") as $owner"; rc=1
        fi
    done < <(find "$FB_ROOT" -mindepth 1 -maxdepth 1 ! -type l -perm /o=rwx -printf '%u\0%p\0')
    sum=$(while IFS= read -r -d '' p; do
              [ -z "${FB_STREAMS[${p##*/}]+x}" ] || continue
              printf 'C\0-\0%s\0' "$p"
          done < <(find "$FB_ROOT" -mindepth 1 -maxdepth 1 ! -type l -print0) \
          | fb_as "$FB" check) || sum=""
    case "$sum" in
        "leak=0 "*) return "$rc" ;;
        "leak="*) fb_warn "a non-stream entry of $FB_ROOT is still reachable — UNRESOLVED"; return 2 ;;
        *) fb_warn "the read-back of $FB_ROOT did not run — treated as open"; return 2 ;;
    esac
}

fb_identity() { # fleetbackup is exactly the locked, group-less, sudo-less account it must be
    local pw ng out ok=0
    pw=$(passwd -S "$FB" 2>/dev/null | awk '{print $2}') || pw=""
    if [ "$pw" != L ]; then fb_warn "the password of $FB is not locked (passwd -S: ${pw:-?})"; ok=1; fi
    ng=$(id -G "$FB" 2>/dev/null | wc -w) || ng=0
    if [ "$ng" -ne 1 ]; then fb_warn "$FB is not in exactly its own group (id -G: $ng groups)"; ok=1; fi
    out=$(LC_ALL=C sudo -n -l -U "$FB" 2>&1) || true
    case "$out" in
        *"is not allowed to run sudo"*) ;;
        *) fb_warn "$FB may have sudo rules — sudo -l: ${out%%$'\n'*}"; ok=1 ;;
    esac
    return "$ok"
}

fb_rootdir() { # a real dir owned by FB_KEY_UID, not writable by group or others
    [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -c %u -- "$1")" = "$FB_KEY_UID" ] \
        && [ $(( 8#$(stat -c %a -- "$1") & 8#022 )) -eq 0 ]
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
    if printf '%s\n' "$1" >"$t" && chmod 0644 "$t" && mv -f "$t" "$k"; then
        fb_say "wrote $k"
        return 0
    fi
    rm -f "$t"
    return 1
}

fb_withdraw() {
    fb_key_write "$FB_WITHDRAWN" || fb_warn "could not withdraw the key"
    fb_warn "key WITHDRAWN — fleetbackup has no login until a clean refresh"
}

fb_refresh() {
    local sig rc=0 r t home u unresolved=0
    local -A FB_STREAMS=()
    cd /
    sig=$(getent passwd "$FB" | cut -d: -f6,7) || sig=""
    if [ "$sig" != "$FB_SIG" ]; then
        fb_say "no airuleset $FB account on this box — ACL refresh skipped"
        return 0
    fi
    for t in setfacl getfacl runuser; do
        if ! command -v "$t" >/dev/null 2>&1; then
            fb_warn "$t missing (setfacl/getfacl come from the acl package) — nothing refreshed"
            fb_withdraw; return 2
        fi
    done
    if ! fb_identity; then fb_withdraw; return 2; fi
    for home in "$FB_ROOT"/*; do
        [ -d "$home" ] || continue
        u=${home##*/}
        grep -q "shared-stream" "$home/.claude/airuleset-box-class" 2>/dev/null || continue
        r=0; fb_account "$u" "$home" || r=$?
        [ "$r" -eq 0 ] || rc=1
        [ "$r" -ne 2 ] || unresolved=1
        # a refused home is not managed: fb_foreign_homes closes it if it is open
        [ "$r" -eq 3 ] || FB_STREAMS[$u]=1
    done
    r=0; fb_foreign_homes || r=$?
    [ "$r" -eq 0 ] || rc=1
    [ "$r" -lt 2 ] || unresolved=1
    if [ "$unresolved" -ne 0 ]; then fb_withdraw; return 2; fi
    fb_key_write "$FB_KEY" || rc=1
    fb_say "ACL refresh + read-back done (${#FB_STREAMS[@]} accounts, rc=$rc)"
    return "$rc"
}

case "${1:-refresh}" in
    refresh) rc=0; fb_refresh || rc=$?; exit "$rc" ;;
    apply) shift; fb_apply "$@" ;;
    acl-deny) cd /; setfacl -n -m "u:$2:---" -- "$3" ;;
    check) fb_check ;;
    records) fb_records "$2" ;;
    *) echo "usage: $FB_SELF [refresh | apply <fb> <home> grant|hold | acl-deny <fb> <path> | check | records <home>]" >&2
       exit 64 ;;
esac
'''


def render_fleetbackup_acl_script(user=FLEETBACKUP_USER, home=FLEETBACKUP_HOME,
                                  shell=FLEETBACKUP_SHELL, home_root=RRSYNC_ROOT,
                                  key_uid=0, key_line=None):
    """The installed ``fleetbackup-acl.sh`` (see the module docstring). The
    keyword arguments exist for the tests (a temp home root, a non-root key
    owner); production uses the defaults."""
    values = {
        "USER": user,
        "SIG": _passwd_signature(home, shell),
        "SSH": home.rstrip("/") + "/.ssh",
        "KEY_UID": str(key_uid),
        "KEY": key_line if key_line is not None else authorized_key_line(),
        "ROOT": home_root,
        "RUN_PATH": FLEETBACKUP_RUN_PATH,
    }
    out = _ACL_SCRIPT_TEMPLATE
    for name, value in values.items():
        out = out.replace("@@%s@@" % name, shlex.quote(value))
    assert "@@" not in out, "unfilled placeholder in the fleetbackup ACL script"
    return out


def render_fleetbackup_push_block(lock_path, lock_wait_s=FLEETBACKUP_LOCK_WAIT_S,
                                  acl_script=FLEETBACKUP_ACL_SCRIPT_PATH,
                                  user=FLEETBACKUP_USER, home=FLEETBACKUP_HOME,
                                  shell=FLEETBACKUP_SHELL, rrsync=RRSYNC_PATH):
    """The push leg (root, inside ``build_apply_script()``): its OWN subshell
    with ``set -e`` effective, so a failure never skips a later guard step; the
    status lands in ``fb_rc`` and ``FLEETBACKUP_DEFERRED_EXIT`` exits with it at
    the very end. Checks rrsync + the acl tools (fail loud, nothing is
    apt-installed), creates the account (never adopts a foreign one), locks its
    password, makes its home and ``.ssh`` root-owned, then runs the refresh
    under the quota lock (``flock -o``: the lock fd is not passed to the
    refresh, whose children run as the accounts)."""
    q = shlex.quote
    return (
        '# --- #1387: read-only fleetbackup login (own subshell; exit deferred) ---\n'
        'set +e\n'
        '(\n'
        '    set -euo pipefail\n'
        '    fb_die() { printf \'  ⚠ FLEETBACKUP: %%s\\n\' "$*" >&2; exit 1; }\n'
        '    [ -x %(rrsync)s ] || fb_die "%(rrsync_raw)s missing (rsync >= 3.2.4 ships it) — nothing changed"\n'
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
        '    flock -o -E %(busy)d -w %(wait)d %(lock)s /bin/bash %(script)s\n'
        ')\n'
        'fb_rc=$?\n'
        'set -e\n'
        'if [ "$fb_rc" -eq %(busy)d ]; then\n'
        '    echo "  ⚠ FLEETBACKUP: quota lock busy — ACL refresh skipped this push (the hourly unit applies it)" >&2\n'
        'fi\n'
        'if [ "$fb_rc" -ne 0 ]; then\n'
        '    echo "  ⚠ FLEETBACKUP FAILED rc=$fb_rc — every other guard step still runs; exit deferred to the end" >&2\n'
        'fi'
        % {"rrsync": q(rrsync), "rrsync_raw": rrsync, "user": q(user), "user_raw": user,
           "home": q(home), "shell": q(shell), "gecos": q(FLEETBACKUP_GECOS),
           "sig": q(_passwd_signature(home, shell)), "ssh": q(home.rstrip("/") + "/.ssh"),
           "busy": FLEETBACKUP_LOCK_BUSY_RC, "wait": lock_wait_s, "lock": q(lock_path),
           "script": q(acl_script)}
    )


def render_fleetbackup_hourly_call(acl_script=FLEETBACKUP_ACL_SCRIPT_PATH):
    """The hourly quota-ceiling script's call: it already holds the quota lock
    on fd 9, the child gets the fd closed (its children run as the accounts).
    Leaves the status in ``fb_rc`` for the script's own exit."""
    q = shlex.quote(acl_script)
    return (
        '# #1387: the fleetbackup ACL refresh, hourly, under this same lock\n'
        'fb_rc=0\n'
        'if [ -f %s ]; then\n'
        '    /bin/bash %s 9>&- || fb_rc=$?\n'
        'else\n'
        '    echo "  ⚠ quota-ceiling: %s missing — fleetbackup ACL refresh not run" >&2\n'
        'fi\n'
        % (q, q, acl_script)
    )
