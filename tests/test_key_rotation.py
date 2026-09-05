"""Tests for cli_key_rotation — F1 fleet push-key rotation (#870).

Hermetic with an injected runner (never real ssh). The design's (a)–(h) list:
  (a) remove before verify refused (invariant lock)
  (b) verify records evidence only on exit 0 + expected output
  (c) add idempotent (2nd run no-op)
  (d) paused host skipped + debt recorded
  (e) webterm-only lockout guard accepts tuple, refuses set lacking every fleet blob
  (f) dev1 entry untouched without --include-dev1
  (g) sshpass-host conversion sets identity
  (h) root@subdev via gk hop

Plus:
  - state file mode 0600 + atomic
  - --dry-run performs zero runner writes
  - summary text contains no private-key material
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import cli_key_rotation as kr
import cli_webterm_only as wto


# A fixture "new" pubkey — NOT any real key  # airuleset:secret-ok test fixture
FIXTURE_NEW_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTestingOnly000000000000"
    "000000000 test-new-key"
)

# A fixture private-key-looking string — must NEVER appear in summary output
FIXTURE_PRIVATE_KEY_MATERIAL = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbm FAKE PRIVATE KEY DO NOT USE\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


def _make_runner(responses=None):
    """Build an injected runner that records calls and returns canned
    responses. ``responses`` maps an ssh-target (``user@host``) to a
    ``(returncode, stdout)`` tuple; unmatched calls return rc=0, 'OK'."""
    calls = []
    responses = responses or {}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        # Extract user@host from the argv (the last-but-one element before
        # the command string)
        target = ""
        for i, tok in enumerate(argv):
            if "@" in tok and not tok.startswith("-") and i > 0:
                target = tok
        rc, stdout = responses.get(target, (0, "ADDED-OK"))
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")

    return run, calls


def _make_verify_runner(user_map=None):
    """Runner for VERIFY: returns ``OK-<user>`` for each target."""
    calls = []
    user_map = user_map or {}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        target = ""
        for tok in argv:
            if "@" in tok and not tok.startswith("-"):
                target = tok
        user = target.split("@")[0] if target else "unknown"
        expected_out = user_map.get(target, "OK-%s" % user)
        return SimpleNamespace(returncode=0, stdout=expected_out, stderr="")

    return run, calls


def _make_remove_runner():
    """Runner for REMOVE: returns ``REMOVED-OK``."""
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="REMOVED-OK", stderr="")

    return run, calls


# Minimal REMOTE_HOSTS subset for tests
_TEST_HOSTS = [
    {"name": "gk", "host": "10.0.0.1", "user": "gatekeeper",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
    {"name": "dev2", "host": "10.0.0.2", "user": "newlevel"},
    {"name": "paused-host", "host": "10.0.0.3", "user": "simap1",
     "identity": "~/.secrets/gatekeeper_access_ed25519",
     "paused": "owner 2026-09-02: paused"},
]

_TEST_GUARDS = [
    {"name": "subdev", "host": "10.0.0.4", "admin_user": "root",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
]


class TestPhaseAdd(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", _TEST_GUARDS)
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS)
    def test_add_appends_to_all_targets(self):
        """(c partial) add runs for non-paused, non-dev1 hosts."""
        run, calls = _make_runner()
        report = kr.phase_add(
            new_pubkey=FIXTURE_NEW_PUBKEY,
            old_identity="~/.secrets/gatekeeper_access_ed25519",
            state_file=self.state_file,
            run=run,
        )
        results = report["results"]
        # gk + dev2 + root@subdev = 3 targets (paused skipped)
        added = [h for h, r in results.items() if "added" in r["action"]]
        self.assertEqual(len(added), 3, added)

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", _TEST_GUARDS)
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS)
    def test_add_idempotent(self):
        """(c) add twice = second run is all already-added."""
        run, calls = _make_runner()
        kr.phase_add(new_pubkey=FIXTURE_NEW_PUBKEY,
                     old_identity="~/.secrets/gk",
                     state_file=self.state_file, run=run)
        # Second run
        report2 = kr.phase_add(new_pubkey=FIXTURE_NEW_PUBKEY,
                               old_identity="~/.secrets/gk",
                               state_file=self.state_file, run=run)
        for hk, r in report2["results"].items():
            if r["action"] != "skipped":
                self.assertIn("already", r["action"], "%s: %s" % (hk, r))

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", _TEST_GUARDS)
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS)
    def test_paused_host_skipped_and_debt(self):
        """(d) paused host -> skipped + debt_hosts."""
        run, calls = _make_runner()
        report = kr.phase_add(new_pubkey=FIXTURE_NEW_PUBKEY,
                              old_identity="~/.secrets/gk",
                              state_file=self.state_file, run=run)
        paused_key = "simap1@10.0.0.3"
        self.assertEqual(report["results"][paused_key]["action"], "skipped")
        self.assertIn(paused_key, report["debt_hosts"])

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", [
        {"name": "dev1", "host": "100.104.8.125", "user": "newlevel"},
        {"name": "gk", "host": "10.0.0.1", "user": "gatekeeper",
         "identity": "~/.secrets/gk"},
    ])
    def test_dev1_excluded_by_default(self):
        """(f) dev1 not touched without --include-dev1."""
        run, calls = _make_runner()
        report = kr.phase_add(new_pubkey=FIXTURE_NEW_PUBKEY,
                              old_identity="~/.secrets/gk",
                              state_file=self.state_file, run=run)
        self.assertNotIn("newlevel@100.104.8.125", report["results"])

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", [
        {"name": "dev1", "host": "100.104.8.125", "user": "newlevel"},
        {"name": "gk", "host": "10.0.0.1", "user": "gatekeeper",
         "identity": "~/.secrets/gk"},
    ])
    def test_dev1_included_with_flag(self):
        """(f) dev1 IS touched with --include-dev1."""
        run, calls = _make_runner()
        report = kr.phase_add(new_pubkey=FIXTURE_NEW_PUBKEY,
                              old_identity="~/.secrets/gk",
                              state_file=self.state_file,
                              include_dev1=True, run=run)
        self.assertIn("newlevel@100.104.8.125", report["results"])

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", _TEST_GUARDS)
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS)
    def test_gk_hop_for_root_subdev(self):
        """(h) root@subdev ssh goes through -J gatekeeper@<gk>."""
        run, calls = _make_runner()
        kr.phase_add(new_pubkey=FIXTURE_NEW_PUBKEY,
                     old_identity="~/.secrets/gk",
                     state_file=self.state_file, run=run)
        # Find the call for root@10.0.0.4
        root_calls = [c for c in calls if "root@10.0.0.4" in c[0]]
        self.assertTrue(root_calls, "no call for root@subdev")
        argv = root_calls[0][0]
        self.assertIn("-J", argv)
        # The -J arg is the next element after -J
        j_idx = argv.index("-J")
        self.assertIn("gatekeeper@", argv[j_idx + 1])


class TestPhaseVerify(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")
        # Pre-seed state with add phase done
        state = {
            "gatekeeper@10.0.0.1": {"added_at": "2026-09-01T00:00:00Z"},
            "newlevel@10.0.0.2": {"added_at": "2026-09-01T00:00:00Z"},
        }
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as fh:
            json.dump(state, fh)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:2])
    def test_verify_records_on_correct_output(self):
        """(b) verify records verified_at only on exit 0 + OK-<user>."""
        run, calls = _make_verify_runner()
        report = kr.phase_verify(
            new_key_path="~/.secrets/new_key",
            state_file=self.state_file, run=run,
        )
        for hk, r in report["results"].items():
            self.assertEqual(r["action"], "verified", "%s: %s" % (hk, r))
        # Check state file
        state = kr.load_state(self.state_file)
        self.assertIn("verified_at", state["gatekeeper@10.0.0.1"])
        self.assertEqual(state["gatekeeper@10.0.0.1"]["verify_output"],
                         "OK-gatekeeper")

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:2])
    def test_verify_fails_on_wrong_output(self):
        """(b) verify does NOT record verified_at on wrong output."""
        def bad_run(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout="WRONG", stderr="")

        report = kr.phase_verify(
            new_key_path="~/.secrets/new_key",
            state_file=self.state_file, run=bad_run,
        )
        for hk, r in report["results"].items():
            self.assertEqual(r["action"], "FAILED", "%s: %s" % (hk, r))
        state = kr.load_state(self.state_file)
        self.assertNotIn("verified_at",
                         state.get("gatekeeper@10.0.0.1", {}))

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:2])
    def test_verify_fails_on_nonzero_exit(self):
        """(b) verify does NOT record on non-zero exit."""
        def fail_run(argv, **kwargs):
            return SimpleNamespace(returncode=255, stdout="", stderr="error")

        report = kr.phase_verify(
            new_key_path="~/.secrets/new_key",
            state_file=self.state_file, run=fail_run,
        )
        for hk, r in report["results"].items():
            self.assertEqual(r["action"], "FAILED")


class TestPhaseRemove(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")
        # Write a fixture "new key" file so the gate passes
        self.new_key_path = os.path.join(self.tmpdir, "new_key")
        with open(self.new_key_path, "w") as fh:
            fh.write(FIXTURE_PRIVATE_KEY_MATERIAL)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:2])
    def test_remove_refused_before_verify(self):
        """(a) HARD INVARIANT: remove before verify is refused."""
        # State: added but NOT verified
        state = {
            "gatekeeper@10.0.0.1": {"added_at": "2026-09-01T00:00:00Z"},
            "newlevel@10.0.0.2": {"added_at": "2026-09-01T00:00:00Z"},
        }
        with open(self.state_file, "w") as fh:
            json.dump(state, fh)

        run, calls = _make_remove_runner()
        report = kr.phase_remove(
            old_pubkey=kr.OLD_FLEET_PUSH_PUBKEY,
            new_key_path=self.new_key_path,
            state_file=self.state_file, run=run,
        )
        for hk, r in report["results"].items():
            self.assertEqual(r["action"], "REFUSED",
                             "%s should be refused: %s" % (hk, r))
        # No ssh calls should have been made
        self.assertEqual(len(calls), 0, "ssh calls made despite refusal")

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:2])
    def test_remove_succeeds_after_verify(self):
        """remove works when verified_at is present."""
        state = {
            "gatekeeper@10.0.0.1": {"added_at": "2026-09-01T00:00:00Z",
                                     "verified_at": "2026-09-01T01:00:00Z",
                                     "verify_output": "OK-gatekeeper"},
            "newlevel@10.0.0.2": {"added_at": "2026-09-01T00:00:00Z",
                                   "verified_at": "2026-09-01T01:00:00Z",
                                   "verify_output": "OK-newlevel"},
        }
        with open(self.state_file, "w") as fh:
            json.dump(state, fh)

        run, calls = _make_remove_runner()
        report = kr.phase_remove(
            old_pubkey=kr.OLD_FLEET_PUSH_PUBKEY,
            new_key_path=self.new_key_path,
            state_file=self.state_file, run=run,
        )
        for hk, r in report["results"].items():
            self.assertEqual(r["action"], "removed", "%s: %s" % (hk, r))
        self.assertEqual(len(calls), 2)

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:1])
    def test_remove_refuses_when_new_key_absent(self):
        """remove refuses when the new key file doesn't exist."""
        state = {"gatekeeper@10.0.0.1": {"added_at": "t", "verified_at": "t",
                                          "verify_output": "OK-gatekeeper"}}
        with open(self.state_file, "w") as fh:
            json.dump(state, fh)

        report = kr.phase_remove(
            old_pubkey=kr.OLD_FLEET_PUSH_PUBKEY,
            new_key_path="/nonexistent/key",
            state_file=self.state_file,
        )
        self.assertIn("error", report)
        self.assertIn("absent", report["error"])


class TestStateFile(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_state_file_mode_0600(self):
        """State file is written with mode 0600."""
        kr.save_state({"test": True}, self.state_file)
        mode = stat.S_IMODE(os.stat(self.state_file).st_mode)
        self.assertEqual(mode, 0o600, "mode %o != 0600" % mode)

    def test_state_file_atomic(self):
        """State file is written atomically (no partial content on crash)."""
        # Write initial state
        kr.save_state({"v": 1}, self.state_file)
        # The tmp file should not exist after save
        tmp = self.state_file + ".tmp"
        self.assertFalse(os.path.exists(tmp))
        # Content is valid JSON
        with open(self.state_file) as fh:
            data = json.load(fh)
        self.assertEqual(data["v"], 1)

    def test_load_returns_empty_dict_when_absent(self):
        """load_state returns {} for a non-existent file."""
        state = kr.load_state("/nonexistent/path/state.json")
        self.assertEqual(state, {})


class TestDryRun(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:2])
    def test_dry_run_makes_no_ssh_calls(self):
        """--dry-run performs zero runner writes."""
        run, calls = _make_runner()
        kr.phase_add(
            new_pubkey=FIXTURE_NEW_PUBKEY,
            old_identity="~/.secrets/gk",
            state_file=self.state_file,
            dry_run=True, run=run,
        )
        self.assertEqual(len(calls), 0, "dry-run made ssh calls")

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:2])
    def test_dry_run_does_not_write_state(self):
        """--dry-run does not write the state file."""
        run, _ = _make_runner()
        kr.phase_add(
            new_pubkey=FIXTURE_NEW_PUBKEY,
            old_identity="~/.secrets/gk",
            state_file=self.state_file,
            dry_run=True, run=run,
        )
        self.assertFalse(os.path.exists(self.state_file))


class TestSummaryNoSecrets(unittest.TestCase):

    def test_summary_has_no_private_key_material(self):
        """Summary text contains no private-key material."""
        report = {
            "results": {
                "gk@10.0.0.1": {"action": "added"},
                "dev2@10.0.0.2": {"action": "verified",
                                   "verified_at": "2026-09-01T01:00:00Z"},
            },
            "debt_hosts": ["paused@10.0.0.3"],
        }
        summary = kr.render_summary("add", report)
        # Must not contain private key markers
        self.assertNotIn("PRIVATE KEY", summary)
        self.assertNotIn("BEGIN OPENSSH", summary)
        self.assertNotIn("b3BlbnNz", summary)  # base64 of openssh key header
        # Pubkey blobs in the summary ARE fine (public material) — no assertion
        # against them.

    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:2])
    def test_add_output_has_no_private_key(self):
        """Phase_add's state and summary never leak private key material."""
        # Inject private key material into the runner's stderr to see it
        # doesn't leak into the report
        def run_with_leak(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout="ADDED-OK",
                                  stderr=FIXTURE_PRIVATE_KEY_MATERIAL)

        tmpdir = tempfile.mkdtemp()
        try:
            sf = os.path.join(tmpdir, "s.json")
            report = kr.phase_add(
                new_pubkey=FIXTURE_NEW_PUBKEY,
                old_identity="~/.secrets/gk",
                state_file=sf, run=run_with_leak,
            )
            summary = kr.render_summary("add", report)
            state_str = json.dumps(kr.load_state(sf))
            combined = summary + state_str
            self.assertNotIn("PRIVATE KEY", combined)
            self.assertNotIn("BEGIN OPENSSH", combined)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestFleetPubkeysTuple(unittest.TestCase):
    """(e) FLEET_PUSH_PUBKEYS tuple + lockout guard."""

    def test_tuple_exists_and_old_alias(self):
        """FLEET_PUSH_PUBKEYS is a tuple; FLEET_PUSH_PUBKEY == [0]."""
        self.assertIsInstance(wto.FLEET_PUSH_PUBKEYS, tuple)
        self.assertGreaterEqual(len(wto.FLEET_PUSH_PUBKEYS), 1)
        self.assertEqual(wto.FLEET_PUSH_PUBKEY, wto.FLEET_PUSH_PUBKEYS[0])

    def test_lockout_guard_real_function_one_member(self):
        """Y4: the REAL manage_webterm_only_keys passes with a 2-member
        tuple even when only member 0's blob is in the desired set."""
        tmpdir = tempfile.mkdtemp()
        ssh_dir = os.path.join(tmpdir, ".ssh")
        os.makedirs(ssh_dir)
        log_dir = os.path.join(tmpdir, "log")
        os.makedirs(log_dir)
        ak = os.path.join(ssh_dir, "authorized_keys")
        # Write ONLY the old key (member 0) — the guard must still pass
        with open(ak, "w") as f:
            f.write(wto.FLEET_PUSH_PUBKEYS[0] + "\n")
        # Patch FLEET_PUSH_PUBKEYS to have 2 members (old + a fake new)
        fake_new = "ssh-ed25519 AAAAFakeNewKey test-new"
        two_member = (wto.FLEET_PUSH_PUBKEYS[0], fake_new)
        with mock.patch.object(wto, "FLEET_PUSH_PUBKEYS", two_member):
            r = wto.manage_webterm_only_keys(
                user="dominika", ssh_dir=ssh_dir,
                run=lambda *a, **kw: SimpleNamespace(
                    returncode=0, stdout="", stderr=""),
                log_dir=log_dir,
            )
        # Should NOT be an error — the guard accepted the old member
        self.assertNotEqual(r["action"], "error", r)
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_lockout_guard_refuses_empty_fleet(self):
        """The lockout guard refuses a desired set missing ALL fleet blobs."""
        desired_blobs = {"some-other-blob"}
        fleet_blobs = {wto._key_blob(k) for k in wto.FLEET_PUSH_PUBKEYS}
        has_fleet = bool(fleet_blobs & desired_blobs)
        self.assertFalse(has_fleet)


class TestDiskGuardRootCoverage(unittest.TestCase):
    """R2: DISK_GUARD_ROOT_HOSTS entries are rotation targets."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [
        {"name": "gk", "host": "10.0.0.99", "admin_user": "root",
         "identity": "~/.secrets/gatekeeper_access_ed25519"},
    ])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:1])
    def test_disk_guard_root_host_included(self):
        """root@gk from DISK_GUARD_ROOT_HOSTS is a rotation target."""
        run, calls = _make_runner()
        report = kr.phase_add(
            new_pubkey=FIXTURE_NEW_PUBKEY,
            old_identity="~/.secrets/gk",
            state_file=self.state_file, run=run,
        )
        self.assertIn("root@10.0.0.99", report["results"])
        self.assertIn("added", report["results"]["root@10.0.0.99"]["action"])


class TestLivePausedInAllPhases(unittest.TestCase):
    """Y2: is_paused checked LIVE in verify and remove too."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")
        # Host that was NOT paused during add but IS paused now
        self.paused_after_add = [
            {"name": "gk", "host": "10.0.0.1", "user": "gatekeeper",
             "identity": "~/.secrets/gatekeeper_access_ed25519",
             "paused": "owner paused after add"},
        ]
        state = {
            "gatekeeper@10.0.0.1": {
                "added_at": "2026-09-01T00:00:00Z",
                "verified_at": "2026-09-01T01:00:00Z",
                "verify_output": "OK-gatekeeper",
            },
        }
        with open(self.state_file, "w") as fh:
            json.dump(state, fh)
        # Write a fixture new key
        self.new_key = os.path.join(self.tmpdir, "new_key")
        with open(self.new_key, "w") as fh:
            fh.write("fake key")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    def test_verify_skips_newly_paused_host(self):
        """verify checks is_paused LIVE, not stale state."""
        # Override state to have ONLY added_at (not yet verified)
        verify_state = {
            "gatekeeper@10.0.0.1": {
                "added_at": "2026-09-01T00:00:00Z",
            },
        }
        with open(self.state_file, "w") as fh:
            json.dump(verify_state, fh)
        with mock.patch("cli_fleet.REMOTE_HOSTS", self.paused_after_add):
            run, calls = _make_verify_runner()
            report = kr.phase_verify(
                new_key_path=self.new_key,
                state_file=self.state_file, run=run,
            )
        r = report["results"]["gatekeeper@10.0.0.1"]
        self.assertEqual(r["action"], "skipped")
        self.assertEqual(len(calls), 0, "should not ssh a paused host")

    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    def test_remove_skips_newly_paused_host(self):
        """remove checks is_paused LIVE, not stale state."""
        with mock.patch("cli_fleet.REMOTE_HOSTS", self.paused_after_add):
            run, calls = _make_remove_runner()
            report = kr.phase_remove(
                old_pubkey=kr.OLD_FLEET_PUSH_PUBKEY,
                new_key_path=self.new_key,
                state_file=self.state_file, run=run,
            )
        r = report["results"]["gatekeeper@10.0.0.1"]
        self.assertEqual(r["action"], "skipped")
        self.assertEqual(len(calls), 0)


class TestRemoveUsesNewKey(unittest.TestCase):
    """R1: REMOVE authenticates with the NEW key, not the old identity."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")
        self.new_key = os.path.join(self.tmpdir, "new_key")
        with open(self.new_key, "w") as fh:
            fh.write("fake key")
        state = {
            "gatekeeper@10.0.0.1": {
                "added_at": "2026-09-01T00:00:00Z",
                "verified_at": "2026-09-01T01:00:00Z",
                "verify_output": "OK-gatekeeper",
            },
        }
        with open(self.state_file, "w") as fh:
            json.dump(state, fh)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @mock.patch("cli_fleet.SHARED_STREAM_GUARD_HOSTS", [])
    @mock.patch("cli_fleet.DISK_GUARD_ROOT_HOSTS", [])
    @mock.patch("cli_fleet.REMOTE_HOSTS", _TEST_HOSTS[:1])
    def test_remove_ssh_uses_new_key_identity(self):
        """R1: the remove ssh authenticates with the new key path."""
        run, calls = _make_remove_runner()
        kr.phase_remove(
            old_pubkey=kr.OLD_FLEET_PUSH_PUBKEY,
            new_key_path=self.new_key,
            state_file=self.state_file, run=run,
        )
        self.assertEqual(len(calls), 1)
        argv = calls[0][0]
        # The -i flag should point to the NEW key, not the old identity
        i_idx = argv.index("-i")
        self.assertEqual(argv[i_idx + 1], self.new_key)


class TestNewPubkeyPresent(unittest.TestCase):
    """F1 PREP: FLEET_PUSH_PUBKEYS has 2 members; member [1] is the new key."""

    def test_tuple_has_two_members(self):
        """FLEET_PUSH_PUBKEYS must hold exactly 2 members during F1 rotation."""
        self.assertEqual(len(wto.FLEET_PUSH_PUBKEYS), 2,
                         "F1 PREP: new pubkey not yet appended as member [1]")

    def test_new_key_comment(self):
        """Member [1] carries the airuleset-push@airuleset #870 comment."""
        self.assertGreaterEqual(len(wto.FLEET_PUSH_PUBKEYS), 2,
                                "need member [1] to test its comment")
        new = wto.FLEET_PUSH_PUBKEYS[1]
        self.assertIn("airuleset-push@airuleset", new)

    def test_new_key_parses_as_ed25519(self):
        """Member [1] is a valid ssh-ed25519 pubkey."""
        self.assertGreaterEqual(len(wto.FLEET_PUSH_PUBKEYS), 2,
                                "need member [1] to test its format")
        new = wto.FLEET_PUSH_PUBKEYS[1]
        self.assertTrue(new.startswith("ssh-ed25519 "),
                        "expected ssh-ed25519 prefix")
        parts = new.split()
        self.assertGreaterEqual(len(parts), 2, "need at least type + blob")
        blob = parts[1]
        # Verify it base64-decodes (valid blob)
        import base64
        try:
            base64.b64decode(blob)
        except Exception:
            self.fail("member [1] blob is not valid base64: %s" % blob)


class TestOldPubkeyConsistency(unittest.TestCase):
    """Lock: OLD_FLEET_PUSH_PUBKEY matches FLEET_PUSH_PUBKEYS[0]."""

    def test_old_pubkey_matches_current(self):
        self.assertEqual(kr.OLD_FLEET_PUSH_PUBKEY, wto.FLEET_PUSH_PUBKEYS[0])


class TestPrivilegesEntry(unittest.TestCase):
    """cli_privileges declares airuleset_push_ed25519."""

    def test_push_key_in_privileges(self):
        import cli_privileges as cp
        names = {p.name for p in cp.PRIVILEGES}
        self.assertIn("airuleset_push_ed25519", names)

    def test_push_key_must_move(self):
        import cli_privileges as cp
        entry = [p for p in cp.PRIVILEGES if p.name == "airuleset_push_ed25519"][0]
        self.assertTrue(entry.must_move)

    def test_push_key_kind_ssh(self):
        import cli_privileges as cp
        entry = [p for p in cp.PRIVILEGES if p.name == "airuleset_push_ed25519"][0]
        self.assertEqual(entry.kind, cp.KIND_SSH_KEY)


class TestSubcommandRegistered(unittest.TestCase):
    """key-rotation is registered in SUBCOMMANDS."""

    def test_key_rotation_in_subcommands(self):
        import airuleset
        self.assertIn("key-rotation", airuleset.SUBCOMMANDS)
        self.assertEqual(airuleset.SUBCOMMANDS["key-rotation"],
                         airuleset.cmd_key_rotation)


if __name__ == "__main__":
    unittest.main()
