"""gates.commitmsg -- the text a commit command's ticket refs are read from
(#1353), for gates.commitdesign.

The design gate used to scan the WHOLE Bash command, so a worker that appended
`#N` lines to an in-repo log (`cat >> docs/autopilot-log.md <<'EOF' … EOF &&
git commit -m "… (#41)"`) needed a design marker for every `#N` in the FILE.

Four review rounds shaped this. Building the message by POSITIVE parsing
leaked (heredoc capture, `split_top_level` and shlex each disagree with bash
somewhere), and so did every check that ENUMERATED dangerous shapes (a
respelt `-F` path, a glob, `sh s.s''h`, `git show :m > .git/MERGE_MSG`). So
everything here is a whitelist (#1307: name what may be removed, never what
must be found). The whole command is scanned EXCEPT the bodies of heredocs
that are provably inert file content, and only when the WHOLE command has this
shape:

  * each removed body belongs to a start line that is, in full, `[cd DIR &&]
    cat >[>] FILE <<'WORD'` (quoted delimiter: the body is never expanded),
    reached at top level (no open quote, comment or continuation) before any
    other heredoc operator; DIR/FILE has no `.git*` / `.husky` / `hooks`
    component (files git itself reads: MERGE_MSG, config, hooks), also after
    resolving symlinks from cwd and every literal `cd` target;
  * every other top-level segment is `cd DIR`, `git add|rm|status ARGS`, or
    `git commit` with only message / -a -q -s -v -n / --allow-empty /
    --no-verify flags (no -F, -t, -C, -c, -e, --no-edit, --fixup, …), with no
    unquoted redirect or subshell token;
  * outside the writer lines: no `$` expansion other than `$(cat <<…`, no
    backtick, no `<` redirect, no backslash;
  * at most _MAX_BODIES bodies (the checks stay linear-time).

Anything else keeps every ref (the old over-check). Message FILES (`-F path`)
are read from disk and only ever ADDED, so a stale or odd read over-checks.
Accepted residual: a custom `core.hooksPath` dir other than `hooks`/`.husky`
is not known (a hook there must already exist and be executable).
"""
import os
import re
import shlex

from gates.shellcmd import split_top_level

_MSGFILE_MAX_BYTES = 1_000_000
_MAX_CMD = 1_000_000            # above this, never prove anything (time bound)
_MAX_BODIES = 50
_MAX_CDS = 20
_MAX_SEGMENT = 64_000
_WRITER_LINE_RX = re.compile(
    r"[ \t]*(?:cd[ \t]+([\w./-]+)[ \t]*&&[ \t]*)?cat[ \t]*>>?[ \t]*([\w./-]+)"
    r"[ \t]*<<(-?)[ \t]*(['\"])(\w+)\4[ \t]*")
_CD_SEG_RX = re.compile(r"\s*cd\s+([\w./-]+)\s*")
_GIT_INTERNAL_RX = re.compile(r"(?:^|/)(?:\.git[^/]*|\.husky|hooks)(?:/|$)")
_GIT_REAL_RX = re.compile(r"(?:^|/)(?:\.git[^/]*|\.husky)(?:/|$)")
# Outside the writer lines: no expansion but `$(cat <<`, no backtick, no `<`
# redirect, no backslash (an escaped space makes a `#` look like a comment).
_UNSAFE_CHAR_RX = re.compile(r"\$(?!\(cat[ \t]*<<)|`|(?<!<)<(?!<)|\\")
_CD_TARGET_RX = re.compile(r"(?:^|[;&|(\n])[ \t]*cd[ \t]+([\w./-]+)")
_MSGFILE_RX = re.compile(
    r"(?<![\w-])(?:-[A-Za-z]*F|--file=?)[ \t]*(['\"]?)([^\s'\"<>|;&]+)\1")
# `git commit` options that neither read a file nor reuse/edit a message.
_COMMIT_FLAGS = frozenset((
    "-a", "-q", "-s", "-v", "-n", "--all", "--quiet", "--signoff", "--verbose",
    "--allow-empty", "--no-verify"))
_COMMIT_CLUSTER_RX = re.compile(r"-[aqsvn]+")
_COMMIT_MSG_CLUSTER_RX = re.compile(r"-[aqsvn]*m")


def _quote_state(line, state):
    """Bash quote state after `line`: None = top level, "'" / '"' = open quote,
    "\\\\" = a line continuation (the next line is not a fresh command). A comment
    ends the line."""
    if state == "\\":
        state = None
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if state == "'":
            if c == "'":
                state = None
        elif state == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                state = None
        elif c == "\\":
            if i == n - 1:
                return "\\"
            i += 1
        elif c in ("'", '"'):
            state = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t;&|("):
            break
        i += 1
    return state


def _writer_bodies(lines):
    """[(dir, file, start_line, terminator_line)] of quoted-delimiter heredocs
    whose start line is a top-level `[cd DIR &&] cat > FILE` writer; stops at
    the first other heredoc operator."""
    out, state, i = [], None, 0
    while i < len(lines):
        line = lines[i]
        m = _WRITER_LINE_RX.fullmatch(line) if state is None else None
        if m:
            delim, strip = m.group(5), m.group(3) == "-"
            j = i + 1
            while j < len(lines) and (lines[j].lstrip("\t") if strip else lines[j]) != delim:
                j += 1
            if j == len(lines):
                break                   # unterminated: bash reads to EOF
            out.append((m.group(1) or "", m.group(2), i, j))
            if len(out) > _MAX_BODIES:
                return []
            i = j + 1
            continue
        if "<<" in line:
            break                       # an unknown heredoc: stop proving
        state = _quote_state(line, state)
        i += 1
    return out


def _inert_target(d, name, dirs):
    """FILE is not a file git reads, literally or through a symlink from any
    directory the command may be in."""
    rel = os.path.join(d, name)
    if _GIT_INTERNAL_RX.search(rel):
        return False
    return not any(_GIT_REAL_RX.search(os.path.realpath(os.path.join(base, rel)))
                   for base in dirs if base)


def _tokens(seg):
    """shlex words with unquoted shell punctuation split out; None on doubt."""
    if len(seg) > _MAX_SEGMENT:
        return None
    try:
        lex = shlex.shlex(seg, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return None


def _commit_args_ok(args):
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            return True
        if a in ("-m", "--message") or _COMMIT_MSG_CLUSTER_RX.fullmatch(a):
            i += 1                      # the message value
        elif a.startswith(("--message=", "-m")) or a in _COMMIT_FLAGS \
                or _COMMIT_CLUSTER_RX.fullmatch(a):
            continue
        elif a.startswith("-"):
            return False                # -F, -t, -C, -c, --no-edit, -e, --fixup, …
    return True


def _segment_ok(seg):
    """`cd DIR` or `git add|rm|status|commit` (commit with message flags only)."""
    if not seg.strip():
        return True
    cd = _CD_SEG_RX.fullmatch(seg)
    if cd:
        return not _GIT_INTERNAL_RX.search(cd.group(1))
    tk = _tokens(seg)
    if not tk or len(tk) < 2 or tk[0] != "git":
        return False
    if any(t and all(ch in "();<>|&" for ch in t) for t in tk):
        return False                    # an unquoted redirect / subshell
    if tk[1] in ("add", "rm", "status"):
        return True
    return tk[1] == "commit" and _commit_args_ok(tk[2:])


def scan_text(cmd, cwd=None):
    """`cmd` minus the heredoc bodies that are provably inert file content."""
    cmd = cmd or ""
    if len(cmd) > _MAX_CMD:
        return cmd
    lines = cmd.split("\n")
    bodies = _writer_bodies(lines)
    if not bodies:
        return cmd
    dirs = _candidate_dirs(cmd, cwd) if cwd else []
    if not all(_inert_target(d, name, dirs) for d, name, _, _ in bodies):
        return cmd
    body_lines, writer_lines = set(), set()
    for _, _, a, b in bodies:
        writer_lines.add(a)
        body_lines.update(range(a + 1, b + 1))
    check = "\n".join(ln for k, ln in enumerate(lines)
                      if k not in body_lines and k not in writer_lines)
    if _UNSAFE_CHAR_RX.search(check) or not all(_segment_ok(s) for s in split_top_level(check)):
        return cmd
    return "\n".join(ln for k, ln in enumerate(lines) if k not in body_lines)


def _candidate_dirs(cmd, cwd):
    """cwd plus the first literal `cd` targets, cumulatively (over-reading is
    harmless: the result only ADDS refs)."""
    dirs, cur = [cwd], cwd
    for k, m in enumerate(_CD_TARGET_RX.finditer(cmd)):
        if k >= _MAX_CDS:
            break
        cur = os.path.normpath(os.path.join(cur, m.group(1)))
        dirs.append(cur)
    return list(dict.fromkeys(dirs))


def msgfile_text(cmd, cwd):
    """Contents of the regular files `-F`/`--file` names (best effort, additive)."""
    cmd, out, seen = cmd or "", [], set()
    if not cwd or len(cmd) > _MAX_CMD:
        return ""
    paths = [m.group(2) for m in _MSGFILE_RX.finditer(cmd) if m.group(2) != "-"][:20]
    dirs = _candidate_dirs(cmd, cwd) if paths else []
    for path in paths:
        for d in dirs:
            real = os.path.realpath(os.path.join(d, path))
            if real in seen or real.startswith(("/dev/", "/proc/")) or not os.path.isfile(real):
                continue
            seen.add(real)
            try:
                with open(real, "r", encoding="utf-8", errors="replace") as fh:
                    out.append(fh.read(_MSGFILE_MAX_BYTES))
            except OSError:
                continue
    return "\n".join(out)
