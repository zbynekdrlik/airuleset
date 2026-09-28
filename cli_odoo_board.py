"""Per-board client-board profile data + Odoo record primitives (#1167).

The machine-readable half of the `client-board-tasks.md` profile table: facts
the Job 49 task-hygiene overseer (`cli_task_hygiene`) and the quals freshness
tags (`cli_quals` `stale!` / `converge!`) must both honour. Today it carries ONE
fact — the montalu Verifikácia AUTO-CLOSE WAIT (client-board-stages.md rule 6,
owner ROZHODNUTÉ 28.9.2026, mechanism odoo-erp#8507):

  * ONE reminder after `reminder_days` (14) in Verifikácia without a reaction,
  * then Hotovo at >= `close_days` (21) AND >= `after_reminder_days` (7) after
    the reminder,
  * the clock is `project.task.date_last_stage_update` (odoo-erp#8507 rule 2),
  * a REACTION (any chatter comment by someone other than the streams and the
    bot authors, after the task entered Verifikácia) cancels the countdown —
    the stream then owns the task exactly as before.

Inside that wait a montalu task is NOT "reminder due" (class C) and its GitHub
ticket is neither `stale!` nor `converge!`; past it (plus one daily cron cycle
of grace) with no auto-close it is flagged OVERDUE, so a stuck mechanism still
surfaces. The bot authors (OdooBot posts the reminder and the close note) are
never an "unanswered client comment" (class A) on such a board.

WHERE the data lives: the per-board default below is keyed on the instance
HOST, so every montalu box gets it on push without hand-editing N per-box
`odoo-task-tracking.json` files (the manual rot the design rejects). The per-box
config key `verif_auto_close` overrides it: a dict replaces the profile values,
`false`/`null` switches the wait off. Any invalid value falls back to NO wait —
the loud direction (the old 3-day reminder), never a silent exemption.

HOW quals reads it: never a live Odoo call. Job 49 persists the tickets whose
task sits inside the wait (`verif_wait` in `~/.claude/task-hygiene/status.json`,
ticket -> until-ts); `auto_close_wait_numbers` honours an entry only while the
status file is fresh (<= STATUS_MAX_AGE_S) and the entry's until-ts is in the
future, so a dead Job 49 or an expired wait restores the tags.
"""
import datetime
import re
import time
from urllib.parse import urlparse

# host -> profile data. A new board adopting an auto-close = a new row.
BOARD_PROFILES = {
    "erp.montalu.cloud": {
        "verif_auto_close": {"reminder_days": 14, "close_days": 21,
                             "after_reminder_days": 7,
                             "bot_author_names": ["OdooBot"]},
    },
}
_WAIT_KEYS = ("reminder_days", "close_days", "after_reminder_days")
_DEFAULT_BOT_NAMES = ("OdooBot",)

# One daily cron cycle of grace before "overdue" (the mechanism is a cron; at
# exactly 21.0 days it may simply not have run yet).
GRACE_DAYS = 1
# Job 49 runs every ~2 h; three missed runs and the exemption lapses.
STATUS_MAX_AGE_S = 6 * 3600
# Extra task fields the auto-close classification reads.
TASK_FIELDS = ("date_last_stage_update", "description")
# The client-board-tasks.md rule 2 description trailer `(GitHub #N)` and the
# `GitHub ticket: #N` marker form. Only GitHub-anchored refs: a bare `(#N)`
# could be anything, and a wrong match would HIDE a ticket.
_TICKET_RX = re.compile(r"GitHub(?:\s+ticket)?\s*:?\s*#(\d+)", re.IGNORECASE)
_DAY_S = 86400.0


def m2o(value):
    """Odoo many2one → `(id, name)`; `(None, "")` for a False/empty value."""
    if isinstance(value, (list, tuple)) and value:
        vid = value[0] if isinstance(value[0], int) else None
        name = value[1] if len(value) > 1 and isinstance(value[1], str) else ""
        return vid, name
    if isinstance(value, int):
        return value, ""
    return None, ""


def parse_dt(s):
    """Odoo naive-UTC datetime string → aware UTC datetime, or None."""
    if not isinstance(s, str) or not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt).replace(
                tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
    return None


def _host(cfg):
    try:
        return (urlparse(str(cfg.get("instance_url") or "")).hostname or "").lower()
    except ValueError:
        return ""


def auto_close_profile(cfg):
    """The board's validated Verifikácia auto-close wait, or None (no wait —
    the legacy `client_confirm_days` reminder applies). A per-box
    `verif_auto_close` key wins over the host default; `false`/`null` = off."""
    if not isinstance(cfg, dict):
        return None
    if "verif_auto_close" in cfg:
        raw = cfg.get("verif_auto_close")
    else:
        raw = BOARD_PROFILES.get(_host(cfg), {}).get("verif_auto_close")
    if not isinstance(raw, dict):
        return None
    prof = {}
    for key in _WAIT_KEYS:
        v = raw.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            return None                    # invalid → no wait (loud direction)
        prof[key] = float(v)
    names = raw.get("bot_author_names", list(_DEFAULT_BOT_NAMES))
    pids = raw.get("bot_partner_ids", [])
    prof["bot_author_names"] = {n for n in names if isinstance(n, str)} \
        if isinstance(names, list) else set(_DEFAULT_BOT_NAMES)
    prof["bot_partner_ids"] = {p for p in pids if isinstance(p, int)
                               and not isinstance(p, bool)} \
        if isinstance(pids, list) else set()
    return prof


def is_bot(author_value, prof):
    """True iff the message author is one of the profile's bot authors."""
    if not prof:
        return False
    aid, name = m2o(author_value)
    return (name in prof["bot_author_names"]
            or (aid is not None and aid in prof["bot_partner_ids"]))


def counted(msgs, prof):
    """The messages the A/C verdicts read: all of them, or — on an auto-close
    board — every message except the bot authors' (still newest-first)."""
    if not prof:
        return msgs
    return [m for m in msgs if not is_bot(m.get("author_id"), prof)]


def ticket_numbers(description):
    """GitHub ticket numbers named by a task description (sorted, unique)."""
    if not isinstance(description, str):
        return []
    return sorted({int(n) for n in _TICKET_RX.findall(description)})


def classify(task, msgs, prof, now, is_stream):
    """The auto-close state of ONE Verifikácia task, or None when the legacy
    reminder rule applies (no profile, an unreadable stage clock, or a
    reaction that cancelled the countdown).

    `msgs` are the task's comments newest-first; `is_stream(author_value)` is
    the caller's stream/owner identity test. Returns `{"auto_close":
    "wait"|"overdue", "days", "until_ts", "tickets"}` — `until_ts` is when the
    wait ends and the task becomes overdue."""
    if not prof:
        return None
    entered = parse_dt(task.get("date_last_stage_update"))
    if entered is None:
        return None
    reminder = None
    for m in msgs:
        author = m.get("author_id")
        dt = parse_dt(m.get("date"))
        if is_bot(author, prof):
            if dt is not None and dt >= entered and (reminder is None or dt > reminder):
                reminder = dt
            continue
        if is_stream(author):
            continue
        if dt is None or dt >= entered:
            return None                # a reaction (or an undatable one) → stream owns it
    grace = GRACE_DAYS * _DAY_S
    until = entered.timestamp() + prof["close_days"] * _DAY_S + grace
    if reminder is not None:
        until = max(until, reminder.timestamp()
                    + prof["after_reminder_days"] * _DAY_S + grace)
    now_ts = now.timestamp()
    return {"auto_close": "overdue" if now_ts >= until else "wait",
            "days": int((now_ts - entered.timestamp()) / _DAY_S),
            "until_ts": until,
            "tickets": ticket_numbers(task.get("description"))}


def wait_ticket_map(wait_items, overdue_items):
    """`{"<ticket>": until_ts}` for the status file: every ticket a waiting
    task names, minus any ticket an OVERDUE task also names (a stuck sibling
    must never be hidden by a waiting one)."""
    stuck = {n for it in overdue_items for n in it.get("tickets") or []}
    out = {}
    for it in wait_items:
        for n in it.get("tickets") or []:
            if n in stuck:
                continue
            key = str(n)
            out[key] = max(out.get(key, 0), it.get("until_ts") or 0)
    return out


def auto_close_wait_numbers(rows, home=None, now=None):
    """The W member numbers (keys of `rows`) whose Odoo task sits inside its
    auto-close wait, per the LAST Job 49 run (never a live Odoo call). Empty
    when the status file is absent/corrupt/stale or carries no `verif_wait` —
    the safe direction (the tags fire as before)."""
    import cli_task_hygiene
    now = time.time() if now is None else now
    st = cli_task_hygiene.read_status(home)
    if not isinstance(st, dict):
        return set()
    ts = st.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) \
            or not 0 <= now - ts <= STATUS_MAX_AGE_S:
        return set()
    waits = st.get("verif_wait")
    if not isinstance(waits, dict):
        return set()
    live = set()
    for key, until in waits.items():
        if isinstance(until, bool) or not isinstance(until, (int, float)) \
                or until <= now:
            continue
        try:
            live.add(int(key))
        except (TypeError, ValueError):
            continue
    return {n for n in (rows or {}) if n in live}
