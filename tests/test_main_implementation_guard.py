"""MAIN-session implementation guard (airuleset #32, generalized by #54).

The user runs 3 full Max subscriptions and still hits limits. Live case 1
(#32, 2026-07-24): a presenter session with the main model set to Fable
IMPLEMENTED a whole issue itself (no subagents) — a Fable main re-reads the
full conversation every turn, so every edit/test/build of an implementation
loop burns Fable prices. Live case 2 (#54, 2026-07-25): david@subdev's Opus
MAIN session, with an ARMED /goal, did 354 direct Edits + 56 Writes
alongside 229 Agent dispatches — the model wasn't Fable, but the failure is
the same: main implements instead of dispatching a worker. The ADVISOR-shape
rule exists in prose (model-awareness.md) and BOTH sessions violated it
anyway — the exact failure class a HOOK enforces:

- `block-main-implementation.sh` (PreToolUse Edit|Write, renamed from
  `block-fable-main-implementation.sh`): a MAIN session (no agent_id)
  writing MORE than AIRULESET_FABLE_EDIT_MAX (~800 chars) in one Edit/Write
  is blocked with the delegation instruction whenever EITHER (a) its CURRENT
  model is claude-fable-*, OR (b) the session's TRANSCRIPT shows an ARMED
  /goal (the latest `<local-command-stdout>Goal set:` / `Goal cleared:`
  marker is a "set" with no later "cleared"). Small surgical edits pass
  (oversight is legitimate); subagents pass (execution belongs there); a
  plain non-Fable, non-goal-armed main passes (unchanged existing
  behavior); unknown model / no goal markers fails open; deliberate bypass
  = touch /tmp/airuleset-main-exec-ok-<session_id> (logged), with the
  original /tmp/airuleset-fable-exec-ok-<session_id> still honored for
  backward compatibility.
- `fable-advisor` skill: the one-command ADVISOR path for a cheap master —
  fable-gate → tight digest → ONE Agent dispatch model:fable effort:xhigh →
  decision back; execution goes to a Sonnet worker.

Goal-armed detection is INDEPENDENT of the Fable-model detection (#38's
stale-model-after-/model-switch caveat applies ONLY to the model path, never
to the goal-armed path — the goal-armed tests here never touch `MODEL`).
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
HOOK = REPO / "hooks" / "block-main-implementation.sh"

BIG = "x = 1\n" * 300          # way over any threshold
SMALL = "x = 1\n"
MID_1200 = "n" * 1200          # #178: over AIRULESET_FABLE_EDIT_MAX (800),
                                # but a bookkeeping path (below) is exempt
                                # from that threshold regardless of length

# #128: arming the bypass now means WRITING A REASON into the marker, not
# `touch`ing an empty file — so every test that arms one arms it the way a
# session must. The one-shot semantics (#80) are unchanged; only the shape
# of a valid marker is.
BYPASS_REASON = "authoring the policy text itself — the content IS the judgment"

# #492: the block/bypass audit logs are per-USER now (`-<uid>`). Tests read
# the SAME per-uid path the hook writes — the test process IS the writer's
# user, so os.getuid() resolves to the identical suffix.
BLOCK_LOG_PATH = Path("/tmp/airuleset-main-exec-block-%d.log" % os.getuid())
BYPASS_LOG_PATH = Path("/tmp/airuleset-main-exec-bypass-%d.log" % os.getuid())


def _isolated_exec_logs(testcase):
    """#732: the hook's block/bypass audit logs are the ONLY cross-SESSION
    shared artifacts it writes — everything else (bypass markers, run
    counter, presence marker) is SID-keyed and thus unique per session. On
    the push-gate box the suite runs on a LIVE dev1 where concurrent worker
    lanes + the supervisor session (all the SAME UID) genuinely arm/consume
    bypass markers, appending to the SAME per-uid log MID-SUITE — so a test
    that counts WHOLE-FILE log lines miscounts (the `2 != 1` false push-block,
    incident 2026-08-26). This hands the test its OWN throwaway log dir;
    passing it to the hook as AIRULESET_MAIN_EXEC_LOG_DIR redirects BOTH logs
    there, so no concurrent real fleet session can touch the file this test
    reads. Returns `(dir, block_log, bypass_log)`; the temp dir outlives the
    subprocess (cleaned at testcase teardown, never inside the hook run)."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    base = Path(d.name)
    uid = os.getuid()
    return (base,
            base / ("airuleset-main-exec-block-%d.log" % uid),
            base / ("airuleset-main-exec-bypass-%d.log" % uid))


def _entry(role_type, content, **extra):
    d = {"type": role_type}
    d.update(extra)
    if role_type == "user":
        d["message"] = {"role": "user", "content": content}
    else:
        d["content"] = content
    return json.dumps(d)


def transcript(model="claude-fable-5"):
    lines = [
        json.dumps({"type": "user", "message": {"role": "user",
                                                "content": "do the thing"}}),
        json.dumps({"type": "assistant", "message": {
            "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "working"}]}}),
    ]
    return "\n".join(lines) + "\n"


def goal_set_line(text="do the whole backlog"):
    # exactly how Claude Code itself writes it: a plain "user" entry whose
    # string content is the <local-command-stdout> the /goal command prints.
    return _entry("user",
                  "<local-command-stdout>Goal set: %s</local-command-stdout>" % text)


def goal_cleared_line(text="do the whole backlog"):
    # observed shape: a "system"/"local_command" entry, content at top level.
    return _entry("system",
                  "<local-command-stdout>Goal cleared: %s</local-command-stdout>" % text,
                  subtype="local_command")


def nested_foreign_goal_line(word="set"):
    # a tool_result whose (array) content happens to QUOTE another session's
    # goal marker text — must NEVER be mistaken for THIS session's own state.
    return json.dumps({"type": "user", "message": {"content": [
        {"tool_use_id": "t1", "type": "tool_result",
         "content": "<local-command-stdout>Goal %s: some other repo's loop"
                    "</local-command-stdout>" % word}]}})


def goal_armed_transcript(model="claude-opus-4-8"):
    lines = [
        json.dumps({"type": "user", "message": {"role": "user",
                                                "content": "/autopilot"}}),
        goal_set_line(),
        json.dumps({"type": "assistant", "message": {
            "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "working the backlog"}]}}),
    ]
    return "\n".join(lines) + "\n"


class MainImplementationGuard(unittest.TestCase):
    def _run(self, tool="Edit", content=BIG, model="claude-fable-5",
             agent_id=None, transcript_text=None, sid=None, bypass=None,
             command=None, bypass_reason=None, presence_age=None,
             extra_env=None, file_path=None):
        sid = sid or ("t-mg-" + uuid.uuid4().hex[:8])
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(transcript_text
                                if transcript_text is not None
                                else transcript(model))
            if bypass:
                marker = ("/tmp/airuleset-main-exec-ok-%s" % sid if bypass == "new"
                          else "/tmp/airuleset-fable-exec-ok-%s" % sid)
                Path(marker).write_text(BYPASS_REASON if bypass_reason is None
                                        else bypass_reason)
                self.addCleanup(lambda: Path(marker).unlink(missing_ok=True))
            # #128: the presence marker clear-question-dedup.sh stamps on
            # UserPromptSubmit. `presence_age` = seconds since the user last
            # typed a REAL prompt; None = no marker at all (unprovable).
            if presence_age is not None:
                active = Path("/tmp/claude-user-active-%s" % sid)
                active.write_text("")
                stamp = int(__import__("time").time()) - int(presence_age)
                os.utime(active, (stamp, stamp))
                self.addCleanup(lambda: active.unlink(missing_ok=True))
            if tool == "Bash":
                ti = {"command": command if command is not None else content}
            elif tool == "Edit":
                ti = {"file_path": file_path or "/x/app.py", "old_string": "a",
                      "new_string": content}
            else:
                ti = {"file_path": file_path or "/x/app.py", "content": content}
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": tool, "tool_input": ti,
                       "transcript_path": tp}
            if agent_id:
                payload["agent_id"] = agent_id
            env = dict(os.environ)
            if extra_env:
                env.update(extra_env)
            return subprocess.run(["bash", str(HOOK)],
                                  input=json.dumps(payload), env=env,
                                  capture_output=True, text=True)

    # ---- existing Fable-model behavior (#32) — unchanged ----

    def test_fable_main_big_edit_blocked(self):
        out = self._run()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("FABLE", out.stderr)
        self.assertIn("worker", out.stderr)

    def test_fable_main_big_write_blocked(self):
        out = self._run(tool="Write")
        self.assertEqual(out.returncode, 2)

    def test_small_surgical_edit_passes(self):
        out = self._run(content=SMALL)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_subagent_context_passes(self):
        # execution BELONGS in workers — a subagent edit is never blocked,
        # even with an armed goal in its (irrelevant) transcript
        out = self._run(agent_id="aWORKER1",
                        transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_non_fable_main_passes(self):
        out = self._run(model="claude-opus-4-8")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_model_switch_mid_session_uses_latest(self):
        # /model can change mid-session — the LAST assistant entry decides
        tx = transcript("claude-fable-5") + transcript("claude-opus-4-8")
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_unknown_model_fails_open(self):
        out = self._run(transcript_text=json.dumps(
            {"type": "user", "message": {"role": "user", "content": "x"}})
            + "\n")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_bypass_file_allows_and_is_deliberate(self):
        out = self._run(bypass="legacy")
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- #38: the transcript LAGS the live turn -> stale-model false block ----

    def test_model_switch_marker_after_last_assistant_entry_fails_open(self):
        # LIVE incident (2026-07-25, session 2d02a127, transcript lines
        # 38913-38935): the user switched /model Fable -> Opus 5, the very next
        # Write was BLOCKED as "runs on FABLE". Replaying the real transcript
        # prefix showed why: at PreToolUse the CURRENT turn's assistant entry
        # is not flushed yet, so the newest `"model"` in the file is still the
        # PREVIOUS turn's Fable one. CC's own `/model` stdout marker is the
        # only in-file evidence of the switch — it must win over the older
        # assistant entry.
        tx = (transcript("claude-fable-5")
              + _entry("user", "<local-command-stdout>Set model to \x1b[1m"
                               "Opus 5 (1M context) (default)\x1b[22m and saved "
                               "as your default for new sessions"
                               "</local-command-stdout>") + "\n"
              + json.dumps({"type": "pr-link"}) + "\n"
              + json.dumps({"type": "bridge-session"}) + "\n")
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 0,
                         "stale Fable read after a /model switch: "
                         + out.stdout + out.stderr)

    def test_model_switch_marker_TO_fable_still_blocks(self):
        # The same marker in the other direction must keep the guard armed —
        # widening the detection must not open a hole.
        tx = (transcript("claude-opus-5")
              + _entry("user", "<local-command-stdout>Set model to \x1b[1m"
                               "Fable 5\x1b[22m and saved as your default for "
                               "new sessions</local-command-stdout>") + "\n")
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("FABLE", out.stderr)

    def test_quoted_foreign_model_in_tool_result_is_not_the_session_model(self):
        # The raw `grep -oE '"model"…'` matched ANY occurrence in the tail —
        # including a tool_result that quotes ANOTHER transcript. The incident
        # session was doing exactly that (measuring per-message usage+model
        # across transcripts). Same structural fix the goal-armed detector
        # already uses: top-level entries only.
        tx = (transcript("claude-opus-5")
              + json.dumps({"type": "user", "message": {"content": [
                  {"tool_use_id": "t1", "type": "tool_result",
                   "content": '{"type":"assistant","message":{"model":'
                              '"claude-fable-5","role":"assistant"}}'}]}})
              + "\n")
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 0,
                         "a QUOTED foreign transcript decided the model: "
                         + out.stdout + out.stderr)

    def test_plain_fable_main_still_blocked_after_the_38_fix(self):
        # regression guard: no switch marker, no quoting — the ordinary
        # Fable-main case #32 exists for must still block.
        out = self._run(transcript_text=transcript("claude-fable-5"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_threshold_env_tunable(self):
        env = dict(os.environ, AIRULESET_FABLE_EDIT_MAX="100000")
        sid = "t-mg-env-" + uuid.uuid4().hex[:6]
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(transcript())
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": "Write",
                       "tool_input": {"file_path": "/x/a.py", "content": BIG},
                       "transcript_path": tp}
            out = subprocess.run(["bash", str(HOOK)],
                                 input=json.dumps(payload), env=env,
                                 capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- new: goal-armed generalization (#54) ----

    def test_goal_armed_opus_main_big_edit_blocked(self):
        # the exact david@subdev shape: NON-Fable model, ARMED /goal
        out = self._run(transcript_text=goal_armed_transcript("claude-opus-4-8"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("ARMED", out.stderr)
        self.assertIn("worker", out.stderr)

    def test_goal_armed_sonnet_main_big_write_blocked(self):
        out = self._run(tool="Write",
                        transcript_text=goal_armed_transcript("claude-sonnet-5"))
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_goal_armed_main_new_bypass_marker_allows(self):
        sid = "t-mg-bypass-new-" + uuid.uuid4().hex[:6]
        out = self._run(sid=sid, bypass="new",
                        transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_goal_armed_main_legacy_bypass_marker_still_allows(self):
        # backward compat: the ORIGINAL fable-only marker name still bypasses
        # the GENERALIZED (goal-armed) block path too.
        sid = "t-mg-bypass-legacy-" + uuid.uuid4().hex[:6]
        out = self._run(sid=sid, bypass="legacy",
                        transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_not_goal_armed_ordinary_main_passes_unchanged(self):
        # THIS MUST NOT CHANGE: an ordinary interactive main session with no
        # /goal ever armed, on a non-Fable model, still passes.
        tx = "\n".join([
            json.dumps({"type": "user", "message": {"role": "user",
                                                    "content": "fix the bug"}}),
            json.dumps({"type": "assistant", "message": {
                "role": "assistant", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "on it"}]}}),
        ]) + "\n"
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_goal_cleared_after_set_is_not_armed(self):
        tx = "\n".join([goal_set_line(), goal_cleared_line()]) + "\n"
        out = self._run(transcript_text=tx, model="claude-opus-4-8")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_goal_set_again_after_cleared_is_re_armed(self):
        tx = "\n".join([goal_set_line("first backlog"), goal_cleared_line("first backlog"),
                        goal_set_line("second backlog")]) + "\n"
        out = self._run(transcript_text=tx, model="claude-opus-4-8")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_small_edit_passes_even_when_goal_armed(self):
        out = self._run(content=SMALL, transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_nested_foreign_transcript_goal_text_is_not_armed(self):
        # a session that greps/pastes ANOTHER session's transcript containing
        # goal marker text (inside a tool_result) must NOT be mistaken for
        # its OWN goal-armed state.
        tx = "\n".join([
            json.dumps({"type": "user", "message": {"role": "user",
                                                    "content": "grep other repos"}}),
            nested_foreign_goal_line("set"),
            json.dumps({"type": "assistant", "message": {
                "role": "assistant", "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "found it"}]}}),
        ]) + "\n"
        out = self._run(transcript_text=tx)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_goal_armed_and_fable_both_hold_still_blocked(self):
        out = self._run(transcript_text=goal_armed_transcript("claude-fable-5"),
                        model="claude-fable-5")
        self.assertEqual(out.returncode, 2, out.stderr)


class MainBashGuard(unittest.TestCase):
    """#66: `Bash` is now ALSO guarded in a goal-armed/Fable MAIN — measured
    2026-07-26 (loop_health.py): gatekeeper's main agent ran 1222 Bash calls
    vs only 97 subagent dispatches in one hour, each call re-sending the
    whole (212K-avg) context. An ALLOW-LIST of short, constant-output
    gh/git/airuleset/tmux/systemctl coordination commands always passes; a
    BLOCK-LIST of bulk read/search/build/test/log-scrape commands is
    rejected ONLY while goal-armed/Fable; anything matching NEITHER list is
    ambiguous and is ALLOWED (conservative — never break a legitimate gh/git
    call the loop depends on). A subagent (agent_id set) is NEVER blocked,
    regardless of the command."""

    # ---- ALLOW-LIST: must pass even while goal-armed ----

    def _armed(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=goal_armed_transcript(
                               kw.pop("model", "claude-opus-4-8")),
                           **kw)

    def _plain(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=transcript(
                               kw.pop("model", "claude-opus-4-8")),
                           **kw)

    def test_gh_pr_view_allowed_while_armed(self):
        out = self._armed("gh pr view 42")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_gh_issue_create_allowed_while_armed(self):
        out = self._armed('gh issue create -t "T" -F body.md -l bug')
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_gh_run_view_allowed_while_armed(self):
        out = self._armed("gh run view 12345")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_git_status_allowed_while_armed(self):
        out = self._armed("git status")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_git_log_oneline_bounded_allowed_while_armed(self):
        out = self._armed("git log --oneline -5")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_git_rev_parse_allowed_while_armed(self):
        out = self._armed("git rev-parse HEAD")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_git_fetch_allowed_while_armed(self):
        out = self._armed("git fetch origin")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_airuleset_py_allowed_while_armed(self):
        out = self._armed("python3 ~/devel/airuleset/airuleset.py notify --run-card")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_tmux_allowed_while_armed(self):
        out = self._armed("tmux send-keys -t main 'hello' Enter")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_systemctl_user_allowed_while_armed(self):
        out = self._armed("systemctl --user status api-watchdog.timer")
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- BLOCK-LIST: rejected ONLY while goal-armed ----

    def test_repo_grep_sweep_blocked_while_armed(self):
        out = self._armed("grep -rn 'TODO' .")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("subagent", out.stderr.lower())

    def test_rg_sweep_blocked_while_armed(self):
        out = self._armed("rg -n 'pattern' src/")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_find_sweep_blocked_while_armed(self):
        out = self._armed("find . -name '*.py'")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_cat_source_file_blocked_while_armed(self):
        out = self._armed("cat airuleset.py")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_head_source_file_blocked_while_armed(self):
        out = self._armed("head -100 airuleset.py")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_pytest_run_blocked_while_armed(self):
        out = self._armed("python3 -m pytest tests/ -q")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_cargo_test_blocked_while_armed(self):
        out = self._armed("cargo test")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_npm_test_blocked_while_armed(self):
        out = self._armed("npm test")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_journalctl_scrape_blocked_while_armed(self):
        out = self._armed("journalctl -u api-watchdog -n 500")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_docker_logs_scrape_blocked_while_armed(self):
        out = self._armed("docker logs some-container")
        self.assertEqual(out.returncode, 2, out.stderr)

    # ---- not armed / not Fable: block-list commands still ALLOWED ----

    def test_grep_sweep_allowed_when_not_armed(self):
        out = self._plain("grep -rn 'TODO' .")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_pytest_allowed_when_not_armed(self):
        out = self._plain("python3 -m pytest tests/ -q")
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- ambiguous (neither list) -> conservative ALLOW even while armed ----

    def test_ambiguous_command_allowed_while_armed(self):
        out = self._armed("git diff --stat")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_gh_pr_diff_ambiguous_allowed_while_armed(self):
        out = self._armed("gh pr diff 42")
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- subagent is NEVER blocked, even for a sweep, even goal-armed ----

    def test_subagent_bash_sweep_never_blocked(self):
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="grep -rn 'TODO' .",
                          agent_id="aWORKER2",
                          transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- bypass marker works for Bash too ----

    def test_bypass_marker_allows_blocked_bash_command(self):
        sid = "t-mg-bash-bypass-" + uuid.uuid4().hex[:6]
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="grep -rn 'TODO' .",
                          sid=sid, bypass="new",
                          transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- Fable-main (no goal armed) also blocks the same way ----

    def test_fable_main_bash_sweep_blocked(self):
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="grep -rn 'TODO' .",
                          model="claude-fable-5")
        self.assertEqual(out.returncode, 2, out.stderr)


class ClassifierHoles73(unittest.TestCase):
    """#73: three shapes the classifier let slip through as 'ambiguous ->
    allow' because their FIRST token was neither allow- nor block-listed:
    a for/while loop body, a `timeout N` / `nice` prefix wrapper, and a
    `bash -c '...'` / `sh -c '...'` sub-shell. The loop body / wrapped /
    quoted command must classify EXACTLY like a standalone command — never
    block a whole loop just for being a loop (the CI-poll shape from
    ci-monitoring.md is the non-negotiable ALLOW case)."""

    def _armed(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=goal_armed_transcript(
                               kw.pop("model", "claude-opus-4-8")),
                           **kw)

    def _plain(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=transcript(
                               kw.pop("model", "claude-opus-4-8")),
                           **kw)

    # ---- non-negotiable: the recommended CI-poll loop must NEVER block ----

    def test_ci_poll_loop_allowed_while_armed(self):
        out = self._armed(
            "for i in $(seq 1 18); do gh run view 12345 "
            "--json status,conclusion; sleep 30; done")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- for-loop body classifies like a standalone command ----

    def test_for_loop_body_bulk_read_blocked_while_armed(self):
        out = self._armed("for f in a b; do cat $f; done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_for_loop_body_bulk_read_allowed_when_not_armed(self):
        out = self._plain("for f in a b; do cat $f; done")
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- timeout/nice prefix wrappers don't hide a blocked command ----

    def test_timeout_prefixed_bulk_read_blocked_while_armed(self):
        out = self._armed("timeout 60 grep -rn foo .")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_timeout_prefixed_allowed_command_still_allowed_while_armed(self):
        out = self._armed("timeout 30 gh pr view 42")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_nice_prefixed_bulk_read_blocked_while_armed(self):
        out = self._armed("nice -n 10 grep -rn foo .")
        self.assertEqual(out.returncode, 2, out.stderr)

    # ---- bash -c / sh -c wraps the real command; classify the script ----

    def test_bash_dash_c_bulk_read_blocked_while_armed(self):
        out = self._armed("bash -c 'grep -rn foo .'")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_sh_dash_c_bulk_read_blocked_while_armed(self):
        out = self._armed("sh -c 'grep -rn foo .'")
        self.assertEqual(out.returncode, 2, out.stderr)

    def test_bash_dash_c_allowed_command_still_allowed_while_armed(self):
        out = self._armed("bash -c 'gh pr view 42'")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_bash_dash_c_bulk_read_allowed_when_not_armed(self):
        out = self._plain("bash -c 'grep -rn foo .'")
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- subagent still never blocked for the new shapes ----

    def test_subagent_for_loop_never_blocked(self):
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="for f in a b; do cat $f; done",
                          agent_id="aWORKER3",
                          transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_subagent_bash_dash_c_never_blocked(self):
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="bash -c 'grep -rn foo .'",
                          agent_id="aWORKER4",
                          transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)


class CoordinatorOutputWrites80(unittest.TestCase):
    """#80 root cause (measured on gk, 2026-07-26): the #66 classifier saw the
    head token `cat` in `cat > body.md <<'EOF' ... EOF` and called it a BULK
    READ — but that is the recipe `gh-cli-recipes.md` MANDATES for every
    issue/PR body (`-F body.md`, never an inline `--body`). gk's main ran it
    58× in a day, got falsely blocked, and armed the permanent bypass marker
    at 01:24 — after which the hook was dead for 17 hours (332 bypass log
    lines, 304 of them Bash). So the false positive is what disabled #66
    entirely.

    Two fixes, both regression-guarded here:
      1. heredoc BODIES are stripped before classification (the same
         `strip_heredocs()` shape `block-gh-invalid-json-flag.sh` already
         uses — one parser shape in this repo, never a second invented one);
         a body line that happens to READ like a bulk command is payload
         text, not a command.
      2. a segment whose STDOUT is redirected to a file (`>`, `>>`, `1>`)
         returns NOTHING to the model, so it is not the context cost this
         hook guards — it is a WRITE, and it passes. `2>/dev/null` is a
         STDERR redirect and must NOT exempt anything.
    A genuine bulk read whose output DOES come back (`cat file`,
    `sed -n '1,200p' file`, `grep -rn x .`) stays blocked."""

    def _armed(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=goal_armed_transcript(), **kw)

    # ---- the mandated gh body recipe must pass ----

    def test_heredoc_body_write_then_gh_comment_allowed_while_armed(self):
        out = self._armed(
            "cat > /tmp/body.md <<'EOF'\n"
            "Unblocks the release: the shadow lane was red.\n"
            "EOF\n"
            "gh issue comment 2180 -F /tmp/body.md")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_heredoc_body_containing_bulk_command_text_is_payload(self):
        # the BODY documents a command; it is text, never a command to classify
        out = self._armed(
            "cat > /tmp/finding.md <<'EOF'\n"
            "Reproduce with: grep -rn 'warning' addons/ | head -20\n"
            "Then run pytest tests/ to confirm.\n"
            "EOF\n"
            "gh issue comment 42 -F /tmp/finding.md")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_append_heredoc_to_playbook_allowed_while_armed(self):
        out = self._armed(
            "cat >> /tmp/wt/SKILL.md <<'EOF'\n## Gotcha\ntext\nEOF")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_stdout_redirect_to_file_is_not_a_context_cost(self):
        out = self._armed("grep -rn 'TODO' . > /tmp/todos.txt")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_stdout_redirect_without_space_is_recognised(self):
        out = self._armed("sed -n '1,900p' models.py >/tmp/slice.txt")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- genuine bulk reads must STILL block (no hole opened) ----

    def test_plain_cat_of_a_source_file_still_blocked(self):
        out = self._armed("cat addons/models/sale_order.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_sed_slice_read_still_blocked(self):
        out = self._armed("sed -n '6996,7130p' models19.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_stderr_redirect_does_not_exempt_a_bulk_read(self):
        out = self._armed("grep -rn 'TODO' . 2>/dev/null")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_bulk_read_after_a_heredoc_write_still_blocked(self):
        # the heredoc write passes; the SEPARATE read segment still doesn't.
        # #178: was `/etc/passwd`, a REAL small existing file that the new
        # size-aware `cat` exemption correctly now allows — replaced with a
        # guaranteed-nonexistent absolute path so this keeps testing what
        # it was meant to (an unbounded-looking read that ISN'T a small
        # explicit existing file stays blocked).
        out = self._armed(
            "cat > /tmp/b.md <<'EOF'\nbody\nEOF\n"
            "cat /etc/airuleset-does-not-exist-178.conf")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class PipeReducers80(unittest.TestCase):
    """#80, found by smoking the hook against gk's REAL main-agent commands:
    the classifier splits on `|` and then judges each stage as if it were a
    standalone command, so the single most common coordination idiom in the
    whole transcript — piping an allow-listed command into an output REDUCER
    (`gh pr merge 2193 2>&1 | tail -2`, `gh pr checks 2189 | awk -F'\\t'
    '{print $2}' | sort | uniq -c`) — was BLOCKED on its `tail`/`awk` stage.

    That is backwards: a downstream pipe stage reads STDIN, and a reducer
    makes the output the model sees SMALLER. Only the FIRST stage of a
    pipeline decides what is read at all, so only the first stage is
    classified. `cat file | grep x` is still blocked — its first stage is
    the bulk read."""

    def _armed(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=goal_armed_transcript(), **kw)

    def test_gh_merge_piped_into_tail_allowed(self):
        out = self._armed("gh pr merge 2193 --merge --delete-branch 2>&1 | tail -2")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_gh_checks_piped_into_awk_pipeline_allowed(self):
        out = self._armed(
            "gh pr checks 2189 2>/dev/null | awk -F'\\t' '{print $2}' "
            "| sort | uniq -c")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_git_log_piped_into_head_allowed(self):
        out = self._armed("git log --oneline -20 | head -5")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_ci_poll_loop_with_awk_reducer_allowed(self):
        # the exact CI-poll shape gk runs, reducer included
        out = self._armed(
            "for i in $(seq 1 17); do\n"
            "  s=$(gh pr checks 2189 2>/dev/null | awk -F'\\t' '{print $2}' "
            "| sort | uniq -c)\n  echo \"[$i] $s\"\n  sleep 60\ndone")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_bulk_read_as_the_first_stage_still_blocks(self):
        out = self._armed("cat models19.py | grep -n warning | head -20")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_grep_sweep_piped_into_head_still_blocks(self):
        out = self._armed("grep -rn 'TODO' . | head -20")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_a_second_statement_is_still_its_own_first_stage(self):
        # `;` and `&&` start a NEW pipeline — each one's first stage counts.
        # #178: was `/etc/passwd` — see the identical note in
        # CoordinatorOutputWrites80.test_bulk_read_after_a_heredoc_write_still_blocked.
        out = self._armed(
            "gh pr view 42 | tail -3; "
            "cat /etc/airuleset-does-not-exist-178.conf")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_journalctl_scrape_still_blocks_with_a_reducer(self):
        out = self._armed("journalctl --user -u api-watchdog.service | tail -50")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class BoundedPeeks80(unittest.TestCase):
    """#80, found by replaying all 687 of gk's real main-agent Bash commands
    through the hook: after the heredoc and pipe-stage fixes the remaining
    false positives were all the same shape — a small BOUNDED peek at a file
    the command itself just produced (`... > /tmp/mt.out; head -5 /tmp/mt.out`,
    `cat >> SKILL.md <<'EOF' ... EOF; tail -3 SKILL.md`).

    `head`/`tail` are inherently bounded (10 lines by default) — what makes
    a read expensive is the SIZE that comes back, so the bound is what
    should be judged, not the head token. A peek up to
    AIRULESET_PEEK_MAX_LINES (default 50) passes; anything larger, an
    unbounded `-n +N` tail, or a byte-count dump is still a bulk read, and
    `cat file` / `sed -n 'A,Bp'` / `grep` are untouched."""

    def _armed(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=goal_armed_transcript(), **kw)

    def test_tail_verification_peek_allowed(self):
        out = self._armed("tail -3 /tmp/wt-pb/.claude/skills/x/SKILL.md")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_head_small_peek_allowed(self):
        out = self._armed("head -5 /tmp/mt.out")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_bare_tail_is_bounded_by_default_and_allowed(self):
        out = self._armed("tail /tmp/deploy.log")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_dash_n_form_allowed(self):
        out = self._armed("tail -n 20 /tmp/out.txt")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_large_head_still_blocked(self):
        out = self._armed("head -100 airuleset.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_tail_from_line_n_to_end_is_unbounded_and_blocked(self):
        out = self._armed("tail -n +1 airuleset.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_byte_count_dump_still_blocked(self):
        out = self._armed("head -c 200000 big.log")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cat_and_a_wide_sed_slice_are_untouched_by_the_peek_rule(self):
        self.assertEqual(self._armed("cat airuleset.py").returncode, 2)
        self.assertEqual(self._armed("sed -n '1,400p' airuleset.py").returncode, 2)

    def test_peek_bound_is_env_tunable(self):
        helper = MainImplementationGuard()
        sid = "t-mg-peek-" + uuid.uuid4().hex[:6]
        env = dict(os.environ, AIRULESET_PEEK_MAX_LINES="2")
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "s.jsonl")
            Path(tp).write_text(goal_armed_transcript())
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": "Bash",
                       "tool_input": {"command": "tail -30 /tmp/x.log"},
                       "transcript_path": tp}
            out = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                                 env=env, capture_output=True, text=True)
        del helper
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class CountsAndBoundedSlices80(unittest.TestCase):
    """#80, third and last pass over the replayed gk corpus. Two more shapes
    were blocked whose output is provably tiny — the same "judge the SIZE
    that comes back, not the head token" principle as the bounded peek:

      • `grep -c pattern file` / `grep -q` return ONE number / nothing. They
        are assertions ("did my write land"), not reads. `grep -rn ...` is
        untouched — that one really does dump matches.
      • `sed -n '250,260p' file` is an 11-line slice — bounded exactly like
        `head -11`. A slice wider than AIRULESET_PEEK_MAX_LINES (default 50)
        stays blocked, as does a `sed -n '1,$p'`-style unbounded form and
        every non `-n` sed usage."""

    def _armed(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=goal_armed_transcript(), **kw)

    def test_grep_count_is_an_assertion_not_a_read(self):
        out = self._armed("grep -c 'sk_SK' /tmp/wt/SKILL.md")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_grep_quiet_allowed(self):
        out = self._armed("grep -q 'needle' /tmp/wt/CHANGELOG.md")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_grep_dump_still_blocked(self):
        out = self._armed("grep -rn 'warning' addons/")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_narrow_sed_slice_allowed(self):
        out = self._armed("sed -n '250,260p' /tmp/wt/spec.ts")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_wide_sed_slice_still_blocked(self):
        out = self._armed("sed -n '1765,1820p' models/sale_order.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_sed_range_by_pattern_is_unbounded_and_blocked(self):
        out = self._armed("sed -n '/^def onchange/,/^    def [a-z]/p' models19.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_sed_in_place_edit_is_not_a_read_but_stays_conservative(self):
        # not a read at all — must not be blocked as one
        out = self._armed("sed -i 's/a/b/' /tmp/x.txt")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class LineContinuationInsideSubstitution_88(unittest.TestCase):
    """#88: a real gk health-check command was blocked by mistake. A
    multi-line curl+grep pipeline written with a bash LINE CONTINUATION
    (`|\\` at end of line, the pipe's reducer stage on the NEXT physical
    line — normal style for a readable one-liner):

        VERSION=$(curl -s https://erp.montalu.sk/web/health |\\
          grep -oP '(?<=.version.: .)[0-9.]+')

    STATEMENTS_RE splits on a bare '\\n' with no notion of bash's
    backslash-newline continuation, so the continued line becomes its OWN
    top-level statement — and since it has no further '|', its head token
    IS 'grep' (no -c/-q), which classifies as a genuine bulk read even
    though it is really just the tail of the previous pipe, feeding a
    single bounded version string into a command substitution. Reproduced
    directly (1/687 in the #80 corpus, filed separately since #80's own
    fixes did not touch it): a bare top-level continued pipe
    (`curl -s https://x |\\` + newline + `  grep -oP 'pattern'`, no
    substitution at all) hits the exact same misclassification — the root
    cause is line-continuation handling, not `$( … )` specifically."""

    def _armed(self, command, **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=goal_armed_transcript(), **kw)

    def test_health_check_version_extraction_inside_substitution_allowed(self):
        cmd = ("VERSION=$(curl -s https://erp.montalu.sk/web/health |\\\n"
               "  grep -oP '(?<=.version.: .)[0-9.]+')")
        out = self._armed(cmd)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_bare_top_level_continued_pipe_allowed(self):
        cmd = "curl -s https://erp.montalu.sk/web/health |\\\n  grep -oP 'pattern'"
        out = self._armed(cmd)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_continuation_inside_single_quotes_is_left_alone(self):
        # a backslash-newline INSIDE single quotes is two literal characters
        # in bash, not a continuation — the joiner must be quote-aware and
        # never touch it. This uses a genuinely blocked bulk read (cat) so a
        # naive "always splice" implementation that also mangles the quoted
        # payload would still show SOME behavior change here to catch a
        # regression the other direction.
        cmd = "cat 'line one\\\nline two' file.txt"
        out = self._armed(cmd)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_ci_poll_regression_guard_still_allowed(self):
        # non-negotiable guard from #73/#80 — must never regress
        cmd = ("for i in $(seq 1 18); do gh run view 1 --json status,"
               "conclusion --jq '.status'; sleep 30; done")
        out = self._armed(cmd)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class OneShotBypass80(unittest.TestCase):
    """#80: the bypass marker was a PERMANENT, self-servable kill switch — a
    single `touch /tmp/airuleset-main-exec-ok-<sid>` disabled this hook for
    the rest of the session. gk's main did exactly that at 01:24 ("Hook's
    documented exception applies... Taking it (it's logged)") and then ran
    304 Bash + 20 Write calls through it unguarded. "Deliberate exception"
    means ONE deliberate action, so the hook now CONSUMES the marker: one
    marker = one exempted call, still logged. Nothing dead-ends (it can
    always be re-touched) but the cost of abuse grows linearly with abuse
    instead of being paid once."""

    def _marker(self, sid, legacy=False):
        name = "fable" if legacy else "main"
        return Path("/tmp/airuleset-%s-exec-ok-%s" % (name, sid))

    def _run(self, sid, command="grep -rn 'TODO' .", **kw):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command, sid=sid,
                           transcript_text=goal_armed_transcript(), **kw)

    def test_marker_is_consumed_after_one_use(self):
        sid = "t-mg-oneshot-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text(BYPASS_REASON)
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        first = self._run(sid)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(m.exists(), "marker must be consumed by the one use")

    def test_second_call_after_the_marker_was_used_is_blocked_again(self):
        sid = "t-mg-oneshot2-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text(BYPASS_REASON)
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self.assertEqual(self._run(sid).returncode, 0)
        second = self._run(sid)
        self.assertEqual(second.returncode, 2,
                         "a used marker must not keep the hook disabled")

    def test_legacy_marker_is_also_consumed(self):
        sid = "t-mg-oneshot3-" + uuid.uuid4().hex[:8]
        m = self._marker(sid, legacy=True)
        m.write_text(BYPASS_REASON)
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self.assertEqual(self._run(sid).returncode, 0)
        self.assertFalse(m.exists())

    def test_re_touching_the_marker_works_again(self):
        sid = "t-mg-oneshot4-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        m.write_text(BYPASS_REASON)
        self.assertEqual(self._run(sid).returncode, 0)
        self.assertEqual(self._run(sid).returncode, 2)
        m.write_text(BYPASS_REASON)
        self.assertEqual(self._run(sid).returncode, 0,
                         "the escape hatch must never dead-end")

    def test_bypass_is_still_logged(self):
        sid = "t-mg-oneshot5-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text(BYPASS_REASON)
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self._run(sid)
        log = BYPASS_LOG_PATH
        self.assertTrue(log.exists())
        self.assertTrue([ln for ln in log.read_text().splitlines() if sid in ln],
                        "a consumed bypass must still be logged")


class DispatchRatioNudge80(unittest.TestCase):
    """#80 direction 3 — the actual lever. Measured on gk 2026-07-26 (MAIN
    agent, `isSidechain=false`): Bash 687 : Agent 22 = 31:1, ten hours with
    ZERO dispatches, runs of consecutive main Bash calls between two
    dispatches of max 119 / median 30. The cost of a main turn does not
    depend on WHICH command runs — it depends on THAT another turn runs,
    because every turn re-sends the whole (256-363K) context. So the lever is
    the COUNT of main-agent Bash turns, not their class.

    Direction 1 from the ticket (">N gh calls per TURN = batch them") is
    REFUTED by the same measurement: 687 of 687 turns carried exactly ONE
    Bash call — the model never puts two in one turn, so a per-turn counter
    can never fire. Counting must be ACROSS turns, which is this: a
    per-session counter of main-agent Bash calls since the last DISPATCH.

    Non-negotiable acceptance constraint from the ticket ("ziadne
    zablokovanie, ktore by loop realne zastavilo"): the nudge itself RESETS
    the counter, so this can never become a wall — the worst case is one
    instructive block per N calls, never two in a row. A dispatch
    (PreToolUse Agent/Task/Workflow) resets it too; touching the bypass
    marker is never counted and never blocked (or the escape hatch would
    dead-end)."""

    def _run(self, sid, command="gh issue view 42", n=None, agent_id=None,
             tool="Bash", armed=True, extra_env=None):
        env = dict(os.environ)
        env["AIRULESET_MAIN_BASH_PER_DISPATCH"] = str(3 if n is None else n)
        if extra_env:
            env.update(extra_env)
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(goal_armed_transcript() if armed
                                else transcript("claude-opus-4-8"))
            ti = ({"command": command} if tool == "Bash"
                  else {"file_path": "/x/a.py", "content": SMALL})
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": tool, "tool_input": ti,
                       "transcript_path": tp}
            if agent_id:
                payload["agent_id"] = agent_id
            return subprocess.run(["bash", str(HOOK)],
                                  input=json.dumps(payload), env=env,
                                  capture_output=True, text=True)

    def _sid(self, tag):
        sid = "t-mg-%s-%s" % (tag, uuid.uuid4().hex[:8])
        self.addCleanup(lambda: Path(
            "/tmp/airuleset-main-bash-run-%s" % sid).unlink(missing_ok=True))
        return sid

    def test_allowed_calls_under_the_cap_pass(self):
        sid = self._sid("cap1")
        for _ in range(3):
            out = self._run(sid)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_call_over_the_cap_is_nudged(self):
        sid = self._sid("cap2")
        for _ in range(3):
            self._run(sid)
        out = self._run(sid)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("dispatch", out.stderr.lower())

    def test_the_nudge_resets_so_it_is_never_a_wall(self):
        sid = self._sid("cap3")
        for _ in range(4):
            self._run(sid)                      # 4th one nudges
        out = self._run(sid)
        self.assertEqual(out.returncode, 0,
                         "a nudge must never be followed by a second block")

    def test_a_dispatch_resets_the_counter(self):
        sid = self._sid("cap4")
        for _ in range(3):
            self._run(sid)
        reset = self._run(sid, tool="Agent")
        self.assertEqual(reset.returncode, 0, reset.stderr)
        out = self._run(sid)
        self.assertEqual(out.returncode, 0,
                         "dispatching is exactly the wanted response")

    def test_task_and_workflow_also_reset(self):
        for tool in ("Task", "Workflow"):
            sid = self._sid("cap5" + tool)
            for _ in range(3):
                self._run(sid)
            self._run(sid, tool=tool)
            out = self._run(sid)
            self.assertEqual(out.returncode, 0, "%s must reset too" % tool)

    def test_not_goal_armed_main_is_never_counted(self):
        sid = self._sid("cap6")
        for _ in range(8):
            out = self._run(sid, armed=False)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_subagent_calls_are_never_counted(self):
        sid = self._sid("cap7")
        for _ in range(8):
            out = self._run(sid, agent_id="aWORKER1")
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_touching_the_bypass_marker_is_never_blocked_by_the_cap(self):
        sid = self._sid("cap8")
        for _ in range(6):
            self._run(sid)
        out = self._run(sid,
                        command="touch /tmp/airuleset-main-exec-ok-%s" % sid)
        self.addCleanup(lambda: Path(
            "/tmp/airuleset-main-exec-ok-%s" % sid).unlink(missing_ok=True))
        self.assertEqual(out.returncode, 0,
                         "the escape hatch must never dead-end behind the cap")

    def test_zero_disables_the_cap(self):
        sid = self._sid("cap9")
        for _ in range(10):
            out = self._run(sid, n=0)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_nudge_is_logged(self):
        sid = self._sid("cap10")
        for _ in range(4):
            self._run(sid)
        log = BLOCK_LOG_PATH
        self.assertTrue(log.exists())
        mine = [ln for ln in log.read_text().splitlines() if sid in ln]
        self.assertTrue(mine, "the nudge must be in the block log")
        self.assertIn("per-dispatch", " ".join(mine))

    def test_a_blocked_bulk_read_still_blocks_before_the_cap(self):
        sid = self._sid("cap11")
        out = self._run(sid, command="grep -rn 'TODO' .")
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("BULK", out.stderr)


class BlockLogging73(unittest.TestCase):
    """#73: every BLOCK must be written to its own log (timestamp, session,
    first ~120 chars of the command, which rule matched) — today the hook
    logs only bypasses, so there is no way to answer 'did it fire, on what'
    after a deploy. Same style/location as the existing bypass log."""

    LOG_PATH = BLOCK_LOG_PATH

    def _lines_for(self, sid):
        if not self.LOG_PATH.exists():
            return []
        return [ln for ln in self.LOG_PATH.read_text().splitlines() if sid in ln]

    def test_bash_block_is_logged(self):
        sid = "t-mg-logbash-" + uuid.uuid4().hex[:8]
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="grep -rn 'TODO' .",
                          sid=sid, transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 2, out.stderr)
        lines = self._lines_for(sid)
        self.assertTrue(lines, "no block-log line for session %s" % sid)
        self.assertIn("Bash", lines[-1])
        self.assertIn("grep", lines[-1])
        self.assertIn(sid, lines[-1])

    def test_edit_block_is_logged(self):
        sid = "t-mg-logedit-" + uuid.uuid4().hex[:8]
        helper = MainImplementationGuard()
        out = helper._run(tool="Edit", sid=sid)
        self.assertEqual(out.returncode, 2, out.stderr)
        lines = self._lines_for(sid)
        self.assertTrue(lines, "no block-log line for session %s" % sid)
        self.assertIn("Edit", lines[-1])

    def test_goal_armed_write_block_is_logged_with_rule(self):
        sid = "t-mg-logwrite-" + uuid.uuid4().hex[:8]
        helper = MainImplementationGuard()
        out = helper._run(tool="Write", sid=sid,
                          transcript_text=goal_armed_transcript("claude-opus-4-8"))
        self.assertEqual(out.returncode, 2, out.stderr)
        lines = self._lines_for(sid)
        self.assertTrue(lines)
        self.assertIn("GOAL_ARMED", lines[-1])

    def test_bypassed_command_is_not_logged_as_block(self):
        sid = "t-mg-logbypass-" + uuid.uuid4().hex[:8]
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="grep -rn 'TODO' .",
                          sid=sid, bypass="new",
                          transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self._lines_for(sid),
                         "bypassed command must not appear in the BLOCK log")

    def test_allowed_command_is_not_logged_as_block(self):
        sid = "t-mg-logallow-" + uuid.uuid4().hex[:8]
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="gh pr view 42",
                          sid=sid, transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self._lines_for(sid),
                         "allowed command must not appear in the BLOCK log")


class AwayEngagement128(unittest.TestCase):
    """#128 — the engagement condition was wrong, decided on measurement.

    The guard used to engage on Fable-main OR an armed /goal. Both are
    PROXIES for "this session is running autonomously"; neither is the cost
    driver, because every main turn re-sends the whole context whether or
    not a goal is armed. Measured on dev1, 2026-07-28, top-level entries of
    all 11 real transcripts: the sessions the guard engages on ran 853 main
    tool calls against 87 dispatches, while the sessions it is INERT on ran
    1339 against 82 — and the single worst session (varos-eft5000: 650 main
    calls, ZERO dispatches, 52 Edit/Writes over the threshold) is one of the
    inert ones.

    But "engage always" is refused on the same measurement: replaying every
    real main Bash command of that day through this hook, it newly blocks
    348 calls, 164 of them within 5 minutes of a live human prompt — the
    attended-session regression the ticket's constraint forbids. Gating on
    "the user is AWAY" instead newly blocks 103 and touches ZERO attended
    calls, because the burn is concentrated in away time (190 of
    varos-eft5000's 497 guardable calls are >15 min after any human prompt).

    So: a THIRD condition, OR'd with the other two, never replacing them —
    the presence marker `/tmp/claude-user-active-<sid>` (stamped by
    clear-question-dedup.sh on UserPromptSubmit, which goal re-pokes and
    hook feedback do NOT fire) older than AIRULESET_MAIN_GUARD_AWAY_S
    (default 900 s). No marker = not provably away = allow (fail-open,
    unchanged behaviour)."""

    SWEEP = "grep -rn 'TODO' ."

    def _plain(self, **kw):
        # ordinary main: not Fable, no goal armed — inert before #128
        helper = MainImplementationGuard()
        kw.setdefault("transcript_text", transcript("claude-opus-5"))
        return helper._run(**kw)

    # ---- the hard constraint: an ATTENDED session is untouched ----

    def test_attended_plain_main_bulk_read_still_passes(self):
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_attended_plain_main_big_write_still_passes(self):
        out = self._plain(tool="Write", presence_age=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_no_presence_marker_fails_open(self):
        # unprovable presence must never start blocking a session that was
        # never guarded before
        out = self._plain(tool="Bash", command=self.SWEEP)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- the widening itself ----

    def test_away_plain_main_bulk_read_blocked(self):
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=1800)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("AWAY", out.stderr.upper())

    def test_away_plain_main_big_write_blocked(self):
        out = self._plain(tool="Write", presence_age=1800)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_away_main_still_allows_coordination_calls(self):
        # non-negotiable: the allow-list is what keeps a running loop alive
        for cmd in ("gh pr view 42", "git status --porcelain",
                    "gh issue list --json number", "tmux list-panes",
                    "git log --oneline -3"):
            out = self._plain(tool="Bash", command=cmd, presence_age=3600)
            self.assertEqual(out.returncode, 0,
                             "%s must pass while away: %s" % (cmd, out.stderr))

    def test_away_main_small_edit_still_passes(self):
        out = self._plain(tool="Edit", content=SMALL, presence_age=3600)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_away_subagent_never_blocked(self):
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=3600,
                          agent_id="aWORKER128")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_away_bypass_marker_still_works(self):
        sid = "t-mg-away-bp-" + uuid.uuid4().hex[:6]
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=3600,
                          sid=sid, bypass="new")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- threshold is tunable, and switchable off ----

    def test_away_threshold_env_tunable(self):
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=400,
                          extra_env={"AIRULESET_MAIN_GUARD_AWAY_S": "300"})
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_under_the_threshold_passes(self):
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=400)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_zero_disables_the_away_condition(self):
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=99999,
                          extra_env={"AIRULESET_MAIN_GUARD_AWAY_S": "0"})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_garbage_threshold_falls_back_to_the_default(self):
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=1800,
                          extra_env={"AIRULESET_MAIN_GUARD_AWAY_S": "abc"})
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    # ---- degenerate presence shapes (found by the pre-push smoke pass) ----

    def test_presence_path_that_is_a_directory_is_ignored(self):
        sid = "t-mg-away-dir-" + uuid.uuid4().hex[:8]
        d = Path("/tmp/claude-user-active-%s" % sid)
        d.mkdir()
        self.addCleanup(lambda: d.rmdir() if d.is_dir() else None)
        out = self._plain(tool="Bash", command=self.SWEEP, sid=sid)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_future_presence_mtime_is_not_away(self):
        # a clock skew must never be read as "the user left"
        out = self._plain(tool="Bash", command=self.SWEEP, presence_age=-9999)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- nothing is removed: the two existing conditions are unchanged ----

    def test_goal_armed_attended_session_still_blocks(self):
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command=self.SWEEP, presence_age=30,
                          transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_fable_attended_session_still_blocks(self):
        helper = MainImplementationGuard()
        out = helper._run(tool="Write", presence_age=30,
                          transcript_text=transcript("claude-fable-5"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    # ---- the block log must say WHICH rule engaged ----

    def test_away_block_is_tagged_in_the_block_log(self):
        sid = "t-mg-awaylog-" + uuid.uuid4().hex[:8]
        self._plain(tool="Bash", command=self.SWEEP, presence_age=1800, sid=sid)
        log = BLOCK_LOG_PATH
        lines = [ln for ln in log.read_text().splitlines() if sid in ln]
        self.assertTrue(lines, "away block was not logged")
        self.assertIn("USER_AWAY", lines[-1])


class BypassCarriesAReason128(unittest.TestCase):
    """#128 ask 3 — the bypass must be auditable, not merely one-shot.

    What the ticket read as abuse ("187 bypasses in one session") is a
    PRE-#80 artifact: 186 of those 193 log lines predate `7bcbafe`
    (2026-07-26T20:25:17), when one `touch` disabled the hook for the rest
    of the session. After it the same session armed the marker 4 times in
    2.5 days, each paired 1:1 with a consume. Nothing automated arms it
    either — watchdog job 22 only ever DELETES a stale marker — the six
    real arms were all emitted by the MODEL itself, inline, right after a
    block, because the block message advertises the escape.

    The real remaining gap is that the arm recorded NO REASON (it lived in
    the tool description and an `echo`, never in the log), so an audit
    cannot read why any exception was taken. So a marker must now CARRY its
    reason: `echo "<reason>" > /tmp/airuleset-main-exec-ok-<sid>`. An empty
    or throwaway marker is refused (and cleared, so it cannot linger), and
    the reason is logged on both the arm and the consume."""

    MIN = 8          # a reason shorter than this is not a reason

    def _marker(self, sid, legacy=False):
        return Path("/tmp/airuleset-%s-exec-ok-%s"
                    % ("fable" if legacy else "main", sid))

    def _run(self, sid, command="grep -rn 'TODO' .", logdir=None):
        # #732: `logdir` set -> redirect BOTH audit logs into a dir this test
        # owns (via AIRULESET_MAIN_EXEC_LOG_DIR), so a whole-file log count is
        # immune to concurrent fleet bypasses. None -> the real shared /tmp
        # path, unchanged.
        helper = MainImplementationGuard()
        extra_env = ({"AIRULESET_MAIN_EXEC_LOG_DIR": str(logdir)}
                     if logdir is not None else None)
        return helper._run(tool="Bash", command=command, sid=sid,
                           transcript_text=goal_armed_transcript(),
                           extra_env=extra_env)

    def _bypass_lines(self, sid, log=None):
        log = log if log is not None else BYPASS_LOG_PATH
        if not log.exists():
            return []
        return [ln for ln in log.read_text().splitlines() if sid in ln]

    def test_marker_with_a_reason_is_honored(self):
        sid = "t-mg-reason-ok-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text(BYPASS_REASON)
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        out = self._run(sid)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(m.exists(), "an honored marker is still consumed")

    def test_empty_marker_is_refused(self):
        sid = "t-mg-reason-empty-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        out = self._run(sid)
        self.assertEqual(out.returncode, 2,
                         "an empty marker must no longer disable the guard")

    def test_whitespace_only_marker_is_refused(self):
        sid = "t-mg-reason-ws-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("   \n\t\n")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self.assertEqual(self._run(sid).returncode, 2)

    def test_throwaway_reason_is_refused(self):
        sid = "t-mg-reason-short-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("ok")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self.assertEqual(self._run(sid).returncode, 2)

    def test_a_refused_marker_is_cleared_not_left_lying_around(self):
        sid = "t-mg-reason-clear-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self._run(sid)
        self.assertFalse(m.exists(),
                         "a refused marker must not sit in /tmp forever")

    def test_refusal_is_logged_so_the_audit_sees_it(self):
        sid = "t-mg-reason-log-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self._run(sid)
        lines = self._bypass_lines(sid)
        self.assertTrue(lines, "a refused marker must be logged")
        self.assertIn("refused", lines[-1])

    def test_the_reason_reaches_the_bypass_log(self):
        sid = "t-mg-reason-audit-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("terminal driver spike — needs the live rig in main")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self._run(sid)
        lines = self._bypass_lines(sid)
        self.assertTrue(lines)
        self.assertIn("terminal driver spike", lines[-1],
                      "the audit must be able to read WHY, from the log alone")

    def test_multiline_reason_does_not_break_the_log_format(self):
        sid = "t-mg-reason-multi-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("first line of the reason\nsecond line\nthird")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        before = (len(BYPASS_LOG_PATH.read_text().splitlines())
                  if BYPASS_LOG_PATH.exists() else 0)
        self._run(sid)
        after = BYPASS_LOG_PATH.read_text().splitlines()
        self.assertEqual(len(after) - before, 1,
                         "one bypass = exactly one log line")
        self.assertIn("first line of the reason", after[-1])

    def test_bypass_log_is_redirected_to_an_isolated_dir(self):
        # #732 regression — PROVES the pollution vector + its fix. The block/
        # bypass logs are the ONLY cross-SESSION-shared artifacts this hook
        # writes (everything else is SID-keyed). On the push-gate box,
        # concurrent lanes + the supervisor (same UID) append to the SAME
        # per-uid bypass log MID-SUITE, so the whole-file count in the test
        # above miscounted (the `2 != 1` false push-block, 2026-08-26). Proof
        # the count is hermetic: with the log redirected into a dir THIS test
        # owns, the multiline bypass lands there — flattened to EXACTLY ONE
        # physical line (the newline-split teeth are kept, not weakened) — and
        # NEVER in the real shared log a concurrent fleet session also writes,
        # so no concurrent write can change what this test counts.
        #
        # RED on unpatched code: the hook ignores AIRULESET_MAIN_EXEC_LOG_DIR,
        # so the bypass line goes to the real /tmp log, the isolated log stays
        # empty (0 != 1), and the real-log assertion below also trips.
        logdir, _block, bypass = _isolated_exec_logs(self)
        sid = "t-mg-reason-isolated-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("first line of the reason\nsecond line\nthird")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self._run(sid, logdir=logdir)
        mine = bypass.read_text().splitlines() if bypass.exists() else []
        self.assertEqual(len(mine), 1,
                         "the bypass must be logged to the ISOLATED dir, "
                         "flattened to exactly one physical line — redirected "
                         "off the shared per-uid log")
        self.assertIn("first line of the reason", mine[0])
        self.assertFalse(self._bypass_lines(sid),
                         "a redirected bypass must never touch the real shared "
                         "log a concurrent fleet session writes")

    def test_legacy_marker_needs_a_reason_too(self):
        sid = "t-mg-reason-legacy-" + uuid.uuid4().hex[:8]
        m = self._marker(sid, legacy=True)
        m.write_text("")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self.assertEqual(self._run(sid).returncode, 2)
        m.write_text(BYPASS_REASON)
        self.assertEqual(self._run(sid).returncode, 0,
                         "the legacy marker still works when it carries a reason")

    # ---- arming must never be blocked or counted, in EITHER form ----

    def test_arming_by_redirect_is_never_blocked(self):
        sid = "t-mg-arm-echo-" + uuid.uuid4().hex[:8]
        cmd = ('echo "reason: the content is the judgment itself" '
               '> /tmp/airuleset-main-exec-ok-%s' % sid)
        out = self._run(sid, command=cmd)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_arming_by_redirect_is_logged_as_an_arm(self):
        sid = "t-mg-arm-echolog-" + uuid.uuid4().hex[:8]
        cmd = ('printf %%s "reason: policy authoring" '
               '> /tmp/airuleset-main-exec-ok-%s' % sid)
        self._run(sid, command=cmd)
        lines = self._bypass_lines(sid)
        self.assertTrue(lines, "arming must be logged")
        self.assertIn("bypass-arm", lines[-1])

    def test_touch_arming_is_still_never_blocked(self):
        # `touch` no longer produces a USABLE marker, but the command itself
        # must still pass — the counter must never sit in front of the
        # escape hatch (#80's acceptance constraint).
        sid = "t-mg-arm-touch-" + uuid.uuid4().hex[:8]
        out = self._run(sid,
                        command="touch /tmp/airuleset-main-exec-ok-%s" % sid)
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- degenerate marker shapes (found by the pre-push smoke pass) ----

    def test_marker_with_control_bytes_does_not_pollute_stderr(self):
        # A NUL in the marker made bash warn ("ignored null byte in input")
        # on an ALLOWED call — a hook that writes to stderr while exiting 0
        # is invisible until something asserts on it (#124's lesson).
        sid = "t-mg-reason-bin-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_bytes(b"\x00\x01 deliberate exception with control bytes")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        out = self._run(sid)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr.strip(), "",
                         "an allowed call must write nothing to stderr")

    def test_non_ascii_reason_survives_to_the_log(self):
        # the reason is user-facing prose and is routinely Slovak — the
        # control-byte strip must spare every byte >= 0x80
        sid = "t-mg-reason-utf8-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("dôvod: písanie politiky — obsah je úsudok")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        self._run(sid)
        lines = self._bypass_lines(sid)
        self.assertTrue(lines)
        self.assertIn("písanie politiky", lines[-1])

    def test_the_block_message_states_the_reason_carrying_form(self):
        sid = "t-mg-arm-msg-" + uuid.uuid4().hex[:8]
        out = self._run(sid)
        self.assertEqual(out.returncode, 2)
        self.assertIn("airuleset-main-exec-ok-", out.stderr)
        self.assertRegex(out.stderr, r"echo\s+[\"'<]")


def _stub_jq_dir(testcase, fail_argpat):
    """(#180) A `jq` shim placed EARLIER on PATH than the real binary: it
    fails (exit 2, no output) whenever its OWN argv contains `fail_argpat`,
    and delegates everything else to the real `/usr/bin/jq` unchanged --
    fault-injecting exactly ONE named extraction inside the hook without
    disturbing any other jq call it makes. `fail_argpat` must be a
    substring that appears in ONLY the targeted call's argv (verified per
    call site against the shipped hook, not guessed)."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    import shutil
    real_jq = shutil.which("jq") or "/usr/bin/jq"
    stub = Path(d.name) / "jq"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$*\" in\n"
        "    *'%s'*) exit 2 ;;\n"
        "esac\n"
        "exec %s \"$@\"\n" % (fail_argpat, real_jq))
    stub.chmod(0o755)
    return d.name


class GoalArmedJqFails180(unittest.TestCase):
    """(#180) The whole armed-/goal block/allow decision used to hinge on
    ONE jq call (`jq -r '<filter>' "$TRANSCRIPT" | grep ... | tail -1`).
    Fault-injecting that call (a stub jq on PATH -- direct reproduction was
    0/8 attempts, confirming the mechanism needs the injection) proved the
    pipe's failure collapsed through `|| echo ""` into GOAL_ARMED=0, which
    reads as "not armed" and ALLOWS -- exactly backwards for a guard whose
    whole point is stopping unattended main-session implementation."""

    def test_jq_failure_reading_the_goal_marker_blocks_not_allows(self):
        sid = "t-mg-jqfail-goal-" + uuid.uuid4().hex[:8]
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            # a PLAIN, non-Fable, no-goal-marker transcript -- with a
            # WORKING jq this would be genuinely GOAL_ARMED=0 and ALLOWED.
            Path(tp).write_text(transcript("claude-opus-4-8"))
            # the goal-armed jq call is the ONLY one in the whole hook that
            # takes $TRANSCRIPT as a positional argument (every other jq
            # call reads its input piped via stdin) -- verified against
            # the shipped hook, so this fails ONLY that one extraction.
            stubdir = _stub_jq_dir(self, tp)
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": "Write",
                       "tool_input": {"file_path": "/x/a.py", "content": BIG},
                       "transcript_path": tp}
            env = dict(os.environ)
            env["PATH"] = stubdir + ":" + env["PATH"]
            out = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                                 env=env, capture_output=True, text=True)
        self.assertEqual(
            out.returncode, 2,
            "a jq failure reading the goal-armed marker must fail CLOSED "
            "(block), never silently allow: " + out.stdout + out.stderr)
        self.assertIn(
            "could not determine whether a /goal is armed", out.stderr,
            "the operator-facing message must say it could not decide, "
            "not silently claim a goal IS armed: " + out.stderr)
        # the machine-readable rule tag (the block log's own `rule=` field)
        # must be the DISTINCT GOAL_UNKNOWN, never GOAL_ARMED -- an audit
        # reading the log must never be told a goal was armed when the
        # truth is "the transcript could not be read".
        block_log = BLOCK_LOG_PATH
        self.assertTrue(block_log.exists(), "the block must be logged")
        lines = [ln for ln in block_log.read_text().splitlines() if sid in ln]
        self.assertTrue(lines, "expected a block log line for this session")
        self.assertIn("rule=GOAL_UNKNOWN", lines[-1],
                      "expected the distinct GOAL_UNKNOWN rule tag, got: %r"
                      % lines[-1])
        self.assertNotIn("rule=GOAL_ARMED", lines[-1],
                         "must never claim GOAL_ARMED when jq genuinely "
                         "failed, got: %r" % lines[-1])

    def test_a_working_jq_with_no_goal_marker_still_allows(self):
        # control: the SAME plain transcript, real jq -- must still ALLOW.
        # Proves the fix didn't flip to a blanket fail-closed that blocks
        # every ordinary main-session call regardless of jq's health.
        sid = "t-mg-jqok-goal-" + uuid.uuid4().hex[:8]
        helper = MainImplementationGuard()
        out = helper._run(tool="Write", content=BIG, model="claude-opus-4-8",
                          sid=sid)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_jq_failure_message_names_the_extraction_that_failed(self):
        sid = "t-mg-jqfail-msg-" + uuid.uuid4().hex[:8]
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(transcript("claude-opus-4-8"))
            stubdir = _stub_jq_dir(self, tp)
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": "Write",
                       "tool_input": {"file_path": "/x/a.py", "content": BIG},
                       "transcript_path": tp}
            env = dict(os.environ)
            env["PATH"] = stubdir + ":" + env["PATH"]
            out = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                                 env=env, capture_output=True, text=True)
        self.assertIn("could not determine", out.stdout + out.stderr,
                      "the operator-facing message must say WHY it "
                      "blocked, not just that it did: " + out.stderr)


class BypassReasonJqFails180(unittest.TestCase):
    """(#180) The bypass-reason extraction's own jq call sits at the end of
    one pipe collapsed by `|| echo ""` -- a genuine jq hiccup and a
    genuinely-too-short reason both used to produce the SAME "no reason,
    cleared" log line, misleading whoever reads the bypass audit log. A
    jq failure here already fails CLOSED overall (the bypass is refused,
    implementation stays blocked) -- the fix is making the LOG say which
    one actually happened."""

    def _marker(self, sid):
        return Path("/tmp/airuleset-main-exec-ok-%s" % sid)

    def _bypass_lines(self, sid):
        log = BYPASS_LOG_PATH
        if not log.exists():
            return []
        return [ln for ln in log.read_text().splitlines() if sid in ln]

    def test_jq_failure_extracting_a_real_reason_still_refuses_the_bypass(self):
        sid = "t-mg-jqfail-bypass-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        # a perfectly good, long reason -- with a WORKING jq this bypass
        # would be HONORED (exit 0).
        m.write_text("authoring the policy text itself — the content IS the judgment")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        # the bypass-reason jq call is the only one in the hook using the
        # exact filter '.[0:200]' (every other slice uses '.[0:120]') --
        # verified against the shipped hook.
        stubdir = _stub_jq_dir(self, ".[0:200]")
        env = dict(os.environ)
        env["PATH"] = stubdir + ":" + env["PATH"]
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="grep -rn 'TODO' .", sid=sid,
                          transcript_text=goal_armed_transcript(),
                          extra_env={"PATH": env["PATH"]})
        self.assertEqual(
            out.returncode, 2,
            "a jq failure must never accidentally HONOR a bypass it "
            "couldn't actually read: " + out.stdout + out.stderr)
        lines = self._bypass_lines(sid)
        self.assertTrue(lines, "the refusal must be logged")
        self.assertIn(
            "FAILED", lines[-1],
            "the log must distinguish a jq FAILURE from a genuinely "
            "too-short/absent reason, got: %r" % lines[-1])
        self.assertNotIn(
            "no reason", lines[-1],
            "a real reason existed -- the log must not claim there was "
            "none, got: %r" % lines[-1])

    def test_unreadable_marker_file_never_crashes_the_hook(self):
        # (adversarial review of this batch's own #180 diff) Splitting the
        # bypass-reason pipe (tr | tr -d | sed) out of its old shared
        # `|| echo ""` fallback removed the safety net from THIS half --
        # under `set -euo pipefail`, a read failure here (a TOCTOU-style
        # race, or -- reproduced deterministically here -- a permission
        # failure) used to crash the WHOLE hook via set -e: no block
        # message, no controlled exit code, before ever reaching the
        # jq-failure handling #180 added. The hook must always resolve to
        # a real decision (0 or 2), never die uncaught.
        sid = "t-mg-180-unreadable-" + uuid.uuid4().hex[:8]
        m = self._marker(sid)
        m.write_text("a perfectly good reason that becomes unreadable")
        m.chmod(0o000)

        def _cleanup():
            # the hook's own `rm -f $BYPASS_FILE` may already have removed
            # it (rm needs write on the PARENT dir, not the file's own
            # mode) -- either way is fine, just don't crash cleanup itself.
            if m.exists():
                m.chmod(0o644)
            m.unlink(missing_ok=True)
        self.addCleanup(_cleanup)
        helper = MainImplementationGuard()
        out = helper._run(tool="Bash", command="grep -rn 'TODO' .", sid=sid,
                          transcript_text=goal_armed_transcript())
        self.assertIn(
            out.returncode, (0, 2),
            "the hook must resolve to a real decision, never crash "
            "uncaught: rc=%d stdout=%r stderr=%r"
            % (out.returncode, out.stdout, out.stderr))


class CombinedAssertionFlags128(unittest.TestCase):
    """#128, found while replaying the 2026-07-28 corpus: #80 exempts
    `grep -c` / `grep -q` as ASSERTIONS ("returns one number / nothing"),
    but the check compares whole tokens, so the COMBINED short-flag form
    real commands actually use — `grep -cE '^(FAILED|ERROR)' /tmp/full10.log`
    — was not recognised and blocked. Faithfulness fix to an already-settled
    exemption, not a policy change: an unbounded `grep -rn` is untouched."""

    def _armed(self, command):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=goal_armed_transcript())

    def test_combined_count_flag_is_an_assertion(self):
        out = self._armed("grep -cE '^(FAILED|ERROR)' /tmp/full10.log")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_combined_quiet_flag_is_an_assertion(self):
        out = self._armed("grep -qE 'panic|fatal' /tmp/run.log")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_combined_count_with_recursive_is_still_a_count(self):
        out = self._armed("grep -rc 'TODO' src/")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_plain_count_flag_unchanged(self):
        out = self._armed("grep -c 'TODO' file.py")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_context_flag_is_not_a_count(self):
        # -C is CONTEXT (dumps matches with surrounding lines) — the
        # case-sensitivity here is load-bearing, not incidental
        out = self._armed("grep -C3 'TODO' file.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_ordinary_sweep_still_blocks(self):
        out = self._armed("grep -rnE 'TODO|FIXME' .")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class SmallBoundedReadsAllowed178(unittest.TestCase):
    """#178 (issue #178, user decision 2026-07-31, option 1): a genuinely
    small, explicit, EXISTING file read is a bounded read — the SIZE that
    comes back is what determines context cost, not the command name.
    Production evidence, same day: a `cat` of a 20-line conf file and a
    7-pattern `grep` sweep over `tests/` were both false blocks under a
    Fable-armed main, despite being trivially bounded reads."""

    def _fable(self, command):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=transcript("claude-fable-5"))

    def test_cat_one_real_small_file_allowed(self):
        with TemporaryDirectory() as d:
            f = Path(d) / "conf.txt"
            f.write_text("line\n" * 20)
            out = self._fable("cat %s" % f)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_grep_two_real_small_files_allowed(self):
        with TemporaryDirectory() as d:
            f1 = Path(d) / "a.py"
            f2 = Path(d) / "b.py"
            f1.write_text("pattern one\n")
            f2.write_text("pattern two\n")
            out = self._fable("grep -n pattern %s %s" % (f1, f2))
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class BookkeepingWritesExempt178(unittest.TestCase):
    """#178: a `/tmp/` scratchpad file or a
    `~/.claude/projects/*/memory/` note is coordinator BOOKKEEPING, never
    implementation — exempt from AIRULESET_FABLE_EDIT_MAX, up to
    AIRULESET_MAIN_READ_MAX_BYTES (default 131072, review fix — NOT
    unlimited), regardless of which arming condition holds. A repo file
    is NOT exempt and keeps the existing AIRULESET_FABLE_EDIT_MAX
    threshold unchanged. Same-day production evidence: two ~1KB
    scratchpad writes were false-blocked under a Fable-armed main.

    Also covers the fresh-context adversarial review of the first #178
    diff: a `..`-bearing path must get NO exemption at all (path
    traversal defeated the string-match exemption, reproducer:
    `/tmp/../<repo>/PWNED.py`), and the exemption itself must be
    SIZE-CAPPED, not unlimited (unlimited /tmp writes let a main session
    stage an implementation to /tmp then `cp` it into the repo)."""

    def _write(self, file_path, content=MID_1200, model="claude-fable-5"):
        helper = MainImplementationGuard()
        return helper._run(tool="Write", content=content, file_path=file_path,
                           transcript_text=transcript(model))

    def test_write_to_tmp_scratchpad_allowed_over_edit_max(self):
        out = self._write("/tmp/claude-x/scratchpad/note.md")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_write_to_memory_note_allowed_over_edit_max(self):
        # the hook only STRING-MATCHES the path — no filesystem check, so
        # the payload path is built literally rather than created on disk.
        mem_path = str(Path.home() / ".claude" / "projects" / "x"
                       / "memory" / "note.md")
        out = self._write(mem_path)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_write_to_repo_relative_py_path_still_blocked(self):
        # neither /tmp/ nor a memory path — the ordinary threshold applies
        out = self._write("addons/models/sale_order.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    # ---- review fix: path traversal gets NO exemption ----

    def test_tmp_path_with_traversal_out_of_tmp_is_never_exempt(self):
        # `/tmp/../<repo>/x.py` string-matches `/tmp/*` but resolves
        # OUTSIDE /tmp entirely — reviewer reproduced a 5000-char Write
        # exiting 0 straight into the repo tree via this shape.
        traversal_path = "/tmp/..%s/x.py" % REPO
        out = self._write(traversal_path)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_memory_path_with_traversal_is_never_exempt(self):
        mem_traversal = str(Path.home() / ".claude" / "projects" / "x"
                            / "memory" / ".." / ".." / ".." / "app.py")
        out = self._write(mem_traversal)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    # ---- review fix: the exemption is size-capped, not unlimited ----

    def test_tmp_scratchpad_write_over_the_cap_is_blocked(self):
        out = self._write("/tmp/claude-x/scratchpad/big.md",
                          content="n" * (131072 + 1))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_tmp_scratchpad_write_at_the_cap_is_allowed(self):
        out = self._write("/tmp/claude-x/scratchpad/atcap.md",
                          content="n" * 131072)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- #640: a DURABLE work-product dir joins the same bookkeeping ----
    # exemption. A large main-session WRITE of a work-product (an unapproved
    # client draft, a generated document, a recipe) must NOT be forced into
    # /tmp (which the fleet subdev-disk-hygiene.sh + airuleset's own
    # sweep_claude_scratch both delete) — the montalu6 loss. The durable home
    # is ~/.claude/work-products/** (hook glob `*/.claude/work-products/*`):
    # the sibling of the ~/.claude/projects/*/memory/* note exemption, and
    # anchored UNDER .claude/ so it can never false-match a repo's own
    # work-products/ subdir (the rejected `*/work-products/*` hole).

    def test_write_to_claude_work_products_allowed_over_edit_max(self):
        # the hook only STRING-MATCHES the path — the payload path is built
        # literally, home-agnostic like the memory-note test above.
        wp = str(Path.home() / ".claude" / "work-products" / "draft.md")
        out = self._write(wp)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_claude_work_products_write_over_the_cap_is_blocked(self):
        wp = str(Path.home() / ".claude" / "work-products" / "big.md")
        out = self._write(wp, content="n" * (131072 + 1))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_claude_work_products_write_at_the_cap_is_allowed(self):
        wp = str(Path.home() / ".claude" / "work-products" / "atcap.md")
        out = self._write(wp, content="n" * 131072)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_claude_work_products_path_with_traversal_is_never_exempt(self):
        # `.../.claude/work-products/../../../<repo>/app.py` string-matches
        # `*/.claude/work-products/*` but resolves OUTSIDE it — the `*..*)`
        # arm (first in the case) must still deny it (#178 review invariant).
        trav = str(Path.home() / ".claude" / "work-products"
                   / ".." / ".." / ".." / "app.py")
        out = self._write(trav)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_repo_work_products_subdir_is_NOT_exempt(self):
        # the REJECTED-approach hole: a bare `*/work-products/*` glob would
        # exempt a repo's OWN work-products/ subdir, letting a main session
        # stage implementation into the tree. The chosen `.claude`-anchored
        # glob must NOT — this repo path carries `/work-products/` but no
        # `.claude/work-products/`, so it stays BLOCKED.
        out = self._write("/home/x/devel/some-repo/work-products/models.py")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_bare_home_work_products_is_NOT_exempt(self):
        # ~/work-products (NOT under .claude) is likewise the rejected option
        # and stays BLOCKED — only ~/.claude/work-products/** is durable-exempt.
        bare = str(Path.home() / "work-products" / "draft.md")
        out = self._write(bare)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_repo_dot_claude_work_products_subdir_is_NOT_exempt(self):
        # #640 review (MAJOR): every managed repo carries a project-local
        # `.claude/` dir, so an UNANCHORED `*/.claude/work-products/*` would
        # exempt `<repo>/.claude/work-products/impl.py` — implementation code
        # landing IN THE TREE. The pattern is $HOME-ANCHORED, so a repo's own
        # .claude/work-products/ (under $HOME/devel/<repo>/, NOT $HOME/.claude/)
        # stays BLOCKED. This path lives under the real home but in a repo
        # subdir — the reviewer's exact live exit-0 repro, now denied.
        repo_dc = str(Path.home() / "devel" / "some-repo"
                      / ".claude" / "work-products" / "impl.py")
        out = self._write(repo_dc)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class SmallBoundedReadsStillBlockedControls178(unittest.TestCase):
    """#178 controls: the nonexistent / oversize / glob / poisoned-token
    shapes must stay blocked exactly as before — the new exemption must
    never widen past a genuinely small, explicit, EXISTING file. This is
    also what keeps every OTHER pre-existing fixture in this file (fake
    filenames that don't exist) blocked unchanged."""

    def _fable(self, command):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=transcript("claude-fable-5"))

    def test_cat_of_a_file_just_over_the_byte_bound_blocked(self):
        with TemporaryDirectory() as d:
            f = Path(d) / "big.txt"
            with open(f, "wb") as fh:
                fh.seek(131072)      # AIRULESET_MAIN_READ_MAX_BYTES default
                fh.write(b"x")       # 131073 bytes — one over the bound
            out = self._fable("cat %s" % f)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cat_glob_still_blocked(self):
        out = self._fable("cat *.jsonl")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_cat_nonexistent_file_still_blocked(self):
        out = self._fable("cat /nonexistent/x")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_recursive_grep_still_blocked(self):
        out = self._fable("grep -rn pattern .")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_grep_with_one_nonexistent_file_token_still_blocked(self):
        # one bad token poisons the whole small_files() check
        with TemporaryDirectory() as d:
            f1 = Path(d) / "real.py"
            f1.write_text("pattern\n")
            out = self._fable("grep -n pattern %s /nonexistent/fake.py" % f1)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class AggregateReadBudget178Review(unittest.TestCase):
    """Fresh-context adversarial review of the #178 diff, finding 3: N
    small-file reads chained in ONE command, each individually under
    AIRULESET_MAIN_READ_MAX_BYTES, must not sum to something huge —
    reviewer reproduced 10 x 120000-byte `cat`s (1.2 MB aggregate) all
    passing. The fix draws every small-file exemption in one command from
    a SHARED per-command budget."""

    def _fable(self, command):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=transcript("claude-fable-5"))

    def test_two_chained_reads_over_the_aggregate_budget_blocked(self):
        with TemporaryDirectory() as d:
            f1 = Path(d) / "a.log"
            f2 = Path(d) / "b.log"
            f1.write_bytes(b"a" * 80000)
            f2.write_bytes(b"b" * 80000)   # 160000 aggregate > 131072
            out = self._fable("cat %s ; cat %s" % (f1, f2))
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_one_read_of_the_same_size_alone_is_allowed(self):
        # control: the SAME per-file size, on its own, fits the budget —
        # proves the aggregate check (not a lowered per-file cap) is what
        # blocks the chained case above.
        with TemporaryDirectory() as d:
            f1 = Path(d) / "a.log"
            f1.write_bytes(b"a" * 80000)
            out = self._fable("cat %s" % f1)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class GrepDereferenceRecursive178Review(unittest.TestCase):
    """Fresh-context adversarial review of the #178 diff, finding 4:
    `--dereference-recursive` (grep's long alias of `-R`) was not in the
    explicit recursive block list. Against a DIRECTORY target it only
    survived by accident via the downstream isfile() refusal — but
    against a REAL SMALL FILE target (where `-R`'s recursion has nothing
    to recurse into, yet the flag still declares recursive INTENT) the
    old classifier fell through to the small-file exemption and allowed
    it. Spelled out explicitly now so intent, not incidental target
    shape, decides."""

    def _fable(self, command):
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=transcript("claude-fable-5"))

    def test_dereference_recursive_grep_against_a_directory_still_blocked(self):
        out = self._fable("grep --dereference-recursive pattern .")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_dereference_recursive_grep_against_a_real_small_file_blocked(self):
        # the genuinely RED-reproducible case: pre-fix, a real small FILE
        # target fell through to the small-file exemption and passed —
        # the directory-target case above was already blocked by accident.
        with TemporaryDirectory() as d:
            f = Path(d) / "real.txt"
            f.write_text("pattern here\n")
            out = self._fable("grep --dereference-recursive pattern %s" % f)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class ManualRevival174(unittest.TestCase):
    """(#174) Live incident (2026-07-29): reviving a stalled `simap` pane by
    hand, the MAIN session read a stale input-box draft off the pane's
    screen and typed it back as if it were a real pending prompt --
    `continue` was never sent at all. It was a frozen render of a session
    that had died 6.5h earlier, not a pending draft.

    UNCONDITIONAL — not gated by Fable/goal-armed/away, and NO bypass
    marker honors it (this is a correctness/safety concern, not a
    cost-control one; there is no legitimate reason to type a pane's own
    captured content back into it). Covers the mechanically-safe slice: a
    command that CAPTURES a pane (`tmux capture-pane`/`display-message`,
    via a variable or an inline command substitution) and feeds that SAME
    value into a `tmux send-keys` payload, in one command line."""

    def _plain(self, command):
        # a completely ordinary transcript -- non-Fable, no goal armed, no
        # bypass marker -- proving the check fires regardless of those.
        helper = MainImplementationGuard()
        return helper._run(tool="Bash", command=command,
                           transcript_text=transcript("claude-opus-4-8"))

    def test_variable_captured_then_sent_is_blocked(self):
        out = self._plain(
            'TEXT=$(tmux capture-pane -t %5 -p); tmux send-keys -t %5 -l "$TEXT"')
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#174", out.stderr)

    def test_braced_variable_form_is_also_blocked(self):
        out = self._plain(
            'TEXT=$(tmux capture-pane -t %5 -p); tmux send-keys -t %5 -l "${TEXT}"')
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_display_message_captured_then_sent_is_blocked(self):
        out = self._plain(
            'D=$(tmux display-message -t %5 -p "#{pane_id}"); '
            'tmux send-keys -t %5 -l "$D"')
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_inline_capture_directly_in_send_keys_is_blocked(self):
        out = self._plain(
            'tmux send-keys -t %5 -l "$(tmux capture-pane -t %5 -p)"')
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_deliberate_literal_continue_is_allowed(self):
        out = self._plain('tmux send-keys -t %5 -l "continue"')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_control_key_name_is_allowed(self):
        out = self._plain('tmux send-keys -t %5 Enter')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_an_unrelated_variable_is_allowed(self):
        # MSG is hand-composed, never derived from a pane capture -- must
        # not be treated as suspicious just for being a variable.
        out = self._plain(
            'MSG="deliberate reply"; tmux send-keys -t %5 -l "$MSG"')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_capture_pane_alone_with_no_send_is_allowed(self):
        # reading a pane to DECIDE what to do next (the normal diagnostic
        # step) must never itself be blocked -- only capture-THEN-send.
        out = self._plain('tmux capture-pane -t %5 -p')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_unrelated_bash_command_is_unaffected(self):
        out = self._plain('gh issue view 42')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_no_bypass_marker_can_rescue_it(self):
        # this is a correctness/safety concern -- unlike the cost-control
        # conditions, there is no escape hatch.
        sid = "t-mg-174-nobypass-" + uuid.uuid4().hex[:8]
        m = Path("/tmp/airuleset-main-exec-ok-%s" % sid)
        m.write_text("a perfectly good reason for something else entirely")
        self.addCleanup(lambda: m.unlink(missing_ok=True))
        helper = MainImplementationGuard()
        out = helper._run(
            tool="Bash",
            command='TEXT=$(tmux capture-pane -t %5 -p); tmux send-keys -t %5 -l "$TEXT"',
            sid=sid, transcript_text=transcript("claude-opus-4-8"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class TestWiringAndSkill(unittest.TestCase):
    def test_hook_exists_and_wired_for_edit_and_write(self):
        self.assertTrue(HOOK.exists())
        self.assertTrue(os.access(HOOK, os.X_OK))
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        for tool in ("Edit", "Write"):
            ms = json.dumps([mm for mm in cfg["hooks"]["PreToolUse"]
                             if mm.get("matcher") == tool])
            self.assertIn("block-main-implementation.sh", ms,
                          "guard missing for PreToolUse(%s)" % tool)

    def test_hook_wired_for_bash_too(self):
        # #66: Bash is now ALSO guarded (goal-armed/Fable main only)
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        ms = json.dumps([mm for mm in cfg["hooks"]["PreToolUse"]
                         if mm.get("matcher") == "Bash"])
        self.assertIn("block-main-implementation.sh", ms,
                      "guard missing for PreToolUse(Bash)")
        # the OLD filename must be gone from the wiring — this is a rename,
        # not an addition of a second hook.
        self.assertNotIn("block-fable-main-implementation.sh",
                         (REPO / "settings" / "hooks.json").read_text())

    def test_hook_wired_for_dispatch_tools_so_the_counter_can_reset(self):
        # #80: the per-dispatch counter resets on a DISPATCH — the hook must
        # therefore run on PreToolUse(Agent/Task/Workflow) too. Exact tool
        # names, never a regex matcher: an unsupported regex would silently
        # never match and the counter would never reset.
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        for tool in ("Agent", "Task", "Workflow"):
            ms = json.dumps([mm for mm in cfg["hooks"]["PreToolUse"]
                             if mm.get("matcher") == tool])
            self.assertIn("block-main-implementation.sh", ms,
                          "counter reset missing for PreToolUse(%s)" % tool)

    def test_fable_advisor_skill_exists_and_registered(self):
        sk = REPO / "skills" / "fable-advisor" / "SKILL.md"
        self.assertTrue(sk.exists())
        txt = sk.read_text()
        for needle in ("fable-gate", "digest", "xhigh", "sonnet"):
            self.assertIn(needle, txt, needle)
        self.assertIn("fable-advisor", airuleset.SKILL_NAMES)

    def test_model_awareness_points_at_the_enforcement(self):
        txt = (REPO / "modules" / "core" / "model-awareness.md").read_text()
        self.assertIn("block-main-implementation.sh", txt)
        self.assertIn("fable-advisor", txt)
        # the generalization itself must be documented, not just the old
        # Fable-only behavior
        self.assertIn("#54", txt)
        self.assertRegex(txt, r"(?i)armed[^\n]*(/goal|goal)")


class CrossUserLogPathCollision492(unittest.TestCase):
    """#492: the audit/bypass telemetry used FIXED /tmp filenames
    (/tmp/airuleset-main-exec-block.log, -bypass.log). On a shared box the
    FIRST user to create each file owns it 0644; every OTHER user's `>>`
    append then fails EACCES — and because `>> file 2>/dev/null || true`
    does NOT silence a redirect-OPEN failure (bash reports the error on the
    shell's stderr before the per-command 2> applies; || true only fixes
    the exit status), the `Permission denied` LEAKS to stderr, which Claude
    Code surfaces as a `PreToolUse:Bash hook error` on every block. Fix:
    per-user (`-<uid>`) path so each user owns their own accumulating file,
    plus a brace-group redirect so an unwritable log can never leak again."""

    UID = os.getuid()
    BLOCK_LOG = Path("/tmp/airuleset-main-exec-block-%d.log" % UID)
    BYPASS_LOG = Path("/tmp/airuleset-main-exec-bypass-%d.log" % UID)
    FIXED_BLOCK = Path("/tmp/airuleset-main-exec-block.log")
    FIXED_BYPASS = Path("/tmp/airuleset-main-exec-bypass.log")

    def _make_unwritable(self, p):
        """Simulate a first-user-owned unwritable file at `p`: save any real
        content, recreate it 0444, restore on cleanup. Skips as root (root
        bypasses permission bits, so the EACCES the bug needs cannot arise)."""
        if os.geteuid() == 0:
            self.skipTest("root bypasses file permission bits; cannot reproduce EACCES")
        saved = None
        if p.exists():
            saved = (p.read_bytes(), p.stat().st_mode)

        def restore():
            try:
                if p.exists():
                    os.chmod(p, 0o644)
                    p.unlink()
                if saved is not None:
                    p.write_bytes(saved[0])
                    os.chmod(p, saved[1])
            except OSError:
                # airuleset:script-ok best-effort test-cleanup restore of a
                # shared /tmp telemetry file; a failure here must not mask
                # the actual test result.
                pass
        self.addCleanup(restore)
        p.write_text("")            # created 0644 by us...
        os.chmod(p, 0o444)          # ...then made unwritable (foreign-owned proxy)

    def test_block_no_permission_denied_leak_when_fixed_path_unwritable(self):
        # RED on old code (writes to the fixed 0444 path -> leaks); GREEN
        # after fix (writes to the per-uid path, never touches this one).
        self._make_unwritable(self.FIXED_BLOCK)
        sid = "t-492-blockleak-" + uuid.uuid4().hex[:8]
        out = MainImplementationGuard()._run(
            tool="Bash", command="grep -rn 'TODO' .", sid=sid,
            transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 2, out.stderr)     # block still enforced
        self.assertNotIn("Permission denied", out.stderr, out.stderr)
        self.assertNotIn("airuleset-main-exec-block.log", out.stderr, out.stderr)

    def test_block_logged_to_per_user_path(self):
        # RED on old code (per-uid file has no such line / doesn't exist);
        # GREEN after fix.
        sid = "t-492-blockpath-" + uuid.uuid4().hex[:8]
        out = MainImplementationGuard()._run(
            tool="Bash", command="grep -rn 'TODO' .", sid=sid,
            transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertTrue(self.BLOCK_LOG.exists(),
                        "per-user block log not created: %s" % self.BLOCK_LOG)
        lines = [ln for ln in self.BLOCK_LOG.read_text().splitlines() if sid in ln]
        self.assertTrue(lines, "block not logged to the per-user path")

    def test_bypass_no_leak_and_logged_to_per_user_path_when_fixed_unwritable(self):
        # RED on old code; GREEN after fix.
        self._make_unwritable(self.FIXED_BYPASS)
        sid = "t-492-bypass-" + uuid.uuid4().hex[:8]
        out = MainImplementationGuard()._run(
            tool="Edit", content=BIG, sid=sid, bypass="new")
        self.assertEqual(out.returncode, 0, out.stderr)     # bypass consumed
        self.assertNotIn("Permission denied", out.stderr, out.stderr)
        self.assertTrue(self.BYPASS_LOG.exists(),
                        "per-user bypass log not created")
        self.assertTrue(
            [ln for ln in self.BYPASS_LOG.read_text().splitlines() if sid in ln],
            "bypass not logged to the per-user path")

    def test_hardened_redirect_no_leak_even_when_per_user_log_unwritable(self):
        # Guards the brace-group hardening: even the per-uid file itself
        # being unwritable (disk full / hostile precreation on sticky /tmp)
        # must never leak. GREEN after fix (verifies the redirect form).
        self._make_unwritable(self.BLOCK_LOG)
        sid = "t-492-hardened-" + uuid.uuid4().hex[:8]
        out = MainImplementationGuard()._run(
            tool="Bash", command="grep -rn 'TODO' .", sid=sid,
            transcript_text=goal_armed_transcript())
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertNotIn("Permission denied", out.stderr, out.stderr)


if __name__ == "__main__":
    unittest.main()
