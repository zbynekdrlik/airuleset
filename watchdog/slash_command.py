"""Recognise Claude Code's transcript record of a typed slash command (#1203).

A local command such as `/model <id>` is NOT written to the transcript as the
raw text: the accepted `user` turn is the composite
`<command-name>/model</command-name> <command-message>model</command-message>
<command-args><id></command-args>`. So `transcripts._submit_confirmed`'s plain
substring match can never confirm a verified `/model` delivery (the same
finding `submit_own_goal_verified` works around for `/goal` by a pane read).
A leaf with no watchdog imports.
"""
import re

_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def matches(entry_text, want):
    """True iff `entry_text` records the typed slash command `want`: the
    command name AND the exact args must match (an earlier `/model` with other
    args never confirms). A `want` that is not a slash command never matches."""
    if not isinstance(entry_text, str) or not isinstance(want, str) or not want.startswith("/"):
        return False
    name, _, args = want.partition(" ")
    if ("<command-name>%s</command-name>" % name) not in entry_text:
        return False
    hit = _ARGS_RE.search(entry_text)
    return bool(hit) and hit.group(1).strip() == args.strip()
