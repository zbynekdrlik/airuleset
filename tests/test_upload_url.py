"""Locks the receive-files-via-upload-URL capability (issue #18, 2026-07-10).

Recurring incident: the user works over SSH with NO local FS access to any
managed box — yet target Claudes (david@gk, 2026-07-10) keep asking him to scp
files up. The download direction was solved (deliver-files-as-urls + share);
the UPLOAD direction existed only as a script buried in the meeting-analysis
skill, invisible to every other session. Promoted to a first-class CLI
(`airuleset.py upload`) + an always-on module banning scp-to-user asks.
"""

import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestUploadCli(TestCase):
    def test_upload_subcommand_registered(self):
        self.assertIn("upload", airuleset.SUBCOMMANDS)

    def test_upload_server_lives_in_filedrop_package(self):
        self.assertTrue((ROOT / "filedrop" / "upload_server.py").exists())

    def test_server_saves_a_put_and_respects_ttl(self):
        dest = Path(tempfile.mkdtemp())
        port = 8797
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "filedrop" / "upload_server.py"),
             "tok123", str(port), "127.0.0.1", str(dest), "30"],
            stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)
        time.sleep(0.6)
        # GET page
        page = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/tok123/", timeout=5).read()
        self.assertIn(b"Upload", page)
        # PUT a file
        body = b"hello-upload" * 100
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/tok123/test.bin", data=body, method="PUT")
        r = urllib.request.urlopen(req, timeout=5)
        self.assertEqual(r.status, 200)
        saved = dest / "test.bin"
        self.assertTrue(saved.exists())
        self.assertEqual(saved.stat().st_size, len(body))
        # wrong token -> 404
        req2 = urllib.request.Request(
            f"http://127.0.0.1:{port}/WRONG/x.bin", data=b"x", method="PUT")
        try:
            urllib.request.urlopen(req2, timeout=5)
            self.fail("wrong token accepted")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


class TestReceiveFilesModule(TestCase):
    MOD = "modules/core/receive-files-via-upload-url.md"

    def test_module_exists_and_always_on(self):
        self.assertTrue((ROOT / self.MOD).exists())
        self.assertIn(self.MOD.replace("modules/", "modules/", 1),
                      read("profiles/universal.profile"))

    def test_bans_scp_to_user_and_names_the_cli(self):
        t = read(self.MOD)
        self.assertIn("airuleset.py upload", t)
        self.assertIn("scp", t)
        self.assertIn("BANNED", t)
        self.assertIn("all rewordings", t)

    def test_cross_referenced_with_download_direction(self):
        self.assertIn("receive-files-via-upload-url",
                      read("modules/core/deliver-files-as-urls.md"))

    def test_meeting_analysis_uses_the_cli(self):
        t = read("skills/meeting-analysis/SKILL.md")
        self.assertIn("airuleset.py upload", t)


if __name__ == "__main__":
    main()
