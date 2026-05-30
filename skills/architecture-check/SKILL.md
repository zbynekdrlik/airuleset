---
name: architecture-check
description: Deep full-project architecture & code-quality review that fans out per-dimension agents (architecture/patchwork, SOTA/idioms, dead-code/YAGNI, tests/security/deps), adversarially verifies findings, dedups against open issues, and files a tiered roadmap of GitHub issues (milestone + epics + children). Read-only on code. Run manually — e.g. each time a new Claude model ships — to plan the next improvement rounds.
user-invocable: true
disable-model-invocation: true
---

# Architecture Check

Deep, full-project architecture and code-quality review. Produces a **tiered set of
GitHub issues** = the next rounds of improvement work. **Read-only on code** — this
skill files issues, it does NOT edit source, branch, or open PRs. Fixes happen later
via `/issue-planner` → dev → PR.

Run it yourself (manual), typically when a new Claude model ships and you want a full
re-review against the newest knowledge + current best practice for the stack.

This skill drives a multi-agent `Workflow`. Token cost is expected to be high — that
is the intended trade for coverage. Do NOT downscope to a single pass.

## Phase 0 — Context & scope (inline, before fan-out)

Gather, in the current project directory:

1. **Stack** — detect language(s)/framework(s)/build system from manifests
   (`Cargo.toml`, `package.json`, `pyproject.toml`, `go.mod`, etc.).
2. **Conventions** — read the project's `CLAUDE.md` (branch policy, size caps, overrides).
3. **Size metrics** — file tree + largest files:
   `find . -type f -not -path '*/.git/*' -not -path '*/target/*' -not -path '*/node_modules/*' | xargs wc -l 2>/dev/null | sort -rn | head -30`
4. **Churn hotspots** — most-changed files:
   `git log --since='12 months ago' --name-only --pretty=format: | grep -v '^$' | sort | uniq -c | sort -rn | head -30`
5. **Entry points** — main/index/lib roots, route/handler files.
6. **Existing open issues** (held for Phase 3 dedup):
   `gh issue list --state open --limit 200 --json number,title,labels,body`

Pass a compact context bundle (stack, hotspots, entry points, existing-issue titles)
into every dimension agent so findings are stack-aware and pre-deduplicated.

If the directory is not a git repo or has no `gh` remote, STOP and tell the user —
the deliverable is GitHub issues and requires both.

## Phase 1 — Fan-out: 4 dimension agents (parallel)

Author and run a `Workflow`. Each dimension agent scans the WHOLE project for its
dimension and returns structured findings via schema. Finding shape:

```
{
  title:        string   // imperative, issue-ready ("Split driver.rs god-file into focused modules")
  severity:     "red" | "yellow" | "blue"
  dimension:    "architecture" | "sota" | "dead-code" | "tests-security-deps"
  files:        string[] // path:line evidence locations
  evidence:     string   // why it's a problem, concrete code references
  proposed_fix: string   // the SOTA-correct approach
  effort_loc:   number   // rough LoC estimate
}
```

Dimensions and what each hunts:

1. **architecture** — layering/dependency-direction violations; code-on-code
   workarounds and stacked patches; god-files / files over the project's size cap;
   tangled responsibilities; wrong or missing abstractions and module boundaries.
   Anchor: the spirit of `architecture-first` (fix the design, don't stack workarounds).

2. **sota** — outdated/non-idiomatic patterns for the detected stack; deprecated
   APIs; approaches superseded by newer best practice. **This agent MUST websearch
   current best practice for the stack** (`"<lang/framework> best practices
   <current-year>"`, official docs, release notes) and CITE source URLs in each
   finding's `evidence`. Uncited "best practice" claims are not allowed.

3. **dead-code** — unused functions/modules/exports, unreachable code,
   one-consumer abstractions, speculative generality. Anchor: `mvp-philosophy`
   (delete unused aggressively).

4. **tests-security-deps** — coverage gaps, shallow/happy-path tests, missing
   regression guards; security boundaries (auth, secret handling, input validation);
   stale/vulnerable dependencies (manifest versions vs current advisories — websearch
   advisories where relevant). Anchors: `test-strictness`, `regression-test-first`,
   `security-basics`.

Use `agentType: 'Explore'` or `general-purpose` for the dimension agents — they are
read-only over the codebase.

## Phase 2 — Adversarial verify (per finding)

Pipeline each finding straight from its dimension agent into a skeptic subagent
(default-to-reject). The skeptic answers:

- Is the problem REAL, not a misread of the code? (re-read the cited files)
- Is it ACTUALLY non-idiomatic for THIS stack (not a false positive imported from
  another ecosystem's conventions)?
- Is the proposed fix sound and in-scope?

Drop any finding that fails verification. This is the analog of functional
verification: prove findings before filing so issues are signal, not noise.

## Phase 3 — Dedup against existing issues

Match each verified finding against the Phase-0 open-issue list (title similarity +
file overlap). Skip findings already tracked. Count suppressed duplicates — report
them, never drop silently.

## Phase 4 — Synthesize the tiered roadmap

- **Milestone:** `arch-review: <model> <YYYY-MM-DD>` — model name from THIS session's
  environment (e.g. `opus-4.8`), date from the system clock at run time.
- **Epic issue per dimension** that has surviving findings — a parent summarizing the
  theme with a child-issue checklist in its body.
- **Child issue per concrete fix**, linked to its epic.

## Phase 5 — Auto-create issues (no confirm gate)

1. Ensure labels exist (create if missing):
   `gh label create architecture-review --color BFD4F2 2>/dev/null || true`
   plus dimension labels (`dimension:architecture`, `dimension:sota`,
   `dimension:dead-code`, `dimension:tests-security-deps`) and severity labels
   (`severity:red` color B60205, `severity:yellow` FBCA04, `severity:blue` 0E8A16).
2. Create the milestone:
   `gh api repos/{owner}/{repo}/milestones -f title='arch-review: <model> <date>' 2>/dev/null || true`
   (resolve owner/repo via `gh repo view --json owner,name`).
3. `gh issue create` epics first, then children. Labels: `architecture-review` +
   `dimension:<d>` + `severity:<s>`. Assign all to the milestone. Put child `#N`
   references as a checklist in each epic body after children are created.

Do NOT ask for confirmation before creating — auto-create is the chosen behavior.

## Phase 6 — Report

Completion summary (concise):

- Milestone URL
- Epic list with child counts
- Counts per severity (🔴 / 🟡 / 🔵)
- Duplicates suppressed (N)
- Next step: "Run `/issue-planner` to start working the `arch-review: <model> <date>` milestone."

## Severity scale

- 🔴 **red** — broken/risky/harmful (security hole, data-loss path, crash).
- 🟡 **yellow** — structural debt (god-file, patchwork, wrong abstraction) slowing all future work.
- 🔵 **blue** — idiom/polish (non-idiomatic but working, minor SOTA drift).

## Boundaries

- Read-only on code. No edits, no branches, no PRs.
- Manual trigger only. Does not auto-run on model release.
- Requires a git repo with a `gh`-accessible remote.
- Not a CI gate — an on-demand, human-initiated review.
