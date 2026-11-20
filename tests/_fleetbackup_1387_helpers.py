"""Shared harness for the #1387 fleetbackup ACL tests.

The installed script runs REAL setfacl/getfacl/find on temp files owned by the
test user, with a real system account (`nobody`, `daemon`) standing in for
fleetbackup, because a named ACL entry needs a resolvable user. Root-only tools
are stubs: `runuser` drops `-u <user> --` and runs the command as the test
user — except a fleetbackup-side `check`, whose answers come from a scenario
queue (a non-root test cannot ask the kernel as another uid; `check` itself is
tested separately with owner-permission proxies). Every record stream a check
receives is SAVED (checks/<n>.bin), so a test can assert what was asked.
"""
import getpass
import os
import pwd
import subprocess

import cli_resource_guards_fleetbackup as fbm

ME = getpass.getuser()
KEYLINE = 'from="10.0.0.1",restrict,command="/bin/sh /w" ssh-ed25519 AAAAtest k@test'

_RUNUSER = r'''#!/bin/bash
[ "$1" = -u ] || exit 90
u="$2"; shift 2
[ "${1:-}" = -- ] && shift
echo "$u $*" >> "$FBT_LOG"
for fd in 8 9; do
    if [ -e "/proc/$$/fd/$fd" ]; then echo "FD$fd-OPEN $u $*" >> "$FBT_LOG"; fi
done
case " $* " in
    *" check "*)
        if [ "$u" = "$FBT_FB" ] && [ -f "$FBT_QUEUE" ]; then
            n=$(ls "$FBT_CHECKS" | wc -l)
            cat > "$FBT_CHECKS/$n.bin"
            [ -z "${FBT_CHECK_DELAY:-}" ] || sleep "$FBT_CHECK_DELAY"
            line=$(head -n 1 "$FBT_QUEUE")
            tail -n +2 "$FBT_QUEUE" > "$FBT_QUEUE.t"; mv "$FBT_QUEUE.t" "$FBT_QUEUE"
            [ -n "$line" ] || exit 91
            echo "$line"; exit 0
        fi ;;
    *" apply "*" grant "*)
        [ -z "${FBT_STALL_APPLY:-}" ] || sleep 30
        if [ -n "${FBT_SPAM_APPLY:-}" ]; then   # stderr off: bash would report the kill INTO the cap
            { head -c 400000 /dev/zero | tr '\0' x; echo "TR_RC=${PIPESTATUS[1]}" >> "$FBT_LOG"; } 2>/dev/null
        fi
        if [ -n "${FBT_ESC_APPLY:-}" ]; then printf '\033[2Jforged\n'; fi ;;
esac
exec "$@"
'''

_CHAGE = r'''#!/bin/bash
if [ -e "$FBT_MARK" ]; then mk=yes; else mk=no; fi
echo "chage $* marker=$mk" >> "$FBT_LOG"
[ -z "${FBT_FAIL_CHAGE:-}" ] || exit 1
[ "$1" = -E ] || exit 2
v="$2"; [ "$v" = -1 ] && v=""
tmp="$FBT_SHADOW.t"
while IFS=: read -r n p a b c d e x r; do
    if [ "$n" = "$3" ]; then x="$v"; fi
    echo "$n:$p:$a:$b:$c:$d:$e:$x:$r"
done < "$FBT_SHADOW" > "$tmp"
mv "$tmp" "$FBT_SHADOW"
'''

# root-side find: fails a -print* walk whose arguments contain FBT_FAIL_FIND
_FIND = r'''#!/bin/bash
rc=0; /usr/bin/find "$@" || rc=$?
if [ -n "${FBT_FAIL_FIND:-}" ]; then
    case "$*" in *"$FBT_FAIL_FIND"*-print*) exit 1 ;; esac
fi
exit "$rc"
'''

_GETENT = r'''#!/bin/bash
case "$1" in passwd) db="$FBT_PASSWD" ;; shadow) db="$FBT_SHADOW" ;; *) exit 2 ;; esac
while IFS= read -r line; do
    [ "${line%%:*}" = "$2" ] && { echo "$line"; exit 0; }
done < "$db"
exit 2
'''

# root-side setfacl (on the test PATH; the account side runs with a clean PATH
# and never reaches it): logged, fails on a path containing FBT_FAIL_SETFACL
_SETFACL = r'''#!/bin/bash
echo "root-setfacl $*" >> "$FBT_LOG"
if [ -n "${FBT_STALL_SETFACL:-}" ]; then
    case "$*" in *"$FBT_STALL_SETFACL"*) sleep 30 ;; esac
fi
if [ -n "${FBT_FAIL_SETFACL:-}" ]; then
    case "$*" in *"$FBT_FAIL_SETFACL"*) echo "setfacl: forced failure" >&2; exit 1 ;; esac
fi
if [ -n "${FBT_FAIL_SETFACL_LAST:-}" ] && [ "${@: -1}" = "$FBT_FAIL_SETFACL_LAST" ]; then
    echo "setfacl: No space left on device" >&2; exit 1
fi
if [ -n "${FBT_FAIL_ONCE:-}" ] && [ ! -e "$FBT_LOG.failed-once" ]; then
    case "$*" in *"$FBT_FAIL_ONCE"*) : > "$FBT_LOG.failed-once"; echo "setfacl: vanished" >&2; exit 1 ;; esac
fi
exec /usr/bin/setfacl "$@"
'''

_ID = r'''#!/bin/bash
if [ "$*" = -un ] && [ -n "${FBT_ID_UN:-}" ]; then echo "$FBT_ID_UN"; exit 0; fi
for a in "$@"; do
    if [ "$a" = "$FBT_FB" ]; then
        case "$1" in
            -G) echo "${FBT_ID_G:-1}" ;;
            -gn) echo "${FBT_ID_GN:-$FBT_FB}" ;;
            -Gn) echo "${FBT_ID_GN:-$FBT_FB}" ;;
        esac
        exit 0
    fi
done
exec /usr/bin/id "$@"
'''


def write_exe(path, body):
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)


def passwd_line(name, home, shell="/bin/bash"):
    try:
        p = pwd.getpwnam(name)
        uid, gid = p.pw_uid, p.pw_gid
    except KeyError:
        uid = gid = 4242
    return "%s:x:%d:%d::%s:%s" % (name, uid, gid, home, shell)


class Box:
    """A temp 'box': <td>/home/<accounts>, <td>/fbhome/.ssh, stub bin dir and
    the rendered script with the test's fleetbackup stand-in."""

    def __init__(self, td, fb="nobody", accounts=(ME,), pw_status="L",
                 sudo_out="User x is not allowed to run sudo on box.", fb_sig_home=None,
                 expiry="", paused=(), account_timeout_s=60, budget_s=None, output_cap_kib=None):
        self.td, self.fb = td, fb
        self.root = os.path.join(td, "home")
        self.fbhome = os.path.join(td, "fbhome")
        self.ssh = os.path.join(self.fbhome, ".ssh")
        self.keys = os.path.join(self.ssh, "authorized_keys")
        self.bindir = os.path.join(td, "bin")
        self.log = os.path.join(td, "calls.log")
        self.queue = os.path.join(td, "queue")
        self.checks = os.path.join(td, "checks")
        self.lock = os.path.join(td, "fb.lock")
        self.extra_env = {}
        for d in (self.root, self.ssh, self.bindir, self.checks):
            os.makedirs(d, exist_ok=True)
        os.chmod(self.fbhome, 0o755)
        os.chmod(self.ssh, 0o755)
        lines = [passwd_line(fb, fb_sig_home or self.fbhome, "/bin/sh")]
        lines += [passwd_line(a, os.path.join(self.root, a)) for a in accounts]
        self.passwd = os.path.join(td, "passwd")
        with open(self.passwd, "w") as f:
            f.write("\n".join(lines) + "\n")
        self.shadow = os.path.join(td, "shadow")
        with open(self.shadow, "w") as f:
            f.write("%s:!:20000:0:99999:7::%s:\n" % (fb, expiry))
        write_exe(os.path.join(self.bindir, "runuser"), _RUNUSER)
        write_exe(os.path.join(self.bindir, "getent"), _GETENT)
        write_exe(os.path.join(self.bindir, "setfacl"), _SETFACL)
        write_exe(os.path.join(self.bindir, "chage"), _CHAGE)
        write_exe(os.path.join(self.bindir, "find"), _FIND)
        write_exe(os.path.join(self.bindir, "id"), _ID)
        write_exe(os.path.join(self.bindir, "pkill"), 'echo "pkill $*" >> "$FBT_LOG"; exit 1')
        write_exe(os.path.join(self.bindir, "passwd"),
                  'echo "$2 %s 01/01/2026 0 99999 7 -1"' % pw_status)
        write_exe(os.path.join(self.bindir, "sudo"), "echo %r" % sudo_out)
        self.script = os.path.join(td, "fleetbackup-acl.sh")
        script = fbm.render_fleetbackup_acl_script(
            user=fb, home=self.fbhome, shell="/bin/sh", home_root=self.root,
            key_uid=os.getuid(), key_line=KEYLINE, lock=self.lock, lock_wait_s=1,
            paused=paused, account_timeout_s=account_timeout_s,
            **({} if budget_s is None else {"budget_s": budget_s}))
        if output_cap_kib is not None:
            line = "FB_OUTPUT_CAP_KIB=%d\n" % fbm.FLEETBACKUP_OUTPUT_CAP_KIB
            assert script.count(line) == 1
            script = script.replace(line, "FB_OUTPUT_CAP_KIB=%d\n" % output_cap_kib)
        with open(self.script, "w") as f:
            f.write(script)
        self.mark = os.path.join(self.fbhome, ".airuleset-withdrawn")

    def home(self, name=ME, stream=True):
        h = os.path.join(self.root, name)
        os.makedirs(os.path.join(h, ".claude"), exist_ok=True)
        if stream:
            with open(os.path.join(h, ".claude", "airuleset-box-class"), "w") as f:
                f.write("shared-stream\n")
        return h

    def answers(self, *lines):
        with open(self.queue, "w") as f:
            f.write("".join(ln + "\n" for ln in lines))

    def env(self):
        env = dict(os.environ)
        env.update(PATH=self.bindir + ":" + env.get("PATH", ""), FBT_LOG=self.log,
                   FBT_QUEUE=self.queue, FBT_FB=self.fb, FBT_PASSWD=self.passwd,
                   FBT_SHADOW=self.shadow, FBT_CHECKS=self.checks, FBT_MARK=self.mark)
        env.update(self.extra_env)
        return env

    def run(self, *args, stdin=None):
        return subprocess.run(["bash", self.script, *args], env=self.env(), input=stdin,
                              capture_output=True, text=True, timeout=120)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        return open(self.log).read().splitlines()

    def expiry(self):
        return open(self.shadow).read().split(":")[7]

    def checked(self, n):
        """The records the n-th answered check received: a set of (kind, path)."""
        data = open(os.path.join(self.checks, "%d.bin" % n), "rb").read().decode()
        f = data.split("\0")[:-1]
        return {(f[i], f[i + 2]) for i in range(0, len(f), 3)}


def acl(path):
    """getfacl -c lines of `path` (absolute names, no header)."""
    out = subprocess.run(["/usr/bin/getfacl", "-p", "-c", "--", path], capture_output=True,
                         text=True, check=True).stdout
    return [ln for ln in out.splitlines() if ln]


def entry(path, prefix):
    """The ACL line of `path` starting with `prefix` (e.g. 'user:nobody:'), or None."""
    for ln in acl(path):
        if ln.startswith(prefix):
            return ln
    return None


def make_layout(h):
    """A realistic stream home: siblings that must close, the two trees, a
    symlink child, 0600 files that must stay closed."""
    for d, mode in (("devel", 0o755), (".private", 0o700), (".claude/projects", 0o775),
                    ("uploads", 0o775), ("uploads/sub", 0o700),
                    (".claude/work-products", 0o775), (".claude/work-products/a", 0o700)):
        os.makedirs(os.path.join(h, d), exist_ok=True)
        os.chmod(os.path.join(h, d), mode)
    for f, mode in (("devel/code.py", 0o644), (".bashrc", 0o644),
                    (".claude/settings.json", 0o664), ("uploads/x.txt", 0o644),
                    ("uploads/p.txt", 0o600), ("uploads/sub/s.txt", 0o640),
                    (".claude/work-products/a/w.md", 0o644)):
        p = os.path.join(h, f)
        with open(p, "w") as fh:
            fh.write("data\n")
        os.chmod(p, mode)
    os.symlink("devel", os.path.join(h, "link"))
    os.chmod(os.path.join(h, ".claude"), 0o700)
    os.chmod(h, 0o750)
