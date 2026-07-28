"""Design-before-code mechanical gate (#136).

THE PROBLEM. `autonomous-batch-issue-development.md` mandates posting root
cause + chosen approach + rejected alternative to the ticket via `gh issue
comment` BEFORE the first code commit for that ticket. Commit `e9d1022`
(2026-07-09) moved that content out of the always-loaded modules into the
on-demand `batch-issue-development` skill; nothing in the `/autopilot`
dispatch chain ever invokes that skill, so for 18 days the step was
physically unreachable by every dispatched worker (a skill BODY never
reaches a dispatched subagent -- only its one-line description does; #104
probes). Commit `95dbc9b` (2026-07-27) restored the step as PROSE, inline in
`agents/autopilot-worker.md` -- reachable now, but with no teeth. Measured:
10/30 compliant pre-fix (33%), roughly half post-fix. Prose alone is exactly
the enforcement class #134 already proved does not hold on its own (the
per-ticket Discord card was prose too, and produced zero reports across
~85 merged PRs before it got a hook).

THE FIX -- a gate in the `subagent-stop-check-run-card.sh` family:

  1. A durable per-issue MARKER (`~/.claude/design-posted/<repo>#<issue>`),
     written only on DELIVERED evidence: a real `gh issue comment` that (a)
     actually posted (confirmed by re-reading the issue's comments back from
     GitHub -- never trusted from the command that was ABOUT to run) and (b)
     whose body plausibly carries root cause + chosen approach + rejected
     alternative (`classify_design_comment` below). The marker's presence is
     a claim, so it is only ever written from code that observed the real
     GitHub state -- the same #135 lesson `notify.marker_delivered` encodes.
  2. `hooks/block-commit-without-design.sh` (PreToolUse, `git commit`) --
     blocks an autopilot-worker's commit that references issue #N when no
     marker exists yet for #N.
  3. `hooks/subagent-stop-check-design.sh` (SubagentStop) -- a backstop for
     when the PreToolUse hook missed (not deployed on some box yet, a commit
     made outside the Bash tool, an explicit but unjustified bypass).

CLASSIFIER. `classify_design_comment` is a heuristic, not a proof of quality
-- it requires (a) the body clears a minimum length (a real paragraph, not
"on it") and (b) uses language from all three concept families: root cause,
chosen approach, rejected alternative (English + Slovak keyword sets, since
tickets in this fleet are written in both). Same documented limitation as
`block-ungated-issue-filing.sh`: this cannot verify the CONTENT is actually
correct, only that a claim of the right SHAPE was made. That is intentional,
not an oversight -- a mechanical gate can judge shape, never engineering
judgment. It is the SAME classifier `scripts/measure_design_compliance.py`
(the Deliverable-1 corpus measurement) uses, so the measured baseline and
the enforced gate are provably the same yardstick, not two independent
guesses that happen to agree.

STATE-FILE CONVENTION. Marker key = `<repo-key>#<issue>`, where repo-key is
`notify.repo_name_for(cwd)` -- the bare last path segment of the `origin`
remote (e.g. "airuleset", "parovanie-produktov"). This is the EXACT
convention `autopilot-notify-sent/` / `_notify_run_card` already use (see
that function's own docstring for the directory-vs-remote-name incident it
exists to avoid) -- deliberately not reinvented here.
"""
import os
import re
import time

_DESIGN_DIRNAME = "design-posted"

# A real "one honest paragraph" (the worker system prompt's own phrase for
# the minimum acceptable depth) reliably clears this; "on it" / "working on
# this now" does not. Calibrated, not derived -- see the classifier's known
# limits above.
MIN_LEN = 120

_CAUSE_RE = re.compile(
    r"root\s*cause|underlying\s*cause|caused\s+by|because\s+the\b|"
    r"pr[ií][cč]in|d[ôo]vod|sp[ôo]soben",
    re.IGNORECASE,
)
_APPROACH_RE = re.compile(
    r"\bapproach\b|chosen\s+approach|pr[ií]stup|rie[šs]enie|"
    r"implementujem|budem\s+(?:robi[ťt]|implementova[ťt]|pou[žz][íi]va[ťt])|"
    r"plan(?:ujem)?\s+to|will\s+(?:implement|fix|add|use|do)|"
    r"fix(?:ing)?\s+(?:it\s+)?by",
    re.IGNORECASE,
)
_ALT_RE = re.compile(
    r"alternat[ií]v|namiesto|zamietol|zavrhol|rejected|instead\s+of|"
    r"rather\s+than|considered\s+(?:but|and)|could\s+(?:also\s+)?have|"
    r"in[eé]\s+(?:mo[žz]nosti|rie[šs]enie)",
    re.IGNORECASE,
)


def classify_design_comment(body):
    """Heuristic verdict: does `body` plausibly carry root cause + chosen
    approach + rejected alternative? Returns `(ok: bool, reason: str)` --
    `reason` is always populated (either "ok" or what's missing), so a
    caller can surface it verbatim instead of re-deriving a message.

    Deliberately lenient in one direction: EITHER of the two Slovak/English
    phrasing families for a concept satisfies it -- this is a shape check,
    not a grader. See the module docstring for the documented limitation.
    """
    text = (body or "").strip()
    if len(text) < MIN_LEN:
        return False, "too short (%d chars, need >= %d)" % (len(text), MIN_LEN)
    missing = []
    if not _CAUSE_RE.search(text):
        missing.append("root cause")
    if not _APPROACH_RE.search(text):
        missing.append("chosen approach")
    if not _ALT_RE.search(text):
        missing.append("rejected alternative")
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "ok"


def _claude_dir():
    return os.path.join(os.path.expanduser("~"), ".claude")


def design_dir():
    return os.path.join(_claude_dir(), _DESIGN_DIRNAME)


def marker_key(repo_key, issue):
    return "%s#%s" % (repo_key, issue)


def marker_path(repo_key, issue):
    safe = re.sub(r"[^A-Za-z0-9._#-]", "_", marker_key(repo_key, issue))
    return os.path.join(design_dir(), safe)


def marker_exists(repo_key, issue):
    if not repo_key or issue in (None, ""):
        return False
    return os.path.isfile(marker_path(repo_key, issue))


def write_marker(repo_key, issue, comment_url, reason="ok", ts=None):
    """Record DELIVERED evidence for `<repo_key>#<issue>`. Never call this
    speculatively -- only from a code path that has just observed a REAL
    posted comment (see `hooks/post-record-design-comment.sh`). Best-effort:
    an unwritable ~/.claude never raises, it just means the gate stays
    active (fail toward re-asking, never toward silently trusting)."""
    if not repo_key or issue in (None, ""):
        return False
    d = design_dir()
    try:
        os.makedirs(d, exist_ok=True)
        with open(marker_path(repo_key, issue), "w", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n" % (ts if ts is not None else time.time(),
                                       comment_url or "-", reason or "ok"))
        return True
    except OSError:
        return False


def read_marker(repo_key, issue):
    """`{"ts": float, "url": str, "reason": str}` or None. Tolerates the
    legacy/short shape (fewer than 3 fields) rather than raising."""
    try:
        with open(marker_path(repo_key, issue), encoding="utf-8") as fh:
            body = fh.read(2000).strip()
    except OSError:
        return None
    parts = body.split("\t")
    if not parts or not parts[0]:
        return None
    try:
        ts = float(parts[0])
    except ValueError:
        ts = None
    return {
        "ts": ts,
        "url": parts[1] if len(parts) > 1 else "",
        "reason": parts[2] if len(parts) > 2 else "",
    }


# Matches a commit-message issue reference: `(#41)`, `Closes #41`, `#41:`,
# `#137/#139` (this repo's own real "docs: entry for #137/#139" shape) --
# a `#` preceded by start-of-text/whitespace/opening-paren/slash, followed
# by digits and a non-digit (or end). Deliberately simple (word-bounded regex,
# not a full shell/quote-aware parse) -- see
# `hooks/block-commit-without-design.sh` for why that simplification is
# safe here: the cost of a false positive/negative in either direction is
# low (an over-strict block costs one extra `gh issue comment`; a missed
# reference just leaves the gate silent for that commit, exactly like any
# other "unmeasurable -> never guess" case in this repo).
ISSUE_REF_RE = re.compile(r"(?:^|[\s(/])#([0-9]+)\b")


def issue_refs(text):
    """Every distinct issue number referenced in `text`, in first-seen
    order. Empty list when there is nothing to check."""
    seen = []
    for m in ISSUE_REF_RE.finditer(text or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen
