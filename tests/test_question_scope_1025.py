"""#1025 half 2 — a `❓ ASKED`/`❓ NEEDS YOU` turn that NAMES a same-repo `#N`
must point at a ticket THIS box's `U` (owner-court) surface actually shows;
otherwise the owner sees `U 0`/`U N` without that ticket and has nowhere to
click. The check is a thin adapter (gate-family #1020) over
`cli_quals.question_ticket_in_u`, cache-first (zero gh when fresh) with a
SINGLE gh-search fallback and fail-OPEN on any gh error.

Covers:
  * statusbar.user_waiting_numbers — the additive cache field reader.
  * cli_quals.question_ticket_in_u — in_u / not_in_u / unmeasurable, cache
    fast-allow + one-gh fallback + fail-open.
  * gates.questionscope — the gate decision (block/allow) and exit codes.
"""
import json
import subprocess
import sys
import time
import unittest.mock as m
import uuid
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_quals  # noqa: E402
import statusbar  # noqa: E402


# --------------------------------------------------------------------------- #
# statusbar.user_waiting_numbers — reads the additive cache field.
# --------------------------------------------------------------------------- #
class TestUserWaitingNumbersReader(TestCase):
    def _write_cache(self, tmp, cwd, **fields):
        d = statusbar.cache_dir(tmp)
        d.mkdir(parents=True, exist_ok=True)
        entry = {"ts": int(time.time()), "root": cwd}
        entry.update(fields)
        (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(entry))
        return tmp

    def test_reads_numbers_list_as_a_set(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = self._write_cache(tmp, "/repo", user_waiting=2,
                                     user_waiting_numbers=[6883, 500])
            nums, ts = statusbar.user_waiting_numbers("/repo", home=home)
            self.assertEqual(nums, {6883, 500})
            self.assertIsInstance(ts, (int, float))

    def test_absent_field_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = self._write_cache(tmp, "/repo", user_waiting=0)
            nums, ts = statusbar.user_waiting_numbers("/repo", home=home)
            self.assertIsNone(nums)

    def test_missing_cache_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            nums, ts = statusbar.user_waiting_numbers("/repo", home=tmp)
            self.assertIsNone(nums)
            self.assertIsNone(ts)


# --------------------------------------------------------------------------- #
# cli_quals.question_ticket_in_u — the membership decision.
# --------------------------------------------------------------------------- #
def _runner_returning(numbers):
    """A fallback runner stand-in: any argv returns a gh issue-list JSON of
    {number} for `numbers`."""
    def r(argv, cwd):
        return json.dumps([{"number": n} for n in numbers])
    return r


def _runner_error():
    def r(argv, cwd):
        return None            # gh failure
    return r


class TestQuestionTicketInU(TestCase):
    def test_cache_fresh_and_member_is_in_u_zero_gh(self):
        # Cache fresh + ref present → in_u without ever calling the runner.
        def _no_gh(argv, cwd):
            raise AssertionError("must not call gh when cache confirms membership")
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=({6883, 500}, time.time())):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo", runner=_no_gh, now=time.time())
        self.assertEqual(v, "in_u")

    def test_not_in_cache_but_label_present_via_gh_is_in_u(self):
        # Cache fresh but missing the ref (label just added / out of the footer's
        # last snapshot) → the one-gh fallback finds the label → in_u (no false block).
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=({500}, time.time())):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo", runner=_runner_returning([6883, 500]),
                now=time.time())
        self.assertEqual(v, "in_u")

    def test_not_in_cache_and_no_label_is_not_in_u(self):
        # Cache without the ref + gh confirms the ref carries no U-label → block.
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=(set(), time.time())):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo", runner=_runner_returning([500]),
                now=time.time())
        self.assertEqual(v, "not_in_u")

    def test_no_cache_and_gh_finds_the_label_is_in_u(self):
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=(None, None)):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo", runner=_runner_returning([6883]),
                now=time.time())
        self.assertEqual(v, "in_u")

    def test_gh_error_is_unmeasurable_fail_open(self):
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=(None, None)):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo", runner=_runner_error(), now=time.time())
        self.assertEqual(v, "unmeasurable")

    def test_stale_cache_does_not_fast_allow_falls_to_gh(self):
        # A cache older than the freshness window is not trusted for fast-allow.
        old = time.time() - 10_000
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=({6883}, old)):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo", runner=_runner_returning([]),
                now=time.time(), cache_max_age_s=120)
        self.assertEqual(v, "not_in_u")

    def test_any_of_several_refs_in_u_allows(self):
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=({500}, time.time())):
            v = cli_quals.question_ticket_in_u(
                [6883, 500], "/repo", runner=_runner_returning([500]),
                now=time.time())
        self.assertEqual(v, "in_u")


# --------------------------------------------------------------------------- #
# gates.questionscope — the gate decision + exit codes.
# --------------------------------------------------------------------------- #
import gates.questionscope as qs  # noqa: E402


def _payload(msg, cwd="/repo", sid=None):
    return json.dumps({"last_assistant_message": msg,
                       "session_id": sid or ("qs-" + uuid.uuid4().hex[:8]),
                       "cwd": cwd})


ASKED_6883 = (
    "**Otázka — projekt odoo-erp (Odoo ERP):** V infra tickete #6883 je "
    "otvorená otázka na teba.\n\n"
    "• A (odporúčam) — dôsledok\n• B — dôsledok\n\n"
    "❓ ASKED: rozhodni A alebo B pre #6883?"
)


class TestGateDecision(TestCase):
    def test_names_ticket_absent_from_u_blocks(self):
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="not_in_u"):
            block, reason = qs.decide(_payload(ASKED_6883))
        self.assertTrue(block)
        self.assertIn("6883", reason)

    def test_names_ticket_present_in_u_allows(self):
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="in_u"):
            block, reason = qs.decide(_payload(ASKED_6883))
        self.assertFalse(block)

    def test_gh_error_unmeasurable_allows(self):
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="unmeasurable"):
            block, reason = qs.decide(_payload(ASKED_6883))
        self.assertFalse(block)

    def test_no_marker_allows(self):
        block, reason = qs.decide(_payload("Just a status update about #6883.\n✅ DONE"))
        self.assertFalse(block)

    def test_no_ticket_ref_allows(self):
        msg = ("**Otázka — projekt X:** všeobecná otázka.\n\n"
               "❓ NEEDS YOU: schváliš nasadenie na PROD?")
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="not_in_u") as p:
            block, reason = qs.decide(_payload(msg))
        self.assertFalse(block)
        p.assert_not_called()          # no #N → never consults U

    def test_cross_repo_ref_is_not_treated_as_a_bare_ref(self):
        msg = ("**Otázka — projekt airuleset:** context odoo-erp#6883.\n\n"
               "❓ NEEDS YOU: schváliš X?")
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="not_in_u") as p:
            block, reason = qs.decide(_payload(msg))
        self.assertFalse(block)
        p.assert_not_called()


class TestGateSubprocessExitCodes(TestCase):
    """Wiring smoke tests via `python3 -m gates.questionscope` with a
    cache-forced verdict (in_u = zero gh)."""

    def _run(self, payload, home):
        import os
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO) + ":" + env.get("PYTHONPATH", "")
        env["HOME"] = home
        return subprocess.run(
            [sys.executable, "-m", "gates.questionscope"],
            input=payload, capture_output=True, text=True, timeout=30, env=env)

    def _write_cache(self, home, cwd, **fields):
        d = Path(home) / ".claude" / "tickets-status"
        d.mkdir(parents=True, exist_ok=True)
        entry = {"ts": int(time.time()), "root": cwd}
        entry.update(fields)
        (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(entry))

    def test_in_u_via_fresh_cache_exits_0(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            self._write_cache(home, "/repo", user_waiting=1,
                              user_waiting_numbers=[6883])
            r = self._run(_payload(ASKED_6883, cwd="/repo"), home)
        self.assertEqual(r.returncode, 0)

    def test_no_marker_exits_0(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            r = self._run(_payload("plain text\n✅ DONE", cwd="/repo"), home)
        self.assertEqual(r.returncode, 0)


# --------------------------------------------------------------------------- #
# hooks/stop-check-question-quality.sh — the thin-adapter wiring (#1025 item 2).
# A ❓ ASKED naming a #N absent from U → exit 2; present → exit 0; gh error →
# exit 0. Membership is forced via the HOME cache (present) or a fake `gh` on
# PATH (absent / gh-error), so no network is touched.
# --------------------------------------------------------------------------- #
HOOK = REPO / "hooks" / "stop-check-question-quality.sh"

# A well-formed ❓ ASKED block naming #6883 (passes the shape/bundle checks so
# execution reaches the #1025 scope check).
HOOK_ASKED = (
    "**Otázka — projekt airuleset (Odoo/airuleset fleet):** V infra tickete "
    "#6883 je otvorená otázka na teba, ktorú treba rozhodnúť.\n\n"
    "• A (odporúčam) — dôsledok A\n• B — dôsledok B\n\n"
    "❓ ASKED: rozhodni A alebo B pre #6883?"
)


def _fake_gh_dir(tmp, mode):
    """A dir holding a fake `gh` script; prepend to PATH. mode:
      'empty' → `gh issue list ... --json number` prints `[]` (not in U)
      'error' → exits 1 (unmeasurable → fail-open)."""
    import stat
    d = Path(tmp) / "fakebin"
    d.mkdir(parents=True, exist_ok=True)
    if mode == "error":
        body = "#!/usr/bin/env bash\nexit 1\n"
    else:
        body = "#!/usr/bin/env bash\necho '[]'\n"
    gh = d / "gh"
    gh.write_text(body)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(d)


class TestHookScopeGate(TestCase):
    def _run_hook(self, msg, home, cwd, path_prefix=None):
        import os
        sid = "qs1025-" + uuid.uuid4().hex[:10]
        for f in ("/tmp/airuleset-question-quality-block-" + sid,
                  "/tmp/claude-discord-lastq-" + sid,
                  "/tmp/claude-user-active-" + sid,
                  "/tmp/claude-lastq-refs-" + sid):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        env = dict(os.environ)
        env["HOME"] = home
        if path_prefix:
            env["PATH"] = path_prefix + ":" + env.get("PATH", "")
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg,
                              "session_id": sid, "cwd": cwd}),
            capture_output=True, text=True, timeout=40, env=env)

    def _repo(self, home):
        r = Path(home) / "repo"
        r.mkdir(parents=True, exist_ok=True)
        return str(r)

    def _write_cache(self, home, cwd, **fields):
        d = Path(home) / ".claude" / "tickets-status"
        d.mkdir(parents=True, exist_ok=True)
        entry = {"ts": int(time.time()), "root": cwd}
        entry.update(fields)
        (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(entry))

    def test_present_in_u_via_cache_exits_0(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            cwd = self._repo(home)
            self._write_cache(home, cwd, user_waiting=1,
                              user_waiting_numbers=[6883])
            r = self._run_hook(HOOK_ASKED, home, cwd)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_absent_from_u_blocks_exit_2(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            cwd = self._repo(home)
            # No cache entry for #6883 + a fake gh returning [] → not_in_u.
            fake = _fake_gh_dir(home, "empty")
            r = self._run_hook(HOOK_ASKED, home, cwd, path_prefix=fake)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("6883", r.stderr)

    def test_gh_error_exits_0_fail_open(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            cwd = self._repo(home)
            fake = _fake_gh_dir(home, "error")
            r = self._run_hook(HOOK_ASKED, home, cwd, path_prefix=fake)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_ticket_ref_exits_0(self):
        import tempfile
        msg = ("**Otázka — projekt airuleset (fleet):** všeobecná otázka bez "
               "ticketu.\n\n• A (odporúčam) — X\n• B — Y\n\n"
               "❓ ASKED: schváliš nasadenie na PROD?")
        with tempfile.TemporaryDirectory() as home:
            cwd = self._repo(home)
            r = self._run_hook(msg, home, cwd)
        self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# cmd_tickets_status --refresh WRITES user_waiting_numbers (the cache field the
# stop gate's fast-allow reads). Drives the full refresh as a subprocess with a
# fake gh (mirrors test_question_slice_gap_948's harness).
# --------------------------------------------------------------------------- #
class TestCacheWritesUserWaitingNumbers(TestCase):
    def _fake_gh(self, bindir):
        import airuleset as _a
        user = _a._current_user()
        stream_label = "stream:%s" % user
        rows = json.dumps([
            {"number": 10, "title": "workable",
             "createdAt": "2026-01-01T00:00:00Z",
             "labels": [{"name": stream_label}]},
            {"number": 77, "title": "asked",
             "createdAt": "2026-01-02T00:00:00Z",
             "labels": [{"name": stream_label}, {"name": "needs-answer"}]},
        ])
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/odoo-erp";;\n'
            '  *rate_limit*) echo \'{"resources":{"graphql":{"remaining":5000}}}\';;\n'
            '  *"label:stream:"*autopilot-skip*) echo "[]";;\n'
            "  *\"label:stream:\"*) echo '%s';;\n" % rows +
            '  *) echo "[]";;\n'
            'esac\n')
        gh.chmod(0o755)

    def test_refresh_writes_user_waiting_numbers(self):
        import os
        import subprocess as sp
        import airuleset as _a
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            sp.run(["git", "init", "-q"], cwd=repo, check=True)
            Path(repo, "CLAUDE.md").write_text(
                "<!-- airuleset:authority=fork-no-merge -->\n")
            self._fake_gh(bindir)
            r = sp.run(
                [sys.executable, str(_a.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": "%s:%s" % (bindir, os.environ["PATH"])})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads(
                (statusbar.cache_dir(home)
                 / (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertIn("user_waiting_numbers", cache)
            self.assertIn(77, cache["user_waiting_numbers"])
            self.assertNotIn(10, cache["user_waiting_numbers"])  # workable, not U


if __name__ == "__main__":
    main()
