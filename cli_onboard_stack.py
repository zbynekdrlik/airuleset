"""cli_onboard_stack.py — the house-stack pin of a project (#1333, owner
21.11.2026; review rounds 1 + 3).

The fleet convention (``rules/house-stack.md``): every NEW app is a Rust
workspace with an axum service, a Leptos/WASM web UI, a tray and, where it
renders video, a D3D11 engine. Any other technology in a house project needs an
owner-approved ``<!-- airuleset:stack-exception <what> — <#N or URL> -->`` in
the project CLAUDE.md, ONE per technology. This leaf module holds that policy:

- ``onboard-project``: ``STACK_SECTION`` (GENERATED from the rule's table rows)
  is seeded into a NEW house-candidate project's CLAUDE.md (``seed_section``);
  ``audit_drift`` adds ``stack-unpinned`` (a registry row with no known
  ``stack``) and, for ``house`` rows only, one ``stack-deviation`` row per
  technology without its marker: a JS-framework family, a template / UI crate,
  a hand-written JS web UI (the inline ``<script>`` of a page plus the JS a page
  serves; never in a ``Trunk.toml`` dir, a leptos / wasm-bindgen crate, or
  wasm-bindgen / Trunk output; never a gitignored path), other technology in
  the ``## Stack`` table's Technology cells; every marker and the gate's
  ALLOW-MARKER lines come back as notes (``NOTE_KINDS``) that never count as
  drift.
- ``gates.housestack`` / ``gates.housestate``: ``package_json_frameworks``
  (families) and ``exception_covers`` (one marker, one technology).

Every target read goes through the injected runner (``cli_onboard_exec``) in
ONE call, so ``--host <remote>`` checks the remote tree over ssh, never the
local disk (the #1198 tray-check shape). STDLIB ONLY.
"""

import json
import os
import posixpath
import re
import sys
import tomllib
from pathlib import Path, PurePosixPath

from cli_onboard_exec import _exec, _read_file, _resolve_remote_path
from cli_onboard_tray import _deps as _cargo_deps
from cli_stack_scope import (HOUSE, REGISTRY_FILENAME, SCAN_DEPTH, SKIP_DIRS, STACK_IDS,
                             load_rows)

# The npm packages that make a package.json a JS UI-framework app, each mapped
# to its FAMILY (round 3 I): a meta framework or a renderer is its base
# framework, so react-dom next to react, or next where react already is, is
# nothing new. Families are what the gates, the audit and the markers compare.
FRAMEWORK_FAMILY = {
    "react": "react", "react-dom": "react", "next": "react", "vue": "vue",
    "nuxt": "vue", "svelte": "svelte", "@sveltejs/kit": "svelte",
    "angular": "angular", "@angular/core": "angular", "preact": "preact",
    "solid-js": "solid", "lit": "lit", "alpinejs": "alpine", "htmx.org": "htmx",
    "astro": "astro", "@builder.io/qwik": "qwik", "ember-source": "ember"}
JS_UI_FRAMEWORKS = frozenset(FRAMEWORK_FAMILY)
# How a marker's `<what>` may name a technology (round 3 E: one marker per
# technology). A word not listed names itself (askama, fastapi, electron, ...).
_TECH_ALIASES = dict(FRAMEWORK_FAMILY, **{
    "reactjs": "react", "react.js": "react", "next.js": "react", "nextjs": "react",
    "vuejs": "vue", "vue.js": "vue", "nuxt.js": "vue", "nuxtjs": "vue",
    "sveltekit": "svelte", "angularjs": "angular", "solid": "solid",
    "solidjs": "solid", "alpine": "alpine", "alpine.js": "alpine", "htmx": "htmx",
    "qwik": "qwik", "ember": "ember", "ember.js": "ember", "emberjs": "ember",
    "js": "javascript", "javascript": "javascript"})
_DEP_KEYS = ("dependencies", "devDependencies", "peerDependencies",
             "optionalDependencies")

# A hand-written JS web UI = the inline <script> of every .html page plus the JS
# a page SERVES (`<script src>`, an import in its inline script, and the modules
# those import; audit precision b, 21.11.2026: tooling scripts are no web UI),
# past this many lines in total: a PWA service worker or a small helper is no
# app. Exempt: a Trunk.toml dir, a leptos / wasm-bindgen crate, wasm-bindgen /
# Trunk OUTPUT (`_wasm_output`, audit precision a), the (non-root) dir of a
# JS-framework package.json (that app is already its own finding), a Bitfocus
# Companion module (a fleet platform, round 5 item 4), and the files a
# VENDORED.md lists (round 4 item 6).
COMPANION_MODULE_DEP = "@companion-module/base"
HAND_JS_MIN_LINES = 200
_WASM_CRATES = ("leptos", "wasm-bindgen")
# Rust crates that render HTML or a UI, or serve HTTP, outside the house stack
# (round 3 C, round 4 item 6).
FORBIDDEN_CRATES = ("askama", "tera", "maud", "yew", "dioxus", "sycamore",
                    "actix-web", "warp", "rocket", "egui", "iced", "slint")
_JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_NOT_HAND_JS = re.compile(
    r"(\.min\.[cm]?[jt]sx?|\.d\.ts|\.config\.[cm]?[jt]s|^sw\.js|"
    r"^service-worker\.js|\.(?:test|spec)\.[cm]?[jt]sx?)$")
# Build output, vendored trees, tests, hidden dirs, docs, generated test and
# coverage reports: never the project's own UI. The trigger/gate SKIP_DIRS plus
# dist/pkg/build (Trunk / wasm-pack JS glue of a Leptos UI).
_PRUNE_DIRS = tuple(sorted(set(SKIP_DIRS) | {
    "pkg", "build", ".*", "examples", "benches", "docs", "playwright-report",
    "test-results", "coverage", ".nyc_output"}))
# wasm-bindgen / Trunk output names (audit precision a): Trunk hashes a bundle
# as `<name>-<16 hex>.js` + `<name>-<16 hex>_bg.wasm`.
_TRUNK_HASH_JS = re.compile(r"-[0-9a-f]{16}\.js$")
_TRUNK_HASH_WASM = re.compile(r"-[0-9a-f]{16}_bg\.wasm$")
_SCAN_DEPTH = str(SCAN_DEPTH)   # the gates' depth (round 4 item 6)
# The payload of an .html page (h=1) or a JS file (h=0): `n <lines>` (a page:
# its inline <script> lines, a `src=` script and a one-line one count nothing;
# a JS file: all its lines), `src <ref>` per `<script src>`, `imp <specifier>`
# per quoted import / `from` (an inline script, a module). POSIX awk (mawk on
# dev1 / dev2, gawk on the controller), passed as an argv: no shell quoting.
# Linear in the line length: `split` once per line, never a substr loop over
# the rest of a line (quadratic on a one-line bundle).
_WEB_AWK = r"""
function grab(s,   part, m, i, c, k) {
  m = split(s, part, /(from|import)[ \t]*[(]?[ \t]*/)
  for (i = 2; i <= m; i++) {
    c = substr(part[i], 1, 1)
    if (c != "\"" && c != "'") continue
    k = index(substr(part[i], 2), c)
    if (k > 1) print "imp " substr(part[i], 2, k - 1)
  }
}
h && s { p = index(tolower($0), "</script>")
         if (p) { grab(substr($0, 1, p - 1)); s = 0 } else { n++; grab($0) }
         next }
h { m = split($0, seg, /<[sS][cC][rR][iI][pP][tT]/)
    for (i = 2; i <= m; i++) {
      e = index(seg[i], ">"); q = index(tolower(e ? substr(seg[i], 1, e) : seg[i]), "src=")
      if (q) {
        v = substr(seg[i], q + 4); c = substr(v, 1, 1)
        if (c == "\"" || c == "'") { v = substr(v, 2); k = index(v, c) }
        else k = match(v, /[ \t>]/)
        if (k) v = substr(v, 1, k - 1)
        print "src " v
      }
      if (!e) break
      if (q) continue
      rest = substr(seg[i], e + 1); k = index(tolower(rest), "</script>")
      if (k) grab(substr(rest, 1, k - 1))
      else { grab(rest); if (i == m) s = 1 }
    }
    next }
{ grab($0) }
END { print "n " (h ? n : NR) }
"""
# NUL <path> NUL <payload>: the content of a package.json / Cargo.toml /
# VENDORED.md, nothing for a Trunk.toml / .wasm / minified file, `_WEB_AWK`'s
# lines for a page or a JS file. $1 is `_WEB_AWK`.
_EMIT = ('a=$1; shift; for f; do printf "\\0%s\\0" "$f"; case "$f" in '
         '*/package.json|*/Cargo.toml|*/VENDORED.md) cat "$f";; '
         '*/Trunk.toml|*.wasm|*.min.*) ;; *.html|*.htm) awk -v h=1 "$a" "$f";; '
         '*) awk -v h=0 "$a" "$f";; esac; done')
# ONE runner call: `sh -c _SCAN_SH sh <root> _EMIT _WEB_AWK <find expression>`.
# Inside a git work tree it then lists git's untracked IGNORED paths as the
# payload of a `<root>/:gitignored` record: a pinned release bundle or other
# ignored output is never the project's code (audit precision a; the gates
# judge `git ls-files -co --exclude-standard` the same way). The find's rc is
# the call's rc; no work tree or a git failure lists nothing.
_IGNORED = ":gitignored"
_SCAN_SH = ('r=$1; e=$2; a=$3; shift 3; find -H "$r" "$@" -exec sh -c "$e" sh "$a" {} +; '
            'rc=$?; if git -C "$r" rev-parse --is-inside-work-tree >/dev/null 2>&1; then '
            'printf "\\0%s\\0" "$r/' + _IGNORED + '"; git -C "$r" ls-files -z -o -i '
            '--exclude-standard --directory | tr "\\0" "\\n"; fi; exit $rc')
# Non-house technology a house project's `## Stack` table may not name in its
# Technology cells without the marker (ROZHODNUTÉ 7, round 3 D).
_NON_HOUSE_TECH_RE = re.compile(
    r"(?<![\w.-])(react|vue(?:\.js)?|svelte(?:kit)?|angular|preact|solid(?:-?js)?|"
    r"lit|alpine\.?js|htmx|astro|qwik|ember(?:\.js)?|next\.js|nuxt|electron|yew|"
    r"dioxus|sycamore|actix(?:-web)?|rocket|warp|django|flask|fastapi|"
    r"express(?:\.js|js)|node\.?js|deno|opengl|vulkan|qt|wpf|winforms|"
    r"asp\.net|\.net)(?![\w-])", re.I)
# A negation drops only the token right after it ("React, no SSR" still names
# React; "No browser/Electron/web canvas" drops Electron) (round 4 item 6).
_NEGATED_RE = re.compile(r"\b(?:no|not|never|without|nie|bez|žiadn\w*)\s+[^\s,;.()]+",
                         re.I)
NOTE_KINDS = ("stack-exception", "stack-exception-use")
GATE_LOG = "~/.claude/stack-gate.log"

# The marker carries `<what> — <owner ref>`. `<` / `>` cannot occur inside, so
# the rule's own template line never counts as an exception, and the match
# stays on one line and cannot run into another comment.
_EXCEPTION_RE = re.compile(
    r"<!--[ \t]*airuleset:stack-exception[ \t]+([^<>\n]{1,400}?)[ \t]*-->")
_EXCEPTION_SEP_RE = re.compile(r"\s*(?:—|–|\s-\s)\s*")
# The owner ref is an issue (`#N`, `owner/repo#N`) or a GitHub URL (#1333 r1).
_OWNER_REF_RE = re.compile(r"#\d+\b|https?://github\.com/\S+")
_STACK_HEADING_RE = re.compile(r"^##[ \t]+Stack\b", re.M)
_RULE = Path(__file__).resolve().parent / "rules" / "house-stack.md"


def _table_rows(text):
    """[[cell, ...]] of the markdown table rows in `text` (header first, the
    `|---|` separator dropped)."""
    rows = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("|") and not re.fullmatch(r"\|[\s:|-]*\|?", s):
            rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows


def _seed():
    """The `## Stack` seed, its table GENERATED from the rule's rows (Layer |
    Applies when; round 3 H); every Technology cell points at the rule, so
    Stack evolution never leaves a frozen copy in a project (round 4 item 7)."""
    try:
        rows = _table_rows(_RULE.read_text(encoding="utf-8"))[1:]
    except OSError:
        rows = []
    lines = ["## Stack", "",
             "House stack (`~/.claude/rules/house-stack.md`, zbynekdrlik/airuleset#1333): "
             "read it before choosing any technology. Another technology only with "
             "an owner-approved `<!-- airuleset:stack-exception <what> — <owner ref> -->` "
             "here, one per technology.", ""]
    if rows:
        lines += ["| Layer | Applies when | Technology |", "|---|---|---|"]
        lines += ["| %s | %s | the current row of `~/.claude/rules/house-stack.md` |"
                  % tuple(r[:2])
                  for r in rows if len(r) >= 3]
    return "\n".join(lines) + "\n\n"


STACK_SECTION = _seed()


class StackCheckUnverified(Exception):
    """The stack check could not read the target (ssh down, find failed)."""


def stack_exceptions(claude_md):
    """[(what, owner ref)] of every valid stack-exception marker: `<what>`,
    a dash, and an `<owner ref>` holding a `#N` or a GitHub URL."""
    out = []
    for m in _EXCEPTION_RE.finditer(claude_md or ""):
        parts = _EXCEPTION_SEP_RE.split(m.group(1).strip(), maxsplit=1)
        if (len(parts) == 2 and parts[0].strip()
                and _OWNER_REF_RE.search(parts[1])):
            out.append((parts[0].strip(), parts[1].strip()))
    return out


def has_stack_exception(claude_md):
    return bool(stack_exceptions(claude_md))


def tech_key(word):
    """The technology a word names, for marker matching (`Next.js` -> react)."""
    w = str(word).strip().strip(".,;:()[]'\"`").lower()
    return _TECH_ALIASES.get(w, w)


def exception_covers(claude_md, tech):
    """True when a valid marker's `<what>` names `tech` (a family, a crate,
    `javascript`, a technology word): one marker covers one technology."""
    want = tech_key(tech)
    for what, _ref in stack_exceptions(claude_md):
        if any(tech_key(w) == want for w in re.split(r"[\s,;]+", what)):
            return True
    return False


def has_stack_section(claude_md):
    return bool(_STACK_HEADING_RE.search(claude_md or ""))


def seed_section(stack):
    """The `## Stack` seed for a NEW project's CLAUDE.md: a registry row with
    `stack` house, or, with no registry stack, a house candidate (Rust, or
    nothing detected yet); a platform project never gets a Rust pin."""
    return STACK_SECTION if stack in ("rust", "unknown", HOUSE) else ""


def stack_table_techs(claude_md):
    """Non-house technology named in the Technology cells of the `## Stack`
    section's table (lowercase, sorted); prose and a negated token ("No
    Electron") never count (round 3 D, round 4 item 6)."""
    m = _STACK_HEADING_RE.search(claude_md or "")
    if not m:
        return []
    rest = claude_md[m.end():]
    nxt = re.search(r"^##[ \t]", rest, re.M)
    rows = _table_rows(rest[:nxt.start()] if nxt else rest)
    if not rows:
        return []
    col = next((i for i, c in enumerate(rows[0]) if c.lower().startswith("technology")),
               None)
    if col is None:
        return []
    found = set()
    for row in rows[1:]:
        cell = row[col] if col < len(row) else ""
        found |= {w.lower() for w in _NON_HOUSE_TECH_RE.findall(_NEGATED_RE.sub(" ", cell))}
    return sorted(found)


def package_json_frameworks(text):
    """The JS UI framework FAMILIES a package.json depends on (any dependency
    table), or None when the text is no JSON object (the caller decides)."""
    try:
        doc = json.loads(str(text).lstrip("﻿"))
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict):
        return None
    found = set()
    for key in _DEP_KEYS:
        deps = doc.get(key)
        if isinstance(deps, dict):
            for name, spec in deps.items():
                fam = FRAMEWORK_FAMILY.get(_alias_target(name, spec))
                if fam:
                    found.add(fam)
    return found


def framework_packages(text):
    """{family: {npm package names}} of a package.json ({} when unreadable):
    what an `npm uninstall` would name."""
    try:
        doc = json.loads(str(text).lstrip("﻿"))
    except (ValueError, TypeError):
        return {}
    out = {}
    for key in _DEP_KEYS:
        deps = doc.get(key) if isinstance(doc, dict) else None
        for name, spec in (deps.items() if isinstance(deps, dict) else ()):
            fam = FRAMEWORK_FAMILY.get(_alias_target(name, spec))
            if fam:
                out.setdefault(fam, set()).add(str(name))
    return out


def _alias_target(name, spec):
    """The real package of a dependency: an npm alias (`"ui": "npm:react@18"`)
    names it in the version spec."""
    spec = str(spec or "").strip()
    if spec.startswith("npm:"):
        real = spec[4:]
        name = ("@" + real[1:].split("@")[0]) if real.startswith("@") else real.split("@")[0]
    return str(name).strip().lower()


def _crate(text):
    """(is a [package] crate, its runtime dependency names) of a Cargo.toml."""
    try:
        doc = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, TypeError):
        return False, set()
    names, _tray = _cargo_deps(doc)
    return "package" in doc, names


def _js_exempt(rel, exempt_dirs, crates):
    """A JS / html file is exempt under a Trunk.toml dir or a JS-framework
    app's dir (`exempt_dirs`), or when the nearest crate above it depends on
    leptos / wasm-bindgen (round 3 C, round 4 item 6). A workspace-only root
    manifest is no crate, so its top-level dirs are judged."""
    parts = PurePosixPath(rel).parts[:-1]
    dirs = ["/".join(parts[:k]) for k in range(len(parts), -1, -1)]
    if any(d in exempt_dirs for d in dirs):
        return True
    crate = next((d for d in dirs if d in crates), None)
    return crate is not None and bool(crates[crate] & set(_WASM_CRATES))


def _is_hand_js(rel):
    """A JS file or an .html page (its inline <script>), anywhere scanned,
    not a test / minified / declaration / service-worker file."""
    p = PurePosixPath(rel)
    return ((p.suffix in _JS_SUFFIXES and not _NOT_HAND_JS.search(p.name))
            or p.suffix in (".html", ".htm"))


def _companion_module(text):
    """True when a package.json depends on @companion-module/base."""
    try:
        doc = json.loads(str(text).lstrip("\ufeff"))
    except (ValueError, TypeError):
        return False
    return isinstance(doc, dict) and any(
        isinstance(doc.get(k), dict) and COMPANION_MODULE_DEP in doc[k] for k in _DEP_KEYS)


def _vendored(scanned):
    """Project-relative paths a VENDORED.md lists (each entry relative to the
    VENDORED.md's dir and to the project root)."""
    out = set()
    for rel, text in scanned:
        if PurePosixPath(rel).name != "VENDORED.md":
            continue
        base = _rel_dir(rel)
        for entry in re.findall(r"(?m)`([^`\s]+)`|^[-*]\s+([^\s`]+)", text or ""):
            path = (entry[0] or entry[1]).strip("/")
            if "/" in path or "." in path:
                out.add(path)
                if base:
                    out.add(base + "/" + path)
    return out


def _rel_dir(rel):
    """The project-relative dir of a scanned file ("" for the project root)."""
    parent = str(PurePosixPath(rel).parent)
    return "" if parent == "." else parent


def _scan(root, host=None, run=None):
    """([(relative path, payload)], {gitignored relative path}) for every
    package.json, Cargo.toml, Trunk.toml, VENDORED.md, JS file, .wasm and .html
    page within SCAN_DEPTH levels of `root`, in ONE runner call; the second item
    is git's untracked ignored paths (a dir ends with `/`), empty outside a
    work tree. find's rc 1 (an unreadable subdir) still leaves valid output;
    any other rc raises StackCheckUnverified."""
    prune = []
    for name in _PRUNE_DIRS:
        prune += (["-o"] if prune else []) + ["-name", name]
    match = ["-name", "package.json", "-o", "-name", "Cargo.toml", "-o", "-name",
             "Trunk.toml", "-o", "-name", "VENDORED.md"]
    for suffix in _JS_SUFFIXES + (".html", ".htm", ".wasm"):
        match += ["-o", "-name", "*" + suffix]
    argv = (["sh", "-c", _SCAN_SH, "sh", root, _EMIT, _WEB_AWK,
             "-mindepth", "1", "-maxdepth", _SCAN_DEPTH,
             "(", "-type", "d", "(", *prune, ")", ")", "-prune", "-o",
             "-type", "f", "(", *match, ")"])
    r = _exec(argv, host=host, run=run)
    if r.returncode not in (0, 1):
        raise StackCheckUnverified(
            "stack check could not read %s on %s (rc %s: %s)"
            % (root, host or "this box", r.returncode,
               (r.stderr or "").strip()[:200]))
    parts = (r.stdout or "").split("\0")
    prefix = root.rstrip("/") + "/"
    scanned, ignored = [], set()
    for p, payload in zip(parts[1::2], parts[2::2]):
        rel = p[len(prefix):] if p.startswith(prefix) else p
        if rel == _IGNORED:
            ignored.update(x for x in payload.splitlines() if x)
        else:
            scanned.append((rel, payload))
    return scanned, ignored


def _gitignored(rel, ignored):
    """True when `rel` or one of its dirs (`dir/`) is a gitignored path."""
    parts = PurePosixPath(rel).parts
    return rel in ignored or any("/".join(parts[:k]) + "/" in ignored
                                 for k in range(1, len(parts)))


def _wasm_output(scanned):
    """Paths that are wasm-bindgen / Trunk OUTPUT, never hand-written (audit
    precision a, camera-box's pinned shading release): a JS file whose stem has
    a `<stem>_bg.wasm` / `<stem>.wasm` sibling, a Trunk hashed
    `<name>-<16 hex>.js` beside any `.wasm`, and the pages of a dir holding a
    Trunk hashed `_bg.wasm` (Trunk's generated loader page)."""
    wasm = {}
    for rel, _ in scanned:
        if rel.endswith(".wasm"):
            wasm.setdefault(_rel_dir(rel), set()).add(PurePosixPath(rel).name)
    out = set()
    for rel, _ in scanned:
        names, p = wasm.get(_rel_dir(rel)), PurePosixPath(rel)
        if not names:
            continue
        if p.suffix in (".html", ".htm"):
            if any(_TRUNK_HASH_WASM.search(n) for n in names):
                out.add(rel)
        elif p.suffix in _JS_SUFFIXES and (
                {p.stem + "_bg.wasm", p.stem + ".wasm"} & names
                or _TRUNK_HASH_JS.search(p.name)):
            out.add(rel)
    return out


def _web_payload(text):
    """(lines, [src refs], [import specifiers]) of a `_WEB_AWK` payload."""
    n, srcs, imps = 0, [], []
    for line in (text or "").splitlines():
        kind, _sep, val = line.partition(" ")
        if kind == "n" and val.strip().isdigit():
            n = int(val.strip())
        elif kind == "src":
            srcs.append(val.strip())
        elif kind == "imp":
            imps.append(val.strip())
    return n, srcs, imps


def _resolve(ref, base, files, module):
    """The scanned JS file `ref` names from the dir `base`, or None. Relative:
    from `base`. Root-absolute (`/app.js`): from `base` or any dir above it (the
    dir a page is served from is the web root). Never a URL; a module import
    never a bare package; an extensionless import also as `<ref>.<js suffix>`
    or `<ref>/index.<js suffix>`."""
    ref = re.split(r"[?#]", ref.strip(), maxsplit=1)[0]
    if (not ref or "://" in ref or ref.startswith(("//", "data:"))
            or (module and not ref.startswith((".", "/")))):
        return None
    parts = PurePosixPath(base).parts if base else ()
    bases = ([parts[:k] for k in range(len(parts), -1, -1)] if ref.startswith("/")
             else [parts])
    for b in bases:
        cand = posixpath.normpath(posixpath.join(*b, ref.lstrip("/")))
        for c in ((cand,) + tuple(cand + s for s in _JS_SUFFIXES)
                  + tuple(cand + "/index" + s for s in _JS_SUFFIXES)):
            if c in files:
                return c
    return None


def _hand_js(scanned, exempt_dirs, crates):
    """{path: lines} of the hand-written JS web UI (audit precision b): the
    inline <script> lines of every countable page, plus the countable JS those
    pages serve (`<script src>`, an import in an inline script) and, transitively,
    the modules a counted file imports. Not countable: an exempt dir or crate
    (`_js_exempt`), wasm-bindgen / Trunk output, a VENDORED.md entry, a test /
    minified / declaration / service-worker name."""
    vendored, output = _vendored(scanned), _wasm_output(scanned)

    def countable(rel):
        return (_is_hand_js(rel) and rel not in output
                and not _js_exempt(rel, exempt_dirs, crates)
                and not any(rel == v or rel.startswith(v + "/") for v in vendored))
    pages, js = {}, {}
    for rel, payload in scanned:
        suffix = PurePosixPath(rel).suffix
        if suffix in (".html", ".htm") and countable(rel):
            pages[rel] = _web_payload(payload)
        elif suffix in _JS_SUFFIXES:
            js[rel] = _web_payload(payload)
    files = set(js)
    todo = [_resolve(ref, _rel_dir(page), files, module)
            for page, (_n, srcs, imps) in pages.items()
            for refs, module in ((srcs, False), (imps, True)) for ref in refs]
    lines = {page: n for page, (n, _s, _i) in pages.items() if n}
    seen = set()
    while todo:
        rel = todo.pop()
        if rel is None or rel in seen or not countable(rel):
            continue
        seen.add(rel)
        n, _srcs, imps = js[rel]
        if n:
            lines[rel] = n
        todo += [_resolve(i, _rel_dir(rel), files, True) for i in imps]
    return lines


def deviation(path, host=None, run=None):
    """[(technology, evidence)] of the non-house technology the project's tree
    shows: JS-framework families, template / UI crates, a hand-written JS web UI
    (key `javascript`); a gitignored path never counts. Raises
    StackCheckUnverified when the target cannot be read."""
    root = str(path).rstrip("/") or "/"
    scanned, ignored = _scan(root, host=host, run=run)
    scanned = [(rel, p) for rel, p in scanned if not _gitignored(rel, ignored)]
    exempt_dirs = {_rel_dir(rel) for rel, _ in scanned
                   if PurePosixPath(rel).name == "Trunk.toml"}
    crates, found = {}, {}
    for rel, payload in scanned:
        name = PurePosixPath(rel).name
        if name == "Cargo.toml":
            is_crate, deps = _crate(payload)
            if is_crate:
                crates[_rel_dir(rel)] = deps
            for crate in sorted(deps & set(FORBIDDEN_CRATES)):
                found.setdefault(crate, []).append(rel)
        elif name == "package.json":
            fams = package_json_frameworks(payload)
            if fams is None:
                print("onboard-project: warning: %s does not parse; skipped "
                      "for the stack check" % rel, file=sys.stderr)
            for fam in sorted(fams or ()):
                found.setdefault(fam, []).append(rel)
            if (fams and _rel_dir(rel)) or _companion_module(payload):
                exempt_dirs.add(_rel_dir(rel))     # its own finding / a platform
    js = _hand_js(scanned, exempt_dirs, crates)
    js_files, js_lines = sorted(js), sum(js.values())
    items = [(tech, ", ".join(sorted(rels)[:3])) for tech, rels in sorted(found.items())]
    if js_lines >= HAND_JS_MIN_LINES:
        items.append(("javascript", "hand-written JS web UI: %d lines in %s" % (
            js_lines, ", ".join(sorted(js_files)[:3])
            + (" …" if len(js_files) > 3 else ""))))
    return items


def _gate_lines(path, host, run, gate_log, account=None):
    """The gate's ALLOW-MARKER lines for this project (its path in the line),
    read on the target from the project ACCOUNT's `/home/<account>/.claude/
    stack-gate.log` (round 4 item 6), else the login's `~`; [] unreadable."""
    if gate_log:
        log, host = gate_log, None
    elif account:
        log = "/home/%s/.claude/stack-gate.log" % account
    else:
        log = _resolve_remote_path(GATE_LOG, host, run=run)
    text = _read_file(log, host=host, run=run) if log else None
    root = str(path).rstrip("/") + "/"
    return [ln.strip() for ln in (text or "").splitlines()
            if "ALLOW-MARKER" in ln and root in ln][-5:]


def audit_drift(path, host=None, run=None, claude_md=None, entry=None,
                registry_path=None, gate_log=None):
    """The `--audit` rows for one registry row (`entry`): `stack-unpinned` when
    it has no known `stack`; for a `house` row only, one `stack-deviation` row
    per technology without its marker, or `unverified-stack` when the target
    could not be read (never shown as clean); plus every marker and the gate's
    ALLOW-MARKER lines as notes (`NOTE_KINDS`). [] otherwise."""
    entry = entry or {}
    stack = entry.get("stack")
    if stack not in STACK_IDS:
        reg = registry_path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            REGISTRY_FILENAME)
        if entry.get("name") not in {r.get("name") for r in load_rows(reg) or []}:
            detail = ("not in projects-registry.json: onboard it; airuleset records "
                      "its `stack` right after onboarding")
        else:
            detail = ("the projects-registry.json row has %s; airuleset records "
                      "`house` or a platform id (%s)"
                      % ("an unknown `stack` %r" % stack if stack else "no `stack`",
                         ", ".join(sorted(STACK_IDS))))
        return [{"kind": "stack-unpinned", "detail": detail}]
    if stack != HOUSE:
        return []
    markers = stack_exceptions(claude_md)
    rows = [{"kind": "stack-exception", "detail": "%s — %s" % m} for m in markers]
    if markers:
        rows += [{"kind": "stack-exception-use", "detail": ln}
                 for ln in _gate_lines(path, host, run, gate_log, entry.get("account"))]
    items = [(t, "`## Stack` table names %s" % t) for t in stack_table_techs(claude_md)]
    try:
        items += deviation(path, host=host, run=run)
    except StackCheckUnverified as e:
        rows.append({"kind": "unverified-stack", "detail": str(e)})
    for tech, evidence in items:
        if not exception_covers(claude_md, tech):
            rows.append({"kind": "stack-deviation", "detail":
                         "%s: %s — not the house stack (rules/house-stack.md); "
                         "another technology needs its own owner-approved "
                         "stack-exception marker" % (tech, evidence)})
    return rows
