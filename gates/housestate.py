"""gates.housestate -- the house-stack STATE check after a Bash call (#1333,
review round 3, ROZHODNUTÉ A).

Entry for hooks/check-frontend-framework-state.sh (PostToolUse Bash).

Two review rounds bypassed a PreToolUse parser of package-manager commands a
dozen ways (`#` comments, `npm --prefix`, `pnpm -C`, `yarn workspace`,
subshells, `npm exec`, `npm pkg set`, `jq > package.json`, ...): the #1220
lesson, never guard STATE by parsing commands. This check reads the STATE the
command left instead.

When the command text mentions a JS package manager or scaffolder (a cheap
prefilter; a false positive only costs one scan) and the cwd is a house
checkout (``cli_stack_scope.stack_for``: registry ``stack`` = house, or an
unregistered repo), it compares the repo's ``package.json`` files -- working
tree + untracked (``git ls-files --cached --others --exclude-standard``),
``SKIP_DIRS`` and hidden dirs left out -- with every ``package.json`` at HEAD
(none in a repo without commits). A framework FAMILY present now but in no
HEAD package.json, with no marker naming it in a CLAUDE.md from the file's dir
up to the repo root, exits 2 with the reason on stderr (the PostToolUse channel
to the model): revert it, or get the owner's exception. A covered family is
logged as ALLOW-MARKER. Outside any repo nothing is compared. A git failure
fails OPEN with a logged reason. STDLIB ONLY.
"""
import json
import os
import re
import subprocess

from cli_stack_scope import HOUSE, SKIP_DIRS, git_layout, stack_for
from gates import allow, emit_block_stderr, read_payload
from gates.housestack import REGISTRY, claude_mds, fail_open, uncovered

_MENTIONS = re.compile(
    r"\b(?:npm|pnpm|yarn|bun|npx|pnpx|bunx|corepack)\b|create-[\w.-]+|package\.json")


def _judged(rel):
    parts = rel.split("/")
    return (parts[-1] == "package.json" and not set(parts[:-1]) & set(SKIP_DIRS)
            and not any(p.startswith(".") for p in parts[:-1]))


def _git(top, args, stdin=None):
    """git stdout bytes, or None on failure."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    try:
        r = subprocess.run(["git", "-C", top, *args], input=stdin, capture_output=True,
                           timeout=20, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def now_files(top):
    """{rel: text} of every judged package.json in the working tree (tracked
    or untracked, not ignored)."""
    out = _git(top, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    if out is None:
        fail_open(f"git ls-files failed in {top}")
    files = {}
    for rel in dict.fromkeys(out.decode("utf-8", "replace").split("\0")):
        if rel and _judged(rel):
            try:
                with open(os.path.join(top, rel), encoding="utf-8") as f:
                    files[rel] = f.read()
            except (OSError, UnicodeDecodeError):
                continue
    return files


def head_files(top):
    """{rel: text} of every judged package.json at HEAD ({} without commits)."""
    if _git(top, ["rev-parse", "--verify", "-q", "HEAD"]) is None:
        return {}
    out = _git(top, ["ls-tree", "-r", "-z", "--name-only", "HEAD"])
    if out is None:
        fail_open(f"git ls-tree failed in {top}")
    rels = [r for r in out.decode("utf-8", "replace").split("\0") if r and _judged(r)]
    if not rels:
        return {}
    blob = _git(top, ["cat-file", "--batch"],
                stdin="".join("HEAD:%s\n" % r for r in rels).encode("utf-8"))
    if blob is None:
        fail_open(f"git cat-file failed in {top}")
    files, pos = {}, 0
    for rel in rels:
        nl = blob.index(b"\n", pos)
        header = blob[pos:nl].split()
        pos = nl + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            files[rel] = blob[pos:pos + size].decode("utf-8", "replace")
            pos += size + 1
    return files


def check(command, cwd):
    """Block reason for the state a Bash call left, or ""."""
    from cli_onboard_stack import package_json_frameworks
    if not _MENTIONS.search(command or "") or not cwd:
        return ""
    layout = git_layout(cwd)
    if layout is None or stack_for(cwd, REGISTRY)[1] != HOUSE:
        return ""
    top = layout[0]
    at_head = set()
    for text in head_files(top).values():
        at_head |= package_json_frameworks(text) or set()
    new_by_file = {}
    for rel, text in now_files(top).items():
        fresh = (package_json_frameworks(text) or set()) - at_head
        if fresh:
            new_by_file[rel] = fresh
    if not new_by_file:
        return ""
    chains, families = [], set()
    for rel, fresh in sorted(new_by_file.items()):
        chains += claude_mds(os.path.join(top, os.path.dirname(rel)), top)
        families |= fresh
    what = f"`{command.strip().splitlines()[0][:80]}`"
    left = uncovered(families, chains, what)
    if not left:
        return ""
    files = ", ".join(rel for rel, fresh in sorted(new_by_file.items()) if fresh & set(left))
    return (
        f"🚫 house stack (#1333): after {what}, {files} depend(s) on the JS UI "
        f"framework(s) {', '.join(left)}, absent from every package.json at HEAD.\n"
        "The house stack's web UI is Leptos CSR → WASM built by Trunk, served by "
        "the axum service (~/.claude/rules/house-stack.md). Revert it now (remove "
        "the dependency / the scaffold, `git checkout -- <file>`), or ask the owner "
        "for the exception and record `<!-- airuleset:stack-exception <what> — <#N "
        "or GitHub URL> -->` (`<what>` names the technology) in the project "
        "CLAUDE.md; never on your own judgement.")


def main():
    payload = read_payload()
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        obj = None
    tool_input = obj.get("tool_input") if isinstance(obj, dict) else None
    if not isinstance(tool_input, dict):
        fail_open("unreadable PostToolUse payload")
    reason = check(str(tool_input.get("command") or ""), str(obj.get("cwd") or ""))
    if reason:
        from gates.housestack import log
        log(f"BLOCK state {obj.get('cwd')}")
        emit_block_stderr(reason)
    allow()


if __name__ == "__main__":
    main()
