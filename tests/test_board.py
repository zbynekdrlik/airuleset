import os
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

class TestConstants(unittest.TestCase):
    def test_constants_present(self):
        from board import (PORT, BOARD_HOST_IP, REPORT_TIMEOUT, TERMINAL_PHASES)
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

    def test_merged_unreported_gate_no_alarm(self):
        # BEHAVIOUR CHANGE (justified): a merged PR already passed GitHub branch
        # protection (CI + required reviews enforced at merge time), so the merge
        # IS the gate evidence. A merely UNREPORTED (pending) worker gate is NOT
        # a failure — flagging it turned every solved-but-partially-reported run
        # into a red MERGED_INCOMPLETE_GATE, which is the core "board shows my
        # finished work as broken" complaint. Only a VERIFIED 'fail' alarms now.
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ci":"ok","mergeable":"ok","plan_check":"ok",
                            "review":"ok","ticket_validated":"ok"})  # rcr missing→pending
        self.assertNotIn("MERGED_INCOMPLETE_GATE", compute_alarms(r))

    def test_merged_failed_gate_still_alarms(self):
        # A gate VERIFIED as 'fail' (not merely unreported) DOES alarm — a real
        # failure slipped past the merge.
        from board.gate import compute_alarms
        r = self._run(merged=True, phase="done",
                      gate={"ci":"ok","mergeable":"ok","plan_check":"fail",
                            "review":"ok","requesting_code_review":"ok",
                            "ticket_validated":"ok"})
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
        b._wq.put((b._apply, {}, bad_done, None))
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


class TestSubmitOutcome(unittest.TestCase):
    """Fix 1: submit() returns True on successful commit, False on failure/timeout."""

    def _b(self):
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    def test_submit_returns_true_on_good_event(self):
        b = self._b()
        b.start_writer()
        ev = {"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
              "phase": "implementing", "event_id": "e1", "event_ts": 1.0}
        result = b.submit(ev, wait=True, timeout=3)
        self.assertTrue(result, "submit must return True when the write committed")
        self.assertIsNotNone(b.get_run("r1"))

    def test_submit_returns_false_on_failing_write(self):
        """A malformed event (missing run_id) makes _apply raise; submit must
        return False — not True — so the server can detect the failure."""
        b = self._b()
        b.start_writer()
        # {} has no 'run_id', causing _apply to raise KeyError.
        bad_ev = {}
        result = b.submit(bad_ev, wait=True, timeout=3)
        self.assertFalse(result, "submit must return False when _apply raised")


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
        # seq-guard on the WORKER path — use a claimed check (review); the
        # verified checks (ci/mergeable) are now worker-locked and cannot be
        # written via set_gate at all.
        b = self._b()
        b.seed_gates("r1", False, False)
        b.set_gate("r1", "review", "ok", seq=5, claimed=False)
        b.set_gate("r1", "review", "fail", seq=2, claimed=False)   # stale
        self.assertEqual(b.gate_map("r1")["review"], "ok")

    def test_worker_claim_cannot_overwrite_verified_gate(self):
        # spec §9/§10: a worker set_gate must NEVER write a gh-verified check
        # (ci/mergeable/merged/issue_state). The gh refresher writes verified
        # rows with seq=0, so the old `seq < cur.seq` guard let a high-seq
        # worker claim overwrite a gh-verified mergeable=fail row (flipping
        # state→ok AND source→claimed) and silence MERGED_INCOMPLETE_GATE.
        b = self._b()
        b.seed_gates("r1", is_bug_fix=False, has_deploy=False)
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "done", "event_id": "e", "event_ts": 1.0})
        # gh observes a bad mergeable (UNSTABLE) — verified path writes the row
        b.set_gh("r1", merged=True, mergeable_gate="fail",
                 mergeable_state="UNSTABLE", ci_conclusion="success")
        # a worker tries to claim mergeable green at a far-higher seq
        b.set_gate("r1", "mergeable", "ok", seq=99, claimed=True)
        self.assertEqual(b.gate_map("r1")["mergeable"], "fail")  # NOT overwritten
        # source stays board-verified (a worker claim never touched it)
        row = b.conn().execute(
            "SELECT source FROM gate WHERE run_id='r1' AND check_name='mergeable'"
        ).fetchone()
        self.assertEqual(row["source"], "verified")
        from board.gate import compute_alarms
        self.assertIn("MERGED_INCOMPLETE_GATE",
                      compute_alarms(b.alarm_input("r1")))

    def test_worker_can_still_write_claimed_checks(self):
        # the lock is ONLY on verified checks — a worker still writes its own
        # claimed checks (review/plan_check/...) exactly as before.
        b = self._b()
        b.seed_gates("r1", False, False)
        b.set_gate("r1", "review", "ok", seq=3, claimed=True)
        self.assertEqual(b.gate_map("r1")["review"], "ok")


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
        self._orig_post_outcome = rp._post_outcome
        self._orig_normalize = rp._normalize_repo

    def tearDown(self):
        import board.reporter as rp
        rp._post_one = self._orig_post_one
        rp._post_outcome = self._orig_post_outcome
        rp._normalize_repo = self._orig_normalize

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
        rp._post_outcome = lambda body: (sent.append(body) or "ok")
        with open(rp._p("autopilot-board-queue.jsonl"), "w") as h:
            h.write(json.dumps({"event_id": "a", "run_id": "r"}) + "\n")
            h.write(json.dumps({"event_id": "b", "run_id": "r"}) + "\n")
        rp.flush_queue()
        self.assertEqual({x["event_id"] for x in sent}, {"a", "b"})
        self.assertEqual(os.path.getsize(rp._p("autopilot-board-queue.jsonl")), 0)

    def test_flush_drops_poison_and_continues(self):
        """A board-4xx poison event must be DROPPED, not block the good events
        behind it (the bug that hid real autopilot runs)."""
        import board.reporter as rp
        import json
        seen = []

        def outcome(body):
            seen.append(body["event_id"])
            return "reject" if body["event_id"] == "poison" else "ok"
        rp._post_outcome = outcome
        with open(rp._p("autopilot-board-queue.jsonl"), "w") as h:
            h.write(json.dumps({"event_id": "poison", "run_id": "r"}) + "\n")
            h.write(json.dumps({"event_id": "good1", "run_id": "r"}) + "\n")
            h.write(json.dumps({"event_id": "good2", "run_id": "r"}) + "\n")
        rp.flush_queue()
        # all three attempted, queue fully drained, breaker NOT tripped
        self.assertEqual(seen, ["poison", "good1", "good2"])
        self.assertEqual(os.path.getsize(rp._p("autopilot-board-queue.jsonl")), 0)
        self.assertFalse(os.path.exists(rp._p("autopilot-board-down")))

    def test_flush_keeps_and_backs_off_when_down(self):
        """A 'down' outcome keeps the event + remainder and opens the breaker."""
        import board.reporter as rp
        import json
        rp._post_outcome = lambda body: "down"
        with open(rp._p("autopilot-board-queue.jsonl"), "w") as h:
            h.write(json.dumps({"event_id": "a", "run_id": "r"}) + "\n")
            h.write(json.dumps({"event_id": "b", "run_id": "r"}) + "\n")
        rp.flush_queue()
        self.assertGreater(os.path.getsize(rp._p("autopilot-board-queue.jsonl")), 0)
        self.assertTrue(os.path.exists(rp._p("autopilot-board-down")))

    def test_post_outcome_classifies_http_codes(self):
        import board.reporter as rp
        import urllib.error

        def mk(exc):
            def fake(req, timeout=None):
                raise exc
            return fake
        orig = rp.urllib.request.urlopen
        try:
            rp.urllib.request.urlopen = mk(
                urllib.error.HTTPError("u", 400, "bad", {}, None))
            self.assertEqual(rp._post_outcome({"x": 1}), "reject")
            rp.urllib.request.urlopen = mk(
                urllib.error.HTTPError("u", 500, "err", {}, None))
            self.assertEqual(rp._post_outcome({"x": 1}), "down")
            rp.urllib.request.urlopen = mk(
                urllib.error.HTTPError("u", 429, "rl", {}, None))
            self.assertEqual(rp._post_outcome({"x": 1}), "down")
            rp.urllib.request.urlopen = mk(urllib.error.URLError("refused"))
            self.assertEqual(rp._post_outcome({"x": 1}), "down")
        finally:
            rp.urllib.request.urlopen = orig

    def test_normalize_repo_passthrough_valid(self):
        import board.reporter as rp
        self.assertEqual(rp._normalize_repo("owner/name"), "owner/name")

    def test_normalize_repo_bare_resolves_via_gh(self):
        import board.reporter as rp
        import subprocess

        class R:
            returncode = 0
            stdout = "owner/name\n"
        orig = subprocess.run
        try:
            subprocess.run = lambda *a, **k: R()
            self.assertEqual(rp._normalize_repo("name"), "owner/name")
        finally:
            subprocess.run = orig

    def test_normalize_repo_bare_unresolvable_returns_original(self):
        import board.reporter as rp
        import subprocess

        class R:
            returncode = 1
            stdout = ""
        orig = subprocess.run
        try:
            subprocess.run = lambda *a, **k: R()
            self.assertEqual(rp._normalize_repo("name"), "name")
        finally:
            subprocess.run = orig

    def test_start_run_uses_normalized_repo_in_run_id(self):
        import board.reporter as rp
        rp.BOARD_URL = "http://127.0.0.1:1/"          # unreachable → just queues
        rp._normalize_repo = lambda r: "owner/name"   # force normalization
        rid = rp.start_run("bare", 5, "t")
        self.assertTrue(rid.startswith("owner_name-5-"), rid)

    # ------------------------------------------------------------------ C1
    def test_report_nonexistent_state_dir_does_not_raise(self):
        """C1: report() must not raise when STATE_DIR doesn't exist."""
        import board.reporter as rp
        import tempfile
        import os
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
                    "$(whoami)/x", "`id`/x", "o/x|y", "o&x/y", None, 5,
                    # leading hyphen in the NAME segment — the name alone could
                    # be read as a gh flag (gh accepts `owner/name`), so both
                    # segments must reject a leading '-'.
                    "o/-x", "o/--json", "-o/x"):
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


class TestRefresh(unittest.TestCase):
    def _b(self):
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    # -------- the plan's contract: UNSTABLE-merged → alarm end-to-end -------
    def test_unstable_merged_alarms_end_to_end(self):
        from board.gate import compute_alarms
        b = self._b()
        b.seed_gates("r1", False, False)
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "done", "event_id": "e", "event_ts": 1.0})
        # worker claims its own (claimed) checks green
        for g in ("ticket_validated", "plan_check", "review",
                  "requesting_code_review"):
            b.set_gate("r1", g, "ok", seq=1, claimed=True)
        # gh-verified path: CI green but mergeable UNSTABLE (the real scenario)
        b.set_gh("r1", ci_conclusion="success", mergeable_gate="fail",
                 mergeable_state="UNSTABLE", merged=True)
        snap = b.alarm_input("r1", merged=True)
        self.assertIn("MERGED_INCOMPLETE_GATE", compute_alarms(snap))

    # -------- alarm_input assembles the right dict --------------------------
    def test_alarm_input_reads_run_and_gates(self):
        b = self._b()
        b.seed_gates("r1", is_bug_fix=True, has_deploy=True)
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "merge", "is_bug_fix": 1, "has_deploy": 1,
                       "merge_mode": "manual", "event_id": "e", "event_ts": 1.0})
        b.set_gh("r1", ci_conclusion="success")  # verified path sets ci=ok
        snap = b.alarm_input("r1")
        self.assertEqual(snap["phase"], "merge")
        self.assertTrue(snap["is_bug_fix"])
        self.assertTrue(snap["has_deploy"])
        self.assertEqual(snap["merge_mode"], "manual")
        self.assertEqual(snap["gate"]["ci"], "ok")
        self.assertIn("last_report_age_s", snap)
        self.assertGreaterEqual(snap["last_report_age_s"], 0)

    def test_alarm_input_merged_from_gh_state(self):
        b = self._b()
        b.seed_gates("r1", False, False)
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "done", "event_id": "e", "event_ts": 1.0})
        # gh says merged — alarm_input picks it up without an override
        b.set_gh("r1", merged=True)
        self.assertTrue(b.alarm_input("r1")["merged"])
        # explicit override wins
        self.assertFalse(b.alarm_input("r1", merged=False)["merged"])

    def test_alarm_input_missing_run(self):
        b = self._b()
        self.assertIsNone(b.alarm_input("nope"))

    def test_alarm_input_uses_board_clock_not_event_ts(self):
        # last_report_age_s must derive from runs.updated_at (board clock at
        # commit), NOT the worker-stamped event_ts (which could be far in the
        # past due to clock skew / queue replay).
        b = self._b()
        b.seed_gates("r1", False, False)
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "implementing",
                       "event_id": "e", "event_ts": 1.0})  # ancient worker ts
        snap = b.alarm_input("r1")
        # board just committed → age is tiny, NOT ~now-1970
        self.assertLess(snap["last_report_age_s"], 60)

    # -------- set_gh writes gh_state AND the verified gate rows -------------
    def test_set_gh_writes_state_and_gate_rows(self):
        b = self._b()
        b.seed_gates("r1", False, False)
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "merge", "event_id": "e", "event_ts": 1.0})
        b.set_gh("r1", merged=True, mergeable_gate="fail",
                 ci_conclusion="failure",
                 mergeable_state="UNSTABLE", pr_url="https://github.com/o/x/pull/1")
        gm = b.gate_map("r1")
        self.assertEqual(gm["mergeable"], "fail")  # written from gh truth
        self.assertEqual(gm["ci"], "fail")
        # and the gate source is board-fixed verified for these
        row = b.conn().execute(
            "SELECT source FROM gate WHERE run_id='r1' AND check_name='mergeable'"
        ).fetchone()
        self.assertEqual(row["source"], "verified")
        st = b.conn().execute(
            "SELECT merged, mergeable_state, pr_url, gh_ok FROM gh_state "
            "WHERE run_id='r1'").fetchone()
        self.assertEqual(st["merged"], 1)
        self.assertEqual(st["mergeable_state"], "UNSTABLE")
        self.assertEqual(st["gh_ok"], 1)

    def test_set_gh_ok_false_sentinel(self):
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "CI", "event_id": "e", "event_ts": 1.0})
        b.set_gh("r1", gh_ok=False)
        st = b.conn().execute(
            "SELECT gh_ok FROM gh_state WHERE run_id='r1'").fetchone()
        self.assertEqual(st["gh_ok"], 0)

    def test_set_gh_pending_does_not_write_gate(self):
        # a 'pending' mergeable_gate (GitHub still computing) must NOT overwrite
        # a previously-known gate state with pending noise.
        b = self._b()
        b.seed_gates("r1", False, False)
        b.set_gh("r1", mergeable_gate="ok", mergeable_state="CLEAN")
        b.set_gh("r1", mergeable_gate="pending")  # later poll, still computing
        self.assertEqual(b.gate_map("r1")["mergeable"], "ok")  # not clobbered

    def test_ci_gate_mapping_terminal_only(self):
        # pure mapping: terminal failures → fail, success → ok, everything
        # non-terminal (None/in_progress/neutral/skipped/unknown) → pending.
        from board.gate import ci_gate
        self.assertEqual(ci_gate("success"), "ok")
        for f in ("failure", "cancelled", "timed_out", "action_required"):
            self.assertEqual(ci_gate(f), "fail", f)
        for p in (None, "in_progress", "neutral", "skipped", "queued",
                  "stale", "unknown", "pending"):
            self.assertEqual(ci_gate(p), "pending", p)

    def test_set_gh_ci_inprogress_does_not_record_fail(self):
        # an in-progress / non-terminal CI conclusion must NOT be recorded as a
        # `ci` gate fail (which would raise a false MERGED_INCOMPLETE_GATE) —
        # it maps to pending and is NOT written, leaving the prior state alone.
        b = self._b()
        b.seed_gates("r1", False, False)
        # success first → ok
        b.set_gh("r1", ci_conclusion="success")
        self.assertEqual(b.gate_map("r1")["ci"], "ok")
        # later in-progress poll must not clobber the known 'ok' with fail
        b.set_gh("r1", ci_conclusion="in_progress")
        self.assertEqual(b.gate_map("r1")["ci"], "ok")
        # None (no conclusion yet) is also a no-op on the gate
        b.set_gh("r1", ci_conclusion=None)
        self.assertEqual(b.gate_map("r1")["ci"], "ok")
        # a terminal failure DOES record fail
        b.set_gh("r1", ci_conclusion="failure")
        self.assertEqual(b.gate_map("r1")["ci"], "fail")

    # -------- (repo,issue) -> newest non-terminal run mapping ---------------
    def test_newest_active_run_for_issue(self):
        b = self._b()
        b.apply_event({"run_id": "old", "repo": "o/x", "issue": 5, "seq": 1,
                       "phase": "implementing", "event_id": "a", "event_ts": 1.0})
        import time
        time.sleep(0.01)
        b.apply_event({"run_id": "new", "repo": "o/x", "issue": 5, "seq": 1,
                       "phase": "implementing", "event_id": "b", "event_ts": 2.0})
        self.assertEqual(b.newest_active_run("o/x", 5), "new")
        # a terminal newest run is NOT "active"
        b.apply_event({"run_id": "new", "seq": 9, "phase": "done",
                       "event_id": "c", "event_ts": 3.0})
        self.assertEqual(b.newest_active_run("o/x", 5), "old")
        self.assertIsNone(b.newest_active_run("o/x", 999))

    # -------- reaper: stale + gh reconcile ---------------------------------
    def test_mark_stale_sets_status(self):
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "implementing", "event_id": "e", "event_ts": 1.0})
        # force updated_at far into the past so it's past STALE_ACTIVE_S
        c = b.conn()
        c.execute("UPDATE runs SET updated_at=? WHERE run_id='r1'", (100.0,))
        c.commit()
        c.close()
        import time
        b.mark_stale(time.time())
        self.assertEqual(b.get_run("r1")["status"], "stale")

    def test_mark_stale_skips_terminal_and_pause(self):
        b = self._b()
        for rid, ph in (("done1", "done"), ("ask1", "asking-user")):
            b.apply_event({"run_id": rid, "repo": "o/x", "issue": 1, "seq": 1,
                           "phase": ph, "event_id": rid, "event_ts": 1.0})
            c = b.conn()
            c.execute("UPDATE runs SET updated_at=100.0 WHERE run_id=?", (rid,))
            c.commit()
            c.close()
        import time
        b.mark_stale(time.time())
        self.assertNotEqual(b.get_run("done1")["status"], "stale")
        self.assertNotEqual(b.get_run("ask1")["status"], "stale")

    def test_reconcile_gh_merged_finalizes_done(self):
        # gh shows the issue merged+closed for a non-terminal run → finalize done
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "merge", "event_id": "e", "event_ts": 1.0})
        b.set_gh("r1", merged=True, issue_state="CLOSED")
        import time
        b.mark_stale(time.time())
        row = b.get_run("r1")
        self.assertEqual(row["phase"], "done")
        self.assertIn("per gh", (row["result"] or ""))


# ============================ Phase E — render =============================

class TestRender(unittest.TestCase):
    def _live(self, **kw):
        base = {"run_id": "o_x-1-1-ab12", "repo": "o/x", "issue": 1,
                "title": "fix the thing", "phase": "CI", "goal": "g",
                "approach": "a", "result": None, "machine": "dev1",
                "gate": {"ci": "ok", "review": "pending"}, "alarms": [],
                "pr_url": None, "updated_at": 1000.0}
        base.update(kw)
        return base

    def test_escapes_xss(self):
        from board.render import card_grid
        html = card_grid(
            live=[self._live(title="<script>alert(1)</script>")],
            recent=[], version="vX", health={})
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert", html)

    def test_escapes_every_field(self):
        # goal/approach/result/repo/machine all escaped, not just title.
        from board.render import card_grid
        html = card_grid(
            live=[self._live(goal="<g>", approach="<a>", result="<r>",
                             repo="<o>/x", machine="<m>", phase="<p>")],
            recent=[], version="vX", health={})
        for inj in ("<g>", "<a>", "<r>", "<o>", "<m>", "<p>"):
            self.assertNotIn(inj, html)
        self.assertIn("&lt;g&gt;", html)
        self.assertIn("&lt;m&gt;", html)

    def test_empty_state(self):
        from board.render import card_grid
        html = card_grid(live=[], recent=[], version="vX",
                         health={"last_report": "never"})
        self.assertIn("No autopilot runs yet", html)
        self.assertIn("vX", html)

    def test_version_in_footer(self):
        from board.render import card_grid
        html = card_grid(live=[self._live()], recent=[],
                         version="v1.2.3-dev.4 (abc1234)", health={})
        self.assertIn("v1.2.3-dev.4 (abc1234)", html)

    def test_version_label_is_escaped(self):
        from board.render import card_grid
        html = card_grid(live=[], recent=[], version="<v>", health={})
        self.assertNotIn("<v>", html)
        self.assertIn("&lt;v&gt;", html)

    def test_alarm_banner_rendered(self):
        from board.render import card_grid
        html = card_grid(
            live=[self._live(alarms=["MERGED_INCOMPLETE_GATE"])],
            recent=[], version="vX", health={})
        self.assertIn("MERGED_INCOMPLETE_GATE", html)

    def test_pr_link_only_for_github_https(self):
        # a valid github https URL becomes a real href; a bad scheme never does.
        from board.render import card_grid
        good = card_grid(
            live=[self._live(pr_url="https://github.com/o/x/pull/5")],
            recent=[], version="vX", health={})
        self.assertIn('href="https://github.com/o/x/pull/5"', good)
        bad = card_grid(
            live=[self._live(pr_url="javascript:alert(1)")],
            recent=[], version="vX", health={})
        self.assertNotIn('href="javascript:', bad)
        self.assertNotIn("javascript:alert", bad)

    def test_ticket_detail_escapes(self):
        from board.render import ticket_detail
        run = {"run_id": "o_x-1-1-ab12", "repo": "o/x", "issue": 1,
               "title": "<b>t</b>", "goal": "<g>", "approach": "<a>",
               "result": "<r>", "phase": "done", "machine": "dev1",
               "merge_mode": "auto", "pr_url": None, "status": None,
               "unverified": "<u>", "filed_issues": None}
        events = [{"seq": 1, "phase": "CI", "message": "<m>", "event_ts": 1.0}]
        gate = {"ci": "ok", "review": "<x>"}
        gh = {"merged": 1, "ci_conclusion": "<c>", "mergeable_state": "<ms>",
              "pr_url": None}
        html = ticket_detail(run, events, gate, gh)
        # the injected payloads must appear ONLY in escaped form. We assert the
        # escaped form is present and the raw payload's closing markup (which is
        # unique to the injection — the template uses <b>..</b> only as labels,
        # never </b> mid-value) does not leak.
        for raw, esc in (("<b>t</b>", "&lt;b&gt;t&lt;/b&gt;"), ("<g>", "&lt;g&gt;"),
                         ("<a>", "&lt;a&gt;"), ("<r>", "&lt;r&gt;"),
                         ("<m>", "&lt;m&gt;"), ("<u>", "&lt;u&gt;"),
                         ("<c>", "&lt;c&gt;"), ("<ms>", "&lt;ms&gt;")):
            self.assertIn(esc, html)
        # the value-injected closing tag must never render raw
        self.assertNotIn("<b>t</b>", html)
        self.assertNotIn("&lt;b&gt;t</b>", html)

    def test_ticket_detail_empty_events(self):
        from board.render import ticket_detail
        run = {"run_id": "r", "repo": "o/x", "issue": 1, "title": "t",
               "goal": None, "approach": None, "result": None,
               "phase": "implementing", "machine": "dev1", "merge_mode": "auto",
               "pr_url": None, "status": None}
        html = ticket_detail(run, [], {}, None)
        self.assertIn("r", html)  # renders without crashing on empty data


# ============================ Phase E — server ============================

class TestServer(unittest.TestCase):
    def setUp(self):
        import tempfile
        import threading
        import board.server as srv
        from board.db import Board
        self.dir = tempfile.mkdtemp()
        self.token = "t0ken"
        # NOTE: make_server only builds the ThreadingHTTPServer + handler. It
        # does NOT start the gh refresher / reaper threads (run_server does), so
        # this test never touches the network.
        self.b = Board(os.path.join(self.dir, "b.sqlite"))
        self.b.start_writer()
        self.httpd = srv.make_server(self.b, token=self.token,
                                     host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, body, token="t0ken"):
        import urllib.request
        import urllib.error
        import json
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Board-Token"] = token
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/report",
            data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def _get(self, path, token=None):
        import urllib.request
        import urllib.error
        headers = {}
        if token is not None:
            headers["X-Board-Token"] = token
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode()

    # ---- POST /report auth + validation ----
    def test_rejects_bad_token(self):
        self.assertEqual(
            self._post({"run_id": "r1", "phase": "CI"}, token="wrong")[0], 403)

    def test_rejects_missing_token(self):
        self.assertEqual(
            self._post({"run_id": "r1", "phase": "CI"}, token=None)[0], 403)

    def test_accepts_and_persists(self):
        import time
        status, _ = self._post({"run_id": "r1", "repo": "o/x", "issue": 1,
                                "seq": 1, "phase": "CI", "event_id": "e1",
                                "event_ts": 1.0})
        self.assertEqual(status, 200)
        # writer is async; the handler waits for commit before 200 so it should
        # already be persisted, but give a tiny margin.
        for _ in range(20):
            row = self.b.get_run("r1")
            if row is not None and row["phase"] == "CI":
                break
            time.sleep(0.05)
        self.assertEqual(self.b.get_run("r1")["phase"], "CI")

    def test_rejects_bad_repo(self):
        status, _ = self._post({"run_id": "r1", "repo": "a;b/c", "issue": 1,
                                "phase": "CI", "event_id": "e2"})
        self.assertEqual(status, 400)
        self.assertIsNone(self.b.get_run("r1"))  # not stored

    def test_rejects_bad_run_id(self):
        status, _ = self._post({"run_id": "a/b", "repo": "o/x", "issue": 1,
                                "phase": "CI", "event_id": "e3"})
        self.assertEqual(status, 400)

    def test_rejects_bad_issue(self):
        status, _ = self._post({"run_id": "r1", "repo": "o/x", "issue": -5,
                                "phase": "CI", "event_id": "e4"})
        self.assertEqual(status, 400)

    def test_body_too_large(self):
        status, _ = self._post({"run_id": "r1", "note": "x" * 70000,
                                "event_id": "e5"})
        self.assertEqual(status, 413)

    def test_bad_json_400(self):
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/report",
            data=b"{not json", method="POST",
            headers={"X-Board-Token": self.token,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        self.assertEqual(code, 400)

    def test_worker_gate_claim_applied(self):
        # a report carrying reviews=[["review","ok"]] writes the claimed gate.
        self.b.seed_gates("r1", is_bug_fix=False, has_deploy=False)
        status, _ = self._post({"run_id": "r1", "repo": "o/x", "issue": 1,
                                "seq": 2, "phase": "review", "event_id": "e6",
                                "event_ts": 2.0,
                                "reviews": [["review", "ok"]]})
        self.assertEqual(status, 200)
        import time
        for _ in range(20):
            if self.b.gate_map("r1").get("review") == "ok":
                break
            time.sleep(0.05)
        self.assertEqual(self.b.gate_map("r1")["review"], "ok")

    def test_worker_cannot_claim_verified_gate(self):
        # a worker review for a gh-verified check (ci) must be ignored by the
        # data layer (set_gate refuses verified checks).
        self.b.seed_gates("r1", is_bug_fix=False, has_deploy=False)
        self._post({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 2,
                    "phase": "CI", "event_id": "e7", "event_ts": 2.0,
                    "reviews": [["ci", "ok"]]})
        import time
        time.sleep(0.2)
        # ci stays 'pending' (seeded) — a worker claim cannot flip it to ok.
        self.assertEqual(self.b.gate_map("r1")["ci"], "pending")

    # ---- GET endpoints ----
    def test_home_renders_and_headers(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Content-Type"),
                         "text/html; charset=utf-8")
        # CSP: no script allowed
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'none'", csp)
        # no-CORS: must NOT advertise cross-origin access
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("Autopilot Board", body)

    def test_ticket_route(self):
        # persist a run, then fetch its ticket page
        self._post({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                    "phase": "CI", "event_id": "e1", "event_ts": 1.0,
                    "title": "<script>x</script>"})
        import time
        time.sleep(0.15)
        status, headers, body = self._get("/ticket/r1")
        self.assertEqual(status, 200)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script>x</script>", body)

    def test_api_state_is_token_gated(self):
        # without a token → 403; with the token → 200 JSON
        self.assertEqual(self._get("/api/state")[0], 403)
        status, headers, body = self._get("/api/state", token=self.token)
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        import json
        data = json.loads(body)
        self.assertIn("version", data)

    def test_unknown_route_404(self):
        self.assertEqual(self._get("/nope")[0], 404)

    # ---- Fix 1: POST /report returns 500 when the write fails ----
    def test_post_returns_500_on_write_failure(self):
        """A write failure (submit returns False) must produce a 500 response,
        not a false 200. We monkeypatch board.submit to return False."""
        import json
        orig_submit = self.b.submit
        self.b.submit = lambda *a, **kw: False
        try:
            status, body = self._post({"run_id": "r_fail", "repo": "o/x",
                                       "issue": 1, "seq": 1, "phase": "CI",
                                       "event_id": "ef1", "event_ts": 1.0})
        finally:
            self.b.submit = orig_submit
        self.assertEqual(status, 500)
        data = json.loads(body)
        self.assertIn("error", data)

    # ---- rate limit ----
    def test_rate_limit_on_report(self):
        # hammer /report; eventually the per-IP token bucket returns 429.
        codes = set()
        for i in range(400):
            codes.add(self._post({"run_id": "r1", "repo": "o/x", "issue": 1,
                                  "seq": 100 + i, "phase": "CI",
                                  "event_id": f"rl{i}", "event_ts": 1.0})[0])
            if 429 in codes:
                break
        self.assertIn(429, codes)


# ============================ Phase E2 — planned queue ====================

class TestQueue(unittest.TestCase):
    def _b(self):
        import tempfile
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    def test_set_queue_replaces_atomically_and_orders(self):
        b = self._b()
        b.set_queue("o/x", [(5, "five"), (9, "nine")])
        b.set_queue("o/x", [(9, "nine"), (7, "seven")])   # replace
        q = b.get_queue()                                  # [{repo,issue,title,position}], excludes active/closed
        nums = [r["issue"] for r in q if r["repo"] == "o/x"]
        self.assertEqual(nums, [9, 7])                     # new order, old #5 gone

    def test_prune_closed(self):
        b = self._b()
        b.set_queue("o/x", [(5, "five"), (9, "nine")])
        b.prune_queue("o/x", open_issues={9})              # 5 no longer open → dropped
        self.assertEqual([r["issue"] for r in b.get_queue()], [9])

    def test_prune_empty_set_leaves_queue_intact(self):
        """_maybe_prune with an empty open-issues set must NOT wipe the queue.

        The refresher receives an empty set when a repo's gh call returns an
        empty issues list (transient rate-limit, all-closed repo).  Wiping every
        queue row in that case destroys the user's planned-work view.  The guard
        must be truthiness (`if open_issue_numbers:`) not `is not None`.
        """
        import board.server as srv
        b = self._b()
        b.set_queue("o/x", [(5, "five"), (9, "nine")])
        # _maybe_prune with empty set → must not prune
        srv._maybe_prune(b, "o/x", set())
        issues = [r["issue"] for r in b.get_queue()]
        self.assertIn(5, issues)
        self.assertIn(9, issues)
        # _maybe_prune with non-empty set → prunes closed issues normally
        srv._maybe_prune(b, "o/x", {9})
        issues2 = [r["issue"] for r in b.get_queue()]
        self.assertNotIn(5, issues2)
        self.assertIn(9, issues2)

    def test_queue_excludes_active_run(self):
        b = self._b()
        b.set_queue("o/x", [(5, "five"), (9, "nine")])
        b.apply_event({"run_id": "o_x-5-1-aa", "repo": "o/x", "issue": 5, "seq": 1,
                       "phase": "implementing", "event_id": "e", "event_ts": 1.0})
        nums = [r["issue"] for r in b.get_queue()]        # 5 is now active → not in queue
        self.assertNotIn(5, nums)
        self.assertIn(9, nums)

    def test_render_queue_count_and_escape(self):
        from board.render import card_grid
        html = card_grid(live=[], recent=[], version="vX",
                         health={}, queue=[{"repo": "o/x", "issue": 1,
                                            "title": "<b>t</b>", "position": 0}])
        self.assertIn("Up next", html)
        self.assertIn("1 queued", html)
        self.assertIn("&lt;b&gt;", html)
        self.assertNotIn("<b>t</b>", html)

    def test_migration_upgrades_existing_db(self):
        """Open a v1 DB (migration 1 only), confirm queue table is absent,
        then trigger migration and confirm queue table now exists."""
        import tempfile
        import sqlite3
        from board.db import _SCHEMA
        d = tempfile.mkdtemp()
        path = os.path.join(d, "v1.sqlite")
        # Manually build a v1 DB (only migration 1 schema, version=1).
        c = sqlite3.connect(path)
        c.executescript(_SCHEMA[0])
        c.execute("DELETE FROM schema_version")
        c.execute("INSERT INTO schema_version(version) VALUES (1)")
        c.commit()
        c.close()
        # Confirm queue table is absent in v1.
        c = sqlite3.connect(path)
        tabs = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        self.assertNotIn("queue", tabs)
        # Now open via Board (triggers migrate()).
        from board.db import Board
        b = Board(path)
        # queue table must now exist.
        c = b.conn()
        tabs2 = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
        self.assertIn("queue", tabs2)
        self.assertGreaterEqual(b.schema_version(), 2)

    def test_queue_report_enqueues(self):
        """queue_report must queue a body without raising."""
        import tempfile
        import board.reporter as rp
        home = tempfile.mkdtemp()
        orig_state = rp.STATE_DIR
        orig_url = rp.BOARD_URL
        try:
            rp.STATE_DIR = home
            rp.BOARD_URL = "http://127.0.0.1:1/"   # nothing listening → queues
            rp.queue_report("o/x", [(1, "issue one"), (2, "issue two")])
            qf = os.path.join(home, "autopilot-board-queue.jsonl")
            self.assertTrue(os.path.exists(qf))
            import json
            lines = open(qf).readlines()
            bodies = [json.loads(l) for l in lines if l.strip()]
            self.assertTrue(any(b.get("kind") == "queue" for b in bodies))
        finally:
            rp.STATE_DIR = orig_state
            rp.BOARD_URL = orig_url

    def test_server_accepts_queue_post(self):
        """POST kind=queue to the server must store it via set_queue."""
        import time
        import threading
        import tempfile
        import board.server as srv
        from board.db import Board
        d = tempfile.mkdtemp()
        token = "qt0k"
        b = Board(os.path.join(d, "b.sqlite"))
        b.start_writer()
        httpd = srv.make_server(b, token=token, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            import urllib.request
            import urllib.error
            import json
            body = {"kind": "queue", "repo": "o/x",
                    "items": [[3, "item three"], [7, "item seven"]]}
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/report",
                data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json",
                         "X-Board-Token": token})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200)
            # Give writer a moment.
            for _ in range(20):
                q = b.get_queue()
                if q:
                    break
                time.sleep(0.05)
            issues = [r["issue"] for r in q]
            self.assertIn(3, issues)
            self.assertIn(7, issues)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_server_rejects_queue_post_bad_repo(self):
        """POST kind=queue with invalid repo must be rejected with 400."""
        import threading
        import tempfile
        import board.server as srv
        from board.db import Board
        d = tempfile.mkdtemp()
        token = "qt0k2"
        b = Board(os.path.join(d, "b.sqlite"))
        b.start_writer()
        httpd = srv.make_server(b, token=token, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            import urllib.request
            import urllib.error
            import json
            body = {"kind": "queue", "repo": "a;b/bad",
                    "items": [[1, "title"]]}
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/report",
                data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json",
                         "X-Board-Token": token})
            try:
                with urllib.request.urlopen(req) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
            self.assertEqual(code, 400)
        finally:
            httpd.shutdown()
            httpd.server_close()


class TestDistinctRepos(unittest.TestCase):
    """Board.distinct_repos() returns repos recorded in the runs table."""

    def _b(self):
        import tempfile
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    def test_empty_db_returns_empty_list(self):
        b = self._b()
        self.assertEqual(b.distinct_repos(), [])

    def test_reported_run_appears_in_distinct_repos(self):
        b = self._b()
        b.apply_event({"run_id": "o_x-1-1-abcd", "repo": "o/x", "issue": 1,
                       "seq": 1, "phase": "implementing", "event_id": "e1",
                       "event_ts": 1.0})
        self.assertIn("o/x", b.distinct_repos())

    def test_multiple_runs_same_repo_deduped(self):
        b = self._b()
        b.apply_event({"run_id": "o_x-1-1-aa", "repo": "o/x", "issue": 1,
                       "seq": 1, "event_id": "e1", "event_ts": 1.0})
        b.apply_event({"run_id": "o_x-2-1-bb", "repo": "o/x", "issue": 2,
                       "seq": 1, "event_id": "e2", "event_ts": 2.0})
        repos = b.distinct_repos()
        self.assertEqual(repos.count("o/x"), 1)

    def test_multiple_repos_all_returned(self):
        b = self._b()
        b.apply_event({"run_id": "o_x-1-1-aa", "repo": "o/x", "issue": 1,
                       "seq": 1, "event_id": "e1", "event_ts": 1.0})
        b.apply_event({"run_id": "o_y-1-1-bb", "repo": "o/y", "issue": 1,
                       "seq": 1, "event_id": "e2", "event_ts": 2.0})
        repos = b.distinct_repos()
        self.assertIn("o/x", repos)
        self.assertIn("o/y", repos)

    def test_null_repo_excluded(self):
        """Runs with NULL repo (no repo field reported yet) must not appear."""
        b = self._b()
        b.apply_event({"run_id": "no_repo-1-1-cc", "seq": 1,
                       "event_id": "e1", "event_ts": 1.0})
        self.assertEqual(b.distinct_repos(), [])


# --------------------------------------------------------------------------- #
# Board reliability fixes (2026-06-16) — STALE_ABANDONED-but-actually-solved,
# Up-next-shows-done, PR↔issue linkage, reconcile-by-closed-issue, terminal-
# clears-stale, robust reporting, gh-health, render truthfulness.
# Each is a regression guard for a confirmed audit finding.
# --------------------------------------------------------------------------- #
class TestReliabilityDB(unittest.TestCase):
    def _b(self):
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    @staticmethod
    def _exec(b, sql):
        # one connection: execute + commit + close (conn() mints a fresh
        # connection per call, so a leaked open conn would hold a WAL write lock)
        c = b.conn()
        c.execute(sql)
        c.commit()
        c.close()

    def test_terminal_phase_clears_stale(self):
        # #620: a run marked stale by the reaper, then a worker 'done' report
        # (which carries NO status) — the done phase MUST clear stale.
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "review", "event_id": "e1", "event_ts": 1.0})
        self._exec(b, "UPDATE runs SET status='stale' WHERE run_id='r1'")
        b.apply_event({"run_id": "r1", "seq": 2, "phase": "done",
                       "event_id": "e2", "event_ts": 2.0})
        row = b.get_run("r1")
        self.assertEqual(row["phase"], "done")
        self.assertNotEqual(row["status"], "stale")

    def test_newest_active_run_finds_stale_asking_user(self):
        # A stale-marked asking-user run must still be reachable so gh maps to it.
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 7, "seq": 1,
                       "phase": "asking-user", "event_id": "e1", "event_ts": 1.0})
        self._exec(b, "UPDATE runs SET status='stale' WHERE run_id='r1'")
        self.assertEqual(b.newest_active_run("o/x", 7), "r1")

    def test_reconcile_closed_finalizes_non_open_issue(self):
        # Issue not in the open set (closed/merged on gh) -> done, even with no
        # final worker report. THE core false-STALE_ABANDONED fix.
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 588, "seq": 1,
                       "phase": "merge", "event_id": "e1", "event_ts": 1.0})
        n = b.reconcile_closed("o/x", {600, 601}, 100.0)   # 588 NOT open
        self.assertEqual(n, 1)
        row = b.get_run("r1")
        self.assertEqual(row["phase"], "done")
        self.assertNotEqual(row["status"], "stale")
        self.assertIn("issue closed", (row["result"] or ""))

    def test_reconcile_closed_leaves_open_issue(self):
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 626, "seq": 1,
                       "phase": "review", "event_id": "e1", "event_ts": 1.0})
        n = b.reconcile_closed("o/x", {626}, 100.0)        # 626 still open
        self.assertEqual(n, 0)
        self.assertEqual(b.get_run("r1")["phase"], "review")

    def test_reconcile_closed_skips_terminal(self):
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 5, "seq": 1,
                       "phase": "done", "event_id": "e1", "event_ts": 1.0})
        self.assertEqual(b.reconcile_closed("o/x", set(), 100.0), 0)

    def test_get_queue_excludes_done_and_stale_runs(self):
        # Up next must NOT list an issue that already has ANY run (done or stale).
        b = self._b()
        b.set_queue("o/x", [(1, "done one"), (2, "stale one"), (3, "fresh one")])
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 1, "seq": 1,
                       "phase": "done", "event_id": "e1", "event_ts": 1.0})
        b.apply_event({"run_id": "r2", "repo": "o/x", "issue": 2, "seq": 1,
                       "phase": "CI", "event_id": "e2", "event_ts": 2.0})
        self._exec(b, "UPDATE runs SET status='stale' WHERE run_id='r2'")
        issues = {r["issue"] for r in b.get_queue()}
        self.assertEqual(issues, {3})

    def test_prune_queue_expired(self):
        b = self._b()
        b.set_queue("o/x", [(1, "old"), (2, "old2")])
        # backdate the rows
        self._exec(b, "UPDATE queue SET reported_at=1000 WHERE repo='o/x'")
        deleted = b.prune_queue_expired(now=1000 + 20 * 24 * 3600, ttl=14 * 24 * 3600)
        self.assertEqual(deleted, 2)
        self.assertEqual(b.get_queue(), [])


class TestReliabilityGh(unittest.TestCase):
    def test_classify_pr_returns_closing_issues(self):
        from board.gh import classify_pr
        c = classify_pr({"state": "MERGED", "mergedAt": "2026-06-15T23:32:42Z",
                         "closingIssuesReferences": [{"number": 588},
                                                     {"number": 615}]})
        self.assertTrue(c["merged"])
        self.assertEqual(sorted(c["closes"]), [588, 615])

    def test_classify_pr_no_closing_issues(self):
        from board.gh import classify_pr
        c = classify_pr({"state": "OPEN"})
        self.assertEqual(c["closes"], [])

    def test_pr_json_fields_include_closing_issues(self):
        from board import gh
        self.assertIn("closingIssuesReferences", gh._PR_JSON_FIELDS)
        self.assertGreaterEqual(gh._PR_LIMIT, 100)
        self.assertGreaterEqual(gh._ISSUE_LIMIT, 100)


class TestReliabilityRefresh(unittest.TestCase):
    def _b(self):
        from board.db import Board
        return Board(os.path.join(tempfile.mkdtemp(), "b.sqlite"))

    def test_apply_repo_refresh_links_pr_by_closed_issue(self):
        # The fix: a PR (number 999) closing issue 588 maps gh signals to the
        # RUN of issue 588 — NOT to a run keyed by the PR number.
        from board.server import _apply_repo_refresh
        b = self._b()
        b.apply_event({"run_id": "r588", "repo": "o/x", "issue": 588, "seq": 1,
                       "phase": "merge", "event_id": "e1", "event_ts": 1.0})
        res = {"gh_ok": True, "open_issues": set(), "issues_capped": False,
               "prs": [{"number": 999, "url": "https://github.com/o/x/pull/999",
                        "pr_state": "MERGED", "merged": True, "mergeable": None,
                        "mergeable_state": None, "mergeable_gate": "pending",
                        "closes": [588]}]}
        _apply_repo_refresh(b, "o/x", res)
        gh = b.get_gh("r588")
        self.assertIsNotNone(gh)
        self.assertEqual(gh["merged"], 1)
        # issue 588 not in open set -> reconciled to done
        self.assertEqual(b.get_run("r588")["phase"], "done")

    def test_apply_repo_refresh_skips_reconcile_when_capped(self):
        from board.server import _apply_repo_refresh
        b = self._b()
        b.apply_event({"run_id": "r1", "repo": "o/x", "issue": 9, "seq": 1,
                       "phase": "review", "event_id": "e1", "event_ts": 1.0})
        # capped open list -> 'not open' is unreliable -> do NOT finalize
        res = {"gh_ok": True, "open_issues": set(), "issues_capped": True,
               "prs": []}
        _apply_repo_refresh(b, "o/x", res)
        self.assertEqual(b.get_run("r1")["phase"], "review")


class TestReliabilityReporter(unittest.TestCase):
    def setUp(self):
        import board.reporter as rp
        self.home = tempfile.mkdtemp()
        rp.STATE_DIR = self.home

    def test_run_to_repo_issue_reverses_map(self):
        import board.reporter as rp
        rp._save(rp._p("autopilot-board-runs.json"),
                 {"zbynekdrlik/odoo-erp#588": "zbynekdrlik_odoo-erp-588-1-aa"})
        repo, issue = rp.run_to_repo_issue("zbynekdrlik_odoo-erp-588-1-aa")
        self.assertEqual(repo, "zbynekdrlik/odoo-erp")
        self.assertEqual(issue, 588)

    def test_run_to_repo_issue_unknown(self):
        import board.reporter as rp
        rp._save(rp._p("autopilot-board-runs.json"), {})
        self.assertEqual(rp.run_to_repo_issue("nope"), (None, None))

    def test_next_seq_unique_under_threads(self):
        # flock guard: concurrent same-run next_seq calls must never collide.
        import threading
        import board.reporter as rp
        seqs = []
        lock = threading.Lock()

        def worker():
            s = rp.next_seq("rid-concurrent")
            with lock:
                seqs.append(s)

        ts = [threading.Thread(target=worker) for _ in range(25)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(seqs), list(range(1, 26)))  # all unique 1..25


class TestReliabilityRender(unittest.TestCase):
    def test_terminal_no_alarm_shows_done_not_pending(self):
        from board.render import card_grid
        run = {"run_id": "r1", "repo": "o/x", "issue": 1, "title": "t",
               "phase": "done", "status": "done", "machine": "dev1",
               "updated_str": "2026-06-16 10:00:00", "gate": {}, "alarms": []}
        html = card_grid([], [run], "v1", {})
        self.assertIn("done✓", html)          # the done✓ pill
        # a finished run does NOT show a wall of pending gate pills
        self.assertNotIn("/rcr", html)

    def test_terminal_with_alarm_shows_gate_pills(self):
        from board.render import card_grid
        run = {"run_id": "r1", "repo": "o/x", "issue": 1, "title": "t",
               "phase": "done", "status": "done", "machine": "dev1",
               "gate": {"plan_check": "fail"},
               "alarms": ["MERGED_INCOMPLETE_GATE"]}
        html = card_grid([], [run], "v1", {})
        self.assertIn("MERGED_INCOMPLETE_GATE", html)

    def test_card_shows_updated_timestamp(self):
        from board.render import card_grid
        run = {"run_id": "r1", "repo": "o/x", "issue": 1, "title": "t",
               "phase": "implementing", "status": None, "machine": "dev1",
               "updated_str": "2026-06-16 11:22:33", "gate": {}, "alarms": []}
        html = card_grid([run], [], "v1", {})
        self.assertIn("2026-06-16 11:22:33", html)

    def test_header_shows_gh_stale_banner(self):
        from board.render import card_grid
        html = card_grid([], [], "v1",
                         {"gh_stale": True, "gh_stale_since": "2026-06-16 09:00:00"})
        self.assertIn("STALE", html)
