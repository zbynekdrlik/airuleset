"""Locks the receive-files-via-upload-URL capability (issue #18, 2026-07-10).

Recurring incident: the user works over SSH with NO local FS access to any
managed box — yet target Claudes (david@gk, 2026-07-10) keep asking him to scp
files up. The download direction was solved (deliver-files-as-urls + share);
the UPLOAD direction existed only as a script buried in the meeting-analysis
skill, invisible to every other session. Promoted to a first-class CLI
(`airuleset.py upload`) + an always-on module banning scp-to-user asks.
"""

import contextlib
import glob
import http.client
import io
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
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


def _log_dir(test):
    """Point `upload`'s endpoint log at a throwaway dir for ONE test (#115).

    The cmd_upload tests pass ephemeral ports, so every suite run used to leave a
    NEW never-reused `/tmp/airuleset-upload-<port>.log` behind — 1183 of them on
    dev1 the day #115 was filed, 1180 of them this user's. The env override is
    the same escape hatch filedrop already gives itself with FILEDROP_DIR.
    """
    d = Path(tempfile.mkdtemp())
    patch = m.patch.dict(os.environ, {"AIRULESET_UPLOAD_LOG_DIR": str(d)})
    patch.start()
    test.addCleanup(patch.stop)
    return d


def _spawn(test, token, dest, ips="127.0.0.1", ttl=20, launcher=None):
    """Launch upload_server.py on a free port — the ONE start in this module.

    Split out of `_serve` for #114, whose subject is a server that can bind
    NOTHING: waiting for it to serve is meaningless (it never will), but its
    exit code and how long it takes to get there are the whole contract. Every
    start that DOES expect to serve still goes through `_serve`, which adds the
    #113 readiness poll on top of this.
    """
    port = _free_port()
    argv = list(launcher or [sys.executable, str(SERVER)]) + [
        token, str(port), ips, str(dest), str(ttl)]
    proc = subprocess.Popen(argv, stderr=subprocess.PIPE, text=True)
    test.addCleanup(proc.kill)
    return proc, port


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
    proc, port = _spawn(test, token, dest, ips=ips, ttl=ttl, launcher=launcher)
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

    def test_served_page_declares_an_inline_icon(self):
        # #117: a document that declares no icon makes every browser auto-fire
        # GET /favicon.ico at the ORIGIN ROOT. That path is not /<token>/, so
        # do_GET refuses it (correctly — a favicon request carries no token, and
        # the token is this write endpoint's only auth) and the browser logs a
        # console error. browser-console-zero-errors.md treats that as a bug.
        #
        # The declaration must be INLINE — a data: URI — on two counts: the page
        # is served raw from one string with no asset pipeline behind it, and
        # upload_server.py is launched BY PATH, so any runtime file lookup would
        # be install-location dependent. Asserted against the bytes a live
        # server actually SERVES, never the source string.
        dest = Path(tempfile.mkdtemp())
        _, port = _serve(self, "toktoktoktoktok117", dest)
        html = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/toktoktoktoktok117/", timeout=5).read().decode()
        link = re.search(r"<link[^>]*\brel=[\"']?icon\b[^>]*>", html, re.I)
        self.assertIsNotNone(
            link, "the served page declares no rel=icon link, so a browser "
                  "auto-requests /favicon.ico and gets a 404 it logs as a "
                  "console error")
        href = re.search(r"href=[\"']([^\"']+)[\"']", link.group(0))
        self.assertIsNotNone(href, "the icon link carries no href: %r" % link.group(0))
        self.assertTrue(
            href.group(1).startswith("data:"),
            "icon href %r is not an inline data: URI — a page served by a "
            "stdlib server with no asset pipeline must not depend on a file "
            "found at runtime" % href.group(1)[:80])

    def test_favicon_path_is_still_refused_by_the_token_gate(self):
        # The #117 companion lock: the fix declares an icon INLINE, it does not
        # open a route. /favicon.ico is unauthenticated by construction (a
        # browser sends no token with it), so it must keep 404-ing exactly like
        # any other non-token path — the console error is removed by never
        # making the request, never by answering it.
        dest = Path(tempfile.mkdtemp())
        _, port = _serve(self, "toktoktoktoktok117b", dest)
        for path in ("favicon.ico", "apple-touch-icon.png"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/{path}", timeout=5)
            self.assertEqual(caught.exception.code, 404,
                             "/%s must stay refused by the token gate" % path)
            caught.exception.close()

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
        # ttl=0 dates from when a TTL kept the child ALIVE through its own
        # failure (the non-daemon timer parked the exit for the full TTL and
        # then exited 0 — #114, fixed since; TestTotalBindFailureIsFatal owns
        # that contract now). Kept as-is because this test is about the WAIT,
        # and ttl=0 keeps it independent of the server's shutdown timing.
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
        #       start goes through _spawn() — and every start that expects to
        #       SERVE goes through _serve(), inheriting its poll. A new call
        #       site cannot grow a private start-and-guess of its own.
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
                         "every upload_server start must go through _spawn() "
                         "(#113); a second Popen is a new un-polled call site")

    def test_cmd_upload_prints_a_url_per_interface(self):
        import airuleset
        import filedrop
        sk = socket.socket()
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
        sk.close()
        dest = Path(tempfile.mkdtemp())
        _log_dir(self)                       # #115: keep the log out of /tmp
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
        _log_dir(self)                       # #115: keep the log out of /tmp
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


class TestTotalBindFailureIsFatal(TestCase):
    """#114 (measured while fixing #113): a server that can bind NOTHING must
    end as a FAILURE, immediately.

    `upload_server.py` arms its TTL self-shutdown timer before the bind loop,
    and `threading.Timer` inherits `Thread.daemon = False` — so the interpreter
    joins it before shutting down, `sys.exit("upload: no address …")` is parked
    for the whole TTL, and the timer's own `os._exit(0)` is what finally ends
    the process. A total bind failure therefore reports SUCCESS to its caller
    (measured at HEAD 6ef01bc: 6.06s / rc=0 at ttl=6 vs 0.10s / rc=1 at ttl=0),
    which is the worst possible shape for the tool the user hands files through.

    Both directions are asserted here, because a fix that simply removed the
    TTL would satisfy the first test alone.
    """

    TTL = 6
    DEAD_IP = "203.0.113.9"     # TEST-NET-3: never local → bind always fails

    def _run_to_exit(self, proc, budget_s):
        """(stderr, elapsed) — or fail naming what the process was still doing."""
        t0 = time.monotonic()
        try:
            err = proc.communicate(timeout=budget_s)[1] or ""
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("upload server never exited within %.1fs" % budget_s)
        return err, time.monotonic() - t0

    def test_a_server_that_binds_nothing_exits_nonzero_well_before_its_ttl(self):
        # The observable contract, not the internals: a caller sees a FAILING
        # status, gets it FAST (so a readiness poll can give up on the child's
        # death instead of burning its budget — #113's `_wait_until_serving`),
        # and finds the diagnosis on stderr.
        #
        # #148: this method uses its OWN larger TTL, not the class's shared
        # `self.TTL` (6s, still used by the sibling expiry test below). At
        # ttl=6 the old bound (ttl/3.0 = 2.0s) had no headroom for full-suite
        # CPU contention — live-reproduced taking 3.34s under load, a false
        # flake, not the #113/#114 regression (whose signature is ~6.06s, the
        # timer holding the process for the FULL ttl). ttl=20 / bound=ttl/2.0
        # (10s) keeps the same "exits well under half the ttl" shape with
        # ~8.6s of headroom over the worst load-driven time observed, while
        # staying far below where the regression would land (~20s).
        ttl = 20
        dest = Path(tempfile.mkdtemp())
        t0 = time.monotonic()
        proc, _ = _spawn(self, "tok114dead", dest, ips=self.DEAD_IP, ttl=ttl)
        err, _ = self._run_to_exit(proc, ttl + 20)
        took = time.monotonic() - t0

        self.assertIn("no address", err)            # diagnosis reached stderr
        self.assertNotEqual(
            0, proc.returncode,
            "a server that bound nothing reported SUCCESS (rc=0) — the parked "
            "SystemExit was overtaken by the TTL timer's os._exit(0) (#114); "
            "stderr was:\n%s" % err)
        self.assertLess(
            took, ttl / 2.0,
            "the failure took %.2fs of a %ds TTL — the non-daemon timer thread "
            "is holding the interpreter open through its own exit (#114)"
            % (took, ttl))

    def test_the_fast_fail_bound_has_headroom_over_realistic_subprocess_overhead(self):
        # #148: the ABOVE test's own bound is only meaningful if it has real
        # headroom over what full-suite CPU contention actually does to
        # subprocess spawn/exit timing. Live-reproduced (3 consecutive full
        # `pytest tests/` runs on this repo, unchanged tree): the SAME fast-
        # fail path took 3.34s on a loaded box, comfortably tripping the OLD
        # `ttl/3.0` bound (2.0s at ttl=6) with no code regression at all.
        #
        # Reproduced DETERMINISTICALLY here (never by waiting for real load,
        # same technique as `test_readiness_wait_survives_a_slow_server_start`
        # above and #226's oneshot-TTL fix): a launcher wrapper injects a
        # fixed startup delay comfortably above the worst load-driven time
        # actually observed, comfortably below the widened bound, and far
        # below where the #113/#114 regression itself would land (~ttl).
        dest = Path(tempfile.mkdtemp())
        ttl = 20
        forced_delay_s = 3.5          # > the 3.34s worst case observed live
        slow = ("import os, sys, time; time.sleep(%r); "
                "os.execv(sys.executable, [sys.executable, %r] + sys.argv[1:])"
                % (forced_delay_s, str(SERVER)))
        t0 = time.monotonic()
        proc, _ = _spawn(self, "tok148slow", dest, ips=self.DEAD_IP, ttl=ttl,
                          launcher=[sys.executable, "-c", slow])
        err, _ = self._run_to_exit(proc, ttl + 20)
        took = time.monotonic() - t0

        self.assertIn("no address", err)
        self.assertNotEqual(0, proc.returncode)
        self.assertLess(
            took, ttl / 2.0,
            "a %.1fs forced startup delay (realistic full-suite load, not "
            "the #113/#114 regression) still tripped the bound — took %.2fs "
            "of a %ds ttl; the bound has no real headroom left (#148)"
            % (forced_delay_s, took, ttl))

    def test_a_bound_server_still_serves_and_still_expires_at_its_ttl(self):
        # The other direction: the fix must not buy a fast failure by breaking
        # the self-shutdown that keeps detached endpoints from orphaning. This
        # server binds, answers, and then must die AT its TTL — not before it
        # (no premature exit) and not never (no orphan).
        dest = Path(tempfile.mkdtemp())
        t0 = time.monotonic()
        proc, port = _serve(self, "tok114ttl", dest, ttl=self.TTL)
        page = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/tok114ttl/", timeout=5).read()
        self.assertIn(b"Upload", page)

        self._run_to_exit(proc, self.TTL + 30)
        lived = time.monotonic() - t0
        self.assertGreater(lived, self.TTL - 2.0,
                           "expired after %.2fs, well before its %ds TTL"
                           % (lived, self.TTL))
        self.assertLess(lived, self.TTL + 20.0,
                        "still alive %.2fs past its %ds TTL" % (lived, self.TTL))
        self.assertEqual(0, proc.returncode,
                         "a TTL expiry is a normal shutdown (os._exit(0))")


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


class TestUploadedFilenameIsDecodedThenContained(TestCase):
    """#116: the page URL-encodes the filename and the server never decoded it.

    `upload_server.py`'s own JS sends `encodeURIComponent(file.name)` (it has to
    — a raw space terminates the HTTP request-line target), but `do_PUT` handed
    the still-encoded segment straight to `_SAFE.sub("_", ...)`, whose class
    `[^A-Za-z0-9._-]` excludes `%`. So every escape survived with its `%` turned
    into `_`. Observed live on dev1 2026-07-28 while verifying #115:

        PUT .../nahr%C3%A1vka%20test%20(1).bin  ->  200
        saved as: nahr_C3_A1vka_20test_20_1_.bin

    Decoding is the fix, and decoding is also what makes the sanitizer a
    security boundary for the first time: `/`, `..`, NUL, control characters and
    a 4000-char name only become reachable once the escapes are resolved. The
    traversal cases below therefore pass at HEAD by accident (an un-decoded
    `..%2F` is inert) — they are guards for the fix, not reproductions of it.
    """

    TOK = "toktoktoktoktok116"

    def _dest(self):
        """(root, dest) — the sentinel parent is how an escape becomes visible."""
        root = Path(tempfile.mkdtemp())
        dest = root / "up"
        dest.mkdir()
        return root, dest

    def _put(self, port, encoded_name, body=b"x"):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/%s/%s" % (port, self.TOK, encoded_name),
            data=body, method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
                return r.status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    # ---------------- the ticket's own case, end to end ---------------- #

    def test_a_percent_encoded_slovak_name_lands_decoded_and_intact(self):
        # Exactly the bytes the page sends for `nahrávka test (1).bin`:
        # encodeURIComponent escapes the accent and the spaces, leaves the
        # parentheses alone. The user's files are Slovak, so a faithful name is
        # the whole point — ASCII-stripping this to `nahr_vka_test__1_.bin`
        # would be a milder spelling of the same complaint.
        root, dest = self._dest()
        _, port = _serve(self, self.TOK, dest, ttl=30)
        body = os.urandom(4096)
        self.assertEqual(
            200, self._put(port, "nahr%C3%A1vka%20test%20(1).bin", body))
        saved = dest / "nahrávka test (1).bin"
        self.assertTrue(
            saved.exists(),
            "the decoded name never landed — the endpoint saved %r instead "
            "(#116: the page sends encodeURIComponent(file.name) and do_PUT "
            "never unquoted it, so every escape's %% became _)"
            % sorted(p.name for p in dest.iterdir()))
        self.assertEqual(body, saved.read_bytes(), "content must be byte-identical")
        self.assertFalse((dest / "nahr_C3_A1vka_20test_20_1_.bin").exists())

    def test_the_token_segment_is_still_matched_raw(self):
        # The decode is deliberately scoped to the FILENAME segment. The
        # unguessable token is this endpoint's ONLY auth (upload_server.py
        # module docstring), so unquoting `_parts()` wholesale would let
        # `%74ok…` authenticate as `tok…` — widening the one boundary the
        # endpoint has, in a commit whose subject is a cosmetic filename bug.
        root, dest = self._dest()
        _, port = _serve(self, self.TOK, dest, ttl=30)
        req = urllib.request.Request(
            "http://127.0.0.1:%d/%s/x.bin" % (port, "%74" + self.TOK[1:]),
            data=b"x", method="PUT")
        try:
            urllib.request.urlopen(req, timeout=5).close()
            self.fail("a percent-encoded spelling of the token authenticated")
        except urllib.error.HTTPError as e:
            e.close()
            self.assertEqual(404, e.code)
        self.assertEqual([], list(dest.iterdir()))

    # ------------- what decoding newly exposes: containment ------------- #

    HOSTILE = [
        ("..%2F..%2F..%2Fetc%2Fpasswd", "relative traversal"),
        ("%2Fetc%2Fpasswd", "an absolute path"),
        ("..%5C..%5Cwindows%5Csystem32", "windows separators"),
        ("%2E%2E%2F%2E%2E%2Fescape.bin", "fully-escaped dots and slashes"),
        ("~%2F.ssh%2Fauthorized_keys", "a leading tilde"),
        ("evil%00.bin", "an embedded NUL"),
        ("a%0D%0Ab.bin", "embedded CR/LF"),
        ("%2E%2E", "the parent directory itself"),
        ("%2E%2E%2E%2E", "nothing but dots"),
        ("-rf", "a name a later CLI would read as a flag"),
        ("%E2%80%AEcod.exe", "an RTL override (extension spoofing)"),
    ]

    def test_no_hostile_name_can_write_outside_the_destination(self):
        root, dest = self._dest()
        _, port = _serve(self, self.TOK, dest, ttl=30)
        for encoded, why in self.HOSTILE:
            with self.subTest(name=encoded, why=why):
                self.assertIn(self._put(port, encoded, b"pwned"), (200, 400), why)
        # Nothing may exist beside the destination directory itself — including
        # a stray `<name>.part`, which is where the streaming write lands first.
        self.assertEqual(
            ["up"], sorted(p.name for p in root.iterdir()),
            "a hostile filename wrote outside the upload directory (#116)")
        for p in dest.iterdir():
            self.assertTrue(p.is_file(), "%s is not a regular file" % p)
            self.assertEqual(dest.resolve(), p.resolve().parent,
                             "%s resolved outside the destination" % p)

    def test_a_traversal_name_lands_flattened_rather_than_silently_renamed(self):
        # `os.path.basename` would turn this into a plausible-looking
        # `passwd`; mapping the separators keeps the hostile name visible in
        # the listing and in the SAVED log line.
        root, dest = self._dest()
        _, port = _serve(self, self.TOK, dest, ttl=30)
        self.assertEqual(200, self._put(port, "..%2F..%2F..%2Fetc%2Fpasswd"))
        self.assertEqual([".._.._.._etc_passwd"],
                         sorted(p.name for p in dest.iterdir()))

    def test_a_name_with_nothing_safe_left_still_saves_under_a_fallback(self):
        # `..` survives a character filter untouched (dots are legal in a
        # filename) and `os.path.join(DEST, "..")` is the upload directory's
        # PARENT — so the empty/dots-only case needs its own guard.
        root, dest = self._dest()
        _, port = _serve(self, self.TOK, dest, ttl=30)
        self.assertEqual(200, self._put(port, "%2E%2E", b"dots"))
        self.assertEqual(["upload.bin"], sorted(p.name for p in dest.iterdir()))
        self.assertEqual(b"dots", (dest / "upload.bin").read_bytes())

    def test_an_absurdly_long_name_is_clipped_to_a_filesystem_safe_length(self):
        # 400 Slovak characters are 800 UTF-8 BYTES, and ext4 caps a name at
        # 255 bytes — so the clip has to count bytes, and has to leave room for
        # the `.part` suffix the stream is written under before the rename.
        root, dest = self._dest()
        _, port = _serve(self, self.TOK, dest, ttl=30)
        self.assertEqual(
            200, self._put(port, urllib.parse.quote("á" * 400 + ".bin"), b"ok"))
        landed = list(dest.iterdir())
        self.assertEqual(1, len(landed), landed)
        self.assertLessEqual(len((landed[0].name + ".part").encode("utf-8")), 255)
        self.assertTrue(landed[0].name.endswith(".bin"),
                        "the extension must survive the clip: %r" % landed[0].name)
        self.assertEqual(b"ok", landed[0].read_bytes())


class TestUploadDocsMatchTheSanitizer(TestCase):
    """#116's other half: the docs described a third behaviour, true neither
    before the fix nor after it."""

    SKILL = "skills/meeting-analysis/SKILL.md"

    def test_the_skill_no_longer_promises_ascii_stripping(self):
        t = read(self.SKILL)
        self.assertNotIn(
            "accents / parens become", t,
            "the skill promised `spaces / accents / parens become _`, which "
            "described neither the old behaviour (percent-escapes spelled out "
            "as _C3_A1) nor the new one (they are preserved) — #116")

    def test_the_skill_documents_that_the_real_name_is_preserved(self):
        # The reader's actual question is "what will the file be called", so the
        # doc has to answer it with a worked example rather than a rule of thumb.
        t = read(self.SKILL)
        self.assertIn("nahrávka test (1).mp4", t)

    def test_the_skill_snippet_quotes_a_path_that_can_contain_spaces(self):
        # Spaces survive now, so an unquoted `VIDEO=$HOME/uploads/...` would
        # word-split the moment a real recording name reaches it.
        t = read(self.SKILL)
        self.assertNotIn("\nVIDEO=$HOME/uploads/", t,
                         "the uploaded-path assignment must be quoted (#116)")


class TestFreePortScanSeesTheServersOwnBinds(TestCase):
    """#115 defect 1: the scan handed out a port its OWN endpoint already held.

    `cmd_upload` probed `connect_ex(("127.0.0.1", cand))`, but `filedrop._is_private`
    EXCLUDES loopback and `upload_server.py` binds exactly `bind_ips()`, never
    `0.0.0.0` (it is a WRITE endpoint on a box that may have a public IP). The
    probe's address and the server's addresses are therefore disjoint BY
    CONSTRUCTION — the blind spot is total, not occasional. Observed live on dev1
    2026-07-28: five listeners on :8799 and the scan still picked 8799, after
    which a second `airuleset.py upload` died with `endpoint failed to come up`
    while its readiness probes hit the FIRST server (`"GET /<new-token>/" 404`
    twenty times, in a log belonging to the other endpoint).
    """

    def _hold(self, ip):
        """Listen on an ephemeral port of `ip` for the test's lifetime."""
        sk = socket.socket()
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sk.bind((ip, 0))
        sk.listen(5)
        self.addCleanup(sk.close)
        return sk.getsockname()[1]

    def test_a_port_held_on_a_bind_address_is_never_handed_out(self):
        # 127.0.0.2 stands in for a real bind_ips() address: bindable, and NOT
        # the one address the old scan probed. The premise is asserted, not
        # assumed — the connect probe really is blind to the held port, so a
        # picker that still returns it is reproducing the live defect and not an
        # artefact of how this test is built.
        held = self._hold("127.0.0.2")
        probe = socket.socket()
        self.addCleanup(probe.close)
        self.assertNotEqual(
            0, probe.connect_ex(("127.0.0.1", held)),
            "premise broken: a connect probe on 127.0.0.1 was supposed to be "
            "blind to a listener held on 127.0.0.2")
        free = _free_port()
        self.assertEqual(
            free, airuleset._pick_free_port(["127.0.0.2"], [held, free]),
            "the scan handed out a port the endpoint's own bind address is "
            "already listening on (#115)")

    def test_an_unbindable_address_does_not_veto_every_port(self):
        # upload_server.py SKIPS an address it cannot bind (a stale LAN IP) and
        # requires only one success — so EADDRNOTAVAIL must not read as "port
        # occupied", or one departed interface rejects all 21 candidates on a
        # box that serves perfectly well.
        free = _free_port()
        self.assertEqual(
            free,
            airuleset._pick_free_port(["203.0.113.9", "127.0.0.1"], [free]))

    def test_no_candidate_left_returns_none(self):
        held = self._hold("127.0.0.2")
        self.assertIsNone(airuleset._pick_free_port(["127.0.0.2"], [held]))

    def test_cmd_upload_scans_the_very_addresses_it_will_bind(self):
        # The root cause in one assertion: the set of addresses probed must be
        # the set of addresses the server is about to bind. Anything else is the
        # #115 blind spot in a new spelling.
        import filedrop
        ips = ["127.0.0.2", "127.0.0.3"]
        dest = Path(tempfile.mkdtemp())
        _log_dir(self)
        port = _free_port()
        with m.patch.object(filedrop, "bind_ips", return_value=ips), \
             m.patch.object(airuleset, "_pick_free_port",
                            return_value=port) as pick:
            with contextlib.redirect_stdout(io.StringIO()):
                airuleset.cmd_upload(m.Mock(dir=str(dest), ttl=5, port=None))
        self.assertEqual(ips, list(pick.call_args[0][0]))


class TestUploadLogPathIsPerUser(TestCase):
    """#115 defect 2: a world-shared /tmp path keyed on the port alone.

    The FIRST user to run on a port owned that filename for every other user on
    the box. dev1 still carries `-rw-rw-r-- montalu /tmp/airuleset-upload-8811.log`,
    so `--port 8811` as anyone else died with an unhandled
    `PermissionError: [Errno 13] ... '/tmp/airuleset-upload-8811.log'` — a
    traceback instead of a diagnosis, from the one CLI whose whole job is to be
    reachable the moment someone needs to hand over a file.
    """

    def _default_path(self, home, port):
        env = m.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("AIRULESET_UPLOAD_LOG_DIR", None)
        with m.patch.object(Path, "home", return_value=Path(home)):
            return airuleset._upload_log_path(port)

    def test_two_users_on_one_port_cannot_collide(self):
        a = self._default_path("/home/userA", 8811)
        b = self._default_path("/home/userB", 8811)
        self.assertNotEqual(a, b, "same log path for two different users (#115)")
        for p in (a, b):
            self.assertNotIn("/tmp/airuleset-upload-", str(p),
                             "still in the world-shared /tmp namespace (#115)")

    def test_the_dir_is_overridable_so_tests_need_not_touch_home(self):
        d = Path(tempfile.mkdtemp())
        with m.patch.dict(os.environ, {"AIRULESET_UPLOAD_LOG_DIR": str(d)}):
            self.assertEqual(d, airuleset._upload_log_path(8799).parent)

    def test_an_unappendable_log_gives_a_diagnosis_not_a_traceback(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores the mode bits this case depends on")
        import filedrop
        d = _log_dir(self)
        dest = Path(tempfile.mkdtemp())
        port = _free_port()
        # Stands in for montalu's file: a log this user cannot append to. chmod 0
        # reproduces the exact PermissionError without touching anyone else's file
        # (never delete or chmod another user's /tmp entries).
        log = airuleset._upload_log_path(port)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("stale\n")
        log.chmod(0o000)
        self.addCleanup(lambda: log.chmod(0o600))
        self.assertEqual(d, log.parent)
        err = io.StringIO()
        with m.patch.object(filedrop, "bind_ips", return_value=["127.0.0.1"]), \
             contextlib.redirect_stderr(err), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                airuleset.cmd_upload(m.Mock(dir=str(dest), ttl=5, port=port))
        self.assertNotEqual(0, ctx.exception.code)
        self.assertIn(str(log), err.getvalue(),
                      "the failure must name the log it could not open")


class TestUploadLogsDoNotLitterTmp(TestCase):
    """#115 defect 3: 1183 leftover `/tmp/airuleset-upload-*.log` on dev1."""

    def test_a_cmd_upload_run_leaves_no_new_tmp_log(self):
        import filedrop
        pattern = "/tmp/airuleset-upload-*.log"
        # A set difference ALONE passes for the wrong reason whenever the
        # ephemeral port happens to match one of the leftovers already there
        # (observed once against the 1186 on dev1) — the litter hides itself.
        # So pick a port whose legacy path does not exist yet, and assert on
        # that exact path as well.
        for _ in range(20):
            port = _free_port()
            legacy = Path("/tmp/airuleset-upload-%d.log" % port)
            if not legacy.exists():
                break
        else:                                           # pragma: no cover
            self.fail("no ephemeral port left without a leftover /tmp log")
        before = set(glob.glob(pattern))
        dest = Path(tempfile.mkdtemp())
        _log_dir(self)
        with m.patch.object(filedrop, "bind_ips", return_value=["127.0.0.1"]):
            with contextlib.redirect_stdout(io.StringIO()):
                airuleset.cmd_upload(m.Mock(dir=str(dest), ttl=5, port=port))
        self.assertFalse(
            legacy.exists(),
            "the run wrote %s — a world-shared /tmp log keyed on the port "
            "(#115)" % legacy)
        self.assertEqual(
            set(), set(glob.glob(pattern)) - before,
            "a suite run still accumulates one shared-/tmp log per ephemeral "
            "port (#115) — 1183 had piled up on dev1")


if __name__ == "__main__":
    main()
