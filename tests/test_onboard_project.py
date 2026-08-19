"""onboard-project — jednotný idempotentný onboarding CLI (#569).

Owner directive (2026-08-19): onboarding projektu pod správu airuleset musí byť
JEDEN udržiavaný mechanizmus (CLI + skill + machine-readable registry), nie
ad-hoc ručné kroky „podľa aktuálnej hlavy". montalu-vyuctovanie (2026-08-19)
bol onboardovaný ručne; predošlé onboardingy driftovali.

Testy sú OFFLINE: git beží reálne na tmp fixture repe, ale KAŽDÝ `gh`/`ssh`
call ide cez injektovaný runner (`FakeRunner`) — NIKDY sa nevytvorí reálne
GitHub repo ani ticket. Core invariant = idempotencia: 2. beh = all-satisfied
no-op, nula mutácií, a rozpracovaný (dirty) worktree sa nikdy nedotýka.
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
# Test runner: git runs for real on the tmp fixture; gh/ssh are intercepted so
# no network / real repo / real ticket is EVER touched.
# --------------------------------------------------------------------------- #
class FakeRunner:
    def __init__(self, gh_handler=None):
        self.calls = []              # every argv seen
        self.gh_calls = []           # only gh argvs
        self.ssh_calls = []          # only ssh argvs
        self._gh_handler = gh_handler

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        head = argv[0] if argv else ""
        if head == "gh":
            self.gh_calls.append(list(argv))
            if self._gh_handler:
                return self._gh_handler(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if head == "ssh":
            self.ssh_calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")
        # git and everything else: run for real against the tmp fixture
        return subprocess.run(argv, **kw)

    def gh_matching(self, *needles):
        return [c for c in self.gh_calls
                if all(any(n in a for a in c) for n in needles)]


def init_repo(path, default_branch="main", commit=True, remote=None):
    subprocess.run(["git", "-C", str(path), "init", "-q",
                    "-b", default_branch], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email",
                    "t@t.t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name",
                    "t"], check=True)
    if commit:
        (Path(path) / "README.md").write_text("x\n")
        subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"],
                       check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin",
                        remote], check=True)


def tracked_files(path):
    r = subprocess.run(["git", "-C", str(path), "ls-files"],
                       capture_output=True, text=True)
    return set(r.stdout.split())


# --------------------------------------------------------------------------- #
# 1) Name derivation — deterministic, studied from real managed names.
# --------------------------------------------------------------------------- #
class TestDeriveRepoName(unittest.TestCase):
    def test_leaf_basename_underscores_to_hyphens(self):
        self.assertEqual(ob.derive_repo_name("/home/x/devel/forestshop/parovanie_produktov"),
                         "parovanie-produktov")
        self.assertEqual(ob.derive_repo_name("/home/x/devel/slovnormal/forecasting_storage"),
                         "forecasting-storage")

    def test_cluster_prefix_applied_for_montalu(self):
        self.assertEqual(ob.derive_repo_name("/home/x/devel/montalu/vyuctovanie"),
                         "montalu-vyuctovanie")
        self.assertEqual(ob.derive_repo_name("/home/x/devel/montalu/n8n"),
                         "montalu-n8n")

    def test_toplevel_path_no_prefix(self):
        self.assertEqual(ob.derive_repo_name("/home/x/devel/automatizacie-montalu"),
                         "automatizacie-montalu")

    def test_non_cluster_parent_not_prefixed(self):
        # forestshop / slovnormal / n8n parents are NOT cluster prefixes
        self.assertEqual(ob.derive_repo_name("/home/x/devel/n8n/email_extract"),
                         "email-extract")

    def test_explicit_name_overrides_derivation(self):
        self.assertEqual(ob.derive_repo_name("/home/x/devel/n8n/email_extract",
                                             name="email-extractor"),
                         "email-extractor")

    def test_lowercased_and_sanitized(self):
        self.assertEqual(ob.derive_repo_name("/home/x/devel/Foo_Bar"), "foo-bar")

    def test_deterministic_same_path_same_name(self):
        p = "/home/x/devel/montalu/vyuctovanie"
        self.assertEqual(ob.derive_repo_name(p), ob.derive_repo_name(p))


# --------------------------------------------------------------------------- #
# 2) Stack + gitignore (append-only, never overwrites).
# --------------------------------------------------------------------------- #
class TestStackAndGitignore(unittest.TestCase):
    def test_detect_python(self):
        with TemporaryDirectory() as d:
            (Path(d) / "pyproject.toml").write_text("[project]\n")
            self.assertEqual(ob.detect_stack(d), "python")

    def test_detect_rust(self):
        with TemporaryDirectory() as d:
            (Path(d) / "Cargo.toml").write_text("[package]\n")
            self.assertEqual(ob.detect_stack(d), "rust")

    def test_detect_node(self):
        with TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text("{}\n")
            self.assertEqual(ob.detect_stack(d), "node")

    def test_missing_patterns_when_no_gitignore(self):
        with TemporaryDirectory() as d:
            missing = ob.gitignore_missing_patterns(d, "rust")
            self.assertIn("target/", missing)
            self.assertIn("__pycache__/", missing)

    def test_append_only_keeps_existing_patterns(self):
        with TemporaryDirectory() as d:
            (Path(d) / ".gitignore").write_text("__pycache__/\ncustom-user-line/\n")
            missing = ob.gitignore_missing_patterns(d, "python")
            # already-present pattern is NOT re-listed
            self.assertNotIn("__pycache__/", missing)
            # a user's own custom line is never proposed for removal
            self.assertTrue(all("custom-user-line/" != m for m in missing))


# --------------------------------------------------------------------------- #
# 3) Branch-convention detection — respects existing default, odoo 3-branch.
# --------------------------------------------------------------------------- #
class TestBranchDetection(unittest.TestCase):
    def test_detect_main_default(self):
        with TemporaryDirectory() as d:
            init_repo(d, default_branch="main")
            self.assertEqual(ob.detect_default_branch(d), "main")

    def test_detect_master_default_never_changed(self):
        with TemporaryDirectory() as d:
            init_repo(d, default_branch="master")
            self.assertEqual(ob.detect_default_branch(d), "master")

    def test_branch_model_two_branch(self):
        with TemporaryDirectory() as d:
            init_repo(d, default_branch="master")
            model = ob.detect_branch_model(d)
            self.assertEqual(model["branch_model"], "2-branch")
            self.assertEqual(model["default_branch"], "master")
            self.assertEqual(model["work_branch"], "dev")

    def test_odoo_three_branch_override(self):
        with TemporaryDirectory() as d:
            init_repo(d, default_branch="main")
            model = ob.detect_branch_model(d, overrides=["3-branch"])
            self.assertEqual(model["branch_model"], "3-branch")
            self.assertEqual(model["work_branch"], "develop")


# --------------------------------------------------------------------------- #
# 4) Registry read/write/upsert.
# --------------------------------------------------------------------------- #
class TestRegistry(unittest.TestCase):
    def test_upsert_adds_then_replaces_by_name(self):
        entries = []
        e1 = {"name": "foo", "host": "dev1", "path": "~/devel/foo"}
        entries = ob.upsert_entry(entries, e1)
        self.assertEqual(len(entries), 1)
        e1b = {"name": "foo", "host": "dev2", "path": "~/devel/foo"}
        entries = ob.upsert_entry(entries, e1b)
        self.assertEqual(len(entries), 1)  # replaced, not duplicated
        self.assertEqual(entries[0]["host"], "dev2")

    def test_save_load_roundtrip(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "projects-registry.json"
            entries = [{"name": "a", "host": "dev1"}]
            ob.save_registry(str(p), entries)
            self.assertEqual(ob.load_registry(str(p)), entries)

    def test_load_missing_returns_empty(self):
        with TemporaryDirectory() as d:
            self.assertEqual(ob.load_registry(str(Path(d) / "nope.json")), [])


# --------------------------------------------------------------------------- #
# 5) Idempotency — the core invariant. 2nd run = all-satisfied, zero mutation.
# --------------------------------------------------------------------------- #
class TestIdempotency(unittest.TestCase):
    def test_second_run_all_satisfied_no_mutation(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, default_branch="main",
                      remote="https://github.com/zbynekdrlik/foo.git")
            reg = str(Path(rd) / "projects-registry.json")
            run = FakeRunner()

            r1 = ob.onboard_project(str(repo), name="foo", registry_path=reg,
                                    run=run)
            statuses1 = {s["step"]: s["status"] for s in r1["steps"]}
            # gitignore + CLAUDE.md must have been applied on the fresh repo
            self.assertEqual(statuses1["gitignore"], "applied")
            self.assertEqual(statuses1["claude_md"], "applied")

            head_after_1 = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()

            r2 = ob.onboard_project(str(repo), name="foo", registry_path=reg,
                                    run=run)
            statuses2 = {s["step"]: s["status"] for s in r2["steps"]}
            for step in ("git_init", "remote", "gitignore", "claude_md",
                         "registry"):
                self.assertEqual(statuses2[step], "satisfied",
                                 "%s not satisfied on 2nd run" % step)

            head_after_2 = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            self.assertEqual(head_after_1, head_after_2,
                             "2nd run created a commit (not idempotent)")

    def test_never_creates_real_github_repo_when_remote_present(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote="https://github.com/zbynekdrlik/foo.git")
            run = FakeRunner()
            ob.onboard_project(str(repo), name="foo",
                               registry_path=str(Path(rd) / "r.json"), run=run)
            # remote already present → no `gh repo create` EVER
            self.assertEqual(run.gh_matching("repo", "create"), [])

    def test_existing_claude_md_never_overwritten(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote="https://github.com/zbynekdrlik/foo.git")
            sentinel = "# My own CLAUDE.md — do not touch\n"
            (repo / "CLAUDE.md").write_text(sentinel)
            subprocess.run(["git", "-C", str(repo), "add", "CLAUDE.md"],
                           check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                            "claude"], check=True)
            run = FakeRunner()
            r = ob.onboard_project(str(repo), name="foo",
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=run)
            self.assertEqual((repo / "CLAUDE.md").read_text(), sentinel)
            statuses = {s["step"]: s["status"] for s in r["steps"]}
            self.assertEqual(statuses["claude_md"], "satisfied")


# --------------------------------------------------------------------------- #
# 6) Dirty worktree guard — never touch a project's in-progress files.
# --------------------------------------------------------------------------- #
class TestDirtyWorktreeGuard(unittest.TestCase):
    def test_modified_tracked_gitignore_not_touched(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote="https://github.com/zbynekdrlik/foo.git")
            (repo / ".gitignore").write_text("__pycache__/\n")
            subprocess.run(["git", "-C", str(repo), "add", ".gitignore"],
                           check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "gi"],
                           check=True)
            # now the project session has UNCOMMITTED edits to .gitignore
            (repo / ".gitignore").write_text("__pycache__/\nWORK-IN-PROGRESS\n")
            run = FakeRunner()
            r = ob.onboard_project(str(repo), name="foo",
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=run)
            # the in-progress content is preserved untouched
            self.assertIn("WORK-IN-PROGRESS", (repo / ".gitignore").read_text())
            statuses = {s["step"]: s["status"] for s in r["steps"]}
            self.assertEqual(statuses["gitignore"], "skipped")

    def test_file_is_dirty_detection(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            init_repo(repo)
            (repo / "README.md").write_text("changed\n")
            self.assertTrue(ob.file_is_dirty(str(repo), "README.md"))
            self.assertFalse(ob.file_is_dirty(str(repo), "nonexistent.md"))


# --------------------------------------------------------------------------- #
# 7) Foundation-gap + notification tickets — filed via gh, gated + idempotent.
# --------------------------------------------------------------------------- #
class TestFoundationAndNotificationTickets(unittest.TestCase):
    def test_notification_ticket_body_has_scope_gate_and_is_gated(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote="https://github.com/zbynekdrlik/foo.git")
            # gh issue list returns empty (no existing onboarding ticket)
            def gh(argv):
                if "list" in argv:
                    return subprocess.CompletedProcess(argv, 0, "[]", "")
                if "create" in argv:
                    return subprocess.CompletedProcess(
                        argv, 0,
                        "https://github.com/zbynekdrlik/foo/issues/1\n", "")
                return subprocess.CompletedProcess(argv, 0, "", "")
            run = FakeRunner(gh_handler=gh)
            ob.onboard_project(str(repo), name="foo",
                               registry_path=str(Path(rd) / "r.json"), run=run)
            created = run.gh_matching("issue", "create")
            self.assertTrue(created, "no onboarding notification ticket filed")
            # every filed issue body carries a Scope-gate line (block-ungated gate)
            for c in created:
                body = " ".join(c)
                self.assertIn("Scope-gate:", body)

    def test_notification_ticket_not_refiled_when_present(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote="https://github.com/zbynekdrlik/foo.git")
            existing = json.dumps([{"number": 1, "title": ob.ONBOARD_TICKET_TITLE}])
            def gh(argv):
                if "list" in argv:
                    return subprocess.CompletedProcess(argv, 0, existing, "")
                return subprocess.CompletedProcess(argv, 0, "", "")
            run = FakeRunner(gh_handler=gh)
            r = ob.onboard_project(str(repo), name="foo",
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=run)
            self.assertEqual(run.gh_matching("issue", "create", "onboard"), [])
            statuses = {s["step"]: s["status"] for s in r["steps"]}
            self.assertEqual(statuses["notification_ticket"], "satisfied")


# --------------------------------------------------------------------------- #
# 8) --audit / --check — READ-ONLY drift report, no mutations.
# --------------------------------------------------------------------------- #
class TestAuditDrift(unittest.TestCase):
    def test_audit_reports_missing_remote(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            init_repo(repo, commit=True, remote=None)  # NO remote
            entry = {"name": "foo", "host": "dev1", "path": str(repo),
                     "branch_model": "2-branch", "default_branch": "main",
                     "work_branch": "dev"}
            drift = ob.audit_project(entry)
            kinds = {x["kind"] for x in drift}
            self.assertIn("missing-remote", kinds)

    def test_audit_reports_tracked_pycache(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            init_repo(repo, remote="https://github.com/zbynekdrlik/foo.git")
            junk = repo / "__pycache__"
            junk.mkdir()
            (junk / "x.pyc").write_text("x")
            subprocess.run(["git", "-C", str(repo), "add", "-f",
                            "__pycache__/x.pyc"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "j"],
                           check=True)
            entry = {"name": "foo", "host": "dev1", "path": str(repo),
                     "branch_model": "2-branch", "default_branch": "main",
                     "work_branch": "dev"}
            drift = ob.audit_project(entry)
            kinds = {x["kind"] for x in drift}
            self.assertIn("tracked-artifact", kinds)

    def test_audit_reports_branch_model_mismatch(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            init_repo(repo, default_branch="master",
                      remote="https://github.com/zbynekdrlik/foo.git")
            entry = {"name": "foo", "host": "dev1", "path": str(repo),
                     "branch_model": "2-branch", "default_branch": "main",
                     "work_branch": "dev"}  # registry says main, repo is master
            drift = ob.audit_project(entry)
            kinds = {x["kind"] for x in drift}
            self.assertIn("branch-model-mismatch", kinds)

    def test_audit_is_read_only(self):
        with TemporaryDirectory() as d:
            repo = Path(d)
            init_repo(repo, commit=True, remote=None)
            before = tracked_files(repo)
            head_before = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            run = FakeRunner()
            entry = {"name": "foo", "host": "dev1", "path": str(repo),
                     "branch_model": "2-branch", "default_branch": "main",
                     "work_branch": "dev"}
            ob.audit_project(entry, run=run)
            # no writes: no gh mutations, no new commit, no tracked-file change
            self.assertEqual(run.gh_matching("issue", "create"), [])
            self.assertEqual(run.gh_matching("repo", "create"), [])
            after = tracked_files(repo)
            head_after = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True, text=True).stdout.strip()
            self.assertEqual(before, after)
            self.assertEqual(head_before, head_after)

    def test_audit_reports_missing_registry_entry_for_path(self):
        # a project on disk that has no registry entry drifts as unregistered
        with TemporaryDirectory() as d:
            reg = Path(d) / "projects-registry.json"
            ob.save_registry(str(reg), [{"name": "a", "path": "~/devel/a"}])
            missing = ob.registry_entry_for(str(reg), "nonexistent")
            self.assertIsNone(missing)


# --------------------------------------------------------------------------- #
# 9) Dry-run — reports would-apply, mutates nothing.
# --------------------------------------------------------------------------- #
class TestDryRun(unittest.TestCase):
    def test_dry_run_no_mutation(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            repo = Path(d)
            init_repo(repo, remote="https://github.com/zbynekdrlik/foo.git")
            reg = str(Path(rd) / "r.json")
            run = FakeRunner()
            r = ob.onboard_project(str(repo), name="foo", registry_path=reg,
                                   run=run, dry_run=True)
            statuses = {s["step"]: s["status"] for s in r["steps"]}
            self.assertEqual(statuses["gitignore"], "would-apply")
            self.assertEqual(statuses["claude_md"], "would-apply")
            # nothing written
            self.assertFalse((repo / "CLAUDE.md").exists())
            self.assertFalse(Path(reg).exists())
            self.assertEqual(run.gh_matching("issue", "create"), [])


# --------------------------------------------------------------------------- #
# 10) Registry seed + CLI wiring sanity.
# --------------------------------------------------------------------------- #
class TestRegistrySeedAndWiring(unittest.TestCase):
    def test_shipped_registry_parses_and_has_managed_set(self):
        reg = Path(__file__).resolve().parent.parent / "projects-registry.json"
        self.assertTrue(reg.exists(), "projects-registry.json not shipped")
        entries = ob.load_registry(str(reg))
        names = {e["name"] for e in entries}
        # spot-check the current managed set (dev1 + dev2)
        for expected in ("camera-box", "montalu-vyuctovanie", "montalu-n8n",
                         "email-extractor", "parovanie-produktov",
                         "forecasting-storage"):
            self.assertIn(expected, names, expected)
        # every entry has the required schema fields
        for e in entries:
            for field in ("name", "host", "path", "branch_model",
                          "default_branch", "work_branch"):
                self.assertIn(field, e, "%s missing %s" % (e.get("name"), field))

    def test_command_registered_in_subcommands(self):
        import airuleset
        self.assertIn("onboard-project", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["onboard-project"],
                      ob.cmd_onboard_project)

    def test_skill_wired_into_skill_names(self):
        import airuleset
        self.assertIn("onboard-project", airuleset.SKILL_NAMES)
        skill = (Path(__file__).resolve().parent.parent
                 / "skills" / "onboard-project" / "SKILL.md")
        self.assertTrue(skill.exists(), "SKILL.md not shipped")


if __name__ == "__main__":
    unittest.main()
