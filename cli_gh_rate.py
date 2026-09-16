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

# --------------------------------------------------------------------------- #
# Tunables (module constants so tests reference them, not magic numbers).
# --------------------------------------------------------------------------- #
CACHE_TTL_S = 60                 # per-box cache lifetime; matches the ~60 s poll cadence
LOW_PCT = 20.0                   # below this remaining %, a poll backs off
RESET_PCT = 50.0                 # above this, the once-alert latch re-arms
BACKOFF_CAP_S = 60               # hard cap on a single backoff sleep (safe within the
                                 # 100 s sweep soft-cap / goal-lane tail deadline; deep
                                 # lows skip the watchdog poller entirely via the #1041
                                 # composition, so this bounds only the few-call consumers)
_FETCH_TIMEOUT_S = 15            # a single `gh api rate_limit` read
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


def shim_path():
    """The managed gh shim's install location. On this fleet gh itself lives at
    ~/.local/bin/gh, so the shim WRAPS IT IN PLACE there (the real gh is
    relocated to ``upstream_path`` at install), which needs no PATH surgery and
    is hit in every context ~/.local/bin/gh already resolves."""
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "gh")


def upstream_path():
    """Where the real gh is relocated to when the shim wraps it in place."""
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "gh-upstream")


def _is_our_wrapper(path):
    """True iff `path` is our managed shim (carries WRAPPER_SENTINEL), read
    defensively (a real gh binary is large/binary — read only a small text
    head, treat any error as "not ours")."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return WRAPPER_SENTINEL in fh.read(4096)
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Real-gh resolution — the shim shadows `gh` on PATH, so the module's refresh
# must resolve the REAL gh, never re-entering the shim.
# --------------------------------------------------------------------------- #
def real_gh_path(env=None):
    """Absolute path of the REAL ``gh`` binary. Resolution order:
      1. the relocated ``upstream_path`` (the wrapped-in-place case), if present;
      2. ``gh`` on PATH, SKIPPING our own shim (sentinel-detected) so a refresh
         can never re-enter the wrapper.
    None if only the shim resolves — the caller then fails open (no throttle)."""
    import shutil
    up = upstream_path()
    if os.path.isfile(up) and os.access(up, os.X_OK):
        return up
    e = env if env is not None else os.environ
    path = e.get("PATH", "") or ""
    cand = shutil.which("gh", path=path)
    if cand and not _is_our_wrapper(cand):
        return cand
    if cand:
        # `cand` is our shim — search the remaining PATH dirs for a real gh.
        shim_dir = os.path.realpath(os.path.dirname(cand))
        entries = []
        for p in path.split(os.pathsep):
            if not p:
                continue
            try:
                same = os.path.realpath(p) == shim_dir
            except OSError:
                same = False   # unreadable PATH entry: keep it as a candidate
            if same:
                continue
            entries.append(p)
        alt = shutil.which("gh", path=os.pathsep.join(entries))
        if alt and not _is_our_wrapper(alt):
            return alt
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
    except Exception:
        return None
    if getattr(r, "returncode", 1) != 0:
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
    try:
        os.makedirs(d, exist_ok=True)
        tmp = status_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(status, fh)
        os.replace(tmp, status_path())
    except OSError as e:
        _diag("cache-write", e)


def _diag(where, exc):
    """Append a best-effort diagnostic line (never raises, never blocks). A
    failure to even write the diagnostic is the one place a bare swallow is
    unavoidable — there is nowhere left to report to."""
    try:
        os.makedirs(gh_rate_dir(), exist_ok=True)
        with open(journal_path(), "a", encoding="utf-8") as fh:
            fh.write("%s\tDIAG\t%s\t%r\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%S%z"), where, exc))
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

    status = {
        "fetched_at": now,
        "resources": resources,
        "alert": cache.get("alert") if isinstance(cache.get("alert"), dict) else {},
    }
    _update_alert_latch(status)
    _save_cache(status)
    return status


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
    for name in _RESOURCES:
        pct = remaining_pct(name, status)
        if pct is None:
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


def alert_line(resource, status):
    """A single human-readable alert line for the journal / conformance detail."""
    pct = remaining_pct(resource, status)
    block = (status or {}).get("resources", {}).get(resource, {})
    reset = _fmt_reset(block.get("reset"))
    return "gh-rate EXHAUSTED: %s %s remaining (reset %s) — pollers backing off" % (
        resource, _fmt_pct(pct), reset)


def record_alerts(status):
    """Append a journal line for each pending alert (best-effort, never
    raises). Returns the list of resources alerted."""
    fired = pending_alerts(status)
    if not fired:
        return []
    try:
        os.makedirs(gh_rate_dir(), exist_ok=True)
        with open(journal_path(), "a", encoding="utf-8") as fh:
            for name in fired:
                fh.write("%s\t%s\n" % (
                    time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    alert_line(name, status)))
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


def status_row(status):
    """A single ``gh-rate: core X% graphql Y% reset HH:MM`` status row, or None
    when no reading is available (nothing to show)."""
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
    return "gh-rate: core %s graphql %s reset %s" % (
        _fmt_pct(core_pct), _fmt_pct(gql_pct), _fmt_reset(reset))


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


def _first_nonflag_tokens(argv, n):
    return [a for a in argv if not a.startswith("-")][:n]


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
    words = _first_nonflag_tokens(toks, 2)
    if not words:
        return False, None

    # `gh api` — only a GET (no mutating flag/method) that is not the free
    # rate_limit endpoint counts as a poll.
    if words[0] == "api":
        for i, a in enumerate(toks):
            if a in _API_WRITE_FLAGS:
                return False, None
            if a in ("-X", "--method") and i + 1 < len(toks) \
                    and toks[i + 1].upper() in _API_WRITE_METHODS:
                return False, None
        endpoint = words[1] if len(words) > 1 else ""
        if "rate_limit" in endpoint:
            return False, None
        resource = "graphql" if "graphql" in endpoint else "core"
        return True, resource

    key2 = (words[0], words[1]) if len(words) > 1 else None
    key1 = (words[0],)
    if key2 in _POLL_SUBCOMMANDS or key1 in _POLL_SUBCOMMANDS:
        # A --json read on issue/pr/search is a GraphQL call (the shape the
        # incident exhausted); run view/list + pr checks are REST (core).
        resource = "graphql" if "--json" in toks else "core"
        return True, resource
    return False, None


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


def wrapper_script(real_gh, python_exe, module_path):
    """Return the bash shim text. It:
      * passes through immediately for the internal refresh and for any call
        with no ``AIRULESET_GH_POLLER=1`` (a human / interactive / hook call —
        never throttled);
      * for a poller, asks this module for the backoff (importing only this
        module, so startup is cheap) and sleeps it before exec'ing the real gh;
      * NEVER blocks — any error path exec's the real gh unchanged (fail-open).

    ``real_gh`` is baked at install time; the shim re-resolves it if that path
    ever disappears, EXCLUDING its own dir, so it can never re-enter itself."""
    return """#!/usr/bin/env bash
# {sentinel}
# MANAGED by airuleset (cli_gh_rate.ensure_gh_rate_wrapper) — do not edit.
# Makes background pollers gh-budget-aware; a human/interactive call and every
# write action pass straight through with zero delay. Fail-open always.
REAL_GH={real_gh}
if [ ! -x "$REAL_GH" ]; then
  # Baked path gone — prefer the relocated upstream, else re-resolve gh on PATH
  # EXCLUDING this shim's own dir (so we can never re-enter ourselves).
  if [ -x {upstream} ]; then
    REAL_GH={upstream}
  else
    _shimdir="$(cd "$(dirname "$0")" && pwd)"
    _p=""
    IFS=':' read -ra _parts <<< "$PATH"
    for _d in "${{_parts[@]}}"; do
      [ -z "$_d" ] && continue
      [ "$_d" = "$_shimdir" ] && continue
      if [ -x "$_d/gh" ]; then _p="$_d/gh"; break; fi
    done
    REAL_GH="$_p"
  fi
fi
[ -x "$REAL_GH" ] || {{ echo "gh: not found (airuleset shim could not resolve real gh)" >&2; exit 127; }}
# Internal refresh, or any non-poller (human/interactive/hook) call: no delay.
if [ "${{{internal_env}:-}}" = "1" ] || [ "${{{poller_env}:-}}" != "1" ]; then
  exec "$REAL_GH" "$@"
fi
# Poller: ask for the backoff (0 for a write/non-poll/healthy/error), sleep it.
_bo="$({internal_env}=1 {python_exe} {module_path} --wrapper-backoff -- "$@" 2>/dev/null || echo 0)"
case "$_bo" in ''|*[!0-9]*) _bo=0;; esac
if [ "$_bo" -gt 0 ] 2>/dev/null; then sleep "$_bo"; fi
exec "$REAL_GH" "$@"
""".format(
        sentinel=WRAPPER_SENTINEL,
        real_gh=_shq(real_gh or ""),
        upstream=_shq(upstream_path()),
        internal_env=INTERNAL_ENV,
        poller_env=POLLER_ENV,
        python_exe=_shq(python_exe),
        module_path=_shq(module_path),
    )


def _shq(s):
    """Minimal single-quote shell escape."""
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def _write_wrapper_file(shim, real_gh, python_exe, module):
    """Atomically (temp + os.replace) write the shim at `shim` delegating to
    `real_gh`. chmod 755 on the temp BEFORE the replace so the shim is never
    momentarily present-but-non-executable."""
    text = wrapper_script(real_gh, python_exe, module)
    tmp = shim + ".airuleset-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o755)
    os.replace(tmp, shim)
    return text


def ensure_gh_rate_wrapper(shim=None, upstream=None, python_exe=None,
                           module=None, verbose=True):
    """Install / refresh the managed gh rate-guard shim (#1040). Idempotent,
    best-effort, non-fatal, and ONLY where the box has a real gh — same shape
    as ``ensure_ffmpeg_static_binary``. Returns a short status string.

    Cases (self-healing across gh reinstalls):
      * shim already ours + a usable upstream → refresh the shim text only if
        changed (no relocation, no 30 MB copy);
      * shim is the REAL gh (this fleet: gh lives at ~/.local/bin/gh) → relocate
        it to ``upstream`` (copy-then-atomic-replace, so gh is never broken mid-
        way) and install the shim in its place;
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

        # Case 1: already wrapped, upstream present -> refresh text if changed.
        if shim_is_ours and up_ok:
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

        # Case 2: shim is the REAL gh (wrap it in place). Copy FIRST (gh stays
        # intact if anything fails), verify, then atomically swap in the shim.
        if shim_exists and not shim_is_ours:
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
def cmd_gh_rate(args):
    """Print the two rate rows (core + graphql), refreshing the cache. With
    ``--json`` dump the raw status; with ``--no-refresh`` read the cache only.
    Fires (records) any pending once-alert as a side effect of the refresh."""
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
        print("%-8s remaining %s (%s/%s) reset %s backoff %ds" % (
            name, _fmt_pct(pct),
            block.get("remaining", "?"), block.get("limit", "?"),
            _fmt_reset(block.get("reset")),
            backoff_seconds(name, status)))
    row = status_row(status)
    if row:
        print(row)
    return 0


def _cache_is_fresh(now=None):
    if now is None:
        now = time.time()
    cache = _load_cache()
    fa = cache.get("fetched_at")
    return isinstance(fa, (int, float)) and (now - fa) < CACHE_TTL_S


def _wrapper_backoff_main(argv):
    """`cli_gh_rate.py --wrapper-backoff -- <gh args>` — print the shim's
    backoff seconds (0 on anything but a poll under a low budget). Never
    raises; prints 0 on any error (fail-open)."""
    try:
        idx = argv.index("--")
        gh_args = argv[idx + 1:]
    except ValueError:
        gh_args = []
    try:
        print(wrapper_backoff(gh_args))
    except Exception:
        print(0)
    return 0


if __name__ == "__main__":
    if "--wrapper-backoff" in sys.argv:
        sys.exit(_wrapper_backoff_main(sys.argv[1:]))

    # A bare run prints the rows (handy for a box operator).
    class _A:
        json = False
        no_refresh = False

    sys.exit(cmd_gh_rate(_A()))
