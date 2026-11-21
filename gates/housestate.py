"""gates.housestate -- the house-stack STATE check after a Bash call (#1333,
review rounds 3 + 4).

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
registry is UNKNOWN: skipped with a log line):

- baseline = the merge-base with the remote default branch (``origin/HEAD``,
  else ``@{upstream}``, else ``HEAD``; none in a repo without commits), so
  committing a framework in the same call does not hide it (round 4 item 2);
- the package.json files that changed against it (``git diff --name-only`` +
  untracked; hidden dirs, ``SKIP_DIRS`` and paths deeper than ``SCAN_DEPTH``
  left out) are read first, and the baseline's package.json files only when
  one of them names a framework; an unparseable baseline file fails open with
  a log line;
- a framework FAMILY present now but in no baseline package.json, with no
  marker naming it in the repo-root ``CLAUDE.md``, exits 2 with the reason on
  stderr (the PostToolUse channel to the model): `npm uninstall <pkgs>` or
  remove the scaffold dir; committing does not make it allowed. A covered
  family is logged as ALLOW-MARKER.

A cwd outside any repo that is house gets a shallow scan of its own
``package.json`` files (the cwd and its direct subdirs) against an empty
baseline. A git failure fails OPEN with a logged reason. STDLIB ONLY.
"""
import json
import os
import re

from cli_stack_scope import SKIP_DIRS, git_layout
from gates import allow, emit_block_stderr, read_payload
from gates.housestack import (REGISTRY, fail_open, git, house, judged, log, read,
                              repo_package_files, uncovered)

_MENTIONS = re.compile(
    r"\b(?:npm|pnpm|yarn|bun|npx|pnpx|bunx|corepack)\b|create-[\w.-]+|package\.json")


def baseline(top):
    """The commit the state is compared with, or None (no commits)."""
    for ref in ("origin/HEAD", "@{upstream}"):
        out = git(top, ["merge-base", "HEAD", ref])
        if out:
            return out.decode().strip()
    out = git(top, ["rev-parse", "--verify", "-q", "HEAD"])
    return out.decode().strip() if out else None


def changed_files(top, base):
    """{rel: text} of the judged package.json files that differ from `base`
    (all of them without a base), tracked or untracked."""
    if base is None:
        return repo_package_files(top)
    files = repo_package_files(top, ("diff", "--name-only", "-z", base))
    files.update(repo_package_files(top, ("ls-files", "-z", "--others",
                                          "--exclude-standard")))
    return files


def baseline_families(top, base):
    """The families every package.json at `base` carries; an unparseable one
    fails open with a log line."""
    from cli_onboard_stack import package_json_frameworks
    if base is None:
        return set()
    out = git(top, ["ls-tree", "-r", "-z", "--name-only", base])
    if out is None:
        fail_open(f"git ls-tree failed in {top}")
    rels = [r for r in out.decode("utf-8", "replace").split("\0") if r and judged(r)]
    if not rels:
        return set()
    blob = git(top, ["cat-file", "--batch"],
               stdin="".join("%s:%s\n" % (base, r) for r in rels).encode("utf-8"))
    if blob is None:
        fail_open(f"git cat-file failed in {top}")
    found, pos = set(), 0
    for rel in rels:
        nl = blob.index(b"\n", pos)
        header = blob[pos:nl].split()
        pos = nl + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            fams = package_json_frameworks(blob[pos:pos + size].decode("utf-8", "replace"))
            pos += size + 1
            if fams is None:
                fail_open(f"unparseable baseline {rel} in {top}")
            found |= fams
    return found


def shallow_files(cwd):
    """{rel: text} of package.json in a non-repo cwd and its direct subdirs."""
    files = {"package.json": read(os.path.join(cwd, "package.json"))}
    try:
        subdirs = [e.name for e in os.scandir(cwd)
                   if e.is_dir() and not e.name.startswith(".") and e.name not in SKIP_DIRS]
    except OSError:
        subdirs = []
    for name in subdirs:
        files[name + "/package.json"] = read(os.path.join(cwd, name, "package.json"))
    return {rel: text for rel, text in files.items() if text}


def check(command, cwd):
    """Block reason for the state a Bash call left, or ""."""
    from cli_onboard_stack import framework_packages
    if not _MENTIONS.search(command or "") or not cwd or not house(cwd, registry=REGISTRY):
        return ""
    layout = git_layout(cwd)
    top = layout[0] if layout else cwd
    base = baseline(top) if layout else None
    changed = changed_files(top, base) if layout else shallow_files(cwd)
    packages = {rel: framework_packages(text) for rel, text in changed.items()}
    if not any(packages.values()):
        return ""
    at_base = baseline_families(top, base) if layout else set()
    new = {fam for pk in packages.values() for fam in pk} - at_base
    what = f"`{command.strip().splitlines()[0][:80]}`"
    left = uncovered(new, os.path.join(top, "CLAUDE.md"), what) if new else []
    if not left:
        return ""
    files = sorted(rel for rel, pk in packages.items() if set(pk) & set(left))
    pkgs = sorted({n for rel in files for fam in left for n in packages[rel].get(fam, ())})
    return (
        f"🚫 house stack (#1333): after {what}, {', '.join(files)} depend(s) on the JS UI "
        f"framework(s) {', '.join(left)}, absent from the default branch (present since "
        "an earlier change, maybe not this command).\n"
        f"Revert it: `npm uninstall {' '.join(pkgs)}`, or remove the scaffold dir; "
        "committing does not make it allowed. The house stack's web UI is Leptos CSR → "
        "WASM built by Trunk, served by the axum service (~/.claude/rules/house-stack.md). "
        "Or ask the owner for the exception and record `<!-- airuleset:stack-exception "
        "<what> — <#N or GitHub URL> -->` (`<what>` names the technology) in the "
        "repo-root CLAUDE.md; never on your own judgement.")


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
        log(f"BLOCK state {obj.get('cwd')}")
        emit_block_stderr(reason)
    allow()


if __name__ == "__main__":
    main()
