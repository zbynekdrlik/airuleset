"""#1076 -- gates.meetingdelegation + hooks/block-meeting-analysis-delegation.sh.

The meeting-analysis INTERPRETATION-stays-in-main PreToolUse gate (owner
directive 2026-09-18): a meeting/call recording is interpreted by the Fable
main, never a subagent. An Agent/Workflow dispatch carrying a meeting-analysis
signal is REFUSED unless marked `MECHANICAL-ONLY: extract|asr|dedup` on its
first line AND free of interpretation verbs. See Hard Rule 0 in
skills/meeting-analysis/SKILL.md.

The acceptance fixtures come straight from the #1076 design comment:
  - an Agent prompt with `frames_kept` + "read the screens" -> exit 2;
  - `MECHANICAL-ONLY: asr` + `transcript.txt` and no verb -> exit 0;
  - a Workflow script with "parallel readers over transcript segments" -> exit 2;
  - an unrelated Agent prompt -> exit 0.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import meetingdelegation as md  # noqa: E402

HOOK = REPO / "hooks" / "block-meeting-analysis-delegation.sh"


def _ev(payload):
    return md.evaluate(json.dumps(payload))


def _run_hook(payload):
    """Drive the REAL hook through its stdin contract (the #963 dry-run harness)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.environ.get("HOME", "/tmp")}
    r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stderr


# ---- design acceptance fixtures (evaluate) -------------------------------- #

class TestDesignAcceptance(unittest.TestCase):
    def test_agent_frames_kept_read_the_screens_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "read the screens in frames_kept and map them"}})
        self.assertEqual(v, "block")

    def test_mechanical_only_asr_transcript_no_verb_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "MECHANICAL-ONLY: asr\nRun the ASR API on transcript.txt"}})
        self.assertEqual(v, "allow")

    def test_workflow_parallel_readers_over_transcript_segments_blocks(self):
        # the real phase-5 fan-out script: parallel readers over transcript
        # segments + screen groups, reading transcript.txt / screen_inventory /
        # frames_kept (all meeting-analysis artifact signals).
        script = ("# fan out parallel readers over transcript segments + screen "
                  "groups\nfor seg in transcript.txt: reader reads frames_kept and "
                  "writes screen_inventory")
        v, _ = _ev({"tool_name": "Workflow", "tool_input": {"script": script}})
        self.assertEqual(v, "block")

    def test_unrelated_agent_prompt_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "refactor the driver and add a test"}})
        self.assertEqual(v, "allow")


# ---- marker + verb semantics --------------------------------------------- #

class TestMarkerAndVerbs(unittest.TestCase):
    def test_marker_on_line2_does_not_save_an_interp_dispatch(self):
        # #1076 review: blocking keys on interpretation intent. A marker below the
        # first line does not save a dispatch that asks for interpretation.
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "read the screens in frames_kept.\nMECHANICAL-ONLY: asr"}})
        self.assertEqual(v, "block")

    def test_marked_but_summarise_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "MECHANICAL-ONLY: dedup\ndedup frames_kept and summarise them"}})
        self.assertEqual(v, "block")

    def test_marked_but_requirements_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "MECHANICAL-ONLY: asr\ntranscribe transcript.txt and list the requirements"}})
        self.assertEqual(v, "block")

    def test_marked_but_slovak_precitaj_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "MECHANICAL-ONLY: extract\nprečítaj obrazovky vo frames_kept"}})
        self.assertEqual(v, "block")

    def test_marked_extract_only_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "MECHANICAL-ONLY: extract|asr|dedup\nffmpeg extract, ASR transcript.txt, dedup frames_kept"}})
        self.assertEqual(v, "allow")

    def test_bypass_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "read the screens in frames_kept  airuleset:meeting-delegation-ok manual verify"}})
        self.assertEqual(v, "allow")


# ---- signal detection ---------------------------------------------------- #

class TestSignals(unittest.TestCase):
    def test_each_signal_with_interp_blocks(self):
        # a signal + an interpretation verb ("read the screen") -> block
        for sig in ("transcript.txt", "speaker_turns.json", "frames_kept",
                    "screen_inventory", "notes_verbatim.md", "VIDEO-NOTES",
                    "analýza meetingu", "meeting analysis", "doplnok z meetingu"):
            v, _ = _ev({"tool_name": "Agent",
                        "tool_input": {"prompt": "read the screens; handle the %s" % sig}})
            self.assertEqual(v, "block", "signal %r + interp should block" % sig)

    def test_signal_alone_without_interp_allows(self):
        # #1076 review (F2): naming a meeting artifact WITHOUT interpretation
        # intent -- a code/dev/review/search dispatch -- must NOT be blocked.
        for sig in ("transcript.txt", "speaker_turns.json", "frames_kept",
                    "screen_inventory"):
            v, _ = _ev({"tool_name": "Agent",
                        "tool_input": {"prompt": "fix the parsing bug in %s handling" % sig}})
            self.assertEqual(v, "allow", "signal %r alone (no interp) should allow" % sig)

    def test_no_signal_allows_workflow(self):
        v, _ = _ev({"tool_name": "Workflow",
                    "tool_input": {"script": "run the build pipeline"}})
        self.assertEqual(v, "allow")

    def test_non_dispatch_tool_allows(self):
        v, _ = _ev({"tool_name": "Bash",
                    "tool_input": {"command": "cat transcript.txt"}})
        self.assertEqual(v, "allow")


class TestCoordinatorTightening(unittest.TestCase):
    """#1076 integration review: the owner's literal plain-Slovak scenario MUST
    block — intent = STEMS (not conjugations), and the interpretation-act PHRASE
    signals (analýza meetingu / meeting analysis / doplnok z meetingu) are intent
    by themselves; artifact-path signals still require an intent token."""

    def test_sprav_analyzu_meetingu_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "Sprav analýzu meetingu z nahrávky (transcript.txt, frames_kept)"}})
        self.assertEqual(v, "block")

    def test_spracuj_nahravku_meetingu_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "spracuj nahrávku meetingu, transcript.txt v ~/work"}})
        self.assertEqual(v, "block")

    def test_english_meeting_analysis_phrase_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "do the meeting analysis from transcript.txt"}})
        self.assertEqual(v, "block")

    def test_doplnok_z_meetingu_phrase_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "priprav doplnok z meetingu"}})
        self.assertEqual(v, "block")

    def test_vyhodnot_stem_with_artifact_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "vyhodnoť frames_kept a speaker_turns.json"}})
        self.assertEqual(v, "block")

    def test_find_where_transcript_written_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "find where transcript.txt is written in the repo"}})
        self.assertEqual(v, "allow")

    def test_mechanical_write_transcript_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "MECHANICAL-ONLY: asr — run Soniox on audio.wav, write transcript.txt"}})
        self.assertEqual(v, "allow")

    def test_review_of_the_gate_itself_blocks_without_bypass(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "review the meeting-analysis gate that reads screen_inventory"}})
        self.assertEqual(v, "block")

    def test_review_of_the_gate_itself_allows_with_bypass(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "review the meeting-analysis gate that reads screen_inventory  airuleset:meeting-delegation-ok reviewing the gate itself"}})
        self.assertEqual(v, "allow")


class TestSkillNameNotOverBlocked(unittest.TestCase):
    """#1076 delta review: the skill's own directory is literally
    `skills/meeting-analysis`, so an English `meeting-analysis` phrase-alone arm
    would block every dev/grep/review/worker dispatch that merely NAMES the skill
    — including the autopilot-worker dispatch for this very ticket. Those carry
    NO artifact + NO interpretation verb, so they MUST stay allowed; a real
    English interpretation ("do the meeting analysis from transcript.txt") still
    blocks via the artifact + the `anal[yý][sz]` stem."""

    def test_typo_fix_in_skill_dir_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "fix a typo in skills/meeting-analysis/SKILL.md"}})
        self.assertEqual(v, "allow")

    def test_autopilot_worker_dispatch_for_this_ticket_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"subagent_type": "autopilot-worker",
                                   "prompt": "Work issue #1076: meeting-analysis interpretation gate in airuleset"}})
        self.assertEqual(v, "allow")

    def test_grep_for_skill_name_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "grep -r 'meeting-analysis' the skills dir and list matches"}})
        self.assertEqual(v, "allow")

    def test_real_english_analysis_with_artifact_still_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "do the meeting analysis from transcript.txt"}})
        self.assertEqual(v, "block")


class TestOverBlockFix(unittest.TestCase):
    """#1076 review F2 (both reviewers): the gate must NOT wedge non-interpretation
    dispatches that merely NAME the meeting-analysis vocabulary."""

    def test_bugfix_dispatch_on_the_skill_code_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"subagent_type": "autopilot-worker",
                                   "prompt": "Work issue 42: fix the speaker_turns.json parser in prep.py"}})
        self.assertEqual(v, "allow")

    def test_review_dispatch_naming_artifacts_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "Review the diff touching frames_kept and screen_inventory dedup logic"}})
        self.assertEqual(v, "allow")

    def test_grep_dispatch_allows(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "grep for frames_kept across the repo and list the files"}})
        self.assertEqual(v, "allow")

    def test_reader_fanout_still_blocks(self):
        v, _ = _ev({"tool_name": "Workflow",
                    "tool_input": {"script": "dispatch parallel readers over transcript.txt segments and screen_inventory groups"}})
        self.assertEqual(v, "block")


# ---- the real hook (stdin contract) -------------------------------------- #

class TestHook(unittest.TestCase):
    def test_hook_blocks_interp_dispatch(self):
        rc, err = _run_hook({"tool_name": "Agent",
                             "tool_input": {"prompt": "read the screens in frames_kept"}})
        self.assertEqual(rc, 2, err)
        self.assertIn("MAIN session", err)

    def test_hook_allows_mechanical_dispatch(self):
        rc, err = _run_hook({"tool_name": "Agent",
                             "tool_input": {"prompt": "MECHANICAL-ONLY: asr\ntranscribe transcript.txt"}})
        self.assertEqual(rc, 0, err)

    def test_hook_allows_unrelated(self):
        rc, err = _run_hook({"tool_name": "Agent",
                             "tool_input": {"prompt": "refactor the parser"}})
        self.assertEqual(rc, 0, err)


# ---- wiring -------------------------------------------------------------- #

class TestWiring(unittest.TestCase):
    def test_registered_on_agent_task_and_workflow(self):
        # #1076 review F1: the sibling dispatch gates are on Agent+Task+Workflow;
        # evaluate() handles all three, so all three must be wired.
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        pre = cfg["hooks"]["PreToolUse"]
        matchers = {}
        for block in pre:
            cmds = [h.get("command", "") for h in block.get("hooks", [])]
            matchers.setdefault(block.get("matcher", ""), []).extend(cmds)
        for m in ("Agent", "Task", "Workflow"):
            joined = " ".join(matchers.get(m, []))
            self.assertIn("block-meeting-analysis-delegation.sh", joined,
                          "delegation hook must be wired on the %s matcher" % m)


if __name__ == "__main__":
    unittest.main()
