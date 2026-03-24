---
paths:
  - "*.ps1"
  - "scripts/"
---

### Windows Deployment Rules

- PowerShell scripts MUST use `$ErrorActionPreference = "Stop"` at the top.
- GUI apps must be started via `schtasks` with `/it` flag for interactive desktop session.
- Never use `Start-Process` for GUI apps from CI/SSH — it runs in Session 0 (invisible).
- Always verify the process runs in `SessionId > 0` after starting.
- Use `taskkill /F /IM app.exe` to stop existing instances before deploying.
- After deploy, verify the app is running and responsive (health check or process check).
