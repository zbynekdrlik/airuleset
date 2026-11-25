"""#1399 (david3 25.11.2026): a fork clone whose remotes use ssh host aliases
(`git@github-fork:` / `git@github-upstream:`) must still be read as GitHub
remotes, so the release-state range uses `upstream/*` and `M`/`C` work."""
import unittest

import cli_release_state as rs
from watchdog import cross_stream


class AliasRemotes(unittest.TestCase):

    def test_slug_regex_reads_ssh_aliases(self):
        for url, want in (
                ("git@github-fork:kvaskodev/odoo-erp.git", "kvaskodev/odoo-erp"),
                ("git@github-upstream:zbynekdrlik/odoo-erp.git", "zbynekdrlik/odoo-erp"),
                ("ssh://git@github-fork/kvaskodev/odoo-erp.git", "kvaskodev/odoo-erp"),
                ("git@github.com:zbynekdrlik/odoo-erp.git", "zbynekdrlik/odoo-erp"),
                ("https://github.com/zbynekdrlik/odoo-erp", "zbynekdrlik/odoo-erp")):
            with self.subTest(url=url):
                m = rs._GITHUB_SLUG_RE.search(url)
                self.assertIsNotNone(m)
                self.assertEqual(m.group(1), want)

    def test_non_github_hosts_stay_unread(self):
        for url in ("git@gitlab.com:a/b.git", "git@mybox:a/b.git",
                    "https://example.com/a/b"):
            with self.subTest(url=url):
                self.assertIsNone(rs._GITHUB_SLUG_RE.search(url))

    def test_cross_stream_repo_name_reads_aliases(self):
        self.assertEqual(cross_stream._parse_github_repo_name(
            "git@github-fork:kvaskodev/odoo-erp.git"), "odoo-erp")
        self.assertIsNone(cross_stream._parse_github_repo_name("git@mybox:a/b.git"))

    def test_fork_with_alias_remotes_picks_upstream(self):
        urls = {"origin": "git@github-fork:kvaskodev/odoo-erp.git",
                "upstream": "git@github-upstream:zbynekdrlik/odoo-erp.git"}

        def remote_fn(root, name):
            m = rs._GITHUB_SLUG_RE.search(urls[name])
            return m.group(1) if m else None

        origin_slug = remote_fn("/r", "origin")
        got = rs._canonical_ref_prefix("/r", "zbynekdrlik/odoo-erp", origin_slug,
                                       remote_fn, lambda root, ref: True)
        self.assertEqual(got, ("upstream", None))


class NoPullRequestRead(unittest.TestCase):
    """Live 25.11.: the david* App token has no pull_requests:read, gh answers
    'Resource not accessible by integration (HTTP 403)'. That is a missing
    permission, not a quota: say so, so nobody waits for a reset."""

    def test_meta_fn_maps_integration_403_to_no_access(self):
        from unittest import mock
        fake = mock.Mock(returncode=1, stdout="", stderr=(
            "gh: Resource not accessible by integration (HTTP 403)"))
        with mock.patch.object(rs.subprocess, "run", return_value=fake):
            self.assertIs(rs._default_pr_meta_fn("o/r")(5), rs.NO_PR_ACCESS)
        fake.stderr = "gh: API rate limit exceeded (HTTP 403)"
        with mock.patch.object(rs.subprocess, "run", return_value=fake):
            self.assertIs(rs._default_pr_meta_fn("o/r")(5), rs.QUOTA)


if __name__ == "__main__":
    unittest.main()
