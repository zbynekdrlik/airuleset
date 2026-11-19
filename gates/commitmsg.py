"""gates.commitmsg -- the text of the `git commit` message(s) a Bash command
records (#1353), for gates.commitdesign's ticket-ref scan.

The design gate used to scan the WHOLE command, so a worker that appended `#N`
lines to an in-repo log (`cat >> docs/autopilot-log.md <<'EOF' … EOF && git
commit -m "… (#41)"`) needed a design marker for every `#N` in the FILE. Now the
refs come from what git puts in the message: `-m`/`--message`, `--trailer`, a
`-F`/`--file` file, or a heredoc feeding `-F -` / `-m "$(cat <<EOF …)"`.

`message_text` returns None whenever the message cannot be isolated with
certainty; the caller then scans the whole command (the old, over-checking
behaviour). A message FILE is read from disk only when every other segment of
the command is on a short allowlist (cd, read-only git, a literal `cat > f
<<EOF`), because the hook runs BEFORE the command: a file the command rewrites
by any other means would be read stale (review of #1353: enumerate what KEEPS
the disk copy trustworthy, never the writers that spoil it -- #1307's lesson).

Bounds: only segments that can matter (`git commit` / `cd`) are tokenised, and
a segment over _MAX_SEGMENT bytes is never tokenised (shlex is quadratic on a
long token) -- it falls back instead.
"""
import os
import re
import shlex

from gates.selfservice import (
    _CATFILE_RE, _HEREDOC_RE, _apply_cd, _capture_heredocs, _strip_prefix,
)
from gates.shellcmd import split_top_level

_MAX_SEGMENT = 64_000
_MSGFILE_MAX_BYTES = 1_000_000
_COMMIT_WORDS_RX = re.compile(r"\bgit\s+commit\b")
_CD_RX = re.compile(r"\s*cd\b")
_HEREDOC_DELIM_RX = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1")
_CAT_HEREDOC_SUBST_RX = re.compile(r"\$\(\s*cat\s*<<-?[ \t]*(['\"]?)(\w+)\1\s*\)")
_SAFE_SEGMENT_RX = re.compile(
    r"\s*(?:true|:|git\s+(?:add|status|diff|log|show|rev-parse)(?:\s[^<>$`\n]*)?)\s*")
_CAT_WRITER_RX = re.compile(
    r"\s*cat\s*(>>?)\s*(['\"]?)([^\s<>&;|'\"$`*?~\[]+)\2\s*<<-?[ \t]*(['\"]?)\w+\4\s*")
# Options whose message text is not in the command (reused / templated).
_UNRESOLVABLE_LONG = ("reuse-message", "reedit-message", "fixup", "squash", "template")
_UNRESOLVABLE_SHORT = "cCt"
_TEXT_LONG = ("message", "trailer")


class _Doubt(Exception):
    """The message cannot be isolated with certainty -> whole-command scan."""


def _tokens(seg):
    """shlex tokens of a segment; a `#` shlex would read as a comment where bash
    would not (mid-word) -- or any parse error -- is doubt."""
    try:
        with_comments = shlex.split(seg, comments=True)
        without = shlex.split(seg, comments=False)
    except ValueError:
        raise _Doubt()
    if with_comments != without[:len(with_comments)]:
        raise _Doubt()
    if len(with_comments) != len(without) and not without[len(with_comments)].startswith("#"):
        raise _Doubt()
    return _strip_prefix(with_comments)


def _long_kind(name):
    if len(name) < 2:
        return None
    if any(o.startswith(name) for o in _UNRESOLVABLE_LONG):
        raise _Doubt()
    if len(name) >= 3 and any(o.startswith(name) for o in _TEXT_LONG):
        return "m"
    if len(name) >= 3 and "file".startswith(name):
        return "F"
    return None


def _sources(args):
    """[(kind, value)] for one `git commit`'s args, kind 'm' (literal text) or
    'F' (a file). No message option at all (editor) is doubt."""
    out, i, n = [], 0, len(args)
    while i < n:
        t = args[i]
        i += 1
        if t == "--":
            break
        if t.startswith("--"):
            name, eq, val = t[2:].partition("=")
            kind = _long_kind(name)
            if kind is None:
                continue
            if not eq:
                if i >= n:
                    raise _Doubt()
                val, i = args[i], i + 1
            out.append((kind, val))
            continue
        if not t.startswith("-") or t == "-":
            continue
        for k, ch in enumerate(t[1:], 1):
            if ch in _UNRESOLVABLE_SHORT:
                raise _Doubt()
            if ch in "uS":          # optional value glued to the flag
                break
            if ch in "mF":
                val = t[k + 1:]
                if not val:
                    if i >= n:
                        raise _Doubt()
                    val, i = args[i], i + 1
                out.append((ch, val))
                break
    if not out:
        raise _Doubt()
    return out


class _Scan:
    """One pass over the command: heredoc capture, segments, cwd, writers."""

    def __init__(self, cmd, cwd):
        self.cmd = cmd
        self.file_bodies, self.direct_bodies, skeleton = _capture_heredocs(cmd)
        self.starts = {}
        for line in cmd.split("\n"):
            m = _HEREDOC_RE.search(line.rstrip())
            if m and not _CATFILE_RE.match(line):
                self.starts[m.group(2)] = self.starts.get(m.group(2), 0) + 1
        self.commits, self.cat_writes, self.unsafe = [], [], False
        eff_cwd = cwd or None
        for seg in split_top_level(skeleton):
            if not seg.strip():
                continue
            eff_cwd = self._segment(seg, eff_cwd)
        # A `cat > f <<EOF` body is trusted only when f's literal name is written
        # once (file_bodies keys by name and holds line-start cats only).
        names = [key for _, key, _ in self.cat_writes]
        self.written = {
            path: (self.file_bodies.get(key) if op == ">" and names.count(key) == 1 else None)
            for path, key, op in self.cat_writes}

    def _segment(self, seg, eff_cwd):
        is_commit = bool(_COMMIT_WORDS_RX.search(seg))
        if is_commit or _CD_RX.match(seg):
            if len(seg) > _MAX_SEGMENT:
                raise _Doubt()
            tk = _tokens(seg)
            if is_commit:
                if tk[:2] != ["git", "commit"]:
                    raise _Doubt()      # a wrapper / subshell / echo of a commit
                self.commits.append((seg, eff_cwd, _sources(tk[2:])))
                return eff_cwd
            if tk and tk[0] == "cd":
                return _apply_cd(eff_cwd, tk)
        m = _CAT_WRITER_RX.fullmatch(seg)
        if m and eff_cwd:
            path = os.path.normpath(os.path.join(eff_cwd, m.group(3)))
            self.cat_writes.append((path, m.group(3), m.group(1)))
        elif m:
            self.unsafe = True          # a write into an unresolvable cwd
        elif not _SAFE_SEGMENT_RX.fullmatch(seg):
            self.unsafe = True
        return eff_cwd

    def heredoc(self, delim):
        if self.starts.get(delim) != 1 or delim not in self.direct_bodies:
            raise _Doubt()
        return self.direct_bodies[delim]

    def inline(self, value):
        parts = [self.heredoc(m.group(2)) for m in _CAT_HEREDOC_SUBST_RX.finditer(value)]
        rest = _CAT_HEREDOC_SUBST_RX.sub(" ", value)
        if "$" in rest or "`" in rest:
            raise _Doubt()
        return "\n".join([rest] + parts)

    def file(self, path, seg, eff_cwd):
        if path == "-":
            delims = [m.group(2) for m in _HEREDOC_DELIM_RX.finditer(seg)]
            if len(delims) != 1:
                raise _Doubt()
            return self.heredoc(delims[0])
        if self.unsafe or not eff_cwd or any(ch in path for ch in "$`*?~["):
            raise _Doubt()
        full = os.path.normpath(os.path.join(eff_cwd, path))
        if full in self.written:
            body = self.written[full]
            if body is None:
                raise _Doubt()
            return body
        real = os.path.realpath(full)
        if real.startswith(("/dev/", "/proc/")) or not os.path.isfile(real):
            raise _Doubt()
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read(_MSGFILE_MAX_BYTES)
        except OSError:
            raise _Doubt()


def message_text(cmd, cwd):
    """Every commit message `cmd` records, joined; None on any doubt."""
    try:
        scan = _Scan(cmd or "", cwd)
        if not scan.commits:
            return None
        texts = []
        for seg, eff_cwd, sources in scan.commits:
            for kind, val in sources:
                texts.append(scan.inline(val) if kind == "m"
                             else scan.file(val, seg, eff_cwd))
        return "\n".join(texts)
    except _Doubt:
        return None
