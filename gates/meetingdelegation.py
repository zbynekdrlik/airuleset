"""gates.meetingdelegation -- the meeting-analysis INTERPRETATION-stays-in-main
PreToolUse gate (#1076, owner directive 2026-09-18). Entry for
hooks/block-meeting-analysis-delegation.sh.

The owner's standing rule: a meeting/call recording is INTERPRETED by the Fable
MAIN session, never a subagent -- "vidim opus 4.8, ze spracoval meeting a velmi
slabo vyhodnotil obsah". Only the MECHANICAL phases (ffmpeg extraction, the ASR
API call, frame dedup -- phases 1-3) may be dispatched to a worker; the reading
of every screen, correlation, synthesis, per-task supplements, and the questions
(phases 4-6) run in main, whole. See Hard Rule 0 in
skills/meeting-analysis/SKILL.md.

This gate REFUSES an Agent/Workflow dispatch whose prompt/script carries a
meeting-analysis SIGNAL unless it is explicitly marked `MECHANICAL-ONLY:
extract|asr|dedup` on its FIRST line AND (for a marked prompt) does not also ask
for interpretation (a "read the screen" / "summarise" / "requirements" /
"precitaj" / "zhrn" / "co klient" verb). A signal-bearing dispatch is blocked by
default -- the fail-CLOSED direction (a partially-malformed / unmarked meeting
delegation never slips through). A dispatch with NO detectable meeting signal
passes untouched.

Bypass: `airuleset:meeting-delegation-ok <reason>` in the prompt/script --
allowed and logged to ~/.claude/meeting-delegation-gate.log.

STDLIB ONLY.
"""
import json
import os
import re
import time

from gates import read_payload, field_of, emit_block_stderr, allow

DELEGATION_LOG = "meeting-delegation-gate.log"

# Meeting-analysis material signals -- the artifacts + phrases a meeting-analysis
# dispatch carries. Substring match, case-insensitive. These are the exact
# signals the #1076 design enumerates.
_SIGNALS = (
    "transcript.txt",
    "speaker_turns.json",
    "frames_kept",
    "screen_inventory",
    "_verbatim.md",
    "video-notes",
    "analyza meetingu",       # Slovak, diacritic-stripped form
    "analýza meetingu",  # Slovak with diacritics (analýza)
    "meeting analysis",
    "doplnok z meetingu",
)

# The FIRST line must be `MECHANICAL-ONLY:` naming at least one real mechanical
# phase (extract / asr / dedup). A first line marking a fake phase (or no phase)
# is treated as UNMARKED -> blocked (fail-closed).
_MARKER_RE = re.compile(r"^\s*MECHANICAL-ONLY:\s*.*\b(extract|asr|dedup)\b",
                        re.IGNORECASE)

# Interpretation intent -- reading/synthesis/mapping that MUST run in main, not a
# subagent. A dispatch that merely NAMES a meeting artifact WITHOUT any of these
# is a mechanical / code / review / search dispatch and is ALLOWED (the #1076
# review's over-block fix: the old "signal alone blocks" wedged airuleset's own
# dev on this skill, code reviews, and greps naming the artifacts). ReDoS-safe:
# fixed tokens with `\s+` gaps and a BOUNDED `[\w ]{0,20}` before a fixed suffix
# (no `\w*` directly before an unbounded proximity gap -- the repo's #577 class).
# Slovak variants carry diacritic-stripped alternates.
_INTERP_RE = re.compile(
    r"read\s+the\s+screen"
    r"|reads?\s+(?:the\s+|every\s+|each\s+)?(?:screen|frame|jpg|obrazovk)"
    r"|readers?\s+over"                 # "parallel readers over transcript segments"
    r"|parallel\s+readers?"
    r"|summari[sz]e"
    r"|analy[sz](?:e|ing)|interpret"
    r"|inspects?\s+(?:the\s+|each\s+|its\s+)?(?:screen|frame)"
    r"|what\s+did\s+the\s+client"
    r"|(?:list|extract|identify|gather|capture)\w*\s+(?:the\s+)?requirement"
    r"|(?:write|writes|writing|record|records|recording)\s+[\w ]{0,20}"
    r"(?:notes|mapping|screen_inventory|inventory|deliverable|tickets?|summary)"
    r"|map\s+(?:it\s+|them\s+)?to\s+(?:tickets?|requirement)"
    r"|prečítaj|precitaj|zhrň|zhrn|vyhodno"       # prečítaj/zhrň/vyhodnoť
    r"|napíš\s+(?:poznámky|zhrnutie|shrnut)"
    r"|čo\s+klient|co\s+klient",                       # čo klient
    re.IGNORECASE)

_BYPASS_RE = re.compile(r"airuleset:meeting-delegation-ok\s*(?P<reason>.*)",
                        re.IGNORECASE)


def _log(line):
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", DELEGATION_LOG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        return


def _read_script_path(tin):
    """The Workflow scriptPath file content appended to any inline script (the
    same best-effort read block-banned-model.sh uses). Any error -> ""."""
    path = tin.get("scriptPath") or ""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _dispatch_text(tin):
    """(primary, full) for the dispatch. `primary` is the prompt (Agent/Task) or
    the script (Workflow) -- whose FIRST line the MECHANICAL-ONLY marker must sit
    on. `full` is everything scanned for signals/verbs (prompt + inline script +
    scriptPath content)."""
    prompt = tin.get("prompt") or ""
    script = tin.get("script") or ""
    script_file = _read_script_path(tin)
    primary = prompt or script or script_file
    full = "\n".join(p for p in (prompt, script, script_file) if p)
    return primary, full


def _first_signal(text):
    low = text.lower()
    for sig in _SIGNALS:
        if sig in low:
            return sig
    return None


def evaluate(payload):
    """('allow', reason) or ('block', reason). Pure of process exit so tests can
    assert the verdict directly; `main()` maps it to allow()/emit_block_stderr()."""
    tool = field_of(payload, "tool_name", "")
    if tool not in ("Agent", "Task", "Workflow"):
        return "allow", "not an Agent/Workflow dispatch"
    try:
        obj = json.loads(payload)
    except Exception:
        return "allow", "unparseable payload"
    tin = obj.get("tool_input") if isinstance(obj, dict) else None
    if not isinstance(tin, dict):
        return "allow", "no tool_input"

    primary, full = _dispatch_text(tin)
    signal = _first_signal(full)
    if not signal:
        return "allow", "no meeting-analysis signal in the dispatch"

    mb = _BYPASS_RE.search(full)
    if mb:
        _log("%s\tBYPASS\t%s" % (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            mb.group("reason").strip()))
        return "allow", "meeting-delegation-ok bypass (logged)"

    # BLOCK only a dispatch that actually delegates INTERPRETATION (an interp
    # verb / a reader fan-out / writing a deliverable). A dispatch that merely
    # NAMES a meeting artifact without interpretation intent — a bug fix on the
    # skill's own code, a review, a grep — is ALLOWED (#1076 review, both
    # reviewers: the old "signal alone blocks" wedged airuleset's own dev + code
    # reviews + greps that name the artifact vocabulary). The `Analysed-by:`
    # stamp + Stop gate are the backstop for a paraphrased fan-out that dodges
    # the verb list (a documented residual of any keyword heuristic).
    mi = _INTERP_RE.search(full)
    if not mi:
        return "allow", ("names meeting artifact `%s` but asks no interpretation "
                         "(a mechanical / code / review / search dispatch)" % signal)

    verb = mi.group(0).strip()
    first_line = primary.split("\n", 1)[0] if primary else ""
    if _MARKER_RE.search(first_line):
        return "block", ("signal `%s`; the dispatch is marked MECHANICAL-ONLY but "
                         "also asks for interpretation (`%s`)" % (signal, verb))
    return "block", ("signal `%s`; the dispatch delegates meeting interpretation "
                     "(`%s`) — that runs in the MAIN session (Hard Rule 0)"
                     % (signal, verb))


def _block_message(reason):
    return (
        "BLOCKED: meeting-analysis interpretation runs in the MAIN session "
        "(#1076, owner 2026-09-18) -- %s\n"
        "\n"
        "  A meeting/call recording is INTERPRETED by the Fable main, never a "
        "subagent\n"
        "  (`CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-4-8`). Hard Rule 0 in\n"
        "  skills/meeting-analysis/SKILL.md: a subagent may do ONLY the "
        "mechanical\n"
        "  phases -- ffmpeg extraction, the ASR API call, frame dedup (phases "
        "1-3);\n"
        "  reading every screen, correlation, synthesis, per-task supplements "
        "and the\n"
        "  questions (phases 4-6) run in main, whole, no sampling.\n"
        "\n"
        "  Dispatch ONLY the mechanical phases (ffmpeg extract / ASR / frame "
        "dedup)\n"
        "  with NO interpretation verb; mark the prompt's FIRST line "
        "`MECHANICAL-ONLY:\n"
        "  extract|asr|dedup` for clarity. Interpretation runs in main. Bypass "
        "(rare,\n"
        "  logged): `airuleset:meeting-delegation-ok <reason>`." % reason)


def main():
    payload = read_payload()
    if not payload:
        allow()
    verdict, reason = evaluate(payload)
    if verdict == "block":
        _log("%s\tBLOCK\t%s" % (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), reason))
        emit_block_stderr(_block_message(reason))
    allow()


if __name__ == "__main__":
    main()
