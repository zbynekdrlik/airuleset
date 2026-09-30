"""cli_onboard_tray.py — the "Rust web app without a tray icon" foundation gap
(#1198, owner 2026-09-30).

The fleet convention (``rules/rust-web-tray.md``): a Rust app that serves a
web UI on a desktop machine ships a tray icon (status, Open <app>, Copy URL,
Exit that ends the tray only), autostarted at the desktop user's logon. The
fohmixer project went live without one because onboarding never checked. This
leaf module is that check; ``cli_onboard`` calls it from the foundation-gap
step (files the ticket) and from ``audit_project`` (``missing-tray`` drift).

Detection = an HTTP server dependency + web assets, and no tray dependency,
no tauri tray feature and no ``*-tray`` crate. Manifests read: every
``Cargo.toml`` within 4 levels (root, workspace members, ``crates/*``, a
``src-tauri/`` outside the workspace), build output, vendored trees, hidden
dirs, examples/benches/tests/docs skipped; web assets are never looked for in
``src/`` (``src/web`` is a Rust module). Every read goes through the injected runner (``cli_onboard_exec``) in
at most two calls, so ``--host <remote>`` checks the remote tree over ssh,
never the local disk.
"""

import os
import sys
import tomllib
from pathlib import Path

from cli_onboard_exec import _exec

FOUNDATION_TRAY_TITLE = "foundation: tray ikona pre webovú Rust appku"

# hyper is left out on purpose: as a direct dependency it is as often an HTTP
# client as a server, so it alone would flag non-web apps.
HTTP_SERVER_DEPS = frozenset({"axum", "axum-server", "actix-web", "warp",
                              "tiny-http", "rocket", "poem", "salvo", "tide",
                              "ntex"})
TRAY_DEPS = frozenset({"tray-icon", "tray-item", "ksni", "trayicon", "systray"})
# tauri ships its tray behind a feature: "tray-icon" (v2), "system-tray" (v1).
TAURI_TRAY_FEATURES = frozenset({"tray-icon", "system-tray"})
# A dependency that embeds or renders a web UI counts as web assets on its own.
WEB_UI_DEPS = frozenset({"rust-embed", "include-dir", "memory-serve",
                         "leptos", "yew", "dioxus"})
WEB_ASSET_FILES = ("index.html", "Trunk.toml")
WEB_ASSET_DIRS = ("web", "static", "frontend", "www", "public", "ui")
# Never the shipped app: build output, vendored trees, hidden dirs (.git, a
# stale .claude/worktrees copy), examples/benches/tests and generated docs.
_PRUNE_DIRS = ("target", "node_modules", "vendor", ".*", "examples",
               "benches", "tests", "docs")
# Assets additionally skip Rust source: `src/web`, `src/ui` are modules.
_ASSET_PRUNE_DIRS = _PRUNE_DIRS + ("src",)
# Depth 4 covers <root>/Cargo.toml, crates/<x>/Cargo.toml,
# apps/<x>/src-tauri/Cargo.toml and crates/<x>/assets/index.html.
_SCAN_DEPTH = "4"
# ONE runner call prints every manifest as NUL <path> NUL <content>.
_CAT_WITH_NAMES = 'for f; do printf "\\0%s\\0" "$f"; cat "$f"; done'


def _norm(name):
    """Cargo dependency names compare with `-` and `_` as equal."""
    return str(name).strip().lower().replace("_", "-")


def _or_names(names):
    """`-name a -o -name b …` for a find expression."""
    expr = []
    for name in names:
        expr += (["-o"] if expr else []) + ["-name", name]
    return expr


def _pruned_find(path, prune, match):
    """find argv over `path` (never the root itself: a checkout named `web`
    must not match) that skips the `prune` dirs, then applies `match`."""
    return (["find", str(path), "-mindepth", "1", "-maxdepth", _SCAN_DEPTH,
             "(", "-type", "d", "(", *_or_names(prune), ")", ")",
             "-prune", "-o"] + match)


def _find_stdout(argv, path, host=None, run=None):
    """stdout of one tray-check find. find's own rc 1 (a missing or
    unreadable subdir) still leaves valid output; any other rc (ssh 255, a
    dead box) is reported LOUDLY on stderr, never read as "not a Rust app"
    in silence."""
    r = _exec(argv, host=host, run=run)
    if r.returncode not in (0, 1):
        print("onboard-project: warning: tray check could not read %s on %s "
              "(rc %s: %s)" % (path, host or "this box", r.returncode,
                               (r.stderr or "").strip()[:200]),
              file=sys.stderr)
    return r.stdout or ""


def _dep_tables(doc):
    """The runtime dependency tables of one parsed manifest: `[dependencies]`,
    `[workspace.dependencies]` and every `[target.<cfg>.dependencies]`.
    dev-/build-dependencies are not the shipped app, so they are left out."""
    tables = [doc.get("dependencies"),
              (doc.get("workspace") or {}).get("dependencies")]
    for spec in (doc.get("target") or {}).values():
        if isinstance(spec, dict):
            tables.append(spec.get("dependencies"))
    return [t for t in tables if isinstance(t, dict)]


def _deps(doc):
    """(normalized dependency names, has a tauri tray feature) of one
    manifest. A renamed dependency (`web = { package = "axum" }`) counts
    under its real package name."""
    names, tauri_tray = set(), False
    for table in _dep_tables(doc):
        for key, spec in table.items():
            real = _norm((spec.get("package") if isinstance(spec, dict)
                          else None) or key)
            names.add(real)
            if real == "tauri" and isinstance(spec, dict):
                feats = {str(f) for f in spec.get("features") or ()}
                tauri_tray = tauri_tray or bool(feats & TAURI_TRAY_FEATURES)
    return names, tauri_tray


def _read_manifests(path, host=None, run=None):
    """[(manifest_path, parsed_doc)] for every Cargo.toml within 4 levels of
    the project root, read in ONE runner call. A manifest that does not parse
    is reported on stderr and skipped (a broken TOML is the project's own
    build error, never a reason to crash onboarding)."""
    argv = _pruned_find(path, _PRUNE_DIRS,
                        ["-type", "f", "-name", "Cargo.toml", "-exec", "sh",
                         "-c", _CAT_WITH_NAMES, "sh", "{}", "+"])
    parts = _find_stdout(argv, path, host=host, run=run).split("\0")
    out = []
    for mp, text in zip(parts[1::2], parts[2::2]):
        try:
            out.append((Path(mp), tomllib.loads(text)))
        except tomllib.TOMLDecodeError as e:
            print("onboard-project: warning: %s does not parse (%s); skipped "
                  "for the tray check" % (mp, e), file=sys.stderr)
    return out


def _web_asset_path(path, host=None, run=None):
    """First web-asset file/dir within 4 levels of the project root (covers
    `static/`, `crates/web/index.html`, `crates/x-web/assets/index.html`), or
    None."""
    argv = _pruned_find(path, _ASSET_PRUNE_DIRS, [
        "(", "(", "-type", "f", "(", *_or_names(WEB_ASSET_FILES), ")", ")",
        "-o", "(", "-type", "d", "(", *_or_names(WEB_ASSET_DIRS), ")", ")",
        ")", "-print", "-quit"])
    first = _find_stdout(argv, path, host=host, run=run).strip()
    if not first:
        return None
    try:
        return str(Path(first).relative_to(Path(path)))
    except ValueError:
        return first


def rust_web_tray_gap(path, host=None, run=None):
    """None when the project is not a Rust web app, or already has a tray.
    Otherwise a one-line reason naming the server dependency and the web UI
    evidence (it goes into the ticket body and the audit drift detail)."""
    manifests = _read_manifests(path, host=host, run=run)
    if not manifests:
        return None
    deps, crate_names, tauri_tray = set(), set(), False
    root_manifest = os.path.normpath(os.path.join(str(path), "Cargo.toml"))
    for mp, doc in manifests:
        names, tray_feature = _deps(doc)
        deps |= names
        tauri_tray = tauri_tray or tray_feature
        pkg = (doc.get("package") or {}).get("name")
        if pkg:
            crate_names.add(_norm(pkg))
        if os.path.normpath(str(mp)) != root_manifest:   # a member crate dir
            crate_names.add(_norm(mp.parent.name))
    servers = sorted(deps & HTTP_SERVER_DEPS)
    if not servers:
        return None
    if (tauri_tray or deps & TRAY_DEPS
            or any(n.endswith("-tray") for n in deps | crate_names)):
        return None
    web = sorted(deps & WEB_UI_DEPS)
    evidence = ("dependency " + web[0]) if web else _web_asset_path(
        path, host=host, run=run)
    if not evidence:
        return None
    return ("HTTP server %s + web UI (%s), no tray dependency (%s), no tauri "
            "tray feature and no *-tray crate"
            % (servers[0], evidence, ", ".join(sorted(TRAY_DEPS))))


def foundation_tray_body(name, reason):
    return (
        "Webová Rust appka **%s** nemá tray ikonu (%s).\n\n"
        "Konvencia `rules/rust-web-tray.md`: tray ukazuje stav (tooltip alebo "
        "menu), má **Open <app>** (lokálna URL), **Copy URL** a Exit, ktorý "
        "ukončí len tray (služba beží ďalej). Tray sa spúšťa pri prihlásení "
        "desktop používateľa vedľa služby. Referencia: iemmixer "
        "`crates/iem-tray`. Onboarding tento ticket LEN zakladá.\n\n"
        "Scope-gate: planned-work"
    ) % (name, reason)
