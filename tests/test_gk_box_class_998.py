"""#998 item 5 — gk box class is Claude-only.

block-heavy-build-toolchain.sh now applies to the `gk` box class too (was
shared-stream/controller), AND bans a local `docker`/`podman` pull|run of an
odoo image on ANY Claude-only class. dev2/workstation stay total no-ops.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-heavy-build-toolchain.sh"


def run(cmd, box_class):
    payload = json.dumps({"tool_input": {"command": cmd}})
    with tempfile.TemporaryDirectory() as home:
        claude = os.path.join(home, ".claude")
        os.makedirs(claude)
        if box_class is not None:
            with open(os.path.join(claude, "airuleset-box-class"), "w") as fh:
                fh.write(box_class + "\n")
        return subprocess.run(
            ["bash", str(HOOK)], input=payload, capture_output=True,
            text=True, env={**os.environ, "HOME": home})


class TestGkBoxClass(TestCase):
    def test_gk_blocks_gradle_heavy_build(self):
        r = run("gradle assembleRelease", "gk")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("dev2", r.stderr)

    def test_gk_blocks_docker_run_odoo(self):
        for cmd in ("docker run -it odoo:16", "docker pull odoo",
                    "podman run odoo/odoo:17", "docker create odoo"):
            r = run(cmd, "gk")
            self.assertEqual(r.returncode, 2, "%s\n%s" % (cmd, r.stderr))
            self.assertIn("odoo", r.stderr.lower())
            self.assertIn("erp-test", r.stderr)

    def test_gk_allows_non_odoo_docker(self):
        for cmd in ("docker run ubuntu", "docker pull postgres:16",
                    "docker ps"):
            r = run(cmd, "gk")
            self.assertEqual(r.returncode, 0, "%s\n%s" % (cmd, r.stderr))

    def test_shared_stream_also_blocks_docker_odoo(self):
        r = run("docker run odoo", "shared-stream")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("odoo", r.stderr.lower())

    def test_workstation_never_blocks(self):
        for cmd in ("docker run odoo", "gradle assembleRelease"):
            self.assertEqual(run(cmd, "workstation").returncode, 0)
            self.assertEqual(run(cmd, None).returncode, 0)

    def test_bypass_marker_allows_docker_odoo(self):
        r = run("docker run odoo:16  # airuleset:heavy-build-ok owner test", "gk")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestGkReaperGate(TestCase):
    """#998 — heavy_build_reaper (Job 38) applies to the gk box class too."""

    def test_gk_box_reaps_a_gradle_daemon(self):
        from watchdog.reaper import heavy_build_reaper, GRADLE_DAEMON_CLASS
        cmd = ("/usr/bin/java -Xmx3072m -cp gradle-launcher.jar "
               + GRADLE_DAEMON_CLASS + " 9.3.1")
        killed = []
        logs = heavy_build_reaper(
            ps_fetch=lambda: [("4242", "5", "0", cmd)],
            kill_fn=lambda pid: killed.append(int(pid)),
            verify_fn=lambda pid: cmd, box_class_fn=lambda: "gk",
            dry_run=False)
        self.assertEqual(killed, [4242], logs)

    def test_workstation_reaper_still_skips(self):
        from watchdog.reaper import heavy_build_reaper, GRADLE_DAEMON_CLASS
        cmd = "/usr/bin/java -cp x.jar " + GRADLE_DAEMON_CLASS + " 9"
        killed = []
        heavy_build_reaper(
            ps_fetch=lambda: [("1", "5", "0", cmd)],
            kill_fn=lambda pid: killed.append(pid),
            verify_fn=lambda pid: cmd, box_class_fn=lambda: "workstation",
            dry_run=False)
        self.assertEqual(killed, [])


if __name__ == "__main__":
    main()
