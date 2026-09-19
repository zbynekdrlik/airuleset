"""gates.meetinganalysis_stop -- the meeting-analysis AUTHORSHIP Stop check
(#1076, owner directive 2026-09-18). Entry for
hooks/stop-check-meeting-analysis.sh.

After a meeting analysis, its deliverables (`screen_inventory.md`, `NOTES.md`,
`MAPPING.md`) each carry a first-line `Analysed-by: main <model>` stamp written
from `cli_authorship.stamp_line("Analysed", cwd)` (the #1061 mechanism, role
`main` + the live session model). This Stop gate BLOCKS a meeting-analysis
completion report unless every NAMED, on-disk deliverable carries `Analysed-by:
main <Fable model>` (an audit marker attesting the Fable main authored the
interpretation, not a weaker subagent -- NOT cryptographic proof: a first line
can be hand-typed, the delegation PreToolUse gate is the real control) AND the
report itself carries the `Analysed-by: main <model>` line.

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
# common filenames elsewhere). Scope ONLY on tokens EXCLUSIVE to this skill --
# NOT the generic `transcript.txt` / `frames_kept` / `speaker_turns.json` /
# `video-notes` (a transcription/podcast/voiceagent project legitimately emits
# those alongside its own NOTES.md, and keying on them wedged that unrelated
# report -- #1076 review, reproduced fleet-wide FP). A genuine meeting report
# always names `screen_inventory.md` (a core deliverable) OR carries the
# mandated `Analysed-by:` line OR says "meeting analysis" / "analýza meetingu".
_MEETING_REPORT_RE = re.compile(
    r"screen_inventory\.md"
    r"|meeting analysis"
    r"|analýza meetingu|analyza meetingu"
    r"|Analy[sz]ed-?by",
    re.IGNORECASE)

# Delimiters that bound a path token in the report (whitespace / quotes / parens
# / a markdown code-span backtick). Used to SPLIT the report into tokens -- a
# linear scan, NOT a greedy `[^delim]*<basename>` regex, which backtracks
# polynomially on a long non-delimiter run (`/aaaa….md`) and the Stop gate runs
# on the whole completion report (#1076 review, ReDoS discipline #577/#1010).
_TOKEN_DELIM_RE = re.compile(r"[\s'\"()`]+")
_DELIVERABLE_SUFFIXES = ("screen_inventory.md", "notes.md", "mapping.md")


def _deliverable_tokens(msg):
    """Every whitespace/quote/backtick-delimited token in `msg` that ENDS in a
    deliverable basename. Linear (no backtracking)."""
    out = []
    for tok in _TOKEN_DELIM_RE.split(msg):
        if tok and tok.lower().endswith(_DELIVERABLE_SUFFIXES):
            out.append(tok)
    return out

# A deliverable's FIRST line as a stamp: capture role + model. Bullet/bold/emoji
# tolerant; the leading char class excludes `*` (the trailing `\**` owns bold) so
# there is no ambiguous `*`-overlap -> no polynomial backtracking on a run of `*`
# (#577/#1010 ReDoS discipline).
_STAMP_RE = re.compile(
    r"^[ \t>#\-🧠]*\**[ \t]*Analy[sz]ed-?by\**[ \t]*:[ \t]*\**[ \t]*"
    r"(?P<role>main|worker|implementer)\b[ \t]*(?P<model>\S+)?", re.IGNORECASE)

# The report line: `Analysed-by: main <model>` (role main; model any).
_REPORT_LINE_RE = re.compile(
    r"(?im)^[ \t>#\-🧠]*\**[ \t]*Analy[sz]ed-?by\**[ \t]*:[ \t]*\**[ \t]*main\b")


def _expected_family():
    """The Fable model-family prefix the deliverable stamp must carry, derived
    from `airuleset.MANAGED_MODEL` (the live MAIN model) so a future family
    rename adapts without an edit here. Falls back to `claude-fable-`."""
    try:
        import airuleset
        mm = re.sub(r"\[[^\]]*\]$", "", (airuleset.MANAGED_MODEL or "").strip().lower())
        parts = mm.split("-")
        if len(parts) >= 2:
            return "-".join(parts[:2]) + "-"          # claude-fable-5-1 -> claude-fable-
        return "claude-fable-"
    except Exception:
        return "claude-fable-"                          # honest default on any error


def _stamp_verdict(first_line):
    """('ok'|'unknown'|'bad'|'missing') for a deliverable's first line.
    ok      -> `main <fable-family model>` (the Fable main authored it);
    unknown -> `main unknown` (a main session whose model was unreadable -- the
               role is main, not a worker, so fail-OPEN rather than block, #1076
               review A/F5);
    bad     -> a stamp with role worker/implementer or a known non-fable model
               (a subagent authored it) -> block;
    missing -> the first line is not an Analysed-by stamp at all -> block."""
    m = _STAMP_RE.match(first_line or "")
    if not m:
        return "missing"
    role = (m.group("role") or "").lower()
    model = re.sub(r"\[[^\]]*\]$", "", (m.group("model") or "").strip().lower())
    if role != "main":
        return "bad"
    if model in ("unknown", ""):
        return "unknown"
    return "ok" if model.startswith(_expected_family()) else "bad"


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

    tokens = _deliverable_tokens(msg)
    if not tokens:
        return "allow", "no deliverable named"

    verdicts = []       # one _stamp_verdict per readable on-disk deliverable
    for tok in tokens:
        rp = _resolve(tok, cwd)
        fl = read_first_line(rp) if rp else None
        if fl is not None:
            verdicts.append(_stamp_verdict(fl))

    if not verdicts:
        # a prose mention / unexpanded $WORK var / unreadable path -> fail open
        # (never wedge an unrelated report).
        return "journal", "meeting report names a deliverable but none is "\
                          "readable on disk"

    # A real deliverable is on disk -> enforce the stamp.
    if any(v in ("bad", "missing") for v in verdicts):
        return "block", ("a meeting-analysis deliverable on disk does not carry "
                         "a first-line `Analysed-by: main <Fable model>` stamp "
                         "(or is stamped by a subagent) -- the interpretation "
                         "must be authored by the Fable main, not a subagent")
    if not _REPORT_LINE_RE.search(msg):
        return "block", ("the meeting-analysis completion report is missing the "
                         "`Analysed-by: main <model>` line")
    if any(v == "unknown" for v in verdicts):
        # a `main unknown` stamp -- the role is main (not a worker), the model
        # was just unreadable -> fail-OPEN with a journal line (#1076 review).
        return "journal", "meeting deliverable stamped `main` but the model was "\
                          "unreadable -- allowing (fail-open)"
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
