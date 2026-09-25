"""#1067 slice 1g — verdict cache for the stale-agent-worktree drain rung.

Live (montalu1, 25.9.2026 01:26 CEST): ``disk-guard: step stale-agent-worktree
took 71.7s``. ``discover_stale_agent_worktrees`` ran ``git rev-parse`` +
``git status --porcelain`` + ``git branch -r --contains HEAD`` for every
``agent-*`` worktree on every pass, and a ~30k-file odoo checkout makes each of
those slow even when nothing changed since the previous verdict.

:class:`VerdictCache` keeps one entry per worktree path in a per-account JSON
file in the guard dir (:data:`VERDICTS_NAME`). An entry holds a fingerprint
built from cheap reads only, with NO git subprocess:

* core: the gitdir ``HEAD`` text and the sha it resolves to (loose ref,
  following symbolic refs, then ``packed-refs``), so a branch move shows even
  when the HEAD file does not change; and the ``mtime_ns`` of the gitdir
  ``index``;
* refs: the ``mtime_ns`` of the main repo's ``packed-refs`` and the newest dir
  ``mtime_ns`` under ``refs/remotes`` (every remote, nested namespaces too —
  ``git branch -r`` looks at all of them). Only a verdict that depends on
  containment (the caller's ``refs_dep``) is keyed on it, so a lane's push or
  fetch does not invalidate a dirty or protected verdict.

A pass reuses the stored verdict while the fingerprint is unchanged and the
entry is younger than :data:`VERDICT_TTL_S`. The TTL covers the one blind spot:
removing untracked files leaves the index untouched.

Safety (the design's invariant — the cache can only SKIP work, never cause a
removal):

* the caller runs the lock (a) and live-use (d) checks fresh before
  consulting the cache;
* only a plain ``skip`` verdict is stored. A ``worktree-remove`` verdict, or
  a git failure (``error``), is never stored, so it always gets every fresh
  check;
* a reused row is BUILT here as ``{bytes: 0, kind: "skip", reason, cached}``.
  Nothing else is read from the file, so a tampered entry cannot turn a row
  into an action on another path;
* a verdict is stored only when the core fingerprint read BEFORE the fresh
  checks equals the one read AFTER. A real ``git status`` may rewrite the
  index, or a commit may race the check; such an entry is recomputed next
  pass. The refs part is read once per repo per pass (memo), before any
  check of that repo, so a stored refs value is never newer than the state
  its verdict saw;
* an unreadable fingerprint, or a corrupt, foreign-version or unreadable
  cache file (any exception), means a full recompute, never a crash, and the
  file is rewritten.

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
_SYMREF_DEPTH = 5


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


def _resolve_ref(ref, gitdir, common, depth=0):
    """The sha ``ref`` points to — a loose ref file first (a symbolic
    ``ref:`` is followed up to :data:`_SYMREF_DEPTH` levels), then
    ``packed-refs``. Raises ValueError when it resolves nowhere."""
    for base in (gitdir, common):
        try:
            val = (base / ref).read_text().strip()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            continue
        if not val.startswith("ref:"):
            return val
        if depth >= _SYMREF_DEPTH:
            raise ValueError("symbolic ref chain too deep at %s" % ref)
        return _resolve_ref(val[4:].strip(), gitdir, common, depth + 1)
    try:
        with open(common / "packed-refs") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except FileNotFoundError:
        pass
    raise ValueError("ref %s resolves nowhere" % ref)


def fingerprint(wt_path, gitdir, memo=None):
    """``[HEAD text, sha, index mtime, packed-refs mtime, remotes mtime]`` of
    one worktree (see the module docstring), or None when any core part
    (gitdir, HEAD, its sha, the index) is unreadable — never an exception.
    ``memo`` (a dict) holds the refs part per repo for one pass."""
    if not gitdir:
        return None
    try:
        gd = Path(gitdir)
        if not gd.is_absolute():
            gd = Path(wt_path) / gd
        head = (gd / "HEAD").read_text().strip()
        common = _common_dir(gd)
        sha = (_resolve_ref(head[4:].strip(), gd, common)
               if head.startswith("ref:") else head)
        index = (gd / "index").stat().st_mtime_ns
        memo = {} if memo is None else memo
        key = str(common)
        if key not in memo:
            memo[key] = [_mtime_ns(common / "packed-refs"),
                         _tree_mtime_ns(common / "refs" / "remotes")]
        return [head, sha, index] + memo[key]
    except (OSError, ValueError) as e:
        _dbg("disk-guard wt-cache: fingerprint %s unreadable: %r" % (wt_path, e))
        return None


def _valid_entry(entry):
    """Only a well-formed ``skip`` entry is ever trusted."""
    if not isinstance(entry, dict):
        return False
    fp, refs, ts, v = entry.get("fp"), entry.get("refs"), entry.get("ts"), entry.get("verdict")
    return (isinstance(fp, list) and len(fp) == 3
            and (refs is None or (isinstance(refs, list) and len(refs) == 2))
            and isinstance(ts, (int, float)) and not isinstance(ts, bool)
            and isinstance(v, dict) and v.get("kind") == "skip"
            and isinstance(v.get("reason"), str))


class VerdictCache:
    """One pass's view of the per-account verdict cache file."""

    def __init__(self, path, now):
        self.path = Path(path)
        self.now = now
        self.dirty = False              # a corrupt/pruned file is rewritten
        self.entries = self._load()
        self.hits = self.misses = 0
        self._refs_memo = {}

    def _load(self):
        """The trusted entries; ANY failure reading this untrusted file
        (EACCES, bad JSON, a RecursionError on deep nesting, a foreign
        shape) is a full recompute, never an exception."""
        try:
            if not self.path.exists():
                return {}
            data = json.loads(self.path.read_text())
            if (not isinstance(data, dict) or data.get("v") != CACHE_VERSION
                    or not isinstance(data.get("entries"), dict)):
                raise ValueError("foreign shape %s" % type(data).__name__)
            entries = {k: v for k, v in data["entries"].items() if _valid_entry(v)}
        except Exception as e:  # airuleset:script-ok untrusted file → recompute, logged
            _dbg("disk-guard wt-cache: %s unreadable, full recompute: %r"
                 % (self.path, e))
            self.dirty = True
            return {}
        self.dirty = len(entries) != len(data["entries"])
        return entries

    def _reusable(self, entry, fp):
        return (entry is not None and fp is not None and entry["fp"] == fp[:3]
                and (entry["refs"] is None or entry["refs"] == fp[3:])
                and 0 <= self.now - entry["ts"] < VERDICT_TTL_S)

    def classify(self, wt_path, gitdir, compute_fn, refs_dep=None):
        """The verdict fields for ``wt_path``: a rebuilt ``skip`` row when the
        stored entry is reusable, else ``compute_fn()`` (the fresh git checks),
        stored when it is a plain ``skip`` and the core fingerprint held still
        across it. ``refs_dep(verdict)`` says whether the verdict depends on
        the remote refs (default: yes, the safe side)."""
        fp = fingerprint(wt_path, gitdir, self._refs_memo)
        entry = self.entries.get(wt_path)
        if self._reusable(entry, fp):
            self.hits += 1
            return {"bytes": 0, "kind": "skip", "reason": entry["verdict"]["reason"],
                    "cached": True}
        self.misses += 1
        verdict = compute_fn()
        stable = fp is not None and fingerprint(wt_path, gitdir, self._refs_memo) == fp
        if stable and verdict.get("kind") == "skip" and not verdict.get("error"):
            dep = refs_dep is None or refs_dep(verdict)
            self.entries[wt_path] = {"fp": fp[:3], "refs": fp[3:] if dep else None,
                                     "ts": self.now, "verdict": {
                                         "kind": "skip", "reason": verdict["reason"]}}
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
