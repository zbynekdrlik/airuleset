# Break-Glass: Owner SSH to Controller
When webterm is down, SSH directly from the laptop (PowerShell):
```powershell
tailscale status                          # ensure tailscale is up
ssh airuleset@ar.newlevel.media           # primary
ssh airuleset@airuleset                   # MagicDNS fallback
tmux attach -t zbynek                     # attach supervisor session
```
Key: `zbynek-windows` in `%USERPROFILE%\.ssh\`. If missing, ask the airuleset
session to run `airuleset.py secret show --name break-glass-key` for a
one-shot recovery URL, save the key, and retry.
<!-- Last resort IP: ssh airuleset@100.101.214.103 -->
