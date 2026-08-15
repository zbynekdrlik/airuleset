"""Idle-prompt backstop + stale-exec-marker hygiene sweep jobs (run_once jobs 5 & 22).

Extracted verbatim from ``watchdog/__init__.py`` as item G step 14 of the
definitive module split (issue #433). Two small, independent sweep-job
families that read session transcripts and ``/tmp`` marker files off disk:

* Job 5 -- ``deliver_pending_done`` and its helpers (``_transcript_for_sid``,
  ``_cwd_from_transcript``, ``_bg_monitor_in_cwd``, ``_safe_mtime``,
  ``_safe_unlink``): the reliable backstop that delivers a pending done-ping
  the unreliable ``idle_prompt`` event failed to send.
* Job 22 -- ``_session_id_is_live`` and ``cleanup_stale_exec_markers``: stale
  main-exec bypass-marker hygiene (#97).

This is a back-reference module: it ``import watchdog`` and reaches every
package-level name it did NOT co-move -- the transcript readers now physically
in ``watchdog/transcripts.py`` (``_iter_jsonl_tail``, ``transcript_last_marker``,
``project_label``, ``find_active_transcript``), ``_default_run`` /
``list_claude_panes`` (``watchdog`` / ``watchdog/tmux_io.py``), and
``PROJECTS_DIR`` (``__init__``) -- call-time as ``watchdog.<name>``. That is the
existing submodule convention and it keeps every ``patch.object(watchdog,
"<name>", ...)`` seam resolving unchanged (the step-1 grep-audit lesson; see
``.claude/rules/internals-watchdog.md``). The intra-module co-moved calls stay
bare: the step-14 four-idiom C5 audit proved none of the eight moved names are
patched at the ``watchdog.`` path.

Every name here (the eight functions AND the ``PENDING_*`` /
``MAIN_EXEC_MARKER_MAX_AGE_S`` / ``_EXEC_MARKER_PREFIXES`` constants) is
re-exported into the ``watchdog`` namespace by the positional facade import in
``__init__.py``. The ``PENDING_*`` constants moved here because their only
consumers are job 5 (``deliver_pending_done``'s def-time defaults) and
run_once's own signature default (which delegates to it) -- the step-14 design
rule (def-time defaults -> move + re-export). ``run_once`` (still in
``__init__``) resolves them at its def time through that re-export, which lands
above run_once's def.
"""

import os
from pathlib import Path

import watchdog

# (5) DELIVER A PENDING ✅ — the reliable backstop for the unreliable idle_prompt.
# notify-discord-pending.sh (Stop) records a ✅ DONE to /tmp/claude-discord-pending-
# <sid>; notify-discord.sh delivers it on the `idle_prompt` Notification event. But
# Claude Code emits idle_prompt UNRELIABLY over tmux/SSH (the same reason ❓ was
# moved to immediate), so over SSH a completed turn's ✅ ping silently never arrives —
# the pending just sits in /tmp (verified: undelivered files on dev2). The watchdog
# polls reliably, so it delivers a pending ✅ once the session has been idle >= GRACE
# (the user is away — the mobile-app "done when idle" model). It delivers ONLY if the
# session's CURRENT last marker is STILL ✅ — if the session re-fired (a background
# task re-invoked it → now ⏳, or it moved on), the ✅ is stale and is cleared WITHOUT
# pinging, so the device never says "done" for work that actually kept going. A
# pending older than MAX_STALE is a legacy orphan (the user has long moved on) →
# cleared without pinging. PING ONLY; claim-then-send so it can't double-fire with the
# idle hook.
PENDING_DONE_GRACE = 120          # idle this long after ✅ → user is away → deliver
PENDING_DONE_MAX_STALE = 12 * 3600  # older → legacy orphan, clear without pinging
PENDING_PREFIX = "/tmp/claude-discord-pending-"


# --------------------------------------------------------------------------- #
# Pending-✅ delivery (job 5) — reliable backstop for the unreliable idle_prompt.
# --------------------------------------------------------------------------- #

def _transcript_for_sid(projects_dir, sid):
    """Path of the session transcript <projects>/*/<sid>.jsonl, or None. (The file
    survives the pane closing, so a closed session's marker/idle is still readable.)"""
    if not sid:
        return None
    for p in Path(projects_dir).glob("*/%s.jsonl" % sid):
        return p
    return None


def _cwd_from_transcript(path):
    """The session cwd recorded in the transcript (most recent entry carrying one),
    or '' — used for the ✅ ping's project header."""
    try:
        for entry in reversed(watchdog._iter_jsonl_tail(path, max_lines=120)):
            if isinstance(entry, dict) and entry.get("cwd"):
                return entry["cwd"]
    except Exception:
        pass
    return ""


def _bg_monitor_in_cwd(cwd, run=None):
    """True if a Claude `shell-snapshots` background shell is still alive in `cwd` —
    a ✅ over a still-running background monitor is likely intermediate, so defer the
    ping (mirrors notify-discord.sh's guard). Best-effort; False on any error."""
    if not cwd:
        return False
    run = run or watchdog._default_run
    out = run(["pgrep", "-f", "shell-snapshots"])
    for pid in (out or "").split():
        try:
            if os.readlink("/proc/%s/cwd" % pid.strip()) == cwd:
                return True
        except OSError:
            continue
    return False


def deliver_pending_done(now, send_fn, projects_dir, owner_by_sid=None,
                         account_owner="", dry_run=False,
                         done_grace=PENDING_DONE_GRACE, max_stale=PENDING_DONE_MAX_STALE,
                         pending_prefix=PENDING_PREFIX, bg_check=None,
                         owner_by_cwd=None, owners_seen=None):
    """Sweep /tmp/claude-discord-pending-* and deliver a ✅ DONE ping the unreliable
    idle_prompt event failed to deliver. Delivers ONLY when the session is genuinely,
    still done: the pending exists AND the session's CURRENT last marker is STILL ✅
    AND it has been idle >= done_grace (user away). A session that re-fired (a
    background task re-invoked it → last marker now ⏳, or it moved on) has its stale
    ✅ CLEARED without pinging — so the device is never told "done" for work that kept
    going (the exact confusion to avoid). PING ONLY; claim-then-send (rm before send)
    so it can't double-fire with the idle hook. Best-effort; returns log lines.

    OWNER resolution is three-step, and the last step is deliberately allowed to
    yield nothing. `owner_by_sid` is authoritative but only covers sessions the
    caller's pane loop registered THIS sweep; `owner_by_cwd` recovers the rest
    from the session's own working directory. `account_owner` — "the first owner
    seen" — is a legitimate answer only on a box where every pane belongs to one
    person; where several do, it is a coin flip, and a ✅ landing in the wrong
    person's thread is worse than one with no @mention: the real owner never
    sees it and someone else gets the noise. dev2 (david + marek + zbynek panes)
    delivered zbynek's presenter ✅ into david's thread that way on 2026-07-29."""
    import glob as _glob
    owner_by_sid = owner_by_sid or {}
    owner_by_cwd = owner_by_cwd or {}
    # An EMPTY owners_seen means the caller did not measure — keep the fallback
    # (every pre-existing caller and test relies on it).
    ambiguous = len(set(owners_seen or ())) > 1
    bg_check = bg_check if bg_check is not None else _bg_monitor_in_cwd
    logs = []
    plen = len(os.path.basename(pending_prefix))
    for pf in sorted(_glob.glob(pending_prefix + "*")):
        try:
            with open(pf) as f:
                content = f.read().strip()
        except OSError:
            continue
        if not content.startswith("✅"):       # ❓ sends immediately, never pends; skip anything else
            continue
        sid = os.path.basename(pf)[plen:]
        text = content[1:].strip()             # drop the leading ✅
        tpath = _transcript_for_sid(projects_dir, sid)
        if tpath is not None:
            try:
                idle = now - tpath.stat().st_mtime
            except OSError:
                idle = now - _safe_mtime(pf)
            marker = watchdog.transcript_last_marker(tpath)   # '' for a closed/normal-ended session
            cwd = _cwd_from_transcript(tpath)
        else:
            idle = now - _safe_mtime(pf)
            marker, cwd = "✅", ""              # no transcript → trust the recorded ✅

        # Deliver ONLY while the session's CURRENT last marker is still ✅. If it
        # re-fired (a background task re-invoked it → ⏳), asked ❓, hit an api-error,
        # or ended a later turn markerless — anything but ✅ — the done-claim is no
        # longer current: clear it, NEVER ping "done" for work that continued. (An
        # orphan with no transcript keeps the recorded marker="✅" and is trusted.)
        if marker != "✅":
            if not dry_run:
                _safe_unlink(pf)
            logs.append("cleared non-✅ sid=%s (now %r)" % (sid[:8], marker))
            continue
        if idle < done_grace:
            continue                            # too fresh — user may continue / idle hook may fire
        if idle > max_stale:
            if not dry_run:
                _safe_unlink(pf)
            logs.append("cleared stale ✅ sid=%s idle=%dh" % (sid[:8], int(idle // 3600)))
            continue
        if cwd and bg_check(cwd):
            continue                            # bg monitor alive → ✅ likely intermediate, defer
        if not dry_run:
            _safe_unlink(pf)                    # claim first so a concurrent idle hook can't double-send
        project = watchdog.project_label(cwd) if cwd else "unknown"
        owner = (owner_by_sid.get(sid)
                 or (owner_by_cwd.get(cwd) if cwd else None)
                 or ("" if ambiguous else account_owner)
                 or None)
        send_fn("✅ **%s** — hotovo\n> %s" % (project, text[:250]),
                owner=owner, dedup_key="done:%s" % sid, dry_run=dry_run)
        logs.append("delivered ✅ sid=%s [%s] idle=%dm" % (sid[:8], project, int(idle // 60)))
    return logs


def _safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Job 22 — STALE EXEC-MARKER CLEANUP (#97, 2026-07-27). block-main-
# implementation.sh's bypass markers (/tmp/airuleset-main-exec-ok-<sid>, and
# the legacy /tmp/airuleset-fable-exec-ok-<sid>) are ONE-SHOT since #80 — the
# hook itself deletes a marker the moment it honors it. But a marker touched
# for a session that then just ENDS without ever making another main-agent
# Bash/Edit/Write call never gets consumed, and sits in /tmp forever (a real
# one found on gk: 0 bytes, ~21h old, for a session id that no longer ran
# anywhere). This is HYGIENE, not a security hole — the hook pairs a marker
# to its session id, so a marker for a dead session is already inert; the
# ONLY hazard is deleting a marker that belongs to a session STILL RUNNING
# (that would silently revoke a deliberately granted exception mid-work).
# So cleanup requires BOTH: the marker is older than `max_age_s`, AND no
# currently-live pane's transcript stem matches its session id.
MAIN_EXEC_MARKER_MAX_AGE_S = 6 * 3600     # a one-shot marker has no business outliving a session by this long
_EXEC_MARKER_PREFIXES = ("airuleset-main-exec-ok-", "airuleset-fable-exec-ok-")


def _session_id_is_live(sid, run=None, projects_dir=None):
    """True when SOME currently-live claude pane's transcript stem is this
    exact session id — regardless of which cwd it runs in (unlike
    `_find_pane_for_session`, which needs a specific target cwd; a stale
    marker only carries a session id, no cwd)."""
    run = run or watchdog._default_run
    projects_dir = projects_dir or watchdog.PROJECTS_DIR
    for _pid, cwd in watchdog.list_claude_panes(run):
        tinfo = watchdog.find_active_transcript(projects_dir, cwd)
        if tinfo and tinfo[0].stem == sid:
            return True
    return False


def cleanup_stale_exec_markers(now, run=None, projects_dir=None,
                               max_age_s=MAIN_EXEC_MARKER_MAX_AGE_S,
                               tmp_dir="/tmp", dry_run=False):
    """Job 22 — see the section comment. Best-effort (never raises); returns
    log lines. Never removes a marker whose session id still resolves to a
    live pane, no matter how old the file is."""
    logs = []
    try:
        entries = os.listdir(tmp_dir)
    except OSError:
        return logs
    for name in entries:
        prefix = next((p for p in _EXEC_MARKER_PREFIXES if name.startswith(p)), None)
        if not prefix:
            continue
        sid = name[len(prefix):]
        if not sid:
            continue
        path = os.path.join(tmp_dir, name)
        age = now - _safe_mtime(path)
        if age < max_age_s:
            continue                            # not old enough to be orphaned yet
        if _session_id_is_live(sid, run=run, projects_dir=projects_dir):
            continue                            # a live session — NEVER touch its marker
        if not dry_run:
            _safe_unlink(path)
        logs.append("exec-marker-cleanup %s age=%ds" % (name, int(age)))
    return logs
