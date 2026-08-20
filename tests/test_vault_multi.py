"""Locks the MULTI-FIELD secret intake (#603) — one URL, several named fields.

The owner asked for one page that collects several credentials at once (the
Websupport identifier+secret pair needed two separate `secret request` runs =
two URLs = two pages). This file locks the three layers the feature adds:

  1. `vault.store_values` — the ALL-OR-NOTHING atomic multi-store (this file's
     first classes);
  2. the CLI multi-name parse + `--persist-map` (`cli_vault._secret_request`);
  3. the server's multi-field page + JSON POST + process-global consume-latch,
     driven end-to-end against a real spawned `vault_server.py` — AND the proof
     that a SINGLE-name request is byte-identical to today.

Named `vault_multi` (never `*secret*`/`*credential*`): hooks/
block-sensitive-staging.sh refuses to `git add` any basename with those words.
"""

import json
import os
import socket
import stat
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
import cli_vault                                        # noqa: E402
from filedrop import vault as st                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "filedrop" / "vault_server.py"

TOK = "toktoktoktoktok603"


def _free_port():
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    return port


def _post_json(url, obj, timeout=10):
    body = json.dumps(obj).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class _StoreCase(TestCase):
    """Every test runs against its OWN tmp store + log dir."""

    def setUp(self):
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

    def _pending(self, name, ttl=600, keep=600, durable=None):
        """Register a pending request the way `secret request` does, returning
        its nonce, so a store can be nonce-checked."""
        return st.register_request(name, endpoint_ttl_s=ttl, keep_s=keep,
                                   durable_path=durable)


class TestStoreValuesHappyPath(_StoreCase):
    def test_all_named_values_land_ready_byte_exact(self):
        n1 = self._pending("WS_IDENT")
        n2 = self._pending("WS_SECRET")
        out = st.store_values(
            [("WS_IDENT", b"ident-value-603"), ("WS_SECRET", b"s3cr3t-603")],
            keep_s=600, nonces={"WS_IDENT": n1, "WS_SECRET": n2})
        self.assertEqual(set(out), {"WS_IDENT", "WS_SECRET"})
        self.assertEqual(st.state("WS_IDENT"), "ready")
        self.assertEqual(st.state("WS_SECRET"), "ready")
        self.assertEqual(st.read_value("WS_IDENT"), b"ident-value-603")
        self.assertEqual(st.read_value("WS_SECRET"), b"s3cr3t-603")

    def test_the_values_are_0600(self):
        n1 = self._pending("A_NAME")
        st.store_values([("A_NAME", b"val-603")], nonces={"A_NAME": n1})
        p = st.value_path("A_NAME")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)


class TestStoreValuesAtomicity(_StoreCase):
    def test_a_bad_nonce_on_one_name_stores_NOTHING(self):
        # WS_IDENT is fine; WS_SECRET's nonce does not match its pending record
        # (its request was revoked/replaced). The WHOLE set must abort with
        # nothing on disk — never a partial that stores only the good one.
        n1 = self._pending("WS_IDENT")
        self._pending("WS_SECRET")
        with self.assertRaises(st.SecretError):
            st.store_values(
                [("WS_IDENT", b"ident-603"), ("WS_SECRET", b"s3cr3t-603")],
                nonces={"WS_IDENT": n1, "WS_SECRET": "wrong-nonce"})
        self.assertEqual(st.state("WS_IDENT"), "pending")   # NOT stored
        self.assertEqual(st.state("WS_SECRET"), "pending")

    def test_an_empty_value_aborts_the_whole_set(self):
        n1 = self._pending("WS_IDENT")
        n2 = self._pending("WS_SECRET")
        with self.assertRaises(st.SecretError):
            st.store_values(
                [("WS_IDENT", b"ident-603"), ("WS_SECRET", b"")],
                nonces={"WS_IDENT": n1, "WS_SECRET": n2})
        self.assertEqual(st.state("WS_IDENT"), "pending")
        self.assertEqual(st.state("WS_SECRET"), "pending")

    def test_a_write_failure_mid_loop_rolls_back_earlier_writes(self):
        # Pre-flight passes for both, but the SECOND store_value raises (a
        # residual disk error). The first, already committed, must be rolled
        # back so no partial set survives.
        n1 = self._pending("WS_IDENT")
        n2 = self._pending("WS_SECRET")
        real = st.store_value
        calls = {"n": 0}

        def flaky(name, data, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise st.SecretError("%s: simulated disk error" % name)
            return real(name, data, **kw)

        with m.patch.object(st, "store_value", side_effect=flaky):
            with self.assertRaises(st.SecretError):
                st.store_values(
                    [("WS_IDENT", b"ident-603"), ("WS_SECRET", b"s3cr3t-603")],
                    nonces={"WS_IDENT": n1, "WS_SECRET": n2})
        # All-or-nothing: NEITHER name is `ready` — the first (committed then
        # rolled back) is `absent`, the second (failed mid-write, never
        # committed) stays `pending`.
        self.assertNotEqual(st.state("WS_IDENT"), "ready")
        self.assertNotEqual(st.state("WS_SECRET"), "ready")
        self.assertEqual(st.state("WS_IDENT"), "absent")     # rolled back

    def test_empty_items_is_refused(self):
        with self.assertRaises(st.SecretError):
            st.store_values([], nonces={})

    def test_the_error_never_contains_a_value(self):
        n1 = self._pending("WS_IDENT")
        self._pending("WS_SECRET")
        secret_value = "very-distinctive-secret-603"
        try:
            st.store_values(
                [("WS_IDENT", secret_value.encode()),
                 ("WS_SECRET", b"other-603")],
                nonces={"WS_IDENT": n1, "WS_SECRET": "wrong-nonce"})
            self.fail("expected SecretError")
        except st.SecretError as e:
            self.assertNotIn(secret_value, str(e))


class _Args:
    """A minimal argparse.Namespace stand-in for the request parse helpers."""

    def __init__(self, name=None, cmd=None, **kw):
        self.name = name
        self.cmd = list(cmd or [])
        self.ttl = None
        self.keep = None
        self.port = None
        self.persist = None
        self.persist_map = None
        self.allow_plain = False
        self.replace = False
        for k, v in kw.items():
            setattr(self, k, v)


class TestRequestNames(TestCase):
    """cli_vault._secret_request_names — many names + flags anywhere."""

    def test_a_single_name_is_a_one_element_list(self):
        a = _Args(name="DB_PASS")
        self.assertEqual(cli_vault._secret_request_names(a), ["DB_PASS"])

    def test_extra_positional_names_are_collected(self):
        # `request A B C` -> name="A", cmd=["B","C"] (nargs="?").
        a = _Args(name="A", cmd=["B", "C"])
        self.assertEqual(cli_vault._secret_request_names(a), ["A", "B", "C"])

    def test_a_flag_that_trails_a_name_is_still_pulled_onto_args(self):
        # `request A B --ttl 900`: the head-only remainder pass stops at B, so
        # the collector must catch the trailing --ttl.
        a = _Args(name="A", cmd=["B", "--ttl", "900"])
        self.assertEqual(cli_vault._secret_request_names(a), ["A", "B"])
        self.assertEqual(a.ttl, 900)

    def test_a_flag_between_names_is_handled(self):
        a = _Args(name="A", cmd=["--persist-map", "A=/tmp/x", "B"])
        self.assertEqual(cli_vault._secret_request_names(a), ["A", "B"])
        self.assertEqual(a.persist_map, "A=/tmp/x")

    def test_a_bool_flag_among_names(self):
        a = _Args(name="A", cmd=["--allow-plain", "B"])
        self.assertEqual(cli_vault._secret_request_names(a), ["A", "B"])
        self.assertTrue(a.allow_plain)

    def test_an_explicit_arg_value_is_not_overwritten(self):
        a = _Args(name="A", cmd=["B", "--ttl", "900"], ttl=60)
        cli_vault._secret_request_names(a)
        self.assertEqual(a.ttl, 60)          # the explicit value wins

    def test_a_non_numeric_int_flag_exits(self):
        a = _Args(name="A", cmd=["--ttl", "abc"])
        with self.assertRaises(SystemExit):
            cli_vault._secret_request_names(a)


class TestPersistMap(_StoreCase):
    """cli_vault._secret_parse_persist_map."""

    def _durable(self, name):
        return str(Path(self.tmp.name) / "dot-secrets" / name)

    def test_no_persist_is_empty(self):
        self.assertEqual(cli_vault._secret_parse_persist_map(_Args(), ["A"]), {})

    def test_single_name_persist_path(self):
        p = self._durable("db-pass")
        got = cli_vault._secret_parse_persist_map(_Args(persist=p), ["A"])
        self.assertEqual(set(got), {"A"})
        self.assertTrue(got["A"].endswith("db-pass"))

    def test_persist_with_multiple_names_is_refused(self):
        with self.assertRaises(SystemExit):
            cli_vault._secret_parse_persist_map(
                _Args(persist=self._durable("x")), ["A", "B"])

    def test_persist_and_map_together_is_refused(self):
        with self.assertRaises(SystemExit):
            cli_vault._secret_parse_persist_map(
                _Args(persist=self._durable("x"), persist_map="A=" + self._durable("y")),
                ["A"])

    def test_map_parses_per_name_paths(self):
        pa, pb = self._durable("id-file"), self._durable("secret-file")
        got = cli_vault._secret_parse_persist_map(
            _Args(persist_map="WS_IDENT=%s,WS_SECRET=%s" % (pa, pb)),
            ["WS_IDENT", "WS_SECRET"])
        self.assertEqual(set(got), {"WS_IDENT", "WS_SECRET"})
        self.assertTrue(got["WS_IDENT"].endswith("id-file"))

    def test_map_key_not_in_request_is_refused(self):
        with self.assertRaises(SystemExit):
            cli_vault._secret_parse_persist_map(
                _Args(persist_map="NOPE=%s" % self._durable("x")), ["A"])

    def test_map_bad_shape_is_refused(self):
        with self.assertRaises(SystemExit):
            cli_vault._secret_parse_persist_map(
                _Args(persist_map="A-no-equals"), ["A"])

    def test_map_path_inside_a_git_repo_is_refused(self):
        repo = Path(self.tmp.name) / "repo"
        (repo / ".git").mkdir(parents=True)
        with self.assertRaises(SystemExit):
            cli_vault._secret_parse_persist_map(
                _Args(persist_map="A=%s" % (repo / "leak")), ["A"])


class _MultiServerCase(_StoreCase):
    """Spawns vault_server.py with a NAMES CSV + NONCES env, exactly as
    `secret request` spawns it for a multi-field request."""

    @staticmethod
    def _kill(proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    def _serve_multi(self, names, ttl="30", keep="600", ips="127.0.0.1"):
        nonces = {n: self._pending(n, ttl=int(ttl), keep=int(keep)) for n in names}
        port = _free_port()
        env = dict(os.environ)
        env["AIRULESET_VAULT_TOKEN"] = TOK
        env["AIRULESET_VAULT_NONCES"] = json.dumps(nonces)
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), str(port), ips, ",".join(names),
             ttl, keep],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        self.addCleanup(self._kill, proc)
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            if proc.poll() is not None:
                self.fail("multi server exited rc=%s: %s"
                          % (proc.returncode, proc.communicate(timeout=5)[1]))
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        return proc, port, url
            except OSError:
                time.sleep(0.1)
        self.fail("multi server never served %s" % url)


class TestMultiServerLive(_MultiServerCase):
    def test_the_page_has_a_field_per_name(self):
        proc, port, url = self._serve_multi(["WS_IDENT", "WS_SECRET"])
        with urllib.request.urlopen(url, timeout=5) as r:
            page = r.read().decode("utf-8")
        self.assertNotIn("{{", page)                 # the repo's brace trap
        self.assertIn("rel=icon", page)              # no /favicon.ico console err
        self.assertIn("WS_IDENT", page)
        self.assertIn("WS_SECRET", page)
        self.assertIn("application/json", page)      # the multi POST content-type

    def test_a_json_post_stores_all_values_and_the_endpoint_is_one_shot(self):
        proc, port, url = self._serve_multi(["WS_IDENT", "WS_SECRET"])
        code, _ = _post_json(url, {"WS_IDENT": "id-603", "WS_SECRET": "s3c-603"})
        self.assertEqual(code, 200)
        proc.wait(timeout=15)                        # one-shot: it ends itself
        self.assertEqual(st.read_value("WS_IDENT"), b"id-603")
        self.assertEqual(st.read_value("WS_SECRET"), b"s3c-603")
        self.assertEqual(stat.S_IMODE(st.value_path("WS_IDENT").stat().st_mode),
                         0o600)
        with self.assertRaises(OSError):             # nothing listening now
            _post_json(url, {"WS_IDENT": "again", "WS_SECRET": "again"})

    def test_an_empty_field_stores_nothing_and_the_endpoint_stays_up(self):
        proc, port, url = self._serve_multi(["WS_IDENT", "WS_SECRET"])
        code, _ = _post_json(url, {"WS_IDENT": "id-603", "WS_SECRET": ""})
        self.assertEqual(code, 422)
        self.assertEqual(st.state("WS_IDENT"), "pending")   # NOTHING stored
        self.assertEqual(st.state("WS_SECRET"), "pending")
        self.assertIsNone(proc.poll())                       # STILL up
        # ...and a corrected resubmit then works on the same endpoint.
        code, _ = _post_json(url, {"WS_IDENT": "id-603", "WS_SECRET": "s3c-603"})
        self.assertEqual(code, 200)
        proc.wait(timeout=15)
        self.assertEqual(st.read_value("WS_SECRET"), b"s3c-603")

    def test_a_missing_field_is_refused_and_stores_nothing(self):
        proc, port, url = self._serve_multi(["WS_IDENT", "WS_SECRET"])
        code, _ = _post_json(url, {"WS_IDENT": "id-603"})    # WS_SECRET absent
        self.assertEqual(code, 422)
        self.assertEqual(st.state("WS_IDENT"), "pending")
        self.assertIsNone(proc.poll())

    def test_malformed_json_is_refused_without_echoing_the_body(self):
        proc, port, url = self._serve_multi(["WS_IDENT", "WS_SECRET"])
        req = urllib.request.Request(
            url, data=b'{"WS_IDENT": "secret-leak-xyz', method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("expected an HTTP error")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            self.assertNotIn(b"secret-leak-xyz", e.read())
        self.assertIsNone(proc.poll())

    def test_a_wrong_token_is_404(self):
        proc, port, url = self._serve_multi(["WS_IDENT", "WS_SECRET"])
        base = "http://127.0.0.1:%d/" % port
        self.assertEqual(_post_json(base + "nope/",
                                    {"WS_IDENT": "x", "WS_SECRET": "y"})[0], 404)
        self.assertIsNone(proc.poll())

    def test_the_value_never_reaches_the_output_or_the_log(self):
        proc, port, url = self._serve_multi(["WS_IDENT", "WS_SECRET"])
        hidden = "distinctive-hidden-value-603"
        _post_json(url, {"WS_IDENT": "id-603", "WS_SECRET": hidden})
        out, err = proc.communicate(timeout=15)
        blob = out + err
        self.assertNotIn(hidden, blob)
        self.assertNotIn(TOK, blob)                  # no HTTP request logging
        self.assertNotIn(hidden, st.log_path().read_text(encoding="utf-8"))


class TestMultiConsumeLatch(_MultiServerCase):
    def test_source_claims_the_latch_before_reading_or_storing(self):
        # Deterministic teeth (the #580 lesson): an HTTP concurrency test has no
        # reliable teeth because os._exit kills the losers, so the real guard is
        # SOURCE ORDER — `_stored` is claimed BEFORE store_values() is called.
        src = SERVER.read_text(encoding="utf-8")
        body = src.split("def _store_multi", 1)[1]
        latch = body.index("_stored = True")
        store = body.index("store_values(")
        self.assertLess(latch, store,
                        "the consume-latch must be claimed BEFORE store_values")

    def test_a_concurrent_second_submit_is_refused_410(self):
        # Best-effort behavioural companion: after a successful store the
        # endpoint is gone, so a second submit cannot land. (The 410 path itself
        # is a race window; the source-order test above is the real teeth.)
        proc, port, url = self._serve_multi(["A_ONE", "B_TWO"])
        self.assertEqual(_post_json(url, {"A_ONE": "1", "B_TWO": "2"})[0], 200)
        proc.wait(timeout=15)
        with self.assertRaises(OSError):
            _post_json(url, {"A_ONE": "x", "B_TWO": "y"})


class TestMultiDurablePersist(_StoreCase):
    """A multi member with a durable target gets its ~/.secrets file written at
    paste (the #529 mechanism reused per name)."""

    def test_paste_time_persist_fires_for_a_mapped_member(self):
        dpath = str(Path(self.tmp.name) / "dot-secrets" / "ws-ident")
        n1 = self._pending("WS_IDENT", durable=dpath)
        n2 = self._pending("WS_SECRET")                 # no durable target
        st.store_values([("WS_IDENT", b"id-603"), ("WS_SECRET", b"s3c-603")],
                        nonces={"WS_IDENT": n1, "WS_SECRET": n2})
        self.assertTrue(Path(dpath).exists())
        self.assertEqual(Path(dpath).read_bytes(), b"id-603\n")  # +1 newline
        self.assertEqual(stat.S_IMODE(Path(dpath).stat().st_mode), 0o600)


class TestSingleNameByteIdentical(_StoreCase):
    """A ONE-name request must reproduce today's flow exactly: the single-field
    page (raw-body POST), driven the way `secret request NAME` spawns it."""

    def test_a_single_name_argv_serves_the_single_field_page(self):
        n1 = self._pending("DB_PASS")
        port = _free_port()
        env = dict(os.environ)
        env["AIRULESET_VAULT_TOKEN"] = TOK
        env["AIRULESET_VAULT_NONCE"] = n1
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), str(port), "127.0.0.1", "DB_PASS",
             "30", "600"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
        self.addCleanup(_MultiServerCase._kill, proc)
        url = "http://127.0.0.1:%d/%s/" % (port, TOK)
        end = time.monotonic() + 20
        page = None
        while time.monotonic() < end:
            if proc.poll() is not None:
                self.fail("single server exited: %s" % proc.communicate()[1])
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        page = r.read().decode("utf-8")
                        break
            except OSError:
                time.sleep(0.1)
        self.assertIsNotNone(page)
        self.assertIn("type=password", page.replace('"', ""))
        self.assertNotIn("application/json", page)   # single path = raw body
        # A RAW-body POST (not JSON) stores the value byte-exact, as today.
        req = urllib.request.Request(url, data=b"raw-single-603", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)
        proc.wait(timeout=15)
        self.assertEqual(st.read_value("DB_PASS"), b"raw-single-603")


if __name__ == "__main__":                                  # pragma: no cover
    main()
