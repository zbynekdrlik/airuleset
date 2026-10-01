"""#1201: a hermetic ``/etc/airuleset/project-accounts`` for tests.

A project account is a declared account whose root render wrote its marker
(``cli_account_hardening.MARKER_DIR/<account>``). Tests that exercise a project
account seed the markers here instead of reading the box's real ``/etc``.
"""
import os
import tempfile
from unittest import mock

import cli_account_bootstrap
import cli_account_hardening

# The hook's test-only override (honoured only under PYTEST_CURRENT_TEST).
ENV = "AIRULESET_TEST_PROJECT_MARKER_DIR"


def write_markers(directory, accounts=None):
    """One marker per account in ``directory`` (default: every declared one)."""
    names = cli_account_bootstrap.SERVICE_ACCOUNTS if accounts is None else accounts
    for name in names:
        with open(os.path.join(directory, name), "w") as fh:
            fh.write("bootstrapped by a test\n")
    return directory


def seed(testcase, accounts=None):
    """Point ``cli_account_hardening.MARKER_DIR`` at a seeded temp dir for the
    rest of ``testcase``; returns the dir."""
    tmp = tempfile.TemporaryDirectory(prefix="project-markers-")
    testcase.addCleanup(tmp.cleanup)
    write_markers(tmp.name, accounts)
    patcher = mock.patch.object(cli_account_hardening, "MARKER_DIR", tmp.name)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return tmp.name
