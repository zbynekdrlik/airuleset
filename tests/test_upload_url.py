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

    def test_served_page_has_no_escaped_brace_leak(self):
        # PAGE is served RAW (PAGE.encode(), no .format()), so doubled `{{`/`}}`
        # would render LITERALLY and break the CSS/JS. Regression for david@gk's
        # live-found fix (2026-07-10): the served HTML must contain real single
        # braces (`body{font`), never an escaped `{{`.
        dest = Path(tempfile.mkdtemp())
        port = 8794
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "filedrop" / "upload_server.py"),
             "toktoktoktoktok16", str(port), "127.0.0.1", str(dest), "20"],
            stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        time.sleep(0.6)
        html = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/toktoktoktoktok16/", timeout=5).read().decode()
        self.assertNotIn("{{", html)            # no escaped-brace leak
        self.assertIn("body{font", html)        # real CSS rule survived

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


class TestMultiFileUpload(TestCase):
    """#27 (Marek, Montalu, reported live 2026-07-23 and again 2026-07-27):
    the drag-drop page only ever sent `files[0]` — dropping several photos at
    once silently uploaded just the first. Agreed scope (issue comment): a
    drag-drop must accept SEVERAL files, each saved+confirmed INDIVIDUALLY
    (its own SAVED log line + size), and one file failing must not bring
    down the others."""

    def _server(self, port, dest):
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "filedrop" / "upload_server.py"),
             "toktoktoktoktok27", str(port), "127.0.0.1", str(dest), "30"],
            stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.kill)
        time.sleep(0.6)
        return proc

    # -- the served PAGE must actually ENABLE + PERFORM a multi-file send -- #

    def test_file_input_accepts_multiple_files(self):
        dest = Path(tempfile.mkdtemp())
        port = 8798
        self._server(port, dest)
        html = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/toktoktoktoktok27/", timeout=5).read().decode()
        self.assertIn("type=file multiple", html)

    def test_page_js_sends_every_dropped_file_not_just_the_first(self):
        dest = Path(tempfile.mkdtemp())
        port = 8798
        self._server(port, dest)
        html = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/toktoktoktoktok27/", timeout=5).read().decode()
        # the OLD bug: only ever `send(ev.dataTransfer.files[0])` /
        # `send(f.files[0])` — a literal `[0]` index into the file list at
        # the point the upload is kicked off. The fix drives the WHOLE list
        # (`Array.from(fileList)`) through a sequential per-file sender.
        self.assertNotIn("files[0])", html)
        self.assertIn("Array.from(fileList)", html)
        self.assertIn("sendAll(ev.dataTransfer.files)", html)
        self.assertIn("sendAll(f.files)", html)

    # -- server side: each file in a multi-file batch is saved + logged     #
    # -- INDIVIDUALLY, exactly like the page's own sequential PUT sequence  #

    def test_each_file_in_a_batch_is_saved_and_logged_individually(self):
        dest = Path(tempfile.mkdtemp())
        port = 8798
        proc = self._server(port, dest)
        bodies = {"photo1.jpg": b"a" * 5000, "photo2.jpg": b"b" * 7000,
                 "photo3.jpg": b"c" * 3000}
        for name, body in bodies.items():
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/toktoktoktoktok27/{name}",
                data=body, method="PUT")
            r = urllib.request.urlopen(req, timeout=5)
            self.assertEqual(r.status, 200)
        for name, body in bodies.items():
            saved = dest / name
            self.assertTrue(saved.exists(), name)
            self.assertEqual(saved.stat().st_size, len(body), name)
        time.sleep(0.3)
        proc.terminate()
        err = proc.stderr.read()
        for name, body in bodies.items():
            self.assertIn("upload SAVED %s (%d bytes)"
                          % (dest / name, len(body)), err)

    def test_one_failing_file_does_not_block_the_others(self):
        dest = Path(tempfile.mkdtemp())
        port = 8798
        self._server(port, dest)
        # a bad PUT (no Content-Length -> 411) must not affect the endpoint's
        # ability to accept the NEXT file right after it.
        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/toktoktoktoktok27/bad.bin", method="PUT")
        bad.add_header("Content-Length", "0")
        try:
            urllib.request.urlopen(bad, timeout=5)
            self.fail("zero-length PUT unexpectedly accepted")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 411)
        self.assertFalse((dest / "bad.bin").exists())
        good = urllib.request.Request(
            f"http://127.0.0.1:{port}/toktoktoktoktok27/good.bin",
            data=b"ok" * 100, method="PUT")
        r = urllib.request.urlopen(good, timeout=5)
        self.assertEqual(r.status, 200)
        self.assertTrue((dest / "good.bin").exists())
        self.assertEqual((dest / "good.bin").stat().st_size, 200)


if __name__ == "__main__":
    main()
