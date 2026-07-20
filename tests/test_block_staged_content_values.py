"""Behaviour tests for hooks/block-sensitive-staging.sh Gate 2 (issue #4).

The original hook only blocked SENSITIVE FILENAMES on `git add` (.env,
credentials.json, *.pem, ...). It never looked at file CONTENT, so a real
secret VALUE inlined inside an otherwise-allowed file (e.g. a committed
`.claude/skills/**` playbook, or `CLAUDE.md`) sailed straight through — this
is exactly how the playbook-rollout leak happened (camera-box #212 OBS WS
password, restreamer #271 FB App Secret).

Gate 2 scans the STAGED/ADDED content on `git add` AND `git commit` for
inlined secret values: `sshpass -p '<literal>'`, a
password|passphrase|secret|token|api_key key assigned a literal 8+ char
value, and 40+ char hex / 32+ char base64-ish high-entropy blobs. A
placeholder/env-ref value ($VAR, <secret>, {{...}}, YOUR_*, *EXAMPLE*) is
NOT flagged. Only ADDED lines are scanned (a diff, not the whole file) so
pre-existing committed content is never re-flagged.
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


def _git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True,
                    capture_output=True, text=True)


def _init_repo():
    d = tempfile.mkdtemp()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (Path(d) / "README.md").write_text("hello\n")
    _git(d, "add", "README.md")
    _git(d, "commit", "-q", "-m", "initial")
    return d


class SecretScanTestCase(TestCase):
    def setUp(self):
        self.repo = _init_repo()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)

    def _run(self, command, home=None):
        payload = json.dumps({"tool_input": {"command": command}})
        env = dict(os.environ)
        if home:
            env["HOME"] = home
        return subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                              capture_output=True, cwd=self.repo, timeout=30, env=env)

    def _write(self, name, content):
        p = Path(self.repo) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


class TestGateOneUnchanged(SecretScanTestCase):
    """Regression: the original filename gate must still work exactly as before."""

    def test_blocks_dotenv_by_filename(self):
        self._write(".env", "FOO=bar\n")
        r = self._run("git add .env")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("BLOCKED", r.stdout)

    def test_allows_normal_file(self):
        self._write("notes.md", "just some notes, nothing secret here\n")
        r = self._run("git add notes.md")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_unrelated_command_fast_exits(self):
        r = self._run("ls -la")
        self.assertEqual(r.returncode, 0, r.stdout)


class TestContentScanGitAdd(SecretScanTestCase):
    def test_blocks_literal_password_kv(self):
        self._write("skills/foo/SKILL.md", "OBS_WS_PASSWORD = \"realpassw0rd123\"\n")
        r = self._run("git add skills/foo/SKILL.md")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("SKILL.md", r.stdout)

    def test_allows_env_var_reference(self):
        self._write("skills/foo/SKILL.md", "OBS_WS_PASSWORD = \"$OBS_WS_PASSWORD\"\n")
        r = self._run("git add skills/foo/SKILL.md")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_angle_bracket_placeholder(self):
        self._write("skills/foo/SKILL.md", "sshpass -p '<value>'\n")
        r = self._run("git add skills/foo/SKILL.md")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_allows_your_prefix_placeholder(self):
        self._write("docs/setup.md", "api_key: \"YOUR_API_KEY_HERE\"\n")
        r = self._run("git add docs/setup.md")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_blocks_sshpass_literal(self):
        self._write("deploy.sh", "sshpass -p 'RealPassw0rd!' ssh user@host\n")
        r = self._run("git add deploy.sh")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("deploy.sh", r.stdout)

    def test_allows_sshpass_env_var(self):
        self._write("deploy.sh", 'sshpass -p "$DEVICE_PW" ssh user@host\n')
        r = self._run("git add deploy.sh")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_blocks_long_hex_blob(self):
        self._write("token.txt", "token=" + ("a1b2c3" * 7) + "\n")
        r = self._run("git add token.txt")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_blocks_high_entropy_base64_blob(self):
        self._write("token.txt", "ghp_9f8A2xQ7mL4kR1vN6pC0yD3zJ8wE5tB2sH9uK\n")
        r = self._run("git add token.txt")
        self.assertEqual(r.returncode, 2, r.stdout)

    def test_no_false_positive_on_plain_identifiers(self):
        # Long, all-letters camelCase identifiers (no digits) must NOT trip
        # the entropy check — this is the exact false-positive class the
        # pattern was tuned against (e.g. long Python test-class names).
        self._write("code.py", "class TestProseViolationsAutoMergeSignalsHandler:\n    pass\n")
        r = self._run("git add code.py")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_no_false_positive_on_file_paths(self):
        self._write("notes.md", "See /home/newlevel/devel/airuleset/skills/meeting-analysis/SKILL.md\n")
        r = self._run("git add notes.md")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_only_scans_added_lines_not_whole_file(self):
        # Commit a file with a benign line, then modify an UNRELATED line —
        # a secret already safely committed earlier must not be re-flagged.
        self._write("mixed.md", "password = \"alreadycommitted1\"\nfoo: bar\n")
        _git(self.repo, "add", "mixed.md")
        _git(self.repo, "commit", "-q", "-m", "wip")
        self._write("mixed.md", "password = \"alreadycommitted1\"\nfoo: baz\n")
        r = self._run("git add mixed.md")
        self.assertEqual(r.returncode, 0, r.stdout)


class TestContentScanGitCommit(SecretScanTestCase):
    def test_commit_backstop_catches_already_staged_secret(self):
        # Simulate the secret having been staged WITHOUT going through this
        # hook (e.g. `git add -p`, or a caller that bypassed add-time
        # scanning) — the commit-time gate must still catch it.
        self._write("skills/bar/SKILL.md", "FB_APP_SECRET: \"abcd1234efgh5678\"\n")
        _git(self.repo, "add", "skills/bar/SKILL.md")
        r = self._run('git commit -m "add playbook notes"')
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("SKILL.md", r.stdout)

    def test_commit_allows_clean_staged_content(self):
        self._write("skills/bar/SKILL.md", "Use `$DEVICE_PW` from the env, never hardcode it.\n")
        _git(self.repo, "add", "skills/bar/SKILL.md")
        r = self._run('git commit -m "add playbook notes"')
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_commit_dash_a_catches_unstaged_tracked_secret(self):
        self._write("cfg.md", "start\n")
        _git(self.repo, "add", "cfg.md")
        _git(self.repo, "commit", "-q", "-m", "wip")
        self._write("cfg.md", "start\ntoken: \"sk_live_abcdefgh12345678\"\n")
        r = self._run('git commit -am "update cfg"')
        self.assertEqual(r.returncode, 2, r.stdout)


class TestBypassMarker(SecretScanTestCase):
    def test_bypass_allows_and_logs(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        self._write("fixture.md", "password = \"totallyrealsecret1\"\n")
        r = self._run(
            "git add fixture.md  # airuleset:secret-ok test fixture value, not a real secret",
            home=home,
        )
        self.assertEqual(r.returncode, 0, r.stdout)
        log = os.path.join(home, "devel", "airuleset", "audits", "secret-scan-bypasses.log")
        self.assertTrue(os.path.exists(log), "bypass must be logged")
        self.assertIn("test fixture value", open(log).read())

    def test_marker_mentioned_inside_a_commit_message_body_is_not_a_bypass(self):
        # A commit message that merely DOCUMENTS the bypass syntax (e.g. this
        # hook's own commit history) must NOT be treated as an actual bypass
        # — only a genuine TRAILING comment on the command counts. Without
        # this, any commit whose message happens to mention the marker text
        # silently skips scanning its own diff.
        self._write("skills/bar/SKILL.md", "FB_APP_SECRET: \"abcd1234efgh5678\"\n")
        _git(self.repo, "add", "skills/bar/SKILL.md")
        commit_cmd = (
            'git commit -m "docs: mention the bypass syntax '
            '# airuleset:secret-ok <reason> right here, but then keep writing '
            'more real content afterwards so it is clearly NOT a trailing comment"'
        )
        r = self._run(commit_cmd)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("SKILL.md", r.stdout)


if __name__ == "__main__":
    main()


class TestLockfileWhitelist(SecretScanTestCase):
    """#19: npm/yarn/cargo lockfile integrity hashes are NOT secrets — the
    entropy gate false-flagged package-lock.json sha512 blobs (bkshading e2e,
    bypassed with airuleset:secret-ok). Known lockfile basenames skip the
    content scan entirely; the same blob in any other file still blocks."""

    BLOB = ('    "integrity": "sha512-'
            "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb3dEf6h" '==",\n')

    def test_package_lock_integrity_hash_is_allowed(self):
        self._write("e2e/package-lock.json", "{\n" + self.BLOB + "}\n")
        r = self._run("git add e2e/package-lock.json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_other_lockfiles_allowed(self):
        for name in ("yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "go.sum",
                     "poetry.lock", "flake.lock"):
            self._write(name, self.BLOB)
            r = self._run("git add " + name)
            self.assertEqual(r.returncode, 0, name + ": " + r.stdout + r.stderr)

    def test_same_blob_in_regular_file_still_blocked(self):
        self._write("notes.md", self.BLOB)
        r = self._run("git add notes.md")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
