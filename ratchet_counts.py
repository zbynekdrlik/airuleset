"""#842 net-drain ratchet counters — per-repo created_today / closed_today.

The autopilot loop was generating more issues than it closed (airuleset
2026-09-01→09-02: 41 created vs 36 closed, net +5). This leaf computes, per
repo, how many issues were CREATED vs CLOSED in the current LOCAL day (the box
timezone, `datetime.now().astimezone()` — the offset carries into the GitHub
search so the day boundary matches what the owner sees), cached with the
tickets-status TTL. Two consumers share it (single source of truth, no drift):

  - `hooks/block-ungated-issue-filing.sh` — at an UNATTENDED filing, BLOCKS a
    non-exempt discovery filing while `created_today >= closed_today` (the repo
    is not draining). A gh error → None → the hook BLOCKS (fail-safe, never a
    wrong ALLOW).
  - `airuleset.cmd_tickets_status --refresh` — records the two counts into the
    cwd tickets cache so `statusbar.tickets_segment` renders `I N▲` when
    `created_today > closed_today`, at ZERO extra gh cost per render.

stdlib only. Attribution is deliberately NOT by gh author (#842 design Prístup
1): the fleet shares one gh identity per repo, so author cannot distinguish the
owner from automation. Instead the ratchet ENGAGES only on the UNATTENDED path
(the hook's job), so the present owner is never ratchet-blocked and owner
filings only inflate created_today in the SAFE (stricter) direction. This
counter counts ALL created vs ALL closed today — the simple, gh-computable
net-drain proxy.
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime

# Mirror statusbar.TICKETS_TTL_S (kept a literal here so this leaf stays
# import-light — no `import statusbar` on the hook's hot path).
TTL_S = 120


def ratchet_blocks(created_today, closed_today):
    """True when an UNATTENDED automation discovery filing must be BLOCKED:
    the repo is NOT strictly draining today (`created_today >= closed_today`).
    #842 req 2: "allowed ONLY while created_today < closed_today". 0/0 blocks
    by design — the first unattended discovery filing of any day needs a close
    first (net-drain by definition)."""
    return created_today >= closed_today


def footer_drift(created_today, closed_today):
    """True when the footer `I N` should render the `▲` drift marker: the repo
    is strictly net-INFLATING today (`created_today > closed_today`, #842 req
    6). Deliberately a STRICTER threshold than `ratchet_blocks` (which also
    blocks at parity) — the ▲ shows the black-hole shape the moment it starts."""
    return created_today > closed_today


def _counts_dir(home=None):
    home = home or os.path.expanduser("~")
    return os.path.join(home, ".claude", "tickets-status")


def _cache_path(repo, home=None):
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", repo or "unknown")
    return os.path.join(_counts_dir(home), "ratchet-" + slug + ".json")


def _local_now(now=None):
    return now or datetime.now().astimezone()


def _day_start_iso(now=None):
    """Midnight of the current LOCAL day, ISO-8601 WITH the box-local UTC offset
    (e.g. `2026-09-02T00:00:00+0200`) — GitHub search reads a bare date as UTC,
    which would shift the boundary by the offset, so the offset is mandatory."""
    start = _local_now(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.strftime("%Y-%m-%dT%H:%M:%S%z")


def _today_str(now=None):
    return _local_now(now).strftime("%Y-%m-%d")


def _gh_count(argv, cwd, gh_env=None):
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=20, env=gh_env)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    try:
        return int((r.stdout or "").strip())
    except (TypeError, ValueError):
        return None


def compute_counts(repo, cwd, gh_env=None, now=None):
    """`(created_today, closed_today)` computed live via `gh`, or `None` on ANY
    gh error (the caller BLOCKS — fail-safe). `--state all` on the created count
    so a created-then-closed-today issue still counts; the closed count is
    `--state closed` with `closed:>=`. `-L 500` caps the (never-huge) day set."""
    day = _day_start_iso(now)
    created_argv = ["gh", "issue", "list", "--state", "all",
                    "--search", "created:>=%s" % day,
                    "-L", "500", "--json", "number", "-q", "length"]
    closed_argv = ["gh", "issue", "list", "--state", "closed",
                   "--search", "closed:>=%s" % day,
                   "-L", "500", "--json", "number", "-q", "length"]
    if repo:
        created_argv += ["-R", repo]
        closed_argv += ["-R", repo]
    created = _gh_count(created_argv, cwd, gh_env)
    if created is None:
        return None
    closed = _gh_count(closed_argv, cwd, gh_env)
    if closed is None:
        return None
    return (created, closed)


def _read_cache(repo, home=None):
    try:
        with open(_cache_path(repo, home), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_cache(repo, created, closed, day, home=None):
    """Best-effort atomic write; returns True on success, False on an unwritable
    ~/.claude (a disk-pressure filing must not crash the hook — the exact
    scenario a net-drain harness runs in — so the failure is reported to the
    caller, never raised; the caller treats it as "cache not updated")."""
    path = _cache_path(repo, home)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"created_today": created, "closed_today": closed,
                       "day": day, "ts": time.time()}, fh)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def cached_counts(repo, cwd, gh_env=None, now=None, home=None, refresh=True):
    """`(created_today, closed_today, day)` from the per-repo counter cache,
    refreshing via `gh` when the cache is missing, day-rolled, or older than
    `TTL_S`. Returns `None` when a NEEDED refresh's gh call fails (caller
    BLOCKS). `refresh=False` reads the cache only (returns None when stale/
    absent) — used where a refresh is not wanted."""
    now = _local_now(now)
    today = _today_str(now)
    cached = _read_cache(repo, home)
    fresh = (isinstance(cached, dict)
             and cached.get("day") == today
             and isinstance(cached.get("created_today"), int)
             and isinstance(cached.get("closed_today"), int)
             and isinstance(cached.get("ts"), (int, float))
             and (time.time() - cached["ts"]) <= TTL_S)
    if fresh:
        return (cached["created_today"], cached["closed_today"], today)
    if not refresh:
        return None
    counts = compute_counts(repo, cwd, gh_env, now)
    if counts is None:
        return None
    created, closed = counts
    _write_cache(repo, created, closed, today, home)
    return (created, closed, today)


def bump_created(repo, now=None, home=None):
    """Increment the cached `created_today` by one after a ratchet-PASS — closes
    the within-TTL burst race across SEPARATE hook invocations (each hook
    subprocess reads a fresh cache, so without this a burst of unattended
    filings inside one 120s window would all pass on the same stale count).
    Best-effort: no cache / day-rolled / non-int → no-op, never raises. Safe
    because only the single-threaded MAIN session ever reaches this (workers are
    hard-blocked), so no concurrent write races the cache."""
    today = _today_str(now)
    c = _read_cache(repo, home)
    if not isinstance(c, dict) or c.get("day") != today:
        return
    if not isinstance(c.get("created_today"), int):
        return
    _write_cache(repo, c["created_today"] + 1, c.get("closed_today", 0),
                 today, home)
