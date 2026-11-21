"""gates.housestack -- the house-stack frontend-framework guard (#1333).

Entry for hooks/block-frontend-framework.sh (PreToolUse Write|Edit).

REFUSES a ``package.json`` Write or Edit (an empty ``old_string`` is a whole new
file) that brings a JS UI framework FAMILY (``cli_onboard_stack.FRAMEWORK_FAMILY``:
react incl. react-dom / next, vue incl. nuxt, svelte incl. sveltekit, angular,
preact, solid, lit, alpine, htmx, astro, qwik, ember) NEW to the git repo into a
house project, unless that family's owner-approved marker
``<!-- airuleset:stack-exception <what> — <#N or GitHub URL> -->`` is in the
repo-root ``CLAUDE.md``. The house stack's web UI is Leptos/WASM
(``rules/house-stack.md``); the owner, 21.11.2026, kept finding projects
"postavane na random adhoc zvolenych technologiach".

It judges file CONTENT only. Review round 3 (ROZHODNUTÉ A) removed a Bash
command-text parser (bypassed a dozen ways, the #1220 lesson: never guard state
by parsing commands); ``gates.housestate`` checks the repo STATE after Bash.

- New = absent from the file before the change AND from every other judged
  package.json of the repo: ``repo_package_files`` = ``git ls-files -co
  --exclude-standard`` (the state check's set; hidden dirs, ``SKIP_DIRS`` and
  paths deeper than ``SCAN_DEPTH`` left out). Nothing under a skipped dir is
  judged.
- House = the registry ``stack`` of the file's checkout, or, for a path outside
  any repo, of the session cwd, whose repo then supplies the existing set and
  the root ``CLAUDE.md`` (round 3 B, round 4 item 3); an unregistered repo is
  house; an unreadable registry is UNKNOWN: skipped with a log line.
- One marker per technology, read from the repo-root ``CLAUDE.md`` only (round
  4 item 6); a marker-allowed change is logged as ALLOW-MARKER.

FAIL-OPEN with a logged reason (the sibling Write|Edit gates' contract), in
``~/.claude/stack-gate.log``: an unreadable payload, an unreadable or
unparseable existing package.json, unparseable new content, an Edit whose
``old_string`` is not in the file (CRLF or stale text), an unreadable
CLAUDE.md, a git failure. Exit 2 = block, reason on STDERR only (the adapter
runs this module with ``1>&2``). STDLIB ONLY.
"""
import json
import os
import subprocess
import time

from cli_stack_scope import (HOUSE, REGISTRY_FILENAME, SCAN_DEPTH, SKIP_DIRS, UNKNOWN,
                             git_env, git_timeout, has_git_ancestor, repo_root,
                             stack_for, start_deadline)
from gates import allow, emit_block_stderr, read_payload

GATE_LOG = "stack-gate.log"
REGISTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        REGISTRY_FILENAME)


def log(line):
    try:
        d = os.path.join(os.path.expanduser("~"), ".claude")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, GATE_LOG), "a", encoding="utf-8") as f:
            f.write(f"{int(time.time())} {line}\n")
    except OSError:
        pass  # airuleset:script-ok a gate must never fail on its own audit log


def fail_open(reason):
    log("FAIL-OPEN " + reason)
    print(f"stack gate: {reason} — allowing (fail-open).", flush=True)
    allow()


def read(path):
    """File text, "" when the file does not exist, None when it cannot be
    read (a dir, no permission, not UTF-8)."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def skipped(rel_or_path):
    parts = str(rel_or_path).replace("\\", "/").split("/")[:-1]
    return bool(set(parts) & set(SKIP_DIRS))


def judged(rel):
    """A repo-relative package.json the gates judge: not under a hidden or
    skipped dir, not deeper than SCAN_DEPTH."""
    parts = rel.split("/")
    return (parts[-1] == "package.json" and len(parts) <= SCAN_DEPTH
            and not set(parts[:-1]) & set(SKIP_DIRS)
            and not any(p.startswith(".") for p in parts[:-1]))


def git(top, args, stdin=None):
    """git stdout bytes, or None on failure; bounded by the run's one deadline
    (cli_stack_scope.start_deadline, round 5 item 5)."""
    timeout = git_timeout()
    if timeout <= 0:
        return None
    try:
        r = subprocess.run(["git", "-C", top, *args], input=stdin, capture_output=True,
                           timeout=timeout, env=git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _names(out):
    return [r for r in dict.fromkeys(out.decode("utf-8", "replace").split("\0"))
            if r and judged(r)]


def repo_package_files(top, args=("ls-files", "-z", "--cached", "--others",
                                  "--exclude-standard", "--", "*package.json")):
    """{rel: text} of the judged package.json files git lists for `top`
    (default: tracked + untracked, not ignored; a pathspec, so git lists only
    package.json files), read from the working tree."""
    out = git(top, list(args))
    if out is None:
        fail_open(f"git {args[0]} failed in {top}")
    files = {}
    for rel in _names(out):
        text = read(os.path.join(top, rel))
        if text:
            files[rel] = text
    return files


def uncovered(families, claude_md, what):
    """The families no marker in the repo-root `claude_md` covers; a covered
    one is logged as ALLOW-MARKER. An unreadable CLAUDE.md fails open."""
    from cli_onboard_stack import exception_covers
    text = read(claude_md)
    if text is None:
        fail_open(f"cannot read {claude_md} for {what}")
    left = []
    for fam in sorted(families):
        if text and exception_covers(text, fam):
            log(f"ALLOW-MARKER {what} framework={fam} claude_md={claude_md}")
        else:
            left.append(fam)
    return left


def house(start, cwd=None, registry=None):
    """True when `start` (or, outside any repo, `cwd`) is a house checkout;
    an unreadable registry is logged and never house."""
    stack = stack_for(start, registry or REGISTRY, fallback=cwd)[1]
    if stack == UNKNOWN:
        log(f"SKIP registry unreadable ({registry or REGISTRY}) for {start}")
    return stack == HOUSE


def block_reason(families, what, where):
    return (
        f"🚫 BLOCKED: {what} adds the JS UI framework(s) {', '.join(families)}, new "
        f"to {where}, without its stack exception (#1333).\n"
        "The house stack is the default for every NEW app: web UI = Leptos CSR → "
        "WASM built by Trunk, served by the axum service — read "
        "~/.claude/rules/house-stack.md before choosing.\n"
        "Another stack only as an OWNER-approved exception, one per technology: ask "
        "the owner, then record `<!-- airuleset:stack-exception <what> — <#N or "
        "GitHub URL> -->` (`<what>` names the technology) in the repo-root "
        "CLAUDE.md, never on your own judgement. A clearly better option than the "
        "house stack is proposed as a stack upgrade (the rule's \"Stack "
        "evolution\"), never used silently.")


def _after_edit(before, tool_input, file_path):
    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        fail_open(f"no old_string/new_string in the Edit of {file_path}")
    if old not in before:
        fail_open(f"old_string not found in {file_path} (CRLF or stale text)")
    if tool_input.get("replace_all") is True:
        return before.replace(old, new)
    return before.replace(old, new, 1)


def classify(tool_name, tool_input, cwd):
    """Block reason for one package.json Write/Edit, or ""."""
    from cli_onboard_stack import package_json_frameworks
    file_path = os.path.abspath(str(tool_input.get("file_path") or "/"))
    if os.path.basename(file_path) != "package.json":
        return ""
    start = os.path.dirname(file_path)
    own_root = repo_root(start)
    if own_root is None and has_git_ancestor(start):
        fail_open(f"git failed for {start}")
    # round 5: the skip check reads the REPO-relative path (a checkout may itself
    # live under a `tests/` dir)
    if skipped(os.path.relpath(file_path, own_root) if own_root else file_path):
        return ""
    if not house(start, cwd):
        return ""
    before = read(file_path)
    if before is None:
        fail_open(f"cannot read the existing {file_path}")
    if tool_name == "Edit" and tool_input.get("old_string") == "":
        after = tool_input.get("new_string")
    elif tool_name == "Edit":
        if not before:
            fail_open(f"cannot read {file_path} to judge the edit")
        after = _after_edit(before, tool_input, file_path)
    else:
        after = tool_input.get("content")
    if not isinstance(after, str):
        fail_open(f"no content in the {tool_name} of {file_path}")
    after_fw = package_json_frameworks(after)
    if after_fw is None:
        fail_open(f"unparseable package.json content for {file_path}")
    before_fw = package_json_frameworks(before) if before.strip() else set()
    if before_fw is None:
        fail_open(f"unparseable existing {file_path}")
    families = after_fw - before_fw
    if not families:
        return ""
    root = own_root
    where = "this repo"
    if root is None and cwd:
        root, where = repo_root(cwd), "the session's repo"
    existing = set()
    if root:
        real = os.path.realpath(file_path)
        for rel, text in repo_package_files(root).items():
            if os.path.realpath(os.path.join(root, rel)) != real:
                existing |= package_json_frameworks(text) or set()
    claude_md = os.path.join(root or cwd or start, "CLAUDE.md")
    left = uncovered(families - existing, claude_md, f"`{file_path}`")
    return block_reason(left, f"`{file_path}`", where) if left else ""


def main():
    payload = read_payload()
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        obj = None
    tool_input = obj.get("tool_input") if isinstance(obj, dict) else None
    if not isinstance(tool_input, dict):
        fail_open("unreadable payload")
    tool = str(obj.get("tool_name") or "")
    if tool not in ("Write", "Edit"):
        allow()
    start_deadline()
    reason = classify(tool, tool_input, str(obj.get("cwd") or "") or None)
    if reason:
        log(f"BLOCK {tool} {tool_input.get('file_path')}")
        emit_block_stderr(reason)
    allow()


if __name__ == "__main__":
    main()
