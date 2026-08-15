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

import hashlib
import json
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


def _pane_live_task_count(captured):
    """Sum of CC's own live background-shell/monitor badge counts (`⏵⏵ … ·
    N shells` / `· M monitors`) read from the pane's CURRENT trailing chrome
    (#365) -- the counting sibling of `_pane_live_shell_evidence` above:
    that function only answers "is the badge showing at all", this answers
    "how many background Bash tasks does it claim". Reuses the IDENTICAL
    bounded peel-walk (never scans quoted scrollback above the chrome
    boundary -- the same #352 F1 lesson). Returns 0 when the badge is
    absent or unparseable -- never a guess, and never negative."""
    lines = str(captured or "").splitlines()
    i = len(lines)
    n = 0
    total = 0
    while i > 0 and _is_bottom_chrome(lines[i - 1].strip()) and n < 40:
        i -= 1
        n += 1
        s = lines[i].strip()
        if s.startswith("⏵⏵"):
            for m in _LIVE_BG_TASK_RX.finditer(s):
                try:
                    total += int(m.group(0).split()[0])
                except (ValueError, IndexError):
                    continue
    return total


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

# #433 item G step 1 -- the pure transcript / session-file readers that used to
# live here (block 395-863) now live in `watchdog/transcripts.py`, re-exported in
# place with the same positional-facade convention as the cluster-B blocks below,
# so every `watchdog.<name>` seam (goal / compact / cross_stream / janitor, hooks,
# tests) resolves unchanged. `_SENTINELS` (bound above) stays in __init__ until a
# later step and is imported by transcripts.py at its module top.
from watchdog.transcripts import (  # noqa: E402
    encode_project_dir as encode_project_dir,
    find_active_transcript as find_active_transcript,
    _iter_jsonl_tail as _iter_jsonl_tail,
    _entry_text as _entry_text,
    transcript_last_error as transcript_last_error,
    transcript_current_context as transcript_current_context,
    transcript_last_marker as transcript_last_marker,
    transcript_last_marker_line as transcript_last_marker_line,
    transcript_last_assistant_text as transcript_last_assistant_text,
    subagent_active as subagent_active,
    _count_live_subagents as _count_live_subagents,
    newest_subagent_transcript as newest_subagent_transcript,
    _entry_has_tool_use as _entry_has_tool_use,
    _ends_with_toolcall as _ends_with_toolcall,
    transcript_text_toolcall_stall as transcript_text_toolcall_stall,
    _hash as _hash,
    _stream_user as _stream_user,
    project_label as project_label,
    _MARKER_RX as _MARKER_RX,
    SUBAGENT_MAX_AGE_SECONDS as SUBAGENT_MAX_AGE_SECONDS,
    SUBAGENT_NUDGE_STATE_TTL_SECONDS as SUBAGENT_NUDGE_STATE_TTL_SECONDS,
    _TEXTCALL_RX as _TEXTCALL_RX,
    _TOOLCALL_BLOCK_RX as _TOOLCALL_BLOCK_RX,
    _TOOLCALL_MARKUP_AFTER_RX as _TOOLCALL_MARKUP_AFTER_RX,
    _GENERIC_DIRS as _GENERIC_DIRS,
)


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
    #335's own general user-stop invariant (`_goal_was_cleared_by_user`,
    itself deleted along with the rest of the heuristic re-arm machinery
    by #403 rather than ever reconciled with this function — the two
    stayed independent implementations of a similar idea for their whole
    overlapping lifetime; #336's own auto-resume mechanism never depended
    on that reconciliation landing).

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


# #433 item G step 2 -- the tmux shims (the only impure part; injectable as
# `run`) that used to live here (block 1000-1430) now live in
# `watchdog/tmux_io.py`, re-exported in place with the same positional-facade
# convention as the earlier steps, so every `watchdog.<name>` seam (capture_pane
# / pane_in_mode / send_continue / pane_owner / list_claude_panes / _default_run
# / the sudo-host + socket-recover + subagent-nudge helpers -- goal / compact /
# cross_stream / janitor, hooks, tests) resolves unchanged. NUDGE_TEXT /
# WORKING_NUDGE_TEXT stay in __init__ (bound above) and are imported by
# tmux_io.py at its module top (C4).
from watchdog.tmux_io import (  # noqa: E402
    _default_run as _default_run,
    _proc_read as _proc_read,
    _pane_hosted_claude_pid as _pane_hosted_claude_pid,
    _hosted_claude_cwd as _hosted_claude_cwd,
    _tmux_default_socket_path as _tmux_default_socket_path,
    _tmux_socket_missing as _tmux_socket_missing,
    _tmux_server_pids as _tmux_server_pids,
    _tmux_socket_recover as _tmux_socket_recover,
    list_claude_panes as list_claude_panes,
    pane_in_mode as pane_in_mode,
    capture_pane as capture_pane,
    pane_owner as pane_owner,
    _strip_selected as _strip_selected,
    send_continue as send_continue,
    send_selfcheck as send_selfcheck,
    send_subagent_nudge as send_subagent_nudge,
    _subagent_transcript_unsalvageable as _subagent_transcript_unsalvageable,
    _nudge_dying_subagent as _nudge_dying_subagent,
)


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

# --- #449: the NEVER-SILENT floor for owner answer attempts ---------------- #
# How long job 7 keeps fetching a QUESTION channel after its last live/grace
# entry vanished (state["dreply_channels"], {channel: {"ts", "q"}}). This is
# what lets the orphan floor below see a reply that lands AFTER the grace
# window (or against an empty map — the david 2026-08-13 incident state):
# without a tracked entry the channel set used to be empty and job 7 returned
# before fetching anything at all, so the loss left zero journal lines.
DREPLY_CHANNEL_MEMORY_S = 7 * 24 * 3600
# Only messages younger than this (by their own snowflake timestamp) can
# trigger the orphan ping — the 25-message fetch window routinely contains
# ancient history (feature rollout, a long-idle thread), and pinging about a
# weeks-old message the user has long moved past is pure noise.
ORPHAN_ANSWER_WINDOW_S = 48 * 3600
_DISCORD_EPOCH_MS = 1420070400000


def _snowflake_ts(sid):
    """Epoch seconds encoded in a Discord snowflake id; 0 when unparsable
    (a non-numeric test id, garbage) — the caller then skips the orphan
    ping rather than guessing the message's age."""
    s = str(sid or "").strip()
    if not s.isdigit():
        return 0
    return ((int(s) >> 22) + _DISCORD_EPOCH_MS) / 1000.0


def _orphan_answer_reason(msg, allowed_ids, qmap, cardmap, question_channels,
                          channel, now):
    """Classify `msg` as an owner ANSWER ATTEMPT that can no longer be routed
    (#449) — returns "untracked-ref" (an explicit reply to a message we no
    longer track: pruned past grace, superseded past grace, or simply never
    ours) or "not-a-reply" (a plain message in a QUESTIONS thread — the
    security gate requires an explicit reply, so it can never route), else
    None. Pure and deliberately NARROW:

    - scoped to QUESTION channels only (the per-owner `-q` threads, #296 —
      every bot message there is a ❓, so an unmatched owner message there is
      near-certainly a lost answer; the main thread's cards/✅ chatter is
      excluded, the card-reopen flow owns replies there);
    - owner-authored, usable text, recent (ORPHAN_ANSWER_WINDOW_S via the
      message's own snowflake — an unparsable id reads as too old, skip);
    - a reply to another HUMAN's message (referenced_message present with a
      non-bot author) is conversation, never an answer attempt — stay quiet.
      A missing/deleted referenced_message stays classified as an orphan:
      in a questions thread the referenced message was almost certainly our
      own ❓, and the never-silent mandate prefers a rare extra ping over a
      silent loss."""
    if channel not in question_channels or not isinstance(msg, dict):
        return None
    mid = str(msg.get("id") or "").strip()
    author_id = str((msg.get("author") or {}).get("id") or "").strip()
    if not mid or author_id not in allowed_ids:
        return None
    if not clean_reply_text(msg.get("content")):
        return None
    sts = _snowflake_ts(mid)
    if not sts or now - sts > ORPHAN_ANSWER_WINDOW_S:
        return None
    ref = str((msg.get("message_reference") or {}).get("message_id") or "").strip()
    if not ref:
        return "not-a-reply"
    if ref in qmap or ref in cardmap:
        return None                    # tracked — the normal flows own it
    rm = msg.get("referenced_message")
    if isinstance(rm, dict):
        rauthor = rm.get("author")
        # Only a GENUINELY human-shaped author (a real id, bot falsy) reads
        # as chatter (#449-review F5: an author-less/degenerate dict must
        # fail toward the orphan ping, per the never-silent mandate — the
        # opposite of what a bare `(rm.get("author") or {}).get("bot")`
        # falsy-check gives).
        if (isinstance(rauthor, dict) and rauthor.get("id")
                and not rauthor.get("bot")):
            return None                # human-to-human chatter, not an answer
    return "untracked-ref"


def _orphan_ping_text(msg, reason):
    """The Slovak owner ping for an unroutable answer attempt (#449) — the
    user must never learn about a lost answer only from silence."""
    frag = clean_reply_text((msg or {}).get("content"))[:120] or "(bez textu)"
    if reason == "not-a-reply":
        return ("⚠️ Tvoja správa na Discorde («%s») nie je Reply na konkrétnu "
                "❓ otázku, takže sa nedá bezpečne priradiť k session. "
                "Odpovedz prosím cez Reply priamo na ❓ kartu, alebo odpoveď "
                "napíš do terminálu." % frag)
    return ("⚠️ Tvoja Discord odpoveď («%s») sa už nedá priradiť — pôvodná "
            "otázka medzitým prestala byť sledovaná (zodpovedaná v termináli "
            "alebo nahradená novšou). Ak stále platí, odpovedz prosím na "
            "NAJNOVŠIU ❓ otázku v tomto vlákne, alebo ju napíš priamo do "
            "terminálu." % frag)


def _merge_grace_questions(qmap, now, dry_run, logs):
    """#449: merge the GRACE store into `qmap` for reply matching, expiring
    entries past QUESTION_GRACE_S as we go (this is the store's ONLY
    consumer, so it owns the lifecycle — the prune deliberately never
    expires, so a bare run_once test without a Discord fetcher never
    touches the store). A dry-run drops nothing (#304 discipline) but still
    merges the same view. Returns the set of ids merged FROM grace so
    `_delivered` drops them from the right store. Residual, documented: a
    HOSTED foreign user's own grace store is not merged (historical shape,
    no live hosted stream today — the never-silent floor still covers it
    via the channel memory)."""
    from notify import (QUESTION_GRACE_S, drop_grace_question,
                        load_grace_questions)
    grace_ids = set()
    for gid, grec in list(load_grace_questions().items()):
        if not isinstance(grec, dict):
            if not dry_run:
                drop_grace_question(gid)
            continue
        gts = grec.get("pruned")
        if isinstance(gts, bool) or not isinstance(gts, (int, float)):
            gts = grec.get("ts")
            if isinstance(gts, bool) or not isinstance(gts, (int, float)):
                gts = 0
        if now - gts > QUESTION_GRACE_S:
            if not dry_run:
                drop_grace_question(gid)
            logs.append("question grace expired — dropped %s [%s]"
                        % (str(gid)[-6:],
                           project_label(str(grec.get("cwd") or ""))))
            continue
        if gid not in qmap:
            qmap[gid] = grec
            grace_ids.add(gid)
    return grace_ids


def _refresh_channel_memory(chan_seen, channels, q_channels, now):
    """#449: expire + refresh the question-channel memory (the shape stored
    in state["dreply_channels"]: {channel: {"ts", "q"}}). Every channel
    currently carrying a tracked entry is re-stamped `now`; a remembered
    channel survives DREPLY_CHANNEL_MEMORY_S past its last sighting, so the
    orphan floor can still SEE an answer that arrives after every entry
    expired. Returns the fresh dict — the CALLER decides whether to persist
    it (never on dry-run, #304) and widens its own fetch/question sets."""
    kept = {}
    for chx, ent in chan_seen.items():
        if not isinstance(ent, dict):
            continue
        cts = ent.get("ts")
        if isinstance(cts, bool) or not isinstance(cts, (int, float)):
            continue
        if now - cts <= DREPLY_CHANNEL_MEMORY_S:
            kept[str(chx)] = {"ts": cts, "q": bool(ent.get("q"))}
    for chx in channels:
        prev_q = bool((kept.get(chx) or {}).get("q"))
        kept[chx] = {"ts": now, "q": prev_q or (chx in q_channels)}
    kept.pop("", None)
    return kept


def _orphan_floor(msg, ch, allowed, qmap, cardmap, q_channels, now, env,
                  dry_run, skip_ids, orphan_done, orphan_done_set,
                  state, persist, logs):
    """#449 NEVER-SILENT floor: an owner's answer attempt that cannot be
    routed (a reply to a message no longer tracked — pruned/superseded past
    grace — or a plain non-reply message in a questions thread the security
    gate can never match) must leave a journal line AND ping the owner,
    never vanish. The journal line is unconditional (the floor even when
    Discord itself is down); the ping is deduped per message id —
    state-marked only on a CONFIRMED send ("sent"/"dedup"), so a transient
    send error retries next sweep, and a dry-run marks nothing (#304)."""
    mid_o = str(msg.get("id") or "").strip() if isinstance(msg, dict) else ""
    if not mid_o or mid_o in skip_ids or mid_o in orphan_done_set:
        return
    reason = _orphan_answer_reason(msg, allowed, qmap, cardmap,
                                   q_channels, ch, now)
    if not reason:
        return
    logs.append("reply orphaned (%s) %s [%s]" % (reason, mid_o[-8:], ch))
    from notify import forget_marker, marker_delivered, send as _send
    dkey = "dorphan:%s" % mid_o
    st_o = _send(_orphan_ping_text(msg, reason), env=env,
                 dedup_key=dkey, dry_run=dry_run, kind="questions")
    # #449-review F2: "dedup" alone is NOT confirmation — notify.send's
    # dedup CLAIM marker is written BEFORE the POST, so a retry after one
    # transient POST failure short-circuits to "dedup" with zero further
    # POSTs. Only marker_delivered (the status recorded AFTER the POST,
    # #135) is the honest read — the #134 marker-presence-vs-delivery
    # trap, avoided at this call site. An undelivered "dedup" claim is
    # additionally RELEASED so the NEXT sweep's send genuinely re-POSTs
    # instead of short-circuiting at the stale claim forever (worst case
    # of the release: one duplicate ⚠️ ping under a rare cross-process
    # race — strictly better than a permanently silent one).
    if not dry_run and (st_o == "sent"
                        or (st_o == "dedup" and marker_delivered(dkey))):
        orphan_done_set.add(mid_o)
        orphan_done.append(mid_o)
        state["dorphan_done"] = orphan_done[-_DREPLY_DONE_CAP:]
        persist()
    elif not dry_run and st_o == "dedup":
        forget_marker(dkey)


from watchdog.pane_text import (  # noqa: E402
    _input_line_text as _input_line_text,
    _classify_boundary as _classify_boundary,
    _above_input_box as _above_input_box,
    _above_box_scan as _above_box_scan,
    _pane_has_queued_compact as _pane_has_queued_compact,
    _trailing_bottom_chrome as _trailing_bottom_chrome,
)


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
    # a `_STOP_FEEDBACK_PREFIX` constant used to carry the identical literal
    # for job 14's own `_stop_already_rejected` gate -- both retired by the
    # #402 compact collapse (that gate's whole premise, an earlier hook in
    # the SAME Stop-hook batch already rejecting the turn, does not apply
    # to compact's two surviving origins). The literal string itself stays
    # duplicated here regardless. Without this entry, a Stop-hook rejection
    # (a routine, real transcript entry -- #108/#109 measured thousands of
    # them corpus-wide) counted as a genuine human prompt and could delay
    # a genuinely-headless virgin arm by up to GOAL_AUTOARM_RECENT_HUMAN_S.
    "Stop hook feedback:",
    # #366 -- GOAL_QUESTION_PARK_TEXT (the "30 min bez odpovede" unanswered-
    # question backstop, ~8000 lines below) is ALSO a machine-typed nudge,
    # not a human answer -- without this entry a session whose ❓ went
    # unanswered 30+ minutes would have that nudge itself misread as "the
    # user answered", pruning the still-pending question entry. #366
    # review MINOR-1: this entry ALSO propagates into the derived
    # `_GOAL_BLOCKED_ANSWER_TRANSPARENT_PREFIXES` tuple below (built FROM
    # this list) -- verified correct for that sibling caller too (a park
    # nudge landing before the user answers a genuine ❓ NEEDS YOU must
    # stay BLOCKED, never read as "released"), locked by its own test in
    # `tests/test_goal_rearm.py`.
    "question-timeout:")

# #366 -- a `/compact` continuation-summary entry (Claude Code's own
# summarizer output at a ticket-boundary compact) is a REAL, top-level
# `user`-typed transcript entry -- neither isMeta nor a tool_result -- so it
# slips past every other transparent check above. It is never a genuine
# typed answer: a session in ask-and-continue mode (❓ ASKED + ⏳ WORKING)
# keeps working OTHER tickets in the same /autopilot run, and completing one
# of them can trigger a compact within minutes of the ❓ ping -- without this
# check, that landing right after the ping misreads as "the user answered"
# and prunes the still-unanswered question entry (the live #366 incident:
# ping delivered, entry gone within minutes, footer shows no Q). Mirrors
# `_goal_blocked_on_unanswered_question`'s own established two-part fix
# (wording prefix here, plus the STRUCTURAL `isCompactSummary` flag checked
# in the loop below so a future summarizer rewording alone can never reopen
# the gap) -- never back-ported here until now, though `_last_human_
# prompt_ts` has drifted behind that sibling classifier before (the
# `"Stop hook feedback:"` gap above). #366 review MINOR-3: the sibling's
# own `_GOAL_BLOCKED_ANSWER_TRANSPARENT_PREFIXES` tuple (~8000 lines
# below) carries the IDENTICAL wording literal inline, defined AFTER this
# constant -- kept as its own duplicate rather than refactored to
# reference this one (touching that already-shipped, reviewed #350 code
# for a purely cosmetic dedup is out of scope here), so it must be kept
# in sync if the wording ever changes. Mitigated in both directions by
# the structural `isCompactSummary` flag check next to each, which does
# not depend on the wording at all.
_COMPACT_CONTINUATION_PREFIX = (
    "This session is being continued from a previous conversation")


def _last_human_prompt_ts(tpath, tail_bytes=2_000_000, extra_human_prefixes=()):
    """Epoch of the NEWEST human-typed prompt in the transcript tail, or None.
    Machine-typed prompts (the list above), tool_result user entries, meta
    entries and a /compact continuation summary don't count — only something
    the USER actually wrote.

    `extra_human_prefixes` (#377-review MINOR-1, optional): every entry in
    `_MACHINE_PROMPT_PREFIXES` NAMED HERE is treated as human-typed instead
    of machine-injected for THIS call only — mirrors #350's own established
    "opposite exclusion set" precedent (`_GOAL_BLOCKED_ANSWER_TRANSPARENT_
    PREFIXES`, ~8000 lines below) for the identical two Discord-relay
    prefixes ("Odpoveď z Discordu:"/"Odpoveď užívateľa na tvoju otázku"):
    this function's OWN default question is "did a human type this
    DIRECTLY" (job 9's `_goal_autoarm_recent_human_activity` needs exactly
    that, unchanged, so its own callers never pass this), while a DIFFERENT
    caller can genuinely need "is the user actively engaging right now" —
    for which a Discord-relayed answer counts just as much as a directly
    typed one (`_compact_recent_human_activity`, #377). The default `()`
    is a complete no-op (`p not in ()` is always True), so every existing
    caller's behavior is byte-for-byte unchanged."""
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
    machine_prefixes = (_MACHINE_PROMPT_PREFIXES if not extra_human_prefixes
                        else tuple(p for p in _MACHINE_PROMPT_PREFIXES
                                  if p not in extra_human_prefixes))
    best = None
    for ln in raw.splitlines():
        try:
            e = json.loads(ln)
        except Exception:
            continue
        if not isinstance(e, dict) or e.get("type") != "user" or e.get("isMeta"):
            continue
        if e.get("isCompactSummary"):
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
                or any(t.startswith(p) for p in machine_prefixes)
                or t.startswith(_COMPACT_CONTINUATION_PREFIX)):
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
    """MOVE question-map entries whose asking session received a HUMAN prompt
    AFTER the ❓ was pinged out of the MAIN map — the question was PRESUMED
    answered at the terminal, so the entry would otherwise linger and
    inflate the statusline 'otazky' badge (user complaint 2026-07-22: 14
    stale questions shown in a project with zero). #368: the map no longer
    has an AGE-based TTL to fall back on either — an entry only ever leaves
    the map here (answered in-session, or superseded by a newer ask of the
    same session+channel — #407), on a routed Discord reply (job 7), or the
    hard cap — so this is the ONLY terminal-answer detection there is.

    #449 (david live incident 2026-08-13): the "any later human prompt =
    answered at the terminal" inference is FALSE for a user who types OTHER
    things at the terminal while answering the actual question on the
    phone. A hard delete here destroyed the only key job 7 can fetch/match/
    route a Discord reply through, so the phone answer was lost with zero
    trace. Entries therefore now move to the GRACE store
    (notify.grace_question — discord-questions-grace.json, 24h window)
    instead of being deleted: the badge still drops IMMEDIATELY (the main
    map is what statusbar + reping read), but job 7 keeps merging grace
    entries for reply matching, so a phone answer inside the window still
    routes normally. Grace EXPIRY is enforced by job 7 at load time — NOT
    here — so a bare run_once test without a Discord fetcher never touches
    the store, and the store's only consumer owns its lifecycle. The old
    "safe by design: the loop re-asks" note only ever covered the re-ask,
    never a reply already sent to the pruned card — that is the gap grace
    closes."""
    from notify import ask_generation, grace_question, load_questions
    logs = []
    try:
        qmap = load_questions()
    except Exception:
        return logs
    # #407: collapse SUPERSEDED duplicates FIRST — per (session, channel)
    # only the NEWEST-ASK entry is the session's current question. A
    # reworded ❓ past the 15-min edit window used to fall through to a
    # fresh POST + record with the OLD entry left tracked forever (no age
    # TTL since #368), so reping_stale_questions re-pinged BOTH daily — an
    # immortal ghost. record_question now supersedes at write time
    # (birth-site, notify/__init__.py); THIS pass reaps the pairs that
    # already exist on the fleet, and it runs BEFORE reping in run_once,
    # so a ghost dies without one further ping. Ordered by ASK GENERATION
    # (notify.ask_generation — `asked` preserved across daily re-tracks,
    # legacy fallback = record ts), never bare record time: a stale
    # sibling's re-post carries a fresh ts, and comparing record times
    # would keep the re-posted OLD ask over the session's LIVE newer
    # question (#407 review MAJOR-1). Channel-scoped exactly like the
    # writer: a DISCORD_MIRROR pair (same session, different channels) is
    # one generation's siblings, never collapsed. No transcript needed —
    # this is a map-shape fact, so it works for sessions the
    # answered-in-session loop below cannot even resolve. Deletion keyed
    # on an ARTIFACT (a duplicate group in the map), never a new
    # suppression that could go silent.
    def _gen_order(gid):
        grec = qmap.get(gid) or {}
        gts = grec.get("ts")
        if isinstance(gts, bool) or not isinstance(gts, (int, float)):
            gts = 0            # legacy/bool ts reads as oldest, never raises
        # Discord snowflakes are time-ordered — on a full tie the larger
        # id IS the later posting (deterministic, never dict order).
        return (ask_generation(grec), gts,
                int(gid) if str(gid).isdigit() else 0)

    groups = {}
    for qid, rec in qmap.items():
        if not isinstance(rec, dict):
            continue
        gsid = str(rec.get("session") or "")
        gchan = str(rec.get("channel") or "")
        if not gsid or not gchan:
            continue
        groups.setdefault((gsid, gchan), []).append(qid)
    for qids in groups.values():
        if len(qids) < 2:
            continue
        qids.sort(key=_gen_order)
        for qid in qids[:-1]:
            gcwd = str((qmap.get(qid) or {}).get("cwd") or "")
            if not dry_run:
                grace_question(qid)     # #449: routable for 24h, never lost
            qmap.pop(qid, None)   # the loop below must not re-process it
            logs.append("question superseded by a newer ask — pruned %s [%s]"
                        " (grace)" % (str(qid)[-6:], project_label(gcwd)))
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
                grace_question(qid)     # #449: routable for 24h, never lost
            logs.append("question answered in-session — pruned %s [%s]"
                        " (grace)" % (str(qid)[-6:], project_label(cwd)))
    return logs


# #368 -- an unanswered ❓ is re-asked FRESH AND WHOLE at least once a day
# instead of the map's old age-based TTL silently deleting it (see the
# module comment above notify's own question-map constants for the user's
# own directive this responds to). One question per QUESTION_REPING_S
# window, via the SAME bucketed-dedup-key shape every other daily-reping
# job in this file already uses (`delivery_stall_watch`/the net-drift
# alarm/the gk-request backstop's own staged schedule):
# `dedup_key="...:%d" % (now // reping)` — a retry within
# the SAME day-bucket is deduped by notify.send()'s own marker (the "at
# most ONE sanctioned daily re-ask" cap, with zero new bookkeeping needed),
# while a genuinely NEW day always gets a fresh key. This is a completely
# different, session-turn-scoped dedup layer than LASTQ (the "verbatim
# repeat within one still-blocked turn" mechanism) — untouched by any of
# this.
QUESTION_REPING_S = 24 * 3600


def _in_sleep_window(now, tz="Europe/Bratislava"):
    """True during the 00:00-05:59 Europe/Bratislava sleep window a question
    re-ask must defer past (message-status-marker.md's night-question
    policy — applied here in code because nothing SESSION-side enforces it
    for a question the session itself may never revisit, #368). Fail-safe:
    an unresolvable clock/tz never blocks a re-ask forever, it just answers
    "not asleep" — a question must still eventually be asked, never
    silently parked on a tz error."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        hour = datetime.fromtimestamp(now, ZoneInfo(tz)).hour
    except Exception:
        return False
    return 0 <= hour < 6


def reping_stale_questions(now, send_fn, dry_run=False, path=None,
                           owner_by_sid=None, owner_by_cwd=None,
                           owners_seen=None, account_owner="",
                           reping=None):
    """#368 -- see the section comment above QUESTION_REPING_S. For every
    question-map entry whose `ts` is >= `reping` old: skip it (leaving `ts`
    untouched, so the SAME entry is re-evaluated -- and fires -- on the very
    next sweep) while inside the 00:00-05:59 sleep window; otherwise repost
    the WHOLE stored block VERBATIM (`rec["block"]`, falling back to the
    collapsed `rec["question"]` for a pre-#368 legacy entry with no `block`
    field -- never a shortened/summarised form, the same "nanovo a cela"
    discipline a session's own re-ask already follows). Only on a CONFIRMED
    `"sent"` status does the map get touched at all: the freshly-posted
    message id is re-tracked (so a reply to the just-re-asked, phone-visible
    message still routes to the session like job 7 already does for the
    original) and the old map key is dropped. Any other status ("dedup",
    "error", "no-config") leaves the entry exactly as it was, so the next
    sweep retries -- a transient failure must never silently defer a whole
    day's worth of "ask again"."""
    from notify import (ask_generation, grace_question, load_questions,
                        record_question, notification_channel)
    reping = QUESTION_REPING_S if reping is None else reping
    owner_by_sid = owner_by_sid or {}
    owner_by_cwd = owner_by_cwd or {}
    ambiguous = len(set(owners_seen or ())) > 1
    logs = []
    if send_fn is None:
        return logs
    try:
        qmap = load_questions(path)
    except Exception:
        return logs
    night = _in_sleep_window(now)
    for qid, rec in sorted(qmap.items()):
        if not isinstance(rec, dict):
            continue
        ts = rec.get("ts") or 0
        if now - ts < reping:
            continue                            # not due yet
        block = str(rec.get("block") or rec.get("question") or "").strip()
        if not block:
            continue
        sid = str(rec.get("session") or "")
        cwd = str(rec.get("cwd") or "")
        if night:
            logs.append("question-reping deferred sleep-window %s"
                        % str(qid)[-6:])
            continue
        owner = (owner_by_sid.get(sid)
                or (owner_by_cwd.get(cwd) if cwd else None)
                or ("" if ambiguous else account_owner)
                or None)
        status, new_mid = send_fn(
            block, owner=owner, kind="questions",
            dedup_key="question-reping:%s:%d" % (qid, int(now // reping)),
            dry_run=dry_run, return_message_id=True)
        logs.append("question-reping %s -> %s [%s]"
                    % (str(qid)[-6:], status, project_label(cwd)))
        if dry_run or status != "sent":
            continue
        # Adversarial review (#368): drop the OLD key ONLY once the fresh
        # message id is genuinely re-tracked. "sent" with no usable id
        # (Discord 2xx with an unparseable body -> _post_discord's bare
        # True), or a record_question refusal / failed save (no resolvable
        # questions channel on THIS box, a non-numeric id, a disk error)
        # used to fall through to drop_question anyway -- re-asking ONCE
        # and then silently UN-TRACKING the question forever, the exact
        # silent-loss failure mode this function exists to kill. Keeping
        # the entry is spam-safe: the day-bucketed dedup key above already
        # caps this qid at one genuine send per bucket, so a kept entry
        # only retries the re-tracking on a later sweep/bucket.
        retracked = False
        if new_mid:
            ch = notification_channel(owner=owner, kind="questions") or ""
            # asked_ts (#407 review MAJOR-1): a re-track is a REPOST of the
            # SAME old ask, never a new one — carry the entry's own ask
            # generation through, so the record-time supersede and the
            # sweep collapse keep treating it as the OLD ask and can never
            # displace a LIVE, newer question tracked on the current
            # questions channel.
            retracked = record_question(new_mid, ch, sid, cwd, now=now,
                                        path=path, question=block,
                                        asked_ts=ask_generation(rec))
        if retracked:
            # #449-review F4: GRACE the old entry, never hard-delete it.
            # Same-channel re-tracks are usually already graced by
            # record_question's own supersede (this is then a no-op), but
            # a CROSS-channel re-track (a freshly provisioned -q thread,
            # owner-resolution drift) misses the channel-scoped supersede
            # — and the user's reply to YESTERDAY's still-visible card
            # must keep routing for the grace window, not vanish.
            grace_question(qid, path=path)
    return logs


# #461 -- the DURABLE owner-decision queue's daily re-ask. #368 (above)
# re-asks unanswered SESSION questions from the discord-questions.json map --
# but that map only ever grows from a live session's own ❓ ping. A TICKET
# blocked on the OWNER's decision -- carrying `needs-answer`/`needs-decision`
# (the SAME labels #468's footer `U N` counts) -- that NO session ever turned
# into a ❓ (or whose asking session has long since ended, its map entry
# pruned) had NO durable re-ask channel at all, so it rotted silently (the
# odoo-erp #3018/#3020/#2968/#3189 incident: 6 owner-blocked tickets for
# weeks, footer empty, Discord never pinged). This is the missing BACKSTOP:
# once per QUESTION_REPING_S day-bucket, summarise every open owner-decision
# ticket across this box's recently-worked repos into ONE Discord ping. An
# extension of the SAME #368 daily-reask section of run_once -- NEVER a new
# numbered job, NEVER a new hook (the FREEZE).
#
# OWNER_DECISION_LABELS is the SAME set cli_quals.USER_WAITING_LABELS feeds the
# footer/stop-proof (#468) -- kept as a flat literal here (never a module-level
# `from cli_quals import`) because the import direction is airuleset -> watchdog
# and watchdog is a 60s systemd timer; a reverse import would add import-time
# cost to every sweep. The EXACT AUTOPILOT_SKIP_EXCL precedent, pinned equal by
# TestOwnerDecisionLabelsInSync.
OWNER_DECISION_LABELS = ("needs-answer", "needs-decision")


def _fetch_owner_decision_tickets(home=None):
    """Open owner-decision tickets across this box's recently-worked repos, as
    a sorted list of (repo_name, number, title). Returns None ONLY when EVERY
    repo query failed (unmeasurable -- an auth/network hiccup must never look
    like 'no decisions pending' and record the day-bucket, silencing the
    digest; mirrors #199/#172's 'never guess from a failed read'). A genuinely
    empty result -- queries ran, nothing labelled, or no repos at all --
    returns []."""
    import subprocess
    env = _gh_env(home)
    # comma-OR within the label qualifier (verified-live label-family behaviour,
    # internals #399/#181) UNIONs the two labels; AUTOPILOT_SKIP_EXCL drops a
    # ticket the owner has deliberately set aside / an ops-channel.
    search = ("label:%s " % ",".join(OWNER_DECISION_LABELS)
              + AUTOPILOT_SKIP_EXCL).strip()
    out = []
    any_ok = False
    roots = _cache_repo_roots(home)
    for root, name in sorted(roots.items()):
        try:
            r = subprocess.run(
                ["gh", "issue", "list", "--state", "open", "--search",
                 search, "-L", "100", "--json", "number,title"],
                cwd=root, env=env, capture_output=True, text=True, timeout=8)
            if r.returncode != 0:
                continue                       # this repo failed -- try the rest
            any_ok = True
            for x in json.loads(r.stdout):
                out.append((name, int(x["number"]),
                            str(x.get("title") or "").strip()))
        except Exception:
            continue                           # one repo never kills the digest
    if roots and not any_ok:
        return None                            # every query failed -> unmeasurable
    return sorted(out)


def _owner_decision_digest_block(tickets, limit=12):
    """The Slovak, phone-readable, self-contained digest body. NOT a session
    `❓` marker (this is posted directly to Discord by send_fn, never via a
    Stop-hook), so it carries no `NEEDS YOU` keyword. `limit` caps the listed
    tickets (the rest collapse into one honest '…a ďalších K' line) so the
    WORST case -- long repo names + 80-char titles + header/footer -- stays
    under notify's `_MAX_CONTENT` (1900) forwarding cap: 12 lines is ~1.6 KB,
    comfortably clear, whereas 15 could reach ~1.9 KB and silently drop the
    tail tickets AND the 'Odpovedz prosím' footer mid-truncation."""
    n = len(tickets)
    lines = [
        "**Rozhodnutia čakajúce na teba (denný súhrn):**",
        "",
        "Týchto %d ticketov je dlhodobo BLOKOVANÝCH na tvojom rozhodnutí — "
        "nikto ich nevie posunúť ďalej, kým neodpovieš:" % n,
        "",
    ]
    for repo, num, title in tickets[:limit]:
        t = (title or "").replace("\n", " ").strip()
        if len(t) > 80:
            t = t[:77] + "…"
        lines.append("- %s #%d%s" % (repo, num, (" — " + t) if t else ""))
    if n > limit:
        lines.append("- … a ďalších %d" % (n - limit))
    lines += [
        "",
        "Odpovedz prosím na konkrétny ticket (v session, ktorá ho rieši, "
        "alebo komentárom na #N).",
    ]
    return "\n".join(lines)


def reping_owner_decision_tickets(now, send_fn, state, home=None, dry_run=False,
                                  reping=None, account_owner="", fetch=None,
                                  persist=None):
    """#461 -- see the section comment above OWNER_DECISION_LABELS. Once per
    QUESTION_REPING_S day-bucket, ping the owner with a summary of every open
    owner-decision ticket. No-op unless BOTH send_fn and fetch are wired
    (fetch=None keeps every OTHER job's run_once test network-free, exactly
    like jobs 8/11).

    Gated on a state day-bucket stamp BEFORE the network fetch (no 60s query
    storm) and deferred past the sleep window (retried after 06:00, bucket
    NOT recorded). The cadence stamp is recorded on a genuine (measurable)
    fetch -- even an empty one -- so a working GitHub with no pending decisions
    costs one query/day, while an all-failed (None) fetch retries next sweep.
    persist() is invoked BEFORE the ping leaves the process (cadence survives a
    TimeoutStartSec kill -- the job-8 pattern). The Discord post itself is
    additionally deduped by notify.send's own marker on the day-bucketed
    dedup_key, so a re-record after a restart cannot double-post."""
    reping = QUESTION_REPING_S if reping is None else reping
    if send_fn is None or fetch is None:
        return []
    bucket = int(now // reping)
    dd = state.get("owner_decision_digest") or {}
    if dd.get("bucket") == bucket:
        return []                              # already handled this day-bucket
    logs = []
    if _in_sleep_window(now):
        logs.append("owner-decision-digest deferred sleep-window")
        return logs                            # retry after 06:00 (bucket unset)
    tickets = fetch(home)
    if tickets is None:
        logs.append("owner-decision-digest unmeasurable "
                    "(all repo queries failed)")
        return logs                            # retry next sweep (bucket unset)
    # The bucket is stamped on a measurable FETCH, before the send -- so a
    # Discord POST failure (transient 5xx, or a box with notify unconfigured)
    # loses THIS day's roundup rather than re-fetching+re-posting every 60s all
    # day. Deliberate: the digest is a daily nicety (the footer `U N` and the
    # per-ticket ❓ pings are the critical paths), and one lost day beats an
    # all-day query/POST storm on a persistent misconfig. notify.send's own
    # marker only records on genuine DELIVERY (#135), so it would NOT dedupe a
    # retry -- our bucket stamp is the only cap, on purpose.
    dd["bucket"] = bucket
    state["owner_decision_digest"] = dd
    (persist or (lambda: None))()              # cadence survives a kill (job 8)
    if not tickets:
        logs.append("owner-decision-digest: 0 pending")
        return logs
    # ONE owner per box: account_owner/resolve_owner() is box-scoped, while the
    # tickets span every repo in _cache_repo_roots(home). On the fleet each
    # box's $HOME belongs to one Discord owner (dev1=newlevel; every subdev
    # stream user has their OWN $HOME + account), so this is per-owner-correct
    # today. Accepted residual (FREEZE): a single $HOME whose repos spanned two
    # different Discord owners would ping the box owner for all of them -- the
    # same box-wide addressing every other watchdog ping already uses (#212).
    owner = account_owner or None              # already stream-redirected (#212)
    if not owner:
        from notify import resolve_owner
        owner = resolve_owner() or None        # self-applies STREAM_NOTIFY_OWNER
    block = _owner_decision_digest_block(tickets)
    status = send_fn(block, owner=owner, kind="questions",
                     dedup_key="owner-decision-digest:%d" % bucket,
                     dry_run=dry_run)
    logs.append("owner-decision-digest -> %s (%d pending)"
                % (status, len(tickets)))
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


from watchdog.stash import (  # noqa: E402
    STASH_MARKER as STASH_MARKER,
    _PASTED_PLACEHOLDER_RX as _PASTED_PLACEHOLDER_RX,
    _typed_landed as _typed_landed,
    GOAL_TYPE_CHUNK_THRESHOLD as GOAL_TYPE_CHUNK_THRESHOLD,
    GOAL_TYPE_CHUNK_SIZE as GOAL_TYPE_CHUNK_SIZE,
    GOAL_TYPE_CHUNK_DELAY_S as GOAL_TYPE_CHUNK_DELAY_S,
    _PASTE_EXPAND_HINT_RX as _PASTE_EXPAND_HINT_RX,
    _pane_shows_collapsed_paste as _pane_shows_collapsed_paste,
    _type_literal as _type_literal,
    STASH_VERIFY_SETTLE_POLLS as STASH_VERIFY_SETTLE_POLLS,
    STASH_VERIFY_SETTLE_S as STASH_VERIFY_SETTLE_S,
    STASH_PARKED as STASH_PARKED,
    STASH_NOOP as STASH_NOOP,
    STASH_UNRESOLVED as STASH_UNRESOLVED,
    STASH_NO_BOUNDARY as STASH_NO_BOUNDARY,
    _await_stash_settled as _await_stash_settled,
    _typed_exclusively as _typed_exclusively,
    STASH_UNDO_MAX_BACKSPACES as STASH_UNDO_MAX_BACKSPACES,
    STASH_UNDO_SETTLE_POLLS as STASH_UNDO_SETTLE_POLLS,
    STASH_UNDO_SETTLE_S as STASH_UNDO_SETTLE_S,
    _undo_appended_text as _undo_appended_text,
    _undo_typed_text as _undo_typed_text,
    draft_rescue_dir as draft_rescue_dir,
    DRAFT_RESCUE_TTL_S as DRAFT_RESCUE_TTL_S,
    _DRAFT_RESCUE_NAME_RX as _DRAFT_RESCUE_NAME_RX,
    _draft_rescue_text as _draft_rescue_text,
    _draft_rescue_prune as _draft_rescue_prune,
    _draft_rescue_ensure_dir as _draft_rescue_ensure_dir,
    _DRAFT_RESCUE_MAX_RETRIES as _DRAFT_RESCUE_MAX_RETRIES,
    _draft_rescue_persist as _draft_rescue_persist,
    _undo_and_release_slot as _undo_and_release_slot,
    deliver_with_stash as deliver_with_stash,
    _JANITOR_OWN_PREFIXES as _JANITOR_OWN_PREFIXES,
    JANITOR_CLEAR_MAX_ITER as JANITOR_CLEAR_MAX_ITER,
    JANITOR_CLEAR_BATCH_MAX as JANITOR_CLEAR_BATCH_MAX,
    JANITOR_CLEAR_SETTLE_S as JANITOR_CLEAR_SETTLE_S,
    JANITOR_WATCH_MAX_AGE_S as JANITOR_WATCH_MAX_AGE_S,
    _looks_like_own_payload as _looks_like_own_payload,
    _looks_like_own_stuck_content as _looks_like_own_stuck_content,
)


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
    from notify import (bot_token, drop_grace_question, drop_question,
                        forget_marker, known_owner_ids, load_cards,
                        load_questions, _read_env)
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
    # (#449) Merge the GRACE store — entries the prune/supersede moved out
    # of the main map. A reply to a graced entry still routes NORMALLY for
    # QUESTION_GRACE_S (see _merge_grace_questions). And the QUESTION-
    # CHANNEL MEMORY: with an EMPTY map this function used to return before
    # fetching anything, which is exactly how david's answer vanished with
    # zero journal lines (2026-08-13) — remembered channels keep the fetch
    # alive for the orphan floor below.
    grace_ids = _merge_grace_questions(qmap, now, dry_run, logs)
    chan_seen = state.get("dreply_channels")
    chan_seen = dict(chan_seen) if isinstance(chan_seen, dict) else {}
    if (not qmap and not cardmap and not state.get("dreply_pointer")
            and not chan_seen):
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
    q_channels = {str(v.get("channel") or "") for v in qmap.values()
                  if isinstance(v, dict)}
    q_channels.discard("")
    channels = set(q_channels)
    channels |= {str(v.get("channel") or "") for v in cardmap.values()
                if isinstance(v, dict)}
    channels.discard("")
    # (#449) update + expire the channel memory (persist only on a real
    # sweep — #304), widen the fetch set with it, and remember which
    # remembered channels were QUESTION channels (the orphan floor's scope).
    kept_chans = _refresh_channel_memory(chan_seen, channels, q_channels, now)
    if not dry_run:
        state["dreply_channels"] = kept_chans
    channels |= set(kept_chans)
    q_channels |= {c for c, e in kept_chans.items() if e.get("q")}
    orphan_done = state.get("dorphan_done")
    orphan_done = list(orphan_done) if isinstance(orphan_done, list) else []
    orphan_done_set = set(orphan_done)

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
            elif r["referenced"] in grace_ids:
                drop_grace_question(r["referenced"])   # #449: graced entry
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
            if not r:
                # (#449) NEVER-SILENT floor — see _orphan_floor: an owner's
                # unroutable answer attempt journals + pings, never vanishes.
                _orphan_floor(msg, ch, allowed, qmap, cardmap, q_channels,
                              now, env, dry_run,
                              done_set | card_done_set,
                              orphan_done, orphan_done_set,
                              state, persist, logs)
                continue
            if r["reply_id"] in done_set:
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
                #
                # #372 (4th incident, forensically confirmed) — a FALSE
                # "delivered" confirmation is a trust-breaking defect: the
                # sender legitimately believes their reply reached the
                # session while it never did. `_input_line_text` returns
                # `None` (undeterminable — a dialog, a spinner, a genuinely
                # unreadable capture) as a value DISTINCT from `""`
                # (genuinely bare, confirmed empty) — the ORIGINAL `while t2`
                # / `if t2:` checks treated `None` identically to `""` (both
                # falsy), so an unreadable post-send capture was silently
                # accepted as proof of delivery. Require the EXPLICIT `""`
                # confirmation — never a merely-falsy one.
                t2 = _input_line_text(capture_pane(pid, run, lines=30))
                tries = 0
                while t2 != "" and tries < 2:
                    run(["tmux", "send-keys", "-t", pid, "Escape"])
                    run(["tmux", "send-keys", "-t", pid, "Enter"])
                    t2 = _input_line_text(capture_pane(pid, run, lines=30))
                    tries += 1
                if t2 != "":
                    blocked.setdefault(r["reply_id"], now)
                    logs.append(
                        "reply wedged (enter swallowed) %s" % r["session"][:12]
                        if t2 is not None else
                        "reply wedged (verify unreadable) %s"
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


# The qualifying-set EXCLUSION fragment for every open-issue search in THIS
# MODULE that builds job 8/11's "workable nudge candidate" set (#364, a
# #362 follow-up). #362 fixed the `/autopilot` `/goal` stop-proof
# (`core-quals`/`slice-quals` in airuleset.py) via its own
# `AUTOPILOT_SKIP_EXCL` constant there, but that diff never reached the two
# backstop queries in THIS file — a PERMANENT ops-channel ticket (a
# stream's own self-declared "never auto-closes" channel — odoo-erp
# #1861/#3037) that also happened to carry `prio:bounce` or
# `needs-gatekeeper` would still surface here as a nudge candidate.
#
# Deliberately an INDEPENDENT literal, same name/value as airuleset.py's
# own constant (pinned equal by TestAutopilotSkipExclConstantsStayInSync
# below), rather than `from airuleset import AUTOPILOT_SKIP_EXCL`. NOT
# because of a circular import -- measured, there isn't one today (every
# `from watchdog import ...` call site inside airuleset.py is local/
# deferred, inside function bodies -- see cmd_gk_request/cmd_core_quals/
# etc. -- and `import watchdog` + `import airuleset` succeed in either
# order). The real reason is COST: this module is a systemd `--user`
# TIMER firing every 60s on every managed box, and `-X importtime`
# measured `import watchdog` alone at ~28ms; pulling in the reverse
# import would drag the whole ~14.5k-line airuleset.py (plus filedrop)
# along for every one of those wake-ups, pushing it to ~49ms (+75%) for
# one shared string. A reverse import would also become a genuine cycle
# the day anyone adds a top-level `import watchdog` to airuleset.py --
# a real, if currently latent, layering-fragility risk on top of the
# cost one. If the label ever changes, both copies must be updated
# together -- `grep -rn ops-channel` finds both, and the sync test below
# fails loudly if one is missed.
AUTOPILOT_SKIP_EXCL = "-label:autopilot-skip -label:ops-channel"


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
#
# (#353, 2026-08-10) A materially UNCHANGED gk-request state (same ticket
# SET, no supervisor-pane appear/disappear transition) re-pings/re-nudges on
# an EXPLICIT, WIDENING staged schedule (`GKREQ_REPING_SCHEDULE_S`, 24h ->
# 3d -> 7d, holding at 7d forever) instead of a fixed 6h window that used to
# fire up to 4x/day forever for a persistently-unaddressed no-action state
# (the live incident: odoo-erp, 21 open tickets, simap's supervisor session
# deliberately off — "toto je spam!!!"). Reset to an immediate ping fires
# ONLY on a materially different observation: the fetched ticket SET
# differs from what was last recorded, or a supervisor pane transitions
# from previously-observed-present back to absent (a genuine appear-then-
# disappear cycle, tracked via `pane_seen` in the same per-label record).
# --------------------------------------------------------------------------- #

GKREQ_INTERVAL = 30 * 60             # min seconds between gk-request sweeps
GKREQ_CACHE_MAX_AGE_S = 48 * 3600    # no-pane ping only for this-fresh roots
# (#353) Mirrors #352's job-4 staged-backoff PATTERN (an explicit tuple of
# widening intervals, indexed by consecutive-occurrence count, holding at
# the final/cap stage forever) — a standalone, independently-implemented
# function following the SAME shape, never a shared helper extracted out of
# job-4's `decide_working` (this ticket's own lane forbids touching job-4's
# code at all; see `_gkreq_reping_due`'s docstring for the full reasoning).
GKREQ_REPING_SCHEDULE_S = (24 * 3600, 3 * 24 * 3600, 7 * 24 * 3600)

GKREQ_NUDGE = ("gk-request backstop: open needs-gatekeeper tickets %s in %s — "
               "a sub-dev stream is waiting on a SUPERVISOR action it cannot "
               "perform itself. Per the autopilot skill's cross-stream "
               "protocol: ACK on the ticket NOW (add the label if only the "
               "GATEKEEPER-ACTION: title carries it), perform the action, "
               "comment the result, remove the label or close, then nudge the "
               "stream's pane so it resumes without polling.")

# (#399) A hand-off (ready-for-review / needs-gatekeeper) untouched for this
# long is STALE — the review pipeline is presumed dead and the phone gets ONE
# staged alarm stream instead of the owner having to notice on GitHub.
# `updatedAt` is the staleness proxy (any touch — a comment, a label, a
# review action — resets it), sized between the 30-min sweep cadence and the
# 24h first reping stage: /process-subdev's own review-watch normally
# consumes a hand-off within minutes-to-hours, so 6h of ZERO activity means
# nothing is consuming the queue. Honest, documented residual: a chatty
# ticket that keeps getting touched without being reviewed never reads
# stale — updatedAt is a proxy, not the label's own application time (a
# per-ticket GraphQL timeline read would be an order of magnitude more API
# cost per 30-min sweep for marginal precision).
GKREQ_STALE_HANDOFF_S = 6 * 3600

# (#399) Hand-off rows carrying any of these labels never feed the stale
# alarm: `prio:bounce` = the ticket is back in the SUB-DEV's court (the same
# bounce-overrides-a-hand-off direction the footer's own handed-off logic
# uses); `ops-channel` = a PERMANENT, never-auto-closing channel that would
# otherwise read "stale" forever; `autopilot-skip` is already excluded
# server-side in every query — kept here as a belt so a future query edit
# cannot silently drop it. Checked CLIENT-SIDE on the fetched `labels`
# field (never only via `--search`), so the exclusion holds regardless of
# GitHub's search-index health and regardless of when any server-side
# exclusion work on the sibling queries lands.
_STALE_HANDOFF_EXCLUDE_LABELS = frozenset(
    {"prio:bounce", "ops-channel", "autopilot-skip"})

STALE_HANDOFF_ALARM = (
    "⚠️ **%(name)s: %(n)d hand-off tiketov čaká na review pridlho**\n> "
    "Tikety %(ticks)s sú odovzdané na review/akciu (ready-for-review / "
    "needs-gatekeeper), ale nikto sa ich nedotkol už ~%(hours)dh. Review "
    "pipeline pre `%(name)s` zrejme stojí — spusti /process-subdev alebo "
    "supervízorskú session v `%(root)s`.")


from watchdog.handoff_alarm import (  # noqa: E402
    _parse_gh_ts as _parse_gh_ts,
    _normalize_gkreq as _normalize_gkreq,
    _stale_handoff_alarm as _stale_handoff_alarm,
)


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


# --------------------------------------------------------------------------- #
# /goal arming — collapsed (#403, 2026-08-12) to a callback model,
# `watchdog/goal.py` (its own module docstring is the single source of
# truth). Job 9's OLD viewport-scan/paste-the-printed-line heuristic
# (`_ARM_QUESTION_RX`/`GOAL_ARM_WINDOW_S`, described here through
# 2026-08-12) is GONE — arming now happens ONLY via an explicit
# `goal-arm --self` callback from the /autopilot skill's own Step 2,
# never a guess reconstructed from a pane's rendered viewport.
# --------------------------------------------------------------------------- #

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


def _goal_autoarm_recent_human_activity(sid, tpath, now, window_s=None,
                                        extra_human_prefixes=()):
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
    unmeasurable read (`_goal_never_armed`, the sibling this used to sit
    adjacent to, was deleted along with the rest of job 9's old guessing
    machinery by #403 -- this discipline now stands on its own).

    `extra_human_prefixes` (#377-review MINOR-1, optional): threaded
    straight through to `_last_human_prompt_ts` -- see ITS docstring. The
    default `()` is a no-op, so job 9's own call (which never passes this)
    keeps its exact reviewed behavior unchanged; a caller that DOES need a
    Discord-relayed answer to count (`_compact_recent_human_activity`)
    passes its own prefix set."""
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
        hts = _last_human_prompt_ts(tpath, extra_human_prefixes=extra_human_prefixes)
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
# Job 15 -- COMPACT OVERGROWN IDLE SESSIONS -- REMOVED (#102, 2026-07-27).
#
# `compact_stale_context` fired /compact purely off CONTEXT SIZE + IDLE
# DURATION, with no regard for what marker the session's last turn ended
# on -- exactly the mechanism the #102 correction identified as the live
# camera-box incident's likely cause (a `❓ NEEDS YOU` turn, blocked
# awaiting the user's answer, sat idle long enough to qualify and got
# compacted). The user's corrected agreement (#102): compaction fires
# ONLY at a completed-ticket boundary (job 14, `watchdog.compact.compact_
# sweep`) -- nothing else, no context-size heuristic of our own. Claude Code's
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
# `_reconcile_candidate_panes`) are KEPT -- but AFTER the #402 compact
# collapse (below) neither is job 14's or job 21's own any more: job 14's
# real code now lives in `watchdog/compact.py`, which never calls either
# (it uses only `_pane_has_queued_compact`, which DOES survive as a
# genuine job-14 dependency via `watchdog._pane_has_queued_compact`); job
# 21 (`pane_turn_elapsed`/`long_turn_watch`) never called either to begin
# with. Both remaining callers are GOAL-owned -- now `watchdog/goal.py`'s
# own job-20 dark-watch/lane-sweep (formerly `_goal_*_nudge` family and
# `goal_rearm`, both collapsed away by #403) -- kept for THAT reason, not
# the one this comment originally gave. `_proc_fingerprint_alive`/
# `_pane_claude_proc_fingerprint`, the real `/compact` claim/lock system,
# and its later #402 always-False/no-op compatibility stubs
# (`compact_claim_active`/`compact_claim_set`) were ALL removed for good
# by #403, once goal's own delivery machinery stopped depending on them.
# See #102's evidence block for the original audit and #402's for the
# later one.
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
# Job 20's ORIGINAL incident (#76, 2026-07-26 live incident, montalu@subdev)
# -- kept verbatim below for the archaeology, since the INTENT-vs-REALITY
# cross-check it motivated (`scan_goal_markers`/`pane_goal_armed`) is still
# exactly how `watchdog/goal.py` decides whether a goal is genuinely dark.
# #403 (2026-08-12) replaced everything AFTER that cross-check -- the
# re-arm/retry/delivery machinery this comment used to describe below is
# gone; `watchdog/goal.py`'s own module docstring is the current source of
# truth for what job 9/20 actually do now.
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
# #403's REPLACEMENT for everything below this line: rather than bounding a
# RETRY loop (the old `GOAL_REARM_MAX_ATTEMPTS`/`GOAL_REARM_STREAK_S` shape),
# `watchdog.goal.goal_dark_watch` never retypes a dark goal at all -- it
# debounces the dark observation across >= 2 sweeps, then sends ONE Discord
# ping asking the user to re-run `/autopilot` (which re-arms via the SAME
# proven callback every fresh arm goes through). Zero keystrokes, so none of
# the old delivery-discipline concerns (fresh-capture verify, the #36
# truncation hazard, `deliver_with_stash` coordination) apply to this branch
# any more -- they still apply, unchanged, to `goal_sweep`'s own delivery of
# a genuinely PENDING (explicitly recorded) request, and to
# `goal_lane_sweep`'s watchdog-initiated nudge.
# --------------------------------------------------------------------------- #

GOAL_INDICATOR = "◎ /goal"          # CC's own armed-goal footer indicator
# #393 -- the real indicator's own header render is SHORT and CLOSED-
# FORM: the glyph, "/goal", optionally the word "active", and optionally
# a single "(Nh)"/"(Nm)" age suffix -- nothing else, ever (every shape
# observed anywhere in this repo's own tests/dev-notes: "◎ /goal",
# "◎ /goal active", "◎ /goal active (1m)" through "(58m)", "(2h)",
# "(3h)"). A bare `.startswith(GOAL_INDICATOR)` check also matches a
# rendered CONTINUATION row of ordinary word-wrapped assistant prose
# that happens to start with the same glyph+word (a genuine live
# incident: "◎ /goal active, right where the earlier bug expected only
# chrome." wrapped as the SECOND line of a paragraph).
#
# #393-review MINOR-1 (fresh-context adversarial review, executed):
# the FIRST cut of this fix (an open-ended `[ \w()]{0,40}` allowlist,
# rejecting only PUNCTUATED continuations) still accepted several
# constructed continuations with no punctuation in their first 40
# chars ("◎ /goal active and the loop keeps going", "◎ /goal armed",
# "◎ /goal active (footer) so we know" — live-reproduced, 7 of 8
# constructed cases). A CLOSED form -- the tail must be EXACTLY
# " active" or " active (<1-3 digits><h|m>)", nothing between or after
# -- rejects every one of those while still accepting every real
# observed shape (verified against the corpus above).
#
# #393-review MINOR-2 (theoretical, unobserved): this closed form is
# strictly NARROWER than the prior allowlist, so any REAL render this
# repo has never observed (a fractional-hour suffix "(1.5h)", an
# "(Nh Nm)" combined form, an nbsp U+00A0 separator instead of a plain
# space -- this file's own `❯\xa0` finding makes an nbsp separator
# plausible) would now read as a false NEGATIVE (dark, when actually
# armed) rather than matching. Accepted residual, not fixed
# speculatively (FREEZE: fix what has failed in production) — every
# age-suffix shape this repo has ever actually rendered is covered;
# widen the class only if a real nbsp/fractional-hour render is ever
# observed live.
#
# #487 (live gk incident, 2026-08-15) — the MINOR-2 "widen the class
# only if a real render is ever observed live" clause fired: gk's own
# armed pane settled to `› stashed · ◎ /goal active (1d)`, a false
# NEGATIVE from TWO newly-observed shapes at once — (1) CC prepends the
# stash-slot marker (`STASH_MARKER` + " · ") onto the SAME header line
# the glyph rides on, so the `^`-anchor never matched; (2) the age
# crossed to DAY granularity `(1d)`/`(2d)`, which `[hm]` rejected. The
# `pane_goal_armed` False that resulted killed the #442/#481 lane-fill
# guard. Widened, still CLOSED-form (the MINOR-1 lesson): an OPTIONAL
# leading `STASH_MARKER + " · "` prefix (derived from the repo constant,
# never a divergent copy; ALL its separators confirmed plain 0x20 — NOT
# nbsp U+00A0 — by hexdump of the raw byte-faithful gk capture, where
# `·` survived as c2 b7 so an nbsp would equally have shown as c2 a0)
# and the age unit class `[hm]` -> `[hmd]`. The tail stays
# exactly " active"/" active (<1-3 digits><h|m|d>)", so every #393
# wrapped-prose false-positive control (with/without punctuation, prefix
# or not) is still rejected. A fractional-hour/nbsp render remains the
# same accepted residual as before.
_GOAL_HEADER_INDICATOR_RX = re.compile(
    r"^(?:" + re.escape(STASH_MARKER + " · ") + r")?"
    + re.escape(GOAL_INDICATOR)
    + r"( active(\s\(\d{1,3}[hmd]\))?)?$")
_GOAL_LCS_OPEN = "<local-command-stdout>"
_GOAL_LCS_CLOSE = "</local-command-stdout>"

# Bootstrap window for a session this job has never scanned before. Later
# sweeps read ONLY the bytes appended since the stored offset, so the steady
# state costs ~nothing regardless of how long ago the goal was armed.
GOAL_MARK_TAIL_BYTES = 4_000_000

GOAL_ARM_ACTIVE_PREFIX = ('A session-scoped Stop hook is now active with '
                          'condition: "')
_GOAL_ARM_PROBE = GOAL_ARM_ACTIVE_PREFIX[:-1].encode()   # quote-free (JSON)


def goal_templates_path():
    """`~/.claude/skills/autopilot/SKILL.md` — the INSTALLED autopilot skill,
    resolved at CALL time (never a frozen module-level constant).

    Deliberately the installed copy, never a repo checkout: the isolated
    sub-dev users (marek / david / montalu) have no `~/devel/airuleset`, and
    the installed skill is by definition the text their `/autopilot` actually
    prints — so it is the only source that can never disagree with what a
    session was armed from."""
    return Path.home() / ".claude" / "skills" / "autopilot" / "SKILL.md"


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
    # #403 -- this used to ALSO track `arm_after` (did an "arm question" line
    # follow the newest marker), fed by `_entry_asks_to_arm`/`_GOAL_ASK_PROBE`
    # -- both existed ONLY to support the old `goal_autoarm`'s viewport-scan
    # arming, which is deleted. `arm_after` had no other reader anywhere in
    # the codebase (confirmed by grep before removing it) -- arming is now
    # driven entirely by an EXPLICIT `goal-arm --self` callback, never by
    # scanning the transcript for a printed question, so this function's
    # only remaining job is finding the newest marker itself.
    best = None
    for ln in body.splitlines():
        # cheap pre-filter over the raw bytes — either of the TWO shapes CC
        # writes (the `/goal` command's own stdout, or the arm instruction it
        # injects, which is the only record a QUEUED arm leaves at all, #64).
        if _GOAL_LCS_OPEN.encode() not in ln and _GOAL_ARM_PROBE not in ln:
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
                str(entry.get("timestamp")).replace(
                    "Z", "+00:00")).timestamp()
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
    below the box and is excluded from the boundary search.

    #383 — non-blank `footer` content is NOT by itself proof the real
    chrome region is even in view. A draft too tall to fit the remaining
    viewport (job 20's own re-arm delivery left one PARKED, unsent, after a
    paste-pending failure) renders as `❯ <head>` followed by many more
    wrapped CONTINUATION rows of the SAME box, filling the capture down to
    its last line — those rows are more box content, never chrome, and the
    real closing border + statusline + `◎ /goal` glyph have scrolled off
    past the bottom of what was captured. Live-confirmed, read-only, on
    camera-box's own stuck pane (session zbynek-4:0, pane %1, 2026-08-11):
    the capture's LAST line was still draft text, with a genuinely live,
    actively-incrementing `◎ /goal active (Nm)` glyph rendered entirely
    OUTSIDE `footer` (above the box's own top border), and the old body
    returned a confident `False` on it. So a `footer` with content but with
    NO row that itself reads as real trailing chrome (`_is_bottom_chrome`,
    the SAME peel used to find the box's own head) cannot support a `False`
    verdict either — the indicator's own home row is not provably in view,
    so the answer is `None`. Verified this reuses `_is_bottom_chrome` (no
    new pattern) without tripping on the repo's own real `/goal` templates
    wrapped at 80/120/176 columns, and without disturbing the pre-existing
    `test_statusline_without_a_ctx_segment_still_reads` case (a mode-hint
    row alone already counts as chrome, independent of the statusline
    row's own segment-count threshold).

    A bare BORDER-RULE row alone does NOT count as that chrome evidence,
    even though `_is_bottom_chrome` itself does classify one: the box's own
    closing border can be the LAST thing that fit in the capture, with the
    real statusline/mode-hint/agent-strip rows (and the `◎ /goal` glyph
    riding on the statusline one) cut off immediately after it — a border
    alone proves the box CLOSED, never that the indicator's own row is in
    view. Every other chrome shape (`ctx `, the >=2-segment statusline, the
    mode hint, an agent-strip row, a selected-strip row, the selector hint)
    still counts on its own; none of them can render without the row that
    would carry the indicator already being on screen.

    #383-review Finding 2 — the chrome evidence must additionally be part
    of an UNBROKEN trailing block ending at the capture's own last line
    (`_trailing_bottom_chrome`), not merely present SOMEWHERE in `footer`:
    a chrome-shaped row (e.g. a pasted quote of a rendered statusline,
    live-triggered — see that helper's own docstring) sitting partway
    through ordinary draft continuation, with MORE draft rows still
    following it, must not count. Real chrome never has draft content
    rendered below it.

    #383-review Finding 3 (theoretical, unconfirmed live) — the trailing
    walk trusts an agent-strip/selected-strip/selector-hint row ON ITS OWN
    even without a statusline row also in the walk. This is safe ONLY
    because `footer` is one CONTIGUOUS suffix of the whole capture (never a
    subset with a gap) and every real CC render this repo has ever captured
    puts the statusline row STRICTLY ABOVE (i.e. earlier in) the agent
    strip — so an agent-strip row reaching the trailing walk structurally
    implies the statusline row above it is ALSO still inside `footer`
    (nothing between idx+1 and the strip row is ever skipped). If a future
    CC layout ever renders the strip BEFORE the statusline, this reasoning
    — and the population of sessions it protects, autopilot supervisors
    with visible worker rows — would need re-checking."""
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
    trailing = _trailing_bottom_chrome(footer)
    # #386 (mirror of #383) -- trust the `◎ /goal` footer indicator ONLY when
    # it rides a genuine TRAILING-chrome row (the statusline in the same
    # unbroken chrome block that ends the capture), NEVER a bare substring
    # anywhere in `footer`. A parked, UNSENT DRAFT renders its wrapped
    # CONTINUATION rows BELOW the `❯` head (so they land in `footer`), so a
    # draft that merely QUOTES the glyph read as armed even on a genuinely
    # dark goal -- the exact False-direction gap #383 closed, one branch over.
    # Reusing the SAME `_trailing_bottom_chrome` peel the #383 check below
    # already needs (computed once) excludes a chrome-shaped-but-mid-draft
    # row by construction (its own Finding-2 backward walk stops at the first
    # non-chrome draft row), while the real legacy render (glyph appended to
    # the statusline chrome row) is inside that block and still matches. The
    # #388 header path below is untouched.
    if any(GOAL_INDICATOR in s for s in trailing):
        return True
    if not any(not _is_border_rule(s) for s in trailing):
        return None                        # #383: footer is box content, a
                                            # chrome-shaped row buried mid-
                                            # draft, or a bare closing
                                            # border with nothing past it --
                                            # not real trailing chrome --
                                            # real footer is off-screen,
                                            # "dark" unproven
    # #388: before declaring the goal dark, check the box's HEADER. Current
    # Claude Code renders `◎ /goal active (Nm)` on a standalone line directly
    # ABOVE the input box's top-border rule (not on the statusline row in the
    # footer this function scans). Confirmed live 2026-08-11 on 3 armed panes
    # (camera-box, airuleset, forestshop). The real indicator line, stripped,
    # STARTS WITH the indicator (right-aligned, alone on its line); a
    # conversation MENTION has it embedded mid-prose. #393 tightened the
    # match from a bare `.startswith` to `_GOAL_HEADER_INDICATOR_RX` --  a
    # wrapped-prose CONTINUATION row can also start with the glyph+word
    # (unlike a mid-prose mention, which never starts a rendered line with
    # it at all), so the tail must additionally look like the real render
    # (short, no sentence punctuation) rather than trailing prose. The
    # 3-line bound keeps the search inside the box header, never reaching
    # the conversation. Placed here (only when the footer path would
    # otherwise say False) so every #383 `None` branch above is preserved.
    header = lines[max(0, idx - 3):idx]
    if any(_GOAL_HEADER_INDICATOR_RX.match(ln.strip()) for ln in header):
        return True
    return False


# #433 item G step 14 -- the job-5 pending done-ping backstop (deliver_pending_done
# + its transcript/mtime helpers) and the job-22 stale-exec-marker hygiene that used
# to live here now live in `watchdog/sweep_jobs.py`, re-exported in place with the
# same positional-facade convention as the other split blocks, so every
# `watchdog.<name>` seam (run_once jobs 5/22, hooks, tests) resolves unchanged. The
# `PENDING_*` constants moved with them (def-time defaults of BOTH deliver_pending_done
# and run_once's own signature) and are re-exported HERE, above run_once's def, so
# run_once's def-time defaults still resolve; `MAIN_EXEC_MARKER_MAX_AGE_S` /
# `_EXEC_MARKER_PREFIXES` moved too (grep-proven unpatched) and are re-exported.
from watchdog.sweep_jobs import (  # noqa: E402
    _transcript_for_sid as _transcript_for_sid,
    _cwd_from_transcript as _cwd_from_transcript,
    _bg_monitor_in_cwd as _bg_monitor_in_cwd,
    deliver_pending_done as deliver_pending_done,
    _safe_mtime as _safe_mtime,
    _safe_unlink as _safe_unlink,
    _session_id_is_live as _session_id_is_live,
    cleanup_stale_exec_markers as cleanup_stale_exec_markers,
    PENDING_DONE_GRACE as PENDING_DONE_GRACE,
    PENDING_DONE_MAX_STALE as PENDING_DONE_MAX_STALE,
    PENDING_PREFIX as PENDING_PREFIX,
    MAIN_EXEC_MARKER_MAX_AGE_S as MAIN_EXEC_MARKER_MAX_AGE_S,
    _EXEC_MARKER_PREFIXES as _EXEC_MARKER_PREFIXES,
)


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


def _owner_disabled(kind):
    """#400 owner kill-switch read: True when the owner's flag file for
    `kind` ("goal"/"compact") exists. The test suite sets
    AIRULESET_TEST_IGNORE_DISABLE=1 (cmd_push injects it for the pre-push
    gate subprocess, tests/conftest.py for direct pytest runs) so a box
    whose OWNER has genuinely disabled the jobs can still run its own
    green suite -- production sweeps never set the var."""
    if os.environ.get("AIRULESET_TEST_IGNORE_DISABLE"):
        return False
    return os.path.exists(
        os.path.expanduser("~/.claude/watchdog-disable-%s" % kind))


# --------------------------------------------------------------------------- #
# #404 point 3 (module split) -- per-service submodule re-exports.
#
# Each name below is DEFINED in its own watchdog/<module>.py file, never
# here -- this is a facade re-export, not a definition. It is placed AFTER
# every symbol `watchdog/usage.py` itself needs from __init__.py is already
# bound (NOT the true end of this file -- run_once() and ~2000 lines of
# other code still follow it) so that (a) `usage.py`'s own imports would
# succeed even if it needed to reach back into `watchdog` (it currently does
# not -- it imports only stdlib), and (b) every existing consumer that
# resolves these names via `watchdog.<name>` dotted access or via
# `from watchdog import <name>` (airuleset.py's cmd_watchdog/cmd_fable_gate)
# keeps working with ZERO changes, since the names still live in watchdog's
# own top-level namespace -- just re-exported instead of defined in this
# file directly. This is the FIRST facade-re-export split in this repo
# (unlike watchdog/compact.py and watchdog/goal.py, which are consumed via
# plain `watchdog.compact`/`watchdog.goal` attribute access with no
# re-export block at all) -- a FUTURE submodule extracted from a cluster
# defined LATER in this file (e.g. inside run_once() itself) needs its own
# facade import placed AFTER that cluster's own definitions, never assume
# this block's current position already covers it. See watchdog/usage.py's
# own module docstring for what this specific cluster does.
from watchdog.usage import (  # noqa: E402
    fetch_usage as fetch_usage,
    weekly_percent as weekly_percent,
    usage_windows as usage_windows,
    write_usage_cache as write_usage_cache,
    fable_gate as fable_gate,
    check_usage as check_usage,
    _account_email as _account_email,
    _local_account as _local_account,
    _box_hostname as _box_hostname,
)


# --------------------------------------------------------------------------- #
# #433 (module split cluster B) -- per-service submodule re-exports, same
# facade convention as the `watchdog.usage` block immediately above (see
# its own comment for the full rationale -- this repeats only what differs).
# Placed after every symbol `watchdog/burn_jobs.py` itself needs from
# __init__.py is already bound (it needs none -- stdlib + the top-level
# `burn` package only, imported locally inside each job, unchanged from
# before the move) so every existing consumer that resolves these names via
# `watchdog.<name>` dotted access, `from watchdog import <name>`, or a bare
# call inside `run_once()` below keeps working with ZERO changes. `_env_num`
# and `FLEET_BURN_DELAY_MINUTES` are re-exported too even though nothing
# outside this cluster currently reaches them via `watchdog.<name>` --
# before this move both were already `watchdog`-namespace-visible (plain
# module globals of `watchdog/__init__.py`), and the verbatim-move contract
# is that every name's visibility is unchanged, not just the ones with a
# known external reader today.
from watchdog.burn_jobs import (  # noqa: E402
    burn_snapshot_job as burn_snapshot_job,
    vault_purge_job as vault_purge_job,
    FLEET_BURN_DELAY_MINUTES as FLEET_BURN_DELAY_MINUTES,
    fleet_burn_job as fleet_burn_job,
    _env_num as _env_num,
    burn_alert_job as burn_alert_job,
)


# --------------------------------------------------------------------------- #
# #433 (module split cluster F) -- per-service submodule re-exports, same
# facade convention as the `watchdog.usage`/`watchdog.burn_jobs` blocks
# immediately above (see `watchdog.usage`'s own comment for the full
# rationale -- this repeats only what differs). `watchdog/cards.py` needs
# NOTHING from `watchdog/__init__.py` (stdlib `re` + locally-imported
# `subprocess` + locally-imported `notify` package functions, unchanged
# from before the move) so this is a plain forward import, not the
# circular-import hazard a coupled cluster would have.
#
# The three git-primitive names re-exported here (`_default_git_run`,
# `_git_first_line`, `_git_base_ref`) were, at the time this facade was
# written, ALSO used by code still resident in this file (`_git_commit_ts`,
# `delivery_state`, `_repo_label` -- cluster E). That is no longer true:
# cluster E was extracted in a later commit (its own `watchdog/repo_health.py`
# imports the trio directly from `watchdog.cards`, a plain leaf-to-leaf
# forward import) -- `_git_commit_ts`/`delivery_state`/`_repo_label` no
# longer live in this file at all, and this repeated re-export block is now
# pure namespace preservation (kept for `watchdog.<name>` attribute-access
# compatibility) rather than the load-bearing bare-name resolution mechanism
# described above when it was cluster E's own facade dependency.
from watchdog.cards import (  # noqa: E402
    _default_git_run as _default_git_run,
    _git_first_line as _git_first_line,
    _git_base_ref as _git_base_ref,
    CARD_WINDOW_S as CARD_WINDOW_S,
    CARD_GRACE_S as CARD_GRACE_S,
    CARD_MAX_LISTED as CARD_MAX_LISTED,
    _CLOSES_RE as _CLOSES_RE,
    merged_closes as merged_closes,
    _normalize_closed as _normalize_closed,
    _commits_in_window as _commits_in_window,
    card_reconcile as card_reconcile,
)


# --------------------------------------------------------------------------- #
# #433 (module split cluster E) -- per-service submodule re-exports, same
# facade convention as the `watchdog.usage`/`watchdog.burn_jobs`/`watchdog.cards`
# blocks immediately above (see `watchdog.usage`'s own comment for the full
# rationale -- this repeats only what differs). `watchdog/repo_health.py`
# needs nothing from `watchdog/__init__.py` itself -- stdlib `os`/`re` plus
# `_git_first_line`/`_git_base_ref`, forward-imported from `watchdog.cards`
# (a leaf module depending on neither `repo_health.py` nor `__init__.py`) --
# so this is a plain forward import, not the circular-import hazard a
# coupled cluster would have.
from watchdog.repo_health import (  # noqa: E402
    _git_commit_ts as _git_commit_ts,
    DELIVERY_STALL_S as DELIVERY_STALL_S,
    DELIVERY_WORK_FRESH_S as DELIVERY_WORK_FRESH_S,
    DELIVERY_MIN_UNDELIVERED as DELIVERY_MIN_UNDELIVERED,
    DELIVERY_REPING_S as DELIVERY_REPING_S,
    DELIVERY_STALL_MAX_S as DELIVERY_STALL_MAX_S,
    delivery_state as delivery_state,
    _delivery_stalled as _delivery_stalled,
    delivery_stall_watch as delivery_stall_watch,
    MANAGED_SWEEP_INTERVAL_S as MANAGED_SWEEP_INTERVAL_S,
    discover_managed_repos as discover_managed_repos,
    _repo_label as _repo_label,
    _repo_is_fork as _repo_is_fork,
    _sweep_due as _sweep_due,
    _repo_sweep_batch as _repo_sweep_batch,
    REPO_SWEEP_BATCH_MAX as REPO_SWEEP_BATCH_MAX,
    DEDUP_MEMORY_MAX_AGE_S as DEDUP_MEMORY_MAX_AGE_S,
    net_drift_alarm as net_drift_alarm,
    NET_DRIFT_THRESHOLD as NET_DRIFT_THRESHOLD,
    stuck_main_sweep as stuck_main_sweep,
    _stuck_main_skip_set as _stuck_main_skip_set,
    STUCK_MAIN_AGE_S as STUCK_MAIN_AGE_S,
    STUCK_MAIN_AHEAD_MIN as STUCK_MAIN_AHEAD_MIN,
)

# #433 cluster C -- the #372 stuck-draft janitor (clear-box / pop-stash /
# watch-provenance / recover-driver / pane-location), extracted verbatim to
# `watchdog/janitor.py`. Re-exported here so `run_once`'s bare-name callers and
# the module-qualified callers in `watchdog/goal.py`/`compact.py` resolve
# unchanged. `janitor.py` reaches its 11 resident dependencies back through its
# own top-level `import watchdog` (call-time attribute access, no cycle).
from watchdog.janitor import (  # noqa: E402
    _janitor_clear_box as _janitor_clear_box,
    _janitor_pop_stash as _janitor_pop_stash,
    _janitor_watch_seen as _janitor_watch_seen,
    _janitor_mark_watch as _janitor_mark_watch,
    _janitor_clear_watch as _janitor_clear_watch,
    _janitor_recover as _janitor_recover,
    _pane_location as _pane_location,
)


# #433 cluster D — cross-stream backstops (job 8 bounce / job 11 gk-request / the
# shared backlog-cache read jobs 10/20 consult). Extracted verbatim to
# `watchdog/cross_stream.py`; re-exported here so `run_once`'s job-8/11 dispatch,
# airuleset.py's deferred `from watchdog import _fetch_bounce_tickets`/
# `_fetch_gkreq_tickets`, and `__init__.py`'s own bare `_gh_env`/`_try_stash_nudge`/
# `_safe_to_bounce_nudge`/`_cached_backlog_open` sites all resolve unchanged. The
# leaf reaches the 16 resident cluster-private constants, the 3 resident private
# helpers, and the 8 shared pane/transcript primitives back through its own
# top-level `import watchdog` (call-time attribute access, no cycle).
from watchdog.cross_stream import (  # noqa: E402
    _repo_in_cross_stream_flow as _repo_in_cross_stream_flow,
    _bounce_quals as _bounce_quals,
    _gh_env as _gh_env,
    _fetch_bounce_tickets as _fetch_bounce_tickets,
    _cache_repo_roots as _cache_repo_roots,
    _try_stash_nudge as _try_stash_nudge,
    _safe_to_bounce_nudge as _safe_to_bounce_nudge,
    bounce_backstop as bounce_backstop,
    _gkreq_reping_due as _gkreq_reping_due,
    _gkreq_supervisor_root as _gkreq_supervisor_root,
    _fetch_gkreq_tickets as _fetch_gkreq_tickets,
    gk_request_backstop as gk_request_backstop,
    _cached_backlog_open as _cached_backlog_open,
    _cached_backlog_count as _cached_backlog_count,
)


from watchdog.pane_classify import (  # noqa: E402
    _is_border_rule as _is_border_rule,
    _statusline_hits as _statusline_hits,
    _is_bottom_chrome as _is_bottom_chrome,
    _is_separator_line as _is_separator_line,
    _find_boundary_line as _find_boundary_line,
    _normalize_queued_hint as _normalize_queued_hint,
    _find_boundary_line_raw as _find_boundary_line_raw,
    _input_box_rows_raw as _input_box_rows_raw,
    _box_is_wrapped as _box_is_wrapped,
    _is_draft_head as _is_draft_head,
    _find_input_box as _find_input_box,
    _find_input_box_from as _find_input_box_from,
    _has_free_prompt as _has_free_prompt,
    pane_waiting_on_user as pane_waiting_on_user,
    pane_question_excerpt as pane_question_excerpt,
    pane_at_idle_prompt as pane_at_idle_prompt,
)


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
             goal_jobs_enabled=False, long_turn_enabled=False,
             goal_requests_path=None, delivery_probe=None, card_probe=None,
             closed_fetch=None,
             repo_roots=None, issue_counts_fetch=None, git_fetch=None,
             vault_purge=None, log_fn=None, reopen_fetch=None,
             time_fn=None, sweep_budget_s=None, backlog_fetch=None,
             progress_dir=None, questions_path=None,
             owner_decision_fetch=None):
    """Scan every `claude` pane once. 29 numbered jobs per poll — 23 LIVE and 6
    RETIRED (12, 18, 23 removed in #132; 15, 17 in #102; 26 in #402), whose
    numbers are kept addressable so historical log lines and code comments
    still resolve.
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
          → ping at the cap % (`watchdog/usage.py`'s docstring is the SSOT);
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
          session/repo pane anywhere, is silently skipped and retried next sweep.
          A THIRD extension, always-on (#368, no `discord_fetch` needed — it
          only posts, it never reads Discord): any question left in the map
          once the terminal-answer prune above has run is a genuinely still-
          unanswered ❓, and `reping_stale_questions` re-asks it FRESH AND
          WHOLE once every `QUESTION_REPING_S` (24h) instead of the map's old
          age-based TTL silently deleting it — deferred, never cancelled,
          during the 00:00-05:59 Europe/Bratislava sleep window;

      (8) (only when `bounce_fetch` is given) BOUNCE BACKSTOP — open `prio:bounce`
          (gatekeeper-returned) tickets for a repo this box touches → nudge the
          repo's IDLE claude pane (busy pane = the label alone queues them; never
          interrupt mid-work), or ONE deduped Discord ping when no session runs
          (bounce_backstop, ~30 min cadence);
      (11) (only when `gkreq_fetch` is given) GK-REQUEST BACKSTOP (#30) — open
          `needs-gatekeeper` (stream→supervisor action request) tickets → nudge
          the repo's IDLE supervisor pane / deduped Discord ping when no session
          runs; reduced-stream homes never nudged (gk_request_backstop, mirror
          of job 8, ~30 min cadence). #353: a materially UNCHANGED state
          (same ticket set, no CONFIRMED supervisor-pane appear-then-
          disappear cycle) re-pings on an EXPLICIT staged schedule (24h →
          3d → 7d, holding at 7d) instead of a fixed 6h window that used
          to fire up to 4x/day forever. A single transient pane-read blip
          is only PENDING, never a confirmed disappearance (needs 2
          consecutive absent sweeps — round-2 review MAJOR-1); the dedup
          key sent to Discord is fresh per real decision instant, not per
          ticket content (round-2 MAJOR-2/A — notify's own unrelated
          14-day marker TTL used to silently swallow every staged
          re-ping); a pane target reuses the tickets-status cache's own
          origin-derived name for its root when one is known, not the
          directory basename (round-2 MAJOR-3), so the appear/disappear
          tracking stays keyed consistently across both observation types.
          #399: the SAME sweep also raises a STALE HAND-OFF ALARM — an open
          `ready-for-review`/`needs-gatekeeper` ticket untouched (updatedAt)
          for 6h+ gets ONE staged Discord alarm stream (same 24h/3d/7d
          backoff, own `stale_seen` dedup; prio:bounce / ops-channel rows
          excluded client-side) so a dead review pipeline surfaces on the
          phone instead of the owner having to notice it on GitHub
          (_stale_handoff_alarm — detection-only, never a keystroke);
      (9) /GOAL ARM CALLBACK (#403, collapsing the old guessed-boundary
          goal_autoarm) — re-evaluates every still-pending arm request
          `airuleset.py goal-arm --self` recorded (never guesses from pane
          content). `watchdog/goal.py`'s own module docstring is the single
          source of truth (goal.goal_sweep);
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
          BOUNDARIES (#39 krok 1c, collapsed by #402) — the periodic
          re-evaluation of every PENDING `/compact` request
          (`watchdog.compact.compact_sweep`). A request exists ONLY when
          one of the two proven origins created it (`compact-request
          --self`, or the SubagentStop hook on an autopilot-worker's
          return) — no context-size/idle-duration guess, no text-sniffed
          message shape. Each sweep re-checks the SAME small set of hard
          conditions (`watchdog/compact.py`'s own module docstring has the
          full list: pane idle with no draft/dialog, no live background
          tasks of the session's own, not on a `⏳`/`❓` marker or an
          unresumed API error, a 30-min per-session cooldown, and a hard
          non-refreshable age cap) — ALL unconditional, no time-boxed
          override on any of them. All pass → `/compact` is typed, logged,
          and the request cleared. Any fails → left pending for the next
          sweep, discarded outright only once the age cap is exceeded (or
          once already otherwise handled) — "no infinite waiting" is the
          age cap's job, never a refusal to re-evaluate. THE ONLY
          `/compact` SENDER left in this module (the old separate
          synchronous #65 path is now the SAME function,
          `deliver_compact`, called once immediately by
          `cmd_compact_request` before this sweep ever sees the request).
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
      (20) (only when `goal_jobs_enabled` is truthy) GOAL DARK-WATCH + LANE
          NUDGE (#403, collapsing the old 968-line goal_rearm's re-arm/
          drift/outage-forensics/untracked-recovery machinery into
          `watchdog/goal.py`, whose own module docstring is the single
          source of truth): TWO independent halves.
          `goal.goal_dark_watch` runs the shared janitor recovery for every
          live candidate pane first (unchanged shared primitives,
          `_janitor_watch_seen`/`_janitor_mark_watch`/`_janitor_clear_watch`
          / the renamed `_janitor_recover`), then cross-checks the SAME two
          independent sources #76 established — the transcript marker
          (INTENT, `scan_goal_markers`) against CC's own `◎ /goal` footer
          indicator (REALITY, `pane_goal_armed`) — but NEVER retypes a
          mismatch: it debounces the dark observation across >= 2 sweeps,
          then sends ONE Discord ping asking the user to re-run
          `/autopilot`, which re-arms via the SAME proven callback job 9
          already uses. Zero keystrokes ever, so none of the old delivery-
          discipline machinery (retry caps, streak windows, template-drift
          detection, achieved-marker forensics, `❓`-blocked suppression)
          is needed any more — a false ping just costs the user one glance
          and a cheap re-run.
          `goal.goal_lane_sweep` is the ONE watchdog-INITIATED keystroke
          left in the whole family (#365/#351's own lane-occupancy nudge,
          functionally unchanged) and needs `compact_handled_this_sweep`
          for the same coordination reason job 9 above does.
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
      (26) COMPACT-STALL WATCH — REMOVED (#402, 2026-08-12). Used to detect
          a `/compact` claim stuck on file with no matching transcript
          boundary — the companion alarm to the claim/lock system #402's
          collapse retired wholesale. With exactly two mutually-exclusive
          request origins and no shared claim file left to get stuck, there
          is nothing left for this job to watch: a request that cannot be
          delivered simply ages out via `COMPACT_REQUEST_MAX_AGE_S` (job
          14's own hard, non-refreshable cap), logged the moment it lapses.
          Number retained (not reused) for historical addressability.
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
    # the asking session leaves the map NOW, not on some later timer (it
    # feeds the statusline 'otazky' badge, which must be trustworthy per
    # stream).
    try:
        logs += prune_answered_questions(now, projects_dir=projects_dir,
                                         dry_run=dry_run)
    except Exception as e:
        logs.append("question-prune error: %r" % (e,))

    # #368 -- an unanswered ❓ that survives the pruning above (nobody
    # answered it, at the terminal or on Discord) is now re-asked FRESH AND
    # WHOLE once a day instead of quietly expiring — an extension of job 7's
    # own question-tracking mechanism (never a new job, per the FREEZE).
    # Needs only `send_fn` (always resolved by now), not `discord_fetch` —
    # it posts, it never reads Discord.
    try:
        # questions_path (None = live box map): same hermeticity reason as
        # state_path/projects_dir — a run_once TEST otherwise inherits the
        # box's REAL pending questions (observed flake, test_watchdog).
        logs += reping_stale_questions(now, send_fn, dry_run=dry_run,
                                       path=questions_path,
                                       owner_by_sid=owner_by_sid,
                                       owner_by_cwd=owner_by_cwd,
                                       owners_seen=owners_seen,
                                       account_owner=account_owner)
    except Exception as e:
        logs.append("question-reping error: %r" % (e,))

    # #461 -- the DURABLE owner-decision queue's daily re-ask (the TICKET
    # backstop of #368's session-question re-ask above). Same #368 daily-reask
    # section, never a new job. Only when a real fetch is wired (cmd_watchdog),
    # so other jobs' run_once tests stay network-free. `account_owner` is the
    # box-wide owner resolved in the pane loop above (already stream-redirected).
    if owner_decision_fetch is not None:
        try:
            logs += reping_owner_decision_tickets(
                now, send_fn, state, dry_run=dry_run,
                account_owner=account_owner, fetch=owner_decision_fetch,
                persist=lambda: save_state(state_path, state))
        except Exception as e:
            logs.append("owner-decision-digest error: %r" % (e,))

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

    # OWNER KILL-SWITCH (2026-08-12, direct order: "vypni compact watcher a aj
    # goal watcher lebo stale si ich neopravil zato mi stale promptuju kde
    # nemaju"): a flag file disables the compact / goal delivery jobs on this
    # box until the owner (or a fix he approves) removes it. Checked fresh
    # every sweep; every skip logs LOUDLY so a disabled job can never be
    # mistaken for a healthy-but-silent one. rm the file to re-enable.
    # Jobs 9/20 (#403) mirror job 14's own shape exactly: gated HERE (a cheap
    # pre-filter that skips the call, never dispatching into `watchdog.goal`
    # at all) AND checked again INSIDE `watchdog.goal`'s own entry functions
    # (defense-in-depth, same as `watchdog.compact`'s internal
    # `_owner_disabled("compact")` check) -- so a direct call that bypasses
    # this gate (a test, a future caller) still self-disables and logs.
    _goal_jobs_disabled = _owner_disabled("goal")
    _compact_jobs_disabled = _owner_disabled("compact")
    if _goal_jobs_disabled:
        logs.append("goal jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-goal (rm to re-enable)")
    if _compact_jobs_disabled:
        logs.append("compact jobs DISABLED by owner flag "
                    "~/.claude/watchdog-disable-compact (rm to re-enable)")

    # Job 9's own real body (goal.goal_sweep) is dispatched further down,
    # alongside job 20 -- both now need `compact_handled_this_sweep`
    # (goal_sweep can deliver via `deliver_with_stash`, same keystroke
    # hazard class job 20's lane-nudge already coordinates against), which
    # is not populated until after job 14's /compact senders run below.

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

    # Job 14 — /COMPACT AT TICKET BOUNDARIES (#39 krok 1c, collapsed #402):
    # only when `compact_requests_path` is given (cmd_watchdog passes
    # watchdog.compact.compact_requests_path()) — same "wired = on"
    # convention as jobs 3/7/8/11/13, so an existing caller of run_once()
    # that knows nothing about this job sees NO behavior change. `state` is
    # threaded through so a real send can mark the shared janitor's
    # provenance (#372). Best-effort.
    if compact_requests_path and not _compact_jobs_disabled:
        try:
            from watchdog import compact as _compact
            logs += _compact.compact_sweep(now, run=run, dry_run=dry_run,
                                           projects_dir=projects_dir,
                                           requests_path=compact_requests_path,
                                           state=state,
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

    # Job 9 — /goal AUTO-ARM CALLBACK (#403, collapsing the old guessed-
    # boundary `goal_autoarm`): re-evaluates every still-pending arm
    # request `airuleset.py goal-arm --self` recorded — never guesses from
    # pane content. `watchdog/goal.py`'s own module docstring is the single
    # source of truth for this model. Dispatched HERE (not in its old
    # earlier slot) because `goal_sweep` can now deliver via
    # `deliver_with_stash`, the same keystroke hazard job 20's lane-nudge
    # below already coordinates against — it needs
    # `compact_handled_this_sweep` fully populated, which only happens
    # after job 14's /compact senders run above. Only when
    # `goal_jobs_enabled` is truthy (cmd_watchdog passes True) — same
    # "wired = on" convention as jobs 13/14/16/19.
    if goal_jobs_enabled and not _goal_jobs_disabled:
        try:
            from watchdog import goal as _goal_mod
            logs += _goal_mod.goal_sweep(
                now, run=run, dry_run=dry_run, projects_dir=projects_dir,
                requests_path=goal_requests_path, state=state,
                handled=compact_handled_this_sweep, send_fn=send_fn,
                sleep_fn=sleep_fn)
        except Exception as e:
            logs.append("goal-sweep error: %r" % (e,))

    # Job 20 — GOAL DARK-WATCH + LANE-OCCUPANCY NUDGE (#403, collapsing the
    # old `goal_rearm`'s 968-line re-arm/drift/outage/forensics machinery):
    # only when `goal_jobs_enabled` is truthy — same "wired = on"
    # convention as jobs 13/14/16/19. Two independent halves, both from
    # `watchdog/goal.py` (its own module docstring is the single source of
    # truth):
    #   * `goal_dark_watch` runs the shared janitor recovery for every live
    #     candidate pane FIRST (the one sweep still guaranteed to visit
    #     every pane every ~60s regardless of pending requests, matching
    #     job 20's old visit cadence), then pings — NEVER types — a
    #     session whose transcript says armed but whose footer has gone
    #     dark, debounced across >= 2 sweeps before it ever pings.
    #   * `goal_lane_sweep` is the ONE watchdog-INITIATED keystroke left in
    #     the whole family (#365/#351's own lane-occupancy nudge,
    #     functionally unchanged) and needs `compact_handled_this_sweep`
    #     for the identical reason job 9 above does.
    if goal_jobs_enabled and not _goal_jobs_disabled:
        try:
            from watchdog import goal as _goal_mod
            logs += _goal_mod.goal_dark_watch(
                now, run=run, state=state, send_fn=send_fn,
                dry_run=dry_run, projects_dir=projects_dir,
                sleep_fn=sleep_fn, time_fn=time_fn,
                sweep_deadline=tail_deadline,
                requests_path=goal_requests_path)
        except Exception as e:
            logs.append("goal-dark-watch error: %r" % (e,))
        try:
            from watchdog import goal as _goal_mod
            logs += _goal_mod.goal_lane_sweep(
                now, run=run, dry_run=dry_run, projects_dir=projects_dir,
                state=state, handled=compact_handled_this_sweep,
                backlog_fetch=backlog_fetch, send_fn=send_fn,
                sleep_fn=sleep_fn, time_fn=time_fn,
                sweep_deadline=tail_deadline)
        except Exception as e:
            logs.append("goal-lane-sweep error: %r" % (e,))

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

    # Job 26 — COMPACT-STALL WATCH — REMOVED (#402, 2026-08-12). Used to
    # watch the shared /compact claim file for a stuck entry; that whole
    # claim system was retired by the compact collapse (see run_once's own
    # docstring paragraph (26)). A request that cannot be delivered now
    # simply ages out via job 14's own hard cap, logged the moment it does.

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
