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
import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import statusbar  # noqa: E402


# --------------------------------------------------------------------------- #
# statusbar.user_waiting_numbers — reads the additive cache field.
# --------------------------------------------------------------------------- #
class TestUserWaitingNumbersReader(TestCase):
    def _write_cache(self, tmp, cwd, **fields):
        d = Path(tmp) / "tickets-status"
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


if __name__ == "__main__":
    main()
