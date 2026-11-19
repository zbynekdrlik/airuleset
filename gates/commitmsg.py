"""gates.commitmsg -- the text a commit command's ticket refs are read from
(#1353), for gates.commitdesign.

The design gate used to scan the WHOLE Bash command, so a worker that appended
`#N` lines to an in-repo log (`cat >> docs/autopilot-log.md <<'EOF' … EOF &&
git commit -m "… (#41)"`) needed a design marker for every `#N` in the FILE.

Three review rounds shaped this. Building the message by POSITIVE parsing
leaked (heredoc capture, `split_top_level` and shlex each disagree with bash
somewhere), and so did every check that ENUMERATED dangerous shapes (a
respelt `-F` path, a glob, `sh s.s''h`). So everything here is a whitelist
(#1307: name what may be removed, never what must be found). The whole command
is scanned EXCEPT the bodies of heredocs that are provably inert file content,
and only when the WHOLE command has this shape:

  * each removed body belongs to a start line that is, in full, `[cd DIR &&]
    cat >[>] FILE <<'WORD'` (quoted delimiter: the body is never expanded),
    reached at top level (no open quote, comment or continuation) before any
    other heredoc operator; FILE has no `.git*` / `.husky` / `hooks` component
    (files git itself reads: MERGE_MSG, config, hooks) and does not resolve into
    one through a symlink in cwd;
  * every other top-level segment is `cd DIR`, such a writer, or `git add|rm|mv|
    diff|status|log|show|restore|commit` -- nothing that can execute a file;
  * nothing can read a file into a message: no `-F`/`-t`/`--file`/`--template`,
    no `$` expansion other than `$(cat <<…`, no backtick, no `<` redirect;
  * at most _MAX_BODIES bodies (the checks stay linear-time).

Anything else keeps every ref (the old over-check). Message FILES (`-F path`)
are read from disk and only ever ADDED, so a stale or odd read over-checks.
Accepted residuals: a `cd` before a writer is not followed when the symlink
check resolves FILE (the `.git` component check on the literal path still is),
and a custom `core.hooksPath` dir other than `hooks`/`.husky` is not known.
"""
import os
import re

from gates.shellcmd import split_top_level

_MSGFILE_MAX_BYTES = 1_000_000
_MAX_CMD = 1_000_000            # above this, never prove anything (time bound)
_MAX_BODIES = 50
_MAX_CDS = 20
_WRITER_LINE_RX = re.compile(
    r"[ \t]*(?:cd[ \t]+[\w./-]+[ \t]*&&[ \t]*)?cat[ \t]*>>?[ \t]*([\w./-]+)"
    r"[ \t]*<<(-?)[ \t]*(['\"])(\w+)\3[ \t]*")
_WRITER_SEG_RX = re.compile(r"\s*cat\s*>>?\s*[\w./-]+\s*<<-?\s*(['\"])\w+\1\s*")
_CD_SEG_RX = re.compile(r"\s*cd\s+[\w./-]+\s*")
_GIT_SEG_RX = re.compile(
    r"\s*git\s+(?:add|rm|mv|diff|status|log|show|restore|commit)(?:\s[\s\S]*)?")
_GIT_INTERNAL_RX = re.compile(r"(?:^|/)(?:\.git[^/]*|\.husky|hooks)(?:/|$)")
_GIT_REAL_RX = re.compile(r"(?:^|/)(?:\.git[^/]*|\.husky)(?:/|$)")
# Anything that could pull a file's content into a commit message.
_FILE_FLAG_RX = re.compile(r"(?<![\w-])(?:-[A-Za-z]*[Ft]|--(?:fi|te))")
_EXPANSION_RX = re.compile(r"\$(?!\(cat[ \t]*<<)|`|(?<!<)<(?!<)")
_CD_TARGET_RX = re.compile(r"(?:^|[;&|(\n])[ \t]*cd[ \t]+([\w./-]+)")
_MSGFILE_RX = re.compile(
    r"(?<![\w-])(?:-[A-Za-z]*F|--file=?)[ \t]*(['\"]?)([^\s'\"<>|;&]+)\1")


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
    """[(file, first_body_line, terminator_line)] of quoted-delimiter heredocs
    whose start line is a top-level `cat > FILE` writer; stops at the first
    other heredoc operator."""
    out, state, i = [], None, 0
    while i < len(lines):
        line = lines[i]
        m = _WRITER_LINE_RX.fullmatch(line) if state is None else None
        if m:
            delim, strip = m.group(4), m.group(2) == "-"
            j = i + 1
            while j < len(lines) and (lines[j].lstrip("\t") if strip else lines[j]) != delim:
                j += 1
            if j == len(lines):
                break                   # unterminated: bash reads to EOF
            out.append((m.group(1), i + 1, j))
            if len(out) > _MAX_BODIES:
                return []
            i = j + 1
            continue
        if "<<" in line:
            break                       # an unknown heredoc: stop proving
        state = _quote_state(line, state)
        i += 1
    return out


def _inert_target(name, cwd):
    if _GIT_INTERNAL_RX.search(name):
        return False
    if cwd:
        real = os.path.realpath(os.path.join(cwd, name))
        if _GIT_REAL_RX.search(real):
            return False
    return True


def _whitelisted(seg):
    return (not seg.strip() or _CD_SEG_RX.fullmatch(seg) or _WRITER_SEG_RX.fullmatch(seg)
            or _GIT_SEG_RX.fullmatch(seg))


def scan_text(cmd, cwd=None):
    """`cmd` minus the heredoc bodies that are provably inert file content."""
    cmd = cmd or ""
    if len(cmd) > _MAX_CMD:
        return cmd
    lines = cmd.split("\n")
    bodies = _writer_bodies(lines)
    if not bodies or not all(_inert_target(name, cwd) for name, _, _ in bodies):
        return cmd
    drop = set()
    for _, a, b in bodies:
        drop.update(range(a, b + 1))
    rest = "\n".join(ln for k, ln in enumerate(lines) if k not in drop)
    if _FILE_FLAG_RX.search(rest) or _EXPANSION_RX.search(rest):
        return cmd
    if not all(_whitelisted(seg) for seg in split_top_level(rest)):
        return cmd
    return rest


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
