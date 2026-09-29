"""Module-level fixture: hold the #1184 account gate open for onboarding tests.

The per-project account gate (``cli_accounts.onboard_account_gate``) is an
ALLOW-list of DECLARED project accounts and has its own tests in
test_project_accounts_1184.py. The onboarding-MECHANICS suites
(test_onboard_project / _review_fixes / _remote_583) run on tmp dirs owned by
the test runner, so they import ``setUpModule`` / ``tearDownModule`` from here
to hold the gate open for their module only — never in production code.
"""
from unittest import mock

_ACCOUNT_GATE = mock.patch("cli_accounts.onboard_account_gate", return_value=None)


def setUpModule():
    _ACCOUNT_GATE.start()


def tearDownModule():
    _ACCOUNT_GATE.stop()
