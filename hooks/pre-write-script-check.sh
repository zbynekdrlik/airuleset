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
#      Write, which always carries the FULL intended content) that starts
#      with a shebang must have `set -euo pipefail` within the first ~15
#      lines. Scoped to files that do NOT already exist on disk (a
#      full-file REWRITE of an existing legacy script is out of scope —
#      same "never retroactively flag pre-existing content" principle) and
#      that DO start with a shebang — a shebang-less .sh is the SOURCED
#      lib/env convention (meant to be `source`d, never executed directly),
#      where `set -euo pipefail` would be WRONG (it leaks into the sourcing
#      shell), so such files are never required to add one.
#
#   2. NEW Python content (Write's full `content`, or Edit's `new_string`)
#      must not introduce an `except ...:` clause whose body is a bare
#      `pass` — silently swallowing an exception with zero logging, banned
#      by script-failure-policy.md ("Python: Never silently catch and
#      ignore exceptions") and comprehensive-logging.md ("every catch logs
#      the error with context, even when handled"). This is intentionally
#      NOT limited to bare `except:` / `except Exception:` — ANY exception
#      type swallowed by a lone `pass` matches the rule's plain wording,
#      one-liner (`except X: pass`) or multi-line. Detection is AST-EXACT
#      when the text parses standalone (an ExceptHandler whose body is
#      exactly one Pass node) — this catches the one-liner form for free
#      and NEVER false-matches a docstring/string-literal that merely
#      CONTAINS the text "except X:\n    pass" (AST only sees real syntax).
#      An Edit's new_string is often a partial snippet that does NOT parse
#      standalone (e.g. an indented `try:` with no enclosing def) — falls
#      back to a line-regex scan (now also one-liner-aware) for those.
#      For an Edit, a hit is suppressed when the SAME except-pass block
#      (by source-line signature) already existed in old_string — Edit's
#      old_string/new_string routinely carry unchanged lines purely to
#      make old_string a unique match; such carried context is not being
#      INTRODUCED by this edit. ruff's default rule set (E4/E7/E9/F) only
#      catches bare `except:` (E722); it does NOT catch
#      `except Exception: pass` — verified via a local ruff run before
#      writing this hook. A repo-wide ruff rule (e.g. selecting S110) was
#      investigated and rejected: this repo's OWN code already has 24
#      pre-existing `except ...: pass` sites (mostly best-effort
#      probes/cleanup) that would all break `ruff check .` immediately — a
#      cross-cutting fix far outside this ticket's scope. A write-time hook
#      sidesteps that entirely: it only ever looks at content being written
#      NOW, so existing files are untouched.
#
# Bypass (rare, logged): put '# airuleset:script-ok <reason>' anywhere in
# the written content.
#
# Exit code 2 = block the tool call.

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && exit 0

# The payload is fed to the embedded python3 child over STDIN via a
# here-string, NOT as an argv arg — Linux caps a single argv/envp string at
# MAX_ARG_STRLEN (128KB on 4KB-page systems), and a large Write/Edit
# payload blew past that with "Argument list too long", silently BLOCKING
# every large write with an empty reason regardless of file type or
# content. `python3 <(cat <<'PYEOF' ... PYEOF)` runs the embedded script
# from a process-substitution fd (not argv), leaving the real stdin free
# for the here-string payload — unbounded by the argv-length limit.
RESULT=$(python3 <(cat <<'PYEOF'
import ast
import json
import re
import sys

payload_raw = sys.stdin.read()
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
    import os as _os
    try:
        exists_on_disk = _os.path.isfile(file_path)
    except Exception:
        exists_on_disk = False
    lines = text.splitlines()
    non_blank = [ln for ln in lines if ln.strip() != ""]
    has_shebang = bool(non_blank) and non_blank[0].lstrip().startswith("#!")
    has_pipefail = any(re.match(r'^\s*set\s+-euo\s+pipefail\b', ln)
                        for ln in lines[:15])
    # Only a TRUE NEW file (not already on disk) that itself declares intent
    # to be directly executed (has a shebang) is required to fail loudly.
    # A file already on disk is a rewrite of pre-existing content (out of
    # scope, same principle as check 2); a shebang-less .sh is the sourced
    # lib/env convention, where `set -euo pipefail` would be wrong.
    if not exists_on_disk and has_shebang and not has_pipefail:
        violations.append(
            "New .sh file '%s' has a shebang but no 'set -euo pipefail' in "
            "the first 15 lines. Per script-failure-policy.md, every Bash "
            "script must fail loudly: add 'set -euo pipefail' right after "
            "the shebang." % file_path
        )

# --- Check 2: new Python content must not swallow exceptions silently -----

_EXCEPT_RE = re.compile(r'^(\s*)except\b.*:\s*(#.*)?$')
_ONELINER_RE = re.compile(r'^(\s*)except\b[^:]*:\s*pass\s*(#.*)?$')
_PASS_RE = re.compile(r'^(\s*)pass\s*(#.*)?$')


def regex_except_pass_hits(lines):
    """Best-effort line-regex scan (fallback for text that does not parse
    as standalone Python, e.g. a partial Edit snippet). Returns
    [(line_no_0based, except_line_text)]. Catches the one-liner form
    directly, and the multi-line `except:\\n    pass` form (requiring
    `pass` be the SOLE statement in the block — a dedent or EOF right
    after it, not more statements at the same indent)."""
    hits = []
    for i, line in enumerate(lines):
        if _ONELINER_RE.match(line):
            hits.append((i, line))
            continue
        m = _EXCEPT_RE.match(line)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j >= len(lines):
            continue
        pm = _PASS_RE.match(lines[j])
        if not pm:
            continue
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
            hits.append((i, line))
    return hits


def ast_except_pass_hits(text):
    """AST-EXACT detection: an ExceptHandler whose body is a single Pass
    statement. Handles the one-liner form for free (AST doesn't care about
    formatting) and NEVER false-matches a string literal/docstring that
    merely CONTAINS the text "except X:\\n    pass" — only real syntax
    counts. Returns [(line_no_0based, source_line)], or None if `text`
    does not parse as standalone Python (a partial Edit snippet is common
    — caller falls back to regex_except_pass_hits)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    lines = text.splitlines()
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.ExceptHandler)
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)):
            lineno = getattr(node, "lineno", None)
            if lineno and 0 < lineno <= len(lines):
                hits.append((lineno - 1, lines[lineno - 1]))
    return hits


def except_pass_hits(text):
    """AST first (exact); regex fallback only when `text` doesn't parse
    standalone."""
    hits = ast_except_pass_hits(text)
    if hits is None:
        hits = regex_except_pass_hits(text.splitlines())
    return hits


if file_path.endswith(".py"):
    new_hits = except_pass_hits(text)

    preexisting_sigs = set()
    if is_edit:
        old_text = ti.get("old_string", "") or ""
        if old_text:
            preexisting_sigs = {ln.strip() for _, ln in except_pass_hits(old_text)}

    for i, line in new_hits:
        if is_edit and line.strip() in preexisting_sigs:
            # This exact except-pass block already existed, verbatim, in
            # old_string — carried as unique-match CONTEXT, not introduced
            # by this edit.
            continue
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
    except Exception as e:
        print("script-check-bypasses.log write failed: %r" % (e,), file=sys.stderr)
    sys.exit(0)

sys.exit(0)
PYEOF
) <<< "$PAYLOAD") || RC=$?
RC=${RC:-0}

if [ "$RC" -eq 2 ]; then
    echo "" >&2
    echo "🚫 BLOCKED: script-failure-policy.md violation in new content." >&2
    echo "" >&2
    echo "$RESULT" | sed 's/^/  /' >&2
    echo "" >&2
    echo "  Bypass (rare, logged): add '# airuleset:script-ok <reason>' " >&2
    echo "  anywhere in the content." >&2
    echo "" >&2
    exit 2
elif [ "$RC" -ne 0 ]; then
    # A non-2 nonzero exit means the CHECK ITSELF malfunctioned (missing
    # python3, a bug in the embedded script) — never a real content
    # violation. Fail CLOSED (a malfunctioning gate must not silently pass
    # through content it never actually inspected) but say so HONESTLY
    # instead of printing an empty "BLOCKED" with no reason.
    echo "" >&2
    echo "🚫 BLOCKED (fail-closed): pre-write-script-check.sh internal error" >&2
    echo "  — python3 exited $RC instead of running the check." >&2
    echo "$RESULT" | sed 's/^/  /' >&2
    echo "" >&2
    echo "  This is a HOOK MALFUNCTION, not necessarily a real violation —" >&2
    echo "  investigate and fix the hook (or install python3) before retrying." >&2
    echo "" >&2
    exit 2
fi

exit 0
