### Windows MCP Server Error Handling

#### ALWAYS use MCP tools first — never fall back to SSH

When a project has Windows MCP servers configured (`.mcp.json` with `win-*` entries), you MUST use `mcp__win-*` tools for ALL Windows operations. **Do NOT use SSH as a workaround or shortcut.** The MCP tools exist for a reason — they handle desktop session, screenshots, GUI interaction, and PowerShell execution correctly.

Anti-patterns:

- Using `ssh user@host "powershell ..."` when `mcp__win-*` tools are available → **WRONG.** Use the MCP tool.
- Falling back to SSH because MCP "might not work" → **WRONG.** Try the MCP tool first. If it fails, STOP.
- Using SSH to take screenshots → **WRONG.** Use `mcp__win-*__screenshot` or equivalent.

#### If MCP is unreachable — STOP IMMEDIATELY

**If any `mcp__win-*` tool call fails with a connection error, timeout, or "server not available" error: STOP all work immediately and alert the user.**

- Do NOT continue working
- Do NOT retry silently
- Do NOT work around it with SSH
- Do NOT investigate why it's down (that's the user's job)
- Do NOT check with curl if the MCP endpoint responds

**STOP and alert:**

> **MCP CONNECTION ERROR**: Windows remote MCP server `win-XXX` is unreachable. Please check that the MCP server is running and fix the connection before I continue.

This applies to ALL Windows remote MCP servers (any tool starting with `mcp__win-`). The user must fix the MCP connection before any work can proceed. Do not attempt any alternative approach.
