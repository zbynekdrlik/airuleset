"""Carry a Claude Code conversation into a project account (#1190).

The #1184 migration moves a legacy project out of the shared ``newlevel``
account into its own account (``cli_account_bootstrap.SERVICE_ACCOUNTS``). It
moved the checkout, keys and tmux session, but not the conversation. Claude
Code keeps that under ``~/.claude/projects/<cwd-key>/``:

  * ``<uuid>.jsonl``  the transcript ``claude --resume <uuid>`` loads;
  * ``<uuid>/``       subagent transcripts and tool results;
  * ``memory/``       the project auto-memory.

The project account cannot read ``/home/newlevel`` (0700), and loosening that
would defeat #1184, so root COPIES those files into the account's own key dir.
``accounts transfer-session`` (``cli_accounts``) is the CLI. This leaf holds
the live-process guard, the plan, and the root script it renders (the
``account-bootstrap --render`` shape). It reuses the fleet's projects-key
encoder and Claude-process recognizer from ``watchdog`` (imported lazily).
The script runs as root, but it never reads file CONTENT from, or writes
into, a path an account controls; root only stats such paths. The source is
read AS its owner (fixed at render time; root-owned is refused) with
``runuser … tar -c``, and unpacked AS the target account into a private temp
dir inside the target. Each item is then placed with a no-clobber rename.
File mtimes are kept, and session dirs keep theirs. ``memory/`` is merged
into, so its own dir mtime becomes the run time.

Not copied, on purpose:

  * the worktree-lane keys (``<key>--claude-worktrees-*``). Those are the
    supervisor's dead lanes, not the owner's conversation;
  * ``file-history``/``todos``. The resume in point 2 below ran from a
    scratch HOME that had neither, so ``--resume`` does not need them. A
    rewind of file edits recorded under the old path would also restore
    files into a checkout that no longer exists.

The jsonl ``cwd`` field is NOT rewritten. Evidence: Claude Code 2.1.284,
throwaway runs under a scratch HOME (#1190 issuecomment-5898853002):

  1. The key is the cwd with EVERY non-``[A-Za-z0-9]`` char turned into ``-``:
     ``.../a.b_c d`` -> ``...-a-b-c-d``. The fleet's one encoder,
     ``watchdog.transcripts.encode_project_dir``, maps only ``/``, ``.`` and
     ``_``. ``project_key`` reuses it and REFUSES any other character, so the
     two agree on every path this accepts.
  2. ``claude -p --resume <uuid>`` found a session copied into the NEW cwd's
     key whose lines still said ``cwd: <old path>``, and also one where that
     path did not exist. The new turn chained onto the old leaf and was
     written with the new cwd.
  3. The interactive ``--resume`` picker listed such a copy exactly like the
     unmoved control. The picker keys on the projects DIRECTORY, not on
     ``cwd``.

So the copy is byte-identical. The old paths inside the history are
explained to the resumed model by the printed first-prompt note
(``resume_hint``).
"""
import os
import pwd
import re
import shlex
import stat
import time

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Claude Code shortens very long keys (a hash suffix — not measured here). Our
# project paths are far shorter, so a longer key is refused, never guessed.
MAX_KEY_LEN = 200
# Path characters on which `encode_project_dir` (maps `/._` only) and Claude
# Code (maps every non-alphanumeric char, measured on 2.1.284) agree.
_SAFE_PATH_RE = re.compile(r"/[A-Za-z0-9/._-]*")
# A worktree lane's cwd is `<checkout>/.claude/worktrees/<name>`, so its key is
# `<checkout key>--claude-worktrees-<name>`.
WORKTREE_KEY_INFIX = "--claude-worktrees-"


def project_key(cwd):
    """Claude Code's ``~/.claude/projects`` directory name for ``cwd``."""
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        raise ValueError("not an absolute path: %r" % (cwd,))
    if not _SAFE_PATH_RE.fullmatch(cwd):
        raise ValueError("%r has a character outside [A-Za-z0-9/._-]: Claude Code "
                         "maps it to '-', the fleet encoder does not — refused "
                         "(#1190)" % (cwd,))
    from watchdog.transcripts import encode_project_dir
    key = encode_project_dir(cwd)
    if len(key) > MAX_KEY_LEN:
        raise ValueError("project key for %r is longer than %d chars (Claude "
                         "Code hashes those) — not supported" % (cwd, MAX_KEY_LEN))
    return key


def default_from_home(from_dir):
    """``/home/<user>`` of an old checkout under ``/home/<user>/``, else None."""
    parts = os.path.normpath(from_dir or "").split("/")
    if len(parts) >= 4 and parts[0] == "" and parts[1] == "home" and parts[2]:
        return "/home/" + parts[2]
    return None


def _is_claude(comm, cmdline, exe=""):
    """A Claude Code process. The fleet recognizer
    (``watchdog.erp_heartbeat._cmdline_is_claude_cli``) covers argv0
    ``claude`` and ``node …/claude`` / ``node …/claude-code/…``. On top of it
    come comm ``claude`` and an exe under ``…/claude/versions/`` (a versioned
    binary launched by its own path). The bash guard in ``_SCRIPT`` mirrors
    this, and a test runs both over the same fixtures."""
    from watchdog.erp_heartbeat import _cmdline_is_claude_cli
    args = cmdline.decode("utf-8", "replace").split("\0")
    return (comm == "claude" or "/claude/versions/" in exe
            or _cmdline_is_claude_cli(" ".join(a for a in args if a)))


def _read(path, mode="r"):
    try:
        with open(path, mode) as h:
            return h.read()
    except OSError:
        return b"" if "b" in mode else ""


def _under(cwd, dirs):
    return any(cwd == d or cwd.startswith(d.rstrip("/") + "/") for d in dirs)


def live_claude_processes(old_dirs, proc_root="/proc", self_pid=None):
    """([(pid, cwd)] of claude processes whose cwd is one of ``old_dirs`` (the
    typed and the resolved old checkout) or below it, worktrees included;
    number of processes whose cwd was unreadable). A non-root caller cannot
    read other users' cwd; the root script re-checks."""
    hits, unreadable = [], 0
    try:
        pids = sorted((p for p in os.listdir(proc_root) if p.isdigit()), key=int)
    except OSError:
        return hits, unreadable
    for pid in pids:
        if self_pid is not None and pid == str(self_pid):
            continue
        base = os.path.join(proc_root, pid)
        try:
            cwd = os.readlink(os.path.join(base, "cwd"))
        except PermissionError:
            unreadable += 1
            continue
        except OSError:        # exited meanwhile, or a kernel thread
            continue
        if cwd.endswith(" (deleted)"):
            cwd = cwd[:-len(" (deleted)")]
        if not _under(cwd, old_dirs):
            continue
        try:
            exe = os.readlink(os.path.join(base, "exe"))
        except OSError:
            exe = ""
        if _is_claude(_read(os.path.join(base, "comm")).strip(),
                      _read(os.path.join(base, "cmdline"), "rb"), exe):
            hits.append((int(pid), cwd))
    return hits, unreadable


def _is_regular(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _is_real_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _tree_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            p = os.path.join(root, f)
            if _is_regular(p):
                total += os.lstat(p).st_size
    return total


def _memory_files(mem_dir):
    """(regular files under ``mem_dir`` as sorted relative paths, skipped)."""
    files, skipped = [], []
    for root, dirs, names in os.walk(mem_dir):
        dirs[:] = [d for d in dirs if _is_real_dir(os.path.join(root, d))]
        for n in names:
            p = os.path.join(root, n)
            rel = os.path.relpath(p, mem_dir)
            (files if _is_regular(p) else skipped).append(rel)
    return sorted(files), sorted("memory/" + s for s in skipped)


def _scan_source(src_dir):
    """(sessions, memory, memory_bytes, other) found in the source key dir."""
    sessions, memory, memory_bytes, other = [], [], 0, []
    for name in sorted(os.listdir(src_dir)):
        path = os.path.join(src_dir, name)
        stem = name[:-len(".jsonl")] if name.endswith(".jsonl") else None
        if stem and UUID_RE.fullmatch(stem) and _is_regular(path):
            d = os.path.join(src_dir, stem)
            has_dir = _is_real_dir(d)
            sessions.append({"uuid": stem, "jsonl_bytes": os.lstat(path).st_size,
                             "mtime": os.lstat(path).st_mtime, "has_dir": has_dir,
                             "dir_bytes": _tree_bytes(d) if has_dir else 0})
        elif name == "memory" and _is_real_dir(path):
            memory, skipped = _memory_files(path)
            memory_bytes = sum(os.lstat(os.path.join(path, m)).st_size for m in memory)
            other += skipped
        elif not (UUID_RE.fullmatch(name) and _is_real_dir(path)
                  and _is_regular(path + ".jsonl")):
            other.append(name)      # a `<uuid>/` WITH its jsonl is its session's
    sessions.sort(key=lambda s: (-s["mtime"], s["uuid"]))
    return sessions, memory, memory_bytes, other


def _target_conflicts(dst_dir, sessions, memory):
    """(refusals, checked). ``checked`` is False when the caller cannot read
    the target (a non-root dry run); the root script re-checks it."""
    refusals = []
    try:
        existing = set(os.listdir(dst_dir))
    except FileNotFoundError:
        existing = set()
    except PermissionError:
        return refusals, False
    for s in sessions:
        u = s["uuid"]
        if u + ".jsonl" in existing or u in existing:
            refusals.append("session %s already exists in %s — never "
                            "overwritten (#1190)" % (u, dst_dir))
    for rel in memory:
        try:
            os.lstat(os.path.join(dst_dir, "memory", rel))
        except FileNotFoundError:
            continue
        except PermissionError:
            return refusals, False
        refusals.append("memory/%s already exists in %s — merge it by hand "
                        "(#1190)" % (rel, dst_dir))
    return refusals, True


def _source_problem(src_dir, from_dir):
    """Why the source key dir cannot be listed here, or None."""
    try:
        os.listdir(src_dir)
    except FileNotFoundError:
        return "no Claude sessions for %s (%s is missing)" % (from_dir, src_dir)
    except NotADirectoryError:
        return "%s is not a directory" % src_dir
    except PermissionError:
        return ("cannot read %s as uid %d — run the dry run as that home's owner "
                "or as root" % (src_dir, os.geteuid()))
    if not _is_real_dir(src_dir):
        return "%s is a symlink, not the key dir — refused" % src_dir
    return None


def build_plan(account, from_dir, *, from_home, target_home, target_cwd,
               proc_root="/proc", self_pid=None):
    """What a transfer of ``from_dir``'s conversation into ``account`` would
    copy, and every reason it must not (``refusals``, empty = go)."""
    for label, path in (("--from-dir", from_dir), ("target cwd", target_cwd),
                        ("--from-home", from_home), ("target home", target_home)):
        if not isinstance(path, str) or not path.startswith("/") or any(
                ord(c) < 32 or ord(c) == 127 for c in path):
            raise ValueError("%s must be an absolute path without control "
                             "characters: %r" % (label, path))
    from_dir = os.path.normpath(from_dir)
    # /proc/<pid>/cwd is the RESOLVED path: guard the typed and the real one
    old_dirs = sorted({from_dir, os.path.realpath(from_dir)})
    src_dir = os.path.join(from_home, ".claude", "projects", project_key(from_dir))
    dst_dir = os.path.join(target_home, ".claude", "projects", project_key(target_cwd))
    plan = {"account": account, "from_dir": from_dir, "from_home": from_home,
            "target_cwd": target_cwd, "src_dir": src_dir, "dst_dir": dst_dir,
            "old_dirs": old_dirs, "proc_root": proc_root, "sessions": [],
            "memory": [], "memory_bytes": 0, "other": [], "excluded_keys": [],
            "refusals": [], "target_checked": True, "unreadable_procs": 0}
    if src_dir == dst_dir:
        plan["refusals"].append("source and target are the same key dir %s" % src_dir)
        return plan
    problem = _source_problem(src_dir, from_dir)
    if problem:
        plan["refusals"].append(problem)
        return plan
    uid = os.lstat(src_dir).st_uid
    try:
        plan["src_owner"] = pwd.getpwuid(uid).pw_name
    except KeyError:
        plan["refusals"].append("%s is owned by uid %d with no passwd name" % (src_dir, uid))
        return plan
    if uid == 0:
        plan["refusals"].append("%s is owned by root — the source is read as its "
                                "owner, never as root (#1190)" % src_dir)
        return plan
    (plan["sessions"], plan["memory"], plan["memory_bytes"],
     plan["other"]) = _scan_source(src_dir)
    prefix = os.path.basename(src_dir) + WORKTREE_KEY_INFIX
    plan["excluded_keys"] = sorted(n for n in os.listdir(os.path.dirname(src_dir))
                                   if n.startswith(prefix))
    if not plan["sessions"] and not plan["memory"]:
        plan["refusals"].append("nothing to transfer in %s" % src_dir)
    conflicts, plan["target_checked"] = _target_conflicts(
        dst_dir, plan["sessions"], plan["memory"])
    plan["refusals"] += conflicts
    hits, plan["unreadable_procs"] = live_claude_processes(
        old_dirs, proc_root, self_pid=os.getpid() if self_pid is None else self_pid)
    plan["refusals"] += ["claude pid %d runs in %s — end that session first "
                         "(#1190)" % hit for hit in hits]
    return plan


def resume_hint(plan):
    """The lines the owner needs after the copy: the resume command (newest
    session first) and the first prompt that tells the model about the move."""
    if not plan["sessions"]:
        return ["(no sessions — only memory/ was transferred)"]
    cwd = shlex.quote(plan["target_cwd"])
    lines = ["as %s: cd %s && claude --resume %s"
             % (plan["account"], cwd, plan["sessions"][0]["uuid"])]
    lines += ["  older: claude --resume %s" % s["uuid"] for s in plan["sessions"][1:]]
    lines.append("first prompt after the resume: \"Projekt sa presťahoval z %s do "
                 "%s (vlastný účet %s, #1184). Cesty so starým umiestnením v tejto "
                 "konverzácii už neplatia — pracuj len v %s.\""
                 % (plan["from_dir"], plan["target_cwd"], plan["account"],
                    plan["target_cwd"]))
    return lines


def _when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def format_plan(plan):
    """The dry-run listing. A refused plan says so and prints no resume line
    (the reasons go to stderr, ``cli_accounts.transfer_session``)."""
    head = ("REFUSED — nothing will be copied (#1190); the reasons follow."
            if plan["refusals"] else
            "DRY RUN — nothing copied (#1190). `--render` prints the root "
            "script, `--apply` runs it (as root).")
    out = [head,
           "source: %s" % plan["src_dir"],
           "target: %s  (cwd %s, owner %s, files 0600 / dirs 0700, mtimes kept)"
           % (plan["dst_dir"], plan["target_cwd"], plan["account"]),
           "sessions (newest first):"]
    for s in plan["sessions"]:
        out.append("  %s.jsonl  %d B  %s" % (s["uuid"], s["jsonl_bytes"],
                                            _when(s["mtime"])))
        if s["has_dir"]:
            out.append("  %s/  %d B" % (s["uuid"], s["dir_bytes"]))
    if plan["memory"]:
        out.append("memory (%d B):" % plan["memory_bytes"])
        out += ["  memory/%s" % m for m in plan["memory"]]
    if plan["excluded_keys"]:
        out.append("excluded worktree-lane keys (not copied):")
        out += ["  %s" % k for k in plan["excluded_keys"]]
    if plan["other"]:
        out.append("not copied (neither a session nor memory):")
        out += ["  %s" % o for o in plan["other"]]
    if not plan["target_checked"]:
        out.append("target not readable here — the root script checks it for "
                   "existing sessions")
    if plan["unreadable_procs"]:
        out.append("%d processes not inspectable here — the root script re-runs "
                   "the live-claude guard" % plan["unreadable_procs"])
    if not plan["refusals"]:
        out.append("after the copy:")
        out += ["  " + ln for ln in resume_hint(plan)]
    return "\n".join(out)


_SCRIPT = """\
#!/usr/bin/env bash
set -euo pipefail
# every command below runs as root OR as an account: start from a cwd all of
# them can enter (a `sudo bash` cwd under the 0700 old home breaks find)
cd /

# airuleset session transfer into project account {account} (#1190)
# Generated by: python3 airuleset.py accounts transfer-session {account} --from-dir {from_dir} --render
# Run as root on the account's host. COPIES (the source stays). Root never
# reads file content from, or writes into, a path an account controls: the
# source is read AS its owner, the target is written AS the account. Refuses
# on a live claude in the old checkout, or on a session or memory file the
# target already has.

ACCOUNT={account}
SRC_OWNER={src_owner}
OLD_DIRS=({old_dirs})
SRC={src}
DST={dst}
PROC={proc}
TARGET_DIRS=({target_dirs})
SESSIONS=({sessions})
MEMORY_FILES=({memory})
ITEM_DIRS=({item_dirs})
ITEMS=({items})

echo "=== airuleset session transfer: $SRC -> $DST ==="

# 1. Live guard: no claude may run in the old checkout or one of its worktrees
#    (the same test as cli_account_session._is_claude, fixture-tested)
for p in "$PROC"/[0-9]*; do
    if [ "${{p##*/}}" = "$$" ]; then continue; fi
    cwd=$(readlink "$p/cwd" 2>/dev/null) || continue
    cwd=${{cwd% (deleted)}}
    under=0
    for d in "${{OLD_DIRS[@]}}"; do
        case "$cwd" in "$d"|"$d"/*) under=1 ;; esac
    done
    if [ "$under" = 0 ]; then continue; fi
    comm=$(cat "$p/comm" 2>/dev/null || true)
    exe=$(readlink "$p/exe" 2>/dev/null || true)
    argv=()
    mapfile -d '' -t argv < "$p/cmdline" 2>/dev/null || argv=()
    claude=0
    if [ "$comm" = claude ]; then claude=1; fi
    if [[ "$exe" == */claude/versions/* ]]; then claude=1; fi
    if [ "${{#argv[@]}}" -gt 0 ]; then
        a0=${{argv[0]##*/}}
        if [ "$a0" = claude ]; then claude=1; fi
        if [ "$a0" = node ] || [ "$a0" = nodejs ]; then
            for t in "${{argv[@]:1}}"; do
                if [ "${{t##*/}}" = claude ] || [[ "$t" == */claude-code/* ]]; then claude=1; fi
            done
        fi
    fi
    if [ "$claude" = 1 ]; then
        echo "REFUSED: claude pid ${{p##*/}} runs in $cwd — end that session first (#1190)" >&2
        exit 1
    fi
done

# 2. Never overwrite: refuse a session or memory file the target already has;
#    the source must still belong to the owner it was rendered for
if [ "$(stat -c %U -- "$SRC")" != "$SRC_OWNER" ]; then
    echo "REFUSED: $SRC is no longer owned by $SRC_OWNER — re-render (#1190)" >&2
    exit 1
fi
for u in "${{SESSIONS[@]}}"; do
    if [ -e "$DST/$u.jsonl" ] || [ -L "$DST/$u.jsonl" ] || [ -e "$DST/$u" ] || [ -L "$DST/$u" ]; then
        echo "REFUSED: session $u already exists in $DST (#1190)" >&2
        exit 1
    fi
done
for f in "${{MEMORY_FILES[@]}}"; do
    if [ -e "$DST/memory/$f" ] || [ -L "$DST/memory/$f" ]; then
        echo "REFUSED: memory/$f already exists in $DST — merge it by hand (#1190)" >&2
        exit 1
    fi
done
for stale in "$DST"/.airuleset-transfer-1190.*; do
    if [ -e "$stale" ]; then
        echo "WARNING: $stale is left over from an interrupted run — remove it as $ACCOUNT" >&2
    fi
done

# From here on, any failure names what was already placed and how to recover.
TMPD=""
PLACED=()
on_exit() {{
    rc=$?
    if [ -n "$TMPD" ]; then runuser -u "$ACCOUNT" -- rm -rf -- "$TMPD" || true; fi
    if [ "$rc" -ne 0 ]; then
        echo "FAILED (rc=$rc) — nothing was overwritten (#1190)." >&2
        echo "  Placed before the failure: ${{PLACED[*]:-none}}" >&2
        echo "  Recover: as $ACCOUNT remove exactly those from $DST, then re-run." >&2
    fi
    exit "$rc"
}}
trap on_exit EXIT

# 3. Read the source AS its owner and unpack it AS the account into a private
#    temp dir inside the target (same filesystem: step 4 only renames).
#    tar keeps the mtimes; the account sets the modes on its own copy.
for d in "${{TARGET_DIRS[@]}}"; do
    if [ ! -d "$d" ]; then runuser -u "$ACCOUNT" -- mkdir -m 0700 -- "$d"; fi
done
TMPD=$(runuser -u "$ACCOUNT" -- mktemp -d "$DST/.airuleset-transfer-1190.XXXXXX")
echo "  temp dir: $TMPD"
runuser -u "$SRC_OWNER" -- tar -C "$SRC" -cf - -- "${{ITEMS[@]}}" \\
    | runuser -u "$ACCOUNT" -- tar -C "$TMPD" -xpf -
runuser -u "$ACCOUNT" -- find "$TMPD" -type d -exec chmod 0700 {{}} +
runuser -u "$ACCOUNT" -- find "$TMPD" -type f -exec chmod 0600 {{}} +

# 4. Place each item with a no-clobber rename, as the account. An item that
#    appeared in the target meanwhile stops the run; nothing is overwritten.
for d in "${{ITEM_DIRS[@]}}"; do
    if [ ! -d "$DST/$d" ]; then runuser -u "$ACCOUNT" -- mkdir -m 0700 -- "$DST/$d"; fi
done
for item in "${{ITEMS[@]}}"; do
    runuser -u "$ACCOUNT" -- mv -n -T -- "$TMPD/$item" "$DST/$item" || true
    if [ -e "$TMPD/$item" ] || [ -L "$TMPD/$item" ]; then
        echo "FAILED: $DST/$item exists or could not be placed." >&2
        exit 1
    fi
    PLACED+=("$item")
done

# 5. Read-back
echo ""
echo "=== Read-back ==="
for u in "${{SESSIONS[@]}}"; do
    stat -c '  %U %a %y %n' -- "$DST/$u.jsonl"
done
echo "  placed items: ${{#PLACED[@]}}"
echo ""
echo "=== Resume ==="
{hint}
"""


def _items(plan):
    """The source-relative paths the script copies, in placement order."""
    items = []
    for s in plan["sessions"]:
        items.append(s["uuid"] + ".jsonl")
        if s["has_dir"]:
            items.append(s["uuid"])
    return items + ["memory/" + m for m in plan["memory"]]


def _item_dirs(items):
    """Every directory the items need inside the target key dir, parents
    first (``memory``, ``memory/sub``), so each is created 0700 on its own."""
    dirs = set()
    for item in items:
        parts = item.split("/")[:-1]
        dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(dirs, key=lambda d: (d.count("/"), d))


def _target_dirs(plan):
    """``~/.claude``, ``~/.claude/projects`` and the key dir, parents first."""
    key_dir = plan["dst_dir"]
    projects = os.path.dirname(key_dir)
    return [os.path.dirname(projects), projects, key_dir]


def render_script(plan):
    """The root script for ``plan``. Raises ValueError for a plan with
    refusals — a refused transfer is never rendered. Not idempotent: a
    finished (or half-finished) run leaves files a re-run refuses, and any
    failure names every item already placed."""
    if plan["refusals"]:
        raise ValueError("; ".join(plan["refusals"]))
    q = shlex.quote
    items = _items(plan)
    return _SCRIPT.format(
        account=q(plan["account"]), src_owner=q(plan["src_owner"]),
        from_dir=q(plan["from_dir"]),
        old_dirs=" ".join(q(d) for d in plan["old_dirs"]),
        src=q(plan["src_dir"]), dst=q(plan["dst_dir"]), proc=q(plan["proc_root"]),
        target_dirs=" ".join(q(d) for d in _target_dirs(plan)),
        sessions=" ".join(q(s["uuid"]) for s in plan["sessions"]),
        memory=" ".join(q(m) for m in plan["memory"]),
        item_dirs=" ".join(q(d) for d in _item_dirs(items)),
        items=" ".join(q(i) for i in items),
        hint="\n".join("echo %s" % q("  " + ln) for ln in resume_hint(plan)))
