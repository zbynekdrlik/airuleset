"""#1260 — Claude Code's "Autocompact is thrashing" stop is never resumed.

CC stops a turn whose context refills to the compact window within 3 turns of the
previous compact, 3 times in a row. It writes a synthetic assistant entry with
`isApiErrorMessage: true` and `apiError: "autocompact_thrashing"`, so job 1 used
to read it as a resumable API stall. A `continue` cannot shrink a context that is
already over the window before the first step: it only replays the thrash. On
david3 (24./25.10.2026) job 1 typed 31 of them in one night, ~40 % of a weekly
limit with no work done. Only a human `/clear` or a smaller baseline fixes it.

So a held session gets NO machine keystroke at all: job 1 holds the episode,
`send_verified` and the stash route refuse every non-owner text, `pane_ready`
refuses every idle-pane rider, and job 1b never resumes a thrashing worker. The
owner's own Discord reply still goes through. The owner gets ONE notice per
episode.
"""
import re

API_ERROR_KIND = "autocompact_thrashing"
_AUTOCOMPACT_THRASH_RX = re.compile(r"autocompact is thrashing", re.I)
NOTICE = ("🛑 **%s** — Claude Code zastavil session: *Autocompact is thrashing* "
          "(kontext je plný hneď po zhrnutí). Watchdog do nej už nič nepíše; "
          "pomôže len `/clear` alebo nová session.")


def is_autocompact_thrash(text):
    """True for CC's thrash banner text."""
    return bool(text) and bool(_AUTOCOMPACT_THRASH_RX.search(text))


def _last_api_error_kind(tpath):
    """The `apiError` field of the transcript's newest `isApiErrorMessage`
    assistant entry (CC's structured marker; survives a reworded banner)."""
    from watchdog.transcripts import _iter_jsonl_tail
    for entry in reversed(_iter_jsonl_tail(tpath, max_lines=200)):
        if (isinstance(entry, dict) and entry.get("type") == "assistant"
                and entry.get("isApiErrorMessage") is True):
            return entry.get("apiError")
    return None


def is_stop(err_text, tpath):
    """`err_text` = `transcript_last_error(tpath)`: a non-empty error that is the
    thrash banner OR carries the structured `autocompact_thrashing` kind."""
    if not err_text:
        return False
    return is_autocompact_thrash(err_text) or (
        bool(tpath) and _last_api_error_kind(tpath) == API_ERROR_KIND)


def held(tpath):
    """True while the session's last real reply is a thrash stop. Later tool
    activity reads as recovered (`transcript_last_error` semantics)."""
    if not tpath:
        return False
    import watchdog
    return is_stop(watchdog.transcript_last_error(tpath), tpath)


def pane_held(pid, run):
    """`held` for a pane known only by id (the stash route): its cwd's newest
    transcript. An unresolvable pane is not held (the route's own gates stand)."""
    import watchdog
    cwd = (run(["tmux", "display-message", "-p", "-t", pid, "#{pane_current_path}"]) or "").strip()
    if not cwd:
        return False
    tinfo = watchdog.find_active_transcript(watchdog.PROJECTS_DIR, cwd)
    return held(tinfo[0] if isinstance(tinfo, (tuple, list)) else tinfo)


def hold(state, key, entry, project, logs, send_fn, owner, dedup_key, dry_run):
    """Park the episode like a usage cap: `decide()` returns 'wait' for a dormant
    entry until the error changes, so this runs once per episode (one journal
    line, one notice)."""
    entry["nudges"], entry["escalated"], entry["dormant"] = [], True, True
    state[key] = entry
    logs.append("autocompact-thrash %s — no continue (#1260)" % project)
    send_fn(NOTICE % project, owner=owner, dedup_key=dedup_key, dry_run=dry_run)
