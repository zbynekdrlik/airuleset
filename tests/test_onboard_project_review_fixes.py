"""Regression tests for the #569 adversarial-review findings (2× fresh-context
general-purpose reviewers). Each test pins the CORRECT (fixed) behavior and
FAILS against the pre-fix code:

- MAJOR-1/M3: re-onboard/audit resolves the registry entry by PATH and uses its
  recorded (authoritative) name, so a project whose seeded name isn't
  path-derivable (email-extractor) is not duplicated / mis-audited.
- MAJOR-2/M1: a bare re-onboard preserves the existing entry's overrides,
  branch model and host (idempotency — no spurious dev branch, no regression).
- MAJOR-3: --audit sweep skips cross-host entries instead of reporting them as
  false missing-repo drift.
- MAJOR-4: a present-but-corrupt registry is NEVER overwritten (no data loss).
- M2: an already-tracked build artifact is ACTUALLY untracked (not a no-op).

Offline: git runs on tmp fixtures, gh/ssh gated behind FakeRunner.
"""

import socket
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_onboard as ob
from test_onboard_project import FakeRunner, init_repo, tracked_files


REMOTE = "https://github.com/zbynekdrlik/foo.git"


class TestOverrideAndBranchReadback(unittest.TestCase):
    def test_bare_reonboard_preserves_overrides_and_branch_model(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote=REMOTE)
            reg = str(Path(rd) / "r.json")
            ob.onboard_project(str(repo), name="foo", overrides=["3-branch"],
                               registry_path=reg, run=FakeRunner())
            e1 = ob.registry_entry_for(reg, "foo")
            self.assertEqual(e1["branch_model"], "3-branch")
            self.assertEqual(e1["work_branch"], "develop")
            self.assertIn("3-branch", e1["overrides"])
            # bare re-onboard: NO --override
            r2 = ob.onboard_project(str(repo), name="foo", registry_path=reg,
                                    run=FakeRunner())
            e2 = ob.registry_entry_for(reg, "foo")
            self.assertEqual(e2["branch_model"], "3-branch",
                             "bare re-run regressed branch model")
            self.assertIn("3-branch", e2["overrides"],
                          "bare re-run dropped overrides")
            st = {s["step"]: s["status"] for s in r2["steps"]}
            self.assertEqual(st["registry"], "satisfied")
            self.assertEqual(st["branches"], "satisfied",
                             "bare re-run created a spurious dev branch")


class TestHostReadback(unittest.TestCase):
    def test_bare_reonboard_preserves_registry_host(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote=REMOTE)
            reg = str(Path(rd) / "r.json")
            ob.save_registry(reg, [{
                "name": "foo", "host": "dev2", "path": str(repo),
                "overrides": [], "onboarded": "2026-01-01",
                "branch_model": "2-branch", "default_branch": "main",
                "work_branch": "dev"}])
            # local exec (host=None → no ssh), but entry host must be preserved
            ob.onboard_project(str(repo), host=None, name="foo",
                               registry_path=reg, run=FakeRunner())
            self.assertEqual(ob.registry_entry_for(reg, "foo")["host"], "dev2")


class TestNameResolvedByPath(unittest.TestCase):
    def test_reonboard_uses_registry_name_not_rederived(self):
        # a path whose leaf derives to "email-extract" but is registered as
        # "email-extractor" (the real seeded name)
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            nested = Path(d) / "n8n" / "email_extract"
            nested.mkdir(parents=True)
            init_repo(nested, remote=REMOTE)
            self.assertEqual(ob.derive_repo_name(str(nested)), "email-extract")
            reg = str(Path(rd) / "r.json")
            ob.save_registry(reg, [{
                "name": "email-extractor", "host": "dev1", "path": str(nested),
                "overrides": [], "onboarded": "2026-01-01",
                "branch_model": "2-branch", "default_branch": "main",
                "work_branch": "dev"}])
            r = ob.onboard_project(str(nested), name=None, registry_path=reg,
                                   run=FakeRunner())
            self.assertEqual(r["name"], "email-extractor",
                             "re-onboard re-derived instead of using registry name")
            names = [e["name"] for e in ob.load_registry(reg)]
            self.assertEqual(names.count("email-extractor"), 1)
            self.assertNotIn("email-extract", names, "duplicate entry created")


class TestAuditSkipsCrossHost(unittest.TestCase):
    def test_audit_registry_skips_other_host(self):
        here = socket.gethostname()
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            local_repo = Path(d)
            init_repo(local_repo, remote=REMOTE)
            reg = str(Path(rd) / "r.json")
            ob.save_registry(reg, [
                {"name": "remote-proj", "host": "some-other-box",
                 "path": "/nonexistent/xyz", "overrides": [],
                 "onboarded": None, "branch_model": "2-branch",
                 "default_branch": "main", "work_branch": "dev"},
                {"name": "local-proj", "host": here, "path": str(local_repo),
                 "overrides": [], "onboarded": None, "branch_model": "2-branch",
                 "default_branch": "main", "work_branch": "dev"},
            ])
            rep = ob.audit_registry(reg)
            kinds = {x["kind"] for x in rep["remote-proj"]}
            self.assertIn("remote-host", kinds)
            self.assertNotIn("missing-repo", kinds,
                             "cross-host entry falsely reported as missing-repo")


class TestCorruptRegistryNotOverwritten(unittest.TestCase):
    def test_corrupt_registry_is_never_overwritten(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote=REMOTE)
            reg = Path(rd) / "r.json"
            corrupt = '[{"name":"a"},{"name":"b"} TRAILING GARBAGE'
            reg.write_text(corrupt, encoding="utf-8")
            r = ob.onboard_project(str(repo), name="foo",
                                   registry_path=str(reg), run=FakeRunner())
            st = {s["step"]: s["status"] for s in r["steps"]}
            self.assertEqual(st["registry"], "skipped")
            self.assertEqual(reg.read_text(encoding="utf-8"), corrupt,
                             "corrupt registry was overwritten (data loss)")


class TestArtifactActuallyUntracked(unittest.TestCase):
    def test_tracked_artifact_is_removed_from_tracking(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote=REMOTE)
            (repo / "Cargo.toml").write_text("[package]\n")  # rust → target/ ignored
            tgt = repo / "target"
            tgt.mkdir()
            (tgt / "x").write_text("built")
            subprocess.run(["git", "-C", str(repo), "add", "Cargo.toml",
                            "target/x"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "a"],
                           check=True)
            self.assertIn("target/x", tracked_files(repo))
            r = ob.onboard_project(str(repo), name="foo",
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=FakeRunner())
            self.assertNotIn("target/x", tracked_files(repo),
                             "build artifact was NOT untracked (no-op commit bug)")
            self.assertTrue((tgt / "x").exists(), "artifact wrongly deleted from disk")
            st = {s["step"]: s["status"] for s in r["steps"]}
            self.assertEqual(st["gitignore"], "applied")


class TestDeriveRejectsEmpty(unittest.TestCase):
    def test_empty_derived_name_raises(self):
        with self.assertRaises(ValueError):
            ob.derive_repo_name("/")
        with self.assertRaises(ValueError):
            ob.derive_repo_name("")


if __name__ == "__main__":
    unittest.main()
