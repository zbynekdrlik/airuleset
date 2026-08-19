"""#583 — `onboard-project --host <remote>` must run EVERY detect + act step
over ssh on the target, never locally.

Live incident (2026-08-19): `onboard-project "~/devel/montalu/automatizacie-
montalu" --host dev2 --dry-run` reported EVERY step `would-apply` — detection
ran LOCALLY on dev1 (where the path does not exist) instead of over ssh on
dev2, and the tilde never expanded remotely. A real run would have produced a
local junk dir + a NEW colliding GH repo `montalu-automatizacie-montalu` (the
name was ALSO wrongly doubled). This file pins the CORRECT behavior and FAILS
against the pre-fix code.

OFFLINE: no real ssh/network is EVER touched — a `RemoteRunner` fixture models
a healthy (or unreachable) remote box; local sub-calls (registry) run for real.
"""

import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_onboard as ob

CP = subprocess.CompletedProcess
REMOTE_URL = "https://github.com/zbynekdrlik/foo583.git"
# dev2 is a real REMOTE_HOSTS entry, so resolve_remote("dev2") ssh-wraps.
REMOTE_HOST = "dev2"


class RemoteRunner:
    """Injected runner modeling a remote box reached over ssh. `gh` is
    intercepted (never a real ticket); `ssh` is answered from a fixture model;
    any LOCAL command (host=None) runs for real. Flags model the remote repo
    state so the same fixture can prove detection went remote AND that writes
    go over ssh (never to the local filesystem)."""

    def __init__(self, reachable=True, claude_present=True,
                 gitignore_present=True, has_ci=True, home="/home/newlevel",
                 gitignore_content=None):
        self.calls = []
        self.ssh_calls = []
        self.gh_calls = []
        self.writes = []            # remote `sh -c 'cat > PATH'` targets seen
        self.reachable = reachable
        self.claude_present = claude_present
        self.gitignore_present = gitignore_present
        self.has_ci = has_ci
        self.home = home
        self.gitignore_content = gitignore_content

    # -- introspection helpers for assertions -----------------------------
    def ssh_cmd_strings(self):
        """The remote command string of every ssh call (last argv element)."""
        return [c[-1] for c in self.ssh_calls if c]

    # -- runner protocol --------------------------------------------------
    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        head = argv[0] if argv else ""
        if head == "gh":
            self.gh_calls.append(list(argv))
            return CP(argv, 0, "[]", "")
        if head == "ssh":
            self.ssh_calls.append(list(argv))
            return self._answer_ssh(argv)
        # local test/cat/sh/find/git — run for real against the fixture
        return subprocess.run(argv, **kw)

    def _answer_ssh(self, argv):
        try:
            cmd = shlex.split(argv[-1])
        except ValueError:
            cmd = [argv[-1]]
        if not self.reachable:
            return CP(cmd, 255, "", "ssh: connect to host: Connection refused")
        if not cmd:
            return CP(cmd, 0, "", "")
        head = cmd[0]
        if head == "true":
            return CP(cmd, 0, "", "")
        if head == "printf":
            return CP(cmd, 0, self.home, "")
        if head == "test":
            return self._test(cmd)
        if head == "cat":
            path = cmd[-1]
            if path.endswith("CLAUDE.md"):
                if not self.claude_present:
                    return CP(cmd, 1, "", "cat: No such file or directory")
                # a healthy CLAUDE.md carries the Playbook router (no drift)
                return CP(cmd, 0, "# proj\n\n## Playbook router\n- x\n", "")
            if not self.gitignore_present:
                return CP(cmd, 1, "", "cat: No such file or directory")
            content = self.gitignore_content
            if content is None:
                content = "\n".join(ob.GITIGNORE_COMMON) + "\n"
            return CP(cmd, 0, content, "")
        if head == "sh" and len(cmd) >= 3 and cmd[1] == "-c" \
                and cmd[2].startswith("cat >"):
            self.writes.append(cmd[2])
            return CP(cmd, 0, "", "")
        if head == "find":
            return CP(cmd, 0, "ci.yml\n" if self.has_ci else "", "")
        if head == "git":
            return self._git(cmd)
        return CP(cmd, 0, "", "")

    def _test(self, cmd):
        path = cmd[-1]
        if path.endswith("CLAUDE.md"):
            return CP(cmd, 0 if self.claude_present else 1, "", "")
        if any(path.endswith(x) for x in ("Cargo.toml", "package.json",
                                          "setup.py", "requirements.txt")):
            return CP(cmd, 1, "", "")           # not rust/node
        return CP(cmd, 0, "", "")               # pyproject.toml, dirs, etc. exist

    def _git(self, cmd):
        s = " ".join(cmd)
        if "rev-parse --git-dir" in s:
            return CP(cmd, 0, ".git\n", "")
        if "remote get-url origin" in s:
            return CP(cmd, 0, REMOTE_URL + "\n", "")
        if "symbolic-ref --short refs/remotes/origin/HEAD" in s:
            return CP(cmd, 0, "origin/main\n", "")
        if "rev-parse --verify --quiet refs/heads/" in s:
            return CP(cmd, 0, "abc123\n", "")   # work branch present
        if "symbolic-ref --short HEAD" in s:
            return CP(cmd, 0, "dev\n", "")
        if "ls-files" in s or "diff --cached" in s or "status --porcelain" in s:
            return CP(cmd, 0, "", "")           # clean, no tracked artifacts
        return CP(cmd, 0, "", "")               # add/commit/rm/init/branch ok


# --------------------------------------------------------------------------- #
# 1) Name derivation — collapse the cluster prefix when the leaf carries it.
# --------------------------------------------------------------------------- #
class TestClusterPrefixCollapse(unittest.TestCase):
    def test_leaf_already_carries_cluster_name_is_not_doubled(self):
        self.assertEqual(
            ob.derive_repo_name("/home/x/devel/montalu/automatizacie-montalu"),
            "automatizacie-montalu")

    def test_case_insensitive_collapse(self):
        self.assertEqual(
            ob.derive_repo_name("/home/x/devel/montalu/Automatizacie-Montalu"),
            "automatizacie-montalu")

    def test_leaf_without_cluster_token_still_prefixed(self):
        # the collapse must NOT swallow the legitimate prefix cases
        self.assertEqual(ob.derive_repo_name("/home/x/devel/montalu/n8n"),
                         "montalu-n8n")
        self.assertEqual(ob.derive_repo_name("/home/x/devel/montalu/vyuctovanie"),
                         "montalu-vyuctovanie")

    def test_leaf_starting_with_cluster_token_not_doubled(self):
        self.assertEqual(
            ob.derive_repo_name("/home/x/devel/montalu/montalu-vyuctovanie"),
            "montalu-vyuctovanie")


# --------------------------------------------------------------------------- #
# 2) Remote detection runs over ssh — a healthy remote repo is `satisfied`,
#    never `would-apply` (the incident's false report).
# --------------------------------------------------------------------------- #
class TestRemoteDetectionOverSsh(unittest.TestCase):
    def test_healthy_remote_repo_all_satisfied_dry_run(self):
        with TemporaryDirectory() as rd:
            # a path that does NOT exist locally — proves detection is remote
            local_missing = "/nonexistent-583-remote-only/proj"
            run = RemoteRunner()
            r = ob.onboard_project(local_missing, host=REMOTE_HOST, name="foo583",
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=run, dry_run=True)
            self.assertIsNone(r.get("error"), r.get("error"))
            st = {s["step"]: s["status"] for s in r["steps"]}
            for step in ("git_init", "remote", "branches", "gitignore",
                         "claude_md"):
                self.assertEqual(st[step], "satisfied",
                                 "%s ran locally (false %s), not over ssh"
                                 % (step, st[step]))
            # detection genuinely went over ssh
            self.assertTrue(run.ssh_calls, "no ssh call — detection ran locally")
            # nothing local was created for a remote path
            self.assertFalse(os.path.exists(local_missing))


# --------------------------------------------------------------------------- #
# 3) No local writes for a remote host — gitignore/CLAUDE.md go over ssh.
# --------------------------------------------------------------------------- #
class TestNoLocalWritesForRemote(unittest.TestCase):
    def test_writes_go_over_ssh_never_to_local_fs(self):
        with TemporaryDirectory() as d, TemporaryDirectory() as rd:
            localdir = Path(d) / "proj583"
            localdir.mkdir()                      # exists locally + empty
            run = RemoteRunner(claude_present=False, gitignore_present=False)
            r = ob.onboard_project(str(localdir), host=REMOTE_HOST, name="foo583",
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=run, dry_run=False)
            self.assertIsNone(r.get("error"), r.get("error"))
            st = {s["step"]: s["status"] for s in r["steps"]}
            self.assertEqual(st["claude_md"], "applied")
            self.assertEqual(st["gitignore"], "applied")
            # NOTHING written to the local filesystem
            self.assertFalse((localdir / "CLAUDE.md").exists(),
                             "CLAUDE.md written LOCALLY for a remote host")
            self.assertFalse((localdir / ".gitignore").exists(),
                             ".gitignore written LOCALLY for a remote host")
            # the writes went over ssh
            self.assertTrue(any("CLAUDE.md" in w for w in run.writes),
                            "no remote CLAUDE.md write recorded")
            self.assertTrue(any(".gitignore" in w for w in run.writes),
                            "no remote .gitignore write recorded")


# --------------------------------------------------------------------------- #
# 4) Fail-safe — an unreachable remote REFUSES the real run (never proceeds on
#    local false-negatives), and dry-run surfaces the error too.
# --------------------------------------------------------------------------- #
class TestUnreachableRemoteRefuses(unittest.TestCase):
    def test_real_run_refuses_on_unreachable_host(self):
        with TemporaryDirectory() as rd:
            run = RemoteRunner(reachable=False)
            r = ob.onboard_project("/home/x/devel/foo583", host=REMOTE_HOST,
                                   name="foo583",
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=run, dry_run=False)
            self.assertTrue(r.get("error"),
                            "unreachable host did NOT refuse — proceeded on "
                            "local false-negatives")
            self.assertEqual(r["steps"], [], "steps ran despite unreachable host")
            # no repo/ticket created anywhere
            self.assertEqual([c for c in run.gh_calls if "create" in c], [])

    def test_dry_run_surfaces_the_error(self):
        with TemporaryDirectory() as rd:
            run = RemoteRunner(reachable=False)
            r = ob.onboard_project("/home/x/devel/foo583", host=REMOTE_HOST,
                                   name="foo583",
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=run, dry_run=True)
            self.assertTrue(r.get("error"),
                            "dry-run hid the unreachable-host error")


# --------------------------------------------------------------------------- #
# 5) Tilde expands REMOTELY (quoting) — the ssh git commands carry the
#    remote-home-expanded absolute path, never a literal `~`.
# --------------------------------------------------------------------------- #
class TestRemoteTildeExpansion(unittest.TestCase):
    def test_tilde_expands_against_remote_home(self):
        with TemporaryDirectory() as rd:
            run = RemoteRunner(home="/remote/home583")
            ob.onboard_project("~/devel/foo583", host=REMOTE_HOST, name="foo583",
                               registry_path=str(Path(rd) / "r.json"),
                               run=run, dry_run=True)
            joined = " || ".join(run.ssh_cmd_strings())
            self.assertIn("/remote/home583/devel/foo583", joined,
                          "path was not expanded against the REMOTE home")
            # never a literal unexpanded tilde path in a remote git command
            for s in run.ssh_cmd_strings():
                if s.startswith("git "):
                    self.assertNotIn("~/devel/foo583", s,
                                     "literal ~ leaked into a remote git command")


# --------------------------------------------------------------------------- #
# 6) --audit uses the same remote ssh path — healthy remote audits clean, an
#    unreachable host audits as `unreachable` (never as drift).
# --------------------------------------------------------------------------- #
class TestAuditRemote(unittest.TestCase):
    def _entry(self, path, host):
        return {"name": "foo583", "host": host, "path": path,
                "branch_model": "2-branch", "default_branch": "main",
                "work_branch": "dev"}

    def test_healthy_remote_audits_clean(self):
        run = RemoteRunner()
        drift = ob.audit_project(self._entry("/home/x/devel/foo583", REMOTE_HOST),
                                 host=REMOTE_HOST, run=run)
        self.assertEqual(drift, [], "healthy remote project reported drift: %r"
                         % drift)
        self.assertTrue(run.ssh_calls, "audit ran locally, not over ssh")

    def test_unreachable_remote_audits_as_unreachable_not_drift(self):
        run = RemoteRunner(reachable=False)
        drift = ob.audit_project(self._entry("/home/x/devel/foo583", REMOTE_HOST),
                                 host=REMOTE_HOST, run=run)
        kinds = {d["kind"] for d in drift}
        self.assertIn("unreachable", kinds)
        self.assertNotIn("missing-repo", kinds,
                         "unreachable host falsely reported as missing-repo drift")


if __name__ == "__main__":
    unittest.main()
