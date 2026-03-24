---
name: deploy-ssh
description: Deploy binaries or scripts to remote machines via SSH. Covers the full stop-copy-start-verify cycle.
user-invocable: true
---

# SSH Deployment

## Read Credentials

Check `TARGETS.md` in the project root for host, user, and authentication details.
TARGETS.md must be in .gitignore — never commit credentials.

## Deploy Steps

1. **Stop service:**
   - Linux: `ssh USER@HOST "systemctl stop SERVICE"`
   - Windows: `ssh USER@HOST "taskkill /F /IM app.exe"`

2. **Copy binary:**
   - `scp binary USER@HOST:/path/to/install/dir/`

3. **Start service:**
   - Linux: `ssh USER@HOST "systemctl start SERVICE"`
   - Windows GUI: Use `schtasks` with `/it` flag (see windows-remote-gui skill)
   - Windows service: `ssh USER@HOST "sc start SERVICE"`

4. **Verify deployment:**
   - Health check: `curl -sf http://HOST:PORT/health`
   - Process check: `ssh USER@HOST "tasklist | findstr app"` or `pgrep`
   - Never assume deploy succeeded without verification

## Windows GUI Apps

GUI apps cannot be started via SSH directly (Session 0 limitation).
Use the `windows-remote-gui` skill for `schtasks`-based launching with `/it` flag.

## Troubleshooting

- If service fails to start, check logs: `ssh USER@HOST "journalctl -u SERVICE -n 50"` (Linux)
- If binary not found after copy, verify the path and permissions
- If health check fails, give the app time to initialize (up to 30 seconds), then investigate
