"""cli_mdreview_audit — structured review input for the fleet-wide mdreview.

Subcommands:
    mdreview-audit [--fleet] [--json]

#858: new CLI leaf (Fable 5.0 design, comment 5548266874).
Produces a structured JSON artifact consumed by the /mdreview skill v2.
"""

import hashlib
import json
import os
import re
from pathlib import Path

import cli_context_baseline
import cli_fleet

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
ARTIFACT_DIR = CLAUDE_DIR / "mdreview-audit"

# Doctrine vocab for R (rule/procedure) classification in memory
_DOCTRINE_RE = re.compile(
    r"\b(always|never|nikdy|vždy|musí|must|ban|zakáz|povinn"
    r"|NEVER|ALWAYS|MUST|BANNED|FORBIDDEN)\b", re.IGNORECASE
)

# Secret patterns — reuse the exact cli_privileges shape (prefix-based)
_SECRET_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])tskey-api-[A-Za-z0-9]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])tskey-auth-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])tskey-[A-Za-z0-9]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])gho_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])xoxb-[0-9A-Za-z-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])cfat_[A-Za-z0-9_-]{20,}"),
]

# Code-fence boundary
_FENCE_RE = re.compile(r"^```")

# Sentence split (period/newline boundaries)
_SENT_SPLIT_RE = re.compile(r"[.!?\n]+")

# Markdown/punctuation stripping for normalization
_STRIP_RE = re.compile(r"[*_`#\[\]()>|~\-]")


def _normalize_sentence(s):
    """Lowercase, collapse whitespace, strip markdown/punctuation."""
    s = _STRIP_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _extract_sentences(text):
    """Extract normalized sentences from text, skipping fenced code blocks."""
    lines = text.splitlines()
    in_fence = False
    plain_lines = []
    for line in lines:
        if _FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if not in_fence:
            plain_lines.append(line)
    plain = "\n".join(plain_lines)
    raw_sents = _SENT_SPLIT_RE.split(plain)
    result = []
    for s in raw_sents:
        norm = _normalize_sentence(s)
        if len(norm) >= 40:
            result.append(norm)
    return result


def _sentence_hashes(text):
    """Return {sha1_hex: normalized_sentence} for sentences >= 40 chars."""
    sents = _extract_sentences(text)
    result = {}
    for s in sents:
        h = hashlib.sha1(s.encode("utf-8")).hexdigest()
        result[h] = s
    return result


# -- inventory_box ---------------------------------------------------------

def inventory_box(project_dirs=None):
    """Inventory always-on context for this box.

    Returns dict with:
      global_modules: {path: bytes}
      skills: {name: {body_bytes, desc_chars}}
      rules: {path: {bytes, has_paths}}
      projects: [{dir, claude_md_bytes, rules_bytes}]
    """
    # Global modules via cli_context_baseline
    global_files, global_missing = {}, []
    if cli_context_baseline.CLAUDE_MD.exists():
        global_files, global_missing = (
            cli_context_baseline.resolve_imports_recursive(
                cli_context_baseline.CLAUDE_MD))

    # Skill bodies
    skills_dir = CLAUDE_DIR / "skills"
    skills = {}
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            skill_file = entry / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                body_bytes = skill_file.stat().st_size
            except OSError:
                body_bytes = 0
            desc_chars, _ = cli_context_baseline._extract_skill_description(
                skill_file)
            skills[entry.name] = {
                "body_bytes": body_bytes,
                "desc_chars": desc_chars,
            }

    # Path-scoped rules
    rules = {}
    for pd in (project_dirs or []):
        rules_dir = Path(pd) / ".claude" / "rules"
        if not rules_dir.is_dir():
            continue
        for entry in sorted(rules_dir.glob("*.md")):
            has_paths = cli_context_baseline._has_paths_frontmatter(entry)
            try:
                sz = entry.stat().st_size
            except OSError:
                sz = 0
            rules[str(entry)] = {"bytes": sz, "has_paths": has_paths}

    # Projects
    projects = []
    if project_dirs:
        for pd in project_dirs:
            pd = Path(pd)
            proj_claude = pd / "CLAUDE.md"
            proj_files = {}
            if proj_claude.exists():
                proj_files, _ = cli_context_baseline.resolve_imports_recursive(
                    proj_claude)
            ao_rules = cli_context_baseline.always_on_rule_files(pd)
            ao_bytes = sum(r.stat().st_size for r in ao_rules
                          if r.exists())
            projects.append({
                "dir": str(pd),
                "claude_md_bytes": sum(proj_files.values()),
                "rules_bytes": ao_bytes,
            })

    return {
        "global_modules": {k: v for k, v in sorted(global_files.items())},
        "global_missing": global_missing,
        "skills": skills,
        "rules": rules,
        "projects": projects,
    }


# -- scoping_matrix --------------------------------------------------------

def scoping_matrix():
    """Build per-box role + profile/module/skill presence from fleet data.

    Returns list of {host, role, user} dicts.
    """
    by_host, _ = cli_context_baseline._load_registry()
    result = []
    for host_entry in cli_fleet.REMOTE_HOSTS:
        name = host_entry.get("name", host_entry.get("host", "unknown"))
        user = host_entry.get("user", "newlevel")
        # Derive role from authority map
        authority = cli_fleet.AUTHORITY_BY_USER.get(user, "full")
        if user == "gatekeeper":
            role = "gatekeeper"
        elif authority in ("fork-no-merge", "branch-merge"):
            role = "stream"
        else:
            role = "workstation"
        projects = by_host.get(name, [])
        result.append({
            "host": name,
            "role": role,
            "user": user,
            "projects": projects,
        })
    return result


# -- dedup_candidates -------------------------------------------------------

def dedup_candidates(files_by_surface):
    """Find cross-surface near-verbatim duplicates via exact normalized
    sentence hashing.

    files_by_surface: {"modules": {path: text}, "skills": {path: text},
                       "rules": {path: text}, "projects": {path: text}}

    Returns list of {surface_a, path_a, surface_b, path_b, shared_count,
                     sample_sentence} dicts — one per flagged pair.
    """
    # Build per-file hash sets, keyed by surface
    file_hashes = {}  # (surface, path) -> {hash: sentence}
    for surface, files in files_by_surface.items():
        for path, text in files.items():
            hashes = _sentence_hashes(text)
            file_hashes[(surface, path)] = hashes

    # Cross-surface pairs only
    pairs = []
    keys = list(file_hashes.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            surf_a, path_a = keys[i]
            surf_b, path_b = keys[j]
            if surf_a == surf_b:
                continue  # same-surface = not flagged
            hashes_a = file_hashes[keys[i]]
            hashes_b = file_hashes[keys[j]]
            shared = set(hashes_a.keys()) & set(hashes_b.keys())
            if not shared:
                continue
            # Flag if >= 2 shared hashes OR 1 hash of >= 120-char sentence
            long_shared = [h for h in shared
                           if len(hashes_a[h]) >= 120]
            if len(shared) >= 2 or long_shared:
                sample = hashes_a[next(iter(shared))]
                # 🔵5 RE-REVIEW: sweep sample_sentence for secrets
                safe_sample = sample[:200]
                for sp in _SECRET_PATTERNS:
                    safe_sample = sp.sub("<REDACTED>", safe_sample)
                pairs.append({
                    "surface_a": surf_a,
                    "path_a": path_a,
                    "surface_b": surf_b,
                    "path_b": path_b,
                    "shared_count": len(shared),
                    "sample_sentence": safe_sample,
                })
    return pairs


# -- memory_candidates ------------------------------------------------------

def _classify_memory_line(line):
    """Classify a memory bullet as R (rule/procedure), P (fact/preference),
    or S (credential-like). Returns (class, is_credential)."""
    stripped = line.strip().lstrip("- ").strip()
    if not stripped:
        return None, False

    is_credential = any(p.search(stripped) for p in _SECRET_PATTERNS)

    # R check — doctrine vocab
    if _DOCTRINE_RE.search(stripped):
        return "R", is_credential

    return "P", is_credential


def memory_candidates(memory_dir):
    """Scan a memory directory for R/P/S classification.

    Returns {
        "R": [{"line": <first 200 chars>, "file": path, "target": proposal}],
        "P": [{"line": <first 200 chars>, "file": path}],
        "S_flag_count": int,
        "candidates": [{"line": <first 200 chars, value NEVER printed>,
                         "file": path, "class": "R"|"P"|"S"}]
    }
    """
    memory_dir = Path(memory_dir)
    r_items = []
    p_items = []
    s_count = 0
    candidates = []

    files_to_scan = []
    main_mem = memory_dir / "MEMORY.md"
    if main_mem.exists():
        files_to_scan.append(main_mem)
    # Topic files
    for f in sorted(memory_dir.glob("*.md")):
        if f.name != "MEMORY.md":
            files_to_scan.append(f)

    for fpath in files_to_scan:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            cls, is_cred = _classify_memory_line(line)
            if cls is None:
                continue

            safe_line = stripped
            if is_cred:
                safe_line = re.sub(
                    r"[A-Za-z0-9_\-]{20,}", "<REDACTED>", safe_line)
                s_count += 1
            safe_line = safe_line[:200]

            entry = {"line": safe_line, "file": str(fpath)}

            if cls == "R":
                target = _propose_target(stripped)
                entry["target"] = target
                r_items.append(entry)
                candidates.append({**entry, "class": "R"})
            else:
                p_items.append(entry)
                candidates.append({**entry, "class": "P"})

    return {
        "R": r_items,
        "P": p_items,
        "S_flag_count": s_count,
        "candidates": candidates,
    }


def _propose_target(text):
    """Propose where a rule-classified memory line should live."""
    text_lower = text.lower()
    # Fleet/global vocab → managed module
    if any(w in text_lower for w in
           ("fleet", "deploy", "push", "install", "remote_hosts",
            "all targets", "all boxes", "every box", "všetky targety")):
        return "managed module"
    # Project/client/stream names → project paths: rule
    if any(w in text_lower for w in
           ("odoo", "montalu", "david", "marek", "miva", "camera",
            "restreamer", "presenter", "bakerion", "forestshop")):
        return "project paths: rule hand-off"
    # Block/deny phrasing → hook
    if any(w in text_lower for w in
           ("block", "deny", "refuse", "reject", "ban", "forbidden")):
        return "hook"
    return "managed module"


# -- dedup surface collection -----------------------------------------------

def _collect_dedup_surfaces(project_dirs=None):
    """Read actual file texts into surface buckets for dedup_candidates."""
    surfaces = {"modules": {}, "skills": {}, "rules": {}, "projects": {}}

    if cli_context_baseline.CLAUDE_MD.exists():
        files, _ = cli_context_baseline.resolve_imports_recursive(
            cli_context_baseline.CLAUDE_MD)
        for fpath in files:
            try:
                text = Path(fpath).read_text(encoding="utf-8", errors="replace")
                surfaces["modules"][fpath] = text
            except OSError:
                pass  # airuleset:script-ok unreadable resolved module

    skills_dir = CLAUDE_DIR / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            skill_file = entry / "SKILL.md"
            if skill_file.exists():
                try:
                    text = skill_file.read_text(encoding="utf-8",
                                                errors="replace")
                    surfaces["skills"][str(skill_file)] = text
                except OSError:
                    pass  # airuleset:script-ok unreadable skill

    for pd in (project_dirs or []):
        pd = Path(pd)
        ao_rules = cli_context_baseline.always_on_rule_files(pd)
        for r in ao_rules:
            try:
                text = r.read_text(encoding="utf-8", errors="replace")
                surfaces["rules"][str(r)] = text
            except OSError:
                pass  # airuleset:script-ok unreadable rule

        proj_claude = pd / "CLAUDE.md"
        if proj_claude.exists():
            try:
                text = proj_claude.read_text(encoding="utf-8",
                                             errors="replace")
                surfaces["projects"][str(proj_claude)] = text
            except OSError:
                pass  # airuleset:script-ok unreadable project CLAUDE.md

    return surfaces


def _compute_zero_caller_skills(days=90):
    """Return list of skill names with 0 calls in the given window."""
    try:
        import cli_skill_usage
        usage = cli_skill_usage.scan_usage(days=days)
    except Exception:
        return []  # airuleset:script-ok scan_usage unavailable

    skills_dir = CLAUDE_DIR / "skills"
    if not skills_dir.is_dir():
        return []

    all_skills = set()
    for entry in sorted(skills_dir.iterdir()):
        if (entry / "SKILL.md").exists():
            all_skills.add(entry.name)

    # 🟡 RE-REVIEW: include slash-only skills — a skill invoked via /slash
    # (usage["slash"]) is NOT zero-caller even if absent from usage["skills"].
    called_skills = set(usage.get("skills", {}).keys()) | set(
        usage.get("slash", {}).keys())
    zero_callers = sorted(all_skills - called_skills)
    return zero_callers


def _scan_memory_all():
    """Scan all project memory directories. Returns aggregated result."""
    mem_dir = CLAUDE_DIR / "projects"
    mem_result = {"R": [], "P": [], "S_flag_count": 0, "candidates": []}
    if mem_dir.is_dir():
        for pdir in sorted(mem_dir.iterdir()):
            mem_sub = pdir / "memory"
            if mem_sub.is_dir():
                sub_result = memory_candidates(mem_sub)
                mem_result["R"].extend(sub_result["R"])
                mem_result["P"].extend(sub_result["P"])
                mem_result["S_flag_count"] += sub_result["S_flag_count"]
                mem_result["candidates"].extend(sub_result["candidates"])
    return mem_result


# -- run_fleet --------------------------------------------------------------

def run_fleet(runner=None, fleet_runner=None):
    """Run mdreview-audit --json on every deployable host.

    runner: legacy alias for fleet_runner (backward compat).
    fleet_runner: callable(host_entry) -> (stdout_str, returncode).
    """
    import datetime
    import subprocess
    import cli_remote

    if fleet_runner is None and runner is not None:
        fleet_runner = runner

    hosts = cli_remote._deployable_hosts()
    boxes = []
    failed = []

    import socket
    if not fleet_runner:
        by_host, _ = cli_context_baseline._load_registry()
        local_hostname = socket.gethostname()
        local_projects = by_host.get(local_hostname, [])
        inv = inventory_box(local_projects)
        mem_result = _scan_memory_all()
        surfaces = _collect_dedup_surfaces(local_projects)
        pairs = dedup_candidates(surfaces)
        zero_callers = _compute_zero_caller_skills()

        boxes.append({
            "host": local_hostname,
            "inventory": inv,
            "dedup_pairs": pairs,
            "memory": mem_result,
            "zero_caller_skills": zero_callers,
        })

    for host in hosts:
        name = host.get("name", host.get("host", "unknown"))
        try:
            if fleet_runner:
                stdout, rc = fleet_runner(host)
            else:
                addr = host.get("host", "")
                user = host.get("user", "newlevel")
                repo_path = host.get("repo_path", "~/devel/airuleset")
                # 🟡 RE-REVIEW finding 14: use host_key_check_opts
                # so a host with host_keys gets StrictHostKeyChecking=yes.
                hk_opts = cli_remote.host_key_check_opts(host)
                ssh_base = ["ssh", "-o", "BatchMode=yes",
                            "-o", "ConnectTimeout=10"] + hk_opts + [
                            f"{user}@{addr}"]
                cmd = ssh_base + [
                    "python3",
                    f"{repo_path}/airuleset.py",
                    "mdreview-audit", "--json"
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60)
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

    result_data = {
        "schema": 1,
        "date": datetime.date.today().isoformat(),
        "boxes": boxes,
        "failed": failed,
        "scoping": scoping_matrix(),
    }
    return result_data


def save_artifact(data):
    """Save the audit artifact atomically to ~/.claude/mdreview-audit/<date>.json."""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = data.get("date", "unknown")
    dest = ARTIFACT_DIR / f"{date_str}.json"
    tmp = str(dest) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, dest)
    return dest


# -- CLI entry --------------------------------------------------------------

def cmd_mdreview_audit(args):
    """CLI handler for the mdreview-audit subcommand."""
    if getattr(args, "fleet", False):
        data = run_fleet()
        if getattr(args, "json_output", False):
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            _print_fleet_table(data)
        save_artifact(data)
        return

    import socket
    import datetime
    by_host, _ = cli_context_baseline._load_registry()
    hostname = socket.gethostname()
    project_dirs = by_host.get(hostname, [])

    inv = inventory_box(project_dirs)
    mem_result = _scan_memory_all()
    surfaces = _collect_dedup_surfaces(project_dirs)
    pairs = dedup_candidates(surfaces)
    zero_callers = _compute_zero_caller_skills()

    data = {
        "schema": 1,
        "host": hostname,
        "date": datetime.date.today().isoformat(),
        "inventory": inv,
        "dedup_pairs": pairs,
        "memory": mem_result,
        "zero_caller_skills": zero_callers,
    }

    if getattr(args, "json_output", False):
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _print_box_table(data)


def _print_box_table(data):
    """Human-readable output for a single box."""
    inv = data.get("inventory", {})
    print(f"Global modules: {len(inv.get('global_modules', {}))}")
    print(f"Skills: {len(inv.get('skills', {}))}")
    print(f"Rules: {len(inv.get('rules', {}))}")
    mem = data.get("memory", {})
    print(f"Memory: {len(mem.get('R', []))} rules, "
          f"{len(mem.get('P', []))} facts, "
          f"{mem.get('S_flag_count', 0)} credential flags")


def _print_fleet_table(data):
    """Human-readable output for fleet audit."""
    for box in data.get("boxes", []):
        mem = box.get("memory", {})
        print(f"{box.get('host', '?')}: "
              f"R={len(mem.get('R', []))} P={len(mem.get('P', []))} "
              f"S={mem.get('S_flag_count', 0)}")
    for f in data.get("failed", []):
        print(f"FAILED: {f['host']} -- {f['error']}")
