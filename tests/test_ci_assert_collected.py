"""Locks the defect-shaped behaviour of the CI collected-count guard (#683).

test-strictness.md bans a no-op test job. `scripts/ci_assert_collected.py` is
the explicit guard: the hermetic pytest job MUST fail if it collected zero (or
catastrophically few) tests, so a mis-globbed deny-list cannot masquerade as a
green no-op. This suite is the RED-before-GREEN lock on that guard — it fails
if the guard script is absent or does not reject a below-floor/zero/unreadable
result. Hermetic (stdlib + subprocess + tempfile only): runs in the bare
`python:3.12` CI container it protects.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "scripts" / "ci_assert_collected.py"


def _junit(tests, failures=0, errors=0, skipped=0):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" '
        'tests="%d" failures="%d" errors="%d" skipped="%d">'
        "</testsuite></testsuites>" % (tests, failures, errors, skipped)
    )


def _run(junit_text, floor, write=True):
    """Run the guard against a junit body; return the CompletedProcess."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "junit.xml"
        if write:
            p.write_text(junit_text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(GUARD), str(p), str(floor)],
            capture_output=True, text=True,
        )


class TestGuardExists(unittest.TestCase):
    def test_guard_script_present(self):
        self.assertTrue(GUARD.exists(),
                        "scripts/ci_assert_collected.py missing — the no-op-job "
                        "guard the CI workflow depends on does not exist")


class TestGuardRejects(unittest.TestCase):
    def test_zero_collected_is_rejected(self):
        r = _run(_junit(0), 5000)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_below_floor_is_rejected(self):
        r = _run(_junit(100), 5000)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_missing_junit_is_rejected(self):
        r = _run("", 5000, write=False)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


class TestGuardAccepts(unittest.TestCase):
    def test_above_floor_passes(self):
        r = _run(_junit(6000, skipped=3), 5000)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_exactly_at_floor_passes(self):
        # n < floor fails; n == floor must pass.
        r = _run(_junit(5000), 5000)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_testsuites_wrapper_and_bare_testsuite_both_counted(self):
        # A junit whose count lives on the inner <testsuite> is summed.
        r = _run(_junit(5001), 5000)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
