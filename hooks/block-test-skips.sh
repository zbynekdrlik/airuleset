#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) — blocks git push if the outgoing diff ADDS a
# test-skip / tautology pattern (modules/ci/test-strictness.md's mechanical
# banned-syntax list — see issue #10). pre-push-test-check.sh already covers
# test PRESENCE + RED->GREEN order; this hook covers the CONTENT of test
# files: `#[ignore]`, `.skip(`, `xit(`, `pytest.mark.skip`, `unittest.skip`,
# `assume!(`, `assert!(true)`, `expect(true).toBe(true)`, and cheaply
# detectable empty test bodies (incl. the 2-line `def test_x():\n    pass`
# form — a Python multiline regex is used for this, not bash grep).
#
# Only ADDED lines (git diff -U0, `^\+` excluding `+++`) in TEST files are
# scanned — pre-existing skips the pusher didn't write don't block them, and
# non-test files are never scanned (a production `assert!(true)` sentinel,
# if ever legitimate, is out of scope for this gate).
#
# Bypass: `# airuleset:test-skip-ok <reason>` in the latest commit message
# (checked the same way pre-push-test-check.sh honors `[no-test: reason]`).
# Every bypass is logged to audits/test-skip-bypasses.log.
#
# Exit code 2 = block the tool call.

# Read the tool payload from STDIN (current CC contract; $TOOL_INPUT is the dead
# old env var, kept as fallback). See block-sensitive-staging.sh for the rationale.
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
[ -z "$INPUT" ] && INPUT="$PAYLOAD"

# Only act on REAL `git push` commands (strip quoted substrings first so a
# command that merely CONTAINS the words "git push" inside a commit message,
# echo string, or file path does NOT falsely trigger this gate).
CMD_NOQUOTES=$(printf '%s' "$INPUT" | sed "s/'[^']*'//g; s/\"[^\"]*\"//g")
if ! printf '%s' "$CMD_NOQUOTES" | grep -qE 'git([[:space:]]+-[^[:space:]]+)*[[:space:]]+push([[:space:]]|$)'; then
    exit 0
fi

# Must be in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")
PROJECT=$(basename "$(git rev-parse --show-toplevel)")

AUDIT_LOG="$HOME/devel/airuleset/audits/test-skip-bypasses.log"
mkdir -p "$(dirname "$AUDIT_LOG")"

# Bypass: `# airuleset:test-skip-ok <reason>` in the latest commit message.
# Flatten newlines first so a reason that wraps onto a second line still
# matches on one logical line (same fix as pre-push-test-check's [no-test:]
# multi-line bug, issue #2).
LAST_MSG=$(git log -1 --pretty=%B 2>/dev/null || echo "")
LAST_SHA=$(git log -1 --pretty=%h 2>/dev/null || echo "unknown")
LAST_MSG_FLAT=$(printf '%s' "$LAST_MSG" | tr '\n' ' ')
if echo "$LAST_MSG_FLAT" | grep -qE '#[[:space:]]*airuleset:test-skip-ok[[:space:]]+[^#]+'; then
    REASON=$(echo "$LAST_MSG_FLAT" | grep -oE '#[[:space:]]*airuleset:test-skip-ok[[:space:]]+[^#]+' | head -1 | sed 's/[[:space:]]*$//')
    echo "$(date -Iseconds)  project=$PROJECT  sha=$LAST_SHA  $REASON" >> "$AUDIT_LOG"
    exit 0
fi

# Test files touched by this push (same detection as pre-push-test-check.sh).
# Restricted to actual CODE extensions — the bare (test|spec|e2e|playwright)
# substring match also matched non-code paths like "docs/xspec.md" ("xspec"
# contains "spec"), false-blocking a push whose ONLY change is prose in a
# doc that merely mentions a banned pattern as an example.
CHANGED_FILES=$(git diff --name-only "origin/${DEFAULT_BRANCH}...HEAD" 2>/dev/null || git diff --name-only HEAD~1 2>/dev/null || echo "")
TEST_CHANGES=$(echo "$CHANGED_FILES" | grep -iE '(test|spec|e2e|playwright)' \
    | grep -iE '\.(rs|py|ts|tsx|js|jsx|mjs|cjs|go|rb|java|kt|kts|cs|cpp|cc|c|swift|scala)$' \
    || echo "")

if [ -z "$TEST_CHANGES" ]; then
    exit 0
fi

# Read into an ARRAY (one filename per line) instead of interpolating the
# variable unquoted — an unquoted `$TEST_CHANGES` word-splits a filename
# containing a space into bogus argv entries; `git diff -- <bogus-path>`
# then fails, the swallowed exception silently skips the file (fail-open),
# and a real violation added to a spaced filename sails through unblocked.
mapfile -t TEST_FILES_ARR <<< "$TEST_CHANGES"

# Scan each test file's ADDED lines for banned patterns. Delegated to Python
# for reliable multiline matching (the empty-body `def test_x():\n    pass`
# 2-line form needs it — bash/grep single-line matching can't span it).
# NOTE: under `set -e`, `VAR=$(failing_cmd)` exits the shell IMMEDIATELY
# (before RC=$? can even run) — the `|| RC=$?` keeps this in a tested
# context so set -e does not fire, and lets the block message print below.
RC=0
VIOLATIONS=$(python3 - "$DEFAULT_BRANCH" "${TEST_FILES_ARR[@]}" <<'PYEOF'
import re
import subprocess
import sys

default_branch = sys.argv[1]
test_files = sys.argv[2:]

PATTERNS = [
    (re.compile(r'#\[ignore\]'), "#[ignore] — disables a Rust test"),
    (re.compile(r'\btest\.skip\('), "test.skip(...) — skips a test"),
    (re.compile(r'\bit\.skip\('), "it.skip(...) — skips a test"),
    (re.compile(r'\bxit\('), "xit(...) — skips a test"),
    (re.compile(r'pytest\.mark\.skip'), "pytest.mark.skip — skips a test"),
    (re.compile(r'unittest\.skip'), "unittest.skip — skips a test"),
    (re.compile(r'assume!\('), "assume!(...) — silent skip in disguise"),
    (re.compile(r'assert!\(true\)'), "assert!(true) — tautology, verifies nothing"),
    (re.compile(r'expect\(true\)\.toBe\(true\)'),
     "expect(true).toBe(true) — tautology, verifies nothing"),
]
# Cheaply-detectable empty test bodies: Python's 2-line `def test_x():` +
# `pass`, a single-line Rust `fn test_x() {}`, or a JS/TS empty arrow body.
EMPTY_BODY = re.compile(
    r'^[ \t]*def\s+test_\w*\([^)]*\):[ \t]*\n[ \t]*pass[ \t]*$'
    r'|fn\s+test_\w*\([^)]*\)\s*\{\s*\}'
    r'|\b(?:it|test)\([^,]+,\s*(?:async\s*)?\(\)\s*=>\s*\{\s*\}\)',
    re.MULTILINE,
)

HUNK_HEADER_RE = re.compile(r'(?m)^@@.*@@.*$')

violations = []
for tf in test_files:
    tf = tf.strip()
    if not tf:
        continue
    try:
        out = subprocess.run(
            ["git", "diff", "-U0", f"origin/{default_branch}...HEAD", "--", tf],
            capture_output=True, text=True,
        ).stdout
    except Exception:
        continue
    # Process PER HUNK, never the whole file's added lines joined into one
    # blob. `git diff -U0` emits a SEPARATE hunk per contiguous change —
    # joining added lines ACROSS hunks concatenated a lone `def test_x():`
    # from one hunk with a completely UNRELATED lone `pass` added far away
    # in a DIFFERENT hunk into a phantom multi-line EMPTY_BODY match. The
    # single-line PATTERNS are unaffected by this split (they never spanned
    # hunk boundaries to begin with).
    for hunk in HUNK_HEADER_RE.split(out):
        added_lines = [ln[1:] for ln in hunk.splitlines()
                       if ln.startswith("+") and not ln.startswith("+++")]
        added_content = "\n".join(added_lines)
        if not added_content:
            continue
        for pat, label in PATTERNS:
            if pat.search(added_content):
                violations.append(f"  {tf}: {label}")
        if EMPTY_BODY.search(added_content):
            violations.append(f"  {tf}: empty test body — passes without exercising real code")

if violations:
    print("\n".join(dict.fromkeys(violations)))
    sys.exit(2)
sys.exit(0)
PYEOF
) || RC=$?

if [ "$RC" -eq 2 ]; then
    echo ""
    echo "🚫 BLOCKED: test-skip / tautology pattern added in this push."
    echo ""
    echo "  Per modules/ci/test-strictness.md, every test must run for real and verify"
    echo "  actual behavior — no #[ignore], no .skip(), no assume!(), no assert!(true),"
    echo "  no empty test bodies."
    echo ""
    echo "$VIOLATIONS"
    echo ""
    echo "  Fix: remove the skip/tautology, write a test that actually exercises the code."
    echo "  If a dependency is genuinely unavailable, the test must FAIL — see"
    echo "  test-strictness.md's dependency-unavailable protocol, not a skip."
    echo "  Bypass (rare, logged): add '# airuleset:test-skip-ok <reason>' to your"
    echo "  commit message."
    echo ""
    exit 2
elif [ "$RC" -ne 0 ]; then
    # A non-2 nonzero exit means the CHECK ITSELF malfunctioned (missing
    # python3, an internal bug) — never a real test-skip/tautology
    # violation. Fail CLOSED but say so HONESTLY instead of reusing the
    # empty-reason "BLOCKED: test-skip" message.
    echo ""
    echo "🚫 BLOCKED (fail-closed): block-test-skips.sh internal error"
    echo "  — python3 exited $RC instead of running the check."
    echo "$VIOLATIONS"
    echo ""
    echo "  This is a HOOK MALFUNCTION, not necessarily a real violation —"
    echo "  investigate and fix the hook (or install python3) before retrying."
    echo ""
    exit 2
fi

exit 0
