### Windows MCP Server Error Handling

**If any `mcp__win-*` tool call fails with a connection error, timeout, or "server not available" error: STOP all work immediately and alert the user.** Do NOT continue working, do NOT retry silently, do NOT work around it. The user must fix the MCP connection before any work can proceed.

This applies to ALL Windows remote MCP servers (any tool starting with `mcp__win-`), not just a specific one.

Alert format:

> **MCP CONNECTION ERROR**: A Windows remote MCP server is unreachable. Please check that the MCP server is running and fix the connection before I continue.
