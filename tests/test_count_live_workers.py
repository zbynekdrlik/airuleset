"""#486 step G2 — `watchdog.transcripts.count_live_workers`.

The STRUCTURED-STATE worker-count reader that replaces agent-strip parsing:
a live worker == a recently-written subagent transcript for THIS session.
Disk-only (no tmux, no ps, no network). Returns ``(count, [WorkerLane, ...])``
where ``count`` is exactly the number of ``state == "live"`` lanes.

These tests lock the behaviour the design comment promises, with mutation-teeth:
every count AND every per-lane state is asserted, so flipping the freshness
comparison, dropping the #484 api-error guard, or leaking a foreign session's
workers each fail a named test.

Time is injected (``now`` is a parameter and file mtimes are set with
``os.utime``), so the suite is deterministic and does not sleep.
"""

import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog.transcripts as transcripts  # noqa: E402
from watchdog.transcripts import count_live_workers, encode_project_dir  # noqa: E402

CWD = "/home/newlevel/devel/demo"
SID = "11111111-1111-1111-1111-111111111111"
OTHER_SID = "22222222-2222-2222-2222-222222222222"
NOW = 1_000_000.0
FRESH = 15 * 60  # 15 min window, matching today's GOAL_LANE_LIVE_WINDOW_S


# --- transcript-entry builders (only the shapes the reader inspects) ----------

def _assistant(text):
    return json.dumps({"type": "assistant",
                       "message": {"role": "assistant",
                                   "content": [{"type": "text", "text": text}]}})


def _api_error(text="API Error: overloaded_error"):
    return json.dumps({"type": "assistant", "isApiErrorMessage": True,
                       "message": {"role": "assistant",
                                   "content": [{"type": "text", "text": text}]}})


def _tool_result_user():
    return json.dumps({"type": "user",
                       "message": {"role": "user",
                                   "content": [{"type": "tool_result",
                                                "content": "ok"}]}})


def _textcall_stall():
    # an assistant turn whose text ENDS with a complete tool-call block the
    # harness never ran — the silent text-emitted-tool-call death mode (job 4a).
    return _assistant('<invoke name="Bash">'
                      '<parameter name="command">ls</parameter></invoke>')


def _subagents_dir(root, cwd, sid):
    return Path(root) / encode_project_dir(cwd) / sid / "subagents"


def _write_worker(root, cwd, sid, agent_id, lines, age_s, now=NOW,
                  meta=None, nested=None):
    """Write a subagent transcript at the proven live layout and stamp its
    mtime to exactly ``now - age_s``. ``nested`` puts it under a sub-dir
    (e.g. 'workflows/wf_x') to mirror the real workflows layout."""
    d = _subagents_dir(root, cwd, sid)
    if nested:
        d = d / nested
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("agent-" + agent_id + ".jsonl")
    p.write_text("\n".join(lines) + "\n")
    if meta is not None:
        (d / ("agent-" + agent_id + ".meta.json")).write_text(json.dumps(meta))
    m = now - age_s
    os.utime(p, (m, m))
    return p


def _states(evidence):
    return sorted(e.state for e in evidence)


def _by_id(evidence, agent_id):
    for e in evidence:
        if e.agent_id.endswith(agent_id):
            return e
    raise AssertionError("no evidence for %s in %r" % (agent_id, evidence))


class TestFreshnessCount(unittest.TestCase):
    def test_a_freshly_written_worker_is_one_live_lane(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "aaa", [_assistant("working")], age_s=3)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])
            self.assertEqual(_by_id(ev, "aaa").state, "live")

    def test_three_fresh_workers_are_three_live_lanes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "aaa", [_assistant("w")], age_s=1)
            _write_worker(root, CWD, SID, "bbb", [_tool_result_user()], age_s=30)
            _write_worker(root, CWD, SID, "ccc", [_assistant("w")], age_s=200)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 3)
            self.assertEqual(_states(ev), ["live", "live", "live"])

    def test_a_stale_worker_is_not_counted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "old", [_assistant("done")],
                          age_s=FRESH + 60)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["stale"])
            self.assertGreater(_by_id(ev, "old").age_s, FRESH)

    def test_freshness_boundary_is_inclusive(self):
        # age exactly == freshness_s counts as live (matches _count_live_subagents `<=`).
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "edge", [_assistant("w")], age_s=FRESH)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_one_second_past_the_boundary_is_stale(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "past", [_assistant("w")], age_s=FRESH + 1)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["stale"])

    def test_nested_workflow_transcript_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "wf1", [_assistant("w")], age_s=5,
                          nested="workflows/wf_abc")
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])


class TestApiErrorGuard(unittest.TestCase):
    """#484 class: a dead-but-recently-written transcript wedged on an
    unrecovered api-error must NOT read as a live lane (over-count would
    suppress the recovery nudge — the exact #486 failure)."""

    def test_fresh_worker_wedged_on_api_error_is_not_live(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "dead", [_assistant("hi"), _api_error()],
                          age_s=5)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["wedged"])
            self.assertEqual(_by_id(ev, "dead").state, "wedged")

    def test_api_error_then_genuine_progress_recovers_and_counts(self):
        # #484 recovery nuance: an api-error FOLLOWED by a tool_result (the harness
        # ran a tool → alive) is recovered → the worker IS a live lane again.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "recov",
                          [_api_error(), _tool_result_user()], age_s=5)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_a_stale_api_error_worker_is_stale_not_wedged(self):
        # The api-error scan only runs on FRESH candidates; a stale one is just stale.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "olderr", [_api_error()], age_s=FRESH + 100)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["stale"])

    def test_wedged_and_live_mix_counts_only_the_live(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "alive", [_assistant("w")], age_s=2)
            _write_worker(root, CWD, SID, "dead", [_api_error()], age_s=2)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live", "wedged"])

    def test_a_text_toolcall_stalled_worker_is_wedged_not_live(self):
        # the SECOND silent-death mode: a tool-call emitted as TEXT (job 4a). Same
        # dangerous direction as api-error — over-counting it live suppresses the
        # nudge — so it must be excluded from the live count too.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "stalled", [_textcall_stall()], age_s=4)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["wedged"])
            self.assertIn("text-toolcall", _by_id(ev, "stalled").detail)

    def test_a_finished_worker_ending_in_a_normal_reply_still_counts_live(self):
        # the honest residual: a cleanly-finished worker (last turn a normal
        # assistant reply, no error, no text-stall) briefly counts live until its
        # mtime ages out — CORRECT (the box just finished, it is not stuck).
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "done",
                          [_tool_result_user(), _assistant("issues: #1 done")],
                          age_s=8)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])


class TestCrossSessionAttribution(unittest.TestCase):
    """Workers are keyed by (cwd, session_id) — a sibling session's workers in
    the same project must never leak into this session's count."""

    def test_another_sessions_workers_do_not_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "mine", [_assistant("w")], age_s=2)
            _write_worker(root, CWD, OTHER_SID, "theirs1", [_assistant("w")], age_s=2)
            _write_worker(root, CWD, OTHER_SID, "theirs2", [_assistant("w")], age_s=2)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])
            self.assertTrue(_by_id(ev, "mine"))

    def test_another_projects_workers_do_not_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "mine", [_assistant("w")], age_s=2)
            _write_worker(root, "/home/newlevel/devel/other", SID, "foreign",
                          [_assistant("w")], age_s=2)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])


class TestAbsentAndMainOnly(unittest.TestCase):
    def test_no_subagents_dir_is_zero_no_warn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            # a main-session-only layout: <project>/<sid>.jsonl, no subagents/ dir
            d = Path(root) / encode_project_dir(CWD)
            d.mkdir(parents=True)
            (d / (SID + ".jsonl")).write_text(_assistant("main") + "\n")
            warns = []
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH,
                                       on_warn=warns.append)
            self.assertEqual(n, 0)
            self.assertEqual(ev, [])
            self.assertEqual(warns, [])  # absent dir is normal, not an error

    def test_absent_project_dir_is_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual((n, ev), (0, []))

    def test_the_main_transcript_is_never_counted_as_a_worker(self):
        # <project>/<sid>.jsonl is a sibling of the <sid>/ dir, never inside
        # <sid>/subagents/, so even a fresh main transcript is not a worker.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "aaa", [_assistant("w")], age_s=2)
            d = Path(root) / encode_project_dir(CWD)
            main = d / (SID + ".jsonl")
            main.write_text(_assistant("main") + "\n")
            os.utime(main, (NOW, NOW))
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])


class TestCorruptTolerance(unittest.TestCase):
    def test_a_corrupt_fresh_transcript_does_not_crash_and_counts_live(self):
        # A half-written JSON tail is genuine WRITE activity (fresh) and cannot be
        # proven wedged → fail-safe toward live (transcript_last_error returns '').
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            d = _subagents_dir(root, CWD, SID)
            d.mkdir(parents=True)
            p = d / "agent-corrupt.jsonl"
            p.write_text('{"type": "assistant", "message": {"content"')  # truncated
            os.utime(p, (NOW - 4, NOW - 4))
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_an_empty_fresh_file_counts_live_without_crashing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            d = _subagents_dir(root, CWD, SID)
            d.mkdir(parents=True)
            p = d / "agent-empty.jsonl"
            p.write_text("")
            os.utime(p, (NOW - 2, NOW - 2))
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_a_per_file_stat_error_is_an_unreadable_lane_and_warns(self):
        # a *.jsonl broken symlink is yielded by rglob but stat() raises → the lane
        # is state="unreadable" (NOT counted) and on_warn fires. Locks the contract
        # so a mutant dropping the unreadable append OR the warn call is caught.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            d = _subagents_dir(root, CWD, SID)
            d.mkdir(parents=True)
            _write_worker(root, CWD, SID, "real", [_assistant("w")], age_s=2)
            os.symlink("/nonexistent/target", d / "agent-broken.jsonl")
            warns = []
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH,
                                       on_warn=warns.append)
            self.assertEqual(n, 1)  # the real worker still counts
            self.assertEqual(_by_id(ev, "broken").state, "unreadable")
            self.assertTrue(warns, "a stat error must fire on_warn")
            self.assertIn("broken", warns[0])

    def test_is_dir_permission_error_is_safe_and_never_raises(self):
        # is_dir() propagates EACCES (unlike ENOENT). A search-permission failure on
        # a parent must return 0 + warn, never crash the sweep the reader guards.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            sid_dir = Path(root) / encode_project_dir(CWD) / SID
            (sid_dir / "subagents").mkdir(parents=True)
            os.chmod(sid_dir, 0o000)
            try:
                warns = []
                n, ev = count_live_workers(root, CWD, SID, NOW, FRESH,
                                           on_warn=warns.append)
                self.assertEqual((n, ev), (0, []))       # safe direction, no crash
                self.assertTrue(warns, "an EACCES must fire on_warn")
            finally:
                os.chmod(sid_dir, 0o755)  # let TemporaryDirectory clean up

    def test_a_dir_named_like_a_jsonl_never_crashes_the_sweep(self):
        # rglob("*.jsonl") can match a directory named "...jsonl"; a stat/open of it
        # must be tolerated (unreadable), never raise out of the reader.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            d = _subagents_dir(root, CWD, SID)
            d.mkdir(parents=True)
            _write_worker(root, CWD, SID, "real", [_assistant("w")], age_s=2)
            (d / "agent-trap.jsonl").mkdir()  # a DIRECTORY matching *.jsonl
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            # the real worker still counts; the trap never raises
            self.assertEqual(n, 1)
            self.assertIn("live", _states(ev))


class TestEvidenceAndMeta(unittest.TestCase):
    def test_evidence_carries_agent_type_from_meta_sidecar(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "typed", [_assistant("w")], age_s=2,
                          meta={"agentType": "autopilot-worker",
                                "description": "Work issue #486 step G2"})
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_by_id(ev, "typed").agent_type, "autopilot-worker")

    def test_missing_meta_leaves_agent_type_none_and_still_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "nometa", [_assistant("w")], age_s=2)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertIsNone(_by_id(ev, "nometa").agent_type)

    def test_agent_id_identifies_the_lane(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "deadbeef", [_assistant("w")], age_s=2)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertIn("deadbeef", ev[0].agent_id)

    def test_count_equals_number_of_live_lanes_in_evidence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "l1", [_assistant("w")], age_s=1)
            _write_worker(root, CWD, SID, "l2", [_assistant("w")], age_s=1)
            _write_worker(root, CWD, SID, "s1", [_assistant("w")], age_s=FRESH + 10)
            _write_worker(root, CWD, SID, "d1", [_api_error()], age_s=1)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, sum(1 for e in ev if e.state == "live"))
            self.assertEqual(n, 2)


class TestNeverRaises(unittest.TestCase):
    def test_projects_dir_is_a_file_not_a_dir_returns_zero(self):
        import tempfile
        with tempfile.NamedTemporaryFile() as f:
            n, ev = count_live_workers(f.name, CWD, SID, NOW, FRESH)
            self.assertEqual((n, ev), (0, []))

    def test_default_on_warn_writes_to_stderr_and_does_not_raise(self):
        # An unreadable subagents dir path must warn (default → stderr) and return 0,
        # never raise. Simulate by making the subagents path a FILE.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            sub = _subagents_dir(root, CWD, SID)
            sub.parent.mkdir(parents=True)
            sub.write_text("not a dir")  # subagents is a FILE → not.is_dir()
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual((n, ev), (0, []))  # not-a-dir → treated as absent → 0

    def test_worker_lane_is_the_exported_namedtuple(self):
        self.assertTrue(hasattr(transcripts, "WorkerLane"))
        wl = transcripts.WorkerLane(agent_id="x", state="live", age_s=1,
                                    agent_type=None, detail="")
        self.assertEqual(wl.state, "live")


if __name__ == "__main__":
    unittest.main()
