"""gates.commitmsg -- the text a commit command's ticket refs are read from
(#1353), for gates.commitdesign.

The design gate used to scan the WHOLE Bash command, so a worker that appended
`#N` lines to an in-repo log (`cat >> docs/autopilot-log.md <<'EOF' … EOF &&
git commit -m "… (#41)"`) needed a design marker for every `#N` in the FILE.

Two review rounds showed that building the message by POSITIVE parsing (which
commit, which -m) leaks: heredoc capture, `split_top_level` and shlex each
disagree with bash somewhere (a quoted `cat <<EOF` line in a message, `git -c x
commit`, a line continuation, `--tr`), and every disagreement dropped a real
ref. So the invariant is inverted (#1307: name what may be REMOVED, never what
must be found): the whole command is scanned EXCEPT heredoc bodies that are
PROVABLY file content:

  * the heredoc's start line is, in full, `[cd DIR &&] cat >[>] FILE <<[-]WORD`,
    reached at top level (no open quote, no comment) with no other heredoc
    operator before it -- so the line is real shell, not message text;
  * nothing in the command can read a written file into a message: no
    `-t`/`--template`, no `-F`/`--file` naming a written file or a glob (`-F -`
    fed by a heredoc is fine), no `$` expansion other than `$(cat <<…`, no
    backtick, no `<` input redirect;
  * FILE is named elsewhere only by git subcommands that cannot execute it
    (add/rm/mv/diff/status/log/show/restore/commit) or another such writer;
  * the body does not itself mention `git commit` (a script being written).

Anything else keeps its refs (the old over-check). Message FILES (`-F path`) are
read from disk and ADDED, so a stale or odd read can only over-check.
"""
import os
import re

from gates.shellcmd import split_top_level

_MSGFILE_MAX_BYTES = 1_000_000
_MAX_CMD = 1_000_000            # above this, never prove anything (time bound)
_WRITER_LINE_RX = re.compile(
    r"[ \t]*(?:cd[ \t]+[\w./-]+[ \t]*&&[ \t]*)?cat[ \t]*>>?[ \t]*([\w./-]+)"
    r"[ \t]*<<(-?)[ \t]*(['\"]?)(\w+)\3[ \t]*")
_WRITER_SEG_RX = re.compile(
    r"\s*(?:cd\s+[\w./-]+|cat\s*>>?\s*[\w./-]+\s*<<-?\s*(['\"]?)\w+\1)\s*")
_GIT_SEG_RX = re.compile(
    r"\s*(?:sudo\s+)?git\s+(?:add|rm|mv|diff|status|log|show|restore|commit)\b")
_BODY_COMMIT_RX = re.compile(r"\bgit\b[^\n]{0,200}?\bcommit\b")
# Anything that could pull a file's content into a commit message.
_FILE_FLAG_RX = re.compile(r"(?<![\w-])(?:-[A-Za-z]*[Ft]|--(?:fi|te))")
_STDIN_HEREDOC_RX = re.compile(r"[ \t]*-[ \t]*<<")
_EXPANSION_RX = re.compile(r"\$(?!\(cat[ \t]*<<)|`|(?<!<)<(?!<)")
_CD_TARGET_RX = re.compile(r"(?:^|[;&|(\n])[ \t]*cd[ \t]+([\w./-]+)")
_MSGFILE_RX = re.compile(
    r"(?<![\w-])(?:-[A-Za-z]*F|--file=?)[ \t]*(['\"]?)([^\s'\"<>|;&]+)\1")


def _quote_state(line, state):
    """Bash quote state after `line`: None = top level, "'" / '"' = open quote,
    "\\" = a line continuation (the next line is not a fresh command). A comment
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


def _writer_bodies(cmd):
    """[(file, start_line, end_line)] of heredocs whose start line is a top-level
    `cat > FILE <<WORD` writer; stops at the first other heredoc operator."""
    lines = cmd.split("\n")
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
            i = j + 1
            continue
        if "<<" in line:
            break                       # an unknown heredoc: stop proving
        state = _quote_state(line, state)
        i += 1
    return out, lines


def _reads_files(rest, names):
    """True when `rest` could pull a written file into a message: a template, a
    `-F`/`--file` naming a written file (or a glob), any expansion or `<`."""
    bases = {os.path.basename(n) for n in names}
    for m in _FILE_FLAG_RX.finditer(rest):
        if _STDIN_HEREDOC_RX.match(rest, m.end()):
            continue
        f = _MSGFILE_RX.match(rest, m.start())
        if not f or not m.group(0).endswith(("F", "fi")):
            return True             # -t / --template / an unparsable -F
        val = f.group(2)
        if any(ch in val for ch in "*?[~") or os.path.basename(val) in bases:
            return True
    return bool(_EXPANSION_RX.search(rest))


def _named_only_safely(name, rest):
    base = os.path.basename(name)
    for seg in split_top_level(rest):
        if base in seg and not (_GIT_SEG_RX.match(seg) or _WRITER_SEG_RX.fullmatch(seg)):
            return False
    return True


def scan_text(cmd):
    """`cmd` minus the heredoc bodies that are provably file content."""
    cmd = cmd or ""
    if len(cmd) > _MAX_CMD:
        return cmd
    bodies, lines = _writer_bodies(cmd)
    if not bodies:
        return cmd
    drop = set()
    for _, a, b in bodies:
        drop.update(range(a, b))
    rest = "\n".join(ln for k, ln in enumerate(lines) if k not in drop)
    if _reads_files(rest, [name for name, _, _ in bodies]):
        return cmd
    for name, a, b in bodies:
        if _BODY_COMMIT_RX.search("\n".join(lines[a:b])) or not _named_only_safely(name, rest):
            return cmd
    return rest


def _candidate_dirs(cmd, cwd):
    """cwd plus every literal `cd` target, cumulatively (over-reading is harmless:
    the result only ADDS refs)."""
    dirs, cur = [cwd], cwd
    for m in _CD_TARGET_RX.finditer(cmd):
        cur = os.path.normpath(os.path.join(cur, m.group(1)))
        dirs.append(cur)
    return list(dict.fromkeys(dirs))[:20]


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
