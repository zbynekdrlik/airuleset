"""Locks the receive-files-via-upload-URL capability (issue #18, 2026-07-10).

Recurring incident: the user works over SSH with NO local FS access to any
managed box — yet target Claudes (david@gk, 2026-07-10) keep asking him to scp
files up. The download direction was solved (deliver-files-as-urls + share);
the UPLOAD direction existed only as a script buried in the meeting-analysis
skill, invisible to every other session. Promoted to a first-class CLI
(`airuleset.py upload`) + an always-on module banning scp-to-user asks.
"""

import contextlib
import http.client
import io
import re
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
SERVER = ROOT / "filedrop" / "upload_server.py"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _free_port():
    """An ephemeral port the kernel says is free RIGHT NOW (#113 source 2).

    A hardcoded literal is shared with every other run of the suite and with
    any leftover server a killed run orphaned — and `upload_server.py` skips a
    failed bind rather than dying on it, so the test could end up talking to
    the OTHER process and passing for the wrong reason. Same idiom the sibling
    tests in TestMultiInterfaceUrls already use.
    """
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    return port


def _drain(proc):
    """The child's stderr — the diagnosis a readiness failure must carry."""
    try:
        return proc.communicate(timeout=5)[1] or ""
    except Exception:                                   # noqa: BLE001
        return "<stderr unavailable>"


def _wait_until_serving(test, proc, url, deadline_s):
    """Block until `url` actually answers — or fail with a diagnosis.

    #113: the readiness signal is the endpoint ANSWERING, never a stopwatch.
    Two exits besides success, both of which a fixed sleep gets wrong:
    the child already EXITED (a dead process never answers, so waiting out the
    budget is pointless — verify-launched-work-liveness.md), or the budget ran
    out (report the server's own stderr, e.g. `upload: skip bind …`, instead of
    a bare `Connection refused` the next reader has to re-diagnose).
    """
    end = time.monotonic() + deadline_s
    last = None
    while time.monotonic() < end:
        if proc.poll() is not None:
            test.fail("upload server exited (rc=%s) before serving %s\n"
                      "stderr:\n%s" % (proc.returncode, url, _drain(proc)))
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                r.read()
            return
        except urllib.error.HTTPError as e:
            e.close()                   # answered (any status) → it is up
            return
        except (OSError, http.client.HTTPException) as e:
            last = e
            time.sleep(0.05)
    proc.kill()
    test.fail("upload server never served %s within %.1fs (last: %r)\nstderr:\n%s"
              % (url, deadline_s, last, _drain(proc)))


def _serve(test, token, dest, ips="127.0.0.1", ttl=20, launcher=None,
           deadline_s=10.0):
    """Start upload_server.py on a free port and return (proc, port) once it
    is ACTUALLY serving.

    #113: every call site used to Popen the server and then `time.sleep(0.6)`
    — a guess at how long a fresh CPython takes to import http.server and
    bind. Under load the guess is wrong and the request hits a socket nobody
    listens on yet (`[Errno 111] Connection refused`, reproduced 1-in-50 under
    CPU+fork load). Polling costs less on an idle box (returns as soon as the
    port answers, typically well under the old 0.6s) and does not lose on a
    loaded one.
    """
    port = _free_port()
    argv = list(launcher or [sys.executable, str(SERVER)]) + [
        token, str(port), ips, str(dest), str(ttl)]
    proc = subprocess.Popen(argv, stderr=subprocess.PIPE, text=True)
    test.addCleanup(proc.kill)
    _wait_until_serving(test, proc, "http://127.0.0.1:%d/%s/" % (port, token),
                        deadline_s)
    return proc, port


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
        _, port = _serve(self, "toktoktoktoktok16", dest)
        html = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/toktoktoktoktok16/", timeout=5).read().decode()
        self.assertNotIn("{{", html)            # no escaped-brace leak
        self.assertIn("body{font", html)        # real CSS rule survived

    def test_server_saves_a_put_and_respects_ttl(self):
        dest = Path(tempfile.mkdtemp())
        _, port = _serve(self, "tok123", dest, ttl=30)
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
        # 203.0.113.9 (TEST-NET-3) is not local → bind fails → skipped; 127.0.0.1
        # binds → the endpoint still comes up. Proves multi-bind is resilient.
        _, port = _serve(self, "tok", dest, ips="203.0.113.9,127.0.0.1")
        page = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/tok/", timeout=5).read()
        self.assertIn(b"Upload", page)

    # ---- #113: the readiness wait itself, as a first-class contract ---- #

    def test_readiness_wait_survives_a_slow_server_start(self):
        # The live flake, made deterministic. The server is launched behind a
        # wrapper that delays its bind past the old fixed 0.6s budget — exactly
        # what a loaded box does to a fresh CPython startup (reproduced 1-in-50
        # under 8 CPU spinners + 2 fork storms on this 4-core box, failing with
        # `[Errno 111] Connection refused`). A readiness POLL waits for the port
        # to actually answer, so the delay is irrelevant; a fixed sleep cannot.
        dest = Path(tempfile.mkdtemp())
        slow = ("import os, sys, time; time.sleep(1.5); "
                "os.execv(sys.executable, [sys.executable, %r] + sys.argv[1:])"
                % str(SERVER))
        _, port = _serve(self, "tok113slow", dest,
                         launcher=[sys.executable, "-c", slow])
        page = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/tok113slow/", timeout=5).read()
        self.assertIn(b"Upload", page)

    def test_a_server_that_can_bind_nothing_fails_fast_with_its_stderr(self):
        # The other half of a readiness poll (verify-launched-work-liveness.md):
        # a DEAD child never answers, so a success-only wait would burn the whole
        # budget and then report a bare connection error. Binding only TEST-NET-3
        # makes upload_server.py exit with its own diagnosis — the wait must
        # notice the exit and surface that text.
        # ttl=0 because a TTL keeps the child ALIVE through its own failure:
        # upload_server.py arms a NON-daemon threading.Timer before the bind
        # loop, so `sys.exit("upload: no address …")` cannot end the process —
        # it lingers the full TTL and then exits 0 via the timer's os._exit
        # (measured: 20.06s, rc=0, vs 0.07s and rc=1 at ttl=0). Filed as #114;
        # this test is about the WAIT, so it uses the ttl that lets the child
        # genuinely die.
        dest = Path(tempfile.mkdtemp())
        t0 = time.monotonic()
        with self.assertRaises(AssertionError) as ctx:
            _serve(self, "tok113dead", dest, ips="203.0.113.9", ttl=0,
                   deadline_s=10.0)
        took = time.monotonic() - t0
        self.assertIn("no address", str(ctx.exception))
        self.assertLess(took, 8.0,
                        "must fail on the child's exit, not on the deadline")

    def test_readiness_timeout_reports_the_servers_own_stderr(self):
        # The ticket's other requirement: when the budget DOES run out, fail
        # with the server's captured stderr rather than a bare connection
        # error, so the next reader gets a diagnosis instead of a mystery.
        # This launcher never binds and never exits — the zombie shape a
        # lingering child (above) presents to the wait.
        dest = Path(tempfile.mkdtemp())
        mute = ('import sys, time; sys.stderr.write("DIAG-never-bound\\n"); '
                'sys.stderr.flush(); time.sleep(30)')
        with self.assertRaises(AssertionError) as ctx:
            _serve(self, "tok113mute", dest, deadline_s=1.0,
                   launcher=[sys.executable, "-c", mute])
        self.assertIn("never served", str(ctx.exception))
        self.assertIn("DIAG-never-bound", str(ctx.exception))

    def test_no_fixed_startup_sleep_survives_in_this_module(self):
        # Locks the fix itself: the startup guess sat at five separate call
        # sites here, and re-adding one at a sixth would silently reintroduce
        # #113 in a place no other test covers. Two locks, because either
        # alone is escapable:
        #   (a) no CODE line IS a bare fixed startup sleep. Matching the bare
        #       statement (not the substring) is what lets this file keep
        #       NAMING the old literal in its own prose, which the docstrings
        #       above and these comments necessarily do;
        #   (b) exactly one Popen exists in the whole module, so every server
        #       start goes through _serve() and inherits its poll — a sixth
        #       call site cannot grow a private start-and-guess of its own.
        src = Path(__file__).read_text(encoding="utf-8")
        guesses = [n for n, ln in enumerate(src.splitlines(), 1)
                   if ln.split("#", 1)[0].strip() == "time.sleep(0.6)"]
        self.assertEqual([], guesses,
                         "a fixed startup sleep is a readiness GUESS (#113) — "
                         "start the server through _serve(), which polls")
        # Matched as a STATEMENT (`x = subprocess.Popen(`), not as a substring:
        # this assertion's own message names the call, so a bare count would
        # count itself — the same self-reference trap the sleep lock above hit.
        starts = re.findall(r"^\s*\w+\s*=\s*subprocess\.Popen\(", src, re.M)
        self.assertEqual(1, len(starts),
                         "every upload_server start must go through _serve() "
                         "(#113); a second Popen is a new un-polled call site")

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

    def _server(self, dest):
        return _serve(self, "toktoktoktoktok27", dest, ttl=30)

    # -- the served PAGE must actually ENABLE + PERFORM a multi-file send -- #

    def test_file_input_accepts_multiple_files(self):
        dest = Path(tempfile.mkdtemp())
        _, port = self._server(dest)
        html = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/toktoktoktoktok27/", timeout=5).read().decode()
        self.assertIn("type=file multiple", html)

    def test_page_js_sends_every_dropped_file_not_just_the_first(self):
        dest = Path(tempfile.mkdtemp())
        _, port = self._server(dest)
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
        proc, port = self._server(dest)
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
        # No wait here either (#113, same defect class as the startup guess):
        # upload_server.py writes `upload SAVED …` BEFORE it sends the 200, and
        # CPython's sys.stderr is line-buffered even when it is a pipe — so the
        # 200 already received above IS the proof the line is in the pipe. The
        # sleep that used to sit here was a guess at a race that cannot happen.
        proc.terminate()
        err = proc.stderr.read()
        for name, body in bodies.items():
            self.assertIn("upload SAVED %s (%d bytes)"
                          % (dest / name, len(body)), err)

    def test_one_failing_file_does_not_block_the_others(self):
        dest = Path(tempfile.mkdtemp())
        _, port = self._server(dest)
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
