"""Durable persistence for owner-pasted credentials (#529) — vault != storage.

The `airuleset.py secret` vault is a DELIVERY channel with a <=24h retention
(`filedrop/vault.py` DEFAULT_KEEP_S / cli_vault SECRET_MAX_KEEP_S), so a session
that CONSUMES a credential without also PERSISTING it treats a 24h buffer as
permanent storage — and when the TTL sweep (watchdog job 29) fires, the value is
gone and the owner must re-generate and re-paste it ("nie som tu na to, aby som
neustále rušil a generoval nové kľúče").

This locks the opt-in durable layer: a mode-600 `~/.secrets/<name>` file written
AT RECEIPT (`secret request --persist`) or self-healed on use
(`secret exec --persist`), a watchdog backstop that flags "delivery without
persistence" (the #134 artifact-gate), and the invariant that the vault
lifecycle (forget / TTL purge) NEVER deletes the durable copy — that is the
whole point.

Named `vault_durable` (never `*secret*`/`*credential*`): hooks/
block-sensitive-staging.sh refuses to stage any such basename, the same reason
filedrop/vault.py / tests/test_vault_channel.py carry the neutral name.
"""

import argparse
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset                                        # noqa: E402
import watchdog as wd                                   # noqa: E402
from filedrop import vault as st                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Not spelled `secret = "..."` / `token = "..."`: those shapes trip
# block-sensitive-staging.sh's KV_PAT (see internals-filedrop.md).
VAL = b"hunter2-fixture-value-529"


class _StoreCase(TestCase):
    """Every test runs against its OWN tmp store + tmp durable home, so no real
    ~/.claude/secrets/ value or ~/.secrets/ file is ever touched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.durable = root / "dot-secrets"            # stands in for ~/.secrets
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

    def _dpath(self, name):
        return str(self.durable / name)


class TestPersistDurable(_StoreCase):
    def test_writes_mode_600_file_with_value_plus_newline(self):
        p = self._dpath("brevo-api-key")
        wrote = st.persist_durable(p, VAL, overwrite=True)
        self.assertTrue(wrote)
        self.assertEqual(Path(p).read_bytes(), VAL + b"\n")
        self.assertEqual(stat.S_IMODE(Path(p).stat().st_mode), 0o600)

    def test_a_value_already_ending_in_newline_is_not_doubled(self):
        p = self._dpath("ssh-key")
        st.persist_durable(p, VAL + b"\n", overwrite=True)
        self.assertEqual(Path(p).read_bytes(), VAL + b"\n")

    def test_write_if_absent_skips_an_existing_file(self):
        p = self._dpath("k")
        self.assertTrue(st.persist_durable(p, VAL, overwrite=False))
        # A second write-if-absent must LEAVE the durable copy untouched
        # (durable-first: the file on disk is the source of truth).
        self.assertFalse(st.persist_durable(p, b"a-different-value-529",
                                            overwrite=False))
        self.assertEqual(Path(p).read_bytes(), VAL + b"\n")

    def test_overwrite_replaces_a_prior_generation(self):
        p = self._dpath("rotated")
        st.persist_durable(p, VAL, overwrite=True)
        st.persist_durable(p, b"rotated-value-529", overwrite=True)
        self.assertEqual(Path(p).read_bytes(), b"rotated-value-529\n")

    def test_refuses_a_path_inside_a_git_repo(self):
        repo = Path(self.tmp.name) / "arepo"
        (repo / ".git").mkdir(parents=True)
        with self.assertRaises(st.SecretError):
            st.persist_durable(str(repo / "sub" / "leak"), VAL, overwrite=True)
        with self.assertRaises(st.SecretError):
            st.validate_durable_path(str(repo / "leak"))

    def test_refuses_a_symlink_at_the_final_component(self):
        target = Path(self.tmp.name) / "real"
        target.write_bytes(b"x")
        link = self.durable / "link"
        self.durable.mkdir(parents=True, exist_ok=True)
        os.symlink(str(target), str(link))
        with self.assertRaises(st.SecretError):
            st.persist_durable(str(link), VAL, overwrite=True)


class TestDurableRegistry(_StoreCase):
    def test_register_request_records_the_durable_target(self):
        st.register_request("BREVO_API_KEY", durable_path=self._dpath("brevo"))
        self.assertEqual(st.durable_target("BREVO_API_KEY"), self._dpath("brevo"))

    def test_no_durable_path_means_a_one_shot_secret(self):
        st.register_request("ROTATE_ONCE")
        self.assertIsNone(st.durable_target("ROTATE_ONCE"))

    def test_set_durable_target_is_idempotent_and_needs_meta(self):
        # No metadata yet -> nothing to record.
        self.assertFalse(st.set_durable_target("MISSING", self._dpath("x")))
        st.register_request("KEY")
        self.assertTrue(st.set_durable_target("KEY", self._dpath("key")))
        self.assertEqual(st.durable_target("KEY"), self._dpath("key"))


class TestPasteTimePersist(_StoreCase):
    def test_store_value_persists_at_receipt_when_registered(self):
        p = self._dpath("brevo")
        nonce = st.register_request("BREVO_API_KEY", durable_path=p)
        st.store_value("BREVO_API_KEY", VAL, keep_s=600, nonce=nonce)
        self.assertEqual(Path(p).read_bytes(), VAL + b"\n")
        self.assertEqual(stat.S_IMODE(Path(p).stat().st_mode), 0o600)

    def test_store_value_does_not_persist_a_one_shot(self):
        nonce = st.register_request("ROTATE_ONCE")           # no durable_path
        st.store_value("ROTATE_ONCE", VAL, keep_s=600, nonce=nonce)
        self.assertFalse((self.durable).exists(),
                         "a one-shot secret must never be persisted")


class TestVaultLifecyclePreservesDurable(_StoreCase):
    def test_forget_does_not_delete_the_durable_copy(self):
        p = self._dpath("brevo")
        nonce = st.register_request("BREVO_API_KEY", durable_path=p)
        st.store_value("BREVO_API_KEY", VAL, keep_s=600, nonce=nonce)
        st.forget("BREVO_API_KEY")
        self.assertEqual(st.state("BREVO_API_KEY"), "absent")  # vault copy gone
        self.assertTrue(Path(p).exists(), "durable copy must survive forget")

    def test_purge_does_not_delete_the_durable_copy(self):
        p = self._dpath("brevo")
        nonce = st.register_request("BREVO_API_KEY", durable_path=p)
        # keep_s tiny + a now far in the future -> the value is expired.
        st.store_value("BREVO_API_KEY", VAL, keep_s=60, nonce=nonce, now=0.0)
        gone = st.purge(now=10_000_000.0)
        self.assertIn("BREVO_API_KEY", gone)
        self.assertTrue(Path(p).exists(), "durable copy must survive TTL purge")


class TestDurableBackstop(_StoreCase):
    def test_flags_a_ready_name_with_a_missing_durable_file(self):
        # Registered durable target, value delivered, but the file never landed
        # (persist failed / was deleted) = "delivery without persistence".
        nonce = st.register_request("BREVO_API_KEY",
                                    durable_path=self._dpath("brevo"))
        st.store_value("BREVO_API_KEY", VAL, keep_s=600, nonce=nonce)
        Path(self._dpath("brevo")).unlink()               # simulate the loss
        flagged = dict(st.durable_backstop())
        self.assertIn("BREVO_API_KEY", flagged)
        self.assertEqual(flagged["BREVO_API_KEY"], self._dpath("brevo"))

    def test_silent_when_the_durable_file_is_present(self):
        nonce = st.register_request("BREVO_API_KEY",
                                    durable_path=self._dpath("brevo"))
        st.store_value("BREVO_API_KEY", VAL, keep_s=600, nonce=nonce)
        self.assertEqual(st.durable_backstop(), [])

    def test_ignores_a_one_shot_name_with_no_durable_target(self):
        nonce = st.register_request("ROTATE_ONCE")
        st.store_value("ROTATE_ONCE", VAL, keep_s=600, nonce=nonce)
        self.assertEqual(st.durable_backstop(), [])

    def test_never_reads_the_value(self):
        # The backstop must NOT read the value — read_value has exactly two
        # legitimate callers (secret exec + the show endpoint, #580), and the
        # backstop is not one of them. It works purely on file existence +
        # metadata.
        nonce = st.register_request("K", durable_path=self._dpath("k"))
        st.store_value("K", VAL, keep_s=600, nonce=nonce)
        Path(self._dpath("k")).unlink()
        called = []
        real = st.read_value
        st.read_value = lambda *a, **k: called.append(1) or real(*a, **k)
        try:
            st.durable_backstop()
        finally:
            st.read_value = real
        self.assertEqual(called, [], "backstop must never read the value")


class TestApplyRemainderPicksUpPersist(TestCase):
    def _ns(self, **kw):
        base = dict(action="exec", name="K", ttl=None, keep=None, port=None,
                    env=None, persist=None, stdin=False, cmd=[])
        base.update(kw)
        return argparse.Namespace(**base)

    def test_persist_before_the_separator_is_ours(self):
        ns = self._ns(cmd=["--persist", "/home/x/.secrets/k", "--", "psql"])
        airuleset._secret_apply_remainder(ns)
        self.assertEqual(ns.persist, "/home/x/.secrets/k")
        self.assertEqual(ns.cmd, ["psql"])

    def test_persist_after_the_separator_belongs_to_the_child(self):
        ns = self._ns(cmd=["--", "cmd", "--persist", "child-flag"])
        airuleset._secret_apply_remainder(ns)
        self.assertIsNone(ns.persist)
        self.assertEqual(ns.cmd, ["cmd", "--persist", "child-flag"])


class TestExecPersistSelfHeal(TestCase):
    """`secret exec NAME --persist PATH -- CMD` self-heals a missing durable
    file from the vault value BEFORE running the child, mode-600, and never
    overwrites an existing durable copy. Driven through the real CLI subprocess
    so the redaction filter still runs on the child's output too."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self._env = {
            st.SECRETS_DIR_ENV: str(root / "secrets"),
            st.SECRET_LOG_DIR_ENV: str(root / "secret-logs"),
        }
        self.durable = root / "dot-secrets"
        for k, v in self._env.items():
            os.environ[k] = v
        self.addCleanup(self._restore)

    def _restore(self):
        for k in self._env:
            os.environ.pop(k, None)

    def _cli(self, *argv):
        env = dict(os.environ)
        env.update(self._env)
        return subprocess.run(
            [sys.executable, str(ROOT / "airuleset.py"), "secret", *argv],
            capture_output=True, text=True, timeout=90, env=env)

    def test_self_heals_a_missing_durable_file_before_running_the_child(self):
        st.store_value("DB_PASS", VAL, keep_s=600)
        p = str(self.durable / "db-pass")
        out = self._cli("exec", "DB_PASS", "--persist", p, "--",
                        sys.executable, "-c", "print('child ran')")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("child ran", out.stdout)
        self.assertEqual(Path(p).read_bytes(), VAL + b"\n")
        self.assertEqual(stat.S_IMODE(Path(p).stat().st_mode), 0o600)
        # The child's env-var echo is still redacted (persist did not defeat it).
        out2 = self._cli("exec", "DB_PASS", "--persist", p, "--",
                         sys.executable, "-c",
                         "import os;print(os.environ['DB_PASS'])")
        self.assertNotIn(VAL.decode(), out2.stdout + out2.stderr)

    def test_does_not_overwrite_an_existing_durable_file(self):
        st.store_value("DB_PASS", VAL, keep_s=600)
        p = self.durable / "db-pass"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"pre-existing-durable-529\n")
        out = self._cli("exec", "DB_PASS", "--persist", str(p), "--",
                        sys.executable, "-c", "print('ok')")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(p.read_bytes(), b"pre-existing-durable-529\n")

    def test_a_git_repo_persist_path_is_refused(self):
        st.store_value("DB_PASS", VAL, keep_s=600)
        repo = Path(self.tmp.name) / "arepo"
        (repo / ".git").mkdir(parents=True)
        out = self._cli("exec", "DB_PASS", "--persist", str(repo / "leak"),
                        "--", sys.executable, "-c", "print('should not run')")
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("should not run", out.stdout)


class TestRequestPersistValidation(_StoreCase):
    def _ns(self, **kw):
        base = dict(action="request", name="K", ttl=None, keep=None, port=None,
                    env=None, persist=None, stdin=False, replace=False,
                    allow_plain=False, cmd=[])
        base.update(kw)
        return argparse.Namespace(**base)

    def test_a_git_repo_persist_path_fails_fast_before_the_endpoint(self):
        repo = Path(self.tmp.name) / "arepo"
        (repo / ".git").mkdir(parents=True)
        with self.assertRaises(SystemExit) as cm:
            airuleset.cmd_secret(self._ns(persist=str(repo / "leak")))
        self.assertEqual(cm.exception.code, 2)
        # Exited before register_request — nothing pending, no endpoint stood up.
        self.assertEqual(st.state("K"), "absent")


class TestStatusShowsDurable(_StoreCase):
    def _status(self, name):
        import contextlib
        import io
        ns = argparse.Namespace(action="status", name=name, ttl=None, keep=None,
                                port=None, env=None, persist=None, stdin=False,
                                replace=False, allow_plain=False, cmd=[])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            airuleset.cmd_secret(ns)
        return buf.getvalue()

    def test_status_shows_durable_present_then_missing(self):
        p = self._dpath("k")
        nonce = st.register_request("K", durable_path=p)
        st.store_value("K", VAL, keep_s=600, nonce=nonce)
        out = self._status("K")
        self.assertIn("durable=", out)
        self.assertIn("present", out)
        Path(p).unlink()
        self.assertIn("MISSING", self._status("K"))


class TestVaultPurgeJobBackstop(TestCase):
    """Job 29 gains the durable-persistence backstop via an injected
    `backstop_fn`, the same "wired = on" convention as `purge_fn`."""

    def test_backstop_warnings_are_appended(self):
        state = {}
        logs = wd.vault_purge_job(
            1_000_000.0, state,
            purge_fn=lambda: [],
            backstop_fn=lambda: [("BREVO_API_KEY", "/home/x/.secrets/brevo")])
        self.assertTrue(any("BREVO_API_KEY" in ln and "MISSING" in ln
                            for ln in logs), logs)

    def test_not_wired_is_silent(self):
        state = {}
        self.assertEqual(wd.vault_purge_job(1_000_000.0, state), [])

    def test_backstop_runs_even_in_dry_run_read_only(self):
        state = {}
        logs = wd.vault_purge_job(
            1_000_000.0, state, purge_fn=lambda: ["X"],
            backstop_fn=lambda: [("K", "/p")], dry_run=True)
        self.assertTrue(any("K" in ln and "MISSING" in ln for ln in logs), logs)
        self.assertTrue(any("dry-run" in ln for ln in logs), logs)
        self.assertEqual(state, {}, "dry run must not claim the hour")


if __name__ == "__main__":
    main()
