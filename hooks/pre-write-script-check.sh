#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Write and Edit matchers — registered twice in hooks.json,
# same script) — issue #13 sub-item 2, script-failure-policy.md.
#
# Two independent checks on the content being WRITTEN right now (never a
# repo-wide scan — pre-existing files/violations are never retroactively
# flagged):
#
#   1. A NEW .sh file (Write tool only — Edit's payload is a partial
#      old_string/new_string diff and can't reliably represent "does the
#      file's header have `set -euo pipefail`"; this check is scoped to
#      Write, which always carries the FULL intended content) must start
#      with a shebang and `set -euo pipefail` within the first ~15 lines.
#
#   2. NEW Python content (Write's full `content`, or Edit's `new_string`)
#      must not introduce an `except ...:` clause whose body is a bare
#      `pass` — silently swallowing an exception with zero logging, banned
#      by script-failure-policy.md ("Python: Never silently catch and
#      ignore exceptions") and comprehensive-logging.md ("every catch logs
#      the error with context, even when handled"). This is intentionally
#      NOT limited to bare `except:` / `except Exception:` — ANY exception
#      type swallowed by a lone `pass` matches the rule's plain wording.
#      ruff's default rule set (E4/E7/E9/F) only catches bare `except:`
#      (E722); it does NOT catch `except Exception: pass` — verified via a
#      local ruff run before writing this hook. A repo-wide ruff rule (e.g.
#      selecting S110) was investigated and rejected: this repo's OWN code
#      already has 24 pre-existing `except ...: pass` sites (mostly
#      best-effort probes/cleanup) that would all break `ruff check .`
#      immediately — a cross-cutting fix far outside this ticket's scope.
#      A write-time hook sidesteps that entirely: it only ever looks at
#      content being written NOW, so existing files are untouched.
#
# Bypass (rare, logged): put '# airuleset:script-ok <reason>' anywhere in
# the written content.
#
# Exit code 2 = block the tool call.

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && exit 0

RESULT=$(python3 - "$PAYLOAD" <<'PYEOF'
import json
import re
import sys

payload_raw = sys.argv[1]
try:
    payload = json.loads(payload_raw)
except Exception:
    sys.exit(0)

ti = payload.get("tool_input", {}) or {}
file_path = ti.get("file_path", "") or ""
if not file_path:
    sys.exit(0)

# Which shape of content are we looking at?
is_write = "content" in ti
is_edit = "new_string" in ti

if is_write:
    text = ti.get("content", "") or ""
elif is_edit:
    text = ti.get("new_string", "") or ""
else:
    sys.exit(0)

if not text:
    sys.exit(0)

BYPASS_RE = re.compile(r'#\s*airuleset:script-ok\s+(.+)')
bypass_m = BYPASS_RE.search(text)

violations = []

# --- Check 1: new .sh file must start with `set -euo pipefail` ------------
# Write only (Edit payloads don't reliably represent the file header).
if is_write and file_path.endswith(".sh"):
    lines = text.splitlines()
    non_blank = [ln for ln in lines if ln.strip() != ""]
    has_shebang = bool(non_blank) and non_blank[0].lstrip().startswith("#!")
    has_pipefail = any(re.match(r'^\s*set\s+-euo\s+pipefail\b', ln)
                        for ln in lines[:15])
    if has_shebang and not has_pipefail:
        violations.append(
            "New .sh file '%s' has a shebang but no 'set -euo pipefail' in "
            "the first 15 lines. Per script-failure-policy.md, every Bash "
            "script must fail loudly: add 'set -euo pipefail' right after "
            "the shebang." % file_path
        )
    elif not has_shebang and not has_pipefail:
        violations.append(
            "New .sh file '%s' is missing both a shebang (#!/usr/bin/env "
            "bash) and 'set -euo pipefail'. Add both at the top." % file_path
        )

# --- Check 2: new Python content must not swallow exceptions silently -----
if file_path.endswith(".py"):
    lines = text.splitlines()
    except_re = re.compile(r'^(\s*)except\b.*:\s*(#.*)?$')
    pass_re = re.compile(r'^(\s*)pass\s*(#.*)?$')
    for i, line in enumerate(lines):
        m = except_re.match(line)
        if not m:
            continue
        except_indent = m.group(1)
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j >= len(lines):
            continue
        pm = pass_re.match(lines[j])
        if not pm:
            continue
        # `pass` must be the ONLY statement in the block — i.e. the next
        # non-blank line after it must NOT be at the same (or deeper)
        # indent as `pass` (which would mean more statements follow it in
        # the same except body). A dedent or EOF confirms `pass` is sole.
        pass_indent = pm.group(1)
        k = j + 1
        while k < len(lines) and lines[k].strip() == "":
            k += 1
        sole = True
        if k < len(lines):
            next_indent = len(lines[k]) - len(lines[k].lstrip())
            if lines[k].strip() and next_indent >= len(pass_indent):
                sole = False
        if sole:
            violations.append(
                "New Python content in '%s' line %d: '%s' followed by a "
                "bare 'pass' — silently swallows the exception with no "
                "logging. script-failure-policy.md: 'Python: Never "
                "silently catch and ignore exceptions.' Log the error "
                "with context (comprehensive-logging.md), even when the "
                "exception is genuinely expected/handled." %
                (file_path, i + 1, line.strip())
            )

if violations and not bypass_m:
    for v in violations:
        print(v)
    sys.exit(2)

if violations and bypass_m:
    import os
    import subprocess
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        project = os.path.basename(top) if top else "unknown"
    except Exception:
        project = "unknown"
    home = os.environ.get("HOME", "")
    log_path = os.path.join(home, "devel", "airuleset", "audits",
                             "script-check-bypasses.log")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            import datetime
            ts = datetime.datetime.now().astimezone().isoformat()
            f.write("%s  project=%s  file=%s  reason=%s\n" %
                    (ts, project, file_path, bypass_m.group(1).strip()))
    except Exception:
        pass
    sys.exit(0)

sys.exit(0)
PYEOF
) || RC=$?
RC=${RC:-0}

if [ "$RC" -ne 0 ]; then
    echo "" >&2
    echo "🚫 BLOCKED: script-failure-policy.md violation in new content." >&2
    echo "" >&2
    echo "$RESULT" | sed 's/^/  /' >&2
    echo "" >&2
    echo "  Bypass (rare, logged): add '# airuleset:script-ok <reason>' " >&2
    echo "  anywhere in the content." >&2
    echo "" >&2
    exit 2
fi

exit 0
