"""api-watchdog — detect a Claude Code session STALLED on an API error and
auto-resume it (`tmux send-keys "continue"`), pinging Discord on the stall and
again if it never recovers.

WHY A POLLER, NOT A HOOK
------------------------
When a turn dies on an API error (529 overloaded, ConnectionRefused, rate/usage
limit) Claude Code ABORTS the turn — it does NOT reliably fire the `Stop` hook
with the error as `last_assistant_message`. So the `notify-api-error.sh` Stop
hook is blind to exactly the event it was meant to catch (the user saw 529s in 3
projects and got no ping). A timer that reads each `claude` pane's transcript +
screen catches the stall reliably. This is the community pattern (amux,
claude-auto-retry): watch the tmux panes, steer a resume message in.

DETECTION (per `claude` tmux pane)
----------------------------------
- Map the pane's cwd → its newest transcript under ~/.claude/projects/<enc>/.
- ERROR signal: the session's last assistant entry is flagged `isApiErrorMessage`
  (Claude Code's own definitive marker), or the visible pane shows an API-error
  banner (`is_api_error`). The flag is precise; a user merely quoting "API Error"
  in prose never sets it.
- IDLE signal: the transcript has not advanced for >= GRACE seconds (Claude Code's
  own quick retries keep the transcript moving, so a 5-min-stale transcript means
  the retries were exhausted and it is genuinely parked).
- STALLED = ERROR and IDLE.

ACTION (state machine, see `decide`)
------------------------------------
first sighting -> 'wait' (record first_seen)
+GRACE still stalled -> 'nudge' #1  (send `continue` + ONE Discord ping)
+INTERVAL each      -> 'nudge' #2, #3
after MAX_NUDGES     -> 'escalate' (ping "gave up", stop — no continue-spam during
                        a long Anthropic outage)
recovered            -> key dropped from state (a future error starts fresh)

A session waiting on a real `❓` is NEVER auto-continued: its last assistant entry
is the question (not `isApiErrorMessage`), so the error signal is false.

This module is PURE logic + thin tmux shims. The I/O (`run` = tmux exec, `send_fn`
= Discord send) is injectable so the state machine is unit-tested with no tmux and
no network.
"""

import hashlib
import json
import os
import time
from pathlib import Path

# Tunables (the CLI may override; defaults match the user's spec: 5-min grace,
# `continue`, 3 retries then give up).
GRACE_SECONDS = 5 * 60
RETRY_INTERVAL_SECONDS = 5 * 60
MAX_NUDGES = 3
NUDGE_TEXT = "continue"

PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATE_PATH = Path.home() / ".claude" / "api-watchdog-state.json"

# Synthetic assistant entries Claude Code appends that are NOT a real reply — when
# scanning back for "the last real assistant message" these are skipped so a
# trailing sentinel does not mask an api-error entry just before it.
_SENTINELS = {"", "No response requested."}


# --------------------------------------------------------------------------- #
# Pure helpers (no tmux, no network)
# --------------------------------------------------------------------------- #

def encode_project_dir(cwd):
    """Claude Code's transcript-dir name for a cwd: every '/', '.' and '_' -> '-'.

    /home/newlevel/devel/website-newlevel.media
        -> -home-newlevel-devel-website-newlevel-media
    /home/newlevel/devel/tomas_pardubsky/cold_mailing
        -> -home-newlevel-devel-tomas-pardubsky-cold-mailing
    """
    return "".join("-" if c in "/._" else c for c in str(cwd))


def find_active_transcript(projects_dir, cwd):
    """(path, mtime) of the newest *.jsonl in the cwd's transcript dir, or None."""
    d = Path(projects_dir) / encode_project_dir(cwd)
    if not d.is_dir():
        return None
    newest, newest_m = None, -1.0
    for p in d.glob("*.jsonl"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest_m:
            newest, newest_m = p, m
    return (newest, newest_m) if newest else None


def _iter_jsonl_tail(path, max_lines=60):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return []
    out = []
    for ln in raw.splitlines()[-max_lines:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _entry_text(entry):
    msg = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def transcript_last_error(path):
    """Text of the session's last real assistant message IF Claude Code flagged it
    as an API error (`isApiErrorMessage`), else ''. Trailing synthetic entries
    ("No response requested.") are skipped; the first real assistant message that
    is NOT an api-error means the session is fine (not stalled)."""
    for entry in reversed(_iter_jsonl_tail(path)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        if entry.get("isApiErrorMessage") is True:
            return _entry_text(entry) or "API Error"
        if (_entry_text(entry) or "").strip() in _SENTINELS:
            continue            # synthetic — keep scanning back
        return ""               # a real normal reply → not stalled
    return ""


def pane_shows_api_error(captured):
    """True if the captured tmux pane text shows an API-error banner. Secondary to
    the transcript flag; strips TUI box-drawing prefixes before the match."""
    from notify import is_api_error
    if not captured:
        return False
    for raw in captured.splitlines():
        ln = raw.strip().lstrip("│┃|>*● \t").strip()
        if is_api_error(ln):
            return True
    return False


def _hash(text):
    return hashlib.sha1((text or "").strip().encode("utf-8", "replace")).hexdigest()[:12]


def decide(state, key, err_hash, now,
           interval=RETRY_INTERVAL_SECONDS, max_nudges=MAX_NUDGES):
    """Pure decision for ONE stalled session. Returns (action, entry) where action
    is 'nudge' | 'wait' | 'escalate' | 'noop'. `entry` is the updated state record
    (caller persists state[key] = entry).

    The INITIAL grace is enforced by the caller: `run_once` only calls `decide`
    once the transcript has been idle >= GRACE (i.e. the stall is already >= 5 min
    old), so the FIRST qualifying sighting nudges IMMEDIATELY — "5 min after the
    error" — rather than waiting a second grace period. Thereafter a nudge fires
    only every `interval`; after `max_nudges` it escalates once, then noops. A
    different err_hash (a new error) restarts the cycle."""
    e = state.get(key)
    if e is None or e.get("hash") != err_hash:
        # already >= grace stale (caller-gated) → first `continue` goes out NOW
        return "nudge", {"hash": err_hash, "first_seen": now, "nudges": [now], "escalated": False}
    if e.get("escalated"):
        return "noop", e
    nudges = list(e.get("nudges", []))
    if (now - (nudges[-1] if nudges else e.get("first_seen", now))) < interval:
        return "wait", e
    if len(nudges) >= max_nudges:
        e2 = dict(e)
        e2["escalated"] = True
        return "escalate", e2
    e2 = dict(e)
    e2["nudges"] = nudges + [now]
    return "nudge", e2


def load_state(state_path):
    try:
        with open(state_path) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state_path, state):
    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(state_path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, state_path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# tmux shims (the only impure part; injectable as `run`)
# --------------------------------------------------------------------------- #

def _default_run(argv, timeout=8):
    import subprocess
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def list_claude_panes(run=None):
    """[(pane_id, cwd)] for every tmux pane running `claude`, deduped by pane_id
    (grouped sessions share the same pane_id)."""
    run = run or _default_run
    out = run(["tmux", "list-panes", "-a", "-F",
               "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"])
    seen, res = set(), []
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid, cmd, cwd = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if cmd != "claude" or not pid or pid in seen:
            continue
        seen.add(pid)
        res.append((pid, cwd))
    return res


def capture_pane(pane_id, run=None, lines=40):
    run = run or _default_run
    return run(["tmux", "capture-pane", "-p", "-t", pane_id, "-S", "-%d" % lines])


def send_continue(pane_id, text=NUDGE_TEXT, run=None):
    """Type `text` literally into the pane, then press Enter to submit it."""
    run = run or _default_run
    run(["tmux", "send-keys", "-t", pane_id, "-l", text])
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])


# --------------------------------------------------------------------------- #
# One poll cycle
# --------------------------------------------------------------------------- #

def run_once(now=None, dry_run=False, run=None, send_fn=None,
             projects_dir=PROJECTS_DIR, state_path=STATE_PATH,
             grace=GRACE_SECONDS, interval=RETRY_INTERVAL_SECONDS,
             max_nudges=MAX_NUDGES):
    """Scan every `claude` pane once; nudge / escalate the stalled ones. Returns a
    list of human-readable action log lines (for --verbose / tests)."""
    now = time.time() if now is None else now
    run = run or _default_run
    from notify import compose_api_error_alert
    if send_fn is None:
        from notify import send as send_fn

    state = load_state(state_path)
    logs = []
    stalled = set()

    for pid, cwd in list_claude_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, tmtime = tinfo
        if (now - tmtime) < grace:        # transcript still advancing → not stalled
            continue
        err_text = transcript_last_error(tpath)
        if not err_text and pane_shows_api_error(capture_pane(pid, run)):
            err_text = "API Error"
        if not err_text:
            continue                       # idle but no api-error signal → leave it

        key = tpath.stem                   # session id (stable across grouped panes)
        stalled.add(key)
        project = os.path.basename(cwd.rstrip("/")) or "unknown"
        err_hash = _hash(err_text)
        action, entry = decide(state, key, err_hash, now, interval, max_nudges)
        state[key] = entry

        if action == "nudge":
            n = len(entry["nudges"])
            logs.append("nudge#%d %s [%s]" % (n, project, key))
            if not dry_run:
                send_continue(pid, NUDGE_TEXT, run)
            if n == 1:                     # first nudge → tell the user it stalled
                send_fn(compose_api_error_alert(project, err_text),
                        dedup_key="apierr:%s:%s" % (key, err_hash), dry_run=dry_run)
        elif action == "escalate":
            logs.append("escalate %s [%s] — gave up after %d nudges" % (project, key, max_nudges))
            body = ("\U0001f6d1 **%s** — API chyba pretrváva\n> Po %d× `continue` sa to "
                    "stále nepohlo — treba zásah." % (project, max_nudges))
            send_fn(body, dedup_key="apierr-giveup:%s:%s" % (key, err_hash), dry_run=dry_run)
        else:
            logs.append("%s %s [%s]" % (action, project, key))

    # Drop recovered / vanished sessions so their next error starts a fresh cycle.
    for k in [k for k in state if k not in stalled]:
        del state[k]
    save_state(state_path, state)
    return logs
