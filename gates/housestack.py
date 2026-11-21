"""gates.housestack -- the house-stack frontend-framework guard (#1333).

Entry for hooks/block-frontend-framework.sh (PreToolUse Write|Edit).

REFUSES a ``package.json`` Write or Edit (an empty ``old_string`` is a whole new
file) that brings a JS UI framework FAMILY (``cli_onboard_stack.FRAMEWORK_FAMILY``:
react incl. react-dom / next, vue incl. nuxt, svelte incl. sveltekit, angular,
preact, solid, lit, alpine, htmx, astro, qwik, ember) NEW to the git repo into a
house project, unless that family's owner-approved marker
``<!-- airuleset:stack-exception <what> — <#N or GitHub URL> -->`` exists. The
house stack's web UI is Leptos/WASM (``rules/house-stack.md``); the owner,
21.11.2026, kept finding projects "postavane na random adhoc zvolenych
technologiach".

It judges file CONTENT only. Review round 3 (ROZHODNUTÉ A) removed the Bash
command-text parser (bypassed a dozen ways in two rounds, the #1220 lesson:
never guard state by parsing commands); ``gates.housestate`` checks the repo's
package.json STATE after a Bash call instead.

- New = absent from the file before the change AND from every other
  package.json of the repo; hidden dirs and ``cli_stack_scope.SKIP_DIRS`` never
  count, and nothing under a skipped dir is judged.
- House = the registry ``stack`` of the file's checkout, or, for a path outside
  any repo, of the session cwd (round 3 B); an unregistered repo is house.
- One marker per technology (round 3 E): a marker covers only the family its
  ``<what>`` names, read from every ``CLAUDE.md`` / ``.claude/CLAUDE.md`` from
  the file's dir up to the git root (and the cwd's, for an out-of-repo path).
  A marker-allowed change is logged as ALLOW-MARKER.

FAIL-OPEN with a logged reason (the sibling Write|Edit gates' contract), in
``~/.claude/stack-gate.log``: an unreadable payload, an unreadable or
unparseable existing package.json, unparseable new content, an Edit whose
``old_string`` is not in the file (CRLF), an unreadable CLAUDE.md, a repo too
large to scan. Exit 2 = block, reason on STDERR only (the adapter runs this
module with ``1>&2``). STDLIB ONLY.
"""
import json
import os
import time

from cli_stack_scope import HOUSE, REGISTRY_FILENAME, SKIP_DIRS, repo_root, stack_for
from gates import allow, emit_block_stderr, read_payload

GATE_LOG = "stack-gate.log"
REGISTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        REGISTRY_FILENAME)
_SCAN_MAX_DIRS = 20000
_SCAN_MAX_DEPTH = 8


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


def claude_mds(start, root):
    """CLAUDE.md and .claude/CLAUDE.md paths from `start` up to `root`
    (inclusive; the filesystem root without one), nearest first."""
    out = []
    d = os.path.abspath(start)
    stop = os.path.abspath(root) if root else None
    while True:
        out += [os.path.join(d, "CLAUDE.md"), os.path.join(d, ".claude", "CLAUDE.md")]
        parent = os.path.dirname(d)
        if d == stop or parent == d:
            return out
        d = parent


def uncovered(families, chains, what):
    """The families no marker in the CLAUDE.md `chains` covers; a covered one
    is logged as ALLOW-MARKER. An unreadable CLAUDE.md fails open."""
    from cli_onboard_stack import exception_covers
    texts = []
    for cm in dict.fromkeys(chains):
        text = read(cm)
        if text is None:
            fail_open(f"cannot read {cm} for {what}")
        if text:
            texts.append((cm, text))
    left = []
    for fam in sorted(families):
        hit = next((cm for cm, text in texts if exception_covers(text, fam)), None)
        if hit:
            log(f"ALLOW-MARKER {what} framework={fam} claude_md={hit}")
        else:
            left.append(fam)
    return left


def repo_frameworks(root, exclude):
    """The families every other package.json of the repo carries, or None when
    the repo is too large to scan. Hidden and skipped dirs never count."""
    from cli_onboard_stack import package_json_frameworks
    found, seen = set(), 0
    base_depth = root.rstrip(os.sep).count(os.sep)
    ex = os.path.abspath(exclude) if exclude else None
    for dirpath, dirnames, filenames in os.walk(root):
        seen += 1
        if seen > _SCAN_MAX_DIRS:
            return None
        if dirpath.count(os.sep) - base_depth >= _SCAN_MAX_DEPTH:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in SKIP_DIRS]
        if "package.json" in filenames:
            p = os.path.join(dirpath, "package.json")
            if os.path.abspath(p) != ex:
                found |= package_json_frameworks(read(p) or "") or set()
    return found


def block_reason(families, what):
    return (
        f"🚫 BLOCKED: {what} adds the JS UI framework(s) {', '.join(families)}, new "
        "to this repo, without its stack exception (#1333).\n"
        "The house stack is the default for every NEW app: web UI = Leptos CSR → "
        "WASM built by Trunk, served by the axum service — read "
        "~/.claude/rules/house-stack.md before choosing.\n"
        "Another stack only as an OWNER-approved exception, one per technology: ask "
        "the owner, then record `<!-- airuleset:stack-exception <what> — <#N or "
        "GitHub URL> -->` (`<what>` names the technology) in the project CLAUDE.md, "
        "never on your own judgement. A clearly better option than the house stack "
        "is proposed as a stack upgrade (the rule's \"Stack evolution\"), never "
        "used silently.")


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
    if os.path.basename(file_path) != "package.json" or skipped(file_path):
        return ""
    start = os.path.dirname(file_path)
    if stack_for(start, REGISTRY, fallback=cwd)[1] != HOUSE:
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
    root = repo_root(start)
    existing = repo_frameworks(root, file_path) if root else set()
    if existing is None:
        fail_open(f"repo at {root} too large to scan for {file_path}")
    chains = claude_mds(start, root)
    if root is None and cwd:
        chains += claude_mds(cwd, repo_root(cwd))
    left = uncovered(families - existing, chains, f"`{file_path}`")
    return block_reason(left, f"`{file_path}`") if left else ""


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
    reason = classify(tool, tool_input, str(obj.get("cwd") or "") or None)
    if reason:
        log(f"BLOCK {tool} {tool_input.get('file_path')}")
        emit_block_stderr(reason)
    allow()


if __name__ == "__main__":
    main()
