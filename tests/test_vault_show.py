"""`secret show` — the OUTPUT direction of the vault (#580).

Delivers a credential the BOX holds (a vault NAME or a `--file` durable path)
TO the owner through a ONE-SHOT render URL — never chat, never "run cat
yourself". Mirrors filedrop/vault_server.py's INPUT endpoint but serves the
value ONCE and tears down.

Offline: the server-handler tests spawn filedrop/show_server.py bound to
127.0.0.1 ONLY (never a public interface, even in a fixture); the vault-helper
and CLI-wiring tests run in-process against tmp dirs.
"""
import os
import socket
import subprocess
import sys
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
        proc = subprocess.Popen(
            [sys.executable, str(SHOW_SERVER), str(port), ips, kind,
             locator, ttl],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        self.addCleanup(self._kill, proc)
        return proc, port

    @staticmethod
    def _kill(proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    def _serve(self, **kw):
        proc, port = self._spawn(**kw)
        health = "http://127.0.0.1:%d/healthz" % port
        end = time.monotonic() + 20
        while time.monotonic() < end:
            if proc.poll() is not None:
                self.fail("show server exited rc=%s: %s"
                          % (proc.returncode, proc.communicate(timeout=5)[1]))
            try:
                with urllib.request.urlopen(health, timeout=2) as r:
                    if r.status in (200, 204):
                        return proc, port
            except OSError:
                time.sleep(0.1)
        self.fail("show server never came up on %d" % port)

    @staticmethod
    def _get(url, timeout=10):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)


class TestShowServerNamedSource(_ShowServerCase):
    def _store(self, name="DB_PASS", val=VAL):
        st.store_value(name, val.encode(), keep_s=600)

    def test_a_named_value_is_shown_once_then_the_endpoint_tears_down(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        code, body, _ = self._get(url)
        self.assertEqual(code, 200)
        self.assertIn(VAL.encode(), body)          # the value is in the page
        proc.wait(timeout=15)                       # one-shot: it ends itself
        with self.assertRaises(OSError):            # nothing listening now
            self._get(url)
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
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        code, body, _ = self._get(url)
        self.assertEqual(code, 200)
        self.assertIn(VAL.encode(), body)

    def test_a_no_store_header_is_present(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        _, _, headers = self._get(url)
        cc = {k.lower(): v for k, v in headers.items()}.get("cache-control", "")
        self.assertIn("no-store", cc)

    def test_a_wrong_token_is_404_and_does_not_tear_down(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        base = "http://127.0.0.1:%d/" % port
        for bad in (base, base + "nope/", base + "favicon.ico"):
            code, _, _ = self._get(bad)
            self.assertEqual(code, 404, bad)
        self.assertIsNone(proc.poll())              # a 404 must not tear it down

    def test_the_value_never_reaches_the_endpoints_own_output_or_log(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        self._get(url)
        out, err = proc.communicate(timeout=15)
        blob = out + err
        self.assertNotIn(VAL, blob)
        self.assertNotIn(TOK, blob)                 # no HTTP request logging
        self.assertNotIn(VAL, st.log_path().read_text(encoding="utf-8"))
        # ...but the delivery log DID record the show event, value-free.
        self.assertIn("shown", st.log_path().read_text(encoding="utf-8"))

    def test_the_page_is_self_contained_no_favicon_route(self):
        self._store()
        proc, port = self._serve(kind="name", locator="DB_PASS")
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        _, body, _ = self._get(url)
        page = body.decode("utf-8")
        self.assertNotIn("{{", page)                # the repo's brace trap
        self.assertIn("rel=icon", page)             # inline data icon, no /favicon.ico

    def test_the_endpoint_self_expires(self):
        self._store()
        proc, _port = self._spawn(kind="name", locator="DB_PASS", ttl="1")
        proc.wait(timeout=30)
        self.assertEqual(proc.returncode, 0)


class TestShowServerFileSource(_ShowServerCase):
    def test_a_file_value_is_shown_once(self):
        p = self._secret_file(mode=0o600)
        proc, port = self._serve(kind="file", locator=str(p))
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        code, body, _ = self._get(url)
        self.assertEqual(code, 200)
        self.assertIn(VAL.encode(), body)
        proc.wait(timeout=15)

    def test_a_public_bind_is_refused_before_binding(self):
        p = self._secret_file(mode=0o600)
        proc, _port = self._spawn(kind="file", locator=str(p), ips="8.8.8.8")
        proc.wait(timeout=20)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("private", (proc.communicate(timeout=5)[1] or "").lower())

    def test_a_bad_file_is_refused_before_binding(self):
        bad = self._secret_file(name="world_readable", mode=0o644)
        proc, _port = self._spawn(kind="file", locator=str(bad))
        proc.wait(timeout=20)
        self.assertNotEqual(proc.returncode, 0)

    def test_the_server_rechecks_private_independently_of_filedrop(self):
        src = SHOW_SERVER.read_text(encoding="utf-8")
        self.assertNotIn("from filedrop import bind_ips", src)
        self.assertIn("def is_private", src)


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


if __name__ == "__main__":
    main()
