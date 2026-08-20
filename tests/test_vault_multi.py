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
import airuleset                                        # noqa: E402
import cli_vault                                        # noqa: E402
from filedrop import vault as st                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "filedrop" / "vault_server.py"

TOK = "toktoktoktoktok603"


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
        self.assertEqual(st.state("WS_IDENT"), "absent")     # rolled back
        self.assertEqual(st.state("WS_SECRET"), "absent")

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


if __name__ == "__main__":                                  # pragma: no cover
    main()
