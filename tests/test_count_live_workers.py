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

def _assistant(text, stop_reason=None):
    msg = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    if stop_reason is not None:
        msg["stop_reason"] = stop_reason
    return json.dumps({"type": "assistant", "message": msg})


def _finished_terminal(text="issues: #1 done"):
    """A DEFINITELY-finished worker: a final text reply carrying a TERMINAL
    stop_reason (end_turn) — #587 classifies this `finished` at ANY fresh age
    (the ~78% common case, incl. autopilot-worker final reports)."""
    return _assistant(text, stop_reason="end_turn")


def _finished_settling(text="issues: #1 done"):
    """A structurally-finished worker with a NON-terminal (absent) stop_reason —
    #587's `settling` state (~18% of real finishes). Classified `finished` ONLY
    once its mtime is older than FINISH_SETTLE_S, so a fresh one stays live."""
    return _assistant(text)   # no stop_reason


def _running():
    """A genuinely RUNNING worker: its last real turn is a `tool_use` (a tool is
    executing, the result not back yet). After #587's finish-immediate
    reclassification a bare assistant TEXT turn reads as a FINISHED lane, so a
    fixture whose INTENT is "a live/running worker" must end in a tool_use (or a
    `user` tool_result tail) — never a lone `_assistant("w")`. Reads `live` under
    both the pre-#587 and post-#587 classifier."""
    return json.dumps({"type": "assistant",
                       "message": {"role": "assistant",
                                   "content": [{"type": "tool_use", "id": "t1",
                                                "name": "Bash", "input": {}}]}})


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
            _write_worker(root, CWD, SID, "aaa", [_running()], age_s=3)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])
            self.assertEqual(_by_id(ev, "aaa").state, "live")

    def test_three_fresh_workers_are_three_live_lanes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "aaa", [_running()], age_s=1)
            _write_worker(root, CWD, SID, "bbb", [_tool_result_user()], age_s=30)
            _write_worker(root, CWD, SID, "ccc", [_running()], age_s=200)
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
            _write_worker(root, CWD, SID, "edge", [_running()], age_s=FRESH)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_one_second_past_the_boundary_is_stale(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "past", [_running()], age_s=FRESH + 1)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["stale"])

    def test_nested_workflow_transcript_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "wf1", [_running()], age_s=5,
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
            _write_worker(root, CWD, SID, "alive", [_running()], age_s=2)
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

    def test_a_finished_worker_ending_in_a_normal_reply_is_finished_not_live(self):
        # #587 INVERTS the pre-#587 residual (a cleanly-finished worker "briefly
        # counts live until its mtime ages out"). A worker whose last real turn is
        # a normal assistant TEXT reply produced its final answer and RETURNED to
        # the parent — it is FINISHED, not executing in-flight work, so it must NOT
        # count live even while its mtime is still fresh. This is the whole fix: on
        # a per-ticket boundary (which ALWAYS follows a worker return) the ghost
        # lane no longer vetoes compact. The lane still appears in the evidence
        # (state="finished"), just excluded from the live count.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "done",
                          [_tool_result_user(), _finished_terminal("issues: #1 done")],
                          age_s=8)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["finished"])
            self.assertEqual(_by_id(ev, "done").state, "finished")
            # still FRESH (its age is inside the window) — the reclassification is
            # by CONTENT (a terminal final turn), NOT by ageing out.
            self.assertLess(_by_id(ev, "done").age_s, FRESH)


def _thinking():
    return json.dumps({"type": "assistant",
                       "message": {"role": "assistant",
                                   "content": [{"type": "thinking",
                                                "thinking": "planning"}]}})


def _plain_user(text="follow up"):
    return json.dumps({"type": "user",
                       "message": {"role": "user", "content": text}})


class TestFinishImmediate587(unittest.TestCase):
    """#587 — a cleanly-FINISHED worker drops out of the live liveness (its last
    real turn is a completed final TEXT response, no pending tool call), so a
    per-ticket compact boundary no longer sees a 15-min mtime ghost. Detection is
    CONSERVATIVE: a `terminal` (end_turn) final turn is finished at any fresh age;
    a `settling` (None-stop_reason) text-tail only once aged past FINISH_SETTLE_S
    (so a mid-stream running worker is never misread finished); a mid-tool-call /
    tool_result-tail / api-error / plain-user tail stays live. Behavioural,
    through the real classifier."""

    def test_terminal_finished_worker_is_finished_at_any_fresh_age(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "fin",
                          [_tool_result_user(), _finished_terminal("issues: #587 done")],
                          age_s=3)   # freshly finished — terminal stop_reason → immediate
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["finished"])

    def test_terminal_finished_with_a_trailing_thinking_then_text_is_finished(self):
        # one API response splits into [thinking, text] JSONL lines; the final
        # produced content is the TEXT (a trailing thinking-only line is a sentinel
        # that is skipped back to the real text). Terminal stop_reason → finished.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "fin2",
                          [_tool_result_user(), _thinking(),
                           _finished_terminal("All done: clean tree, PR merged.")],
                          age_s=5)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["finished"])

    def test_settling_finished_worker_stays_live_until_it_settles(self):
        # #587-review: a text-tail with a NON-terminal (None) stop_reason could be
        # a text block the model streamed just before a large tool_use in the SAME
        # message (a ~14s gap). A FRESH settling worker must stay LIVE (not yet
        # provably finished), so a running worker mid-large-edit is never orphaned.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "settling",
                          [_tool_result_user(), _finished_settling("issues: #587 done")],
                          age_s=5)   # < FINISH_SETTLE_S
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_settling_finished_worker_is_finished_once_aged_past_settle(self):
        # the same settling worker, now aged past FINISH_SETTLE_S (any pending
        # tool_use would have been flushed by now) → a genuine finish → not live.
        import tempfile
        from watchdog.transcripts import FINISH_SETTLE_S
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "settled",
                          [_tool_result_user(), _finished_settling("issues: #587 done")],
                          age_s=FINISH_SETTLE_S + 5)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["finished"])

    def test_running_mid_tool_call_worker_stays_live(self):
        # last real turn is a tool_use (a tool is executing) → RUNNING → live.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "run",
                          [_assistant("let me check"), _running()], age_s=60)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_tool_result_tail_worker_stays_live(self):
        # last entry is a user tool_result (tool just completed, the model is
        # generating its next turn) → RUNNING → live.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "tr",
                          [_running(), _tool_result_user()], age_s=30)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_plain_user_followup_tail_is_not_finished(self):
        # a parent SendMessage follow-up not yet acted on → the agent is about to
        # run → NOT finished (stays live), never misread as a completed lane.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "fu",
                          [_assistant("first answer"), _plain_user("do more")],
                          age_s=45)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_result_looking_block_mid_stream_then_tool_use_is_not_finished(self):
        # the adversarial false-positive: a RUNNING worker whose transcript
        # CONTAINS a completion-looking text block mid-stream, but whose LAST real
        # turn is a tool_use → still RUNNING → live. Only the LAST real turn
        # decides; a mid-stream result-looking block never flips it to finished.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "trap",
                          [_assistant("issues: #587 done (interim)"),
                           _tool_result_user(), _running()], age_s=90)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])

    def test_text_toolcall_stall_is_wedged_not_finished(self):
        # a tool-call emitted as TEXT then died on (a wedged lane, job 4a) must
        # NOT be misread as a finished final reply: the wedged check runs BEFORE
        # the finish check, so state is "wedged" (still live for compact), never
        # "finished". Guards the ordering.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "stall", [_textcall_stall()], age_s=100)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["wedged"])

    def test_api_error_lane_is_wedged_not_finished(self):
        # an unrecovered api-error is a wedged (recoverable, job-1-owned) lane,
        # NOT a clean finish — wedged check precedes finish, and worker_finished
        # itself returns False on an api-error last turn.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "err",
                          [_assistant("hi"), _api_error()], age_s=100)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["wedged"])

    def test_finished_and_running_mix_counts_only_running(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "fin", [_finished_terminal("done")], age_s=5)
            _write_worker(root, CWD, SID, "run", [_running()], age_s=5)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["finished", "live"])
            self.assertEqual(_by_id(ev, "fin").state, "finished")
            self.assertEqual(_by_id(ev, "run").state, "live")

    def test_terminal_finished_lane_at_the_freshness_boundary_is_finished_not_live(self):
        # a TERMINAL finish is by CONTENT, not age — a finished worker at exactly
        # the freshness boundary (still "fresh") is "finished", not "live".
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "edge", [_finished_terminal("done")],
                          age_s=FRESH)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["finished"])

    def test_stale_finished_worker_is_stale_not_finished(self):
        # the age gate runs first: a finished worker past the window is just
        # "stale" (never reaches the content classifier), same as any other lane.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "old", [_finished_terminal("done")],
                          age_s=FRESH + 60)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_states(ev), ["stale"])

    def test_finished_lane_carries_agent_type_from_meta(self):
        # a finished lane is still full evidence (agent_type enriched) — the
        # journal must be able to name it, it just is not counted live.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "typedfin", [_finished_terminal("done")],
                          age_s=10, meta={"agentType": "autopilot-worker"})
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 0)
            self.assertEqual(_by_id(ev, "typedfin").state, "finished")
            self.assertEqual(_by_id(ev, "typedfin").agent_type, "autopilot-worker")


class TestCrossSessionAttribution(unittest.TestCase):
    """Workers are keyed by (cwd, session_id) — a sibling session's workers in
    the same project must never leak into this session's count."""

    def test_another_sessions_workers_do_not_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "mine", [_running()], age_s=2)
            _write_worker(root, CWD, OTHER_SID, "theirs1", [_running()], age_s=2)
            _write_worker(root, CWD, OTHER_SID, "theirs2", [_running()], age_s=2)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_states(ev), ["live"])
            self.assertTrue(_by_id(ev, "mine"))

    def test_another_projects_workers_do_not_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "mine", [_running()], age_s=2)
            _write_worker(root, "/home/newlevel/devel/other", SID, "foreign",
                          [_running()], age_s=2)
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
            _write_worker(root, CWD, SID, "aaa", [_running()], age_s=2)
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

    def test_a_wrong_typed_text_block_degrades_to_unreadable_never_crashes(self):
        # valid JSON, but `"text": null` → _entry_text (used by both death-mode
        # detectors) would TypeError. The content scan must catch it, degrade the
        # lane to `unreadable` (safe under-count) + warn, and NOT crash the sweep —
        # and a real sibling worker in the same dir must still count (loop survives).
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            d = _subagents_dir(root, CWD, SID)
            d.mkdir(parents=True)
            _write_worker(root, CWD, SID, "real", [_running()], age_s=2)
            bad = d / "agent-badtype.jsonl"
            bad.write_text(json.dumps({"type": "assistant", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": None}]}}) + "\n")
            os.utime(bad, (NOW - 3, NOW - 3))
            warns = []
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH,
                                       on_warn=warns.append)
            self.assertEqual(n, 1)  # the real worker still counts; no crash
            self.assertEqual(_by_id(ev, "badtype").state, "unreadable")
            self.assertTrue(warns, "a content-scan failure must fire on_warn")

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
            _write_worker(root, CWD, SID, "real", [_running()], age_s=2)
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
            _write_worker(root, CWD, SID, "real", [_running()], age_s=2)
            (d / "agent-trap.jsonl").mkdir()  # a DIRECTORY matching *.jsonl
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            # the real worker still counts; the trap never raises
            self.assertEqual(n, 1)
            self.assertIn("live", _states(ev))


class TestEvidenceAndMeta(unittest.TestCase):
    def test_evidence_carries_agent_type_from_meta_sidecar(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "typed", [_running()], age_s=2,
                          meta={"agentType": "autopilot-worker",
                                "description": "Work issue #486 step G2"})
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertEqual(_by_id(ev, "typed").agent_type, "autopilot-worker")

    def test_missing_meta_leaves_agent_type_none_and_still_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "nometa", [_running()], age_s=2)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(n, 1)
            self.assertIsNone(_by_id(ev, "nometa").agent_type)

    def test_agent_id_identifies_the_lane(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "deadbeef", [_running()], age_s=2)
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertIn("deadbeef", ev[0].agent_id)

    def test_count_equals_number_of_live_lanes_in_evidence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            _write_worker(root, CWD, SID, "l1", [_running()], age_s=1)
            _write_worker(root, CWD, SID, "l2", [_running()], age_s=1)
            _write_worker(root, CWD, SID, "s1", [_running()], age_s=FRESH + 10)
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

    def test_a_file_where_subagents_should_be_reads_as_no_workers(self):
        # a FILE at the subagents path → is_dir() False → (0, []), no crash. This is
        # the not-a-dir behaviour; the default-stderr sink is exercised separately.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            sub = _subagents_dir(root, CWD, SID)
            sub.parent.mkdir(parents=True)
            sub.write_text("not a dir")  # subagents is a FILE → not.is_dir()
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual((n, ev), (0, []))

    def test_the_default_on_warn_sink_writes_to_stderr(self):
        # genuine coverage of _warn_stderr (the `# airuleset:script-ok` last-resort
        # sink): trigger the EACCES-is_dir warn path with NO on_warn passed, and
        # assert the default sink actually wrote to stderr. Without this, a mutant
        # breaking _warn_stderr survives (every other warn test passes a custom sink).
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            sid_dir = Path(root) / encode_project_dir(CWD) / SID
            (sid_dir / "subagents").mkdir(parents=True)
            os.chmod(sid_dir, 0o000)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stderr(buf):
                    n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
                self.assertEqual((n, ev), (0, []))
                self.assertIn("count_live_workers", buf.getvalue())
            finally:
                os.chmod(sid_dir, 0o755)  # let TemporaryDirectory clean up

    def test_worker_lane_is_the_exported_namedtuple(self):
        self.assertTrue(hasattr(transcripts, "WorkerLane"))
        wl = transcripts.WorkerLane(agent_id="x", state="live", age_s=1,
                                    agent_type=None, detail="")
        self.assertEqual(wl.state, "live")


class TestTranscriptWorkerFinishedUnit(unittest.TestCase):
    """#587 — direct unit coverage of the pure `transcript_worker_finished`
    reader (the classifier `count_live_workers` delegates the finish decision to).
    Returns a 3-state string: "terminal" (final text + end_turn/stop_sequence),
    "settling" (final text, non-terminal stop_reason — trusted only once settled),
    "" (running / unmeasurable). Written transcript files, no mtime dependence
    (the age gate lives in count_live_workers, not this reader)."""

    def _write(self, root, *lines):
        d = _subagents_dir(root, CWD, SID)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "agent-u.jsonl"
        p.write_text("\n".join(lines) + "\n")
        return p

    def test_terminal_stop_reason_text_is_terminal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _tool_result_user(), _finished_terminal("all done"))
            self.assertEqual(transcripts.transcript_worker_finished(p), "terminal")

    def test_stop_sequence_is_also_terminal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _assistant("done", stop_reason="stop_sequence"))
            self.assertEqual(transcripts.transcript_worker_finished(p), "terminal")

    def test_non_terminal_stop_reason_text_is_settling(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _tool_result_user(), _assistant("all done"))
            self.assertEqual(transcripts.transcript_worker_finished(p), "settling")

    def test_thinking_then_terminal_text_is_terminal(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _tool_result_user(), _thinking(),
                            _finished_terminal("final report body"))
            self.assertEqual(transcripts.transcript_worker_finished(p), "terminal")

    def test_tool_use_ending_turn_is_running(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _assistant("let me check"), _running())
            self.assertEqual(transcripts.transcript_worker_finished(p), "")

    def test_tool_result_tail_is_running(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _running(), _tool_result_user())
            self.assertEqual(transcripts.transcript_worker_finished(p), "")

    def test_plain_user_followup_tail_is_running(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _assistant("first"), _plain_user("more"))
            self.assertEqual(transcripts.transcript_worker_finished(p), "")

    def test_api_error_last_turn_is_running(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _assistant("hi"), _api_error())
            self.assertEqual(transcripts.transcript_worker_finished(p), "")

    def test_text_toolcall_stall_is_wedged_in_the_caller_not_finished(self):
        # a tool-call emitted as TEXT (no stop_reason) reads "settling" from the
        # PURE function (it is a text-ending turn), but count_live_workers checks
        # `wedged` (which owns this shape) FIRST, so the lane is `wedged`, never
        # `finished`. Locks the caller's ordering, not a mislabelled outcome.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            p = self._write(root, _textcall_stall())
            self.assertEqual(transcripts.transcript_worker_finished(p), "settling")
            n, ev = count_live_workers(root, CWD, SID, NOW, FRESH)
            self.assertEqual(_states(ev), ["wedged"])

    def test_wrong_typed_text_block_is_running_never_raises(self):
        # #587-review 🔵: a `"text": null` block makes _entry_text raise TypeError;
        # the reader must guard it (the facade re-exports this for direct callers),
        # degrading to "" (not finished), never propagating the exception.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            bad = json.dumps({"type": "assistant", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": None}]}})
            p = self._write(root, bad)
            self.assertEqual(transcripts.transcript_worker_finished(p), "")

    def test_empty_or_missing_transcript_is_running(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(
                transcripts.transcript_worker_finished(Path(root) / "nope.jsonl"), "")
            p = self._write(root)  # empty (just a newline)
            self.assertEqual(transcripts.transcript_worker_finished(p), "")

    def test_exported_on_the_watchdog_facade(self):
        import watchdog as wd
        self.assertTrue(hasattr(wd, "transcript_worker_finished"))
        self.assertIs(wd.transcript_worker_finished,
                      transcripts.transcript_worker_finished)


if __name__ == "__main__":
    unittest.main()
