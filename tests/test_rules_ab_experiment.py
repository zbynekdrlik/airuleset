"""Tests for the #94 A/B experiment harness (scripts/rules_ab_experiment.py).

The harness decides what "the ruleset" means for the measurement, so its
choices have to be pinned: the minimal condition must really be minimal, the
enforcing hooks must survive in both conditions, and the oracle must be
extracted from the commit that actually shipped the real fix.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "rules_ab_experiment.py"


def _load():
    spec = importlib.util.spec_from_file_location("rules_ab_experiment", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolve their module from sys.modules during class creation
    sys.modules["rules_ab_experiment"] = mod
    spec.loader.exec_module(mod)
    return mod


ab = _load()


# --- the minimal condition is genuinely minimal ---------------------------


def test_minimal_claude_md_imports_only_the_five_modules():
    text = ab.minimal_claude_md()
    imports = [ln for ln in text.splitlines() if ln.startswith("@")]
    assert len(imports) == 5
    for module in ab.MINIMAL_MODULES:
        assert f"@~/devel/airuleset/{module}" in imports


def test_minimal_condition_modules_all_exist_on_disk():
    for module in ab.MINIMAL_MODULES:
        assert (REPO / module).is_file(), f"{module} referenced but missing"


def test_minimal_claude_md_is_far_smaller_than_the_full_prefix():
    """Not the full file with a note saying to ignore it."""
    minimal_bodies = sum((REPO / m).stat().st_size for m in ab.MINIMAL_MODULES)
    full = REPO / "profiles"
    all_modules = list((REPO / "modules").rglob("*.md"))
    full_bodies = sum(p.stat().st_size for p in all_modules)
    assert full.is_dir()
    assert minimal_bodies < full_bodies * 0.25


# --- enforcement survives in both conditions ------------------------------


def _settings_with(commands):
    return {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": c} for c in commands]}
            ]
        }
    }


def test_strip_hooks_removes_only_the_named_scripts():
    settings = _settings_with(
        [
            "bash ~/devel/airuleset/hooks/notify-discord.sh",
            "bash ~/devel/airuleset/hooks/block-test-skips.sh",
        ]
    )
    out = ab.strip_hooks(settings, ("notify-discord",))
    kept = [h["command"] for h in out["hooks"]["PreToolUse"][0]["hooks"]]
    assert kept == ["bash ~/devel/airuleset/hooks/block-test-skips.sh"]


def test_strip_hooks_prunes_a_group_that_becomes_empty():
    settings = _settings_with(["bash ~/devel/airuleset/hooks/notify-discord.sh"])
    out = ab.strip_hooks(settings, ("notify-discord",))
    assert "PreToolUse" not in out.get("hooks", {})


def test_the_rule_delivery_hook_is_dropped_only_in_the_minimal_condition():
    """inject-situational-rule injects rule BODIES; it blocks nothing."""
    assert ab.RULE_DELIVERY_HOOK == "inject-situational-rule"
    assert ab.RULE_DELIVERY_HOOK not in ab.NEUTRALISED_HOOKS
    hook = REPO / "hooks" / f"{ab.RULE_DELIVERY_HOOK}.sh"
    assert hook.is_file()
    assert "NEVER blocks" in hook.read_text()


def test_no_blocking_gate_is_neutralised_in_either_condition():
    dropped = set(ab.NEUTRALISED_HOOKS) | {ab.RULE_DELIVERY_HOOK}
    for name in dropped:
        assert not name.startswith("block-"), f"{name} is an enforcement gate"
        assert not name.startswith("stop-check-"), f"{name} is an enforcement gate"


# --- the tasks are real, replayable tickets -------------------------------


def test_every_task_replays_a_real_commit_pair():
    for issue, task in ab.TASKS.items():
        for sha in (task.red, task.green):
            out = subprocess.run(
                ["git", "cat-file", "-t", sha],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            assert out.stdout.strip() == "commit", f"#{issue}: {sha} is not a commit"
        assert task.base == f"{task.red}^"


def test_the_oracle_test_file_is_the_one_the_red_commit_touched():
    for issue, task in ab.TASKS.items():
        changed = subprocess.run(
            ["git", "show", "--stat", "--format=", "--name-only", task.red],
            cwd=REPO,
            capture_output=True,
            text=True,
        ).stdout.split()
        assert task.oracle_test in changed, f"#{issue}: oracle not in the red commit"


def test_the_red_commit_only_adds_tests_never_the_fix():
    """If the red commit carried the fix, the oracle would be worthless."""
    for issue, task in ab.TASKS.items():
        changed = subprocess.run(
            ["git", "show", "--format=", "--name-only", task.red],
            cwd=REPO,
            capture_output=True,
            text=True,
        ).stdout.split()
        assert changed, f"#{issue}: red commit touched nothing"
        for path in changed:
            assert path.startswith("tests/"), f"#{issue}: red commit touched {path}"


def test_new_test_functions_extracts_added_tests_only():
    diff = (
        "--- a/tests/x.py\n"
        "+++ b/tests/x.py\n"
        "+def test_added_one():\n"
        "+    pass\n"
        " def test_untouched():\n"
        "-def test_removed():\n"
    )
    assert ab.new_test_functions(diff) == ["test_added_one"]


def test_each_task_has_at_least_one_extractable_oracle_test():
    for issue, task in ab.TASKS.items():
        diff = subprocess.run(
            ["git", "show", task.red, "--", task.oracle_test],
            cwd=REPO,
            capture_output=True,
            text=True,
        ).stdout
        assert ab.new_test_functions(diff), f"#{issue}: no oracle test names found"


def test_issue_bodies_are_committed_so_the_prompt_is_reproducible():
    for issue in ab.TASKS:
        body = REPO / "audits" / "ab94" / f"issue-{issue}.md"
        assert body.is_file() and body.stat().st_size > 200


# --- the prompt is identical across conditions ----------------------------


def test_prompt_does_not_depend_on_the_condition():
    task = ab.TASKS[88]
    body = (REPO / "audits" / "ab94" / "issue-88.md").read_text()
    assert ab.build_prompt(task, body) == ab.build_prompt(task, body)


def test_prompt_forbids_network_and_push():
    task = ab.TASKS[88]
    prompt = ab.build_prompt(task, "body")
    assert "do not push" in prompt
    assert "do not run `gh`" in prompt
    assert "#88" in prompt


# --- metric extraction ----------------------------------------------------


def test_parse_run_json_pulls_turns_cost_and_tokens():
    got = ab.parse_run_json(
        {
            "num_turns": 12,
            "duration_ms": 90000,
            "total_cost_usd": 0.4213456,
            "is_error": False,
            "subtype": "success",
            "session_id": "abc",
            "usage": {
                "input_tokens": 30,
                "output_tokens": 4000,
                "cache_read_input_tokens": 1200000,
                "cache_creation_input_tokens": 90000,
            },
        }
    )
    assert got["num_turns"] == 12
    assert got["cost_usd"] == 0.4213
    assert got["cache_read_tokens"] == 1200000
    assert got["is_error"] is False


def test_parse_run_json_survives_a_truncated_payload():
    got = ab.parse_run_json({})
    assert got["num_turns"] == 0 and got["cost_usd"] == 0.0


def test_count_hook_blocks_groups_by_hook_name():
    text = (
        "[block-main-implementation] denied\n"
        "noise\n"
        "[block-main-implementation] denied again\n"
        "[stop-check-status-marker] missing marker\n"
    )
    assert ab.count_hook_blocks(text) == {
        "block-main-implementation": 2,
        "stop-check-status-marker": 1,
    }


def test_summarise_aggregates_per_condition():
    agg = ab.summarise(
        [
            {
                "condition": "A",
                "oracle_pass": True,
                "suite_green": True,
                "wrote_own_test": True,
                "num_turns": 10,
                "cost_usd": 1.0,
                "duration_ms": 1000,
                "output_tokens": 100,
                "cache_read_tokens": 5,
                "hook_blocks": {"block-x": 2},
            },
            {
                "condition": "B",
                "oracle_pass": False,
                "suite_green": True,
                "wrote_own_test": False,
                "num_turns": 4,
                "cost_usd": 0.5,
                "duration_ms": 400,
                "output_tokens": 40,
                "cache_read_tokens": 1,
                "hook_blocks": {},
            },
        ]
    )
    assert agg["A"]["oracle_pass"] == 1 and agg["B"]["oracle_pass"] == 0
    assert agg["A"]["hook_blocks"] == 2
    assert agg["B"]["runs"] == 1


# --- replicates (#106: n=1 per cell is not enough to separate a real effect
# from ordinary run-to-run variance) --------------------------------------


def test_slot_name_rep1_keeps_the_original_unsuffixed_naming():
    """Round-1 (#94) artifacts are already committed unsuffixed — rep=1 must
    stay addressable with no rename."""
    assert ab.slot_name(88, "A", 1) == "88-A"
    assert ab.slot_name(88, "A") == "88-A"


def test_slot_name_rep2_appends_a_replicate_suffix():
    assert ab.slot_name(88, "A", 2) == "88-A-r2"
    assert ab.slot_name(96, "B", 3) == "96-B-r3"


def test_slot_name_different_reps_never_collide():
    names = {ab.slot_name(88, "A", r) for r in range(1, 5)}
    assert len(names) == 4


def test_make_tree_uses_the_replicate_slot(tmp_path):
    task = ab.TASKS[88]
    t1 = ab.make_tree(tmp_path, task, "A", rep=1)
    t2 = ab.make_tree(tmp_path, task, "A", rep=2)
    assert t1 != t2
    assert t1.name == "run-88-A"
    assert t2.name == "run-88-A-r2"


def test_summarise_by_task_never_pools_different_tickets():
    """Pooling task 88 and task 96 into one A-vs-B total can hide a real
    per-ticket effect behind an unrelated one — each task gets its own
    aggregate."""
    records = [
        {"task": 88, "condition": "A", "oracle_pass": True, "suite_green": True,
         "wrote_own_test": True, "num_turns": 10, "cost_usd": 1.0,
         "duration_ms": 1000, "output_tokens": 100, "cache_read_tokens": 5,
         "hook_blocks": {}},
        {"task": 88, "condition": "B", "oracle_pass": False, "suite_green": True,
         "wrote_own_test": False, "num_turns": 4, "cost_usd": 0.5,
         "duration_ms": 400, "output_tokens": 40, "cache_read_tokens": 1,
         "hook_blocks": {}},
        {"task": 96, "condition": "A", "oracle_pass": False, "suite_green": True,
         "wrote_own_test": True, "num_turns": 20, "cost_usd": 2.0,
         "duration_ms": 2000, "output_tokens": 200, "cache_read_tokens": 9,
         "hook_blocks": {}},
        {"task": 96, "condition": "B", "oracle_pass": True, "suite_green": True,
         "wrote_own_test": False, "num_turns": 8, "cost_usd": 0.7,
         "duration_ms": 700, "output_tokens": 70, "cache_read_tokens": 2,
         "hook_blocks": {}},
    ]
    by_task = ab.summarise_by_task(records)
    assert set(by_task) == {"88", "96"}
    assert by_task["88"]["A"]["oracle_pass"] == 1
    assert by_task["88"]["B"]["oracle_pass"] == 0
    assert by_task["96"]["A"]["oracle_pass"] == 0
    assert by_task["96"]["B"]["oracle_pass"] == 1
    # a naive pool of all four records would read 2/4 each condition, masking
    # that 88 and 96 disagree in OPPOSITE directions
    pooled = ab.summarise(records)
    assert pooled["A"]["oracle_pass"] == 1 and pooled["B"]["oracle_pass"] == 1


def test_summarise_by_task_reports_replicate_count_per_cell():
    records = [
        {"task": 88, "condition": "A", "rep": 1, "oracle_pass": True,
         "suite_green": True, "wrote_own_test": True, "num_turns": 10,
         "cost_usd": 1.0, "duration_ms": 1000, "output_tokens": 100,
         "cache_read_tokens": 5, "hook_blocks": {}},
        {"task": 88, "condition": "A", "rep": 2, "oracle_pass": True,
         "suite_green": True, "wrote_own_test": True, "num_turns": 12,
         "cost_usd": 1.1, "duration_ms": 1100, "output_tokens": 110,
         "cache_read_tokens": 6, "hook_blocks": {}},
    ]
    by_task = ab.summarise_by_task(records)
    assert by_task["88"]["A"]["runs"] == 2


def test_report_writes_a_by_task_breakdown(tmp_path):
    (tmp_path / "graded-88-A.json").write_text(json.dumps({
        "task": 88, "condition": "A", "oracle_pass": True, "suite_green": True,
        "wrote_own_test": True, "num_turns": 3, "cost_usd": 0.1,
        "duration_ms": 10, "output_tokens": 1, "cache_read_tokens": 1,
        "hook_blocks": {},
    }))
    (tmp_path / "graded-88-A-r2.json").write_text(json.dumps({
        "task": 88, "condition": "A", "rep": 2, "oracle_pass": True,
        "suite_green": True, "wrote_own_test": True, "num_turns": 4,
        "cost_usd": 0.2, "duration_ms": 20, "output_tokens": 2,
        "cache_read_tokens": 2, "hook_blocks": {},
    }))
    ab.report(tmp_path)
    out = json.loads((tmp_path / "report.json").read_text())
    assert out["by_task"]["88"]["A"]["runs"] == 2
    assert out["aggregate"]["A"]["runs"] == 2


def test_run_and_grade_cli_accept_rep():
    out = subprocess.run(
        ["python3", str(SCRIPT), "run", "--help"], capture_output=True, text=True
    )
    assert "--rep" in out.stdout
    out = subprocess.run(
        ["python3", str(SCRIPT), "grade", "--help"], capture_output=True, text=True
    )
    assert "--rep" in out.stdout


# --- isolation ------------------------------------------------------------


def test_harness_never_reads_the_real_projects_dir():
    src = SCRIPT.read_text()
    assert 'Path.home() / ".claude" / "projects"' not in src
    assert "~/.claude/projects" not in src


def test_harness_uses_the_raw_binary_not_the_bashrc_wrapper():
    src = SCRIPT.read_text()
    assert ".local/share/claude/versions" in src
    assert "CLAUDE_CONFIG_DIR" in src


def test_run_trees_have_no_push_target():
    src = SCRIPT.read_text()
    assert '"git", "remote", "remove", "origin"' in src
    assert 'env["GH_TOKEN"] = ""' in src


@pytest.mark.parametrize("cmd", ["bootstrap", "run", "grade", "report"])
def test_cli_accepts_the_documented_commands(cmd):
    out = subprocess.run(
        ["python3", str(SCRIPT), cmd, "--help"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0 or "--root" in out.stderr + out.stdout


def test_report_writes_json(tmp_path):
    (tmp_path / "graded-1-A.json").write_text(
        json.dumps(
            {
                "condition": "A",
                "oracle_pass": True,
                "suite_green": True,
                "wrote_own_test": True,
                "num_turns": 3,
                "cost_usd": 0.1,
                "duration_ms": 10,
                "output_tokens": 1,
                "cache_read_tokens": 1,
                "hook_blocks": {},
            }
        )
    )
    ab.report(tmp_path)
    out = json.loads((tmp_path / "report.json").read_text())
    assert out["aggregate"]["A"]["runs"] == 1
