# Discord Notification on Claude Code Stop

## Purpose

Send a Discord message every time any Claude Code session stops and waits for user input, across all projects and machines. This eliminates the need to manually check each terminal.

## Architecture

```
Claude Code (any project, any machine)
    |
    v  Stop hook fires
    |
    v  hooks/stop-notify-discord.sh
    |  - Reads stdin JSON: cwd, transcript_path
    |  - Extracts project name from cwd
    |  - Extracts last ~500 chars from transcript
    |  - Detects machine (hostname)
    |  - curl POST -> n8n webhook (fire-and-forget)
    |
    v  n8n workflow: "Claude Code Notifications"
    |  - Webhook trigger (receives JSON)
    |  - Discord node -> channel 1491798759103795301
    |
    v  Discord message in #claude-notifications
```

## Components

### 1. Stop Hook Script (`hooks/stop-notify-discord.sh`)

**Trigger:** Claude Code Stop event (fires every time Claude finishes responding).

**Input:** JSON on stdin from Claude Code with fields:
- `cwd` — current working directory (used to derive project name)
- `transcript_path` — path to conversation JSONL (used to extract last message)
- `session_id` — unique session identifier

**Behavior:**
1. Read stdin JSON, extract `cwd` and `transcript_path`
2. Derive project name from `cwd` (basename of git root, or basename of cwd)
3. Extract last Claude assistant message from transcript JSONL (~500 chars)
4. Detect machine via `hostname -s`
5. POST JSON to n8n webhook URL (from `CLAUDE_DISCORD_WEBHOOK_URL` env var)
6. Fire and forget — curl runs in background, exit 0 always

**Safety rules:**
- `exit 0` always — notification failure must NEVER block Claude (Stop hook blocking causes infinite loops)
- Background curl (`&>/dev/null &`) — no waiting for response
- Skip silently if `CLAUDE_DISCORD_WEBHOOK_URL` is not set (no error, no warning)
- Skip if `jq` is not available
- Timeout on curl: 5 seconds max

**Payload:**
```json
{
  "project": "restreamer",
  "machine": "dev1",
  "message": "PR ready for review. CI green, all 4 jobs passed...",
  "timestamp": "2026-04-09T14:30:00Z",
  "session_id": "abc123"
}
```

### 2. n8n Workflow: "Claude Code Notifications"

**Nodes:**
1. **Webhook** — POST endpoint, receives JSON payload
2. **Discord** — sends message to channel `1491798759103795301`

**Discord message format:**
```
**Claude waiting** | restreamer (dev1)
> PR ready for review. CI green, all 4 jobs passed...
_14:30 UTC_
```

### 3. Hook Registration (`settings/hooks.json`)

Add to the existing Stop matcher alongside `stop-check-ci.sh`:
```json
{
  "type": "command",
  "command": "bash ~/devel/airuleset/hooks/stop-notify-discord.sh",
  "timeout": 10
}
```

### 4. Environment Variable

`CLAUDE_DISCORD_WEBHOOK_URL` — the n8n webhook URL. Set in `~/.bashrc` on both dev1 and dev2. Not stored in git.

## Message Extraction Strategy

The transcript at `transcript_path` is a JSONL file. Each line is a JSON object. The last assistant message is extracted by:
1. `tac` the file (read backwards)
2. Find first line with `"role":"assistant"`
3. Extract the text content, truncate to 500 chars

If transcript extraction fails, fall back to "No message available".

## Deployment

1. Create n8n workflow via MCP, get webhook URL
2. Write `stop-notify-discord.sh` hook script
3. Register in `hooks.json`
4. Set `CLAUDE_DISCORD_WEBHOOK_URL` in `~/.bashrc` on dev1 and dev2
5. `python3 airuleset.py push` to deploy to both machines

## Edge Cases

- **n8n down:** curl times out after 5s, hook exits 0, Claude unaffected
- **No webhook URL configured:** hook exits 0 silently
- **No transcript file:** falls back to "No message available"
- **Very long Claude output:** truncated to 500 chars
- **Multiple Claude sessions stopping simultaneously:** each fires independently, n8n handles concurrent webhooks
