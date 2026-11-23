"""#1395: `airuleset.py session-shrink` keeps a long session on /compact.

A transcript (``~/.claude/projects/<dir>/<id>.jsonl``) is append-only from a
normal interactive session: ``/compact`` keeps the CONVERSATION small, but the
FILE keeps every record (942 MB on the controller, 23.11.2026), and every
``claude -c`` streams all of it again.

Claude Code 2.1.330 already has its own on-disk compactor
(``performCompactTranscript``). It uses a per-type keep policy, copies rows
appended during the rewrite, aborts when the source changes under it, and
renames atomically. It runs in a NON-interactive session with
``CLAUDE_CODE_TRANSCRIPT_LOCAL_GC=1`` when a ``/compact`` completes
(``requestCompact``). It skips a file under 5 MiB or a low-yield rewrite. Proven
on a scratch session: 7 890 993 -> 135 681 bytes, with the same resume answer.
So this command does not rewrite anything itself. It is a thin wrapper:

1. Refuse when:
   * the file is not a regular file owned by the caller (run it AS the owner);
   * the session is live: an open fd, a ``~/.claude/sessions`` record of a live
     pid (EPERM = alive), a running claude (native or npm) naming the id, or
     an mtime within ``--min-idle-min``;
   * the recorded cwd is gone.
2. Back the file up to ``<file>.shrink-<UTC ts>.gz``. That name is not
   ``*.jsonl``/``*.jsonl.gz``, so no session list or sweep takes it for a
   session. The backup is written to a random dot-temp opened 0600, fsynced,
   renamed, and given the original's ns atime/mtime.
3. Check liveness AGAIN (the backup took time), then run an ISOLATED
   ``claude --resume <id> -p /compact``: hooks disabled
   (``--settings {"disableAllHooks":true}``), ``--strict-mcp-config`` with an
   empty MCP config, ``--tools ""``, ``--model claude-sonnet-5-5``, and an env
   with every ``CLAUDE*`` variable dropped (only the GC switch added). It runs
   in the recorded cwd whose sanitized form IS the ``projects/<dir>`` name, so
   it resumes this session and no other. The /compact is one model call.
4. Verify: a compact boundary with a uuid the file did not have before, a
   smaller file, and every session-metadata type the file had (except Claude
   Code's reclaimable request/file-history rows). On any failure the backup
   stays, and the message names it.
5. Clean this session's leftover native temp (``<file>.compact.tmp.*``) and
   keep only its newest backup.

Dry-run is the default. ``--apply`` runs it. Only the caller's OWN sessions:
``--account X`` refuses unless it runs as X. Streaming reads only (the #1224
lesson).
"""
import argparse
import getpass
import gzip
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time

MIB = 1024 * 1024
NATIVE_MIN_BYTES = 5 * MIB           # Claude Code's own floor (d6a = 5242880)
DEFAULT_OVER_MIB = 64
DEFAULT_MIN_IDLE_MIN = 10
NATIVE_TIMEOUT_S = 1800
GC_ENV = "CLAUDE_CODE_TRANSCRIPT_LOCAL_GC"
COMPACT_MODEL = "claude-sonnet-5-5"
ISOLATION_ARGS = ["--settings", '{"disableAllHooks":true}', "--strict-mcp-config",
                  "--mcp-config", '{"mcpServers":{}}', "--tools", "", "--model", COMPACT_MODEL]
COMPACT_NEEDLE = b'"compact_boundary"'
# rows Claude Code's own compactor legitimately reclaims (probe 23.11.2026: the
# api-request* replay rows went, every other metadata type stayed)
RECLAIMABLE_META = frozenset({"api-request", "api-request-blob", "api-request-shape",
                              "api-request-resumed", "file-history-snapshot",
                              "file-history-delta", "attribution-snapshot"})
_META_HEAD = re.compile(rb'^\{"type":"([A-Za-z0-9_-]+)"')
_CWD = re.compile(rb'"cwd":"((?:[^"\\]|\\.)*)"')


class Refused(Exception):
    """The session must not be shrunk (or the shrink failed); says why."""


def session_id(path):
    base = os.path.basename(path)
    return base[:-len(".jsonl")] if base.endswith(".jsonl") else base


def census(path):
    """``(metadata types present, compact boundary uuids)``, streamed."""
    types, boundaries = set(), set()
    with open(path, "rb") as fh:
        for raw in fh:
            m = _META_HEAD.match(raw)
            if m:
                types.add(m.group(1).decode())
            elif COMPACT_NEEDLE in raw:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    print("session-shrink: %s: a torn boundary-like line skipped" % path,
                          file=sys.stderr)
                    continue
                if rec.get("subtype") == "compact_boundary":
                    boundaries.add(rec.get("uuid"))
    return types, boundaries


def sanitize(path):
    """Claude Code's ``projects/<dir>`` name for a cwd."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def recorded_cwd(path, tail=4 * MIB):
    """The newest recorded ``cwd`` whose sanitized form names this file's
    projects dir (read from the tail), or None."""
    want = os.path.basename(os.path.dirname(os.path.abspath(path)))
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - tail))
        chunk = fh.read()
    for raw in reversed(_CWD.findall(chunk)):
        cwd = json.loads(b'"' + raw + b'"')
        if sanitize(cwd) == want:
            return cwd
    return None


def _pid_alive(pid, kill=os.kill):
    try:
        kill(int(pid), 0)
    except PermissionError:
        return True                     # exists, another uid
    except (OSError, ValueError, TypeError):
        return False
    return True


def _is_claude(cmdline):
    """A Claude Code process: ``claude …``, ``…/claude/versions/<v>``, or an npm
    install run by node (``node …/@anthropic-ai/claude-code/cli.js`` /
    ``node …/bin/claude``). Never a shell or a tool whose text names the id."""
    argv = cmdline.split(b"\0")
    a0 = argv[0]
    if os.path.basename(a0) == b"claude" or b"/claude/versions/" in a0:
        return True
    if os.path.basename(a0).startswith(b"node") and len(argv) > 1:
        a1 = argv[1]
        return b"@anthropic-ai/claude-code" in a1 or os.path.basename(a1) == b"claude"
    return False


def live_reason(path, home=None, proc_dir="/proc", now=None, min_idle_min=DEFAULT_MIN_IDLE_MIN):
    """Why ``path`` is (or may be) in live use, or ``""``. Undeterminable = live."""
    real = os.path.realpath(path)
    sid = session_id(path)
    now = time.time() if now is None else now
    try:
        age = now - os.stat(path).st_mtime
    except OSError as e:
        return "cannot stat (%s)" % e
    if age < min_idle_min * 60:
        return "modified %.0f s ago (< %g min idle)" % (age, min_idle_min)
    sessions = os.path.join(home or os.path.expanduser("~"), ".claude", "sessions")
    try:
        names = os.listdir(sessions)
    except FileNotFoundError:
        names = []
    except OSError as e:
        return "cannot read %s (%s)" % (sessions, e)
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(sessions, name)) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("sessionId") == sid and _pid_alive(rec.get("pid")):
            return "running claude pid %s has this session id" % rec.get("pid")
    try:
        pids = [p for p in os.listdir(proc_dir) if p.isdigit()]
    except OSError as e:
        return "cannot list %s (%s)" % (proc_dir, e)
    me = str(os.getpid())
    for pid in pids:
        if pid == me:
            continue
        try:
            with open(os.path.join(proc_dir, pid, "cmdline"), "rb") as fh:
                cmd = fh.read()
        except OSError:
            cmd = b""
        if sid.encode() in cmd and _is_claude(cmd):
            return "claude pid %s names this session id on its command line" % pid
        fd_dir = os.path.join(proc_dir, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) == real:
                    return "pid %s holds the file open" % pid
            except OSError:
                continue
    return ""


def check(path, home=None, euid=None, min_idle_min=DEFAULT_MIN_IDLE_MIN, proc_dir="/proc"):
    """Refuse an unsafe target; returns ``(stat, cwd)``."""
    if os.path.islink(path):
        raise Refused("%s: a symlink, never followed" % path)
    st = os.stat(path)
    if not os.path.isfile(path):
        raise Refused("%s: not a regular file" % path)
    euid = os.geteuid() if euid is None else euid
    if st.st_uid != euid:
        raise Refused("%s: owner uid %d is not you (uid %d): run it as the owner"
                      % (path, st.st_uid, euid))
    why = live_reason(path, home=home, proc_dir=proc_dir, min_idle_min=min_idle_min)
    if why:
        raise Refused("%s: in live use: %s" % (path, why))
    cwd = recorded_cwd(path)
    if not cwd:
        raise Refused("%s: no recorded cwd matches its projects dir %s; claude --resume "
                      "would open another project" % (path, os.path.basename(
                          os.path.dirname(os.path.abspath(path)))))
    if not os.path.isdir(cwd):
        raise Refused("%s: its recorded cwd %r is gone; claude --resume needs it" % (path, cwd))
    return st, cwd


def backup(path, st, now=None):
    """Gzip copy beside the file: random 0600 dot-temp -> fsync -> rename;
    the original's ns atime/mtime. Returns the backup path."""
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now if now is not None else time.time()))
    final = "%s.shrink-%s.gz" % (path, ts)
    if os.path.exists(final):
        raise Refused("%s: backup %s already exists" % (path, final))
    d = os.path.dirname(path) or "."
    tmp = os.path.join(d, ".%s.shrink-%s.tmp" % (os.path.basename(path), secrets.token_hex(6)))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw:
            copied = 0
            with gzip.GzipFile(fileobj=raw, mode="wb") as gz, open(path, "rb") as src:
                while True:
                    chunk = src.read(MIB)
                    if not chunk:
                        break
                    gz.write(chunk)
                    copied += len(chunk)
            raw.flush()
            os.fsync(raw.fileno())
        if copied < st.st_size:
            raise Refused("%s: shorter than its stat while backing up" % path)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.rename(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return final


def native_compact(path, cwd, claude_bin="claude", env=None, timeout=NATIVE_TIMEOUT_S):
    """An isolated ``claude --resume <id> -p /compact`` with the GC env, in ``cwd``:
    no hooks, no MCP, no tools, a fixed model, no ``CLAUDE*`` env of the caller."""
    e = {k: v for k, v in (os.environ if env is None else env).items()
         if not k.startswith("CLAUDE")}
    e[GC_ENV] = "1"
    argv = [claude_bin, "--resume", session_id(path), "-p", "/compact"] + ISOLATION_ARGS
    try:
        r = subprocess.run(argv, cwd=cwd, env=e, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    return r.returncode, (r.stderr or r.stdout or "").strip()[-400:]


def shrink(path, home=None, claude_bin="claude", env=None, euid=None,
           min_idle_min=DEFAULT_MIN_IDLE_MIN, min_bytes=NATIVE_MIN_BYTES, proc_dir="/proc"):
    """Run the native compactor on one idle session. Returns a result dict."""
    st, cwd = check(path, home=home, euid=euid, min_idle_min=min_idle_min, proc_dir=proc_dir)
    res = {"path": path, "before": st.st_size, "after": st.st_size, "cwd": cwd,
           "backup": None, "action": "skip"}
    if st.st_size < min_bytes:
        res["reason"] = "under %d bytes: Claude Code's compactor skips it" % min_bytes
        return res
    before_types, before_bounds = census(path)
    res["backup"] = backup(path, st)
    why = live_reason(path, home=home, proc_dir=proc_dir, min_idle_min=0)
    if why:
        raise Refused("%s: in live use after the backup: %s; nothing ran, backup at %s"
                      % (path, why, res["backup"]))
    rc, out = native_compact(path, cwd, claude_bin=claude_bin, env=env)
    clean_native_temps(path)
    if rc != 0:
        raise Refused("%s: claude --resume -p /compact failed (%s): %s; backup kept at %s"
                      % (path, "exit %s" % rc if rc is not None else "did not run", out,
                         res["backup"]))
    after_types, after_bounds = census(path)
    after = os.path.getsize(path)
    lost = sorted((before_types - RECLAIMABLE_META) - after_types)
    if not (after_bounds - before_bounds):
        problem = "no new compact boundary (the /compact did not run)"
    elif after >= st.st_size:
        problem = "the file did not shrink (%d -> %d bytes)" % (st.st_size, after)
    elif lost:
        problem = "kept-forever types are gone: %s" % ", ".join(lost)
    else:
        problem = ""
    if problem:
        raise Refused("%s: after the native compactor %s; backup kept at %s"
                      % (path, problem, res["backup"]))
    prune_backups(path, keep=res["backup"])
    res.update(after=after, action="shrunk")
    return res


def clean_native_temps(path):
    """Remove this session's leftover ``<file>.compact.tmp.*`` (a native rewrite
    that died). Only ever this file's own temps, after the run."""
    d, base = os.path.split(path)
    for n in os.listdir(d or "."):
        if n.startswith(base + ".compact.tmp."):
            os.unlink(os.path.join(d, n))
            print("session-shrink: removed leftover %s" % n, file=sys.stderr)


def prune_backups(path, keep):
    """Keep only the newest backup of this session (``keep``)."""
    d, base = os.path.split(path)
    for n in os.listdir(d or "."):
        full = os.path.join(d, n)
        if n.startswith(base + ".shrink-") and n.endswith(".gz") and full != keep:
            os.unlink(full)
            print("session-shrink: removed older backup %s" % n, file=sys.stderr)


def candidates(home, over_bytes):
    """The caller's main session transcripts at least ``over_bytes`` big."""
    root = os.path.join(home, ".claude", "projects")
    try:
        dirs = sorted(os.listdir(root))
    except OSError:
        return []
    found = []
    for d in dirs:
        full = os.path.join(root, d)
        try:
            names = os.listdir(full)
        except OSError:
            continue
        for n in sorted(names):
            p = os.path.join(full, n)
            if n.endswith(".jsonl") and not os.path.islink(p):
                try:
                    if os.stat(p).st_size >= over_bytes:
                        found.append(p)
                except OSError:
                    continue
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(prog="airuleset session-shrink",
                                 description=__doc__.splitlines()[0])
    _add_options(ap)
    return _run(ap.parse_args(argv))


def _run(a):
    me = getpass.getuser()
    home = os.path.expanduser("~")
    if a.account and a.account != me:
        print("session-shrink: --account %s must run AS that account (you are %s): "
              "sudo -u %s -H python3 ~/devel/airuleset/airuleset.py session-shrink --account %s"
              % (a.account, me, a.account, a.account), file=sys.stderr)
        return 2
    if a.path:
        paths = [a.path]
    elif a.account or a.over is not None:
        paths = candidates(home, int((a.over if a.over is not None else DEFAULT_OVER_MIB) * MIB))
    else:
        print("session-shrink: give a <path>, --account <you> or --over <MiB>", file=sys.stderr)
        return 2
    min_bytes = int(a.min_mib * MIB)
    rc = 0
    for path in paths:
        try:
            if a.apply and not a.dry_run:
                r = shrink(path, home=home, claude_bin=a.claude_bin,
                           min_idle_min=a.min_idle_min, min_bytes=min_bytes)
                if r["action"] == "skip":
                    print("%s: untouched (%s)" % (path, r["reason"]))
                else:
                    print("%s: shrunk %d -> %d bytes by Claude Code's compactor; backup %s"
                          % (path, r["before"], r["after"], r["backup"]))
            else:
                st, cwd = check(path, home=home, min_idle_min=a.min_idle_min)
                if st.st_size < min_bytes:
                    print("%s: untouched (%d bytes, under the %d-byte floor)"
                          % (path, st.st_size, min_bytes))
                else:
                    print("%s: would run (cd %s && %s=1 %s --resume %s -p /compact %s): "
                          "%d bytes, backup %s.shrink-<ts>.gz first; --apply to run"
                          % (path, cwd, GC_ENV, a.claude_bin, session_id(path),
                             " ".join(shlex.quote(x) for x in ISOLATION_ARGS), st.st_size,
                             path))
        except Refused as e:
            print("session-shrink: REFUSED %s" % e, file=sys.stderr)
            rc = 1
        except OSError as e:
            print("session-shrink: %s: %s" % (path, e), file=sys.stderr)
            rc = 1
    if not paths:
        print("session-shrink: no session transcript over the size floor")
    return rc


def _add_options(p):
    p.add_argument("path", nargs="?", help="one session <id>.jsonl")
    p.add_argument("--account", help="this account's own sessions (must run as it)")
    p.add_argument("--over", type=float, default=None, metavar="MiB",
                   help="sessions at least this big (default %d with --account)" % DEFAULT_OVER_MIB)
    p.add_argument("--apply", action="store_true", help="run it (default: dry-run)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="never run (default)")
    p.add_argument("--min-idle-min", dest="min_idle_min", type=float,
                   default=DEFAULT_MIN_IDLE_MIN, help="refuse a file modified more recently")
    p.add_argument("--min-mib", dest="min_mib", type=float, default=NATIVE_MIN_BYTES / MIB,
                   help=argparse.SUPPRESS)
    p.add_argument("--claude-bin", dest="claude_bin", default="claude", help=argparse.SUPPRESS)


def cmd_session_shrink(args):
    return _run(args)


def register_parser(sub):
    _add_options(sub.add_parser(
        "session-shrink", help="run Claude Code's own transcript compactor on an idle session (#1395)"))


if __name__ == "__main__":
    sys.exit(main())
