"""#946 — onboard-project registry guard + deploy-leg diagnostics.

Branch 1: step_registry MUST refuse on a non-controller box and auto-commit
on the controller.
Branch 2: a failed deploy leg (git pull failure) MUST print a WARN line with
the dirty file names from git status --porcelain.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_onboard as ob


# --------------------------------------------------------------------------- #
# Helpers — minimal git fixture (reused from test_onboard_project.py pattern).
# --------------------------------------------------------------------------- #
def _init_repo(path, default_branch="main"):
    subprocess.run(["git", "-C", str(path), "init", "-q",
                    "-b", default_branch], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email",
                    "t@t.t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name",
                    "t"], check=True)
    (Path(path) / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"],
                   check=True)


class FakeRunner:
    """git runs for real on the tmp fixture; gh/ssh are intercepted."""
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        head = argv[0] if argv else ""
        if head in ("gh", "ssh"):
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.run(argv, **kw)


# --------------------------------------------------------------------------- #
# Branch 1: step_registry controller guard.
# --------------------------------------------------------------------------- #
class TestStepRegistryControllerGuard(unittest.TestCase):
    """step_registry MUST refuse on a non-controller box."""

    def test_non_controller_box_refuses_registry_write(self):
        """On a non-controller box (box_class != 'controller'),
        step_registry must NOT write to the registry and must return a
        status indicating refusal, with the JSON entry printed."""
        with TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            _init_repo(proj)
            reg_path = str(Path(td) / "projects-registry.json")
            Path(reg_path).write_text("[]\n")
            entry = {"name": "test-proj", "host": "dev2",
                     "path": "~/devel/test-proj",
                     "branch_model": "2-branch",
                     "default_branch": "main",
                     "work_branch": "dev"}
            # Inject box_class_fn returning "workstation" (non-controller).
            result = ob.step_registry(
                str(proj), entry, reg_path,
                box_class_fn=lambda: "workstation")
            # The registry must NOT have been written.
            self.assertEqual(json.loads(Path(reg_path).read_text()), [])
            # The step must indicate refusal.
            self.assertEqual(result["status"], "refused")
            self.assertIn("controller", result["detail"].lower())

    def test_controller_box_writes_and_commits_registry(self):
        """On the controller box, step_registry MUST write the entry AND
        auto-commit projects-registry.json in the airuleset repo."""
        with TemporaryDirectory() as td:
            # Create a fake airuleset repo with a registry file.
            airuleset_repo = Path(td) / "airuleset"
            airuleset_repo.mkdir()
            _init_repo(airuleset_repo)
            reg_path = str(airuleset_repo / "projects-registry.json")
            Path(reg_path).write_text("[]\n")
            subprocess.run(
                ["git", "-C", str(airuleset_repo), "add",
                 "projects-registry.json"], check=True)
            subprocess.run(
                ["git", "-C", str(airuleset_repo), "commit", "-q", "-m",
                 "add registry"], check=True)
            proj = Path(td) / "proj"
            proj.mkdir()
            _init_repo(proj)
            entry = {"name": "test-proj", "host": "dev1",
                     "path": "~/devel/test-proj",
                     "branch_model": "2-branch",
                     "default_branch": "main",
                     "work_branch": "dev"}
            # Inject box_class_fn returning "controller".
            result = ob.step_registry(
                str(proj), entry, reg_path,
                box_class_fn=lambda: "controller")
            # The registry must have been written.
            entries = json.loads(Path(reg_path).read_text())
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["name"], "test-proj")
            self.assertEqual(result["status"], "applied")
            # The registry file must be committed (clean tree).
            r = subprocess.run(
                ["git", "-C", str(airuleset_repo), "status", "--porcelain",
                 "projects-registry.json"],
                capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "",
                             "registry should be committed, not dirty")

    def test_non_controller_prints_json_entry(self):
        """The refusal message must contain the JSON entry so the user
        can copy-paste it on the controller."""
        with TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            _init_repo(proj)
            reg_path = str(Path(td) / "projects-registry.json")
            Path(reg_path).write_text("[]\n")
            entry = {"name": "my-proj", "host": "dev2",
                     "path": "~/devel/my-proj",
                     "branch_model": "2-branch",
                     "default_branch": "main",
                     "work_branch": "dev"}
            result = ob.step_registry(
                str(proj), entry, reg_path,
                box_class_fn=lambda: "workstation")
            # The detail must contain the JSON-serialized entry.
            self.assertIn('"my-proj"', result["detail"])


# --------------------------------------------------------------------------- #
# Branch 2: deploy-leg diagnostics.
# --------------------------------------------------------------------------- #
class TestDeployLegDiagnostics(unittest.TestCase):
    """A failed deploy leg must print a WARN line with dirty file names."""

    def test_deploy_loop_calls_diagnostics_on_failure(self):
        """_deploy_to_all_remotes must call _print_deploy_leg_diagnostics
        when a remote command fails (non-auth failure)."""
        import cli_remote
        import inspect

        src = inspect.getsource(cli_remote._deploy_to_all_remotes)
        self.assertIn("_print_deploy_leg_diagnostics", src,
                      "_deploy_to_all_remotes must call "
                      "_print_deploy_leg_diagnostics on pull failure")

    def test_diagnostics_function_issues_git_status(self):
        """_print_deploy_leg_diagnostics must issue git status --porcelain
        and print a WARN line with dirty file info."""
        import cli_remote
        import inspect

        src = inspect.getsource(cli_remote._print_deploy_leg_diagnostics)
        self.assertIn("git status", src,
                      "_print_deploy_leg_diagnostics must run "
                      "git status --porcelain")
        # WARN and dirty may span f-string continuations (separate lines
        # in the source), so use re.DOTALL.
        import re
        self.assertTrue(
            re.search(r"WARN.*dirty|dirty.*WARN", src, re.DOTALL),
            "_print_deploy_leg_diagnostics must print a WARN "
            "line with dirty file information")


if __name__ == "__main__":
    unittest.main()
