"""cli_onboard.py — jednotný idempotentný onboarding projektu pod správu
airuleset (#569).

Owner directive (2026-08-19, verbatim): "pridavanie musi byt jednotny sposob
bud cez nejaky udrziavany script alebo slash cmd, nemoze to byt ze kazdy
projekt ktory mas ako target je v inom stave lebo si mal random naladu a
znalosti co ako ma byt!" — onboarding musí byť JEDEN udržiavaný mechanizmus,
nie ad-hoc ručné kroky. Ručný onboarding montalu-vyuctovanie (2026-08-19) a
driftujúce predošlé onboardingy sú incident, ktorý tento modul rieši.

Design (issue #569, design comment): stdlib-only leaf modul (vzor ostatných
19× ``cli_*.py``), registrovaný v ``airuleset.SUBCOMMANDS``. Core invariant =
IDEMPOTENCIA: každý krok detekuje stav → koná LEN ak želaný stav chýba →
reportuje ``satisfied``/``applied``/``would-apply``/``skipped``. 2. beh =
all-satisfied no-op, nula mutácií. Rozpracovaný (dirty) worktree sa NIKDY
nedotýka (per-file dirty guard). Všetky side-effecty (git/gh/ssh) idú cez
injektovaný ``run`` callable (default ``subprocess.run``, presne vzor
``cli_remote.py``) → offline testovateľné, žiadne reálne GitHub repá/tickety.

Machine-readable registry žije v ``projects-registry.json`` v tomto repe
(jeden autoritatívny, versioned, review-ovateľný zoznam) — ITERÁCIA "all
projects" ide odtiaľ, nie z naratívnej memory.
"""

import datetime
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

GITHUB_OWNER = "zbynekdrlik"
REGISTRY_FILENAME = "projects-registry.json"

# Nested path pod ~/devel/<cluster>/<leaf>: <cluster> v tomto sete dostane
# prefix (montalu-vyuctovanie, montalu-n8n vzor). Deterministicky overené
# proti reálnym menám — forestshop/slovnormal/n8n NIE sú v sete (parovanie-
# produktov, forecasting-storage, email-extract). 1-riadková reviewovateľná
# extenzia keď nový klaster prevezme konvenciu; `--name` prebije pre výnimky.
CLUSTER_PREFIXES = {"montalu"}

# .gitignore default vzory — append-only, nikdy neprepisujú existujúci súbor.
GITIGNORE_COMMON = [
    "__pycache__/", "*.pyc", ".pytest_cache/", ".venv/", "venv/",
    ".env", "*.env.local", ".DS_Store",
]
GITIGNORE_BY_STACK = {
    "rust": ["target/"],
    "node": ["node_modules/", "dist/", "build/"],
    "python": [],
}

ONBOARD_TICKET_TITLE = "onboarding: projekt pod správou airuleset"
FOUNDATION_CI_TITLE = "foundation: pridať CI pipeline"
FOUNDATION_VERSION_TITLE = "foundation: version label na dashboarde"

STEP_ORDER = [
    "git_init", "gitignore", "claude_md", "remote", "branches",
    "foundation_tickets", "notification_ticket", "registry",
]


# --------------------------------------------------------------------------- #
# Injectable executor — local argv, or ssh-wrapped for a REMOTE_HOSTS target.
# --------------------------------------------------------------------------- #
def _run(run):
    return run or subprocess.run


def resolve_remote(host):
    """The REMOTE_HOSTS entry for `host` (name or ip), or None for local.
    dev1 / local / None run locally (this maintainer box)."""
    if host in (None, "", "local", "dev1"):
        return None
    try:
        import cli_fleet
        for h in cli_fleet.REMOTE_HOSTS:
            if h.get("name") == host or h.get("host") == host:
                return h
    except Exception:
        return None
    return None


def _exec(argv, host=None, run=None):
    """Run `argv` locally, or ssh-wrapped when `host` names a remote box.
    gh API calls (issue list/create with -R) pass host=None — they talk to
    GitHub from any box; only working-tree git / repo-create needs the host."""
    remote = resolve_remote(host)
    if remote is None:
        return _run(run)(argv, capture_output=True, text=True)
    ssh = ["ssh", "-o", "StrictHostKeyChecking=no"]
    ident = remote.get("identity")
    if ident:
        ssh += ["-i", os.path.expanduser(ident)]
    ssh += ["%s@%s" % (remote["user"], remote["host"]),
            " ".join(shlex.quote(a) for a in argv)]
    return _run(run)(ssh, capture_output=True, text=True)


def _git(path, args, host=None, run=None):
    return _exec(["git", "-C", str(path), *args], host=host, run=run)


def _gh(args, run=None):
    """gh API call (repo-scoped via -R) — always local, never ssh."""
    return _run(run)(["gh", *args], capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# Name derivation — deterministic, studied from the real managed set.
# --------------------------------------------------------------------------- #
def _sanitize_name(s):
    s = (s or "").strip().lower().replace("_", "-")
    s = re.sub(r"[^a-z0-9.-]+", "-", s)
    return s.strip("-")


def derive_repo_name(path, name=None):
    """Deterministic repo name from `path` (or an explicit `--name` override).
    Leaf basename with `_`→`-`, lowercased; a nested path under a
    CLUSTER_PREFIXES dir gets the `<cluster>-<leaf>` prefix."""
    if name:
        return _sanitize_name(name)
    p = Path(str(path).rstrip("/"))
    base = _sanitize_name(p.name)
    parent = p.parent.name
    if parent in CLUSTER_PREFIXES:
        return "%s-%s" % (parent, base)
    return base


# --------------------------------------------------------------------------- #
# Stack + .gitignore (append-only).
# --------------------------------------------------------------------------- #
def detect_stack(path):
    p = Path(path)
    if (p / "Cargo.toml").exists():
        return "rust"
    if (p / "package.json").exists():
        return "node"
    if ((p / "pyproject.toml").exists() or (p / "setup.py").exists()
            or (p / "requirements.txt").exists()):
        return "python"
    return "unknown"


def _default_gitignore_patterns(stack):
    return list(GITIGNORE_COMMON) + GITIGNORE_BY_STACK.get(stack, [])


def gitignore_missing_patterns(path, stack):
    """Default patterns for `stack` that are ABSENT from the existing
    .gitignore — append-only, never proposes removing a user's own lines."""
    existing = set()
    gi = Path(path) / ".gitignore"
    if gi.exists():
        for line in gi.read_text(encoding="utf-8").splitlines():
            existing.add(line.strip())
    return [p for p in _default_gitignore_patterns(stack) if p not in existing]


def _is_artifact(relpath):
    r = relpath
    return ("__pycache__/" in r or r.endswith(".pyc")
            or r == "target" or r.startswith("target/") or "/target/" in r
            or r.startswith("node_modules/") or "/node_modules/" in r
            or ".pytest_cache/" in r)


def _tracked_ignored_artifacts(path, host=None, run=None):
    r = _git(path, ["ls-files", "-z"], host=host, run=run)
    files = [f for f in (r.stdout or "").split("\0") if f]
    return [f for f in files if _is_artifact(f)]


# --------------------------------------------------------------------------- #
# Repo / branch detection.
# --------------------------------------------------------------------------- #
def has_git_repo(path, run=None):
    return _git(path, ["rev-parse", "--git-dir"], run=run).returncode == 0


def remote_url(path, run=None):
    r = _git(path, ["remote", "get-url", "origin"], run=run)
    if r.returncode == 0 and (r.stdout or "").strip():
        return r.stdout.strip()
    return None


def detect_default_branch(path, run=None):
    """The repo's default branch — origin/HEAD if known, else the checked-out
    branch. NEVER changed by this module (respect existing convention)."""
    r = _git(path, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
             run=run)
    if r.returncode == 0 and (r.stdout or "").strip():
        return r.stdout.strip().split("/")[-1]
    r = _git(path, ["symbolic-ref", "--short", "HEAD"], run=run)
    if r.returncode == 0 and (r.stdout or "").strip():
        return r.stdout.strip()
    return None


def detect_branch_model(path, run=None, overrides=None):
    overrides = overrides or []
    default = detect_default_branch(path, run=run) or "main"
    if "3-branch" in overrides:
        return {"branch_model": "3-branch", "default_branch": default,
                "work_branch": "develop"}
    return {"branch_model": "2-branch", "default_branch": default,
            "work_branch": "dev"}


def worktree_is_dirty(path, run=None):
    return bool((_git(path, ["status", "--porcelain"], run=run).stdout or "").strip())


def file_is_dirty(path, relpath, run=None):
    r = _git(path, ["status", "--porcelain", "--", relpath], run=run)
    return bool((r.stdout or "").strip())


# --------------------------------------------------------------------------- #
# Registry.
# --------------------------------------------------------------------------- #
def default_registry_path():
    return str(Path(__file__).resolve().parent / REGISTRY_FILENAME)


def load_registry(registry_path=None):
    p = registry_path or default_registry_path()
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_registry(registry_path, entries):
    Path(registry_path).write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")


def registry_entry_for_entries(entries, name):
    for e in entries:
        if e.get("name") == name:
            return e
    return None


def registry_entry_for(registry_path, name):
    return registry_entry_for_entries(load_registry(registry_path), name)


def upsert_entry(entries, entry):
    out = [e for e in entries if e.get("name") != entry.get("name")]
    out.append(entry)
    out.sort(key=lambda e: (e.get("host", ""), e.get("name", "")))
    return out


def _today():
    return datetime.date.today().isoformat()


def _tilde(path):
    home = os.path.expanduser("~")
    ap = os.path.abspath(path)
    if ap.startswith(home + os.sep):
        return "~" + ap[len(home):]
    return ap


def build_registry_entry(path, host, name, overrides, run, registry_path,
                         onboarded_date=None):
    model = detect_branch_model(path, run=run, overrides=overrides)
    existing = registry_entry_for(registry_path, name)
    onboarded = (existing or {}).get("onboarded") or onboarded_date or _today()
    return {
        "name": name,
        "host": host or "dev1",
        "path": _tilde(path),
        "branch_model": model["branch_model"],
        "default_branch": model["default_branch"],
        "work_branch": model["work_branch"],
        "overrides": list(overrides or []),
        "onboarded": onboarded,
    }


# --------------------------------------------------------------------------- #
# gh ticket helpers (gated behind run; -R makes them box-agnostic).
# --------------------------------------------------------------------------- #
def _issue_exists(name, title, run=None):
    repo = "%s/%s" % (GITHUB_OWNER, name)
    r = _gh(["issue", "list", "-R", repo, "--state", "all", "--search", title,
             "--json", "number,title", "--limit", "50"], run=run)
    try:
        data = json.loads(r.stdout or "[]")
    except ValueError:
        return False
    tl = title.strip().lower()
    return any((it.get("title", "").strip().lower() == tl) for it in data)


def _file_issue(name, title, body, run=None):
    repo = "%s/%s" % (GITHUB_OWNER, name)
    return _gh(["issue", "create", "-R", repo, "--title", title,
                "--body", body], run=run)


def _onboard_ticket_body(name):
    return (
        "Projekt **%s** je odteraz pod správou airuleset (jednotný "
        "onboarding, `airuleset.py onboard-project`).\n\n"
        "Čo to znamená: platia globálne pravidlá (two-branch/three-branch "
        "workflow, auto-merge default alebo `airuleset:merge=manual` marker, "
        "TDD/regresné testy, post-deploy verifikácia, Playbook router). "
        ".gitignore hygiena a CLAUDE.md skeleton boli doplnené len ak "
        "chýbali; existujúce súbory sa neprepisovali.\n\n"
        "Zápis v machine-readable registry: `projects-registry.json` v repe "
        "airuleset.\n\n"
        "Scope-gate: planned-work"
    ) % name


def _foundation_ci_body(name):
    return (
        "Projekt **%s** nemá CI pipeline (`.github/workflows/`). Doplniť "
        "lint + test + (podľa stacku) build/deploy job, aby každý PR prešiel "
        "bránami pred mergom. Onboarding tento ticket LEN zakladá — CI sa "
        "negeneruje automaticky.\n\n"
        "Scope-gate: planned-work"
    ) % name


def _foundation_version_body(name):
    return (
        "Web projekt **%s** by mal viditeľne zobrazovať nasadenú verziu "
        "(v<semver>, build-time injected, overené DOM read-om po deployi). "
        "Onboarding tento ticket LEN zakladá.\n\n"
        "Scope-gate: planned-work"
    ) % name


# --------------------------------------------------------------------------- #
# CLAUDE.md skeleton.
# --------------------------------------------------------------------------- #
def _claude_md_skeleton(name):
    return (
        "# %s — Project Instructions\n\n"
        "## Overview\n\n"
        "TODO: jednou vetou čo projekt je a pre koho.\n\n"
        "## Branch policy\n\n"
        "Two-branch: work na `dev`, PR `dev`→default. (Ak projekt používa "
        "inú konvenciu, uprav tento riadok — onboarding default branch "
        "nikdy nemení.)\n\n"
        "## Playbook router\n\n"
        "- <area> → `.claude/rules/<area>.md` (auto-loads on its `paths:`)\n"
        "- build / deploy / release → load `.claude/skills/<area>` "
        "(invoke by name)\n"
    ) % name


# --------------------------------------------------------------------------- #
# Idempotent step executors — each returns {step, status, detail}.
# --------------------------------------------------------------------------- #
def _step(name, status, detail):
    return {"step": name, "status": status, "detail": detail}


def step_git_init(path, host=None, run=None, dry_run=False):
    if has_git_repo(path, run=run):
        return _step("git_init", "satisfied", "git repo present")
    if dry_run:
        return _step("git_init", "would-apply", "would git init")
    r = _git(path, ["init", "-q"], host=host, run=run)
    ok = r.returncode == 0
    return _step("git_init", "applied" if ok else "skipped",
                 "git init" if ok else "git init failed: " + (r.stderr or "").strip())


def _commit_paths(path, message, paths, host=None, run=None):
    """Scoped commit — ONLY the given pathspecs, never `git add -A`, so a
    project session's other in-progress files are left untouched."""
    _git(path, ["commit", "-q", "-m", message, "--", *paths], host=host, run=run)


def step_gitignore(path, stack, host=None, run=None, dry_run=False):
    missing = gitignore_missing_patterns(path, stack)
    if not missing:
        return _step("gitignore", "satisfied", "all default patterns present")
    if file_is_dirty(path, ".gitignore", run=run):
        return _step("gitignore", "skipped",
                     ".gitignore has uncommitted changes — not touching a "
                     "dirty worktree")
    if dry_run:
        return _step("gitignore", "would-apply",
                     "would append %d pattern(s)" % len(missing))
    gi = Path(path) / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    gi.write_text(existing + "\n".join(missing) + "\n", encoding="utf-8")
    _git(path, ["add", ".gitignore"], host=host, run=run)
    paths = [".gitignore"]
    arts = _tracked_ignored_artifacts(path, host=host, run=run)
    for a in arts:
        _git(path, ["rm", "--cached", "-q", "--", a], host=host, run=run)
        paths.append(a)
    _commit_paths(path, "chore: onboard — .gitignore hygiene (#569)", paths,
                  host=host, run=run)
    detail = "appended %d pattern(s)" % len(missing)
    if arts:
        detail += ", untracked %d artifact(s)" % len(arts)
    return _step("gitignore", "applied", detail)


def step_claude_md(path, name, host=None, run=None, dry_run=False):
    cm = Path(path) / "CLAUDE.md"
    if cm.exists():
        return _step("claude_md", "satisfied", "CLAUDE.md present (never overwritten)")
    if dry_run:
        return _step("claude_md", "would-apply",
                     "would create CLAUDE.md skeleton + Playbook router")
    cm.write_text(_claude_md_skeleton(name), encoding="utf-8")
    _git(path, ["add", "CLAUDE.md"], host=host, run=run)
    _commit_paths(path, "chore: onboard — CLAUDE.md skeleton + Playbook router (#569)",
                  ["CLAUDE.md"], host=host, run=run)
    return _step("claude_md", "applied", "created CLAUDE.md skeleton + Playbook router")


def step_remote(path, name, host=None, run=None, dry_run=False):
    url = remote_url(path, run=run)
    if url:
        return _step("remote", "satisfied", "origin: " + url)
    target = "%s/%s" % (GITHUB_OWNER, name)
    if dry_run:
        return _step("remote", "would-apply", "would create private repo " + target)
    r = _exec(["gh", "repo", "create", target, "--private", "--source",
               str(path), "--push"], host=host, run=run)
    ok = r.returncode == 0
    return _step("remote", "applied" if ok else "skipped",
                 "gh repo create " + target
                 + ("" if ok else ": " + (r.stderr or "").strip()))


def step_branches(path, overrides=None, host=None, run=None, dry_run=False):
    model = detect_branch_model(path, run=run, overrides=overrides)
    work = model["work_branch"]
    detail = "%s (default %s, work %s)" % (
        model["branch_model"], model["default_branch"], work)
    exists = _git(path, ["rev-parse", "--verify", "--quiet",
                         "refs/heads/" + work], run=run).returncode == 0
    if exists:
        return _step("branches", "satisfied", detail + " — work branch present")
    if dry_run:
        return _step("branches", "would-apply", detail + " — would create work branch")
    r = _git(path, ["branch", work], host=host, run=run)
    ok = r.returncode == 0
    return _step("branches", "applied" if ok else "skipped", detail)


def step_foundation_tickets(path, name, host=None, run=None, dry_run=False):
    gaps = []
    if not _has_ci(path):
        gaps.append(("ci", FOUNDATION_CI_TITLE, _foundation_ci_body(name)))
    if detect_stack(path) == "node":
        gaps.append(("version-label", FOUNDATION_VERSION_TITLE,
                     _foundation_version_body(name)))
    if not gaps:
        return _step("foundation_tickets", "satisfied", "no foundation gaps")
    filed, already = [], []
    for gap_id, title, body in gaps:
        if _issue_exists(name, title, run=run):
            already.append(gap_id)
            continue
        if dry_run:
            filed.append(gap_id)
            continue
        _file_issue(name, title, body, run=run)
        filed.append(gap_id)
    if not filed:
        return _step("foundation_tickets", "satisfied",
                     "gap tickets already filed: " + ", ".join(already))
    status = "would-apply" if dry_run else "applied"
    detail = ("would file: " if dry_run else "filed: ") + ", ".join(filed)
    if already:
        detail += " (already: %s)" % ", ".join(already)
    return _step("foundation_tickets", status, detail)


def _has_ci(path):
    wf = Path(path) / ".github" / "workflows"
    if not wf.is_dir():
        return False
    return any(wf.glob("*.yml")) or any(wf.glob("*.yaml"))


def step_notification_ticket(path, name, host=None, run=None, dry_run=False):
    if _issue_exists(name, ONBOARD_TICKET_TITLE, run=run):
        return _step("notification_ticket", "satisfied",
                     "onboarding ticket already present")
    if dry_run:
        return _step("notification_ticket", "would-apply",
                     "would file onboarding notification ticket")
    _file_issue(name, ONBOARD_TICKET_TITLE, _onboard_ticket_body(name), run=run)
    return _step("notification_ticket", "applied",
                 "filed onboarding notification ticket")


def step_registry(path, entry, registry_path, host=None, run=None, dry_run=False):
    entries = load_registry(registry_path)
    existing = registry_entry_for_entries(entries, entry["name"])
    if existing == entry:
        return _step("registry", "satisfied", "registry entry up to date")
    if dry_run:
        return _step("registry", "would-apply",
                     "would upsert registry entry for " + entry["name"])
    save_registry(registry_path, upsert_entry(entries, entry))
    return _step("registry", "applied",
                 ("updated" if existing else "added") + " registry entry for "
                 + entry["name"])


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def onboard_project(path, host=None, name=None, overrides=None,
                    registry_path=None, run=None, dry_run=False,
                    onboarded_date=None):
    path = str(Path(path))
    name = derive_repo_name(path, name=name)
    registry_path = registry_path or default_registry_path()
    overrides = list(overrides or [])
    stack = detect_stack(path)
    steps = [
        step_git_init(path, host, run, dry_run),
        step_gitignore(path, stack, host, run, dry_run),
        step_claude_md(path, name, host, run, dry_run),
        step_remote(path, name, host, run, dry_run),
        step_branches(path, overrides, host, run, dry_run),
        step_foundation_tickets(path, name, host, run, dry_run),
        step_notification_ticket(path, name, host, run, dry_run),
    ]
    entry = build_registry_entry(path, host, name, overrides, run,
                                 registry_path, onboarded_date)
    steps.append(step_registry(path, entry, registry_path, host, run, dry_run))
    return {"name": name, "stack": stack, "steps": steps, "entry": entry}


def audit_project(entry, run=None):
    """READ-ONLY drift report for one registry entry — never mutates."""
    path = os.path.expanduser(entry.get("path", ""))
    drift = []
    if not has_git_repo(path, run=run):
        drift.append({"kind": "missing-repo", "detail": path or "(no path)"})
        return drift
    if remote_url(path, run=run) is None:
        drift.append({"kind": "missing-remote", "detail": "no origin remote"})
    arts = _tracked_ignored_artifacts(path, run=run)
    if arts:
        drift.append({"kind": "tracked-artifact",
                      "detail": ", ".join(arts[:5])})
    actual = detect_default_branch(path, run=run)
    want = entry.get("default_branch")
    if want and actual and actual != want:
        drift.append({"kind": "branch-model-mismatch",
                      "detail": "registry=%s repo=%s" % (want, actual)})
    cm = Path(path) / "CLAUDE.md"
    if not cm.exists():
        drift.append({"kind": "missing-claude-md", "detail": "CLAUDE.md absent"})
    elif "## Playbook router" not in cm.read_text(encoding="utf-8", errors="replace"):
        drift.append({"kind": "missing-router",
                      "detail": "CLAUDE.md has no Playbook router"})
    return drift


def audit_registry(registry_path=None, run=None):
    registry_path = registry_path or default_registry_path()
    return {e["name"]: audit_project(e, run=run)
            for e in load_registry(registry_path) if e.get("name")}


# --------------------------------------------------------------------------- #
# CLI entry.
# --------------------------------------------------------------------------- #
def _print_result(result):
    print("onboard-project: %s (stack %s)" % (result["name"], result["stack"]))
    for s in result["steps"]:
        icon = {"satisfied": "✓", "applied": "＋", "would-apply": "…",
                "skipped": "⤼"}.get(s["status"], "?")
        print("  %s %-20s %-11s %s" % (icon, s["step"], s["status"], s["detail"]))


def _print_audit(report):
    clean = True
    for name in sorted(report):
        drift = report[name]
        if not drift:
            print("  ✓ %-28s no drift" % name)
            continue
        clean = False
        print("  ✗ %s" % name)
        for d in drift:
            print("      - %-24s %s" % (d["kind"], d["detail"]))
    if clean:
        print("onboard-project --audit: no drift across %d project(s)" % len(report))


def cmd_onboard_project(args):
    registry_path = getattr(args, "registry", None) or default_registry_path()
    path = getattr(args, "path", None)
    if getattr(args, "audit", False) or getattr(args, "check", False):
        if path:
            name = derive_repo_name(path, getattr(args, "name", None))
            entry = registry_entry_for(registry_path, name) or {
                "name": name, "path": path,
                "host": getattr(args, "host", None) or "dev1",
                "default_branch": None}
            _print_audit({name: audit_project(entry)})
        else:
            _print_audit(audit_registry(registry_path))
        return 0
    if not path:
        print("onboard-project: <path> required (or --audit for registry-wide "
              "drift)", file=sys.stderr)
        return 2
    result = onboard_project(
        path, host=getattr(args, "host", None), name=getattr(args, "name", None),
        overrides=getattr(args, "override", None) or [],
        registry_path=registry_path, dry_run=getattr(args, "dry_run", False))
    _print_result(result)
    return 0
