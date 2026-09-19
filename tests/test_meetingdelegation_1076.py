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
    def test_marker_not_on_first_line_blocks(self):
        # marker present but not the FIRST line -> unmarked -> block
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "Please transcribe transcript.txt.\nMECHANICAL-ONLY: asr"}})
        self.assertEqual(v, "block")

    def test_fake_phase_marker_blocks(self):
        v, _ = _ev({"tool_name": "Agent",
                    "tool_input": {"prompt": "MECHANICAL-ONLY: interpret\nread transcript.txt"}})
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
    def test_each_signal_triggers(self):
        for sig in ("transcript.txt", "speaker_turns.json", "frames_kept",
                    "screen_inventory", "notes_verbatim.md", "VIDEO-NOTES",
                    "analýza meetingu", "meeting analysis", "doplnok z meetingu"):
            v, _ = _ev({"tool_name": "Agent",
                        "tool_input": {"prompt": "please handle the %s here" % sig}})
            self.assertEqual(v, "block", "signal %r should trigger a block" % sig)

    def test_no_signal_allows_workflow(self):
        v, _ = _ev({"tool_name": "Workflow",
                    "tool_input": {"script": "run the build pipeline"}})
        self.assertEqual(v, "allow")

    def test_non_dispatch_tool_allows(self):
        v, _ = _ev({"tool_name": "Bash",
                    "tool_input": {"command": "cat transcript.txt"}})
        self.assertEqual(v, "allow")


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
    def test_registered_on_agent_and_workflow(self):
        cfg = json.loads((REPO / "settings" / "hooks.json").read_text())
        pre = cfg["hooks"]["PreToolUse"]
        matchers = {}
        for block in pre:
            cmds = [h.get("command", "") for h in block.get("hooks", [])]
            matchers.setdefault(block.get("matcher", ""), []).extend(cmds)
        for m in ("Agent", "Workflow"):
            joined = " ".join(matchers.get(m, []))
            self.assertIn("block-meeting-analysis-delegation.sh", joined,
                          "delegation hook must be wired on the %s matcher" % m)


if __name__ == "__main__":
    unittest.main()
