"""#1193 — the LIVE-LANE gate every worktree reclaimer consults.

Live (david1@subdev, 30.9.2026 00:21:46Z): the disk-guard
``stale-agent-worktree`` rung removed ``agent-a29b49e670b4cb8f5`` while that
lane's own subagent transcript was still being written. Every reclaimer
decided from the lock, the tree and origin containment, plus a process
cwd/fd inside the tree. An in-session lane's process cwd is the MAIN checkout,
so a lane that had just pushed and sat between tool calls matched "finished".

The evidence that separates them already exists: a worktree-isolated worker's
subagent transcript stem EQUALS its worktree basename (``agent-<hash>``), and
``watchdog.count_live_workers`` classifies it live / wedged / unreadable /
finished / stale. :func:`live_worker_agent_ids_checked` is that read (moved
here from ``cli_lane_liveness._live_worker_agent_ids``, which now delegates),
extended to say whether it could tell: a reclaimer must not read "evidence
unavailable" as "no live lane".

:class:`LiveLaneGate` is the per-pass answer a reclaimer asks per worktree.
It memoizes per repo for ONE pass only and is never persisted, so the answer
is always fresh (never served from ``disk_guard_wt_cache``).
"""
# airuleset:script-ok helper module, errors are returned as data and logged
from __future__ import annotations

import json
import os
import stat
import sys
import time

LIVE_LANE_KEPT = "live lane (fresh subagent transcript) — kept"
LIVENESS_UNKNOWN_KEPT = "lane liveness unknown (transcript evidence unreadable) — kept"


def _is_dir(path):
    """True for a directory, False when it does not exist; any OTHER error
    (EACCES on a parent) propagates — "could not look" is not "absent"."""
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


_META_SUFFIX = ".meta.json"
_META_WORKTREE_KEYS = ("worktreePath", "inheritedWorktreePath")


def _meta_paths(subagents_dir, warn):
    """``{agent_id: meta path}`` for every ``<agent_id>.meta.json`` Claude Code
    keeps beside a subagent transcript (nested dirs too). A directory that
    cannot be listed is reported through ``warn``: ``count_live_workers`` walks
    with ``Path.rglob``, which swallows that error and would read an unreadable
    dir as "no live lane" (#1193 review)."""
    metas = {}
    for dp, _dn, fns in os.walk(subagents_dir, onerror=lambda e: warn(
            "subagents dir unlistable (%s): %s" % (e.filename, e))):
        for fn in fns:
            if fn.endswith(_META_SUFFIX):
                metas[fn[:-len(_META_SUFFIX)]] = os.path.join(dp, fn)
    return metas


def _meta_worktrees(meta_path, warn):
    """Basenames of the worktrees a live agent's meta ties it to: its own
    ``worktreePath`` and, for a child dispatched by a lane, the lane's
    ``inheritedWorktreePath`` — a lane blocked on a long foreground child
    writes nothing itself, so the live child keeps it. No meta = none."""
    if not meta_path:
        return set()
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except FileNotFoundError:
        return set()
    except (OSError, ValueError) as e:
        warn("subagent meta unreadable (%s): %s" % (meta_path, e))
        return set()
    if not isinstance(meta, dict):
        return set()
    return {os.path.basename(v.rstrip(os.sep)) for v in
            (meta.get(k) for k in _META_WORKTREE_KEYS)
            if isinstance(v, str) and v.strip(os.sep + " ")}


def live_worker_agent_ids_checked(repo_root, projects_dir=None, now=None,
                                  freshness_s=None, strict=True):
    """``(ids, err)``: the agent-ids (``agent-<hash>``) with a FRESH LIVE
    subagent transcript under the repo's project dir, and ``err`` — None when
    the read was complete, else why it was not (``ids`` is then a lower bound).

    Live = not ``stale``/``finished`` (the #565/#587 partition): a WEDGED or
    UNREADABLE fresh lane is a worker still in flight, so it counts. No project
    dir for the repo at all is a confident empty (no transcript = no lane). A
    ``count_live_workers`` warning (a stat or content-scan failure) or any
    exception sets ``err``. ``strict`` (the reclaimer mode) also lists each
    ``subagents`` dir itself (an unlistable one sets ``err``) and adds the
    worktree basenames a live agent's meta names (its parent lane's too).
    ``strict=False`` is the pre-#1193 set ``_live_worker_agent_ids`` returns.
    Never raises."""
    ids, errs = set(), []
    try:
        import watchdog
        import watchdog.transcripts as T

        def _warn(msg):
            errs.append(str(msg))
            T._warn_stderr(msg)

        if freshness_s is None:
            from watchdog.compact import COMPACT_LIVE_WORKER_FRESHNESS_S
            freshness_s = COMPACT_LIVE_WORKER_FRESHNESS_S
        projects_dir = projects_dir or os.path.join(
            os.path.expanduser("~"), ".claude", "projects")
        now = time.time() if now is None else now
        proj = os.path.join(projects_dir, T.encode_project_dir(repo_root))
        if not _is_dir(proj):
            return ids, None
        not_live = getattr(T, "_LANE_NOT_LIVE_STATES", frozenset())
        for name in os.listdir(proj):
            sub = os.path.join(proj, name, "subagents")
            try:
                if not _is_dir(sub):
                    continue
                metas = _meta_paths(sub, _warn) if strict else {}
                _count, evidence = watchdog.count_live_workers(
                    projects_dir, repo_root, name, now, freshness_s,
                    on_warn=_warn)
            except Exception as e:  # noqa: BLE001 — one bad session never sinks the set
                _warn("session %s: %r" % (name, e))
                continue
            for lane in evidence or []:
                st = getattr(lane, "state", None)
                is_live = (st not in not_live) if not_live else (st == "live")
                if st is not None and is_live:
                    ids.add(lane.agent_id)
                    ids |= _meta_worktrees(metas.get(lane.agent_id), _warn)
    except Exception as e:  # noqa: BLE001
        errs.append(repr(e))
        print("lane-overlap: worker-transcript evidence unavailable (%s)" % e,
              file=sys.stderr)
    return ids, ("; ".join(errs)[:300] if errs else None)


class LiveLaneGate:
    """One pass's live-lane answers, memoized per repo root.

    ``home`` (or ``projects_dir``) locates the transcripts; ``ids`` injects a
    known live set (the evidence is then complete); ``evidence_fn`` replaces
    the reader (``(repo_root, projects_dir, now) -> (ids, err)``)."""

    def __init__(self, home=None, now=None, projects_dir=None, ids=None,
                 evidence_fn=None):
        if projects_dir is None:
            home = home or os.environ.get("HOME") or os.path.expanduser("~")
            projects_dir = os.path.join(str(home), ".claude", "projects")
        self.projects_dir = projects_dir
        self.now = time.time() if now is None else now
        if ids is not None:
            evidence_fn = lambda *_a: (set(ids), None)  # noqa: E731
        self._evidence_fn = evidence_fn or live_worker_agent_ids_checked
        self._memo = {}

    def evidence(self, repo_root):
        """``(ids, err)`` for ``repo_root``, read once per pass. An error is
        logged once, naming the repo whose worktrees are kept this pass."""
        key = str(repo_root)
        if key not in self._memo:
            try:
                got = self._evidence_fn(key, self.projects_dir, self.now)
            except Exception as e:  # noqa: BLE001 — a reader crash is "could not tell"
                got = (set(), repr(e))
            self._memo[key] = got
            if got[1]:
                print("lane-live-gate: %s — agent worktrees kept this pass (%s)"
                      % (key, got[1]), file=sys.stderr)
        return self._memo[key]

    def keep_reason(self, repo_root, wt_path):
        """Why ``wt_path`` must be kept (a skip reason), or None when the gate
        does not object: a live lane, or an ``agent-*`` worktree of a repo
        whose evidence was unreadable (transcript evidence can only ever name
        an ``agent-*`` dir, so it never pins any other worktree)."""
        ids, err = self.evidence(repo_root)
        base = os.path.basename(str(wt_path).rstrip(os.sep))
        if base in ids:
            return LIVE_LANE_KEPT
        return LIVENESS_UNKNOWN_KEPT if err and base.startswith("agent-") else None
