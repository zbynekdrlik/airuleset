"""Locks the invariants of the #683 CI foundation workflow.

Pure-infra (YAML + a data deny-list + two stdlib helpers) is locked by a
meta-test rather than a per-line RED (per the ticket's calibrated-TDD note).
Invariants pinned here:
  * the workflow exists and carries NO continue-on-error (no-continue-on-error.md);
  * it wires all three gates (ruff / size_ratchet / hermetic pytest) + the
    no-op-job guard, in a pinned python:3.12 container with jq;
  * the box-bound deny-list exists, every entry's file path still exists
    (no rot), and the two collection-error files are ignored;
  * scripts/ci_pytest_args.py maps a bare file -> --ignore and a node-id
    (with ::) -> --deselect, one arg per line.

Hermetic (stdlib + subprocess + tempfile): runs in the bare CI container.
Text-parses the YAML (no PyYAML dependency, which the bare container lacks) —
a substring/line check is both dependency-free and robust for these invariants.
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
DENYLIST = REPO / ".github" / "box-bound-tests.txt"
ARGS_GEN = REPO / "scripts" / "ci_pytest_args.py"

COLLECTION_ERROR_FILES = (
    "tests/test_webterm_ctrlbw_darkening.py",
    "tests/test_webterm_stream_footer_672.py",
)


def _wf_text():
    return WORKFLOW.read_text(encoding="utf-8")


def _denylist_entries():
    """Non-comment, non-blank deny-list entries (inline ` #` comment stripped)."""
    out = []
    for raw in DENYLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        if line:
            out.append(line)
    return out


class TestWorkflowExists(unittest.TestCase):
    def test_workflow_file_present(self):
        self.assertTrue(WORKFLOW.exists(),
                        ".github/workflows/ci.yml missing — the repo still has "
                        "no CI workflow (#683)")

    def test_denylist_present(self):
        self.assertTrue(DENYLIST.exists(),
                        ".github/box-bound-tests.txt missing")

    def test_args_generator_present(self):
        self.assertTrue(ARGS_GEN.exists(), "scripts/ci_pytest_args.py missing")


class TestNoContinueOnError(unittest.TestCase):
    def test_no_continue_on_error_anywhere(self):
        # no-continue-on-error.md: a green tick must mean the step really passed.
        self.assertNotIn("continue-on-error", _wf_text(),
                         "continue-on-error is banned in this repo's CI")


class TestGatesWired(unittest.TestCase):
    def test_all_gates_and_guard_referenced(self):
        wf = _wf_text()
        for needle in (
            "ruff check",
            "scripts/size_ratchet.py --check",
            "scripts/ci_pytest_args.py",
            "scripts/ci_assert_collected.py",
            "python:3.12",
            "container:",
            "jq",
            "workflow_dispatch",
        ):
            self.assertIn(needle, wf, "workflow missing %r" % needle)

    def test_guard_floor_is_a_reasonable_positive_int(self):
        wf = _wf_text()
        m = re.search(r"ci_assert_collected\.py\s+\S+\s+(\d+)", wf)
        self.assertIsNotNone(m, "no collected-count floor argument found")
        floor = int(m.group(1))
        self.assertGreaterEqual(floor, 1000,
                                "floor too low to prove a substantial suite ran")


class TestWorkspaceGitTrust(unittest.TestCase):
    """Run 32836799214 (#683 run 3): the container runs as uid 0 while the
    bind-mounted workspace is owned by the host runner uid, so every git
    call needs `safe.directory` — AND `git clone <workspace>` (which
    tests/test_rules_ab_experiment.py's make_tree does) opens the SOURCE
    repo at `<workspace>/.git`, an EXACT-path safe.directory match the
    workspace entry alone does not cover (probed live in docker
    python:3.12: the clone dies `dubious ownership in repository at
    '.../.git'` with only the workspace entry present). Lock BOTH."""

    def test_workspace_marked_safe(self):
        self.assertIn('safe.directory "$GITHUB_WORKSPACE"', _wf_text(),
                      "workflow must mark the workspace safe for git — the "
                      "container's uid 0 does not own the bind-mounted checkout")

    def test_workspace_gitdir_marked_safe_for_local_clones(self):
        self.assertIn('safe.directory "$GITHUB_WORKSPACE/.git"', _wf_text(),
                      "a local `git clone <workspace>` opens the source at "
                      "<workspace>/.git — exact-path matching means the "
                      "workspace entry alone does not cover it (run "
                      "32836799214's make_tree failure)")


class TestDenylistIntegrity(unittest.TestCase):
    def test_every_entry_file_path_exists(self):
        # No rot: a deny-listed file (or a node-id's file part) that no longer
        # exists is a stale entry to fix.
        for entry in _denylist_entries():
            relpath = entry.split("::", 1)[0]
            self.assertTrue((REPO / relpath).exists(),
                            "deny-list entry points at a missing file: %r" % entry)

    def test_collection_error_files_are_ignored(self):
        entries = set(_denylist_entries())
        for f in COLLECTION_ERROR_FILES:
            self.assertIn(f, entries,
                          "%s errors at COLLECTION and MUST stay in the deny-list "
                          "as an --ignore, or CI aborts collection" % f)


class TestArgsGeneratorMapping(unittest.TestCase):
    def _gen(self, body):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "deny.txt"
            p.write_text(body, encoding="utf-8")
            r = subprocess.run([sys.executable, str(ARGS_GEN), str(p)],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [ln for ln in r.stdout.splitlines() if ln.strip()]

    def test_file_becomes_ignore_and_node_becomes_deselect(self):
        lines = self._gen(
            "# comment\n"
            "tests/test_x.py\n"
            "tests/test_y.py::Cls::test_m\n"
        )
        self.assertIn("--ignore=tests/test_x.py", lines)
        self.assertIn("--deselect=tests/test_y.py::Cls::test_m", lines)

    def test_counts_match_real_denylist(self):
        entries = _denylist_entries()
        files = [e for e in entries if "::" not in e]
        nodes = [e for e in entries if "::" in e]
        r = subprocess.run([sys.executable, str(ARGS_GEN), str(DENYLIST)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertEqual(out.count("--ignore="), len(files))
        self.assertEqual(out.count("--deselect="), len(nodes))


if __name__ == "__main__":
    unittest.main()


class TestCheckoutFetchDepth(unittest.TestCase):
    def test_checkout_fetches_full_history(self):
        # tests/test_rules_ab_experiment.py replays REAL historical commit
        # pairs (e.g. #88's 8a298b3/501c4be) straight from git history, so a
        # shallow checkout (actions/checkout's fetch-depth: 1 default) fails
        # it with "is not a commit" — the second-ever main run died exactly
        # there. The checkout step must pin fetch-depth: 0 (full history).
        self.assertIn("fetch-depth: 0", _wf_text(),
                      "actions/checkout must set fetch-depth: 0 — history-"
                      "replaying tests need the full clone (#683 run 2 failure)")
