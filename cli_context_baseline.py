"""cli_context_baseline — measure always-on context per box/project + ratchet.

Subcommands:
    context-baseline [--fleet] [--json] [--project <dir>]
                     [--check] [--update-ratchet] [--allow-raise <reason>]

#857: new CLI leaf (Fable 5.0 design synthesis, comment 5547745895).
"""

import json
import os
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_MD = CLAUDE_DIR / "CLAUDE.md"

# Ratchet file (repo-checked, NOT registered with ratchet-union merge driver)
CONTEXT_RATCHET_PATH = REPO_DIR / "tests" / "context_ratchet.json"

# History dir on dev1
HISTORY_DIR = CLAUDE_DIR / "context-baseline"

# MCP flat estimate per enabled server (conservative, rough)
MCP_FLAT_ESTIMATE_BYTES = 2000

MAX_IMPORT_DEPTH = 5


def bytes_to_tokens(b):
    """tokens = bytes // 4 — the ONE function everywhere (stdlib-only,
    reproduces the owner's table: 329 116 B -> 82 279 tok)."""
    return b // 4


# -- @ import resolution ------------------------------------------------

def _resolve_import_path(line, base_dir=None):
    """Expand an @-import line to an absolute Path (or None)."""
    stripped = line.strip()
    if not stripped.startswith("@"):
        return None
    ref = stripped[1:]
    if ref.startswith("~/"):
        return Path(ref).expanduser()
    if ref.startswith("/"):
        return Path(ref)
    if base_dir:
        return (base_dir / ref).resolve()
    return None


def resolve_imports_recursive(filepath, depth=0, visited=None):
    """Resolve a CLAUDE.md-style file recursively, returning
    {path_str: byte_count} for all resolved files + a list of missing paths.

    depth cap = MAX_IMPORT_DEPTH, cycle guard via visited set.
    """
    if visited is None:
        visited = set()

    result = {}
    missing = []

    fp = Path(filepath).resolve()
    if fp in visited or depth > MAX_IMPORT_DEPTH:
        return result, missing
    visited.add(fp)

    if not fp.exists():
        missing.append(str(fp))
        return result, missing

    try:
        content = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        missing.append(str(fp))
        return result, missing

    try:
        result[str(fp)] = fp.stat().st_size
    except OSError:
        result[str(fp)] = len(content.encode("utf-8"))

    base_dir = fp.parent
    for line in content.splitlines():
        imp_path = _resolve_import_path(line, base_dir)
        if imp_path is None:
            continue
        sub_result, sub_missing = resolve_imports_recursive(
            imp_path, depth + 1, visited
        )
        result.update(sub_result)
        missing.extend(sub_missing)

    return result, missing


# -- Always-on rules (no paths: frontmatter) ----------------------------

def _has_paths_frontmatter(filepath):
    """True if the file has a YAML frontmatter block with a paths: key.
    Same parse shape as size_ratchet.tracked_rule_files."""
    try:
        head = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    stripped = head.lstrip()
    if not stripped.startswith("---"):
        return False
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return False
    front = parts[1]
    return any(ln.strip().startswith("paths:") for ln in front.splitlines())


def always_on_rule_files(project_dir):
    """Return list of .claude/rules/*.md files that are always-on
    (i.e. do NOT have a paths: frontmatter key)."""
    rules_dir = Path(project_dir) / ".claude" / "rules"
    if not rules_dir.is_dir():
        return []
    result = []
    for entry in sorted(rules_dir.glob("*.md")):
        if not _has_paths_frontmatter(entry):
            result.append(entry)
    return result


# -- Skill description measurement --------------------------------------

def _extract_skill_description(skill_path):
    """Extract the description: value from a SKILL.md frontmatter.
    Hand-parsed YAML scalar, stdlib only. Returns (chars, is_malformed)."""
    try:
        content = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, True

    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return 0, True

    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return 0, True

    front = parts[1]
    lines = front.splitlines()

    desc_value = None
    desc_idx = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("description:"):
            val = ln.split(":", 1)[1].strip()
            if val:
                if (val.startswith('"') and val.endswith('"')) or \
                   (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                desc_value = val
            desc_idx = i
            break

    if desc_value is None and desc_idx is not None:
        parts_ml = []
        for ln in lines[desc_idx + 1:]:
            if ln and (ln[0] == ' ' or ln[0] == '\t'):
                parts_ml.append(ln.strip())
            else:
                break
        if parts_ml:
            desc_value = " ".join(parts_ml)

    if desc_value is None:
        return 0, True

    return len(desc_value), False


def measure_skills(skills_dir=None):
    """Measure all skill descriptions.
    Returns {name: {"chars": N, "malformed": bool}}."""
    if skills_dir is None:
        skills_dir = CLAUDE_DIR / "skills"
    if not skills_dir.is_dir():
        return {}

    result = {}
    for entry in sorted(skills_dir.iterdir()):
        skill_file = entry / "SKILL.md"
        if not skill_file.exists():
            continue
        chars, malformed = _extract_skill_description(skill_file)
        result[entry.name] = {"chars": chars, "malformed": malformed}
    return result


# -- MCP estimate -------------------------------------------------------

def measure_mcp():
    """Rough estimate of MCP tool schema cost from ~/.claude.json.
    Returns {"estimate_bytes": N, "servers": N, "estimate": True}."""
    claude_json = Path.home() / ".claude.json"
    if not claude_json.exists():
        return {"estimate_bytes": 0, "servers": 0, "estimate": True}

    try:
        data = json.loads(claude_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"estimate_bytes": 0, "servers": 0, "estimate": True}

    servers = data.get("mcpServers", {})
    enabled = sum(1 for s in servers.values()
                  if not s.get("disabled", False))

    return {
        "estimate_bytes": enabled * MCP_FLAT_ESTIMATE_BYTES,
        "servers": enabled,
        "estimate": True,
    }


# -- MEMORY.md measurement ----------------------------------------------

def _memory_key(project_dir):
    """Derive the Claude Code projects/ key from an absolute dir path."""
    return str(Path(project_dir).resolve()).replace("/", "-")


def measure_memory(project_dir):
    """Measure the per-project MEMORY.md size (bytes)."""
    key = _memory_key(project_dir)
    mem_path = CLAUDE_DIR / "projects" / key / "memory" / "MEMORY.md"
    if not mem_path.exists():
        return 0
    try:
        return mem_path.stat().st_size
    except OSError:
        print(f"  warning: could not read {mem_path}", file=sys.stderr)
        return 0


# -- Per-box measurement ------------------------------------------------

def measure_box(project_dirs=None):
    """Measure context baseline for this box.
    Returns dict matching the --json schema (schema 1)."""
    import datetime
    import socket

    global_files, global_missing = {}, []
    if CLAUDE_MD.exists():
        global_files, global_missing = resolve_imports_recursive(CLAUDE_MD)
    global_bytes = sum(global_files.values())
    global_modules = len(global_files)

    skills_data = measure_skills()
    skill_desc_chars = sum(s["chars"] for s in skills_data.values())

    mcp = measure_mcp()

    projects = []
    if project_dirs:
        for pd in project_dirs:
            pd = Path(pd)
            proj_claude = pd / "CLAUDE.md"
            proj_files, _proj_missing = {}, []
            if proj_claude.exists():
                proj_files, _proj_missing = resolve_imports_recursive(
                    proj_claude)
            proj_claude_bytes = sum(proj_files.values())

            ao_rules = always_on_rule_files(pd)
            ao_bytes = 0
            for r in ao_rules:
                try:
                    ao_bytes += r.stat().st_size
                except OSError:
                    print(f"  warning: could not stat {r}", file=sys.stderr)

            mem_bytes = measure_memory(pd)

            total_proj = proj_claude_bytes + ao_bytes + mem_bytes
            projects.append({
                "dir": str(pd),
                "claude_md_bytes": proj_claude_bytes,
                "rules_always_on_bytes": ao_bytes,
                "memory_bytes": mem_bytes,
                "tokens": bytes_to_tokens(total_proj),
            })

    hostname = socket.gethostname()

    return {
        "schema": 1,
        "host": hostname,
        "date": datetime.date.today().isoformat(),
        "global": {
            "resolved_bytes": global_bytes,
            "tokens": bytes_to_tokens(global_bytes),
            "modules": global_modules,
            "missing": global_missing,
        },
        "skills": {
            "count": len(skills_data),
            "desc_chars": skill_desc_chars,
            "per_skill": skills_data,
        },
        "projects": projects,
        "mcp": mcp,
    }


# -- Ratchet ------------------------------------------------------------

def _measure_repo_ceilings():
    """Measure the repo-derivable dimensions for the ratchet ceiling check.

    Returns dict with keys matching context_ratchet.json ceilings:
      modules_resolved_bytes, skill_desc_chars, module_count
    """
    profile = REPO_DIR / "profiles" / "universal.profile"
    if not profile.exists():
        modules_dir = REPO_DIR / "modules"
        total = 0
        count = 0
        if modules_dir.is_dir():
            for f in modules_dir.rglob("*.md"):
                try:
                    total += f.stat().st_size
                except OSError:
                    print(f"  warning: could not stat {f}", file=sys.stderr)
                count += 1
        skills_dir = REPO_DIR / "skills"
        skill_data = measure_skills(skills_dir)
        desc_chars = sum(s["chars"] for s in skill_data.values())
        return {
            "modules_resolved_bytes": total,
            "skill_desc_chars": desc_chars,
            "module_count": count,
        }

    try:
        lines = profile.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        print(f"  warning: could not read profile: {e}", file=sys.stderr)
        return {"modules_resolved_bytes": 0, "skill_desc_chars": 0,
                "module_count": 0}

    total = 0
    count = 0
    missing_modules = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        mod_path = REPO_DIR / line
        if mod_path.exists():
            try:
                total += mod_path.stat().st_size
            except OSError:
                print(f"  warning: could not stat {mod_path}",
                      file=sys.stderr)
            count += 1
        else:
            missing_modules.append(line)
            print(f"  warning: missing profile module {line}",
                  file=sys.stderr)

    skills_dir = REPO_DIR / "skills"
    skill_data = measure_skills(skills_dir)
    desc_chars = sum(s["chars"] for s in skill_data.values())

    return {
        "modules_resolved_bytes": total,
        "skill_desc_chars": desc_chars,
        "module_count": count,
    }


def load_ratchet():
    """Load tests/context_ratchet.json."""
    if not CONTEXT_RATCHET_PATH.exists():
        return {"ceilings": {}}
    try:
        return json.loads(CONTEXT_RATCHET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  warning: could not load ratchet: {e}", file=sys.stderr)
        return {"ceilings": {}}


def save_ratchet(data):
    """Atomic write to tests/context_ratchet.json."""
    tmp = str(CONTEXT_RATCHET_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, CONTEXT_RATCHET_PATH)


def check_ratchet():
    """Check current repo measurements against committed ceilings.
    Returns (ok, details_list)."""
    ratchet = load_ratchet()
    ceilings = ratchet.get("ceilings", {})
    if not ceilings:
        return True, ["no ceilings defined"]

    current = _measure_repo_ceilings()
    results = []
    ok = True
    for key, ceiling in ceilings.items():
        actual = current.get(key, 0)
        if actual > ceiling:
            results.append(f"OVER: {key} = {actual} > ceiling {ceiling}")
            ok = False
        else:
            results.append(f"ok: {key} = {actual} <= {ceiling}")
    return ok, results


def update_ratchet(allow_raise=None):
    """Update ratchet ceilings. Only LOWERS by default.
    Returns (updated, details)."""
    ratchet = load_ratchet()
    ceilings = ratchet.get("ceilings", {})
    current = _measure_repo_ceilings()

    updated = False
    details = []
    for key, value in current.items():
        old = ceilings.get(key)
        if old is None:
            ceilings[key] = value
            details.append(f"new: {key} = {value}")
            updated = True
        elif value < old:
            ceilings[key] = value
            details.append(f"lowered: {key} {old} -> {value}")
            updated = True
        elif value > old:
            if allow_raise:
                ceilings[key] = value
                details.append(
                    f"RAISED: {key} {old} -> {value} (reason: {allow_raise})")
                updated = True
            else:
                details.append(
                    f"REFUSED raise: {key} {old} -> {value} "
                    f"(use --allow-raise '<reason>')")
        else:
            details.append(f"unchanged: {key} = {value}")

    if updated:
        ratchet["ceilings"] = ceilings
        save_ratchet(ratchet)

    return updated, details


# -- History snapshot ---------------------------------------------------

def save_snapshot(data, history_dir=None):
    """Save a dated snapshot to ~/.claude/context-baseline/<date>.json.
    Atomic tmp+os.replace."""
    if history_dir is None:
        history_dir = HISTORY_DIR
    history_dir = Path(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)

    date_str = data.get("date", "unknown")
    dest = history_dir / f"{date_str}.json"
    tmp = str(dest) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, dest)
    return dest


# -- Fleet --------------------------------------------------------------

def run_fleet(runner=None):
    """Run context-baseline --json on every deployable host.
    Returns the fleet JSON schema.

    runner: callable(host_entry) -> (stdout_str, returncode) for testing.
    """
    import datetime
    import subprocess
    import cli_remote

    hosts = cli_remote._deployable_hosts()
    boxes = []
    failed = []

    # Local box in-process (design: "Local box in-process")
    if not runner:
        local_data = measure_box()
        boxes.append(local_data)

    for host in hosts:
        name = host.get("name", host.get("host", "unknown"))
        try:
            if runner:
                stdout, rc = runner(host)
            else:
                addr = host.get("host", "")
                user = host.get("user", "newlevel")
                repo_path = host.get("repo_path",
                                     "~/devel/airuleset")
                ssh_base = ["ssh", "-o", "BatchMode=yes",
                            "-o", "ConnectTimeout=10",
                            "-o", "StrictHostKeyChecking=no",
                            f"{user}@{addr}"]
                cmd = ssh_base + [
                    "python3",
                    f"{repo_path}/airuleset.py",
                    "context-baseline", "--json"
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30)
                stdout = result.stdout
                rc = result.returncode

            if rc != 0:
                failed.append({"host": name, "error": f"rc={rc}"})
                continue

            box_data = json.loads(stdout)
            boxes.append(box_data)
        except (json.JSONDecodeError, OSError,
                subprocess.TimeoutExpired) as e:
            failed.append({"host": name, "error": type(e).__name__})

    return {
        "schema": 1,
        "date": datetime.date.today().isoformat(),
        "boxes": boxes,
        "failed": failed,
    }


# -- Push summary line --------------------------------------------------

def push_summary_line():
    """One-line summary for cmd_push:
    context-baseline: global <N>k tok (strop <B> check|cross)"""
    current = _measure_repo_ceilings()
    total_bytes = current.get("modules_resolved_bytes", 0)
    total_tok = bytes_to_tokens(total_bytes)

    ratchet = load_ratchet()
    ceilings = ratchet.get("ceilings", {})

    ceiling = ceilings.get("modules_resolved_bytes")
    if ceiling is not None:
        ok = total_bytes <= ceiling
        mark = "✓" if ok else "✗"
        return (f"context-baseline: global {total_tok / 1000:.1f}k tok "
                f"(strop {ceiling} {mark})")
    return f"context-baseline: global {total_tok / 1000:.1f}k tok (no ceiling)"


# -- CLI entry ----------------------------------------------------------

def cmd_context_baseline(args):
    """CLI handler for the context-baseline subcommand."""
    if getattr(args, "check", False):
        ok, details = check_ratchet()
        for d in details:
            print(d)
        sys.exit(0 if ok else 1)

    if getattr(args, "update_ratchet", False):
        allow_raise = getattr(args, "allow_raise", None)
        updated, details = update_ratchet(allow_raise=allow_raise)
        for d in details:
            print(d)
        if updated:
            print("Ratchet updated.")
        else:
            print("No changes.")
        return

    if getattr(args, "fleet", False):
        data = run_fleet()
        if getattr(args, "json_output", False):
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            _print_fleet_table(data)
        save_snapshot(data)
        return

    project_dirs = []
    if getattr(args, "project", None):
        project_dirs = [args.project]

    data = measure_box(project_dirs)

    if getattr(args, "json_output", False):
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _print_box_table(data)


def _print_box_table(data):
    """Human-readable table for a single box measurement."""
    g = data["global"]
    print(f"Global CLAUDE.md resolved: {g['resolved_bytes']:,} B "
          f"({g['tokens']:,} tokens, {g['modules']} modules)")
    if g["missing"]:
        for m in g["missing"]:
            print(f"  MISSING: {m}")

    s = data["skills"]
    print(f"Skills: {s['count']} installed, "
          f"{s['desc_chars']:,} description chars")

    mcp = data["mcp"]
    print(f"MCP: {mcp['servers']} servers, "
          f"~{mcp['estimate_bytes']:,} B estimate")

    for proj in data.get("projects", []):
        print(f"\nProject: {proj['dir']}")
        print(f"  CLAUDE.md: {proj['claude_md_bytes']:,} B")
        print(f"  Always-on rules: {proj['rules_always_on_bytes']:,} B")
        print(f"  MEMORY.md: {proj['memory_bytes']:,} B")
        print(f"  Total: {proj['tokens']:,} tokens")


def _print_fleet_table(data):
    """Human-readable table for fleet measurement."""
    for box in data.get("boxes", []):
        g = box.get("global", {})
        print(f"{box.get('host', '?')}: "
              f"{g.get('resolved_bytes', 0):,} B "
              f"({g.get('tokens', 0):,} tok)")
    for f in data.get("failed", []):
        print(f"FAILED: {f['host']} -- {f['error']}")
