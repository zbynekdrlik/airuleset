# architecture-check Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a user-invoked `/architecture-check` skill to airuleset that fans out per-dimension agents over a project, verifies + dedups findings, and files a tiered roadmap of GitHub issues.

**Architecture:** A new airuleset-managed skill = one prose `skills/architecture-check/SKILL.md` (the workflow the agent follows) + registration in `airuleset.py`'s `SKILL_NAMES` list + a registration test. No runtime code — the skill body IS the deliverable. Deployed with `python3 airuleset.py push` (GitHub + local symlink + dev2).

**Tech Stack:** Python 3 stdlib (airuleset.py), unittest (tests/test_airuleset.py), Markdown SKILL.md with YAML frontmatter, `gh` CLI + `Workflow` tool (invoked by the skill at runtime, not at build time).

---

## File Structure

- **Create** `skills/architecture-check/SKILL.md` — the skill the agent executes. One responsibility: drive the architecture-review workflow end to end.
- **Modify** `airuleset.py` — add `"architecture-check"` to `SKILL_NAMES` (one line). This is what symlinks the skill into `~/.claude/skills/` and gates the existence test.
- **Modify** `tests/test_airuleset.py` — add a test asserting the new skill has the required user-invocable frontmatter (strengthens the existing existence-only check).
- **Docs** already created: `docs/superpowers/specs/2026-05-30-architecture-check-design.md` (committed `7b92617`).

Why this split: airuleset's pattern is "skill = a directory with SKILL.md, registered in `SKILL_NAMES`". The existing `test_all_skills_have_skill_md` already iterates `SKILL_NAMES`, so registration + file creation form a natural RED→GREEN pair.

---

### Task 1: Register the skill (RED)

Add the skill name to `SKILL_NAMES` BEFORE the SKILL.md exists, so the existing existence test goes red and proves it guards the new skill.

**Files:**
- Modify: `airuleset.py:38` (the `SKILL_NAMES` list)

- [ ] **Step 1: Add the name to SKILL_NAMES**

Change line 38 from:

```python
SKILL_NAMES = ["ci-monitor", "deploy-ssh", "windows-remote-gui", "issue-planner", "plan-check", "rules-audit", "mdreview", "fast-iterate"]
```

to:

```python
SKILL_NAMES = ["ci-monitor", "deploy-ssh", "windows-remote-gui", "issue-planner", "plan-check", "rules-audit", "mdreview", "fast-iterate", "architecture-check"]
```

- [ ] **Step 2: Run the existence test to verify it fails**

Run: `cd /home/newlevel/devel/airuleset && python3 -m pytest tests/test_airuleset.py::TestSkillsExist -v`
Expected: FAIL — `AssertionError: Missing SKILL.md: .../skills/architecture-check/SKILL.md`

No commit yet — RED state is intentional; Task 2 makes it green in the same logical change.

---

### Task 2: Write the skill (GREEN)

Create the full SKILL.md. This is the actual deliverable — the workflow the agent follows when the user runs `/architecture-check`.

**Files:**
- Create: `skills/architecture-check/SKILL.md`

- [ ] **Step 1: Create `skills/architecture-check/SKILL.md` with this exact content**

````markdown
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
````

- [ ] **Step 2: Run the existence test to verify it now passes**

Run: `cd /home/newlevel/devel/airuleset && python3 -m pytest tests/test_airuleset.py::TestSkillsExist -v`
Expected: PASS

- [ ] **Step 3: Commit registration + skill together**

```bash
cd /home/newlevel/devel/airuleset
git add airuleset.py skills/architecture-check/SKILL.md
git commit -m "Add architecture-check skill

User-invoked /architecture-check: multi-agent deep project review that fans out
per-dimension agents, adversarially verifies + dedups findings, and files a tiered
roadmap of GitHub issues (milestone + epics + children). Read-only on code.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Strengthen the registration test (frontmatter assertion)

The existing test only checks the file exists. Add a check that the new skill is
correctly marked user-invocable + manual-only, matching the `rules-audit` /
`issue-planner` pattern.

**Files:**
- Modify: `tests/test_airuleset.py` (append a method to `class TestSkillsExist`)
- Test: same file

- [ ] **Step 1: Write the failing test**

Append this method inside `class TestSkillsExist` (after `test_all_skills_have_skill_md`, lines ~90-93):

```python
    def test_architecture_check_is_user_invocable(self):
        path = airuleset.REPO_DIR / "skills" / "architecture-check" / "SKILL.md"
        content = path.read_text()
        self.assertIn("user-invocable: true", content)
        self.assertIn("disable-model-invocation: true", content)
```

- [ ] **Step 2: Run it to verify it passes (file already has the frontmatter from Task 2)**

Run: `cd /home/newlevel/devel/airuleset && python3 -m pytest tests/test_airuleset.py::TestSkillsExist::test_architecture_check_is_user_invocable -v`
Expected: PASS (the SKILL.md created in Task 2 contains both lines).

Note: this assertion is green immediately because Task 2 already wrote the frontmatter. It is a regression guard — if a future edit strips the frontmatter, this fails. (Genuine RED-before-GREEN does not apply: this is a new guard on already-correct content, not a bug fix.)

- [ ] **Step 3: Commit**

```bash
cd /home/newlevel/devel/airuleset
git add tests/test_airuleset.py
git commit -m "test: assert architecture-check skill is user-invocable + manual-only

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full validation

Run the whole suite + airuleset's own validators before deploy.

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/newlevel/devel/airuleset && python3 -m pytest tests/ -v`
Expected: all PASS, including `TestSkillsExist` (now 9 skills) and the new frontmatter test.

- [ ] **Step 2: Run airuleset validate**

Run: `cd /home/newlevel/devel/airuleset && python3 airuleset.py validate`
Expected: `All validations passed.` and `Skills: 9` (was 8).

- [ ] **Step 3: Preview the deploy diff**

Run: `cd /home/newlevel/devel/airuleset && python3 airuleset.py diff`
Expected: shows the new `architecture-check` skill symlink to be added; no unexpected changes.

---

### Task 5: Deploy to all machines

airuleset MUST deploy via `push` (GitHub + local install + dev2), never bare `git push`.

**Files:** none (deploy only)

- [ ] **Step 1: Push + install + deploy**

Run: `cd /home/newlevel/devel/airuleset && python3 airuleset.py push`
Expected: pushes to GitHub `main`, symlinks `architecture-check` into `~/.claude/skills/`, deploys to dev2 (10.77.8.134).

- [ ] **Step 2: Verify the local symlink**

Run: `ls -l ~/.claude/skills/architecture-check/SKILL.md`
Expected: symlink resolves to `~/devel/airuleset/skills/architecture-check/SKILL.md`.

- [ ] **Step 3: Verify status reports the skill managed**

Run: `cd /home/newlevel/devel/airuleset && python3 airuleset.py status`
Expected: `architecture-check` listed under managed skills, not under "Unmanaged skills".

---

## Self-Review

**Spec coverage:**
- Multi-agent engine → Task 2 Phase 1 (Workflow fan-out). ✔
- 4 dimensions (architecture/sota/dead-code/tests-security-deps) → Phase 1 list. ✔
- Adversarial verify → Phase 2. ✔
- Dedup vs open issues → Phase 0 pull + Phase 3 match. ✔
- Tiered roadmap (milestone + epics + children) → Phase 4. ✔
- Auto-create w/ labels+milestone, no gate → Phase 5. ✔
- SOTA websearch for freshness → Phase 1 dimension 2 (mandatory + cited). ✔
- Read-only boundary / manual trigger → Boundaries section + frontmatter `disable-model-invocation`. ✔
- Report w/ severity counts + next-step → Phase 6. ✔
- Install: SKILL.md + SKILL_NAMES + push → Tasks 1, 2, 5. ✔
- Severity scale → present in spec and SKILL.md. ✔

**Placeholder scan:** No TBD/TODO. Every code/command step shows exact content. SKILL.md is complete inline. ✔

**Type consistency:** Dimension keys are identical everywhere — `architecture`, `sota`, `dead-code`, `tests-security-deps` — in the finding schema, the dimension list, and the labels. Severity values `red|yellow|blue` consistent in schema, labels, and scale. `SKILL_NAMES` final value matches across Task 1 and the test in Task 3. ✔
