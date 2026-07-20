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
      PANE (not reliably a transcript api-error). This is TIME-BASED — `continue`
      BEFORE the reset is a no-op that just re-hits the limit (the user's incident:
      repeated `continue` → "You've hit your session limit"). So the watchdog PINGS
      ONCE with the reset time, waits for the reset clock, then sends ONE `continue`
      AFTER it to resume — never before. State (`sesslimit:<sid>`) carries the parsed
      reset epoch + pinged/continued flags across polls; a genuinely new limit window
      (5h → weekly) re-arms both.
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


def project_label(cwd):
    parts = [p for p in str(cwd).rstrip("/").split("/") if p]
    if not parts:
        return "unknown"
    if parts[-1].lower() in _GENERIC_DIRS and len(parts) >= 2:
        return parts[-2] + "/" + parts[-1]
    return parts[-1]


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
    + one `◯ <agent>` row PER concurrent subagent), the mode hint (`⏵⏵ …`), the `ctx …`
    footer statusline, or a horizontal border rule. Their count is VARIABLE — the agent
    strip grows one row per running subagent — so these MUST be stripped from the bottom
    before locating the `❯` prompt. `s` is already stripped."""
    if not s:
        return True
    if s[0] in "●◯":                                    # agent-strip rows
        return True
    if s.startswith("⏵⏵"):                              # bypass / mode hint
        return True
    if s.startswith("ctx "):                            # footer statusline
        return True
    if _is_border_rule(s):                              # a box border / rule (labelled ok)
        return True
    return False


def _has_free_prompt(captured, bare_only=False):
    """True if the pane shows a FREE `❯` input prompt at the bottom — the session is IDLE
    at the prompt, NOT running a foreground turn (which replaces the input box with a
    spinner / "esc to interrupt" and shows NO input `❯`).

    The input box is the FIRST non-chrome line up from the bottom. We locate it by peeling
    the VARIABLE-height trailing chrome (agent strip — `● main` + one `◯ <agent>` row PER
    concurrent subagent — plus the statusline / mode hint / border rules; see
    `_is_bottom_chrome`) and then testing ONLY that one boundary line. Chrome-stripping
    already absorbs the whole agent strip, so a genuinely idle `⏳ WORKING` session with N
    background workers still lands its `❯` exactly at the boundary regardless of N.

    We must NOT scan a multi-line window above the boundary: during a running foreground
    turn the boundary line IS the spinner, and the transcript output just above it can
    contain a lone `❯` (shell-prompt help, tool output, a session editing THIS very code).
    A window that reached up into that transcript would match the stray `❯`, call a BUSY
    pane idle, and INTERRUPT the running turn — the exact #233 scar this gate prevents. For
    a keystroke gate the safe failure is a MISSED nudge (an unrecognized chrome line below
    the box hides it → we simply don't type), NEVER a false "idle" that types into a turn.
    So: the `❯` must be the boundary line itself. The real prompt renders as `❯` + U+00A0,
    which `str.strip()` reduces to a bare `❯`.

    bare_only=True (the TYPING gate, `pane_at_idle_prompt`): require a BARE `❯` (empty input
    box). If the user has typed text (`❯ blah`) we must NOT type over it. bare_only=False
    (the inverse used by `pane_waiting_on_user`): `❯ <typed text>` still counts as "at a
    prompt, not blocked". A menu pointer `❯ <digit>.` is never a free prompt (open dialog)."""
    if not captured:
        return False
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    i, n = len(lines), 0
    while i > 0 and _is_bottom_chrome(lines[i - 1]) and n < 40:
        i -= 1
        n += 1
    if i <= 0:
        return False
    s = lines[i - 1]                         # ONLY the boundary line — never the transcript above it
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
# The tz the banner names, e.g. "(Europe/Prague)"; default Bratislava (same offset).
_RESET_TZ_RX = re.compile(r"\(([A-Za-z]+/[A-Za-z_]+)\)")


def pane_session_limited(captured):
    """True if the pane shows Claude Code's 5-hour session-limit banner."""
    return bool(captured) and bool(_SESSION_LIMIT_RX.search(captured))


def parse_reset_epoch(captured, now):
    """Parse 'resets <clock>' from the banner into an epoch >= now, or None.
    The clock is read in the tz the banner names (Europe/Prague) — default
    Europe/Bratislava (same offset) — and rolled to tomorrow if already past.
    Fail-safe: any parse/tz error returns None (job 6 then pings but cannot
    auto-resume — the user handles it)."""
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
            tzm = _RESET_TZ_RX.search(captured or "")
            tz = ZoneInfo(tzm.group(1)) if tzm else ZoneInfo("Europe/Bratislava")
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


def send_continue(pane_id, text=NUDGE_TEXT, run=None):
    """Type `text` literally into the pane, then press Enter to submit it."""
    run = run or _default_run
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


DREPLY_TICKET_FALLBACK_S = 600      # reply blocked this long → deliver via the ticket
_TICKET_NUM_RX = re.compile(r"#(\d{1,6})")


def _input_line_text(captured):
    """Text sitting in the pane's INPUT BOX: '' = bare prompt, None = no input
    box at the chrome boundary (running-turn spinner / dialog / no pane). Same
    boundary-line discipline as _has_free_prompt — never scans the transcript
    above the box. For a long WRAPPED input, capture-pane (no -J) renders it as
    multiple lines and the boundary is the LAST one — i.e. the TAIL of the
    typed text — which is why callers match with endswith()."""
    if not captured:
        return None
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    i, n = len(lines), 0
    while i > 0 and _is_bottom_chrome(lines[i - 1]) and n < 40:
        i -= 1
        n += 1
    if i <= 0:
        return None
    s = lines[i - 1]
    if not s.startswith("❯"):
        return None
    return s[1:].strip()


def _ticket_fallback_text(r):
    q = " ".join(str(r.get("question") or "").split())[:400]
    return ("**ODPOVEĎ UŽÍVATEĽA Z DISCORDU** (doručená na ticket — session input "
            "bol obsadený/wedged, watchdog ticket-fallback): «%s»\n\n"
            "Otázka: %s\n\n"
            "Zariaď sa podľa odpovede (číslo = poradie ponúknutej možnosti)."
            % (" ".join(str(r.get("text") or "").split()), q))


def _gh_comment(cwd, num, text):
    """Post `text` as a comment on issue #num of the repo at `cwd` (the DURABLE
    delivery lane of the ticket-fallback). Best-effort: False on any failure —
    the reply then stays pending on the keystroke path."""
    import subprocess
    try:
        p = subprocess.run(["gh", "issue", "comment", str(num), "-F", "-"],
                           cwd=cwd or None, env=_gh_env(), input=text,
                           capture_output=True, text=True, timeout=25)
        return p.returncode == 0
    except Exception:
        return False


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


def deliver_discord_replies(now, run, state, panes_by_sid, dry_run=False,
                            discord_fetch=None, env=None, gh_comment=None):
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
    if not qmap:
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
    channels = {str(v.get("channel") or "") for v in qmap.values()}
    channels.discard("")

    def _delivered(r, via_ticket=None):
        done_set.add(r["reply_id"])
        done.append(r["reply_id"])
        drop_question(r["referenced"])
        qmap.pop(r["referenced"], None)     # same-batch 2nd reply won't re-fire
        blocked.pop(r["reply_id"], None)
        if via_ticket:
            logs.append("reply→ticket #%s [%s]" % (via_ticket, r["session"][:12]))
        else:
            logs.append("reply→%s [%s]" % (project_label(r["cwd"]), r["session"][:12]))

    def _pending(r, why):
        blocked.setdefault(r["reply_id"], now)
        # the durable lane once the keystroke path has been blocked long enough
        if now - blocked[r["reply_id"]] >= DREPLY_TICKET_FALLBACK_S:
            m = _TICKET_NUM_RX.search(r.get("question") or "")
            if m:
                ok = True if dry_run else gh_fn(r["cwd"], m.group(1),
                                               _ticket_fallback_text(r))
                if ok:
                    if not dry_run:
                        _react_ok(r["channel"], r["reply_id"], token)
                    _delivered(r, via_ticket=m.group(1))
                    return
        logs.append("reply pending (%s) %s" % (why, r["session"][:12]))

    for ch in sorted(channels):
        for msg in fetch(ch, token):
            r = parse_discord_reply(msg, allowed, qmap, bot_id="")
            if not r or r["reply_id"] in done_set:
                continue
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
                # busy mid-turn with no input box, a dialog, or a FOREIGN draft
                # we must never type over (#233) — retry / fallback clock.
                _pending(r, "busy")
                continue
            if pane_in_mode(pid, run):
                continue
            if not dry_run:
                if own_stuck:
                    run(["tmux", "send-keys", "-t", pid, "Enter"])
                else:
                    send_continue(pid, prompt, run)
                # verify the input box emptied — a swallowed Enter (#20) leaves
                # the text at `❯`; up to 2 corrective Enters, then give up.
                t2 = _input_line_text(capture_pane(pid, run, lines=30))
                tries = 0
                while t2 and tries < 2:
                    run(["tmux", "send-keys", "-t", pid, "Enter"])
                    t2 = _input_line_text(capture_pane(pid, run, lines=30))
                    tries += 1
                if t2:
                    blocked.setdefault(r["reply_id"], now)
                    logs.append("reply wedged (enter swallowed) %s"
                                % r["session"][:12])
                    continue
                _react_ok(r["channel"], r["reply_id"], token)
            _delivered(r)

    state["dreply_done"] = done[-_DREPLY_DONE_CAP:]
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
# mid-work — the user's standing rule). NB: montalu's claude runs inside
# NEWLEVEL's tmux (a `sudo su - montalu` window) — pane-driven detection is
# what reaches it; a montalu-side watchdog sees no tmux at all.
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
    from its /home/<user>/ prefix (montalu's claude runs under newlevel's tmux,
    so the WATCHDOG user is meaningless; and gh identity is the same account
    everywhere, so @me cannot scope). Reduced streams → their stream label (the
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


def _cache_repo_roots(home=None):
    """{root: name} from the tickets-status cache — the repos this box recently
    worked (the Discord-fallback candidate set for panes that no longer exist)."""
    import statusbar
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
        if root and name:
            roots[root] = name
    return roots


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


# Users whose claude sessions live in ANOTHER user's tmux (montalu runs inside
# newlevel's tmux via `sudo su`) — their own watchdog can never see the pane,
# so its job 8 would ALWAYS conclude "no session runs" and fire a false Discord
# ping (live incident 2026-07-20, #1727/#1732/#1827). The machine's primary
# watchdog owns the pane-driven nudge there; these users skip job 8 entirely.
_FOREIGN_TMUX_USERS = ("montalu",)


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
    for root, name in _cache_repo_roots(home).items():
        if not any(c == root or c.startswith(root + "/") for c in pane_cwds):
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
            if not pane_at_idle_prompt(captured) or pane_in_mode(pid, run) \
                    or not _safe_to_bounce_nudge(captured, root, projects_dir):
                # working / armed-loop / already-nudged pane gets NOTHING —
                # the label alone is the insertion (never interrupt mid-work).
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


_GOAL_CONT_OK = ("```", "─", "•", "**", "❓", "❯", "⎿", "●", "✻")


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
        tail = cap[-1500:]
        if not _ARM_QUESTION_RX.search(tail):
            continue
        # NB: an armed-goal indicator (◎ /goal) does NOT block — a resolved
        # /goal cycle re-prints the arm question while the OLD indicator is
        # still lit, and typing /goal safely replaces the old goal (the gk
        # re-arm incident, 2026-07-20). The tail arm question is authoritative.
        if "esc to interrupt" in tail or "Waiting for" in tail:
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
             discord_fetch=None, bounce_fetch=None):
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
          with the reset time, then send ONE `continue` AFTER the reset clock passes
          (never before — `continue` pre-reset just re-hits the limit);
      (7) (only when `discord_fetch` is given) a session's ❓ ping was ANSWERED by
          the owner REPLYING in Discord → type the answer into that exact session's
          idle pane (deliver_discord_replies), react ✅ on success;
      (8) (only when `bounce_fetch` is given) BOUNCE BACKSTOP — open `prio:bounce`
          (gatekeeper-returned) tickets for a repo this box touches → nudge the
          repo's IDLE claude pane (busy pane = the label alone queues them; never
          interrupt mid-work), or ONE deduped Discord ping when no session runs
          (bounce_backstop, ~30 min cadence);
      (9) /GOAL AUTO-ARM — an idle pane asking to paste a printed /goal template
          gets it typed + submitted (goal_autoarm; the user's exact keystrokes,
          never over user text, never when a goal is already armed).
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
    for pid, cwd in list_claude_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
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

            # --- (6) 5-HOUR SESSION LIMIT → ping once, then `continue` AFTER the reset --
            # A TIME-BASED cap: `continue` BEFORE the reset just re-hits it (the user's
            # incident), so we ping ONCE with the reset time, do NOTHING until the reset
            # clock, then send ONE `continue` AFTER it — never before. Read from the PANE
            # (the banner is on screen, not reliably a transcript api-error). While a
            # session is limited job 6 owns it (skips the api-error / nudge paths).
            if pane_session_limited(captured):
                skey = "sesslimit:" + key
                s = state.get(skey)
                if s is None:
                    # Parse the reset clock ONCE at first detection and keep it stable for
                    # the whole episode — re-parsing after the reset would roll the same
                    # "6:10pm" forward to tomorrow and wrongly re-ping instead of resuming.
                    s = {"resets_at": parse_reset_epoch(captured, now),
                         "pinged": False, "continued": False, "first_seen": int(now)}
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
                elif ra and now >= ra and not s.get("continued"):
                    if pane_in_mode(pid, run):          # never type into a scrolled pane
                        logs.append("skip in-mode (session-limit resume) %s" % (project or pid))
                        continue
                    # Race guard: the user may have manually resumed inside the window and the
                    # session is now running a FOREGROUND agent while the "session limit" banner
                    # is still within the captured pane. Typing `continue` there would INTERRUPT
                    # the live work (the #233 harm class). Only resume into a free `❯` idle
                    # prompt; skip WITHOUT setting `continued` (a later poll retries, and if it
                    # already resumed the banner scrolls out and job 6 exits on its own).
                    if not pane_at_idle_prompt(captured):
                        logs.append("skip busy-pane (session-limit resume) %s" % (project or pid))
                        continue
                    s["continued"] = True
                    logs.append("session-limit %s — reset passed → continue" % project)
                    if not dry_run:
                        send_continue(pid, NUDGE_TEXT, run)
                    send_fn("✅ **%s** — 5h limit sa resetol, poslal som `continue` — "
                            "pokračujem." % project,
                            owner=owner, dedup_key="sesslimit-resume:%s:%s" % (key, ra),
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
        elif k not in stalled:
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
                                            dry_run=dry_run, discord_fetch=discord_fetch)
        except Exception:
            pass

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

    # Job 9 — /goal auto-arm (the printed template pastes itself; pure tmux,
    # no network). Best-effort.
    try:
        logs += goal_autoarm(now, run, state, dry_run=dry_run)
    except Exception as e:
        logs.append("goal-autoarm error: %r" % (e,))

    save_state(state_path, state)
    return logs
