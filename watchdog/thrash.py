"""#1260 — Claude Code's "Autocompact is thrashing" stop is never resumed.

CC stops a turn whose context refills to the compact window within 3 turns of the
previous compact, 3 times in a row. It writes a synthetic assistant entry with
`isApiErrorMessage: true` and `apiError: "autocompact_thrashing"`, so job 1 used
to read it as a resumable API stall. A `continue` cannot shrink a context that is
already over the window before the first step: it only replays the thrash. On
david3 (24./25.10.2026) job 1 typed 31 of them in one night, ~40 % of a weekly
limit with no work done. Only a human `/clear` or a smaller baseline fixes it.
"""
import re

_AUTOCOMPACT_THRASH_RX = re.compile(r"autocompact is thrashing", re.I)


def is_autocompact_thrash(text):
    """True for CC's thrash stop. The input is always an `isApiErrorMessage`
    entry's text (job 1's `transcript_last_error`), so prose cannot reach it."""
    return bool(text) and bool(_AUTOCOMPACT_THRASH_RX.search(text))


def hold(state, key, entry, project, logs):
    """Park the episode like a usage cap: `decide()` returns 'wait' for a dormant
    entry until the error changes, so no sweep ever types `continue` into it.
    Journal only — the stopped pane already shows CC's own banner to the owner."""
    entry["nudges"], entry["escalated"], entry["dormant"] = [], True, True
    state[key] = entry
    logs.append("autocompact-thrash %s — no continue (#1260)" % project)
