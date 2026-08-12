"""block-tier0-local-build.sh — close the evidenced #381 bypass shapes.

#381 (user, 2026-08-11, repeatedly, angry): ~25GB of Rust `target/` grew on
dev1 in Tier-0 (markerless) projects despite this hook existing since
`b25a893` and being registered as a PreToolUse(Bash) hook. STEP 0's
investigation (issue #381, comment) traced this to THREE evidenced gaps in
the hook itself, none of them a bug in its direct `cargo build`/`cargo test`
regex (that part already worked correctly):

  Shape A — the sanctioned `# airuleset:build-ok <reason>` one-off escape
    hatch is self-granted with zero accountability: nothing ever logs who
    used it, when, or why (camera-box worktree agent-ad32605ec3b7d0e03,
    spinbike main + worktree agent-a035c9b671927d76d, all traced from
    session transcripts). This file adds a durable audit log for every
    bypass/block decision — the marker itself stays untouched/sanctioned.

  Shape B — a heavy build hidden INSIDE an invoked wrapper/deploy script
    (`dantesync/install.sh:72`'s unconditional `cargo build --release`) is
    invisible to the hook: `is_heavy()` only ever pattern-matched the
    literal `tool_input.command` text, and `bash install.sh` / `sudo
    ./install.sh` never contains the substring "cargo build" itself.

  Shape C — discovered while designing the Shape-A fix, and evidenced
    directly against the OLD (unfixed) hook by this file's own
    `MarkerIsQuoteAwareTest`: the marker check was a naive
    `case "$CMD" in *"airuleset:build-ok"*)` with NO quote-stripping, so the
    marker text merely being MENTIONED inside an unrelated quoted string
    (e.g. a commit message) incorrectly exempted a REAL, unrelated heavy
    build chained on the same command line. `block-destructive-remote.sh`
    already documents fixing this exact class of bug for its own marker
    (`# airuleset:destructive-ok`) — same fix applied here.

Every test in this file shells out to the REAL, shipped
`hooks/block-tier0-local-build.sh` (never mocked) — `airuleset.py`'s own
`_tier0_via_hook()` depends on this hook being the single source of truth
for tier resolution (test_target_purge.py's own docstring), and a mock here
would let the two drift apart with nothing to catch it.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402

REPO = Path(airuleset.__file__).resolve().parent
HOOK = REPO / "hooks" / "block-tier0-local-build.sh"


def payload(command, cwd):
    return json.dumps({"tool_input": {"command": command}, "cwd": cwd})


class _Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.audit_log = self.root / "audit.log"

    def _mkproj(self, name="proj", marker=None):
        """A minimal Tier-0 (or Tier-1/2, if `marker` given) project: a real
        `.git` dir (so `git rev-parse --show-toplevel` resolves for the
        audit log's project name) + a CLAUDE.md."""
        p = self.root / name
        p.mkdir(parents=True, exist_ok=True)
        (p / ".git").mkdir(exist_ok=True)
        body = "# no marker\n"
        if marker:
            body = "## Local Build Policy\n\n<!-- airuleset:local-builds=%s -->\n" % marker
        (p / "CLAUDE.md").write_text(body)
        return p

    def run_hook(self, command, cwd, extra_env=None):
        env = dict(os.environ)
        env["AIRULESET_TIER0_AUDIT_LOG"] = str(self.audit_log)
        env.pop("AIRULESET_ALLOW_LOCAL_BUILD", None)
        if extra_env:
            env.update(extra_env)
        out = subprocess.run(
            ["bash", str(HOOK)], input=payload(command, str(cwd)),
            text=True, env=env, capture_output=True, timeout=30)
        return out

    def audit_lines(self):
        if not self.audit_log.exists():
            return []
        return [ln for ln in self.audit_log.read_text().splitlines() if ln.strip()]


class WiringTest(_Runner):
    def test_hook_exists_and_is_wired_as_a_pretooluse_bash_hook(self):
        self.assertTrue(HOOK.exists(), "hooks/block-tier0-local-build.sh missing")
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        wired = [h.get("command", "")
                 for entry in cfg["hooks"]["PreToolUse"]
                 if entry.get("matcher") == "Bash"
                 for h in entry.get("hooks", [])]
        self.assertTrue(any("block-tier0-local-build.sh" in c for c in wired),
                         "hook is not registered under PreToolUse/Bash")


class DirectHeavyBuildUnchangedTest(_Runner):
    """Regression net: the pre-existing direct-command detection must be
    byte-for-byte unchanged in outcome after #381's fix."""

    def test_cargo_build_release_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("BLOCKED", out.stderr)

    def test_cargo_test_running_is_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cargo_check_is_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo check --workspace", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_cargo_test_no_run_is_allowed(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --no-run --workspace", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_build_mentioned_inside_a_string_is_not_matched(self):
        proj = self._mkproj()
        out = self.run_hook('echo "reminder: run cargo build later"', proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_tier1_allowed_marker_exempts(self):
        proj = self._mkproj(marker="allowed")
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_tier2_fast_iterate_marker_exempts(self):
        proj = self._mkproj(marker="fast-iterate")
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_unmanaged_dir_not_enforced(self):
        # No CLAUDE.md anywhere above `root` (a bare tempdir) -> not enforced.
        bare = self.root / "unmanaged"
        bare.mkdir()
        out = self.run_hook("cargo build --release", bare)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_inline_marker_still_bypasses(self):
        proj = self._mkproj()
        out = self.run_hook("cargo build --release # airuleset:build-ok testing", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_env_override_still_bypasses(self):
        proj = self._mkproj()
        out = self.run_hook("cargo build --release", proj,
                             extra_env={"AIRULESET_ALLOW_LOCAL_BUILD": "1"})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class WrapperScriptBypassClosedTest(_Runner):
    """Shape B: a heavy build hidden inside an invoked .sh script."""

    def _mkscript(self, proj, name, body):
        s = proj / name
        s.write_text(body)
        s.chmod(0o755)
        return s

    def test_bash_invoked_script_with_unconditional_build_is_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "install.sh", "#!/bin/bash\ncargo build --release\n")
        out = self.run_hook("bash install.sh", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("install.sh", out.stderr)

    def test_direct_exec_of_script_with_build_is_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "install.sh", "#!/bin/bash\ncargo build --release\n")
        out = self.run_hook("./install.sh", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_sudo_bash_invoked_script_is_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "install.sh", "cargo build --release\n")
        out = self.run_hook("sudo bash install.sh", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_sourced_script_with_test_run_is_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "setup.sh", "cargo test --lib foo\n")
        out = self.run_hook("source setup.sh", proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_script_with_only_cheap_checks_is_not_blocked(self):
        proj = self._mkproj()
        self._mkscript(proj, "check.sh", "#!/bin/bash\ncargo check\ncargo clippy\n")
        out = self.run_hook("bash check.sh", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_referenced_but_missing_script_is_not_blocked(self):
        proj = self._mkproj()
        out = self.run_hook("bash does-not-exist.sh", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_marker_on_outer_command_still_bypasses_a_heavy_script(self):
        proj = self._mkproj()
        self._mkscript(proj, "install.sh", "cargo build --release\n")
        out = self.run_hook("bash install.sh # airuleset:build-ok one-off", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class MarkerIsQuoteAwareTest(_Runner):
    """Shape C: the marker text merely mentioned inside a quoted string must
    NOT bypass a real, unrelated heavy command on the same line."""

    def test_marker_mentioned_in_a_commit_message_does_not_bypass_a_real_build(self):
        proj = self._mkproj()
        cmd = ('git commit -m "docs: mention airuleset:build-ok in readme" '
               '&& cargo build --release')
        out = self.run_hook(cmd, proj)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_unquoted_trailing_marker_comment_still_bypasses(self):
        proj = self._mkproj()
        out = self.run_hook("cargo test --lib foo # airuleset:build-ok reason", proj)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class AuditLogTest(_Runner):
    """Shape A: every bypass/block decision on a genuinely HEAVY command is
    now a durable, greppable line — never silent."""

    def test_blocked_heavy_build_is_logged(self):
        proj = self._mkproj(name="camera-box")
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 2)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("blocked", lines[0])
        self.assertIn("project=camera-box", lines[0])
        self.assertIn("cargo build --release", lines[0])

    def test_inline_marker_bypass_is_logged(self):
        proj = self._mkproj(name="spinbike")
        out = self.run_hook("cargo build --release # airuleset:build-ok reason", proj)
        self.assertEqual(out.returncode, 0)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("inline-bypass", lines[0])
        self.assertIn("project=spinbike", lines[0])

    def test_env_override_bypass_is_logged(self):
        proj = self._mkproj(name="dantesync")
        out = self.run_hook("cargo build --release", proj,
                             extra_env={"AIRULESET_ALLOW_LOCAL_BUILD": "1"})
        self.assertEqual(out.returncode, 0)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("env-bypass", lines[0])

    def test_wrapper_script_block_is_logged_with_script_path(self):
        proj = self._mkproj(name="dantesync")
        s = proj / "install.sh"
        s.write_text("cargo build --release\n")
        out = self.run_hook("bash install.sh", proj)
        self.assertEqual(out.returncode, 2)
        lines = self.audit_lines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("blocked", lines[0])
        self.assertIn("install.sh", lines[0])

    def test_non_heavy_command_with_marker_is_not_logged(self):
        """No noise: a marker on a command that was never heavy in the
        first place is not the accountability gap this ticket closes."""
        proj = self._mkproj()
        out = self.run_hook("echo hi # airuleset:build-ok", proj)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(self.audit_lines(), [])

    def test_tier1_exempt_heavy_build_is_not_logged(self):
        """A Tier-1/2 project's own declared, visible marker is not the
        silent one-off gap either — don't add log noise for it."""
        proj = self._mkproj(marker="allowed")
        out = self.run_hook("cargo build --release", proj)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(self.audit_lines(), [])


if __name__ == "__main__":
    unittest.main()
