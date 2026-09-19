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

This gate REFUSES an Agent/Workflow dispatch that carries INTERPRETATION intent
for a meeting (#1076 integration review):
  - a PHRASE that NAMES the interpretation act (`analýza meetingu` / `meeting
    analysis` / `doplnok z meetingu`, diacritic + both-order robust) blocks on
    its own -- the owner's literal "Sprav analýzu meetingu…" / "spracuj … meeting"
    must block;
  - a FILE-ARTIFACT signal (`transcript.txt` / `speaker_turns.json` /
    `frames_kept` / `screen_inventory` / `_verbatim.md` / `VIDEO-NOTES`) blocks
    ONLY when an interpretation-intent STEM is also present (analy[yý][sz] /
    spracuj / vyhodno / summari / interpret / read-the-screen / reader fan-out /
    write-a-deliverable / prečítaj / zhrň / čo klient) -- so a grep, a code
    review, or a bug fix that merely NAMES an artifact stays ALLOWED.
A dispatch with no meeting signal at all passes untouched.

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

# ARTIFACT signals -- the FILE artifacts a meeting-analysis dispatch names. These
# are a SIGNAL but NOT intent: a dispatch that only names one (a grep, a code
# review, a bug fix on the skill's own code) is ALLOWED unless it ALSO carries an
# interpretation-intent token (#1076 integration review). Substring, case-insensitive.
_ARTIFACT_SIGNALS = (
    "transcript.txt",
    "speaker_turns.json",
    "frames_kept",
    "screen_inventory",
    "_verbatim.md",
    "video-notes",
)

# PHRASE-INTENT signals -- phrases that NAME the interpretation act itself, so
# they are BOTH a signal AND intent (a dispatch carrying one BLOCKS on its own,
# no separate verb needed -- #1076 integration review: the owner's literal
# "Sprav analýzu meetingu …" must block). Diacritic-robust (`anal[yý][sz]`
# catches analýza/analýzu/analyza/analysis). `\w*\s+` is ReDoS-safe (disjoint
# classes either side of the gap).
#
# NOTE (#1076 delta review): the ENGLISH `meeting[\s-]anal[yý][sz]` order is
# DELIBERATELY NOT a phrase-alone arm -- the skill's own directory is literally
# `skills/meeting-analysis`, so a `meeting-analysis` phrase-alone arm blocks
# every dev/grep/review/worker dispatch that merely names the skill (incl. the
# autopilot-worker dispatch for this ticket). A REAL English interpretation
# ("do the meeting analysis from transcript.txt") still blocks via the
# `anal[yý][sz]` STEM in _INTERP_RE + the artifact signal; only the analysis→
# meeting (Slovak) order and `doplnok z meetingu` are phrase-alone.
_PHRASE_INTENT_RE = re.compile(
    r"anal[yý][sz]\w*\s+meeting"       # analýza/analýzu/analyza meetingu; "analysis meeting"
    r"|doplnok\s+z\s+meeting",              # doplnok z meetingu
    re.IGNORECASE)

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
# #1076 integration review: intent = STEMS, not conjugations, so a plain Slovak
# dispatch ("Sprav analýzu…", "spracuj ten meeting", "vyhodnoť…") is caught. Each
# stem is a bare substring (`re.search` finds it anywhere); the `\s+`-gapped arms
# keep disjoint classes either side → ReDoS-safe (#577).
_INTERP_RE = re.compile(
    r"read\s+the\s+screen"
    r"|reads?\s+(?:the\s+|every\s+|each\s+)?(?:screen|frame|jpg|obrazovk)"
    r"|readers?\s+over"                 # "parallel readers over transcript segments"
    r"|parallel\s+readers?"
    r"|summari[sz]|sumariz"             # summarise/summarize/summary, sumarizuj
    r"|anal[yý][sz]"                    # analyse/analyze/analysis, analýza/analýzu/analyzuj
    r"|interpret"
    r"|vyhodno[tť]"                     # vyhodnoť/vyhodnotiť/vyhodnotenie
    r"|spracuj|spracova[tť]|spracovan"  # spracuj/spracovať/spracovanie ten meeting
    r"|inspects?\s+(?:the\s+|each\s+|its\s+)?(?:screen|frame)"
    r"|what\s+did\s+the\s+client"
    r"|(?:list|extract|identify|gather|capture)\w*\s+(?:the\s+)?requirement"
    r"|(?:write|writes|writing|record|records|recording)\s+[\w ]{0,20}"
    r"(?:notes|mapping|screen_inventory|inventory|deliverable|tickets?|summary)"
    r"|map\s+(?:it\s+|them\s+)?to\s+(?:tickets?|requirement)"
    r"|prečítaj|precitaj|zhr[nň]"       # prečítaj / zhrň / zhrnutie
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


def _first_artifact(text):
    low = text.lower()
    for sig in _ARTIFACT_SIGNALS:
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
    phrase = _PHRASE_INTENT_RE.search(full)     # names the interpretation act
    artifact = _first_artifact(full)            # names a file artifact
    if not (phrase or artifact):
        return "allow", "no meeting-analysis signal in the dispatch"

    mb = _BYPASS_RE.search(full)
    if mb:
        _log("%s\tBYPASS\t%s" % (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            mb.group("reason").strip()))
        return "allow", "meeting-delegation-ok bypass (logged)"

    # BLOCK a dispatch with INTERPRETATION intent: a PHRASE signal names the
    # interpretation act itself (blocks on its own -- #1076 integration review:
    # "Sprav analýzu meetingu…" / "spracuj ten meeting" must block), OR an
    # interp-verb/stem is present. A dispatch that only NAMES a file ARTIFACT
    # (a grep, a code review, a bug fix on the skill's own code) with NO intent
    # is ALLOWED. Escape a legit interpretation-adjacent dispatch (e.g. a review
    # of THIS gate) with `airuleset:meeting-delegation-ok <reason>` (logged).
    mi = _INTERP_RE.search(full)
    if not (phrase or mi):
        return "allow", ("names meeting artifact `%s` but asks no interpretation "
                         "(a mechanical / code / review / search dispatch)" % artifact)

    signal = phrase.group(0).strip() if phrase else artifact
    intent = phrase.group(0).strip() if phrase else mi.group(0).strip()
    first_line = primary.split("\n", 1)[0] if primary else ""
    if _MARKER_RE.search(first_line):
        return "block", ("signal `%s`; the dispatch is marked MECHANICAL-ONLY but "
                         "also asks for interpretation (`%s`)" % (signal, intent))
    return "block", ("signal `%s`; the dispatch delegates meeting interpretation "
                     "(`%s`) — that runs in the MAIN session (Hard Rule 0)"
                     % (signal, intent))


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
        "  extract|asr|dedup` for clarity. Interpretation runs in main.\n"
        "\n"
        "  If this dispatch is NOT a meeting interpretation (e.g. a code review "
        "of, or\n"
        "  a bug fix on, the meeting-analysis gate/skill itself that legitimately "
        "names\n"
        "  the vocabulary), add `airuleset:meeting-delegation-ok <reason>` to the "
        "prompt\n"
        "  to bypass (rare, logged)." % reason)


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
