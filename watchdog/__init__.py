"""api-watchdog — keep unattended Claude Code sessions moving. Seven jobs per poll:

  (1) AUTO-RESUME: a session STALLED ON AN API ERROR (529 / ConnectionRefused /
      rate limit) is resumed with `tmux send-keys "continue"` + a Discord ping.
  (2) NOTIFY-ONLY: a session WAITING ON THE USER (an AskUserQuestion / permission
      dialog is open) is PINGED — never acted on, a design decision needs the
      human. This closes the gap that left a blocked `bakerion` session silent:
      an AskUserQuestion wait is neither a `❓` Stop-marker turn nor `idle_prompt`,
      so no hook covered it.
  (3) WEEKLY-LIMIT ALERT: the 3rd reason work stalls — the WEEKLY subscription
      token limit runs out. A rate-limited poll of Anthropic's oauth/usage window
      state (the same data `/usage` shows) pings ONCE per reset window when a
      weekly limit reaches the cap percent (default 98), so the user can react before it
      hard-stops. Polled at most every USAGE_INTERVAL — the endpoint 429s hard.
  (4) WORKING-STALL SELF-CHECK NUDGE: the 4th reason work stalls — a session ended
      `⏳ WORKING` (a background process / Monitor / build / dispatched subagent is
      running, it'll report when done) but then NOTHING happened for STALL_WORKING and
      NO subagent is advancing. A crashed / OOM-killed / hung job emits no completion
      event, so a success-only wait hangs FOREVER (the bug that lost the user 8 hours
      on a dead `verdict` process; the user also had to hand-type "stucked?" into
      nearly every session one morning because none of them internally re-checked why a
      subagent/subprocess had gone silent for hours). The watchdog NUDGES the parked
      pane with a `stuck-check` self-check prompt (`send-keys` — the autonomous
      equivalent of the user's manual "stucked?") telling the session to verify the
      LIVENESS of its launched work (ps / log mtime / dashboard / gh run) and intervene
      if it died. WHY THIS IS SAFE where a blind `continue` was NOT (the user's scar):
      the nudge is a QUESTION, not a forced resume — it delegates the healthy-vs-dead
      judgment to the ONE entity equipped to make it (the session has eyes: PIDs,
      logs, the dashboard). A healthy CI/encode wait nudged this way just self-checks,
      confirms alive, and continues; the keystroke itself writes a transcript entry, so
      idle resets and the episode self-resolves in ONE nudge with no Discord noise. A
      dead job → the session intervenes. Only if the nudge produces NO response across
      MAX_WORKING_NUDGES retries (the Claude process itself is wedged) does it ESCALATE
      to ONE Discord ping ("auto-recovery failed — needs you"). So the common case is
      ZERO user-facing pings (it just un-sticks itself); a ping fires only when the
      automated fix genuinely failed. Safety gates: copy-mode + ambiguous-pane skips
      (never type into the wrong/scrolled pane), an advancing-subagent check (a live
      worker writing its transcript is progress, not silence → skip), idle-reset
      self-dedup. THRESHOLD is 30 min — the user's explicitly chosen cadence: they said
      an occasional "stucked?" on silence is fine EVEN IF the answer is "not stuck",
      because a liveness confirmation beats hoping nothing is wedged and losing a whole
      day (so firing on a still-healthy wait is acceptable by design, not a bug). The
      real fix is still the agent's OWN in-session liveness poll (modules/quality/
      verify-launched-work-liveness.md); job 4 is the model-independent backstop for
      when a session fails to follow it.
  (5) DELIVER A PENDING ✅ (the unreliable-idle_prompt backstop): notify-discord-
      pending.sh records a `✅ DONE` to /tmp/claude-discord-pending-<sid>; notify-
      discord.sh delivers it on `idle_prompt` — but CC emits idle_prompt UNRELIABLY
      over tmux/SSH, so on dev2 a finished turn's ✅ ping silently never arrives (the
      pending just sits in /tmp — the reported symptom). The watchdog delivers it
      once the session has been idle >= PENDING_DONE_GRACE, but ONLY while its
      CURRENT last marker is STILL ✅ — a session that re-fired (a background task
      re-invoked it → now ⏳) has its stale ✅ cleared WITHOUT pinging, so the device
      is never told "done" for work that kept going. PING ONLY; claim-then-send.
  (6) 5-HOUR SESSION-LIMIT AUTO-RESUME: the session hit Claude Code's 5-hour
      session limit ("You've hit your session limit · resets <time>"), shown in the
      PANE (not reliably a transcript api-error), detected only in the last 10
      lines above the input box (a stale echo of the banner scrolled higher in
      the transcript output does not count — gk incident 2026-07-24). This is
      TIME-BASED — `continue` BEFORE the reset is a no-op that just re-hits the
      limit (the user's incident: repeated `continue` → "You've hit your session
      limit"). So the watchdog PINGS ONCE with the reset time, waits for the
      reset clock (read in the banner's own tz, incl. a bare "UTC"/"GMT"), then
      RETRIES an auto-resume AFTER it — every SESSLIMIT_RETRY_S, up to
      SESSLIMIT_MAX_TRIES, submitting the user's own stable draft instead of
      typing over it when one is present, giving up with ONE ping if the resume
      never lands. State (`sesslimit:<sid>`) carries the parsed reset epoch,
      pinged/attempts/last_try/draft_hash/gave_up across polls; a genuinely new
      limit window (5h → weekly) re-arms it.
  (7) DISCORD REPLY → THE ASKING SESSION: when a ❓ ping is delivered, the send path
      records the ping's Discord message id → the asking session
      (notify.record_question). The user ANSWERS by REPLYING to that ping in Discord;
      this job reads recent messages in the notification thread(s), matches a reply's
      referenced message id to the local question map, and types the answer into that
      exact session's IDLE pane (send-keys, gated on pane_at_idle_prompt — the #233
      invariant). SECURITY: a reply is actioned ONLY when its author is a KNOWN OWNER
      of this machine (notify.known_owner_ids), it explicitly REPLIES to a ❓ THIS
      machine sent, and the target pane is idle at a free `❯`. Delivered once (dedup +
      the question is dropped on delivery), ✅-reacted on success. So the user can
      answer an autopilot question from their phone and it lands in the right Claude.

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
  (Claude Code's own definitive marker). This is the ONLY trigger — it is set only
  on an error CC actually hit, never on a user/agent merely quoting "API Error" in
  prose. (An earlier pane-text fallback was removed: it matched any session
  DISPLAYING api-error text and false-nudged an active meta-conversation.)
- STALL TIMER (state, NOT transcript mtime): from the first poll where the last
  reply is an api-error, `decide` counts how long it has stayed that way and nudges
  after GRACE. It does NOT gate on transcript-idle — Claude Code's own retries +
  queue/snapshot writes keep touching the transcript, so an mtime-idle gate never
  reaches GRACE for a rate-limited session (the bug that left `presenter`
  unnudged). first_seen is seeded with `now - idle` so an already-stale stall
  counts from when it really began; a session that recovers (last reply turns
  normal) is dropped from state, giving Claude Code its own GRACE to recover first.

SAFETY GATES (never steer keys into the wrong / busy pane)
---------------------------------------------------------
- AMBIGUOUS BINDING: panes are grouped by their resolved transcript; if two panes
  own one transcript (two `claude` terminals in one cwd, or two cwds colliding
  under CC's '/'/'.'/'_'→'-' dir encoding) we SKIP — never guess which pane to
  poke. A missed auto-resume beats a `continue` typed into a healthy pane.
- COPY-MODE: a pane in copy-mode / a modal (user scrolling) is skipped this cycle
  WITHOUT burning a retry — keys would be swallowed or corrupt the user's selection.

ACTION (state machine, see `decide`)
------------------------------------
first sighting -> 'wait' (record first_seen); 'nudge' #1 right away IF already
                 >=GRACE stuck (seeded from now-idle), else after GRACE (+ ONE ping)
+INTERVAL each      -> 'nudge' #2, #3
after MAX_NUDGES     -> 'escalate' (ping "gave up", stop — no continue-spam during
                        a long Anthropic outage)
USAGE/QUOTA cap      -> ping ONCE, NO `continue` (time-based; only the reset clock
                        fixes it — CC auto-resumes when the cap resets)
recovered            -> key dropped from state (a future error starts fresh)

A session waiting on a real `❓` is NEVER auto-continued: its last assistant entry
is the question (not `isApiErrorMessage`), so the error signal is false.

This module is PURE logic + thin tmux shims. The I/O (`run` = tmux exec, `send_fn`
= Discord send) is injectable so the state machine is unit-tested with no tmux and
no network.
"""

import datetime
import hashlib
import json
import os
import re
import time
from pathlib import Path

# Tunables (the CLI may override; defaults match the user's spec: 5-min grace,
# `continue`, 3 retries then give up).
GRACE_SECONDS = 5 * 60
RETRY_INTERVAL_SECONDS = 5 * 60
MAX_NUDGES = 3
NUDGE_TEXT = "continue"
# A session sitting on an interactive prompt (AskUserQuestion / permission /
# plan-approval) this long with no progress = the user is away → ping (NEVER act).
WAIT_GRACE_SECONDS = 2 * 60
# A waiting episode is kept alive while the prompt footer keeps appearing; it ends
# (and a future prompt may ping again) only after the footer has been ABSENT this
# long. Tolerates a transient capture miss / transcript jitter (a multi-question
# dialog or a re-ask loop) so the SAME open prompt is pinged exactly once.
WAIT_CLEAR_SECONDS = 90
# (4) WORKING-STALL self-check NUDGE. A turn that ended `⏳ WORKING` (Claude said a
# background task / subagent is running and it'll report when done) but has then been
# idle a LONG time MIGHT mean the launched work died silently — a crashed / OOM-killed
# / hung process or a dead subagent emits no completion event, so a success-only wait
# hangs forever (the bug that lost the user 8 hours on a dead `verdict` process; and
# the morning the user had to hand-type "stucked?" into nearly every session because
# none re-checked why a subagent/subprocess had been silent for hours). PRIOR DESIGN
# was PING-ONLY: an adversarial review noted idle-after-`⏳` is ALSO the signature of
# HEALTHY waiting (a live CI run 15-25 min, a mutation gate ≤20 min, an encode, GPU
# transcription all freeze the parent transcript), and a blind `continue` into a
# possibly-healthy pane was the user's scar — so it refused to act. BUT a ping to an
# OFFLINE user does nothing for the whole night, and the user explicitly asked for the
# autonomous form of their own "stucked?": NUDGE the session to self-check. This is
# safe where blind `continue` was not, because the nudge is a QUESTION, not a forced
# resume — it delegates the healthy-vs-dead judgment to the session itself, the one
# entity with eyes (PID, log mtime, dashboard, gh run). A healthy wait nudged this way
# self-checks, confirms alive, and continues; the keystroke writes a transcript entry
# so idle resets and the episode self-resolves in ONE nudge with no Discord noise. A
# dead job → the session intervenes. The poller never decides liveness — it only
# triggers the decision the rule (verify-launched-work-liveness.md) already mandates
# the session make. THRESHOLD = 30 min, the user's explicitly chosen cadence: the user
# said an occasional "stucked?" on silence is "úplne v poriadku" EVEN IF the session
# answers "not stuck" — they'd far rather over-nudge and get a liveness CONFIRMATION
# than hope nothing is stuck and lose a whole day. So firing on a still-healthy wait is
# acceptable BY DESIGN, not a bug to avoid (the v1 review's "never type into a healthy
# pane" was about blind `continue`; a benign self-check QUESTION the user wants).
# Kept targeted: (a) copy-mode + ambiguous-pane skips (never type into the wrong /
# scrolled pane); (b) an advancing SUBAGENT transcript = visible progress, not silence
# → skip (the user's trigger is SILENCE, and a live worker writing its transcript isn't
# silent); (c) escalate to a Discord ping ONLY after MAX_WORKING_NUDGES no-response
# nudges (the Claude process itself is wedged) — so the common case is ZERO user pings.
STALL_WORKING_SECONDS = 30 * 60
# The self-check nudge text — the autonomous equivalent of the user typing "stucked?".
# Single line (send-keys -l types it as one prompt, then Enter submits). Contains
# "stuck-check" so the pane content is unambiguous in the transcript + greppable.
# WORDING IS DEATH-GATED ON PURPOSE (adversarial review, finding #2): the nudge fires
# on a STILL-HEALTHY long wait too (a 35-min GPU transcription, a long Monitor), so it
# must NOT read as "restart the job". Order: verify FIRST → if ALIVE, only confirm and
# continue, restart NOTHING → ONLY IF death is proven by concrete evidence, intervene.
WORKING_NUDGE_TEXT = (
    "stuck-check: tvrdíš ⏳ WORKING ale dlho ticho a nebeží žiadny podagent. "
    "NAJPRV over liveness spustenej úlohy KONKRÉTNYM dôkazom — ps PID, mtime "
    "logu/transcriptu podagenta, dashboard, gh run. AK ešte žije, len to potvrď a "
    "pokračuj v bounded sledovaní — NIČ nereštartuj. LEN AK je smrť potvrdená dôkazom, "
    "zasiahni (reštart / re-route / re-dispatch). Nikdy nečakaj slepo na success-only "
    "signál, ale ani neintervenuj bez dôkazu o smrti."
)
# After the first nudge, re-nudge only this often — and only if the session produced
# NO response (a successful nudge resets idle below the threshold, so job 4 stops
# firing for it). So a retry means the keystroke had no effect = the Claude process
# itself is wedged, not just its launched job.
WORKING_RETRY_INTERVAL_SECONDS = 5 * 60
# After this many no-response nudges, give up auto-recovery and ping the user once.
MAX_WORKING_NUDGES = 3

# (4a) TEXT-EMITTED TOOL-CALL STALL — a faster, higher-precision sibling of job 4.
# Sometimes the model emits a tool call as LITERAL TEXT (a `<invoke name="...">...`
# block inside an assistant TEXT block) instead of a structured tool_use. The harness
# never parses it → nothing runs → the turn just ENDS and the session sits idle at the
# prompt, often with a now-stale `⏳ WORKING` (or no marker at all) still on screen. It
# LOOKS like it was about to act; it is dead. Unlike job 4 (which must wait
# STALL_WORKING_SECONDS because idle-after-`⏳` is ALSO healthy waiting), this stall is
# detectable INSTANTLY and with high precision from the transcript SHAPE — the last
# real assistant message ENDS with the tool-call markup and carries NO parsed tool_use
# block — so it nudges after only a short grace (which guards against reading a
# mid-write turn), with no 30-min wait, regardless of marker. Incident: camera-box PR
# #305 sat ~20 min on a `court <invoke name="Read">…</invoke>` text turn (caveman lite
# suspected) while a green auto-merge PR went unmerged and the user could not tell it
# had died. See verify-launched-work-liveness.md.
STALL_TEXTCALL_SECONDS = 2 * 60
# The nudge for a text-emitted tool-call stall — tells the session its last turn
# emitted a tool call as TEXT (so it never ran) and to re-issue it and continue.
# Single line (send-keys -l). Contains "stuck-check" so the pane line stays greppable.
TEXTCALL_NUDGE_TEXT = (
    "stuck-check: tvoj posledný turn vypísal volanie nástroja ako TEXT "
    "(<invoke name=...>) namiesto reálneho tool-callu — nespustilo sa, turn skončil "
    "a stojíš (nepracuješ, hoci to tak možno vyzerá). Zopakuj to volanie poriadne "
    "ako reálny nástroj a pokračuj v rozrobenej práci."
)

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

PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATE_PATH = Path.home() / ".claude" / "api-watchdog-state.json"

# Api-error episode keys in the state file are BARE SESSION IDS (transcript
# stems — UUIDs). The cleanup pass may delete ONLY these; every other bare key
# is a NAMED, job-owned store (dreply_*, inputdead, goalarm, …) with its own
# pruning — deleting those each cycle reset job 7's ticket-fallback clock
# forever (the starved montalu #1638 answer, 2026-07-21).
_SESSION_KEY_RX = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

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


def transcript_last_model(path, max_lines=200):
    """`message.model` of the session's most recent assistant entry that
    carries one (e.g. `claude-fable-5`, `claude-opus-5[1m]`), or '' if none /
    unreadable. Feeds job 12 (MODEL RECONCILE) — a long-lived session keeps
    whatever model it started on; this is how that job tells which sessions
    are still parked on an expensive tier. Widens the tail window past
    `_iter_jsonl_tail`'s default 60 (a `model` field sits only on assistant
    entries — a run of tool-result/system entries after the last real reply
    can push it further back than 60 lines)."""
    for entry in reversed(_iter_jsonl_tail(path, max_lines=max_lines)):
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message")
        if isinstance(msg, dict):
            model = msg.get("model")
            if model:
                return model
    return ""


def transcript_current_context(path, max_lines=200):
    """The session's CURRENT context size — cache_read_input_tokens +
    cache_creation_input_tokens off the newest assistant usage entry.
    Feeds job 15 (COMPACT OVERGROWN IDLE SESSIONS).

    A SINGLE API call can render as SEVERAL transcript lines (a thinking
    block, a text block, a tool_use block — each its own JSONL entry) that
    all share the SAME `message.id` and carry an IDENTICAL usage snapshot
    (verified live against a real forestshop-parovanie-produktov transcript,
    2026-07-25). Counting every line would triple-count one turn's context,
    so this walks backward from the newest entry, takes the newest
    `message.id`'s group, and returns the MAX (never the SUM) across the
    records sharing that id. An entry with no `id` at all (older/foreign
    transcript shapes) is treated as its own standalone group — its context
    is returned directly with no further grouping.

    0 if the transcript has no usage entries / is unreadable / doesn't
    exist (`_iter_jsonl_tail` already fails safe on a missing file)."""
    _MISSING = object()
    best_id = _MISSING
    best_ctx = 0
    for entry in reversed(_iter_jsonl_tail(path, max_lines=max_lines)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        u = msg.get("usage") or {}
        if not u:
            continue
        ctx = (int(u.get("cache_read_input_tokens") or 0)
              + int(u.get("cache_creation_input_tokens") or 0))
        mid = msg.get("id")
        if best_id is _MISSING:
            best_id = mid
            best_ctx = ctx
            if mid is None:
                break               # no id to group further entries by
            continue
        if mid != best_id:
            break                   # walked past the newest message.id's group
        best_ctx = max(best_ctx, ctx)
    return 0 if best_id is _MISSING else best_ctx


# A status marker is the FIRST glyph of its own line (`⏳ WORKING: …`) — anchored so
# a `⏳`/`✅`/`❓` QUOTED mid-prose (common in this very project, which documents the
# markers) does NOT false-match. Checked over the last few non-blank lines, not only
# the last, so a turn that appends a trailing URL / PR / deploy line after the marker
# is still recognised.
_MARKER_RX = re.compile(r"^\s*(⏳|✅|❓)")


def transcript_last_marker(path):
    """The status marker (⏳ / ✅ / ❓) of the session's last REAL assistant message,
    or '' if none. Trailing synthetic / tool-only entries are skipped. An
    `isApiErrorMessage` entry returns '' — an api-error is NOT a status marker (job 1
    owns those). Used by job 4 to spot a `⏳ WORKING` turn that has gone idle; a
    `✅`/`❓`/none last marker is NOT a working-stall (done / waiting-on-user / plain
    end), so it never triggers the job-4 ping."""
    return transcript_last_marker_line(path)[0]


def transcript_last_marker_line(path):
    """(marker, full marker LINE) of the session's last real assistant message,
    or ('', ''). Same walk/semantics as transcript_last_marker — the LINE feeds
    declared_wait_until (a ⏳ that names a future clock time is a healthy wait,
    not a stall; the 2026-07-20 drilling incident)."""
    for entry in reversed(_iter_jsonl_tail(path)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        if entry.get("isApiErrorMessage") is True:
            return "", ""           # api-error → job 1's domain, not a marker
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue                # synthetic / tool-only — keep scanning back
        nonblank = [ln for ln in text.splitlines() if ln.strip()]
        for ln in reversed(nonblank[-3:]):     # marker line, tolerating ≤2 trailing lines
            m = _MARKER_RX.match(ln)
            if m:
                return m.group(1), ln
        return "", ""               # a real reply, but with no status marker
    return "", ""


def subagent_active(transcript_path, now, window):
    """True if a SUBAGENT transcript of this session was written within `window`
    seconds — a dispatched worker / workflow is live, so the parent's `⏳ WORKING`
    idle is HEALTHY waiting, not a stall. Subagent transcripts live at
    <session-dir>/<session-id>/subagents/**/*.jsonl (confirmed on disk). This is the
    one POSITIVE-liveness signal the external poller has; without it, idle-after-`⏳`
    is indistinguishable from a healthy subagent run, so job 4 would false-fire on
    every autopilot/workflow dispatch. Fail-safe True is NOT assumed — a missing dir
    returns False (no subagent), which only ALLOWS a ping (never a keystroke)."""
    try:
        d = Path(transcript_path).parent / Path(transcript_path).stem / "subagents"
        if not d.is_dir():
            return False
        for p in d.rglob("*.jsonl"):
            try:
                if (now - p.stat().st_mtime) <= window:
                    return True
            except OSError:
                continue
        return False
    except Exception:
        return False


def newest_subagent_transcript(transcript_path):
    """Path to the most-recently-written subagents/**/*.jsonl for this session, or
    None if there is no subagents/ dir or it holds no transcripts (issue #6). Lets
    jobs 1/4a apply their OWN detectors (transcript_last_error /
    transcript_text_toolcall_stall) to a BACKGROUND WORKER's own transcript, not
    just the supervisor's — a worker that dies on an api-error or a text-emitted
    tool-call is otherwise invisible until job 4's indirect ~30-min
    subagent_active() staleness path (and worse: a FRESH worker write is exactly
    what makes job 4a's `not subagent_active(...)` gate skip the supervisor check,
    since a fresh subagent write normally reads as healthy activity)."""
    try:
        d = Path(transcript_path).parent / Path(transcript_path).stem / "subagents"
        if not d.is_dir():
            return None
        newest, newest_m = None, -1.0
        for p in d.rglob("*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > newest_m:
                newest, newest_m = p, m
        return newest
    except Exception:
        return None


# Jobs 1b / 4a-sub (subagent api-error / text-toolcall-stall detectors) apply
# to whatever `newest_subagent_transcript` returns — which is always the MOST
# RECENTLY WRITTEN file under subagents/, even if that file itself is hours
# or days old. Without an upper age bound, a single historical dying-worker
# file (its frozen last-entry an old api-error or stuck text-toolcall) would
# nudge/escalate FOREVER, on every poll, indefinitely — even once the
# supervisor session has long since reported `✅ DONE`. Both detectors also
# gate on the SUPERVISOR's own last marker being `⏳ WORKING` (see their call
# sites) — the marker gate handles "the supervisor moved on"; this ceiling is
# the independent backstop for "the file itself is simply too old to still
# be about a CURRENT dispatch", even for a supervisor that is genuinely still
# `⏳ WORKING` on something else entirely.
SUBAGENT_MAX_AGE_SECONDS = 2 * 3600


# A tool-call opening the model emitted as TEXT — `<invoke name="...">` / `<invoke
# name="...">` — instead of a structured tool_use. Used by job 4a.
_TEXTCALL_RX = re.compile(r"<\s*(?:antml:)?invoke\b[^>]*\bname\s*=", re.I)
# A COMPLETE tool-call block — `<invoke name=...>` + zero-or-more `<parameter>` +
# closing `</invoke>` — anchored to END of string (re.S so a parameter VALUE may
# span newlines / contain status glyphs). Used to verify a message ENDS with exactly
# one clean call block (the signature of a tool call emitted as text and never run),
# which a meta-conversation that merely quotes `<invoke>` and then continues never is.
_TOOLCALL_BLOCK_RX = re.compile(
    r"<\s*(?:antml:)?invoke\b[^>]*>"
    r"(?:\s*<\s*(?:antml:)?parameter\b[^>]*>.*?</\s*(?:antml:)?parameter\s*>)*"
    r"\s*</\s*(?:antml:)?invoke\s*>\s*\Z",
    re.I | re.S)
# Markup (a parameter / nested invoke) that may legitimately follow an UNCLOSED open
# tag in a turn cut off mid-call — distinct from prose (which means a discussion).
_TOOLCALL_MARKUP_AFTER_RX = re.compile(r"<\s*(?:antml:)?(?:parameter|invoke)\b", re.I)


def _entry_has_tool_use(entry):
    """True if an assistant transcript entry carries a parsed `tool_use` content
    block — i.e. the harness DID call a tool, so this entry is NOT a text-toolcall
    stall (the malformed-as-text case has only `text` blocks)."""
    msg = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(msg, dict):
        return False
    c = msg.get("content")
    if isinstance(c, list):
        return any(isinstance(x, dict) and x.get("type") == "tool_use" for x in c)
    return False


def _ends_with_toolcall(text):
    """True iff `text` ENDS with tool-call markup — the signature of a tool call the
    model emitted as TEXT (the harness never ran it; the turn died right after
    emitting it, so nothing follows). This is the precision guard: a message that
    merely MENTIONS `<invoke>` and then continues with prose, a status marker, or a
    closing code fence does NOT end with the markup, so it does NOT match (the
    airuleset repo — and a review/completion-report session about THIS feature — is
    full of such mentions and must never be nudged)."""
    s = (text or "").rstrip()
    last = None
    for last in _TEXTCALL_RX.finditer(s):
        pass                                # last = the FINAL `<invoke name=` (or None)
    if last is None:
        return False
    # A QUOTED example block (markdown code fence or blockquote) is not a real
    # emitted call — reject it. A real model-emitted call is raw output, never inside
    # a fence or a `> ` blockquote. (We do NOT require the tag at column 0: a real
    # stall can have a same-line prose lead-in, e.g. camera-box's `court <invoke…>`.)
    line_start = s.rfind("\n", 0, last.start()) + 1
    if s[line_start:last.start()].lstrip().startswith(">"):
        return False                        # markdown blockquote → quoted example
    if s[:last.start()].count("```") % 2 == 1:
        return False                        # inside an open ``` code fence → quoted example
    # NOTE — accepted residual (per the user's job-4 over-nudge policy): a marker-LESS
    # message whose final content is a bare, unfenced, unquoted `<invoke>…</invoke>`
    # block is textually identical to a real stall, so it returns True. In practice the
    # hook-enforced status-marker convention means a compliant turn ends with ⏳/✅/❓
    # (prose after the block → False), and the worst case is one benign `stuck-check`
    # keystroke the session answers "not stuck" — exactly the residual job 4 accepts.
    tail = s[last.start():]                 # from the last opening to end-of-message
    if _TOOLCALL_BLOCK_RX.match(tail):
        return True                         # closed form: the tail IS exactly one call block
    if re.search(r"</\s*(?:antml:)?invoke", tail, re.I):
        return False                        # a close exists but the tail isn't a clean block → prose around it
    # unclosed: the turn was cut off mid-call. Accept only if no PROSE follows the
    # opening tag (a real cut-off ends inside the opening tag, at its '>', or inside a
    # following <parameter> — never in a natural-language sentence).
    gt = tail.find(">")
    if gt == -1:
        return True                         # truncated inside the opening tag itself
    rest = tail[gt + 1:].lstrip()
    return rest == "" or bool(_TOOLCALL_MARKUP_AFTER_RX.match(rest))


def transcript_text_toolcall_stall(path):
    """True iff the session's last real turn emitted a tool call as TEXT (failed
    parse → turn ended → idle). High precision so a conversation that merely
    DISCUSSES `<invoke>` markup (this very repo documents it) does NOT match:

      - the last NON-system entry must be an assistant message (a trailing
        user / tool_result entry means the conversation progressed → not stalled);
      - it is not an api-error (job 1 owns those);
      - it carries NO parsed tool_use block — checked BEFORE the empty/sentinel skip,
        because a pure tool_use entry has empty text (`"" in _SENTINELS`) and would
        otherwise be skipped and let the scan walk back to an older message;
      - its text ENDS with the tool-call markup (`_ends_with_toolcall`), not merely
        mentions it mid-prose / in a code fence.

    Scans more than the default tail window so a stall buried under a burst of
    trailing hook/system entries is not missed.
    """
    for entry in reversed(_iter_jsonl_tail(path, max_lines=200)):
        if not isinstance(entry, dict):
            continue
        t = entry.get("type")
        if t == "system":
            continue                        # hook / system noise after the turn — skip
        if t != "assistant":
            return False                    # user / tool_result tail → progressed, not stalled
        if entry.get("isApiErrorMessage") is True:
            return False                    # api-error → job 1's domain
        if _entry_has_tool_use(entry):
            return False                    # a real tool_use (incl. in-flight) → not a text-stall
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue                        # synthetic / tool-only text — keep scanning back
        return _ends_with_toolcall(text)
    return False


def _hash(text):
    return hashlib.sha1((text or "").strip().encode("utf-8", "replace")).hexdigest()[:12]


# Generic checkout-dir basenames that carry no project identity on their own — when
# the cwd ends in one, the label uses parent/base (e.g. .../bakerion-ai/repo →
# "bakerion-ai/repo") so the ping names a recognisable project, not "repo".
_GENERIC_DIRS = {"repo", "src", "app", "code", "main", "checkout", "work", "dist"}


def _stream_user():
    """The box's unix user when it IS a stream identity (gatekeeper / montalu /
    david / marek boxes), '' on the personal boxes. Appended to ping labels so
    the phone can tell WHICH stream speaks (user complaint 2026-07-20: every
    stream pinged as bare 'odoo-erp')."""
    try:
        import getpass
        u = getpass.getuser()
    except Exception:
        return ""
    return "" if u in ("newlevel", "root", "") else u


def project_label(cwd):
    parts = [p for p in str(cwd).rstrip("/").split("/") if p]
    if not parts:
        label = "unknown"
    elif parts[-1].lower() in _GENERIC_DIRS and len(parts) >= 2:
        label = parts[-2] + "/" + parts[-1]
    else:
        label = parts[-1]
    u = _stream_user()
    return label + "-" + u if u else label


# A subscription / quota USAGE cap is time-based — `continue` cannot fix it (only
# the reset clock can), so it is classified separately and only PINGED, never
# nudged. Kept narrow so a transient 529 / "rate limited" / overloaded (which a
# retry CAN clear) is NOT caught here and still gets the 3×continue lifecycle.
_USAGE_CAP_RX = re.compile(
    r"usage limit|quota|limit (?:reached|will reset|resets)|reset at|reached your"
    r"|hit your (?:session|usage) limit", re.I)
# Transient SERVER-side throttles — a retry / `continue` CAN clear these, so they
# must NOT be read as a quota cap. Checked FIRST. Critically this catches
# "(not your usage limit)" — Claude Code's transient rate-limit banner literally
# CONTAINS the words "usage limit", which would otherwise false-match above.
_TRANSIENT_RX = re.compile(
    r"not your usage limit|temporarily limiting|rate.?limit|overloaded|\b529\b|try again", re.I)


def is_usage_cap(text):
    """True ONLY for a real subscription/quota cap (time-based → `continue` can't
    fix it → ping only). A transient server throttle returns False so it still gets
    the 3×`continue` lifecycle."""
    if not text or _TRANSIENT_RX.search(text):
        return False
    return bool(_USAGE_CAP_RX.search(text))


# A Claude Code INTERACTIVE PROMPT footer — present only while a selection dialog
# (AskUserQuestion), a permission request, or a plan approval is OPEN and waiting
# for the human. Used for a NOTIFICATION ONLY (never to send keys), so a loose
# match is safe: a false ping is harmless. (The api-error ACTION path stays strict
# / flag-only precisely because it injects keystrokes.)
_WAITING_RX = re.compile(
    r"Tab/Arrow keys to navigate|Enter to select|Do you want to proceed", re.I)
# A menu SELECTION pointer: `❯ 1. Yes` (CC numbers its options). Distinguishes an OPEN
# numbered menu (still waiting) from a FREE `❯ <typed text>` input prompt (not waiting).
_MENU_POINTER_RX = re.compile(r"❯ \d+\.")


def _is_border_rule(s):
    """A box border / horizontal rule line (`╭────╮`, `─── labelled ok ───`). `s` is
    already stripped. Split out of `_is_bottom_chrome` because `pane_question_excerpt`
    needs borders as BOUNDARY MARKERS (they delimit the dialog box) while the other
    chrome rows (agent strip, statusline) are plain drops."""
    bars = sum(c in "─—━═╌╍┄┅┈┉╭╮╰╯┌┐└┘│┃" for c in s)
    return bars >= 4 and bars >= len(s.replace(" ", "")) - 12


def _is_bottom_chrome(s):
    """A trailing 'chrome' line rendered BELOW the input box: the agent strip (`● main`
    + one `◯ <agent>` row PER concurrent subagent — including a SELECTED row, which
    renders `❯ ● main` / `❯ ◯ <agent>` instead, per issue #36), the strip's selector
    hint (`↑/↓ to select · Enter to view`), the mode hint (`⏵⏵ …`), the `ctx …`
    footer statusline, or a horizontal border rule. Their count is VARIABLE — the agent
    strip grows one row per running subagent — so these MUST be stripped from the bottom
    before locating the `❯` prompt. `s` is already stripped."""
    if not s:
        return True
    if s[0] in "●◯":                                    # agent-strip rows
        return True
    if s.startswith("❯ ●") or s.startswith("❯ ◯"):       # a SELECTED strip row
        return True
    if s.startswith("↑/↓") or ("to select" in s and "Enter to view" in s):
        return True                                     # the strip's selector hint
    if s.startswith("⏵⏵"):                              # bypass / mode hint
        return True
    if s.startswith("ctx "):                            # footer statusline
        return True
    if _is_border_rule(s):                              # a box border / rule (labelled ok)
        return True
    return False


# Box-drawing glyphs a Claude Code input box renders as its own top/bottom
# border. Shares its vocabulary with `_is_border_rule` (which ALSO accepts a
# LABELLED rule for bounding a dialog's question) but `_is_separator_line`
# below is strict: every non-space char must be one of these, so a line of
# prose is never mistaken for the box's own edge.
_SEP_CHARS = set("─—━═╌╍┄┅┈┉╭╮╰╯┌┐└┘│┃")


def _is_separator_line(s):
    """A pure box-border row (`──────────`) — the input box's own top/bottom
    edge in the `separator / ❯ <draft> / separator` structure `_find_boundary_line`
    searches for (issue #46). `s` is already stripped. A narrow `capture-pane`
    can truncate the row to fewer repeated characters — still accepted (a
    length threshold, not a fixed rule width)."""
    if not s:
        return False
    core = s.replace(" ", "")
    return len(core) >= 3 and all(c in _SEP_CHARS for c in core)


_QUEUED_PLACEHOLDER_TEXT = "press up to edit queued messages"


def _find_boundary_line(captured):
    """Locate the pane's INPUT-BOX boundary line — the row `_has_free_prompt`
    and `_input_line_text` both test. Two strategies, issue #46:

    1. STRUCTURAL (tried first). The input box always renders as
       `separator / ❯ <draft> / separator`. Find the LAST pair of separator
       lines in the capture and take the line immediately above the second
       (bottom) one. This is immune to whatever Claude Code renders BELOW
       the box — the agent strip, the statusline, or any UI element never
       seen before (the live `⧉  <project>` row that made job 12/14/15 and
       Discord-reply delivery mislabel a drafting pane "busy", 2026-07-25,
       dev2 marek-1:5.0 — the second occurrence of this class after #36). A
       multi-row WRAPPED draft still resolves correctly: the last content
       row directly above the bottom separator is its TAIL, the same
       convention the pre-#46 peel already used (callers match with
       `endswith()` for exactly this reason).

    2. GLYPH-BASED FALLBACK (pre-#46 behavior, unchanged). When no separator
       pair is found — many real captures, and most of this file's older
       fixtures, render the box borderless — peel the VARIABLE-height
       trailing chrome via `_is_bottom_chrome` (agent strip + statusline +
       mode hint + border rules) and take the first non-chrome line up from
       the bottom. An UNRECOGNIZED chrome shape below the box still stops
       this scan early; that known limitation is exactly why strategy 1 is
       tried first, and this fallback exists only so nothing regresses for
       captures that never had a border to find.

    We must NOT scan a multi-line window above the boundary in EITHER
    strategy: during a running foreground turn the boundary line IS the
    spinner, and the transcript above it can contain a lone `❯` (the #233
    scar) — a window reaching up into that transcript would call a BUSY pane
    idle and INTERRUPT it. So the boundary is always exactly one line.

    A boundary line showing CC's greyed `Press up to edit queued messages`
    HINT (an otherwise-EMPTY box, recallable via the Up arrow — never text
    the user typed) is normalized to a bare `❯` before returning (#65
    acceptance: this placeholder is never mistaken for a real draft by any
    caller — `_has_free_prompt`, `_input_line_text`, `_classify_boundary`
    all resolve through this one function).

    Returns the raw (stripped) boundary line, or None if NEITHER strategy
    locates one at all (e.g. the whole capture is chrome, or it's empty)."""
    line = _find_boundary_line_raw(captured)
    if line is not None and line.startswith("❯"):
        if line[1:].strip().lower() == _QUEUED_PLACEHOLDER_TEXT:
            return "❯"
    return line


def _find_boundary_line_raw(captured):
    """The two-strategy scan `_find_boundary_line` normalizes — see its
    docstring for the full rationale. Split out so the queued-placeholder
    normalization has exactly ONE place to apply, regardless of which
    strategy located the boundary."""
    if not captured:
        return None
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    if not lines:
        return None

    seps = [i for i, ln in enumerate(lines) if _is_separator_line(ln)]
    if len(seps) >= 2:
        idx_b = seps[-1]
        earlier = [i for i in seps if i < idx_b]
        if earlier:
            content = lines[earlier[-1] + 1:idx_b]
            if content:
                return content[-1]

    i, n = len(lines), 0
    while i > 0 and _is_bottom_chrome(lines[i - 1]) and n < 40:
        i -= 1
        n += 1
    if i <= 0:
        return None
    return lines[i - 1]


def _has_free_prompt(captured, bare_only=False):
    """True if the pane shows a FREE `❯` input prompt at the bottom — the session is IDLE
    at the prompt, NOT running a foreground turn (which replaces the input box with a
    spinner / "esc to interrupt" and shows NO input `❯`).

    The boundary line is located by `_find_boundary_line` (structural
    separator-pair search first, glyph-based chrome peel as fallback — see
    its docstring, issue #46). Chrome-stripping already absorbs the whole
    agent strip, so a genuinely idle `⏳ WORKING` session with N background
    workers still lands its `❯` exactly at the boundary regardless of N, and
    an unrecognized row below a bordered box no longer hides it at all.

    bare_only=True (the TYPING gate, `pane_at_idle_prompt`): require a BARE `❯` (empty input
    box). If the user has typed text (`❯ blah`) we must NOT type over it. bare_only=False
    (the inverse used by `pane_waiting_on_user`): `❯ <typed text>` still counts as "at a
    prompt, not blocked". A menu pointer `❯ <digit>.` is never a free prompt (open dialog)."""
    s = _find_boundary_line(captured)
    if s is None:
        return False
    if s == "❯":
        return True
    if not bare_only and s.startswith("❯ ") and not _MENU_POINTER_RX.match(s):
        return True
    return False


def pane_waiting_on_user(captured):
    # A LIVE blocking dialog (AskUserQuestion / permission / plan approval) occupies
    # the input area — there is NO free `❯` input-prompt line at the bottom. A CLOSED
    # dialog can leave its footer text on screen while the session sits at the normal
    # `❯` prompt (idle) or works past it — that is NOT waiting, and matching the loose
    # footer regex anywhere in the pane false-pinged "čaká na teba" (bypass-permissions
    # flashes + AskUserQuestions that auto-continue after ~60s). So require the footer
    # AND the absence of a bottom `❯` input prompt (the persistence gate in run_once
    # adds the second guard: the footer must survive ≥2 polls before it pings).
    if not captured or not _WAITING_RX.search(captured):
        return False
    return not _has_free_prompt(captured)


_OPTION_ROW_RX = re.compile(r"^(?:❯\s*)?\d+\.\s+\S")
# Navigation-help footer ONLY — deliberately NARROWER than _WAITING_RX, whose
# "Do you want to proceed" alternative IS the question of a permission dialog
# and must stay in the excerpt.
_DIALOG_HELP_RX = re.compile(r"Tab/Arrow keys to navigate|Enter to select", re.I)
# Dialog UI AFFORDANCES the fullscreen renderer (CC 2.1.20x) appends below the
# real options ("4. Type something." / "5. Chat about this", often past a border
# rule) — they are chrome, not options, and anchoring on them shipped a phone
# ping whose entire "question" was "Chat about this" (david@gk, 2026-07-09).
_DIALOG_UI_ROW_RX = re.compile(
    r"^(?:❯\s*)?(?:\d+\.\s+)?(?:Type something\.?|Chat about this)\s*$", re.I)
# The dialog's FIRST option row — the anchor for the question walk.
_FIRST_OPTION_RX = re.compile(r"^(?:❯\s*)?1\.\s+\S")


def pane_question_excerpt(captured, max_chars=900):
    """Extract the OPEN dialog's question + options from a captured pane, so the job-2
    "čaká na teba" ping carries WHAT is being asked — the user's explicit complaint was
    pings saying only "a question is waiting" with no question in them (2026-07-04).

    A blocking dialog (AskUserQuestion / permission / plan approval) renders as
    question text, then numbered option rows (`❯ 1. …` / `  2. …`) — since CC
    2.1.20x (fullscreen renderer) with WRAPPED description lines interleaved and
    UI affordance rows appended below — then the help footer. Strategy: strip
    box edges / help footer / UI affordances, anchor on the dialog's FIRST
    option row (`1. …`) nearest the bottom, take up to 6 text lines directly
    above it (bounded by a border rule or a ● bullet, so we never reach past the
    dialog into transcript prose) as the question, and every numbered option row
    from the anchor down as the options (descriptions between them are skipped —
    they'd blow the cap). Anchoring on the LAST numbered row instead picked the
    "Chat about this" affordance and lost the question (david@gk, 2026-07-09).
    Returns "" when no options block is visible — the caller falls back to the
    generic text. Read-only (feeds a NOTIFICATION only, never a keystroke), so a
    slightly messy excerpt is harmless; missing it entirely is the failure."""
    if not captured:
        return ""
    rows = []                               # (text, is_border_marker)
    for raw in captured.splitlines():
        s = raw.strip()
        if not s:
            continue
        if _is_border_rule(s):              # dialog edge → keep as a boundary marker
            rows.append(("", True))
            continue
        inner = s.strip("│┃║").strip()      # peel the box's vertical edges
        if not inner or _DIALOG_HELP_RX.search(inner):
            continue                        # empty in-box line / navigation help footer
        if inner[0] in "●◯":
            # A transcript bullet / agent-strip row is a BLOCK BOUNDARY, not a
            # plain drop: the common AskUserQuestion dialog renders BORDERLESS
            # with `● Claude asked:` as its top, and without this marker the
            # question walk climbed past it into transcript prose (review
            # finding, 2026-07-04).
            rows.append(("", True))
            continue
        if inner.startswith("⏵⏵") or inner.startswith("ctx "):
            continue                        # mode hint / statusline
        if _DIALOG_UI_ROW_RX.match(inner):
            continue                        # renderer affordance, never an option
        rows.append((inner, False))
    anchor = None
    for i in range(len(rows) - 1, -1, -1):  # dialog's `1. …` nearest the bottom
        if not rows[i][1] and _FIRST_OPTION_RX.match(rows[i][0]):
            anchor = i
            break
    if anchor is None:
        return ""
    question = []
    j = anchor - 1
    while j >= 0 and not rows[j][1] and len(question) < 6:
        question.insert(0, rows[j][0])
        j -= 1
    options = [r[0] for r in rows[anchor:]
               if not r[1] and _OPTION_ROW_RX.match(r[0])]
    out = " · ".join(question + options)
    if len(out) > max_chars:
        out = out[:max_chars - 1] + "…"
    return out


def pane_at_idle_prompt(captured):
    """True if the pane is IDLE at a free `❯` prompt — safe to type a self-check nudge.

    Job 4 / 4a REQUIRE this before sending a keystroke. A FOREGROUND subagent (a
    ticket-validator, a Task/Agent dispatch) BLOCKS the parent, so the parent transcript
    FREEZES and looks idle (`⏳ WORKING`, 30 min stale) while the session is very much
    ALIVE — and the pane shows the agent running with NO free `❯` prompt. Typing there
    does not land at a prompt, it INTERRUPTS the running agent (the observed "Agent
    Validate issue #233 finished · Interrupted" incident). Requiring a free `❯` at the
    bottom means we only ever type into a genuinely idle session (turn ended, waiting on
    a background job / input) — never into one blocked on live foreground work. The
    BACKGROUND-subagent case (main idle at `❯` while an autopilot-worker runs) still
    shows a free `❯`, so it passes THIS gate but is caught by `subagent_active`.

    Requires a BARE `❯` (empty input box): a session with USER-TYPED but unsubmitted text
    (`❯ blah`) means the user is present and interacting — not a silent stall — and we
    must not type over their input, so bare_only=True."""
    return _has_free_prompt(captured, bare_only=True)


# --- 5-HOUR SESSION LIMIT (a distinct, TIME-BASED cap) --------------------------
# Claude Code's session-limit banner shows in the PANE, e.g.
#   "You've hit your session limit · resets 6:10pm (Europe/Prague)"
#   "/usage-credits to finish what you're working on."
# It is NOT a transient 529 and NOT reliably an `isApiErrorMessage` transcript
# entry — it lives on screen. Unlike a server throttle, `continue` BEFORE the
# reset is a no-op that just re-hits the limit (the incident: repeated `continue`
# → "You've hit your session limit"). So job (6) reads it from the PANE, PINGS
# ONCE with the reset time, does NOTHING until the reset clock, then sends ONE
# `continue` AFTER it — never before.
_SESSION_LIMIT_RX = re.compile(
    r"hit your (?:session|usage) limit|/usage-credits to finish", re.I)
# "resets 6:10pm" / "resets 6pm" / "resets at 18:10" — capture the clock.
_RESET_TIME_RX = re.compile(
    r"reset(?:s|ting)?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", re.I)
# The tz the banner names — "(Europe/Prague)" OR a bare zone word like
# "(UTC)"/"(GMT)". Broadened from Area/City-only after the gk incident
# (2026-07-24): the gk box runs UTC, its banner reads "resets 4:40pm (UTC)",
# and the narrower Area/City-only regex never matched it — silently falling
# through to the Europe/Bratislava default and computing a reset epoch 2h
# EARLY (a nonsense past reset time on the Discord ping).
_RESET_TZ_RX = re.compile(r"\(([A-Za-z]+(?:/[A-Za-z_]+)?)\)")
# Job 6's bounded post-reset resume retry (FIX C, gk incident 2026-07-24) —
# see the `elif ra and now >= ra:` branch in run_once for the full story.
SESSLIMIT_RETRY_S = 5 * 60
SESSLIMIT_MAX_TRIES = 4


def pane_session_limited(captured):
    """True if the pane's BOTTOM shows Claude Code's 5-hour session-limit
    banner — scoped to the last 10 lines of the region ABOVE the input box
    (falling back to the raw capture's last 10 lines when no input box is
    located at all, e.g. a busy/spinner pane with no `❯` boundary).

    A dead BACKGROUND WORKER can leave a `⎿ You've hit your session limit …`
    ECHO line sitting HIGH in the transcript output, with many later
    `● pokracujem v praci`-style lines scrolling underneath it for hours — a
    whole-capture search kept the episode "limited" long after a real resume
    already happened (gk incident 2026-07-24). Bottom-scoping means only a
    banner that is still the FRESHEST thing on screen counts."""
    if not captured:
        return False
    region = _above_input_box(captured)
    lines = [ln for ln in region.splitlines() if ln.strip()]
    if not lines:
        lines = [ln for ln in captured.splitlines() if ln.strip()]
    return bool(_SESSION_LIMIT_RX.search("\n".join(lines[-10:])))


def parse_reset_epoch(captured, now):
    """Parse 'resets <clock>' from the banner into an epoch >= now, or None.
    The clock is read in the tz the banner names: "UTC"/"GMT" literally, an
    "Area/City" name via ZoneInfo, any other bare parenthesized word (e.g. a
    stray "(debug)" elsewhere in the pane) falls back to the Europe/
    Bratislava default (same offset as Prague) — and rolled to tomorrow if
    already past. The tz is searched ONLY in the ~80 chars starting at the
    TIME match, never the whole capture: a global search would hijack on
    ANY parenthesized word anywhere in the pane, however far from the clock
    (gk incident 2026-07-24). Fail-safe: any parse/tz error returns None
    (job 6 then pings but cannot auto-resume — the user handles it)."""
    try:
        m = _RESET_TIME_RX.search(captured or "")
        if not m:
            return None
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        ap = (m.group(3) or "").lower()
        if ap == "pm" and hh != 12:
            hh += 12
        elif ap == "am" and hh == 12:
            hh = 0
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        from datetime import datetime, timedelta
        tz = None
        try:
            from zoneinfo import ZoneInfo
            seg = (captured or "")[m.start():m.start() + 80]
            tzm = _RESET_TZ_RX.search(seg)
            if tzm:
                name = tzm.group(1)
                if name in ("UTC", "GMT"):
                    tz = ZoneInfo("UTC")
                elif "/" in name:
                    tz = ZoneInfo(name)
                else:
                    tz = ZoneInfo("Europe/Bratislava")
            else:
                tz = ZoneInfo("Europe/Bratislava")
        except Exception:
            tz = None
        base = datetime.fromtimestamp(now, tz)
        target = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        ts = target.timestamp()
        # The 5-hour reset window is short. A clock only SLIGHTLY in the past means
        # the reset just happened (or the banner is momentarily stale) → resume NOW,
        # don't wait a whole day. Only a clock > 6h in the past is really a next-day
        # time (e.g. a late-night "resets 12:10am" seen at 23:50) → roll to tomorrow.
        if ts <= now - 6 * 3600:
            ts = (target + timedelta(days=1)).timestamp()
        return ts
    except Exception:
        return None


def _human_clock(epoch):
    """Epoch → 'HH:MM' in Europe/Bratislava, for the ping text."""
    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Europe/Bratislava")
        except Exception:
            tz = None
        return datetime.fromtimestamp(epoch, tz).strftime("%H:%M")
    except Exception:
        return "?"


def decide(state, key, err_hash, now, grace=GRACE_SECONDS,
           interval=RETRY_INTERVAL_SECONDS, max_nudges=MAX_NUDGES, first_seen_seed=None):
    """Pure decision for ONE stalled session. Returns (action, entry) where action
    is 'nudge' | 'wait' | 'escalate' | 'noop'. `entry` is the updated state record
    (caller persists state[key] = entry).

    The grace is tracked HERE, from `first_seen` (the moment the session's last
    reply became an api-error), NOT from transcript mtime — Claude Code's own
    retries + queue/snapshot writes keep touching the transcript, so an mtime-idle
    gate never trips for a rate-limited session (that bug left `presenter`
    unnudged). On first sighting `first_seen = first_seen_seed` (the caller seeds it
    with `now - idle` so an already-stale stall counts from when it really began);
    if that is already >= grace old the first `continue` goes out NOW, else we
    `wait` and let Claude Code recover on its own for `grace` first. Thereafter a
    nudge fires every `interval`; after `max_nudges` it escalates once, then noops.
    A different err_hash (a new error) restarts the cycle."""
    e = state.get(key)
    if e is None or e.get("hash") != err_hash:
        fs = int(first_seen_seed) if first_seen_seed is not None else int(now)
        entry = {"hash": err_hash, "first_seen": fs, "nudges": [], "escalated": False}
        if (now - fs) >= grace:           # already stuck >= grace → first continue now
            entry["nudges"] = [int(now)]
            return "nudge", entry
        return "wait", entry              # fresh → give Claude Code `grace` to recover
    if e.get("escalated"):
        return "noop", e
    nudges = list(e.get("nudges", []))
    last = nudges[-1] if nudges else e.get("first_seen", now)
    needed = grace if not nudges else interval
    if (now - last) < needed:
        return "wait", e
    if len(nudges) >= max_nudges:
        e2 = dict(e)
        e2["escalated"] = True
        return "escalate", e2
    e2 = dict(e)
    e2["nudges"] = nudges + [int(now)]
    return "nudge", e2


# --- Stuck-check sensitivity (2026-07-20, codex-bridge drilling incident) ----
# A session honestly waiting on a SCHEDULED event ("čakám na 14:15 auto-sync")
# got nudged every cycle until pressured into premature work. Two valves:
# declared_wait_until() (respect an explicit future clock in the ⏳ marker) and
# the responded-backoff in decide_working (answered nudges space out
# exponentially and never escalate — escalation is for a DEAD process only).
DECLARED_WAIT_GRACE_S = 20 * 60      # nudge only this long AFTER the declared time
DECLARED_WAIT_MAX_S = 12 * 3600      # a "future" time further than this is noise
NUDGE_BACKOFF_CAP_S = 4 * 3600       # answered-nudge spacing cap
_CLOCK_RX = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def declared_wait_until(marker_line, now, tz="Europe/Bratislava"):
    """Epoch until which the ⏳ marker line's DECLARED future clock time
    suppresses the stuck-check (latest declared time + grace), or 0 when the
    line names no usable future time. A time already past resolves to its next
    occurrence; anything further than DECLARED_WAIT_MAX_S away is ignored
    (a mentioned historical time, not a wait declaration)."""
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        local = datetime.fromtimestamp(now, ZoneInfo(tz))
    except Exception:
        local = datetime.fromtimestamp(now)
    best = 0.0
    for m in _CLOCK_RX.finditer(str(marker_line or "")):
        h, mi = int(m.group(1)), int(m.group(2))
        cand = local.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand.timestamp() <= now:
            cand += timedelta(days=1)
        delta = cand.timestamp() - now
        if 0 < delta <= DECLARED_WAIT_MAX_S:
            best = max(best, cand.timestamp())
    return best + DECLARED_WAIT_GRACE_S if best else 0


def decide_working(state, wkey, now, idle, interval=WORKING_RETRY_INTERVAL_SECONDS,
                   max_nudges=MAX_WORKING_NUDGES, responded=False,
                   backoff_base=STALL_WORKING_SECONDS,
                   backoff_cap=NUDGE_BACKOFF_CAP_S):
    """Pure decision for ONE `⏳ WORKING`-stalled session (job 4). Returns
    (action, entry) where action is 'nudge' | 'wait' | 'escalate' | 'noop'; the
    caller persists state[wkey] = entry. Called ONLY after the caller has already
    confirmed `⏳` marker + idle >= threshold + no advancing subagent, so the FIRST
    sighting nudges immediately (the threshold IS the grace).

    Unlike job 1's `decide` (api-error, where CC keeps writing the transcript so the
    timer is state-based), a job-4 nudge that LANDS resets the transcript idle below
    the threshold — so the caller simply stops invoking this for that session and the
    episode is cleaned up by last_seen. We only get here AGAIN if the prior nudge
    produced no transcript write within `interval` (the Claude process is itself
    wedged), so a retry is the right escalation. After `max_nudges` no-response nudges
    it escalates ONCE (the single user-facing ping), then noops."""
    e = state.get(wkey)
    if e is None:
        e = {"first_seen": int(now - idle), "nudges": [], "escalated": False}
    e["last_seen"] = int(now)
    if e.get("escalated"):
        return "noop", e
    nudges = list(e.get("nudges", []))
    if not nudges:                         # first sighting past the threshold → nudge now
        e["nudges"] = [int(now)]
        return "nudge", e
    if responded:
        # The session ANSWERED the previous nudge — it is ALIVE, just waiting.
        # Space repeats out exponentially (30m→1h→2h→…, capped) and never let
        # answered checks count toward the 'wedged' escalation (the drilling
        # incident: 3 answered nudges fired a false wedged ping and the
        # session got pressured into premature work).
        answered = int(e.get("answered", 0)) + 1
        e["answered"] = answered
        e["noresp"] = 0
        gap_needed = min(backoff_base * (2 ** answered), backoff_cap)
        if (now - nudges[-1]) < gap_needed:
            return "wait", e
        e["nudges"] = nudges + [int(now)]
        return "nudge", e
    noresp = int(e.get("noresp", len(nudges)))
    if noresp >= max_nudges:               # MAX no-response nudges → give up, ping once
        e["escalated"] = True
        return "escalate", e
    if (now - nudges[-1]) >= interval:     # still wedged `interval` later → re-nudge
        e["nudges"] = nudges + [int(now)]
        e["noresp"] = noresp + 1
        return "nudge", e
    return "wait", e                       # within the retry interval → hold


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


def _proc_fingerprint(pid):
    """{"pid": str(pid), "starttime": <str>} for a LIVE process, read from
    `/proc/<pid>/stat` — or None if that file is unreadable (the process
    does not exist, or never did). `starttime` is field 22 of `stat` (the
    20th token after the comm-closing ") " — the same `rsplit(") ", 1)`
    trick `_pane_hosted_claude_pid` above already uses for `ppid`, field 4
    / `rest[1]`), included specifically to defeat PID RE-USE (#82): the
    kernel recycles PIDs, so "a process with this PID exists" alone is not
    proof it is the SAME process that originally received a queued
    command — a recycled PID would have a different `starttime`."""
    stat = _proc_read("/proc/%s/stat" % pid)
    if not stat:
        return None
    try:
        starttime = stat.rsplit(") ", 1)[-1].split()[19]
    except (IndexError, ValueError):
        return None
    return {"pid": str(pid), "starttime": starttime}


def _proc_fingerprint_alive(fp):
    """True/False/None for whether the stored fingerprint `fp`
    (`{"pid", "starttime"}`, from `_proc_fingerprint`) still identifies a
    LIVE process with the SAME starttime. None means "nothing recorded, or
    it can't be checked" — callers must treat that as "not proven dead"
    (never as FAILED), so a claim set before this fix existed, or one
    whose owning pane could not be resolved at queue time, is never
    wrongly declared failed just because it lacks a fingerprint."""
    if not fp or not fp.get("pid"):
        return None
    cur = _proc_fingerprint(fp["pid"])
    if cur is None:
        return False
    return cur.get("starttime") == fp.get("starttime")


def _pane_claude_proc_fingerprint(pane_id, run=None):
    """Fingerprint (`_proc_fingerprint`) of the `claude` process hosted by
    tmux pane `pane_id` — resolves the pane's own pid (`#{pane_pid}`) then
    walks its process tree via `_pane_hosted_claude_pid` (the SAME helper
    that already handles both a direct pane and a sudo/su-hosted stream
    session — the montalu-in-newlevel-tmux shape). None when the pane or
    its hosted claude process cannot be resolved at all — fail-safe: a
    claim recorded without a fingerprint simply never triggers the
    process-death FAILED check (#82)."""
    run = run or _default_run
    pane_pid = (run(["tmux", "display-message", "-p", "-t", pane_id,
                     "#{pane_pid}"]) or "").strip()
    if not pane_pid:
        return None
    claude_pid = _pane_hosted_claude_pid(pane_pid)
    if not claude_pid:
        return None
    return _proc_fingerprint(claude_pid)


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


def list_claude_panes(run=None):
    """[(pane_id, cwd)] for every tmux pane running `claude` — directly, or
    hosted under sudo/su (the montalu-in-newlevel-tmux stream shape) — deduped
    by pane_id (grouped sessions share the same pane_id)."""
    run = run or _default_run
    out = run(["tmux", "list-panes", "-a", "-F",
               "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"
               "\t#{pane_pid}"])
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
            cpid = _pane_hosted_claude_pid(ppid)
            if not cpid:
                continue
            cwd = _hosted_claude_cwd(cpid, cwd)
        seen.add(pid)
        res.append((pid, cwd))
    return res


def pane_in_mode(pane_id, run=None):
    """True if the pane is in tmux copy-mode / a modal (the user is scrolling, or a
    menu is open). Sending keys then would be swallowed or would corrupt the user's
    selection — so the watchdog skips such a pane this cycle (without burning a
    retry)."""
    run = run or _default_run
    out = run(["tmux", "display-message", "-p", "-t", pane_id, "#{pane_in_mode}"])
    return (out or "").strip() == "1"


def capture_pane(pane_id, run=None, lines=40):
    """Last `lines` of the pane's visible content. Used ONLY for the ping-only
    waiting-on-user detector — never for the api-error action trigger (that is
    flag-only, after the pane-text-fallback incident)."""
    run = run or _default_run
    return run(["tmux", "capture-pane", "-p", "-t", pane_id, "-S", "-%d" % lines])


def pane_owner(pane_id, run=None):
    """Lowercase tmux owner (zbynek / marek) of a SPECIFIC pane, so a ping about
    that pane @mentions the right person — the watchdog runs headless (systemd
    --user) with NO tmux context of its own, so it must resolve the owner from the
    waiting/stalled pane, not from itself. Matches notify.resolve_owner's
    normalization ('marek-12' → 'marek')."""
    run = run or _default_run
    for fmt in ("#{session_group}", "#S"):
        out = (run(["tmux", "display-message", "-p", "-t", pane_id, fmt]) or "").strip()
        if out:
            out = re.sub(r"-\d+$", "", out)
            return re.sub(r"[^a-z0-9]", "", out.lower())
    return ""


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


def send_continue(pane_id, text=NUDGE_TEXT, run=None):
    """Type `text` literally into the pane, then press Enter to submit it.

    Captures the pane FIRST (issue #36): if the agent-strip selector holds
    focus (`_strip_selected`), send ONE Escape before typing — otherwise the
    submit Enter can be swallowed as "view agent" instead of submitting our
    text. Best-effort only: we do NOT re-verify the Escape actually cleared
    the selection — proceed with the type + Enter regardless (today's
    behavior), since the retry paths (job 7's verify loop, job 10's machine
    submit) already Escape-and-retry on a swallowed submit. NEVER send a
    second Escape here — a rapid double-Escape into a pane holding a draft
    PERMANENTLY DELETES it (empirically confirmed, issue #35)."""
    run = run or _default_run
    captured = capture_pane(pane_id, run, lines=10)
    if _strip_selected(captured):
        run(["tmux", "send-keys", "-t", pane_id, "Escape"])
    run(["tmux", "send-keys", "-t", pane_id, "-l", text])
    run(["tmux", "send-keys", "-t", pane_id, "Enter"])


def send_selfcheck(pane_id, run=None):
    """Job 4's self-check nudge — the autonomous form of the user's manual 'stucked?'.
    Types WORKING_NUDGE_TEXT into the pane and submits it, prompting the session to
    verify the liveness of its launched work and intervene if it died silently."""
    send_continue(pane_id, WORKING_NUDGE_TEXT, run)


def send_subagent_nudge(pane_id, worker_id, kind, run=None):
    """(issue #6) Nudge the SUPERVISOR pane about a dying BACKGROUND WORKER —
    `kind` is a short human label ('api-error' or 'text-toolcall-stall'). Types a
    stuck-check-style self-check message naming the worker's own transcript file,
    so the supervisor — the only thing that can decide resume vs re-dispatch —
    investigates. Never acts on the worker's behalf directly."""
    text = ("stuck-check: background worker %s vyzerá mŕtvy (%s v subagents/%s.jsonl) "
            "— over jeho transcript a zasiahni (dispatchni znova alebo naň nadviaž), "
            "nič nerob naslepo." % (worker_id, kind, worker_id))
    send_continue(pane_id, text, run)


def _nudge_dying_subagent(state, logs, send_fn, pid, run, captured, project, owner,
                          now, sub_path, sub_idle, kind, dedup_prefix,
                          interval, max_nudges, dry_run):
    """(issue #6) Shared busy/idle nudge-or-ping logic for a detected dying SUBAGENT
    (jobs 1b / 4a-sub). `kind` is a human label for the nudge/ping text; `dedup_prefix`
    namespaces the state/dedup keys per detector ('apierr' / 'textcall'). Mutates
    `state` and `logs` in place. Same keystroke discipline as every other job: NEVER
    type into a copy-mode or busy (no free `❯`) pane — ping instead, mirroring job 4's
    busy-pane-wedged path — and reuse decide_working's nudge → retry → escalate
    lifecycle for the idle-pane case, so a wedged supervisor still only pings once."""
    wid = sub_path.stem
    if pane_in_mode(pid, run):
        logs.append("skip in-mode (subagent-%s) %s" % (dedup_prefix, project or pid))
        return
    if not pane_at_idle_prompt(captured):
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
    wkey = "subagent-%s:%s" % (dedup_prefix, wid)
    action, entry = decide_working(state, wkey, now, sub_idle,
                                   interval=interval, max_nudges=max_nudges)
    state[wkey] = entry
    if action == "nudge":
        n = len(entry["nudges"])
        logs.append("subagent-%s-nudge#%d %s [%s]" % (dedup_prefix, n, project, wid))
        if not dry_run:
            send_subagent_nudge(pid, wid, kind, run)
    elif action == "escalate":
        logs.append("subagent-%s-escalate %s [%s] — gave up after %d nudges"
                    % (dedup_prefix, project, wid, max_nudges))
        send_fn("\U0001f6d1 **%s** — background worker `%s` (%s) a session nereaguje na "
                "nudge\n> Treba zásah." % (project, wid, kind),
                owner=owner, dedup_key="subagent-%s-giveup:%s" % (dedup_prefix, wid),
                dry_run=dry_run)
    else:
        logs.append("subagent-%s-%s %s [%s]" % (dedup_prefix, action, project, wid))


# --------------------------------------------------------------------------- #
# Job 7 — Discord REPLY → the Claude session that asked the ❓.
#
# When a ❓ ping is delivered, notify.record_question() maps the ping's Discord
# message id → the asking session. The user answers by REPLYING to that ping in
# Discord; this job reads recent messages in the notification thread(s), matches
# a reply's referenced message id against the local question map, and types the
# answer into that exact session's tmux pane. SECURITY: a reply is actioned ONLY
# when (a) its author id is a KNOWN OWNER of this machine (notify.known_owner_ids
# — the DISCORD_MENTION_* set), (b) it explicitly REPLIES to a ❓ ping THIS
# machine sent (in the local map), and (c) the target pane is IDLE at a free `❯`
# (pane_at_idle_prompt — never inject into a running turn, the #233 invariant).
# --------------------------------------------------------------------------- #

DISCORD_REPLY_MAX_CHARS = 1500       # cap a typed answer (Discord msgs ≤ 2000)
_DREPLY_DONE_CAP = 200               # bounded dedup set of delivered reply ids
_MENTION_TOKEN_RX = re.compile(r"<@[!&]?\d+>")


def clean_reply_text(raw, bot_id=""):
    """Turn a Discord reply's raw content into a single-line prompt safe to type.

    Strips @mention tokens (`<@id>` / `<@!id>` / `<@&role>` — a reply that pings
    the bot must not type the ping), collapses ALL whitespace (incl. newlines — a
    stray newline would submit the prompt early / split it), and caps the length.
    Returns "" when nothing usable remains (→ the caller ignores the reply)."""
    if not raw:
        return ""
    s = _MENTION_TOKEN_RX.sub(" ", str(raw))
    if bot_id:
        s = s.replace("<@%s>" % bot_id, " ").replace("<@!%s>" % bot_id, " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:DISCORD_REPLY_MAX_CHARS]


def parse_discord_reply(msg, allowed_ids, qmap, bot_id=""):
    """Validate ONE Discord message as an answer to a tracked ❓ ping.

    Returns {reply_id, referenced, session, cwd, channel, text} when `msg` is a
    reply BY an allowed owner TO a message id in `qmap` with usable text; else
    None. Pure — all the security checks live here so they are unit-testable."""
    if not isinstance(msg, dict):
        return None
    reply_id = str(msg.get("id") or "").strip()
    author = msg.get("author") or {}
    author_id = str(author.get("id") or "").strip()
    ref = (msg.get("message_reference") or {}).get("message_id")
    ref = str(ref or "").strip()
    if not reply_id or not author_id or not ref:
        return None
    if author_id not in allowed_ids:            # SECURITY: only a known owner
        return None
    q = qmap.get(ref)
    if not q:                                   # must reply to a ❓ WE sent
        return None
    text = clean_reply_text(msg.get("content"), bot_id)
    if not text:
        return None
    return {"reply_id": reply_id, "referenced": ref,
            "session": str(q.get("session") or ""), "cwd": str(q.get("cwd") or ""),
            "channel": str(q.get("channel") or ""), "text": text,
            "question": " ".join(str(q.get("question") or "").split()),
            "asked_ts": q.get("ts") or 0}


DREPLY_TICKET_FALLBACK_S = 180      # reply blocked this long → deliver via the ticket
# A box with NO pane for the asking session may not be the pane's HOST — a
# hosted stream's session (montalu claude inside newlevel's tmux) is delivered
# by the HOST watchdog's keystrokes. The no-pane fallback therefore waits
# longer, so the host wins the race and a double gh comment cannot happen; for
# a genuinely dead session it still fires (later), never silently.
DREPLY_NOPANE_FALLBACK_S = 900
_TICKET_NUM_RX = re.compile(r"#(\d{1,6})")


def _input_line_text(captured):
    """Text sitting in the pane's INPUT BOX: '' = bare prompt, None = no input
    box at the chrome boundary (running-turn spinner / dialog / no pane / no
    boundary locatable at all). Same boundary-line discipline as
    `_has_free_prompt` — resolved via `_find_boundary_line` (structural
    separator-pair search first, glyph-based chrome peel as fallback, issue
    #46) — never scans the transcript above the box. For a long WRAPPED
    input, capture-pane (no -J) renders it as multiple lines and the
    boundary is the LAST one — i.e. the TAIL of the typed text — which is
    why callers match with endswith()."""
    s = _find_boundary_line(captured)
    if s is None:
        return None
    if not s.startswith("❯"):
        return None
    return s[1:].strip()


def _classify_boundary(captured):
    """Classify the pane's input-box boundary for the keystroke-sending jobs
    (12/14/15) — splits `_input_line_text`'s collapsed-to-None result into
    its two genuinely different causes (issue #46): a session that is truly
    BUSY (a foreground spinner / dialog occupies the boundary, and it just
    isn't `❯`-shaped) versus one where NO boundary could be located at all
    (`_find_boundary_line` returns None — structural AND glyph fallback both
    failed, e.g. the whole capture is chrome). Collapsing both into one
    "busy" bucket is exactly what mislabeled the #46 incident: an
    unrecognized chrome row below the box made the OLD glyph peel stop on
    that row and report "busy", when the pane was neither busy nor missing
    an input line at all — it just held a draft the peel could no longer
    reach.

    Returns (kind, text):
      ("input", <draft text, "" if bare>) -- a real boundary, safe to inspect
      ("busy", None)                      -- a real boundary, not `❯`-shaped
      ("no-input-line", None)             -- no boundary found under either strategy
    """
    s = _find_boundary_line(captured)
    if s is None:
        return ("no-input-line", None)
    if s.startswith("❯"):
        return ("input", s[1:].strip())
    return ("busy", None)


def _ticket_fallback_text(r):
    q = " ".join(str(r.get("question") or "").split())[:400]
    return ("**ODPOVEĎ UŽÍVATEĽA Z DISCORDU** (doručená na ticket — session input "
            "bol obsadený/wedged, watchdog ticket-fallback): «%s»\n\n"
            "Otázka: %s\n\n"
            "Zariaď sa podľa odpovede (číslo = poradie ponúknutej možnosti)."
            % (" ".join(str(r.get("text") or "").split()), q))


def _gh_comment(cwd, num, text, user=None):
    """Post `text` as a comment on issue #num of the repo at `cwd` (the DURABLE
    delivery lane of the ticket-fallback). `user`: a FOREIGN question's repo —
    run via `sudo -n -u <user>` with THEIR gh auth and a login-shell cd (their
    HOME isn't even cd-able for this process). Best-effort: False on any
    failure — the reply then stays pending on the keystroke path."""
    import shlex
    import subprocess
    try:
        if user:
            p = subprocess.run(
                ["sudo", "-n", "-u", user, "bash", "-lc",
                 "cd %s && exec gh issue comment %d -F -"
                 % (shlex.quote(str(cwd)), int(num))],
                input=text, capture_output=True, text=True, timeout=30)
        else:
            p = subprocess.run(["gh", "issue", "comment", str(num), "-F", "-"],
                               cwd=cwd or None, env=_gh_env(), input=text,
                               capture_output=True, text=True, timeout=25)
        return p.returncode == 0
    except Exception:
        return False


def _foreign_user(cwd):
    """Unix user owning `cwd` when it lives under ANOTHER user's /home — the
    sudo-hosted stream shape (montalu claude inside newlevel's tmux). None for
    the current user's own paths and non-home paths."""
    import getpass
    m = re.match(r"/home/([a-z0-9_-]+)/", str(cwd) + "/")
    if not m:
        return None
    user = m.group(1)
    try:
        return None if user == getpass.getuser() else user
    except Exception:
        return None


def _foreign_session_info(user, cwd):
    """(session_id, transcript_mtime) of the FOREIGN user's newest transcript
    for `cwd` (`sudo -n`) — binds a sudo-hosted pane to its session for jobs
    7 + 10. None on any failure (no passwordless sudo / no transcript)."""
    import subprocess
    script = (
        "import glob,os\n"
        "d=os.path.expanduser('~/.claude/projects/'+"
        "''.join('-' if c in '/._' else c for c in %r))\n"
        "fs=sorted(glob.glob(d+'/*.jsonl'),key=os.path.getmtime)\n"
        "if fs: print(os.path.basename(fs[-1])[:-6], os.path.getmtime(fs[-1]))\n"
        % str(cwd))
    try:
        p = subprocess.run(["sudo", "-n", "-u", user, "python3", "-c", script],
                           capture_output=True, text=True, timeout=10)
        parts = (p.stdout or "").split()
        if p.returncode == 0 and len(parts) == 2:
            return parts[0], float(parts[1])
        return None
    except Exception:
        return None            # fail-safe: no passwordless sudo / no transcript


def _foreign_questions(user):
    """The FOREIGN user's outstanding-❓ map (`sudo -n` read), {} on any
    failure — a hosted stream records its questions under ITS home, invisible
    to this box's notify.load_questions."""
    import subprocess
    try:
        p = subprocess.run(
            ["sudo", "-n", "-u", user, "cat",
             os.path.join("/home", user, ".claude", "discord-questions.json")],
            capture_output=True, text=True, timeout=10)
        if p.returncode != 0 or not (p.stdout or "").strip():
            return {}
        d = json.loads(p.stdout)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}              # fail-safe: an unreadable foreign map = none


def _foreign_drop_question(user, qid):
    """Drop a delivered question from the FOREIGN user's map (`sudo -n`) so
    their own watchdog never re-handles / double-fallbacks the same reply."""
    import subprocess
    script = ("import json,os\n"
              "p=os.path.expanduser('~/.claude/discord-questions.json')\n"
              "d=json.load(open(p))\n"
              "d.pop(%r,None)\n"
              "tmp=p+'.tmp'\n"
              "json.dump(d,open(tmp,'w'))\n"
              "os.replace(tmp,p)\n" % str(qid))
    try:
        p = subprocess.run(["sudo", "-n", "-u", user, "python3", "-c", script],
                           capture_output=True, text=True, timeout=10)
        return p.returncode == 0
    except Exception:
        return False


# Prompts TYPED BY MACHINERY into a session (watchdog nudges/deliveries,
# auto-armed /goal, harness task-notifications, slash-command echoes) must
# never count as "the user answered the pending ❓ at the terminal".
_MACHINE_PROMPT_EXACT = ("continue",)
_MACHINE_PROMPT_PREFIXES = (
    "stuck-check:", "Priorita: prio:bounce", "bounce-backstop:",
    "gk-request backstop:", "/goal ",
    "Odpoveď z Discordu:", "Odpoveď užívateľa na tvoju otázku",
    "<task-notification>", "<local-command", "<command-", "<system-reminder")


def _last_human_prompt_ts(tpath, tail_bytes=2_000_000):
    """Epoch of the NEWEST human-typed prompt in the transcript tail, or None.
    Machine-typed prompts (the list above), tool_result user entries and meta
    entries don't count — only something the USER actually wrote."""
    from datetime import datetime
    try:
        with open(tpath, "rb") as f:
            try:
                f.seek(-tail_bytes, 2)
            except OSError:
                f.seek(0)
            raw = f.read()
    except OSError:
        return None
    best = None
    for ln in raw.splitlines():
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if not isinstance(e, dict) or e.get("type") != "user" or e.get("isMeta"):
            continue
        c = (e.get("message") or {}).get("content")
        if isinstance(c, str):
            text = c
        elif isinstance(c, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_result"
                   for b in c):
                continue
            text = " ".join(b.get("text", "") for b in c
                            if isinstance(b, dict) and b.get("type") == "text")
        else:
            continue
        t = text.strip()
        if (not t or t in _MACHINE_PROMPT_EXACT
                or any(t.startswith(p) for p in _MACHINE_PROMPT_PREFIXES)):
            continue
        try:
            ep = datetime.fromisoformat(
                str(e.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if best is None or ep > best:
            best = ep
    return best


def _transcript_for_session(projects_dir, sid, cwd):
    """Path of the session's transcript, or None. The cwd-encoded dir is only
    a HINT: the ❓ hook records the session's CURRENT dir while CC keys the
    transcript dir by the LAUNCH dir (montalu ran at …/odoo but asked from
    …/odoo/odoo-slovnormal, 2026-07-22) — the session id is unique across the
    projects tree, so a miss falls back to a SID glob."""
    p = Path(projects_dir) / encode_project_dir(cwd) / (sid + ".jsonl")
    if p.is_file():
        return p
    try:
        hits = list(Path(projects_dir).glob("*/" + sid + ".jsonl"))
    except OSError:
        return None
    if not hits:
        return None
    try:
        return max(hits, key=lambda h: h.stat().st_mtime)
    except OSError:
        return hits[0]


def prune_answered_questions(now, projects_dir=PROJECTS_DIR, dry_run=False):
    """Drop question-map entries whose asking session received a HUMAN prompt
    AFTER the ❓ was pinged — the question was answered at the terminal, so
    the entry would otherwise linger until the 24h TTL and inflate the
    statusline 'otazky' badge (user complaint 2026-07-22: 14 stale questions
    shown in a project with zero). Safe by design: if the question is somehow
    still pending after a human prompt, the loop re-asks (the prompt cleared
    the ping dedup) and re-records a fresh entry."""
    from notify import load_questions, drop_question
    logs = []
    try:
        qmap = load_questions()
    except Exception:
        return logs
    for qid, rec in list(qmap.items()):
        if not isinstance(rec, dict):
            continue
        sid = str(rec.get("session") or "")
        cwd = str(rec.get("cwd") or "")
        qts = rec.get("ts") or 0
        if not sid or not cwd:
            continue
        tpath = _transcript_for_session(projects_dir, sid, cwd)
        if tpath is None:
            continue
        hts = _last_human_prompt_ts(tpath)
        if hts and hts > qts + 30:       # grace: never race the ping's own turn
            if not dry_run:
                drop_question(qid)
            logs.append("question answered in-session — pruned %s [%s]"
                        % (str(qid)[-6:], project_label(cwd)))
    return logs


def compose_reply_prompt(r):
    """The ONE-LINE prompt typed into the asking session for a Discord reply.

    A reply may land hours/days after the ❓ was asked — a bare '1' is
    meaningless once the session's context no longer holds the question (user
    ask, 2026-07-17), so the prompt carries WHEN + WHAT was asked + the answer.
    One line only: send_continue types the text literally then presses Enter, a
    newline would submit early. A legacy map entry without stored question text
    falls back to the raw reply (the pre-2026-07-17 behavior)."""
    # The re-arm tail closes the ping-pong: a /goal loop correctly ENDS on a
    # blocked ❓ (stop condition A) — after the answer resolves the ticket the
    # session must re-print the continuation /goal + arm question so the
    # watchdog auto-arm re-starts the loop (montalu break, 2026-07-20: the
    # answer landed, nothing re-armed, bounce tickets rotted).
    rearm = (" Ak predtým bežal /goal loop (ukončil sa touto otázkou), po "
             "vyriešení vytlač continuation /goal + arm otázku s prázdnym "
             "inputom — auto-arm ho nalepí sám.")
    q = str(r.get("question") or "").strip()
    if not q:
        return r["text"] + rearm
    ts = r.get("asked_ts") or 0
    when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts
            else "nedávno")
    return ("Odpoveď z Discordu: %s ti bola cez Discord položená táto otázka: "
            "«%s» Užívateľ na ňu teraz odpovedal: «%s» — zariaď sa podľa tejto "
            "odpovede (číslo = poradie ponúknutej možnosti).%s"
            % (when, q, " ".join(str(r.get("text") or "").split()), rearm))


def _discord_get(url, token, timeout=6):
    import urllib.request
    req = urllib.request.Request(
        url, headers={"Authorization": "Bot " + token,
                      "User-Agent": "DiscordBot (https://github.com/zbynekdrlik/airuleset, 1.0)"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def fetch_channel_messages(channel, token, limit=25):
    """GET the last `limit` messages of a channel/thread (newest first). Returns a
    list of message dicts, or [] on any error (fail-safe — never breaks the poll)."""
    if not channel or not token:
        return []
    try:
        raw = _discord_get(
            "https://discord.com/api/v10/channels/%s/messages?limit=%d" % (channel, limit),
            token)
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _react_ok(channel, message_id, token):
    """React ✅ to a delivered reply as a visible 'answer routed' confirmation.
    Best-effort (a failed reaction never blocks delivery)."""
    import urllib.request
    try:
        url = ("https://discord.com/api/v10/channels/%s/messages/%s/reactions/%s/@me"
               % (channel, message_id, "%E2%9C%85"))     # ✅ url-encoded
        req = urllib.request.Request(
            url, method="PUT", data=b"",
            headers={"Authorization": "Bot " + token, "Content-Length": "0",
                     "User-Agent": "DiscordBot (https://github.com/zbynekdrlik/airuleset, 1.0)"})
        urllib.request.urlopen(req, timeout=6).read()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Stash-around delivery (issue #35) — CC's native prompt STASH (Ctrl+S) lets
# the watchdog deliver its own text into a pane that is idle but holds a
# FOREIGN draft, without losing that draft: park it, type + submit ours, and
# CC auto-restores the parked draft the instant the delivered turn completes
# (empirically verified against CC 2.1.220 — NOT a second Ctrl+S press, which
# the docs wrongly imply). The stash is SINGLE-SLOT with a SILENT overwrite,
# so every step is verified against a fresh capture and any surprise aborts
# to the caller's pre-#35 fallback (pending / ticket-fallback / skip) —
# never a guess, never data loss.
# --------------------------------------------------------------------------- #

STASH_MARKER = "› stashed"          # "› stashed" — CC's stash-slot indicator

# CC COLLAPSES a long literal `send-keys -l` into a single placeholder row
# (`[Pasted text #3]`) instead of rendering the text — live-observed
# 2026-07-26 on job 20's first real re-arm (a 3152-char /goal). Every
# verified delivery here matches the typed text against the boundary line,
# which can NEVER match that placeholder; the check only ever worked because
# previous callers all sent short text.
_PASTED_PLACEHOLDER_RX = re.compile(r"^\[Pasted text #\d+\]$")


def _typed_landed(text, itext):
    """True if the input-box content `itext` is evidence that `text` was
    typed IN FULL — either the literal tail (a short type renders verbatim;
    a WRAPPED one puts its tail on the boundary line, hence endswith) or
    CC's collapsed `[Pasted text #N]` placeholder for a long one. A
    genuinely TRUNCATED partial type matches neither and is still refused —
    submitting one is the #36 disaster this verification exists to prevent."""
    if not itext:
        return False
    if text.endswith(itext):
        return True
    return bool(_PASTED_PLACEHOLDER_RX.match(itext.strip()))


def deliver_with_stash(pid, text, run, captured=None, logs=None):
    """Deliver `text` into a pane that is IDLE but holds a foreign draft.

    Protocol (every step verified, any surprise aborts to the SAFEST state):
      1. Abort if a stash slot is already occupied (`› stashed` anywhere in
         the capture) — stashing over it would SILENTLY destroy whatever is
         already parked there.
      2. Require idle-with-a-draft: a free `❯` prompt with non-empty text and
         no live-turn signal ('esc to interrupt'). Anything else aborts —
         this helper handles exactly one shape, never a guess.
      3. If the agent-strip selector holds focus (`_strip_selected`, #36),
         ONE Escape first — never two (a rapid double-Escape PERMANENTLY
         DELETES a draft, empirically confirmed).
      4. Ctrl+S stashes the draft. Re-capture and verify the box is now bare
         AND the `› stashed` indicator is lit — else abort (the draft is
         presumably untouched; nothing lost, nothing typed).
      5. Type `text` literally, re-capture, verify the boundary line is `❯` +
         a TAIL of `text` (a wrapped input renders its tail at the
         boundary). Verify failure → ABORT-RESTORE: Ctrl+S pops the stashed
         draft back, and we return False regardless of that restore's own
         outcome (best-effort; the alternative is discarding state).
      6. Enter submits. If the text is STILL at the boundary (a swallowed
         Enter — the agent-strip-selector class of bug, #36), ONE corrective
         Escape+Enter (never a second bare Enter, never two Escapes).
      7. Success = the box no longer shows our text. The stashed draft
         auto-restores itself once the delivered turn completes.

    `logs`, if a list, gets one reason string appended on every abort/success
    path — callers that want visibility (or tests) pass one in; the default
    (None) is silent, matching every other keystroke helper in this file."""
    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    run = run or _default_run
    cap = captured if captured is not None else capture_pane(pid, run, lines=30)
    if cap and STASH_MARKER in cap:
        _log("stash-abort: slot occupied")
        return False
    if not (_has_free_prompt(cap, bare_only=False) and _input_line_text(cap)):
        _log("stash-abort: not idle-with-draft")
        return False
    if "esc to interrupt" in (cap or ""):
        _log("stash-abort: live turn")
        return False
    if _strip_selected(cap):
        run(["tmux", "send-keys", "-t", pid, "Escape"])
    run(["tmux", "send-keys", "-t", pid, "C-s"])
    cap = capture_pane(pid, run, lines=30)
    if _input_line_text(cap) != "" or STASH_MARKER not in (cap or ""):
        _log("stash-abort: verify-bare-failed")
        return False
    run(["tmux", "send-keys", "-t", pid, "-l", text])
    cap = capture_pane(pid, run, lines=30)
    itext = _input_line_text(cap)
    if not _typed_landed(text, itext):
        _log("stash-abort: type-verify-failed")
        run(["tmux", "send-keys", "-t", pid, "C-s"])      # restore the draft
        capture_pane(pid, run, lines=30)
        return False
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    cap = capture_pane(pid, run, lines=30)
    itext2 = _input_line_text(cap)
    if _typed_landed(text, itext2):
        # swallowed submit (#36 class) — ONE corrective Escape+Enter, never a
        # second Escape.
        run(["tmux", "send-keys", "-t", pid, "Escape"])
        run(["tmux", "send-keys", "-t", pid, "Enter"])
        cap = capture_pane(pid, run, lines=30)
        itext3 = _input_line_text(cap)
        if _typed_landed(text, itext3):
            _log("stash-abort: swallowed-submit-not-recovered")
            return False
    _log("stash-delivered")
    return True


def _pane_location(pid, run):
    """Best-effort 'session:window.pane' for a pane — a job-10 wedge ping
    that named no window was a live complaint ('nevidim nikde ziaden
    neodoslany text', issue #35). Blank on any failure; never blocks a ping."""
    try:
        out = run(["tmux", "display-message", "-p", "-t", pid,
                   "#{session_name}:#{window_index}.#{pane_index}"])
        return (out or "").strip()
    except Exception:
        return ""


_DREPLY_TYPED_TTL_S = 48 * 3600


def _record_dreply_typed(state, pid, prompt, now):
    """Remember the TAIL of text job 7 just typed into `pid`, so job 10 can
    recognize the SAME text sitting stuck as MACHINE (issue #35) instead of
    pinging the user about their own delivery's swallowed submit. Pruned to
    the last 48h so a dead pane's entry doesn't linger forever."""
    typed = state.get("dreply_typed")
    typed = ({k: v for k, v in typed.items()
             if isinstance(v, dict) and now - (v.get("ts") or 0) < _DREPLY_TYPED_TTL_S}
            if isinstance(typed, dict) else {})
    typed[pid] = {"tail": prompt[-160:], "ts": now}
    state["dreply_typed"] = typed


def _is_dreply_machine_text(state, pid, txt):
    """True if `txt` (the pane's current input-box text) is a SUFFIX of — or
    contained in — the tail job 7 recorded for `pid` via
    `_record_dreply_typed`. A wrapped input renders its TAIL at the
    boundary, hence the endswith/contains check rather than equality."""
    rec = (state.get("dreply_typed") or {}).get(pid)
    if not isinstance(rec, dict):
        return False
    tail = str(rec.get("tail") or "")
    return bool(tail) and (tail.endswith(txt) or txt in tail)


def deliver_discord_replies(now, run, state, panes_by_sid, dry_run=False,
                            discord_fetch=None, env=None, gh_comment=None,
                            hosted_users=None, foreign_questions=None,
                            foreign_drop=None):
    """Route owner Discord replies into the sessions that asked (job 7).

    `panes_by_sid`: {session_id: (pane_id, captured_pane)} for the live `claude`
    panes this cycle (collected in run_once). `discord_fetch(channel, token)`:
    returns recent messages (injectable for tests). Delivers each matching reply
    ONCE (dedup on reply id + the question is dropped on delivery), only into an
    IDLE-input pane, and reacts ✅ on success. Returns log lines. Never raises.

    2026-07-20 (#1832 incident) hardening:
    - VERIFY after typing: a swallowed Enter (queued-prompt wedge, airuleset#20)
      leaves the text sitting at `❯` — up to 2 corrective Enters; still stuck →
      NOT delivered (the fallback clock keeps ticking), and the next cycle
      recognizes OUR OWN stuck text (prompt tail at `❯`) and presses Enter only,
      never retypes (no doubled text).
    - TICKET-FALLBACK: a reply blocked > DREPLY_TICKET_FALLBACK_S (busy pane
      with a foreign draft, dead session, persistent wedge) is delivered as a
      gh comment on the #N parsed from the stored question text — the DURABLE
      lane (the loop's question tracking lives on the ticket anyway); then
      ✅-reacted + dropped like a normal delivery. No #N / gh failure → the
      keystroke path keeps retrying as before."""
    from notify import (bot_token, known_owner_ids, load_questions, drop_question,
                        _read_env)
    logs = []
    env = _read_env() if env is None else env
    qmap = load_questions()
    # Merge HOSTED users' question maps (2026-07-21: montalu's claude runs in
    # THIS tmux, but its ❓ map lives under /home/montalu — the session was
    # invisible to both watchdogs). `q_owner` remembers which foreign user owns
    # a merged question so delivery drops it from THEIR map.
    hosted_users = hosted_users or {}
    f_load = foreign_questions or _foreign_questions
    f_drop = foreign_drop or _foreign_drop_question
    q_owner = {}                        # question id -> foreign unix user
    for fu in sorted(set(hosted_users.values())):
        for qid, rec in (f_load(fu) or {}).items():
            if qid not in qmap and isinstance(rec, dict):
                qmap[qid] = rec
                q_owner[qid] = fu
    if not qmap and not state.get("dreply_pointer"):
        return logs
    token = bot_token(env)
    allowed = known_owner_ids(env)
    if not token or not allowed:
        return logs
    fetch = discord_fetch or fetch_channel_messages
    gh_fn = gh_comment or _gh_comment

    done = state.get("dreply_done")
    done = list(done) if isinstance(done, list) else []
    done_set = set(done)
    blocked = state.get("dreply_blocked")
    blocked = dict(blocked) if isinstance(blocked, dict) else {}
    acked = state.get("dreply_acked")
    acked = list(acked) if isinstance(acked, list) else []
    acked_set = set(acked)
    channels = {str(v.get("channel") or "") for v in qmap.values()}
    channels.discard("")

    def _ack(r):
        # ✅ = RECEIPT, fired the moment the reply is MATCHED (even while the
        # delivery pends) — a green check minutes late reads as "answer lost"
        # (3rd user report, 2026-07-20). Once per reply.
        if r["reply_id"] in acked_set:
            return
        if not dry_run:
            _react_ok(r["channel"], r["reply_id"], token)
        acked_set.add(r["reply_id"])
        acked.append(r["reply_id"])

    def _delivered(r, via_ticket=None):
        done_set.add(r["reply_id"])
        done.append(r["reply_id"])
        # dry-run simulates delivery — it must NEVER mutate the real on-disk
        # map (a dropped live question loses the answer). A FOREIGN question
        # is dropped from its owner's map so their watchdog never re-handles.
        if not dry_run:
            fu = q_owner.get(r["referenced"])
            if fu:
                f_drop(fu, r["referenced"])
            else:
                drop_question(r["referenced"])
        qmap.pop(r["referenced"], None)     # same-batch 2nd reply won't re-fire
        blocked.pop(r["reply_id"], None)
        if via_ticket:
            logs.append("reply→ticket #%s [%s]" % (via_ticket, r["session"][:12]))
        else:
            logs.append("reply→%s [%s]" % (project_label(r["cwd"]), r["session"][:12]))

    def _pending(r, why):
        blocked.setdefault(r["reply_id"], now)
        # The durable lane once the keystroke path has been blocked long
        # enough. NO pane = we may not be the pane's HOST (a hosted stream's
        # session lives in another user's tmux) — defer longer so the host
        # delivers by keystroke first; a busy/wedged pane WE own keeps the
        # tight deadline.
        deadline = (DREPLY_NOPANE_FALLBACK_S if why == "no pane"
                    else DREPLY_TICKET_FALLBACK_S)
        if now - blocked[r["reply_id"]] >= deadline:
            m = _TICKET_NUM_RX.search(r.get("question") or "")
            if m:
                fu = q_owner.get(r["referenced"])
                if dry_run:
                    ok = True
                elif fu:
                    ok = gh_fn(r["cwd"], m.group(1), _ticket_fallback_text(r),
                               user=fu)
                else:
                    ok = gh_fn(r["cwd"], m.group(1), _ticket_fallback_text(r))
                if ok:
                    ptr = state.get("dreply_pointer")
                    ptr = dict(ptr) if isinstance(ptr, dict) else {}
                    ptr[r["session"]] = {"num": m.group(1), "ts": now}
                    state["dreply_pointer"] = ptr
                    _delivered(r, via_ticket=m.group(1))
                    return
        logs.append("reply pending (%s) %s" % (why, r["session"][:12]))

    for ch in sorted(channels):
        for msg in fetch(ch, token):
            r = parse_discord_reply(msg, allowed, qmap, bot_id="")
            if not r or r["reply_id"] in done_set:
                continue
            _ack(r)
            pane = panes_by_sid.get(r["session"])
            if not pane:
                # the asking session isn't a live pane right now — retry next
                # cycle (the question stays in the map; TTL prunes if it's gone).
                _pending(r, "no pane")
                continue
            pid, captured = pane
            prompt = compose_reply_prompt(r)
            itext = _input_line_text(captured)
            # a PRIOR wedged delivery of THIS reply left our own text at `❯`
            # (endswith: a wrapped input renders its TAIL as the boundary line)
            own_stuck = bool(itext) and prompt.endswith(itext)
            if not (pane_at_idle_prompt(captured) or own_stuck):
                # A genuinely busy mid-turn pane / open dialog we must never
                # type over (#233) has no free `❯` at all — but an IDLE pane
                # holding a FOREIGN draft (also lands here, since its box
                # isn't bare) is no longer a dead end (issue #35): stash the
                # draft, deliver, let CC auto-restore it. Any ambiguity
                # (busy pane, copy-mode, a verify failure) falls straight
                # through to the pre-#35 pending/fallback path, unchanged.
                if not dry_run and not pane_in_mode(pid, run):
                    _record_dreply_typed(state, pid, prompt, now)
                    if deliver_with_stash(pid, prompt, run, captured=captured):
                        idead = state.get("inputdead")
                        if isinstance(idead, dict):
                            idead.pop(r["session"], None)
                            state["inputdead"] = idead
                        _delivered(r)
                        continue
                _pending(r, "busy")
                continue
            if pane_in_mode(pid, run):
                continue
            if not dry_run:
                if own_stuck:
                    run(["tmux", "send-keys", "-t", pid, "Enter"])
                else:
                    _record_dreply_typed(state, pid, prompt, now)
                    send_continue(pid, prompt, run)
                # verify the input box emptied — a swallowed Enter (#20) leaves
                # the text at `❯`; up to 2 corrective Escape+Enter retries, then
                # give up. Escape FIRST (issue #36) — a swallow while the
                # agent-strip selector holds focus makes a bare Enter navigate
                # ("view agent") instead of submitting; ONE Escape clears that.
                # Never two Escapes in a row — that permanently deletes a draft.
                t2 = _input_line_text(capture_pane(pid, run, lines=30))
                tries = 0
                while t2 and tries < 2:
                    run(["tmux", "send-keys", "-t", pid, "Escape"])
                    run(["tmux", "send-keys", "-t", pid, "Enter"])
                    t2 = _input_line_text(capture_pane(pid, run, lines=30))
                    tries += 1
                if t2:
                    blocked.setdefault(r["reply_id"], now)
                    logs.append("reply wedged (enter swallowed) %s"
                                % r["session"][:12])
                    # An ACTIVE session with a DEAD input box (the 4th #20
                    # recurrence) is invisible to job 10 — count our own
                    # verify failures; >= 3 cycles → ONE deduped ping (the
                    # armed /goal survives a kill + resume, so the restart
                    # is safe to ask for).
                    idead = state.get("inputdead")
                    idead = dict(idead) if isinstance(idead, dict) else {}
                    idead[r["session"]] = int(idead.get(r["session"]) or 0) + 1
                    state["inputdead"] = idead
                    if idead[r["session"]] == 3:   # exactly once per episode
                        from notify import send as _send
                        _send("⚠️ **%s** — session BEŽÍ, ale jej VSTUP je "
                              "mŕtvy (Enter sa opakovane nechytá — wedge). "
                              "Nedá sa jej nič doručiť. Reštart: v okne kill "
                              "claude procesu + `claude` (resume) — armovaný "
                              "/goal reštart prežije."
                              % project_label(r["cwd"]),
                              env=env,
                              dedup_key="inputdead:%s" % r["session"][:12],
                              dry_run=dry_run)
                    continue
            idead = state.get("inputdead")
            if isinstance(idead, dict):
                idead.pop(r["session"], None)
                state["inputdead"] = idead
            _delivered(r)

    # A ticket-fallback delivery is durable but INVISIBLE in the terminal —
    # the user watching the window assumes the answer vanished (2026-07-20).
    # Type a short visible pointer into the asking pane as soon as it is
    # typable; once per fallback, expired after a day.
    ptr = state.get("dreply_pointer")
    ptr = dict(ptr) if isinstance(ptr, dict) else {}
    for sid in list(ptr):
        ent = ptr[sid] if isinstance(ptr[sid], dict) else {}
        if now - (ent.get("ts") or 0) > 86400:
            ptr.pop(sid)
            continue
        pane = panes_by_sid.get(sid)
        if not pane:
            continue
        pid, captured = pane
        if not pane_at_idle_prompt(captured) or pane_in_mode(pid, run):
            continue
        if not dry_run:
            send_continue(pid, "Odpoveď užívateľa na tvoju otázku je na "
                               "tickete #%s (komentár ODPOVEĎ UŽÍVATEĽA Z "
                               "DISCORDU) — prečítaj ho a zariaď sa podľa "
                               "neho." % ent.get("num"), run)
        ptr.pop(sid)
        logs.append("reply pointer→#%s [%s]" % (ent.get("num"), sid[:12]))
    state["dreply_pointer"] = ptr

    state["dreply_done"] = done[-_DREPLY_DONE_CAP:]
    state["dreply_acked"] = acked[-_DREPLY_DONE_CAP:]
    state["dreply_blocked"] = {k: v for k, v in blocked.items()
                               if now - v < 86400}
    return logs


# --------------------------------------------------------------------------- #
# Bounce backstop (job 8, 2026-07-19) — gatekeeper-returned work must never rot.
# The gatekeeper's /process-subdev files findings as `prio:bounce` tickets and
# nudges over brittle ssh/tmux paths; when the sub-dev's autopilot loop has
# ended (or the nudge missed), the tickets sit unworked until the user notices
# (the 4 stalled david re-handoffs). This job is the machine-local backstop:
# every ~30 min, check the repos this box touches for open prio:bounce tickets
# scoped to the pane's stream; a live IDLE claude pane gets a typed nudge (the
# autopilot skill's nudge-ack dispatches a worker even with no /goal armed); a
# repo with NO live pane (known from the tickets-status cache) gets ONE deduped
# Discord ping. A BUSY pane gets NOTHING — a running loop re-queries the
# backlog each turn, so the label alone is the insertion (never interrupt
# mid-work — the user's standing rule). NB (historical — until the
# 2026-07-24 subdev migration, airuleset#33 + odoo-erp#1895): montalu's claude
# used to run inside NEWLEVEL's tmux (a `sudo su - montalu` window) on dev1 —
# pane-driven detection was what reached it, since a montalu-side watchdog saw
# no tmux at all. montalu now has its own tmux session on subdev.
# --------------------------------------------------------------------------- #

BOUNCE_INTERVAL = 30 * 60            # min seconds between bounce sweeps
BOUNCE_RENUDGE_SECONDS = 6 * 3600    # same ticket set re-nudged at most this often
_REDUCED_STREAM_USERS = ("david", "marek", "montalu")

BOUNCE_NUDGE = ("bounce-backstop: open prio:bounce tickets %s in %s — "
                "gatekeeper-returned work is waiting. Per the autopilot "
                "skill's bounce nudge-ack: if a /goal loop is armed the label "
                "alone queues them; with NO loop armed, validate and dispatch "
                "the background autopilot-worker for them now.")


def _bounce_quals(cwd):
    """gh search quals scoping the bounce query to the PANE's stream, derived
    from its /home/<user>/ prefix — historically because montalu's claude ran
    under newlevel's tmux (until the 2026-07-24 subdev migration), making the
    WATCHDOG user meaningless there; the prefix derivation stays regardless,
    since gh identity is the same account everywhere, so @me cannot scope.
    Reduced streams → their stream label (the
    #1599 convention: findings tickets carry stream:<name>); a full-authority
    box takes the CORE slice — sub-dev streams EXCLUDED (live dry-run finding
    2026-07-19: an unscoped dev1 query picked up david's bounces and would
    have pinged the wrong person; the sub-dev's own box nudges those). The
    GATEKEEPER is skipped ENTIRELY ([] = no query, no nudge): the bounce lane's
    direction is reviewer→sub-dev — nudging the reviewer about bounces IT filed
    is backwards (the live gatekeeper-pane spam incident, 2026-07-19)."""
    c = str(cwd or "")
    if c.startswith("/home/gatekeeper/"):
        return []
    for u in _REDUCED_STREAM_USERS:
        if c.startswith("/home/%s/" % u):
            return ["label:stream:%s" % u]
    return [" ".join("-label:stream:%s" % u for u in _REDUCED_STREAM_USERS)]


def _gh_env(home=None, base=None):
    """Env for gh subprocesses. david's box keeps GH_TOKEN only as an `export`
    in ~/.bashrc (no hosts.yml), which a systemd --user service never sources —
    parse that one line as a fallback. An already-set token is never touched;
    the value is never logged."""
    env = dict(os.environ if base is None else base)
    if env.get("GH_TOKEN") or env.get("GITHUB_TOKEN"):
        return env
    homedir = home or os.path.expanduser("~")
    try:
        rc = Path(os.path.join(homedir, ".bashrc")
                  ).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return env                      # no .bashrc → gh runs with what it has
    m = re.search(r'^\s*export\s+(GH_TOKEN|GITHUB_TOKEN)=["\x27]?(.+)$',
                  rc, re.M)
    if not m:
        return env
    key, val = m.group(1), m.group(2).strip().strip('"\x27')
    # `export GH_TOKEN=$(cat ~/.config/gh-token 2>/dev/null)` — david's real
    # form (2026-07-20 401 root cause: the literal regex captured '$(cat').
    # Resolve the one safe substitution shape by reading the file ourselves;
    # any OTHER substitution is unresolvable → leave the env untouched
    # (a garbage literal would turn every gh call into a silent 401).
    cat = re.match(r"\$\(\s*cat\s+([^\s)]+)", val)
    if cat:
        p = cat.group(1)
        p = os.path.join(homedir, p[2:]) if p.startswith("~/") else p
        try:
            val = Path(p).read_text(encoding="utf-8").strip()
        except OSError:
            return env
    elif val.startswith("$("):
        return env
    else:
        val = val.split()[0] if val.split() else ""
    if val:
        env[key] = val
    return env


def _fetch_bounce_tickets(root, home=None):
    """Open prio:bounce ticket numbers for the repo at `root`, scoped to the
    root's stream. None on any error (fail-safe — an auth/network hiccup must
    never look like 'no bounces')."""
    import subprocess
    nums, env = set(), _gh_env(home)
    for qual in _bounce_quals(root):
        try:
            r = subprocess.run(
                ["gh", "issue", "list", "--state", "open", "--label",
                 "prio:bounce", "--search",
                 ("-label:autopilot-skip " + qual).strip(), "-L", "100",
                 "--json", "number"],
                cwd=root, env=env, capture_output=True, text=True, timeout=8)
            if r.returncode != 0:
                return None
            nums.update(x["number"] for x in json.loads(r.stdout))
        except Exception:
            return None
    return sorted(nums)


def _cache_repo_roots(home=None, max_age_s=None):
    """{root: name} from the tickets-status cache — the repos this box recently
    worked (the Discord-fallback candidate set for panes that no longer exist).
    `max_age_s` keeps only entries whose cache ts is that fresh — job 11's
    no-pane ping fired for a 16-DAY-stale checkout supervised from another box
    (live false ping 2026-07-24); 'a session was here recently and is now
    gone' is the only state that justifies a session-missing ping."""
    import statusbar
    import time as _time
    roots = {}
    try:
        files = list(statusbar.cache_dir(home).glob("*.json"))
    except OSError:
        return roots                    # unreadable cache dir → empty candidate set
    for f in files:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue                    # one corrupt cache entry never kills the sweep
        root, name = str(d.get("root") or ""), str(d.get("name") or "")
        if not (root and name):
            continue
        if max_age_s is not None:
            try:
                if (_time.time() - float(d.get("ts") or 0)) > max_age_s:
                    continue            # stale root — no session here lately
            except (TypeError, ValueError):
                continue
        roots[root] = name
    return roots


def _try_stash_nudge(pid, captured, text, run, dry_run):
    """Shared bounce/gk-request helper (issue #35): attempt a stash-around
    delivery of `text` for a pane that already passed the live-work / armed
    -loop / already-nudged guards but isn't bare-idle — i.e. it holds a
    draft, not a running turn. dry_run never attempts it (keeps the
    diagnostic simulation identical to the pre-#35 behavior)."""
    if dry_run:
        return False
    return deliver_with_stash(pid, text, run, captured=captured)


def _safe_to_bounce_nudge(captured, cwd, projects_dir):
    """Is the pane a session at TRUE REST — safe to type the bounce nudge?

    The live incident (2026-07-19, gatekeeper pane): CC renders a free `❯`
    prompt while WAITING on a background Workflow, so a bare idle-prompt check
    pasted the nudge 4× into a mid-review session — the user's hardest rule
    violated ('nesmie sa pastovat do promptu pocas behu'). Refuse when the
    pane shows live-work signals (an active spinner's `esc to interrupt`, a
    background `Waiting for`, a `⏳ WORKING` tail), an ARMED WORKING /goal
    (the statusline's `◎ /goal` — the label alone queues bounce tickets
    there), or a previous nudge still on screen (belt against lost dedup
    state); and when the session transcript is readable, refuse while its
    last marker is ⏳ (mid-flight even if the prompt looks free) or ❓
    (waiting on the user's answer — never interject before it).

    DONE-PARKED override (the 2026-07-20 deadlock): a SATISFIED /goal keeps
    `◎ /goal` lit although no turn will ever fire again — david's session sat
    at ✅ DONE while a bounce rotted and the gatekeeper waited on him. A pane
    whose tail shows `✅ DONE` with no live-work signal is AT REST: the
    ◎ /goal indicator alone must not block the nudge there (the ✻/✳ glyphs
    are NOT used as signals — they appear in finished-turn summaries too)."""
    if "bounce-backstop:" in captured:
        return False
    for sig in ("esc to interrupt", "Waiting for", "⏳ WORKING"):
        if sig in captured:
            return False
    if "◎ /goal" in captured and "✅ DONE" not in captured:
        return False
    tinfo = find_active_transcript(Path(projects_dir), cwd)
    if tinfo:
        m = transcript_last_marker(tinfo[0]) or ""
        if "⏳" in m or "❓" in m:
            return False
    return True


# Users whose claude sessions live in ANOTHER user's tmux — their own watchdog
# can never see the pane, so its job 8 would ALWAYS conclude "no session runs"
# and fire a false Discord ping (live incident 2026-07-20, #1727/#1732/#1827).
# The machine's primary watchdog owns the pane-driven nudge there; these users
# skip job 8 entirely. montalu (the ONLY user this ever applied to) ran inside
# NEWLEVEL's tmux via `sudo su` — historical, until the 2026-07-24 subdev
# migration (airuleset#33 + odoo-erp#1895): it now has its OWN tmux session on
# subdev, so no user needs the skip anymore. The tuple stays EMPTY (not
# deleted) so a future shared-tmux stream can be added back here.
_FOREIGN_TMUX_USERS = ()


def bounce_backstop(now, run, state, send_fn, home=None, dry_run=False,
                    gh_fetch=None, interval=BOUNCE_INTERVAL,
                    renudge=BOUNCE_RENUDGE_SECONDS, persist=None,
                    projects_dir=None, user=None):
    """Job 8 — see the section comment. Mutates state['bounce']; `persist` (the
    caller's save-state closure) is invoked BEFORE any keystroke/ping leaves
    the process — the live incident: TimeoutStartSec killed the run after the
    nudge but before run_once's save, so dedup had no memory and the same
    nudge repeated every sweep. Returns log lines. Best-effort (never raises)."""
    if user is None:
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
    if user in _FOREIGN_TMUX_USERS:
        return []                          # pane lives in another user's tmux
    b = state.get("bounce") or {}
    if (now - b.get("last_check", 0)) < interval:
        return []
    b["last_check"] = int(now)
    seen = dict(b.get("seen") or {})
    b["seen"] = seen
    state["bounce"] = b
    fetch = gh_fetch or (lambda root: _fetch_bounce_tickets(root, home))
    persist = persist or (lambda: None)
    projects_dir = projects_dir or PROJECTS_DIR
    persist()                                  # cadence stamp survives a kill
    logs = []

    panes = list_claude_panes(run)
    # candidate repos: every live pane cwd (nudge path) + cached roots (Discord
    # fallback for repos whose session is gone)
    targets = {}                               # root -> (name, pane_id | None)
    for pid, cwd in panes:
        targets[cwd] = (os.path.basename(cwd.rstrip("/")), pid)
    pane_cwds = [c for _p, c in panes]

    def _covered_by_pane(root):
        """A cached root is covered when a live pane sits INSIDE it — or when
        the root is a WORKTREE under the repo a pane sits in (the false
        'nebeží žiadna session' ping, 2026-07-23: David's claude ran in the
        MAIN checkout while the cached bounce root was the repo's
        .claude/worktrees/<agent> path; bounce tickets are per-REPO, so a
        session anywhere in the repo tree handles them)."""
        for c in pane_cwds:
            if c == root or c.startswith(root + "/"):
                return True
            if root.startswith(c + "/.claude/worktrees/"):
                return True
        return False

    for root, name in _cache_repo_roots(home).items():
        if not _covered_by_pane(root):
            targets.setdefault(root, (name, None))

    for root, (name, pid) in sorted(targets.items()):
        if not _bounce_quals(root):
            continue                           # gatekeeper: never bounce-nudged
        tickets = fetch(root)
        if tickets is None:
            continue                           # gh error → keep prior state
        if not tickets:
            seen.pop(name, None)               # clean → forget the set
            continue
        prev = seen.get(name) or {}
        same = prev.get("tickets") == tickets
        fresh = (now - prev.get("ts", 0)) < renudge
        if same and fresh:
            continue                           # already nudged/pinged this set
        tick_str = " ".join("#%d" % n for n in tickets)
        if pid:
            captured = capture_pane(pid, run)
            if pane_in_mode(pid, run) \
                    or not _safe_to_bounce_nudge(captured, root, projects_dir):
                # working / armed-loop / already-nudged pane gets NOTHING —
                # the label alone is the insertion (never interrupt mid-work).
                continue
            if not pane_at_idle_prompt(captured):
                # not bare-idle but past every live-work guard above — an
                # idle-with-a-FOREIGN-draft pane, not a running turn. Stash
                # it, deliver the nudge, let CC restore it (issue #35). Any
                # verify failure falls straight through to the pre-#35
                # silent skip.
                if not _try_stash_nudge(pid, captured, BOUNCE_NUDGE % (tick_str, name),
                                        run, dry_run):
                    continue
                seen[name] = {"tickets": tickets, "ts": int(now)}
                persist()
                logs.append("bounce-nudge %s %s" % (name, tick_str))
                continue
            seen[name] = {"tickets": tickets, "ts": int(now)}
            persist()                          # dedup memory BEFORE the keystroke
            if not dry_run:
                send_continue(pid, BOUNCE_NUDGE % (tick_str, name), run)
            logs.append("bounce-nudge %s %s" % (name, tick_str))
        else:
            body = ("⚠️ **%s: %d vrátené tikety čakajú**\n> Gatekeeper vrátil "
                    "prácu (%s), ale nebeží žiadna Claude session, ktorá by ju "
                    "spracovala. Spusti session v `%s` (autopilot ich zoberie "
                    "cez prio:bounce)." % (name, len(tickets), tick_str, root))
            seen[name] = {"tickets": tickets, "ts": int(now)}
            persist()                          # dedup memory BEFORE the ping
            send_fn(body, dedup_key="bounce:%s:%s" % (name, tick_str),
                    dry_run=dry_run)
            logs.append("bounce-ping %s %s" % (name, tick_str))
    return logs


# --------------------------------------------------------------------------- #
# gk-request backstop (job 11, airuleset #30, 2026-07-24) — the MIRROR of the
# bounce backstop: sub-dev streams need SUPERVISOR actions (box access,
# workflow re-dispatch, infra) and the only path used to be the USER as a
# middleman (3× in one day — explicitly rejected). The canonical channel is a
# ticket labeled `needs-gatekeeper` (filed via `airuleset.py gk-request`;
# no-label-permission streams degrade to the `GATEKEEPER-ACTION:` title
# prefix). This job delivers it to the supervisor WITHOUT the user: every
# ~30 min, repos this box touches are checked for open requests; a live IDLE
# supervisor pane gets a typed nudge (machine-prefixed — job 10 auto-Enters a
# lost submit), a BUSY pane gets NOTHING (the label alone is the queue
# insertion — the master loop's lane scheduler picks it up next turn), a repo
# with NO live pane gets ONE deduped Discord ping. Reduced-stream homes
# (david/marek/montalu) are never nudged — the requester must not be told
# about its own request; only a full-authority session works these.
# --------------------------------------------------------------------------- #

GKREQ_INTERVAL = 30 * 60             # min seconds between gk-request sweeps
GKREQ_RENUDGE_SECONDS = 6 * 3600     # same ticket set re-nudged at most this often
GKREQ_CACHE_MAX_AGE_S = 48 * 3600    # no-pane ping only for this-fresh roots

GKREQ_NUDGE = ("gk-request backstop: open needs-gatekeeper tickets %s in %s — "
               "a sub-dev stream is waiting on a SUPERVISOR action it cannot "
               "perform itself. Per the autopilot skill's cross-stream "
               "protocol: ACK on the ticket NOW (add the label if only the "
               "GATEKEEPER-ACTION: title carries it), perform the action, "
               "comment the result, remove the label or close, then nudge the "
               "stream's pane so it resumes without polling.")


def _gkreq_supervisor_root(cwd):
    """Only a FULL-authority session works gk-requests. A root under a reduced
    stream's HOME is skipped — nudging the REQUESTER about its own request is
    backwards (the inverse of `_bounce_quals`' gatekeeper skip)."""
    c = str(cwd or "")
    return not any(c.startswith("/home/%s/" % u)
                   for u in _REDUCED_STREAM_USERS)


def _fetch_gkreq_tickets(root, home=None):
    """Open stream→supervisor request ticket numbers for the repo at `root`:
    the `needs-gatekeeper` label query UNION the no-label-permission fallback
    (`GATEKEEPER-ACTION:` in the title — `airuleset.py gk-request`'s
    degradation for read-only-fork streams). None on any error (fail-safe —
    an auth/network hiccup must never look like 'no requests')."""
    import subprocess
    nums, env = set(), _gh_env(home)
    # NB: GitHub search TOKENIZES — the in:title query ALSO returns titles
    # merely containing the words gatekeeper+action ("… gatekeeper GitHub
    # Actions runner", the live #1768 false ping, 2026-07-24) — so the
    # fallback fetches titles and keeps only the LITERAL marker client-side.
    queries = (
        (["gh", "issue", "list", "--state", "open", "--label",
          "needs-gatekeeper", "--search", "-label:autopilot-skip",
          "-L", "100", "--json", "number"], None),
        (["gh", "issue", "list", "--state", "open", "--search",
          '"GATEKEEPER-ACTION:" in:title -label:autopilot-skip',
          "-L", "100", "--json", "number,title"],
         lambda x: str(x.get("title", "")).startswith("GATEKEEPER-ACTION:")),
    )
    for argv, keep in queries:
        try:
            r = subprocess.run(argv, cwd=root, env=env, capture_output=True,
                               text=True, timeout=8)
            if r.returncode != 0:
                return None
            nums.update(x["number"] for x in json.loads(r.stdout)
                        if keep is None or keep(x))
        except Exception:
            return None
    return sorted(nums)


def gk_request_backstop(now, run, state, send_fn, home=None, dry_run=False,
                        gh_fetch=None, interval=GKREQ_INTERVAL,
                        renudge=GKREQ_RENUDGE_SECONDS, persist=None,
                        projects_dir=None, user=None):
    """Job 11 — see the section comment. Mutates state['gkreq']; `persist` is
    invoked BEFORE any keystroke/ping leaves the process (the job-8 lesson:
    a TimeoutStartSec kill after the nudge but before save left dedup with no
    memory). Returns log lines. Best-effort (never raises)."""
    if user is None:
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            user = ""
    if user in _FOREIGN_TMUX_USERS:
        return []                          # pane lives in another user's tmux
    g = state.get("gkreq") or {}
    if (now - g.get("last_check", 0)) < interval:
        return []
    g["last_check"] = int(now)
    seen = dict(g.get("seen") or {})
    g["seen"] = seen
    state["gkreq"] = g
    fetch = gh_fetch or (lambda root: _fetch_gkreq_tickets(root, home))
    persist = persist or (lambda: None)
    projects_dir = projects_dir or PROJECTS_DIR
    persist()                                  # cadence stamp survives a kill
    logs = []

    panes = list_claude_panes(run)
    targets = {}                               # root -> (name, pane_id | None)
    for pid, cwd in panes:
        targets[cwd] = (os.path.basename(cwd.rstrip("/")), pid)
    pane_cwds = [c for _p, c in panes]

    def _covered_by_pane(root):
        for c in pane_cwds:
            if c == root or c.startswith(root + "/"):
                return True
            if root.startswith(c + "/.claude/worktrees/"):
                return True
        return False

    for root, name in _cache_repo_roots(
            home, max_age_s=GKREQ_CACHE_MAX_AGE_S).items():
        if not _covered_by_pane(root):
            targets.setdefault(root, (name, None))

    for root, (name, pid) in sorted(targets.items()):
        if not _gkreq_supervisor_root(root):
            continue                           # requester homes never nudged
        tickets = fetch(root)
        if tickets is None:
            continue                           # gh error → keep prior state
        if not tickets:
            seen.pop(name, None)               # clean → forget the set
            continue
        prev = seen.get(name) or {}
        same = prev.get("tickets") == tickets
        fresh = (now - prev.get("ts", 0)) < renudge
        if same and fresh:
            continue                           # already nudged/pinged this set
        tick_str = " ".join("#%d" % n for n in tickets)
        if pid:
            captured = capture_pane(pid, run)
            if pane_in_mode(pid, run) \
                    or not _safe_to_bounce_nudge(captured, root, projects_dir):
                # working / armed-loop pane gets NOTHING — the label alone is
                # the queue insertion (never interrupt mid-work).
                continue
            if not pane_at_idle_prompt(captured):
                # idle-with-a-FOREIGN-draft, not a running turn — stash it,
                # deliver, let CC restore it (issue #35). Any verify failure
                # falls straight through to the pre-#35 silent skip.
                if not _try_stash_nudge(pid, captured, GKREQ_NUDGE % (tick_str, name),
                                        run, dry_run):
                    continue
                seen[name] = {"tickets": tickets, "ts": int(now)}
                persist()
                logs.append("gkreq-nudge %s %s" % (name, tick_str))
                continue
            seen[name] = {"tickets": tickets, "ts": int(now)}
            persist()                          # dedup memory BEFORE the keystroke
            if not dry_run:
                send_continue(pid, GKREQ_NUDGE % (tick_str, name), run)
            logs.append("gkreq-nudge %s %s" % (name, tick_str))
        else:
            body = ("⚠️ **%s: %d needs-gatekeeper žiadostí čaká**\n> Sub-dev "
                    "stream žiada akciu supervízora (%s), ale nebeží žiadna "
                    "supervízorská Claude session. Spusti session v `%s` — "
                    "master loop si žiadosti zoberie."
                    % (name, len(tickets), tick_str, root))
            seen[name] = {"tickets": tickets, "ts": int(now)}
            persist()                          # dedup memory BEFORE the ping
            send_fn(body, dedup_key="gkreq:%s:%s" % (name, tick_str),
                    dry_run=dry_run)
            logs.append("gkreq-ping %s %s" % (name, tick_str))
    return logs


# --------------------------------------------------------------------------- #
# /goal auto-arm (job 9, 2026-07-20) — the printed template pastes itself.
# /autopilot and /process-subdev end by PRINTING the /goal template and asking
# the user to paste it — the one manual step left in every stream ("dost mi
# vadi ze musim pracne vsade chodit a zadavat goal — malo by sa to samo").
# The watchdog performs the paste: an IDLE pane whose tail asks to paste a
# /goal and carries the printed line gets it typed + submitted — the exact
# keystrokes the user would make. Safety: bare empty prompt only (never over
# user text), never when a goal is already armed (◎ /goal), never into a busy
# pane, one arm per pane per window.
# --------------------------------------------------------------------------- #

GOAL_ARM_WINDOW_S = 10 * 60          # one auto-arm per pane per this window
_ARM_QUESTION_RX = re.compile(
    r"❓[^\n]*NEEDS YOU[^\n]*/goal|"          # vlož /goal riadok / pastni /goal
    r"(vlo\S|pastni|paste)[^\n]{0,40}/goal", re.I)


PWEDGE_MIN_IDLE_S = 30 * 60      # transcript must be this stale before a wedge ping
PWEDGE_SWEEPS = 2                # identical box text across this many sweeps
# Per-pane ping cooldown (issue #35) — a re-WRAPPED draft changes its hash,
# which used to read as a brand-new episode and re-ping the same pane
# ("často mi chodí" spam). The cooldown is keyed on the PANE, independent of
# the hash-tracking dict, so it survives a hash change.
PWEDGE_PING_COOLDOWN_S = 24 * 3600
# Canonical CROSS-STREAM machine-nudge prefix (autopilot skill protocol) — a
# frozen draft starting with it is MACHINE text whose submission is always the
# intent, so job 10 auto-Enters it instead of pinging (the gk→montalu nudge
# kept losing its Enter and sat unsubmitted for hours, 3× in 24 h).
MACHINE_NUDGE_PREFIX = ("Priorita: prio:bounce", "bounce-backstop:",
                        "gk-request backstop:")


def _session_is_waiting(tpath, max_lines=50):
    """True iff the session's LAST assistant transcript entry's text
    contains 'NEEDS YOU' — i.e. it ended on a ❓ block, genuinely blocked on
    the user. Feeds job 10's `waiting` gate (issue #35): a parked draft only
    pings while the session is ACTUALLY waiting on it."""
    for entry in reversed(_iter_jsonl_tail(tpath, max_lines)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        return "NEEDS YOU" in (_entry_text(entry) or "")
    return False


def prompt_wedge_check(now, state, pid, captured, tmtime, owner, project,
                       send_fn, dry_run=False, run=None, waiting=True):
    """Job 10 (#20, reworked #35) — queued-prompt-wedge detection, PING-FIRST.

    Text sitting in the input box (a submitted-but-stuck queued prompt, or an
    abandoned draft) blocks every keystroke delivery (job 7 draft protection)
    and can park a session for hours (gk+david 2026-07-20; the gk-master
    'nechať ako je' draft). Detection: the box text is BYTE-identical across
    >= PWEDGE_SWEEPS sweeps AND the transcript is >= PWEDGE_MIN_IDLE_S stale
    AND the pane shows no live-work signals.

    Three refinements from issue #35 on top of the original ping-first
    design:
      - MACHINE recognition now covers BOTH the static cross-stream prefixes
        AND job 7's own compose-reply text (`state['dreply_typed']`, set by
        `_record_dreply_typed`) — a swallowed delivery of OUR OWN text is
        auto-submitted (Escape+Enter — #36), never pinged as if it were a
        foreign draft.
      - `waiting` (default True — callers that can't cheaply determine it,
        e.g. a sudo-hosted foreign transcript, keep the old always-eligible
        behavior): a genuine USER draft pings NOTHING while the session is
        NOT waiting on the user (stash-around delivery, #35, means a parked
        draft no longer blocks anything urgently) — but state keeps
        tracking, so a later flip to `waiting=True` on the SAME stable draft
        still pings.
      - A per-pane cooldown (`PWEDGE_PING_COOLDOWN_S`) suppresses a re-ping
        for `pid` regardless of the draft's hash changing (a re-wrap).

    Deliberately NO auto-Enter on a genuine foreign draft (the ticket's
    decision — a machine must never submit a half-typed user draft)."""
    key = "pwedge:" + pid
    txt = _input_line_text(captured)
    if not txt:
        state.pop(key, None)
        return []
    machine = txt.startswith(MACHINE_NUDGE_PREFIX) or _is_dreply_machine_text(state, pid, txt)
    if not machine and ("esc to interrupt" in (captured or "")
                        or "Waiting for" in (captured or "")
                        or now - tmtime < PWEDGE_MIN_IDLE_S):
        # a USER draft gets the conservative ping-first handling only once the
        # session is provably at rest; a MACHINE nudge is submit-anytime.
        state.pop(key, None)
        return []
    import hashlib
    h = hashlib.sha1(txt.encode("utf-8")).hexdigest()[:12]
    st = state.get(key)
    st = dict(st) if isinstance(st, dict) else {}
    if st.get("hash") != h:
        state[key] = {"hash": h, "n": 1, "pinged": False}
        return []
    st["n"] = int(st.get("n") or 1) + 1
    state[key] = st
    if st["n"] < PWEDGE_SWEEPS or st.get("pinged"):
        return []
    if machine:
        if not dry_run and run and not pane_in_mode(pid, run):
            # Escape first (issue #36) — a swallowed submit while the
            # agent-strip selector holds focus makes a bare Enter navigate
            # instead of submit; never a SECOND Escape (issue #35: deletes a
            # draft permanently).
            run(["tmux", "send-keys", "-t", pid, "Escape"])
            run(["tmux", "send-keys", "-t", pid, "Enter"])
        state.pop(key, None)     # still stuck → re-tracks and retries in 2 sweeps
        return ["machine-nudge submit %s (%s)" % (pid, project)]
    if not waiting:
        # tracked but nothing urgent — the session isn't blocked on the
        # user, and a stash-around delivery (#35) can still reach it. A
        # later poll where `waiting` flips True (same stable draft) pings.
        return ["pwedge-parked (not waiting) %s (%s)" % (pid, project)]
    ping_key = "pwedge-ping:" + pid
    last_ping = state.get(ping_key)
    if last_ping and now - last_ping < PWEDGE_PING_COOLDOWN_S:
        st["pinged"] = True
        return ["pwedge-suppressed (cooldown) %s (%s)" % (pid, project)]
    st["pinged"] = True
    state[ping_key] = now
    where = _pane_location(pid, run) if run else ""
    loc = " (%s)" % where if where else ""
    send_fn("⚠️ **%s**%s — v okne visí NEODOSLANÝ text („%s…“) a session stojí "
            "vyše 30 minút. Môže ísť aj o odložený (Ctrl+S stash) príkaz, "
            "ktorý sa po skončení bežiaceho ťahu vráti sám. Stlač v tom okne "
            "Enter (text sa odošle) alebo ho zmaž — dovtedy sa doň nedá nič "
            "doručiť."
            % (project, loc, txt[:60]),
            owner=owner or None,
            dedup_key="pwedge:%s:%s" % (pid, h), dry_run=dry_run)
    return ["prompt-wedge ping %s (%s)" % (pid, project)]


_GOAL_CONT_OK = ("```", "─", "•", "**", "❓", "❯", "⎿", "●", "✻")

# CC's AMBIENT status while background subagents run ("✻ Waiting for N
# background agents to finish") stays on screen although the turn has ENDED
# and the prompt is a free bare `❯` — the exact autopilot shape (main idle,
# worker in background; `pane_at_idle_prompt` passes by design). Job 9 must
# not read it as live work: the arm question at the tail IS the session
# asking for the paste, and typing /goal there is what the user would do by
# hand (restreamer incident 2026-07-24 — the goal never armed while any
# background worker ran). Every OTHER `Waiting for` still blocks.
_BG_AGENTS_WAIT_RX = re.compile(
    r"Waiting for \d+ background agents? to finish")


def _above_input_box(cap):
    """The conversation region ABOVE the input box: trailing chrome peeled
    (statusline / mode hint / borders / the AGENT STRIP — `_is_bottom_chrome`),
    then the `❯` input-box line itself dropped. A strip row's activity label is
    ARBITRARY model-generated text — `◯ autopilot-worker  Waiting for
    deploy-prod.yml jobs` false-matched the busy guard (gk incident
    2026-07-24) — and the strip grows one row per worker, crowding the arm
    question out of a fixed tail window taken from the raw capture. Nothing
    below the input box is ever TURN state; job 9 scans only this region.
    The peel caps at 40 chrome lines (mirroring `_has_free_prompt`) — a
    taller-than-40-row strip leaves chrome in the region and the pane is
    simply skipped that sweep (missed arm = the safe direction, never a
    keystroke into a misread pane)."""
    lines = cap.splitlines()
    i = len(lines)
    n = 0
    while i > 0 and _is_bottom_chrome(lines[i - 1].strip()) and n < 40:
        i -= 1
        n += 1
    if i > 0 and lines[i - 1].strip().startswith("❯"):
        i -= 1
    return "\n".join(lines[:i])


def _transcript_goal_line(path, max_lines=400):
    """The NEWEST `/goal …` line printed by the ASSISTANT in the transcript
    tail — the EXACT template bytes. The pane RENDERING hard-wraps a long goal
    (a code block re-flowed by CC), so the viewport fragment is truncated (the
    166-of-3100-char gk arm, 2026-07-20); the transcript is never wrapped."""
    best = None
    try:
        for entry in _iter_jsonl_tail(path, max_lines):
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            for ln in _entry_text(entry).splitlines():
                ln = ln.strip()
                if ln.startswith("/goal "):
                    best = ln
    except Exception:
        return None
    return best


def _foreign_transcript_goal(cwd):
    """Full `/goal ` line from ANOTHER user's newest transcript for `cwd` —
    the sudo-hosted stream case (montalu claude in newlevel's tmux): the pane
    is visible here but the transcript lives under the foreign HOME. Uses
    `sudo -n -u <user>` (passwordless on the boxes where this shape exists);
    every failure returns None (the caller then refuses to arm a fragment)."""
    import getpass
    import subprocess
    m2 = re.match(r"/home/([a-z0-9_-]+)/", str(cwd) + "/")
    if not m2:
        return None
    user = m2.group(1)
    try:
        if user == getpass.getuser():
            return None
    except Exception:
        return None
    script = (
        "import glob,json,os,sys\n"
        "d=os.path.expanduser('~/.claude/projects/'+"
        "''.join('-' if c in '/._' else c for c in %r))\n"
        "fs=sorted(glob.glob(d+'/*.jsonl'),key=os.path.getmtime)\n"
        "best=None\n"
        "for line in open(fs[-1]) if fs else []:\n"
        "    try: e=json.loads(line)\n"
        "    except Exception: continue\n"
        "    if e.get('type')!='assistant': continue\n"
        "    c=e.get('message',{}).get('content')\n"
        "    ts=[c] if isinstance(c,str) else ["
        "b.get('text','') for b in c or [] "
        "if isinstance(b,dict) and b.get('type')=='text']\n"
        "    for t in ts:\n"
        "        for ln in t.splitlines():\n"
        "            ln=ln.strip()\n"
        "            if ln.startswith('/goal '): best=ln\n"
        "print(best or '')\n" % str(cwd))
    try:
        p = subprocess.run(["sudo", "-n", "-u", user, "python3", "-c", script],
                           capture_output=True, text=True, timeout=15)
        line = (p.stdout or "").strip()
        return line if p.returncode == 0 and line.startswith("/goal ") else None
    except Exception:
        return None


def _viewport_goal_wrapped(cap, frag):
    """True if the viewport `/goal` line is a hard-wrapped FRAGMENT — the next
    non-empty rendered line continues its prose instead of being structure
    (fence / border / bullet / arm question / prompt / chrome)."""
    lines = cap.splitlines()
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == frag:
            idx = i
    if idx is None:
        return False
    for ln in lines[idx + 1:]:
        s = ln.strip()
        if not s:
            continue
        return not s.startswith(_GOAL_CONT_OK)
    return False


def goal_autoarm(now, run, state, dry_run=False, projects_dir=None):
    """Job 9 — see the section comment. Mutates state['goalarm']; returns log
    lines. Best-effort (never raises)."""
    ga = state.get("goalarm") or {}
    logs = []
    projects_dir = projects_dir or PROJECTS_DIR
    for pid, cwd in list_claude_panes(run):
        if (now - ga.get(pid, 0)) < GOAL_ARM_WINDOW_S:
            continue
        # VISIBLE VIEWPORT ONLY (no -S): after a claude restart the tmux
        # SCROLLBACK still holds the dead session's arm question + /goal line
        # — arming those into the fresh session typed a stale, wrong goal
        # (gk incident 2026-07-20). CC redraws its own screen, so the viewport
        # is always the CURRENT session's content; history never arms anything.
        cap = run(["tmux", "capture-pane", "-p", "-J", "-t", pid]) or ""
        # Arm-question + busy detection run on the CONVERSATION only — the
        # chrome below the input box (agent strip, statusline) is never turn
        # state and its arbitrary labels/bulk broke both checks (gk 2026-07-24).
        tail = _above_input_box(cap)[-1500:]
        if not _ARM_QUESTION_RX.search(tail):
            continue
        # NB: an armed-goal indicator (◎ /goal) does NOT block — a resolved
        # /goal cycle re-prints the arm question while the OLD indicator is
        # still lit, and typing /goal safely replaces the old goal (the gk
        # re-arm incident, 2026-07-20). The tail arm question is authoritative.
        busy_tail = _BG_AGENTS_WAIT_RX.sub("", tail)
        if "esc to interrupt" in busy_tail or "Waiting for" in busy_tail:
            continue                       # live work on screen — not at rest
        if not pane_at_idle_prompt(cap) or pane_in_mode(pid, run):
            continue                       # busy, or user text in the box
        goals = re.findall(r"^\s*(/goal \S.*)$", cap, re.M)
        if not goals:
            continue
        frag = goals[-1].strip()
        # The rendered viewport hard-wraps long goals — arm the TRANSCRIPT's
        # exact bytes when available; the fragment only when provably whole.
        full = None
        tr = find_active_transcript(projects_dir, cwd)
        if tr:
            full = _transcript_goal_line(tr[0])
        if full is None:
            # sudo-hosted stream pane: the transcript lives under the FOREIGN
            # user's HOME — read it via sudo -n (best-effort).
            full = _foreign_transcript_goal(cwd)
        if full is None:
            if _viewport_goal_wrapped(cap, frag):
                logs.append("goal wrapped + no transcript — not arming %s (%s)"
                            % (pid, os.path.basename(cwd.rstrip("/"))))
                continue
            full = frag
        ga[pid] = int(now)
        state["goalarm"] = ga          # key exists only once something armed
        if not dry_run:
            send_continue(pid, full, run)
        logs.append("goal-autoarm %s (%s)" % (pid,
                                              os.path.basename(cwd.rstrip("/"))))
    return logs


# --------------------------------------------------------------------------- #
# Job 12 — MODEL RECONCILE, restart-based (#42 rework, 2026-07-25). The
# managed default (`MANAGED_MODEL` in airuleset.py) only binds a NEW Claude
# Code session — a long-lived session keeps whatever model it started on.
#
# The ORIGINAL version of this job (#37) tried to fix that by typing
# `/model <target>` into the stale session — but a LIVE incident
# (gatekeeper, 2026-07-25) proved that is structurally futile: a running
# session's AVAILABLE-MODEL LIST is fixed at its own start, so a model
# released AFTER that start is simply absent from the list and `/model` can
# never select it, no matter how many times it's retried (every attempt
# just burned a prompt-cache-invalidating context re-read for nothing). The
# gk box ran CC 2.1.220 (which DOES offer the newer model) with
# `settings.json` requesting it, yet a resumed (`-c`) session stayed on the
# stale model; opening `/model` by hand there confirmed the newer model was
# not even LISTED. Only a session RESTART fixed it.
#
# So this job no longer types `/model` at all. Instead, for every live
# claude/node/bun pane whose newest transcript's last model is still
# fable/opus-4, and ONLY when the pane is genuinely SAFE-TO-RESTART (idle at
# a bare prompt, no draft, no open dialog, not in copy-mode, AND no
# in-flight background agent — a restart would KILL a running worker), it
# restarts the session: `/exit`, wait for the shell to come back, relaunch
# `claude` (the managed bashrc function bakes `--model MANAGED_MODEL` into
# EVERY launch, airuleset.py — this job never passes a model itself), then
# accept Claude Code's "Resume from summary" dialog for a large prior
# session (verified live: 701.9k tokens -> 175k) or simply proceed when no
# dialog appears (a small/absent prior session). A pane that is NOT safe is
# left untouched and recorded as "needs restart" for a later sweep — never
# forced, never nudged past a live worker.
#
# Reuses the SAME pane helpers every other keystroke-sending job here uses
# (`pane_in_mode`, `pane_waiting_on_user`, `_input_line_text`,
# `pane_at_idle_prompt`, `_pane_location`, `_strip_selected`) rather than a
# parallel chrome-detector, plus the module's own agent-strip / "Waiting for
# N background agents" detection (`_BG_AGENTS_WAIT_RX`, job 9) for the NEW
# in-flight-agent guard this rework adds.
# --------------------------------------------------------------------------- #

MODEL_RECONCILE_MAX_ATTEMPTS = 3     # cap retries per session — a live incident (gk,
                                     # 2026-07-25) showed the SAME pane FAIL every ~60s
                                     # sweep forever — each FAIL released the dedup
                                     # claim, so every sweep retried, burning a full
                                     # context re-read (prompt-cache invalidation) on
                                     # every attempt. Bounded, then GIVE UP for good.

# `/exit` hands the pane back to its shell — poll (bounded) for the
# foreground process to leave claude/node/bun.
MODEL_RESTART_EXIT_POLL_S = 1
MODEL_RESTART_EXIT_MAX_POLLS = 10
# After the `claude` relaunch, poll (bounded) for EITHER the resume-from-
# summary dialog or a bare idle prompt with no dialog (nothing to resume).
MODEL_RESTART_LAUNCH_POLL_S = 1
MODEL_RESTART_LAUNCH_MAX_POLLS = 15
# After accepting the resume dialog, poll (bounded) for the resumed session
# to settle at an idle prompt.
MODEL_RESTART_VERIFY_POLL_S = 1
MODEL_RESTART_VERIFY_MAX_POLLS = 10

# Claude Code's large-prior-session resume dialog (verified live, gk
# 2026-07-25):
#   This session is 1h 21m old and 701.9k tokens.
#   Resuming the full session will consume a substantial portion of your
#   usage limits. We recommend resuming from a summary.
#   ❯ 1. Resume from summary (recommended)
#     2. Resume full session as-is
#     3. Don't ask me again
# Option 1 is pre-selected — a single Enter accepts it. A small/absent
# prior session shows no such dialog at all; the caller proceeds directly.
_RESUME_DIALOG_RX = re.compile(r"Resume from summary", re.I)

# An agent-strip row for an OTHER (non-main) agent — `◯ <agent>` or its
# SELECTED form `❯ ◯ <agent>` (issue #36). Deliberately excludes the bare
# `● main` row: that renders even with zero subagents running, so it alone
# does not mean a worker is in flight.
_AGENT_STRIP_ROW_RX = re.compile(r"^(?:❯\s*)?◯\s+\S")


def _reconcile_candidate_panes(run):
    """[(pane_id, cwd, cmd)] for every tmux pane whose foreground command is
    claude/node/bun — the reference implementation's exact filter (some CC
    installs surface the wrapper's process name, e.g. 'node', as
    pane_current_command, not 'claude'). Deliberately its OWN enumeration,
    NOT `list_claude_panes` (which also resolves sudo-hosted stream panes
    and is relied on by every OTHER keystroke-sending job in this file) —
    widening THAT filter to node/bun would make an unrelated node/bun pane
    look like a live Claude session to every job, not just this one.

    Deduped by pane_id (same discipline as `list_claude_panes`): a GROUPED
    tmux session shares its underlying pane with every session name it is
    linked under, so `tmux list-panes -a` lists the SAME pane_id once per
    session name — confirmed live on dev1 (marek's grouped sessions each
    re-list every shared window). Without the dedup, a single live pane
    would be visited twice per sweep (harmless — the second visit's dedup
    check on `state['modelswitch']` always sees the first visit's claim —
    but wasteful and noisy in the logs)."""
    run = run or _default_run
    out = run(["tmux", "list-panes", "-a", "-F",
               "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"])
    seen = set()
    res = []
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid, cmd, cwd = parts
        if not pid or pid in seen:
            continue
        if cmd not in ("claude", "node", "bun"):
            continue
        seen.add(pid)
        res.append((pid, cwd, cmd))
    return res


def _pane_has_bg_agent(captured):
    """True if the captured pane shows a live BACKGROUND AGENT — a
    `◯ <agent>` agent-strip row (or its SELECTED form `❯ ◯ <agent>`, issue
    #36), or the ambient "Waiting for N background agents to finish" line
    CC prints while one runs. A restart (`/exit`) would KILL any in-flight
    worker — this is the one guard job 12 needs that no OTHER
    keystroke-sending job in this file needs, since none of them ever kill
    the pane's process (#42)."""
    if not captured:
        return False
    if _BG_AGENTS_WAIT_RX.search(captured):
        return True
    for ln in captured.splitlines():
        if _AGENT_STRIP_ROW_RX.match(ln.strip()):
            return True
    return False


def _mark_modelswitch_pending(state, sid, loc, model, reason, now):
    """Persist that `sid` still needs a restart but the pane isn't safe to
    attempt it on YET (#42 item 4: 'record the session as needs-restart in
    state and log it; re-evaluate on later sweeps'). Overwritten every
    sweep the pane stays unsafe; cleared the moment it becomes safe."""
    pending = state.get("modelswitch_pending") or {}
    pending[sid] = {"loc": loc, "model": model, "reason": reason,
                    "last_seen": int(now)}
    state["modelswitch_pending"] = pending


def _clear_modelswitch_pending(state, sid):
    pending = state.get("modelswitch_pending") or {}
    if sid in pending:
        pending.pop(sid, None)
        state["modelswitch_pending"] = pending


def _wait_for_shell_returns(pid, run, sleep_fn):
    """Poll (bounded) for `/exit` to hand the pane back to its shell — the
    foreground process leaves claude/node/bun. Never an unbounded wait
    (`no-timeout-band-aids.md`): `MODEL_RESTART_EXIT_MAX_POLLS` caps it."""
    for _ in range(MODEL_RESTART_EXIT_MAX_POLLS):
        sleep_fn(MODEL_RESTART_EXIT_POLL_S)
        cmd = (run(["tmux", "display-message", "-p", "-t", pid,
                    "#{pane_current_command}"]) or "").strip()
        if cmd and cmd not in ("claude", "node", "bun"):
            return True
    return False


def _wait_for_relaunch(pid, run, sleep_fn):
    """Poll (bounded) after the `claude` relaunch keystrokes for EITHER the
    "Resume from summary" dialog or a bare idle `❯` with no dialog text (a
    small/absent prior session — nothing to resume). Returns
    `("dialog"|"idle", last_captured)` on success, `(None, last_captured)`
    once `MODEL_RESTART_LAUNCH_MAX_POLLS` is exceeded."""
    captured = ""
    for _ in range(MODEL_RESTART_LAUNCH_MAX_POLLS):
        sleep_fn(MODEL_RESTART_LAUNCH_POLL_S)
        captured = capture_pane(pid, run, lines=40) or ""
        if _RESUME_DIALOG_RX.search(captured):
            return "dialog", captured
        if pane_at_idle_prompt(captured):
            return "idle", captured
    return None, captured


def _wait_for_idle_after_dialog(pid, run, sleep_fn):
    """Poll (bounded) for the resumed session to settle at a bare idle `❯`
    after accepting the "Resume from summary" dialog."""
    for _ in range(MODEL_RESTART_VERIFY_MAX_POLLS):
        sleep_fn(MODEL_RESTART_VERIFY_POLL_S)
        captured = capture_pane(pid, run, lines=40) or ""
        if pane_at_idle_prompt(captured):
            return True
    return False


# #79 (2026-07-26 live incident) -- a bare `claude` typed into a shell OLDER
# than #77's launcher rewrite resolves to whatever `.bashrc` that shell
# already had loaded at ITS OWN start; bash reads `.bashrc` exactly once,
# at shell start, and never again. A shell started before #77 shipped still
# holds the OLD fat `claude()` function with `--settings
# '{"ultracode":true}'` baked in -- live-verified on `david:0.0`@subdev
# (shell started Jul 22, well before #77): `type claude` showed the frozen
# old function even after #77's `.bashrc` rewrite had long since deployed.
# `_restart_pane` is the ONE place that launches `claude` from OUTSIDE an
# interactive user keystroke (job 12 model-reconcile, job 18
# hooks-reconcile) -- re-sourcing `.bashrc` in the SAME command guarantees
# the CURRENT wrapper resolves regardless of how old the target shell is.
# Any FUTURE mechanism that launches `claude` from a pane must go through
# this same shape (or call `_restart_pane` itself) -- there must never be a
# second place typing a bare `claude`.
RELAUNCH_CMD = "source ~/.bashrc && claude"


def _restart_pane(pid, run, sleep_fn, captured):
    """Perform the restart sequence for a pane the caller already proved
    SAFE-TO-RESTART (idle, no draft, no open dialog, not in-mode, no
    in-flight background agent). Returns `(ok, reason)`.

    Keystrokes, in order:
      1. `/exit` + Enter — escaping the agent-strip selector FIRST (issue
         #36) if it happens to hold focus in the `captured` frame the
         caller's guards already read (no extra capture-pane round-trip
         needed for this step — it's the SAME frame).
      2. Poll (bounded) for the shell prompt to return.
      3. `RELAUNCH_CMD` (`source ~/.bashrc && claude`) + Enter — re-sourcing
         `.bashrc` FIRST guarantees the CURRENT managed wrapper resolves
         even in a shell that predates the last `.bashrc` deploy (#79) —
         a bare `claude` would resolve to whatever that shell already had
         loaded at its own start. The managed bashrc function bakes
         `--model MANAGED_MODEL` into every launch (airuleset.py); this job
         never passes a model itself.
      4. Poll (bounded) for EITHER the "Resume from summary" dialog (a
         large prior session) or a bare idle `❯` with no dialog (a
         small/absent prior session).
      5. If the dialog appeared: ONE more Enter accepts the pre-selected
         "Resume from summary" option, then poll (bounded) for the resumed
         session to settle at an idle `❯`.

    NEVER two consecutive Escapes (issue #35) — this sequence sends at most
    ONE Escape (step 1, only when the strip selector holds focus), and
    every other keystroke is a real command or a lone Enter."""
    if _strip_selected(captured):
        run(["tmux", "send-keys", "-t", pid, "Escape"])
    run(["tmux", "send-keys", "-t", pid, "-l", "/exit"])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    if not _wait_for_shell_returns(pid, run, sleep_fn):
        return False, "shell did not return after /exit"
    run(["tmux", "send-keys", "-t", pid, "-l", RELAUNCH_CMD])
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    kind, _cap = _wait_for_relaunch(pid, run, sleep_fn)
    if kind is None:
        return False, "relaunch did not render (no resume dialog, no idle prompt)"
    if kind == "dialog":
        run(["tmux", "send-keys", "-t", pid, "Enter"])   # accept "Resume from summary"
        if not _wait_for_idle_after_dialog(pid, run, sleep_fn):
            return False, "session did not settle idle after the resume dialog"
    return True, "restarted"


def model_reconcile(now, run, state, target_model, dry_run=False,
                    projects_dir=None, sleep_fn=None, handled=None):
    """Job 12 — see the section comment. `target_model` MUST be passed in —
    cmd_watchdog passes `MANAGED_MODEL`; this module never hardcodes a model
    literal. A falsy `target_model` disables the job entirely (mirrors the
    other optional-fetch-gated jobs: usage_fetch=None skips job 3, etc.).

    `handled` (#70, optional): a mutable set run_once passes so job 18
    (hooks-reconcile, below) can see — within the SAME sweep — that this
    session is ALREADY being restarted here for a MODEL change, and skip
    firing a SECOND restart for a hooks-config change landing in the same
    sweep. Populated ONLY at the real restart CLAIM (never in `dry_run` —
    nothing is actually happening then, so there is nothing to coalesce
    against — and never for a session that was merely SKIPPED this sweep).

    Dedup: `state['modelswitch'][<session id>]` is CLAIMED right before the
    restart keystrokes are sent and is only ever DELETED again on a FAILED
    restart BELOW the attempt cap — so a real success is never retried, and
    a failed attempt (shell didn't return, relaunch never rendered, …) is
    retried on a later sweep instead of being stuck forever either way.
    `state['modelswitch_attempts'][<session id>]` counts failures; once it
    reaches `MODEL_RECONCILE_MAX_ATTEMPTS` the session GIVES UP for good
    (the claim is kept, never released again) — a live incident (gk,
    2026-07-25) showed a FAILing pane retried on EVERY ~60s sweep forever,
    each attempt burning a full context re-read (prompt-cache invalidation)
    for nothing.

    `state['modelswitch_pending'][<session id>]` records a session that
    still needs a restart but wasn't SAFE-TO-RESTART this sweep (busy,
    drafting, dialog open, in copy-mode, or a background agent in flight) —
    never forced, never nudged past live work; re-evaluated next sweep.

    Never restarts a pane that is in copy-mode, showing an open dialog,
    mid-turn/busy, holding an unsent draft, or running a background agent
    (a restart would KILL it) — `_classify_boundary` distinguishes
    busy (a real boundary, not `❯`-shaped) vs no-input-line (no boundary
    locatable at all, issue #46) vs draft (non-empty) vs safe-to-type (empty
    bare `❯`), the same boundary-line discipline every other keystroke-sending
    job here relies on; `_pane_has_bg_agent` is the NEW guard this rework
    adds. Best-effort: exceptions are the caller's (run_once's)
    responsibility to catch, same as every other job."""
    if not target_model:
        return []
    run = run or _default_run
    sleep_fn = sleep_fn or time.sleep
    projects_dir = projects_dir or PROJECTS_DIR
    reconciled = state.get("modelswitch") or {}
    attempts_map = state.get("modelswitch_attempts") or {}
    logs = []
    for pid, cwd, cmd in _reconcile_candidate_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, _tmtime = tinfo
        sid = tpath.stem
        model = transcript_last_model(tpath)
        ml = model.lower()
        if not model or ("fable" not in ml and "opus-4" not in ml):
            continue                      # already-on-target / not a tracked tier
        if reconciled.get(sid):
            continue                      # already restarted this session — never retry
        loc = _pane_location(pid, run) or pid
        if pane_in_mode(pid, run):
            logs.append("skip in-mode (model-reconcile) %s %s" % (loc, model))
            _mark_modelswitch_pending(state, sid, loc, model, "in-mode", now)
            continue
        captured = capture_pane(pid, run, lines=40)
        if pane_waiting_on_user(captured):
            logs.append("skip dialog-open (model-reconcile) %s %s" % (loc, model))
            _mark_modelswitch_pending(state, sid, loc, model, "dialog-open", now)
            continue
        kind, draft = _classify_boundary(captured)
        if kind == "no-input-line":
            logs.append("skip no-input-line (model-reconcile) %s %s" % (loc, model))
            _mark_modelswitch_pending(state, sid, loc, model, "no-input-line", now)
            continue
        if kind == "busy":
            logs.append("skip busy (model-reconcile) %s %s" % (loc, model))
            _mark_modelswitch_pending(state, sid, loc, model, "busy", now)
            continue
        if draft:
            logs.append("skip draft (model-reconcile) %s %s: %r"
                        % (loc, model, draft[:40]))
            _mark_modelswitch_pending(state, sid, loc, model, "draft", now)
            continue
        if not pane_at_idle_prompt(captured):
            logs.append("skip not-idle (model-reconcile) %s %s" % (loc, model))
            _mark_modelswitch_pending(state, sid, loc, model, "not-idle", now)
            continue
        if _pane_has_bg_agent(captured):
            logs.append("skip bg-agent (model-reconcile) %s %s" % (loc, model))
            _mark_modelswitch_pending(state, sid, loc, model, "bg-agent", now)
            continue
        _clear_modelswitch_pending(state, sid)
        if dry_run:
            logs.append("READY (model-reconcile) %s %s -> %s (restart)"
                        % (loc, model, target_model))
            continue
        reconciled[sid] = True
        state["modelswitch"] = reconciled
        if handled is not None:
            handled.add(sid)
        ok, reason = _restart_pane(pid, run, sleep_fn, captured)
        if ok:
            logs.append("OK (model-reconcile) %s %s -> %s (restarted)"
                        % (loc, model, target_model))
            attempts_map.pop(sid, None)
            state["modelswitch_attempts"] = attempts_map
        else:
            attempts = attempts_map.get(sid, 0) + 1
            attempts_map[sid] = attempts
            state["modelswitch_attempts"] = attempts_map
            if attempts >= MODEL_RECONCILE_MAX_ATTEMPTS:
                # Bounded — never retry this session again. Keep `reconciled[sid]`
                # claimed (same as a real OK) so the dedup check at the top of the
                # loop skips it forever, instead of releasing it for yet another
                # doomed retry.
                logs.append("GAVE UP (model-reconcile) %s %s -> %s after %d attempts (%s)"
                            % (loc, model, target_model, attempts, reason))
            else:
                del reconciled[sid]
                state["modelswitch"] = reconciled
                logs.append("FAIL (model-reconcile) %s %s -> %s (%s, attempt %d/%d)"
                            % (loc, model, target_model, reason, attempts,
                               MODEL_RECONCILE_MAX_ATTEMPTS))
    return logs


# --------------------------------------------------------------------------- #
# Job 13 — HOURLY BURN SNAPSHOT (#37 follow-up, 2026-07-25). The user's
# standing directive: change things one step at a time and measure hourly
# whether it got better or worse, AUTOMATICALLY — he must not have to check
# anything himself. Once per hour, append this host's $/msgs/avg-context/
# by-model row for the PREVIOUS full hour to `burn-history/snapshots.jsonl`
# — the raw feed `airuleset.py burn --compare` reads. Reuses
# `burn.hourly_snapshot()` (itself built on `burn.scan()`'s existing
# per-line parser) — no duplicate transcript parsing anywhere in this path.
# --------------------------------------------------------------------------- #


def burn_snapshot_job(now, state, snapshot_path=None, transcripts_root=None,
                      host=None, user=None, dry_run=False):
    """Job 13 — see the section comment. Guarded by `state['burn_snapshot_hour']`
    so the 60s sweep cadence writes AT MOST once per UTC-epoch hour, no matter
    how many times this fires inside that hour. `dry_run`: compute + log, but
    never write the file or claim the hour (so a later real sweep still
    writes it). Best-effort: exceptions are the caller's (run_once's)
    responsibility to catch, same as every other job."""
    import burn as burn_mod
    hour_bucket = int(now // 3600)
    if state.get("burn_snapshot_hour") == hour_bucket:
        return []
    now_dt = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    row = burn_mod.hourly_snapshot(now_dt, root=transcripts_root, host=host, user=user)
    if dry_run:
        return ["[dry-run] burn-snapshot %s $%.2f %d msgs avg_ctx=%d (not written)"
               % (row["host"], row["usd"], row["msgs"], row["avg_ctx"])]
    path = Path(snapshot_path or burn_mod.snapshots_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    state["burn_snapshot_hour"] = hour_bucket
    return ["burn-snapshot %s $%.2f %d msgs avg_ctx=%d -> %s"
           % (row["host"], row["usd"], row["msgs"], row["avg_ctx"], path)]


# --------------------------------------------------------------------------- #
# Job 16 — HOURLY FLEET BURN (#55, 2026-07-25 follow-up to job 13). The
# user's ask: "zacat aj v hodinovych intervaloch vyhodnocovat stav spotreby
# tokenov cez monitorovanu sadu claude targetov" — job 13 above only ever
# measures THIS box. This job runs ONLY on the coordinator (cmd_watchdog
# wires `fleet_fetch` ONLY when `os.uname().nodename == "dev1"` — every OTHER
# managed box already writes ITS OWN hourly row via job 13, so this job just
# TAILS each box's already-written `snapshots.jsonl` over ssh
# (`airuleset._watchdog_fleet_fetch`, injected as `fleet_fetch` — never
# re-scans transcripts remotely) and merges them into ONE combined
# `~/.claude/burn-history/fleet.jsonl` row per hour (`burn.merge_fleet_row`).
# When the observed weekly-%/day pace exceeds the budget implied by the
# watchdog's own usage cache (`burn.fleet_budget_alert`), fires ONE deduped
# Discord ping — never spam, at most once per hour_bucket.
#
# #60 follow-up (2026-07-25): the fetch is now HOUR-MATCHED — `fetch(hosts,
# hour_bucket)` passes the SAME epoch-hour bucket this job itself uses for
# its own once-per-hour guard, so `_fleet_remote_row` can reject a remote's
# stale tail line instead of silently counting it twice. And the job now
# WAITS until `FLEET_BURN_DELAY_MINUTES` past the hour boundary before doing
# any collection at all — at HH:00 a remote's OWN job 13 may simply not have
# written this hour's row YET, which would otherwise make "missing sample"
# the NORMAL state on every collection.
# --------------------------------------------------------------------------- #

FLEET_BURN_DELAY_MINUTES = 5


def fleet_burn_job(now, state, hosts, send_fn, fetch=None, local_snapshot_path=None,
                   fleet_path=None, usage_cache=None, owner=None, dry_run=False):
    """Job 16 — see the section comment. Guarded by `state['fleet_burn_hour']`,
    the SAME at-most-once-per-UTC-hour convention job 13 uses, PLUS a wait
    until `FLEET_BURN_DELAY_MINUTES` past the hour boundary (#60 point 4) —
    before that, this cycle no-ops WITHOUT claiming the hour, so the next
    sweep (60s later) retries until a remote box's own job 13 has had time to
    write. `fetch(hosts, want_hour_bucket)` is the INJECTED remote collector
    (real impl: `airuleset._watchdog_fleet_fetch`) — hour-matched against the
    LAST COMPLETED hour (#63; see `want_hour_bucket` below), so a failing OR
    stale host must come back as `{"error": ...}` in its own slot, never
    raise; a fetch that DOES raise is caught here too so one broken collector
    never drops the whole row (still writes local-only data). `dry_run`:
    compute + log, but never write the file, claim the hour, or send the
    budget alert — mirrors `burn_snapshot_job`'s dry-run contract exactly.

    #63: job 13 (`burn_snapshot_job`) stamps its row with the hour that JUST
    completed (`bucket(now) - 1`), never the current still-open one — so this
    job must collect against that SAME completed-hour bucket
    (`want_hour_bucket = hour_bucket - 1`), for EVERY host including dev1's
    own local `snapshots.jsonl` tail row (previously trusted unconditionally,
    with no freshness check at all — the asymmetry behind "dev1 always has a
    number, every remote column is permanently --"). `hour_bucket` itself
    stays the CURRENT hour purely as the once-per-hour state guard (unchanged
    from #60/#55) — it is never used to select data."""
    import burn as burn_mod
    hour_bucket = int(now // 3600)
    if state.get("fleet_burn_hour") == hour_bucket:
        return []
    now_utc = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    if now_utc.minute < FLEET_BURN_DELAY_MINUTES:
        return []
    want_hour_bucket = hour_bucket - 1
    host_rows = {}
    local_rows = burn_mod.load_snapshots(local_snapshot_path)
    if local_rows:
        last = local_rows[-1]
        name = last.get("host") or "dev1"
        if burn_mod.hour_bucket_of_ts(last.get("ts")) == want_hour_bucket:
            host_rows[name] = last
        else:
            host_rows[name] = {"error": "no local sample for hour %s (latest %s)"
                                        % (want_hour_bucket, last.get("ts")),
                               "stale": True}
    fetch = fetch or (lambda hs, hb: {})
    try:
        remote_rows = fetch(hosts, want_hour_bucket) or {}
    except Exception as e:
        remote_rows = {h.get("name", "?"): {"error": "fetch raised: %r" % (e,)}
                       for h in (hosts or [])}
    host_rows.update(remote_rows)
    completed_hour_utc = datetime.datetime.fromtimestamp(
        want_hour_bucket * 3600, datetime.timezone.utc)
    ts = completed_hour_utc.astimezone().isoformat()
    cache = usage_cache if usage_cache is not None else burn_mod.load_usage_cache()
    wk = burn_mod.shared_weekly_window(cache) if cache else None
    weekly_pct, resets_at = wk if wk else (None, None)
    row = burn_mod.merge_fleet_row(ts, host_rows, weekly_pct=weekly_pct, resets_at=resets_at)
    if dry_run:
        return ["[dry-run] fleet-burn ts=%s total=$%.2f hosts=%d (not written)"
               % (ts, row["total_usd"], len(host_rows))]
    path = Path(fleet_path or burn_mod.fleet_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    state["fleet_burn_hour"] = hour_bucket
    logs = ["fleet-burn ts=%s total=$%.2f hosts=%d -> %s"
           % (ts, row["total_usd"], len(host_rows), path)]
    all_rows = burn_mod.load_fleet(path)
    alert = burn_mod.fleet_budget_alert(
        all_rows, cache,
        now=datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
    if alert:
        status = send_fn(alert["message"], owner=owner,
                         dedup_key="fleet-burn-budget:%d" % hour_bucket, dry_run=dry_run)
        logs.append("fleet-budget-alert -> %s" % status)
    return logs


# --------------------------------------------------------------------------- #
# Job 19 -- HOURLY BURN ALERT (#81, 2026-07-26 follow-up to job 16). Job 16
# above only ever WRITES the merged hourly fleet row; nothing ever LOOKS at
# it against a reference and pings on its own -- "the only thing that does
# that today is remembering to check, and exactly during an incident, when
# spend spikes most, there's no time to remember" (the ticket's own words).
# Runs right after job 16 in run_once, on the SAME dev1-only coordinator
# gate (cmd_watchdog computes it, this module stays host-agnostic, mirroring
# job 16's own convention) -- every other managed box never writes
# fleet.jsonl at all, so the job would simply see an empty file there.
#
# Plain JSONL read + comparison (`burn.hourly_burn_alert`) + one Discord
# POST -- no agent, no model, so it survives the operator being busy
# fighting whatever incident is actually driving the spend up.
# --------------------------------------------------------------------------- #

def _env_num(name, default, cast=float):
    """Resolve one env-overridable numeric threshold, falling back to
    `default` on a missing or unparsable value. Shared by every
    `AIRULESET_BURN_ALERT_*` threshold below -- avoids four near-identical
    try/except blocks."""
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def burn_alert_job(now, state, send_fn, fleet_path=None, owner=None,
                   dry_run=False, abs_usd=None, rel_mult=None,
                   rel_window=None, weekly_step_pct=None):
    """Job 19 -- see the section comment. Reads the CURRENT latest row of
    `fleet.jsonl` (job 16's merge, run immediately before this in
    `run_once`) -- if that row's hour bucket was already evaluated
    (`state['burn_alert_hour']`), this is a no-op, the SAME at-most-once-
    per-hour convention jobs 13/16 already use ("druhe spustenie v tej
    istej hodine neposle nic"). Otherwise claims the bucket and checks it
    via `burn.hourly_burn_alert`; a triggered hour sends ONE combined
    Discord ping, deduped a SECOND time via `send_fn`'s own `dedup_key`
    (mirrors job 16's `fleet-burn-budget` dedup) so a lost/reset `state`
    can never double-post either. A quiet hour still claims the bucket
    (never re-evaluated) and sends nothing -- "ticha hodina neposiela
    nic". `dry_run`: compute + log, but never claim the hour or send
    (mirrors `fleet_burn_job`'s own dry-run contract exactly). Best-
    effort: exceptions are the caller's (run_once's) responsibility to
    catch, same as every other job."""
    import burn as burn_mod
    fleet_path = fleet_path or burn_mod.fleet_path()
    rows = burn_mod.load_fleet(fleet_path)
    if not rows:
        return []
    hb = burn_mod.hour_bucket_of_ts(rows[-1].get("ts"))
    if hb is None or state.get("burn_alert_hour") == hb:
        return []
    if abs_usd is None:
        abs_usd = _env_num("AIRULESET_BURN_ALERT_ABS_USD", burn_mod.BURN_ALERT_ABS_USD)
    if rel_mult is None:
        rel_mult = _env_num("AIRULESET_BURN_ALERT_REL_MULT", burn_mod.BURN_ALERT_REL_MULT)
    if rel_window is None:
        rel_window = _env_num("AIRULESET_BURN_ALERT_REL_WINDOW",
                              burn_mod.BURN_ALERT_REL_WINDOW, cast=int)
    if weekly_step_pct is None:
        weekly_step_pct = _env_num("AIRULESET_BURN_ALERT_WEEKLY_STEP_PCT",
                                   burn_mod.BURN_ALERT_WEEKLY_STEP_PCT)
    alert = burn_mod.hourly_burn_alert(rows, abs_usd=abs_usd, rel_mult=rel_mult,
                                       rel_window=rel_window,
                                       weekly_step_pct=weekly_step_pct)
    if dry_run:
        return ["[dry-run] burn-alert hour=%s %s (not claimed, not sent)"
               % (hb, "TRIGGERED" if alert else "quiet")]
    state["burn_alert_hour"] = hb
    if not alert:
        return ["burn-alert hour=%s quiet" % hb]
    status = send_fn(alert["message"], owner=owner,
                     dedup_key="burn-alert:%d" % hb, dry_run=dry_run)
    return ["burn-alert hour=%s TRIGGERED (%s) -> %s"
           % (hb, "; ".join(alert["reasons"]), status)]


# --------------------------------------------------------------------------- #
# Compact-request state ("krok 1c — ohraničenie kontextu", #39 follow-up,
# 2026-07-25). A completed-ticket report is a SAFE compaction boundary — the
# ticket's durable state already lives in git/GitHub/the issue, so whatever
# `/compact` discards there is genuinely disposable — unlike MANAGED_
# AUTOCOMPACT_WINDOW firing mid-task, which risks losing working context
# nothing durable has captured yet. A Stop hook (notify-compact-request.sh)
# records the request the MOMENT a turn's final message is a completed-
# ticket report (`## Work Complete` heading / terminal `✅ DONE:` marker,
# never when the last line is `❓`/`⏳` — the same precedence
# notify-discord-pending.sh already uses for its own ✅/❓/⏳ detection).
#
# This is its OWN file (~/.claude/compact-requests.json), NEVER folded into
# the watchdog's own api-watchdog-state.json: the hook writes it from the
# INTERACTIVE session's process, not from the watchdog sweep — writing into
# the sweep's in-memory `state` dict would race the sweep's own end-of-cycle
# save_state() and get silently clobbered. This is the identical reason
# notify/discord-questions.json is its own file rather than a `state` key
# (see record_question there) — `compact_requests_path()` mirrors
# `burn.snapshots_path()`'s own documented reasoning: Path.home() must be
# read at CALL time, never frozen into a module-level constant.
# --------------------------------------------------------------------------- #


def compact_requests_path():
    """`~/.claude/compact-requests.json`, resolved at CALL time (see the
    section comment above — never a frozen module-level constant)."""
    return Path.home() / ".claude" / "compact-requests.json"


def load_compact_requests(path=None):
    """{session_id: {"cwd":..., "ts":...}} — the pending /compact requests.
    {} on any error or missing file; never raises."""
    path = path or compact_requests_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_compact_requests(d, path=None):
    path = path or compact_requests_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def record_compact_request(session, cwd, now=None, path=None, msg_hash=None):
    """Record that `session` (transcript stem = CC session id) just reported
    a completed ticket — a /compact request for job 14 to action once the
    pane goes genuinely idle. Overwrites any earlier pending request for the
    SAME session (only the LATEST ticket boundary matters — a session that
    completes a second ticket before the watchdog picks up the first request
    should compact at the newer boundary, not lose the request). Fail-safe
    (never raises). Returns True on success.

    `msg_hash` (#71, optional): a fingerprint of the `last_assistant_message`
    that triggered this request — stored on the entry so whichever channel
    ends up delivering (the synchronous #65 path, or job 14's poll) can mark
    `compact_already_delivered` for THIS exact reported completion, below.
    Omitted by every pre-#71 caller — those requests simply carry no hash
    and never participate in the delivered-dedup check."""
    session = str(session or "").strip()
    if not session:
        return False
    now = time.time() if now is None else now
    d = load_compact_requests(path)
    entry = {"cwd": str(cwd or ""), "ts": int(now)}
    if msg_hash:
        entry["msg_hash"] = str(msg_hash)
    d[session] = entry
    return _save_compact_requests(d, path)


def clear_compact_request(session, path=None):
    """Remove one handled/stale request. Fail-safe. Returns True iff a
    request for `session` existed and was removed."""
    session = str(session or "").strip()
    if not session:
        return False
    d = load_compact_requests(path)
    if session in d:
        d.pop(session, None)
        return _save_compact_requests(d, path)
    return False


# --------------------------------------------------------------------------- #
# #71 (2026-07-26 live incident) — DELIVERED-DEDUP: one ticket boundary must
# produce exactly ONE `/compact`. Live evidence (gatekeeper, ~07:35): a
# SINGLE completed-ticket report produced THREE synchronous deliveries in a
# row (one success, two "Not enough messages to compact") within ~2.5
# minutes — with ZERO watchdog job 14/17 log lines in that window
# (confirmed via journalctl), proving the repeats were REPEATED Stop-hook
# fires against an UNCHANGED `last_assistant_message` (the armed goal
# loop's own re-evaluation re-running the Stop hook chain right after the
# first compaction finished), not a job-14 race — though the same mechanism
# below also closes the theoretical sync-vs-job14 race the issue names.
#
# A fingerprint of the triggering message is tracked in its OWN small file
# (`compact-delivered.json`), separate from `compact-requests.json`, so the
# EXISTING "success clears the pending entry" contract there stays exactly
# as it was (job 14's own dedup — a fresh entry per NEW boundary) — this is
# an ADDITIONAL layer: once EITHER channel confirms a hash is handled
# (delivered, or #48-gated as nothing-to-compact), a LATER request carrying
# the SAME hash — from either channel — is recognized as a duplicate of an
# ALREADY-handled boundary and produces zero keystrokes.
# --------------------------------------------------------------------------- #


def compact_delivered_path():
    """`~/.claude/compact-delivered.json`, resolved at CALL time (same
    reasoning as `compact_requests_path()` above — never a frozen
    module-level constant)."""
    return Path.home() / ".claude" / "compact-delivered.json"


def _load_compact_delivered(path=None):
    """{session_id: msg_hash} — the last message hash actually handled
    (delivered, or confirmed nothing-to-compact) per session. {} on any
    error or missing file; never raises."""
    path = path or compact_delivered_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_compact_delivered(d, path=None):
    path = path or compact_delivered_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def compact_already_delivered(session, msg_hash, path=None):
    """#71 — True iff `msg_hash` matches the LAST hash actually handled for
    `session`. A blank `session` or `msg_hash` never matches — callers with
    no hash (every pre-#71 caller) always get False, i.e. never deduped
    this way, preserving the exact pre-#71 behavior for anyone who doesn't
    opt in. Fail-safe (never raises)."""
    session = str(session or "").strip()
    msg_hash = str(msg_hash or "").strip()
    if not session or not msg_hash:
        return False
    return _load_compact_delivered(path).get(session) == msg_hash


def mark_compact_delivered(session, msg_hash, path=None):
    """#71 — record that `msg_hash` was just handled for `session`, so a
    LATER request reporting the SAME (unchanged) completion — from EITHER
    channel — is recognized as a duplicate and skipped. A blank `session`
    or `msg_hash` is a no-op (nothing to key the dedup on, and never even
    touches disk). Fail-safe (never raises). Returns True on success."""
    session = str(session or "").strip()
    msg_hash = str(msg_hash or "").strip()
    if not session or not msg_hash:
        return False
    d = _load_compact_delivered(path)
    d[session] = msg_hash
    return _save_compact_delivered(d, path)


# --------------------------------------------------------------------------- #
# #78 (2026-07-26 live incident) — SHARED /compact CLAIM, generalizing #72's
# job-17-only QUEUED/CONSUMED/FAILED state machine to EVERY sender.
#
# Live proof #71 did NOT fix (camera-box, session 90bc51f3, right after #71
# shipped): a single completed-ticket report triggered the synchronous #65
# path TWICE with two DIFFERENT msg_hashes (the Stop hook rejected the first
# draft for missing the playbook line, so the regenerated report hashed
# differently) — #71's dedup is keyed on msg_hash, so BOTH looked like new,
# undelivered boundaries to it. The pane was BUSY compacting from the first
# send; both extra sends queued behind it and fired back-to-back the instant
# the busy pane went idle:
#
#   13:07:24  /compact                                    <- 1st send (queued)
#   13:09:26  Compacted (ctrl+o to see full summary)       <- real compaction, 2 min later
#   13:09:29  /compact -> "Not enough messages to compact."   <- 2nd send, drained
#   13:09:29  /compact -> "Not enough messages to compact."   <- 3rd send, drained
#
# msg_hash dedup protects the DECISION to send. It does not protect the
# PANEL's own type-ahead QUEUE — the window between "keystrokes typed" and
# "compaction actually landed" is as wide as the whole busy turn, and every
# sender (the synchronous #65 path, job 14, job 15, job 17) is blind to
# what any OTHER sender already queued during it.
#
# The fix: ONE claim per session, shared by every sender, checked BEFORE
# any of a sender's own logic (msg_hash, context threshold, ticket
# boundary, hard ceiling) ever runs:
#
#   absent   -- never claimed, or a prior claim just resolved; eligible.
#   {"cwd", "ts"}
#            -- `/compact` was typed once and is awaiting resolution. EVERY
#               sender must skip entirely while this is true — never a
#               resend "just this once" for its own reason. Resolves via
#               EXACTLY two paths, never a timer (the #72 lesson,
#               generalized):
#                 CONSUMED -- the claimed session's OWN transcript carries a
#                   `compact_boundary` system entry (Claude Code's own
#                   durable "a real compaction landed" record — verified
#                   live against the camera-box incident transcript itself,
#                   entry at 2026-07-26T13:09:26.880Z) NEWER than the
#                   claim's send time. This is the #78-mandated proof —
#                   NEVER a context-threshold read (job 15/17's OWN
#                   internal state machines still use that for their own
#                   re-trigger cadence, which is a SEPARATE, narrower
#                   concern from "did anyone already queue a /compact").
#                 FAILED -- the claim's `cwd` now resolves to a DIFFERENT,
#                   NEWER session id than the one the claim was queued
#                   under (the pane went through a restart — `/exit` +
#                   relaunch always mints a fresh session id, so the old
#                   process holding the queued keystrokes is gone). A
#                   resend is legitimate ONLY here.
#
# Deliberately does NOT replace job 15's `compact_stale` / job 17's
# `compact_ceiling` state machines — those decide WHEN THEIR OWN job should
# next consider re-triggering (idle duration, context regrowth). This claim
# answers a narrower, independent question — "does ANY sender already have
# an outstanding, unconfirmed /compact for this session" — and gates every
# sender's send point, in ADDITION to (before) each job's own logic.
# --------------------------------------------------------------------------- #


def compact_claims_path():
    """`~/.claude/compact-claims.json`, resolved at CALL time (same
    reasoning as `compact_requests_path()` above — never a frozen
    module-level constant)."""
    return Path.home() / ".claude" / "compact-claims.json"


def _load_compact_claims(path=None):
    """{session_id: {"cwd":..., "ts":...}} — the ONE outstanding /compact
    claim per session, shared by every sender. {} on any error or missing
    file; never raises."""
    path = path or compact_claims_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_compact_claims(d, path=None):
    path = path or compact_claims_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _transcript_compact_boundary_ts(path, tail_bytes=4_000_000):
    """Epoch (float) of the NEWEST `system`/`compact_boundary` transcript
    entry within the file's tail, or None if none found in the scanned
    window / the file is unreadable. Bounded tail read (mirrors
    `_last_human_prompt_ts`'s own shape) — never loads a huge transcript
    whole. This is the #78 proof of CONSUMPTION: `compact_boundary` is
    Claude Code's OWN durable record that a real compaction landed
    (verified live against the camera-box #78 incident transcript itself,
    entry `{"type": "system", "subtype": "compact_boundary", ...}` at
    2026-07-26T13:09:26.880Z) — never an indirect proxy like a context
    read, which can lag or race."""
    from datetime import datetime
    try:
        with open(path, "rb") as f:
            try:
                f.seek(-tail_bytes, 2)
            except OSError:
                f.seek(0)
            raw = f.read()
    except OSError:
        return None
    best = None
    for ln in raw.splitlines():
        if b"compact_boundary" not in ln:
            continue
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if (not isinstance(e, dict) or e.get("type") != "system"
                or e.get("subtype") != "compact_boundary"):
            continue
        try:
            ep = datetime.fromisoformat(
                str(e.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if best is None or ep > best:
            best = ep
    return best


def compact_claim_active(sid, cwd, path=None, projects_dir=None):
    """#78 — the SINGLE shared gate every `/compact` sender (the
    synchronous #65 path, job 14, job 15, job 17) MUST consult BEFORE
    typing `/compact` into `sid`'s pane, and BEFORE any of the sender's OWN
    logic (msg_hash dedup, context threshold, ticket boundary, hard
    ceiling) runs. Returns True iff `sid` currently has an outstanding,
    UNRESOLVED claim — a send is FORBIDDEN.

    Reconciles the stored claim against reality on every call (never
    trusts a stale entry blindly), resolving it via EXACTLY the two paths
    the section comment above describes — CONSUMED (a `compact_boundary`
    transcript entry newer than the send time) or FAILED (the claim's cwd
    now belongs to a different, newer session — a demonstrated delivery
    loss). NEVER resolves on elapsed time alone — a claim with no
    matching transcript evidence stays queued no matter how long ago it
    was set."""
    sid = str(sid or "").strip()
    if not sid:
        return False
    projects_dir = projects_dir or PROJECTS_DIR
    claims = _load_compact_claims(path)
    entry = claims.get(sid)
    if not isinstance(entry, dict):
        return False                      # never claimed — eligible
    # #83 — a claim with NO "proc" key at ALL (written before #82, or whose
    # owning pane could not be fingerprinted at queue time) has NOTHING that
    # can ever prove delivery loss for the exact case that matters most: a
    # watchdog-driven restart (jobs 12/18, `_restart_pane`) relaunches via
    # `claude -c`, which CONTINUES the SAME transcript — the process-death
    # check below is a no-op (no fingerprint to check), the session never
    # changes id (the cwd/session-id FAILED check below can never fire
    # either), and nothing forces a fresh `compact_boundary`. Live incident
    # (gatekeeper, #83): a claim in this exact shape stayed queued for 3.5h,
    # context climbing to 397010, zero compaction. Per the issue's preferred
    # fix (option 1 — a one-time migration, not a permanent branch in
    # practice: every real send after #82 DOES resolve a fingerprint, so
    # this only ever matters for the pre-#82 shape or a genuine
    # fingerprint-resolution failure): treat it as unresolvable and drop it
    # on the FIRST evaluation instead of waiting on the other two checks —
    # simplest and safe, the worst case is one redundant `/compact`.
    if "proc" not in entry:
        claims.pop(sid, None)
        _save_compact_claims(claims, path)
        return False                      # unresolvable — eligible again
    claimed_cwd = entry.get("cwd") or cwd
    # FAILED — the process that received the queued keystrokes is gone, or
    # a NEW process has since reused its PID (#82): a demonstrated delivery
    # loss, independent of session id / cwd. `proc` is guaranteed present
    # here (the check above already dropped/returned any entry without it).
    proc = entry.get("proc")
    if proc and _proc_fingerprint_alive(proc) is False:
        claims.pop(sid, None)
        _save_compact_claims(claims, path)
        return False                      # FAILED — eligible for a fresh send
    # FAILED — the cwd's CURRENT active transcript is a DIFFERENT session
    # than the one this claim was queued under; the typed keystroke is
    # gone with the old (now-replaced) process.
    tinfo = find_active_transcript(projects_dir, claimed_cwd) if claimed_cwd else None
    current_sid = tinfo[0].stem if tinfo else None
    if current_sid is not None and current_sid != sid:
        claims.pop(sid, None)
        _save_compact_claims(claims, path)
        return False                      # FAILED — eligible for a fresh send
    # CONSUMED — the CLAIMED session's own transcript shows a real
    # compact_boundary newer than the send time.
    tpath = _transcript_for_session(projects_dir, sid, claimed_cwd)
    if tpath is not None:
        boundary_ts = _transcript_compact_boundary_ts(tpath)
        send_ts = entry.get("ts")
        if (boundary_ts is not None and send_ts is not None
                and boundary_ts > send_ts):
            claims.pop(sid, None)
            _save_compact_claims(claims, path)
            return False                  # CONSUMED — eligible again
    return True                           # still genuinely queued — NEVER
                                           # resend here, no matter how much
                                           # time has passed


def compact_claim_set(sid, cwd, now=None, path=None, pane_id=None, run=None,
                      proc=None):
    """Record that `/compact` was just typed into `sid`'s pane — the
    SINGLE shared claim every sender (#78) writes on every real send.
    `now` (default `time.time()`) is the send time `compact_claim_active`
    compares transcript `compact_boundary` entries against.

    #82: also fingerprints the PROCESS the keystrokes were actually
    delivered to (see `_proc_fingerprint`), so a later restart of that
    process is detectable as a demonstrated delivery loss even when
    neither of the other two resolutions (CONSUMED / cwd-session-id
    FAILED) can ever fire — the exact "relaunch continues the same
    transcript via `claude -c`" case. `proc` (`{"pid", "starttime"}`) may
    be passed directly (a caller that already resolved it, or a test);
    otherwise, when `pane_id` is given, it is resolved via
    `_pane_claude_proc_fingerprint`. Neither given is fine — the claim is
    simply recorded without a fingerprint (the pre-#82 shape), and the
    process-death check in `compact_claim_active` is then a no-op for it.
    Fail-safe (never raises). Returns True on success."""
    sid = str(sid or "").strip()
    if not sid:
        return False
    now = time.time() if now is None else now
    claims = _load_compact_claims(path)
    entry = {"cwd": str(cwd or ""), "ts": now}
    if proc is None and pane_id:
        proc = _pane_claude_proc_fingerprint(pane_id, run=run)
    if proc:
        entry["proc"] = proc
    claims[sid] = entry
    return _save_compact_claims(claims, path)


COMPACT_SYNC_LOG_LINES_MAX = 2000


def compact_sync_log_path():
    """`~/.claude/compact-sync.log`, resolved at CALL time (same reasoning
    as `compact_requests_path()` above). #78 — the SYNCHRONOUS #65
    delivery path (`deliver_compact_now`) runs directly inside the Stop
    hook subprocess with its stdout thrown at /dev/null
    (`notify-compact-request.sh`), so a plain print() there leaves NO
    trace anywhere journalctl can see. This is the ONLY record of every
    send/drop decision that path makes — the #78 incident itself was
    undebuggable from journalctl for exactly this reason (confirmed: ZERO
    job 14/17 log lines in the incident window, while THREE synchronous
    deliveries fired in a row)."""
    return Path.home() / ".claude" / "compact-sync.log"


def _log_compact_sync(line, path=None):
    """Best-effort append-only log line for the synchronous delivery path
    (`deliver_compact_now`). Never raises — a logging failure must never
    break delivery (returns False instead, same fail-safe shape as every
    other `_save_*` helper in this module). Bounded: trims to the last
    `COMPACT_SYNC_LOG_LINES_MAX` lines on every write so this can never
    grow unbounded on a long-lived box."""
    path = path or compact_sync_log_path()
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    existing = []
    try:
        existing = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    existing.append("%s %s" % (ts, line))
    existing = existing[-COMPACT_SYNC_LOG_LINES_MAX:]
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(existing) + "\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Job 14 — /COMPACT AT TICKET BOUNDARIES (#39 krok 1c, 2026-07-25). See the
# section comment above. Reuses the EXACT idle guards job 12 (MODEL
# RECONCILE) already uses: never busy, never mid-dialog, never with a draft
# present, never in copy-mode — plus the SAME strip-select discipline as
# every other keystroke-sending job here (send_continue: escape the
# agent-strip selector ONLY when it holds focus, never a second Escape into
# a live pane — issue #35). A pane is matched to a request by SESSION ID
# (`panes_by_sid`, built once per sweep in run_once — the SAME map job 7's
# reply-routing uses), never by cwd alone (a cwd can be revisited by a
# LATER, unrelated session).
# --------------------------------------------------------------------------- #

COMPACT_TEXT = "/compact"

# #48 (2026-07-25 user report): job 14 fired `/compact` after EVERY completed-
# ticket report, even a trivial one that barely grew the context — "mať
# nonstop volaný compact za každou blbosťou je dosť nízka inteligencia". A
# wasted /compact has a real cost (a summary turn + a cache-write + losing
# working context) for ~zero benefit below this floor: the static context
# floor is ~93K tokens, and the typical gain from compacting under ~200K is
# small. Gated on the CONSUME side (here, right before send_continue), not on
# the record side (notify-compact-request.sh) — between record and consume
# the context can still grow, so reading it fresh here is the most accurate
# and the recording hook stays dumb/fast. Env override lets a box tune the
# floor without a code change.
COMPACT_BOUNDARY_MIN_CONTEXT = 200_000

# #67 (2026-07-26 live incident, david@subdev): a forgotten USER DRAFT sitting
# in the input box made job 14/15 log "skip draft" and retry FOREVER — the
# same draft (one unfinished sentence) skipped 13 sweeps straight overnight
# while the session's context grew 214K -> 449K with zero compactions. The
# fix already existed for a DIFFERENT problem (job 7's Discord-reply
# delivery, issue #35): `deliver_with_stash` parks the foreign draft via
# CC's native Ctrl+S stash, delivers `/compact`, and lets CC auto-restore
# the draft the instant the delivered turn completes — a draft is no longer
# a dead end for EITHER compact job. Shared here so job 14 and job 15 apply
# the identical policy (try stash -> on success proceed exactly like the
# no-draft path; on "slot already occupied" -> log a DISTINCT skip reason
# and count it, pinging the owner once every COMPACT_STASH_SKIP_PING_EVERY
# consecutive occupied-skips so a permanently jammed stash slot never rots
# silently, per the issue's acceptance).
COMPACT_STASH_SKIP_PING_EVERY = 3


def _compact_stash_attempt(pid, run, captured, state, sid, loc, project,
                           owner=None, ctx=None, send_fn=None):
    """Try `deliver_with_stash` for a session whose pane holds a DRAFT (#67).

    Returns True if `/compact` was actually delivered (caller proceeds
    exactly like its own no-draft success path: consume the request /
    record the compacted-pending state), False if the stash slot was
    already occupied by something else — stashing over it would SILENTLY
    destroy whatever is parked there, so this is a LEGITIMATE skip, not a
    failure: the caller retries next sweep, unchanged from the pre-#67
    behavior, except the repeated-skip count is tracked in
    `state['compact_stash_skips'][sid]` and the owner is pinged once every
    Nth consecutive occupied-skip (deduped per (sid, n) so it never spams
    every single sweep). A successful delivery clears any prior count for
    this session."""
    delivered = deliver_with_stash(pid, COMPACT_TEXT, run, captured=captured)
    skips = state.get("compact_stash_skips") or {}
    if delivered:
        skips.pop(sid, None)
        state["compact_stash_skips"] = skips
        return True
    n = int(skips.get(sid, 0)) + 1
    skips[sid] = n
    state["compact_stash_skips"] = skips
    if send_fn is not None and n % COMPACT_STASH_SKIP_PING_EVERY == 0:
        ctx_txt = (" (kontext %d tokenov)" % ctx) if ctx is not None else ""
        send_fn(
            "⚠️ **%s** (%s) — zabudnutý draft blokuje /compact už %d× po "
            "sebe%s. Stash slot je obsadený iným príkazom, takže sa doň "
            "nedá bezpečne zasiahnuť. Skontroluj okno a vybav draft rukou."
            % (project, loc, n, ctx_txt),
            owner=owner or None,
            dedup_key="compact-stash-skip:%s:%d" % (sid, n))
    return False


def compact_ticket_boundary(now, run, state, panes_by_sid, dry_run=False,
                            path=None, projects_dir=None, min_context=None,
                            send_fn=None, handled=None, delivered_path=None):
    """Job 14 — see the section comment. `state` is used ONLY for #67's
    stash-skip counter (`compact_stash_skips`) — this job's own request
    dedup still lives entirely in the requests file, not in
    api-watchdog-state.json.

    #78: the SHARED `/compact` claim (`compact_claim_active`) is checked
    FIRST, before the #71 msg_hash dedup below — while another sender
    already has an outstanding claim for this sid, this request is dropped
    with ZERO tmux interaction, regardless of msg_hash/context. Every real
    send (idle or stash) below also SETS the claim via
    `compact_claim_set`, so job 15/17/the sync path see it too.

    #71: an entry whose `msg_hash` is ALREADY marked delivered (in
    `delivered_path` — default `compact_delivered_path()`, checked via
    `compact_already_delivered`) is dropped with ZERO tmux interaction — the
    synchronous #65 path (or a prior pass of THIS job) already handled this
    exact reported completion; a stale poll must never re-send `/compact`
    for it. Every success path below (idle send, stash delivery, and the
    #48 context-gate drop) marks the hash delivered via
    `mark_compact_delivered`, using the entry's OWN `msg_hash` — so whoever
    acts first closes the door for the other. A request recorded with no
    `msg_hash` (every pre-#71 caller) never consults or writes this file at
    all — `compact_already_delivered`/`mark_compact_delivered` are no-ops on
    a blank hash, so existing behavior is untouched.

    `handled` (#69, optional): a mutable set run_once passes so job 17 (the
    hard-ceiling backstop) can see, WITHIN THE SAME SWEEP, which session ids
    this job already sent `/compact` into — and skip them, rather than
    relying solely on the pane's own "Compacting conversation" text (which
    can lag a beat behind the send). Every real send (direct or stash)
    records its sid here; a dropped/skipped request never does.

    #48 context-threshold gate: right before actually sending `/compact` for
    an otherwise-ready request, the session's CURRENT context is measured via
    `transcript_current_context()` — the SAME helper job 15 uses, resolved
    for this specific sid+cwd via `_transcript_for_session` (falls back to a
    sid-only glob across the whole projects tree, same as the ❓-reply-prune
    path). Below `min_context` (default `COMPACT_BOUNDARY_MIN_CONTEXT`, env
    `AIRULESET_COMPACT_BOUNDARY_MIN_CONTEXT`) the request is DROPPED (cleared
    from the requests file, never left to fire on the next done) — no
    `/compact` is sent. When no transcript can be resolved at all (unknown
    session — every pre-#48 test fixture here has no real transcript), the
    context is unmeasurable and this gate does not block the send: it only
    ever skips on a POSITIVELY confirmed small context, never on "we don't
    know". Measured BEFORE the draft/idle decision (moved up in #67) so a
    trivial-context session never even attempts a stash dance.

    A request is REMOVED from the requests file (dedup — never retried) the
    MOMENT `/compact` is actually typed into an idle pane — sending the
    keystrokes IS the observable success here, the same fire-and-forget
    shape job 9's goal_autoarm uses (unlike job 12, `/compact` has no
    reliable confirmation text to poll for). A request is LEFT IN PLACE
    (retried next sweep) whenever the pane is busy / mid-dialog / in
    copy-mode / holding a draft whose STASH SLOT is already occupied by
    something else, or when no live pane maps to that session yet —
    "release the claim on failure so it retries" per the spec: there is no
    separate claim step to release, because nothing is ever claimed until
    the keystrokes are actually sent. A draft whose stash slot is FREE is no
    longer a dead end (#67): `_compact_stash_attempt` parks it, delivers
    `/compact`, and the request IS consumed on that success — the draft
    auto-restores itself once the delivered turn completes. Best-effort:
    exceptions are the caller's (run_once's) responsibility to catch, same
    as every other job."""
    reqs = load_compact_requests(path)
    if not reqs:
        return []
    run = run or _default_run
    if min_context is None:
        try:
            min_context = int(os.environ.get(
                "AIRULESET_COMPACT_BOUNDARY_MIN_CONTEXT",
                COMPACT_BOUNDARY_MIN_CONTEXT))
        except ValueError:
            min_context = COMPACT_BOUNDARY_MIN_CONTEXT
    pdir = projects_dir or PROJECTS_DIR
    logs = []
    changed = False
    for sid in list(reqs.keys()):
        entry = reqs.get(sid) or {}
        cwd = entry.get("cwd", "")
        # #78 -- the SHARED claim gate, checked FIRST and unconditionally:
        # if ANY sender (this job, job 15, job 17, or the synchronous #65
        # path) already has an outstanding, unresolved /compact claim for
        # this session, drop this request with ZERO tmux interaction --
        # regardless of msg_hash, context, or anything else below.
        if compact_claim_active(sid, cwd, projects_dir=pdir):
            logs.append("skip claim-queued (compact-request) %s" % sid)
            if not dry_run:
                reqs.pop(sid, None)
                changed = True
            continue
        mhash = str(entry.get("msg_hash") or "").strip()
        if mhash and compact_already_delivered(sid, mhash, path=delivered_path):
            # #71 — this exact reported completion was already handled (by
            # the synchronous path, or an earlier pass of this job): a stale
            # poll must never re-send `/compact` for it.
            logs.append("skip already-delivered (compact-request) %s" % sid)
            if not dry_run:
                reqs.pop(sid, None)
                changed = True
            continue
        pane = panes_by_sid.get(sid)
        if not pane:
            logs.append("skip no-pane (compact-request) %s" % sid)
            continue
        pid, captured = pane
        loc = _pane_location(pid, run) or pid
        if pane_in_mode(pid, run):
            logs.append("skip in-mode (compact-request) %s" % loc)
            continue
        if pane_waiting_on_user(captured):
            logs.append("skip dialog-open (compact-request) %s" % loc)
            continue
        kind, draft = _classify_boundary(captured)
        if kind == "no-input-line":
            logs.append("skip no-input-line (compact-request) %s" % loc)
            continue
        if kind == "busy":
            logs.append("skip busy (compact-request) %s" % loc)
            continue
        # #48 — read the FRESHEST context right before deciding anything else
        # (it may have grown since the Stop hook recorded the request); a
        # session with no resolvable transcript is unmeasurable, so it does
        # NOT block the send (see the docstring above). Measured before the
        # draft branch (#67) so a trivial-context draft-holding session is
        # simply dropped, never stash-attempted for nothing.
        ctx = None
        tpath = _transcript_for_session(pdir, sid, cwd)
        if tpath is not None:
            ctx = transcript_current_context(tpath)
            if ctx < min_context:
                logs.append("skip small-context (compact-request) %s ctx=%d"
                            % (loc, ctx))
                if not dry_run:
                    reqs.pop(sid, None)
                    changed = True
                    if mhash:
                        mark_compact_delivered(sid, mhash, path=delivered_path)
                continue
        if draft:
            # #67 — a forgotten draft must not permanently block compaction:
            # stash it around the /compact delivery instead of skipping
            # forever.
            if dry_run:
                logs.append("READY (compact-request, draft) %s" % loc)
                continue
            project = project_label(cwd)
            owner = pane_owner(pid, run)
            if _compact_stash_attempt(pid, run, captured, state, sid, loc,
                                      project, owner=owner, ctx=ctx,
                                      send_fn=send_fn):
                reqs.pop(sid, None)
                changed = True
                if handled is not None:
                    handled.add(sid)
                if mhash:
                    mark_compact_delivered(sid, mhash, path=delivered_path)
                compact_claim_set(sid, cwd, pane_id=pid, run=run)  # #78/#82
                logs.append("OK (compact-request, stash) %s" % loc)
            else:
                logs.append("skip draft (stash occupied) %s: %r"
                            % (loc, draft[:40]))
            continue
        if not pane_at_idle_prompt(captured):
            logs.append("skip not-idle (compact-request) %s" % loc)
            continue
        if dry_run:
            logs.append("READY (compact-request) %s" % loc)
            continue
        send_continue(pid, COMPACT_TEXT, run)
        reqs.pop(sid, None)
        changed = True
        if handled is not None:
            handled.add(sid)
        if mhash:
            mark_compact_delivered(sid, mhash, path=delivered_path)
        compact_claim_set(sid, cwd, pane_id=pid, run=run)  # #78/#82
        logs.append("OK (compact-request) %s" % loc)
    if changed:
        _save_compact_requests(reqs, path)
    return logs


# --------------------------------------------------------------------------- #
# #65 (2026-07-26 live incident) — job 14's poll (above) loses the RACE with
# an armed `/goal` loop: the Stop hook (notify-compact-request.sh) records a
# request, but the loop's own next-ticket dispatch can fire within SECONDS —
# long before the next ~60s watchdog tick ever gets a chance to see the pane
# idle. Measured `## ✅ Work Complete` : actual-compaction ratios confirm the
# gap: david 38→13, montalu 63→49, forestshop 63→16, gatekeeper 0→9 (it never
# even emits a `Work Complete`, so its chain has nothing to start from).
#
# The fix is DELIVERY TIMING, not a smarter poll: `airuleset.py
# compact-request --record` now ALSO attempts to deliver `/compact`
# SYNCHRONOUSLY, in the same process, the MOMENT the Stop hook records the
# request — before the loop gets a chance to dispatch the next ticket at
# all. This is safe specifically because a SHORT `send-keys` command reliably
# QUEUES even into a BUSY pane (verified live 2026-07-26 for /compact itself
# and for `/goal clear`) — CC's own type-ahead queues it as the next turn
# once the current one ends, so this delivery does NOT need to wait for an
# idle pane the way job 14's poll does. Only a genuinely UNSAFE pane state
# (in copy-mode, an open dialog, an unlocatable boundary, or a real unsent
# DRAFT — never CC's own "Press up to edit queued messages" placeholder,
# which `_find_boundary_line` already normalizes to bare) falls back to
# `record_compact_request` for job 14's polled retry, unchanged.
# --------------------------------------------------------------------------- #

def _find_pane_for_session(sid, cwd, run=None, projects_dir=None):
    """Resolve the SINGLE current live pane hosting session `sid` — for a
    synchronous, Stop-hook-time delivery where there is no per-sweep
    `panes_by_sid` map to consult (unlike job 14/7, which reuse the ONE map
    run_once already built this cycle). Matches by TRANSCRIPT STEM, never by
    cwd alone (a cwd can be revisited later by an unrelated session) — the
    same discipline run_once's own `by_transcript` grouping uses. Ambiguous
    (0 or >1 matching pane) returns None so the caller falls back to the
    polled retry path rather than risk typing into the wrong pane."""
    run = run or _default_run
    projects_dir = projects_dir or PROJECTS_DIR
    matches = []
    for pid, pcwd in list_claude_panes(run):
        tinfo = find_active_transcript(projects_dir, pcwd)
        if not tinfo:
            continue
        tpath, _tmtime = tinfo
        if tpath.stem == sid:
            matches.append(pid)
    return matches[0] if len(matches) == 1 else None


def deliver_compact_now(sid, cwd, run=None, projects_dir=None, min_context=None):
    """#65 — attempt to deliver `/compact` for `sid` SYNCHRONOUSLY, right when
    the ticket-boundary request is recorded (see the section comment above).

    Returns True when this session is FULLY HANDLED (either `/compact` was
    actually typed, or an existing claim / the #48 context-threshold gate
    confirms nothing more needs to happen here) — the caller
    (`cmd_compact_request`) must NOT leave a pending request behind in
    either case. Returns False when the caller should fall back to
    `record_compact_request` for job 14's polled retry: no pane resolves
    unambiguously, the pane is in copy-mode / showing an open dialog / has
    no locatable boundary at all, or — the one case this function
    deliberately stays conservative on — the pane holds a genuine unsent
    DRAFT (job 14/15's `_compact_stash_attempt`, #67, handles that on
    retry; a synchronous multi-round-trip stash dance at Stop-hook time is
    unnecessary risk for what should be a rare case). A pane merely BUSY
    (mid-turn) is NOT a reason to fall back — that is exactly the case
    this function exists to handle, per the section comment's queuing
    behavior.

    #78 — checks the SHARED `/compact` claim FIRST, before anything else
    (no pane resolution, no tmux round-trip needed): if another sender
    already has an outstanding, unresolved claim for `sid`, this call sends
    NOTHING and returns True (handled — the outstanding claim is what will
    resolve this, not a second send). Every real send below sets the claim
    via `compact_claim_set`. Every decision this function makes (skip or
    send) is logged via `_log_compact_sync` — this is the ONLY trace of
    this path's behavior, since its caller (the Stop hook) throws stdout at
    /dev/null (#78's own incident was undebuggable from journalctl for
    exactly this reason)."""
    run = run or _default_run
    projects_dir = projects_dir or PROJECTS_DIR
    if compact_claim_active(sid, cwd, projects_dir=projects_dir):
        _log_compact_sync("SKIP claim-queued sid=%s cwd=%s" % (sid, cwd))
        return True   # another sender already has this queued — handled
    pid = _find_pane_for_session(sid, cwd, run=run, projects_dir=projects_dir)
    if not pid:
        _log_compact_sync("SKIP no-pane sid=%s cwd=%s" % (sid, cwd))
        return False
    if pane_in_mode(pid, run):
        _log_compact_sync("SKIP in-mode sid=%s cwd=%s" % (sid, cwd))
        return False
    captured = capture_pane(pid, run, lines=40)
    if pane_waiting_on_user(captured):
        _log_compact_sync("SKIP dialog-open sid=%s cwd=%s" % (sid, cwd))
        return False
    kind, draft = _classify_boundary(captured)
    if kind == "no-input-line":
        _log_compact_sync("SKIP no-input-line sid=%s cwd=%s" % (sid, cwd))
        return False
    if draft:
        _log_compact_sync("SKIP draft sid=%s cwd=%s" % (sid, cwd))
        return False
    # kind is "input" (bare, or the queued-messages placeholder already
    # normalized to bare) or "busy" — BOTH are safe to send into here (see
    # the section comment: a short send-keys queues reliably even on a busy
    # pane), so there is no `pane_at_idle_prompt` gate on this path.
    if min_context is None:
        try:
            min_context = int(os.environ.get(
                "AIRULESET_COMPACT_BOUNDARY_MIN_CONTEXT",
                COMPACT_BOUNDARY_MIN_CONTEXT))
        except ValueError:
            min_context = COMPACT_BOUNDARY_MIN_CONTEXT
    tpath = _transcript_for_session(projects_dir, sid, cwd)
    if tpath is not None and transcript_current_context(tpath) < min_context:
        _log_compact_sync("DROP small-context sid=%s cwd=%s" % (sid, cwd))
        return True   # #48 gate: nothing worth compacting — handled, drop it
    send_continue(pid, COMPACT_TEXT, run)
    compact_claim_set(sid, cwd, pane_id=pid, run=run)  # #78/#82
    _log_compact_sync("SEND sid=%s cwd=%s" % (sid, cwd))
    return True


# --------------------------------------------------------------------------- #
# Job 15 — COMPACT OVERGROWN IDLE SESSIONS (#39/#43 follow-up, 2026-07-25).
#
# Job 14 only /compact's a session at a completed-ticket boundary (its Stop
# hook fires the request). A long-lived session that is NOT an autopilot
# loop — nothing ever reports a "ticket done" — can sit on the right model
# with a huge, still-growing context FOREVER with no mechanism to shrink it.
# Measured live on dev1 over 10 hours, grouped by `message.id` (never raw
# line count — a thinking/text/tool_use burst shares one id and one usage
# snapshot): `restreamer` (an autopilot session that got compacted) ran at
# $0.40/turn @ 375K context vs $0.185/turn @ ~250K post-compaction — a real
# 2.2x. `forestshop-parovanie-produktov` (untouched all day, no ticket
# boundary) stayed at $0.38-0.40/turn while its context climbed 559K ->
# 642K and kept climbing — exactly the gap job 14 cannot close.
#
# This job closes it: for every live claude/node/bun pane (SAME detection
# job 12 uses, `_reconcile_candidate_panes` — deliberately its own
# enumeration, not run_once's narrower `list_claude_panes`) whose newest
# transcript turn's context (`transcript_current_context` — cache_read +
# cache_creation, grouped by message.id, never summed) exceeds
# COMPACT_CONTEXT_THRESHOLD, AND whose transcript has been quiet for at
# least COMPACT_MIN_IDLE_S, AND the pane is genuinely idle at a bare prompt
# (no draft, no open dialog, not in copy-mode, not mid-turn) AND has no
# in-flight background agent (`_pane_has_bg_agent`, reused from job 12 — a
# worker in flight must never be interrupted), `/compact` is typed in.
#
# This is DELIBERATELY NOT Claude Code's own `autoCompactWindow` (which the
# user had airuleset actively STRIP from settings.json in the 2026-07-25
# correction batch, reverting the SAME-DAY "krok 1c" addition — see the
# comment above `MANAGED_MODEL` in airuleset.py). `autoCompactWindow` fires
# at a raw token threshold regardless of what the session is doing, so it
# cuts a long task off MID-WORK and defeats the entire point of the 1M
# context window. This job only ever fires on a session that is
# DEMONSTRABLY doing nothing — idle >= COMPACT_MIN_IDLE_S, no draft, no
# worker in flight — so compaction here cannot interrupt anything, and the
# full 1M window stays available to any task that actually needs the depth.
#
# Dedup: unlike job 14 (fire-and-forget — sending the keystrokes IS success,
# no confirmation text exists to poll for), THIS job's own trigger condition
# (current context) does NOT reliably clear right after `/compact` is sent —
# the transcript's last usage record still reads the PRE-compaction (huge)
# number until the session's NEXT real turn, which may never come for an
# idle session with nobody prompting it. A NAIVE immediate re-check would
# re-send `/compact` on EVERY ~60s sweep forever.
#
# STATE MACHINE for `state['compact_stale'][sid]` (issue #46 part 2 — the
# original design claimed PERMANENTLY on every outcome, so a session that
# lives for days got compacted exactly ONCE, ever, even after its context
# regrew past the threshold well past compaction):
#
#   absent                          -- never touched by this job; eligible.
#   True                             -- GAVE UP after COMPACT_STALE_MAX_ATTEMPTS
#                                       failed verifications (mirrors job 12's
#                                       bounded-attempts fix for the live gk
#                                       incident, 2026-07-25, where a FAILing
#                                       pane retried every sweep forever).
#                                       PERMANENT — never reconsidered, no
#                                       matter what the context does later.
#   COMPACT_STALE_PENDING_CONFIRM    -- a REAL `/compact` success is recorded
#                                       but not yet CONFIRMED to have landed.
#                                       Every sweep re-reads the CURRENT
#                                       context for a pending sid (cheap,
#                                       local, no tmux round-trip): while it
#                                       is STILL >= threshold, do nothing (no
#                                       resend, no log — the stale-reading
#                                       race the original design worried
#                                       about); the moment it is observed
#                                       BELOW threshold — proof the session
#                                       had a real turn and the compaction's
#                                       effect reached the transcript — the
#                                       claim is CLEARED back to absent, so a
#                                       future re-growth past the threshold
#                                       is eligible again.
#
# A below-cap verify FAILURE still releases the claim entirely (absent) so
# the next sweep retries, same as before this rework.
# --------------------------------------------------------------------------- #

COMPACT_CONTEXT_THRESHOLD = 400_000   # tokens (cache_read + cache_creation) —
                                      # measured 2.2x cost improvement below this
COMPACT_MIN_IDLE_S = 20 * 60          # transcript must be this stale (no new turn)
                                      # before compaction is safe — this IS the
                                      # guard that makes the whole job safe: a
                                      # session doing nothing for 20 minutes with
                                      # no worker running cannot be interrupted
COMPACT_STALE_MAX_ATTEMPTS = 3        # cap verify-failure retries per session,
                                      # same rationale as MODEL_RECONCILE_MAX_ATTEMPTS
COMPACT_STALE_VERIFY_POLL_S = 1
COMPACT_STALE_VERIFY_MAX_POLLS = 10   # bounded (no-timeout-band-aids.md)
# Marker for "compacted, awaiting confirmation the context actually dropped"
# — see the STATE MACHINE comment above. Deliberately a non-True truthy value
# so `claim is True` (give-up) and `claim == COMPACT_STALE_PENDING_CONFIRM`
# (awaiting confirmation) are never confused with each other.
COMPACT_STALE_PENDING_CONFIRM = "pending-confirm"


def _wait_for_compact_return(pid, run, sleep_fn):
    """Poll (bounded) for the pane to land back at a free idle prompt after
    `/compact` was typed — the only observable "it worked" signal available
    for this command. Unlike job 12's fixed "Resume from summary" dialog
    text, `/compact` has no reliable confirmation TEXT to match (job 14's
    own docstring already notes this) — so "the pane returned to a bare `❯`"
    is the verification."""
    for _ in range(COMPACT_STALE_VERIFY_MAX_POLLS):
        sleep_fn(COMPACT_STALE_VERIFY_POLL_S)
        captured = capture_pane(pid, run, lines=40) or ""
        if pane_at_idle_prompt(captured):
            return True
    return False


def compact_stale_context(now, run, state, dry_run=False, projects_dir=None,
                          sleep_fn=None, send_fn=None, handled=None):
    """Job 15 — see the section comment. Always wired into run_once (no
    gating param — same "always on" shape as job 9's goal_autoarm), since
    it depends on nothing external (no fetcher, no target model).

    `handled` (#69, optional): same shared per-sweep set job 14 populates —
    a real success here (either path) records the sid so job 17 (hard
    ceiling) never double-fires on it within the same sweep.

    #78: the SHARED `/compact` claim (`compact_claim_active`) is checked
    right before any tmux round-trip, AFTER this job's OWN `compact_stale`
    state machine has already had its say (PENDING_CONFIRM clearing / a
    below-threshold skip) — those decide THIS job's own re-trigger
    cadence and are UNCHANGED. The shared claim answers the separate,
    narrower question "does anyone else already have one in flight" and
    takes precedence over a bare failed-verification retry: while another
    sender's claim (or this job's own prior send) is still queued, this
    sweep's send attempt is skipped outright — never resent on a mere
    verification timeout (the #72 lesson, generalized). Every real send
    (idle or stash) also SETS the claim via `compact_claim_set`.

    Local, cheap checks (dedup, current context, idle duration — all read
    off the transcript file, no tmux round-trip) run BEFORE any tmux call,
    so the overwhelmingly common case (a session under threshold, or idle
    under 20 minutes) costs nothing beyond a transcript read and is never
    logged — logging would spam every sweep for every ordinary session.
    Only a pane that already cleared BOTH gates is worth a tmux round-trip
    and a log line.

    Never touches a pane that is in copy-mode, showing an open dialog,
    mid-turn/busy, or running a background agent — identical guards to
    job 12's model_reconcile, reusing the very same helpers (`pane_in_mode`,
    `pane_waiting_on_user`, `_classify_boundary`, `pane_at_idle_prompt`,
    `_pane_has_bg_agent`, `_pane_location`) rather than a parallel
    chrome-detector. A pane holding an unsent DRAFT is no longer a dead end
    (#67, 2026-07-26): `_compact_stash_attempt` parks the draft via CC's
    native Ctrl+S stash, delivers `/compact`, and lets CC auto-restore the
    draft once the delivered turn completes — the same mechanism issue #35
    already uses for job 7's Discord-reply delivery. Only a genuinely
    OCCUPIED stash slot (something else already parked there) is a
    legitimate skip, tracked + pinged after
    `COMPACT_STASH_SKIP_PING_EVERY` repeats so it never rots silently.
    `send_continue`/`deliver_with_stash` already handle the "Escape the
    agent-strip selector ONLY if it holds focus, then type + Enter"
    sequence (issue #36) — never a second Escape anywhere (issue #35).
    Best-effort: exceptions are the caller's (run_once's) responsibility to
    catch, same as every other job."""
    run = run or _default_run
    sleep_fn = sleep_fn or time.sleep
    projects_dir = projects_dir or PROJECTS_DIR
    compacted = state.get("compact_stale") or {}
    attempts_map = state.get("compact_stale_attempts") or {}
    logs = []
    for pid, cwd, cmd in _reconcile_candidate_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, tmtime = tinfo
        sid = tpath.stem
        claim = compacted.get(sid)
        if claim is True:
            continue                          # gave up for good — never retry, ever
        if claim == COMPACT_STALE_PENDING_CONFIRM:
            # Awaiting confirmation the earlier /compact success landed (the
            # STATE MACHINE comment above). Cheap, local, no tmux round-trip
            # for the common (still-high) case — only a genuine CLEAR is
            # worth logging + a pane-location lookup.
            if transcript_current_context(tpath) < COMPACT_CONTEXT_THRESHOLD:
                compacted.pop(sid, None)
                state["compact_stale"] = compacted
                loc = _pane_location(pid, run) or pid
                logs.append("CLEARED (compact-stale) %s -> context confirmed "
                            "below threshold, eligible again" % loc)
            continue                          # still high, or just cleared — never resend THIS sweep
        idle = now - tmtime
        if idle < COMPACT_MIN_IDLE_S:
            continue                          # too fresh to touch — no log (the common case)
        ctx = transcript_current_context(tpath)
        if ctx < COMPACT_CONTEXT_THRESHOLD:
            continue                          # under threshold — no log (the common case)
        # #78 -- the SHARED claim gate, checked right before any tmux
        # round-trip (cheap, local): if ANY sender (job 14, job 17, or the
        # synchronous #65 path) already has an outstanding, unresolved
        # /compact claim for this sid, skip THIS SWEEP's send attempt --
        # this job's OWN `compact_stale` state (above) still governs ITS
        # OWN re-trigger cadence (PENDING_CONFIRM clearing, give-up); the
        # shared claim is the separate, narrower "does anyone else already
        # have one in flight" question, and it takes precedence over a
        # bare failed-verification retry (the #72 lesson, generalized:
        # never resend on a mere timeout).
        if compact_claim_active(sid, cwd, projects_dir=projects_dir):
            continue
        loc = _pane_location(pid, run) or pid
        if pane_in_mode(pid, run):
            logs.append("skip in-mode (compact-stale) %s %d" % (loc, ctx))
            continue
        captured = capture_pane(pid, run, lines=40)
        if pane_waiting_on_user(captured):
            logs.append("skip dialog-open (compact-stale) %s %d" % (loc, ctx))
            continue
        kind, draft = _classify_boundary(captured)
        if kind == "no-input-line":
            logs.append("skip no-input-line (compact-stale) %s %d" % (loc, ctx))
            continue
        if kind == "busy":
            logs.append("skip busy (compact-stale) %s %d" % (loc, ctx))
            continue
        if _pane_has_bg_agent(captured):
            logs.append("skip bg-agent (compact-stale) %s %d" % (loc, ctx))
            continue
        if draft:
            # #67 — try the stash-around delivery instead of skipping forever.
            if dry_run:
                logs.append("READY (compact-stale, draft) %s ctx=%d idle=%ds"
                            % (loc, ctx, int(idle)))
                continue
            project = project_label(cwd)
            owner = pane_owner(pid, run)
            delivered = _compact_stash_attempt(pid, run, captured, state, sid,
                                               loc, project, owner=owner,
                                               ctx=ctx, send_fn=send_fn)
            if not delivered:
                logs.append("skip draft (stash occupied) %s %d: %r"
                            % (loc, ctx, draft[:40]))
                continue
            compact_claim_set(sid, cwd, pane_id=pid, run=run)  # #78/#82
            tag = "compact-stale, stash"
        else:
            if not pane_at_idle_prompt(captured):
                logs.append("skip not-idle (compact-stale) %s %d" % (loc, ctx))
                continue
            if dry_run:
                logs.append("READY (compact-stale) %s ctx=%d idle=%ds"
                            % (loc, ctx, int(idle)))
                continue
            send_continue(pid, COMPACT_TEXT, run)
            compact_claim_set(sid, cwd, pane_id=pid, run=run)  # #78/#82
            tag = "compact-stale"
        ok = _wait_for_compact_return(pid, run, sleep_fn)
        if ok:
            # PENDING, not permanently True — the claim clears once the
            # observed context confirms the compaction actually landed (the
            # STATE MACHINE comment above), so a later re-growth past the
            # threshold is eligible again instead of this session being
            # compacted exactly once for its entire lifetime.
            compacted[sid] = COMPACT_STALE_PENDING_CONFIRM
            state["compact_stale"] = compacted
            attempts_map.pop(sid, None)
            state["compact_stale_attempts"] = attempts_map
            if handled is not None:
                handled.add(sid)
            logs.append("OK (%s) %s ctx=%d -> compacted" % (tag, loc, ctx))
        else:
            attempts = attempts_map.get(sid, 0) + 1
            attempts_map[sid] = attempts
            state["compact_stale_attempts"] = attempts_map
            if attempts >= COMPACT_STALE_MAX_ATTEMPTS:
                # Bounded — never retry this session again (same rationale as
                # job 12's give-up: a failing pane must not burn a full
                # context re-read on every single ~60s sweep forever).
                compacted[sid] = True
                state["compact_stale"] = compacted
                logs.append("GAVE UP (%s) %s ctx=%d after %d attempts"
                            % (tag, loc, ctx, attempts))
            else:
                logs.append("FAIL (%s) %s ctx=%d (did not return idle, "
                            "attempt %d/%d)"
                            % (tag, loc, ctx, attempts, COMPACT_STALE_MAX_ATTEMPTS))
    return logs


# --------------------------------------------------------------------------- #
# Job 17 — HARD CONTEXT CEILING BACKSTOP (#69, 2026-07-26 live incident).
#
# Job 14 only fires at a completed-ticket boundary (its Stop hook records the
# request). Job 15 only fires once a pane has been genuinely IDLE for
# `COMPACT_MIN_IDLE_S` (20 min). Measured live 2026-07-26, a WHOLE CLASS of
# sessions has NEITHER trigger: the gatekeeper master loop (a continuous
# review/merge loop across repos, 3460 turns / 9 compactions / zero `Work
# Complete` reports — job 14 has nothing to fire from) and the supervisor/
# governance session on dev1 (340K context, never compacted all day — it ends
# every turn `⏳ WORKING` per `message-status-marker.md`, so the Stop hook
# never records a job-14 request, and it is CONTINUOUSLY busy dispatching
# subagents so it is never idle 20 minutes for job 15 either).
#
# This job is the BACKSTOP: independent of both the ticket boundary AND idle
# duration. Above `COMPACT_HARD_CEILING` (deliberately its OWN threshold,
# separate from job 14's floor `COMPACT_BOUNDARY_MIN_CONTEXT` (200K, #48) and
# job 15's own `COMPACT_CONTEXT_THRESHOLD` (400K) — sitting BETWEEN the two),
# `COMPACT_MIN_IDLE_S` is IGNORED entirely: delivery only needs the pane to be
# in a state a short `send-keys` can reach at all (a real input boundary
# exists — `kind != "no-input-line"`, no open dialog, not in copy-mode). This
# is the ONE deliberate divergence from job 15: job 15 treats `kind == "busy"`
# as a skip (it wants proof of genuine stillness before compacting); THIS job
# treats "busy" as a perfectly fine send target, reusing the exact insight
# #65 already proved live: a SHORT `send-keys` command reliably QUEUES even
# into a BUSY pane — CC types it as the next turn the moment the current one
# ends, so nothing in flight is interrupted. That is precisely what makes
# this a real backstop for a CONTINUOUSLY busy session (the master loop, the
# supervisor) rather than one more mechanism that also needs stillness to
# fire. For the SAME reason this job deliberately does NOT check
# `_pane_has_bg_agent` (unlike job 15) — queuing `/compact` behind a running
# background agent does not kill or interrupt that agent, it only adds one
# more turn to the front of the queue; skipping on a live bg-agent would
# re-create the exact gap this job exists to close, since the supervisor's
# whole failure mode IS "always has a worker in flight". A DRAFT is still
# handled exactly like #67: stashed around the delivery, never typed over.
#
# Dedup / re-fire safety, THREE layers:
#   1. `handled` (a set run_once builds fresh each sweep and threads through
#      job 14 -> job 15 -> job 17, in that order): a sid job 14 or job 15
#      ALREADY sent `/compact` into THIS SAME sweep is skipped outright —
#      the deterministic, race-free regression lock for "the ticket-boundary
#      path stays primary and this backstop never double-fires against it"
#      (#69 acceptance; see `RunOnceCompactHardCeilingWiring.
#      test_ticket_boundary_fires_first_and_ceiling_never_double_fires` in
#      tests/test_watchdog.py). This is the mechanism that actually decides
#      ordering — job 14/15 simply run first in run_once and populate the
#      set before job 17 ever looks at it.
#   2. `_pane_compacting` — defense in depth for a LATER sweep: if the
#      pane's OWN current text shows CC's "Compacting conversation" progress
#      indicator (from a manual user /compact, or a prior sweep's send that
#      hasn't cleared PENDING_CONFIRM yet), resending would just queue a
#      redundant second compaction. Checked right after the pane capture,
#      before any send.
#   3. The STATE MACHINE below (`state['compact_ceiling'][sid]`), the same
#      shape as job 15's `compact_stale` — `absent` (eligible) /
#      `COMPACT_STALE_PENDING_CONFIRM` (sent, awaiting confirmation the
#      context actually dropped) / `True` (gave up for good after
#      `COMPACT_CEILING_MAX_ATTEMPTS`, permanent, Discord-pinged once). While
#      PENDING, a session is left ALONE until `COMPACT_CEILING_RETRY_S` has
#      elapsed since the last send — resending immediately would just stack
#      another queued `/compact` behind one that may simply not have run yet
#      (a continuously-busy pane can take a long time to reach the head of
#      its own queue). Once the retry window elapses AND the context still
#      reads at/above the ceiling, this is a genuine retry, bounded by
#      `COMPACT_CEILING_MAX_ATTEMPTS`; exceeding it gives up permanently and
#      pings the owner (unlike job 15's silent give-up — this job's whole
#      point is that NOTHING ELSE will ever compact this session, so a
#      terminal failure here must be visible).
#
# Wired in run_once right after job 16, ALWAYS on (no gating param — same
# "always on" shape as job 9's goal_autoarm and job 15 itself), since it
# depends on nothing external.
#
# --------------------------------------------------------------------------- #
# #72 (2026-07-26 live incident) -- the ORIGINAL state machine above (a fixed
# `COMPACT_CEILING_RETRY_S` timer + a `COMPACT_CEILING_MAX_ATTEMPTS` give-up)
# is broken on EXACTLY the session class job 17 exists for: a pane that stays
# BUSY in one single long turn for far longer than the retry window. Live
# proof, gatekeeper pane 0:0.0: a turn ran 1h14m; job 17 sent `/compact`,
# waited 5 minutes, saw the context STILL above the ceiling (correctly, since
# nothing had been consumed yet -- the turn was still running), and treated
# that as license to resend -- twice more, then GAVE UP while three duplicate
# `/compact` sat unconsumed in the pane's own input queue (CC only drains the
# queue at a turn boundary, so resending never helps and just plants more
# duplicates that later fire as "Not enough messages to compact"). Logging
# each send as "-> compacted" was ALSO false: only keystrokes were typed,
# nothing was verified.
#
# The fix replaces BOTH the timer and the give-up with a strict three-state
# machine, `state['compact_ceiling'][sid] = {"status": ..., "cwd": ...}`:
#
#   absent                     -- never touched, or the previous claim was
#                                  resolved (consumed or lost); eligible.
#   {"status": QUEUED, "cwd"}  -- `/compact` was typed exactly once and is
#                                  awaiting consumption. Every sweep re-reads
#                                  the CURRENT context (cheap, local, no tmux
#                                  round-trip) for a queued sid: while it is
#                                  STILL >= ceiling, do ABSOLUTELY NOTHING --
#                                  no resend, no log, no matter how much time
#                                  has passed (this is the entire #72 fix --
#                                  elapsed time is NEVER a resend trigger).
#                                  The moment it reads BELOW the ceiling --
#                                  proof a real compaction landed -- the claim
#                                  is CONSUMED: cleared back to absent and
#                                  logged as such, so a future re-growth past
#                                  the ceiling is eligible again.
#
# The ONLY other exit from QUEUED is a demonstrated LOSS of delivery: the
# `cwd` this claim was queued against now has a DIFFERENT, NEWER active
# transcript (a genuinely new session id) than the one the claim was sent
# to. Since a `/compact` keystroke lives in the OLD process's own type-ahead
# queue, a session replacement (the pane went through a restart -- `/exit`
# + relaunch always mints a fresh session id, it never resumes the old
# transcript file) means that keystroke is gone forever with the process
# that held it -- this is "the pane meanwhile went through a restart" from
# the issue, and it is the ONLY condition under which a resend is legitimate.
# Detected in a cheap PRE-PASS (pure transcript-directory reads, no tmux)
# before the main per-pane loop even runs: for every currently QUEUED entry,
# resolve the newest transcript for its recorded `cwd` and compare its
# session id against the one the claim was queued under. A mismatch logs
# FAILED, drops the stale entry, and lets the main loop's normal absent-state
# eligibility check pick the NEW session up fresh -- producing exactly ONE
# new send (the acceptance's "strata dorucenia -> jedno nove poslanie"), not
# a special-cased immediate resend.
#
# There is NO permanent give-up state anymore, and NO Discord ping for
# "tried too many times" -- the issue is explicit that a session still above
# the ceiling must NEVER be abandoned. The only Discord-visible signal that
# survives is the (unrelated, untouched) #67 stash-occupied-repeatedly ping.
#
# Coordination with #71 (compact-request delivered-dedup): #71's own
# mechanism (`compact-delivered.json`, keyed by a ticket-completion message
# hash) and this job's `handled`-set + `_pane_compacting` + QUEUED-state
# checks are structurally independent but serve the SAME goal -- "one
# trigger produces exactly one /compact". #71 closes it for job 14/#65's
# ticket-boundary trigger; this fix closes the identical gap for job 17's
# OWN ceiling trigger, so neither source can now double-fire against
# itself, and the pre-existing `handled` set still keeps them from
# double-firing against EACH OTHER within the same sweep.
# --------------------------------------------------------------------------- #

COMPACT_HARD_CEILING = 300_000     # tokens -- deliberately BETWEEN job 14's
                                   # floor (200K) and job 15's own threshold
                                   # (400K); a true backstop, so it must not
                                   # sit so high that a session dies of
                                   # neglect waiting for it

# `state['compact_ceiling'][sid]["status"]` value while a `/compact` has been
# typed and is awaiting confirmation (context drop) or a demonstrated loss
# (session replaced) -- see the #72 section comment above. Deliberately its
# OWN sentinel (not job 15's shared `COMPACT_STALE_PENDING_CONFIRM`) since
# job 17's state entries are now dicts carrying `cwd`, a distinct shape.
COMPACT_CEILING_QUEUED = "queued"

# #82 -- once a QUEUED entry has been observed still above the ceiling for
# this many consecutive sweeps (~30 sweeps * the 60s cadence = ~30 minutes),
# the job starts LOGGING it every sweep as STUCK instead of the silent
# `continue` that let the live incident run for HOURS before an accident
# uncovered it. Never a resend trigger -- purely a visibility fix.
COMPACT_CEILING_STUCK_CYCLES = 30

COMPACTING_MARKER = "Compacting conversation"


def _pane_compacting(captured):
    """True if the pane's OWN current text shows CC's "Compacting
    conversation" progress indicator -- a `/compact` already in flight, sent
    by THIS job, job 14, job 15, or #65's synchronous delivery (the
    indicator does not care who triggered it). Resending `/compact` while
    this shows would just queue a redundant second compaction (#69)."""
    return COMPACTING_MARKER in (captured or "")


def compact_hard_ceiling(now, run, state, dry_run=False, projects_dir=None,
                         send_fn=None, ceiling=None, handled=None,
                         stuck_cycles=None):
    """Job 17 — see the section comments above (#69 for the job itself, #72
    for this state machine).

    Local, cheap checks (dedup state, current context -- read off the
    transcript file, no tmux round-trip) run BEFORE any tmux call, mirroring
    job 15's own cost discipline: the overwhelmingly common case (a session
    under the ceiling) costs nothing beyond a transcript read and is never
    logged.

    `handled` (#69, optional): the SAME per-sweep set run_once threads
    through job 14 and job 15 — a sid already in it was JUST compacted by
    one of them this very sweep (their own capture may not show "Compacting
    conversation" yet), so this job skips it outright rather than racing a
    second `/compact` into the same pane. This is the concrete regression
    lock for "the ticket-boundary path stays primary and this backstop never
    double-fires against it" (#69 acceptance).

    #72: a QUEUED claim is NEVER resent on a timer and NEVER permanently
    given up -- it only resolves via CONSUMED (context confirmed below the
    ceiling) or FAILED (the claim's `cwd` now belongs to a different, newer
    session -- a demonstrated delivery loss, detected in a pre-pass before
    any pane is even visited). See the section comment for the full
    rationale.

    #78: the SHARED `/compact` claim (`compact_claim_active`) is checked
    BEFORE this job's OWN `compact_ceiling` state machine (above) -- if
    another sender already has an outstanding claim for this sid, this job
    skips it entirely this sweep. Every real send (idle or stash) also SETS
    the shared claim via `compact_claim_set`, so job 14/15/the sync path
    see it too. This job's #72 state machine is UNCHANGED -- it still
    decides its OWN re-trigger cadence against the ceiling; the shared
    claim answers the separate, narrower "does anyone already have one in
    flight" question.

    #82: both this job's OWN `compact_ceiling` entries AND the shared claim
    now also carry a process fingerprint (`_pane_claude_proc_fingerprint`)
    captured at send time, resolved via `pid`/`starttime` from
    `/proc/<pid>/stat`. A watchdog-driven restart of THAT process
    (`_restart_pane`, jobs 12/18) relaunches via `claude -c`, which
    CONTINUES the same transcript -- so the session id never changes and
    neither CONSUMED nor the pre-pass's session-id-replace check can ever
    fire for it. The fingerprint closes that gap: a missing or
    different-starttime process is a demonstrated delivery loss (FAILED),
    independent of session id. `stuck_cycles` (default
    `COMPACT_CEILING_STUCK_CYCLES`, env
    `AIRULESET_COMPACT_CEILING_STUCK_CYCLES`) bounds how many consecutive
    still-queued-and-above-ceiling sweeps pass silently before this job
    starts logging the entry as STUCK every sweep -- never a resend
    trigger, purely so a genuine wedge (neither CONSUMED nor FAILED ever
    fires) is visible in journalctl instead of silently skipped for hours.

    Only once a session is at/above `ceiling` (default `COMPACT_HARD_CEILING`,
    env `AIRULESET_COMPACT_HARD_CEILING`) is a tmux round-trip made at all --
    `pane_in_mode` / `pane_waiting_on_user` / `_classify_boundary` guard the
    genuinely unsafe states (copy-mode, an open dialog, no locatable
    boundary at all) exactly like jobs 12/14/15 do, reusing the identical
    helpers. Unlike job 15, `kind == "busy"` is NOT a skip here -- see the
    section comment's rationale (#65's proven busy-pane queuing). A DRAFT
    still goes through `_compact_stash_attempt` (#67) exactly like job
    14/15 -- shares the SAME `state['compact_stash_skips']` counter, since
    an occupied stash slot is the same real-world condition regardless of
    which job hit it.

    Best-effort: exceptions are the caller's (run_once's) responsibility to
    catch, same as every other job."""
    run = run or _default_run
    projects_dir = projects_dir or PROJECTS_DIR
    if ceiling is None:
        try:
            ceiling = int(os.environ.get("AIRULESET_COMPACT_HARD_CEILING",
                                         COMPACT_HARD_CEILING))
        except ValueError:
            ceiling = COMPACT_HARD_CEILING
    if stuck_cycles is None:
        try:
            stuck_cycles = int(os.environ.get(
                "AIRULESET_COMPACT_CEILING_STUCK_CYCLES",
                COMPACT_CEILING_STUCK_CYCLES))
        except ValueError:
            stuck_cycles = COMPACT_CEILING_STUCK_CYCLES
    compacted = state.get("compact_ceiling") or {}
    logs = []

    def _save():
        state["compact_ceiling"] = compacted

    # #72 PRE-PASS: a demonstrated delivery LOSS is the ONLY legitimate
    # resend trigger -- detected here, independent of which (if any) live
    # tmux pane the main loop below happens to visit this sweep. A QUEUED
    # claim whose recorded `cwd` no longer resolves to the SAME session id
    # means the process that held our typed `/compact` in its own input
    # queue is gone (a restart always mints a fresh session id) -- the
    # keystroke is lost with it. Dropping the stale claim here lets the
    # main loop's normal absent-state eligibility check pick the new
    # session up fresh, producing exactly ONE new send.
    for stale_sid, entry in list(compacted.items()):
        if not isinstance(entry, dict) or entry.get("status") != COMPACT_CEILING_QUEUED:
            continue                          # unrecognized/legacy shape --
                                               # leave it, main loop below
                                               # treats it as fresh-eligible
        # #83 -- an entry with NO "proc" key at ALL (queued before #82, or
        # whose pane could not be fingerprinted at send time) has nothing
        # that can ever resolve it: the process-death check just below is a
        # no-op, and for a watchdog restart that relaunches via `claude -c`
        # (CONTINUES the same transcript) the session-id-replace check
        # further down can never fire either -- exactly the shape that
        # stayed queued FOREVER on gatekeeper (it never even reached the
        # STUCK cycle counter in the main loop below, since the SHARED
        # compact_claim_active gate blocked this sid before this job's own
        # state machine ever ran). Drop it here -- logged as STUCK for
        # visibility instead of a silent, permanent wedge -- and let the
        # main loop below pick the session up fresh this SAME sweep.
        if "proc" not in entry:
            compacted.pop(stale_sid, None)
            _save()
            logs.append("STUCK (compact-ceiling) %s -> no process "
                        "fingerprint recorded (pre-#82 claim), dropping, "
                        "resend enabled" % (entry.get("cwd") or stale_sid))
            continue
        # #82 -- a demonstrated PROCESS-death/reuse loss is checked FIRST
        # (cheap: a /proc read, no transcript walk at all). This is what
        # catches a watchdog-driven restart that relaunches via `claude -c`
        # and so CONTINUES the SAME transcript -- the session-id-replace
        # check below can never fire for that case, since the session id
        # never changes on a `-c` resume. `proc` is guaranteed present here
        # (the check above already dropped/continued any entry without it).
        proc = entry.get("proc")
        if proc and _proc_fingerprint_alive(proc) is False:
            compacted.pop(stale_sid, None)
            _save()
            logs.append("FAILED (compact-ceiling) %s -> delivery lost "
                        "(process gone/replaced), resending once for the "
                        "same session" % (entry.get("cwd") or stale_sid))
            continue
        cwd = entry.get("cwd") or ""
        tinfo = find_active_transcript(projects_dir, cwd) if cwd else None
        current_sid = tinfo[0].stem if tinfo else None
        if current_sid == stale_sid:
            continue                          # same session -- still
                                               # genuinely queued, untouched
        compacted.pop(stale_sid, None)
        _save()
        logs.append("FAILED (compact-ceiling) %s -> delivery lost (session "
                    "replaced), resending once for the new session" % cwd)

    for pid, cwd, cmd in _reconcile_candidate_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, _tmtime = tinfo
        sid = tpath.stem
        if handled is not None and sid in handled:
            continue                          # job 14/15 already compacted
                                               # this sid THIS sweep (#69)
        # #78 -- the SHARED claim gate, checked BEFORE this job's OWN
        # compact_ceiling state machine: if ANY sender already has an
        # outstanding, unresolved /compact claim for this sid, skip it
        # entirely this sweep -- this job's own state machine below still
        # decides ITS re-trigger cadence, the shared claim decides whether
        # anyone else already has one in flight right now.
        if compact_claim_active(sid, cwd, projects_dir=projects_dir):
            continue
        entry = compacted.get(sid)
        if isinstance(entry, dict) and entry.get("status") == COMPACT_CEILING_QUEUED:
            # #72: cheap, local, no tmux round-trip for the common (still
            # above ceiling) case -- NEVER resend here, no matter how long
            # this has been queued. The only way out is CONSUMED (below) or
            # FAILED (the pre-pass above, on a LATER sweep).
            ctx = transcript_current_context(tpath)
            if ctx < ceiling:
                compacted.pop(sid, None)
                _save()
                loc = _pane_location(pid, run) or pid
                logs.append("CONSUMED (compact-ceiling) %s -> compact "
                            "verified (context confirmed below ceiling), "
                            "eligible again" % loc)
            else:
                # #82 -- a queued claim that stays above the ceiling for a
                # long time must be VISIBLE, not silently skipped every
                # sweep forever (exactly what let the live incident run for
                # HOURS before an accident uncovered it). Never a resend
                # trigger by itself -- only makes the wedge loggable.
                cycles = entry.get("cycles", 0) + 1
                entry["cycles"] = cycles
                _save()
                if cycles >= stuck_cycles:
                    loc = _pane_location(pid, run) or pid
                    logs.append("STUCK (compact-ceiling) %s ctx=%d queued "
                                "%d cycles, still unresolved (no CONSUMED, "
                                "no FAILED)" % (loc, ctx, cycles))
            continue                          # still queued+high, or just
                                               # consumed -- never send here
        ctx = transcript_current_context(tpath)
        if ctx < ceiling:
            continue                          # under the ceiling -- no log (common)
        loc = _pane_location(pid, run) or pid
        if pane_in_mode(pid, run):
            logs.append("skip in-mode (compact-ceiling) %s %d" % (loc, ctx))
            continue
        captured = capture_pane(pid, run, lines=40)
        if pane_waiting_on_user(captured):
            logs.append("skip dialog-open (compact-ceiling) %s %d" % (loc, ctx))
            continue
        if _pane_compacting(captured):
            logs.append("skip already-compacting (compact-ceiling) %s %d"
                        % (loc, ctx))
            continue
        kind, draft = _classify_boundary(captured)
        if kind == "no-input-line":
            logs.append("skip no-input-line (compact-ceiling) %s %d" % (loc, ctx))
            continue
        # NOTE: no `kind == "busy": skip` branch -- see the section comment.
        if draft:
            if dry_run:
                logs.append("READY (compact-ceiling, draft) %s ctx=%d"
                            % (loc, ctx))
                continue
            project = project_label(cwd)
            owner = pane_owner(pid, run)
            delivered = _compact_stash_attempt(pid, run, captured, state, sid,
                                               loc, project, owner=owner,
                                               ctx=ctx, send_fn=send_fn)
            if not delivered:
                logs.append("skip draft (stash occupied) (compact-ceiling) "
                            "%s %d: %r" % (loc, ctx, draft[:40]))
                continue
            tag = "compact-ceiling, stash"
        else:
            if dry_run:
                logs.append("READY (compact-ceiling) %s ctx=%d kind=%s"
                            % (loc, ctx, kind))
                continue
            send_continue(pid, COMPACT_TEXT, run)
            tag = "compact-ceiling"
        # #82 -- fingerprint the process the keystrokes were actually
        # delivered to (resolved ONCE, shared between the two state
        # machines below) so a later restart of THIS PROCESS is a
        # detectable delivery loss even when the session id never changes
        # (a `-c` resume). None in the common test/no-pane-info case --
        # both call sites simply omit the "proc" key then, unchanged from
        # before this fix.
        proc = _pane_claude_proc_fingerprint(pid, run=run)
        compact_claim_set(sid, cwd, proc=proc)   # #78/#82 -- keystrokes typed, claim it
        entry_out = {"status": COMPACT_CEILING_QUEUED, "cwd": cwd}
        if proc:
            entry_out["proc"] = proc
        compacted[sid] = entry_out
        _save()
        logs.append("OK (%s) %s ctx=%d -> /compact sent, awaiting consumption"
                    % (tag, loc, ctx))
    return logs


# --------------------------------------------------------------------------- #
# Job 18 — HOOKS RECONCILE, restart-based (#70, 2026-07-26 live incident). CC
# snapshots its hook set ONCE at process START — `rCu()` / the telemetry event
# `setup_hooks_captured`, read directly out of the CC 2.1.220 binary — and
# NEVER re-reads it. There is no code path that calls `rCu()` a second time.
# So a hook merged into `settings.json` while a session is ALREADY RUNNING
# has ZERO effect on that session for its ENTIRE remaining lifetime, no
# matter how many new hooks get deployed afterward — silently nullifying
# every hook this repo has ever shipped into a long-lived loop (concretely,
# #66's Bash-guard hook landed at `ed83955` but the gatekeeper master loop,
# running since before that commit, never picked it up).
#
# This job is job 12's EXACT restart machinery (`_restart_pane`,
# `_pane_has_bg_agent`, the boundary-classification guards) driven by a
# DIFFERENT staleness signal: the CONTENT hash (never mtime — a touch/rewrite
# with identical bytes must never trigger a restart) of the effective
# settings.json `"hooks"` block, rather than a target-model string read out
# of the transcript. Unlike job 12, there is no way to introspect from a
# transcript what hook set a running process currently has loaded in memory
# — hooks are never recorded per-message — so this job tracks its OWN
# baseline per session id (`state['hooks_session_hash']`), bootstrapped on
# the FIRST sweep it ever observes a given sid (there is no way to know
# retroactively what hash a session ALREADY RUNNING at this job's own deploy
# time actually started with, so the first observation is treated as the
# baseline — this only ever matters once, at this job's own rollout; every
# hooks change from then on is caught exactly, because the baseline was
# recorded before that change landed). A LATER sweep where the CURRENT hash
# no longer matches the stored baseline is what proves the session is
# genuinely stale, and only then does it restart.
#
# Dedup / never-retry-a-success, mirroring job 12's shape exactly:
# `state['hooks_restarted'][sid]` is CLAIMED right before the restart
# keystrokes are sent and is only ever DELETED again on a FAILED restart
# BELOW the attempt cap — a real success is never retried, a failed attempt
# is retried on a later sweep instead of being stuck forever either way.
# `state['hooks_restart_attempts'][sid]` counts failures; once it reaches
# `HOOKS_RECONCILE_MAX_ATTEMPTS` the session GIVES UP for good (never
# retried again), the exact same bounded-retry shape job 12/15/17 use.
#
# Coalescing with job 12 (#70's own explicit ask: "one restart, not two"):
# run_once threads a SHARED per-sweep `handled` set into BOTH job 12 and this
# job — a sid job 12 already restarted (or attempted to restart) THIS sweep
# for a MODEL change is skipped outright here, so a hooks change landing in
# the same sweep as a model change coalesces into the ONE restart job 12
# already performs.
#
# Fails CLOSED on any uncertainty (#70 hard constraint: "make the restart
# path fail CLOSED on any uncertainty"): an unreadable/missing/invalid
# settings.json returns `None` from `_hooks_config_hash` and disables the
# WHOLE job for that sweep — it never guesses "hash changed" from a read
# failure, which would restart every live session on the box for no real
# reason.
# --------------------------------------------------------------------------- #

HOOKS_RECONCILE_MAX_ATTEMPTS = 3   # same cap shape as job 12/15/17


def hooks_settings_path():
    """`~/.claude/settings.json` — the EFFECTIVE settings file Claude Code
    reads its hook set from at its own process start, resolved at CALL time
    (never a frozen module-level constant — mirrors `compact_requests_path()`'s
    own documented reasoning: `Path.home()` must be read at call time, not at
    import time)."""
    return Path.home() / ".claude" / "settings.json"


def _hooks_config_hash(settings_path=None):
    """Content hash (NEVER mtime — #70) of the `"hooks"` block inside the
    effective settings.json. `sort_keys=True` so key REORDERING with
    otherwise-identical content never looks like a change. Returns `None` on
    ANY read/parse failure (missing file, bad JSON, not even a dict) — the
    caller (`hooks_reconcile`) treats `None` as "disable this sweep", never
    as "hash changed"; see the section comment's fail-closed rationale."""
    path = settings_path or hooks_settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    hooks = d.get("hooks") or {}
    return _hash(json.dumps(hooks, sort_keys=True))


def hooks_reconcile(now, run, state, dry_run=False, projects_dir=None,
                    sleep_fn=None, settings_path=None, handled=None):
    """Job 18 — see the section comment above.

    Reuses `_reconcile_candidate_panes` (claude/node/bun, same as job 12) and
    `_restart_pane` (the identical `/exit` + relaunch + accept-"Resume from
    summary" sequence) — never a parallel restart implementation. Never
    restarts a pane that is in copy-mode, showing an open dialog, mid-turn/
    busy, holding an unsent draft, not at a bare idle prompt, or running a
    BACKGROUND AGENT (`_pane_has_bg_agent`) — the exact same guards job 12
    uses, via the exact same helpers. Best-effort: exceptions are the
    caller's (run_once's) responsibility to catch, same as every other job."""
    cur_hash = _hooks_config_hash(settings_path)
    if cur_hash is None:
        return []                      # fail closed — unreadable settings.json
    run = run or _default_run
    sleep_fn = sleep_fn or time.sleep
    projects_dir = projects_dir or PROJECTS_DIR
    session_hash = state.get("hooks_session_hash") or {}
    restarted = state.get("hooks_restarted") or {}
    attempts_map = state.get("hooks_restart_attempts") or {}
    logs = []
    for pid, cwd, cmd in _reconcile_candidate_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, _tmtime = tinfo
        sid = tpath.stem
        if restarted.get(sid):
            continue                   # already restarted (or gave up) — never retry
        known = session_hash.get(sid)
        if known is None:
            session_hash[sid] = cur_hash
            state["hooks_session_hash"] = session_hash
            continue                   # bootstrap: first sight, assume current hash
        if known == cur_hash:
            continue                   # up to date — the common case, no log
        if handled is not None and sid in handled:
            continue                   # job 12 already restarting this sid this sweep (#70)
        loc = _pane_location(pid, run) or pid
        if pane_in_mode(pid, run):
            logs.append("skip in-mode (hooks-reconcile) %s" % loc)
            continue
        captured = capture_pane(pid, run, lines=40)
        if pane_waiting_on_user(captured):
            logs.append("skip dialog-open (hooks-reconcile) %s" % loc)
            continue
        kind, draft = _classify_boundary(captured)
        if kind == "no-input-line":
            logs.append("skip no-input-line (hooks-reconcile) %s" % loc)
            continue
        if kind == "busy":
            logs.append("skip busy (hooks-reconcile) %s" % loc)
            continue
        if draft:
            logs.append("skip draft (hooks-reconcile) %s: %r" % (loc, draft[:40]))
            continue
        if not pane_at_idle_prompt(captured):
            logs.append("skip not-idle (hooks-reconcile) %s" % loc)
            continue
        if _pane_has_bg_agent(captured):
            logs.append("skip bg-agent (hooks-reconcile) %s" % loc)
            continue
        if dry_run:
            logs.append("READY (hooks-reconcile) %s (restart)" % loc)
            continue
        restarted[sid] = True
        state["hooks_restarted"] = restarted
        ok, reason = _restart_pane(pid, run, sleep_fn, captured)
        if ok:
            logs.append("OK restart (hooks changed) %s" % loc)
            attempts_map.pop(sid, None)
            state["hooks_restart_attempts"] = attempts_map
        else:
            attempts = attempts_map.get(sid, 0) + 1
            attempts_map[sid] = attempts
            state["hooks_restart_attempts"] = attempts_map
            if attempts >= HOOKS_RECONCILE_MAX_ATTEMPTS:
                logs.append("GAVE UP restart (hooks changed) %s after %d attempts (%s)"
                            % (loc, attempts, reason))
            else:
                del restarted[sid]
                state["hooks_restarted"] = restarted
                logs.append("FAIL restart (hooks changed) %s (%s, attempt %d/%d)"
                            % (loc, reason, attempts, HOOKS_RECONCILE_MAX_ATTEMPTS))
    return logs


# --------------------------------------------------------------------------- #
# Job 20 — GOAL RE-ARM BACKSTOP (#76, 2026-07-26 live incident, montalu@subdev).
#
# An armed `/goal` dies SILENTLY. Not (only) on a restart, as #76 originally
# assumed — the montalu stream runs in ONE continuous transcript since
# 2026-06-15 (240 MB, one session id) and the loop died TWICE in one day with
# no restart at all, correlated with `/compact` (survived one compaction,
# died at the next). The exact mechanism is NOT established: gatekeeper
# survived 7+ compactions the same day with its goal intact, so compaction
# alone is plainly not sufficient. What IS measured, and what this job keys
# on, is the OBSERVABLE END STATE:
#
#   * NO `Goal cleared:` marker is ever written when this happens. That marker
#     is `<local-command-stdout>` — the OUTPUT OF THE `/goal` SLASH COMMAND
#     itself — so it only exists when a human (or this job) runs `/goal`.
#     Whatever disarms the loop otherwise leaves the transcript's last marker
#     saying `set`.
#   * Therefore EVERY transcript-based goal detector is fooled — the
#     `block-main-implementation.sh` goal-armed path (#54), and any re-arm
#     mechanism built on the transcript alone — and nothing ever alerts. The
#     user found the stream parked on a finished ticket ~6 hours later, twice.
#
# Hence the ticket's own demand: detection needs TWO independent sources.
#   INTENT  = the transcript marker (`scan_goal_markers`): the last
#             `<local-command-stdout>Goal set:` with no later `Goal cleared:`.
#             Verified live against this repo's own session: the marker body
#             is BYTE-IDENTICAL to the `/goal ` line that armed it (1612 chars
#             both ways), so it is a faithful re-arm payload — unlike the
#             rendered viewport, which hard-wraps a long goal (the 166-of-3100
#             gk arm, 2026-07-20).
#   REALITY = CC's own footer indicator `◎ /goal` (`pane_goal_armed`), read
#             ONLY from the pane's trailing chrome region (the same
#             `_is_bottom_chrome` peel every other job here uses) — a pane
#             whose CONVERSATION merely quotes the indicator (this ticket's
#             own discussion) must never read as armed.
# INTENT says armed + REALITY says dark = this exact failure -> re-arm.
#
# This ALSO covers #76's original restart case with no per-job patching: a
# watchdog restart (jobs 12/18) relaunches via `claude -c`, which CONTINUES
# the same transcript, so the marker still says `set` while the fresh process
# has no goal — precisely the mismatch above, healed on the next sweep.
#
# Bounded, because a goal can also disarm by legitimately RESOLVING (the
# evaluator confirming the condition writes no marker either). Re-arming that
# would spin one full-context turn per sweep forever. So: at most
# `GOAL_REARM_MAX_ATTEMPTS` deliveries per `GOAL_REARM_STREAK_S` window per
# session, ONE Discord ping on give-up, and the streak resets by TIME (never
# by a confirmation — a confirm-then-die-again loop must still hit the cap)
# so a stream that genuinely dies once or twice a day keeps healing itself.
#
# Delivery discipline (the payload is ~1.6-3.4 KB — far past what a short
# `send-keys` can be assumed to queue safely, #65's busy-pane guarantee does
# NOT extend to it): FREE PROMPT ONLY, every step verified against a fresh
# capture, never a second Escape, and NEVER `Enter` after a type-verify
# failure — a truncated goal is exactly the #36 disaster. A foreign draft
# goes through `deliver_with_stash` (#35), never typed over. A pane already
# compacting, holding an outstanding shared `/compact` claim (#78), or
# compacted THIS sweep (#69's `handled` set) is skipped — the compaction
# comes first, the re-arm heals afterwards.
# --------------------------------------------------------------------------- #

GOAL_INDICATOR = "◎ /goal"          # CC's own armed-goal footer indicator
GOAL_ACHIEVED_MARKER = "Goal achieved"   # CC's own completion line, printed
                                    # into the CONVERSATION (never persisted
                                    # to the transcript) when the evaluator
                                    # confirms the condition
_GOAL_LCS_OPEN = "<local-command-stdout>"
_GOAL_LCS_CLOSE = "</local-command-stdout>"

# Bootstrap window for a session this job has never scanned before. Later
# sweeps read ONLY the bytes appended since the stored offset, so the steady
# state costs ~nothing regardless of how long ago the goal was armed.
GOAL_MARK_TAIL_BYTES = 4_000_000

GOAL_REARM_CONFIRM_S = 120          # after typing, allow this long for the
                                    # `◎ /goal` indicator to light before
                                    # calling the delivery a failure
GOAL_REARM_MAX_ATTEMPTS = 2         # deliveries per streak window (the
                                    # ticket's "try once, then ping, never a
                                    # loop" — one retry for a genuinely lost
                                    # keystroke, then stop)
GOAL_REARM_STREAK_S = 2 * 3600      # streak window; resets by TIME only
GOAL_REARM_MAX_PAYLOAD = 12_000     # refuse to type anything larger

# --- the SECOND shape (same job, opposite reading of the indicator) --------
# The 2026-07-26 forensics (montalu + gatekeeper transcripts and journals,
# read side by side) does NOT support "a /compact disarms the goal". It
# supports "the loop loses its STIMULUS": every SURVIVAL had something arrive
# after the compaction — a background subagent's task-notification (montalu
# 04:44, 155 ms later) or the human typing — and every DEATH had a `/compact`
# land at a `## ✅ Work Complete` boundary with NO worker in flight, after
# which nothing fired again until a human intervened (montalu 05:15:55 via job
# 14's idle poll, 14:38:57 via #65's synchronous path — DIFFERENT senders,
# identical outcome, so the delivery mechanism is not the discriminator).
#
# Nothing covered that: job 4, the only "idle when it should be working"
# nudge, is hard-gated on the last marker being `⏳ WORKING`, while a
# completed ticket inside an armed loop correctly ends `✅ DONE`
# (message-status-marker.md, 2026-07-25). So "goal ARMED, last turn ✅, then
# silence" was a total blind spot.
#
# Honest limit: it is NOT established that a local `/compact` is structurally
# incapable of re-firing CC's own goal Stop hook — the pattern is consistent
# across both deaths and absent in every survival, but it is an inference from
# transcript timing, not documented CC behavior. Which is exactly why this
# branch keys on the OBSERVABLE end state (armed + idle + nothing in flight)
# rather than on any theory of the cause.
GOAL_STALL_TEXT = "continue"        # same minimal wake job 1 uses
GOAL_STALL_IDLE_S = 15 * 60         # transcript stale this long while ARMED
GOAL_STALL_INTERVAL_S = 15 * 60     # min spacing between nudges
GOAL_STALL_MAX_NUDGES = 3           # then ONE ping, then silence


def _goal_marker_content(entry):
    """TOP-LEVEL string content of a transcript entry — the ONLY shapes CC
    writes a `local-command-stdout` marker as (`user` with a plain-string
    `.message.content`, or `system` with a plain-string `.content`). A NESTED
    `tool_result` is structurally excluded: its content is always a list, so
    a session that grepped ANOTHER session's transcript can never be misread
    as its own goal state (this repo's own CLAUDE.md, #54)."""
    if not isinstance(entry, dict):
        return None
    if entry.get("type") == "user":
        msg = entry.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"]
        return None
    if entry.get("type") == "system" and isinstance(entry.get("content"), str):
        return entry["content"]
    return None


def _parse_goal_marker(content):
    """`{"state": "set"|"cleared", "payload": str|None}` for a genuine `/goal`
    marker, else None. The whole entry content must BE the marker (it starts
    with the `<local-command-stdout>` tag) — a compaction SUMMARY narrating
    "the loop's Goal set: …" in prose is not state."""
    s = (content or "").strip()
    if not s.startswith(_GOAL_LCS_OPEN):
        return None
    body = s[len(_GOAL_LCS_OPEN):]
    if body.endswith(_GOAL_LCS_CLOSE):
        body = body[:-len(_GOAL_LCS_CLOSE)]
    for kind in ("set", "cleared"):
        head = "Goal %s:" % kind
        if body.startswith(head):
            payload = body[len(head):].strip()
            return {"state": kind, "payload": payload or None}
    return None


def scan_goal_markers(path, off=None, tail_bytes=GOAL_MARK_TAIL_BYTES):
    """`(new_off, marker_or_None)` — the NEWEST `/goal` marker in the bytes
    read, plus the offset to resume from next time.

    `off is None` bootstraps from the file's TAIL (`tail_bytes`); a later call
    passing the returned offset reads ONLY what was appended since, so a
    long-lived 240 MB transcript costs one small read per sweep and the
    marker's AGE never matters (the caller remembers the last marker it saw).
    An `off` past EOF (a truncated/rotated file) falls back to the bootstrap.

    `new_off` always stops after the LAST COMPLETE line — a transcript is
    appended to live, so the final line may be half-written; consuming it
    would drop a real marker on the next pass.

    Fail-safe: an unreadable file returns `(off or 0, None)`; never raises."""
    from datetime import datetime
    try:
        size = os.path.getsize(path)
    except OSError:
        return (off or 0, None)
    start = off
    if start is None or start > size or start < 0:
        start = max(0, size - tail_bytes)
    try:
        with open(path, "rb") as f:
            f.seek(start)
            raw = f.read()
    except OSError:
        return (off or 0, None)
    cut = raw.rfind(b"\n")
    if cut < 0:
        return (start, None)              # no complete line in this window
    new_off = start + cut + 1
    body = raw[:cut]
    if start > 0 and off is None:
        # bootstrap mid-file: the first line is probably truncated
        nl = body.find(b"\n")
        body = body[nl + 1:] if nl >= 0 else b""
    best = None
    for ln in body.splitlines():
        if _GOAL_LCS_OPEN.encode() not in ln:
            continue
        try:
            entry = json.loads(ln)
        except Exception:
            continue
        mark = _parse_goal_marker(_goal_marker_content(entry))
        if mark is None:
            continue
        ts = None
        try:
            ts = datetime.fromisoformat(
                str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = None
        mark["ts"] = ts
        best = mark
    return (new_off, best)


def pane_goal_armed(captured):
    """`True` / `False` / `None` — is CC's `◎ /goal` footer indicator lit?

    Read ONLY from the pane's trailing CHROME region (the `_is_bottom_chrome`
    peel every keystroke job here shares): the indicator renders on the `ctx …`
    statusline row, and a pane whose CONVERSATION merely mentions `◎ /goal`
    (a session discussing this very ticket) must never read as armed.

    `None` = UNDETERMINABLE, never a guess: the input box could not be located
    (a BUSY pane's spinner occupies the boundary, a scrolled pane, an empty
    capture) or nothing was rendered below it, so neither "armed" nor "dark"
    can be claimed.

    The footer is defined as everything BELOW the input box — NOT as
    "`_is_bottom_chrome` rows" and NOT as "the row starting with `ctx `".
    Live 2026-07-26: a freshly launched session renders the managed statusline
    without caveman's `ctx …` block, a row `_is_bottom_chrome` does not
    classify as chrome at all — so a peel-based read stopped ABOVE the very
    row the indicator sits on, and a `ctx `-prefix guard declared the whole
    pane unreadable. A SELECTED agent-strip row (`❯ ● main`, #36) renders
    below the box and is excluded from the boundary search."""
    lines = (captured or "").splitlines()
    idx = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("❯") and not _is_bottom_chrome(s):
            idx = i
    if idx is None:
        return None                        # no input box in view
    footer = lines[idx + 1:]
    if not any(ln.strip() for ln in footer):
        return None                        # nothing rendered below it
    return any(GOAL_INDICATOR in ln for ln in footer)


def _send_goal_verified(pid, text, run, captured=None):
    """Type a LONG `/goal …` into a BARE input box and submit it, verifying
    every step against a fresh capture — the same protocol
    `deliver_with_stash` uses for its own type/submit steps (steps 5-7), minus
    the stash (there is no draft here).

    NEVER presses Enter after a type-verify failure: submitting a truncated
    goal is the exact #36 disaster this job exists to avoid. NEVER sends two
    consecutive Escapes (that permanently deletes a draft, #35). Returns True
    only when the box is provably empty again after the submit."""
    run = run or _default_run
    cap = captured if captured is not None else capture_pane(pid, run, lines=40)
    if _input_line_text(cap) != "":
        return False                       # not a bare box — caller's problem
    if _strip_selected(cap):
        run(["tmux", "send-keys", "-t", pid, "Escape"])
    run(["tmux", "send-keys", "-t", pid, "-l", text])
    cap = capture_pane(pid, run, lines=40)
    itext = _input_line_text(cap)
    if not _typed_landed(text, itext):
        return False                       # partial type — never submit it
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    cap = capture_pane(pid, run, lines=40)
    itext2 = _input_line_text(cap)
    if _typed_landed(text, itext2):
        # swallowed submit (the #36 agent-strip class) — ONE corrective
        # Escape+Enter, never a second bare Enter, never two Escapes.
        run(["tmux", "send-keys", "-t", pid, "Escape"])
        run(["tmux", "send-keys", "-t", pid, "Enter"])
        cap = capture_pane(pid, run, lines=40)
        itext3 = _input_line_text(cap)
        if _typed_landed(text, itext3):
            return False
    return True


def _goal_stall_nudge(now, run, rec, sid, cwd, pid, captured, tpath, tmtime,
                      loc, send_fn, dry_run, handled, projects_dir):
    """The ARMED-but-silent branch of job 20 — see the `GOAL_STALL_*` section
    comment. Mutates `rec` (the caller persists it); returns log lines.

    A nudge fires ONLY when every one of these holds, and each refusal is a
    deliberate lock, not an oversight:
      * the transcript has been idle >= `GOAL_STALL_IDLE_S` (real progress
        RESETS the counter — the bound is tied to observed movement, never a
        blind timer);
      * the last marker is not `⏳` (job 4 owns a working-stall) and not `❓`
        (an unanswered question IS the loop's own stop condition — never
        nudge past one, the user's hardest standing rule);
      * no background worker is in flight (`_pane_has_bg_agent`) — its
        task-notification is the stimulus, still coming;
      * the pane is genuinely idle at a BARE prompt, not compacting, with no
        outstanding `/compact` claim and not compacted this sweep."""
    logs = []
    idle = now - (tmtime or now)
    if idle < GOAL_STALL_IDLE_S:
        if rec.get("sn"):                  # the loop really advanced — the
            rec.update({"sn": 0, "spinged": False})   # bound is progress-tied
        return logs
    marker = transcript_last_marker(tpath)
    if marker in ("⏳", "❓"):
        return logs                        # job 4's, or a legitimate block
    if _pane_has_bg_agent(captured) or pane_waiting_on_user(captured):
        return logs
    if _pane_compacting(captured):
        return logs
    if handled is not None and sid in handled:
        return logs
    n = rec.get("sn", 0)
    if n >= GOAL_STALL_MAX_NUDGES:
        if not rec.get("spinged"):
            rec["spinged"] = True
            if send_fn is not None and not dry_run:
                send_fn("⚠️ **%s** — `/goal` je armovaný (`◎ /goal` svieti), ale "
                        "slučka sa už %d minút nepohla a ani po %d šťuchnutiach "
                        "sa nerozbehla (%s). Pozri sa na ňu prosím — dovtedy "
                        "nič nepokračuje."
                        % (project_label(cwd), int(idle // 60),
                           GOAL_STALL_MAX_NUDGES, loc),
                        owner=pane_owner(pid, run) or None,
                        dedup_key="goalstall:%s:%d" % (sid, int(tmtime or 0)),
                        dry_run=dry_run)
            logs.append("GAVE UP (goal-stall) %s after %d nudges"
                        % (loc, GOAL_STALL_MAX_NUDGES))
        return logs
    last = rec.get("slast")
    if last is not None and (now - last) < GOAL_STALL_INTERVAL_S:
        return logs
    if compact_claim_active(sid, cwd, projects_dir=projects_dir):
        return logs
    kind, draft = _classify_boundary(captured)
    if kind != "input" or draft:
        logs.append("skip %s (goal-stall) %s" % (draft and "draft" or kind, loc))
        return logs
    if not pane_at_idle_prompt(captured):
        return logs
    if dry_run:
        logs.append("READY (goal-stall) %s idle=%dm" % (loc, idle // 60))
        return logs
    send_continue(pid, GOAL_STALL_TEXT, run)
    rec["sn"] = n + 1
    rec["slast"] = now
    logs.append("goal-stall nudge %s idle=%dm (%d/%d) -> armed goal not "
                "advancing" % (loc, idle // 60, n + 1, GOAL_STALL_MAX_NUDGES))
    return logs


def goal_rearm(now, run, state, send_fn=None, dry_run=False, projects_dir=None,
               handled=None, max_attempts=None, streak_s=None, confirm_s=None):
    """Job 20 — see the section comment above (#76). Mutates
    `state['goal_rearm']`; returns log lines. Best-effort (exceptions are
    run_once's to catch, like every other job here).

    `handled` (optional): the SAME per-sweep set jobs 14/15/17 populate — a
    sid compacted THIS sweep is skipped outright (a long goal must never be
    typed into a pane whose `/compact` is still draining)."""
    run = run or _default_run
    projects_dir = projects_dir or PROJECTS_DIR
    max_attempts = GOAL_REARM_MAX_ATTEMPTS if max_attempts is None else max_attempts
    streak_s = GOAL_REARM_STREAK_S if streak_s is None else streak_s
    confirm_s = GOAL_REARM_CONFIRM_S if confirm_s is None else confirm_s
    recs = state.get("goal_rearm") or {}
    logs = []

    def _save():
        state["goal_rearm"] = recs

    for pid, cwd, _cmd in _reconcile_candidate_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath, tmtime = tinfo
        sid = tpath.stem
        rec = recs.get(sid)
        if not isinstance(rec, dict):
            rec = {}
        # --- REALITY: the footer indicator (cheap, no transcript read) ------
        if pane_in_mode(pid, run):
            continue                       # scrolled — the footer isn't current
        # VISIBLE VIEWPORT ONLY (no -S), the same discipline job 9 learned the
        # hard way (gk 2026-07-20: a stale scrollback `/goal` line armed into a
        # fresh session). Here it bites the OTHER way round: after a `claude
        # -c` restart the scrollback still shows the DEAD session's `✔ Goal
        # achieved` line, which would veto the heal for exactly the case this
        # job exists for (live, 2026-07-26). CC redraws its own screen, so the
        # viewport is always the CURRENT session's content.
        captured = run(["tmux", "capture-pane", "-p", "-t", pid]) or ""
        armed = pane_goal_armed(captured)
        if armed is None:
            continue                       # undeterminable — never guess
        # --- INTENT: the transcript marker (incremental, offset-resumed) ----
        new_off, mark = scan_goal_markers(tpath, off=rec.get("off"))
        rec["off"] = new_off
        if mark is not None:
            rec["mark"] = mark.get("state")
            rec["payload"] = mark.get("payload")
            rec["mts"] = mark.get("ts")
            rec["mseen"] = now             # when WE first saw this marker
        recs[sid] = rec
        _save()
        loc = _pane_location(pid, run) or pid
        if armed:
            if rec.get("queued_at"):
                rec["queued_at"] = None
                logs.append("CONFIRMED (goal-rearm) %s -> ◎ /goal lit again"
                            % loc)
            # ARMED for real — but is the loop actually FIRING? (the second
            # shape, and the one the forensics points at)
            logs += _goal_stall_nudge(now, run, rec, sid, cwd, pid, captured,
                                      tpath, tmtime, loc, send_fn, dry_run,
                                      handled, projects_dir)
            _save()
            continue
        if rec.get("mark") != "set":
            continue                       # never armed here, or the user
                                           # deliberately cleared it
        if GOAL_ACHIEVED_MARKER in _above_input_box(captured):
            # CC writes NO marker for a NATURAL resolution either (live:
            # `/goal` -> `✔ Goal achieved (3s · 1 turn)` -> indicator gone,
            # transcript marker untouched), so "marker set + footer dark" also
            # describes a SUCCESSFULLY FINISHED run. Re-arming that restarts
            # finished work and ends every good run with a false "goal died"
            # ping. The transcript cannot tell the two apart — montalu's own
            # HEALTHY loop wrote `stop_hook_summary` entries with
            # `preventedContinuation: false` every ~19 min while working
            # perfectly — so the pane's own completion line is the signal.
            # Skipping is the safe direction: no wasted turns, no false ping,
            # and the stall branch still covers a loop that merely stopped.
            logs.append("skip goal-achieved (goal-rearm) %s -> loop finished "
                        "legitimately" % loc)
            continue
        payload = rec.get("payload") or ""
        if not payload or "\n" in payload or len(payload) > GOAL_REARM_MAX_PAYLOAD:
            logs.append("skip unusable-payload (goal-rearm) %s (%d chars)"
                        % (loc, len(payload)))
            continue
        h = _hash(payload)
        if rec.get("hash") != h or (now - rec.get("first", now)) > streak_s:
            rec.update({"hash": h, "n": 0, "first": now, "pinged": False})
            _save()
        # --- a delivery is in flight: confirm / expire it -------------------
        q = rec.get("queued_at")
        if q:
            mts = rec.get("mts")
            if mts is not None and rec.get("mseen", 0) > q:
                # CC echoed a FRESH `Goal set:` marker after our keystrokes,
                # yet the indicator is dark again — the goal armed and then
                # resolved itself. The attempt still counts (that is what the
                # cap is for); stop waiting on this delivery.
                rec["queued_at"] = None
                _save()
                logs.append("RESOLVED-AGAIN (goal-rearm) %s -> re-armed goal "
                            "disarmed itself" % loc)
            elif (now - q) < confirm_s:
                continue                   # grace — CC may still be arming
            else:
                rec["queued_at"] = None
                _save()
                logs.append("LOST (goal-rearm) %s -> typed /goal never armed"
                            % loc)
        if rec.get("n", 0) >= max_attempts:
            if not rec.get("pinged"):
                rec["pinged"] = True
                _save()
                if send_fn is not None and not dry_run:
                    send_fn("⚠️ **%s** — `/goal` slučka ticho zanikla (v pätičke "
                            "už nie je `◎ /goal`) a automatické prearmovanie sa "
                            "nechytilo ani po %d pokusoch (%s). Prearmuj ju "
                            "prosím ručne — dovtedy nič nebeží."
                            % (project_label(cwd), max_attempts, loc),
                            owner=pane_owner(pid, run) or None,
                            dedup_key="goalrearm:%s:%s" % (sid, h),
                            dry_run=dry_run)
                logs.append("GAVE UP (goal-rearm) %s after %d attempts"
                            % (loc, max_attempts))
            else:
                logs.append("skip gave-up (goal-rearm) %s" % loc)
            continue
        # --- coordination with the /compact senders (#69 / #78) -------------
        if handled is not None and sid in handled:
            logs.append("skip just-compacted (goal-rearm) %s" % loc)
            continue
        if compact_claim_active(sid, cwd, projects_dir=projects_dir):
            logs.append("skip compact-claim (goal-rearm) %s" % loc)
            continue
        if _pane_compacting(captured):
            logs.append("skip already-compacting (goal-rearm) %s" % loc)
            continue
        if pane_waiting_on_user(captured):
            logs.append("skip dialog-open (goal-rearm) %s" % loc)
            continue
        kind, draft = _classify_boundary(captured)
        if kind != "input":
            logs.append("skip %s (goal-rearm) %s" % (kind, loc))
            continue
        text = "/goal " + payload
        if dry_run:
            logs.append("READY (goal-rearm) %s -> %d chars" % (loc, len(text)))
            continue
        if draft:
            # never typed OVER a user's draft — stash it around the delivery
            dlogs = []
            ok = deliver_with_stash(pid, text, run, captured=captured,
                                    logs=dlogs)
            tag = "goal-rearm, stash"
        else:
            if not pane_at_idle_prompt(captured):
                logs.append("skip not-idle (goal-rearm) %s" % loc)
                continue
            dlogs = []
            ok = _send_goal_verified(pid, text, run, captured=captured)
            tag = "goal-rearm"
        rec["n"] = rec.get("n", 0) + 1
        if ok:
            rec["queued_at"] = now
            _save()
            logs.append("OK (%s) %s -> /goal re-armed (%d chars), awaiting ◎"
                        % (tag, loc, len(text)))
        else:
            _save()
            logs.append("FAIL (%s) %s -> delivery not verified%s"
                        % (tag, loc, (" (%s)" % dlogs[-1]) if dlogs else ""))
    return logs


# --------------------------------------------------------------------------- #
# Weekly token-usage alert (a 3rd reason work stalls: the WEEKLY subscription
# limit runs out). Reads Anthropic's oauth/usage window state — the same data
# `/usage` shows — and pings Discord once when a weekly window reaches a % cap.
# The endpoint is AGGRESSIVELY rate-limited (429), so it is polled at most every
# USAGE_INTERVAL (not on the 60s tmux cadence).
# --------------------------------------------------------------------------- #

USAGE_THRESHOLD = 98              # alert when a weekly window reaches this %
USAGE_INTERVAL = 15 * 60         # min seconds between usage polls (429s hard)
_OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_CC_VERSION_FALLBACK = "2.1.185"


def _cc_version():
    import subprocess
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True,
                             text=True, timeout=5).stdout
        m = re.search(r"(\d+\.\d+\.\d+)", out or "")
        return m.group(1) if m else _CC_VERSION_FALLBACK
    except Exception:
        return _CC_VERSION_FALLBACK


def _read_oauth_token():
    try:
        d = json.load(open(os.path.expanduser("~/.claude/.credentials.json")))
        return (d.get("claudeAiOauth") or {}).get("accessToken") or None
    except Exception:
        return None


def fetch_usage():
    """GET Anthropic's oauth/usage window state, or None on any error / 429. The
    `claude-code` User-Agent is REQUIRED (without it the endpoint 429s even harder)."""
    import urllib.request
    tok = _read_oauth_token()
    if not tok:
        return None
    req = urllib.request.Request(_OAUTH_USAGE_URL, headers={
        "Authorization": "Bearer " + tok,
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/" + _cc_version(),
        "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=12).read())
    except Exception:
        return None


def weekly_percent(usage):
    """(percent, resets_at, label) of the HIGHEST active WEEKLY window in the
    oauth/usage payload, or None — ANY weekly window hitting the cap stalls work."""
    best = None
    for lim in (usage or {}).get("limits", []):
        if lim.get("group") != "weekly":
            continue
        pct = lim.get("percent")
        if pct is None:
            continue
        label = "týždenný limit"
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        if model:
            label = "týždenný limit (%s)" % model
        if best is None or pct > best[0]:
            best = (float(pct), lim.get("resets_at"), label)
    return best


# --------------------------------------------------------------------------- #
# Usage cache for the statusline. The oauth/usage endpoint 429s hard, so the
# statusline can NOT poll it per render. The watchdog already fetches it every
# ~15 min (check_usage) — piggyback a tiny cache of the flattened windows so the
# statusline can show a PER-MODEL window (e.g. Fable's weekly) that CC's statusLine
# stdin `rate_limits` does not expose (stdin only carries the shared 5h + weekly).
# NB the 5-hour "session" window is account-wide (scope=null) — there is NO
# per-model 5h; the only per-model split is the weekly (`weekly_scoped`).
# --------------------------------------------------------------------------- #

_USAGE_CACHE_PATH = os.path.expanduser("~/.claude/airuleset-usage-cache.json")


def usage_windows(usage):
    """Flatten the oauth/usage limits[] into simple dicts: kind, group, percent,
    model (display_name or None for the shared windows), resets_at, is_active."""
    out = []
    for lim in (usage or {}).get("limits", []):
        pct = lim.get("percent")
        if pct is None:
            continue
        model = ((lim.get("scope") or {}).get("model") or {}).get("display_name")
        out.append({"kind": lim.get("kind"), "group": lim.get("group"),
                    "percent": int(pct), "model": model,
                    "resets_at": lim.get("resets_at"),
                    "is_active": bool(lim.get("is_active"))})
    return out


def write_usage_cache(usage, now, path=None):
    """Best-effort: persist {ts, windows} so the statusline renders a per-model
    window without hitting the 429-prone endpoint. Never raises. `path` defaults to
    the module global resolved AT CALL TIME (so tests can patch _USAGE_CACHE_PATH to
    a tmp file — a def-time default would bind the real ~/.claude path and clobber
    the user's live cache during the suite)."""
    path = path or _USAGE_CACHE_PATH
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": int(now), "windows": usage_windows(usage)}, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _human_reset(iso):
    if not iso:
        return "?"
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%d.%m. %H:%M")
    except Exception:
        return str(iso)[:16]


# --------------------------------------------------------------------------- #
# Fable budget gate — `airuleset.py fable-gate`. The 2026-07-03 model-tiering
# policy escalates genuinely HARD judgment work to Fable 5 AUTOMATICALLY — but
# the 2026-07-01 Fable-everywhere incident (limits tripped mid-work, user's work
# stopped) must never repeat, so every automatic escalation is BUDGET-GATED:
# Fable fires only while its weekly window (and the shared weekly) has headroom.
# Reads the same usage cache the watchdog writes every ~15 min (never hits the
# 429-prone endpoint). FAIL-SAFE: missing/stale/empty cache → CLOSED (Opus),
# never a blind Fable burn.
# --------------------------------------------------------------------------- #

FABLE_GATE_PCT = 80            # default: escalate only below 80% used (leaves the
                               # user headroom for their own manual /model Fable)
FABLE_GATE_MAX_AGE = 6 * 3600  # cache older than this = unknown → CLOSED (same
                               # staleness bound the statusline uses)


def fable_gate(now=None, path=None, threshold=None):
    """(open, reason) — may automatic HARD-task escalation dispatch to Fable NOW?

    OPEN  ⇔ the cache is fresh AND every gating window is below `threshold`%:
      - the Fable-scoped weekly window (the binding one under heavy Fable use), and
      - the shared account weekly (Fable burn counts there too).
    The 5h session window deliberately does NOT gate (it resets within hours and
    would keep the gate closed exactly when the user works most; the incident
    being prevented was the WEEKLY trip). A fresh cache with NO Fable-scoped
    window gates on the shared weekly alone; a fresh cache with NO weekly windows
    at all is unknown → CLOSED."""
    now = now if now is not None else time.time()
    if threshold is None:
        try:
            threshold = int(os.environ.get("AIRULESET_FABLE_GATE_PCT", FABLE_GATE_PCT))
        except ValueError:
            threshold = FABLE_GATE_PCT
    path = path or _USAGE_CACHE_PATH
    # The ENTIRE evaluation is fail-safe: any unexpected shape/type in the cache
    # (list top-level, string ts, bool/str percents, garbage windows) must return
    # CLOSED, never raise — a caller unpacking (ok, reason) on a corrupt cache
    # crashing IS a gate failure (review finding F3).
    try:
        with open(path) as f:
            cache = json.load(f)
        # Clock-skew guard (F1): a FUTURE ts makes age negative, which a plain
        # `age > MAX` check calls "fresh" FOREVER — fail-open on frozen numbers.
        # Any age outside [0, MAX] is unknown → stale.
        age = now - float(cache.get("ts") or 0)
        if not (0 <= age <= FABLE_GATE_MAX_AGE):
            return False, "usage cache stale (ts %dh off) — fail-safe CLOSED, use opus" % (
                abs(age) // 3600)
        # Window selection (F2): gate ONLY on WEEKLY windows (a per-model session/
        # surface window must neither gate nor mask), and across MULTIPLE matching
        # windows take the MAX percent — the binding one decides.
        fable_pct = shared_pct = None
        for w in cache.get("windows") or []:
            pct = w.get("percent")
            if isinstance(pct, bool) or not isinstance(pct, (int, float)):
                continue                    # bool/str/None percent = unknown, never 0%
            if w.get("group") != "weekly":
                continue
            model = w.get("model") or ""
            if "fable" in str(model).lower():
                fable_pct = pct if fable_pct is None else max(fable_pct, pct)
            elif not model:
                shared_pct = pct if shared_pct is None else max(shared_pct, pct)
        if fable_pct is None and shared_pct is None:
            return False, "no weekly window in cache — fail-safe CLOSED, use opus"
        parts = []
        for label, pct in (("fable", fable_pct), ("weekly", shared_pct)):
            if pct is None:
                continue
            parts.append("%s=%d%%" % (label, pct))
            if pct >= threshold:
                return False, ("%s window at %d%% (>= %d%% gate) — CLOSED, use opus"
                               % (label, pct, threshold))
        return True, " ".join(parts) + " (< %d%% gate)" % threshold
    except FileNotFoundError:
        return False, "no usage cache (%s) — fail-safe CLOSED, use opus" % path
    except Exception as e:
        return False, "unreadable/corrupt usage cache (%s: %s) — fail-safe CLOSED, use opus" % (
            type(e).__name__, e)


def check_usage(now, state, send_fn, fetch=None, owner=None, dry_run=False,
                threshold=USAGE_THRESHOLD, interval=USAGE_INTERVAL):
    """Rate-limited weekly-usage poll: at most once per `interval`, and an alert
    ONCE per reset window when a weekly limit reaches `threshold`%. Mutates
    state['usage']; returns a log line or ''. Best-effort (never raises)."""
    fetch = fetch or fetch_usage
    u = state.get("usage") or {}
    if (now - u.get("last_check", 0)) < interval:
        return ""
    u["last_check"] = int(now)
    state["usage"] = u
    data = fetch()
    if not data:
        return ""                          # 429 / error → try again next interval
    write_usage_cache(data, now)           # feed the statusline's per-model window
    wk = weekly_percent(data)
    if not wk:
        return ""
    pct, resets_at, label = wk
    if pct < threshold:
        u["alerted_window"] = None         # back below threshold → re-arm the dedup
        state["usage"] = u
        return ""
    if u.get("alerted_window") == resets_at:
        return ""                          # already alerted for THIS reset window
    u["alerted_window"] = resets_at
    state["usage"] = u
    send_fn("⚠️ **Tokeny — %s na %d%%**\n> Práca sa môže čoskoro zastaviť "
            "(vyčerpaný týždenný limit). Reset: %s." % (label, int(pct), _human_reset(resets_at)),
            owner=owner, dedup_key="usage:%s:%d" % (resets_at, int(pct)), dry_run=dry_run)
    return "usage-alert %s %d%%" % (label, int(pct))


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
        for entry in reversed(_iter_jsonl_tail(path, max_lines=120)):
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
    run = run or _default_run
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
                         pending_prefix=PENDING_PREFIX, bg_check=None):
    """Sweep /tmp/claude-discord-pending-* and deliver a ✅ DONE ping the unreliable
    idle_prompt event failed to deliver. Delivers ONLY when the session is genuinely,
    still done: the pending exists AND the session's CURRENT last marker is STILL ✅
    AND it has been idle >= done_grace (user away). A session that re-fired (a
    background task re-invoked it → last marker now ⏳, or it moved on) has its stale
    ✅ CLEARED without pinging — so the device is never told "done" for work that kept
    going (the exact confusion to avoid). PING ONLY; claim-then-send (rm before send)
    so it can't double-fire with the idle hook. Best-effort; returns log lines."""
    import glob as _glob
    owner_by_sid = owner_by_sid or {}
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
            marker = transcript_last_marker(tpath)   # '' for a closed/normal-ended session
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
        project = project_label(cwd) if cwd else "unknown"
        owner = owner_by_sid.get(sid) or account_owner or None
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
# One poll cycle
# --------------------------------------------------------------------------- #

def run_once(now=None, dry_run=False, run=None, send_fn=None,
             projects_dir=PROJECTS_DIR, state_path=STATE_PATH,
             grace=GRACE_SECONDS, interval=RETRY_INTERVAL_SECONDS,
             max_nudges=MAX_NUDGES, wait_grace=WAIT_GRACE_SECONDS,
             wait_clear=WAIT_CLEAR_SECONDS, usage_fetch=None,
             stall_working=STALL_WORKING_SECONDS,
             stall_textcall=STALL_TEXTCALL_SECONDS,
             working_interval=WORKING_RETRY_INTERVAL_SECONDS,
             max_working_nudges=MAX_WORKING_NUDGES,
             done_grace=PENDING_DONE_GRACE, pending_prefix=PENDING_PREFIX,
             discord_fetch=None, bounce_fetch=None, gkreq_fetch=None,
             target_model=None, sleep_fn=None, burn_snapshot_path=None,
             compact_requests_path=None, fleet_fetch=None, fleet_hosts=None,
             fleet_path=None, hooks_settings_path=None, burn_alert_enabled=False,
             goal_rearm_enabled=False):
    """Scan every `claude` pane once. Jobs:
      (1) a session STALLED ON AN API ERROR → auto-resume it (`continue`) + ping;
      (2) a session WAITING ON THE USER (AskUserQuestion / permission dialog) →
          PING ONLY, never act (a design decision needs the human);
      (3) (only when `usage_fetch` is given) a rate-limited WEEKLY-TOKEN-USAGE poll
          → ping when a weekly limit reaches the cap %;
      (4) a session idle on `⏳ WORKING` ≥ `stall_working` with NO advancing subagent
          → NUDGE the pane with a `stuck-check` self-check prompt (its launched work
          may have died silently); retry up to `max_nudges`, escalate-ping on give-up;
      (4a) a session whose last turn emitted a tool call as TEXT (`<invoke name=...>`
          that never ran → turn ended → idle) → NUDGE immediately after a short grace
          (`stall_textcall`), no 30-min wait, regardless of marker;
      (5) a session that ended `✅ DONE` and went idle ≥ `done_grace` → DELIVER the
          pending ✅ device ping the unreliable idle_prompt event failed to send
          (only while the session is STILL ✅ — a re-fired one is cleared silently);
      (6) a session showing the 5-HOUR SESSION-LIMIT banner in its pane → PING ONCE
          with the reset time, then RETRY an auto-resume AFTER the reset clock
          passes — bounded, submitting a stable user draft instead of typing over
          it (never before the reset — `continue` pre-reset just re-hits the limit);
      (7) (only when `discord_fetch` is given) a session's ❓ ping was ANSWERED by
          the owner REPLYING in Discord → type the answer into that exact session's
          idle pane (deliver_discord_replies), react ✅ on success;
      (8) (only when `bounce_fetch` is given) BOUNCE BACKSTOP — open `prio:bounce`
          (gatekeeper-returned) tickets for a repo this box touches → nudge the
          repo's IDLE claude pane (busy pane = the label alone queues them; never
          interrupt mid-work), or ONE deduped Discord ping when no session runs
          (bounce_backstop, ~30 min cadence);
      (11) (only when `gkreq_fetch` is given) GK-REQUEST BACKSTOP (#30) — open
          `needs-gatekeeper` (stream→supervisor action request) tickets → nudge
          the repo's IDLE supervisor pane / deduped Discord ping when no session
          runs; reduced-stream homes never nudged (gk_request_backstop, mirror
          of job 8, ~30 min cadence);
      (9) /GOAL AUTO-ARM — an idle pane asking to paste a printed /goal template
          gets it typed + submitted (goal_autoarm; the user's exact keystrokes,
          never over user text, never when a goal is already armed);
      (12) (only when `target_model` is given) MODEL RECONCILE, restart-based
          (#42) — a live claude/node/bun pane whose newest transcript's last
          model is still fable/opus-4 gets RESTARTED (`/exit` + relaunch
          `claude`, accepting CC's "Resume from summary" dialog when one
          appears) so the managed launcher's `--model target_model` binds it
          by construction — never `/model` (a running session's model list is
          fixed at its own start and can't accept one released later); never
          busy/dialog/draft/in-mode/running-a-background-agent
          (model_reconcile);
      (13) HOURLY BURN SNAPSHOT (#37) — once per hour, append this host's
          $/msgs/avg-context row for the PREVIOUS full hour to
          `burn-history/snapshots.jsonl` (burn_snapshot_job) — the feed
          `airuleset.py burn --compare` reads, so a change's cost impact is
          measured automatically, with nothing for the user to check.
      (14) (only when `compact_requests_path` is given) /COMPACT AT TICKET
          BOUNDARIES (#39 krok 1c) — a session whose Stop hook just recorded
          a completed-ticket report gets `/compact` typed into its pane once
          it goes genuinely idle (never busy/dialog/draft/in-mode;
          compact_ticket_boundary) — a safe compaction point, since the
          ticket's durable state already lives in git/GitHub/the issue.
      (15) COMPACT OVERGROWN IDLE SESSIONS (#39/#43 follow-up) — always on,
          no gating param: a live claude/node/bun pane whose CURRENT
          context (cache_read + cache_creation, grouped by message.id)
          exceeds COMPACT_CONTEXT_THRESHOLD AND has been idle
          >= COMPACT_MIN_IDLE_S gets `/compact` typed in, but only while
          genuinely idle (no draft/dialog/busy/in-mode) and with no
          in-flight background agent (compact_stale_context) — closes the
          gap job 14 leaves for a long-lived session that never reports a
          completed-ticket boundary; deliberately NOT the token-threshold
          `autoCompactWindow` the user had this project actively strip.
      (16) (only when `fleet_fetch` is given) HOURLY FLEET BURN (#55) —
          coordinator-only (cmd_watchdog wires this ONLY on dev1): merges
          every managed box's own hourly burn-snapshot row (job 13's output,
          tailed over ssh via `fleet_fetch`) into ONE combined
          `~/.claude/burn-history/fleet.jsonl` row per hour
          (fleet_burn_job), plus a deduped Discord ping when the observed
          weekly-%/day pace exceeds the budget implied by the usage cache.
      (17) HARD CONTEXT CEILING BACKSTOP (#69) — always on, no gating param:
          a live claude/node/bun pane whose CURRENT context exceeds
          `COMPACT_HARD_CEILING` gets `/compact` typed in REGARDLESS of idle
          duration and even into a BUSY pane (a short send-keys reliably
          queues — #65) — the backstop for a session that neither reports a
          ticket boundary (job 14) NOR ever goes idle 20 minutes (job 15),
          e.g. a continuous review/merge loop or a governance session that
          ends every turn `⏳ WORKING` (compact_hard_ceiling).
      (18) (only when `hooks_settings_path` is given) HOOKS RECONCILE,
          restart-based (#70) — Claude Code snapshots its hook set once at
          process START and never re-reads it, so a hook deployed into
          settings.json while a session is already running has zero effect
          until that session restarts. A live claude/node/bun pane whose
          settings.json hooks block content hash no longer matches the hash
          this job first saw that session running under gets RESTARTED via
          job 12's exact `_restart_pane` machinery — never busy/dialog/
          draft/in-mode/running-a-background-agent (hooks_reconcile).
          Coalesces with job 12 via a shared `handled` set so a model change
          and a hooks change landing in the same sweep produce ONE restart,
          not two. Same "wired = on" convention as jobs 13/14/16 (a path
          param gates it — never a bare env/global default) so an existing
          caller of run_once() that passes nothing sees NO behavior change
          and never has its own test state polluted by this box's real
          settings.json.
      (19) (only when `burn_alert_enabled` is truthy) HOURLY BURN ALERT
          (#81) — runs right after job 16; reads the LATEST merged
          `fleet.jsonl` row and, at most once per hour bucket, checks it
          against three thresholds (absolute $, a multiple of the median
          of the last N hours, crossing a whole step of the weekly usage
          window) via `burn.hourly_burn_alert` — any one firing sends ONE
          combined Discord ping (burn_alert_job). Coordinator-only, same
          "wired = on" convention as job 16 (cmd_watchdog computes the
          dev1-only gate; this module stays host-agnostic).
      (20) (only when `goal_rearm_enabled` is truthy) GOAL RE-ARM BACKSTOP
          (#76) — an armed `/goal` dies SILENTLY (no restart needed, no
          `Goal cleared:` marker written), so every transcript-based
          detector keeps reading "armed" while CC runs no loop at all. This
          job cross-checks the TWO independent sources — the transcript
          marker (INTENT, `scan_goal_markers`) against CC's own `◎ /goal`
          footer indicator (REALITY, `pane_goal_armed`) — and re-arms a
          proven mismatch with the marker's EXACT bytes, verified
          keystroke by keystroke into a free prompt (goal_rearm). Bounded
          (a self-resolving goal must not spin a turn per sweep) with ONE
          Discord ping on give-up; a `Goal cleared:` newer than the last
          `Goal set:` is a deliberate shutdown and is never touched. Runs
          LAST so jobs 14/15/17 get first crack at the same pane, and
          skips any sid they compacted this sweep (`handled`) or that
          holds an outstanding shared claim (#78).
    Returns a list of human-readable action log lines (for --verbose / tests)."""
    now = time.time() if now is None else now
    run = run or _default_run
    from notify import compose_api_error_alert
    if send_fn is None:
        from notify import send as send_fn

    state = load_state(state_path)
    logs = []
    stalled = set()
    owner_by_sid = {}                   # session id -> tmux owner, for job 5's ✅ @mention
    panes_by_sid = {}                   # session id -> (pane_id, captured), for job 7 reply routing
    account_owner = ""                  # owner to @mention on the account-wide usage alert

    # Resolve every `claude` pane to its transcript, grouped BY transcript. A nudge
    # is bound to a transcript that exactly ONE pane owns — if two panes resolve to
    # the same transcript (two `claude` terminals in one cwd, or two distinct cwds
    # that collide under CC's '/'/'.'/'_'→'-' dir encoding) we cannot tell which
    # pane is the stalled one, so we SKIP rather than fire `continue` into the
    # wrong (possibly healthy) pane. Mis-targeted keystroke injection is worse than
    # a missed auto-resume (the user still gets pinged on the stall via the flag).
    by_transcript = {}
    hosted_panes = []                   # sudo-hosted stream panes (foreign HOME)
    for pid, cwd in list_claude_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
            fu = _foreign_user(cwd)
            if fu:
                hosted_panes.append((pid, cwd, fu))
            continue
        tpath, tmtime = tinfo
        by_transcript.setdefault(str(tpath), []).append((pid, cwd, tmtime, tpath))

    for tkey, owners in by_transcript.items():
        try:
            if len(owners) > 1:
                logs.append("skip ambiguous (%d panes → %s)" % (len(owners), Path(tkey).stem))
                continue
            pid, cwd, tmtime, tpath = owners[0]
            idle = now - tmtime
            project = project_label(cwd)
            key = tpath.stem                   # session id (stable across grouped panes)
            owner = pane_owner(pid, run)       # @mention the right person for THIS pane
            if owner:
                owner_by_sid[key] = owner      # so job 5's ✅ ping @mentions this session's owner
                if not account_owner:
                    account_owner = owner      # first owner seen → the account/usage owner

            # Capture the pane ONCE per session (reused by job 6 + the job-2 waiting
            # check + job 7 reply routing).
            captured = capture_pane(pid, run)
            panes_by_sid[key] = (pid, captured)   # job 7: route a Discord reply here

            # --- (10) QUEUED-PROMPT-WEDGE (#20): frozen input-box text + stale
            # transcript → ONE deduped owner ping (ping-first, never auto-Enter).
            # waiting: the LOCAL transcript is directly readable here, so the
            # gate is computed precisely (issue #35) — a parked draft only
            # pings while the session actually asks ❓ NEEDS YOU.
            logs += prompt_wedge_check(now, state, pid, captured, tmtime,
                                       owner, project, send_fn,
                                       dry_run=dry_run, run=run,
                                       waiting=_session_is_waiting(tpath))

            # --- (6) 5-HOUR SESSION LIMIT → ping once, then RETRY a resume AFTER --
            # the reset, bounded, with a stable-draft submit path -------------------
            # A TIME-BASED cap: `continue` BEFORE the reset just re-hits it (the
            # user's incident), so we ping ONCE with the reset time, do NOTHING
            # until the reset clock, then attempt an auto-resume AFTER it — never
            # before. Read from the PANE (the banner is on screen, not reliably a
            # transcript api-error). While a session is limited job 6 owns it
            # (skips the api-error / nudge paths).
            #
            # A single one-shot attempt was NOT enough (gk incident 2026-07-24):
            # the first post-reset poll can land mid-race — still busy, or with
            # the user's OWN hand-typed draft sitting unsubmitted in the input box
            # (it was typed INTO a limit-parked session that could go nowhere) —
            # and `pane_at_idle_prompt`'s bare-❯ gate never matches a box holding
            # text. A one-shot attempt then deadlocks forever with no retry and no
            # further ping. So job 6 now RETRIES every SESSLIMIT_RETRY_S up to
            # SESSLIMIT_MAX_TRIES, pings ONCE if it gives up, and — when a draft is
            # present and STABLE across sweeps — submits THAT draft instead of
            # typing "continue" over it. A bounced attempt (the limit somehow still
            # active) just retries; a REAL resume makes the banner leave the bottom
            # region so `pane_session_limited` goes False and this branch stops
            # firing on its own (FIX B, above).
            if pane_session_limited(captured):
                skey = "sesslimit:" + key
                s = state.get(skey)
                if s is None:
                    # Parse the reset clock ONCE at first detection and keep it stable for
                    # the whole episode — re-parsing after the reset would roll the same
                    # "6:10pm" forward to tomorrow and wrongly re-ping instead of resuming.
                    s = {"resets_at": parse_reset_epoch(captured, now),
                         "pinged": False, "continued": False, "first_seen": int(now),
                         "attempts": 0, "gave_up": False}
                    state[skey] = s
                elif s.get("resets_at") is None:
                    # an earlier poll couldn't read the clock — try again to refine it.
                    s["resets_at"] = parse_reset_epoch(captured, now)
                s["last_seen"] = int(now)
                ra = s.get("resets_at")
                if not s.get("pinged"):
                    s["pinged"] = True
                    when = _human_clock(ra) if ra else "čoskoro"
                    logs.append("session-limit %s — ping (reset %s)" % (project, when))
                    send_fn("⏳ **%s** — dosiahnutý 5-hodinový limit\n> Reset o %s. Po "
                            "resete pošlem `continue` automaticky — nič nemusíš robiť."
                            % (project, when),
                            owner=owner, dedup_key="sesslimit:%s:%s" % (key, ra or s["first_seen"]),
                            dry_run=dry_run)
                elif ra and now >= ra:
                    attempts = s.get("attempts", 0)
                    if attempts >= SESSLIMIT_MAX_TRIES:
                        # Bounded — never retry forever. One give-up ping, then silence.
                        if not s.get("gave_up"):
                            s["gave_up"] = True
                            logs.append("session-limit %s — gave up after %d attempts"
                                        % (project, SESSLIMIT_MAX_TRIES))
                            send_fn("❌ **%s** — limit sa mal resetnúť, ale session sa "
                                    "nepodarilo obnoviť ani po %d pokusoch — obnov ju "
                                    "prosím ručne." % (project, SESSLIMIT_MAX_TRIES),
                                    owner=owner,
                                    dedup_key="sesslimit-giveup:%s:%s" % (key, ra),
                                    dry_run=dry_run)
                        else:
                            logs.append("skip gave-up (session-limit resume) %s" % (project or pid))
                    elif s.get("last_try") is not None and (now - s["last_try"]) < SESSLIMIT_RETRY_S:
                        logs.append("skip retry-wait (session-limit resume) %s" % (project or pid))
                    elif pane_in_mode(pid, run):        # never type into a scrolled pane
                        logs.append("skip in-mode (session-limit resume) %s" % (project or pid))
                    else:
                        draft = _input_line_text(captured)
                        if draft:
                            # A draft sitting in the input box of a limit-parked session
                            # could go nowhere while limited — it is the user's OWN
                            # intended prompt, typed here, not stray input. Never type
                            # over it blind. Track its hash first; only once it is
                            # BYTE-STABLE across at least one more sweep do we submit it
                            # (Escape first — CC's agent-strip selector can hold focus
                            # and swallow a bare Enter, observed live — then Enter).
                            # Leaving a stable draft untouched forever is the exact
                            # deadlock this incident hit. This path is deliberately
                            # NARROW to the post-reset resume race; job 10's ping-first
                            # rule for an ORDINARY wedged draft elsewhere is untouched.
                            draft_hash = hashlib.sha1(draft.encode()).hexdigest()[:12]
                            if s.get("draft_hash") == draft_hash:
                                if not dry_run:
                                    run(["tmux", "send-keys", "-t", pid, "Escape"])
                                    run(["tmux", "send-keys", "-t", pid, "Enter"])
                                attempts += 1
                                s["attempts"] = attempts
                                s["last_try"] = now
                                s["continued"] = True
                                logs.append("session-limit %s — reset passed → submit "
                                            "user draft" % project)
                                if attempts == 1:
                                    send_fn("✅ **%s** — 5h limit sa resetol, odosielam "
                                            "tvoj rozpísaný prompt — pokračujem." % project,
                                            owner=owner,
                                            dedup_key="sesslimit-resume:%s:%s" % (key, ra),
                                            dry_run=dry_run)
                            else:
                                s["draft_hash"] = draft_hash
                                logs.append("session-limit %s — draft tracked" % project)
                        elif not pane_at_idle_prompt(captured):
                            # Race guard: the user may have manually resumed inside the
                            # window and the session is now running a FOREGROUND agent
                            # while the "session limit" banner is still within the
                            # captured pane. Typing `continue` there would INTERRUPT the
                            # live work (the #233 harm class). Skip WITHOUT burning an
                            # attempt — a later poll retries every sweep (no rate limit
                            # on a busy-pane skip), and if it already resumed the banner
                            # leaves the bottom region and job 6 exits on its own.
                            logs.append("skip busy-pane (session-limit resume) %s" % (project or pid))
                        else:
                            if not dry_run:
                                send_continue(pid, NUDGE_TEXT, run)
                            attempts += 1
                            s["attempts"] = attempts
                            s["last_try"] = now
                            s["continued"] = True
                            logs.append("session-limit %s — reset passed → continue" % project)
                            if attempts == 1:
                                send_fn("✅ **%s** — 5h limit sa resetol, poslal som "
                                        "`continue` — pokračujem." % project,
                                        owner=owner,
                                        dedup_key="sesslimit-resume:%s:%s" % (key, ra),
                                        dry_run=dry_run)
                continue                                # job 6 owns this session this poll

            # --- (1) STALLED ON AN API ERROR → auto-resume (ACTS: injects `continue`) -
            # ERROR signal = Claude Code's OWN `isApiErrorMessage` flag on the last
            # assistant entry — the ONLY trigger (an earlier pane-text fallback false-
            # nudged a meta-conversation merely DISPLAYING api-error text). The grace is
            # tracked from when the last reply BECAME an error (in state, via decide),
            # NOT from transcript mtime: CC's own retries + queue/snapshot writes keep
            # touching the transcript, so an mtime-idle gate never trips for a rate-
            # limited session (that bug left `presenter` unnudged).
            err_text = transcript_last_error(tpath)
            if err_text:
                # user scrolling / a menu open → keys would be swallowed or corrupt the
                # selection. Skip WITHOUT advancing state (no retry burned).
                if pane_in_mode(pid, run):
                    logs.append("skip in-mode %s" % (project or pid))
                    continue
                # BUSY-PANE guard (uniform with jobs 4/4a/6): the api-error flag on the
                # last transcript entry means CC ABORTED that turn → the pane is normally
                # idle at a free `❯`. But if the user MANUALLY resumed within the idle
                # window, a foreground turn/agent is now running (spinner, no free `❯`) and
                # its first entry hasn't landed yet — typing `continue` would INTERRUPT it
                # (the #233 scar). Never inject unless the pane shows a free prompt; skip
                # WITHOUT burning a retry (the next poll re-checks).
                if not pane_at_idle_prompt(captured):
                    logs.append("skip busy-pane (api-error) %s" % (project or pid))
                    continue
                stalled.add(key)
                err_hash = _hash(err_text)
                # seed first_seen with now-idle so an already-stale stall counts from
                # when it really began (idle = age of the last transcript write).
                action, entry = decide(state, key, err_hash, now, grace, interval,
                                       max_nudges, first_seen_seed=now - idle)
                state[key] = entry
                # first_seen in the dedup key so a recover→re-stall still pings
                # (notify's own dedup TTL is 14 days).
                fs = int(entry.get("first_seen", now))
                if action == "nudge" and is_usage_cap(err_text):
                    # quota USAGE cap — time-based, `continue` can't fix it. Ping ONCE,
                    # mark escalated (no nudge, no retries, no false giveup).
                    entry["nudges"], entry["escalated"] = [], True
                    state[key] = entry
                    logs.append("usage-cap %s — ping only, no continue" % project)
                    send_fn(compose_api_error_alert(project, err_text)
                            + "\n> (usage cap — `continue` nepomôže; CC sa obnoví po resete)",
                            owner=owner, dedup_key="apierr:%s:%s:%s" % (key, err_hash, fs), dry_run=dry_run)
                elif action == "nudge":
                    n = len(entry["nudges"])
                    logs.append("nudge#%d %s [%s]" % (n, project, key))
                    if not dry_run:
                        send_continue(pid, NUDGE_TEXT, run)
                    if n == 1:                 # first nudge → tell the user it stalled
                        send_fn(compose_api_error_alert(project, err_text),
                                owner=owner, dedup_key="apierr:%s:%s:%s" % (key, err_hash, fs), dry_run=dry_run)
                elif action == "escalate":
                    logs.append("escalate %s [%s] — gave up after %d nudges" % (project, key, max_nudges))
                    body = ("\U0001f6d1 **%s** — API chyba pretrváva\n> Po %d× `continue` sa to "
                            "stále nepohlo — treba zásah." % (project, max_nudges))
                    send_fn(body, owner=owner, dedup_key="apierr-giveup:%s:%s:%s" % (key, err_hash, fs),
                            dry_run=dry_run)
                else:
                    logs.append("%s %s [%s]" % (action, project, key))
                continue                       # handled as an api-error stall

            # --- (1b) SUBAGENT API-ERROR → nudge the supervisor, naming the worker ----
            # (issue #6) Job 1 above only reads the SUPERVISOR transcript. A dispatched
            # BACKGROUND WORKER that stalls on an api-error writes it to
            # subagents/<id>.jsonl — invisible to job 1, caught only indirectly (up to
            # STALL_WORKING_SECONDS later) by job 4's mtime-based subagent_active()
            # gate. Reached only when the SUPERVISOR itself has no error (err_text was
            # falsy above). Apply the SAME detector to the newest subagent transcript;
            # on a hit (past `grace`, mirroring job 1's own grace before its first
            # nudge — CC's own retries may still recover it), nudge/ping via the shared
            # busy/idle helper. Purely additive: no existing keystroke gate is touched.
            # Gated on the SUPERVISOR's own last marker being `⏳ WORKING` AND the
            # subagent file's age being under SUBAGENT_MAX_AGE_SECONDS — without
            # both, a HISTORICAL worker file (the supervisor long done, or the file
            # simply ancient) would nudge/escalate forever (adversarial-review
            # finding). `continue`s after handling — one keystroke injection per
            # pane per poll, the same discipline every other job keeps; without it,
            # job 4 below could fall through in the SAME poll and inject a SECOND
            # keystroke into this pane, gated on the now-stale pre-injection capture.
            sub_path = newest_subagent_transcript(tpath)
            if (sub_path is not None
                    and transcript_last_marker(tpath) == "⏳"):
                sub_err = transcript_last_error(sub_path)
                if sub_err:
                    try:
                        sub_idle = now - sub_path.stat().st_mtime
                    except OSError:
                        sub_idle = 0
                    if grace <= sub_idle <= SUBAGENT_MAX_AGE_SECONDS:
                        _nudge_dying_subagent(state, logs, send_fn, pid, run, captured,
                                              project, owner, now, sub_path, sub_idle,
                                              "api-error", "apierr", interval, max_nudges,
                                              dry_run)
                        continue

            # --- (2) WAITING ON THE USER (AskUserQuestion / permission) → PING ONLY ---
            # Blocked on an interactive prompt the human must answer. NEVER send keys
            # (a design decision needs the user), so the loose pane-text match is safe.
            # Dedup is by the FOOTER EPISODE, NOT per-poll idle: the episode lives while
            # the prompt footer keeps appearing, and ends only after WAIT_CLEAR seconds
            # without it. So a multi-question dialog / re-ask loop that jitters the
            # transcript (idle dipping, a momentary capture miss) does NOT re-ping the
            # SAME open prompt — `pinged` stays set for the whole episode.
            if pane_waiting_on_user(captured):
                wkey = "wait:" + key
                w = state.get(wkey)
                if w is None:
                    # FIRST sight of this footer — record, do NOT ping yet. A transient
                    # flash (a bypass-permissions prompt that auto-approves, an
                    # AskUserQuestion that auto-continues after ~60s, a one-capture
                    # lingering footer) is GONE by the next poll and never pings. Only a
                    # footer that PERSISTS to a later poll (a genuinely unanswered wait)
                    # pings — the persistence half of the false-"čaká na teba" fix (the
                    # other half is the bottom-`❯` guard in pane_waiting_on_user).
                    w = {"first_seen": int(now - idle), "pinged": False, "confirmed": False}
                    state[wkey] = w
                w.setdefault("pinged", False)
                w.setdefault("confirmed", False)
                w["last_seen"] = int(now)
                if (not w["pinged"] and w["confirmed"]
                        and (now - w["first_seen"]) >= wait_grace):
                    w["pinged"] = True
                    logs.append("waiting %s [%s]" % (project, key))
                    # Carry the ACTUAL question (+ options) from the pane — a ping that
                    # says only "a question is waiting" forces the user to the terminal
                    # to even learn what is asked (their explicit complaint, 2026-07-04).
                    excerpt = pane_question_excerpt(captured)
                    detail = excerpt or ("Session sa zastavila na otázke "
                                         "(AskUserQuestion) — pozri sa naň.")
                    send_fn("❓ **%s** — čaká na teba\n> %s" % (project, detail),
                            owner=owner, dedup_key="waiting:%s:%s" % (key, w["first_seen"]),
                            dry_run=dry_run)
                w["confirmed"] = True          # seen this poll → a LATER poll may ping
                continue                       # waiting on the user → not a working-stall

            # --- (4a) TEXT-EMITTED TOOL-CALL STALL → nudge immediately (no 30-min wait) --
            # The model emitted a tool call as TEXT (a `<invoke name=...>` block in an
            # assistant text block) instead of a structured tool_use → it never ran, the
            # turn ENDED, and the session sits idle (often on a now-stale `⏳`, or no marker
            # at all). Detectable instantly from the transcript SHAPE (see
            # transcript_text_toolcall_stall — precise, so a meta-conversation merely
            # DISCUSSING `<invoke>` does not match), so unlike job 4 this fires after only a
            # short grace (guarding a mid-write turn), REGARDLESS of marker. Reuses job 4's
            # nudge lifecycle (decide_working: nudge → retry → escalate) under a distinct
            # `textcall:` key. Same copy-mode / advancing-subagent skips as job 4.
            if (idle >= stall_textcall
                    and transcript_text_toolcall_stall(tpath)
                    and not subagent_active(tpath, now, stall_textcall)):
                if pane_in_mode(pid, run):
                    logs.append("skip in-mode (textcall-stall) %s" % (project or pid))
                    continue
                # NEVER type into a pane that is NOT at a free `❯` idle prompt — a running
                # foreground agent / tool blocks the parent transcript (looks idle) while
                # the pane shows live work; a keystroke would INTERRUPT it (the #233 incident).
                if not pane_at_idle_prompt(captured):
                    logs.append("skip busy-pane (textcall-stall) %s" % (project or pid))
                    continue
                wkey = "textcall:" + key
                action, entry = decide_working(state, wkey, now, idle,
                                               interval=working_interval,
                                               max_nudges=max_working_nudges)
                state[wkey] = entry
                fs = int(entry.get("first_seen", now))
                if action == "nudge":
                    n = len(entry["nudges"])
                    logs.append("textcall-nudge#%d %s [%s] idle=%dm"
                                % (n, project, key, int(idle // 60)))
                    if not dry_run:
                        send_continue(pid, TEXTCALL_NUDGE_TEXT, run)
                elif action == "escalate":
                    logs.append("textcall-escalate %s [%s] — wedged after %d nudges"
                                % (project, key, max_working_nudges))
                    send_fn("\U0001f6d1 **%s** — turn sa zlomil (tool-call vypísaný ako "
                            "text) a nereaguje\n> Po %d× automatickom stuck-check pingu sa "
                            "session stále nepohla — pravdepodobne zamrzol samotný Claude "
                            "proces. Treba zásah." % (project, max_working_nudges),
                            owner=owner, dedup_key="textcall-giveup:%s:%s" % (key, fs),
                            dry_run=dry_run)
                else:
                    logs.append("textcall-%s %s [%s]" % (action, project, key))
                continue                        # handled as a text-toolcall stall

            # --- (4a-sub) SUBAGENT TEXT-TOOLCALL STALL → nudge the supervisor ----------
            # (issue #6) Job 4a above only reads the SUPERVISOR transcript — and worse,
            # its own `not subagent_active(...)` gate SKIPS the supervisor check
            # whenever the subagent transcript is FRESH, which is exactly the case right
            # after a worker's stalled turn writes its last (broken) entry. Reached only
            # when job 4a's own block above did not already fire/continue. Apply the
            # SAME detector to the newest subagent transcript directly; on a hit (past
            # `stall_textcall`, mirroring job 4a's own short grace against a mid-write
            # turn), nudge/ping via the shared busy/idle helper. Purely additive.
            # Same two gates as job 1b (SUPERVISOR marker == `⏳`, subagent file age
            # under SUBAGENT_MAX_AGE_SECONDS) and the same `continue` afterward — one
            # keystroke injection per pane per poll (see job 1b's comment for the
            # double-injection failure this prevents: job 4 below could otherwise
            # fall through in the SAME poll and inject a second keystroke).
            sub_path = newest_subagent_transcript(tpath)
            if (sub_path is not None
                    and transcript_last_marker(tpath) == "⏳"
                    and transcript_text_toolcall_stall(sub_path)):
                try:
                    sub_idle = now - sub_path.stat().st_mtime
                except OSError:
                    sub_idle = 0
                if stall_textcall <= sub_idle <= SUBAGENT_MAX_AGE_SECONDS:
                    _nudge_dying_subagent(state, logs, send_fn, pid, run, captured,
                                          project, owner, now, sub_path, sub_idle,
                                          "text-toolcall-stall", "textcall",
                                          working_interval, max_working_nudges, dry_run)
                    continue

            # --- (4) ⏳ WORKING, long-idle, NO live subagent → NUDGE the session ---------
            # Claude ended the turn `⏳ WORKING` (a background job / subagent is running,
            # it'll report when done) but nothing has happened for `stall_working` AND no
            # subagent transcript is advancing → the launched work MIGHT have died silently.
            # We send the autonomous form of the user's manual "stucked?": a `stuck-check`
            # self-check nudge telling the session to verify the liveness of its launched
            # work and intervene if dead. Safe where a blind `continue` was the user's scar
            # (see the STALL_WORKING / WORKING_NUDGE_TEXT block): the nudge is a QUESTION
            # that delegates the healthy-vs-dead call to the session (which has eyes); a
            # landed nudge resets idle so the episode self-resolves in ONE nudge with no
            # Discord noise; only a wedged session (no response across `max_working_nudges`
            # retries) escalates to ONE ping. Gates: advancing-subagent (skips the common
            # healthy long wait), high threshold (skips CI ≤25 min / mutation ≤20 min),
            # copy-mode skip (never type into a scrolled pane).
            mk4, mk4_line = transcript_last_marker_line(tpath)
            if (mk4 == "⏳" and idle >= stall_working
                    and not subagent_active(tpath, now, stall_working)):
                # DECLARED WAIT — the marker names a future clock time ("čakám
                # na 14:15 auto-sync", "deploy okno 22:00"): a scheduled
                # external event, not a stall. No nudge until it passes
                # (+grace) — the codex-bridge drilling incident, 2026-07-20.
                dwu = declared_wait_until(mk4_line, now)
                if dwu > now:
                    logs.append("declared-wait %s [%s] +%dm — no nudge"
                                % (project, key, int((dwu - now) // 60)))
                    continue
                # user scrolling / a menu open → keys would be swallowed or corrupt the
                # selection. Skip WITHOUT advancing state (no retry burned) — same gate as
                # job 1's api-error nudge. (Adversarial-review finding #3: we deliberately do
                # NOT add a "pane input buffer non-empty" guard. tmux cannot tell typed text
                # from the CC input PLACEHOLDER, so such a guard would false-positive and
                # SUPPRESS the overnight nudge — the exact failure the user is angry about.
                # The residual — a user typing into a 30-min-stale ⏳ pane in the same 60s
                # window gets one interleaved, recoverable, visible buffer line while PRESENT
                # — matches job 1's accepted residual and is not the forced-resume scar.)
                if pane_in_mode(pid, run):
                    logs.append("skip in-mode (working-stall) %s" % (project or pid))
                    continue
                # NEVER type into a pane that is NOT at a free `❯` idle prompt. A FOREGROUND
                # subagent (a ticket-validator, a Task/Agent dispatch) BLOCKS the parent, so
                # its transcript freezes and looks 30-min-idle while the session is ALIVE and
                # the pane shows the agent running — a nudge keystroke there INTERRUPTS the
                # live work (the observed "Agent Validate issue #233 · Interrupted" incident).
                # subagent_active covers the BACKGROUND case (main idle at `❯`); this covers
                # the FOREGROUND case (no free `❯`). We are ALREADY past `not subagent_active`,
                # so nothing is advancing — a pane that stays busy with NO progress for a LONG
                # time is a genuinely wedged / hung foreground turn (the 8-hour silent-loss
                # class). We can't type (that would interrupt), but a PING never interrupts, so
                # escalate to ONE ping at a LONGER threshold (2× stall_working, so a merely
                # long-THINKING foreground agent that just isn't writing its transcript isn't
                # pinged). One ping per episode; last_seen refreshed so cleanup can't drop it
                # mid-episode. NEVER a keystroke.
                if not pane_at_idle_prompt(captured):
                    bkey = "busypane:" + key
                    b = state.get(bkey) or {"first_seen": int(now - idle), "pinged": False}
                    b["last_seen"] = int(now)
                    state[bkey] = b
                    if not b["pinged"] and idle >= 2 * stall_working:
                        b["pinged"] = True
                        logs.append("busy-pane-wedged %s [%s] idle=%dm — ping only (never type)"
                                    % (project, key, int(idle // 60)))
                        send_fn("\U0001f6d1 **%s** — visí na ⏳ WORKING, beží agent ktorý sa "
                                "dlho nepohol (%d min)\n> Vyzerá zaseknuto. Nezasahujem "
                                "klávesami do bežiaceho agenta (rozbilo by to jeho prácu) — "
                                "over ho prosím." % (project, int(idle // 60)),
                                owner=owner, dedup_key="busypane:%s:%s" % (key, b["first_seen"]),
                                dry_run=dry_run)
                    else:
                        logs.append("skip busy-pane (working-stall) %s" % (project or pid))
                    continue
                wkey = "working:" + key
                # responded = the transcript advanced AFTER the last nudge (the
                # session answered the check and is merely waiting again) —
                # answered checks back off exponentially and never escalate.
                _prev_n = (state.get(wkey) or {}).get("nudges") or []
                responded = bool(_prev_n) and (now - idle) > (_prev_n[-1] + 30)
                action, entry = decide_working(state, wkey, now, idle,
                                               interval=working_interval,
                                               max_nudges=max_working_nudges,
                                               responded=responded)
                state[wkey] = entry
                fs = int(entry.get("first_seen", now))
                if action == "nudge":
                    n = len(entry["nudges"])
                    logs.append("working-nudge#%d %s [%s] idle=%dm"
                                % (n, project, key, int(idle // 60)))
                    if not dry_run:
                        send_selfcheck(pid, run)
                elif action == "escalate":
                    logs.append("working-escalate %s [%s] — wedged after %d nudges"
                                % (project, key, max_working_nudges))
                    send_fn("\U0001f6d1 **%s** — visí na ⏳ WORKING a nereaguje\n> Po %d× "
                            "automatickom stuck-check pingu sa session stále nepohla — "
                            "pravdepodobne zamrzol samotný Claude proces. Treba zásah."
                            % (project, max_working_nudges),
                            owner=owner, dedup_key="workingstall-giveup:%s:%s" % (key, fs),
                            dry_run=dry_run)
                else:
                    logs.append("working-%s %s [%s]" % (action, project, key))

        except Exception as e:
            # one bad pane (corrupted transcript, unexpected tmux-shim
            # output shape, a raise inside a job handler) must never
            # abort the whole poll and blank state for every OTHER
            # healthy pane this cycle — isolate it and move on. A
            # TRANSIENT capture/read error here must NOT be conflated with
            # "the session recovered": if job 1 (api-error) had this
            # session mid-episode, its bare-key state entry must survive
            # this failed poll (add it to `stalled`) so the cleanup pass
            # below does not delete it — a later successful poll then
            # continues the SAME nudge/escalation episode instead of
            # silently resetting to nudge#1. `tkey` (the loop variable
            # itself) is always available here regardless of where inside
            # the try block the exception fired, unlike the local `key`.
            err_key = Path(tkey).stem
            stalled.add(err_key)
            logs.append("skip error %s: %r" % (err_key, e))
            continue

    # Sudo-hosted stream panes (montalu claude in THIS tmux): the transcript
    # lives under the FOREIGN home, so the pane fell out of the loop above —
    # yet it is reachable ONLY from here (the hosted user's own watchdog has
    # no tmux server at all; 2026-07-21: the montalu Discord answer starved
    # invisible to both sides). Bind each to its foreign session id so job 7
    # delivers replies into it and job 10 catches wedged prompts.
    hosted_users = {}                   # session id -> foreign unix user
    for pid, cwd, fu in hosted_panes:
        try:
            info = _foreign_session_info(fu, cwd)
            if not info:
                continue
            sid, f_mtime = info
            if sid in panes_by_sid:
                continue
            captured = capture_pane(pid, run)
            panes_by_sid[sid] = (pid, captured)
            hosted_users[sid] = fu
            owner = pane_owner(pid, run)
            if owner:
                owner_by_sid.setdefault(sid, owner)
            # waiting=True: the FOREIGN transcript isn't cheaply readable from
            # here (a different HOME) — stay eligible-by-default rather than
            # silently going quiet for every hosted pane (issue #35).
            logs += prompt_wedge_check(now, state, pid, captured, f_mtime,
                                       owner, project_label(cwd) + "-" + fu,
                                       send_fn, dry_run=dry_run, run=run,
                                       waiting=True)
        except Exception as e:
            logs.append("skip hosted pane %s: %r" % (pid, e))

    # Cleanup. api-error keys (no prefix): drop the moment the session recovers.
    # wait: keys: drop only after the footer has been absent for WAIT_CLEAR seconds
    # (the episode is genuinely over / the prompt was answered) — tolerating a
    # single missed poll so the same open prompt is never pinged twice.
    for k in list(state.keys()):
        if k == "usage":
            continue                       # account-wide usage state, not a session
        if (k.startswith("wait:") or k.startswith("working:") or k.startswith("textcall:")
                or k.startswith("sesslimit:") or k.startswith("busypane:")
                or k.startswith("subagent-apierr:") or k.startswith("subagent-textcall:")
                or k.startswith("subagent-busypane:")):
            # episode keys (job 2 waiting / job 4 working-stall): drop only after the
            # condition has been ABSENT for wait_clear seconds (the prompt was
            # answered / the session moved on), so the SAME episode pings/nudges exactly
            # once and a transient miss doesn't re-arm it. (Adversarial-review finding
            # #1: dropping a `working:` key resets its nudge counter, but this is BENIGN
            # by design — a job-4 nudge that LANDS resets idle below the 30-min
            # threshold, and re-triggering then needs a GENUINELY NEW 30-min silence,
            # which correctly deserves a fresh nudge#1, not a resumed escalation. The
            # only escalation-continuity path — a wedged process — keeps idle growing so
            # the condition stays continuously true, last_seen advances every poll, and
            # this branch never fires before all 3 nudges + the give-up ping land.)
            if int(now) - state[k].get("last_seen", 0) > wait_clear:
                del state[k]
        elif _SESSION_KEY_RX.fullmatch(k) and k not in stalled:
            # bare UUID = an api-error episode key; drop it the moment the
            # session recovered. Anything else bare is a NAMED job store
            # (dreply_*, inputdead, goalarm, …) — NEVER cleanup's to delete
            # (doing so starved the ticket-fallback, 2026-07-21).
            del state[k]

    # --- (3) WEEKLY TOKEN-USAGE alert (only when a fetcher is wired) — rate-limited
    # to USAGE_INTERVAL inside check_usage so the 60s tmux cadence doesn't hammer
    # the aggressively-429'd endpoint. Best-effort: never breaks the tmux jobs.
    if usage_fetch is not None:
        try:
            line = check_usage(now, state, send_fn, fetch=usage_fetch,
                               owner=account_owner or None, dry_run=dry_run)
            if line:
                logs.append(line)
        except Exception:
            pass

    # --- (5) DELIVER PENDING ✅ — backstop for the unreliable idle_prompt event.
    # Best-effort: a bad pending file must never break the tmux jobs.
    try:
        logs += deliver_pending_done(now, send_fn, projects_dir,
                                     owner_by_sid=owner_by_sid, account_owner=account_owner,
                                     dry_run=dry_run, done_grace=done_grace,
                                     pending_prefix=pending_prefix)
    except Exception:
        pass

    # --- (7) ROUTE DISCORD REPLIES → the asking session (only when a fetcher is
    # wired). Best-effort: a Discord/network hiccup must never break the tmux jobs.
    if discord_fetch is not None:
        try:
            logs += deliver_discord_replies(now, run, state, panes_by_sid,
                                            dry_run=dry_run, discord_fetch=discord_fetch,
                                            hosted_users=hosted_users)
        except Exception as e:
            logs.append("discord-reply error: %r" % (e,))

    # Terminal-answered ❓ cleanup — a question answered by a HUMAN prompt in
    # the asking session leaves the map NOW, not at the 24h TTL (it feeds the
    # statusline 'otazky' badge, which must be trustworthy per stream).
    try:
        logs += prune_answered_questions(now, projects_dir=projects_dir,
                                         dry_run=dry_run)
    except Exception as e:
        logs.append("question-prune error: %r" % (e,))

    # Job 8 — bounce backstop (gatekeeper-returned prio:bounce tickets must
    # never rot after a loop ends). Only when a fetch is wired (cmd_watchdog
    # passes the real one; unit tests of other jobs stay network-free).
    # Cadence-gated internally; best-effort.
    if bounce_fetch is not None:
        try:
            logs += bounce_backstop(
                now, run, state, send_fn, dry_run=dry_run,
                gh_fetch=bounce_fetch, projects_dir=projects_dir,
                persist=lambda: save_state(state_path, state))
        except Exception as e:
            logs.append("bounce-backstop error: %r" % (e,))

    # Job 11 — gk-request backstop (#30): the stream→supervisor mirror of
    # job 8. Same gating: only when a fetch is wired; cadence-gated
    # internally; best-effort.
    if gkreq_fetch is not None:
        try:
            logs += gk_request_backstop(
                now, run, state, send_fn, dry_run=dry_run,
                gh_fetch=gkreq_fetch, projects_dir=projects_dir,
                persist=lambda: save_state(state_path, state))
        except Exception as e:
            logs.append("gkreq-backstop error: %r" % (e,))

    # Job 9 — /goal auto-arm (the printed template pastes itself; pure tmux,
    # no network). Best-effort.
    try:
        logs += goal_autoarm(now, run, state, dry_run=dry_run)
    except Exception as e:
        logs.append("goal-autoarm error: %r" % (e,))

    # #70 — shared per-sweep set: job 12 records every sid it actually
    # restarts (or attempts to restart) THIS sweep so job 18 (hooks-reconcile,
    # below) never fires a SECOND restart into the same pane for a hooks
    # config change that happens to land in the same sweep as a model switch.
    model_handled_this_sweep = set()

    # Job 12 — MODEL RECONCILE, restart-based (#42): only when `target_model`
    # is given (cmd_watchdog passes MANAGED_MODEL — this module never
    # hardcodes a model literal). Best-effort.
    if target_model:
        try:
            logs += model_reconcile(now, run, state, target_model,
                                    dry_run=dry_run, projects_dir=projects_dir,
                                    sleep_fn=sleep_fn,
                                    handled=model_handled_this_sweep)
        except Exception as e:
            logs.append("model-reconcile error: %r" % (e,))

    # Job 18 — HOOKS RECONCILE, restart-based (#70): only when
    # `hooks_settings_path` is given (cmd_watchdog passes
    # watchdog.hooks_settings_path()) — same "wired = on" convention as jobs
    # 13/14/16, so an existing caller of run_once() that knows nothing about
    # this job (and every test that doesn't pass it) sees NO behavior change
    # and is never polluted by this box's real ~/.claude/settings.json. Runs
    # right after job 12 so the shared `handled` set above is already
    # populated before this job checks it. Best-effort.
    if hooks_settings_path:
        try:
            logs += hooks_reconcile(now, run, state, dry_run=dry_run,
                                    projects_dir=projects_dir, sleep_fn=sleep_fn,
                                    settings_path=hooks_settings_path,
                                    handled=model_handled_this_sweep)
        except Exception as e:
            logs.append("hooks-reconcile error: %r" % (e,))

    # Job 13 — HOURLY BURN SNAPSHOT (#37): the automatic before/after
    # feedback loop — nothing for the user to remember to check. Only when
    # `burn_snapshot_path` is given (cmd_watchdog passes the real
    # `burn.snapshots_path()`) — same "wired = on" convention as jobs 3/7/
    # 8/11, so an existing caller of run_once() that knows nothing about
    # this job sees NO behavior change (no write to the real
    # ~/.claude/burn-history/ during a test, no surprise state key).
    # Best-effort; internally also cadence-gated to at most once per hour.
    if burn_snapshot_path:
        try:
            logs += burn_snapshot_job(now, state, snapshot_path=burn_snapshot_path,
                                      transcripts_root=str(projects_dir),
                                      dry_run=dry_run)
        except Exception as e:
            logs.append("burn-snapshot error: %r" % (e,))

    # #69 — shared per-sweep set: job 14 and job 15 record every sid they
    # actually compact THIS sweep so job 17 (hard-ceiling backstop, below)
    # never races a second /compact into the same pane. See job 17's own
    # docstring / section comment for the full rationale.
    compact_handled_this_sweep = set()

    # Job 14 — /COMPACT AT TICKET BOUNDARIES (#39 krok 1c): only when
    # `compact_requests_path` is given (cmd_watchdog passes
    # watchdog.compact_requests_path()) — same "wired = on" convention as
    # jobs 3/7/8/11/13, so an existing caller of run_once() that knows
    # nothing about this job sees NO behavior change. Uses the SAME
    # `panes_by_sid` map built above for job 7. Best-effort.
    if compact_requests_path:
        try:
            logs += compact_ticket_boundary(now, run, state, panes_by_sid,
                                            dry_run=dry_run,
                                            path=compact_requests_path,
                                            projects_dir=projects_dir,
                                            send_fn=send_fn,
                                            handled=compact_handled_this_sweep)
        except Exception as e:
            logs.append("compact-request error: %r" % (e,))

    # Job 15 — COMPACT OVERGROWN IDLE SESSIONS (#39/#43 follow-up): ALWAYS
    # wired (no gating param — same "always on" shape as job 9's
    # goal_autoarm), since it depends on nothing external. Closes the gap
    # job 14 leaves open for a long-lived session that never reports a
    # completed-ticket boundary. Best-effort.
    try:
        logs += compact_stale_context(now, run, state, dry_run=dry_run,
                                      projects_dir=projects_dir, sleep_fn=sleep_fn,
                                      send_fn=send_fn,
                                      handled=compact_handled_this_sweep)
    except Exception as e:
        logs.append("compact-stale error: %r" % (e,))

    # Job 16 — HOURLY FLEET BURN (#55): only when `fleet_fetch` is given
    # (cmd_watchdog wires this ONLY on the coordinator, dev1 — every other
    # managed box already writes its own local hourly row via job 13, so
    # this job just merges them). Same "wired = on" convention as jobs
    # 3/7/8/11/13/14. Best-effort; internally also cadence-gated to at most
    # once per hour.
    if fleet_fetch is not None:
        try:
            logs += fleet_burn_job(now, state, fleet_hosts or [], send_fn,
                                   fetch=fleet_fetch, fleet_path=fleet_path,
                                   owner=account_owner or None, dry_run=dry_run)
        except Exception as e:
            logs.append("fleet-burn error: %r" % (e,))

    # Job 19 — HOURLY BURN ALERT (#81): only when `burn_alert_enabled` is
    # truthy (cmd_watchdog computes it the SAME dev1-only way it computes
    # `fleet_fetch` for job 16 — every other managed box never writes
    # fleet.jsonl at all, so this job would just see an empty file there).
    # Runs right after job 16 so it evaluates the row job 16 may have just
    # written THIS sweep. Best-effort; internally cadence-gated to at most
    # once per hour bucket.
    if burn_alert_enabled:
        try:
            logs += burn_alert_job(now, state, send_fn, fleet_path=fleet_path,
                                   owner=account_owner or None, dry_run=dry_run)
        except Exception as e:
            logs.append("burn-alert error: %r" % (e,))

    # Job 17 — HARD CONTEXT CEILING BACKSTOP (#69): ALWAYS wired (no gating
    # param — same "always on" shape as job 9's goal_autoarm and job 15
    # itself), since it depends on nothing external. Runs LAST so job 14's
    # ticket-boundary send (this same sweep, reusing the initial
    # `panes_by_sid` capture) and job 15's idle-based send both get first
    # crack; `_pane_compacting` + the per-session state machine keep this
    # job from double-firing on a session either of them already handled.
    # Best-effort.
    try:
        logs += compact_hard_ceiling(now, run, state, dry_run=dry_run,
                                     projects_dir=projects_dir, send_fn=send_fn,
                                     handled=compact_handled_this_sweep)
    except Exception as e:
        logs.append("compact-ceiling error: %r" % (e,))

    # Job 20 — GOAL RE-ARM BACKSTOP (#76): only when `goal_rearm_enabled` is
    # truthy (cmd_watchdog passes True) — same "wired = on" convention as
    # jobs 13/14/16/18/19, so an existing caller of run_once() that knows
    # nothing about this job sees NO behavior change and never has a pane's
    # goal re-armed by a test. Runs LAST, after every /compact sender, so the
    # shared `compact_handled_this_sweep` set is fully populated before this
    # job decides whether the pane is safe for a ~2 KB keystroke burst.
    # Best-effort.
    if goal_rearm_enabled:
        try:
            logs += goal_rearm(now, run, state, send_fn=send_fn,
                               dry_run=dry_run, projects_dir=projects_dir,
                               handled=compact_handled_this_sweep)
        except Exception as e:
            logs.append("goal-rearm error: %r" % (e,))

    save_state(state_path, state)
    return logs
