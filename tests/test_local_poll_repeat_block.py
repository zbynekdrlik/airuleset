"""A REPEATED foreground poll loop with no CI signature is hard-blocked
(airuleset #119).

#118 narrows on a CI signature BEFORE any loop-shape detection:

    grep -qE 'gh[[:space:]]+run[[:space:]]+(view|watch|list)|gh[[:space:]]+pr[[:space:]]+checks' || exit 0

so a loop with no such token exits 0 and is never seen. That narrowing is
deliberate and correct for #118 — it is a boundary, not a bug — and everything
outside it was unguarded.

Measured over 8,107 transcripts / 251,551 Bash commands: of 4,430 foreground
loop-shape commands, 2,326 carry a CI signature and 2,104 do NOT. The
uncovered remainder is remote/process 814, subagent-result 746, other 191,
local-log 185, endpoint 106, file-appears 62 — so a guard keyed on
`result.json` (the ticket title's framing) would miss about two thirds of its
own family. In the ticket's own 24h window the population is 335 commands
across SIX projects, not 118 across two, and only 41 (12%) poll a
subagent-result-shaped path at all.

Shape decisions, each carried over from #118 because its corpus already paid
for them: loop 1 per key is FREE (wait length is unknowable up front; a first
loop that came back without its condition met is measured proof it is long); a
mutating command is not primarily a wait; `run_in_background` is the compliant
path and is never touched; the bypass marker is honoured and logged.

What is NEW here, and is the ticket's own constraint: the KEY is a digit-blind
hash of the normalised loop SHAPE, not a path or a filename — so the guard is
general, a genuinely new wait always gets a free first loop, and `seq 1 30` ->
`seq 1 60` still collides. And the MESSAGE BRANCHES by what is being waited on,
because the honest advice differs: a real Claude Code task artefact already has
a notification coming, while a log line / ssh state / file has none, and telling
the second group "trust the notification" would be a lie.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset

REPO = Path(airuleset.__file__).resolve().parent
HOOKS = REPO / "hooks"
HOOK = HOOKS / "block-local-poll-repeat.sh"

# Fixtures are DATA, never read back from a repo file, so no test can pass or
# fail because of prose that happens to sit near it.
TASK_OUT = ("/tmp/claude-1000/-home-newlevel-devel-airuleset/"
            "1fb70b5c-9e19-49f5-b9c1-094e98544792/tasks/bu7t7edz8.output")


def obs_loop(iters=36, nap=5):
    return ('for i in $(seq 1 %d); do\n'
            '  grep -q "Loaded Modules" /tmp/obs-boot.log && break\n'
            '  sleep %d\n'
            'done' % (iters, nap))


def task_loop(path=TASK_OUT, nap=15):
    return ('for i in $(seq 1 30); do\n'
            '  if [ -f %s ]; then echo READY; break; fi\n'
            '  sleep %d\n'
            'done' % (path, nap))


def ssh_loop():
    return ("ssh dev2 'for i in $(seq 1 16); do pgrep -f setup-device "
            ">/dev/null || break; sleep 30; done'")


def ci_loop(run="30326991380"):
    return ('for i in $(seq 1 18); do\n'
            '  s=$(gh run view %s --json status,conclusion)\n'
            '  case "$s" in completed*) break;; esac\n'
            '  sleep 30\n'
            'done' % run)


def payload(command, session="sess-119", agent_id=None, background=False,
            timeout_ms=540000):
    d = {"session_id": session, "tool_name": "Bash",
         "tool_input": {"command": command, "timeout": timeout_ms}}
    if background:
        d["tool_input"]["run_in_background"] = True
    if agent_id:
        d["agent_id"] = agent_id
    return json.dumps(d)


class _Runner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def run_hook(self, command, hook=None, **kw):
        env = dict(os.environ)
        env["AIRULESET_LOCALPOLL_STATE_DIR"] = self.state
        out = subprocess.run(
            ["bash", str(hook or HOOK)], input=payload(command, **kw),
            text=True, env=env, capture_output=True, timeout=60)
        if out.returncode == 0:
            self.assertEqual(out.stderr.strip(), "",
                             "an allowed command must be silent on stderr")
        return out


class WiringTest(_Runner):
    def test_hook_exists_and_is_wired_as_a_pretooluse_bash_hook(self):
        self.assertTrue(HOOK.exists(), "hooks/block-local-poll-repeat.sh missing")
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        wired = [h.get("command", "")
                 for entry in cfg["hooks"]["PreToolUse"]
                 if entry.get("matcher") == "Bash"
                 for h in entry.get("hooks", [])]
        self.assertTrue(any("block-local-poll-repeat.sh" in c for c in wired),
                        "hook is not registered under PreToolUse/Bash")

    def test_it_uses_the_shared_payload_stripper(self):
        """#119's constraint 3: the new guard must not inherit #124."""
        self.assertIn("lib-poll-payload.sh", HOOK.read_text())


class FirstLoopFreeRepeatBlockedTest(_Runner):
    def test_first_local_poll_loop_is_allowed(self):
        self.assertEqual(self.run_hook(obs_loop()).returncode, 0)

    def test_second_loop_on_the_same_wait_is_blocked(self):
        self.assertEqual(self.run_hook(obs_loop()).returncode, 0)
        out = self.run_hook(obs_loop())
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#119", out.stderr)

    def test_third_loop_is_still_blocked(self):
        self.run_hook(obs_loop())
        self.run_hook(obs_loop())
        self.assertEqual(self.run_hook(obs_loop()).returncode, 2)

    def test_a_different_wait_gets_its_own_free_first_loop(self):
        self.run_hook(obs_loop())
        self.assertEqual(self.run_hook(obs_loop()).returncode, 2)
        out = self.run_hook(task_loop())
        self.assertEqual(out.returncode, 0,
                         "a genuinely new wait is a new short wait")
        self.assertEqual(self.run_hook(task_loop()).returncode, 2)

    def test_the_key_is_digit_blind_so_retuning_the_loop_still_collides(self):
        """`seq 1 30` -> `seq 1 60`, `sleep 15` -> `sleep 20`: same wait."""
        self.assertEqual(self.run_hook(obs_loop(36, 5)).returncode, 0)
        self.assertEqual(self.run_hook(obs_loop(72, 10)).returncode, 2)

    def test_state_is_per_session(self):
        self.run_hook(obs_loop())
        self.assertEqual(self.run_hook(obs_loop()).returncode, 2)
        self.assertEqual(
            self.run_hook(obs_loop(), session="other").returncode, 0)


class OutOfContractTest(_Runner):
    """Everything this hook must never touch."""

    def test_ci_poll_loops_are_never_touched_at_any_repeat_count(self):
        """#118 owns CI, and owns it alone — two hooks blocking one command
        would give the model two different corrective commands."""
        for _ in range(5):
            self.assertEqual(self.run_hook(ci_loop()).returncode, 0)

    def test_a_ci_loop_does_not_even_consume_a_slot(self):
        for _ in range(3):
            self.run_hook(ci_loop())
        self.assertEqual(self.run_hook(ci_loop()).returncode, 0)

    def test_background_waiter_is_never_blocked(self):
        waiter = ("timeout 10800 bash -c 'until [ -f /tmp/done ]; "
                  "do sleep 60; done'")
        for _ in range(4):
            self.assertEqual(
                self.run_hook(waiter, background=True).returncode, 0)

    def test_a_mutating_command_is_exempt_even_after_a_block(self):
        self.run_hook(obs_loop())
        self.assertEqual(self.run_hook(obs_loop()).returncode, 2)
        cmd = ("git push origin main && " + obs_loop())
        self.assertEqual(self.run_hook(cmd).returncode, 0,
                         "blocking this would block the PUSH, not a poll")

    def test_a_tight_retry_loop_is_not_a_long_wait(self):
        tight = ('for i in $(seq 1 5); do curl -sf http://x/ && break; '
                 'sleep 1; done')
        for _ in range(4):
            self.assertEqual(self.run_hook(tight).returncode, 0)

    def test_a_loop_with_no_measurable_sleep_is_not_blocked(self):
        loop = 'for i in $(seq 1 9); do check; sleep "$T"; done'
        for _ in range(3):
            self.assertEqual(self.run_hook(loop).returncode, 0)

    def test_a_single_status_check_is_always_allowed(self):
        self.run_hook(obs_loop())
        self.assertEqual(self.run_hook(obs_loop()).returncode, 2)
        self.assertEqual(
            self.run_hook("grep -c 'Loaded Modules' /tmp/obs-boot.log")
            .returncode, 0)

    def test_it_does_not_inherit_124(self):
        """A doc write QUOTING a local poll loop polls nothing, however often
        it is repeated (#119 constraint 3)."""
        doc = ("cat >> docs/autopilot-log.md <<'MDEOF'\n"
               "The shape that burns is:\n" + obs_loop() + "\nMDEOF")
        for _ in range(4):
            self.assertEqual(self.run_hook(doc).returncode, 0)
        # control pin: the same body as bare control flow DOES block on repeat
        self.assertEqual(self.run_hook(obs_loop()).returncode, 0)
        self.assertEqual(self.run_hook(obs_loop()).returncode, 2)


class MessageBranchesByWhatIsWaitedOnTest(_Runner):
    """The ticket's constraint 2. Handing a local-log wait the
    'the harness wakes you' line would be false; handing a task artefact
    #118's CI waiter command would be a different wrong answer."""

    # Assert the CLAIM, never a bare substring: the generic branch legitimately
    # uses the word "notification" to say there is NOT one, so `assertNotIn
    # ("notification")` would forbid the very sentence that makes the branch
    # honest. These two phrases are the branches' load-bearing claims, and each
    # is asserted absent from the other — which is strictly stronger than a
    # word check, since a single-message hook fails both directions.
    TASK_CLAIM = "harness ALREADY wakes you"
    GENERIC_CLAIM = "Nothing will wake you"

    def _blocked_stderr(self, cmd, **kw):
        self.run_hook(cmd, **kw)
        out = self.run_hook(cmd, **kw)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        return out.stderr

    def test_a_task_artefact_wait_is_told_the_notification_already_exists(self):
        err = self._blocked_stderr(task_loop())
        self.assertIn(self.TASK_CLAIM, err)
        self.assertNotIn(self.GENERIC_CLAIM, err)
        self.assertIn("stat -L", err,
                      "that path is a symlink — plain stat reads the link")
        self.assertIn("29193", err, "name the orphaned-handle recovery case")

    def test_a_local_log_wait_is_NOT_told_a_notification_is_coming(self):
        err = self._blocked_stderr(obs_loop())
        self.assertNotIn(self.TASK_CLAIM, err,
                         "there is no notification for an OBS boot log — "
                         "claiming one would be a lie")
        self.assertIn(self.GENERIC_CLAIM, err, "say so explicitly")
        self.assertIn("run_in_background", err)

    def test_an_ssh_wait_gets_the_generic_branch_too(self):
        err = self._blocked_stderr(ssh_loop())
        self.assertNotIn(self.TASK_CLAIM, err)
        self.assertIn(self.GENERIC_CLAIM, err)
        self.assertIn("run_in_background", err)

    def test_a_user_invented_result_json_is_NOT_the_task_branch(self):
        """A `/tmp/ab94/result-96-B.json` written by a detached nohup has no
        notification at all — this is exactly why the guard must not key on
        `result.json` (#119 constraint 1)."""
        loop = ('for i in $(seq 1 37); do\n'
                '  if [ -f /tmp/ab94/result-96-B.json ]; then break; fi\n'
                '  sleep 15\n'
                'done')
        err = self._blocked_stderr(loop)
        self.assertNotIn(self.TASK_CLAIM, err)
        self.assertIn(self.GENERIC_CLAIM, err)

    def test_no_branch_ever_hands_out_118s_ci_waiter(self):
        for cmd in (task_loop(), obs_loop()):
            err = self._blocked_stderr(cmd, session="s-" + cmd[:12])
            self.assertNotIn("gh run view", err,
                             "this is not a CI wait; #118's command is wrong "
                             "advice here")

    def test_a_subagent_is_never_offered_a_background_waiter(self):
        for cmd in (task_loop(), obs_loop()):
            err = self._blocked_stderr(cmd, agent_id="a1b2c3",
                                       session="sub-" + cmd[:12])
            self.assertNotIn("run_in_background", err,
                             "a subagent that backgrounds a wait TERMINATES")
            self.assertIn("timeout", err)

    def test_a_subagent_is_never_told_to_end_its_turn_on_a_pending_task(self):
        err = self._blocked_stderr(task_loop(), agent_id="a1b2c3")
        self.assertNotIn(MessageBranchesByWhatIsWaitedOnTest.TASK_CLAIM, err)
        self.assertNotIn("end your turn and let", err,
                         "a subagent that ends its turn on a pending task "
                         "TERMINATES — the main-context advice is fatal here")
        self.assertIn("do NOT end your turn", err)


class LoggingTest(_Runner):
    def test_every_block_is_logged(self):
        self.run_hook(obs_loop())
        self.run_hook(obs_loop())
        log = Path(self.state) / "airuleset-localpoll-block.log"
        self.assertTrue(log.exists(), "every block must be reviewable later")
        self.assertIn("sess-119", log.read_text())

    def test_inline_bypass_is_honoured_and_logged(self):
        self.run_hook(obs_loop())
        cmd = obs_loop() + "  # airuleset:poll-ok fanning 9 repos, not a wait"
        self.assertEqual(self.run_hook(cmd).returncode, 0)
        log = Path(self.state) / "airuleset-localpoll-bypass.log"
        self.assertTrue(log.exists())
        self.assertIn("fanning 9 repos", log.read_text())

    def test_exemptions_are_logged_for_corpus_review(self):
        self.run_hook("git push origin main && " + obs_loop())
        log = Path(self.state) / "airuleset-localpoll-exempt.log"
        self.assertTrue(log.exists())
        self.assertIn("mutating", log.read_text())


class TeethTest(_Runner):
    """The assertions above can only be trusted if they FAIL on the tempting
    wrong fixes. Each mutant is built from the REAL shipped script, so a
    mutation that stops applying fails loudly rather than certifying nothing."""

    def _mutant(self, transform):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        (d / "hooks").mkdir()
        for f in HOOKS.glob("*.sh"):
            shutil.copy2(f, d / "hooks" / f.name)
        p = d / "hooks" / HOOK.name
        src = HOOK.read_text()
        out = transform(src)
        self.assertNotEqual(out, src, "the mutation did not apply to the "
                                      "shipped script — this certifies nothing")
        p.write_text(out)
        return p

    def test_a_guard_with_no_free_first_loop_blocks_every_sleep_loop(self):
        """'block all sleep loops' — the obvious wrong fix the ticket names."""
        mut = self._mutant(lambda s: s.replace(
            'if [ ! -e "$FIRST_FILE" ]; then', "if false; then", 1))
        self.assertEqual(self.run_hook(obs_loop()).returncode, 0,
                         "the shipped hook lets loop 1 through")
        out = self.run_hook(obs_loop(), hook=mut, session="mut1")
        self.assertEqual(out.returncode, 2,
                         "the capping mutant must block the very first loop — "
                         "proving the carve-out is what allows it")

    def test_dropping_the_ci_exemption_makes_this_hook_fight_118(self):
        mut = self._mutant(lambda s: s.replace(
            "exit 0   # airuleset:ci-owned-by-118", ":", 1))
        for _ in range(3):
            self.assertEqual(self.run_hook(ci_loop()).returncode, 0)
        self.run_hook(ci_loop(), hook=mut, session="mut2")
        out = self.run_hook(ci_loop(), hook=mut, session="mut2")
        self.assertEqual(out.returncode, 2,
                         "without the exemption both hooks block one command")

    def test_a_single_message_for_both_branches_fails_the_branch_assertions(self):
        """'one message fits all' — the failure mode constraint 2 forbids."""
        mut = self._mutant(lambda s: s.replace(
            'if [ "$WAIT_KIND" = "task" ]', "if true", 1))
        probe = MessageBranchesByWhatIsWaitedOnTest(
            "test_a_local_log_wait_is_NOT_told_a_notification_is_coming")
        probe.setUp()
        self.addCleanup(probe._tmp.cleanup)
        probe.run_hook(obs_loop(), hook=mut)
        err = probe.run_hook(obs_loop(), hook=mut).stderr
        self.assertIn(MessageBranchesByWhatIsWaitedOnTest.TASK_CLAIM, err,
                      "the single-message mutant hands the log wait the task "
                      "branch — which the shipped hook must not")


class FailOpenTest(_Runner):
    def test_unparseable_payload_fails_open(self):
        env = dict(os.environ)
        env["AIRULESET_LOCALPOLL_STATE_DIR"] = self.state
        out = subprocess.run(["bash", str(HOOK)], input="not json at all",
                             text=True, env=env, capture_output=True,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_a_command_with_no_loop_is_never_blocked(self):
        for _ in range(4):
            self.assertEqual(self.run_hook("ls -la /tmp").returncode, 0)


if __name__ == "__main__":
    unittest.main()
