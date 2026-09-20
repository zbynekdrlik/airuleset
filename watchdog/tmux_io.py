"""tmux I/O shims + pane keystroke helpers (the only impure part of the watchdog).

Extracted verbatim from ``watchdog/__init__.py`` as item G step 2 of the
definitive module split (issue #433). Everything here shells out to ``tmux``
(injectable as ``run``, defaulting to :func:`_default_run`) or walks ``/proc``:
the tmux socket-recovery + sudo-hosted-pane discovery (:func:`list_claude_panes`
and friends), pane capture / mode / owner reads, and the keystroke senders
(:func:`send_continue`, :func:`send_subagent_nudge`) plus
the shared dying-subagent nudge logic. Every name here is re-exported into the
``watchdog`` namespace by the positional facade import in ``__init__.py``, so all
existing ``watchdog.<name>`` seams (goal / compact / cross_stream / janitor,
hooks, tests) keep resolving unchanged.

Direction: back-reference module. Cross-references to any name that was a
top-level ``watchdog`` name go through the package namespace call-time
(``import watchdog`` at module top; ``watchdog.<name>(...)`` in bodies), which is
what keeps ``monkeypatch``/``patch.object(watchdog, ...)`` seams effective:
- ``watchdog._default_run`` -- the step-2 C5 grep found it patched
  (``patch.object(wd, "_default_run", ...)`` in test_goal_arm), so the seven
  ``run = run or watchdog._default_run`` fallbacks go through the package
  namespace, NOT a bare module-local name (the design's "module-local" note
  assumed a def-time ``run=_default_run`` default, but the real shape is a body
  ``or`` fallback and C5 proves it a live seam).
- ``watchdog._pane_hosted_claude_pid`` / ``watchdog._hosted_claude_cwd`` --
  patched by test_sudo_hosted_pane.
- ``watchdog.capture_pane`` / ``watchdog.pane_in_mode`` / ``watchdog.send_continue``
  -- the three heaviest monkeypatch seams in the codebase (the design mandates
  the package-namespace form for these even where a single step's grep is empty).
- ``watchdog.pane_at_idle_prompt`` (pane_classify, patched), ``watchdog.decide_working``
  (still in ``__init__``), ``watchdog.transcript_last_error`` /
  ``watchdog._iter_jsonl_tail`` (transcripts.py) -- external readers reached
  call-time through the package namespace.
Intra-module calls to names the step-2 C5 grep proves UNPATCHED (``_proc_read``,
the ``_tmux_*`` socket helpers, ``_strip_selected``, ``send_subagent_nudge``,
``_subagent_transcript_unsalvageable``) stay bare (byte-verbatim, C3 exception).

``NUDGE_TEXT`` / ``WORKING_NUDGE_TEXT`` still live in ``__init__.py`` (bound above
the facade-import position, proven unpatched) and are imported here at module top
-- ``NUDGE_TEXT`` is :func:`send_continue`'s def-time default, so it MUST be a
from-import, never a below-position ``from watchdog import <function>`` shape.
"""

import os
import re
import time

import watchdog
# WORKING_NUDGE_TEXT is no longer USED here (its `send_selfcheck` caller was
# removed when job 4 adopted send_verified, #497 batch 3) but is deliberately
# RE-EXPORTED — the module-split invariant test locks tmux_io.WORKING_NUDGE_TEXT
# is watchdog.WORKING_NUDGE_TEXT (it stays resident in __init__.py). The `as`
# alias marks it an intentional re-export so ruff does not flag F401.
from watchdog import NUDGE_TEXT, WORKING_NUDGE_TEXT as WORKING_NUDGE_TEXT

# #490 — transcript-proof submit verification (`send_verified`, below). CC
# writes the accepted `user` turn near-instantly, so most confirms land on the
# first poll; the window only ever runs to the end for a genuinely swallowed
# submit. Worst case a lost keystroke runs BOTH windows (~2 × POLLS × S ≈ 20s
# of real sleep) inside ONE pane's iteration; `goal_lane_sweep`'s wall-clock
# deadline is checked only BETWEEN panes, so several wedged panes in one sweep
# trend toward the #172 timeout class (acceptable: a lost keystroke is rare and
# the happy path returns on the first poll).
SEND_VERIFY_POLLS = 10
SEND_VERIFY_S = 1
# Pre-Enter type-settle (borrowed from goal._await_typed's GOAL_TYPE_SETTLE_*):
# CC needs a moment to INGEST a multi-KB chunked paste before it renders it.
SEND_TYPE_SETTLE_POLLS = 8
SEND_TYPE_SETTLE_S = 1

# --------------------------------------------------------------------------- #
# #994 -- the global nudge KILL SWITCH. ONE predicate consulted at the TOP of
# each of the five keystroke helpers below (`send_verified`, `send_continue`,
# `send_subagent_nudge`, `submit_own_goal_verified`, `submit_own_draft_verified`)
# -- the ONLY places machine-composed text is typed into a Claude pane, so this
# is the single chokepoint and there is NO per-job check anywhere else. When the
# owner turns nudges OFF, the machine stops overriding the priority the owner
# just agreed with the session (the incident: a lane nudge announcing "priority
# = as many parallel subagents from I as possible"). Mirrors the EXISTING owner
# kill-switch `_owner_disabled` (#400): existence of the marker is the whole
# signal (fail-safe OFF -- a corrupt/partial marker still suppresses, never a
# silent re-enable), and `AIRULESET_TEST_IGNORE_DISABLE` is honored so a real
# box's OFF flag never fails the suite / the pre-push gate. The ONLY bypass is
# the owner's OWN Discord reply (the `user_authored` kwarg, set solely by
# `discord_replies`): the owner speaking is never a machine nudge.
# --------------------------------------------------------------------------- #
NUDGES_OFF_MARKER = "nudges-off"          # #994 legacy global marker (see below)
NUDGES_KINDS_STATE = "nudges-kinds.json"  # #1023 per-kind staging state file

# #1023 — the canonical set of MACHINE-NUDGE identities the owner stages one at a
# time. Distinct from the keystroke `kind` (a delivery SHAPE — GATED_KINDS below);
# a nudge identity is the SEMANTIC kind that a delivery call site threads via
# `nudge=` into `keys`, and it names the per-kind switch state key AND the
# `nudge_gate` cadence category. `nudges status` enumerates exactly this set.
MACHINE_NUDGE_KINDS = frozenset({
    # goal-family riders (into an armed /goal loop).
    # #1089 -- `lane-occupancy` DELIVERY is RETIRED: the lane-fill Stop gate
    # (gates/lanefill.py) is the refill lever now (it blocks the turn end WITHOUT
    # typing into the pane), so `goal_lane_occupancy_nudge` no longer calls any
    # keystroke-delivery primitive. The identity STAYS here as a recognized
    # observability/journal category (the "would-refill; DELIVERY RETIRED"
    # decision line + the nudge_gate cadence bucket) -- staging it ON can no
    # longer produce a keystroke. Do NOT touch the other kinds.
    "queue-arrival", "lane-occupancy", "release-gap", "lane-reconcile",
    "partition-audit", "u-freshness", "goal-guard",
    # goal auto-arm / dying-subagent stuck-check
    "goal-sweep", "subagent-stuck",
    # idle-pane backstops + report-owed card (jobs 8/11 + cards)
    "bounce", "gk-request", "card",
    # #1036 Job 49 — Odoo task-hygiene overseer nudge (per-kind staged, OFF by
    # default; NOT a nudge_gate GATED_CATEGORY — its cadence is decided by
    # gate_ok("task-hygiene") directly, like bounce/card/goal-sweep).
    "task-hygiene",
})

# #1023 addendum (owner, 2026-09-14) — RECOVERY revivals: identities that REVIVE a
# dead/blocked session, NEVER suppressed by the per-kind kill switch (default
# all-OFF) and NOT stageable/floored — the owner needs them to fire when a
# login/limit problem killed the session even though every PRIORITY nudge is off
# ("aj ked claudy to prepne na funkcny agent je nefunkcny lebo mu neprride nudge
# na ozivenie"). `resume` = the api-error/401-OAuth resume + the limit/usage-cap
# `continue` after reset (`__init__.py` Jobs 1/1b/6) AND the jobs 4/4a dying-
# session stuck-checks (`_send_stuckcheck_verified` default); `compact` = the
# `/compact` delivery (`compact.py`). They keep their OWN bounds — per-error
# dedup/attempt caps (Jobs 1/6), the decide_working `max_working_nudges` cadence
# (jobs 4/4a), compact's 30-min cooldown + #855 vetoes — plus the recent-human
# veto; NONE of them ever went through the nudge_gate 60-min per-kind floor, so
# dropping the switch gate removed no rate bound. PRIORITY nudges (every
# MACHINE_NUDGE_KINDS member) stay gated + floored + per-kind staged.
#
# #1038 (owner, 2026-09-15) — `goal-arm`: the arm keystroke into a DECLARED
# managed window (gk review, gk-infra, d3 today — any box that declares
# `windows` in cli_fleet). A declared
# window that comes back DARK after a reboot is a dead/blocked session the
# owner needs REVIVED with zero staging — semantically the same revival class
# as `resume`/`compact` — so its arm rides an always-on recovery nudge and is
# NEVER suppressed by the machine-nudge OFF switch. NON-declared boxes keep the
# staged PRIORITY `goal-sweep` identity (unchanged). Delivery derives which of
# the two identities to use from the pane's cwd (declared-window == source
# "role" in `cli_concurrency.resolve_concurrency`); see `watchdog/goal.py`
# `deliver_goal`. Its own recent-human + tri-state-armed + boundary + per-sid
# rate-floor gates bound it, exactly as the other recovery nudges keep theirs.
#
# #1063 (owner incident, 2026-09-17) — `goal-disarm`: the `/goal clear` disarm
# keystroke the #522 question-repoke backstop (Job 33) types into a `/goal` loop
# STUCK re-poking an unanswered `❓ NEEDS YOU`. It is a session RECOVERY / damage-
# control action, NOT a prompt interruption — it must fire even when every owner
# cadence control is off. Before #1063 it rode `_send_goal_verified`'s DEFAULT
# machine nudge `goal-sweep`, which the per-kind staging has OFF on every box, so
# the backstop was silently disabled fleet-wide and a re-poke storm ran 2h45m.
# As a recovery kind it is now exempt from the kill switch, the per-kind floor
# and the total cap; its OWN bounds stay (a proven 5-streak, the recent-human
# veto, and the 24h/2 attempt cap in `goal_question_repoke_watch`).
# #1084 (2026-09-19): machine-triggered `/compact` is REMOVED — its producer
# (`compact.deliver_compact` / `_compact_submit_verified`) is deleted. `compact`
# STAYS a reserved recovery identity here (a compaction nudge, if one ever
# returns, is a session revival, never a prompt) and keeps `MACHINE|RECOVERY`
# disjoint/union invariants + the drift-lock with nudge_gate stable; nothing
# emits `nudge="compact"` today.
RECOVERY_NUDGE_KINDS = frozenset(
    {"resume", "compact", "goal-arm", "wake-parked", "goal-disarm"})

# Every threaded nudge identity — the stageable PRIORITY set plus the always-on
# RECOVERY set. A `nudge=` threaded by any delivery site is one of these.
ALL_NUDGE_KINDS = MACHINE_NUDGE_KINDS | RECOVERY_NUDGE_KINDS


def nudges_marker_path(home=None):
    """Path to the legacy #994 global nudge kill-switch marker. Retained for
    back-compat reads only — the #1023 per-kind state file below is the live
    source of truth. Present == the pre-#1023 global OFF."""
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, ".claude", NUDGES_OFF_MARKER)


def read_nudges_marker(home=None):
    """The legacy #994 marker's advisory JSON when present, else None. Kept for
    back-compat; the live per-kind state is `read_nudges_kinds` below."""
    path = nudges_marker_path(home)
    if not os.path.exists(path):
        return None
    try:
        import json
        with open(path, encoding="utf-8") as h:
            data = json.load(h)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def nudges_kinds_path(home=None):
    """Path to the #1023 per-kind staging state file (`~/.claude/nudges-kinds.json`)."""
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, ".claude", NUDGES_KINDS_STATE)


def read_nudges_kinds(home=None):
    """The per-kind state dict `{"on": [<kind>...], "since", "by"}`, or a fresh
    all-OFF shape when the file is ABSENT (#1023: absent reads as every kind
    OFF). A present-but-corrupt file also reads as all-OFF (fail-safe: never a
    silent re-enable), never raises."""
    path = nudges_kinds_path(home)
    if not os.path.exists(path):
        return {"on": []}
    try:
        import json
        with open(path, encoding="utf-8") as h:
            data = json.load(h)
        if isinstance(data, dict) and isinstance(data.get("on"), list):
            return data
        return {"on": []}
    except (OSError, ValueError):
        return {"on": []}


def nudges_on_kinds(home=None):
    """The SET of currently-enabled machine-nudge kinds (intersected with
    MACHINE_NUDGE_KINDS so a stale/unknown key never counts). Absent state → the
    empty set (all OFF)."""
    on = read_nudges_kinds(home).get("on") or []
    return {k for k in on if k in MACHINE_NUDGE_KINDS}


def set_nudge_kind(kind, enabled, home=None, by=None):
    """Enable/disable ONE machine-nudge `kind` in the per-kind state file, then
    return the resulting on-set. Creates `~/.claude/` if missing; a no-op write is
    still idempotent. Unknown kinds are ignored (never persisted)."""
    if kind not in MACHINE_NUDGE_KINDS:
        return nudges_on_kinds(home)
    on = nudges_on_kinds(home)
    if enabled:
        on.add(kind)
    else:
        on.discard(kind)
    import datetime
    import json
    path = nudges_kinds_path(home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "on": sorted(on),
        "since": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "by": by or "",
    }
    with open(path, "w", encoding="utf-8") as h:
        json.dump(payload, h)
    return on


def nudges_enabled(kind=None, home=None):
    """True iff a machine nudge of `kind` may be delivered (#1023 per-kind
    staging). PRIORITY kinds (every MACHINE_NUDGE_KINDS member) default OFF (state
    file absent / a kind not enabled) — the owner enables them one at a time.
    RECOVERY kinds (RECOVERY_NUDGE_KINDS = resume/compact/goal-arm/wake-parked/goal-disarm) are
    ALWAYS-ON: they revive a dead/blocked session (a 401/limit revival, /compact),
    so the kill switch never suppresses them. `kind=None` (a gated keystroke fired
    with NO nudge identity — a programming error the AST contract test catches)
    fails safe to SUPPRESS (False): an un-threaded machine nudge is never
    delivered, so enabling ONE kind can never re-activate an unrelated un-threaded
    delivery (the #1023-review BLOCKER-2 leak). Honors `AIRULESET_TEST_IGNORE_DISABLE`
    exactly like the #994 predicate (and `_owner_disabled`, #400) so a real box's
    staged state never fails the suite / the pre-push gate."""
    if os.environ.get("AIRULESET_TEST_IGNORE_DISABLE"):
        return True
    if kind is None:
        return False             # fail-safe: no identity → suppress (never any-on)
    if kind in RECOVERY_NUDGE_KINDS:
        return True              # #1023 addendum: recovery revivals are always-on
    return kind in nudges_on_kinds(home)


def _suppress_nudge(kind, text, logs):
    """Journal ONE line for a nudge suppressed by the kill switch (an explicit
    #486-direction decision log). `kind` names the keystroke kind / nudge
    identity; the first 60 chars of `text` identify the specific suppressed
    nudge."""
    if isinstance(logs, list):
        logs.append("nudges OFF: suppressed %s %s" % (kind, (text or "")[:60]))


# --------------------------------------------------------------------------- #
# #1002 -- the ONE keystroke primitive. `keys` is the ONLY function in
# `watchdog/` that builds a `tmux send-keys` argv (control keys, the `-l --`
# literal-type form via `_type_literal`, and the `-H` hex form). The #994 kill
# switch -- and every future owner keystroke switch (a per-role mode #998, a
# `watchdog-disable-<kind>`) -- is evaluated ONCE here, via
# `_keystroke_suppressed(kind, user_authored)`, and the `nudges OFF: suppressed`
# journal line is written here and nowhere else. This retires the pre-#1002
# patchwork of 9 gate sites (each new owner switch needed N edits): a helper now
# fires its FIRST gated keystroke through `keys`, reads the return, and bails on
# False -- so a suppressed delivery types NOTHING and journals exactly one line.
#
# `kind` classifies the keystroke. GATED_KINDS are the machine-nudge DELIVERY
# kinds (a control key that is PART of such a delivery carries the delivery's
# kind, so the whole delivery gates as a unit). RECOVERY_KINDS are NOT machine
# nudges -- janitor cleanup, undo backspaces, the session-limit user-draft
# submit, the prompt-wedge submit of the OWNER's own wedged draft, and the
# resurrect relaunch (its own `AIRULESET_RESURRECT_ACTION` opt-in + recent-human
# veto is its gate, per its docstring) -- and they run regardless of the switch.
# A new machine-nudge kind = ONE line added to GATED_KINDS.
# --------------------------------------------------------------------------- #
GATED_KINDS = frozenset({"continue", "send", "goal", "draft", "stash", "type"})
RECOVERY_KINDS = frozenset({"janitor", "undo", "user-draft", "wedge", "resurrect"})


def _keystroke_suppressed(kind, user_authored, nudge=None):
    """True iff a keystroke of `kind` must be WITHHELD now -- the SINGLE gate
    (#1002). The owner's OWN reply (`user_authored`, granted solely by
    `discord_replies`) always passes. A RECOVERY_KINDS keystroke (not a machine
    nudge) always passes. Only a GATED (machine-nudge delivery) keystroke is
    withheld, and only when `nudges_enabled(nudge)` is False: a PRIORITY nudge the
    owner has NOT staged on (#1023 per-kind staging) — a RECOVERY nudge identity
    (RECOVERY_NUDGE_KINDS = resume/compact/goal-arm/wake-parked/goal-disarm) is always-on and never
    withheld. A gated keystroke with NO `nudge` identity (a programming error the
    contract test catches) FAILS SAFE to SUPPRESS (`nudges_enabled(None)` is
    False, BLOCKER-2) — never any-kind-on. Goes through `watchdog.nudges_enabled()`
    (the package facade) so the monkeypatch seam stays effective."""
    if user_authored or kind not in GATED_KINDS:
        return False
    return not watchdog.nudges_enabled(nudge)


def keys(pane_id, *keystrokes, kind, nudge=None, user_authored=False, run=None,
         logs=None, journal_text=None):
    """Send `keystrokes` to `pane_id` via `tmux send-keys` -- the ONE place in
    `watchdog/` that builds a send-keys argv (#1002). `keystrokes` are passed
    verbatim after `-t <pane_id>`: control keys (`"Enter"`, `"Escape"`, `"C-s"`,
    `"BSpace"`), the literal-type form (`"-l", "--", text`), or the hex form
    (`"-H", "1b", ...`). Returns True on a sent keystroke, False when the kill
    switch suppressed it (nothing sent, ONE journal line written).

    `kind` classifies the keystroke for the gate (see GATED_KINDS /
    RECOVERY_KINDS). `nudge` (#1023) is the machine-nudge IDENTITY (a member of
    ALL_NUDGE_KINDS — a stageable PRIORITY kind in MACHINE_NUDGE_KINDS or an
    always-on RECOVERY kind in RECOVERY_NUDGE_KINDS) the per-kind switch keys on
    -- every GATED machine-nudge delivery threads it from its call site (the
    contract test enforces this).
    `user_authored` (the owner's OWN Discord reply, forwarded from
    `discord_replies`) BYPASSES the gate. `journal_text` overrides the
    suppression-journal snippet (a literal-type caller passes its full text so
    the journal reads the payload, not `-l -- ...`); default derives a snippet
    from the non-flag keystrokes."""
    if watchdog._keystroke_suppressed(kind, user_authored, nudge):
        snippet = (journal_text if journal_text is not None
                   else " ".join(str(k) for k in keystrokes
                                 if not str(k).startswith("-")))
        watchdog._suppress_nudge(nudge or kind, snippet, logs)
        return False
    run = run or watchdog._default_run
    run(["tmux", "send-keys", "-t", pane_id, *keystrokes])
    return True


def relaunch_pane(pane_id, run=None, launcher="claude-continue"):
    """#1075 CREDENTIAL-DEAD recovery primitive: KILL the pane's current
    foreground process (a claude session stuck on a REVOKED OAuth token it can
    never re-read) and respawn the managed launcher in the SAME pane —
    `tmux respawn-pane -k -t <pane> <launcher>`. Returns True iff the respawn
    command was issued without raising.

    Distinct from the two existing relaunch shapes on purpose:
      * `keys` sends into a LIVE Claude Code input box (a `continue` nudge) — a
        stuck process would just 401 the nudge, which is the whole miva1 bug;
      * `resurrect.relaunch` types a launch command into a pane that ALREADY
        dropped to a BARE SHELL (the session exited) — here the process is still
        RUNNING and occupying the pane, so nothing can be typed; `respawn-pane
        -k` replaces it.

    `respawn-pane` is a tmux MANAGEMENT command, not a CC keystroke, so it is
    outside the `keys` per-kind kill-switch gate — it is a `resume`-class RECOVERY
    action, which #1023 keeps ALWAYS-ON precisely for a login/limit death like
    this (a nudges-OFF box must still come back from a dead credential). The
    CALLER owns every safety gate (the recent-human veto + the bare-shell /
    stopped-session check + a once-per-episode latch). Like `resurrect.relaunch`,
    the send is `delivered_unconfirmed`: the STRUCTURAL confirmation is the NEXT
    sweep (the session comes back live → its 401 episode clears). Never raises."""
    run = run or watchdog._default_run
    if not pane_id or not launcher:
        return False
    try:
        run(["tmux", "respawn-pane", "-k", "-t", str(pane_id), launcher])
        return True
    except Exception:
        return False


def impl_window_presence(marker, run=None, logs=None, dry_run=False):
    """#1060 L3b item 8 — keep the dual-agent IMPLEMENTER window alive.

    On a model-backend MARKER box the managed tmux session carries window 0
    ``<box>`` (the main/Fable session) and window 1 ``impl`` (the ``claude-impl``
    gateway session). The webterm-only boxes create that impl window from the
    ``-g session-created`` hook (`cli_tmux_provisioning._session_created_hook_
    value`) / the bashrc attach block (`cli_bashrc_appliers.render_tmux_attach_
    block`). But a session running since BEFORE the marker shipped — or whose
    impl window was closed / crashed out — has no impl window and no creation
    event to bring it back. This helper is the watchdog's CONVERGENCE path:
    once per sweep, on a marker box, it re-creates window 1 through the SAME
    ``tmux new-window … -n impl [-c <cwd>] <launcher>`` + ``remain-on-exit on``
    shape those two creators use — a tmux MANAGEMENT command, never a keystroke
    into a live pane (the `relaunch_pane`/`respawn-pane` precedent), marker-gated,
    and idempotent (DEDUP by window name — the same three-creator guard the
    attach block carries).

    ``marker`` is the box's model-backend marker dict, or None -> a no-op on a
    non-marker box (the caller passes ``cli_model_backend.load_marker()``).
    Returns exactly one of:
      * ``None``               — no marker (non-marker box; nothing logged);
      * ``"no-session"``       — no tmux server running;
      * ``"no-managed-session"``— a tmux server with NO box-named session (a
                                  window renamed to its own session name) to
                                  attach the window to — never guesses a foreign
                                  session;
      * ``"present"``          — the impl window already exists;
      * ``"relaunched"``       — it was missing and got created;
      * ``"would-relaunch"``   — missing, but ``dry_run`` so nothing was created.
    ``logs``, if a list, gets ONE decision line. Never raises."""
    if logs is None:
        logs = []
    if not marker:
        return None
    run = run or watchdog._default_run
    out = run(["tmux", "list-windows", "-a", "-F",
               "#{session_name}\t#{window_index}\t#{window_name}"]) or ""
    sessions = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        sess, name = parts[0].strip(), parts[2].strip()
        if sess:
            sessions.setdefault(sess, []).append(name)
    if not sessions:
        return "no-session"
    # The session-created hook renames window 0 to the box name (== session
    # name), so ``window_name == session_name`` identifies the managed session
    # (its box-renamed primary window). On a single-session marker box (phase 1:
    # miva1) this is unambiguous; for grouped siblings (which SHARE windows) it
    # resolves to the base session, never a double-create. It picks the FIRST
    # match, so a USER-created session whose window happens to be renamed to its
    # own name could in principle be chosen — harmless on the single-session
    # marker box this runs on today.
    target = None
    for sess, names in sessions.items():
        if sess in names:
            target = sess
            break
    if target is None:
        return "no-managed-session"
    if "impl" in sessions[target]:
        logs.append("dual: impl window present on %s" % target)
        return "present"
    if dry_run:
        logs.append("dual: impl window missing on %s — would relaunch (dry-run)"
                    % target)
        return "would-relaunch"
    # The SAME launcher script the attach block / session-created hook run
    # (a script path, not the interactive `claude-impl` shell function).
    try:
        from cli_claude_scripts import CLAUDE_IMPL_LAUNCH_SCRIPT_DEST
        launcher = os.path.expanduser("~/.claude/%s"
                                      % CLAUDE_IMPL_LAUNCH_SCRIPT_DEST.name)
    except Exception:
        launcher = os.path.expanduser("~/.claude/airuleset-claude-impl.sh")
    argv = ["tmux", "new-window", "-d", "-t", target, "-n", "impl"]
    cwd = (marker.get("cwd") or "").strip() if isinstance(marker, dict) else ""
    if cwd:
        ecwd = os.path.expanduser(cwd)
        if os.path.isdir(ecwd):
            argv += ["-c", ecwd]
    argv.append(launcher)
    run(argv)
    # Keep the pane visible if `claude-impl` REFUSES (exit 1 on a misprovisioned
    # key/cwd) so its LOUD stderr is readable — matching the attach block.
    run(["tmux", "set-window-option", "-t", "%s:impl" % target,
         "remain-on-exit", "on"])
    logs.append("dual: impl window missing — relaunched on %s" % target)
    return "relaunched"


def _default_run(argv, timeout=8):
    # #1055 P2 -- the watchdog's default subprocess runner (tmux calls, plus any
    # caller that threads `run=_default_run`). Time + record every invocation in
    # the per-sweep counter (label = command basename, e.g. `tmux`); a memoized
    # caller that short-circuits BEFORE reaching here never records, so the
    # counter reflects the real per-sweep subprocess spend.
    import subprocess
    import time
    from watchdog.subprocess_budget import record_subprocess
    t0 = time.monotonic()
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""
    finally:
        try:
            _lbl = os.path.basename(str(argv[0])) if argv else "?"
        except Exception:
            _lbl = "?"
        record_subprocess(_lbl, time.monotonic() - t0)


def _proc_read(path):
    try:
        with open(path) as h:
            return h.read()
    except OSError:
        return ""                # process exited mid-walk — expected race


def _pane_hosted_claude_pid(pane_pid):
    """PID of a `claude` process inside the pane's process TREE, or None — a
    sudo-hosted stream session (`sudo su - montalu` → bash → claude) reports
    pane_current_command='sudo', which hid the montalu pane from every
    watchdog job (2026-07-20: /goal auto-arm structurally impossible there).
    Pure /proc walk, fail-safe None."""
    try:
        children = {}
        for p in os.listdir("/proc"):
            if not p.isdigit():
                continue
            stat = _proc_read("/proc/%s/stat" % p)
            if not stat:
                continue
            ppid = stat.rsplit(") ", 1)[-1].split()[1]
            children.setdefault(ppid, []).append(p)
        frontier = [str(int(pane_pid))]
        while frontier:
            cur = frontier.pop()
            for ch in children.get(cur, []):
                if "claude" in _proc_read("/proc/%s/comm" % ch):
                    return ch
                frontier.append(ch)
    except Exception:
        return None              # fail-safe: an unreadable /proc = not hosted
    return None


def _hosted_claude_cwd(claude_pid, pane_cwd):
    """The hosted claude process's REAL cwd — tmux reports the SUDO root's cwd
    (where the human ran `sudo su`, e.g. /home/newlevel/devel/odoo), which
    mis-binds every cwd-keyed lookup. Direct readlink works only same-user;
    a foreign process needs `sudo -n -u <owner>`. Falls back to the pane cwd."""
    import subprocess
    link = "/proc/%s/cwd" % claude_pid
    try:
        return os.readlink(link)
    except OSError:
        status = _proc_read("/proc/%s/status" % claude_pid)
        m2 = re.search(r"^Uid:\s+(\d+)", status, re.M)
        if m2:
            try:
                import pwd
                user = pwd.getpwuid(int(m2.group(1))).pw_name
                p = subprocess.run(["sudo", "-n", "-u", user, "readlink", link],
                                   capture_output=True, text=True, timeout=5)
                out = (p.stdout or "").strip()
                if p.returncode == 0 and out.startswith("/"):
                    return out
            except Exception:
                return pane_cwd  # no passwordless sudo → best effort
    return pane_cwd


def _proc_start_epoch(pid):
    """Epoch seconds when process `pid` STARTED — `/proc/<pid>/stat` field 22
    (`starttime`, clock ticks since boot) + `/proc/stat` `btime`. None on any
    read/parse failure. This is the per-PANE session discriminator (#645): the
    claude process's start aligns with a RESUME BOUNDARY in the session's
    transcript, the only signal that holds (fd/env/cmdline carry no sid)."""
    stat = _proc_read("/proc/%s/stat" % pid)
    if not stat:
        return None
    # comm can contain spaces/parens, so split AFTER the last ") " — field 3
    # (state) is then index 0, field 22 (starttime) index 19.
    try:
        after = stat.rsplit(") ", 1)[-1].split()
        ticks = int(after[19])
    except (IndexError, ValueError):
        return None
    btime = None
    for line in _proc_read("/proc/stat").splitlines():
        if line.startswith("btime "):
            try:
                btime = int(line.split()[1])
            except (IndexError, ValueError):
                return None
            break
    if btime is None:
        return None
    try:
        hz = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        return None
    if not hz:
        return None
    return btime + ticks / hz


def _pane_claude_pid(pane_pid):
    """The `claude` PID for a pane: `pane_pid` itself when it IS claude (an
    `exec claude` launch), else the claude descendant in its process tree (the
    normal shell-forks-claude launch — reuses `_pane_hosted_claude_pid`). None if
    neither. Fail-safe None on a bad pane_pid."""
    p = str(pane_pid).strip()
    if not p.isdigit():
        return None
    if "claude" in _proc_read("/proc/%s/comm" % p):
        return p
    return watchdog._pane_hosted_claude_pid(p)


def _pane_claude_start_epoch(pane_id, run=None):
    """Start-epoch of the claude process hosting tmux pane `pane_id`:
    `#{pane_pid}` → `_pane_claude_pid` → `_proc_start_epoch`. None when
    unresolved (fail-safe: the #645 disambiguation then safe-skips this pane)."""
    run = run or watchdog._default_run
    ppid = (run(["tmux", "display-message", "-p", "-t", pane_id,
                 "#{pane_pid}"]) or "").strip()
    if not ppid.isdigit():
        return None
    cpid = watchdog._pane_claude_pid(ppid)
    if not cpid:
        return None
    return watchdog._proc_start_epoch(cpid)


def _tmux_default_socket_path():
    """The tmux server's DEFAULT control socket path -- `$TMUX_TMPDIR` (or
    `/tmp` when unset) + `tmux-<uid>/default` -- what every managed session
    (the one hosting a `claude` pane) uses. A project MAY run OTHER tmux
    servers on `-L`/`-S` sockets alongside it (this repo's own scripts/tests
    do, and dev2 runs a real `-L t2` server right now) -- those are simply a
    DIFFERENT server this function has no opinion about; the recovery this
    module performs only ever targets the DEFAULT socket, since that is the
    one a managed Claude Code session's tmux actually binds to."""
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return os.path.join(base, "tmux-%d" % os.getuid(), "default")


def _tmux_socket_missing(path=None):
    """True when the tmux control socket the server should be listening on
    is absent from disk -- the orphaned-server shape (#318): a `tmpfiles.d`
    age-based reap (or any other removal) of `/tmp/tmux-*` deletes the
    socket FILE while the server PROCESS keeps running, so every NEW client
    connection to it fails from then on, although the session itself is
    alive. `path` is overridable for tests; production always resolves the
    real default socket path."""
    return not os.path.exists(path or _tmux_default_socket_path())


def _tmux_server_pids(run=None):
    """PIDs of every live `tmux: server` process OWNED BY THIS UID, found
    via `ps` (never the tmux socket itself -- the whole point is this must
    still resolve even when the socket is unreachable). Adversarial review
    of #318 measured live: `ps -e` also lists OTHER users' tmux servers on
    a shared box (subdev's montalu/marek/david), and a box can genuinely
    run MORE THAN ONE server for the SAME uid (dev2 right now: a default
    socket AND a `-L t2` one) -- picking just the first candidate can
    signal the WRONG server (a foreign uid's SIGUSR1 attempt just EPERMs
    silently; a same-uid `-L` server's own SIGUSR1 only ever recreates ITS
    OWN socket, never the default one, confirmed live). So this returns
    EVERY same-uid candidate, in `ps`'s own order, for the caller to try
    each in turn until the DEFAULT socket actually comes back. Empty when
    no such process is running at all (the ordinary "tmux genuinely isn't
    up" case -- nothing to recover, behavior unchanged from before #318).
    Injectable via `run` like every other tmux shim in this module."""
    run = run or watchdog._default_run
    out = run(["ps", "-eo", "pid,uid,comm"])
    my_uid = os.getuid()
    pids = []
    for line in (out or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or parts[2].strip() != "tmux: server":
            continue
        try:
            pid, uid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if uid == my_uid:
            pids.append(pid)
    return pids


def _tmux_socket_recover(pid, run=None):
    """SIGUSR1 to a tmux server whose socket was removed out from under it
    re-creates the socket at its configured path (tmux(1) SIGNALS: "If the
    socket is accidentally removed, the SIGUSR1 signal may be sent to the
    tmux server process to recreate it") -- the SAME recovery the #318
    incident applied by hand (`kill -USR1 <server-pid>`). Only ever called
    after `_tmux_socket_missing()` has already confirmed the DEFAULT socket
    is genuinely gone, so this never signals a healthy default-socket
    server -- it CAN still be sent to the wrong same-uid candidate (a `-L`
    server) when several exist, which is why `list_claude_panes` retries
    the real query after EACH candidate rather than trusting the first."""
    run = run or watchdog._default_run
    run(["kill", "-USR1", str(pid)])


# #1055 P2 -- the 4-field SUPERSET `list-panes` format. `list_claude_panes`
# needs pane_pid (the 4th field, for the sudo/su-hosted stream shape);
# `_reconcile_candidate_panes` reads only the first three -- so ONE query in this
# format feeds BOTH readers, shared per sweep via the memo below.
_PANE_INVENTORY_QUERY = [
    "tmux", "list-panes", "-a", "-F",
    "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_pid}",
]


def _pane_inventory_raw(run=None, logs=None, dry_run=False):
    """Raw `tmux list-panes -a` output (the 4-field superset format), SHARED
    across a sweep via the per-sweep memo so `list_claude_panes` and
    `_reconcile_candidate_panes` issue ONE query between them (#1055 P2). Carries
    `list_claude_panes`'s socket-orphan recovery (#318) verbatim, so a cached
    inventory is a RECOVERED one whichever reader runs first. OUTSIDE a sweep
    (memo inactive) every call runs its own query + recovery -- behaviour is
    byte-identical to the pre-#1055 inline read for every direct caller.

    ORDERING INVARIANT (adversarial-review F4): the recovery's real SIGUSR1 fires
    ONLY on the FIRST computing caller (a memo MISS). In run_once,
    `list_claude_panes(run, dry_run=dry_run)` runs in the top pane loop BEFORE
    any job-20 rider, so it ALWAYS seeds the memo (threading dry_run correctly)
    and `_reconcile_candidate_panes` — which passes `dry_run=False` by default —
    only ever hits the cache, never triggering a live SIGUSR1 in a `--dry-run`
    sweep. If a future caller could compute the inventory FIRST during a dry-run
    sweep, thread `dry_run` to it (the param is here for that)."""
    from watchdog.subprocess_budget import memoized
    run = run or watchdog._default_run

    def _compute():
        out = run(_PANE_INVENTORY_QUERY)
        if not (out or "").strip() and _tmux_socket_missing():
            # `_tmux_socket_missing()` is a cheap stat -- checked BEFORE the
            # `ps -e` process-table scan below (MINOR-1, #318 review) so a box
            # whose socket is intact never pays that cost, even when
            # list-panes came back empty for some other, unrelated reason.
            pids = _tmux_server_pids(run)
            if pids and dry_run:
                if logs is not None:
                    logs.append("tmux-socket-orphaned server-pid=%d -- "
                                "would recover via SIGUSR1 (dry-run)" % pids[0])
            elif pids:
                recovered = False
                for pid in pids:
                    if logs is not None:
                        logs.append("tmux-socket-orphaned server-pid=%d -- "
                                    "recovering via SIGUSR1" % pid)
                    _tmux_socket_recover(pid, run)
                    out = run(_PANE_INVENTORY_QUERY)
                    if (out or "").strip():
                        if logs is not None:
                            logs.append("tmux-socket-recovered")
                        recovered = True
                        break
                if not recovered and logs is not None:
                    logs.append("tmux-socket-recovery-failed server-pids=%s"
                                % ",".join(str(p) for p in pids))
        return out or ""

    return memoized(("panes_raw",), _compute)


def list_claude_panes(run=None, logs=None, dry_run=False):
    """[(pane_id, cwd)] for every tmux pane running `claude` — directly, or
    hosted under sudo/su (the montalu-in-newlevel-tmux stream shape) — deduped
    by pane_id (grouped sessions share the same pane_id).

    Self-heals the orphaned-tmux-server shape (#318): `tmux list-panes -a`
    returning EMPTY is structurally ambiguous on its own -- a live server
    always hosts >=1 pane, so empty means EITHER genuinely no tmux server is
    running, OR the server is alive but its socket FILE was reaped out from
    under it (the live incident: subdev's `/tmp/tmux-1000/` was recreated by
    a tmpfiles-clean-shaped age-based sweep while both the tmux server and
    david's claude session kept running -- every watchdog job funnels
    through THIS function, so recovering here fixes job 8's false "no
    session" bounce ping and every other pane-reading job at once, instead
    of teaching each one to special-case it). `logs`, if a list, gets one
    line describing the recovery attempt and its outcome -- best-effort,
    callers that don't care about it (nearly all of them) just omit it.
    `dry_run=True` (adversarial-review finding) logs what WOULD be tried
    but never sends the real SIGUSR1, so a `watchdog --once --dry-run` stays
    genuinely side-effect-free through every caller that threads it here.

    #1055 P2: the raw `tmux list-panes -a` read (with the socket-orphan
    recovery) is delegated to `_pane_inventory_raw`, which memoizes it per
    sweep so this reader and `_reconcile_candidate_panes` share ONE query."""
    out = _pane_inventory_raw(run, logs=logs, dry_run=dry_run)
    seen, res = set(), []
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid, cmd, cwd = parts[0].strip(), parts[1].strip(), parts[2].strip()
        ppid = parts[3].strip() if len(parts) > 3 else ""
        if not pid or pid in seen:
            continue
        if cmd != "claude":
            if cmd not in ("sudo", "su") or not ppid:
                continue
            cpid = watchdog._pane_hosted_claude_pid(ppid)
            if not cpid:
                continue
            cwd = watchdog._hosted_claude_cwd(cpid, cwd)
        seen.add(pid)
        res.append((pid, cwd))
    return res


def pane_in_mode(pane_id, run=None):
    """True if the pane is in tmux copy-mode / a modal (the user is scrolling, or a
    menu is open). Sending keys then would be swallowed or would corrupt the user's
    selection — so the watchdog skips such a pane this cycle (without burning a
    retry)."""
    run = run or watchdog._default_run
    out = run(["tmux", "display-message", "-p", "-t", pane_id, "#{pane_in_mode}"])
    return (out or "").strip() == "1"


def capture_pane(pane_id, run=None, lines=40):
    """Last `lines` of the pane's visible content. Used ONLY for the ping-only
    waiting-on-user detector — never for the api-error action trigger (that is
    flag-only, after the pane-text-fallback incident).

    #1055 P2: deliberately NOT memoized. Every same-pane recapture in the
    codebase is an intentional FRESH read -- the top-of-sweep baseline is
    re-verified before every keystroke send against a fresh capture (#176-F3),
    the goal riders poll a render-settling box (`_await_typed`/`_await_goal_
    armed`, #720), and parked-wake re-reads to detect a same-sweep resume;
    memoizing the CONTENT would defeat those race/liveness checks (the #233
    keystroke-into-a-moved-pane scar). Only the pane's OWNER (`pane_owner`,
    which cannot change mid-sweep) and the pane-list inventory are memoized."""
    run = run or watchdog._default_run
    return run(["tmux", "capture-pane", "-p", "-t", pane_id, "-S", "-%d" % lines])


def pane_owner(pane_id, run=None):
    """Lowercase tmux owner (zbynek / marek) of a SPECIFIC pane, so a ping about
    that pane @mentions the right person — the watchdog runs headless (systemd
    --user) with NO tmux context of its own, so it must resolve the owner from the
    waiting/stalled pane, not from itself. Matches notify.resolve_owner's
    normalization ('marek-12' → 'marek').

    #1055 P2: memoized per pane within a sweep -- the owner of a pane cannot
    change mid-sweep, so the main loop and the goal riders share ONE
    `display-message` per pane."""
    from watchdog.subprocess_budget import memoized
    run = run or watchdog._default_run

    def _compute():
        for fmt in ("#{session_group}", "#S"):
            out = (run(["tmux", "display-message", "-p", "-t", pane_id, fmt]) or "").strip()
            if out:
                stripped = re.sub(r"-\d+$", "", out)
                return re.sub(r"[^a-z0-9]", "", stripped.lower())
        return ""

    return memoized(("owner", pane_id), _compute)


def _strip_selected(captured):
    """True if the agent-strip SELECTOR holds focus — any line renders as a
    selected strip row (`❯ ● main` / `❯ ◯ <agent>`, issue #36). While
    selected, Enter navigates ("view agent") instead of submitting the input
    box — a bare Enter typed there is silently swallowed. Scans every line
    (not just the boundary) since the selection can sit below other chrome.
    Fail-safe direction: a false positive costs one harmless extra Escape,
    never a lost draft."""
    if not captured:
        return False
    for ln in captured.splitlines():
        s = ln.strip()
        if s.startswith("❯ ●") or s.startswith("❯ ◯"):
            return True
    return False


def send_continue(pane_id, text=NUDGE_TEXT, run=None, logs=None, nudge=None,
                  state=None):
    """Type `text` literally into the pane, then press Enter to submit it.

    #994 suppression contract: when a GATED nudge kind is OFF the chokepoint types
    NOTHING, journals one line, and returns False (the caller reads the return so
    the request stays pending, never booked delivered). Returns True on an
    attempted send (this helper never post-verifies). NOTE (#1023 addendum;
    updated #1084): its former sole production caller was the callback-compact
    submit path (`nudge="compact"`, a RECOVERY kind, ALWAYS-ON) — deleted with the
    #1084 machine-compact removal — so the False/suppressed path is unexercised in
    production today and only reachable for a hypothetical future GATED caller; the
    logic stays general and correct for that case.

    Captures the pane FIRST (issue #36): if the agent-strip selector holds
    focus (`_strip_selected`), send ONE Escape before typing — otherwise the
    submit Enter can be swallowed as "view agent" instead of submitting our
    text. Best-effort only: we do NOT re-verify the Escape actually cleared
    the selection — proceed with the type + Enter regardless (today's
    behavior), since the retry paths (job 7's verify loop, job 10's machine
    submit) already Escape-and-retry on a swallowed submit. NEVER send a
    second Escape here — a rapid double-Escape into a pane holding a draft
    PERMANENTLY DELETES it (empirically confirmed, issue #35)."""
    run = run or watchdog._default_run
    captured = watchdog.capture_pane(pane_id, run, lines=10)
    # #1002 -- every keystroke goes through the ONE `keys`/`_type_literal`
    # primitive, so the #994 kill switch is evaluated there, not here. The
    # strip-deselect Escape carries the delivery's "continue" kind: at OFF `keys`
    # suppresses it (ZERO stray keystrokes into the owner's pane) and returns
    # False, and this helper bails -- exactly one journal line, no type, no
    # Enter. `_type_literal` carries the `--` end-of-options `-` safety
    # (#322/#372) and the same kind, so at OFF it types NOTHING and returns False
    # -- a GATED caller then leaves its request PENDING (`nudges-off`), never
    # booked delivered (the former callback-compact caller was deleted in #1084).
    if _strip_selected(captured):
        if not watchdog.keys(pane_id, "Escape", kind="continue", nudge=nudge,
                             run=run, logs=logs):
            return False
    if not watchdog._type_literal(pane_id, run, text, kind="continue", nudge=nudge,
                                  logs=logs, state=state):
        return False
    watchdog.keys(pane_id, "Enter", kind="continue", nudge=nudge, run=run, logs=logs)
    return True


def _subagent_nudge_signature(worker_id):
    """(#491) The stable, per-worker substring that identifies our dead-worker
    stuck-check nudge in a supervisor transcript `user` turn. Built once, used
    BOTH to compose the nudge (`send_subagent_nudge`) and to recognize it as a
    LANDED nudge (`supervisor_responded_to_nudge`), so the two can never drift
    on what text to match. `wid` is the dispatched agent's own transcript stem
    — unique per dispatch — so the signature never collides across workers."""
    return "background worker %s vyzerá mŕtvy" % worker_id


def send_subagent_nudge(pane_id, worker_id, kind, run=None, tpath=None,
                        sleep_fn=None, logs=None, nudge="subagent-stuck",
                        state=None):
    """(issue #6) Nudge the SUPERVISOR pane about a dying BACKGROUND WORKER —
    `kind` is a short human label ('api-error' or 'text-toolcall-stall'). Types a
    stuck-check-style self-check message naming the worker's own transcript file,
    so the supervisor — the only thing that can decide resume vs re-dispatch —
    investigates. Never acts on the worker's behalf directly. Returns True on a
    delivered/verified submit, False on a swallowed one.

    #497 batch 3: the text embeds the worker-id (a 36-char UUID on real boxes)
    TWICE, so it runs 238-248c → CHUNK-typed. When a `tpath` is supplied it is
    the SUPERVISOR's transcript — the pane typed into, NEVER the dying worker's
    `sub_path` — so route through the transcript-proof `send_verified` and verify
    the submit landed. The `_nudge_dying_subagent` caller marks/clears the #372
    janitor provenance around this call, so a swallowed chunk-typed residue is
    reclaimable via the shared `"stuck-check: "` own-payload prefix. Without a
    tpath there is NO transcript to prove the submit landed, so #806 REFUSES
    (returns False) rather than the old raw-`send_continue` book-as-delivered:
    an unverifiable send left a swallowed stuck-check stranded in the composer.
    A refused send is retried next sweep once a transcript is resolvable."""
    # #994 REOPEN -- no own gate: this helper delegates to `send_verified`, whose
    # `_type_literal_verified` primitive carries the kill switch. At OFF that
    # returns False and this helper returns False (its swallowed-nudge shape).
    text = ("stuck-check: %s (%s v subagents/%s.jsonl) "
            "— over jeho transcript a zasiahni (dispatchni znova alebo naň nadviaž), "
            "nič nerob naslepo." % (_subagent_nudge_signature(worker_id), kind, worker_id))
    if tpath is not None:
        return watchdog.send_verified(pane_id, text, run, tpath,
                                      sleep_fn=sleep_fn, logs=logs, nudge=nudge,
                                      state=state)  # #1022: record for the wedge
    # #806 -- no transcript = unverifiable delivery; never a raw unverified type.
    # The old tpath-less `send_continue` fallback returned True unconditionally,
    # so a swallowed Enter left the stuck-check stranded in the composer while
    # the caller believed it delivered (the mode-6 class). Refuse instead (return
    # False, exactly like a genuine swallow). BOTH production call sites derive
    # the supervisor tpath before calling and so never reach this branch (it is a
    # defensive floor, not a live path); the sole caller (`_nudge_dying_subagent`)
    # treats a False like a swallowed nudge -- it logs "(submit-unverified)" and
    # decide_working re-tries on its own cadence -- so no nudge is ever booked as
    # delivered. (A persistently tpath-less pane, which cannot arise today, would
    # escalate through decide_working's normal give-up like any un-landed nudge.)
    if isinstance(logs, list):
        logs.append("send-subagent-nudge refuse: no transcript path (unverifiable)")
    return False


def _subagent_transcript_unsalvageable(sub_path):
    """(#287) True when a dying SUBAGENT's OWN transcript has genuinely NOTHING
    left to investigate: no `tool_use` it ever issued actually COMPLETED
    (returned a `tool_result`), and its last real entry is a bare
    `isApiErrorMessage`. Matches the reporting incident's OWN stated bar
    verbatim (odoo-erp#3036: "4 lines total... 1 tool_use — the dispatch
    itself, **0 completed tool calls**") — not "zero tool_use ever ISSUED",
    which is stricter than what the incident actually reports and would
    wrongly classify its own worker (1 issued-but-never-returned tool_use)
    as salvageable (adversarial-review finding, #287). An issued tool_use
    with no observed tool_result is exactly as un-investigable as no
    tool_use at all — the supervisor has no evidence it ever produced
    anything, so a session nudged about either shape can only ever
    re-derive the SAME "nothing to salvage" conclusion. `_nudge_dying_
    subagent` nudges such a worker AT MOST ONCE rather than the full
    nudge/nudge/nudge/escalate cycle a genuinely recoverable stall earns.

    Fails SAFE toward "salvageable" (False) on any read problem or on
    finding even ONE returned `tool_result` — under-classifying only costs
    a few extra (harmless, now BOUNDED by SUBAGENT_NUDGE_STATE_TTL_SECONDS)
    nudges, never a silently-skipped genuinely-recoverable worker. The scan
    is bounded to the last `max_lines` entries — a real completed tool_use
    sitting further back than that (an unusually long-lived worker that
    only went quiet for its own final stretch) could still be missed and
    the worker over-classified as unsalvageable; the harm stays bounded to
    one nudge instead of three, never a silently-dropped one."""
    if not watchdog.transcript_last_error(sub_path):
        return False                     # doesn't even end on an api-error
    for entry in watchdog._iter_jsonl_tail(sub_path, max_lines=500):
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        msg = entry.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False                 # a tool call actually RETURNED -> real progress
    return True


def _nudge_dying_subagent(state, logs, send_fn, pid, run, captured, project, owner,
                          now, sub_path, sub_idle, kind, dedup_prefix,
                          interval, max_nudges, dry_run, tpath=None, sleep_fn=None):
    """(issue #6) Shared busy/idle nudge-or-ping logic for a detected dying SUBAGENT
    (jobs 1b / 4a-sub). `kind` is a human label for the nudge/ping text; `dedup_prefix`
    namespaces the state/dedup keys per detector ('apierr' / 'textcall'). Mutates
    `state` and `logs` in place. Same keystroke discipline as every other job: NEVER
    type into a copy-mode or busy (no free `❯`) pane — ping instead, mirroring job 4's
    busy-pane-wedged path — and reuse decide_working's nudge → retry → escalate
    lifecycle for the idle-pane case, so a wedged supervisor still only pings once.

    (#287) When the worker's own transcript is PROVABLY unsalvageable
    (`_subagent_transcript_unsalvageable`), `max_nudges` is capped at 1 —
    decide_working then delivers exactly ONE typed nudge (the thing that
    actually costs the session a paid turn) and escalates to a single
    passive Discord ping on its next evaluation, before going permanently
    silent — never the full multi-nudge cycle for a transcript with nothing
    left to learn from a second look."""
    wid = sub_path.stem
    if watchdog.pane_in_mode(pid, run):
        logs.append("skip in-mode (subagent-%s) %s" % (dedup_prefix, project or pid))
        return
    # (#491) ACKNOWLEDGEMENT RESOLUTION — checked BEFORE the busy/idle branches
    # so a resolved worker is PERMANENTLY silent on BOTH paths (no keystroke,
    # no busy-pane ping). A dead worker never recovers, so the only useful
    # thing a nudge does is tell the SUPERVISOR its worker died. Once the
    # supervisor has demonstrably ACKNOWLEDGED a nudge about THIS worker — its
    # own transcript shows our nudge as a LANDED `user` turn FOLLOWED BY a
    # genuine assistant response (`supervisor_responded_to_nudge`) — it has
    # SEEN the death and owns the resume-vs-redispatch call. Re-nudging from
    # here delivers zero new information and costs a full paid turn each time
    # (the reporting incident's 244k-token resurrect-to-silence workaround,
    # #491, which the salvageable full-nudge cycle earns even AFTER #287
    # bounded the never-recover case). Mark the worker RESOLVED and go silent;
    # the state's own SUBAGENT_NUDGE_STATE_TTL_SECONDS cleanup drops it once
    # the file ages past the trigger window. Purely durable-state driven (the
    # supervisor's own transcript) — no manual ack, no supervisor-transcript
    # edit. Retry-if-unseen is preserved: a swallowed nudge writes no `user`
    # turn → no causal ack → the decide_working cycle below still fires
    # nudge#2/#3 then escalates to the human. The causal (landed-`user`-turn)
    # signal — not a mere timestamp-after — is what makes that hold in the
    # incident's own `/goal` loop, where the supervisor emits genuine
    # post-nudge turns independently of ever seeing the nudge (adversarial
    # review #491: a timestamp-only signal falsely resolved a swallowed nudge
    # on the loop's next re-fire).
    wkey = "subagent-%s:%s" % (dedup_prefix, wid)
    prev = state.get(wkey) or {}
    resolved = bool(prev.get("resolved"))
    if not resolved and (prev.get("nudges") or []):
        sup_tpath = watchdog.supervisor_transcript_for_subagent(sub_path)
        if sup_tpath is not None:
            resolved = watchdog.supervisor_responded_to_nudge(
                sup_tpath, _subagent_nudge_signature(wid))
    if resolved:
        prev["resolved"] = True
        prev["last_seen"] = int(now)       # keep sticky across the TTL window
        state[wkey] = prev
        logs.append("subagent-%s-resolved %s [%s] — supervisor acknowledged"
                    % (dedup_prefix, project, wid))
        return
    if not watchdog.pane_at_idle_prompt(captured):
        bkey = "subagent-busypane:%s:%s" % (dedup_prefix, wid)
        b = state.get(bkey) or {"first_seen": int(now - sub_idle), "pinged": False}
        b["last_seen"] = int(now)
        state[bkey] = b
        if not b["pinged"]:
            b["pinged"] = True
            logs.append("subagent-%s-busy %s [%s] — ping only" % (dedup_prefix, project, wid))
            send_fn("\U0001f6d1 **%s** — background worker `%s` (%s), ale hlavná session "
                    "je zaneprázdnená\n> Nezasahujem klávesami (rozbilo by to bežiacu "
                    "prácu) — over `subagents/%s.jsonl`." % (project, wid, kind, wid),
                    owner=owner, dedup_key="subagent-%s-busy:%s" % (dedup_prefix, wid),
                    dry_run=dry_run)
        else:
            logs.append("skip busy-pane (subagent-%s) %s [%s]" % (dedup_prefix, project, wid))
        return
    # (#287) A worker whose own transcript is provably unsalvageable earns
    # AT MOST ONE typed nudge, never the full retry cycle — see
    # _subagent_transcript_unsalvageable's own docstring. (`wkey` and the
    # resolution short-circuit are handled at the top, before the busy/idle
    # branch, so a resolved worker never reaches here.)
    unsalvageable = _subagent_transcript_unsalvageable(sub_path)
    effective_max_nudges = 1 if unsalvageable else max_nudges
    action, entry = watchdog.decide_working(state, wkey, now, sub_idle,
                                   interval=interval, max_nudges=effective_max_nudges)
    state[wkey] = entry
    if action == "nudge":
        n = len(entry["nudges"])
        # #497 batch 3 — transcript-proof send (238-248c CHUNK) against the
        # SUPERVISOR's own `tpath` (the pane typed into — threaded from the
        # run_once caller, NEVER `sub_path`, the dying worker's transcript), plus
        # the #372 janitor mark BEFORE the keystroke so a swallowed chunk-typed
        # residue is reclaimable via the shared "stuck-check: " own-payload
        # prefix; cleared on a verified submit. decide_working's own cadence
        # retries a swallowed nudge, so no persist reorder — log honestly.
        ok = True
        if not dry_run:
            watchdog._janitor_mark_watch(state, pid, now)
            ok = send_subagent_nudge(pid, wid, kind, run, tpath, sleep_fn, logs,
                                     state=state)  # #1022: record for the wedge
            if ok:
                watchdog._janitor_clear_watch(state, pid)
        logs.append("subagent-%s-nudge#%d %s [%s]%s"
                    % (dedup_prefix, n, project, wid,
                       "" if ok else " (submit-unverified)"))
    elif action == "escalate":
        logs.append("subagent-%s-escalate %s [%s] — gave up after %d nudges"
                    % (dedup_prefix, project, wid, effective_max_nudges))
        # (#287 adversarial-review MINOR) The unsalvageable path escalates
        # after exactly ONE nudge, often within the very next sweep — "session
        # nereaguje na nudge" (not responding) is the WRONG claim there (no
        # time to respond, and often nothing TO respond to); it stays correct
        # only for the genuine multi-nudge no-response cycle.
        if unsalvageable:
            send_fn("\U0001f6d1 **%s** — background worker `%s` (%s) transcript nemá čo "
                    "ponúknuť na skúmanie (0 dokončených tool calls pred chybou)\n> "
                    "Ďalšie skúmanie by neprinieslo nič nové — ak treba, over "
                    "`subagents/%s.jsonl` ručne." % (project, wid, kind, wid),
                    owner=owner, dedup_key="subagent-%s-giveup:%s" % (dedup_prefix, wid),
                    dry_run=dry_run)
        else:
            send_fn("\U0001f6d1 **%s** — background worker `%s` (%s) a session nereaguje na "
                    "nudge\n> Treba zásah." % (project, wid, kind),
                    owner=owner, dedup_key="subagent-%s-giveup:%s" % (dedup_prefix, wid),
                    dry_run=dry_run)
    else:
        logs.append("subagent-%s-%s %s [%s]" % (dedup_prefix, action, project, wid))


def _await_submit_confirmed(tpath, baseline, text, sleep_fn):
    """Poll (bounded, ~SEND_VERIFY_POLLS × SEND_VERIFY_S) for the accepted
    `user` turn — returns the instant `_submit_confirmed` sees it, or False
    after the window. A structured-settle poll, never a blind timeout."""
    sleep_fn = sleep_fn or time.sleep
    for i in range(SEND_VERIFY_POLLS):
        if watchdog._submit_confirmed(tpath, baseline, text):
            return True
        if i < SEND_VERIFY_POLLS - 1:
            sleep_fn(SEND_VERIFY_S)
    return False


def _await_typed_landed(pane_id, text, run, sleep_fn, want=True):
    """Poll (bounded) until the input box shows evidence of `text`
    (`want=True`) or has stopped showing it (`want=False`) — the pre-Enter
    TYPE verify. Mirrors `goal._await_typed`, reproduced here to keep tmux_io's
    `import watchdog`-only module boundary (#433). Returns the final verdict; a
    type that never renders is refused (`not want`)."""
    sleep_fn = sleep_fn or time.sleep
    for i in range(SEND_TYPE_SETTLE_POLLS):
        landed = watchdog._typed_landed(text, watchdog._input_line_text(
            watchdog.capture_pane(pane_id, run, lines=40)))
        if landed is want:
            return landed
        if i < SEND_TYPE_SETTLE_POLLS - 1:
            sleep_fn(SEND_TYPE_SETTLE_S)
    return not want


def send_verified(pane_id, text, run=None, tpath=None, sleep_fn=None, logs=None,
                  out=None, user_authored=False, nudge=None, state=None,
                  skip_confirm=False, now=None):
    """Type `text` + Enter into a BARE input box and VERIFY the submit landed
    via the TRANSCRIPT (the #486 delivery bullet's structured proof), not the
    pane render: after the send, the session jsonl at `tpath` must gain a new
    `user` turn carrying `text` within a bounded window. This is the missing
    transcript-proof member of the delivery family (`deliver_with_stash` /
    `_send_goal_verified` / the compact submit-verify) — the piece a raw
    `send_continue` (type + Enter, no post-send read) never had, so a swallowed
    Enter (agent-strip selector #36, or a turn started under the send) used to
    be booked "sent" with the text left hanging in the user's input box (#490).

    The type half mirrors `_send_goal_verified`: a fresh bare re-check right
    before typing, a strip-selector Escape (#36), `_type_literal` CHUNK-typing
    (never a single multi-KB `-l` burst that CC would collapse into a paste,
    #322 — the lane nudge texts run ~550–720 chars, well past the 200-char
    threshold), and a pre-Enter TYPE verify that never submits a collapsed or
    unrendered paste. Only the SUBMIT verify differs: the transcript, not the
    pane render.

    Returns True ONLY on a transcript-CONFIRMED submit. On failure:
      - our text still PROVABLY stuck in the box -> ONE corrective Escape+Enter
        (#36; never a second Escape #35, never a bare second Enter), re-verify;
        if still stuck, `_undo_and_release_slot` backspaces exactly our own
        text off the box (verified bare before we typed, so every char is ours);
      - box bare but unconfirmed -> return False with NO corrective Escape (a
        bare box after a submit may mean a turn genuinely STARTED — an Escape
        there would interrupt it, the #233 harm) and nothing undone;
      - box holds UNRECOGNIZED content (a truncated type, a collapsed hint) ->
        withhold keystrokes (a blind backspace could eat a real draft, #193),
        log the residue HONESTLY (never claim "bare" unread, #134/#360), and
        let the caller's #372 janitor mark backstop it.
    False means "not delivered, retryable next sweep" — the caller leaves its
    own budget unconsumed.

    `out` (#594, optional): when a dict is passed, `send_verified` records the
    ONE outcome a flat bool cannot express — a submit that was DELIVERED but the
    transcript confirmation RACED. It sets `out["delivered_unconfirmed"] = True`
    in the "box bare after our Enter, submit not proven" branch: the Enter
    CLEARED the box (CC accepted/queued the submit), only the `user` turn was
    not written inside the window (the normal case when injecting into an
    actively-cycling armed `/goal` loop). The genuine-swallow path (text left
    STUCK) returns ABOVE via `_undo_and_release_slot` — text backed out, never
    accepted — so it never reaches this branch. TWO live paths DO reach it, both
    delivery: the first-Enter path (box cleared straight away) and the corrective
    Escape+Enter path (lines below) that ends bare; the latter's delivery-ness
    rests on this module's #36 premise (the corrective Escape only DESELECTS the
    agent strip, it never clears the composer), so a bare box after it means the
    Enter, not the Escape, emptied it. A caller that must not re-deliver (job
    20's re-check nudge, #594) reads `ok OR out.get("delivered_unconfirmed")` as
    "delivered", while still retrying a genuine swallow (neither True nor the flag
    set). Even in the theoretical over-claim (an Escape that DID clear a real
    composer), the only cost is the caller advancing its cadence by one period
    (job 20: ≥6h, ~daily) while the ticket stays OPEN + surfaced and job 20 is
    itself the re-check backstop — bounded and self-healing, never a permanent
    silence. Default None -> byte-identical for every existing caller.

    `tpath` is REQUIRED (the transcript is the whole proof); a falsy or
    unreadable `tpath` refuses to send rather than typing blind or reading from
    byte 0. Bare-box ONLY: a pane holding a DRAFT is delivered via
    `deliver_with_stash`; a draft that RACED into the box since the caller's own
    check is rescued and the send aborted.

    `user_authored` (#994): True ONLY for the owner's OWN Discord reply (set
    solely by `discord_replies`). It BYPASSES the nudge kill switch -- the owner
    speaking is never a machine nudge -- so an OFF box still delivers the owner's
    answer. Every machine caller leaves it False and is suppressed when OFF. The
    switch is enforced (and journalled) at the `_type_literal_verified` primitive
    below (#994 REOPEN), forwarding this `user_authored`; no own gate here."""
    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep

    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    if not tpath:
        _log("send-verified abort: no transcript path")
        return False
    # A FRESH capture right before typing (the sibling helpers' own race
    # guard): the caller proved the box bare a moment ago, but round-trips pass
    # before the real keystroke lands.
    cap = watchdog.capture_pane(pane_id, run, lines=40)
    if watchdog._input_line_text(cap) != "":
        watchdog._draft_rescue_persist(pane_id, cap, logs=logs)
        _log("send-verified abort: box not bare pre-send")
        return False
    # #1092 (c) -- the PER-PANE typing-attempt BUDGET, consulted BEFORE any
    # keystroke. A GATED machine nudge (a threaded `nudge=` that is not a RECOVERY
    # revival kind, with `state` to read/write, and NOT the owner's own reply) is
    # refused when the pane has already had `PANE_ATTEMPT_BUDGET` typing attempts
    # this rolling hour, across ALL kinds -- the belt on top of the per-kind floor
    # + total cap that makes any future escape (the #1092 swallowed-batch storm was
    # exactly that) harmless by construction. A REVIVAL of a dead session (resume/
    # compact/wake-parked -> RECOVERY_NUDGE_KINDS) and the owner's OWN reply
    # (`user_authored`) are NEVER gated here: "the owner's problem is typing INTO
    # an active/human pane, not recovery itself" (#1092 addendum), and stranding a
    # limit/401-dead session for an hour is the #520 harm. Placed AFTER the bare
    # check so a box-busy abort (no keystroke) never burns the budget, and BEFORE
    # the strip-Escape (the first keystroke). The goal RE-ARM family's own pane
    # budget is enforced in `deliver_goal` (it delivers via `_send_goal_verified`,
    # not this primitive) -- #1092 item (e).
    # #1092 F1 fix — use the CALLER's sweep `now` when threaded, so the pane-budget
    # stamp shares ONE clock with the end-of-sweep prune (`_prune_pane_attempts`,
    # run at the sweep `now`) and with the re-arm path (`deliver_goal`, which stamps
    # at `now`). A `time.time()` stamp (later than the sweep `now`) used to be
    # reaped by that same-sweep prune as "future", so the budget never accumulated
    # (Review 2 F1). Callers that thread no `now` (card/bounce) fall back to
    # `time.time()`, and the prune's future-keep direction covers that skew.
    _pane_now = now if now is not None else time.time()
    _gated_nudge = (state is not None and nudge is not None
                    and nudge not in RECOVERY_NUDGE_KINDS and not user_authored)
    if _gated_nudge:
        from watchdog import nudge_gate as _ng
        if not _ng.pane_budget_ok(state, pane_id, _pane_now):
            _log("send-verified " + _ng.pane_budget_hold_reason(
                state, pane_id, _pane_now))
            if isinstance(out, dict):
                out["pane_budget_held"] = True
            return False
    # #1002 -- the strip-deselect Escape carries the delivery's "send" kind, so
    # the ONE `keys` primitive gates it: at OFF a machine caller fires ZERO
    # keystrokes (keys suppresses + returns False, this helper bails), while the
    # owner's OWN reply (`user_authored`) bypasses and still deselects + delivers.
    if watchdog._strip_selected(cap):
        if not watchdog.keys(pane_id, "Escape", kind="send", nudge=nudge,
                             user_authored=user_authored, run=run, logs=logs):
            return False
    # Re-verify bare AFTER the strip-Escape and immediately before the type
    # keystroke — a draft racing into that gap would otherwise be typed over
    # (the same second bare-check `_send_goal_verified` does, #176-F3).
    fresh = watchdog.capture_pane(pane_id, run, lines=40)
    if watchdog._input_line_text(fresh) != "":
        watchdog._draft_rescue_persist(pane_id, fresh, logs=logs)
        _log("send-verified abort: box raced busy pre-send")
        return False
    try:
        baseline = os.path.getsize(tpath)
    except OSError:
        # An unreadable transcript cannot verify a submit; refuse rather than
        # read from byte 0 (a prior identical nudge would false-confirm).
        _log("send-verified abort: transcript unreadable pre-send")
        return False
    # #670 -- HEAD-INCLUSIVE verified type + bounded settle/undo/retry, replacing
    # the old `_type_literal` + `_await_typed_landed(want=True)` pair that
    # verified only the TAIL (`_typed_landed`'s endswith) and was head-blind: a
    # swallowed FIRST char (`ane-check...` for `lane-check...`, the send-keys
    # first-byte race) IS a suffix of the intended text, so it passed and Enter
    # submitted the corrupted prompt. `_type_literal_verified` reads the box HEAD
    # back too (it retains the same bounded render-settle tolerance, #670-review
    # R1), re-types on a genuine swallow, and on a HOLD (unreadable / collapsed)
    # box withholds every keystroke (#670-review R2) -- so a head-corrupted
    # prompt is NEVER submitted, and no keystroke is fired into a box we cannot
    # safely backspace.
    if not watchdog._type_literal_verified(pane_id, run, text, sleep_fn,
                                           kind="send", nudge=nudge,
                                           user_authored=user_authored,
                                           logs=logs, state=state):
        if watchdog._pane_shows_collapsed_paste(watchdog._input_line_text(
                watchdog.capture_pane(pane_id, run, lines=40))):
            _log("send-verified abort: collapsed-paste, not submitted")
        else:
            _log("send-verified abort: type not head+tail-verified, not submitted")
        return False
    # #1092 (c) -- the type keystrokes landed (text is in the box), so a typing
    # attempt has DEFINITIVELY been made: stamp the per-pane budget NOW, before
    # the Enter, so it counts regardless of the submit outcome below (delivered,
    # delivered-unconfirmed, OR swallowed). A pre-type abort (box busy / collapsed
    # paste / withheld) returned above WITHOUT reaching here, so it never burns the
    # budget. Only gated machine nudges are counted (see the consult above).
    if _gated_nudge:
        from watchdog import nudge_gate as _ng
        _ng.mark_pane_attempt(state, pane_id, _pane_now)
        if isinstance(out, dict):
            out["attempted"] = True
    watchdog.keys(pane_id, "Enter", kind="send", nudge=nudge,
                  user_authored=user_authored, run=run, logs=logs)
    # #1023 timeout-race — `skip_confirm` (budget too low for the ~10s
    # transcript confirm-wait) short-circuits BOTH the confirm poll AND the
    # corrective Escape+Enter (which itself confirm-waits): the Enter already
    # went in, so fall straight through to the ONE box read below — a bare box
    # there is surfaced as `delivered_unconfirmed` (an accepted state that stamps
    # the floor), so the caller delivers+marks fast instead of polling into the
    # 2-min unit kill. The pre-Enter type-settle above keeps its real sleep.
    if not skip_confirm and _await_submit_confirmed(tpath, baseline, text, sleep_fn):
        return True
    # Unconfirmed. Only act further when our text is PROVABLY still in the box.
    if not skip_confirm and watchdog._typed_landed(text, watchdog._input_line_text(
            watchdog.capture_pane(pane_id, run, lines=40))):
        # A swallowed Enter (#36 class) — ONE corrective Escape+Enter (reached
        # only when ON: a suppressed type bailed above).
        watchdog.keys(pane_id, "Escape", kind="send", nudge=nudge,
                      user_authored=user_authored, run=run, logs=logs)
        watchdog.keys(pane_id, "Enter", kind="send", nudge=nudge,
                      user_authored=user_authored, run=run, logs=logs)
        if _await_submit_confirmed(tpath, baseline, text, sleep_fn):
            return True
        if watchdog._typed_landed(text, watchdog._input_line_text(
                watchdog.capture_pane(pane_id, run, lines=40))):
            # Genuinely stuck — back our own text off the bare-verified box so
            # the next sweep retries from a clean prompt. parked=False: bare
            # box, no stash to pop.
            watchdog._undo_and_release_slot(pane_id, run, text, False, _log,
                                            "send-verified swallowed",
                                            sleep_fn=sleep_fn)
            # #1092 (a)+(b) -- a SWALLOWED attempt IS a delivery attempt: the text
            # reached the pane (and is now backed out, the janitor UNDO). Stamp
            # the per-kind FLOOR for this kind so the SAME kind can never re-fire
            # on the next ~70s sweep (the storm shape) -- one attempt per kind per
            # hour, matching the delivered-unconfirmed floor (#1023 🟡4). Only for
            # a gated single-nudge caller (`send_verified` IS the single delivery
            # path, #1092 design); it already holds `state`+`nudge`+`tpath`, so the
            # sid is `tpath`.stem. The BATCH caller additionally stamps ALL its
            # included kinds via `mark_batch_sent` in its else branch (this stamps
            # only the composite's first kind), and surfaces `out["swallowed"]`.
            if isinstance(out, dict):
                out["swallowed"] = True
            if _gated_nudge:
                from watchdog import nudge_gate as _ng
                _sid = os.path.splitext(os.path.basename(str(tpath)))[0] or None
                if _sid:
                    _ng.mark_sent(state, _sid, nudge, _pane_now)
            return False
    # #1023 timeout-race — on the skip_confirm path we did NO post-Enter poll, so
    # the box may not have render-cleared yet; ONE short settle before the read
    # (far cheaper than the skipped ~10s confirm-wait) lets CC clear the box so
    # the bare-box branch below correctly surfaces `delivered_unconfirmed` instead
    # of reading stale text as "unrecognized" and forcing a re-type next sweep
    # (which would re-open the 1/hour double-delivery this whole lane closes).
    if skip_confirm:
        sleep_fn(SEND_VERIFY_S)
    # Unconfirmed and NOT provably stuck. Read the box ONCE and log honestly —
    # never claim a state we did not read (#134/#360). Withhold keystrokes on
    # every branch (Escape could interrupt a turn that started #233; a blind
    # backspace could eat a real draft #193). A caller that marked janitor
    # provenance AND whose payload starts with a recognized OWN prefix
    # (`_JANITOR_OWN_PREFIXES`, e.g. the lane-check nudge) has the #372 janitor
    # reclaim any residue before the next sweep re-reads the pane.
    itext = watchdog._input_line_text(watchdog.capture_pane(pane_id, run, lines=40))
    if itext is None:
        _log("send-verified unconfirmed: box unreadable, submit not proven")
    elif itext == "":
        _log("send-verified unconfirmed: box bare, submit not proven")
        # #594: the Enter CLEARED the box (CC accepted/queued the submit) — this
        # is a DELIVERY the transcript confirmation merely raced (a cycling armed
        # loop). NOT a swallow (that path returned above with the text UNDONE).
        # Surface it so a caller that must not re-deliver treats it as delivered.
        if isinstance(out, dict):
            out["delivered_unconfirmed"] = True
    else:
        _log("send-verified unconfirmed: box holds unrecognized content, "
             "left in place (retryable)")
    return False


def submit_own_draft_verified(pane_id, draft, run=None, tpath=None,
                              sleep_fn=None, logs=None, caller_proven_own=False,
                              out=None, user_authored=False, nudge=None):
    """#501 — SUBMIT an EXISTING recognized-own nudge draft already sitting in
    the input box, transcript-verified — WITHOUT typing anything. The missing
    "submit an already-composed OWN draft" member of the delivery family
    (`send_verified` TYPES then submits; `deliver_with_stash` parks a FOREIGN
    draft and types around it): a pane holding OUR OWN previously-swallowed
    nudge (a pre-#490 blind Enter stranded it) must be FINISHED by submitting
    the draft in place, never stashed-around and retyped — that retype aborts
    forever against the persistent swallow that stranded it (the live cam-box
    zbynek-4:0.0 incident: `stash-abort 1/5 -> backoff -> give-up`, the nudge
    never delivered).

    HARD foreign-draft gate (never weakened — HARD CONSTRAINT a): `draft` MUST
    start with one of the UNAMBIGUOUS machine-diagnostic nudge prefixes
    (`_own_nudge_submit_prefix`: `lane-check: `/`bounce-backstop: `/`gk-request
    backstop: `, texts a human PROVABLY never types). The human-typeable
    `/goal `/`/compact` prefixes are refused here (content is not proof of
    ownership for them — the #372 janitor recovers those only WITH provenance).
    Any unrecognized/foreign draft is refused with ZERO keystrokes: NEVER a
    blind Enter on a user's parked draft.

    `caller_proven_own` (#806): a CALLER-PROVEN own PLAIN-TEXT draft. When True,
    the caller has ALREADY established ownership against this exact expected
    `draft` (job 7's `_box_holds_our_own_text` over a recorded `dreply_typed`
    reply — a head+tail check for a WRAPPED box that DEGRADES to a tail/suffix
    match on a NON-wrapped one), and THIS primitive COMPLETES the proof by
    re-verifying the box HEAD row is a leading substring of `draft` on its OWN
    fresh capture — together a STRONGER proof than the unambiguous-nudge prefix,
    the caller-proven shape `submit_own_goal_verified` (#566) uses (head-only
    here, the plain-text reply being transcript-confirmable). So the prefix gate
    is SATISFIED by that combined proof: recognition + the transcript token key
    on the box HEAD row being a leading substring of `draft` (wrap-safe) rather
    than on a registered machine prefix. Default False -> byte-identical
    prefix-gated behavior for every existing caller. The
    two modes never mix: a caller-proven `draft` is PLAIN TEXT (transcript-
    confirmable), never a slash command (`submit_own_goal_verified` owns the
    pane-confirmed `/goal ` path — content proof there is a composite the
    transcript can never match).

    Recognition + verification read the box HEAD row (`_input_box_head_text`),
    NEVER the tail (`_input_line_text`): every real own nudge is 289-720 chars
    and WRAPS, so its prefix sits on the head and is absent from the tail (#501
    -- reading the tail made this path dead against exactly the wrapped drafts
    the incident is about).

    Transcript-proof (HARD CONSTRAINT b): after the Enter, the session jsonl at
    `tpath` must gain a NEW top-level `user` turn carrying the HEAD-ROW TEXT
    (the draft's own leading substring -- wrap-safe AND far more specific than
    the bare prefix, so a foreign turn merely containing `lane-check: ` cannot
    false-confirm) within the bounded window (`_await_submit_confirmed`). A
    swallowed Enter (#36) earns ONE corrective Escape+Enter (never a second
    Escape #35), re-verified. Never booked delivered on a pane render alone.

    No keystroke ever RE-TYPES or BACKSPACES the draft: the box already holds
    our own text, and we neither typed it nor can prove its exact length (a
    wrapped multi-row draft makes a byte-exact undo unprovable), so on a
    genuinely-stuck submit we leave it EXACTLY as it is — a legit pending own
    nudge — and return False; the caller's give-up ping escalates (#193: never
    destroy an unproven buffer).

    `out` (#594/#806, optional): the SAME `delivered_unconfirmed` channel
    `send_verified` carries, so the own_stuck lane matches the fresh lane. When a
    dict is passed, a box that went BARE after the Enter (submit not confirmed
    inside the window but the box CLEARED — CC accepted/queued it, the transcript
    merely raced, the normal case for an actively-cycling armed `/goal` loop) sets
    `out["delivered_unconfirmed"] = True`. A caller reads `ok OR
    out.get("delivered_unconfirmed")` as delivered so it NEVER re-types the reply
    next sweep (a genuine swallow leaves the draft in place and sets NOTHING, so
    it is still retried). Default None -> byte-identical for every existing caller.

    Returns True ONLY on a transcript-CONFIRMED submit; False = not delivered,
    retryable next sweep (the caller leaves its own budget unconsumed). A
    falsy/unreadable `tpath` refuses (the transcript is the whole proof).

    `user_authored` (#994): True ONLY for the owner's OWN Discord reply (set
    solely by `discord_replies`). It BYPASSES the nudge kill switch so an OFF
    box still submits the owner's own answer draft; every machine caller leaves
    it False and is suppressed when OFF -- enforced at the ONE `keys` primitive
    (#1002): the FIRST keystroke of the submit (the strip-deselect Escape, else
    the submit Enter) carries kind="draft", so at OFF it is suppressed +
    journalled once and this helper bails, replacing the deleted helper-top gate."""
    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep

    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    if caller_proven_own:
        # #806 — the CALLER proved head+tail ownership of this exact PLAIN-TEXT
        # `draft`; the box HEAD row (a leading substring of `draft`, wrap-safe)
        # is the fresh-race recognizer, mirroring `submit_own_goal_verified`'s
        # `text.startswith(head)`. `startswith` is robust to the tail-whitespace
        # strip that would break an `endswith` (#720/#763).
        def _still_own(h):
            return bool(h) and draft.startswith(h)
    else:
        prefix = watchdog._own_nudge_submit_prefix(draft)
        if not prefix:
            _log("submit-own abort: draft is not an unambiguous own nudge")
            return False

        def _still_own(h):
            return bool(h) and h.startswith(prefix)
    if not tpath:
        _log("submit-own abort: no transcript path")
        return False
    # A FRESH capture right before the Enter (the sibling helpers' own race
    # guard): the box must STILL hold the same OWN draft. Recognition reads the
    # box HEAD row (`_input_box_head_text`), NOT `_input_line_text` (the TAIL):
    # every real own nudge is 289-720 chars and WRAPS at a live pane width, so
    # its prefix sits on the head row and is NEVER on the tail (#501 -- keying
    # on the tail made this whole path DEAD against exactly the wrapped drafts
    # the incident is about). A draft that raced OUT (bare / submitted) or a
    # FOREIGN draft that raced IN must NEVER be Entered.
    cap = watchdog.capture_pane(pane_id, run, lines=40)
    head = watchdog._input_box_head_text(cap)
    if not _still_own(head):
        _log("submit-own abort: box no longer holds the recognized own draft")
        return False
    if "esc to interrupt" in (cap or ""):
        _log("submit-own abort: live turn")
        return False
    # A SELECTED agent-strip row (#36) steals the Enter — ONE Escape returns
    # focus to the input box (the draft survives ONE Escape; two would delete
    # it, #35), then re-confirm the draft is still there before submitting.
    # #1002 -- the Escape carries kind="draft": at OFF `keys` suppresses it (the
    # deleted helper-top gate) and this helper bails keystroke-free.
    if watchdog._strip_selected(cap):
        if not watchdog.keys(pane_id, "Escape", kind="draft", nudge=nudge,
                             user_authored=user_authored, run=run, logs=logs):
            return False
        cap = watchdog.capture_pane(pane_id, run, lines=40)
        head = watchdog._input_box_head_text(cap)
        if not _still_own(head):
            _log("submit-own abort: own draft gone after strip Escape")
            return False
    try:
        baseline = os.path.getsize(tpath)
    except OSError:
        _log("submit-own abort: transcript unreadable pre-send")
        return False
    # The head row IS the draft's leading substring (the first ~pane-width
    # chars), so it appears verbatim in the transcript's `user` turn AND is far
    # more specific than the bare 12-char prefix — a wrap-safe, low-false-
    # confirm verification token (a foreign turn would have to carry this whole
    # ~170-char line, not just `lane-check: `). Its only failure mode is a
    # benign non-confirm -> retry, never a false positive nor a destroyed draft.
    token = head
    # #1002 -- the submit Enter carries kind="draft": for a machine caller at
    # OFF (not strip-selected, so this is the FIRST keystroke) `keys` suppresses
    # it, journals once, returns False, and this helper bails -- the deleted
    # helper-top gate, now enforced at the primitive. The owner's own reply
    # (`user_authored`) bypasses.
    if not watchdog.keys(pane_id, "Enter", kind="draft", nudge=nudge,
                         user_authored=user_authored, run=run, logs=logs):
        return False
    if _await_submit_confirmed(tpath, baseline, token, sleep_fn):
        _log("submit-own delivered")
        return True
    # Unconfirmed. Only send a corrective Escape+Enter when our OWN draft is
    # PROVABLY still in the box (a swallowed Enter, #36) — never a second
    # Escape (#35), never an Escape into a box that already went bare (a turn
    # may have started, #233). Reached only when ON (a suppressed submit bailed).
    still = watchdog._input_box_head_text(watchdog.capture_pane(pane_id, run, lines=40))
    if _still_own(still):
        watchdog.keys(pane_id, "Escape", kind="draft", nudge=nudge,
                      user_authored=user_authored, run=run, logs=logs)
        watchdog.keys(pane_id, "Enter", kind="draft", nudge=nudge,
                      user_authored=user_authored, run=run, logs=logs)
        if _await_submit_confirmed(tpath, baseline, token, sleep_fn):
            _log("submit-own delivered (after corrective Escape+Enter)")
            return True
    # Genuinely unconfirmed. Leave the box EXACTLY as-is — never backspace our
    # own draft (we did not type it, cannot prove its length, and it is a legit
    # pending own nudge). Read the box ONCE and log honestly (#134/#360), never
    # claim a state we did not read; the caller's give-up escalation fires.
    final = watchdog._input_box_head_text(watchdog.capture_pane(pane_id, run, lines=40))
    if final is None:
        _log("submit-own unconfirmed: box unreadable, submit not proven")
    elif _still_own(final):
        _log("submit-own unconfirmed: own draft still in box, left in place "
             "(retryable)")
    elif final == "":
        _log("submit-own unconfirmed: box bare, submit not proven")
        # #594/#806: the Enter CLEARED the box (CC accepted/queued the submit) —
        # a DELIVERY whose transcript confirm merely raced (a cycling armed /goal
        # loop, exactly the population job-7 replies target). NOT a swallow (that
        # path leaves the draft in place above). Surface it so the own_stuck
        # caller reads it as delivered and never RE-TYPES the reply next sweep
        # (the double-delivery the fresh lane already avoids via send_verified).
        if isinstance(out, dict):
            out["delivered_unconfirmed"] = True
    else:
        _log("submit-own unconfirmed: box changed, left in place (retryable)")
    return False


def submit_own_goal_verified(pane_id, text, run=None, sleep_fn=None, logs=None,
                             nudge="goal-sweep"):
    """#566 -- SUBMIT an EXISTING, COMPLETE, own `/goal <...>` payload already
    sitting swallowed-unsubmitted in the input box, PANE-verified, WITHOUT
    re-typing or backspacing it. The `/goal`-specific sibling of
    `submit_own_draft_verified` (#501): that primitive REFUSES the human-typeable
    `/goal ` prefix because CONTENT is not proof of ownership for it -- here the
    CALLER (the #566 goal recovery in `deliver_goal`) has ALREADY proven
    ownership (the #372 janitor watch/park provenance + the recent-human gate)
    AND passes the EXACT expected payload, so ownership is proven by an EXACT
    head+tail match against `text` rather than an unambiguous-nudge prefix.

    COMPLETENESS is mandatory before ANY keystroke (HARD CONSTRAINT: a truncated
    slash command is NEVER submitted -- the #36 disaster): the box HEAD row must
    be a leading substring of `text` AND the box TAIL row must be a trailing
    substring of `text` AND `text` must start with `/goal `. Both ends matching
    the literal expected payload prove the box holds the WHOLE rendered
    `/goal <...>`; a partial/truncated type (tail does not match `text`'s tail)
    is refused. Recognition reads the HEAD row (`_input_box_head_text`) + the
    TAIL row (`_input_line_text`), never guessing. A collapsed-paste placeholder
    (`[Pasted text #N]`) never `startswith("/goal ")`, so it is refused too.

    Confirmation is PANE-based, NOT transcript-based (#566-review F1 -- the
    #501 sibling's transcript token works for a PLAIN-TEXT nudge, but a SLASH
    COMMAND like `/goal` is written to the transcript as a `<command-name>
    /goal</command-name> ... <command-args>...` COMPOSITE, so the raw `/goal
    <...>` text is NEVER a contiguous substring of the accepted `user` turn and a
    transcript match can never succeed -- `watchdog/decide.py`'s own #336-F1
    finding). So this mirrors the PROVEN production `/goal` typing path
    `goal._send_goal_verified` exactly: press Enter, and the submit is confirmed
    the instant the box no longer holds our `/goal` (`_await_typed_landed(...,
    want=False)`); a swallowed Enter (#36 agent-strip) earns ONE corrective
    Escape+Enter (never a second Escape #35), re-verified. On a genuinely
    unconfirmed submit the box is left EXACTLY as-is (never backspace our own
    complete payload; it is a legit pending arm), logged honestly, return False;
    the caller's escalation fires.

    #994/#1002: goal auto-arm is machine-composed, so an OFF box suppresses it
    (type NOTHING, journal one line, return False -- the caller retries when back
    ON). Enforced at the ONE `keys` primitive: the FIRST keystroke of the submit
    (the strip-deselect Escape, else the submit Enter) carries kind="goal", so at
    OFF `keys` suppresses it + journals once and this helper bails -- the deleted
    helper-top gate, now at the primitive."""
    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep

    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    if not text or not text.startswith("/goal "):
        _log("submit-own-goal abort: payload is not a /goal command")
        return False

    def _complete_own_goal(cap):
        # The box holds the COMPLETE literal `/goal <text>`: head row is its
        # leading substring AND tail row is its trailing substring. A truncated
        # type matches the head but NOT the tail -> refused.
        head = watchdog._input_box_head_text(cap)
        tail = watchdog._input_line_text(cap)
        return (bool(head) and head.startswith("/goal ")
                and text.startswith(head)
                and bool(tail) and text.endswith(tail))

    cap = watchdog.capture_pane(pane_id, run, lines=40)
    if not _complete_own_goal(cap):
        _log("submit-own-goal abort: box no longer holds the complete own /goal")
        return False
    if "esc to interrupt" in (cap or ""):
        _log("submit-own-goal abort: live turn")
        return False
    # A SELECTED agent-strip row (#36) steals the Enter -- ONE Escape returns
    # focus (the draft survives ONE Escape; two would delete it, #35), then
    # re-confirm the complete own /goal is still there before submitting.
    if watchdog._strip_selected(cap):
        if not watchdog.keys(pane_id, "Escape", kind="goal", nudge=nudge, run=run, logs=logs):
            return False
        cap = watchdog.capture_pane(pane_id, run, lines=40)
        if not _complete_own_goal(cap):
            _log("submit-own-goal abort: own /goal gone after strip Escape")
            return False
    if not watchdog.keys(pane_id, "Enter", kind="goal", nudge=nudge, run=run, logs=logs):
        return False
    # PANE proof: the box no longer holds our `/goal` (`want=False`) => submitted.
    if not _await_typed_landed(pane_id, text, run, sleep_fn, want=False):
        _log("submit-own-goal delivered")
        return True
    # STILL in the box -- a swallowed Enter (#36). ONE corrective Escape+Enter
    # (reached only when ON: a suppressed submit bailed above).
    watchdog.keys(pane_id, "Escape", kind="goal", nudge=nudge, run=run, logs=logs)
    watchdog.keys(pane_id, "Enter", kind="goal", nudge=nudge, run=run, logs=logs)
    if not _await_typed_landed(pane_id, text, run, sleep_fn, want=False):
        _log("submit-own-goal delivered (after corrective Escape+Enter)")
        return True
    # Genuinely unconfirmed -- our own complete /goal is still in the box. Leave
    # it EXACTLY as-is (never backspace our own payload; it is a legit pending
    # arm), logged honestly (#134/#360); the caller's escalation fires.
    _log("submit-own-goal unconfirmed: own /goal still in box, left in place "
         "(retryable)")
    return False
