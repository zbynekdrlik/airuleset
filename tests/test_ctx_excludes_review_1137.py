"""#1137 review round: the gk lens excludes must never abort an install, must
resolve a window cwd against the ACCOUNT's home, must never strip the review
window that shares a cwd, and must also cover worktree lanes of a non-review
window. Plus: no session-scoped host remains (the #1224 module is gone).
"""
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_context_excludes as cx  # noqa: E402
import cli_fleet  # noqa: E402

HOME = "/home/gatekeeper"
SUFFIX = cx.LENS_SUFFIX


def _apply(windows, settings=None, raises=None):
    bw = mock.patch.object(cli_fleet, "box_windows",
                           side_effect=raises) if raises else \
        mock.patch.object(cli_fleet, "box_windows", return_value=windows)
    with bw, mock.patch.object(cx, "_reduced_account", return_value=False), \
            mock.patch.object(cx, "_account_home", return_value=HOME), \
            mock.patch.dict(os.environ, {"HOME": "/somewhere/else"}):
        return cx.apply(json.loads(json.dumps(settings or {})))


class TestRobust(unittest.TestCase):
    def test_box_windows_error_never_aborts(self):
        out = _apply(None, {"claudeMdExcludes": ["/keep"]}, raises=RuntimeError("x"))
        self.assertEqual(out["claudeMdExcludes"], ["/keep"])

    def test_malformed_windows_are_skipped(self):
        out = _apply([None, "x", {"cwd": 5, "role": "infra"}, {"role": "infra"}])
        self.assertNotIn("claudeMdExcludes", out)


class TestHome(unittest.TestCase):
    def test_tilde_and_relative_cwd_use_the_account_home(self):
        out = _apply([{"cwd": "~/devel/a", "role": "infra"},
                      {"cwd": "devel/b", "role": "quality"}])
        ex = out["claudeMdExcludes"]
        self.assertIn(HOME + "/devel/a" + SUFFIX, ex)
        self.assertIn(HOME + "/devel/b" + SUFFIX, ex)

    def test_account_home_comes_from_pwd(self):
        with mock.patch("pwd.getpwnam", return_value=SimpleNamespace(pw_dir="/h/gk")), \
                mock.patch("airuleset._current_user", return_value="gatekeeper"):
            self.assertEqual(cx._account_home(), "/h/gk")


class TestShared(unittest.TestCase):
    def test_review_cwd_is_never_excluded(self):
        out = _apply([{"cwd": "~/devel/x", "role": "review"},
                      {"cwd": "~/devel/x", "role": "infra"}])
        self.assertNotIn("claudeMdExcludes", out)


class TestLanes(unittest.TestCase):
    def test_worktree_lanes_of_a_non_review_window_are_covered(self):
        ex = _apply([{"cwd": "~/devel/i", "role": "infra"}])["claudeMdExcludes"]
        self.assertEqual(ex, [HOME + "/devel/i" + SUFFIX,
                              HOME + "/devel/i/.claude/worktrees/**" + SUFFIX])


class TestScopeHosts(unittest.TestCase):
    def test_no_session_scope_module_remains(self):
        # owner 25.10.2026: no Tier-0-style limit on any workstation, dev2
        # included, so the #1224 scope module is gone (no host left).
        self.assertFalse((REPO / "cli_session_scope.py").exists())


if __name__ == "__main__":
    unittest.main()
