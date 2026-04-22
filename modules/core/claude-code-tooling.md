### Claude Code Tooling (2026)

Use built-in Claude Code features that accelerate autonomous work. Suggest them proactively when relevant.

#### Auto Mode (Shift+Tab in CLI)

Permission-classifier that auto-approves safe commands and pauses on risky ones. Preferred over `--dangerously-skip-permissions`. Enable at the start of any long agentic session.

#### Effort levels

Adaptive thinking with five tiers: `low`, `medium`, `high`, `xhigh`, `max`. Default is `xhigh` (Opus 4.7). Guidance:
- `max` — deep debugging, complex architecture, multi-file refactors
- `xhigh` — default for most coding work (keep as-is)
- `low`/`medium` — trivial edits, formatting fixes, simple commits

Set with `/model` in CLI.

#### `/fewer-permission-prompts` skill

Analyzes session history, identifies safe Bash/MCP commands that keep triggering prompts, and suggests allowlist additions. Run periodically (monthly) to reduce friction.

#### `/focus` mode

Hides intermediate tool calls, shows only final results. Useful for long runs where you trust the model to do the right thing.

#### Recaps

Brief summaries of agent activity. Enabled by default. Review before resuming work after a break. Disable via `/config` if noisy.

#### Verification tools

4.7 benefits from explicit verification paths:
- **Frontend**: Chromium extension or Playwright MCP
- **Backend**: test runners, DB inspectors
- **Desktop**: Computer Use

Wire these into the workflow so Claude can self-verify without asking you to "check it".

#### `--channels` flag

Starts Claude Code with a push-message channel (Discord, Telegram, iMessage). Enables two-way remote control from chat platforms. Use when working away from the terminal.

```bash
claude --channels plugin:discord@claude-plugins-official
```
