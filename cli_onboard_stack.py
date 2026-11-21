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
  hand-written JS (exempt only in a ``Trunk.toml`` dir or a leptos /
  wasm-bindgen crate), other technology in the ``## Stack`` table's Technology
  cells; every marker and the gate's ALLOW-MARKER lines come back as notes
  (``NOTE_KINDS``) that never count as drift.
- ``gates.housestack`` / ``gates.housestate``: ``package_json_frameworks``
  (families) and ``exception_covers`` (one marker, one technology).

Every target read goes through the injected runner (``cli_onboard_exec``) in
ONE call, so ``--host <remote>`` checks the remote tree over ssh, never the
local disk (the #1198 tray-check shape). STDLIB ONLY.
"""

import json
import os
import re
import sys
import tomllib
from pathlib import Path, PurePosixPath

from cli_onboard_exec import _exec, _read_file, _resolve_remote_path
from cli_onboard_tray import _deps as _cargo_deps
from cli_stack_scope import HOUSE, REGISTRY_FILENAME, SKIP_DIRS, STACK_IDS, load_rows

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

# Hand-written JS counts only inside a web-asset dir (the tray check's set) and
# only past this many lines in total: a PWA service worker or a small helper
# is no app. It is exempt in a Trunk.toml dir or a leptos / wasm-bindgen crate.
WEB_ASSET_DIRS = ("web", "static", "frontend", "www", "public", "ui")
HAND_JS_MIN_LINES = 200
_WASM_CRATES = ("leptos", "wasm-bindgen")
# Rust crates that render HTML or a UI outside Leptos/WASM (round 3 C).
FORBIDDEN_CRATES = ("askama", "tera", "maud", "yew", "dioxus", "sycamore")
_JS_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
_NOT_HAND_JS = re.compile(
    r"(\.min\.[cm]?[jt]sx?|\.d\.ts|\.config\.[cm]?[jt]s|^sw\.js|"
    r"^service-worker\.js|\.(?:test|spec)\.[cm]?[jt]sx?)$")
# Build output, vendored trees, tests, hidden dirs, docs: never the project's
# own UI. The trigger/gate SKIP_DIRS plus dist/pkg/build (Trunk / wasm-pack JS
# glue of a Leptos UI).
_PRUNE_DIRS = tuple(sorted(set(SKIP_DIRS) | {
    "pkg", "build", ".*", "examples", "benches", "docs"}))
_SCAN_DEPTH = "5"
# ONE runner call: NUL <path> NUL <payload>, the payload being the content of a
# package.json / Cargo.toml, the line count of a JS file, nothing for Trunk.toml.
_EMIT = ('for f; do printf "\\0%s\\0" "$f"; case "$f" in '
         '*/package.json|*/Cargo.toml) cat "$f";; */Trunk.toml) ;; '
         '*) wc -l < "$f";; esac; done')
# Non-house technology a house project's `## Stack` table may not name in its
# Technology cells without the marker (ROZHODNUTÉ 7, round 3 D).
_NON_HOUSE_TECH_RE = re.compile(
    r"(?<![\w.-])(react|vue(?:\.js)?|svelte(?:kit)?|angular|preact|solid(?:-?js)?|"
    r"lit|alpine\.?js|htmx|astro|qwik|ember(?:\.js)?|next\.js|nuxt|electron|yew|"
    r"dioxus|sycamore|actix(?:-web)?|rocket|warp|django|flask|fastapi|"
    r"express(?:\.js|js)|node\.?js|deno|opengl|vulkan|qt|gtk|wpf|winforms|"
    r"asp\.net|\.net)(?![\w-])", re.I)
_NEGATION_RE = re.compile(r"\b(?:no|not|never|without|nie|bez|žiadn\w*)\b", re.I)
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
    Applies when | Technology; round 3 H), so it can never drift from it."""
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
        lines += ["| %s | %s | %s |" % tuple(r[:3]) for r in rows if len(r) >= 3]
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
    """The `## Stack` seed for a NEW project's CLAUDE.md: only a house
    candidate (Rust, or nothing detected yet) gets it; a Python / Node repo
    is a platform project and never gets a Rust pin."""
    return STACK_SECTION if stack in ("rust", "unknown") else ""


def stack_table_techs(claude_md):
    """Non-house technology named in the Technology cells of the `## Stack`
    section's table (lowercase, sorted); prose and negated clauses ("No
    Electron") never count (round 3 D)."""
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
        for clause in re.split(r";|\.\s+|\.$", cell):
            if not _NEGATION_RE.search(clause):
                found |= {w.lower() for w in _NON_HOUSE_TECH_RE.findall(clause)}
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


def _js_exempt(rel, trunk_dirs, crates):
    """A JS file is exempt in a dir with a Trunk.toml, or when the nearest crate
    above it depends on leptos / wasm-bindgen (round 3 C). A workspace-only
    root manifest is no crate, so its top-level web dirs are judged."""
    parts = PurePosixPath(rel).parts[:-1]
    dirs = ["/".join(parts[:k]) for k in range(len(parts), -1, -1)]
    if any(d in trunk_dirs for d in dirs):
        return True
    crate = next((d for d in dirs if d in crates), None)
    return crate is not None and bool(crates[crate] & set(_WASM_CRATES))


def _is_hand_js(rel):
    p = PurePosixPath(rel)
    return (p.suffix in _JS_SUFFIXES and not _NOT_HAND_JS.search(p.name)
            and any(part in WEB_ASSET_DIRS for part in p.parts[:-1]))


def _rel_dir(rel):
    """The project-relative dir of a scanned file ("" for the project root)."""
    parent = str(PurePosixPath(rel).parent)
    return "" if parent == "." else parent


def _scan(root, host=None, run=None):
    """[(relative path, payload)] for every package.json, Cargo.toml,
    Trunk.toml and JS file within 5 levels of `root`, in ONE runner call.
    find's rc 1 (an unreadable subdir) still leaves valid output; any other rc
    raises StackCheckUnverified."""
    prune = []
    for name in _PRUNE_DIRS:
        prune += (["-o"] if prune else []) + ["-name", name]
    match = ["-name", "package.json", "-o", "-name", "Cargo.toml", "-o", "-name",
             "Trunk.toml"]
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
    """[(technology, evidence)] of the non-house technology the project's tree
    shows: JS-framework families, template / UI crates, hand-written JS (key
    `javascript`). Raises StackCheckUnverified when the target cannot be read."""
    root = str(path).rstrip("/") or "/"
    scanned = _scan(root, host=host, run=run)
    trunk_dirs = {_rel_dir(rel) for rel, _ in scanned
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
    js_files, js_lines = [], 0
    for rel, payload in scanned:
        if _is_hand_js(rel) and not _js_exempt(rel, trunk_dirs, crates):
            try:
                js_lines += int(payload.strip() or 0)
            except ValueError:
                continue
            js_files.append(rel)
    items = [(tech, ", ".join(sorted(rels)[:3])) for tech, rels in sorted(found.items())]
    if js_lines >= HAND_JS_MIN_LINES:
        items.append(("javascript", "hand-written JS web UI: %d lines in %s" % (
            js_lines, ", ".join(sorted(js_files)[:3])
            + (" …" if len(js_files) > 3 else ""))))
    return items


def _gate_lines(path, host, run, gate_log):
    """The gate's ALLOW-MARKER lines for this project (its path in the line),
    read from the target's `~/.claude/stack-gate.log`; [] when unreadable."""
    log = gate_log or _resolve_remote_path(GATE_LOG, host, run=run)
    text = _read_file(log, host=None if gate_log else host, run=run) if log else None
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
            detail = ("not in projects-registry.json: onboard it, and airuleset sets "
                      "its `stack`")
        else:
            detail = ("the projects-registry.json row has %s; airuleset sets `house` "
                      "or a platform id (%s)"
                      % ("an unknown `stack` %r" % stack if stack else "no `stack`",
                         ", ".join(sorted(STACK_IDS))))
        return [{"kind": "stack-unpinned", "detail": detail}]
    if stack != HOUSE:
        return []
    markers = stack_exceptions(claude_md)
    rows = [{"kind": "stack-exception", "detail": "%s — %s" % m} for m in markers]
    if markers:
        rows += [{"kind": "stack-exception-use", "detail": ln}
                 for ln in _gate_lines(path, host, run, gate_log)]
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
