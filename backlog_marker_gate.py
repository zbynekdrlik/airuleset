"""#166 -- mention-vs-use classifier for the `🏁 BACKLOG EMPTY:` marker #159
introduced in the three `/goal` STOP CONDITIONS templates
(`skills/autopilot/SKILL.md`).

THE PROBLEM. `/autopilot`'s `/goal` loop reads only the conversation
transcript; it cannot run `gh`. #159 made a false "backlog empty" stop
require a PRESENCE test (the `🏁 BACKLOG EMPTY:` marker plus pasted `gh`
proof) instead of accepting confident prose. #166 set out to make that
presence test MECHANICAL -- a Stop hook that re-runs `gh` itself and blocks
the stop when the pasted claim disagrees with reality.

Measured before writing that hook (see the #166 issue comment posted before
this module's first commit, and the new entry in
`.claude/rules/airuleset-internals.md`): Claude Code overrides ANY blocking
Stop hook -- including a brand new project one -- after
`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (default 8) consecutive blocked Stop
events, and force-ends the turn anyway (confirmed both by reading the CC
2.1.220 binary directly and by a live control-arm probe in an isolated
scratch session). A Stop hook can therefore only ever DELAY a false stop,
never durably PREVENT one -- so #166 does not ship an enforcing hook.

RE-SCOPE. The value that survives is the one piece #160's watchdog-side fix
(its own Acceptance item 1: "before taking that exit, run `gh issue list`
... Tickets remain -> the achievement is FALSE -> re-arm") needs and would
otherwise have to re-derive: telling a genuine `🏁 BACKLOG EMPTY:` CLAIM
(the marker starting its own line, in a live completion turn) apart from a
MENTION of the marker string -- inside a fenced code block, an inline
backtick span, or mid-line prose. This is exactly the self-tripping risk
the #166 ticket named: "any session working on this protocol writes that
string -- including the one that implemented #159." Both
`skills/autopilot/SKILL.md` (the shipped template's own worked example,
backtick-wrapped) and `tests/test_goal_backlog_proof.py` (a Python string
literal assignment) genuinely mention the marker without ever emitting a
real claim, and a naive substring scan would misclassify both.

This module does PRESENCE detection only -- it never calls `gh` and never
compares the claim's content against reality. That comparison is #160's own
job (it has the pane's cwd and the real backlog state; this module doesn't).
"""
import re

MARKER = "🏁 BACKLOG EMPTY:"

# Fenced code blocks (```...```, greedy across lines) and inline backtick
# spans (`...`, single line) -- both strip the ENTIRE span including the
# backticks themselves, so a marker occurrence inside either is removed
# before the line-start test ever sees it.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_BACKTICK_RE = re.compile(r"`[^`\n]*`")


def naive_marker_present(text):
    """Dumb substring check -- `MARKER in text`, nothing else. Exists only
    for the corpus-replay comparison (`scripts/replay_backlog_marker_
    corpus.py`); never call this to make a real decision."""
    return MARKER in (text or "")


def _strip_noise(text):
    """Remove fenced code blocks, then inline backtick spans. Order
    matters: a fenced block can itself contain single backticks (rare but
    possible in a worked example), so stripping fences first prevents the
    inline pass from ever seeing partial fence content."""
    text = _FENCE_RE.sub("", text)
    text = _INLINE_BACKTICK_RE.sub("", text)
    return text


def classify_backlog_empty_claim(text):
    """`(present: bool, reason: str)` -- is `text` a genuine
    `🏁 BACKLOG EMPTY:` CLAIM (the marker starting its own line, outside any
    fenced code block or inline backtick span), as opposed to a MERE
    MENTION of the marker string?

    Deliberately narrow, matching the ticket's own acceptance wording ("a
    message merely DISCUSSING the marker (backticked, fenced, or mid-line)
    does not fire"): this checks SHAPE (mention vs. use), never whether the
    claim's content is actually true -- that needs a real `gh` call against
    the real backlog, which is #160's job, not this module's."""
    if not text:
        return False, "empty text"
    stripped = _strip_noise(text)
    for line in stripped.splitlines():
        if line.strip().startswith(MARKER):
            return True, "genuine line-start claim"
    if MARKER in stripped:
        return False, "marker present but not at line start (mid-line mention)"
    if MARKER in text:
        return False, "marker only inside a fenced code block or backtick span"
    return False, "marker not present"
