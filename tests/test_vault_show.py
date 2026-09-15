"""`secret show` — the OUTPUT direction of the vault (#580).

Delivers a credential the BOX holds (a vault NAME or a `--file` durable path)
TO the owner through a ONE-SHOT render URL — never chat, never "run cat
yourself". Mirrors filedrop/vault_server.py's INPUT endpoint but serves the
value ONCE and tears down.

Offline: the server-handler tests spawn filedrop/show_server.py bound to
127.0.0.1 ONLY (never a public interface, even in a fixture); the vault-helper
and CLI-wiring tests run in-process against tmp dirs.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset                                        # noqa: E402
import cli_vault                                        # noqa: E402
from filedrop import vault as st                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SHOW_SERVER = ROOT / "filedrop" / "show_server.py"

# Deliberately not spelled `token = "..."` — hooks/block-sensitive-staging.sh's
# KV_PAT reads that shape as a credential assignment (#116 lesson).
VAL = "hunter2-show-value-580"
TOK = "toktoktoktoktok580"


def _free_port():
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    return port


class _StoreCase(TestCase):
    """Every test runs against its OWN tmp store + tmp log dirs — never the real
    `~/.claude/secrets/`."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._env = {
            st.SECRETS_DIR_ENV: str(root / "secrets"),
            st.SECRET_LOG_DIR_ENV: str(root / "secret-logs"),
        }
        for k, v in self._env.items():
            os.environ[k] = v
        self.addCleanup(self._restore)
        self.assertTrue(str(st.secrets_dir()).startswith(self.tmp.name))

    def _restore(self):
        for k in self._env:
            os.environ.pop(k, None)

    def _secret_file(self, name="webterm_credential", data=VAL, mode=0o600):
        p = Path(self.tmp.name) / name
        p.write_text(data, encoding="utf-8")
        os.chmod(str(p), mode)
        return p


# --------------------------------------------------------------------------- #
# vault.py helpers — validate_show_file / read_show_file / show_log_label
# --------------------------------------------------------------------------- #
class TestValidateShowFile(_StoreCase):
    def test_a_0600_regular_file_is_accepted_and_resolved(self):
        p = self._secret_file(mode=0o600)
        real = st.validate_show_file(str(p))
        self.assertEqual(real, p.resolve())

    def test_a_0400_owner_only_file_is_accepted(self):
        p = self._secret_file(mode=0o400)
        self.assertEqual(st.validate_show_file(str(p)), p.resolve())

    def test_a_group_or_world_readable_file_is_refused(self):
        for bad in (0o644, 0o640, 0o664, 0o604):
            p = self._secret_file(name="cred_%o" % bad, mode=bad)
            with self.assertRaises(st.SecretError, msg=oct(bad)):
                st.validate_show_file(str(p))

    def test_a_symlink_source_is_refused(self):
        real = self._secret_file(mode=0o600)
        link = Path(self.tmp.name) / "link_to_cred"
        link.symlink_to(real)
        with self.assertRaises(st.SecretError):
            st.validate_show_file(str(link))

    def test_a_file_inside_a_git_repo_is_refused(self):
        repo = Path(self.tmp.name) / "repo"
        (repo / ".git").mkdir(parents=True)
        p = repo / "cred"
        p.write_text(VAL, encoding="utf-8")
        os.chmod(str(p), 0o600)
        with self.assertRaises(st.SecretError):
            st.validate_show_file(str(p))

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(st.SecretError):
            st.validate_show_file(str(Path(self.tmp.name) / "nope"))

    def test_a_directory_is_refused(self):
        d = Path(self.tmp.name) / "adir"
        d.mkdir(mode=0o700)
        with self.assertRaises(st.SecretError):
            st.validate_show_file(str(d))

    def test_a_foreign_owned_file_is_refused(self):
        # Review finding (t4): a cross-user 0600 file on the shared subdev VPS
        # passes the mode check but is not ours to show.
        p = self._secret_file(mode=0o600)
        with m.patch("os.getuid", return_value=os.getuid() + 424242):
            with self.assertRaises(st.SecretError):
                st.validate_show_file(str(p))


class TestReadShowFile(_StoreCase):
    def test_reads_the_bytes_byte_exact(self):
        raw = b"-----BEGIN KEY-----\nabc\n-----END KEY-----\n"
        p = Path(self.tmp.name) / "sshkey"
        p.write_bytes(raw)
        os.chmod(str(p), 0o600)
        self.assertEqual(st.read_show_file(str(p)), raw)

    def test_refuses_a_symlink_swapped_in_after_validation(self):
        # O_NOFOLLOW: a symlink planted at the path is never read THROUGH.
        victim = Path(self.tmp.name) / "victim"
        victim.write_text("secret-key-material", encoding="utf-8")
        os.chmod(str(victim), 0o600)
        link = Path(self.tmp.name) / "link"
        link.symlink_to(victim)
        with self.assertRaises(st.SecretError):
            st.read_show_file(str(link))

    def test_an_empty_file_is_refused(self):
        p = Path(self.tmp.name) / "empty"
        p.write_bytes(b"")
        os.chmod(str(p), 0o600)
        with self.assertRaises(st.SecretError):
            st.read_show_file(str(p))

    def test_an_oversize_file_is_refused(self):
        p = Path(self.tmp.name) / "big"
        p.write_bytes(b"x" * (st.MAX_SECRET_BYTES + 1))
        os.chmod(str(p), 0o600)
        with self.assertRaises(st.SecretError):
            st.read_show_file(str(p))


class TestShowLogLabel(TestCase):
    def test_a_vault_name_is_used_verbatim(self):
        self.assertEqual(st.show_log_label("name", "DB_PASS"), "DB_PASS")

    def test_a_file_label_is_the_sanitised_basename_and_name_re_valid(self):
        lab = st.show_log_label("file", "/home/x/.secrets/cloudflare-spinbike")
        self.assertNotIn("/", lab)                 # never the directory path
        self.assertTrue(st.NAME_RE.fullmatch(lab), lab)   # log_event-safe

    def test_a_digit_leading_basename_is_prefixed(self):
        lab = st.show_log_label("file", "/x/9key")
        self.assertTrue(st.NAME_RE.fullmatch(lab), lab)

    def test_the_label_carries_no_value(self):
        # The basename names the PURPOSE, never the value.
        lab = st.show_log_label("file", "/x/webterm_credential")
        self.assertNotIn(VAL, lab)


# --------------------------------------------------------------------------- #
# show_server.py — the one-shot render endpoint
# --------------------------------------------------------------------------- #
class _ShowServerCase(_StoreCase):
    def _spawn(self, kind="name", locator="DB_PASS", ips="127.0.0.1",
               ttl="30", port=None):
        port = port or _free_port()
        env = dict(os.environ)
        env.update(self._env)
        env["AIRULESET_VAULT_TOKEN"] = TOK
        # stderr -> a per-port FILE (as the real CLI does, `show-endpoint-<port>.log`),
        # so the endpoint log can be read WHILE the process is still alive — the
        # post-#1011 endpoint stays up after serving (410 for a later view) instead
        # of os._exit'ing, so `proc.communicate()` would block.
        errpath = Path(self.tmp.name) / ("show-err-%d.log" % port)
        errf = open(errpath, "w", encoding="utf-8")
        self.addCleanup(errf.close)
        proc = subprocess.Popen(
            [sys.executable, str(SHOW_SERVER), str(port), ips, kind,
             locator, ttl],
            stdout=subprocess.DEVNULL, stderr=errf, env=env, text=True)
        proc._errpath = str(errpath)
        self.addCleanup(self._kill, proc)
        return proc, port

    @staticmethod
    def _kill(proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    def _errlog(self, proc):
        try:
            return Path(proc._errpath).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _serve(self, **kw):
        proc, port = self._spawn(**kw)
        health = "http://127.0.0.1:%d/healthz" % port
        end = time.monotonic() + 20
        while time.monotonic() < end:
            if proc.poll() is not None:
                self.fail("show server exited rc=%s: %s"
                          % (proc.returncode, self._errlog(proc)))
            try:
                with urllib.request.urlopen(health, timeout=2) as r:
                    if r.status in (200, 204):
                        return proc, port
            except OSError:
                time.sleep(0.1)
        self.fail("show server never came up on %d" % port)

    def _req(self, url, method="GET", headers=None, timeout=10):
        data = b"" if method == "POST" else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def _get(self, url, headers=None, timeout=10):
        return self._req(url, "GET", headers, timeout)

    def _post(self, url, headers=None, timeout=10):
        return self._req(url, "POST", headers, timeout)

    def _url(self, port):
        return "http://127.0.0.1:%d/%s/" % (port, TOK)


class TestShowServerNamedSource(_ShowServerCase):
    def _store(self, name="DB_PASS", val=VAL):
        st.store_value(name, val.encode(), keep_s=600)

    def test_a_named_value_is_shown_once_via_post(self):
        # #1011: a GET only ever serves the click-to-reveal page (no value); the
        # value is revealed once by the same-origin POST a human's click sends.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = self._url(port)
        gcode, gbody, _ = self._get(url)
        self.assertEqual(gcode, 200)
        self.assertNotIn(VAL.encode(), gbody)      # the reveal page has NO value
        pcode, pbody, _ = self._post(url)
        self.assertEqual(pcode, 200)
        self.assertIn(VAL.encode(), pbody)         # the POST reveals the value
        # The value STAYS in the vault — show does not consume it.
        self.assertEqual(st.state("DB_PASS"), "ready")

    def test_healthz_does_not_consume_the_one_shot(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        health = "http://127.0.0.1:%d/healthz" % port
        for _ in range(3):
            code, _, _ = self._get(health)
            self.assertIn(code, (200, 204))
        self.assertIsNone(proc.poll())              # still alive
        code, body, _ = self._post(self._url(port))
        self.assertEqual(code, 200)
        self.assertIn(VAL.encode(), body)

    def test_the_reveal_page_carries_a_no_store_header(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        _, _, headers = self._get(self._url(port))
        cc = {k.lower(): v for k, v in headers.items()}.get("cache-control", "")
        self.assertIn("no-store", cc)

    def test_a_wrong_token_is_404_and_does_not_tear_down(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        base = "http://127.0.0.1:%d/" % port
        for bad in (base, base + "nope/", base + "favicon.ico"):
            code, _, _ = self._get(bad)
            self.assertEqual(code, 404, bad)
            pcode, _, _ = self._post(bad)         # a POST on a bad token is 404 too
            self.assertEqual(pcode, 404, bad)
        self.assertIsNone(proc.poll())              # a 404 must not tear it down

    def test_the_value_never_reaches_the_endpoints_own_output_or_log(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        self._post(self._url(port))                 # consume via POST
        err = self._errlog(proc)
        self.assertNotIn(VAL, err)                  # never the value on stderr
        self.assertNotIn(TOK, err)                  # never the token on stderr
        self.assertNotIn(VAL, st.log_path().read_text(encoding="utf-8"))
        # ...but the delivery log DID record the show event, value-free.
        self.assertIn("shown", st.log_path().read_text(encoding="utf-8"))

    def test_both_pages_are_self_contained_no_favicon_route(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        _, gbody, _ = self._get(self._url(port))    # reveal page
        reveal = gbody.decode("utf-8")
        self.assertNotIn("{{", reveal)              # the repo's brace trap
        self.assertIn("rel=icon", reveal)           # inline data icon, no /favicon.ico
        _, pbody, _ = self._post(self._url(port))   # value page
        value = pbody.decode("utf-8")
        self.assertNotIn("{{", value)
        self.assertIn("rel=icon", value)

    def test_the_endpoint_self_expires(self):
        self._store()
        proc, _port = self._spawn(kind="name", locator="DB_PASS", ttl="1")
        proc.wait(timeout=30)
        self.assertEqual(proc.returncode, 0)


class TestShowServerFileSource(_ShowServerCase):
    def test_a_file_value_is_shown_once_via_post(self):
        p = self._secret_file(mode=0o600)
        proc, port = self._serve(kind="file", locator=str(p))
        url = self._url(port)
        _, gbody, _ = self._get(url)
        self.assertNotIn(VAL.encode(), gbody)      # reveal page, no value
        code, body, _ = self._post(url)
        self.assertEqual(code, 200)
        self.assertIn(VAL.encode(), body)

    def test_a_public_bind_is_refused_before_binding(self):
        p = self._secret_file(mode=0o600)
        proc, _port = self._spawn(kind="file", locator=str(p), ips="8.8.8.8")
        proc.wait(timeout=20)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("private", self._errlog(proc).lower())

    def test_a_bad_file_is_refused_before_binding(self):
        bad = self._secret_file(name="world_readable", mode=0o644)
        proc, _port = self._spawn(kind="file", locator=str(bad))
        proc.wait(timeout=20)
        self.assertNotEqual(proc.returncode, 0)

    def test_the_server_rechecks_private_independently_of_filedrop(self):
        src = SHOW_SERVER.read_text(encoding="utf-8")
        self.assertNotIn("from filedrop import bind_ips", src)
        self.assertIn("def is_private", src)


class TestShowServerHardening(_ShowServerCase):
    """Review-fix coverage (#580) carried onto the #1011 POST-reveal flow: the
    one-shot consume-latch (MAJOR) now lives on do_POST, the placeholder-order
    integrity fix, and the non-ASCII token guard."""

    def _store(self, name="DB_PASS", val=VAL):
        st.store_value(name, val.encode(), keep_s=600)

    def test_concurrent_posts_serve_the_value_at_most_once(self):
        # The value-revealing request is now the POST. Without the process-global
        # consume-latch, up to MAX_CONNECTIONS threads each read + serve the value.
        # The endpoint no longer os._exit's after serving (a later view gets 410),
        # so EVERY racing POST completes — giving this behavioural test real teeth:
        # latch off -> several 200s; latch on -> exactly one 200, the rest 410.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = self._url(port)
        results = []
        barrier = threading.Barrier(8)

        def hit():
            barrier.wait()
            try:
                code, body, _ = self._post(url, timeout=10)
                results.append((code, VAL.encode() in body))
            except OSError:
                results.append((None, False))

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=25)
        served = [r for r in results if r[0] == 200 and r[1]]
        self.assertEqual(len(served), 1, results)   # value served EXACTLY once
        self.assertTrue(all(c == 410 for c, _ in results if c != 200), results)

    def test_the_one_shot_latch_precedes_the_value_read(self):
        # Deterministic teeth for the one-shot: the consume-latch (claim _served
        # under _serve_lock) MUST run BEFORE _read_value() in do_POST — the request
        # that reveals the value. do_GET no longer reads the value at all.
        src = SHOW_SERVER.read_text(encoding="utf-8")
        do_post = src.split("def do_POST", 1)[1].split("\n\n\n", 1)[0]
        self.assertIn("with _serve_lock:", do_post)
        latch = do_post.find("_served = True")
        read = do_post.find("_read_value(")
        self.assertNotEqual(latch, -1, "consume-latch missing from do_POST")
        self.assertNotEqual(read, -1, "_read_value call missing from do_POST")
        self.assertLess(latch, read,
                        "the consume-latch must be claimed BEFORE _read_value()")

    def test_do_get_never_reads_the_value(self):
        # #1011: a GET must NEVER reach the value — the reveal page carries none
        # and the value read moved wholly onto do_POST.
        src = SHOW_SERVER.read_text(encoding="utf-8")
        do_get = src.split("def do_GET", 1)[1].split("\n    def ", 1)[0]
        self.assertNotIn("_read_value(", do_get)

    def test_a_value_containing_the_placeholder_token_is_not_corrupted(self):
        # NAME is substituted first, VALUE last, so a value literally
        # containing "NAME_PLACEHOLDER" survives intact — now on the POST page.
        tricky = "PART1-NAME_PLACEHOLDER-PART2-580"
        self._store(val=tricky)
        proc, port = self._serve(kind="name", locator="DB_PASS")
        code, body, _ = self._post(self._url(port))
        self.assertEqual(code, 200)
        self.assertIn(json.dumps(tricky).encode(), body)

    def test_a_raw_non_ascii_token_segment_is_404_not_a_crash(self):
        # A raw high byte in the request target -> Latin-1-decoded non-ASCII
        # self.path -> hmac.compare_digest would raise TypeError; the guard
        # turns it into a plain 404 with no teardown and no traceback.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"GET /\xc3\xa9nope/ HTTP/1.1\r\n"
                  b"Host: x\r\nConnection: close\r\n\r\n")
        resp = s.recv(4096)
        s.close()
        self.assertIn(b"404", resp.split(b"\r\n", 1)[0])
        self.assertIsNone(proc.poll())              # did not crash / tear down


class TestShowServerRevealFlow1011(_ShowServerCase):
    """#1011: a browser prefetch / hover-preload / chat unfurl must NOT burn the
    one-shot before the owner's intentional click. GET serves a click-to-reveal
    page (no value, no latch); the value + latch live only on the POST the button
    sends; an announced prefetch GET is a no-latch 204."""

    def _store(self, name="DB_PASS", val=VAL):
        st.store_value(name, val.encode(), keep_s=600)

    def test_a_get_serves_the_reveal_page_with_no_value_and_no_latch_flip(self):
        # (a) a GET returns the reveal page with NO secret bytes AND does not flip
        # the latch — a SECOND GET still serves the page, and the value is still
        # revealable by POST afterwards.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = self._url(port)
        for _ in range(2):
            code, body, _ = self._get(url)
            self.assertEqual(code, 200)
            self.assertNotIn(VAL.encode(), body)
        # the two GETs did NOT consume the one-shot: the POST still reveals it.
        code, body, _ = self._post(url)
        self.assertEqual(code, 200)
        self.assertIn(VAL.encode(), body)

    def test_the_post_reveals_the_value_exactly_once(self):
        # (b) the POST reveals the value once; a second POST is 410, never a value.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = self._url(port)
        code, body, _ = self._post(url)
        self.assertEqual(code, 200)
        self.assertIn(VAL.encode(), body)
        code2, body2, _ = self._post(url)
        self.assertEqual(code2, 410)
        self.assertNotIn(VAL.encode(), body2)

    def test_a_get_after_a_successful_post_is_410_never_the_value(self):
        # (c) once revealed, EVERY subsequent request is 410 — a GET after the POST
        # gets 410, never the value and never a fresh reveal page.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = self._url(port)
        code, body, _ = self._post(url)
        self.assertEqual(code, 200)
        self.assertIn(VAL.encode(), body)
        gcode, gbody, _ = self._get(url)
        self.assertEqual(gcode, 410)
        self.assertNotIn(VAL.encode(), gbody)

    def test_a_sec_purpose_prefetch_get_is_204_and_does_not_latch(self):
        # (d) an announced prefetch GET (Sec-Purpose: prefetch) is a bare 204 that
        # touches nothing — no value, no latch, not even the reveal page HTML; the
        # owner's later GET still gets the reveal page and the POST still reveals.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = self._url(port)
        code, body, _ = self._get(url, headers={"Sec-Purpose": "prefetch"})
        self.assertEqual(code, 204)
        self.assertEqual(body, b"")
        self.assertNotIn(VAL.encode(), body)
        # the prefetch did NOT flip the latch:
        gcode, gbody, _ = self._get(url)
        self.assertEqual(gcode, 200)
        self.assertNotIn(VAL.encode(), gbody)      # still the reveal page
        pcode, pbody, _ = self._post(url)
        self.assertEqual(pcode, 200)
        self.assertIn(VAL.encode(), pbody)          # value still revealable

    def test_the_consuming_request_method_and_ua_are_logged_never_the_value(self):
        # (e) the consuming request's method + User-Agent are logged to the
        # endpoint log (stderr, `show-endpoint-<port>.log`) for attribution —
        # never the value, never the token.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        marker_ua = "owner-browser-repro-1011"
        code, _, _ = self._post(self._url(port), headers={"User-Agent": marker_ua})
        self.assertEqual(code, 200)
        err = self._errlog(proc)
        self.assertIn("POST", err)                  # the consuming method
        self.assertIn(marker_ua, err)               # the consuming UA (attribution)
        self.assertNotIn(VAL, err)                  # NEVER the value
        self.assertNotIn(TOK, err)                  # NEVER the token

    def test_the_reveal_page_carries_anti_cache_headers_and_no_token(self):
        # (f) the reveal page (GET) carries the SAME anti-cache headers the value
        # response carries, and no inline copy of the token beyond the form action.
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        _, body, headers = self._get(self._url(port))
        low = {k.lower(): v for k, v in headers.items()}
        self.assertIn("no-store", low.get("cache-control", ""))
        self.assertIn("no-referrer", low.get("referrer-policy", ""))
        self.assertEqual(low.get("x-content-type-options", ""), "nosniff")
        self.assertIn("default-src 'none'", low.get("content-security-policy", ""))
        # No inline copy of the token: the form posts to the current URL (no
        # action attribute), so the token never appears in the page body.
        self.assertNotIn(TOK.encode(), body)


# --------------------------------------------------------------------------- #
# CLI wiring — cmd_secret's `show` action
# --------------------------------------------------------------------------- #
class TestShowCliWiring(_StoreCase):
    def test_show_is_a_valid_action(self):
        self.assertIn("show", cli_vault.SECRET_ACTIONS)
        self.assertIn("show", airuleset.SECRET_ACTIONS)

    def test_show_ports_are_disjoint_from_secret_and_upload(self):
        show = set(cli_vault.SHOW_PORTS)
        secret = set(cli_vault.SECRET_PORTS)
        self.assertFalse(show & secret)
        # upload's 8799-8819 (filedrop) must not overlap either
        self.assertFalse(show & set(range(8799, 8820)))

    def _ns(self, **kw):
        import argparse as ap
        base = dict(action="show", name=None, ttl=None, keep=None, port=None,
                    env=None, allow_plain=False, replace=False, stdin=False,
                    persist=None, file=None, cmd=[])
        base.update(kw)
        return ap.Namespace(**base)

    def test_a_bad_file_exits_2(self):
        bad = self._secret_file(name="world_readable", mode=0o644)
        with self.assertRaises(SystemExit) as cm:
            cli_vault.cmd_secret(self._ns(file=str(bad)))
        self.assertEqual(cm.exception.code, 2)

    def test_no_name_and_no_file_exits_2(self):
        with self.assertRaises(SystemExit) as cm:
            cli_vault.cmd_secret(self._ns())
        self.assertEqual(cm.exception.code, 2)

    def test_a_not_stored_name_exits_nonzero(self):
        with self.assertRaises(SystemExit) as cm:
            cli_vault.cmd_secret(self._ns(name="NOPE"))
        self.assertNotEqual(cm.exception.code, 0)

    def test_show_resolution_never_reads_the_value(self):
        # The CLI parent resolves the source WITHOUT reading the value —
        # read_value/read_show_file must not be called during resolution.
        p = self._secret_file(mode=0o600)
        with m.patch.object(st, "read_show_file",
                            side_effect=AssertionError("value read in parent")):
            kind, locator, label = cli_vault._secret_show_source(self._ns(file=str(p)), st)
        self.assertEqual(kind, "file")
        self.assertEqual(locator, str(p.resolve()))


# --------------------------------------------------------------------------- #
# Doctrine — the OUTPUT direction in the credential modules (#580)
# --------------------------------------------------------------------------- #
class TestShowDoctrine(TestCase):
    RECV = ROOT / "modules" / "core" / "receive-files-via-upload-url.md"
    DELIVER = ROOT / "modules" / "core" / "deliver-files-as-urls.md"
    # #859 batch 3: the `secret show` output-direction detail moved out of the
    # always-on module into this on-demand companion.
    RECV_DEEP = ROOT / "skills" / "receive-files-credentials" / "DEEP.md"

    def _line_with(self, text, finder, *cotokens):
        """Per-line teeth (#500): the ONE operative line containing `finder`
        must carry every co-token — a partial revert drops them together."""
        hits = [ln for ln in text.splitlines() if finder in ln]
        self.assertTrue(hits, "no line contains %r" % finder)
        self.assertTrue(
            any(all(c in ln for c in cotokens) for ln in hits),
            "no %r line carries all of %r" % (finder, cotokens))

    def test_receive_module_documents_the_output_direction(self):
        t = self.RECV_DEEP.read_text(encoding="utf-8")
        self.assertIn("Delivering a CREDENTIAL TO the Owner", t)
        # The command line names both sources.
        self._line_with(t, "secret show <NAME>", "secret show <NAME>",
                        "secret show --file")
        # A BANNED line forbids chat + "run cat yourself", routing to `secret show`.
        self._line_with(t, "run `cat` yourself", "run `cat` yourself",
                        "chat")
        self.assertIn("SESSION never sees the value", t)

    def test_deliver_stub_points_at_the_credential_out_channel(self):
        t = self.DELIVER.read_text(encoding="utf-8")
        self._line_with(t, "A credential is NOT a file", "secret show",
                        "one-shot")
        # Must stay a short stub AND keep its cross-reference (other tests
        # assert these too — do not regress them here).
        self.assertLess(len(t.splitlines()), 12)
        self.assertIn("receive-files-via-upload-url", t)


if __name__ == "__main__":
    main()
