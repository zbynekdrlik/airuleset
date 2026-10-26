"""cli_commit_kind — does a commit / merged PR IMPLEMENT a ticket? (#1228)

The footer `M` (merged, not yet released) reads the ticket numbers a change
carries (`cli_release_state._issue_refs`). A design doc, a mockup or a spec that
MENTIONS an epic implements none of it: on presenter (13.10.2026) two
`docs(…): … (#850, #851)` commits on `dev` put two unstarted epics into M while
the owner saw `I 1`. A change implements nothing when its conventional-commit
type is docs / chore / style / ci (a scope and `!` allowed), or when it carries
the `[no-test: docs…]` marker. Such a ticket stays in I — the safe over-count
(the owner sees open work). Every other type (fix, feat, test, refactor, …)
keeps its refs.

stdlib only; `introducing_commit_implements_nothing` is the one LOCAL git read
(never the network), used to re-judge a PR-cache entry written before the rule.
"""
import re
import subprocess

_NOTHING_TYPE_RE = re.compile(r"^\s*(?:docs|chore|style|ci)(?:\(.*\))?!?:",
                              re.IGNORECASE)
_NO_TEST_DOCS_RE = re.compile(r"\[no-test:\s*docs", re.IGNORECASE)
_MERGE_SUBJECT_RE = re.compile(r"^Merge pull request #\d+\b")
_TRAILING_PR_RE = re.compile(r"\s*\(#\d+\)\s*$")


def implements_nothing(title, body=""):
    """True when a commit subject / PR title (+ body) marks a change that
    implements no ticket (module docstring)."""
    title = title or ""
    return bool(_NOTHING_TYPE_RE.match(title)
                or _NO_TEST_DOCS_RE.search(title)
                or _NO_TEST_DOCS_RE.search(body or ""))


def _commit_message(root, oid):
    """`(subject, body)` of commit `oid` in the repo at `root` (local git), or
    None when git cannot read it (missing commit / repo, timeout)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%s%x1f%b", oid],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    subject, _, body = (r.stdout or "").partition("\x1f")
    return subject.strip(), body


def introducing_commit_implements_nothing(root, oid):
    """`implements_nothing` for a merged PR known only by its INTRODUCING commit
    (a PR-cache entry written before #1228 kept the refs, not the title): a
    GitHub merge commit carries the PR title as its body's first line, a squash
    commit as its subject minus the trailing `(#N)`. False when git cannot read
    the commit, so the entry keeps its refs (the pre-#1228 behaviour)."""
    msg = _commit_message(root, oid) if root and oid else None
    if msg is None:
        return False
    subject, body = msg
    if _MERGE_SUBJECT_RE.match(subject):
        title, _, rest = body.strip().partition("\n")
        return implements_nothing(title, rest)
    return implements_nothing(_TRAILING_PR_RE.sub("", subject), body)
