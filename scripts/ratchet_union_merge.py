#!/usr/bin/env python3
"""Custom git merge driver for ``tests/size_ratchet.json`` (#553).

Every fleet integration round, two DISJOINT code lanes still collide on the
size ratchet because each grows some file and bumps its per-key ceiling. The
default text merge knows nothing about the ratchet's semantics (a JSON of
``{section: {path: ceiling}}`` where a merge means "per-key union-max"), so it
conflicts on ceilings that a machine can reconcile deterministically.

This driver does a **3-way, base-aware, recursive per-key** merge:

* ``%O`` = merge base, ``%A`` = ours, ``%B`` = theirs — the three temp files
  git hands a merge driver. The merged result is written back over ``%A`` and
  exit 0 signals a clean merge (``gitattributes(5)``).
* Per key, considering the base value:
    - one side changed, the other didn't → take the CHANGED side (this is what
      preserves a deliberate LOWERING or a DELETION — a file split that dropped
      a ceiling or pruned a function key is honored, never resurrected; a naive
      2-way union-max would resurrect it, the batch-19 failure).
    - both sides changed the same numeric leaf → ``max`` (ratchet-safe: a
      higher ceiling can never fail ``size_ratchet --check``; the supervisor's
      post-merge ``--check`` tops up any combined-growth overshoot).
    - delete-on-one-side vs modify-on-the-other, or a type mismatch → a genuine
      semantic conflict → raise ``MergeConflict`` (never silently pick a side).

**Fail-safe:** on ANY failure (corrupt JSON, a MergeConflict, a type clash)
the driver does NOT invent a merge — it falls back to git's built-in
``git merge-file`` (the same 3-way TEXT merge that would happen with no driver
at all), writing conflict markers into ``%A``, and exits NON-ZERO so git treats
the path as conflicted for a human to resolve. A wrong-but-silent merge is the
one outcome this must never produce.

Config is per-clone (git will not run a driver named only in a committed
``.gitattributes`` from committed config, for security), so ``airuleset.py``
``cmd_install`` writes the repo-local ``git config merge.ratchet-union.driver``
idempotently (worktrees share the common ``.git/config``). A clone WITHOUT that
config simply falls back to git's default text merge — a safe degradation, not
a breakage.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

# Unique sentinel for "key absent on this side". A plain object() compares
# unequal (identity) to every real JSON value and equal only to itself, which
# is exactly the semantics the 3-way rule below relies on.
MISSING = object()


class MergeConflict(Exception):
    """A genuine semantic conflict the driver must not resolve on its own
    (delete-vs-modify, or a leaf that changed type between the sides)."""


def _is_int(value: Any) -> bool:
    # bool is an int subclass; a JSON ``true``/``false`` is never a ratchet
    # ceiling, so exclude it explicitly rather than max()-ing booleans.
    return isinstance(value, int) and not isinstance(value, bool)


def _three_way(base: Any, ours: Any, theirs: Any) -> Any:
    """Merge one node given its base/ours/theirs values (any may be MISSING).
    Returns the merged value, or MISSING if the key should be absent. Raises
    MergeConflict on an irreconcilable divergence."""
    if ours == theirs:            # both sides agree (includes both-MISSING)
        return ours
    if ours == base:              # ours untouched → take theirs' change (incl. deletion)
        return theirs
    if theirs == base:            # theirs untouched → take ours' change
        return ours
    # both sides changed this node, differently
    if isinstance(ours, dict) and isinstance(theirs, dict):
        base_dict = base if isinstance(base, dict) else {}
        return _merge_dicts(base_dict, ours, theirs)
    if _is_int(ours) and _is_int(theirs):
        return max(ours, theirs)  # ratchet-safe: higher ceiling never fails --check
    raise MergeConflict(
        f"irreconcilable change: base={base!r} ours={ours!r} theirs={theirs!r}")


def _merge_dicts(base: dict, ours: dict, theirs: dict) -> dict:
    result: dict = {}
    for key in set(base) | set(ours) | set(theirs):
        merged = _three_way(base.get(key, MISSING),
                             ours.get(key, MISSING),
                             theirs.get(key, MISSING))
        if merged is not MISSING:
            result[key] = merged
    return result


def merge_snapshots(base: dict, ours: dict, theirs: dict) -> dict:
    """3-way merge of two size-ratchet snapshots against their base. Raises
    MergeConflict on an irreconcilable divergence. Preserves every top-level
    section (never silently drops an unknown one)."""
    merged = _three_way(base, ours, theirs)
    if merged is MISSING or not isinstance(merged, dict):
        raise MergeConflict("merged snapshot is not a JSON object")
    return merged


def _load(path: str, allow_empty: bool = False) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    if allow_empty and not raw.strip():
        # git hands an EMPTY %O when the file has no common-ancestor version
        # (added on both sides) — that means "no base", not a parse error.
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise MergeConflict(f"{path}: top level is not a JSON object")
    return data


def _fallback_text_merge(o_path: str, a_path: str, b_path: str) -> int:
    """Degrade to git's built-in 3-way text merge (conflict markers into %A),
    then force a NON-ZERO exit so git always treats the path as conflicted when
    our smart merge could not validate it — never a silent clean claim."""
    proc = subprocess.run(
        ["git", "merge-file", "-L", "ours", "-L", "base", "-L", "theirs",
         a_path, o_path, b_path])
    return proc.returncode if proc.returncode != 0 else 1


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 3:
        print("usage: ratchet_union_merge.py %O %A %B [%P]", file=sys.stderr)
        return 2
    o_path, a_path, b_path = argv[0], argv[1], argv[2]
    path_hint = argv[3] if len(argv) > 3 else a_path
    try:
        base = _load(o_path, allow_empty=True)
        ours = _load(a_path)
        theirs = _load(b_path)
        merged = merge_snapshots(base, ours, theirs)
        text = json.dumps(merged, indent=2, sort_keys=True) + "\n"
        with open(a_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return 0
    except Exception as err:  # explicit fail-safe: log + fall back, never silent
        print(f"ratchet_union_merge: falling back to text merge for "
              f"{path_hint}: {err}", file=sys.stderr)
        return _fallback_text_merge(o_path, a_path, b_path)


if __name__ == "__main__":
    sys.exit(main())
