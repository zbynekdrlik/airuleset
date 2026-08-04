"""Statusline ticket segment — autopilot done/total, else open GitHub issues.

Rendered by the airuleset caveman-statusline shim on EVERY prompt render, so the
hard rules are: NEVER block, NEVER touch the network inline. The segment is
composed from two small machine-local caches:

  ~/.claude/tickets-status/<cwd-key>.json   — {open, name, root, ts}; written by
      `airuleset.py tickets-status --refresh --cwd <dir>` (the only place that
      calls `gh`), spawned DETACHED by tickets_segment() when the cache is stale.
  ~/.claude/autopilot-progress/<repo>.json  — {done, remaining, ts}; written by
      `notify --run-card` each time a ticket's completion card is sent, so during
      an autopilot run the segment shows done/total instead of the open count.

stdlib only; every function is fail-safe (an error renders as no segment, never
a broken statusline).
"""
import calendar
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import burn

TICKETS_TTL_S = 120                 # refresh the open-issues count at most this often
SPAWN_GUARD_S = 30                  # min seconds between background refresh spawns
AUTOPILOT_RUN_WINDOW_S = 6 * 3600   # a run-card younger than this = active run
QUESTIONS_TTL_S = 24 * 3600         # mirror notify._QUESTIONS_TTL_S (map prune TTL)
CTX_GREEN_MAX = 150_000             # context-cost segment colour thresholds
CTX_YELLOW_MAX = 400_000            # (raw token count, not %)


def _claude_dir(home=None):
    return Path(home or os.path.expanduser("~")) / ".claude"


def cache_dir(home=None):
    return _claude_dir(home) / "tickets-status"


def progress_dir(home=None):
    return _claude_dir(home) / "autopilot-progress"


def cwd_key(cwd):
    return hashlib.sha1(str(cwd).encode()).hexdigest()[:12]


def _load(path):
    try:
        with open(path, encoding="utf-8") as h:
            d = json.load(h)
            return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _spawn_refresh(cwd, home=None):
    """Kick a DETACHED `tickets-status --refresh` for `cwd` — guarded by a marker
    mtime so a burst of statusline renders spawns at most one per SPAWN_GUARD_S."""
    guard = cache_dir(home) / (".spawn-" + cwd_key(cwd))
    try:
        if guard.exists() and time.time() - guard.stat().st_mtime < SPAWN_GUARD_S:
            return
        guard.parent.mkdir(parents=True, exist_ok=True)
        guard.touch()
    except OSError:
        return
    script = Path(__file__).resolve().parent / "airuleset.py"
    try:
        subprocess.Popen(
            [sys.executable, str(script), "tickets-status", "--refresh",
             "--cwd", str(cwd)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def tickets_segment(cwd, now=None, home=None, spawn=True):
    """The GitHub-tickets statusline segment for the session at `cwd`
    (label shortened 'Issues' -> 'I', #223):

      - 'I D/T' during an ACTIVE autopilot run for this repo (D tickets carded
        this run, T = D + the remaining backlog from the last card; green when
        the backlog is empty),
      - 'I N' otherwise (open non-autopilot-skip GitHub issues),
      - ''  when unknown (not a git/GitHub repo, gh unavailable, no cache yet).

    Reads caches only; a stale/missing tickets cache triggers a detached
    background refresh (unless spawn=False) and renders the stale value
    meanwhile — the statusline never waits on `gh`."""
    if not cwd:
        return ""
    now = time.time() if now is None else now
    cache = _load(cache_dir(home) / (cwd_key(cwd) + ".json"))
    if spawn and (cache is None or now - (cache.get("ts") or 0) > TICKETS_TTL_S):
        _spawn_refresh(cwd, home)
    if not cache:
        return ""

    # Skipped bucket (2026-07-16): tickets labeled autopilot-skip. An EXCLUSION
    # count, not a partition of the visible tickets (unlike gk, whose zero must
    # stay visible) — so it renders only when >= 1 and stays off the line at 0.
    # Label shortened 'skipped' -> 'skip' (#223).
    skipped = cache.get("skipped")
    skip_sfx = (" \033[38;5;245m· skip %d\033[0m" % skipped
                if isinstance(skipped, int) and skipped > 0 else "")

    # gk-req badge (airuleset #30): open needs-gatekeeper stream→supervisor
    # action requests (full-authority boxes collect the count). Orange —
    # a stream is BLOCKED waiting on this box's supervisor; hidden at 0.
    # Label shortened 'gk-req' -> 'gkq' (#223).
    gk_req = cache.get("gk_req")
    if isinstance(gk_req, int) and gk_req > 0:
        skip_sfx += " \033[38;5;208m· gkq %d\033[0m" % gk_req

    # Active autopilot run for this repo → done/total from the last run-card.
    name = cache.get("name") or ""
    if name:
        prog = _load(progress_dir(home) / (name + ".json"))
        if prog and now - (prog.get("ts") or 0) <= AUTOPILOT_RUN_WINDOW_S:
            done, remaining = prog.get("done"), prog.get("remaining")
            if isinstance(done, int) and isinstance(remaining, int):
                # The card's `remaining` freezes at card time and can sit stale
                # for the whole 6 h run window ('Issues 1/2' shown after the
                # backlog emptied — 2026-07-20). The tickets cache's open count
                # (TTL 120 s) is the LIVE truth — prefer it when known, in both
                # directions (closed outside cards / new tickets filed mid-run).
                if isinstance(cache.get("open"), int):
                    remaining = cache["open"]
                color = 40 if remaining == 0 else 75    # green when backlog empty
                return "\033[38;5;%dmI %d/%d\033[0m%s" % (
                    color, done, done + remaining, skip_sfx)

    open_n = cache.get("open")
    if isinstance(open_n, int):
        # Sub-dev slice split (scope=mine): "I <active> · gk <handed-off>" — the
        # gk bucket is own tickets labeled ready-for-review, i.e. parked with the
        # gatekeeper. Rendered ALWAYS when the cache carries gk, INCLUDING gk 0: a
        # hidden zero bucket looks exactly like a broken counter (the user panicked
        # when the gatekeeper returned tickets and "gk" vanished — 2026-07-11).
        gk = cache.get("gk")
        if isinstance(gk, int):
            return ("\033[38;5;75mI %d\033[0m \033[38;5;245m· gk %d\033[0m%s"
                    % (open_n, gk, skip_sfx))
        # Full-authority (scope=core): self-describe the population (#164) —
        # a bare "I 28" hid the 45 sub-dev-owned tickets excluded from
        # it, which looks exactly like a broken counter (the same reasoning
        # that already keeps `gk` visible at 0, just for a bigger number).
        # Rendered whenever the cache actually carries a streamy count
        # (label shortened 'streamy' -> 'str', #223); falls back to the
        # plain form on a stale/older cache.
        if cache.get("scope") == "core":
            streamy = cache.get("streamy")
            if isinstance(streamy, int):
                return ("\033[38;5;75mI %d core\033[0m "
                        "\033[38;5;245m· str %d\033[0m%s"
                        % (open_n, streamy, skip_sfx))
            return "\033[38;5;75mI %d core\033[0m%s" % (open_n, skip_sfx)
        return "\033[38;5;75mI %d\033[0m%s" % (open_n, skip_sfx)
    return ""


def _fmt_tokens(n):
    """570000 -> '570K', 1500000 -> '1.5M', 999 -> '999'."""
    n = int(n)
    if n >= 1_000_000:
        v = n / 1_000_000.0
        s = ("%.1f" % v).rstrip("0").rstrip(".")
        return s + "M"
    if n >= 1000:
        return "%dK" % round(n / 1000.0)
    return str(n)


def _tail_usage_from_transcript(path, max_bytes=200_000):
    """Fallback source for context_cost_segment() when the statusline stdin
    payload carries no `context_window.current_usage` (older Claude Code, or
    a payload shape that dropped it): scan the LAST `max_bytes` of the
    session transcript for the final `message.usage` entry and return its
    (model, usage) pair. Bounded read so a huge transcript never blocks the
    prompt. Returns (None, None) on any failure or if no usage line is
    found."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop a partial line at the seek point
            data = fh.read()
    except OSError:
        return None, None
    model = usage = None
    for line in data.decode("utf-8", "replace").splitlines():
        if '"usage"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("message") if isinstance(e, dict) else None
        u = (msg or {}).get("usage") if isinstance(msg, dict) else None
        if not u:
            continue
        model = (msg or {}).get("model")
        usage = u
    return model, usage


def context_cost_segment(payload):
    """'ctx <size> ~$<cost>' — the CURRENT context size + its STEADY-STATE
    per-turn dollar cost (2026-07-25 cost-fix package, #37; pricing fixed
    same day; the ' · '/'/ťah' separator+suffix dropped, #223). Source:
    the statusline stdin payload's
    `context_window.current_usage` (the exact token breakdown of the last
    billed API call) + `model.id`; falls back to the transcript tail (see
    _tail_usage_from_transcript) when that's missing. `ctx` is
    cache_read + cache_creation tokens (the dominant, resent-every-turn
    cost) — colour-escalates on that RAW count: green <150K
    (CTX_GREEN_MAX), yellow 150-400K, red >400K (CTX_YELLOW_MAX).

    The cost estimate is deliberately `ctx * the model's cache-READ rate`,
    NOT `i*price0 + cw*price1 + cr*price2 + o*price3` (what this exact API
    call literally billed) — pricing the literal mix skews wildly right
    after a compaction or any cache-miss turn: cache_creation there is huge
    (a full context re-write) and cache_read tiny, so the real-cost formula
    priced a compaction turn at the cache-WRITE rate ($6.25/Mtok on Opus)
    instead of the cache-READ rate ($0.50/Mtok) that every ORDINARY turn
    actually pays to resend an already-cached context. Live-observed bug: gk
    showed 'ctx 175K · ~$1.10/ťah' right after a compaction; steady-state for
    175K on Opus is 175000 * 0.5 / 1e6 = ~$0.09. A one-off compaction /
    cache-miss turn must never skew the displayed estimate. Cheap and
    non-blocking by construction: no network, no `gh` — the payload is
    already in hand, and the fallback is one bounded local file read."""
    if not isinstance(payload, dict):
        return ""
    model_id = ((payload.get("model") or {}).get("id")) or ""
    cu = ((payload.get("context_window") or {}).get("current_usage")) or {}
    if not cu:
        tp = payload.get("transcript_path")
        if not tp:
            return ""
        t_model, t_usage = _tail_usage_from_transcript(tp)
        if not t_usage:
            return ""
        model_id = t_model or model_id
        cu = t_usage
    tr = burn.tier(model_id)
    price = burn.PRICE.get(tr)
    if price is None:
        return ""
    cw = int(cu.get("cache_creation_input_tokens") or 0)
    cr = int(cu.get("cache_read_input_tokens") or 0)
    ctx = cr + cw
    usd = ctx * price[2] / 1e6
    if ctx < CTX_GREEN_MAX:
        color = 40
    elif ctx < CTX_YELLOW_MAX:
        color = 220
    else:
        color = 196
    return "\033[38;5;%dmctx %s ~$%.2f\033[0m" % (color, _fmt_tokens(ctx), usd)


def questions_segment(cwd, now=None, home=None):
    """Unanswered-❓ badge, SCOPED to the session's project (user complaint
    2026-07-22: the airuleset footer showed the machine-global 14 — "custe
    hluposti"; every map entry carries the asking session's cwd, so the badge
    must attribute questions to their stream):

      - 'Q N'          — pending ❓ asked from THIS cwd (orange, label
                          shortened 'otazky' -> 'Q', #223)
      - 'Q N · inde M' — plus M pending in OTHER projects (grey)
      - 'Q inde M'     — none here, M elsewhere (all grey)
      - ''             — none anywhere (badge semantics, like `skipped`)

    Source: ~/.claude/discord-questions.json — notify.record_question adds an
    entry per ❓ ping; the watchdog drops it when the user's reply is routed
    into the asking session (job 7) or when the session got a later HUMAN
    prompt (answered at the terminal — prune_answered_questions). Entries past
    QUESTIONS_TTL_S are ignored to match the map's own prune TTL."""
    now = time.time() if now is None else now
    d = _load(_claude_dir(home) / "discord-questions.json")
    if not d:
        return ""
    here = str(cwd or "").rstrip("/")

    def _same_project(q):
        # either-direction containment: the session may run at the LAUNCH dir
        # (…/odoo) while its ❓ hook recorded a subdir (…/odoo/odoo-slovnormal)
        # — same project tree = LOCAL, never 'inde' (montalu, 2026-07-22)
        q = str(q or "").rstrip("/")
        return bool(here and q) and (
            q == here or q.startswith(here + "/") or here.startswith(q + "/"))

    local = other = 0
    for v in d.values():
        if not (isinstance(v, dict)
                and now - (v.get("ts") or 0) <= QUESTIONS_TTL_S):
            continue
        if _same_project(v.get("cwd")):
            local += 1
        else:
            other += 1
    if local and other:
        return ("\033[38;5;214mQ %d\033[0m \033[38;5;245m· inde %d\033[0m"
                % (local, other))
    if local:
        return "\033[38;5;214mQ %d\033[0m" % local
    if other:
        return "\033[38;5;245mQ inde %d\033[0m" % other
    return ""


# --------------------------------------------------------------------------- #
# #223 -- account identity: WHICH Claude account is logged in on this box,
# and WHEN its monthly subscription renews. Both already local
# (~/.claude.json), no network call, no new watchdog job (repo FREEZE).
# --------------------------------------------------------------------------- #

def _claude_json(home=None):
    path = Path(home or os.path.expanduser("~")) / ".claude.json"
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _clamp_day(year, month, day):
    last = calendar.monthrange(year, month)[1]
    return min(day, last)


def _next_renewal(created_at, now_ts):
    """Given an ISO-8601 `subscriptionCreatedAt` timestamp and the current
    epoch time, return (day, month, days_until) for the NEXT occurrence of
    that day-of-month at/after today -- the monthly subscription renewal
    anchor. Clamps the day for short months (31 -> the month's last day).
    Returns None on any unparseable input; never raises."""
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        today = datetime.fromtimestamp(now_ts, tz=timezone.utc).date()
        day = created.day
        y, m = today.year, today.month
        cand = date(y, m, _clamp_day(y, m, day))
        if cand < today:
            m += 1
            if m > 12:
                m = 1
                y += 1
            cand = date(y, m, _clamp_day(y, m, day))
        return cand.day, cand.month, (cand - today).days
    except Exception:
        return None


def subscription_segment(home=None, now=None):
    """'sub <D.M.>(<Nd>)' -- the monthly renewal anchor of the Claude
    account logged in on THIS box (~/.claude.json ->
    oauthAccount.subscriptionCreatedAt, #223). Coloured by proximity, the
    same green/yellow/red convention the usage-window segments already
    use (green far, yellow near, red on the last day). Fails SILENTLY on
    any missing/malformed input -- a statusline segment must never raise."""
    try:
        d = _claude_json(home)
        if not isinstance(d, dict):
            return ""
        created = (d.get("oauthAccount") or {}).get("subscriptionCreatedAt")
        if not created:
            return ""
        result = _next_renewal(created, time.time() if now is None else now)
        if result is None:
            return ""
        day, month, days = result
        color = 196 if days <= 0 else (220 if days <= 3 else 40)
        return "\033[38;5;%dmsub %d.%d.(%dd)\033[0m" % (color, day, month, days)
    except Exception:
        return ""


def account_email_segment(home=None):
    """The Claude account's login email (~/.claude.json ->
    oauthAccount.emailAddress, #223), rendered FAINT -- the point is
    knowing WHICH account this box is logged in as, not drawing the eye.
    Fails SILENTLY on any missing/malformed input."""
    try:
        d = _claude_json(home)
        if not isinstance(d, dict):
            return ""
        email = (d.get("oauthAccount") or {}).get("emailAddress")
        if not email or not isinstance(email, str):
            return ""
        return "\033[2m%s\033[0m" % email
    except Exception:
        return ""
