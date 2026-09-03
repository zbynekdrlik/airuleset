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
      Probe-first reconcile (#520): job 6 waits for the PRINTED reset before its
      first `continue` BY DESIGN — a `continue` before the window frees re-hits
      the limit (the frozen rationale above) — and its SESSLIMIT_RETRY_S
      post-reset retries are bounded (5-min spacing, capped), not a tight hammer.
      The "the window may free EARLIER, and idle-parking a session that BELIEVES
      a stale limit is banned" half of probe-first is covered by the
      reset-time-AGNOSTIC job-4 stuck-check nudge (its text now carries the
      probe-first instruction) + the `verify-launched-work-liveness` skill
      (the session-limit vs monthly-spend-cap classes) — NOT a new behaviour here.
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
import os
import re
import time
from pathlib import Path

# #564: the source-of-truth authority map, for deriving _REDUCED_STREAM_USERS
# below. cli_fleet is a zero-import constants leaf, so a top-level import here
# cannot create a circular import (unlike the watchdog.* facade re-exports
# further down, which are deliberately mid-file).
import cli_fleet

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
# #602 -- the resume prompt job 1 types INSTEAD of a bare `continue` when the
# api-error is the OAuth-rotation 401 REVOKED class (`is_oauth_revoked`). A
# rotation revokes the old access token INSTANTLY and the SAME kill-window
# terminates in-flight background agents; a bare `continue` resumes the main
# session but leaves that dead agent work lying (the gk incident 2026-08-20).
# So this text (a) still tells the session to continue -- the fresh token is
# already on disk, `continue`-class text resumes -- and (b) names the
# re-dispatch-from-durable-state duty (subagent-continuation.md) with a
# CONDITIONAL clause ("if any agent died…"), so it is HARMLESS for a solo
# session with no agents (it just reads it and continues). Starts with the
# `oauth-resume:` prefix registered in `_MACHINE_PROMPT_PREFIXES` below, so the
# submitted `user` turn is never misread as a human answer (`_last_human_prompt_ts`
# / `_is_genuine_human_prompt`) -- the same guarantee bare `continue` gets from
# `_MACHINE_PROMPT_EXACT`. Delivered through job 1's normal generic-nudge path
# (`send_verified` / `deliver_with_stash`), not a new mechanism.
OAUTH_REVOKED_NUDGE_TEXT = (
    "oauth-resume: práve prebehla rotácia OAuth tokenu — starý access token bol "
    "revoknutý a session dostala 401 (Please run /login). Čerstvý token je už na "
    "disku, takže pokračuj v rozrobenej práci. DÔLEŽITÉ: tá istá rotácia mohla "
    "TERMINÁLNE zabiť tvojich background agentov (Agent … failed: … 401 OAuth "
    "access token has been revoked) a watchdog ich NEVIE oživiť. Ak ti nejaký "
    "padol, nečakaj naň ako na „waiting for background agents“ — re-dispatchni "
    "každú padnutú prácu z durable state (subagent-continuation.md): otvorený "
    "PR/branch, gh issue stav, súbory na disku. Ak žiadny nepadol, len pokračuj."
)
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
    "signál, ale ani neintervenuj bez dôkazu o smrti. A AK je dôvod ticha 429 limit "
    "(vypísaný reset) — PROBNI / re-dispatchni HNEĎ, tvoj vlastný turn je dôkaz voľnej "
    "kapacity; nikdy slepé čakanie na vypísaný čas, pri vrátenom 429 bounded ~10–15 min "
    "re-proby, medzitým rob všetku ne-dispatch prácu (#520)."
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


# #433 item G step 4 -- the decision / parse / state-file core (the two
# `_pane_live_*` live-task pane readers just above's siblings, plus the whole
# usage-cap / session-limit / reset-epoch / decide / decide_working / load_state
# / save_state cluster that used to sit below the transcripts facade) now lives
# in `watchdog/decide.py`, re-exported here with the same positional-facade
# convention as the earlier steps. This ONE import covers BOTH originally
# non-contiguous blocks (nothing between them referenced these names at def
# time; step 1 already emptied the transcripts block in between). Every
# `watchdog.<name>` seam (run_once jobs 1/4/6 -- decide/decide_working/save_state
# /parse_reset_epoch*/is_usage_cap/pane_session_limited -- goal / compact /
# cross_stream / janitor, hooks, tests) resolves unchanged. `_LIVE_BG_TASK_RX`
# (bound just above) stays in __init__ and is read by decide.py at its call site
# via `watchdog._LIVE_BG_TASK_RX` (C4), like pane_text.py's `_QUEUED_COMPACT_RX`.
from watchdog.decide import (  # noqa: E402
    _pane_live_shell_evidence as _pane_live_shell_evidence,
    _pane_live_task_count as _pane_live_task_count,
    _USAGE_CAP_RX as _USAGE_CAP_RX,
    _TRANSIENT_RX as _TRANSIENT_RX,
    is_usage_cap as is_usage_cap,
    is_account_dispatch_block as is_account_dispatch_block,
    is_oauth_revoked as is_oauth_revoked,
    _SESSION_LIMIT_RX as _SESSION_LIMIT_RX,
    _RESET_TIME_RX as _RESET_TIME_RX,
    _RESET_MONTH_NUM as _RESET_MONTH_NUM,
    _RESET_TZ_RX as _RESET_TZ_RX,
    SESSLIMIT_RETRY_S as SESSLIMIT_RETRY_S,
    SESSLIMIT_MAX_TRIES as SESSLIMIT_MAX_TRIES,
    DATED_RESET_STALE_GRACE_S as DATED_RESET_STALE_GRACE_S,
    pane_session_limited as pane_session_limited,
    parse_reset_epoch as parse_reset_epoch,
    _reset_epoch_from_scanned_text as _reset_epoch_from_scanned_text,
    parse_reset_epoch_from_error_text as parse_reset_epoch_from_error_text,
    session_user_stopped as session_user_stopped,
    _human_clock as _human_clock,
    decide as decide,
    DECLARED_WAIT_GRACE_S as DECLARED_WAIT_GRACE_S,
    DECLARED_WAIT_MAX_S as DECLARED_WAIT_MAX_S,
    WORKING_RESPONDED_BACKOFF_SCHEDULE_S as WORKING_RESPONDED_BACKOFF_SCHEDULE_S,
    _CLOCK_RX as _CLOCK_RX,
    declared_wait_until as declared_wait_until,
    decide_working as decide_working,
    load_state as load_state,
    save_state as save_state,
)


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
    _submit_confirmed as _submit_confirmed,
    count_live_workers as count_live_workers,   # #486 G2 -> consumed by G3
    lane_has_live_evidence as lane_has_live_evidence,   # #571 -> lane working-no-tasks
    live_lane_labels as live_lane_labels,               # #605 -> compact SKIP live-tasks log detail
    transcript_current_context as transcript_current_context,
    transcript_last_marker as transcript_last_marker,
    transcript_last_marker_line as transcript_last_marker_line,
    transcript_last_marker_bounded as transcript_last_marker_bounded,   # #599 bounded ❓-veto read
    transcript_last_assistant_text as transcript_last_assistant_text,
    transcript_last_backlog_empty_ts as transcript_last_backlog_empty_ts,   # #764 fulfilled-rearm 🏁 proof
    session_live_bg_bash as session_live_bg_bash,             # #599 pure bg-bash pairing
    session_has_live_bg_bash as session_has_live_bg_bash,     # #599 bg-bash liveness (#848 retired the compact veto)
    live_bg_bash_ids as live_bg_bash_ids,                     # #605 pure bg-bash live ids (log detail)
    session_live_bg_bash_ids as session_live_bg_bash_ids,     # #605 bg-bash live ids I/O wrapper
    _read_jsonl_byte_tail as _read_jsonl_byte_tail,           # #599 bounded-seek reader
    _jsonl_entry_epoch as _jsonl_entry_epoch,                       # #645
    _transcript_resume_boundary_at as _transcript_resume_boundary_at,  # #645
    RESUME_GAP_BEFORE_S as RESUME_GAP_BEFORE_S,                     # #645
    RESUME_BURST_AFTER_S as RESUME_BURST_AFTER_S,                   # #645
    question_repoke_run as question_repoke_run,           # #522
    question_repoke_streak as question_repoke_streak,     # #522
    supervisor_responded_to_nudge as supervisor_responded_to_nudge,
    subagent_active as subagent_active,
    _count_live_subagents as _count_live_subagents,
    newest_subagent_transcript as newest_subagent_transcript,
    supervisor_transcript_for_subagent as supervisor_transcript_for_subagent,
    _entry_has_tool_use as _entry_has_tool_use,
    _ends_with_toolcall as _ends_with_toolcall,
    transcript_text_toolcall_stall as transcript_text_toolcall_stall,
    transcript_worker_finished as transcript_worker_finished,   # #587 finish-immediate
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


# #433 item G step 4 -- the usage-cap / session-limit / reset-epoch-parse /
# `decide` / `decide_working` / `load_state` / `save_state` cluster that used to
# live here (block 864-1411 at design time) now lives in `watchdog/decide.py`,
# re-exported by the single positional facade above (search for "item G step 4"
# near the `_pane_live_*` readers). Its regexes/constants (`_USAGE_CAP_RX`,
# `_SESSION_LIMIT_RX`, `_RESET_TIME_RX`, `SESSLIMIT_*`, `DECLARED_WAIT_*`, ...)
# moved and are re-exported with it, so every `watchdog.<name>` seam resolves
# unchanged.


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
    _proc_start_epoch as _proc_start_epoch,                     # #645
    _pane_claude_pid as _pane_claude_pid,                       # #645
    _pane_claude_start_epoch as _pane_claude_start_epoch,       # #645
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
    send_verified as send_verified,
    submit_own_draft_verified as submit_own_draft_verified,
    submit_own_goal_verified as submit_own_goal_verified,
    _await_submit_confirmed as _await_submit_confirmed,
    _await_typed_landed as _await_typed_landed,
    _subagent_nudge_signature as _subagent_nudge_signature,
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

from watchdog.discord_replies import (  # noqa: E402
    DISCORD_REPLY_MAX_CHARS as DISCORD_REPLY_MAX_CHARS,
    _DREPLY_DONE_CAP as _DREPLY_DONE_CAP,
    _DQ_POSTED_CAP as _DQ_POSTED_CAP,
    _MENTION_TOKEN_RX as _MENTION_TOKEN_RX,
    _CONTROL_CHAR_RX as _CONTROL_CHAR_RX,
    DREPLY_TICKET_FALLBACK_S as DREPLY_TICKET_FALLBACK_S,
    DREPLY_NOPANE_FALLBACK_S as DREPLY_NOPANE_FALLBACK_S,
    _TICKET_NUM_RX as _TICKET_NUM_RX,
    DREPLY_CHANNEL_MEMORY_S as DREPLY_CHANNEL_MEMORY_S,
    _merge_grace_questions as _merge_grace_questions,
    _refresh_channel_memory as _refresh_channel_memory,
    _refresh_posted_memory as _refresh_posted_memory,
    _orphan_floor as _orphan_floor,
    _ticket_fallback_text as _ticket_fallback_text,
    _gh_comment as _gh_comment,
    compose_reply_prompt as compose_reply_prompt,
    _record_dreply_typed as _record_dreply_typed,
    _is_dreply_machine_text as _is_dreply_machine_text,
    _box_holds_our_own_text as _box_holds_our_own_text,
    deliver_discord_replies as deliver_discord_replies,
)


from watchdog.discord_api import (  # noqa: E402
    clean_reply_text as clean_reply_text,
    parse_discord_reply as parse_discord_reply,
    _snowflake_ts as _snowflake_ts,
    _discord_get as _discord_get,
    fetch_channel_messages as fetch_channel_messages,
    _react_ok as _react_ok,
    parse_discord_card_reply as parse_discord_card_reply,
    fetch_reaction_users as fetch_reaction_users,
    _reacted_by_owner as _reacted_by_owner,
)


# A box with NO pane for the asking session may not be the pane's HOST — a
# hosted stream's session (montalu claude inside newlevel's tmux) is delivered
# by the HOST watchdog's keystrokes. The no-pane fallback therefore waits
# longer, so the host wins the race and a double gh comment cannot happen; for
# a genuinely dead session it still fires (later), never silently.
# --- #449: the NEVER-SILENT floor for owner answer attempts ---------------- #
# How long job 7 keeps fetching a QUESTION channel after its last live/grace
# entry vanished (state["dreply_channels"], {channel: {"ts", "q"}}). This is
# what lets the orphan floor below see a reply that lands AFTER the grace
# window (or against an empty map — the david 2026-08-13 incident state):
# without a tracked entry the channel set used to be empty and job 7 returned
# before fetching anything at all, so the loss left zero journal lines.
# Only messages younger than this (by their own snowflake timestamp) can
# trigger the orphan ping — the 25-message fetch window routinely contains
# ancient history (feature rollout, a long-idle thread), and pinging about a
# weeks-old message the user has long moved past is pure noise.
ORPHAN_ANSWER_WINDOW_S = 48 * 3600
_DISCORD_EPOCH_MS = 1420070400000


from watchdog.questions import (  # noqa: E402
    _orphan_answer_reason as _orphan_answer_reason,
    _orphan_ping_text as _orphan_ping_text,
    _foreign_user as _foreign_user,
    _foreign_session_info as _foreign_session_info,
    _foreign_questions as _foreign_questions,
    _foreign_drop_question as _foreign_drop_question,
    _last_human_prompt_ts as _last_human_prompt_ts,
    _is_genuine_human_prompt as _is_genuine_human_prompt,   # #522
    _last_real_turn_ts as _last_real_turn_ts,
    _transcript_for_session as _transcript_for_session,
    prune_answered_questions as prune_answered_questions,
    # reping_stale_questions is a PERMANENT NO-OP tombstone (#795 — the daily
    # question re-ask is retired; a ❓ is asked once, the footer `U N` holds
    # it, the owner invokes processing himself, #606). Kept re-exported for
    # stale callers.
    reping_stale_questions as reping_stale_questions,
    # reping_owner_decision_tickets is a PERMANENT NO-OP tombstone (#707 —
    # the daily owner-decision digest is retired; its _fetch/_digest_block
    # helpers were deleted outright). Kept re-exported for stale callers.
    reping_owner_decision_tickets as reping_owner_decision_tickets,
)


from watchdog.pane_text import (  # noqa: E402
    _input_line_text as _input_line_text,
    _input_box_head_text as _input_box_head_text,
    _classify_boundary as _classify_boundary,
    _above_input_box as _above_input_box,
    _above_box_scan as _above_box_scan,
    _pane_has_queued_compact as _pane_has_queued_compact,
    _trailing_bottom_chrome as _trailing_bottom_chrome,
)


# Prompts TYPED BY MACHINERY into a session (watchdog nudges/deliveries,
# auto-armed /goal, harness task-notifications, slash-command echoes) must
# never count as "the user answered the pending ❓ at the terminal".
_MACHINE_PROMPT_EXACT = ("continue",)
_MACHINE_PROMPT_PREFIXES = (
    # #602 -- the OAuth-rotation 401-revoked resume prompt (OAUTH_REVOKED_NUDGE_TEXT)
    # is machine-injected by job 1, not a human answer, so its `user` turn must
    # not count as "the user answered at the terminal" (mirrors bare `continue`
    # in _MACHINE_PROMPT_EXACT).
    "oauth-resume:",
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


_REAL_TURN_TYPES = ("user", "assistant")


# #368 -- the daily question re-ask -- is RETIRED (#795, owner ruling
# 2026-09-01, verbatim: "aha mame aj reask ale ani ten uz nie je potrebny
# dokial plati U v peticke tak ja nepotrebujem aby sa nieco dokolecka
# pytalo, sam si vyvolam spracovanie U"). #368 used to repost every
# unanswered ❓ FRESH AND WHOLE at least once a day instead of the map's old
# age-based TTL silently deleting it — that premise no longer holds: a
# question is asked ONCE, the footer `U N` badge holds it visible for as
# long as it stays open, and the owner invokes its processing himself via
# the "U N?" step-by-step flow (#606). `reping_stale_questions` is a
# permanent no-op tombstone in questions.py, its run_once registry entry +
# `QUESTION_REPING_S` cadence constant are gone, and the question map itself
# (`prune_answered_questions`, the terminal-answer prune + #407 ghost-pair
# collapse) is untouched — see the #461/#707 retirement note immediately
# below for the SAME pattern applied to the sibling owner-decision digest.


# #461 -- the DURABLE owner-decision queue's daily re-ask -- is RETIRED
# (#707, owner ruling 2026-08-26). The digest summarised every open
# `needs-answer`/`needs-decision` ticket across the box's repos into ONE
# daily Discord ping addressed to `account_owner` -- the first-owner-seen
# pane-scan coin flip -- with none of the `owners_seen` ambiguity guard its
# siblings carry, so on multi-owner dev2 it delivered montalu client-ticket
# content into David's thread (a cross-subject leak; #489 had gated only
# reduced-authority boxes). The owner abolished the WHOLE message class:
# `reping_owner_decision_tickets` is a permanent no-op tombstone in
# questions.py, its fetch/digest-block helpers and the run_once wiring are
# deleted, and notify.SUPPRESSED_ALERT_PREFIXES denylists the
# `owner-decision-digest` dedup_key class so even stale code cannot ping.
# The tickets still surface via the footer `U N` badge + the per-ticket ❓
# pings (the critical paths, #693/#704).
#
# OWNER_DECISION_LABELS OUTLIVES the digest -- its live consumers are job
# 32's mechanical U-label clear (watchdog/u_labels.py) and the invariant
# below. It is the DECISION SUBSET of cli_quals.USER_WAITING_LABELS
# (the footer/stop-proof `U N` family, #468). #512 DIVERGED the two on purpose:
# USER_WAITING_LABELS gained `needs-acceptance` (a DONE ticket awaiting the
# owner's/client's sign-off) and #601 added `needs-owner-action` (a physical
# owner step) — both are deliberately OUTSIDE the decision subset job 32
# clears on an owner answer. Kept as
# a flat literal here (never a module-level `from cli_quals import`) because the
# import direction is airuleset -> watchdog and watchdog is a 60s systemd timer;
# a reverse import would add import-time cost to every sweep. The SUBSET
# invariant (every owner-decision label ∈ USER_WAITING_LABELS; the excluded
# set is exactly {needs-acceptance, needs-owner-action}) is pinned by
# TestOwnerDecisionLabelsInSync.
OWNER_DECISION_LABELS = ("needs-answer", "needs-decision")


def _box_authority():
    """This BOX's autopilot authority profile (full / branch-merge /
    fork-no-merge), resolved cwd-INDEPENDENTLY from the OS user's fixed
    `AUTHORITY_BY_USER` mapping.

    #489: born to gate the owner-decision digest (RETIRED in #707 — its
    coin-flip addressing leaked cross-subject anyway); the doctrine outlives
    it: any BOX-WIDE decision keyed to the box owner (live consumer: job 24's
    delivery-stall watch, #667) gates on "is THIS BOX's owner the genuine
    recipient" — fixed by the
    box identity, never by whatever cwd the watchdog happens to run from.
    Deliberately NOT `resolve_authority()`: that honours a per-repo
    `airuleset:authority=full` CLAUDE.md marker, and a stray such marker in the
    watchdog's cwd must NEVER re-open the cross-stream leak this gate closes. It
    uses the SAME two registries `_authority_decision` does — the reduced-stream
    `AUTHORITY_BY_USER` map, then the explicit full-authority allow-list — never a
    parallel derivation, just the marker-free half. Deferred `import airuleset`
    (the idiom already used in this package's `goal.py` + `cli_quals.py`) avoids
    the module-load cycle. airuleset#827: an unmapped user now fails SAFE to
    `fork-no-merge` (the prior `.get(user, "full")` fail-OPEN default is removed);
    the real full boxes (gk = gatekeeper, dev1/dev2 = newlevel) resolve `full`
    from `FULL_AUTHORITY_USERS`."""
    import airuleset
    user = airuleset._current_user()
    # airuleset#827 (review): EXPLICIT membership, mirroring _authority_decision's
    # marker-free half — no `.get(user) or ...` truthiness dependency (a falsy
    # profile value would otherwise silently degrade to fork-no-merge). Map row
    # wins first (restrictive), then the explicit full allow-list, then fail-SAFE.
    if user in airuleset.AUTHORITY_BY_USER:
        return airuleset.AUTHORITY_BY_USER[user]
    # airuleset#839: the ci-runner recognition (`_is_github_ci_runner`) is
    # DELIBERATELY NOT wired here — the watchdog never runs on the GitHub CI
    # runner, so it would be a dead branch. Identity is already the hardened
    # `_current_user()` (uid-based, env-spoof-proof); do not "fix" the asymmetry
    # with the resolver's ci-runner branch.
    if user in airuleset.FULL_AUTHORITY_USERS:
        return "full"
    return "fork-no-merge"


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
    GOAL_TYPE_VERIFY_RETRIES as GOAL_TYPE_VERIFY_RETRIES,
    _type_verify_landed as _type_verify_landed,
    _settle_type_verify as _settle_type_verify,
    _type_two_phase_head_checkpoint as _type_two_phase_head_checkpoint,
    _type_literal_verified as _type_literal_verified,
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
    STRAY_PREFIX_MAX_OFFSET as STRAY_PREFIX_MAX_OFFSET,
    _own_prefix_stray_offset as _own_prefix_stray_offset,
    GOAL_ARM_LEFTOVER_MIN_SUBSTR as GOAL_ARM_LEFTOVER_MIN_SUBSTR,
    _box_norm_from_capture as _box_norm_from_capture,
    _box_is_own_leftover as _box_is_own_leftover,
    _box_own_with_short_prefix as _box_own_with_short_prefix,
    _OWN_NUDGE_SUBMIT_PREFIXES as _OWN_NUDGE_SUBMIT_PREFIXES,
    _own_nudge_submit_prefix as _own_nudge_submit_prefix,
)


_DREPLY_TYPED_TTL_S = 48 * 3600


# --------------------------------------------------------------------------- #
# #297/#298 -- extending job 7's poll pass with two more Discord signals a
# session can react to: a REPLY on a completion CARD (#298 -- reopen the
# ticket with the remark) and a ❓/❔ REACTION on any TRACKED bot message
# (#297 -- ask the sending stream about it). Both reuse the SAME
# `fetch(ch, token)` poll loop and the SAME `known_owner_ids` security
# boundary the ❓-reply flow already established; neither is a new job (the
# FREEZE only permits extending job 7's existing mechanism).
# --------------------------------------------------------------------------- #

from watchdog.card_flags import (  # noqa: E402
    BOUNCE_REMARK_LABEL as BOUNCE_REMARK_LABEL,
    _repo_live_pane as _repo_live_pane,
    _nudge_repo_pane as _nudge_repo_pane,
    _card_remark_comment_body as _card_remark_comment_body,
    compose_card_reopen_nudge as compose_card_reopen_nudge,
    _gh_call as _gh_call,
    _card_reopen_flow as _card_reopen_flow,
    _flagged_emoji as _flagged_emoji,
    _flag_target as _flag_target,
    _flag_delivery_target as _flag_delivery_target,
    _deliver_flag_prompt_to_exact_session as _deliver_flag_prompt_to_exact_session,
    _FLAG_EMOJI as _FLAG_EMOJI,
    _FLAG_PROMPT_TEMPLATE as _FLAG_PROMPT_TEMPLATE,
    compose_flag_prompt as compose_flag_prompt,
)


# #515 -- the mechanical U-label lifecycle (job 32 `reconcile_u_labels` + the
# job-7 `_delivered` capture `capture_answered_ticket`). u_labels.py has ONE
# top-level `import watchdog` and reaches every resident name as
# `watchdog.<name>` at CALL time (the cluster-C idiom), so this re-export can
# land anywhere in the sequence with no load-time dependency on another leaf.
from watchdog.u_labels import (  # noqa: E402
    reconcile_u_labels as reconcile_u_labels,
    capture_answered_ticket as capture_answered_ticket,
    _clear_owner_question_labels as _clear_owner_question_labels,
    _u_reconcile_decide as _u_reconcile_decide,
    U_RECONCILE_STATE_KEY as U_RECONCILE_STATE_KEY,
    U_RECONCILE_TTL_S as U_RECONCILE_TTL_S,
)


# --- #298: reply on a completion CARD -> reopen the ticket ----------------

# --- #297: ❓/❔ reaction on a TRACKED bot message -> ask the session -------

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
# The reduced-authority sub-dev streams that participate in the gatekeeper<->
# sub-dev bounce/gkreq flow -- consumed by `_bounce_quals` (scope a pane's own
# home to its stream label vs the full-authority exclude-all branch),
# `_gkreq_supervisor_root` (skip a reduced box's own HOME) and
# `_origin_reduced_stream` (attribute a `handed-by:<x>` gk request).
#
# #564: DERIVED from AUTHORITY_BY_USER (every non-"full" key) rather than
# hand-listed. Semantics: reduced authority <=> sub-dev stream <=> bounce/gkreq
# participant, so the AUTHORITY_BY_USER key set IS exactly this registry -- and
# onboarding a new stream into that one map now auto-registers it here too, so
# the recurring staleness class (a live stream missing from a hand-typed tuple:
# `_gkreq_supervisor_root` mis-classifies its pane as a FULL-authority
# supervisor, or `_bounce_quals` scopes it to the exclude-all branch instead of
# its own label -- the montalu2/3/4 #251 and david2/3/4 #326 regressions, then
# simap1/miva1/montalu5-8/david1 found still missing by the issue-561 audit)
# cannot recur. The `!= "full"` filter is defensive (no "full" key exists in
# the map today, but a future one must never leak into this reduced registry).
#
# Read from `cli_fleet` (the zero-import constants leaf = the source of truth)
# at MODULE LOAD, NOT the `airuleset` facade re-export (not guaranteed resolved
# at watchdog import time) -- safe because cli_fleet imports nothing, so this
# cannot create a circular import. This is a STATIC registry consumed as a
# tuple attribute by its call sites and tests (`x in wd._REDUCED_STREAM_USERS`),
# not a per-call value; the rename TRANSITION layer that IS test-patchable
# lives in `_stream_rename_equivalents()` (call-time facade read of
# STREAM_RENAME_ALIASES), so the static registry and the dynamic alias compose
# cleanly. AUTHORITY_BY_USER carries both a rename base and its numbered alias
# during a transition (e.g. david + david1); the duplicates are equivalents and
# every consumer unions/dedupes, so a slice is never narrowed by carrying both.
_REDUCED_STREAM_USERS = tuple(
    u for u, _prof in cli_fleet.AUTHORITY_BY_USER.items() if _prof != "full")

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

# (#551) The orphaned gk-hand-off-marker backstop (job 36) runs on a GENEROUS
# cadence: an orphaned mutated marker is a RARE event, and each sweep bursts up
# to `_SELFSERVICE_MAX_CANDIDATES` `gh issue view` calls (comments) plus a
# paginated timeline read for the ≈0 candidates past the cheap gate — too
# costly for the 30-min gk-request cadence, cheap at 6h (#172/#504 budget).
GKORPHAN_INTERVAL = 6 * 3600

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

# (#607) The 24h-push kontrakt (owner 2026-08-21): a hand-off untouched
# (updatedAt) for this long — 24h of WORKING time, Sat/Sun in Europe/Bratislava
# EXCLUDED (`working_time.working_seconds_between`) — gets a DURABLE ticket
# COMMENT ("gk, vieš o tom?") + a gk-session nudge, so the gatekeeper KNOWS the
# request is aging even when no owner is watching GitHub. Deliberately DISTINCT
# from `GKREQ_STALE_HANDOFF_S` (6h): that fires ONE Discord alarm to the OWNER's
# phone (detection-only, #399); this fires a durable comment + a session nudge to
# the GATEKEEPER at the harder 24h-working contract — two orthogonal channels,
# each with its own dedup namespace (`stale_seen` vs `stale_push_seen`).
GK_STALE_PUSH_S = 24 * 3600

# (#607) The durable "does the gk even know?" comment posted on a >24h-working
# stale hand-off. Re-comment cadence is per-ticket 24h/3d/7d (GKREQ_REPING_SCHEDULE_S).
GK_STALE_PUSH_COMMENT = (
    "⚠️ **gk-freshness (#607): tento hand-off čaká na akciu gatekeepera už "
    ">24h pracovných dní bez pohybu.**\n> Vieš o ňom? Sprac ho (review / merge / "
    "unblock) alebo komentárom napíš, na čo čaká. Ak sa nič nezmení, pripomeniem "
    "sa zas (watchdog job 11, staged 24h/3d/7d).")

# (#607) The gk-session nudge naming the freshly-pushed stale hand-offs — reuses
# the existing job-11 idle-pane keystroke channel, fired only when a supervisor
# session is at true rest (the durable comment above is the record either way).
GK_STALE_PUSH_NUDGE = (
    "gk-freshness backstop (#607): hand-off tikety %s čakajú >24h pracovných dní "
    "na akciu gatekeepera bez pohybu — otázka či o nich vieš. Sprac ich "
    "(review/merge/unblock) alebo komentárom napíš na čo čakajú; na každom už je "
    "durable pripomienka.")


from watchdog.handoff_alarm import (  # noqa: E402
    _parse_gh_ts as _parse_gh_ts,
    _normalize_gkreq as _normalize_gkreq,
    _stale_handoff_alarm as _stale_handoff_alarm,
    _stale_handoff_push as _stale_handoff_push,
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


from watchdog.wedge import (  # noqa: E402
    _session_is_waiting as _session_is_waiting,
    prompt_wedge_check as prompt_wedge_check,
)


_GOAL_CONT_OK = ("```", "─", "•", "**", "❓", "❯", "⎿", "●", "✻")

# CC's AMBIENT status while background subagents run ("✻ Waiting for N
# background agents to finish") stays on screen although the turn has ENDED
# and the prompt is a free bare `❯` — the exact autopilot shape (main idle,
# worker in background; `pane_at_idle_prompt` passes by design). `_pane_has_bg_
# agent` must not read it as live work blocking dark-watch re-DETECTION
# (restreamer 2026-07-24). But a KEYSTROKE's Enter is SWALLOWED here, so
# `_pane_busy_waiting` (#714/#720) DEFERS a /goal submit into it — never type
# into the Waiting state (empirical swallow). Every OTHER `Waiting for` blocks.
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


# #433 item G step 10 -- the goal-marker transcript scan (`scan_goal_markers`
# + its `_goal_marker_content`/`_parse_goal_marker` parsers), the `◎ /goal`
# footer/header read (`pane_goal_armed`), the installed-SKILL.md resolver
# (`goal_templates_path`), and job 9's virgin-arm human-recency gate
# (`_goal_autoarm_recent_human_activity`) now live in `watchdog/goal_scan.py`,
# re-exported in place with the same positional-facade convention as the other
# split blocks, so every `watchdog.<name>` seam (goal.py's deliver_goal +
# job-9 auto-arm, and the tests) resolves unchanged. `GOAL_MARK_TAIL_BYTES`
# moved with them (scan_goal_markers' def-time default, whose __init__ home sat
# BELOW this re-import position) and is re-exported HERE; the goal-format
# constants it reads (`_GOAL_LCS_OPEN`/`_GOAL_LCS_CLOSE`, `GOAL_ARM_ACTIVE_PREFIX`,
# `_GOAL_ARM_PROBE`, `_GOAL_HEADER_INDICATOR_RX`, `GOAL_AUTOARM_RECENT_HUMAN_S`)
# stay in `__init__` as live `watchdog.*` seams, read call-time.
from watchdog.goal_scan import (  # noqa: E402
    _goal_autoarm_recent_human_activity as _goal_autoarm_recent_human_activity,
    goal_templates_path as goal_templates_path,
    _goal_marker_content as _goal_marker_content,
    _parse_error_clear_payload as _parse_error_clear_payload,
    _parse_goal_marker as _parse_goal_marker,
    _newest_marker as _newest_marker,
    scan_goal_markers as scan_goal_markers,
    seed_goal_marker as seed_goal_marker,
    pane_goal_armed as pane_goal_armed,
    _tmux_client_recent_input as _tmux_client_recent_input,
    GOAL_MARK_TAIL_BYTES as GOAL_MARK_TAIL_BYTES,
    GOAL_MARK_SEED_CAP_BYTES as GOAL_MARK_SEED_CAP_BYTES,
    GOAL_CLIENT_INPUT_VETO_S as GOAL_CLIENT_INPUT_VETO_S,
)

# #486 G3 -- the G1 session-heartbeat reader + reaper, now CONSUMED (G1/G2 left
# these unwired; G3 is the consumer). Re-exported so the render lane path
# (watchdog/goal.py) reaches them through the same `watchdog.<name>` seam every
# other reader uses, and tests patch them at the package level. This block sits
# AFTER `scan_goal_markers` above BY NECESSITY: session_status.py does a guarded
# top-level `from watchdog import scan_goal_markers`, so importing it before that
# name is bound here would degrade its goal-armed read to `None` (the import
# would silently fail into the module's own `except`).
from watchdog.session_status import (  # noqa: E402
    read_status as read_status,
    reap_stale_status as reap_stale_status,
    status_dir as status_dir,
)


# #433 item G step 11 -- the job-21 LONG-TURN WATCH family (long_turn_watch +
# pane_turn_elapsed / _human_duration / _human_age_desc + the LONG_TURN_*
# thresholds), job 20's pane-liveness readers (_reconcile_candidate_panes /
# _pane_has_bg_agent) and the /compact-in-flight + queued-/compact guards
# (_pane_compacting / COMPACTING_MARKER / _QUEUED_COMPACT_RX) now live in
# `watchdog/long_turn.py`, re-exported in place with the same positional-facade
# convention as the other split blocks, so every `watchdog.<name>` seam
# (run_once job 21, `watchdog/goal.py`'s job-20 dark-watch / lane-sweep,
# `watchdog/pane_text.py`, hooks, tests) resolves unchanged. long_turn.py is a
# back-reference module reaching _default_run / _above_box_scan / _pane_location
# / _BG_AGENTS_WAIT_RX call-time as `watchdog.<name>`; no moved constant is a
# def-time default, so all six move + re-export with no from-import.
from watchdog.long_turn import (  # noqa: E402
    _human_age_desc as _human_age_desc,
    _AGENT_STRIP_ROW_RX as _AGENT_STRIP_ROW_RX,
    _reconcile_candidate_panes as _reconcile_candidate_panes,
    _pane_has_bg_agent as _pane_has_bg_agent,
    COMPACTING_MARKER as COMPACTING_MARKER,
    _QUEUED_COMPACT_RX as _QUEUED_COMPACT_RX,
    _TURN_ELAPSED_RX as _TURN_ELAPSED_RX,
    pane_turn_elapsed as pane_turn_elapsed,
    _pane_compacting as _pane_compacting,
    LONG_TURN_THRESHOLD_S as LONG_TURN_THRESHOLD_S,
    LONG_TURN_SAME_TURN_TOLERANCE_S as LONG_TURN_SAME_TURN_TOLERANCE_S,
    _human_duration as _human_duration,
    long_turn_watch as long_turn_watch,
)


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
# `watchdog.goal.goal_dark_watch` either RE-ARMS a CONFIRMED-dead loop
# (#478/#524 -- records a request for job 9 to type, ONLY after a hardened
# death-confirmation run; an idle-but-alive flicker never reaches it) or, when
# it cannot self-heal the loop, sends ONE Discord ping asking the user to
# re-run `/autopilot`. For the RE-ARM path `goal_dark_watch` types nothing --
# that keystroke happens in `goal_sweep`/`deliver_goal` (the #617 stranded-
# truncated-/goal CLEAR is the one keystroke path in `goal_dark_watch` itself,
# `_clear_stranded_truncated_goal` -> `_janitor_clear_box`), so the delivery-
# discipline concerns (fresh-capture verify, the #36 truncation hazard,
# `deliver_with_stash` coordination) apply to `goal_sweep`'s own delivery of a
# genuinely PENDING (explicitly recorded) request -- INCLUDING a `dark-rearm`
# one -- and to `goal_lane_sweep`'s watchdog-initiated nudge.
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
# #617 (live montalu1@subdev, 2026-08-21) — the #487 MINOR-2 "widen only on a
# real render" clause fired a SECOND time, for the CC PENDING-UPDATE
# notification. When an update is downloaded-not-yet-restarted, Claude Code
# renders `✔ Update installed · Restart to update` on the SAME standalone
# header line the `◎ /goal` glyph rides on, and the glyph directly ABUTS the
# word "update" with NO separator (byte-faithful hexdump of the raw capture:
# `…to update` = `…20 75 70 64 61 74 65`, then `e2 97 8e`=◎, ` /goal active
# (21m)`). The `^`-anchor plus the stash-only optional prefix therefore never
# matched, so `pane_goal_armed` read a genuinely-armed pane as False (dark) —
# dark-watch CONFIRMED-DEAD + re-armed the live loop and typed a truncated
# second /goal into the box (the #617 poisoned draft). Add the CC update
# notification as ANOTHER optional prefix alternative.
#
# #617-review — the FIRST cut anchored on a LAZY `.*?Restart to (?:update|
# apply)`, which re-opened a #393-class FALSE POSITIVE: a dark pane whose
# header carried a prose/quote row ending exactly at the glyph (`the banner
# reads: Restart to update◎ /goal active (21m)` — likely in a session working
# on THIS code) read armed=True and SUPPRESSED a legit re-arm. Anchored the
# alternative on the banner's own LINE-START shape instead — the CC glyph +
# `Update installed · Restart to (update|apply)` — so ordinary prose that
# merely quotes the phrase mid-line no longer matches. The tail stays the SAME
# CLOSED form (` active`/` active (<1-3 digits><h|m|d>)` then `$`), so every
# #393 wrapped-prose control is still rejected. ACCEPTED RESIDUALS (the #393
# MINOR-2 "fix what failed in production, widen on a real render" discipline):
# (1) a DIFFERENT CC banner in the same chrome slot — the theorised
# `✗ Auto-update failed · Try claude doctor …◎ /goal` — is UNOBSERVED, so it
# is NOT matched (its glyph would re-darken; add it if seen live); (2) a
# banner render whose wording differs from `Update installed · Restart to
# update|apply` likewise re-darkens.
_GOAL_HEADER_INDICATOR_RX = re.compile(
    r"^(?:" + re.escape(STASH_MARKER + " · ")
    + r"|(?:[✔✓]\s*)?Update installed\s*·\s*Restart to (?:update|apply)"
    + r")?"
    + re.escape(GOAL_INDICATOR)
    + r"( active(\s\(\d{1,3}[hmd]\))?)?$")
_GOAL_LCS_OPEN = "<local-command-stdout>"
_GOAL_LCS_CLOSE = "</local-command-stdout>"

GOAL_ARM_ACTIVE_PREFIX = ('A session-scoped Stop hook is now active with '
                          'condition: "')
_GOAL_ARM_PROBE = GOAL_ARM_ACTIVE_PREFIX[:-1].encode()   # quote-free (JSON)

# #675 -- CC's OWN goal-clear on a TRANSIENT failure (NEVER a user `/goal clear`):
# a plain `system` message whose TOP-LEVEL `content` starts with this and is NOT
# `<local-command-stdout>`-wrapped (so it matches neither of the two probes above).
# CC writes it as `Goal cleared after an unrecoverable error (<reason>): "…". Run
# /goal again to continue.` (camera-box 90bc51f3, 2026-08-18). The
# `(authentication failed)` reason is the owner-ruled NORMAL transient
# (subscription switching, #662/#676) -> `clear_kind="auth"`, auto-rearm eligible;
# any OTHER reason is recognized as `cleared` (flips armed->false) but tagged
# `clear_kind="error"` and NEVER auto-rearmed (needs human attention, not a
# re-type loop). The byte probe widens `_newest_marker`'s pre-filter.
_GOAL_CLEARED_ERROR_PREFIX = "Goal cleared after an unrecoverable error"
_GOAL_CLEARED_ERROR_PROBE = _GOAL_CLEARED_ERROR_PREFIX.encode()

# #675 -- a presence/human-prompt timestamp slightly in the FUTURE is legitimate
# mid-sweep drift (`now` is captured once at run_once's top; job 9/20 run at the
# TAIL, so a human prompt landing after that capture reads a small negative age)
# and must still count as recent-human. But a GROSSLY future value (a clock
# desync, a transcript synced off another box) is NOT a live human and must not
# extend the recency window to a false ~30-min veto. This is the small future
# tolerance `_goal_autoarm_recent_human_activity`'s clamp uses on BOTH signals in
# place of the full `GOAL_AUTOARM_RECENT_HUMAN_S` window (refines #339's symmetric
# bound into the small-future form #339 itself named as the goal). Comfortably
# covers a delayed/slow sweep (the 120s TimeoutStartSec + contention) while
# rejecting minutes-to-hours of skew.
GOAL_PRESENCE_FUTURE_SKEW_S = 300


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


# #662 -- the interactive-/login instruction Claude Code surfaces on ANY auth
# dead-end: the genuine token revoke ("Please run /login · … revoked"), a
# "Login expired · Please run /login", a "Not logged in · Please run /login".
# is_oauth_revoked (decide.py) matches ONLY the revoke text; this regex adds the
# bare /login-instruction family so the escalation valve covers the WHOLE
# "persistent non-self-healing AUTH block" class the ticket names, not just the
# revoke sub-class. It is used ONLY at ESCALATION (after max_nudges continues
# provably failed to self-heal), so a transient login-expired that resumes on an
# early continue never reaches it -- only a genuinely stuck one fires.
_LOGIN_NEEDED_RX = re.compile(r"run\s+/login", re.I)


def _needs_interactive_login(text):
    """#662 -- True for a PERSISTENT auth dead-end needing an interactive
    `/login`: the OAuth-revoke class (is_oauth_revoked) OR any error naming the
    `/login` instruction (login-expired / not-logged-in). Domain is always an
    `isApiErrorMessage` transcript entry, so quoted prose can't false-match."""
    return bool(text) and (is_oauth_revoked(text) or bool(_LOGIN_NEEDED_RX.search(text)))


def _apierr_escalation_ping(send_fn, project, pid, run, key, err_hash, fs,
                            err_text, owner, max_nudges, dry_run):
    """#175 give-up ping + #662 interactive-/login escape valve, extracted from
    job 1's escalation transition so run_once does not grow past its frozen
    ratchet ceiling. Fired ONCE per err_hash episode, the moment
    `entry['escalated']` first flips True (after `max_nudges` `continue`s).

    (1) The #175 give-up ping (`apierr-giveup:`) -- nudging itself never stops,
    only this ping is one-shot. Its key is #546-SUPPRESSED (apierr prefix), so
    for an ordinary api-error this posts nothing; that is the owner directive.

    (2) #662 -- but an error that needs an INTERACTIVE `/login`
    (`_needs_interactive_login`: the genuine `access token has been revoked` OR
    the bare "Please run /login" login-expired/not-logged-in family) and that
    survived every one of the `max_nudges` `continue`s is NOT self-healing (the
    #602 rotation resumes on nudge #1); it is a PERSISTENT non-self-healing AUTH
    block, exactly the acctblock class. So for that class ONLY, ALSO fire ONE
    `oauthblock:` alert naming the session (`loc`). #676 (owner ruling
    2026-08-24) then owner-suppressed `oauthblock:` (a 401 revoke is normal
    subscription-switching, not an incident), so this alert is now MACHINE-
    CHANNEL only: send() drops the Discord PING and keeps the journal +
    `suppressed` delivery-log line. Returns log lines."""
    logs = []
    body = ("\U0001f6d1 **%s** — API chyba pretrváva\n> Po %d× `continue` sa to "
            "stále nepohlo — treba zásah. (Skúšam ďalej, interval sa "
            "postupne predlžuje až na 30 min.)" % (project, max_nudges))
    send_fn(body, owner=owner,
            dedup_key="apierr-giveup:%s:%s:%s" % (key, err_hash, fs),
            dry_run=dry_run)
    if _needs_interactive_login(err_text):
        from notify import compose_oauth_block_alert
        loc = _pane_location(pid, run) or pid
        send_fn(compose_oauth_block_alert(project, loc, max_nudges), owner=owner,
                dedup_key="oauthblock:%s:%s:%s" % (key, err_hash, fs),
                dry_run=dry_run)
        logs.append("oauthblock machine-channel record %s [%s] — persistent "
                    "/login-needed auth block, self-heal failed after %d nudges "
                    "(owner-suppressed #676; journal + delivery-log, no PING)"
                    % (project, key, max_nudges))
    return logs


def _send_stuckcheck_verified(state, pid, text, run, tpath, now, sleep_fn, logs):
    """#497 batch 3 — the shared janitor-marked transcript-proof send for the
    CHUNK-typed decide_working-family nudges (jobs 4 working, 4a textcall). The
    sibling of batch-1's `_send_bare_nudge_verified`, minus the tpath
    re-resolution: jobs 4/4a already hold `tpath` as the owners[0] local, so it
    is passed IN rather than resolved from a cwd.

    Marks #372 janitor provenance BEFORE the keystroke so a swallowed chunk-typed
    residue (a wrapped/collapsed partial `send_verified`'s own undo cannot back
    off) is reclaimable by the janitor via the shared `"stuck-check: "`
    own-payload prefix, then calls `send_verified` and clears the mark on a
    transcript-VERIFIED submit, returning True. On an unverified/swallowed submit the mark is LEFT
    as the residue backstop and False is returned — the caller does NO persist
    reorder (decide_working's own interval/re-sighting cadence retries a
    swallowed nudge, and its after-max_nudges escalation is correct on a repeated
    swallow), it only logs the delivery result honestly."""
    _janitor_mark_watch(state, pid, now)
    if send_verified(pid, text, run, tpath, sleep_fn=sleep_fn, logs=logs):
        _janitor_clear_watch(state, pid)
        return True
    return False


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
    REPORT_GRACE_S as REPORT_GRACE_S,
    REPORT_MAX_SWALLOWS as REPORT_MAX_SWALLOWS,
    REPORT_REPROBE_S as REPORT_REPROBE_S,
    REPORT_MAX_LISTED as REPORT_MAX_LISTED,
    REPORT_TAIL_LINES as REPORT_TAIL_LINES,
    _iso_epoch as _iso_epoch,
    report_boundary_after as report_boundary_after,
    _self_callback_records as _self_callback_records,
    _autopilot_mutex_held as _autopilot_mutex_held,
    report_reconcile as report_reconcile,
    _owned_identity as _owned_identity,
    make_owned_closed_filter as make_owned_closed_filter,
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
    net_drift_alarm as net_drift_alarm,
    NET_DRIFT_THRESHOLD as NET_DRIFT_THRESHOLD,
    _repo_owner_from_panes as _repo_owner_from_panes,   # #717
    _alert_recipient as _alert_recipient,               # #717
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
    _janitor_park_record as _janitor_park_record,
    _janitor_park_seen as _janitor_park_seen,
    _janitor_park_typed as _janitor_park_typed,
    _janitor_clear_park as _janitor_clear_park,
    _janitor_prune_parks as _janitor_prune_parks,
    _janitor_recover as _janitor_recover,
    _pane_location as _pane_location,
)


# #504 — orphaned refs/autopilot-wip/* backup-ref reclaimer (the origin-ref
# counterpart of cli_worktree_sweep.py's LOCAL worktree sweep). A watchdog
# run_once job wired below via `_add`; re-exported here so `run_once`'s
# dispatch resolves `sweep_orphaned_wip_refs` by bare name. The leaf reaches
# `_sweep_due`/`_repo_sweep_batch` (re-exported above from repo_health) back
# through its own top-level `import watchdog` (call-time, no cycle).
from watchdog.wip_ref_sweep import (  # noqa: E402
    sweep_orphaned_wip_refs as sweep_orphaned_wip_refs,
    discover_orphaned_wip_refs as discover_orphaned_wip_refs,
    classify_wip_ref as classify_wip_ref,
    WIP_REF_PREFIX as WIP_REF_PREFIX,
    WIP_REF_MAX_AGE_S as WIP_REF_MAX_AGE_S,
    WIP_REF_SWEEP_INTERVAL_S as WIP_REF_SWEEP_INTERVAL_S,
)

# #834 — Job 40, the per-box DISK-PRESSURE GUARD (stdlib-only, no watchdog
# import at its top level → no cycle; reuses the per-class discovery functions
# under pressure).
from watchdog import disk_guard as disk_guard  # noqa: E402,F401
# #841 leg C — the owner-daily root-level finding surface Job 40 records at
# CRITICAL pressure (stdlib-only, no top-level watchdog/notify import → no
# cycle, never pings; a SESSION reads the finding cache and raises the ❓).
from watchdog import disk_guard_root as disk_guard_root  # noqa: E402,F401


# #535 — job 34, per-box cross-target conformance check. Extracted to
# `watchdog/conformance.py`; re-exported here so `run_once`'s job-34 dispatch and
# airuleset.py's `cmd_install` baseline recording resolve unchanged. The leaf
# reaches the resident `_sweep_due` cadence gate through its own top-level
# `import watchdog` (call-time attribute access, no cycle — it dereferences NO
# `watchdog` attribute at load time).
from watchdog.conformance import (  # noqa: E402
    run_conformance_check as run_conformance_check,
    record_conformance_baseline as record_conformance_baseline,
    default_baseline_path as default_baseline_path,
    classify_head as classify_head,
    classify_dirty as classify_dirty,
    classify_md5 as classify_md5,
    classify_timer as classify_timer,
    CONFORMANCE_BASELINE_NAME as CONFORMANCE_BASELINE_NAME,
)

# #543 — job 35, central dead-box heartbeat-missing detector (dev1-only). The
# per-box conformance check (job 34) cannot report a DEAD box; this reads the
# already-collected fleet.jsonl liveness and pings when a box goes silent past a
# threshold. Extracted to `watchdog/conformance_heartbeat.py`; re-exported here
# so `run_once`'s job-35 dispatch resolves unchanged. Same circular-import-safe
# idiom as conformance.py (its own `import watchdog`, call-time attribute access).
from watchdog.conformance_heartbeat import (  # noqa: E402
    run_conformance_heartbeat_check as run_conformance_heartbeat_check,
    classify_collection as classify_collection,
    classify_box as classify_box,
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
    _selfservice_bounce_decide as _selfservice_bounce_decide,
    _fetch_gk_action_requests as _fetch_gk_action_requests,
    _apply_selfservice_bounce as _apply_selfservice_bounce,
    gk_selfservice_bounce as gk_selfservice_bounce,
    _gk_marker_kinds as _gk_marker_kinds,
    _gk_orphan_decide as _gk_orphan_decide,
    _fetch_gk_orphan_candidates as _fetch_gk_orphan_candidates,
    _apply_gk_orphan_reconcile as _apply_gk_orphan_reconcile,
    gk_orphan_marker_sweep as gk_orphan_marker_sweep,
    GK_COMMENT_HANDOFF_WINDOW_S as GK_COMMENT_HANDOFF_WINDOW_S,
    _comment_handoff_window_s as _comment_handoff_window_s,
    _gk_proper_marker_in_window as _gk_proper_marker_in_window,
    _gk_comment_handoff_decide as _gk_comment_handoff_decide,
    _fetch_gk_comment_handoffs as _fetch_gk_comment_handoffs,
    _apply_gk_comment_handoff_reconcile as _apply_gk_comment_handoff_reconcile,
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
    _pane_shows_queued_messages_hint as _pane_shows_queued_messages_hint,
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


# #776 — Job 37, the runaway shadow-ugrep OS-process reaper (the FIRST
# OS-process reaper in the watchdog). Re-exported so run_once's job-37
# dispatch resolves the bare name, exactly like gk_orphan_marker_sweep.
from watchdog.reaper import (  # noqa: E402
    shadow_ugrep_reaper as shadow_ugrep_reaper,
    default_ps_fetch as default_ps_fetch,
    default_kill_fn as default_kill_fn,
    REAPER_MIN_AGE_S as REAPER_MIN_AGE_S,
    SHADOW_UGREP_SIGNATURE as SHADOW_UGREP_SIGNATURE,
    # #778 — Job 38, the heavy-build-toolchain reaper (shared-stream box only).
    heavy_build_reaper as heavy_build_reaper,
    default_box_class as default_box_class,
    is_shared_stream_box as is_shared_stream_box,
)

# #775 — Job 39, the VERIFY-ONLY shared-stream resource-guard check. Re-exported
# so run_once's job-39 dispatch resolves the bare name (and so the docstring's
# `resource_guard_verify` reference passes the `hasattr(wd, name)` live-job
# check), exactly like the reaper jobs above.
from watchdog.resource_guard import (  # noqa: E402
    resource_guard_verify as resource_guard_verify,
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
             vault_purge=None, vault_backstop=None, log_fn=None, reopen_fetch=None,
             time_fn=None, sweep_budget_s=None, backlog_fetch=None,
             ops_wait_fetch=None,
             progress_dir=None,
             gk_selfservice_fetch=None,
             u_reconcile_clear=None, conformance_root=None,
             conformance_is_target=None, conformance_hb_enabled=False,
             gkorphan_fetch=None, gkorphan_handoff_fetch=None,
             release_state_fetch=None, queue_fetch=None,
             reaper_ps_fetch=None, reaper_kill_fn=None,
             resource_guard_gk_request=None,
             u_fetch=None, reconcile_fetch=None, disk_guard_enabled=False):
    """Scan every `claude` pane once. 40 numbered jobs per poll — 34 LIVE and 6
    RETIRED (12, 18, 23 removed in #132; 15, 17 in #102; 26 in #402), whose
    numbers are kept addressable so historical log lines and code comments
    still resolve.
    The (4a) sub-entry belongs to job 4 and is not separately numbered:
      (1) a session STALLED ON AN API ERROR → auto-resume it (`continue`).
          NOTE (#546): every ORDINARY Discord "ping" jobs 1/1b/3/6 describe
          below is RETIRED — `notify.send()` suppresses the api-error/limit/usage
          alert classes (machine-channel `suppressed` log, never Discord). Only
          the SILENT `continue` auto-resume stays; it never routes through
          send(), so suppression leaves it untouched.
          Past `max_nudges` it does NOT give up — it keeps nudging forever at a
          widening interval (#175), with a one-shot #546-suppressed "gave up"
          ping. EXCEPTION (#662, see `_apierr_escalation_ping`): a persistent
          interactive-`/login` block that survived every `continue` ALSO fires
          an `oauthblock:` alert, and a dark pane the nudge can't reach is
          backstopped by the lane-sweep `stuckalert:` rider (job 20) — but BOTH
          of those classes are now MACHINE-CHANNEL only (owner-suppressed:
          oauthblock #676, stuckalert #688; journal + `suppressed` delivery-log
          line, no Discord PING). `acctblock:` is the one un-suppressed class.
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
          A THIRD extension, `prune_answered_questions` (#368/#449), collapses
          the map (terminal-answer prune + #407 ghost-pair collapse) —
          always-on, no `discord_fetch` needed. The daily RE-ASK that used to
          follow it (#368, `reping_stale_questions` reposting an unanswered
          ❓ FRESH AND WHOLE once a day) is RETIRED (#795): a question is
          asked ONCE, the footer `U N` badge holds it visible for as long as
          it stays open, and the owner invokes its processing himself via
          the "U N?" step-by-step flow (#606) — the watchdog never
          automatically re-asks a question again;

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
          full list: pane idle with no draft/dialog, NOT a running turn
          (#855: a `/compact` is typed ONLY into an idle pane — never
          queued behind a running turn, whose CC queue drain is not
          idempotent; a running turn → `skip:turn-running`, re-polled),
          not on a `⏳`/`❓` marker or an unresumed API error, a 120-s
          recently-compacted anti-double veto, a 30-min per-session
          cooldown, and a hard non-refreshable age cap) — ALL
          unconditional, no time-boxed override on any of them.
          #855-recurrence: a delivered `/compact` also CONSUMES every
          pending record for the SAME already-compacted boundary — a
          DUPLICATE `self-callback` record (the #411 Stop-hook backstop of
          the same `## ✅ Work Complete` the proactive `--self` already got
          compacted) is discarded `already-compacted`, never re-delivered,
          when a compaction is observed newer than the newest report OR the
          record's `bts` is not newer than the last delivered ts. All pass →
          `/compact` is typed into the idle prompt (executing immediately,
          exactly once), logged, and the request cleared. Any fails → left pending for the next
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
          NUDGE (#403, collapsing the old 968-line goal_rearm machinery into
          `watchdog/goal.py`, whose own module docstring is the single source
          of truth): TWO independent halves. `goal.goal_dark_watch` runs the
          shared janitor recovery for every live candidate pane first, then
          cross-checks the SAME two independent sources #76 established — the
          transcript marker (INTENT, `scan_goal_markers`) against CC's own `◎
          /goal` footer indicator (REALITY, `pane_goal_armed`) — but NEVER
          retypes a mismatch: it debounces the dark observation across >= 2
          sweeps, then sends ONE Discord ping asking the user to re-run
          `/autopilot`, which re-arms via the SAME proven callback job 9 already
          uses. The re-arm/ping path types nothing (the #617
          stranded-truncated-/goal CLEAR is the one keystroke it makes,
          Escape+BSpace via `_clear_stranded_truncated_goal`), so none of the
          old re-arm delivery-discipline machinery is needed any more — a false
          ping just costs the user one glance and a cheap re-run.
          `goal.goal_lane_sweep` is the ONE watchdog-INITIATED keystroke
          left in the whole family (#365/#351's own lane-occupancy nudge,
          functionally unchanged) and needs `compact_handled_this_sweep`
          for the same coordination reason job 9 above does. It also carries
          the per-armed-pane RIDERS (ZERO extra pane walk, same `handled`
          coordination, NOT separately numbered): the W/I partition-audit
          re-check (#547/#552/#578/#698, `ops_wait_recheck` — since #698 it
          ALSO consumes `release_state_fetch` on full/branch-merge boxes to
          escalate a release-gated W member's nudge wording once the repo's
          own release train is PROVEN drained) and, when
          `release_state_fetch` is wired, the release-gap nudge (#616,
          `release_gap.goal_release_gap_recheck`) — on a FULL-authority box
          only (the INVERSE of #618's lane-nudge authority gate) whose
          integration branch is ahead of prod, a `staging` branch exists, and
          NO release is in flight, it keystrokes the armed loop to run its
          release pipeline (the recurring "merged into develop but never
          released"). And, when `queue_fetch` is wired, the gk QUEUE-ARRIVAL
          watcher (#733, `queue_arrival_recheck.goal_queue_arrival_recheck`) —
          FULL-authority only, snapshots the union `ready-for-review ∪
          needs-gatekeeper ∪ prio:bounce` per repo and, on a SET DELTA (a NEW
          member vs the baseline), keystrokes the armed-but-WAITING loop to
          re-derive + process its gk backlog (the "parked on a waiter, blind to
          new hand-offs" incident). And, when `u_fetch` is wired, the
          U-FRESHNESS reconcile rider (#797,
          `u_freshness.goal_u_freshness_recheck`) — for an armed pane whose
          footer `user_waiting` count (read from the SAME tickets-status cache
          the footer renders, ZERO gh) is > 0, it keystrokes the session to
          re-audit each U member and drop a `needs-answer`/`needs-decision`
          label + question-map entry that is no longer a live owner question
          (so the footer `U`, the owner's ONLY question surface since #795,
          stays truthful) — NEVER an owner ping, and hard-floored at 1×/hour
          per session by the shared cadence gate. And, when `reconcile_fetch` is
          wired, the #844 POST-COMPACT LANE RECONCILE rider
          (`lane_reconcile.goal_lane_reconcile_recheck`) — keyed on the
          transcript's OBSERVED compaction (newest `isCompactSummary` epoch, ZERO
          gh unless a compaction is observed), FULL-authority only, it keystrokes
          the armed session to integrate returned worktree lanes from durable
          state (the branch + its LANE-RETURN comment) after the #844 forced
          compact may have lost a lane-completion notification — NEVER an owner
          ping, deduped one attempt per observed compaction. All six riders (lane
          / ops-wait / release-gap / queue-arrival / u-freshness / lane-reconcile)
          now consult that
          SHARED `nudge_gate` (`state["nudge_cadence"]`, #797): a per-category
          floor (only u-freshness's 1×/hour strop is non-zero) plus a
          cross-category family-spacing floor that DEFERS a second category's
          keystroke to a later sweep, killing the "nudges chodia jak besne po
          sebe" cross-sweep bursts without changing any rider's own cadence.
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
          (/tmp/airuleset-main-exec-ok-<sid>, legacy -fable- form too) plus,
          since #819, their deferred-consume pending flag
          (/tmp/airuleset-main-exec-pending-<sid>) are consumed when the
          exempted command actually RUNS (a PostToolUse consumer,
          post-consume-main-exec-marker.sh), but a session that ends without
          another guarded call never consumes its own marker/pending — it
          sits in /tmp forever (a real orphan found live: 0 bytes, ~21h
          old, no session anywhere still matching it). Hygiene, not a
          security hole (a marker is matched by session id, so a stale one
          is already inert) — a marker/pending older than
          `MAIN_EXEC_MARKER_MAX_AGE_S` (default 6h) is removed ONLY when no
          currently-live pane's transcript stem still resolves to its session
          id (`cleanup_stale_exec_markers`); a live session's marker/pending
          is never touched no matter its age, since removing it would silently
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
          When `vault_backstop` is ALSO wired (#529), the same hourly gate
          ALSO flags a `ready` credential whose registered durable
          ~/.secrets/<name> file never landed (delivery without persistence)
          — a PURE READ (never opens the value), so it warns without deleting.
      (30) (only when `repo_roots` is given) ORPHANED WIP-REF RECLAIMER
          (#504) — the origin-ref counterpart of `sweep_stale_worktrees`'s
          LOCAL worktree sweep. Reclaims leaked `refs/autopilot-wip/*`
          durability-backup refs (a dead-and-fresh-redispatched #503 worker
          leaves one on origin forever, the Step-4 delete never firing for its
          old branch). Own generous cadence (6h) + repo batching; deletes ONLY
          a ref proven MERGED into an origin base or aged past 7d, every delete
          lease-guarded — never a young/uncertain ref (salvage-before-
          discarding). See `watchdog/wip_ref_sweep.py`.
      (31) (only when `gk_selfservice_fetch` is given) GK SELF-SERVICE
          AUTO-BOUNCE (#516) — the gatekeeper-side mirror of side A's filing
          hook. On a SUPERVISOR box, for a cross-stream repo, an open
          `needs-gatekeeper` ACTION request that carries NO `Self-service-
          checked:` line and is attributable to a reduced stream
          (`handed-by:<stream>`) is AUTO-BOUNCED back to that stream (Slovak
          template comment + `prio:bounce` + `stream:<stream>` + drop
          needs-gatekeeper) so gk is never overloaded by a self-serviceable
          prod-STATE READ. MECHANICAL only (falsifiable no-line check, never a
          prose classifier); a code-review hand-off (`stream:`/ready-for-review)
          is never touched; every candidate's verdict is logged (#486). See
          `gk_selfservice_bounce` in `watchdog/cross_stream.py`.
      (32) (only when `u_reconcile_clear` is given) MECHANICAL U-LABEL
          LIFECYCLE (#515) — a `needs-answer`/`needs-decision` label whose
          question the owner ALREADY ANSWERED on Discord is cleared
          mechanically, instead of relying on the asking session's prose
          discipline to remove it (the phantom-`U` incidents miva1/dev1). Job 7
          `_delivered` captures the (session, cwd, #N) of each routed answer
          into `state`; this job clears the ticket's owner-question label once
          the asking session has demonstrably moved PAST the `❓` (tail != `❓`,
          no fresh re-ask in the map). Clears ONLY needs-answer/needs-decision
          (never needs-acceptance, the #526 W lane); acts only on questions
          THIS box asked+answered (local capture = ownership proof), so it runs
          on EVERY box; every candidate's verdict is logged (#486). See
          `reconcile_u_labels` in `watchdog/u_labels.py`.
      (33) (only when goal jobs are enabled) QUESTION-REPOKE DISARM (#522) —
          a `/goal` loop STUCK re-poking an unanswered `❓ NEEDS YOU` (the native
          evaluator ignoring stop-condition (A)) is DISARMED. Reads the
          AUTHORITATIVE transcript for GOAL_QUESTION_REPOKE_MIN (5) consecutive
          byte-identical re-pokes with NO genuine human answer between them, then
          — only on an ARMED pane, recent-human-gated and 24h/2 capped — types
          `/goal clear` (the symmetric inverse of the arm keystroke, via the same
          verified-delivery primitives). A landed disarm writes a
          `goal_disarmed_q` veto that job 20's `goal_dark_watch` honours (no
          disarm<->re-arm ping-pong) until a genuine human answer lands after it.
          See `goal_question_repoke_watch` in `watchdog/goal.py`.
      (34) (only when `conformance_root` is given) PER-BOX CONFORMANCE CHECK
          (#535) — a DAILY self-check that this box's airuleset config/repo has
          not drifted from the fleet, across four PURE deciders (True conformant /
          False drift / None undetermined): (1) HEAD vs origin/main after a
          bounded ≤1×/day fetch (drift only when strictly BEHIND — a missed
          deploy; ahead/diverged = local dev, no alarm), (2) `git status
          --porcelain` empty (deploy TARGETS only — the dev1 source is
          legitimately dirty), (3) md5 of `~/.claude/CLAUDE.md` vs the install-
          recorded `{md5, HEAD}` baseline (skipped when baseline HEAD != current
          HEAD — install pending after a repo move, so immune to a mid-push false
          alarm), (4) `api-watchdog.timer` active. ANYTHING uncertain (a git/fetch
          error, a missing baseline, a systemctl gap) is None → logged, NEVER a
          false alarm (#486). Drift → LOUD owner ping via `send_fn`, deduped
          per-dimension (bounded set of 4, no leak): a re-remind cadence (3d)
          surfaces an unchanged divergence without daily re-spam yet is never
          permanently silent (#134), an UNDETERMINED sweep never drops a prior
          episode (#486-G5), resolution clears the dedup. No ssh, no central
          fan-out (works even when dev1 sleeps); the dead-box gap is a filed
          central-heartbeat follow-up. See `run_conformance_check` in
          `watchdog/conformance.py`.

      (35) (only on dev1 — `conformance_hb_enabled`) CENTRAL DEAD-BOX HEARTBEAT
          DETECTOR (#543) — closes job 34's structural gap: a DEAD box's own
          self-check sends NOTHING (its watchdog stopped), so silence looks like
          health. dev1 (the always-on deploy source) reads the already-collected
          `fleet.jsonl` liveness (each box's hourly burn snapshot, job 16) — a box
          FRESH in a fleet row was alive that hour, a dead box is `{"error": ...}`
          — derives each deployable box's last-fresh instant, and LOUD-pings the
          owner when one goes silent past ~36h (env-tunable, generous — survives a
          reboot / brief job-16 hiccup). PURE deciders (True alive / False dead /
          None undetermined). FAIL-SAFE: if the COLLECTION itself is stale (dev1's
          job 16 degraded), the per-box check would false-alarm the WHOLE fleet —
          so a stale collection pings ONCE about the collector and SKIPS the
          per-box check. Deduped per-box + reping (3d) + fresh dedup_key (#535
          patterns); `pending` rename targets filtered via `_deployable_hosts`
          (#537); UNDETERMINED never drops an episode (#486-G5); dry_run mutates
          nothing. See `run_conformance_heartbeat_check` in
          `watchdog/conformance_heartbeat.py`.
      (36) (only when `gkorphan_fetch` is given) ORPHANED gk HAND-OFF MARKER
          BACKSTOP (#551) — the SUPERVISOR-side detection complement of the
          verified-delivery guidance in `cmd_gk_request`'s docstring (item 2 =
          docs + this backstop, NOT a hook — see the ticket's design-refinement).
          The miva1 incident: a stream HAND-WROTE a MUTATED `GATEKEEPER-ACTION
          (spresnenie …):` marker COMMENT (a parenthetical before the colon)
          instead of using `airuleset.py gk-request`; the repo auto-label
          workflow matches only line-start `GATEKEEPER-ACTION:` so no
          `needs-gatekeeper` label landed, and job 11's queries never scan
          COMMENTS — the hand-off was invisible to every layer and the stream
          parked for hours on a NEVER-DELIVERED request. On a supervisor box,
          for a cross-stream repo, ONE cheap `in:comments` search narrows the
          field; each candidate (ALL ≤60 fetched, once per 6h) is then verified
          from its own comments/labels against a FALSIFIABLE SIX-condition
          orphan gate biased hard to SILENCE (a MUTATED bracketed-annotation
          marker ∧ NO proper `GATEKEEPER-ACTION:` sibling ∧ NO downstream
          `ready-for-review`/`needs-acceptance` flow label ∧ not currently
          labeled ∧ `needs-gatekeeper` NEVER in the PAGINATED label timeline ∧
          no GA-title; fenced code pastes stripped). The token is pervasive in
          this repo's comment history (every gk-request leaves a marker
          forever), so a naive "token + no label = orphan" rule would
          false-accuse ~44 processed tickets (measured live) — hence the narrow
          gate. A genuine orphan is RECONCILED supervisor-side (evidence comment
          + add `needs-gatekeeper` → job 11 nudges the supervisor); a label-add
          failure keeps the comment (never re-posted) and surfaces via ONE
          deduped ping. Every candidate's verdict is logged (#486); a gh error
          reports nothing (never a false accusation); the `seen` dedup is
          dry-run-safe (#516 F1). Generous 6h cadence. See
          `gk_orphan_marker_sweep` in `watchdog/cross_stream.py`.
      (37) (only when `reaper_ps_fetch` is given) RUNAWAY SHADOW-UGREP
          OS-PROCESS REAPER (#776) — the FIRST OS-process reaper in the
          watchdog. Claude Code shadows every Bash `grep` into `ugrep -G
          --ignore-files ...` (shell-snapshots); the bundled ugrep 7.5.0
          busy-loops at 100% CPU forever on a whole-filesystem recursive scan
          and orphans when its tool call times out (a 15-day 295%-CPU orphan on
          subdev, #774; upstream anthropics/claude-code#81916, still OPEN). Runs
          on EVERY box every cycle (NO cadence gate — a runaway must be reaped
          within one cycle after 30 min). Each cycle it reads the process table,
          finds processes whose cmdline NARROWLY carries the exact
          `ugrep -G --ignore-files` signature AND have run longer than 30 min,
          SIGKILLs them, and logs kill-reason + cmdline + age. FAIL-SAFE:
          exact signature only, NEVER a young process (the age gate is the whole
          discriminator — no legitimate grep runs 30 min), any ps/parse error
          kills NOTHING, a malformed row is skipped, dry_run kills nothing.
          Log-only self-heal (like the api-error `continue`), never a Discord
          ping (#546). `hooks/block-root-recursive-grep.sh` (Layer 1) stops NEW
          ones from spawning; this reaper (Layer 2) cleans up the already-
          orphaned; #775 (resource caps) is Layer 3. See `shadow_ugrep_reaper`
          in `watchdog/reaper.py`.
      (38) (only when `reaper_ps_fetch` is given) HEAVY-BUILD-TOOLCHAIN
          OS-PROCESS REAPER (#778), SHARED-STREAM BOX ONLY — a SIBLING of Job
          37 with the OPPOSITE gating: kill-on-sight, NO age/CPU gate, because a
          JVM/Android build daemon is BANNED OUTRIGHT on a shared-stream box
          (subdev). Root cause: the subdev VPS runs N isolated Claude stream
          users and exists ONLY for Claude sessions + git + light scripts; two
          streams self-installed a JDK/Android toolchain and ran Gradle/Kotlin
          daemons (`-Xmx3072m` × 2 = 13.3 GB RAM), collapsing the box (#774).
          The owner's standing rule: Android/JVM/RN builds run on dev2, never on
          a shared-stream box. Each cycle, ONLY on a box whose class marker
          (`~/.claude/airuleset-box-class`) reads `shared-stream`, it SIGKILLs
          processes whose argv[0] is a Gradle/Kotlin daemon, `aapt2`, or a
          `qemu-system*` VM/emulator, and logs the kill. FAIL-SAFE: off a
          shared-stream box (or on any box-class read error) it kills NOTHING;
          argv[0]-anchored signatures only (a process merely quoting one never
          matches); NODE is never matched (it runs Claude Code/MCP/webterm); a
          pre-kill TOCTOU re-verify; any ps/parse/kill error kills NOTHING;
          dry_run kills nothing. Log-only self-heal, never a Discord ping
          (#546). `hooks/block-heavy-build-toolchain.sh` (Layer 1) stops a NEW
          launch on a shared-stream box; this reaper (Layer 2) cleans up
          anything already running. See `heavy_build_reaper` in
          `watchdog/reaper.py`.
      (39) (only when `resource_guard_gk_request` is given) SHARED-STREAM
          RESOURCE-GUARD VERIFY (#775), SHARED-STREAM BOX ONLY — VERIFY-ONLY,
          no killer logic (#486). Layer 3 of the subdev-OOM fix: #776 (ugrep)
          and #778 (heavy-build) reap two known runaway signatures; this checks
          that the mechanical per-user cgroup CEILING (`cli_resource_guards`,
          applied by `airuleset.py push`) is actually in place, and shouts when
          it is not. Only on a `shared-stream` box, it reads this account's OWN
          cgroup `memory.max` (`/sys/fs/cgroup/user.slice/user-<uid>.slice/`,
          world-readable — no root); a definitively UNLIMITED (`max`) ceiling
          means the box runs without guardrails → one LOUD journal line + one
          deduped gk-request per ~day (marker-file throttle). FAIL-SAFE toward
          SILENCE (#539): off a shared-stream box, an unreadable/missing cgroup,
          or a finite ceiling → NOTHING; `dry_run` files nothing. Machine
          channel only, never a Discord ping (#546). See `resource_guard_verify`
          in `watchdog/resource_guard.py`.
      (40) PER-BOX DISK-PRESSURE GUARD (#834, hardened #854), gated on
          `disk_guard_enabled` (cmd_watchdog passes True → runs every real
          poll). Reads `statvfs` every poll, writes the `disk NN%` footer cache
          (footer shown only >= 90 %, #854), and at >= 80 % runs an auto-drain
          ladder — CADENCE-GATED (once/10 min) in the 80-95 % band, but at
          CRITICAL pressure (>= 95 %, #854) it drains EVERY poll (severity beats
          cadence; a guard that only logs at 97 % is the bug #854 fixes). The
          cache-class ladder (each rung a pure dry-run-able selector logging
          `disk-guard: NN% → drain rung=<name> freed=<b> → MM%`): apt cache,
          rotated `/var/log/*.1|*.gz`, gh-runner `_work/_update|_temp` + stale
          `_work/<repo>` checkouts, docker images (0-containers AND untagged OR
          > 14 d — Runner.Worker-gated, never a tagged in-use image, never
          `docker system prune`), stale Claude self-update binaries (except the
          running one), one-off numbered venvs > 2 d, per-user `~/.cache` > 30 d,
          own-home scratch/uploads/CLI-versions/worktrees/toolchain, transcripts
          > 7 d gzip (LAST). Fail-LOUD (every action + skip logged to
          `disk-guard.log`), never deleting on uncertainty, never crossing a
          filesystem, never as root (the root/system leg is #841). >= 90 % after
          the drain → machine-channel escalation, box-wide deduped once/day.
          `watchdog/disk_guard.py`'s docstring is the SSOT.
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
                    swallow_fails = s.get("swallow_fails", 0)
                    # #497 batch-2 review — a PERSISTENTLY swallowed `continue`
                    # (send_verified→False every window, input genuinely wedged)
                    # never bumps `attempts`, so it must reach the give-up via its
                    # OWN consecutive-swallow streak; otherwise the escalation is
                    # STRUCTURALLY UNREACHABLE for the case the user most needs it
                    # (the #442-F2 class — batch 1 of this ticket fixed the identical
                    # shape for bounce/gkreq).
                    if attempts >= SESSLIMIT_MAX_TRIES or swallow_fails >= SESSLIMIT_MAX_TRIES:
                        # Bounded — never retry forever. One give-up ping, then silence.
                        if not s.get("gave_up"):
                            s["gave_up"] = True
                            logs.append("session-limit %s — gave up (attempts=%d, "
                                        "swallow-fails=%d)"
                                        % (project, attempts, swallow_fails))
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
                            # #497 — transcript-proof send: a swallowed `continue`
                            # (agent-strip selector #36, or a turn started under the
                            # send) must NOT be booked as "resumed". Record
                            # continued/attempts/the resume ping ONLY on a verified
                            # submit; on an unverified one set none of them so the
                            # session stays limit-parked and the next sweep retries
                            # from a clean prompt.
                            ok = True
                            if not dry_run:
                                ok = send_verified(pid, NUDGE_TEXT, run, tpath,
                                                   sleep_fn=sleep_fn, logs=logs)
                            if ok:
                                attempts += 1
                                s["attempts"] = attempts
                                s["last_try"] = now
                                s["continued"] = True
                                s["swallow_fails"] = 0        # verified → the swallow episode ended
                                logs.append("session-limit %s — reset passed → continue" % project)
                                if attempts == 1:
                                    send_fn("✅ **%s** — 5h limit sa resetol, poslal som "
                                            "`continue` — pokračujem." % project,
                                            owner=owner,
                                            dedup_key="sesslimit-resume:%s:%s" % (key, ra),
                                            dry_run=dry_run)
                            else:
                                # a swallowed `continue` is NOT a resume: never book
                                # continued/attempts. Bump the consecutive-swallow
                                # streak (feeds the give-up above) and stamp last_try
                                # so the SESSLIMIT_RETRY_S throttle applies next window
                                # (no 60s re-type spam on a wedged pane).
                                s["swallow_fails"] = swallow_fails + 1
                                s["last_try"] = now
                                logs.append("session-limit %s — continue submit-unverified "
                                            "(%d/%d), retry next window"
                                            % (project, s["swallow_fails"], SESSLIMIT_MAX_TRIES))
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
                    # #602: the OAuth-rotation 401 REVOKED class gets an ENRICHED
                    # resume prompt naming the re-dispatch-from-durable-state duty
                    # (its kill-window kills in-flight agents a bare `continue`
                    # would orphan); every other api-error stays bare `continue`.
                    # Selection only — delivery/lifecycle/dedup all unchanged.
                    resume_text = (OAUTH_REVOKED_NUDGE_TEXT
                                   if is_oauth_revoked(err_text) else NUDGE_TEXT)
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
                            pid, resume_text, run, captured=fresh, logs=logs,
                            state=state)  # #852-review 🟡-5
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
                        # #497 — transcript-proof send. NO persist reorder here:
                        # decide()'s own interval/re-sighting cadence retries a
                        # swallowed nudge, and its after-max_nudges escalation is
                        # CORRECT on repeated swallows (delivery genuinely failing
                        # → the give-up ping below). So just swap the primitive and
                        # log the delivery result honestly (napísané ≠ odoslané).
                        ok = True
                        if not dry_run:
                            ok = send_verified(pid, resume_text, run, tpath,
                                               sleep_fn=sleep_fn, logs=logs)
                        logs.append("nudge#%d %s [%s]%s"
                                    % (n, project, key,
                                       "" if ok else " (submit-unverified)"))
                    if n == 1:                 # first nudge → tell the user it stalled
                        send_fn(compose_api_error_alert(project, err_text),
                                owner=owner, dedup_key="apierr:%s:%s:%s" % (key, err_hash, fs), dry_run=dry_run)
                    # (#175) one-shot "gave up" ping the moment the (max_nudges+1)-th
                    # nudge is due — nudging itself NEVER stops; only this ping is
                    # one-shot per err_hash.
                    if entry.get("escalated") and not prev_escalated:
                        logs.append("escalate %s [%s] — still stuck after %d nudges, "
                                     "backing off (keeps retrying)" % (project, key, max_nudges))
                        # #175 give-up ping + the #662 oauthblock valve for a
                        # persistent /login-needed block — BOTH machine-channel
                        # only now (#546/#676 owner-suppressed; no Discord PING).
                        logs += _apierr_escalation_ping(
                            send_fn, project, pid, run, key, err_hash, fs,
                            err_text, owner, max_nudges, dry_run)
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
                                              dry_run, tpath=tpath, sleep_fn=sleep_fn)
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
                    # #497 batch 3 — transcript-proof send + #372 janitor mark
                    # (271c CHUNK); see _send_stuckcheck_verified.
                    ok = True
                    if not dry_run:
                        ok = _send_stuckcheck_verified(
                            state, pid, TEXTCALL_NUDGE_TEXT, run, tpath, now,
                            sleep_fn, logs)
                    logs.append("textcall-nudge#%d %s [%s] idle=%dm%s"
                                % (n, project, key, int(idle // 60),
                                   "" if ok else " (submit-unverified)"))
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
                                          working_interval, max_working_nudges, dry_run,
                                          tpath=tpath, sleep_fn=sleep_fn)
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
                    # #497 batch 3 — transcript-proof send + #372 janitor mark
                    # (WORKING_NUDGE_TEXT 431c CHUNK); see _send_stuckcheck_verified.
                    ok = True
                    if not dry_run:
                        ok = _send_stuckcheck_verified(
                            state, pid, WORKING_NUDGE_TEXT, run, tpath, now,
                            sleep_fn, logs)
                    logs.append("working-nudge#%d %s [%s] idle=%dm%s"
                                % (n, project, key, int(idle // 60),
                                   "" if ok else " (submit-unverified)"))
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

    # --- STANDALONE JOB REGISTRY (#433 step 16) ------------------------------
    # The post-loop standalone sequence (jobs 3/5/7 + the #368 extension,
    # then jobs 8→29) as an ORDER-PRESERVING (label, gate, invoke, err) registry
    # run by the single loop before save_state. Gates/args are verbatim inside
    # closures capturing locals naturally, so every `watchdog.<name>` seam is
    # unchanged. Representation change only — characterization test pins it.
    # `err`: "<prefix> error" text logged on a raise, or None to swallow it.
    _standalone_registry = []

    def _add(label, gate, invoke, err):
        _standalone_registry.append((label, gate, invoke, err))

    # --- (3) WEEKLY TOKEN-USAGE alert (only when a fetcher is wired) — rate-limited
    # to USAGE_INTERVAL inside check_usage so the 60s tmux cadence doesn't hammer
    # the aggressively-429'd endpoint. Best-effort: never breaks the tmux jobs.
    def _job_check_usage():
        line = check_usage(now, state, send_fn, fetch=usage_fetch,
                           owner=account_owner or None, dry_run=dry_run)
        return [line] if line else []
    _add("check_usage", lambda: usage_fetch is not None, _job_check_usage, None)

    # --- (5) DELIVER PENDING ✅ — backstop for the unreliable idle_prompt event.
    # Best-effort: a bad pending file must never break the tmux jobs.
    _add("deliver_pending_done", lambda: True,
         lambda: deliver_pending_done(now, send_fn, projects_dir,
                                      owner_by_sid=owner_by_sid, account_owner=account_owner,
                                      dry_run=dry_run, done_grace=done_grace,
                                      pending_prefix=pending_prefix,
                                      owner_by_cwd=owner_by_cwd, owners_seen=owners_seen),
         None)

    # --- (7) ROUTE DISCORD REPLIES → the asking session, a ❓/❔ REACTION on a
    # tracked bot message (#297), and a REPLY on a completion card (#298) —
    # only when a fetcher is wired. Best-effort: a Discord/network hiccup must
    # never break the tmux jobs. `cwd_by_sid` (#297/#298): resolves "the
    # nearest live session of repo X" for the flag-fallback / post-reopen
    # nudge — already built above for job 24's own repo read.
    _add("deliver_discord_replies", lambda: discord_fetch is not None,
         lambda: deliver_discord_replies(now, run, state, panes_by_sid,
                                         dry_run=dry_run, discord_fetch=discord_fetch,
                                         hosted_users=hosted_users,
                                         cwd_by_sid=cwd_by_sid,
                                         projects_dir=projects_dir,
                                         persist=lambda: save_state(state_path, state),
                                         sleep_fn=sleep_fn),
         "discord-reply error")

    # Terminal-answered ❓ cleanup — a question answered by a HUMAN prompt in
    # the asking session leaves the map NOW, not on some later timer (it
    # feeds the statusline 'otazky' badge, which must be trustworthy per
    # stream).
    _add("prune_answered_questions", lambda: True,
         lambda: prune_answered_questions(now, projects_dir=projects_dir,
                                          dry_run=dry_run),
         "question-prune error")

    # #368 (the daily question re-ask) is RETIRED (#795, owner ruling
    # 2026-09-01): its registry entry and the `questions_path` param that
    # only ever fed IT are GONE — a question is asked ONCE, the footer `U N`
    # badge holds it, and the owner invokes its processing himself (#606).
    # `reping_stale_questions` survives only as a no-op tombstone
    # (questions.py); `prune_answered_questions` above is UNCHANGED — it
    # collapses the map for its own #368/#449/#407 reasons, unrelated to
    # re-asking.

    # #461 (the daily owner-decision digest) is RETIRED (#707): its registry
    # entry, `owner_decision_fetch` param and fetch/digest helpers are GONE —
    # the box-wide digest leaked cross-subject via the `account_owner` coin
    # flip on multi-owner dev2, and the owner abolished the message class.
    # `reping_owner_decision_tickets` survives only as a no-op tombstone
    # (questions.py); notify's `owner-decision-digest` dedup_key denylist
    # backstops stale code.

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
    _add("bounce_backstop", lambda: bounce_fetch is not None,
         lambda: bounce_backstop(
             now, run, state, send_fn, dry_run=dry_run,
             gh_fetch=bounce_fetch, projects_dir=projects_dir,
             persist=lambda: save_state(state_path, state),
             time_fn=time_fn, sweep_deadline=tail_deadline, sleep_fn=sleep_fn),
         "bounce-backstop error")

    # Job 11 — gk-request backstop (#30): the stream→supervisor mirror of
    # job 8. Same gating: only when a fetch is wired; cadence-gated
    # internally; best-effort.
    _add("gk_request_backstop", lambda: gkreq_fetch is not None,
         lambda: gk_request_backstop(
             now, run, state, send_fn, dry_run=dry_run,
             gh_fetch=gkreq_fetch, projects_dir=projects_dir,
             persist=lambda: save_state(state_path, state), sleep_fn=sleep_fn),
         "gkreq-backstop error")

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

    # The DISABLED lines emit from an ORDERED registry entry (the one non-job
    # entry) at the if-chain's exact position, so `logs` order is byte-identical;
    # the flags stay computed HERE because the later compact/goal gates read them.
    def _kill_switch_notice():
        _n = []
        if _goal_jobs_disabled:
            _n.append("goal jobs DISABLED by owner flag "
                      "~/.claude/watchdog-disable-goal (rm to re-enable)")
        if _compact_jobs_disabled:
            _n.append("compact jobs DISABLED by owner flag "
                      "~/.claude/watchdog-disable-compact (rm to re-enable)")
        return _n
    _add("_owner_kill_switch_notice", lambda: True, _kill_switch_notice, None)

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
    _add("burn_snapshot_job", lambda: burn_snapshot_path,
         lambda: burn_snapshot_job(now, state, snapshot_path=burn_snapshot_path,
                                   transcripts_root=str(projects_dir),
                                   dry_run=dry_run),
         "burn-snapshot error")

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
    def _job_compact_sweep():
        from watchdog import compact as _compact
        return _compact.compact_sweep(now, run=run, dry_run=dry_run,
                                      projects_dir=projects_dir,
                                      requests_path=compact_requests_path,
                                      state=state,
                                      handled=compact_handled_this_sweep)
    _add("compact_sweep", lambda: compact_requests_path and not _compact_jobs_disabled,
         _job_compact_sweep, "compact-request error")

    # Job 15 — REMOVED (#102, 2026-07-27). Used to fire /compact off context
    # size + idle duration alone; see run_once's own docstring paragraph
    # (15) and the removed section comment above `_pane_compacting`.

    # Job 16 — HOURLY FLEET BURN (#55): only when `fleet_fetch` is given
    # (cmd_watchdog wires this ONLY on the coordinator, dev1 — every other
    # managed box already writes its own local hourly row via job 13, so
    # this job just merges them). Same "wired = on" convention as jobs
    # 3/7/8/11/13/14. Best-effort; internally also cadence-gated to at most
    # once per hour.
    _add("fleet_burn_job", lambda: fleet_fetch is not None,
         lambda: fleet_burn_job(now, state, fleet_hosts or [], send_fn,
                                fetch=fleet_fetch, fleet_path=fleet_path,
                                owner=account_owner or None, dry_run=dry_run),
         "fleet-burn error")

    # Job 19 — HOURLY BURN ALERT (#81): only when `burn_alert_enabled` is
    # truthy (cmd_watchdog computes it the SAME dev1-only way it computes
    # `fleet_fetch` for job 16 — every other managed box never writes
    # fleet.jsonl at all, so this job would just see an empty file there).
    # Runs right after job 16 so it evaluates the row job 16 may have just
    # written THIS sweep. Best-effort; internally cadence-gated to at most
    # once per hour bucket.
    _add("burn_alert_job", lambda: burn_alert_enabled,
         lambda: burn_alert_job(now, state, send_fn, fleet_path=fleet_path,
                                owner=account_owner or None, dry_run=dry_run),
         "burn-alert error")

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
    def _job_goal_sweep():
        from watchdog import goal as _goal_mod
        return _goal_mod.goal_sweep(
            now, run=run, dry_run=dry_run, projects_dir=projects_dir,
            requests_path=goal_requests_path, state=state,
            handled=compact_handled_this_sweep, send_fn=send_fn,
            sleep_fn=sleep_fn)
    _add("goal_sweep", lambda: goal_jobs_enabled and not _goal_jobs_disabled,
         _job_goal_sweep, "goal-sweep error")

    # Job 20 — GOAL DARK-WATCH + LANE-OCCUPANCY NUDGE (#403, collapsing the
    # old `goal_rearm`'s 968-line re-arm/drift/outage/forensics machinery):
    # only when `goal_jobs_enabled` is truthy — same "wired = on"
    # convention as jobs 13/14/16/19. Two independent halves, both from
    # `watchdog/goal.py` (its own module docstring is the single source of
    # truth):
    #   * `goal_dark_watch` runs the shared janitor recovery for every live
    #     candidate pane FIRST (the one sweep still guaranteed to visit
    #     every pane every ~60s regardless of pending requests, matching
    #     job 20's old visit cadence), then, for a session whose transcript
    #     says armed but whose footer has gone dark, either RE-ARMS a
    #     CONFIRMED-dead loop (#478/#524 -- records a request for job 9 to
    #     type, gated on a hardened death-confirmation run) or pings the user;
    #     for the RE-ARM path `goal_dark_watch` itself never types (that
    #     keystroke is job 9's) -- its ONLY keystroke is the #617 stranded-
    #     truncated-/goal CLEAR (`_clear_stranded_truncated_goal`, Escape+
    #     BSpace, gated on a clean boundary + fail-closed recent-human + a
    #     byte-exact-prefix content proof + a bounded give-up).
    #   * `goal_lane_sweep` is the OTHER watchdog-INITIATED keystroke in
    #     the family (#365/#351's own lane-occupancy nudge,
    #     functionally unchanged) and needs `compact_handled_this_sweep`
    #     for the identical reason job 9 above does.
    def _job_goal_dark_watch():
        from watchdog import goal as _goal_mod
        return _goal_mod.goal_dark_watch(
            now, run=run, state=state, send_fn=send_fn,
            dry_run=dry_run, projects_dir=projects_dir,
            sleep_fn=sleep_fn, time_fn=time_fn,
            sweep_deadline=tail_deadline,
            requests_path=goal_requests_path)
    _add("goal_dark_watch", lambda: goal_jobs_enabled and not _goal_jobs_disabled,
         _job_goal_dark_watch, "goal-dark-watch error")

    # #522 -- disarm a `/goal` loop STUCK re-poking an unanswered `❓ NEEDS YOU`
    # (the native evaluator ignoring stop-condition (A)). Reads the AUTHORITATIVE
    # transcript for N consecutive byte-identical re-pokes and types `/goal clear`
    # (recent-human-gated + 24h/2 capped, the LANE-nudge keystroke discipline),
    # writing a `goal_disarmed_q` veto that `goal_dark_watch` (registered ABOVE,
    # so the veto it reads is already this sweep's) honours to prevent a
    # disarm<->re-arm ping-pong. Shares `state` + `tail_deadline` budget with the
    # other goal jobs; never types when goal jobs are disabled.
    def _job_goal_question_repoke_watch():
        from watchdog import goal as _goal_mod
        return _goal_mod.goal_question_repoke_watch(
            now, run=run, state=state, send_fn=send_fn,
            dry_run=dry_run, projects_dir=projects_dir,
            sleep_fn=sleep_fn, time_fn=time_fn,
            sweep_deadline=tail_deadline)
    _add("goal_question_repoke_watch",
         lambda: goal_jobs_enabled and not _goal_jobs_disabled,
         _job_goal_question_repoke_watch, "goal-question-repoke-watch error")

    def _job_goal_lane_sweep():
        from watchdog import goal as _goal_mod
        return _goal_mod.goal_lane_sweep(
            now, run=run, dry_run=dry_run, projects_dir=projects_dir,
            state=state, handled=compact_handled_this_sweep,
            backlog_fetch=backlog_fetch, send_fn=send_fn,
            sleep_fn=sleep_fn, time_fn=time_fn,
            sweep_deadline=tail_deadline, ops_wait_fetch=ops_wait_fetch,
            release_state_fetch=release_state_fetch,     # #616
            queue_fetch=queue_fetch,                     # #733
            u_fetch=u_fetch,                             # #797
            reconcile_fetch=reconcile_fetch)             # #844
    _add("goal_lane_sweep", lambda: goal_jobs_enabled and not _goal_jobs_disabled,
         _job_goal_lane_sweep, "goal-lane-sweep error")

    # Job 21 — LONG-TURN WATCH (#84): only when `long_turn_enabled` is truthy
    # (cmd_watchdog passes True) — same "wired = on" convention as jobs
    # 13/14/16/18/19/20. Detection only, so it is safe to run LAST: it takes
    # no tmux round-trip beyond `_pane_location` for a pane already past the
    # threshold, and it never types anything, so it cannot interact with any
    # sender above it. Best-effort.
    _add("long_turn_watch", lambda: long_turn_enabled,
         lambda: long_turn_watch(now, run, state, panes_by_sid,
                                 send_fn=send_fn, dry_run=dry_run,
                                 project_by_sid=project_by_sid,
                                 owner_by_sid=owner_by_sid),
         "long-turn error")

    # Job 24 — DELIVERY-STALL WATCH (#138): only when `delivery_probe` is
    # given (cmd_watchdog wires the real fetch + gh lookup) — same
    # "wired = on" convention as jobs 8/11/16, and here it is also the
    # correctness gate: the probe carries the confirming fetch, and a verdict
    # that was never confirmed must not reach the user's phone. Detection
    # only, so it is safe alongside job 21 at the end; it takes no tmux
    # round-trip at all (the cwd map was built during the pane sweep above)
    # and never types anything. Best-effort.
    _add("delivery_stall_watch", lambda: delivery_probe is not None,
         lambda: delivery_stall_watch(now, run, state, cwd_by_sid,
                                      send_fn=send_fn, dry_run=dry_run,
                                      delivery_probe=delivery_probe,
                                      owner_by_sid=owner_by_sid,
                                      owner_by_cwd=owner_by_cwd,          # #717
                                      owners_seen=owners_seen,            # #717
                                      account_owner=account_owner,        # #717
                                      project_by_sid=project_by_sid, authority=_box_authority()),  # #667
         "delivery-stall error")

    # Job 25 — CARD RECONCILIATION (#134): the mirror of job 24, same
    # "wired = on" convention and the same confirm-then-announce contract.
    # Detection only, no tmux round-trip (the cwd map came from the pane
    # sweep above). Best-effort — a failure here must never cost a sweep.
    # Job 25 also runs REPORT reconciliation (#525) — the SAME registry entry
    # (no new job), a second check over the same closed set for a saturated
    # supervisor that merged tickets but never wrote their per-ticket report.
    # It early-returns on empty `cwd_by_sid` (a no-op in the characterization
    # sweep) and types in-band, so it takes panes_by_sid/projects_dir/sleep_fn.
    # #534: ONE owner-scoping filter, shared by both jobs so its per-repo
    # GraphQL ownership lookup fires at most once per repo per sweep (both jobs
    # derive the identical `merged_closes` candidate set for a given root). Built
    # HERE, per sweep, so its memo is fresh each sweep (a gh-failure sweep
    # re-queries next sweep rather than latching a wrong owner map).
    _owned_scope = make_owned_closed_filter()
    _add("card_reconcile", lambda: card_probe is not None,
         lambda: card_reconcile(now, run, state, cwd_by_sid,
                                send_fn=send_fn, dry_run=dry_run,
                                card_probe=card_probe,
                                closed_fetch=closed_fetch,
                                reopen_fetch=reopen_fetch,
                                owner_by_sid=owner_by_sid,
                                owned_closed=_owned_scope)
                 + report_reconcile(now, run, state, cwd_by_sid, panes_by_sid,
                                    send_fn=send_fn, dry_run=dry_run,
                                    owner_by_sid=owner_by_sid,
                                    projects_dir=projects_dir, sleep_fn=sleep_fn,
                                    owned_closed=_owned_scope),
         "card-reconcile error")

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
    _add("net_drift_alarm", lambda: issue_counts_fetch is not None,
         lambda: net_drift_alarm(now, state, send_fn=send_fn,
                                 dry_run=dry_run, repo_roots=repo_roots,
                                 issue_counts_fetch=issue_counts_fetch,
                                 owner_by_cwd=owner_by_cwd,          # #717
                                 owners_seen=owners_seen,            # #717
                                 account_owner=account_owner,        # #717
                                 persist=lambda: save_state(state_path, state)),
         "net-drift error")

    # Job 28 — STUCK-MAIN SWEEP (#137): only when `repo_roots` is given —
    # the "wired = on" convention. Self-gated on an hourly cadence
    # internally; best-effort. Independent of job 27's own gate (a repo
    # sweep with no gh access can still measure this locally). `persist=`
    # (#172, extended #172 REOPENED F3/F4): same reasoning as job 27 — one
    # `git fetch` timeout at a time, dedup memory persisted before the ping,
    # cadence marker persisted before `repo_roots()` runs. #172 REOPENED F5:
    # a fetch failure now SKIPS the repo this sweep rather than measuring on
    # refs that may be stale.
    _add("stuck_main_sweep", lambda: repo_roots is not None,
         lambda: stuck_main_sweep(now, state, send_fn=send_fn,
                                  dry_run=dry_run, repo_roots=repo_roots,
                                  git_fetch=git_fetch,
                                  owner_by_cwd=owner_by_cwd,          # #717
                                  owners_seen=owners_seen,            # #717
                                  account_owner=account_owner,        # #717
                                  persist=lambda: save_state(state_path, state)),
         "stuck-main error")

    # Job 22 — STALE EXEC-MARKER CLEANUP (#97): ALWAYS wired (no gating
    # param — same "always on" shape as jobs 9/15/17, since it depends on
    # nothing external beyond /tmp + the pane list already available this
    # sweep). Pure hygiene, never security-critical (a marker is matched by
    # session id, so a stale one is already inert) — best-effort.
    _add("cleanup_stale_exec_markers", lambda: True,
         lambda: cleanup_stale_exec_markers(now, run=run,
                                            projects_dir=projects_dir,
                                            dry_run=dry_run),
         "exec-marker-cleanup error")

    # Job 29 — HOURLY CREDENTIAL-STORE SWEEP (#144) + DURABLE-PERSISTENCE
    # BACKSTOP (#529): only when `vault_purge` is given (cmd_watchdog passes
    # filedrop.vault.purge). Best-effort and internally hour-gated; deletes
    # only what is already past its own TTL, and — when `vault_backstop` is
    # also wired — flags any `ready` credential that registered a durable
    # ~/.secrets/<name> target whose file never landed (delivery without
    # persistence — a PURE READ, so dry-run safe and never a one-shot latch).
    _add("vault_purge_job", lambda: vault_purge,
         lambda: vault_purge_job(now, state, purge_fn=vault_purge,
                                 backstop_fn=vault_backstop, dry_run=dry_run),
         "vault-purge error")

    # Job 30 — ORPHANED refs/autopilot-wip/* RECLAIMER (#504): only when
    # `repo_roots` is given (same per-repo scoping + injected `git_fetch` as job
    # 28). Internally cadence-gated (own `wip_ref_last_sweep` key, generous 6h
    # default) + repo-batched, so the 60s tmux cadence never hammers origin.
    # Best-effort; the never-delete-a-salvageable-copy invariant lives in the
    # job (merged OR aged-past-7d only; lease-guarded deletes).
    _add("wip_ref_sweep", lambda: repo_roots is not None,
         lambda: sweep_orphaned_wip_refs(now, state, repo_roots=repo_roots,
                                         git_fetch=git_fetch, dry_run=dry_run,
                                         persist=lambda: save_state(state_path, state)),
         "wip-ref-sweep error")

    # Job 31 — GK SELF-SERVICE AUTO-BOUNCE (#516). Appended LAST (keeps the
    # kill-switch NOTICE pinned between job 11 and job 13). Only when a fetch is
    # wired (cmd_watchdog passes the real one; other jobs' unit tests stay
    # network-free); cadence-gated internally; best-effort. Runs before the next
    # sweep's job 11, so a request bounced here (needs-gatekeeper removed) is out
    # of job 11's fetch set from the next sweep on — at worst ONE transient
    # supervisor nudge (which itself resolves to the same manual-triage bounce),
    # never gk actually working a self-serviceable read.
    _add("gk_selfservice_bounce", lambda: gk_selfservice_fetch is not None,
         lambda: gk_selfservice_bounce(
             now, run, state, dry_run=dry_run,
             gh_fetch=gk_selfservice_fetch,
             persist=lambda: save_state(state_path, state)),
         "gk-selfservice-bounce error")

    # Job 32 (#515) — mechanical U-label lifecycle: clear a needs-answer /
    # needs-decision label whose question the owner already ANSWERED on Discord
    # (job-7 `_delivered` captured it into `state`), once the asking session
    # moved past the `❓`. Gated on `u_reconcile_clear` being wired (network-free
    # tests for every other job, exactly like jobs 8/11/31). Best-effort.
    _add("reconcile_u_labels", lambda: u_reconcile_clear is not None,
         lambda: reconcile_u_labels(
             now, state, dry_run=dry_run, projects_dir=projects_dir,
             clear_fn=u_reconcile_clear,
             persist=lambda: save_state(state_path, state)),
         "u-label-reconcile error")

    # Job 34 (#535) — PER-BOX CONFORMANCE CHECK. Appended LAST (keeps the
    # kill-switch NOTICE pinned between job 11 and job 13). Gated on
    # `conformance_root` being wired (cmd_watchdog passes REPO_DIR; network-free
    # tests for every other job). Internally cadence-gated (own
    # `conformance_last_check` key, daily) + fully injectable I/O seams; best-
    # effort. Every dimension fails safe to UNDETERMINED, never a false alarm.
    _add("conformance_check", lambda: conformance_root is not None,
         lambda: run_conformance_check(
             now, state, send_fn=send_fn, dry_run=dry_run,
             repo_root=conformance_root, is_target_check=conformance_is_target,
             persist=lambda: save_state(state_path, state)),
         "conformance-check error")

    # Job 35 (#543) — CENTRAL DEAD-BOX HEARTBEAT-MISSING DETECTOR, dev1-only.
    # The per-box conformance check (job 34) cannot report a DEAD box (its own
    # watchdog sends nothing); this reads the already-collected fleet.jsonl
    # liveness (burn snapshot per box, job 16) and LOUD-pings the owner when a
    # box goes silent past ~36h — with a collection-stale fail-safe (never
    # false-alarm the whole fleet). Coordinator-only (`conformance_hb_enabled`
    # = dev1, the SAME host gate job 16/19 use — only dev1 collects fleet.jsonl).
    # Internally cadence-gated (own `conformance_hb_last_check` key) + injectable
    # I/O seams; best-effort, every verdict fails safe to UNDETERMINED.
    _add("conformance_heartbeat_check", lambda: conformance_hb_enabled,
         lambda: run_conformance_heartbeat_check(
             now, state, send_fn=send_fn, dry_run=dry_run,
             persist=lambda: save_state(state_path, state)),
         "conformance-heartbeat error")

    # Job 36 (#551) — orphaned gk hand-off marker backstop. Gated on
    # `gkorphan_fetch` being wired (network-free tests for every other job,
    # exactly like jobs 8/11/31). Supervisor-box + cross-stream-repo only;
    # generous 6h internal cadence; best-effort, never a false accusation.
    _add("gk_orphan_marker_sweep", lambda: gkorphan_fetch is not None,
         lambda: gk_orphan_marker_sweep(
             now, run, state, send_fn=send_fn, dry_run=dry_run,
             gh_fetch=gkorphan_fetch,
             # #570: the PARALLEL comment-handoff pass, gated on its OWN fetch
             # being wired (the "wired = on" convention). None → the sweep runs
             # ONLY the mutated pass (byte-identical to #551).
             handoff_fetch=gkorphan_handoff_fetch,
             persist=lambda: save_state(state_path, state)),
         "gk-orphan-marker-sweep error")

    # Job 37 (#776) — RUNAWAY SHADOW-UGREP OS-PROCESS REAPER (the FIRST
    # OS-process reaper in the watchdog). Runs on EVERY box (a runaway ugrep
    # can orphan anywhere — a 15-day 295%-CPU orphan on subdev, #774); gated on
    # `reaper_ps_fetch` being wired (network-free tests for every other job,
    # exactly like jobs 8/11/31/36). NO cadence gate — a runaway must be caught
    # within one cycle after 30 min. Kills ONLY the exact `ugrep -G
    # --ignore-files` signature aged > 30 min; NEVER a young process; any
    # ps/parse/kill error kills NOTHING. Log-only self-heal (like the
    # api-error `continue`), never a Discord ping (#546).
    _add("shadow_ugrep_reaper", lambda: reaper_ps_fetch is not None,
         lambda: shadow_ugrep_reaper(
             ps_fetch=reaper_ps_fetch, kill_fn=reaper_kill_fn,
             dry_run=dry_run),
         "shadow-ugrep-reaper error")

    # Job 38 (#778) — HEAVY-BUILD-TOOLCHAIN OS-PROCESS REAPER, SHARED-STREAM
    # BOX ONLY. A SIBLING of Job 37 (opposite gating: kill-on-sight, no age/CPU
    # gate — a JVM/Android build daemon is BANNED OUTRIGHT on a shared-stream
    # box). Reuses the SAME `reaper_ps_fetch`/`reaper_kill_fn` seams (identical
    # ps read shape), so it gates on `reaper_ps_fetch is not None` exactly like
    # Job 37 — the box-class gate lives INSIDE heavy_build_reaper (off a
    # shared-stream box it reads no process table and kills nothing). Any
    # ps/parse/kill error kills NOTHING; log-only self-heal, never a Discord
    # ping (#546). See `heavy_build_reaper` in `watchdog/reaper.py`.
    _add("heavy_build_reaper", lambda: reaper_ps_fetch is not None,
         lambda: heavy_build_reaper(
             ps_fetch=reaper_ps_fetch, kill_fn=reaper_kill_fn,
             dry_run=dry_run),
         "heavy-build-reaper error")

    # Job 39 (#775) — VERIFY-ONLY shared-stream resource-guard check. Gated on
    # `resource_guard_gk_request` being wired (the "wired = on" convention,
    # network-free tests for every other job, like jobs 8/11/31/36/37/38). The
    # box-class gate lives INSIDE resource_guard_verify (off a shared-stream box
    # it reads no cgroup and never alarms). NO killer logic (#486) — it reads
    # this account's own cgroup memory ceiling and, if UNLIMITED, files ONE
    # deduped gk-request (via the wired seam) + a LOUD journal line. Fail-safe
    # toward silence (#539); machine-channel only, never a Discord ping (#546).
    _add("resource_guard_verify", lambda: resource_guard_gk_request is not None,
         lambda: resource_guard_verify(
             gk_request_fn=resource_guard_gk_request, dry_run=dry_run),
         "resource-guard-verify error")

    # Job 40 (#834) — PER-BOX DISK-PRESSURE GUARD. Gated on `disk_guard_enabled`
    # (cmd_watchdog passes True → it runs EVERY real poll on every box; left
    # False in run_once unit tests so the real `statvfs`/drain never touches a
    # developer box). It reads `statvfs` itself and acts ONLY on the calling
    # user's OWN home: every poll it writes the footer cache; only at >= 80 %
    # (and not as root, cadence-gated to <= once/10min, single-instance flocked)
    # does the du-heavy drain ladder run; >= 90 % after -> machine-channel
    # escalation. Best-effort; the never-delete-on-uncertainty + scope-fence
    # invariants live in the job. The ROOT/system-scope legs (other users'
    # /tmp, /var/log, system journal, apt, logrotate/fail2ban) are #841.
    _add("disk_guard", lambda: disk_guard_enabled,
         lambda: disk_guard.run_disk_guard(now, dry_run=dry_run),
         "disk-guard error")

    # --- EXECUTE THE STANDALONE REGISTRY (#433 step 16) — literal order. ONE
    # try/except = the SAME per-job isolation boundary; `err` logs a raise with
    # the job's custom prefix or (None) swallows it. Accumulation is verbatim.
    for _label, _gate, _invoke, _err in _standalone_registry:
        if _gate():
            try:
                logs += _invoke()
            except Exception as e:
                if _err:
                    logs.append("%s: %r" % (_err, e))

    save_state(state_path, state)
    return logs
