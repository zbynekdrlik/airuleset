# Per-Project Playbook Maintenance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every project a single, visible, in-repo "playbook" of how to best work with it, kept fresh by an enforced post-ticket review loop, so figured-out procedures are reused instead of re-derived.

**Architecture:** airuleset ships the *machinery* (a rule module, a `playbook-review` skill, a `playbook-cleanup` skill, a Stop-hook gate, autopilot-worker integration). The *content* lives per-project: on-demand skills in the project's own `.claude/skills/` (git-versioned → visible diffs) indexed by a lean `## Playbook router` in the project `CLAUDE.md`. After every ticket the review routes new/stale knowledge to the right store (skill / CLAUDE.md / memory) and emits a `📔 Playbook:` line; a Stop hook enforces that completion reports carry it.

**Tech Stack:** Python 3 stdlib (airuleset.py + unittest), Bash + jq (Stop hooks), Markdown (modules/skills), JSON (settings/hooks.json). No third-party deps.

## Global Constraints

- Python **stdlib only** — no third-party imports anywhere (`airuleset.py`, tests).
- Modules are **standalone Markdown**, 5–20 lines focus per block; skills use **SKILL.md** with YAML frontmatter (`name`, `description`).
- New skills MUST be added to `SKILL_NAMES` in `airuleset.py` (else install won't symlink them).
- New Stop hooks MUST be registered in `settings/hooks.json` under `hooks.Stop` and use the established stdin contract: `MSG=$(jq -r '.last_assistant_message // empty')`, `SESSION_ID=$(jq -r '.session_id // "unknown"')`, block via `jq -n --arg reason "$R" '{decision:"block",reason:$reason}'` with a per-session retry cap.
- Deploy ONLY via `python3 airuleset.py push` (never bare git push) — it runs the test suite fail-closed, installs locally, deploys dev2.
- Tests pass (`python3 -m unittest discover -s tests`) before every commit.
- **Out of scope:** global airuleset module reduction (that's `/mdreview`); the `feedback_rules_effectiveness_over_size` principle stands.
- **Routing rule (verbatim, used everywhere):** *opakovateľný HOW-TO/gotcha → projektový skill; vždy-platné pravidlo projektu → CLAUDE.md router/rules; user-pref/transient stav → memory; globálne disciplinárne → airuleset (out).*
- **Visibility level (decided):** autonomous maintenance + a 1–2 line `📔 Playbook:` summary in the completion report; NO approval gate.

---

## File Structure

- `modules/core/project-playbook-maintenance.md` — CREATE. The rule: boundaries, routing rule, the post-ticket mandate, the `📔` summary requirement, and the `## Playbook router` convention (with a copy-paste template).
- `profiles/universal.profile` — MODIFY. Add the module under core.
- `skills/playbook-review/SKILL.md` — CREATE. Operational skill: reflect → route → write → prune → emit `📔`.
- `skills/playbook-cleanup/SKILL.md` — CREATE. One-time per-project consolidation procedure (read 3 stores → route → trim → lean CLAUDE.md → before/after).
- `airuleset.py:70` (`SKILL_NAMES`) — MODIFY. Add `playbook-review`, `playbook-cleanup`.
- `hooks/stop-check-playbook-review.sh` — CREATE. Stop gate: a completion report must carry a `📔 Playbook:` line.
- `settings/hooks.json` — MODIFY. Register the hook under `hooks.Stop`.
- `agents/autopilot-worker.md` — MODIFY. Add the per-ticket playbook-review step before the completion report.
- `CLAUDE.md` (airuleset) — MODIFY. Document the playbook system under `## Services`.
- `tests/test_playbook.py` — CREATE. Wiring + content tests (module/skills/SKILL_NAMES/profile/validate) + behavioral hook tests via subprocess.

---

## Task 1: Rule module + profile wiring

**Files:**
- Create: `modules/core/project-playbook-maintenance.md`
- Modify: `profiles/universal.profile`
- Test: `tests/test_playbook.py`

**Interfaces:**
- Produces: a profile entry `modules/core/project-playbook-maintenance.md` that `airuleset.py install` renders into the global CLAUDE.md; the routing rule + router convention other tasks reference.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_playbook.py
import subprocess, sys
from pathlib import Path
from unittest import TestCase, main
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

REPO = airuleset.REPO_DIR

class TestPlaybookRuleModule(TestCase):
    def test_module_exists_and_in_profile(self):
        mod = REPO / "modules" / "core" / "project-playbook-maintenance.md"
        self.assertTrue(mod.exists(), "rule module missing")
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        self.assertIn("modules/core/project-playbook-maintenance.md", entries)

    def test_module_states_the_boundaries_and_marker(self):
        text = (REPO / "modules" / "core" / "project-playbook-maintenance.md").read_text()
        for needle in ["Playbook router", "📔 Playbook:", ".claude/skills/", "po každom tickete"]:
            self.assertIn(needle, text, f"rule module missing: {needle}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_playbook -v`
Expected: FAIL — `rule module missing`.

- [ ] **Step 3: Write the rule module**

Create `modules/core/project-playbook-maintenance.md` (5–20 line focus, precise wording per `model-awareness.md`). It MUST contain:
- A one-line statement that each project keeps a maintained **playbook** = on-demand skills in the project's own `.claude/skills/`, indexed by a lean `## Playbook router` in the project `CLAUDE.md`.
- The boundary table (skill = procedures/gotchas; CLAUDE.md = router + always-rules; memory = user-pref/transient only; global = out).
- The verbatim routing rule (from Global Constraints).
- "After **po každom tickete** run the `playbook-review` skill before the completion report; the report MUST carry a `📔 Playbook:` line."
- The router template:
  ```markdown
  ## Playbook router
  Load the matching skill BEFORE working on that area (don't re-derive):
  - build / deploy / release → load `.claude/skills/build-deploy`
  - <area> → load `.claude/skills/<area>`
  ```
- Closing: "Applies to all rewordings and semantic equivalents."

- [ ] **Step 4: Add the module to the profile**

In `profiles/universal.profile`, under the core group (next to `modules/core/complete-planned-work.md`), add:
```
modules/core/project-playbook-maintenance.md
```

- [ ] **Step 5: Run test + validate**

Run: `python3 -m unittest tests.test_playbook -v && python3 airuleset.py validate`
Expected: PASS; validate prints `All validations passed.`

- [ ] **Step 6: Commit**

```bash
git add modules/core/project-playbook-maintenance.md profiles/universal.profile tests/test_playbook.py
git commit -m "feat(playbook): rule module — per-project playbook boundaries + router + post-ticket mandate"
```

---

## Task 2: `playbook-review` skill

**Files:**
- Create: `skills/playbook-review/SKILL.md`
- Modify: `airuleset.py:70` (`SKILL_NAMES`)
- Test: `tests/test_playbook.py`

**Interfaces:**
- Consumes: the routing rule + router convention from Task 1.
- Produces: a skill named `playbook-review` that, run at ticket end, writes captures to the right store and emits the `📔 Playbook:` line the Task 3 gate checks.

- [ ] **Step 1: Write the failing test**

```python
class TestPlaybookReviewSkill(TestCase):
    def test_skill_present_and_registered(self):
        skill = REPO / "skills" / "playbook-review" / "SKILL.md"
        self.assertTrue(skill.exists(), "playbook-review SKILL.md missing")
        self.assertIn("playbook-review", airuleset.SKILL_NAMES)

    def test_skill_frontmatter_and_emits_marker(self):
        text = (REPO / "skills" / "playbook-review" / "SKILL.md").read_text()
        self.assertTrue(text.startswith("---"), "missing YAML frontmatter")
        self.assertIn("name: playbook-review", text)
        self.assertIn("description:", text)
        self.assertIn("📔 Playbook:", text)          # emits the gated line
        self.assertIn("routing", text.lower())        # applies the routing rule
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_playbook -v`
Expected: FAIL — `playbook-review SKILL.md missing`.

- [ ] **Step 3: Write the skill**

Create `skills/playbook-review/SKILL.md` with frontmatter `name: playbook-review`, a `description:` that triggers at ticket completion (keywords: "after a ticket / before the completion report / capture reusable procedure / update project playbook"), and a body that instructs:
1. Reflect on the ticket diff + session: any (a) reusable procedure/gotcha, (b) stale/wrong existing playbook entry, (c) long-way-now-figured-out?
2. Route each finding by the verbatim routing rule → write to the project's `.claude/skills/<area>/SKILL.md` (create area if new + add a `## Playbook router` line in the project `CLAUDE.md`), or CLAUDE.md rule, or memory. Procedures NEVER go to memory.
3. Prune/dedup what you touch (anti-bloat); keep the router ≤ ~10 lines.
4. In-repo edits ride the ticket's PR (visible diff). Emit exactly one line in the completion report: `📔 Playbook: <1–2 lines what was learned/updated>` — or `📔 Playbook: nič nové` when there is genuinely nothing reusable (the review still ran).

- [ ] **Step 4: Register the skill**

In `airuleset.py`, append to `SKILL_NAMES` (line ~70): `"playbook-review"`.

- [ ] **Step 5: Run test + validate**

Run: `python3 -m unittest tests.test_playbook -v && python3 airuleset.py validate`
Expected: PASS; validate OK.

- [ ] **Step 6: Commit**

```bash
git add skills/playbook-review/ airuleset.py tests/test_playbook.py
git commit -m "feat(playbook): playbook-review skill — reflect, route to the right store, emit 📔 summary"
```

---

## Task 3: Enforcement Stop hook

**Files:**
- Create: `hooks/stop-check-playbook-review.sh`
- Modify: `settings/hooks.json`
- Test: `tests/test_playbook.py`

**Interfaces:**
- Consumes: the `📔 Playbook:` line emitted by Task 2.
- Produces: a Stop hook that blocks a completion report lacking the `📔 Playbook:` line.

- [ ] **Step 1: Write the failing test (behavioral, via subprocess)**

```python
import json
class TestPlaybookStopHook(TestCase):
    HOOK = str(REPO / "hooks" / "stop-check-playbook-review.sh")
    def _run(self, msg):
        payload = json.dumps({"last_assistant_message": msg, "session_id": "test-pb"})
        return subprocess.run(["bash", self.HOOK], input=payload,
                              capture_output=True, text=True)

    def test_completion_report_without_marker_blocks(self):
        msg = "## ✅ Work Complete\n\nGoal: x\nPR #5 merged abc123"
        out = self._run(msg)
        self.assertIn('"decision": "block"', out.stdout.replace(" ", " "))

    def test_completion_report_with_marker_passes(self):
        msg = "## ✅ Work Complete\n\n📔 Playbook: naučil som build cez CI\nPR #5"
        out = self._run(msg)
        self.assertNotIn("block", out.stdout)

    def test_non_completion_message_passes(self):
        out = self._run("just a normal status update ✅ DONE: hotovo")
        self.assertNotIn("block", out.stdout)
```
(Clear the retry file between runs if needed: `/tmp/airuleset-playbook-block-test-pb`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_playbook -v`
Expected: FAIL — hook file not found / no block emitted.

- [ ] **Step 3: Write the hook** (mirror `stop-check-status-marker.sh` contract)

```bash
#!/usr/bin/env bash
set -euo pipefail
# Hook: Stop — enforces project-playbook-maintenance.md: a completion report
# (## ✅ Work Complete) MUST carry a "📔 Playbook:" line proving the post-ticket
# playbook-review ran. Blocks via {"decision":"block"} with a per-session cap.
command -v jq &>/dev/null || exit 0
INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

# Only gate completion reports.
echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete" || exit 0
# Pass if the playbook line is present.
echo "$MSG" | grep -qE "📔 Playbook:" && exit 0

RETRY_FILE="/tmp/airuleset-playbook-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3
if [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
  echo "$((RETRIES+1))" > "$RETRY_FILE"
  REASON="Completion report is missing the '📔 Playbook:' line. Per project-playbook-maintenance.md, run the playbook-review skill before the report: capture any reusable procedure/gotcha to the project's .claude/skills/ (or CLAUDE.md router / memory per the routing rule), then add a 1-2 line '📔 Playbook: <what you learned/updated>' (or '📔 Playbook: nič nové' if genuinely nothing). This keeps project knowledge fresh + visible."
  jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
  exit 0
fi
rm -f "$RETRY_FILE"
exit 0
```

- [ ] **Step 4: Register in hooks.json**

Add to `settings/hooks.json` → `hooks.Stop` array (after `stop-check-status-marker.sh`):
```json
{ "type": "command", "command": "bash ~/devel/airuleset/hooks/stop-check-playbook-review.sh", "timeout": 5 }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m unittest tests.test_playbook -v`
Expected: PASS (block on missing, pass on present, pass on non-report).

- [ ] **Step 6: Commit**

```bash
chmod +x hooks/stop-check-playbook-review.sh
git add hooks/stop-check-playbook-review.sh settings/hooks.json tests/test_playbook.py
git commit -m "feat(playbook): Stop gate — completion reports must carry the 📔 Playbook line"
```

---

## Task 4: `playbook-cleanup` skill

**Files:**
- Create: `skills/playbook-cleanup/SKILL.md`
- Modify: `airuleset.py` (`SKILL_NAMES`)
- Test: `tests/test_playbook.py`

**Interfaces:**
- Consumes: routing rule (Task 1).
- Produces: a `playbook-cleanup` skill that consolidates one project's scattered knowledge into the boundary-correct stores.

- [ ] **Step 1: Write the failing test**

```python
class TestPlaybookCleanupSkill(TestCase):
    def test_present_and_registered(self):
        self.assertTrue((REPO / "skills" / "playbook-cleanup" / "SKILL.md").exists())
        self.assertIn("playbook-cleanup", airuleset.SKILL_NAMES)
    def test_describes_consolidation(self):
        t = (REPO / "skills" / "playbook-cleanup" / "SKILL.md").read_text()
        self.assertIn("name: playbook-cleanup", t)
        for n in ["memory", ".claude/skills/", "router", "before/after"]:
            self.assertIn(n, t)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_playbook -v` → FAIL (missing skill).

- [ ] **Step 3: Write the skill**

Create `skills/playbook-cleanup/SKILL.md` (frontmatter `name: playbook-cleanup`, description triggering on "consolidate / tidy project knowledge / one-time playbook cleanup"). Body steps:
1. Read all three stores for the project: `~/.claude/projects/<proj>/memory/*`, project `CLAUDE.md`, existing project skills.
2. For each item apply the routing rule → move procedures/gotchas into `.claude/skills/<area>/SKILL.md`; demote memory to user-pref/transient only (delete procedure-memories after moving); shrink `CLAUDE.md` to global imports + `## Playbook router` + always-rules.
3. Dedup; keep router ≤ ~10 lines; commit via the PROJECT's two-branch flow (dev → PR → main) — this is in-lane agent-config (CLAUDE.md/.claude/), not project source.
4. Output a **before/after** summary (counts: memories before/after, skills created, CLAUDE.md line delta) so the consolidation is visible.

- [ ] **Step 4: Register + Step 5: Test + validate**

Add `"playbook-cleanup"` to `SKILL_NAMES`. Run: `python3 -m unittest tests.test_playbook -v && python3 airuleset.py validate` → PASS.

- [ ] **Step 6: Commit**

```bash
git add skills/playbook-cleanup/ airuleset.py tests/test_playbook.py
git commit -m "feat(playbook): playbook-cleanup skill — one-time per-project consolidation"
```

---

## Task 5: Autopilot worker + docs + deploy

**Files:**
- Modify: `agents/autopilot-worker.md`
- Modify: `CLAUDE.md` (airuleset `## Services`)
- Test: `tests/test_playbook.py`

**Interfaces:**
- Consumes: the `playbook-review` skill (Task 2).

- [ ] **Step 1: Write the failing test**

```python
class TestAutopilotAndDocs(TestCase):
    def test_autopilot_worker_runs_playbook_review(self):
        t = (REPO / "agents" / "autopilot-worker.md").read_text()
        self.assertIn("playbook-review", t)
    def test_services_doc_mentions_playbook(self):
        self.assertIn("playbook", (REPO / "CLAUDE.md").read_text().lower())
```

- [ ] **Step 2: Run → FAIL.** `python3 -m unittest tests.test_playbook -v`

- [ ] **Step 3: Wire autopilot-worker**

In `agents/autopilot-worker.md`, in the per-ticket sequence right before "write the completion report", add a step: "Run the `playbook-review` skill (capture reusable procedures/gotchas to the project playbook per project-playbook-maintenance.md; the completion report MUST carry the `📔 Playbook:` line)."

- [ ] **Step 4: Document under Services**

In airuleset `CLAUDE.md` `## Services`, add a bullet describing the playbook system (machinery in airuleset, content per-repo `.claude/skills/`, enforced post-ticket `playbook-review`, `playbook-cleanup` for consolidation).

- [ ] **Step 5: Run full suite**

Run: `python3 -m unittest discover -s tests` → all PASS.

- [ ] **Step 6: Commit + deploy**

```bash
git add agents/autopilot-worker.md CLAUDE.md tests/test_playbook.py
git commit -m "feat(playbook): autopilot-worker runs playbook-review per ticket + Services docs"
python3 airuleset.py push   # test suite fail-closed, install local, deploy dev2
```

---

## Task 6: Pilot the cleanup on ONE project (end-to-end validation)

**Files (in the PILOT PROJECT repo, NOT airuleset):**
- Create: `<pilot>/.claude/skills/<area>/SKILL.md` (the consolidated procedures)
- Modify: `<pilot>/CLAUDE.md` (shrink to global imports + `## Playbook router` + always-rules)
- Modify/trim: `~/.claude/projects/<pilot>/memory/*` (demote to user-pref/transient)

**Interfaces:**
- Consumes: the deployed `playbook-cleanup` + `playbook-review` skills (Task 4/5).

- [ ] **Step 1: Pick the pilot** — heaviest mess + actively worked. Default `restreamer` (70 memories / 184-line MEMORY.md). Confirm with the user if they prefer `songplayer` (73).

- [ ] **Step 2: Baseline (capture before-state)**

Run: `ls ~/.claude/projects/-home-newlevel-devel-restreamer/memory/*.md | wc -l ; wc -l ~/devel/restreamer/CLAUDE.md`
Record counts.

- [ ] **Step 3: Run `playbook-cleanup` on restreamer** on its `dev` branch — consolidate per the routing rule: procedures → `.claude/skills/<area>/`, lean `CLAUDE.md` router, memory trimmed.

- [ ] **Step 4: Verify reliable load** — start fresh: confirm the `## Playbook router` in `CLAUDE.md` points Claude to load a real consolidated skill for a known area, and that a previously-scattered procedure now lives in exactly one place.

- [ ] **Step 5: After-state + open a project PR**

Run the baseline commands again; show before/after deltas. Open the `restreamer` dev→main PR with the consolidation (visible diff). Drive CI green per the project's flow.

- [ ] **Step 6: Report** the before/after to the user; decide rollout order for the remaining projects (one PR per project, applied incrementally — not all at once).

---

## Self-Review

**1. Spec coverage:**
- Boundaries/routing → Task 1 ✓. In-repo skills + lean CLAUDE.md router → Tasks 1,2,6 ✓. Post-ticket loop + `📔` → Tasks 2,3 ✓. Enforcement gate → Task 3 ✓. Autopilot integration → Task 5 ✓. One-time cleanup + pilot → Tasks 4,6 ✓. Visibility (git diffs + `📔`) → Tasks 2,3,6 ✓. Builds on existing pieces / not global → Global Constraints + Task 1 ✓. Memory demoted to its role → Tasks 1,4 ✓.
- No uncovered spec requirement.

**2. Placeholder scan:** No "TBD/TODO/handle edge cases". The two skills' bodies are specified by required content + structure (markdown instruction files, not code) — concrete enough for a fresh implementer. Hook + tests have full code.

**3. Type consistency:** The gated string is `📔 Playbook:` everywhere (module, skill, hook, tests). Skill names `playbook-review` / `playbook-cleanup` consistent across SKILL_NAMES, tests, autopilot. Hook stdin keys (`last_assistant_message`, `session_id`) match the existing contract. Retry file name consistent in hook + test note.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-per-project-playbook-maintenance.md`.

Tasks 1–5 build + deploy the airuleset machinery (in-lane, fully testable). Task 6 is the end-to-end pilot on one project repo (validation; in-lane agent-config via that project's PR flow). Recommended execution: **subagent-driven-development** for Tasks 1–5 (fresh subagent per task, two-stage review), then drive Task 6 in-session (it's interactive cleanup + a project PR).
