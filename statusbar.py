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
STALE_CACHE_MAX_AGE_S = 7 * 86400   # #689: sweep a cache entry unrefreshed this long
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


def obligation_count(cwd, home=None):
    """The LIVE obligation-ticket count for `cwd`, read from the SAME
    machine-local tickets-status cache `tickets_segment` renders. Returns
    `(open: int | None, ts: float | None)`: the cache's `open` field (which
    is, by construction, `core-quals`/`slice-quals`'s own /goal stop-proof
    count — `len(mine) - gk` for a reduced-authority stream) plus the cache
    write time. `(None, None)` when the cache is absent/unparseable or
    carries no int `open`. Reads only — never spawns a refresh, never
    touches the network. Callers: watchdog `goal_dark_watch` (#459 — its
    death-vs-achievement discriminator, `open > 0` = the goal's own
    SLICE-EMPTY stop condition is NOT met, a genuine stall) AND the #618
    backlog fetch; each caller applies its OWN freshness gate on `ts`."""
    if not cwd:
        return None, None
    cache = _load(cache_dir(home) / (cwd_key(cwd) + ".json"))
    if not isinstance(cache, dict):
        return None, None
    open_n = cache.get("open")
    if not isinstance(open_n, int):
        return None, None
    ts = cache.get("ts")
    return open_n, (ts if isinstance(ts, (int, float)) else None)


def obligation_partition(cwd, home=None):
    """#693 — the FULL I/U/W/gk partition for `cwd`, off the SAME per-cwd
    tickets-status cache file `obligation_count` reads (never a parallel
    derivation — the #367 lesson). Returns `(workable, user_waiting,
    ops_wait, gk, ts)`: each count is the cache's int or None (absent field,
    non-int, or a failed refresh recorded as null; `gk` is also None on a
    full-authority entry, which has no such bucket by construction), `ts` is
    the cache write time or None. All-None when the cache file is absent or
    unparseable. Reads only — never spawns a refresh, never touches the
    network. Callers: the watchdog lane give-up cause classifier
    (`watchdog/goal.py::_lane_giveup_cause`), which applies its OWN freshness
    gate on `ts` — a stale partition classifies as `unknown`, never a guess —
    and the #797 U-freshness reconcile seams (`airuleset._watchdog_u_fetch` /
    `watchdog/u_freshness._default_u_fetch`), which read the `user_waiting` +
    `ts` fields (the production seam is `_watchdog_u_fetch`, wired into run_once;
    `_default_u_fetch` is the rider's direct-call fallback — do NOT delete it as
    'duplicate', it is the seam's default when the orchestrator is called without
    `u_fetch`)."""
    if not cwd:
        return None, None, None, None, None
    cache = _load(cache_dir(home) / (cwd_key(cwd) + ".json"))
    if not isinstance(cache, dict):
        return None, None, None, None, None

    def _i(key):
        v = cache.get(key)
        return v if isinstance(v, int) else None

    ts = cache.get("ts")
    return (_i("open"), _i("user_waiting"), _i("ops_wait"), _i("gk"),
            ts if isinstance(ts, (int, float)) else None)


def _spawn_refresh(cwd, home=None):
    """Kick a DETACHED `tickets-status --refresh` for `cwd` — guarded by a marker
    mtime so a burst of statusline renders / watchdog sweeps (#618) spawns at most one per SPAWN_GUARD_S."""
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


def sweep_stale_cache(home=None, now=None, max_age_s=STALE_CACHE_MAX_AGE_S):
    """#689 hygiene: delete tickets-status cache entries for worktrees that no
    longer exist. Returns the list of removed filenames.

    Deletes a `<cwd-key>.json` cache entry IFF EITHER
      (a) its `root` is a non-empty path that no longer exists on disk — a
          removed worktree, the primary case (dead `agent-*` roots), OR
      (b) its `ts` is older than `max_age_s` (a conservative window, default 7
          days) — a per-cwd entry no refresh has re-written that long, harmless
          to drop (it is recreated on the next render of that cwd).

    Best-effort: NEVER raises (the statusline / a refresh must never break over
    hygiene). Skips `.spawn-*` guard files and anything not ending in `.json`,
    and skips symlinks (a cache entry is never legitimately a symlink — never
    follow one to read/unlink its target). Per-`$HOME` directory, so there is
    NO cross-user concern (unlike the #687 shared dedup store). An UNPARSEABLE
    entry is deliberately LEFT (no readable root/ts to judge; rare, low-harm,
    overwritten on the next refresh of that cwd). Accepted residual: a live root
    that is temporarily INACCESSIBLE (EACCES / an unmounted volume) reads as
    missing via `os.path.exists` and gets swept — low-harm, the entry is
    recreated on that cwd's next refresh."""
    removed = []
    now = time.time() if now is None else now
    d = cache_dir(home)
    try:
        names = os.listdir(d)
    except OSError:
        return removed
    for name in names:
        if not name.endswith(".json"):
            continue                      # `.spawn-*` guards + non-cache files
        path = d / name
        try:
            if path.is_symlink():
                continue                  # never follow/unlink a symlinked entry
            data = _load(path)
        except OSError:
            continue
        if not isinstance(data, dict):
            continue                      # unparseable — leave it (safe direction)
        dead = False
        root = data.get("root")
        if isinstance(root, str) and root and not os.path.exists(root):
            dead = True
        ts = data.get("ts")
        if isinstance(ts, (int, float)) and (now - ts) > max_age_s:
            dead = True
        if not dead:
            continue
        try:
            path.unlink()
            removed.append(name)
        except OSError:
            continue                      # a concurrent sweep / perms — skip it
    return removed


def _stream_split_sfx(cache):
    """The '· gk N' suffix — sub-dev (scope=mine) boxes only: own tickets
    already handed off to the gatekeeper. #391 (2026-08-11) REVERSES the
    prior "subset of I N" relationship for this reduced-authority path: I N
    now counts own UNHANDLED work (len(mine) - gk), so gk is a SEPARATE
    parked-count shown alongside I N, no longer a slice carved out of it —
    a sub-dev's responsibility for a ticket ends the moment it is handed
    off, so a handed-off ticket stops being part of I N at all.

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


def _user_waiting_sfx(cache, ping_count=0):
    """The '· U N' suffix — everything WAITING ON THE OWNER, split OUT of the
    workable `I N` so `I` = only what THIS box must action and `U` = what is on
    the owner (#468 for answer/decision; #512 consolidates in needs-acceptance
    AND the standalone `Q` badge; #601 adds needs-owner-action — an owner
    physical/manual step). `N` = the label-based `user_waiting`
    (needs-answer/needs-decision/needs-acceptance/needs-owner-action, from the
    cache) PLUS
    `ping_count` — the live count of ticketless ❓ pings the caller computed via
    `question_ping_count` (the merged-in `Q`, deduped so a ping tied to a labeled
    ticket is not counted twice). Orange-adjacent (208), distinct from the grey
    gk/skip exclusion badges, hidden at 0. Rendered on BOTH scopes.

    Schema-compatible: a stale/legacy cache with no `user_waiting` key reads the
    label part as 0; a valid int adds to `ping_count`. Hidden only when the TOTAL
    is 0 — so a fresh ❓ ping shows `U 1` even with zero user-waiting labels."""
    u = cache.get("user_waiting")
    total = (u if isinstance(u, int) else 0) + \
            (ping_count if isinstance(ping_count, int) else 0)
    if total > 0:
        return " \033[38;5;208m· U %d\033[0m" % total
    return ""


def _ops_wait_sfx(cache):
    """The '· W N' suffix — tickets parked on an external event / evidence
    (`ops-wait`), split OUT of the workable `I N` like the #468 `U N` bucket but
    for a DIFFERENT parking reason: blocked on an external event / evidence with
    no dispatchable code lane and no automated completion signal, not on the
    user's answer (#510). Its OWN bucket (pattern `skip K` / `U N`), grey (245,
    the exclusion-badge family — not this box's urgent workable), hidden at 0.

    Schema-compatible: a stale/legacy cache written before #510 carries no
    `ops_wait` key, so `.get(...)` is None → hidden (never a crash, never W 0)."""
    w = cache.get("ops_wait")
    if isinstance(w, int) and w > 0:
        return " \033[38;5;245m· W %d\033[0m" % w
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
    (own tickets already handed off to the gatekeeper) — unlike 'gkq' it
    was never named as redundant, so it stays. #391 (2026-08-11) reverses
    what 'gk' means RELATIVE to N on this path: N used to be the FULL
    slice (handed-off included, gk a subset of it); N is now own
    UNHANDLED work only (len(mine) - gk), so 'gk' is the parked count
    shown NEXT TO N, no longer a slice carved out of it — see
    `_stream_split_sfx`'s own docstring for the full reasoning.

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

    # #512: the standalone `Q` ❓ badge folded into `U N`, read LIVE here (as the
    # old `Q` badge was) so a fresh ❓ shows immediately. The old badge was ALSO
    # gh-INDEPENDENT — it rendered from the local question map even with NO
    # tickets cache or a gh error. Preserve that (both #512 adversarial reviews
    # caught its loss): when there is no renderable `I` count — a missing/cold
    # cache, or `open` not an int on a gh error / non-repo cwd / SliceUnresolved
    # (`cmd_tickets_status` writes open=user_waiting=ops_wait=None together on
    # any failure) — still surface a pending ping as a STANDALONE `U N`, so a
    # question the user must answer is never invisible in the footer.
    ping_count = question_ping_count(cwd, home=home)
    if not isinstance(cache, dict) or not isinstance(cache.get("open"), int):
        u = cache.get("user_waiting") if isinstance(cache, dict) else None
        total = (u if isinstance(u, int) else 0) + \
                (ping_count if isinstance(ping_count, int) else 0)
        return "\033[38;5;208mU %d\033[0m" % total if total > 0 else ""

    # Skipped bucket (2026-07-16): tickets labeled autopilot-skip. An EXCLUSION
    # count, not a partition of the visible tickets (unlike gk, whose zero must
    # stay visible) — so it renders only when >= 1 and stays off the line at 0.
    # Label shortened 'skipped' -> 'skip' (#223).
    skipped = cache.get("skipped")
    skip_sfx = (" \033[38;5;245m· skip %d\033[0m" % skipped
                if isinstance(skipped, int) and skipped > 0 else "")

    return "\033[38;5;75mI %d\033[0m%s%s%s%s" % (
        cache["open"], _user_waiting_sfx(cache, ping_count), _ops_wait_sfx(cache),
        _stream_split_sfx(cache), skip_sfx)


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


# #512: a ticket reference inside a ❓ ping's own text — a `#<digits>` token.
# A ping whose question REFERENCES a ticket is assumed already counted in the
# label-based `user_waiting` (that ticket carries needs-answer/needs-decision/
# needs-acceptance — the ask-and-continue flow labels the ticket AND pings), so
# it must NOT be double-counted in the `U N` ping-merge. A ping with NO ticket
# reference is a session-level question that no label counts → +1 to U.
_TICKET_REF_RE = re.compile(r"#\d+")


def _question_same_project(here, q):
    """Either-direction cwd containment — the session may run at the LAUNCH dir
    (…/odoo) while its ❓ hook recorded a subdir (…/odoo/odoo-slovnormal), same
    project tree either way (montalu 2026-07-22). `here`/`q` are the caller's
    session cwd and the ping entry's recorded cwd. Extracted from the old
    `questions_segment` closure (#512) so the footer ping-merge and
    `core-quals --waiting`'s own ping listing scope pings identically."""
    here = str(here or "").rstrip("/")
    q = str(q or "").rstrip("/")
    return bool(here and q) and (
        q == here or q.startswith(here + "/") or here.startswith(q + "/"))


def ticketless_question_pings(cwd, home=None):
    """The unanswered ❓ pings SCOPED to the session's project (`cwd`) that carry
    NO ticket reference (`#N`) in their own question text — the session-level
    questions that fold into `U N` (#512: the standalone `Q` segment was removed;
    its count merges into `U`, "waiting on the OWNER"). Returns a list of the map
    ENTRIES (dicts), so both the footer count and `core-quals --waiting`'s ping
    listing use the SAME derivation.

    Dedup rationale: a ping whose text references a ticket is already counted in
    the label-based `user_waiting` (its ticket carries a USER_WAITING label), so
    it is EXCLUDED here — never double-counted.

    Source (unchanged): ~/.claude/discord-questions.json — notify.record_question
    adds an entry per ❓ ping; the watchdog drops it when answered/routed. #368:
    no age filter (an entry still in the map is still open, re-asked daily). The
    map + re-ask jobs are UNTOUCHED by #512 — this is render/count only."""
    d = _load(_claude_dir(home) / "discord-questions.json")
    if not d:
        return []
    here = str(cwd or "").rstrip("/")
    out = []
    for v in d.values():
        if not isinstance(v, dict):
            continue
        if not _question_same_project(here, v.get("cwd")):
            continue
        text = "%s %s" % (v.get("block") or "", v.get("question") or "")
        if _TICKET_REF_RE.search(text):
            continue   # references a ticket -> already in the label U count
        out.append(v)
    return out


def question_ping_count(cwd, home=None):
    """The number of ticketless ❓ pings scoped to `cwd` (#512) — folded into the
    footer's `U N` count. See `ticketless_question_pings` for the derivation."""
    return len(ticketless_question_pings(cwd, home=home))


def question_map_ticket_refs(cwd, home=None):
    """The SET of issue numbers (int) referenced by any ❓ ping recorded in
    ~/.claude/discord-questions.json AND SCOPED to `cwd`'s project — the
    DELIVERED-question signal for the #527 `--waiting` no-question check (#539).
    A ping whose text references `#N` is a question DELIVERED to the owner about
    `#N` (`notify.record_question` writes an entry per phone ping; #368: an
    unanswered entry STAYS in the map, re-asked daily), so its target `#N` is
    covered.

    **Scoped by project, exactly like the sibling `ticketless_question_pings`**
    (`_question_same_project(cwd, entry.cwd)`) — a MUST, not a nicety: the map
    is shared across every repo a box works, and low issue numbers COLLIDE
    across repos (airuleset #300 == odoo-erp #300). Without scoping, a
    multi-repo supervisor box (dev1/gatekeeper) would clear/route a ticket in
    repo A off an unrelated ping about `#300` in repo B — reintroducing the very
    "U > 0 with no real question here" invariant violation this ticket exists to
    kill (#539 review MAJOR-1). `cwd` is the caller's repo root; an empty cwd
    matches nothing (returns an empty set on a readable map — the safe degrade).

    The return distinguishes ABSENT from UNREADABLE, which the caller's fail-safe
    (`cli_quals._acceptance_present_set`/`_no_question_flagged`) depends on — a
    plain `_load` (which collapses both to None) is deliberately NOT reused here:

      - file ABSENT (no ❓ ever pinged from this box) -> **empty set**: a
        readable "nothing delivered" state, so a `U` member with no ref is a
        genuine no-question candidate (checked further via the comment fallback).
      - file present but CORRUPT / not a dict / unreadable -> **None**: the
        caller then tags NOTHING (never a false accusation off an unreadable
        map, "nikdy falošný", #539).
      - file valid -> the set of every `#N` in any SAME-PROJECT entry's
        block+question text (the SAME `_TICKET_REF_RE` the ticketless-ping dedup
        uses, run in the opposite direction — here we COLLECT the numbers)."""
    path = _claude_dir(home) / "discord-questions.json"
    if not path.exists():
        return set()
    try:
        with open(path, encoding="utf-8") as h:
            d = json.load(h)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    here = str(cwd or "").rstrip("/")
    refs = set()
    for v in d.values():
        if not isinstance(v, dict):
            continue
        if not _question_same_project(here, v.get("cwd")):
            continue                         # #539 review MAJOR-1: cross-repo scope
        text = "%s %s" % (v.get("block") or "", v.get("question") or "")
        for m in _TICKET_REF_RE.finditer(text):
            try:
                refs.add(int(m.group(0)[1:]))   # "#4" -> 4
            except ValueError:
                continue
    return refs


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
