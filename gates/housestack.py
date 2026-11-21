"""gates.housestack -- the house-stack frontend-framework guard (#1333).

Entry for hooks/block-frontend-framework.sh (PreToolUse Write|Edit).

REFUSES a ``package.json`` Write or Edit that ADDS a JS UI framework
(``cli_onboard_stack.JS_UI_FRAMEWORKS``: react / vue / svelte / angular /
preact / solid-js and their meta frameworks) to a project whose CLAUDE.md
carries no owner-approved ``<!-- airuleset:stack-exception <what> — <owner
ref> -->``. The house stack's web UI is Leptos/WASM (``rules/house-stack.md``);
the owner, 21.11.2026, kept finding new projects "postavane na random adhoc
zvolenych technologiach". This is the rule-intake gate's step 1: the one
mechanically checkable moment a session leaves the stack.

Which CLAUDE.md counts: every ``CLAUDE.md`` and ``.claude/CLAUDE.md`` from the
written file's directory up to the git root (the first dir holding ``.git``);
without a git root, up to the filesystem root. The marker predicate is the
one ``onboard-project --audit`` uses (``cli_onboard_stack.has_stack_exception``).

Only an ADDED framework blocks: the frameworks of the content after the
write/edit minus those already in the file, so an existing app's unrelated
edits never wedge. Vendored / fixture trees (node_modules, vendor, fixtures,
__fixtures__, testdata) are never judged.

FAIL-OPEN with a logged reason (the sibling Write|Edit gates' contract): an
unreadable payload, an unparseable package.json, or an Edit whose file cannot
be read is allowed, the reason printed on stderr and appended to
``~/.claude/stack-gate.log``. Exit 2 = block, reason on STDERR only (the adapter
runs this module with ``1>&2``). STDLIB ONLY.
"""
import json
import os
import time

from gates import allow, emit_block_stderr, read_payload

GATE_LOG = "stack-gate.log"
_SKIP_DIRS = frozenset({"node_modules", "vendor", "fixtures", "__fixtures__",
                        "testdata"})


def _log(line):
    try:
        d = os.path.join(os.path.expanduser("~"), ".claude")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, GATE_LOG), "a", encoding="utf-8") as f:
            f.write(f"{int(time.time())} {line}\n")
    except OSError:
        pass  # airuleset:script-ok a gate must never fail on its own audit log


def _fail_open(reason):
    _log("FAIL-OPEN " + reason)
    print(f"stack gate: {reason} — allowing (fail-open).", flush=True)
    allow()


def _read(path):
    """File text, "" when the file does not exist, None when it cannot be
    read."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def project_claude_mds(file_path):
    """CLAUDE.md and .claude/CLAUDE.md paths from the file's dir up to the git
    root (inclusive), nearest first."""
    out = []
    d = os.path.dirname(os.path.abspath(file_path))
    while True:
        out += [os.path.join(d, "CLAUDE.md"),
                os.path.join(d, ".claude", "CLAUDE.md")]
        parent = os.path.dirname(d)
        if os.path.exists(os.path.join(d, ".git")) or parent == d:
            return out
        d = parent


def _has_exception(file_path):
    from cli_onboard_stack import has_stack_exception
    for cm in project_claude_mds(file_path):
        text = _read(cm)
        if text and has_stack_exception(text):
            return True
    return False


def _after_edit(before, tool_input):
    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or old not in before:
        return before  # Claude Code refuses such an edit itself
    if tool_input.get("replace_all") is True:
        return before.replace(old, new)
    return before.replace(old, new, 1)


def classify(tool_name, tool_input):
    """``(blocked, reason)`` for one Write/Edit. Fail-open cases exit through
    ``_fail_open`` (never return)."""
    file_path = str(tool_input.get("file_path") or "")
    parts = file_path.replace("\\", "/").split("/")
    if parts[-1] != "package.json" or _SKIP_DIRS.intersection(parts[:-1]):
        return False, ""
    from cli_onboard_stack import package_json_frameworks
    before = _read(file_path)
    if before is None:
        _fail_open(f"cannot read the existing {file_path}")
    if tool_name == "Edit":
        if not before:
            _fail_open(f"cannot read {file_path} to judge the edit")
        after = _after_edit(before, tool_input)
    else:
        after = tool_input.get("content")
        if not isinstance(after, str):
            _fail_open(f"no content in the Write of {file_path}")
    after_fw = package_json_frameworks(after)
    if after_fw is None:
        _fail_open(f"unparseable package.json content for {file_path}")
    before_fw = package_json_frameworks(before) if before else set()
    added = sorted(after_fw - (before_fw or set()))
    if not added or _has_exception(file_path):
        return False, ""
    reason = (
        f"🚫 BLOCKED: `{file_path}` adds the JS UI framework(s) "
        f"{', '.join(added)} to a project without a stack exception (#1333).\n"
        "The house stack is the default for every app: web UI = Leptos CSR → "
        "WASM built by Trunk, served by the axum service — read "
        "~/.claude/rules/house-stack.md before choosing.\n"
        "Another stack only as an OWNER-approved exception: ask the owner, then "
        "record `<!-- airuleset:stack-exception <what> — <owner ref> -->` in "
        "the project CLAUDE.md (never on your own judgement). A clearly better "
        "option than the house stack is proposed as a stack upgrade (the rule's "
        "\"Stack evolution\" section), never used silently."
    )
    return True, reason


def main():
    payload = read_payload()
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        obj = None
    tool_input = obj.get("tool_input") if isinstance(obj, dict) else None
    if not isinstance(tool_input, dict):
        _fail_open("unreadable payload")
    blocked, reason = classify(str(obj.get("tool_name") or ""), tool_input)
    if blocked:
        _log(f"BLOCK path={tool_input.get('file_path')}")
        emit_block_stderr(reason)
    allow()


if __name__ == "__main__":
    main()
