"""#1373 review: a pinned `cargo:` tool always reinstalls (#1275 `_cargo_lines`), and a
box has ONE `/opt/rust/cargo/bin`, so two accounts on one host pinning the same crate
to different versions would replace each other on every root render. validate_tools
refuses it, the same way it refuses a pipx or binaryen clash."""
import unittest

import cli_account_bootstrap as bootstrap
import cli_project_toolchain as tc


class CargoPinsPerHost(unittest.TestCase):
    OTHER = {"x": {"host": "dev1", "tools": ["cargo:cargo-nextest@0.9.150"]}}

    def test_a_different_pin_on_the_same_host_is_refused(self):
        errs = tc.validate_tools({"host": "dev1", "tools": ["cargo:cargo-nextest@0.9.146"]},
                                 "me", self.OTHER)
        self.assertTrue([e for e in errs if "cargo:cargo-nextest@0.9.146 conflicts with x" in e],
                        errs)

    def test_same_pin_unpinned_or_other_host_is_fine(self):
        for spec in ({"host": "dev1", "tools": ["cargo:cargo-nextest@0.9.150"]},
                     {"host": "dev1", "tools": ["cargo:cargo-nextest"]},
                     {"host": "dev2", "tools": ["cargo:cargo-nextest@0.9.146"]},
                     {"host": "dev1", "tools": ["cargo:trunk@0.21.0"]}):
            self.assertEqual(tc.validate_tools(spec, "me", self.OTHER), [], spec)

    def test_the_account_itself_is_not_its_own_sibling(self):
        self.assertEqual(tc.validate_tools(
            {"host": "dev1", "tools": ["cargo:cargo-nextest@0.9.146"]}, "x", self.OTHER), [])

    def test_every_declared_account_is_still_valid(self):
        for acct in bootstrap.SERVICE_ACCOUNTS:
            self.assertEqual(bootstrap.validate_account(acct, bootstrap.account_spec(acct)),
                             [], acct)


if __name__ == "__main__":
    unittest.main()
