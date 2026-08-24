"""Owner SSH public-key provisioning onto every managed target (#653).

Owner directive (2026-08-24, spinbike session): "airuleset by mal byt
informovany ze tam musi byt moj windows laptop kluc aby som nemusel zadavat
heslo." The owner was locked out of the new key-only spinbike-vps from his
Windows laptop because nothing in provisioning ensures his CURRENT laptop key
(`zbynek-windows`) lands on a new target — it reached subdev/gk only by hand.

`cli_owner_keys.provision_owner_keys()` closes that gap: it appends the
maintained OWNER_PUBKEYS set (public material, safe to commit) into the
account's `~/.ssh/authorized_keys`, idempotently, keyed on the base64 key
BLOB — folded into `cmd_install`, so the deploy loop's single existing
`git pull && install` connection provisions every target (and the local box)
with ZERO extra ssh rounds (the #358 subdev MaxStartups-pressure lesson).

HARD INVARIANTS asserted here (the whole point of this ticket):
  * PUBLIC keys only; the dead `newlevel@newlevel-baking-ai-nb` RSA is NEVER
    propagated (excluded from OWNER_PUBKEYS).
  * Idempotent APPEND keyed on the blob — a re-run, or a differently-commented
    existing copy, is a no-op.
  * NEVER truncates, NEVER removes ANY existing line — so the current ssh
    connection's own key is structurally safe and a VPS root lockout is
    impossible (append-only). The root path's generated shell script contains
    no truncation/removal operators AT ALL.
  * Root provisioning is best-effort, gated on passwordless sudo (never
    prompts), keys piped via stdin (never argv), never fails install.
"""
import os
import stat
import subprocess
import sys
import unittest.mock as m
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_owner_keys  # noqa: E402
import airuleset  # noqa: E402


# The two canonical owner public keys, read from dev1's own authorized_keys
# (hand-seeded per the ticket's interim note). Public material — asserting the
# EXACT blobs is safe and is the point (a wrong/fabricated key would lock the
# owner out just as badly as no key).
WINDOWS_BLOB = "AAAAC3NzaC1lZDI1NTE5AAAAIDXysBDPzwyPUO+7hs4u0P/0Ef0kx4MEd+uenFPTjgnk"
GITHUB_BLOB = "AAAAC3NzaC1lZDI1NTE5AAAAIOU4rk5Y/gDjnXYdH02MEXQsAbWVQ8dUJMounPvIl3ND"

# An obviously-fake pre-existing "session" key — never removed by provisioning.
SESSION_KEY = "ssh-ed25519 AAAAFAKEsessionKEYblobNEVERremoved00000000000000000000 session@box"


def _fake_cp(returncode=0, stdout="", stderr=""):
    return m.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


# --------------------------------------------------------------------------
# The OWNER_PUBKEYS constant — exactly the two owner keys, no dead RSA.
# --------------------------------------------------------------------------
class TestOwnerPubkeysConstant(TestCase):
    def test_contains_both_owner_ed25519_keys(self):
        joined = "\n".join(cli_owner_keys.OWNER_PUBKEYS)
        self.assertIn(WINDOWS_BLOB, joined)
        self.assertIn(GITHUB_BLOB, joined)
        self.assertIn("zbynek-windows", joined)
        self.assertIn("zbynek-github", joined)

    def test_every_entry_is_an_ed25519_public_key_line(self):
        for line in cli_owner_keys.OWNER_PUBKEYS:
            self.assertTrue(line.startswith("ssh-ed25519 "),
                            "owner key is not an ed25519 pubkey line: %r" % line)
            # a well-formed authorized_keys line: type, blob, comment
            self.assertGreaterEqual(len(line.split()), 3, line)

    def test_dead_rsa_notebook_key_is_never_propagated(self):
        joined = "\n".join(cli_owner_keys.OWNER_PUBKEYS)
        # the ticket: the ancient newlevel@newlevel-baking-ai-nb RSA must NOT
        # ride along — a stale key for a machine that no longer exists.
        self.assertNotIn("ssh-rsa", joined)
        self.assertNotIn("baking-ai-nb", joined)

    def test_no_private_key_material_committed(self):
        joined = "\n".join(cli_owner_keys.OWNER_PUBKEYS)
        self.assertNotIn("PRIVATE KEY", joined)
        self.assertNotIn("BEGIN OPENSSH", joined)


# --------------------------------------------------------------------------
# _key_blob — the base64 blob (field 2) an authorized_keys line is keyed on.
# --------------------------------------------------------------------------
class TestKeyBlob(TestCase):
    def test_extracts_field_two(self):
        self.assertEqual(
            cli_owner_keys._key_blob("ssh-ed25519 %s zbynek-windows" % WINDOWS_BLOB),
            WINDOWS_BLOB)

    def test_none_for_malformed(self):
        self.assertIsNone(cli_owner_keys._key_blob(""))
        self.assertIsNone(cli_owner_keys._key_blob("ssh-ed25519"))
        self.assertIsNone(cli_owner_keys._key_blob("   "))


# --------------------------------------------------------------------------
# _append_missing_keys — pure-python idempotent append for the current user.
# --------------------------------------------------------------------------
class TestAppendMissingKeys(TestCase):
    def _ak(self, tmp):
        return os.path.join(str(tmp), ".ssh", "authorized_keys")

    def test_creates_file_and_dir_with_correct_perms(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ak(tmp)
            cli_owner_keys._append_missing_keys(path, cli_owner_keys.OWNER_PUBKEYS)
            self.assertTrue(os.path.exists(path))
            content = Path(path).read_text()
            self.assertIn(WINDOWS_BLOB, content)
            self.assertIn(GITHUB_BLOB, content)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)

    def test_idempotent_second_run_appends_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ak(tmp)
            cli_owner_keys._append_missing_keys(path, cli_owner_keys.OWNER_PUBKEYS)
            first = Path(path).read_text()
            res = cli_owner_keys._append_missing_keys(path, cli_owner_keys.OWNER_PUBKEYS)
            second = Path(path).read_text()
            self.assertEqual(first, second, "a second run must be a no-op")
            self.assertTrue(all(action == "present" for _k, action in res))

    def test_matches_on_blob_not_comment(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ak(tmp)
            os.makedirs(os.path.dirname(path))
            # SAME windows blob, DIFFERENT comment already present.
            Path(path).write_text("ssh-ed25519 %s some-other-comment\n" % WINDOWS_BLOB)
            cli_owner_keys._append_missing_keys(path, cli_owner_keys.OWNER_PUBKEYS)
            content = Path(path).read_text()
            # windows blob appears exactly once (not re-added under a new comment)
            self.assertEqual(content.count(WINDOWS_BLOB), 1)
            # its original comment is untouched, github key added
            self.assertIn("some-other-comment", content)
            self.assertIn(GITHUB_BLOB, content)

    def test_never_removes_existing_keys_append_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ak(tmp)
            os.makedirs(os.path.dirname(path))
            # pre-existing keys incl. the current-connection SESSION key
            Path(path).write_text(SESSION_KEY + "\n")
            cli_owner_keys._append_missing_keys(path, cli_owner_keys.OWNER_PUBKEYS)
            content = Path(path).read_text()
            # the session key is STILL there (never removed) and comes FIRST
            self.assertIn(SESSION_KEY, content)
            self.assertTrue(content.startswith(SESSION_KEY))
            # owner keys appended AFTER it
            self.assertIn(WINDOWS_BLOB, content)
            self.assertIn(GITHUB_BLOB, content)

    def test_preserves_file_without_trailing_newline(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._ak(tmp)
            os.makedirs(os.path.dirname(path))
            Path(path).write_text(SESSION_KEY)  # NO trailing newline
            cli_owner_keys._append_missing_keys(path, cli_owner_keys.OWNER_PUBKEYS)
            content = Path(path).read_text()
            # the session key line is intact on its own line (not glued to an owner key)
            self.assertIn(SESSION_KEY + "\n", content)
            self.assertNotIn(SESSION_KEY + "ssh-ed25519", content)


# --------------------------------------------------------------------------
# Root path — the generated shell script is APPEND-ONLY and runs under real sh.
# --------------------------------------------------------------------------
class TestRootAppendScript(TestCase):
    def test_script_has_no_truncation_or_removal(self):
        script = cli_owner_keys._authorized_keys_append_script("/root/.ssh")
        # append-only: no truncating redirect, no rm, no in-place edit/truncate
        self.assertNotIn(" rm ", " " + script + " ")
        self.assertNotIn("sed -i", script)
        self.assertNotIn("truncate", script)
        self.assertNotIn("tee ", script)  # tee w/o -a truncates
        # a single '>' (truncating redirect) must not appear; only '>>' allowed
        import re
        self.assertIsNone(re.search(r"(?<![>0-9])>(?![>])", script),
                          "script contains a truncating '>' redirect: %r" % script)
        self.assertIn(">>", script)
        self.assertIn("grep", script)

    def test_real_sh_appends_idempotently_and_preserves_existing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ssh_dir = os.path.join(tmp, "root", ".ssh")
            os.makedirs(ssh_dir)
            ak = os.path.join(ssh_dir, "authorized_keys")
            Path(ak).write_text(SESSION_KEY + "\n")  # pre-existing key
            script = cli_owner_keys._authorized_keys_append_script(ssh_dir)
            keys_stdin = "".join(k + "\n" for k in cli_owner_keys.OWNER_PUBKEYS)
            subprocess.run(["sh", "-c", script], input=keys_stdin, text=True,
                           check=True, capture_output=True)
            content = Path(ak).read_text()
            self.assertIn(SESSION_KEY, content)      # existing preserved
            self.assertIn(WINDOWS_BLOB, content)
            self.assertIn(GITHUB_BLOB, content)
            self.assertEqual(stat.S_IMODE(os.stat(ak).st_mode), 0o600)
            # idempotent second run
            subprocess.run(["sh", "-c", script], input=keys_stdin, text=True,
                           check=True, capture_output=True)
            content2 = Path(ak).read_text()
            self.assertEqual(content, content2)
            self.assertEqual(content2.count(WINDOWS_BLOB), 1)


# --------------------------------------------------------------------------
# provision_owner_keys — orchestration; root best-effort via mocked `run`.
# --------------------------------------------------------------------------
class TestProvisionOwnerKeys(TestCase):
    def test_user_keys_written_and_root_probed(self):
        import tempfile
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            if argv[:3] == ["sudo", "-n", "true"]:
                return _fake_cp(returncode=0)          # passwordless sudo available
            return _fake_cp(returncode=0)              # the sh -c append

        with tempfile.TemporaryDirectory() as tmp:
            user_ssh = os.path.join(tmp, ".ssh")
            res = cli_owner_keys.provision_owner_keys(
                run=fake_run, user_ssh_dir=user_ssh)
            content = Path(os.path.join(user_ssh, "authorized_keys")).read_text()
            self.assertIn(WINDOWS_BLOB, content)
            self.assertIn(GITHUB_BLOB, content)
            self.assertEqual(res["root"], "root-provisioned")

    def test_root_skipped_without_passwordless_sudo(self):
        import tempfile
        sh_calls = []

        def fake_run(argv, **kw):
            if argv[:3] == ["sudo", "-n", "true"]:
                return _fake_cp(returncode=1)          # NO passwordless sudo
            if argv[:3] == ["sudo", "-n", "sh"]:
                sh_calls.append(argv)
            return _fake_cp(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            res = cli_owner_keys.provision_owner_keys(
                run=fake_run, user_ssh_dir=os.path.join(tmp, ".ssh"))
            self.assertEqual(res["root"], "no-passwordless-sudo")
            self.assertEqual(sh_calls, [], "must not attempt root append without sudo")

    def test_root_keys_piped_via_stdin_never_argv(self):
        import tempfile
        captured = {}

        def fake_run(argv, **kw):
            if argv[:3] == ["sudo", "-n", "true"]:
                return _fake_cp(returncode=0)
            if argv[:3] == ["sudo", "-n", "sh"]:
                captured["argv"] = argv
                captured["input"] = kw.get("input")
            return _fake_cp(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            cli_owner_keys.provision_owner_keys(
                run=fake_run, user_ssh_dir=os.path.join(tmp, ".ssh"))
            # the key material travels via stdin, NEVER in argv
            joined_argv = " ".join(captured["argv"])
            self.assertNotIn(WINDOWS_BLOB, joined_argv)
            self.assertNotIn(GITHUB_BLOB, joined_argv)
            self.assertIn(WINDOWS_BLOB, captured["input"])
            self.assertIn(GITHUB_BLOB, captured["input"])

    def test_provision_root_false_skips_root_entirely(self):
        import tempfile
        ran_sudo = []

        def fake_run(argv, **kw):
            ran_sudo.append(argv)
            return _fake_cp(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            res = cli_owner_keys.provision_owner_keys(
                run=fake_run, user_ssh_dir=os.path.join(tmp, ".ssh"),
                provision_root=False)
            self.assertEqual(res["root"], "skipped")
            self.assertEqual(ran_sudo, [], "provision_root=False must never touch sudo")


# --------------------------------------------------------------------------
# Wiring — facade re-export + cmd_install actually calls it.
# --------------------------------------------------------------------------
class TestWiring(TestCase):
    def test_facade_reexports(self):
        self.assertIs(airuleset.provision_owner_keys,
                      cli_owner_keys.provision_owner_keys)
        self.assertEqual(airuleset.OWNER_PUBKEYS, cli_owner_keys.OWNER_PUBKEYS)

    def test_cmd_install_invokes_provision_owner_keys(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("provision_owner_keys", src,
                      "cmd_install must provision owner keys on every install")


if __name__ == "__main__":
    main()
