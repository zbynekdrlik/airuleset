"""gates.housestate -- the house-stack STATE check after a Bash call (#1333,
review rounds 3-5).

Entry for hooks/check-frontend-framework-state.sh (PostToolUse and
PostToolUseFailure, Bash).

Two review rounds bypassed a PreToolUse parser of package-manager commands a
dozen ways (`#` comments, `npm --prefix`, `pnpm -C`, `yarn workspace`,
subshells, `npm exec`, `npm pkg set`, `jq > package.json`, ...): the #1220
lesson, never guard STATE by parsing commands. This check reads the STATE the
command left instead, also when the command failed half-way.

When the command mentions a JS package manager or scaffolder (the adapter's
cheap prefilter on the command field; a false positive only costs one scan)
and the cwd is a house checkout (``cli_stack_scope.stack_for``; an unreadable
registry is UNKNOWN: skipped with a log line), within ONE deadline below the
hook timeout:

- the judged package.json files are listed with ``git ls-files -co
  --exclude-standard -- '*package.json'`` (hidden dirs, ``SKIP_DIRS`` and paths
  deeper than ``SCAN_DEPTH`` left out) and read from the working tree; nothing
  more is read unless one of them names a framework;
- baseline = the merge-base with ``origin/HEAD``, ``origin/main``,
  ``origin/master``, then ``@{upstream}``, then ``HEAD`` (logged as ``BASELINE
  HEAD <reason>``), none without commits (round 5 item 3): committing a
  framework in the same call does not hide it;
- each listed file is compared with its ``<base>:<path>`` blob (no ``git
  diff`` re-hash); a framework FAMILY in a changed file and in no baseline blob,
  with no marker naming it in the repo-root ``CLAUDE.md``, exits 2 with the
  reason on stderr: `npm uninstall <pkgs>` or remove the scaffold dir;
  committing does not make it allowed. A covered family is logged as
  ALLOW-MARKER; an unparseable changed file is logged; an unparseable baseline
  blob fails open with a log line.

A git failure (a ``.git`` in the cwd or an ancestor, but git cannot answer)
fails OPEN with a log line. Only a cwd with no ``.git`` anywhere above gets the
shallow scan of its own ``package.json`` files: the cwd and its direct
subdirs, never a subdir that is its own git checkout (round 5 item 1: dev1's
``~/devel`` holds platform repos). STDLIB ONLY.
"""
import json
import os
import re

from cli_stack_scope import SKIP_DIRS, git_layout, has_git_ancestor, start_deadline
from gates import allow, emit_block_stderr, read_payload
from gates.housestack import (REGISTRY, fail_open, git, house, log, read,
                              repo_package_files, uncovered)

_MENTIONS = re.compile(
    r"\b(?:npm|pnpm|yarn|bun|npx|pnpx|bunx|corepack)\b|create-[\w.-]+|package\.json")
_BASELINE_REFS = ("origin/HEAD", "origin/main", "origin/master", "@{upstream}")


def baseline(top):
    """(commit or None, label) the state is compared with."""
    for ref in _BASELINE_REFS:
        out = git(top, ["merge-base", "HEAD", ref])
        if out:
            sha = out.decode().strip()
            log(f"BASELINE {ref} {sha[:12]} {top}")
            return sha, ref
    out = git(top, ["rev-parse", "--verify", "-q", "HEAD"])
    if out:
        log(f"BASELINE HEAD no {', '.join(_BASELINE_REFS)} in {top}")
        return out.decode().strip(), "HEAD"
    log(f"BASELINE none: no commits in {top}")
    return None, "a repo without commits"


def baseline_blobs(top, base, rels):
    """{rel: text} of `<base>:<rel>` for the listed files (missing ones left
    out)."""
    if base is None or not rels:
        return {}
    blob = git(top, ["cat-file", "--batch"],
               stdin="".join("%s:%s\n" % (base, r) for r in rels).encode("utf-8"))
    if blob is None:
        fail_open(f"git cat-file failed in {top}")
    out, pos = {}, 0
    for rel in rels:
        nl = blob.index(b"\n", pos)
        header = blob[pos:nl].split()
        pos = nl + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            out[rel] = blob[pos:pos + size].decode("utf-8", "replace")
            pos += size + 1
    return out


def shallow_files(cwd):
    """{rel: text} of package.json in a non-git cwd and its direct subdirs; a
    subdir with a `.git` entry is its own repo with its own scope: skipped."""
    files = {"package.json": read(os.path.join(cwd, "package.json"))}
    try:
        subdirs = [e.name for e in os.scandir(cwd)
                   if e.is_dir() and not e.name.startswith(".") and e.name not in SKIP_DIRS
                   and not os.path.lexists(os.path.join(cwd, e.name, ".git"))]
    except OSError:
        subdirs = []
    for name in subdirs:
        files[name + "/package.json"] = read(os.path.join(cwd, name, "package.json"))
    return {rel: text for rel, text in files.items() if text}


def check(command, cwd):
    """Block reason for the state a Bash call left, or ""."""
    from cli_onboard_stack import framework_packages, package_json_frameworks
    if not _MENTIONS.search(command or "") or not cwd or not house(cwd, registry=REGISTRY):
        return ""
    layout = git_layout(cwd)
    if layout is None and has_git_ancestor(cwd):
        fail_open(f"git failed for {cwd}")
    top = layout[0] if layout else cwd
    files = repo_package_files(top) if layout else shallow_files(cwd)
    if not any(framework_packages(text) for text in files.values()):
        return ""
    base, label = baseline(top) if layout else (None, "a dir without git")
    at_base = baseline_blobs(top, base, sorted(files))
    base_fams = set()
    for rel, text in at_base.items():
        fams = package_json_frameworks(text)
        if fams is None:
            fail_open(f"unparseable baseline {rel} in {top}")
        base_fams |= fams
    packages = {}
    for rel, text in files.items():
        if at_base.get(rel) == text:
            continue
        if package_json_frameworks(text) is None:
            log(f"SKIP unparseable changed {rel} in {top}")
            continue
        packages[rel] = framework_packages(text)
    new = {fam for pk in packages.values() for fam in pk} - base_fams
    what = f"`{command.strip().splitlines()[0][:80]}`"
    left = uncovered(new, os.path.join(top, "CLAUDE.md"), what) if new else []
    if not left:
        return ""
    changed = sorted(rel for rel, pk in packages.items() if set(pk) & set(left))
    pkgs = sorted({n for rel in changed for fam in left for n in packages[rel].get(fam, ())})
    return (
        f"🚫 house stack (#1333): after {what}, {', '.join(changed)} depend(s) on the JS UI "
        f"framework(s) {', '.join(left)}, absent from {label} (present since an earlier "
        "change, maybe not this command).\n"
        f"Revert it: `npm uninstall {' '.join(pkgs)}`, or remove the scaffold dir; "
        "committing does not make it allowed. The house stack's web UI is Leptos CSR → "
        "WASM built by Trunk, served by the axum service (~/.claude/rules/house-stack.md). "
        "Or ask the owner for the exception and record `<!-- airuleset:stack-exception "
        "<what> — <#N or GitHub URL> -->` (`<what>` names the technology) in the "
        "repo-root CLAUDE.md; never on your own judgement.")


def main():
    start_deadline()
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
        log(f"BLOCK state {obj.get('cwd')}")
        emit_block_stderr(reason)
    allow()


if __name__ == "__main__":
    main()
