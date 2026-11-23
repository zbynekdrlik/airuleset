"""#1395: `airuleset.py session-shrink` keeps a long session on /compact.

A session's transcript (``~/.claude/projects/<dir>/<id>.jsonl``) is append-only:
``/compact`` keeps the CONVERSATION small, but the FILE keeps every record since
the session started (942 MB on this box, 23.11.2026), and every ``claude -c`` /
``--resume`` streams all of it again. Claude Code itself already ignores what lies
before the LAST ``compact_boundary`` record when it loads a transcript
(``readTranscriptForLoad`` resets its buffer at a boundary, and the relink step
deletes every chain record positioned before the last boundary except the ones the
boundary's ``compactMetadata.preservedMessages.uuids`` / ``preservedSegment`` name).
So the bytes before that point are dead weight for resume, and this command moves
them into a gzip archive beside the file and keeps the rest byte-identical:

* cut = the line of the last boundary, or the earliest line of a record the
  boundary preserves (those records sit physically BEFORE the boundary);
* the archived bytes go to ``<file>.pre-<UTC ts>.jsonl.gz`` (never deleted, and
  not ``*.jsonl``, so no session list and no sweep treats it as a session);
* the newest record of each session-METADATA type (title, mode, tag, …) that the
  kept part no longer carries is copied in front of it, so the picker title and
  modes survive; history-shaped metadata (file-history, queue, api-request) is
  not, and a ``last-prompt``/``summary`` only when its ``leafUuid`` is kept;
* the replace is atomic (same-dir temp, fsync, ``os.replace``) and keeps the
  file's mode and its atime/mtime (``claude -c`` picks the newest file by mtime);
* it REFUSES a live session: an open fd on the file, a ``~/.claude/sessions``
  record of a running pid with that session id, a running process whose command
  line names the id, or a file modified in the last ``--min-idle-min`` minutes; and
  it refuses when a kept chain record would lose its parent, or a preserved record
  cannot be found.

Dry-run is the default; ``--apply`` writes. Only the caller's OWN sessions:
``--account X`` refuses unless it runs as X. Streaming throughout: no whole-file
load (the #1224 lesson), json only for the few lines that need it.
"""
import argparse
import getpass
import gzip
import json
import os
import re
import sys
import time

COMPACT_NEEDLE = b'"compact_boundary"'
# history-shaped metadata: positional or uuid-addressed, stale once archived
NO_CARRY = frozenset({"file-history-snapshot", "file-history-delta", "queue-operation",
                      "api-request", "api-request-blob", "api-request-shape",
                      "data-flow-records", "data-flow-incomplete"})
LEAF_TYPES = frozenset({"last-prompt", "summary"})
_TYPE_HEAD = re.compile(rb'^\{"type":"([A-Za-z0-9_-]+)"')
DEFAULT_OVER_MIB = 64
DEFAULT_MIN_IDLE_MIN = 10
MIB = 1024 * 1024


class Refused(Exception):
    """The session must not be shrunk; the message says why."""


def _lines(path):
    """Yield ``(offset, raw_line_bytes)``; one line in memory at a time."""
    off = 0
    with open(path, "rb") as fh:
        for raw in fh:
            yield off, raw
            off += len(raw)


def _loads(raw):
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _is_boundary(rec):
    return bool(rec) and rec.get("type") == "system" and rec.get("subtype") == "compact_boundary"


def _is_chain(rec):
    return "parentUuid" in rec and "uuid" in rec


def _preserved(meta):
    """``(must, extra)``: uuids the loader relinks (missing = refuse) and the
    advisory ones (``allUuids``, anchor) that only widen the kept range."""
    must, extra = set(), set()
    pm = meta.get("preservedMessages") or {}
    ps = meta.get("preservedSegment") or {}
    must.update(u for u in (pm.get("uuids") or []) if isinstance(u, str))
    must.update(ps.get(k) for k in ("headUuid", "tailUuid") if isinstance(ps.get(k), str))
    extra.update(u for u in (pm.get("allUuids") or []) if isinstance(u, str))
    extra.update(x.get("anchorUuid") for x in (pm, ps) if isinstance(x.get("anchorUuid"), str))
    return must, extra - must


def plan(path):
    """Measure what a shrink of ``path`` would do. Never writes. Returns a dict;
    ``plan["cut"] == 0`` means nothing to shrink (``plan["reason"]`` says why)."""
    st = os.stat(path)
    last = None                         # (offset, line_no, record)
    for no, (off, raw) in enumerate(_lines(path)):
        if COMPACT_NEEDLE in raw:
            rec = _loads(raw)
            if _is_boundary(rec):
                last = (off, no, rec)
    out = {"path": path, "size": st.st_size, "mtime": st.st_mtime, "atime": st.st_atime,
           "cut": 0, "cut_line": None, "boundary_line": None, "carry": [], "reason": ""}
    if last is None:
        out["reason"] = "no compact boundary: nothing to shrink"
        return out
    b_off, b_no, b_rec = last
    out["boundary_line"] = b_no + 1
    must, extra = _preserved(b_rec.get("compactMetadata") or {})
    want = must | extra
    found = {}                          # uuid -> (offset, line_no) before the boundary
    last_meta = {}                      # metadata type -> raw line (archived part)
    for no, (off, raw) in enumerate(_lines(path)):
        if off >= b_off:
            break
        m = _TYPE_HEAD.match(raw)
        if m and b'"parentUuid"' not in raw[:200]:
            last_meta[m.group(1).decode()] = (off, raw)
            continue
        if want and any(u.encode() in raw for u in want):
            rec = _loads(raw)
            if rec and rec.get("uuid") in want:
                found.setdefault(rec["uuid"], (off, no))
    cut, cut_no = b_off, b_no
    for off, no in found.values():
        if off < cut:
            cut, cut_no = off, no
    kept_uuids, kept_types, parents = set(), set(), []
    for no, (off, raw) in enumerate(_lines(path)):
        if off < cut:
            continue
        m = _TYPE_HEAD.match(raw)
        if m and b'"parentUuid"' not in raw[:200]:
            kept_types.add(m.group(1).decode())
            continue
        rec = _loads(raw)
        if rec is None:
            continue
        if _is_chain(rec):
            kept_uuids.add(rec["uuid"])
            parents.append((no + 1, rec["uuid"], rec.get("parentUuid")))
        elif isinstance(rec.get("type"), str):
            kept_types.add(rec["type"])
    missing = sorted(u for u in must if u not in found and u not in kept_uuids)
    if missing:
        raise Refused("%s: the last boundary preserves %d record(s) not in the file "
                      "(%s): cannot prove a safe cut" % (path, len(missing), missing[0]))
    relinked = must | extra
    dangling = [(ln, u, p) for ln, u, p in parents
                if p and p not in kept_uuids and u not in relinked]
    if dangling:
        ln, u, p = dangling[0]
        raise Refused("%s: %d kept record(s) would lose their parent (line %d, %s -> %s)"
                      % (path, len(dangling), ln, u, p))
    if cut == 0:
        out["reason"] = "the last boundary is the first record: nothing to shrink"
        return out
    carry = []
    for typ, (off, raw) in sorted(last_meta.items(), key=lambda kv: kv[1][0]):
        if typ in kept_types or typ in NO_CARRY:
            continue
        if typ in LEAF_TYPES:
            rec = _loads(raw) or {}
            if rec.get("leafUuid") not in kept_uuids:
                continue
        carry.append(raw if raw.endswith(b"\n") else raw + b"\n")
    out.update(cut=cut, cut_line=cut_no + 1, carry=carry,
               after=st.st_size - cut + sum(len(c) for c in carry),
               archive=archive_path(path))
    return out


def archive_path(path, now=None):
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now if now is not None else time.time()))
    return "%s.pre-%s.jsonl.gz" % (path, ts)


def session_id(path):
    base = os.path.basename(path)
    return base[:-len(".jsonl")] if base.endswith(".jsonl") else base


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def _is_claude(cmdline):
    """A Claude Code process (``claude …`` or ``…/claude/versions/<v> …``), never
    the shell or tool whose own command text merely mentions the session."""
    argv0 = cmdline.split(b"\0", 1)[0]
    return os.path.basename(argv0) == b"claude" or b"/claude/versions/" in argv0


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
        return "modified %.0f s ago (< %d min idle)" % (age, min_idle_min)
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


def shrink(path, home=None, proc_dir="/proc", min_idle_min=DEFAULT_MIN_IDLE_MIN, now=None):
    """Apply the plan. Returns the plan dict (with ``archive`` written)."""
    why = live_reason(path, home=home, proc_dir=proc_dir, now=now, min_idle_min=min_idle_min)
    if why:
        raise Refused("%s: in live use: %s" % (path, why))
    p = plan(path)
    if not p["cut"]:
        return p
    st = os.stat(path)
    archive = archive_path(path, now)
    tmp = os.path.join(os.path.dirname(path), ".%s.shrink-tmp" % os.path.basename(path))
    with open(path, "rb") as src:
        with gzip.open(archive, "xb") as gz:
            left = p["cut"]
            while left:
                chunk = src.read(min(left, MIB))
                if not chunk:
                    raise Refused("%s: shorter than planned" % path)
                gz.write(chunk)
                left -= len(chunk)
        with open(archive, "rb") as fh:
            os.fsync(fh.fileno())
        with open(tmp, "xb") as out:
            for line in p["carry"]:
                out.write(line)
            while True:
                chunk = src.read(MIB)
                if not chunk:
                    break
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    os.chmod(archive, st.st_mode & 0o777)
    os.chmod(tmp, st.st_mode & 0o777)
    os.utime(tmp, (st.st_atime, st.st_mtime))
    now_st = os.stat(path)
    if (now_st.st_size, now_st.st_mtime) != (p["size"], p["mtime"]):
        os.unlink(tmp)
        raise Refused("%s: changed while shrinking; archive %s kept, file untouched"
                      % (path, archive))
    os.replace(tmp, path)
    dfd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    p["archive"] = archive
    return p


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


def _report(p, applied):
    if not p["cut"]:
        return "%s: untouched (%s)" % (p["path"], p["reason"])
    return ("%s: %s %d -> %d bytes; cut before line %d (last boundary line %d); "
            "carried %d metadata record(s); archive %s"
            % (p["path"], "shrunk" if applied else "would shrink", p["size"], p["after"],
               p["cut_line"], p["boundary_line"], len(p["carry"]), p["archive"]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="airuleset session-shrink",
                                 description=__doc__.splitlines()[0])
    _add_options(ap)
    a = ap.parse_args(argv)
    return _run(a)


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
    apply = a.apply and not a.dry_run
    rc = 0
    for path in paths:
        try:
            if apply:
                p = shrink(path, min_idle_min=a.min_idle_min)
            else:
                why = live_reason(path, min_idle_min=a.min_idle_min)
                p = plan(path)
                if why and p["cut"]:
                    print("%s: LIVE (%s): --apply would refuse" % (path, why))
            print(_report(p, apply))
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
    p.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="never write (default)")
    p.add_argument("--min-idle-min", dest="min_idle_min", type=float,
                   default=DEFAULT_MIN_IDLE_MIN, help="refuse a file modified more recently")


def cmd_session_shrink(args):
    return _run(args)


def register_parser(sub):
    _add_options(sub.add_parser(
        "session-shrink", help="archive a session transcript's pre-compact records (#1395)"))


if __name__ == "__main__":
    sys.exit(main())
