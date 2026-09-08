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

    def test_non_controller_satisfied_when_entry_already_present(self):
        """On a non-controller box with the entry already registered,
        step_registry must return 'satisfied' (read-only path), NOT
        'refused' — C1 fix: the guard fires only at the write point."""
        with TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            _init_repo(proj)
            entry = {"name": "test-proj", "host": "dev2",
                     "path": "~/devel/test-proj",
                     "branch_model": "2-branch",
                     "default_branch": "main",
                     "work_branch": "dev"}
            reg_path = str(Path(td) / "projects-registry.json")
            # Pre-populate the registry with the SAME entry.
            Path(reg_path).write_text(json.dumps([entry]) + "\n")
            result = ob.step_registry(
                str(proj), entry, reg_path,
                box_class_fn=lambda: "workstation")
            self.assertEqual(result["status"], "satisfied")

    def test_non_controller_dry_run_returns_would_apply(self):
        """On a non-controller box with dry_run=True, step_registry must
        return 'would-apply' (read-only path), NOT 'refused' — C1 fix."""
        with TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            _init_repo(proj)
            entry = {"name": "test-proj", "host": "dev2",
                     "path": "~/devel/test-proj",
                     "branch_model": "2-branch",
                     "default_branch": "main",
                     "work_branch": "dev"}
            reg_path = str(Path(td) / "projects-registry.json")
            Path(reg_path).write_text("[]\n")
            result = ob.step_registry(
                str(proj), entry, reg_path, dry_run=True,
                box_class_fn=lambda: "workstation")
            self.assertEqual(result["status"], "would-apply")

    def test_controller_commit_failure_surfaces_in_status(self):
        """When the auto-commit fails (e.g. git lock held), the step must
        return 'applied-uncommitted' with the failure detail — C2 fix."""
        import unittest.mock as m
        with TemporaryDirectory() as td:
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
            entry = {"name": "fail-proj", "host": "dev1",
                     "path": "~/devel/fail-proj",
                     "branch_model": "2-branch",
                     "default_branch": "main",
                     "work_branch": "dev"}
            # Patch subprocess.run globally (cli_onboard imports subprocess
            # inside _auto_commit_registry, so there is no module-level
            # attribute to patch).
            orig_run = subprocess.run

            def fail_commit(cmd, *a, **k):
                if cmd[:2] == ["git", "-C"] and "commit" in cmd:
                    return m.Mock(returncode=1, stdout="",
                                  stderr="fatal: lock held")
                return orig_run(cmd, *a, **k)

            with m.patch("subprocess.run", side_effect=fail_commit):
                result = ob.step_registry(
                    str(proj), entry, reg_path,
                    box_class_fn=lambda: "controller")
            self.assertEqual(result["status"], "applied-uncommitted")
            self.assertIn("lock held", result["detail"])

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
# Branch 2: deploy-leg diagnostics — BEHAVIORAL tests.
# --------------------------------------------------------------------------- #
class TestDeployLegDiagnostics(unittest.TestCase):
    """A failed deploy leg must print a WARN line with dirty file names."""

    def _remote(self):
        return {"name": "dev2", "host": "1.2.3.4", "user": "u",
                "repo_path": "~/devel/airuleset"}

    def test_dirty_tree_prints_warn_with_filenames(self):
        """When git status --porcelain returns dirty files, the WARN line
        must contain the file names."""
        import io
        import unittest.mock as m
        import cli_remote

        fake_diag = m.Mock(returncode=0, stdout=" M foo.py\n M bar.py\n",
                           stderr="")
        err = io.StringIO()
        with m.patch("subprocess.run", return_value=fake_diag), \
                m.patch("sys.stderr", err):
            cli_remote._print_deploy_leg_diagnostics(
                self._remote(), None, ["-o", "StrictHostKeyChecking=no"],
                [])
        output = err.getvalue()
        self.assertIn("WARN dev2", output)
        self.assertIn("dirty:", output)
        self.assertIn("M foo.py", output)

    def test_clean_tree_prints_warn_appears_clean(self):
        """When git status --porcelain returns empty, the WARN line must
        say the tree appears clean."""
        import io
        import unittest.mock as m
        import cli_remote

        fake_diag = m.Mock(returncode=0, stdout="", stderr="")
        err = io.StringIO()
        with m.patch("subprocess.run", return_value=fake_diag), \
                m.patch("sys.stderr", err):
            cli_remote._print_deploy_leg_diagnostics(
                self._remote(), None, ["-o", "StrictHostKeyChecking=no"],
                [])
        output = err.getvalue()
        self.assertIn("WARN dev2", output)
        self.assertIn("tree appears clean", output)

    def test_identity_branch_uses_ssh_with_identity(self):
        """When identity is provided, the ssh command must include -i."""
        import unittest.mock as m
        import cli_remote

        calls = []

        def capture_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=capture_run), \
                m.patch("sys.stderr", m.Mock()):
            cli_remote._print_deploy_leg_diagnostics(
                self._remote(), "~/.secrets/key",
                ["-o", "StrictHostKeyChecking=no"], [])
        ssh_call = calls[0]
        self.assertEqual(ssh_call[0], "ssh")
        self.assertIn("-i", ssh_call)
        self.assertIn("git status", ssh_call[-1])

    def test_no_identity_branch_uses_sshpass(self):
        """When identity is None, the ssh command must use sshpass."""
        import unittest.mock as m
        import cli_remote

        calls = []

        def capture_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="", stderr="")

        with m.patch("subprocess.run", side_effect=capture_run), \
                m.patch("sys.stderr", m.Mock()):
            cli_remote._print_deploy_leg_diagnostics(
                self._remote(), None,
                ["-o", "StrictHostKeyChecking=no"], [])
        ssh_call = calls[0]
        self.assertEqual(ssh_call[0], "sshpass")
        self.assertIn("git status", ssh_call[-1])

    def test_timeout_exception_prints_warn_not_raises(self):
        """When the diagnostic ssh times out, it must print a WARN and
        NOT raise — the original failure must not be masked."""
        import io
        import unittest.mock as m
        import cli_remote

        err = io.StringIO()
        with m.patch("subprocess.run",
                     side_effect=subprocess.TimeoutExpired("ssh", 30)), \
                m.patch("sys.stderr", err):
            # Must not raise.
            cli_remote._print_deploy_leg_diagnostics(
                self._remote(), None,
                ["-o", "StrictHostKeyChecking=no"], [])
        output = err.getvalue()
        self.assertIn("WARN dev2", output)
        self.assertIn("diagnostic ssh failed", output)


if __name__ == "__main__":
    unittest.main()
