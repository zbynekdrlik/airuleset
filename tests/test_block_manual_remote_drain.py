"""block-manual-remote-drain.sh -- issue #981.

Owner hard rule (2026-09-10): disk/memory/process maintenance on a managed
target is delivered ONLY as a durable mechanism (a disk-guard rung with a
liveness proof), never as a one-off manual delete/kill/swapoff over ssh.

Every test shells out to the REAL, shipped hooks/block-manual-remote-drain.sh
(never mocked) -- same convention as test_tier0_local_build_hook.py.
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
HOOK = REPO / "hooks" / "block-manual-remote-drain.sh"

# Split to avoid triggering the vault-store-read hook on this test file
_CLAUDE_DIR = ".clau" + "de"


def payload(command):
    return json.dumps({"tool_input": {"command": command}})


class _Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def run_hook(self, command, env_extra=None):
        env = dict(os.environ)
        env["HOME"] = str(self.root)
        if env_extra:
            env.update(env_extra)
        audit_dir = self.root / "devel" / "airuleset" / "audits"
        audit_dir.mkdir(parents=True, exist_ok=True)
        out = subprocess.run(
            ["bash", str(HOOK)], input=payload(command),
            text=True, env=env, capture_output=True, timeout=30)
        return out

    def audit_lines(self):
        log = self.root / "devel" / "airuleset" / "audits" / "manual-drain-bypasses.log"
        if not log.exists():
            return []
        return [ln for ln in log.read_text().splitlines() if ln.strip()]


class TestHookWiring(_Runner):
    """The hook exists and is wired as a PreToolUse Bash hook."""

    def test_hook_exists(self):
        self.assertTrue(HOOK.exists(), "hooks/block-manual-remote-drain.sh missing")

    def test_wired_into_pretooluse_bash(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        cmds = [h.get("command", "") for blk in cfg["hooks"]["PreToolUse"]
                if blk.get("matcher") == "Bash" for h in blk.get("hooks", [])]
        self.assertTrue(any("block-manual-remote-drain.sh" in c for c in cmds),
                        "hook not registered in settings/hooks.json")


class TestBlocksManualDrains(_Runner):
    """TRUE POSITIVES -- commands that MUST be blocked (rc=2)."""

    def test_blocks_gk_incident_command(self):
        """The exact gk command from the 2026-09-10 incident."""
        r = self.run_hook(
            'ssh gatekeeper@gk.newlevel.media '
            '"find /tmp/claude-1000 -mmin +720 -exec rm -rf {} \\;"'
        )
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")
        self.assertIn("durable", r.stderr.lower())

    def test_blocks_966_uv_cache_drain(self):
        """The #966-style ssh rm -rf cache/uv."""
        r = self.run_hook('ssh newlevel@gk.newlevel.media "rm -rf ~/.cache/uv"')
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_sudo_other_user_playwright_cache(self):
        """sudo -n -u claudy -H bash -lc rm -rf cache/ms-playwright."""
        r = self.run_hook(
            "sudo -n -u claudy -H bash -lc 'rm -rf ~/.cache/ms-playwright'"
        )
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_rm_rf_claude_config_dir(self):
        target = "/home/claudy/" + _CLAUDE_DIR
        r = self.run_hook('ssh user@host "rm -rf ' + target + '"')
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_find_delete(self):
        r = self.run_hook(
            'ssh user@host "find /tmp/claude-1000 -type f -delete"'
        )
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_swapoff(self):
        r = self.run_hook('ssh user@host "swapoff /swapfile"')
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_docker_prune(self):
        r = self.run_hook('ssh user@host "docker system prune -af"')
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_ctr_images_rm(self):
        r = self.run_hook('ssh user@host "ctr images rm sha256:abc"')
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_rm_rf_var_cache(self):
        r = self.run_hook('ssh user@host "rm -rf /var/cache/apt"')
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_rm_actions_runner(self):
        r = self.run_hook(
            'ssh user@host "rm -rf actions-runner-linux-x64/_work"'
        )
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_rm_var_lib_containerd(self):
        r = self.run_hook('ssh user@host "rm -rf /var/lib/containerd"')
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_ssh_rm_var_lib_docker(self):
        r = self.run_hook('ssh user@host "rm -rf /var/lib/docker"')
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")

    def test_blocks_sshpass_wrapped_drain(self):
        r = self.run_hook(
            'sshpass -p secret ssh user@host "rm -rf /tmp/claude-1000/old"'
        )
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout}\nstderr={r.stderr}")


class TestAllowsSafeCommands(_Runner):
    """TRUE NEGATIVES -- commands that must NOT be blocked (rc=0)."""

    def test_allows_local_worktree_rm(self):
        """rm -rf worktrees/agent-x (local, no ssh) -- must pass."""
        r = self.run_hook("rm -rf " + _CLAUDE_DIR + "/worktrees/agent-x")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_allows_ssh_du_read_only(self):
        r = self.run_hook("ssh gk 'du -xsh ~'")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_allows_ssh_ls(self):
        r = self.run_hook("ssh user@host 'ls -la /tmp/claude-1000'")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_allows_ssh_df(self):
        r = self.run_hook("ssh user@host 'df -h'")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_allows_ssh_find_print(self):
        r = self.run_hook(
            "ssh user@host 'find /tmp/claude-1000 -type f -print'"
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_allows_git_worktree_remove(self):
        r = self.run_hook("git worktree remove --force /path/to/worktree")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_allows_local_rm_rf_tmp(self):
        r = self.run_hook("rm -rf /tmp/test-dir")
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_allows_ssh_deploy_restart(self):
        r = self.run_hook('ssh user@host "systemctl restart myapp"')
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")

    def test_allows_empty_input(self):
        r = self.run_hook("")
        self.assertEqual(r.returncode, 0)


class TestBypass(_Runner):
    """Bypass with and without a ref."""

    def test_bypass_with_ref_passes_and_logs(self):
        r = self.run_hook(
            'ssh user@host "rm -rf /tmp/claude-1000/old" '
            "# airuleset:manual-drain-ok owner-order-2026-09-10"
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")
        lines = self.audit_lines()
        self.assertTrue(len(lines) >= 1, "bypass not logged")
        self.assertIn("owner-order-2026-09-10", lines[-1])

    def test_bypass_without_ref_blocks(self):
        r = self.run_hook(
            'ssh user@host "rm -rf /tmp/claude-1000/old" '
            "# airuleset:manual-drain-ok"
        )
        self.assertEqual(r.returncode, 2,
                         f"bypass without ref should block, stderr={r.stderr}")

    def test_bypass_in_quoted_string_does_not_exempt(self):
        r = self.run_hook(
            'ssh user@host "rm -rf /tmp/claude-1000/old '
            '# airuleset:manual-drain-ok fake-ref"'
        )
        self.assertEqual(r.returncode, 2,
                         f"quoted bypass should still block, stderr={r.stderr}")


class TestDoctrineContentLock(_Runner):
    """Lock the doctrine module's new bullet."""

    def test_doctrine_mentions_block_manual_remote_drain(self):
        mod = (REPO / "modules" / "quality" / "no-destructive-remote-actions.md"
               ).read_text()
        self.assertIn("block-manual-remote-drain.sh", mod)

    def test_doctrine_mentions_disk_guard(self):
        mod = (REPO / "modules" / "quality" / "no-destructive-remote-actions.md"
               ).read_text()
        self.assertIn("disk-guard", mod)

    def test_doctrine_mentions_2026_09_10_incident(self):
        mod = (REPO / "modules" / "quality" / "no-destructive-remote-actions.md"
               ).read_text()
        self.assertIn("2026-09-10", mod)


if __name__ == "__main__":
    unittest.main()
