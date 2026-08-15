"""Session heartbeat — per-session structured status file (#486, step G1).

PRODUCER + READER for the "one-glance" supervision redesign. A tiny JSON
heartbeat is written to ``~/.claude/session-status/<sid>.json`` on EVERY turn
boundary (Stop / SubagentStop / SessionStart) by three EXISTING hooks that
invoke this module's CLI. It carries the STRUCTURED facts a hook has
in-process for free — the turn's terminal status marker (❓/⏳/✅), the session
cwd, whether a ``/goal`` is armed (for a main Stop), and which hook event last
wrote it — so the watchdog can read a session's liveness / armed / marker
state from DATA instead of scraping the rendered tmux footer (the render-parse
blindness #486 exists to end).

G1 is ONLY the producer + this reader helper + tests. NOTHING consumes the
reader yet — the render path stays the single authoritative source until the
G5 parallel-run phase. Do not wire a consumer here.

FRESHNESS = the file's mtime. ``write_heartbeat`` replaces the file atomically
(temp + ``os.replace``) on every event, so its mtime is the heartbeat;
``read_status`` treats mtime (never the stored ``ts``, a debug convenience) as
the liveness signal and classifies a file older than ``stale_after_s`` as
STALE.

COST: the per-turn producer does NO network / gh / subprocess. The whole
CLI invocation costs ~0.15-0.48 s (typ. ~0.3 s), dominated by Python cold-start
plus importing ``watchdog/__init__.py`` (a warm ``import watchdog`` alone is
~0.05 s; the rest is interpreter start + the ``-m`` machinery). That import is
deliberate: a main Stop reuses the CANONICAL ``scan_goal_markers`` — the single
source of truth for the ``<local-command-stdout>Goal set:/cleared:`` markers,
never a re-implemented parser that could drift (#486's own thesis) — plus one
bounded, byte-prefiltered transcript tail scan. SubagentStop / SessionStart skip
the goal scan (and are guarded so their cost never blocks the host hook).
"""
import argparse
import json
import os
import re
import sys
import time
from collections import namedtuple
from pathlib import Path

SCHEMA_VERSION = 1
# G1 placeholder default. G3's one-glance predicate passes its own idle>N
# threshold; the reader's staleness verdict is always parameterised, never
# hard-wired to this number.
DEFAULT_STALE_AFTER_S = 300

_NOTE = (
    "airuleset session heartbeat (#486 G1). FRESHNESS = this file's mtime "
    "(rewritten atomically every hook event); the reader treats mtime as the "
    "heartbeat, `ts` is a debug convenience. marker = terminal ❓/⏳/✅ of the "
    "last turn's final text. goal_armed: true/false only meaningful on a main "
    "'stop' event; null = not applicable or undetermined. kind distinguishes a "
    "main session from a dispatched subagent (which SHARES the parent's sid, so "
    "it is keyed by agent_id to avoid clobbering the main file)."
)

# Reuse the single source of truth for /goal markers — never a re-implemented
# parser that could drift from the rest of the supervision (#486). Guarded so a
# broken package import degrades goal detection to "unknown" rather than
# breaking the module load.
try:
    from watchdog import scan_goal_markers as _scan_goal_markers
except Exception:  # pragma: no cover - defensive; package import is normally fine
    _scan_goal_markers = None


# --------------------------------------------------------------------------- #
# terminal status marker
# --------------------------------------------------------------------------- #

# A marker only counts at the START of the last line (after optional leading
# whitespace / markdown emphasis / bullet / heading chars) — NEVER mid-sentence.
# This mirrors notify-discord-pending.sh's own `^[[:space:]]*[*_>~-]*…❓` anchor
# and the documented incident it prevents (a `✅ DONE: … Discord ❓ ping …` line
# is a DONE turn whose ❓ is prose, not an active question). A bare substring
# test would re-land that bug.
_MARK_PREFIX = r"^[\s*_>~#-]*"
_MARK_NEEDS_RE = re.compile(_MARK_PREFIX + r"❓")
_MARK_WORKING_RE = re.compile(_MARK_PREFIX + r"⏳")
_MARK_DONE_LINE_RE = re.compile(
    _MARK_PREFIX + r"✅\s*(DONE|complete[d]?|work\s+complete)", re.I)
_MARK_DONE_HEADING_RE = re.compile(r"(?m)^#{1,6}\s*✅\s*Work\s+Complete\b", re.I)


def classify_marker(msg):
    """Terminal status marker of a turn's final assistant text: one of
    ``needs_you`` / ``working`` / ``done`` / ``unknown``.

    Mirrors the two existing gates so the heartbeat AGREES with what they
    already consider the turn's state: the marker is read from the START of the
    LAST non-blank line (``stop-check-status-marker.sh``'s terminal-marker rule
    + ``notify-discord-pending.sh``'s line-start anchor — a ❓/⏳/✅ MID-SENTENCE
    is prose, never a marker); an active ❓ on that line WINS over a
    ``## ✅ Work Complete`` heading elsewhere (``notify-discord-pending.sh``'s
    precedence); the completion-report heading anywhere counts as done. The
    ask-and-continue dual-marker shape (a ``❓ ASKED:`` line in the BODY,
    ``⏳ WORKING:`` as the terminal line) resolves to ``working`` — the terminal
    line decides.
    """
    if not msg:
        return "unknown"
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    if _MARK_NEEDS_RE.search(last):
        return "needs_you"
    if _MARK_WORKING_RE.search(last):
        return "working"
    if _MARK_DONE_LINE_RE.search(last):
        return "done"
    if _MARK_DONE_HEADING_RE.search(msg):
        return "done"
    return "unknown"


# --------------------------------------------------------------------------- #
# armed /goal (via the canonical scanner)
# --------------------------------------------------------------------------- #

def goal_armed_from_transcript(transcript_path):
    """``True`` / ``False`` / ``None`` for "is a ``/goal`` armed", read from the
    STRUCTURED transcript via the canonical ``watchdog.scan_goal_markers`` (so
    the heartbeat can never drift from the rest of the supervision).

    ``None`` = could not be attempted (no ``transcript_path``, or the canonical
    scanner unavailable). ``False`` = the scanner ran and found no ``Goal set:``
    as the newest marker in its scanned window — which, matching
    ``scan_goal_markers``'s own fail-safe, also covers an unreadable / missing
    transcript. ``True`` = the newest marker is ``Goal set:``.
    """
    if not transcript_path or _scan_goal_markers is None:
        return None
    try:
        _off, mark = _scan_goal_markers(transcript_path)
    except Exception:  # pragma: no cover - the scanner is itself fail-safe
        return None
    if not mark:
        return False
    return mark.get("state") == "set"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #

_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _safe(token):
    """Defang a session_id / agent_id to a filesystem-safe token. Close to the
    ``tr -cd 'A-Za-z0-9._-'`` filter the hooks apply to CC ids, but also strips
    the dot (this module builds the FILENAME itself, so dropping ``.`` keeps a
    crafted ``..`` id from ever forming a surprising name; a real uuid id
    carries no dot, so the two agree in practice)."""
    cleaned = _SAFE_RE.sub("", token or "")
    return cleaned or "unknown"


def status_dir(base_dir=None):
    """The directory heartbeat files live in. ``base_dir`` (explicit) >
    ``AIRULESET_SESSION_STATUS_DIR`` env (tests / overrides) > the default
    ``~/.claude/session-status/``."""
    if base_dir:
        return Path(base_dir)
    env = os.environ.get("AIRULESET_SESSION_STATUS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "session-status"


def status_path(sid, agent_id=None, base_dir=None):
    """Heartbeat file path. main → ``<sid>.json``; subagent →
    ``<sid>__<agent_id>.json``.

    A subagent SHARES the parent's sid (its SubagentStop payload carries the
    PARENT's ``session_id`` plus its own ``agent_id``), so it MUST be keyed by
    agent_id or it would clobber the main session's file — #486 point (b)."""
    base = status_dir(base_dir)
    sid_s = _safe(sid)
    if agent_id:
        return base / ("%s__%s.json" % (sid_s, _safe(agent_id)))
    return base / ("%s.json" % sid_s)


# --------------------------------------------------------------------------- #
# producer
# --------------------------------------------------------------------------- #

def build_heartbeat(payload, event, goal_armed=None, now=None):
    """Pure builder: the heartbeat dict from a hook payload + event. No I/O."""
    ts = int(now if now is not None else time.time())
    kind = "subagent" if event == "subagent_stop" else "main"
    msg = payload.get("last_assistant_message") or ""
    data = {
        "schema": SCHEMA_VERSION,
        "sid": payload.get("session_id") or "unknown",
        "kind": kind,
        "last_turn": event,
        "ts": ts,
        "cwd": payload.get("cwd") or "",
        "marker": classify_marker(msg),
        "goal_armed": goal_armed,
        "_note": _NOTE,
    }
    if kind == "subagent":
        data["agent_id"] = payload.get("agent_id") or "unknown"
        agent_type = payload.get("agent_type")
        if agent_type:
            data["agent_type"] = agent_type
    return data


def write_heartbeat(payload, event, base_dir=None, now=None):
    """Compute + ATOMICALLY write the heartbeat file; returns its Path.

    goal-armed is scanned ONLY for a main ``stop`` (n/a for a subagent, and a
    fresh ``session_start`` has no goal yet). The write is temp-file +
    ``os.replace`` so a reader never sees a half-written file — #486 point (c).
    """
    goal_armed = None
    if event == "stop":
        goal_armed = goal_armed_from_transcript(payload.get("transcript_path"))
    data = build_heartbeat(payload, event, goal_armed=goal_armed, now=now)
    sid = payload.get("session_id") or "unknown"
    # A subagent is ALWAYS keyed by agent_id (defaulting to "unknown" when the
    # payload lacks one) so it can never fall back to <sid>.json and clobber the
    # main session's file — the whole point of #486 (b).
    agent_id = (payload.get("agent_id") or "unknown") if event == "subagent_stop" else None
    path = status_path(sid, agent_id, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.tmp.%d" % (path.name, os.getpid()))
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# reader helper
# --------------------------------------------------------------------------- #

SessionStatus = namedtuple(
    "SessionStatus",
    "state age_s data sid kind marker goal_armed cwd agent_id agent_type "
    "last_turn error",
)


def _emit_warn(on_warn, message):
    if on_warn is not None:
        on_warn(message)
    else:  # loud by default — a corrupt heartbeat must never pass silently
        print(message, file=sys.stderr)


def _absent(error=None):
    return SessionStatus("absent", None, None, None, None, None, None, None,
                         None, None, None, error)


def _corrupt(age, error, on_warn, path):
    _emit_warn(on_warn, "session-status: corrupt %s: %s" % (path, error))
    return SessionStatus("corrupt", age, None, None, None, None, None, None,
                         None, None, None, str(error))


def read_status(path=None, sid=None, agent_id=None, base_dir=None, now=None,
                stale_after_s=DEFAULT_STALE_AFTER_S, on_warn=None):
    """Read one heartbeat → a ``SessionStatus`` verdict.

    ``state`` ∈ {``fresh``, ``stale``, ``corrupt``, ``absent``}. Freshness is
    the file's MTIME vs ``stale_after_s`` (#486 point d). A half-written or
    otherwise unparseable file NEVER raises — it returns ``corrupt`` (data
    ``None``) and is logged LOUDLY via ``on_warn`` (default stderr), so a
    corrupt file can never crash a sweep (#486 point c).

    Pass ``path`` directly, or ``sid`` (+ ``agent_id`` for a subagent) to
    resolve it via ``status_path``.
    """
    if path is None:
        path = status_path(sid, agent_id, base_dir)
    path = Path(path)
    now = time.time() if now is None else now
    try:
        st = path.stat()
    except FileNotFoundError:
        return _absent()
    except OSError as exc:
        return _corrupt(None, exc, on_warn, path)
    age = now - st.st_mtime
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("heartbeat is not a JSON object")
    except Exception as exc:
        return _corrupt(age, exc, on_warn, path)
    state = "stale" if age > stale_after_s else "fresh"
    return SessionStatus(
        state, age, data,
        data.get("sid"), data.get("kind"), data.get("marker"),
        data.get("goal_armed"), data.get("cwd"),
        data.get("agent_id"), data.get("agent_type"), data.get("last_turn"),
        None,
    )


# --------------------------------------------------------------------------- #
# CLI — invoked by the three hooks (payload JSON on stdin)
# --------------------------------------------------------------------------- #

def main(argv=None):
    """Read the hook payload JSON on stdin, write the heartbeat. NEVER raises /
    always returns 0 — a heartbeat failure must not interfere with the Stop
    decision pipeline."""
    parser = argparse.ArgumentParser(prog="watchdog.session_status")
    parser.add_argument("--event", required=True,
                        choices=["stop", "subagent_stop", "session_start"])
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    try:
        write_heartbeat(payload, args.event)
    except Exception:  # pragma: no cover - producer must never break the hook
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
