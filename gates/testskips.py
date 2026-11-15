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
from gates import audit, pushscope, pushtarget

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

# #1069 / issue 1255 -- the ONE sanctioned skip: a Playwright
# `test.skip` call with the args (notApplicable(), <reason>) at ANY indentation (the odoo-erp CI
# job `No Skip Validation` accepts every line carrying that literal; the spec
# marks a per-instance e2e test not applicable). Each COMPLETE call is cut out
# of an added line before PATTERNS run, so a real skip on the same physical line
# still blocks (git diff -U0 is per physical line). The reason must be one
# quoted/template string that cannot span its own closing quote, so a missing
# reason, `notApplicable() || x` and a nested `;)` are not the literal. A `//`
# comment line skips nothing and is not scanned.
SANCTIONED = re.compile(
    r"""\btest\.skip\(\s*notApplicable\(\)\s*,\s*"""
    r"""(?:'[^'\n]*'|"[^"\n]*"|`[^`\n]*`)\s*\)""")
_COMMENT_LINE_RE = re.compile(r"^\s*//")
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


def _dest_line_set(tf, dest_ref, cwd=None):
    """#1003 -- stripped, non-empty lines present in the DESTINATION version of
    ``tf`` (a line already there rode in via a merge, not this push). Empty set
    when unreadable -> nothing excluded (fail toward flagging)."""
    r = _git(["show", "%s:%s" % (dest_ref, tf)], cwd)
    if not r or r.returncode != 0:
        return set()
    return set(ln.strip() for ln in r.stdout.splitlines() if ln.strip())


def scan_test_file(tf, base_ref, dest_ref, cwd=None, head="HEAD"):
    """Return (violations, excluded_banned) for one test file's added lines."""
    violations = []
    excluded_banned = False
    dest = _dest_line_set(tf, dest_ref, cwd)
    r = _git(["diff", "-U0", "%s...%s" % (base_ref, head), "--", tf], cwd)
    if not r:
        return violations, excluded_banned
    out = r.stdout or ""
    for hunk in HUNK_HEADER_RE.split(out):
        added_lines = [ln[1:] for ln in hunk.splitlines()
                       if ln.startswith("+") and not ln.startswith("+++")]
        kept = []
        for raw in added_lines:
            if _COMMENT_LINE_RE.match(raw):
                continue
            ln = SANCTIONED.sub("", raw)  # #1069 / issue 1255
            if dest and raw.strip() in dest:
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
    "  §test-integrity): a `test.skip` whose arguments are exactly notApplicable()\n"
    "  and a reason, at any indentation — a per-instance Playwright e2e spec not\n"
    "  applicable here. That literal is allowed automatically; every other skip\n"
    "  shape (truthy/condition arg, compound arg, no reason, a second skip on the\n"
    "  same line, it-skip, pytest mark) stays blocked below.\n"
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


def _target_violations(project, cwd, head, cur):
    """The violations of ONE pushed ref (#1312); [] when its tip carries the
    bypass `# airuleset:test-skip-ok <reason>` (logged)."""
    base_ref, dest_ref, _cur, _default = pushscope.resolve(cwd, apply_branch_override=True, cur=cur)
    last = _git(["log", "-1", "--pretty=%B", head], cwd)
    lsha = _git(["log", "-1", "--pretty=%h", head], cwd)
    reason = _bypass_reason(((last.stdout or "") if last else "").replace("\n", " "))
    if reason:
        audit.append_line(_AUDIT_LOG, "%s  project=%s  sha=%s  %s" % (
            audit.iso_now(), project, ((lsha.stdout or "").strip() if lsha else "") or "unknown",
            reason))
        return []
    violations, excluded_banned = [], False
    for tf in pushscope.changed_files(base_ref, cwd, head):
        tf = tf.strip()
        if tf and _TEST_NAME_RE.search(tf) and _CODE_EXT_RE.search(tf):
            v, ex = scan_test_file(tf, base_ref, dest_ref, cwd, head)
            violations.extend(v)
            excluded_banned = excluded_banned or ex
    if excluded_banned:
        audit.append_line(_AUDIT_LOG,
                          "%s  project=%s  merge-in banned line(s) excluded from scan "
                          "(already on %s) (#1003)" % (audit.iso_now(), project, dest_ref))
    return violations


def main():
    payload = read_payload()
    cmd = command_of(payload)

    # Only real `git push` commands (quote-stripped, precise).
    if not pushscope.is_push_command(cmd, quote_strip=True):
        sys.exit(0)
    # #503 -- the durability BACKUP push triggers no CI; do not gate it.
    if pushscope.is_wip_backup_push(cmd):
        sys.exit(0)
    # #1312: EVERY repo + ref the command pushes, never the session cwd's HEAD.
    targets = pushtarget.resolved(cmd, _git, pushscope.is_inside_work_tree)
    if not targets:
        sys.exit(0)
    project = audit.project_of()

    # #909 -- same-call commit+push: the marker in the pending commit is
    # invisible to `git log -1`. Check the -m text of git commit segments.
    same_call = _same_call_commit_msgs(cmd)
    reason = _bypass_reason(same_call)
    if reason:
        audit.append_line(_AUDIT_LOG, "%s  project=%s  sha=pending  %s (same-call)" % (
            audit.iso_now(), project, reason))
        sys.exit(0)

    try:
        violations = [v for t in targets for v in _target_violations(project, *t)]
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
