"""A REPEATED foreground CI poll loop is hard-blocked (airuleset #118).

Live burn (2026-07-28, restreamer pane zbynek-0:0): a session watching one
2-hour CI run chained ~13 nine-minute FOREGROUND poll turns, each re-sending
a ~170K-token context, after a /compact and after re-reading ci-monitoring.md
— i.e. prose had just been read and still lost. Two rewrites of that rule
(#107, #110) were already post-dated by the same failure, so the repo's own
intake gate applies: a mechanically checkable rule belongs in a hook.

The decided shape (issue #118, option A):

- The FIRST foreground poll loop per (session, run-id) stays ALLOWED. Wait
  length is unknowable at decision time — that is why every predict-the-length
  rule failed — but a first loop that returned non-terminal is MEASURED proof
  the wait is long, and genuinely short waits end inside loop 1 and never
  reach the hook. The burn was loops 2-13, not loop 1.
- The 2nd+ loop for the same run is BLOCKED (exit 2) with a message carrying
  the ready-to-paste background waiter, run-id already substituted. The model
  reaches for the first concrete artefact, so the COMPLIANT command must be
  the zero-thought path; a block that says only "no" produces evasion.
- In SUBAGENT context the background waiter is BROKEN (a subagent with no
  pending foreground call is returned as completed and terminates —
  block-subagent-bg-ci-poll.sh / subagent-stop-check-bg-work.sh exist to force
  foreground waits there). The subagent branch must therefore hand the run-id
  back to the supervisor and RETURN, and must never offer run_in_background.

The detector is REUSED from #111 (a `sleep` inside a `do`…`done` body), gated
by a CI-wait signature so non-CI wait loops are untouched.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset

REPO = Path(airuleset.__file__).resolve().parent
HOOK = REPO / "hooks" / "block-ci-poll-repeat.sh"

RUN_A = "30326991380"
RUN_B = "30419922711"

# The verbatim ci-monitoring.md foreground block, run-id substituted. Built
# as data here (never read back from any repo file) so this test cannot pass
# or fail because of prose that happens to sit near it.
def poll_loop(run_id):
    return (
        'DEADLINE=$((SECONDS + ${AIRULESET_POLL_BUDGET_S:-540}))\n'
        'for i in $(seq 1 18); do\n'
        '  s=$(gh run view %s --json status,conclusion '
        '--jq \'.status+" "+(.conclusion//"")\')\n'
        '  case "$s" in completed*) echo "TERMINAL: $s"; break;; esac\n'
        '  if [ "$SECONDS" -ge "$DEADLINE" ]; then echo "BUDGET"; break; fi\n'
        '  sleep 30\n'
        'done' % run_id
    )


def payload(command, session="sess-118", agent_id=None, background=False,
            timeout_ms=540000):
    """A real PreToolUse(Bash) payload — always via json.dumps, never hand-escaped."""
    d = {
        "session_id": session,
        "tool_name": "Bash",
        "tool_input": {"command": command, "timeout": timeout_ms},
    }
    if background:
        d["tool_input"]["run_in_background"] = True
    if agent_id:
        d["agent_id"] = agent_id
    return json.dumps(d)


class CiPollRepeatBlockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def run_hook(self, command, **kw):
        env = dict(os.environ)
        env["AIRULESET_CIPOLL_STATE_DIR"] = self.state
        return subprocess.run(
            ["bash", str(HOOK)],
            input=payload(command, **kw), text=True, env=env,
            capture_output=True, timeout=30)

    # ---- wiring ----------------------------------------------------------
    def test_hook_exists_and_is_wired_as_a_pretooluse_bash_hook(self):
        self.assertTrue(HOOK.exists(), "hooks/block-ci-poll-repeat.sh missing")
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        wired = [
            h.get("command", "")
            for entry in cfg["hooks"]["PreToolUse"]
            if entry.get("matcher") == "Bash"
            for h in entry.get("hooks", [])
        ]
        self.assertTrue(
            any("block-ci-poll-repeat.sh" in c for c in wired),
            "hook is not registered under PreToolUse/Bash")

    # ---- the carve-out: loop 1 is free -----------------------------------
    def test_first_foreground_poll_loop_is_allowed(self):
        out = self.run_hook(poll_loop(RUN_A))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr.strip(), "")

    def test_second_loop_on_the_same_run_is_blocked(self):
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 0)
        out = self.run_hook(poll_loop(RUN_A))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#118", out.stderr)

    def test_third_loop_is_still_blocked(self):
        self.run_hook(poll_loop(RUN_A))
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)

    # ---- state keying ----------------------------------------------------
    def test_a_second_run_id_gets_its_own_free_first_loop(self):
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)
        # push -> fix -> push: a NEW run is legitimately a new short wait
        out = self.run_hook(poll_loop(RUN_B))
        self.assertEqual(out.returncode, 0, out.stderr)
        # ...and its own repeat is blocked
        self.assertEqual(self.run_hook(poll_loop(RUN_B)).returncode, 2)

    def test_state_is_per_session(self):
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)
        out = self.run_hook(poll_loop(RUN_A), session="other-session")
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- the block message IS the fix ------------------------------------
    def test_block_message_carries_the_substituted_run_id_and_the_waiter(self):
        self.run_hook(poll_loop(RUN_A))
        err = self.run_hook(poll_loop(RUN_A)).stderr
        self.assertIn(RUN_A, err, "run-id must be substituted into the command")
        self.assertNotIn("<run-id>", err, "no placeholder left to think about")
        self.assertIn("run_in_background", err)
        self.assertIn("gh run view", err)
        self.assertIn("AIRULESET_LONG_POLL_BUDGET_S", err)
        # the subagent branch must be present even in main context, because a
        # detection miss must not push a subagent onto the background waiter
        self.assertIn("SUBAGENT", err)
        self.assertIn("RETURN", err)

    def test_subagent_block_never_offers_a_background_waiter(self):
        kw = {"agent_id": "a2afddb67fb83f7c7"}
        self.assertEqual(self.run_hook(poll_loop(RUN_A), **kw).returncode, 0)
        out = self.run_hook(poll_loop(RUN_A), **kw)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertNotIn(
            "run_in_background", out.stderr,
            "a subagent that backgrounds a wait TERMINATES — never offer it")
        self.assertIn("supervisor", out.stderr)
        self.assertIn("RETURN", out.stderr)
        self.assertIn(RUN_A, out.stderr)

    # ---- what must NEVER be blocked --------------------------------------
    def test_background_waiter_is_never_blocked(self):
        self.run_hook(poll_loop(RUN_A))
        waiter = (
            'timeout "${AIRULESET_LONG_POLL_BUDGET_S:-10800}" bash -c \'while :; do\n'
            '  s=$(gh run view %s --json status,conclusion) || s="ERROR"\n'
            '  case "$s" in completed*) echo "TERMINAL: $s"; exit 0 ;; esac\n'
            '  sleep 60\n'
            'done\'' % RUN_A)
        out = self.run_hook(waiter, background=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_single_status_check_is_always_allowed_even_after_a_block(self):
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)
        out = self.run_hook("gh run view %s --json status,conclusion" % RUN_A)
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_hook("gh run view %s --log-failed | tail -50" % RUN_A)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_non_ci_wait_loops_are_untouched(self):
        loop = ('for i in $(seq 1 30); do\n'
                '  [ -f /tmp/build.done ] && break\n'
                '  sleep 10\n'
                'done')
        self.assertEqual(self.run_hook(loop).returncode, 0)
        self.assertEqual(self.run_hook(loop).returncode, 0)
        self.assertEqual(self.run_hook(loop).returncode, 0)

    def test_prose_mentioning_a_poll_loop_is_not_blocked(self):
        # #111/#112 residual: a heredoc body that merely CONTAINS the tokens.
        prose = ("cat > /tmp/note.md <<'EOF'\n"
                 "The rule says: do the poll, sleep between attempts, done.\n"
                 "EOF")
        self.assertEqual(self.run_hook(prose).returncode, 0)
        self.assertEqual(self.run_hook(prose).returncode, 0)

    def test_a_gh_command_without_a_loop_is_not_blocked(self):
        cmd = "gh run list --limit 5 --json databaseId,status"
        for _ in range(4):
            self.assertEqual(self.run_hook(cmd).returncode, 0)

    # ---- bounded backstop after a block ----------------------------------
    def test_sleep_then_view_burst_is_free_before_a_block(self):
        out = self.run_hook("sleep 300 && gh run view %s --json status" % RUN_A)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_sleep_then_view_burst_is_blocked_after_a_block_on_that_run(self):
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)
        out = self.run_hook("sleep 300 && gh run view %s --json status" % RUN_A)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn(RUN_A, out.stderr)

    def test_burst_backstop_does_not_leak_to_another_run(self):
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)
        out = self.run_hook("sleep 300 && gh run view %s --json status" % RUN_B)
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- escape hatch + corpus logging -----------------------------------
    def test_inline_bypass_is_honoured_and_logged(self):
        self.run_hook(poll_loop(RUN_A))
        cmd = poll_loop(RUN_A) + "  # airuleset:poll-ok fanning 9 repos, not a wait"
        out = self.run_hook(cmd)
        self.assertEqual(out.returncode, 0, out.stderr)
        log = Path(self.state) / "airuleset-cipoll-bypass.log"
        self.assertTrue(log.exists(), "bypass must be logged for corpus review")
        self.assertIn("fanning 9 repos", log.read_text())

    def test_every_block_is_logged(self):
        self.run_hook(poll_loop(RUN_A))
        self.run_hook(poll_loop(RUN_A))
        log = Path(self.state) / "airuleset-cipoll-block.log"
        self.assertTrue(log.exists(), "every block must be logged")
        body = log.read_text()
        self.assertIn(RUN_A, body)
        self.assertIn("sess-118", body)

    def test_post_block_poll_bursts_are_logged(self):
        self.run_hook(poll_loop(RUN_A))
        self.run_hook(poll_loop(RUN_A))
        self.run_hook("gh run view %s --json status" % RUN_A)
        log = Path(self.state) / "airuleset-cipoll-postblock.log"
        self.assertTrue(log.exists(), "post-block bursts must be reviewable")
        self.assertIn(RUN_A, log.read_text())

    # ---- fail-open -------------------------------------------------------
    def test_unparseable_payload_fails_open(self):
        env = dict(os.environ)
        env["AIRULESET_CIPOLL_STATE_DIR"] = self.state
        out = subprocess.run(["bash", str(HOOK)], input="not json at all",
                             text=True, env=env, capture_output=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)


class ModulePointerTest(unittest.TestCase):
    """The ONLY prose change permitted: one line on the foreground bullet."""

    def test_foreground_bullet_names_the_hook_once(self):
        text = (REPO / "modules" / "core" / "ci-monitoring.md").read_text()
        self.assertEqual(
            text.count("block-ci-poll-repeat.sh"), 1,
            "exactly one pointer — this ticket is not a third rewrite")
        self.assertIn("Foreground bounded poll loop", text)
        # the pointer sits in the foreground bullet's own paragraph
        head, _, tail = text.partition("block-ci-poll-repeat.sh")
        self.assertIn("nudge-poll-loop-timeout.sh", head[-600:])


if __name__ == "__main__":
    unittest.main()
