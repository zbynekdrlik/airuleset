### Script Failure Policy

All scripts and automation MUST fail loudly on errors:

- **Bash:** Use `set -euo pipefail` at the top of every script.
- **PowerShell:** Use `$ErrorActionPreference = "Stop"`.
- **Python:** Never silently catch and ignore exceptions.
- **CI steps:** Every step must exit non-zero on failure. No silent fallbacks.

If a script encounters an error it cannot handle, it must exit with a non-zero code and a clear error message. Silent failures are worse than crashes — they hide bugs and waste debugging time.

#### Enforcement

A `pre-write-script-check.sh` PreToolUse(Write|Edit) hook blocks new content that violates the Bash and Python rules above (never retroactively). Bypass (rare, logged): `# airuleset:script-ok <reason>` anywhere in the written content. PowerShell and CI steps have no hook — those two are on you.
