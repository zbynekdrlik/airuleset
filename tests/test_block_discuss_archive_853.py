"""Tests for hooks/block-discuss-archive.sh — issue 853.

Block `discuss.channel` `action_archive` / `toggle_active` / `active=False` write
shapes (XML-RPC/JSON-RPC via python/curl in the command text) on SHARED-STREAM
boxes only. Pass on workstation/gk boxes, pass for reads (search_read), and the
bypass `# airuleset:discuss-archive-ok <reason>` is logged.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

HOOK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "hooks",
    "block-discuss-archive.sh",
)

SETTINGS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "settings",
    "hooks.json",
)


def _run_hook(command: str, box_class: str = "shared-stream") -> subprocess.CompletedProcess:
    """Run the hook with the given command payload and box-class marker."""
    payload = json.dumps({"tool_input": {"command": command}})
    with tempfile.TemporaryDirectory() as td:
        # Write a box-class marker file
        marker_dir = os.path.join(td, ".claude")
        os.makedirs(marker_dir, exist_ok=True)
        marker_file = os.path.join(marker_dir, "airuleset-box-class")
        with open(marker_file, "w") as f:
            f.write(box_class + "\n")
        env = {**os.environ, "HOME": td}
        return subprocess.run(
            ["bash", HOOK],
            input=payload.encode(),
            capture_output=True,
            timeout=10,
            env=env,
        )


class TestBlockOnSharedStream(unittest.TestCase):
    """The hook BLOCKS archive shapes on a shared-stream box."""

    def test_action_archive_python_blocked(self):
        cmd = "python3 -c \"import xmlrpc.client; s=xmlrpc.client.ServerProxy('...'); s.execute_kw('db','2','pw','discuss.channel','action_archive',[[262]])\""
        r = _run_hook(cmd)
        self.assertEqual(r.returncode, 2, f"expected block, got: {r.stderr.decode()}")
        self.assertIn("hide", r.stderr.decode().lower())

    def test_toggle_active_curl_blocked(self):
        cmd = "curl -X POST https://erp.example.com/json/2/discuss.channel/toggle_active -d '{\"args\": [[262]]}'"
        r = _run_hook(cmd)
        self.assertEqual(r.returncode, 2, f"expected block, got: {r.stderr.decode()}")

    def test_active_false_write_blocked(self):
        cmd = "python3 -c \"proxy.execute_kw('db','2','pw','discuss.channel','write',[[262],{'active': False}])\""
        r = _run_hook(cmd)
        self.assertEqual(r.returncode, 2, f"expected block, got: {r.stderr.decode()}")

    def test_active_false_curl_json_blocked(self):
        cmd = """curl -X POST https://erp.example.com/json/2/discuss.channel/write -d '{"args": [[262], {"active": false}]}'"""
        r = _run_hook(cmd)
        self.assertEqual(r.returncode, 2, f"expected block, got: {r.stderr.decode()}")


class TestPassOnWorkstation(unittest.TestCase):
    """The hook is a NO-OP on a workstation/gk box."""

    def test_action_archive_passes_on_workstation(self):
        cmd = "python3 -c \"proxy.execute_kw('db','2','pw','discuss.channel','action_archive',[[262]])\""
        r = _run_hook(cmd, box_class="workstation")
        self.assertEqual(r.returncode, 0)

    def test_action_archive_passes_with_no_marker(self):
        cmd = "python3 -c \"proxy.execute_kw('db','2','pw','discuss.channel','action_archive',[[262]])\""
        r = _run_hook(cmd, box_class="")
        self.assertEqual(r.returncode, 0)


class TestPassForReads(unittest.TestCase):
    """Read operations (search_read, read) must NOT be blocked."""

    def test_search_read_passes(self):
        cmd = "python3 -c \"proxy.execute_kw('db','2','pw','discuss.channel','search_read',[[]])\""
        r = _run_hook(cmd)
        self.assertEqual(r.returncode, 0)

    def test_read_passes(self):
        cmd = "python3 -c \"proxy.execute_kw('db','2','pw','discuss.channel','read',[[262]])\""
        r = _run_hook(cmd)
        self.assertEqual(r.returncode, 0)

    def test_message_post_passes(self):
        cmd = "python3 -c \"proxy.execute_kw('db','2','pw','discuss.channel','message_post',[[262]])\""
        r = _run_hook(cmd)
        self.assertEqual(r.returncode, 0)


class TestBypass(unittest.TestCase):
    """The bypass marker lets the archive through and is logged."""

    def test_bypass_passes(self):
        cmd = "python3 -c \"proxy.execute_kw('db','2','pw','discuss.channel','action_archive',[[262]])\"  # airuleset:discuss-archive-ok gk-cleanup"
        r = _run_hook(cmd)
        self.assertEqual(r.returncode, 0)


class TestHookWired(unittest.TestCase):
    """The hook is registered in settings/hooks.json."""

    def test_hook_in_settings(self):
        with open(SETTINGS) as f:
            data = json.load(f)
        bash_hooks = data["hooks"]["PreToolUse"]
        commands = []
        for entry in bash_hooks:
            if entry.get("matcher") == "Bash":
                for h in entry.get("hooks", []):
                    commands.append(h.get("command", ""))
        self.assertTrue(
            any("block-discuss-archive.sh" in c for c in commands),
            f"block-discuss-archive.sh not found in PreToolUse Bash hooks: {commands}",
        )


class TestDoctrineUpdates(unittest.TestCase):
    """handover-compose.md carries the issue 5946 recipe and the mis-shape naming."""

    def test_self_service_recipe_present(self):
        compose = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skills", "odoo-discuss-xmlrpc", "handover-compose.md",
        )
        with open(compose) as f:
            text = f.read()
        self.assertIn("schedule_close_hide_guarded", text)

    def test_archive_named_as_mis_shape(self):
        compose = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skills", "odoo-discuss-xmlrpc", "handover-compose.md",
        )
        with open(compose) as f:
            text = f.read()
        self.assertIn("mis-shape", text.lower().replace("mis shape", "mis-shape"))

    def test_hide_arming_mandatory(self):
        compose = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skills", "odoo-discuss-xmlrpc", "handover-compose.md",
        )
        with open(compose) as f:
            text = f.read()
        # The bullet must state hide arming is mandatory
        self.assertIn("POVINNÝ", text)

    def test_per_stream_sweep_line(self):
        compose = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skills", "odoo-discuss-xmlrpc", "handover-compose.md",
        )
        with open(compose) as f:
            text = f.read()
        # The per-stream own-thread sweep doctrine line
        self.assertTrue(
            "sweep" in text.lower() or "auditne" in text.lower() or "audit" in text.lower(),
            "per-stream sweep/audit doctrine line missing",
        )


if __name__ == "__main__":
    unittest.main()
