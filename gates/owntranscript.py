"""#1224 ask 2: a session never reads its OWN transcript with a content reader.

The songplayer session froze dev1 for 19 minutes by loading its own 598 MB
transcript (``python3 -`` at 2.0 GB RSS plus ``grep -o 'Otázka — projekt …'`` at
622 MB) to find the block of a question it had asked before. That block already
lives in ``~/.claude/discord-questions.json``, and ``airuleset.py
question-block`` prints it, so a session never needs the transcript for it.

The adapter ``hooks/block-own-transcript-read.sh`` runs this module only when
the command names the session id at all (a bash substring pre-filter), so the
other Bash calls pay no python start. A segment that names the session's
transcript must start with a metadata or bounded reader (``ALLOWED_HEADS``);
anything else (grep, cat, python, jq, …) is refused. An inline
``# airuleset:own-transcript-ok <reason>`` marker passes a deliberate repair.
"""
import os
import re
import shlex
import sys

from gates import allow, command_of, emit_block, read_payload

ALLOWED_HEADS = frozenset({"ls", "stat", "wc", "du", "test", "[", "file", "head",
                           "tail", "readlink", "realpath"})
_WRAPPERS = frozenset({"env", "sudo", "command", "nice", "ionice", "timeout",
                       "nohup", "stdbuf", "time"})
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;\n|&]")
_BYPASS_RE = re.compile(r"#\s*airuleset:own-transcript-ok\s+\S")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _session_id(transcript_path):
    base = os.path.basename(transcript_path or "")
    return base[:-len(".jsonl")] if base.endswith(".jsonl") else ""


def _head(segment):
    try:
        words = shlex.split(segment, comments=True)
    except ValueError:
        words = segment.split()
    i = 0
    while i < len(words) and (_ASSIGN_RE.match(words[i])
                              or os.path.basename(words[i]) in _WRAPPERS
                              or (i and words[i].startswith("-"))):
        i += 1
    return os.path.basename(words[i]) if i < len(words) else ""


def violation(cmd, transcript_path):
    """The block message for ``cmd`` (``""`` when it may run)."""
    sid = _session_id(transcript_path)
    if not sid or sid not in (cmd or "") or _BYPASS_RE.search(cmd):
        return ""
    for segment in _SEGMENT_SPLIT.split(cmd):
        if sid in segment and _head(segment) not in ALLOWED_HEADS:
            return ("BLOCKED (#1224): this reads your OWN session transcript (%s), "
                    "a single-line jsonl that grows to hundreds of MB. Loading it "
                    "froze dev1 for 19 min. The last ❓ block you asked is in "
                    "`python3 ~/devel/airuleset/airuleset.py question-block`; "
                    "decisions live on the ticket. ls/stat/wc/du and bounded "
                    "head/tail stay allowed. A deliberate repair: append "
                    "`# airuleset:own-transcript-ok <reason>`."
                    % os.path.basename(transcript_path))
    return ""


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
