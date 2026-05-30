# `/architecture-check` — Design Spec

**Date:** 2026-05-30
**Status:** Approved (design), pending implementation
**Type:** New airuleset-managed, user-invocable skill

## Purpose

A user-invoked slash command that performs a deep, full-project architecture and
code-quality review and emits a tiered set of GitHub issues representing the next
rounds of improvement work.

Intended trigger: the user runs `/architecture-check` **manually**, typically each
time a new Claude model ships, to re-review a whole project against the newest
knowledge the current model carries (plus a live websearch of current best
practice for the detected stack). It finds weak spots, design/architecture mess,
patchwork ("code above code"), dead code, and non-SOTA approaches — and turns
them into trackable, prioritized issues.

It is a sibling of:
- `rules-audit` — periodic deep review that produces a cited punch list (the model for the analysis rigor + websearch step).
- `issue-planner` — produces/selects GitHub issues (the model for issue output + dedup).

`architecture-check` **produces** the improvement rounds; `issue-planner` later
**consumes** them through the normal dev → PR flow.

## Scope boundary (critical)

- **Read-only on code.** The skill analyses and files issues. It does **NOT** edit
  source, create branches, or open PRs.
- **The deliverable is the issue set**, not a code change. Fixes happen later via the
  user's normal `issue-planner` → `dev` → PR workflow.
- Runs in whatever project directory it is invoked from (global skill).

## Engine — multi-agent workflow

Fan-out → adversarial verify → synthesize. Chosen for maximum coverage on large
codebases and to match the user's max-autonomy / ultracode preference (token cost
is explicitly not a concern). Uses the `Workflow` tool.

### Phase 0 — Context & scope (inline, before fan-out)

- Detect stack: language(s), framework(s), build system, package manifests.
- Read the project's `CLAUDE.md` (project conventions, branch policy, overrides).
- Compute size metrics: file tree, largest files, line counts.
- Compute churn: most-frequently-changed files via `git log` (hotspots).
- Identify entry points (main/index/lib roots, route files).
- Pull existing open issues: `gh issue list --state open --limit 200 --json number,title,labels,body`
  — held for dedup in Phase 3.

This context (stack, hotspots, entry points, existing-issue titles) is passed to
every dimension agent so findings are stack-aware and pre-deduplicated.

### Phase 1 — Fan-out: 4 dimension agents (parallel)

Each agent scans the whole project for its dimension and returns **structured
findings**. Each finding:

```
{
  title:        string   // imperative, issue-ready ("Split driver.rs god-file into N modules")
  severity:     "red" | "yellow" | "blue"
  dimension:    string
  files:        string[] // path:line references (evidence locations)
  evidence:     string   // why it's a problem, with concrete code references
  proposed_fix: string   // the SOTA-correct approach
  effort_loc:   number   // rough LoC estimate (feeds bundling/round sizing)
}
```

Dimensions:

1. **Architecture & patchwork**
   - Layering violations, dependency-direction breaks
   - Code-on-code workarounds, patches stacked on patches
   - God-files / files over the project's size cap, tangled responsibilities
   - Wrong/missing abstractions, missing module boundaries
   - Anchored by `architecture-first.md` philosophy.

2. **SOTA / idioms**
   - Outdated or non-idiomatic patterns for the detected stack
   - Deprecated APIs, superseded language/framework features
   - Approaches improved upon by newer best practice
   - **Websearches current best practice for the stack** (e.g.
     `"<lang/framework> best practices <current-year>"`, official docs) so the
     review reflects *newest* knowledge, not static training. Cite source URLs in
     finding evidence.

3. **Dead code & YAGNI**
   - Unused functions/modules/exports, unreachable code
   - One-consumer abstractions, speculative generality
   - Anchored by `mvp-philosophy.md` (delete unused aggressively).

4. **Tests, security & deps**
   - Coverage gaps, shallow/happy-path tests, missing regression guards
   - Security boundaries (auth, secret handling, input validation)
   - Stale / vulnerable dependencies (check manifest vs current advisories)
   - Anchored by `test-strictness.md`, `regression-test-first.md`, `security-basics.md`.

### Phase 2 — Adversarial verify (per finding)

Each surviving finding gets a skeptic pass (independent subagent, default-to-reject):

- Is the problem **real** (not a misread of the code)?
- Is it **actually non-idiomatic for THIS stack** (not a false positive from a
  different ecosystem's conventions)?
- Is the proposed fix sound and in-scope?

Findings that fail verification are dropped. This is the analog of the
autonomous-verification rule: prove findings before filing, so issues are signal
not noise.

### Phase 3 — Dedup against existing issues

Match each verified finding against the open-issue list from Phase 0 (title
similarity + file overlap). Skip any finding already tracked. Report how many were
suppressed as duplicates (no silent drops).

### Phase 4 — Synthesize tiered roadmap

- Create one **milestone**: `arch-review: <model> <YYYY-MM-DD>`
  (e.g. `arch-review: opus-4.8 2026-05-30`). Model name from the live session's
  environment; date from the system clock at run time.
- For each dimension that has surviving findings, create one **epic issue**
  (parent) summarizing the theme, with a child-issue checklist.
- For each concrete fix, create a **child issue** linked to its epic.

### Phase 5 — Auto-create issues

- `gh issue create` for epics, then children.
- Labels: `architecture-review` + dimension label + severity label
  (`severity:red|yellow|blue`). Create labels if missing.
- Assign all to the milestone. Link children to their epic (checklist in epic body
  referencing child `#N`).
- No confirmation gate (user's no-hand-holding preference).

### Phase 6 — Report

Completion summary:
- Milestone URL
- Epic list with child counts
- Counts per severity (🔴 / 🟡 / 🔵)
- Duplicates suppressed
- Next step pointer: run `/issue-planner` to start working the milestone.

## Severity scale

- 🔴 **red** — broken, risky, or actively harmful (security hole, data-loss path, crash).
- 🟡 **yellow** — structural debt (god-file, patchwork, wrong abstraction) that slows all future work.
- 🔵 **blue** — idiom / polish (non-idiomatic but working code, minor SOTA drift).

## Installation

- New directory: `skills/architecture-check/SKILL.md`.
- Frontmatter: `user-invocable: true`, `disable-model-invocation: true`
  (manual-only, like `rules-audit` / `issue-planner`).
- Add `"architecture-check"` to `SKILL_NAMES` in `airuleset.py`.
- Deploy: `python3 airuleset.py push` (GitHub + local install + dev2).

## Decisions locked

- **Engine:** multi-agent workflow (not single pass).
- **Output:** tiered roadmap (milestone + epics + child issues), not flat or single-report.
- **Dimensions:** all four (architecture, SOTA, dead-code, tests/security/deps).
- **Creation:** auto-create with labels + milestone, then report (no confirm gate).
- **Trigger:** manual only (`/architecture-check`); no auto-run on model release.
- **Freshness:** SOTA agent websearches current stack best practice each run.

## Non-goals

- Does not edit code or open PRs.
- Does not auto-trigger on model release.
- Does not run the fixes — that is `issue-planner`'s job downstream.
- Not a CI gate; it is an on-demand human-initiated review.
