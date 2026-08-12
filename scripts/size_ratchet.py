"""Size ratchet: growth-only cap on file and function line counts for
airuleset's own source (#404 point 1 — "aplikovat vlastne pravidla na seba").

Snapshots today's sizes as CEILINGS in ``tests/size_ratchet.json``. A later
measurement may only equal or beat a stored ceiling, never exceed it —
shrinking or stagnation always passes; growth past a stored ceiling (or past
the flat default for a brand-new file/function) fails. See
``.claude/rules/airuleset-internals.md``'s ratchet section for the full
design rationale (chosen approach + the rejected "test auto-writes on every
run" alternative).

CLI:
    python3 scripts/size_ratchet.py --check    # same comparison the test
                                                # runs; read-only, prints
                                                # violations, non-zero exit
                                                # on any
    python3 scripts/size_ratchet.py --update   # tighten ceilings for
                                                # anything that shrank; add
                                                # new items within their
                                                # default cap; NEVER raises
                                                # an existing ceiling.
                                                # Refuses (non-zero exit) a
                                                # brand-new file/function
                                                # that already exceeds its
                                                # own default cap — split it
                                                # before it is ever tracked.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = REPO / "tests" / "size_ratchet.json"

# Default ceiling for anything NOT yet in the snapshot (a brand-new file or
# function nobody has run --update for yet). The file default matches the
# target-repo standard this ticket itself cites (camera-box #234's own
# ~1000-line/file cap, `architecture-first.md`); the function default is
# tighter since a single 1000-line function is a design smell even inside a
# file that hasn't yet crossed its own cap.
FILE_DEFAULT_CEILING = 1000
FUNC_DEFAULT_CEILING = 300

# Only a function already at/above this size at the moment a snapshot is
# (re)generated gets its OWN explicit ceiling entry. A function smaller than
# this is governed by FUNC_DEFAULT_CEILING alone until it crosses the
# threshold and someone runs --update — this keeps the JSON scoped to "the
# offenders" rather than every function in a ~50k-line tree.
FUNC_TRACK_THRESHOLD = 200

# Tracked path set — deliberately EXACTLY the ticket's own "at minimum"
# list. hooks/*.py and scripts/*.py (this file included) are NOT tracked:
# widening scope beyond the ticket's explicit list is its own decision, not
# something to do silently while shipping the mechanism itself.
TRACKED_DIRS = ("watchdog", "notify", "filedrop", "burn")

# Files/functions whose measured size is a genuine artifact of something
# other than "code that grew" (a vendored/generated blob, an embedded
# resource counted as file bytes but not as any function's own logic).
# Empty by design — see the #404 design comment: every large embedded-
# script constant in this repo (CLAUDE_HISTORY_SCRIPT_CONTENT,
# CAVEMAN_SHIM_CONTENT, CLAUDE_LAUNCH_SCRIPT_CONTENT, REMOTE_HOSTS) is a
# MODULE-LEVEL assignment, never inside a function body, so none of them
# distort per-function measurement, and each is an honest part of its
# file's real on-disk size, not a measurement artifact to hide. Add an
# entry here ONLY with a comment naming the specific, genuine distortion.
EXEMPT_FILES: set = set()
EXEMPT_FUNCTIONS: set = set()


def tracked_files(repo: Path = REPO) -> list:
    """Every path this ratchet governs, relative to *repo*, as posix strings."""
    out = []
    for entry in sorted(repo.glob("*.py")):
        out.append(entry.relative_to(repo).as_posix())
    for d in TRACKED_DIRS:
        base = repo / d
        if not base.is_dir():
            continue
        for entry in sorted(base.rglob("*.py")):
            out.append(entry.relative_to(repo).as_posix())
    tests_dir = repo / "tests"
    if tests_dir.is_dir():
        for entry in sorted(tests_dir.glob("*.py")):
            out.append(entry.relative_to(repo).as_posix())
    return [p for p in out if p not in EXEMPT_FILES]


def file_line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def _walk_functions(tree: ast.AST, prefix: str = ""):
    """Yield (qualname, node) for every def/async def in *tree*, dotted like
    CPython's own __qualname__ (Class.method, outer.<locals>.inner)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{prefix}{node.name}" if prefix else node.name
            yield qual, node
            yield from _walk_functions(node, prefix=f"{qual}.<locals>.")
        elif isinstance(node, ast.ClassDef):
            qual = f"{prefix}{node.name}" if prefix else node.name
            yield from _walk_functions(node, prefix=f"{qual}.")
        else:
            yield from _walk_functions(node, prefix=prefix)


def function_line_counts(path: Path) -> dict:
    """{qualname: line_count} for every function/method defined in *path*.

    Uses ``ast``, never regex — a string literal or comment containing the
    text "def " (a docstring, an embedded script) can never be mistaken for
    a real function definition.
    """
    with path.open("r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return {}
    out = {}
    for qual, node in _walk_functions(tree):
        end = getattr(node, "end_lineno", None)
        if end is None:
            continue
        out[qual] = end - node.lineno + 1
    return out


def measure(repo: Path = REPO) -> dict:
    """{"files": {relpath: n}, "functions": {"relpath::qualname": n}}"""
    files = {}
    functions = {}
    for rel in tracked_files(repo):
        path = repo / rel
        files[rel] = file_line_count(path)
        for qual, n in function_line_counts(path).items():
            key = f"{rel}::{qual}"
            if key in EXEMPT_FUNCTIONS:
                continue
            functions[key] = n
    return {"files": files, "functions": functions}


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    if not path.exists():
        return {"files": {}, "functions": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("files", {})
    data.setdefault("functions", {})
    return data


def save_snapshot(data: dict, path: Path = SNAPSHOT_PATH) -> None:
    ordered = {
        "files": dict(sorted(data.get("files", {}).items())),
        "functions": dict(sorted(data.get("functions", {}).items())),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, sort_keys=True)
        f.write("\n")


def _violation(kind: str, key: str, current: int, ceiling: int, is_new: bool) -> str:
    what = "file" if kind == "files" else "function"
    label = key if kind == "files" else key.replace("::", " in ") + "()"
    if is_new:
        return (
            f"RATCHET VIOLATION: new {what} {label} is {current} lines, "
            f"exceeding the day-one cap of {ceiling} lines. Split it into "
            f"smaller {'modules' if kind == 'files' else 'helper functions'} "
            f"(architecture-first.md) before it ever lands, rather than "
            f"tracking it at this size."
        )
    return (
        f"RATCHET VIOLATION: {what} {label} grew from {ceiling} to "
        f"{current} lines (ceiling {ceiling}). "
        f"{'Split this file into smaller per-service modules' if kind == 'files' else 'Extract new logic into a smaller helper function instead of growing this one further'} "
        f"(architecture-first.md) — or, if this growth is genuinely "
        f"deliberate and reviewed, raise the ceiling by hand in "
        f"tests/size_ratchet.json with a comment explaining why."
    )


def check(measured: dict, stored: dict) -> list:
    """Compare *measured* (from measure()) against *stored* (from
    load_snapshot()). Returns a list of violation messages — empty means
    clean. Never mutates either argument."""
    violations = []
    for kind, default_ceiling in (
        ("files", FILE_DEFAULT_CEILING),
        ("functions", FUNC_DEFAULT_CEILING),
    ):
        stored_items = stored.get(kind, {})
        for key, current in sorted(measured.get(kind, {}).items()):
            if key in stored_items:
                ceiling = stored_items[key]
                if current > ceiling:
                    violations.append(_violation(kind, key, current, ceiling, False))
            else:
                if current > default_ceiling:
                    violations.append(
                        _violation(kind, key, current, default_ceiling, True)
                    )
    return violations


def regen(measured: dict, stored: dict, bootstrap: bool = False):
    """Recompute a tightened snapshot from *measured* against *stored*.

    Returns (new_snapshot, refusals). An existing ceiling is NEVER raised —
    only ever left unchanged or lowered (min(stored, current)). A
    brand-new file/function that already exceeds its own default cap is
    REFUSED (not silently added at its own oversized value); refusals is a
    list of human-readable reasons for each. A file/function present in
    *stored* but no longer found in *measured* (deleted or renamed) is
    pruned from the returned snapshot.

    *bootstrap* is the explicit, one-time, deliberate act of seeding the
    ratchet from scratch: every currently-tracked file (and every function
    at/above FUNC_TRACK_THRESHOLD) is captured at TODAY's real size
    VERBATIM, with NO default-cap refusal — the whole point of a ratchet is
    to freeze the status quo as the ceiling, however large that status quo
    already is, rather than demanding an instant split before it can even
    be tracked. The default-cap refusal is a GOING-FORWARD policy for a
    genuinely new file/function that appears AFTER a real baseline already
    exists; it does not apply to the baseline itself. Never inferred from
    an empty *stored* dict — always explicit, so a corrupted/emptied JSON
    can never silently re-bootstrap with lenient defaults.
    """
    new_snapshot = {"files": {}, "functions": {}}
    refusals = []
    for kind, default_ceiling, track_threshold in (
        ("files", FILE_DEFAULT_CEILING, 0),
        ("functions", FUNC_DEFAULT_CEILING, FUNC_TRACK_THRESHOLD),
    ):
        stored_items = stored.get(kind, {})
        for key, current in sorted(measured.get(kind, {}).items()):
            if key in stored_items:
                new_snapshot[kind][key] = min(stored_items[key], current)
                continue
            if current < track_threshold:
                # Not yet big enough to warrant its own explicit entry;
                # stays governed by the flat default ceiling.
                continue
            if bootstrap:
                new_snapshot[kind][key] = current
                continue
            if current > default_ceiling:
                what = "file" if kind == "files" else "function"
                refusals.append(
                    f"REFUSED: new {what} {key} is {current} lines, "
                    f"exceeding the day-one cap of {default_ceiling} lines. "
                    f"Split it before adding it to the ratchet."
                )
                continue
            new_snapshot[kind][key] = current
    return new_snapshot, refusals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check", action="store_true", help="Read-only: print violations, if any."
    )
    group.add_argument(
        "--update",
        action="store_true",
        help="Tighten ceilings that shrank; add new items within the default "
        "cap; never raises an existing ceiling; refuses an oversized new item.",
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Only with --update: the one-time, explicit act of seeding the "
        "ratchet from scratch — every tracked item is captured at TODAY's "
        "real size verbatim, with no default-cap refusal. Never inferred "
        "automatically; must be passed by hand.",
    )
    args = parser.parse_args(argv)

    if args.bootstrap and not args.update:
        parser.error("--bootstrap only makes sense together with --update")

    measured = measure()
    stored = load_snapshot()

    if args.check:
        violations = check(measured, stored)
        if violations:
            for v in violations:
                print(v)
            return 1
        print("size ratchet: clean — nothing exceeds its ceiling.")
        return 0

    new_snapshot, refusals = regen(measured, stored, bootstrap=args.bootstrap)
    if refusals:
        for r in refusals:
            print(r)
        print(
            "size_ratchet.json NOT updated for the refused item(s) above — "
            "split them first, then re-run --update."
        )
    save_snapshot(new_snapshot)
    print(f"size ratchet updated: {SNAPSHOT_PATH}")
    return 1 if refusals else 0


if __name__ == "__main__":
    sys.exit(main())
