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
* a content READER (``READERS``) that names the transcript, a ``*.jsonl``
  under ``.claude/projects``, or reads ``.claude/projects`` recursively
  (``-r``, ``rg``) is refused; a command that only NAMES such a path
  (``gh --body``, ``git commit -m``, ``echo``) is text;
* a heredoc body is data, unless an interpreter consumes it
  (``python3 - <<EOF``) and its program names a transcript;
* metadata (``ALLOWED_HEADS``) passes, and ``head``/``tail`` pass with ONE
  numeric ``-n`` ≤ ``MAX_LINES`` or ``-c`` ≤ ``MAX_BYTES`` (never ``+N``/follow).
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
# Only these READ a file's content. Anything else that merely NAMES a path
# (gh --body, git commit -m, echo, printf) is text, never a read (live false
# positive on this ticket's own evidence comment, 2026-10-13).
READERS = frozenset({"cat", "tac", "nl", "grep", "egrep", "fgrep", "zgrep", "rg",
                     "ag", "ack", "jq", "sed", "awk", "gawk", "mawk", "perl",
                     "python", "python3", "node", "ruby", "less", "more", "cut",
                     "sort", "uniq", "strings", "xxd", "od", "base64", "zcat",
                     "gzip", "gunzip", "cp", "dd"}) | BOUNDED_HEADS
# A heredoc body is data, except when one of these consumes it as a program.
INTERPRETERS = frozenset({"python", "python3", "bash", "sh", "node", "perl", "ruby"})
MAX_LINES = 50
MAX_BYTES = 65536
_WRAPPERS = frozenset({"env", "sudo", "command", "nice", "ionice", "timeout",
                       "nohup", "stdbuf", "time", "exec"})
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;\n|&]")
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
        if _ASSIGN_RE.match(w):
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


def _touches(words, sid, under_projects):
    """Does this command's argument list name a transcript?"""
    for w in words:
        if sid and sid + ".jsonl" in w:
            return True
        if under_projects and (w.endswith(".jsonl") or w.endswith("*.jsonl")):
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
    for segment in _SEGMENT_SPLIT.split(skeleton) + [x for b in bodies
                                                      for x in _SEGMENT_SPLIT.split(b)]:
        head, args = _command(_words(segment))
        if head not in READERS:
            continue
        recursive = projects and (head == "rg" or any(a in _RECURSIVE for a in args))
        if not (recursive or _touches(args, sid, projects)):
            continue
        if head in BOUNDED_HEADS and not recursive and _bounded(head, args):
            continue
        return _message(transcript_path)
    return ""


def _message(transcript_path):
    return ("BLOCKED (#1224): this reads a Claude session TRANSCRIPT (%s), a "
                "single-line-per-record jsonl that grows to hundreds of MB. Loading "
                "one froze dev1 for 19 min. Your last ❓ block is printed by "
                "`python3 ~/devel/airuleset/airuleset.py question-block`; decisions "
                "live on the ticket. ls/stat/wc/du and head/tail with -n <= %d or "
                "-c <= %d stay allowed. A deliberate repair: append "
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
