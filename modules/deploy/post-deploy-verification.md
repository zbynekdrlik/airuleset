### Post-Deploy Verification

After every deployment, verify the target machine responds correctly before reporting success:

- Use `curl` or API calls to check health endpoints.
- Verify processes are running: `ssh USER@HOST "tasklist | findstr app"` or `pgrep`.
- When a verification tool fails (e.g., ECONNREFUSED), try alternative tools (`curl` via Bash, MCP tools, SSH) before concluding the target is unreachable.
- NEVER claim verification passed without actually confirming via a working tool.
- NEVER claim "deployed successfully" based on the deploy step exiting cleanly — the app may have crashed on startup.
- If post-deploy verification fails, investigate and fix immediately. A deploy is not done until it is verified working.
