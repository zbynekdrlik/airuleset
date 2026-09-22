"""gates.testskips -- the test-skip / tautology gate (entry for block-test-skips.sh).

Blocks a ``git push`` whose outgoing diff ADDS a banned test-skip / tautology
pattern (modules/ci/test-strictness.md) in a TEST file. The BASE/DEST ref
resolution (#847/#909/#1003) is delegated to gates.pushscope; the scan
(PATTERNS, empty-body, per-hunk processing, #1003 merge-in exclusion) and the
bypass are verbatim from the bash original. Bypass:
``# airuleset:test-skip-ok <reason>`` in the latest commit message (or the -m
text of a same-call commit), logged to test-skip-bypasses.log via gates.audit.
"""
import re
import subprocess
import sys

from gates import command_of, emit_block_stderr, read_payload
from gates import audit, pushscope

_AUDIT_LOG = "test-skip-bypasses.log"

# Banned test-skip / tautology patterns (verbatim).
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

# #1069 -- the ONE sanctioned skip shape the fleet gate allows: a TOP-LEVEL
# (column 0) Playwright `test.skip` whose first argument is EXACTLY the bare
# call notApplicable(), followed by a comma (a reason). It marks a per-instance
# e2e spec not applicable when its addon is absent from the -i install list
# (odoo-erp .claude/rules/gk-review-lenses.md §test-integrity; the odoo-erp CI
# job `No Skip Validation` guards its placement). Anchored at column 0 (no
# leading `\s*`): the SAME shape INDENTED inside a `test(`/`describe(` body —
# where a real skip could hide behind the literal — is NOT sanctioned and stays
# blocked by PATTERNS. A line matching a SANCTIONED entry is dropped by
# scan_test_file before the PATTERNS pass; everything else still runs.
SANCTIONED = [
    re.compile(r'^test\.skip\(\s*notApplicable\(\)\s*,'),
]
EMPTY_BODY = re.compile(
    r'^[ \t]*def\s+test_\w*\([^)]*\):[ \t]*\n[ \t]*pass[ \t]*$'
    r'|fn\s+test_\w*\([^)]*\)\s*\{\s*\}'
    r'|\b(?:it|test)\([^,]+,\s*(?:async\s*)?\(\)\s*=>\s*\{\s*\}\)',
    re.MULTILINE,
)
HUNK_HEADER_RE = re.compile(r'(?m)^@@.*@@.*$')

_TEST_NAME_RE = re.compile(r'(?i)(test|spec|e2e|playwright)')
_CODE_EXT_RE = re.compile(
    r'(?i)\.(rs|py|ts|tsx|js|jsx|mjs|cjs|go|rb|java|kt|kts|cs|cpp|cc|c|swift|scala)$')
_BYPASS_RE = re.compile(r'#\s*airuleset:test-skip-ok\s+[^#]+')


def _git(args, cwd=None):
    try:
        return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def _dest_line_set(tf, dest_ref):
    """#1003 -- stripped, non-empty lines present in the DESTINATION version of
    ``tf`` (a line already there rode in via a merge, not this push). Empty set
    when unreadable -> nothing excluded (fail toward flagging)."""
    r = _git(["show", "%s:%s" % (dest_ref, tf)])
    if not r or r.returncode != 0:
        return set()
    return set(ln.strip() for ln in r.stdout.splitlines() if ln.strip())


def scan_test_file(tf, base_ref, dest_ref):
    """Return (violations, excluded_banned) for one test file's added lines."""
    violations = []
    excluded_banned = False
    dest = _dest_line_set(tf, dest_ref)
    r = _git(["diff", "-U0", "%s...HEAD" % base_ref, "--", tf])
    if not r:
        return violations, excluded_banned
    out = r.stdout or ""
    for hunk in HUNK_HEADER_RE.split(out):
        added_lines = [ln[1:] for ln in hunk.splitlines()
                       if ln.startswith("+") and not ln.startswith("+++")]
        kept = []
        for ln in added_lines:
            if any(s.search(ln) for s in SANCTIONED):
                continue  # #1069 -- the one allowed literal; never scanned
            if dest and ln.strip() in dest:
                for pat, _lbl in PATTERNS:
                    if pat.search(ln):
                        excluded_banned = True
                        break
                continue
            kept.append(ln)
        added_content = "\n".join(kept)
        if not added_content:
            continue
        for pat, label in PATTERNS:
            if pat.search(added_content):
                violations.append("  %s: %s" % (tf, label))
        if EMPTY_BODY.search(added_content):
            violations.append("  %s: empty test body — passes without exercising real code" % tf)
    return violations, excluded_banned


def _bypass_reason(text):
    m = _BYPASS_RE.search(text)
    if not m:
        return ""
    return m.group(0).rstrip()


_COMMIT_MSG_RE = re.compile(r"""git\s+commit\b[^&|;]*?-m\s+["'](.*?)["']""", re.DOTALL)
_COMMIT_MSG_EMPTY_RE = re.compile(
    r"""git\s+commit\b[^&|;]*?--allow-empty[^&|;]*?-m\s+["'](.*?)["']""", re.DOTALL)


def _same_call_commit_msgs(text):
    msgs = _COMMIT_MSG_RE.findall(text)
    msgs += _COMMIT_MSG_EMPTY_RE.findall(text)
    return " ".join(msgs)


_BLOCK_MSG = (
    "\n"
    "🚫 BLOCKED: test-skip / tautology pattern added in this push.\n"
    "\n"
    "  Per modules/ci/test-strictness.md, every test must run for real and verify\n"
    "  actual behavior — no #[ignore], no .skip(), no assume!(), no assert!(true),\n"
    "  no empty test bodies.\n"
    "\n"
    "  The ONE sanctioned exception (odoo-erp .claude/rules/gk-review-lenses.md\n"
    "  §test-integrity): a TOP-LEVEL (column 0) `test.skip` whose first argument is\n"
    "  exactly the bare call notApplicable() followed by a reason — marking a\n"
    "  per-instance Playwright e2e spec not applicable. That literal is allowed\n"
    "  automatically; every other skip shape (indented, truthy/condition arg,\n"
    "  compound arg, no reason, it-skip, pytest mark) stays blocked below.\n"
    "\n"
    "%s\n"
    "\n"
    "  Fix: remove the skip/tautology, write a test that actually exercises the code.\n"
    "  If a dependency is genuinely unavailable, the test must FAIL — see\n"
    "  test-strictness.md's dependency-unavailable protocol, not a skip.\n"
    "  Bypass (rare, logged): add '# airuleset:test-skip-ok <reason>' to your\n"
    "  commit message.\n"
)

_FAILCLOSED_MSG = (
    "\n"
    "🚫 BLOCKED (fail-closed): block-test-skips internal error\n"
    "  — the gate raised %r instead of running the check.\n"
    "\n"
    "  This is a HOOK MALFUNCTION, not necessarily a real violation —\n"
    "  investigate and fix the hook before retrying.\n"
)


def main():
    payload = read_payload()
    cmd = command_of(payload)

    # Only real `git push` commands (quote-stripped, precise).
    if not pushscope.is_push_command(cmd, quote_strip=True):
        sys.exit(0)
    # #503 -- the durability BACKUP push triggers no CI; do not gate it.
    if pushscope.is_wip_backup_push(cmd):
        sys.exit(0)
    if not pushscope.is_inside_work_tree():
        sys.exit(0)

    project = audit.project_of()
    base_ref, dest_ref, _cur, _default = pushscope.resolve(apply_branch_override=True)

    # Bypass: `# airuleset:test-skip-ok <reason>` in the latest commit message.
    last = _git(["log", "-1", "--pretty=%B"])
    last_msg = (last.stdout or "") if last else ""
    lsha = _git(["log", "-1", "--pretty=%h"])
    last_sha = (lsha.stdout or "").strip() if lsha else "unknown"
    last_msg_flat = last_msg.replace("\n", " ")
    reason = _bypass_reason(last_msg_flat)
    if reason:
        audit.append_line(_AUDIT_LOG, "%s  project=%s  sha=%s  %s" % (
            audit.iso_now(), project, last_sha or "unknown", reason))
        sys.exit(0)

    # #909 -- same-call commit+push: the marker in the pending commit is
    # invisible to `git log -1`. Check the -m text of git commit segments.
    same_call = _same_call_commit_msgs(cmd)
    reason = _bypass_reason(same_call)
    if reason:
        audit.append_line(_AUDIT_LOG, "%s  project=%s  sha=pending  %s (same-call)" % (
            audit.iso_now(), project, reason))
        sys.exit(0)

    try:
        changed = pushscope.changed_files(base_ref)
        test_files = [f for f in changed
                      if _TEST_NAME_RE.search(f) and _CODE_EXT_RE.search(f)]
        if not test_files:
            sys.exit(0)

        violations = []
        excluded_banned = False
        for tf in test_files:
            tf = tf.strip()
            if not tf:
                continue
            v, ex = scan_test_file(tf, base_ref, dest_ref)
            violations.extend(v)
            excluded_banned = excluded_banned or ex

        if excluded_banned:
            audit.append_line(_AUDIT_LOG,
                              "%s  project=%s  merge-in banned line(s) excluded from scan "
                              "(already on %s) (#1003)" % (audit.iso_now(), project, dest_ref))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- fail CLOSED, but HONESTLY
        emit_block_stderr(_FAILCLOSED_MSG % (exc,))
        return

    if violations:
        deduped = list(dict.fromkeys(violations))
        emit_block_stderr(_BLOCK_MSG % ("\n".join(deduped)))

    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
