"""watchdog.priority_policy — Jobs 44+45: priority enforcement + orphan
bg-poll-loop reaper (#885, odoo-erp #6274).

Job 44 — PRIORITY ENFORCER.  Every watchdog cycle, renices background
processes (Chrome/Playwright, MCP node servers) to yield CPU to the
interactive CLI.  WRITE-side complement of Job 42's READ-ONLY nice_check.

Job 45 — ORPHAN BG-POLL-LOOP REAPER.  Finds bash/sh poll loops whose
parent session is dead (no live Claude ancestor in the ppid chain),
whose target (a gh run or PR) is already terminal, and SIGKILLs them.

Both are fail-safe by construction: any error in the process-table read,
classification, or TOCTOU re-verify kills/renices NOTHING.  Scoped to
the calling user's own UID.  Log-only (journal), never a Discord ping
(#546).  Seams are injectable for testing — no real /proc, no real gh,
no real renice/kill in tests.
"""

import os
import re
import subprocess

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def ppid_from_proc_stat(stat_line):
    """Extract the PPID (field 4) from a /proc/<pid>/stat line.

    Field numbering (1-indexed, per proc(5)):
      1=pid, 2=(comm), 3=state, 4=ppid, ...

    Uses the same last-')' technique as nice_check.nice_from_proc_stat.
    """
    rp = stat_line.rfind(")")
    if rp < 0:
        raise ValueError("malformed /proc/pid/stat: no closing paren")
    rest = stat_line[rp + 1:].split()
    # rest[0] = state (field 3), rest[1] = ppid (field 4)
    if len(rest) < 2:
        raise ValueError("malformed /proc/pid/stat: too few fields")
    return int(rest[1])


def _default_stat_reader(pid):
    """Read /proc/<pid>/stat.  Raises on unreadable."""
    with open("/proc/%d/stat" % pid) as f:
        return f.read()


def _default_cmdline_reader(pid):
    """Read /proc/<pid>/cmdline as a space-joined string, or None."""
    try:
        with open("/proc/%d/cmdline" % int(pid), "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return None
    return " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)


def _default_cwd_reader(pid):
    """Read /proc/<pid>/cwd via readlink, or None."""
    try:
        return os.readlink("/proc/%d/cwd" % int(pid))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Part A — Priority policy enforcer (Job 44)
# ---------------------------------------------------------------------------

# Chrome/Playwright family basenames — argv[0]-anchored.
_CHROME_BASENAMES = frozenset({
    "chrome", "chromium", "chromium-browser", "headless_shell",
    "chrome-crashpad-handler", "chrome_crashpad_handler", "nacl_helper",
})


def _is_chrome_family(args):
    """True iff args (cmdline string) has argv[0] basename in the Chrome family."""
    toks = (args or "").split()
    if not toks:
        return False
    return os.path.basename(toks[0]) in _CHROME_BASENAMES


def _looks_like_claude_cli(cmdline):
    """True iff cmdline looks like a Claude CLI process.

    Two shapes: (1) any token's basename is 'claude' (the bin-symlink);
    (2) the cmdline contains 'claude-code/cli' (the npm-shape, where
    basename is 'cli.js' — #885 F2/F3 review finding).  Over-match is
    the fail-safe direction (not-MCP / not-orphan).
    """
    if not cmdline:
        return False
    toks = cmdline.split()
    if any(os.path.basename(t) == "claude" for t in toks[:5]):
        return True
    if "claude-code/" in cmdline or "claude-code\\" in cmdline:
        return True
    return False


def _is_mcp_node(args, parent_cmdline=None):
    """True iff args is a node process whose parent is a Claude CLI.

    Structural match: argv[0] basename is node/nodejs AND the parent's
    cmdline contains 'claude' (the CLI binary).  The main Claude CLI's own
    parent is a shell/tmux, so it is structurally unreachable.
    """
    toks = (args or "").split()
    if not toks:
        return False
    base = os.path.basename(toks[0])
    if base not in ("node", "nodejs"):
        return False
    # Must NOT itself be the Claude CLI.  Two shapes: the bin-symlink
    # `node .../claude` (basename 'claude') and the npm-shape
    # `node .../claude-code/cli.js` (#885 F3 review finding).
    if _looks_like_claude_cli(args):
        return False
    # Parent must be a Claude CLI — same two-shape check.
    if not parent_cmdline:
        return False
    if _looks_like_claude_cli(parent_cmdline):
        return True
    return False


# Policy table: (label, nice_target, ionice_idle)
# Classifier is separate — see _classify_process below.
PRIORITY_POLICY = {
    "chrome": (10, True),
    "mcp-node": (10, False),
}


def _classify_process(args, parent_cmdline=None):
    """Classify a process cmdline into a policy label, or None."""
    if _is_chrome_family(args):
        return "chrome"
    if _is_mcp_node(args, parent_cmdline):
        return "mcp-node"
    return None


def _default_renice_fn(pid, nice_val):
    """Renice via os.setpriority.  Raises on failure."""
    os.setpriority(os.PRIO_PROCESS, pid, nice_val)


def _default_ionice_fn(pid):
    """Set ionice to idle class (3) via subprocess.  Best-effort."""
    try:
        subprocess.run(
            ["ionice", "-c", "3", "-p", str(pid)],
            capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # airuleset:script-ok best-effort ionice


def priority_policy_job(ps_fetch=None, renice_fn=None, ionice_fn=None,
                        verify_fn=None, stat_reader=None,
                        parent_cmdline_fn=None,
                        dry_run=False):
    """Job 44 entry point.  Enforce the priority policy.

    `ps_fetch` returns (pid, etimes, cputimes, args) tuples (same as
    reaper.default_ps_fetch).  `renice_fn(pid, nice)` sets the nice value.
    `ionice_fn(pid)` sets ionice idle.  `verify_fn(pid)` returns the live
    cmdline for TOCTOU re-verify.  `stat_reader(pid)` returns /proc/pid/stat
    for reading current nice.  `parent_cmdline_fn(ppid)` returns the parent's
    cmdline for MCP-node structural match.

    All seams injectable; `renice_fn=None` / `ionice_fn=None` means use
    the real defaults (`os.setpriority` / `ionice`).  Pass a recorder
    lambda in tests. `dry_run=True` logs "would renice" and never mutates.
    Returns journal log lines.  Never pings Discord.
    """
    if ps_fetch is None:
        from watchdog.reaper import default_ps_fetch
        ps_fetch = default_ps_fetch
    if renice_fn is None:
        renice_fn = _default_renice_fn
    if ionice_fn is None:
        ionice_fn = _default_ionice_fn
    if verify_fn is None:
        verify_fn = _default_cmdline_reader
    if stat_reader is None:
        stat_reader = _default_stat_reader
    if parent_cmdline_fn is None:
        parent_cmdline_fn = _default_cmdline_reader

    logs = []
    try:
        procs = ps_fetch()
    except Exception as e:
        return ["priority-policy: ps error, did nothing: %r" % (e,)]
    if procs is None:
        return logs

    # Import nice_from_proc_stat for reading current nice values.
    from watchdog.nice_check import nice_from_proc_stat

    for entry in procs:
        try:
            pid, etimes, cputimes, args = entry
        except Exception:
            continue

        # Determine parent cmdline for MCP-node detection.
        pcmdline = None
        try:
            stat_line = stat_reader(pid)
            ppid_val = ppid_from_proc_stat(stat_line)
            if ppid_val and ppid_val > 1:
                pcmdline = parent_cmdline_fn(ppid_val)
        except Exception:
            pass

        label = _classify_process(args, pcmdline)
        if label is None:
            continue

        target_nice, do_ionice = PRIORITY_POLICY[label]

        # Read current nice.
        try:
            stat_line_cur = stat_reader(pid)
            current_nice = nice_from_proc_stat(stat_line_cur)
        except Exception:
            continue  # can't read → skip

        # Idempotent: skip if already at or below target.
        # NEVER lower nice (raise priority) — only raise nice (demote).
        if current_nice >= target_nice:
            continue

        if dry_run:
            logs.append(
                "priority-policy: DRY-RUN would renice pid=%s label=%s "
                "%s->%s cmd=%s" % (pid, label, current_nice, target_nice, args))
            continue
        if renice_fn is None:
            logs.append(
                "priority-policy: renice_fn not wired — would renice pid=%s "
                "label=%s %s->%s cmd=%s (skipped)"
                % (pid, label, current_nice, target_nice, args))
            continue

        # TOCTOU: re-verify the pid still matches the classifier.
        try:
            live = verify_fn(pid)
        except Exception:
            live = None
        if live is None:
            logs.append(
                "priority-policy: pid=%s vanished before renice, "
                "skipped (cmd was %s)" % (pid, args))
            continue

        # Re-fetch parent cmdline for TOCTOU.
        live_pcmdline = None
        try:
            live_stat = stat_reader(pid)
            live_ppid = ppid_from_proc_stat(live_stat)
            if live_ppid and live_ppid > 1:
                live_pcmdline = parent_cmdline_fn(live_ppid)
        except Exception:
            pass

        if _classify_process(live, live_pcmdline) != label:
            logs.append(
                "priority-policy: pid=%s no longer %s (reused?), "
                "skipped (now %r)" % (pid, label, live))
            continue

        try:
            renice_fn(pid, target_nice)
            msg = ("priority-policy: renice pid=%s label=%s %s->%s cmd=%s"
                   % (pid, label, current_nice, target_nice, args))
            if do_ionice and ionice_fn is not None:
                ionice_fn(pid)
                msg += " +ionice-idle"
            logs.append(msg)
        except Exception as e:
            logs.append(
                "priority-policy: renice pid=%s FAILED: %r "
                "(label=%s cmd=%s)" % (pid, e, label, args))
    return logs


# ---------------------------------------------------------------------------
# Part B — Orphan bg-poll-loop reaper (Job 45)
# ---------------------------------------------------------------------------

# Max gh API checks per cycle to bound API rate consumption.
ORPHAN_MAX_GH_CHECKS = 3

# Min age in seconds before a poll loop is considered for reaping.
ORPHAN_MIN_AGE_S = 1800  # 30 min, same as shadow-ugrep (#776)

# Max hops walking the ppid chain (prevents infinite loop on a cycle).
_MAX_PPID_WALK = 20

# Regex to extract a run id or PR number from the poll-loop cmdline.
_GH_RUN_VIEW_RE = re.compile(r"gh\s+run\s+view\s+(\d+)")
_GH_PR_VIEW_RE = re.compile(r"gh\s+pr\s+(?:view|checks)\s+(\d+)")

# Signature tokens that identify a poll loop.
_LOOP_KEYWORDS = {"while", "for"}
_SHELL_BASENAMES = frozenset({"bash", "sh", "timeout"})


def _is_poll_loop_signature(args):
    """True iff args looks like a bg poll loop: bash/sh/timeout with
    while/for + sleep + gh run/pr view."""
    toks = (args or "").split()
    if not toks:
        return False
    if os.path.basename(toks[0]) not in _SHELL_BASENAMES:
        return False
    # Tokenized keyword match (#885 F6: substring `"for" in lower` also
    # matches `--format`/`before`/`info`).  Split on whitespace + shell
    # metacharacters for a cleaner match.
    tokens = set(re.split(r"[\s;|&(){}'\"]+", (args or "").lower()))
    has_loop = bool(tokens & _LOOP_KEYWORDS)
    has_sleep = "sleep" in tokens
    has_gh = ("gh run view" in args or "gh pr view" in args
              or "gh pr checks" in args or "gh api" in args)
    return has_loop and has_sleep and has_gh


def _extract_target(args):
    """Extract (kind, ident) from a poll loop cmdline.

    Returns ("run", "12345") or ("pr", "67") or None if no id found.
    """
    m = _GH_RUN_VIEW_RE.search(args or "")
    if m:
        return ("run", m.group(1))
    m = _GH_PR_VIEW_RE.search(args or "")
    if m:
        return ("pr", m.group(1))
    return None


def _is_orphan(pid, stat_reader=None, cmdline_reader=None):
    """Walk the ppid chain from pid.  Return True only if the walk
    reaches pid 1 without encountering a claude CLI process.

    Any read error → False (fail-safe: not orphan → not killed).
    """
    if stat_reader is None:
        stat_reader = _default_stat_reader
    if cmdline_reader is None:
        cmdline_reader = _default_cmdline_reader

    current = pid
    for _ in range(_MAX_PPID_WALK):
        try:
            stat_line = stat_reader(current)
            parent = ppid_from_proc_stat(stat_line)
        except Exception:
            return False  # can't read → not orphan
        if parent <= 1:
            return True  # reached init with no claude ancestor
        # Check if parent is a claude process.
        try:
            pcmd = cmdline_reader(parent)
        except Exception:
            return False
        if _looks_like_claude_cli(pcmd):
            # Uses the shared two-shape match (bin-symlink basename 'claude'
            # OR npm-shape 'claude-code/' in path — #885 F2 review finding).
            # Over-match is the fail-safe not-orphan direction.
            return False  # live claude ancestor → not orphan
        current = parent
    return False  # too many hops → fail-safe not orphan


def _default_gh_check_fn(cwd, kind, ident):
    """Check if a gh target is terminal.  Returns "terminal", "live", or "error".

    For runs: `gh run view <id> --json status --jq .status` → completed = terminal.
    For PRs: `gh pr view <id> --json state --jq .state` → MERGED/CLOSED = terminal.
    """
    try:
        if kind == "run":
            r = subprocess.run(
                ["gh", "run", "view", str(ident), "--json", "status",
                 "--jq", ".status"],
                capture_output=True, text=True, timeout=15, cwd=cwd)
        elif kind == "pr":
            r = subprocess.run(
                ["gh", "pr", "view", str(ident), "--json", "state",
                 "--jq", ".state"],
                capture_output=True, text=True, timeout=15, cwd=cwd)
        else:
            return "error"
    except Exception:
        return "error"
    if r.returncode != 0:
        return "error"
    val = r.stdout.strip().lower()
    if kind == "run" and val == "completed":
        return "terminal"
    if kind == "pr" and val in ("merged", "closed"):
        return "terminal"
    if val:
        return "live"
    return "error"


def orphan_poll_reaper(ps_fetch=None, kill_fn=None, verify_fn=None,
                       stat_reader=None, cmdline_reader=None,
                       cwd_reader=None, gh_check_fn=None,
                       dry_run=False, min_age_s=ORPHAN_MIN_AGE_S,
                       max_gh_checks=ORPHAN_MAX_GH_CHECKS):
    """Job 45 entry point.  Find + SIGKILL orphan bg poll loops.

    An orphan poll loop is a bash/sh process with: (1) no live Claude
    ancestor (ppid chain reaches pid 1), (2) a poll-loop signature
    (while+sleep+gh), (3) age > min_age_s, (4) target confirmed terminal
    via gh.

    `gh_check_fn(cwd, kind, ident)` returns "terminal"/"live"/"error".
    All seams injectable.  Returns journal log lines.  Never pings Discord.
    """
    if ps_fetch is None:
        from watchdog.reaper import default_ps_fetch
        ps_fetch = default_ps_fetch
    if verify_fn is None:
        verify_fn = _default_cmdline_reader
    if stat_reader is None:
        stat_reader = _default_stat_reader
    if cmdline_reader is None:
        cmdline_reader = _default_cmdline_reader
    if cwd_reader is None:
        cwd_reader = _default_cwd_reader
    if gh_check_fn is None:
        gh_check_fn = _default_gh_check_fn

    logs = []
    try:
        procs = ps_fetch()
    except Exception as e:
        return ["orphan-poll-reaper: ps error, killed nothing: %r" % (e,)]
    if procs is None:
        return logs

    # Collect candidates.
    candidates = []
    for entry in procs:
        try:
            pid, etimes, cputimes, args = entry
        except Exception:
            continue

        # Age gate.
        try:
            if int(etimes) <= min_age_s:
                continue
        except (TypeError, ValueError):
            continue

        # Signature check.
        if not _is_poll_loop_signature(args):
            continue

        # Orphan check (ppid walk).
        if not _is_orphan(pid, stat_reader=stat_reader,
                          cmdline_reader=cmdline_reader):
            continue

        # Extract target.
        target = _extract_target(args)
        if target is None:
            logs.append(
                "orphan-poll-reaper: skip:no-id pid=%s age=%ss "
                "cmd=%s" % (pid, etimes, args))
            continue

        # Read cwd for gh context.
        cwd = cwd_reader(pid)
        if not cwd:
            logs.append(
                "orphan-poll-reaper: skip:no-cwd pid=%s age=%ss "
                "cmd=%s" % (pid, etimes, args))
            continue

        candidates.append((pid, etimes, cputimes, args, target, cwd))

    # Dedupe by (cwd, kind, ident) and check targets (bounded).
    checked = {}  # (cwd, kind, ident) -> "terminal"/"live"/"error"
    gh_calls = 0

    for pid, etimes, cputimes, args, (kind, ident), cwd in candidates:
        key = (cwd, kind, ident)
        if key not in checked:
            if gh_calls >= max_gh_checks:
                logs.append(
                    "orphan-poll-reaper: skip:gh-budget pid=%s %s=%s "
                    "age=%ss cmd=%s" % (pid, kind, ident, etimes, args))
                continue
            checked[key] = gh_check_fn(cwd, kind, ident)
            gh_calls += 1

        status = checked.get(key, "error")
        if status != "terminal":
            logs.append(
                "orphan-poll-reaper: skip:%s pid=%s %s=%s age=%ss "
                "cmd=%s" % (
                    "target-live" if status == "live" else "gh-error",
                    pid, kind, ident, etimes, args))
            continue

        if dry_run:
            logs.append(
                "orphan-poll-reaper: DRY-RUN would SIGKILL pid=%s %s=%s "
                "status=%s age=%ss cmd=%s"
                % (pid, kind, ident, status, etimes, args))
            continue
        if kill_fn is None:
            logs.append(
                "orphan-poll-reaper: kill_fn not wired — would SIGKILL "
                "pid=%s %s=%s age=%ss cmd=%s (skipped)"
                % (pid, kind, ident, etimes, args))
            continue

        # TOCTOU: re-verify pid is still the orphan poll loop.
        try:
            live = verify_fn(pid)
        except Exception:
            live = None
        if live is None:
            logs.append(
                "orphan-poll-reaper: pid=%s vanished before kill, skipped "
                "(cmd was %s)" % (pid, args))
            continue
        if not _is_poll_loop_signature(live):
            logs.append(
                "orphan-poll-reaper: pid=%s no longer a poll loop (reused?),"
                " skipped (now %r)" % (pid, live))
            continue
        # Re-verify orphan status.
        if not _is_orphan(pid, stat_reader=stat_reader,
                          cmdline_reader=cmdline_reader):
            logs.append(
                "orphan-poll-reaper: pid=%s no longer orphan, skipped"
                % (pid,))
            continue

        try:
            kill_fn(pid)
            logs.append(
                "orphan-poll-reaper: SIGKILL pid=%s %s=%s status=%s "
                "age=%ss orphan-poll (issue 885) cmd=%s"
                % (pid, kind, ident, status, etimes, args))
        except Exception as e:
            logs.append(
                "orphan-poll-reaper: SIGKILL pid=%s FAILED: %r "
                "(%s=%s age=%ss cmd=%s)"
                % (pid, e, kind, ident, etimes, args))
    return logs
