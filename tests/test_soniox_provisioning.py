"""Meeting-analysis dev-env provisioning for the subdev stream accounts (#275).

Two systemic gaps behind the 2026-08-06 montalu hotfix, neither of which was
code before this ticket:

1. `~/.soniox.env` (the skill's canonical, UN-guarded Soniox key path — see
   tests/test_meeting_analysis_skill.py for the guard-collision half) was
   hand-delivered to all 7 subdev stream accounts as a one-time fix, not a
   repeatable install/push step. `provision_subdev_soniox_key()` closes that:
   it extracts ONLY the `SONIOX_API_KEY=` line out of dev1's own
   `~/devel/voiceagent/.env` (a file that carries MANY other unrelated
   secrets — never read out whole) and pipes it, via `input=` (never argv,
   never printed), to every `AUTHORITY_BY_USER` (subdev stream) account over
   the SAME ssh identity plumbing `REMOTE_HOSTS`'s own deploy loop already
   uses. A missing source on this box is a LOUD stderr failure, never a
   silent skip (the gatekeeper Discord `.env` lesson).

2. ffmpeg has no install path at all for the subdev accounts — they have NO
   sudo, so `check_runtime_deps()`'s `apt-get install` can never run there
   (montalu already worked around this by hand). `ensure_ffmpeg_static_binary()`
   is the SAME "best-effort, idempotent, fleet-wide" shape as
   `ensure_claude_cli_installed()`/`ensure_playwright_browsers()`: ONE
   subprocess call does download + extract + place + chmod into `~/bin/ffmpeg`
   (no privilege needed), so no test here needs a real network call or a real
   tar archive — only the constructed shell command and the subprocess's own
   returncode are asserted, exactly like the claude-CLI/Playwright tests it
   mirrors.
"""
import os
import sys
import tempfile
import unittest.mock as m
from io import StringIO
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_remote  # noqa: E402  (#433 L-E seam re-target)
# #433 cluster L: ensure_ffmpeg_static_binary + _ffmpeg_available moved here;
# the leaf→leaf internal call resolves in this leaf, so _ffmpeg_available is
# patched via cli_binary_installers.
import cli_binary_installers


# A fixed, obviously-fake value — never a real Soniox key. Used to prove the
# value travels via subprocess `input=`, never argv, never stdout.
FAKE_KEY = "FAKE-SONIOX-KEY-VALUE-NEVER-IN-ARGV-1234"

FAKE_HOSTS = [
    {"name": "dev2", "host": "1.2.3.4", "user": "newlevel",
     "repo_path": "~/devel/airuleset"},
    {"name": "montalu@subdev", "host": "9.9.9.9", "user": "montalu",
     "repo_path": "~/devel/airuleset"},
    {"name": "marek@subdev", "host": "9.9.9.9", "user": "marek",
     "repo_path": "~/devel/airuleset",
     "identity": "~/.secrets/gatekeeper_access_ed25519"},
]

FAKE_AUTHORITY = {"montalu": "branch-merge", "marek": "branch-merge"}


def _fake_cp(returncode=0, stdout="", stderr=""):
    return m.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# Soniox key extraction — one line out of a multi-secret file, never the
# whole file.
# ---------------------------------------------------------------------------

class TestSonioxKeyLine(TestCase):
    def test_none_when_source_file_is_missing(self):
        src = Path(tempfile.mkdtemp()) / "does-not-exist" / ".env"
        self.assertIsNone(airuleset._soniox_key_line(src))

    def test_extracts_only_the_soniox_key_line(self):
        d = Path(tempfile.mkdtemp())
        src = d / ".env"
        src.write_text(
            "DISCORD_BOT_TOKEN=unrelated-secret-1\n"
            "SONIOX_API_KEY=%s\n"
            "OTHER_SERVICE_TOKEN=unrelated-secret-2\n" % FAKE_KEY)
        self.assertEqual(airuleset._soniox_key_line(src),
                         "SONIOX_API_KEY=%s" % FAKE_KEY)

    def test_none_when_key_is_absent_from_an_otherwise_real_source(self):
        d = Path(tempfile.mkdtemp())
        src = d / ".env"
        src.write_text("DISCORD_BOT_TOKEN=unrelated-secret-1\n")
        self.assertIsNone(airuleset._soniox_key_line(src))

    def test_defaults_to_the_module_level_source_constant(self):
        # never actually reads it — just proves the default resolves under
        # THIS account's own home dir, at the documented dev1 voiceagent
        # path, never a hardcoded literal elsewhere. Structural SUFFIX
        # check, never a live Path.home() re-call: some OTHER test module,
        # elsewhere in a full `unittest discover` run, mutates $HOME
        # without restoring it — a direct Path.home()-vs-Path.home()
        # comparison here is then order-dependent and flakes depending on
        # which tests ran first in the SAME process (SONIOX_KEY_SOURCE
        # itself is unaffected — it's a module-level constant resolved
        # ONCE at airuleset.py's own import time, before any such leak).
        self.assertEqual(airuleset.SONIOX_KEY_SOURCE.parts[-3:],
                         ("devel", "voiceagent", ".env"))


# ---------------------------------------------------------------------------
# Delivery to subdev stream accounts
# ---------------------------------------------------------------------------

class TestProvisionSubdevSonioxKey(TestCase):
    def _source_with_key(self, value=FAKE_KEY):
        d = Path(tempfile.mkdtemp())
        src = d / ".env"
        src.write_text("SONIOX_API_KEY=%s\n" % value)
        return src

    def test_missing_source_is_loud_and_touches_no_host(self):
        out = StringIO()
        missing = Path(tempfile.mkdtemp()) / "does-not-exist" / ".env"
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY), \
                m.patch("sys.stderr", out):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=FAKE_HOSTS, run=run, source=missing)
        self.assertEqual(calls, [], "a missing source must never attempt ssh")
        self.assertIn("MISSING", out.getvalue().upper())
        # both stream accounts (montalu, marek) are reported failed — dev2 is not
        self.assertEqual(len(failed), 2)
        self.assertTrue(all("soniox" in reason for _n, reason in failed))

    def test_no_source_targets_means_no_source_read_at_all(self):
        # #275 design: filter the target list FIRST, read the source only if
        # there is somewhere to deliver it — this is what keeps cmd_push's
        # OWN pre-existing tests (which patch REMOTE_HOSTS to non-stream
        # accounts) safe from ever touching this box's real filesystem.
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        non_stream_hosts = [FAKE_HOSTS[0]]  # dev2 only
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY), \
                m.patch.object(cli_remote, "_soniox_key_line") as line_fn:
            failed = airuleset.provision_subdev_soniox_key(
                hosts=non_stream_hosts, run=run)
        line_fn.assert_not_called()
        self.assertEqual(failed, [])
        self.assertEqual(calls, [])

    def test_only_stream_accounts_receive_the_key(self):
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=FAKE_HOSTS, run=run, source=src)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 2)   # montalu + marek — never dev2
        self.assertFalse(any("1.2.3.4" in " ".join(str(x) for x in argv)
                             for argv, _kw in calls))

    def test_key_value_travels_via_stdin_never_argv(self):
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"montalu": "branch-merge"}):
            airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[1]], run=run, source=src)
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertNotIn(FAKE_KEY, " ".join(str(a) for a in argv))
        self.assertIn(FAKE_KEY, kw.get("input", ""))

    def test_uses_the_declared_identity_when_present(self):
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"marek": "branch-merge"}):
            airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[2]], run=run, source=src)
        argv, _kw = calls[0]
        self.assertIn("-i", argv)
        self.assertIn(
            os.path.expanduser("~/.secrets/gatekeeper_access_ed25519"), argv)

    def test_no_identity_flag_for_default_key_accounts(self):
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"montalu": "branch-merge"}):
            airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[1]], run=run, source=src)
        argv, _kw = calls[0]
        self.assertNotIn("-i", argv)

    def test_one_remote_delivery_failure_does_not_abort_the_rest(self):
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            if "montalu@" in " ".join(str(a) for a in argv):
                return _fake_cp(returncode=255, stderr="Connection refused")
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=FAKE_HOSTS, run=run, source=src)
        self.assertEqual(len(calls), 2, "both accounts must still be attempted")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][0], "montalu@subdev")

    def test_writes_to_soniox_env_under_a_tight_umask_and_chmod(self):
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"montalu": "branch-merge"}):
            airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[1]], run=run, source=src)
        argv, _kw = calls[0]
        remote_cmd = argv[-1]
        self.assertIn("umask 077", remote_cmd)
        self.assertIn("~/.soniox.env", remote_cmd)
        # #275 adversarial-review MINOR: umask only governs file CREATION —
        # `cat >` over an already-existing, loosely-permissioned file would
        # otherwise leave the old mode in place. An explicit chmod after the
        # write closes that regardless of what was there before.
        self.assertIn("chmod 600 ~/.soniox.env", remote_cmd)

    def test_ssh_failure_exception_is_tracked_not_raised(self):
        src = self._source_with_key()

        def run(argv, **kw):
            raise OSError("no route to host")

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"montalu": "branch-merge"}):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[1]], run=run, source=src)   # must not raise
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][0], "montalu@subdev")

    # -- #341: never re-probe a host the deploy loop already saw fail auth --

    def test_skip_names_prevents_a_second_connection_attempt(self):
        # montalu is in the skip set (its deploy leg already failed auth,
        # per cmd_push's own reported findings this run) -- marek is not.
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=FAKE_HOSTS, run=run, source=src,
                skip_names={"montalu@subdev"})
        self.assertEqual(len(calls), 1, "the skipped host must never be "
                          "contacted a second time")
        argv, _kw = calls[0]
        self.assertIn("marek@", " ".join(str(a) for a in argv))
        self.assertNotIn("montalu@", " ".join(str(a) for a in argv))
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][0], "montalu@subdev")

    def test_skipped_host_is_reported_loudly_not_silently(self):
        src = self._source_with_key()
        out = StringIO()

        def run(argv, **kw):
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"montalu": "branch-merge"}), \
                m.patch("sys.stderr", out):
            airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[1]], run=run, source=src,
                skip_names={"montalu@subdev"})
        self.assertIn("montalu@subdev", out.getvalue())
        self.assertIn("auth", out.getvalue().lower())

    def test_all_targets_skipped_never_reads_the_source(self):
        # mirrors test_no_source_targets_means_no_source_read_at_all: no
        # deliverable target left means no reason to touch the filesystem.
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"montalu": "branch-merge"}), \
                m.patch.object(cli_remote, "_soniox_key_line") as line_fn:
            failed = airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[1]], run=run,
                skip_names={"montalu@subdev"})
        line_fn.assert_not_called()
        self.assertEqual(calls, [])
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][0], "montalu@subdev")

    def test_no_skip_names_behaves_exactly_as_before(self):
        # control: an absent/empty skip set must not change delivery at all.
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=FAKE_HOSTS, run=run, source=src)
        self.assertEqual(len(calls), 2)
        self.assertEqual(failed, [])

    # -- #341: cap retries PER connection (openssh default is 3 prompts) --

    def test_sshpass_calls_cap_password_prompts_to_one(self):
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"montalu": "branch-merge"}):
            airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[1]], run=run, source=src)
        argv, _kw = calls[0]
        self.assertIn("-o", argv)
        self.assertIn("NumberOfPasswordPrompts=1", argv)

    def test_identity_calls_use_batch_mode(self):
        src = self._source_with_key()
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                             {"marek": "branch-merge"}):
            airuleset.provision_subdev_soniox_key(
                hosts=[FAKE_HOSTS[2]], run=run, source=src)
        argv, _kw = calls[0]
        self.assertIn("BatchMode=yes", argv)


# ---------------------------------------------------------------------------
# #451 -- a managed NON-subdev host (newlevel@dev2) that still runs the
# meeting-analysis skill needs `~/.soniox.env` too. It carries an explicit
# `"soniox": True` marker, and the target filter admits it ALONGSIDE the
# AUTHORITY_BY_USER subdev accounts -- so it rides the SAME delivery loop
# (line-scoped source read, value via stdin, umask/chmod, retry, loud
# failure) with no parallel mechanism. The marker decouples soniox
# eligibility from merge-authority (which is what AUTHORITY_BY_USER means).
# ---------------------------------------------------------------------------

class TestSonioxFlaggedNonSubdevHost(TestCase):
    def _source_with_key(self, value=FAKE_KEY):
        d = Path(tempfile.mkdtemp())
        src = d / ".env"
        src.write_text("SONIOX_API_KEY=%s\n" % value)
        return src

    def test_flagged_host_receives_key_even_when_user_not_in_authority(self):
        # user "newlevel" is NOT in FAKE_AUTHORITY, but the `soniox` flag
        # makes it a target anyway -- exactly the dev2 case (#451).
        src = self._source_with_key()
        flagged = {"name": "dev2", "host": "1.2.3.4", "user": "newlevel",
                   "soniox": True, "repo_path": "~/devel/airuleset"}
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=[flagged], run=run, source=src)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 1,
                          "a soniox-flagged host must receive the key even "
                          "when its user is not in AUTHORITY_BY_USER")
        argv, kw = calls[0]
        # value via stdin, never argv -- the same guarantee every target has
        self.assertNotIn(FAKE_KEY, " ".join(str(a) for a in argv))
        self.assertIn(FAKE_KEY, kw.get("input", ""))
        self.assertIn("1.2.3.4", " ".join(str(a) for a in argv))

    def test_an_unflagged_non_authority_host_is_still_excluded(self):
        # control: WITHOUT the flag, a non-authority user is filtered out
        # (the pre-#451 behaviour, unchanged) -- proves the flag, not any
        # widening of the user check, is what admits dev2. A filtered host
        # never even triggers the source read.
        src = self._source_with_key()
        plain = {"name": "dev2", "host": "1.2.3.4", "user": "newlevel",
                 "repo_path": "~/devel/airuleset"}
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return _fake_cp()

        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY), \
                m.patch.object(cli_remote, "_soniox_key_line") as line_fn:
            failed = airuleset.provision_subdev_soniox_key(
                hosts=[plain], run=run, source=src)
        line_fn.assert_not_called()
        self.assertEqual(calls, [])
        self.assertEqual(failed, [])

    def test_dev2_carries_the_soniox_flag(self):
        # regression lock over the REAL REMOTE_HOSTS: the dev2 entry must
        # carry the flag (this is the one load-bearing guard that a future
        # edit removing it fails loudly on).
        dev2 = next(h for h in airuleset.REMOTE_HOSTS if h["name"] == "dev2")
        self.assertTrue(dev2.get("soniox"),
                        "dev2 must be flagged for soniox delivery (#451)")

    def test_dev2_is_a_real_delivery_target_via_the_production_filter(self):
        # drive the REAL provision function over the REAL REMOTE_HOSTS +
        # REAL AUTHORITY_BY_USER (neither patched) with a fake run and a
        # fake source -- proves the PRODUCTION filter itself (not a
        # re-derivation of it) admits dev2 alongside the subdev accounts,
        # with no regression to any existing target.
        src = self._source_with_key()
        contacted = []

        def run(argv, **kw):
            contacted.append(" ".join(str(a) for a in argv))
            return _fake_cp()

        failed = airuleset.provision_subdev_soniox_key(run=run, source=src)
        self.assertEqual(failed, [])
        joined = "\n".join(contacted)
        # dev2's real user@host -- the account this ticket exists to fix
        self.assertIn("newlevel@100.82.64.27", joined)
        # a couple of subdev stream accounts still contacted (no regression).
        # #537: montalu was renamed to montalu1 (live 2026-08-19) — the
        # numbered account is the real deployable target now.
        self.assertIn("montalu1@100.118.174.27", joined)
        self.assertIn("marek@100.118.174.27", joined)


# ---------------------------------------------------------------------------
# #358 adversarial-review F2 (MAJOR): the soniox delivery leg was exactly
# as exposed to gk's saturated-MaxStartups drop as the deploy leg, but had
# NO retry at all -- a transient connection failure permanently lost that
# account's soniox delivery, precisely the hole #358's own ticket text
# says the deploy loop's retry exists to close. Uses the SAME
# `_is_ssh_transient_failure` classifier and `SSH_RETRY_MAX_ATTEMPTS`/
# backoff as _deploy_to_all_remotes()'s own deploy loop.
# ---------------------------------------------------------------------------

class TestProvisionSubdevSonioxKeyRetry(TestCase):
    _TRANSIENT_ERR = ("kex_exchange_identification: read: Connection reset "
                       "by peer\n")

    def _source_with_key(self, value=FAKE_KEY):
        d = Path(tempfile.mkdtemp())
        src = d / ".env"
        src.write_text("SONIOX_API_KEY=%s\n" % value)
        return src

    def _scripted_run(self, calls, results):
        counters = {"n": 0}

        def run(argv, **kw):
            calls.append((list(argv), dict(kw)))
            idx = counters["n"]
            counters["n"] += 1
            rc, out, err = results[min(idx, len(results) - 1)]
            return m.Mock(returncode=rc, stdout=out, stderr=err)
        return run

    def test_transient_failure_is_retried_and_succeeds(self):
        src = self._source_with_key()
        calls = []
        results = [(255, "", self._TRANSIENT_ERR), (0, "ok", "")]
        hosts = [FAKE_HOSTS[1]]  # montalu@subdev only
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY), \
                m.patch("time.sleep"):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=hosts, run=self._scripted_run(calls, results),
                source=src)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 2,
                          "a transient connection failure must be retried, "
                          "not permanently lose the account's soniox key")

    def test_retry_is_bounded(self):
        src = self._source_with_key()
        calls = []
        results = [(255, "", self._TRANSIENT_ERR)] * 5
        hosts = [FAKE_HOSTS[1]]
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY), \
                m.patch("time.sleep"):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=hosts, run=self._scripted_run(calls, results),
                source=src)
        self.assertEqual(len(failed), 1)
        self.assertEqual(len(calls), airuleset.SSH_RETRY_MAX_ATTEMPTS,
                          "must stop at the bound, never keep retrying "
                          "forever")

    def test_ordinary_failure_is_never_retried(self):
        src = self._source_with_key()
        calls = []
        # rc=1 -- an ordinary failed write (disk full, permission
        # problem), NOT an ssh connection failure -- retrying it would
        # just repeat the same failing remote command for no benefit.
        results = [(1, "", "cat: write error\n"), (0, "ok", "")]
        hosts = [FAKE_HOSTS[1]]
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY):
            failed = airuleset.provision_subdev_soniox_key(
                hosts=hosts, run=self._scripted_run(calls, results),
                source=src)
        self.assertEqual(len(failed), 1)
        self.assertEqual(len(calls), 1)

    def test_backoff_is_applied_between_retries(self):
        src = self._source_with_key()
        calls = []
        results = [(255, "", self._TRANSIENT_ERR), (0, "ok", "")]
        hosts = [FAKE_HOSTS[1]]
        with m.patch.object(airuleset, "AUTHORITY_BY_USER", FAKE_AUTHORITY), \
                m.patch("time.sleep") as sleep_mock:
            airuleset.provision_subdev_soniox_key(
                hosts=hosts, run=self._scripted_run(calls, results),
                source=src)
        sleep_mock.assert_called_once()


# ---------------------------------------------------------------------------
# ffmpeg + ffprobe static binaries — the no-sudo subdev accounts have NO
# other install path (RUNTIME_DEPS' apt-get can never run there). #275
# adversarial-review MAJOR-1/2/3: extract.sh needs BOTH binaries, they must
# land somewhere the launcher's own PATH fix-up already covers (~/.local/bin,
# never ~/bin), and a hard-killed subprocess must never leave a
# truncated-but-"executable" binary at the final destination.
# ---------------------------------------------------------------------------

class TestBinaryReachable(TestCase):
    def test_false_when_dest_missing_and_not_on_path(self):
        d = Path(tempfile.mkdtemp()) / "does-not-exist" / "ffmpeg"
        with m.patch("shutil.which", return_value=None):
            self.assertFalse(airuleset._binary_reachable(d, "ffmpeg"))

    def test_true_when_dest_is_a_real_executable(self):
        # this is what recognizes montalu's own HAND install as already-done.
        d = Path(tempfile.mkdtemp()) / "ffmpeg"
        d.write_text("#!/bin/sh\n")
        d.chmod(0o755)
        with m.patch("shutil.which", return_value=None):
            self.assertTrue(airuleset._binary_reachable(d, "ffmpeg"))

    def test_a_non_executable_destination_falls_back_to_path(self):
        d = Path(tempfile.mkdtemp()) / "ffmpeg"
        d.write_text("not executable, e.g. a truncated download")
        with m.patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            self.assertTrue(airuleset._binary_reachable(d, "ffmpeg"))

    def test_true_via_system_path_when_our_own_destination_is_absent(self):
        d = Path(tempfile.mkdtemp()) / "does-not-exist"
        with m.patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            self.assertTrue(airuleset._binary_reachable(d, "ffmpeg"))

    def test_asks_which_for_the_exact_binary_name_given(self):
        seen = []

        def fake_which(name):
            seen.append(name)
            return None

        d = Path(tempfile.mkdtemp()) / "does-not-exist"
        with m.patch("shutil.which", side_effect=fake_which):
            airuleset._binary_reachable(d, "ffprobe")
        self.assertEqual(seen, ["ffprobe"])


class TestFfmpegAvailable(TestCase):
    def _paths(self):
        base = Path(tempfile.mkdtemp())
        return base / "ffmpeg", base / "ffprobe"

    def test_false_when_only_ffmpeg_is_present(self):
        # #275 review MAJOR-1: ffmpeg alone is NOT "available" -- extract.sh
        # needs ffprobe too.
        d, p = self._paths()
        d.write_text("#!/bin/sh\n")
        d.chmod(0o755)
        with m.patch("shutil.which", return_value=None):
            self.assertFalse(airuleset._ffmpeg_available(d, p))

    def test_false_when_only_ffprobe_is_present(self):
        d, p = self._paths()
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
        with m.patch("shutil.which", return_value=None):
            self.assertFalse(airuleset._ffmpeg_available(d, p))

    def test_true_when_both_are_present(self):
        d, p = self._paths()
        for f in (d, p):
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)
        with m.patch("shutil.which", return_value=None):
            self.assertTrue(airuleset._ffmpeg_available(d, p))

    def test_true_via_system_path_for_both_when_our_own_dests_are_absent(self):
        d, p = self._paths()
        with m.patch("shutil.which", return_value="/usr/bin/x"):
            self.assertTrue(airuleset._ffmpeg_available(d, p))

    def test_uses_the_dot_local_bin_destination_by_default(self):
        # #275 review MAJOR-2: never ~/bin -- only ~/.local/bin is on PATH
        # inside an actual Bash tool call (the launcher's own fix-up).
        self.assertEqual(airuleset.FFMPEG_STATIC_DEST.parts[-3:],
                         (".local", "bin", "ffmpeg"))
        self.assertEqual(airuleset.FFPROBE_STATIC_DEST.parts[-3:],
                         (".local", "bin", "ffprobe"))


class TestEnsureFfmpegStaticBinary(TestCase):
    def _dests(self):
        base = Path(tempfile.mkdtemp()) / "local" / "bin"
        return base / "ffmpeg", base / "ffprobe"

    def test_no_op_when_already_available(self):
        d, p = self._dests()
        with m.patch.object(cli_binary_installers, "_ffmpeg_available", return_value=True), \
                m.patch("subprocess.run") as run:
            airuleset.ensure_ffmpeg_static_binary(d, p)
        run.assert_not_called()

    def test_installs_via_one_subprocess_call_when_missing(self):
        d, p = self._dests()
        calls = {"n": 0}

        def fake_available(dest=None, probe_dest=None):
            calls["n"] += 1
            return calls["n"] > 1   # missing on the check, present after install

        with m.patch.object(cli_binary_installers, "_ffmpeg_available",
                             side_effect=fake_available), \
                m.patch("subprocess.run", return_value=_fake_cp()) as run:
            airuleset.ensure_ffmpeg_static_binary(d, p)
        run.assert_called_once()
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "bash")
        self.assertEqual(argv[1], "-c")
        script = argv[2]
        self.assertIn("set -o pipefail", script)
        self.assertIn(airuleset.FFMPEG_STATIC_URL, script)
        self.assertIn("tar -xJ", script)
        self.assertIn(str(d), script)
        self.assertIn(str(p), script)
        # #275 review MAJOR-3: extraction/chmod happen inside a SCRATCH dir
        # under the destination's own parent (never the final path directly,
        # and never a separate /tmp -- same filesystem is what makes the
        # final `mv` atomic), only `mv`d into place at the very end.
        self.assertIn("mktemp -d -p", script)
        self.assertIn('mv "$TMP/ffmpeg.new" %s' % d, script)
        self.assertIn('mv "$TMP/ffprobe.new" %s' % p, script)
        self.assertNotIn('cp "$MBIN" %s' % d, script,
                         "must never cp directly INTO the final destination")

    def test_install_failure_is_loud_but_non_fatal(self):
        out = StringIO()
        d, p = self._dests()
        with m.patch.object(cli_binary_installers, "_ffmpeg_available", return_value=False), \
                m.patch("subprocess.run",
                        return_value=_fake_cp(returncode=1, stderr="boom")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_ffmpeg_static_binary(d, p)   # must not raise
        self.assertIn("ffmpeg static install failed", out.getvalue())

    def test_install_exception_is_non_fatal(self):
        out = StringIO()
        d, p = self._dests()
        with m.patch.object(cli_binary_installers, "_ffmpeg_available", return_value=False), \
                m.patch("subprocess.run", side_effect=FileNotFoundError("curl")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_ffmpeg_static_binary(d, p)   # must not raise
        self.assertIn("ffmpeg static install skipped", out.getvalue())

    def test_a_real_extraction_writes_both_binaries_atomically(self):
        # #275 review MAJOR-3, end-to-end: build a REAL small tar.xz fixture
        # (no network) containing both "binaries", run the REAL bash script
        # this function constructs (only the curl step is stubbed, via a
        # fake `curl` earlier on PATH that just cats the fixture archive),
        # and confirm both land at their real final destinations, correctly
        # executable, with nothing left mid-way.
        import subprocess
        import tarfile

        d, p = self._dests()
        work = Path(tempfile.mkdtemp())
        srcdir = work / "ffmpeg-release-amd64-static"
        srcdir.mkdir()
        (srcdir / "ffmpeg").write_text("#!/bin/sh\necho fake-ffmpeg\n")
        (srcdir / "ffprobe").write_text("#!/bin/sh\necho fake-ffprobe\n")
        for f in (srcdir / "ffmpeg", srcdir / "ffprobe"):
            f.chmod(0o755)
        archive = work / "fixture.tar.xz"
        with tarfile.open(archive, "w:xz") as tf:
            tf.add(srcdir, arcname=srcdir.name)

        fake_bin = work / "fakebin"
        fake_bin.mkdir()
        curl_stub = fake_bin / "curl"
        curl_stub.write_text("#!/bin/sh\ncat %s\n" % archive)
        curl_stub.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = "%s:%s" % (fake_bin, env.get("PATH", ""))

        # captured BEFORE `subprocess.run` gets patched below -- calling the
        # (post-patch) name `subprocess.run` from inside this same helper
        # would recurse into the mock itself forever.
        _real_subprocess_run = subprocess.run

        def real_run(argv, **kw):
            kw.setdefault("env", env)
            return _real_subprocess_run(argv, **kw)

        with m.patch.object(cli_binary_installers, "_ffmpeg_available",
                             side_effect=lambda dd=None, pp=None:
                             (dd or d).is_file() and (pp or p).is_file()), \
                m.patch("subprocess.run", side_effect=real_run):
            airuleset.ensure_ffmpeg_static_binary(d, p)

        self.assertTrue(d.is_file() and os.access(d, os.X_OK))
        self.assertTrue(p.is_file() and os.access(p, os.X_OK))
        self.assertEqual(d.read_text(), "#!/bin/sh\necho fake-ffmpeg\n")
        self.assertEqual(p.read_text(), "#!/bin/sh\necho fake-ffprobe\n")
        # no leftover scratch dirs under the destination's parent
        leftovers = [c for c in d.parent.iterdir() if c.name not in ("ffmpeg", "ffprobe")]
        self.assertEqual(leftovers, [], "scratch dir must be cleaned up: %s" % leftovers)


if __name__ == "__main__":
    main()
