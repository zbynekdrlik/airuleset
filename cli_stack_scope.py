"""cli_stack_scope.py — which stack a checkout follows (#1333, ROZHODNUTÉ 1-2,
round 3 B + I).

The house stack (``rules/house-stack.md``) is the default for a NEW standalone
app. An existing project follows its established stack, and that platform is
recorded CENTRALLY: the ``"stack"`` field of the project's
``projects-registry.json`` row, set by airuleset only, never by a project
session. ``STACK_IDS`` is the closed set of values.

``stack_for(start, registry_path, fallback=None)`` resolves a directory (a
cwd, or the dir of a file being written) to ``(project name, stack)``:

- inside a git checkout, git itself names the checkout (``git rev-parse
  --show-toplevel --git-common-dir``, so a linked worktree resolves through its
  MAIN checkout); the row is the one whose name / ``github_repo`` is the
  ``origin`` repo name, else whose path is the main checkout or the worktree;
- outside any repo, the registry row whose path is the longest prefix of the
  directory (a non-git project such as mikrotik), else the ``fallback`` (the
  session cwd: a scratchpad file belongs to the session's project);
- a row without a ``stack``, and a checkout no row claims (a brand-new repo:
  the moment the stack is chosen), read ``house``.

Consumers: the situational injector (``hooks/inject-situational-rule.sh``),
``gates.housestack`` and ``gates.housestate`` skip a non-``house`` project;
``onboard-project --audit`` reads the field. STDLIB ONLY, no repo imports
(hook hot path), and it never raises.
"""

import json
import os
import re
import subprocess

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
             "testdata", "node_modules", "target", "vendor", "dist", "e2e")

# Managed repos with NO registry row. The registry lists the projects airuleset
# manages; its consumers (checkout freshness, deploy state, peer links, project
# accounts) must never see airuleset itself, so its stack is recorded here.
UNREGISTERED_STACKS = {"airuleset": "python-tooling"}

_URL_NAME_RE = re.compile(r"[:/]([^/:\s]+?)(?:\.git)?/?\s*$")
_SECTION_RE = re.compile(r'^\s*\[\s*remote\s+"origin"\s*\]\s*$')


def _existing_dir(start):
    d = os.path.abspath(start or ".")
    while not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return d


def git_layout(start):
    """``(worktree top, main checkout, common git dir)`` of the checkout holding
    `start`, asked of git itself; None outside a repo, without git, or when git
    refuses (a foreign owner). A `start` that does not exist yet is walked up
    from its first existing ancestor."""
    d = _existing_dir(start)
    if d is None:
        return None
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")}
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "--path-format=absolute",
                            "--show-toplevel", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = r.stdout.splitlines() if r.returncode == 0 else []
    if len(lines) < 2:
        return None
    top, common = lines[0], lines[1]
    main = os.path.dirname(common) if os.path.basename(common) == ".git" else top
    return top, main, common


def repo_root(start):
    """The worktree top of the checkout holding `start`, or None."""
    layout = git_layout(start)
    return layout[0] if layout else None


def origin_repo_name(common):
    """The repo name of the `origin` remote in the common git dir's config
    (`git@github.com:o/x.git` / `https://github.com/o/x` -> `x`), or None."""
    try:
        with open(os.path.join(common, "config"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError, TypeError):
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


def _row_stack(row):
    return row.get("name"), str(row.get("stack") or HOUSE)


def _row_by_path(rows, path, prefix=False):
    """The row whose path is `path` (or, with `prefix`, the longest row path
    that `path` lies under)."""
    want = _real(path)
    best = None
    for row in rows:
        rp = _real(row["path"]) if row.get("path") else None
        if not rp or not want:
            continue
        if want == rp or (prefix and want.startswith(rp.rstrip(os.sep) + os.sep)):
            if best is None or len(rp) > len(_real(best["path"])):
                best = row
    return best


def stack_for(start, registry_path, fallback=None):
    """``(project name or None, stack id)`` for the checkout holding `start`;
    outside any repo and registry path, for `fallback` (the session cwd).
    Never raises; anything unresolvable reads ``(None, "house")``."""
    try:
        rows = load_rows(registry_path)
        layout = git_layout(start)
        if layout is None:
            row = _row_by_path(rows, start, prefix=True)
            if row:
                return _row_stack(row)
            if fallback is not None:
                return stack_for(fallback, registry_path)
            return None, HOUSE
        top, main, common = layout
        name = origin_repo_name(common)
        for row in rows:
            repo = str(row.get("github_repo") or "").rsplit("/", 1)[-1]
            if name and name in (row.get("name"), repo):
                return _row_stack(row)
        row = _row_by_path(rows, main) or _row_by_path(rows, top)
        if row:
            return _row_stack(row)
        if name in UNREGISTERED_STACKS:
            return name, UNREGISTERED_STACKS[name]
        return None, HOUSE
    except Exception:  # airuleset:script-ok a hook-side resolver never raises
        return None, HOUSE
