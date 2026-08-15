"""#461 — the durable owner-decision queue's daily re-ask (watchdog digest).

Covers `_fetch_owner_decision_tickets` (aggregate gh fetch across the box's
recently-worked repos, unmeasurable-vs-empty discipline), the
`reping_owner_decision_tickets` cadence/sleep-window/dedup logic, the digest
message body, the OWNER_DECISION_LABELS sync with cli_quals, and the run_once
wiring (the digest fires only when a real fetch is injected).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import watchdog as wd  # noqa: E402


DAY = 24 * 3600


def _daytime_now():
    """An epoch that is NOT inside the Europe/Bratislava sleep window."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026, 8, 14, 13, 0,
                    tzinfo=ZoneInfo("Europe/Bratislava")).timestamp()


def _night_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026, 8, 14, 3, 0,
                    tzinfo=ZoneInfo("Europe/Bratislava")).timestamp()


class _FakeProc:
    def __init__(self, returncode, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class TestFetchOwnerDecisionTickets(unittest.TestCase):
    def _patch_env(self):
        return mock.patch.object(wd, "_gh_env", lambda home=None, base=None: {})

    def test_none_when_every_repo_query_fails(self):
        # unmeasurable: an auth/network hiccup must NEVER look like "no
        # decisions pending" (which would record the day-bucket and silence
        # the digest).
        roots = {"/r/odoo-erp": "odoo-erp", "/r/camera-box": "camera-box"}
        with self._patch_env(), \
                mock.patch.object(wd, "_cache_repo_roots", lambda home=None: roots), \
                mock.patch("subprocess.run", return_value=_FakeProc(1, "")):
            self.assertIsNone(wd._fetch_owner_decision_tickets())

    def test_empty_list_when_queries_run_but_nothing_labelled(self):
        roots = {"/r/odoo-erp": "odoo-erp"}
        with self._patch_env(), \
                mock.patch.object(wd, "_cache_repo_roots", lambda home=None: roots), \
                mock.patch("subprocess.run", return_value=_FakeProc(0, "[]")):
            self.assertEqual(wd._fetch_owner_decision_tickets(), [])

    def test_empty_when_no_repos_at_all(self):
        with self._patch_env(), \
                mock.patch.object(wd, "_cache_repo_roots", lambda home=None: {}):
            # no roots => no queries => genuinely nothing pending, not "failed"
            self.assertEqual(wd._fetch_owner_decision_tickets(), [])

    def test_aggregates_and_sorts_across_repos(self):
        roots = {"/r/odoo-erp": "odoo-erp", "/r/camera-box": "camera-box"}

        def fake_run(argv, **kw):
            if kw.get("cwd") == "/r/odoo-erp":
                return _FakeProc(0, json.dumps([
                    {"number": 3020, "title": "decide X"},
                    {"number": 3018, "title": "decide Y"}]))
            return _FakeProc(0, json.dumps([{"number": 12, "title": "pick a"}]))

        with self._patch_env(), \
                mock.patch.object(wd, "_cache_repo_roots", lambda home=None: roots), \
                mock.patch("subprocess.run", side_effect=fake_run):
            got = wd._fetch_owner_decision_tickets()
        self.assertEqual(got, [
            ("camera-box", 12, "pick a"),
            ("odoo-erp", 3018, "decide Y"),
            ("odoo-erp", 3020, "decide X"),
        ])

    def test_one_failing_repo_never_kills_the_whole_digest(self):
        roots = {"/r/ok": "ok", "/r/bad": "bad"}

        def fake_run(argv, **kw):
            if kw.get("cwd") == "/r/bad":
                raise OSError("boom")
            return _FakeProc(0, json.dumps([{"number": 5, "title": "t"}]))

        with self._patch_env(), \
                mock.patch.object(wd, "_cache_repo_roots", lambda home=None: roots), \
                mock.patch("subprocess.run", side_effect=fake_run):
            got = wd._fetch_owner_decision_tickets()
        # the good repo still contributes; the bad one is skipped, not fatal
        self.assertEqual(got, [("ok", 5, "t")])

    def test_search_uses_comma_or_labels_and_skip_exclusion(self):
        captured = {}
        roots = {"/r/x": "x"}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return _FakeProc(0, "[]")

        with self._patch_env(), \
                mock.patch.object(wd, "_cache_repo_roots", lambda home=None: roots), \
                mock.patch("subprocess.run", side_effect=fake_run):
            wd._fetch_owner_decision_tickets()
        argv = captured["argv"]
        i = argv.index("--search")
        search = argv[i + 1]
        self.assertIn("label:needs-answer,needs-decision", search)
        self.assertIn("-label:autopilot-skip", search)
        self.assertIn("-label:ops-channel", search)


class TestDigestBlock(unittest.TestCase):
    def test_lists_tickets_with_repo_number_and_title(self):
        block = wd._owner_decision_digest_block([
            ("odoo-erp", 3018, "decide the box"),
            ("camera-box", 12, "pick a codec"),
        ])
        self.assertIn("odoo-erp #3018", block)
        self.assertIn("decide the box", block)
        self.assertIn("camera-box #12", block)
        # Slovak, self-contained framing — NOT a session ❓ marker keyword.
        self.assertIn("rozhodnut", block.lower())
        self.assertNotIn("NEEDS YOU", block)

    def test_caps_a_huge_list_and_reports_the_remainder(self):
        many = [("r", n, "t%d" % n) for n in range(50)]
        block = wd._owner_decision_digest_block(many, limit=15)
        # 15 shown, remainder summarised — never a wall of 50 lines.
        self.assertLessEqual(block.count("\n- "), 16)  # 15 tickets + the "…"
        self.assertIn("35", block)  # 50 - 15 = 35 more

    def test_long_title_is_truncated(self):
        block = wd._owner_decision_digest_block([("r", 1, "z" * 200)])
        self.assertNotIn("z" * 120, block)

    def test_default_worst_case_stays_under_notify_max_content(self):
        # notify.send truncates a forwarded block at _MAX_CONTENT (1900). The
        # default `limit` must keep the WORST case -- long repo names + full
        # 80-char titles -- comfortably under that, or the tail tickets AND the
        # 'Odpovedz prosím' footer would be silently cut mid-truncation. A
        # revert of the default from 12 back to 15 makes this fail.
        import notify
        many = [("automatizacie-montalu-x", 9999999, "T" * 200)
                for _ in range(60)]
        block = wd._owner_decision_digest_block(many)   # production default
        self.assertLess(len(block), notify._MAX_CONTENT,
                        "digest %d >= _MAX_CONTENT %d -- footer would be cut"
                        % (len(block), notify._MAX_CONTENT))
        # The cap still fired and stayed honest about the remainder.
        self.assertIn("ďalších", block)


class TestRepingOwnerDecisionTickets(unittest.TestCase):
    def _spy_send(self):
        calls = []

        def send(body, **k):
            calls.append((body, k))
            return "sent"
        return send, calls

    def test_no_op_without_fetch(self):
        send, calls = self._spy_send()
        state = {}
        out = wd.reping_owner_decision_tickets(
            _daytime_now(), send, state, fetch=None)
        self.assertEqual(out, [])
        self.assertEqual(calls, [])
        self.assertNotIn("owner_decision_digest", state)

    def test_no_op_without_send_fn(self):
        state = {}
        out = wd.reping_owner_decision_tickets(
            _daytime_now(), None, state, fetch=lambda home=None: [])
        self.assertEqual(out, [])

    def test_fires_one_ping_and_records_bucket(self):
        send, calls = self._spy_send()
        state = {}
        persisted = []
        now = _daytime_now()
        tickets = [("odoo-erp", 3018, "decide")]
        wd.reping_owner_decision_tickets(
            now, send, state, fetch=lambda home=None: tickets,
            account_owner="zbynek", persist=lambda: persisted.append(1),
            authority="full")
        # exactly ONE ping
        self.assertEqual(len(calls), 1)
        body, k = calls[0]
        self.assertEqual(k.get("kind"), "questions")
        self.assertEqual(k.get("owner"), "zbynek")
        self.assertEqual(k.get("dedup_key"),
                         "owner-decision-digest:%d" % int(now // DAY))
        self.assertIn("odoo-erp #3018", body)
        # bucket recorded + persisted (cadence survives a kill, job-8 pattern)
        self.assertEqual(state["owner_decision_digest"]["bucket"],
                         int(now // DAY))
        self.assertEqual(persisted, [1])

    def test_second_call_same_bucket_is_deduped_no_refetch(self):
        send, calls = self._spy_send()
        state = {}
        now = _daytime_now()
        fetch_calls = []

        def fetch(home=None):
            fetch_calls.append(1)
            return [("r", 1, "t")]

        wd.reping_owner_decision_tickets(now, send, state,
                                         fetch=fetch, account_owner="z",
                                         authority="full")
        # a SECOND sweep in the same day-bucket must NOT fetch or send again
        wd.reping_owner_decision_tickets(now + 60, send, state,
                                         fetch=fetch, account_owner="z",
                                         authority="full")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(fetch_calls), 1)

    def test_deferred_in_sleep_window_records_nothing(self):
        send, calls = self._spy_send()
        state = {}
        fetch_calls = []
        out = wd.reping_owner_decision_tickets(
            _night_now(), send, state,
            fetch=lambda home=None: fetch_calls.append(1) or [("r", 1, "t")],
            account_owner="z", authority="full")
        self.assertEqual(calls, [])
        self.assertEqual(fetch_calls, [])           # no network at night
        self.assertNotIn("owner_decision_digest", state)  # retried after 06:00
        self.assertTrue(any("sleep-window" in ln for ln in out))

    def test_unmeasurable_fetch_does_not_record_bucket(self):
        send, calls = self._spy_send()
        state = {}
        out = wd.reping_owner_decision_tickets(
            _daytime_now(), send, state,
            fetch=lambda home=None: None, account_owner="z", authority="full")
        self.assertEqual(calls, [])
        self.assertNotIn("owner_decision_digest", state)  # retry next sweep
        self.assertTrue(any("unmeasurable" in ln for ln in out))

    def test_empty_fetch_records_bucket_but_sends_nothing(self):
        send, calls = self._spy_send()
        state = {}
        now = _daytime_now()
        out = wd.reping_owner_decision_tickets(
            now, send, state, fetch=lambda home=None: [], account_owner="z",
            authority="full")
        self.assertEqual(calls, [])                         # nothing pending
        self.assertEqual(state["owner_decision_digest"]["bucket"],
                         int(now // DAY))                   # but one fetch/day
        self.assertTrue(any("0 pending" in ln for ln in out))

    def test_falls_back_to_resolve_owner_when_no_account_owner(self):
        send, calls = self._spy_send()
        state = {}
        with mock.patch("notify.resolve_owner", return_value="marek"):
            wd.reping_owner_decision_tickets(
                _daytime_now(), send, state,
                fetch=lambda home=None: [("r", 1, "t")], account_owner="",
                authority="full")
        self.assertEqual(calls[0][1].get("owner"), "marek")


class TestReducedAuthorityBoxSkipsDigest(unittest.TestCase):
    """#489 — a reduced-authority (sub-dev) box must NEVER send the box-wide
    owner-decision digest. Its box OWNER is an external sub-dev (david = CEO
    slovnormalu), but the repo-scoped tickets belong to the gatekeeper/boss —
    so the repo-wide digest leaked internal odoo-erp tickets to david's Discord
    thread with a false 'these N tickets wait on YOUR decision'. The digest runs
    ONLY on a full-authority (owner = the genuine decision recipient) box."""

    def _spy_send(self):
        calls = []

        def send(body, **k):
            calls.append((k.get("owner"), body))
            return "sent"
        return send, calls

    def test_reduced_authority_box_never_sends_repo_wide_digest(self):
        # RED on today's code: reping has no authority gate, so a reduced-
        # authority box (box OS user = david, fork-no-merge) sends the repo-wide
        # digest to the external owner AND hits the repo-wide fetch.
        import airuleset
        send, calls = self._spy_send()
        state = {}
        repo_wide = [("odoo-erp", 3020, "provizie obchodnikov"),
                     ("odoo-erp", 3018, "Money migracia")]
        fetch_calls = []

        def fetch(home=None):
            fetch_calls.append(1)
            return repo_wide

        with mock.patch.object(airuleset, "_current_user", lambda: "david"):
            wd.reping_owner_decision_tickets(
                _daytime_now(), send, state, fetch=fetch, account_owner="david")
        self.assertEqual(calls, [],
                         "digest leaked to a reduced-authority box owner")
        self.assertEqual(fetch_calls, [],
                         "digest must skip BEFORE the box-wide repo fetch")

    def test_default_box_authority_resolver_skips_reduced_box(self):
        # No explicit authority=: the default _box_authority() reads the box OS
        # user's AUTHORITY_BY_USER entry. A real reduced user (david) -> skip.
        import airuleset
        send, calls = self._spy_send()
        state = {}
        with mock.patch.object(airuleset, "_current_user", lambda: "david"):
            out = wd.reping_owner_decision_tickets(
                _daytime_now(), send, state,
                fetch=lambda home=None: [("odoo-erp", 1, "t")],
                account_owner="david")
        self.assertEqual(calls, [])
        self.assertTrue(any("reduced-authority" in ln for ln in out))

    def test_branch_merge_box_also_skips(self):
        # EVERY non-full profile skips, not just fork-no-merge (marek/montalu are
        # branch-merge sub-devs; the tickets still belong to the boss).
        send, calls = self._spy_send()
        state = {}
        out = wd.reping_owner_decision_tickets(
            _daytime_now(), send, state,
            fetch=lambda home=None: [("odoo-erp", 1, "t")],
            account_owner="marek", authority="branch-merge")
        self.assertEqual(calls, [])
        self.assertTrue(any("branch-merge" in ln for ln in out))

    def test_reduced_skip_stamps_bucket_and_dedups_second_sweep(self):
        # The skip records the day-bucket, so the reduced box logs the skip at
        # most ONCE per day and every later same-bucket sweep is a silent dedup
        # (no per-60s-sweep log noise, no repeated fetch).
        send, calls = self._spy_send()
        state = {}
        now = _daytime_now()
        fetch_calls = []

        def fetch(home=None):
            fetch_calls.append(1)
            return [("odoo-erp", 1, "t")]

        out1 = wd.reping_owner_decision_tickets(
            now, send, state, fetch=fetch, account_owner="david",
            authority="fork-no-merge")
        out2 = wd.reping_owner_decision_tickets(
            now + 60, send, state, fetch=fetch, account_owner="david",
            authority="fork-no-merge")
        self.assertTrue(any("reduced-authority" in ln for ln in out1))
        self.assertEqual(out2, [])                 # deduped, silent
        self.assertEqual(calls, [])                # never sent
        self.assertEqual(fetch_calls, [])          # never fetched
        self.assertEqual(state["owner_decision_digest"]["bucket"],
                         int(now // DAY))

    def test_full_authority_default_resolver_still_sends(self):
        # gk/dev1/dev2 keep working: an UNMAPPED box user resolves to "full" via
        # the default _box_authority() resolver, and the digest sends as before.
        import airuleset
        send, calls = self._spy_send()
        state = {}
        with mock.patch.object(airuleset, "_current_user",
                               lambda: "newlevel-not-in-map"):
            wd.reping_owner_decision_tickets(
                _daytime_now(), send, state,
                fetch=lambda home=None: [("odoo-erp", 3018, "decide")],
                account_owner="zbynek")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "zbynek")


class TestOwnerDecisionLabelsInSync(unittest.TestCase):
    def test_labels_match_cli_quals_user_waiting(self):
        import cli_quals
        self.assertEqual(tuple(wd.OWNER_DECISION_LABELS),
                         tuple(cli_quals.USER_WAITING_LABELS))


class TestRunOnceWiring(unittest.TestCase):
    def _tmp_json(self):
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        f.write(b"{}")
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        return f.name

    def _tmp_dir(self):
        import shutil
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d

    def test_digest_fetch_is_reached_when_wired(self):
        """The digest fires from run_once's #368 daily-reask section only when a
        real fetch is injected (jobs 8/11 network-free-tests pattern). Pinned to a
        full-authority box (#489) so the reach test is hermetic regardless of the
        test box's own OS user."""
        fetch_calls = []

        def spy_fetch(home=None):
            fetch_calls.append(1)
            return []       # measurable, empty → no send, just records bucket

        with mock.patch.object(wd, "_box_authority", lambda: "full"):
            wd.run_once(now=_daytime_now(), dry_run=False,
                        run=lambda argv, **k: "", send_fn=lambda *a, **k: "sent",
                        projects_dir=self._tmp_dir(), state_path=self._tmp_json(),
                        questions_path=self._tmp_json(),
                        owner_decision_fetch=spy_fetch)
        self.assertEqual(fetch_calls, [1],
                         "run_once must reach the owner-decision digest fetch "
                         "when it is wired")

    def test_digest_not_reached_when_fetch_absent(self):
        # No owner_decision_fetch → the digest section is a no-op (default None).
        logs = wd.run_once(now=_daytime_now(), dry_run=False,
                           run=lambda argv, **k: "", send_fn=lambda *a, **k: "sent",
                           projects_dir=self._tmp_dir(), state_path=self._tmp_json(),
                           questions_path=self._tmp_json())
        self.assertFalse(any("owner-decision-digest" in ln for ln in logs))


if __name__ == "__main__":
    unittest.main()
