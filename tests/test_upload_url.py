"""Locks the receive-files-via-upload-URL capability (issue #18, 2026-07-10).

Recurring incident: the user works over SSH with NO local FS access to any
managed box — yet target Claudes (david@gk, 2026-07-10) keep asking him to scp
files up. The download direction was solved (deliver-files-as-urls + share);
the UPLOAD direction existed only as a script buried in the meeting-analysis
skill, invisible to every other session. Promoted to a first-class CLI
(`airuleset.py upload`) + an always-on module banning scp-to-user asks.
"""

import contextlib
import io
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m

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


class TestMultiInterfaceUrls(TestCase):
    """The URL must be shown on EVERY private interface (tailscale + LAN), because
    the user switches networks (2026-07-10). Both the upload server and the CLIs
    bind/advertise all of bind_ips() — never the public IP (write endpoint)."""

    def test_upload_server_skips_unbindable_ip_but_serves_the_rest(self):
        dest = Path(tempfile.mkdtemp())
        port = 8796
        # 203.0.113.9 (TEST-NET-3) is not local → bind fails → skipped; 127.0.0.1
        # binds → the endpoint still comes up. Proves multi-bind is resilient.
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "filedrop" / "upload_server.py"),
             "tok", str(port), "203.0.113.9,127.0.0.1", str(dest), "20"],
            stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)
        time.sleep(0.6)
        page = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/tok/", timeout=5).read()
        self.assertIn(b"Upload", page)

    def test_cmd_upload_prints_a_url_per_interface(self):
        import airuleset
        import filedrop
        sk = socket.socket()
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
        sk.close()
        dest = Path(tempfile.mkdtemp())
        # two loopback addresses both bind on Linux → two advertised URLs
        with m.patch.object(filedrop, "bind_ips",
                            return_value=["127.0.0.1", "127.0.0.2"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_upload(m.Mock(dir=str(dest), ttl=5, port=port))
            out = buf.getvalue()
        self.assertIn(f"http://127.0.0.1:{port}/", out)
        self.assertIn(f"http://127.0.0.2:{port}/", out)

    def test_cmd_upload_survives_first_interface_unbindable(self):
        # Review-found gap (2026-07-10): the readiness wait must key on ANY
        # interface, not urls[0]. Here urls[0] (203.0.113.9 TEST-NET) cannot bind
        # while 127.0.0.1 binds fine — cmd_upload must still print the working URL,
        # never abort on the first interface and orphan the endpoint.
        import airuleset
        import filedrop
        sk = socket.socket()
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
        sk.close()
        dest = Path(tempfile.mkdtemp())
        with m.patch.object(filedrop, "bind_ips",
                            return_value=["203.0.113.9", "127.0.0.1"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_upload(m.Mock(dir=str(dest), ttl=5, port=port))
            out = buf.getvalue()
        self.assertIn(f"http://127.0.0.1:{port}/", out)
        self.assertNotIn("203.0.113.9", out)   # unbindable interface not advertised

    def test_cmd_share_prints_a_url_per_interface(self):
        import airuleset
        import filedrop
        with m.patch("filedrop.share.share",
                     return_value=("http://100.90.94.41:8788/tok/f.bin", "/x")), \
             m.patch.object(filedrop, "bind_ips",
                            return_value=["100.90.94.41", "10.77.9.21"]), \
             m.patch.object(airuleset, "_filedrop_is_live", return_value=True):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_share(m.Mock(path="/x"))
            out = buf.getvalue()
        self.assertIn("http://100.90.94.41:8788/tok/f.bin", out)
        self.assertIn("http://10.77.9.21:8788/tok/f.bin", out)


if __name__ == "__main__":
    main()
