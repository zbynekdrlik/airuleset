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
             command=None):
        sid = sid or ("t-mg-" + uuid.uuid4().hex[:8])
        with TemporaryDirectory() as d:
            tp = str(Path(d) / "sess.jsonl")
            Path(tp).write_text(transcript_text
                                if transcript_text is not None
                                else transcript(model))
            if bypass:
                marker = ("/tmp/airuleset-main-exec-ok-%s" % sid if bypass == "new"
                          else "/tmp/airuleset-fable-exec-ok-%s" % sid)
                Path(marker).write_text("")
                self.addCleanup(lambda: Path(marker).unlink(missing_ok=True))
            if tool == "Bash":
                ti = {"command": command if command is not None else content}
            elif tool == "Edit":
                ti = {"file_path": "/x/app.py", "old_string": "a",
                      "new_string": content}
            else:
                ti = {"file_path": "/x/app.py", "content": content}
            payload = {"session_id": sid, "hook_event_name": "PreToolUse",
                       "tool_name": tool, "tool_input": ti,
                       "transcript_path": tp}
            if agent_id:
                payload["agent_id"] = agent_id
            return subprocess.run(["bash", str(HOOK)],
                                  input=json.dumps(payload),
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
        # the heredoc write passes; the SEPARATE read segment still doesn't
        out = self._armed(
            "cat > /tmp/b.md <<'EOF'\nbody\nEOF\ncat /etc/passwd")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class BlockLogging73(unittest.TestCase):
    """#73: every BLOCK must be written to its own log (timestamp, session,
    first ~120 chars of the command, which rule matched) — today the hook
    logs only bypasses, so there is no way to answer 'did it fire, on what'
    after a deploy. Same style/location as the existing bypass log."""

    LOG_PATH = Path("/tmp/airuleset-main-exec-block.log")

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


if __name__ == "__main__":
    unittest.main()
