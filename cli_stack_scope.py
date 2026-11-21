"""cli_stack_scope.py — which stack a checkout follows (#1333, ROZHODNUTÉ 1-2).

The house stack (``rules/house-stack.md``) is the default for a NEW standalone
app. An existing project follows its established stack, and that platform is
recorded CENTRALLY: the ``"stack"`` field of the project's
``projects-registry.json`` row, set by airuleset only, never by a project
session. ``STACK_IDS`` is the closed set of values.

``stack_for(start, registry_path)`` resolves a directory (a cwd, or the dir of
a file being written) to ``(project name, stack)``: git root (a linked worktree
reads the main repo's config) -> ``origin`` repo name -> the registry row with
that name / ``github_repo``, else the row whose path IS the git root. A row
without a ``stack`` and a directory no registry row claims (a brand-new repo:
the moment the stack is chosen) both read ``house``.

Consumers: the situational injector (``hooks/inject-situational-rule.sh``)
and ``gates.housestack`` skip a non-``house`` project; ``onboard-project
--audit`` reads the field. STDLIB ONLY, no repo imports: both hook consumers
import it on their hot path, and it never raises.
"""

import json
import os
import re

HOUSE = "house"
REGISTRY_FILENAME = "projects-registry.json"

STACK_IDS = {
    "house": "Rust app on the house stack (rules/house-stack.md)",
    "odoo": "Odoo ERP addons and the services and apps inside that repo",
    "n8n": "n8n workflows",
    "python-tooling": "Python CLI, automation or MCP tooling",
    "python-service": "Python web service or dashboard",
    "ha-addon": "Home Assistant add-on",
    "sveltekit": "SvelteKit (Node) web app",
    "obs": "OBS Studio fork, plugins and scripts",
    "config": "device or router configuration",
    "cuda": "GPU / CUDA workload",
    "mobile-rn": "React Native mobile app",
}

# Directory names never judged as the project's own app (tests, fixtures,
# vendored trees, build output). ONE list for the situational trigger's exclude
# column and gates.housestack (a test pins the conf regex to it).
SKIP_DIRS = ("test", "tests", "__tests__", "fixture", "fixtures", "__fixtures__",
             "testdata", "node_modules", "target", "vendor", "dist")

# Managed repos with NO registry row. The registry lists the projects airuleset
# manages; its consumers (checkout freshness, deploy state, peer links, project
# accounts) must never see airuleset itself, so its stack is recorded here.
UNREGISTERED_STACKS = {"airuleset": "python-tooling"}

_URL_NAME_RE = re.compile(r"[:/]([^/:\s]+?)(?:\.git)?/?\s*$")
_SECTION_RE = re.compile(r'^\s*\[\s*remote\s+"origin"\s*\]\s*$')


def git_root(start):
    """The nearest directory at or above `start` that holds `.git` (a dir, or
    the file of a linked worktree), or None. A `start` that does not exist yet
    (a file about to be written in a new dir) is walked up from its first
    existing ancestor."""
    d = os.path.abspath(start or ".")
    while not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _config_path(root):
    dotgit = os.path.join(root, ".git")
    if os.path.isdir(dotgit):
        return os.path.join(dotgit, "config")
    try:
        with open(dotgit, encoding="utf-8") as f:
            line = f.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = line[len("gitdir:"):].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(root, gitdir)
    common = gitdir
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as f:
            rel = f.read().strip()
        common = rel if os.path.isabs(rel) else os.path.join(gitdir, rel)
    except (OSError, UnicodeDecodeError):
        pass
    return os.path.join(common, "config")


def origin_repo_name(root):
    """The repo name of the `origin` remote of the checkout at `root`
    (`git@github.com:o/x.git` / `https://github.com/o/x` -> `x`), or None."""
    cfg = _config_path(root) if root else None
    if not cfg:
        return None
    try:
        with open(cfg, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    in_origin = False
    for line in lines:
        if line.lstrip().startswith("["):
            in_origin = bool(_SECTION_RE.match(line))
            continue
        if in_origin:
            key, sep, value = line.partition("=")
            if sep and key.strip() == "url":
                m = _URL_NAME_RE.search(value.strip())
                return m.group(1) if m else None
    return None


def load_rows(registry_path):
    """Registry rows (dicts), [] when the file is absent or broken."""
    try:
        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _real(path):
    try:
        return os.path.realpath(os.path.expanduser(str(path)))
    except (OSError, ValueError):
        return None


def stack_for(start, registry_path):
    """``(project name or None, stack id)`` for the checkout holding `start`.
    Never raises; anything unresolvable reads ``(None, "house")``."""
    try:
        root = git_root(start)
        if root is None:
            return None, HOUSE
        name = origin_repo_name(root)
        rows = load_rows(registry_path)
        real_root = _real(root)
        for row in rows:
            repo = str(row.get("github_repo") or "").rsplit("/", 1)[-1]
            if name and name in (row.get("name"), repo):
                return row.get("name"), str(row.get("stack") or HOUSE)
        for row in rows:
            if row.get("path") and _real(row["path"]) == real_root:
                return row.get("name"), str(row.get("stack") or HOUSE)
        if name in UNREGISTERED_STACKS:
            return name, UNREGISTERED_STACKS[name]
        return None, HOUSE
    except Exception:  # airuleset:script-ok a hook-side resolver never raises
        return None, HOUSE
