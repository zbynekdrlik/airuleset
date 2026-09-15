"""#1020 Part 2 item 2 -- block-sensitive-staging.sh must fail CLOSED.

The secrets gate is security-adjacent: it blocks staging a file whose name or
content carries a secret. Before this fix the adapter `exec`ed `gates.secrets`
DIRECTLY, so an INTERNAL python error (the gate could not run) exited non-2 and
Claude Code read it as "not a block" -> the tool PROCEEDED and the secret was
staged. That is fail-OPEN on the one gate where fail-open is worst. This locks
the fix: an internal malfunction now exits 2 with the HOOK MALFUNCTION text
(the same fail-closed wrapper the push adapters have), while a benign payload
still passes (rc 0) and a real secret still blocks (rc 2).
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "block-sensitive-staging.sh"


def _run(hook_path, command, *, cwd, clear_pythonpath=False, home=None):
    payload = json.dumps({"tool_input": {"command": command}})
    env = dict(os.environ)
    if home:
        env["HOME"] = home
    if clear_pythonpath:
        env.pop("PYTHONPATH", None)
    return subprocess.run(["bash", str(hook_path)], input=payload, text=True,
                          capture_output=True, cwd=cwd, timeout=30, env=env)


class TestSecretsAdapterFailClosed(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="airuleset-secrets-failclosed-")
        self.home = tempfile.mkdtemp(prefix="airuleset-secrets-fc-home-")

    def _isolated_hook_no_gates(self):
        """A copy of the adapter under a dir with NO sibling `gates/` package,
        so `python3 -m gates.secrets` genuinely fails to import -> the adapter's
        fail-closed wrapper is exercised. Returns (hook_path, gates_free_cwd)."""
        iso = Path(self.tmp) / "iso"
        (iso / "hooks").mkdir(parents=True)
        hook = iso / "hooks" / "block-sensitive-staging.sh"
        hook.write_text(HOOK.read_text())
        hook.chmod(0o755)
        # a cwd that also has no gates/, so `python -m`'s cwd-prepend can't
        # rescue the import either.
        cwd = Path(self.tmp) / "emptycwd"
        cwd.mkdir()
        return str(hook), str(cwd)

    def test_internal_error_fails_closed_exit_2(self):
        # The forced-malfunction case: gates is unresolvable -> the gate cannot
        # run. It MUST exit 2 with the HOOK MALFUNCTION text, never proceed.
        hook, cwd = self._isolated_hook_no_gates()
        r = _run(hook, "echo hi", cwd=cwd, clear_pythonpath=True, home=self.home)
        self.assertEqual(r.returncode, 2, "fail-OPEN: %r\n%s" % (r.returncode, r.stderr))
        self.assertIn("HOOK MALFUNCTION", r.stderr)
        self.assertIn("block-sensitive-staging", r.stderr)

    def test_benign_payload_still_passes(self):
        # A normal (non-add/non-commit) command still returns 0 with the real hook.
        r = _run(str(HOOK), "echo hello world", cwd=str(REPO), home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_real_secret_still_blocks(self):
        # A real secret in a staged file still blocks (rc 2) — the fix does not
        # weaken the actual gate. Build a real git repo and stage a .env.
        repo = Path(self.tmp) / "gitrepo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
        (repo / ".env").write_text("SECRET=x\n")
        r = _run(str(HOOK), "git add .env", cwd=str(repo), home=self.home)
        self.assertEqual(r.returncode, 2, r.stderr)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)


if __name__ == "__main__":
    main()
