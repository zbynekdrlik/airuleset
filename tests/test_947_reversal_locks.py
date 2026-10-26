"""Tests locking the #947 reversal — no session-restart action exists.

Owner directive 2026-09-10: "regresia je ze nieco nam pcha exit do vsetkych
targetov a ked prideme rano je vsetko seknute … Ja som vobec taketo random
existovanie target claude urcite nikdy neschvalil a som proti tomu aby sa
nieco take dialo!!!"

The reversal deleted the action path (session_restart.py: /exit + claude
--continue) and left Job 46 as a passive journal-only health observation
(session_health_observe.py). #1137 (2026-10-26) deleted that too — nothing
read its journal line — so the lock is now ABSENCE: neither module exists,
no watchdog module defines the exit/relaunch executors, and run_once
registers no session-restart / session-health job. The repo-wide AST scan
for a literal `/exit` payload lives in tests/test_no_session_kill.py.

Uses `unittest.TestCase` so the `unittest discover` push gate runs it too.
"""

import contextlib
import inspect
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd            # noqa: E402
from cli_filedrop_watchdog import cleanup_session_restart_dropins  # noqa: E402

_GONE_MODULES = ("session_restart.py", "session_health_observe.py")


class TestRestartModulesGone(unittest.TestCase):
    def test_neither_module_exists(self):
        present = [n for n in _GONE_MODULES
                   if (REPO / "watchdog" / n).exists()]
        self.assertEqual(present, [], "a #947 session-restart / Job 46 "
                         "session-health module is back (#1137)")

    def test_no_exit_or_relaunch_executor_anywhere(self):
        for py in sorted((REPO / "watchdog").glob("*.py")):
            src = py.read_text(encoding="utf-8")
            for fn in ("def execute_exit", "def execute_relaunch"):
                self.assertNotIn(fn, src, "%s in %s (#947)" % (fn, py.name))


class TestNoDropinProvisioningInInstall(unittest.TestCase):
    """cmd_install must not render a session-restart drop-in."""

    def test_no_render_or_setup_session_restart_dropin(self):
        src = (REPO / "cli_filedrop_watchdog.py").read_text(encoding="utf-8")
        for fn in ("def render_session_restart_dropin",
                   "def setup_session_restart_dropin"):
            self.assertNotIn(fn, src, "%s still exists (#947)" % fn)


class TestDropinCleanup(unittest.TestCase):
    """install removes the stale #947 drop-ins (moved from the retired
    tests/test_session_health_observe.py, #1137)."""

    def _paths(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return (Path(d.name) / "50-airuleset-session-restart.conf",
                Path(d.name) / "50-session-restart-947.conf")

    def _clean(self, paths):
        reloads, err = [], io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(
                io.StringIO()):
            cleanup_session_restart_dropins(
                paths=list(paths), daemon_reload_fn=lambda: reloads.append(1))
        return reloads, err.getvalue()

    def test_removes_present_files_and_reloads(self):
        paths = self._paths()
        for f in paths:
            f.write_text("[Service]\n")
        reloads, _err = self._clean(paths)
        self.assertEqual([f.exists() for f in paths], [False, False])
        self.assertEqual(reloads, [1])

    def test_nothing_present_no_reload(self):
        self.assertEqual(self._clean(self._paths())[0], [])

    def test_oserror_logged_not_raised(self):
        d = self._paths()[0]
        d.mkdir()       # a directory cannot be unlinked
        self.assertIn("failed to remove", self._clean([d])[1])


class TestRunOnceRegistersNoSuchJob(unittest.TestCase):
    def test_no_session_restart_enabled_param(self):
        src = (REPO / "watchdog" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("session_restart_enabled", src)

    def test_no_session_restart_or_health_label(self):
        labels = re.findall(r'_add\(\s*"([^"]+)"',
                            inspect.getsource(wd.run_once))
        bad = [lb for lb in labels
               if "session_restart" in lb or "session_health" in lb]
        self.assertEqual(bad, [], "run_once registers a session job (#1137)")

    def test_job_46_docstring_entry_is_removed(self):
        m = re.search(r"\(46\)(.*?)(?=\(\d+[a-z]?\)|Returns a list)",
                      wd.run_once.__doc__, re.S)
        self.assertIsNotNone(m, "job 46 must stay addressable in the doc")
        self.assertIn("REMOVED", " ".join(m.group(1).split()))


if __name__ == "__main__":
    unittest.main()
