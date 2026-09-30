"""cli_onboard_tray.py — the "Rust web app without a tray icon" foundation gap
(#1198, owner 2026-09-30).

The fleet convention (``rules/rust-web-tray.md``): a Rust app that serves a
web UI on a desktop machine ships a tray icon (status, Open <app>, Copy URL,
Exit that ends the tray only), autostarted at the desktop user's logon. The
fohmixer project went live without one because onboarding never checked. This
leaf module is that check; ``cli_onboard`` calls it from the foundation-gap
step (files the ticket) and from ``audit_project`` (``missing-tray`` drift).

Detection = an HTTP server dependency + web assets, and no tray dependency or
``*-tray`` crate. Manifests read: the root ``Cargo.toml`` and every
``crates/*/Cargo.toml`` (workspace layout). Every read goes through the
injected runner (``cli_onboard_exec``), so ``--host <remote>`` checks the
remote tree over ssh, never the local disk.
"""

import sys
import tomllib
from pathlib import Path

from cli_onboard_exec import _exec, _read_file

FOUNDATION_TRAY_TITLE = "foundation: tray ikona pre webovú Rust appku"

HTTP_SERVER_DEPS = frozenset({"axum", "actix-web", "warp", "tiny-http",
                              "rocket", "poem"})
TRAY_DEPS = frozenset({"tray-icon", "tray-item", "ksni"})
# A dependency that embeds or renders a web UI counts as web assets on its own.
WEB_UI_DEPS = frozenset({"rust-embed", "include-dir", "memory-serve",
                         "leptos", "yew", "dioxus"})
WEB_ASSET_FILES = ("index.html", "Trunk.toml")
WEB_ASSET_DIRS = ("web", "static", "frontend", "www", "public", "ui")
# Build output / vendored trees never count as the project's own web assets.
_PRUNE_DIRS = ("target", "node_modules", ".git")


def _norm(name):
    """Cargo dependency names compare with `-` and `_` as equal."""
    return str(name).strip().lower().replace("_", "-")


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


def _dep_names(doc):
    """Normalized crate names this manifest depends on; a renamed dependency
    (`web = { package = "axum" }`) counts under its real package name."""
    names = set()
    for table in _dep_tables(doc):
        for key, spec in table.items():
            real = spec.get("package") if isinstance(spec, dict) else None
            names.add(_norm(real or key))
    return names


def _manifest_paths(path, host=None, run=None):
    """Root Cargo.toml plus every crates/*/Cargo.toml, as found on the target."""
    root = Path(path)
    found = [root / "Cargo.toml"]
    r = _exec(["find", str(root / "crates"), "-mindepth", "2", "-maxdepth", "2",
               "-name", "Cargo.toml"], host=host, run=run)
    found.extend(Path(p) for p in sorted((r.stdout or "").split("\n")) if p)
    return found


def _read_manifests(path, host=None, run=None):
    """[(manifest_path, parsed_doc)] for every readable manifest. A manifest
    that does not parse is reported on stderr and skipped (a broken TOML is
    the project's own build error, never a reason to crash onboarding)."""
    out = []
    for mp in _manifest_paths(path, host=host, run=run):
        text = _read_file(mp, host=host, run=run)
        if text is None:
            continue
        try:
            out.append((mp, tomllib.loads(text)))
        except tomllib.TOMLDecodeError as e:
            print("onboard-project: warning: %s does not parse (%s); skipped "
                  "for the tray check" % (mp, e), file=sys.stderr)
    return out


def _web_asset_path(path, host=None, run=None):
    """First web-asset file/dir within 3 levels of the project root (covers
    `static/`, `crates/web/index.html`, `crates/web/static/`), or None."""
    prune = []
    for name in _PRUNE_DIRS:
        prune += (["-o"] if prune else []) + ["-name", name]
    files = []
    for name in WEB_ASSET_FILES:
        files += (["-o"] if files else []) + ["-name", name]
    dirs = []
    for name in WEB_ASSET_DIRS:
        dirs += (["-o"] if dirs else []) + ["-name", name]
    argv = (["find", str(path), "-mindepth", "1", "-maxdepth", "3",
             "(", "-type", "d", "(", *prune, ")", ")", "-prune", "-o",
             "(", "(", "-type", "f", "(", *files, ")", ")", "-o",
             "(", "-type", "d", "(", *dirs, ")", ")", ")",
             "-print", "-quit"])
    first = (_exec(argv, host=host, run=run).stdout or "").strip()
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
    deps, crate_names = set(), set()
    root_manifest = Path(path) / "Cargo.toml"
    for mp, doc in manifests:
        deps |= _dep_names(doc)
        pkg = (doc.get("package") or {}).get("name")
        if pkg:
            crate_names.add(_norm(pkg))
        if mp != root_manifest:          # crates/<dir>/Cargo.toml
            crate_names.add(_norm(mp.parent.name))
    servers = sorted(deps & HTTP_SERVER_DEPS)
    if not servers:
        return None
    if deps & TRAY_DEPS or any(n.endswith("-tray") for n in deps | crate_names):
        return None
    web = sorted(deps & WEB_UI_DEPS)
    evidence = ("dependency " + web[0]) if web else _web_asset_path(
        path, host=host, run=run)
    if not evidence:
        return None
    return ("HTTP server %s + web UI (%s), no tray dependency (%s) and no "
            "*-tray crate" % (servers[0], evidence,
                              ", ".join(sorted(TRAY_DEPS))))


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
