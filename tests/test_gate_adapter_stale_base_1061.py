"""#1061 item 5b -- gate adapters run `python3 -P -m gates.<module>` so a stale
worktree `gates/` can never shadow the real package (folded from #1046).

Incident (three lanes, 2026-09-17): a worktree on a stale base carries an older
`gates/` that predates a deployed gate module. `python3 -m gates.<module>`
prepends the CALLER'S cwd to sys.path[0] BEFORE the adapter's PYTHONPATH, so the
stale in-cwd `gates` package shadows the real one on PYTHONPATH and the submodule
is not found -- `ModuleNotFoundError: No module named gates.<module>`. The
fail-CLOSED adapters then exit 2 ("internal error"), wedging EVERY Bash call.
`-P` (Python >= 3.11) drops the unsafe cwd/script path from sys.path so the
adapter's own `PYTHONPATH=<repo>` resolves the real package.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"

# The line every gate adapter uses to invoke its module. Matches
#   python3 -m gates.<module>
# and (after the fix)
#   python3 -P -m gates.<module>
_ADAPTER_INVOKE_RE = re.compile(
    r"python3\s+(?P<flags>(?:-\w+\s+)*)-m\s+gates\.(?P<mod>[A-Za-z_][A-Za-z0-9_]*)")


def _adapters_running_gates():
    """Every hooks/*.sh that invokes `python3 [...] -m gates.<module>`, as
    (path, module, invoke_line, flags). Discovered dynamically so a NEWLY-added
    gate adapter is covered without editing this test. Comment lines (a `#`
    before the match, e.g. a doc line quoting `python3 -m gates.<module>`) are
    skipped -- only the real, executed invocation is graded."""
    found = []
    for sh in sorted(HOOKS.glob("*.sh")):
        for line in sh.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _ADAPTER_INVOKE_RE.search(line)
            if not m:
                continue
            if line[:m.start()].lstrip().startswith("#"):
                continue  # a documentation comment, not the executed line
            found.append((sh, m.group("mod"), m.group(0), m.group("flags")))
    return found


class TestAllGateAdaptersUseDashP(unittest.TestCase):
    def test_every_gate_adapter_passes_dash_P(self):
        adapters = _adapters_running_gates()
        self.assertTrue(adapters, "expected at least one gate adapter")
        missing = []
        for sh, mod, line, flags in adapters:
            if "-P" not in flags.split():
                missing.append("%s (gates.%s): %r" % (sh.name, mod, line))
        self.assertEqual(
            missing, [],
            "these gate adapters must run `python3 -P -m gates.<module>` so a "
            "stale in-cwd gates/ cannot shadow the real package (#1061 item 5b):\n"
            + "\n".join(missing))


class TestFailClosedAdapterRealVerdictFromStaleCwd(unittest.TestCase):
    """The behavioural lock: drive the REAL fail-closed adapter from a temp cwd
    holding only a stale empty `gates/__init__.py`, over a benign command, and
    require a REAL verdict (exit 0 / allow) -- never the `ModuleNotFoundError`
    fail-closed wedge (exit 2, "No module named")."""

    def _stale_cwd(self):
        d = tempfile.mkdtemp(prefix="stale-gates-")
        self.addCleanup(shutil.rmtree, d, True)
        g = Path(d) / "gates"
        g.mkdir()
        (g / "__init__.py").write_text("")  # stale/empty package
        return d

    def _run(self, adapter, payload, cwd):
        env = hermetic_hook_env(self)
        return subprocess.run(
            ["bash", str(HOOKS / adapter)],
            input=json.dumps(payload), capture_output=True, text=True,
            cwd=cwd, env=env)

    def test_block_test_skips_real_verdict(self):
        cwd = self._stale_cwd()
        r = self._run("block-test-skips.sh",
                      {"tool_input": {"command": "echo hi"}}, cwd)
        self.assertNotIn("No module named", r.stderr,
                         "stale-cwd wedge: gates.testskips shadowed (stderr=%r)"
                         % r.stderr)
        self.assertEqual(r.returncode, 0,
                         "benign command must allow, not fail-closed "
                         "(rc=%d stderr=%r)" % (r.returncode, r.stderr))

    def test_pre_push_test_check_real_verdict(self):
        cwd = self._stale_cwd()
        r = self._run("pre-push-test-check.sh",
                      {"tool_input": {"command": "echo hi"}}, cwd)
        self.assertNotIn("No module named", r.stderr, r.stderr)
        self.assertEqual(r.returncode, 0,
                         "rc=%d stderr=%r" % (r.returncode, r.stderr))

    def test_block_ungated_issue_filing_no_module_error(self):
        cwd = self._stale_cwd()
        r = self._run("block-ungated-issue-filing.sh",
                      {"tool_input": {"command": "echo hi"}}, cwd)
        self.assertNotIn("No module named", r.stderr, r.stderr)
        self.assertEqual(r.returncode, 0,
                         "rc=%d stderr=%r" % (r.returncode, r.stderr))


class TestGateModulesImportWithDashPFromStaleCwd(unittest.TestCase):
    """The mechanism lock: with `-P` and PYTHONPATH=<repo>, every gate module
    imports from a stale cwd; WITHOUT `-P` the stale in-cwd gates/ shadows it."""

    def _stale_cwd(self):
        d = tempfile.mkdtemp(prefix="stale-gates-mod-")
        self.addCleanup(shutil.rmtree, d, True)
        g = Path(d) / "gates"
        g.mkdir()
        (g / "__init__.py").write_text("")
        return d

    def test_modules_import_with_dash_P_but_not_without(self):
        mods = sorted({mod for _sh, mod, _l, _f in _adapters_running_gates()})
        self.assertTrue(mods)
        cwd = self._stale_cwd()
        env = dict(os.environ, PYTHONPATH=str(REPO))
        for mod in mods:
            with_p = subprocess.run(
                ["python3", "-P", "-c", "import gates.%s" % mod],
                cwd=cwd, env=env, capture_output=True, text=True)
            self.assertEqual(with_p.returncode, 0,
                             "gates.%s must import with -P from a stale cwd "
                             "(stderr=%r)" % (mod, with_p.stderr))
            without_p = subprocess.run(
                ["python3", "-c", "import gates.%s" % mod],
                cwd=cwd, env=env, capture_output=True, text=True)
            self.assertNotEqual(
                without_p.returncode, 0,
                "gates.%s unexpectedly imported WITHOUT -P from a stale cwd -- "
                "the shadowing premise no longer holds" % mod)
            self.assertIn("No module named", without_p.stderr,
                          "gates.%s without -P should hit the shadow "
                          "(stderr=%r)" % (mod, without_p.stderr))


if __name__ == "__main__":
    unittest.main()
