"""cli_commit_kind — does a commit / merged PR IMPLEMENT a ticket? (#1228)

The footer `M` (merged, not yet released) reads the ticket numbers a change
carries (`cli_release_state._issue_refs`). A design doc, a mockup or a spec that
MENTIONS an epic implements none of it: on presenter (13.10.2026) two
`docs(…): … (#850, #851)` commits on `dev` put two unstarted epics into M while
the owner saw `I 1`. A change implements nothing when its conventional-commit
type is docs / chore / style / ci (a scope and `!` allowed), or when it carries
the `[no-test: docs…]` / `[no-test: documentation…]` marker. Such a ticket stays
in I — the safe over-count (the owner sees open work). Every other type (fix,
feat, test, refactor, …) keeps its refs.

stdlib only; `rejudge` is the one LOCAL git read (never the network), used to
re-judge a PR-cache entry written before the rule.
"""
import re
import subprocess

_NOTHING_TYPE_RE = re.compile(r"^\s*(?:docs|chore|style|ci)(?:\([^)]*\))?!?:",
                              re.IGNORECASE)
_NO_TEST_DOCS_RE = re.compile(r"\[no-test:\s*(?:docs|documentation)\b",
                              re.IGNORECASE)
_MERGE_SUBJECT_RE = re.compile(r"^Merge pull request #\d+\b")
_OID_RE = re.compile(r"^[0-9a-f]{7,64}$")


def implements_nothing(title, body=""):
    """True when a commit subject / PR title (+ the commit's own body) marks a
    change that implements no ticket (module docstring). A PR is judged by its
    TITLE only: a bundled PR's body can quote a docs commit's marker."""
    title = title or ""
    return bool(_NOTHING_TYPE_RE.match(title)
                or _NO_TEST_DOCS_RE.search(title)
                or _NO_TEST_DOCS_RE.search(body or ""))


def _commit_message(root, oid):
    """`(subject, body)` of commit `oid` in the repo at `root` (local git), or
    None when it cannot be read. Only a hex object id ever reaches git (a
    cached value could be anything, even an `--option`)."""
    if not root or not isinstance(oid, str) or not _OID_RE.match(oid):
        return None
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
    """True / False for a merged PR known only by its INTRODUCING commit (a
    PR-cache entry written before #1228 kept the refs, not the title), None
    when git cannot read the commit. The PR title is a GitHub merge commit's
    body first line, else the squash commit's subject; judged by the title
    alone, like a PR."""
    msg = _commit_message(root, oid)
    if msg is None:
        return None
    subject, body = msg
    if _MERGE_SUBJECT_RE.match(subject):
        subject = body.strip().partition("\n")[0]
    return implements_nothing(subject)


def rejudge(entry, root, oid, version):
    """Re-judge a PR-cache `entry` written before the rule (its `v` is not
    `version`) from commit `oid`: once git READ it, stamp `v` and `docs` (the
    refs stay: a docs change is still a pending change for the C veto).
    Unreadable leaves the entry as it is (implementing, retried next time).
    True when the entry changed."""
    if entry.get("v") == version:
        return False
    nothing = introducing_commit_implements_nothing(root, oid)
    if nothing is None:
        return False
    entry["v"], entry["docs"] = version, nothing
    return True


class MergedUnreleased(frozenset):
    """The M set — the tickets an IMPLEMENTING change merged but did not
    release — plus `.docs`: the tickets only a docs/chore/style/ci change
    names. Those never enter M, yet stay a pending change that keeps C open
    (`cli_ticket_state._c_veto`, through `TicketFacts.merged_docs`)."""

    def __new__(cls, issues=(), docs=()):
        obj = super().__new__(cls, issues)
        obj.docs = frozenset(docs)
        return obj
