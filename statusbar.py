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
QUESTIONS_TTL_S = 24 * 3600         # mirror notify._QUESTIONS_TTL_S (map prune TTL)


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

    # Skipped bucket (2026-07-16): tickets labeled autopilot-skip. An EXCLUSION
    # count, not a partition of the visible tickets (unlike gk, whose zero must
    # stay visible) — so it renders only when >= 1 and stays off the line at 0.
    skipped = cache.get("skipped")
    skip_sfx = (" \033[38;5;245m· skipped %d\033[0m" % skipped
                if isinstance(skipped, int) and skipped > 0 else "")

    # gk-req badge (airuleset #30): open needs-gatekeeper stream→supervisor
    # action requests (full-authority boxes collect the count). Orange —
    # a stream is BLOCKED waiting on this box's supervisor; hidden at 0.
    gk_req = cache.get("gk_req")
    if isinstance(gk_req, int) and gk_req > 0:
        skip_sfx += " \033[38;5;208m· gk-req %d\033[0m" % gk_req

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
                return "\033[38;5;%dmIssues %d/%d\033[0m%s" % (
                    color, done, done + remaining, skip_sfx)

    open_n = cache.get("open")
    if isinstance(open_n, int):
        # Sub-dev slice split (scope=mine): "Issues <active> · gk <handed-off>" — the
        # gk bucket is own tickets labeled ready-for-review, i.e. parked with the
        # gatekeeper. Rendered ALWAYS when the cache carries gk, INCLUDING gk 0: a
        # hidden zero bucket looks exactly like a broken counter (the user panicked
        # when the gatekeeper returned tickets and "gk" vanished — 2026-07-11).
        gk = cache.get("gk")
        if isinstance(gk, int):
            return ("\033[38;5;75mIssues %d\033[0m \033[38;5;245m· gk %d\033[0m%s"
                    % (open_n, gk, skip_sfx))
        return "\033[38;5;75mIssues %d\033[0m%s" % (open_n, skip_sfx)
    return ""


def questions_segment(cwd, now=None, home=None):
    """Unanswered-❓ badge, SCOPED to the session's project (user complaint
    2026-07-22: the airuleset footer showed the machine-global 14 — "custe
    hluposti"; every map entry carries the asking session's cwd, so the badge
    must attribute questions to their stream):

      - 'otazky N'          — pending ❓ asked from THIS cwd (orange)
      - 'otazky N · inde M' — plus M pending in OTHER projects (grey)
      - 'otazky inde M'     — none here, M elsewhere (all grey)
      - ''                  — none anywhere (badge semantics, like `skipped`)

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
        return ("\033[38;5;214motazky %d\033[0m \033[38;5;245m· inde %d\033[0m"
                % (local, other))
    if local:
        return "\033[38;5;214motazky %d\033[0m" % local
    if other:
        return "\033[38;5;245motazky inde %d\033[0m" % other
    return ""
