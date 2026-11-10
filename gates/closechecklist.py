"""gates.closechecklist -- refuse closing an issue whose DESCRIPTION still has an
unchecked `- [ ]` line (#1297, owner 10.11.2026).

The issue description is the single task list of its deliverable: a step of the
same deliverable is a `- [ ] <step>` line, a separate deliverable is its own
issue linked back as `- [ ] #<child>` (or `- [ ] owner/repo#N`). So a close with
any unchecked line left would report open work as done. Every unchecked line
counts, the child links included: tick `- [x] #child` once the child is closed.

Shape: a pure predicate (`unchecked_items`) + the close-target parser
(`close_targets`, which reuses the ONE close segmenter
`hooks/close_guard_segment.py` that `block-fork-no-merge-issue-close.sh` runs)
+ `main()` for the thin hook adapter `hooks/block-close-with-open-checklist.sh`.

Exempt: a `--reason "not planned"` close (and `state_reason=not_planned` on the
`gh api ... PATCH` form): nothing is being reported done. FAIL-OPEN on every
read failure (no slug, gh error, unparseable JSON): the close passes and the
reason is logged to ~/.claude/close-checklist-gate.log -- this gate guards a
discipline, it must never wedge a session on a GitHub outage. Bypass:
`# airuleset:checklist-close-ok <reason>` (logged).
"""
import os
import re
import sys

from gates import audit, command_of, field_of, read_payload

_HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")

# `- [ ] text`, `* [ ] text`, `+ [ ] text`, `1. [ ] text`, `1) [ ] text`.
_UNCHECKED_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[ \]\s+(\S.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

# `gh api repos/<o>/<r>/issues/<n>` (an optional leading `/`).
_API_ISSUE_RE = re.compile(r"(?:^|\s)/?repos/([\w.-]+/[\w.-]+)/issues/(\d+)\b")
_API_NOT_PLANNED_RE = re.compile(r"state_reason=[\"']?not_planned")

BYPASS = "airuleset:checklist-close-ok"
LOG_NAME = "close-checklist-gate.log"


def unchecked_items(body):
    """Texts of every unchecked task line in `body`, in order, skipping fenced
    code blocks (an example inside ``` is not a task)."""
    items = []
    in_fence = False
    for line in (body or "").splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _UNCHECKED_RE.match(line)
        if m:
            items.append(m.group(1))
    return items


def _segment(cmd):
    """`(signals, err)` from the close segmenter; signals None when it cannot run."""
    if _HOOKS_DIR not in sys.path:
        sys.path.insert(0, _HOOKS_DIR)
    try:
        import close_guard_segment
        return close_guard_segment.analyze(cmd), None
    except Exception as exc:  # noqa: BLE001 -- reported to the caller, which logs it
        return None, "close segmenter failed: %r" % (exc,)


def close_targets(cmd):
    """`(targets, err)`: `targets` is a list of `(repo_or_None, number)` closes
    this command performs that are NOT not-planned; `err` names why the command
    could not be parsed (then targets is empty and the caller fails open)."""
    if "close" not in cmd and "PATCH" not in cmd.upper():
        return [], None
    sig, err = _segment(cmd)
    if sig is None:
        return [], err or "close segmenter returned nothing"
    targets = []
    repo = sig.get("D_REPO_ARG") or None
    not_planned = set((sig.get("D_NOTPLANNED") or "").split())
    for num in (sig.get("D_NUMS") or "").split():
        if num not in not_planned:
            targets.append((repo, num))
    if sig.get("HAS_PATCH_CLOSE") == "1" and not _API_NOT_PLANNED_RE.search(cmd):
        for m in _API_ISSUE_RE.finditer(cmd):
            targets.append((m.group(1), m.group(2)))
    return targets, None


def _log(line):
    path = os.path.join(os.path.expanduser("~"), ".claude", LOG_NAME)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (audit.iso_now(), line))
    except OSError as exc:
        sys.stderr.write("close-checklist gate: cannot write %s: %s\n" % (path, exc))


def _read_body(repo, num, cwd, runner=None):
    """`(body, err)` -- the issue description via REST (gates.ghread)."""
    from gates import ghread
    slug = repo or ghread.canonical_slug(cwd, runner=runner)
    if not slug:
        return None, "no repo slug for cwd %s" % cwd
    obj, err = ghread.read_issue(num, slug, cwd=cwd, runner=runner)
    if err or not isinstance(obj, dict):
        return None, err or "unreadable issue"
    return obj.get("body") or "", None


def block_message(blocked):
    """The model-visible refusal for `[(ref, [item, ...]), ...]`."""
    lines = ["BLOCKED: closing an issue whose description still has unchecked task lines (#1297)."]
    for ref, items in blocked:
        lines.append("  %s:" % ref)
        for it in items[:15]:
            lines.append("    - [ ] %s" % it)
        if len(items) > 15:
            lines.append("    ... and %d more" % (len(items) - 15))
    lines += [
        "",
        "The description is the task list. For EVERY unchecked line, either:",
        "  - finish it and tick it with its evidence: `- [x] <item> — <commit / test / live read>`, or",
        "  - move it to its own ticket (`Part of #<this>` in the new body) and list it here as",
        "    `- [ ] #<new>` -- then this ticket closes only once that child is closed and ticked.",
        "A comment that added work must also have added its `- [ ]` line to the description.",
        "Edit with `gh issue edit <N> --body-file <file>`. Nothing done here? Close it",
        "`--reason \"not planned\"`. Bypass (logged): `# %s <reason>`." % BYPASS,
    ]
    return "\n".join(lines)


def evaluate(cmd, cwd, runner=None):
    """`(verdict, text)`: verdict "allow" or "block"; text is the block message
    or a log note. Pure apart from the gh read (`runner` is the test seam)."""
    targets, err = close_targets(cmd)
    if err:
        return "allow", "fail-open: %s" % err
    if not targets:
        return "allow", ""
    blocked, notes = [], []
    for repo, num in targets:
        body, rerr = _read_body(repo, num, cwd, runner=runner)
        ref = "%s#%s" % (repo or "", num)
        if rerr:
            notes.append("fail-open: %s %s" % (ref, rerr))
            continue
        items = unchecked_items(body)
        if items:
            blocked.append((ref, items))
        else:
            notes.append("pass: %s no unchecked lines" % ref)
    if blocked:
        return "block", block_message(blocked)
    return "allow", "; ".join(notes)


def main():
    payload = read_payload()
    cmd = command_of(payload)
    if not cmd or "gh" not in cmd:
        sys.exit(0)
    cwd = field_of(payload, "cwd", "") or os.getcwd()
    if BYPASS in cmd:
        _log("bypass: %s" % cmd[:200].replace("\n", " "))
        sys.exit(0)
    verdict, text = evaluate(cmd, cwd)
    if verdict == "block":
        _log("block: %s" % cmd[:200].replace("\n", " "))
        sys.stderr.write(text + "\n")
        sys.exit(2)
    if text:
        _log(text)
    sys.exit(0)


if __name__ == "__main__":
    main()
