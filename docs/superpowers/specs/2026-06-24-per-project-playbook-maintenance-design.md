# Per-Project Playbook Maintenance — Design

Date: 2026-06-24
Status: design (approved in brainstorming; pending implementation plan)
Owner: airuleset

## Problem

Claude does things sub-optimally and repeatedly per project even when a good
procedure was already figured out in an earlier session. The user's own framing:
*"to ja neviem, nevidim a nekontrolujem kde co ma zapisane"* — project working
knowledge is scattered across **three stores** with no clear boundaries and no
visibility:

- **auto-memory** (`~/.claude/projects/<proj>/memory/`) — heavily used (restreamer
  70, songplayer 73, odoo 51 entries) but **invisible to the user** (outside the
  repo, not in git) and bloated (reaperiem: 15 memories → 304-line MEMORY.md).
- **project `CLAUDE.md`** — visible/in-repo, used well by some (camera-box has rich
  build/deploy gotchas) but inconsistent and a growth risk if it becomes the dump.
- **project skills** — exist for *some* projects only (`montalu-odoo-19`, `n8n-*`),
  ad-hoc.

The mechanisms exist; what's missing is (1) **clear boundaries** so knowledge stops
getting scattered, (2) an **enforced post-ticket maintenance loop** so it stays
fresh and gets captured to the right place, and (3) a **one-time cleanup** of the
existing mess.

## Goals

- Per project, a single coherent, **visible**, maintained "playbook" of how to best
  work with it — so figured-out procedures are reused, not re-derived.
- An **enforced** "after every ticket, review & update" loop (autopilot + manual).
- Knowledge lands in **one right place** (no re-scattering).
- The user can **see** what's captured (git diffs + a short per-ticket summary).
- Consolidate the existing scattered state.

## Non-goals

- **Global airuleset CLAUDE.md / module reduction** — explicitly out of scope; that
  is what `/mdreview` is for. The prior principle `feedback_rules_effectiveness_over_size`
  (don't cut global discipline rules for size) stands untouched.
- Reducing the always-loaded **global** footprint.

## The three stores + boundaries (the anti-mess core)

Each store gets ONE job. The maintenance loop routes every learning by this rule:

| Knowledge type | Store | Loaded | Visible |
|---|---|---|---|
| Reusable HOW-TO / procedure / gotcha / "best way to do X here" | **project skill** `.claude/skills/<area>/SKILL.md` (in repo) | on-demand | git diff (in repo) |
| Always-must-apply project rule + the skill router | **project `CLAUDE.md`** (lean) | always | git diff (in repo) |
| User preference / transient cross-session state / non-repo context | **auto-memory** | MEMORY.md index always | not user-visible |
| Global discipline rule (cross-project) | **airuleset module** (via `/mdreview`) | always | — (out of scope) |

**Routing function (used by the review):** *opakovateľný HOW-TO/gotcha → skill;
vždy-platné pravidlo projektu → CLAUDE.md; user-pref/stav → memory; globálne → out.*

## Artifact: in-repo project playbook skills + a lean CLAUDE.md router

- **Project skills live in the project repo** at `.claude/skills/<area>/SKILL.md`
  (decided: in-repo, not centralized in airuleset). On-demand → no always-loaded
  token bloat. In git → every update is a **visible diff in the PR** (directly fixes
  "nevidím"). Split by area, scaled to the project (e.g. `build-deploy`,
  `hardware-quirks`, `domain-model`, `testing`).
- **Project `CLAUDE.md` shrinks** to: global `@import`s + a **`## Playbook router`**
  (≤ ~10 lines: "topic → load skill X when doing Y") + the few truly-always rules.
  The router is the reliability fix for on-demand skills: it tells Claude *when* to
  load each skill rather than relying on description-match alone (same pattern as the
  n8n plugin's always-on `using-n8n-skills` router + on-demand detail skills).
- **Bulk moves out of CLAUDE.md into the skills.** CLAUDE.md never becomes the dump.

## The post-ticket maintenance loop (enforced)

- **Trigger:** end of every ticket — PR merged (autopilot worker) AND manual
  work-done (before the completion report).
- **`playbook-review` skill (global, in airuleset, symlinked to `~/.claude/skills`):**
  reflects on the ticket's diff + session and asks:
  1. Did I discover a **reusable** procedure / gotcha / better way? → capture to the
     right store (routing rule).
  2. Is an existing playbook entry now **stale/wrong** (freshness)? → fix it.
  3. Did I do something the long way that's now figured out? → record the short way.
  Then: write to the correct in-repo skill / CLAUDE.md router / memory; **prune &
  dedup** (anti-bloat); the in-repo changes ride the ticket's PR → visible diff;
  emit a **1–2 line `📔 Playbook:` summary** in the completion report (the visibility
  level the user chose — autonomous + short summary, no approval gate).
- **Enforcement:** an airuleset rule module `project-playbook-maintenance.md` mandates
  the review post-ticket; a completion-report / Stop gate verifies it ran (same shape
  as `plan-check`). The autopilot worker calls it per merged ticket.
- **No-noise discipline:** the review writes only on a real learning/staleness; it
  always *logs that it ran* (for the gate) but does not invent empty updates. The
  `📔` line rides the existing completion report — no separate device ping.

## One-time cleanup (consolidation)

Per project: read memory + CLAUDE.md + existing skills → dedup, **move procedures to
in-repo skills**, trim memory to its role (user-pref/transient only), shrink CLAUDE.md
to the router + always-rules. **Pilot on ONE project first**, show the before/after,
then roll out. Candidate pilots (heaviest mess): restreamer (70 memories / 184-line
index) or songplayer (73). The cleanup is governance work (CLAUDE.md / `.claude/` /
memory) — in-lane — but it commits to the project repo, so it follows that project's
two-branch flow (dev → PR → main).

## Relationship to existing pieces (don't reinvent)

- Builds on `claude-md-management:revise-claude-md` (capture learnings) and the
  auto-memory writer — adds **boundaries + a per-ticket trigger + the skills layer**.
- Distinct from `/mdreview` (global module review) and `rules-audit` (global). This is
  **per-project + per-ticket**.
- Reuses the `plan-check`-style completion gate pattern for enforcement.

## Visibility (the user's core need)

- In-repo skills + lean CLAUDE.md → every capture is a **git diff in the PR**.
- A **`📔` 1–2 line summary** per ticket in the completion report.
- One file per area to open anytime — the playbook is no longer an invisible memory dump.

## Testing & success criteria

- **Routing logic** of `playbook-review` is exercised with example learnings (each
  lands in the expected store) — testable as a checklist/fixture, not reflection.
- **Gate fires:** a ticket whose review was skipped is blocked at the report (like
  `plan-check`).
- **Router format** validated (lean, ≤ ~10 lines, points only to skills that exist).
- **Pilot proof:** on the pilot project, a previously-scattered procedure now lives in
  one in-repo skill that the router reliably loads, memory shrank to its role, and
  CLAUDE.md is router-thin — shown as a before/after diff.
- **Outcome:** procedures stop being re-derived; the user can see what's captured; no
  re-scattering.

## Decided (resolved in brainstorming)

- Skills live **in the project repo** (`.claude/skills/`), not centralized in airuleset.
- Control level: **autonomous maintenance + a short per-ticket summary** (no approval gate).
- Scope: **per-project** only; global reduction stays with `/mdreview`.

## Rollout (high level — detailed in the implementation plan)

1. Build the machinery in airuleset: `playbook-review` skill, the
   `project-playbook-maintenance.md` rule module + completion-gate, the router
   convention, the boundary doc.
2. Pilot the cleanup + loop on ONE project; show before/after; refine.
3. Roll out the cleanup to the rest, project by project, via each repo's PR flow.
