"""cli_target_governance — TARGET-GOVERNANCE inventory for the fleet mdreview (#874).

Split VERBATIM out of cli_mdreview_audit.py (#993 area-review verdict: the audit
module was over the ~1000-line budget once the inventory landed). This leaf owns
what a TARGET project adds to its OWN `.claude/` — the pinned Claude Code
built-in slash-command set, the classifier constants, `target_governance()` and
the delta helpers. `cli_mdreview_audit` re-exports every public name, so every
existing import path (incl. the gate's `from cli_mdreview_audit import
CLAUDE_CODE_BUILTIN_COMMANDS` SSOT reference) keeps working unchanged.

STDLIB ONLY + `cli_context_baseline` (a cheap leaf) — no watchdog/notify.
"""

import json
import re
from pathlib import Path

import cli_context_baseline

REPO_DIR = Path(__file__).resolve().parent

# -- Target governance (#874) ---------------------------------------------
# Claude Code built-in slash commands. A target project's
# `.claude/commands/<name>.md` or `.claude/skills/<name>/SKILL.md` that reuses
# one of these names silently SHADOWS the built-in on every checkout (the
# odoo-erp `/resume` incident, 67 days invisible). Pinned + SOURCED so a future
# /mdreview pass re-verifies the list against the live `claude --help`/docs.
CLAUDE_CODE_BUILTIN_COMMANDS_SOURCE = (
    "Claude Code v2.1.268 — EXTRACTED from the installed bundle's slash-command "
    "registry (every `type:\"local\"|\"local-jsx\"|\"prompt\"` command descriptor "
    "under ~/.local/share/claude/versions/2.1.268), NAMES + ALIASES (e.g. "
    "`continue` is an alias of `resume` — the exact incident class the guard must "
    "catch), curated to the user-facing set (internal/setup/telemetry descriptors "
    "such as heapdump/daemon/setup-bedrock excluded). `claude --help` alone lists "
    "CLI SUBcommands, not slash commands, so it is NOT the source. Re-extract from "
    "the live bundle each /mdreview pass; recorded 2026-09-18 (#874)."
)
CLAUDE_CODE_BUILTIN_COMMANDS = frozenset({
    "add-dir", "advisor", "agents", "allowed-tools", "android", "app",
    "artifacts", "background", "bashes", "bg", "branch", "break-reminder",
    "breaks", "bug", "checkpoint", "clear", "compact", "config",
    "context", "continue", "copy", "cost", "desktop", "diff",
    "downtime", "effort", "exit", "export", "fast", "feedback",
    "focus", "fork", "goal", "help", "hooks", "ide",
    "import", "init", "insights", "install", "ios", "keybindings",
    "list-agents", "login", "logout", "loops", "marketplace", "mcp",
    "memory", "memory-pause", "mobile", "model", "name", "pause-memory",
    "peers", "permissions", "plan", "plugin", "plugins", "privacy-settings",
    "quit", "rc", "recap", "remote", "remote-control", "rename",
    "restart", "resume", "review", "rewind", "session", "settings",
    "share", "skill-doctor", "skills", "stats", "status", "stop",
    "tasks", "teleport", "terminal-setup", "theme", "toggle-memory", "undo",
    "update", "upgrade", "usage", "version", "voice", "wellbeing",
    "workflows",
})
_BUILTINS_LOWER = frozenset(c.lower() for c in CLAUDE_CODE_BUILTIN_COMMANDS)

# Governance classifier constants (#874).
CLASS_BUILTIN_COLLISION = "BUILTIN-COLLISION"   # name == a Claude Code built-in
CLASS_MANAGED_DUPLICATE = "MANAGED-DUPLICATE"   # name == an airuleset skill/rule
CLASS_UNREVIEWED = "UNREVIEWED"                 # new/changed since the last run
CLASS_RULE_SHAPE = "RULE-SHAPE"                 # malformed rule frontmatter

# -- Target governance inventory (#874) -----------------------------------

def _provenance_map_default(project_dir):
    """First-commit (sha, date, author) for EVERY file added under `.claude/`,
    in ONE git pass — walked newest→oldest so the OLDEST add wins (the true
    first-commit). Returns {rel_path: (sha, date, author)}; {} on any failure.

    #874 review-3 cost fix: this replaces N per-file `git log --follow` calls
    (15 s timeout EACH) with ONE bounded call, so a project with many governed
    files never blows `run_fleet`'s 60 s per-host SSH budget. Full `%H` sha
    (not abbreviated `%h`) so a repo growing its abbrev length never flips a
    spurious "changed" delta. Repo-relative output paths are made relative to
    ``project_dir`` via the git prefix, so a sub-directory checkout still keys
    correctly (best-effort: an unresolved path just yields empty provenance)."""
    import subprocess

    def _run(args):
        try:
            return subprocess.run(
                ["git", "-C", str(project_dir)] + args,
                capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None

    pref = _run(["rev-parse", "--show-prefix"])
    prefix = pref.stdout.strip() if (pref and pref.returncode == 0) else ""
    r = _run(["log", "--diff-filter=A", "--name-only",
              "--format=%x00%H%x00%ad%x00%an", "--date=short",
              "--", ".claude"])
    if not r or r.returncode != 0:
        return {}
    result = {}
    sha = date = author = ""
    for line in r.stdout.splitlines():
        if line.startswith("\x00"):
            parts = line.split("\x00")
            sha = parts[1] if len(parts) > 1 else ""
            date = parts[2] if len(parts) > 2 else ""
            author = parts[3] if len(parts) > 3 else ""
        elif line.strip():
            path = line.strip()
            if prefix and path.startswith(prefix):
                path = path[len(prefix):]
            result[path] = (sha, date, author)  # newest→oldest: oldest wins
    return result


def _first_heading(path):
    """First markdown heading text in a file, or ''."""
    try:
        for line in path.read_text(
                encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith("#"):
                return s.lstrip("#").strip()[:120]
    except OSError:
        return ""
    return ""


def _managed_skill_names():
    """Skill names airuleset itself ships (REPO_DIR/skills/*/SKILL.md)."""
    names = set()
    sd = REPO_DIR / "skills"
    if sd.is_dir():
        for e in sd.iterdir():
            if (e / "SKILL.md").exists():
                names.add(e.name)
    return names


def _managed_rule_names():
    """Rule file names airuleset itself ships (REPO_DIR/rules/*.md)."""
    rd = REPO_DIR / "rules"
    if rd.is_dir():
        return {e.name for e in rd.glob("*.md")}
    return set()


def _cmd_basename(cmd):
    """A stable short id for a settings-hook command (its script basename)."""
    for tok in cmd.split():
        if tok.endswith(".sh") or "/" in tok:
            return tok.rsplit("/", 1)[-1]
    toks = cmd.split()
    return toks[-1] if toks else cmd


def _cmd_script_token(cmd):
    """The script-path token from a hook command string (the first token that
    looks like a path / .sh), or ''. e.g. `bash ~/x/hooks/y.sh --flag` → the
    `~/x/hooks/y.sh` token."""
    for tok in cmd.split():
        if tok.endswith(".sh") or "/" in tok:
            return tok
    return ""


def _cmd_is_airuleset_managed(cmd, project_dir, airuleset_root):
    """True when a settings-hook command invokes an airuleset-managed hook —
    the script token RESOLVES under ~/devel/airuleset (#874 review-2 fix:
    a naive ``"devel/airuleset" in cmd`` substring wrongly treats a sibling
    like ~/devel/airuleset-fork/hooks/x.sh as managed and hides it). A path
    is resolved against the project dir when relative, and ~ is expanded."""
    tok = _cmd_script_token(cmd)
    if not tok:
        return False
    tok = tok.strip('"').strip("'")
    try:
        p = Path(tok).expanduser()
        if not p.is_absolute():
            p = (Path(project_dir) / p)
        resolved = p.resolve()
    except (OSError, ValueError):
        return False
    return _is_under(resolved, airuleset_root)


def _is_under(path, root):
    """True if ``path`` resolves inside ``root``."""
    try:
        Path(path).resolve().relative_to(Path(root))
        return True
    except (ValueError, OSError):
        return False


def _bad_frontmatter(path):
    """True if the file OPENS a YAML frontmatter block but never closes it
    (malformed rule frontmatter → RULE-SHAPE). To avoid a false positive on a
    file that merely STARTS with a `---` markdown horizontal rule (#874
    review-1), it is treated as frontmatter ONLY when the FIRST physical line
    is exactly `---` AND the SECOND line looks like a YAML `key:` — then a
    missing closing `---` is the RULE-SHAPE defect."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    # second non-empty line must look like a YAML key to be frontmatter
    second = ""
    for ln in lines[1:]:
        if ln.strip():
            second = ln
            break
    if not re.match(r"^[A-Za-z0-9_-]+\s*:", second.strip()):
        return False
    # it IS frontmatter — flagged only when the closing fence is missing
    return not any(ln.strip() == "---" for ln in lines[1:])


def _prov_dict(triple):
    sha, date, author = (list(triple) + ["", "", ""])[:3]
    return {"sha": sha, "date": date, "author": author}


def target_governance(project_dir, git_fn=None):
    """Inventory what a TARGET project adds to its OWN `.claude/` (#874).

    Returns ``{dir, repo, items}`` where each item is
    ``{repo, kind, name, classes:[...], provenance:{sha,date,author}, detail}``
    and ``kind`` ∈ {command, skill, hook, settings-hook, rule, import}.

    Classifier (stored at inventory time; UNREVIEWED is added later at delta
    time): BUILTIN-COLLISION (name == a Claude Code built-in), MANAGED-DUPLICATE
    (name == an airuleset-shipped skill/rule), RULE-SHAPE (malformed rule
    frontmatter).

    ``git_fn(project_dir, rel_path) -> (sha, date, author)`` is injected in
    tests; defaults to a real timeout-bounded, error-swallowing git call.
    """
    pd = Path(project_dir)
    repo = pd.name
    claude = pd / ".claude"
    items = []
    managed_skills = _managed_skill_names()
    managed_rules = _managed_rule_names()

    # Provenance resolver. Tests inject a per-file ``git_fn(pd, rel)``; prod
    # builds the whole-.claude map in ONE git pass (the #874 review-3 cost fix).
    if git_fn is not None:
        def prov(rel):
            return _prov_dict(git_fn(pd, rel))
    else:
        _pmap = _provenance_map_default(pd)

        def prov(rel):
            return _prov_dict(_pmap.get(rel, ("", "", "")))

    airuleset_root = (Path.home() / "devel" / "airuleset").resolve()

    # slash commands
    cdir = claude / "commands"
    if cdir.is_dir():
        for f in sorted(cdir.glob("*.md")):
            name = f.stem
            classes = []
            if name.lower() in _BUILTINS_LOWER:
                classes.append(CLASS_BUILTIN_COLLISION)
            try:
                mtime = f.stat().st_mtime
            except OSError:
                mtime = 0
            items.append({
                "repo": repo, "kind": "command", "name": name,
                "classes": classes,
                "provenance": prov(f".claude/commands/{f.name}"),
                "detail": {"mtime": mtime, "first_heading": _first_heading(f)},
            })

    # skills
    sdir = claude / "skills"
    if sdir.is_dir():
        for e in sorted(sdir.iterdir()):
            if not (e / "SKILL.md").exists():
                continue
            name = e.name
            classes = []
            if name.lower() in _BUILTINS_LOWER:
                classes.append(CLASS_BUILTIN_COLLISION)
            if name in managed_skills:
                classes.append(CLASS_MANAGED_DUPLICATE)
            items.append({
                "repo": repo, "kind": "skill", "name": name,
                "classes": classes,
                "provenance": prov(f".claude/skills/{name}/SKILL.md"),
                "detail": {},
            })

    # project-owned hook files
    hdir = claude / "hooks"
    if hdir.is_dir():
        for f in sorted(hdir.iterdir()):
            if not f.is_file():
                continue
            items.append({
                "repo": repo, "kind": "hook", "name": f.name,
                "classes": [],
                "provenance": prov(f".claude/hooks/{f.name}"),
                "detail": {},
            })

    # settings*.json hook entries NOT managed by airuleset
    for sname in ("settings.json", "settings.local.json"):
        sfile = claude / sname
        if not sfile.exists():
            continue
        try:
            cfg = json.loads(sfile.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if not isinstance(cfg, dict):
            continue
        hooks_cfg = cfg.get("hooks")
        if not isinstance(hooks_cfg, dict):
            continue
        for event, blocks in sorted(hooks_cfg.items()):
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                matcher = block.get("matcher", "")
                for h in block.get("hooks", []) or []:
                    if not isinstance(h, dict):
                        continue
                    cmd = h.get("command", "") or ""
                    if not cmd or _cmd_is_airuleset_managed(cmd, pd,
                                                            airuleset_root):
                        continue  # empty or airuleset-managed — skip
                    items.append({
                        "repo": repo, "kind": "settings-hook",
                        "name": f"{sname}:{event}:{matcher}:{_cmd_basename(cmd)}",
                        "classes": [],
                        "provenance": prov(f".claude/{sname}"),
                        "detail": {"command": cmd, "event": event,
                                   "matcher": matcher, "file": sname},
                    })

    # path-scoped / always-on rules
    rdir = claude / "rules"
    if rdir.is_dir():
        for f in sorted(rdir.glob("*.md")):
            classes = []
            has_paths = cli_context_baseline._has_paths_frontmatter(f)
            if _bad_frontmatter(f):
                classes.append(CLASS_RULE_SHAPE)
            if f.name in managed_rules:
                classes.append(CLASS_MANAGED_DUPLICATE)
            try:
                sz = f.stat().st_size
            except OSError:
                sz = 0
            items.append({
                "repo": repo, "kind": "rule", "name": f.name,
                "classes": classes,
                "provenance": prov(f".claude/rules/{f.name}"),
                "detail": {"has_paths": has_paths, "bytes": sz},
            })

    # CLAUDE.md @imports outside ~/devel/airuleset
    claude_md = pd / "CLAUDE.md"
    if claude_md.exists():
        try:
            text = claude_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            imp = cli_context_baseline._resolve_import_path(
                line, claude_md.parent)
            if imp is None:
                continue
            try:
                resolved = imp.resolve()
            except OSError:
                resolved = imp
            if _is_under(resolved, airuleset_root):
                continue
            ref = line.strip()[1:].strip()  # strip leading '@'
            items.append({
                "repo": repo, "kind": "import", "name": ref,
                "classes": [],
                "provenance": {"sha": "", "date": "", "author": ""},
                "detail": {"ref": ref, "resolved": str(resolved)},
            })

    return {"dir": str(pd), "repo": repo, "items": items}


# -- Target governance delta (#874) ---------------------------------------

def _tg_key(repo, kind, name):
    return "\x1f".join((repo, kind, name))


def collect_target_governance(audit_data):
    """Flatten target_governance items across every box of an audit artifact
    into ``{key: item}``, deduped by (repo, kind, name). Handles BOTH the
    fleet (``{boxes:[...]}``) and single-box (``{inventory:...}``) shapes.
    When the same item appears on several boxes (a repo checked out on N
    boxes), classes are unioned and the provenance carrying a sha wins."""
    result = {}

    def _ingest(inv):
        if not isinstance(inv, dict):
            return
        for proj in inv.get("target_governance", []) or []:
            for it in (proj or {}).get("items", []) or []:
                key = _tg_key(it.get("repo", ""), it.get("kind", ""),
                              it.get("name", ""))
                cur = result.get(key)
                if cur is None:
                    result[key] = {
                        "repo": it.get("repo", ""),
                        "kind": it.get("kind", ""),
                        "name": it.get("name", ""),
                        "classes": sorted(set(it.get("classes", []))),
                        "provenance": dict(it.get("provenance", {})),
                        "detail": dict(it.get("detail", {})),
                    }
                else:
                    cur["classes"] = sorted(
                        set(cur["classes"]) | set(it.get("classes", [])))
                    if not cur["provenance"].get("sha") and \
                            it.get("provenance", {}).get("sha"):
                        cur["provenance"] = dict(it["provenance"])

    boxes = audit_data.get("boxes")
    if isinstance(boxes, list):
        for box in boxes:
            _ingest((box or {}).get("inventory", {}))
    else:
        _ingest(audit_data.get("inventory", {}))
    return result


def governance_snapshot(gmap):
    """Durable snapshot form for the cadence state file: ``{key: {classes, sha}}``.
    Host-set is deliberately excluded so a box pulling a fix (fewer checkouts
    carrying an item) never reads as a 'change'."""
    return {
        k: {"classes": sorted(v.get("classes", [])),
            "sha": v.get("provenance", {}).get("sha", "")}
        for k, v in gmap.items()
    }


_MAX_DELTA_LINES = 300
_MAX_DELTA_BYTES = 60000   # GitHub's comment limit is ~65 536 chars; stay under


def _delta_line(item):
    classes = item.get("classes") or [CLASS_UNREVIEWED]
    prov = item.get("provenance", {})
    sha = prov.get("sha") or "?"
    date = prov.get("date") or "?"
    author = prov.get("author") or "?"
    return (f"- {item['repo']} {item['kind']} {item['name']} "
            f"[{', '.join(classes)}] (added {sha} {date} by {author})")


def target_governance_delta(current_map, previous_snapshot, date_str):
    """Diff the current governance map against the previous snapshot.

    Returns ``{count, collisions, comment}``. An item is new/changed when its
    key is absent from the snapshot, or its sha/classes differ. ``collisions``
    counts new/changed items carrying BUILTIN-COLLISION or MANAGED-DUPLICATE.
    ``comment`` is None when nothing changed (journal-only day)."""
    prev = previous_snapshot or {}
    changed = []
    for key, item in sorted(current_map.items()):
        old = prev.get(key)
        cur_classes = sorted(item.get("classes", []))
        cur_sha = item.get("provenance", {}).get("sha", "")
        if old is None:
            changed.append(item)
        elif sorted(old.get("classes", [])) != cur_classes or \
                old.get("sha", "") != cur_sha:
            changed.append(item)

    collisions = sum(
        1 for it in changed
        if set(it.get("classes", [])) &
        {CLASS_BUILTIN_COLLISION, CLASS_MANAGED_DUPLICATE})

    if not changed:
        return {"count": 0, "collisions": 0, "comment": None}

    header = (f"Target-governance delta {date_str}: {len(changed)} new/changed, "
              f"{collisions} collision{'' if collisions == 1 else 's'}")
    all_lines = [_delta_line(it) for it in changed]
    # Cap by BOTH line count and byte size (a first-run "full snapshot" across
    # many projects, or long import/settings-hook names, must never exceed
    # GitHub's ~65 KB comment limit — #874 review).
    lines = []
    used = len(header) + 2
    for i, ln in enumerate(all_lines):
        if i >= _MAX_DELTA_LINES or used + len(ln) + 1 > _MAX_DELTA_BYTES:
            lines.append(f"- … and {len(all_lines) - i} more "
                         "(see the full artifact)")
            break
        lines.append(ln)
        used += len(ln) + 1
    comment = header + "\n\n" + "\n".join(lines)
    return {"count": len(changed), "collisions": collisions, "comment": comment}
