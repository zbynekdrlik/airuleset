import os
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

class TestConstants(unittest.TestCase):
    def test_constants_present(self):
        from board import (PORT, BOARD_HOST_IP, REPORT_TIMEOUT, CIRCUIT_BREAKER_S,
                           FLUSH_CAP, QUEUE_MAX_BYTES, QUEUE_TTL_S, BODY_MAX,
                           EVENT_CAP_PER_RUN, STALE_ACTIVE_S, STALE_WAIT_S,
                           GH_POLL_FLOOR_S, TERMINAL_PHASES)
        self.assertEqual(PORT, 8787)
        self.assertEqual(BOARD_HOST_IP, os.getenv("BOARD_HOST", "10.77.9.21"))
        self.assertEqual(REPORT_TIMEOUT, 2)
        self.assertIn("done", TERMINAL_PHASES)
        self.assertIn("obsolete-closed", TERMINAL_PHASES)


class TestGate(unittest.TestCase):
    def test_required_set_and_source(self):
        from board.gate import REQUIRED_GATES, source_of
        self.assertEqual(source_of("ci"), "verified")
        self.assertEqual(source_of("mergeable"), "verified")
        self.assertEqual(source_of("review"), "claimed")
        self.assertEqual(source_of("deploy_verified"), "claimed")
        self.assertIn("requesting_code_review", REQUIRED_GATES)

    def test_applicable_gates(self):
        from board.gate import applicable_gates
        feat = applicable_gates(is_bug_fix=False, has_deploy=False)
        self.assertNotIn("regression", feat)
        self.assertNotIn("deploy_verified", feat)
        self.assertIn("review", feat)
        bug = applicable_gates(is_bug_fix=True, has_deploy=True)
        self.assertIn("regression", bug)
        self.assertIn("deploy_verified", bug)


class TestAlarm(unittest.TestCase):
    def _run(self, **kw):
        base = dict(merged=False, merge_mode="auto", is_bug_fix=False,
                    has_deploy=False, phase="implementing",
                    last_report_age_s=10, gate={})
        base.update(kw)
        return base

    def test_merged_all_ok_no_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ticket_validated":"ok","ci":"ok","mergeable":"ok",
                            "plan_check":"ok","review":"ok","requesting_code_review":"ok"})
        self.assertNotIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_merged_missing_rcr_alarms(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ci":"ok","mergeable":"ok","plan_check":"ok",
                            "review":"ok","ticket_validated":"ok"})  # rcr missing→pending
        self.assertIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_merged_unstable_alarms(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ticket_validated":"ok","ci":"ok","mergeable":"fail",
                            "plan_check":"ok","review":"ok","requesting_code_review":"ok"})
        self.assertIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_manual_unmerged_green_no_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=False, merge_mode="manual", phase="done",
                      gate={k:"ok" for k in ("ticket_validated","ci","mergeable",
                            "plan_check","review","requesting_code_review")})
        self.assertEqual(compute_alarms(r), [])

    def test_pending_gate_recent_report_is_verifying_not_alarm(self):
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="merge", last_report_age_s=30,
                      gate={"ci":"ok","mergeable":"ok"})  # rest pending, fresh
        a = compute_alarms(r)
        self.assertIn("VERIFYING", a)
        self.assertNotIn("MERGED_INCOMPLETE_GATE", a)

    def test_stale_abandoned_midgate(self):
        from board.gate import compute_alarms
        r = self._run(merged=False, phase="review", last_report_age_s=9999)
        self.assertIn("STALE_ABANDONED", compute_alarms(r))

    def test_wait_phase_uses_longer_stale_threshold(self):
        from board.gate import compute_alarms
        # CI phase: 9 min elapsed — below 30-min WAIT threshold, no alarm
        r = self._run(merged=False, phase="CI", last_report_age_s=9 * 60)
        self.assertNotIn("STALE_ABANDONED", compute_alarms(r))
        # CI phase: 31 min elapsed — above 30-min WAIT threshold, alarm fires
        r = self._run(merged=False, phase="CI", last_report_age_s=31 * 60)
        self.assertIn("STALE_ABANDONED", compute_alarms(r))


import tempfile


class TestSchema(unittest.TestCase):
    def _db(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "b.sqlite")
        from board.db import Board
        return Board(p)

    def test_cold_init_creates_tables(self):
        b = self._db()
        tabs = {r[0] for r in b.conn().execute(
            "select name from sqlite_master where type='table'")}
        for t in ("runs", "events", "gate", "gh_state", "schema_version"):
            self.assertIn(t, tabs)

    def test_migration_idempotent(self):
        b = self._db()
        v1 = b.schema_version()
        b.migrate()  # re-run
        self.assertEqual(b.schema_version(), v1)
        self.assertGreaterEqual(v1, 1)


class TestUpsert(unittest.TestCase):
    def _b(self):
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    def test_coalesce_no_null_clobber(self):
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "implementing", "goal": "G", "event_id": "e1", "event_ts": 1.0})
        b.apply_event({"run_id": "r1", "seq": 2, "phase": "CI", "event_id": "e2", "event_ts": 2.0})
        row = b.get_run("r1")
        self.assertEqual(row["goal"], "G")      # preserved
        self.assertEqual(row["phase"], "CI")    # advanced

    def test_stale_report_does_not_clobber_content(self):
        b = self._b()
        b.apply_event({"run_id":"r1","repo":"o/x","issue":1,"seq":5,"phase":"merge",
                       "goal":"refined goal","machine":"dev1","event_id":"e5","event_ts":5.0})
        # stale lower-seq replay carrying an OLDER goal and OLDER machine must NOT overwrite
        b.apply_event({"run_id":"r1","seq":2,"phase":"implementing",
                       "goal":"initial goal","machine":"stale-machine",
                       "event_id":"e2","event_ts":2.0})
        row = b.get_run("r1")
        self.assertEqual(row["goal"], "refined goal")
        self.assertEqual(row["phase"], "merge")
        self.assertEqual(row["machine"], "dev1")  # Fix 2: machine is seq-guarded via _content()

    def test_seq_guard_ignores_stale(self):
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 5,
                       "phase": "merge", "event_id": "e5", "event_ts": 5.0})
        b.apply_event({"run_id": "r1", "seq": 2, "phase": "CI",
                       "event_id": "e2", "event_ts": 2.0})  # stale replay
        self.assertEqual(b.get_run("r1")["phase"], "merge")

    def test_event_id_idempotent(self):
        b = self._b()
        ev = {"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
              "phase": "CI", "event_id": "dup", "event_ts": 1.0}
        b.apply_event(ev)
        b.apply_event(ev)
        n = b.conn().execute("SELECT count(*) FROM events WHERE run_id='r1'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_terminal_not_regressed(self):
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "done", "event_id": "e1", "event_ts": 1.0})
        b.apply_event({"run_id": "r1", "seq": 9, "phase": "implementing",
                       "event_id": "e9", "event_ts": 9.0})
        self.assertEqual(b.get_run("r1")["phase"], "done")

    def test_asking_user_pause_does_not_regress(self):
        # PAUSE exemption (Fix 2): implementing -> asking-user -> implementing.
        # asking-user has a HIGHER rank than implementing, so going BACK to
        # implementing would normally be a rank regression and be rejected. The
        # PAUSE exemption must skip the rank check so the final phase is
        # implementing, not stuck at asking-user.
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "implementing", "event_id": "e1", "event_ts": 1.0})
        b.apply_event({"run_id": "r1", "seq": 2, "phase": "asking-user",
                       "event_id": "e2", "event_ts": 2.0})
        self.assertEqual(b.get_run("r1")["phase"], "asking-user")
        b.apply_event({"run_id": "r1", "seq": 3, "phase": "implementing",
                       "event_id": "e3", "event_ts": 3.0})
        self.assertEqual(b.get_run("r1")["phase"], "implementing")


class TestWriterSurvivesBadEvent(unittest.TestCase):
    """Fix 1: writer thread logs exceptions and survives; subsequent good events land."""

    def test_writer_survives_bad_event(self):
        import threading
        from board.db import Board
        b = Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))
        t = b.start_writer()

        # Inject a malformed event (missing 'run_id' key) directly onto the queue
        # so _apply raises KeyError — simulating a production write failure.
        bad_done = threading.Event()
        b._wq.put((b._apply, {}, bad_done))
        bad_done.wait(timeout=2)

        # Writer must still be alive and able to process a subsequent good event.
        good_ev = {"run_id": "r_good", "repo": "o/x", "issue": 7, "seq": 1,
                   "phase": "implementing", "event_id": "eg1", "event_ts": 1.0}
        b.submit(good_ev, wait=True, timeout=2)

        row = b.get_run("r_good")
        self.assertIsNotNone(row, "good event must persist after writer survived bad one")
        self.assertEqual(row["phase"], "implementing")

        b._wq.put(None)  # stop writer
        t.join(timeout=2)


class TestConcurrentWrites(unittest.TestCase):
    def test_parallel_posts_no_loss(self):
        import threading
        from board.db import Board
        b = Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

        def w(i):
            b.apply_event({"run_id": f"r{i % 5}", "repo": "o/x", "issue": i % 5, "seq": i,
                           "phase": "implementing", "event_id": f"e{i}", "event_ts": float(i)})
        ts = [threading.Thread(target=w, args=(i,)) for i in range(100)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        n = b.conn().execute("SELECT count(*) FROM events").fetchone()[0]
        self.assertEqual(n, 100)


class TestGateRows(unittest.TestCase):
    def _b(self):
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    def test_seed_pending(self):
        b = self._b()
        b.seed_gates("r1", is_bug_fix=False, has_deploy=False)
        g = b.gate_map("r1")
        self.assertEqual(g["review"], "pending")
        self.assertNotIn("regression", g)        # not applicable

    def test_set_gate_source_is_board_fixed(self):
        b = self._b()
        b.seed_gates("r1", False, False)
        # worker tries to claim review verified — board forces 'claimed'
        b.set_gate("r1", "review", "ok", seq=3, claimed=True)
        row = b.conn().execute(
            "SELECT source,state FROM gate WHERE run_id='r1' AND check_name='review'").fetchone()
        self.assertEqual(row["source"], "claimed")
        self.assertEqual(row["state"], "ok")

    def test_gate_seq_guard(self):
        b = self._b()
        b.seed_gates("r1", False, False)
        b.set_gate("r1", "ci", "ok", seq=5, claimed=False)
        b.set_gate("r1", "ci", "fail", seq=2, claimed=False)   # stale
        self.assertEqual(b.gate_map("r1")["ci"], "ok")


class TestRunId(unittest.TestCase):
    def setUp(self):
        import board.reporter as rp
        self.home = tempfile.mkdtemp()
        rp.STATE_DIR = self.home

    def test_mint_format_and_reuse(self):
        import board.reporter as rp
        rid = rp.start_run("o/x", 1, "title", is_bug_fix=True,
                           has_deploy=False, merge_mode="auto")
        self.assertRegex(rid, r"^o_x-1-\d+-[0-9a-f]{4}$")
        self.assertEqual(rp.current_run("o/x", 1), rid)      # persisted, reusable

    def test_seq_monotonic(self):
        import board.reporter as rp
        rid = rp.start_run("o/x", 2, "t")
        s1 = rp.next_seq(rid)
        s2 = rp.next_seq(rid)
        self.assertEqual(s2, s1 + 1)


class TestReporter(unittest.TestCase):
    def setUp(self):
        import board.reporter as rp
        self.home = tempfile.mkdtemp()
        rp.STATE_DIR = self.home
        self._orig_post_one = rp._post_one

    def tearDown(self):
        import board.reporter as rp
        rp._post_one = self._orig_post_one

    def test_secret_scrub(self):
        from board.reporter import scrub
        self.assertNotIn("ghp_", scrub("token ghp_ABCDEF123456 here"))
        self.assertIn("[redacted]", scrub("Bearer abc.def.ghi"))

    def test_queue_on_unreachable(self):
        import board.reporter as rp
        rp.BOARD_URL = "http://127.0.0.1:1/"   # nothing listening
        rid = rp.start_run("o/x", 3, "t")      # _start queues
        rp.report(rid, phase="CI")
        self.assertTrue(os.path.exists(rp._p("autopilot-board-queue.jsonl")))

    def test_flush_idempotent_event_ids(self):
        import board.reporter as rp
        import json
        # two queued lines with distinct event_id; a fake sender that records
        sent = []
        rp._post_one = lambda body: sent.append(body) or True
        with open(rp._p("autopilot-board-queue.jsonl"), "w") as h:
            h.write(json.dumps({"event_id": "a", "run_id": "r"}) + "\n")
            h.write(json.dumps({"event_id": "b", "run_id": "r"}) + "\n")
        rp.flush_queue()
        # _post_one receives the parsed event dict (it serialises internally),
        # so read event_id off the dict directly.
        self.assertEqual({x["event_id"] for x in sent}, {"a", "b"})
        self.assertEqual(os.path.getsize(rp._p("autopilot-board-queue.jsonl")), 0)

    # ------------------------------------------------------------------ C1
    def test_report_nonexistent_state_dir_does_not_raise(self):
        """C1: report() must not raise when STATE_DIR doesn't exist."""
        import board.reporter as rp
        import tempfile, os
        # Point STATE_DIR at a path whose parent also doesn't exist
        deep = os.path.join(tempfile.mkdtemp(), "nonexistent", "subdir")
        rp.STATE_DIR = deep
        rp.BOARD_URL = "http://127.0.0.1:1/"  # unreachable — forces queue path
        try:
            rid = "test-run-no-dir"
            result = rp.report(rid, phase="implementing")
            self.assertIsNone(result)  # fire-and-forget returns None
        except Exception as e:
            self.fail(f"report() raised {e!r} — must never raise")

    def test_report_non_serializable_field_does_not_raise(self):
        """C1: report() with a non-JSON-serializable field must not raise."""
        import board.reporter as rp
        rp.BOARD_URL = "http://127.0.0.1:1/"
        rid = rp.start_run("o/x", 99, "t")
        try:
            result = rp.report(rid, phase="implementing", obj=set())
            self.assertIsNone(result)
        except Exception as e:
            self.fail(f"report() raised {e!r} — must never raise")

    def test_post_one_bad_url_returns_false(self):
        """C1: _post_one with a malformed URL returns False, never raises."""
        import board.reporter as rp
        old_url = rp.BOARD_URL
        rp.BOARD_URL = "not-a-valid-url"
        try:
            result = rp._post_one({"x": 1})
        except Exception as e:
            rp.BOARD_URL = old_url
            self.fail(f"_post_one raised {e!r} — must never raise")
        rp.BOARD_URL = old_url
        self.assertFalse(result)

    # ------------------------------------------------------------------ I1
    def test_queue_trimmed_when_board_unreachable(self):
        """I1: queue file stays at/under QUEUE_MAX_BYTES when board is down."""
        import board.reporter as rp
        old_max = rp.QUEUE_MAX_BYTES
        old_url = rp.BOARD_URL
        cap = 300  # small cap; one JSONL event line is ~200 bytes
        try:
            rp.QUEUE_MAX_BYTES = cap
            rp.BOARD_URL = "http://127.0.0.1:1/"  # unreachable
            rid = rp.start_run("o/x", 50, "cap-test")
            # Emit enough events to overflow the cap
            for _ in range(20):
                rp.report(rid, phase="implementing")
            qf = rp._p("autopilot-board-queue.jsonl")
            size = os.path.getsize(qf)
            # The queue may be one line over the cap (append-then-trim);
            # allow 2× slack to absorb the last appended line before trim kicks in
            self.assertLessEqual(
                size, cap * 2,
                f"Queue grew to {size} bytes, expected ≤{cap * 2}"
            )
        finally:
            rp.QUEUE_MAX_BYTES = old_max
            rp.BOARD_URL = old_url


class TestGh(unittest.TestCase):
    def test_validate_repo_issue_runid(self):
        from board.gh import valid_repo, valid_issue, valid_run_id
        self.assertTrue(valid_repo("owner/name"))
        self.assertFalse(valid_repo("--version"))
        self.assertFalse(valid_repo("a;b/c"))
        self.assertTrue(valid_issue(5))
        self.assertFalse(valid_issue("-1"))
        self.assertFalse(valid_issue("x"))
        self.assertTrue(valid_run_id("o_x-1-123-ab12"))
        self.assertFalse(valid_run_id("a/b"))

    def test_validate_rejects_injection_vectors(self):
        # gh-argument-injection safety: nothing that could be read as a flag, a
        # shell metachar, whitespace, or a path traversal may pass validation.
        from board.gh import valid_repo, valid_issue, valid_run_id
        for bad in ("--json", "-f", "o/x;rm -rf", "o/x x", "o x/y", "o/x\n",
                    "", "o/", "/x", "o//x", "../etc/passwd", "o/x/../y",
                    "$(whoami)/x", "`id`/x", "o/x|y", "o&x/y", None, 5):
            self.assertFalse(valid_repo(bad), f"valid_repo accepted {bad!r}")
        for bad in ("--flag", "5; rm", "5 6", "5\n", "0", "-1", "1.5",
                    "1e3", " 5", None, [], "0x5"):
            self.assertFalse(valid_issue(bad), f"valid_issue accepted {bad!r}")
        for bad in ("a/b", "a;b", "a b", "--flag", "a\nb", "", None, "$(x)"):
            self.assertFalse(valid_run_id(bad), f"valid_run_id accepted {bad!r}")
        # positives across the supported charset
        self.assertTrue(valid_repo("Org-1/repo.name_2"))
        self.assertTrue(valid_issue("42"))
        self.assertTrue(valid_issue(42))
        self.assertTrue(valid_run_id("Org-1_repo.name_2-42-1700000000000-ab12"))

    def test_parse_pr_mergeable(self):
        from board.gh import classify_pr
        ok = classify_pr({"state": "MERGED", "mergedAt": "2026-06-15T00:00:00Z",
                          "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})
        self.assertTrue(ok["merged"])
        self.assertEqual(ok["mergeable_gate"], "ok")
        un = classify_pr({"state": "OPEN", "mergeable": "MERGEABLE",
                          "mergeStateStatus": "UNSTABLE"})
        self.assertEqual(un["mergeable_gate"], "fail")
        pend = classify_pr({"state": "OPEN", "mergeable": "UNKNOWN",
                            "mergeStateStatus": "UNKNOWN"})
        self.assertEqual(pend["mergeable_gate"], "pending")

    def test_classify_pr_fields_and_states(self):
        from board.gh import classify_pr
        # merged via mergedAt even if state lags
        m = classify_pr({"state": "OPEN", "mergedAt": "2026-06-15T00:00:00Z",
                         "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"})
        self.assertTrue(m["merged"])
        # open, clean, mergeable true → ok; mergeable bool surfaced
        o = classify_pr({"state": "OPEN", "mergeable": "MERGEABLE",
                         "mergeStateStatus": "CLEAN"})
        self.assertFalse(o["merged"])
        self.assertEqual(o["pr_state"], "OPEN")
        self.assertIs(o["mergeable"], True)
        self.assertEqual(o["mergeable_state"], "CLEAN")
        self.assertEqual(o["mergeable_gate"], "ok")
        # CONFLICTING (mergeable false) → fail
        c = classify_pr({"state": "OPEN", "mergeable": "CONFLICTING",
                         "mergeStateStatus": "DIRTY"})
        self.assertIs(c["mergeable"], False)
        self.assertEqual(c["mergeable_gate"], "fail")
        # missing mergeable key → None → pending
        miss = classify_pr({"state": "OPEN", "mergeStateStatus": "CLEAN"})
        self.assertIsNone(miss["mergeable"])
        self.assertEqual(miss["mergeable_gate"], "pending")

    def test_gh_argv_only_never_shell(self):
        # _gh must invoke gh via an argv list (subprocess.run([...])) and NEVER
        # with shell=True / a shell string. We assert by inspecting the call.
        import board.gh as gh
        calls = {}

        class _R:
            returncode = 0
            stdout = "[]"
            stderr = ""

        def fake_run(args, **kw):
            calls["args"] = args
            calls["kw"] = kw
            return _R()

        orig = gh.subprocess.run
        gh.subprocess.run = fake_run
        try:
            gh._gh(["pr", "list", "--repo", "o/x"])
        finally:
            gh.subprocess.run = orig
        self.assertIsInstance(calls["args"], list)
        self.assertEqual(calls["args"][0], "gh")
        self.assertNotIn("shell", calls["kw"])  # never shell=True
        self.assertFalse(calls["kw"].get("shell", False))
