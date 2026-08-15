# airuleset — Project Instructions

This is the airuleset repository: a Claude Code configuration management system.

## Overview

Centralized management of Claude Code rules, skills, and hooks shared across multiple projects. Uses native `@import` syntax in CLAUDE.md for zero-build-step module loading.

## Services

One line each — the full internals of every service live in the per-area
`.claude/rules/internals-<area>.md` files (`watchdog`/`hooks`/`notify`/`filedrop`/
`cli`/`tests`/`scripts`/`statusbar`/`skills-modules`/`burn`), each auto-loading the
moment you touch that area's files (`paths:` frontmatter), so they cost nothing in
a session that never goes near them. `.claude/rules/airuleset-internals.md` is now a
small always-on ROUTER; the deep archive is the on-demand
`.claude/rules-reference/internals-archive.md` (#482).

- **File-Drop** (`filedrop/`, `:8788`) — serves user-facing files as clickable LAN URLs (`airuleset.py share`) and receives them back (`airuleset.py upload`). Governs `deliver-files-as-urls.md` + `receive-files-via-upload-url.md`.
- **Caveman plugin wiring + statusline** (`maybe_setup_caveman`, `statusbar.py`) — keeps caveman wired through a hash-proof runtime shim and renders the `Issues` statusline segment.
- **Playbook system** — per-project knowledge capture after every ticket: `project-playbook-maintenance.md` + the `playbook-review` / `playbook-cleanup` skills + the `stop-check-playbook-review.sh` Stop gate. Content lives per-repo in `.claude/rules/<area>.md`, never in airuleset.
- **api-watchdog** (`watchdog/`) — systemd `--user` timer, every 60 s, on every managed box. `run_once()`'s docstring in `watchdog/__init__.py` is the SINGLE SOURCE OF TRUTH for what each numbered job does. `python3 airuleset.py watchdog --once [--dry-run --verbose]`.
- **Discord notify** (`notify/` + `hooks/notify-discord*.sh`) — the single device-ping path (❓ ask / ✅ done / api-error / autopilot card), per-owner thread routing + mirrors. Governs `milestone-notifications.md`.
- **Transcript retention** (`discover_old_transcript_candidates`/`sweep_old_transcripts`/`_compress_transcript_file`, #410) — gzip-at-rest for old (30d+) MAIN session transcripts, NEVER deletes; wired report-only into `cmd_install()` step 12 everywhere until the user signs off on live compression. `python3 airuleset.py sweep-transcripts [--dry-run]`.

## Structure

- `modules/` — Atomic rule blocks (standalone .md files), organized by category
- `rules/` — Path-scoped rules with YAML frontmatter (for `.claude/rules/` symlinks)
- `profiles/` — Named sets of modules for different project types
- `skills/` — Global skills in SKILL.md format
- `hooks/` — Hook scripts referenced by settings.json
- `settings/` — JSON fragments for settings.json merging
- `airuleset.py` — CLI tool (Python, stdlib only)

## Commands

```bash
python3 airuleset.py install    # Deploy to ~/.claude/ (CLAUDE.md + skills + hooks)
python3 airuleset.py diff       # Preview changes before installing
python3 airuleset.py validate   # Check all files exist and resolve
python3 airuleset.py status     # Show current managed config state
python3 airuleset.py push       # Push to GitHub + install locally + deploy to ALL remote machines
```

## Deployment Policy — BOTH MACHINES

**After ANY change to airuleset, you MUST deploy to ALL machines.** Use `python3 airuleset.py push` instead of `git push` — it pushes to GitHub, installs locally, AND deploys to all remote machines automatically.

Remote machines:

- **dev2**: 100.82.64.27 (user: newlevel) — `~/devel/airuleset/`

**Never use bare `git push` for airuleset changes.** Always use `python3 airuleset.py push`.

## Development Rules

Moved VERBATIM off the always-on prefix (#92), then SPLIT (#482) out of the former
973 KB `.claude/rules/airuleset-internals.md` monolith — whose broad `paths:` matched
nearly the whole repo and injected ~240k tokens on almost any Read, killing sessions.
Now: a small always-on ROUTER at `.claude/rules/airuleset-internals.md`, per-area
path-scoped `.claude/rules/internals-<area>.md` files (each < 50 KB, byte-capped by
the `scripts/size_ratchet.py` ratchet), and the on-demand
`.claude/rules-reference/internals-archive.md` deep archive. **A new playbook lesson
goes into the matching `internals-<area>.md`** (not the router, not the archive); when
that file nears the ~50 KB cap, move its oldest lessons into the archive.

## Rule intake gate — before ADDING any new always-on module

Every new rule originates from a real problem — but it must land on the RIGHT surface, not reflexively in the always-on profile (content loads context every turn of every project; the gate picks the surface, never drops the content):

1. **Mechanically checkable?** → a hook (deterministic) + at most a one-line pointer in an existing module.
2. **Situational** (fires only for one task-type/area — deploy, migration, hardware, mutation, one language)? → a `rules/` path-scoped rule (auto-injects on matching Reads), or a binding in `hooks/situational-triggers.conf` (auto-injects on the matching ACTION; extending that table is not a new hook). A SKILL is a valid destination ONLY for a WORKFLOW invoked by name — NEVER for knowledge/rules: a skill body loads only on an explicit Skill call, and #91 measured 32 of 53 skills with zero lifetime loads (rules parked there behaved as deleted, 2026-07-09→07-27).
3. **Topic already owned by an existing module?** → extend that module; never create a parallel new one.
4. Only a genuinely always-relevant, cross-project discipline becomes a NEW module — and its body cites the originating incident + date, so future `/mdreview` native-now passes have something to re-validate against.

## Supervision machinery — FREEZE LIFTED; quality bar instead (user decision, 2026-08-15)

The 2026-07-31 FREEZE (no new watchdog job / no new hook; same-defect fixes wait
for a live failure) is **LIFTED**. The user's 2026-08-15 verdict on why it had to
exist: the machinery grew without reviews and without a strict SOTA architecture
— "sám si napísal 17-tisíc riadkovú heuristiku, ktorá nikdy nevedela fungovať
normálne" — so the answer is QUALITY, not blocking. What applies now to EVERY
machinery change:

- **Full per-ticket quality bar, no exceptions:** design comment (root cause
  traced in code) BEFORE the first commit, RED→GREEN regression pair, 2×
  fresh-context adversarial review, and verification on a LIVE box — a green
  suite alone proves nothing (the montalu regression lesson stands).
- **Architectural direction = #486:** replace pane-render heuristics with
  structured state (transcript jsonl, agent list, hook events); silent
  suppression branches become explicit decision logs; net supervision LOC goes
  DOWN. No new thousand-line heuristic, no new suppression gate stacked onto an
  incident.
- **Previously-parked same-defect tickets are workable again** (#360, #375,
  #386, #488 were the parked set at lift time).
- **Kept from the freeze era:** fix root causes, not symptoms; projects come
  before tooling when both are open; the rule-intake gate above applies in FULL
  again (its step 1 "mechanically checkable → hook" is available — a new
  hook/job carries the same design-comment justification as any other change).

## Skill Ownership — DO NOT manage skills belonging to other projects

airuleset only manages skills it created: `ci-monitor`, `deploy-ssh`, `windows-remote-gui`.

These skills are NOT managed by airuleset — do not add, symlink, or modify them:

- `win-mcp.md` — belongs to `winremote-setup` project
- `test-contact-form.md` — belongs to `website-bakerion.ai` project
