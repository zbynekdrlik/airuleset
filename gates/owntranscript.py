"""#1224 ask 2: a session never reads its OWN transcript with a content reader.

The songplayer session froze dev1 for 19 minutes by loading its own 598 MB
transcript (``python3 -`` at 2.0 GB RSS plus ``grep -o 'Otázka — projekt …'`` at
622 MB) to find the block of a question it had asked before. That block already
lives in ``~/.claude/discord-questions.json``, and ``airuleset.py
question-block`` prints it, so a session never needs the transcript for it.

The adapter ``hooks/block-own-transcript-read.sh`` runs this module only when
the command names ``<session-id>.jsonl`` or ``.claude/projects`` at all (a bash
substring pre-filter), so the other Bash calls pay no python start. The bare
session id is NOT a trigger: it is also in the session's scratchpad, task-output
and tool-results paths, which are ordinary files.

Per command segment (``&&``/``||``/``;``/``|``/``&`` and every ``$(…)`` or
backtick body):
* a command whose arguments name the transcript, a ``*.jsonl`` under
  ``.claude/projects`` (or a ``*.jsonl.*`` backup there), or that reads
  ``.claude/projects`` recursively (``-r``, ``rg``) is judged below; a TEXT head
  that only NAMES such a path (``gh --body``, ``git commit -m``, ``echo``) is
  never a read (live false positive, 2026-10-13);
* a heredoc body is data, unless an interpreter consumes it
  (``python3 - <<EOF``) and its program names a transcript;
* metadata (``ALLOWED_HEADS``) passes, and ``head``/``tail`` pass with ONE
  numeric ``-n`` ≤ ``MAX_LINES`` or ``-c`` ≤ ``MAX_BYTES`` (never ``+N``/follow);
* #1395, DENY BY DEFAULT per pipeline: every stage whose arguments name a
  transcript (any head but the text/metadata ones) must be either
  COUNT/LOCATE-only (``grep``/``rg -c -l -L -q``, ``wc``, a bounded head/tail,
  ``airuleset.py session-shrink``, ``ls``/``stat``/``du``), or a STREAMING
  reader (grep family, ``awk``, ``sed`` without ``-i``, ``cut``, ``zcat``,
  ``jq --stream``) followed by a LAST stage that bounds the output (head/tail
  -n <= 50 or -c <= 64 KiB, ``wc``, ``grep -c/-q/-l``). ``sort``/``uniq``/``cat``
  and the interpreters are never the first reader. A 1 GB transcript streams
  through ``grep -c`` in ~1 s at a few MB RSS; what froze dev1 was a whole-file
  LOAD (``python3 -`` at 2.0 GB), and an unbounded stream (``awk '{print}'``,
  ``| cat``) dumps the same bytes into the session. Stages split quote-aware:
  ``'…'``, ``"…"``, ``$'…'``, ``$(…)``, backticks, and a ``#`` comment runs to its
  newline, so an apostrophe in a comment never hides the next command.
An inline ``# airuleset:own-transcript-ok <reason>`` marker passes a deliberate
repair. A resource guard against an accident, not a boundary.
"""
import os
import re
import shlex
import sys

from gates import allow, command_of, emit_block, read_payload

ALLOWED_HEADS = frozenset({"ls", "stat", "wc", "du", "test", "[", "file",
                           "readlink", "realpath"})
BOUNDED_HEADS = frozenset({"head", "tail"})    # only with a small numeric -n/-c
# A heredoc body is data, except when one of these consumes it as a program.
INTERPRETERS = frozenset({"python", "python3", "bash", "sh", "node", "perl", "ruby"})
MAX_LINES = 50
MAX_BYTES = 65536
_WRAPPERS = frozenset({"env", "sudo", "command", "nice", "ionice", "timeout",
                       "nohup", "stdbuf", "time", "exec"})
# #1395 deny by default: heads that only NAME a path (text), never read it
TEXT_HEADS = frozenset({"echo", "printf", "gh", "git", "true", "false", ":", "cd",
                        "pushd", "popd", "export", "for", "case", "mkdir"})
_GREPS = frozenset({"grep", "egrep", "fgrep", "zgrep", "rg"})
_GREP_COUNT_SHORT = frozenset("clLq")
_GREP_COUNT_LONG = ("--count", "--files-with-matches", "--files-without-match", "--quiet",
                    "--silent")
_STREAMERS = frozenset({"awk", "gawk", "mawk", "sed", "cut", "zcat", "gzip", "gunzip",
                        "jq"}) | _GREPS
_SHELL_WORDS = frozenset({"do", "then", "else", "elif", "!", "{", "(", "if", "while", "until"})
# a command substitution body is a command of its own (never split on a bare
# paren: python -c 'open("x.jsonl")' must stay one command)
_SUBST_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_BYPASS_RE = re.compile(r"#\s*airuleset:own-transcript-ok\s+\S")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_RECURSIVE = frozenset({"-r", "-R", "--recursive", "-rn", "-rl", "-ri", "-Rn"})


def _session_id(transcript_path):
    base = os.path.basename(transcript_path or "")
    return base[:-len(".jsonl")] if base.endswith(".jsonl") else ""


def _words(segment):
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def _command(words):
    """``(head, args)`` after env assignments and wrappers with their args."""
    i = 0
    while i < len(words):
        w = words[i]
        if _ASSIGN_RE.match(w) or w in _SHELL_WORDS:
            i += 1
        elif os.path.basename(w) in _WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-")
                                      or re.fullmatch(r"[0-9.]+[smhd]?", words[i])):
                i += 1
        else:
            break
    if i >= len(words):
        return "", []
    return os.path.basename(words[i]), words[i + 1:]


def _bounded(head, args):
    """``head``/``tail`` with ONE small numeric -n/-c bound, no ``+N``/follow."""
    limit = None
    it = iter(args)
    for a in it:
        if a in ("-f", "-F", "--follow") or a.startswith("--follow"):
            return False
        m = re.fullmatch(r"-([nc])(\d*)", a)
        if m:
            val = m.group(2) or next(it, "")
            if not val.isdigit():
                return False
            cap = MAX_LINES if m.group(1) == "n" else MAX_BYTES
            limit = int(val) <= cap
        elif a.startswith("-"):
            return False
    return bool(limit)


def _stages(text):
    """``[(segment, piped)]``: split on ``; & && || | |&`` and newlines OUTSIDE
    quotes (``'…'``, ``"…"``, ``$'…'``), ``$(…)`` and backticks; a ``#`` that
    starts a word comments out the rest of its line. ``piped`` = its stdout
    feeds a ``|``."""
    out, cur, i, n = [], [], 0, len(text)
    quote, depth = "", 0

    def cut(piped):
        out.append(("".join(cur), piped))
        cur.clear()
    while i < n:
        c = text[i]
        if quote:
            cur.append(c)
            if c == "\\" and quote != "'" and i + 1 < n:
                cur.append(text[i + 1])
                i += 1
            elif c == quote[-1]:
                quote = ""
        elif c == "#" and depth == 0 and (i == 0 or text[i - 1] in " \t\n;&|("):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "$" and text.startswith("$'", i) and depth == 0:
            quote = "$'"
            cur.append("$'")
            i += 2
            continue
        elif depth:
            cur.append(c)
            if text.startswith("$(", i):
                depth += 1
                cur.append("(")
                i += 1
            elif c == ")":
                depth -= 1
            elif c in "'\"`":
                quote = c
        elif c == "\\" and i + 1 < n:
            cur.append(text[i:i + 2])
            i += 1
        elif c in "'\"`":
            quote = c
            cur.append(c)
        elif text.startswith("$(", i):
            depth = 1
            cur.append("$(")
            i += 1
        elif text.startswith("||", i) or text.startswith("&&", i):
            cut(False)
            i += 1
        elif c == "|":
            cut(True)
            if text.startswith("|&", i):
                i += 1
        elif c == "&" and ((cur and cur[-1].endswith((">", "<"))) or text.startswith("&>", i)):
            cur.append(c)                   # 2>&1, >&2, &>file: a redirection
        elif c in ";&\n":
            cut(False)
        else:
            cur.append(c)
        i += 1
    cut(False)
    return [(seg, piped) for seg, piped in out if seg.strip()]


def _grep_counts(args):
    """``grep``/``rg`` that prints counts, names or nothing (-c -l -L -q)."""
    for a in args:
        if a == "--":
            break
        if a.startswith("--"):
            if a.split("=", 1)[0] in _GREP_COUNT_LONG:
                return True
        elif a.startswith("-") and len(a) > 1 and not a[1].isdigit():
            for ch in a[1:]:
                if ch in _GREP_COUNT_SHORT:
                    return True
                if ch in "efABCdmM":        # the rest of the cluster is its value
                    break
    return False


def _runs_session_shrink(args):
    names = [os.path.basename(a) for a in args]
    return "cli_session_shrink.py" in names or (
        "airuleset.py" in names and "session-shrink" in args)


def _self_bounded(head, args):
    """A reader whose own output is a count, a name or a bounded slice."""
    if head in _GREPS:
        return _grep_counts(args)
    if head in BOUNDED_HEADS:
        return _bounded(head, args)
    if head in INTERPRETERS:
        return _runs_session_shrink(args)
    return head in ALLOWED_HEADS


def _streamer(head, args):
    """A one-pass reader whose output a LATER bounding stage can cap."""
    if head == "sed":
        return not any(a.startswith("-i") or a.startswith("--in-place") for a in args)
    if head == "jq":
        return "--stream" in args
    if head in ("gzip", "gunzip"):
        return any(a in ("-c", "-dc", "-cd", "--stdout") for a in args)
    return head in _STREAMERS


def _bounding_last(head, args):
    """The pipeline's LAST stage caps what reaches the session."""
    if head in BOUNDED_HEADS:
        return _bounded(head, args)
    if head in _GREPS:
        return _grep_counts(args)
    return head == "wc"


def _pipelines(stages):
    """Group ``_stages`` output into pipelines (lists of segments)."""
    out, cur = [], []
    for seg, piped in stages:
        cur.append(seg)
        if not piped:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _pipeline_ok(segments, sid, projects):
    cmds = [_command(_words(seg)) for seg in segments]
    last = cmds[-1]
    for k, (head, args) in enumerate(cmds):
        if not head or head in TEXT_HEADS:
            continue
        recursive = projects and (any(a in _RECURSIVE for a in args) or (
            head == "rg" and not any(a.endswith(".jsonl") for a in args)))
        if not (recursive or _touches(args, sid, projects)):
            continue
        if recursive:
            return False
        if _self_bounded(head, args):
            continue
        if k < len(cmds) - 1 and _streamer(head, args) and _bounding_last(*last):
            continue
        return False
    return True


def _touches(words, sid, under_projects):
    """Does this command's argument list name a transcript?"""
    for w in words:
        if sid and sid + ".jsonl" in w:
            return True
        if under_projects and (w.endswith(".jsonl") or ".jsonl." in w):
            return True
    return False


def _names_transcript(text, sid):
    return bool((sid and sid + ".jsonl" in text)
                or (".claude/projects" in text and ".jsonl" in text))


def _interpreter_heredoc(cmd, sid):
    """A heredoc an interpreter runs (``python3 - <<EOF``) whose program names
    a transcript: the songplayer shape."""
    from gates.selfservice import _HEREDOC_RE, _capture_heredocs
    _, direct, _ = _capture_heredocs(cmd)
    for line in cmd.split("\n"):
        mm = _HEREDOC_RE.search(line.rstrip())
        if not mm:
            continue
        head, _ = _command(_words(line[:mm.start()]))
        if head in INTERPRETERS and _names_transcript(direct.get(mm.group(2), ""), sid):
            return True
    return False


def violation(cmd, transcript_path):
    """The block message for ``cmd`` (``""`` when it may run)."""
    sid = _session_id(transcript_path)
    if not cmd or _BYPASS_RE.search(cmd):
        return ""
    if not ((sid and sid + ".jsonl" in cmd) or ".claude/projects" in cmd):
        return ""
    if _interpreter_heredoc(cmd, sid):
        return _message(transcript_path)
    from gates.selfservice import _capture_heredocs
    skeleton = _capture_heredocs(cmd)[2]          # heredoc bodies are data
    projects = ".claude/projects" in skeleton
    bodies = [a or b for a, b in _SUBST_RE.findall(skeleton)]
    for text in [skeleton] + bodies:
        for pipeline in _pipelines(_stages(text)):
            if not _pipeline_ok(pipeline, sid, projects):
                return _message(transcript_path)
    return ""


def _message(transcript_path):
    return ("BLOCKED (#1224): this reads a Claude session TRANSCRIPT (%s), a "
                "single-line-per-record jsonl that grows to hundreds of MB. Loading "
                "one froze dev1 for 19 min. Your last ❓ block is printed by "
                "`python3 ~/devel/airuleset/airuleset.py question-block`; decisions "
                "live on the ticket. ls/stat/wc/du and head/tail with -n <= %d or "
                "-c <= %d stay allowed, and a bounded STREAM too (#1395): a count or "
                "locate (grep -c / -l / -q, wc), or a grep/awk/sed/cut/zcat pipe that ENDS "
                "in a bound (grep x f | head -n 5, ... | sort | uniq -c | head -n 50). "
                "`airuleset.py session-shrink` shrinks a heavy session. A deliberate repair: append "
                "`# airuleset:own-transcript-ok <reason>`."
                % (os.path.basename(transcript_path) or "*.jsonl", MAX_LINES, MAX_BYTES))


def main():
    import json
    raw = read_payload()
    try:
        tp = (json.loads(raw) or {}).get("transcript_path") or ""
    except ValueError:
        tp = ""
    msg = violation(command_of(raw), tp)
    if msg:
        emit_block(msg)
    allow()


if __name__ == "__main__":
    sys.exit(main())
