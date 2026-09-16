"""#1053 — verify-on-copy hand-back obligation: after the gatekeeper deploys and
returns a ticket to the sub-dev with `verify-on-copy`, the stream MUST verify the
deployed change on its own fresh PROD copy (REFRESH-DEV-BOX-FROM-PROD) and post a
`Verified-on-copy: refresh <id> at <ISO-UTC> — <what was checked>` comment. A
`verify-on-copy` ticket older than 24 h WITHOUT that comment is the stream's
Stop-hook obligation — the SAME class as the #1036 task-hygiene gate.

This module is the persisted-status writer + pure classifiers the Stop hook reads
(`hooks/stop-check-untracked-work.sh`, extending the existing #1036 family — a
status file + fail-open, never a gh call on the hook). The footer refresh
(`cmd_tickets_status --refresh`) already holds the stream's slice rows, so it is
the writer: for each verify-on-copy row it reads the ticket timeline ONCE (the
same bounded per-candidate pattern `_slice_mine_and_handed` already uses) to find
the label-add anchor and any verification comment after it, then persists the
overdue set. Fail-open everywhere: a gh error / absent-stale status never blocks.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

VERIFY_LABEL = "verify-on-copy"
OVERDUE_SECONDS = 24 * 3600
# The hook treats a status file older than this as a dead writer → fail open,
# mirroring the #1036 task-hygiene STALE window.
STALE_SECONDS = 3 * 3600

_STATUS_DIRNAME = "verify-on-copy"
_STATUS_BASENAME = "status.json"

# A line-anchored, colon-required `Verified-on-copy:` marker (the #818 lesson:
# never a bare substring — a quoted/draft/prose mention must not satisfy it). A
# leading `>` quote is DELIBERATELY excluded from the prefix class: a reply that
# QUOTES an old verification must not clear the current hand-back.
_VERIFIED_MARKER_RE = re.compile(
    r"(?mi)^[ \t*#-]*\**[ \t]*Verified-on-copy\**[ \t]*:")


def status_path(home=None):
    home = home or os.path.expanduser("~")
    return Path(home) / ".claude" / _STATUS_DIRNAME / _STATUS_BASENAME


def has_verified_marker(body):
    """True iff `body` carries a genuine `Verified-on-copy:` line."""
    if not isinstance(body, str) or not body:
        return False
    return bool(_VERIFIED_MARKER_RE.search(body))


def _iso_to_epoch(s):
    """Parse a GitHub ISO-8601 UTC timestamp ('2026-09-16T20:30:00Z') to epoch
    seconds, or None on any malformed value (fail-safe: an unparseable anchor
    is treated as 'no anchor', so the ticket is NOT flagged)."""
    if not isinstance(s, str) or not s:
        return None
    try:
        from datetime import datetime, timezone
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def label_add_anchor(events):
    """The epoch of the LATEST `labeled verify-on-copy` timeline event in
    `events` (the age anchor — when the ticket ENTERED verify-on-copy), or None
    when no such event is present."""
    anchor = None
    if not isinstance(events, list):
        return None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("event") != "labeled":
            continue
        name = (ev.get("label") or {}).get("name")
        if name != VERIFY_LABEL:
            continue
        ts = _iso_to_epoch(ev.get("created_at"))
        if ts is not None and (anchor is None or ts > anchor):
            anchor = ts
    return anchor


def verified_after(events, since_ts):
    """True iff a `Verified-on-copy:` comment was posted AT/AFTER `since_ts`
    (a verification for the CURRENT hand-back — an older one for a prior deploy
    does not count). A None `since_ts` means no anchor → treat as not-verified-
    for-this-cycle so the caller's anchor guard decides."""
    if since_ts is None or not isinstance(events, list):
        return False
    for ev in events:
        if not isinstance(ev, dict) or ev.get("event") != "commented":
            continue
        if not has_verified_marker(ev.get("body")):
            continue
        ts = _iso_to_epoch(ev.get("created_at"))
        if ts is not None and ts >= since_ts:
            return True
    return False


def timeline_overdue(number, title, events, now):
    """Return `{number, title, age_h}` when the ticket's verify-on-copy hand-back
    is OVERDUE (anchored > 24 h ago AND not verified since the anchor), else
    None. No anchor (label never added / unparseable) → NOT overdue (fail-safe:
    never invent an obligation)."""
    anchor = label_add_anchor(events)
    if anchor is None:
        return None
    if (now - anchor) <= OVERDUE_SECONDS:
        return None
    if verified_after(events, anchor):
        return None
    return {"number": number, "title": title,
            "age_h": int((now - anchor) // 3600)}


def compute_overdue(items, now):
    """`items` = iterable of `(number, title, events)`; return the list of
    overdue `{number, title, age_h}` dicts. Pure — the footer supplies the
    already-fetched timelines."""
    out = []
    for number, title, events in items:
        rec = timeline_overdue(number, title, events, now)
        if rec is not None:
            out.append(rec)
    return out


def persist_status(overdue, home=None, now=None, repo=None):
    """Write `{ts, overdue, repo}` for the Stop hook (never raises — a footer
    refresh must never break on this; logs to stderr on failure)."""
    now = now if now is not None else time.time()
    path = status_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": now, "overdue": overdue, "repo": repo}
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as h:
            json.dump(payload, h)
        os.replace(tmp, str(path))
    except OSError as e:
        sys.stderr.write("verify-on-copy: could not persist status (%s)\n" % e)


def read_status(home=None):
    """The persisted status dict, or None (absent/corrupt → the hook fails
    open)."""
    try:
        with open(status_path(home), encoding="utf-8") as h:
            st = json.load(h)
    except (OSError, ValueError):
        return None
    return st if isinstance(st, dict) else None
