#!/usr/bin/env python3
"""Turn the box-bound deny-list into pytest args for the hermetic CI job (#683).

The hermetic subset = the whole ``tests/`` suite MINUS a maintained deny-list
of tests that cannot run in the bare ``python:3.12`` CI container (no tmux, no
~/.claude, fresh HOME, uid 0). The deny-list is DATA
(``.github/box-bound-tests.txt``), not code, so maintenance is a reviewable diff.

Two entry shapes, distinguished purely by whether the line contains ``::``:

  * a bare FILE path (``tests/test_x.py``)         -> ``--ignore=tests/test_x.py``
    Used for files that ERROR AT COLLECTION (e.g. a module-level ``tmux``
    call) — a collection error aborts the whole run, so the file must be
    dropped before collection, which ``--deselect`` (a post-collection
    filter) cannot do.
  * a test NODE-ID (``tests/test_x.py::Class::method``) -> ``--deselect=...``
    Used for individual box-bound tests scattered inside otherwise-hermetic
    files, so the file's hermetic tests still run.

Blank lines and ``#`` comment lines are ignored. Prints ONE arg per line
(nothing if the deny-list is empty) so the caller can read them into a bash
array shellcheck-cleanly:
``mapfile -t ARGS < <(python3 scripts/ci_pytest_args.py <denylist>)`` then
``pytest tests/ "${ARGS[@]}"``. Dependency-free stdlib so it runs in the bare
CI container.

Usage:
    python3 scripts/ci_pytest_args.py <denylist-path>
"""
from __future__ import annotations

import sys


def parse_denylist(text: str) -> tuple[list[str], list[str]]:
    """Return ``(ignore_files, deselect_nodeids)`` from a deny-list file body.

    A line is a deselect node-id iff it contains ``::``; otherwise it is an
    ignore file path. Comments (``#`` after optional whitespace) and blank
    lines are skipped. An inline trailing ``  # reason`` comment is stripped
    so entries may be annotated on their own line.
    """
    ignore_files: list[str] = []
    deselect: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # strip an inline trailing comment (kept simple: " #" splits it)
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        if not line:
            continue
        if "::" in line:
            deselect.append(line)
        else:
            ignore_files.append(line)
    return ignore_files, deselect


def build_args(text: str) -> list[str]:
    ignore_files, deselect = parse_denylist(text)
    args: list[str] = []
    for f in ignore_files:
        args.append("--ignore=" + f)
    for nid in deselect:
        args.append("--deselect=" + nid)
    return args


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: ci_pytest_args.py <denylist-path>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print("cannot read deny-list %r: %s" % (argv[1], exc), file=sys.stderr)
        return 2
    args = build_args(text)
    if args:
        print("\n".join(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
