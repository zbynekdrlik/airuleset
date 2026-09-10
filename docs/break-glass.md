# Break-Glass: Owner Access to Controller

## From any device (phone, borrowed PC — no tailscale, no key)
```
ssh airuleset@ar.newlevel.media
# password from the owner's password manager
tmux attach -t zbynek
```
Password re-delivery (from the controller itself):
`airuleset.py secret show --file ~/.secrets/airuleset-ssh-password`

Password rotation: generate a new strong random password, then:
`echo 'airuleset:<newpass>' | sudo chpasswd`
Update `~/.secrets/airuleset-ssh-password` (0600) and re-deliver via `secret show`.

## From the laptop (tailscale + key)
```powershell
tailscale status                          # ensure tailscale is up
ssh airuleset@ar.newlevel.media           # primary (public IP)
ssh airuleset@airuleset                   # MagicDNS fallback
ssh airuleset@100.101.214.103             # last resort (raw tailscale IP)
tmux attach -t zbynek                     # attach supervisor session
```
Key: `zbynek-windows` in `%USERPROFILE%\.ssh\`. If missing, ask the airuleset
session to run `airuleset.py secret show break-glass-key` for a one-shot
recovery URL, save the key, and retry.

## IP reference
- `ar.newlevel.media` → `159.69.209.249` (controller public IP, unproxied A record)
- `airuleset` → `100.101.214.103` (tailscale MagicDNS)
- fail2ban sshd jail: maxretry 3, bantime 1 h; owner laptop in ignoreip
