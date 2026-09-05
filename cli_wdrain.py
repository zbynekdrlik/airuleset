"""cli_wdrain.py — the W-drain receipt CLI (#868).

Records a W-drain decision log (receipt) after the supervisor validates a
per-member verdict for every current ``--ops-wait`` member. The receipt
unblocks ``block-dispatch-over-wdrain.sh`` for 24h WORKING time (#607).

Leaf module, facade-re-exported from ``airuleset.py`` into ``SUBCOMMANDS``.
"""
import json
import os
import sys
import time

import statusbar


def _claude_dir(home=None):
    return statusbar._claude_dir(home)


def _wdrain_dir(home=None):
    return _claude_dir(home) / "wdrain"


def _receipt_path(cwd, home=None):
    key = statusbar.cwd_key(cwd)
    return _wdrain_dir(home) / (key + ".json")



def _compute_expires_at(now):
    """Compute receipt expiry: now + 24h WORKING time (weekend-aware, #607).

    Uses working_time.working_seconds_between to skip weekends. The receipt
    expires when 24h of WORKING seconds have elapsed since ``now``.
    """
    import working_time

    # Walk forward in time to find the epoch when 24h of working seconds
    # will have elapsed. Start optimistically at now + 24h (if all weekdays)
    # and extend if weekends intervene.
    target_working_s = 24 * 3600
    # Upper bound: at most now + 4*24h (worst case: receipt written Friday
    # evening, 2 weekend days + the 24h working = ~96h wall time).
    candidate = now + target_working_s
    for _ in range(10):  # bounded iteration
        elapsed = working_time.working_seconds_between(now, candidate)
        if elapsed >= target_working_s:
            # Fine-tune: binary search is overkill for day-granularity;
            # subtract the overshoot directly.
            overshoot = elapsed - target_working_s
            candidate -= overshoot
            # Recheck
            final = working_time.working_seconds_between(now, candidate)
            if final < target_working_s:
                candidate += (target_working_s - final)
            break
        # Not enough working time — add the deficit
        deficit = target_working_s - elapsed
        candidate += deficit
    return int(candidate)


def _parse_verdicts_file(path):
    """Parse a verdicts file (tab-separated lines).

    Format per line: ``#N<TAB>close|unpark|re-cite<TAB>citation``
    or ``family:<slug><TAB>proposed<TAB>citation``

    Returns a list of dicts: [{number: int|None, family: str|None,
    action: str, citation: str}], or raises ValueError on parse error.
    """
    verdicts = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\n\r")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                raise ValueError(
                    f"line {lineno}: expected 3 tab-separated fields, got {len(parts)}: {line!r}"
                )
            ref, action, citation = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if ref.startswith("family:"):
                verdicts.append({
                    "number": None,
                    "family": ref,
                    "action": action,
                    "citation": citation,
                })
            elif ref.startswith("#") and ref[1:].isdigit():
                verdicts.append({
                    "number": int(ref[1:]),
                    "family": None,
                    "action": action,
                    "citation": citation,
                })
            else:
                raise ValueError(
                    f"line {lineno}: ref must be #N or family:<slug>, got {ref!r}"
                )
    return verdicts


def cmd_wdrain_pass(args):
    """Record a W-drain receipt after validating per-member verdicts.

    Validates that every current --ops-wait member is covered directly
    or via a family:<slug> verdict, and that every citation is non-empty
    and passes _comment_has_citation. On pass, writes the receipt; on
    any gap, exits 1 naming the uncovered members.
    """
    if not getattr(args, "record", False):
        print("Usage: airuleset wdrain-pass --record --verdicts-file F [--cwd DIR]",
              file=sys.stderr)
        return 1

    verdicts_path = getattr(args, "verdicts_file", None)
    if not verdicts_path or not os.path.isfile(verdicts_path):
        print(f"ERROR: --verdicts-file {verdicts_path!r} not found", file=sys.stderr)
        return 1

    cwd = getattr(args, "cwd", None) or os.getcwd()

    # Parse verdicts
    try:
        verdicts = _parse_verdicts_file(verdicts_path)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not verdicts:
        print("ERROR: verdicts file is empty", file=sys.stderr)
        return 1

    # Validate citations
    from cli_quals import _comment_has_citation

    for v in verdicts:
        ref = v.get("family") or f"#{v['number']}"
        if not v["citation"]:
            print(f"ERROR: {ref} — citation is empty", file=sys.stderr)
            return 1
        if not _comment_has_citation(v["citation"]):
            print(f"ERROR: {ref} — citation does not pass _comment_has_citation: "
                  f"{v['citation']!r}", file=sys.stderr)
            return 1

    # Get current ops-wait members via the CLI subprocess (the authoritative
    # source for the member list, same as the hook reads the cache for count)
    import subprocess
    import pathlib

    script = pathlib.Path(__file__).resolve().parent / "airuleset.py"
    # Determine authority
    try:
        auth_out = subprocess.run(
            [sys.executable, str(script), "authority"],
            capture_output=True, text=True, timeout=10, cwd=cwd
        )
        authority = auth_out.stdout.strip()
    except Exception:
        authority = "full"

    quals_cmd = "core-quals" if authority == "full" else "slice-quals"
    try:
        result = subprocess.run(
            [sys.executable, str(script), quals_cmd, "--ops-wait"],
            capture_output=True, text=True, timeout=30, cwd=cwd
        )
    except Exception as exc:
        print(f"ERROR: failed to run {quals_cmd} --ops-wait: {exc}", file=sys.stderr)
        return 1

    # #868 review RED-1: a failed quals subprocess must NOT mint a receipt.
    # A gh outage/auth error returns rc!=0 + empty stdout; treating that as
    # "no members" would persist a ~96h false pass. Genuine zero is rc=0 +
    # empty stdout.
    if result.returncode != 0:
        print(f"ERROR: {quals_cmd} --ops-wait failed (rc={result.returncode})",
              file=sys.stderr)
        if result.stderr.strip():
            print(f"  {result.stderr.strip()[:200]}", file=sys.stderr)
        return 1

    # Parse ops-wait members (issue numbers from column 0, skip # lines)
    members = set()
    member_families = {}  # number -> family label if present
    for line in result.stdout.strip().splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        parts = line.split("\t")
        if not parts:
            continue
        try:
            num = int(parts[0].strip())
            members.add(num)
            # Check for family: token in the line
            for p in parts:
                p = p.strip()
                if p.startswith("family:"):
                    member_families[num] = p
                    break
        except (ValueError, IndexError):
            continue

    if not members:
        print("INFO: no ops-wait members — nothing to drain", file=sys.stderr)
        # Still write a receipt so the hook passes
        now = time.time()
        _write_receipt(cwd, [], now)
        return 0

    # Check coverage: every member must be covered directly or via family
    verdict_numbers = {v["number"] for v in verdicts if v["number"] is not None}
    verdict_families = {v["family"] for v in verdicts if v["family"] is not None}

    uncovered = []
    for num in sorted(members):
        if num in verdict_numbers:
            continue
        # Check if covered by a family verdict
        fam = member_families.get(num)
        if fam and fam in verdict_families:
            continue
        uncovered.append(num)

    if uncovered:
        print(f"ERROR: uncovered ops-wait members: {', '.join(f'#{n}' for n in uncovered)}",
              file=sys.stderr)
        print("  Each must have a direct #N verdict or a family:<slug> verdict "
              "matching their family label.", file=sys.stderr)
        return 1

    # All covered, all citations valid — write receipt
    now = time.time()
    _write_receipt(cwd, verdicts, now)
    print(f"W-drain receipt recorded for {len(members)} members, "
          f"expires in 24h working time.")
    return 0


def _write_receipt(cwd, verdicts, now):
    """Write the W-drain receipt JSON."""
    expires_at = _compute_expires_at(now)
    receipt = {
        "ts": int(now),
        "cwd": str(cwd),
        "members": [v.get("family") or f"#{v['number']}" for v in verdicts],
        "verdicts": [
            {"ref": v.get("family") or f"#{v['number']}",
             "action": v["action"],
             "citation": v["citation"]}
            for v in verdicts
        ],
        "expires_at": expires_at,
    }

    key = statusbar.cwd_key(cwd)
    wdir = _wdrain_dir()
    wdir.mkdir(parents=True, exist_ok=True)
    path = wdir / (key + ".json")

    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)
    os.replace(tmp, str(path))
