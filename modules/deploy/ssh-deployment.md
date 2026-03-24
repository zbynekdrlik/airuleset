### SSH Deployment Patterns

When deploying to remote machines via SSH:

1. **Read credentials** from `TARGETS.md` in the project root (host, user, password/key).
2. **Never commit credentials** — `TARGETS.md` must be in `.gitignore`.
3. **Deploy steps:**
   - Stop the service: `ssh USER@HOST "systemctl stop SERVICE"` or `taskkill /F /IM app.exe`
   - Copy the binary: `scp binary USER@HOST:/path/`
   - Start the service: `ssh USER@HOST "systemctl start SERVICE"`
   - Verify: health check endpoint or process check
4. **For Windows GUI apps:** Use `schtasks` with `/it` flag (see `windows-desktop-session` module).
5. **Always verify after deploy** — never assume the deploy succeeded without checking.
