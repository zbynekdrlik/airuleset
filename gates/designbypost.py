"""gates.designbypost -- the `Design-by: main` anti-spoof block (#1061 item 1).
Entry for hooks/block-design-by-spoof.sh.

A lane AUTHOR (a dispatched WORKER, or the #1060 L3b dual-agent IMPLEMENTER
session) must not pass off its own design as the Fable main's. The truthful
stamp is written by `airuleset.py design-record` (which reads the model from the
session's OWN transcript and, from a worktree cwd, stamps `Design-by: worker`).
This gate BLOCKS a RAW `gh issue comment` whose body carries `Design-by: main`
when the author is a lane worker (worktree cwd) OR the implementer
(`AIRULESET_ROLE=implementer`) -- the stamp the implementer's design check
(gates.designdispatch) trusts. FAIL-CLOSED for the spoof (a lane author + `Design-by: main` ->
block); everything else (a non-comment command, a main cwd, a `Design-by:
worker` comment, design-record's own stdin post) is ALLOWED. Bypass:
`# airuleset:design-by-ok <reason>` in the command (logged to
~/.claude/design-by-gate.log).

STDLIB ONLY at import; cli_authorship is imported lazily.
"""
import os
import re
import time

from gates import read_payload, command_of, field_of, emit_block_stderr, allow
from gates.shellcmd import split_top_level

DESIGN_BY_LOG = "design-by-gate.log"

# A comment-POST prefilter (#1061 review): `gh issue comment` (optional
# -R/--repo) and a `gh api …issues/<N>/comments` call that WRITES a body.
_GH_COMMENT_RE = re.compile(
    r"\bgh\s+(?:(?:-R[=\s]*|--repo[=\s]+)\S+\s+)?issue\s+comment\b")
_GH_API_COMMENTS_RE = re.compile(r"\bgh\s+api\b[^\n]*?/issues/[0-9]+/comments\b")
# Issue 1137 B1: a READ (no field/input/write method) posts nothing, even
# when its --jq filter names the token.
_API_WRITE_RE = re.compile(
    r"\s(?:-f|-F|--field|--raw-field|--input)\b|(?:-X|--method)[=\s]*(?:POST|PATCH|PUT)",
    re.IGNORECASE)


def _posts_comment(cmd):
    cmd = cmd.replace("\\\n", " ")   # a `\`+newline continuation (review A7)
    if _GH_COMMENT_RE.search(cmd):
        return True
    return any(_API_WRITE_RE.search(cmd[m.start():].split("\n", 1)[0])
               for m in _GH_API_COMMENTS_RE.finditer(cmd))

# `Design-by: main` (bold/bullet tolerant), the spoof signature. Deliberately
# does NOT match `Design-by: worker` (an honest worker stamp is fine).
_DESIGN_BY_MAIN_RE = re.compile(
    r"Design-?by\**\s*:\s*\**\s*main\b", re.IGNORECASE)

_BYPASS_RE = re.compile(r"airuleset:design-by-ok\s*(?P<reason>.*)", re.IGNORECASE)

# `-F <path>` / `--body-file <path>`, the file whose content is the comment body.
# `[=\s]*` (not `[=\s]+`) so the GLUED short-flag form `-F<path>` (valid gh/cobra
# syntax) is caught too (#1061 review). `-` (stdin) is not a file and is skipped.
_BODY_FILE_RE = re.compile(r"(?:-F|--body-file)[=\s]*(?P<path>[^\s'\"|;&]+)")


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
    # "lane author" = ANY non-main author who must not pass a design off as the
    # Fable main's: a dispatched WORKER (worktree cwd) OR the #1060 L3b dual-agent
    # IMPLEMENTER session (AIRULESET_ROLE=implementer, set by the claude-impl
    # launcher). authorship_role() returns "implementer" for the latter, so the
    # gate must block BOTH (keying on `== "worker"` alone let an implementer spoof
    # `Design-by: main` straight through — the L3b review 🔴).
    try:
        import cli_authorship
        return cli_authorship.authorship_role(cwd) in ("worker", "implementer")
    except Exception:
        # cli_authorship unavailable -> fall back to the same substring predicate
        # PLUS the env role (the implementer works in a worktree, so the path
        # check usually catches it too, but the env is the authoritative signal).
        return ("/.claude/worktrees/" in ((cwd or "").rstrip("/") + "/")
                or os.environ.get("AIRULESET_ROLE") == "implementer")


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
    if not cmd or not _posts_comment(cmd):
        return "allow", "not a comment post (gh issue comment / gh api …/comments)"
    if _BYPASS_RE.search(cmd):
        _log("BYPASS", cwd, _BYPASS_RE.search(cmd).group("reason").strip())
        return "allow", "design-by-ok bypass (logged)"
    if not _is_lane_worker(cwd):
        return "allow", "not a lane worktree cwd (main may stamp Design-by: main)"
    for text in _body_texts(cmd, cwd):
        if _DESIGN_BY_MAIN_RE.search(text):
            return "block", ("a lane author (worker/implementer) comment post "
                             "carries `Design-by: main`")
    return "allow", "no Design-by: main in a lane-author comment"


def _edits_and_posts(cmd):
    """Another command in the same call names the token — typically the edit
    that strips it from the draft, which runs only AFTER this hook read it."""
    return any(re.search(r"Design-?by", seg, re.I) and not _posts_comment(seg)
               for seg in split_top_level(cmd))


def _block_message(cmd=""):
    split = ("  This call edits the draft and posts it, but the hook reads the draft\n"
             "  before the edit runs: edit the draft in one call, post it in the next.\n"
             "\n") if _edits_and_posts(cmd) else ""
    return (
        "BLOCKED: a lane author may not post `Design-by: main` -- the main session's\n"
        "  stamp comes from `python3 ~/devel/airuleset/airuleset.py design-record`.\n"
        "\n" + split +
        "  A worker or the dual-agent implementer cites the main's design by its\n"
        "  comment id, WITHOUT the `Design-by: main` token (#1061 / #1060 L3b);\n"
        "  from a worktree, design-record itself stamps `Design-by: worker`.\n"
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
        emit_block_stderr(_block_message(cmd))
    allow()


if __name__ == "__main__":
    main()
