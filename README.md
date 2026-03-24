# airuleset

Claude Code configuration management system. Centralizes shared rules, skills, and hooks across multiple projects using native `@import` syntax.

## Problem

Multiple projects (Rust, Python, web) share the same Claude Code conventions: CI monitoring, TDD workflow, PR policies, Windows deployment patterns. Without centralization, these rules are duplicated across project CLAUDE.md files, drift out of sync, and are hard to maintain.

## Solution

airuleset extracts shared rules into standalone modules that the global `~/.claude/CLAUDE.md` imports directly via `@~/devel/airuleset/modules/...`. Changes to modules take effect immediately with no build step.

## Quick Start

```bash
git clone https://github.com/zbynekdrlik/airuleset.git ~/devel/airuleset
cd ~/devel/airuleset

# Preview what will change
python airuleset.py diff

# Deploy to ~/.claude/
python airuleset.py install

# Verify
python airuleset.py status
```

## Architecture

```
~/.claude/CLAUDE.md          # Thin import manifest (~30 lines)
  @~/devel/airuleset/modules/core/pr-merge-policy.md
  @~/devel/airuleset/modules/core/ci-monitoring.md
  ...

~/.claude/skills/            # Symlinks to airuleset skills
  ci-monitor -> ~/devel/airuleset/skills/ci-monitor
  deploy-ssh -> ~/devel/airuleset/skills/deploy-ssh
  ...

~/.claude/settings.json      # Hooks referencing airuleset scripts
  hooks.SessionStart -> bash ~/devel/airuleset/hooks/session-start-fetch.sh
  hooks.PreToolUse   -> bash ~/devel/airuleset/hooks/block-sensitive-staging.sh
```

## Commands

| Command    | Description                                          |
| ---------- | ---------------------------------------------------- |
| `install`  | Generate CLAUDE.md, symlink skills, merge hooks      |
| `diff`     | Show what install would change (unified diff)        |
| `validate` | Check all module/rule files exist and resolve        |
| `status`   | Show current managed config (imports, skills, hooks) |

## Module Categories

- **core/** — PR policy, CI monitoring, TDD, CAWE, work completion, git fetch
- **git/** — Two-branch workflow, commit conventions
- **ci/** — Test strictness, no-local-builds, security audit, coverage thresholds
- **deploy/** — MCP error handling, Windows sessions, SSH deployment, post-deploy verification
- **quality/** — Architecture first, MVP philosophy, security basics, script failure policy

## Requirements

- Python 3.10+ (stdlib only, no dependencies)
- Claude Code with `@import` support
