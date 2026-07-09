### Script Failure Policy

All scripts and automation MUST fail loudly on errors:

- **Bash:** Use `set -euo pipefail` at the top of every script.
- **PowerShell:** Use `$ErrorActionPreference = "Stop"`.
- **Python:** Never silently catch and ignore exceptions.
- **CI steps:** Every step must exit non-zero on failure. No silent fallbacks.

If a script encounters an error it cannot handle, it must exit with a non-zero code and a clear error message. Silent failures are worse than crashes — they hide bugs and waste debugging time.

#### Enforcement — write-time hook, new content only

`ruff check .` (the push gate) already catches bare `except:` for free (pycodestyle E722, in ruff's default rule set) — but NOT `except Exception: pass` (that needs the separate `S110` rule, which is NOT enabled: this repo's own code has 24 pre-existing `except ...: pass` sites that would all break the gate). Instead, a `pre-write-script-check.sh` PreToolUse(Write|Edit) hook blocks it going forward: any NEW Python content (Write's full file, or Edit's inserted text) introducing an `except` clause whose sole body is `pass` is blocked — pre-existing files are never retroactively scanned. The same hook blocks a brand-NEW `.sh` file (Write only) that's missing `set -euo pipefail` in its first 15 lines. Bypass (rare, logged): `# airuleset:script-ok <reason>` anywhere in the written content.
