"""#1137 — pin the PARALLEL concurrency mode for a suite that drives the
parallel-only machinery (the job-20 lane-occupancy refill nudge, the
queue-arrival nudge, the resource lane caps).

Since #1137 (owner ROZHODNUTÉ 2026-09-30: "seqvencny normal mod nech je default
stav vsetkych targetov") `cli_concurrency.DEFAULT_MODE` is ``sequential`` on
every box, and a pane reaches ``parallel`` ONLY through an explicit
``mode: parallel`` declaration. The suites that exercise the parallel machinery
drive a FAKE cwd (no project file can be written there), so they model a
declared-parallel pane by pinning the resolver's default for the test's
duration. The pin goes through the ONE resolver every consumer reads, so the
code under test is unchanged; the sequential default itself is locked in
``tests/test_concurrency_998.py``.
"""
import unittest.mock as m

import cli_concurrency


def pin_parallel(testcase):
    """Resolve an undeclared pane as ``parallel`` until ``testcase`` ends."""
    patcher = m.patch.object(cli_concurrency, "DEFAULT_MODE", "parallel")
    patcher.start()
    testcase.addCleanup(patcher.stop)


#: Class decorator form (patches every ``test*`` method of a TestCase): the
#: one-line pin for a whole parallel-machinery suite.
PARALLEL_PIN = m.patch.object(cli_concurrency, "DEFAULT_MODE", "parallel")
