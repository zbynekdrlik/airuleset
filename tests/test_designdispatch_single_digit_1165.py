"""#1165 -- gates.designdispatch must parse SINGLE-DIGIT ticket numbers.

A young repo (fohmixer, 2026-09-28) has tickets 1-9. The dispatch prompt
`Work issue 4 (#4, S0 bootstrap) ...` parsed to NO ticket because every
ticket regex required `\\d{2,6}`, so the gate refused it with "names no
ticket" even though `design-record` had posted a valid main design on issue 4.

The fix accepts 1-6 digits wherever the number is anchored by an
`issue`/`issues`/`#` prefix. A prefix-less number (a version `0.1.5`, `v2`, a
count `3 lanes`) is still never a ticket. A follower in an `issue` run is a
ticket at any width (`issue 4, 7` -> [4, 7]): over-checking blocks, the safe
direction for a fail-closed gate. A 1-digit `#N` outside an `issue` run
(`step #1`, `bounce #2`, `PR #7`) never outranks a stronger reference.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import designdispatch as dd  # noqa: E402

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

_ORIG_HOME = None
_TMP_HOME = None


def setUpModule():
    # dd._log writes ~/.claude/design-by-gate.log in-process -- keep it hermetic.
    global _ORIG_HOME, _TMP_HOME
    _ORIG_HOME = os.environ.get("HOME")
    _TMP_HOME = tempfile.mkdtemp(prefix="a1165-modhome-")
    os.environ["HOME"] = _TMP_HOME


def tearDownModule():
    if _ORIG_HOME is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _ORIG_HOME
    shutil.rmtree(_TMP_HOME, ignore_errors=True)


FABLE = "claude-fable-5-1"
MAIN_DESIGN = "Design-by: main claude-opus-5-5"
FOHMIXER_PROMPT = "Work issue 4 (#4, S0 bootstrap) in fohmixer"


def _payload(prompt, cwd="/repo"):
    return json.dumps({
        "tool_name": "Agent", "cwd": cwd,
        "tool_input": {"subagent_type": "autopilot-worker", "prompt": prompt}})


def _ev(prompt, comments_by_issue):
    """evaluate() network-free: fetch/resolve_slug/is_pr injected."""
    def fetch(slug, number, cwd):
        return comments_by_issue.get(number)
    return dd.evaluate(_payload(prompt), fetch=fetch,
                       resolve_slug=lambda cwd: "owner/fohmixer",
                       is_pr=lambda n, slug, cwd: (False, None), fable_id=FABLE)


class TestSingleDigitParse(unittest.TestCase):
    def test_fohmixer_prompt(self):
        self.assertEqual(dd.issue_numbers(FOHMIXER_PROMPT), [4])

    def test_bare_issue_word(self):
        self.assertEqual(dd.issue_numbers("issue 4"), [4])

    def test_hash_form(self):
        self.assertEqual(dd.issue_numbers("#4"), [4])

    def test_plural_comma_run(self):
        self.assertEqual(dd.issue_numbers("issues 4, 7"), [4, 7])

    def test_plural_and_run(self):
        self.assertEqual(dd.issue_numbers("Work issues 4 and 7 in fohmixer"),
                         [4, 7])

    def test_singular_run_with_hash_member(self):
        self.assertEqual(dd.issue_numbers("Work issue 4, #7 in fohmixer"), [4, 7])

    def test_multi_digit_still_parses(self):
        self.assertEqual(dd.issue_numbers("#1165"), [1165])
        self.assertEqual(dd.issue_numbers("Work issues 1056, 1057"), [1056, 1057])

    def test_mixed_width_plural_run(self):
        self.assertEqual(dd.issue_numbers("Work issues 9, 10 and 11"), [9, 10, 11])


class TestPrefixlessNumbersAreNotTickets(unittest.TestCase):
    def test_versions_and_counts(self):
        self.assertEqual(
            dd.issue_numbers("bump to 0.1.5, deploy v2, run 3 lanes"), [])

    def test_singular_issue_run_follower_is_checked_not_dropped(self):
        # review of 1165: dropping `7` as a "count" would FAIL OPEN on a real
        # batch member; the run is read at any width, like the pre-fix 2-digit run.
        self.assertEqual(dd.issue_numbers("Work issue 4, 7 in fohmixer"), [4, 7])
        self.assertEqual(dd.issue_numbers("Work issue 4 and 7"), [4, 7])

    def test_items_after_issue_not_captured(self):
        self.assertEqual(
            dd.issue_numbers("Work issue 4 with items 1, 2, 3, 5"), [4])

    def test_leading_zero_is_not_a_ticket(self):
        # no ticket 0 / 007 -- `issue 0` must not become a check on issue 0.
        self.assertEqual(dd.issue_numbers("see issue 0 and #007 notes"), [])

    def test_prefixed_version_is_not_a_ticket(self):
        # a dotted number after a prefix reads as a version, not ticket 1 / 4.
        self.assertEqual(dd.issue_numbers("changelog #1.5 and issue 4.2"), [])

    def test_versioned_follower_does_not_hide_the_lead_ticket(self):
        self.assertEqual(dd.issue_numbers("Work issue 4 (ships 0.1.5)"), [4])

    def test_word_containing_issue_is_not_a_prefix(self):
        self.assertEqual(dd.issue_numbers("tissue 5 sample"), [])

    def test_no_ticket_prompt_still_refused(self):
        v, r = _ev("bump to 0.1.5, deploy v2, run 3 lanes", {})
        self.assertEqual(v, "block")
        self.assertIn("names no ticket", r)


class TestEvaluateSingleDigit(unittest.TestCase):
    def test_fohmixer_main_design_allows(self):
        # RED on the pre-#1165 code: the prompt parsed to [] -> "names no ticket".
        v, r = _ev(FOHMIXER_PROMPT, {4: [MAIN_DESIGN]})
        self.assertEqual(v, "allow", r)

    def test_fohmixer_worker_design_blocks_on_issue_4(self):
        v, r = _ev(FOHMIXER_PROMPT, {4: ["Design-by: worker claude-opus-4-8"]})
        self.assertEqual(v, "block")
        self.assertIn("#4", r)

    def test_single_digit_batch_one_missing_blocks(self):
        v, r = _ev("Work issues 4, 7 in fohmixer", {4: [MAIN_DESIGN], 7: []})
        self.assertEqual(v, "block")
        self.assertIn("#7", r)


class TestAdapterSingleDigit(unittest.TestCase):
    """The REAL hook adapter with a fake `gh` on PATH (same fixture shape as
    test_designdispatch_gate_1061.TestAdapterEndToEnd)."""

    HOOK = REPO / "hooks" / "block-dispatch-without-main-design.sh"

    def _run(self, prompt, comments_json):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        gh = Path(d) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "repo" ]; then echo "owner/fohmixer"; exit 0; fi\n'
            'if [ "$1" = "api" ]; then cat <<\'JSON\'\n%s\nJSON\n'
            "exit 0; fi\nexit 0\n" % comments_json)
        gh.chmod(0o755)
        env = hermetic_hook_env(self, PATH=d + os.pathsep + os.environ["PATH"])
        return subprocess.run(
            ["bash", str(self.HOOK)], input=_payload(prompt, cwd=str(REPO)),
            capture_output=True, text=True, env=env)

    def test_fohmixer_prompt_allows_via_adapter(self):
        r = self._run(FOHMIXER_PROMPT, json.dumps({"body": MAIN_DESIGN}))
        self.assertEqual(r.returncode, 0,
                         "rc=%d stderr=%r" % (r.returncode, r.stderr))

    def test_fohmixer_worker_design_blocks_via_adapter(self):
        r = self._run(FOHMIXER_PROMPT,
                      json.dumps({"body": "Design-by: worker claude-opus-4-8"}))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#4", r.stderr)


if __name__ == "__main__":
    unittest.main()
