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
import time
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


class CorpusFoundWrongBlockTest(unittest.TestCase):
    """Two wrong-block classes the corpus replay exposed (#118, 863 blocks over
    250,201 real commands). A hook that wrongly blocks stops real work, and on
    these boxes the working tree is production — so both are narrowed out.

    1. COMPOUND commands: 29 of the 863 carried a mutating action with the wait
       merely bolted on the tail (`gh pr merge 312 --merge && ... until run=...;
       do sleep 15; done`). Blocking one blocks the MERGE, not a poll. Such a
       command is also structurally not the burn shape — you merge once, then
       the repeat polls that follow are pure waits and are still caught.
    2. GENERIC-bucket SHORT waits: 31 were `gh run list`-driven "wait for the
       new run to APPEAR" loops (sleep 8-15, seconds to a minute). With no
       run-id there is nothing to prove two such loops are the SAME wait, and
       five consecutive ones in one restreamer session were five different PRs.
       A run-KEYED repeat stays blocked at any sleep interval, because the id
       proves it is the same run.
    """

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

    # verbatim corpus specimens (restreamer session 8125adb8, 101 blocks)
    MERGE_THEN_WAIT = (
        'gh pr merge %d --merge && gh pr view %d --json state,mergeCommit '
        '--jq \'{state, sha: .mergeCommit.oid[0:8]}\' && until '
        'run=$(gh run list -b main -L 1 --json databaseId,headSha '
        '--jq \'.[0].databaseId\') && [ -n "$run" ]; do sleep 15; done; '
        'echo "main run: $run"')
    APPEAR_LOOP = (
        'until rid=$(gh run list --branch dev --limit 3 --json '
        'headSha,databaseId -q \'.[0].databaseId\'); [ -n "$rid" ]; do '
        'sleep 10; done; echo "$rid"')

    def test_merging_two_prs_in_one_session_is_never_blocked(self):
        for pr in (312, 313, 314):
            out = self.run_hook(self.MERGE_THEN_WAIT % (pr, pr))
            self.assertEqual(out.returncode, 0,
                             "blocking this would block the MERGE: " + out.stderr)

    def test_a_mutating_command_is_exempt_even_after_a_block(self):
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)
        # SAME run-id as the blocked key, so this can only pass via the
        # mutating-action exemption — never because the key happens to differ
        out = self.run_hook(
            'git push origin dev && until [ "$(gh run view %s --json status '
            '--jq .status)" = "completed" ]; do sleep 30; done' % RUN_A)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_exemptions_are_logged_for_corpus_review(self):
        self.run_hook(self.MERGE_THEN_WAIT % (312, 312))
        self.run_hook(self.MERGE_THEN_WAIT % (313, 313))
        log = Path(self.state) / "airuleset-cipoll-exempt.log"
        self.assertTrue(log.exists(), "an exemption must be reviewable later")
        self.assertIn("mutating", log.read_text())

    def test_generic_short_appear_loops_are_not_blocked(self):
        for _ in range(4):
            out = self.run_hook(self.APPEAR_LOOP)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_generic_LONG_poll_loop_still_blocks_on_repeat(self):
        longer = ('for i in $(seq 1 20); do s=$(gh run list --branch dev '
                  '--limit 3 --json status --jq \'.[0].status\'); '
                  'case "$s" in completed*) break;; esac; sleep 60; done')
        self.assertEqual(self.run_hook(longer).returncode, 0)
        self.assertEqual(self.run_hook(longer).returncode, 2)

    def test_run_keyed_repeat_blocks_at_ANY_sleep_interval(self):
        # the id proves it is the same run — a short sleep is no defence
        short = ('until [ "$(gh run view %s --json status --jq .status)" = '
                 '"completed" ]; do sleep 10; done' % RUN_A)
        self.assertEqual(self.run_hook(short).returncode, 0)
        self.assertEqual(self.run_hook(short).returncode, 2)

    # --- backstop narrowing: a post-mortem diagnostic is not a wait --------
    # Stage-2 replay (all 56,038 commands of the 185 blocked sessions): the
    # backstop fired 139 times, but only 27 via a long sleep. The other 112
    # qualified ONLY through "two or more `gh run view` in one command" — and
    # every one of those is a post-mortem (`view --json jobs --jq failure`
    # then `view --log-failed`), i.e. reading WHY CI failed. Blocking that
    # stops the debugging, so the density criterion is dropped entirely and
    # the backstop now keys on a long sleep alone.
    DIAGNOSTIC = (
        'gh run view %s --json jobs -q \'.jobs[] | select(.conclusion=="failure") '
        '| .steps[] | select(.conclusion=="failure") | .name\' 2>&1\n'
        'echo "---log tail---"\n'
        'gh run view %s --log-failed 2>&1 | tail -30')

    def test_post_mortem_diagnostics_are_never_blocked(self):
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)
        out = self.run_hook(self.DIAGNOSTIC % (RUN_A, RUN_A))
        self.assertEqual(out.returncode, 0,
                         "reading WHY CI failed is the work, not a poll")

    def test_a_long_sleep_before_a_view_still_blocks_after_a_block(self):
        self.run_hook(poll_loop(RUN_A))
        self.assertEqual(self.run_hook(poll_loop(RUN_A)).returncode, 2)
        out = self.run_hook('sleep 300 && gh run view %s --json status,'
                            'conclusion,jobs 2>&1 | tail -5' % RUN_A)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)


class Issue127CiSideGapTest(unittest.TestCase):
    """#127: `gh pr view <N> --json …statusCheckRollup…` loops carry no
    `gh run view|watch|list` / `gh pr checks` token, so they must NEVER match
    this hook's CI-wait signature — that boundary is deliberate (#118 owns a
    narrow, run-id-keyed token set), and #119's sibling hook already covers
    the gap correctly (see the paired test in test_local_poll_repeat_block.py).

    Measured (2026-07-29, 8,231 transcripts / 258,724 commands): 52 such
    loops exist in the corpus, 14 of which repeat and are already blocked —
    by #119, not here. Tested empirically that WIDENING this hook's signature
    to swallow the shape is the wrong fix, not merely asserted: PR numbers in
    the corpus never reach the 8-digit RUN_ID floor, so every one would fall
    into this hook's `generic` bucket, whose compliant command is `RID=$(gh
    run list -L 1 --json databaseId …)` — the single most recent run in the
    WHOLE repo, not the run behind the polled PR. That is a wrong-run waiter,
    strictly worse than #119's honestly-scoped generic message. The generic
    bucket's 1800s TTL would also intermittently let these (naturally
    slower-cadence) `gh pr view` waits reset to "first loop free" and stop
    blocking real repeats — #119's per-(session, shape) key never decays.
    Decision recorded on #127: the split stands, #118's signature stays
    exactly as-is.
    """

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

    # verbatim shape from the ticket's own specimen (camera-box, poll #436-444)
    PR_VIEW_LOOP = (
        'for i in 1 2 3; do\n'
        '  r=$(gh pr view %d --json mergeable,mergeStateStatus,'
        'statusCheckRollup --jq \'.\')\n'
        '  echo "$r"\n'
        '  sleep 250\n'
        'done')

    def test_a_gh_pr_view_statuscheckrollup_loop_never_matches_here(self):
        for _ in range(4):
            out = self.run_hook(self.PR_VIEW_LOOP % 436)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_still_unmatched_even_after_many_repeats_of_the_same_pr(self):
        cmd = self.PR_VIEW_LOOP % 704
        for _ in range(6):
            out = self.run_hook(cmd)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout, "")


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


class JobLevelFailFastTest(unittest.TestCase):
    """#405: the canonical waiter now requests `,jobs` alongside
    `status,conclusion` and branches TERMINAL/JOBFAIL/PENDING in its own
    `--jq` filter — this must NOT change how #118's digit-blind loop
    detector classifies a repeated poll (the extra JSON field is content,
    not shape), and the printed compliant background waiter (both the
    loop-repeat MAINMSG and the oneshot-repeat ONESHOT_MAINMSG) must carry
    the SAME job-fail-fast branch as the canonical doc snippet — an
    inconsistent copy is exactly the rot #405 was filed to prevent."""

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

    def job_aware_poll_loop(self, run_id):
        # the SAME shape poll_loop() builds, except the --json value also
        # carries ,jobs and the --jq filter is the new 3-way branch — proves
        # the loop-repeat detector still recognises this as the identical
        # loop SHAPE (do...sleep...done), regardless of the JSON field list.
        return (
            'DEADLINE=$((SECONDS + ${AIRULESET_POLL_BUDGET_S:-540}))\n'
            'for i in $(seq 1 18); do\n'
            "  s=$(gh run view %s --json status,conclusion,jobs --jq "
            "'if .status==\"completed\" then \"TERMINAL \"+.status else "
            '"PENDING" end\')\n'
            '  case "$s" in "TERMINAL "*) break;; esac\n'
            '  sleep 30\n'
            'done' % run_id
        )

    def test_job_aware_loop_still_gets_its_free_first_pass(self):
        out = self.run_hook(self.job_aware_poll_loop(RUN_A))
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_job_aware_loop_repeat_is_still_blocked_same_as_plain(self):
        self.run_hook(self.job_aware_poll_loop(RUN_A))
        out = self.run_hook(self.job_aware_poll_loop(RUN_A))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#118", out.stderr)

    def test_loop_repeat_block_message_carries_the_jobfail_branch(self):
        # MAINMSG's printed background waiter (the compliant command handed
        # to the model after a loop-repeat block) must stay in sync with
        # ci-monitoring.md's own canonical shape.
        self.run_hook(poll_loop(RUN_A))
        err = self.run_hook(poll_loop(RUN_A)).stderr
        self.assertIn("JOBFAIL", err)
        self.assertIn("JOB FAILED (run still in progress)", err)
        self.assertIn(",jobs", err)

    def test_oneshot_repeat_block_message_carries_the_jobfail_branch(self):
        # ONESHOT_MAINMSG prints BOTH the foreground loop and the background
        # waiter — both copies must carry the same branch.
        cmd = "gh run view %s --json status,conclusion" % RUN_B
        self.run_hook(cmd)
        self.run_hook(cmd)
        err = self.run_hook(cmd).stderr
        self.assertIn("JOBFAIL", err)
        self.assertIn("JOB FAILED (run still in progress)", err)
        self.assertIn(",jobs", err)


class OneShotStatusPollBlockTest(unittest.TestCase):
    """#210: a bare, non-loop `gh run view <run-id>` status poll — no sleep,
    no `do...done` — is invisible to #118's loop detector, but production
    transcripts show workers doing hundreds of them, one per TURN (one real
    dispatch: 157 `gh run view` calls, 157 non-loop, one run polled across 35
    separate turns). Each one-shot re-sends the whole accumulated context for
    a single line of status, same burn shape as the repeat LOOP #118 already
    blocks — just without a `sleep` in the same Bash call.

    Decided shape: the 1st and 2nd one-shot status poll per (session, run-id)
    stay ALLOWED (a worker legitimately checks once, then again a moment
    later); the 3rd+ is BLOCKED, same as the loop mechanism's own carve-out
    philosophy but with a 2-free budget instead of 1, since a bare one-shot
    carries no evidence by itself that the wait is long.

    Only a STATUS-POLL shape counts: `--json status`, `--json
    status,conclusion` (either order), or a fully bare `gh run view <id>`
    with no `--json` at all. `--log` / `--log-failed` (reading WHY a run
    failed) and any `--json` value naming a field other than status/
    conclusion (`jobs`, etc.) are never counted — that is the actual
    debugging work, not a wasted poll. A command with TWO `gh run view`
    invocations (the post-mortem shape: one to inspect failed jobs, one for
    `--log-failed`) is never counted either, regardless of repeats.
    """

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

    def oneshot(self, run_id, json_val="status,conclusion"):
        if json_val is None:
            return "gh run view %s" % run_id
        return "gh run view %s --json %s" % (run_id, json_val)

    # ---- the carve-out: the first two one-shots per (session, run) --------
    def test_first_oneshot_status_poll_is_allowed(self):
        out = self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr.strip(), "")

    def test_second_oneshot_status_poll_is_still_allowed(self):
        self.run_hook(self.oneshot(RUN_A))
        out = self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_third_oneshot_status_poll_is_blocked(self):
        self.run_hook(self.oneshot(RUN_A))
        self.run_hook(self.oneshot(RUN_A))
        out = self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#210", out.stderr)

    def test_fourth_and_later_oneshots_stay_blocked(self):
        for _ in range(3):
            self.run_hook(self.oneshot(RUN_A))
        out = self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_bare_view_with_no_json_flag_counts_as_a_status_poll(self):
        # ci-monitoring.md's own "ONE plain status check" carve-out is a bare
        # `gh run view <id>` with no --json at all — still a status-poll
        # shape, still counted once it repeats past the free budget.
        self.run_hook(self.oneshot(RUN_A, json_val=None))
        self.run_hook(self.oneshot(RUN_A, json_val=None))
        out = self.run_hook(self.oneshot(RUN_A, json_val=None))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_json_status_alone_and_conclusion_first_both_count(self):
        self.run_hook(self.oneshot(RUN_A, json_val="status"))
        self.run_hook(self.oneshot(RUN_A, json_val="conclusion,status"))
        out = self.run_hook(self.oneshot(RUN_A, json_val="status,conclusion"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_status_conclusion_jobs_still_counts_as_a_status_poll(self):
        # #405 made `status,conclusion,jobs` the CANONICAL one-shot field
        # list (ci-monitoring.md's own snippets, and this hook's own printed
        # "compliant" templates) — a repeated one-shot in that exact shape is
        # still just the same "re-send the whole context for one status
        # line" burn #210 exists to throttle; the extra `,jobs` field must
        # not exempt it from the count the way a genuine `--json jobs`-only
        # debugging read (test_json_jobs_field_reads_are_never_counted,
        # below) correctly stays exempt.
        self.run_hook(self.oneshot(RUN_A, json_val="status,conclusion,jobs"))
        self.run_hook(self.oneshot(RUN_A, json_val="status,conclusion,jobs"))
        out = self.run_hook(self.oneshot(RUN_A, json_val="status,conclusion,jobs"))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#210", out.stderr)

    # ---- state keying, mirrors the loop mechanism -------------------------
    def test_a_second_run_id_gets_its_own_free_first_two(self):
        for _ in range(3):
            self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(self.run_hook(self.oneshot(RUN_A)).returncode, 2)
        out = self.run_hook(self.oneshot(RUN_B))
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_hook(self.oneshot(RUN_B))
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_hook(self.oneshot(RUN_B))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)

    def test_state_is_per_session(self):
        for _ in range(3):
            self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(self.run_hook(self.oneshot(RUN_A)).returncode, 2)
        out = self.run_hook(self.oneshot(RUN_A), session="other-session-210")
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- what must NEVER count as a one-shot poll --------------------------
    def test_log_failed_reads_are_never_counted_however_often_repeated(self):
        cmd = "gh run view %s --log-failed | tail -50" % RUN_A
        for _ in range(5):
            out = self.run_hook(cmd)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_plain_log_reads_are_never_counted(self):
        cmd = "gh run view %s --log" % RUN_A
        for _ in range(5):
            self.assertEqual(self.run_hook(cmd).returncode, 0)

    def test_json_jobs_field_reads_are_never_counted(self):
        # not a status-poll shape — inspecting WHY a run failed, not waiting
        cmd = "gh run view %s --json jobs -q '.jobs[]'" % RUN_A
        for _ in range(5):
            self.assertEqual(self.run_hook(cmd).returncode, 0)

    def test_two_view_invocations_in_one_command_is_never_counted(self):
        # the #118 post-mortem shape: inspect the failed job, then the log.
        # Neither call alone repeats, and the pair must never trip the
        # one-shot counter no matter how many times it is issued.
        cmd = (
            "gh run view %s --json jobs -q 'select(.conclusion==\"failure\")' "
            "2>&1\ngh run view %s --log-failed 2>&1 | tail -30" % (RUN_A, RUN_A))
        for _ in range(4):
            out = self.run_hook(cmd)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_loop_shaped_poll_is_governed_by_the_loop_mechanism_not_this_one(self):
        # a real `do...sleep...done` loop must keep working exactly as #118
        # already specifies — this ticket must not perturb that mechanism.
        loop = poll_loop(RUN_A)
        self.assertEqual(self.run_hook(loop).returncode, 0)
        out = self.run_hook(loop)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#118", out.stderr)

    def test_a_mutating_command_is_never_counted_even_with_a_view_tail(self):
        cmd = ('gh pr merge 312 --merge && gh run view %s '
               '--json status,conclusion' % RUN_A)
        for _ in range(4):
            self.assertEqual(self.run_hook(cmd).returncode, 0)

    def test_background_waiter_is_never_counted(self):
        waiter = ('timeout "${AIRULESET_LONG_POLL_BUDGET_S:-10800}" bash -c '
                  '\'while :; do s=$(gh run view %s --json status,conclusion) '
                  '|| s="ERROR"; case "$s" in completed*) exit 0 ;; esac; '
                  'sleep 60; done\'' % RUN_A)
        for _ in range(4):
            out = self.run_hook(waiter, background=True)
            self.assertEqual(out.returncode, 0, out.stderr)

    # ---- the block message IS the fix --------------------------------------
    def test_block_message_carries_the_run_id_and_both_wait_shapes(self):
        self.run_hook(self.oneshot(RUN_A))
        self.run_hook(self.oneshot(RUN_A))
        err = self.run_hook(self.oneshot(RUN_A)).stderr
        self.assertIn(RUN_A, err)
        self.assertIn("run_in_background", err)
        self.assertIn("AIRULESET_LONG_POLL_BUDGET_S", err)
        self.assertIn("AIRULESET_POLL_BUDGET_S", err)
        self.assertIn("SUBAGENT", err)
        self.assertIn("RETURN", err)

    def test_subagent_block_never_offers_a_background_waiter(self):
        kw = {"agent_id": "a2afddb67fb83f7c7210"}
        self.run_hook(self.oneshot(RUN_A), **kw)
        self.run_hook(self.oneshot(RUN_A), **kw)
        out = self.run_hook(self.oneshot(RUN_A), **kw)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertNotIn(
            "run_in_background", out.stderr,
            "a subagent that backgrounds a wait TERMINATES — never offer it")
        self.assertIn("supervisor", out.stderr)
        self.assertIn("RETURN", out.stderr)
        self.assertIn(RUN_A, out.stderr)

    # ---- corpus logging: the one-shot block is a real block ---------------
    def test_every_oneshot_block_is_logged(self):
        self.run_hook(self.oneshot(RUN_A))
        self.run_hook(self.oneshot(RUN_A))
        self.run_hook(self.oneshot(RUN_A))
        log = Path(self.state) / "airuleset-cipoll-block.log"
        self.assertTrue(log.exists(), "every block must be logged")
        body = log.read_text()
        self.assertIn(RUN_A, body)

    def test_still_allowed_after_a_oneshot_block_a_single_plain_status_check(self):
        # the hook must never dead-end a session: `--log-failed` still works
        # even once the run-id's one-shot budget is exhausted.
        for _ in range(3):
            self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(self.run_hook(self.oneshot(RUN_A)).returncode, 2)
        out = self.run_hook("gh run view %s --log-failed | tail -50" % RUN_A)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_inline_bypass_is_honoured_and_logged(self):
        self.run_hook(self.oneshot(RUN_A))
        self.run_hook(self.oneshot(RUN_A))
        cmd = self.oneshot(RUN_A) + "  # airuleset:poll-ok fanning 9 repos, not a wait"
        out = self.run_hook(cmd)
        self.assertEqual(out.returncode, 0, out.stderr)
        log = Path(self.state) / "airuleset-cipoll-bypass.log"
        self.assertTrue(log.exists(), "bypass must be logged for corpus review")
        self.assertIn("fanning 9 repos", log.read_text())


class OneShotReviewFollowupTest(unittest.TestCase):
    """Review follow-up on the #210 one-shot extension (post-cf5f4cd/76d8e9c).

    Two 🔴 findings from the adversarial review of #210:

    1. DEAD END: once the 3rd+ oneshot blocks a (session, run-id), the exact
       command the block message prints as the permanent escape (`gh run view
       <id> --json status,conclusion`) is blocked again on EVERY subsequent
       call, forever — no decay. Contradicts the file's own invariant that no
       plain status check ever dead-ends. Fix: a sliding TTL window on the
       oneshot counter, same mtime-based decay style the generic loop bucket
       already uses.
    2. The oneshot block writes the SHARED BLOCKED_FILE, and the #118
       "loop 1 is free" carve-out gates only on that file's existence,
       agnostic of WHY it was set — so 3 oneshots (3rd blocks) permanently
       poison the free-first-loop carve-out for that run-id, even though no
       loop ever ran. Fix: the oneshot mechanism gets its OWN state; only an
       actual LOOP (or burst) block may consume the loop carve-out.

    Plus the 🔵 closed in the same pass: a bare `gh run view --json
    status,conclusion` with NO run-id argument (defaults to the latest run on
    the current branch) previously evaded the oneshot counter entirely, since
    the whole counting block was gated on `-n "$RUN_ID"`.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def run_hook(self, command, extra_env=None, **kw):
        env = dict(os.environ)
        env["AIRULESET_CIPOLL_STATE_DIR"] = self.state
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(HOOK)],
            input=payload(command, **kw), text=True, env=env,
            capture_output=True, timeout=30)

    def oneshot(self, run_id, json_val="status,conclusion"):
        if json_val is None:
            return "gh run view %s" % run_id
        return "gh run view %s --json %s" % (run_id, json_val)

    # ---- 🔴 1: no dead end — the promised escape must decay, not vanish ----
    def test_the_promised_escape_is_blocked_forever_without_decay(self):
        # Live-verified sequence from the review: calls 1,2 allowed; 3,4,5,6
        # all blocked. This locks the CURRENT (broken) shape so a future
        # change to the free budget doesn't silently un-notice a regression
        # of the SAME dead-end class reappearing at a different count.
        results = [self.run_hook(self.oneshot(RUN_A)).returncode for _ in range(6)]
        self.assertEqual(results, [0, 0, 2, 2, 2, 2])

    def test_oneshot_dead_end_decays_once_the_ttl_genuinely_passes(self):
        for _ in range(3):
            self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(self.run_hook(self.oneshot(RUN_A)).returncode, 2)

        oneshot_file = Path(self.state) / (
            "airuleset-cipoll-oneshot-sess-118-run-%s" % RUN_A)
        self.assertTrue(
            oneshot_file.exists(),
            "the oneshot mechanism must persist its own per-key state file")
        old = os.path.getmtime(oneshot_file) - 1900  # past the default 1800s TTL
        os.utime(oneshot_file, (old, old))

        out = self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(
            out.returncode, 0, out.stderr +
            " — the window genuinely passed, so this must be a fresh count")

    def test_oneshot_ttl_is_configurable_and_short_ttls_decay_fast(self):
        # #226: a literal 1s TTL races real subprocess-spawn overhead — 3
        # setup calls (each a genuine bash+hook subprocess) plus the
        # verification call can, under real machine load, cumulatively
        # exceed 1 real second, making the LAST touch see an already-stale
        # oneshot file and decay early. Fixed by giving the accumulate phase
        # a TTL with genuine headroom (30s) over realistic subprocess
        # overhead — proven with a forced ~1.2s delay below (comfortably
        # more than the ORIGINAL literal 1s TTL this test used to race, and
        # comfortably less than the 30s TTL now configured), rather than by
        # hoping the box stays idle. The "short TTL DOES decay" claim stays
        # fully deterministic — via os.utime backdating, never real elapsed
        # wall-clock time — using the CONFIGURED (30s) boundary, not the
        # 1800s default.
        env = {"AIRULESET_CIPOLL_ONESHOT_TTL_S": "30"}
        for _ in range(3):
            self.run_hook(self.oneshot(RUN_A), extra_env=env)
        time.sleep(1.2)
        self.assertEqual(
            self.run_hook(self.oneshot(RUN_A), extra_env=env).returncode, 2)

        oneshot_file = Path(self.state) / (
            "airuleset-cipoll-oneshot-sess-118-run-%s" % RUN_A)
        old = os.path.getmtime(oneshot_file) - 31   # past the configured 30s TTL
        os.utime(oneshot_file, (old, old))

        out = self.run_hook(self.oneshot(RUN_A), extra_env=env)
        self.assertEqual(out.returncode, 0, out.stderr)

    # ---- 🔴 2: oneshot must never consume the #118 loop free-carve-out ----
    def test_oneshot_block_never_consumes_the_loop_free_carveout(self):
        for _ in range(3):
            self.run_hook(self.oneshot(RUN_A))
        self.assertEqual(self.run_hook(self.oneshot(RUN_A)).returncode, 2)

        # a genuine loop-shaped poll for the SAME run-id must still get its
        # free first pass — no loop has run yet, only oneshots
        out = self.run_hook(poll_loop(RUN_A))
        self.assertEqual(
            out.returncode, 0, out.stderr +
            " — an oneshot block must never poison the #118 loop carve-out")
        self.assertNotIn("Loop 1 already came back non-terminal", out.stderr)

        # and its behavior from here is unchanged from a fresh key: the loop
        # mechanism's OWN second call is what blocks it, with the #118 message
        out = self.run_hook(poll_loop(RUN_A))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#118", out.stderr)
        self.assertIn("Loop 1 already came back non-terminal", out.stderr)

    # ---- 🔵: a bare no-run-id status poll must be counted somewhere -------
    def test_bare_no_run_id_status_poll_is_counted_and_eventually_blocked(self):
        cmd = "gh run view --json status,conclusion"
        self.assertEqual(self.run_hook(cmd).returncode, 0)
        self.assertEqual(self.run_hook(cmd).returncode, 0)
        out = self.run_hook(cmd)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("#210", out.stderr)

    def test_bare_no_run_id_gh_run_list_is_still_never_counted_as_oneshot(self):
        # `gh run list` (no `view`) must stay completely outside the oneshot
        # mechanism — only the loop mechanism's own generic bucket governs it.
        cmd = "gh run list --limit 3 --json databaseId,status"
        for _ in range(5):
            self.assertEqual(self.run_hook(cmd).returncode, 0)


if __name__ == "__main__":
    unittest.main()
