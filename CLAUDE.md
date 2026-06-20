# airuleset — Project Instructions

This is the airuleset repository: a Claude Code configuration management system.

## Overview

Centralized management of Claude Code rules, skills, and hooks shared across multiple projects. Uses native `@import` syntax in CLAUDE.md for zero-build-step module loading.

## Services

- **File-Drop** (`filedrop/` package, `:8788`) — serves user-facing files as clickable LAN URLs so the user (no direct FS access) can open them. `python3 airuleset.py share <file>` → `http://<lan-ip>:8788/<token>/<name>`. systemd `--user` service on BOTH machines (each binds its own LAN IP, baked into the unit at install). Read-only static server; per-file token = the link's auth. Governs via `modules/core/deliver-files-as-urls.md`.

- **api-watchdog** (`watchdog/` package) — systemd `--user` timer (every 60s, BOTH machines) that detects a Claude Code session **stalled on an API error** (529 overloaded / ConnectionRefused / rate & usage limits) and auto-resumes it with `tmux send-keys "continue"`. Why a poller, not a hook: when a turn dies on an API error CC ABORTS the turn and does NOT reliably fire the `Stop` hook with the error text, so `notify-api-error.sh` is blind. Detection = the pane's transcript last assistant entry is `isApiErrorMessage` (or the pane shows an api-error banner) AND the transcript is ≥5 min idle. Action: `continue` immediately, retry every 5 min up to 3×, then ping "gave up". Pings Discord on the stall + on give-up (the reliable replacement for the Stop-hook ping). A session waiting on a real `❓` is never auto-continued (its last entry isn't an api error). `python3 airuleset.py watchdog --once [--dry-run --verbose]`.

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

- Python stdlib only — no third-party dependencies
- Every module must be standalone, actionable, and 5-20 lines
- Rules must have YAML `paths:` frontmatter
- Skills use the SKILL.md format with YAML frontmatter
- Test with `python -m pytest tests/` before committing

## Skill Ownership — DO NOT manage skills belonging to other projects

airuleset only manages skills it created: `ci-monitor`, `deploy-ssh`, `windows-remote-gui`.

These skills are NOT managed by airuleset — do not add, symlink, or modify them:

- `win-mcp.md` — belongs to `winremote-setup` project
- `test-contact-form.md` — belongs to `website-bakerion.ai` project
