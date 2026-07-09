### Windows MCP Error Handling → skill `windows-remote-gui`

Projects with `mcp__win-*` servers: ALWAYS use the MCP tools, NEVER fall back to SSH; if any `mcp__win-*` call fails with connection/timeout/unavailable — STOP all work immediately and alert the user (no retries, no workarounds). Full protocol moved VERBATIM into the `windows-remote-gui` skill — load it before any Windows-remote work.
