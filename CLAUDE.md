# airuleset — Project Instructions

This is the airuleset repository: a Claude Code configuration management system.

## Overview

Centralized management of Claude Code rules, skills, and hooks shared across multiple projects. Uses native `@import` syntax in CLAUDE.md for zero-build-step module loading.

## Services

One line each — the full internals of every service live in
`.claude/rules/airuleset-internals.md`, which auto-loads the moment you touch
that service's files (`paths:` frontmatter), so they cost nothing in a session
that never goes near them.

- **File-Drop** (`filedrop/`, `:8788`) — serves user-facing files as clickable LAN URLs (`airuleset.py share`) and receives them back (`airuleset.py upload`). Governs `deliver-files-as-urls.md` + `receive-files-via-upload-url.md`.
- **Caveman plugin wiring + statusline** (`maybe_setup_caveman`, `statusbar.py`) — keeps caveman wired through a hash-proof runtime shim and renders the `Issues` statusline segment.
- **Playbook system** — per-project knowledge capture after every ticket: `project-playbook-maintenance.md` + the `playbook-review` / `playbook-cleanup` skills + the `stop-check-playbook-review.sh` Stop gate. Content lives per-repo in `.claude/rules/<area>.md`, never in airuleset.
- **api-watchdog** (`watchdog/`) — systemd `--user` timer, every 60 s, on every managed box. `run_once()`'s docstring in `watchdog/__init__.py` is the SINGLE SOURCE OF TRUTH for what each numbered job does. `python3 airuleset.py watchdog --once [--dry-run --verbose]`.
- **Discord notify** (`notify/` + `hooks/notify-discord*.sh`) — the single device-ping path (❓ ask / ✅ done / api-error / autopilot card), per-owner thread routing + mirrors. Governs `milestone-notifications.md`.

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

Moved VERBATIM to `.claude/rules/airuleset-internals.md` (#92) — a path-scoped rule
Claude Code injects automatically the moment you read `airuleset.py`, `statusbar.py`,
`watchdog/`, `hooks/`, `notify/`, `filedrop/`, `burn/`, `tests/`, `settings/` or
`scripts/`. It used to sit on the always-on prefix of every session in this repo,
including the many that never touch that code.

## Rule intake gate — before ADDING any new always-on module

Every new rule originates from a real problem — but it must land on the RIGHT surface, not reflexively in the always-on profile (content loads context every turn of every project; the gate picks the surface, never drops the content):

1. **Mechanically checkable?** → a hook (deterministic) + at most a one-line pointer in an existing module.
2. **Situational** (fires only for one task-type/area — deploy, migration, hardware, mutation, one language)? → an on-demand skill or a `rules/` path-scoped rule.
3. **Topic already owned by an existing module?** → extend that module; never create a parallel new one.
4. Only a genuinely always-relevant, cross-project discipline becomes a NEW module — and its body cites the originating incident + date, so future `/mdreview` native-now passes have something to re-validate against.

## Skill Ownership — DO NOT manage skills belonging to other projects

airuleset only manages skills it created: `ci-monitor`, `deploy-ssh`, `windows-remote-gui`.

These skills are NOT managed by airuleset — do not add, symlink, or modify them:

- `win-mcp.md` — belongs to `winremote-setup` project
- `test-contact-form.md` — belongs to `website-bakerion.ai` project
