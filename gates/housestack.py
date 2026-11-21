"""gates.housestack -- the house-stack frontend-framework guard (#1333).

Entry for hooks/block-frontend-framework.sh (PreToolUse Write|Edit|Bash).

REFUSES bringing a JS UI framework (``cli_onboard_stack.JS_UI_FRAMEWORKS``:
react / vue / svelte / angular / preact / solid-js / lit / alpinejs / htmx.org /
astro / qwik / ember and the meta frameworks) that is NEW to the git repo into a
house project without an owner-approved
``<!-- airuleset:stack-exception <what> — <#N or GitHub URL> -->``. The house
stack's web UI is Leptos/WASM (``rules/house-stack.md``); the owner, 21.11.2026,
kept finding projects "postavane na random adhoc zvolenych technologiach".

What is judged (review round 1, ROZHODNUTÉ 2 + 3):
- a ``package.json`` Write, or Edit (an empty ``old_string`` is a whole new
  file): the frameworks of the content after the change minus those already in
  the file;
- a Bash ``npm|pnpm|yarn|bun create|init|add|i|install`` / ``npx|pnpx|bunx
  create-*`` / ``pnpm|yarn dlx`` naming a framework (``npm:`` aliases,
  ``--template react-ts``, ``create-react-app``), and a heredoc into a
  ``package.json``.
A framework another ``package.json`` of the repo already depends on is not new
(odoo-erp's ``frontline/`` shape); hidden dirs and ``cli_stack_scope.SKIP_DIRS``
never count, and nothing under a skipped dir is judged. A registry project
whose ``stack`` is not ``house`` is never judged; an unregistered repo is.

Which CLAUDE.md counts: every ``CLAUDE.md`` and ``.claude/CLAUDE.md`` from the
judged dir up to the git root. A marker-allowed change is logged.

FAIL-OPEN with a logged reason (the sibling Write|Edit gates' contract), in
``~/.claude/stack-gate.log``: an unreadable payload, an unreadable or
unparseable existing package.json, unparseable new content, an unreadable
CLAUDE.md, a repo too large to scan. Exit 2 = block, reason on STDERR only (the
adapter runs this module with ``1>&2``). STDLIB ONLY.
"""
import json
import os
import re
import shlex
import time

from cli_stack_scope import HOUSE, REGISTRY_FILENAME, SKIP_DIRS, git_root, stack_for
from gates import allow, emit_block_stderr, read_payload

GATE_LOG = "stack-gate.log"
_REGISTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         REGISTRY_FILENAME)
_SCAN_MAX_DIRS = 20000
_SCAN_MAX_DEPTH = 8

_PMS = {"npm", "pnpm", "yarn", "bun"}
_PM_VERBS = {"create", "init", "add", "i", "install"}
_RUNNERS = {"npx", "pnpx", "bunx"}
_CREATE_VERBS = {"create", "init"}
# a scaffolder / template name -> the framework it brings
_CREATE_ALIASES = {
    "react": "react", "react-app": "react", "remix": "react", "next": "next",
    "next-app": "next", "vue": "vue", "nuxt": "nuxt", "nuxt-app": "nuxt",
    "svelte": "svelte", "sveltekit": "@sveltejs/kit", "solid": "solid-js",
    "qwik": "@builder.io/qwik", "astro": "astro", "preact": "preact", "lit": "lit",
    "ember-app": "ember-source", "angular": "@angular/core",
    "@angular/cli": "@angular/core"}
_SEPARATORS = {"&&", "||", ";", "|", "&", "\n"}
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
_PKG_TARGET_RE = re.compile(
    r"(?:>>?|\btee(?:\s+-a)?)\s*(['\"]?)([^\s'\"<>|;&]*package\.json)\1")


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
    read (a dir, no permission, not UTF-8)."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _skipped(path):
    return bool(set(str(path).replace("\\", "/").split("/")[:-1]) & set(SKIP_DIRS))


def project_claude_mds(start, root):
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


def repo_frameworks(root, exclude):
    """The frameworks every other package.json of the repo depends on, or None
    when the repo is too large to scan. Hidden and skipped dirs never count."""
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
                found |= package_json_frameworks(_read(p) or "") or set()
    return found


def judge(candidates, start, exclude, what):
    """Block reason for `candidates` brought in at `start`, or "" (allowed).
    Fail-open cases exit through `_fail_open`."""
    if not candidates or stack_for(start, _REGISTRY)[1] != HOUSE:
        return ""
    root = git_root(start)
    existing = repo_frameworks(root, exclude) if root else set()
    if existing is None:
        _fail_open(f"repo at {root} too large to scan for {what}")
    new = sorted(candidates - existing)
    if not new:
        return ""
    from cli_onboard_stack import has_stack_exception
    for cm in project_claude_mds(start, root):
        text = _read(cm)
        if text is None:
            _fail_open(f"cannot read {cm} for {what}")
        if text and has_stack_exception(text):
            _log(f"ALLOW-MARKER {what} frameworks={','.join(new)} claude_md={cm}")
            return ""
    return (
        f"🚫 BLOCKED: {what} adds the JS UI framework(s) {', '.join(new)}, new to "
        "this repo, without a stack exception (#1333).\n"
        "The house stack is the default for every NEW app: web UI = Leptos CSR → "
        "WASM built by Trunk, served by the axum service — read "
        "~/.claude/rules/house-stack.md before choosing.\n"
        "Another stack only as an OWNER-approved exception: ask the owner, then "
        "record `<!-- airuleset:stack-exception <what> — <#N or GitHub URL> -->` "
        "in the project CLAUDE.md (never on your own judgement). A clearly better "
        "option than the house stack is proposed as a stack upgrade (the rule's "
        "\"Stack evolution\" section), never used silently.")


def _after_edit(before, tool_input):
    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str) or old not in before:
        return before  # Claude Code refuses such an edit itself
    if tool_input.get("replace_all") is True:
        return before.replace(old, new)
    return before.replace(old, new, 1)


def classify_file(tool_name, tool_input):
    """Block reason for one package.json Write/Edit, or ""."""
    from cli_onboard_stack import package_json_frameworks
    file_path = str(tool_input.get("file_path") or "")
    if os.path.basename(file_path) != "package.json" or _skipped(file_path):
        return ""
    before = _read(file_path)
    if before is None:
        _fail_open(f"cannot read the existing {file_path}")
    if tool_name == "Edit":
        if tool_input.get("old_string") == "":
            after = tool_input.get("new_string")
        elif not before:
            _fail_open(f"cannot read {file_path} to judge the edit")
        else:
            after = _after_edit(before, tool_input)
    else:
        after = tool_input.get("content")
    if not isinstance(after, str):
        _fail_open(f"no content in the {tool_name} of {file_path}")
    after_fw = package_json_frameworks(after)
    if after_fw is None:
        _fail_open(f"unparseable package.json content for {file_path}")
    before_fw = package_json_frameworks(before) if before.strip() else set()
    if before_fw is None:
        _fail_open(f"unparseable existing {file_path}")
    return judge(after_fw - before_fw, os.path.dirname(os.path.abspath(file_path)),
                 file_path, f"`{file_path}`")


def _token_framework(tok, creating):
    """The framework one command token names, or None."""
    from cli_onboard_stack import JS_UI_FRAMEWORKS
    t = tok.strip().lower()
    if t.startswith("--template="):
        t = t.split("=", 1)[1]
    if "@npm:" in t:
        t = t.split("@npm:", 1)[1]
    elif t.startswith("npm:"):
        t = t[4:]
    name = ("@" + t[1:].split("@")[0]) if t.startswith("@") else t.split("@")[0]
    if name in JS_UI_FRAMEWORKS:
        return name
    if not creating:
        return None
    base = name[len("create-"):] if name.startswith("create-") else name
    base = re.sub(r"(?:-swc)?(?:-ts)?$", "", base)
    return _CREATE_ALIASES.get(base) or _CREATE_ALIASES.get(name)


def _segments(cmd):
    cmd = cmd.replace("\n", " ; ")
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = cmd.split()
    seg = []
    for tok in tokens:
        if tok in _SEPARATORS or set(tok) <= set(";&|\n"):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def bash_frameworks(cmd):
    """Frameworks a package-manager / scaffolder command names (heredoc bodies
    are not commands)."""
    found = set()
    for seg in _segments(_strip_heredoc_bodies(cmd)):
        heads = [os.path.basename(t) for t in seg]
        for i, head in enumerate(heads[:-1]):
            nxt = heads[i + 1]
            if head in _PMS and nxt in _PM_VERBS:
                creating, args = nxt in _CREATE_VERBS, seg[i + 2:]
            elif head in _RUNNERS or (head in ("pnpm", "yarn") and nxt == "dlx"):
                creating, args = True, seg[i + 1:] if head in _RUNNERS else seg[i + 2:]
            else:
                continue
            for tok in args:
                fw = _token_framework(tok, creating)
                if fw:
                    found.add(fw)
            break
    return found


def _strip_heredoc_bodies(cmd):
    out, pending = [], None
    for line in cmd.split("\n"):
        if pending is not None:
            if line.strip() == pending:
                pending = None
            continue
        out.append(line)
        m = _HEREDOC_RE.search(line)
        if m:
            pending = m.group(2)
    return "\n".join(out)


def heredoc_package_writes(cmd):
    """[(target path, body)] for every heredoc written into a package.json."""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        m = _HEREDOC_RE.search(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() != m.group(2):
            j += 1
        target = _PKG_TARGET_RE.search(lines[i])
        if target:
            out.append((target.group(2), "\n".join(lines[i + 1:j])))
        i = j + 1
    return out


def classify_bash(command, cwd):
    """Block reason for one Bash command, or ""."""
    from cli_onboard_stack import package_json_frameworks
    for target, body in heredoc_package_writes(command):
        path = target if os.path.isabs(target) else os.path.join(cwd, target)
        fw = package_json_frameworks(body)
        if fw and not _skipped(path):
            reason = judge(fw, os.path.dirname(os.path.abspath(path)), path,
                           f"a heredoc into `{path}`")
            if reason:
                return reason
    fw = bash_frameworks(command)
    return judge(fw, cwd, None, f"`{command.strip().splitlines()[0][:120]}`") if fw else ""


def main():
    payload = read_payload()
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError):
        obj = None
    tool_input = obj.get("tool_input") if isinstance(obj, dict) else None
    if not isinstance(tool_input, dict):
        _fail_open("unreadable payload")
    tool = str(obj.get("tool_name") or "")
    if tool == "Bash":
        try:
            cwd = str(obj.get("cwd") or os.getcwd())
        except OSError:
            _fail_open("no working directory for the Bash command")
        reason = classify_bash(str(tool_input.get("command") or ""), cwd)
    else:
        reason = classify_file(tool, tool_input)
    if reason:
        _log(f"BLOCK {tool} {tool_input.get('file_path') or ''}".rstrip())
        emit_block_stderr(reason)
    allow()


if __name__ == "__main__":
    main()
