---
name: mdreview
description: Fleet-wide ruleset review — reads mdreview-audit artifact for structural baseline, adds native-now evidence + model-combination audit. Run after CC releases or on the 30d cadence.
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Write, WebSearch, WebFetch, Grep, Glob, AskUserQuestion, Skill
---

# /mdreview v2 — Fleet-Wide Ruleset Review

**Goal — CONTENT is the indicator, never line count.** Every rule here originates from a concrete problem; that work is never thrown away because a doc says "keep CLAUDE.md short". Size is a review-due trigger + a one-way ratchet (`tests/context_ratchet.json`, #857); content is reduced by conversion (hook/paths-scoped rule/reference archive) and native-now evidence, never by deletion.

## Step 0 — Read the live model

Read this session's `## Environment` block. Note the live primary model. ALL search queries are built from this value at runtime. A hardcoded model version anywhere in this skill body is itself a finding.

## Step 1 — Structured baseline from the JSON artifact (replaces rules-audit)

```bash
# Read the newest artifact; run the audit if stale >7d
ls -t ~/.claude/mdreview-audit/*.json 2>/dev/null | head -1
# If missing or older than 7 days:
python3 ~/devel/airuleset/airuleset.py mdreview-audit --fleet --json
```

The artifact (`~/.claude/mdreview-audit/<date>.json`, schema 1) carries:
- **Per-box inventory** — global modules (resolved bytes), skill bodies + desc chars, path-scoped rules, per-project CLAUDE.md + always-on rules
- **Cross-surface dedup pairs** — exact-hash matches of normalized sentences (>=40 chars) across module/skill/rule/project surfaces; flagged at >=2 shared hashes or 1 hash of >=120-char sentence
- **Memory R/P/S candidates** — R (rule/procedure: doctrine vocab), P (fact/preference), S (credential-like: LOUD flag, value NEVER in output); per-R: proposed target surface
- **Zero-caller skills** — from `skill-usage --json` (90d fleet window)
- **Scoping matrix** — per-box role (gk/stream/workstation) + profile/module/skill presence
- **Slimming candidates** (#908) — modules flagged as conversion/review-eligible (hook-enforced prose, reference-growth), each with category + reason + verdict hint
- **Context snapshot** (#908) — current `modules_resolved_bytes` / `module_count` / `skill_desc_chars` + ceilings from the #857 ratchet

Consume the artifact's punch-list directly. Do NOT re-grep what the artifact already computed.

### Former rules-audit checks (now scripted in the artifact)

The following checks from the former `rules-audit` skill are now covered by the artifact's structured output. Review each section:

1. **Size metric** — review-due trigger, largest-first priority; the artifact's `global_modules` resolved bytes vs `context_ratchet.json` ceilings
2. **Duplicates** — the artifact's `dedup_pairs` list: cross-surface verbatim sentences. Verdict per pair: merge/consolidate/keep-both-with-reason
3. **Orphans** — modules in `modules/` not in any profile; skills with zero callers (the artifact's zero-caller list)
4. **Contradictions** — global rule says X, project overrides to Y (read the artifact's per-project rules inventory)
5. **Context-gate coverage** — high-traffic modules should have "Context gate" pointers
6. **Memory hygiene** — the artifact's R/P/S classification: R candidates for promotion, P for keep, S for LOUD alert
7. **Model currency** — stale version strings (grep the live model against the artifact's inventory)

## Step 2 — Scoping-matrix review

Read the artifact's `scoping` section. For each box/role combination:
- Does the box's profile match its role? (gk = full review + deploy; stream = reduced; workstation = dev)
- Any module deployed where it doesn't belong? (a gk-only module on a stream box, a stream skill on gk)
- Any box WITHOUT a module it should have?

## Step 3 — Bidirectional dedup verdicts

The artifact lists cross-surface dedup pairs. For EACH pair, decide:
- **Module ↔ Skill** — if the content is identical, keep it on the EFFECTIVE surface (modules for always-on, skill for on-demand). The other gets a one-line pointer (the #9 stub pattern).
- **Module ↔ Project CLAUDE.md** — project-specific content in a global module → move to project `paths:` rule. Global discipline in a project CLAUDE.md → delete from project (inherits from global).
- **Skill ↔ Rule** — a skill body and a path-scoped rule covering the same topic → consolidate per the content surface that loads (#104: skill bodies don't reach dispatched workers).

## Step 4 — Memory-promotion decisions

For each R-classified memory item:
- **Promote** to the proposed target surface (managed module / project `paths:` rule / hook), with evidence
- **Dedup-delete** if it duplicates a managed rule
- **Hand-off** if it belongs to another project's own rule surface
- **Keep** in memory if it's a genuine per-box preference that varies by box

For S-flagged items: LOUD alert — a credential in memory is a leak surface. The value is NEVER in the output; surface the file path + pattern name only.

## Step 5 — Zero-caller skills

From the artifact's zero-caller list (skill-usage 90d fleet window):
- A skill with ZERO calls across the fleet in 90 days is a retirement candidate
- Ask the user per candidate: retire (delete) / keep (with reason) / convert to `paths:` rule
- User-invocable: false skills with zero model invocations are agent-only dead code

## Step 5b — /skill-doctor: per-skill health (#893)

Run `/skill-doctor` once on the current box (non-interactive: `echo '/skill-doctor' | claude -p`). Install-shape signals (context cost, duplicates, plugin wiring) are fleet-invariant — airuleset manages them identically, so one box answers for all. The 7-day token attribution is per-machine usage and stays a SECONDARY signal.

**Consume (unique signals /skill-doctor provides):**
- **Per-skill context cost** (~N tokens/turn for description listing) — rank skills by always-on cost; top-cost skills are first candidates to slim descriptions or convert to `paths:` rules. Feeds the #857 context ceiling.
- **7-day token attribution** — heavy body cost + rare fleet use = slim/convert candidate. SECONDARY signal only; never a retirement basis alone.
- **Duplicate detection** — same skill loaded from multiple sources is an install-wiring BUG to fix, not just a finding to report.
- **Plugin freshness** — cross-check with the artifact's fleet usage (Step 5) before disabling; per-machine recency alone never disables a plugin.

**Ignore (dedup with Step 5):** the `uses`/`last used` columns and the "N skills loaded but never invoked" footer. The artifact's fleet 90-day `skill-usage` data (Step 5) is the sole authority for zero-caller retirement decisions — never use /skill-doctor's per-machine 7-day window for that.

If `/skill-doctor` is unavailable (CC < v2.1.252), skip this step with a logged note — never block the review.

## Step 5c — SLIMMING PASS (mandatory, #908)

**This step is MANDATORY on every /mdreview run. A model-generation trigger (Job 43 `reason=model-generation`) makes it NON-SKIPPABLE; a 30d cadence run also runs it but may produce zero candidates if the set is already minimal.**

Read the artifact's per-box `slimming` section (`boxes[].slimming`, added by `mdreview-audit` since #908). It carries:
- **`candidates`** — modules/rules flagged as slimming-eligible, each with `category`, `reason`, and `verdict_hint`
- **`context_snapshot`** — current `modules_resolved_bytes`, `module_count`, `skill_desc_chars`, and `ceilings`

For EACH candidate, assign a verdict:

| Verdict | Meaning | Evidence required |
|---|---|---|
| **keep** | The rule still earns its always-on place | State WHY — what the current model still needs it for, or what the hook does NOT cover |
| **convert** | Move to hook-stub / paths-scoped rule / reference archive | The #9 stub pattern: enforcement-critical core stays as a stub pointing at the hook/skill; the verbose prose moves off always-on |
| **remove** | Delete (content no longer useful at all) | Cite live prompting docs OR an observed-behavior check proving the current gen does this natively. Per existing hard lines: conversion never deletion for still-useful content |

**Candidate areas to seed the first run (issue #908 point 4):**
1. Oldest always-on modules with hook coverage (the `hook-enforced` category in the artifact) — the Rust/mutation-era calibrations from the 2026-07-09 run are precedent
2. Duplicate enforcement: a hook + prose banning the same thing → the prose is the conversion candidate (the hook is deterministic, the prose is probabilistic)
3. `rules-reference/` and `*-history.md` files growing past 50 KB without fleet reads — archive or trim oldest entries

**A run that identifies ZERO slimming candidates must say WHY in the audit log** — evidence that the current set is already minimal (e.g. "all N candidates reviewed, all kept because: <reason>"), never a bare "nothing to slim" default.

**Record `context-baseline` bytes BEFORE and AFTER** this step's applied changes (or the whole run's changes if slimming edits are applied in Step 7):
```bash
python3 ~/devel/airuleset/airuleset.py context-baseline --check
# Record the output in the audit log as "BEFORE"
# After applying any conversions/removals:
python3 ~/devel/airuleset/airuleset.py context-baseline --check
# Record as "AFTER" — ties to the #857 down-only ceiling
```

## Step 6 — Live web research (AXIS 1–3, extends the artifact)

WebSearch + WebFetch, queries built from the live model:
- Native-now: `"Claude <live-model> prompt engineering best practices"` — what does the current gen do natively?
- Model-combination: `"Claude Code <year> hooks skills features"` — audit `model-awareness.md` against the live docs
- Dynamic-application: `"CLAUDE.md best practices length budget"` — which always-on modules should be `paths:` scoped?

Every proposed change carries a source URL; no URL = no change.

## Step 7 — Score, apply, log

1. **Score** each proposed change: `Impact × Confidence / Effort`; sort high→low.
2. **AskUserQuestion** — EVERYTHING goes to the user's review. Per change: Apply now / Defer-to-issue / Reject. Never apply silently.
3. **Apply** accepted edits.
4. **Validate + deploy:** `python3 airuleset.py validate` then `python3 airuleset.py push`.
5. **Log** to `audits/mdreview-<date>.md`: every finding, score, source, verdict, **+ context-baseline bytes BEFORE/AFTER** (from Step 5c). A run whose verdict is "reviewed, all rules still earn their place" is a SUCCESSFUL run — but it MUST include the slimming-pass evidence (candidates reviewed + verdicts, or "zero candidates because: <reason>").

## Rules

- **Content over line count.** The three axes (native-now / model-combination / dynamic application) are the review; size is a one-way ratchet, never a target.
- Every proposed change cites a source URL or artifact evidence. No evidence → no change.
- Model generation is read from Environment ONCE — never hardcoded.
- MCP/connector changes are for the OWNING project to apply.
- Never apply silently; always validate before push.
- **Re-audit trigger:** after every Claude Code release + the watchdog's 30d/model-generation cadence.
- **Slimming pass is MANDATORY** (#908, owner directive): every run produces the slimming output (Step 5c). A model-generation trigger (Job 43) makes the pass non-skippable. A run that slimmed nothing must say WHY with evidence.
- **Measurability:** context-baseline bytes BEFORE/AFTER in every audit log. The #857 down-only ceiling is the target; a run that raises it is a finding.
