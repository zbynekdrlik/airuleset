#!/usr/bin/env python3
"""Manual LIVE check for the `/goal` arm delivery (#1181) -- never run by CI.

WHAT IT PROVES. It drives a REAL Claude Code session through the REAL watchdog
delivery path: `watchdog.goal.deliver_goal(origin="self-callback")` ->
`_send_goal_verified` -> the one `keys` primitive, exactly as job 9 does after
the owner's `/autopilot`. Then it reads the result back from the pane (the
`◎ /goal` footer) and from the session transcript (a `user` turn / a `Goal set`
marker). One run = one row of the #1181 reproduction matrix.

#1181 proved with this script that a `self-callback` arm on a NON-declared pane
was keystroke-suppressed: it rode the staged `goal-sweep` machine-nudge kind,
which #1023 made default OFF on every box, so nothing was typed and the
zero-keystroke refusal was misreported as `skip:verify-failed` (box empty, no
transcript turn). Re-run it after touching the goal delivery to prove a real
session still arms.

ISOLATION -- read before running, it types into a terminal.
  * It starts its OWN tmux server on the private socket `-L airuleset-repro-1181`
    and kills it on exit (verify: `tmux -L airuleset-repro-1181 ls` fails).
  * Every tmux call of the watchdog code goes through `PrivateRun`, which pins
    it to that socket with `-S`. Any OTHER command the watchdog code tries to
    spawn (`kill`, ...) is REFUSED, except the read-only `ps`.
  * The driver unsets `TMUX`/`TMUX_PANE` and points `TMUX_TMPDIR` at scratch, so
    a stray bare `tmux` call cannot reach the owner's default server.
  * The claude session runs in a scratch dir under `--scratch` (default
    `<repo>/tmp/repro-1181`, git-ignored via `tmp/`), never a project dir, with
    `--setting-sources project` so the user's hooks stay out of the experiment.
  * The driver's own `HOME` is a scratch dir: the nudge-kind state it reads
    (`nudges-kinds.json`) and the `goal-sync.log` it writes stay there.
  * It costs real tokens: one "reply ok" turn per run, plus the armed turn a
    `/goal` starts (the server is killed right after the arm is read back).

USAGE
  python3 scripts/repro_goal_arm_1181.py [--version 2.1.283] [--payload full|short]
      [--kinds-on goal-sweep] [--remote-control] [--keep]
Prints ONE JSON object: the factors, the delivery word, the watchdog log lines,
and the observed pane/transcript facts.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOCKET_NAME = "airuleset-repro-1181"
SHORT_PAYLOAD = "/goal reply with the single word ok, then the goal is met"
READY_TIMEOUT_S = 90
TURN_TIMEOUT_S = 180


def socket_path():
    """Where `tmux -L airuleset-repro-1181` puts its socket for this user."""
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return os.path.join(base, "tmux-%d" % os.getuid(), SOCKET_NAME)


class PrivateRun:
    """The `run` seam the watchdog code calls for every subprocess. tmux is
    pinned to the private socket; `ps` passes (read-only); anything else is
    refused and recorded, so the experiment can never signal or type into a
    process outside the private server."""

    def __init__(self, sock):
        self.sock = sock
        self.refused = []

    def __call__(self, argv, timeout=8):
        argv = list(argv)
        if argv and argv[0] == "tmux":
            argv = ["tmux", "-S", self.sock] + argv[1:]
        elif not (argv and argv[0] == "ps"):
            self.refused.append(argv)
            return ""
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            print("run failed: %r (%s)" % (argv, e), file=sys.stderr)
            return ""
        return r.stdout if r.returncode == 0 else ""


def tmux(sock, *args):
    return subprocess.run(["tmux", "-S", sock, *args], capture_output=True,
                          text=True, timeout=10).stdout


def capture(sock, pane, lines=60):
    return tmux(sock, "capture-pane", "-p", "-t", pane, "-S", "-%d" % lines)


def start_server(sock, cwd, bindir, remote_control):
    """Start the private server with a placeholder window (NOT a claude pane,
    so the resolver ignores it), keep dead panes visible (`remain-on-exit`, so a
    crashed claude leaves its last frame), then open the claude window. Returns
    the claude pane id. `-f /dev/null`: none of the owner's tmux.conf hooks."""
    if os.path.exists(sock):
        probe = subprocess.run(["tmux", "-S", sock, "ls"], capture_output=True,
                               text=True, timeout=10)
        if probe.returncode == 0:
            sys.exit("refusing: a server is already running on %s "
                     "(`tmux -L %s kill-server` first)" % (sock, SOCKET_NAME))
        os.unlink(sock)                     # a dead server's leftover socket file
    cmd = "claude --setting-sources project"
    if remote_control:
        cmd += " --remote-control"
    env = {"HOME": str(Path.home()), "USER": os.environ.get("USER", ""),
           "PATH": "%s:/usr/local/bin:/usr/bin:/bin" % bindir,
           "TERM": "xterm-256color", "LANG": "C.UTF-8", "SHELL": "/bin/bash"}
    base = ["tmux", "-S", sock, "-f", "/dev/null"]
    subprocess.run(base + ["new-session", "-d", "-s", "repro", "-x", "200",
                           "-y", "50", "sleep 3600"], env=env, check=True,
                   timeout=10)
    tmux(sock, "set-option", "-g", "remain-on-exit", "on")
    out = subprocess.run(base + ["new-window", "-d", "-P", "-F", "#{pane_id}",
                                 "-t", "repro", "-c", str(cwd), cmd],
                         env=env, check=True, capture_output=True, text=True,
                         timeout=10).stdout
    return out.strip()


def wait_ready(sock, pane, pane_text):
    """Take the DEFAULT of each first-start dialog (folder trust = yes; the
    external CLAUDE.md imports = no, which keeps the scratch context small),
    then wait for a bare input box."""
    deadline = time.time() + READY_TIMEOUT_S
    while time.time() < deadline:
        cap = capture(sock, pane)
        if "Enter to confirm" in cap:
            tmux(sock, "send-keys", "-t", pane, "Enter")
            time.sleep(2)
            continue
        box = pane_text._input_line_text(cap)
        # a fresh session greets with a dim `Try "…"` suggestion in the box
        if box == "" or (box or "").startswith('Try "'):
            return True
        time.sleep(1)
    return False


def transcript_for(projects_dir, cwd, watchdog):
    t = watchdog.find_active_transcript(projects_dir, str(cwd))
    return Path(t[0]) if t else None


def run_turn(sock, pane, projects_dir, cwd, watchdog):
    """One tiny turn so the session owns a transcript (the pane resolver needs
    it; a real self-callback always follows the `/autopilot` turn)."""
    tmux(sock, "send-keys", "-t", pane, "-l", "--", "reply with the single word ok")
    time.sleep(0.5)
    tmux(sock, "send-keys", "-t", pane, "Enter")
    deadline = time.time() + TURN_TIMEOUT_S
    while time.time() < deadline:
        tp = transcript_for(projects_dir, cwd, watchdog)
        if tp and tp.exists():
            rows = [json.loads(ln) for ln in tp.read_text().splitlines() if ln.strip()]
            if any(r.get("type") == "assistant" for r in rows):
                return tp
        time.sleep(2)
    return None


def transcript_after(tp, offset):
    """(user turns, `Goal set` seen) appended after byte `offset`."""
    with open(tp, "rb") as f:
        f.seek(offset)
        tail = f.read().decode("utf-8", "replace")
    users, goal_set = [], False
    for ln in tail.splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        blob = json.dumps(r)
        if "Goal set" in blob:
            goal_set = True
        if r.get("type") == "user":
            users.append(blob[:160])
    return users, goal_set


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default="2.1.283",
                    help="a binary under ~/.local/share/claude/versions/")
    ap.add_argument("--payload", choices=("full", "short"), default="full")
    ap.add_argument("--kinds-on", default="",
                    help="comma list written to the scratch nudges-kinds.json "
                         "(empty = every staged kind OFF, as on every box)")
    ap.add_argument("--remote-control", action="store_true")
    ap.add_argument("--scratch", default=str(REPO / "tmp" / "repro-1181"))
    ap.add_argument("--keep", action="store_true",
                    help="leave the private server running (kill it yourself)")
    a = ap.parse_args()

    binary = Path.home() / ".local/share/claude/versions" / a.version
    if not binary.exists():
        sys.exit("no such claude version: %s" % binary)
    stamp = time.strftime("%H%M%S")
    scratch = Path(a.scratch).resolve()
    cwd, bindir, home = (scratch / ("cwd-" + stamp), scratch / ("bin-" + stamp),
                         scratch / ("home-" + stamp))
    for d in (cwd, bindir, home / ".claude"):
        d.mkdir(parents=True, exist_ok=True)
    (bindir / "claude").symlink_to(binary)   # comm stays `claude` for the resolver
    kinds = [k for k in a.kinds_on.split(",") if k]
    (home / ".claude" / "nudges-kinds.json").write_text(json.dumps({"on": kinds}))

    sock = socket_path()
    real_projects = Path.home() / ".claude" / "projects"
    # Isolation belts BEFORE importing the watchdog: no inherited tmux target,
    # no reachable default server, a scratch HOME for its state files.
    for k in ("TMUX", "TMUX_PANE", "AIRULESET_TEST_IGNORE_DISABLE"):
        os.environ.pop(k, None)
    os.environ["TMUX_TMPDIR"] = str(scratch)
    real_home = str(Path.home())
    sys.path.insert(0, str(REPO))
    import goal_registry
    import watchdog
    from watchdog import goal, pane_text
    from watchdog import tmux_io
    tmux_io._tmux_server_pids = lambda run=None: []   # never SIGUSR1 anything

    result = {"version": a.version, "payload": a.payload, "kinds_on": kinds,
              "remote_control": a.remote_control}
    pane = None
    try:
        pane = start_server(sock, cwd, bindir, a.remote_control)
        if not wait_ready(sock, pane, pane_text):
            result["error"] = "session never reached a bare input box"
            result["frame_tail"] = capture(sock, pane).rstrip().splitlines()[-25:]
            return result
        tp = run_turn(sock, pane, real_projects, cwd, watchdog)
        if tp is None:
            result["error"] = "the warm-up turn never produced a transcript"
            return result
        # past the #1110 transcript-liveness window, like a real idle pane
        time.sleep(watchdog.goal_turn_liveness.GOAL_TURN_LIVE_WINDOW_S + 5)
        text = (goal_registry.render_goal_line("full", "parallel")
                if a.payload == "full" else SHORT_PAYLOAD)
        offset = tp.stat().st_size
        run = PrivateRun(sock)
        os.environ["HOME"] = str(home)
        logs, out = [], {}
        t0 = time.time()
        try:
            word = goal.deliver_goal(tp.stem, str(cwd), text, "full", run=run,
                                     projects_dir=real_projects, now=time.time(),
                                     state={}, request_ts=time.time(),
                                     origin="self-callback", logs=logs, out=out)
        finally:
            os.environ["HOME"] = real_home
        time.sleep(0.5)   # an armed /goal starts a turn at once: read, then kill
        users, goal_set = transcript_after(tp, offset)
        cap = capture(sock, pane)
        result.update(
            sid=tp.stem, payload_len=len(text), word=word,
            delivery_s=round(time.time() - t0, 1), logs=logs,
            refused_commands=run.refused,
            armed=watchdog.pane_goal_armed(cap),
            box=pane_text._input_line_text(cap),
            transcript_user_turns=len(users), transcript_goal_set=goal_set,
            frame_tail=cap.rstrip().splitlines()[-8:])
        return result
    finally:
        if not a.keep:
            subprocess.run(["tmux", "-S", sock, "kill-server"],
                           capture_output=True, timeout=10)
        shutil.rmtree(bindir, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
