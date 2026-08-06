#!/usr/bin/env python3
"""Reproducible measurement for #267: does CLAUDE_CODE_NO_FLICKER=1 actually
fix tmux scrollback HOLES (missing/duplicated passages) -- or only flicker?

Background (#253, #235): Claude Code's own TUI renderer re-emits a copy of
the visible transcript into the terminal's PRIMARY scrollback buffer on
every SIGWINCH/relayout event -- a window resize, the Ctrl+O transcript
toggle, or the Shift+Tab permission-mode cycle (upstream:
anthropics/claude-code#84247, #46834, both open). #253 shipped
CLAUDE_CODE_NO_FLICKER=1 as an OPT-IN launcher mode (`claude-fullscreen`) on
the theory that alternate-screen mode never writes into native scrollback at
all, so the defect class has nothing to corrupt -- but that was never
measured against the actual scrollback CONTENT, only reasoned from the
mechanism. #267 asks: does it actually hold?

Method
------
Spins up TWO fully throwaway, isolated real interactive Claude Code sessions
-- one plain, one with CLAUDE_CODE_NO_FLICKER=1 -- each under its own
CLAUDE_CONFIG_DIR (a scratch profile, real credentials copied in, no
mcpServers/hooks/real projects) and both inside a dedicated, disposable tmux
server (`-L <socket>`, never the fleet's real managed server or any real
pane). Each session gets exactly ONE real prompt asking for a long run of
deterministic, uniquely-numbered "SCROLLBACK-PROBE-NNNNN" lines (cheap: one
real API turn per arm, holds fleet/session-limit usage to a minimum) --
then, with ZERO further API cost, each pane is driven through a burst of the
three upstream-named relayout triggers: window resizes, Ctrl+O toggles, and
Shift+Tab permission-mode cycles.

The session's own CC transcript JSONL is the SOURCE OF TRUTH for what was
actually said (never our prompt's wish, which the model may not follow
verbatim). Every real assistant text line (>=8 chars) is checked against the
pane's native `tmux capture-pane -S` scrollback:
  - MISSING: a real line that never appears anywhere in the pane's history
    at all (a "hole").
  - DUPLICATED: a real line that appears as an identical, separate row TWO
    OR MORE times in the pane's history (the renderer's own stacked/
    re-emitted frame).

Nothing here touches the user's real Claude Code profile, real tmux server,
or any real project pane -- both arms run under an isolated
CLAUDE_CONFIG_DIR and an isolated tmux -L socket, and everything (sessions,
tmux server, scratch dirs) is torn down at the end unless --keep is passed.

Usage:
    python3 scripts/measure_scrollback_holes.py [--lines 300] [--model haiku]
        [--resizes 8] [--ctrl-o 4] [--shift-tab 4] [--keep] [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import watchdog as wd  # noqa: E402  (encode_project_dir / find_active_transcript)

PROBE_PREFIX = "SCROLLBACK-PROBE-"
REAL_CREDS = Path.home() / ".claude" / ".credentials.json"
REAL_CLAUDE_JSON = Path.home() / ".claude.json"
CLAUDE_BIN = shutil.which("claude") or str(Path.home() / ".local/bin/claude")

STARTUP_TIMEOUT_S = 40
TURN_TIMEOUT_S = 300
BUSY_MARKERS = ("esc to interrupt",)


def run(argv, timeout=15):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def tmux(sock, *args, timeout=15):
    return run(["tmux", "-L", sock, *args], timeout=timeout)


def bootstrap_profile(base_dir, scratch_cwd):
    """A minimal, isolated CLAUDE_CONFIG_DIR: real credentials copied in (so
    the session is genuinely authenticated -- no login flow to drive), a
    settings.json that pre-accepts the bypass-permissions dialog, and a
    .claude.json whose `projects` entry pre-trusts our OWN scratch cwd only
    -- so nothing but real content generation and real rendering happens
    once the session comes up. mcpServers/other real projects are stripped."""
    config_dir = Path(base_dir) / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    creds_dst = config_dir / ".credentials.json"
    creds_dst.write_bytes(REAL_CREDS.read_bytes())
    os.chmod(creds_dst, 0o600)

    real = json.loads(REAL_CLAUDE_JSON.read_text())
    real.pop("mcpServers", None)
    real["hasCompletedOnboarding"] = True
    real["projects"] = {str(scratch_cwd): {"hasTrustDialogAccepted": True}}
    (config_dir / ".claude.json").write_text(json.dumps(real))

    settings = {
        "model": "sonnet",
        "permissions": {"allow": [], "defaultMode": "bypassPermissions"},
        "hooks": {},
        "cleanupPeriodDays": 365,
        "skipDangerousModePermissionPrompt": True,
    }
    (config_dir / "settings.json").write_text(json.dumps(settings))
    return config_dir


def clear_startup_dialogs(sock, session, timeout=STARTUP_TIMEOUT_S):
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        cap = tmux(sock, "capture-pane", "-p", "-t", session).stdout
        last = cap
        low = cap.lower()
        if "trust this folder" in low or "is this a project you created" in low:
            tmux(sock, "send-keys", "-t", session, "Enter")
            time.sleep(2)
            continue
        if "bypass permissions mode" in low:
            tmux(sock, "send-keys", "-t", session, "2")
            time.sleep(0.5)
            tmux(sock, "send-keys", "-t", session, "Enter")
            time.sleep(2)
            continue
        if "enter to confirm" in low:
            time.sleep(1)
            continue
        if "❯" in cap:
            return cap
        time.sleep(1)
    raise RuntimeError(f"[{session}] startup dialogs never cleared; last capture:\n{last}")


def send_prompt(sock, session, text):
    tmux(sock, "send-keys", "-t", session, "-l", text, timeout=15)
    time.sleep(0.3)
    tmux(sock, "send-keys", "-t", session, "Enter")


def wait_for_idle(sock, session, timeout=TURN_TIMEOUT_S, settle_checks=2, poll=2.0):
    time.sleep(2.0)  # give the spinner a moment to appear before we start polling
    deadline = time.time() + timeout
    clean_streak = 0
    while time.time() < deadline:
        cap = tmux(sock, "capture-pane", "-p", "-t", session).stdout
        low = cap.lower()
        busy = any(m in low for m in BUSY_MARKERS)
        if not busy:
            clean_streak += 1
            if clean_streak >= settle_checks:
                return cap
        else:
            clean_streak = 0
        time.sleep(poll)
    raise TimeoutError(f"[{session}] turn never finished within {timeout}s")


def stress_relayout(sock, session, sizes, ctrl_o_n, shift_tab_n):
    """Fire every upstream-named relayout trigger (#253:
    anthropics/claude-code#84247/#46834) with ZERO further API cost."""
    for w, h in sizes:
        tmux(sock, "resize-window", "-t", session, "-x", str(w), "-y", str(h))
        time.sleep(1.0)
    for _ in range(ctrl_o_n):
        tmux(sock, "send-keys", "-t", session, "C-o")
        time.sleep(0.6)
    for _ in range(shift_tab_n):
        tmux(sock, "send-keys", "-t", session, "BTab")
        time.sleep(0.6)


def capture_full_history(sock, session):
    r = tmux(sock, "capture-pane", "-p", "-t", session, "-S", "-100000", timeout=20)
    return r.stdout


def read_assistant_probe_lines(config_dir, cwd):
    projects_dir = Path(config_dir) / "projects"
    res = wd.find_active_transcript(str(projects_dir), str(cwd))
    if not res:
        return None, []
    path, _mtime = res
    lines = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    for ln in block.get("text", "").split("\n"):
                        ln = ln.strip()
                        if len(ln) >= 8:
                            lines.append(ln)
    return path, lines


def compare(capture_text, expected_lines):
    row_counts = {}
    for row in capture_text.split("\n"):
        s = row.strip()
        if s:
            row_counts[s] = row_counts.get(s, 0) + 1

    missing, duplicated = [], []
    for ln in expected_lines:
        cnt = row_counts.get(ln, 0)
        if cnt == 0:
            if ln not in capture_text:  # fallback: odd wrap, still search the raw blob
                missing.append(ln)
        elif cnt >= 2:
            duplicated.append([ln, cnt])

    total = len(expected_lines)
    return {
        "total_expected": total,
        "missing_count": len(missing),
        "missing_pct": round(len(missing) / total * 100, 2) if total else None,
        "missing_sample": missing[:15],
        "duplicate_count": len(duplicated),
        "duplicate_pct": round(len(duplicated) / total * 100, 2) if total else None,
        "duplicate_sample": duplicated[:15],
    }


def run_arm(sock, base_dir, mode, n_lines, model, resizes, ctrl_o_n, shift_tab_n):
    session = f"sb-{mode}"
    cwd = Path(base_dir) / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    config_dir = bootstrap_profile(base_dir, cwd)

    env_prefix = ["env", f"CLAUDE_CONFIG_DIR={config_dir}"]
    if mode == "noflicker":
        env_prefix.append("CLAUDE_CODE_NO_FLICKER=1")

    tmux(sock, "new-session", "-d", "-s", session, "-x", "200", "-y", "50",
         "-c", str(cwd), *env_prefix, CLAUDE_BIN, "--dangerously-skip-permissions",
         "--model", model)

    clear_startup_dialogs(sock, session)

    prompt = (
        f"Output exactly {n_lines} lines and nothing else -- no preamble, no "
        f"markdown, no code fence, no summary. Each line must be exactly of "
        f"the form '{PROBE_PREFIX}NNNNN' where NNNNN is the line number from "
        f"00001 to {n_lines:05d}, zero-padded to 5 digits. One per line, in "
        f"order. Do not explain, just print the lines."
    )
    send_prompt(sock, session, prompt)
    wait_for_idle(sock, session)

    baseline_capture = capture_full_history(sock, session)

    sizes = [(200, 50), (170, 40), (220, 55), (180, 45)] * ((resizes // 4) + 1)
    stress_relayout(sock, session, sizes[:resizes], ctrl_o_n, shift_tab_n)

    final_capture = capture_full_history(sock, session)

    tpath, expected = read_assistant_probe_lines(config_dir, cwd)
    expected = [ln for ln in expected if ln.startswith(PROBE_PREFIX)]

    return {
        "mode": mode,
        "session": session,
        "transcript": str(tpath) if tpath else None,
        "expected_line_count": len(expected),
        "baseline": compare(baseline_capture, expected),
        "final": compare(final_capture, expected),
    }


def teardown(sock, root, keep):
    tmux(sock, "kill-server", timeout=10)
    if not keep:
        shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lines", type=int, default=300)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--resizes", type=int, default=8)
    ap.add_argument("--ctrl-o", type=int, default=4)
    ap.add_argument("--shift-tab", type=int, default=4)
    ap.add_argument("--keep", action="store_true",
                     help="leave scratch dirs + tmux server up for inspection")
    ap.add_argument("--out", default=None, help="write JSON results here")
    args = ap.parse_args()

    sock = f"airuleset-sbmeasure-{uuid.uuid4().hex[:10]}"
    root = Path("/tmp") / f"airuleset-sbmeasure-{uuid.uuid4().hex[:10]}"
    base_dirs = {"default": root / "default", "noflicker": root / "noflicker"}

    results = {"tmux_version": run(["tmux", "-V"]).stdout.strip(),
               "cc_version": run([CLAUDE_BIN, "--version"]).stdout.strip(),
               "args": vars(args), "arms": {}}
    try:
        for mode, base_dir in base_dirs.items():
            print(f"=== arm: {mode} ===", file=sys.stderr)
            base_dir.mkdir(parents=True, exist_ok=True)
            arm = run_arm(sock, base_dir, mode, args.lines, args.model,
                          args.resizes, args.ctrl_o, args.shift_tab)
            results["arms"][mode] = arm
            print(json.dumps(arm, indent=2), file=sys.stderr)
    finally:
        teardown(sock, root, args.keep)

    print(json.dumps(results, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
