# airuleset — Project Instructions

This is the airuleset repository: a Claude Code configuration management system.

## Overview

Centralized management of Claude Code rules, skills, and hooks shared across multiple projects. Uses native `@import` syntax in CLAUDE.md for zero-build-step module loading.

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

- **dev2**: 10.77.8.134 (user: newlevel) — `~/devel/airuleset/`

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
