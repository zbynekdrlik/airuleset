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


# ---------------------------------------------------------------------------
# count_hook_blocks: structural parse, not a text-blob regex (#108 item 4)
#
# Every fixture below is a shape OBSERVED in the real transcript corpus
# (8,058 files / 1,653,949 entries under ~/.claude/projects), not invented.
# The decoys matter more than the positives: the bracket regex this replaced
# scored 0 genuine denials and 19 spurious matches corpus-wide, and the
# "obvious" `"decision":"block"` grep scored 0 genuine out of 258 hits.
# ---------------------------------------------------------------------------

DENIAL_PREFIX = "PreToolUse:Bash hook error: "
STOP_PREFIX = "Stop hook feedback: "
HOOK_CMD = "bash ~/devel/airuleset/hooks/block-tier0-local-build.sh"
# assembled, never written as one literal, so this file can never match itself
JSON_BLOCK_DECOY = '{"decision"' + ': ' + '"block"' + ', "reason": "..."}'


def _entry(payload):
    return json.dumps(payload)


def _denial(cmd=HOOK_CMD, stderr="BLOCKED: heavy local build in a Tier-0 project"):
    """A genuine PreToolUse denial, exactly as Claude Code writes it."""
    return _entry({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_x",
                "is_error": True,
                "content": DENIAL_PREFIX + "[" + cmd + "]: " + stderr,
            }],
        },
    })


def _stop(body):
    return _entry({
        "type": "user",
        "message": {"role": "user", "content": STOP_PREFIX + body},
    })


def _model_read_of_hook_source():
    """DECOY: the model cats the hook's own source, which contains the JSON shape.

    This is the 20-50x over-count the naive grep produces on a hook-fix ticket.
    Note is_error is absent: the READ SUCCEEDED.
    """
    return _entry({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_y",
                "content": "1\t#!/usr/bin/env bash\n2\techo " + JSON_BLOCK_DECOY,
            }],
        },
    })


def _model_ran_the_hook_itself():
    """DECOY: the run EXECUTES the hook in a repro harness and captures its output.

    Observed verbatim in #106's run-96-A-r2 transcript. The bytes are a real
    hook block payload; the meaning is not a denial of the model's own call.
    Only `is_error` separates the two, which is why a repaired regex cannot.
    """
    return _entry({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_z",
                "content": "2686 BLOCKED " + JSON_BLOCK_DECOY,
            }],
        },
    })


def _assistant_quoting_a_denial():
    """DECOY: the model writes the denial text itself, in an assistant turn."""
    return _entry({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text",
                         "text": DENIAL_PREFIX + "[" + HOOK_CMD + "]: BLOCKED"}],
        },
    })


def _task_notification_quoting_stop_feedback():
    """DECOY: a task-notification whose nested excerpt contains the stop phrase.

    Observed in session 7caa03ad. Excluded by anchoring at position 0.
    """
    return _entry({
        "type": "user",
        "message": {"role": "user",
                    "content": "<task-notification> ... " + STOP_PREFIX + "blah"},
    })


def _transcript(tmp_path, *lines, name="session.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_count_hook_blocks_counts_a_real_pretooluse_denial(tmp_path):
    t = _transcript(tmp_path, _denial())
    assert ab.count_hook_blocks([t]) == {"block-tier0-local-build": 1}


def test_count_hook_blocks_keys_by_hook_name_and_sums_repeats(tmp_path):
    other = "bash ~/devel/airuleset/hooks/pre-write-script-check.sh"
    t = _transcript(tmp_path, _denial(), _denial(), _denial(cmd=other))
    assert ab.count_hook_blocks([t]) == {
        "block-tier0-local-build": 2,
        "pre-write-script-check": 1,
    }


def test_count_hook_blocks_ignores_the_model_reading_or_running_the_hook(tmp_path):
    """The whole point: identical bytes, opposite meaning, told apart by structure."""
    t = _transcript(
        tmp_path,
        _model_read_of_hook_source(),
        _model_ran_the_hook_itself(),
        _assistant_quoting_a_denial(),
        _task_notification_quoting_stop_feedback(),
    )
    assert ab.count_hook_blocks([t]) == {}


def test_count_hook_blocks_finds_the_signal_amongst_the_decoys(tmp_path):
    t = _transcript(
        tmp_path,
        _model_read_of_hook_source(),
        _denial(),
        _model_ran_the_hook_itself(),
        _assistant_quoting_a_denial(),
    )
    assert ab.count_hook_blocks([t]) == {"block-tier0-local-build": 1}


def test_count_hook_blocks_counts_stop_hook_rejections(tmp_path):
    """#108 hypothesised this shape was unobservable; it is not (refuted in-ticket)."""
    t = _transcript(tmp_path, _stop("Hard violations detected in your message"))
    assert ab.count_hook_blocks([t]) == {"stop-hook-feedback": 1}


def test_count_hook_blocks_attributes_a_named_stop_hook(tmp_path):
    t = _transcript(
        tmp_path,
        _stop("[bash ~/devel/airuleset/hooks/stop-check-ci.sh]: STOP BLOCKED: CI red"),
    )
    assert ab.count_hook_blocks([t]) == {"stop-check-ci": 1}


def test_count_hook_blocks_reads_every_transcript_without_joining_them(tmp_path):
    """No trailing newline must not fuse two files into one corrupt entry."""
    a = tmp_path / "a.jsonl"
    a.write_text(_denial(), encoding="utf-8")          # deliberately unterminated
    b = tmp_path / "b.jsonl"
    b.write_text(_denial(), encoding="utf-8")
    assert ab.count_hook_blocks([a, b]) == {"block-tier0-local-build": 2}


def test_count_hook_blocks_survives_a_truncated_or_binary_line(tmp_path):
    t = _transcript(tmp_path, "{not json", _denial(), "")
    assert ab.count_hook_blocks([t]) == {"block-tier0-local-build": 1}


def test_count_hook_blocks_ignores_the_routine_stop_hook_summary_field(tmp_path):
    """preventedContinuation is false on all 21,990 corpus occurrences (#109)."""
    line = _entry({
        "type": "system",
        "stop_hook_summary": {"preventedContinuation": False},
    })
    t = _transcript(tmp_path, line)
    assert ab.count_hook_blocks([t]) == {}


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


def test_make_tree_fails_loud_when_the_clone_fails(tmp_path, monkeypatch):
    """CI run 32836799214 (#683 run 3): the clone died in the container
    (dubious ownership on <workspace>/.git + a fatal cross-device hardlink)
    but _run() swallowed the non-zero rc — the NEXT command then raised a
    misleading FileNotFoundError on the never-created tree, two steps away
    from the real cause. script-failure-policy: the clone failure itself
    must raise, carrying git's own stderr."""
    monkeypatch.setattr(ab, "REPO", tmp_path / "definitely-not-a-repo")
    with pytest.raises(RuntimeError, match="clone"):
        ab.make_tree(tmp_path, ab.TASKS[88], "A", rep=1)


def test_make_tree_clone_never_demands_hardlinks(tmp_path, monkeypatch):
    """An EXPLICIT `--local` makes a cross-filesystem hardlink failure FATAL
    (`Invalid cross-device link` — the CI container's bind-mounted /__w
    workspace vs its overlay /tmp), while the default local-path clone falls
    back to copying; on a same-fs box git hardlinks by default anyway, so
    dropping the flag costs nothing locally. Probed live in docker
    python:3.12 (#683 run-3 debug). Lock the clone argv free of --local."""
    calls = []

    def fake_run(cmd, cwd=None, env=None, timeout=600):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ab, "_run", fake_run)
    ab.make_tree(tmp_path, ab.TASKS[88], "A", rep=1)
    clone = calls[0]
    assert clone[:2] == ["git", "clone"]
    assert "--no-checkout" in clone
    assert "--local" not in clone


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


# --- item 11 (#95) — condition C: architecture-first.md + rewordings-clause
# ablation -------------------------------------------------------------


def test_condition_c_is_registered():
    assert "C" in ab.CONDITIONS


def test_strip_rewordings_coda_removes_a_standalone_clause_sentence():
    line = "Applies to all rewordings and semantic equivalents."
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is True
    assert new_line == ""


def test_strip_rewordings_coda_removes_only_the_coda_after_a_period():
    line = "Do the real thing. Applies to all rewordings and semantic equivalents."
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is True
    assert new_line == "Do the real thing."


def test_strip_rewordings_coda_removes_only_the_coda_after_a_semicolon():
    line = "The intent is banned; applies to all rewordings and semantic equivalents."
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is True
    # the trailing semicolon (a CONNECTOR, not a sentence terminator) is
    # stripped too -- "The intent is banned;" would read as a dangling
    # leftover with nothing left to connect to (#95 item 11 adversarial
    # review, 🔵 finding).
    assert new_line == "The intent is banned"


def test_strip_rewordings_coda_never_leaves_a_dangling_connector_real_corpus_shape():
    # the exact real modules/core/autonomous-verification.md:76 shape --
    # a semicolon boundary immediately preceding the clause.
    line = (
        'The intent — not the exact wording — is banned; applies to all '
        "rewordings and semantic equivalents."
    )
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is True
    assert new_line == "The intent — not the exact wording — is banned"
    assert not new_line.endswith(";")
    assert not new_line.endswith("—")


def test_strip_rewordings_coda_keeps_the_terminal_period_never_strips_it():
    # a period is the PREVIOUS sentence's own terminator, not a connector --
    # it must survive, unlike a trailing ";"/"—".
    line = "Do the real thing. Applies to all rewordings and semantic equivalents."
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is True
    assert new_line == "Do the real thing."
    assert new_line.endswith(".")


def test_strip_rewordings_coda_removes_only_the_coda_after_an_em_dash():
    line = (
        "Applies to all rewordings and semantic equivalents — the intent: "
        "do X, never Y."
    )
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is True
    assert new_line == ""


def test_strip_rewordings_coda_strips_a_dangling_trailing_em_dash_too():
    # the exact real modules/core/autonomous-quality-discipline.md:63 shape
    # -- an em-dash boundary immediately preceding the clause, with real
    # text before it (unlike the empty-result em-dash case above).
    line = (
        "The intent is banned, not the wording — applies to all "
        "rewordings and semantic equivalents."
    )
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is True
    assert new_line == "The intent is banned, not the wording"
    assert not new_line.endswith("—")


def test_strip_rewordings_coda_drops_a_bullet_only_line_entirely():
    line = "- Applies to all rewordings and semantic equivalents (extra detail)."
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is True
    assert new_line == ""


def test_strip_rewordings_coda_never_touches_a_double_quoted_mention():
    line = (
        '- **End with "applies to all rewordings and semantic equivalents"** '
        "— prevents 4.8 from taking bullet-point lists as exhaustive."
    )
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is False
    assert new_line == line


def test_strip_rewordings_coda_never_touches_a_backticked_mention():
    line = "See `applies to all rewordings and semantic equivalents` in the docs."
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is False
    assert new_line == line


def test_strip_rewordings_coda_leaves_a_clause_free_line_untouched():
    line = "Follow existing patterns in the codebase."
    new_line, changed = ab.strip_rewordings_coda(line)
    assert changed is False
    assert new_line == line


def test_ablate_module_text_counts_every_genuine_strip():
    text = (
        "First sentence stays.\n"
        "Applies to all rewordings and semantic equivalents.\n"
        'Mentioning "applies to all rewordings and semantic equivalents" stays put.\n'
        "Second real one. Applies to all rewordings and semantic equivalents.\n"
    )
    new_text, stripped = ab.ablate_module_text(text)
    assert stripped == 2
    assert "Mentioning" in new_text
    assert '"applies to all rewordings and semantic equivalents"' in new_text
    lines = new_text.splitlines()
    assert lines[0] == "First sentence stays."
    assert lines[1] == ""
    assert lines[3] == "Second real one."


def test_ablate_module_text_on_the_real_architecture_first_module_finds_none():
    """architecture-first.md itself carries no rewordings clause — it is
    dropped from the prefix outright, not ablated, so this is a genuine
    zero, never a silent parsing failure."""
    text = (REPO / ab.ARCHITECTURE_FIRST_MODULE).read_text(encoding="utf-8")
    _, stripped = ab.ablate_module_text(text)
    assert stripped == 0


def _fake_real_claude_md_source(tmp_path):
    """A scratch ``~/.claude``-shaped dir whose CLAUDE.md imports two REAL
    repo modules (architecture-first.md + one known to carry a genuine
    rewordings-clause use) plus a settings.json bootstrap() also needs."""
    real = tmp_path / "fake-real"
    real.mkdir()
    (real / "CLAUDE.md").write_text(
        "# fake\n"
        f"@~/devel/airuleset/{ab.ARCHITECTURE_FIRST_MODULE}\n"
        "@~/devel/airuleset/modules/core/tdd-workflow.md\n",
        encoding="utf-8",
    )
    return real


def test_write_architecture_ablated_claude_md_drops_the_architecture_first_import(tmp_path):
    real = _fake_real_claude_md_source(tmp_path)
    cfg = tmp_path / "config-C"
    cfg.mkdir()
    ab.write_architecture_ablated_claude_md(real, cfg)
    new_md = (cfg / "CLAUDE.md").read_text(encoding="utf-8")
    assert ab.ARCHITECTURE_FIRST_MODULE not in new_md


def test_write_architecture_ablated_claude_md_rewires_the_other_import_to_an_ablated_copy(tmp_path):
    real = _fake_real_claude_md_source(tmp_path)
    cfg = tmp_path / "config-C"
    cfg.mkdir()
    ab.write_architecture_ablated_claude_md(real, cfg)
    new_md = (cfg / "CLAUDE.md").read_text(encoding="utf-8")
    dest = cfg / "ablated-modules" / "modules" / "core" / "tdd-workflow.md"
    assert f"@{dest}" in new_md
    assert dest.is_file()
    ablated_text = dest.read_text(encoding="utf-8")
    # a genuine use, stripped -- no live "Applies to all rewordings..." left
    for line in ablated_text.splitlines():
        m = ab._REWORDINGS_PHRASE_RE.search(line)
        assert m is None or ab._is_clause_mention(line, m.start())


def test_write_architecture_ablated_claude_md_never_writes_to_the_real_module(tmp_path):
    """Safety-critical: the function must ONLY ever write under ``cfg`` —
    the real ``modules/core/tdd-workflow.md`` this repo ships must be
    byte-identical before and after the call."""
    real_module = REPO / "modules" / "core" / "tdd-workflow.md"
    before = real_module.read_bytes()
    real = _fake_real_claude_md_source(tmp_path)
    cfg = tmp_path / "config-C"
    cfg.mkdir()
    ab.write_architecture_ablated_claude_md(real, cfg)
    after = real_module.read_bytes()
    assert before == after


def test_write_architecture_ablated_claude_md_returns_touched_and_stripped_counts(tmp_path):
    real = _fake_real_claude_md_source(tmp_path)
    cfg = tmp_path / "config-C"
    cfg.mkdir()
    touched, stripped = ab.write_architecture_ablated_claude_md(real, cfg)
    assert touched == 1  # only tdd-workflow.md -- architecture-first.md was dropped
    assert stripped >= 1  # tdd-workflow.md carries at least one genuine use


def test_bootstrap_builds_condition_c_alongside_a_and_b(tmp_path):
    """bootstrap() reads the REAL ~/.claude — a real profile must exist on
    this box (true for every managed box, and this session's own)."""
    real = Path.home() / ".claude"
    if not (real / "settings.json").is_file():
        pytest.skip("no real ~/.claude/settings.json on this box")
    if not (real / "CLAUDE.md").is_file():
        pytest.skip("no real ~/.claude/CLAUDE.md on this box")
    root = tmp_path / "ab95"
    ab.bootstrap(root)
    assert (root / "config-C" / "CLAUDE.md").is_file()
    c_md = (root / "config-C" / "CLAUDE.md").read_text(encoding="utf-8")
    assert ab.ARCHITECTURE_FIRST_MODULE not in c_md
    a_md = (root / "config-A" / "CLAUDE.md").read_text(encoding="utf-8")
    assert ab.ARCHITECTURE_FIRST_MODULE in a_md
    # condition C keeps full rule delivery (unlike B) -- only the CLAUDE.md
    # content differs from A, never the hook wiring.
    c_settings = json.loads((root / "config-C" / "settings.json").read_text())
    b_settings = json.loads((root / "config-B" / "settings.json").read_text())
    a_settings = json.loads((root / "config-A" / "settings.json").read_text())
    assert c_settings == a_settings
    assert c_settings != b_settings
