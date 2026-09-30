"""#1195 item 3 — a persistently unreadable lane-liveness streak, per repo.

``cli_lane_live_gate.LiveLaneGate.keep_reason`` keeps every ``agent-*``
worktree of a repo whose transcript evidence cannot be read (#1193, fail-safe).
That was only one stderr line per pass, so a box whose evidence stayed
unreadable kept those worktrees forever and silently lost that part of its
disk drain.

The disk-guard ``stale-agent-worktree`` rung (``disk_guard._plan_stale_agent_
worktrees``) hands this leaf the gate's per-repo outcomes after every REAL
(non-dry-run) pass. The streak lives in the guard dir
(:data:`STATE_NAME`), not in ``status.json``: that file is rebuilt from
scratch on every 60 s poll, while this one changes only on a drain pass.

Per repo, per pass:

* evidence unreadable → ``passes`` + 1; ``since`` stays the first
  unreadable pass of the streak. A second record in the SAME poll (the quota
  pass and the fs pass of ``run_drain_passes`` share one ``now``) is the same
  pass: it never increments, and a readable read cannot reset it;
* evidence readable → the streak is dropped (reset);
* not consulted (every ``agent-*`` worktree was locked or in live use, so the
  gate was never asked) → unchanged, it is no evidence either way;
* the repo has no ``agent-*`` worktree left, or the entry was not refreshed
  for :data:`STALE_S` (pressure dropped, so the rung stopped running) →
  dropped, so an old alert never pins ``status``.

From :data:`ALERT_PASSES` consecutive passes on, ``airuleset.py status``
prints ``lane liveness unreadable for <repo> since <ts>`` next to the
``root disk-guard:`` row (:func:`status_lines`). No footer segment and no
Discord ping (#693). Best-effort throughout: nothing here ever raises into the
drain or the status command.
"""
# airuleset:script-ok helper module, errors are logged and swallowed by design
import json
import os
import sys
import time
from pathlib import Path

STATE_NAME = "lane-liveness-unknown.json"
# One or two unreadable passes can be a read race (a transcript rotated or
# deleted mid-walk is a count_live_workers stat warning). Three consecutive
# drain passes span two drain intervals (>= 2 min at the every-poll >= 95 %
# tier, ~20 min at the 600 s tier), which is a persistent condition.
ALERT_PASSES = 3
STALE_S = 24 * 3600
_ERR_MAX = 200


def state_path(home=None):
    from watchdog import disk_guard as dg
    return dg._guard_dir(home) / STATE_NAME


def _load(path):
    try:
        repos = json.loads(Path(path).read_text()).get("repos")
    except (OSError, ValueError, AttributeError):
        return {}
    return repos if isinstance(repos, dict) else {}


def _passes(entry):
    """A valid prior entry's pass count, else None (a malformed entry restarts)."""
    if not isinstance(entry, dict):
        return None
    n = entry.get("passes")
    ok = (isinstance(n, int) and not isinstance(n, bool) and n > 0
          and isinstance(entry.get("since"), (int, float)))
    return n if ok else None


def has_agent_worktree(repo):
    """True when ``<repo>/.claude/worktrees`` still holds an ``agent-*`` dir."""
    try:
        with os.scandir(os.path.join(repo, ".claude", "worktrees")) as it:
            return any(e.name.startswith("agent-") and e.is_dir() for e in it)
    except OSError:
        return False


def _fresh(entry, now):
    last = entry.get("last")
    return isinstance(last, (int, float)) and now - last <= STALE_S


def update(prior, outcomes, now, exists_fn=has_agent_worktree):
    """The new ``{repo: {passes, since, last, err}}`` from ``prior`` and one
    pass's ``{repo: err_or_None}`` outcomes. Pure apart from ``exists_fn``."""
    prior = {r: e for r, e in (prior or {}).items() if _passes(e)}
    repos = {}
    for repo, entry in prior.items():
        same_poll = entry.get("last") == now
        if same_poll or (repo not in outcomes and _fresh(entry, now) and exists_fn(repo)):
            repos[repo] = entry
    for repo, err in outcomes.items():
        if not err:
            continue
        entry = prior.get(repo)
        if entry and entry.get("last") == now:
            repos[repo] = dict(entry, err=str(err)[:_ERR_MAX])
            continue
        repos[repo] = {"passes": entry["passes"] + 1 if entry else 1,
                       "since": entry["since"] if entry else now,
                       "last": now, "err": str(err)[:_ERR_MAX]}
    return repos


def record_pass(outcomes, home=None, now=None, path=None):
    """Fold one real pass's outcomes into the state file. Never raises; writes
    only when there is something to keep or to clear."""
    try:
        path = Path(path) if path else state_path(home)
        now = time.time() if now is None else now
        prior = _load(path)
        repos = update(prior, outcomes or {}, now)
        if repos == prior or (not repos and not path.exists()):
            return
        from watchdog import disk_guard_timing as dgt
        dgt.put_text(path, json.dumps({"repos": repos}, sort_keys=True))
        for repo, e in sorted(repos.items()):
            if e["passes"] == ALERT_PASSES and e["last"] == now:
                print("disk-guard: lane liveness unreadable for %s for %d passes "
                      "since %s — agent worktrees kept (#1195)"
                      % (repo, ALERT_PASSES, _iso(e["since"])), file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — bookkeeping never sinks a drain
        print("disk-guard: lane-liveness streak not recorded: %r" % e, file=sys.stderr)


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def status_lines(home=None, path=None, now=None):
    """``airuleset.py status`` lines for every repo at or past
    :data:`ALERT_PASSES` and refreshed within :data:`STALE_S`; empty when none
    (or the file is missing/corrupt)."""
    try:
        now = time.time() if now is None else now
        repos = _load(path or state_path(home))
        out = []
        for repo, e in sorted(repos.items()):
            n = _passes(e)
            if n and n >= ALERT_PASSES and _fresh(e, now):
                last = e.get("last")
                out.append("lane liveness unreadable for %s since %s "
                           "(%d consecutive drain passes%s; agent worktrees kept)"
                           % (repo, _iso(e["since"]), n,
                              ", last %s" % _iso(last)
                              if isinstance(last, (int, float)) else ""))
        return out
    except Exception as e:  # noqa: BLE001 — a status row never breaks `status`
        return ["lane liveness: streak unreadable (%r)" % e]
