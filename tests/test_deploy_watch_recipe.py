"""airuleset #588 — the DEPLOY / VERSION-LIVE watch recipe in ci-monitoring.md.

Owner report (montalu5, 2026-08-20): a release-wait watcher watched an
odoo-erp deploy run to RUN-level terminal state while the version was
already LIVE on PROD for 30+ min — only a long post-deploy "PROD E2E Tests"
tail job kept the run ``in_progress``. The unblock condition must be
DEPLOYED-STATE (the deploy-completing job set green), never run-terminal,
so the tail never keeps a worker "watching a deploy" that is long done.

The mechanical piece is a jq DEPLOY-DONE classifier documented as a poll
recipe in ``modules/core/ci-monitoring.md``. These tests EXTRACT that jq
filter from the doc (single source of truth) and RUN it with the real
``jq`` against fixtures for each scenario — so the recipe cannot silently
drift away from the behaviour it claims. RED before the recipe exists
(extraction returns None); GREEN once it is added.
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CI_MONITORING = REPO / "modules" / "core" / "ci-monitoring.md"
STATUSLINE = REPO / "modules" / "core" / "statusline-vocabulary.md"

sys.path.insert(0, str(REPO))
from watchdog import ops_wait_recheck as owr  # noqa: E402


def _line_with(text, finder):
    for line in text.split("\n"):
        if finder in line:
            return line
    return ""

# The deploy-completing job set for the owner's odoo-erp case — the SAME
# DEPLOY_JOB_RE the recipe documents. The "PROD E2E Tests" tail is
# deliberately NOT in it (it is the optional confirmation, never the gate).
DEPLOY_JOB_RE = "Deploy to PROD|Disable Maintenance|Smoke"

# Extract the single-quoted jq filter that follows `jq -r` in the recipe.
# The filter uses only double-quoted jq strings, so it contains no `'`; the
# first `'` after `jq -r ...` opens it and the next `'` closes it.
_FILTER_RE = re.compile(r"jq -r[^']*'(.+?)'", re.DOTALL)


def _deploy_watch_filter():
    """The jq DEPLOY-DONE filter string as written in ci-monitoring.md, or
    None if the recipe is not present (the RED state)."""
    if not CI_MONITORING.is_file():
        return None
    m = _FILTER_RE.search(CI_MONITORING.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def _classify(run_json):
    """Run the real jq DEPLOY-DONE filter over one `gh run view` payload,
    exactly as the recipe's poll loop does, and return its verdict token."""
    flt = _deploy_watch_filter()
    assert flt is not None, "deploy-watch jq filter not found in ci-monitoring.md"
    proc = subprocess.run(
        ["jq", "-r", "--arg", "re", DEPLOY_JOB_RE, flt],
        input=json.dumps(run_json), capture_output=True, text=True,
    )
    assert proc.returncode == 0, "jq failed: %s" % proc.stderr
    return proc.stdout.strip()


def _job(name, conclusion):
    return {"name": name, "conclusion": conclusion}


# The owner's real topology: 3 deploy-completing jobs + 1 long E2E tail.
_DEPLOY_SET = [
    _job("Deploy to PROD", "success"),
    _job("Disable Maintenance Mode", "success"),
    _job("Smoke tests", "success"),
]


@unittest.skipIf(shutil.which("jq") is None, "jq not installed")
class TestDeployDoneClassifier(unittest.TestCase):
    """The jq filter classifies each real deploy-run shape correctly."""

    def test_recipe_is_present(self):
        """RED anchor: the recipe (and thus its jq filter) exists at all."""
        self.assertIsNotNone(
            _deploy_watch_filter(),
            "ci-monitoring.md carries no `jq -r`-based DEPLOY-DONE recipe (#588)",
        )

    def test_deploy_green_while_e2e_tail_running_is_deployed(self):
        """The owner's exact case: deploy set green, E2E tail still running,
        run still in_progress → DEPLOYED (unblock), NOT PENDING/TERMINAL."""
        run = {"status": "in_progress", "conclusion": None,
               "jobs": _DEPLOY_SET + [_job("PROD E2E Tests", None)]}
        self.assertTrue(_classify(run).startswith("DEPLOYED"))

    def test_e2e_tail_failure_never_masks_deployed(self):
        """The load-bearing scope rule (owner point 3): a FAILED E2E tail is
        outside the deploy set, so it must NOT flip DEPLOYED to a failure —
        the tail is optional confirmation, never the re-entry gate."""
        run = {"status": "in_progress", "conclusion": None,
               "jobs": _DEPLOY_SET + [_job("PROD E2E Tests", "failure")]}
        verdict = _classify(run)
        self.assertTrue(verdict.startswith("DEPLOYED"), verdict)
        self.assertNotIn("DEPLOYFAIL", verdict)

    def test_deploy_set_job_failure_is_deployfail(self):
        """A failure INSIDE the deploy set fails fast — the deploy broke."""
        run = {"status": "in_progress", "conclusion": None, "jobs": [
            _job("Deploy to PROD", "failure"),
            _job("Disable Maintenance Mode", None),
            _job("Smoke tests", None),
            _job("PROD E2E Tests", None)]}
        verdict = _classify(run)
        self.assertTrue(verdict.startswith("DEPLOYFAIL"), verdict)
        self.assertIn("Deploy to PROD", verdict)

    def test_deploy_set_still_running_is_pending(self):
        """No deploy-set job green yet, none failed → keep polling."""
        run = {"status": "in_progress", "conclusion": None, "jobs": [
            _job("Deploy to PROD", None),
            _job("Disable Maintenance Mode", None),
            _job("Smoke tests", None),
            _job("PROD E2E Tests", None)]}
        self.assertTrue(_classify(run).startswith("PENDING"))

    def test_run_completed_without_a_deploy_match_is_terminal(self):
        """A misconfigured DEPLOY_JOB_RE (no job matches) must fall back to
        the run's own terminal state, never hang forever on PENDING."""
        run = {"status": "completed", "conclusion": "success", "jobs": [
            _job("build", "success"), _job("test", "success")]}
        self.assertTrue(_classify(run).startswith("TERMINAL"))


class TestRecipeDocumentsDeployedStateSemantics(unittest.TestCase):
    """Content-lock (#500 single-line teeth) on the non-executed prose that
    states WHY the recipe exists — unblock on deployed-state, and the tail
    is never the re-entry gate. The functional tests above are the teeth on
    the jq itself; these guard the framing a partial revert would drop."""

    def _text(self):
        return CI_MONITORING.read_text(encoding="utf-8")

    def test_names_deployed_state_not_run_terminal(self):
        text = self._text()
        self.assertIn("DEPLOYED-STATE, not run-terminal", text)

    def test_carries_the_deploy_job_re_parameter(self):
        self.assertIn("DEPLOY_JOB_RE", self._text())

    def test_tail_is_optional_confirmation_never_the_gate(self):
        """Owner point 3 must survive verbatim as the doc's own rule."""
        text = self._text().lower()
        self.assertIn("never the re-entry gate", text)


class TestWDoctrineNamesVersionLive(unittest.TestCase):
    """#588 part (c): the W/ops-wait re-entry guidance names 'version live on
    PROD' (deployed-state) as the event for a release/deploy-parked W ticket,
    NOT run-terminal — on BOTH the statusline W doctrine and the job-20
    ops_wait_recheck nudge text."""

    # Content-lock teeth (#578): each token must be UNIQUE on the one huge
    # physical W-bullet line, or a partial revert leaves the token elsewhere
    # and the lock has no teeth.
    W_FINDER = "Deploy/release re-entry event = verzia ŽIVÁ na PROD (#588)"
    W_TOKENS = ("DEPLOYED-STATE", "run-terminal", "deploy-watch recept")

    def test_statusline_W_bullet_names_deployed_state_re_entry(self):
        text = STATUSLINE.read_text(encoding="utf-8")
        line = _line_with(text, self.W_FINDER)
        self.assertTrue(line, "the W bullet must carry the #588 deploy/release "
                              "re-entry clause")
        for tok in self.W_TOKENS:
            self.assertEqual(
                line.count(tok), 1,
                "W bullet token %r must be whole-line-unique for #578 teeth" % tok)

    def test_ops_wait_nudge_names_version_live_not_run_terminal(self):
        """The job-20 W->I re-check nudge tells a parked-W session the release
        re-entry signal is the LIVE version on the target, not run-terminal."""
        t = owr._nudge_text(None, [41], now=1000.0, w_first_seen=1000.0)
        self.assertIn("verzia ŽIVÁ na cieli", t)
        self.assertIn("run-terminal", t)
        # the member is still named (the existing #547/#570 behaviour is intact)
        self.assertIn("#41", t)


if __name__ == "__main__":
    unittest.main()
