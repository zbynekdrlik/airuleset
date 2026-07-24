"""Hook-level enforcement: a SUBAGENT must never end its turn with in-flight
background work (airuleset #28).

ci-monitoring.md has said it for weeks — "inside a subagent, wait FOREGROUND"
— and workers violate it anyway (~40% of autopilot-worker failures; odoo-erp
worker #2061/PR #2063 ended its turn with an active background Monitor and
terminated, 2026-07-24; restreamer specimen agent-a4cd262f…: transcript ends
with 'Command running in background with ID: b2w33fmts' and nothing after).
A subagent with no pending foreground call is returned as "completed" and the
detached task's completion fires to the PARENT — the subagent silently dies.
Rules don't bind a reduced-prompt worker session; hooks do (model-awareness:
"critical enforcement goes in hooks, not rules").

Two layers, both fail-open on anything unparseable:

- SubagentStop `subagent-stop-check-bg-work.sh` — parses the subagent's OWN
  transcript: background launches (toolUseResult.backgroundTaskId / .taskId /
  isAsync+agentId) minus terminal completions (a task-notification carrying
  BOTH `<task-id>` and `<status>`, or a TaskStop/KillShell naming the id).
  Live remainder → {"decision":"block"} forcing the subagent to continue
  (foreground poll / TaskOutput / TaskStop). Retry-capped, then fail-open
  (the transcript is written async and may lag).
- PreToolUse(Bash) `block-subagent-bg-ci-poll.sh` — in subagent context
  (payload carries agent_id) a `run_in_background` CI poll (`gh run …`,
  `gh pr checks`) is NEVER right: deny at launch with the foreground pattern.
"""

import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset

REPO = Path(airuleset.__file__).resolve().parent
STOP_HOOK = REPO / "hooks" / "subagent-stop-check-bg-work.sh"
PRE_HOOK = REPO / "hooks" / "block-subagent-bg-ci-poll.sh"
RECORD_HOOK = REPO / "hooks" / "post-record-subagent-bg-launch.sh"


def _jl(**kw):
    return json.dumps(kw)


def bash_bg_launch(task_id, sidecar=True):
    # MAIN-session-style entry carries a toolUseResult sidecar; the SUBAGENT
    # transcript (restreamer agent-a4cd262f… specimen, verified 2026-07-24)
    # has ONLY the tool_result content string — no sidecar. Both must detect.
    kw = dict(type="user", isSidechain=True,
              message={"role": "user", "content": [
                  {"type": "tool_result", "tool_use_id": "toolu_01X",
                   "is_error": False,
                   "content": "Command running in background with ID: %s. "
                              "Output is being written to: /tmp/x/tasks/"
                              "%s.output" % (task_id, task_id)}]})
    if sidecar:
        kw["toolUseResult"] = {"stdout": "", "stderr": "",
                               "interrupted": False,
                               "backgroundTaskId": task_id}
    return _jl(**kw)


def monitor_launch(task_id):
    return _jl(type="user", isSidechain=True,
               message={"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "toolu_02M",
                    "content": "Monitor started (task %s, timeout 360000ms)."
                               % task_id}]},
               toolUseResult={"taskId": task_id, "timeoutMs": 360000,
                              "persistent": False})


def agent_bg_launch(agent_id):
    return _jl(type="user", isSidechain=True,
               message={"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "toolu_03A",
                    "content": "Async agent launched successfully. agentId: %s"
                               % agent_id}]},
               toolUseResult={"isAsync": True, "status": "async_launched",
                              "agentId": agent_id})


def terminal_notification(task_id, status="completed"):
    xml = ("[SYSTEM NOTIFICATION - NOT USER INPUT]\n<task-notification>\n"
           "<task-id>%s</task-id>\n<status>%s</status>\n"
           "<summary>Background command completed (exit code 0)</summary>\n"
           "</task-notification>" % (task_id, status))
    return _jl(type="user", isMeta=True,
               origin={"kind": "task-notification"},
               message={"role": "user", "content": xml})


def midstream_event(task_id):
    # a Monitor mid-stream event has NO <status> — the task is still live
    xml = ("<task-notification>\n<task-id>%s</task-id>\n"
           "<summary>Monitor event: \"progress=42\"</summary>\n"
           "<event>progress=42</event>\n</task-notification>" % task_id)
    return _jl(type="user", isMeta=True,
               origin={"kind": "task-notification"},
               message={"role": "user", "content": xml})


def task_stop(task_id):
    return _jl(type="assistant", isSidechain=True,
               message={"role": "assistant", "content": [
                   {"type": "tool_use", "id": "toolu_04S", "name": "TaskStop",
                    "input": {"task_id": task_id}}]})


class SubagentStopHookBase(unittest.TestCase):
    def setUp(self):
        import glob
        self.addCleanup(lambda: [os.remove(f) for f in glob.glob(
            "/tmp/airuleset-subagent-bgwork-block-t-*")
            + glob.glob("/tmp/airuleset-bgtasks-t-*")])

    def _run(self, lines, agent_id="aTESTAGENT1", transcript_path=None,
             sid=None, background_tasks=None, parent_lines=None,
             ledger=None):
        sid = sid or ("t-" + uuid.uuid4().hex[:10])
        if ledger is not None:
            Path("/tmp/airuleset-bgtasks-%s-%s" % (sid, agent_id)).write_text(
                "\n".join(ledger) + "\n")
        with TemporaryDirectory() as d:
            tr = transcript_path
            if tr is None:
                tr = str(Path(d) / ("agent-%s.jsonl" % agent_id))
                Path(tr).write_text("\n".join(lines) + "\n")
            parent = str(Path(d) / "session.jsonl")
            Path(parent).write_text("\n".join(parent_lines or []) + "\n")
            payload = {
                "session_id": sid, "hook_event_name": "SubagentStop",
                "agent_id": agent_id, "agent_type": "autopilot-worker",
                "last_assistant_message": "⏳ monitoring CI run",
                "transcript_path": parent,
                "agent_transcript_path": tr}
            if background_tasks is not None:
                payload["background_tasks"] = background_tasks
            return subprocess.run(["bash", str(STOP_HOOK)],
                                  input=json.dumps(payload),
                                  capture_output=True, text=True)


class TestSubagentStopBlocksLiveBgWork(SubagentStopHookBase):
    def test_live_background_bash_blocks(self):
        out = self._run([bash_bg_launch("b2w33fmts")])
        self.assertIn("block", out.stdout)
        self.assertIn("b2w33fmts", out.stdout)
        self.assertIn("FOREGROUND", out.stdout)

    def test_completed_background_bash_passes(self):
        out = self._run([bash_bg_launch("b2w33fmts"),
                         terminal_notification("b2w33fmts")])
        self.assertNotIn("block", out.stdout)

    def test_failed_status_is_terminal_too(self):
        out = self._run([bash_bg_launch("bfail1"),
                         terminal_notification("bfail1", status="failed")])
        self.assertNotIn("block", out.stdout)

    def test_live_monitor_blocks(self):
        out = self._run([monitor_launch("buub5aih3")])
        self.assertIn("block", out.stdout)

    def test_monitor_midstream_event_is_not_terminal(self):
        # the odoo-erp #2061 shape: events flowing, stream never ended
        out = self._run([monitor_launch("buub5aih3"),
                         midstream_event("buub5aih3")])
        self.assertIn("block", out.stdout)

    def test_monitor_stream_ended_passes(self):
        out = self._run([monitor_launch("buub5aih3"),
                         midstream_event("buub5aih3"),
                         terminal_notification("buub5aih3")])
        self.assertNotIn("block", out.stdout)

    def test_live_background_child_agent_blocks(self):
        out = self._run([agent_bg_launch("a38306d0f8a10da9d")])
        self.assertIn("block", out.stdout)

    def test_task_stopped_id_passes(self):
        out = self._run([bash_bg_launch("b2w33fmts"),
                         task_stop("b2w33fmts")])
        self.assertNotIn("block", out.stdout)

    def test_no_background_work_passes(self):
        out = self._run([_jl(type="assistant", isSidechain=True,
                             message={"role": "assistant", "content": [
                                 {"type": "text", "text": "done"}]})])
        self.assertNotIn("block", out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_subagent_shape_without_sidecar_blocks(self):
        # THE real failure shape (restreamer agent-a4cd262f…): the subagent
        # transcript's launch entry has NO toolUseResult — only the
        # tool_result content string. Caught live 2026-07-24: the deployed
        # hook passed the actual specimen while synthetic fixtures blocked.
        out = self._run([bash_bg_launch("b2w33fmts", sidecar=False)])
        self.assertIn("block", out.stdout)
        self.assertIn("b2w33fmts", out.stdout)

    def test_sidecarless_launch_with_completion_passes(self):
        out = self._run([bash_bg_launch("b2w33fmts", sidecar=False),
                         terminal_notification("b2w33fmts")])
        self.assertNotIn("block", out.stdout)

    def test_assistant_quoting_launch_text_is_not_a_launch(self):
        # an assistant TEXT block merely quoting the harness wording (a
        # research digest, this very repo's tests) must not count
        out = self._run([_jl(type="assistant", isSidechain=True,
                             message={"role": "assistant", "content": [
                                 {"type": "text",
                                  "text": "the result said 'Command running "
                                          "in background with ID: bquoted1' "
                                          "and Monitor started (task bq2)"}]})])
        self.assertNotIn("block", out.stdout)


def self_task(agent_id, status="running"):
    return {"id": agent_id, "type": "subagent", "status": status,
            "description": "Launch background sleep task",
            "agent_type": "general-purpose"}


class TestPayloadBackgroundTasks(SubagentStopHookBase):
    """Live-fired 2026-07-24 (headless E2E, CC 2.1.x): the REAL SubagentStop
    payload carries `background_tasks` — the harness's OWN live-task list
    (shells, monitors, child subagents) with current statuses, INCLUDING an
    entry for the stopping subagent itself (id == agent_id). That list is
    authoritative and lag-free; the async-written transcript missed a launch
    on the first live run (no block) and over-blocked after cleanup on the
    second (counter=2). When the key is present the hook must trust it alone;
    the transcript parse stays only as the fallback for CC versions without
    the field."""

    AID = "acf0601a75908549f"

    def test_payload_running_owned_shell_blocks(self):
        # liveness from the payload + ownership from the OWN transcript
        # (the launch record) — both hold → block
        out = self._run([bash_bg_launch("b26esxqc2", sidecar=False)],
                        agent_id=self.AID,
                        background_tasks=[
                            self_task(self.AID),
                            {"id": "b26esxqc2", "type": "shell",
                             "status": "running", "command": "sleep 90"}])
        self.assertIn("block", out.stdout)
        self.assertIn("b26esxqc2", out.stdout)

    def test_payload_self_entry_only_passes_despite_stale_transcript(self):
        # after TaskStop the payload lists only the subagent's own entry —
        # the stale transcript still shows the launch; payload must win
        out = self._run([bash_bg_launch("b26esxqc2", sidecar=False)],
                        agent_id=self.AID,
                        background_tasks=[self_task(self.AID)])
        self.assertNotIn("block", out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_payload_completed_shell_passes(self):
        out = self._run([], agent_id=self.AID,
                        background_tasks=[
                            self_task(self.AID),
                            {"id": "bdone1", "type": "shell",
                             "status": "completed", "command": "make"}])
        self.assertNotIn("block", out.stdout)

    def test_payload_running_owned_child_subagent_blocks(self):
        out = self._run([agent_bg_launch("a38306d0f8a10da9d")],
                        agent_id=self.AID,
                        background_tasks=[
                            self_task(self.AID),
                            {"id": "a38306d0f8a10da9d", "type": "subagent",
                             "status": "running",
                             "agent_type": "general-purpose"}])
        self.assertIn("block", out.stdout)
        self.assertIn("a38306d0f8a10da9d", out.stdout)


class TestSiblingTasksNeverBlock(SubagentStopHookBase):
    """airuleset #29 (odoo-erp, 2026-07-24): `background_tasks` is
    SESSION-wide — a healthy review subagent that launched NOTHING in the
    background was blocked over 5 in-flight tasks ALL belonging to sibling
    workers dispatched by the supervisor. It could not TaskStop them (not
    the owner), so it had no legitimate way to satisfy the hook. Ownership
    filter: a payload task blocks ONLY when its LAUNCH record exists in the
    stopping subagent's OWN transcript; liveness stays payload-driven."""

    AID = "a067c0000000000ff"
    SIBLINGS = [{"id": "bsib%d" % i, "type": "shell", "status": "running",
                 "command": "sleep 300 && gh run view %d" % i}
                for i in range(5)]

    def test_sibling_only_tasks_pass(self):
        # own transcript = foreground work only, no launches
        out = self._run(
            [_jl(type="assistant", isSidechain=True,
                 message={"role": "assistant", "content": [
                     {"type": "text", "text": "review done, all foreground"}]})],
            agent_id=self.AID,
            background_tasks=[self_task(self.AID)] + list(self.SIBLINGS))
        self.assertNotIn("block", out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_owned_task_still_blocks_among_siblings(self):
        # the one task THIS subagent launched blocks; the siblings never do
        out = self._run(
            [bash_bg_launch("bmine1", sidecar=False)],
            agent_id=self.AID,
            background_tasks=[self_task(self.AID),
                              {"id": "bmine1", "type": "shell",
                               "status": "running", "command": "sleep 600"}]
            + list(self.SIBLINGS))
        self.assertIn("block", out.stdout)
        self.assertIn("bmine1", out.stdout)
        self.assertNotIn("bsib", out.stdout,
                         "sibling ids must never appear in the reason")

    def test_unreadable_transcript_means_no_ownership_no_block(self):
        out = self._run([], transcript_path="/nonexistent/agent-z.jsonl",
                        agent_id=self.AID,
                        background_tasks=[self_task(self.AID)]
                        + list(self.SIBLINGS))
        self.assertNotIn("block", out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_empty_payload_list_passes_without_touching_transcript(self):
        out = self._run([bash_bg_launch("bstale1", sidecar=False)],
                        agent_id=self.AID, background_tasks=[])
        self.assertNotIn("block", out.stdout)


class TestFallbackTranscriptSelection(SubagentStopHookBase):
    def test_fallback_reads_agent_transcript_not_parent(self):
        # no background_tasks key → fallback parses agent_transcript_path
        # (the subagent's own file); transcript_path is the PARENT session
        # (live payload 2026-07-24) and contains no subagent launches
        out = self._run([bash_bg_launch("bfall1", sidecar=False)],
                        parent_lines=[_jl(type="assistant",
                                          message={"role": "assistant",
                                                   "content": []})])
        self.assertIn("block", out.stdout)
        self.assertIn("bfall1", out.stdout)


class TestLedgerOwnership(SubagentStopHookBase):
    """The live-lag fix (#29 follow-through): the agent transcript is written
    ASYNC, so a launch seconds before the stop is often NOT in the file yet —
    live E2E showed the ownership-by-transcript gate letting an abandoning
    subagent through. A PostToolUse recorder writes each background launch to
    a per-(session, agent) ledger SYNCHRONOUSLY at launch time; SubagentStop
    unions ledger + transcript for ownership. The ledger is removed when the
    stop passes."""

    AID = "a72ef9d62dcb0beb8"

    def _sid(self):
        return "t-led-" + uuid.uuid4().hex[:8]

    def test_ledger_owned_task_blocks_despite_lagged_transcript(self):
        sid = self._sid()
        out = self._run([], agent_id=self.AID, sid=sid,
                        ledger=["be5t8pqaa"],
                        background_tasks=[
                            self_task(self.AID),
                            {"id": "be5t8pqaa", "type": "shell",
                             "status": "running", "command": "sleep 45"}])
        self.assertIn("block", out.stdout)
        self.assertIn("be5t8pqaa", out.stdout)

    def test_sibling_ids_not_in_ledger_still_pass(self):
        out = self._run([], agent_id=self.AID, sid=self._sid(),
                        ledger=["bmine9"],
                        background_tasks=[
                            self_task(self.AID),
                            {"id": "bsibling9", "type": "shell",
                             "status": "running", "command": "sleep 300"}])
        self.assertNotIn("block", out.stdout)

    def test_stop_gate_sanitizes_ids_consistently_with_recorder(self):
        # the stop gate must sanitize session_id/agent_id the SAME way as the
        # recorder, or the ledger written at launch is never found at stop
        safe = Path("/tmp/airuleset-bgtasks-t-led-evil-aX2")
        safe.write_text("bown1\n")
        self.addCleanup(lambda: safe.unlink(missing_ok=True))
        payload = {"session_id": "../t-led-evil", "agent_id": "a/../X2",
                   "hook_event_name": "SubagentStop",
                   "transcript_path": "/nonexistent/x.jsonl",
                   "agent_transcript_path": "/nonexistent/x.jsonl",
                   "background_tasks": [
                       {"id": "bown1", "type": "shell", "status": "running"}]}
        out = subprocess.run(["bash", str(STOP_HOOK)],
                             input=json.dumps(payload),
                             capture_output=True, text=True)
        self.assertIn("block", out.stdout,
                      "the sanitized ledger path must be found → block")
        Path("/tmp/airuleset-subagent-bgwork-block-t-led-evil-aX2"
             ).unlink(missing_ok=True)

    def test_ledger_removed_when_stop_passes(self):
        sid = self._sid()
        path = Path("/tmp/airuleset-bgtasks-%s-%s" % (sid, self.AID))
        out = self._run([], agent_id=self.AID, sid=sid,
                        ledger=["bolddone1"],
                        background_tasks=[self_task(self.AID)])
        self.assertNotIn("block", out.stdout)
        self.assertFalse(path.exists(),
                         "a passing stop must clean up the agent's ledger")


class TestPostToolUseRecorder(unittest.TestCase):
    """post-record-subagent-bg-launch.sh — the synchronous ownership ledger.
    Live PostToolUse payload (captured 2026-07-24): subagent context carries
    agent_id; tool_response is the structured sidecar (backgroundTaskId for
    Bash, taskId for Monitor, isAsync+agentId for a background Agent)."""

    def setUp(self):
        import glob
        self.addCleanup(lambda: [os.remove(f) for f in glob.glob(
            "/tmp/airuleset-bgtasks-t-rec-*")])

    def _run(self, tool_response, agent_id="aREC1", sid=None,
             tool_name="Bash"):
        sid = sid or ("t-rec-" + uuid.uuid4().hex[:8])
        payload = {"session_id": sid, "hook_event_name": "PostToolUse",
                   "tool_name": tool_name,
                   "tool_input": {"command": "x"},
                   "tool_response": tool_response}
        if agent_id:
            payload["agent_id"] = agent_id
            payload["agent_type"] = "general-purpose"
        r = subprocess.run(["bash", str(RECORD_HOOK)],
                           input=json.dumps(payload),
                           capture_output=True, text=True)
        return r, Path("/tmp/airuleset-bgtasks-%s-%s" % (sid, agent_id))

    def test_bash_background_launch_recorded(self):
        r, ledger = self._run({"stdout": "", "stderr": "",
                               "backgroundTaskId": "be5t8pqaa"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("be5t8pqaa", ledger.read_text())

    def test_monitor_launch_recorded(self):
        r, ledger = self._run({"taskId": "buub5aih3", "timeoutMs": 360000,
                               "persistent": False}, tool_name="Monitor")
        self.assertIn("buub5aih3", ledger.read_text())

    def test_background_agent_dispatch_recorded(self):
        r, ledger = self._run({"isAsync": True, "status": "async_launched",
                               "agentId": "a38306d0f8a10da9d"},
                              tool_name="Agent")
        self.assertIn("a38306d0f8a10da9d", ledger.read_text())

    def test_foreground_agent_not_recorded(self):
        r, ledger = self._run({"status": "completed",
                               "agentId": "afgabc123"}, tool_name="Agent")
        self.assertFalse(ledger.exists())

    def test_main_session_never_recorded(self):
        r, ledger = self._run({"backgroundTaskId": "bmainx1"}, agent_id="")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(
            Path("/tmp/airuleset-bgtasks-%s-" % "t-rec-none").exists())

    def test_foreground_bash_not_recorded(self):
        r, ledger = self._run({"stdout": "ok", "stderr": "",
                               "interrupted": False})
        self.assertFalse(ledger.exists())

    def test_path_traversal_ids_are_sanitized(self):
        # review finding 2026-07-24: session_id/agent_id come from the hook
        # payload and land in a /tmp file path — '../'-style values must be
        # stripped to [A-Za-z0-9_-] before path construction. Sanitized:
        # '../t-rec-evil' → 't-rec-evil', 'a/../X1' → 'aX1'.
        safe = Path("/tmp/airuleset-bgtasks-t-rec-evil-aX1")
        self.addCleanup(lambda: safe.unlink(missing_ok=True))
        payload = {"session_id": "../t-rec-evil", "agent_id": "a/../X1",
                   "hook_event_name": "PostToolUse", "tool_name": "Bash",
                   "tool_input": {"command": "x"},
                   "tool_response": {"backgroundTaskId": "btrav1"}}
        r = subprocess.run(["bash", str(RECORD_HOOK)],
                           input=json.dumps(payload),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(safe.exists(),
                        "ledger must land at the SANITIZED path")
        self.assertIn("btrav1", safe.read_text())
        self.assertFalse(Path("/tmp/t-rec-evil-aX1").exists())


class TestSubagentStopFailsOpen(SubagentStopHookBase):
    def test_missing_transcript_passes(self):
        out = self._run([], transcript_path="/nonexistent/agent-x.jsonl")
        self.assertNotIn("block", out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_garbage_transcript_passes(self):
        out = self._run(["not json at all", "{broken"])
        self.assertNotIn("block", out.stdout)

    def test_retry_cap_fails_open(self):
        # transcript lag can false-positive — cap the block, then let go
        sid = "t-cap-" + uuid.uuid4().hex[:8]
        lines = [bash_bg_launch("bcap1")]
        blocks = 0
        last = None
        for _ in range(5):
            last = self._run(lines, sid=sid)
            if "block" in last.stdout:
                blocks += 1
            else:
                break
        self.assertGreaterEqual(blocks, 1)
        self.assertLessEqual(blocks, 3)
        self.assertNotIn("block", last.stdout,
                         "after the cap the hook must fail open")


class TestPreToolUseBgCiPollGuard(unittest.TestCase):
    def _run(self, command, run_in_background=True, agent_id="aWORKER1"):
        payload = {"session_id": "t-pre", "hook_event_name": "PreToolUse",
                   "tool_name": "Bash",
                   "tool_input": {"command": command,
                                  "run_in_background": run_in_background}}
        if agent_id:
            payload["agent_id"] = agent_id
            payload["agent_type"] = "autopilot-worker"
        return subprocess.run(["bash", str(PRE_HOOK)],
                              input=json.dumps(payload),
                              capture_output=True, text=True)

    def test_bg_ci_poll_in_subagent_denied(self):
        out = self._run("sleep 300 && gh run view 24421409735 --json status")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("FOREGROUND", out.stderr)

    def test_gh_pr_checks_bg_denied(self):
        out = self._run("gh pr checks 123 --watch")
        self.assertEqual(out.returncode, 2)

    def test_main_session_bg_poll_allowed(self):
        # no agent_id → main session; bg CI polls there are the SUPERVISOR
        # pattern and stay allowed
        out = self._run("sleep 300 && gh run view 1 --json status",
                        agent_id=None)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_foreground_ci_poll_in_subagent_allowed(self):
        out = self._run("sleep 300 && gh run view 1 --json status",
                        run_in_background=False)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_non_ci_background_in_subagent_allowed(self):
        # legit: start a dev server in background, test against it foreground
        out = self._run("python3 -m http.server 8123")
        self.assertEqual(out.returncode, 0, out.stderr)


class TestWiring(unittest.TestCase):
    def test_scripts_exist_and_executable(self):
        for p in (STOP_HOOK, PRE_HOOK, RECORD_HOOK):
            self.assertTrue(p.exists(), "missing hook: %s" % p)
            self.assertTrue(os.access(p, os.X_OK), "not executable: %s" % p)

    def test_hooks_json_wires_both(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        hooks = cfg["hooks"]
        sub = json.dumps(hooks.get("SubagentStop", []))
        self.assertIn("subagent-stop-check-bg-work.sh", sub)
        pre = json.dumps([m for m in hooks["PreToolUse"]
                          if m.get("matcher") == "Bash"])
        self.assertIn("block-subagent-bg-ci-poll.sh", pre)

    def test_recorder_wired_for_bash_monitor_agent(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        for tool in ("Bash", "Monitor", "Agent"):
            ms = json.dumps([m for m in cfg["hooks"]["PostToolUse"]
                             if m.get("matcher") == tool])
            self.assertIn("post-record-subagent-bg-launch.sh", ms,
                          "recorder missing for PostToolUse(%s)" % tool)

    def test_ci_monitoring_module_points_at_the_hook(self):
        txt = (REPO / "modules" / "core" / "ci-monitoring.md").read_text()
        self.assertIn("subagent-stop-check-bg-work.sh", txt)


if __name__ == "__main__":
    unittest.main()
