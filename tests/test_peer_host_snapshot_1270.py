"""#1270 (owner 30.10.2026: "nepaci sa mi ze prepojovanie ma trvat tolko hodin"):
a link reaches a session without the full push. The root leg writes a host
snapshot of the peer config and the channel prefers it over the checkout."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_peer_channel as cpc  # noqa: E402
import peer_channel  # noqa: E402

LINKS = {"hosts": {"h1": {"links": [["a", "b"]], "topics": {"a|b": "t"}}}}
REGISTRY = [{"name": "a", "host": "h1", "account": "u1", "path": "~/a"}]


class TestConfigDir(unittest.TestCase):

    def test_the_snapshot_wins_when_both_files_are_there(self):
        with tempfile.TemporaryDirectory() as d:
            for name in (peer_channel.LINKS_FILE, peer_channel.REGISTRY_FILE):
                Path(d, name).write_text("{}")
            self.assertEqual(peer_channel.active_config_dir(d), d)

    def test_a_partial_snapshot_falls_back_to_the_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, peer_channel.LINKS_FILE).write_text("{}")
            self.assertEqual(peer_channel.active_config_dir(d), peer_channel.CONFIG_DIR)


class TestRootSnapshot(unittest.TestCase):

    def _run(self, script, root):
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir)
        for tool in ("systemctl", "systemd-tmpfiles"):
            Path(bindir, tool).write_text("#!/bin/sh\nexit 0\n")
            os.chmod(os.path.join(bindir, tool), 0o755)
        env = {**os.environ, "PATH": bindir + ":" + os.environ["PATH"]}
        return subprocess.run(["bash", "-c", script.replace("/etc/", root + "/etc/")],
                              capture_output=True, text=True, env=env)

    def test_the_root_script_writes_both_files_verbatim(self):
        with tempfile.TemporaryDirectory() as root:
            script = cpc.render_root_script(["root"], links_doc=LINKS, registry=REGISTRY)
            self._run(script, root)     # the later read-back needs a real mount
            snap = root + cpc.SNAPSHOT_DIR
            self.assertEqual(json.loads(Path(snap, peer_channel.LINKS_FILE).read_text()), LINKS)
            self.assertEqual(json.loads(Path(snap, peer_channel.REGISTRY_FILE).read_text()),
                             REGISTRY)
            self.assertEqual(os.stat(Path(snap, peer_channel.LINKS_FILE)).st_mode & 0o777, 0o644)

    def test_teardown_removes_the_snapshot(self):
        script = cpc.render_teardown_script()
        for name in (peer_channel.LINKS_FILE, peer_channel.REGISTRY_FILE):
            self.assertIn(os.path.join(cpc.SNAPSHOT_DIR, name), script)


class TestRootApplyRefusal(unittest.TestCase):

    def test_an_uncommitted_link_is_refused(self):
        calls = []
        rc = cpc.main(["root-apply", "--host", "h1"], dirty=lambda: ["peer-links.json"],
                      apply=lambda **k: calls.append(k) or [])
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [])

    def test_a_committed_link_applies_only_the_named_host(self):
        calls = []
        rc = cpc.main(["root-apply", "--host", "h1"], dirty=lambda: [],
                      apply=lambda **k: calls.append(k) or [])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [{"only": "h1"}])


if __name__ == "__main__":
    unittest.main()
