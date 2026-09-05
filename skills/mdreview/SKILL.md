---
name: mdreview
description: Fleet-wide ruleset review consuming the structured `mdreview-audit` JSON artifact. Step 1 reads the newest artifact (runs `mdreview-audit --fleet` if stale >7d) for the structural baseline (inventories, cross-surface dedup pairs, memory R/P/S candidates, zero-caller skills, scoping matrix). Then adds what needs the network — native-now evidence, model-combination audit, dynamic-application triage, bidirectional dedup verdicts, memory-promotion decisions, scoping-matrix review. Content is the indicator, never line count; the context ratchet ceiling only goes DOWN. Run after every CC release and on the watchdog's 30d/model-generation cadence trigger.
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
5. **Log** to `audits/mdreview-<date>.md`: every finding, score, source, verdict. A run whose verdict is "reviewed, all rules still earn their place" is a SUCCESSFUL run.

## Rules

- **Content over line count.** The three axes (native-now / model-combination / dynamic application) are the review; size is a one-way ratchet, never a target.
- Every proposed change cites a source URL or artifact evidence. No evidence → no change.
- Model generation is read from Environment ONCE — never hardcoded.
- MCP/connector changes are for the OWNING project to apply.
- Never apply silently; always validate before push.
- **Re-audit trigger:** after every Claude Code release + the watchdog's 30d/model-generation cadence.
