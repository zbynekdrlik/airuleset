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
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TICKETS_TTL_S = 120                 # refresh the open-issues count at most this often
SPAWN_GUARD_S = 30                  # min seconds between background refresh spawns
AUTOPILOT_RUN_WINDOW_S = 6 * 3600   # a run-card younger than this = active run


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
    """The GitHub-tickets statusline segment for the session at `cwd`:

      - 'Issues D/T' during an ACTIVE autopilot run for this repo (D tickets carded
        this run, T = D + the remaining backlog from the last card; green when
        the backlog is empty),
      - 'Issues N' otherwise (open non-autopilot-skip GitHub issues),
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

    # Active autopilot run for this repo → done/total from the last run-card.
    name = cache.get("name") or ""
    if name:
        prog = _load(progress_dir(home) / (name + ".json"))
        if prog and now - (prog.get("ts") or 0) <= AUTOPILOT_RUN_WINDOW_S:
            done, remaining = prog.get("done"), prog.get("remaining")
            if isinstance(done, int) and isinstance(remaining, int):
                color = 40 if remaining == 0 else 75    # green when backlog empty
                return "\033[38;5;%dmIssues %d/%d\033[0m" % (color, done,
                                                            done + remaining)

    open_n = cache.get("open")
    if isinstance(open_n, int):
        # Sub-dev slice split (scope=mine): "Issues <active> · gk <handed-off>" — the
        # gk bucket is own tickets labeled ready-for-review, i.e. parked with the
        # gatekeeper. Rendered ALWAYS when the cache carries gk, INCLUDING gk 0: a
        # hidden zero bucket looks exactly like a broken counter (the user panicked
        # when the gatekeeper returned tickets and "gk" vanished — 2026-07-11).
        gk = cache.get("gk")
        if isinstance(gk, int):
            return ("\033[38;5;75mIssues %d\033[0m \033[38;5;245m· gk %d\033[0m"
                    % (open_n, gk))
        return "\033[38;5;75mIssues %d\033[0m" % open_n
    return ""
