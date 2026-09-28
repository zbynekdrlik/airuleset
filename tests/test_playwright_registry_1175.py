"""#1175: airuleset's per-user Playwright cleanup must never remove a chromium
build that another project's live playwright install still needs.

The per-user cache `~/.cache/ms-playwright` is SHARED by every project on the
account. playwright records who needs what in `.links/<hash>`: each file holds
the path of a playwright-core package whose `browsers.json` lists the revisions
it needs. On dev1 (2026-09-28) every push removed fohmixer's chromium-1208 and
iemmixer's chromium-1243 although both were registered there.
"""

import json
import tempfile
import unittest
from pathlib import Path

import cli_playwright_mcp as p


def _cache_with_pinned(root):
    b = p.PLAYWRIGHT_CHROMIUM_BUILD
    d = Path(root) / ".cache" / "ms-playwright"
    d.mkdir(parents=True)
    for half in ("chromium-" + b, "chromium_headless_shell-" + b):
        (d / half).mkdir()
        (d / half / "INSTALLATION_COMPLETE").write_text("")
    return d


def _project(root, name, revision):
    """A fake project with a playwright-core package needing `revision`."""
    pkg = Path(root) / name / "node_modules" / "playwright-core"
    pkg.mkdir(parents=True)
    (pkg / "browsers.json").write_text(json.dumps({"browsers": [
        {"name": "chromium", "revision": revision},
        {"name": "chromium-headless-shell", "revision": revision},
        {"name": "chromium-tip-of-tree", "revision": "1401"},
    ]}))
    return pkg


def _link(cache, key, target):
    links = cache / ".links"
    links.mkdir(exist_ok=True)
    (links / key).write_text(str(target))


class TestCleanupRespectsPlaywrightRegistry1175(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.cache = _cache_with_pinned(self.root)

    def _names(self):
        return sorted(x.name for x in self.cache.iterdir())

    def test_a_build_a_live_project_needs_is_kept(self):
        for half in ("chromium-1208", "chromium_headless_shell-1208"):
            (self.cache / half).mkdir()
        _link(self.cache, "fohmixer", _project(self.root, "fohmixer", "1208"))
        p._cleanup_old_builds(self.cache, live_check=lambda d: False)
        self.assertIn("chromium-1208", self._names())
        self.assertIn("chromium_headless_shell-1208", self._names())

    def test_an_unregistered_superseded_build_is_still_removed(self):
        for half in ("chromium-1234", "chromium_headless_shell-1234"):
            (self.cache / half).mkdir()
        _link(self.cache, "fohmixer", _project(self.root, "fohmixer", "1208"))
        p._cleanup_old_builds(self.cache, live_check=lambda d: False)
        self.assertNotIn("chromium-1234", self._names())
        self.assertNotIn("chromium_headless_shell-1234", self._names())

    def test_a_stale_link_whose_package_is_gone_protects_nothing(self):
        (self.cache / "chromium-1234").mkdir()
        _link(self.cache, "gone", Path(self.root) / "deleted" / "playwright-core")
        p._cleanup_old_builds(self.cache, live_check=lambda d: False)
        self.assertNotIn("chromium-1234", self._names())

    def test_an_unreadable_registry_removes_nothing(self):
        (self.cache / "chromium-1234").mkdir()
        pkg = Path(self.root) / "broken" / "playwright-core"
        pkg.mkdir(parents=True)
        (pkg / "browsers.json").write_text("{not json")
        _link(self.cache, "broken", pkg)
        p._cleanup_old_builds(self.cache, live_check=lambda d: False)
        self.assertIn("chromium-1234", self._names())


if __name__ == "__main__":
    unittest.main()
