"""Behaviour test for hooks/block-heavy-build-toolchain.sh (#778, Layer 1).

The hook BLOCKS a heavy build-toolchain / VM launch (gradle/gradlew/kotlinc/
aapt2/sdkmanager/emulator/qemu-system, and a java build-daemon launch) — but
ONLY on a box whose class marker (~/.claude/airuleset-box-class) reads
`shared-stream`. On any other box (workstation / no marker) it is a total
NO-OP. FAIL-OPEN on any classifier malfunction.

The box-class marker is controlled per-test by pointing HOME at a tempdir.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-heavy-build-toolchain.sh"


def run(cmd, box_class="shared-stream"):
    """Run the hook with a controlled HOME carrying (or not) the box-class
    marker. box_class=None → no marker file at all."""
    payload = json.dumps({"tool_input": {"command": cmd}})
    with tempfile.TemporaryDirectory() as home:
        claude = os.path.join(home, ".claude")
        os.makedirs(claude)
        if box_class is not None:
            with open(os.path.join(claude, "airuleset-box-class"), "w") as fh:
                fh.write(box_class + "\n")
        env = {**os.environ, "HOME": home}
        return subprocess.run(
            ["bash", str(HOOK)], input=payload, capture_output=True,
            text=True, env=env)


class TestBlockHeavyBuildToolchain(TestCase):
    def assertBlocked(self, cmd, box_class="shared-stream"):
        r = run(cmd, box_class)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK for: {cmd}\nstderr={r.stderr}")
        self.assertIn("dev2", r.stderr)

    def assertAllowed(self, cmd, box_class="shared-stream"):
        r = run(cmd, box_class)
        self.assertEqual(r.returncode, 0,
                         f"expected ALLOW for: {cmd}\nstderr={r.stderr}")

    # ---- BLOCKED on a shared-stream box --------------------------------
    def test_blocks_gradlew(self):
        self.assertBlocked("./gradlew assembleRelease")

    def test_blocks_bare_gradle(self):
        self.assertBlocked("gradle build")

    def test_blocks_kotlinc(self):
        self.assertBlocked("kotlinc src -include-runtime -d app.jar")

    def test_blocks_aapt2(self):
        self.assertBlocked("aapt2 compile --dir res -o out.zip")

    def test_blocks_sdkmanager(self):
        self.assertBlocked("sdkmanager 'platforms;android-34'")

    def test_blocks_avdmanager(self):
        self.assertBlocked("avdmanager create avd -n test -k 'system-images;android-34'")

    def test_blocks_emulator(self):
        self.assertBlocked("emulator -avd Pixel_6 -no-window")

    def test_blocks_qemu_system(self):
        self.assertBlocked("qemu-system-x86_64 -m 4096 -hda disk.img")

    def test_blocks_java_gradle_daemon_launch(self):
        self.assertBlocked(
            "java -Xmx3072m -cp gradle-launcher.jar "
            "org.gradle.launcher.daemon.bootstrap.GradleDaemon 9.3.1")

    def test_blocks_gradlew_inside_a_cd_chain(self):
        self.assertBlocked("cd wt-5564/frontline/apps/pekarova-zena-erp && ./gradlew build")

    def test_blocks_gradle_after_env_prefix(self):
        self.assertBlocked("JAVA_HOME=~/tools/jdk17 ./gradlew assembleDebug")

    # ---- the OTHER branch: NO-OP off a shared-stream box ---------------
    def test_workstation_box_never_blocks(self):
        self.assertAllowed("./gradlew assembleRelease", box_class="workstation")

    def test_no_marker_never_blocks(self):
        self.assertAllowed("./gradlew assembleRelease", box_class=None)

    # ---- legitimate work always passes (fail toward allow) ------------
    def test_allows_git_and_python_on_shared_box(self):
        self.assertAllowed("git status && python3 -m pytest tests/ -x -q")

    def test_allows_plain_java_version(self):
        self.assertAllowed("java -version")

    def test_allows_node_on_shared_box(self):
        # node is DELIBERATELY not banned — it runs Claude Code / MCP / webterm
        self.assertAllowed("node server.js")

    def test_allows_commit_message_merely_quoting_gradle(self):
        self.assertAllowed("git commit -m 'ban gradle daemon on subdev'")

    # ---- bypass marker -------------------------------------------------
    def test_bypass_comment_allows(self):
        self.assertAllowed("./gradlew build  # airuleset:heavy-build-ok one-off owner-approved")


if __name__ == "__main__":
    main()
