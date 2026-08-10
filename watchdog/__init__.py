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
      limit window (5h → weekly) re-arms it. #336: the SAME `sesslimit:<sid>`
      state is also seeded directly from job (1)'s own TRANSCRIPT-based
      `is_usage_cap` detection (the error TEXT, parsed with
      `parse_reset_epoch_from_error_text`) — never solely from the live pane —
      so a limit hit that lands on a background Agent/subagent and never
      renders the banner as this pane's own bottom-most content still parks a
      resume time and auto-continues; resolution then re-derives from the
      transcript too whenever the pane doesn't currently show the banner, and
      every delivery attempt is gated on `session_user_stopped` (the user's
      own `/exit` since the limit hit always wins, never auto-resumed).
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
past MAX_NUDGES      -> KEEPS 'nudge'-ing forever (#175 — a multi-hour upstream
                        529 storm used to strand a session after ~15-20 min even
                        with a healthy watchdog), at a WIDENING interval (300s x3,
                        then 600/1200/1800/1800/...s, capped). The one-shot "gave
                        up" Discord ping still fires exactly once, the moment the
                        attempt past MAX_NUDGES is due (never noop, never spammed
                        again for the same err_hash).
USAGE/QUOTA cap      -> ping ONCE, NO `continue` (time-based; only the reset clock
                        fixes it — CC auto-resumes when the cap resets); this path
                        alone stays permanently dormant (a quota reset is not
                        something re-nudging can fix)
recovered            -> key dropped from state (a future error starts fresh)

A session waiting on a real `❓` is NEVER auto-continued: its last assistant entry
is the question (not `isApiErrorMessage`), so the error signal is false.

This module is PURE logic + thin tmux shims. The I/O (`run` = tmux exec, `send_fn`
= Discord send) is injectable so the state machine is unit-tested with no tmux and
no network.
"""

import collections
import datetime
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

# Tunables (the CLI may override; defaults match the user's spec: 5-min grace,
# `continue`, 3 retries at the base cadence, then widen — see BACKOFF_CAP_SECONDS).
GRACE_SECONDS = 5 * 60
RETRY_INTERVAL_SECONDS = 5 * 60
MAX_NUDGES = 3
# (#175) Past MAX_NUDGES, decide() no longer gives up — it keeps nudging
# forever, doubling the interval each attempt (300 -> 600 -> 1200 -> 1800),
# capped here so a multi-hour upstream outage (a 529 storm) is covered
# cheaply (one `continue` per interval) instead of stranding the session
# once the fixed 3-strike policy used to run out after ~15-20 min.
BACKOFF_CAP_SECONDS = 30 * 60
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

# (#352) PROOF OF LIFE the pane already renders, unread until now: CC's own
# footer badge for a live BACKGROUND `local_bash` task (a `run_in_background:
# true` Bash call, or its "monitor" kind) — `⏵⏵ bypass permissions on · 1
# shell` / `· N shells` — confirmed live against a real pane holding a
# genuine bounded release-watcher Bash task, and cross-checked directly
# against the installed CC binary's own label-building source
# (`o===1?"1 shell":`${o} shells`` joined with an identical "monitor"/
# "monitors" count). This is DIRECT, pane-visible evidence a session
# genuinely has bounded work still executing — distinct from
# `subagent_active()`'s dispatched-SUBAGENT-transcript signal below, and the
# ONLY signal available for a plain background Bash task (no `subagents/`
# dir at all, since a `run_in_background` Bash call is not a dispatched
# subagent). Live montalu2 incident: the footer showed exactly this badge
# while job 4 kept nudging every ~1h anyway, burning a full context turn
# each time purely to re-prove liveness the pane already proved for free.
_LIVE_BG_TASK_RX = re.compile(r"\b\d+\s+(?:shells?|monitors?)\b", re.I)


def _pane_live_shell_evidence(captured):
    """True if the pane's own GENUINELY CURRENT mode-hint line (`⏵⏵ …`)
    shows CC's live background-shell/monitor badge. (#352 F1, adversarial
    review round 1: scanning the WHOLE bounded capture for ANY
    `⏵⏵`-prefixed line was WRONG — a completion report or playbook excerpt
    quoted verbatim inside the SAME capture window can contain that exact
    text as scrollback, sitting above the real conversation, and would be
    misread as live evidence even with a badge-free CURRENT footer, proven
    by execution.) Fixed the same way every other footer reader in this
    file resolves "what is the pane's OWN trailing chrome right now": walk
    UP from the bottom, peeling only rows `_is_bottom_chrome` accepts as
    genuinely trailing chrome (agent strip, statusline, mode hint, border
    rules — the identical bounded walk `_above_input_box` already uses),
    and only ever look for the badge WITHIN that walk. The walk stops dead
    at the first non-chrome row (an ordinary input-box `❯` line, real
    conversation prose) — a quoted scrollback line sitting ABOVE that
    boundary is structurally unreachable, never merely unlikely to match."""
    lines = str(captured or "").splitlines()
    i = len(lines)
    n = 0
    while i > 0 and _is_bottom_chrome(lines[i - 1].strip()) and n < 40:
        i -= 1
        n += 1
        s = lines[i].strip()
        if s.startswith("⏵⏵") and _LIVE_BG_TASK_RX.search(s):
            return True
    return False


# (#352 F3, adversarial review round 1) The skip above trusts whatever the
# pane's LAST RENDER shows — that is sound for a HEALTHY session (the render
# is only ever as old as the last screen update), but if Claude Code's own
# process wedges WHILE the badge happens to be on screen, the frozen render
# keeps "proving" life forever with nothing left alive behind it, which
# would permanently silence the busy-pane wedge-ping for exactly the
# wedged-process population that check exists to catch (a #134-class
# suppression with no reachable exit, reproduced live: `kill -STOP` the
# claude process mid-turn with the badge showing, then kill the background
# PIDs too — the badge never leaves the screen). Bound how long the badge
# ALONE is trusted without the normal flow getting a real look: past this
# many CONSECUTIVE seconds of skipping the SAME session, fall through once
# (still gated by every existing safety check that flow already has —
# copy-mode, the busy-pane ping's own one-shot dedup, decide_working's own
# schedule) and then reset the streak, so a still-legitimate long task earns
# a fresh trust window rather than being force-checked every sweep from then
# on. Sized well past the reported incident's own bounded monitor (3h strop)
# so it never interrupts the exact case this fix exists for, while still
# giving a genuinely wedged pane a periodic real check instead of none ever.
LIVE_SHELL_TRUST_CAP_S = 6 * 3600


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


def transcript_current_context(path, max_lines=200):
    """The session's CURRENT context size — cache_read_input_tokens +
    cache_creation_input_tokens off the newest assistant usage entry.
    Feeds job 14's #48 small-context gate (job 15's own use, REMOVED #102,
    no longer applies).

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


def transcript_last_assistant_text(path):
    """FULL text of the session's last REAL assistant message (same
    walk/skip semantics as `transcript_last_marker_line` — synthetic/
    tool-only sentinels and an `isApiErrorMessage` entry are skipped), or
    `''` if none. Unlike `transcript_last_marker_line` (which returns only
    the tail-trimmed marker line), this returns the WHOLE message — job
    20's goal-achieved backstop (#160) needs to scan it for a genuine
    `🏁 BACKLOG EMPTY:` claim anywhere in the turn, not just its last 3
    lines."""
    for entry in reversed(_iter_jsonl_tail(path)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        if entry.get("isApiErrorMessage") is True:
            return ""
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue
        return text
    return ""


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

# (#287) The per-worker nudge/escalate dedup state `_nudge_dying_subagent`
# keeps at `state["subagent-<prefix>:<wid>"]` used to be aged out by the
# generic episode-cleanup's WAIT_CLEAR_SECONDS (90s) -- the SAME short TTL
# tuned for the SUPERVISOR's own transient wait/working episodes. That is
# wrong for THIS state: `_nudge_dying_subagent` only ever runs while the
# supervisor's own last marker reads `⏳ WORKING` (see the two call sites'
# gates), which a busy /goal loop working OTHER tickets steps away from for
# minutes at a stretch (a completed ticket's own `✅ DONE` turn, per
# message-status-marker.md's per-ticket boundary). Every such gap exceeding
# 90s silently wiped "already nudged N times" back to zero, so the SAME dead
# worker's nudge#1 re-fired every time the gate reopened -- a full paid turn
# each time, unbounded (the observed "identical nudge delivered forever").
# Bound this state by the SAME ceiling that already decides whether to look
# at the worker at ALL (SUBAGENT_MAX_AGE_SECONDS): `sub_idle` only grows, so
# once the outer grace<=sub_idle<=SUBAGENT_MAX_AGE_SECONDS gate permanently
# closes for a wid it can never reopen -- the entry is never needed again
# once genuinely stale, and this TTL is comfortably longer than any
# realistic gap between `⏳` sightings within the still-active window.
#
# Residual (adversarial-review finding, THEORETICAL, no live trigger named):
# `wid` is the dispatched agent's own transcript filename stem, assigned
# fresh per dispatch and never observed reused -- but IF something ever
# reused the identical wid for a genuinely NEW dispatch within this TTL
# window (a same-file "follow up on the worker" path, say), the OLD
# worker's already-`escalated` entry would keep the new death's nudge
# suppressed until the TTL expires. Not reachable via any dispatch path
# this repo has today; worth re-checking if that ever changes.
SUBAGENT_NUDGE_STATE_TTL_SECONDS = SUBAGENT_MAX_AGE_SECONDS


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
# (#175 F2) The WEEKLY cap ("You've hit your weekly limit …") and the BARE cap
# ("You've hit your limit …", no qualifier word at all) used to be invisible
# here — only "session"/"usage" were recognized before "limit", and the literal
# space required between "limit" and "reached/resets" never matched Claude
# Code's real rendering, which separates them with a MIDDLE DOT ("limit ·
# resets 11am …"), not a space. Both gaps let a real weekly/bare cap fall
# through to the generic nudge path and get `continue`d every ~30 min for the
# WHOLE cap window (days), instead of staying bounded (ping once, wait for the
# reset). `[\s·]*` accepts any run of whitespace and/or the middle-dot
# separator between "limit" and the reset wording; `(?:session|usage|weekly)?`
# is now optional so the bare "hit your limit" shape matches too.
_USAGE_CAP_RX = re.compile(
    r"usage limit|quota|limit[\s·]*(?:reached|will reset|resets)|reset at|reached your"
    r"|hit your (?:(?:session|usage|weekly)\s+)?limit", re.I)
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
_MENU_POINTER_RX = re.compile(r"❯[ \xa0]\d+\.")


def _is_border_rule(s):
    """A box border / horizontal rule line (`╭────╮`, `─── labelled ok ───`). `s` is
    already stripped. Split out of `_is_bottom_chrome` because `pane_question_excerpt`
    needs borders as BOUNDARY MARKERS (they delimit the dialog box) while the other
    chrome rows (agent strip, statusline) are plain drops."""
    bars = sum(c in "─—━═╌╍┄┅┈┉╭╮╰╯┌┐└┘│┃" for c in s)
    return bars >= 4 and bars >= len(s.replace(" ", "")) - 12


# airuleset's OWN managed statusline (composed by `statusbar.py` through the
# caveman shim), rendered directly below the input box. Every segment is
# individually optional and their ORDER has changed once already: the ctx meter
# used to lead the row (`ctx ██░░░  5h 20% …`), which is the only reason a
# `startswith("ctx ")` test ever worked. #223 dropped the fill bar and the row
# now starts `5h 7%(4h)` — so the test silently stopped matching, the bottom-up
# chrome peel stopped ON the statusline, and handed it back as the input box
# (#243). Match the segment VOCABULARY anywhere in the row instead of anchoring
# on whichever segment happens to lead — but require at least TWO distinct
# segment shapes to co-occur: a real statusline always carries several, while
# ordinary prose quoting ONE token with its value ("the wk 65% figure") must
# never be eaten as chrome (adversarial review of #243, finding 3 — the harm
# is a wrapped draft's continuation row swallowed as chrome, returning the
# wrong tail).
_STATUSLINE_SEG_RES = (
    re.compile(r"(?:^|\s)ctx [\d█▓▒░]"),
    re.compile(r"(?:^|\s)5h \d+%"),
    re.compile(r"(?:^|\s)wk \d+%"),
    re.compile(r"(?:^|\s)sub \d+\.\d+\."),
    re.compile(r"caveman:"),
    re.compile(r"(?:^|\s)(?:F|Fable) \d+%"),
    re.compile(r"~\$\d"),
    re.compile(r"(?:^|\s)(?:I|Issues) \d+"),
)


def _statusline_hits(s):
    return sum(1 for rx in _STATUSLINE_SEG_RES if rx.search(s))


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
    if s.startswith("ctx "):                            # legacy pre-#223 statusline
        return True
    # The managed statusline in ANY segment order (#243): at least TWO distinct
    # segment shapes must co-occur — see _STATUSLINE_SEG_RES. A row carrying
    # the prompt glyph is the input box and must never be peeled away as chrome
    # however it reads; the `❯ ●` / `❯ ◯` selected-strip shapes are already
    # answered by their own branch above, so this guard cannot regress them
    # (#36).
    if s[0] != "❯" and _statusline_hits(s) >= 2:
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


# CC also renders a COUNTED form of the same hint once more than one message
# is queued ("Press up to edit 2 queued messages") — a regex, not the
# original exact-equality check, so every counted variant normalizes too
# (#176 item 4: the exact check missed this shape and misread it as a real
# held draft).
_QUEUED_PLACEHOLDER_RX = re.compile(r"^press up to edit(?:\s+\d+)?\s+queued messages$")


def _find_boundary_line(captured):
    """Locate the pane's INPUT-BOX boundary line — its LAST row.

    Since #193 the consumers (`_has_free_prompt`, `_input_line_text`,
    `_classify_boundary`) no longer test this row for the prompt glyph: it is
    the box's TAIL, and a WRAPPED draft's tail never carries one. They resolve
    through `_find_input_box`, which reads the glyph off the box's HEAD row.
    This function remains the boundary/"is there anything there at all"
    answer, and `_classify_boundary` still uses it to tell a real-but-not-a-box
    boundary ("busy") from no boundary at all. Two strategies, issue #46:

    1. STRUCTURAL (tried first). The input box always renders as
       `separator / ❯ <draft> / separator`. Find the LAST pair of separator
       lines in the capture and take the line immediately above the second
       (bottom) one. This is immune to whatever Claude Code renders BELOW
       the box — the agent strip, the statusline, or any UI element never
       seen before (the live `⧉  <project>` row that made job 14 and
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
    the user typed), singular or a COUNTED variant ("... 2 queued messages"),
    is normalized to a bare `❯` before returning (#65 acceptance, widened by
    #176 item 4: this placeholder is never mistaken for a real draft by any
    caller). The normalization itself lives in `_normalize_queued_hint`, which
    `_find_input_box` applies to the head row too — so there is still exactly
    ONE definition of that hint, applied wherever a glyph row is read.

    Returns the raw (stripped) boundary line, or None if NEITHER strategy
    locates one at all (e.g. the whole capture is chrome, or it's empty)."""
    return _normalize_queued_hint(_find_boundary_line_raw(captured))


def _normalize_queued_hint(line):
    """Collapse CC's greyed `Press up to edit [N] queued messages` HINT to a
    bare `❯`. One place, so every caller resolving through this module agrees
    (#65/#176 item 4)."""
    if line is not None and line.startswith("❯") \
            and _QUEUED_PLACEHOLDER_RX.match(line[1:].strip().lower()):
        return "❯"
    return line


def _find_boundary_line_raw(captured):
    """The LAST row of whatever sits at the pane's chrome boundary — the input
    box's tail when there IS a box (for a WRAPPED draft the tail of the typed
    text, which is why callers match with `endswith()`), otherwise whatever
    occupies that position instead: a running turn's spinner, an open dialog's
    last row. Deciding WHICH of those it is belongs to `_find_input_box`. The
    two-strategy scan itself lives in `_input_box_rows_raw`; see
    `_find_boundary_line`'s docstring for its full rationale."""
    rows = _input_box_rows_raw(captured)
    return rows[-1] if rows else None


def _input_box_rows_raw(captured):
    """The pane's bottom input-box candidate ROWS (stripped), HEAD FIRST.

    The scan `_find_boundary_line_raw` has always performed, factored out so
    the box's FIRST row is reachable and not only its last (#193). The glyph
    that identifies a row as an input box sits on the box's first RENDERED
    row; a wrapped draft's last row is its tail and never carries it, so
    every consumer that tested the boundary row for `❯` read a wrapped draft
    as "there is no input box at all".

    1. STRUCTURAL (tried first). The rows strictly between the last pair of
       separator lines ARE the box, bounded by its own borders — so reading
       the head needs no scan into the transcript above. A live CC 2.1.220
       pane renders exactly this shape (`────` / `❯\xa0…` / `────`, read off
       three real panes 2026-07-30), which is why this is the strategy every
       real capture resolves through.
    2. GLYPH-BASED FALLBACK, for a borderless capture: peel the VARIABLE
       trailing chrome (`_is_bottom_chrome`) and take the first non-chrome
       row up from the bottom. It returns ONE row and deliberately never
       more. Nothing bounds the box there, so walking further up is exactly
       the #233 scar — during a running turn the boundary row IS the spinner
       and the transcript above it can contain a lone `❯`, so an upward
       window would call a BUSY pane idle and INTERRUPT it. Continuation rows
       arrive stripped of the indentation that would identify them, and CC's
       transcript rows are themselves indented, so no sound stop condition
       exists. The unknown stays unknown and `_find_input_box` resolves it to
       "no box" — the safe direction for the may-I-type question.

    Returns [] when neither strategy locates anything."""
    if not captured:
        return []
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    if not lines:
        return []

    seps = [i for i, ln in enumerate(lines) if _is_separator_line(ln)]
    if seps:
        idx_b = seps[-1]
        # The BOTTOM edge stays STRICT — it is the anchor, and a real pane
        # always renders it pure. The TOP edge may carry a LABEL: Claude Code
        # writes the session's effort mode into the box's own top border
        # (`──── ultracode ─`), which the strict test rejects (#243, live on
        # dev2's presenter pane). Three guards shape the scan, all from the
        # adversarial review of that fix:
        #
        # 1. A candidate pair is trusted ONLY when nothing below the bottom
        #    edge could be the REAL box or a REAL running turn: a non-chrome
        #    row starting with the prompt glyph (a genuine draft/bare prompt
        #    below the candidate) or carrying "esc to interrupt" (a live
        #    foreground turn) means the pair is QUOTED transcript content, not
        #    the pane's own box — reject it and let the glyph fallback find
        #    the real state. A real box has only chrome below it; requiring
        #    full chrome below would re-open the unknown-chrome hole (#46,
        #    the `⧉` row incident), so only these two decisive shapes reject.
        # 2. The box's HEAD is the nearest row above the bottom edge carrying
        #    the prompt glyph, found by walking up PAST non-glyph content
        #    rows (a wrapped draft's own lines — including a pasted table row
        #    `│ a │ b │ c │`, which `_is_border_rule` would misread as the
        #    top edge) but never past a STRICT separator (crossing one would
        #    leave the box's own span).
        # 3. The row immediately above the head must be a border — strict, or
        #    a labelled `_is_border_rule` (the ultracode shape). No border
        #    above the glyph row means this is not a box.
        # `_is_separator_line` itself stays strict — it is shared with every
        # other separator consumer, and its strictness is what stops a line
        # of prose being read as the box's own edge.
        below_disqualifies = any(
            not _is_bottom_chrome(ln)
            and (ln.startswith("❯") or "esc to interrupt" in ln)
            for ln in lines[idx_b + 1:])
        if not below_disqualifies:
            i = idx_b - 1
            while i >= 0 and not _is_separator_line(lines[i]) \
                    and not lines[i].startswith("❯"):
                i -= 1
            if i > 0 and lines[i].startswith("❯") \
                    and (_is_separator_line(lines[i - 1])
                         or _is_border_rule(lines[i - 1])):
                content = lines[i:idx_b]
                if content:
                    return content

    i, n = len(lines), 0
    while i > 0 and _is_bottom_chrome(lines[i - 1]) and n < 40:
        i -= 1
        n += 1
    if i <= 0:
        return []
    return [lines[i - 1]]


def _box_is_wrapped(captured):
    """True if the pane's input box renders over more than one row — i.e. any
    append to it re-flows the whole box, so neither an exact-content check nor
    an exact append signature can be read back out of it."""
    box = _find_input_box(captured)
    return bool(box and box[2])


def _is_draft_head(s):
    """True if `s` is an input-box row carrying the prompt glyph AND text —
    `❯`, CC's separator (a NON-BREAKING SPACE on a real pane), then a draft.
    A BARE box (`❯` alone) and a menu pointer (`❯ 1. Yes` — an open dialog,
    never an input prompt) are both excluded."""
    return bool(s) and len(s) > 1 and s[0] == "❯" and s[1] in " \xa0" \
        and not _MENU_POINTER_RX.match(s)


def _find_input_box(captured):
    """Locate the pane's INPUT BOX. Returns `(head, tail, wrapped)` or None.

    This is the one place that decides "is there an input box here", and it
    decides it from the row that actually carries the prompt glyph — the
    box's HEAD — rather than from whichever row happens to be its boundary
    (#193). `head` is the glyph row, `tail` the box's last row (a wrapped
    draft's TAIL, the documented `endswith()` contract), `wrapped` says
    whether the two differ.

    STRICTLY ADDITIVE by construction: when the BOUNDARY row itself starts
    with `❯` that row is the box, exactly as every consumer has always read
    it. Only when it does NOT — precisely where they all get None / "busy"
    today — do we consult `rows[0]`, and only accept it as a box when it is a
    genuine non-bare, non-menu prompt row with at least one further row below
    it. So no capture that currently reads as an input box can change its
    answer; the change can only turn a "no box" into a box.

    Fail direction: None. The callers asking "may I type here?" resolve that
    to NO. The caller asking "is there a draft I would destroy?" (job 10)
    must NOT read it as "there is no draft" — an unreadable box is unknown,
    not empty."""
    return _find_input_box_from(_input_box_rows_raw(captured))


def _find_input_box_from(rows):
    """`_find_input_box`'s decision over rows already scanned, so a caller that
    needs BOTH the rows and the verdict pays for one scan instead of two."""
    if not rows:
        return None
    tail = _normalize_queued_hint(rows[-1])
    if tail.startswith("❯"):
        return (tail, tail, False)
    if len(rows) < 2:
        return None
    head = _normalize_queued_hint(rows[0])
    if not _is_draft_head(head):
        return None
    return (head, rows[-1], True)


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
    prompt, not blocked". A menu pointer `❯ <digit>.` is never a free prompt (open dialog).

    LIVE-VERIFIED (a real CC v2.1.220 scratch session, #100/#101 live proof): CC renders the
    separator between `❯` and any typed text as a NON-BREAKING SPACE (`\xa0`), never a plain
    ASCII space — a BARE box captures as the literal single glyph `'❯'` (nothing to separate),
    but the instant there is text it is `'❯\xa0<text>'`. A check anchored on a plain `"❯ "`
    therefore NEVER matches a real held draft, which made `deliver_with_stash`'s own
    idle-with-draft precondition refuse EVERY real delivery with "not idle-with-draft" — the
    exact #101 incident signature — regardless of how genuinely idle the pane was. Both
    characters are accepted below; `_input_line_text` already worked correctly throughout
    (`str.strip()` treats `\xa0` as whitespace).

    The glyph is read off the box's HEAD row, never its boundary row (#193) —
    a WRAPPED draft puts its tail at the boundary, so testing the boundary for
    `❯` reported "no free prompt" for every payload long enough to wrap. That
    condition NAMED "the boundary row begins with the glyph" while it was
    asked to DECIDE "is there an input box I may type into". Fail direction is
    unchanged and deliberate: an unlocatable box answers NO (do not type)."""
    box = _find_input_box(captured)
    if box is None:
        return False
    head, _tail, _wrapped = box
    if head == "❯":
        return True
    return bool(not bare_only and _is_draft_head(head))


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
# (#175 F2) Claude Code also renders a WEEKLY cap ("You've hit your weekly
# limit · resets Jul 31, 9pm (Europe/Prague)") and a BARE one ("You've hit
# your limit · resets 11am (Europe/Prague)"), with no "session"/"usage" word
# at all — this regex used to require one, so both shapes fell straight
# through to job 1's generic nudge path and got `continue`d every ~30 min for
# the whole cap window instead of getting job 6's bounded ping-once-then-wait
# treatment. `(?:session|usage|weekly)?` is now optional.
#
# (#172, carried over from #175/#176's own closing pass) A weekly cap's
# "resets Jul 31, 9pm" clock names an explicit CALENDAR DATE ahead of the
# time-of-day — `_RESET_TIME_RX` used to require a digit immediately after
# "resets "/"resets at ", so this dated form matched `is_usage_cap` (bounded,
# per #175 F2 above) but `parse_reset_epoch` returned None: job 6 could ping
# once but never compute a resume instant, so a weekly-capped session pinged
# once and then never auto-resumed even after the real reset passed — the
# user had to type `continue` by hand. The optional `(?:MONTH DAY,? )?`
# group below captures the date too, and `parse_reset_epoch` uses it (rather
# than assuming "today") when present — assuming today would compute an
# epoch DAYS too early for a multi-day-out weekly reset, and job 6 would
# retry-resume long before the real reset, immediately re-hitting the limit
# (exactly what this whole mechanism exists to prevent). The bare
# clock-time-only forms (`resets 11:20pm`, `resets 12pm`, `resets at 18:10`)
# are unaffected — the date group is optional and simply doesn't match them.
_SESSION_LIMIT_RX = re.compile(
    r"hit your (?:(?:session|usage|weekly)\s+)?limit|/usage-credits to finish", re.I)
# "resets 6:10pm" / "resets 6pm" / "resets at 18:10" / "resets Jul 31, 9pm"
# -- capture an optional MONTH + DAY ahead of the clock, then the clock.
_RESET_TIME_RX = re.compile(
    r"reset(?:s|ting)?\s+(?:at\s+)?(?:([A-Za-z]{3,9})\s+(\d{1,2}),?\s+)?"
    # #183 finding 2: the hour group is `(?!\d)`-guarded so it can never be
    # a TRUNCATED PREFIX of a longer digit run — without it, a 4-digit year
    # ("resets Jul 31, 2026 9pm") silently absorbed its first two digits as
    # the hour (epoch one hour early) instead of the whole match failing
    # (the previously fail-safe None a 2-digit year / reversed-order form
    # still correctly returns).
    r"(\d{1,2})(?!\d)(?::(\d{2}))?\s*([ap]m)?", re.I)
_RESET_MONTH_NUM = {name: i + 1 for i, name in enumerate((
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec"))}
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
# #183: how stale a DATED reset target (this year's occurrence) may be
# before it must mean NEXT year's occurrence instead. The bare-clock
# branch's OWN 6h window is sized for a 5-HOUR session-limit banner, which
# can only ever be a few hours stale — far too tight for the WEEKLY-cap
# banner `parse_reset_epoch` ALSO parses (the same function, the dated
# branch), whose date can legitimately be up to ~7 days out. Comfortably
# wider than one full weekly cycle so a genuinely-this-week date is never
# mistaken for "must be next year".
DATED_RESET_STALE_GRACE_S = 8 * 86400


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
    """Parse 'resets <clock>' (optionally 'resets <Month> <day>, <clock>')
    from the banner. The BARE-CLOCK form (a 5-hour session-limit reset)
    always returns an epoch >= now (rolled to tomorrow if already past by
    more than 6h). The DATED form (also used for a WEEKLY-cap banner, whose
    date can legitimately be up to ~7 days out) returns the parsed target
    AS-IS whenever it is within `DATED_RESET_STALE_GRACE_S` of now — INCLUDING
    slightly in the past, which means "the reset already happened" and is
    correct, not an error: job 6 treats any `resets_at <= now` as "resume
    immediately". Only once THIS YEAR's occurrence is stale by more than
    that grace does it roll to next year's occurrence. Either way, returns
    None whenever the banner cannot be read with confidence — see the
    per-branch notes below — so job 6 pings but leaves the episode
    refinable rather than locking in a wrong epoch.

    The clock is read in the tz the banner names: "UTC"/"GMT" literally, an
    "Area/City" name via ZoneInfo, any other bare parenthesized word (e.g. a
    stray "(debug)" elsewhere in the pane) falls back to the
    Europe/Bratislava default (same offset as Prague). The tz is searched
    ONLY in the ~80 chars starting at the TIME match, never the whole
    capture: a global search would hijack on ANY parenthesized word
    anywhere in the pane, however far from the clock (gk incident
    2026-07-24). Fail-safe: any parse/tz error returns None (job 6 then
    pings but cannot auto-resume — the user handles it).

    #172 (carried over from #175/#176's own closing pass): when the banner
    names an explicit calendar date ("resets Jul 31, 9pm"), that date is
    used for the target — NOT "today". Assuming today for a multi-day-out
    weekly reset would compute an epoch DAYS too early, and job 6 would
    retry-resume long before the real reset (immediately re-hitting the
    limit — the one thing `continue`-before-reset must never do).

    #183 finding 3: the search is BOTTOM-SCOPED to the same last-10-lines
    region `pane_session_limited` itself uses, never the whole capture — a
    STALE reset-time echo higher on screen (a dead background worker's old
    output, or last episode's own banner) must never beat a fresher banner
    lower down; before this the parse searched globally while the detector
    that gates it was already deliberately bottom-scoped (the exact
    stale-echo shape `pane_session_limited`'s own docstring documents).

    #336: the box-scoping happens ONLY here — the actual clock/timezone/
    date parse below is shared with `parse_reset_epoch_from_error_text`
    (job 1's own PLAIN error-message text, which needs no pane/box scoping
    at all) via `_reset_epoch_from_scanned_text`."""
    try:
        region = _above_input_box(captured)
        lines = [ln for ln in region.splitlines() if ln.strip()]
        if not lines:
            lines = [ln for ln in (captured or "").splitlines() if ln.strip()]
        scoped = "\n".join(lines[-10:])
    except Exception:
        return None
    return _reset_epoch_from_scanned_text(scoped, now)


def _reset_epoch_from_scanned_text(scoped, now):
    """The shared clock/timezone/date-parsing core of `parse_reset_epoch` —
    `scoped` is ALREADY the text to search (a pane's bottom-scoped region
    for the pane-based caller, or a plain error-message string for
    `parse_reset_epoch_from_error_text`). See `parse_reset_epoch`'s own
    docstring for the full parsing contract; this function does not repeat
    it. Fail-safe: any parse/tz error returns None."""
    try:
        # The LAST match, not the first: `.search()` would still pick a
        # STALE echo sitting higher in the scoped window over a FRESHER
        # banner below it (#183 finding 3's exact reproduction — bottom
        # scoping alone narrows the window, it doesn't reorder within it).
        matches = list(_RESET_TIME_RX.finditer(scoped))
        if not matches:
            return None
        m = matches[-1]
        month_name, day_s, hh_s, mm_s, ap_s = m.groups()
        hh = int(hh_s)
        mm = int(mm_s or 0)
        ap = (ap_s or "").lower()
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
            seg = scoped[m.start():m.start() + 80]
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
        month = _RESET_MONTH_NUM.get((month_name or "")[:3].lower())
        if month_name and not month:
            # #183 finding 1: the date group MATCHED (a month-shaped word +
            # a day both present) but the word is not a recognised month
            # (e.g. a weekday, "Thu 31" — the regex only requires 3-9
            # letters, it never validates the word itself). Falling through
            # to the bare-clock branch below would silently reuse TODAY's
            # date with this banner's clock, computing an epoch DAYS too
            # early — the exact "resumes before the real reset, immediately
            # re-hits the limit" outcome this whole function exists to
            # prevent. An unrecognised month must return None, not guess.
            return None
        if month and day_s:
            # An explicit calendar date -- use IT, not "today" (see the
            # docstring above).
            try:
                target = base.replace(month=month, day=int(day_s), hour=hh,
                                      minute=mm, second=0, microsecond=0)
            except ValueError:
                return None       # e.g. day out of range for the month
            # #183 findings 4/5: NOT the bare-clock branch's 6h window (see
            # DATED_RESET_STALE_GRACE_S's own comment) -- a dated target
            # slightly in the past (including a small negative delta) is
            # simply returned as-is; only real staleness beyond one weekly
            # cycle means "must be next year".
            if target.timestamp() <= now - DATED_RESET_STALE_GRACE_S:
                target = target.replace(year=target.year + 1)
            return target.timestamp()
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


def parse_reset_epoch_from_error_text(text, now):
    """Same clock/timezone/date-parsing as `parse_reset_epoch`, run directly
    over a PLAIN error-message STRING (job 1's own `transcript_last_error()`
    output) instead of a captured tmux pane — no box-scoping, no agent-strip
    chrome to strip first, since the transcript's own `isApiErrorMessage`
    text is already just the message.

    #336: this is what lets a session-limit hit that NEVER renders its
    banner on the live pane (a background Agent/subagent dying on the
    account's 5h limit, whose failure only ever shows up in the parent
    session's OWN next `isApiErrorMessage` entry, never as pane chrome —
    the montalu2 incident) still get a resume time parked from the error
    TEXT itself, instead of depending on job 6's live, continuously
    re-scanned pane detection, which structurally cannot see an error that
    was never rendered as the pane's bottom-most content in the first
    place."""
    return _reset_epoch_from_scanned_text(text or "", now)


def session_user_stopped(tpath, since_ts=None):
    """True if the user explicitly told THIS session to stop (`/exit`) at
    or after `since_ts`. The narrow, session-limit-scoped counterpart of
    #335's own (not-yet-landed at the time this was written) general
    user-stop invariant, expected to be reconciled into #335's own
    `_goal_user_exit_ts` once it ships — a one-point integration, not a
    permanent second implementation; #336's own auto-resume mechanism does
    not need to wait on that landing.

    A session the user deliberately exited must NEVER be auto-resumed by
    delivering `continue`, even once its parked reset time has passed and
    even if the SAME transcript is later reattached (`claude -c`) — the
    user's own explicit `/exit`, issued after the limit hit, is a stronger
    signal than "the reset clock passed" and always wins.

    Scans the transcript's recent tail for a top-level, plain-STRING
    `/exit` command entry — Claude Code's own literal marker for the user's
    `/exit` command, the same top-level shape #335's own design comment
    names. Matched by PREFIX (`<command-name>/exit</command-name>`), never
    strict equality: a real `/exit` entry's `message.content` is a
    COMPOSITE string, e.g.
    `"<command-name>/exit</command-name>\n            <command-message>exit`
    `</command-message>\n            <command-args></command-args>"`
    (verified against real Claude Code transcripts, #336's own adversarial
    review, finding F1) — a strict-equality check against the bare marker
    alone NEVER matches a real transcript, which made this whole predicate
    inert against every genuine `/exit`. The closing `</command-name>` tag
    is part of the required prefix, so a DIFFERENT command name that merely
    starts with the same letters (never a real Claude Code shape, but
    checked defensively) is still correctly refused. `timestamp` must be
    `>= since_ts` (no lower bound at all when `since_ts` is None).

    Fail-SAFE in the direction that never strands a healthy session: an
    unreadable/missing transcript, or any parse error, returns False —
    "can't tell" must never be read as "the user stopped it", or a merely-
    unreadable transcript would strand a session that was never actually
    exited."""
    try:
        from datetime import datetime
        for entry in _iter_jsonl_tail(tpath, max_lines=400):
            if not isinstance(entry, dict) or entry.get("type") != "user":
                continue
            msg = entry.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, str):
                continue
            if not content.lstrip().startswith("<command-name>/exit</command-name>"):
                continue
            if since_ts is None:
                return True
            try:
                ts = datetime.fromisoformat(
                    str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue          # unparseable timestamp — this is an ANY
                                  # over the whole window (oldest-to-newest
                                  # file order, not reversed), so a single
                                  # bad timestamp just moves on to the next
                                  # candidate entry, in either direction
            if ts >= since_ts:
                return True
        return False
    except Exception:
        return False


def _human_clock(epoch, now=None):
    """Epoch → 'HH:MM' in Europe/Bratislava, for the ping text — but only
    when the reset falls on TODAY's local date (relative to `now`, default
    the real wall clock). #183 finding 6: `parse_reset_epoch` started
    successfully parsing a multi-day-out WEEKLY-cap banner without this
    consumer ever being updated to match — a cap five days out read as
    "Reset o 21:00", telling the user it resumes TONIGHT when it actually
    resumes on a later date. A reset on a different day renders
    'DD.MM HH:MM' instead."""
    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Europe/Bratislava")
        except Exception:
            tz = None
        dt = datetime.fromtimestamp(epoch, tz)
        today = datetime.fromtimestamp(time.time() if now is None else now, tz)
        if dt.date() == today.date():
            return dt.strftime("%H:%M")
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return "?"


def decide(state, key, err_hash, now, grace=GRACE_SECONDS,
           interval=RETRY_INTERVAL_SECONDS, max_nudges=MAX_NUDGES, first_seen_seed=None,
           backoff_cap=BACKOFF_CAP_SECONDS):
    """Pure decision for ONE stalled session. Returns (action, entry) where action
    is 'nudge' | 'wait'. `entry` is the updated state record (caller persists
    state[key] = entry).

    The grace is tracked HERE, from `first_seen` (the moment the session's last
    reply became an api-error), NOT from transcript mtime — Claude Code's own
    retries + queue/snapshot writes keep touching the transcript, so an mtime-idle
    gate never trips for a rate-limited session (that bug left `presenter`
    unnudged). On first sighting `first_seen = first_seen_seed` (the caller seeds it
    with `now - idle` so an already-stale stall counts from when it really began);
    if that is already >= grace old the first `continue` goes out NOW, else we
    `wait` and let Claude Code recover on its own for `grace` first. Thereafter a
    nudge fires every `interval`, for the first `max_nudges` attempts.

    PAST `max_nudges` the policy no longer gives up (#175 — a multi-hour upstream
    529 storm used to strand a session after ~15-20 min of silence even with a
    healthy watchdog, and the hash-stability rule made it worse: a REPEATED
    identical error is exactly the case that never re-arms). Nudging CONTINUES
    INDEFINITELY, but the retry interval WIDENS each attempt (doubling from
    `interval`, capped at `backoff_cap`) so a long outage is covered cheaply
    (one `continue` per interval) instead of hammering a dead endpoint: attempts
    #1-#3 at `interval` (300s) spacing, then 600 / 1200 / 1800 / 1800 / ... The
    one-shot "gave up" Discord ping still fires exactly once — the caller detects
    it by `entry['escalated']` flipping False -> True on the attempt that FIRST
    crosses `max_nudges` (the (max_nudges + 1)-th nudge) and fires its own ping
    then; every later call leaves `escalated` True with no further ping. A
    different err_hash (a new error) restarts the whole cycle from scratch,
    including a fresh one-shot escalation.

    A caller-forced `entry['dormant']` (used for a usage/quota cap, where only
    the external reset clock — not `continue` — can fix it) makes THIS CALL
    return 'wait' regardless of the schedule above. (#175 F4 correction: a new
    err_hash is the COMMON way the flag goes away, not the ONLY one — the
    caller's own state-cleanup pass can drop `state[key]` entirely, e.g. once
    the session's pane is no longer visible. That is harmless here: the caller
    re-derives `dormant` from `is_usage_cap(err_text)` on every sweep that
    reaches a fresh 'nudge', so a rebuilt entry is immediately re-marked
    dormant from the SAME live error text — no `continue` is ever typed into a
    genuinely-capped session either way. What CAN differ is the ping: a
    rebuilt entry reseeds `first_seen`, which changes the alert's dedup key, so
    a wipe-and-rebuild can cost a second, otherwise-redundant ping — never a
    keystroke.)"""
    e = state.get(key)
    if e is None or e.get("hash") != err_hash:
        fs = int(first_seen_seed) if first_seen_seed is not None else int(now)
        entry = {"hash": err_hash, "first_seen": fs, "nudges": [], "escalated": False}
        if (now - fs) >= grace:           # already stuck >= grace → first continue now
            entry["nudges"] = [int(now)]
            return "nudge", entry
        return "wait", entry              # fresh → give Claude Code `grace` to recover
    if e.get("dormant"):
        return "wait", e                 # permanently held (usage cap) until a new hash
    nudges = list(e.get("nudges", []))
    last = nudges[-1] if nudges else e.get("first_seen", now)
    n = len(nudges)
    if n < max_nudges:
        needed = grace if not nudges else interval
    else:
        step = n - max_nudges + 1        # 1, 2, 3, ... widening back-off step
        needed = min(interval * (2 ** step), backoff_cap)
    if (now - last) < needed:
        return "wait", e
    e2 = dict(e)
    e2["nudges"] = nudges + [int(now)]
    if n >= max_nudges and not e.get("escalated"):
        e2["escalated"] = True           # one-shot: caller fires the give-up ping now
    return "nudge", e2


# --- Stuck-check sensitivity (2026-07-20, codex-bridge drilling incident) ----
# A session honestly waiting on a SCHEDULED event ("čakám na 14:15 auto-sync")
# got nudged every cycle until pressured into premature work. Two valves:
# declared_wait_until() (respect an explicit future clock in the ⏳ marker) and
# the responded-backoff in decide_working (answered nudges space out
# exponentially and never escalate — escalation is for a DEAD process only).
DECLARED_WAIT_GRACE_S = 20 * 60      # nudge only this long AFTER the declared time
DECLARED_WAIT_MAX_S = 12 * 3600      # a "future" time further than this is noise
# (#352) A session that keeps ANSWERING the self-check nudge (genuinely alive,
# still legitimately waiting) is re-checked on a widening, EXPLICIT schedule
# rather than an unbounded-feeling exponential — the user's own concrete ask
# after a live incident of hourly re-checks each burning a full context turn
# purely to re-prove liveness: 1h, then 3h, then 6h, holding at 6h for any
# further round. Never nudges MORE often than this even for a session that
# keeps answering forever, and — paired with `_pane_live_shell_evidence`
# above, which skips the check ENTIRELY while the pane already shows proof
# of life — this schedule is now the fallback for the case that check can't
# see (a shell alive but not pane-visible), not the primary defense.
WORKING_RESPONDED_BACKOFF_SCHEDULE_S = (3600, 3 * 3600, 6 * 3600)
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
                   backoff_schedule=WORKING_RESPONDED_BACKOFF_SCHEDULE_S):
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
        # Space repeats out on the EXPLICIT staged schedule (#352: 1h → 3h →
        # 6h, holding at the last step) and never let answered checks count
        # toward the 'wedged' escalation (the drilling incident: 3 answered
        # nudges fired a false wedged ping and the session got pressured into
        # premature work).
        answered = int(e.get("answered", 0)) + 1
        e["answered"] = answered
        e["noresp"] = 0
        step = min(answered - 1, len(backoff_schedule) - 1)
        gap_needed = backoff_schedule[step]
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
    run = run or _default_run
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
    run = run or _default_run
    run(["kill", "-USR1", str(pid)])


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
    genuinely side-effect-free through every caller that threads it here."""
    run = run or _default_run
    query = ["tmux", "list-panes", "-a", "-F",
             "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"
             "\t#{pane_pid}"]
    out = run(query)
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
                out = run(query)
                if (out or "").strip():
                    if logs is not None:
                        logs.append("tmux-socket-recovered")
                    recovered = True
                    break
            if not recovered and logs is not None:
                logs.append("tmux-socket-recovery-failed server-pids=%s"
                            % ",".join(str(p) for p in pids))
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
    if not transcript_last_error(sub_path):
        return False                     # doesn't even end on an api-error
    for entry in _iter_jsonl_tail(sub_path, max_lines=500):
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
                          interval, max_nudges, dry_run):
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
    # (#287) A worker whose own transcript is provably unsalvageable earns
    # AT MOST ONE typed nudge, never the full retry cycle — see
    # _subagent_transcript_unsalvageable's own docstring.
    unsalvageable = _subagent_transcript_unsalvageable(sub_path)
    effective_max_nudges = 1 if unsalvageable else max_nudges
    action, entry = decide_working(state, wkey, now, sub_idle,
                                   interval=interval, max_nudges=effective_max_nudges)
    state[wkey] = entry
    if action == "nudge":
        n = len(entry["nudges"])
        logs.append("subagent-%s-nudge#%d %s [%s]" % (dedup_prefix, n, project, wid))
        if not dry_run:
            send_subagent_nudge(pid, wid, kind, run)
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


_CONTROL_CHAR_RX = re.compile(r"[\x00-\x1f\x7f\x80-\x9f]")


def clean_reply_text(raw, bot_id=""):
    """Turn a Discord reply's raw content into a single-line prompt safe to type.

    Strips @mention tokens (`<@id>` / `<@!id>` / `<@&role>` — a reply that pings
    the bot must not type the ping), strips C0/C1 control characters (THEORETICAL-13,
    #297/#298 review — `\\s+` alone leaves ESC/BEL/NUL etc. untouched, and this text
    is later typed via `send-keys -l` into a real pane; a raw ESC byte risks the
    terminal reading it as the start of an escape sequence rather than literal
    text), collapses ALL whitespace (incl. newlines — a stray newline would submit
    the prompt early / split it), and caps the length. Returns "" when nothing
    usable remains (→ the caller ignores the reply)."""
    if not raw:
        return ""
    s = _MENTION_TOKEN_RX.sub(" ", str(raw))
    if bot_id:
        s = s.replace("<@%s>" % bot_id, " ").replace("<@!%s>" % bot_id, " ")
    s = _CONTROL_CHAR_RX.sub(" ", s)
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
    why callers match with endswith().

    That contract was promised here and never delivered (#193): the function
    required the BOUNDARY row to start with `❯`, which only a one-row box
    ever does, so a wrapped draft returned None — indistinguishable from a
    running turn. The box is now located by its HEAD row and the tail is
    returned as documented. `''` still means exactly "bare box": blank rows
    are filtered out upstream, so a wrapped box can never return it."""
    box = _find_input_box(captured)
    if box is None:
        return None
    head, tail, wrapped = box
    return tail if wrapped else head[1:].strip()


def _classify_boundary(captured):
    """Classify the pane's input-box boundary for the keystroke-sending jobs
    (1/9/14/20; jobs 12/15 that once used this are REMOVED, #132/#102) —
    splits `_input_line_text`'s collapsed-to-None result into
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

    A WRAPPED draft is an "input" with its TAIL as the draft text (#193) — it
    used to land in the "busy" bucket, which is how every keystroke-sending
    job came to treat a pane holding a medium-length draft as a running turn.
    """
    rows = _input_box_rows_raw(captured)
    box = _find_input_box_from(rows)
    if box is not None:
        head, tail, wrapped = box
        return ("input", tail if wrapped else head[1:].strip())
    return ("busy", None) if rows else ("no-input-line", None)


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
    generic sudo-hosted stream shape (a claude pane launched via `sudo su -
    <user>` inside a DIFFERENT user's tmux, so that user's own watchdog has
    no tmux server to see it from). Historical worked example: montalu ran
    this way inside newlevel's tmux on dev1 until the 2026-07-24 subdev
    migration (#33) gave it its own tmux — no live pane currently matches
    (#34). Kept generic + tested for the next shared-tmux stream that needs
    it, not deleted. None for the current user's own paths and non-home
    paths."""
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
    "<task-notification>", "<local-command", "<command-", "<system-reminder",
    # #339 adversarial-review MINOR: a Stop-hook-rejected turn is ALSO
    # machine-injected, not human-typed -- this repo's own
    # `_STOP_FEEDBACK_PREFIX` (defined ~5000 lines below, next to job 20's
    # compact-boundary code) carries the identical literal; duplicated
    # here rather than reordering that far-away constant, and must be kept
    # in sync if it ever changes. Without this entry, a Stop-hook rejection
    # (a routine, real transcript entry -- #108/#109 measured thousands of
    # them corpus-wide) counted as a genuine human prompt and could delay
    # a genuinely-headless virgin arm by up to GOAL_AUTOARM_RECENT_HUMAN_S.
    "Stop hook feedback:")


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


_REAL_TURN_TYPES = ("user", "assistant")


def _last_real_turn_ts(tpath, tail_bytes=2_000_000):
    """Epoch of the newest transcript entry whose top-level `type` is
    `user` or `assistant` — a genuine conversational turn, never a
    bookkeeping write. Job 10's own idle-clock fix, watchdog issue #177:
    Claude Code appends several NON-TURN entry types to the SAME transcript
    file (`mode`, `permission-mode`, `bridge-session`, `pr-link`,
    `last-prompt`, and even a bare mtime touch with no new line at all —
    all live-observed on the incident this fixes) that bump the transcript
    FILE's own mtime with ZERO real progress. A consumer reading that raw
    mtime as "how long has this session been idle" — `prompt_wedge_check`'s
    own `now - tmtime < PWEDGE_MIN_IDLE_S` gate is exactly this — never
    accumulates toward its threshold and never fires: live-reproduced, the
    transcript's own LINE COUNT was identical across two checks minutes
    apart while the file's mtime kept moving anyway.

    Filtering to `user`/`assistant` mirrors `_last_human_prompt_ts`'s own
    top-level-content discipline (never a nested `tool_result` quoting
    ANOTHER session's transcript), but deliberately does NOT additionally
    require human-typed content: an assistant turn, a machine-typed prompt,
    or a tool_result-carrying `user` entry are all genuine session activity
    for THIS purpose (job 10 asks "has anything real happened", not "did
    the human type something" — `_last_human_prompt_ts` exists for the
    latter, a different question with a different caller). `user`/
    `assistant` is the closed, structurally-guaranteed pair every other
    turn-boundary reader in this file already keys on (`transcript_last_
    marker`, `scan_goal_markers`) — never widened to an open-ended allow-
    list of "meaningful" bookkeeping types, which would need to track every
    new entry kind CC's transcript format ever grows.

    Returns None on any read failure or an entirely-untyped tail — never
    asserts staleness from an unmeasurable read; the caller falls back to
    the raw file mtime it already has (never worse than the pre-#177
    behavior)."""
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
        if not isinstance(e, dict) or e.get("type") not in _REAL_TURN_TYPES:
            continue
        try:
            ep = datetime.fromisoformat(
                str(e.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if best is None or ep > best:
            best = ep
    return best


_LOCAL_COMMAND_BOOKKEEPING_PREFIXES = ("<local-command-caveat>",
                                       "<local-command-stdout>")


def _last_real_turn_ts_excluding_command_bookkeeping(tpath, tail_bytes=None):
    """#335-review F1, ROUND 2 (fresh-context adversarial review, executed
    proof over the real transcript corpus): a genuine `/exit` is NOT one
    transcript entry — it is a TRIPLE, all `type=="user"`: a
    `<local-command-caveat>` entry, the `<command-name>/exit</command-name>`
    entry `_goal_user_exit_ts` matches, and a
    `<local-command-stdout>Bye!</local-command-stdout>` entry. The caveat
    and stdout companions routinely carry a timestamp a few MILLISECONDS
    NEWER than the exit command entry itself (same command batch, written
    microseconds apart) — so `_last_real_turn_ts` (which deliberately
    counts ANY real turn, by design, for job 10's own #177 purpose) reads
    the exit's own bookkeeping as "activity postdating the exit" and
    releases `_goal_was_cleared_by_user`'s round-1 suppression the instant
    it is written. Measured against the real corpus: 13 of 15
    genuinely-exited-and-never-resumed sessions read as released through
    this exact defect — the protection was a millisecond-timestamp-
    coincidence lottery, not a real release condition.

    This is a SEPARATE reader, never a change to `_last_real_turn_ts`
    itself — job 10 genuinely wants those companion entries counted as
    activity for ITS OWN, different purpose (idle-clock staleness), and
    widening the shared function would silently change job 10's own
    behaviour for a reason job 10 has nothing to do with.

    Same read-window contract as `_last_real_turn_ts` (mirrors it exactly,
    including the tail-bootstrap default), so any caller pays the
    identical cost profile. A REAL user turn (a fresh chat message, an
    assistant reply, a genuinely different slash command) still releases
    the suppression normally — only the two named bookkeeping prefixes are
    excluded, never anything else."""
    if tail_bytes is None:
        tail_bytes = 2_000_000
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
        if not isinstance(e, dict) or e.get("type") not in _REAL_TURN_TYPES:
            continue
        msg = e.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if (isinstance(content, str)
                and content.startswith(_LOCAL_COMMAND_BOOKKEEPING_PREFIXES)):
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


# #322 — CC 2.1.226 introduced a SECOND, DIFFERENT paste-collapse shape: a
# single long literal `send-keys -l` burst renders as `paste again to
# expand` rather than `[Pasted text #N]` — an INCOMPLETE/unexpanded paste
# that is never parsed as a slash command. Pressing Enter on it submits
# whatever content IS committed as an ordinary chat message instead of
# arming the goal — the exact live incident (dev1's own job 20, and
# montalu2@subdev, both CC 2.1.226): the delivery "succeeds" with no error,
# the model receives the `/goal ...` text as a normal prompt, and the goal
# never arms. A controlled live experiment DISPROVED the earlier "busy
# pane" hypothesis directly — a 23-char payload armed fine on a BUSY pane,
# while a 3960-char one failed on an IDLE one; the only variable that
# discriminated pass/fail was LENGTH, and typing the SAME long payload in
# small chunks (instead of one burst) armed correctly every time.
GOAL_TYPE_CHUNK_THRESHOLD = 200      # below this, unchanged single-burst
                                     # send-keys (never observed to trigger
                                     # the collapse at this size)
GOAL_TYPE_CHUNK_SIZE = 120
GOAL_TYPE_CHUNK_DELAY_S = 0.12
_PASTE_EXPAND_HINT_RX = re.compile(r"paste again to expand", re.I)


def _pane_shows_collapsed_paste(itext):
    """True when the input box shows CC's 'paste again to expand' hint — an
    incomplete/unexpanded paste. `_type_literal`'s chunking exists
    specifically to avoid ever reaching this state; this check is a fast,
    explicit abort for the case something still does (a coalesced terminal
    write, a lower-than-expected collapse threshold), rather than relying
    on the generic type-verify timeout to eventually give up."""
    return bool(itext) and bool(_PASTE_EXPAND_HINT_RX.search(itext))


def _type_literal(pid, run, text, sleep_fn=None):
    """Send `text` into the pane's input box literally — in ONE burst below
    `GOAL_TYPE_CHUNK_THRESHOLD` chars (unchanged from before this ticket),
    or in small CHUNKS at/above it (#322) so CC never treats the whole
    payload as one terminal-paste event.

    #322 REOPENED (adversarial-review CRITICAL-1, both live-verified against
    a real tmux 3.7b pane): `tmux send-keys -l <chunk>` parses `<chunk>` as a
    getopt-style ARGUMENT — a chunk whose first character happens to be `-`
    (e.g. a 120-char slice landing mid-word on "...`-self` (holds..." in a
    real shipped template) is read as an unknown FLAG and the whole call
    fails (`command send-keys: unknown flag -X`, rc 1). `_default_run`
    silently swallows a non-zero-rc command as `""` (no exception, no log),
    so the chunk is DROPPED with zero indication — every later chunk still
    lands, so `_typed_landed`'s tail-based `endswith()` check is satisfied
    by the (now internally corrupted) remainder, and a GOAL WITH A
    120-CHARACTER HOLE IN THE MIDDLE gets armed. This is a materially WORSE
    outcome than the paste-collapse bug this function exists to fix (which
    armed nothing, rather than arming something silently wrong) — and the
    same `-l` call is on `deliver_with_stash`'s only type path, so an
    arbitrary Discord-reply `prompt` (job 7) can be mangled and SUBMITTED
    the identical way. `--` (end-of-options) makes every following argument
    literal regardless of a leading `-`; live-verified: `send-keys -l --
    '-DASH-OK'` lands the literal text, `send-keys -l '-DASH-OK'` (no `--`)
    fails exactly as above. Applied to BOTH the single-burst and the
    chunked path — a single-burst payload can equally start with `-` (job
    7's arbitrary `prompt` argument is not `/goal`-prefixed)."""
    sleep_fn = sleep_fn or time.sleep
    if len(text) < GOAL_TYPE_CHUNK_THRESHOLD:
        run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
        return
    for i in range(0, len(text), GOAL_TYPE_CHUNK_SIZE):
        run(["tmux", "send-keys", "-t", pid, "-l", "--",
            text[i:i + GOAL_TYPE_CHUNK_SIZE]])
        if i + GOAL_TYPE_CHUNK_SIZE < len(text):
            sleep_fn(GOAL_TYPE_CHUNK_DELAY_S)


STASH_VERIFY_SETTLE_POLLS = 3      # bounded: a `C-s` toggle's render can lag
STASH_VERIFY_SETTLE_S = 0.3        # behind the keystroke actually landing (#176 F4)

# The four honest outcomes of our own `C-s` toggle (#189). The mechanism used
# to collapse them into one boolean ("did the box go bare WITH the marker?")
# and treat everything else as a failed stash — which is how an EMPTY box
# became an error condition, and why zero `continue`s were delivered in 24h
# box-wide. Each outcome now names a genuinely different pane state and gets
# a genuinely different recovery.
STASH_PARKED = "parked"            # bare + marker lit: a real draft is in the slot
STASH_NOOP = "noop"                # bare, no marker: there was nothing to park
STASH_UNRESOLVED = "unresolved"    # still shows content, no marker: a rendered
                                   # ghost suggestion, or a genuinely lost
                                   # keystroke — DELIBERATELY not distinguished
STASH_NO_BOUNDARY = "no-boundary"  # the input line itself is gone (spinner /
                                   # dialog / unreadable): touch nothing more


def _await_stash_settled(pid, run, sleep_fn):
    """Poll (bounded) for the pane to settle after our OWN `C-s`, and report
    WHICH of the four states above it settled into.

    Still a render-SETTLE poll, never a blind timeout — it returns the instant
    the box agrees, because an IMMEDIATE re-capture can show the UNCHANGED
    screen even though the toggle already landed server-side (#176 F4).

    What changed in #189 is the question being asked. The old poll demanded
    `input == "" AND marker lit` and called anything else a failure. But a
    bare box with NO marker is not a failure at all: it means there was
    nothing to park, and stashing nothing is a no-op. Since Claude Code's grey
    prompt suggestion renders as ordinary text once `capture-pane -p` strips
    its SGR attributes, "the box looks non-empty" was never evidence that a
    draft existed — so the poll reports what it can actually see and lets the
    caller act on each state, rather than folding three deliverable states
    into one abort. Returns (last_capture, outcome, boundary_text) where
    boundary_text is the input line as it stands at the end (None when there
    is no input line at all)."""
    cap = None
    for i in range(STASH_VERIFY_SETTLE_POLLS):
        cap = capture_pane(pid, run, lines=30)
        if _input_line_text(cap) == "":
            return cap, (STASH_PARKED if STASH_MARKER in (cap or "")
                         else STASH_NOOP), ""
        if i < STASH_VERIFY_SETTLE_POLLS - 1:
            sleep_fn(STASH_VERIFY_SETTLE_S)
    itext = _input_line_text(cap)
    if itext is None:
        return cap, STASH_NO_BOUNDARY, None
    return cap, STASH_UNRESOLVED, itext


def _typed_exclusively(text, itext):
    """True only if the input box holds NOTHING BUT `text` — the STRICT form of
    `_typed_landed`, required when we typed into a box that visibly held
    something (#189).

    `_typed_landed` accepts a TAIL of `text` on the boundary line, because a
    wrapped input renders its tail there. That allowance is safe on a box we
    verified bare first, but not on a box that already showed content: if the
    content was a real draft our text APPENDS to it, and a wrap landing inside
    our own text would leave a boundary that is a legitimate suffix of `text`
    — passing the loose check and submitting the user's draft with our text
    glued on. Requiring an exact match (or Claude Code's collapsed
    `[Pasted text #N]` placeholder for a long payload) has no such hole: a
    ghost suggestion is REPLACED by the keystroke, so the box holds exactly
    our text, while a real draft never can."""
    if not itext:
        return False
    if itext == text:
        return True
    return bool(_PASTED_PLACEHOLDER_RX.match(itext.strip()))


# Bounds how many backspaces an undo may ever spray at a pane. It sat at 400,
# BELOW a real payload — the gk-request nudge is 458 chars — so the recovery
# #193 added for a box verified bare could not have run for the very delivery
# that stranded its text. Sized off the largest payload this helper actually
# carries: job 20 delivers `"/goal " + <condition>`, and Claude Code caps that
# condition at 4000 (#169), so 4096 covers the whole line with headroom rather
# than refusing it by six characters.
STASH_UNDO_MAX_BACKSPACES = 4096


def _undo_appended_text(pid, run, pre_text, text):
    """Remove exactly the characters WE typed from a box that already held
    `pre_text`, and VERIFY the box came back to `pre_text` (#189).

    Reached only when nothing was parked and the box shows the exact append
    signature `pre_text + text` — i.e. our own keystroke landed on top of a
    real draft because the stash toggle was lost. A restoring `C-s` would be
    the WRONG recovery here: with an empty slot it would PARK the polluted
    text, jamming the single slot into the one decline this design keeps and
    leaving the user's draft invisible. Backspacing our own characters is the
    only recovery that touches nothing of the user's. Returns whether the
    draft verifiably came back."""
    if not text or len(text) > STASH_UNDO_MAX_BACKSPACES:
        return False
    run(["tmux", "send-keys", "-t", pid] + ["BSpace"] * len(text))
    return _input_line_text(capture_pane(pid, run, lines=30)) == pre_text


def _undo_typed_text(pid, run, text):
    """Remove exactly the characters WE typed from a box that was VERIFIED
    BARE immediately before we typed, and confirm it came back to bare (#193).

    Reached only from the PARKED / NOOP outcomes, where the settle poll had
    already read `_input_line_text(cap) == ""`. Every character in the box is
    therefore ours, so backspacing exactly `len(text)` provably cannot reach
    anything of the user's — whatever fraction of the type actually landed, a
    surplus backspace lands on an empty box. That proof is a property of the
    BOX (observed bare by this function's own caller a moment earlier), never
    of who called or of what they passed.

    Note it can only run at all when the verify FAILED: a payload Claude Code
    collapsed into `[Pasted text #N]` verifies through that placeholder and is
    submitted, so this never has to reason about a collapsed buffer."""
    if not text or len(text) > STASH_UNDO_MAX_BACKSPACES:
        return False
    run(["tmux", "send-keys", "-t", pid] + ["BSpace"] * len(text))
    return _input_line_text(capture_pane(pid, run, lines=30)) == ""


def draft_rescue_dir():
    """`~/.claude/draft-rescue/`, resolved at CALL time (same reasoning as
    `compact_claims_path()`: never a frozen module-level constant, so a
    relocated `$HOME`/override is honoured on every call, not just the one
    that happened to run at import time).

    `AIRULESET_DRAFT_RESCUE_DIR`, when set, overrides the default. This is
    NOT a security boundary (there is nothing to bypass here — see the
    vault-store env-override lesson in the playbook, which is about a
    guard an attacker could defeat; this is a plain write-only, self-pruning
    diagnostic directory nothing else reads or acts on). It exists so
    `airuleset.py`'s own push-gate test-suite subprocess (`cmd_push`) can
    point the WHOLE `unittest discover` run at a throwaway directory in one
    place, instead of adding per-file test isolation to every one of the
    ~19 test files whose fixtures transitively reach `deliver_with_stash`/
    `_send_goal_verified` via `run_once` (#271) — the live systemd watchdog
    executes this repo's own working tree every 60s, so a test process that
    wrote into the REAL directory would be indistinguishable from production
    activity. A single test wanting a precise, deterministic rescue-file
    assertion still patches this function directly
    (`unittest.mock.patch.object(wd, "draft_rescue_dir", return_value=<tmp>)`
    — the same established shape `compact_claims_path()` already uses)."""
    override = os.environ.get("AIRULESET_DRAFT_RESCUE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "draft-rescue"


# 14 days: deliberately generous (#271). This file exists ONLY so a human can
# recover text nothing else could save — the cost of keeping one around too
# long is a few KB, the cost of pruning too eagerly is the exact
# unrecoverable loss this mechanism exists to prevent. There is no "restore
# confirmed, safe to delete now" signal available to this process (the
# restore is Claude Code's own async, on-screen-only effect, fired once the
# DELIVERED turn completes — minutes away, and never observed here), so age
# is the only thing that ever removes a rescue file.
DRAFT_RESCUE_TTL_S = 14 * 24 * 3600

# `<safe-pid>-<epoch-ms, >=10 digits>[-<retry-n>].txt` — the EXACT shape
# `_draft_rescue_persist` writes. `_draft_rescue_prune` matches against this
# (adversarial-review MAJOR finding, #271) rather than unlinking every name
# in the directory: a misconfigured `AIRULESET_DRAFT_RESCUE_DIR` pointed at
# an already-populated directory must never let a routine prune delete
# unrelated content it happens to share a mtime-age with.
_DRAFT_RESCUE_NAME_RX = re.compile(r"^.+-\d{10,}(?:-\d+)?\.txt$")


def _draft_rescue_text(captured):
    """Best-effort plain-text reconstruction of the pane's CURRENT input-box
    content — "" when the box is bare or unlocatable (nothing to rescue).

    Never the exact original bytes: a WRAPPED draft loses its true line
    breaks the instant Claude Code re-flows it (#189) — this is the
    RENDERED rows (`_input_box_rows_raw`, already head-first and chrome-
    stripped) joined back together, with the leading `❯` glyph + its
    separator stripped off the head row only. Enough for a human to read
    and retype, which is the whole point of a rescue file.

    `_input_box_rows_raw`'s own glyph-based fallback (a borderless capture)
    deliberately returns exactly ONE row and never more — so a borderless
    capture can never pull agent-strip/chrome text into a rescue file; only
    the STRUCTURAL (bordered) strategy can return multiple rows, and it
    returns strictly the box's own interior, already guarded (#243) against
    a transcript-quoted box being misread as the real one."""
    rows = _input_box_rows_raw(captured)
    if not rows:
        return ""
    head = _normalize_queued_hint(rows[0])
    if not _is_draft_head(head):
        return ""            # bare box ("❯" alone) — nothing to rescue
    first = head[1:].lstrip(" \xa0")
    body = [first] + list(rows[1:])
    return "\n".join(ln for ln in body if ln)


def _draft_rescue_prune(now, dir_path=None, ttl_s=None):
    """Remove rescue files older than the TTL. Best-effort (never raises) —
    called inline from EVERY write (`_draft_rescue_persist`), never a
    separate job: the repo FREEZE forbids a new numbered watchdog job, and
    there is no "confirmed delivered" event to hang a dedicated sweep off
    of anyway (see `DRAFT_RESCUE_TTL_S`'s own docstring) — an inline prune
    on every write is sufficient to keep the directory bounded.

    Only names matching `_DRAFT_RESCUE_NAME_RX` are ever candidates for
    deletion — never a bare "everything in this directory" sweep."""
    dir_path = dir_path or draft_rescue_dir()
    ttl_s = DRAFT_RESCUE_TTL_S if ttl_s is None else ttl_s
    try:
        names = os.listdir(dir_path)
    except OSError:
        return
    for name in names:
        if not _DRAFT_RESCUE_NAME_RX.match(name):
            continue
        p = Path(dir_path) / name
        try:
            # A matching-named SYMLINK is never one of our own writes (we
            # only ever create real files, via O_EXCL|O_NOFOLLOW) — remove
            # it unconditionally, on age or not. `Path.unlink()` removes the
            # link itself, never follows it, so this is safe either way.
            if os.path.islink(p) or now - p.stat().st_mtime > ttl_s:
                p.unlink()
        except OSError:
            continue


def _draft_rescue_ensure_dir(dir_path):
    """Create `dir_path` as an owner-only (0700) directory if missing, and
    refuse — creating nothing — if it is, or becomes, a symlink.

    Mirrors this repo's own vault-store discipline (checked BEFORE *and*
    AFTER `os.makedirs`, since that call happily succeeds against a symlink
    to an existing directory and a later `chmod` would then tighten the
    TARGET, not a real directory of our own) — these boxes host foreign
    uids by design, and the rescue directory holds arbitrary user-typed
    text that can include a pasted credential (adversarial-review CRITICAL
    finding, #271). Returns True only when `dir_path` is a genuine,
    owner-only directory ready to receive a write."""
    try:
        if os.path.islink(dir_path):
            return False
        os.makedirs(dir_path, mode=0o700, exist_ok=True)
        if os.path.islink(dir_path):
            return False
        os.chmod(dir_path, 0o700)
        return True
    except OSError:
        return False


# Bounds the filename-collision retry loop below — two rescue writes for the
# SAME pane landing in the same millisecond needs two full tmux round-trips
# inside 1ms, which is not realistic in production, but `O_EXCL` makes the
# collision loud (FileExistsError) instead of a silent O_TRUNC overwrite, so
# bound the retry rather than loop forever on a persistently occupied name.
_DRAFT_RESCUE_MAX_RETRIES = 5


def _draft_rescue_persist(pid, captured, now=None, dir_path=None, logs=None):
    """Persist the pane's CURRENT input-box content to disk BEFORE any
    keystroke of OURS lands in it (#271).

    `deliver_with_stash` parks a real draft via `C-s` and Claude Code
    auto-restores it once the delivered turn completes — but that restore is
    entirely on-screen and asynchronous; nothing in this process ever
    observes it landing. The reported incident (2026-08-06): a long typed
    message sat in the box, a goal-autoarm delivery ran, and the draft was
    gone with NO trace at all — CC's input box renders in-place via cursor
    addressing, so unsent text never even enters tmux scrollback (confirmed
    against the real incident capture, forensics comment on this issue).

    So every primitive that is about to touch a pane's box
    (`deliver_with_stash`, `_send_goal_verified`) calls this FIRST,
    unconditionally, with the SAME capture it is about to act on — before
    its own first `send-keys`. Stash+restore stays the PRIMARY recovery
    path; this file is the safety net and is NEVER deleted here on a claimed
    success (there is no verifiable "the restore actually landed" signal to
    delete on) — only `_draft_rescue_prune`'s generous TTL, run inline on
    every write, ever removes one. `send_continue` (job 1/4/7's short-nudge
    primitive) needs no equivalent: it only ever types into a box its own
    caller already verified bare, and on the rare swallowed-submit case a
    stuck draft is APPENDED to and later submitted or auto-recovered — never
    silently discarded the way an unrestored stash can be.

    The write is owner-only (0600, via `os.O_EXCL | os.O_NOFOLLOW` — never
    followed through a planted symlink, never silently overwritten by a
    filename collision) into an owner-only (0700) directory
    (`_draft_rescue_ensure_dir`) — the content can be arbitrary user-typed
    text, including a pasted credential (adversarial-review CRITICAL
    finding, #271), and these boxes host foreign uids by design.

    Returns the path written, or None (box was bare, the directory could not
    be secured, or the write failed after exhausting the collision-retry
    budget — best-effort; never blocks the delivery it is protecting, but
    now LOGS why whenever it fails with a real draft in hand, so a failure
    here is never as silent as the incident this whole mechanism exists to
    prevent)."""
    text = _draft_rescue_text(captured)
    if not text:
        return None
    now = time.time() if now is None else now
    dir_path = dir_path or draft_rescue_dir()
    if not _draft_rescue_ensure_dir(dir_path):
        if isinstance(logs, list):
            logs.append("draft-rescue: FAILED to secure %s (pane %s, %d "
                        "chars would have been lost with no trace)"
                        % (dir_path, pid, len(text)))
        return None
    _draft_rescue_prune(now, dir_path=dir_path)
    safe_pid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(pid)).strip("_") or "pane"
    base = "%s-%d" % (safe_pid, int(now * 1000))
    for attempt in range(_DRAFT_RESCUE_MAX_RETRIES):
        suffix = "" if attempt == 0 else "-%d" % (attempt + 1)
        path = Path(dir_path) / (base + suffix + ".txt")
        try:
            fd = os.open(str(path),
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600)
        except FileExistsError:
            continue                       # a genuine name collision — retry
        except OSError as exc:
            if isinstance(logs, list):
                logs.append("draft-rescue: FAILED to open %s (pane %s, %d "
                            "chars would have been lost with no trace) -> %r"
                            % (path, pid, len(text), exc))
            return None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            if isinstance(logs, list):
                logs.append("draft-rescue: FAILED to write %s (pane %s, %d "
                            "chars would have been lost with no trace) -> %r"
                            % (path, pid, len(text), exc))
            return None
        if isinstance(logs, list):
            logs.append("draft-rescue: saved %d chars (pane %s) -> %s"
                        % (len(text), pid, path))
        return str(path)
    if isinstance(logs, list):
        logs.append("draft-rescue: FAILED for pane %s -> %d consecutive "
                    "filename collisions, %d chars would have been lost "
                    "with no trace" % (pid, _DRAFT_RESCUE_MAX_RETRIES,
                                       len(text)))
    return None


def _undo_and_release_slot(pid, run, text, parked, log_fn, prefix):
    """Backspace exactly `text` and, only once bare is CONFIRMED, pop a
    PARKED draft back with one corrective `C-s` -- the shared recovery
    THREE call sites need (`deliver_with_stash`'s original #193
    PARKED/NOOP verify-failure branch and its new #306
    swallowed-submit-not-recovered branch, plus `_send_goal_verified`'s own
    #306 swallowed-submit path, always with `parked=False`).

    The precondition every caller has already proven before reaching here
    -- the box's ENTIRE content is, by construction, exactly `text` and
    nothing else -- holds for a DIFFERENT reason depending on the caller:
    a settle poll verified the box BARE immediately before typing (the
    PARKED/NOOP outcome, and `_send_goal_verified`'s own bare-box-only
    entry gate), or `_typed_exclusively` already proved the box holds ONLY
    `text` with nothing of a foreign draft's (the UNRESOLVED-exclusive
    outcome -- `parked` is structurally False on that path, since only a
    bare-box settle can ever set `STASH_PARKED`, so the dangerous `C-s`
    pop below can never fire there; only the safe backspace half runs).
    Either way, `_undo_typed_text` backspaces exactly `len(text)` and
    itself re-verifies the box came back to bare before this function ever
    considers popping anything.

    `log_fn(reason)` receives exactly ONE reason string, built from
    `prefix` (e.g. `"stash-abort"` for the original call site, so the
    result is byte-identical to the pre-#306 wording; a fuller phrase like
    `"stash-abort: swallowed-submit-not-recovered"` for the new ones)."""
    undone = _undo_typed_text(pid, run, text)
    if not parked:
        log_fn("%s: typed-undone" % prefix if undone
               else "%s: typed-NOT-undone" % prefix)
        return
    if not undone:
        log_fn("%s: typed-NOT-undone, draft left parked" % prefix)
        return
    # ONLY now, with the box confirmed bare again, may the toggle pop the
    # parked draft back. Firing it while our own text was still in the box
    # would PARK that text instead — the slot is single with a SILENT
    # overwrite — destroying the very draft this protocol exists to
    # protect (#193).
    run(["tmux", "send-keys", "-t", pid, "C-s"])
    # Read the pop back rather than asserting it: a log line that claims a
    # delivery it never checked is the #134 mistake.
    log_fn("%s: typed-undone, parked draft popped back" % prefix
           if _input_line_text(capture_pane(pid, run, lines=30))
           else "%s: typed-undone, pop UNVERIFIED (draft still parked)"
                % prefix)


def deliver_with_stash(pid, text, run, captured=None, logs=None, sleep_fn=None):
    """Deliver `text` into an IDLE pane, parking whatever the input box holds.

    #189 — STASH UNCONDITIONALLY. This helper used to require a NON-EMPTY
    draft before it would act, and then required the box to go bare WITH the
    `› stashed` marker before it would type. Both conditions are answers to
    the same question — "is there really something in the prompt?" — and that
    question is UNANSWERABLE from what we can see: `capture_pane` shells
    `tmux capture-pane -p` with no `-e`, so Claude Code's dim (SGR 246) prompt
    suggestion arrives stripped of its colour and is byte-identical to text
    the user typed. Every classifier in this file therefore reported drafts
    that did not exist; the stash then had nothing to park, no marker could
    light, and the verify failed forever. Measured cost: `stash-delivered = 0`
    across the whole fleet in 24h, with a 529 sitting unattended on gatekeeper
    until the user typed `continue` by hand.

    So we stop computing the answer. Park first, look after, and let each
    OBSERVED outcome choose its own recovery:

      1. Abort if the stash slot is ALREADY occupied (`› stashed` anywhere in
         the capture). This is the ONE genuine decline that remains: the slot
         is single and overwrites silently, so stashing over it would destroy
         a parked draft with no way back.
      2. Require only that the pane is idle at an input boundary — a free `❯`
         (bare or holding anything at all) and no live-turn signal
         ('esc to interrupt'). Emptiness is explicitly NOT a precondition.
      3. If the agent-strip selector holds focus (`_strip_selected`, #36),
         ONE Escape first — never two (a rapid double-Escape PERMANENTLY
         DELETES a draft, empirically confirmed).
      4. Ctrl+S. Then a bounded render-SETTLE poll (never a blind timeout)
         reports which of four states the pane is in: PARKED (bare + marker —
         a real draft is in the slot), NOOP (bare, no marker — there was
         nothing to park, which is a fine outcome and not an error),
         UNRESOLVED (still shows content, no marker — a rendered ghost, or a
         genuinely lost keystroke, deliberately NOT distinguished), or
         NO_BOUNDARY (the input line is gone — abort, touch nothing else).
      5. Type `text` literally and verify. Typing is itself the discriminator
         the pane refuses to give us: a ghost suggestion is REPLACED by the
         keystroke, while a real draft is APPENDED to. After PARKED/NOOP the
         box was verified bare, so the usual tail-tolerant `_typed_landed`
         applies; after UNRESOLVED the box held something, so the STRICT
         `_typed_exclusively` is required — otherwise a wrap landing inside
         our own text could pass an append off as a clean type.
      6. Verify failure recovers by what actually happened, never blindly, and
         never leaves our own text behind (#193). PARKED/NOOP → the box was
         VERIFIED BARE a moment ago, so every character in it is ours:
         `_undo_typed_text` backspaces exactly what we typed and confirms the
         box is bare again. Only THEN, and only when PARKED, does one Ctrl+S
         pop the draft back — firing it while our text still sat in the box
         would PARK that text over the user's parked draft, since the slot is
         single and overwrites silently. UNRESOLVED → nothing is parked, so a
         Ctrl+S would jam the slot into step 1's decline; `_undo_appended_text`
         backspaces once the box provably ends with our own characters. When
         it does not — a truncated type — no FURTHER keystrokes are sent and
         the log says plainly that the payload we already typed is still in
         the box, because "is there a draft I would destroy?" resolves an
         unknown to YES. Typing into an already-WRAPPED unresolved box is
         refused back at step 4, while refusing is still free.
      7. Enter submits. If the text is STILL at the boundary (a swallowed
         Enter — the agent-strip-selector class of bug, #36), ONE corrective
         Escape+Enter (never a second bare Enter, never two Escapes).
      8. Success = the box no longer shows our text. A parked draft
         auto-restores itself once the delivered turn completes.

    `logs`, if a list, gets one reason string appended on every abort/success
    path — callers that want visibility (or tests) pass one in; the default
    (None) is silent, matching every other keystroke helper in this file.
    `sleep_fn` backs step 4's settle poll (default `time.sleep`; tests inject
    a no-op)."""
    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    run = run or _default_run
    sleep_fn = sleep_fn or time.sleep
    cap = captured if captured is not None else capture_pane(pid, run, lines=30)
    if cap and STASH_MARKER in cap:
        _log("stash-abort: slot occupied")
        return False
    if not _has_free_prompt(cap, bare_only=False):
        _log("stash-abort: no free prompt")
        return False
    if "esc to interrupt" in (cap or ""):
        _log("stash-abort: live turn")
        return False
    # #271: from here on we are genuinely about to act on this box — persist
    # whatever it currently holds BEFORE the first keystroke of ours lands,
    # so a stash whose eventual on-screen restore silently fails still has a
    # recoverable trace. A no-op (returns None, writes nothing) whenever the
    # box is bare, which is the common case. Honesty note (adversarial
    # review, #271): `cap` is the CALLER's own capture, which for some
    # callers (job 7's Discord reply, job 20's silent-death re-arm) can be a
    # few tmux round-trips stale by the time the actual `C-s` fires a couple
    # of lines below — the rescued text is therefore a best-effort SNAPSHOT
    # near the moment of action, not a byte-exact guarantee of what the box
    # holds at the literal instant `C-s` lands.
    _draft_rescue_persist(pid, cap, logs=logs)
    if _strip_selected(cap):
        run(["tmux", "send-keys", "-t", pid, "Escape"])
    run(["tmux", "send-keys", "-t", pid, "C-s"])
    cap, outcome, pre_text = _await_stash_settled(pid, run, sleep_fn)
    if outcome == STASH_NO_BOUNDARY:
        # The input line vanished between the toggle and the settle window (a
        # turn started, a dialog opened). We already sent one keystroke, so
        # this is not a free pre-send refusal — but sending anything MORE into
        # a pane we can no longer read is exactly the #233 scar. Stop here.
        _log("stash-abort: input-line-vanished")
        return False
    if outcome == STASH_UNRESOLVED and _box_is_wrapped(cap):
        # The box still shows content our toggle did not park, AND it is
        # already multi-row. Anything we append re-flows it, so neither
        # `_typed_exclusively` (which needs the box to hold our text and
        # nothing else) nor `_undo_appended_text` (which needs the exact
        # `pre + text` signature) could ever succeed afterwards — we would be
        # typing into a box we can neither verify nor undo, on top of what may
        # be a real draft. Refuse while it is still free to refuse (#193).
        # This shape was unreachable before wrapped boxes became readable, so
        # refusing restores exactly the guarantee that reading them removed.
        _log("stash-abort: unresolved wrapped box")
        return False
    parked = outcome == STASH_PARKED
    _type_literal(pid, run, text, sleep_fn)
    cap = capture_pane(pid, run, lines=30)
    itext = _input_line_text(cap)
    if _pane_shows_collapsed_paste(itext):
        # #322 — CC's "paste again to expand" is a DIFFERENT collapse shape
        # than `[Pasted text #N]` (which `_typed_landed` already treats as
        # landed) — it is never parsed as a slash command, so submitting it
        # would send whatever IS committed as an ordinary chat message.
        # `_undo_typed_text`'s OWN docstring states its backspace-N-times
        # proof depends on the box never being a collapsed buffer ("this
        # never has to reason about a collapsed buffer") — that
        # precondition does NOT hold for this shape, so routing it through
        # the existing recovery below would send an unproven backspace
        # count against a state this function was never designed to undo.
        # No further keystrokes; the box is left exactly as CC rendered it.
        # Known, deliberate residual: a PARKED draft stays in the single
        # stash slot until a LATER sweep's own "slot occupied" check
        # surfaces it — rare in practice, since `_type_literal`'s chunking
        # exists specifically to avoid ever reaching this state.
        _log("stash-abort: collapsed-paste")
        return False
    landed = (_typed_exclusively(text, itext) if pre_text
              else _typed_landed(text, itext))
    if not landed:
        _log("stash-abort: type-verify-failed")
        if not pre_text:
            # PARKED or NOOP — the settle poll VERIFIED this box bare a moment
            # ago, so every character in it is ours and backspacing exactly
            # what we typed can reach nothing of the user's.
            _undo_and_release_slot(pid, run, text, parked, _log, "stash-abort")
        elif itext == pre_text + text or _typed_landed(text, itext):
            # We only ever type into a SINGLE-ROW unresolved box — the wrapped
            # shape is refused above — so `pre_text` is that box's COMPLETE
            # content, and either signature proves our characters sit at its
            # END: the exact `pre + text` when nothing re-flowed, or
            # `_typed_landed` when our own payload wrapped the box. Backspacing
            # exactly what we typed restores it, and `_undo_appended_text`
            # verifies that byte-for-byte before reporting success.
            _log("stash-abort: append-undone" if
                 _undo_appended_text(pid, run, pre_text, text)
                 else "stash-abort: append-NOT-undone")
        else:
            # The box does not end with our text at all — a truncated type, or
            # something else moved it. Backspacing an unproven buffer could eat
            # a real draft, and "is there a draft I would destroy?" resolves an
            # unknown to YES. No FURTHER keystrokes; the payload we already
            # typed stays where it is, said plainly rather than glossed.
            _log("stash-abort: append-unprovable, typed text left in box")
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
            # #306 — delivery is genuinely dead here, but by construction the
            # box's ENTIRE content is exactly `text` (nothing else could be
            # in it: PARKED/NOOP means it was verified bare before we typed;
            # UNRESOLVED-exclusive means `_typed_exclusively` already proved
            # the box holds ONLY `text`) — the same precondition the
            # verify-failure recovery above already relies on. Recover the
            # SAME way: backspace our own text and, if we parked something
            # in THIS call, pop it back — never leave the pane stuck with
            # our own garbled text AND the single stash slot silently
            # occupied forever (the live david@subdev ~2h wedge).
            _undo_and_release_slot(pid, run, text, parked, _log,
                                   "stash-abort: swallowed-submit-not-recovered")
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
    typed[pid] = {"head": prompt[:160], "tail": prompt[-160:], "ts": now}
    state["dreply_typed"] = typed


def _is_dreply_machine_text(state, pid, head_txt, txt):
    """True if the pane's input box holds text job 7 typed into `pid` — so job
    10 auto-submits our OWN swallowed delivery instead of pinging the user
    about it. `head_txt` is the box's HEAD row, `txt` its tail.

    BOTH ENDS must agree (#193). The recorded value is a 160-char tail and a
    WRAPPED box's boundary row can be a single character, so the original bare
    `txt in tail` substring test was satisfied by very nearly any draft the
    moment wrapped boxes became readable — which would have let job 10
    classify a genuine USER draft as MACHINE, skip its at-rest guards, and
    auto-submit it. That is the one thing job 10's own docstring forbids.

    An entry recorded before this change carries no `head` and therefore never
    matches: job 10 falls back to PINGING about it rather than typing, which
    is the safe direction, and the 48h TTL retires such entries anyway."""
    rec = (state.get("dreply_typed") or {}).get(pid)
    if not isinstance(rec, dict):
        return False
    tail = str(rec.get("tail") or "")
    head = str(rec.get("head") or "")
    if not tail or not head or not txt or not head_txt:
        return False
    if not (tail.endswith(txt) or txt in tail):
        return False
    return head.startswith(head_txt) or head_txt.startswith(head)


def _box_holds_our_own_text(captured, text):
    """True if the pane's input box holds OUR OWN `text` — a prior delivery
    whose submit was swallowed — and not merely something that happens to END
    the way `text` does (#193).

    Job 7 used `text.endswith(_input_line_text(captured))` alone. That was
    unreachable for a wrapped box (which read as None) and became lethal the
    moment wrapped boxes were readable: the boundary row of a wrapped draft
    can be a SINGLE character, and every reply prompt ends with the same fixed
    sentence, so a FOREIGN draft would satisfy it — authorising a bare `Enter`
    that submits the user's unsent text AND marking the Discord answer
    delivered although it was never typed.

    The box's HEAD row is the discriminator: our own stuck text starts where
    `text` starts. Both ends must agree."""
    box = _find_input_box(captured)
    if box is None:
        return False
    head, tail, wrapped = box
    if not wrapped:
        content = head[1:].strip()
        return bool(content) and text.endswith(content)
    head_txt = head[1:].strip()
    return bool(head_txt) and text.startswith(head_txt) and text.endswith(tail)


# --------------------------------------------------------------------------- #
# #297/#298 -- extending job 7's poll pass with two more Discord signals a
# session can react to: a REPLY on a completion CARD (#298 -- reopen the
# ticket with the remark) and a ❓/❔ REACTION on any TRACKED bot message
# (#297 -- ask the sending stream about it). Both reuse the SAME
# `fetch(ch, token)` poll loop and the SAME `known_owner_ids` security
# boundary the ❓-reply flow already established; neither is a new job (the
# FREEZE only permits extending job 7's existing mechanism).
# --------------------------------------------------------------------------- #

BOUNCE_REMARK_LABEL = "prio:bounce"


def _repo_live_pane(repo_name, cwd_by_sid, panes_by_sid, run=None):
    """(sid, pid, cwd) of a live `claude` pane whose cwd's repo NAME matches
    `repo_name` (bare, case-sensitive as `gh`/`repo_name_for` render it), or
    None. Resolved via `repo_name_for` (a single local `git remote get-url`)
    — the SAME repo identity job 24/25 already key on, never the directory
    basename. Shared by #297's flag-delivery fallback and #298's
    post-reopen nudge, so both features agree on what 'a live session of
    that repo' means."""
    from notify import repo_name_for
    target = str(repo_name or "").strip()
    if not target:
        return None
    for sid, cwd in cwd_by_sid.items():
        if sid not in panes_by_sid:
            continue
        if repo_name_for(cwd, run=run) == target:
            return sid, panes_by_sid[sid][0], cwd
    return None


def _nudge_repo_pane(pid, cwd, run, text, dry_run, projects_dir, logs=None):
    """Best-effort: type `text` into a live pane at TRUE REST — job 8's own
    at-rest discipline (`_safe_to_bounce_nudge`), reused verbatim so a
    genuinely mid-work pane is NEVER interrupted (never a new mechanism, the
    exact same check `bounce_backstop` already relies on). A pane holding a
    foreign draft is stashed around (issue #35); a busy/copy-mode/live-work
    pane gets NOTHING this sweep. Returns True when delivered (or, in
    dry-run, would have been)."""
    captured = capture_pane(pid, run)
    if pane_in_mode(pid, run):
        return False
    if not _safe_to_bounce_nudge(captured, cwd, projects_dir):
        return False
    if not pane_at_idle_prompt(captured):
        return _try_stash_nudge(pid, captured, text, run, dry_run, logs=logs)
    if not dry_run:
        send_continue(pid, text, run)
    return True


# --- #298: reply on a completion CARD -> reopen the ticket ----------------

def parse_discord_card_reply(msg, allowed_ids, cardmap):
    """Validate ONE Discord message as a reply to a TRACKED completion card.

    Returns {reply_id, referenced, repo, issue, text, channel} when `msg` is
    a reply BY an allowed owner TO a message id in `cardmap` with usable
    text; else None. Mirrors `parse_discord_reply`'s exact security shape
    (SAME `allowed_ids` boundary) — the only difference is WHICH map the
    referenced id is looked up in."""
    if not isinstance(msg, dict):
        return None
    reply_id = str(msg.get("id") or "").strip()
    author = msg.get("author") or {}
    author_id = str(author.get("id") or "").strip()
    ref = (msg.get("message_reference") or {}).get("message_id")
    ref = str(ref or "").strip()
    if not reply_id or not author_id or not ref:
        return None
    if author_id not in allowed_ids:             # SECURITY: only a known owner
        return None
    c = cardmap.get(ref)
    if not isinstance(c, dict):
        return None
    repo = str(c.get("repo") or "").strip()
    issue = c.get("issue")
    if not repo or issue is None:
        return None
    text = clean_reply_text(msg.get("content"))
    if not text:
        return None
    return {"reply_id": reply_id, "referenced": ref, "repo": repo,
            "issue": issue, "text": text, "channel": str(c.get("channel") or "")}


def _card_remark_comment_body(text):
    return ("**Pripomienka z Discordu k done karte:**\n\n%s\n\n"
            "(Ticket bol automaticky znovu otvorený — dokonči ho podľa "
            "tejto pripomienky.)" % text)


def compose_card_reopen_nudge(issue):
    return ("Discord pripomienka: ticket #%s bol znovu otvorený s "
            "pripomienkou od užívateľa (je v poslednom komentári na "
            "tickete) — prečítaj si ju a dokonči ticket podľa nej."
            % issue)


def _gh_call(argv, input_text=None, timeout=25):
    """One `gh` subprocess call. Returns (ok, stdout) — ok=False on any
    failure/exception, stdout="" then. Auth via `_gh_env()`, the SAME
    fallback the ticket-fallback comment (`_gh_comment`) already uses."""
    import subprocess
    try:
        r = subprocess.run(argv, env=_gh_env(), input=input_text,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "")
    except Exception:
        return False, ""


def _card_reopen_flow(repo, issue, remark_text, gh_fn=None):
    """The #298 durable hand-off on `repo`#`issue`: reopen (best-effort —
    `gh issue reopen` on an already-open issue is idempotent, so a SECOND
    reply on the same card never double-reopens), comment the user's remark
    VERBATIM via stdin (never shell-interpolated), and apply
    `prio:bounce` — creating the label first ONLY when it is genuinely
    absent from `repo` (a read-only `gh label list --search` check FIRST,
    `gh label create` never `--force`'d — the #191-established discipline
    against clobbering an existing label's own colour/description on a
    shared repo).

    Returns True only when the COMMENT (the durable record of the remark)
    succeeded — reopen/label are best-effort on top of that; a repo whose
    label-list read itself fails still gets the `--add-label` attempt (gh's
    own error there is harmless — never blocks the comment, which already
    landed).

    MINOR-11 (#297/#298 review): `prio:bounce` is applied on EVERY `repo`
    here, unconditional on `_repo_in_cross_stream_flow()` — deliberately,
    since a #298 card-reopen is a plain user priority marker on THIS one
    ticket, not a cross-stream gatekeeper<->sub-dev hand-off signal. On a
    repo NOT enrolled in `_CROSS_STREAM_REPOS` the label carries no
    automated meaning at all (job 8's `bounce_backstop` only ever queries
    enrolled repos, per its own established discipline against reading a
    bare `prio:bounce` on an unrelated repo as a protocol artifact) — it is
    just a normal, human-visible priority tag here."""
    call = gh_fn or _gh_call
    call(["gh", "issue", "reopen", str(issue), "-R", repo])
    ok, _out = call(["gh", "issue", "comment", str(issue), "-R", repo, "-F", "-"],
                    input_text=_card_remark_comment_body(remark_text))
    if not ok:
        return False
    list_ok, list_out = call(["gh", "label", "list", "-R", repo, "--search",
                              BOUNCE_REMARK_LABEL, "--json", "name"])
    have_label = False
    if list_ok:
        try:
            have_label = BOUNCE_REMARK_LABEL in [
                x.get("name") for x in json.loads(list_out or "[]")]
        except Exception:
            have_label = False
    if not have_label:
        call(["gh", "label", "create", BOUNCE_REMARK_LABEL, "-R", repo,
             "--color", "D93F0B", "--description",
             "Bounce lane — jumps the autopilot queue, seeds next batch"])
    call(["gh", "issue", "edit", str(issue), "-R", repo, "--add-label",
         BOUNCE_REMARK_LABEL])
    return True


# --- #297: ❓/❔ reaction on a TRACKED bot message -> ask the session -------

_FLAG_EMOJI = ("❓", "❔")


def _flagged_emoji(msg):
    """The ❓/❔ emoji NAME on `msg` with a nonzero reaction count, or "".
    Discord's own message object already carries a `reactions` COUNT
    summary on every channel fetch — no extra call is needed to DETECT a
    flag; verifying WHO reacted (`fetch_reaction_users`) is a separate call,
    spent only on a message this job actually TRACKS (see `_flag_target`)."""
    if not isinstance(msg, dict):
        return ""
    for r in (msg.get("reactions") or []):
        if not isinstance(r, dict):
            continue
        name = ((r.get("emoji") or {}).get("name") or "")
        if name in _FLAG_EMOJI and (r.get("count") or 0) > 0:
            return name
    return ""


def _flag_target(msg_id, qmap, cardmap):
    """What a flagged message id resolves to for #297 — a tracked ❓
    question (exact asking session known) or a tracked completion card
    (repo known). None when `msg_id` is untracked by either map (the
    message is not one job 7 can act on — silently not actionable, never a
    guess)."""
    q = qmap.get(msg_id)
    if isinstance(q, dict):
        return {"kind": "question", "session": str(q.get("session") or ""),
                "cwd": str(q.get("cwd") or "")}
    c = cardmap.get(msg_id)
    if isinstance(c, dict):
        return {"kind": "card", "repo": str(c.get("repo") or ""),
                "issue": c.get("issue")}
    return None


def _flag_delivery_target(target, panes_by_sid, cwd_by_sid, run=None):
    """(sid, pid, cwd, exact) of the pane to deliver a #297 flag-prompt
    into, or None. Prefers the EXACT asking session while it is still alive
    (question kind); otherwise falls back to the nearest live pane whose
    repo matches — the ticket's own explicit fallback clause, and the SAME
    resolution `_repo_live_pane` gives #298's post-reopen nudge.

    `exact` (adversarial-review MAJOR-1) tells the caller WHICH delivery
    gate to use: True only for the exact-asking-session branch, where the
    flagged message IS the very ❓ that session's transcript is sitting on
    — job 7's own idle/draft gate must apply there (never job 8's
    at-rest discipline, which REFUSES a session whose last marker is ❓,
    which would make the dominant #297 use case never deliver at all).
    False for every repo-fallback / card-kind resolution, where the target
    pane may be doing something UNRELATED and the stronger at-rest check is
    correct."""
    from notify import repo_name_for
    if target.get("kind") == "question":
        sess = target.get("session") or ""
        pane = panes_by_sid.get(sess)
        if pane:
            return sess, pane[0], target.get("cwd", ""), True
        cwd = target.get("cwd") or ""
        repo = repo_name_for(cwd, run=run) if cwd else ""
    else:
        repo = str(target.get("repo") or "").rstrip("/").split("/")[-1]
    if not repo:
        return None
    found = _repo_live_pane(repo, cwd_by_sid, panes_by_sid, run=run)
    return (found[0], found[1], found[2], False) if found else None


def _deliver_flag_prompt_to_exact_session(pid, run, text, dry_run, logs=None):
    """Deliver a #297 flag-prompt into the EXACT session that is (or was)
    asking the flagged question — job 7's OWN idle/draft delivery gate
    (`pane_at_idle_prompt`/`deliver_with_stash` via `_try_stash_nudge`),
    never `_nudge_repo_pane`'s job-8-style `_safe_to_bounce_nudge` check.

    Adversarial-review MAJOR-1: that stronger gate refuses ANY session
    whose transcript's last marker is ❓ ("never interject before the user
    answers") — but a tracked outstanding question's asking session is, BY
    CONSTRUCTION, sitting at exactly that marker (it is what fired the ❓
    ping being flagged). This is job 7's OWN everyday operating condition
    for delivering a REPLY, so the same gate is correct here too — a
    genuinely BUSY (mid-turn / dialog / copy-mode) pane still gets
    nothing, via `pane_in_mode`/`pane_at_idle_prompt` exactly as job 7's
    reply flow already enforces."""
    captured = capture_pane(pid, run)
    if pane_in_mode(pid, run):
        return False
    if not pane_at_idle_prompt(captured):
        return _try_stash_nudge(pid, captured, text, run, dry_run, logs=logs)
    if not dry_run:
        send_continue(pid, text, run)
    return True


_FLAG_PROMPT_TEMPLATE = (
    "Užívateľ označil túto tvoju Discord správu ikonkou ❓ ("
    "\"niečo sa mi na nej nezdá\"). Označená správa: «%s» — polož mu k tomu "
    "štruktúrovanú otázku (per user-questions-slovak.md: úvod čo/prečo, "
    "možnosti, jasné rozhodnutie), zisti čo konkrétne mu na nej nesedí, a "
    "konaj podľa jeho odpovede."
)


def compose_flag_prompt(flagged_text):
    text = clean_reply_text(flagged_text)[:900] or "(bez textu)"
    return _FLAG_PROMPT_TEMPLATE % text


def fetch_reaction_users(channel, message_id, emoji, token):
    """GET the users who reacted with `emoji` to `message_id` in `channel`.
    [] on any error/missing input — fail-safe, mirrors `fetch_channel_messages`
    (never breaks the poll)."""
    if not channel or not message_id or not token:
        return []
    try:
        from urllib.parse import quote
        raw = _discord_get(
            "https://discord.com/api/v10/channels/%s/messages/%s/reactions/%s"
            % (channel, message_id, quote(emoji)), token)
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _reacted_by_owner(users, allowed_ids):
    """True if any user in `users` (a `fetch_reaction_users` result) is a
    KNOWN OWNER of this machine — the SAME security boundary
    `known_owner_ids` already enforces for replies."""
    for u in users or []:
        if not isinstance(u, dict):
            continue
        uid = str(u.get("id") or "").strip()
        if uid in allowed_ids:
            return True
    return False


def deliver_discord_replies(now, run, state, panes_by_sid, dry_run=False,
                            discord_fetch=None, env=None, gh_comment=None,
                            hosted_users=None, foreign_questions=None,
                            foreign_drop=None, cwd_by_sid=None,
                            projects_dir=PROJECTS_DIR, reaction_fetch=None,
                            card_gh_fn=None, persist=None):
    """Route owner Discord replies into the sessions that asked (job 7) — AND
    (#297/#298) a ❓/❔ REACTION on a tracked bot message, and a REPLY on a
    tracked completion CARD.

    `panes_by_sid`: {session_id: (pane_id, captured_pane)} for the live `claude`
    panes this cycle (collected in run_once). `cwd_by_sid`: {session_id: cwd} —
    needed to resolve "the nearest live session of repo X" for #297's fallback
    and #298's post-reopen nudge (`{}` when omitted — both features then simply
    find nothing live, same as an empty box). `discord_fetch(channel, token)`:
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
      keystroke path keeps retrying as before.

    `card_gh_fn` (#298, deliberately a DIFFERENT name from the ticket-fallback
    `gh_comment`/its local `gh_fn` below — the two are unrelated gh call sites
    and sharing a name would silently shadow one of them): injectable
    `(argv, input_text=None) -> (ok, stdout)` for the card-reopen flow's `gh`
    calls; defaults to a real subprocess (`_gh_call`).

    `persist` (#297/#298 review MAJOR-4): the caller's save-state closure,
    called immediately after EACH successful card-reopen or flag-react
    dedup-marks — mirrors jobs 8/11's "dedup memory BEFORE the next item"
    shape, so a mid-sweep kill between two fetched messages never loses the
    marker for a mutation that already landed (a real `gh` reopen/comment or
    a real keystroke delivery). Defaults to a no-op; `run_once`'s own trailing
    `save_state()` still covers the ordinary case."""
    from notify import (bot_token, known_owner_ids, load_questions, drop_question,
                        _read_env, load_cards, forget_marker)
    logs = []
    env = _read_env() if env is None else env
    qmap = load_questions()
    cardmap = load_cards()
    cwd_by_sid = cwd_by_sid or {}
    # Merge HOSTED users' question maps (2026-07-21: montalu's claude used to
    # run in THIS tmux, its ❓ map living under /home/montalu — the session was
    # invisible to both watchdogs; historical since the 2026-07-24 subdev
    # migration gave montalu its own tmux, #33/#34 — kept generic for the next
    # shared-tmux stream). `q_owner` remembers which foreign user owns
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
    if (not qmap and not cardmap and not state.get("dreply_pointer")):
        return logs
    token = bot_token(env)
    allowed = known_owner_ids(env)
    if not token or not allowed:
        return logs
    fetch = discord_fetch or fetch_channel_messages
    gh_fn = gh_comment or _gh_comment
    react_fetch = reaction_fetch or fetch_reaction_users
    card_gh = card_gh_fn or _gh_call
    persist = persist or (lambda: None)

    done = state.get("dreply_done")
    done = list(done) if isinstance(done, list) else []
    done_set = set(done)
    blocked = state.get("dreply_blocked")
    blocked = dict(blocked) if isinstance(blocked, dict) else {}
    acked = state.get("dreply_acked")
    acked = list(acked) if isinstance(acked, list) else []
    acked_set = set(acked)
    card_done = state.get("dcard_done")
    card_done = list(card_done) if isinstance(card_done, list) else []
    card_done_set = set(card_done)
    react_done = state.get("dreact_done")
    react_done = list(react_done) if isinstance(react_done, list) else []
    react_done_set = set(react_done)
    # MINOR-7 (#297/#298 review): a malformed/legacy qmap/cardmap entry (not
    # a dict) must not crash the channel-set build — skip it, never guess.
    channels = {str(v.get("channel") or "") for v in qmap.values()
                if isinstance(v, dict)}
    channels |= {str(v.get("channel") or "") for v in cardmap.values()
                if isinstance(v, dict)}
    channels.discard("")

    def _ack(r):
        # ✅ = RECEIPT, fired the moment the reply is MATCHED (even while the
        # delivery pends) — a green check minutes late reads as "answer lost"
        # (3rd user report, 2026-07-20). Once per reply.
        if r["reply_id"] in acked_set:
            return
        if not dry_run:
            _react_ok(r["channel"], r["reply_id"], token)
            # #304: a --dry-run sweep must NEVER mark the dedup set — this
            # `acked`/`acked_set` pair is persisted into
            # state["dreply_acked"] unconditionally by run_once, so a
            # dry-run mark here would poison the REAL next sweep's dedup
            # state exactly like the done_set/done leak below.
            acked_set.add(r["reply_id"])
            acked.append(r["reply_id"])

    def _delivered(r, via_ticket=None):
        # dry-run simulates delivery — it must NEVER mutate the real on-disk
        # map (a dropped live question loses the answer), and it must NEVER
        # mark the reply done either (#304): run_once persists `done`/
        # `done_set` into state["dreply_done"] unconditionally, so a
        # dry-run mark here would make the FOLLOWING real sweep believe the
        # reply was already delivered and silently skip it.
        if not dry_run:
            done_set.add(r["reply_id"])
            done.append(r["reply_id"])
            fu = q_owner.get(r["referenced"])
            if fu:
                f_drop(fu, r["referenced"])
            else:
                drop_question(r["referenced"])
            # #304 review MINOR-5: popping the reply's "first blocked at"
            # timestamp is itself a real mutation of persisted state (the
            # fallback-deadline clock) -- a dry-run simulating delivery via
            # the idle-pane fast path must not silently wipe a REAL clock a
            # concurrent/earlier real sweep already started.
            blocked.pop(r["reply_id"], None)
        qmap.pop(r["referenced"], None)     # same-batch 2nd reply won't re-fire
        if via_ticket:
            logs.append("reply→ticket #%s [%s]" % (via_ticket, r["session"][:12]))
        else:
            logs.append("reply→%s [%s]" % (project_label(r["cwd"]), r["session"][:12]))

    def _pending(r, why):
        # #304 review MINOR-5: creating the "first blocked at" timestamp is
        # itself real persisted state (the fallback-deadline clock) — a
        # dry-run must not create one that didn't already exist.
        if not dry_run:
            blocked.setdefault(r["reply_id"], now)
        # The durable lane once the keystroke path has been blocked long
        # enough. NO pane = we may not be the pane's HOST (a hosted stream's
        # session lives in another user's tmux) — defer longer so the host
        # delivers by keystroke first; a busy/wedged pane WE own keeps the
        # tight deadline. #304 review MAJOR: this whole branch is a REAL
        # mutation (a gh comment, plus a durable state["dreply_pointer"]
        # entry that a LATER real sweep types into a live pane as an
        # instruction to go read it) — a --dry-run diagnostic must never
        # fake success here. The original code's `if dry_run: ok = True`
        # did exactly that: a real keystroke telling a live session to read
        # a ticket comment that was never actually posted.
        if not dry_run:
            deadline = (DREPLY_NOPANE_FALLBACK_S if why == "no pane"
                        else DREPLY_TICKET_FALLBACK_S)
            if now - blocked[r["reply_id"]] >= deadline:
                m = _TICKET_NUM_RX.search(r.get("question") or "")
                if m:
                    fu = q_owner.get(r["referenced"])
                    if fu:
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
            # (#297) ❓/❔ REACTION on a TRACKED bot message -> ask the
            # sending stream about it. Independent of the reply-shaped
            # checks below — a message can be a reply AND separately
            # flagged. Cheap in the common (untracked) case: the reaction
            # COUNT is already on `msg` from the channel fetch, so
            # `_flag_target` (a dict lookup, no network) runs BEFORE the
            # one extra call (`react_fetch`) that verifies WHO reacted.
            mid = str(msg.get("id") or "").strip()
            emoji = _flagged_emoji(msg)
            if mid and emoji and mid not in react_done_set:
                target = _flag_target(mid, qmap, cardmap)
                if target is not None:
                    users = react_fetch(ch, mid, emoji, token)
                    if _reacted_by_owner(users, allowed):
                        deliver = _flag_delivery_target(
                            target, panes_by_sid, cwd_by_sid, run=run)
                        if deliver is not None:
                            d_sid, d_pid, d_cwd, d_exact = deliver
                            prompt = compose_flag_prompt(msg.get("content"))
                            # MAJOR-1 (adversarial review): the EXACT
                            # asking session's own ❓ marker must never
                            # block delivery of the flag ABOUT that very
                            # question — job 7's own idle/draft gate, not
                            # job 8's at-rest discipline, which refuses
                            # any ❓-marker session outright.
                            if d_exact:
                                ok = _deliver_flag_prompt_to_exact_session(
                                    d_pid, run, prompt, dry_run, logs=logs)
                            else:
                                ok = _nudge_repo_pane(d_pid, d_cwd, run, prompt,
                                                      dry_run, projects_dir,
                                                      logs=logs)
                            if ok and not dry_run:
                                react_done_set.add(mid)
                                react_done.append(mid)
                                state["dreact_done"] = react_done[-_DREPLY_DONE_CAP:]
                                persist()
                                logs.append("flag-react→%s [%s]"
                                            % (project_label(d_cwd),
                                               d_sid[:12] if d_sid else "-"))
                            elif ok:
                                logs.append("flag-react (dry-run)→%s [%s]"
                                            % (project_label(d_cwd),
                                               d_sid[:12] if d_sid else "-"))
                            else:
                                logs.append("flag-react pending (busy) %s"
                                            % mid[-8:])
                        else:
                            logs.append("flag-react pending (no live "
                                        "session) %s" % mid[-8:])

            # (#298) REPLY on a tracked completion CARD -> reopen the
            # ticket with the remark. `parse_discord_card_reply` returns
            # None for a reply whose referenced id is NOT a card (incl.
            # every ❓-ping reply the block below already owns), so this
            # never competes with the existing reply flow.
            cr = parse_discord_card_reply(msg, allowed, cardmap)
            if cr and cr["reply_id"] not in card_done_set:
                if dry_run:
                    reopened = True
                else:
                    reopened = _card_reopen_flow(cr["repo"], cr["issue"],
                                                 cr["text"], gh_fn=card_gh)
                if reopened and not dry_run:
                    card_done_set.add(cr["reply_id"])
                    card_done.append(cr["reply_id"])
                    state["dcard_done"] = card_done[-_DREPLY_DONE_CAP:]
                    persist()
                    logs.append("card-reopen #%s (%s)"
                                % (cr["issue"], cr["repo"]))
                    # MINOR-9: prefer the FETCHING channel `ch` (this is the
                    # channel the reply was actually read from) — `cr["channel"]`
                    # is only a best-effort fallback for a card record from
                    # before that field existed.
                    _react_ok(ch or cr["channel"], cr["reply_id"], token)
                    bare = cr["repo"].rstrip("/").split("/")[-1]
                    # MAJOR-5: the ticket's own run-card dedup marker (written
                    # at card-send time) would otherwise block a FRESH card
                    # once the worker re-fixes and re-closes this issue —
                    # release it now that the user has flagged the old one
                    # as needing another look.
                    forget_marker("%s#%s" % (bare, cr["issue"]))
                    ctarget = _repo_live_pane(bare, cwd_by_sid, panes_by_sid,
                                              run=run)
                    if ctarget is not None:
                        c_sid, c_pid, c_cwd = ctarget
                        _nudge_repo_pane(
                            c_pid, c_cwd, run,
                            compose_card_reopen_nudge(cr["issue"]),
                            dry_run, projects_dir, logs=logs)
                elif reopened:
                    logs.append("card-reopen (dry-run) #%s (%s)"
                                % (cr["issue"], cr["repo"]))
                else:
                    logs.append("card-reopen failed #%s (%s)"
                                % (cr["issue"], cr["repo"]))

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
            # a PRIOR wedged delivery of THIS reply left our own text at `❯`.
            # Both ENDS of the box must agree that it is ours — a tail-only
            # suffix test submits a foreign draft (#193).
            own_stuck = _box_holds_our_own_text(captured, prompt)
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
                    if deliver_with_stash(pid, prompt, run, captured=captured,
                                          logs=logs):
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
            # #304 review MINOR-5: clearing the wedge-episode counter is a
            # real mutation of persisted alerting state — a dry-run
            # simulating delivery via this idle-pane fast path must not
            # silently reset a REAL wedge counter (delaying/hiding the
            # 3-cycle alert threshold above).
            if not dry_run:
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
    state["dcard_done"] = card_done[-_DREPLY_DONE_CAP:]
    state["dreact_done"] = react_done[-_DREPLY_DONE_CAP:]
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
# montalu2/montalu3/montalu4 (airuleset#251, odoo-erp#2961): three MORE full
# parallel montalu streams working on odoo-erp (a _CROSS_STREAM_REPOS
# member) — need their own bounce-quals scoping like montalu itself, or a
# pane in one of their homes falls through to the full-authority
# exclude-all-reduced-streams branch instead of its own stream:<user> label.
#
# simap and miva1 (airuleset#143 / #300) are NOT in this tuple -- an
# omission carried over unrevisited from #143, never a documented decision.
# Their odoo-erp involvement was never confirmed at onboarding time, so
# widening the tuple for them would be exactly the speculative pre-emptive
# fix this repo's own FREEZE forbids ("the same defect exists in N more
# places" is not fixed pre-emptively). Consequence, scoped to
# _CROSS_STREAM_REPOS (odoo-erp): _gkreq_supervisor_root would (wrongly)
# treat a pane under /home/simap/ or /home/miva1/ as a FULL-authority
# supervisor root, so job 11 could nudge that stream's OWN pane to work a
# needs-gatekeeper ticket it filed itself -- backwards, the same failure
# _gkreq_supervisor_root's own docstring says to avoid. Neither stream has
# ever produced that failure in production -- if it ever does, start here.
#
# david2/david3/david4 (airuleset#326, 2026-08-08) ARE in this tuple --
# corrected by adversarial review after the first onboarding pass initially
# left them out, misapplying the simap/miva1 precedent above. The two cases
# are NOT the same: simap/miva1's odoo-erp involvement is unconfirmed
# (speculative), while #326's own ticket text states david2/3/4 are
# provisioned specifically "for the odoo-erp repo" as clones of `david`
# itself (which IS in this tuple) -- a KNOWN, ticket-scoped fact, not a
# pre-emptive guess. Confirmed live before the fix: `_bounce_quals(
# "/home/david2/devel/odoo-erp")` returned the full-authority exclude-all
# fragment instead of `["label:stream:david2"]`, and `_gkreq_supervisor_root(
# "/home/david2/devel/odoo-erp")` returned True (would nudge david2's own
# pane about its own gk-request) -- the same montalu2/3/4-style regression
# item 3 of #326 exists to prevent, just in the watchdog's separate
# bounce/gkreq registry rather than AUTHORITY_BY_USER's generic one.
_REDUCED_STREAM_USERS = ("david", "marek", "montalu",
                         "montalu2", "montalu3", "montalu4",
                         "david2", "david3", "david4")

BOUNCE_NUDGE = ("bounce-backstop: open prio:bounce tickets %s in %s — "
                "gatekeeper-returned work is waiting. Per the autopilot "
                "skill's bounce nudge-ack: if a /goal loop is armed the label "
                "alone queues them; with NO loop armed, validate and dispatch "
                "the background autopilot-worker for them now.")

# #89 (2026-07-26 restreamer incident): `prio:bounce` only has protocol
# meaning INSIDE the gatekeeper<->sub-dev cross-stream flow (`## Cross-stream
# protocol`, skills/autopilot/SKILL.md) -- a bare label on an unrelated repo
# (a human using it as a generic "priority" marker, exactly what happened on
# restreamer #337, author+label both zbynekdrlik, nothing to do with any
# bounce) must never read as a real bounce. There is no reliable PER-TICKET
# signal (the false ticket had a real, unrelated comment too, so "has a
# comment" doesn't discriminate) -- the discriminator is the REPO: job 8 may
# only ever query/nudge repos that actually PARTICIPATE in the protocol. Add
# a repo's basename here the day it onboards a gatekeeper<->sub-dev flow;
# everything else is structurally exempt, by construction, from job 8 ever
# even asking GitHub about it.
_CROSS_STREAM_REPOS = frozenset({"odoo-erp"})


def _repo_in_cross_stream_flow(root, cross_stream_repos=None):
    """Does the repo at `root` actually participate in the gatekeeper<->
    sub-dev cross-stream flow? `cross_stream_repos=None` resolves to the
    real registry above (the DI convention every other bounce_backstop
    input already follows: `gh_fetch=None` -> the real fetcher,
    `projects_dir=None` -> the real PROJECTS_DIR) -- pass an explicit set
    only to override it (e.g. in a test)."""
    repos = _CROSS_STREAM_REPOS if cross_stream_repos is None else cross_stream_repos
    name = os.path.basename(str(root or "").rstrip("/"))
    return name in repos


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


def _try_stash_nudge(pid, captured, text, run, dry_run, logs=None):
    """Shared bounce/gk-request helper (issue #35): attempt a stash-around
    delivery of `text` for a pane that already passed the live-work / armed
    -loop / already-nudged guards but isn't bare-idle — i.e. it holds a
    draft, not a running turn. dry_run never attempts it (keeps the
    diagnostic simulation identical to the pre-#35 behavior).

    `logs`, if a list, collects `deliver_with_stash`'s own reason strings.
    Both callers used to pass none at all, so every internal reason went
    nowhere and a failed delivery was indistinguishable from a supervisor
    who simply chose not to act — 48h of gk-request nudges left no trace of
    any kind (#193). A delivery that cannot run now always says why.

    A dry run records nothing, and its callers log nothing either — a
    SIMULATED skip is not a failed delivery, and reporting it as one would
    make `--dry-run` accuse a repo whose real sweep would have succeeded."""
    if dry_run:
        return False
    return deliver_with_stash(pid, text, run, captured=captured, logs=logs)


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
                    projects_dir=None, user=None, cross_stream_repos=None,
                    time_fn=None, sweep_deadline=None):
    """Job 8 — see the section comment. Mutates state['bounce']; `persist` (the
    caller's save-state closure) is invoked BEFORE any keystroke/ping leaves
    the process — the live incident: TimeoutStartSec killed the run after the
    nudge but before run_once's save, so dedup had no memory and the same
    nudge repeated every sweep. Returns log lines. Best-effort (never raises).

    #255 Fix 1: `time_fn`/`sweep_deadline` (both optional, default None ->
    unbounded, exactly today's behavior) give this job's own per-TARGET loop
    the SAME wall-clock self-bound `run_once`'s per-transcript pane loop
    already has via #172 -- this loop previously had none at all, unlike
    that one. The check sits strictly BETWEEN targets, checked BEFORE a
    target's delivery starts, never nested inside one target's own
    `send_continue`/`_try_stash_nudge` call (each a single atomic type+
    submit pair) -- a target already being delivered always finishes; only
    a NOT-YET-STARTED target is deferred to the next sweep. A deferred
    target's dedup memory (`seen`) is never written, so it is retried next
    sweep rather than silently dropped."""
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
    time_fn = time_fn or time.monotonic
    persist()                                  # cadence stamp survives a kill
    logs = []

    panes = list_claude_panes(run, logs=logs, dry_run=dry_run)
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

    for idx, (root, (name, pid)) in enumerate(sorted(targets.items())):
        if sweep_deadline is not None and time_fn() >= sweep_deadline:
            # #255 Fix 1: never START a new target's delivery once the
            # shared sweep budget is exhausted. Nothing has been fetched or
            # typed for THIS (or any later) target yet, so deferring here
            # loses nothing -- it is retried on the next sweep exactly like
            # an untouched target always would be.
            logs.append("bounce-budget-exceeded — %d/%d targets handled "
                        "this tick, rest retried next" % (idx, len(targets)))
            break
        if not _bounce_quals(root):
            continue                           # gatekeeper: never bounce-nudged
        if not _repo_in_cross_stream_flow(root, cross_stream_repos):
            # #89: prio:bounce has no protocol meaning outside a repo that
            # actually participates in the gatekeeper<->sub-dev flow — never
            # even ask GitHub, let alone nudge (the restreamer #337 false
            # nudge: a bare label used as a generic priority marker).
            logs.append("bounce-skip-not-cross-stream %s" % name)
            continue
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
                # it, deliver the nudge, let CC restore it (issue #35). A
                # verify failure still skips, but never silently (#193).
                why = []
                ok = _try_stash_nudge(pid, captured, BOUNCE_NUDGE % (tick_str, name),
                                      run, dry_run, logs=why)
                # #271 (adversarial-review MAJOR finding): `why` also carries
                # `deliver_with_stash`'s own rescue-persist line — promote it
                # to the main journal on EITHER outcome, not just failure, or
                # a successful rescue here is as silent as the incident this
                # mechanism exists to prevent.
                logs.extend(ln for ln in why if "draft-rescue" in ln)
                if not ok:
                    if not dry_run:
                        logs.append("bounce-nudge-failed %s %s (%s)"
                                    % (name, tick_str, "; ".join(why) or "no reason"))
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

    panes = list_claude_panes(run, logs=logs, dry_run=dry_run)
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
                # deliver, let CC restore it (issue #35). A verify failure
                # still skips, but never silently (#193): this job threaded no
                # log list at all, which is why 48h of stranded nudges left
                # not one `stash-*` line in the journal.
                why = []
                ok = _try_stash_nudge(pid, captured, GKREQ_NUDGE % (tick_str, name),
                                      run, dry_run, logs=why)
                # #271 — see bounce_backstop's identical fix above.
                logs.extend(ln for ln in why if "draft-rescue" in ln)
                if not ok:
                    if not dry_run:
                        logs.append("gkreq-nudge-failed %s %s (%s)"
                                    % (name, tick_str, "; ".join(why) or "no reason"))
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
# #160 (2026-08-06) — SHARED cached "does this repo's own backlog still have
# open, actionable work?" read, consulted by TWO jobs: job 20's
# goal-achieved backstop (defect 1 — don't trust a false "✔ Goal achieved"
# while tickets remain) and job 10's widened wedge ping (defect 4 — an
# unsent draft blocking delivery while nothing runs and real work is
# waiting IS worth a ping). One shared per-cwd cache in `state` means a repo
# with several panes/sessions costs at most one `gh` call per TTL window,
# never one per pane. `backlog_fetch(cwd)` (wired in airuleset.py, like
# every other network call in this file, so tests stay network-free) returns
# an int (possibly 0) or None on failure.
# --------------------------------------------------------------------------- #

BACKLOG_CHECK_INTERVAL_S = 10 * 60   # 10 min; a dead pane in this state can
                                      # sit for HOURS (the whole point of
                                      # both defects) — this bounds how often
                                      # `gh` gets hit while it does
# #160-review-style finding 🔵F5 (this ticket's own review) — a FAILED/
# refused fetch (`open_ is None`) used to be cached for the SAME 10-minute
# TTL as a genuine answer, which is backwards: the expensive-and-useless
# case (a transient `gh` hiccup, an auth blip, a 15s subprocess timeout) is
# what gets rate-limited IN for the longest, exactly when a retry is
# cheapest to want. A much shorter negative TTL still bounds `gh` load
# while letting the next sweep try again soon.
BACKLOG_CHECK_FAILURE_TTL_S = 60


def _cached_backlog_open(cwd, backlog_fetch, state, now, ttl=None):
    """True/False/None -- does the repo at `cwd` have an open, actionable
    (non-`autopilot-skip`) issue backlog right now? Cached per `cwd` in
    `state['backlog_cache']` for `ttl` (default `BACKLOG_CHECK_INTERVAL_S`
    seconds for a genuine True/False answer, `BACKLOG_CHECK_FAILURE_TTL_S`
    for an unmeasurable/failed one — #160-review 🔵F5).

    `backlog_fetch is None` (not wired) -> None, unconditionally, no cache
    write -- same "wired = on" convention as every other injected fetch in
    this file. None is UNMEASURABLE and every caller must treat it as
    "cannot tell, do not act" -- never as either polarity (#166's
    carried-forward fail-open requirement)."""
    if backlog_fetch is None:
        return None
    ttl = BACKLOG_CHECK_INTERVAL_S if ttl is None else ttl
    cache = state.setdefault("backlog_cache", {})
    entry = cache.get(cwd)
    if isinstance(entry, dict):
        # #160-review 🔵F9 -- `ts` crosses a JSON persistence boundary
        # (this repo's own established rule: never trust a `.get()` off
        # such a boundary without a type check) -- a malformed/legacy
        # entry must read as EXPIRED, never raise or silently misbehave.
        try:
            age = now - float(entry.get("ts", 0))
        except (TypeError, ValueError):
            age = None
        if age is not None:
            entry_ttl = (ttl if entry.get("open") is not None
                        else BACKLOG_CHECK_FAILURE_TTL_S)
            if age < entry_ttl:
                return entry.get("open")
    try:
        count = backlog_fetch(cwd)
    except Exception:
        count = None
    open_ = (count > 0) if isinstance(count, int) else None
    cache[cwd] = {"ts": now, "open": open_}
    return open_


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
# #255: consecutive machine-submit attempts (each its own PWEDGE_SWEEPS-sweep
# cycle) tolerated on an ordinary swallowed-submit race before escalating to
# the bracketed-paste-END unstick sequence -- the live incident took 3
# attempts over ~5 minutes with plain Enter alone, all ineffective.
PWEDGE_SUBMIT_UNSTICK_AFTER = 2
# #255 adversarial-review MINOR finding: without this, an unrecoverable
# stuck pane would retry the paste-end unstick every ~2 sweeps FOREVER with
# nobody ever told. Once escalation ITSELF has failed this many further
# times (still on the SAME draft), send ONE deduped give-up ping — the
# automatic retry keeps happening (still the best available recovery), but
# a human is now told this pane needs a look.
PWEDGE_SUBMIT_GIVEUP_AFTER = PWEDGE_SUBMIT_UNSTICK_AFTER + 3
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
                       send_fn, dry_run=False, run=None, waiting=True,
                       cwd=None, backlog_fetch=None):
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
    decision — a machine must never submit a half-typed user draft).

    #160 defect 4 (`cwd`/`backlog_fetch`, both optional): the `not waiting`
    branch used to stay silent UNCONDITIONALLY — "tracked but nothing
    urgent" — even though a session stuck this way (unsent draft, nothing
    running, per the guard already above this branch) is worth a ping
    whenever the repo's own backlog genuinely has open, actionable work
    nobody else can reach while the draft blocks delivery. When
    `backlog_fetch` is wired and `_cached_backlog_open` confirms the
    backlog is non-empty, this falls through to the SAME ping path below
    instead of returning silently. `cwd`/`backlog_fetch` left at their
    default `None` (every pre-#160 caller/test) makes this a complete
    no-op — unchanged behavior."""
    key = "pwedge:" + pid
    box = _find_input_box(captured)
    if box is None:
        # We could not READ the input line — a running-turn spinner, an open
        # dialog, or no locatable boundary at all. That is NOT evidence that
        # there is no draft, and collapsing the two is what made this job
        # blind to every wrapped draft (#193): `_input_line_text` returned
        # None, the episode was forgotten on every sweep, and the two stable
        # sweeps its auto-Enter recovery needs could never accumulate — so
        # the one backstop for a stuck draft could not see the stuck draft.
        # An unreadable sweep neither ADVANCES an episode nor FORGETS it.
        return []
    head, tail, wrapped = box
    txt = tail if wrapped else head[1:].strip()
    attempts_key = "pwedge-submit-attempts:" + pid
    giveup_key = "pwedge-submit-giveup:" + pid
    if not txt:
        state.pop(key, None)          # provably BARE — there really is no draft
        state.pop(attempts_key, None)  # #255: the draft cleared -- forget any
                                       # escalation count so a LATER, unrelated
                                       # stuck draft starts counting from zero
        state.pop(giveup_key, None)    # ...and the give-up-ping dedup with it
        return []
    # A machine nudge's PREFIX lives on the box's HEAD row. A wrapped draft's
    # boundary row is its TAIL and can never carry it, so testing `txt` alone
    # mislabels our own stranded nudge a foreign draft and pings instead of
    # submitting it.
    head_txt = head[1:].strip()
    machine = (head_txt.startswith(MACHINE_NUDGE_PREFIX)
               or _is_dreply_machine_text(state, pid, head_txt, txt))
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
        # NOTE: this fires both for a genuinely NEW draft AND for the SAME
        # still-stuck draft starting its next PWEDGE_SWEEPS-cycle right
        # after a machine-submit attempt popped `key` (#255) -- `attempts_key`
        # is deliberately NOT touched here; it carries its OWN hash below and
        # is the thing that tells those two cases apart.
        state[key] = {"hash": h, "n": 1, "pinged": False}
        return []
    st["n"] = int(st.get("n") or 1) + 1
    state[key] = st
    if st["n"] < PWEDGE_SWEEPS or st.get("pinged"):
        return []
    if machine:
        unstick_note = ""
        if not dry_run and run and not pane_in_mode(pid, run):
            # #255: attempts_key tracks its OWN (hash, count) pair,
            # independent of `key`'s own pop/reset cycle above -- it must
            # keep incrementing across repeated PWEDGE_SWEEPS-cycles of the
            # SAME stuck draft (same hash) and only reset to 1 when the
            # CONTENT genuinely changes (a different draft entirely).
            arec = state.get(attempts_key)
            arec = dict(arec) if isinstance(arec, dict) else {}
            attempts = (int(arec.get("n") or 0) + 1) if arec.get("hash") == h else 1
            state[attempts_key] = {"hash": h, "n": attempts}
            if attempts > PWEDGE_SUBMIT_UNSTICK_AFTER:
                # #255: this SAME draft has already survived
                # PWEDGE_SUBMIT_UNSTICK_AFTER consecutive machine-submit
                # attempts (each of which already sent its OWN leading
                # Escape below and still failed) -- an ordinary swallowed-
                # submit race (issue #36's class) is ruled out by now. Live
                # incident (david@subdev, 2026-08-05): Claude Code was left
                # waiting for the ANSI bracketed-paste END marker (ESC[201~)
                # that nothing in this codebase ever sends -- `send-keys -l`
                # never emits real bracket framing (confirmed empirically:
                # tmux only wraps `paste-buffer -p`, never `send-keys -l`,
                # per `man tmux`) -- so every further Enter was swallowed as
                # literal text. A human's manual `send-keys -H 1b 5b 32 30
                # 31 7e` + Enter (NO leading Escape) recovered it instantly.
                #
                # CRITICAL (adversarial review): do NOT also send the
                # ordinary pre-Escape on THIS attempt -- it would put two
                # ESC bytes back to back on the wire (Escape, then this
                # sequence's own leading 0x1b), which is the exact rapid-
                # double-escape shape that PERMANENTLY DELETES a draft
                # (issue #35's hard rule) and might not even be recognized
                # as ESC[201~ at all (could parse as an Alt-modified CSI
                # instead). Send EXACTLY the sequence that was proven to
                # work, nothing more.
                run(["tmux", "send-keys", "-t", pid, "-H",
                     "1b", "5b", "32", "30", "31", "7e"])
                unstick_note = " (paste-end unstick)"
                if attempts > PWEDGE_SUBMIT_GIVEUP_AFTER and not state.get(giveup_key):
                    # #255 (adversarial review MINOR finding): the paste-end
                    # unstick itself has now failed to actually clear this
                    # SAME draft PWEDGE_SUBMIT_GIVEUP_AFTER times over --
                    # keep retrying (it is still the best automatic
                    # recovery available), but tell a human ONCE rather
                    # than retrying silently forever.
                    state[giveup_key] = True
                    where = _pane_location(pid, run) if run else ""
                    loc = " (%s)" % where if where else ""
                    send_fn(
                        "⚠️ **%s**%s — automatické odoslanie "
                        "(vrátane paste-end unstick) opakovane "
                        "zlyháva na tom istom texte v okne "
                        "(pid %s); over to ručne — stlač tam "
                        "Enter alebo priamo v termináli skontroluj "
                        "stav." % (project, loc, pid),
                        owner=owner or None,
                        dedup_key="pwedge-submit-giveup:%s:%s" % (pid, h),
                        dry_run=dry_run)
                    unstick_note += " + give-up ping"
            else:
                # Escape first (issue #36) — a swallowed submit while the
                # agent-strip selector holds focus makes a bare Enter
                # navigate instead of submit; never a SECOND Escape (issue
                # #35: deletes a draft permanently).
                run(["tmux", "send-keys", "-t", pid, "Escape"])
            run(["tmux", "send-keys", "-t", pid, "Enter"])
        state.pop(key, None)     # still stuck → re-tracks and retries in 2 sweeps
        return ["machine-nudge submit %s (%s)%s" % (pid, project, unstick_note)]
    # #238-review-style finding 🔴F3 (this ticket's own review): resolved
    # ONCE, before EITHER branch below — the `not waiting` backlog-ping used
    # to send unconditionally, completely bypassing this per-pane cooldown
    # (a re-wrap resets `st["hash"]`/`st["pinged"]` above, so without this
    # check a fresh PWEDGE_SWEEPS-cycle after a mere terminal-width reflow
    # would re-ping every time — the EXACT "často mi chodí" spam issue #35
    # already fixed for the waiting branch, reintroduced one branch over).
    ping_key = "pwedge-ping:" + pid
    last_ping = state.get(ping_key)
    in_cooldown = last_ping and now - last_ping < PWEDGE_PING_COOLDOWN_S
    if not waiting:
        # #160 defect 4 — before giving up silently, check whether the
        # repo's own backlog genuinely has open work waiting: "not waiting
        # on the user" no longer means "nothing depends on this clearing" —
        # a stash-around delivery (#35) already reaches the pane fine, but
        # nobody benefits from that while nothing is running and real
        # tickets sit unactioned. Unmeasurable/empty -> unchanged silent
        # "not waiting" behavior; genuinely non-empty -> fall through to the
        # SAME ping path below (own cooldown/dedup, tagged distinctly).
        if _cached_backlog_open(cwd, backlog_fetch, state, now) is not True:
            # tracked but nothing urgent — the session isn't blocked on the
            # user, and a stash-around delivery (#35) can still reach it. A
            # later poll where `waiting` flips True (same stable draft) pings.
            return ["pwedge-parked (not waiting) %s (%s)" % (pid, project)]
        if in_cooldown:
            st["pinged"] = True
            return ["pwedge-suppressed (cooldown, backlog) %s (%s)"
                    % (pid, project)]
        if dry_run:
            # #160-review-style finding 🟡F4 (this ticket's own review,
            # proven live) -- a --dry-run sweep run against REAL state (a
            # routine manual diagnostic on this repo) used to permanently
            # consume the one-shot cooldown with nothing ever actually
            # sent, since the state write happened unconditionally. Matches
            # this file's own established convention elsewhere (e.g. the
            # long-turn ping: `if dry_run ...: <skip, no state write>`
            # BEFORE ever calling send_fn) -- state is written only on a
            # genuine delivery.
            return ["prompt-wedge dry-run (backlog) %s (%s)" % (pid, project)]
        st["pinged"] = True
        state[ping_key] = now
        where = _pane_location(pid, run) if run else ""
        loc = " (%s)" % where if where else ""
        send_fn(
            "⚠️ **%s**%s — v okne visí NEODOSLANÝ text („%s…“), session stojí "
            "vyše 30 minút a v repozitári čaká otvorená práca, ktorú kým "
            "text blokuje, nikto neurobí. Stlač v tom okne Enter (text sa "
            "odošle) alebo ho zmaž."
            % (project, loc, head_txt[:60]),
            owner=owner or None,
            dedup_key="pwedge-backlog:%s:%s" % (pid, h), dry_run=dry_run)
        return ["prompt-wedge ping (backlog) %s (%s)" % (pid, project)]
    if in_cooldown:
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
            # the START of the draft, never its wrapped tail — a human
            # recognises their own text by how it begins (#193).
            % (project, loc, head_txt[:60]),
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
    the generic sudo-hosted stream case: a live pane visible here whose
    transcript lives under a foreign HOME (historical example: montalu
    inside newlevel's tmux on dev1, until the 2026-07-24 subdev migration
    gave it its own tmux, #33/#34 — no live pane matches this today, kept
    generic for the next shared-tmux stream). Uses `sudo -n -u <user>`
    (passwordless on the boxes where this shape exists); every failure
    returns None (the caller then refuses to arm a fragment)."""
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


_EXIT_CMD_MARKER = "<command-name>/exit</command-name>"


def _goal_user_exit_ts(tpath, off=None, tail_bytes=None):
    """The epoch timestamp of the NEWEST explicit `/exit` slash-command
    anywhere in the bytes read, or `None` if unreadable/absent/never found
    (#335).

    `/exit` is the user's OWN stated way of deliberately ending a session
    ("ja ked nerobim v niektorom projekte exitnem sa z claude") -- but
    unlike `/goal clear` (#170), Claude Code writes NO special marker for
    it that `scan_goal_markers` would ever see. The ONLY durable trace is
    the ordinary `<command-name>/exit</command-name>` slash-command entry
    any local command leaves — a TOP-LEVEL `type=="user"` entry whose
    `message.content` is a plain STRING (verified live against
    simap@subdev's own real transcript). Matched ONLY on that shape, never
    inside a `tool_result` array — the SAME quoted-transcript exclusion
    `scan_goal_markers` already applies (a `tool_result` is always a list,
    never a bare string), so a session reading/pasting ANOTHER session's
    transcript (a review, an audit) is never misread as having exited
    itself.

    `off`/`tail_bytes` mirror `scan_goal_markers`'s OWN read-window
    contract exactly, so each caller pays the SAME cost profile it already
    accepts for its own marker read: `off=None` (the default) bootstraps
    from the file's TAIL — cheap, matching `_goal_was_cleared_by_user`'s
    existing per-sweep cost (job 9 calls this on every candidate, every
    60s sweep); `off=0` forces a FULL read — matching
    `_goal_dark_died_by_outage`'s own established correctness-over-cost
    choice for its rare, already-expensive dark-past-cap population (a
    long-lived busy loop can push its own final `/exit` far past a tail
    window before it dies).

    Fail-safe: unreadable/absent/never-found returns `None` — never
    guessed as "definitely no exit happened"; the caller must treat `None`
    the same conservative way an absent `Goal cleared:` marker already is
    (no positive evidence of a deliberate exit does not, by itself, prove
    one never happened, but it also must never become a NEW reason to
    suppress a re-arm the caller has other, independent grounds for)."""
    if tail_bytes is None:
        # `GOAL_MARK_TAIL_BYTES` is defined LATER in this module (it lives
        # beside `scan_goal_markers`) -- resolved here, at CALL time, never
        # as a default-parameter-value expression (which Python evaluates
        # at DEF time, still module-load-order-before the constant exists).
        tail_bytes = GOAL_MARK_TAIL_BYTES
    try:
        size = os.path.getsize(tpath)
    except OSError:
        return None
    start = off
    if start is None or start > size or start < 0:
        start = max(0, size - tail_bytes)
    try:
        with open(tpath, "rb") as f:
            f.seek(start)
            raw = f.read()
    except OSError:
        return None
    from datetime import datetime
    marker = _EXIT_CMD_MARKER.encode()
    best = None
    for ln in raw.splitlines():
        if marker not in ln:
            continue
        try:
            entry = json.loads(ln)
        except Exception:
            continue
        if entry.get("type") != "user":
            continue
        content = entry.get("message", {}).get("content")
        # #335-review F4 (MINOR) -- `startswith`, not `in`: CC's own real
        # `/exit` entries always OPEN with the marker (verified live
        # against simap@subdev), so requiring it at position 0 is the same
        # mention-vs-use discriminator `_parse_goal_marker` already applies
        # -- a longer message merely QUOTING the marker text (a runbook
        # excerpt, a review comment pasted into chat) must never count as
        # a real exit.
        if not isinstance(content, str) or not content.startswith(
                _EXIT_CMD_MARKER):
            continue
        try:
            ts = datetime.fromisoformat(
                str(entry.get("timestamp")).replace(
                    "Z", "+00:00")).timestamp()
        except Exception:
            continue
        if best is None or ts > best:
            best = ts
    return best


def _goal_was_cleared_by_user(tpath):
    """True when this session's NEWEST `/goal` marker is a CLEAR (#170), OR
    when an explicit `/exit` postdates the newest goal marker (#335 —
    "kým to používateľ sám nespustí": the user's own stated stop mechanism
    must carry the SAME force as an explicit clear).

    Claude Code writes `Goal cleared:` ONLY for an explicit `/goal clear`; a
    goal that finishes on its own prints `✔ Goal achieved` to the screen and
    persists nothing. Measured across 8329 local transcripts: 86 `Goal set:`
    markers against 32 genuine `Goal cleared:` ones, every one an explicit
    clear. So a newest-marker-is-clear reading means the user switched this
    loop off — which the pane's own viewport cannot show, since the arm
    question and the printed `/goal` line survive the clear untouched.

    Reuses `scan_goal_markers` rather than adding a second marker reader, so
    the structural filter that keeps a QUOTED marker (one session pasting
    another's transcript) from being read as state applies here too.

    The clear stops governing once the session prints a NEW arm question
    (`arm_after`). Without that exit the suppression could never release: a
    clear stays the newest marker for the rest of the session's life, and the
    only thing that would arm it again is the very job the clear is blocking
    — a suppression whose exit condition is the action it suppresses. Live
    regression (montalu@subdev, 2026-07-31): the user cleared a goal, later
    asked the session to print the arm question again, and job 9 refused
    forever, one `skip cleared` line per sweep against a `/goal` line sitting
    unarmed on screen. #170's own case is the OPPOSITE order — its arm
    question was printed BEFORE the clear and merely survives on screen,
    which is exactly why the viewport could not decide it.

    #335's `/exit` check applies the IDENTICAL release logic: a goal marker
    (set OR cleared) whose own `ts` postdates the exit means something
    happened AFTERWARDS (a fresh arm, or an even later explicit clear) and
    the exit no longer governs — never a second, independent suppression
    that could itself go stale.

    #335-review F1 (CRITICAL, fresh-context adversarial review): the FIRST
    shipped version had NO release condition analogous to `arm_after` at
    all — the exit-block lifted ONLY on a newer goal marker, but `/exit`
    itself writes no marker, so a printed arm question AFTER the exit did
    nothing and job 9 refused forever, reproducing #170's own exact
    regression class one address over (live-verified: 6 of 9 real dev1
    panes flipped from arming to permanently-blocked; this repo's own
    session transcript alone has 70 real `/exit` entries with 2654 entries
    after the last one). Release the exit the moment ANY real transcript
    turn (user or assistant) postdates it, via `_last_real_turn_ts` — the
    SAME reader job 10's #177 hardening already established, reused rather
    than a second one. STRICT `>`, never `>=`: `_last_real_turn_ts`
    deliberately counts the `/exit` entry ITSELF as a real turn (it is a
    genuine `type=="user"` entry) for job 10's own, different purpose --
    here that would let an exit release itself the instant it is written,
    so only activity STRICTLY newer than the exit counts as override.

    #335-review F1 sub-finding: a marker whose OWN `ts` is unmeasurable
    (present but unparseable) used to still let the exit stand as
    decisive — inconsistent with sibling functions (`_goal_cleared_stale`,
    `_goal_dark_died_by_outage`'s own anchor logic) that refuse to guess
    when a value is unmeasurable. Whenever a marker exists at all but its
    `ts` cannot be read, this function now returns False too (fail-open,
    matching its own documented default) rather than trusting an ordering
    it cannot actually establish.

    Fail-open: unreadable, no marker AND no exit found returns False, so a
    pane that used to arm keeps arming. Not provably cleared/exited must
    never start blocking.

    #335-review F7 (MINOR, doc correction): "markerless" alone no longer
    implies False by itself — a markerless session CAN still be genuinely
    exited (that is the whole point of F1's fix); "markerless" only means
    the `Goal cleared:` half of this check is silent, never that the
    function as a whole returns False."""
    try:
        _off, mark = scan_goal_markers(tpath)
    except Exception:
        mark = None
    cleared = (bool(mark) and mark.get("state") == "cleared"
               and not mark.get("arm_after"))
    if cleared:
        return True
    exit_ts = _goal_user_exit_ts(tpath)
    if exit_ts is None:
        return False
    if mark:
        mark_ts = mark.get("ts")
        if mark_ts is None:
            return False            # unmeasurable ordering — never guess
        if mark_ts >= exit_ts:
            return False            # a marker newer than the exit — stale
    # #335-review F1 ROUND 2 -- `_last_real_turn_ts` (job 10's own #177
    # reader) deliberately counts the exit's OWN companion bookkeeping
    # entries (`<local-command-caveat>`, `<local-command-stdout>Bye!`) as
    # "activity", and those companions routinely carry a timestamp a few
    # milliseconds NEWER than the exit command entry itself -- defeating
    # this release check on the exit's own bookkeeping the instant it is
    # written. `_last_real_turn_ts_excluding_command_bookkeeping` is the
    # dedicated sibling reader that excludes exactly those two prefixes;
    # see its own docstring for the measured blast radius.
    activity_ts = _last_real_turn_ts_excluding_command_bookkeeping(tpath)
    if activity_ts is not None and activity_ts > exit_ts:
        return False                # real activity postdates the exit
    return True


GOAL_AUTOARM_RECENT_HUMAN_S = 30 * 60
# #339 -- reuses `PWEDGE_MIN_IDLE_S`'s own VALUE (job 10's already-
# established "must be idle this long before treated as genuinely at
# rest" bound), never the constant NAME (this one governs a structurally
# different question and stays independently tunable). The live
# incident's own observed gap was ~2 minutes (human prompt -> auto-arm);
# 30 min is a ~15x margin, chosen deliberately generous because the two
# failure directions are asymmetric -- a missed arm just retries next
# sweep (cheap), a wrong arm interrupts a live human conversation (the
# reported incident).


def _goal_autoarm_recent_human_activity(sid, tpath, now, window_s=None):
    """#339 -- job 9's virgin-candidate path (`_goal_autoarm_virgin_candidate`)
    had NO discriminator at all for "is a live human using this pane RIGHT
    NOW" -- only whether `/goal` had ever been touched. montalu3's own
    first-ever session was interactive (the user typed "kto si?"), had
    never touched `/goal`, satisfied every existing gate, and got
    auto-armed ~2 minutes later -- the live incident this closes.

    Combines TWO independent signals, EITHER sufficient to refuse:

    1. The presence marker `/tmp/claude-user-active-<sid>` (stamped by
       `clear-question-dedup.sh` on UserPromptSubmit ONLY -- a `/goal`
       re-poke or a hook-feedback re-invocation never stamps it). A fresh
       mtime is cheap, direct evidence of a live human. A MISSING marker
       fails OPEN (the established #128 axis: `/tmp` gets cleared under
       long-running sessions or a box reboot, so absence never
       MANUFACTURES a "human present" signal on its own) -- it only means
       fall through to signal 2, never "safe to arm".
    2. `_last_human_prompt_ts(tpath)` -- the mandatory, always-available
       signal (no `/tmp` dependency, already used elsewhere in this file
       for question-dedup pruning). It excludes goal re-pokes, Stop-hook
       feedback, AND job-7 Discord-reply deliveries via its own
       `_MACHINE_PROMPT_PREFIXES` list ("Odpoveď z Discordu:" and
       "Stop hook feedback:" both there) -- the ticket's own named
       job-7 edge case needed no special-casing here at all.

    Both timestamps are clamped `-window_s <= now - ts < window_s`
    ("recent" in EITHER direction, bounded) before counting as recent.

    #339-review MAJOR (fresh-context adversarial review, live-reproduced
    against the real function): the first shipped clamp was the
    ASYMMETRIC `0 <= age < window_s` -- reasoning (wrongly) that a
    future-dated value must be clock-skew and should never read as
    recent, mirroring the #177 `min()` clamp lesson elsewhere in this
    file. That lesson does NOT transfer here: `now` is captured ONCE at
    the top of the whole sweep (`run_once`), while job 9 (and this
    check) runs at the sweep's TAIL, so a human's FIRST prompt landing
    AFTER `now` was captured but BEFORE this pane's own turn in the loop
    produces a transcript/marker timestamp genuinely NEWER than `now` --
    a negative `age` that the asymmetric clamp read as "not recent",
    reproducing the EXACT #339 incident through a timing hole. The
    symmetric bound absorbs that mid-sweep-drift case (a human prompt up
    to `window_s` in the future still counts as recent) while still
    refusing a GROSSLY future-dated value (clock skew, a transcript
    synced off another box, anything beyond `window_s`) -- and being
    generous on the future side costs nothing: it can only ever make this
    function MORE cautious (refuse when uncertain), never arm when it
    shouldn't. `window_s` defaults to `GOAL_AUTOARM_RECENT_HUMAN_S`.

    Evaluated and REJECTED (full reasoning on the #339 design comment):
    requiring ZERO human prompts in the WHOLE transcript before ever
    treating a session as virgin-armable. The genuinely-headless
    population this path targets includes reduced-authority stream boxes
    a human periodically attaches to and briefly chats with -- normal,
    expected use, not evidence the box should never self-heal into an
    armed loop again. The RECENT-window form already fixes the reported
    failure (arming DURING a live conversation, a recency question); a
    whole-transcript form would additionally, permanently disqualify a
    large legitimate population for zero extra safety on the incident
    actually reported.

    Fail direction: an unreadable transcript is refused OUTSIDE this
    function entirely (the caller's own `find_active_transcript` already
    gates it); `_last_human_prompt_ts` itself returns `None` on any read
    failure (its own documented contract), which this function treats as
    "no signal, not recent" -- never a manufactured refusal from an
    unmeasurable read, mirroring `_goal_never_armed`'s own adjacent
    discipline one function down."""
    window_s = GOAL_AUTOARM_RECENT_HUMAN_S if window_s is None else window_s
    try:
        mtime = os.stat("/tmp/claude-user-active-%s" % sid).st_mtime
    except OSError:
        mtime = None
    if mtime is not None:
        age = now - mtime
        if -window_s <= age < window_s:
            return True, "presence marker %s" % _human_age_desc(age)
    try:
        hts = _last_human_prompt_ts(tpath)
    except Exception:
        hts = None
    if hts is not None:
        age = now - hts
        if -window_s <= age < window_s:
            return True, "transcript human prompt %s" % _human_age_desc(age)
    return False, ""


def _human_age_desc(age):
    """#339-review MAJOR -- a human-activity timestamp can legitimately land
    slightly AFTER `now` (mid-sweep drift, see the caller's own docstring),
    so the log line must read sensibly either way rather than printing a
    confusing negative number."""
    return "%ds old" % int(age) if age >= 0 else "%ds in the future" % int(-age)


def _goal_never_armed(tpath):
    """True when this session's transcript has NEVER shown a `/goal` marker
    of any kind (#320 shape 2) — never armed, never cleared. A FULL-file
    scan (`off=0`) is deliberate: the whole point is "has this EVER
    happened", which an incremental/tail read cannot honestly answer (the
    same #173 lesson `_goal_dark_died_by_outage` already applies one level
    up). Unreadable or absent transcript is NOT virgin — unmeasurable must
    never guess a session into being armed.

    #320-review MAJOR-1 (fresh-context adversarial review, executed proof):
    the docstring's own "unreadable is not virgin" claim was FALSE as
    shipped — `scan_goal_markers` never raises on a read failure, it
    fails safe by returning `(off, None)` (its OWN documented contract),
    which is BYTE-IDENTICAL to "scanned cleanly, found nothing" — so a
    disappeared/permission-denied transcript (including one that DOES
    carry a real `Goal cleared:` marker the read simply couldn't reach)
    read as `mark is None` -> virgin -> armed, the opposite of #170's
    direction. An explicit open-for-real-bytes check BEFORE trusting the
    scan's "no marker" answer is what actually distinguishes "read
    genuinely failed" from "read succeeded, file genuinely has nothing"
    — `scan_goal_markers` provides no such signal itself."""
    try:
        if not os.path.isfile(tpath):
            return False
        with open(tpath, "rb"):
            pass
    except OSError:
        return False
    try:
        _off, mark = scan_goal_markers(tpath, off=0)
    except Exception:
        return False
    return mark is None


def _goal_autoarm_virgin_candidate(now, run, state, pid, cwd, templates,
                                   projects_dir, dry_run, sleep_fn, cap,
                                   unique_cwd=True):
    """#320 shape 2 (montalu2) — `goal_autoarm`'s arm-question branch only
    ever fires for a session that PRINTS the `/autopilot` arm question; a
    session doing ORDINARY interactive work never does, so it never gets a
    `/goal` loop at all and sits idle, silently, once its current work
    concludes. This is the COMPANION candidate path the caller takes only
    when the arm question was NOT found — a genuinely at-rest, never-
    touched-by-`/goal`-ever session gets armed ONCE with the authority-
    appropriate template.

    #170 stays untouched by construction: `_goal_never_armed` requires the
    WHOLE transcript to have NEVER shown a marker of ANY kind — a session
    the user genuinely cleared (or ever armed at all) is permanently
    excluded from this path, forever, the moment that single fact is
    established.

    #320-review MAJOR-2 (fresh-context adversarial review, reasoned trace):
    `find_active_transcript(projects_dir, cwd)` resolves the NEWEST
    transcript in the cwd's dir, regardless of which PANE actually hosts
    it — with two live panes sharing one cwd (routine: a second window
    opened in the same repo), the OLDER pane could get the NEWER pane's
    virgin/non-virgin verdict typed into IT, including pasting a fresh
    `/goal` into a session the user genuinely cleared (#170) while the
    session the feature exists for never gets its one-shot at all. The
    caller precomputes `unique_cwd` from ITS OWN full per-sweep pane list
    (cheap, O(panes), no new tmux call) — pairing is refused outright the
    moment it is ambiguous, never guessed at.

    #320-review MAJOR-3 (fresh-context adversarial review, executed
    proof): `pane_in_mode` (and the OTHER cheap dict-cache checks) must
    run BEFORE the expensive full-file scan, not after — a pane parked in
    copy-mode (routine: the user scrolled up and walked away) used to
    re-pay the WHOLE-transcript scan every single sweep forever, since
    neither cache gets written on that return path. Reordered so a
    still-undecided candidate is refused on tmux-cheap grounds first;
    the scan is reached only once every earlier, cheaper gate has passed.

    Cost discipline: every check before the (comparatively expensive)
    full-file marker scan reuses `cap`/`tail` the caller already captured
    this sweep, except the one `pane_in_mode` tmux call immediately
    before it (moved here specifically so a copy-mode pane never reaches
    the scan at all). The scan itself is paid AT MOST ONCE per session
    for its whole lifetime, either way it resolves: a confirmed
    non-virgin session is cached in `state['goalarm_hasgoal']` and never
    rescanned; a virgin session is armed and recorded in
    `state['goalarm_virgin_tried']`, one-shot, never retried automatically
    — if the arm itself silently fails to persist (the SAME open
    marker-writing mystery #320's own dev1 forensics surfaced), this job
    will not keep hammering the pane; that needs a human, not a loop.

    #320-review MINOR-8: `pane_goal_armed(cap)` anything other than a
    confirmed `False` (i.e. `True`, or `None` = undeterminable) refuses —
    a session CC armed WITHOUT writing any transcript marker (the same
    open mystery this ticket surfaced live, three times in one day on
    dev1) is transcript-virgin but NOT actually goal-less; never guess
    past an undeterminable footer either.

    No backlog pre-check: the armed goal's OWN stop condition re-verifies
    the backlog on its very first turn and self-terminates immediately if
    it is genuinely empty — cheaper and simpler than a `gh`-backed
    pre-check inside this hot per-pane sweep, at the cost of at most one
    wasted Stop-hook round-trip."""
    logs = []
    if _pane_has_bg_agent(cap):
        return logs
    kind, draft = _classify_boundary(cap)
    if kind != "input" or draft:
        return logs
    if pane_goal_armed(cap) is not False:
        return logs
    if not unique_cwd:
        return logs
    tr = find_active_transcript(projects_dir, cwd)
    if not tr:
        return logs
    sid = os.path.basename(str(tr[0])).rsplit(".", 1)[0]
    va = state.setdefault("goalarm_virgin_tried", {})
    if va.get(sid):
        return logs
    hg = state.setdefault("goalarm_hasgoal", {})
    if hg.get(sid):
        return logs
    if not templates:
        return logs
    loc = os.path.basename(cwd.rstrip("/"))
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
        idx = airuleset.AUTHORITY_PROFILES.index(authority)
        full = templates[idx]
    except Exception as e:
        # #320-review MINOR-6 — the `backlog_marker_gate` sibling import a
        # few hundred lines away logs its own degraded mode rather than
        # swallowing it silently; this import deserves the same discipline
        # (an authority/template-count mismatch must never disable this
        # whole path fleet-wide with zero journal trace).
        logs.append("goal-autoarm-virgin degraded (authority) %s (%s) -> %r"
                    % (pid, loc, e))
        return logs
    if pane_in_mode(pid, run):
        return logs
    # #339 -- a session with RECENT real human activity is never a virgin
    # candidate, regardless of what the (expensive) full-file scan below
    # would say. Checked here (cheaper than that scan, after every cheaper
    # gate above it) so a candidate mid-conversation is refused before the
    # scan is ever paid for. #101/#266 transient-refusal discipline: this
    # is a zero-keystroke SKIP-TRANSIENT -- `va`/`hg`/`ga` stay untouched,
    # so the very next sweep after the human leaves (or the window lapses)
    # retries and arms normally. The per-pane log is throttled to once per
    # continuous streak (mirrors `goalarm_noresolve`'s own dedup shape a
    # few lines up in the caller) and cleared the moment the check no
    # longer fires.
    rc = state.setdefault("goalarm_recentuser", {})
    recent, reason = _goal_autoarm_recent_human_activity(sid, tr[0], now)
    if recent:
        if not rc.get(pid):
            rc[pid] = True
            logs.append(
                "goal-autoarm SKIP-TRANSIENT (virgin) %s (%s) -> %s -- "
                "recent human activity, never overwrite a live "
                "conversation" % (pid, loc, reason))
        return logs
    rc.pop(pid, None)
    # #335-review F2 (MAJOR) -- this path is `_goal_never_armed`'s own
    # arming route (#320), reachable for a session that has never shown
    # ANY `/goal` marker at all -- but "never armed" and "never exited"
    # are different questions, and CC writes NO marker for `/exit` either,
    # so a session the user deliberately stopped (and which happens to
    # have never had a `/goal` before) sailed straight through
    # `_goal_never_armed`'s own check untouched, bypassing #335's whole
    # fix. `_goal_was_cleared_by_user` already handles the markerless case
    # correctly (falls straight to its own exit-vs-activity release check
    # when `mark` stays None) -- reused here rather than a second
    # exit-vs-activity comparison. Placed BEFORE the (comparatively
    # expensive) `_goal_never_armed` full-file scan, mirroring #320-review
    # MAJOR-3's own "cheap gates before an expensive read" ordering: both
    # of `_goal_user_exit_ts`/`_last_real_turn_ts` (which this reuses) are
    # tail-bounded, cheaper than a full-file marker scan.
    if _goal_was_cleared_by_user(tr[0]):
        return logs
    if not _goal_never_armed(tr[0]):
        hg[sid] = True
        return logs
    ga = state.get("goalarm") or {}
    if dry_run:
        ga[pid] = int(now)
        state["goalarm"] = ga
        logs.append("goal-autoarm READY (virgin, %s) %s (%s)"
                    % (authority, pid, loc))
        return logs
    fresh = run(["tmux", "capture-pane", "-p", "-J", "-t", pid]) or ""
    if _input_line_text(fresh) != "":
        logs.append("goal-autoarm SKIP-TRANSIENT (virgin) %s (%s) -> pane "
                    "no longer bare" % (pid, loc))
        return logs
    va[sid] = True
    ga[pid] = int(now)
    state["goalarm"] = ga
    ok = _send_goal_verified(pid, full, run, captured=fresh,
                             sleep_fn=sleep_fn, logs=logs)
    logs.append("goal-autoarm %s (virgin, %s) %s (%s)"
                % ("OK" if ok else "FAIL", authority, pid, loc))
    return logs


def goal_autoarm(now, run, state, dry_run=False, projects_dir=None,
                 time_fn=None, sweep_deadline=None, sleep_fn=None,
                 templates_path=None):
    """Job 9 — see the section comment. Mutates state['goalarm']; returns log
    lines. Best-effort (never raises).

    #255 Fix 1: same gap class as bounce_backstop (job 8) — this loop had no
    wall-clock self-bound of its own either. `time_fn`/`sweep_deadline`
    (both optional, default None -> unbounded, today's behavior) are
    checked strictly BETWEEN panes, before a pane's own delivery starts —
    never nested inside one pane's own capture/classify/deliver sequence.
    A deferred pane writes no dedup state, so it is retried next sweep.

    #266 Defect 1: `sleep_fn` (default None -> real `time.sleep`) is
    threaded into the plain (bare-box) branch's verified-delivery
    primitive, mirroring `goal_rearm`'s own signature — tests stub it to
    avoid real sleeps.

    `templates_path` (optional, #320 shape 2): wired = on, like job 20's
    own identically-named param — without it (production default None)
    `load_goal_templates` returns `[]` and the NEW virgin-candidate branch
    below is a guaranteed no-op (`if not templates: return`), so this job
    behaves exactly as it did before this ticket."""
    ga = state.get("goalarm") or {}
    logs = []
    projects_dir = projects_dir or PROJECTS_DIR
    time_fn = time_fn or time.monotonic
    sleep_fn = sleep_fn or time.sleep
    templates = load_goal_templates(templates_path)
    panes = list_claude_panes(run, dry_run=dry_run)
    # #320-review MAJOR-2 -- computed ONCE from this sweep's own full pane
    # list (cheap, no new tmux call): a cwd shared by MORE THAN ONE live
    # pane makes "the newest transcript in this cwd's dir" an ambiguous
    # pairing for the virgin-candidate path below (which pane actually
    # HOSTS that transcript is not otherwise knowable) -- refused outright
    # rather than guessed.
    cwd_counts = collections.Counter(c for _, c in panes)
    for idx, (pid, cwd) in enumerate(panes):
        if sweep_deadline is not None and time_fn() >= sweep_deadline:
            logs.append("goalarm-budget-exceeded — %d/%d panes handled "
                        "this tick, rest retried next" % (idx, len(panes)))
            break
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
            # #320 shape 2 (montalu2) — a session doing ORDINARY work never
            # prints the arm question above, so it never reaches anything
            # below this point either; this is its ONLY other path to ever
            # getting a `/goal` loop at all. See the helper's own docstring
            # for the full gate (never fights a #170 clear, never retried).
            logs += _goal_autoarm_virgin_candidate(
                now, run, state, pid, cwd, templates, projects_dir,
                dry_run, sleep_fn, cap,
                unique_cwd=(cwd_counts[cwd] <= 1))
            continue
        # NB: an armed-goal indicator (◎ /goal) does NOT block — a resolved
        # /goal cycle re-prints the arm question while the OLD indicator is
        # still lit, and typing /goal safely replaces the old goal (the gk
        # re-arm incident, 2026-07-20). The tail arm question is authoritative.
        #
        # What DOES block is a goal the user explicitly CLEARED (#170): a
        # `/goal clear` leaves the arm question and the printed `/goal` line on
        # screen, so a viewport-only decision re-armed the very loop they had
        # just switched off. That signal is not in the viewport at all — it is
        # the transcript's newest goal marker, read below once the session is
        # resolved. The indicator stays ignored; the marker is the addition.
        busy_tail = _BG_AGENTS_WAIT_RX.sub("", tail)
        if "esc to interrupt" in busy_tail or "Waiting for" in busy_tail:
            continue                       # live work on screen — not at rest
        if pane_in_mode(pid, run):
            continue                       # copy-mode / other non-text mode
        # #100 — a BARE prompt arms directly; a FOREIGN DRAFT (incl. the
        # user's own half-typed /goal paste — the live incident this ticket
        # was filed from, a 43-minute idle gap while the pane held exactly
        # that) is never overwritten, but is no longer a dead end either: it
        # goes through the SAME stash-around delivery job 20's revival path
        # already uses (`deliver_with_stash`), never a second, invented
        # mechanism. Only a genuinely busy/undeterminable boundary skips.
        kind, draft = _classify_boundary(cap)
        if kind != "input":
            continue
        loc = os.path.basename(cwd.rstrip("/"))
        # #266 Defect 2: a literal `/goal …` line printed IN THE VIEWPORT
        # used to be a HARD requirement here, enforced BEFORE anything else
        # was even attempted — but the payload is TRANSCRIPT-sourced
        # whenever a transcript resolves (below), so this regex is only
        # ever needed as the LAST-RESORT fallback (no local transcript AND
        # no foreign transcript). A held draft — including job 9's OWN
        # earlier stuck paste (defect 1) — visually pushes the printed
        # `/goal ` line out of the CURRENT viewport, so requiring it up
        # front turned that draft into a PERMANENT dead end: `ga[pid]`
        # never gets set (arming never proceeds far enough to reach it), so
        # the identical doomed check re-runs every sweep forever with ZERO
        # journal trace (live, spinbike 2026-08-06 — 1h24m+ with a hanging
        # draft and not one relevant log line). `goals`/`frag` are now
        # computed lazily and every skip below LOGS why.
        goals = re.findall(r"^\s*(/goal \S.*)$", cap, re.M)
        frag = goals[-1].strip() if goals else None
        # The rendered viewport hard-wraps long goals — arm the TRANSCRIPT's
        # exact bytes when available; the fragment only when provably whole.
        #
        # Adversarial-review finding (post-#266, MINOR, narrow): with the
        # viewport `/goal` line no longer REQUIRED (defect 2 above),
        # `find_active_transcript` still returns the newest `.jsonl` in
        # this `cwd`'s dir regardless of which SESSION the pane actually
        # hosts — a second window/process sharing the same cwd with a
        # materially different `/goal` text could, in principle, get its
        # newest line armed into THIS pane with no on-screen cross-check at
        # all (the gk 2026-07-20 "stale/wrong goal" incident class). Not
        # observed and not realistically constructible today (same-repo
        # `/goal` templates are near-identical across sessions), so left as
        # a documented residual rather than adding session-id matching
        # (`_find_pane_for_session`'s stronger, pane<->transcript-stem
        # discipline) here.
        full = None
        tr = find_active_transcript(projects_dir, cwd)
        if tr:
            sid = os.path.basename(str(tr[0])).rsplit(".", 1)[0]
            cl = state.setdefault("goalarm_cleared", {})
            if _goal_was_cleared_by_user(tr[0]):
                # #170 — the user turned this loop OFF. Leave it alone until
                # they arm it again themselves, which flips the newest marker
                # back to `set` and re-enables this pane on the next sweep.
                # Logged ONCE per session: a cleared session is otherwise a
                # permanent resident and would print this every sweep, forever
                # (the `untracked_logged` precedent in _goal_template_drift).
                if not cl.get(sid):
                    cl[sid] = True
                    logs.append(
                        "skip cleared (goal-autoarm) %s (%s) -> the user "
                        "cleared this goal; not re-arming until they arm it "
                        "again" % (pid, loc))
                continue
            # No longer cleared — drop the bookkeeping rather than leaving a
            # key per session forever. The state file is long-lived and this
            # dict would otherwise only ever grow.
            cl.pop(sid, None)
        if tr:
            full = _transcript_goal_line(tr[0])
        if full is None:
            # sudo-hosted stream pane: the transcript lives under the FOREIGN
            # user's HOME — read it via sudo -n (best-effort).
            full = _foreign_transcript_goal(cwd)
        if full is None:
            if frag is None:
                # Adversarial-review finding (post-#266, cosmetic): unlike
                # "skip cleared" above (logged once per session, then
                # quiet), a pane that can NEVER resolve a goal anywhere (no
                # local transcript, no foreign transcript, no printed
                # `/goal` line either) re-hits this branch every 60s sweep
                # forever. Keyed on `pid` -- there is no `sid` here by
                # definition, since neither transcript resolved -- logged
                # ONCE per pane per continuous streak; ARMING itself is
                # UNCHANGED and keeps silently retrying every sweep (the
                # whole point of the fix: recover the instant a transcript
                # later resolves), only the noisy repeat LOG is suppressed.
                nr = state.setdefault("goalarm_noresolve", {})
                if not nr.get(pid):
                    nr[pid] = True
                    logs.append(
                        "skip no-goal-on-screen (goal-autoarm) %s (%s) -> "
                        "no transcript payload resolved and no printed "
                        "/goal line visible in the viewport -- nothing to "
                        "arm" % (pid, loc))
                continue
            if _viewport_goal_wrapped(cap, frag):
                logs.append("goal wrapped + no transcript — not arming %s (%s)"
                            % (pid, loc))
                continue
            full = frag
        # A goal resolved (from the transcript OR the viewport fallback) —
        # this pane is no longer in the "nothing to arm anywhere" streak,
        # so the log-once marker above is stale; drop it rather than
        # leaving a key per pane forever (mirrors the `cl.pop(sid, None)`
        # cleanup for the cleared-goal bookkeeping just above).
        state.get("goalarm_noresolve", {}).pop(pid, None)
        if draft:
            if dry_run:
                logs.append("goal-autoarm READY (stash) %s (%s)" % (pid, loc))
                continue
            dlogs = []
            ok = deliver_with_stash(pid, full, run, captured=cap, logs=dlogs)
            if not ok and dlogs and dlogs[-1] in _GOAL_REARM_TRANSIENT_REASONS:
                # never touched the pane (still mid-typing, another stash in
                # flight) — NOT counted against the per-pane dedup window, so
                # it is retried next sweep instead of waiting out the full
                # GOAL_ARM_WINDOW_S (the exact #101 lesson, applied here).
                logs.append("goal-autoarm SKIP-TRANSIENT (stash) %s (%s) -> %s"
                            % (pid, loc, dlogs[-1]))
                continue
            ga[pid] = int(now)
            state["goalarm"] = ga
            status = "OK" if ok else "FAIL"
            reason = (", %s" % dlogs[-1]) if (dlogs and not ok) else ""
            logs.append("goal-autoarm %s (stash%s) %s (%s)"
                        % (status, reason, pid, loc))
            continue
        # #266 Defect 1: this branch used to fire-and-forget
        # (`send_continue`) — `send-keys -l` + an IMMEDIATE Enter, no
        # settle/verify poll — the exact paste-collapse render race that
        # left a long /goal payload sitting unsubmitted after the type
        # never got a chance to render before Enter fired (live, spinbike
        # 2026-08-06). Job 20 and job 9's OWN stash branch above already
        # use a verified primitive; this plain branch was the one path
        # left unguarded.
        if dry_run:
            ga[pid] = int(now)
            state["goalarm"] = ga      # key exists only once something armed
            logs.append("goal-autoarm READY %s (%s)" % (pid, loc))
            continue
        # Adversarial-review finding (post-#266): `cap` above was captured
        # at the TOP of this pane's iteration, before
        # `_transcript_goal_line`/`_foreign_transcript_goal` (a sudo
        # subprocess, timeout 15s) ran — job 20's own bare-box sibling
        # re-verifies against a FRESH capture immediately before sending
        # for exactly this reason ("by now the sweep's own capture is
        # several tmux round-trips old, which is exactly the width of the
        # race"). Re-capture and re-check bareness HERE, right before the
        # send: a pane that stopped being bare in the interim (a) never
        # gets typed into at all and (b) is a PRE-SEND refusal — zero
        # keystrokes sent — so it must NOT consume the 10-minute dedup
        # window, the same carve-out the stash branch above already gets
        # via `_GOAL_REARM_TRANSIENT_REASONS` (the #101 lesson).
        fresh = run(["tmux", "capture-pane", "-p", "-J", "-t", pid]) or ""
        if _input_line_text(fresh) != "":
            logs.append("goal-autoarm SKIP-TRANSIENT %s (%s) -> pane no "
                        "longer bare" % (pid, loc))
            continue
        ga[pid] = int(now)
        state["goalarm"] = ga          # key exists only once something armed
        ok = _send_goal_verified(pid, full, run, captured=fresh,
                                 sleep_fn=sleep_fn, logs=logs)
        logs.append("goal-autoarm %s %s (%s)"
                    % ("OK" if ok else "FAIL", pid, loc))
    return logs


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
    would be visited twice per sweep (harmless — the consuming job's own
    per-session dedup sees the first visit's claim — but wasteful and noisy
    in the logs). Job 20 (goal re-arm) is the only consumer since #132
    removed jobs 12/18/23."""
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
    worker. Added for job 12 (#42), which #132 removed; job 20 (goal re-arm)
    is the remaining consumer — it must not paste a `/goal` into a pane whose
    background worker is mid-flight."""
    if not captured:
        return False
    if _BG_AGENTS_WAIT_RX.search(captured):
        return True
    for ln in captured.splitlines():
        if _AGENT_STRIP_ROW_RX.match(ln.strip()):
            return True
    return False


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
                      host=None, user=None, dry_run=False, usage_cache_path=None):
    """Job 13 — see the section comment. Guarded by `state['burn_snapshot_hour']`
    so the 60s sweep cadence writes AT MOST once per UTC-epoch hour, no matter
    how many times this fires inside that hour. `dry_run`: compute + log, but
    never write the file or claim the hour (so a later real sweep still
    writes it). Best-effort: exceptions are the caller's (run_once's)
    responsibility to catch, same as every other job.

    `usage_cache_path` (#269) is a thin pass-through to
    `burn.hourly_snapshot()`'s own param (which stamps `account_email` onto
    the row) — unset (the production default, `cmd_watchdog`'s real wiring)
    means the real local `~/.claude/airuleset-usage-cache.json`; tests pass
    an isolated path so they never read this box's own real cache file."""
    import burn as burn_mod
    hour_bucket = int(now // 3600)
    if state.get("burn_snapshot_hour") == hour_bucket:
        return []
    now_dt = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    row = burn_mod.hourly_snapshot(now_dt, root=transcripts_root, host=host, user=user,
                                   usage_cache_path=usage_cache_path)
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
# Job 29 — HOURLY CREDENTIAL-STORE SWEEP (#144). `airuleset.py secret` stores a
# credential 0600 under ~/.claude/secrets/ with a TTL, but the only thing that
# ever enforced that TTL was the next `secret` invocation — so the normal
# one-off shape (request, exec, never run it again) left the value on disk
# indefinitely, which is precisely the property the channel exists to provide.
# This box already runs a sweep every 60s; expiry belongs here rather than in a
# CLI nobody is obliged to call again. Detection-and-delete only: no keystrokes,
# no pings, nothing to a pane.
# --------------------------------------------------------------------------- #


def vault_purge_job(now, state, purge_fn=None, dry_run=False):
    """Job 29 — delete every stored credential past its TTL, at most hourly.

    `purge_fn` is injected (cmd_watchdog passes `filedrop.vault.purge`) so the
    job never imports a store path in a test, and so an existing caller that
    knows nothing about it sees no behavior change — the same "wired = on"
    convention as jobs 3/7/8/11/13.

    THE GRANULARITY IS THE HONEST TTL (#153 finding 3). The gate is an HOUR
    bucket, so a value whose `keep` is SHORTER than an hour outlives its own
    expiry: at the 60s minimum it can sit on disk for up to ~1h before this
    sweep reaches it. The CLI's opportunistic `purge()` shortens that only when
    someone happens to run `airuleset.py secret` in the meantime, which for the
    one-off shape of this feature is usually never. The guarantee is "a value
    does not lie on disk indefinitely" — never "it is gone the second its TTL
    passes". Anything needing the stricter property must call `secret forget`.
    """
    if purge_fn is None:
        return []
    hour_bucket = int(now // 3600)
    if state.get("vault_purge_hour") == hour_bucket:
        return []
    if dry_run:
        return ["[dry-run] vault-purge (not swept)"]
    gone = purge_fn() or []
    state["vault_purge_hour"] = hour_bucket
    if not gone:
        return []
    return ["vault-purge expired %d: %s" % (len(gone), ", ".join(gone))]


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


# #225 (adversarial-review hardening) — how long a PROVEN origin survives a
# later BLANK-origin `--record` call for the same session before it is
# treated as stale rather than preserved. See `record_compact_request`'s
# own docstring for why this has to be bounded at all (an unbounded
# preservation defeats `COMPACT_REQUEST_MAX_AGE_S`).
COMPACT_ORIGIN_PRESERVE_WINDOW_S = 120


def record_compact_request(session, cwd, now=None, path=None, msg_hash=None,
                           origin=None):
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
    and never participate in the delivered-dedup check.

    `origin` (#121, optional): WHAT proved this is a ticket boundary.
    `"subagent-stop"` means an autopilot-worker concluded with zero other
    live tasks in the session's own task registry; `"self-callback"` (#225)
    means the session itself asserted the boundary via `compact-request
    --self`. Both are evidence the DELIVERY gate (`_compact_not_at_boundary`)
    consults so a supervisor's `⏳` last line, which refers to the NEXT
    batch, stops vetoing the boundary of the ticket that just landed.
    Absent/blank = the Stop-hook origin, gated exactly as before.

    #225 — a BLANK origin never DOWNGRADES an already-recorded PROVEN one
    for the SAME still-pending session, PROVIDED that proven entry is still
    FRESH (within `COMPACT_ORIGIN_PRESERVE_WINDOW_S` of its own `ts`). The
    automatic Stop-hook's own `--record` call (blank origin, its unchanged
    default shape) fires again moments after e.g. a self-callback's bounded
    hold gives up without delivering — without preservation, that later
    call would silently overwrite the self-callback's trusted entry with an
    untrusted one, erasing exactly the proof job 14's later retry depends
    on. The freshness bound exists because #225's adversarial review found
    the UNBOUNDED form of this fix defeats `COMPACT_REQUEST_MAX_AGE_S`
    outright — a session producing repeated blank-origin `✅` boundaries
    could resurrect the SAME old proof indefinitely, laundering it onto a
    ticket boundary it says nothing about, well past the point any of it is
    still true. `COMPACT_ORIGIN_PRESERVE_WINDOW_S` (120s) comfortably covers
    the one real gap it exists for (a self-callback's own default 60s hold,
    plus the time until the next Stop-hook fire) without resurrecting
    anything materially stale. A call that supplies its OWN non-blank
    origin is never affected by any of this — it always wins outright, as
    before. `ts`/`cwd`/`msg_hash` still take the NEWER call's values either
    way (only the LATEST boundary matters, per the doc above); only the
    origin can be preserved instead of overwritten, and only within the
    window.

    #250-review (MAJOR) — `deferred_since` (set by job 14 the FIRST sweep it
    observes THIS session deferred on live tasks, `_session_has_live_bg_tasks`)
    is preserved across a re-record for the SAME still-pending session
    UNCONDITIONALLY, unlike `origin` above. `ts` is deliberately overwritten
    on every call (only the LATEST boundary matters), which means a session
    completing tickets FASTER than the live-tasks grace window (#250's own
    target population — a supervisor dispatching workers back to back) would
    otherwise reset its own grace anchor on every single re-record and never
    actually reach it, no matter how long it has genuinely gone un-compacted.
    No freshness window is needed here (unlike origin's, which exists to stop
    an OLD, unrelated proof from laundering onto a much LATER boundary it
    says nothing about): `deferred_since` answers a different question — "how
    long has THIS pending request specifically been stuck" — and it is
    already bounded by the entry's own lifetime: the moment the request is
    delivered, dropped, or expires, the whole entry (and this field with it)
    is removed, so there is nothing further to bound."""
    session = str(session or "").strip()
    if not session:
        return False
    now = time.time() if now is None else now
    d = load_compact_requests(path)
    prior = d.get(session)
    entry = {"cwd": str(cwd or ""), "ts": int(now)}
    if msg_hash:
        entry["msg_hash"] = str(msg_hash)
    if isinstance(prior, dict) and prior.get("deferred_since") is not None:
        entry["deferred_since"] = prior["deferred_since"]
    resolved_origin = str(origin or "").strip()
    if not resolved_origin:
        if isinstance(prior, dict):
            prior_origin = str(prior.get("origin") or "").strip()
            try:
                prior_age = float(now) - float(prior.get("ts"))
            except (TypeError, ValueError):
                prior_age = None
            if (prior_origin in _COMPACT_PROVEN_BOUNDARY_ORIGINS
                    and prior_age is not None
                    and 0 <= prior_age <= COMPACT_ORIGIN_PRESERVE_WINDOW_S):
                resolved_origin = prior_origin
    if resolved_origin:
        entry["origin"] = resolved_origin
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
# #99 (2026-07-27 live incident) — SUBSTANTIALITY GATE. The #48 context-size
# gate (COMPACT_BOUNDARY_MIN_CONTEXT, above) answers "is compacting worth it
# size-wise", not "did real work actually happen this boundary" — a
# long-running session's context climbs past 200K from ordinary back-and-forth
# regardless of whether any given turn did anything durable, so once a
# session is big enough EVERY completed-ticket-shaped turn (a one-line
# answer, filing a single ticket) also compacts. Live proof: user wrote
# "sprav ticket", Claude filed #602 and ended `✅ DONE:` — nothing was
# committed, merged, or deployed, yet `/compact` fired.
#
# A ticket boundary's durable state lives in git (the whole point of
# `notify-compact-request.sh`'s design comment: "whatever /compact discards
# AT THAT boundary is genuinely disposable" — true only when something was
# actually committed). So the signal this gate reads is COMMITS in the
# session's own `cwd` git repo, counted from an ANCHOR forward:
#   - the last time THIS repo actually had `/compact` delivered through this
#     gate (persisted in `compact-substantiality.json`, updated by
#     `mark_compact_boundary` on every real send), or
#   - failing that (first boundary ever seen for this repo), this SESSION's
#     own start time (its transcript's first timestamped entry) — never
#     "always permissive on first sight", or a repo that has literally never
#     had a real ticket done in it would compact on its very first trivial
#     Q&A turn.
#
# Exactly like the #48 context gate, this only ever BLOCKS on a POSITIVELY
# CONFIRMED zero-commits measurement — an unmeasurable case (cwd is not a
# git repo, git unavailable, no resolvable anchor) returns None and the
# caller falls through to the pre-#99 behavior (context gate only), never a
# block on "we don't know".
# --------------------------------------------------------------------------- #


def compact_substantiality_path():
    """`~/.claude/compact-substantiality.json` — {cwd: last_boundary_ts}, the
    per-repo anchor `compact_boundary_substantial` counts commits FROM.
    Resolved at CALL time, never a frozen module-level constant (matches
    every other compact-* path helper here)."""
    return Path.home() / ".claude" / "compact-substantiality.json"


def _load_compact_substantiality(path=None):
    path = path or compact_substantiality_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_compact_substantiality(d, path=None):
    path = path or compact_substantiality_path()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def mark_compact_boundary(cwd, now=None, path=None):
    """Record that a REAL `/compact` just fired for `cwd`'s repo, at `now`
    (default: current time) — the anchor the NEXT substantiality check
    counts commits from. A blank `cwd` is a no-op. Fail-safe (never
    raises). Returns True on success."""
    cwd = str(cwd or "").strip()
    if not cwd:
        return False
    now = time.time() if now is None else now
    d = _load_compact_substantiality(path)
    d[cwd] = float(now)
    return _save_compact_substantiality(d, path)


def _session_start_ts(tpath, head_bytes=200_000):
    """Epoch of the FIRST timestamped entry in the transcript at `tpath` —
    this session's own start time, the fallback anchor for a repo that has
    never had a `/compact` boundary recorded yet. Reads only the HEAD of
    the file (a session's opening entries), not the whole transcript. None
    when the file can't be read or carries no timestamped entry in that
    head window."""
    try:
        with open(tpath, "rb") as f:
            raw = f.read(head_bytes)
    except OSError:
        return None
    from datetime import datetime
    for ln in raw.splitlines():
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if not isinstance(e, dict):
            continue
        t = e.get("timestamp")
        if not t:
            continue
        try:
            return datetime.fromisoformat(
                str(t).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
    return None


def _last_boundary_ts(cwd, sid, projects_dir=None, path=None):
    """The anchor `compact_boundary_substantial` counts commits SINCE: the
    persisted last-real-boundary time for `cwd` if one exists, else this
    session's own start time (via its transcript), else None
    (unmeasurable — no anchor could be resolved at all)."""
    d = _load_compact_substantiality(path)
    ts = d.get(str(cwd or "").strip())
    if isinstance(ts, (int, float)):
        return float(ts)
    projects_dir = projects_dir or PROJECTS_DIR
    tpath = _transcript_for_session(projects_dir, sid, cwd)
    if tpath is None:
        return None
    return _session_start_ts(tpath)


def _default_git_run(argv, timeout=10):
    import subprocess
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _git_commit_count_since(cwd, since_ts, git_run=None):
    """Count commits reachable from HEAD in `cwd`'s repo with commit date
    on/after `since_ts` (epoch). None (unmeasurable) when `cwd`/`since_ts`
    is missing, `cwd` is not a git repo, or `git` itself is unavailable —
    NEVER a reason to block, only a reason to defer to the caller's
    pre-#99 fallback. An int (possibly 0) on a successful git call."""
    if not cwd or since_ts is None:
        return None
    from datetime import datetime, timezone
    git_run = git_run or _default_git_run
    since_iso = datetime.fromtimestamp(
        float(since_ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = git_run(["git", "-C", str(cwd), "rev-list", "--count",
                   "--since=" + since_iso, "HEAD"])
    if out is None:
        return None
    out = out.strip()
    try:
        return int(out)
    except ValueError:
        return None


def compact_boundary_substantial(cwd, sid, projects_dir=None, path=None,
                                 git_run=None):
    """#99 — did real work (>=1 commit) land in `cwd`'s repo since the
    substantiality anchor (see `_last_boundary_ts`)? Returns True (>=1
    commit — a genuine boundary), False (0 commits measured — NOT a
    boundary, e.g. a bare Q&A turn or a single filed ticket with no code
    change), or None (unmeasurable — not a git repo, git unavailable, or no
    anchor resolvable — caller must treat this exactly like "don't know",
    never as a block)."""
    since = _last_boundary_ts(cwd, sid, projects_dir=projects_dir, path=path)
    if since is None:
        return None
    n = _git_commit_count_since(cwd, since, git_run=git_run)
    if n is None:
        return None
    return n >= 1


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
# sender (the synchronous #65 path, job 14, and — until REMOVED, #102 —
# jobs 15/17) is blind to what any OTHER sender already queued during it.
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
#               three EVIDENCE paths (the #72 lesson, generalized) plus a
#               TTL backstop for the case none of them can reach (#140,
#               EXPIRED — below):
#                 CONSUMED -- the claimed session's OWN transcript carries a
#                   `compact_boundary` system entry (Claude Code's own
#                   durable "a real compaction landed" record — verified
#                   live against the camera-box incident transcript itself,
#                   entry at 2026-07-26T13:09:26.880Z) NEWER than the
#                   claim's send time. This is the #78-mandated proof —
#                   NEVER a context-threshold read (jobs 15/17's OWN
#                   internal state machines used that for their own
#                   re-trigger cadence — a SEPARATE, narrower concern from
#                   "did anyone already queue a /compact"; both jobs are
#                   REMOVED, #102, but the claim's own resolution rule is
#                   unchanged since it never depended on them existing).
#                 FAILED -- the claim's `cwd` now resolves to a DIFFERENT,
#                   NEWER session id than the one the claim was queued
#                   under (the pane went through a restart — `/exit` +
#                   relaunch always mints a fresh session id, so the old
#                   process holding the queued keystrokes is gone).
#                 EXPIRED (#140) -- none of the above can EVER fire for
#                   this claim and it is older than COMPACT_CLAIM_TTL_S.
#                   Live 2026-07-28, two boxes: process alive, session id
#                   unchanged (`claude -c`), and the queued `/compact`
#                   never drained (#84) so no boundary was ever written —
#                   21h26m of refused boundaries on montalu@subdev, ended
#                   by the USER hand-compacting, not by the mechanism.
#
# Deliberately did NOT replace job 15's `compact_stale` / job 17's
# `compact_ceiling` state machines (both REMOVED, #102) — those decided
# WHEN THEIR OWN job should next consider re-triggering (idle duration,
# context regrowth). This claim answers a narrower, independent question —
# "does ANY sender already have an outstanding, unconfirmed /compact for
# this session" — and gates every sender's send point, in ADDITION to
# (before) each job's own logic. Still true for the two surviving senders
# (job 14, the synchronous #65 path).
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


COMPACT_CLAIM_TTL_S = 1800       # 30 min; env AIRULESET_COMPACT_CLAIM_TTL_S


def _compact_claim_ttl(ttl=None):
    if ttl is not None:
        return ttl
    try:
        return int(os.environ.get("AIRULESET_COMPACT_CLAIM_TTL_S",
                                  COMPACT_CLAIM_TTL_S))
    except ValueError:
        return COMPACT_CLAIM_TTL_S


def compact_claim_active(sid, cwd, path=None, projects_dir=None, now=None,
                         ttl=None):
    """#78 — the SINGLE shared gate every `/compact` sender (the
    synchronous #65 path, job 14 — jobs 15/17, once also senders, REMOVED
    #102) MUST consult BEFORE typing `/compact` into `sid`'s pane, and
    BEFORE any of the sender's OWN logic (msg_hash dedup, context
    threshold, ticket boundary) runs. Returns True iff `sid` currently has
    an outstanding, UNRESOLVED claim — a send is FORBIDDEN.

    Reconciles the stored claim against reality on every call (never
    trusts a stale entry blindly), resolving it via the paths the section
    comment above describes — CONSUMED (a `compact_boundary` transcript
    entry newer than the send time), FAILED (the claim's process is gone,
    or the claim's cwd now belongs to a different, newer session — a
    demonstrated delivery loss), and, since #140, EXPIRED (older than
    `COMPACT_CLAIM_TTL_S`). Elapsed time is still never EVIDENCE: the
    three evidence paths are evaluated first and a claim inside the TTL
    stays queued however long it has been waiting. The TTL exists only so
    that a claim NONE of them can ever resolve cannot disable compaction
    for that session forever — the live 2026-07-28 wedge."""
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
    # a restart (jobs 12/18's `_restart_pane`, removed in #132) relaunched via
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
    send_ts = entry.get("ts")
    tpath = _transcript_for_session(projects_dir, sid, claimed_cwd)
    if tpath is not None:
        boundary_ts = _transcript_compact_boundary_ts(tpath)
        if (boundary_ts is not None and send_ts is not None
                and boundary_ts > send_ts):
            claims.pop(sid, None)
            _save_compact_claims(claims, path)
            return False                  # CONSUMED — eligible again
    # EXPIRED (#140) — the fourth resolution, and the ONLY one that can end a
    # claim nothing will ever be able to prove. Live wedge, two boxes, two
    # users, 2026-07-28: the claim's `claude` process stayed ALIVE (montalu
    # pid 3489717, up since 07-26 23:14), the session id never changed
    # (`claude -c` continues the same transcript), and the queued `/compact`
    # never drained — CC drains type-ahead only at an ACCEPTED Stop, and an
    # armed `/goal` loop keeps rejecting Stops (#84) — so no boundary was
    # ever written. All three checks above were structurally unavailable and
    # the claim refused every later ticket boundary `SKIP claim-queued` for
    # 21h26m (context 346,944) until the USER hand-compacted; forestshop@dev1
    # showed the identical signature at ~500K.
    #
    # #72's "never a timer" rule is right about what counts as PROOF and
    # wrong to conclude an unprovable claim may be held forever. The window
    # is generous by construction: #109 timed 11 real sends, 9 of which
    # started within ~6s of the keystroke and the worst parked +98s, so 30
    # min is ~18x the worst HEALTHY delivery and cannot fire on one.
    # Releasing is also nearly free, because a second guard already exists —
    # both send points (job 14, `deliver_compact_now`) check
    # `_pane_has_queued_compact` first, so a `/compact` genuinely still
    # parked in that pane is skipped as `SKIP queued-compact` rather than
    # typed twice. Worst case is ONE redundant `/compact`; the alternative,
    # measured, is a whole session that can never compact again.
    #
    # A claim whose `ts` cannot be read is treated the same way — it can
    # neither age out nor be proven, which is exactly the shape #83 already
    # drops for a missing "proc", and the failure direction is the same one
    # this ticket mandates: fail toward RELEASING.
    now = time.time() if now is None else now
    ttl = _compact_claim_ttl(ttl)
    try:
        expired = (now - float(send_ts)) > ttl
    except (TypeError, ValueError):
        expired = True                    # unusable ts — unprovable, release
    if ttl > 0 and expired:
        claims.pop(sid, None)
        _save_compact_claims(claims, path)
        return False                      # EXPIRED — eligible for a fresh send
    return True                           # still genuinely queued — inside the
                                           # TTL, elapsed time alone is still
                                           # not evidence (#72)


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
    grow unbounded on a long-lived box.

    #238-review-style finding 🔵F6 (this ticket's own review, proven live)
    -- a bounded-retry caller (`deliver_compact_record`/
    `deliver_compact_self`) can call `deliver_compact_now` several times
    in a row for the SAME sid/cwd, each attempt hitting the SAME
    early-return decision (e.g. a genuine `blocked-question` state that
    does not change between attempts a couple hundred ms apart) --
    measured live on gatekeeper: 5 IDENTICAL lines inside one 3-second
    hold. When the new line's own CONTENT (excluding its timestamp) is
    byte-identical to the log's current last line, this refreshes that
    line's timestamp in place instead of appending a duplicate -- so the
    bounded log's forensic window is spent on distinct DECISIONS, not
    repeated retries of the same one."""
    path = path or compact_sync_log_path()
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    existing = []
    try:
        existing = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    if existing and existing[-1].partition(" ")[2] == line:
        existing[-1] = "%s %s" % (ts, line)
    else:
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
# section comment above. Reuses the EXACT idle guards job 12 (MODEL RECONCILE,
# removed in #132) used: never busy, never mid-dialog, never with a draft
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
# 2026-08-08 (user directive, #333 recurrence): raised 200_000 -> 250_000.
# The always-on prefix (system prompt + rules + skills + agent-strip blocks)
# grew to the point where a supervisor session's POST-compact context already
# exceeds 200K, so the old floor never dropped anything — every recorded
# request delivered and the user saw "random" compacts all day. 250K gives
# real headroom between the post-compact baseline and the floor.
COMPACT_BOUNDARY_MIN_CONTEXT = 250_000

# #109 point 3 — a request that never found a safe delivery moment must LAPSE,
# not fire hours after the boundary that justified it. A request is only ever
# retried while the session stays away from a boundary (busy pane, occupied
# stash slot, a `⏳`/`❓` turn, no live pane at all); past this age the
# completion it was recorded for is simply no longer the session's present, so
# job 14 drops it with ZERO tmux interaction. Generous on purpose: this is a
# floor against a stale request, not a second delivery policy — the state
# gates above are what decide whether a fresh request is safe.
COMPACT_REQUEST_MAX_AGE_S = 30 * 60

# #250 (2026-08-05, live evidence: dev1 supervisor + gk) — the live-tasks
# defer (`_session_has_live_bg_tasks`, below) starves a SUPERVISOR-shaped
# session outright: a session that continuously dispatches background
# workers never has a quiet moment, so every recorded request cycles
# "skip live-tasks" on every sweep, forever, until COMPACT_REQUEST_MAX_AGE_S
# silently drops it -- zero real compactions, ever, for that session. This
# bounds the defer by TIME instead of leaving it unconditional: once a
# request has been sitting through a live-tasks defer for longer than this
# window, `/compact` is delivered anyway (see `_compact_live_tasks_in_grace`
# below) -- a short type-ahead-queue send, exactly the pre-#246 delivery
# shape, reliable even into a busy pane.
#
# MUST stay comfortably UNDER COMPACT_REQUEST_MAX_AGE_S above -- a grace >=
# the TTL recreates the exact starvation this fix exists to kill: the
# request would LAPSE (dropped, unfired) before its own grace window ever
# has a chance to elapse, so the "deliver anyway" branch below would never
# be reached for a session that never has a quiet moment. Locked by
# `TestCompactDeferGraceRelationship`, and by `_compact_defer_grace`'s own
# clamp on a misconfigured env override (#250-review MINOR).
#
# #250-review (MAJOR): "sitting through a live-tasks defer for longer than
# this window" is measured from the FIRST sweep that observed THIS pending
# request deferred (`deferred_since`, `record_compact_request`), never from
# the request's `ts` -- `ts` refreshes on every re-record, so a session
# completing tickets faster than this window would otherwise reset its own
# anchor forever and never actually reach it.
COMPACT_DEFER_GRACE_S = 300      # 5 min; env AIRULESET_COMPACT_DEFER_GRACE_S


def _compact_defer_grace(grace=None):
    """An explicit `grace=` (test/caller override) is returned verbatim,
    unclamped — every production call site leaves it `None`.

    #250-review (MINOR) — the CONSTANT/ENV-derived value is clamped to
    `[1, COMPACT_REQUEST_MAX_AGE_S)`: a misconfigured
    `AIRULESET_COMPACT_DEFER_GRACE_S` could otherwise silently disable the
    live-tasks safety defer outright (0 or negative -> every request reads
    as instantly "past grace") or recreate the lapse-before-grace
    starvation `COMPACT_DEFER_GRACE_S`'s own comment forbids (a value at or
    above the request TTL)."""
    if grace is not None:
        return grace
    try:
        raw = int(os.environ.get("AIRULESET_COMPACT_DEFER_GRACE_S",
                                 COMPACT_DEFER_GRACE_S))
    except ValueError:
        raw = COMPACT_DEFER_GRACE_S
    if raw < 1:
        return 1
    if raw >= COMPACT_REQUEST_MAX_AGE_S:
        return COMPACT_REQUEST_MAX_AGE_S - 1
    return raw


def _compact_live_tasks_in_grace(request_ts, now, grace=None):
    """#250 -- True while the live-tasks defer (`_session_has_live_bg_tasks`)
    should still apply: `now - request_ts` is under the grace window
    (`_compact_defer_grace`). Once deferred on live tasks for longer than
    this, the defer stops -- the caller delivers `/compact` anyway.

    `request_ts` is whichever anchor the CALLER passes -- job 14 passes its
    request's own `deferred_since` (the first sweep it observed THIS
    session deferred, preserved by `record_compact_request` across a
    re-record; see #250-review above for why the raw `ts` field cannot be
    used here), `deliver_compact_now`'s callers pass the `ts` their own
    `record_compact_request` call just wrote (see that function's own
    docstring for why its attempt is always in-grace in practice).

    A missing/unreadable `request_ts` is treated as IN-GRACE (True), never
    as "infinite age": guessing "past grace" from an unmeasurable value
    would start typing into a session with live siblings the FIRST time
    age can't be read, which is exactly the safety property this defer
    exists to protect. In real production `request_ts` is always a plain
    `int`/`float` timestamp a prior call already wrote, so an unmeasurable
    value here means corrupted/hand-edited state, not a normal condition --
    #250-review found `COMPACT_REQUEST_MAX_AGE_S`'s own expiry check does
    NOT reliably drop such an entry either (it treats an unparseable `ts`
    as age 0, i.e. never-expiring), so an entry in that state simply stays
    deferred until the underlying data is fixed or the session genuinely
    goes quiet -- never guessed either way."""
    try:
        age = float(now) - float(request_ts)
    except (TypeError, ValueError):
        return True   # unmeasurable age -- stay in-grace, never guess "past"
    return age < _compact_defer_grace(grace)


# #238 (2026-08-06) -- same-turn dispatch race. `_session_has_live_bg_tasks`'s
# two signals are both PROXIES with real propagation latency: a subagent
# transcript file only appears once the dispatched task's own process starts
# writing, and the pane's ambient "Waiting for N background agents" text only
# appears once CC re-renders. A task dispatched in the SAME turn that a
# DIFFERENT worker's SubagentStop fires (triggering THIS session's compact
# request) can be invisible to BOTH proxies for a short window right after
# dispatch -- and the synchronous delivery path (`cmd_compact_request
# --record` -> `deliver_compact_now`) evaluates `_session_has_live_bg_tasks`
# milliseconds after the request was recorded, i.e. always inside that
# window when it exists. Live incident: odoo-erp session sid f219f0e3,
# 2026-08-05 04:11:52Z -- compacted mid-`⏳ WORKING` while the pane itself
# still showed "Waiting for 1 background agent to finish".
#
# Scoped NARROWLY: this gate only matters on the "no live tasks found"
# branch -- a genuinely-live verdict (`_session_has_live_bg_tasks` == True)
# is completely unaffected, so the existing #246/#250 grace logic keeps its
# exact current behavior. `request_ts is None` (every pre-#238 caller) makes
# this a complete no-op, per the docstring below.
COMPACT_MIN_REQUEST_AGE_S = 2.0   # env AIRULESET_COMPACT_MIN_REQUEST_AGE_S


def _compact_min_request_age(min_age=None):
    """An explicit `min_age=` (test/caller override) is returned verbatim.
    The CONSTANT/ENV-derived default is clamped to a small positive floor --
    a misconfigured `AIRULESET_COMPACT_MIN_REQUEST_AGE_S` of 0 or negative
    would silently disable this gate outright (every request would read as
    already old enough), the identical clamp shape `_compact_defer_grace`
    already uses for the same reason."""
    if min_age is not None:
        return min_age
    try:
        raw = float(os.environ.get("AIRULESET_COMPACT_MIN_REQUEST_AGE_S",
                                   COMPACT_MIN_REQUEST_AGE_S))
    except ValueError:
        raw = COMPACT_MIN_REQUEST_AGE_S
    return raw if raw > 0 else COMPACT_MIN_REQUEST_AGE_S


def _compact_request_too_young(request_ts, now, min_age=None):
    """#238 -- True when `request_ts` is too fresh for a "no live tasks"
    verdict to be trusted yet (see the section comment above for the exact
    race this closes). Callers apply this ONLY on the branch where
    `_session_has_live_bg_tasks` already returned False -- a genuinely live
    signal is never affected by this function at all.

    A missing/unreadable `request_ts` (every pre-#238 caller, and any
    caller that genuinely cannot supply one) returns False -- "not too
    young" -- so this gate is a complete no-op for them, exactly the
    unchanged behavior every existing caller/test already relies on. This
    mirrors every other unmeasurable-never-blocks gate in this file, with
    one deliberate twist: here "unmeasurable" must default to NOT gating
    (rather than to gating, the usual direction) because the age check is
    itself an ADDITIONAL restriction layered on top of an already-passing
    verdict, not a new safety property in its own right."""
    if request_ts is None:
        return False
    try:
        now_ts = float(now) if now is not None else time.time()
        age = now_ts - float(request_ts)
    except (TypeError, ValueError):
        return False
    return age < _compact_min_request_age(min_age)


# #238 (2026-08-06) -- thin-context gate. Live evidence (gk sid f219f0e3,
# 2026-08-05, compact-sync.log + transcript): the msg-hash dedup layer (#71)
# treats ANY change in the supervisor's own `last_assistant_message` as a
# NEW distinct completion worth a fresh `/compact` request -- but a
# supervisor idling on a background worker produces a new hash on every
# trivial re-evaluation turn even when ZERO real conversational content was
# added since the last compaction actually landed. Measured directly:
# counting real `type=="assistant"` transcript entries strictly after the
# session's own newest `compact_boundary` entry, three real SENDs on that
# session showed 28 / 26 / 0 such entries respectively -- the first two
# landed real, distinct boundaries; the third (05:22:16Z) produced NONE (CC
# replied "Not enough messages to compact") and is the exact needless-resend
# shape this ticket names. No in-between case was observed, so the floor is
# the smallest value that admits any genuine activity at all.
#
# Applied UNCONDITIONALLY, unlike #99/#48 (which #126 exempts for a PROVEN
# boundary origin, because "the boundary is the ticket, not the size of its
# diff"): the live ZERO-activity send WAS `origin=subagent-stop` (a
# genuinely proven boundary by that same definition), so exempting proven
# origins here would leave the exact incident unfixed. This gate asks a
# different question than #99/#48 -- not "was the work worth compacting",
# but "did ANY new conversational state exist to compact at all", which is
# a precondition for compaction being possible, not a judgment about
# whether it was worthwhile.
COMPACT_THIN_CONTEXT_MIN_MESSAGES = 1


def _compact_messages_since_boundary(path, tail_bytes=4_000_000):
    """`(delta, boundary_ts)` -- `delta` is the count of real
    `type=="assistant"` transcript entries found strictly AFTER the newest
    `compact_boundary` entry in the file's bounded tail; `boundary_ts` is
    that boundary's own epoch, or None if no boundary was found in the
    scanned window at all (a session's first-ever compaction, or one
    further back than `tail_bytes` covers -- unmeasurable, never a reason
    to treat the request as thin).

    Bounded tail read (mirrors `_transcript_compact_boundary_ts`'s own
    shape) -- never loads a huge transcript whole. `boundary_ts is None`
    must never be read as "thin" by any caller -- there is nothing to
    measure a delta against, the same unmeasurable-never-blocks direction
    every other gate in this file uses.

    #238-review-style finding 🔵F5 (this ticket's own review) -- a
    boundary WAS found (`delta` genuinely reflects real activity since it)
    but its own `timestamp` field failed to parse (malformed/missing) is a
    THIRD case, distinct from "no boundary at all" -- `boundary_ts` reads
    `0.0` for it (found, epoch unknown), never `None`, so `delta` stays
    trustworthy for the caller instead of the whole read being discarded
    as unmeasurable."""
    from datetime import datetime
    try:
        with open(path, "rb") as f:
            try:
                f.seek(-tail_bytes, 2)
            except OSError:
                f.seek(0)
            raw = f.read()
    except OSError:
        return 0, None
    lines = raw.splitlines()
    boundary_idx = None
    boundary_ts = None
    for i, ln in enumerate(lines):
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
            ts = datetime.fromisoformat(
                str(e.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = None
        # JSONL is append-only chronological -- the LAST matching line found
        # is always the newest, no timestamp comparison needed.
        boundary_idx = i
        boundary_ts = ts
    if boundary_idx is None:
        return 0, None
    delta = 0
    for ln in lines[boundary_idx + 1:]:
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if isinstance(e, dict) and e.get("type") == "assistant":
            delta += 1
    # #238-review-style finding 🔵F5 (this ticket's own review, proven) --
    # `boundary_ts` can ALSO be None when a boundary WAS found
    # (`boundary_idx is not None`) but its own `timestamp` field failed to
    # parse (a malformed/corrupted line, or a missing field) -- the caller
    # keys "unmeasurable, never treat as thin" on `boundary_ts is None`
    # alone, which then wrongly disables the whole gate for a case where
    # `delta` is perfectly measurable. `0.0` distinguishes "found, epoch
    # unknown" from "not found at all" (`None`) without changing this
    # function's 2-tuple return contract every existing caller/test relies
    # on.
    if boundary_ts is None:
        boundary_ts = 0.0
    return delta, boundary_ts


def _compact_thin_context(cwd, sid, projects_dir=None, min_messages=None):
    """True when there is NOT enough real conversational activity since the
    session's last `compact_boundary` to justify another compaction -- see
    the section comment above. Unmeasurable (no transcript resolves, or no
    prior boundary exists yet for this session) never reads as thin --
    same fail-direction as every other gate in this file."""
    pdir = projects_dir or PROJECTS_DIR
    tpath = _transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return False
    delta, boundary_ts = _compact_messages_since_boundary(str(tpath))
    if boundary_ts is None:
        return False
    threshold = (COMPACT_THIN_CONTEXT_MIN_MESSAGES if min_messages is None
                else min_messages)
    return delta < threshold


# #67 (2026-07-26 live incident, david@subdev): a forgotten USER DRAFT sitting
# in the input box made job 14/15 log "skip draft" and retry FOREVER — the
# same draft (one unfinished sentence) skipped 13 sweeps straight overnight
# while the session's context grew 214K -> 449K with zero compactions. The
# fix already existed for a DIFFERENT problem (job 7's Discord-reply
# delivery, issue #35): `deliver_with_stash` parks the foreign draft via
# CC's native Ctrl+S stash, delivers `/compact`, and lets CC auto-restore
# the draft the instant the delivered turn completes — a draft is no longer
# a dead end for the compact job. Shared here so job 14 (and, until
# REMOVED #102, job 15) applied the identical policy (try stash -> on
# success proceed exactly like the no-draft path; on "slot already
# occupied" -> log a DISTINCT skip reason and count it, pinging the owner
# once every COMPACT_STASH_SKIP_PING_EVERY
# consecutive occupied-skips so a permanently jammed stash slot never rots
# silently, per the issue's acceptance).
COMPACT_STASH_SKIP_PING_EVERY = 3


def _compact_stash_attempt(pid, run, captured, state, sid, loc, project,
                           owner=None, ctx=None, send_fn=None, logs=None):
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
    this session.

    `logs` (optional, #271): the caller's own accumulating log list, so a
    draft rescued here (before the /compact keystroke) is journaled like
    every other job's decision rather than silently written with no trace
    in `run_once`'s own output."""
    delivered = deliver_with_stash(pid, COMPACT_TEXT, run, captured=captured,
                                   logs=logs)
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


def _compact_blocked_by_question(cwd, sid, projects_dir=None):
    """#102 (2026-07-27 live incident, camera-box) — True when the session's
    CURRENT last real turn ends on a `❓` status marker (NEEDS YOU / ASKED):
    genuinely blocked, in-flight work awaiting the user's answer that lives
    ONLY in context. Every `/compact` SENDER must consult this right before
    its own send point, not just once at record time: the record-time gate
    (notify-compact-request.sh) only ever sees the turn that JUST reported
    `✅ DONE` / `## ✅ Work Complete` — a request recorded there can still be
    sitting in compact-requests.json once the session has since moved on to
    a NEW `❓` turn (CC only drains its type-ahead queue at an ACCEPTED
    Stop, so a `/compact` queued behind a goal-loop-continued turn can land
    exactly as the NEXT turn asks its question — the reported incident:
    `❓ NEEDS YOU` fired, then `/compact` arrived before the user could
    answer). Unmeasurable (no resolvable transcript) never blocks — same
    "never block on don't know" philosophy as every other compact gate here
    (#48/#99)."""
    pdir = projects_dir or PROJECTS_DIR
    tpath = _transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return False
    return transcript_last_marker(tpath) == "❓"


# #109's own delivery-time marker set. #102 blocked only on `❓`; the reported
# incident executed while the turn read `⏳ WORKING: worker implementing #608`
# — a DISPATCHED worker whose in-flight state lives ONLY in context, which is
# the very thing a completed-ticket boundary is supposed to guarantee is
# already durable. Both markers are POSITIVE evidence that the session is not
# at a boundary; a missing/unreadable marker stays "don't know" and never
# blocks, exactly like #48/#99/#102.
_COMPACT_NON_BOUNDARY_MARKERS = ("❓", "⏳")

# #121 — the ORIGIN that carries its own proof of a ticket boundary, and the
# reduced marker set that applies to it.
#
# A supervisor whose work is performed by DISPATCHED workers can never reach
# the Stop-shaped boundary above: it reports batch N and dispatches batch N+1
# in the SAME turn, so the turn carrying the completed-ticket report always
# has a live worker and therefore always ends `⏳`. Measured
# (forestshop/parovanie_produktov, 2026-07-27/28): 19 hours with no
# compaction at 375K context, five turns carrying a `## ✅ Work Complete`
# heading inside a `⏳`-terminated message, and `compact-requests.json` empty
# — no request was ever even created.
#
# For such a session the durable boundary is a property of the TICKET, not of
# the supervisor's message: the `SubagentStop` of an `autopilot-worker` with
# ZERO other live tasks in the session's own task registry
# (`notify-compact-subagent-boundary.sh`). At THAT instant the worker's
# completion is already durable in git / GitHub / the issue — which is the
# entire justification for compacting at a ticket boundary — and the
# supervisor's `⏳` refers to the NEXT batch, not to the ticket that landed.
# `⏳ WORKING: next batch dispatched` is not evidence that anything undurable
# is in flight for the ticket that just completed; "a task this session owns
# is still running" is, and THAT is proven from the payload's
# `background_tasks` before the request is ever recorded.
#
# `❓` is deliberately NOT relaxed: a pending question is genuinely undurable
# state (the #102 camera-box incident compacted a `❓ NEEDS YOU` turn before
# the user could answer), and no worker's completion makes it durable.
_COMPACT_PROVEN_BOUNDARY_ORIGIN = "subagent-stop"

# #225 — a SECOND origin that proves its own boundary the same way: the
# SESSION ITSELF calling the new `compact-request --self` entry point,
# asserting "I am at a safe boundary right now" directly rather than having
# it re-derived later from transcript marker text (see `resolve_self_pane`/
# `deliver_compact_self` below). Both origins get IDENTICAL trust — a single
# frozenset so every consumer checks membership instead of `==` against one
# literal, which is the only change needed at each of the 4 call sites below.
_COMPACT_SELF_CALLBACK_ORIGIN = "self-callback"
_COMPACT_PROVEN_BOUNDARY_ORIGINS = frozenset(
    (_COMPACT_PROVEN_BOUNDARY_ORIGIN, _COMPACT_SELF_CALLBACK_ORIGIN))
# #333 removed the sibling `_COMPACT_NON_BOUNDARY_MARKERS_PROVEN = ("❓",)`
# constant here — every origin now shares the single
# `_COMPACT_NON_BOUNDARY_MARKERS` set (`_compact_not_at_boundary` below); a
# proven-boundary origin no longer gets a relaxed (⏳-exempt) marker set.

# The `user` entry Claude Code writes when a Stop hook REFUSES a turn's final
# message — verified against this box's own transcripts (2026-07-27): every
# rejection appears as a top-level `user` entry whose string content starts
# with exactly this prefix. It is the ONLY durable record of a refused Stop:
# `stop_hook_summary.preventedContinuation` is `false` on all 20,257 such
# entries in the local corpus and carries no rejection signal at all.
_STOP_FEEDBACK_PREFIX = "Stop hook feedback:"


def _compact_not_at_boundary(cwd, sid, projects_dir=None, origin=None):
    """#109 — the DELIVERY-time half of #102's gate. True when the session's
    CURRENT last real turn carries a marker that POSITIVELY says "this is not
    a completed-ticket boundary" (`❓` blocked on the user, `⏳` still working
    — a dispatched worker's state lives only in context).

    #102 re-checked only `❓`. The reported incident (#109, presenter,
    2026-07-27) executed on a `⏳ WORKING` turn with a live worker, several
    turns after the `✅ DONE` that had justified the request — so the request
    outlived its own boundary and the ❓-only gate waved it through. Every
    `/compact` sender consults this right before its own send point; a
    request it blocks is LEFT IN PLACE (never consumed) so the next sweep
    retries once the session genuinely returns to a boundary — or the entry
    expires (`COMPACT_REQUEST_MAX_AGE_S`). Unmeasurable never blocks.

    #121 (REVERSED by #333) — `origin` used to relax the marker set for a
    PROVEN-boundary origin (`⏳` no longer blocked, on the premise that a
    supervisor's `⏳` refers only to the NEXT batch and can never clear). Live
    forensic evidence on THIS box's own transcript (three occurrences in one
    day, #333) disproved the premise directly: `/compact` was typed while the
    pane was BUSY and only executed several turns later, at whatever turn's
    Stop was first ACCEPTED — under an active `/goal` loop that turn is almost
    always either a genuine completion or an ask-and-continue `❓`-block, and
    BOTH of this box's own confirmed-CLEAN historical sends landed exactly on
    a literal `✅ DONE` turn, never on `⏳`. So `⏳` is exactly as untrustworthy
    a boundary as `❓` for EVERY origin, proven and unproven alike — every
    origin now shares the single `_COMPACT_NON_BOUNDARY_MARKERS` set, with no
    per-origin relaxation. `origin` is kept as a parameter (unused by this
    function) only so callers don't need to change; the busy-pane send itself
    is what #333 actually closes (see `deliver_compact_now`/job 14)."""
    pdir = projects_dir or PROJECTS_DIR
    tpath = _transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return False
    return transcript_last_marker(tpath) in _COMPACT_NON_BOUNDARY_MARKERS


def _compact_session_unresumed(cwd, sid, projects_dir=None, origin=None):
    """#188 — True when a PROVEN boundary's result has demonstrably not been
    consumed yet, because the session's last real turn died on an API error.

    `notify-compact-subagent-boundary.sh` proves a boundary from one fact: this
    session's own task registry is empty apart from the stopping worker. That
    predicate is correct, and the justification it rests on is broader than the
    predicate — *a completed ticket's durable state already lives in git /
    GitHub, so the conversation is safe to summarise*. That holds for a ticket
    the supervisor VERIFIED, and verification is exactly the step that has not
    happened at the instant a worker returns.

    Normally it happens before the compaction anyway: CC drains its type-ahead
    queue only at a turn boundary, so the supervisor's next turn — the one that
    reads the worker's evidence block — runs FIRST and `/compact` lands after
    it. The reported incident (montalu@subdev, 2026-07-30) is the case that
    skips that step: the turn died on `API Error: 529 Overloaded`, ended
    without reading anything, and the queued `/compact` drained into a session
    whose most recent result was unprocessed. After compaction the supervisor
    would be verifying a ticket from a summary of a message it never saw.

    `_compact_not_at_boundary` cannot see this — it reads the `⏳`/`❓` status
    marker, and an api-error turn carries neither — so this is its sibling,
    consulted at the same send points and with the same contract: the request
    is LEFT IN PLACE, never consumed. Job 1 then sends its `continue`, the
    session resumes and consumes the evidence block, the next real assistant
    message clears the error, and the following sweep delivers the compaction.
    Self-healing, no new state, and still bounded by
    `COMPACT_REQUEST_MAX_AGE_S` so a permanently wedged session cannot hold a
    request forever.

    Scoped to the proven-boundary origin ON PURPOSE. Every other origin's
    request was justified by the supervisor's OWN `✅ DONE` turn, so its work
    was already consumed and reported; a later API error does not retroactively
    invalidate that boundary, and gating those too would be over-broad.

    Unmeasurable never blocks — the same fail-direction every other compact
    gate uses."""
    if origin not in _COMPACT_PROVEN_BOUNDARY_ORIGINS:
        return False
    pdir = projects_dir or PROJECTS_DIR
    tpath = _transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return False
    return bool(transcript_last_error(tpath))


# #246 (2026-08-05, live evidence: montalu@subdev) — the live-tasks SAFETY
# check `notify-compact-subagent-boundary.sh` used to apply at RECORD time
# (declining outright whenever a sibling worker was still live) now applies
# HERE, at the two DELIVERY points, exactly like every other "is it safe to
# type into this pane right now" gate above
# (`_compact_blocked_by_question`/`_compact_not_at_boundary`/
# `_compact_session_unresumed`). The safety property itself is unchanged —
# compacting while a SIBLING worker is mid-flight would drop that worker's
# own task linkage — only WHERE it is enforced moved: on a box running
# continuously OVERLAPPING autopilot-workers the zero-siblings moment the
# old record-time gate demanded almost never arrived, so the boundary was
# never even recorded and the compact-stall backstop (job 26) had nothing
# queued to watch. See the hook's own header for the full incident.
#
# Two INDEPENDENT signals, either one sufficient:
#   (a) the pane's OWN capture still shows CC's ambient "Waiting for N
#       background agents to finish" row (`_BG_AGENTS_WAIT_RX` — already the
#       job 9/20 bg-agent detection). It renders above the input box for as
#       long as the MAIN session has live background tasks, so it is a
#       direct, real-time read of the exact fact the old record-time gate
#       tried to observe from the SubagentStop payload.
#   (b) a SIBLING WORKER's own subagent transcript
#       (`<projects_dir>/<encode_project_dir(cwd)>/<sid>/subagents/*.jsonl`)
#       was written within the last `_LIVE_BG_TASK_WINDOW_S` seconds —
#       reuses `subagent_active` (already job 4's "is a dispatched worker
#       alive" signal), so a worker that is actively writing but whose pane
#       happens not to be showing the ambient row (a rare render-timing gap)
#       still counts. A JUST-FINISHED worker keeps this True for up to a
#       couple of sweeps after it stops — harmless over-deferral, deliberate:
#       the request simply retries next sweep.
#
# Fail direction, same as every other compact gate in this file: when
# NEITHER signal is readable (no pane id, the capture fails, no subagents
# dir at all) this returns False — deferral is an OPTIMIZATION of an
# already-real safety property, never itself a new way to block delivery on
# "we don't know".
#
# This function answers only "does live sibling work exist RIGHT NOW" — it
# says nothing about how LONG a request may be deferred on that basis. #250
# bounds that separately (`COMPACT_DEFER_GRACE_S` /
# `_compact_live_tasks_in_grace`, above): a session that never has a quiet
# moment must not be deferred forever.
_LIVE_BG_TASK_WINDOW_S = 120


def _session_has_live_bg_tasks(pid, sid, cwd, run, projects_dir=None, now=None,
                               captured=None):
    """True when EITHER signal above says this session still has background
    work in flight (see the section comment). `now` defaults to
    `time.time()` — tests pass a fixed value.

    `captured` (optional): the pane's ALREADY-KNOWN capture, when the caller
    has one in scope — both call sites do, by the time they reach this
    check. Reusing it (rather than issuing a fresh `capture_pane` call)
    matches this file's existing `cap = captured if captured is not None
    else capture_pane(...)` idiom, avoids a redundant tmux round-trip, and
    — the reason it MATTERS, not just an optimisation — job 14's own draft
    delivery path hands its FakeTmux a SEQUENCE of scripted `capture-pane`
    replies for `deliver_with_stash`'s own internal re-captures; an extra
    real capture-pane call inserted ahead of that sequence would silently
    consume its first entry and desync every reply after it. `captured=None`
    (the default) falls back to a fresh capture — used when nothing is
    already in scope (e.g. a direct/standalone call)."""
    if pid:
        cap = captured
        if cap is None:
            try:
                cap = capture_pane(pid, run, lines=40)
            except Exception:
                cap = None
        if cap and _BG_AGENTS_WAIT_RX.search(cap):
            return True
    pdir = projects_dir or PROJECTS_DIR
    try:
        tpath = _transcript_for_session(pdir, sid, cwd)
    except Exception:
        tpath = None
    if tpath is None:
        return False
    now_ts = now if now is not None else time.time()
    return bool(subagent_active(str(tpath), now_ts, _LIVE_BG_TASK_WINDOW_S))


def _stop_already_rejected(cwd, sid, projects_dir=None):
    """#109 — the ENQUEUE-time gate, and the ONE moment the reported incident
    is still preventable.

    True when the session's last real assistant message (the completed-ticket
    report a compact request is reacting to) is ALREADY followed by a
    `Stop hook feedback:` user entry — i.e. an earlier hook in this SAME Stop
    batch has refused it, so the turn does not end, the ticket boundary never
    happens, and CC will not drain its type-ahead queue (#84). Typing
    `/compact` into that pane does not compact anything now; it parks
    keystrokes that fire at some arbitrary LATER accepted Stop, in a state
    that would never have justified them.

    This is readable at enqueue time only because `notify-compact-request.sh`
    is ordered AFTER every `stop-check-*.sh` gate in the managed Stop chain
    (pinned by `TestCompactHookRunsAfterTheStopGates`), so their verdicts are
    already in the transcript when it runs. A hook that runs AFTER it — the
    session-scoped `/goal` hook does — is structurally invisible here; such a
    request is caught instead by `_compact_not_at_boundary` on the next
    delivery attempt. Unmeasurable never blocks (same philosophy as every
    other compact gate); a missed rejection just means today's behavior."""
    pdir = projects_dir or PROJECTS_DIR
    tpath = _transcript_for_session(pdir, sid, cwd)
    if tpath is None:
        return False
    entries = _iter_jsonl_tail(tpath)
    last_assistant = -1
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        if not isinstance(e, dict) or e.get("type") != "assistant":
            continue
        if (_entry_text(e) or "").strip() in _SENTINELS:
            continue    # synthetic / tool-only — keep scanning back
        last_assistant = i
        break
    if last_assistant < 0:
        return False
    for e in entries[last_assistant + 1:]:
        if not isinstance(e, dict) or e.get("type") != "user":
            continue
        msg = e.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and content.startswith(_STOP_FEEDBACK_PREFIX):
            return True
    return False


def compact_ticket_boundary(now, run, state, panes_by_sid, dry_run=False,
                            path=None, projects_dir=None, min_context=None,
                            send_fn=None, handled=None, delivered_path=None,
                            git_run=None):
    """Job 14 — see the section comment. `state` is used ONLY for #67's
    stash-skip counter (`compact_stash_skips`) — this job's own request
    dedup still lives entirely in the requests file, not in
    api-watchdog-state.json.

    #78: the SHARED `/compact` claim (`compact_claim_active`) is checked
    FIRST, before the #71 msg_hash dedup below — while another sender
    already has an outstanding claim for this sid, this request is dropped
    with ZERO tmux interaction, regardless of msg_hash/context. Every real
    send (idle or stash) below also SETS the claim via
    `compact_claim_set`, so the sync path sees it too.

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

    `handled` (#69, optional): a mutable set run_once passes so job 20 (goal
    re-arm) can see, WITHIN THE SAME SWEEP, which session ids this job
    already sent `/compact` into — and skip them, rather than relying
    solely on the pane's own "Compacting conversation" text (which can lag
    a beat behind the send). Every real send (direct or stash) records its
    sid here; a dropped/skipped request never does. (Originally also fed
    jobs 15/17 for the same reason — both REMOVED, #102.)

    #48 context-threshold gate: right before actually sending `/compact` for
    an otherwise-ready request, the session's CURRENT context is measured via
    `transcript_current_context()`, resolved for this specific sid+cwd via
    `_transcript_for_session` (falls back to a
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
    shape job 9's goal_autoarm uses (`/compact` has no
    reliable confirmation text to poll for). A request is LEFT IN PLACE
    (retried next sweep) whenever the pane is busy (see the #122 exemption
    below) / mid-dialog / in copy-mode / holding a draft whose STASH SLOT is
    already occupied by something else, or when no live pane maps to that
    session yet — "release the claim on failure so it retries" per the
    spec: there is no separate claim step to release, because nothing is
    ever claimed until the keystrokes are actually sent. A draft whose
    stash slot is FREE is no longer a dead end (#67): `_compact_stash_attempt`
    parks it, delivers `/compact`, and the request IS consumed on that
    success — the draft auto-restores itself once the delivered turn
    completes. Best-effort: exceptions are the caller's (run_once's)
    responsibility to catch, same as every other job.

    #122 (2026-07-28, REVERSED by #333): used to let a BUSY pane fall
    THROUGH for a proven-boundary origin instead of being an unconditional
    skip, on the "a short send-keys reliably queues even into a busy pane"
    finding (#65). #333's live forensic trace (three same-day incidents on
    this box's own supervisor session) proved that finding's SAFETY claim
    was too narrow: the queued keystrokes are mechanically safe to TYPE,
    but they only DRAIN (execute) at whatever LATER turn's Stop is first
    genuinely ACCEPTED — a moment the marker check at type-time cannot see
    or prevent — so a busy-typed `/compact` for a proven-boundary origin
    reproduced the identical incident job 14 was already safe from for
    every OTHER origin. A BUSY pane is once again an unconditional skip,
    for every origin, with no exemption. Job 14's own separate copies of
    the #99/#48 gates remain deliberately untouched by this reversal too —
    #126 scoped that parity gap out explicitly ("job 14's own separate
    copy of these same two gates is untouched"); #333 does not fold it
    back in either.

    #122 also fixes the OTHER half of the same ticket: a request that
    lapses via `COMPACT_REQUEST_MAX_AGE_S` (below) with no delivery, of
    ANY origin, now also writes a `"LAPSE"` line to `compact-sync.log` via
    `_log_compact_sync` — the same observable channel
    `deliver_compact_now` already uses for every send/drop decision —
    instead of only the pre-#122 journalctl `"skip expired"` line, buried
    among thousands of no-pane polls that nobody actually watches.

    #246 — the live-tasks SAFETY check the SubagentStop hook used to apply
    at RECORD time (declining the request outright) now applies HERE too,
    right before EITHER keystroke-sending branch below (the stash-around-
    a-draft path and the plain send), via `_session_has_live_bg_tasks`.
    The request is left in place (never consumed) on a defer — the next
    sweep retries once the sibling work clears, or the entry expires via
    `COMPACT_REQUEST_MAX_AGE_S` above.

    #250 — that defer is bounded by TIME, not unconditional: once the
    request has been sitting through a live-tasks defer for longer than
    `COMPACT_DEFER_GRACE_S` (`_compact_live_tasks_in_grace`), `/compact` is
    delivered anyway. A session shaped like a supervisor that continuously
    dispatches background workers never has a quiet moment, so an
    unconditional defer starves it completely — see that constant's own
    comment for the live evidence and the bounded-relationship invariant."""
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
        # #109 -- EXPIRY, checked before any tmux work: a request older than
        # COMPACT_REQUEST_MAX_AGE_S no longer describes the session's present,
        # so it lapses instead of firing at some unrelated later moment.
        try:
            age = float(now) - float(entry.get("ts") or 0)
        except (TypeError, ValueError):
            age = 0.0
        if entry.get("ts") and age > COMPACT_REQUEST_MAX_AGE_S:
            logs.append("skip expired (compact-request) %s age=%ds" % (sid, int(age)))
            if not dry_run:
                # #122 — a silent 30-minute lapse is a defect regardless of
                # which delivery branch a request fell through: write it to
                # the SAME observable channel deliver_compact_now already
                # uses for every send/drop decision
                # (compact-sync.log), not just a journalctl line buried
                # among thousands of no-pane polls that nobody watches.
                _log_compact_sync(
                    "LAPSE expired sid=%s cwd=%s age=%ds origin=%s"
                    % (sid, cwd, int(age), entry.get("origin") or "-"))
                reqs.pop(sid, None)
                changed = True
            continue
        # #78 -- the SHARED claim gate, checked FIRST and unconditionally:
        # if ANY sender (this job, or the synchronous #65 path) already has
        # an outstanding, unresolved /compact claim for this session, drop
        # this request with ZERO tmux interaction -- regardless of
        # msg_hash, context, or anything else below.
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
        # #102 -- never deliver while the session's CURRENT last turn is a
        # ❓ block: re-checked HERE (delivery time), not just at record
        # time -- see `_compact_blocked_by_question`'s docstring. Left in
        # place (never consumed) so the next sweep retries once the
        # question resolves (or the entry is superseded by a newer
        # ticket-boundary report for this same sid).
        if _compact_blocked_by_question(cwd, sid, projects_dir=pdir):
            logs.append("skip blocked-question (compact-request) %s" % loc)
            continue
        # #109 -- the general delivery-time boundary re-check: the request was
        # justified by a `✅` boundary that may be several turns in the past.
        # A session now on `⏳ WORKING` (a dispatched worker whose state lives
        # only in context) is NOT a boundary, and the ❓-only gate above waved
        # exactly that case through. Left in place, never consumed -- the next
        # sweep retries once the session is genuinely back at a boundary, or
        # the entry expires. #121 used to exempt a request carrying its own
        # boundary proof (`origin="subagent-stop"`) from the `⏳` half; #333
        # REVERSED that -- `⏳` blocks for EVERY origin now, here and in
        # `_compact_blocked_by_question` above (which was never relaxed by
        # #121 in the first place).
        if _compact_not_at_boundary(cwd, sid, projects_dir=pdir,
                                    origin=str(entry.get("origin") or "")):
            logs.append("skip not-a-boundary (compact-request) %s" % loc)
            continue
        # #188 — a PROVEN boundary whose result the supervisor demonstrably
        # never consumed (its turn died on an API error). Left in place, never
        # consumed: job 1's `continue` resumes the session, it reads the
        # worker's evidence block, and the next sweep delivers this.
        if _compact_session_unresumed(cwd, sid, projects_dir=pdir,
                                      origin=str(entry.get("origin") or "")):
            logs.append("skip unresumed-session (compact-request) %s" % loc)
            continue
        kind, draft = _classify_boundary(captured)
        if kind == "no-input-line":
            logs.append("skip no-input-line (compact-request) %s" % loc)
            continue
        # #122 (REVERSED by #333) — used to let a proven-boundary origin
        # bypass the busy-skip below, on the premise that "a short send-keys
        # reliably queues even into a busy pane" (#65) made typing into a
        # busy pane safe here. It IS mechanically safe to TYPE — but #333's
        # live forensic trace proved the real hazard is not about the TYPE,
        # it's that a busy-typed `/compact` sits QUEUED and only DRAINS
        # (executes) at whatever LATER turn's Stop is first genuinely
        # ACCEPTED — which the marker check at type-time cannot see or
        # prevent. So `/compact` is now only ever TYPED when the pane is
        # observably at rest RIGHT NOW, for every origin, with no exemption:
        # `kind == "busy"` always skips, regardless of `proven_boundary`.
        proven_boundary = (str(entry.get("origin") or "")
                           in _COMPACT_PROVEN_BOUNDARY_ORIGINS)
        if kind == "busy":
            logs.append("skip busy (compact-request) %s" % loc)
            continue
        # #238 — the thin-context gate, UNCONDITIONAL (never skipped for a
        # proven-boundary origin — see `_compact_thin_context`'s own section
        # comment: the live incident this exists for WAS
        # origin=subagent-stop). Checked BEFORE #99/#48 — mirroring
        # `deliver_compact_now`'s own ordering — and before a claim is ever
        # set, so nothing is left for `compact_claim_active`'s TTL fallback
        # to have to release.
        if _compact_thin_context(cwd, sid, projects_dir=pdir):
            logs.append("skip thin-context (compact-request) %s" % loc)
            if not dry_run:
                reqs.pop(sid, None)
                changed = True
                if mhash:
                    mark_compact_delivered(sid, mhash, path=delivered_path)
            continue
        # #99 — did REAL work (>=1 commit) happen since the last genuine
        # boundary for this repo? A positively-confirmed zero drops the
        # request outright, regardless of context size. Unmeasurable falls
        # through unchanged to the #48 context gate below.
        #
        # #301 — SKIPPED ENTIRELY for a `proven_boundary` request, mirroring
        # `deliver_compact_now`'s own #126 exemption exactly: `origin in
        # _COMPACT_PROVEN_BOUNDARY_ORIGINS` already PROVES a completed-ticket
        # boundary (an autopilot-worker's own SubagentStop with zero other
        # live tasks, or a session's own `compact-request --self` call) —
        # both #99's "zero commits" proxy and #48's "context too small" proxy
        # exist only to GUESS whether an anonymous Stop-hook turn was a real
        # boundary, a question `origin` already answers directly. #122/#126
        # deliberately scoped this parity gap OUT of job 14 at the time ("job
        # 14's own separate copy of these same two gates is untouched ...
        # this ticket does not fold it back in") — live evidence on gk and
        # david@subdev (#301) shows PROVEN requests that fail to deliver
        # synchronously (very common: `deferred=live-tasks`, often still
        # inside #250's own grace window) falling through to this poll and
        # getting dropped here anyway, exactly as if they were anonymous
        # turns, producing multi-hour compaction-free stretches despite
        # continuous completed-ticket activity.
        if not proven_boundary:
            substantial = compact_boundary_substantial(cwd, sid, projects_dir=pdir,
                                                        git_run=git_run)
            if substantial is False:
                logs.append("skip no-work (compact-request) %s" % loc)
                if not dry_run:
                    reqs.pop(sid, None)
                    changed = True
                    if mhash:
                        mark_compact_delivered(sid, mhash, path=delivered_path)
                continue
        # #48 — read the FRESHEST context right before deciding anything else
        # (it may have grown since the Stop hook recorded the request); a
        # session with no resolvable transcript is unmeasurable, so it does
        # NOT block the send (see the docstring above). Measured before the
        # draft branch (#67) so a trivial-context draft-holding session is
        # simply dropped, never stash-attempted for nothing.
        #
        # #301 — the BLOCKING check is SKIPPED ENTIRELY for a
        # `proven_boundary` request, same reasoning and same exemption set
        # as the #99 gate immediately above. `ctx` is unconditionally
        # initialized to `None` first, but the TRANSCRIPT READ itself is
        # skipped along with the rest of this block for a proven boundary —
        # it is consumed further below ONLY as an informational `ctx=ctx`
        # value for `_compact_stash_attempt`'s own diagnostic ping text,
        # never as a gate, so a proven boundary simply loses that one
        # cosmetic "(kontext N tokenov)" detail; nothing downstream treats
        # `None` as anything other than "unknown, omit it" (#301-review).
        ctx = None
        if not proven_boundary:
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
        if _pane_has_queued_compact(captured):
            # #84 — an unexecuted `/compact` is already queued in this pane;
            # it is what will satisfy THIS boundary request, so drop the
            # request rather than leaving it pending for a duplicate send.
            logs.append("skip queued-compact (compact-request) %s" % loc)
            if not dry_run:
                reqs.pop(sid, None)
                changed = True
                if handled is not None:
                    handled.add(sid)
                if mhash:
                    mark_compact_delivered(sid, mhash, path=delivered_path)
            continue
        # #246 — the live-tasks SAFETY check, moved here from the
        # SubagentStop hook's old RECORD-time decline (see
        # `_session_has_live_bg_tasks`'s own section comment). Checked
        # immediately before EITHER keystroke-sending path below (the
        # stash-around-a-draft path and the direct send), after every one
        # of this job's own state-resolution gates above (#78/#71 dedup,
        # in-mode/dialog/❓/⏳/unresumed/busy/#99/#48/#84) — per this repo's
        # own #78 lesson, a new cross-cutting gate belongs at the send
        # point, never at the top of the loop.
        #
        # #250 — bounded by TIME: the defer applies only while this
        # request is still within COMPACT_DEFER_GRACE_S of the FIRST sweep
        # that observed it deferred (`deferred_since`, stamped below the
        # first time this branch fires for a given pending request, and
        # PRESERVED across a re-record by `record_compact_request` — see
        # its own #250-review docstring). Still in grace -> left in place,
        # retried next sweep, exactly like every other "not safe RIGHT NOW"
        # skip in this loop. Past grace -> `grace_elapsed` marks every
        # OK/READY line below so a field audit of journalctl can tell a
        # genuinely-clear delivery apart from one that fired anyway.
        #
        # #250-review (MAJOR) — this must NOT anchor on the entry's own
        # `ts` field: `ts` is overwritten on EVERY re-record (only the
        # latest boundary matters, by design), so a session completing
        # tickets faster than the grace window — the exact population this
        # fix exists to un-starve — would otherwise reset its own grace
        # anchor every time and never actually reach it, no matter how long
        # it has genuinely gone un-compacted.
        # #238 — the same-turn dispatch race the min-request-age gate closes
        # (`_compact_request_too_young`) is specific to the SYNCHRONOUS path
        # (`deliver_compact_now`, called moments after the request is
        # recorded — see that gate's own section comment). Job 14 is an
        # independent ~60s poll; its own request is always well past that
        # window by the time it is evaluated here, so the gate is
        # deliberately NOT duplicated in this loop — it would be a pure
        # no-op in production while still coupling this job's tests to a
        # timing assumption ("evaluated long after recording") that a fast
        # unit test cannot cheaply reproduce.
        grace_elapsed = False
        if _session_has_live_bg_tasks(pid, sid, cwd, run, projects_dir=pdir,
                                      now=now, captured=captured):
            deferred_since = entry.get("deferred_since")
            if deferred_since is None:
                deferred_since = now
                entry["deferred_since"] = deferred_since
                reqs[sid] = entry
                changed = True
            if _compact_live_tasks_in_grace(deferred_since, now):
                logs.append("skip live-tasks (compact-request) %s" % loc)
                continue
            grace_elapsed = True
        grace_tag = ", grace-elapsed" if grace_elapsed else ""
        if draft:
            # #67 — a forgotten draft must not permanently block compaction:
            # stash it around the /compact delivery instead of skipping
            # forever.
            if dry_run:
                logs.append("READY (compact-request, draft%s) %s"
                            % (grace_tag, loc))
                continue
            project = project_label(cwd)
            from notify import stream_redirect  # #212/#270: STREAM_NOTIFY_OWNER-aware
            owner = stream_redirect(pane_owner(pid, run))
            if _compact_stash_attempt(pid, run, captured, state, sid, loc,
                                      project, owner=owner, ctx=ctx,
                                      send_fn=send_fn, logs=logs):
                reqs.pop(sid, None)
                changed = True
                if handled is not None:
                    handled.add(sid)
                if mhash:
                    mark_compact_delivered(sid, mhash, path=delivered_path)
                compact_claim_set(sid, cwd, pane_id=pid, run=run)  # #78/#82
                mark_compact_boundary(cwd)  # #99 — reset substantiality anchor
                logs.append("OK (compact-request, stash%s) %s"
                            % (grace_tag, loc))
            else:
                logs.append("skip draft (stash occupied) %s: %r"
                            % (loc, draft[:40]))
            continue
        # #333 -- `kind == "busy"` was `continue`d unconditionally above, so
        # `kind` here is ALWAYS "input" (the pre-#333 #122 dead-code branch
        # exempting a busy pane from this gate is gone; every request that
        # reaches here has a real idle-prompt to check, same as the plain
        # Stop-hook channel always had).
        if not pane_at_idle_prompt(captured):
            logs.append("skip not-idle (compact-request) %s" % loc)
            continue
        if dry_run:
            logs.append("READY (compact-request%s) %s" % (grace_tag, loc))
            continue
        # #333-review MAJOR-2 -- `captured` is the SWEEP-TOP snapshot handed
        # in via `panes_by_sid`, taken before this job even ran (job 14 runs
        # late in `run_once`'s per-sweep pane loop, after every other job's
        # own keystroke sends and a git subprocess in this same request's
        # own substantiality check above); it can be several tmux
        # round-trips stale by the time control reaches here. Re-verify
        # against a FRESH capture immediately before typing -- the same
        # discipline `_goal_template_drift`/job 20 and job 9's plain-branch
        # rescue already use for exactly this race (#176-F3/#266) -- so the
        # "only ever TYPED when the pane is observably at rest RIGHT NOW"
        # claim above is actually true at the moment of the send, not just
        # at the moment of the sweep-top read. A pane that moved on in the
        # interim is a PRE-SEND refusal (zero keystrokes sent) and must NOT
        # consume the request -- the next sweep retries.
        fresh = capture_pane(pid, run, lines=40)
        fresh_kind, fresh_draft = _classify_boundary(fresh)
        if fresh_kind != "input" or fresh_draft:
            logs.append("skip raced (compact-request) %s -> pane moved "
                        "since the sweep" % loc)
            continue
        send_continue(pid, COMPACT_TEXT, run)
        reqs.pop(sid, None)
        changed = True
        if handled is not None:
            handled.add(sid)
        if mhash:
            mark_compact_delivered(sid, mhash, path=delivered_path)
        compact_claim_set(sid, cwd, pane_id=pid, run=run)  # #78/#82
        mark_compact_boundary(cwd)  # #99 — reset substantiality anchor
        logs.append("OK (compact-request%s) %s" % (grace_tag, loc))
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
#
# #109 (2026-07-27, reported live from the presenter project) — the cost of
# that queuing behavior, and its bound. Because this path types DURING the
# Stop-hook batch, it acts BEFORE the turn's Stop verdict exists. When an
# earlier gate REFUSES the message the boundary never happens, CC never
# drains its type-ahead queue (#84), and the keystrokes execute at some later
# accepted Stop — the report had it fire several turns on, with a dispatched
# worker running and the turn ending `⏳ WORKING`.
#
# Measured on this box's 12 real sends (compact-sync.log, 2026-07-27;
# compaction START = boundary_ts - compactMetadata.durationMs): 9 sends with
# no rejection pending started within ~6s (genuinely atomic); all 3 made
# while a `Stop hook feedback:` entry already sat in the transcript started
# +24s / +77s / +98s later, every one with the marker moved to `⏳`.
#
# THERE IS NO WORK-COMPLETE CALLBACK TO USE INSTEAD, and this says so plainly
# rather than inventing something that merely looks atomic:
#   * the Stop hook fires BEFORE the accept/reject decision — that IS the bug,
#     not a detail of how we call it;
#   * #87 already proved by tracing the CC 2.1.220 call graph that a local
#     `/compact` returns `shouldQuery:false` and never reaches either call
#     site of the Stop-hook implementation, so a self-signalling `/compact`
#     is closed;
#   * once keystrokes are in CC's type-ahead queue they cannot be safely
#     recalled: the only key that clears the input box is Escape, which
#     INTERRUPTS a running turn (it would abort live work), and the whole
#     manoeuvre races the drain it is trying to beat.
# So the fix is PREVENTION AT ENQUEUE (`_stop_already_rejected`), plus a
# delivery-time re-check on every retry (`_compact_not_at_boundary`) and a
# lapse (`COMPACT_REQUEST_MAX_AGE_S`) — no new job, no new state machine.
# Residual, stated rather than papered over: a Stop refused by a hook that
# runs AFTER `notify-compact-request.sh` (the session-scoped `/goal` hook
# does) is invisible at enqueue time; such a request is caught only by the
# delivery-time gate on its next attempt.
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


def deliver_compact_now(sid, cwd, run=None, projects_dir=None, min_context=None,
                        git_run=None, origin=None, request_ts=None, now=None):
    """#65 — attempt to deliver `/compact` for `sid` SYNCHRONOUSLY, right when
    the ticket-boundary request is recorded (see the section comment above).

    `request_ts`/`now` (#250, both optional): the SAME grace-bound check job
    14 applies (`_compact_live_tasks_in_grace`) — `request_ts` is the `ts`
    the caller's OWN `record_compact_request` call just wrote for this exact
    `sid` (both callers thread it through: `cmd_compact_request`'s
    `--record` branch, `deliver_compact_self`'s retry loop). Left at the
    default `None` (every pre-#250 caller/test), a missing `request_ts` is
    treated as unmeasurable and therefore ALWAYS in-grace — i.e. UNCHANGED
    behavior: this function still always defers on live tasks, exactly as
    before. That default is also the CORRECT behavior for how this function
    is actually invoked in production: it runs SYNCHRONOUSLY, moments after
    the request was recorded with `ts=now`, so its own attempt is always
    in-grace when tasks are live (the "wait for a genuinely quiet moment
    first" preference) — the post-grace delivery belongs to job 14's own
    polled retry, which is why only ITS decision log carries the explicit
    `grace-elapsed` marker; see `COMPACT_DEFER_GRACE_S`'s own comment for
    the full reasoning and the live evidence that motivated it.

    Returns a non-empty STRING word when this session is FULLY HANDLED
    (either `/compact` was actually typed, or an existing claim / the #99 /
    #48 gates confirm nothing more needs to happen here) — the caller
    (`cmd_compact_request`) must NOT leave a pending request behind in
    either case, and (#125) now prints the word itself, verbatim, instead
    of a single generic "delivered" that could not distinguish a real send
    from a downstream drop. The word names the disposition:
    `"sent"` — `/compact` was actually typed.
    `"claim-queued"` — another sender already has an outstanding claim.
    `"queued-compact"` — the pane already holds an unexecuted `/compact`.
    `"dropped-no-work"` — the #99 gate: zero commits since the anchor.
    `"dropped-small-context"` — the #48 gate: context too small to bother.
    Returns `""` (falsy) when the caller should fall back to
    `record_compact_request` for job 14's polled retry: no pane resolves
    unambiguously, the pane is in copy-mode / showing an open dialog / has
    no locatable boundary at all, the session's CURRENT last turn is a ❓
    OR `⏳` block (#102 — `_compact_blocked_by_question`; #109/#333 —
    `_compact_not_at_boundary`, unrelaxed for every origin), the pane is
    currently BUSY (mid-turn — #333 REVERSED this function's own earlier
    "busy is safe to type into" premise, see the `kind == "busy"` check's
    own section comment below for the live evidence), the session STILL has
    live background work of its own (#246 —
    `_session_has_live_bg_tasks`, checked LAST, right before the send), the
    #238 thin-context gate reads zero real assistant activity since the
    last compact_boundary (the "Not enough messages" shape — #238-review
    🟡3: this SYNCHRONOUS path defers rather than drops, since the read
    can be a false zero from a not-yet-flushed transcript; job 14's own
    later re-check is what actually consumes a genuine thin boundary), or
    — the one case this function deliberately stays conservative on — the
    pane holds a genuine unsent DRAFT (job 14's `_compact_stash_attempt`,
    #67, handles that on retry; a synchronous multi-round-trip stash dance
    at Stop-hook time is unnecessary risk for what should be a rare case).

    #78 — checks the SHARED `/compact` claim FIRST, before anything else
    (no pane resolution, no tmux round-trip needed): if another sender
    already has an outstanding, unresolved claim for `sid`, this call sends
    NOTHING and returns `"claim-queued"` (handled — the outstanding claim
    is what will resolve this, not a second send). Every real send below
    sets the claim via `compact_claim_set`. Every decision this function
    makes (skip or send) is logged via `_log_compact_sync` — this is the
    ONLY trace of this path's behavior, since its caller (the Stop hook)
    used to throw stdout at /dev/null (#78's own incident was undebuggable
    from journalctl for exactly this reason; #125 finally made the Stop
    hook's own channel observable too).

    #126 — the #99/#48 substantiality gates below are SKIPPED ENTIRELY
    when `origin=="subagent-stop"`: that origin is itself the proof of a
    genuine completed-ticket boundary, so neither heuristic is needed or
    wanted for it (see the inline comment at the gates for the full
    reasoning, and the #126 issue comment for why NEITHER gate survives
    for that origin, not just the small-context one)."""
    run = run or _default_run
    projects_dir = projects_dir or PROJECTS_DIR
    if compact_claim_active(sid, cwd, projects_dir=projects_dir):
        _log_compact_sync("SKIP claim-queued sid=%s cwd=%s" % (sid, cwd))
        return "claim-queued"   # another sender already has this queued
    pid = _find_pane_for_session(sid, cwd, run=run, projects_dir=projects_dir)
    if not pid:
        _log_compact_sync("SKIP no-pane sid=%s cwd=%s" % (sid, cwd))
        return ""
    if pane_in_mode(pid, run):
        _log_compact_sync("SKIP in-mode sid=%s cwd=%s" % (sid, cwd))
        return ""
    captured = capture_pane(pid, run, lines=40)
    if pane_waiting_on_user(captured):
        _log_compact_sync("SKIP dialog-open sid=%s cwd=%s" % (sid, cwd))
        return ""
    # #102 -- never deliver while the session's CURRENT last turn is a ❓
    # block (see `_compact_blocked_by_question`'s docstring). Falls back to
    # job 14's polled retry, exactly like every other "unsafe right now"
    # state this function refuses on.
    if _compact_blocked_by_question(cwd, sid, projects_dir=projects_dir):
        _log_compact_sync("SKIP blocked-question sid=%s cwd=%s" % (sid, cwd))
        return ""
    # #109 -- the general form of the gate above: never deliver while the
    # session's CURRENT last turn positively says "not a boundary" (`⏳` too,
    # not just `❓`). #121 used to relax this for a request carrying its own
    # proof of a boundary (`origin="subagent-stop"`); #333 REVERSED that --
    # `⏳` blocks for EVERY origin now, proven or not (see
    # `_compact_not_at_boundary`'s own docstring for the live evidence).
    if _compact_not_at_boundary(cwd, sid, projects_dir=projects_dir,
                                origin=origin):
        _log_compact_sync("SKIP not-a-boundary sid=%s cwd=%s" % (sid, cwd))
        return ""
    # #188 -- a PROVEN boundary whose result the supervisor demonstrably never
    # consumed (its turn died on an API error). Deferred, not dropped: the
    # request stays on file and the next sweep delivers it once job 1's
    # `continue` has resumed the session and it has read the evidence block.
    if _compact_session_unresumed(cwd, sid, projects_dir=projects_dir,
                                  origin=origin):
        _log_compact_sync("SKIP unresumed-session sid=%s cwd=%s" % (sid, cwd))
        return ""
    # #109 -- the ENQUEUE-time gate, and the ONE moment the reported incident
    # is still preventable: this function runs INSIDE the Stop-hook batch, so
    # the boundary it is acting on may ALREADY have been refused by an earlier
    # hook. Typing `/compact` then does not compact anything now -- it parks
    # keystrokes CC will only drain at some LATER accepted Stop (#84), in a
    # state that would never have justified them. Fall back to job 14's polled
    # retry, which re-checks the session's then-current state.
    #
    # #225-review -- this gate's whole premise ("an EARLIER hook in THIS
    # Stop-hook batch already rejected THIS turn") does not hold for the
    # `self-callback` origin: `deliver_compact_self` calls this MID-TURN, as
    # the session's own Bash tool call, not from inside any Stop-hook batch
    # at all. Under an active /goal loop the PREVIOUS turn almost always DOES
    # have a `Stop hook feedback:` entry after it (that is what keeps the
    # loop going), so this check would misread that as "this boundary was
    # already refused" and refuse every retry for the WHOLE hold window --
    # confirmed by tracing the scan against a real transcript shape. Exempt
    # ONLY `self-callback`, not the whole proven set: `subagent-stop` keeps
    # its existing (untouched, out of this ticket's scope) behavior.
    if (origin != _COMPACT_SELF_CALLBACK_ORIGIN
            and _stop_already_rejected(cwd, sid, projects_dir=projects_dir)):
        _log_compact_sync("SKIP stop-rejected sid=%s cwd=%s" % (sid, cwd))
        return ""
    kind, draft = _classify_boundary(captured)
    if kind == "no-input-line":
        _log_compact_sync("SKIP no-input-line sid=%s cwd=%s" % (sid, cwd))
        return ""
    if draft:
        _log_compact_sync("SKIP draft sid=%s cwd=%s" % (sid, cwd))
        return ""
    # #333 -- `kind == "busy"` used to be considered SAFE to type into here
    # (a short send-keys reliably queues even into a busy pane, #65) -- true
    # mechanically, but this box's own forensic trace proved the real
    # hazard: a busy-typed `/compact` sits QUEUED and only DRAINS (executes)
    # at whatever LATER turn's Stop is first genuinely ACCEPTED, which under
    # an active `/goal` loop is almost always either a real completion or an
    # ask-and-continue `❓`/`⏳`-blocked turn -- exactly the boundary this
    # whole gate exists to refuse. The marker check above cannot see what a
    # currently-busy generation will eventually produce, so it cannot
    # prevent this. Refuse now and fall back to job 14's polled retry, which
    # re-checks the session's then-current (later, hopefully idle) state.
    if kind == "busy":
        _log_compact_sync("SKIP busy sid=%s cwd=%s" % (sid, cwd))
        return ""
    if _pane_has_queued_compact(captured):
        # #84 — the pane already holds an unexecuted `/compact`; a second one
        # would only answer "Not enough messages to compact" when the queue
        # finally drains. The queued one IS the handling.
        _log_compact_sync("SKIP queued-compact sid=%s cwd=%s" % (sid, cwd))
        return "queued-compact"
    # #238 -- the thin-context gate, UNCONDITIONAL (never skipped for a
    # proven-boundary origin — see the gate's own section comment for why:
    # the live incident this exists for WAS origin="subagent-stop"). Checked
    # before the #99/#48 gates, and before a claim is ever set, so a request
    # this drops here strands NOTHING for `compact_claim_active`'s TTL
    # fallback to have to clean up later.
    #
    # #238-review 🟡3 -- this SYNCHRONOUS path runs moments after the
    # triggering message was written, when the session's own transcript
    # write may not have flushed to disk yet -- a "zero real assistant
    # activity" read here can be a FALSE zero from an unflushed file, not
    # a genuine thin boundary. Returning "" (falsy, non-consuming) defers
    # to job 14's polled ~60s-later re-check instead of trusting this
    # read as final; job 14's OWN thin-context branch (see its section
    # comment) keeps consuming the request on a positive read, since by
    # then the transcript has had time to catch up.
    if _compact_thin_context(cwd, sid, projects_dir=projects_dir):
        _log_compact_sync("SKIP thin-context sid=%s cwd=%s" % (sid, cwd))
        return ""
    # #126 -- a request carrying its OWN proof of a boundary
    # (`origin=="subagent-stop"`) is EXEMPT from BOTH substantiality
    # heuristics below (#99 and #48). Both exist only to GUESS whether an
    # anonymous Stop-hook turn was a real, worthwhile boundary --
    # `origin=="subagent-stop"` already answers that question directly: an
    # autopilot-worker just concluded with zero other live tasks in the
    # session's own task registry (notify-compact-subagent-boundary.sh).
    # Both #99's "zero commits" proxy and #48's "context too small" proxy
    # can trip on a LEGITIMATE completed ticket that produced no diff at
    # all (closing an issue as already-fixed, a decision-only turn that
    # only filed a follow-up) -- and the user's own requirement is
    # unconditional ("autopilot ide ticket za ticketom a po kazdom tickete
    # ma prebehnut compact", #121): the boundary is the TICKET, not the
    # size of its diff. See the #126 issue comment for the full "why
    # no-work too, not just small-context" reasoning. Every OTHER origin
    # (the plain Stop-hook channel, and job 14's own separate copy of these
    # same two gates) is completely untouched by this.
    proven_boundary = origin in _COMPACT_PROVEN_BOUNDARY_ORIGINS
    if not proven_boundary:
        # #99 — did REAL work (>=1 commit) actually happen since the last
        # genuine boundary for this repo? A positively-confirmed zero drops
        # the request outright — a bare Q&A turn or a single filed ticket is
        # not a safe/worthwhile compaction boundary, no matter how large the
        # context has grown. Unmeasurable (not a git repo, no anchor) falls
        # through to the pre-#99 behavior below, unchanged.
        substantial = compact_boundary_substantial(cwd, sid, projects_dir=projects_dir,
                                                    git_run=git_run)
        if substantial is False:
            _log_compact_sync("DROP no-work sid=%s cwd=%s" % (sid, cwd))
            return "dropped-no-work"   # #99 gate: nothing durable happened
    # #333 -- `kind` is ALWAYS "input" by this point (bare, or the
    # queued-messages placeholder already normalized to bare): "no-input-line"
    # and "draft" both already returned above, and "busy" is now refused
    # above too (see that section comment for why "busy is safe to type
    # into" was reversed) -- so there is nothing left for a
    # `pane_at_idle_prompt` gate to add here.
    if not proven_boundary:
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
            return "dropped-small-context"   # #48 gate: nothing worth compacting
    # #246 — the live-tasks SAFETY check, moved here from the SubagentStop
    # hook's old RECORD-time decline (see `_session_has_live_bg_tasks`'s own
    # section comment). Checked LAST, immediately before the actual
    # keystroke send, after every other state-resolution step above — never
    # a reason to consume/drop the request, just to leave it in place for
    # job 14's polled retry once the sibling work clears.
    #
    # #250 — bounded by TIME, the identical check job 14 applies
    # (`_compact_live_tasks_in_grace`), using `request_ts` (see this
    # function's own docstring for why it is always in-grace in practice).
    grace_elapsed = False
    now_ts = now if now is not None else time.time()
    has_live = _session_has_live_bg_tasks(pid, sid, cwd, run,
                                          projects_dir=projects_dir,
                                          captured=captured)
    if has_live:
        if _compact_live_tasks_in_grace(request_ts, now_ts):
            _log_compact_sync("SKIP live-tasks sid=%s cwd=%s" % (sid, cwd))
            return ""
        grace_elapsed = True
    elif _compact_request_too_young(request_ts, now_ts):
        # #238 -- close the same-turn dispatch race: a "no live tasks"
        # verdict this fresh is not yet trustworthy (see
        # `_compact_request_too_young`'s own docstring). Left in place —
        # the CALLER retries (both `deliver_compact_self` and
        # `deliver_compact_record` hold briefly and re-attempt with a
        # FRESH `now`, so `request_ts` stays fixed while age genuinely
        # grows across retries), or job 14's polled retry re-checks well
        # past this window if the hold itself expires first.
        #
        # #238 adversarial-review (🟡6): no origin is exempt any more. The
        # earlier `self-callback` exemption rested on that origin's own
        # protocol never dispatching in the same turn — true, but (a) it
        # is a PROSE constraint nothing enforces, defeated by a parallel
        # tool-call batch, and (b) it was solving a problem that no
        # longer exists once the caller genuinely retries: self-callback
        # already re-attempts with a fresh `now_fn()` every
        # `retry_interval`, so its own SECOND attempt (a few seconds
        # later) clears this gate on real elapsed time, no exemption
        # needed. One gate, every origin, no prose to keep correct.
        _log_compact_sync("SKIP too-young sid=%s cwd=%s" % (sid, cwd))
        return ""
    # #333-review MAJOR-2 -- `captured` was resolved once, near the top of
    # this call, before every check above (the marker re-read, the #238/
    # #99/#48 gates, and the `_session_has_live_bg_tasks` subprocess check
    # immediately above) each spent real wall-clock time. Re-verify against
    # a FRESH capture immediately before typing -- the same discipline job
    # 14's own send point now uses (see its section comment) and
    # `_goal_template_drift`/job 20 established first (#176-F3/#266) -- so
    # the "only ever TYPED when the pane is observably at rest RIGHT NOW"
    # claim is true at the actual moment of the send, not just at this
    # call's own entry. A pane that moved on in the interim is a PRE-SEND
    # refusal (zero keystrokes sent) -- fall back to job 14's polled retry.
    fresh = capture_pane(pid, run, lines=40)
    fresh_kind, fresh_draft = _classify_boundary(fresh)
    if fresh_kind != "input" or fresh_draft:
        _log_compact_sync("SKIP raced sid=%s cwd=%s" % (sid, cwd))
        return ""
    send_continue(pid, COMPACT_TEXT, run)
    compact_claim_set(sid, cwd, pane_id=pid, run=run)  # #78/#82
    mark_compact_boundary(cwd)  # #99 — reset the substantiality anchor
    _log_compact_sync("SEND%s sid=%s cwd=%s"
                      % (" grace-elapsed" if grace_elapsed else "", sid, cwd))
    return "sent"


# --------------------------------------------------------------------------- #
# #225 (2026-08-04) — the SELF-CALLBACK entry point.
#
# The user's own directive (asked repeatedly before this ticket): at Work
# Complete the session must not immediately walk on to the next ticket — it
# should fire an explicit external command that asks the watchdog to deliver
# `/compact` into its OWN pane right now, holding (bounded) until it lands,
# rather than parking a request for a LATER sweep to re-derive a boundary
# that may have already moved on by the time that sweep runs.
#
# `airuleset.py compact-request --self` (wired below) is that command. It
# needs no `--session`/`--cwd` from the caller at all — unlike every other
# sender, which has to resolve an AMBIGUOUS "which pane hosts this sid"
# question (`_find_pane_for_session`, above), the session calling THIS
# command already IS the one true pane: `$TMUX_PANE` (tmux's own per-pane
# identity env var, set for every process running inside a tmux pane) names
# it directly, with zero matching/guessing involved.
# --------------------------------------------------------------------------- #

def resolve_self_pane(run=None, projects_dir=None, pane_env=None):
    """Resolve the EXACT pane/cwd/sid of the CALLING session for the
    self-callback entry point — never ambiguity-resolved by transcript
    matching the way `_find_pane_for_session` has to be, because the caller
    already knows precisely which pane it is.

    `pane_env` (optional, for tests) overrides `$TMUX_PANE`. Returns
    `(pane_id, cwd, sid)`; any element that could not be resolved is `""`:
    `pane_id==""` means `$TMUX_PANE` was absent (not running inside a tmux
    pane at all — this command is only ever meant to be invoked from one);
    `cwd==""` means the pane id is set but `list_claude_panes` does not
    recognize it as a `claude`/hosted-sudo pane (should not happen for a
    genuine self-call, but never guess); `sid==""` means the pane resolved
    but has no active transcript yet. The caller treats a blank `sid` as
    total failure — there is nothing safe to record or deliver without one."""
    run = run or _default_run
    pane_id = (pane_env if pane_env is not None
              else os.environ.get("TMUX_PANE", "")).strip()
    if not pane_id:
        return "", "", ""
    cwd = ""
    for pid, pcwd in list_claude_panes(run):
        if pid == pane_id:
            cwd = pcwd
            break
    if not cwd:
        return pane_id, "", ""
    pdir = projects_dir or PROJECTS_DIR
    tinfo = find_active_transcript(pdir, cwd)
    if not tinfo:
        return pane_id, cwd, ""
    tpath, _mtime = tinfo
    return pane_id, cwd, tpath.stem


COMPACT_SELF_HOLD_DEFAULT_S = 60
# #238-review-style finding 🟡F3 (this ticket's own review, proven live) --
# removing the self-callback exemption from the too-young gate (#238's own
# fix) means EVERY real self-callback call now blocks on age at least once
# (request_ts and the first now_fn() read are always microseconds apart),
# and the OLD value here (5) overshot COMPACT_MIN_REQUEST_AGE_S's default
# (2.0) by 3s on every single call -- traced with injected clocks: attempt
# 1 always fails too-young at t=0, attempt 2 (after ONE sleep of this
# interval) succeeds. Lowered to comfortably clear the default age gate
# with a small margin (never exactly 2.0 -- real `sleep()` should never
# undershoot, but there is no reason to shave it that fine) without
# meaningfully changing this function's OTHER retry scenarios (copy-mode
# clearing, a transient failure) -- those still get up to
# COMPACT_SELF_HOLD_DEFAULT_S / this interval attempts, just more of them.
COMPACT_SELF_RETRY_INTERVAL_S = 2.5

# #238-review-style finding 🔵F8 (this ticket's own review) -- safety
# ceilings for `_compact_retry_until`, well past every legitimate caller's
# own hold (self-callback's 60s is the longest today).
COMPACT_RETRY_HOLD_CEILING_S = 600
COMPACT_RETRY_MIN_INTERVAL_S = 0.1


def _compact_retry_until(attempt_fn, hold_s, retry_interval, clock_fn=None,
                         sleep_fn=None):
    """Call `attempt_fn()` (no args, returns a `deliver_compact_now`-shaped
    disposition word or a falsy value) repeatedly until it returns truthy
    or `hold_s` elapses, bounded on a MONOTONIC clock (#225-review: a
    wall-clock hold is sensitive to a backward NTP step, which would
    silently extend it). Returns the truthy word, or `""` if the hold
    elapses with nothing landing -- never raises (an exception from
    `attempt_fn` is treated as a falsy attempt, exactly like a caught
    exception around a single `deliver_compact_now` call always was).

    Shared by `deliver_compact_self` (60s hold, self-callback origin) and
    `deliver_compact_record` (#238 adversarial review 🔴1 -- a SHORT hold
    for the plain --record path, whose caller passes `request_ts == now`
    on every real invocation, making `_compact_request_too_young` an
    unconditional off-switch for that path without a genuine retry).

    #238-review-style finding 🔵F8 (this ticket's own review) -- both
    callers run SYNCHRONOUSLY inside a Stop/SubagentStop hook, which the
    harness itself time-limits, so a misconfigured/malformed `hold_s`
    (`inf`, `nan`, or an absurdly large env override) must never turn this
    into an effectively unbounded loop -- clamped to
    `COMPACT_RETRY_HOLD_CEILING_S` (well past every legitimate caller's
    own hold, self-callback's 60s included). A non-finite/negative
    `retry_interval` is clamped to a small positive floor for the same
    reason (`time.sleep` on a genuine negative raises; `nan` compares
    False everywhere and would otherwise silently defeat the clamp
    below)."""
    clock_fn = clock_fn or time.monotonic
    sleep_fn = sleep_fn or time.sleep
    try:
        hold_s = float(hold_s)
        if not math.isfinite(hold_s):
            hold_s = COMPACT_RETRY_HOLD_CEILING_S
    except (TypeError, ValueError):
        hold_s = 0.0
    hold_s = min(max(0.0, hold_s), COMPACT_RETRY_HOLD_CEILING_S)
    try:
        retry_interval = float(retry_interval)
        if not math.isfinite(retry_interval) or retry_interval <= 0:
            retry_interval = COMPACT_RETRY_MIN_INTERVAL_S
    except (TypeError, ValueError):
        retry_interval = COMPACT_RETRY_MIN_INTERVAL_S
    deadline = clock_fn() + hold_s
    while True:
        try:
            word = attempt_fn()
        except Exception:
            word = ""
        if word:
            return word
        now = clock_fn()
        if now >= deadline:
            return ""
        sleep_fn(min(retry_interval, deadline - now))


# #238 adversarial review 🔴1 (2026-08-06) -- the SAME bounded hold/retry
# shape `deliver_compact_self` already used, for the PLAIN (non-self)
# synchronous path (`cmd_compact_request --record`, called from the Stop /
# SubagentStop hooks). Live-measured against this box's own real
# `compact-decisions.log`: `cmd_compact_request` always captures ONE
# `time.time()` read and passes it as BOTH `record_compact_request`'s `now=`
# and `deliver_compact_now`'s `request_ts=`/`now=` -- so without a genuine
# retry, `_compact_request_too_young`'s age is exactly 0.0 on every single
# real call, meaning the min-request-age gate silently became an
# UNCONDITIONAL off-switch for this whole path (18 of 87 real sends on this
# box alone would have moved to job 14's slower poll, some of which lapse
# entirely on the plain Stop-hook channel). A SHORT hold (a few seconds,
# NOT self-callback's 60s -- this runs synchronously inside a Stop/
# SubagentStop hook, which must return promptly) with a fresh `now_fn()` on
# every retry gives the same-turn-dispatch race's own proxy signals
# (`_session_has_live_bg_tasks`) a real chance to catch up, which is the
# entire point of the gate.
COMPACT_RECORD_HOLD_DEFAULT_S = 3.0   # env AIRULESET_COMPACT_RECORD_HOLD_S
COMPACT_RECORD_RETRY_INTERVAL_S = 0.5


def deliver_compact_record(sid, cwd, origin=None, request_ts=None, run=None,
                           projects_dir=None, min_context=None, git_run=None,
                           hold_s=None, retry_interval=None, now_fn=None,
                           sleep_fn=None, clock_fn=None):
    """`cmd_compact_request --record`'s own delivery attempt (see the
    section comment above) -- a bounded hold/retry around
    `deliver_compact_now`, re-evaluating with a FRESH `now_fn()` on every
    attempt while `request_ts` stays fixed at the caller's own recorded
    timestamp, so real elapsed time (not a single instantaneous read)
    decides whether the min-request-age gate clears.

    Returns whatever `deliver_compact_now` returns once truthy, or `""` if
    the (short) hold elapses with nothing landing -- the caller's existing
    contract (`""` means "leave the request recorded for job 14's polled
    retry") is unchanged, this function just gives that first attempt a
    real few seconds instead of exactly none.

    #238-review-style finding 🟡F4 (this ticket's own review, proven live)
    -- `hold_s` and the min-request-age gate (`COMPACT_MIN_REQUEST_AGE_S`,
    itself env-overridable via `AIRULESET_COMPACT_MIN_REQUEST_AGE_S`) are
    otherwise UNCOUPLED: raising the min-age env var past `hold_s -
    retry_interval` silently restores the exact 🔴1 off-switch this
    function exists to fix (the age gate can never clear inside the hold).
    Floors `hold_s` to comfortably outlast the CURRENTLY RESOLVED min-age
    -- only ever WIDENS an accidentally-too-short POSITIVE hold, never
    shortens a deliberately configured longer one, and never touches an
    explicit `hold_s <= 0` (that spelling already has its own, DIFFERENT
    meaning -- "exactly one attempt, no retry at all" -- which callers/
    tests use deliberately, e.g. to keep a real end-to-end hook test fast
    when only the RECORDING side is under test, not this function's own
    retry timing)."""
    now_fn = now_fn or time.time
    if hold_s is None:
        try:
            hold_s = float(os.environ.get("AIRULESET_COMPACT_RECORD_HOLD_S",
                                          COMPACT_RECORD_HOLD_DEFAULT_S))
        except ValueError:
            hold_s = COMPACT_RECORD_HOLD_DEFAULT_S
    if retry_interval is None:
        retry_interval = COMPACT_RECORD_RETRY_INTERVAL_S
    if hold_s > 0:
        floor = _compact_min_request_age() + retry_interval
        if hold_s < floor:
            hold_s = floor
    return _compact_retry_until(
        lambda: deliver_compact_now(sid, cwd, run=run, projects_dir=projects_dir,
                                    min_context=min_context, git_run=git_run,
                                    origin=origin, request_ts=request_ts,
                                    now=now_fn()),
        hold_s, retry_interval, clock_fn=clock_fn, sleep_fn=sleep_fn)


def deliver_compact_self(run=None, projects_dir=None, pane_env=None,
                         hold_s=None, retry_interval=None,
                         now_fn=None, sleep_fn=None, clock_fn=None,
                         min_context=None, git_run=None):
    """The self-callback: resolve the calling session's own pane, record a
    request under the `self-callback` proven-boundary origin, and hold
    (bounded) while retrying synchronous delivery until it lands or the
    window expires.

    Returns `(word, sid)`. `sid==""` means the pane/session could not be
    resolved at all (nothing was recorded — see `resolve_self_pane`).
    Otherwise `word` is `deliver_compact_now`'s own disposition word once
    one is returned truthy (`"sent"`/`"claim-queued"`/`"queued-compact"` —
    `"dropped-no-work"`/`"dropped-small-context"` cannot occur here, since
    the proven-boundary origin exempts both #99/#48 gates entirely) — on a
    truthy word the request is also CLEARED (`clear_compact_request`), the
    same contract `cmd_compact_request`'s own `--record` branch already
    gives every other caller; leaving it recorded after a genuine send was
    a #225-review-found bug (job 14 would then see a STALE self-callback
    entry it had no reason to ever act on again). Or the literal
    `"recorded"` if the whole hold window elapses with nothing landing —
    the request stays on file, WITH the trusted origin, for job 14's own
    later sweep to pick up (extended by #225 to trust this origin exactly
    like `subagent-stop`).

    Reachable expiry, no wedging (the hard constraint this ticket names
    explicitly): this writes nothing new outside the TWO stores that are
    already bounded — `compact-requests.json` via `COMPACT_REQUEST_MAX_AGE_S`
    (30 min, `record_compact_request` above, unchanged) and
    `compact_claims.json` via its own existing CONSUMED/FAILED/EXPIRED
    resolution (#72/#78/#83/#140, `compact_claim_set`/`compact_claim_active`,
    unchanged). This function's OWN loop is bounded by `hold_s` regardless —
    it can hang the calling process for at most that long, never indefinitely,
    and holds no lock/claim of its own that could outlive it.

    `hold_s` defaults to `AIRULESET_COMPACT_SELF_HOLD_S` or
    `COMPACT_SELF_HOLD_DEFAULT_S` (60s) — the ticket's own "~60s" figure.
    `now_fn` (wall-clock, for the ONE `record_compact_request` call's stored
    `ts`) and `clock_fn` (a MONOTONIC clock, for the loop's own deadline
    arithmetic — #225-review finding: a wall-clock hold is sensitive to a
    backward NTP step, which would silently extend it) are injectable
    separately so tests never sleep for real and never depend on which
    clock a mock happens to advance.

    #250 — the ONE `record_compact_request` call's `ts` is captured once
    (`record_ts`) and threaded into EVERY retry as `deliver_compact_now`'s
    own `request_ts=`, with a FRESH `now_fn()` read each time — the same
    grace-bound check job 14 applies. Under the default 60s hold and 5-min
    grace this never actually crosses the boundary (see
    `COMPACT_DEFER_GRACE_S`'s own comment), which is deliberate: the
    post-grace delivery belongs to job 14's own polled retry, not this
    bounded hold."""
    now_fn = now_fn or time.time
    clock_fn = clock_fn or time.monotonic
    sleep_fn = sleep_fn or time.sleep
    if hold_s is None:
        try:
            hold_s = float(os.environ.get("AIRULESET_COMPACT_SELF_HOLD_S",
                                          COMPACT_SELF_HOLD_DEFAULT_S))
        except ValueError:
            hold_s = COMPACT_SELF_HOLD_DEFAULT_S
    if retry_interval is None:
        retry_interval = COMPACT_SELF_RETRY_INTERVAL_S
    _pane_id, cwd, sid = resolve_self_pane(run=run, projects_dir=projects_dir,
                                           pane_env=pane_env)
    if not sid:
        return "", ""
    record_ts = now_fn()
    record_compact_request(sid, cwd, now=record_ts,
                           origin=_COMPACT_SELF_CALLBACK_ORIGIN)
    word = _compact_retry_until(
        lambda: deliver_compact_now(sid, cwd, run=run, projects_dir=projects_dir,
                                    min_context=min_context, git_run=git_run,
                                    origin=_COMPACT_SELF_CALLBACK_ORIGIN,
                                    request_ts=record_ts, now=now_fn()),
        hold_s, retry_interval, clock_fn=clock_fn, sleep_fn=sleep_fn)
    if word:
        clear_compact_request(sid)
        return word, sid
    return "recorded", sid


# --------------------------------------------------------------------------- #
# Job 15 -- COMPACT OVERGROWN IDLE SESSIONS -- REMOVED (#102, 2026-07-27).
#
# `compact_stale_context` fired /compact purely off CONTEXT SIZE + IDLE
# DURATION, with no regard for what marker the session's last turn ended
# on -- exactly the mechanism the #102 correction identified as the live
# camera-box incident's likely cause (a `❓ NEEDS YOU` turn, blocked
# awaiting the user's answer, sat idle long enough to qualify and got
# compacted). The user's corrected agreement (#102): compaction fires
# ONLY at a completed-ticket boundary (job 14, `compact_ticket_boundary`)
# -- nothing else, no context-size heuristic of our own. Claude Code's
# own native auto-compact already handles a genuinely full context; this
# job never fed anything else (its state keys `compact_stale`/
# `compact_stale_attempts`, its constants COMPACT_CONTEXT_THRESHOLD/
# COMPACT_MIN_IDLE_S/COMPACT_STALE_*, and `_wait_for_compact_return` were
# ALL private to this job's own delivery and are removed with it -- none
# of it was reused elsewhere). See #102's evidence block for the full
# audit of what survived from jobs 15/17 and why.
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Job 17 -- HARD CONTEXT CEILING BACKSTOP -- REMOVED (#102, 2026-07-27).
#
# `compact_hard_ceiling` fired /compact purely off CONTEXT SIZE (a fixed
# ceiling), regardless of idle duration and even into a BUSY pane, with no
# regard for what marker the session's last turn ended on. Same #102
# correction as job 15: compaction fires ONLY at a completed-ticket
# boundary (job 14) -- no context-size heuristic of our own. This job's
# state key (`state['compact_ceiling']`), its own constants
# (COMPACT_HARD_CEILING/COMPACT_CEILING_QUEUED/COMPACT_CEILING_STUCK_CYCLES)
# were private to its own delivery and are removed with it. The SHARED
# helpers it also used (`_pane_compacting`/COMPACTING_MARKER,
# `_reconcile_candidate_panes`, `_pane_has_queued_compact`,
# `_proc_fingerprint_alive`/`_pane_claude_proc_fingerprint`, the shared
# `/compact` claim `compact_claim_active`/`compact_claim_set`) are KEPT --
# every one of them is still used by job 14, the synchronous #65 path,
# and/or jobs 18/20/21. See #102's evidence block for the full audit.
# --------------------------------------------------------------------------- #

COMPACTING_MARKER = "Compacting conversation"

# --------------------------------------------------------------------------- #
# #84 (2026-07-26 live gk incident) — reading the rows CC renders DIRECTLY
# ABOVE the input box. Two independent consumers share this ONE walk:
#
#   * the QUEUED-COMPACT guard (`_pane_has_queued_compact`) — a pane that
#     already has a `/compact` submitted-but-unexecuted must never be sent a
#     second one. CC drains its type-ahead queue only where a turn actually
#     ENDS, so during a long turn the queued commands just pile up and then
#     fire back-to-back: the first really compacts, the rest answer "Not
#     enough messages to compact". That is the duplicate-compact spam.
#
#   * the LONG-TURN detector (`pane_turn_elapsed`, job 21) — CC's spinner row
#     carries the turn's own elapsed time.
#
# The walk starts at the input box and moves UP through blank spacer rows,
# the box's own top separator, and the queued `❯ …` rows; the FIRST other
# content row it meets is the spinner (if the pane is running a turn at all)
# and also terminates the queue. That adjacency requirement is what keeps a
# conversation that merely QUOTES a panel (a session working on this very
# ticket) from being read as a live queue — real content below the quoted
# rows stops the walk before it ever reaches them. Everything BELOW the box
# (the agent strip, whose activity label is ARBITRARY model-generated text —
# the #36 scar — plus the statusline and mode hint) is peeled by
# `_above_input_box` and is never even looked at.
#
# Known, accepted limitation (the safe direction): a pane whose conversation
# ends with quoted `❯ /compact` rows IMMEDIATELY above the box would read as
# queued and skip one compaction. A missed compaction self-heals on the next
# sweep as the viewport scrolls; a missed GUARD is the duplicate this exists
# to kill.
# --------------------------------------------------------------------------- #

_QUEUED_COMPACT_RX = re.compile(r"^❯\s+/compact\s*$")

# CC's spinner: `· Germinating… (2h 40m 36s · ↓ 69.3k tokens)`,
# `✳ Baking… (4m 2s · esc to interrupt)`, `✳ Baking… (7s · …)`. Anchored on
# the ellipsis + parenthesised duration CC always renders, so a line of prose
# that merely mentions a duration is not a spinner.
#
# EVERY component is optional because CC DROPS the seconds once a turn is a
# few minutes old (`(2m · esc to interrupt)` — a shape that appears verbatim
# in this repo's own live-captured fixtures). A seconds-mandatory pattern
# therefore went blind exactly as a turn started getting long, which is the
# one case job 21 exists for. `\b` after each unit keeps `(3 messages)` from
# reading as 3 minutes, and an all-empty match (`(esc to interrupt)`, no
# duration claimed at all) is rejected by the caller rather than invented as
# 0s — a fake "just started" would reset the incident identity every sweep.
_TURN_ELAPSED_RX = re.compile(
    r"…\s*\(\s*(?:(\d+)\s*h\b)?\s*(?:(\d+)\s*m\b)?\s*(?:(\d+)\s*s\b)?")


def _above_box_scan(captured, max_rows=25):
    """Walk UP from the input box — see the section comment.

    Returns `(queued_rows, spinner_row)`: every `❯ …` row contiguously above
    the box (outermost last), and the first non-queued content row met, or
    None when the walk runs out of region first. Bounded by `max_rows` so a
    pathological capture can never make this walk the whole transcript."""
    rows = [ln.strip() for ln in _above_input_box(captured or "").splitlines()]
    queued = []
    n = 0
    for ln in reversed(rows):
        if not ln or _is_separator_line(ln):
            continue                       # spacer / the box's own top border
        n += 1
        if n > max_rows:
            return queued, None
        if ln.startswith("❯"):
            queued.append(ln)
            continue
        return queued, ln                  # the spinner (or whatever is there)
    return queued, None


def _pane_has_queued_compact(captured):
    """True if the pane ALREADY holds a `/compact` waiting to execute (#84).
    Every `/compact` sender consults this immediately before its own send —
    it composes with, never replaces, the shared claim (#78) and the
    proc fingerprint (#82/#83): those track what THIS watchdog typed, while
    this reads what the PANE actually shows, whoever put it there."""
    queued, _spinner = _above_box_scan(captured)
    return any(_QUEUED_COMPACT_RX.match(ln) for ln in queued)


def pane_turn_elapsed(captured):
    """How long the pane's CURRENT turn has been running, in seconds — read
    off CC's own spinner row — or None when no turn is running (or the row
    carries no elapsed time yet).

    Deliberately a PANE read, not a transcript read. The #84 forensic
    analysis showed CC logged the 2h40m incident as THREE internal turns,
    each continued by the armed `/goal` loop's Stop hook REJECTING the stop —
    and the input queue drained at NONE of those boundaries, only at the
    user's manual interrupt. A transcript-boundary-based duration would
    therefore have reported three short turns and missed the incident
    entirely. The pane's label runs from the last genuine EXTERNAL input,
    which is exactly the quantity that matters: how long the queue has been
    unable to drain."""
    _queued, spinner = _above_box_scan(captured)
    if not spinner:
        return None
    mm = _TURN_ELAPSED_RX.search(spinner)
    if not mm:
        return None
    h, mi, s = mm.group(1), mm.group(2), mm.group(3)
    if h is None and mi is None and s is None:
        return None                    # a spinner claiming no elapsed time
    return int(h or 0) * 3600 + int(mi or 0) * 60 + int(s or 0)


def _pane_compacting(captured):
    """True if the pane's OWN current text shows CC's "Compacting
    conversation" progress indicator -- a `/compact` already in flight, sent
    by job 14 or #65's synchronous delivery (jobs 15/17, once also senders,
    were REMOVED #102 -- the indicator does not care who triggered it, so
    this stays a useful defense-in-depth for any remaining sender).
    Resending `/compact` while this shows would just queue a redundant
    second compaction (#69)."""
    return COMPACTING_MARKER in (captured or "")

# --------------------------------------------------------------------------- #
# Job 21 — LONG-TURN WATCH (#84, 2026-07-26 live gatekeeper incident).
#
# The watchdog could only ever see CONTEXT SIZE. But a turn that simply RUNS
# for hours is a fault state of its own, independent of how big the context
# is: while it runs nothing compacts, no question is delivered, and every
# keystroke anyone sends just piles up in CC's type-ahead queue. The live
# incident: ONE turn at 2h40m, three `/compact` queued behind it, context at
# 398K, and nothing moved until the user manually interrupted.
#
# WHY THE PANE AND NOT THE TRANSCRIPT. The forensic read of that session
# (posted on #84) is what settles this. CC logged those 2h40m as THREE
# internal turns — 13:25→14:36, 14:36→15:30, 15:30→15:44 — each ended by the
# armed `/goal` loop's Stop hook REJECTING the stop and forcing continuation.
# The input queue drained at NONE of those boundaries; the two queued
# `/compact` commands sat there from 14:31 and 15:41 until the manual
# interrupt at 15:44. So "CC drains the queue at a turn boundary" is really
# "…only where the turn actually ENDS (the Stop is accepted)" — and a
# detector built on transcript turn boundaries would have seen three ordinary
# turns and missed this incident completely. The PANE's own elapsed label
# runs from the last genuine EXTERNAL input, which is precisely the quantity
# that matters here.
#
# (The same read DISPROVED the ticket's original hypothesis: no foreground
# subagent dispatch was involved. Every `Agent` call in the window returned
# async within ~100 ms and none was in flight during the long stretch — the
# turn was a long unbroken chain of correctly-bounded foreground `Bash` CI
# polls, extended over and over by CI restarting from concurrent merges.)
#
# Detection only — this job NEVER sends a keystroke. A long turn may be
# entirely legitimate (a genuine long CI wait), so the response is ONE
# deduped Discord ping per incident plus an unconditional log line every
# sweep, never an interrupt: deciding to break a running turn is the user's
# call, and interrupting a healthy one is the worse error.
# --------------------------------------------------------------------------- #

LONG_TURN_THRESHOLD_S = 1800          # 30 min; env AIRULESET_LONG_TURN_S
# Two sweeps of the SAME turn compute `start = now - elapsed` from a
# whole-second pane label read at slightly different moments, so the value
# jitters by a second or two. A generous tolerance keeps that jitter from
# reading as a new turn (which would re-ping); a genuinely new turn's start
# is minutes-to-hours away, never within this window.
LONG_TURN_SAME_TURN_TOLERANCE_S = 300


def _human_duration(seconds):
    """`9636` -> `2h 40m` — the phone-readable form for the ping."""
    h, rem = divmod(int(seconds), 3600)
    mins = rem // 60
    if h:
        return "%dh %dm" % (h, mins)
    return "%dm" % mins


def long_turn_watch(now, run, state, panes_by_sid, send_fn=None, dry_run=False,
                    threshold=None, project_by_sid=None, owner_by_sid=None):
    """Job 21 — see the section comment.

    Reuses the ONE per-sweep capture run_once already took (`panes_by_sid`,
    the same map jobs 7 and 14 consume), so a sweep costs no extra tmux
    round-trip for panes that are idle or short-running.

    Every DETECTION is logged unconditionally, every sweep (issue #36's
    print-always convention) — a long turn that persists must be visible in
    journalctl for as long as it lasts, not only on the sweep it crossed the
    threshold. The PING is deduped per (session, turn): the turn's identity
    is its START (`now - elapsed`, tolerant of read jitter per
    `LONG_TURN_SAME_TURN_TOLERANCE_S`), so a single incident pings once no
    matter how many sweeps it spans, while a NEW long turn later pings
    again. `dry_run` logs exactly as usual but never pings and never records
    state, mirroring every other job's dry-run contract."""
    if threshold is None:
        try:
            threshold = int(os.environ.get("AIRULESET_LONG_TURN_S",
                                           LONG_TURN_THRESHOLD_S))
        except ValueError:
            threshold = LONG_TURN_THRESHOLD_S
    project_by_sid = project_by_sid or {}
    owner_by_sid = owner_by_sid or {}
    seen = state.get("long_turn") or {}
    logs = []
    live = set()

    for sid, (pid, captured) in sorted((panes_by_sid or {}).items()):
        elapsed = pane_turn_elapsed(captured)
        if elapsed is None:
            continue                       # no turn running — nothing to say
        live.add(sid)
        start = now - elapsed
        prev = seen.get(sid) or {}
        same_turn = abs(start - float(prev.get("start") or 0)) \
            <= LONG_TURN_SAME_TURN_TOLERANCE_S
        entry = {"start": start,
                 "pinged": bool(same_turn and prev.get("pinged"))}
        if elapsed < threshold:
            if not dry_run:
                seen[sid] = entry
            continue

        label = project_by_sid.get(sid) or _pane_location(pid, run) or pid
        human = _human_duration(elapsed)
        logs.append("long-turn %s [%s] elapsed=%ds (%s)"
                    % (label, sid, elapsed, human))
        if dry_run or entry["pinged"] or send_fn is None:
            # `send_fn is None` (a caller with no notify path) must NOT mark
            # the turn as pinged — nothing was delivered, so a later sweep
            # with a real send_fn still owes the user this ping.
            continue
        status = send_fn(
            "\U0001f570 **%s** — jeden ťah beží už %s\n"
            "> Kým beží, nič sa nekompaktuje, otázky sa nedoručujú a "
            "napísané príkazy čakajú vo fronte. Ak to nie je zámer "
            "(dlhé čakanie na CI), treba sa pozrieť." % (label, human),
            owner=owner_by_sid.get(sid) or None,
            dedup_key="long-turn:%s:%d" % (sid, int(start)),
            dry_run=dry_run)
        logs.append("long-turn PING %s [%s] -> %s" % (label, sid, status))
        entry["pinged"] = True
        seen[sid] = entry

    if not dry_run:
        # drop sessions whose turn ended (or whose pane is gone) so the state
        # file cannot grow without bound across a long-lived watchdog.
        state["long_turn"] = {k: v for k, v in seen.items() if k in live}
    return logs


# --------------------------------------------------------------------------- #
# Job 24 — DELIVERY-STALL WATCH (#138, camera-box 2026-07-11 → 2026-07-28).
#
# Measured, from camera-box's own git + GitHub state:
#
#   * PR #704 (dev -> main) OPEN since 2026-07-11T20:57Z — 17 days.
#     `mergeable: MERGEABLE`, `mergeStateStatus: BLOCKED`. Eleven of twelve
#     checks green; the one red is `Full-path E2E (rig zero-loss gate)`.
#   * That gate's last SUCCESS on dev was 2026-07-13T04:53Z. Since then:
#     105 failures, 31 cancelled, 0 successes. The failure is a rig
#     precondition ("GATE FAILED: 1 node(s) DRIFTED or PTP-DEGRADED"), not a
#     defect in any diff, so no amount of code work could clear it.
#   * `origin/main` frozen at 2026-07-11; `origin/dev` 422 commits ahead.
#   * Issue closures/day: 21, 21, 12, 15, 6, 4 (07-10..07-15) then ZERO until
#     a single one on 07-27 — because closure there is merge-driven, so a
#     blocked merge makes closure structurally zero no matter how much lands.
#
# The loop meanwhile kept spending: on 07-27/28 alone, 33 dispatches across
# ~15 distinct tickets and 85 commits, for zero merges.
#
# WHY NOTHING NOTICED, AND WHY THAT IS THE ACTUAL DEFECT. Every signal this
# repo owns is merge-TRIGGERED: the per-ticket run-card fires AFTER a merge,
# `autopilot-progress/<repo>.json` is fed by that card, the statusline shows
# an open-issue count that only ever grows. A loop that cannot merge is
# therefore silent BY CONSTRUCTION — silence and health are the same
# observation — which is how 17 days passed unremarked.
#
# WHAT THIS JOB MEASURES, and why it is git and not turns. Two purely LOCAL
# facts per repo hosting a live pane:
#   SPEND    = the newest commit on the checked-out branch (local HEAD; always
#              current, needs no fetch).
#   DELIVERY = the newest commit on the base branch (`origin/HEAD`, falling
#              back to origin/main / origin/master), plus the count of commits
#              on HEAD not reachable from it.
# Fresh SPEND with a frozen DELIVERY and a real backlog is the stall.
#
# A re-poke / no-dispatch detector was the obvious alternative and the
# evidence rejects it: it would have been silent on BOTH halves of this
# incident. Through 07-16..07-26 there were no turns to count (0 Agent
# dispatches, 0-153 transcript lines/day, `/goal` unarmed since 07-13 — a
# deliberate live-event pause, not a stuck agent, and not this repo's bug).
# Through 07-27..07-28 the loop dispatched CORRECTLY across ~15 distinct
# tickets with real commits, so a dispatch-liveness detector would have read
# perfectly healthy while zero work shipped. Dispatch is not delivery.
#
# Three properties keep it quiet where it should be quiet:
#   * HEAD == base (this repo, which pushes straight to main) yields 0
#     undelivered — silent STRUCTURALLY, not by threshold.
#   * A parked repo with no fresh commits is silent: nothing is being spent,
#     so there is nothing to warn about.
#   * A candidate is CONFIRMED before it is announced. The injected probe
#     fetches the base ref and the verdict is RE-MEASURED after it, so a
#     remote-tracking ref that had merely gone stale locally can never on its
#     own raise an alarm. The probe's other half (naming the blocking PR and
#     its red check) is pure enrichment — losing it costs the detail, never
#     the alert.
#
# Detection only, exactly like job 21: it never types into a pane and never
# touches the repo's worktree, index or local branches (a fetch writes only
# remote-tracking refs). Deciding what to do about a blocked merge — fix the
# gate, park the PR, split the batch — is the user's call, and this repo is
# not the owner of any repo it watches.
# --------------------------------------------------------------------------- #

DELIVERY_STALL_S = 172800        # 48h with no delivery; env AIRULESET_DELIVERY_STALL_S
DELIVERY_WORK_FRESH_S = 86400    # 24h — the work branch must be actively moving
DELIVERY_MIN_UNDELIVERED = 3     # a stray commit or two is not a stalled batch
DELIVERY_REPING_S = 86400        # re-ping daily while it persists, not per sweep

# The stall window is bounded on BOTH ends. Past this, the base branch is not
# a delivery target that stopped receiving — it is a branch nobody delivers to
# at all, and the difference is invisible from the lower bound alone. Found
# live six minutes after job 24 shipped: `~/varos/eft5000`, a GitLab repo
# whose `origin/master` last moved on 2019-09-07 (2515 days) while real work
# merges into `develop-50` — which took a merge the same day the alert fired.
# `origin/HEAD` is unset there, so the fallback picks a branch abandoned in
# 2019 and correctly reports 3,248 commits "undelivered" to it, forever.
# The upper bound costs a genuine stall nothing: one that ever reached it has
# already pinged every single day for three months on the way.
DELIVERY_STALL_MAX_S = 90 * 86400


def _git_first_line(cwd, argv, git_run=None):
    """One `git -C <cwd> …` call, stripped. None on any failure OR empty
    output — never a partial guess."""
    out = (git_run or _default_git_run)(["git", "-C", str(cwd)] + list(argv))
    if out is None:
        return None
    return out.strip() or None


def _git_base_ref(cwd, git_run=None):
    """The repo's DELIVERY ref — the branch a merge lands on. `origin/HEAD`
    is authoritative when the checkout has it; the explicit fallbacks cover a
    clone that never resolved it. None when neither exists (then the repo is
    unmeasurable and job 24 stays silent about it)."""
    ref = _git_first_line(cwd, ["symbolic-ref", "--quiet",
                                "refs/remotes/origin/HEAD"], git_run)
    if ref and ref.startswith("refs/remotes/"):
        return ref[len("refs/remotes/"):]
    for cand in ("origin/main", "origin/master"):
        if _git_first_line(cwd, ["rev-parse", "--verify", "--quiet",
                                 cand + "^{commit}"], git_run):
            return cand
    return None


def _git_commit_ts(cwd, ref, git_run=None):
    out = _git_first_line(cwd, ["log", "-1", "--format=%ct", ref], git_run)
    try:
        return int(out)
    except (TypeError, ValueError):
        return None


def delivery_state(cwd, now, git_run=None):
    """SPEND vs DELIVERY for `cwd`'s repo (see the section comment).

    Returns a dict — `root`, `base`, `undelivered`, `work_age`,
    `delivery_age`, `base_ts` — or None when the answer is UNMEASURABLE (not
    a git repo, no resolvable base ref, `git` unavailable, a count that did
    not parse). None is never a stall: a repo this cannot read is a repo this
    says nothing about, the same "never block on don't-know" contract
    `compact_boundary_substantial` uses."""
    if not cwd:
        return None
    root = _git_first_line(cwd, ["rev-parse", "--show-toplevel"], git_run)
    if not root:
        return None
    base = _git_base_ref(root, git_run)
    if not base:
        return None
    head_ts = _git_commit_ts(root, "HEAD", git_run)
    base_ts = _git_commit_ts(root, base, git_run)
    if head_ts is None or base_ts is None:
        return None
    raw = _git_first_line(root, ["rev-list", "--count", base + "..HEAD"],
                          git_run)
    try:
        undelivered = int(raw)
    except (TypeError, ValueError):
        return None
    return {"root": root, "base": base, "undelivered": undelivered,
            "base_ts": base_ts,
            "work_age": now - head_ts, "delivery_age": now - base_ts}


def _delivery_stalled(st, stall, work_fresh, min_undelivered):
    """The verdict, in one place so the pre-probe and post-probe reads can
    never drift apart."""
    return (st is not None
            and st["undelivered"] >= min_undelivered
            and st["work_age"] <= work_fresh
            and stall <= st["delivery_age"] <= DELIVERY_STALL_MAX_S)


def delivery_stall_watch(now, run, state, cwd_by_sid, send_fn=None,
                         dry_run=False, git_run=None, delivery_probe=None,
                         owner_by_sid=None, project_by_sid=None,
                         stall=None, work_fresh=None, min_undelivered=None,
                         reping=None):
    """Job 24 — see the section comment.

    Gated on `delivery_probe` (the "wired = on" convention of jobs 8/11/16):
    the probe carries the confirming fetch, and a verdict that was never
    confirmed must not reach the user's phone.

    One repo is examined once per sweep however many panes sit in it, the
    DETECTION is logged every sweep (issue #36's print-always convention) and
    the PING is deduped per repo per `reping` window, so a stall that lasts
    weeks produces a daily reminder rather than one forgotten alert or 1,440
    a day. State is dropped the moment the base advances, so a later stall in
    the same repo pings again on its own."""
    if delivery_probe is None:
        return []
    if stall is None:
        try:
            stall = int(os.environ.get("AIRULESET_DELIVERY_STALL_S",
                                       DELIVERY_STALL_S))
        except ValueError:
            stall = DELIVERY_STALL_S
    work_fresh = DELIVERY_WORK_FRESH_S if work_fresh is None else work_fresh
    if min_undelivered is None:
        min_undelivered = DELIVERY_MIN_UNDELIVERED
    reping = DELIVERY_REPING_S if reping is None else reping
    owner_by_sid = owner_by_sid or {}
    seen = dict(state.get("delivery_stall") or {})
    logs = []
    live = set()
    examined = set()

    for sid, cwd in sorted((cwd_by_sid or {}).items()):
        st = delivery_state(cwd, now, git_run=git_run)
        if st is None or st["root"] in examined:
            continue
        root = st["root"]
        examined.add(root)
        live.add(root)
        if not _delivery_stalled(st, stall, work_fresh, min_undelivered):
            seen.pop(root, None)
            continue

        # CONFIRM, then announce. The probe fetches the base ref; the verdict
        # is re-read afterwards so a locally-stale remote-tracking ref cannot
        # by itself produce a ping. Enrichment is best-effort by design.
        try:
            info = delivery_probe(root, st["base"])
        except Exception:
            info = None
        confirmed = delivery_state(cwd, now, git_run=git_run) or st
        if not _delivery_stalled(confirmed, stall, work_fresh, min_undelivered):
            seen.pop(root, None)
            logs.append("delivery-stall confirmed-clear %s" % root)
            continue
        st = confirmed

        label = os.path.basename(root)
        days = int(st["delivery_age"] // 86400)
        logs.append("delivery-stall %s undelivered=%d delivery_age=%ds base=%s"
                    % (label, st["undelivered"], int(st["delivery_age"]),
                       st["base"]))
        prev = seen.get(root) or {}
        pinged = prev.get("pinged_ts")
        if dry_run or send_fn is None or (
                pinged is not None and now - float(pinged) < reping):
            # `send_fn is None` must NOT mark this as pinged — nothing was
            # delivered, so a later sweep with a real notify path still owes
            # the user this alert (job 21's contract, reused verbatim).
            continue
        blocker = ""
        if isinstance(info, dict) and info.get("pr"):
            blocker = "\n> Blokuje to PR #%s%s." % (
                info["pr"],
                (" — neprejde kontrola `%s`" % info["check"])
                if info.get("check") else "")
        status = send_fn(
            "\U0001f4e6 **%s** — %d dní sa nič nedoručilo\n"
            "> Na pracovnej vetve čaká %d commitov hotovej práce, ale do "
            "vetvy `%s` sa už %d dní nič nezlúčilo, takže sa nezatvára ani "
            "jeden ticket.%s"
            % (label, days, st["undelivered"], st["base"].split("/")[-1],
               days, blocker),
            owner=owner_by_sid.get(sid) or None,
            dedup_key="delivery-stall:%s:%d" % (label, int(now // reping)),
            dry_run=dry_run)
        logs.append("delivery-stall PING %s -> %s" % (label, status))
        seen[root] = {"pinged_ts": now, "base_ts": st["base_ts"]}

    if not dry_run:
        # drop repos with no live pane this sweep, so the state file cannot
        # grow without bound across a long-lived watchdog (job 21's shape).
        state["delivery_stall"] = {k: v for k, v in seen.items() if k in live}
    return logs


# --------------------------------------------------------------------------- #
# Job 26 — COMPACT-STALL WATCH (#140, 2026-07-28).
#
# The second half of #140, and the ticket is explicit that it is a defect in
# its own right: two sessions on two boxes sat wedged — forestshop@dev1 at
# ~500K, montalu@subdev at 400K — and NOTHING noticed. The user did, by
# looking at two screens, and had to hand-compact both. The TTL above stops
# THIS cause; a compaction that was asked for and never landed has others (a
# pane whose type-ahead queue never drains, a boundary that scrolled out of
# the bounded transcript tail), and all of them look identical from outside:
# the context simply keeps growing and costing money on every later turn.
#
# The condition is read from the ARTIFACT, never from intent (#134's lesson —
# a guard that defers to an unenforced action is a silence generator): a claim
# STILL ON FILE, for a session that STILL HAS A LIVE PANE, older than the
# stall window, with no `compact_boundary` in that session's own transcript
# newer than the claim. That is precisely the measured state of both boxes.
#
# Job 24 could not carry this — it is REPO-keyed and measures git delivery,
# this is SESSION-keyed and measures compaction; one job with two unrelated
# subjects would be worse than two jobs. What IS reused, deliberately and
# verbatim, is job 24's shape: detection logged every sweep (#36), the ping
# deduped per re-ping window rather than per sweep, `send_fn is None` never
# marking anything as pinged, state pruned to sessions with a live pane, and
# DETECTION ONLY — it never types into a pane and never touches a claim (the
# next send attempt is what releases an expired one, via the TTL above).
#
# Known residual, stated rather than hidden: `_transcript_compact_boundary_ts`
# reads a bounded 4 MB tail, so on a very busy session (forestshop writes
# ~80 MB/day) a boundary that has scrolled out of that window reads as "never
# landed" and can produce a false ping. That direction is deliberate — a
# spurious reminder costs a glance, and the failure this job exists for cost
# two hand-compactions and several hundred thousand tokens.
# --------------------------------------------------------------------------- #

COMPACT_STALL_S = 3600           # 1h since the claim; env AIRULESET_COMPACT_STALL_S
COMPACT_STALL_REPING_S = 21600   # re-ping every 6h while it persists


def compact_stall_watch(now, state, cwd_by_sid, send_fn=None, dry_run=False,
                        projects_dir=None, claims_path=None, owner_by_sid=None,
                        project_by_sid=None, stall=None, reping=None):
    """Job 26 — see the section comment. Detection only; never a keystroke."""
    projects_dir = projects_dir or PROJECTS_DIR
    owner_by_sid = owner_by_sid or {}
    project_by_sid = project_by_sid or {}
    if stall is None:
        try:
            stall = int(os.environ.get("AIRULESET_COMPACT_STALL_S",
                                       COMPACT_STALL_S))
        except ValueError:
            stall = COMPACT_STALL_S
    reping = COMPACT_STALL_REPING_S if reping is None else reping
    seen = dict(state.get("compact_stall") or {})
    logs = []
    live = set()

    for sid, entry in sorted(_load_compact_claims(claims_path).items()):
        cwd = (cwd_by_sid or {}).get(sid)
        if not cwd or not isinstance(entry, dict):
            continue                      # no live pane — nothing to tell
        live.add(sid)
        try:
            age = now - float(entry.get("ts"))
        except (TypeError, ValueError):
            # unusable ts — the TTL above releases it on the next send
            # attempt; this job says nothing it cannot measure.
            seen.pop(sid, None)
            continue
        if age < stall:
            seen.pop(sid, None)
            continue
        claimed_cwd = entry.get("cwd") or cwd
        tpath = _transcript_for_session(projects_dir, sid, claimed_cwd)
        boundary = _transcript_compact_boundary_ts(tpath) if tpath else None
        if boundary is not None and boundary > float(entry.get("ts")):
            seen.pop(sid, None)           # it landed after all — healthy
            continue

        label = (project_by_sid.get(sid) or os.path.basename(
            str(claimed_cwd).rstrip("/")) or "session")
        hours = int(age // 3600)
        logs.append("compact-stall /%s sid=%s age=%ds cwd=%s"
                    % (label, sid[:8], int(age), claimed_cwd))
        prev = seen.get(sid) or {}
        pinged = prev.get("pinged_ts")
        if dry_run or send_fn is None or (
                pinged is not None and now - float(pinged) < reping):
            # `send_fn is None` must NOT mark this as pinged — nothing was
            # delivered, so a later sweep with a real notify path still owes
            # the user this alert (job 21/24's contract, reused verbatim).
            continue
        status = send_fn(
            "\U0001f9f9 **%s** — kontext sa už %d h nezmenšil\n"
            "> Sedenie si pred %d h vyžiadalo upratanie kontextu, ale k nemu "
            "doteraz nedošlo, takže kontext ďalej rastie a každý ďalší krok "
            "je drahší. Ak to tak ostane, pomôže ručné `/compact`."
            % (label, hours, hours),
            owner=owner_by_sid.get(sid) or None,
            dedup_key="compact-stall:%s:%d" % (sid, int(now // reping)),
            dry_run=dry_run)
        logs.append("compact-stall PING %s -> %s" % (label, status))
        seen[sid] = {"pinged_ts": now}

    if not dry_run:
        state["compact_stall"] = {k: v for k, v in seen.items() if k in live}
    return logs


# --------------------------------------------------------------------------- #
# Job 25 — CARD RECONCILIATION (#134, marek's stream 2026-07-23 → 2026-07-28).
#
# Measured: the `claude-marek` thread received ZERO per-ticket completion
# cards for five days while tvdole closed 6 issues / merged 5 PRs and
# parovanie-produktov closed 97 / merged 80. The last dedup marker for that
# repo is `#192` at 2026-07-23 19:05; issues #193–#295 produced none, and
# tvdole has never produced one at any point. Nothing was broken — the
# mechanism is healthy and zbynek-owned repos card through today. The card is
# simply an ACTION WITH NO ARTIFACT ANYONE CHECKS, so workers drifted out of
# the habit and nothing pushed back.
#
# WHY THIS EXISTS ALONGSIDE THE SubagentStop GATE. The gate
# (`hooks/subagent-stop-check-run-card.sh`) is the in-band half and it is the
# one that restores a REAL card, with the worker's own plain-Slovak goal and
# achieved text. But it is structurally blind to two of the ways this fails:
# a worker that DIES mid-run never reaches SubagentStop at all, and a
# delivery that fails AFTER the worker returned happens when no hook is
# looking. Both collapse to one observable — an issue closed by a merge with
# no delivered card — which is what this job measures.
#
# WHAT IT READS, AND WHY NOT `gh`. Purely LOCAL git, per repo hosting a live
# pane: the merge commits that landed on the base branch inside the window,
# and the `Closes/Fixes/Resolves #N` they carry. That is the same fact a `gh`
# query would return, for free, without spending an API call per repo per
# sweep against a 5,000/h budget and without needing auth in every checkout.
#
# RELATION TO JOB 24 (#138), checked before adding rather than after. They do
# not overlap and in the common case are mutually exclusive BY CONSTRUCTION:
# job 24 fires when the base branch is FROZEN (merges stopped); job 25 fires
# when the base branch MOVED (merges happened) and the reports did not. They
# share no state key and answer different questions.
#
# Detection only, exactly like jobs 21 and 24: it never types into a pane and
# never touches a worktree, index or local branch. Deciding what to do about
# an unreported ticket is the user's call.
# --------------------------------------------------------------------------- #

CARD_WINDOW_S = 172800            # merges older than 48h are history, not a gap
CARD_GRACE_S = 1200               # a ticket younger than this hasn't had time
                                   # to be carded yet (#224) — the worker's own
                                   # post-merge sequence (deploy, verify, THEN
                                   # the card) takes minutes by design, and the
                                   # original code had no minimum age at all,
                                   # so it beat the worker's own card by
                                   # seconds on every clean run
CARD_MAX_LISTED = 8               # the phone gets numbers, not a wall

_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
                        re.I)


def merged_closes(root, base, since_ts, git_run=None):
    """`{issue_num: commit_epoch_ts}` for every issue closed by a commit on
    `base` since `since_ts`.

    GitHub closes an issue from a `Closes #N` in a commit reachable from the
    default branch, so this is the delivery event itself, read locally — and
    the commit's OWN timestamp is carried forward so `card_reconcile` can
    gate a fresh merge with a per-TICKET grace period (#224), not just the
    window's edge. A record separator (`%x1e`) marks the start of each
    commit's block and a field separator (`%x1f`) splits its timestamp from
    the message, so a `Closes #N` mentioned in either the subject or the
    body is still found — the whole message stays in the same record. When
    the same issue is mentioned in more than one commit within the window
    (rare), the MOST RECENT commit's timestamp wins — the newest mention is
    the honest "when did this actually settle" answer."""
    text = (git_run or _default_git_run)(
        ["git", "-C", str(root), "log", base,
         "--since=@%d" % int(since_ts), "--format=%x1e%ct%x1f%s%n%b"])
    result = {}
    for rec in (text or "").split("\x1e"):
        ts_str, sep, rest = rec.partition("\x1f")
        if not sep:
            continue
        try:
            ts = float(ts_str)
        except (TypeError, ValueError):
            continue
        for m in _CLOSES_RE.finditer(rest):
            n = int(m.group(1))
            result[n] = max(ts, result.get(n, ts))
    return result


def _normalize_closed(value):
    """Normalize what a `closed_fetch` callback returns into the same
    `{issue_num: ts_or_None}` shape `merged_closes` produces.

    Accepts a `{n: ts}` dict, a list of `(n, ts)` pairs, or a bare list of
    ints (the pre-#224 contract some callers — and this job's own existing
    tests — still use). A bare int gets `ts=None`, and `card_reconcile`
    treats an unknown timestamp as ALREADY past grace: not knowing when a
    ticket closed must never suppress a report the pre-grace code would
    have sent instantly — "report now" is the safe default, never "wait
    forever for a timestamp that will never arrive"."""
    if isinstance(value, dict):
        return {int(k): (None if v is None else float(v))
                for k, v in value.items()}
    out = {}
    for item in value or ():
        if isinstance(item, (tuple, list)) and len(item) == 2:
            n, ts = item
            out[int(n)] = None if ts is None else float(ts)
        else:
            out[int(item)] = None
    return out


def _commits_in_window(root, base, since_ts, git_run=None):
    out = _git_first_line(root, ["rev-list", "--count",
                                 "--since=@%d" % int(since_ts), base], git_run)
    try:
        return int(out)
    except (TypeError, ValueError):
        return 0


def card_reconcile(now, run, state, cwd_by_sid, send_fn=None, dry_run=False,
                   git_run=None, card_probe=None, marker_ok=None,
                   owner_by_sid=None, window=None, grace=None,
                   closed_fetch=None, reopen_fetch=None):
    """Job 25 — see the section comment.

    Gated on `card_probe` (the "wired = on" convention of jobs 8/11/16/24):
    the probe carries the confirming fetch, so a base ref that had merely
    gone stale locally cannot on its own claim a ticket went unreported.

    Two independent fixes over the original #134 shape, both from live
    false positives measured on this box (#224):

      * GRACE PERIOD, per ticket. The original code had no minimum age at
        all — only `window`, a MAXIMUM age — so it beat the worker's own
        delivered card by seconds on every clean run (merge closed 3
        tickets at 11:46:21, this job pinged "unreported" at 11:46:24,
        the worker's cards were `sent` at 11:49:06-11:49:14). A ticket is
        only "pingable" once `now - <its own closing commit's timestamp>
        >= grace` — read from `merged_closes`'s per-ticket dict, never
        derived from the window's edge, so the grace genuinely applies
        per ticket rather than per repo (two tickets in the same repo, one
        old and one fresh, are judged independently in the same sweep).
      * DEDUP, per ticket, forever — not per repo per calendar day. A
        ticket whose card can never arrive (the worker died, or the issue
        closed with no PR at all) used to be re-announced once a day for
        as long as it sat inside the 48h window, and any NEWLY stuck
        ticket restarted that clock for every OTHER ticket in the same
        repo (834 consecutive sweeps of the same six camera-box tickets,
        ~14h). `state["card_unreported"][root]["pinged"]` now remembers
        every issue number this job has ever pinged for that repo — a
        ticket earns exactly one nag, and a genuinely NEW unreported
        ticket still pings immediately, even in the sweep that just
        deduped an old one. Entries are pruned once their ticket ages out
        of `closed` (the 48h window), so the memory stays bounded.

    One repo is examined once per sweep however many panes sit in it, and
    DETECTION is logged every sweep regardless of grace/dedup (issue #36's
    print-always convention — the log is a local journal line, never the
    thing that reaches the user's phone)."""
    if card_probe is None:
        return []
    window = CARD_WINDOW_S if window is None else window
    grace = CARD_GRACE_S if grace is None else grace
    if marker_ok is None:
        try:
            from notify import marker_delivered as marker_ok
        except ImportError:
            return []
    owner_by_sid = owner_by_sid or {}
    seen = dict(state.get("card_unreported") or {})
    logs, live, examined = [], set(), set()

    for sid, cwd in sorted((cwd_by_sid or {}).items()):
        root = _git_first_line(cwd, ["rev-parse", "--show-toplevel"], git_run)
        if not root or root in examined:
            continue
        examined.add(root)
        base = _git_base_ref(root, git_run)
        if not base:
            continue                      # unmeasurable — never a finding
        live.add(root)

        # The repo NAME comes from `origin`, never the directory basename:
        # the checkout is `parovanie_produktov` while every marker is keyed
        # `parovanie-produktov`, so a directory-derived key matches nothing
        # and would report every ticket as unreported. Resolved HERE, ahead
        # of `closed` — the #182 reopen-clear step below needs it and must
        # run independent of whether anything closed in THIS sweep's window
        # (adversarial-review finding: it used to sit AFTER `if not closed:
        # continue`, so a repo where nothing closed this sweep — the ticket's
        # OWN original close having aged out of the window, with no sibling
        # ticket closing to keep `closed` non-empty — never reached it at
        # all, and the exact bug #182 exists to fix kept reproducing).
        try:
            from notify import repo_name_for
            name = repo_name_for(root)
        except ImportError:
            name = ""

        # #182: a REOPENED ticket's existing run-card marker refers to a
        # PRIOR close and must not keep deduping the card the NEXT close
        # earns. `reopen_fetch` (wired = on, like `closed_fetch`) answers
        # "of the issue numbers that already have a marker for this repo,
        # which are OPEN again right now" — a marker for one of those is
        # cleared so the next close claims fresh. Gated separately from the
        # rest of this job (never on `card_probe`/`closed` at all) so a
        # caller that never wires it — every pre-#182 test — sees NO
        # behavior change at all. An existing marker predates THIS sweep's
        # window entirely, which is exactly why this cannot depend on
        # `closed` being non-empty.
        if name and reopen_fetch is not None:
            try:
                from notify import card_marker_numbers, forget_marker
            except ImportError:
                card_marker_numbers = forget_marker = None
            if card_marker_numbers is not None:
                candidates = card_marker_numbers(name)
                if candidates:
                    try:
                        reopened = reopen_fetch(root, candidates)
                    except Exception as e:
                        logs.append("card-reconcile reopen-fetch-failed %s: %r"
                                    % (root, e))
                        reopened = None
                    for n in sorted(reopened or ()):
                        forget_marker("%s#%d" % (name, n))
                        logs.append("card-reconcile reopen-cleared %s#%d"
                                    % (name, n))

        # CONFIRM, then announce (job 24's contract, reused verbatim): the
        # probe fetches the base ref, and the measurement happens after it.
        try:
            card_probe(root, base)
        except Exception as e:
            logs.append("card-reconcile probe-failed %s: %r" % (root, e))
        closed = merged_closes(root, base, now - window, git_run)
        # `verified_source` tracks whether `closed` ALREADY passed GitHub's
        # own CLOSED_EVENT->closer check (#230's `_watchdog_closed_fetch`),
        # as opposed to being a bare local commit-message-keyword match
        # (`merged_closes`) that has not yet been verified against anything.
        verified_source = False
        if not closed and closed_fetch is not None:
            # A repo that never writes `Closes #N` trailers is invisible to
            # the local read — its issues close from the PR body, which is a
            # GitHub-side fact. tvdole is exactly that repo, and it is one of
            # the two in #134's own evidence, so the local-only design would
            # have been blind to half the incident. The fallback is bounded
            # to precisely that signature — fresh merges on the base branch,
            # yet zero trailers — so a repo that answers locally never costs
            # an API call, and a parked repo never costs one either.
            if _commits_in_window(root, base, now - window, git_run) > 0:
                try:
                    closed = _normalize_closed(closed_fetch(root, now - window))
                    verified_source = True
                except Exception as e:
                    logs.append("card-reconcile closed-fetch-failed %s: %r"
                                % (root, e))
                    closed = {}
        if not closed:
            seen.pop(root, None)
            continue

        if not name:
            continue

        # Two ways a ticket can already have been reported: its OWN card, or
        # a catch-up DIGEST that accounted for it (#141). The digest writes
        # its own namespace, and only on a delivered POST — so a digest that
        # never reached Discord suppresses nothing here.
        #
        # Resolved SEPARATELY from `repo_name_for`, whose ImportError path
        # skips the repo entirely: the timer runs the working tree every
        # 60s, so a mid-push checkout can genuinely see a watchdog newer
        # than notify. Sharing that import would turn a transient
        # missing-symbol window into a silent, unlogged death of the whole
        # backstop — the exact shape this job exists to catch.
        try:
            from notify import backfill_marker_key
        except ImportError:
            backfill_marker_key = None
            logs.append("card-reconcile backfill-namespace-unavailable %s"
                        % name)
        missing = [n for n in sorted(closed)
                   if not marker_ok("%s#%d" % (name, n))
                   and not (backfill_marker_key is not None
                            and marker_ok(backfill_marker_key(name, n)))]
        if not missing:
            seen.pop(root, None)
            continue

        # Detection is logged unconditionally, on `missing` alone (never
        # gated on grace or on per-ticket dedup) — this is a local journal
        # line, not the phone ping, so it stays true to issue #36's
        # print-always convention regardless of what happens next.
        logs.append("card-unreported %s n=%d issues=%s"
                    % (name, len(missing),
                       ",".join(str(n) for n in missing[:CARD_MAX_LISTED])))

        prev = seen.get(root) or {}
        # Per-ticket "already pinged, ever" memory. Pruned to tickets still
        # inside `closed` — once a ticket ages out of the 48h window it can
        # never be "missing" again, so its entry is dead weight.
        pinged = {k: v for k, v in (prev.get("pinged") or {}).items()
                  if int(k) in closed}
        # A ticket is still inside its own grace window when its closing
        # commit's timestamp is known AND recent. An UNKNOWN timestamp
        # (the `closed_fetch` bare-int-list fallback) is never treated as
        # "inside grace" — not knowing when a ticket closed must never
        # suppress a report the pre-#224 code would have sent instantly.
        pingable = [n for n in missing
                    if not (closed.get(n) is not None
                            and now - closed[n] < grace)
                    and str(n) not in pinged]

        # #232: `merged_closes` is a bare commit-message-keyword match — it
        # is NOT proof GitHub actually closed the ticket from that commit
        # (odoo-erp: 22 local `Fixes #N` matches on a non-default branch,
        # none of which ever triggered a GitHub auto-close). Only a
        # candidate that already came from the VERIFIED `closed_fetch`
        # fallback (`verified_source`) skips this — asking again would cost
        # a second `gh` call for an answer already known. Every OTHER
        # candidate that survived grace + dedup (i.e. would actually nag
        # this sweep) is checked against the SAME CLOSED_EVENT->closer
        # GraphQL query #230 built, and only a confirmed merged-PR closer
        # is kept. A `closed_fetch` failure (raises, times out, or itself
        # degrades to `None`) drops every currently-pingable candidate for
        # THIS SWEEP ONLY — nothing here is added to `pinged`, so a genuine
        # finding is retried next sweep rather than silently swallowed
        # forever (the direction the pre-existing fallback-failure branch
        # above already takes).
        if pingable and not verified_source and closed_fetch is not None:
            try:
                confirmed = _normalize_closed(closed_fetch(root, now - window))
            except Exception as e:
                logs.append("card-reconcile verify-failed %s: %r" % (root, e))
                confirmed = {}
            rejected = [n for n in pingable if n not in confirmed]
            if rejected:
                logs.append("card-reconcile verify-rejected %s issues=%s"
                            % (name, ",".join(str(n)
                                              for n in rejected[:CARD_MAX_LISTED])))
            pingable = [n for n in pingable if n in confirmed]

        if dry_run or send_fn is None or not pingable:
            # `send_fn is None` must NOT mark anything pinged — nothing was
            # delivered, so a later sweep still owes the user the alert
            # (jobs 21/24's contract, reused verbatim). A `pingable` empty
            # only because everything is still inside grace, or already
            # pinged, is equally a no-op this sweep.
            seen[root] = {"pinged": pinged}
            continue

        shown = ", ".join("#%d" % n for n in pingable[:CARD_MAX_LISTED])
        more = ("" if len(pingable) <= CARD_MAX_LISTED
                else " a ďalších %d" % (len(pingable) - CARD_MAX_LISTED))
        status = send_fn(
            "\U0001f4ee **%s** — %d hotových ticketov bez hlásenia\n"
            "> Tieto tickety sa dokončili a zavreli, ale na telefón o nich "
            "neprišla žiadna správa: %s%s.\n"
            "> Práca je hotová — chýba len hlásenie o nej."
            % (name, len(pingable), shown, more),
            owner=owner_by_sid.get(sid) or None,
            dedup_key="card-unreported:%s:%s"
                      % (name, "-".join(str(n) for n in pingable)),
            dry_run=dry_run)
        logs.append("card-unreported PING %s -> %s" % (name, status))
        for n in pingable:
            pinged[str(n)] = now
        seen[root] = {"pinged": pinged}

    if not dry_run:
        state["card_unreported"] = {k: v for k, v in seen.items() if k in live}
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
# a watchdog restart (jobs 12/18, removed in #132) relaunched via `claude -c`, which CONTINUED
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

# --- the FOURTH shape (same job, refining the FIRST): #101 -----------------
# Two gaps in the original bounded-retry design, both from the same live
# incident (dev2, 2026-07-27): (a) `deliver_with_stash` can refuse BEFORE
# sending a single keystroke — the pane still holds a foreign draft, is
# mid-turn, or another stash is already in flight — and that refusal used to
# count exactly like a real, verified delivery failure. Two such refusals (the
# draft simply hadn't been submitted yet) permanently gave up the whole
# streak window even though nothing was ever actually attempted. Only a
# refusal that happens AFTER `deliver_with_stash` starts touching the pane
# (stash didn't verify bare, the type didn't land, the submit wasn't
# recovered) counts toward the cap now — a pre-send refusal is retried next
# sweep for free. (b) the revival path had no bound on how OLD the last
# `Goal set:` marker it trusts can be: a goal that finished (or died) days ago
# and was never explicitly cleared stays "set" forever, and the natural-
# completion viewport check (`GOAL_ACHIEVED_MARKER in _above_input_box`) only
# protects a RECENT death — days of normal conversation scroll the
# `✔ Goal achieved` line out of the visible viewport. Live: a marker from
# `2026-07-25T20:07:21Z`, three days old, from an already-closed run, was
# still the only one on record and got typed at as if it were current.
#
# #322 widened this from a STASH-only set to a genuinely GENERAL one: the
# plain (non-draft) branch's own `_send_goal_verified` has the identical
# pre-send-refusal shape (it takes a FRESH, LIVE re-capture right before
# typing — the pane can race from idle, at the caller's stale top-of-sweep
# capture, to busy in that gap — same #176-F3 pattern) but used to `return
# False` from both its pre-send checks with an EMPTY `logs` list, so this
# carve-out's own `dlogs[-1] in ...` test could never recognise it. Live on
# dev1: two such races (a blocking foreground pytest call straddling the
# sweep boundary) permanently exhausted the 2-attempt cap even though the
# pane was demonstrably idle again a minute later.
_GOAL_REARM_TRANSIENT_REASONS = frozenset((
    "stash-abort: slot occupied",
    "stash-abort: no free prompt",       # #189 renamed: emptiness is no longer
                                         # a precondition, so the old
                                         # "not idle-with-draft" no longer
                                         # describes what this refusal means
    "stash-abort: live turn",
    "goal-verify-abort: not-bare",       # #322 — `_send_goal_verified`'s
                                         # entry check found the box already
                                         # occupied/unreadable; zero keystrokes
    "goal-verify-abort: raced-busy",     # #322 — the SECOND, live re-capture
                                         # (taken immediately before typing)
                                         # found the pane had already gone
                                         # busy since the caller's own stale
                                         # capture; zero keystrokes
))
GOAL_REARM_MAX_DARK_S = 6 * 3600    # a `set` marker (or the last sweep that
                                    # actually saw `◎ /goal` lit, whichever is
                                    # newer) older than this is presumed
                                    # already resolved and is never revived —
                                    # generous past any normal watchdog-state
                                    # gap, far short of the incident's 3 days
GOAL_REARM_GIVEUP_RESET_S = 15 * 60  # #322 — a reachable exit condition for
                                    # the give-up state: once GAVE UP has held
                                    # this long AND the pane is PROVABLY idle
                                    # right now (read live, every sweep,
                                    # independent of anything the give-up
                                    # state itself blocks — the #134 test),
                                    # retry instead of skipping forever. 15
                                    # min is drastically shorter than the 2h
                                    # streak window while staying long enough
                                    # that a genuinely-still-broken pane does
                                    # not ping-storm every few minutes.
GOAL_REARM_GIVEUP_MAX_RESETS = 1    # #322 REOPENED (adversarial-review
                                    # MAJOR-1) — bounds how many times ONE
                                    # streak can retry off the give-up state:
                                    # without this an idle-but-permanently-
                                    # broken pane (idleness is its STEADY
                                    # STATE) retries every
                                    # GOAL_REARM_GIVEUP_RESET_S forever until
                                    # the blind GOAL_REARM_STREAK_S reset —
                                    # measured 10 pings / 152 multi-KB /goal
                                    # submissions per 3h. One extra try, then
                                    # the SAME permanent skip until the
                                    # streak reset, is the only behavioural
                                    # difference from before this ticket for
                                    # a genuinely unrecoverable pane.
GOAL_REARM_SLOT_STUCK_MAX = 3       # #322 REOPENED (2nd adversarial-review
                                    # MAJOR-1) — bounds how many CONSECUTIVE
                                    # "stash-abort: slot occupied" transient
                                    # skips a session may accumulate before
                                    # it counts as a real failure instead —
                                    # a slot deliver_with_stash's own
                                    # collapsed-paste early return stranded
                                    # (never popped, zero further
                                    # keystrokes) would otherwise refuse
                                    # forever as "transient" and never let
                                    # `n` reach `max_attempts`, so GAVE UP
                                    # (and this round's own reachable
                                    # give-up-reset) is never reached for
                                    # that pane at all.

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
# --- the THIRD shape (same job, the goal is ALIVE but running OLD text) ----
# `/goal` reads the autopilot template ONCE, at arm time. Change the template
# in `skills/autopilot/SKILL.md`, `airuleset.py push` it to all six boxes, and
# every ALREADY-RUNNING loop keeps evaluating the OLD stop conditions —
# forever, since nothing re-reads it (#64). That is not cosmetic: #58's
# ticket-boundary `✅ Work Complete` rule is what fires the compaction chain,
# so a loop still running a pre-#58 template never compacts and its context
# grows without bound (the measured 214K -> 432K david case).
#
# The whole difficulty is telling "the template, older version" apart from "a
# goal the user wrote themselves". Measured across 62 commits of this repo's
# own template history, a similarity threshold CANNOT do it — the ranges
# overlap:
#     tpl1 vs tpl2, both CURRENT, DIFFERENT variants      ratio 0.7100
#     tpl0 current vs tpl0 from 2026-07-05, SAME variant  ratio 0.7279
# and the cost of the two mistakes is wildly asymmetric: missing a stale loop
# is merely the status quo, while a false positive silently overwrites what
# the user asked for. So identity here is an EXACT hash and never a threshold.
#
# The hash is taken over a NORMALIZED form (parenthetical citations, backtick
# spans and punctuation dropped, lowercased, whitespace collapsed) because an
# armed payload is frequently the template minus a few editorial citations —
# the agent trims them when it prints the line. Live on this box when the
# ticket was worked, all three cases were present at once:
#     forestshop   payload == template verbatim              -> exact match
#     restreamer   template minus 3 citations, ratio 0.9808,
#                  byte-matching NO shipped version at all    -> exact match
#     airuleset    the user's own goal, ratio 0.2420          -> no match
# and normalisation does not blur real changes: 26 distinct raw historical
# template strings collapse to 25 normalised ones, the single merged pair
# being a citation-only edit, which is exactly what should be ignored.
#
# Proof that a payload IS the template comes from OBSERVATION, not from
# similarity: the first time this job sees a session's armed goal matching a
# CURRENT template it records the variant (`tvar`). Only such a TRACKED
# session is ever re-armed, and only with the current text of the SAME variant
# it was already running — the authority profile is never re-resolved, so a
# branch-merge stream can never be handed the full-authority template. A
# payload that never matched is untouchable by construction, not by threshold.
#
# Delivery is ONE `/goal <new>` — live-verified on CC 2.1.220 that this
# REPLACES an armed goal (typing GOAL-BRAVO over an armed GOAL-ALPHA made
# every subsequent Stop-hook evaluation cite BRAVO, and the later `/goal
# clear` reported BRAVO as the active one). The ticket's proposed `/goal
# clear` first is therefore not just unnecessary but harmful: it opens a
# window with no loop at all, and if the second step failed the marker would
# read `cleared`, which the #76 branch deliberately never revives.
#
# Confirmation needs no new mechanism: a successful re-arm changes the
# payload, the next sweep's hash matches the current template, and the drift
# state clears itself. Until then it is bounded exactly like every other shape
# here — `GOAL_DRIFT_MAX_ATTEMPTS` deliveries per target, then ONE Discord
# ping and silence.
GOAL_ARM_ACTIVE_PREFIX = ('A session-scoped Stop hook is now active with '
                          'condition: "')
_GOAL_ARM_PROBE = GOAL_ARM_ACTIVE_PREFIX[:-1].encode()   # quote-free (JSON)
GOAL_DRIFT_MAX_ATTEMPTS = 2         # deliveries per NEW template hash
GOAL_DRIFT_MIN_QUIET_S = 30         # the transcript must have been quiet this
                                    # long before a multi-KB goal is typed —
                                    # a loop mid-turn writes entries
                                    # continuously, so this is what separates
                                    # a PAUSED loop from a running one (a
                                    # pane's momentary look is not enough:
                                    # live-observed, a delivery started in
                                    # such a moment, the loop fired again
                                    # before the Enter landed, and the whole
                                    # payload stayed UNSUBMITTED in the box,
                                    # clearable by neither Escape nor C-u)
_GOAL_TEMPLATE_RX = re.compile(r"^/goal .+$", re.M)

GOAL_STALL_TEXT = "continue"        # same minimal wake job 1 uses
GOAL_STALL_IDLE_S = 15 * 60         # transcript stale this long while ARMED
GOAL_STALL_INTERVAL_S = 15 * 60     # min spacing between nudges
GOAL_STALL_MAX_NUDGES = 3           # then ONE ping, then silence

# #161 — the user's own directive (2026-07-29): a `❓ NEEDS YOU` block should
# wait for an answer at most ~30 minutes; past it, the loop is told to park
# that ticket and move to other backlog work rather than sit stopped
# forever. `_goal_stall_nudge` above still REFUSES to nudge past a `❓`
# marker at all (its own pinned invariant, unchanged) — this is a wholly
# separate, independently-gated sub-branch of the SAME job (never a new
# watchdog job, per the repo FREEZE).
GOAL_QUESTION_TIMEOUT_S = 30 * 60   # measured from DELIVERY (discord-
                                    # questions.json's own `ts`), never from
                                    # when the question was merely drafted —
                                    # a ping that never sent must never start
                                    # this clock (the issue's own design note)
# #161-review MAJOR M1 — a matching-session map entry alone is not enough:
# the map can carry an OLDER, unrelated question for the same session (a
# stale ASKED entry that outran pruning, or the wrong sibling of a genuine
# multi-question history). The delivered ping this function trusts must
# have been posted for THIS SPECIFIC block — proven by proximity: a real
# delivery lands within seconds of the assistant entry that raised it
# (the Stop hook fires immediately), so a map entry whose `ts` is not
# CLOSE to the transcript's own `❓ NEEDS YOU` entry timestamp is not about
# the current block and must be refused, never adopted as a stand-in.
GOAL_QUESTION_MATCH_SLOP_S = 30      # delivery slightly BEFORE the entry's
                                     # own timestamp — clock skew only
GOAL_QUESTION_MATCH_WINDOW_S = 600   # delivery AFTER the entry — generous
                                     # for hook retry/backoff latency
GOAL_QUESTION_PARK_TEXT = (
    "question-timeout: 30 min bez odpovede na poslednu otazku. Zaparkuj "
    "TENTO tiket (needs-answer komentar + label na tikete, otazku nechaj "
    "sledovanu) a pokracuj na iny tiket z backlogu; k tomuto sa vrat, ked "
    "prijde odpoved (Discord reply sa dorucuje priamo do tejto session)."
)


def _needs_you_block_ts(tpath):
    """`(marker_line, entry_ts)` for the session's last REAL assistant
    message, ONLY when it is a genuine, TRAILING `❓ NEEDS YOU:` status
    line — never merely a message that MENTIONS those words somewhere in
    its body (#161-review MAJOR M1, scenario A: a completion report or a
    `/goal` template discussion naming "NEEDS YOU" in prose is not a
    block). Mirrors `transcript_last_marker_line`'s own walk/skip
    semantics (synthetic/tool-only sentinels skipped, an api-error entry
    refuses) rather than calling it, because this ALSO needs the entry's
    own `timestamp` for the delivery-proximity check below — one walk,
    not two. `(None, None)` when the last real turn is not a trailing
    NEEDS YOU line, or its timestamp cannot be parsed (never guess)."""
    from datetime import datetime
    for entry in reversed(_iter_jsonl_tail(tpath)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        if entry.get("isApiErrorMessage") is True:
            return None, None
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue
        nonblank = [ln for ln in text.splitlines() if ln.strip()]
        line = ""
        for ln in reversed(nonblank[-3:]):
            if _MARKER_RX.match(ln):
                line = ln
                break
        if "❓" not in line or "NEEDS YOU" not in line:
            return None, None
        try:
            ts = datetime.fromisoformat(
                str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except Exception:
            return None, None
        return line, ts
    return None, None


# #350 — every entry in `_MACHINE_PROMPT_PREFIXES` EXCEPT the two
# Discord-relay shapes: `_goal_blocked_on_unanswered_question` needs the
# OPPOSITE exclusion set from `_last_human_prompt_ts` (which deliberately
# excludes "Odpoveď z Discordu:"/"Odpoveď užívateľa..." because job 9's own
# question is "did a HUMAN type this directly into the pane", where a
# Discord relay must NOT count). #350's question is "has this question
# been ANSWERED at all" — a Discord-relayed reply genuinely IS the
# answer, so it stays counted as a real newer turn; every OTHER machine
# nudge in that list (a watchdog backstop, a task-notification, a bare
# `/goal ` re-arm attempt) is NOT an answer and must be treated as
# transparent bookkeeping instead, so the walk keeps looking further back
# for the actual decision point.
_GOAL_BLOCKED_ANSWER_TRANSPARENT_PREFIXES = tuple(
    p for p in _MACHINE_PROMPT_PREFIXES
    if p not in ("Odpoveď z Discordu:", "Odpoveď užívateľa na tvoju otázku")
) + (
    # A `/compact` continuation summary is a real, top-level `user`-typed
    # entry (confirmed live across several transcripts under
    # ~/.claude/projects/) that is NEITHER `isMeta` NOR a bare
    # `<system-reminder>`-wrapped block -- Claude Code writes its own
    # summarizer's output as plain string content, so it slips past
    # every OTHER transparent check above. It is still never a genuine
    # typed answer: without this entry, a compaction landing right
    # after a real (A)-blocked stop (this repo's own job 14/#65 compact
    # machinery can fire on an armed `/goal` session at a ticket
    # boundary) would misread the summary text as "a GENUINE later real
    # turn exists" and wrongly release a block the user never actually
    # answered. Scoped to THIS one function's own transparent-prefix
    # set (a `+` concatenation, not a change to `_MACHINE_PROMPT_
    # PREFIXES` itself) precisely so `_last_human_prompt_ts` and its
    # many other callers stay byte-for-byte unaffected.
    #
    # #350 round-2 review MINOR: this literal is CC's summarizer WORDING,
    # which could reword in a future build and go silently stale. The
    # `entry.get("isCompactSummary")` check in the function body below is
    # CC's own STRUCTURAL flag on the identical entries (confirmed live:
    # 790/790 real compact-continuation entries across a 120-transcript
    # sample carry it) -- ORing both means a future wording change alone
    # can never reopen this gap.
    "This session is being continued from a previous conversation",
)


def _goal_blocked_on_unanswered_question(tpath):
    """The genuine terminal `❓ NEEDS YOU:` marker LINE (a truthy `str`)
    when this session's own STOP CONDITION (A) — BLOCKED ON MY ANSWER —
    genuinely still holds RIGHT NOW; `''` otherwise.

    #350 — job 20's ACHIEVED-marker check (further down `goal_rearm`,
    `GOAL_ACHIEVED_MARKER in _above_input_box(captured)`) used to treat
    "CC's own footer shows `✔ Goal achieved` yet the repo's backlog is
    still open" as unconditional proof the loop died PREMATURELY —
    re-arming it every time. But EVERY shipped `/goal` template's own
    STOP CONDITIONS include (A) as an EQUALLY legitimate stop: the loop
    correctly ends there, footer dark, backlog untouched, waiting on the
    USER — and the achieved-marker check alone could never tell the two
    apart. Livelock, live-evidenced 2026-08-10 (dev1, session zbynek-4):
    goal stops on (A) -> watchdog re-arms it a minute later -> the
    freshly re-armed session immediately re-evaluates the SAME unanswered
    question and re-stops -> repeat, every cycle re-sending the whole
    context for nothing, "for days" per the ticket's own words.

    Structurally MIRRORS `_needs_you_block_ts` (#161's own armed-branch
    "genuine trailing NEEDS YOU, never a mere mention" classifier) — same
    walk source, same sentinel/api-error skip, same 3-trailing-line
    marker search, same `"❓" in line and "NEEDS YOU" in line` substring
    test — rather than calling it, because #350 ALSO needs to know
    whether a NEWER real turn (any GENUINE `user`-typed answer — a
    human's own reply, OR a Discord-relayed one, `compose_reply_prompt`,
    delivered by job 7 as a plain `user`-typed entry — needs no special-
    casing here since it's simply "a later real turn" like any other)
    already exists — something `_needs_you_block_ts` structurally cannot
    answer (it walks PAST every non-assistant entry silently, by design,
    since ITS own caller `_goal_question_park_nudge` answers a different
    question: "how long since DELIVERY", never "has this been
    superseded"). Per this file's own established rule
    (`_last_real_turn_ts_excluding_command_bookkeeping`'s own docstring:
    never widen a shared classifier's meaning for a caller with a
    different need), this is a SEPARATE reader rather than a change to
    `_needs_you_block_ts` itself — and a single self-contained walk,
    rather than composing two independently-windowed reads, sidesteps
    any risk of the two disagreeing about which entries are even "in
    view".

    A `user`-typed entry matching `_GOAL_BLOCKED_ANSWER_TRANSPARENT_
    PREFIXES` (a watchdog nudge, a task-notification, a bare `/goal `
    re-arm attempt — every machine-injected shape this file already
    knows about, EXCEPT the two Discord-relay ones), carrying only a
    `tool_result` (a routine post-tool-call entry, mirrors `_last_human_
    prompt_ts`'s own extraction), flagged `isMeta` (CC's own system-
    injected user-type entries — a goal-arm confirmation echo, a resume-
    injection notice, a skill-directory listing, a malformed-tool-call
    notice — none of which is a genuine typed answer either, and none of
    which carries a recognisable prefix at all, so this check catches
    what the prefix list structurally cannot), or flagged
    `isCompactSummary` (CC's own STRUCTURAL marker on a `/compact`
    continuation entry, checked alongside the wording-based prefix above
    so a future summarizer rewording alone can never reopen the gap) is
    treated as TRANSPARENT bookkeeping, never a genuine answer — the walk
    keeps scanning further back for the real decision point instead of
    concluding "answered". Without this, a bare nudge (or an `isMeta`
    entry) landing as the transcript's newest entry with no subsequent
    assistant turn yet would misread as "answered" even though the user
    never replied — narrower
    than the #350 ticket's own reported incident, but the same
    false-negative direction it explicitly warns against.

    Returns `''` the moment the walk finds a GENUINE newer `user`-typed
    turn — the REACHABLE exit condition #350 needs (mirrors #134: name
    the event that releases the guard, then prove it fires without the
    guarded action). If the loop is genuinely STILL blocked (nothing new
    happened, or an intervening nudge only produced another turn that
    repeats the SAME unanswered question — message-status-marker.md's
    own verbatim re-poke rule), the newest real turn resolves right back
    to an assistant entry ending in `❓ NEEDS YOU:` on the very next
    sweep, so re-arming stays correctly suppressed.

    Also returns `''` on an api-error entry, a message with no marker at
    all, or a message ending in a DIFFERENT terminal marker (`⏳`/`✅`, or
    a bare `❓ ASKED:` with no accompanying `NEEDS YOU` — ask-and-continue,
    whose own terminal line is `⏳ WORKING` per message-status-marker.md,
    and must never be misread as (A)-BLOCKED — the ticket's own explicit
    worry) — the walk stops the moment the truly-last non-blank line
    resolves to anything other than a genuine NEEDS YOU line, never
    scanning further back for one instead. And `''` on an unreadable/
    empty transcript — an UNMEASURABLE read must never manufacture a NEW
    suppression; the existing FALSE-ACHIEVED machinery this sits in
    front of already has its own bounded, reachable give-up/reset gates
    for a genuinely-stuck pane.

    Accepted, DELIBERATELY undocumented-in-code residual (round-1
    review, defensible either way): an interrupt PSEUDO-ENTRY — the
    synthetic `user`-typed record Claude Code writes when the user
    presses Escape mid-turn (no recognised prefix, no `isMeta`, no
    `isApiErrorMessage`) — is NOT distinguished from a genuine typed
    answer here, same as it is not distinguished by `_last_human_
    prompt_ts` either. An interrupt is, in the overwhelming common
    case, itself accompanied by real subsequent human input (the user
    interrupted TO type something), so this walk resolving it as "a
    later real turn exists" is very rarely wrong in practice — and
    building a dedicated exclusion for a shape this file has never
    needed to identify anywhere else would be exactly the kind of
    speculative widening the FREEZE forbids (fix what has actually
    failed in production, not every theoretically-adjacent shape)."""
    for entry in reversed(_iter_jsonl_tail(tpath)):
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype not in _REAL_TURN_TYPES:
            continue
        if etype == "user":
            if entry.get("isMeta") or entry.get("isCompactSummary"):
                continue            # CC-injected system text -- transparent
            msg = entry.get("message")
            c = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(c, str):
                t = c
            elif isinstance(c, list):
                if any(isinstance(b, dict) and b.get("type") == "tool_result"
                       for b in c):
                    continue            # a routine tool-call result -- transparent
                t = " ".join(b.get("text", "") for b in c
                             if isinstance(b, dict) and b.get("type") == "text")
            else:
                continue                # unparseable content -- transparent
            t = t.strip()
            if (not t or t in _MACHINE_PROMPT_EXACT
                    or any(t.startswith(p)
                           for p in _GOAL_BLOCKED_ANSWER_TRANSPARENT_PREFIXES)):
                continue                # a machine nudge, not a user answer
            return ""                   # a GENUINE later real turn exists
        if entry.get("isApiErrorMessage") is True:
            return ""                  # api-error, not a status marker
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue                   # synthetic/tool-only -- keep scanning back
        nonblank = [ln for ln in text.splitlines() if ln.strip()]
        line = ""
        for ln in reversed(nonblank[-3:]):
            if _MARKER_RX.match(ln):
                line = ln
                break
        if "❓" not in line or "NEEDS YOU" not in line:
            return ""
        return line
    return ""


def _goal_question_delivered_ts(sid, questions_path=None, logs=None):
    """Epoch seconds the NEWEST `❓` ping for session `sid` was DELIVERED,
    read from `discord-questions.json` (`notify.record_question` stamps
    `ts` only on a CONFIRMED 2xx POST — never a draft/compose time). `None`
    when no delivery is on record for this session at all (Discord
    unconfigured, the send failed, or the question was already answered/
    pruned) — #161's own design note: a ping that never reached the user
    must never start the 30-minute clock, so an unresolvable lookup is a
    refusal, never a guess. A degraded lookup (the `notify` module itself
    unimportable/unreadable — a mid-push checkout window, since the timer
    runs the working tree live) appends ONE line to `logs` (when given)
    rather than failing silently forever (#161-review MINOR m1).

    #161-review MAJOR M1 (scenario B): several entries can match the same
    session (a multi-question history, or a stale sibling that outran
    pruning) — this returns only the NEWEST such `ts`; it is the CALLER's
    job (`_goal_question_park_nudge`'s proximity check against the
    transcript's own entry timestamp) to confirm that newest entry is
    actually ABOUT the current block, not merely the same session. Do not
    trust this function's return value alone as "the current question".

    `questions_path` is the test seam (mirrors `backlog_fetch`/
    `templates_path` elsewhere in this file) — production always resolves
    the real `~/.claude/discord-questions.json` via
    `notify.load_questions()`'s own default."""
    try:
        from notify import load_questions
    except Exception as e:
        if logs is not None:
            logs.append("goal-question-timeout unmeasurable (notify import "
                        "failed, %r)" % (e,))
        return None
    try:
        qmap = (load_questions(path=questions_path) if questions_path
                else load_questions())
    except Exception as e:
        if logs is not None:
            logs.append("goal-question-timeout unmeasurable (load_questions "
                        "failed, %r)" % (e,))
        return None
    if not isinstance(qmap, dict):
        return None
    best = None
    for rec in qmap.values():
        if not isinstance(rec, dict) or str(rec.get("session") or "") != sid:
            continue
        try:
            ts = float(rec.get("ts"))
        except (TypeError, ValueError):
            continue
        if best is None or ts > best:
            best = ts
    return best


def _goal_question_park_nudge(now, run, rec, sid, cwd, pid, captured, tpath,
                              loc, send_fn, dry_run, handled, projects_dir,
                              questions_path=None, persist=None):
    """#161 — the BOUNDED-WAIT escape hatch for a genuine, stopped
    `❓ NEEDS YOU` block. Mutates `rec`; returns log lines. `persist`
    (optional, mirrors jobs 8/11/27/28's SAME shape): called immediately
    after `rec['qparked_ts']` is set and BEFORE the keystroke send — a
    process killed between the two must never lose the "already parked"
    memory and re-send (#161-review MAJOR M2), the same order those jobs'
    own `seen[label] = {...}; persist()` comment already documents. Never
    wired = the pre-#161-review behaviour (persisted only at `run_once`'s
    trailing `save_state`, like before this fix).

    Structurally different from `_goal_stall_nudge`'s "continue" nudge on
    purpose: typing bare "continue" here would make the SAME model, still
    holding the SAME missing answer, regenerate the SAME question — the
    exact camera-box chat wall (2026-07-05) this file already refuses to
    resurrect. `GOAL_QUESTION_PARK_TEXT` is a STRUCTURALLY DIFFERENT
    instruction (park this ticket, switch to another) — never the question
    itself, so it can never be mistaken for a re-print of it.

    Every refusal here is deliberate, never an oversight:
      * not a genuine, TRAILING `❓ NEEDS YOU:` status line at all
        (`_needs_you_block_ts`) — a message that merely MENTIONS those
        words, or an `❓ ASKED` turn (which already ends `⏳ WORKING`), is
        never this branch;
      * no CONFIRMED delivery on record for this session
        (`_goal_question_delivered_ts` returns `None`) — never guess a
        start time;
      * the newest delivered entry for this session is not CLOSE to the
        transcript's own block timestamp (`GOAL_QUESTION_MATCH_SLOP_S` /
        `_WINDOW_S`) — a stale sibling question must never stand in for
        this one (#161-review M1 scenario B);
      * under `GOAL_QUESTION_TIMEOUT_S` since delivery — too soon;
      * THIS exact outstanding question (keyed on its own delivered `ts`,
        `rec['qparked_ts']`) was already nudged once — bounded to ONE
        attempt per episode; a NEW question later (a different `ts`) gets
        its own fresh one;
      * a background worker is in flight, the pane is compacting, or a
        `/compact` claim is outstanding for this sid — the same battery
        `_goal_stall_nudge` already applies;
      * the pane is not genuinely idle at a bare input prompt (busy, a
        foreign draft, scrolled) — never type over any of those."""
    logs = []
    marker_line, entry_ts = _needs_you_block_ts(tpath)
    if marker_line is None:
        return logs                        # not a genuine trailing NEEDS YOU block
    delivered = _goal_question_delivered_ts(sid, questions_path, logs=logs)
    if delivered is None:
        return logs                        # never confirmed delivered — never guess
    if not (entry_ts - GOAL_QUESTION_MATCH_SLOP_S
            <= delivered <= entry_ts + GOAL_QUESTION_MATCH_WINDOW_S):
        return logs                        # newest entry isn't about THIS block
    waited = now - delivered
    if waited < GOAL_QUESTION_TIMEOUT_S:
        return logs
    if rec.get("qparked_ts") == delivered:
        return logs                        # this exact question already parked once
    if handled is not None and sid in handled:
        return logs
    if _pane_has_bg_agent(captured) or _pane_compacting(captured):
        return logs
    if compact_claim_active(sid, cwd, projects_dir=projects_dir):
        return logs
    kind, draft = _classify_boundary(captured)
    if kind != "input" or draft:
        if rec.get("qskip_logged") != delivered:
            rec["qskip_logged"] = delivered
            logs.append("skip %s (goal-question-timeout) %s"
                        % (draft and "draft" or kind, loc))
        return logs
    if not pane_at_idle_prompt(captured):
        return logs
    if dry_run:
        logs.append("READY (goal-question-timeout) %s waited=%dm"
                    % (loc, waited // 60))
        return logs
    rec["qparked_ts"] = delivered
    if persist is not None:
        persist()
    send_continue(pid, GOAL_QUESTION_PARK_TEXT, run)
    logs.append("goal-question-timeout %s waited=%dm -> parked, moving to "
                "other tickets" % (loc, waited // 60))
    return logs


def goal_templates_path():
    """`~/.claude/skills/autopilot/SKILL.md` — the INSTALLED autopilot skill,
    resolved at CALL time (never a frozen module-level constant).

    Deliberately the installed copy, never a repo checkout: the isolated
    sub-dev users (marek / david / montalu) have no `~/devel/airuleset`, and
    the installed skill is by definition the text their `/autopilot` actually
    prints — so it is the only source that can never disagree with what a
    session was armed from."""
    return Path.home() / ".claude" / "skills" / "autopilot" / "SKILL.md"


def goal_template_norm(text):
    """The comparable form of a `/goal` line — see the GOAL_DRIFT section.

    Drops parenthetical citations, backtick-quoted spans and punctuation, then
    lowercases and collapses whitespace. What survives is the SUBSTANCE of the
    stop conditions, which is what makes two prints of the same template equal
    while keeping genuinely different templates (and genuinely changed ones)
    apart."""
    s = re.sub(r"\([^()]*\)", "", text or "")
    s = re.sub(r"`[^`]*`", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.lower().split())


def goal_template_hash(text):
    """Stable identity of a `/goal` line (hash of its normalized form)."""
    return _hash(goal_template_norm(text))


def load_goal_templates(path):
    """The `/goal …` lines shipped in the autopilot SKILL, in file order.

    Read out of the SKILL prose itself — never a side-car data file somebody
    has to remember to regenerate when the template changes. Missing or
    unreadable file returns `[]`, which switches the whole drift check off
    rather than guessing."""
    if not path:
        return []
    try:
        with open(str(path), encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return []
    return [ln.strip() for ln in _GOAL_TEMPLATE_RX.findall(body) if ln.strip()]


def goal_template_variant(line, templates):
    """Index of the template `line` IS (exact match on the normalized form),
    else None. Never a nearest-neighbour — 'close to a template' is precisely
    the ambiguous case that must do nothing."""
    if not line or not templates:
        return None
    h = goal_template_hash(line)
    for i, t in enumerate(templates):
        if goal_template_hash(t) == h:
            return i
    return None


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
    with the `<local-command-stdout>` tag, or with CC's own arm instruction)
    — a compaction SUMMARY narrating "the loop's Goal set: …" in prose, or any
    message merely QUOTING the arm sentence, is not state."""
    s = (content or "").strip()
    if s.startswith(GOAL_ARM_ACTIVE_PREFIX):
        # The record CC writes for EVERY arm — and the ONLY one it writes when
        # the `/goal` was typed into a BUSY pane and drained from the
        # type-ahead queue (that path logs a `queue-operation` entry instead of
        # a `local-command-stdout` marker; live-captured on CC 2.1.220, #64).
        # Without this shape a re-armed session keeps reporting its OLD payload
        # forever, so the drift check would re-arm it every single sweep.
        rest = s[len(GOAL_ARM_ACTIVE_PREFIX):]
        # The payload itself contains quotes (`--search "-label:autopilot-skip"`
        # is in every shipped template), so the terminator is the LAST `". `,
        # never the first quote. CC's trailing instruction sentence carries no
        # quote of its own, which is what makes this unambiguous.
        cut = rest.rfind('". ')
        if cut < 0:
            cut = rest.rfind('"')
        if cut <= 0:
            return None
        return {"state": "set", "payload": rest[:cut].strip() or None}
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


# Cheap raw-bytes pre-filter for "this line might be the session asking to be
# armed". Both alternatives of `_ARM_QUESTION_RX` require the literal `/goal`,
# and CC does not escape `/` in its JSONL, so this is a provable superset.
_GOAL_ASK_PROBE = b"/goal"


def _entry_asks_to_arm(entry):
    """True when this entry is an ASSISTANT turn printing the arm question.

    TOP-LEVEL text blocks only. The same words arrive as a `tool_result` when
    one session greps another's transcript — that content is always a LIST
    inside a `user` entry, so the structural filter that keeps a quoted `Goal
    set:` from being read as state (#54) applies here unchanged: somebody
    else's question is not this session asking for anything.
    """
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return False
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return False
    blocks = msg.get("content")
    if isinstance(blocks, str):
        return bool(_ARM_QUESTION_RX.search(blocks))
    if not isinstance(blocks, list):
        return False
    for b in blocks:
        if (isinstance(b, dict) and b.get("type") == "text"
                and isinstance(b.get("text"), str)
                and _ARM_QUESTION_RX.search(b["text"])):
            return True
    return False


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
        # cheap pre-filter over the raw bytes — either of the TWO shapes CC
        # writes (the `/goal` command's own stdout, or the arm instruction it
        # injects, which is the only record a QUEUED arm leaves at all, #64).
        # The arm probe deliberately stops BEFORE the prefix's opening quote:
        # in the JSON line that quote is escaped as `\"`, so matching the full
        # prefix here would never hit.
        is_mark = (_GOAL_LCS_OPEN.encode() in ln or _GOAL_ARM_PROBE in ln)
        # Only once a marker is in hand does a later arm question mean
        # anything — before that there is nothing for it to qualify.
        is_ask = best is not None and _GOAL_ASK_PROBE in ln
        if not is_mark and not is_ask:
            continue
        try:
            entry = json.loads(ln)
        except Exception:
            continue
        if is_mark:
            mark = _parse_goal_marker(_goal_marker_content(entry))
            if mark is not None:
                ts = None
                try:
                    ts = datetime.fromisoformat(
                        str(entry.get("timestamp")).replace(
                            "Z", "+00:00")).timestamp()
                except Exception:
                    ts = None
                mark["ts"] = ts
                # A fresh marker supersedes whatever asked before it.
                mark["arm_after"] = False
                best = mark
                continue
        if best is not None and _entry_asks_to_arm(entry):
            best["arm_after"] = True
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


GOAL_TYPE_SETTLE_POLLS = 8          # bounded: CC needs a moment to INGEST a
GOAL_TYPE_SETTLE_S = 1              # multi-KB paste before it renders it


def _await_typed(pid, text, run, sleep_fn, want=True):
    """Poll (bounded) until the pane's input box shows evidence of `text`
    (`want=True`) or has stopped showing it (`want=False`), returning the
    final verdict. A 2859-char payload live-observed 2026-07-26 really did
    land but was NOT yet rendered when the capture taken immediately after
    `send-keys -l` was read — the delivery was declared failed and the goal
    never re-armed. This is a render-settle poll, not a blind timeout: it
    returns the instant the box agrees, and a type that never appears is
    still refused (`no-timeout-band-aids.md`)."""
    for i in range(GOAL_TYPE_SETTLE_POLLS):
        landed = _typed_landed(text, _input_line_text(
            capture_pane(pid, run, lines=40)))
        if landed is want:
            return landed
        if i < GOAL_TYPE_SETTLE_POLLS - 1:
            sleep_fn(GOAL_TYPE_SETTLE_S)
    return not want


def _send_goal_verified(pid, text, run, captured=None, sleep_fn=None, logs=None):
    """Type a LONG `/goal …` into a BARE input box and submit it, verifying
    every step against a fresh capture — the same protocol
    `deliver_with_stash` uses for its own type/submit steps (steps 5-7), minus
    the stash (there is no draft here).

    NEVER presses Enter after a type-verify failure: submitting a truncated
    goal is the exact #36 disaster this job exists to avoid. NEVER sends two
    consecutive Escapes (that permanently deletes a draft, #35). Returns True
    only when the box is provably empty again after the submit.

    #271 (adversarial-review MAJOR finding): persisting `cap` — the SAME
    capture object the caller already verified bare — can never observe a
    draft, because every real caller already gates entry on that exact
    object reading bare; a rescue call there is provably a no-op in
    production. The race this primitive actually needs to guard is a draft
    appearing AFTER the caller's own check but BEFORE this function's real
    type keystroke, several tmux round-trips later — so a SECOND, FRESH
    capture is taken immediately before typing (job 20's own
    re-capture-right-before-send pattern, #176-F3) and THAT is what gets
    persisted-and-refused-on, not the stale one. A genuinely bare box still
    costs nothing (persist is a no-op on empty content); only a real race
    ever writes a file."""
    run = run or _default_run
    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    sleep_fn = sleep_fn or time.sleep
    cap = captured if captured is not None else capture_pane(pid, run, lines=40)
    if _input_line_text(cap) != "":
        _draft_rescue_persist(pid, cap, logs=logs)
        # #322 — zero keystrokes sent yet; the caller's own #101 carve-out
        # (`dlogs[-1] in _GOAL_REARM_TRANSIENT_REASONS`) needs a reason to
        # recognise, or this pre-send refusal is silently counted as a real
        # attempt.
        _log("goal-verify-abort: not-bare")
        return False                       # not a bare box — caller's problem
    if _strip_selected(cap):
        run(["tmux", "send-keys", "-t", pid, "Escape"])
    fresh = capture_pane(pid, run, lines=40)
    if _input_line_text(fresh) != "":
        _draft_rescue_persist(pid, fresh, logs=logs)
        # #322 — the LIVE incident shape: the caller's own outer capture was
        # idle, but this fresh, live re-read (taken immediately before
        # typing, #176-F3) shows the pane already busy — a real race, never a
        # delivery failure. Zero keystrokes sent; must not consume the cap.
        _log("goal-verify-abort: raced-busy")
        return False                       # raced — a draft appeared since the caller's own check
    _type_literal(pid, run, text, sleep_fn)
    if not _await_typed(pid, text, run, sleep_fn, want=True):
        # #322 — a clearer reason when the poll never confirmed landing
        # because CC collapsed the burst into its "paste again to expand"
        # hint (never parsed as a slash command) rather than a generic
        # render timeout. `_type_literal`'s chunking exists to avoid ever
        # reaching this state; checked HERE (after the poll already gave
        # up, not before it) so a successful type pays no extra capture.
        # Real keystrokes went out (this is NOT a pre-send refusal), so
        # this reason is deliberately NOT in `_GOAL_REARM_TRANSIENT_REASONS`.
        if _pane_shows_collapsed_paste(
                _input_line_text(capture_pane(pid, run, lines=40))):
            _log("goal-verify-abort: collapsed-paste")
        return False                       # never rendered — never submit it
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    if _await_typed(pid, text, run, sleep_fn, want=False):
        # STILL in the box after the same bounded settle window — a genuinely
        # swallowed submit (the #36 agent-strip class). ONE corrective
        # Escape+Enter, never a second bare Enter, never two Escapes. The
        # poll matters: checking immediately read a WORKING submit as a
        # swallowed one and fired this Escape into a turn that had just
        # started (live, 2026-07-26).
        run(["tmux", "send-keys", "-t", pid, "Escape"])
        run(["tmux", "send-keys", "-t", pid, "Enter"])
        if _await_typed(pid, text, run, sleep_fn, want=False):
            # #306 — the sibling gap `deliver_with_stash` had: this box was
            # verified bare before we ever typed, so its ENTIRE content now
            # is, by construction, exactly `text` — recover it (there is no
            # draft to protect here, `parked` is always False on this
            # bare-box-only primitive) rather than leaving our own typed
            # `/goal …` glued in the box unrecovered.
            _undo_and_release_slot(pid, run, text, False, _log,
                                   "goal-verify-abort: "
                                   "swallowed-submit-not-recovered")
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
                from notify import stream_redirect     # #212: STREAM_NOTIFY_OWNER-aware
                send_fn("⚠️ **%s** — `/goal` je armovaný (`◎ /goal` svieti), ale "
                        "slučka sa už %d minút nepohla a ani po %d šťuchnutiach "
                        "sa nerozbehla (%s). Pozri sa na ňu prosím — dovtedy "
                        "nič nepokračuje."
                        % (project_label(cwd), int(idle // 60),
                           GOAL_STALL_MAX_NUDGES, loc),
                        owner=stream_redirect(pane_owner(pid, run)) or None,
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


def _goal_template_drift(now, run, rec, sid, cwd, pid, captured, loc, templates,
                         send_fn, dry_run, handled, projects_dir, sleep_fn,
                         tmtime=None):
    """The STALE-TEMPLATE branch of job 20 (#64) — see the `GOAL_DRIFT_*`
    section comment. Runs only for a pane whose goal is provably ARMED.
    Mutates `rec` (the caller persists it); returns log lines.

    Four states, and the second and third are entered by OBSERVATION of the
    PAYLOAD itself, never by resemblance to a template or by the mere fact
    that `tvar` happens to be set (#186):
      * the payload matches a CURRENT template  -> record the variant AND
        the hash of that exact text (`armed_hash` — the only positive proof
        this session's live text IS the template, at THIS instant), clear
        any drift bookkeeping, do nothing. This is also the ONLY place a
        session becomes eligible for a future re-arm at all.
      * it does not match any CURRENT template, `tvar` is set, AND the
        payload's own hash is UNCHANGED since `armed_hash` was last
        recorded -> the armed text has not moved; the only thing that could
        have changed is the shipped template itself -> genuine drift under a
        live loop, re-arm with that variant's current text.
      * it does not match any CURRENT template, but the payload IS exactly
        what WE ourselves last delivered (`dq` outstanding, its hash equals
        `dhash`) -> CC echoing our own re-arm landing, even though the
        template moved again before we could confirm it. Adopt it as the
        new confirmed baseline and re-evaluate against whatever is CURRENT
        now (#186-review finding S3) — never mistaken for a hand-edit.
      * it does not match, `tvar` is set, but the payload's hash has CHANGED
        since `armed_hash` (or `armed_hash` was never recorded at all —
        state persisted before this fix ever ran, provably NOT the same as
        an observed edit) -> the armed text itself moved since we last
        positively confirmed it was the template. There is no way to tell
        "the user hand-edited it" (#186's own live incident — the machinery
        must never fight a deliberate override, same family as never
        auto-downtiering the user's own `/model` choice) apart from a
        multi-step drift this job never observed directly — and per this
        job's own governing rule ("proof comes from observation, never
        resemblance"), an unconfirmed guess is treated exactly like a goal
        that never matched at all: `tvar` is downgraded to untracked, so the
        SAME once-per-episode `untracked` handling below applies. This
        downgrade is the one state mutation in this function gated on
        `not dry_run` — unlike a passive refresh, it is a one-way door.
    Anything else (a goal that never matched anything, or no longer counts
    as having matched one) is the user's own and is left alone, with the
    reason logged ONCE — never every 60 s (a genuinely LEGACY downgrade,
    `armed_hash` never recorded, additionally gets a one-shot Discord ping,
    since this branch now short-circuits before ever reaching the pre-fix
    GAVE UP ping such a session used to get; an OBSERVED hand-edit stays
    deliberately quiet)."""
    logs = []
    payload = rec.get("payload") or ""
    if not payload or "\n" in payload or len(payload) > GOAL_REARM_MAX_PAYLOAD:
        return logs
    line = "/goal " + payload
    cur = goal_template_variant(line, templates)
    if cur is not None:
        # up to date — and this is also the ONLY place a session becomes
        # eligible for a future re-arm at all
        if rec.get("tvar") != cur or rec.get("dhash") or rec.get("dq"):
            rec.update({"tvar": cur, "dhash": None, "dn": 0, "dpinged": False,
                        "dq": None})
        rec["untracked_logged"] = False
        rec["armed_hash"] = goal_template_hash(line)
        return logs
    tvar = rec.get("tvar")
    if tvar is None or tvar >= len(templates):
        # never seen matching a template => a goal the user wrote. Logged the
        # first time only: a custom-goal session is otherwise a permanent
        # resident and would print this line every sweep, forever.
        if not rec.get("untracked_logged"):
            rec["untracked_logged"] = True
            logs.append("skip untracked (goal-drift) %s -> not the autopilot "
                        "template, leaving it alone" % loc)
        return logs
    payload_hash = goal_template_hash(line)
    if rec.get("dq") and rec.get("dhash") == payload_hash:
        # #186-review finding (S3) — the payload NOW equals what WE
        # ourselves last typed (`dhash`, the hash of the delivery target),
        # with a delivery genuinely still outstanding (`dq`): this is CC
        # echoing OUR OWN re-arm landing, never a hand-edit, even though the
        # shipped template may have moved AGAIN before this confirming
        # sweep ran (so it no longer matches any CURRENT template either).
        # Adopt it as the new confirmed baseline and fall through into the
        # ordinary drift machinery below, which re-evaluates against
        # whatever is current NOW — never gated on `dry_run` for the same
        # reason the `cur is not None` branch above isn't: this is a
        # passive, idempotent observation refresh, not a destructive one.
        rec["armed_hash"] = payload_hash
        rec["dq"] = None
        logs.append("CONFIRMED (goal-drift) %s -> re-armed text landed" % loc)
    elif rec.get("armed_hash") != payload_hash:
        # #186 — the armed text itself changed since the last CONFIRMED
        # match (or was never confirmed at all under this fix: `armed_hash`
        # ABSENT, provably a session whose bookkeeping predates this fix —
        # never the same thing as an OBSERVED hand-edit, which leaves
        # `armed_hash` present but different). Either way, treated as a
        # hand-edit: downgrade to untracked so this — and every future —
        # sweep funnels through the SAME safe, once-logged "leave it alone"
        # path above, never a guessed revert. Drift bookkeeping in flight
        # for the OLD (now-stale) assumption is cleared too, so a later
        # genuine re-match starts clean.
        #
        # #186-review finding 1 — a LEGACY record (armed_hash absent) is
        # unmeasurable either way, and downgrading it silently would remove
        # the ONLY signal a genuinely-stuck legacy drift loop used to get
        # (the pre-existing GAVE UP ping after GOAL_DRIFT_MAX_ATTEMPTS,
        # never reached now that this branch exits before ever attempting
        # delivery). One-shot, loud, never silent — distinct from a
        # genuinely OBSERVED hand-edit, which stays deliberately quiet
        # (never pester the user for their own deliberate edit).
        #
        # #186-review finding — none of this mutates `rec` under
        # `dry_run`: unlike the passive refresh above, downgrading `tvar`
        # is a ONE-WAY door (the only way back is an exact CURRENT-template
        # match) and `untracked_logged` is a genuine one-shot flag, exactly
        # the `dark_pinged` class `#238-review 🟡F4` already fixed once in
        # this same file — a manual `--dry-run` diagnostic sweep must never
        # permanently disable healing for a real session.
        legacy = rec.get("armed_hash") is None
        if dry_run:
            logs.append("READY (goal-drift) %s -> %s, would leave it alone"
                        % (loc, "legacy/unconfirmed state" if legacy
                           else "hand-edited text"))
            return logs
        rec["tvar"] = None
        rec.update({"dhash": None, "dn": 0, "dpinged": False, "dq": None})
        if not rec.get("untracked_logged"):
            rec["untracked_logged"] = True
            if legacy and not rec.get("legacy_pinged"):
                rec["legacy_pinged"] = True
                if send_fn is not None:
                    from notify import stream_redirect  # #212
                    send_fn(
                        "⚠️ **%s** — `/goal` slučka má armovaný text, ktorý sa "
                        "nezhoduje so žiadnou aktuálnou šablónou, a watchdog si "
                        "nevie byť istý, či ide o tvoju vlastnú úpravu, alebo o "
                        "stav spred opravy #186 — preto ho NEPREPÍŠE, len ho "
                        "prestane sledovať (%s). Skontroluj si ho prosím ručne; "
                        "ak má opäť bežať aktuálna šablóna, spusti `/autopilot` "
                        "znova." % (project_label(cwd), loc),
                        owner=stream_redirect(pane_owner(pid, run)) or None,
                        dedup_key="goallegacy:%s" % sid,
                        dry_run=dry_run)
            logs.append(
                "skip hand-edited (goal-drift) %s -> %s" % (
                    loc,
                    "legacy state, unconfirmed either way -- pinged once"
                    if legacy else
                    "armed text no longer matches the last confirmed "
                    "template, leaving the user's edit alone"))
        return logs
    target = templates[tvar]
    th = goal_template_hash(target)
    if rec.get("dhash") != th:
        rec.update({"dhash": th, "dn": 0, "dpinged": False, "dq": None})
    # A delivery already in flight is not a failed one. CC records the new arm
    # only once the session actually processes the command, so for a short
    # window the marker scan legitimately still reports the OLD payload —
    # live-observed re-delivering a re-arm that had in fact landed
    # byte-identically. Wait it out before spending another attempt.
    q = rec.get("dq")
    if q and (now - q) < GOAL_REARM_CONFIRM_S:
        return logs
    if q:
        rec["dq"] = None
        logs.append("LOST (goal-drift) %s -> typed template never took" % loc)
    if rec.get("dn", 0) >= GOAL_DRIFT_MAX_ATTEMPTS:
        if not rec.get("dpinged"):
            rec["dpinged"] = True
            if send_fn is not None and not dry_run:
                from notify import stream_redirect     # #212: STREAM_NOTIFY_OWNER-aware
                send_fn("⚠️ **%s** — beží `/goal` slučka so STAROU verziou "
                        "autopilot šablóny a automatické preármovanie sa "
                        "nechytilo ani po %d pokusoch (%s). Preármuj ju prosím "
                        "ručne — dovtedy sa riadi starými podmienkami."
                        % (project_label(cwd), GOAL_DRIFT_MAX_ATTEMPTS, loc),
                        owner=stream_redirect(pane_owner(pid, run)) or None,
                        dedup_key="goaldrift:%s:%s" % (sid, th),
                        dry_run=dry_run)
            logs.append("GAVE UP (goal-drift) %s after %d attempts"
                        % (loc, GOAL_DRIFT_MAX_ATTEMPTS))
        return logs
    # --- the same refusals the #76 branch uses, for the same reasons --------
    if handled is not None and sid in handled:
        logs.append("skip just-compacted (goal-drift) %s" % loc)
        return logs
    if compact_claim_active(sid, cwd, projects_dir=projects_dir):
        logs.append("skip compact-claim (goal-drift) %s" % loc)
        return logs
    if _pane_compacting(captured) or pane_waiting_on_user(captured):
        logs.append("skip busy-state (goal-drift) %s" % loc)
        return logs
    kind, draft = _classify_boundary(captured)
    if kind != "input":
        # A LIVE loop is busy much of the time, so this is a common outcome
        # and the delivery simply waits for a real prompt. Queuing a 3 KB
        # paste into a running turn is refused on purpose: #84 showed a queued
        # command can sit undrained for over an hour under an armed goal,
        # which would leave us unable to tell "not executed yet" from "lost"
        # and re-typing it every sweep — the #78 duplicate class.
        logs.append("skip %s (goal-drift) %s" % (kind, loc))
        return logs
    # A pane can LOOK like a free prompt for a moment while the loop is merely
    # between tool calls. Typing into such a moment is what left a 3 KB
    # payload unsubmitted in a live box (see GOAL_DRIFT_MIN_QUIET_S), so the
    # transcript's own quiet window has to agree that the loop is paused.
    quiet = now - (tmtime or now)
    if quiet < GOAL_DRIFT_MIN_QUIET_S:
        logs.append("skip not-quiet (goal-drift) %s (%ds)" % (loc, int(quiet)))
        return logs
    if dry_run:
        logs.append("READY (goal-drift) %s -> %d chars" % (loc, len(target)))
        return logs
    # …and re-verify against a FRESH capture: by now the sweep's own capture is
    # several tmux round-trips old, which is exactly the width of the race.
    fresh = capture_pane(pid, run, lines=40)
    kind, draft = _classify_boundary(fresh)
    if kind != "input" or pane_goal_armed(fresh) is not True:
        logs.append("skip raced (goal-drift) %s -> pane moved since the sweep"
                    % loc)
        return logs
    if draft:
        dlogs = []
        ok = deliver_with_stash(pid, target, run, captured=fresh, logs=dlogs)
        tag = "goal-drift, stash"
    else:
        if not pane_at_idle_prompt(fresh):
            logs.append("skip not-idle (goal-drift) %s" % loc)
            return logs
        dlogs = []
        ok = _send_goal_verified(pid, target, run, captured=fresh,
                                 sleep_fn=sleep_fn, logs=dlogs)
        tag = "goal-drift"
    rec["dn"] = rec.get("dn", 0) + 1
    if ok:
        rec["dq"] = now
        logs.append("OK (%s) %s -> /goal updated to the current template "
                    "(%d chars), variant %d" % (tag, loc, len(target), tvar))
    else:
        logs.append("FAIL (%s) %s -> delivery not verified%s"
                    % (tag, loc, (" (%s)" % dlogs[-1]) if dlogs else ""))
    return logs


def _goal_dark_died_by_outage(tpath, last_armed=None):
    """True when job 20's dark-goal check (#173) should treat a goal that
    has been dark past `GOAL_REARM_MAX_DARK_S` as a technical OUTAGE (a
    crash, a binary update, an API error during the dark stretch — or the
    watchdog itself being unable to sweep for hours, #172) rather than a
    deliberate `/goal clear` (#170) — i.e. safe to re-arm PAST the cap
    instead of the permanent stale-goal skip.

    Decided (user, 2026-08-06, #173 comment): re-read the transcript for
    the NEWEST `/goal` marker. No `Goal cleared:` after the last `Goal
    set:` -> the goal died by outage, not by the user's own hand -> keep
    re-arming past the cap. A `Goal cleared:` IS present -> #170's
    invariant stands untouched -> leave it dark forever.

    Reuses `scan_goal_markers` -- the SAME transcript mechanism #266 wired
    for job 9's own clear-detection (`_goal_was_cleared_by_user`) -- never
    a second marker reader (FREEZE, no new hook/job). Re-reads the
    transcript FRESH, independent of job 20's own incremental
    `rec['mark']` bookkeeping (which reaching this branch at all already
    implies is "set" -- this is a defensive, independent re-derivation
    made at the exact instant the risky decision is taken, never a trust
    of state that may have gone stale).

    `off=0` (the WHOLE file), never a bare `scan_goal_markers(tpath)`
    (which bootstraps from a `GOAL_MARK_TAIL_BYTES`, 4 MB, TAIL) —
    adversarial review of this exact ticket (fresh-context, 2026-08-06)
    PROVED the tail-bootstrap form regresses on precisely the population
    #173 exists to revive: a long-lived, busy loop that keeps writing
    (and so keeps pushing its own `Goal set:` marker further from EOF)
    for hours during an outage before dying. Live-reproduced against the
    shipped tail-bootstrap code: a transcript with one `set` marker
    followed by >4 MB of filler and NO clear read back `mark is None`
    (nothing found in the tail window) -> `False` -> the OPPOSITE of the
    decided semantics (permanent skip+ping despite no clear ever having
    happened). `off=0` closes this by construction — it is the exact
    scenario `GOAL_MARK_TAIL_BYTES`'s own docstring says the incremental,
    stored-offset read exists to avoid re-paying for on every sweep
    (montalu's own 240 MB single-session transcript); THIS call pays that
    cost deliberately, because it fires only for the narrow, already-rare
    population that is BOTH dark past the cap AND unconfirmed (never for
    a healthy sweep), and correctness here outweighs the incremental
    read's savings.

    Fail-safe direction is the OPPOSITE of `_goal_was_cleared_by_user`'s:
    that function arms a FRESH session, so failing open (an unreadable
    transcript keeps arming) is the safe direction there. This function
    revives a goal that has been genuinely DARK for
    `GOAL_REARM_MAX_DARK_S` (>=6h) with a payload that may be stale, into
    a pane whose current use is unknown -- so an unreadable, absent, or
    markerless transcript must stay CONSERVATIVE here: False (not an
    outage), leaving the existing skip+ping in place. Multiple set/clear
    cycles: `scan_goal_markers` already returns only the NEWEST marker in
    the bytes it reads, so only the LAST set/clear pair is ever consulted
    -- exactly the ticket's own edge case.

    `last_armed` (#321, optional -- every existing caller passes only
    `tpath`, unaffected): THIS job's own REALITY observation
    (`rec['last_armed']`), the SAME signal `_goal_cleared_stale` already
    uses to prove a genuine arm happened AFTER the transcript's newest
    tracked 'cleared' marker even though CC wrote no marker for it (#320's
    own dev1 forensics). Without it, this function's OWN fresh re-read can
    independently re-discover that exact stale 'cleared' marker and
    disbelieve the caller's own #320 determination -- two mechanisms in the
    SAME sweep contradicting each other (#321, live: dev1 2d02a127's `mark`
    correctly flipped 'cleared' -> 'set' via #320, but this function still
    found the transcript's newest marker was 'cleared' and returned False,
    permanently re-skipping the very session #320 had just revived, every
    sweep, forever). A 'cleared' marker whose own `ts` PREDATES
    `last_armed` is stale by the IDENTICAL rule `_goal_cleared_stale`
    already applies -- treated as no real clear (True, outage-like), never
    re-derived independently only to contradict a decision this exact
    sweep already made. Unmeasurable (either `last_armed` or the marker's
    own `ts` absent) never guesses -- a genuine `set` verdict, or a
    'cleared' marker with no comparable timestamp, is unaffected either
    way.

    #335 -- an explicit `/exit` (the user's OWN stated way of stopping a
    session) postdating the anchor (`last_armed` when confirmed, else the
    marker's own `ts`) makes this False too, exactly like a `Goal cleared:`
    marker: Claude Code writes no goal-marker for `/exit` at all, so
    without this check a deliberate exit was completely invisible here and
    a `set`-only transcript re-armed it past the cap regardless — live on
    simap@subdev, the ACTUAL incident this ticket was filed over never even
    involved an `/exit` (the loop simply finished naturally and sat
    abandoned — see `goal_rearm`'s own `revived_from_dark_outage` gate for
    THAT half), but a future session that DOES exit deserves the identical
    protection `/goal clear` already has. `off=0` (the SAME full-file read
    this function already does for its own marker scan) for the identical
    reason: a long-lived loop can push even its OWN final `/exit` far past
    a tail-bootstrap window before this rare, already-expensive check
    ever runs."""
    try:
        _off, mark = scan_goal_markers(tpath, off=0)
    except Exception:
        return False
    if not mark:
        return False
    exit_ts = _goal_user_exit_ts(tpath, off=0)
    if exit_ts is not None:
        anchor = last_armed if last_armed is not None else mark.get("ts")
        if anchor is None or exit_ts > anchor:
            return False
    if mark.get("state") == "set":
        return True
    if (last_armed is not None and mark.get("ts") is not None
            and last_armed > mark.get("ts")):
        return True
    return False


GOAL_REARM_PROGRESS_WINDOW_S = 6 * 3600   # mirrors statusbar.py's own
                                            # AUTOPILOT_RUN_WINDOW_S -- a
                                            # progress-file heartbeat this
                                            # fresh means an ACTIVE run, not
                                            # week-old leftover state

GOAL_REARM_UNTRACKED_PING_S = 30 * 60  # #324 -- how long a session may sit
                                        # in the untracked-no-evidence state
                                        # (footer dark, `rec['mark']` never
                                        # resolved, no live autopilot-
                                        # progress heartbeat, no job-9
                                        # goalarm record for this pane
                                        # either) before this job stops
                                        # merely LOGGING it once and instead
                                        # tells a human -- montalu2@subdev's
                                        # own #324 incident was already past
                                        # this (30 real minutes, per #321's
                                        # own evidence) by the time it was
                                        # filed, so this is not a
                                        # speculative bound.

GOAL_ARMED_ACTIVITY_GRACE_S = 5 * 60   # #312-hardening (live pane evidence,
                                        # 2026-08-08): CC's own `◎ /goal`
                                        # footer glyph can be TRUNCATED off
                                        # by a wide custom statusline row --
                                        # live-confirmed on zbynek-4:2.0
                                        # (width 176, the custom statusline
                                        # consumes the full row and CC's own
                                        # right-edge indicator area is cut
                                        # off) -- so a genuinely-armed goal
                                        # can read `pane_goal_armed() ==
                                        # False` with NO way to tell that
                                        # apart from a real dead goal by the
                                        # footer alone. A transcript still
                                        # being WRITTEN TO this recently is
                                        # independent, strong evidence real
                                        # work is still happening -- refuse
                                        # to recover/re-arm on the strength
                                        # of a missing glyph alone; defer to
                                        # a later sweep instead of risking a
                                        # duplicate `/goal` paste into a
                                        # session that never actually died.


def _live_autopilot_progress(cwd, progress_dir, now):
    """True when `<progress_dir>/<repo>.json` (repo resolved from `cwd`'s
    own git remote via `notify.repo_name_for(cwd)` -- NEVER the directory
    basename, the marek/parovanie-produktov trap that module's own
    docstring documents; `progress_dir` defaults to the REAL
    `~/.claude/autopilot-progress` when not given, same "wired = on
    optional test seam" convention as every other injected path/fetch in
    this file) shows a genuinely ACTIVE, INCOMPLETE batch run: `done > 0`
    (real progress already happened) AND `remaining > 0` (real work is
    still outstanding) AND its `ts` heartbeat is fresher than
    `GOAL_REARM_PROGRESS_WINDOW_S` (a stale file from a long-finished run
    must never look like live evidence). Any failure -- an unresolvable
    repo, a missing/malformed/non-dict file -- is UNMEASURABLE -> False,
    never a guess -- #312's own recovery path only ever ACTS on a True
    here, so the safe default is the one that never revives anything."""
    try:
        import notify
        repo = notify.repo_name_for(cwd)
    except Exception:
        repo = ""
    if not repo:
        return False
    d = Path(progress_dir) if progress_dir else (
        Path.home() / ".claude" / "autopilot-progress")
    try:
        data = json.loads((d / ("%s.json" % repo)).read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    try:
        done = int(data.get("done") or 0)
        remaining = int(data.get("remaining") or 0)
        ts = float(data.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    if done <= 0 or remaining <= 0:
        return False
    return (now - ts) < GOAL_REARM_PROGRESS_WINDOW_S


def _goal_recover_untracked(now, rec, sid, cwd, tpath, tmtime, loc,
                            progress_dir=None, state=None, pid=None,
                            run=None, send_fn=None, dry_run=False):
    """#312 -- `rec['mark']` used to collapse TWO structurally different
    states into one silent-forever `continue` at `goal_rearm`'s own
    `if rec.get("mark") != "set":` gate: an EXPLICIT `Goal cleared:` THIS
    job itself scanned (`mark == "cleared"` -- the #170 class, the user's
    own deliberate choice, MUST stay untouchable forever) versus this job
    NEVER HAVING TRACKED THE SESSION'S GOAL HISTORY AT ALL (`mark is
    None` -- e.g. a hand-adapted, non-fleet-template goal armed before
    this sid was first swept, or any other coverage gap in an earlier
    sweep's incremental `off` bootstrap). The second case is the real
    "untouchable by construction" gap -- NOT, as first hypothesised,
    `_goal_template_drift`'s own fleet-template-hash tracking (#64), which
    only ever gates the SEPARATE stale-template re-sync sub-feature and
    never this core dead-goal path at all (confirmed by direct code
    reading + a live dry-run probe against this box's own real panes,
    2026-08-08).

    Recovery is a ONE-SHOT, cheap-gated full-file scan
    (`scan_goal_markers(tpath, off=0)`, #173's own established discipline:
    a bare-tail bootstrap can miss a marker already outside the last
    `GOAL_MARK_TAIL_BYTES`) — but the (comparatively expensive) full scan
    is ONLY ever paid for once there is independent, LIVE evidence of real
    in-flight batch work (`_live_autopilot_progress`, checked FIRST,
    cheap) — never for an ordinary interactive session whose `rec` simply
    has no `mark` key yet because it has never run `/goal` in a batch
    sense at all. `rec['recovery_scanned']` is set on a DEFINITIVE
    full-scan result (a real marker found, of either state, or none found
    at all) but NEVER on a merely "too fresh to trust right now" verdict
    (below) — so a genuinely buried-but-recent marker gets retried on a
    later sweep once it has aged, rather than being permanently excluded
    by having consumed its one shot too early.

    A recovered `"set"` marker is adopted verbatim (`rec['mark']`,
    `rec['payload']`, `rec['mts']`) and the caller falls through into the
    EXACT SAME dark-cap / outage-detection / hash-streak-attempt machinery
    every other re-arm reason already uses -- reusing #173's own
    outage-vs-stale distinction for free rather than inventing a parallel
    one. A recovered `"cleared"` marker, or no marker at all, is left
    alone (`rec['mark']` stays `None`) -- there is nothing here this job
    ever missed that is safe to act on.

    #312-hardening (supervisor, live pane evidence 2026-08-08): CC's own
    `◎ /goal` footer glyph can be TRUNCATED off by a wide custom
    statusline row -- live-confirmed on zbynek-4:2.0 (width 176, the
    custom statusline consumes the full row and CC's own right-edge
    indicator area is cut off) -- so a genuinely-armed goal can read
    `pane_goal_armed() == False` with no way to tell that apart from a
    real dead goal by the footer alone. The transcript's own marker
    STATE cannot discriminate the two either (a crash-outage and a
    truncated-but-alive session both read `mark == "set", no clear`
    identically) -- but two INDEPENDENT clocks can: the recovered
    marker's own declared arm TIMESTAMP (`mts`), and the transcript
    FILE's own mtime (`tmtime` -- adversarial-review finding F3, this
    ticket's own review, TRIGGERED live: checking `mts` ALONE never
    actually verifies "is the session still ACTIVE", only "how long ago
    did it arm" -- a session armed hours ago but still busy working
    passes `mts` staleness trivially, since `_live_autopilot_progress`
    already requires `done > 0`, i.e. real elapsed batch work, which by
    construction means the arm itself is never recent for this gated
    population. `tmtime` is what actually answers "is anything still
    being written right now"). EITHER clock reading within
    `GOAL_ARMED_ACTIVITY_GRACE_S` defers -- never acted on this sweep (no
    delivery, no mutation of `rec` beyond nothing) -- to a later sweep
    once BOTH have genuinely aged, closing the exact "duplicate /goal
    paste into a session that never actually died" risk. The staleness
    test is also explicitly bounded to `0 <=` (finding F5: a
    FUTURE-dated marker -- clock skew, a transcript synced from another
    box -- must never look "already old enough", which an unbounded `<`
    comparison against a negative delta would wrongly accept forever,
    and never a marker whose `recovery_scanned` gets silently skipped
    sweep after sweep).

    `state` (#321 shape B, optional -- every existing caller/test omits it,
    unaffected): the `_live_autopilot_progress` bail-out used to be
    TOTALLY SILENT, every sweep, forever, for any session whose `/goal` is
    not a batch `/autopilot` loop with a live progress heartbeat -- an
    ordinary, manually-armed `/goal` has none of that by design, so this
    branch fires unconditionally for it (live: montalu2@subdev pane `%0`,
    odoo-erp -- zero journal lines containing "goal" across 30 real
    minutes, for a session with a dark footer and two untracked recs).
    Logged ONCE per session (`state['goalrearm_noprogress'][sid]`, mirrors
    the `goalarm_cleared`/`goalarm_noresolve` log-once-then-quiet shape
    already used elsewhere in this file), popped the moment EITHER
    evidence source below genuinely becomes live -- the recovery DECISION
    itself is UNCHANGED (still never acts without independent evidence);
    only the silence was fixed by #321.

    #324 -- `_live_autopilot_progress` used to be the ONLY evidence this
    function would ever pay the (comparatively expensive) full-file scan
    for, which permanently excludes a manually-armed `/goal` loop (no
    batch `/autopilot` progress heartbeat exists for that shape of
    session at all, by design) -- exactly montalu2@subdev's own live
    incident. `state['goalarm']` (job 9's own per-pane dedup record,
    `ga[pid] = int(now)`, set on every REAL -- non-transient-skip -- arm
    attempt) is a second, independent, CHEAP (plain dict lookup, no
    filesystem I/O) piece of evidence that a `/goal` really was sent to
    THIS pane at some point -- the "goalarm record of a genuinely-sent
    arm" alternative the ticket names. Either evidence source unlocks the
    one-shot scan below; the existing `rec['recovery_scanned']` one-shot
    bound already caps its cost regardless of which one triggered it.
    (The ticket's OTHER named alternative, "footer `◎ /goal` read from the
    pane", needs no code here at all -- `goal_rearm`'s own per-session
    loop already reads `pane_goal_armed(captured)` BEFORE this function is
    ever reached, and a lit footer short-circuits straight into the armed
    branch below, never landing here.)

    If NEITHER evidence source ever resolves, this must not end in silent
    waiting either (the ticket's second requirement -- "nesmie to skončiť
    tichým čakaním na človeka"): `state['goalrearm_noprogress'][sid]` now
    stores the WALL-CLOCK TIMESTAMP of the first sighting (not a bare
    `True`), so a later sweep can measure real elapsed dark time; once
    that clears `GOAL_REARM_UNTRACKED_PING_S`, ONE deduped Discord ping
    fires (`state['goalrearm_untracked_pinged'][sid]`, mirrors
    `_goal_stall_nudge`'s own give-up shape verbatim: the owner resolved
    via `stream_redirect` over the pane's raw owner, a stable
    `dedup_key`, silent under `dry_run`), telling the human this
    session's `/goal` has no
    recoverable trace and needs a manual re-arm. Rejected alternative --
    the ticket's OTHER option, an AUTOMATIC re-arm reusing job 9's own
    delivery machinery ("rovnaká cesta, akou job 9 armuje po banneri"):
    this function has none of job 9's own fresh-capture/busy/draft
    classification (`_classify_boundary`/`pane_in_mode`, always re-checked
    immediately before job 9 ever types), and blindly retyping a `/goal`
    into a session with zero live evidence it is even safe to type into
    risks the #35/#36 stash/collapse hazard class this file has hit
    repeatedly; the ticket itself offers the ping as an equally acceptable
    alternative ("alebo sa to eskaluje pingom"), and today NEITHER
    happens ("dnes sa nedeje ani jedno") -- a bounded, deduped, human-
    facing ping closes exactly that gap without any new keystroke-sending
    code path. Both new bookkeeping keys are popped the moment EITHER
    evidence source resolves, so a later, genuinely NEW dark episode is
    never permanently suppressed by an old one's ping."""
    if rec.get("recovery_scanned"):
        return []
    # #324-review (cost note) -- the goalarm-record check is a plain dict
    # lookup (no I/O); `_live_autopilot_progress` is a real file read.
    # Try the cheap one FIRST so it can short-circuit the expensive one,
    # mirroring the #320-review "cheap gates before an expensive read"
    # discipline this file already applies elsewhere.
    #
    # #324-review MAJOR-4 -- a bare `state['goalarm'][pid]` truthiness
    # check has NO freshness bound at all, unlike `_live_autopilot_
    # progress`'s own explicit `< GOAL_REARM_PROGRESS_WINDOW_S` window
    # (whose comment already says "fresh means an ACTIVE run, not
    # week-old leftover state"). `state['goalarm']` is a NAMED store the
    # cleanup loop deliberately never prunes, and its key is a tmux PANE
    # ID -- unique within one tmux server generation, but reused from
    # `%0` after a server restart while the state file persists across
    # it. Reproduced live on dev1: entries up to 16.9 days old, several
    # colliding with CURRENTLY live, unrelated panes. Bound it by the
    # SAME window `_live_autopilot_progress` already uses, with the same
    # `0 <=` lower clamp #312 uses for its own two clocks (a future-dated
    # entry -- clock skew -- must never look "already fresh").
    has_goalarm_record = False
    if pid and state is not None:
        ga_ts = (state.get("goalarm") or {}).get(pid)
        if isinstance(ga_ts, (int, float)) and not isinstance(ga_ts, bool):
            has_goalarm_record = (
                0 <= (now - ga_ts) < GOAL_REARM_PROGRESS_WINDOW_S)
    has_progress = (has_goalarm_record
                    or _live_autopilot_progress(cwd, progress_dir, now))
    if not has_progress:
        logs = []
        if state is not None:
            nr = state.setdefault("goalrearm_noprogress", {})
            first_seen = nr.get(sid)
            # #324-review CRITICAL-2/MINOR-5 -- `first_seen` used to be
            # trusted verbatim the moment it was non-None. Two ways that
            # is unsafe: (a) a LEGACY entry written by the pre-#324 code
            # is the bare bool `True` -- `now - True` is `now - 1`
            # (Python treats `bool` as `int`), which clears the 30-min
            # bound by ~56 years and pings on the very first sweep after
            # deploy with a nonsense "vyše 29770187 minút" claim
            # (reproduced live against dev1's own real persisted state);
            # (b) a FUTURE-dated value (clock skew, a restored snapshot)
            # would never clear the bound at all, silently reproducing
            # the exact permanent-silence defect this whole branch
            # exists to remove. Re-stamp to `now` whenever the stored
            # value isn't a genuinely usable, non-future number --
            # mirrors #312's own `0 <=` future-date guard on its two
            # clocks, one level up.
            if (not isinstance(first_seen, (int, float))
                    or isinstance(first_seen, bool)
                    or first_seen > now):
                first_seen = None
            if first_seen is None:
                nr[sid] = now
                first_seen = now
                logs.append(
                    "skip untracked-no-progress (goal-rearm) %s -> this "
                    "session's /goal marker was never tracked and there "
                    "is no live autopilot-progress evidence, nor a job-9 "
                    "arm record, for this repo -- recovery only ever acts "
                    "on one of those two, so a manually-armed /goal with a "
                    "lost transcript marker stays dark until either "
                    "resolves or the %ds escalation ping fires"
                    % (loc, GOAL_REARM_UNTRACKED_PING_S))
            pinged = state.setdefault("goalrearm_untracked_pinged", {})
            if (not pinged.get(sid)
                    and (now - first_seen) > GOAL_REARM_UNTRACKED_PING_S):
                if dry_run:
                    # #324-review CRITICAL-1 -- a `--dry-run` sweep run
                    # against REAL persisted state (this repo's own
                    # documented manual diagnostic) must NEVER mark
                    # `pinged` -- the exact `dark_pinged` #238-review
                    # 🟡F4 fix already made this job's OTHER give-up
                    # ping immune to: setting the flag before checking
                    # `send_fn`/`dry_run` would permanently consume the
                    # one-shot escalation with nothing ever actually
                    # sent, silently reproducing the very defect this
                    # branch exists to remove. (The unfixed sibling,
                    # `_goal_stall_nudge`, still has the pre-#324 shape
                    # -- a pre-existing, out-of-scope defect under this
                    # repo's own FREEZE policy, never a precedent to
                    # copy.) READY here is diagnostic-only: it neither
                    # marks `pinged` nor calls `send_fn`, so a later
                    # REAL sweep still escalates normally.
                    logs.append(
                        "READY (goal-rearm untracked-escalate) %s -> "
                        "would ping the owner -- no evidence this goal "
                        "was ever armed after %ds dark"
                        % (loc, int(now - first_seen)))
                elif send_fn is not None:
                    from notify import stream_redirect
                    pinged[sid] = True
                    send_fn(
                        "⚠️ **%s** — `/goal` v tejto relácii nemá žiadnu "
                        "dohľadateľnú stopu (pätička nesvieti, žiadny "
                        "priebeh dávky, ani záznam o odoslanom arme) už "
                        "vyše %d minút. Treba ju ručne prearmovať (%s)."
                        % (project_label(cwd),
                           int((now - first_seen) // 60), loc),
                        owner=stream_redirect(pane_owner(pid, run)) or None,
                        # #324-review MAJOR-3 -- `sid` ALONE is stable
                        # across a revival: a session whose evidence
                        # resolves (popping this bookkeeping), then
                        # goes dark again with NO evidence a second
                        # time, would produce the IDENTICAL dedup key
                        # for the genuinely new episode -- the same
                        # class `dark_pinged`'s own dedup fix already
                        # closed one job above. Folding in `first_seen`
                        # (this episode's own anchor, re-stamped fresh
                        # whenever the episode genuinely restarts,
                        # per the CRITICAL-2/MINOR-5 fix above) makes
                        # each episode's ping distinct.
                        dedup_key="goalrearm-untracked:%s:%d"
                                  % (sid, int(first_seen)),
                        dry_run=dry_run)
                    logs.append(
                        "ESCALATED (goal-rearm) %s -> no evidence this "
                        "goal was ever armed after %ds dark; pinged the "
                        "owner" % (loc, int(now - first_seen)))
        return logs
    if state is not None:
        state.get("goalrearm_noprogress", {}).pop(sid, None)
        state.get("goalrearm_untracked_pinged", {}).pop(sid, None)
    _, mark = scan_goal_markers(tpath, off=0)
    if mark is None or mark.get("state") != "set":
        rec["recovery_scanned"] = True
        return []
    mts = mark.get("ts")
    mts_fresh = mts is not None and 0 <= (now - mts) < GOAL_ARMED_ACTIVITY_GRACE_S
    tmtime_fresh = 0 <= (now - tmtime) < GOAL_ARMED_ACTIVITY_GRACE_S
    if mts_fresh or tmtime_fresh:
        age = int(now - mts) if mts is not None else int(now - tmtime)
        return ["skip maybe-truncated-footer (goal-rearm) %s -> the "
                "transcript's own recovered marker (or the transcript "
                "file itself) is only %ds old -- too recent to trust a "
                "missing ◎ glyph as proof this goal is actually dead "
                "(could be a TRUNCATED statusline render, not a dead "
                "goal) -- deferring, not consuming the recovery"
                % (loc, age)]
    rec["recovery_scanned"] = True
    rec["mark"] = "set"
    rec["payload"] = mark.get("payload")
    rec["mts"] = mts
    rec["mseen"] = now
    return ["RECOVERED (goal-rearm) %s -> %s and the transcript's own "
            "last /goal marker (never scanned by this job before) was "
            "'set' -- re-arm eligibility restored"
            % (loc, "a job-9 goalarm record for this pane is present"
               if has_goalarm_record else
               "live autopilot-progress evidence present")]


def _goal_cleared_stale(rec):
    """True when THIS job's own REALITY observation (`rec['last_armed']`,
    the pane footer read LIVE, independent of the transcript) proves a
    genuine arm happened AFTER the transcript's newest tracked marker was a
    'cleared' one (#320) -- the transcript alone cannot see this: a
    busy-pane arm (or, per #320's own dev1 forensics, an arm CC silently
    failed to write ANY marker for at all) leaves `rec['mark']` stuck at
    'cleared' forever, which used to permanently block this session even
    though it plainly was re-armed afterward.

    #170's own invariant survives untouched by construction: a genuine,
    never-re-armed clear has no `last_armed` postdating it at all, so this
    stays False for it. Unmeasurable (either field absent) never guesses
    -> False, never True."""
    last_armed = rec.get("last_armed")
    mts = rec.get("mts")
    if last_armed is None or mts is None:
        return False
    return last_armed > mts


def goal_rearm(now, run, state, send_fn=None, dry_run=False, projects_dir=None,
               handled=None, max_attempts=None, streak_s=None, confirm_s=None,
               sleep_fn=None, templates_path=None, backlog_fetch=None,
               time_fn=None, sweep_deadline=None, questions_path=None,
               persist=None, progress_dir=None):
    """Job 20 — see the section comment above (#76). Mutates
    `state['goal_rearm']`; returns log lines. Best-effort (exceptions are
    run_once's to catch, like every other job here).

    `progress_dir` (optional, #312): the test seam for
    `_goal_recover_untracked`'s own `_live_autopilot_progress` lookup —
    production leaves it `None`, which resolves the REAL
    `~/.claude/autopilot-progress/` dir.

    `handled` (optional): the SAME per-sweep set job 14 populates (jobs
    15/17 also populated it once, before both were REMOVED, #102) — a sid
    compacted THIS sweep is skipped outright (a long goal must never be
    typed into a pane whose `/compact` is still draining).

    `templates_path` (optional, #64): the installed autopilot SKILL. Wired =
    on, like jobs 13/14/16 — without it the STALE-TEMPLATE branch does not
    run at all and this job behaves exactly as it did before.

    `backlog_fetch` (optional, #160 defect 1): wired like every other
    network call in this file — without it the goal-achieved branch behaves
    exactly as it did before (unconditional skip, no verification).

    `time_fn`/`sweep_deadline` (both optional, default None -> unbounded,
    exactly the pre-#160-review behavior) — #160-review-style finding 🟡F2
    (this ticket's own review, measured live): before this fix, this job
    had NO wall-clock self-bound of its own (unlike jobs 8/9, #255) and now
    makes a blocking `subprocess.run` per distinct repo it re-verifies
    (`_watchdog_backlog_fetch`, measured 4-6s per call on this box, versus
    the ~1s the raw listing it replaced cost) — on a box already being
    SIGTERM-killed by the systemd 120s `TimeoutStartSec` several times a
    day with essentially no headroom left, an unbounded number of such
    calls in the LAST big job of the sweep is the #172 livelock shape at a
    new address. Mirrors jobs 8/9's OWN `time_fn`/`sweep_deadline` pattern
    exactly (checked strictly BETWEEN panes, before a pane's own work
    starts, never nested inside one pane's delivery) — a deferred pane's
    state is untouched, so it is retried next sweep exactly like an
    untouched pane always would be.

    `questions_path` (optional, #161): the test seam for
    `_goal_question_park_nudge`'s delivered-ping lookup — production leaves
    it `None`, which resolves the REAL `~/.claude/discord-questions.json`
    via `notify.load_questions()`'s own default (no wiring needed in
    `airuleset.py`, unlike `backlog_fetch`/`templates_path`: the real path
    already matches this box's own `HOME` with no subprocess involved).

    `persist` (optional, #161-review MAJOR M2): threaded ONLY into
    `_goal_question_park_nudge`, called immediately after `rec['qparked_ts']`
    is set and before the keystroke send — never wired = the state is
    persisted only at `run_once`'s own trailing `save_state`, exactly the
    pre-review behaviour."""
    run = run or _default_run
    projects_dir = projects_dir or PROJECTS_DIR
    templates = load_goal_templates(templates_path)
    max_attempts = GOAL_REARM_MAX_ATTEMPTS if max_attempts is None else max_attempts
    streak_s = GOAL_REARM_STREAK_S if streak_s is None else streak_s
    confirm_s = GOAL_REARM_CONFIRM_S if confirm_s is None else confirm_s
    recs = state.get("goal_rearm") or {}
    logs = []
    time_fn = time_fn or time.monotonic

    def _save():
        state["goal_rearm"] = recs

    candidates = list(_reconcile_candidate_panes(run))
    for idx, (pid, cwd, _cmd) in enumerate(candidates):
        if sweep_deadline is not None and time_fn() >= sweep_deadline:
            # #160-review 🟡F2 — never START a new pane's work once the
            # shared sweep budget is exhausted; nothing has been fetched or
            # typed for THIS (or any later) pane yet, so deferring here
            # loses nothing.
            logs.append("goal-rearm-budget-exceeded — %d/%d panes handled "
                        "this tick, rest retried next" % (idx, len(candidates)))
            break
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
            rec["last_armed"] = now        # #101 — the freshest proof this
                                            # goal was genuinely alive; the
                                            # revival path trusts THIS over an
                                            # old `mts` when it's available
            if rec.get("dark_pinged"):
                # #160 — genuinely alive again; a FUTURE dark episode (a
                # different death) must be able to ping once more.
                rec["dark_pinged"] = False
            if rec.get("stale_achieved_pinged"):
                # #335 — the SAME "genuinely alive again, a future episode
                # gets its own fresh ping" reset, for the SEPARATE
                # stale-achieved ping's own dedicated flag (never
                # `dark_pinged` — that one is owned by the sibling
                # "skip stale-goal" branch, and its OWN reset inside the
                # "stale-but-outage" branch fires on every sweep this
                # session stays dark-and-outage-presumed, which would
                # otherwise silently re-arm this ping's one-shot dedup
                # every single sweep).
                rec["stale_achieved_pinged"] = False
            if state is not None:
                # #324 — the footer is proof of REALITY that is stronger
                # than anything `_goal_recover_untracked` itself could ever
                # observe; drop its own untracked-no-evidence bookkeeping
                # so a LATER, genuinely new dark episode gets its own fresh
                # escalation rather than being silently gated by this one.
                state.get("goalrearm_noprogress", {}).pop(sid, None)
                state.get("goalrearm_untracked_pinged", {}).pop(sid, None)
            if rec.get("queued_at"):
                rec["queued_at"] = None
                logs.append("CONFIRMED (goal-rearm) %s -> ◎ /goal lit again"
                            % loc)
            # ARMED for real — but is the loop actually FIRING? (the second
            # shape, and the one the forensics points at)
            logs += _goal_stall_nudge(now, run, rec, sid, cwd, pid, captured,
                                      tpath, tmtime, loc, send_fn, dry_run,
                                      handled, projects_dir)
            # …or is it genuinely STOPPED on an unanswered `❓ NEEDS YOU`
            # past the bounded wait? (#161 — independent of the stall
            # nudge above, which correctly never touches a `❓` marker)
            logs += _goal_question_park_nudge(now, run, rec, sid, cwd, pid,
                                              captured, tpath, loc, send_fn,
                                              dry_run, handled, projects_dir,
                                              questions_path=questions_path,
                                              persist=persist)
            # …and is it firing the CURRENT template? (the third shape, #64)
            if templates:
                logs += _goal_template_drift(now, run, rec, sid, cwd, pid,
                                             captured, loc, templates, send_fn,
                                             dry_run, handled, projects_dir,
                                             sleep_fn, tmtime=tmtime)
            _save()
            continue
        if rec.get("mark") != "set":
            # #312 -- `mark is None` (this job never tracked this session's
            # goal history at all) is NOT the same state as `mark ==
            # "cleared"` (an explicit clear THIS job itself scanned, the
            # #170 class) -- only the former is ever worth a recovery
            # attempt; a `"cleared"` mark falls straight through to the
            # unchanged `continue` below, exactly as before this ticket.
            if rec.get("mark") is None:
                logs += _goal_recover_untracked(now, rec, sid, cwd,
                                                tpath, tmtime, loc,
                                                progress_dir=progress_dir,
                                                state=state, pid=pid,
                                                run=run, send_fn=send_fn,
                                                dry_run=dry_run)
                _save()
            elif _goal_cleared_stale(rec):
                # #320 -- REALITY (this job's OWN direct footer-lit
                # observation, `rec['last_armed']`) proves a genuine arm
                # happened AFTER the transcript's newest 'cleared' marker,
                # even though NO transcript marker of any shape records it
                # (dev1 2d02a127, live: three `goal-autoarm OK` sends after
                # the clear, zero resulting markers anywhere in the whole
                # file). Treat the cleared state as STALE, never #170's
                # standing user-off signal, and fall through into the SAME
                # dark-check / re-arm machinery every other tracked session
                # already uses below -- never a new, parallel path.
                logs.append("stale-cleared-but-rearmed (goal-rearm) %s -> "
                            "last_armed postdates the transcript's own "
                            "'cleared' marker; treating as armed" % loc)
                rec["mark"] = "set"
                _save()
            if rec.get("mark") != "set":
                continue                   # never armed here, still nothing
                                           # recoverable, or the user
                                           # deliberately cleared it
        last_seen_alive = rec.get("last_armed")
        if last_seen_alive is None:
            last_seen_alive = rec.get("mts")
        # #335-review F3 (MAJOR, sub-findings S3+S4) -- an explicit `/exit`
        # deserves the SAME unconditional-at-any-darkness protection
        # `/goal clear` already gets via the `rec.get("mark") != "set"`
        # check above: CC writes NO transcript marker for `/exit` at all,
        # so without an EARLY, unconditional check here a FRESH exit (well
        # inside `GOAL_REARM_MAX_DARK_S`) got ZERO protection -- the
        # achieved-banner re-arm below (`GOAL_ACHIEVED_MARKER in
        # _above_input_box(captured)`) is reachable regardless of
        # `dark_by_age`, and `_goal_dark_died_by_outage`'s own #335
        # exit-check only ever runs once `dark_by_age and not active_now`
        # -- both false for a session exited moments after its goal
        # finished (S3). Checking it HERE, before `active_now` is even
        # computed, also structurally closes S4 for free: S4's own
        # scenario (`_last_real_turn_ts` — job 10's #177-hardened reader,
        # which deliberately counts a plain `/exit` entry as "real
        # activity" for ITS purpose — making `active_now` wrongly True)
        # REQUIRES `exit_ts > last_seen_alive` (a later confirmed arm would
        # itself be newer real activity, so the exit could never be the
        # newest turn otherwise) -- exactly the condition this check
        # already intercepts on, before `active_now` is ever reached.
        # `_goal_dark_died_by_outage`'s own thorough `off=0` exit-check
        # (already shipped) stays as the fallback for the harder,
        # already-dark case this cheap tail-bootstrap read can legitimately
        # miss (a long-lived busy session that pushed its OWN final `/exit`
        # past the tail window before it died -- the exact scenario that
        # check's own docstring already documents). Unmeasurable
        # (`last_seen_alive` absent) never guesses -- matches this ticket's
        # own established fail-open convention throughout.
        #
        # #335-review NEW-2 (round 2, MINOR, accepted disclosed cost): this
        # tail-bootstrap read (up to `GOAL_MARK_TAIL_BYTES`, 4 MB) now runs
        # UNCONDITIONALLY every ~60s sweep for every tracked `mark=="set"`,
        # non-armed pane -- including the common steady state of a
        # finished-goal session still in ordinary use, not just the rare
        # dark-past-the-cap population `_goal_dark_died_by_outage`'s own
        # `off=0` fallback is reserved for. It cannot be gated behind
        # `dark_by_age` the way that fallback is, since S3's whole point is
        # protecting the NON-dark population too. It is page-cached and far
        # cheaper than the `off=0` full-file reads this repo's prior
        # reviews have already gated behind stricter cost discipline
        # elsewhere in this same function, so it is accepted here rather
        # than optimized -- matching the F5 revert's own precedent
        # (documented at this file's `revived_from_dark_outage` branch) of
        # stating a cost explicitly rather than risking a masked-recovery
        # regression from a premature cache.
        exit_ts = _goal_user_exit_ts(tpath)
        # #335-review NEW-1 (round 2) -- `last_seen_alive` prefers
        # `rec['last_armed']` (WHEN this job last CONFIRMED an arm via the
        # live footer) over `rec['mts']` (the transcript marker's own
        # timestamp) whenever `last_armed` is present at all -- correct in
        # general (#321 shape A2), but it can be STALE relative to a
        # GENUINE re-arm: the user exits, then re-arms with a fresh
        # `/goal` (a real marker, `mts > exit_ts`), and if that new goal
        # dies again before the pane's footer is ever sampled with it
        # showing armed, `last_armed` never advances to reflect the
        # re-arm at all. Without this guard, `exit_ts > last_seen_alive`
        # (built on the STALE `last_armed`) would wrongly treat the
        # already-superseded exit as still decisive forever. Mirrors the
        # SAME `mts >= exit_ts` comparison `_goal_was_cleared_by_user`
        # already applies one function over -- but NOT its fail direction
        # on an unmeasurable `mts`: that function fails OPEN (unparseable
        # marker ts -> never treat the exit as decisive), while this guard
        # deliberately fails CLOSED (`mts is None` -> not superseded ->
        # `skip exited` can still fire on `last_armed` alone) -- matching
        # THIS function's own established opposite fail-direction
        # convention (`_goal_dark_died_by_outage`'s docstring: a re-arm
        # here types into a pane whose current use is unknown, so an
        # unmeasurable input must stay conservative, never guess toward
        # re-arming). #335-review round-3 confirmed this asymmetry is
        # deliberate and correct, not a bug (round-3 review comment).
        mts = rec.get("mts")
        exit_superseded_by_newer_marker = (mts is not None
                                           and mts >= exit_ts) \
            if exit_ts is not None else False
        if (exit_ts is not None and last_seen_alive is not None
                and exit_ts > last_seen_alive
                and not exit_superseded_by_newer_marker):
            logs.append("skip exited (goal-rearm) %s -> an explicit /exit "
                        "postdates the last confirmed arm; never re-arming "
                        "without the user's own action" % loc)
            continue
        # #321 shape A2 -- `last_armed`/`mts` is WHEN this job last
        # CONFIRMED an arm, never a measure of whether the session is alive
        # NOW. A continuously busy loop (live, dev1: transcript mtime in
        # seconds, `run 12/19` in the footer -- never "dark" by any real
        # measure) can run for hours without a fresh confirmation (the SAME
        # truncated-`◎`-glyph class `GOAL_ARMED_ACTIVITY_GRACE_S`/
        # `_goal_recover_untracked` already guard for a DIFFERENT branch)
        # while the dark cap's own premise -- "typing into a pane whose
        # CURRENT purpose is unknown after this long dark" -- is flatly
        # false for it. Measure darkness by TRANSCRIPT ACTIVITY too (mirrors
        # #177's job-10 fix verbatim: the transcript's own last REAL turn,
        # clamped to <= the file's raw mtime so a future-dated/clock-skewed
        # entry can never read as "more recent than the OS itself observed"
        # -- a `min()` only ever REMOVES that violation, never introduces
        # one). Genuine, recent activity means the whole dark branch is
        # skipped entirely -- never a new re-arm path, just the SAME
        # machinery every other non-dark session already falls through to
        # below.
        #
        # Adversarial-review finding MINOR-1 (this ticket's own review):
        # the activity read (a 2 MB tail read + a json.loads per line) is
        # only ever CONSUMED inside the dark `if` below -- gated behind the
        # SAME cheap age comparison that already decides candidacy, not
        # paid unconditionally for every mark=="set" pane on every sweep
        # (the #320-review "cheap gates before an expensive read" lesson,
        # applied here too).
        dark_by_age = (last_seen_alive is not None
                       and (now - last_seen_alive) > GOAL_REARM_MAX_DARK_S)
        active_now = False
        if dark_by_age:
            activity_ts = _last_real_turn_ts(tpath)
            if activity_ts is None:
                activity_ts = tmtime
            elif tmtime is not None:
                activity_ts = min(activity_ts, tmtime)
            active_now = (activity_ts is not None
                         and 0 <= (now - activity_ts) <= GOAL_REARM_MAX_DARK_S)
        # #335 — set True only inside the "presumed outage, keep re-arming
        # past the cap" branch just below; consumed by the ACHIEVED-marker
        # check further down to decide whether a `backlog_open is True`
        # verdict may actually RE-ARM or must instead get the SAME
        # one-shot-ping-never-retype treatment the sibling "skip
        # stale-goal" branch already uses. See that check's own comment
        # for why: the paragraph directly below this one ((a)) reasoned
        # that a stale achieved banner is safe to re-arm because the user
        # "kept using the session for other work" — but by construction
        # this whole `if` only runs when `active_now` is ALREADY False,
        # i.e. there has been no activity of any kind, so that premise
        # never held for the population this flag actually gates.
        revived_from_dark_outage = False
        if dark_by_age and not active_now:
            # #335-review F5 (MINOR, performance) -- once a session is
            # PERMANENTLY trapped in this branch (never re-armed: no
            # achieved banner in view, or `backlog_open` never turns True),
            # `_goal_dark_died_by_outage`'s own `off=0` full-file read
            # (measured ~5.19s on a real 587 MB transcript, plus this
            # check's own tail-bootstrap exit scan, ~2s more) repeats every
            # 60s sweep forever for that session. A `tmtime`-keyed cache
            # was tried and REVERTED: `test_dark_pinged_is_reset_by_the_
            # outage_branch_itself` proves mtime is NOT a safe invalidation
            # signal here -- a `chmod` that restores READABILITY (this
            # test's own repro of a transient permission/outage recovery)
            # changes nothing about the file's mtime at all, so a cache
            # keyed on it would silently keep serving the STALE
            # "couldn't read, defaulted to False" verdict forever even
            # after the transcript becomes readable again -- the exact
            # kind of masked recovery this whole ticket exists to prevent.
            # Closing that gap correctly would need `_goal_dark_died_by_
            # outage` to distinguish "confirmed no-exit" from "unreadable,
            # defaulted" as two DIFFERENT return values, which is a larger
            # change than a MINOR finding warrants; accepted, disclosed
            # cost, not fixed here.
            if _goal_dark_died_by_outage(tpath, last_seen_alive):
                # #173 — a fresh, independent re-read of the transcript
                # finds NO `Goal cleared:` after the last `Goal set:`, so
                # this darkness is presumed a technical OUTAGE (a crash, a
                # binary update, an API error, or the watchdog itself
                # being unable to sweep for hours — #172's own incident)
                # rather than a deliberate #170 clear. Fall through into
                # the SAME bounded payload/hash/streak/attempt machinery
                # every other re-arm reason already uses below, instead
                # of the permanent stale-goal skip.
                #
                # Adversarial-review findings on this exact ticket
                # (2026-08-06), accepted as the DIRECT, disclosed cost of
                # the decided semantics rather than bugs to route around:
                # (a) a goal that finished NATURALLY (never writes a
                # marker, #101) is byte-indistinguishable from an outage
                # here — the `GOAL_ACHIEVED_MARKER` viewport check right
                # below still catches this for a RECENT completion still
                # in view; a completion the user has since scrolled past
                # (typically: they kept using the same session for other
                # work) can be re-armed once more, self-terminating on
                # the next sweep once its own fresh `✔ Goal achieved` or
                # `Goal set:` becomes the newest signal again — CORRECTED
                # by #335 (live P0 on simap@subdev, 66.5h dark, zero
                # activity of ANY kind the whole time): that "kept using
                # it for other work" premise is exactly what `active_now`
                # has already disproven by the time this branch runs, so
                # `revived_from_dark_outage` (set below) now BLOCKS the
                # achieved-marker check's own re-arm for precisely this
                # case — see its own comment; (b) the dark cap used to be
                # this cycle's only permanent terminator — a session that
                # revives-and-fails repeatedly now gets a GAVE UP ping
                # roughly every `GOAL_REARM_STREAK_S` (2h) instead of
                # exactly once, which is the direct, accepted cost of
                # "re-arm continues normally" (the ticket's own words)
                # rather than a new cap invented here.
                logs.append("stale-but-outage (goal-rearm) %s (%d s dark, "
                            "no explicit clear -> re-arming past the cap)"
                            % (loc, int(now - last_seen_alive)))
                revived_from_dark_outage = True
                if rec.get("dark_pinged"):
                    # a later genuinely-unconfirmable dark episode must
                    # still be able to ping once more (mirrors the ARMED
                    # branch's own reset above) — this session is no
                    # longer presumed-resolved.
                    rec["dark_pinged"] = False
                    _save()
            else:
                # #101 — this job never saw the goal actually lit recently (or at
                # all), and the viewport-based achieved check just below only
                # protects a RECENT death (days of normal conversation scroll the
                # `✔ Goal achieved` line out of view). A marker this old is
                # presumed already resolved, one way or another; never type its
                # payload into whatever the pane is being used for now.
                logs.append("skip stale-goal (goal-rearm) %s (%d s since last "
                            "confirmed armed)" % (loc, int(now - last_seen_alive)))
                # #160 defect 3 — this used to be a PERMANENT, silent dead end:
                # the pane is retired forever and nobody is ever told (the live
                # gatekeeper incident's own 07:05:29 boundary, confirmed by a
                # hard-debug consult against the surviving systemd journal). A
                # bounded, ONE-SHOT ping — never a retry, never a re-arm attempt
                # (typing a potentially-stale payload into a pane whose current
                # purpose is unknown after this long dark would be the riskier
                # move) — at least tells a human this stream needs a look.
                if not rec.get("dark_pinged"):
                    # #238-review-style finding 🟡F4 (this ticket's own review):
                    # the flag/save used to happen BEFORE the `send_fn is not
                    # None and not dry_run` check, so a `--dry-run` sweep run
                    # against REAL state (a normal manual diagnostic on this
                    # repo, per its own playbook) would permanently consume the
                    # one-shot ping with nothing ever actually sent — moved
                    # inside the real-send branch so only a GENUINE send marks
                    # it delivered.
                    if send_fn is not None and not dry_run:
                        from notify import stream_redirect  # #212: STREAM_NOTIFY_OWNER-aware
                        rec["dark_pinged"] = True
                        _save()
                        send_fn("⚠️ **%s** — `/goal` slučka stíchla pred vyše %d "
                                "hodinami (`◎ /goal` už dávno nesvieti) a nikdy "
                                "sa znovu neozvala; automatické preármovanie sa "
                                "vzdalo, lebo payload je príliš starý na to, aby "
                                "sa dal bezpečne napísať do panela (%s). Pozri sa "
                                "tam prosím ručne."
                                % (project_label(cwd),
                                   GOAL_REARM_MAX_DARK_S // 3600, loc),
                                owner=stream_redirect(pane_owner(pid, run)) or None,
                                # #238-review-style finding 🔴F2 (this ticket's
                                # own review) — `hash`/`mts` alone are STABLE
                                # across a revival: a session that goes dark,
                                # gets pinged, revives (`dark_pinged` reset
                                # above), then goes dark AGAIN with the SAME
                                # goal payload and no new transcript marker in
                                # between would produce the IDENTICAL dedup key
                                # for the second, genuinely new dark episode —
                                # the exact same bug class the give-up ping's
                                # own `first`-timestamp fix (above) already
                                # closed. Folding in `last_seen_alive` (this
                                # episode's own anchor — the last confirmed-armed
                                # instant BEFORE this specific dark stretch
                                # began) makes each episode's ping distinct.
                                dedup_key="goaldark:%s:%s:%d"
                                          % (sid, rec.get("hash") or rec.get("mts"),
                                             int(last_seen_alive or 0)),
                                dry_run=dry_run)
                continue
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
            #
            # #160 defect 1 — that signal is only trustworthy when the
            # session's own last real turn made a GENUINE backlog-empty
            # CLAIM (`🏁 BACKLOG EMPTY:`, #159's own proof protocol) — a
            # mere MENTION (fenced/backticked/mid-line) is not a claim at
            # all (`backlog_marker_gate.classify_backlog_empty_claim`, #166).
            # Only when a genuine claim is present is it worth spending a
            # `gh` call (cached per repo, `_cached_backlog_open`) to verify
            # it against reality: a claim proven FALSE (tickets remain) is
            # NOT the safe direction to skip on — fall through into the
            # SAME bounded payload/hash/streak/attempt machinery every
            # other re-arm reason already uses below. No claim at all, a
            # genuinely empty backlog, or an unmeasurable check (fail-open,
            # #166) all keep the original skip.
            # #160-review-style finding 🔵F7 (this ticket's own review) --
            # a repo-root SIBLING module import executed inside the
            # per-pane loop; the ExecStart wiring makes this resolve in
            # production (verified, not assumed), but this repo runs its
            # OWN working tree live every 60s -- a mid-deploy window where
            # one file has landed and its sibling hasn't is real. Any
            # failure here degrades to "no claim" (the pre-#160 skip),
            # loudly, rather than escaping and killing job 20 for EVERY
            # pane this sweep (this repo's own established rule: "resolve
            # a new name in its OWN try, fall back to None, and LOG the
            # degraded mode").
            marker_const = "🏁 BACKLOG EMPTY:"
            try:
                import backlog_marker_gate
                marker_const = backlog_marker_gate.MARKER
                claim_present, claim_reason = (
                    backlog_marker_gate.classify_backlog_empty_claim(
                        transcript_last_assistant_text(tpath)))
            except Exception as e:
                claim_present, claim_reason = False, "classifier error: %r" % (e,)
            # #312 item 3 -- a session's LAST turn failing to restate the
            # claim cleanly (a truncated report, a plain one-line
            # "✅ DONE:", the classifier missing a malformed line) must
            # not, by itself, be trusted as proof this loop's own STOP
            # CONDITIONS were never backlog-shaped. `rec['payload']` is
            # THIS loop's own tracked goal text -- persists across dark
            # cycles, independent of what the LAST turn said -- and every
            # shipped `/goal` template's STOP CONDITIONS names the SAME
            # marker verbatim, so its presence there is a reliable,
            # session-specific "this loop DOES care about the backlog"
            # signal that survives a missing/garbled final claim. A
            # payload that never mentions the marker (a one-off, non-batch
            # `/goal`) is untouched -- `claim_present` alone stays
            # decisive there, exactly as before #312.
            backlog_shaped = (
                claim_present
                or marker_const in (rec.get("payload") or ""))
            if not backlog_shaped:
                # no genuine claim to verify at all (mention-only, absent, or
                # this goal's stop condition isn't backlog-shaped) -- nothing
                # changes, same as before #160.
                logs.append("skip goal-achieved (goal-rearm) %s -> loop finished "
                            "legitimately (%s)" % (loc, claim_reason))
                continue
            # #350 -- checked BEFORE the (network-calling) backlog_open
            # lookup just below, deliberately: when the loop is genuinely
            # still (A)-BLOCKED there is no reason to spend a `gh` call at
            # all (nothing here is going to re-arm either way), and doing
            # it first means EVERY branch that follows -- the dark-outage-
            # revival ping AND the plain FALSE-ACHIEVED re-arm -- is
            # uniformly protected, never just the one log line. See the
            # function's own docstring for the full incident + design.
            blocked_line = _goal_blocked_on_unanswered_question(tpath)
            if blocked_line:
                logs.append(
                    "skip (A)-blocked (goal-rearm) %s -> the loop's own "
                    "last real turn is still an unanswered %r; this is a "
                    "LEGITIMATE stop condition, never re-arming without "
                    "the user's own reply" % (loc, blocked_line))
                continue
            backlog_open = _cached_backlog_open(cwd, backlog_fetch, state, now)
            if backlog_open is True and revived_from_dark_outage:
                # #335 P0 -- live-reproduced on simap@subdev: an achieved
                # banner that has been sitting DARK past
                # `GOAL_REARM_MAX_DARK_S` (66.5h in the real incident) with
                # NO activity of any kind since (this whole `if` only ran
                # because `active_now` was ALREADY False -- see the
                # comment above `revived_from_dark_outage`'s own
                # assignment) is, for every purpose here, the user having
                # stepped away from a session that finished ALL its
                # available work -- indistinguishable from a deliberate
                # stop. Re-arming it the moment new backlog appears would
                # violate the user-stop invariant just as directly as
                # re-arming an explicit `/goal clear` would. Treat it
                # EXACTLY like the sibling "skip stale-goal" branch: a
                # bounded, ONE-SHOT ping (its OWN dedicated bookkeeping,
                # `stale_achieved_pinged` -- see the comment right below
                # this one for why it is NOT `dark_pinged`) for the
                # identical "dark past the cap, no positive signal to act
                # automatically" state, just discovered via the
                # achieved-marker path instead of the outage path, never
                # an automatic re-arm. A FRESH achieved
                # banner (`revived_from_dark_outage` False, i.e. `not
                # dark_by_age` or `active_now`) is completely unaffected —
                # #160's own "resume promptly once new work appears"
                # behavior for an actively-cycling loop stays exactly as
                # before this ticket.
                logs.append(
                    "skip stale-achieved (goal-rearm) %s -> achieved %d+ s "
                    "ago with no activity since and new backlog available, "
                    "but re-arming a session this stale without the user's "
                    "own action would violate the user-stop invariant"
                    % (loc, int(now - last_seen_alive)))
                # #335 -- a DEDICATED flag, never `dark_pinged` (that one is
                # owned by the sibling "skip stale-goal" branch, and its own
                # reset inside the "stale-but-outage" branch above fires on
                # EVERY sweep this session stays dark-and-outage-presumed --
                # reusing it here would silently re-arm THIS ping's one-shot
                # dedup every single sweep instead of firing once).
                if not rec.get("stale_achieved_pinged"):
                    if send_fn is not None and not dry_run:
                        from notify import stream_redirect  # #212: STREAM_NOTIFY_OWNER-aware
                        rec["stale_achieved_pinged"] = True
                        _save()
                        # #335-review F7 (MINOR) -- the REAL elapsed time
                        # since this session was last confirmed armed, not
                        # the hardcoded `GOAL_REARM_MAX_DARK_S` cap: this
                        # branch fires anywhere PAST the cap, often well
                        # past it (the real simap@subdev incident this
                        # ticket was filed over was 66.5h dark, not 6h) --
                        # a message hardcoded to "vyše 6 hodín" understates
                        # how long the session has genuinely sat abandoned.
                        elapsed_h = int((now - last_seen_alive) // 3600)
                        send_fn("⚠️ **%s** — `/goal` slučka dokončila celý "
                                "backlog a je ticho už vyše %d hodín, no "
                                "medzitým pribudla nová práca; automaticky "
                                "ju znova NEspúšťam (rešpektujem, že si ju "
                                "mohol zámerne nechať tak) — spusti ju "
                                "prosím ručne, ak má pokračovať (%s)."
                                % (project_label(cwd), elapsed_h, loc),
                                owner=stream_redirect(pane_owner(pid, run)) or None,
                                dedup_key="goalstaleachieved:%s:%s:%d"
                                          % (sid, rec.get("hash") or rec.get("mts"),
                                             int(last_seen_alive or 0)),
                                dry_run=dry_run)
                continue
            if backlog_open is True:
                logs.append("FALSE-ACHIEVED (goal-rearm) %s -> %s but the "
                            "repo's backlog is not, re-arming"
                            % (loc, "claimed backlog empty" if claim_present
                               else "no fresh claim (%s), but this loop's "
                                    "own tracked goal is backlog-shaped"
                                    % claim_reason))
                # #160-review-style finding 🔴F1 (this ticket's own review,
                # proven live) — a stale CACHED `True` re-arms a loop whose
                # backlog THIS RE-ARM just emptied: the loop closes the
                # remaining ticket(s) and claims empty again within the
                # SAME 10-minute cache window, and job 20 reads the stale
                # `True` again -> a SECOND spurious re-arm -> the two-attempt
                # cap is now exhausted by re-arms that were never wrong,
                # and the NEXT achieved sweep fires the give-up ping at a
                # loop that finished correctly every time. Dropping the
                # cwd's own cache entry the moment `True` is ACTED ON (never
                # just read) forces the next achieved-with-claim sweep for
                # this repo onto a fresh read, so a re-arm this one caused
                # can never be double-counted as evidence of a second one.
                cache = state.get("backlog_cache")
                if isinstance(cache, dict):
                    cache.pop(cwd, None)
                # falls through -- no `continue` here on purpose
            else:
                reason = ("backlog verified empty" if backlog_open is False
                          else "backlog unverifiable")
                logs.append("skip goal-achieved (goal-rearm) %s -> loop finished "
                            "legitimately (%s)" % (loc, reason))
                continue
        payload = rec.get("payload") or ""
        if not payload or "\n" in payload or len(payload) > GOAL_REARM_MAX_PAYLOAD:
            logs.append("skip unusable-payload (goal-rearm) %s (%d chars)"
                        % (loc, len(payload)))
            continue
        h = _hash(payload)
        if rec.get("hash") != h or (now - rec.get("first", now)) > streak_s:
            # #322 — a genuinely NEW streak also gets a fresh give-up-reset
            # budget; otherwise a spent `giveup_resets` from a PRIOR streak
            # would silently disable the reset for a session's whole life.
            # `slot_stuck_n` (2nd adversarial-review MAJOR-1) clears the
            # same way — a stuck-slot streak from an OLD payload must never
            # carry into a brand-new one's own consecutive count.
            rec.update({"hash": h, "n": 0, "first": now, "pinged": False,
                       "gaveup_at": None, "giveup_resets": 0,
                       "slot_stuck_n": 0})
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
                rec["gaveup_at"] = now     # #322 — the give-up state's own
                                            # anchor for the reachable reset
                                            # below; never touched by anything
                                            # the give-up state itself blocks
                _save()
                if send_fn is not None and not dry_run:
                    from notify import stream_redirect  # #212: STREAM_NOTIFY_OWNER-aware
                    send_fn("⚠️ **%s** — `/goal` slučka ticho zanikla (v pätičke "
                            "už nie je `◎ /goal`) a automatické prearmovanie sa "
                            "nechytilo ani po %d pokusoch (%s). Prearmuj ju "
                            "prosím ručne — dovtedy nič nebeží."
                            % (project_label(cwd), max_attempts, loc),
                            owner=stream_redirect(pane_owner(pid, run)) or None,
                            # #160 defect 3 — `h` (the payload hash) alone is
                            # STABLE across a streak reset (same goal, same
                            # text), so the notify layer's dedup silently
                            # absorbed every GAVE UP after the first one —
                            # live-confirmed (a hard-debug consult found
                            # gatekeeper's own reset firing correctly twice
                            # more, each producing a real GAVE UP that never
                            # reached the phone). Folding in this EPISODE's
                            # own `first` timestamp (stamped fresh on every
                            # reset) makes each episode's give-up genuinely
                            # distinct, so a LATER episode's ping is no
                            # longer mistaken for a repeat of the first.
                            dedup_key="goalrearm:%s:%s:%d"
                                      % (sid, h, int(rec.get("first") or 0)),
                            dry_run=dry_run)
                logs.append("GAVE UP (goal-rearm) %s after %d attempts"
                            % (loc, max_attempts))
                continue
            # #322 — a REACHABLE exit condition for `skip gave-up`. It used to
            # be permanent: nothing ever cleared `pinged`/`n` except the blind
            # `GOAL_REARM_STREAK_S` (2h) full-state reset above, tied to
            # elapsed time only, never to the pane actually recovering.
            #
            # REOPENED (adversarial-review MAJOR-1): the first cut here
            # re-stamped `first` on every reset — but `first` feeds the GAVE
            # UP ping's own `dedup_key` above (deliberately, per #160
            # defect 3's own comment: the payload hash `h` alone is STABLE
            # across a reset, so folding in `first` is what makes each
            # EPISODE's ping genuinely distinct from a repeat). Re-stamping
            # it on every retry therefore minted a genuinely NEW dedup key
            # every time, so the notify layer could never recognise a repeat
            # give-up as a repeat — measured live-replay: 10 pings / 152
            # multi-KB `/goal` submissions into a live pane over 3h on a
            # genuinely-still-broken IDLE pane (idleness is the STEADY STATE
            # there, so "idle + elapsed" alone reduces to a bare 15-minute
            # timer), against 2 pings / 4 submissions before this ticket.
            # Two changes close it: `first` is NEVER touched by this reset
            # (so a repeat give-up within the SAME streak shares the FIRST
            # give-up's dedup key and the notify layer's own dedup absorbs
            # it, exactly as #160 intended), and the reset is BOUNDED to
            # `GOAL_REARM_GIVEUP_MAX_RESETS` per streak — converting
            # "retries every 15 min until the blind 2h streak reset" into
            # "one extra try, then the SAME permanent skip until that 2h
            # reset", the only behavioural difference from before this
            # ticket for a pane that is genuinely, permanently unable to
            # arm. `giveup_resets`/`gaveup_at` are cleared by the SAME
            # streak-reset block above that already clears `n`/`pinged`, so
            # a genuinely new streak starts with a fresh reset budget.
            #
            # `isinstance(gaveup_at, (int, float))` (adversarial-review T1):
            # a corrupted/hand-edited state file could otherwise raise on
            # `now - gaveup_at` and kill job 20 for every pane, every sweep.
            # A future-dated `gaveup_at` (T2, clock skew) makes the elapsed
            # delta negative, which already fails the `>=` check — the
            # fail-safe direction needs no extra clamp.
            #
            # Reset fires ONLY when the give-up has held long enough that a
            # genuinely-still-broken pane won't ping-storm on a retry, the
            # reset budget is not yet spent, AND the pane is PROVABLY idle
            # RIGHT NOW — read fresh every sweep from `captured`, entirely
            # independent of anything this give-up state itself blocks (the
            # #134 test: name the event that releases the guard, then prove
            # it's reachable without the guarded action).
            #
            # Known, deliberate residual (adversarial-review MAJOR-2):
            # `pane_at_idle_prompt` requires a BARE box. A pane whose own
            # typed-but-never-confirmed `/goal` is still sitting in the
            # input line (the `_await_typed(want=True)` timeout path, a few
            # lines below) never reads as idle, so this reset never engages
            # for that population — it self-heals only via the existing 2h
            # streak window. Extending the idle gate to accept that shape
            # would need its own delivery-recovery design (stash-style undo
            # before a fresh type); out of this ticket's scope.
            #
            # `dry_run` (adversarial-review MINOR-1): the state mutation
            # only happens for a real sweep — a `--dry-run` diagnostic must
            # never consume the one-way-door reset budget or clear a
            # genuine suppression with nothing actually sent.
            gaveup_at = rec.get("gaveup_at")
            resets_used = rec.get("giveup_resets", 0)
            # #322 REOPENED (2nd adversarial review, MINOR-1): `.get(...,
            # 0)` only supplies the default when the KEY is absent — a
            # PRESENT-but-corrupt value (the same hand-edited/pruned state
            # file class T1 guarded `gaveup_at` against, #232) reaches the
            # `<` comparison below and raises `TypeError` uncaught, killing
            # job 20 for every pane, every sweep. Symmetric guard, same
            # fail-safe direction as `gaveup_at`'s own isinstance check:
            # never guess, never crash — an unreadable value is simply
            # never "ready".
            if not isinstance(resets_used, int):
                resets_used = GOAL_REARM_GIVEUP_MAX_RESETS  # treat as spent
            ready = (isinstance(gaveup_at, (int, float))
                    and (now - gaveup_at) >= GOAL_REARM_GIVEUP_RESET_S
                    and resets_used < GOAL_REARM_GIVEUP_MAX_RESETS
                    and pane_at_idle_prompt(captured))
            if ready and not dry_run:
                rec.update({"n": 0, "pinged": False, "gaveup_at": None,
                           "giveup_resets": resets_used + 1})
                _save()
                logs.append("RESET (goal-rearm) %s -> pane idle %ds after "
                            "giving up (%d/%d resets used), retrying"
                            % (loc, int(now - gaveup_at), resets_used + 1,
                               GOAL_REARM_GIVEUP_MAX_RESETS))
                # falls through — attempt delivery again this SAME sweep
            elif ready and dry_run:
                logs.append("RESET (goal-rearm) %s -> would retry "
                            "(dry-run, not applied)" % loc)
                continue
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
            ok = _send_goal_verified(pid, text, run, captured=captured,
                                     sleep_fn=sleep_fn, logs=dlogs)
            tag = "goal-rearm"
        if not ok and dlogs and dlogs[-1] in _GOAL_REARM_TRANSIENT_REASONS:
            # #101 — deliver_with_stash bailed BEFORE sending a single
            # keystroke (still holding the foreign draft, mid-turn, or
            # another stash already in flight): the pane is alive and well,
            # it simply wasn't deliverable at THIS instant. Never count it
            # toward the permanent give-up cap — retried next sweep, same as
            # every other pre-send skip above, no different from "not-idle".
            #
            # #322 REOPENED (2nd adversarial-review MAJOR-1) — "stash-abort:
            # slot occupied" is a SPECIAL case of this. `deliver_with_stash`'s
            # own collapsed-paste early return cannot safely pop a PARKED
            # draft back (the box then holds an unproven collapsed-paste
            # hint, not `text` — no safe backspace count exists), so it
            # leaves the single stash slot occupied with ZERO further
            # keystrokes. Every LATER sweep's own entry precondition then
            # refuses with this SAME "slot occupied" reason — correctly
            # transient for a genuinely FOREIGN occupant (self-resolves
            # within a sweep or two once whoever else is done) but, for a
            # slot WE stranded, would otherwise mean `n` never advances,
            # GAVE UP never fires, and this round's own reachable
            # give-up-reset is unreachable for this pane — silently,
            # forever (the #134 "a suppression needs a reachable exit"
            # lesson, one level down). Bound a CONSECUTIVE run of this ONE
            # specific reason: past `GOAL_REARM_SLOT_STUCK_MAX`, stop
            # treating it as transient so the pane still eventually counts
            # a real failure and can reach GAVE UP — a genuinely transient
            # foreign occupant never gets close to the bound.
            if dlogs[-1] == "stash-abort: slot occupied":
                stuck = rec.get("slot_stuck_n", 0) + 1
                if stuck < GOAL_REARM_SLOT_STUCK_MAX:
                    rec["slot_stuck_n"] = stuck
                    _save()
                    logs.append("SKIP-TRANSIENT (%s) %s -> %s (%d/%d), "
                                "retrying next sweep"
                                % (tag, loc, dlogs[-1], stuck,
                                   GOAL_REARM_SLOT_STUCK_MAX))
                    continue
                rec["slot_stuck_n"] = 0
                # falls through — counted as a real failure below, same as
                # any other non-transient FAIL
            else:
                logs.append("SKIP-TRANSIENT (%s) %s -> %s, retrying next sweep"
                            % (tag, loc, dlogs[-1]))
                continue
        elif rec.get("slot_stuck_n"):
            # any outcome OTHER than a fresh "slot occupied" transient skip
            # ends the consecutive streak — a genuinely resolved pane (ok)
            # or a different failure must not inherit a stale stuck-count.
            rec["slot_stuck_n"] = 0
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


# --------------------------------------------------------------------------- #
# Box/account IDENTITY for the usage alert body (#212) — resolved at CALL
# TIME from module-global paths (the `_USAGE_CACHE_PATH` convention: a
# def-time default would bind the real `~/.claude.json`, so tests patch the
# global instead of passing a path through 3 call frames).
# --------------------------------------------------------------------------- #

_CLAUDE_JSON_PATH = os.path.expanduser("~/.claude.json")


def _account_email(path=None):
    """The Claude account's login email (`~/.claude.json` ->
    oauthAccount.emailAddress — the SAME field statusbar.account_email_segment
    reads), or "" on any missing/malformed input. Never raises."""
    try:
        with open(path or _CLAUDE_JSON_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            return ""
        email = (d.get("oauthAccount") or {}).get("emailAddress")
        return email if isinstance(email, str) else ""
    except Exception:
        return ""


def _local_account():
    """This box's own unix account (e.g. 'montalu') — distinct from the
    Discord OWNER a message ends up addressed to (`notify.stream_redirect`
    may redirect a stream persona to a different real person's thread)."""
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "") or ""


def _box_hostname():
    try:
        return os.uname().nodename
    except Exception:
        return ""


def _reset_bucket(resets_at):
    """Normalize an ISO-8601 `resets_at` to a stable dedup key by ROUNDING
    (never truncating) to the nearest whole minute in UTC — for use as a
    dedup key. Live-verified (airuleset#212): two `fetch_usage()` calls
    seconds apart for the SAME weekly window returned DIFFERENT `resets_at`
    strings (sub-second jitter) — comparing the raw string (the pre-fix
    `check_usage` dedup) therefore NEVER matched, and the usage alert re-fired
    every poll interval instead of once per window (11 duplicate Discord
    pings observed live inside 2.5h).

    ROUND, not truncate (adversarial-review finding): the jitter is centered
    ON the reset BOUNDARY itself (typically a whole hour), not offset from
    it — a real 456-sample replay of `~/.claude/burn-history/fleet.jsonl`
    showed every window straddles its own boundary (samples land on BOTH
    sides, e.g. some at `HH:59:59.xx`, some at `(HH+1):00:00.xx`). A bare
    TRUNCATE-to-minute (the first version of this fix) therefore still
    split roughly one poll in five into the WRONG bucket (43 of 231 replayed
    real polls for one window, instead of 1). Adding 30s before truncating
    moves the bucket boundary to `:30`, comfortably clear of the observed
    ~1s jitter, so both sides of a real straddle round to the SAME minute.
    `.astimezone(timezone.utc)` additionally stops two DIFFERENT instants at
    different UTC offsets from colliding on the same rounded string
    (unreachable today — the endpoint always returns `+00:00` — free to
    close while already touching this line).

    Falls back to a STABLE, never-time-varying sentinel — `"raw:<value>"`,
    NEVER bare `None` — on any missing/unparseable input: `check_usage`
    uses `None` in `state["usage"]["alerted_window"]` to mean "not currently
    alerted for any window" (both the fresh-state default and the
    below-threshold re-arm), so returning `None` here for a genuinely
    missing `resets_at` would make the very FIRST real alert compare
    `None == None` and silently never fire. A fixed sentinel per distinct
    bad input still fires once and then correctly dedupes on repeat polls
    with the same missing/malformed value."""
    try:
        from datetime import datetime, timedelta, timezone
        dt = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
        dt = dt.astimezone(timezone.utc) + timedelta(seconds=30)
        return dt.strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return "raw:%s" % (resets_at,)


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
    """Best-effort: persist {ts, account_email, windows} so the statusline
    renders a per-model window without hitting the 429-prone endpoint, AND
    (#212) so fleet usage reporting can tell WHICH Anthropic subscription a
    box's percentages belong to — the original gap this cache existed
    without: "impossible to reconcile without knowing the box→account
    mapping". `account_email` is the SAME `~/.claude.json` field
    `statusbar.account_email_segment` already renders; "" when unreadable
    (never blocks the write). Never raises. `path` defaults to the module
    global resolved AT CALL TIME (so tests can patch _USAGE_CACHE_PATH to
    a tmp file — a def-time default would bind the real ~/.claude path and
    clobber the user's live cache during the suite)."""
    path = path or _USAGE_CACHE_PATH
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ts": int(now), "account_email": _account_email(),
                       "windows": usage_windows(usage)}, f)
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
    state['usage']; returns a log line or ''. Best-effort (never raises).

    The alert body carries this box's ACCOUNT IDENTITY (airuleset#212: a bare
    "⚠️ Tokeny — týždenný limit na 99%" is undecodable on a phone with no
    terminal context — WHICH account, WHICH box?) — the account email
    (`_account_email()`), this box's hostname/unix-account
    (`_box_hostname()`/`_local_account()`), and the Discord `owner` the alert
    is actually addressed to. `owner` is trusted AS GIVEN by the caller — by
    the time `run_once()` calls this, it has already been passed through
    `notify.stream_redirect()`, so a stream persona (montalu/simap/…) shows
    the REAL person it was routed to, not its own unix account name.

    Dedup keys off `_reset_bucket(resets_at)`, never the raw `resets_at`
    string — the raw string carries SUB-SECOND jitter from the Anthropic
    API (live-verified on montalu@subdev: two polls of the SAME weekly
    window, 3s apart, returned two DIFFERENT `resets_at` strings), which
    used to defeat the "already alerted for THIS window" check on every
    single 15-minute poll — 11 duplicate Discord pings observed live across
    one incident window (98%→99%, ~05:15–07:47)."""
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
    bucket = _reset_bucket(resets_at)
    if pct < threshold:
        u["alerted_window"] = None         # back below threshold → re-arm the dedup
        state["usage"] = u
        return ""
    if u.get("alerted_window") == bucket:
        return ""                          # already alerted for THIS reset window
    u["alerted_window"] = bucket
    state["usage"] = u
    send_fn("⚠️ **Tokeny — %s na %d%%**\n"
            "> Účet: %s · Box: %s/%s → adresát: %s.\n"
            "> Práca sa môže čoskoro zastaviť "
            "(vyčerpaný týždenný limit). Reset: %s."
            % (label, int(pct), _account_email() or "?", _box_hostname() or "?",
               _local_account() or "?", owner or "?", _human_reset(resets_at)),
            owner=owner, dedup_key="usage:%s:%d" % (bucket, int(pct)), dry_run=dry_run)
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
    run = run or _default_run
    projects_dir = projects_dir or PROJECTS_DIR
    for _pid, cwd in list_claude_panes(run):
        tinfo = find_active_transcript(projects_dir, cwd)
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


# --------------------------------------------------------------------------- #
# Jobs 27/28 (#137, 2026-07-28). Both close the SAME observation gap: camera-
# box's +101 net-open drift ran two weeks before the user noticed by feel, and
# the merge deadlock behind most of it (origin/main frozen 2026-07-11) ran 15
# days before job 24 (#138) existed to catch it. Job 24 needed a LIVE PANE in
# the repo to fire at all — these two sweep EVERY locally-cloned repo on the
# box on a bounded cadence, independent of whether any session happens to be
# open in it right now, so a repo nobody is actively looking at still gets
# checked. #138's own corrected lesson applies here from the start: the
# corpus is `$HOME`, never a guessed project directory, and any age-only
# window is bounded on BOTH ends (a repo abandoned in 2019 is not a "stopped
# receiving" repo, it never was a delivery target at all).
#
# Repo discovery is INJECTED (`repo_roots`), never done by the jobs
# themselves — same "wired = on" convention as `delivery_probe`/`fleet_fetch`
# and the same reason: a unit test controls its own repo set explicitly, and
# `cmd_watchdog` (airuleset.py) owns the real `find $HOME -maxdepth N -name
# .git` sweep, gated by cadence so it costs the network once an hour, not
# once a minute.
# --------------------------------------------------------------------------- #

MANAGED_SWEEP_INTERVAL_S = 3600      # run at most once an hour; env AIRULESET_MANAGED_SWEEP_S


def discover_managed_repos(home=None, max_depth=4, run=None):
    """Every `.git` directory under `home` (default `$HOME`), `max_depth`
    levels deep, EXCLUDING dependency/build noise -- the real corpus for any
    job that must sweep "every repo on this box", per #138's own correction
    (the intuitive `~/devel` guess silently missed a real repo). Returns a
    sorted list of repo ROOT paths (parent of `.git`), deduped. Best-effort:
    an unreadable subtree is skipped, never raised."""
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    skip_names = {"node_modules", ".cache", ".local", "venv", ".venv",
                  "__pycache__", ".npm", ".cargo", "target", "dist", "build"}
    roots = set()
    base_depth = str(home).rstrip("/").count("/")
    for dirpath, dirnames, _filenames in os.walk(home, topdown=True):
        depth = dirpath.rstrip("/").count("/") - base_depth
        if depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in skip_names]
        if ".git" in dirnames:
            roots.add(dirpath)
            dirnames.remove(".git")   # never descend into .git itself
    return sorted(roots)


def _repo_label(root, git_run=None):
    """The repo's canonical NAME for logging/dedup -- from `origin`, never
    the directory basename (a checkout can be renamed locally; #134's own
    lesson, reused here)."""
    url = _git_first_line(root, ["remote", "get-url", "origin"], git_run)
    if url:
        m = re.search(r'[:/]([^/]+/[^/]+?)(\.git)?$', url)
        if m:
            return m.group(1)
    return os.path.basename(str(root).rstrip("/"))


def _sweep_due(state, key, now, interval):
    """True once per `interval`, tracked in `state[key]`. Shared cadence gate
    for jobs 27/28 -- neither needs to run every 60s poll; both cost a
    network round trip (gh / git fetch) per repo."""
    last = state.get(key)
    elapsed = None
    if last is not None:
        try:
            elapsed = now - float(last)
        except (TypeError, ValueError):
            elapsed = None    # unusable stamp -- treat as "due", never crash
    if elapsed is not None and elapsed < interval:
        return False
    return True


# #172 fix (2): dev1 alone hosts 40+ repos -- sweeping ALL of them every hour,
# each costing a `git fetch` (job 28) or two `gh issue list` calls (job 27),
# is exactly what blew the 120s TimeoutStartSec budget in the first place.
# Bound the batch and rotate through the full repo list via a cursor kept in
# state, so coverage still reaches every repo over successive hourly sweeps
# instead of either "all of them, maybe killed" or "arbitrarily few forever".
#
# The real per-repo stale-data bound this buys is `interval *
# ceil(n_repos / batch)` -- NOT "one hour of drift data" (the #172 fix's own
# original commit message, docstring and playbook entry all overclaimed
# this; corrected in the #172 reopened pass). At the default batch of 3 on
# a 40-repo box that's `ceil(40/3) = 14` hourly sweeps, i.e. up to ~14h
# before a given repo's drift/stuck-main state is re-measured -- a real
# trade, not the number an operator would reason from if only told "an
# hour". Batching cuts the REPO COUNT examined per sweep ~13x (40/3), which
# is what actually prevents the livelock; it is not a time-based cut and it
# does NOT make a killed sweep impossible: jobs 27+28 alone still cost up
# to ~105s worst case per sweep at the default batch (job 27: up to 2
# `gh issue list` calls at `AIRULESET_ISSUE_FETCH_TIMEOUT`-ish 10s each per
# repo x 3 repos = ~60s; job 28: one `git fetch` at 15s per repo x 3 repos =
# ~45s) -- comfortably under the 120s `TimeoutStartSec` on its own, but not
# a hard guarantee once jobs 24/25's own (also network-bound) per-pane
# probes ahead of them in the same sweep are accounted for.
REPO_SWEEP_BATCH_MAX = 3          # env AIRULESET_REPO_SWEEP_BATCH

# #172 (reopened) smaller item: a dedup-memory entry (job 27's `net_drift` /
# job 28's `stuck_main`) used to be kept FOREVER once a repo stopped
# appearing in `repo_roots()` at all (deleted, renamed, or moved past
# `discover_managed_repos`' max_depth) -- "not touched this sweep" is true
# both for a repo merely sitting out the round-robin batch (must survive)
# and for a repo that is simply gone (should eventually be forgotten), and
# the old pruning filter could not tell them apart. Age entries out instead
# of a touched/live check alone -- comfortably longer than one full
# rotation (~14h at the current default) so an ordinary sit-out is never
# mistaken for abandonment.
DEDUP_MEMORY_MAX_AGE_S = 30 * 86400   # 30 days


def _repo_sweep_batch(repos, state, cursor_key, max_repos=None):
    """Round-robin slice of `repos` (already deduped) bounded to `max_repos`
    per sweep. `repos` MUST be the same stable order every call (the caller
    passes a sorted list) or the cursor drifts. Returns the batch AND does
    NOT mutate `repos` itself -- only `state[cursor_key]` advances.

    #172 (reopened) finding 2: `max_repos <= 0` (the obvious spelling for
    "disable batching" -- `AIRULESET_REPO_SWEEP_BATCH=0`, or any negative
    value) must NEVER silently sweep the FULL repo list -- that re-arms the
    exact pathological cost this cap exists to prevent, via the knob an
    operator is most likely to reach for. Clamp to the documented default
    instead of trusting the value verbatim.

    #172 (reopened) smaller item: the short-list fast path (batch size >=
    repo count, whether from a small `max_repos` or a transient short
    `repos` list) must NOT reset the cursor to 0. `discover_managed_repos`
    is explicitly best-effort -- a mount hiccup or a permissions blip that
    makes ONE sweep see only 2 repos instead of 40 must not rewind the
    whole rotation once the real count comes back, or the tail of the list
    is starved another full rotation for nothing."""
    if max_repos is None:
        try:
            max_repos = int(os.environ.get("AIRULESET_REPO_SWEEP_BATCH",
                                           REPO_SWEEP_BATCH_MAX))
        except ValueError:
            max_repos = REPO_SWEEP_BATCH_MAX
    if max_repos <= 0:
        max_repos = REPO_SWEEP_BATCH_MAX
    n = len(repos)
    if n == 0 or max_repos >= n:
        # Leave state[cursor_key] untouched -- see the finding-2/short-list
        # docstring note above. Nothing to rotate through when the whole
        # list fits in one batch anyway.
        return list(repos)
    try:
        start = int(state.get(cursor_key, 0) or 0) % n
    except (TypeError, ValueError):
        start = 0
    end = start + max_repos
    if end <= n:
        batch = repos[start:end]
    else:
        batch = repos[start:n] + repos[0:end - n]
    state[cursor_key] = end % n
    return batch


# --------------------------------------------------------------------------- #
# Job 27 — NET-ISSUE-DRIFT ALARM (#137). Per managed repo, the trailing-7-day
# opened-minus-closed count via `gh`. camera-box's own +101 over 21 days is
# ~+34/week at its worst window -- this would have pinged around 2026-07-14,
# instead of the user noticing by feel two weeks later. Gated on
# `issue_counts_fetch` (the "wired = on" convention): the real callable does
# the `gh issue list --search` round trip; a unit test injects a fake so the
# job itself never shells out.
# --------------------------------------------------------------------------- #

NET_DRIFT_WINDOW_S = 7 * 86400
NET_DRIFT_THRESHOLD = 10          # net > this pings; env AIRULESET_NET_DRIFT_THRESHOLD
NET_DRIFT_REPING_S = 86400        # once a day while it persists


def net_drift_alarm(now, state, send_fn=None, dry_run=False, repo_roots=None,
                    issue_counts_fetch=None, git_run=None, threshold=None,
                    window=NET_DRIFT_WINDOW_S, reping=NET_DRIFT_REPING_S,
                    interval=MANAGED_SWEEP_INTERVAL_S, persist=None,
                    max_repos=None):
    """Job 27 -- see the section comment. `issue_counts_fetch(repo_label,
    window_s) -> (opened, closed) | None` -- None means unmeasurable (no gh
    auth, rate-limited, repo not on GitHub) and is never treated as a stall.

    #172: `persist` (the caller's save-state closure, same shape jobs 8/11
    already use) is invoked BEFORE any per-repo network call leaves this
    process -- the live incident: systemd's TimeoutStartSec=120 killed the
    run mid-sweep, the cadence marker had only ever been set in run_once's
    OWN memory, and the next 60s tick re-attempted the identical 40-repo
    sweep, was killed again, forever. The repo list is also BOUNDED per
    sweep (`_repo_sweep_batch`) so a box with many repos doesn't try to
    fetch all of them in one 120s-budgeted run in the first place -- see
    `REPO_SWEEP_BATCH_MAX`'s own comment for the real (not "one hour")
    stale-data bound this buys.

    #172 (reopened) finding 4: the cadence marker is now persisted BEFORE
    `repo_roots()` even runs (an `os.walk($HOME)`, not free) -- a kill
    inside the walk itself used to lose the marker exactly like a kill
    inside the per-repo loop did. The cursor advance is persisted again
    once the batch is drawn, still before the first `gh` call.

    #172 (reopened) finding 3: dedup memory (`state['net_drift']`) is now
    the SAME dict object as the caller's `state`, updated AND persisted the
    moment a ping fires -- mirroring jobs 8/11's own '# dedup memory BEFORE
    the ping' shape, which the original #172 fix copied only half of (the
    cadence stamp, not the per-repo dedup write). Before this, a kill
    between two pings in the same sweep lost the FIRST repo's dedup entry
    entirely, so it re-pinged on its next rotation -- a duplicate alert
    across `notify.send`'s own daily dedup bucket."""
    if issue_counts_fetch is None:
        return []
    if threshold is None:
        try:
            threshold = int(os.environ.get("AIRULESET_NET_DRIFT_THRESHOLD",
                                           NET_DRIFT_THRESHOLD))
        except ValueError:
            threshold = NET_DRIFT_THRESHOLD
    persist = persist or (lambda: None)
    logs = []
    if not _sweep_due(state, "net_drift_last_sweep", now, interval):
        return logs
    if not dry_run:
        # #172 F4: stamp + persist BEFORE repo_roots() (the os.walk) runs --
        # not just before the per-repo network loop.
        state["net_drift_last_sweep"] = now
        persist()
    repos = sorted(set(repo_roots() if callable(repo_roots) else (repo_roots or [])))
    if dry_run:
        # dry-run must not mutate persistent state -- peek the batch on a
        # throwaway copy of state so the real cursor never advances.
        batch = _repo_sweep_batch(repos, dict(state), "net_drift_cursor", max_repos)
    else:
        batch = _repo_sweep_batch(repos, state, "net_drift_cursor", max_repos)
        persist()      # cursor advance also survives a kill BEFORE a
                        # single per-repo `gh` call leaves this process
    touched = set()
    seen = dict(state.get("net_drift") or {})
    if not dry_run:
        state["net_drift"] = seen     # #172 F3: same dict from here on, so
                                       # a per-repo write below is already
                                       # visible in `state` for persist()
    live = set()
    for root in batch:
        label = _repo_label(root, git_run)
        touched.add(label)
        try:
            counts = issue_counts_fetch(label, window)
        except Exception as exc:
            counts = None
            logs.append("net-drift fetch-error %s: %r" % (label, exc))
        if not counts:
            continue
        opened, closed = counts
        net = opened - closed
        logs.append("net-drift %s opened=%d closed=%d net=%+d"
                    % (label, opened, closed, net))
        if net <= threshold:
            seen.pop(label, None)
            continue
        live.add(label)
        prev = seen.get(label) or {}
        pinged = prev.get("pinged_ts")
        if dry_run or send_fn is None or (
                pinged is not None and now - float(pinged) < reping):
            continue
        seen[label] = {"pinged_ts": now}
        if not dry_run:
            persist()      # #172 F3: dedup memory BEFORE the ping
        status = send_fn(
            "\U0001f4c8 **%s** -- backlog rastie: +%d ticketov za posledny "
            "tyzden\n"
            "> Za poslednych 7 dni pribudlo %d novych a zavrelo sa len %d -- "
            "backlog rastie rychlejsie, ako sa stiha spracovavat."
            % (label, net, opened, closed),
            dedup_key="net-drift:%s:%d" % (label, int(now // reping)),
            dry_run=dry_run)
        logs.append("net-drift PING %s -> %s" % (label, status))
    if not dry_run:
        # keep dedup memory for every repo NOT touched THIS sweep (the
        # round-robin batch means most repos sit out most sweeps) -- only
        # drop/refresh entries for repos actually re-measured just now --
        # AND age out anything that hasn't been refreshed in
        # DEDUP_MEMORY_MAX_AGE_S regardless of touched/live, so a repo that
        # simply stops existing (deleted, renamed, moved past max_depth)
        # doesn't keep its dedup entry forever (#172 reopened smaller item).
        state["net_drift"] = {
            k: v for k, v in seen.items()
            if (k in live or k not in touched)
            and (now - float(v.get("pinged_ts", now)) < DEDUP_MEMORY_MAX_AGE_S)}
    return logs


# --------------------------------------------------------------------------- #
# Job 28 — STUCK-MAIN SWEEP (#137). Per managed repo, purely local git (no
# `gh` call, no auth needed): the base branch (`origin/HEAD`, same resolver
# job 24 uses) has not moved in `age_threshold`, while the checked-out
# branch carries more than `ahead_threshold` commits not reachable from it.
# This is job 24's OWN measurement (`delivery_state`/`_delivery_stalled`),
# reused deliberately rather than reimplemented -- the difference is scope:
# job 24 only ever sees a repo with a LIVE PANE open in it right now; this
# sweeps every repo on the box on its own cadence, so a repo nobody has a
# session open in still gets checked. Bounded on both ends, per #138's own
# fix (`DELIVERY_STALL_MAX_S`) -- reused here too, so a repo that simply
# never merges anywhere (an abandoned fork) does not alarm forever.
# --------------------------------------------------------------------------- #

STUCK_MAIN_AGE_S = 5 * 86400          # env AIRULESET_STUCK_MAIN_AGE_S
STUCK_MAIN_AHEAD_MIN = 20             # env AIRULESET_STUCK_MAIN_AHEAD
STUCK_MAIN_REPING_S = 86400


def stuck_main_sweep(now, state, send_fn=None, dry_run=False, repo_roots=None,
                     git_run=None, git_fetch=None, age_threshold=None,
                     ahead_threshold=None, reping=STUCK_MAIN_REPING_S,
                     interval=MANAGED_SWEEP_INTERVAL_S, persist=None,
                     max_repos=None):
    """Job 28 -- see the section comment. `git_fetch(root)` is called (best-
    effort before this pass -- see the #172 reopened note below) before
    reading refs, since no live session may have fetched this repo recently
    -- injected so a test never shells a real network fetch. `git_fetch=None`
    skips the fetch entirely (a test working with fixture repos that have
    no real remote).

    #172: `persist` (same shape jobs 8/11/27 already use) is invoked BEFORE
    any per-repo `git fetch` leaves this process, and the repo list is
    BOUNDED per sweep (`_repo_sweep_batch`) -- see net_drift_alarm's
    docstring for the full incident this fixes (a systemd
    TimeoutStartSec=120 kill mid-sweep, with the cadence marker never
    reaching disk, re-attempting the identical sweep forever) and
    `REPO_SWEEP_BATCH_MAX`'s own comment for the real (not "one hour")
    stale-data bound the batching buys.

    #172 (reopened) finding 5: a git_fetch FAILURE now SKIPS the repo for
    this sweep rather than falling through to `delivery_state()` on
    whatever refs are already on disk. A repo not fetched in days has a
    stale `origin/<base>` -- measuring on it inflates both `delivery_age`
    and `undelivered`, which is exactly the stuck-main signature this job
    pings on. A repo merely behind a slow link must never read as stuck.

    #172 (reopened) finding 4: the cadence marker is persisted BEFORE
    `repo_roots()` runs (see net_drift_alarm's matching note); the cursor
    advance is persisted again once the batch is drawn.

    #172 (reopened) finding 3: dedup memory (`state['stuck_main']`) is the
    SAME dict object as the caller's `state` from here on, written and
    persisted the moment a ping fires -- mirroring jobs 8/11's own shape,
    which the original #172 fix only half-copied (see net_drift_alarm's
    matching note for the full consequence of the gap)."""
    if age_threshold is None:
        try:
            age_threshold = int(os.environ.get("AIRULESET_STUCK_MAIN_AGE_S",
                                               STUCK_MAIN_AGE_S))
        except ValueError:
            age_threshold = STUCK_MAIN_AGE_S
    if ahead_threshold is None:
        try:
            ahead_threshold = int(os.environ.get("AIRULESET_STUCK_MAIN_AHEAD",
                                                  STUCK_MAIN_AHEAD_MIN))
        except ValueError:
            ahead_threshold = STUCK_MAIN_AHEAD_MIN
    persist = persist or (lambda: None)
    logs = []
    if not _sweep_due(state, "stuck_main_last_sweep", now, interval):
        return logs
    if not dry_run:
        # #172 F4: stamp + persist BEFORE repo_roots() (the os.walk) runs.
        state["stuck_main_last_sweep"] = now
        persist()
    repos = sorted(set(repo_roots() if callable(repo_roots) else (repo_roots or [])))
    if dry_run:
        # dry-run must not mutate persistent state -- peek the batch on a
        # throwaway copy of state so the real cursor never advances.
        batch = _repo_sweep_batch(repos, dict(state), "stuck_main_cursor", max_repos)
    else:
        batch = _repo_sweep_batch(repos, state, "stuck_main_cursor", max_repos)
        persist()      # cursor advance also survives a kill BEFORE a
                        # single `git fetch` leaves this process
    touched = set()
    seen = dict(state.get("stuck_main") or {})
    if not dry_run:
        state["stuck_main"] = seen    # #172 F3: same dict from here on
    live = set()
    for root in batch:
        label = _repo_label(root, git_run)
        touched.add(label)
        if git_fetch is not None:
            try:
                git_fetch(root)
            except Exception as exc:
                logs.append("stuck-main git-fetch-error %s: %r" % (root, exc))
                continue        # #172 F5: refs may be STALE -- never
                                 # measure on them, skip this repo entirely
        st = delivery_state(root, now, git_run=git_run)
        if st is None:
            continue
        stalled = (st["undelivered"] >= ahead_threshold
                   and age_threshold <= st["delivery_age"] <= DELIVERY_STALL_MAX_S)
        logs.append("stuck-main %s undelivered=%d delivery_age=%ds base=%s"
                    % (label, st["undelivered"], int(st["delivery_age"]),
                       st["base"]))
        if not stalled:
            seen.pop(label, None)
            continue
        live.add(label)
        prev = seen.get(label) or {}
        pinged = prev.get("pinged_ts")
        if dry_run or send_fn is None or (
                pinged is not None and now - float(pinged) < reping):
            continue
        seen[label] = {"pinged_ts": now}
        if not dry_run:
            persist()      # #172 F3: dedup memory BEFORE the ping
        days = int(st["delivery_age"] // 86400)
        status = send_fn(
            "\U0001f512 **%s** -- vetva %s stoji %d dni, %d commitov caka na zluenie\n"
            "> Praca sa hromadi na pracovnej vetve, ale do %s sa uz %d dni nic "
            "nezluilo -- skontroluj, ci nie je zablokovany merge/PR."
            % (label, st["base"].split("/")[-1], days, st["undelivered"],
               st["base"].split("/")[-1], days),
            dedup_key="stuck-main:%s:%d" % (label, int(now // reping)),
            dry_run=dry_run)
        logs.append("stuck-main PING %s -> %s" % (label, status))
    if not dry_run:
        # See net_drift_alarm's matching comment: prune untouched-but-live
        # entries normally, and age out anything unrefreshed past
        # DEDUP_MEMORY_MAX_AGE_S regardless (#172 reopened smaller item).
        state["stuck_main"] = {
            k: v for k, v in seen.items()
            if (k in live or k not in touched)
            and (now - float(v.get("pinged_ts", now)) < DEDUP_MEMORY_MAX_AGE_S)}
    return logs


class _FlushList(list):
    """A plain list that ALSO fans every appended/extended item out to
    `log_fn` immediately, as it happens (#172 fix 3). `run_once()`'s own
    decision log used to be visible to a caller ONLY through the list it
    RETURNS -- so a sweep killed mid-way by systemd's TimeoutStartSec (the
    exact #172 incident) printed NOTHING for the whole 14h it recurred, even
    though job 1 (the API-error auto-resume) runs first and had already
    decided plenty before a later job's hung network call ate the rest of
    the unit's budget. Every existing `logs += job(...)` / `logs.append(...)`
    call site in this module keeps working unchanged: `+=` on a list calls
    `__iadd__`, which here still fans out, and a helper handed `logs` by
    reference (e.g. `_nudge_dying_subagent`) mutates the SAME object."""

    def __init__(self, log_fn=None):
        super().__init__()
        self._log_fn = log_fn

    def append(self, item):
        super().append(item)
        if self._log_fn:
            self._log_fn(item)

    def extend(self, items):
        items = list(items)
        super().extend(items)
        if self._log_fn:
            for item in items:
                self._log_fn(item)

    def __iadd__(self, other):
        self.extend(other)
        return self


def _apierr_stashabort_skip(state, logs, send_fn, project, pid, key, now, idle,
                            grace, reason, owner, dry_run, skip_label):
    """Shared bounded escalation for EVERY job-1 skip path that could not
    verify a stash delivery this poll for an idle-with-a-draft pane — an
    ABORTED stash (`deliver_with_stash` returning False, #176 F1) or a RACED
    re-classification (the fresh capture no longer agrees with idle-with-draft
    between the top-of-sweep capture and the send, #176 REOPENED R2). Both
    are structurally the SAME shape from the escalation's point of view: no
    keystroke was sent this poll and delivery could not be verified, so BOTH
    share the SAME dedicated `apierr-stashabort:` state record — a single
    episode gets job 4's escalation shape (exactly one deduped ping once the
    stall runs strictly past 2x grace of WALL CLOCK, never live transcript
    idle — the #176 F2 anchor, extended here) regardless of which of the two
    reasons kept recurring. `state[key]` (the decide()-tracked nudge/backoff
    entry) is never touched here — only this dedicated bookkeeping key — so
    NEITHER skip path ever burns a retry."""
    skey = "apierr-stashabort:" + key
    sb = state.get(skey) or {"first_seen": int(now - idle), "pinged": False}
    sb["last_seen"] = int(now)
    state[skey] = sb
    logs.append("%s %s [%s] (%s)" % (skip_label, project or pid, key, reason))
    if not sb["pinged"] and (now - sb["first_seen"]) > 2 * grace:
        sb["pinged"] = True
        logs.append("stash-abort-wedged (api-error) %s [%s] (%s) "
                    "— ping only (never a raw keystroke)"
                    % (project, key, reason))
        send_fn("\U0001f6d1 **%s** — API chyba drží pane s "
                "rozpísaným draftom, no doručenie zlyhalo (%s) "
                "— over ju prosím ručne."
                % (project, reason),
                owner=owner,
                dedup_key="apierr-stashabort:%s:%s" % (key, sb["first_seen"]),
                dry_run=dry_run)


# --------------------------------------------------------------------------- #
# One poll cycle
# --------------------------------------------------------------------------- #

# #172: a wall-clock SELF-BOUND on the per-transcript pane loop below (jobs 1,
# 2, 4, 4a, 5, 6, 7, 9, 10 all run inside it). Live evidence on dev1
# (2026-08-05): the sweep is still occasionally SIGTERM-killed by systemd's
# TimeoutStartSec=120 (~4 times/24h, self-recovering next tick, never the
# original 7h+ livelock) with NOT ONE log line printed before the kill —
# meaning the hang sits well before jobs 27/28's OWN already-bounded per-repo
# work (their cadence markers were progressing normally the whole time). A
# leftover from a slow tmux round-trip or a large pane count can still push
# this loop past budget under load. Rather than let systemd SIGTERM the
# process mid-loop (losing whatever this tick hadn't reached AND printing
# nothing), the loop checks its own wall clock and exits gracefully once
# `SWEEP_WALL_CLOCK_BUDGET_S` is spent — the remaining lightweight jobs still
# run afterward and this sweep's own `save_state()` still fires. A degraded
# sweep (fewer sessions handled this tick) beats a killed one.
SWEEP_WALL_CLOCK_BUDGET_S = 90    # env AIRULESET_SWEEP_BUDGET_S
# #255 (adversarial review, MAJOR finding): jobs 8/9's OWN per-item budget
# checks (added the same ticket) must NOT reuse the bare `sweep_deadline`
# above verbatim -- that deadline is scoped to the PANE LOOP, which runs
# BEFORE jobs 8/9 and can legitimately consume the entire 90s on its own
# (measured live: 26 of 3837 sweeps over 3 days). Reusing it meant jobs 8/9
# got ZERO of the ~30s margin the 90/120 split was always meant to leave
# them (the docstring above already says "the remaining lightweight jobs
# still run afterward") -- they would silently defer EVERY target/pane at
# idx=0 whenever the pane loop alone used its full budget, exactly when a
# real backlog is most likely to exist. `TAIL_BUDGET_S` extends their
# deadline into that same margin, still comfortably under the 120s hard
# kill (90 + 20 = 110s, 10s of slack left for jobs 1-7/11 in between).
TAIL_BUDGET_S = 20                # extra seconds for jobs 8/9 past sweep_deadline

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
             sleep_fn=None, burn_snapshot_path=None,
             compact_requests_path=None, fleet_fetch=None, fleet_hosts=None,
             fleet_path=None, burn_alert_enabled=False,
             goal_rearm_enabled=False, long_turn_enabled=False,
             goal_templates_path=None, delivery_probe=None, card_probe=None,
             closed_fetch=None, compact_stall_enabled=False,
             repo_roots=None, issue_counts_fetch=None, git_fetch=None,
             vault_purge=None, log_fn=None, reopen_fetch=None,
             time_fn=None, sweep_budget_s=None, backlog_fetch=None,
             progress_dir=None):
    """Scan every `claude` pane once. 29 numbered jobs per poll — 24 LIVE and 5
    RETIRED (12, 18, 23 removed in #132; 15, 17 in #102), whose numbers are
    kept addressable so historical log lines and code comments still resolve.
    The (4a) sub-entry belongs to job 4 and is not separately numbered:
      (1) a session STALLED ON AN API ERROR → auto-resume it (`continue`) + ping;
          past `max_nudges` it does NOT give up — it keeps nudging forever at a
          widening interval (#175), with a one-shot "gave up" ping alongside.
          #176: a pane idle at `❯` but holding a FOREIGN DRAFT is genuinely idle,
          not busy — `_classify_boundary` tells it apart from a real foreground
          turn, and delivery goes through `deliver_with_stash` (never a raw
          keystroke over the draft; an aborted stash never burns a retry). A
          pane that IS genuinely busy (or has no locatable boundary at all)
          gets job 4's busypane shape: zero keystrokes, one deduped ping per
          episode once the stall runs past 2x grace of WALL CLOCK (anchored on
          the episode's own first_seen, NOT on transcript mtime — an unrelated
          transcript write can hold `idle` artificially low for as long as the
          busy stretch lasts). An ABORTED stash delivery gets the identical
          bound under its OWN dedicated prefix (#176 REOPENED finding F1: the
          first pass moved this exact silent-unbounded-skip from the busy
          branch to the stash-abort branch instead of eliminating it) — so
          EITHER job-1 skip path is now bounded to at most one ping, never
          silent forever. That guarantee is about the USER being told, not a
          proof the draft itself survives every possible race in
          `deliver_with_stash` (its own internal render-race is mitigated by a
          bounded settle poll + a best-effort restore, #176 F4, not eliminated
          — the ping above is what still fires even in that residual case);
      (2) a session WAITING ON THE USER (AskUserQuestion / permission dialog) →
          PING ONLY, never act (a design decision needs the human);
      (3) (only when `usage_fetch` is given) a rate-limited WEEKLY-TOKEN-USAGE poll
          → ping when a weekly limit reaches the cap %;
      (4) a session idle on `⏳ WORKING` ≥ `stall_working` with NO advancing subagent
          → NUDGE the pane with a `stuck-check` self-check prompt (its launched work
          may have died silently); retry up to `max_nudges`, escalate-ping on give-up.
          #352: FIRST checked against the pane's OWN footer badge for a live
          background shell/monitor task (`⏵⏵ … · N shell[s]`) —
          `_pane_live_shell_evidence` — direct pane-visible proof of life the
          session never has to be asked to reconfirm; the whole check (both
          this nudge and the busy-pane ping) is SKIPPED while that badge is
          on screen, re-evaluated fresh every sweep. When the session DOES
          answer a nudge (no pane badge, but it replied and is genuinely
          still waiting), repeats are spaced on an EXPLICIT widening
          schedule (1h → 3h → 6h, holding) instead of nudging on the base
          cadence forever;
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
          idle pane (deliver_discord_replies), react ✅ on success. The SAME poll
          pass also carries two more owner-driven signals (#297/#298 — extensions
          of this job, never new ones, per the supervision-machinery FREEZE): a
          ❓/❔ REACTION on a bot message this job already TRACKS (an outstanding
          question, or a completion card via the new `discord-cards.json` map
          `notify.record_card_message` writes at send time) nudges the exact
          asking session — or, when it is no longer live, the nearest live pane
          whose repo matches (`_repo_live_pane`) — to ask the user a structured
          question quoting the flagged message; and a REPLY on a completion card
          reopens the card's `repo`#`issue` (idempotent), comments the remark
          verbatim, applies `prio:bounce` (creating the label only if genuinely
          absent, never `--force`), and best-effort nudges a live idle pane of
          that repo. Both dedup on their own bounded state lists
          (`dreact_done`/`dcard_done`, mirroring `dreply_done`) and never guess a
          delivery target — an untracked flagged message, or one with no live
          session/repo pane anywhere, is silently skipped and retried next sweep;

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
      (10) QUEUED-PROMPT-WEDGE (#20, reworked #35) — text sitting in a pane's
          input box (a submitted-but-stuck queued prompt, or an abandoned
          draft) blocks every keystroke delivery and can park a session for
          hours. Byte-identical box text across >= PWEDGE_SWEEPS sweeps with a
          stale transcript and no live-work signal → PING FIRST, never a blind
          keystroke into text the user may still want (prompt_wedge_check).
          #160 defect 4 (only when `backlog_fetch` is given): a draft not
          blocking a pending USER question is still worth a ping when the
          repo's own backlog genuinely has open work nobody else can reach.
      (12) MODEL RECONCILE — REMOVED (#132, 2026-07-28). Restarted a session
          parked on an expensive tier by typing `/exit` into its pane and
          relaunching. Two independent reasons it is gone: the remedy
          auto-downtiered a model the user had deliberately chosen with
          `/model`, which `model-awareness.md` forbids ("their call alone —
          never auto-downtier it"); and `MANAGED_MODEL` already binds every
          NEW session, so drift only survives in long-lived sessions the user
          can move with one `/model` — measured live: a running session
          launched on `claude-opus-5` can select Opus 5 (1M context) with no
          restart at all. Number retained, never reused (#102 convention).
      (13) HOURLY BURN SNAPSHOT (#37) — once per hour, append this host's
          $/msgs/avg-context row for the PREVIOUS full hour to
          `burn-history/snapshots.jsonl` (burn_snapshot_job) — the feed
          `airuleset.py burn --compare` reads, so a change's cost impact is
          measured automatically, with nothing for the user to check.
      (14) (only when `compact_requests_path` is given) /COMPACT AT TICKET
          BOUNDARIES (#39 krok 1c) — a session whose Stop hook just recorded
          a completed-ticket report gets `/compact` typed into its pane once
          it goes genuinely idle (never busy/dialog/draft/in-mode; never
          while its CURRENT last turn is a ❓ block, #102 —
          `_compact_blocked_by_question`; compact_ticket_boundary) — a safe
          compaction point, since the ticket's durable state already lives
          in git/GitHub/the issue. #109 adds the DELIVERY-time half of that
          re-check (`_compact_not_at_boundary` — `⏳` too, not just `❓`: a
          request justified by a `✅` boundary several turns ago must not
          fire once a dispatched worker's context-only state is in flight)
          and a lapse for a request that never found a safe moment
          (`COMPACT_REQUEST_MAX_AGE_S`). THE ONLY /compact SENDER left in
          this module besides the synchronous #65 path
          (`deliver_compact_now`, called from `cmd_compact_request`, which
          #109 also stops from typing into a boundary whose Stop an earlier
          hook already refused — `_stop_already_rejected`) — see (15)/(17)
          below.
      (15) COMPACT OVERGROWN IDLE SESSIONS — REMOVED (#102, 2026-07-27). Used
          to fire `/compact` purely off CONTEXT SIZE + IDLE DURATION, with
          no regard for what marker the session's last turn ended on — the
          user's corrected agreement: compaction fires ONLY at a completed-
          ticket boundary (14), nothing else. See the removed section
          comment above `_pane_compacting` for the full audit of what
          survived. Number retained (not reused) for historical
          addressability of prior comments/logs referencing "job 15".
      (16) (only when `fleet_fetch` is given) HOURLY FLEET BURN (#55) —
          coordinator-only (cmd_watchdog wires this ONLY on dev1): merges
          every managed box's own hourly burn-snapshot row (job 13's output,
          tailed over ssh via `fleet_fetch`) into ONE combined
          `~/.claude/burn-history/fleet.jsonl` row per hour
          (fleet_burn_job), plus a deduped Discord ping when the observed
          weekly-%/day pace exceeds the budget implied by the usage cache.
      (17) HARD CONTEXT CEILING BACKSTOP — REMOVED (#102, 2026-07-27). Used
          to fire `/compact` purely off CONTEXT SIZE (a fixed ceiling),
          regardless of idle duration and even into a BUSY pane — the same
          #102 correction as (15): compaction fires ONLY at a completed-
          ticket boundary (14). See the removed section comment above
          `_pane_compacting` for the full audit. Number retained for
          historical addressability.
      (18) HOOKS RECONCILE — REMOVED (#132, 2026-07-28). Existed on the
          premise that Claude Code snapshots its hook set at process start and
          never re-reads it, so only a restart could pick up a newly deployed
          hook. That premise is measurably FALSE. Measured bidirectionally in
          an isolated scratch session: a hook ENTRY appended to settings.json
          mid-session fired on the very next tool call, and the same entry
          REMOVED mid-session stopped firing immediately, with a control arm
          (a new line inside an already-registered hook script) proving the
          event fired on every turn. Hooks are re-read per event; nothing ever
          needed restarting. Number retained, never reused.
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
          Discord ping on give-up — only a refusal that actually touched the
          pane counts toward that cap, a PRE-send refusal (still holding a
          foreign draft, mid-turn) is retried next sweep for free, and a
          `Goal set:` marker older than `GOAL_REARM_MAX_DARK_S` since it was
          last confirmed lit is never revived at all (#101); a `Goal cleared:`
          newer than the last `Goal set:` is a deliberate shutdown and is
          never touched. A session whose OWN last real turn is still an
          unanswered `❓ NEEDS YOU:` (STOP CONDITION (A) — never a bare
          `❓ ASKED:` body line paired with a terminal `⏳ WORKING`, which
          keeps working and is never this) is likewise never re-armed,
          regardless of what CC's own achieved-marker/backlog state says —
          a loop legitimately BLOCKED on the user's own answer is not a
          died one (`_goal_blocked_on_unanswered_question`, #350 — the
          livelock this closes: re-arm -> the freshly-armed session
          re-evaluates the SAME unanswered question -> immediately
          re-stops -> repeat, forever, until the user actually replies).
          Runs LAST so job 14 (and, until REMOVED #102, jobs
          15/17) gets first crack at the same pane, and skips any sid it
          compacted this sweep (`handled`) or that holds an outstanding
          shared claim (#78).
          When `goal_templates_path` is ALSO given, the same job carries a
          THIRD shape — STALE TEMPLATE (#64): `/goal` reads the autopilot
          template once, at arm time, so a template change pushed to the
          fleet leaves every RUNNING loop evaluating the old stop
          conditions forever (which also starves compaction, since #58's
          ticket-boundary report is what triggers it). A pane whose goal is
          provably ARMED but whose payload no longer matches the shipped
          template is re-armed with the current text — identity by EXACT
          hash of a NORMALIZED form (citations / backtick spans /
          punctuation dropped), never a similarity threshold, since two
          CURRENT variants measure closer together than one variant does to
          its own three-week-old version. Only a session OBSERVED matching
          a template on an earlier sweep is ever re-armed, and only with
          the SAME variant it already ran (the authority profile is never
          re-resolved), so a goal the user wrote is untouchable by
          construction rather than by threshold (`_goal_template_drift`).
          A FOURTH, always-on shape (#161): a genuine, TRAILING
          `❓ NEEDS YOU` status line (never a mere mention of those words
          elsewhere in the turn) whose CONFIRMED-delivered ping
          (`discord-questions.json`'s own `ts`, matched to THIS block by
          timestamp proximity — a stale, unrelated sibling question can
          never stand in) is more than `GOAL_QUESTION_TIMEOUT_S` (~30 min)
          old with no reply gets ONE `question-timeout:` nudge — never
          bare "continue" (which would just re-print the same question,
          the camera-box wall) — instructing the session to park that
          ticket and work others (`_goal_question_park_nudge`); the
          "already nudged" state is persisted BEFORE the keystroke send
          (never after), bounded to one nudge per distinct outstanding
          question, never a guess when no delivery is on record.
      (21) (only when `long_turn_enabled` is truthy) LONG-TURN WATCH (#84) —
          a turn that simply RUNS for hours is a fault state of its own:
          nothing compacts, no question is delivered, and every keystroke
          piles up unexecuted in CC's type-ahead queue. Read from the PANE's
          own spinner elapsed label (`pane_turn_elapsed`), never from
          transcript turn boundaries — the live incident was logged by CC as
          three ordinary turns, each continued by the goal loop's REJECTED
          Stop, and the queue drained at none of them. Above
          `LONG_TURN_THRESHOLD_S` (env `AIRULESET_LONG_TURN_S`, default 30
          min) it logs unconditionally every sweep and sends ONE Discord
          ping per (session, turn) via the #81 notify path
          (long_turn_watch). DETECTION ONLY — it never types anything;
          breaking a running turn is the user's call.
      (22) STALE EXEC-MARKER CLEANUP (#97) — always on. block-main-
          implementation.sh's one-shot bypass markers
          (/tmp/airuleset-main-exec-ok-<sid>, legacy -fable- form too) are
          consumed the moment the hook honors one, but a session that ends
          without another guarded call never consumes its own marker — it
          sits in /tmp forever (a real orphan found live: 0 bytes, ~21h
          old, no session anywhere still matching it). Hygiene, not a
          security hole (a marker is matched by session id, so a stale one
          is already inert) — a marker older than `MAIN_EXEC_MARKER_MAX_AGE_S`
          (default 6h) is removed ONLY when no currently-live pane's
          transcript stem still resolves to its session id
          (`cleanup_stale_exec_markers`); a live session's marker is never
          touched no matter its age, since removing it would silently
          revoke a deliberately granted exception mid-work.
      (23) MANAGED_MODEL GENERATION RECONCILE — REMOVED (#132, 2026-07-28).
          Compared the session's LAUNCH-time model — an API model id, always
          `claude-opus-5` — against `MANAGED_MODEL`, a CLI alias spelled
          `claude-opus-5[1m]`. The `[1m]` suffix never reaches the API model
          field (zero occurrences in 119k model entries across every real
          transcript on dev1), so `launch != target` was permanently true for
          every session on every box, and each restart minted a NEW session id
          that defeated the per-sid dedup meant to stop the retry — an
          unbounded restart engine. It restarted a pz-server session the user
          was working in, 3 minutes after job 18 had already restarted it.
          Number retained, never reused.
      (24) (only when `delivery_probe` is given) DELIVERY-STALL WATCH (#138)
          — a loop that cannot MERGE produces nothing while spending at full
          rate, and every other signal here is merge-TRIGGERED (the run-card
          fires AFTER a merge, autopilot-progress is fed by that card, the
          Issues segment only ever grows), so that state is silent BY
          CONSTRUCTION. camera-box ran 17 days that way: PR #704 BLOCKED on
          one permanently-red rig gate (105 failures, 0 successes since
          2026-07-13), origin/main frozen at 2026-07-11, 422 commits
          stranded on dev, and issue closure — merge-driven there — at
          exactly zero, while the loop kept dispatching and committing. Per
          repo hosting a live pane this compares two purely LOCAL git facts:
          the newest commit on the checked-out branch (SPEND) against the
          newest commit on the base branch plus the count of commits not
          reachable from it (DELIVERY). Fresh work over a frozen base with a
          real backlog is the stall. A candidate is CONFIRMED before it is
          announced — the probe fetches the base ref and the verdict is
          RE-MEASURED, so a locally stale remote-tracking ref can never on
          its own raise an alarm; the probe's other half names the blocking
          PR and its red check and is pure best-effort enrichment. The stall
          window is bounded on BOTH ends — past `DELIVERY_STALL_MAX_S` the
          base is not a delivery target that stopped receiving but one
          nobody delivers to at all (a legacy `origin/master` abandoned in
          2019 while real work merges elsewhere), which no lower bound can
          tell apart. Silent where it should be: HEAD == base (this repo,
          which pushes straight
          to main) yields 0 undelivered — structurally, not by threshold —
          and a parked repo with no fresh commits says nothing, because
          nothing is being spent. DETECTION ONLY (job 21's discipline): one
          deduped ping per repo per day while it lasts, never a keystroke —
          what to do about a blocked merge is the user's call, and this
          module owns none of the repos it watches (delivery_stall_watch).
      (25) (only when `card_probe` is given) CARD RECONCILIATION (#134) — the
          MIRROR of job 24: that one fires when the base branch FROZE, this
          one when the base branch MOVED and the reports did not. marek's
          Discord thread got ZERO per-ticket completion cards for five days
          while parovanie-produktov closed 97 issues / merged 80 PRs and
          tvdole closed 6 / merged 5; the last marker for that repo is #192
          at 2026-07-23 19:05 and tvdole has never produced one at all. The
          mechanism was healthy throughout — the card is simply an action
          with no artifact anyone checks, so workers drifted out of the
          habit. The SubagentStop gate is the in-band half and restores a
          REAL card, but it is structurally blind to a worker that DIED
          mid-run (it never reaches SubagentStop) and to a delivery that
          failed after the worker returned; both collapse to the observable
          this job measures. Per repo hosting a live pane, purely LOCAL git:
          the `Closes/Fixes/Resolves #N` carried by commits that landed on
          the base branch inside `CARD_WINDOW_S`, against the DELIVERED card
          markers (`notify.marker_delivered` — a marker is written BEFORE
          the POST, so presence alone is a claim, never a delivery, #135).
          The repo NAME comes from `origin`, never the directory basename:
          the checkout is `parovanie_produktov` while every marker is keyed
          `parovanie-produktov`. Confirmed before announced (job 24's
          contract): the probe fetches the base ref, the measurement follows
          it. #224: a ticket younger than `CARD_GRACE_S` (20 min, read from
          its OWN closing commit) is not yet a gap — the original code had
          no minimum age at all and beat the worker's own delivered cards
          by 3 seconds on a live clean run. And the ping is deduped per
          TICKET, forever, never per repo per calendar day — a ticket that
          can never get a card (the worker died, or the issue closed with
          no PR) used to re-announce daily for the whole 48h window and
          restart the clock for every OTHER ticket in the same repo (834
          consecutive sweeps of the same six camera-box tickets). DETECTION
          ONLY, never a keystroke (card_reconcile).
      (26) (only when `compact_stall_enabled`) COMPACT-STALL WATCH (#140) —
          the companion to the claim TTL in `compact_claim_active`. Two
          sessions on two boxes sat unable to compact — forestshop@dev1 at
          ~500K, montalu@subdev at 400K for 21h26m — and NOTHING noticed;
          the user did, from two screens, and hand-compacted both. Per
          session with a live pane this reads the ARTIFACT, never intent
          (#134's lesson): a `/compact` claim still on file, older than
          `COMPACT_STALL_S`, with no `compact_boundary` in that session's
          own transcript newer than the claim — i.e. a compaction that was
          asked for and never landed, whatever the cause (a pane whose
          type-ahead never drained, a boundary scrolled out of the bounded
          tail read). Job 24 could not carry it: that one is REPO-keyed and
          measures git delivery, this is SESSION-keyed and measures
          compaction — what is reused is its shape. DETECTION ONLY: logged
          every sweep, ONE deduped ping per session per
          `COMPACT_STALL_REPING_S`, never a keystroke and never a write to
          the claim (the TTL releases it on the next send attempt)
          (compact_stall_watch).
      (27) (only when `issue_counts_fetch` is given) NET-ISSUE-DRIFT ALARM
          (#137) — per repo discovered by `repo_roots` (a list or a callable
          returning one — cmd_watchdog wires the real `find $HOME -maxdepth
          N -name .git` sweep), the trailing-7-day opened-minus-closed
          issue count via the injected `gh` fetch. camera-box's own
          measured +101 over 21 days ran at roughly +34/week at its worst
          window — this would have pinged around the point the drift
          started, instead of the user noticing by feel two weeks later.
          Runs at most once an hour (`MANAGED_SWEEP_INTERVAL_S`, its own
          cadence gate — a `gh` round trip per repo is too costly for a
          60s poll), one deduped ping per repo per day while the net stays
          above `NET_DRIFT_THRESHOLD` (net_drift_alarm). #172 (regression
          from #137's own launch, a dev1-only livelock — per the #176
          correction on job (1) above, it never starved job 1, which runs
          long before jobs 27/28 in the same per-pane loop): the cadence
          marker used to live only in run_once's in-memory `state`
          (`save_state` ran once, at the very end), so a systemd
          `TimeoutStartSec=120` kill mid-sweep lost it entirely and the
          NEXT 60s tick re-attempted the identical 40-repo sweep, killed
          again, forever — 236 kills in one day, zero before. Fixed by
          persisting the cadence marker to DISK (`persist=`) BEFORE the
          per-repo loop, plus a BOUNDED + round-robin-cursored repo list
          per sweep (`_repo_sweep_batch`/`AIRULESET_REPO_SWEEP_BATCH`) —
          see `REPO_SWEEP_BATCH_MAX`'s own comment for the real (not "one
          hour") stale-data bound this buys and the honest worst-case cost
          that remains. #172 REOPENED (post-merge adversarial review of
          the shipped fix) found 4 more real defects here: finding 1 —
          `log_fn=print` never actually flushed under systemd's piped,
          non-tty stdout, so a killed sweep STILL printed nothing (fixed:
          `cmd_watchdog` wires an explicit `flush=True` wrapper instead of
          bare `print`); finding 2 — `AIRULESET_REPO_SWEEP_BATCH=0` (or a
          negative value) used to silently sweep ALL repos, re-arming the
          exact cost the cap exists to prevent, via the knob most likely to
          be reached for (fixed: clamped to the documented default);
          finding 3 — the per-repo dedup memory (duplicate-ping
          suppression) was written to disk only at the very END of the
          loop, so a kill between two pings lost the FIRST repo's dedup
          entry and re-pinged it on its next rotation (fixed: mirrors jobs
          8/11's own "dedup memory BEFORE the ping" shape, which the
          original #172 fix had copied only half of); finding 4 — the
          cadence marker was persisted only AFTER `repo_roots()` (an
          `os.walk($HOME)`) ran, so a kill inside the walk itself still
          lost it (fixed: persisted before `repo_roots()` is even called).
      (28) (only when `repo_roots` is given) STUCK-MAIN SWEEP (#137) — the
          NON-pane-gated sibling of job 24: same measurement
          (`delivery_state`/the base-branch-frozen-while-work-piles-up
          verdict, bounded on both ends per #138's own fix), but swept
          across EVERY repo `repo_roots` discovers rather than only a repo
          with a currently open pane — the gap that let camera-box's own
          15-day merge deadlock (#138's origin story) run undetected for as
          long as it did whenever no session happened to be open there.
          `git_fetch(root)` (best-effort, errors logged) runs before
          reading refs, since no live session may have fetched recently —
          #172 REOPENED finding 5: a fetch FAILURE now SKIPS the repo this
          sweep instead of measuring on refs that may be stale (a repo
          merely behind a slow network link used to read as stuck-main —
          a false-positive generator job 27's own `None`-on-failure
          contract never had). Same hourly cadence gate as job 27, one
          deduped ping per repo per day (stuck_main_sweep). Same #172 (and
          #172 REOPENED findings 2/3/4) fixes as job 27: `persist=` before
          `repo_roots()` and again before the per-repo loop, dedup memory
          persisted before each ping, batch-cap clamping — same repo-batch
          cap, its own cursor, so the two jobs rotate through the repo
          list independently.
      (29) (only when `vault_purge` is given) HOURLY CREDENTIAL-STORE SWEEP
          (#144) — delete every `airuleset.py secret` value past its TTL.
          The store's expiry used to be enforced ONLY by the next `secret`
          invocation, so the normal one-off shape (request, exec, never run
          it again) left a credential 0600 on disk indefinitely — the exact
          property the channel exists to provide. Delete-only: no pane, no
          keystrokes, no ping, and it removes only what is already expired.
    Returns a list of human-readable action log lines (for --verbose / tests).
    `log_fn` (#172), when given, is called with EACH line as it is decided —
    incrementally, job by job — rather than the caller only ever seeing the
    full list after this function returns. cmd_watchdog wires it to `print`
    so a sweep killed mid-way (systemd TimeoutStartSec) still leaves every
    earlier job's decision in the journal, instead of the whole sweep's
    output vanishing with it (the exact #172 "job 1 logged nothing for 14h"
    symptom).

    `time_fn`/`sweep_budget_s` (#172, cross-cutting — not a numbered job):
    the per-transcript pane loop self-bounds against `time_fn()` (default
    `time.monotonic`) so an unusually slow sweep exits gracefully once
    `sweep_budget_s` (default `SWEEP_WALL_CLOCK_BUDGET_S`, env
    `AIRULESET_SWEEP_BUDGET_S`) is spent, rather than being SIGTERM-killed by
    systemd mid-loop. Every session not reached this tick keeps its existing
    episode state (added to `stalled`) exactly like a session skipped by a
    busy-pane/copy-mode gate — never wiped by the cleanup pass below."""
    now = time.time() if now is None else now
    run = run or _default_run
    time_fn = time_fn or time.monotonic
    if sweep_budget_s is None:
        try:
            sweep_budget_s = int(os.environ.get("AIRULESET_SWEEP_BUDGET_S",
                                                 SWEEP_WALL_CLOCK_BUDGET_S))
        except ValueError:
            sweep_budget_s = SWEEP_WALL_CLOCK_BUDGET_S
    # (adversarial review, batch-3 #172/#183/#180/#174) a `<= 0` value (an
    # operator's `AIRULESET_SWEEP_BUDGET_S=0`, or a negative one -- both
    # parse as valid ints, so the `except ValueError` above never catches
    # them) would set `sweep_deadline` to now-or-earlier, so the very FIRST
    # loop-top check trips and EVERY session is skipped on EVERY sweep
    # forever -- silently disabling jobs 1/2/4/4a/5/6/7/9/10 fleet-wide, the
    # opposite of this fix's whole purpose. Unconditional (not just the
    # env-resolved branch above), matching `_repo_sweep_batch`'s own
    # `max_repos <= 0` clamp (REPO_SWEEP_BATCH_MAX's own finding-2 comment)
    # — a caller-supplied value gets the same safety net as an env one.
    if sweep_budget_s <= 0:
        sweep_budget_s = SWEEP_WALL_CLOCK_BUDGET_S
    sweep_deadline = time_fn() + sweep_budget_s
    from notify import compose_api_error_alert, stream_redirect
    if send_fn is None:
        from notify import send as send_fn

    state = load_state(state_path)
    logs = _FlushList(log_fn)
    stalled = set()
    owner_by_sid = {}                   # session id -> tmux owner, for job 5's ✅ @mention
    owner_by_cwd = {}                   # pane cwd -> tmux owner, job 5's recovery path
    owners_seen = set()                 # every owner with a pane here — >1 = multi-owner box
    project_by_sid = {}                 # session id -> project label, for job 21's ping
    cwd_by_sid = {}                     # session id -> pane cwd, for job 24's repo read
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
    live_pane_ids = set()               # every claude pane id THIS sweep sees — job 10
                                         # cleanup (#199) prunes a pwedge:/pwedge-ping:
                                         # entry for a pane id NOT in this set. Populated
                                         # from EVERY pane list_claude_panes discovers
                                         # (not just the subset job 10 actually processes
                                         # this sweep) — a pane ambiguously mapped to a
                                         # shared transcript, or a sudo-hosted one, is
                                         # still genuinely LIVE even though job 10 skips
                                         # it this particular sweep.
    for pid, cwd in list_claude_panes(run, dry_run=dry_run):
        live_pane_ids.add(pid)
        tinfo = find_active_transcript(projects_dir, cwd)
        if not tinfo:
            fu = _foreign_user(cwd)
            if fu:
                hosted_panes.append((pid, cwd, fu))
            continue
        tpath, tmtime = tinfo
        by_transcript.setdefault(str(tpath), []).append((pid, cwd, tmtime, tpath))

    for idx, (tkey, owners) in enumerate(by_transcript.items()):
        if time_fn() >= sweep_deadline:
            # #172: every session not reached THIS sweep must not lose its
            # existing episode state — the identical #175 F1 hazard, applied
            # to a session skipped by BUDGET rather than by a busy-pane/
            # copy-mode gate. Preserve each remaining transcript's own
            # bare-UUID key so the cleanup pass below does not wipe it.
            for rtkey in list(by_transcript.keys())[idx:]:
                stalled.add(Path(rtkey).stem)
            logs.append("sweep-budget-exceeded (pane-loop) — %d/%d sessions "
                        "handled this tick, rest retried next"
                        % (idx, len(by_transcript)))
            break
        try:
            if len(owners) > 1:
                logs.append("skip ambiguous (%d panes → %s)" % (len(owners), Path(tkey).stem))
                continue
            pid, cwd, tmtime, tpath = owners[0]
            idle = now - tmtime
            project = project_label(cwd)
            key = tpath.stem                   # session id (stable across grouped panes)
            project_by_sid[key] = project      # job 21: a human label for the ping
            cwd_by_sid[key] = cwd              # job 24: which REPO this loop spends in
            # #212: STREAM_NOTIFY_OWNER-aware — raw tmux session name redirected
            # to its Discord identity (a stream persona's own account name
            # otherwise bypasses the SAME map every other notification uses).
            owner = stream_redirect(pane_owner(pid, run))
            if owner:
                owner_by_sid[key] = owner      # so job 5's ✅ ping @mentions this session's owner
                owner_by_cwd[cwd] = owner      # job 5: recover the owner when the sid is missing
                owners_seen.add(owner)         # >1 → account_owner is a coin flip, not an answer
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
            #
            # Watchdog #177: `tmtime` above is the transcript FILE's raw
            # mtime, which several NON-TURN entry types keep bumping to
            # "now" with zero real progress (see `_last_real_turn_ts`'s own
            # docstring) — job 10's own 30-minute staleness gate then never
            # accumulates and the job silently never fires. Job 10's idle
            # clock reads the newest genuine `user`/`assistant` turn from
            # the transcript's CONTENT instead, falling back to the raw
            # mtime only when that tail is unmeasurable (never worse than
            # before). Every OTHER consumer of `tmtime` in this loop is
            # untouched — this is scoped to job 10's own gate only.
            #
            # Adversarial-review hardening (post-#177): the "can only fire
            # MORE readily, never later" claim holds because raw mtime is
            # normally >= the true last-real-turn timestamp — but ONLY if
            # every entry's own `timestamp` field is trustworthy. A single
            # entry with a clock-skewed/future timestamp (never observed
            # live, but not provably impossible) would make
            # `_last_real_turn_ts` return something NEWER than the file's
            # own mtime, which would make job 10 wait LONGER than before —
            # the one direction the fix must never move. Clamping to the
            # file's own mtime removes that failure mode by construction
            # (a min() can only ever REMOVE a violation of the invariant,
            # never introduce a new one) at zero cost to the real fix.
            wedge_tmtime = _last_real_turn_ts(tpath)
            if wedge_tmtime is None:
                wedge_tmtime = tmtime
            else:
                wedge_tmtime = min(wedge_tmtime, tmtime)
            logs += prompt_wedge_check(now, state, pid, captured, wedge_tmtime,
                                       owner, project, send_fn,
                                       dry_run=dry_run, run=run,
                                       waiting=_session_is_waiting(tpath),
                                       cwd=cwd, backlog_fetch=backlog_fetch)

            # --- (6) 5-HOUR SESSION LIMIT → ping once, then RETRY a resume AFTER --
            # the reset, bounded, with a stable-draft submit path -------------------
            # A TIME-BASED cap: `continue` BEFORE the reset just re-hits it (the
            # user's incident), so we ping ONCE with the reset time, do NOTHING
            # until the reset clock, then attempt an auto-resume AFTER it — never
            # before. Detected either from the PANE (the banner on screen) OR —
            # #336 — from a `sesslimit:<key>` entry job 1's own TRANSCRIPT-based
            # usage-cap detection already seeded (below, near job 1's own
            # `is_usage_cap` branch): a session-limit hit that lands on a
            # background Agent/subagent, rather than this session's own
            # foreground turn, can settle at an ordinary idle prompt WITHOUT ever
            # rendering the banner as the pane's bottom-most content — the real
            # montalu2 incident — so `pane_session_limited` alone can never
            # engage for that pane at all. While a session is limited job 6 owns
            # it (skips the api-error / nudge paths).
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
            # active) just retries.
            #
            # RESOLUTION (#336): a REAL resume used to be detected purely by the
            # banner leaving the pane's bottom region (FIX B, original design) —
            # correct for the pane-visible case, but a transcript-only episode
            # NEVER had a banner to lose, so that signal alone would leak
            # `sesslimit:<key>` state forever once widened to cover it. Whenever
            # the pane does NOT currently show the banner, resolution is instead
            # re-derived straight from the TRANSCRIPT's own current error state
            # (`transcript_last_error` + `is_usage_cap`) — never the pane, and
            # never the frozen tmux statusline (this repo's own documented
            # "a pane's statusline is a frozen render" gotcha) — so both the
            # pane-visible and transcript-only cases resolve on the SAME
            # evidence a human would actually trust.
            skey = "sesslimit:" + key
            s = state.get(skey)
            if s is not None:
                # Job 6 legitimately owns this session's api-error tracking too
                # while an episode is parked — protect job 1's OWN bare-UUID
                # state entry from the end-of-sweep cleanup pass exactly like
                # job 1's own `stalled.add(key)` does for itself below (mirrored
                # placement, not a new mechanism).
                stalled.add(key)
                if not pane_session_limited(captured):
                    cur_err = transcript_last_error(tpath)
                    if not (cur_err and is_usage_cap(cur_err)):
                        del state[skey]
                        s = None
                        logs.append("session-limit %s — resolved, tracking cleared"
                                    % project)
            if pane_session_limited(captured) or s is not None:
                if s is None:
                    # Parse the reset clock ONCE at first detection and keep it stable for
                    # the whole episode — re-parsing after the reset would roll the same
                    # "6:10pm" forward to tomorrow and wrongly re-ping instead of resuming.
                    s = {"resets_at": parse_reset_epoch(captured, now),
                         "pinged": False, "continued": False, "first_seen": int(now),
                         "attempts": 0, "gave_up": False}
                    state[skey] = s
                elif s.get("resets_at") is None:
                    # an earlier poll couldn't read the clock — try again to
                    # refine it, from whichever surface THIS episode's own
                    # evidence actually comes from (adversarial review, F3):
                    # the PANE when it currently shows the banner, the
                    # TRANSCRIPT's own error text otherwise. Refining a
                    # transcript-only episode from the pane would read
                    # whatever the session's own SUBSEQUENT, unrelated reply
                    # happens to render near the bottom of the screen —
                    # including a stray "resets 17:00"-shaped phrase — and
                    # inject a bogus epoch, up to SESSLIMIT_MAX_TRIES
                    # premature `continue`s.
                    if pane_session_limited(captured):
                        s["resets_at"] = parse_reset_epoch(captured, now)
                    else:
                        cur_err = transcript_last_error(tpath)
                        if cur_err and is_usage_cap(cur_err):
                            s["resets_at"] = parse_reset_epoch_from_error_text(
                                cur_err, now)
                s["last_seen"] = int(now)
                ra = s.get("resets_at")
                if not s.get("pinged"):
                    s["pinged"] = True
                    # (adversarial review, batch-3 #172/#183/#180/#174):
                    # `now=now` -- run_once's OWN notion of "now" -- not the
                    # implicit default (real time.time()). Production calls
                    # run_once with now=None (so both are the same instant
                    # anyway, called microseconds apart), but ANY caller
                    # that injects a fixed/historical `now` (this whole test
                    # suite routinely does) would otherwise have
                    # _human_clock's "is this reset TODAY" check silently
                    # compare against the REAL wall clock instead of the
                    # run's own timeline -- wall-clock dependence with no
                    # functional purpose here.
                    when = _human_clock(ra, now=now) if ra else "čoskoro"
                    logs.append("session-limit %s — ping (reset %s)" % (project, when))
                    send_fn("⏳ **%s** — dosiahnutý 5-hodinový limit\n> Reset o %s. Po "
                            "resete pošlem `continue` automaticky — nič nemusíš robiť."
                            % (project, when),
                            owner=owner, dedup_key="sesslimit:%s:%s" % (key, ra or s["first_seen"]),
                            dry_run=dry_run)
                elif ra and now >= ra:
                    if session_user_stopped(tpath, since_ts=s.get("first_seen")):
                        # #336: the user explicitly typed `/exit` for THIS
                        # session at or after the limit hit — a stronger
                        # signal than "the reset clock passed", and it
                        # always wins. No keystroke, no ping (the user is
                        # already handling it themselves) — just stop
                        # tracking so a genuinely NEW limit hit later
                        # re-parks cleanly.
                        del state[skey]
                        logs.append(
                            "session-limit %s — user stopped (/exit) since "
                            "the limit hit; never auto-resuming" % project)
                        continue
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
                # (#175 F1) Mark this episode ALIVE for THIS sweep the instant we
                # know the session is still api-error-stalled — BEFORE any of the
                # gates below can `continue` out of this block. The cleanup pass
                # at the end of run_once deletes any bare-UUID episode key not in
                # `stalled`; with `stalled.add(key)` sitting AFTER the copy-mode /
                # busy-pane gates (its old position), a single gated sweep — the
                # pane busy precisely BECAUSE we had just typed `continue` into
                # it, or genuinely in copy-mode — silently deleted the
                # accumulated nudge-count/`escalated` entry and reset the #175
                # widening back-off to nudge #1 on the very next sweep. Worse,
                # the re-created entry re-seeds `first_seen` from transcript
                # mtime (which Claude Code's own retries/queue writes keep
                # moving), so the one-shot give-up ping re-armed under a
                # DIFFERENT dedup key and fired twice for the same real episode.
                # Hoisting the add here covers every early `continue` in this
                # neighbourhood: the copy-mode gate and busy-pane gate right
                # below, AND the #176 stash-abort/raced skips further down
                # (which already ran after the old position and needed no
                # change).
                stalled.add(key)
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
                # (the #233 scar). Never inject unless the pane shows a free prompt.
                #
                # #176 ROOT CAUSE: `pane_at_idle_prompt` alone (bare_only=True) cannot
                # distinguish "a real foreground turn is running" from "the pane is
                # genuinely idle at `❯` but its input box holds a FOREIGN DRAFT" — both
                # read as `not pane_at_idle_prompt`. The gatekeeper incident (2026-07-29)
                # was the second shape: a stale draft sat in the box for 36 minutes while
                # job 1 silently skipped 32 consecutive polls, wrote no state and pinged
                # nobody. `_classify_boundary` (#46) tells the two apart: `kind == "input"`
                # with a non-empty draft means the session IS idle — never busy — so
                # deliver via `deliver_with_stash` (the verified idle-with-draft protocol,
                # already used by jobs 7/9/20) instead of refusing forever. Only
                # `kind != "input"` (a real spinner/dialog, or no boundary locatable at
                # all) is genuinely busy and is still NEVER typed into.
                draft_pending = False
                if not pane_at_idle_prompt(captured):
                    boundary_kind, boundary_txt = _classify_boundary(captured)
                    # `and boundary_txt` is never False here: reaching this line already
                    # required `not pane_at_idle_prompt(captured)`, and a genuinely bare
                    # `("input", "")` boundary WOULD have made that gate True — so
                    # `kind == "input"` at this point always carries a non-empty draft
                    # (#176 F9, harmless dead conjunction — kept for readability).
                    if boundary_kind == "input" and boundary_txt:
                        draft_pending = True
                    else:
                        # Genuinely busy (or no boundary at all) → NEVER type. THIS
                        # BRANCH is bounded to at most one ping (#176 item 2): job
                        # 4's own escalation shape (a dedicated state record +
                        # exactly ONE ping per episode, below in this same
                        # function) fires once the stall runs strictly past 2x
                        # grace of wall clock. (#176 R1: this comment used to
                        # over-claim, presenting THIS one branch's own bound as if
                        # it were a system-wide guarantee — false at the time it
                        # was written: the separate stash-abort/raced skip further
                        # below (in this same function) had no bound of its own
                        # until the reopened pass gave it the identical shape; see
                        # its own comment for what it covers.) A DEDICATED state prefix
                        # ("apierr-busypane:", not job 4's own "busypane:") keeps the two
                        # independent episodes from ever clobbering each other's
                        # bookkeeping for the same session. Logging the classified kind +
                        # a snippet of the offending text (item 3) names the shape on
                        # every occurrence instead of every skip being an indistinguishable
                        # "busy-pane".
                        #
                        # Threshold anchored on THIS EPISODE's own `first_seen` (wall
                        # clock), NOT on live `idle` (= now - transcript mtime, #176 F2):
                        # CC's own retries / queue-snapshot writes keep touching the
                        # transcript while the pane stays genuinely busy, which can hold
                        # `idle` artificially low for as long as the busy stretch lasts —
                        # exactly the mtime trap job 1's OWN grace (above) already avoids
                        # for the other branch. `first_seen` is fixed the moment this
                        # episode is first observed and only accumulates real wall-clock
                        # time on every later sweep, so it cannot be reset by an unrelated
                        # transcript write.
                        bkey = "apierr-busypane:" + key
                        b = state.get(bkey) or {"first_seen": int(now - idle), "pinged": False}
                        b["last_seen"] = int(now)
                        state[bkey] = b
                        snippet = (boundary_txt or "")[:40]
                        logs.append("skip busy-pane (api-error) %s [%s txt=%r]"
                                    % (project or pid, boundary_kind, snippet))
                        if not b["pinged"] and (now - b["first_seen"]) > 2 * grace:
                            b["pinged"] = True
                            logs.append("busy-pane-wedged (api-error) %s [%s] idle=%dm "
                                        "— ping only (never type)"
                                        % (project, key, int(idle // 60)))
                            send_fn("\U0001f6d1 **%s** — API chyba (529/…) drží pane už "
                                    "%d min, no pane vyzerá zaneprázdnená (%s) — nepíšem "
                                    "do nej klávesy, over ju prosím ručne."
                                    % (project, int(idle // 60), boundary_kind),
                                    owner=owner,
                                    dedup_key="apierr-busypane:%s:%s" % (key, b["first_seen"]),
                                    dry_run=dry_run)
                        continue
                # (`stalled.add(key)` now happens above, right after `if err_text:`
                # — see the #175 F1 comment there. Left uncalled here on purpose.)
                err_hash = _hash(err_text)
                # captured BEFORE decide() mutates state — the one-shot give-up ping
                # (#175) fires on the False->True transition of entry["escalated"],
                # never on its raw value (which stays True on every later call).
                prev = state.get(key) or {}
                prev_escalated = bool(prev.get("escalated")) if prev.get("hash") == err_hash else False
                # seed first_seen with now-idle so an already-stale stall counts from
                # when it really began (idle = age of the last transcript write).
                action, entry = decide(state, key, err_hash, now, grace, interval,
                                       max_nudges, first_seen_seed=now - idle)
                # first_seen in the dedup key so a recover→re-stall still pings
                # (notify's own dedup TTL is 14 days).
                fs = int(entry.get("first_seen", now))
                if action == "nudge" and is_usage_cap(err_text):
                    # quota USAGE cap — time-based, `continue` can't fix it. Ping ONCE,
                    # mark dormant (decide() then returns 'wait' for this hash while the
                    # entry survives — #175's widening back-off does NOT apply here: only
                    # the external reset clock fixes a quota cap, never re-nudging). This
                    # is RE-DERIVED from is_usage_cap(err_text) on every sweep that reaches
                    # a fresh 'nudge', not solely remembered in the stored flag (#175 F4) —
                    # so even if the cleanup pass below ever drops this entry (the
                    # session's pane no longer visible, say), the next rebuild
                    # reclassifies it dormant again from the SAME live error text; no
                    # `continue` leaks into a capped session either way — only a
                    # redundant re-ping is possible (see decide()'s own docstring). No
                    # pane interaction at all here, so draft_pending is irrelevant.
                    #
                    # #336: this is job 6's OWN `sesslimit:<key>` tracking, seeded
                    # straight from THIS detection (the transcript's own error TEXT)
                    # — never from the pane, never from the frozen tmux statusline.
                    # `decide()` only reaches `action == "nudge"` ONCE per distinct
                    # err_hash (every later poll for the SAME error returns "wait",
                    # dormant), so this seed fires exactly once per genuinely new
                    # limit hit — a fresh error later (a real NEW hash) re-parks
                    # cleanly through this same path. Job 6's own widened gate (see
                    # its comment, above in this same loop) then manages the parked
                    # episode exactly like a pane-visible one — ping/wait/bounded
                    # retry-after-reset/give-up, respecting the user-stop invariant
                    # — even though `pane_session_limited` may never once fire for
                    # this pane (the montalu2 incident: a background Agent/subagent
                    # died on the limit, and the top-level pane never rendered the
                    # banner as its own bottom-most content at all).
                    sk = "sesslimit:" + key
                    if sk not in state:
                        # `last_seen` set explicitly (unlike job 6's own creation
                        # branch, which sets it right afterward, unconditionally,
                        # a few lines below in THIS SAME function) — job 1's seed
                        # is a standalone insert with no such follow-up statement,
                        # and the end-of-sweep generic cleanup pass treats a
                        # missing `last_seen` as epoch 0, i.e. "ancient", deleting
                        # a freshly-seeded entry on the VERY SAME sweep it was
                        # created.
                        state[sk] = {"resets_at": parse_reset_epoch_from_error_text(err_text, now),
                                    "pinged": True,      # the usage-cap ping just below covers it
                                    "continued": False, "first_seen": int(now),
                                    "last_seen": int(now),
                                    "attempts": 0, "gave_up": False}
                    entry["nudges"], entry["escalated"], entry["dormant"] = [], True, True
                    state[key] = entry
                    logs.append("usage-cap %s — ping only, no continue" % project)
                    # #336 (adversarial review, messaging drift): the ping text
                    # must reflect what job 6's own widened tracking actually
                    # promises — a resume time it genuinely parsed means the
                    # watchdog itself WILL deliver `continue` after the reset
                    # (never "CC sa obnoví" on its own); a clock it could NOT
                    # parse means the episode is only pinged, never auto-
                    # resumed, and the user should intervene manually.
                    if state.get(sk, {}).get("resets_at"):
                        resume_note = ("\n> (usage cap — po resete pošlem `continue` "
                                       "automaticky — nič nemusíš robiť)")
                    else:
                        resume_note = ("\n> (usage cap — `continue` teraz nepomôže a "
                                       "čas resetu sa nepodarilo rozpoznať z chybovej "
                                       "hlášky — obnov session prosím ručne po resete)")
                    send_fn(compose_api_error_alert(project, err_text) + resume_note,
                            owner=owner, dedup_key="apierr:%s:%s:%s" % (key, err_hash, fs), dry_run=dry_run)
                elif action == "nudge":
                    n = len(entry["nudges"])
                    if draft_pending:
                        # #176 item 1: idle-with-a-draft delivers via the VERIFIED
                        # stash protocol, never a raw keystroke over the user's own
                        # text. An ABORTED stash (#176 item 5) must NOT burn a
                        # retry — `state[key]` stays untouched (the pre-decide()
                        # value) and the next poll re-derives from scratch.
                        #
                        # #176 F3: `captured` is the TOP-OF-SWEEP capture — job 10
                        # (prompt_wedge_check, above) already had a chance to send
                        # real keystrokes into this exact pane (a recognized MACHINE
                        # draft gets auto-submitted) BEFORE job 1 ever reaches this
                        # line, so `captured` can be stale by the time we're about to
                        # act. Re-verify against a FRESH capture right here (job 20's
                        # own pattern for the identical race) instead of trusting it —
                        # a pane that moved since the sweep started is skipped WITHOUT
                        # burning a retry, exactly like an aborted stash below.
                        fresh = capture_pane(pid, run, lines=30)
                        fkind, ftxt = _classify_boundary(fresh)
                        if fkind != "input":
                            # #189: this used to also require `ftxt` to be
                            # non-empty, i.e. it re-asked the unanswerable
                            # "does the box really hold something?" a second
                            # time, one line before the send. A BARE input
                            # line is perfectly deliverable — `deliver_with_stash`
                            # now parks unconditionally and treats an empty box
                            # as the no-op it is. What still matters is only
                            # that the boundary IS an input line: a spinner or
                            # dialog appearing here is the genuine race, and
                            # typing into it is the #233 scar.
                            # #176 REOPENED R2: this "raced" mismatch used to
                            # `continue` bare — no state, no ping, no bound —
                            # a THIRD stateless skip path, structurally the
                            # same shape this ticket removed from the busy
                            # and stash-abort branches. It shares the SAME
                            # `apierr-stashabort:` episode as an aborted
                            # stash (below): both mean "delivery could not be
                            # verified for this pane, this poll", the same
                            # granularity the eventual ping's reason text
                            # already distinguishes.
                            _apierr_stashabort_skip(
                                state, logs, send_fn, project, pid, key, now,
                                idle, grace, "raced: pane moved since the "
                                "sweep's own top-of-sweep capture", owner,
                                dry_run, "skip raced (api-error stash)")
                            continue
                        _logs_before = len(logs)
                        delivered = True if dry_run else deliver_with_stash(
                            pid, NUDGE_TEXT, run, captured=fresh, logs=logs)
                        if not delivered:
                            # #176 F1: the shipped fix RELOCATED the silent unbounded
                            # skip from the busy branch to HERE instead of eliminating
                            # it — an occupied stash slot or a live-turn abort left
                            # this branch with no state, no ping, no bound (the
                            # gatekeeper incident's exact failure signature, just moved).
                            # Bound it the SAME way as the busy branch above: a
                            # DEDICATED state prefix (never job 4's/the busy branch's
                            # own prefixes — three independent episodes must never share
                            # bookkeeping for the same session), exactly one deduped ping
                            # per episode past 2x grace of WALL CLOCK, the abort reason
                            # named in the ping text.
                            reason = (logs[-1] if len(logs) > _logs_before
                                     else "stash-abort: unknown")
                            _apierr_stashabort_skip(
                                state, logs, send_fn, project, pid, key, now,
                                idle, grace, reason, owner, dry_run,
                                "skip stash-abort (api-error)")
                            continue
                        state[key] = entry
                        logs.append("nudge#%d %s [%s] (stash)" % (n, project, key))
                    else:
                        state[key] = entry
                        logs.append("nudge#%d %s [%s]" % (n, project, key))
                        if not dry_run:
                            send_continue(pid, NUDGE_TEXT, run)
                    if n == 1:                 # first nudge → tell the user it stalled
                        send_fn(compose_api_error_alert(project, err_text),
                                owner=owner, dedup_key="apierr:%s:%s:%s" % (key, err_hash, fs), dry_run=dry_run)
                    # (#175) one-shot "gave up" ping the moment the (max_nudges+1)-th
                    # nudge is due — nudging itself NEVER stops; only this ping is
                    # one-shot per err_hash.
                    if entry.get("escalated") and not prev_escalated:
                        logs.append("escalate %s [%s] — still stuck after %d nudges, "
                                     "backing off (keeps retrying)" % (project, key, max_nudges))
                        body = ("\U0001f6d1 **%s** — API chyba pretrváva\n> Po %d× `continue` sa to "
                                "stále nepohlo — treba zásah. (Skúšam ďalej, interval sa "
                                "postupne predlžuje až na 30 min.)" % (project, max_nudges))
                        send_fn(body, owner=owner, dedup_key="apierr-giveup:%s:%s:%s" % (key, err_hash, fs),
                                dry_run=dry_run)
                else:
                    state[key] = entry
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
                # (#352) LIVE-SHELL EVIDENCE — the pane's OWN footer already
                # shows CC's live background-shell/monitor badge (a bounded
                # `run_in_background` Bash task genuinely still executing).
                # This is PROOF of life the session itself never had to be
                # asked to produce — skip the WHOLE working-stall check
                # (neither the busy-pane ping below nor the working: keystroke
                # nudge further down) while it persists. Refresh `last_seen`
                # on whichever episode key is already tracked so the
                # end-of-sweep cleanup (which prunes a `working:`/`busypane:`
                # entry after `wait_clear` of no touch) does not wipe its
                # nudge/backoff history mid-skip. Re-evaluated fresh every
                # sweep, never a one-way door: the instant the badge is gone
                # (the shell died), the very next sweep resumes normal
                # nudge/escalate flow with no special reset required. (F3,
                # round-1 review) The badge alone is trusted only for
                # LIVE_SHELL_TRUST_CAP_S of CONSECUTIVE skipping — a
                # genuinely wedged CC process whose last render happens to
                # show the badge would otherwise be trusted FOREVER; past
                # the cap the streak resets and this ONE sweep falls through
                # to the normal flow for a real re-check.
                lsk = "liveshell:" + key
                if _pane_live_shell_evidence(captured):
                    ls = state.get(lsk)
                    if not isinstance(ls, dict):
                        ls = {"first_skip": int(now)}
                    if (now - ls["first_skip"]) < LIVE_SHELL_TRUST_CAP_S:
                        ls["last_seen"] = int(now)
                        state[lsk] = ls
                        for _pfx in ("working:", "busypane:"):
                            _lk = _pfx + key
                            if isinstance(state.get(_lk), dict):
                                state[_lk]["last_seen"] = int(now)
                        logs.append("skip live-shell (working-stall) %s [%s]"
                                    % (project, key))
                        continue
                    state.pop(lsk, None)
                    logs.append(
                        "live-shell trust cap exceeded (working-stall) %s [%s] "
                        "— forcing a real re-check" % (project, key))
                else:
                    state.pop(lsk, None)
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

    # Sudo-hosted stream panes (generic: a claude pane whose transcript lives
    # under a FOREIGN home falls out of the loop above) — yet it is reachable
    # ONLY from here, since the hosted user's own watchdog has no tmux server
    # at all (2026-07-21 incident: a montalu Discord answer starved, invisible
    # to both sides — montalu ran this way until the 2026-07-24 subdev
    # migration gave it its own tmux, #33/#34; no live pane matches this shape
    # today, kept generic for the next shared-tmux stream). Bind each to its
    # foreign session id so job 7 delivers replies into it and job 10 catches
    # wedged prompts.
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
            owner = stream_redirect(pane_owner(pid, run))  # #212: STREAM_NOTIFY_OWNER-aware
            if owner:
                owner_by_sid.setdefault(sid, owner)
            # waiting=True: the FOREIGN transcript isn't cheaply readable from
            # here (a different HOME) — stay eligible-by-default rather than
            # silently going quiet for every hosted pane (issue #35).
            #
            # Adversarial-review finding (post-#177, MINOR, no live victim):
            # `f_mtime` here is STILL the raw foreign-transcript mtime — the
            # SAME #177 non-turn-write staleness bug the local-pane call
            # site above was fixed for is knowingly retained here, because
            # `_last_real_turn_ts` needs to read the transcript's own
            # CONTENT and this transcript lives under a different user's
            # HOME (a `sudo -n` read, not a cheap local one). No hosted
            # pane exists on any managed box today (#34) — if this shape
            # returns, give it its own sudo-based content read rather than
            # silently trusting raw mtime forever.
            logs += prompt_wedge_check(now, state, pid, captured, f_mtime,
                                       owner, project_label(cwd) + "-" + fu,
                                       send_fn, dry_run=dry_run, run=run,
                                       waiting=True)
        except Exception as e:
            logs.append("skip hosted pane %s: %r" % (pid, e))

    # One-shot cleanup of the REMOVED restart jobs' state (#132). These eight
    # named stores are on every managed box's state file with no writer and no
    # reader left; the generic cleanup below deliberately refuses to touch a
    # NAMED job store (deleting one starved the ticket-fallback, 2026-07-21),
    # so they would otherwise persist forever and read as live tracking to the
    # next person opening the file. Harmless but confusing — drop them once.
    for k in ("modelswitch", "modelswitch_pending", "modelswitch_attempts",
              "hooks_session_hash", "hooks_restarted", "hooks_restart_attempts",
              "modelgen_restarted", "modelgen_restart_attempts"):
        if k in state and not dry_run:
            del state[k]

    # Cleanup. api-error keys (no prefix): drop the moment the session recovers.
    # wait: keys: drop only after the footer has been absent for WAIT_CLEAR seconds
    # (the episode is genuinely over / the prompt was answered) — tolerating a
    # single missed poll so the same open prompt is never pinged twice.
    for k in list(state.keys()):
        if k == "usage":
            continue                       # account-wide usage state, not a session
        if (k.startswith("pwedge:") or k.startswith("pwedge-ping:")
                or k.startswith("pwedge-submit-attempts:")
                or k.startswith("pwedge-submit-giveup:")):
            # Job 10's episode + ping-cooldown + (#255) submit-escalation +
            # give-up-ping state is keyed by PANE ID (tmux target), never a
            # transcript session id — a DIFFERENT identity space from every
            # other prefix below (all keyed by session id, aged via
            # `last_seen` or membership in `stalled`). pwedge state carries
            # no timestamp field to age by in the first place, so a pane id
            # not among THIS sweep's live_pane_ids is dropped outright; a
            # still-live pane's entry is left completely untouched, however
            # stale it looks.
            #
            # (adversarial-review finding on #199): `live_pane_ids` being
            # EMPTY is NOT proof every pane died — `list_claude_panes`
            # degrades to `[]` on ANY tmux read failure (`_default_run`'s
            # own bare `except Exception: return ""`), so an empty set here
            # is exactly as likely to mean "this sweep could not see tmux at
            # all" as "nothing is running". Conflating the two would wipe
            # every pwedge entry fleet-wide on one transient hiccup — skip
            # pruning entirely rather than guess.
            if not live_pane_ids:
                continue
            if k.startswith("pwedge-ping:"):
                prefix = "pwedge-ping:"
            elif k.startswith("pwedge-submit-attempts:"):
                prefix = "pwedge-submit-attempts:"
            elif k.startswith("pwedge-submit-giveup:"):
                prefix = "pwedge-submit-giveup:"
            else:
                prefix = "pwedge:"
            if k[len(prefix):] not in live_pane_ids:
                del state[k]
        elif (k.startswith("subagent-apierr:") or k.startswith("subagent-textcall:")
                or k.startswith("subagent-busypane:")):
            # (#287) These track a SPECIFIC DEAD SUBAGENT's own triage state —
            # unlike every other episode key in this loop, visibility into it
            # depends on the SUPERVISOR's own marker happening to read
            # `⏳ WORKING` at poll time (the outer
            # `transcript_last_marker(tpath) == "⏳"` gate in
            # `_nudge_dying_subagent`'s two call sites), which a busy /goal
            # loop working OTHER tickets steps away from routinely (a
            # completed ticket's own `✅ DONE` turn) for well over
            # `wait_clear`'s 90s. The generic wait_clear-based branch below
            # used to WIPE this state on every such gap, so a session
            # alternating through several other tickets re-sent an
            # already-nudged dead worker's nudge as a fresh "nudge#1" every
            # time the gate reopened — the observed "same nudge delivered
            # forever" (#287; the previously-diagnosed
            # `decide_working(responded=True)` cause does NOT apply here —
            # `_nudge_dying_subagent` never passes `responded=`, so that
            # branch is unreachable for this wkey; verified against the
            # current source before this fix, see the tracking issue).
            # A dead worker's own status does not need re-confirming every
            # 90s of supervisor busyness — bound it instead by the SAME
            # ceiling the outer gate already uses to decide whether to even
            # look at the worker (see SUBAGENT_NUDGE_STATE_TTL_SECONDS's own
            # docstring for why this can never fire prematurely).
            if int(now) - state[k].get("last_seen", 0) > SUBAGENT_NUDGE_STATE_TTL_SECONDS:
                del state[k]
        elif (k.startswith("working:") and isinstance(state.get(k), dict)
                and state[k].get("nudges") and not state[k].get("escalated")):
            # (#352 F2, adversarial review round 2, finding 1) round-1's own
            # fix gated this carve-out on `answered` — organically
            # UNREACHABLE: `answered` is set ONLY inside decide_working's
            # `responded=True` branch, which itself needs THIS SAME entry
            # to have already survived one full regrowth gap under the OLD
            # 90s-only rule before `answered` can ever be written at all.
            # A first-cycle entry (nudged once, not yet re-checked) can
            # NEVER acquire `answered` before the old carve-out prunes it
            # first — so the whole 1h/3h/6h staged schedule stayed dead
            # code for every real session, proven live (a bare nudge#1,
            # answered within 60s, pruned at 180s, fresh nudge#1 again at
            # regrowth — `answered` never gets set, forever).
            #
            # Key it on `nudges` instead — present the instant a wedged
            # episode is FIRST nudged, not only once it has been re-checked
            # — plus `not escalated`. The `not escalated` half is
            # load-bearing on its own: WITHOUT it, an already-escalated
            # entry surviving past the generic 90s window into a brand-new
            # stall (same session, same wkey) would become IMMORTAL under
            # decide_working's own unconditional top-of-function
            # `last_seen` refresh (it fires even on the
            # `escalated -> "noop"` early return) — and, worse,
            # decide_working's own `if e.get("escalated"): return "noop"`
            # check would then silently swallow the NEW episode's entire
            # nudge ladder, since the key would never again look like a
            # fresh first sighting. An escalated entry therefore keeps
            # using the ORIGINAL generic wait_clear (90s) rule below,
            # unchanged, so a later genuinely-new stall on the same session
            # starts completely fresh.
            #
            # Give the eligible (nudged, not-yet-escalated) entry a TTL
            # comfortably longer than the ONE-TIME regrowth gap (2x
            # `stall_working`, floored at `wait_clear` so it is never
            # SHORTER than the generic rule it replaces) — once
            # decide_working IS reachable again it refreshes `last_seen`
            # every sweep on its own (same as before), so this only ever
            # has to survive the regrowth window, never the schedule's own
            # multi-hour gaps.
            if int(now) - state[k].get("last_seen", 0) > max(2 * stall_working, wait_clear):
                del state[k]
        elif (k.startswith("wait:") or k.startswith("working:") or k.startswith("textcall:")
                or k.startswith("sesslimit:") or k.startswith("busypane:")
                or k.startswith("apierr-busypane:") or k.startswith("apierr-stashabort:")
                or k.startswith("liveshell:")):
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

    # #160-review-style finding 🔵F6 (this ticket's own review) --
    # `state["backlog_cache"]` (one entry per distinct cwd ever measured by
    # `_cached_backlog_open`, shared by jobs 10/20) is a NAMED store, so the
    # flat-key cleanup loop above correctly never touches it (per its own
    # comment: "NEVER cleanup's to delete") -- but nothing else pruned it
    # either, so it grows by one small entry per NEW repo this box ever
    # monitors, forever. A bound well past either TTL keeps normal reads
    # (freshness is already re-checked per-entry on every lookup) unaffected.
    bc = state.get("backlog_cache")
    if isinstance(bc, dict):
        stale_after = 10 * BACKLOG_CHECK_INTERVAL_S
        for k in list(bc.keys()):
            entry = bc.get(k)
            try:
                ts = float(entry.get("ts", 0)) if isinstance(entry, dict) else 0.0
            except (TypeError, ValueError):
                ts = 0.0
            if (now - ts) > stale_after:
                del bc[k]

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
                                     pending_prefix=pending_prefix,
                                     owner_by_cwd=owner_by_cwd, owners_seen=owners_seen)
    except Exception:
        pass

    # --- (7) ROUTE DISCORD REPLIES → the asking session, a ❓/❔ REACTION on a
    # tracked bot message (#297), and a REPLY on a completion card (#298) —
    # only when a fetcher is wired. Best-effort: a Discord/network hiccup must
    # never break the tmux jobs. `cwd_by_sid` (#297/#298): resolves "the
    # nearest live session of repo X" for the flag-fallback / post-reopen
    # nudge — already built above for job 24's own repo read.
    if discord_fetch is not None:
        try:
            logs += deliver_discord_replies(now, run, state, panes_by_sid,
                                            dry_run=dry_run, discord_fetch=discord_fetch,
                                            hosted_users=hosted_users,
                                            cwd_by_sid=cwd_by_sid,
                                            projects_dir=projects_dir,
                                            persist=lambda: save_state(state_path, state))
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

    # #255 (adversarial review, MAJOR finding): jobs 8/9 must NOT reuse the
    # bare `sweep_deadline` above -- that deadline is scoped to the
    # per-transcript PANE LOOP, which runs entirely BEFORE this point and
    # can legitimately consume its whole budget on its own. Reusing it
    # verbatim meant jobs 8/9 got ZERO of the ~30s margin the 90/120 split
    # was always meant to leave them, silently deferring every target/pane
    # at idx=0 whenever the pane loop alone used its full budget -- exactly
    # when a real backlog is most likely to exist. `tail_deadline` extends
    # into that same margin, still comfortably under the 120s hard kill.
    tail_deadline = sweep_deadline + TAIL_BUDGET_S

    # Job 8 — bounce backstop (gatekeeper-returned prio:bounce tickets must
    # never rot after a loop ends). Only when a fetch is wired (cmd_watchdog
    # passes the real one; unit tests of other jobs stay network-free).
    # Cadence-gated internally; best-effort.
    if bounce_fetch is not None:
        try:
            logs += bounce_backstop(
                now, run, state, send_fn, dry_run=dry_run,
                gh_fetch=bounce_fetch, projects_dir=projects_dir,
                persist=lambda: save_state(state_path, state),
                time_fn=time_fn, sweep_deadline=tail_deadline)
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
    # no network). Best-effort. `templates_path=goal_templates_path` (#320
    # shape 2) — wired = on, like job 20's own identical param; without it
    # this job's new virgin-candidate branch is a guaranteed no-op.
    try:
        logs += goal_autoarm(now, run, state, dry_run=dry_run,
                             time_fn=time_fn, sweep_deadline=tail_deadline,
                             templates_path=goal_templates_path)
    except Exception as e:
        logs.append("goal-autoarm error: %r" % (e,))

    # Jobs 12 / 18 / 23 — REMOVED (#132, 2026-07-28). All three drove the
    # same `_restart_pane` helper, which typed `/exit` into a live pane and
    # relaunched the session; on 2026-07-28 it did so twice in three minutes
    # to a session the user was working in. Job 18's premise (CC snapshots
    # its hook set at process start) is measurably FALSE — hooks are re-read
    # per event, so nothing needed restarting. Job 23 compared an API model
    # id against a CLI alias (`claude-opus-5` vs `claude-opus-5[1m]`), a
    # condition no session can ever satisfy, making it an unbounded restart
    # engine. Job 12's remedy auto-downtiered a model the user had chosen,
    # which `model-awareness.md` forbids outright. Nothing in this repo can
    # end a Claude session any more, and tests/test_no_session_kill.py fails
    # if that capability returns.

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

    # #69 — shared per-sweep set: job 14 records every sid it actually
    # compacts THIS sweep. Originally also fed job 15/17 (both REMOVED,
    # #102) to keep them from double-firing on the same pane; job 14 is now
    # this module's only /compact sender that populates it, and job 20
    # still reads it (a session just compacted this sweep is not safe for
    # a fresh keystroke burst either).
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

    # Job 15 — REMOVED (#102, 2026-07-27). Used to fire /compact off context
    # size + idle duration alone; see run_once's own docstring paragraph
    # (15) and the removed section comment above `_pane_compacting`.

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

    # Job 17 — REMOVED (#102, 2026-07-27). Used to fire /compact off a fixed
    # context ceiling alone, regardless of idle duration; see run_once's own
    # docstring paragraph (17) and the removed section comment above
    # `_pane_compacting`.

    # Job 20 — GOAL RE-ARM BACKSTOP (#76): only when `goal_rearm_enabled` is
    # truthy (cmd_watchdog passes True) — same "wired = on" convention as
    # jobs 13/14/16/19, so an existing caller of run_once() that knows
    # nothing about this job sees NO behavior change and never has a pane's
    # goal re-armed by a test. Runs LAST, after every /compact sender, so the
    # shared `compact_handled_this_sweep` set is fully populated before this
    # job decides whether the pane is safe for a ~2 KB keystroke burst.
    # Best-effort.
    if goal_rearm_enabled:
        try:
            # #160-review 🟡F2 — the SAME `tail_deadline` jobs 8/9 already
            # share (never the bare per-transcript-loop `sweep_deadline`,
            # which can legitimately be fully consumed before this point —
            # see the #255 comment on `tail_deadline`'s own definition
            # above).
            logs += goal_rearm(now, run, state, send_fn=send_fn,
                               dry_run=dry_run, projects_dir=projects_dir,
                               handled=compact_handled_this_sweep,
                               sleep_fn=sleep_fn,
                               templates_path=goal_templates_path,
                               backlog_fetch=backlog_fetch,
                               time_fn=time_fn, sweep_deadline=tail_deadline,
                               persist=lambda: save_state(state_path, state),
                               progress_dir=progress_dir)
        except Exception as e:
            logs.append("goal-rearm error: %r" % (e,))

    # Job 21 — LONG-TURN WATCH (#84): only when `long_turn_enabled` is truthy
    # (cmd_watchdog passes True) — same "wired = on" convention as jobs
    # 13/14/16/18/19/20. Detection only, so it is safe to run LAST: it takes
    # no tmux round-trip beyond `_pane_location` for a pane already past the
    # threshold, and it never types anything, so it cannot interact with any
    # sender above it. Best-effort.
    if long_turn_enabled:
        try:
            logs += long_turn_watch(now, run, state, panes_by_sid,
                                    send_fn=send_fn, dry_run=dry_run,
                                    project_by_sid=project_by_sid,
                                    owner_by_sid=owner_by_sid)
        except Exception as e:
            logs.append("long-turn error: %r" % (e,))

    # Job 24 — DELIVERY-STALL WATCH (#138): only when `delivery_probe` is
    # given (cmd_watchdog wires the real fetch + gh lookup) — same
    # "wired = on" convention as jobs 8/11/16, and here it is also the
    # correctness gate: the probe carries the confirming fetch, and a verdict
    # that was never confirmed must not reach the user's phone. Detection
    # only, so it is safe alongside job 21 at the end; it takes no tmux
    # round-trip at all (the cwd map was built during the pane sweep above)
    # and never types anything. Best-effort.
    if delivery_probe is not None:
        try:
            logs += delivery_stall_watch(now, run, state, cwd_by_sid,
                                         send_fn=send_fn, dry_run=dry_run,
                                         delivery_probe=delivery_probe,
                                         owner_by_sid=owner_by_sid,
                                         project_by_sid=project_by_sid)
        except Exception as e:
            logs.append("delivery-stall error: %r" % (e,))

    # Job 25 — CARD RECONCILIATION (#134): the mirror of job 24, same
    # "wired = on" convention and the same confirm-then-announce contract.
    # Detection only, no tmux round-trip (the cwd map came from the pane
    # sweep above). Best-effort — a failure here must never cost a sweep.
    if card_probe is not None:
        try:
            logs += card_reconcile(now, run, state, cwd_by_sid,
                                   send_fn=send_fn, dry_run=dry_run,
                                   card_probe=card_probe,
                                   closed_fetch=closed_fetch,
                                   reopen_fetch=reopen_fetch,
                                   owner_by_sid=owner_by_sid)
        except Exception as e:
            logs.append("card-reconcile error: %r" % (e,))

    # Job 26 — COMPACT-STALL WATCH (#140): only when `compact_stall_enabled`
    # is truthy (cmd_watchdog passes True) — the "wired = on" convention of
    # jobs 13/14/16/19/20/21, and here it also keeps an existing caller of
    # run_once() (every pre-#140 test) from ever reading the REAL
    # ~/.claude/compact-claims.json, which the live systemd watchdog on this
    # box writes every 60s. Detection only, no tmux round-trip: it reads the
    # claims file and the transcripts, and the cwd map came from the pane
    # sweep above. Best-effort — a failure here must never cost a sweep.
    if compact_stall_enabled:
        try:
            logs += compact_stall_watch(now, state, cwd_by_sid,
                                        send_fn=send_fn, dry_run=dry_run,
                                        projects_dir=projects_dir,
                                        owner_by_sid=owner_by_sid,
                                        project_by_sid=project_by_sid)
        except Exception as e:
            logs.append("compact-stall error: %r" % (e,))

    # Job 27 — NET-ISSUE-DRIFT ALARM (#137): only when `issue_counts_fetch`
    # is given (cmd_watchdog wires the real gh round trip) — the "wired = on"
    # convention. Self-gated on an hourly cadence internally; best-effort.
    # `persist=` (#172, extended #172 REOPENED F3/F4): the cadence marker
    # reaches DISK before `repo_roots()` even runs, the repo-batch cursor
    # before the per-repo loop, and the per-repo dedup memory before EACH
    # ping — so a systemd TimeoutStartSec kill anywhere in this job costs at
    # most `REPO_SWEEP_BATCH_MAX` repos' worth of stale data (see that
    # constant's own comment for the real, per-repo bound — NOT "one hour"),
    # never an unbounded livelock and never a lost dedup entry.
    if issue_counts_fetch is not None:
        try:
            logs += net_drift_alarm(now, state, send_fn=send_fn,
                                    dry_run=dry_run, repo_roots=repo_roots,
                                    issue_counts_fetch=issue_counts_fetch,
                                    persist=lambda: save_state(state_path, state))
        except Exception as e:
            logs.append("net-drift error: %r" % (e,))

    # Job 28 — STUCK-MAIN SWEEP (#137): only when `repo_roots` is given —
    # the "wired = on" convention. Self-gated on an hourly cadence
    # internally; best-effort. Independent of job 27's own gate (a repo
    # sweep with no gh access can still measure this locally). `persist=`
    # (#172, extended #172 REOPENED F3/F4): same reasoning as job 27 — one
    # `git fetch` timeout at a time, dedup memory persisted before the ping,
    # cadence marker persisted before `repo_roots()` runs. #172 REOPENED F5:
    # a fetch failure now SKIPS the repo this sweep rather than measuring on
    # refs that may be stale.
    if repo_roots is not None:
        try:
            logs += stuck_main_sweep(now, state, send_fn=send_fn,
                                     dry_run=dry_run, repo_roots=repo_roots,
                                     git_fetch=git_fetch,
                                     persist=lambda: save_state(state_path, state))
        except Exception as e:
            logs.append("stuck-main error: %r" % (e,))

    # Job 22 — STALE EXEC-MARKER CLEANUP (#97): ALWAYS wired (no gating
    # param — same "always on" shape as jobs 9/15/17, since it depends on
    # nothing external beyond /tmp + the pane list already available this
    # sweep). Pure hygiene, never security-critical (a marker is matched by
    # session id, so a stale one is already inert) — best-effort.
    try:
        logs += cleanup_stale_exec_markers(now, run=run,
                                           projects_dir=projects_dir,
                                           dry_run=dry_run)
    except Exception as e:
        logs.append("exec-marker-cleanup error: %r" % (e,))

    # Job 29 — HOURLY CREDENTIAL-STORE SWEEP (#144): only when `vault_purge`
    # is given (cmd_watchdog passes filedrop.vault.purge). Best-effort and
    # internally hour-gated; deletes only what is already past its own TTL.
    if vault_purge:
        try:
            logs += vault_purge_job(now, state, purge_fn=vault_purge,
                                    dry_run=dry_run)
        except Exception as e:
            logs.append("vault-purge error: %r" % (e,))

    save_state(state_path, state)
    return logs
