#!/usr/bin/env python3
"""Collected-count guard for the hermetic-subset CI pytest job (#683).

test-strictness.md bans a no-op test job: "a CI test job that cannot execute
real tests MUST fail, not silently pass". The hermetic subset is selected by
subtracting a box-bound deny-list from the full suite (see
``.github/workflows/ci.yml`` + ``.github/box-bound-tests.txt``). Two silent
failure modes this guard closes:

  1. The deny-list generation mis-globs and ``--ignore``s every file, so pytest
     collects ZERO tests. pytest exits 5 ("no tests collected") which IS
     non-zero, but the workflow's own ``run:`` chain could mask it; this guard
     re-asserts it EXPLICITLY from the junit artifact so the intent is legible
     and locked, not implicit in an exit code.
  2. The deny-list silently swallows a large fraction of the suite (a bad glob
     that matched a whole directory, a merge that duplicated entries). A bare
     ``> 0`` check would pass with a single surviving test. So the guard takes
     a FLOOR: the run must collect at least ``floor`` tests. The floor is set
     WELL BELOW the real hermetic count (generous slack) so ordinary suite
     growth/shrink and incremental deny-list maintenance never trip it — it
     catches only catastrophic mis-selection.

Reads the junit XML pytest wrote (``--junitxml``). Dependency-free (stdlib
``xml.etree`` only) so it runs in the bare ``python:3.12`` CI container with no
extra install. Non-zero exit on violation, with a loud message.

Usage:
    python3 scripts/ci_assert_collected.py <junit.xml> <floor>
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def collected_count(junit_path: str) -> int:
    """Return the number of test cases pytest recorded in the junit XML.

    pytest writes a top-level ``<testsuite tests="N" ...>`` (wrapped in
    ``<testsuites>`` in newer pytest). Sum the ``tests`` attribute across every
    ``testsuite`` element so both shapes work; a testcase-count fallback covers
    a malformed header.
    """
    root = ET.parse(junit_path).getroot()
    suites = root.iter("testsuite")
    total = 0
    saw_attr = False
    for suite in suites:
        val = suite.get("tests")
        if val is not None:
            saw_attr = True
            total += int(val)
    if not saw_attr:
        total = sum(1 for _ in root.iter("testcase"))
    return total


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: ci_assert_collected.py <junit.xml> <floor>",
              file=sys.stderr)
        return 2
    junit_path, floor_s = argv[1], argv[2]
    try:
        floor = int(floor_s)
    except ValueError:
        print("floor must be an integer, got %r" % floor_s, file=sys.stderr)
        return 2
    try:
        n = collected_count(junit_path)
    except (OSError, ET.ParseError, ValueError) as exc:
        # ValueError covers a malformed non-integer tests="" attribute — keep
        # the loud BLOCKED message instead of an uncaught traceback (#683 review 🔵-3).
        print("BLOCKED: cannot read junit XML %r: %s — the hermetic pytest job "
              "produced no parseable result, treated as a no-op FAILURE (#683)."
              % (junit_path, exc), file=sys.stderr)
        return 1
    if n < floor:
        print("BLOCKED: hermetic pytest collected %d test(s), below the floor "
              "of %d (#683). A no-op or catastrophically-reduced test job is "
              "banned (test-strictness.md) — the box-bound deny-list "
              "(.github/box-bound-tests.txt) likely over-matched. Investigate "
              "before trusting main-green." % (n, floor), file=sys.stderr)
        return 1
    print("OK: hermetic pytest collected %d test(s) (floor %d)." % (n, floor))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
