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

#1395 (owner 23.11.2026: inspecting a big transcript must stay possible), a
SMALL allow-list on top (supervisor ROZHODNUTÉ round 3):
* COUNT/LOCATE readers pass: ``grep``/``rg`` with ``-c -l -L -q``, ``wc``,
  ``ls``/``stat``/``du``, ``find`` without ``-exec``/``-ok``/``-delete``/
  ``-fprint``, and ``python3 … airuleset.py session-shrink``;
* any other reader passes only when its pipeline ENDS in a BYTE bound
  (``head -c``/``tail -c`` ≤ ``MAX_BYTES``, ``wc``, ``grep -c/-q/-l``) and no
  stage of it slurps (an interpreter, ``tac``, ``jq -s``); ``sort`` or
  ``head``/``tail`` is never the first reader (``sort``/``uniq``/``cut``/``grep``
  downstream are fine);
* ``session-shrink`` passes only as the interpreter's SCRIPT (first non-option
  argument; ``-c``/``-m`` never);
* a ``grep``/``rg`` reads only its PATH operands and ``-f``/``--file`` pattern
  files: a code search for the string or the session id
  (``grep -rn '.claude/projects' gates/``) is not a read, a value option
  (``-g -t -T -j --include --exclude --type …``) never shifts the pattern, a
  pattern FILE that is a transcript is always refused, and a recursive search
  with no or a relative operand after ``cd`` into ``.claude/projects`` reads
  the tree.
Segments split quote-aware (``'…'``, ``"…"``, ``$'…'``, ``$(…)``, backticks; a
word-initial ``#`` comments to its newline; a ``(…)`` subshell or ``<(…)`` is
its own command), so a ``;`` inside ``python3 -c '…'`` stays one command.

Accepted residuals (a guardrail against an accident, not a security boundary:
three review rounds showed a command-text parser cannot be complete, and
``session-shrink`` removes the root risk, the 1 GB file itself):
* a read with no command head: ``x=$(<F)``, ``while read l; do …; done < F``;
* a path the text never names: ``F=…; cat "$F"``, names fed through ``xargs``,
  a copy read later (``cp F /tmp/x; cat /tmp/x`` reads the copy);
* a program in a string: ``bash -c "cat F"``, ``eval``, an alias or function,
  an obfuscated head (``$'\\x63at' F``), a script file that opens F;
* the Read tool (this gate sees Bash only).
An inline ``# airuleset:own-transcript-ok <reason>`` marker passes a deliberate
repair.
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
                     "gzip", "gunzip", "cp", "dd", "find"}) | BOUNDED_HEADS
# A heredoc body is data, except when one of these consumes it as a program.
INTERPRETERS = frozenset({"python", "python3", "bash", "sh", "node", "perl", "ruby"})
MAX_LINES = 50
MAX_BYTES = 65536
_WRAPPERS = frozenset({"env", "sudo", "command", "nice", "ionice", "timeout",
                       "nohup", "stdbuf", "time", "exec"})
# #1395 count/locate readers and byte bounds
_GREPS = frozenset({"grep", "egrep", "fgrep", "zgrep", "rg"})
_GREP_COUNT_SHORT = frozenset("clLq")
_GREP_COUNT_LONG = ("--count", "--files-with-matches", "--files-without-match", "--quiet",
                    "--silent")
# short options whose value is the rest of the cluster (or the next word)
_VALUE_SHORT = {"grep": frozenset("efABCdDm"), "rg": frozenset("efABCmMgtTjErd")}
_VALUE_LONG = frozenset({"--regexp", "--file", "--after-context", "--before-context",
                         "--context", "--max-count", "--include", "--exclude",
                         "--exclude-dir", "--exclude-from", "--type", "--type-not",
                         "--type-add", "--type-clear", "--glob", "--iglob", "--max-depth",
                         "--threads", "--encoding", "--replace", "--max-columns",
                         "--max-filesize", "--devices", "--directories", "--binary-files",
                         "--label", "--sort", "--sortr", "--pre", "--pre-glob",
                         "--ignore-file", "--path-separator", "--context-separator",
                         "--field-match-separator", "--field-context-separator",
                         "--engine", "--colors", "--color", "--colour", "--dfa-size-limit",
                         "--regex-size-limit"})
_FIND_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint",
                           "-fprint0", "-fprintf", "-fls"})
_NEVER_BOUNDED = INTERPRETERS | BOUNDED_HEADS | {"sort", "tac"}   # never the first reader
_SHELL_WORDS = frozenset({"do", "then", "else", "elif", "!", "{", "}"})
# a command substitution body is a command of its own (never split on a bare
# paren: python -c 'open("x.jsonl")' must stay one command)
_SUBST_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_BYPASS_RE = re.compile(r"#\s*airuleset:own-transcript-ok\s+\S")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


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


def _bounded(head, args, bytes_only=False):
    """``head``/``tail`` with ONE small numeric -n/-c bound, no ``+N``/follow
    (``bytes_only``: a ``-c`` bound only)."""
    limit = None
    it = iter(args)
    for a in it:
        if a in ("-f", "-F", "--follow") or a.startswith("--follow"):
            return False
        m = re.fullmatch(r"-([nc])(\d*)", a)
        if m:
            val = m.group(2) or next(it, "")
            if not val.isdigit() or (bytes_only and m.group(1) == "n"):
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
        elif c in ";&\n()":
            cut(False)                      # a (…) subshell / <(…) is its own command
        else:
            cur.append(c)
        i += 1
    cut(False)
    return [(seg, piped) for seg, piped in out if seg.strip()]


def _pipelines(stages):
    out, cur = [], []
    for seg, piped in stages:
        cur.append(seg)
        if not piped:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _grep_split(head, args):
    """``(counts, recursive, operands, pattern_files)`` of a grep/rg argument
    list. ``operands`` are the PATHS (the non-option words after the pattern);
    a value option's value is never taken for the pattern."""
    kind = "rg" if head == "rg" else "grep"
    values = _VALUE_SHORT[kind]
    counts = recursive = False
    words, files, pattern_given, it = [], [], False, iter(args)
    for a in it:
        if a == "--":
            words.extend(it)
            break
        if a.startswith("--"):
            key, eq, val = a.partition("=")
            counts |= key in _GREP_COUNT_LONG
            recursive |= key in ("--recursive", "--dereference-recursive")
            if key in _VALUE_LONG:
                if not eq:
                    val = next(it, "")
                if key in ("--regexp", "--file"):
                    pattern_given = True
                if key == "--file":
                    files.append(val)
        elif a.startswith("-") and len(a) > 1 and not a[1].isdigit():
            for k, ch in enumerate(a[1:]):
                counts |= ch in _GREP_COUNT_SHORT
                recursive |= kind == "grep" and ch in "rR"
                if ch in values:
                    val = a[k + 2:] or next(it, "")
                    if ch in "ef":
                        pattern_given = True
                    if ch == "f":
                        files.append(val)
                    break
        else:
            words.append(a)
    return counts, recursive, (words if pattern_given else words[1:]), files


def _runs_session_shrink(args):
    """The interpreter's SCRIPT is session-shrink (never ``-c``/``-m`` code)."""
    it = iter(args)
    for a in it:
        if a.startswith("-") and a != "-":
            if not a.startswith("--") and ("c" in a[1:] or "m" in a[1:]):
                return False
            if a in ("-X", "-W"):
                next(it, None)
            continue
        base = os.path.basename(a)
        if base == "cli_session_shrink.py":
            return True
        return base == "airuleset.py" and next(it, None) == "session-shrink"
    return False


def _touches(words, sid, under_projects):
    """Does this command's argument list name a transcript?"""
    for w in words:
        if sid and sid + ".jsonl" in w:
            return True
        if under_projects and (w.endswith(".jsonl") or w.endswith("*.jsonl")):
            return True
    return False


def _relative(path):
    return not path.startswith(("/", "~", "$"))


def _reads(head, args, sid, projects, in_projects):
    """``""`` (no transcript read), ``"read"`` or ``"hard"`` (refused whatever
    else holds: a transcript as a grep PATTERN file)."""
    if head in _GREPS:
        _, recursive, operands, files = _grep_split(head, args)
        if any(_touches([f], sid, True) or ".claude/projects" in f for f in files):
            return "hard"
        recursive = recursive or head == "rg"
        if recursive and any(".claude/projects" in o for o in operands):
            return "read"
        if recursive and in_projects and (not operands or any(map(_relative, operands))):
            return "read"
        return "read" if _touches(operands, sid, projects) else ""
    if head == "find":
        hit = (any(".claude/projects" in a for a in args) or _touches(args, sid, projects)
               or in_projects)
        return "read" if hit else ""
    return "read" if _touches(args, sid, projects) else ""


def _stage_ok(head, args, bounded_pipe):
    if head in _GREPS and _grep_split(head, args)[0]:
        return True                                # counts / names / quiet
    if head == "find":
        return not any(a in _FIND_ACTIONS for a in args)
    if head in INTERPRETERS and _runs_session_shrink(args):
        return True
    if head in BOUNDED_HEADS and _bounded(head, args):
        return True
    return bounded_pipe and head not in _NEVER_BOUNDED


def _byte_bounded_last(head, args):
    if head in BOUNDED_HEADS:
        return _bounded(head, args, bytes_only=True)
    if head in _GREPS:
        return _grep_split(head, args)[0]
    return head == "wc"


def _slurps(head, args):
    """A stage that holds its whole input in memory whatever the output bound."""
    if head in INTERPRETERS or head == "tac":
        return True
    return head == "jq" and any(a == "--slurp" or (a.startswith("-") and not a.startswith("--")
                                                   and "s" in a[1:]) for a in args)


def _cd_into_projects(text):
    for seg, _ in _stages(text):
        head, args = _command(_words(seg))
        if head in ("cd", "pushd") and any(".claude/projects" in a for a in args):
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
    in_projects = _cd_into_projects(skeleton)
    for text in [skeleton] + bodies:
        for pipeline in _pipelines(_stages(text)):
            cmds = [_command(_words(seg)) for seg in pipeline]
            bounded_pipe = (len(cmds) > 1 and _byte_bounded_last(*cmds[-1])
                            and not any(_slurps(h, a) for h, a in cmds))
            for k, (head, args) in enumerate(cmds):
                if head not in READERS:
                    continue
                reads = _reads(head, args, sid, projects, in_projects)
                if not reads:
                    continue
                if reads != "hard" and _stage_ok(head, args,
                                                 bounded_pipe and k < len(cmds) - 1):
                    continue
                return _message(transcript_path)
    return ""


def _message(transcript_path):
    return ("BLOCKED (#1224): this reads a Claude session TRANSCRIPT (%s), a "
            "single-line-per-record jsonl that grows to hundreds of MB. Loading "
            "one froze dev1 for 19 min. Your last ❓ block is printed by "
            "`python3 ~/devel/airuleset/airuleset.py question-block`; decisions "
            "live on the ticket. Allowed: ls/stat/wc/du, find without -exec, "
            "head/tail with -n <= %d or -c <= %d, a count or locate (grep -c / -l / -q), "
            "or a pipe that ENDS in a byte bound (... | sort | uniq -c | head -c 4000). "
            "`airuleset.py session-shrink` shrinks a heavy session (#1395). A deliberate "
            "repair: append `# airuleset:own-transcript-ok <reason>`."
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
