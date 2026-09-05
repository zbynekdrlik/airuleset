"""RED tests for subagent-stop-check-review-tier.sh (#876).

Nine cases from the design comment — synthetic payload + transcript fixtures,
isolated $HOME / /tmp state.
"""

import glob
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "hooks" / "subagent-stop-check-review-tier.sh"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transcript(entries, path):
    """Write a synthetic JSONL transcript at *path*."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _run_hook(payload, *, home_dir=None, env_extra=None):
    """Run the hook with the given JSON payload on stdin.

    Returns (returncode, stdout, stderr).
    """
    env = os.environ.copy()
    if home_dir:
        env["HOME"] = str(home_dir)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _worktree_return_msg(*, reviewed_by_tier=None, extra_lines=""):
    """Build a synthetic worktree-mode evidence block."""
    lines = [
        "issues: #876 (review-tier gate)",
        "plan: 1/1 fulfilled",
        "validated: still valid",
        "approach: design comment posted",
        "review: /review + /requesting-code-review clean — 0 red 0 yellow 0 blue",
    ]
    if reviewed_by_tier is not None:
        lines.append("reviewed-by-tier: %s" % reviewed_by_tier)
    lines.extend([
        "achieved: review-tier gate implemented",
        "worktree: /home/test/.claude/worktrees/agent-abc123",
        "branch: worktree-agent-abc123",
        "local_verify: pytest + ruff green",
        "lane_return: comment posted",
        "dropped: none",
        "obsolete_closed: none",
        "unverified: none",
        "filed: none",
    ])
    if extra_lines:
        lines.append(extra_lines)
    return "\n".join(lines)


def _base_payload(msg, *, transcript_path="", session_id=None):
    if session_id is None:
        session_id = "test-876-" + uuid.uuid4().hex
    return {
        "agent_type": "autopilot-worker",
        "session_id": session_id,
        "cwd": str(REPO_ROOT),
        "last_assistant_message": msg,
        "agent_transcript_path": transcript_path,
    }


# A fable-gate OPEN result in the transcript.
def _gate_open_entry():
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "content": "OPEN fable=15% weekly=20% (< 90% gate)",
                }
            ]
        }
    }


def _gate_closed_entry():
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "content": "CLOSED fable=92% weekly=95% (>= 90% gate)",
                }
            ]
        }
    }


def _fable_advisor_dispatch_entry():
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Agent",
                    "input": {
                        "subagent_type": "fable-advisor",
                        "prompt": "Review the diff...",
                        "description": "Fable review",
                    },
                }
            ]
        }
    }


def _fable_gate_bash_entry():
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {
                        "command": "python3 ~/devel/airuleset/airuleset.py fable-gate",
                    },
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReviewTierHook(unittest.TestCase):
    """Nine cases from the design."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="review-tier-876-")
        self.home = os.path.join(self.tmpdir, "home")
        os.makedirs(self.home, exist_ok=True)
        # Unique session id per test to avoid once-per state collision (#494).
        self.sid = "test-876-" + uuid.uuid4().hex
        # Register cleanup for any state files this session creates.
        self.addCleanup(self._cleanup_state_files)

    def _cleanup_state_files(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        # airuleset:script-ok test teardown cleanup — best-effort removal
        for f in glob.glob("/tmp/airuleset-reviewtier-*%s*" % self.sid):
            try:
                os.unlink(f)
            except OSError:
                pass  # airuleset:script-ok test cleanup, file may already be gone

    # Case 1: OPEN + fable dispatch + fable line => pass
    def test_case1_open_fable_dispatch_fable_line_passes(self):
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        _make_transcript([
            _fable_gate_bash_entry(),
            _gate_open_entry(),
            _fable_advisor_dispatch_entry(),
        ], transcript)

        msg = _worktree_return_msg(
            reviewed_by_tier="claude-fable-5 gate:OPEN")
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        # Should NOT produce a block decision.
        if out:
            parsed = json.loads(out)
            self.assertNotEqual(parsed.get("decision"), "block")

    # Case 2: completed worktree return, line missing => block
    def test_case2_missing_tier_line_blocks(self):
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        _make_transcript([_fable_gate_bash_entry(), _gate_open_entry()],
                         transcript)

        msg = _worktree_return_msg(reviewed_by_tier=None)
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        self.assertIn('"decision"', out)
        parsed = json.loads(out)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("reviewed-by-tier", parsed["reason"])

    # Case 3: fable line, no dispatch => block
    def test_case3_fable_line_no_dispatch_blocks(self):
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        # Gate OPEN but NO fable-advisor dispatch.
        _make_transcript([
            _fable_gate_bash_entry(),
            _gate_open_entry(),
        ], transcript)

        msg = _worktree_return_msg(
            reviewed_by_tier="claude-fable-5 gate:OPEN")
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        self.assertIn('"decision"', out)
        parsed = json.loads(out)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("fable-advisor", parsed["reason"])

    # Case 4: OPEN + opus line, no trivial marker => block
    def test_case4_open_opus_no_trivial_blocks(self):
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        _make_transcript([
            _fable_gate_bash_entry(),
            _gate_open_entry(),
        ], transcript)

        msg = _worktree_return_msg(
            reviewed_by_tier="claude-opus-4-6 gate:OPEN")
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        self.assertIn('"decision"', out)
        parsed = json.loads(out)
        self.assertEqual(parsed["decision"], "block")
        self.assertIn("OPEN", parsed["reason"])

    # Case 5: CLOSED + opus line => pass
    def test_case5_closed_opus_passes(self):
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        _make_transcript([
            _fable_gate_bash_entry(),
            _gate_closed_entry(),
        ], transcript)

        msg = _worktree_return_msg(
            reviewed_by_tier="claude-opus-4-6 gate:CLOSED")
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        if out:
            self.assertNotIn('"block"', out)

    # Case 6: blocked/question/ISOLATION-FAILED return => pass (skip)
    def test_case6_incomplete_return_skipped(self):
        # Test with ISOLATION FAILED.
        msg = "ISOLATION FAILED: /home/newlevel/devel/airuleset main"
        payload = _base_payload(msg, session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

        # Test with UNVERIFIED.
        msg2 = ("issues: #876\n"
                "UNVERIFIED: cannot test\n"
                "branch: worktree-agent-xyz")
        payload2 = _base_payload(msg2, session_id=self.sid)
        rc2, out2, err2 = _run_hook(payload2, home_dir=self.home)
        self.assertEqual(rc2, 0)
        self.assertEqual(out2, "")

        # Test with question marker.
        msg3 = "issues: #876\nbranch: worktree-agent-xyz\n❓ NEEDS YOU: question"
        payload3 = _base_payload(msg3, session_id=self.sid)
        rc3, out3, err3 = _run_hook(payload3, home_dir=self.home)
        self.assertEqual(rc3, 0)
        self.assertEqual(out3, "")

    # Case 7: unreadable transcript + line present => pass + log line
    def test_case7_unreadable_transcript_passes(self):
        msg = _worktree_return_msg(
            reviewed_by_tier="claude-fable-5 gate:OPEN")
        payload = _base_payload(msg, transcript_path="/nonexistent/path.jsonl",
                                session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        # Should pass (no block) with a log line on stderr.
        if out:
            self.assertNotIn('"block"', out)
        self.assertIn("fail-open", err)

    # Case 8: trivial-diff declaration, no gate call => pass
    def test_case8_trivial_diff_no_gate_passes(self):
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        # Empty transcript — no gate call at all.
        _make_transcript([], transcript)

        msg = _worktree_return_msg(
            reviewed_by_tier="claude-opus-4-6 trivial-diff gate:n/a")
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        if out:
            self.assertNotIn('"block"', out)

    # Case 9: second stop same issue => pass (once-per guard)
    def test_case9_second_stop_same_issue_passes(self):
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        _make_transcript([_fable_gate_bash_entry(), _gate_open_entry()],
                         transcript)

        msg = _worktree_return_msg(reviewed_by_tier=None)
        # Use self.sid (unique per test run) for once-per testing.
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)

        # First stop — should block.
        rc1, out1, _ = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc1, 0)
        self.assertIn('"block"', out1)

        # Second stop same session+issue — should pass (once-per).
        rc2, out2, _ = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc2, 0)
        if out2:
            self.assertNotIn('"block"', out2)


    # Case 10: noise immunity — gate CLOSED + unrelated "OPEN" in transcript
    def test_case10_noise_immunity_closed_with_unrelated_open(self):
        """A gate-CLOSED result + an unrelated tool_result containing 'OPEN'
        (e.g. from gh issue view --json state) must NOT false-trigger a
        BLOCK_DOWNTIER. #876 Fable review RED fix."""
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        _make_transcript([
            _fable_gate_bash_entry(),
            _gate_closed_entry(),
            # An unrelated tool_result that contains "OPEN" — e.g. gh issue view.
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": '{"state": "OPEN", "title": "some issue"}',
                        }
                    ]
                }
            },
        ], transcript)

        msg = _worktree_return_msg(
            reviewed_by_tier="claude-opus-4-6 gate:CLOSED")
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)
        rc, out, err = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc, 0)
        # Must NOT block — the OPEN is noise, the gate is CLOSED.
        if out:
            self.assertNotIn('"block"', out)

    # Case 11: stage-2 block (BLOCK_NO_DISPATCH) also has once-per guard
    def test_case11_stage2_block_once_per(self):
        """BLOCK_NO_DISPATCH must also be non-wedging (once per session+issue).
        #876 Fable review YELLOW fix."""
        transcript = os.path.join(self.tmpdir, "transcript.jsonl")
        _make_transcript([_fable_gate_bash_entry(), _gate_open_entry()],
                         transcript)

        msg = _worktree_return_msg(
            reviewed_by_tier="claude-fable-5 gate:OPEN")
        payload = _base_payload(msg, transcript_path=transcript,
                                session_id=self.sid)

        # First stop — should block (fable claimed, no dispatch).
        rc1, out1, _ = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc1, 0)
        self.assertIn('"block"', out1)

        # Second stop same session+issue — should pass (once-per).
        rc2, out2, _ = _run_hook(payload, home_dir=self.home)
        self.assertEqual(rc2, 0)
        if out2:
            self.assertNotIn('"block"', out2)


class TestNonWorkerSkipped(unittest.TestCase):
    """The hook should exit 0 silently for non-autopilot-worker agents."""

    def test_general_purpose_agent_skipped(self):
        payload = {
            "agent_type": "general-purpose",
            "session_id": "s1",
            "cwd": str(REPO_ROOT),
            "last_assistant_message": "hello",
        }
        rc, out, _ = _run_hook(payload)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")


class TestSharedTierConstant(unittest.TestCase):
    """The shared REVIEWED_BY_TIER_VALUES constant exists and matches."""

    def test_constant_exists_and_is_correct(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import importlib
        airuleset = importlib.import_module("airuleset")
        self.assertIn("claude-fable-5", airuleset.REVIEWED_BY_TIER_VALUES)
        self.assertIn("claude-opus-4-6", airuleset.REVIEWED_BY_TIER_VALUES)
        self.assertEqual(len(airuleset.REVIEWED_BY_TIER_VALUES), 2)

    def test_constant_derives_from_model_tiers(self):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import importlib
        airuleset = importlib.import_module("airuleset")
        expected = {airuleset.MODEL_TIERS["fable"],
                    airuleset.MODEL_TIERS["opus"]}
        self.assertEqual(airuleset.REVIEWED_BY_TIER_VALUES, expected)


class TestHookRegistered(unittest.TestCase):
    """The hook must be registered in settings/hooks.json."""

    def test_hook_in_settings(self):
        settings = json.loads(
            (REPO_ROOT / "settings" / "hooks.json").read_text())
        hooks = settings.get("hooks", {}).get("SubagentStop", [])
        commands = []
        for entry in hooks:
            for h in entry.get("hooks", []):
                commands.append(h.get("command", ""))
        self.assertTrue(
            any("subagent-stop-check-review-tier" in c for c in commands),
            "subagent-stop-check-review-tier.sh not found in SubagentStop hooks")


class TestWorkerDoctrineReviewedByTier(unittest.TestCase):
    """Worker.md must carry the reviewed-by-tier field in the evidence block."""

    def test_evidence_block_has_reviewed_by_tier(self):
        worker = (REPO_ROOT / "agents" / "autopilot-worker.md").read_text()
        self.assertIn("reviewed-by-tier:", worker)
        # The worktree evidence block specifically.
        self.assertIn(
            "reviewed-by-tier: claude-fable-5|claude-opus-4-6", worker)

    def test_step6_qualifies_self_review(self):
        worker = (REPO_ROOT / "agents" / "autopilot-worker.md").read_text()
        # The text may wrap across lines — check each key phrase.
        self.assertIn("in-context", worker)
        self.assertIn("DECLARED trivial diff", worker)
        self.assertIn("subagent-stop-check-review-tier.sh", worker)

    def test_lane_return_template_has_tier(self):
        worker = (REPO_ROOT / "agents" / "autopilot-worker.md").read_text()
        self.assertIn("reviewed-by-tier:", worker)


class TestSKILLStep4ReviewTier(unittest.TestCase):
    """SKILL.md Step 4 must mention the review-tier consistency check."""

    def test_step4_mentions_review_tier(self):
        skill = (REPO_ROOT / "skills" / "autopilot" / "SKILL.md").read_text()
        self.assertIn("Review-tier", skill)
        self.assertIn("#876", skill)


if __name__ == "__main__":
    unittest.main()
