"""gates.meetinganalysis_stop -- the meeting-analysis AUTHORSHIP Stop check
(#1076, owner directive 2026-09-18). Entry for
hooks/stop-check-meeting-analysis.sh.

After a meeting analysis, its deliverables (`screen_inventory.md`, `NOTES.md`,
`MAPPING.md`) each carry a first-line `Analysed-by: main <model>` stamp written
from `cli_authorship.authorship_value(cwd)` (the #1061 mechanism, role `main` +
the live session model). This Stop gate BLOCKS a meeting-analysis completion
report unless every NAMED, on-disk deliverable carries `Analysed-by: main
claude-fable-*` (proving the Fable main authored the interpretation, not a
weaker subagent) AND the report itself carries the `Analysed-by: main <model>`
line.

FAIL-OPEN by construction (a Stop hook must never wedge an UNRELATED report):
- a report that is not a meeting-analysis report -> allow;
- a meeting report naming NO on-disk-readable deliverable (a prose mention, an
  unexpanded `$WORK/...` path, an unreadable path) -> journal + allow.
The block fires ONLY when a real deliverable file is found on disk under a path
the report names -- then the stamp is enforced.

STDLIB ONLY.
"""
import os
import re
import sys
import time

from gates import read_payload, field_of, emit_block_stderr, allow

STOP_LOG = "meeting-analysis-gate.log"

# The three deliverable basenames the design stamps + this gate enforces.
_BASENAMES = ("screen_inventory.md", "NOTES.md", "MAPPING.md")

# Is the final message a MEETING-ANALYSIS report at all? Only then do the
# generic `NOTES.md` / `MAPPING.md` basenames mean a deliverable (they are
# common filenames elsewhere -- scoping here is what stops a fleet-wide false
# block on any project's own NOTES.md). A meeting report carries at least one
# unambiguous meeting signal.
_MEETING_REPORT_RE = re.compile(
    r"screen_inventory\.md"
    r"|transcript\.txt"
    r"|speaker_turns\.json"
    r"|frames_kept"
    r"|_verbatim\.md"
    r"|video-notes"
    r"|meeting analysis"
    r"|analýza meetingu|analyza meetingu"
    r"|Analy[sz]ed-?by",
    re.IGNORECASE)

# A path token ending in one of the deliverable basenames. `[^\s'"()`]*` eats the
# leading directory back to the last whitespace/quote/backtick (paths are
# whitespace-delimited; a markdown code-span's backticks bound it).
_PATH_RE = re.compile(
    r"[^\s'\"()`]*(?:screen_inventory|NOTES|MAPPING)\.md", re.IGNORECASE)

# The deliverable file's FIRST line: `Analysed-by: main claude-fable-*` (role
# main + the Fable model family). Bullet/bold/emoji tolerant.
_FILE_STAMP_RE = re.compile(
    r"^[ \t>*#\-🧠]*\**[ \t]*Analy[sz]ed-?by\**[ \t]*:[ \t]*\**[ \t]*"
    r"main[ \t]+claude-fable-", re.IGNORECASE)

# The report line: `Analysed-by: main <model>` (role main; model any).
_REPORT_LINE_RE = re.compile(
    r"(?im)^[ \t>*#\-🧠]*\**[ \t]*Analy[sz]ed-?by\**[ \t]*:[ \t]*\**[ \t]*main\b")


def _journal(why):
    """The fail-open journal line (stderr + the log file), like questionscope's
    fail-open writes. Never embeds a resolved filesystem path (leak class)."""
    sys.stderr.write("meeting-analysis: %s -- not enforced\n" % why)
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", STOP_LOG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), why))
    except OSError:
        return


def _resolve(token, cwd):
    """Resolve a report path token to an absolute path, or None when it cannot be
    resolved (an unexpanded shell variable). Relative tokens resolve against the
    turn's cwd."""
    if not token or "$" in token:
        return None                       # unexpanded var -> unresolvable
    p = os.path.expanduser(token)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return p


def _first_line(path):
    """The file's first line (newline stripped), or None on any read error."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readline().rstrip("\n")
    except OSError:
        return None


def evaluate(msg, cwd, read_first_line=None):
    """('allow', reason) or ('block', reason). `read_first_line(path)->str|None`
    is injected in tests; production reads the real file's first line."""
    read_first_line = read_first_line or _first_line
    if not isinstance(msg, str) or not msg:
        return "allow", "no final message"
    if not _MEETING_REPORT_RE.search(msg):
        return "allow", "not a meeting-analysis report"

    tokens = _PATH_RE.findall(msg)
    if not tokens:
        return "allow", "no deliverable named"

    readable = []       # (token, first_line)
    saw_named = False
    for tok in tokens:
        saw_named = True
        rp = _resolve(tok, cwd)
        fl = read_first_line(rp) if rp else None
        if fl is not None:
            readable.append((tok, fl))

    if not readable:
        # a prose mention / unexpanded / unreadable path -> fail open (never
        # wedge an unrelated report).
        return "journal", "meeting report names a deliverable but none is "\
                          "readable on disk"

    # A real deliverable is on disk -> enforce the stamp.
    for _tok, fl in readable:
        if not _FILE_STAMP_RE.match(fl or ""):
            return "block", ("a meeting-analysis deliverable on disk does not "
                             "carry a first-line `Analysed-by: main "
                             "claude-fable-*` stamp -- the interpretation must "
                             "be authored by the Fable main, not a subagent")
    if not _REPORT_LINE_RE.search(msg):
        return "block", ("the meeting-analysis completion report is missing the "
                         "`Analysed-by: main <model>` line")
    _ = saw_named
    return "allow", "every named deliverable is stamped + the report carries "\
                    "the Analysed-by line"


def _block_message(reason):
    return (
        "BLOCKED: meeting-analysis authorship stamp missing (#1076, owner "
        "2026-09-18) -- %s\n"
        "\n"
        "  A meeting/call recording is INTERPRETED by the Fable main, never a "
        "subagent.\n"
        "  Each deliverable (`screen_inventory.md` / `NOTES.md` / `MAPPING.md`) "
        "must carry\n"
        "  a FIRST line `Analysed-by: main <model>` written from\n"
        "  `cli_authorship.authorship_value(cwd)`, and the completion report "
        "must carry\n"
        "  the line `Analysed-by: main <model>` (see Hard Rule 0 + the phase-4/5 "
        "write\n"
        "  steps in skills/meeting-analysis/SKILL.md)." % reason)


def run(payload, *, read_first_line=None):
    """The I/O shell: resolve inputs, decide, emit. Any unexpected error
    journals + allows (fail-open); never raises."""
    msg = field_of(payload, "last_assistant_message", "")
    cwd = field_of(payload, "cwd", "") or os.getcwd()
    try:
        verdict, reason = evaluate(msg, cwd, read_first_line=read_first_line)
    except Exception as e:  # noqa: BLE001 -- a Stop gate never wedges
        _journal("unreadable (%s)" % type(e).__name__)
        return
    if verdict == "journal":
        _journal(reason)
        return
    if verdict == "block":
        emit_block_stderr(_block_message(reason))


def main():
    run(read_payload())
    allow()


if __name__ == "__main__":
    main()
