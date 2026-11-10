"""gates.closechecklist -- refuse closing an issue whose DESCRIPTION still has an
unchecked `- [ ]` line (#1297, owner 10.11.2026).

The issue description is the single task list of its deliverable: a step of the
same deliverable is a `- [ ] <step>` line, a separate deliverable is its own
issue linked back as `- [ ] #<child>` (or `- [ ] owner/repo#N`). So a close with
any unchecked line left would report open work as done. Every unchecked line
counts, the child links included: tick `- [x] #child` once the child is closed.

Shape: a pure predicate (`unchecked_items`) + a per-segment close parser
(`close_targets`: heredoc bodies dropped, top-level split by
`gates.shellcmd.split_top_level`, each segment tokenised with shlex, `cd` folded
with `gates.filing.parse._apply_cd`, the command word and the not-planned flag
read with the fork close guard's own helpers in `hooks/close_guard_segment.py`)
+ `main()` for the thin hook adapter `hooks/block-close-with-open-checklist.sh`.

Covered close shapes: `gh [-R o/r] issue close <N | issue URL> [-R o/r]` and
`gh api [https://api.github.com/]repos/<o>/<r>/issues/<N> -X PATCH` with a
`state=closed` field or an `--input` JSON file whose `state` is `closed`. A PATCH
that only edits the body is NOT a close. GitHub's own auto-close from a merged
PR's `Closes #N` never passes through this gate.

Exempt: a `--reason "not planned"` close (`state_reason=not_planned` on the PATCH
form): nothing is being reported done. FAIL-OPEN on every read failure (no slug,
gh error, unreadable --input file, overall time budget spent): the close passes
and the reason is logged to ~/.claude/close-checklist-gate.log -- this gate
guards a discipline, it must never wedge a session on a GitHub outage. Bypass:
`# airuleset:checklist-close-ok <reason>` (logged).
"""
import json
import os
import re
import shlex
import sys
import time

from gates import audit, command_of, field_of, read_payload

_HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")

# A task line: optional blockquote markers, a list marker, `[ ]`, then text.
_UNCHECKED_RE = re.compile(r"^\s*(?:>\s*)*(?:[-*+]|\d+[.)])\s+\[ \]\s+(\S.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*(?:>\s*)*(`{3,}|~{3,})")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

# `<<WORD`, `<<-'WORD'`, `<<"WORD"` anywhere on a line (never `<<<`).
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\2")
_API_PATH_RE = re.compile(r"^(?:https://api\.github\.com)?/?repos/([^/\s]+)/([^/\s]+)/issues/(\d+)/?$")
_ISSUE_URL_RE = re.compile(r"^https://github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)/?$")
_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_VALUE_FLAGS = {"-c", "--comment", "-r", "--reason", "-b", "--body", "-X", "--method",
                "-H", "--header", "-q", "--jq", "-t", "--template", "--input", "-f", "-F",
                "--field", "--raw-field", "-p", "--preview", "--hostname", "--cache"}
_FIELD_FLAGS = {"-f", "-F", "--field", "--raw-field"}

BYPASS = "airuleset:checklist-close-ok"
LOG_NAME = "close-checklist-gate.log"
TIME_BUDGET_S = 11.0  # under the hook's 15 s timeout in settings/hooks.json


def unchecked_items(body):
    """Texts of every unchecked task line in `body`, in order. Skips fenced code
    blocks (closed only by the same fence char, at least as long) and HTML
    comments (issue templates carry `<!-- - [ ] ... -->` examples)."""
    items = []
    fence = None
    for line in _HTML_COMMENT_RE.sub("", body or "").splitlines():
        fm = _FENCE_RE.match(line)
        if fm:
            mark = fm.group(1)
            if fence is None:
                fence = mark
                continue
            if mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
                continue
        if fence is not None:
            continue
        m = _UNCHECKED_RE.match(line)
        if m:
            items.append(m.group(1))
    return items


def _strip_heredoc_bodies(cmd):
    """`cmd` with every heredoc BODY removed (the operator line is kept). A body
    is data for its consumer, never a top-level command, whatever follows the
    delimiter on the operator line (`cat <<EOF > f` included)."""
    out, pending = [], []
    for line in cmd.split("\n"):
        if pending:
            dash, word = pending[0]
            if (line.lstrip("\t") if dash else line) == word:
                pending.pop(0)
            continue
        out.append(line)
        pending = [(m.group(1) == "-", m.group(3)) for m in _HEREDOC_RE.finditer(line)]
    return "\n".join(out)


def _helpers():
    if _HOOKS_DIR not in sys.path:
        sys.path.insert(0, _HOOKS_DIR)
    import close_guard_segment
    return close_guard_segment


def _flag_values(args, names):
    """Every value of the flags in `names` (`-X v`, `-X=v`, `--method=v`, `-Xv`)."""
    vals = []
    for i, t in enumerate(args):
        for n in names:
            if t == n and i + 1 < len(args):
                vals.append(args[i + 1])
            elif t.startswith(n + "="):
                vals.append(t[len(n) + 1:])
            elif len(n) == 2 and t.startswith(n) and len(t) > 2 and t[2] != "=":
                vals.append(t[2:])
    return vals


def _positional(args):
    pos, i = [], 0
    while i < len(args):
        t = args[i]
        if t in _VALUE_FLAGS or t in ("-R", "--repo"):
            i += 2
            continue
        if not t.startswith("-"):
            pos.append(t)
        i += 1
    return pos


def _patch_close(args, cwd, notes):
    """`(is_close, not_planned)` for a `gh api ... PATCH` segment's args."""
    fields = _flag_values(args, _FIELD_FLAGS)
    state = {f.split("=", 1)[0]: f.split("=", 1)[1] for f in fields if "=" in f}
    if state.get("state") == "closed":
        return True, state.get("state_reason") == "not_planned"
    for path in _flag_values(args, ("--input",)):
        if path == "-":
            notes.append("fail-open: PATCH --input from stdin cannot be read")
            continue
        full = path if os.path.isabs(path) or cwd is None else os.path.join(cwd, path)
        try:
            with open(full, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            notes.append("fail-open: PATCH --input %s unreadable: %s" % (path, exc))
            continue
        if isinstance(data, dict) and data.get("state") == "closed":
            return True, data.get("state_reason") == "not_planned"
    return False, False


def _segment_targets(tokens, cwd, notes):
    """Close targets `(repo_or_None, number, cwd)` of one tokenised segment."""
    cg = _helpers()
    wi = cg._effective_word_index(tokens)
    if wi >= len(tokens) or os.path.basename(tokens[wi]) != "gh":
        return []
    args = tokens[wi + 1:]
    repos = [r for r in _flag_values(args, ("-R", "--repo")) if r]
    repo = repos[0] if repos else None
    pos = _positional(args)
    if pos[:2] == ["issue", "close"]:
        if len(pos) < 3:
            notes.append("fail-open: issue close without a number")
            return []
        if cg._is_not_planned(args):
            return []
        url = _ISSUE_URL_RE.match(pos[2])
        if url:
            return [(url.group(1), url.group(2), cwd)]
        num = re.match(r"^#?(\d+)$", pos[2])
        if not num:
            notes.append("fail-open: unparseable close target %r" % pos[2][:60])
            return []
        if repo is not None and not _REPO_RE.match(repo):
            notes.append("fail-open: unparseable repo %r" % repo[:60])
            return []
        return [(repo, num.group(1), cwd)]
    if pos[:1] == ["api"] and len(pos) >= 2:
        methods = [m.upper() for m in _flag_values(args, ("-X", "--method"))]
        if "PATCH" not in methods:
            return []
        m = _API_PATH_RE.match(pos[1])
        if not m:
            return []
        is_close, not_planned = _patch_close(args, cwd, notes)
        if not is_close or not_planned:
            return []
        owner, name, number = m.groups()
        placeholder = "{" in owner or "{" in name
        return [(None if placeholder else "%s/%s" % (owner, name), number, cwd)]
    return []


def close_targets(cmd, cwd=None):
    """`(targets, notes)`: `targets` lists `(repo_or_None, number, seg_cwd)` for
    every close in `cmd` that is not a not-planned close; `notes` names every
    shape that could not be read (the caller logs them and fails open)."""
    notes = []
    if "close" not in cmd and "patch" not in cmd.lower():
        return [], notes
    from gates.filing.parse import _apply_cd
    from gates.shellcmd import split_top_level
    targets, seg_cwd = [], cwd
    for seg in split_top_level(_strip_heredoc_bodies(cmd.replace("\\\n", ""))):
        if not seg.strip():
            continue
        try:
            tokens = shlex.split(seg, posix=True, comments=True)
        except ValueError:
            if "close" in seg or "PATCH" in seg.upper():
                notes.append("fail-open: untokenisable segment %r" % seg[:80])
            continue
        if not tokens:
            continue
        if tokens[0] == "cd":
            seg_cwd = _apply_cd(seg_cwd, tokens)
            continue
        targets.extend(_segment_targets(tokens, seg_cwd, notes))
    return targets, notes


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


def evaluate(cmd, cwd, runner=None, budget_s=TIME_BUDGET_S):
    """`(verdict, text)`: verdict "allow" or "block"; text is the block message
    or a log note. Pure apart from the gh reads (`runner` is the test seam)."""
    targets, notes = close_targets(cmd, cwd)
    deadline = time.monotonic() + budget_s
    blocked = []
    for repo, num, seg_cwd in targets:
        ref = "%s#%s" % (repo or "", num)
        if time.monotonic() > deadline:
            notes.append("fail-open: time budget spent before %s" % ref)
            break
        body, rerr = _read_body(repo, num, seg_cwd or cwd, runner=runner)
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
    try:
        verdict, text = evaluate(cmd, cwd)
    except Exception as exc:  # noqa: BLE001 -- fail open, but never silently
        _log("fail-open: internal error %r" % (exc,))
        sys.stderr.write("close-checklist gate: internal error %r -- allowing\n" % (exc,))
        sys.exit(0)
    if verdict == "block":
        _log("block: %s" % cmd[:200].replace("\n", " "))
        sys.stderr.write(text + "\n")
        sys.exit(2)
    if text:
        _log(text)
    sys.exit(0)


if __name__ == "__main__":
    main()
