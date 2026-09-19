"""cli_gh_rate — fleet-wide `gh` rate-guard (#1040).

Origin: odoo-erp issue 7308 (15.9.2026, gk FLOW window). The owner-token
GitHub GraphQL budget (5000/h) was exhausted by N independent pollers with
NO shared budget — 5 per-PR merge waiters (9 `--json`/GraphQL calls each,
every 60 s) + watchers + review lanes — one blind hour, the release chain
stalled, comments could not be posted. The gk-local merge-queue DAEMON that
reduces the poller COUNT is odoo-erp issue 7308's own infra; THIS is the
FLEET-wide guard that makes ANY gh consumer on ANY managed box budget-aware.

Design (see #1040 design comment):
  * ONE shared reading of `gh api rate_limit` (a GitHub endpoint that does
    NOT count against the limit), cached per box for 60 s in
    ``~/.claude/gh-rate/status.json`` — token-free (rate_limit returns no
    token; nothing here ever logs env/argv).
  * ``remaining_pct(resource)`` / ``backoff_seconds(resource)`` — 0 at/above
    20 % remaining, exponential below, capped.
  * A once-per-episode alert latch: two consecutive exhausted (< 20 %)
    readings raise ONE alert (a journal line + a ``status`` row + a
    report-only conformance dimension — never an owner ping, per issue 1032
    / issue 693); the latch resets when remaining climbs back above 50 %.
  * A ``gh`` PATH shim (installed by ``cmd_install``) throttles only a
    background POLL shape carrying ``AIRULESET_GH_POLLER=1``; a human /
    interactive call (no env) and EVERY write action (comment/edit/merge/
    create/reopen/close/api -X≠GET) pass straight through with zero delay.
  * FAIL-OPEN everywhere: any gh / parse / cache error → no throttle. A
    rate-guard must never be able to BLOCK a real action.

stdlib only (repo policy). Pure logic + wrapper-script template + a tiny
``--wrapper-backoff`` ``__main__`` entry the shim calls (imports only this
module, so its startup stays cheap on the poll hot path).
"""
import json
import os
import subprocess
import sys
import time


def _ghql_mod():
    """#1087: import the GraphQL rateLimit-object reader LAZILY. Only the
    cache-REFRESH path (`read_status` / `_update_alert_latch`) needs it; the
    call-accounting `--record` and the zero-budget `--zero-budget-line` shim
    entries (spawned on the hot path — every `gh` call) must stay cheap, so they
    never pay the import."""
    import cli_gh_rate_graphql as _ghql
    return _ghql

# --------------------------------------------------------------------------- #
# Tunables (module constants so tests reference them, not magic numbers).
# --------------------------------------------------------------------------- #
CACHE_TTL_S = 180                # #1055 P2: 60->180. The rate bucket is HOURLY, so a
#                                # per-sweep (60 s) refresh over-samples it 3x for no gain
#                                # -- one `gh api rate_limit` read every 3 sweeps is ample
#                                # to catch a low-budget episode (the alert latch tolerates a
#                                # 2-min-late read), and it removes ~1 subprocess/sweep from
#                                # every box's budget. (`gh api rate_limit` is itself un-metered.)
LOW_PCT = 20.0                   # below this remaining %, a poll backs off
RESET_PCT = 50.0                 # above this, the once-alert latch re-arms
BACKOFF_CAP_S = 60               # hard cap on a single backoff SLEEP. Watchdog pure-read
                                 # pollers are HELD (skipped), not slept, when low (the
                                 # #1040/#1041 composition), so this sleep bounds only the
                                 # few-call consumers (goal-lane riders, hooks, ad-hoc). A
                                 # cold-cache shim call additionally pays up to
                                 # 2 * _FETCH_TIMEOUT_S for the one-off refresh before
                                 # the sleep (#1040 review-1 MINOR-5; #1052 added the
                                 # GraphQL-object probe, a second read on the SAME
                                 # refresh path); in the watchdog the per-sweep
                                 # gh_rate_fetch keeps the cache warm, so a rider's own
                                 # call rarely refreshes.
_FETCH_TIMEOUT_S = 15            # a single `gh api rate_limit` / `graphql rateLimit` read
_RESOURCES = ("core", "graphql")

# Env markers.
POLLER_ENV = "AIRULESET_GH_POLLER"      # set by the watchdog: this call is a background poll
INTERNAL_ENV = "AIRULESET_GH_RATE_INTERNAL"  # set by the refresh: never recurse/throttle


def gh_rate_dir():
    return os.path.join(os.path.expanduser("~"), ".claude", "gh-rate")


def status_path():
    """Overridable in tests — the per-box cache file."""
    return os.path.join(gh_rate_dir(), "status.json")


def journal_path():
    return os.path.join(gh_rate_dir(), "gh-rate.log")


def throttle_marker_path():
    """A tiny presence marker: exists iff throttling is currently active (some
    resource < 20 %). The shim tests it with a single cheap ``[ -e ]`` so the
    HEALTHY common case never spawns python at all — python (the precise
    write-vs-poll classify + backoff) runs only during an actual low-budget
    episode. Correctness never depends on it: a stale-present marker just makes
    the shim run python, which returns the correct 0; a stale-absent marker
    fails open (no throttle)."""
    return os.path.join(gh_rate_dir(), "throttle-active")


def _set_throttle_marker(active):
    """Create/remove the throttle marker (best-effort, never raises)."""
    path = throttle_marker_path()
    try:
        if active:
            os.makedirs(gh_rate_dir(), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("1\n")
        elif os.path.exists(path):
            os.remove(path)
    except OSError as e:
        _diag("throttle-marker", e)


def shim_path():
    """The managed gh shim's install location. On this fleet gh itself lives at
    ~/.local/bin/gh, so the shim WRAPS IT IN PLACE there (the real gh is
    relocated to ``upstream_path`` at install), which needs no PATH surgery and
    is hit in every context ~/.local/bin/gh already resolves."""
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "gh")


def upstream_path():
    """Where the real gh is relocated to when the shim wraps it in place."""
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "gh-upstream")


def app_shim_path():
    """#1087 L1b: where the odoo-erp App-token shim is relocated to when we
    CHAIN over it (never wrap it in place — #1051). On a stream box the live
    chain is ``gh`` (our wrapper) -> ``gh-app-shim`` (the App shim, mints the
    installation token) -> the real gh binary. Basename ``gh-app-shim`` is NOT a
    ``gh`` on PATH, so a ``gh`` PATH walk never re-enters it (the loop breaker)."""
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "gh-app-shim")


def _is_app_token_shim(path):
    """#1087 L1b: True iff ``path`` is the odoo-erp issue-888 App-token shim
    (``gh-app-gh-shim.sh``) — the ONE foreign wrapper we know how to chain. It
    is recognised by its header citing odoo-erp issue 3281/3282 OR by exporting
    ``GH_TOKEN`` from ``gh-app-token`` (a ``cat`` of ``gh-app-tokens/primary``).
    Never matches our own wrapper (checked first) or a real binary. Read only a
    small text head; any error => not the App shim (fail-safe)."""
    try:
        if _is_our_wrapper(path):
            return False
        with open(path, "rb") as fh:
            if fh.read(2) != b"#!":
                return False        # a real binary / non-script is never it
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except (OSError, ValueError):
        return False
    return (("3281" in head or "3282" in head)
            or "gh-app-token" in head or "gh-app-tokens" in head)


def is_app_shim_box():
    """#1087 L1b: True iff this box's gh chain runs through the App-token shim
    (either still at ``~/.local/bin/gh`` pre-chain, or moved to
    ``gh-app-shim`` post-chain). On such a box the installation ``rate_limit``
    endpoint LIES (reports a fresh 5000 bucket while real calls 403), so the
    budget must come from response headers + observed 403s, never a probe."""
    return (_is_app_token_shim(app_shim_path())
            or _is_app_token_shim(shim_path()))


def _is_our_wrapper(path):
    """True iff `path` is our managed shim (carries WRAPPER_SENTINEL), read
    defensively (a real gh binary is large/binary — read only a small text
    head, treat any error as "not ours")."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return WRAPPER_SENTINEL in fh.read(4096)
    except (OSError, ValueError):
        return False


def _classify_local_gh(path):
    """#1051: classify whatever sits at ``~/.local/bin/gh`` BEFORE the installer
    touches it, so a FOREIGN wrapper SCRIPT is never copy-and-wrapped into an
    exec-loop. Returns one of:

      * ``"absent"``  — nothing at ``path``;
      * ``"ours"``    — our managed rate-guard shim (WRAPPER_SENTINEL);
      * ``"foreign"`` — a ``#!``-script that is NOT ours (the odoo-erp issue-888
        app-token shim ``gh-app-gh-shim.sh``, or any other text wrapper). Both
        it and our shim resolve "the first non-self gh on PATH", so wrapping our
        shim over it and copying it to ``gh-upstream`` makes each resolve to the
        other → the #1051 infinite exec-loop. Such a script is CHAINED by its
        owner (odoo-erp issue 3281), never copy-and-wrapped by us;
      * ``"binary"``  — a real gh executable (ELF / not a ``#!``-script): safe to
        wrap in place.

    Fails toward NOT-wrapping-a-script (a hang is worse than no throttle): our
    sentinel is checked first, then a ``#!`` prefix ⇒ foreign, else binary."""
    if not os.path.exists(path):
        return "absent"
    if _is_our_wrapper(path):
        return "ours"
    try:
        with open(path, "rb") as fh:
            head = fh.read(2)
    except OSError:
        # Unreadable: treat as foreign so we never copy-and-wrap it (fail-safe).
        return "foreign"
    if head[:2] == b"#!":
        return "foreign"
    return "binary"


# --------------------------------------------------------------------------- #
# Real-gh resolution — the shim shadows `gh` on PATH, so the module's refresh
# must resolve the REAL gh, never re-entering the shim.
# --------------------------------------------------------------------------- #
def real_gh_path(env=None):
    """Absolute path of the REAL ``gh`` BINARY. Resolution order:
      1. the relocated ``upstream_path`` (the wrapped-in-place case), if present
         AND it is a real binary (not itself a foreign wrapper);
      2. the first ``gh`` on PATH classified as a real BINARY — SKIPPING both our
         own shim (sentinel) AND any FOREIGN wrapper script (the issue-888
         app-token shim, or any other #!-wrapper).
    None if only shims resolve — the caller then fails open (no throttle).

    #1051 review-2 (finding #1): this MUST skip a FOREIGN wrapper, not only our
    own. Cases 3/4 of ``ensure_gh_rate_wrapper`` bake our shim's REAL_GH at
    whatever this returns; if it returned the app-token shim, our shim would exec
    the app shim, which resolves the first non-self gh on PATH back to our shim →
    the exact our↔app exec-loop. Returning ONLY a real binary here makes that
    impossible by construction (the box stays un-throttled — fail-open — when no
    real binary is directly resolvable, never looped)."""
    up = upstream_path()
    if (os.path.isfile(up) and os.access(up, os.X_OK)
            and _classify_local_gh(up) == "binary"):
        return up
    e = env if env is not None else os.environ
    path = e.get("PATH", "") or ""
    # Walk PATH in order; return the FIRST `gh` that is a real binary. `gh` files
    # that are our shim or a foreign wrapper are skipped so resolution can never
    # re-enter (or wrap onto) a shim.
    for p in path.split(os.pathsep):
        if not p:
            continue
        cand = os.path.join(p, "gh")
        if (os.path.isfile(cand) and os.access(cand, os.X_OK)
                and _classify_local_gh(cand) == "binary"):
            return cand
    return None


# --------------------------------------------------------------------------- #
# Fetch + cache.
# --------------------------------------------------------------------------- #
def _fetch_rate_limit(run, real_gh, now):
    """Call the real gh `api rate_limit` (a non-counting endpoint) and return
    a ``resources`` dict, or None on ANY error (fail-open). The refresh sets
    INTERNAL_ENV so it can never be throttled by the shim, and it never logs
    env/argv (no token can leak)."""
    if not real_gh:
        return None
    env = {**os.environ, INTERNAL_ENV: "1"}
    try:
        r = run([real_gh, "api", "rate_limit"], capture_output=True, text=True,
                timeout=_FETCH_TIMEOUT_S, env=env)
    except Exception as e:
        _diag("fetch-rate-limit", e)   # never logs stdout/stderr (no token)
        return None
    if getattr(r, "returncode", 1) != 0:
        # A persistently-failing fetch (e.g. broken auth) leaves the feature
        # silently inert — record the rc ONLY (never stdout/stderr) so an
        # operator has a trail (#1040 review-2 MINOR-5).
        _diag("fetch-rate-limit", RuntimeError("rc=%s" % getattr(r, "returncode", "?")))
        return None
    try:
        data = json.loads(r.stdout or "{}")
    except (ValueError, TypeError):
        return None
    res = data.get("resources")
    if not isinstance(res, dict):
        return None
    out = {}
    for name in _RESOURCES:
        block = res.get(name)
        if isinstance(block, dict) and "remaining" in block and "limit" in block:
            try:
                out[name] = {
                    "remaining": int(block["remaining"]),
                    "limit": int(block["limit"]),
                    "reset": int(block.get("reset") or 0),
                    # #1052: tag the reading's SOURCE so a row can name it. The
                    # REST bucket is "rest"; the graphql bucket may later be
                    # overridden to "graphql-object" by _ghql.merge_graphql_object.
                    "source": "rest",
                }
            except (ValueError, TypeError):
                continue
    if not out:
        return None
    return out


def _load_cache():
    try:
        with open(status_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(status):
    """Best-effort atomic cache write. A failed write is fail-open (the next
    read simply refetches), so an unwritable ~/.claude is tolerated — but the
    failure is surfaced to the diagnostic journal, never silently swallowed."""
    d = gh_rate_dir()
    # Never persist transient underscore keys (esp. `_pending_alerts`): a later
    # fresh-cache read must NOT re-surface an already-fired alert (#1040
    # review-1 MINOR-3, the once-per-episode guarantee). The alert LATCH itself
    # (`alert`) IS persisted, so re-firing stays blocked until >50 %.
    persist = {k: v for k, v in status.items() if not k.startswith("_")}
    try:
        os.makedirs(d, exist_ok=True)
        tmp = status_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(persist, fh)
        os.replace(tmp, status_path())
    except OSError as e:
        _diag("cache-write", e)


_JOURNAL_MAX_BYTES = 512 * 1024


def _cap_journal():
    """Bound the journal (#1040 review-1 NIT-6): once it exceeds the cap, keep
    ONE previous generation (.1) and start fresh — total stays ~2x the cap even
    under a chronic write-permission DIAG loop. Best-effort, never raises."""
    p = journal_path()
    try:
        if os.path.getsize(p) > _JOURNAL_MAX_BYTES:
            os.replace(p, p + ".1")
    except OSError:
        return   # missing/unrotatable — nothing to cap


def _diag_dedup_path():
    return os.path.join(gh_rate_dir(), "diag-dedup.json")


def _diag(where, exc, now=None):
    """Append a best-effort diagnostic line (never raises, never blocks). A
    failure to even write the diagnostic is the one place a bare swallow is
    unavoidable — there is nowhere left to report to.

    #1087 L1b item 3: identical ``(where, exc)`` failures are DEDUPED to once
    per hour — the first occurrence writes a line, subsequent identical ones in
    the same hour only accumulate a count, and the previous hour's total is
    flushed as ``… ×N since HH:MM`` on the next hour's first occurrence. This
    collapses the 949-lines/day ``fetch-rate-limit`` DIAG spam (and any other
    repeated-identical failure) without losing the count. Dedup state is
    best-effort; if it cannot be read the line is written directly (fail toward
    logging)."""
    if now is None:
        now = time.time()
    lt = time.localtime(now)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z", lt)
    hour = time.strftime("%Y-%m-%dT%H", lt)
    hhmm = time.strftime("%H:%M", lt)
    payload = "%r" % (exc,)
    key = "%s\t%s" % (where, payload)
    to_write = []
    state = None
    try:
        with open(_diag_dedup_path(), encoding="utf-8") as fh:
            state = json.load(fh)
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    prev = state.get(key) if isinstance(state.get(key), dict) else None
    if prev is None or prev.get("hour") != hour:
        if prev is not None and int(prev.get("count", 0) or 0) > 1:
            to_write.append("%s\tDIAG\t%s\t%s\t×%d since %s" % (
                ts, where, payload, int(prev["count"]), prev.get("since", "?")))
        to_write.append("%s\tDIAG\t%s\t%s" % (ts, where, payload))
        state[key] = {"hour": hour, "count": 1, "since": hhmm}
    else:
        prev["count"] = int(prev.get("count", 0) or 0) + 1
        state[key] = prev
    try:
        os.makedirs(gh_rate_dir(), exist_ok=True)
        _cap_journal()
        if to_write:
            with open(journal_path(), "a", encoding="utf-8") as fh:
                fh.write("\n".join(to_write) + "\n")
        tmp = _diag_dedup_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, _diag_dedup_path())
    except OSError:
        return  # nowhere left to log; fail-open by design


def read_status(now=None, run=None, real_gh="__auto__", force=False):
    """Return the cached rate status, refreshing via `gh api rate_limit` when
    the cache is older than CACHE_TTL_S (or ``force``). ALWAYS returns a dict
    (never raises); an unrefreshable reading simply has no ``resources`` for a
    resource, so ``remaining_pct`` returns None and callers treat it as plenty
    (fail-open). Updates the once-alert latch on every fresh reading.

    ``run`` defaults to subprocess.run; ``real_gh`` resolves the real binary
    when left at the sentinel (tests pass an explicit path or None)."""
    if now is None:
        now = time.time()
    if run is None:
        run = subprocess.run
    if real_gh == "__auto__":
        real_gh = real_gh_path()

    cache = _load_cache()

    # #1087 L1b: on an App-shim (installation-token) box, `gh api rate_limit`
    # LIES (a fresh 5000 bucket while real calls 403), and it resolves the real
    # gh binary WITHOUT the token → rc=4 every sweep (the 949-lines/day DIAG
    # spam). SKIP the probe entirely; the budget is recorded from response
    # headers (ghread) + observed 403s via `record_headers_reading`. Mark the
    # probe fresh (the header capture IS the probe here) and keep the marker in
    # sync so the zero-budget line fires.
    if is_app_shim_box():
        _diag("budget-probe-skip",
              "installation token — headers are the source", now=now)
        cache["fetched_at"] = now
        if not isinstance(cache.get("resources"), dict):
            cache["resources"] = {}
        _save_cache(cache)
        _set_throttle_marker(_throttle_warranted(cache))
        return cache

    fetched_at = cache.get("fetched_at")
    fresh = (isinstance(fetched_at, (int, float))
             and (now - fetched_at) < CACHE_TTL_S)
    if fresh and not force:
        return cache

    resources = _fetch_rate_limit(run, real_gh, now)
    if resources is None:
        # Keep the last (possibly stale) reading rather than wiping it, but do
        # not advance fetched_at — a later call will retry. No resources at all
        # on first run => an empty-resources status (fail-open).
        cache.setdefault("resources", {})
        return cache

    # #1052: the REST rate_limit `graphql` bucket lags/mis-reports during a
    # GraphQL exhaustion, so ALSO read the authoritative GraphQL rateLimit
    # object and take the LOWER of the two graphql readings. ONE extra call,
    # only on this refresh path (never on a cache hit), fail-open on error.
    _ghql = _ghql_mod()
    _ghql.merge_graphql_object(resources, _ghql.fetch_graphql_object(
        run, real_gh, timeout=_FETCH_TIMEOUT_S, internal_env=INTERNAL_ENV,
        diag=_diag))

    status = {
        "fetched_at": now,
        "resources": resources,
        "alert": cache.get("alert") if isinstance(cache.get("alert"), dict) else {},
    }
    _update_alert_latch(status)
    _save_cache(status)
    # Keep the shim's cheap fast-path marker in sync with the fresh reading:
    # present iff throttling is currently warranted (some resource < 20 % OR
    # exhausted at remaining==0, #1087 L1b — so the zero-budget line fires even
    # when the limit is unknown and remaining_pct is None).
    _set_throttle_marker(_throttle_warranted(status))
    return status


def _throttle_warranted(status):
    """#1087 L1b: the throttle marker should be present when EITHER resource
    warrants a backoff (< LOW_PCT) OR is exhausted (remaining == 0). The
    remaining==0 arm matters for header-sourced readings where the limit may be
    unknown (remaining_pct None → backoff 0), so a bare backoff test would miss
    a genuine exhaustion. Fail-open to False."""
    try:
        if max(backoff_seconds("core", status),
               backoff_seconds("graphql", status)) > 0:
            return True
        res = (status or {}).get("resources", {})
        for name in _RESOURCES:
            blk = res.get(name)
            if isinstance(blk, dict) and int(blk.get("remaining", 1) or 0) == 0:
                return True
    except (TypeError, ValueError):
        return False
    return False


def record_headers_reading(resource, remaining, reset=None, limit=None,
                           now=None, source="headers"):
    """#1087 L1b: record a budget reading taken from a real response's
    ``X-RateLimit-*`` headers (via ``gates.ghread``) or an observed 403/429,
    into the gh-rate status cache. This is the ONLY truthful budget signal on an
    App-shim (installation-token) box, where ``gh api rate_limit`` lies.

    Read-modify-write under an flock so concurrent gh calls never lose a reading.
    Token-free (only the numeric rate fields are stored). Best-effort: any error
    is journalled and swallowed (accounting must never break gh). A prior known
    ``limit`` is preserved when this reading has none (a 403 body carries no
    limit). On an App-shim box the top-level ``fetched_at`` is bumped so the
    header capture doubles as the probe; on a plain box it is left for
    ``_fetch_rate_limit`` to manage (no graphql-staleness regression)."""
    try:
        if now is None:
            now = time.time()
        resource = resource or "core"
        os.makedirs(gh_rate_dir(), exist_ok=True)
        import fcntl
        fd = os.open(status_path(), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks).decode("utf-8", "replace")
            try:
                data = json.loads(raw) if raw.strip() else {}
                if not isinstance(data, dict):
                    data = {}
            except (ValueError, TypeError):
                data = {}
            res = data.get("resources")
            if not isinstance(res, dict):
                res = data["resources"] = {}
            block = {"remaining": int(remaining),
                     "reset": int(reset or 0), "source": source}
            if limit is not None:
                block["limit"] = int(limit)
            else:
                prev = res.get(resource)
                if isinstance(prev, dict) and "limit" in prev:
                    block["limit"] = prev["limit"]
            res[resource] = block
            if is_app_shim_box():
                data["fetched_at"] = now
            payload = json.dumps(data).encode("utf-8")
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, payload)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        _set_throttle_marker(_throttle_warranted(_load_cache()))
    except Exception as e:   # noqa: BLE001 — accounting must never break gh
        _diag("record-headers", e)


# --------------------------------------------------------------------------- #
# Percentages + backoff.
# --------------------------------------------------------------------------- #
def remaining_pct(resource, status):
    """Percent of `resource`'s budget remaining, or None when unknown (a
    caller treats None as "plenty" — fail-open)."""
    try:
        block = (status or {}).get("resources", {}).get(resource)
        limit = int(block["limit"])
        remaining = int(block["remaining"])
    except (KeyError, TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return 100.0 * remaining / limit


def backoff_seconds(resource, status):
    """Backoff for a background poll of `resource`: 0 at/above LOW_PCT (or when
    unknown), otherwise exponential in how far below LOW_PCT the budget is,
    capped at BACKOFF_CAP_S.

    Bands (5-pt steps below 20 %): the mild band (15-20 %) is small enough that
    even several sequential poll calls stay well within a sweep; the deep bands
    grow past the 100 s sweep soft-cap so the #1041 composition (see
    ``should_hold_gh_poller``) skips such a poller job entirely rather than run
    it late."""
    pct = remaining_pct(resource, status)
    if pct is None or pct >= LOW_PCT:
        return 0
    steps = int((LOW_PCT - pct) // 5)      # 0 for [15,20), 1 [10,15), 2 [5,10), 3 [0,5)
    val = 15 * (2 ** steps)                 # 15, 30, 60, 120, ... capped
    return int(val) if val < BACKOFF_CAP_S else BACKOFF_CAP_S


# --------------------------------------------------------------------------- #
# Once-per-episode alert latch.
# --------------------------------------------------------------------------- #
def _update_alert_latch(status):
    """Mutate ``status['alert']`` in place from the current reading, and stage
    any NEW alerts on ``status['_pending_alerts']`` (a transient list; not
    persisted meaningfully — the consumer reads it right after the fresh read).

    Per resource: a reading < LOW_PCT increments a consecutive-low counter and,
    on the SECOND consecutive low while not already latched, raises ONE alert
    and latches. Any reading at/above LOW_PCT resets the consecutive counter;
    a reading at/above RESET_PCT additionally clears the latch (re-arming the
    next episode). A single transient low never alerts (debounce)."""
    alert = status.setdefault("alert", {})
    pending = []
    gql_authoritative = _ghql_mod().graphql_reading_authoritative(status)
    for name in _RESOURCES:
        pct = remaining_pct(name, status)
        if pct is None:
            continue
        # #1052 review MAJOR-1/MINOR-1: a graphql reading that fell back to REST
        # (object probe unavailable) lies only HIGH during a real exhaustion
        # (REST over-reports remaining), never low. So HOLD the latch on a
        # non-authoritative NOT-low reading — an untrustworthy "recovery" that
        # would otherwise CLEAR an already-fired alert — but let a genuinely LOW
        # REST reading still advance/fire (a low REST reading is a trustworthy
        # floor, so graphql alerting is not lost while the object stays down).
        if name == "graphql" and not gql_authoritative and pct >= LOW_PCT:
            continue
        a = alert.setdefault(name, {"consecutive_low": 0, "alerted": False})
        if pct < LOW_PCT:
            a["consecutive_low"] = int(a.get("consecutive_low", 0)) + 1
            if a["consecutive_low"] >= 2 and not a.get("alerted"):
                a["alerted"] = True
                pending.append(name)
        else:
            a["consecutive_low"] = 0
            if pct >= RESET_PCT:
                a["alerted"] = False
    status["_pending_alerts"] = pending


def pending_alerts(status):
    """Resources that just crossed into a NEW exhaustion episode on this
    reading (the once-alert list). Empty on a latched/healthy reading."""
    p = (status or {}).get("_pending_alerts")
    return list(p) if isinstance(p, list) else []


def alert_line(resource, status, burners=None):
    """A single human-readable alert line for the journal / conformance detail.
    #1087 (a): an optional ``burners`` suffix (the current hour's top-3 gh
    callers) makes an exhaustion episode self-explaining."""
    pct = remaining_pct(resource, status)
    block = (status or {}).get("resources", {}).get(resource, {})
    reset = _fmt_reset(block.get("reset"))
    return ("gh-rate EXHAUSTED: %s %s remaining (reset %s) — pollers backing off%s"
            % (resource, _fmt_pct(pct), reset, burners or ""))


def record_alerts(status):
    """Append a journal line for each pending alert (best-effort, never
    raises). Returns the list of resources alerted."""
    fired = pending_alerts(status)
    if not fired:
        return []
    try:
        os.makedirs(gh_rate_dir(), exist_ok=True)
        _cap_journal()
        burners = current_hour_burner_suffix()   # #1087 (a): top-3 of this hour
        with open(journal_path(), "a", encoding="utf-8") as fh:
            for name in fired:
                fh.write("%s\t%s\n" % (
                    time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    alert_line(name, status, burners=burners)))
    except OSError as e:
        _diag("journal-write", e)
    return fired


# --------------------------------------------------------------------------- #
# Status row (cmd_status / conformance-dimension detail).
# --------------------------------------------------------------------------- #
def _fmt_pct(pct):
    return "?" if pct is None else "%d%%" % int(pct)


def _fmt_reset(reset):
    try:
        r = int(reset)
    except (TypeError, ValueError):
        return "?"
    if r <= 0:
        return "?"
    return time.strftime("%H:%M", time.localtime(r))


def status_row(status, now=None):
    """A single ``gh-rate: core X% graphql Y% reset HH:MM [calls N]`` status row,
    or None when no reading is available (nothing to show). #1087 (a): the
    ``calls N`` suffix is the day's total gh spend recorded by the shim (omitted
    when zero / unrecorded)."""
    res = (status or {}).get("resources") or {}
    if not any(name in res for name in _RESOURCES):
        return None
    core_pct = remaining_pct("core", status)
    gql_pct = remaining_pct("graphql", status)
    reset = None
    for name in _RESOURCES:
        b = res.get(name)
        if isinstance(b, dict) and b.get("reset"):
            reset = b["reset"]
            break
    row = "gh-rate: core %s graphql %s reset %s" % (
        _fmt_pct(core_pct), _fmt_pct(gql_pct), _fmt_reset(reset))
    total = day_total(load_calls(now))
    if total:
        row += " calls %d" % total
    return row


# --------------------------------------------------------------------------- #
# Call classification — a WRITE / human action is NEVER throttled.
# --------------------------------------------------------------------------- #
# READ-ONLY poll shapes (allowlist): only these are ever throttled, and only
# under the poller env. Anything not matched here passes straight through — a
# fail-open bias toward the human/write action.
_POLL_SUBCOMMANDS = {
    ("run", "view"), ("run", "list"),
    ("pr", "view"), ("pr", "checks"), ("pr", "list"), ("pr", "status"),
    ("issue", "view"), ("issue", "list"), ("issue", "status"),
    ("search",),
}
# gh flags that MUTATE via `gh api` — never a poll.
_API_WRITE_FLAGS = {"-X", "--method", "-f", "-F", "--field", "--raw-field",
                    "--input"}
_API_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# gh flags that TAKE A VALUE in the NEXT token. `_subcommand_words` must skip the
# value too, or the value is misread as a COMMAND word — and for `gh api` that
# means a header value (`-H "If-None-Match: <etag>"`, `-H "Authorization: token
# X"`) or a `-q`/`-f` value would be persisted into the call-accounting key
# (#1087 B-review 🟡1: key-space pollution + a token-free-invariant leak on the
# lane's OWN ETag reads). `-R`/`--repo` was already handled specially below.
_VALUE_FLAGS = {"-H", "--header", "-f", "-F", "--field", "--raw-field",
                "-X", "--method", "-q", "--jq", "--input"}


def _subcommand_words(argv, n):
    """The first `n` COMMAND words, skipping any leading flags — notably
    `-R`/`--repo` and every `_VALUE_FLAGS` flag AND ITS value token, so
    `gh -R o/r issue view 5` -> ("issue", "view") and
    `gh api -H "If-None-Match: X" repos/o/r/issues` -> ("api",
    "repos/o/r/issues") (never the header value). A lone unrecognized `-flag`
    is treated as valueless (best-effort; a misparse only ever fails open to
    "not a poll" = pass-through, never a wrongly-throttled call)."""
    out = []
    i = 0
    while i < len(argv) and len(out) < n:
        t = argv[i]
        if t in ("-R", "--repo") or t in _VALUE_FLAGS:
            i += 2                       # skip the flag AND its value token
            continue
        if t.startswith("-R") and len(t) > 2:      # glued `-Ro/r`
            i += 1
            continue
        if "=" in t and t.startswith("-"):         # `--repo=o/r`, `--header=X`
            i += 1
            continue
        if t.startswith("-"):
            i += 1                       # a lone flag before the subcommand
            continue
        out.append(t)
        i += 1
    return out


def classify_call(argv):
    """Classify a gh invocation's argv (WITHOUT the leading ``gh``): returns
    ``(is_poll: bool, resource: str | None)``.

    A poll is a read-only shape on the allowlist; a resource of ``graphql`` is
    inferred for a ``--json`` call or ``api graphql`` (the shapes the incident
    exhausted), else ``core``. Everything else — every write, and the free
    ``api rate_limit`` refresh — is (False, None) and never throttled."""
    toks = [a for a in argv if a]
    if not toks:
        return False, None
    words = _subcommand_words(toks, 2)
    if not words:
        return False, None

    # `gh api` — classify by endpoint + method/field. An explicit non-GET
    # method is always a write (never throttled). The free `rate_limit`
    # endpoint is never a poll.
    if words[0] == "api":
        endpoint = words[1] if len(words) > 1 else ""
        for i, a in enumerate(toks):
            if a in ("-X", "--method") and i + 1 < len(toks) \
                    and toks[i + 1].upper() in _API_WRITE_METHODS:
                return False, None
        if "rate_limit" in endpoint:
            return False, None
        # `api graphql`: a field flag (`-f query=…`) is the NORMAL read idiom
        # here, NOT a write signal — a GraphQL operation is a write ONLY when it
        # is a `mutation` (the keyword is mandatory for a mutation and never
        # present in a read/query), so key on that. Fail-open: a read whose text
        # merely contains "mutation" is classified as a write (never throttled),
        # never the dangerous reverse (#1040 review-1 MAJOR-2).
        if "graphql" in endpoint:
            # A query loaded from a FILE (`-F query=@file`) hides its text from
            # argv, so we cannot tell a mutation from a read — fail SAFE: treat
            # it as a write (never throttle), never the dangerous reverse of
            # delaying a hidden mutation (#1040 review-2 MINOR-3).
            for a in toks:
                if a.startswith("@") or "=@" in a:
                    return False, None
            joined = " ".join(toks).lower()
            if "mutation" in joined:
                return False, None
            return True, "graphql"
        # REST `api`: a field flag implies a POST body -> a write, never a poll.
        for a in toks:
            if a in _API_WRITE_FLAGS:
                return False, None
        return True, "core"

    key2 = (words[0], words[1]) if len(words) > 1 else None
    key1 = (words[0],)
    if key2 in _POLL_SUBCOMMANDS or key1 in _POLL_SUBCOMMANDS:
        # A --json read on issue/pr/search is a GraphQL call (the shape the
        # incident exhausted); run view/list + pr checks are REST (core).
        resource = "graphql" if "--json" in toks else "core"
        return True, resource
    return False, None


# --------------------------------------------------------------------------- #
# #1087 (a) — per-box call accounting. Every shim invocation appends one counter
# to ``~/.claude/gh-rate/calls-<YYYY-MM-DD>.json`` keyed by
# ``"<w0> <w1>|<kind>"`` under the current HOUR, where kind is poller|human|
# write (internal refreshes are never counted). NO argv beyond the two
# subcommand words, NO env values, NO tokens — token-free like the status cache.
# Fail-open: a counter error never delays or blocks the call.
# --------------------------------------------------------------------------- #
def classify_kind(argv, env=None):
    """The 3-way call class the counter records: ``poller`` (background poll,
    ``AIRULESET_GH_POLLER=1``), ``human`` (an interactive READ shape with no
    poller env), ``write`` (everything else — comment/edit/merge/create/api
    write/…), or ``internal`` (the shim's own free ``rate_limit`` refresh,
    ``AIRULESET_GH_RATE_INTERNAL=1`` — never counted). Single source of truth,
    reusing ``classify_call``'s read-shape allowlist so the write/human split
    never drifts from the throttle classifier."""
    env = env if env is not None else os.environ
    if env.get(INTERNAL_ENV) == "1":
        return "internal"
    if env.get(POLLER_ENV) == "1":
        return "poller"
    is_poll, _res = classify_call(argv)
    return "human" if is_poll else "write"


def _day_str(now):
    return time.strftime("%Y-%m-%d", time.localtime(now))


def calls_path(now=None):
    if now is None:
        now = time.time()
    return os.path.join(gh_rate_dir(), "calls-%s.json" % _day_str(now))


def _call_key(argv, kind):
    words = _subcommand_words([a for a in argv if a], 2)
    # #1087 B-review 🟡1: for `gh api <path>` strip the query string so the key
    # is the ENDPOINT (`api repos/o/r/issues`), never fragmented per page / per
    # etag / per state qualifier — the accounting counts endpoints, not URLs.
    if len(words) == 2 and words[0] == "api":
        words = [words[0], words[1].split("?", 1)[0]]
    label = " ".join(words) if words else "?"
    return "%s|%s" % (label, kind)


def record_call(argv, env=None, now=None):
    """Append one counter for this gh invocation (best-effort, never raises,
    never delays — the shim backgrounds it). Internal refreshes are skipped.
    Uses an flock'd read-modify-write so concurrent shim calls never lose a
    count."""
    try:
        if now is None:
            now = time.time()
        kind = classify_kind(argv, env)
        if kind == "internal":
            return
        key = _call_key(argv, kind)
        hour = time.strftime("%H", time.localtime(now))
        path = calls_path(now)
        os.makedirs(gh_rate_dir(), exist_ok=True)
        import fcntl
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            # Read the WHOLE file (#1087 review 🔵: a fixed 1 MB cap would
            # truncate an oversized day file -> json parse fail -> the day's
            # counters reset). Loop until EOF so no size assumption is made.
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks).decode("utf-8", "replace")
            try:
                data = json.loads(raw) if raw.strip() else {}
                if not isinstance(data, dict):
                    data = {}
            except (ValueError, TypeError):
                data = {}
            bucket = data.setdefault(hour, {})
            if not isinstance(bucket, dict):
                bucket = data[hour] = {}
            bucket[key] = int(bucket.get(key, 0) or 0) + 1
            payload = json.dumps(data).encode("utf-8")
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, payload)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except Exception as e:   # noqa: BLE001 — accounting must never break gh
        _diag("record-call", e)


def load_calls(now=None):
    """The day's counter dict ``{hour: {key: count}}`` (empty on absent/bad)."""
    try:
        with open(calls_path(now), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _flatten_calls(data, hour=None):
    """Sum a day's (or one hour's) counters into ``{key: count}``."""
    out = {}
    hours = [hour] if hour is not None else list((data or {}).keys())
    for h in hours:
        bucket = (data or {}).get(h) or {}
        if isinstance(bucket, dict):
            for k, v in bucket.items():
                try:
                    out[k] = out.get(k, 0) + int(v)
                except (ValueError, TypeError):
                    continue
    return out


def top_burners(data, limit=10, hour=None):
    """``[(key, count), …]`` for the day (or one hour), highest first."""
    flat = _flatten_calls(data, hour=hour)
    return sorted(flat.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


def hour_top3(data, hour):
    return top_burners(data, limit=3, hour=hour)


def day_total(data):
    return sum(_flatten_calls(data).values())


def current_hour_burner_suffix(now=None):
    """`` — top: k1×n1, k2×n2, k3×n3`` from the current hour's counters, or ``""``
    when nothing is recorded (so the alert line stays clean)."""
    if now is None:
        now = time.time()
    hour = time.strftime("%H", time.localtime(now))
    top = hour_top3(load_calls(now), hour)
    if not top:
        return ""
    return " — top: " + ", ".join("%s×%d" % (k, n) for k, n in top)


# --------------------------------------------------------------------------- #
# #1087 (c) — honest zero-budget fast-fail line for a non-poller READ.
# --------------------------------------------------------------------------- #
def zero_budget_line(argv, env=None, status=None, now=None):
    """The ONE stderr line the shim prints BEFORE exec'ing the real gh when the
    CACHED status shows ``remaining == 0`` for the resource a HUMAN read will
    use: ``gh: <resource> budget exhausted until HH:MM — retry after the reset``.
    Empty for a poller (it backs off instead), a write (passes through
    unchanged), or when budget remains / is unknown. Cache-only (never a gh
    call), fail-open to ``""``."""
    try:
        if classify_kind(argv, env) != "human":
            return ""
        is_poll, resource = classify_call(argv)
        if not resource:
            return ""
        if status is None:
            status = _load_cache()
        block = (status or {}).get("resources", {}).get(resource)
        if not isinstance(block, dict):
            return ""
        if int(block.get("remaining", 1)) != 0:
            return ""
        return ("gh: %s budget exhausted until %s — retry after the reset"
                % (resource, _fmt_reset(block.get("reset"))))
    except Exception:
        return ""


def wrapper_backoff(argv, status=None, now=None, run=None):
    """Seconds the shim should sleep before a poll call, or 0. A non-poll /
    write / human call → 0. A poll → the MAX backoff across BOTH resources
    (a background poll slows when EITHER budget is low — over-throttling a
    background poll is harmless; under-throttling risks the blind hour). Any
    error → 0 (fail-open)."""
    try:
        is_poll, _res = classify_call(argv)
        if not is_poll:
            return 0
        if status is None:
            status = read_status(now=now, run=run)
        return max(backoff_seconds("core", status),
                   backoff_seconds("graphql", status))
    except Exception:
        return 0


def should_hold_gh_poller(backoff):
    """The #1041 composition decision for a WATCHDOG gh-poller registry job:
    hold (skip, `hold:budget`) whenever a rate backoff is warranted. Watchdog
    jobs loop over many gh calls, so a per-call shim sleep would multiply and
    run the sweep into the unit-kill; skipping the whole job when the budget is
    low is the kill-safe composition ("never run late"). The shim's per-call
    sleep remains the fleet-wide guard for the few-call consumers (hooks,
    ad-hoc scripts, goal-lane riders) not in this registry."""
    try:
        return int(backoff) > 0
    except (TypeError, ValueError):
        return False


def current_gh_backoff(status=None, now=None, run=None):
    """The max backoff across both resources for the current reading — the one
    value the registry loop reads once per sweep to drive
    ``should_hold_gh_poller``. Fail-open → 0."""
    try:
        if status is None:
            status = read_status(now=now, run=run)
        return max(backoff_seconds("core", status),
                   backoff_seconds("graphql", status))
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# The gh PATH shim script (installed by cmd_install / ensure_gh_rate_wrapper).
# --------------------------------------------------------------------------- #
WRAPPER_SENTINEL = "airuleset gh rate-guard shim (#1040)"


def wrapper_script(real_gh, python_exe, module_path, upstream=None,
                   observe=False):
    """Return the bash shim text. It:
      * passes through immediately for the internal refresh and for any call
        with no ``AIRULESET_GH_POLLER=1`` (a human / interactive / hook call —
        never throttled);
      * for a poller, asks this module for the backoff (importing only this
        module, so startup is cheap) and sleeps it before exec'ing the real gh;
      * NEVER blocks — any error path exec's the real gh unchanged (fail-open).

    ``upstream`` (defaults to ``real_gh``) is the depth-1 exec target baked at
    install time: the real gh binary on a plain box, OR the odoo-erp App-token
    shim (``gh-app-shim``) on a stream box (#1087 L1b — it mints the
    installation token, then re-resolves ``gh`` back to THIS wrapper).

    #1087 L1b depth-2 loop breaker: on an ``observe`` (App-shim chain) box, when
    the App shim re-enters us (``AIRULESET_GH_SHIM_DEPTH`` > 1, the token already
    in the env), we SKIP our baked upstream (re-exec'ing the App shim would loop)
    and exec the REAL gh binary directly — re-resolved on PATH, skipping our own
    sentinel. The App shim now lives at ``gh-app-shim`` (not a ``gh`` on PATH),
    so this resolves the real binary in one hop: ``gh -> gh-app-shim -> real gh``.

    #1087 L1b exhaustion observation: on an ``observe`` box the depth-1 hop runs
    the upstream (instead of exec) capturing stderr to a temp, re-emits it
    unchanged, and on a ``rate limit exceeded`` / HTTP 403 / 429 line records
    ``remaining=0`` (token-free, backgrounded). A plain box keeps the fast
    ``exec`` path with zero behaviour change."""
    if upstream is None:
        upstream = real_gh
    return """#!/usr/bin/env bash
# {sentinel}
# MANAGED by airuleset (cli_gh_rate.ensure_gh_rate_wrapper) — do not edit.
# Makes background pollers gh-budget-aware; a human/interactive call and every
# write action pass straight through with zero delay. Fail-open always.
# #1051 depth guard: count our own exec hops and ABORT (never loop) if a
# mis-chain (e.g. a foreign shim resolving back to us) bounces through this
# shim more than twice. Runs FIRST, on every hop, and is exported so it
# survives each exec — a real chain reaches the true gh in 1 hop.
AIRULESET_GH_SHIM_DEPTH=$(( ${{AIRULESET_GH_SHIM_DEPTH:-0}} + 1 ))
export AIRULESET_GH_SHIM_DEPTH
if [ "${{AIRULESET_GH_SHIM_DEPTH}}" -gt 2 ]; then
  echo "gh: airuleset rate-guard shim aborting — exec depth ${{AIRULESET_GH_SHIM_DEPTH}} > 2 (shim loop detected; check the ~/.local/bin/gh chain — odoo-erp issue 3281)" >&2
  exit 89
fi
_OBSERVE={observe}
_UPSTREAM={upstream}
# Resolve the REAL gh BINARY on PATH, skipping THIS shim's own dir and any copy
# of our own shim (sentinel). Used for the #1087 L1b depth>1 App-shim re-entry
# and as the fallback when the baked upstream vanished.
_resolve_real_gh() {{
  _shimdir="$(cd "$(dirname "$0")" && pwd)"
  IFS=':' read -ra _parts <<< "$PATH"
  for _d in "${{_parts[@]}}"; do
    [ -z "$_d" ] && continue
    [ "$_d" = "$_shimdir" ] && continue
    if [ -x "$_d/gh" ] && ! grep -q {sentinel_q} "$_d/gh" 2>/dev/null; then
      printf '%s' "$_d/gh"; return 0
    fi
  done
  return 1
}}
# #1087 L1b: re-entered by the App shim (depth>1) on an observe box — the token
# is already in the env; skip our (App-shim) upstream and exec the real binary
# directly, the loop-free terminator.
if [ "${{AIRULESET_GH_SHIM_DEPTH}}" -gt 1 ] && [ "$_OBSERVE" = "1" ]; then
  _RB="$(_resolve_real_gh || true)"
  [ -x "$_RB" ] || {{ echo "gh: not found (airuleset shim could not resolve real gh)" >&2; exit 127; }}
  exec "$_RB" "$@"
fi
REAL_GH="$_UPSTREAM"
if [ ! -x "$REAL_GH" ]; then
  # Baked upstream gone — prefer the relocated real gh (wrap-in-place), else
  # re-resolve gh on PATH EXCLUDING our own shim.
  if [ -x {upstream_reloc} ]; then
    REAL_GH={upstream_reloc}
  else
    REAL_GH="$(_resolve_real_gh || true)"
  fi
fi
[ -x "$REAL_GH" ] || {{ echo "gh: not found (airuleset shim could not resolve real gh)" >&2; exit 127; }}
# _run_upstream: exec the upstream on a plain box (fast, zero behaviour change);
# on an observe box run it, capture stderr, re-emit unchanged, and on an
# exhaustion line record remaining=0 (backgrounded, token-free).
_run_upstream() {{
  if [ "$_OBSERVE" != "1" ]; then exec "$REAL_GH" "$@"; fi
  _errf="$(mktemp 2>/dev/null)" || {{ exec "$REAL_GH" "$@"; }}
  "$REAL_GH" "$@" 2>"$_errf"; _rc=$?
  cat "$_errf" >&2 2>/dev/null || true
  if grep -qiE 'rate limit exceeded|API rate limit|HTTP/[0-9.]+ (403|429)' "$_errf" 2>/dev/null; then
    ( {internal_env}=1 {python_exe} {module_path} --observe-exhausted >/dev/null 2>&1 & ) 2>/dev/null || true
  fi
  rm -f "$_errf" 2>/dev/null || true
  exit $_rc
}}
# Internal refresh (the shim's own free read): no delay, no accounting.
if [ "${{{internal_env}:-}}" = "1" ]; then
  exec "$REAL_GH" "$@"
fi
# #1087 (a) call accounting: append one counter for THIS call (poller/human/
# write), BACKGROUNDED so it never delays exec, and fail-open (any error is
# journalled by --record, never surfaced). Every non-internal call is counted.
( {python_exe} {module_path} --record -- "$@" >/dev/null 2>&1 & ) 2>/dev/null || true
# Non-poller (human/interactive/hook) call: never throttled. #1087 (c): print
# ONE honest zero-budget line only when the throttle marker says budget MIGHT be
# low (so a healthy call never spends python), then run with zero delay.
if [ "${{{poller_env}:-}}" != "1" ]; then
  if [ -e {marker} ]; then
    {python_exe} {module_path} --zero-budget-line -- "$@" || true
  fi
  _run_upstream "$@"
fi
# Fast path: no throttle marker => budget healthy => never spend python. The
# marker is kept in sync by the per-sweep rate read; a stale marker only ever
# triggers the (correct) python path below.
[ -e {marker} ] || _run_upstream "$@"
# Poller during a low-budget episode: ask for the backoff (0 for a write/
# non-poll/healthy/error), sleep it.
_bo="$({internal_env}=1 {python_exe} {module_path} --wrapper-backoff -- "$@" 2>/dev/null || echo 0)"
case "$_bo" in ''|*[!0-9]*) _bo=0;; esac
if [ "$_bo" -gt 0 ] 2>/dev/null; then sleep "$_bo"; fi
_run_upstream "$@"
""".format(
        sentinel=WRAPPER_SENTINEL,
        sentinel_q=_shq(WRAPPER_SENTINEL),
        upstream=_shq(upstream or ""),
        upstream_reloc=_shq(upstream_path()),
        observe=("1" if observe else "0"),
        marker=_shq(throttle_marker_path()),
        internal_env=INTERNAL_ENV,
        poller_env=POLLER_ENV,
        python_exe=_shq(python_exe),
        module_path=_shq(module_path),
    )


def _shq(s):
    """Minimal single-quote shell escape."""
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def _write_wrapper_file(shim, real_gh, python_exe, module, upstream=None,
                        observe=False):
    """Atomically (temp + os.replace) write the shim at `shim` delegating to
    `real_gh` (or `upstream` when given — the #1087 L1b App-shim chain). chmod
    755 on the temp BEFORE the replace so the shim is never momentarily
    present-but-non-executable."""
    text = wrapper_script(real_gh, python_exe, module, upstream=upstream,
                          observe=observe)
    # pid-suffixed so two overlapping installs never race on the same temp path
    # (the final os.replace is atomic either way — #1040 review-1 NIT-7).
    tmp = "%s.airuleset-tmp.%d" % (shim, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o755)
    os.replace(tmp, shim)
    return text


def _chain_over_app_shim(shim, python_exe, module, verbose=True):
    """#1087 L1b: CHAIN our wrapper over the odoo-erp App-token shim currently at
    `shim` (~/.local/bin/gh) — never wrap it in place (#1051 exec-loop). Move the
    App shim to ``app_shim_path()`` (only if that target is absent OR
    byte-identical — never clobber an unexpected file), then install our wrapper
    at `shim` with the moved shim baked as its upstream and ``observe=True``. The
    resulting live chain is ``gh -> gh-app-shim -> real gh``, loop-free (the
    App shim's basename is no longer a ``gh`` on PATH). Idempotent + LOUD;
    fail-open (a conflict leaves the App shim at gh, working but unthrottled)."""
    dest = app_shim_path()
    try:
        if os.path.exists(dest):
            with open(dest, "rb") as a, open(shim, "rb") as b:
                identical = a.read() == b.read()
            if not identical:
                if verbose:
                    print("    gh rate-guard: chain skipped — %s occupied by a "
                          "different file (App shim left at gh, unthrottled)"
                          % dest)
                return "skip: gh-app-shim occupied by a different file"
            # byte-identical: gh-app-shim already holds the App shim (odoo-erp
            # re-installed the identical shim at gh) — just re-assert our wrapper.
        else:
            os.replace(shim, dest)          # move the App shim aside (atomic)
        os.chmod(dest, 0o755)
        _write_wrapper_file(shim, dest, python_exe, module, upstream=dest,
                            observe=True)
        real = real_gh_path() or "/usr/bin/gh"
        if verbose:
            print("    gh rate-guard: airuleset wrapper chained over the App "
                  "shim (gh -> gh-app-shim -> %s)" % real)
        return "chained over app shim (-> %s)" % dest
    except OSError as e:
        _diag("chain-app-shim", e)
        if verbose:
            print("    gh rate-guard: ⚠ chain failed (%r) — App shim left at gh"
                  % e)
        return "error: chain failed %r" % e


def ensure_gh_rate_wrapper(shim=None, upstream=None, python_exe=None,
                           module=None, verbose=True):
    """Install / refresh the managed gh rate-guard shim (#1040). Idempotent,
    best-effort, non-fatal, and ONLY where the box has a real gh — same shape
    as ``ensure_ffmpeg_static_binary``. Returns a short status string.

    Cases (self-healing across gh reinstalls):
      * shim already ours + a usable upstream → refresh the shim text only if
        changed (no relocation, no 30 MB copy);
      * a FOREIGN wrapper SCRIPT at ~/.local/bin/gh (the odoo-erp issue-888
        app-token shim, or any #!-script that is not ours) → SKIP loudly and
        leave it in place (#1051): copy-and-wrapping it makes our shim and the
        app shim each resolve to the other → an infinite exec-loop. The box
        stays on its own gh chain (working, just unthrottled — fail-open);
      * shim is the REAL gh BINARY (this fleet: gh lives at ~/.local/bin/gh) →
        relocate it to ``upstream`` (copy-then-atomic-replace, so gh is never
        broken mid-way) and install the shim in its place;
      * no shim yet but a real gh resolves elsewhere (e.g. /usr/bin/gh) → install
        the shim pointing at it (no relocation);
      * no gh anywhere → no-op.

    NEVER raises. On any error it leaves gh untouched (fail-open toward the
    human action) and reports the failure."""
    import shutil
    shim = shim or shim_path()
    upstream = upstream or upstream_path()
    python_exe = python_exe or sys.executable or "python3"
    module = module or os.path.abspath(__file__)

    def _say(msg):
        if verbose:
            print("    gh rate-guard: %s" % msg)

    try:
        shim_exists = os.path.exists(shim)
        shim_is_ours = shim_exists and _is_our_wrapper(shim)
        up_ok = os.path.isfile(upstream) and os.access(upstream, os.X_OK)

        # Case A (#1087 L1b): the steady CHAINED state — our wrapper at gh + the
        # App-token shim at gh-app-shim. Refresh our wrapper text only if changed
        # (upstream = the moved App shim, observe on); NEVER repoint at the real
        # binary — that would bypass the installation token. Checked BEFORE the
        # wrap-in-place cases so a chained box is never mis-repointed by Case 3.
        app_dest = app_shim_path()
        if shim_is_ours and _is_app_token_shim(app_dest):
            desired = wrapper_script(app_dest, python_exe, module,
                                     upstream=app_dest, observe=True)
            try:
                with open(shim, encoding="utf-8") as fh:
                    current = fh.read()
            except OSError:
                current = None
            if current == desired:
                return "already installed (chain -> %s)" % app_dest
            _write_wrapper_file(shim, app_dest, python_exe, module,
                                upstream=app_dest, observe=True)
            _say("chain refreshed (-> %s)" % app_dest)
            return "chained (refreshed -> %s)" % app_dest

        # Case 1: our shim is installed and an upstream is present.
        if shim_is_ours and up_ok:
            # #1051 review-1 SELF-HEAL: the 12 incident boxes were left (before
            # the supervisor's manual hotfix) as our #1040 shim at gh + a COPY of
            # the app-token shim at gh-upstream — the exec-loop baked in. On such
            # a box this Case would blindly refresh our shim pointing REAL_GH at
            # the FOREIGN upstream, RE-BAKING the loop (the runtime depth guard
            # would then only downgrade the hang to a fast exit-89 — gh still
            # broken). So if the upstream is itself a foreign wrapper, UN-WRAP:
            # restore it as ~/.local/bin/gh (removing our shim) so gh WORKS
            # again; the next install re-classifies it as foreign -> skip.
            if _classify_local_gh(upstream) == "foreign":
                os.replace(upstream, shim)   # app shim back to gh; drops our shim
                if verbose:
                    print("    gh-rate: un-wrapped a foreign upstream at %s — "
                          "restored the app shim, removed our shim (#1051 loop "
                          "self-heal)" % shim)
                return "unwrapped-foreign-upstream"
            desired = wrapper_script(upstream, python_exe, module)
            try:
                with open(shim, encoding="utf-8") as fh:
                    current = fh.read()
            except OSError:
                current = None
            if current == desired:
                return "already installed (-> %s)" % upstream
            _write_wrapper_file(shim, upstream, python_exe, module)
            _say("shim refreshed (-> %s)" % upstream)
            return "refreshed"

        # Case 2: something that is NOT our shim sits at ~/.local/bin/gh.
        # #1051: classify it FIRST. A FOREIGN wrapper SCRIPT (the issue-888
        # app-token shim, or any #!-script) must NEVER be copy-and-wrapped —
        # that is the exec-loop. SKIP it loudly and leave it in place; the box
        # stays on its own gh chain (working, just unthrottled — fail-open: no
        # throttle there is acceptable, a hang is not). Only a real gh BINARY
        # (ELF) is wrapped in place.
        if shim_exists and not shim_is_ours:
            kind = _classify_local_gh(shim)
            if kind == "foreign":
                # #1087 L1b: the odoo-erp App-token shim is CHAINED (moved to
                # gh-app-shim, our wrapper installed over it) — supersedes the
                # #1051 skip so the accounting + zero-budget line + header-sourced
                # budget go live on the 12 stream boxes that share the scarce
                # installation budget.
                if _is_app_token_shim(shim):
                    return _chain_over_app_shim(shim, python_exe, module,
                                                verbose=verbose)
                # Any OTHER foreign wrapper we do not recognise: SKIP loudly and
                # leave it in place (we only know how to chain the App shim —
                # fail-open: no throttle there is acceptable, a hang is not).
                if verbose:
                    print("    gh-rate: shim install skipped — unknown foreign "
                          "wrapper at %s (chain it via odoo-erp issue 3281)"
                          % shim)
                return "skip: foreign wrapper at ~/.local/bin/gh"
            # kind == "binary": wrap the real gh in place. Copy FIRST (gh stays
            # intact if anything fails), verify, then atomically swap in the shim.
            shutil.copy2(shim, upstream)
            os.chmod(upstream, 0o755)
            if not (os.path.isfile(upstream) and os.access(upstream, os.X_OK)):
                _say("⚠ upstream copy failed — leaving real gh untouched")
                return "error: upstream copy failed"
            _write_wrapper_file(shim, upstream, python_exe, module)
            _say("wrapped real gh in place (real -> %s)" % upstream)
            return "wrapped-in-place"

        # Case 3: shim already ours but upstream vanished (a gh self-update
        # overwrote our shim, or upstream was deleted). Re-resolve a real gh.
        if shim_is_ours and not up_ok:
            real = real_gh_path()
            if real and os.path.realpath(real) != os.path.realpath(shim):
                _write_wrapper_file(shim, real, python_exe, module)
                _say("shim repointed at %s (upstream was missing)" % real)
                return "repointed"
            # No real gh reachable at all -> remove our shim so gh is not broken.
            try:
                os.remove(shim)
            except OSError as e:
                _diag("shim-remove", e)
            _say("removed shim — no real gh resolvable (fail-open)")
            return "removed-no-gh"

        # Case 4: no shim yet. Install it only if a real gh resolves elsewhere.
        real = real_gh_path()
        if not real:
            return "skip: no gh on this box"
        _write_wrapper_file(shim, real, python_exe, module)
        _say("shim installed (-> %s)" % real)
        return "installed"
    except Exception as e:  # noqa: BLE001 — non-fatal, fail-open
        _diag("ensure-wrapper", e)
        _say("⚠ install skipped (%r) — gh left untouched" % e)
        return "error: %r" % e


# --------------------------------------------------------------------------- #
# `airuleset.py gh-rate` + the shim's `--wrapper-backoff` entry.
# --------------------------------------------------------------------------- #
def _cmd_gh_rate_top(args):
    """#1087 (a): ``airuleset.py gh-rate --top [--day YYYY-MM-DD]`` — print the
    day's top gh burners per (subcommand, class), highest first. Reads ONLY the
    counter file (no gh call, no budget)."""
    day = getattr(args, "day", None)
    if day:
        try:
            now = time.mktime(time.strptime(day, "%Y-%m-%d"))
        except ValueError:
            print("gh-rate --top: bad --day %r (want YYYY-MM-DD)" % day)
            return 2
    else:
        now = time.time()
    data = load_calls(now)
    total = day_total(data)
    if not total:
        print("gh-rate: no calls recorded for %s" % _day_str(now))
        return 0
    print("gh-rate top burners for %s (total %d):" % (_day_str(now), total))
    for key, count in top_burners(data, limit=getattr(args, "limit", 15) or 15):
        print("  %6d  %s" % (count, key))
    return 0


def cmd_gh_rate(args):
    """Print the two rate rows (core + graphql), refreshing the cache. With
    ``--top`` print the day's top burners (counter file only, no gh); with
    ``--json`` dump the raw status; with ``--no-refresh`` read the cache only.
    Fires (records) any pending once-alert as a side effect of the refresh."""
    if getattr(args, "top", False):
        return _cmd_gh_rate_top(args)
    no_refresh = getattr(args, "no_refresh", False)
    status = read_status(force=(not no_refresh) and not _cache_is_fresh())
    record_alerts(status)
    if getattr(args, "json", False):
        print(json.dumps({k: v for k, v in status.items()
                          if not k.startswith("_")}, indent=2))
        return 0
    for name in _RESOURCES:
        pct = remaining_pct(name, status)
        block = (status.get("resources") or {}).get(name, {})
        # #1052: name the reading's SOURCE so an operator sees whether graphql
        # came from the authoritative GraphQL object or the REST bucket.
        src = block.get("source", "rest")
        print("%-8s remaining %s (%s/%s) reset %s backoff %ds (%s)" % (
            name, _fmt_pct(pct),
            block.get("remaining", "?"), block.get("limit", "?"),
            _fmt_reset(block.get("reset")),
            backoff_seconds(name, status), src))
    row = status_row(status)
    if row:
        print(row)
    return 0


def status_row_cached():
    """The ``gh-rate: …`` status row from the CACHED reading ONLY (no gh call),
    for a status display that must never spend budget. None when nothing is
    cached yet."""
    return status_row(_load_cache())


def _cache_is_fresh(now=None):
    if now is None:
        now = time.time()
    cache = _load_cache()
    fa = cache.get("fetched_at")
    return isinstance(fa, (int, float)) and (now - fa) < CACHE_TTL_S


def _gh_args_after_dashdash(argv):
    try:
        return argv[argv.index("--") + 1:]
    except ValueError:
        return []


def _wrapper_backoff_main(argv):
    """`cli_gh_rate.py --wrapper-backoff -- <gh args>` — print the shim's
    backoff seconds (0 on anything but a poll under a low budget). Never
    raises; prints 0 on any error (fail-open)."""
    gh_args = _gh_args_after_dashdash(argv)
    try:
        print(wrapper_backoff(gh_args))
    except Exception:
        print(0)
    return 0


def _record_main(argv):
    """`cli_gh_rate.py --record -- <gh args>` — the shim's backgrounded call-
    accounting entry (#1087 a). `record_call` is itself fully fail-open (logs to
    the diag journal, never raises), so this is a thin pass-through."""
    record_call(_gh_args_after_dashdash(argv))
    return 0


def _zero_budget_main(argv):
    """`cli_gh_rate.py --zero-budget-line -- <gh args>` — print the honest
    zero-budget stderr line for a non-poller read at remaining==0 (#1087 c), or
    nothing. `zero_budget_line` is cache-only and fail-open (returns "")."""
    line = zero_budget_line(_gh_args_after_dashdash(argv))
    if line:
        sys.stderr.write(line + "\n")
    return 0


def _observe_exhausted_main(argv):
    """`cli_gh_rate.py --observe-exhausted [--reset <epoch>] [--resource <name>]`
    — the shim's backgrounded 403/429-observed entry (#1087 L1b). Records
    ``remaining=0`` for the resource (default ``core``) with the reset epoch when
    the wrapper parsed one, else ``now + 3600`` (a conservative hourly window).
    Token-free (only the numeric fields are written); fully fail-open."""
    reset = None
    resource = "core"
    i = 1
    while i < len(argv):
        if argv[i] == "--reset" and i + 1 < len(argv):
            try:
                reset = int(argv[i + 1])
            except (ValueError, TypeError):
                reset = None
            i += 2
            continue
        if argv[i] == "--resource" and i + 1 < len(argv):
            resource = argv[i + 1] or "core"
            i += 2
            continue
        i += 1
    now = time.time()
    if reset is None:
        reset = int(now) + 3600
    record_headers_reading(resource, remaining=0, reset=reset, now=now,
                           source="headers")
    return 0


if __name__ == "__main__":
    # #1087 review 🔵: dispatch on the FIRST arg only (equality), never
    # membership — a gh arg literally equal to a sentinel (e.g. `gh issue
    # comment 5 --body "--record"`, passed after `--`) must not mis-dispatch.
    _mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if _mode == "--wrapper-backoff":
        sys.exit(_wrapper_backoff_main(sys.argv[1:]))
    if _mode == "--record":
        sys.exit(_record_main(sys.argv[1:]))
    if _mode == "--zero-budget-line":
        sys.exit(_zero_budget_main(sys.argv[1:]))
    if _mode == "--observe-exhausted":
        sys.exit(_observe_exhausted_main(sys.argv[1:]))

    # A bare run prints the rows (handy for a box operator).
    class _A:
        json = False
        no_refresh = False

    sys.exit(cmd_gh_rate(_A()))
