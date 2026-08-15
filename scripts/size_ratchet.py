"""Size ratchet: growth-only cap on file and function line counts for
airuleset's own source (#404 point 1 — "aplikovat vlastne pravidla na seba").

Snapshots today's sizes as CEILINGS in ``tests/size_ratchet.json``. A later
measurement may only equal or beat a stored ceiling, never exceed it —
shrinking or stagnation always passes; growth past a stored ceiling (or past
the flat default for a brand-new file/function) fails. See
``.claude/rules/internals-scripts.md`` (deep archive:
``.claude/rules-reference/internals-archive.md``) for the full design rationale (chosen approach + the rejected "test auto-writes on every
run" alternative).

This is DELIBERATELY strict: every currently-tracked file/function is frozen
at its EXACT current size on the day it is (re)bootstrapped — there is no
headroom up to the default cap for an already-tracked item, even one well
under the default. That is the ticket's own literal wording ("kazdy subor a
kazda funkcia moze len klesat alebo stagnovat") and the whole point of an
"immediate growth stop": genuinely deliberate growth is still possible, via
a human hand-editing tests/size_ratchet.json (raising ONE number) in the
same commit, with the reason stated in the commit/PR — a visible, reviewable
diff, never a silent default. JSON itself has no comment syntax, so that
justification belongs in the commit message, not in the JSON file.

Known, deliberately-accepted residual: because the snapshot is keyed by
PATH/qualname (not by content or git rename-tracking), renaming a tracked
file/function in the same commit it grows lets the growth land as a
"brand-new item" governed by the flat default cap rather than by the old,
possibly-tighter ceiling — i.e. a rename can incidentally regain headroom a
same-named edit would not have. This is an inherent limitation of any
name-keyed ratchet without semantic rename detection, considered out of
scope for this narrow, mechanical point-1 test (see the #404 design
comment); real semantic rename-tracking is real complexity that belongs to
a bigger structural investment, not this ticket.

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
    python3 scripts/size_ratchet.py --update --bootstrap
                                                # the ONE-TIME act of seeding
                                                # the ratchet from scratch;
                                                # refuses outright (no write
                                                # at all) unless the snapshot
                                                # is currently empty/missing
                                                # — never a repeatable way to
                                                # bless an oversized new item.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import tempfile
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

# #482: a BYTE cap for every path-scoped `.claude/rules/*.md`. Byte metric,
# not lines: the former airuleset-internals.md monolith was only 1575 lines
# but 973 KB (~616 B/line) and its `paths:` frontmatter matched nearly the
# whole repo, so a Read of almost any file injected ~240k tokens and killed
# the session. ~50 KB keeps any single auto-injected rule well under a
# session-threatening size. Unlike the .py ratchet above (freeze-at-current,
# code should only shrink), rule files are DESIGNED to accrete playbook
# lessons up to this flat CAP, then archive their oldest to the on-demand
# `.claude/rules-reference/` surface — so this is a cap, not a freeze.
RULE_BYTES_DEFAULT_CEILING = 51200

# Only a function already at/above this size at the moment a snapshot is
# (re)generated gets its OWN explicit ceiling entry. A function smaller than
# this is governed by FUNC_DEFAULT_CEILING alone until it crosses the
# threshold and someone runs --update — this keeps the JSON scoped to "the
# offenders" rather than every function in a ~50k-line tree.
FUNC_TRACK_THRESHOLD = 200

# A freshly-bootstrapped snapshot must pass check() immediately (day one can
# never itself be a violation) — that only holds if every untracked function
# under the threshold is also under the flat default it falls back to.
assert FUNC_TRACK_THRESHOLD <= FUNC_DEFAULT_CEILING, (
    "FUNC_TRACK_THRESHOLD must not exceed FUNC_DEFAULT_CEILING, or a "
    "freshly-bootstrapped function between the two would fail check() the "
    "moment it is measured, even though nothing has grown since bootstrap."
)

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


def tracked_rule_files(repo: Path = REPO) -> list:
    """Every path-scoped project rule this byte-cap governs (#482): a
    ``.claude/rules/*.md`` file that carries a ``paths:`` frontmatter key
    (i.e. one Claude Code auto-INJECTS when a matching file is read).
    Relative posix strings. The on-demand ``.claude/rules-reference/``
    archive has NO frontmatter and lives outside ``.claude/rules/``, so it
    is deliberately NOT tracked — it never auto-injects, so its size is
    harmless."""
    base = repo / ".claude" / "rules"
    if not base.is_dir():
        return []
    out = []
    for entry in sorted(base.glob("*.md")):
        try:
            head = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        stripped = head.lstrip()
        if not stripped.startswith("---"):
            continue
        parts = stripped.split("---", 2)
        if len(parts) < 3:
            continue
        # a frontmatter `paths:` key at the start of a line inside the block
        front = parts[1]
        if not any(ln.strip().startswith("paths:") for ln in front.splitlines()):
            continue
        out.append(entry.relative_to(repo).as_posix())
    return out


def rule_byte_counts(repo: Path = REPO) -> dict:
    """{relpath: byte_size} for every path-scoped rule file."""
    out = {}
    for rel in tracked_rule_files(repo):
        out[rel] = (repo / rel).stat().st_size
    return out


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

    A qualname can legitimately be defined more than once in real Python
    (a @property getter/setter pair, an @overload group, a try/except
    ImportError fallback def, a conditional def) — when that happens, the
    LARGEST of the colliding definitions' line counts is kept, never
    whichever happened to be textually last. This is deliberately
    conservative: it can only make the ratchet MORE sensitive to growth in
    that qualname, never less.
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
        size = end - node.lineno + 1
        out[qual] = max(out.get(qual, 0), size)
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
    return {"files": files, "functions": functions,
            "rule_bytes": rule_byte_counts(repo)}


def _validate_snapshot_shape(data: dict, source: str = "the snapshot") -> None:
    """Raise ValueError with an ACTIONABLE message on malformed snapshot
    data — the sanctioned way to raise a ceiling is a human hand-editing
    tests/size_ratchet.json, and a typo there (a quoted number, a null,
    a wrong-shaped value) must fail loudly and clearly, not as a raw
    TypeError three call-frames deep inside check()."""
    for kind in ("files", "functions", "rule_bytes"):
        items = data.get(kind, {})
        if not isinstance(items, dict):
            raise ValueError(
                f"{source}: {kind!r} must be an object of {{path: ceiling}}, "
                f"got {type(items).__name__}"
            )
        for key, ceiling in items.items():
            if not isinstance(key, str):
                raise ValueError(f"{source}: {kind!r} key {key!r} is not a string")
            if isinstance(ceiling, bool) or not isinstance(ceiling, int):
                raise ValueError(
                    f"{source}: {kind}[{key!r}] must be a plain integer line "
                    f"count, got {ceiling!r} ({type(ceiling).__name__})"
                )
            if ceiling < 0:
                raise ValueError(
                    f"{source}: {kind}[{key!r}] = {ceiling} — a ceiling cannot "
                    f"be negative"
                )


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    if not path.exists():
        return {"files": {}, "functions": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    data.setdefault("files", {})
    data.setdefault("functions", {})
    data.setdefault("rule_bytes", {})
    _validate_snapshot_shape(data, source=str(path))
    return data


def save_snapshot(data: dict, path: Path = SNAPSHOT_PATH) -> None:
    """Atomically write *data* to *path* — a killed/interrupted write must
    never leave the sole source of truth truncated or half-written."""
    ordered = {
        "files": dict(sorted(data.get("files", {}).items())),
        "functions": dict(sorted(data.get("functions", {}).items())),
        "rule_bytes": dict(sorted(data.get("rule_bytes", {}).items())),
    }
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ordered, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError as cleanup_err:
            print(
                f"size_ratchet: could not remove leftover tmp file "
                f"{tmp_name!r} after a failed write: {cleanup_err}",
                file=sys.stderr,
            )
        raise


def snapshot_is_empty(stored: dict) -> bool:
    """True when *stored* tracks nothing at all — the only state --bootstrap
    is ever allowed to act on."""
    return not stored.get("files") and not stored.get("functions")


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
        f"tests/size_ratchet.json (JSON has no comments — explain why in "
        f"the commit/PR message instead)."
    )


def _rule_bytes_violation(key: str, current: int, ceiling: int, is_new: bool) -> str:
    if is_new:
        return (
            f"RATCHET VIOLATION: new path-scoped rule {key} is {current} "
            f"bytes, exceeding the {ceiling}-byte cap. A rule this large "
            f"auto-injects into every session that touches a matching file "
            f"(#482) — move its oldest lessons to the on-demand "
            f".claude/rules-reference/ archive before tracking it."
        )
    return (
        f"RATCHET VIOLATION: path-scoped rule {key} grew to {current} bytes, "
        f"over its {ceiling}-byte cap. Move its oldest lessons to the "
        f".claude/rules-reference/ archive (#482) — or, if this growth is "
        f"genuinely deliberate and reviewed, raise the ceiling by hand in "
        f"tests/size_ratchet.json (explain why in the commit message)."
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
    stored_rb = stored.get("rule_bytes", {})
    for key, current in sorted(measured.get("rule_bytes", {}).items()):
        ceiling = stored_rb.get(key, RULE_BYTES_DEFAULT_CEILING)
        if current > ceiling:
            violations.append(
                _rule_bytes_violation(key, current, ceiling, key not in stored_rb)
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

    *bootstrap* is the explicit, ONE-TIME, deliberate act of seeding the
    ratchet from scratch: every currently-tracked file (and every function
    at/above FUNC_TRACK_THRESHOLD) is captured at TODAY's real size
    VERBATIM, with NO default-cap refusal — the whole point of a ratchet is
    to freeze the status quo as the ceiling, however large that status quo
    already is, rather than demanding an instant split before it can even
    be tracked. The default-cap refusal is a GOING-FORWARD policy for a
    genuinely new file/function that appears AFTER a real baseline already
    exists; it does not apply to the baseline itself.

    bootstrap=True is REFUSED OUTRIGHT (nothing written, *stored* returned
    unchanged) unless *stored* is currently empty — this is what makes it
    genuinely one-time rather than a repeatable way to silently bless an
    oversized new item into the snapshot. Re-tightening an already-seeded
    ratchet is plain --update (bootstrap=False); a genuine deliberate
    re-bootstrap needs the snapshot emptied first, which is itself a
    visible, reviewable diff.
    """
    if bootstrap and not snapshot_is_empty(stored):
        return (
            # mirror stored EXACTLY (its own key set, values deep-copied) —
            # a refused bootstrap writes stored back unchanged, so it must
            # neither add a rule_bytes key stored lacks nor drop one it has.
            {k: (dict(v) if isinstance(v, dict) else v) for k, v in stored.items()},
            [
                "REFUSED: --bootstrap requires an empty/missing snapshot "
                f"({len(stored.get('files', {}))} file(s) + "
                f"{len(stored.get('functions', {}))} function(s) already "
                "tracked). Bootstrap is a ONE-TIME act, not a repeatable way "
                "to add new items without the default-cap check — use plain "
                "--update to tighten an already-seeded ratchet."
            ],
        )
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
    # rule_bytes: flat CAP (not freeze-at-current) — a per-area rule file may
    # grow with playbook lessons up to its ceiling, then must archive its
    # oldest. Ceiling = human override if present (tighten or, with a stated
    # reason, loosen), else the default cap. Deleted rule files drop out
    # naturally (only measured keys are iterated).
    stored_rb = stored.get("rule_bytes", {})
    new_snapshot["rule_bytes"] = {
        key: stored_rb.get(key, RULE_BYTES_DEFAULT_CEILING)
        for key in sorted(measured.get("rule_bytes", {}))
    }
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
        help="Only with --update, and only on an empty/missing snapshot: the "
        "ONE-TIME act of seeding the ratchet from scratch — every tracked "
        "item captured at TODAY's real size verbatim, with no default-cap "
        "refusal. Refused outright on an already-seeded snapshot.",
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

    if args.bootstrap and not snapshot_is_empty(stored):
        n_files = len(stored.get("files", {}))
        n_funcs = len(stored.get("functions", {}))
        print(
            f"REFUSED: --bootstrap requires an empty/missing {SNAPSHOT_PATH}"
            f" ({n_files} file(s) + {n_funcs} function(s) already tracked). "
            f"Bootstrap is a ONE-TIME act — run plain --update to tighten an "
            f"already-seeded ratchet, or deliberately empty the snapshot "
            f"first if a genuine re-bootstrap is truly intended."
        )
        return 1

    new_snapshot, refusals = regen(measured, stored, bootstrap=args.bootstrap)
    for r in refusals:
        print(r)
    if refusals:
        print(
            "The refused item(s) above were NOT added to the snapshot; "
            "split them first, then re-run --update."
        )
    save_snapshot(new_snapshot)
    print(f"size ratchet written: {SNAPSHOT_PATH}")
    return 1 if refusals else 0


if __name__ == "__main__":
    sys.exit(main())
