"""watchdog/usage.py -- weekly token-usage tracking + the fable-gate budget
check (#404 point 3, module split).

WHY THIS FILE EXISTS. Extracted VERBATIM (a MOVE, not a rewrite -- unlike
#402/#403's compact.py/goal.py, which collapsed genuinely obsolete
machinery, this cluster's behavior is unchanged) from `watchdog/__init__.py`
as part of #404's per-service module split: the "usage/fable-gate tracking"
concern -- reading Anthropic's oauth/usage window state, pinging Discord once
a weekly window reaches its alert threshold, and the `fable_gate()` budget
check that gates every automatic Fable-5 escalation fleet-wide
(`model-awareness.md`) -- was one of several self-contained clusters inside
the 15k+-line watchdog module with low fan-in from the rest of the file.

Re-exported from `watchdog/__init__.py` (`from watchdog.usage import ...`,
placed after every symbol this module depends on is already defined --
`watchdog.check_usage`/`watchdog.fable_gate`/etc. keep resolving via
attribute access exactly as before) so every existing caller (`run_once()`'s
job 3, `airuleset.py`'s `cmd_fable_gate`/`cmd_watchdog`, `burn/__init__.py`,
and the test suite) needs zero changes beyond import path updates where a
caller imported these names directly rather than via the `watchdog` package
attribute. This is the FIRST facade-re-export split in this repo -- the
prior #402/#403 extractions (`watchdog/compact.py`, `watchdog/goal.py`) are
consumed via plain `watchdog.compact`/`watchdog.goal` attribute access with
no re-export block at all, since neither one had a pre-existing bare-name
consumer inside `watchdog/__init__.py` to preserve; this cluster's own
`check_usage()` call inside `run_once()` did, hence the facade.
"""

import json
import os
import re
import time

USAGE_THRESHOLD = 98              # alert when a weekly window reaches this %
USAGE_INTERVAL = 15 * 60         # min seconds between usage polls (429s hard)
_OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CC_VERSION_FALLBACK = "2.1.185"


def _cc_version():
    import subprocess
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True,
                             text=True, timeout=5).stdout
        m = re.search(r"(\d+\.\d+\.\d+)", out or "")
        return m.group(1) if m else _CC_VERSION_FALLBACK
    except Exception:
        return _CC_VERSION_FALLBACK


def _read_oauth_token():
    try:
        d = json.load(open(os.path.expanduser("~/.claude/.credentials.json")))
        return (d.get("claudeAiOauth") or {}).get("accessToken") or None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Box/account IDENTITY for the usage alert body (#212) — resolved at CALL
# TIME from module-global paths (the `_USAGE_CACHE_PATH` convention: a
# def-time default would bind the real `~/.claude.json`, so tests patch the
# global instead of passing a path through 3 call frames).
# --------------------------------------------------------------------------- #

_CLAUDE_JSON_PATH = os.path.expanduser("~/.claude.json")


def _account_email(path=None):
    """The Claude account's login email (`~/.claude.json` ->
    oauthAccount.emailAddress — the SAME field statusbar.account_email_segment
    reads), or "" on any missing/malformed input. Never raises."""
    try:
        with open(path or _CLAUDE_JSON_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            return ""
        email = (d.get("oauthAccount") or {}).get("emailAddress")
        return email if isinstance(email, str) else ""
    except Exception:
        return ""


def _local_account():
    """This box's own unix account (e.g. 'montalu') — distinct from the
    Discord OWNER a message ends up addressed to (`notify.stream_redirect`
    may redirect a stream persona to a different real person's thread)."""
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "") or ""


def _box_hostname():
    try:
        return os.uname().nodename
    except Exception:
        return ""


def _reset_bucket(resets_at):
    """Normalize an ISO-8601 `resets_at` to a stable dedup key by ROUNDING
    (never truncating) to the nearest whole minute in UTC — for use as a
    dedup key. Live-verified (airuleset#212): two `fetch_usage()` calls
    seconds apart for the SAME weekly window returned DIFFERENT `resets_at`
    strings (sub-second jitter) — comparing the raw string (the pre-fix
    `check_usage` dedup) therefore NEVER matched, and the usage alert re-fired
    every poll interval instead of once per window (11 duplicate Discord
    pings observed live inside 2.5h).

    ROUND, not truncate (adversarial-review finding): the jitter is centered
    ON the reset BOUNDARY itself (typically a whole hour), not offset from
    it — a real 456-sample replay of `~/.claude/burn-history/fleet.jsonl`
    showed every window straddles its own boundary (samples land on BOTH
    sides, e.g. some at `HH:59:59.xx`, some at `(HH+1):00:00.xx`). A bare
    TRUNCATE-to-minute (the first version of this fix) therefore still
    split roughly one poll in five into the WRONG bucket (43 of 231 replayed
    real polls for one window, instead of 1). Adding 30s before truncating
    moves the bucket boundary to `:30`, comfortably clear of the observed
    ~1s jitter, so both sides of a real straddle round to the SAME minute.
    `.astimezone(timezone.utc)` additionally stops two DIFFERENT instants at
    different UTC offsets from colliding on the same rounded string
    (unreachable today — the endpoint always returns `+00:00` — free to
    close while already touching this line).

    Falls back to a STABLE, never-time-varying sentinel — `"raw:<value>"`,
    NEVER bare `None` — on any missing/unparseable input: `check_usage`
    uses `None` in `state["usage"]["alerted_window"]` to mean "not currently
    alerted for any window" (both the fresh-state default and the
    below-threshold re-arm), so returning `None` here for a genuinely
    missing `resets_at` would make the very FIRST real alert compare
    `None == None` and silently never fire. A fixed sentinel per distinct
    bad input still fires once and then correctly dedupes on repeat polls
    with the same missing/malformed value."""
    try:
        from datetime import datetime, timedelta, timezone
        dt = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
        dt = dt.astimezone(timezone.utc) + timedelta(seconds=30)
        return dt.strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return "raw:%s" % (resets_at,)


def fetch_usage():
    """GET Anthropic's oauth/usage window state, or None on any error / 429. The
    `claude-code` User-Agent is REQUIRED (without it the endpoint 429s even harder)."""
    import urllib.request
    tok = _read_oauth_token()
    if not tok:
        return None
    req = urllib.request.Request(_OAUTH_USAGE_URL, headers={
        "Authorization": "Bearer " + tok,
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/" + _cc_version(),
        "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=12).read())
    except Exception:
        return None


def weekly_percent(usage):
    """(percent, resets_at, label) of the HIGHEST active WEEKLY window in the
    oauth/usage payload, or None — ANY weekly window hitting the cap stalls work."""
    best = None
    for lim in (usage or {}).get("limits", []):
        if lim.get("group") != "weekly":
            continue
        pct = lim.get("percent")
        if pct is None:
            continue
        label = "týždenný limit"
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        if model:
            label = "týždenný limit (%s)" % model
        if best is None or pct > best[0]:
            best = (float(pct), lim.get("resets_at"), label)
    return best


# --------------------------------------------------------------------------- #
# Usage cache for the statusline. The oauth/usage endpoint 429s hard, so the
# statusline can NOT poll it per render. The watchdog already fetches it every
# ~15 min (check_usage) — piggyback a tiny cache of the flattened windows so the
# statusline can show a PER-MODEL window (e.g. Fable's weekly) that CC's statusLine
# stdin `rate_limits` does not expose (stdin only carries the shared 5h + weekly).
# NB the 5-hour "session" window is account-wide (scope=null) — there is NO
# per-model 5h; the only per-model split is the weekly (`weekly_scoped`).
# --------------------------------------------------------------------------- #

_USAGE_CACHE_PATH = os.path.expanduser("~/.claude/airuleset-usage-cache.json")


def usage_windows(usage):
    """Flatten the oauth/usage limits[] into simple dicts: kind, group, percent,
    model (display_name or None for the shared windows), resets_at, is_active."""
    out = []
    for lim in (usage or {}).get("limits", []):
        pct = lim.get("percent")
        if pct is None:
            continue
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        out.append({"kind": lim.get("kind"), "group": lim.get("group"),
                    "percent": int(pct), "model": model,
                    "resets_at": lim.get("resets_at"),
                    "is_active": bool(lim.get("is_active"))})
    return out


def write_usage_cache(usage, now, path=None):
    """Best-effort: persist {ts, account_email, windows} so the statusline
    renders a per-model window without hitting the 429-prone endpoint, AND
    (#212) so fleet usage reporting can tell WHICH Anthropic subscription a
    box's percentages belong to — the original gap this cache existed
    without: "impossible to reconcile without knowing the box→account
    mapping". `account_email` is the SAME `~/.claude.json` field
    `statusbar.account_email_segment` already renders; "" when unreadable
    (never blocks the write). Never raises. `path` defaults to the module
    global resolved AT CALL TIME (so tests can patch _USAGE_CACHE_PATH to
    a tmp file — a def-time default would bind the real ~/.claude path and
    clobber the user's live cache during the suite)."""
    path = path or _USAGE_CACHE_PATH
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": int(now), "account_email": _account_email(),
                       "windows": usage_windows(usage)}, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _human_reset(iso):
    if not iso:
        return "?"
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%d.%m. %H:%M")
    except Exception:
        return str(iso)[:16]


# --------------------------------------------------------------------------- #
# Fable budget gate — `airuleset.py fable-gate`. Under the 2026-08-13
# model-tiering policy (Opus 5 banned, #440) the gate guards the DEFAULT
# judgment layer: every automatic Fable judgment dispatch is BUDGET-GATED so
# the 2026-07-01 Fable-everywhere incident (limits tripped mid-work, user's
# work stopped) can never repeat — Fable fires only while its weekly window
# (and the shared weekly) has headroom. Reads the same usage cache the
# watchdog writes every ~15 min (never hits the 429-prone endpoint).
# FAIL-SAFE: missing/stale/empty cache → CLOSED (the work runs on Opus 4.8,
# claude-opus-4-8), never a blind Fable burn.
# --------------------------------------------------------------------------- #

FABLE_GATE_PCT = 80            # default: escalate only below 80% used (leaves the
                               # user headroom for their own manual /model Fable)
FABLE_GATE_MAX_AGE = 6 * 3600  # cache older than this = unknown → CLOSED (same
                               # staleness bound the statusline uses)


def fable_gate(now=None, path=None, threshold=None):
    """(open, reason) — may automatic HARD-task escalation dispatch to Fable NOW?

    OPEN  ⇔ the cache is fresh AND every gating window is below `threshold`%:
      - the Fable-scoped weekly window (the binding one under heavy Fable use), and
      - the shared account weekly (Fable burn counts there too).
    The 5h session window deliberately does NOT gate (it resets within hours and
    would keep the gate closed exactly when the user works most; the incident
    being prevented was the WEEKLY trip). A fresh cache with NO Fable-scoped
    window gates on the shared weekly alone; a fresh cache with NO weekly windows
    at all is unknown → CLOSED."""
    now = now if now is not None else time.time()
    if threshold is None:
        try:
            threshold = int(os.environ.get("AIRULESET_FABLE_GATE_PCT", FABLE_GATE_PCT))
        except ValueError:
            threshold = FABLE_GATE_PCT
    path = path or _USAGE_CACHE_PATH
    # The ENTIRE evaluation is fail-safe: any unexpected shape/type in the cache
    # (list top-level, string ts, bool/str percents, garbage windows) must return
    # CLOSED, never raise — a caller unpacking (ok, reason) on a corrupt cache
    # crashing IS a gate failure (review finding F3).
    try:
        with open(path) as f:
            cache = json.load(f)
        # Clock-skew guard (F1): a FUTURE ts makes age negative, which a plain
        # `age > MAX` check calls "fresh" FOREVER — fail-open on frozen numbers.
        # Any age outside [0, MAX] is unknown → stale.
        age = now - float(cache.get("ts") or 0)
        if not (0 <= age <= FABLE_GATE_MAX_AGE):
            return False, "usage cache stale (ts %dh off) — fail-safe CLOSED, use opus" % (
                abs(age) // 3600)
        # Window selection (F2): gate ONLY on WEEKLY windows (a per-model session/
        # surface window must neither gate nor mask), and across MULTIPLE matching
        # windows take the MAX percent — the binding one decides.
        fable_pct = shared_pct = None
        for w in cache.get("windows") or []:
            pct = w.get("percent")
            if isinstance(pct, bool) or not isinstance(pct, (int, float)):
                continue                    # bool/str/None percent = unknown, never 0%
            if w.get("group") != "weekly":
                continue
            model = w.get("model") or ""
            if "fable" in str(model).lower():
                fable_pct = pct if fable_pct is None else max(fable_pct, pct)
            elif not model:
                shared_pct = pct if shared_pct is None else max(shared_pct, pct)
        if fable_pct is None and shared_pct is None:
            return False, "no weekly window in cache — fail-safe CLOSED, use opus"
        parts = []
        for label, pct in (("fable", fable_pct), ("weekly", shared_pct)):
            if pct is None:
                continue
            parts.append("%s=%d%%" % (label, pct))
            if pct >= threshold:
                return False, ("%s window at %d%% (>= %d%% gate) — CLOSED, use opus"
                               % (label, pct, threshold))
        return True, " ".join(parts) + " (< %d%% gate)" % threshold
    except FileNotFoundError:
        return False, "no usage cache (%s) — fail-safe CLOSED, use opus" % path
    except Exception as e:
        return False, "unreadable/corrupt usage cache (%s: %s) — fail-safe CLOSED, use opus" % (
            type(e).__name__, e)


def check_usage(now, state, send_fn, fetch=None, owner=None, dry_run=False,
                threshold=USAGE_THRESHOLD, interval=USAGE_INTERVAL):
    """Rate-limited weekly-usage poll: at most once per `interval`, and an alert
    ONCE per reset window when a weekly limit reaches `threshold`%. Mutates
    state['usage']; returns a log line or ''. Best-effort (never raises).

    The alert body carries this box's ACCOUNT IDENTITY (airuleset#212: a bare
    "⚠️ Tokeny — týždenný limit na 99%" is undecodable on a phone with no
    terminal context — WHICH account, WHICH box?) — the account email
    (`_account_email()`), this box's hostname/unix-account
    (`_box_hostname()`/`_local_account()`), and the Discord `owner` the alert
    is actually addressed to. `owner` is trusted AS GIVEN by the caller — by
    the time `run_once()` calls this, it has already been passed through
    `notify.stream_redirect()`, so a stream persona (montalu/simap/…) shows
    the REAL person it was routed to, not its own unix account name.

    Dedup keys off `_reset_bucket(resets_at)`, never the raw `resets_at`
    string — the raw string carries SUB-SECOND jitter from the Anthropic
    API (live-verified on montalu@subdev: two polls of the SAME weekly
    window, 3s apart, returned two DIFFERENT `resets_at` strings), which
    used to defeat the "already alerted for THIS window" check on every
    single 15-minute poll — 11 duplicate Discord pings observed live across
    one incident window (98%→99%, ~05:15–07:47)."""
    fetch = fetch or fetch_usage
    u = state.get("usage") or {}
    if (now - u.get("last_check", 0)) < interval:
        return ""
    u["last_check"] = int(now)
    state["usage"] = u
    data = fetch()
    if not data:
        return ""                          # 429 / error → try again next interval
    write_usage_cache(data, now)           # feed the statusline's per-model window
    wk = weekly_percent(data)
    if not wk:
        return ""
    pct, resets_at, label = wk
    bucket = _reset_bucket(resets_at)
    if pct < threshold:
        u["alerted_window"] = None         # back below threshold → re-arm the dedup
        state["usage"] = u
        return ""
    if u.get("alerted_window") == bucket:
        return ""                          # already alerted for THIS reset window
    u["alerted_window"] = bucket
    state["usage"] = u
    send_fn("⚠️ **Tokeny — %s na %d%%**\n"
            "> Účet: %s · Box: %s/%s → adresát: %s.\n"
            "> Práca sa môže čoskoro zastaviť "
            "(vyčerpaný týždenný limit). Reset: %s."
            % (label, int(pct), _account_email() or "?", _box_hostname() or "?",
               _local_account() or "?", owner or "?", _human_reset(resets_at)),
            owner=owner, dedup_key="usage:%s:%d" % (bucket, int(pct)), dry_run=dry_run)
    return "usage-alert %s %d%%" % (label, int(pct))
