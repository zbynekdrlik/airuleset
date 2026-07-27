#!/usr/bin/env python3
"""A/B experiment harness — full ruleset vs minimal ruleset (airuleset #94).

The question this settles is behavioural, not descriptive: does the always-on
rule prefix make a current-generation model produce BETTER, WORSE, or
indistinguishable work on a real task?

Method — retro-ticket replay with an objective oracle:

  * Every task is a REAL closed issue of this repo, replayed on the commit
    immediately BEFORE its fix landed (``<red>^``).
  * Correctness is decided by the RED test that actually shipped with the real
    fix: check that test into the run's tree afterwards and run it.  Pass/fail,
    no LLM judgement on the primary axis.
  * Both conditions get a byte-identical prompt and an identical scratch
    profile; only the rule TEXT differs.

Definition of "the ruleset" used here (stated so the result cannot be
misread): every surface that delivers rule TEXT into the context without the
model asking for it — the ``CLAUDE.md`` prefix with expanded module bodies, and
``inject-situational-rule.sh`` (a PreToolUse hook whose only job is to inject
converted rule bodies).  Every ENFORCING hook — the ``block-*`` gates, the
``stop-check-*`` gates, ``pre-push-*`` — stays enabled in BOTH conditions.

Measured layer: the MAIN-session prefix.  It is the only layer a main session
and a dispatched worker share (#104: a worker inherits the global CLAUDE.md
with module bodies but no skill bodies; #105: ``paths:``-scoped rules never
reach a worker), and it is the layer the complaint is about.

Usage::

    python3 scripts/rules_ab_experiment.py bootstrap --root /tmp/ab94
    python3 scripts/rules_ab_experiment.py run  --root /tmp/ab94 --task 88 --condition A
    python3 scripts/rules_ab_experiment.py grade --root /tmp/ab94 --task 88 --condition A
    python3 scripts/rules_ab_experiment.py report --root /tmp/ab94
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Modules kept in the MINIMAL condition: the things a model demonstrably does
# NOT do on its own (machine topology it cannot know, this repo's branch model,
# what it may deploy without asking, the version-bump-first ordering, and the
# no-backwards-compat stance).  Everything else is removed.
MINIMAL_MODULES = (
    "modules/core/machine-identities.md",
    "modules/git/two-branch-workflow.md",
    "modules/quality/approval-scope.md",
    "modules/core/version-bumping.md",
    "modules/quality/mvp-philosophy.md",
)

# Hooks disabled in BOTH conditions: they reach the real user's phone or the
# real compaction bookkeeping.  Nothing about them is under measurement.
NEUTRALISED_HOOKS = ("notify-discord", "notify-api-error", "notify-compact-request")

# Hook disabled in the MINIMAL condition ONLY: its sole function is to inject
# rule bodies, so leaving it on would make "minimal" not minimal.  It blocks
# nothing, so removing it removes no enforcement.
RULE_DELIVERY_HOOK = "inject-situational-rule"


@dataclass(frozen=True)
class Task:
    """One replayed ticket."""

    issue: int
    red: str  # commit that added the failing test (the oracle)
    green: str  # commit that made it pass (ground truth, never shown to a run)
    oracle_test: str  # path of the test file the oracle lives in
    title: str

    @property
    def base(self) -> str:
        """The commit a run starts from: neither test nor fix present."""
        return f"{self.red}^"


TASKS: dict[int, Task] = {
    t.issue: t
    for t in (
        Task(
            issue=88,
            red="8a298b3",
            green="501c4be",
            oracle_test="tests/test_main_implementation_guard.py",
            title="block-main-implementation: klasifikator nevidi do $( ... )",
        ),
        Task(
            issue=96,
            red="f5a65e0",
            green="72b1442",
            oracle_test="tests/test_quality_bypass_gate.py",
            title="stop-check-prose-violations: gate blokuje spravu, ktora o frazach iba REFERUJE",
        ),
        Task(
            issue=86,
            red="5edf056",
            green="c5a1bb4",
            oracle_test="tests/test_airuleset.py",
            title="block-test-skips.sh false-blocks every push in a 3-branch repo",
        ),
    )
}

CONDITIONS = ("A", "B")


# --------------------------------------------------------------------------
# pure helpers (unit-tested)
# --------------------------------------------------------------------------


def minimal_claude_md(modules: tuple[str, ...] = MINIMAL_MODULES) -> str:
    """Render the MINIMAL condition's CLAUDE.md.

    A genuinely reduced file with real ``@import`` lines — never the full file
    with a note telling the model to ignore it.
    """
    lines = [
        "# User-Wide Claude Code Instructions",
        "",
        "# A/B experiment condition B (airuleset #94) — minimal ruleset.",
        "",
    ]
    lines += [f"@~/devel/airuleset/{m}" for m in modules]
    lines.append("")
    return "\n".join(lines)


def strip_hooks(settings: dict, drop: tuple[str, ...]) -> dict:
    """Return ``settings`` with every hook whose command names a dropped script removed.

    Empty matcher groups are pruned so Claude Code never sees a group with an
    empty ``hooks`` list.
    """
    out = json.loads(json.dumps(settings))
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = [
                h
                for h in entries
                if not any(name in str(h.get("command", "")) for name in drop)
            ]
            if kept:
                new_group = dict(group)
                new_group["hooks"] = kept
                kept_groups.append(new_group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event)
    return out


def parse_run_json(payload: dict) -> dict:
    """Extract the cost/effort metrics from a ``--output-format json`` result."""
    usage = payload.get("usage") or {}
    return {
        "is_error": bool(payload.get("is_error")),
        "subtype": payload.get("subtype") or "",
        "num_turns": int(payload.get("num_turns") or 0),
        "duration_ms": int(payload.get("duration_ms") or 0),
        "cost_usd": round(float(payload.get("total_cost_usd") or 0.0), 4),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "cache_creation_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "session_id": payload.get("session_id") or "",
    }


HOOK_BLOCK_RE = re.compile(r"\[(block|stop-check|pre-push|pre-write|pre-deploy)-[a-z0-9-]+\]")


def count_hook_blocks(transcript_text: str) -> dict[str, int]:
    """Count hook feedback markers seen in a run transcript, per hook name."""
    counts: dict[str, int] = {}
    for match in HOOK_BLOCK_RE.finditer(transcript_text):
        name = match.group(0).strip("[]")
        counts[name] = counts.get(name, 0) + 1
    return counts


def new_test_functions(diff_text: str) -> list[str]:
    """Names of test functions ADDED by a diff (the oracle's own tests)."""
    return re.findall(r"^\+\s*def (test_[A-Za-z0-9_]+)", diff_text, flags=re.MULTILINE)


def build_prompt(task: Task, issue_body: str) -> str:
    """The byte-identical prompt both conditions receive."""
    return (
        f"Work GitHub issue #{task.issue} in this repository "
        "(a local checkout of zbynekdrlik/airuleset).\n\n"
        "This checkout has NO access to GitHub: do not run `gh`, do not push, "
        "do not open a PR. Do the work locally and commit it to the current branch.\n\n"
        "The local gate is `python -m pytest tests/ -q` and `ruff check .` — both must "
        "be green before the work is done.\n\n"
        f"--- ISSUE #{task.issue}: {task.title} ---\n"
        f"{issue_body.strip()}\n"
        "--- END ISSUE ---\n"
    )


def summarise(results: list[dict]) -> dict:
    """Aggregate per-condition totals from a list of graded run records."""
    agg: dict[str, dict] = {}
    for r in results:
        cond = r["condition"]
        slot = agg.setdefault(
            cond,
            {
                "runs": 0,
                "oracle_pass": 0,
                "suite_green": 0,
                "wrote_own_test": 0,
                "turns": 0,
                "cost_usd": 0.0,
                "duration_ms": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "hook_blocks": 0,
            },
        )
        slot["runs"] += 1
        slot["oracle_pass"] += 1 if r.get("oracle_pass") else 0
        slot["suite_green"] += 1 if r.get("suite_green") else 0
        slot["wrote_own_test"] += 1 if r.get("wrote_own_test") else 0
        slot["turns"] += r.get("num_turns", 0)
        slot["cost_usd"] = round(slot["cost_usd"] + r.get("cost_usd", 0.0), 4)
        slot["duration_ms"] += r.get("duration_ms", 0)
        slot["output_tokens"] += r.get("output_tokens", 0)
        slot["cache_read_tokens"] += r.get("cache_read_tokens", 0)
        slot["hook_blocks"] += sum((r.get("hook_blocks") or {}).values())
    return agg


# --------------------------------------------------------------------------
# filesystem / process plumbing
# --------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None,
         timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def claude_binary() -> str:
    """The RAW binary — never the bashrc ``claude`` wrapper.

    The wrapper's ``_has_conversation()`` reads the REAL ``$HOME`` and would
    silently pick the wrong profile under ``CLAUDE_CONFIG_DIR``.
    """
    explicit = os.environ.get("AB_CLAUDE_BINARY")
    if explicit:
        return explicit
    versions = Path.home() / ".local/share/claude/versions"
    if versions.is_dir():
        picks = sorted(versions.iterdir(), key=lambda p: p.name)
        if picks:
            return str(picks[-1])
    return str(Path.home() / ".local/bin/claude")


def bootstrap(root: Path) -> None:
    """Build the two scratch profiles. Never touches the real ``~/.claude``."""
    real = Path.home() / ".claude"
    settings = json.loads((real / "settings.json").read_text())

    for cond in CONDITIONS:
        cfg = root / f"config-{cond}"
        cfg.mkdir(parents=True, exist_ok=True)

        drop = list(NEUTRALISED_HOOKS)
        if cond == "B":
            drop.append(RULE_DELIVERY_HOOK)
        (cfg / "settings.json").write_text(
            json.dumps(strip_hooks(settings, tuple(drop)), indent=2)
        )

        if cond == "A":
            shutil.copy2(real / "CLAUDE.md", cfg / "CLAUDE.md")
        else:
            (cfg / "CLAUDE.md").write_text(minimal_claude_md())

        for sub in ("skills", "agents"):
            src, dst = real / sub, cfg / sub
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)

        creds = real / ".credentials.json"
        if creds.exists():
            shutil.copy2(creds, cfg / ".credentials.json")

        # CC writes the top-level dotfile INSIDE the config dir when
        # CLAUDE_CONFIG_DIR is set — copy the real one so the session does not
        # hit fresh onboarding. MCP servers are stripped: they are identical
        # across conditions and only add startup cost and noise.
        top = json.loads((Path.home() / ".claude.json").read_text())
        top["mcpServers"] = {}
        top["projects"] = {}
        (cfg / ".claude.json").write_text(json.dumps(top))

        (cfg / "projects").mkdir(exist_ok=True)

    print(f"bootstrapped: {root}/config-A (full), {root}/config-B (minimal)")
    a_md = (root / "config-A" / "CLAUDE.md").read_text()
    b_md = (root / "config-B" / "CLAUDE.md").read_text()
    print(f"  CLAUDE.md bytes: A={len(a_md)} B={len(b_md)}")


def make_tree(root: Path, task: Task, cond: str) -> Path:
    """A standalone clone at the pre-fix commit, with no remote to push to."""
    tree = root / f"run-{task.issue}-{cond}"
    if tree.exists():
        shutil.rmtree(tree)
    _run(["git", "clone", "--local", "--no-checkout", str(REPO), str(tree)], timeout=600)
    _run(["git", "checkout", "-B", "ab-run", task.base], cwd=tree)
    _run(["git", "remote", "remove", "origin"], cwd=tree)
    _run(["git", "config", "user.email", "ab@experiment.local"], cwd=tree)
    _run(["git", "config", "user.name", "AB Experiment"], cwd=tree)
    return tree


def run_condition(root: Path, task: Task, cond: str, budget: float, model: str) -> dict:
    tree = make_tree(root, task, cond)
    issue_body = (REPO / "audits" / "ab94" / f"issue-{task.issue}.md").read_text()
    prompt = build_prompt(task, issue_body)
    (root / f"prompt-{task.issue}.txt").write_text(prompt)

    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(root / f"config-{cond}")
    env["GH_TOKEN"] = ""
    env["GITHUB_TOKEN"] = ""
    env.pop("CLAUDE_PROJECT_DIR", None)

    cmd = [
        claude_binary(),
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--max-budget-usd",
        str(budget),
        "--dangerously-skip-permissions",
    ]
    started = time.time()
    proc = _run(cmd, cwd=tree, env=env, timeout=5400)
    wall = time.time() - started

    raw = proc.stdout.strip()
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"is_error": True, "subtype": "unparseable", "result": raw[-4000:]}

    record = {"task": task.issue, "condition": cond, "wall_s": round(wall, 1)}
    record.update(parse_run_json(payload))
    record["exit_code"] = proc.returncode
    record["final_message"] = str(payload.get("result", ""))[-4000:]
    record["stderr_tail"] = proc.stderr[-2000:]

    out = root / f"result-{task.issue}-{cond}.json"
    out.write_text(json.dumps({"record": record, "raw": payload}, indent=2))
    print(json.dumps(record, indent=2))
    return record


def grade(root: Path, task: Task, cond: str) -> dict:
    """Apply the objective oracle + collect the run's own artefacts."""
    tree = root / f"run-{task.issue}-{cond}"
    record = json.loads((root / f"result-{task.issue}-{cond}.json").read_text())["record"]

    # what the run itself produced, BEFORE the oracle overwrites anything
    diff = _run(["git", "diff", task.base, "--", "."], cwd=tree, timeout=120).stdout
    (root / f"diff-{task.issue}-{cond}.patch").write_text(diff)
    record["commits"] = _run(
        ["git", "log", "--oneline", f"{task.base}..HEAD"], cwd=tree
    ).stdout.strip()
    record["files_changed"] = sorted(
        set(
            _run(["git", "diff", "--name-only", task.base], cwd=tree).stdout.split()
        )
    )
    record["wrote_own_test"] = any(f.startswith("tests/") for f in record["files_changed"])

    # harm: the rest of the suite, on the run's own tree
    suite = _run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "-x", "--timeout=300"],
        cwd=tree,
        timeout=2400,
    )
    record["suite_green"] = suite.returncode == 0
    record["suite_tail"] = (suite.stdout or suite.stderr)[-1500:]

    # oracle: the RED test that shipped with the real fix
    oracle_diff = _run(
        ["git", "show", task.red, "--", task.oracle_test], cwd=REPO, timeout=120
    ).stdout
    names = new_test_functions(oracle_diff)
    _run(["git", "checkout", task.red, "--", task.oracle_test], cwd=tree)
    selector = " or ".join(names) if names else None
    cmd = [sys.executable, "-m", "pytest", task.oracle_test, "-q", "--timeout=300"]
    if selector:
        cmd += ["-k", selector]
    oracle = _run(cmd, cwd=tree, timeout=1200)
    record["oracle_tests"] = names
    record["oracle_pass"] = oracle.returncode == 0
    record["oracle_tail"] = (oracle.stdout or oracle.stderr)[-1500:]
    _run(["git", "checkout", "HEAD", "--", task.oracle_test], cwd=tree)

    # hook activity, from the run's own transcript
    text = ""
    for p in (root / f"config-{cond}" / "projects").rglob("*.jsonl"):
        try:
            text += p.read_text(errors="replace")
        except OSError:
            continue
    record["hook_blocks"] = count_hook_blocks(text)

    (root / f"graded-{task.issue}-{cond}.json").write_text(json.dumps(record, indent=2))
    print(json.dumps({k: v for k, v in record.items() if k != "final_message"}, indent=2))
    return record


def report(root: Path) -> None:
    records = [
        json.loads(p.read_text()) for p in sorted(root.glob("graded-*.json"))
    ]
    out = {"runs": records, "aggregate": summarise(records)}
    (root / "report.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out["aggregate"], indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("bootstrap", "run", "grade", "report"))
    ap.add_argument("--root", required=True)
    ap.add_argument("--task", type=int)
    ap.add_argument("--condition", choices=CONDITIONS)
    ap.add_argument("--budget", type=float, default=1.5)
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args(argv)

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)

    if args.command == "bootstrap":
        bootstrap(root)
        return 0
    if args.command == "report":
        report(root)
        return 0

    if args.task not in TASKS or not args.condition:
        ap.error("--task and --condition are required for run/grade")
    task = TASKS[args.task]
    if args.command == "run":
        run_condition(root, task, args.condition, args.budget, args.model)
    else:
        grade(root, task, args.condition)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
