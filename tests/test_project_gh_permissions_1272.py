"""#1272: a repo token carries a declared scope; `statuses: write` goes to the
account that declares it, not to everything the App holds (owner 31.10.2026)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_project_gh_token as pgt  # noqa: E402


class TestScope(unittest.TestCase):

    def test_only_iemmixer_writes_statuses(self):
        writers = [a for a in pgt.github_app_accounts()
                   if pgt.project_permissions(bootstrap.account_spec(a))["statuses"] == "write"]
        self.assertEqual(writers, ["iemmixer"])

    def test_the_rest_of_the_scope_stays_equal(self):
        got = pgt.project_permissions({"statuses_write": True})
        self.assertEqual({**got, "statuses": "read"}, pgt.project_permissions({}))

    def test_no_administration_and_no_secrets(self):
        self.assertNotIn("administration", pgt.PROJECT_PERMISSIONS)
        self.assertNotIn("secrets", pgt.PROJECT_PERMISSIONS)


if __name__ == "__main__":
    unittest.main()
