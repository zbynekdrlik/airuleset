---
name: win-mcp
description: Manage WinRemote MCP connections to remote Windows machines. Use when adding, removing, listing, or troubleshooting remote Windows desktop connections. Also use for installing winremote-mcp on Windows machines.
user-invocable: true
---

# Windows Remote MCP Connection Manager

## Commands

Parse the user's intent from these patterns:

- `/win add <name> <ip> <key> [port]` — Add a Windows machine connection
- `/win remove <name>` — Remove a connection
- `/win list` — List configured Windows connections
- `/win install <host>` — Install winremote-mcp on a Windows machine via SSH
- `/win status` — Check connectivity to all configured Windows machines

## Add Connection

When user says `/win add <name> <ip> <key> [port]`:

1. Default port is 8090 if not specified
2. Run: `claude mcp add --transport http --scope user "win-<name>" "http://<ip>:<port>/mcp" --header "Authorization: Bearer <key>"`
3. Confirm the connection was added
4. Remind user to restart Claude Code for the new MCP server to take effect

## Add Scoped Connection (restricted to specific project)

When user says `/win add <name> <ip> <key> --project <path>`:

1. Create or update `<path>/.mcp.json` with the connection:

```json
{
  "mcpServers": {
    "win-<name>": {
      "type": "http",
      "url": "http://<ip>:<port>/mcp",
      "headers": {
        "Authorization": "Bearer <key>"
      }
    }
  }
}
```

2. This restricts the connection to only be available when working in that project directory

## Remove Connection

Run: `claude mcp remove "win-<name>" --scope user`

## List Connections

Run: `claude mcp list` and filter for entries starting with `win-`

## Install on Remote Windows Machine

When user says `/win install <host>`:

1. Check SSH connectivity: `ssh <host> "echo ok"`
2. Run the installer remotely: `ssh <host> "powershell -ExecutionPolicy Bypass -Command \"irm https://raw.githubusercontent.com/zbynekdrlik/winremote-setup/master/install.ps1 | iex\""`
3. Parse the output for the auth key and IP
4. Automatically add the connection using the add flow above

Or tell the user to run this on the Windows machine directly (PowerShell as Admin):

```powershell
irm https://raw.githubusercontent.com/zbynekdrlik/winremote-setup/master/install.ps1 | iex
```

## Restricting Which Claude Controls Which Windows

**Option A: Project-scoped MCP** (recommended)

- Add connections with `--project <path>` flag
- Each project directory only sees its own Windows machines

**Option B: User-scoped (global)**

- Default behavior — all Claude sessions see all Windows machines

**Option C: Separate .mcp.json files**

- Create different `.mcp.json` in different working directories
- Claude Code only loads MCP servers from the current project's `.mcp.json`

## Troubleshooting

If connection fails:

1. Check the server is running on Windows: `ssh <host> "netstat -an | findstr <port>"`
2. Check firewall: `ssh <host> "netsh advfirewall firewall show rule name=\"WinRemote MCP\""`
3. Test HTTP directly: `curl -s -H "Authorization: Bearer <key>" http://<ip>:<port>/mcp`
4. Check Python process: `ssh <host> "tasklist | findstr python"`

## Notes

- WinRemote MCP runs in the desktop session — it has full GUI access unlike SSH
- The server must be started by the logged-in desktop user (or via auto-start task)
- If the Windows machine reboots, the server auto-starts if installed with `-AutoStart`
- Auth key is sent via HTTP header — on LAN this is fine, for internet use TLS
