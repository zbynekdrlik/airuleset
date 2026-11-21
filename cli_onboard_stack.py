"""cli_onboard_stack.py — the house-stack pin of a project (#1333, owner
21.11.2026).

The fleet convention (``rules/house-stack.md``): every app is a Rust workspace
with an axum service, a Leptos/WASM web UI, a tray and, where it renders video,
a D3D11 engine. Any other technology needs an owner-approved
``<!-- airuleset:stack-exception <what> — <owner ref> -->`` in the project
CLAUDE.md. This leaf module holds that policy for two consumers:

- ``onboard-project``: ``STACK_SECTION`` is seeded into a NEW project's
  CLAUDE.md; ``audit_drift`` adds ``stack-unpinned`` (no ``## Stack`` section,
  no marker) and ``stack-deviation`` (a JS-framework or hand-written JS web UI
  without the marker) to ``--audit``.
- ``gates.housestack`` (PreToolUse Write|Edit): ``has_stack_exception`` and
  ``package_json_frameworks`` decide whether a ``package.json`` write may add a
  JS UI framework.

Every target read goes through the injected runner (``cli_onboard_exec``) in
ONE call, so ``--host <remote>`` checks the remote tree over ssh, never the
local disk (the #1198 tray-check shape). STDLIB ONLY.
"""

import json
import re
import sys
from pathlib import PurePosixPath

from cli_onboard_exec import _exec

# The npm packages that make a package.json a JS UI-framework app. The meta
# frameworks (next, nuxt, @sveltejs/kit) imply React / Vue / Svelte.
JS_UI_FRAMEWORKS = frozenset({
    "react", "react-dom", "next", "vue", "nuxt", "svelte", "@sveltejs/kit",
    "angular", "@angular/core", "preact", "solid-js", "lit", "alpinejs",
    "htmx.org", "astro", "@builder.io/qwik", "ember-source"})
_DEP_KEYS = ("dependencies", "devDependencies", "peerDependencies",
             "optionalDependencies")

# Hand-written JS counts only inside a web-asset dir (the tray check's set) and
# only past this many lines in total: a PWA service worker or a small helper
# is no app.
WEB_ASSET_DIRS = ("web", "static", "frontend", "www", "public", "ui")
HAND_JS_MIN_LINES = 200
_JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_NOT_HAND_JS = re.compile(
    r"(\.min\.[cm]?[jt]sx?|\.d\.ts|\.config\.[cm]?[jt]s|^sw\.js|"
    r"^service-worker\.js)$")
# Build output, vendored trees, hidden dirs, docs: never the project's own UI.
# dist/ and pkg/ hold Trunk / wasm-pack JS glue of a Leptos UI.
_PRUNE_DIRS = ("target", "node_modules", "vendor", "dist", "pkg", "build",
               ".*", "examples", "benches", "tests", "docs")
_SCAN_DEPTH = "5"
# ONE runner call: NUL <path> NUL <payload>, the payload being the content of a
# package.json or the line count of a JS file.
_EMIT = ('for f; do printf "\\0%s\\0" "$f"; case "$f" in '
         '*/package.json) cat "$f";; *) wc -l < "$f";; esac; done')

# The marker carries `<what> — <owner ref>`. `<` / `>` cannot occur inside, so
# the rule's own template line never counts as an exception, and the match
# stays on one line and cannot run into another comment.
_EXCEPTION_RE = re.compile(
    r"<!--[ \t]*airuleset:stack-exception[ \t]+([^<>\n]{1,400}?)[ \t]*-->")
_EXCEPTION_SEP_RE = re.compile(r"\s*(?:—|–|\s-\s)\s*")
# The owner ref is an issue (`#N`, `owner/repo#N`) or a GitHub URL (#1333 r1).
_OWNER_REF_RE = re.compile(r"#\d+\b|https?://github\.com/\S+")
_STACK_HEADING_RE = re.compile(r"^##[ \t]+Stack\b", re.M)

STACK_SECTION = (
    "## Stack\n\n"
    "House stack (`~/.claude/rules/house-stack.md`, "
    "zbynekdrlik/airuleset#1333): read it before choosing any technology. "
    "Another technology only with an owner-approved "
    "`<!-- airuleset:stack-exception <what> — <owner ref> -->` here.\n\n"
    "| Layer | Technology |\n"
    "|---|---|\n"
    "| Workspace | Rust Cargo workspace, `crates/<prefix>-*` |\n"
    "| Service | axum + tokio, `GET /api/status`, WebSocket |\n"
    "| Web UI | Leptos CSR → WASM via Trunk (PWA, version on the "
    "dashboard) |\n"
    "| Tray | `tray-icon` in `<prefix>-tray` |\n"
    "| Graphics engine | `windows` crate D3D11, Spout / NDI, vblank pacing |\n"
    "| Windows build | dev1 cross-build `x86_64-pc-windows-gnu` |\n"
    "| Install | scheduled tasks, `C:\\ProgramData\\<App>\\logs` |\n\n")


class StackCheckUnverified(Exception):
    """The stack check could not read the target (ssh down, find failed)."""


def has_stack_exception(claude_md):
    """True when the text carries a stack-exception marker that names
    `<what>` and, after a dash, an `<owner ref>` holding a `#N` or a GitHub
    URL."""
    for m in _EXCEPTION_RE.finditer(claude_md or ""):
        parts = _EXCEPTION_SEP_RE.split(m.group(1).strip(), maxsplit=1)
        if (len(parts) == 2 and parts[0].strip()
                and _OWNER_REF_RE.search(parts[1])):
            return True
    return False


def has_stack_section(claude_md):
    return bool(_STACK_HEADING_RE.search(claude_md or ""))


def package_json_frameworks(text):
    """The JS UI frameworks a package.json depends on (any dependency table),
    or None when the text is no JSON object (unreadable — the caller decides)."""
    try:
        doc = json.loads(str(text).lstrip("\ufeff"))
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict):
        return None
    found = set()
    for key in _DEP_KEYS:
        deps = doc.get(key)
        if isinstance(deps, dict):
            for name, spec in deps.items():
                found |= {_alias_target(name, spec)} & JS_UI_FRAMEWORKS
    return found


def _alias_target(name, spec):
    """The real package of a dependency: an npm alias (`"ui": "npm:react@18"`)
    names it in the version spec."""
    spec = str(spec or "").strip()
    if spec.startswith("npm:"):
        real = spec[4:]
        name = ("@" + real[1:].split("@")[0]) if real.startswith("@") else real.split("@")[0]
    return str(name).strip().lower()


def _is_hand_js(rel):
    p = PurePosixPath(rel)
    return (p.suffix in _JS_SUFFIXES and not _NOT_HAND_JS.search(p.name)
            and any(part in WEB_ASSET_DIRS for part in p.parts[:-1]))


def _scan(root, host=None, run=None):
    """[(relative path, payload)] for every package.json and JS file within
    5 levels of `root`, in ONE runner call. find's rc 1 (an unreadable subdir)
    still leaves valid output; any other rc raises StackCheckUnverified."""
    prune = []
    for name in _PRUNE_DIRS:
        prune += (["-o"] if prune else []) + ["-name", name]
    match = ["-name", "package.json"]
    for suffix in _JS_SUFFIXES:
        match += ["-o", "-name", "*" + suffix]
    argv = (["find", "-H", root, "-mindepth", "1", "-maxdepth", _SCAN_DEPTH,
             "(", "-type", "d", "(", *prune, ")", ")", "-prune", "-o",
             "-type", "f", "(", *match, ")",
             "-exec", "sh", "-c", _EMIT, "sh", "{}", "+"])
    r = _exec(argv, host=host, run=run)
    if r.returncode not in (0, 1):
        raise StackCheckUnverified(
            "stack check could not read %s on %s (rc %s: %s)"
            % (root, host or "this box", r.returncode,
               (r.stderr or "").strip()[:200]))
    parts = (r.stdout or "").split("\0")
    prefix = root.rstrip("/") + "/"
    return [(p[len(prefix):] if p.startswith(prefix) else p, payload)
            for p, payload in zip(parts[1::2], parts[2::2])]


def deviation(path, host=None, run=None):
    """None when the project has no JS web UI, else a one-line reason naming
    the framework dependency or the hand-written JS files. Raises
    StackCheckUnverified when the target cannot be read."""
    root = str(path).rstrip("/") or "/"
    frameworks, js_files, js_lines = {}, [], 0
    for rel, payload in _scan(root, host=host, run=run):
        if PurePosixPath(rel).name == "package.json":
            found = package_json_frameworks(payload)
            if found is None:
                print("onboard-project: warning: %s does not parse; skipped "
                      "for the stack check" % rel, file=sys.stderr)
            elif found:
                frameworks[rel] = sorted(found)
        elif _is_hand_js(rel):
            try:
                n = int(payload.strip() or 0)
            except ValueError:
                continue
            js_files.append(rel)
            js_lines += n
    if frameworks:
        rel = sorted(frameworks)[0]
        return "%s depends on %s" % (rel, ", ".join(frameworks[rel]))
    if js_lines >= HAND_JS_MIN_LINES:
        return "hand-written JS web UI: %d lines in %s" % (
            js_lines, ", ".join(sorted(js_files)[:3])
            + (" …" if len(js_files) > 3 else ""))
    return None


def audit_drift(path, host=None, run=None, claude_md=None):
    """The `--audit` rows: `stack-unpinned` (a CLAUDE.md with neither a
    `## Stack` section nor a marker; an absent CLAUDE.md is its own drift),
    `stack-deviation`, or `unverified-stack` when the target could not be read
    (never shown as clean). [] when the project is pinned and on the stack."""
    if has_stack_exception(claude_md):
        return []
    rows = []
    if claude_md is not None and not has_stack_section(claude_md):
        rows.append({"kind": "stack-unpinned", "detail":
                     "CLAUDE.md has no `## Stack` section and no "
                     "`<!-- airuleset:stack-exception <what> — <owner ref> -->`"
                     " (rules/house-stack.md)"})
    try:
        reason = deviation(path, host=host, run=run)
    except StackCheckUnverified as e:
        return rows + [{"kind": "unverified-stack", "detail": str(e)}]
    if reason:
        rows.append({"kind": "stack-deviation", "detail": reason
                     + " — the house stack web UI is Leptos/WASM; another stack "
                     "needs an owner-approved stack-exception marker"})
    return rows
