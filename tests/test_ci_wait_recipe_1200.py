"""airuleset #1200: the CI wait recipes read cheap status on every poll and
the heavy jobs list only every 3rd poll and at completion; a gh failure is a
retry, never a result.

Owner question (30.9.2026, verbatim): "a je to tak spravne ze sa zistuje stav
vsetkych jobov naraz?" All three recipe shapes in
``skills/ci-monitoring-deep/DEEP.md`` (the foreground loop, the background
waiter and the #588 deploy watch) called
``gh run view <id> --json status,conclusion,jobs`` on EVERY poll. ``jobs`` is
the heaviest run payload: every step of every job, 33 jobs on songplayer.
GitHub intermittently answers it with HTTP 502. That was seen live on dev1:
``HTTP 502 ... /actions/runs/36719843757/jobs?per_page=100`` after 11.6 s,
and the next call succeeded.

The decided shape (Approach 1 on the ticket):

- every poll runs only ``gh run view <id> --json status,conclusion``;
- ``--json jobs`` (the JOBFAIL fail-fast) runs when ``i % 3 == 0`` and when
  the run is completed;
- a non-zero gh exit is "retry next poll" in every shape.

These tests EXECUTE the recipes extracted from the doc against a stub ``gh``
that logs every call, so they pin behaviour, not wording. They also
``bash -n`` every block and feed the real ``--jq`` filters to a real ``jq``.
They were moved here from ``tests/test_airuleset.py``
(``TestCiMonitoringPollSnippetSelfBounds`` and
``TestCiMonitoringJqFilterHasRealTeeth``, #90/#405), which pinned the old
one-call shape.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402
from test_fork_no_merge_close_guard import (  # noqa: E402
    _cwd_with_authority, run as run_close_guard)

DEEP = REPO / "skills" / "ci-monitoring-deep" / "DEEP.md"
POLL_HOOK = REPO / "hooks" / "block-ci-poll-repeat.sh"
RUN_ID = "30326991380"
OUTER_KILLED = -9999  # the harness had to kill the recipe itself

FOREGROUND_MARKER = "DEADLINE="
BACKGROUND_MARKER = "AIRULESET_LONG_POLL_BUDGET_S"
DEPLOY_MARKER = "DEPLOY_JOB_RE="

# A stub `gh`: logs each call's --json value (GH_LOG) and its --jq filter
# (JQ_LOG), then answers from a `|`-separated script per call kind. FAIL
# prints a 502 on stderr and exits 1 (the live failure). EMPTY prints an
# empty line. The last item repeats.
_GH_STUB = r"""#!/usr/bin/env bash
if [ "${1-}" = "run" ] && [ "${2-}" = "list" ]; then echo "${RUN_LIST_ID-}"; exit 0; fi
printf '%s\n' "$*" >> "$GH_LOG.args"
json=""; jq=""; prev=""
for a in "$@"; do
  [ "$prev" = "--json" ] && json="$a"
  [ "$prev" = "--jq" ] && jq="$a"
  prev="$a"
done
printf '%s\n' "$json" >> "$GH_LOG"
[ -n "$jq" ] && printf '%s\t%s\n' "$json" "$jq" >> "$JQ_LOG"
case "$json" in
  status,conclusion) seq="${STATUS_SEQ-}"; kind=status ;;
  jobs) seq="${JOBS_SEQ-}"; kind=jobs ;;
  *) seq="${OTHER_SEQ-}"; kind=other ;;
esac
cnt="$GH_LOG.$kind"
n=$(( $(cat "$cnt" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$cnt"
IFS='|' read -r -a items <<<"$seq"
[ "${#items[@]}" -eq 0 ] && { echo; exit 0; }
idx=$(( n - 1 ))
[ "$idx" -ge "${#items[@]}" ] && idx=$(( ${#items[@]} - 1 ))
item="${items[$idx]}"
if [ "$item" = "FAIL" ]; then
  echo "HTTP 502: Bad Gateway (https://api.github.com/repos/o/r/actions/runs/1/jobs?per_page=100)" >&2
  exit 1
fi
[ "$item" = "EMPTY" ] && item=""
printf '%s\n' "$item"
"""


def deep_blocks():
    """The fenced code blocks of DEEP.md, fence language tag stripped."""
    parts = DEEP.read_text(encoding="utf-8").split("```")
    blocks = []
    for i in range(1, len(parts), 2):
        code = parts[i]
        if code.startswith("bash\n"):
            code = code[len("bash\n"):]
        blocks.append(code.strip("\n"))
    return blocks


def deep_block(marker):
    matches = [b for b in deep_blocks() if marker in b]
    assert len(matches) == 1, (
        "expected exactly one fenced block containing %r in DEEP.md, found %d"
        % (marker, len(matches)))
    return matches[0]


def background_inner_body(block):
    """The script the background waiter hands to `bash -c '...'`."""
    start = block.index("bash -c '") + len("bash -c '")
    end = block.rindex("'")
    return block[start:end]


class StubGh:
    """A temp dir with the stub `gh` first on PATH, plus its call logs."""

    def __init__(self, testcase):
        self.root = tempfile.mkdtemp()
        testcase.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        binroot = os.path.join(self.root, "bin")
        os.makedirs(binroot)
        gh = os.path.join(binroot, "gh")
        with open(gh, "w") as fh:
            fh.write(_GH_STUB)
        os.chmod(gh, 0o755)
        self.gh_log = os.path.join(self.root, "gh.log")
        self.jq_log = os.path.join(self.root, "jq.log")
        self.env = dict(os.environ)
        self.env["PATH"] = binroot + os.pathsep + self.env.get("PATH", "")
        self.env["GH_LOG"] = self.gh_log
        self.env["JQ_LOG"] = self.jq_log

    def run(self, script, timeout=20, strict=False, **seqs):
        """Run `script` in its own SESSION with output to files; on the outer
        timeout kill every process of that session and return OUTER_KILLED.
        (GNU `timeout` moves its child into a new process GROUP, so a group
        kill would miss the background waiter, and a captured pipe it holds
        open would hang the test forever.)"""
        env = dict(self.env)
        env.update(seqs)
        out_path = os.path.join(self.root, "stdout")
        err_path = os.path.join(self.root, "stderr")
        with open(out_path, "w") as out_fh, open(err_path, "w") as err_fh:
            proc = subprocess.Popen(
                ["bash", "-c", ("set -euo pipefail\n" if strict
                                else "set -uo pipefail\n") + script],
                stdout=out_fh, stderr=err_fh, env=env, cwd=self.root,
                start_new_session=True)
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                subprocess.run(["pkill", "-KILL", "-s", str(proc.pid)],
                               check=False)
                proc.wait()
                rc = OUTER_KILLED
        with open(out_path) as fh_out, open(err_path) as fh_err:
            return subprocess.CompletedProcess(proc.args, rc, fh_out.read(),
                                               fh_err.read())

    def argv(self):
        """Every `gh run view` argv line, as the stub saw it."""
        path = self.gh_log + ".args"
        if not os.path.isfile(path):
            return []
        with open(path) as fh:
            return [line.rstrip("\n") for line in fh]

    def calls(self):
        if not os.path.isfile(self.gh_log):
            return []
        with open(self.gh_log) as fh:
            return [line.rstrip("\n") for line in fh]

    def filters(self):
        """{--json value: the set of --jq filters gh was handed for it}."""
        out = {}
        if os.path.isfile(self.jq_log):
            with open(self.jq_log) as fh:
                for line in fh:
                    json_val, _, flt = line.rstrip("\n").partition("\t")
                    out.setdefault(json_val, set()).add(flt)
        return out


def foreground(sleep="0.01", seq=None):
    snippet = deep_block(FOREGROUND_MARKER).replace("<id>", RUN_ID)
    snippet = snippet.replace("sleep 30", "sleep " + sleep)
    if seq is not None:
        snippet = snippet.replace("seq 1 18", "seq 1 %d" % seq)
    return snippet


def background():
    snippet = deep_block(BACKGROUND_MARKER).replace("<id>", RUN_ID)
    return snippet.replace("sleep 60", "sleep 0.01")


class TestRecipesAreValidBash(unittest.TestCase):
    """Every recipe must parse. A broken paste is a silent dead waiter."""

    def _bash_n(self, script, label):
        r = subprocess.run(["bash", "-n"], input=script, text=True,
                           capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0, "%s is not valid bash: %s\n%s"
                         % (label, r.stderr, script))

    def test_each_block_parses(self):
        for marker in (FOREGROUND_MARKER, BACKGROUND_MARKER, DEPLOY_MARKER):
            self._bash_n(deep_block(marker).replace("<id>", RUN_ID), marker)

    def test_background_inner_script_parses(self):
        body = background_inner_body(deep_block(BACKGROUND_MARKER))
        self.assertNotIn("'", body, "the bash -c body must not carry a `'`")
        self._bash_n(body.replace("<id>", RUN_ID), "background bash -c body")


class TestForegroundLoop(unittest.TestCase):
    """The short-wait foreground loop (#90 self-bound, #405 fail-fast)."""

    def setUp(self):
        self.gh = StubGh(self)

    def test_every_poll_reads_status_only_and_jobs_every_third_poll(self):
        r = self.gh.run(foreground(seq=6), STATUS_SEQ="in_progress ",
                        JOBS_SEQ="EMPTY",
                        AIRULESET_POLL_BUDGET_S="100")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.gh.calls()
        self.assertEqual(calls.count("status,conclusion"), 6, calls)
        self.assertEqual(calls.count("jobs"), 2, calls)
        self.assertEqual(len(calls), 8, "no other gh call shape: %r" % calls)

    def test_completion_reads_jobs_off_the_cadence(self):
        r = self.gh.run(foreground(), STATUS_SEQ="in_progress |completed failure",
                        JOBS_SEQ="JOBFAIL E2E",
                        AIRULESET_POLL_BUDGET_S="100")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TERMINAL: completed failure (JOBFAIL E2E)", r.stdout)
        self.assertNotIn("JOB FAILED", r.stdout)
        self.assertEqual(self.gh.calls(),
                         ["status,conclusion", "status,conclusion", "jobs"])

    def test_job_failure_wakes_on_the_third_poll(self):
        r = self.gh.run(foreground(), STATUS_SEQ="in_progress ",
                        JOBS_SEQ="JOBFAIL E2E Tests (slovnormal)",
                        AIRULESET_POLL_BUDGET_S="100")
        self.assertNotEqual(r.returncode, OUTER_KILLED, r.stdout + r.stderr)
        self.assertIn(
            "JOB FAILED (run still in progress): E2E Tests (slovnormal)",
            r.stdout)
        self.assertNotIn("TERMINAL", r.stdout)
        self.assertEqual(self.gh.calls().count("status,conclusion"), 3)

    def test_terminal_success_breaks_on_the_first_poll(self):
        r = self.gh.run(foreground(), STATUS_SEQ="completed success",
                        JOBS_SEQ="EMPTY", AIRULESET_POLL_BUDGET_S="100")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TERMINAL: completed success", r.stdout)
        self.assertNotIn("POLL BUDGET REACHED", r.stdout)
        self.assertNotIn("JOB FAILED", r.stdout)

    def test_gh_status_failure_is_a_retry_never_a_result(self):
        r = self.gh.run(foreground(), STATUS_SEQ="FAIL|FAIL|completed success",
                        JOBS_SEQ="EMPTY", AIRULESET_POLL_BUDGET_S="100")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TERMINAL: completed success", r.stdout)
        self.assertEqual(self.gh.calls().count("status,conclusion"), 3)

    def test_gh_jobs_failure_is_a_retry_never_a_result(self):
        r = self.gh.run(
            foreground(),
            STATUS_SEQ="in_progress |in_progress |in_progress |completed success",
            JOBS_SEQ="FAIL|EMPTY", AIRULESET_POLL_BUDGET_S="100")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TERMINAL: completed success", r.stdout)
        self.assertNotIn("JOB FAILED", r.stdout)
        self.assertEqual(self.gh.calls().count("jobs"), 2)

    def test_gh_failures_are_retries_under_set_e(self):
        # A session may paste the loop into a `set -e` shell: a gh failure
        # must still be a retry there, never an abort (#1200 review).
        r = self.gh.run(foreground(), strict=True,
                        STATUS_SEQ="FAIL|in_progress |in_progress |completed success",
                        JOBS_SEQ="FAIL|EMPTY", AIRULESET_POLL_BUDGET_S="100")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TERMINAL: completed success", r.stdout)

    def test_gh_down_the_whole_time_ends_on_the_budget(self):
        r = self.gh.run(foreground(sleep="0.2"), STATUS_SEQ="FAIL",
                        JOBS_SEQ="FAIL", AIRULESET_POLL_BUDGET_S="1")
        self.assertNotEqual(r.returncode, OUTER_KILLED,
                            "snippet did not self-bound: " + r.stdout + r.stderr)
        self.assertIn("POLL BUDGET REACHED", r.stdout)
        self.assertNotIn("TERMINAL", r.stdout)
        self.assertNotIn("JOB FAILED", r.stdout)

    def test_never_terminal_exits_on_its_own_budget(self):
        r = self.gh.run(foreground(sleep="0.2"), STATUS_SEQ="queued ",
                        JOBS_SEQ="EMPTY", AIRULESET_POLL_BUDGET_S="1")
        self.assertNotEqual(r.returncode, OUTER_KILLED, r.stdout + r.stderr)
        self.assertIn("POLL BUDGET REACHED", r.stdout)
        self.assertIn("queued", r.stdout)

    def test_default_budget_is_under_the_observed_harness_timeout(self):
        m = re.search(r"AIRULESET_POLL_BUDGET_S:-(\d+)\}",
                      deep_block(FOREGROUND_MARKER))
        self.assertIsNotNone(m)
        self.assertLess(int(m.group(1)), 120)


class TestBackgroundWaiter(unittest.TestCase):
    """The long-wait background waiter: same split, same retry rule."""

    def setUp(self):
        self.gh = StubGh(self)

    def test_split_cadence_and_completion(self):
        status = "|".join(["in_progress "] * 5 + ["completed success"])
        r = self.gh.run(background(), STATUS_SEQ=status, JOBS_SEQ="EMPTY")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TERMINAL: completed success", r.stdout)
        calls = self.gh.calls()
        self.assertEqual(calls.count("status,conclusion"), 6, calls)
        self.assertEqual(calls.count("jobs"), 2, calls)
        self.assertEqual(len(calls), 8, calls)

    def test_job_failure_wakes_on_the_third_poll(self):
        r = self.gh.run(background(), STATUS_SEQ="in_progress ",
                        JOBS_SEQ="JOBFAIL Slow Job")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("JOB FAILED (run still in progress): Slow Job", r.stdout)
        self.assertEqual(self.gh.calls().count("status,conclusion"), 3)

    def test_gh_failures_are_retries(self):
        r = self.gh.run(background(),
                        STATUS_SEQ="FAIL|in_progress |in_progress |completed success",
                        JOBS_SEQ="FAIL|EMPTY")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TERMINAL: completed success", r.stdout)
        self.assertNotIn("JOB FAILED", r.stdout)

    def test_gh_down_the_whole_time_never_reports_a_result(self):
        r = self.gh.run(background(), STATUS_SEQ="FAIL", JOBS_SEQ="FAIL",
                        AIRULESET_LONG_POLL_BUDGET_S="1")
        self.assertEqual(r.returncode, 124, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "")


class TestRealJqFilters(unittest.TestCase):
    """The --jq filters, as bash resolves them, run by a real jq (#405)."""

    STATUS_CASES = [
        ('{"status":"queued","conclusion":null}', "queued"),
        ('{"status":"in_progress","conclusion":""}', "in_progress"),
        ('{"status":"completed","conclusion":"failure"}', "completed failure"),
        ('{"status":"completed","conclusion":"success"}', "completed success"),
    ]
    JOBS_CASES = [
        ('{"jobs":[]}', ""),
        ('{"jobs":null}', ""),
        ('{"jobs":[{"name":"E2E Tests (slovnormal)","conclusion":"failure"},'
         '{"name":"Lint","conclusion":"success"}]}',
         "JOBFAIL E2E Tests (slovnormal)"),
        ('{"jobs":[{"name":"A","conclusion":"failure"},'
         '{"name":"B","conclusion":"failure"}]}', "JOBFAIL A, B"),
        ('{"jobs":[{"name":"Slow Job","conclusion":"timed_out"}]}',
         "JOBFAIL Slow Job"),
        # a cancelled job is a cascade or a deliberate cancel, never a wake
        ('{"jobs":[{"name":"Dependent","conclusion":"cancelled"},'
         '{"name":"Lint","conclusion":"success"}]}', ""),
    ]

    def _filters_of(self, script):
        gh = StubGh(self)
        r = gh.run(script, STATUS_SEQ="completed success", JOBS_SEQ="EMPTY",
                   AIRULESET_POLL_BUDGET_S="100")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        filters = gh.filters()
        self.assertEqual(sorted(filters), ["jobs", "status,conclusion"], filters)
        for flts in filters.values():
            self.assertEqual(len(flts), 1, flts)
        return {k: next(iter(v)) for k, v in filters.items()}

    def _jq(self, flt, payload):
        r = subprocess.run(["jq", "-r", flt], input=payload, text=True,
                           capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0, "jq failed on %r: %s" % (flt, r.stderr))
        return r.stdout.strip()

    def _check(self, filters):
        for payload, expected in self.STATUS_CASES:
            self.assertEqual(self._jq(filters["status,conclusion"], payload),
                             expected, payload)
        for payload, expected in self.JOBS_CASES:
            self.assertEqual(self._jq(filters["jobs"], payload), expected,
                             payload)

    @unittest.skipIf(shutil.which("jq") is None, "jq not installed")
    def test_foreground_filters(self):
        self._check(self._filters_of(foreground()))

    @unittest.skipIf(shutil.which("jq") is None, "jq not installed")
    def test_background_filters_match_the_foreground_ones(self):
        bg = self._filters_of(background())
        self._check(bg)
        self.assertEqual(bg, self._filters_of(foreground()))


@unittest.skipIf(shutil.which("jq") is None, "jq not installed")
class TestDeployWatchCadence(unittest.TestCase):
    """The #588 deploy watch reads jobs on the same gate, same retry rule."""

    GREEN = ('{"status":"in_progress","conclusion":"","jobs":['
             '{"name":"Deploy to PROD","conclusion":"success"},'
             '{"name":"Smoke tests","conclusion":"success"},'
             '{"name":"PROD E2E Tests","conclusion":null}]}')

    def _run(self, i, s, other):
        gh = StubGh(self)
        script = ('i=%d; s="%s"; d=""\n%s\nprintf "D=%%s\\n" "$d"\n'
                  % (i, s, deep_block(DEPLOY_MARKER).replace("<id>", RUN_ID)))
        r = gh.run(script, OTHER_SEQ=other)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout, gh.calls()

    def test_off_cadence_poll_reads_no_jobs(self):
        out, calls = self._run(1, "in_progress ", self.GREEN)
        self.assertEqual(calls, [])
        self.assertIn("D=\n", out)

    def test_every_third_poll_classifies(self):
        out, calls = self._run(3, "in_progress ", self.GREEN)
        self.assertEqual(calls, ["status,conclusion,jobs"])
        self.assertIn("D=DEPLOYED", out)

    def test_completion_classifies_off_cadence(self):
        out, calls = self._run(1, "completed success", self.GREEN)
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("D=DEPLOYED", out)

    def test_non_json_body_is_no_verdict_under_set_e(self):
        gh = StubGh(self)
        script = ('i=3; s="in_progress "; d=""\n%s\nprintf "D=%%s\\n" "$d"\n'
                  % deep_block(DEPLOY_MARKER).replace("<id>", RUN_ID))
        r = gh.run(script, strict=True, OTHER_SEQ="<html>502 Bad Gateway</html>")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("D=\n", r.stdout)

    def test_gh_failure_is_no_verdict(self):
        out, calls = self._run(3, "in_progress ", "FAIL")
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("D=\n", out)


class TestHookHandsOutTheSameRecipe(unittest.TestCase):
    """block-ci-poll-repeat.sh prints paste-ready copies of the recipe. Each
    gh line in them must equal the doc's (#405: an out-of-sync copy is the
    rot to prevent), and the hook must still treat the new loops as bounded
    polls (first free, repeat blocked)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _hook(self, command, background=False):
        import json
        env = hermetic_hook_env(self)
        env["AIRULESET_CIPOLL_STATE_DIR"] = self._tmp.name
        tool_input = {"command": command, "timeout": 540000}
        if background:
            tool_input["run_in_background"] = True
        payload = json.dumps({"session_id": "sess-1200", "tool_name": "Bash",
                              "tool_input": tool_input})
        return subprocess.run(["bash", str(POLL_HOOK)], input=payload,
                              text=True, capture_output=True, timeout=30,
                              env=env)

    @staticmethod
    def _gh_lines(block):
        return [ln.strip() for ln in block.replace("<id>", RUN_ID).splitlines()
                if "gh run view" in ln]

    def _assert_lines_in(self, block, text):
        printed = {ln.strip() for ln in text.splitlines()}
        lines = self._gh_lines(block)
        self.assertEqual(len(lines), 2, lines)
        for ln in lines:
            self.assertIn(ln, printed, "hook copy drifted from DEEP.md: " + ln)

    def test_loop_repeat_message_carries_the_doc_waiter(self):
        loop = foreground(sleep="30")
        self.assertEqual(self._hook(loop).returncode, 0)
        out = self._hook(loop)
        self.assertEqual(out.returncode, 2, out.stderr)
        self._assert_lines_in(deep_block(BACKGROUND_MARKER), out.stderr)

    def test_oneshot_message_carries_both_doc_shapes(self):
        cmd = "gh run view %s --json status,conclusion" % RUN_ID
        self._hook(cmd)
        self._hook(cmd)
        out = self._hook(cmd)
        self.assertEqual(out.returncode, 2, out.stderr)
        short_wait, _, long_wait = out.stderr.partition("LONG wait")
        self._assert_lines_in(deep_block(FOREGROUND_MARKER), short_wait)
        self._assert_lines_in(deep_block(BACKGROUND_MARKER), long_wait)

    def test_generic_bucket_copies_resolve_the_run_id(self):
        # No run id in the polled command: the printed copies resolve it
        # with `gh run list` and must hand it to the gh calls, including the
        # background waiter's child `bash -c` shell (#1200 review).
        cmd = "gh run view --json status,conclusion"
        for _ in range(2):
            self._hook(cmd)
        out = self._hook(cmd)
        self.assertEqual(out.returncode, 2, out.stderr)
        short_wait, _, long_wait = out.stderr.partition("LONG wait")
        fg = short_wait[short_wait.index("export RID="):]
        fg = fg[:fg.index("  done") + len("  done")]
        bg = long_wait[long_wait.index("export RID="):]
        bg = bg[:bg.index("  done'") + len("  done'")]
        for label, script in (("foreground", fg.replace("sleep 30", "sleep 0.01")),
                              ("background", bg.replace("sleep 60", "sleep 0.01"))):
            gh = StubGh(self)
            r = gh.run(script, STATUS_SEQ="in_progress |completed success",
                       JOBS_SEQ="EMPTY", RUN_LIST_ID="424242424242",
                       AIRULESET_LONG_POLL_BUDGET_S="10")
            self.assertIn("TERMINAL: completed success", r.stdout,
                          label + ": " + r.stdout + r.stderr)
            argv = gh.argv()
            self.assertTrue(argv, label)
            for line in argv:
                self.assertIn("run view 424242424242 ", line, label)

    def test_background_waiter_is_never_blocked(self):
        waiter = deep_block(BACKGROUND_MARKER).replace("<id>", RUN_ID)
        for _ in range(3):
            self.assertEqual(self._hook(waiter, background=True).returncode, 0)


class TestRecipesNeverTripTheCloseGuard(unittest.TestCase):
    """A reduced-authority stream pastes these recipes too; the fork-no-merge
    issue-close guard must never read them as a close (#873 class)."""

    def test_each_recipe_is_allowed(self):
        cwd = _cwd_with_authority("fork-no-merge")
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        for marker in (FOREGROUND_MARKER, BACKGROUND_MARKER, DEPLOY_MARKER):
            cmd = deep_block(marker).replace("<id>", RUN_ID)
            r = run_close_guard(cmd, cwd, me="someoneelse", author="zbynekdrlik")
            self.assertEqual(r.returncode, 0, marker + ": " + r.stderr)


if __name__ == "__main__":
    unittest.main()
