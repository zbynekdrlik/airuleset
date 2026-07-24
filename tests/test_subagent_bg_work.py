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


def _jl(**kw):
    return json.dumps(kw)


def bash_bg_launch(task_id):
    # real shape: camera-box 90bc51f3… / restreamer agent-a4cd262f… specimens
    return _jl(type="user", isSidechain=True,
               message={"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "toolu_01X",
                    "content": "Command running in background with ID: %s. "
                               "Output is being written to: /tmp/x/tasks/"
                               "%s.output." % (task_id, task_id)}]},
               toolUseResult={"stdout": "", "stderr": "", "interrupted": False,
                              "backgroundTaskId": task_id})


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
    def _run(self, lines, agent_id="aTESTAGENT1", transcript_path=None,
             sid=None):
        sid = sid or ("t-" + uuid.uuid4().hex[:10])
        with TemporaryDirectory() as d:
            tr = transcript_path
            if tr is None:
                tr = str(Path(d) / ("agent-%s.jsonl" % agent_id))
                Path(tr).write_text("\n".join(lines) + "\n")
            payload = json.dumps({
                "session_id": sid, "hook_event_name": "SubagentStop",
                "agent_id": agent_id, "agent_type": "autopilot-worker",
                "last_assistant_message": "⏳ monitoring CI run",
                "transcript_path": tr})
            return subprocess.run(["bash", str(STOP_HOOK)], input=payload,
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
        for p in (STOP_HOOK, PRE_HOOK):
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

    def test_ci_monitoring_module_points_at_the_hook(self):
        txt = (REPO / "modules" / "core" / "ci-monitoring.md").read_text()
        self.assertIn("subagent-stop-check-bg-work.sh", txt)


if __name__ == "__main__":
    unittest.main()
