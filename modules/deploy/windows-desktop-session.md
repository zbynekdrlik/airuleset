### Windows Desktop Session Requirements

GUI applications MUST run in the user's interactive desktop session, NOT as a background service or in Session 0 (SYSTEM).

**Use Windows Task Scheduler with the `/it` (interactive) flag:**

```powershell
$action = New-ScheduledTaskAction -Execute $ExePath -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtLogon -User "USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "AppName" -Action $action -Trigger $trigger -Principal $principal
Start-ScheduledTask -TaskName "AppName"
```

**Verify the process runs in the correct session:**

```powershell
$proc = Get-Process -Name "AppName"
if ($proc.SessionId -eq 0) { throw "Must run in user session, not SYSTEM" }
```

SSH on Windows runs in Session 0 which cannot interact with the desktop. Always use `schtasks` with `/it` to start GUI apps from SSH. See the `windows-remote-gui` skill for detailed patterns.
