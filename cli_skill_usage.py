"""cli_skill_usage — scan transcript jsonl for Skill tool_use + slash commands.

Subcommand:
    skill-usage [--fleet] [--json] [--days N]

#857: new CLI leaf (Fable 5.0 design synthesis, comment 5547745895).

The Skill tool_use input key is: input.skill (string).
Pinned by a RED test from a real captured transcript record.
"""

import json
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"

DEFAULT_DAYS = 60

# Prefilter substrings for line-level fast skip (before json.loads)
_SKILL_PREFILTER = '"Skill"'
_SLASH_PREFILTER = '<command-name>'


def bytes_to_tokens(b):
    """tokens = bytes // 4 — shared with cli_context_baseline."""
    return b // 4


def scan_usage(days=DEFAULT_DAYS, projects_dir=None):
    """Scan ~/.claude/projects/*/*.jsonl for Skill tool_use and slash commands.

    Returns the skill-usage --json schema (schema 1):
    {
        "schema": 1,
        "host": str,
        "days": int,
        "window_start": str,
        "scanned_files": int,
        "skills": {name: {"calls": N, "sessions": N}},
        "slash": {name: {"calls": N, "sessions": N}},
    }
    """
    import datetime
    import socket

    if projects_dir is None:
        projects_dir = PROJECTS_DIR

    projects_dir = Path(projects_dir)
    if not projects_dir.is_dir():
        return _empty_result(days)

    cutoff_ts = time.time() - days * 86400
    cutoff_iso = datetime.datetime.fromtimestamp(
        cutoff_ts, tz=datetime.timezone.utc
    ).isoformat()

    skills = {}   # name -> {"calls": N, "sessions": set()}
    slash = {}    # name -> {"calls": N, "sessions": set()}
    scanned = 0

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            # mtime window: skip files older than cutoff
            try:
                mtime = jsonl_file.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff_ts:
                continue

            scanned += 1
            session_id = jsonl_file.stem

            try:
                with open(jsonl_file, "r", encoding="utf-8",
                          errors="replace") as f:
                    for raw_line in f:
                        # Substring prefilter before json.loads
                        has_skill = _SKILL_PREFILTER in raw_line
                        has_slash = _SLASH_PREFILTER in raw_line
                        if not has_skill and not has_slash:
                            continue

                        try:
                            rec = json.loads(raw_line)
                        except json.JSONDecodeError:
                            continue

                        # Check timestamp if present
                        ts = rec.get("timestamp")
                        if ts and ts < cutoff_iso:
                            continue

                        msg = rec.get("message", {})
                        role = msg.get("role", "")

                        # Skill tool_use: assistant message with tool_use
                        if has_skill and role == "assistant":
                            for item in msg.get("content", []):
                                if (item.get("type") == "tool_use"
                                        and item.get("name") == "Skill"):
                                    inp = item.get("input", {})
                                    skill_name = inp.get("skill", "")
                                    if skill_name:
                                        entry = skills.setdefault(
                                            skill_name,
                                            {"calls": 0, "sessions": set()})
                                        entry["calls"] += 1
                                        entry["sessions"].add(session_id)

                        # Slash command: user message with <command-name>
                        if has_slash and role == "user":
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                _extract_slash(content, session_id, slash)
                            elif isinstance(content, list):
                                for item in content:
                                    if isinstance(item, dict):
                                        txt = item.get("text", "")
                                        if txt:
                                            _extract_slash(
                                                txt, session_id, slash)

            except OSError as e:
                print(f"  warning: could not read {jsonl_file}: {e}",
                      file=sys.stderr)

    # Convert session sets to counts
    skill_out = {}
    for name, data in sorted(skills.items()):
        skill_out[name] = {
            "calls": data["calls"],
            "sessions": len(data["sessions"]),
        }

    slash_out = {}
    for name, data in sorted(slash.items()):
        slash_out[name] = {
            "calls": data["calls"],
            "sessions": len(data["sessions"]),
        }

    hostname = socket.gethostname()

    return {
        "schema": 1,
        "host": hostname,
        "days": days,
        "window_start": cutoff_iso,
        "scanned_files": scanned,
        "skills": skill_out,
        "slash": slash_out,
    }


def _extract_slash(text, session_id, slash_dict):
    """Extract <command-name>X</command-name> from text."""
    import re
    for m in re.finditer(r'<command-name>([^<]+)</command-name>', text):
        name = m.group(1).strip()
        if name:
            entry = slash_dict.setdefault(
                name, {"calls": 0, "sessions": set()})
            entry["calls"] += 1
            entry["sessions"].add(session_id)


def _empty_result(days):
    """Empty result when no projects dir exists."""
    import datetime
    import socket
    cutoff_ts = time.time() - days * 86400
    return {
        "schema": 1,
        "host": socket.gethostname(),
        "days": days,
        "window_start": datetime.datetime.fromtimestamp(
            cutoff_ts, tz=datetime.timezone.utc).isoformat(),
        "scanned_files": 0,
        "skills": {},
        "slash": {},
    }


# -- Fleet --------------------------------------------------------------

def run_fleet(days=DEFAULT_DAYS, runner=None):
    """Run skill-usage --json on every deployable host.
    Returns the fleet aggregated schema.

    runner: callable(host_entry) -> (stdout_str, returncode) for testing.
    """
    import datetime
    import subprocess
    import cli_remote

    hosts = cli_remote._deployable_hosts()
    boxes = []
    failed = []

    for host in hosts:
        name = host.get("name", host.get("addr", "unknown"))
        try:
            if runner:
                stdout, rc = runner(host)
            else:
                addr = host.get("addr", "")
                user = host.get("user", "newlevel")
                ssh_base = ["ssh", "-o", "BatchMode=yes",
                            "-o", "ConnectTimeout=10",
                            "-o", "StrictHostKeyChecking=no",
                            f"{user}@{addr}"]
                cmd = ssh_base + [
                    "python3", "~/devel/airuleset/airuleset.py",
                    "skill-usage", "--json",
                    "--days", str(days)
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

    return {
        "schema": 1,
        "date": datetime.date.today().isoformat(),
        "days": days,
        "boxes": boxes,
        "failed": failed,
    }


# -- CLI entry ----------------------------------------------------------

def cmd_skill_usage(args):
    """CLI handler for the skill-usage subcommand."""
    days = getattr(args, "days", DEFAULT_DAYS) or DEFAULT_DAYS

    if getattr(args, "fleet", False):
        data = run_fleet(days=days)
        if getattr(args, "json_output", False):
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            _print_fleet_table(data)
        return

    data = scan_usage(days=days)

    if getattr(args, "json_output", False):
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _print_usage_table(data)


def _print_usage_table(data):
    """Human-readable table for skill usage."""
    print(f"Skill usage ({data['days']}d, {data['scanned_files']} files):")
    print()

    if data["skills"]:
        print("Skills:")
        for name, info in sorted(data["skills"].items(),
                                 key=lambda x: -x[1]["calls"]):
            print(f"  {name}: {info['calls']} calls, "
                  f"{info['sessions']} sessions")
    else:
        print("Skills: (none)")

    zero_skills = set()
    # Count installed skills with zero calls
    skills_dir = CLAUDE_DIR / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if (entry / "SKILL.md").exists():
                if entry.name not in data["skills"]:
                    zero_skills.add(entry.name)

    if zero_skills:
        print(f"\n  {len(zero_skills)} skills with 0 calls: "
              f"{', '.join(sorted(zero_skills))}")

    if data["slash"]:
        print("\nSlash commands:")
        for name, info in sorted(data["slash"].items(),
                                 key=lambda x: -x[1]["calls"]):
            print(f"  /{name}: {info['calls']} calls, "
                  f"{info['sessions']} sessions")


def _print_fleet_table(data):
    """Human-readable table for fleet skill usage."""
    for box in data.get("boxes", []):
        skills = box.get("skills", {})
        total_calls = sum(s["calls"] for s in skills.values())
        print(f"{box.get('host', '?')}: "
              f"{len(skills)} skills used, {total_calls} total calls")
    for f in data.get("failed", []):
        print(f"FAILED: {f['host']} -- {f['error']}")
