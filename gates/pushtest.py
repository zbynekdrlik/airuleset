"""gates.pushtest -- the test-presence / RED-before-GREEN gate (entry for
pre-push-test-check.sh).

Three gates, verbatim from the bash original:
  * Gate 1 -- feature code changed but no test files (path-named OR inline).
  * Gate 2 -- a bug-fix commit appears BEFORE any test commit in the PR.
  * Gate 3 -- shallow-assertion WARNING (never blocks).
The BASE_REF resolution is delegated to gates.pushscope (WITHOUT the
origin/<branch> override -- #909 F1: these gates are PR-scoped). Bypass:
``[no-test: <reason>]`` in the latest commit message, logged to
no-test-skips.log via gates.audit.
"""
import os
import re
import subprocess
import sys

from gates import command_of, emit_block_stderr, read_payload
from gates import audit, pushscope

_AUDIT_LOG = "no-test-skips.log"

_DOCS_EXT_RE = re.compile(
    r'(?i)\.(md|markdown|mdx|rst|txt|adoc|png|jpe?g|gif|svg|webp|ico)$')
_DOCS_BASENAME_RE = re.compile(
    r'(?i)^(LICENSE|LICENCE|COPYING|NOTICE|AUTHORS|CONTRIBUTORS?|CHANGELOG|CHANGES|README)'
    r'([.][A-Za-z0-9]+)?$')
_FEATURE_EXT_RE = re.compile(r'\.(rs|ts|tsx|js|jsx|py)$')
_FEATURE_EXCLUDE_RE = re.compile(r'(test|spec|e2e|playwright|_test\.|\.test\.)')
_TEST_NAME_RE = re.compile(r'(?i)(test|spec|e2e|playwright)')
_INLINE_TEST_RE = re.compile(
    r"""#\[(test|cfg\(test\)|tokio::test|rstest)\]|assert(_eq|_ne)?!|\bfn test_|"""
    r"""def test_|\bit\(['"]|describe\(""")
_TEST_SUBJECT_RE = re.compile(r'(?i)^test[:(]|\[red\]')
_BUGFIX_SUBJECT_RE = re.compile(r'(?i)^(fix\(|fix:|bug:|bugfix:|regression:|hotfix:|patch:|repair:)')
_BUGFIX_BODY_RE = re.compile(r'(?i)(closes|fixes|resolves)\s+#[0-9]+')
_BARE_NOTEST_RE = re.compile(r'\[no-test\](\s|$)', re.MULTILINE)
_NOTEST_REASON_RE = re.compile(r'\[no-test:\s*[^\]]+\]')


def _git(args, cwd=None):
    try:
        # #1139: errors="replace" — a non-UTF-8 byte anywhere in a diff must
        # never crash the gate (it only scans ASCII patterns, so a replaced
        # byte can never hide a violation).
        return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                              text=True, errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def _stdout(args):
    r = _git(args)
    return (r.stdout or "") if (r and r.returncode == 0) else ""


def _is_docs_only(files):
    """True iff there is >=1 file and EVERY file is a docs/prose/image path."""
    any_f = False
    for f in files:
        f = f.strip()
        if not f:
            continue
        any_f = True
        if _DOCS_EXT_RE.search(f):
            continue
        if _DOCS_BASENAME_RE.search(os.path.basename(f)):
            continue
        return False
    return any_f


_GATE1_MSG = (
    "\n"
    "🚫 BLOCKED: Feature code changed but NO test files modified.\n"
    "\n"
    "  Changed feature files:\n"
    "%s\n"
    "\n"
    "  To fix: write a Playwright E2E test or unit test for your changes.\n"
    "  Bypass: add [no-test: <reason>] to your commit message\n"
    "          (the reason is logged to audits/no-test-skips.log).\n"
)
_GATE2_MSG = (
    "\n"
    "🚫 BLOCKED: Bug-fix commit appears BEFORE any test commit in this PR.\n"
    "\n"
    "  Per regression-test-first.md, every bug fix needs:\n"
    "    1. RED commit (test first) — adds failing test that asserts correct behavior\n"
    "    2. GREEN commit (fix second) — fix that makes the test pass\n"
    "\n"
    "  Bug-fix commits without a preceding test commit:\n"
    "%s\n"
    "\n"
    "  Fix: amend the branch so a test commit precedes each fix commit.\n"
    "       (Reorder commits, or add a new test commit before the fix.)\n"
    "  Bypass: [no-test: <reason>] — but NEVER use this for real bug fixes.\n"
)
_BARE_NOTEST_MSG = (
    "\n"
    "🚫 BLOCKED: Bare [no-test] is no longer accepted.\n"
    "\n"
    "  Use [no-test: <reason>] explaining WHY a test is not feasible.\n"
    "  Valid reasons: 'config-only change, no logic', 'release tag',\n"
    "                  'auto-generated file', 'docs only',\n"
    "                  'ci-yaml conditional logic, not unit-testable\n"
    "                  outside a real run' (#41 — a GitHub Actions\n"
    "                  if:/needs.job.result expression fix, when the\n"
    "                  workflow's own self-check IS the regression\n"
    "                  guard already running on every future push).\n"
    "  NEVER use this for a bug fix with testable logic — see regression-test-first.md.\n"
)


def _warn(msg):
    # stderr-only: the adapter runs this module with 1>&2 (the old hook's
    # `exec 1>&2`), so a stdout write would double the warning on stderr.
    sys.stderr.write(msg + "\n")


def main():
    payload = read_payload()
    cmd = command_of(payload)

    if not pushscope.is_push_command(cmd, quote_strip=False):
        sys.exit(0)
    if pushscope.is_wip_backup_push(cmd):
        sys.exit(0)
    if not pushscope.is_inside_work_tree():
        sys.exit(0)

    project = audit.project_of()
    base_ref, _dest, cur, _default = pushscope.resolve(apply_branch_override=False)

    last = _git(["log", "-1", "--pretty=%B"])
    last_msg = (last.stdout or "") if last else ""
    lsha = _git(["log", "-1", "--pretty=%h"])
    last_sha = (lsha.stdout or "").strip() if lsha else "unknown"

    # Bare [no-test] (no reason) -> BLOCK.
    if _BARE_NOTEST_RE.search(last_msg):
        emit_block_stderr(_BARE_NOTEST_MSG)

    # [no-test: <reason>] -> honored + logged.
    last_msg_flat = last_msg.replace("\n", " ")
    m = _NOTEST_REASON_RE.search(last_msg_flat)
    if m:
        audit.append_line(_AUDIT_LOG, "%s  project=%s  sha=%s  %s" % (
            audit.iso_now(), project, last_sha or "unknown", m.group(0)))
        sys.exit(0)

    changed = pushscope.changed_files(base_ref)
    if not changed:
        sys.exit(0)

    feature_changes = [f for f in changed
                       if _FEATURE_EXT_RE.search(f) and not _FEATURE_EXCLUDE_RE.search(f)]
    test_changes = [f for f in changed if _TEST_NAME_RE.search(f)]

    # Inline tests (or a test-signal commit subject) in the branch?
    inline_test_added = False
    diff_added = _stdout(["diff", "-U0", "%s...HEAD" % base_ref])
    for ln in diff_added.splitlines():
        if ln.startswith("+") and _INLINE_TEST_RE.search(ln):
            inline_test_added = True
            break
    if not inline_test_added:
        subjects = _stdout(["log", "--pretty=%s", "%s..HEAD" % base_ref])
        for subj in subjects.splitlines():
            if _TEST_SUBJECT_RE.search(subj):
                inline_test_added = True
                break

    # Gate 1: feature code but no test (path-named OR inline).
    if feature_changes and not test_changes and not inline_test_added:
        head = "\n".join("    " + f for f in feature_changes[:10])
        emit_block_stderr(_GATE1_MSG % head)

    # Gate 2: RED-before-GREEN order.
    commits = _stdout(["log", "--reverse", "--pretty=%H", "%s..HEAD" % base_ref]).split()
    bug_fix_before_test = []
    if commits:
        seen_test_commit = False
        for sha in commits:
            subject = _stdout(["log", "-1", "--pretty=%s", sha])
            body = _stdout(["log", "-1", "--pretty=%b", sha])
            files = _stdout(["diff-tree", "--no-commit-id", "--name-only", "-r", sha]).splitlines()

            if any(_TEST_NAME_RE.search(f) for f in files):
                seen_test_commit = True
            if not seen_test_commit and _TEST_SUBJECT_RE.search(subject):
                seen_test_commit = True
            if not seen_test_commit:
                added = _stdout(["diff-tree", "--no-commit-id", "-p", "-r", sha])
                for ln in added.splitlines():
                    if ln.startswith("+") and _INLINE_TEST_RE.search(ln):
                        seen_test_commit = True
                        break

            is_bugfix = bool(_BUGFIX_SUBJECT_RE.search(subject) or _BUGFIX_BODY_RE.search(body))
            if is_bugfix and _is_docs_only(files):
                is_bugfix = False
                short = _stdout(["log", "-1", "--pretty=%h", sha]).strip() or sha
                audit.append_line(_AUDIT_LOG,
                                  "%s  project=%s  sha=%s  docs-only fix commit — exempt "
                                  "from RED-order gate (#1003)" % (audit.iso_now(), project, short))

            if is_bugfix and not seen_test_commit:
                one = _stdout(["log", "-1", "--pretty=%h %s", sha]).rstrip("\n")
                bug_fix_before_test.append("    " + one)

    if bug_fix_before_test:
        emit_block_stderr(_GATE2_MSG % ("\n".join(bug_fix_before_test)))

    # Gate 3: shallow-assertion WARNING (never blocks).
    if test_changes:
        warnings = []
        for tf in test_changes:
            if not os.path.isfile(tf):
                continue
            try:
                with open(tf, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
            except OSError:
                continue
            lines = content.splitlines()
            n = 0
            n += sum(1 for ln in lines if re.search(r'expect\(.+\)\.(to|not)', ln))
            n += sum(1 for ln in lines if re.search(r'assert(_eq|_ne)?!', ln))
            n += sum(1 for ln in lines if re.search(r'^\s*assert\s|self\.assert', ln))
            if n < 2:
                warnings.append("  ⚠️ %s: only %d assertions (minimum: 2)" % (tf, n))
            if n <= 2:
                if (re.search(r'toBeVisible\(\)\s*;?\s*$', content, re.M)
                        and not re.search(r'toHaveText|toContainText|toHaveValue|boundingBox', content)):
                    warnings.append("  ⚠️ %s: only checks visibility, not content or behavior" % tf)
                if (re.search(r'response\.status.*200|statusCode.*200', content)
                        and not re.search(r'toContain|toMatch|toHaveProperty|response\.body|\.json\(\)', content)):
                    warnings.append("  ⚠️ %s: only checks HTTP 200, not response content" % tf)
        if warnings:
            _warn("\n⚠️ SHALLOW TEST WARNING: Test files have weak assertions.\n"
                  + "\n".join(warnings)
                  + "\n\n  Tests must verify actual behavior (text, values, state changes),\n"
                  + "  not just that pages load or APIs return 200.\n")

    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
