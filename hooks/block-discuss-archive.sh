#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — BLOCK a `discuss.channel` archive/deactivation on
# SHARED-STREAM boxes only (#853). The sanctioned disposition after a closing
# note is TTL self-hide (`schedule_close_hide_guarded` / the #5630 mechanism),
# NEVER `active=False` archiving (the #788 doctrine). Streams kept archiving
# despite prose — this guard mechanically prevents it.
#
# Shapes blocked (any discuss.channel archive/deactivation via XML-RPC/JSON-RPC):
#   - discuss.channel  action_archive
#   - discuss.channel  toggle_active
#   - discuss.channel  write  with  active=False / active: false
#
# BOX-CLASS GATE: no-op unless `~/.claude/airuleset-box-class` == `shared-stream`.
# A workstation (dev1/dev2/gk) or a box with no marker exits 0 immediately — the
# gk/owner can archive for cleanup, never blocked.
#
# Bypass (rare, NOT auto-logged — same honest convention as
# block-heavy-build-toolchain.sh): `# airuleset:discuss-archive-ok <reason>` as
# a trailing comment.
#
# DELIBERATELY NARROW (fail toward ALLOW): only KNOWN archive/deactivation shapes
# are blocked. Accepted residuals: uppercase `FALSE`; `discuss.channel` `unlink`
# (outright deletion — strictly worse than archiving); a bypass marker inside a
# quoted string (low exploitability, same as block-heavy-build-toolchain.sh).
#
# Reads `.tool_input.command` on STDIN. Exit 2 = block (reason on STDERR).
# Exit 0 = allow. ANY classifier malfunction FAILS OPEN.

# --- BOX-CLASS GATE: no-op off a shared-stream box -------------------------
BOX_CLASS_FILE="${HOME:-/nonexistent}/.claude/airuleset-box-class"
CLASS="$(cat "$BOX_CLASS_FILE" 2>/dev/null | head -1 | tr -d '[:space:]' || true)"
[ "$CLASS" = "shared-stream" ] || exit 0

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# --- Bypass check -----------------------------------------------------------
if printf '%s' "$CMD" | grep -q '# airuleset:discuss-archive-ok'; then
    exit 0
fi

# --- Classifier (Python — same pattern as block-heavy-build-toolchain.sh) ----
RC=0
python3 - "$CMD" <<'PYEOF' 1>&2 2>&1 || RC=$?
import re
import sys

cmd = sys.argv[1]

# Only fire when the command mentions discuss.channel in an RPC context
if not re.search(r'discuss\.channel', cmd, re.IGNORECASE):
    sys.exit(0)

# Check for the three banned shapes:
# 1. action_archive method call
if re.search(r'action_archive', cmd, re.IGNORECASE):
    print(
        "\n🚫 BLOCKED: discuss.channel action_archive is banned on stream boxes.\n"
        "Use the sanctioned TTL self-hide instead:\n"
        "  schedule_close_hide_guarded(channel_id, hours=None) via /json/2\n"
        "(see skills/odoo-discuss-xmlrpc/handover-compose.md, #788/#853).\n"
        "Bypass (rare): # airuleset:discuss-archive-ok <reason>\n",
        file=sys.stderr,
    )
    sys.exit(2)

# 2. toggle_active method call
if re.search(r'toggle_active', cmd, re.IGNORECASE):
    print(
        "\n🚫 BLOCKED: discuss.channel toggle_active is banned on stream boxes.\n"
        "Use the sanctioned TTL self-hide instead:\n"
        "  schedule_close_hide_guarded(channel_id, hours=None) via /json/2\n"
        "(see skills/odoo-discuss-xmlrpc/handover-compose.md, #788/#853).\n"
        "Bypass (rare): # airuleset:discuss-archive-ok <reason>\n",
        file=sys.stderr,
    )
    sys.exit(2)

# 3. active=False / active: false in a write context
#    Match Python dict syntax, JSON syntax, AND kwarg/attribute forms
if re.search(r"""['"]active['"]\s*:\s*(False|false)\b""", cmd) or \
   re.search(r"""\bactive\s*=\s*(False|false)\b""", cmd):
    print(
        "\n🚫 BLOCKED: setting discuss.channel active=False is banned on stream boxes.\n"
        "Use the sanctioned TTL self-hide instead:\n"
        "  schedule_close_hide_guarded(channel_id, hours=None) via /json/2\n"
        "(see skills/odoo-discuss-xmlrpc/handover-compose.md, #788/#853).\n"
        "Bypass (rare): # airuleset:discuss-archive-ok <reason>\n",
        file=sys.stderr,
    )
    sys.exit(2)

# No banned shape found
sys.exit(0)
PYEOF

[ "$RC" -eq 2 ] || exit 0
exit 2
