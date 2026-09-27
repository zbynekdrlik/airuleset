"""gates.pushbypass -- the per-commit ``[no-test: <reason>]`` bypass for the
pre-push test gates (#1162 ruling b).

gates.pushtest measures the whole PR range (``origin/<default>..HEAD`` on a
two-branch ``dev``, #1162). Its latest-commit bypass reads only HEAD's message,
so a sanctioned bypassed fix commit pushed EARLIER in that range would re-block
every later push until the release merge. The ruling: a commit whose OWN
message carries ``[no-test: <reason>]`` is exempt from both checks (test
presence for its files, fix-before-test ordering) wherever it sits in the
range. Each exempted commit is logged ONCE to audits/no-test-skips.log (deduped
by sha, so a commit re-seen on every later push is not re-counted as a new
bypass by ``audit.count_cli_bypasses``).
"""
import re

from gates import audit

AUDIT_LOG = "no-test-skips.log"
NOTEST_REASON_RE = re.compile(r'\[no-test:\s*[^\]]+\]')
_LOGGED_SHA_RE = re.compile(r'\bsha=([0-9a-f]{7,40})\b')


def _touched(sha, stdout_fn):
    out = stdout_fn(["diff-tree", "--no-commit-id", "--name-only", "-r", sha])
    return {ln for ln in out.splitlines() if ln}


def _already_logged(sha):
    """True iff the audit log already names ``sha`` (full or abbreviated, the
    latest-commit bypass logs ``%h``). An unreadable log reads as not logged."""
    try:
        with open(audit.audit_log_path(AUDIT_LOG), encoding="utf-8",
                  errors="replace") as fh:
            text = fh.read()
    except OSError:
        return False
    return any(sha.startswith(tok) for tok in _LOGGED_SHA_RE.findall(text))


def scan(base_ref, stdout_fn, project):
    """Scan ``base_ref..HEAD`` for commits whose own message carries
    ``[no-test: <reason>]``. Returns ``(bypassed, exempt_files)``: ``bypassed``
    is the set of their full shas; ``exempt_files`` the files touched ONLY by
    bypassed commits (a file a normal commit also touched stays gated).
    ``stdout_fn(args)`` runs git and returns stdout ("" on failure)."""
    raw = stdout_fn(["log", "--pretty=%H%x1f%B%x1e", "%s..HEAD" % base_ref])
    bypassed, normal = {}, []
    for rec in raw.split("\x1e"):
        sha, sep, body = rec.lstrip("\n").partition("\x1f")
        if not sep:
            continue
        m = NOTEST_REASON_RE.search(body.replace("\n", " "))
        if m:
            bypassed[sha] = m.group(0)
        else:
            normal.append(sha)
    exempt = set().union(*[_touched(s, stdout_fn) for s in bypassed])
    exempt -= set().union(*[_touched(s, stdout_fn) for s in normal])
    for sha, marker in bypassed.items():
        if not _already_logged(sha):
            audit.append_line(AUDIT_LOG, "%s  project=%s  sha=%s  %s (in-range commit, #1162)"
                              % (audit.iso_now(), project, sha[:12], marker))
    return set(bypassed), exempt
