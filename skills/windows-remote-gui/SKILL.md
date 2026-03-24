---
name: windows-remote-gui
description: Use when running GUI applications on remote Windows machines via SSH. Applies to starting apps, taking screenshots, window manipulation, or any desktop interaction over SSH.
user-invocable: true
---

# Windows Remote GUI Control via SSH

## The Problem

SSH on Windows runs in "session 0" (service session) which **cannot see or interact with the desktop**. GUI apps started via SSH start in an invisible session and silently fail for any GUI operation.

## The Solution: Scheduled Tasks

Use Windows Task Scheduler to run commands as the **logged-in desktop user** with the **interactive flag**.

```
SSH (service session 0) -> schtasks -> Desktop User Session -> GUI Access
```

## Quick Reference

### Start a GUI Application

```bash
ssh USER@HOST "schtasks /create /tn TempTask /tr \"\\\"APP_PATH\\\"\" /sc once /st 00:00 /ru DESKTOP_USER /it /f && schtasks /run /tn TempTask && schtasks /delete /tn TempTask /f"
```

### schtasks Flags Explained

| Flag           | Value        | Purpose                               |
| -------------- | ------------ | ------------------------------------- |
| `/tn`          | TaskName     | Task name (any string)                |
| `/tr`          | "command"    | Command to run (escaped quotes!)      |
| `/sc once`     | one-time     | Required but ignored (we use `/run`)  |
| `/st 00:00`    | midnight     | Required but ignored                  |
| `/ru USERNAME` | desktop user | Run as this user (must be logged in)  |
| `/it`          | interactive  | **CRITICAL** - access desktop session |
| `/f`           | force        | Overwrite existing task               |
| `/rl highest`  | elevated     | Admin privileges (optional)           |

### Run PowerShell Script as Desktop User

```bash
# 1. Write script to remote machine
ssh USER@HOST "echo YOUR_POWERSHELL_CODE > C:\\temp\\script.ps1"

# 2. Run as desktop user
ssh USER@HOST "schtasks /create /tn RunScript /tr \"powershell -ExecutionPolicy Bypass -File C:\\temp\\script.ps1\" /sc once /st 00:00 /ru DESKTOP_USER /it /f && schtasks /run /tn RunScript"

# 3. Wait for completion
sleep 3

# 4. Read output file (if script wrote one)
ssh USER@HOST "type C:\\temp\\output.txt"

# 5. Clean up
ssh USER@HOST "schtasks /delete /tn RunScript /f"
```

### Take Screenshot

```bash
# Create screenshot script
ssh USER@HOST "mkdir C:\\temp 2>nul & echo Add-Type -AssemblyName System.Windows.Forms,System.Drawing > C:\\temp\\screenshot.ps1 && echo \$b = New-Object System.Drawing.Bitmap([System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width,[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height) >> C:\\temp\\screenshot.ps1 && echo [System.Drawing.Graphics]::FromImage(\$b).CopyFromScreen(0,0,0,0,\$b.Size) >> C:\\temp\\screenshot.ps1 && echo \$b.Save('C:\\temp\\screenshot.png') >> C:\\temp\\screenshot.ps1"

# Run as desktop user
ssh USER@HOST "schtasks /create /tn Screenshot /tr \"powershell -ExecutionPolicy Bypass -File C:\\temp\\screenshot.ps1\" /sc once /st 00:00 /ru DESKTOP_USER /it /f && schtasks /run /tn Screenshot && schtasks /delete /tn Screenshot /f"

# Wait and download
sleep 2
scp USER@HOST:C:/temp/screenshot.png /tmp/screenshot.png
```

## What Works WITHOUT schtasks

| Operation       | Command                           |
| --------------- | --------------------------------- |
| List processes  | `tasklist`                        |
| Kill process    | `taskkill /IM name.exe /F`        |
| File operations | `type`, `copy`, `del`, `dir`      |
| Registry        | `reg query`, `reg add`            |
| Services        | `sc query`, `sc start`, `sc stop` |
| Network         | `netstat`, `ipconfig`             |
| Scheduled tasks | `schtasks /query`                 |

## What REQUIRES schtasks

| Operation           | Why                   |
| ------------------- | --------------------- |
| Start GUI app       | Needs desktop session |
| Take screenshot     | Needs desktop access  |
| Window manipulation | Needs desktop session |
| Interact with GUI   | Needs desktop session |

## Troubleshooting

- **App starts but no window visible:** Forgot `/it` flag, or wrong `/ru` user, or user not logged in
- **Task fails immediately:** App path needs escaped quotes: `\"\\\"path with spaces\\\"\"`
- **No response/timeout:** Desktop user not logged in, or app crashed on startup

## Startup Registration

```bash
ssh USER@HOST "reg add \"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\" /v AppName /t REG_SZ /d \"\\\"APP_PATH\\\"\" /f"
```
