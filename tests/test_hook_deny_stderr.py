"""Locks the STDERR contract for PreToolUse deny hooks (#682).

Claude Code surfaces ONLY a hook's STDERR to the model on a PreToolUse
`exit 2` deny; STDOUT is invisible to the model (shown to the human in
transcript mode only). A hook that prints its BLOCKED reason to stdout while
exiting 2 therefore denies the tool call with no explanation the model can
read -- the deny arrives as "No stderr output" and the session must open and
read the hook source to learn why (live spinbike incident, 2026-08-24).

Two complementary locks:

  1. `TestBashHookStderrContract` -- STATIC and DYNAMIC over every
     Bash-matcher PreToolUse hook wired in `settings/hooks.json` (the same
     discovery the STDIN meta-test `TestBashHookStdinContract` in
     `test_airuleset.py` uses -- this is its structural sibling). Any such
     hook that can `exit 2` MUST carry a stderr emit (`>&2`) or a JSON
     block-decision, so a NEW deny hook can never silently ship
     reason-to-stdout-only. Future-proof, coarse.

  2. `TestDenyReasonOnStderr` -- EMPIRICAL, one case per hook this ticket
     fixed: trip the real gate in a throwaway repo and assert the reason
     lands on fd2 (model-visible) with stdout NOT carrying the BLOCKED
     reason. Precise teeth. Mirrors the library's own split (a coarse
     contract lock + specific empirical hook tests like
     `TestSecretStagingHook`).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main, skipUnless

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"


def _git(repo, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True, env=env)


def _bash_pretooluse_hooks():
    """Every `hooks/*.sh` wired under a matcher=='Bash' PreToolUse entry in
    settings/hooks.json -- derived dynamically so a newly-added Bash deny
    hook is covered automatically. Same shape as
    `TestBashHookStdinContract._bash_hooks_from_settings` in
    test_airuleset.py (the STDIN sibling of this STDERR contract)."""
    cfg = json.loads((ROOT / "settings" / "hooks.json").read_text())
    names = []
    for entry in cfg.get("hooks", {}).get("PreToolUse", []):
        if entry.get("matcher") != "Bash":
            continue
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            if "airuleset/hooks/" in cmd:
                # first whitespace-delimited token only, so a future command
                # carrying trailing args (none do today) resolves to the bare
                # hook filename rather than "<name>.sh --arg".
                names.append(cmd.split("airuleset/hooks/")[-1].split()[0])
    return names


# A deny (exit 2) hook must route its reason to stderr, OR use the structured
# JSON permission-decision channel. Either is model-visible on a PreToolUse
# deny; plain stdout is not. `>&\s*2` matches both `>&2` and `1>&2`.
_STDERR_RE = re.compile(r">&\s*2")
_JSON_DECISION_RE = re.compile(r'permissionDecision|hookSpecificOutput|"decision"')
_EXIT2_RE = re.compile(r"\bexit 2\b")


class TestBashHookStderrContract(TestCase):
    """The general lock: no Bash PreToolUse hook may `exit 2` with its reason
    reachable only on stdout."""

    def test_discovery_finds_bash_hooks(self):
        names = _bash_pretooluse_hooks()
        self.assertGreaterEqual(len(names), 5)
        self.assertIn("pre-push-test-check.sh", names)

    def test_every_deny_hook_writes_its_reason_to_stderr(self):
        offenders = []
        for name in _bash_pretooluse_hooks():
            src = (HOOKS / name).read_text()
            if not _EXIT2_RE.search(src):
                continue  # never denies -> no reason to surface
            if _STDERR_RE.search(src) or _JSON_DECISION_RE.search(src):
                continue
            offenders.append(name)
        self.assertEqual(
            offenders, [],
            "these Bash PreToolUse hooks `exit 2` but emit no stderr (>&2) / "
            "JSON decision -- the block reason is invisible to the model on a "
            "deny (#682): %s" % ", ".join(offenders))


def _run_hook(name, command, cwd=None):
    env = dict(os.environ)
    env["HOME"] = tempfile.mkdtemp()  # isolate any audit-log writes
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(["bash", str(HOOKS / name)], input=payload, text=True,
                          capture_output=True, cwd=cwd, timeout=60, env=env)


class TestDenyReasonOnStderr(TestCase):
    """One empirical case per hook fixed in #682: on a real deny the reason is
    on fd2 (model-visible) and stdout does NOT carry the BLOCKED reason."""

    def _mktemp(self, prefix):
        d = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_pre_push_test_check_gate2_reason_on_stderr(self):
        # Gate 2 (RED-before-GREEN order): a bug-fix commit BEFORE any test
        # commit. A test file IS in the PR (so Gate 1's "no test at all" does
        # not pre-empt), but the fix commit precedes the test commit.
        repo = self._mktemp("airuleset-i682-ppt-")
        _git(repo, "init", "-q", "-b", "main")
        (Path(repo) / "app.py").write_text("def f():\n    return 1\n")
        _git(repo, "add", "app.py")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "update-ref", "refs/remotes/origin/main", base)
        _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD",
             "refs/remotes/origin/main")
        _git(repo, "checkout", "-qb", "dev")
        # commit A: the bug fix (feature code), no test yet
        (Path(repo) / "app.py").write_text("def f():\n    return 2\n")
        _git(repo, "add", "app.py")
        _git(repo, "commit", "-qm", "fix: correct return value")
        # commit B: the test, AFTER the fix
        os.makedirs(os.path.join(repo, "tests"))
        (Path(repo) / "tests" / "test_app.py").write_text(
            "def test_f():\n    assert 1 == 1\n    assert 2 == 2\n")
        _git(repo, "add", "tests/test_app.py")
        _git(repo, "commit", "-qm", "test: add coverage")
        r = _run_hook("pre-push-test-check.sh", "git push origin dev", repo)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stderr, "reason must be on stderr: " + repr(r))
        self.assertIn("BEFORE any test commit", r.stderr)
        self.assertNotIn("BLOCKED", r.stdout,
                         "deny reason must NOT be carried on stdout")

    def test_pre_push_test_check_gate1_reason_on_stderr(self):
        # Gate 1: feature code changed, no test anywhere in the PR.
        repo = self._mktemp("airuleset-i682-ppt1-")
        _git(repo, "init", "-q", "-b", "main")
        (Path(repo) / "app.py").write_text("x = 1\n")
        _git(repo, "add", "app.py")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "update-ref", "refs/remotes/origin/main",
             _git(repo, "rev-parse", "HEAD").stdout.strip())
        _git(repo, "checkout", "-qb", "dev")
        (Path(repo) / "feature.py").write_text("y = 2\n")
        _git(repo, "add", "feature.py")
        _git(repo, "commit", "-qm", "feat: add feature")
        r = _run_hook("pre-push-test-check.sh", "git push origin dev", repo)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NO test files modified", r.stderr)
        self.assertNotIn("BLOCKED", r.stdout)

    def test_block_history_rewrite_reason_on_stderr(self):
        cwd = self._mktemp("airuleset-i682-hist-")
        r = _run_hook("block-history-rewrite.sh", "git reset --hard HEAD~1", cwd)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stderr, "reason must be on stderr: " + repr(r))
        self.assertIn("reset --hard", r.stderr)
        self.assertNotIn("BLOCKED", r.stdout)

    def test_block_test_skips_reason_on_stderr(self):
        repo = self._mktemp("airuleset-i682-skip-")
        _git(repo, "init", "-q", "-b", "main")
        os.makedirs(os.path.join(repo, "tests"))
        tf = os.path.join(repo, "tests", "test_thing.py")
        Path(tf).write_text("def test_ok():\n    assert 1 == 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "update-ref", "refs/remotes/origin/main",
             _git(repo, "rev-parse", "HEAD").stdout.strip())
        # the skip marker is built by concatenation so THIS test file does
        # not itself carry a literal skip pattern that block-test-skips.sh
        # would flag on push (#682).
        skip_marker = "pytest.mark" + ".skip"
        with open(tf, "a") as fh:
            fh.write("\n@%s\ndef test_skipped():\n    assert 1 == 1\n" % skip_marker)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "test: add coverage")
        r = _run_hook("block-test-skips.sh", "git push origin main", repo)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stderr, "reason must be on stderr: " + repr(r))
        self.assertIn(skip_marker, r.stderr)
        self.assertNotIn("BLOCKED", r.stdout)

    @skipUnless(shutil.which("ruff"), "ruff not installed")
    def test_pre_push_lint_reason_on_stderr(self):
        repo = self._mktemp("airuleset-i682-lint-")
        _git(repo, "init", "-q", "-b", "main")
        (Path(repo) / "pyproject.toml").write_text('[project]\nname = "x"\n')
        (Path(repo) / "clean.py").write_text("VALUE = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "update-ref", "refs/remotes/origin/main",
             _git(repo, "rev-parse", "HEAD").stdout.strip())
        _git(repo, "checkout", "-qb", "dev")
        # a NEW python file with a ruff error (F401 unused import)
        (Path(repo) / "bad.py").write_text("import os\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "add bad")
        r = _run_hook("pre-push-lint.sh", "git push origin dev", repo)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("BLOCKED", r.stderr, "reason must be on stderr: " + repr(r))
        self.assertNotIn("BLOCKED", r.stdout)


if __name__ == "__main__":
    main()
