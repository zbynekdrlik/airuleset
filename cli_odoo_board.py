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
  * a REACTION — any chatter comment/e-mail after the task entered Verifikácia
    by anyone except the STREAMS and the bot authors (odoo-erp#8507 rule 5;
    the owner and every human count) — cancels the countdown, and the stream
    owns the task exactly as before (the legacy 3-day C rule).

Inside that wait a montalu task is NOT "reminder due" (class C) and its GitHub
ticket is neither `stale!` nor `converge!`. Past it (plus one daily cron cycle
of grace) with no auto-close the task lands in C as OVERDUE — the same surface
the old reminder-due C had (the `task-hygiene` report, the persisted `c`
count; C alone never nudges or Stop-blocks) — and the ticket's exemption has
expired, so `stale!`/`converge!` return. The bot authors (OdooBot posts the
reminder and the close note) are never an "unanswered client comment" (class A)
on such a board.

WHERE the data lives: the per-board default below is keyed on the instance
HOST, so every montalu box gets it on push without hand-editing N per-box
`odoo-task-tracking.json` files (the manual rot the design rejects). The per-box
config key `verif_auto_close` overrides it: a dict with ALL three day keys
replaces the profile values, `false`/`null` switches the wait off. Any invalid
or partial value falls back to NO wait — the loud direction (the old 3-day
reminder), never a silent exemption.

HOW quals reads it: never a live Odoo call. Job 49 persists `verif_wait`
(`{"repo": <owner/name>, "tickets": {"<N>": until_ts}}`) into
`~/.claude/task-hygiene/status.json`; `auto_close_wait_numbers` honours an
entry only while the status file is fresh (<= STATUS_MAX_AGE_S), the entry's
until-ts is in the future, AND the recorded repo is the quals checkout's
canonical repo — so a dead Job 49, an expired wait or another repo's same
number restores the tags. A ticket is exempt only when EVERY open task naming
it is waiting (a live sibling task keeps the ticket reported).
"""
import datetime
import os
import re
import time
from urllib.parse import urlparse

# host -> profile data. A new board adopting an auto-close = a new row.
BOARD_PROFILES = {
    "erp.montalu.cloud": {
        "verif_auto_close": {"reminder_days": 14, "close_days": 21,
                             "after_reminder_days": 7,
                             "bot_author_names": ["OdooBot"],
                             "stream_author_names": ["ZbynekAI", "MarekAI"],
                             "github_repo": "zbynekdrlik/odoo-erp"},
    },
}
_WAIT_KEYS = ("reminder_days", "close_days", "after_reminder_days")

# One daily cron cycle of grace before "overdue" (the mechanism is a cron; at
# exactly 21.0 days it may simply not have run yet), and the same tolerance for
# recognising the day-14 reminder.
GRACE_DAYS = 1
# Job 49 runs every ~2 h; three missed runs and the exemption lapses.
STATUS_MAX_AGE_S = 6 * 3600
# Extra task fields the auto-close classification reads.
TASK_FIELDS = ("date_last_stage_update", "description")
# Chatter message types a reaction can arrive as (a client answering the
# notification mail creates an `email` message); tracking `notification`s are
# NOT reactions (a stage move is logged as its mover's notification).
MESSAGE_TYPES = ("comment", "email")
# ONLY the client-board-tasks.md rule 2 trailer `(GitHub #N)` and the explicit
# `GitHub ticket: #N` marker — a prose mention ("nadväzuje na GitHub #800")
# must never exempt an unrelated ticket.
_TICKET_RX = re.compile(r"\(GitHub\s*#(\d+)\)|GitHub ticket:\s*#(\d+)",
                        re.IGNORECASE)
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


def _str_set(value):
    return {v for v in value if isinstance(v, str)} if isinstance(value, list) else set()


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
            return None                    # invalid/partial → no wait (loud)
        prof[key] = float(v)
    prof["bot_author_names"] = _str_set(raw.get("bot_author_names", ["OdooBot"]))
    pids = raw.get("bot_partner_ids", [])
    prof["bot_partner_ids"] = {p for p in pids if isinstance(p, int)
                               and not isinstance(p, bool)} \
        if isinstance(pids, list) else set()
    prof["stream_author_names"] = _str_set(raw.get("stream_author_names", []))
    repo = raw.get("github_repo", cfg.get("github_repo"))
    prof["github_repo"] = repo if isinstance(repo, str) and "/" in repo else None
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
    """GitHub ticket numbers a task description names (sorted, unique)."""
    if not isinstance(description, str):
        return []
    return sorted({int(a or b) for a, b in _TICKET_RX.findall(description)})


def classify(task, msgs, prof, now, stream_pids):
    """The auto-close state of ONE Verifikácia task, or None when the legacy
    reminder rule applies (no profile, an unreadable stage clock, or a
    reaction that cancelled the countdown).

    `msgs` are the task's comments/e-mails newest-first. The reminder is the
    EARLIEST bot message from `reminder_days` (minus the grace) after entry on,
    so a later recurring bot post never extends the wait. Returns
    `{"auto_close": "wait"|"overdue", "days", "until_ts", "tickets"}` —
    `until_ts` is when the wait ends and the task becomes overdue."""
    if not prof:
        return None
    entered = parse_dt(task.get("date_last_stage_update"))
    if entered is None:
        return None
    grace = GRACE_DAYS * _DAY_S
    remind_from = entered.timestamp() + prof["reminder_days"] * _DAY_S - grace
    reminder = None
    for m in msgs:
        author = m.get("author_id")
        dt = parse_dt(m.get("date"))
        if is_bot(author, prof):
            if dt is not None and dt.timestamp() >= remind_from \
                    and (reminder is None or dt < reminder):
                reminder = dt
            continue
        aid, name = m2o(author)
        if name in prof["stream_author_names"] or aid in stream_pids:
            continue                   # the streams never react (rule 5)
        if dt is None or dt >= entered:
            return None                # a reaction (or an undatable one) → stream owns it
    until = entered.timestamp() + prof["close_days"] * _DAY_S + grace
    if reminder is not None:
        until = max(until, reminder.timestamp()
                    + prof["after_reminder_days"] * _DAY_S + grace)
    now_ts = now.timestamp()
    return {"auto_close": "overdue" if now_ts >= until else "wait",
            "days": int((now_ts - entered.timestamp()) / _DAY_S),
            "until_ts": until,
            "tickets": ticket_numbers(task.get("description"))}


class AutoCloseTracker:
    """One Job 49 run's auto-close bookkeeping: classifies each open task
    (`observe`) and builds the quals-facing ticket map (`status`)."""

    def __init__(self, cfg):
        self.prof = auto_close_profile(cfg)
        self.stream_pids = set((cfg or {}).get("stream_partner_ids") or [])
        self.waiting = []
        self._blocked = set()

    def observe(self, task, msgs, now, in_verif):
        """Classify one open task (None off-profile / outside Verifikácia /
        legacy). A task that is NOT waiting blocks every ticket it names."""
        if not self.prof:
            return None
        ac = classify(task, msgs, self.prof, now, self.stream_pids) \
            if in_verif else None
        if ac is not None and ac["auto_close"] == "wait":
            self.waiting.append(dict(ac, task_id=task.get("id")))
        else:
            self._blocked.update(ticket_numbers(task.get("description")))
        return ac

    def status(self, truncated):
        """`{"repo", "tickets": {"<N>": until_ts}}` for status.json, or None
        (no profile / no repo / a truncated task read that may hide a sibling
        task — the loud direction)."""
        if not self.prof or not self.prof["github_repo"] or truncated:
            return None
        tickets = {}
        for it in self.waiting:
            for n in it["tickets"]:
                if n not in self._blocked:
                    tickets[str(n)] = max(tickets.get(str(n), 0), it["until_ts"])
        return {"repo": self.prof["github_repo"], "tickets": tickets}


def _repo_slug(root):
    """The quals checkout's canonical `owner/name` (LOCAL git only), or None."""
    try:
        from gates import ghread
        return ghread.canonical_slug(root or os.getcwd())
    except Exception:
        return None


def auto_close_wait_numbers(rows, root=None, home=None, now=None):
    """The W member numbers (keys of `rows`) whose Odoo task sits inside its
    auto-close wait, per the LAST Job 49 run (never a live Odoo call). Empty
    when the status file is absent/corrupt/stale, names another repo, or
    carries no live entry — the safe direction (the tags fire as before).
    Reads the status via `cli_task_hygiene.read_status` (deferred import: that
    module imports this one at load)."""
    import cli_task_hygiene
    now = time.time() if now is None else now
    st = cli_task_hygiene.read_status(home)
    if not isinstance(st, dict):
        return set()
    ts = st.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)) \
            or not 0 <= now - ts <= STATUS_MAX_AGE_S:
        return set()
    vw = st.get("verif_wait")
    waits = vw.get("tickets") if isinstance(vw, dict) else None
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
    hits = {n for n in (rows or {}) if n in live}
    if not hits:
        return set()                   # nothing to exempt → no git call
    repo, slug = vw.get("repo"), _repo_slug(root)
    if not isinstance(repo, str) or not slug or repo.casefold() != slug.casefold():
        return set()
    return hits
