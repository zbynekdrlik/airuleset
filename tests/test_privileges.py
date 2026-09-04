"""Behaviour tests for the #870 F0 privilege inventory (`cli_privileges`).

Covers the four surfaces the ticket requires of `airuleset.py privileges`:
  * the DECLARED registry — non-empty, and every distinct ssh identity path
    ``cli_fleet.REMOTE_HOSTS`` actually uses is a declared ssh-key
    ``local_path`` (the drift-lock: a new REMOTE_HOSTS identity that no
    registry entry covers fails here);
  * the live PROBE against a tmp-HOME fixture (declared present + declared
    absent + undeclared extras + a wrong-mode file) → correct findings and
    exit codes;
  * a VALUE-LEAK lock — the probe output (table AND json) never contains a
    fixture token's value, only ``len=`` / a fingerprint;
  * the ``--json`` schema.

Every test builds an isolated tmp HOME — no real ~/.secrets or ~/.ssh is ever
read, and the only subprocess is ``ssh-keygen`` against fixture keys.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_privileges as p              # noqa: E402
import cli_fleet                        # noqa: E402
import airuleset                        # noqa: E402


def _mk(path: Path, content: str = "x", mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.chmod(path, mode)


def _gen_ed25519(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", str(path)],
        check=True, capture_output=True,
    )
    os.chmod(path, 0o600)


class TestRegistry(unittest.TestCase):
    def test_registry_non_empty(self):
        self.assertTrue(len(p.PRIVILEGES) >= 17)

    def test_every_entry_has_citations(self):
        for e in p.PRIVILEGES:
            self.assertTrue(e.used_by, "entry %r has no used_by citation" % e.name)
            self.assertTrue(e.name and e.kind and e.reach and e.rotation)

    def test_kinds_are_known(self):
        known = {p.KIND_SSH_KEY, p.KIND_API_TOKEN, p.KIND_OAUTH,
                 p.KIND_HOP, p.KIND_SUDO, p.KIND_PASSWORD, p.KIND_STORE}
        for e in p.PRIVILEGES:
            self.assertIn(e.kind, known, "entry %r has unknown kind" % e.name)

    def test_facade_reexport(self):
        self.assertIs(airuleset.cmd_privileges, p.cmd_privileges)
        self.assertIn("privileges", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.PRIVILEGES, p.PRIVILEGES)

    def test_new_entries_exist(self):
        names = {e.name for e in p.PRIVILEGES}
        for expected in ("vault_store", "fleet_shared_password",
                         "sudo_nopasswd", "gh_cli_token", "gh_app_token",
                         "soniox_source", "soniox_fanout",
                         "hetzner_airuleset", "tailscale_api_key",
                         "cloudflared_tunnel_creds", "cloudflared_config"):
            self.assertIn(expected, names,
                          "missing registry entry: %s" % expected)

    def test_vault_store_is_directory_kind(self):
        by = {e.name: e for e in p.PRIVILEGES}
        self.assertEqual(by["vault_store"].kind, p.KIND_STORE)

    def test_fleet_shared_password_kind(self):
        by = {e.name: e for e in p.PRIVILEGES}
        self.assertEqual(by["fleet_shared_password"].kind, p.KIND_PASSWORD)


class TestRemoteHostsSymmetry(unittest.TestCase):
    """The drift-lock: a new REMOTE_HOSTS ssh identity that is not declared in
    the registry must fail this test (the #870 completeness guarantee)."""

    def test_every_fleet_identity_is_declared(self):
        declared_ssh = {e.local_path for e in p.PRIVILEGES
                        if e.kind == p.KIND_SSH_KEY}
        for ident in p.fleet_identity_paths():
            if ident == "":
                self.assertIn("~/.ssh/id_ed25519", declared_ssh,
                              "default-key hosts have no registry entry")
                continue
            self.assertIn(
                ident, declared_ssh,
                "REMOTE_HOSTS identity %r is not declared in PRIVILEGES" % ident)

    def test_fleet_identity_paths_matches_remote_hosts(self):
        expect = {(h.get("identity", "") or "") for h in cli_fleet.REMOTE_HOSTS}
        self.assertEqual(p.fleet_identity_paths(), expect)

    def test_fleet_hosts_derived_live_from_cli_fleet(self):
        by = {e["name"]: e for e in p.build_report()["entries"]}
        self.assertIn("gatekeeper", by["gatekeeper_access_ed25519"]["fleet_hosts"])
        self.assertIn("dev2", by["default_key_id_ed25519"]["fleet_hosts"])
        self.assertEqual(by["webterm_david_ed25519"]["fleet_hosts"], [])


class _FakeArgs:
    def __init__(self, json=False):
        self.json = json


class TestProbe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / ".secrets").mkdir()
        (self.home / ".ssh").mkdir()

    def _seed_declared(self):
        _gen_ed25519(self.home / ".secrets" / "gatekeeper_access_ed25519")
        _mk(self.home / ".secrets" / "cloudflare-newlevel-access",
            "CF_SECRET_VALUE_ABCDEF", 0o600)

    def test_declared_present_reports_fingerprint_and_len(self):
        self._seed_declared()
        rep = p.build_report(home=self.home)
        by = {e["name"]: e for e in rep["entries"]}
        gk = by["gatekeeper_access_ed25519"]
        self.assertTrue(gk["present"])
        self.assertIn("fp=SHA256:", gk["detail"])
        cf = by["cloudflare-newlevel-access"]
        self.assertTrue(cf["present"])
        self.assertEqual(cf["detail"], "len=%d" % len("CF_SECRET_VALUE_ABCDEF"))

    def test_absent_declared_is_not_a_finding(self):
        rep = p.build_report(home=self.home)
        self.assertEqual(rep["findings"]["undeclared_count"], 0)
        self.assertEqual(rep["findings"]["wrong_mode_count"], 0)
        self.assertEqual(rep["exit_code"], 0)
        absent = [e for e in rep["entries"]
                  if e["local_path"] and not e["present"]]
        self.assertTrue(absent)

    def test_undeclared_secret_is_a_finding_exit_1(self):
        _mk(self.home / ".secrets" / "some-foreign-token", "zzz", 0o600)
        rep = p.build_report(home=self.home)
        paths = [u["path"] for u in rep["undeclared"]]
        self.assertIn("~/.secrets/some-foreign-token", paths)
        self.assertEqual(rep["exit_code"], 1)

    def test_undeclared_ssh_private_key_is_a_finding(self):
        _gen_ed25519(self.home / ".ssh" / "some_other_key")
        rep = p.build_report(home=self.home)
        paths = [u["path"] for u in rep["undeclared"]]
        self.assertIn("~/.ssh/some_other_key", paths)

    def test_ssh_config_and_known_hosts_are_not_flagged(self):
        _mk(self.home / ".ssh" / "config", "Host x\n", 0o644)
        _mk(self.home / ".ssh" / "known_hosts", "host ssh-ed25519 AAAA\n", 0o644)
        rep = p.build_report(home=self.home)
        paths = [u["path"] for u in rep["undeclared"]]
        self.assertNotIn("~/.ssh/config", paths)
        self.assertNotIn("~/.ssh/known_hosts", paths)

    def test_declared_pub_sibling_not_flagged(self):
        _gen_ed25519(self.home / ".secrets" / "gatekeeper_access_ed25519")
        rep = p.build_report(home=self.home)
        paths = [u["path"] for u in rep["undeclared"]]
        self.assertNotIn("~/.secrets/gatekeeper_access_ed25519.pub", paths)

    def test_wrong_mode_declared_file_is_a_finding_exit_1(self):
        _mk(self.home / ".secrets" / "cloudflare-newlevel-access", "tok", 0o644)
        rep = p.build_report(home=self.home)
        self.assertIn("cloudflare-newlevel-access",
                      rep["findings"]["wrong_mode_names"])
        self.assertEqual(rep["exit_code"], 1)

    def test_correct_mode_0600_is_ok(self):
        _mk(self.home / ".secrets" / "cloudflare-newlevel-access", "tok", 0o600)
        rep = p.build_report(home=self.home)
        self.assertNotIn("cloudflare-newlevel-access",
                         rep["findings"]["wrong_mode_names"])

    def test_env_file_token_len(self):
        env = self.home / ".claude" / "channels" / "discord" / ".env"
        _mk(env, "FOO=bar\nDISCORD_BOT_TOKEN=abcdefghij\nBAZ=1\n", 0o600)
        rep = p.build_report(home=self.home)
        by = {e["name"]: e for e in rep["entries"]}
        self.assertEqual(by["discord_bot_token"]["detail"],
                         "len=%d" % len("abcdefghij"))

    def test_hop_entry_has_no_file(self):
        rep = p.build_report(home=self.home)
        by = {e["name"]: e for e in rep["entries"]}
        hop = by["root@subdev"]
        self.assertEqual(hop["kind"], p.KIND_HOP)
        self.assertTrue(hop["present"])
        self.assertIsNone(hop["mode"])
        self.assertFalse(hop["wrong_mode"])


class TestProbeHardening(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / ".secrets").mkdir()
        (self.home / ".ssh").mkdir()

    def test_symlink_in_secrets_is_undeclared(self):
        link = self.home / ".secrets" / "dangling-link"
        os.symlink("/nonexistent/target", link)
        rep = p.build_report(home=self.home)
        paths = [u["path"] for u in rep["undeclared"]]
        self.assertIn("~/.secrets/dangling-link", paths)

    def test_symlink_declared_entry_flagged(self):
        real = self.home / ".secrets" / "real"
        real.write_text("x")
        os.chmod(real, 0o600)
        path = self.home / ".secrets" / "cloudflare-newlevel-access"
        os.symlink(str(real), path)
        rep = p.build_report(home=self.home)
        by = {e["name"]: e for e in rep["entries"]}
        entry = by["cloudflare-newlevel-access"]
        self.assertTrue(entry["wrong_mode"])
        self.assertIn("SYMLINK", entry["detail"])

    def test_oserror_does_not_crash(self):
        bad_dir = self.home / ".secrets" / "unreadable-subdir"
        bad_dir.mkdir(mode=0o000)
        self.addCleanup(lambda: os.chmod(bad_dir, 0o700))
        rep = p.build_report(home=self.home)
        self.assertIn(rep["exit_code"], (0, 1))

    def test_vault_dir_contents_not_flagged(self):
        """Vault contents are legitimate transient values, NOT undeclared
        credentials (#870 review-2 🟡3). Only the dir's MODE is probed."""
        vault = self.home / ".claude" / "secrets"
        vault.mkdir(parents=True)
        _mk(vault / "some-vault-secret", "vaultval", 0o600)
        rep = p.build_report(home=self.home)
        paths = [u["path"] for u in rep["undeclared"]]
        self.assertFalse(any("some-vault-secret" in item for item in paths))

    def test_secrets_subdir_recursed(self):
        sub = self.home / ".secrets" / "subdir"
        sub.mkdir()
        _mk(sub / "nested-token", "nest", 0o600)
        rep = p.build_report(home=self.home)
        paths = [u["path"] for u in rep["undeclared"]]
        self.assertTrue(any("nested-token" in item for item in paths))

    def test_pem_key_detected_in_ssh(self):
        pem = self.home / ".ssh" / "legacy.pem"
        pem.write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nfake\n"
            "-----END RSA PRIVATE KEY-----\n")
        os.chmod(pem, 0o600)
        rep = p.build_report(home=self.home)
        paths = [u["path"] for u in rep["undeclared"]]
        self.assertIn("~/.ssh/legacy.pem", paths)


class TestMemoryCredScan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / ".secrets").mkdir()
        (self.home / ".ssh").mkdir()

    def test_memory_scan_finds_planted_token(self):
        mem_dir = self.home / ".claude" / "projects" / "-test" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "ref_key.md").write_text(
            "---\nname: key\n---\nThe key is tskey-api-FAKEVALUE123\n")
        findings = p.scan_memory_credentials(self.home)
        self.assertTrue(len(findings) >= 1)
        self.assertEqual(findings[0]["pattern"], "tailscale-api-key")
        self.assertNotIn("FAKEVALUE123", str(findings[0]))

    def test_memory_scan_no_false_positive(self):
        mem_dir = self.home / ".claude" / "projects" / "-test" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "safe.md").write_text("No tokens here.\n")
        findings = p.scan_memory_credentials(self.home)
        self.assertEqual(findings, [])

    def test_sk_prefix_no_false_positive_on_prose(self):
        mem_dir = self.home / ".claude" / "projects" / "-test" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "prose.md").write_text(
            "task-runner ask-before disk-guard\n")
        findings = p.scan_memory_credentials(self.home)
        self.assertEqual(findings, [])

    def test_memory_scan_multiple_patterns(self):
        mem_dir = self.home / ".claude" / "projects" / "-test" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "multi.md").write_text(
            "ghp_abcdef1234567890abcdef12 and cfat_xyzxyzxyzxyzxyzxyzxyz12\n")
        findings = p.scan_memory_credentials(self.home)
        patterns_found = {f["pattern"] for f in findings}
        self.assertIn("github-pat", patterns_found)
        self.assertIn("cloudflare-account-token", patterns_found)

    def test_build_report_includes_memory(self):
        mem_dir = self.home / ".claude" / "projects" / "-test" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "leak.md").write_text("tskey-api-LEAKED12345\n")
        rep = p.build_report(home=self.home)
        self.assertIn("memory_credentials", rep)
        self.assertTrue(len(rep["memory_credentials"]) >= 1)
        self.assertEqual(rep["exit_code"], 1)


class TestSudoProbe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / ".secrets").mkdir()
        (self.home / ".ssh").mkdir()

    def test_sudo_entry_probed(self):
        rep = p.build_report(home=self.home)
        by = {e["name"]: e for e in rep["entries"]}
        self.assertIn("sudo_nopasswd", by)
        self.assertIn(by["sudo_nopasswd"]["detail"],
                      ("sudo available", "sudo unavailable"))


class TestHermeticity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / ".secrets").mkdir()
        (self.home / ".ssh").mkdir()

    def test_cmd_privileges_uses_injected_home(self):
        args = _FakeArgs(json=True)
        with mock.patch.object(Path, "home", return_value=self.home):
            with self.assertRaises(SystemExit) as cm:
                p.cmd_privileges(args)
        self.assertEqual(cm.exception.code, 0)


class TestValueLeakLock(unittest.TestCase):
    SECRET = "TOTALLY-SECRET-TOKEN-VALUE-9f8e7d6c5b4a"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        _mk(self.home / ".secrets" / "cloudflare-newlevel-access",
            self.SECRET, 0o600)
        _mk(self.home / ".secrets" / "cloudflare-account-tokens",
            self.SECRET + "X", 0o600)
        env = self.home / ".claude" / "channels" / "discord" / ".env"
        _mk(env, "DISCORD_BOT_TOKEN=" + self.SECRET + "\n", 0o600)

    def test_value_never_in_table_or_json(self):
        rep = p.build_report(home=self.home)
        table = p.render_table(rep)
        blob = json.dumps(rep, ensure_ascii=False)
        self.assertNotIn(self.SECRET, table)
        self.assertNotIn(self.SECRET, blob)
        self.assertIn("len=%d" % len(self.SECRET), table)


class TestJsonSchema(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / ".secrets").mkdir()

    def test_json_schema_shape(self):
        rep = p.build_report(home=self.home)
        for key in ("entries", "undeclared", "findings", "exit_code"):
            self.assertIn(key, rep)
        self.assertEqual(len(rep["entries"]), len(p.PRIVILEGES))
        e0 = rep["entries"][0]
        for key in ("name", "kind", "local_path", "must_move", "present",
                    "mode", "mode_ok", "owner", "detail", "wrong_mode",
                    "fleet_hosts"):
            self.assertIn(key, e0)
        for key in ("undeclared_count", "wrong_mode_count", "wrong_mode_names"):
            self.assertIn(key, rep["findings"])
        json.loads(json.dumps(rep, ensure_ascii=False))

    def test_json_includes_memory_credentials(self):
        rep = p.build_report(home=self.home)
        self.assertIn("memory_credentials", rep)

    def test_cmd_privileges_exit_code(self):
        args = _FakeArgs(json=True)
        rep = p.build_report(home=self.home)
        self.assertEqual(rep["exit_code"], 0)
        with mock.patch.object(Path, "home", return_value=self.home):
            with self.assertRaises(SystemExit) as cm:
                p.cmd_privileges(args)
        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
