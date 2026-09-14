"""gates.design.markers -- the durable per-issue evidence markers
(design/validated/reviewed/lane-return) + their I/O for the design gate.

Split out of the old top-level design_gate.py (#1020) with ZERO behaviour
change. A marker is written only from a code path that has just observed a REAL
posted comment (see hooks/post-record-design-comment.sh); best-effort I/O never
raises (fail toward re-asking, never toward silently trusting).
"""
import os
import re
import time

_DESIGN_DIRNAME = "design-posted"

# #213/#214 -- the SAME artifact pattern extended to two more evidence
# kinds: "validated" (Step 0's live-reproduction proof) and "reviewed"
# (the /review + /requesting-code-review pass). One marker directory per
# kind, same sanitized-key convention as design. See `_dir_for_kind`.
_VALIDATED_DIRNAME = "validated-posted"
_REVIEWED_DIRNAME = "reviewed-posted"
# #844 -- the LANE-RETURN kind: a worktree worker's durable return artifact
# (branch + head sha + worktree + evidence) posted as its LAST act, so a lost
# lane-completion notification loses nothing. DELIBERATELY NOT in `ALL_KINDS` --
# the design/validated/reviewed gates (block-commit-without-design.sh,
# subagent-stop-check-design.sh) key on ALL_KINDS for the MERGE flow and must not
# demand a LANE-RETURN of a merging worker; the LANE-RETURN gate is its OWN
# SubagentStop hook (subagent-stop-check-lane-return.sh), keyed on the worktree-
# mode return shape.
_LANE_RETURN_DIRNAME = "lane-return-posted"
_KIND_DIRNAMES = {
    "design": _DESIGN_DIRNAME,
    "validated": _VALIDATED_DIRNAME,
    "reviewed": _REVIEWED_DIRNAME,
    "lane-return": _LANE_RETURN_DIRNAME,
}
ALL_KINDS = ("design", "validated", "reviewed")


def _dir_for_kind(kind):
    return _KIND_DIRNAMES.get(kind, _DESIGN_DIRNAME)


def _claude_dir():
    return os.path.join(os.path.expanduser("~"), ".claude")


def design_dir(kind="design"):
    return os.path.join(_claude_dir(), _dir_for_kind(kind))


def marker_key(repo_key, issue):
    return "%s#%s" % (repo_key, issue)


def marker_path(repo_key, issue, kind="design"):
    safe = re.sub(r"[^A-Za-z0-9._#-]", "_", marker_key(repo_key, issue))
    return os.path.join(design_dir(kind), safe)


def marker_exists(repo_key, issue, kind="design"):
    if not repo_key or issue in (None, ""):
        return False
    return os.path.isfile(marker_path(repo_key, issue, kind))


def write_marker(repo_key, issue, comment_url, reason="ok", ts=None, kind="design"):
    """Record DELIVERED evidence for `<repo_key>#<issue>` (kind =
    "design" / "validated" / "reviewed"). Never call this speculatively --
    only from a code path that has just observed a REAL posted comment (see
    `hooks/post-record-design-comment.sh`). Best-effort: an unwritable
    ~/.claude never raises, it just means the gate stays active (fail
    toward re-asking, never toward silently trusting)."""
    if not repo_key or issue in (None, ""):
        return False
    d = design_dir(kind)
    try:
        os.makedirs(d, exist_ok=True)
        with open(marker_path(repo_key, issue, kind), "w", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n" % (ts if ts is not None else time.time(),
                                       comment_url or "-", reason or "ok"))
        return True
    except OSError:
        return False


def read_marker(repo_key, issue, kind="design"):
    """`{"ts": float, "url": str, "reason": str}` or None. Tolerates the
    legacy/short shape (fewer than 3 fields) rather than raising."""
    try:
        with open(marker_path(repo_key, issue, kind), encoding="utf-8") as fh:
            body = fh.read(2000).strip()
    except OSError:
        return None
    parts = body.split("\t")
    if not parts or not parts[0]:
        return None
    try:
        ts = float(parts[0])
    except ValueError:
        ts = None
    return {
        "ts": ts,
        "url": parts[1] if len(parts) > 1 else "",
        "reason": parts[2] if len(parts) > 2 else "",
    }


def claimed_urls(repo_key, issue):
    """The set of comment URLs already used by an EXISTING marker of any
    kind for `<repo_key>#<issue>`. Adversarial-review finding (post-#214):
    one comment posted before any code exists could plausibly satisfy
    design + validated + reviewed all at once, which defeats the premise
    that each kind proves its own step happened. A caller uses this to
    refuse granting a SECOND kind from a comment url that already granted
    one -- distinct evidence kinds must come from distinct comments."""
    urls = set()
    for kind in ALL_KINDS:
        m = read_marker(repo_key, issue, kind)
        if m and m.get("url"):
            urls.add(m["url"])
    return urls
