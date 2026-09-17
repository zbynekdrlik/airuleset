"""gates.designbypost -- the `Design-by: main` anti-spoof block (#1061 item 1).
Entry for hooks/block-design-by-spoof.sh.

A lane WORKER must not pass off its own design as the Fable main's. The truthful
stamp is written by `airuleset.py design-record` (which reads the model from the
session's OWN transcript and, from a worktree cwd, stamps `Design-by: worker`).
This gate BLOCKS a RAW `gh issue comment` whose body carries `Design-by: main`
when the cwd is a lane worktree -- the exact spoof the dispatch precondition
(gates.designdispatch, which trusts a `Design-by: main` comment) must not be
fooled by. FAIL-CLOSED for the spoof (a worker cwd + `Design-by: main` -> block);
everything else (a non-comment command, a main cwd, a `Design-by: worker`
comment, design-record's own stdin post) is ALLOWED. Bypass:
`# airuleset:design-by-ok <reason>` in the command (logged to
~/.claude/design-by-gate.log).

STDLIB ONLY at import; cli_authorship is imported lazily.
"""
import os
import re
import time

from gates import read_payload, command_of, field_of, emit_block_stderr, allow

DESIGN_BY_LOG = "design-by-gate.log"

# The `gh issue comment` invocation prefilter -- the SAME shape
# post-record-design-comment.sh uses (optional -R/--repo between gh and issue).
_GH_COMMENT_RE = re.compile(
    r"\bgh\s+(?:(?:-R[=\s]*|--repo[=\s]+)\S+\s+)?issue\s+comment\b")

# `Design-by: main` (bold/bullet tolerant), the spoof signature. Deliberately
# does NOT match `Design-by: worker` (an honest worker stamp is fine).
_DESIGN_BY_MAIN_RE = re.compile(
    r"Design-?by\**\s*:\s*\**\s*main\b", re.IGNORECASE)

_BYPASS_RE = re.compile(r"airuleset:design-by-ok\s*(?P<reason>.*)", re.IGNORECASE)

# `-F <path>` / `--body-file <path>` (glued or separated), the file whose content
# is the comment body. `-` (stdin) is not a file and is skipped.
_BODY_FILE_RE = re.compile(r"(?:-F|--body-file)[=\s]+(?P<path>[^\s'\"|;&]+)")


def _log(kind, cwd, extra=""):
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", DESIGN_BY_LOG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\t%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                kind, cwd, extra))
    except OSError:
        return


def _is_lane_worker(cwd):
    try:
        import cli_authorship
        return cli_authorship.authorship_role(cwd) == "worker"
    except Exception:
        # cli_authorship unavailable -> fall back to the same substring predicate.
        return "/.claude/worktrees/" in ((cwd or "").rstrip("/") + "/")


def _body_texts(cmd, cwd):
    """The command text itself (covers an inline --body/-b/-m) PLUS the content
    of any readable `-F`/`--body-file` literal file (best-effort). Yields each
    text to scan for the spoof signature."""
    yield cmd or ""
    for m in _BODY_FILE_RE.finditer(cmd or ""):
        p = m.group("path")
        if p == "-":
            continue
        if not os.path.isabs(p):
            p = os.path.join(cwd or ".", p)
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                yield fh.read(200000)
        except OSError:
            continue


def evaluate(cmd, cwd):
    """('allow'|'block', reason). Pure of process exit for tests."""
    if not cmd or not _GH_COMMENT_RE.search(cmd):
        return "allow", "not a gh issue comment"
    if _BYPASS_RE.search(cmd):
        _log("BYPASS", cwd, _BYPASS_RE.search(cmd).group("reason").strip())
        return "allow", "design-by-ok bypass (logged)"
    if not _is_lane_worker(cwd):
        return "allow", "not a lane worktree cwd (main may stamp Design-by: main)"
    for text in _body_texts(cmd, cwd):
        if _DESIGN_BY_MAIN_RE.search(text):
            return "block", "a lane worker's gh issue comment carries `Design-by: main`"
    return "allow", "no Design-by: main in a worker comment"


def _block_message():
    return (
        "BLOCKED: a lane WORKER may not post `Design-by: main` (#1061).\n"
        "\n"
        "  The truthful authorship stamp is written by the MAIN session via\n"
        "  `airuleset.py design-record` (it reads the model from the session's\n"
        "  OWN transcript; from a worktree it stamps `Design-by: worker`). A\n"
        "  worker hand-typing `Design-by: main` is exactly the spoof the dispatch\n"
        "  precondition must not trust.\n"
        "\n"
        "  - If you ARE the main: run design-record from the main checkout, not a\n"
        "    worktree.\n"
        "  - If you are the worker: post your `Anchors-confirmed:` /\n"
        "    `Design-question:` comment WITHOUT a `Design-by: main` line.\n"
        "\n"
        "  Bypass (rare, logged): add `# airuleset:design-by-ok <reason>` to the\n"
        "  command.")


def main():
    payload = read_payload()
    if not payload:
        allow()
    cmd = command_of(payload)
    cwd = field_of(payload, "cwd", "")
    verdict, reason = evaluate(cmd, cwd)
    if verdict == "block":
        _log("BLOCK", cwd, reason)
        emit_block_stderr(_block_message())
    allow()


if __name__ == "__main__":
    main()
