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
``accounts transfer-session`` (``cli_accounts``) is the CLI. This leaf is the
pure half: the key encoder, the live-process guard, the plan, and the root
script it renders (the ``account-bootstrap --render`` shape).

Not copied, on purpose:

  * the worktree-lane keys (``<key>--claude-worktrees-*``). Those are the
    supervisor's dead lanes, not the owner's conversation;
  * ``file-history``/``todos``. ``--resume`` does not need them (proved on
    2.1.284 below), and a rewind of file edits recorded under the old path
    would restore files into a checkout that no longer exists.

The jsonl ``cwd`` field is NOT rewritten. Evidence: Claude Code 2.1.284,
throwaway runs under a scratch HOME (#1190 issuecomment-5898853002):

  1. The key is the cwd with EVERY non-``[A-Za-z0-9]`` char turned into ``-``:
     ``.../a.b_c d`` -> ``...-a-b-c-d``.
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
import re
import shlex
import stat
import time

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Claude Code shortens (hashes) keys longer than this; our project paths never
# are, so a longer one is refused instead of guessing the hash.
MAX_KEY_LEN = 200
# A worktree lane's cwd is `<checkout>/.claude/worktrees/<name>`, so its key is
# `<checkout key>--claude-worktrees-<name>`.
WORKTREE_KEY_INFIX = "--claude-worktrees-"


def project_key(cwd):
    """Claude Code's ``~/.claude/projects`` directory name for ``cwd``."""
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        raise ValueError("not an absolute path: %r" % (cwd,))
    key = "".join(c if c.isascii() and c.isalnum() else "-" for c in cwd)
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


def _is_claude(comm, cmdline):
    argv0 = cmdline.split(b"\0", 1)[0].decode("utf-8", "replace")
    return (comm == "claude" or os.path.basename(argv0) == "claude"
            or b"claude-code" in cmdline)


def _read(path, mode="r"):
    try:
        with open(path, mode) as h:
            return h.read()
    except OSError:
        return b"" if "b" in mode else ""


def live_claude_processes(old_dir, proc_root="/proc", self_pid=None):
    """([(pid, cwd)] of claude processes whose cwd is ``old_dir`` or below it
    (its worktrees included), number of processes whose cwd was unreadable).
    A non-root caller cannot read other users' cwd; the root script re-checks."""
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
        if cwd != old_dir and not cwd.startswith(old_dir.rstrip("/") + "/"):
            continue
        if _is_claude(_read(os.path.join(base, "comm")).strip(),
                      _read(os.path.join(base, "cmdline"), "rb")):
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


def build_plan(account, from_dir, *, from_home, target_home, target_cwd,
               proc_root="/proc", stage_base="/tmp", self_pid=None):
    """What a transfer of ``from_dir``'s conversation into ``account`` would
    copy, and every reason it must not (``refusals``, empty = go)."""
    for label, path in (("--from-dir", from_dir), ("target cwd", target_cwd),
                        ("--from-home", from_home), ("target home", target_home)):
        if not isinstance(path, str) or not path.startswith("/") or any(
                ord(c) < 32 or ord(c) == 127 for c in path):
            raise ValueError("%s must be an absolute path without control "
                             "characters: %r" % (label, path))
    from_dir = os.path.normpath(from_dir)
    src_dir = os.path.join(from_home, ".claude", "projects", project_key(from_dir))
    dst_dir = os.path.join(target_home, ".claude", "projects", project_key(target_cwd))
    plan = {"account": account, "from_dir": from_dir, "from_home": from_home,
            "target_cwd": target_cwd, "src_dir": src_dir, "dst_dir": dst_dir,
            "proc_root": proc_root, "stage_base": stage_base, "sessions": [],
            "memory": [], "memory_bytes": 0, "other": [], "excluded_keys": [],
            "refusals": [], "target_checked": True, "unreadable_procs": 0}
    if src_dir == dst_dir:
        plan["refusals"].append("source and target are the same key dir %s" % src_dir)
        return plan
    if not _is_real_dir(src_dir):
        plan["refusals"].append("no Claude sessions for %s (%s is missing)"
                                % (from_dir, src_dir))
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
        from_dir, proc_root, self_pid=os.getpid() if self_pid is None else self_pid)
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
    """The dry-run listing."""
    out = ["DRY RUN — nothing copied (#1190). `--render` prints the root "
           "script, `--apply` runs it (as root).",
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
    out.append("after the copy:")
    out += ["  " + ln for ln in resume_hint(plan)]
    return "\n".join(out)


_SCRIPT = """\
#!/usr/bin/env bash
set -euo pipefail

# airuleset session transfer into project account {account} (#1190)
# Generated by: python3 airuleset.py accounts transfer-session {account} --from-dir {from_dir} --render
# Run as root on the account's host. COPIES (the source stays); refuses on a
# live claude in the old checkout or on a session/memory file already present.

ACCOUNT={account}
OLD_DIR={from_dir}
SRC={src}
DST={dst}
PROC={proc}
SESSIONS=({sessions})
MEMORY_FILES=({memory})

echo "=== airuleset session transfer: $SRC -> $DST ==="

# 1. Live guard: no claude may run in the old checkout or one of its worktrees
for p in "$PROC"/[0-9]*; do
    [ "${{p##*/}}" = "$$" ] && continue
    cwd=$(readlink "$p/cwd" 2>/dev/null) || continue
    cwd=${{cwd% (deleted)}}
    case "$cwd" in "$OLD_DIR"|"$OLD_DIR"/*) ;; *) continue ;; esac
    comm=$(cat "$p/comm" 2>/dev/null || true)
    argv0=$(tr '\\0' '\\n' < "$p/cmdline" 2>/dev/null | head -n 1 || true)
    cmdline=$(tr '\\0' ' ' < "$p/cmdline" 2>/dev/null || true)
    if [ "$comm" = claude ] || [ "${{argv0##*/}}" = claude ] || [[ "$cmdline" == *claude-code* ]]; then
        echo "REFUSED: claude pid ${{p##*/}} runs in $cwd — end that session first (#1190)" >&2
        exit 1
    fi
done

# 2. Never overwrite: refuse a session or memory file the target already has
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

# 3. Stage a copy root-side. Modes are set while it is still root-owned, then
#    it is chowned: the account never owns a path a root chmod later follows.
STAGE=$(mktemp -d {stage_base}/airuleset-session-1190.XXXXXX)
trap 'rm -rf -- "$STAGE"' EXIT
chmod 0711 "$STAGE"
mkdir -m 0700 "$STAGE/copy"
for u in "${{SESSIONS[@]}}"; do
    cp -a -- "$SRC/$u.jsonl" "$STAGE/copy/"
    if [ -d "$SRC/$u" ]; then cp -a -- "$SRC/$u" "$STAGE/copy/"; fi
done
if [ "${{#MEMORY_FILES[@]}}" -gt 0 ]; then
    mkdir -m 0700 "$STAGE/copy/memory"
    for f in "${{MEMORY_FILES[@]}}"; do
        mkdir -p -- "$STAGE/copy/memory/$(dirname -- "$f")"
        cp -a -- "$SRC/memory/$f" "$STAGE/copy/memory/$f"
    done
fi
find "$STAGE/copy" -type d -exec chmod 0700 {{}} +
find "$STAGE/copy" -type f -exec chmod 0600 {{}} +
chown -R "$ACCOUNT:$ACCOUNT" "$STAGE/copy"

# 4. Place it AS the account: every write into its home runs with its uid
runuser -u "$ACCOUNT" -- mkdir -p -m 0700 -- "$DST"
for u in "${{SESSIONS[@]}}"; do
    runuser -u "$ACCOUNT" -- cp -a -- "$STAGE/copy/$u.jsonl" "$DST/"
    if [ -d "$STAGE/copy/$u" ]; then
        runuser -u "$ACCOUNT" -- cp -a -- "$STAGE/copy/$u" "$DST/"
    fi
done
if [ "${{#MEMORY_FILES[@]}}" -gt 0 ]; then
    for f in "${{MEMORY_FILES[@]}}"; do
        runuser -u "$ACCOUNT" -- mkdir -p -m 0700 -- "$DST/memory/$(dirname -- "$f")"
        runuser -u "$ACCOUNT" -- cp -a -- "$STAGE/copy/memory/$f" "$DST/memory/$f"
    done
fi

# 5. Read-back
echo ""
echo "=== Read-back ==="
for u in "${{SESSIONS[@]}}"; do
    stat -c '  %U %a %y %n' "$DST/$u.jsonl"
done
echo "  memory files: ${{#MEMORY_FILES[@]}}"
echo ""
echo "=== Resume ==="
{hint}
"""


def render_script(plan):
    """The idempotent-by-refusal root script for ``plan``. Raises ValueError
    for a plan with refusals — a refused transfer is never rendered."""
    if plan["refusals"]:
        raise ValueError("; ".join(plan["refusals"]))
    q = shlex.quote
    return _SCRIPT.format(
        account=q(plan["account"]), from_dir=q(plan["from_dir"]),
        src=q(plan["src_dir"]), dst=q(plan["dst_dir"]),
        proc=q(plan["proc_root"]), stage_base=q(plan["stage_base"]),
        sessions=" ".join(q(s["uuid"]) for s in plan["sessions"]),
        memory=" ".join(q(m) for m in plan["memory"]),
        hint="\n".join("echo %s" % q("  " + ln) for ln in resume_hint(plan)))
