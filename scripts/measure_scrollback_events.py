#!/usr/bin/env python3
"""Measurement for #289 (root-cause redirect, 2026-08-07): the ORIGINAL
history-limit fix (#235/#267) already made scrollback corruption a small,
event-triggered phenomenon (2.67-6.0% duplicated after 8 resize-window calls
+ 4 Ctrl+O + 4 Shift+Tab -- see scrollback-holes-measurement-267.md). The
user's #289 report -- "proste som sa v tmuxe posuval hore a vsetko tam bolo,
teraz je tam vzdy bordel... rozdiel je ze sa robi ovela viacej compactov,
bezia subagenti a na jeden tmux sa pozera viacero ludi" -- names THREE event
classes as candidate amplifiers the #267 harness never isolated:

  1. Multiple CLIENTS attaching to the SAME session with DIFFERING terminal
     geometry (viacero ludi na jeden tmux) -- #267's own resize measurement
     only ever drove `tmux resize-window` on a single attached client; it
     never modeled a SECOND client attaching with a different size, which is
     what actually happens when several people/devices view one pane and is
     the scenario `window-size manual` (removed fleet-wide by #241 because
     it crashes tmux 3.4 at conf-parse time -- see TMUX_MARK_START's own
     module comment in airuleset.py) exists to control.
  2. `/compact` events -- now far more frequent (per-ticket boundary
     compaction, `notification-mechanics` skill) than when the user first
     had no complaints.
  3. Background subagent strip churn -- not modeled here (would need a real
     dispatched subagent inside the scratch profile, materially more
     expensive; left as a documented gap, not silently skipped).

Method (mirrors measure_scrollback_holes.py's harness exactly: isolated
CLAUDE_CONFIG_DIR, isolated disposable tmux `-L` socket, one real cheap
prompt for N deterministic SCROLLBACK-PROBE-NNNNN lines, the session's own
transcript JSONL as ground truth, `missing`/`duplicated` against
`capture-pane -S -100000`):

  --event resize-multiclient (default): after the baseline capture, attach a
    SECOND pty client at a DIFFERENT geometry than the first (a real tmux
    ATTACH, not a `resize-window` call -- tmux's own `window-size` option
    decides what happens next, exactly matching a real second viewer), let
    it settle, detach it, repeat for N_CYCLES. Run once with `window-size
    latest` (today's live fleet default -- confirmed via a READ-ONLY `tmux
    show -gw window-size` against the real server, #289) and once with
    `window-size manual` + `default-size 176x50` set BEFORE the first client
    ever attaches (conf-time, never a live flip against a running server --
    the #236 hazard this avoids by construction).

  --event compact: after the baseline capture, send a real `/compact`
    (matching the documented `tmux send-keys -l '/compact'` recipe) and wait
    for CC's own `Compacting conversation` state to clear, then capture
    again.

Nothing here ever touches the user's real tmux server or any real pane --
every session/client/server here is disposable and isolated, exactly like
measure_scrollback_holes.py.

Usage:
    python3 scripts/measure_scrollback_events.py --event resize-multiclient \\
        [--lines 80] [--model haiku] [--cycles 4] [--keep] [--out results.json]
    python3 scripts/measure_scrollback_events.py --event compact
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import signal
import struct
import sys
import termios
import threading
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import measure_scrollback_holes as msh  # noqa: E402 (reuse the #267 harness helpers)

FIRST_GEOMETRY = (200, 50)   # matches #267's own harness default session size
SECOND_GEOMETRY = (100, 30)  # a materially smaller second viewer -- the
                              # "someone else looks at the same tmux from a
                              # smaller terminal" scenario the user described


def _pty_resize(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _drain_fd(fd):
    """Continuously read a pty MASTER fd and discard the bytes, so the
    slave-side writer (an attached tmux client re-rendering the whole
    screen on every spinner tick) can never block on write() once the
    kernel pty buffer (~64KB) fills with nobody reading it. Runs as a
    background daemon thread for the lifetime of the fd; a read error just
    means the fd was closed elsewhere (kill_pty_client) or the process
    holding the slave end exited -- expected, logged, non-fatal."""
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
    except OSError as e:
        print(f"_drain_fd: read(fd={fd}) ended (non-fatal): {e}", file=sys.stderr)


def terminate_pid(pid, timeout=3.0, poll=0.05):
    """Bounded reap: SIGTERM, poll for exit up to `timeout` seconds, then
    an unconditional SIGKILL + a final blocking waitpid. SIGKILL cannot be
    ignored or blocked, so this call is GUARANTEED to return -- the
    harness must never again hang indefinitely on a child that doesn't
    honor SIGTERM (#291: a live run hung >500s on exactly a plain
    unbounded `os.waitpid(pid, 0)` with no escalation at all)."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError as e:
        print(f"terminate_pid: pid {pid} already gone before SIGTERM (non-fatal): {e}",
              file=sys.stderr)
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            reaped, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError as e:
            print(f"terminate_pid: waitpid({pid}, WNOHANG) failed -- already reaped "
                  f"(non-fatal): {e}", file=sys.stderr)
            return
        if reaped == pid:
            return
        time.sleep(poll)
    print(f"terminate_pid: pid {pid} did not exit within {timeout}s of SIGTERM -- "
          f"escalating to SIGKILL", file=sys.stderr)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError as e:
        print(f"terminate_pid: pid {pid} gone before SIGKILL (non-fatal): {e}", file=sys.stderr)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError as e:
        print(f"terminate_pid: final waitpid({pid}) failed (non-fatal): {e}", file=sys.stderr)


def attach_pty_client(sock, session, cols, rows):
    """A REAL tmux client attach via a genuine attached pty (never
    `send-keys` -- see this repo's own #236/#267 precedent: only a truly
    attached client's negotiated size makes tmux's `window-size` option
    logic fire for real). Sized BEFORE exec so tmux's own initial
    negotiation already sees the target geometry.

    Two #291 hardenings over the naive form: FD_CLOEXEC on the master fd
    (so a LATER attach_pty_client() call's own pty.fork()+exec never
    inherits an earlier client's still-open master), and a background
    drain thread on the master (see _drain_fd) so the attached client can
    never block on a full, unread pty buffer."""
    pid, fd = pty.fork()
    if pid == 0:
        _pty_resize(0, cols, rows)  # child's own fd 0 is the pty slave
        os.execvp("tmux", ["tmux", "-L", sock, "attach", "-t", session])
        os._exit(1)
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
    threading.Thread(target=_drain_fd, args=(fd,), daemon=True).start()
    return pid, fd


def kill_pty_client(pid, fd):
    terminate_pid(pid)
    try:
        os.close(fd)
    except OSError as e:
        print(f"kill_pty_client: close(fd={fd}) failed (non-fatal): {e}", file=sys.stderr)


def configure_window_size(sock, mode):
    """`window-size`/`default-size` set via a plain `set-option -g` call
    against the freshly-created (zero clients attached yet) isolated
    session -- conf-TIME, never a live flip against an ALREADY-ATTACHED
    server (the #236-proven-disruptive case). `latest` needs no call at
    all -- it is tmux's own factory default, and is what the real fleet
    server is confirmed (read-only `tmux show -gw window-size`, #289) to
    still be running today."""
    if mode == "manual":
        msh.tmux(sock, "set-option", "-g", "window-size", "manual")
        msh.tmux(sock, "set-option", "-g", "default-size", "176x50")
    elif mode != "latest":
        raise ValueError("mode must be 'latest' or 'manual': %r" % mode)


def run_resize_multiclient_arm(sock, base_dir, window_size_mode, n_lines, model, cycles):
    session = f"sb-mc-{window_size_mode}"
    cwd = Path(base_dir) / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    config_dir = msh.bootstrap_profile(base_dir, cwd)

    w0, h0 = FIRST_GEOMETRY
    msh.tmux(sock, "new-session", "-d", "-s", session, "-x", str(w0), "-y", str(h0),
              "-c", str(cwd), "env", f"CLAUDE_CONFIG_DIR={config_dir}",
              msh.CLAUDE_BIN, "--dangerously-skip-permissions", "--model", model)

    configure_window_size(sock, window_size_mode)

    pid1, fd1 = attach_pty_client(sock, session, w0, h0)
    try:
        msh.clear_startup_dialogs(sock, session)

        prompt = (
            f"Output exactly {n_lines} lines and nothing else -- no preamble, no "
            f"markdown, no code fence, no summary. Each line must be exactly of "
            f"the form '{msh.PROBE_PREFIX}NNNNN' where NNNNN is the line number "
            f"from 00001 to {n_lines:05d}, zero-padded to 5 digits. One per "
            f"line, in order. Do not explain, just print the lines."
        )
        msh.send_prompt(sock, session, prompt)
        msh.wait_for_idle(sock, session)

        baseline_capture = msh.capture_full_history(sock, session)

        for i in range(cycles):
            geom = SECOND_GEOMETRY if i % 2 == 0 else FIRST_GEOMETRY
            pid2, fd2 = attach_pty_client(sock, session, geom[0], geom[1])
            time.sleep(2.5)  # let tmux's own resize/redraw settle
            kill_pty_client(pid2, fd2)
            time.sleep(1.0)

        final_capture = msh.capture_full_history(sock, session)
    finally:
        kill_pty_client(pid1, fd1)

    tpath, expected = msh.read_assistant_probe_lines(config_dir, cwd)
    expected = [ln for ln in expected if ln.startswith(msh.PROBE_PREFIX)]

    return {
        "event": "resize-multiclient",
        "window_size_mode": window_size_mode,
        "cycles": cycles,
        "session": session,
        "transcript": str(tpath) if tpath else None,
        "expected_line_count": len(expected),
        "baseline": msh.compare(baseline_capture, expected),
        "final": msh.compare(final_capture, expected),
    }


def wait_for_compact_to_finish(sock, session, timeout=120, poll=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cap = msh.tmux(sock, "capture-pane", "-p", "-t", session).stdout
        if "compacting conversation" not in cap.lower():
            time.sleep(2.0)  # settle past the boundary render
            return cap
        time.sleep(poll)
    raise TimeoutError(f"[{session}] /compact never finished within {timeout}s")


def run_compact_arm(sock, base_dir, n_lines, model):
    session = "sb-compact"
    cwd = Path(base_dir) / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    config_dir = msh.bootstrap_profile(base_dir, cwd)

    w0, h0 = FIRST_GEOMETRY
    msh.tmux(sock, "new-session", "-d", "-s", session, "-x", str(w0), "-y", str(h0),
              "-c", str(cwd), "env", f"CLAUDE_CONFIG_DIR={config_dir}",
              msh.CLAUDE_BIN, "--dangerously-skip-permissions", "--model", model)

    pid1, fd1 = attach_pty_client(sock, session, w0, h0)
    try:
        msh.clear_startup_dialogs(sock, session)

        prompt = (
            f"Output exactly {n_lines} lines and nothing else -- no preamble, no "
            f"markdown, no code fence, no summary. Each line must be exactly of "
            f"the form '{msh.PROBE_PREFIX}NNNNN' where NNNNN is the line number "
            f"from 00001 to {n_lines:05d}, zero-padded to 5 digits. One per "
            f"line, in order. Do not explain, just print the lines."
        )
        msh.send_prompt(sock, session, prompt)
        msh.wait_for_idle(sock, session)

        baseline_capture = msh.capture_full_history(sock, session)

        # The established recipe for a real slash command over send-keys:
        # a plain literal `-l` payload + Enter (this repo's own watchdog
        # module uses the identical shape).
        msh.tmux(sock, "send-keys", "-t", session, "-l", "/compact")
        time.sleep(0.3)
        msh.tmux(sock, "send-keys", "-t", session, "Enter")
        time.sleep(2.0)
        wait_for_compact_to_finish(sock, session)

        final_capture = msh.capture_full_history(sock, session)
    finally:
        kill_pty_client(pid1, fd1)

    tpath, expected = msh.read_assistant_probe_lines(config_dir, cwd)
    expected = [ln for ln in expected if ln.startswith(msh.PROBE_PREFIX)]

    return {
        "event": "compact",
        "session": session,
        "transcript": str(tpath) if tpath else None,
        "expected_line_count": len(expected),
        "baseline": msh.compare(baseline_capture, expected),
        "final": msh.compare(final_capture, expected),
    }


def teardown(sock, root, keep):
    try:
        msh.tmux(sock, "kill-server", timeout=10)
    except Exception as e:
        print(f"teardown: kill-server failed (non-fatal): {e}", file=sys.stderr)
    if not keep:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", choices=["resize-multiclient", "compact"],
                     default="resize-multiclient")
    ap.add_argument("--lines", type=int, default=80)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--cycles", type=int, default=4,
                     help="resize-multiclient only: attach/detach cycles")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    sock = f"airuleset-sbevents-{uuid.uuid4().hex[:10]}"
    root = Path("/tmp") / f"airuleset-sbevents-{uuid.uuid4().hex[:10]}"

    results = {"tmux_version": msh.run(["tmux", "-V"]).stdout.strip(),
               "cc_version": msh.run([msh.CLAUDE_BIN, "--version"]).stdout.strip(),
               "args": vars(args), "arms": {}}
    try:
        if args.event == "resize-multiclient":
            for mode in ("latest", "manual"):
                print(f"=== arm: window-size={mode} ===", file=sys.stderr)
                base_dir = root / mode
                base_dir.mkdir(parents=True, exist_ok=True)
                arm = run_resize_multiclient_arm(sock, base_dir, mode, args.lines,
                                                   args.model, args.cycles)
                results["arms"][mode] = arm
                print(json.dumps(arm, indent=2), file=sys.stderr)
        else:
            base_dir = root / "compact"
            base_dir.mkdir(parents=True, exist_ok=True)
            arm = run_compact_arm(sock, base_dir, args.lines, args.model)
            results["arms"]["compact"] = arm
            print(json.dumps(arm, indent=2), file=sys.stderr)
    finally:
        teardown(sock, root, args.keep)

    print(json.dumps(results, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
