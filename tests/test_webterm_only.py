"""Tests for cli_webterm_only.py (#869) — webterm-only SSH access management.

Hermetic: uses fake $HOME tmpdirs and run= recorder fakes — no network,
no real ssh, no real authorized_keys touched.
"""
import os
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_webterm_only  # noqa: E402
from cli_fleet import (  # noqa: E402
    REMOTE_HOSTS,
    WEBTERM_OBSERVER_USERS,
    WEBTERM_ONLY_USERS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FLEET_BLOB = cli_webterm_only._key_blob(cli_webterm_only.FLEET_PUSH_PUBKEY)
FOREIGN_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePersonalKeyBlobThatShouldBeRemoved"
    "xxxx grena@MacBook-Air.local"
)
FOREIGN_BLOB = cli_webterm_only._key_blob(FOREIGN_KEY)


def _fake_run_ok(args, **kwargs):
    """A subprocess.run fake that succeeds for ssh-keygen fingerprinting."""
    class R:
        returncode = 0
        stdout = "256 SHA256:fakefp comment (ED25519)"
        stderr = ""
    return R()


def _fake_run_fail(args, **kwargs):
    """A subprocess.run fake that fails."""
    class R:
        returncode = 1
        stdout = ""
        stderr = "error"
    return R()


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestWebtermOnlyRegistry(unittest.TestCase):
    """Registry subset + identity lock tests."""

    def test_webterm_only_users_is_frozenset(self):
        self.assertIsInstance(WEBTERM_ONLY_USERS, frozenset)

    def test_webterm_only_subset_of_subdev_remote_hosts(self):
        """Every WEBTERM_ONLY_USERS member must be a REMOTE_HOSTS user on
        the subdev VPS (100.118.174.27)."""
        subdev_users = {
            e["user"] for e in REMOTE_HOSTS
            if e.get("host") == "100.118.174.27"
        }
        for user in WEBTERM_ONLY_USERS:
            self.assertIn(user, subdev_users,
                          "%s is webterm-only but not a subdev user" % user)

    def test_webterm_only_distinct_from_observer(self):
        """The two sets overlap (dominika is in both) but are conceptually
        distinct — test the expected membership."""
        self.assertIn("dominika", WEBTERM_ONLY_USERS)
        self.assertIn("dominika", WEBTERM_OBSERVER_USERS)
        # david1-4 are webterm-only but NOT observers
        for d in ("david1", "david2", "david3", "david4"):
            self.assertIn(d, WEBTERM_ONLY_USERS)
            self.assertNotIn(d, WEBTERM_OBSERVER_USERS)

    def test_expected_members(self):
        self.assertEqual(
            WEBTERM_ONLY_USERS,
            frozenset({"david1", "david2", "david3", "david4", "dominika"}),
        )

    def test_each_member_has_gatekeeper_identity(self):
        """Every webterm-only user's REMOTE_HOSTS entry must pin the
        gatekeeper_access_ed25519 identity."""
        for entry in REMOTE_HOSTS:
            if entry["user"] in WEBTERM_ONLY_USERS:
                self.assertIn(
                    "gatekeeper_access_ed25519",
                    entry.get("identity", ""),
                    "webterm-only user %s lacks gatekeeper identity"
                    % entry["user"],
                )


# ---------------------------------------------------------------------------
# Desired-set tests
# ---------------------------------------------------------------------------

class TestDesiredKeysForUser(unittest.TestCase):

    def test_non_webterm_user_raises(self):
        with self.assertRaises(ValueError):
            cli_webterm_only.desired_keys_for_user("marek")

    def test_dominika_gets_fleet_and_owner_keys(self):
        """dominika gets fleet push + owner keys, no lane key."""
        keys = cli_webterm_only.desired_keys_for_user("dominika")
        blobs = {cli_webterm_only._key_blob(k) for k in keys}
        self.assertIn(FLEET_BLOB, blobs)
        # Must have at least 3 keys (fleet + 2 owner)
        self.assertGreaterEqual(len(keys), 3)

    def test_david_needs_lane_key(self):
        """david1 raises ValueError when WEBTERM_DAVID_LANE_PUBKEY is None."""
        with mock.patch.object(cli_webterm_only, "WEBTERM_DAVID_LANE_PUBKEY",
                               None):
            with self.assertRaises(ValueError) as cm:
                cli_webterm_only.desired_keys_for_user("david1")
            self.assertIn("None", str(cm.exception))

    def test_david_with_lane_key(self):
        """david1 includes lane key when set."""
        fake_lane = "ssh-ed25519 AAAAFakeLaneKeyBlob webterm_david@subdev"
        with mock.patch.object(cli_webterm_only, "WEBTERM_DAVID_LANE_PUBKEY",
                               fake_lane):
            keys = cli_webterm_only.desired_keys_for_user("david1")
            blobs = {cli_webterm_only._key_blob(k) for k in keys}
            self.assertIn(cli_webterm_only._key_blob(fake_lane), blobs)
            self.assertIn(FLEET_BLOB, blobs)

    def test_keys_sorted_by_blob(self):
        """Desired keys must be sorted by blob for deterministic output."""
        keys = cli_webterm_only.desired_keys_for_user("dominika")
        blobs = [cli_webterm_only._key_blob(k) for k in keys]
        self.assertEqual(blobs, sorted(blobs))


# ---------------------------------------------------------------------------
# Render authorized_keys
# ---------------------------------------------------------------------------

class TestRenderAuthorizedKeys(unittest.TestCase):

    def test_header_present(self):
        content = cli_webterm_only.render_authorized_keys("dominika")
        self.assertIn("airuleset:managed", content)
        self.assertIn("#869", content)

    def test_trailing_newline(self):
        content = cli_webterm_only.render_authorized_keys("dominika")
        self.assertTrue(content.endswith("\n"))

    def test_fleet_key_in_content(self):
        content = cli_webterm_only.render_authorized_keys("dominika")
        self.assertIn(FLEET_BLOB, content)


# ---------------------------------------------------------------------------
# Key manager tests (hermetic — fake $HOME tmpdir)
# ---------------------------------------------------------------------------

class TestManageWebtermOnlyKeys(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ssh_dir = os.path.join(self.tmpdir, ".ssh")
        self.log_dir = os.path.join(self.tmpdir, ".claude")
        self.ak_path = os.path.join(self.ssh_dir, "authorized_keys")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_ak(self, content):
        os.makedirs(self.ssh_dir, exist_ok=True)
        with open(self.ak_path, "w") as f:
            f.write(content)

    def test_lockout_guard_empty_desired_set(self):
        """Guard refuses when desired set would be empty."""
        with mock.patch.object(cli_webterm_only, "FLEET_PUSH_PUBKEY", ""):
            r = cli_webterm_only.manage_webterm_only_keys(
                user="dominika", ssh_dir=self.ssh_dir,
                run=_fake_run_ok, log_dir=self.log_dir,
            )
        self.assertEqual(r["action"], "error")

    def test_lockout_guard_preserves_file(self):
        """Guard must NOT touch the file when refusing."""
        original = FOREIGN_KEY + "\n"
        self._write_ak(original)
        with mock.patch.object(cli_webterm_only, "FLEET_PUSH_PUBKEY",
                               "ssh-ed25519 AAAA_BROKEN"):
            # desired_keys_for_user will include the broken key; the guard
            # catches it structurally because the builder always includes
            # FLEET_PUSH_PUBKEY. But let's simulate a broken state:
            with mock.patch.object(cli_webterm_only, "desired_keys_for_user",
                                   return_value=[]):
                r = cli_webterm_only.manage_webterm_only_keys(
                    user="dominika", ssh_dir=self.ssh_dir,
                    run=_fake_run_ok, log_dir=self.log_dir,
                )
        # File must be BYTE IDENTICAL
        with open(self.ak_path) as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(r["action"], "error")

    def test_foreign_key_quarantined(self):
        """A foreign key is moved to quarantine, not destroyed."""
        self._write_ak(
            cli_webterm_only.FLEET_PUSH_PUBKEY + "\n" + FOREIGN_KEY + "\n"
        )
        r = cli_webterm_only.manage_webterm_only_keys(
            user="dominika", ssh_dir=self.ssh_dir,
            run=_fake_run_ok, log_dir=self.log_dir,
        )
        self.assertEqual(r["action"], "updated")
        # Foreign blob must NOT be in the live file
        with open(self.ak_path) as f:
            live = f.read()
        self.assertNotIn(FOREIGN_BLOB, live)
        # Quarantine file must exist and contain the foreign blob
        removed_files = [
            f for f in os.listdir(self.ssh_dir)
            if f.startswith("authorized_keys.airuleset-removed-")
        ]
        self.assertTrue(removed_files, "quarantine file not created")
        with open(os.path.join(self.ssh_dir, removed_files[0])) as f:
            quarantine = f.read()
        self.assertIn(FOREIGN_BLOB, quarantine)

    def test_prev_backup_created(self):
        """The previous whole file is saved as -prev-<ts>."""
        original = cli_webterm_only.FLEET_PUSH_PUBKEY + "\n" + FOREIGN_KEY + "\n"
        self._write_ak(original)
        cli_webterm_only.manage_webterm_only_keys(
            user="dominika", ssh_dir=self.ssh_dir,
            run=_fake_run_ok, log_dir=self.log_dir,
        )
        prev_files = [
            f for f in os.listdir(self.ssh_dir)
            if f.startswith("authorized_keys.airuleset-prev-")
        ]
        self.assertTrue(prev_files, "prev backup not created")
        with open(os.path.join(self.ssh_dir, prev_files[0])) as f:
            self.assertEqual(f.read(), original)

    def test_idempotent_second_run(self):
        """Second run with identical content is a no-op — no writes."""
        r1 = cli_webterm_only.manage_webterm_only_keys(
            user="dominika", ssh_dir=self.ssh_dir,
            run=_fake_run_ok, log_dir=self.log_dir,
        )
        self.assertEqual(r1["action"], "updated")
        # Record files before second run
        files_before = set(os.listdir(self.ssh_dir))
        r2 = cli_webterm_only.manage_webterm_only_keys(
            user="dominika", ssh_dir=self.ssh_dir,
            run=_fake_run_ok, log_dir=self.log_dir,
        )
        self.assertEqual(r2["action"], "no-op")
        # No new files created
        files_after = set(os.listdir(self.ssh_dir))
        self.assertEqual(files_before, files_after)

    def test_os_replace_used(self):
        """The atomic os.replace path is used (not in-place truncate)."""
        # We verify by checking that a -new- temp file is NOT left behind
        cli_webterm_only.manage_webterm_only_keys(
            user="dominika", ssh_dir=self.ssh_dir,
            run=_fake_run_ok, log_dir=self.log_dir,
        )
        new_files = [
            f for f in os.listdir(self.ssh_dir)
            if f.startswith("authorized_keys.airuleset-new-")
        ]
        self.assertEqual(new_files, [],
                         "temp -new- file should not remain after os.replace")

    def test_audit_log_comment_fingerprint_not_blob(self):
        """Audit log must contain comment + fingerprint, NEVER the raw blob."""
        self._write_ak(
            cli_webterm_only.FLEET_PUSH_PUBKEY + "\n" + FOREIGN_KEY + "\n"
        )
        cli_webterm_only.manage_webterm_only_keys(
            user="dominika", ssh_dir=self.ssh_dir,
            run=_fake_run_ok, log_dir=self.log_dir,
        )
        log_path = os.path.join(self.log_dir, "webterm-only-keys.log")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path) as f:
            log_content = f.read()
        # Must contain the comment
        self.assertIn("grena@MacBook-Air.local", log_content)
        # Must contain fingerprint
        self.assertIn("SHA256:", log_content)
        # Must NOT contain the raw blob
        self.assertNotIn(FOREIGN_BLOB, log_content)

    def test_creates_ssh_dir_if_missing(self):
        """Manages keys even when ~/.ssh doesn't exist yet."""
        r = cli_webterm_only.manage_webterm_only_keys(
            user="dominika", ssh_dir=self.ssh_dir,
            run=_fake_run_ok, log_dir=self.log_dir,
        )
        self.assertEqual(r["action"], "updated")
        self.assertTrue(os.path.exists(self.ak_path))

    def test_dry_run_touches_nothing(self):
        """dry_run=True reports what would happen without writing."""
        self._write_ak(FOREIGN_KEY + "\n")
        r = cli_webterm_only.manage_webterm_only_keys(
            user="dominika", ssh_dir=self.ssh_dir,
            run=_fake_run_ok, log_dir=self.log_dir, dry_run=True,
        )
        self.assertEqual(r["action"], "dry-run")
        # File must be unchanged
        with open(self.ak_path) as f:
            self.assertIn(FOREIGN_BLOB, f.read())


# ---------------------------------------------------------------------------
# sshd drop-in renderer
# ---------------------------------------------------------------------------

class TestSshdDropinRenderer(unittest.TestCase):

    def test_render_contains_all_users(self):
        content = cli_webterm_only.render_sshd_dropin()
        for user in WEBTERM_ONLY_USERS:
            self.assertIn(user, content)

    def test_users_sorted(self):
        content = cli_webterm_only.render_sshd_dropin()
        # Extract the Match User line
        for line in content.splitlines():
            if line.startswith("Match User"):
                users = line.split("Match User ")[1].split(",")
                self.assertEqual(users, sorted(users))
                break

    def test_password_auth_disabled(self):
        content = cli_webterm_only.render_sshd_dropin()
        self.assertIn("PasswordAuthentication no", content)
        self.assertIn("KbdInteractiveAuthentication no", content)

    def test_authorized_keys_file_pinned(self):
        """AuthorizedKeysFile is pinned to close the authorized_keys2 side
        door."""
        content = cli_webterm_only.render_sshd_dropin()
        self.assertIn("AuthorizedKeysFile .ssh/authorized_keys", content)

    def test_render_is_deterministic(self):
        self.assertEqual(
            cli_webterm_only.render_sshd_dropin(),
            cli_webterm_only.render_sshd_dropin(),
        )


# ---------------------------------------------------------------------------
# Subdev accounts conf renderer
# ---------------------------------------------------------------------------

class TestSubdevAccountsConf(unittest.TestCase):

    def test_render_covers_all_subdev_users(self):
        """Every REMOTE_HOSTS subdev account appears in the conf."""
        subdev_users = {
            e["user"] for e in REMOTE_HOSTS
            if e.get("host") == "100.118.174.27"
        }
        conf = cli_webterm_only.render_subdev_accounts_conf()
        for user in subdev_users:
            self.assertIn(user, conf,
                          "%s missing from subdev accounts conf" % user)

    def test_dominika_included(self):
        """dominika must be in the rendered conf (#869 regression)."""
        conf = cli_webterm_only.render_subdev_accounts_conf()
        self.assertIn("dominika", conf)

    def test_identity_derivation(self):
        """Accounts with gatekeeper identity get the basename, others
        get 'default'."""
        conf = cli_webterm_only.render_subdev_accounts_conf()
        for line in conf.strip().splitlines():
            parts = line.split("\t")
            self.assertEqual(len(parts), 2)
            user, key_basename = parts
            if user.startswith("montalu"):
                self.assertEqual(key_basename, "default",
                                 "%s should have default key" % user)
            elif user in ("marek", "david1", "david2", "david3", "david4",
                          "dominika", "simap1", "miva1"):
                self.assertEqual(key_basename, "gatekeeper_access_ed25519",
                                 "%s should have gatekeeper key" % user)

    def test_fallback_matches_rendered(self):
        """The hardcoded fallback must match the rendered conf — test-lock."""
        conf = cli_webterm_only.render_subdev_accounts_conf()
        rendered = {}
        for line in conf.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                rendered[parts[0]] = parts[1]
        self.assertEqual(
            rendered, cli_webterm_only.SUBDEV_ACCOUNTS_FALLBACK,
            "Hardcoded fallback drifted from rendered conf"
        )

    def test_sorted_output(self):
        conf = cli_webterm_only.render_subdev_accounts_conf()
        lines = conf.strip().splitlines()
        self.assertEqual(lines, sorted(lines))


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):

    def test_key_blob_valid(self):
        blob = cli_webterm_only._key_blob(
            "ssh-ed25519 AAAAB3NzaC1 comment"
        )
        self.assertEqual(blob, "AAAAB3NzaC1")

    def test_key_blob_empty(self):
        self.assertIsNone(cli_webterm_only._key_blob(""))
        self.assertIsNone(cli_webterm_only._key_blob(None))

    def test_key_comment(self):
        self.assertEqual(
            cli_webterm_only._key_comment("ssh-ed25519 BLOB my comment"),
            "my comment",
        )

    def test_key_comment_no_comment(self):
        self.assertEqual(
            cli_webterm_only._key_comment("ssh-ed25519 BLOB"),
            "",
        )

    def test_fingerprint_success(self):
        fp = cli_webterm_only._fingerprint(
            "ssh-ed25519 AAAAB3NzaC1 test", run=_fake_run_ok
        )
        self.assertIn("SHA256:", fp)

    def test_fingerprint_failure(self):
        fp = cli_webterm_only._fingerprint(
            "ssh-ed25519 AAAAB3NzaC1 test", run=_fake_run_fail
        )
        self.assertEqual(fp, "(unavailable)")


# ---------------------------------------------------------------------------
# Audit tests
# ---------------------------------------------------------------------------

class TestAudit(unittest.TestCase):

    def test_audit_clean(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ssh_dir = os.path.join(tmpdir, ".ssh")
            os.makedirs(ssh_dir)
            content = cli_webterm_only.render_authorized_keys("dominika")
            with open(os.path.join(ssh_dir, "authorized_keys"), "w") as f:
                f.write(content)
            result = cli_webterm_only.audit_webterm_only_keys(
                "dominika", ssh_dir=ssh_dir, run=_fake_run_ok
            )
            self.assertEqual(result["findings"], [])

    def test_audit_foreign_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ssh_dir = os.path.join(tmpdir, ".ssh")
            os.makedirs(ssh_dir)
            content = (
                cli_webterm_only.render_authorized_keys("dominika")
                + FOREIGN_KEY + "\n"
            )
            with open(os.path.join(ssh_dir, "authorized_keys"), "w") as f:
                f.write(content)
            result = cli_webterm_only.audit_webterm_only_keys(
                "dominika", ssh_dir=ssh_dir, run=_fake_run_ok
            )
            self.assertTrue(
                any("FOREIGN" in f for f in result["findings"]),
                "Foreign key not flagged"
            )

    def test_audit_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ssh_dir = os.path.join(tmpdir, ".ssh")
            os.makedirs(ssh_dir)
            result = cli_webterm_only.audit_webterm_only_keys(
                "dominika", ssh_dir=ssh_dir, run=_fake_run_ok
            )
            self.assertTrue(
                any("MISSING" in f for f in result["findings"])
            )


# ---------------------------------------------------------------------------
# Go-live grep-gate
# ---------------------------------------------------------------------------

class TestGoLiveGrepGate(unittest.TestCase):
    """The go-live procedures must never instruct adding a developer's
    personal SSH key."""

    def test_david_go_live_no_personal_key(self):
        """cli_webterm_david must not contain instructions to add a
        personal developer key."""
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cli_webterm_david.py",
        )
        if not os.path.exists(path):
            self.skipTest("cli_webterm_david.py not found")
        with open(path) as f:
            content = f.read()
        # Should not instruct adding personal keys like grena@
        self.assertNotIn("grena@", content)

    def test_dominika_go_live_no_personal_key(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cli_webterm_dominika.py",
        )
        if not os.path.exists(path):
            self.skipTest("cli_webterm_dominika.py not found")
        with open(path) as f:
            content = f.read()
        self.assertNotIn("grena@", content)


# ---------------------------------------------------------------------------
# Fleet push pubkey constant
# ---------------------------------------------------------------------------

class TestFleetPushPubkey(unittest.TestCase):

    def test_fleet_push_pubkey_is_public_key(self):
        """The committed constant is a PUBLIC key (ssh-ed25519), not private."""
        self.assertTrue(
            cli_webterm_only.FLEET_PUSH_PUBKEY.startswith("ssh-ed25519 "),
            "FLEET_PUSH_PUBKEY must be a public key"
        )
        self.assertNotIn("PRIVATE", cli_webterm_only.FLEET_PUSH_PUBKEY.upper())

    def test_fleet_push_pubkey_blob_matches_gatekeeper_access(self):
        """The committed blob must match what's on disk at
        ~/.secrets/gatekeeper_access_ed25519.pub."""
        pub_path = os.path.expanduser(
            "~/.secrets/gatekeeper_access_ed25519.pub"
        )
        if not os.path.exists(pub_path):
            self.skipTest("gatekeeper_access_ed25519.pub not on this box")
        with open(pub_path) as f:
            on_disk = f.read().strip()
        disk_blob = cli_webterm_only._key_blob(on_disk)
        committed_blob = cli_webterm_only._key_blob(
            cli_webterm_only.FLEET_PUSH_PUBKEY
        )
        self.assertEqual(committed_blob, disk_blob,
                         "Committed FLEET_PUSH_PUBKEY blob drifted from disk")


if __name__ == "__main__":
    unittest.main()
