"""meeting-analysis skill: the Soniox key load line must not collide with
the vault-store read guard (#275).

Two problems, one line: `skills/meeting-analysis/SKILL.md`'s Soniox key load
line named `$HOME/.claude/secrets/soniox.env` as its primary key source --
inside the directory `hooks/block-vault-store-read.sh` guards fleet-wide
(#153/#156/#157). The guard refuses ANY Bash/Read/Grep/Glob call whose TEXT
names that directory, by design, so the skill's own load line was
structurally unusable on every managed box regardless of whether a key
actually sat there. This test drives the REAL hook against the REAL,
currently-shipped line (never a hand-typed stand-in), so a future edit that
reintroduces the guarded path fails this test for the same reason the
original line did.

The fix moves the canonical path OUT of the guarded directory entirely:
primary `$HOME/.soniox.env`, fallback the existing dev1
`/home/newlevel/devel/voiceagent/.env` -- the guarded path is REMOVED, not
merely deprioritized (a third fallback pointing back at it would keep the
collision alive for anyone still holding a key there).
"""
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "skills" / "meeting-analysis" / "SKILL.md"
HOOK = REPO_ROOT / "hooks" / "block-vault-store-read.sh"

_LOAD_LINE_RE = re.compile(
    r"^\s*export\s+SONIOX_API_KEY=.*$", re.MULTILINE)


def _load_line():
    """The skill's OWN currently-shipped Soniox key load line -- extracted
    from the real file, never hand-typed, so an edit to the real line is
    what this test actually verifies."""
    text = SKILL_MD.read_text(encoding="utf-8")
    m = _LOAD_LINE_RE.search(text)
    assert m is not None, (
        "SKILL.md no longer has an `export SONIOX_API_KEY=...` line -- "
        "update this test's extraction pattern")
    return m.group(0)


def _run_hook(cmd):
    """Feed `cmd` to the REAL block-vault-store-read.sh as a genuine
    PreToolUse Bash payload -- the same oracle tests/test_vault_read_guard.py
    already uses (never a re-implemented regex guess at the hook's own
    matching rules)."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                          "cwd": str(REPO_ROOT)})
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/newlevel")}
    return subprocess.run(["/bin/bash", str(HOOK)], input=payload,
                          capture_output=True, text=True, env=env)


class TheLoadLineNeverNamesTheGuardedPath(unittest.TestCase):
    def test_no_dot_claude_secrets_component(self):
        line = _load_line()
        self.assertNotIn(".claude/secrets", line,
                         "the load line must never name the vault-guarded "
                         "directory -- see #153/#156/#157")

    def test_names_the_new_unguarded_primary_path(self):
        self.assertIn("$HOME/.soniox.env", _load_line())

    def test_still_names_the_dev1_voiceagent_fallback(self):
        self.assertIn("/home/newlevel/devel/voiceagent/.env", _load_line())


class TheLoadLinePassesTheRealVaultGuardHook(unittest.TestCase):
    """The decisive proof: run the REAL hook against the REAL line."""

    def test_the_shipped_load_line_is_not_blocked(self):
        line = _load_line()
        r = _run_hook(line)
        self.assertEqual(
            r.returncode, 0,
            "the skill's own load line is BLOCKED by block-vault-store-read.sh "
            "-- it still names the guarded path.\nline: %s\nstdout=%s\nstderr=%s"
            % (line, r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
