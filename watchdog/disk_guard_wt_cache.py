"""#1067 slice 1g — verdict cache for the stale-agent-worktree drain rung.

Live (montalu1, 25.9.2026 01:26 CEST): ``disk-guard: step stale-agent-worktree
took 71.7s``. ``discover_stale_agent_worktrees`` ran ``git rev-parse`` +
``git status --porcelain`` + ``git branch -r --contains HEAD`` for every
``agent-*`` worktree on every pass, and a ~30k-file odoo checkout makes each of
those slow even when nothing changed since the previous verdict.

:class:`VerdictCache` keeps one entry per worktree path in a per-account JSON
file in the guard dir (:data:`VERDICTS_NAME`). An entry holds a fingerprint
built from cheap reads only, with NO git subprocess:

* the gitdir ``HEAD`` text and the sha it resolves to (loose ref, then
  ``packed-refs``), so a branch move shows even when the HEAD file does not
  change;
* the ``mtime_ns`` of the gitdir ``index``;
* the ``mtime_ns`` of the main repo's ``packed-refs``, and the newest dir
  ``mtime_ns`` under ``refs/remotes/origin``, so nested namespaces are covered.

A pass reuses the stored verdict while the fingerprint is unchanged and the
entry is younger than :data:`VERDICT_TTL_S`. The TTL covers the one blind spot:
removing untracked files leaves the index untouched.

Safety (the design's invariant — the cache can only SKIP work, never cause a
removal):

* the caller runs the lock (a) and live-use (d) checks fresh before
  consulting the cache;
* a ``worktree-remove`` verdict is never reused. It is always recomputed with
  every fresh check;
* a verdict is stored only when the fingerprint read BEFORE the fresh checks
  equals the one read AFTER. A real ``git status`` may rewrite the index, or a
  commit may race the check; such an entry is recomputed next pass;
* an unreadable fingerprint, or a corrupt or foreign-version cache file,
  means a full recompute, never a crash.

Entries of worktrees not seen this pass are dropped on :meth:`save`. The file
is written only when something changed, through the atomic
``disk_guard_timing.put_text``. The caller passes no cache path on a dry-run
poll, which then neither reads nor writes it.
"""
# airuleset:script-ok helper module, imported from disk_guard_worktrees
from __future__ import annotations

import json
import os
from pathlib import Path

VERDICTS_NAME = "stale-wt-verdicts.json"
VERDICT_TTL_S = 6 * 3600
CACHE_VERSION = 1
_VERDICT_KEYS = ("bytes", "kind", "reason", "branch", "contained_in")


def _dbg(msg):
    from watchdog import disk_guard as dg
    dg._dbg(msg)


def _mtime_ns(path):
    """``st_mtime_ns`` of ``path``, or None when it does not exist."""
    try:
        return os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return None


def _tree_mtime_ns(path):
    """Newest dir ``mtime_ns`` under ``path`` (a ref update renames a lock file
    inside the ref's own dir), or None when ``path`` does not exist."""
    newest = _mtime_ns(path)
    if newest is None:
        return None
    for dirpath, _dirs, _files in os.walk(path):
        m = _mtime_ns(dirpath)
        if m is not None and m > newest:
            newest = m
    return newest


def _common_dir(gitdir):
    """The main repo's git dir for a worktree gitdir (its ``commondir`` file),
    or ``gitdir`` itself for a main checkout."""
    try:
        rel = (gitdir / "commondir").read_text().strip()
    except FileNotFoundError:
        return gitdir
    return (gitdir / rel).resolve()


def _resolve_ref(ref, gitdir, common):
    """The sha ``ref`` points to — a loose ref file first, then
    ``packed-refs``. Raises ValueError when it resolves nowhere."""
    for base in (gitdir, common):
        try:
            return (base / ref).read_text().strip()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            continue
    try:
        with open(common / "packed-refs") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except FileNotFoundError:
        pass
    raise ValueError("ref %s resolves nowhere" % ref)


def fingerprint(wt_path, gitdir):
    """The cheap fingerprint of one worktree (see the module docstring), or
    None when any required part (gitdir, HEAD, its sha, the index) is
    unreadable — never an exception."""
    if not gitdir:
        return None
    try:
        gd = Path(gitdir)
        if not gd.is_absolute():
            gd = Path(wt_path) / gd
        head = (gd / "HEAD").read_text().strip()
        common = _common_dir(gd)
        sha = (_resolve_ref(head[5:].strip(), gd, common)
               if head.startswith("ref:") else head)
        index = (gd / "index").stat().st_mtime_ns
        return [head, sha, index, _mtime_ns(common / "packed-refs"),
                _tree_mtime_ns(common / "refs" / "remotes" / "origin")]
    except (OSError, ValueError) as e:
        _dbg("disk-guard wt-cache: fingerprint %s unreadable: %r" % (wt_path, e))
        return None


def _valid_entry(entry):
    return (isinstance(entry, dict) and isinstance(entry.get("fp"), list)
            and isinstance(entry.get("ts"), (int, float))
            and isinstance(entry.get("verdict"), dict)
            and isinstance(entry["verdict"].get("kind"), str))


class VerdictCache:
    """One pass's view of the per-account verdict cache file."""

    def __init__(self, path, now):
        self.path = Path(path)
        self.now = now
        self.dirty = False              # a corrupt/pruned file is rewritten
        self.entries = self._load()
        self.hits = self.misses = 0

    def _load(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as e:
            data = e
        if (not isinstance(data, dict) or data.get("v") != CACHE_VERSION
                or not isinstance(data.get("entries"), dict)):
            _dbg("disk-guard wt-cache: %s unreadable or foreign, full recompute: %r"
                 % (self.path, data if isinstance(data, Exception) else type(data)))
            self.dirty = True
            return {}
        entries = {k: v for k, v in data["entries"].items() if _valid_entry(v)}
        self.dirty = len(entries) != len(data["entries"])
        return entries

    def _reusable(self, entry, fp):
        # a reclaimable verdict is never STORED (classify); this re-check is
        # the belt for a hand-edited or older file that carries one anyway
        return (entry is not None and fp is not None and entry["fp"] == fp
                and entry["verdict"]["kind"] != "worktree-remove"
                and 0 <= self.now - entry["ts"] < VERDICT_TTL_S)

    def classify(self, wt_path, gitdir, compute_fn):
        """The verdict fields for ``wt_path``: the stored ones when reusable,
        else ``compute_fn()`` (the fresh git checks), stored when the
        fingerprint held still across it. Reused rows carry ``cached=True``."""
        fp = fingerprint(wt_path, gitdir)
        entry = self.entries.get(wt_path)
        if self._reusable(entry, fp):
            self.hits += 1
            return dict(entry["verdict"], cached=True)
        self.misses += 1
        verdict = compute_fn()
        if (fp is not None and verdict.get("kind") != "worktree-remove"
                and fingerprint(wt_path, gitdir) == fp):
            self.entries[wt_path] = {"fp": fp, "ts": self.now, "verdict": {
                k: verdict[k] for k in _VERDICT_KEYS if k in verdict}}
            self.dirty = True
        elif self.entries.pop(wt_path, None) is not None:
            self.dirty = True
        return verdict

    def save(self, seen):
        """Drop entries of worktrees not seen this pass; write when changed."""
        keep = set(seen)
        for gone in [k for k in self.entries if k not in keep]:
            del self.entries[gone]
            self.dirty = True
        _dbg("disk-guard wt-cache: %d reused, %d recomputed" % (self.hits, self.misses))
        if self.dirty:
            from watchdog import disk_guard_timing as dgt
            dgt.put_text(self.path, json.dumps({"v": CACHE_VERSION,
                                                "entries": self.entries}))
