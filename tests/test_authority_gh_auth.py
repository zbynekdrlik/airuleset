"""authority_gh_auth tests split out of the former monolithic `tests/test_authority_profiles.py`
(#830). Moved VERBATIM; shared helpers live in `tests/authority_testlib.py`."""
import os
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m
from authority_testlib import (  # noqa: E402
    airuleset,
)


class TestGhCallsCarryTheCredentialsFileToken(TestCase):
    """#181 I-6, CONFIRMED live on david@subdev 2026-07-30: `_gh_out` did not
    use `_gh_env()`, unlike `cmd_tickets_status`. That box has no GH_TOKEN in
    its shell env and authenticates per-command from ~/.git-credentials, so
    bare `gh` exits 4 and `slice-quals --count` printed "a gh query failed"
    and exited 1 — which means the fork-no-merge template's condition (B) can
    never hold there and that loop can never legitimately finish."""

    def test_slice_quals_works_on_a_box_whose_gh_is_only_credentials_file_authed(self):
        import os
        import subprocess
        import sys as _sys
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            Path(home, ".git-credentials").write_text(
                "https://kvaskodev:ghp_fake_token_for_this_test@github.com\n")
            gh = Path(bindir) / "gh"
            # Exactly david's box: unauthenticated without a token in the env.
            gh.write_text(
                "#!/usr/bin/env bash\n"
                'if [ -z "${GH_TOKEN:-}" ]; then\n'
                '  echo "To get started with GitHub CLI, please run: '
                'gh auth login" >&2\n'
                "  exit 4\n"
                "fi\n"
                'case "$*" in\n'
                '  *"api user"*) echo "kvaskodev";;\n'
                '  *assignee:@me*) echo \'[{"number":1,"title":"t",'
                '"createdAt":"2026-07-01T00:00:00Z"}]\';;\n'
                '  *) echo "[]";;\n'
                "esac\n")
            gh.chmod(0o755)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
            env.update(HOME=home, PATH="%s:%s" % (bindir, os.environ["PATH"]))
            r = subprocess.run(
                [_sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "slice-quals", "--count"],
                cwd=repo, capture_output=True, text=True, env=env)
            self.assertEqual(
                r.returncode, 0,
                "condition (B) is unsatisfiable on a credentials-file box: %s"
                % r.stderr)
            self.assertEqual(r.stdout.strip(), "1")


class TestGhEnvPrefersFreshMintOverGitCredentialsCorpse(TestCase):
    """#401: on an App-token box (odoo-erp#3281's gh-app-stream-tokens
    mechanism -- david2-4, marek, montalu/2/3/4) ~/.git-credentials can
    independently hold a ONE-SHOT snapshot of a 60-minute App installation
    token, written once by whatever process last wrote it and NEVER
    refreshed -- while the box's real live-refresh path
    (~/.config/gh-app-tokens/, refreshed every 45 min by a gatekeeper timer)
    sits right next to it. `_gh_env()` used to always prefer the static
    .git-credentials snapshot -- once that corpse token expired, EVERY gh
    call this file makes failed 401 forever, even though a live token sat
    right next to it (live-diagnosed on montalu3@subdev, 2026-08-12: the
    .git-credentials line was ~11.5h stale -- App tokens live 60 min --
    while ~/.config/gh-app-tokens/primary was ~31 min old and fully live).
    Fix: an App-token box (directory-presence detected via
    `_is_gh_app_token_box()`, #356's existing, local/static signal
    `_slice_quals()` already uses -- no network call) reads the fresh
    per-call token file instead of the git-credentials snapshot."""

    def test_app_token_box_uses_the_fresh_token_file_not_git_credentials(self):
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()
            (token_dir / "primary").write_text("fresh-app-token\n")
            Path(home, ".git-credentials").write_text(
                "https://x-access-token:corpse-token-dead@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir),
                                "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "fresh-app-token")

    def test_app_token_box_with_no_delivered_token_yet_falls_through_to_a_real_pat(self):
        # Adversarial-review MAJOR-1: `_is_gh_app_token_box()`'s own
        # docstring already documents a known residual -- a STRAY App-token
        # dir can exist on a genuine own-account PAT box (a misdirected
        # delivery, or an App->PAT migration leftover). An unconditional
        # early-return here (no fall-through) would silently kill that
        # box's real, working PAT auth the moment such a stray dir shows
        # up -- a new, mirror-image regression of #401 itself. The box's
        # own timer not having delivered a token YET must fall through to
        # the same .git-credentials logic every other box uses.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()          # provisioned, but no "primary" file yet
            Path(home, ".git-credentials").write_text(
                "https://montalu3:ghp_realpattoken@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "ghp_realpattoken")

    def test_app_token_box_with_only_a_corpse_credential_still_leaves_gh_token_unset(self):
        # The fall-through above must NEVER resurrect the #401 corpse: when
        # the ONLY thing .git-credentials holds is an x-access-token line
        # (the App mechanism's own fixed placeholder username), the second
        # belt still refuses it even though the App branch fell through to
        # reach it. This is the precise, un-masked version of the old
        # (adversarial-review MAJOR-2) test -- that one used an
        # x-access-token fixture too, which made it pass for EITHER "no
        # fall-through" or "fall through but still refuse", so it never
        # actually distinguished the two designs from each other.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()          # provisioned, but no "primary" file yet
            Path(home, ".git-credentials").write_text(
                "https://x-access-token:corpse-token-dead@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()
        self.assertNotIn("GH_TOKEN", env)

    def test_an_earlier_corpse_line_never_hides_a_genuine_pat_recorded_later(self):
        # Adversarial-review MINOR-2: the old `re.search` took the FIRST
        # github.com match only, so an x-access-token line appearing BEFORE
        # a real PAT line in .git-credentials would refuse the whole file
        # even though a genuine, usable credential sat right below it --
        # reachable on a box mid-migration where both lines coexist. The
        # scan must try every match, not just the first.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            missing = Path(home) / "gh-app-tokens"      # never created
            Path(home, ".git-credentials").write_text(
                "https://x-access-token:corpse-token-dead@github.com\n"
                "https://montalu3:ghp_realpattoken@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(missing),
                                "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "ghp_realpattoken")

    def test_a_malformed_token_file_degrades_to_no_token_never_crashes(self):
        # Adversarial-review MINOR-4/5: `primary` may be caught mid-write by
        # its own 45-min refresh timer, or otherwise corrupt -- a
        # UnicodeDecodeError (a ValueError subclass, NOT caught by a bare
        # `except OSError`) must degrade to "no token found", never
        # propagate and crash the caller. A multi-line file (e.g. a stray
        # trailing metadata line) must also never smuggle a 2nd line into
        # the token value -- only the first line is ever used.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()
            (token_dir / "primary").write_bytes(b"\xff\xfe not valid utf-8")
            Path(home, ".git-credentials").write_text(
                "https://montalu3:ghp_realpattoken@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()      # must not raise
        self.assertEqual(env.get("GH_TOKEN"), "ghp_realpattoken")

    def test_multi_line_token_file_only_ever_uses_the_first_line(self):
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()
            (token_dir / "primary").write_text("fresh-app-token\nexpires=soon\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir),
                                "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "fresh-app-token")

    def test_a_spoofed_subdomain_host_is_never_matched(self):
        # Adversarial-review THEORETICAL-3: the host match had no trailing
        # anchor, so a credentials line for a LOOKALIKE host
        # (github.com.evil.example) would match "github.com" as a mere
        # substring and hand that unrelated token to gh, which would then
        # send it to the real github.com in an Authorization header.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            missing = Path(home) / "gh-app-tokens"      # never created
            Path(home, ".git-credentials").write_text(
                "https://someone:gitlab-secret@github.com.evil.example\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(missing)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()
        self.assertFalse(env.get("GH_TOKEN"))

    def test_real_env_token_still_wins_on_an_app_token_box(self):
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            token_dir = Path(home) / "gh-app-tokens"
            token_dir.mkdir()
            (token_dir / "primary").write_text("fresh-app-token\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(token_dir),
                                "GH_TOKEN": "explicit-real-token"}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "explicit-real-token")

    def test_x_access_token_credential_is_never_authoritative_even_without_app_dir(self):
        # A stray x-access-token: line in .git-credentials structurally can
        # only be an App installation-token corpse (it's the FIXED username
        # GitHub requires for one) -- never a valid durable PAT, so it must
        # never be fed to gh even on a box _is_gh_app_token_box() doesn't
        # currently recognize (dir missing/relocated).
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            missing = Path(home) / "gh-app-tokens"      # never created
            Path(home, ".git-credentials").write_text(
                "https://x-access-token:corpse-token-dead@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(missing)},
                               clear=False):
                os.environ.pop("GH_TOKEN", None)
                os.environ.pop("GITHUB_TOKEN", None)
                env = airuleset._gh_env()
        self.assertFalse(env.get("GH_TOKEN"))

    def test_pat_box_git_credentials_fallback_is_completely_unaffected(self):
        # False-positive control: a genuine PAT box (david-style, no
        # App-token dir, non-x-access-token username) must behave BYTE-
        # IDENTICAL to before this fix.
        import tempfile
        import unittest.mock as mk

        with tempfile.TemporaryDirectory() as home:
            missing = Path(home) / "gh-app-tokens"      # never created
            Path(home, ".git-credentials").write_text(
                "https://kvaskodev:ghp_realpattoken@github.com\n")
            with mk.patch.dict(os.environ,
                               {"HOME": home, "GH_APP_TOKEN_DIR": str(missing),
                                "GH_TOKEN": "", "GITHUB_TOKEN": ""}):
                env = airuleset._gh_env()
        self.assertEqual(env.get("GH_TOKEN"), "ghp_realpattoken")


class TestCIRunnerAuth839(TestCase):
    """airuleset#839: after #827 made an unmapped unix user fail-SAFE to
    fork-no-merge, the GitHub-hosted CI runner (in neither registry) resolved
    fork-no-merge and broke ~33 tests that shell out to the FULL-authority-gated
    core-quals / tickets-status / run-card. This recognises the runner as a
    legitimate full-authority context for THIS repo's OWN CI by the UNSPOOFABLE
    identity — `pw_name == "runner"` (a hosted runner outside a container) OR
    uid 0 + `pw_name == "root"` (attempt 2: the `gate` job runs its pytest step
    INSIDE a `container: python:3.12` as root, never `runner`) — AND both
    `GITHUB_ACTIONS=true` AND `RUNNER_ENVIRONMENT=github-hosted`. It hardens
    `_current_user()` against the `$USER`/`$LOGNAME` env-spoof and proves no
    stream reaches `full` through the seam. The recognition + hardening tests are
    RED on the corresponding attempt's prior code (v0.1.128 for the runner arm,
    v0.1.129 for the container arm); the no-elevation guards (stream / non-runner
    / self-hosted / unmapped / registry) are green-by-design on both sides — they
    lock that the seam does NOT widen."""

    # ci-runner (GitHub-HOSTED) recognition needs all three signals.
    _HOSTED = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}

    # -- ci-runner recognition in the resolver ----------------------------- #
    def test_ci_runner_with_github_actions_resolves_full(self):
        with m.patch.object(airuleset, "_current_user", return_value="runner"):
            with m.patch.dict(os.environ, self._HOSTED):
                self.assertEqual(airuleset.resolve_authority(), "full")

    def test_ci_runner_explain_source_is_named_ci_runner(self):
        import cli_quals
        with m.patch.object(airuleset, "_current_user", return_value="runner"):
            with m.patch.dict(os.environ, self._HOSTED):
                profile, source, _raw = cli_quals._authority_decision()
        self.assertEqual(profile, "full")
        self.assertIn("ci-runner", source,
                      "the --explain source must NAME the ci-runner path (#839)")

    def test_cmd_authority_explain_output_names_ci_runner(self):
        # 🔵4: lock cmd_authority's PRINTED --explain map_val (the elif whose
        # order-correctness was comment-only), not just _authority_decision.
        import cli_quals
        import io
        import contextlib
        buf = io.StringIO()
        with m.patch.object(airuleset, "_current_user", return_value="runner"):
            with m.patch.dict(os.environ, self._HOSTED):
                with m.patch.object(cli_quals, "_authority_marker_raw",
                                    return_value=None):
                    with contextlib.redirect_stdout(buf):
                        cli_quals.cmd_authority(m.Mock(
                            maintainer_login=False, self_login=False,
                            app_bot_login=False, stream_label=False,
                            explain=True))
        out = buf.getvalue()
        self.assertIn("full", out)
        self.assertIn("ci-runner (GitHub-hosted)", out)
        self.assertIn("GitHub-hosted CI runner -> full", out)

    def test_runner_without_github_actions_stays_fork_no_merge(self):
        # #827 preserved (constraint 4): an unmapped `runner` on a REAL box
        # (no GITHUB_ACTIONS) is NOT full -- the conjunction is load-bearing.
        with m.patch.dict(os.environ):
            os.environ.pop("GITHUB_ACTIONS", None)
            os.environ.pop("RUNNER_ENVIRONMENT", None)
            with m.patch.object(airuleset, "_current_user", return_value="runner"):
                self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    def test_self_hosted_runner_named_runner_stays_fork_no_merge(self):
        # 🟡3: a SELF-hosted actions runner provisioned under a `runner` unix
        # account (owner misconfig, the one non-stream actor that could carry
        # that pw_name) is NOT elevated -- RUNNER_ENVIRONMENT distinguishes it.
        with m.patch.object(airuleset, "_current_user", return_value="runner"):
            with m.patch.dict(os.environ, {"GITHUB_ACTIONS": "true",
                                           "RUNNER_ENVIRONMENT": "self-hosted"}):
                self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    def test_runner_is_in_no_authority_registry(self):
        # 🟡3 belt-and-braces: the predicate is the ONLY path recognising
        # `runner`; it must never leak into a registry (which would grant a
        # self-hosted runner full authority, bypassing the github-hosted gate).
        self.assertNotIn("runner", airuleset.FULL_AUTHORITY_USERS)
        self.assertNotIn("runner", airuleset.AUTHORITY_BY_USER)

    def test_stream_with_hosted_env_cannot_reach_full(self):
        # constraint 5: a stream pw_name + the full hosted-runner env STILL
        # resolves its reduced profile -- the branch requires pw_name=="runner".
        with m.patch.object(airuleset, "_current_user", return_value="montalu1"):
            with m.patch.dict(os.environ, self._HOSTED):
                self.assertEqual(airuleset.resolve_authority(), "branch-merge")

    def test_mapped_runner_is_never_elevated_map_wins(self):
        # 🔵5: locks "placed AFTER the map". If `runner` were ever mapped
        # reduced, the map row wins over the ci-runner branch (defense-in-depth).
        with m.patch.object(airuleset, "_current_user", return_value="runner"):
            with m.patch.dict(airuleset.AUTHORITY_BY_USER,
                              {"runner": "fork-no-merge"}):
                with m.patch.dict(os.environ, self._HOSTED):
                    self.assertEqual(airuleset.resolve_authority(),
                                     "fork-no-merge")

    def test_unmapped_non_runner_with_hosted_env_is_fork_no_merge(self):
        # proves the branch REQUIRES pw_name == "runner", not just the env:
        # an unmapped non-runner identity with the hosted env stays fork-no-merge.
        with m.patch.object(airuleset, "_current_user", return_value="ci-bot"):
            with m.patch.dict(os.environ, self._HOSTED):
                self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    # -- ci-runner CONTAINER (uid-0) arm (airuleset#839 attempt 2) --------- #
    # The gate job runs `Pytest — hermetic subset` INSIDE a job-level
    # `container: python:3.12`, where the process is uid 0 / pw_name "root",
    # NEVER "runner" -- so the pw_name=="runner"-only recognition (v0.1.129)
    # never fired and 33 FULL-authority-gated tests failed on CI. Recognise
    # the container by os.getuid() == 0 under the SAME hosted-CI env.
    def test_container_root_uid0_resolves_full(self):
        # RED on v0.1.129 (pw_name=="runner" only) -> fork-no-merge.
        with m.patch.object(airuleset, "_current_user", return_value="root"):
            with m.patch("os.getuid", return_value=0):
                with m.patch.dict(os.environ, self._HOSTED):
                    self.assertEqual(airuleset.resolve_authority(), "full")

    def test_container_root_uid0_explain_names_container_source(self):
        # The uid-0 CONTAINER arm names a DISTINCT --explain source so the
        # printed log stays consistent with the resolved profile. RED on
        # v0.1.129.
        import cli_quals
        with m.patch.object(airuleset, "_current_user", return_value="root"):
            with m.patch("os.getuid", return_value=0):
                with m.patch.dict(os.environ, self._HOSTED):
                    profile, source, _raw = cli_quals._authority_decision()
        self.assertEqual(profile, "full")
        self.assertEqual(source, "ci-runner (GitHub-hosted, container)",
                         "the uid-0 container arm must name a DISTINCT source")

    def test_cmd_authority_explain_names_container_arm(self):
        # The PRINTED --explain map= annotation names the container arm too, so
        # it stays consistent with the (container-distinct) source line.
        import cli_quals
        import io
        import contextlib
        buf = io.StringIO()
        with m.patch.object(airuleset, "_current_user", return_value="root"):
            with m.patch("os.getuid", return_value=0):
                with m.patch.dict(os.environ, self._HOSTED):
                    with m.patch.object(cli_quals, "_authority_marker_raw",
                                        return_value=None):
                        with contextlib.redirect_stdout(buf):
                            cli_quals.cmd_authority(m.Mock(
                                maintainer_login=False, self_login=False,
                                app_bot_login=False, stream_label=False,
                                explain=True))
        out = buf.getvalue()
        self.assertIn("full", out)
        self.assertIn("ci-runner (GitHub-hosted, container)", out)
        self.assertIn("GitHub-hosted CI runner (container) -> full", out)

    def test_container_root_uid0_without_env_is_fork_no_merge(self):
        # The env conjuncts are load-bearing for the uid-0 arm TOO: uid 0
        # WITHOUT the hosted-CI env (a plain root shell on a REAL box) stays
        # fork-no-merge -- root is in NO registry -- never full, and the
        # source is NOT the ci-runner path. Green-by-design (locks that the
        # uid-0 arm cannot elevate a real root box).
        import cli_quals
        with m.patch.dict(os.environ):
            os.environ.pop("GITHUB_ACTIONS", None)
            os.environ.pop("RUNNER_ENVIRONMENT", None)
            with m.patch.object(airuleset, "_current_user", return_value="root"):
                with m.patch("os.getuid", return_value=0):
                    prof, source, _raw = cli_quals._authority_decision()
        self.assertEqual(prof, "fork-no-merge")
        self.assertNotIn("ci-runner", source)

    def test_root_is_in_no_authority_registry(self):
        # Belt-and-braces mirror of test_runner_is_in_no_authority_registry:
        # `root` is recognised ONLY by the uid-0 arm under the hosted-CI env,
        # never a registry membership (which would grant a plain root shell
        # full authority on ANY box).
        self.assertNotIn("root", airuleset.FULL_AUTHORITY_USERS)
        self.assertNotIn("root", airuleset.AUTHORITY_BY_USER)

    # -- _current_user() env-spoof hardening ------------------------------- #
    def test_current_user_ignores_env_spoof(self):
        # RED on getpass.getuser() (reads $USER/$LOGNAME/$USERNAME FIRST): the
        # real uid's pw_name must win over an attacker-set USER/LOGNAME, or a
        # stream could set USER=newlevel to self-elevate to `full`. The spoof
        # value MUST differ from the real pw_name or the test has no teeth on
        # the box literally named `newlevel` (#786) -- so we spoof to a value
        # that is provably not this uid's account.
        import pwd
        real = pwd.getpwuid(os.getuid()).pw_name
        spoof = "spoofed-not-a-real-account"
        self.assertNotEqual(real, spoof)
        with m.patch.dict(os.environ, {"USER": spoof,
                                       "LOGNAME": spoof,
                                       "USERNAME": spoof}):
            self.assertEqual(airuleset._current_user(), real)

    def test_current_user_passwdless_uid_returns_safe_sentinel(self):
        # Exotic: a uid with no passwd entry -> a uid<N> sentinel that is in
        # NEITHER registry (never "runner") -> fork-no-merge, NEVER a getpass
        # fallback (which would re-open the env-spoof at the one moment the
        # unspoofable source failed).
        with m.patch("pwd.getpwuid", side_effect=KeyError("no passwd entry")):
            with m.patch.dict(os.environ, {"USER": "newlevel",
                                           "LOGNAME": "newlevel"}):
                u = airuleset._current_user()
        self.assertNotEqual(u, "newlevel",
                            "must NOT fall back to the env-spoofable getpass")
        self.assertEqual(u, "uid%d" % os.getuid())
        self.assertNotIn(u, airuleset.FULL_AUTHORITY_USERS)
        self.assertNotIn(u, airuleset.AUTHORITY_BY_USER)
        with m.patch.object(airuleset, "_current_user", return_value=u):
            self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    # -- skill_names_for_user() uses the hardened identity ----------------- #
    def test_skill_names_for_user_ignores_env_spoof(self):
        # #839 Q4: skill_names_for_user must resolve identity via the hardened
        # _current_user() (uid-based), NOT raw getpass.getuser(). A stream
        # (montalu1) setting USER=newlevel must NOT get the full-authority-only
        # skills. RED on the direct getpass.getuser() call.
        import cli_deployer_glue
        with m.patch.object(airuleset, "_current_user", return_value="montalu1"):
            with m.patch.dict(os.environ, {"USER": "newlevel",
                                           "LOGNAME": "newlevel"}):
                names = cli_deployer_glue.skill_names_for_user()
        full_only = set(airuleset.SKILLS_FULL_AUTHORITY_ONLY)
        self.assertTrue(
            full_only.isdisjoint(names),
            "a reduced stream must not receive full-authority-only skills even "
            "with USER=newlevel spoofed -- got %r" % (full_only & set(names)))


if __name__ == "__main__":
    main()
