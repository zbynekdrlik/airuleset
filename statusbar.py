"""Statusline ticket segment — the LIVE obligation-ticket count for this box.

Rendered by the airuleset caveman-statusline shim on EVERY prompt render, so the
hard rules are: NEVER block, NEVER touch the network inline. The segment reads
ONE machine-local cache:

  ~/.claude/tickets-status/<cwd-key>.json   — {open, name, root, ts, ...};
      written by `airuleset.py tickets-status --refresh --cwd <dir>` (the only
      place that calls `gh`), spawned DETACHED by tickets_segment() when the
      cache is stale.

#367 (third simplification round, after #307/#313 — "JEDNO číslo, koľko
ticketov box ešte má"): the segment used to ALSO read
~/.claude/autopilot-progress/<repo>.json (written by `notify --run-card`) to
show a `run D/T` progress ratio during an active autopilot run — dropped
entirely, since it duplicated the SAME live backlog the idle form already
showed ("dve čísla o tom istom"). That cache is UNCHANGED and still written —
watchdog job 20 still reads it as goal-armed evidence — only this render
stopped consuming it.

stdlib only; every function is fail-safe (an error renders as no segment, never
a broken statusline).
"""
import calendar
import hashlib
import json
import os
import re
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

# #313 pt 4: headroom reserved for Claude Code's OWN right-edge indicators
# (the armed-'/goal' glyph "◎ /goal" is the live-evidence one -- a 176-col
# pane fully consumed by the statusline truncated it clean off the end of
# the row, twice misread as "the goal died"). Picked generously rather than
# measured to the pixel: under-reserving repeats that exact incident, while
# over-reserving costs at most one segment trimmed a little early.
STATUSLINE_RESERVE_COLS = 20

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


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


def _stream_split_sfx(cache):
    """The '· gk N' suffix — sub-dev (scope=mine) boxes only: own tickets
    already handed off to the gatekeeper. A SUBSET of the live 'I N'
    obligation count (#367) — never subtracted out of N, just a decorator
    naming which slice of N is already parked with the gatekeeper.

    #313 pt 3 REVERSES #164's "0 still renders" call: the user reads a bare
    '· gk 0' as noise on every repo where it is routinely zero — hidden at
    0, like every other zero-value bucket on this line (`skip`) already
    does — no special case any more.

    #367 dropped the SIBLING '· str M' branch this function used to also
    render (full-authority: open non-skip tickets excluded from the core
    partition because a sub-dev stream owns them, "cudzia práca, nie tohto
    boxu"). A full-authority cache no longer carries a `streamy` field at
    all (cmd_tickets_status stopped computing it), so there is nothing left
    for a second branch here to read."""
    gk = cache.get("gk")
    if isinstance(gk, int) and gk > 0:
        return " \033[38;5;245m· gk %d\033[0m" % gk
    return ""


def tickets_segment(cwd, now=None, home=None, spawn=True):
    """The GitHub-tickets statusline segment for the session at `cwd`
    (label shortened 'Issues' -> 'I', #223): 'I N' where N is the LIVE
    obligation-ticket count for THIS box — `core-quals`'s own obligation set
    on a full-authority box, `slice-quals`'s own slice on a reduced-
    authority one (#367, third simplification round after #307/#313: "I
    want to see just how many tickets this box still has left before
    everything is done" — a single live, decreasing number, never a
    ratio/total pair and never a badge the user cannot explain).

    #367 DROPPED entirely (all three had accumulated complaints — #307
    "chaos v cislach", #313 "nechapem na co vidim str 0", this ticket's own
    "naco stale vidim komplikovane run 5/12 I 12 core"):
      - the 'run D/T' autopilot-progress ratio (a SEPARATE number for the
        SAME live backlog the idle form already showed — "dve cisla o tom
        istom"). ~/.claude/autopilot-progress/<repo>.json is UNCHANGED and
        still WRITTEN by `notify --run-card` — watchdog job 20 still reads
        it as goal-armed evidence; only THIS render stopped consuming it.
      - the 'core' suffix on a full-authority box's number ("mi nic
        nepomaha").
      - '· gkq N' (open needs-gatekeeper tickets, whole repo) — these
        tickets are ALREADY inside a full-authority box's own obligation
        set (`_obligation_quals()` unions `needs-gatekeeper`), so the badge
        was duplicate decoration of a number N already includes.
      - '· str M' (tickets excluded from the core partition because a
        sub-dev stream owns them) — "cudzia praca, nie tohto boxu".

    KEPT unchanged: '· skip K' (autopilot-skip-labeled, hidden at 0, #313)
    and the separate 'Q N' question badge — neither was named in any
    complaint. A reduced-authority ('mine') cache still ALSO carries 'gk'
    (own tickets already handed off to the gatekeeper, a SUBSET of N) —
    unlike 'gkq' it was never named as redundant, so it stays.

    Consistency guard: N is computed by `cmd_tickets_status --refresh`
    (airuleset.py) calling the SAME `_obligation_quals()`/`_union_open_issues()`
    (full authority) or `_slice_quals()` (reduced authority) that
    `core-quals`/`slice-quals` themselves call for the `/goal` stop-proof —
    never a parallel derivation, so the two cannot drift apart from an
    ORDINARY code change the way they used to. (This is about the QUERY
    being shared, not an infallibility claim: `core-quals`/`slice-quals`
    additionally REFUSE an untrustworthy empty result — a broken search
    index, #181 round 4 — while this cache can still record a plain `0`
    from the same condition; that residual gap is pre-existing, not
    introduced by #367.)

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

    open_n = cache.get("open")
    if isinstance(open_n, int):
        return "\033[38;5;75mI %d\033[0m%s%s" % (
            open_n, _stream_split_sfx(cache), skip_sfx)
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


def context_cost_segment(payload, show_cost=True):
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

    `show_cost=False` renders just 'ctx <size>', dropping the '~$<cost>'
    suffix — the width-budget trim's last-resort shortening (#313 pt 4,
    `fit_statusline`), never used by a normal render.

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
    if show_cost:
        return "\033[38;5;%dmctx %s ~$%.2f\033[0m" % (color, _fmt_tokens(ctx), usd)
    return "\033[38;5;%dmctx %s\033[0m" % (color, _fmt_tokens(ctx))


def _managed_model():
    """Lazy `import airuleset` to read MANAGED_MODEL -- airuleset.py has no
    module-level `import statusbar` anywhere (only inside function bodies),
    so this is a deferred import, not a real circular one. Fails silently to
    None (renders as "no mismatch signal", i.e. treated as a match) rather
    than raise -- a statusline segment must never crash the render."""
    try:
        import airuleset
        return airuleset.MANAGED_MODEL
    except Exception:
        return None


def model_segment(payload, managed_model=None):
    """'<tier>' -- a short alias of the CURRENT session's model (#133: the
    passive replacement for the #37 model-cost signal, after #132 removed
    the restart-based watchdog jobs that used to nudge a stale session).

    Source: the statusline stdin payload's `model.id` (fallback
    `display_name`) -- the SAME field `context_cost_segment` already reads
    for pricing, never guessed from config/settings. Mapped to a tier
    (fable/opus/sonnet/haiku) via the existing `burn.tier()`; an
    empty/unrecognized model ("other") renders "" -- graceful n/a, mirroring
    `context_cost_segment`'s own unknown-tier behavior.

    Highlighted yellow when the session's tier differs from this box's
    MANAGED_MODEL default (a passive, no-ping reminder that a long-lived
    session is coasting on a different tier -- the original #37 intent);
    green when it matches, and ALSO green when the comparison itself is
    unresolvable (managed_model not given and the lazy `import airuleset`
    fails, or an explicit empty override) -- never manufacture a false
    alarm from an unresolvable comparison.

    Deliberately compares TIER, never the raw model string: MANAGED_MODEL
    carries a `[1m]` launch-flag suffix that never appears in what a
    session reports back for its own model id (the exact bug the removed
    watchdog job 23 hit, #132) -- `burn.tier()` is already suffix-agnostic
    (substring match), so comparing tiers sidesteps that regression by
    construction.

    Two failure surfaces closed by adversarial review (#133): (1) a truthy
    NON-STRING `id`/`display_name` (int/float/list/dict/bool -- all valid
    JSON scalars a hostile/malformed payload can carry) used to reach
    `burn.tier()`'s `.lower()` uncaught; coerced to `str()` first, since
    every JSON scalar stringifies safely. (2) an UNRECOGNIZED managed_model
    (burn.tier() -> "other", e.g. a future MANAGED_MODEL value with no tier
    word) used to stand in as a real tier and compare as a genuine
    mismatch -- "other" is now treated the same as an unresolvable
    comparison (never a manufactured false alarm), matching how the
    session's OWN "other" tier already renders "" above."""
    if not isinstance(payload, dict):
        return ""
    model = payload.get("model")
    if not isinstance(model, dict):
        return ""
    model_id = str(model.get("id") or model.get("display_name") or "")
    tier = burn.tier(model_id)
    if tier == "other":
        return ""
    if managed_model is None:
        managed_model = _managed_model()
    managed_tier = burn.tier(managed_model) if managed_model else None
    if managed_tier == "other":
        managed_tier = None
    color = 40 if (managed_tier is None or tier == managed_tier) else 220
    return "\033[38;5;%dm%s\033[0m" % (color, tier)


def questions_segment(cwd, now=None, home=None):
    """Unanswered-❓ badge, SCOPED to the session's project (user complaint
    2026-07-22: the airuleset footer showed the machine-global 14 — "custe
    hluposti"; every map entry carries the asking session's cwd, so the badge
    must attribute questions to their stream):

      - 'Q N'  — pending ❓ asked from THIS cwd (orange, label shortened
                 'otazky' -> 'Q', #223), hidden at 0 (badge semantics, like
                 `skipped`).
      - ''     — none here.

    #313 pt 5 REMOVED the cross-project '· inde M' / 'Q inde M' forms
    entirely (user: "na co ja potrebujem byt obtazovany ze niekde inde je
    otazka, ved to si mam riesit tam kde je ta otazka a nie tu" — a pending
    question anywhere already pings the phone via Discord regardless of
    which project's footer happens to be on screen, so a second project's
    footer repeating the count was pure noise, never actionable from there).
    Only the LOCAL count is computed/rendered now.

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
        # — same project tree = LOCAL (there is no other bucket any more).
        q = str(q or "").rstrip("/")
        return bool(here and q) and (
            q == here or q.startswith(here + "/") or here.startswith(q + "/"))

    local = 0
    for v in d.values():
        if not (isinstance(v, dict)
                and now - (v.get("ts") or 0) <= QUESTIONS_TTL_S):
            continue
        if _same_project(v.get("cwd")):
            local += 1
    if local:
        return "\033[38;5;214mQ %d\033[0m" % local
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
    oauthAccount.emailAddress, #223) -- WHICH account this box is logged in
    as. #313 pt 6 REVERSES the original "rendered FAINT" choice: the dim SGR
    attribute (\\033[2m) renders as near-invisible on many real terminals
    (user: "email je tak sivy ze je skoro necitatelny"). A plain 256-color
    foreground (250 -- a light but solid grey) keeps the same "secondary
    info, not shouting" intent while staying legible. Fails SILENTLY on any
    missing/malformed input."""
    try:
        d = _claude_json(home)
        if not isinstance(d, dict):
            return ""
        email = (d.get("oauthAccount") or {}).get("emailAddress")
        if not email or not isinstance(email, str):
            return ""
        return "\033[38;5;250m%s\033[0m" % email
    except Exception:
        return ""


def visible_len(s):
    """Character length ignoring ANSI SGR colour codes -- what a terminal
    actually renders. Used by the width-budget trim (#313 pt 4)."""
    return len(_ANSI_RE.sub("", s or ""))


def pane_width(run=None):
    """Live tmux pane width via `$TMUX_PANE` + `tmux display-message` -- the
    width-budget trim's one live input (#313 pt 4). Returns None (never
    trim) when TMUX_PANE is unset, tmux is unavailable, or the call fails
    for any reason -- a statusline segment must never guess a width it
    cannot actually measure.

    `run` is an injectable subprocess.run-alike for tests: a test must NEVER
    let this call the REAL tmux binary -- this very box's own real
    $TMUX_PANE would make the query genuinely succeed, non-deterministically
    sizing the render against whatever pane the TEST happened to be running
    inside.

    Adversarial review MINOR-4: this forks+execs `tmux` once per statusline
    render (every prompt). Accepted, not fixed here -- the shim already
    shells out several times per render (the caveman badge script, `gh`
    refreshes when the tickets cache is stale), so one more short-lived
    `tmux display-message` is consistent with the shim's existing cost
    profile rather than a new class of overhead. A short-TTL cache would
    trade a small, bounded correctness risk (a stale width across a live
    terminal resize) for a saving too small to matter here."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    runner = run or subprocess.run
    try:
        r = runner(["tmux", "display-message", "-p", "-t", pane,
                    "#{pane_width}"], capture_output=True, text=True,
                   timeout=2)
    except Exception:
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except (TypeError, ValueError):
        return None


def fit_statusline(segs, identity, cm_tag, ctx_full, ctx_short, width):
    """Join `segs` (never trimmed) with the trailing account-identity block
    and the caveman tag, using the segment separator '  ' -- dropping the
    LEAST important pieces first once the visible width would exceed
    `width` (#313 pt 4):

      1. the account-identity block (email + sub, #313 pt 6 groups them as
         ONE unit -- "email+sub are first trim candidates when narrow").
      2. the caveman tag.
      3. last resort: swap the `ctx` segment's full text (`ctx_full`, with
         its '~$<cost>' suffix) for the shorter `ctx_short` (just 'ctx
         <size>').

    `width=None` means the pane width could not be measured -- return the
    full, untrimmed line; a statusline segment must never guess. `ctx_full`
    must be one of the elements of `segs` (or absent) -- it is matched by
    identity, never by position, so the caller doesn't need to track its
    index."""
    def _join(show_identity, show_cm, ctx_text):
        parts = [ctx_text if s == ctx_full else s for s in segs]
        if show_identity and identity:
            parts.append(identity)
        if show_cm and cm_tag:
            parts.append(cm_tag)
        return "  ".join(p for p in parts if p)

    line = _join(True, True, ctx_full)
    if width is None or visible_len(line) <= width:
        return line
    line = _join(False, True, ctx_full)          # drop identity first
    if visible_len(line) <= width:
        return line
    line = _join(False, False, ctx_full)          # drop caveman tag next
    if visible_len(line) <= width:
        return line
    if ctx_full and ctx_short:                    # last: shorten ctx
        line = _join(False, False, ctx_short)
    return line
