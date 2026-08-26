"""Discord notification sender for airuleset — the single Discord send path.

Two callers share this:
  * `hooks/notify-discord.sh` (the idle ❓/✅ mobile-app ping) asks ONLY for the
    `mention_prefix()` so it can tag the right person, then sends via its own curl.
  * the `/autopilot` supervisor sends the per-ticket COMPLETION CARD (the user's
    explicit ask: each finished+deployed ticket → a structured Slovak Discord
    message with Cieľ / Dosiahnuté / double-review + backlog progress).

Responsibilities:
  * resolve the OWNER from the tmux session group (zbynek / marek / …) and turn it
    into a Discord @mention so every message clearly targets the right person;
  * compose the canonical autopilot card (Slovak, structured markdown) FROM FIELDS
    so the structure is guaranteed by code, not by agent prose;
  * dedupe (atomic marker file) so the same ticket card is never posted twice
    (worker retry / supervisor re-dispatch);
  * POST to the Discord notification channel using the bot token + channel id from
    the shared Discord channel .env (the same config notify-discord.sh reads).

stdlib only. Every public function is fail-safe — a missing token, no tmux, an
unknown owner, or a network error degrades to "no mention / no send", never raises.
"""
import os
import re
import sys
import json
import time
import hashlib
import tempfile
import contextlib
import subprocess
import urllib.request

# Discord hard cap is 2000 chars per message; stay safely under it.
_MAX_CONTENT = 1900
# Per-field cap so one long goal/achieved can't dominate the card.
_FIELD_CAP = 320

_ENV_REL = "channels/discord/.env"
_DEDUP_DIRNAME = "autopilot-notify-sent"
_DEDUP_TTL_S = 14 * 24 * 3600
# Well under the ~255-byte NAME_MAX most filesystems enforce on a single path
# COMPONENT (ext4/xfs/btrfs) — leaves headroom for any TTL/status suffix a
# future caller might append to the sanitised key (#359).
_DEDUP_NAME_MAX = 180

# #559: throttle window (seconds) for an AUTO-derived key given to a send that
# arrived without a dedup_key. A keyless send used to bypass dedup AND trace
# entirely (logged key=-, 1871 lines / the single largest bucket on dev1); it
# now gets `auto:<owner>:<sha16(body)>:<bucket>` so identical spam WITHIN the
# window is throttled while distinct pings (distinct body -> distinct hash) and
# a legitimate re-send AFTER the window both still deliver. The window (default
# 300 s, env-overridable) bounds the dedup horizon to minutes rather than the
# 14-day marker TTL a bare content hash would inherit (cross_stream.py:304's
# documented "content-stable key swallowed by the 14-day TTL" trap).
AUTO_DEDUP_WINDOW_S = 300

# #687: cross-session (cross-USER) content dedup for the ✅ device ping. The ✅
# SHELL send path (notify-discord-send.sh, kind=shell) never routes through
# send()/_dedup_claim, and david1–4 are SEPARATE unix accounts (own $HOME), so
# a fleet-wide ✅ (a bounce resolved) is announced N times. A SHARED sticky /tmp
# store (not per-$HOME) coalesces an identical payload across users within the
# window. The window is short (coalesce a near-simultaneous fleet event), and it
# rides the FILENAME's time-bucket so there are no mtime races.
CONTENT_DEDUP_DIRNAME = "airuleset-notify-content-dedup"
CONTENT_DEDUP_WINDOW_S = 120

# Stream personas whose tmux session name has NO Discord identity of its own
# (airuleset#259, 2026-08-06): montalu/montalu2/montalu3/simap/miva1 (#300)
# route to zbynek's own thread; montalu4 routes to MAREK's own thread (his dev stream
# — airuleset#295, 2026-08-07: the user's own statement, independently
# corroborated by odoo-erp#2961's 2026-08-05 ACCESS DECISION comment,
# montalu4 is the ONLY montalu-family account marek's own SSH key was added
# to — montalu/montalu2/montalu3 stay zbynek-only). david routes to its own
# thread. Checked in resolve_owner() ITSELF — never via a bashrc
# AIRULESET_NOTIFY_OWNER export — so it takes effect on the very NEXT hook
# invocation everywhere. A bashrc export only reaches shells started AFTER
# the write; an adversarial review of the first version of this fix
# live-verified that simap's OWN already-running session kept misrouting
# after the bashrc line was applied, because that session's process
# environment predated the write and nothing short of restarting the live
# session (never done to another user's session) would have picked it up.
# `marek` is deliberately absent as a MAP KEY: its own tmux session name
# ("marek") already resolves directly and has its own
# DISCORD_NOTIFICATION_CHANNEL_MAREK/DISCORD_MENTION_MAREK keys, so no
# override is needed FOR IT — it is still a valid map VALUE (montalu4's
# redirect target). montalu/david ALSO still carry a redundant, hand-added
# `export AIRULESET_NOTIFY_OWNER=...` bashrc line from before this fix
# existed — harmless, since the env override is checked FIRST in
# resolve_owner() and carries the identical value either way.
STREAM_NOTIFY_OWNER = {
    # david1/montalu1/simap1 (#537): the NUMBERED names for the base-stream
    # rename — all three now LIVE (montalu1 2026-08-19, simap1 2026-08-18,
    # david1 2026-08-21). Each mirrors its base's routing decision EXACTLY —
    # same human owner, same thread — so the live rename flipped the linux user
    # without touching Discord routing. The bare `david` self-map is GONE (the
    # OS account no longer exists); `david1 -> david` carries the routing. Note
    # `david` STAYS a valid map VALUE (the owner of david1/david2/3/4, a real
    # Discord thread) — exactly like marek is a value but not a key; dropping
    # the self-map is behaviour-neutral (resolve_owner falls back to the tmux
    # session group for a self-mapped stream). marek deliberately stays
    # unnumbered (and out of this map, per the header comment above).
    "david1": "david",
    "montalu1": "zbynek",   # #537 rename of montalu (live 2026-08-19) — see the david1 comment above
    "montalu2": "zbynek",
    "montalu3": "zbynek",
    "montalu4": "marek",
    # montalu5/6/7/8 (airuleset#378, odoo-erp#3642): FOUR MORE full parallel
    # montalu streams -> claude-zbynek. Owner routing decision 2026-08-19
    # (airuleset#572) REVERSED the earlier 2026-08-11 #378 decision that
    # "montalu5 is operated by MAREK -> claude-marek": the owner directed that
    # montalu5's notifications go to claude-zbynek, so montalu5 now routes to
    # zbynek exactly like montalu6/7/8 (montalu4 stays Marek's own stream).
    # §6a still requires verifying delivery with a REAL notify-delivery.log
    # ping on the montalu5 box, not only configuring it.
    "montalu5": "zbynek",
    "montalu6": "zbynek",
    "montalu7": "zbynek",
    "montalu8": "zbynek",
    "simap1": "zbynek",   # #537 rename of simap (live 2026-08-18) — see the david1 comment above
    # miva1 (airuleset#300, 2026-08-07): phase-1 isolated stream, same shape
    # as simap/montalu -- its own tmux session name carries no Discord
    # identity of its own, so it redirects outright to zbynek's own thread.
    # DISCORD_MIRROR_MIVA1 was considered and rejected here (see #300's own
    # design comment): that mechanism lives entirely in a LOCAL, non-git
    # per-box .env this repo's code cannot provision or deploy, whereas this
    # redirect is the code-side ROUTING DECISION and needs no such file to
    # BE correct. It is NOT sufficient on its own for a real ping to land,
    # though -- like every other stream persona, miva1's own box still needs
    # its LOCAL ~/.claude/channels/discord/.env hand-wired (bot token +
    # DISCORD_NOTIFICATION_CHANNEL_ZBYNEK) before any notify call there does
    # anything but fail-safe silently; check_discord_notify_config() already
    # surfaces that gap loudly at install time, same as for every stream.
    "miva1": "zbynek",
    # david2/david3/david4 (airuleset#326, 2026-08-08): three MORE parallel
    # david streams -- additional capacity for the SAME external developer
    # david1 is, so they redirect to owner `david`'s own thread (the map VALUE
    # `david`, still a real Discord thread after the #537 self-map drop; the
    # base stream is david1 now, but the OWNER/thread stays `david`) -- never a
    # NEW DISCORD_MIRROR_DAVID2/3/4 local .env key, which
    # this repo's code cannot provision or deploy at all -- the ticket's own
    # suggested shape, rejected in favour of this code-side redirect, the
    # same #300 precedent already established for miva1). This also
    # inherits david's own real DISCORD_MIRROR_DAVID=zbynek local mirror
    # config for free, once resolved -- no extra config needed for that.
    #
    # RESIDUAL (adversarial-review MAJOR finding, #326): this routing
    # decision is CODE, not PROOF -- #326's own item 4 explicitly demands a
    # real test ping + a DELIVERED line per stream in
    # ~/.claude/notify-delivery.log before the ticket counts as done (the
    # #134/#135 "configured-but-unproven routing does not count as done"
    # policy). That live check is structurally impossible from this
    # worktree (no ssh, and the code isn't even deployed to david2/3/4 until
    # a post-merge `push` runs) -- it is the SUPERVISOR's job, after push,
    # before treating #326 as genuinely closed. See #326's own review
    # comment for the "Closes #326" auto-close caveat this residual implies.
    "david2": "david",
    "david3": "david",
    "david4": "david",
    # admin/stepan (airuleset#572, 2026-08-19): the forestshop-dev box's two
    # linux accounts (cli_fleet.py -- admin@forestshop-dev /
    # stepan@forestshop-dev). Owner directive: forestshop-dev notifications
    # go to claude-marek. Neither account has a Discord identity of its own
    # and neither is in AUTHORITY_BY_USER (both are full-authority), so
    # without this redirect resolve_owner() finds no per-owner mapping for
    # them (it returns "" with no tmux, else the box's own unmapped session
    # group), so notification_channel() falls back to the shared
    # DISCORD_NOTIFICATION_CHANNEL_ID thread -- the "chodí do claude" reported.
    #
    # GENERIC-USERNAME CAVEAT: "admin"/"stepan" are generic names that TODAY
    # exist in the managed fleet ONLY on the forestshop-dev box.
    # resolve_owner() maps the box's OWN _current_user() (there is no
    # box-qualified key), so if any FUTURE box ever adds a linux user
    # "admin"/"stepan" belonging to a DIFFERENT owner, this map MUST be
    # narrowed at that point. The risk is low (managed boxes use named
    # accounts -- newlevel/gatekeeper/david/montalu*/simap*/miva*/marek), but
    # it is real and must be re-checked whenever a new box is onboarded.
    #
    # RESIDUAL (routing decision != delivered ping, the #300 gap): this
    # redirect deploys with the next push, but a real ping still needs the
    # forestshop-dev box's own local ~/.claude/channels/discord/.env to carry
    # DISCORD_NOTIFICATION_CHANNEL_MAREK (+ DISCORD_MENTION_MAREK).
    # check_discord_notify_config() surfaces that gap loudly at install; the
    # supervisor provisions it and live-verifies with a real notify-delivery.log
    # ping at integration.
    "admin": "marek",
    "stepan": "marek",
}


def _current_user():
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "")

# --- delivery log (#135) ---------------------------------------------------
# A delivery that never happened used to leave NO trace anywhere: the shell
# `emit_one()` set DELIVERY_FAILED=1 and returned silently, and this module
# returned 'no-config'/'error' as a string nobody was obliged to consume. So
# "sent and rejected", "never sent" and "nothing to send" were one
# observation — which is how #134's five-day silence stayed invisible.
# ONE durable log, written by BOTH send paths (the shell hook appends to the
# same file), size-capped and never fatal.
_DELIVERY_LOG_REL = "notify-delivery.log"
_DELIVERY_LOG_CAP = 512000

# Redact anything that smells like a credential before it reaches Discord.
_SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9]+"
    r"|github_pat_[A-Za-z0-9_]+"
    r"|AKIA[0-9A-Z]+"
    r"|xox[a-z]-[A-Za-z0-9-]+"
    r"|-----BEGIN[^\n]*"
    r"|Bearer\s+[A-Za-z0-9._-]+)")


def _claude_dir():
    return os.path.join(os.path.expanduser("~"), ".claude")


def _env_path():
    return os.path.join(_claude_dir(), _ENV_REL)


def delivery_log_path():
    return os.path.join(_claude_dir(), _DELIVERY_LOG_REL)


def _log_field(value, cap=120):
    """One log FIELD: single-line and bounded, always.

    A caller can hand this an arbitrary string — a status that turned out to
    be a whole multi-line card body is not hypothetical, it happened live
    while building this (a test fake returned the body as its status). One
    such value turns the log into something no `grep`/`tail` can read, which
    defeats the entire point of having it."""
    s = " ".join(str(value or "-").split()) or "-"
    return s[:cap]


def log_delivery(status, kind="", key="", reason=""):
    """Append ONE line about a delivery decision. Diagnostics only: every write
    is guarded, so a read-only $HOME can never turn logging into a failed
    notification. Rotated at the cap like `compact-decisions.log`."""
    path = delivery_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # absent / unstat-able → 0 → nothing to rotate (never an exception path)
        if os.path.isfile(path) and os.path.getsize(path) > _DELIVERY_LOG_CAP:
            os.replace(path, path + ".1")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s %s kind=%s key=%s reason=%s\n"
                     % (stamp, _log_field(status, 40), _log_field(kind, 24),
                        _log_field(key, 120), _log_field(reason, 120)))
    except OSError:
        return False                 # never fatal — the notification matters more
    return True


def _read_env():
    """Parse the Discord channel .env into a dict. Tolerant of quotes / CRLF /
    comments. Returns {} if the file is absent or unreadable."""
    out = {}
    try:
        with open(_env_path(), encoding="utf-8", errors="replace") as h:
            for line in h:
                line = line.strip().lstrip("﻿")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip("'\"").strip().rstrip("\r")
    except OSError:
        return {}
    return out


def _clean(s):
    if s is None:
        return ""
    s = _SECRET_RE.sub("[redacted]", str(s)).replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_owner():
    """Return the lowercase owner of the current tmux session (e.g. 'zbynek' /
    'marek'), or "" when it can't be determined.

    AIRULESET_NOTIFY_OWNER overrides everything — for the non-tmux / test / future
    board-daemon path. Next, STREAM_NOTIFY_OWNER maps the current LINUX USER (an
    automated persona like simap/montalu/david, whose tmux session name carries
    no Discord identity of its own) straight to its real owner — checked here,
    not via a bashrc export, so it is correct on every invocation with no
    restart needed (#259). Otherwise the tmux SESSION GROUP is authoritative
    (sessions are 'zbynek-18' in group 'zbynek'); the session name with a
    trailing '-<n>' stripped is the fallback."""
    forced = os.environ.get("AIRULESET_NOTIFY_OWNER")
    if forced is not None:
        return re.sub(r"[^a-z0-9]", "", forced.strip().lower())
    mapped = STREAM_NOTIFY_OWNER.get(_current_user())
    if mapped:
        return mapped
    if not os.environ.get("TMUX"):
        return ""
    for fmt in ("#{session_group}", "#S"):
        try:
            r = subprocess.run(["tmux", "display-message", "-p", fmt],
                               capture_output=True, text=True, timeout=3)
        except Exception:
            return ""
        name = (r.stdout or "").strip()
        if name:
            name = re.sub(r"-\d+$", "", name)          # 'zbynek-18' -> 'zbynek'
            return re.sub(r"[^a-z0-9]", "", name.lower())
    return ""


def stream_redirect(raw_owner):
    """Redirect a RAW owner string (a tmux session name, a unix account) to
    its Discord identity via `STREAM_NOTIFY_OWNER` — the SAME map
    `resolve_owner()` applies to `_current_user()` internally, exposed here
    as a standalone form for a caller resolving SOMEONE/SOMETHING ELSE's raw
    owner string. Returns `raw_owner` UNCHANGED for anyone not in the map (a
    real person's own box, or a name that IS its own owner — e.g. 'david',
    which is a map VALUE/owner-thread but not a key after the #537 self-map
    drop, so it passes through to itself). Empty/falsy input passes through
    unchanged. Never raises.

    airuleset#212: `watchdog.pane_owner()` resolves a SPECIFIC PANE's owner
    straight from its tmux session name/group — a completely separate
    implementation from `resolve_owner()`, with NO knowledge of this
    redirect at all. On a stream-persona box (montalu/simap/montalu2-4)
    that raw value is the box's own unix account name (e.g. 'montalu'),
    which the deliberately-unconfigured per-owner `.env` keys (relying on
    THIS redirect) cannot address — so an account-wide alert built from that
    raw value (job 3's usage-limit ping, or any other watchdog job that
    @mentions "the owner of this pane") fell back to whatever the box's
    generic default channel/mention happened to be instead of the person
    the redirect is meant to reach. `watchdog.run_once()` calls this
    immediately after `pane_owner()` so every downstream consumer (job 3's
    account-wide alert, job 5/6/10/21's per-session pings) gets the
    corrected value."""
    if not raw_owner:
        return raw_owner
    return STREAM_NOTIFY_OWNER.get(raw_owner, raw_owner)


# #710 (owner directive 2026-08-26): owners whose ❓ QUESTION Discord delivery is
# turned OFF — they take questions in webterm + the footer `U N` (in-session ❓
# markers + the #606 step-by-step delivery), NOT a phone ping. The DISCORD POST
# of a `kind="questions"` ping is suppressed for these owners; the session ❓
# marker discipline, the question-map CODE and `needs-answer` tracking are
# untouched, and a TICKET-CARRYING question still folds into the footer `U N`
# via its `needs-answer` label. KNOWN bounded gap (#716): a genuinely TICKETLESS
# ❓ is no longer recorded in `discord-questions.json` (the record is coupled to
# a successful Discord POST via the returned message-id, which `record_question`
# requires to be a real snowflake), so it surfaces only in webterm (the session
# ❓ marker), not the `U N` ticketless fold — #716 preserves that fold. Owner
# `david` (and david1-4 -> `david`) keeps FULL question delivery, so it is
# deliberately NOT in this set.
QUESTION_PING_OWNERS_OFF = frozenset({"zbynek", "marek"})


def question_ping_off(owner):
    """True when a ❓ QUESTION ping to `owner`'s Discord thread must be
    SUPPRESSED (#710). Normalised via `stream_redirect` so a stream persona
    whose questions ROUTE INTO claude-zbynek / claude-marek (montalu5/montalu1/
    simap1 -> zbynek, ...) is classified by its real THREAD owner and is off
    too, while david1-4 -> `david` passes through and stays ON. Empty/None/
    unknown owner -> False (never suppress on "don't know" — the safe direction:
    a spurious ping is one extra line, a wrong suppression is a lost question).
    Never raises."""
    if not owner:
        return False
    return stream_redirect(str(owner).strip().lower()) in QUESTION_PING_OWNERS_OFF


def notification_channel(env=None, owner=None, kind="default", project=None):
    """Resolve the Discord channel/THREAD id to POST to for the current owner.

    Per-owner routing: each person gets their OWN thread so notifications don't
    mix (the user runs zbynek + marek side by side and an @mention in a shared
    thread was not enough — they want a separate `claude-zbynek` / `claude-marek`
    thread). `DISCORD_NOTIFICATION_CHANNEL_<OWNER>` (e.g.
    DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=<thread id>) wins when set; it falls back
    to the shared `DISCORD_NOTIFICATION_CHANNEL_ID` when the owner has no per-owner
    thread configured OR the owner can't be determined (no tmux). Returns "" when
    neither is set. A Discord thread IS a channel in the API, so the POST target is
    identical — only the id differs.

    `kind="questions"` (#296) resolves the owner's SEPARATE questions thread
    (`DISCORD_NOTIFICATION_CHANNEL_<OWNER>_Q`) FIRST — so a ❓ ping lands in its
    own `claude-<owner>-q` thread instead of mixing with ✅/card/api-error pings
    in the owner's normal thread. It then falls through to the EXACT SAME cascade
    `kind="default"` already uses (the owner's normal thread, then the shared id)
    whenever the questions thread isn't configured yet for that owner — so an
    owner with no provisioned `-q` thread keeps their pre-#296 behaviour
    (questions land in their normal thread) instead of silently losing them to
    the shared channel. `kind="default"` (the parameter's default) is
    byte-for-byte the pre-#296 behaviour — every EXISTING caller (`send()`, the
    run-card, api-error) never passes `kind=` at all and is unaffected.

    `project` (#369) resolves the owner's PER-PROJECT thread
    (`DISCORD_NOTIFICATION_CHANNEL_<OWNER>_P_<SLUG>`) — checked ONLY under
    `kind="default"` (the parameter's own default), FIRST, before the owner's
    normal thread. It is DELIBERATELY IGNORED when `kind == "questions"` —
    #369's own design decision keeps the questions thread CENTRALIZED, never
    project-split (see the ticket's design comment for the full rationale:
    job 7's Discord-reply routing is a security-critical mechanism this
    ticket does not touch). `project=None` (the default) is byte-for-byte
    the pre-#369 behaviour for every existing caller."""
    env = _read_env() if env is None else env
    owner = resolve_owner() if owner is None else owner
    if owner:
        if kind == "questions":
            perq = (env.get("DISCORD_NOTIFICATION_CHANNEL_" + owner.upper() + "_Q")
                    or "").strip()
            if perq:
                return perq
        elif project:
            perp = (env.get(_owner_project_key(owner, project)) or "").strip()
            if perp:
                return perp
        per = (env.get("DISCORD_NOTIFICATION_CHANNEL_" + owner.upper()) or "").strip()
        if per:
            return per
    return (env.get("DISCORD_NOTIFICATION_CHANNEL_ID") or "").strip()


# Min seconds between background provision-thread spawns, PER OWNER (#330) —
# mirrors statusbar.SPAWN_GUARD_S's own marker-mtime shape, just a wider
# window: provisioning is a one-time-until-persisted repair, not a routine
# per-render refresh, so there is no benefit to retrying every few seconds.
_QTHREAD_SPAWN_GUARD_S = 300


def _qthread_spawn_guard_path(owner):
    return os.path.join(_claude_dir(), "notify-qthread-spawn", owner)


def _qthread_spawn_claim(guard):
    """Atomically CLAIM `guard` for this call — True iff THIS call may
    proceed to spawn. #330 adversarial-review F2 (MAJOR): the original
    check-then-create was a plain `os.path.exists`+`getmtime` TEST followed
    by a SEPARATE `open()` — a classic TOCTOU race. Measured live: 8
    concurrent callers for the SAME owner produced 4-5 real spawns, not 1,
    which both feeds F1 (many concurrent `_env_upsert` writers) and can
    fork a SECOND real Discord thread — the exact duplicate-thread bug
    #296's own find-before-create was built to prevent.

    `O_CREAT|O_EXCL` is atomic at the OS level: of any number of racing
    callers, AT MOST ONE can ever win a fresh path. On a STALE guard
    (older than `_QTHREAD_SPAWN_GUARD_S` — a genuine retry window), this
    reclaims it via unlink-then-recreate, itself gated by a SECOND
    `O_CREAT|O_EXCL` — so at most one of any number of callers racing the
    reclaim wins that too. Residual, accepted risk: a caller mid-reclaim
    (between its own `unlink` and `open`) can theoretically overlap with a
    caller that had not yet made its OWN first `open` attempt, admitting
    two winners in that narrow window instead of one — low-consequence
    now that F1 makes concurrent `_env_upsert` writers safe (worst case:
    one harmless duplicate, idempotent `find`-before-`create`
    provisioning attempt, never file corruption)."""
    try:
        os.makedirs(os.path.dirname(guard), exist_ok=True)
    except OSError:
        return False
    try:
        os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except OSError as exc:
        if not isinstance(exc, FileExistsError):
            return False
        # FileExistsError: a sibling call already holds the guard -- fall
        # through to the staleness/reclaim check below instead of refusing
        # outright (that is the whole reason a stale guard is reclaimable).
    try:
        if time.time() - os.path.getmtime(guard) < _QTHREAD_SPAWN_GUARD_S:
            return False   # fresh: a sibling call already owns this window
    except OSError:
        return False       # can't even stat it -- treat as "someone else has it"
    try:
        os.unlink(guard)
        os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except OSError:
        return False        # lost the reclaim race -- another caller wins


def _spawn_provision_question_thread(owner):
    """Kick a DETACHED, background `notify --provision-question-thread
    --find-only` for `owner` (#330) — guarded by `_qthread_spawn_claim`
    (mirrors `statusbar._spawn_refresh`'s own marker-mtime GUARD shape, now
    made ATOMIC — no new mechanism) so a burst of ❓ deliveries for the
    same not-yet-provisioned owner spawns at most one attempt per
    `_QTHREAD_SPAWN_GUARD_S`. `--find-only` (#330 F3): the AUTOMATIC,
    unattended self-heal must never auto-CREATE a brand-new Discord thread
    — only pick up one a human (or another box) already made; the
    explicit, human-typed CLI action keeps find-then-CREATE. Never raises:
    a failure to even SPAWN the attempt just means the box stays on the
    (already safe, already logged) fallback a little longer — never a
    lost ping, since the delivery this rides alongside has ALREADY been
    resolved by the caller using the same fallback channel it always
    has."""
    if not _qthread_spawn_claim(_qthread_spawn_guard_path(owner)):
        return
    # os.path.realpath (not abspath) mirrors statusbar._spawn_refresh's own
    # Path(__file__).resolve() precedent verbatim — follows a symlink to
    # this module, should one ever exist (#330 adversarial-review F8,
    # THEORETICAL: unreached today, since notify/ is a real directory in
    # every checkout and no install/push step symlinks it).
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
        "airuleset.py")
    try:
        subprocess.Popen(
            [sys.executable, script, "notify", "--provision-question-thread",
             "--owner-name", owner, "--find-only"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as exc:
        # Widened from OSError to Exception (#330 adversarial-review F8):
        # Popen can also raise ValueError (an embedded NUL, a bad argument
        # combination) or UnicodeEncodeError — THEORETICAL today (every
        # real caller reaches `owner` via resolve_owner()'s own
        # `[a-z0-9]`-only sanitizing regex before it ever gets here), but
        # this is the ONE function that must NEVER raise into the live ❓
        # delivery path it rides alongside, matching
        # `statusbar._spawn_refresh`'s own `except Exception` precedent.
        # Could not even LAUNCH the self-heal attempt — the fallback
        # delivery this rides alongside already happened safely (see
        # resolve_questions_channel); log the degraded state instead of
        # guessing whether a retry helps.
        log_delivery("spawn-error", kind="questions", key=owner, reason=repr(exc))


def resolve_questions_channel(env=None, owner=None, spawn=None):
    """The channel id a REAL ❓ delivery should POST to (#330) — the single
    call site `--channel-id --kind questions` (and so
    `hooks/notify-discord-send.sh`'s `emit_one()`, the ONLY path that ever
    delivers a genuine question) should use, in place of calling
    `notification_channel(kind="questions")` directly.

    The RETURNED value is byte-identical to `notification_channel`'s own —
    this never changes WHICH channel a fallback delivery lands in, only
    whether the fallback is silent. When the owner's `-q` thread IS already
    configured on this box (the fast, common path — dev1, and any box
    someone has run `--provision-question-thread` on), this is a pure,
    zero-network read with NO side effect at all, identical to before.

    Only when the owner's `_Q` key is genuinely ABSENT locally does this
    additionally (1) write a LOUD, DISTINGUISHABLE delivery-log line
    (`status="fallback"`, `reason="q-thread-not-provisioned-on-this-host"`)
    — so a silent fallback (100% of gatekeeper's ❓ history, live-confirmed
    #330) is a `grep fallback notify-delivery.log` away instead of hiding
    inside an identical `sent` line — and (2) kick a GUARDED, DETACHED
    background attempt (`_spawn_provision_question_thread`, default; a test
    double via `spawn=`) to provision it, so the NEXT ❓ for that owner on
    that box self-heals with no manual per-box step ever needed again.

    A fully SYNCHRONOUS provisioning attempt before falling back was
    considered and rejected here: `find_owner_question_thread` /
    `create_owner_question_thread` can need up to 3-5 sequential Discord
    REST round-trips (`_discord_api`'s own per-call timeout is 6s) against a
    Stop-hook budget of only 15s total for `notify-discord-pending.sh`
    (`settings/hooks.json`) — a budget the EXISTING code already spends ~3s
    of on its own pre-send settle sleep plus up to 5s on the real POST's own
    `--max-time`. Blocking the in-flight delivery on that risks the Stop
    hook itself timing out and the ❓ never reaching the user at all —
    strictly worse than today's silent-but-safe fallback, for a device whose
    entire purpose is guaranteeing a genuine question always reaches the
    phone.

    Gated on `bot_token(env)` being present (#330 round-2 adversarial
    review MINOR 6): a box with NO Discord bot token at all is ALREADY
    going to fail delivery for a more fundamental reason
    (`notify-discord-send.sh`'s own pre-existing `no-token` check) — a
    "fallback ... q-thread-not-provisioned" line on top of that points the
    operator at the wrong repair (`check_discord_notify_config()`'s job,
    not a `-q` thread), and the self-heal spawn would be doomed before its
    first network call anyway."""
    env = _read_env() if env is None else env
    owner = resolve_owner() if owner is None else owner
    chan = notification_channel(env=env, owner=owner, kind="questions")
    # The whole log+spawn side effect is wrapped so a genuinely unexpected
    # failure here can NEVER raise into the caller — this function sits on
    # the live ❓ delivery's critical path, and `chan` above has ALREADY
    # been resolved by the time this runs (#330 round-1 F8's own "must
    # never raise into the live ❓ path" principle, applied structurally
    # rather than by per-call inspection).
    try:
        if owner and bot_token(env):
            configured = (env.get("DISCORD_NOTIFICATION_CHANNEL_" + owner.upper()
                                  + "_Q") or "").strip()
            if not configured:
                log_delivery("fallback", kind="questions", key=owner,
                             reason="q-thread-not-provisioned-on-this-host")
                (spawn if spawn is not None
                 else _spawn_provision_question_thread)(owner)
    except Exception as exc:
        # stderr only — never log_delivery() itself here, to avoid a
        # failure IN reporting the failure. `chan` is already resolved and
        # returned below regardless; this side effect is purely diagnostic.
        print("resolve_questions_channel: self-heal side effect failed: %r"
             % exc, file=sys.stderr)
    return chan


# --------------------------------------------------------------------------- #
# #296 -- provisioning the per-owner QUESTIONS thread (claude-<owner>-q).
#
# No code in this repo has ever created a Discord thread before -- the
# existing per-owner threads (claude-zbynek / claude-marek / claude-david)
# were configured BY HAND into the .env. #296's own body allows the mechanism
# to CREATE the thread ("vlákno sa vytvorí/nájde"), and the ticket's live
# acceptance criterion needs a REAL thread to post a test ❓ into -- so this
# ships a thin, explicit, ONE-TIME provisioning action
# (`notify --provision-question-thread`), NOT wired into `push`/`install`
# (creating a thread for every possible stream owner on every deploy was not
# asked for). It anchors the new thread as a SIBLING of the owner's EXISTING
# thread (same Discord parent channel, found via one GET), so it needs no
# extra "which channel" configuration, and persists the id into the local
# (non-git) .env so every later resolution is a pure, side-effect-free read.
# --------------------------------------------------------------------------- #


def _owner_anchor_channel(env, owner):
    """The owner's OWN configured normal thread —
    `DISCORD_NOTIFICATION_CHANNEL_<OWNER>` ONLY, never the cascaded/shared
    fallback `notification_channel()` would fall through to. Used to anchor
    a NEW `-q` thread: falling back to the SHARED channel here would (in the
    rare case that shared channel itself happens to be a thread) let one
    owner's questions-thread creation silently anchor off infrastructure
    meant for everyone, not this owner — and "no existing thread to anchor
    off of" should mean exactly that, not "no shared fallback either"."""
    if not owner:
        return ""
    return (env.get("DISCORD_NOTIFICATION_CHANNEL_" + owner.upper()) or "").strip()


def _channel_parent_id(token, channel_id, http=None):
    """The `parent_id` Discord reports for `channel_id` — populated both for
    a genuine THREAD (its parent channel) and for a plain guild channel that
    sits inside a CATEGORY (the category's id), so a non-null result alone
    does NOT prove `channel_id` is a thread. `create_owner_question_thread`
    relies on Discord itself rejecting a malformed thread-create POST
    against a category id (it can never create a thread in the wrong
    place), not on this function disambiguating the two shapes. None when
    the lookup fails or the channel genuinely has no parent. Never raises."""
    api = http or _discord_api
    info = api(token, "GET", "channels/%s" % channel_id)
    return info.get("parent_id") if isinstance(info, dict) else None


def _threads_of(resp):
    """The list of thread dicts in a Discord thread-listing response
    (`{"threads": [...], ...}`), or [] for any unexpected shape — a failed
    API call (`resp` not a dict), a missing/None/non-list `threads` key, or
    non-dict entries inside it. Never raises."""
    threads = resp.get("threads") if isinstance(resp, dict) else None
    if not isinstance(threads, list):
        return []
    return [t for t in threads if isinstance(t, dict)]


def find_owner_question_thread(env, owner, http=None):
    """FIND an existing `claude-<owner>-q` thread — the "nájde" half of
    #296's own "vytvorí/nájde" requirement (adversarial-review MAJOR
    finding). Without this, provisioning the SAME owner from a SECOND box
    (or after a lost/rebuilt LOCAL .env — the questions-thread id is
    box-local, non-git config) never searched Discord for an
    already-created thread and unconditionally POSTED a new one, silently
    forking that owner's questions across two threads. Searches the
    owner's anchor channel's ACTIVE guild threads first, then its ARCHIVED
    PUBLIC threads (a week-idle `-q` thread auto-archives at the
    10080-minute duration `create_owner_question_thread` itself sets).
    Returns the existing thread id, or "" when genuinely absent / on any
    lookup failure — never guesses, never raises."""
    token = bot_token(env)
    if not token or not owner:
        return ""
    parent_ch = _owner_anchor_channel(env, owner)
    if not parent_ch:
        return ""
    api = http or _discord_api
    info = api(token, "GET", "channels/%s" % parent_ch)
    if not isinstance(info, dict):
        return ""
    parent_id = info.get("parent_id")
    guild_id = info.get("guild_id")
    if not parent_id:
        return ""
    name = "claude-%s-q" % owner
    if guild_id:
        active = api(token, "GET", "guilds/%s/threads/active" % guild_id)
        for t in _threads_of(active):
            if t.get("parent_id") == parent_id and t.get("name") == name:
                return t.get("id") or ""
    archived = api(token, "GET",
                   "channels/%s/threads/archived/public" % parent_id)
    for t in _threads_of(archived):
        if t.get("name") == name:
            return t.get("id") or ""
    return ""


def create_owner_question_thread(env, owner, http=None):
    """Create the QUESTIONS thread `claude-<owner>-q` — a sibling of the
    owner's EXISTING normal thread (same PARENT channel), the SAME mechanism
    the existing per-owner threads already use (a real Discord thread the
    bot posts into), just extended to a second thread per owner. Returns the
    new thread id, or "" on any failure (no token, no owner, the owner has
    no existing thread to anchor off of, the parent lookup/create call
    fails) — never raises. Does NOT persist the result, and does NOT search
    for an existing thread first; see `provision_question_thread` for the
    idempotent find-then-create-and-save action (`find_owner_question_thread`
    is the "find" half)."""
    token = bot_token(env)
    if not token or not owner:
        return ""
    parent_ch = _owner_anchor_channel(env, owner)
    if not parent_ch:
        return ""
    api = http or _discord_api
    parent_id = _channel_parent_id(token, parent_ch, http=api)
    if not parent_id:
        return ""
    resp = api(token, "POST", "channels/%s/threads" % parent_id,
              {"name": "claude-%s-q" % owner, "type": 11,
               "auto_archive_duration": 10080})
    return (resp.get("id") if isinstance(resp, dict) else "") or ""


def _env_upsert(path, key, value):
    """Append/replace ONE `KEY=value` line in the local .env at `path` —
    creates the file (and its parent dir) if missing. Idempotent: replaces
    an EXISTING `KEY=...` line in place rather than duplicating it. The read
    tolerates non-UTF8 bytes the SAME way `_read_env()` already does
    (`errors="replace"` — this file also holds `DISCORD_BOT_TOKEN`, so
    falling back to an EMPTY line list on a decode wobble would silently
    destroy every other key instead of just this one). The write is
    ATOMIC (a per-call UNIQUE tmp file + `os.replace`, mirroring
    `_save_questions()`'s own atomic shape in this same module) — a crash
    mid-write can never leave the token file half truncated, and (#330
    adversarial-review F1, CRITICAL) two CONCURRENT callers can never
    interleave either: the original implementation shared ONE FIXED tmp
    path (`path + ".tmp"`) across every caller, so a later writer's
    `os.replace` could publish an EARLIER writer's still-being-written
    (truncated/EMPTY) file, destroying every key including
    `DISCORD_BOT_TOKEN` — measured live at ~3.2% per upsert under 2
    concurrent writers. `tempfile.mkstemp`, in the SAME directory as
    `path` (so `os.replace` stays an atomic same-filesystem rename), gives
    every call a collision-proof name — no caller can ever observe or
    publish another caller's in-progress write. Returns True on success,
    False on any I/O failure. Never raises."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            lines = []
        prefix = key + "="
        found = False
        for i, ln in enumerate(lines):
            if ln.strip().startswith(prefix):
                lines[i] = "%s=%s\n" % (key, value)
                found = True
                break
        if not found:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append("%s=%s\n" % (key, value))
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(path) or ".",
            prefix=os.path.basename(path) + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            os.replace(tmp, path)
        except OSError:
            # #330 round-2 adversarial review MINOR 3: the OLD fixed tmp
            # name (`path + ".tmp"`) leaked at most ONE stray file on a
            # failed write (silently overwritten by the next successful
            # call); the per-call UNIQUE name this function now uses (the
            # F1 fix) leaks a NEW orphaned file on every failure instead.
            # Best-effort cleanup on the way out — the ORIGINAL failure is
            # re-raised and handled (-> False) by the outer except below;
            # a failure to even unlink the orphan is not worth its own
            # report on top of that.
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        return True
    except OSError:
        return False


def provision_question_thread(owner, env=None, env_path=None, http=None,
                              create=True):
    """Idempotently ENSURE `DISCORD_NOTIFICATION_CHANNEL_<OWNER>_Q` is
    configured for `owner` (#296): returns the EXISTING id unchanged (zero
    network calls) when already set; otherwise FINDS an existing
    `claude-<owner>-q` thread on Discord itself (`find_owner_question_thread`
    — the fix for the duplicate-thread-per-box finding above) before
    falling back to creating one (`create_owner_question_thread`), then
    appends the key to the local .env (`_env_upsert`). Returns the id on
    success, "" on any failure — never raises. A one-time, explicit
    provisioning action (CLI: `notify --provision-question-thread`), NOT
    wired into every `push`/`install`.

    `create=False` (#330) limits this to the FIND half only — never POSTs
    a new thread. This is what the AUTOMATIC background self-heal
    (`_spawn_provision_question_thread`) passes, so an unattended,
    detached, periodic retry can only ever pick up a `-q` thread a HUMAN
    (or another box) already created, never spin one up on its own —
    an unsupervised auto-CREATE risks a duplicate thread whenever a
    transient network hiccup makes `find_owner_question_thread` return ""
    even though the thread genuinely exists. The explicit CLI keeps
    `create=True` (its pre-#330 default) completely unchanged."""
    if not owner:
        return ""
    path = env_path if env_path is not None else _env_path()
    env = _read_env() if env is None else env
    existing = (env.get("DISCORD_NOTIFICATION_CHANNEL_" + owner.upper() + "_Q")
                or "").strip()
    if existing:
        return existing
    new_id = find_owner_question_thread(env, owner, http=http)
    if not new_id and create:
        new_id = create_owner_question_thread(env, owner, http=http)
    if not new_id:
        return ""
    _env_upsert(path, "DISCORD_NOTIFICATION_CHANNEL_" + owner.upper() + "_Q",
               new_id)
    return new_id


# --------------------------------------------------------------------------- #
# #718 -- wire the CREATE-capable questions-thread provisioning into `install`.
#
# Root cause it closes: `--provision-question-thread` WITH create (the only
# path that POSTs a new thread) is a ONE-TIME, EXPLICIT CLI action, never wired
# into push/install (#296: "creating a thread for every possible stream owner
# on every deploy was not asked for"), and the AUTOMATIC per-❓ self-heal is
# `--find-only` by #330 design (never auto-CREATE unattended). So a
# `claude-<owner>-q` thread NOBODY ever explicitly created stays uncreated
# forever, and every ❓ ping for that owner falls back into their main thread
# (live incident #718: david on subdev). Since #710 made question DELIVERY a
# per-owner decision, the principled set to provision-at-install is now exact:
# the SINGLE question-delivery-ENABLED owner THIS box actually delivers AS
# (`resolve_owner()`, which maps david1-4 -> "david" via STREAM_NOTIFY_OWNER
# with no tmux needed), skipping the #710-suppressed owners (zbynek/marek) and
# any box with no owner / no bot token. Install is a deliberate, non-per-❓
# action (the same tier #296 already blesses for create), so it carries
# create=True WITHOUT the duplicate-thread risk #330 fenced off for the
# unattended self-heal.
# --------------------------------------------------------------------------- #


def provision_owner_question_thread_for_install(env=None, env_path=None,
                                                http=None, owner=None):
    """At install, ensure the question-delivery-ENABLED owner THIS box delivers
    AS has a provisioned `claude-<owner>-q` thread (#718). Reuses the existing
    idempotent `provision_question_thread(create=True)` (find-then-create-and-
    persist), so it CREATES the thread once (on the first such box to install)
    and FINDS+persists it on every subsequent box — self-healing fleet-wide.

    Returns `{"owner": <owner or "">, "status": <str>, "thread": <id or "">}`,
    status one of:
      - "skip-no-owner"  : this box delivers as no owner (nothing to provision)
      - "skip-suppressed": owner's ❓ delivery is OFF (#710 zbynek/marek) — no
                           -q thread is needed; makes ZERO Discord calls
      - "skip-no-token"  : no bot token here (a more fundamental gap that
                           `check_discord_notify_config` already reports loudly
                           — a -q gap on top would point at the wrong repair,
                           the SAME rationale `resolve_questions_channel` uses)
      - "provisioned"    : the -q thread id is now configured (found/created)
      - "gap"            : enabled owner + token present, but the thread could
                           not be found/created (no anchor / Discord
                           unreachable) — a durable `provision-gap` delivery-log
                           line is written; `cmd_install` also prints LOUD.
    Never raises (best-effort install step)."""
    owner_for_log = owner
    try:
        env = _read_env() if env is None else env
        owner = resolve_owner() if owner is None else owner
        owner_for_log = owner
        if not owner:
            return {"owner": "", "status": "skip-no-owner", "thread": ""}
        if question_ping_off(owner):
            return {"owner": owner, "status": "skip-suppressed", "thread": ""}
        if not bot_token(env):
            return {"owner": owner, "status": "skip-no-token", "thread": ""}
        tid = provision_question_thread(owner, env=env, env_path=env_path,
                                        http=http, create=True)
        if tid:
            return {"owner": owner, "status": "provisioned", "thread": tid}
        log_delivery("provision-gap", kind="questions", key=owner,
                     reason="install: q-thread enabled but not find/create-able")
        return {"owner": owner, "status": "gap", "thread": ""}
    except Exception as exc:
        # This is a best-effort install step riding no live delivery — a
        # provisioning attempt that blows up is a GAP, reported like any other,
        # never an exception into cmd_install (matches the surrounding steps'
        # non-fatal try/except contract).
        try:
            log_delivery("provision-gap", kind="questions",
                         key=str(owner_for_log or ""),
                         reason="install-exc: %r" % (exc,))
        except Exception as log_exc:
            # A failure IN reporting the failure — stderr only, never re-raise
            # (mirrors resolve_questions_channel's own side-effect guard). The
            # gap result below is still returned regardless.
            print("provision_owner_question_thread_for_install: gap-log "
                  "failed: %r" % (log_exc,), file=sys.stderr)
        return {"owner": str(owner_for_log or ""), "status": "gap",
                "thread": ""}


def format_qthread_install_report(result):
    """Install-output lines for a `provision_owner_question_thread_for_install`
    result (#718) — PURE, returns the list of lines, never prints. LOUD only on
    "gap" (an enabled owner whose -q thread could not be provisioned — machine
    channel, NEVER an owner Discord ping); a quiet one-line confirmation on
    "provisioned"; SILENT on every skip-* (no owner / suppressed owner / no
    Discord — all legitimate non-cases that need no operator attention)."""
    result = result or {}
    status = result.get("status", "")
    owner = result.get("owner", "") or ""
    if status == "provisioned":
        return ["    Discord questions thread ready for %s (claude-%s-q)."
                % (owner, owner)]
    if status == "gap":
        return [
            "    ⚠ Discord questions thread claude-%s-q NOT provisioned on "
            "this host." % owner,
            "      ❓ pings for %s fall back into the main claude-%s thread "
            "(questions mixed with cards)." % (owner, owner),
            "      Check: DISCORD_NOTIFICATION_CHANNEL_%s anchor present + bot "
            "can find/create threads here." % owner.upper(),
        ]
    return []


# --------------------------------------------------------------------------- #
# #369 -- per-PROJECT threads, generalizing #296's per-owner QUESTIONS
# thread to "one thread per project/subdev-stream, per owner". Mirrors the
# question-thread trio above (find/create/provision) as closely as
# possible, verbatim in shape, parametrized by a PROJECT LABEL instead of
# the fixed "questions" concept — see the ticket's own design comment for
# the full rationale (why the questions thread itself stays centralized and
# is NOT part of this split; why a handful of watchdog "session/pane
# health" alerts also stay on the owner's plain thread by design).
# --------------------------------------------------------------------------- #

_PROJECT_KEY_MAX = 60           # cap on the SLUG portion of an env-key suffix
_PROJECT_THREAD_NAME_MAX = 80   # cap on the slug portion of a Discord thread
                                # name (Discord's own hard cap is 100 chars;
                                # this leaves headroom for "claude-<owner>-")


def _project_env_slug(label):
    """ENV-KEY-safe suffix for a project LABEL: `[A-Z0-9_]+`, capped. Distinct
    from `project_thread_slug()` (the Discord THREAD-NAME form) because a
    `.env` key and a Discord channel name have different charsets. "" for an
    empty/unusable label (a caller must treat "" as "no usable project")."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(label or "").strip()).strip("_").upper()
    return s[:_PROJECT_KEY_MAX]


def _owner_project_key(owner, project_label):
    """The `.env` KEY for owner+project's channel id. The `_P_` separator
    (never a bare `_`) keeps this namespace DISTINCT from the questions
    thread's own `_Q` suffix — without it, a project literally named "q"
    would collide with `DISCORD_NOTIFICATION_CHANNEL_<OWNER>_Q`, silently
    routing a project's traffic into the SHARED questions thread."""
    return ("DISCORD_NOTIFICATION_CHANNEL_" + str(owner).upper() + "_P_"
           + _project_env_slug(project_label))


def project_thread_slug(label):
    """Discord thread-NAME suffix for a project label — lowercase,
    dash-separated, ASCII alnum only, capped well under Discord's 100-char
    channel-name limit. "project" for an empty/unusable label (a thread name
    must never be empty)."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(label or "").strip()).strip("-").lower()
    return (s or "project")[:_PROJECT_THREAD_NAME_MAX]


def project_label_for(cwd, run=None):
    """The per-project routing/display LABEL for `cwd` (#369): the
    canonical, ORIGIN-derived repo name (`repo_name_for` — never a
    checkout-directory basename, which this repo's own history has shown
    can diverge from the real repo name, e.g. a local `odoo-slovnormal`
    checkout of the real `odoo-erp`), STREAM-QUALIFIED (`stream_qualified`)
    so a sub-dev stream's own checkout of the SAME repo gets its own
    distinguishable label ("odoo-erp-david2") instead of colliding with the
    maintainer's own thread. Falls back to the cwd's own basename when no
    git repo / no origin can be resolved (a bare directory, a repo with no
    remote) — never empty for a real cwd, so a hook computing a routing key
    always has SOMETHING stable to key on. This is the SAME label already
    used for run-card headers (`compose_autopilot_card`'s own
    `stream_qualified(repo_name)`), so a project's thread name and its own
    card header text always agree."""
    name = repo_name_for(cwd, run=run)
    if not name:
        name = os.path.basename(str(cwd or "").rstrip("/")) or "unknown"
    return stream_qualified(name)


def find_owner_project_thread(env, owner, project_label, http=None):
    """FIND an existing `claude-<owner>-<project-slug>` thread (#369) — the
    "find" half of `provision_project_thread`'s idempotent find-then-create,
    mirroring `find_owner_question_thread` verbatim (see its own docstring
    for the full duplicate-thread-across-boxes rationale this closes:
    provisioning the SAME owner+project from a SECOND box must reuse the
    first box's thread, never fork a duplicate one). Searches the owner's
    anchor channel's ACTIVE guild threads first, then its ARCHIVED PUBLIC
    threads. Returns the existing thread id, or "" on any lookup
    failure/genuine absence — never guesses, never raises."""
    token = bot_token(env)
    if not token or not owner or not project_label:
        return ""
    parent_ch = _owner_anchor_channel(env, owner)
    if not parent_ch:
        return ""
    api = http or _discord_api
    info = api(token, "GET", "channels/%s" % parent_ch)
    if not isinstance(info, dict):
        return ""
    parent_id = info.get("parent_id")
    guild_id = info.get("guild_id")
    if not parent_id:
        return ""
    name = "claude-%s-%s" % (owner, project_thread_slug(project_label))
    if guild_id:
        active = api(token, "GET", "guilds/%s/threads/active" % guild_id)
        for t in _threads_of(active):
            if t.get("parent_id") == parent_id and t.get("name") == name:
                return t.get("id") or ""
    archived = api(token, "GET",
                   "channels/%s/threads/archived/public" % parent_id)
    for t in _threads_of(archived):
        if t.get("name") == name:
            return t.get("id") or ""
    return ""


def create_owner_project_thread(env, owner, project_label, http=None):
    """Create the PROJECT thread `claude-<owner>-<project-slug>` — a sibling
    of the owner's EXISTING normal thread (same parent channel), mirroring
    `create_owner_question_thread` verbatim. Returns the new thread id, or ""
    on any failure — never raises. Does NOT persist the result, and does NOT
    search for an existing thread first; see `provision_project_thread` for
    the idempotent find-then-create-and-save action."""
    token = bot_token(env)
    if not token or not owner or not project_label:
        return ""
    parent_ch = _owner_anchor_channel(env, owner)
    if not parent_ch:
        return ""
    api = http or _discord_api
    parent_id = _channel_parent_id(token, parent_ch, http=api)
    if not parent_id:
        return ""
    resp = api(token, "POST", "channels/%s/threads" % parent_id,
              {"name": "claude-%s-%s" % (owner, project_thread_slug(project_label)),
               "type": 11, "auto_archive_duration": 10080})
    return (resp.get("id") if isinstance(resp, dict) else "") or ""


def provision_project_thread(owner, project_label, env=None, env_path=None,
                             http=None, create=True):
    """Idempotently ENSURE the owner+project channel key is configured
    (#369): returns the EXISTING id unchanged (zero network calls) when
    already set; otherwise FINDS an existing `claude-<owner>-<slug>` thread
    on Discord itself before falling back to creating one, then appends the
    key to the local `.env`. Mirrors `provision_question_thread` verbatim,
    including its `create=False` find-only mode (the AUTOMATIC background
    self-heal's own mode — never auto-CREATE unattended). Returns the id on
    success, "" on any failure/missing owner-or-project — never raises."""
    if not owner or not project_label:
        return ""
    path = env_path if env_path is not None else _env_path()
    env = _read_env() if env is None else env
    key = _owner_project_key(owner, project_label)
    existing = (env.get(key) or "").strip()
    if existing:
        return existing
    new_id = find_owner_project_thread(env, owner, project_label, http=http)
    if not new_id and create:
        new_id = create_owner_project_thread(env, owner, project_label, http=http)
    if not new_id:
        return ""
    _env_upsert(path, key, new_id)
    return new_id


def _pthread_spawn_guard_path(owner, project_label):
    """Per (owner, project) spawn-throttle marker — a SEPARATE namespace
    from `_qthread_spawn_guard_path` (the questions-thread guard), so a
    burst of BOTH kinds of missing thread never shares one throttle window
    and one kind's self-heal cannot silently suppress the other's.

    #369 review m8 (TRIGGERED): `owner` reaches here from `send()`'s own
    per-TARGET loop, which includes MIRROR recipients
    (`DISCORD_MIRROR_<OWNER>`) — a locally-owner-controlled `.env` value
    `mirror_owners()` only lowercases, never path-sanitises. Unlike the
    questions-thread guard (never called with a mirror owner), this is a
    genuine new path-traversal-shaped surface: an owner value like
    `../../../tmp/x` would otherwise land outside `notify-pthread-spawn/`
    entirely. Sanitised the SAME way `_project_env_slug` already sanitises
    the project half, so both path components are safe."""
    safe_owner = re.sub(r"[^A-Za-z0-9_-]+", "_", str(owner or "")).strip("_") or "owner"
    return os.path.join(_claude_dir(), "notify-pthread-spawn",
                        safe_owner, _project_env_slug(project_label) or "project")


def _spawn_provision_project_thread(owner, project_label):
    """Kick a DETACHED, background `notify --provision-project-thread
    --find-only` for (owner, project) (#369) — mirrors
    `_spawn_provision_question_thread` verbatim: guarded by the SAME atomic
    `_qthread_spawn_claim` primitive (generic over its own guard PATH, so
    reusing it here needs no changes to it at all), find-only (never
    auto-CREATE unattended), and never raises into the live delivery path
    it rides alongside."""
    if not _qthread_spawn_claim(_pthread_spawn_guard_path(owner, project_label)):
        return
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
        "airuleset.py")
    try:
        subprocess.Popen(
            [sys.executable, script, "notify", "--provision-project-thread",
             "--owner-name", owner, "--project", project_label, "--find-only"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as exc:
        log_delivery("spawn-error", kind="project",
                     key="%s:%s" % (owner, project_label), reason=repr(exc))


def resolve_project_channel(env=None, owner=None, project=None, spawn=None):
    """The channel id a REAL delivery for `project` should POST to (#369) —
    mirrors `resolve_questions_channel` verbatim, one level over (a
    caller-supplied `project` instead of the fixed "questions" kind).

    Returns `notification_channel(..., project=project)`'s own value
    unconditionally — this never changes WHICH channel a fallback delivery
    lands in, only whether the fallback is silent. Only when the owner's
    per-project key is genuinely ABSENT does it additionally (1) write a
    LOUD, distinguishable delivery-log line (`status="fallback"`) and
    (2) kick a GUARDED, DETACHED, find-only background attempt to provision
    it — so the NEXT notification for this (owner, project) self-heals with
    no manual per-box step. `project` falsy (None/"") is a pure no-op: no
    log, no spawn, identical to `notification_channel()` with no project.
    Gated on `bot_token(env)` being present, exactly like
    `resolve_questions_channel`."""
    env = _read_env() if env is None else env
    owner = resolve_owner() if owner is None else owner
    chan = notification_channel(env=env, owner=owner, project=project)
    try:
        if owner and project and bot_token(env):
            configured = (env.get(_owner_project_key(owner, project)) or "").strip()
            if not configured:
                log_delivery("fallback", kind="project",
                             key="%s:%s" % (owner, project),
                             reason="p-thread-not-provisioned-on-this-host")
                (spawn if spawn is not None
                 else _spawn_provision_project_thread)(owner, project)
    except Exception as exc:
        print("resolve_project_channel: self-heal side effect failed: %r"
             % exc, file=sys.stderr)
    return chan


def mention_prefix(env=None, owner=None):
    """Return the Discord @mention prefix ('<@123> ') for the current owner, or ""
    when there is no owner or no mapping. The mapping lives in the .env as
    `DISCORD_MENTION_<OWNER>` (e.g. DISCORD_MENTION_ZBYNEK=123456789012345678).

    A bare numeric id is wrapped as <@id>; a value already shaped like a mention
    (<@…>, <@&role>, @here/@everyone) is used verbatim — so a role/group ping is
    possible without code changes."""
    env = _read_env() if env is None else env
    owner = resolve_owner() if owner is None else owner
    if not owner:
        return ""
    val = (env.get("DISCORD_MENTION_" + owner.upper()) or "").strip()
    if not val:
        return ""
    if re.fullmatch(r"\d{5,25}", val):
        val = "<@%s>" % val
    return val + " "


def mirror_owners(env=None, owner=None):
    """Owners to ALSO notify, IN PARALLEL, when a notification is for `owner` — the
    CC / supervisor recipients. Config lives in the .env as `DISCORD_MIRROR_<OWNER>`,
    a comma/space-separated list of other owner names — e.g.
    `DISCORD_MIRROR_DAVID=zbynek` makes every david notification ALSO land in
    zbynek's own thread with zbynek's @mention. Returns a de-duplicated list of
    lowercase owners, excluding the primary owner itself; empty when unset or the
    owner can't be determined. Fail-safe (never raises)."""
    env = _read_env() if env is None else env
    owner = resolve_owner() if owner is None else owner
    if not owner:
        return []
    raw = (env.get("DISCORD_MIRROR_" + owner.upper()) or "").strip()
    out, seen = [], {owner.lower()}
    for tok in re.split(r"[,\s]+", raw):
        t = tok.strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _plural_done(n):
    if n == 1:
        return "ticket dokončený"
    return "%d tickety dokončené" % n



def stream_qualified(name):
    """Append the box's unix user to a ping label for STREAM users
    (gatekeeper/montalu/david/marek) so the phone can tell which stream
    speaks; personal boxes (newlevel/root) keep the plain label. The shell
    hook + watchdog project_label carry the same rule (2026-07-20)."""
    import getpass
    try:
        u = getpass.getuser()
    except Exception:
        return name
    if u in ("newlevel", "root", ""):
        return name
    if str(name).endswith("-" + u):
        # already stream-qualified upstream (watchdog project_label appends
        # the same suffix on stream boxes) — never double it ("odoo-erp-
        # david-david" api-error alert, 2026-07-22)
        return name
    return "%s-%s" % (name, u)


def compose_autopilot_card(repo, tickets, pr=None, version=None, merge_sha=None,
                           review_ok=True, done=None, remaining=None, urls=None,
                           handoff=False, scope_label=None):
    """Build the canonical per-ticket completion card (Slovak, Discord markdown).

    `tickets` is a list of dicts {n, title, goal, achieved}. `urls` is a list of
    "where to SEE the change live" links — each a bare URL or "Label=URL" (e.g.
    "Money Gate stav=https://…/money-gate"). `pr` is accepted for call-compatibility
    but NOT rendered (the user wants the live view, not the code/diff). Structure is
    fixed here so every card is consistent regardless of who calls it. No @mention
    here — send() prepends it.

    `scope_label` (#164): a short word appended to the 📊 progress line stating
    WHICH POPULATION `remaining` counts (e.g. "core" on a full-authority box,
    once `remaining` is scoped to the core slice) — a bare "ostáva 72" reads as
    "remaining for this run" when it is really the whole repo; the footer has
    the same self-description problem and the same fix. None (default) keeps
    the line unlabeled — unchanged for every existing caller.

    `handoff=True` renders the FORK-NO-MERGE variant: the stream cannot merge or
    deploy, so instead of the 📦 "nasadené <version>" line it shows a 🔎 "odovzdané
    na review" status (locally verified, waiting for the gatekeeper to merge/close).
    Without this, a fork-no-merge worker had NO card shape that fit — the merge-only
    card never fired, so the user got no per-ticket evaluation at all (incident
    2026-07-10, david@gk / odoo-erp)."""
    tickets = tickets or []
    # Show only the repo NAME (last path segment), not "owner/name": the @mention
    # send() prepends already names the person, so an "owner/" prefix repeats it
    # (e.g. "@Zbynek Drlik … zbynekdrlik/bakerion-ai" said "zbynek" twice).
    repo_name = stream_qualified((_clean(repo) or "?").rstrip("/").split("/")[-1] or "?")
    n_tk = len(tickets) or 1
    header = ("%d ticket odovzdaný na review" % n_tk if handoff
              else _plural_done(n_tk))
    lines = ["🚀 **%s** — %s" % (repo_name, header)]
    for t in tickets:
        n = t.get("n")
        # Header is JUST the number — the issue title is technical/long and was
        # repeated verbatim in 🎯 Cieľ. 🎯 Cieľ / ✅ Dosiahnuté carry the worker's
        # PLAIN-language one-liners instead (simple, understandable).
        goal = _clean(t.get("goal"))[:_FIELD_CAP] or "—"
        achieved = _clean(t.get("achieved"))[:_FIELD_CAP] or "—"
        lines += ["", "🎫 **#%s**" % n, "> 🎯 **Cieľ:** %s" % goal,
                  "> ✅ **Dosiahnuté:** %s" % achieved]

    # (The "🔍 Double-review" line was removed at the user's request: a card only
    # ever fires on a CLEAN merge, so the line was always ✅ — pure repetition the
    # user does not need to re-read. `review_ok` is kept in the signature so the
    # worker's `--review` arg stays valid, but it no longer prints.)

    if handoff:
        # Fork-no-merge: nothing merged/deployed. The stream verified locally and
        # handed the branch to the gatekeeper — say exactly that (no 📦 version line).
        lines.append("🔎 **Odovzdané na review** — lokálne overené (testy/lint), "
                     "čaká na gatekeeper merge")
    else:
        # Deploy line leads with the DEPLOYED VERSION (the fact the user actually
        # wants — "which version went live?"). The PR number was removed at the
        # user's request (noise). `pr` is still accepted so callers don't break.
        deploy = []
        v = _clean(version)
        if v and v not in ("—", "-"):
            deploy.append("nasadené **%s**" % v)
        if merge_sha:
            deploy.append("`%s`" % _clean(str(merge_sha))[:12])
        lines.append("📦 " + (" · ".join(deploy) if deploy else "zmergnuté"))

    # 🔗 links — WHERE to SEE the change LIVE: the app's web page, or the specific
    # dashboard sub-page / route the change is visible on. NOT the PR/diff (the user
    # is not interested in the code link). Each `urls` entry is a bare URL (label
    # "pozri naživo") or "Label=URL" (e.g. "Money Gate stav=https://…/money-gate").
    links = []
    for raw in (urls or []):
        entry = _clean(raw)
        label, sep, url = entry.partition("=")
        if not sep:
            label, url = "pozri naživo", entry
        url = url.strip()
        if url.startswith("http"):
            links.append("[%s](%s)" % (label.strip() or "pozri naživo", url))
    if links:
        lines.append("🔗 " + " · ".join(links))

    if remaining is not None:
        try:
            rem = int(remaining)
        except (TypeError, ValueError):
            rem = remaining
        if done is not None:
            prog = "hotové %s · ostáva %s" % (done, rem)
        else:
            prog = "ostáva %s" % rem
        label = _clean(scope_label)
        if label:
            prog += " " + label
        tail = " (backlog prázdny 🎉)" if rem == 0 else ""
        lines.append("📊 **Autopilot:** %s%s" % (prog, tail))

    return "\n".join(lines)


# --- API-error detection (the CONCRETE stall signal) ---------------------
# Claude Code marks a real, user-facing API error with `isApiErrorMessage` in the
# session transcript and ends the turn on it, so the Stop hook's last assistant
# message IS the error text (e.g. "API Error: Server is temporarily limiting
# requests · Rate limited"). These are the genuine "work stopped" events — NOT a
# board-silence guess — so notifying on them never false-positives.
# A turn that ENDS on a CC API error leads with "API Error:" — the strongest,
# safest signal (won't match an agent's normal prose).
_API_ERROR_LEAD = re.compile(r"^\s*(api\s+error|claude\s+api\s+error)\b", re.IGNORECASE)
# Specific CC error phrases for the rarer cases that don't lead with "API Error".
# Deliberately precise — NOT a bare "rate limit"/"overloaded" substring, which
# appears in normal dev talk ("fix the rate limiter config") and false-positived.
_API_ERROR_PHRASE = re.compile(
    r"(temporarily limiting requests"
    r"|socket connection was closed unexpectedly"
    r"|issue with the selected model"
    r"|usage limit (reached|exceeded)"
    r"|rate[ -]?limited\b"
    r"|internal server error"
    r"|service unavailable)",
    re.IGNORECASE)
# A bare "529"/"502" in prose ("the 529 did nothing") is NOT an error — a real CC
# 529 leads with "API Error:" (the LEAD) or says "Overloaded", so the bare-number
# alternative was removed (it false-pinged an agent's own status update).

# Status markers prove the text is the agent's OWN message (it narrated a past
# error inside a ⏳/✅/❓ update) — a genuine api error ABORTS the turn, so its
# last_assistant_message is the bare banner with NO status marker.
_AGENT_STATUS_RX = re.compile(r"⏳|✅|❓|NEEDS YOU|\bWORKING:|\bDONE:")


def is_api_error(text):
    """True if `text` (a turn's final assistant message) is a real Claude Code API
    error that stopped the work — the concrete signal the notifier keys on. Precise
    on purpose: a normal message that merely MENTIONS '529' / 'rate limiter' / a
    status marker is NOT an error (the false positives that produced spam)."""
    if not text:
        return False
    t = str(text).strip()
    if len(t) > 600:                 # CC API-error lines are short; long = normal prose
        return False
    if _AGENT_STATUS_RX.search(t):   # the agent's own ⏳/✅/❓ status update, not an error
        return False
    return bool(_API_ERROR_LEAD.match(t) or _API_ERROR_PHRASE.search(t))


def compose_api_error_alert(project, text):
    """Build the API-error ping (Slovak, Discord markdown) from the ACTUAL error
    text Claude Code surfaced. No @mention here — send() prepends it."""
    proj = stream_qualified((_clean(project) or "?").rstrip("/").split("/")[-1] or "?")
    err = _clean(text)[:300] or "neznáma API chyba"
    return ("🛑 **%s** — API chyba, práca sa zastavila\n> %s\n"
            "> Agent sa zasekol na API chybe — pozri sa naň / skús znova."
            % (proj, err))


def compose_oauth_block_alert(project, loc, nudges):
    """#662 — the PERSISTENT interactive-/login escape-valve alert (Slovak,
    Discord markdown). Fired by watchdog job 1 ONLY when an error needing an
    interactive `/login` (`_needs_interactive_login`: a genuine
    `access token has been revoked`, or a "Login expired"/"Not logged in ·
    Please run /login") survived every one of the `nudges` automatic resume
    attempts — i.e. the #602 self-heal (fresh token on disk) provably did NOT
    land, so this is a non-self-healing AUTH block needing a MANUAL `/login`.
    Keyed `oauthblock:`. #662 kept this OUTSIDE the #546 apierr family (like
    `acctblock:`) so it would never be swallowed; #676 (owner ruling
    2026-08-24) REVERSED that — a 401 OAuth-revoke is NORMAL subscription-
    switching by the Claude project + watchers, not an incident, so the
    `oauthblock:` class is now in `SUPPRESSED_ALERT_PREFIXES`: this body is
    still COMPOSED and passed to send(), but send() suppresses the Discord PING
    (the machine channel keeps the signal — watchdog journal + the `suppressed`
    delivery-log line; the silent auto-resume is untouched). `loc` names the
    session/pane. No @mention here — send() prepends it."""
    proj = stream_qualified((_clean(project) or "?").rstrip("/").split("/")[-1] or "?")
    where = _clean(loc) or "?"
    return ("⛔ **%s** — session (%s) potrebuje manuálny `/login` (odvolaný token "
            "alebo vypršané prihlásenie) a automatické obnovenie ZLYHALO — po "
            "%d× automatickom pokuse o obnovu sa stále nevie prihlásiť. Toto "
            "NEMÁ automatický reset: prihlás sa prosím v tejto session, inak "
            "coverage tejto slučky ďalej stojí." % (proj, where, int(nudges)))


def compose_stuck_owner_alert(project, loc, sweeps):
    """#662 — the STRUCTURAL persistent-stuck owner alert (Slovak, Discord
    markdown). Fired by the goal lane sweep when one_glance's `stuck` verdict
    (armed /goal + 0 workers + backlog waiting + idle over threshold) held for
    `sweeps` consecutive ~1-min sweeps — long enough that the session did NOT
    revive despite the bounded keystroke lane-nudge recovery (a dead /
    login-dialog-covered session a `continue` cannot bring back). Keyed
    `stuckalert:`. #688 (owner ruling 2026-08-25) added that key to
    `SUPPRESSED_ALERT_PREFIXES` — the structural `stuck` verdict is a heuristic
    that fires on many non-human-needed states, so this body is still COMPOSED
    and passed to send(), but send() suppresses the Discord PING (the machine
    channel keeps the signal — watchdog journal + the `suppressed` delivery-log
    line; the keystroke auto-recovery is untouched), exactly like the sibling
    `oauthblock:` alarm (#676). No @mention here — send() prepends it."""
    proj = stream_qualified((_clean(project) or "?").rstrip("/").split("/")[-1] or "?")
    where = _clean(loc) or "?"
    return ("⛔ **%s** — /goal slučka ZAMRZLA: %d× po sebe (~1 min/kontrola) "
            "0 workerov, tikety čakajú a session sa nehýbe (%s). Sama sa "
            "neoživila — pravdepodobne treba manuálny zásah (napr. `/login`, "
            "reštart session alebo zatvorenie dialógu). Coverage tejto slučky "
            "stojí, pozri sa na ňu prosím." % (proj, int(sweeps), where))


# --- dedup ---------------------------------------------------------------
def _dedup_dir():
    return os.path.join(_claude_dir(), _DEDUP_DIRNAME)


def _dedup_path(key):
    """The marker FILE for `key` — always a single, filesystem-safe filename.

    `key` is sanitised to `[A-Za-z0-9._#-]` first, same as ever. When that
    sanitised form would exceed `_DEDUP_NAME_MAX` bytes, the RAW key is
    hashed instead (#359): `os.open(..., O_CREAT | O_EXCL)` in
    `_dedup_claim` raises `OSError` (`ENAMETOOLONG`) once a filename crosses
    the filesystem's real NAME_MAX (~255 bytes), and `_dedup_claim`'s own
    deliberate `except OSError: return True` fail-open then turns THAT into
    "first attempt, send it" on EVERY call for that key — the marker never
    reaches disk, so dedup silently never happens for it. Confirmed to be a
    LIVE, currently-reachable trigger, not just a theoretical one:
    `card-unreported`'s own dedup_key joins the FULL `pingable` list
    (unlike its `shown`/`more` DISPLAY text, which `CARD_MAX_LISTED`
    truncates) — a repo with ~40+ unreported closed tickets already crosses
    this module's own threshold. A sha256 hex digest is itself pure
    `[a-f0-9]`, so it needs no further sanitisation. The `long-` prefix
    could in principle collide with a SHORT key that is *itself* literally
    spelled `long-<64 lowercase hex chars>` — no real caller in this repo
    composes a key anywhere near that shape, so this is unreachable in
    practice, not impossible in the abstract. Every existing short key (the
    overwhelming majority — a `<repo>#<issue>` card, a session/pid-keyed
    watchdog key) resolves to EXACTLY the same path it always has; only a
    key long enough to be unsafe on disk in the first place is affected. A
    key that used to sanitise into the (181..255)-byte window worked fine
    before this fix (a valid, if fragile, on-disk marker) and gets a NEW
    hashed path after it — the old marker is orphaned, costing at most one
    duplicate notification within its dedup window before the new hashed
    marker takes over; self-healing, never a permanent loss of dedup."""
    safe = re.sub(r"[^A-Za-z0-9._#-]", "_", str(key))
    if len(safe) > _DEDUP_NAME_MAX:
        digest = hashlib.sha256(str(key).encode("utf-8", "replace")).hexdigest()
        safe = "long-%s" % digest
    return os.path.join(_dedup_dir(), safe)


def _dedup_claim(key):
    """Atomically claim `key`. Returns True if THIS call is the first (send it),
    False if it was already sent (skip — dedup hit). Best-effort: any error claims
    (better a possible double-send than dropping the user's requested message)."""
    if not key:
        return True
    d = _dedup_dir()
    try:
        os.makedirs(d, exist_ok=True)
        _prune_dedup(d)
        fd = os.open(_dedup_path(key), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(time.time()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return True


def _dedup_mark_status(key, status):
    """Record the TERMINAL delivery status in the marker (#135).

    `_dedup_claim` writes the marker BEFORE the POST — it has to, or a racing
    duplicate could double-post — so marker PRESENCE proves a claim, never a
    delivery. Writing the outcome afterwards is what turns the marker into an
    artifact the #134 gate / backstop / suppression can key on. Best-effort:
    a marker that cannot be updated keeps its claim and simply reads as
    legacy (see `marker_delivered`). Returns whether the marker really
    reached disk, so a caller counting artifacts counts writes that
    happened rather than writes attempted."""
    if not key:
        return False
    try:
        with open(_dedup_path(key), "w", encoding="utf-8") as fh:
            fh.write("%s %s" % (time.time(), status))
    except OSError:
        return False
    return True


def marker_delivered(key):
    """True when `key`'s marker records a message that actually REACHED
    Discord.

    Three states, deliberately distinguished:
      * no marker              → False (nothing was ever claimed)
      * marker says 'sent'     → True
      * marker says anything else that is a KNOWN failure → False

    A LEGACY marker (a bare timestamp, written before #135) reads as
    DELIVERED. Every marker on every managed box predates this change, and
    reading them as undelivered would make the whole existing history look
    failed and flood the user with false alerts on the first deploy."""
    if not key:
        return False
    try:
        with open(_dedup_path(key), encoding="utf-8") as fh:
            body = fh.read(200).strip()
    except OSError:
        return False
    parts = body.split()
    if len(parts) < 2:
        return True                  # legacy: timestamp only
    return parts[1] == "sent"


def backfill_marker_key(name, number):
    """The marker key a DIGEST writes for one ticket it accounted for.

    Deliberately a SEPARATE namespace from the per-ticket card key
    (`<repo>#<n>`): that one means "this ticket got its OWN delivered card"
    and is read by `subagent-stop-check-run-card.sh` and
    `newest_delivered_card`, so writing it here would let a worker's
    genuinely missing card pass the gate. Two facts, two namespaces.

    `#` survives `_dedup_path`'s sanitisation while `:` is rewritten to `_`
    (so a `backfill:<name>` key could collide with a repo literally named
    `backfill_<name>`), and no GitHub repo name may contain `#` — hence a
    card key for any real repo has exactly one `#` and this one has two.
    `name` is sanitised to the card alphabet because it does NOT arrive
    validated: `repo_name_for` parses whatever a remote URL ends with, and
    `--repo` is split by hand. That keeps the digest side from ever being
    the one that forges a collision."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(name))
    return "backfill#%s#%s" % (safe, number)


def mark_backfill_reported(name, numbers, status):
    """Record that a DELIVERED catch-up digest accounted for `numbers`.
    Returns how many markers were written.

    Writes ONLY when `status` is the literal 'sent' — `send()`'s return
    value AFTER its POST, i.e. proof the message reached Discord. This is
    the #134 constraint in code: a suppression must key on the ARTIFACT the
    action leaves behind, never on the intent to act, or it becomes a
    silence generator. 'dry-run', 'dedup', 'no-config' and 'error' all write
    nothing, so a digest that never arrived leaves job 25 flagging every one
    of its tickets on the next sweep. The rule lives HERE rather than at the
    call site precisely so no caller can lose it by forgetting a branch.

    Best-effort in the fail-OPEN direction: a marker that cannot be written
    simply is not written, and the ticket stays reported as missing. The
    count is therefore writes that HAPPENED, never writes attempted — this
    is the one function whose whole premise is honesty about artifacts, so
    reporting N while a read-only filesystem took none would be the same
    class of lie it exists to prevent."""
    if status != "sent":
        return 0
    try:
        os.makedirs(_dedup_dir(), exist_ok=True)
    except OSError:
        return 0
    return sum(1 for n in numbers
               if _dedup_mark_status(backfill_marker_key(name, n), status))


def run_card_refused_key(name, issue, fp):
    """The marker key for a TERMINAL content-refusal of a run-card (#474).

    A content refusal (an empty/generic --goal or --achieved that is
    deterministic on the CLI args ALONE) will be refused again on every
    byte-identical retry — so the FIRST such refusal writes this marker and
    a later identical retry short-circuits: no re-verify, no wasted `gh
    issue view` fetch, no new durable log line (the 3502x/33h `x#457` spam
    this fixes).

    A DISTINCT namespace from BOTH the per-ticket card key (`<repo>#<n>`,
    exactly one `#`, read by the run-card gate/backstop) and the backfill
    key (`backfill#<name>#<n>`): this one starts with `refused` and carries
    a CONTENT fingerprint, so a genuinely FIXED card (a different
    fingerprint) is never suppressed by the old refusal's marker. `#`
    survives `_dedup_path`'s sanitisation, so the parts stay distinct.
    `name` is sanitised to the card alphabet because it does NOT arrive
    validated (`repo_name_for`/`--repo` split by hand), matching the SAME
    discipline `backfill_marker_key` uses."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(name))
    return "refused#%s#%s#%s" % (safe, issue, fp)


def mark_run_card_content_refused(name, issue, fp, reason=""):
    """Record that a run-card for (name, issue) was TERMINALLY refused for a
    content reason with fingerprint `fp` (#474).

    Best-effort in the fail-OPEN direction: a marker that cannot be written
    simply is not written, so the next identical retry re-refuses and
    re-logs once — never a false-suppress of a genuine card. Returns whether
    the marker reached disk. `reason` is a short CLASSIFICATION only (never
    the raw worker-authored --goal/--achieved text — the marker store is a
    second place that content could come to rest, #157's lesson)."""
    if not name or issue is None or not fp:
        return False
    try:
        os.makedirs(_dedup_dir(), exist_ok=True)
    except OSError:
        return False
    return _dedup_mark_status(run_card_refused_key(name, issue, fp),
                              "refused:%s" % (reason or "content"))


def run_card_content_refused(name, issue, fp):
    """True when (name, issue) already carries a TERMINAL content-refusal
    marker for fingerprint `fp` (#474) — so the caller short-circuits before
    any gh fetch or new log line.

    A missing/unreadable marker reads as NOT-refused (fail-open: re-evaluate
    the card rather than suppress it), the SAME safe direction as
    `_dedup_claim`'s own error handling."""
    if not name or issue is None or not fp:
        return False
    return os.path.exists(_dedup_path(run_card_refused_key(name, issue, fp)))


# --- the autopilot worker's evidence block (#134) --------------------------
# Two facts below come from reading 339 REAL evidence blocks (extracted from
# 5,180 subagent transcripts), not from the template in
# `agents/autopilot-worker.md` — a parser written against the template alone
# is wrong on both:
#
#   * `merge_sha:` is frequently NOT a sha. Real values include
#     "NOT MERGED — dispatch = STOP-at-green-PR (autorita FULL, ...)" and
#     "STOPPED: DYNAX eshop in maintenance (HTTP 503) — hard blocker".
#     Requiring the value to START with hex is what keeps every downstream
#     check off a worker that correctly did not merge.
#   * `issue_state:` mixes closed and open in one line, with or without the
#     `=`, in either case, and its parentheticals mention OTHER issue
#     numbers: "#109=closed (auto-closed by Closes #109 at 15:18:14Z on the
#     #133 merge)". A scan for "any #N near the word closed" claims #133,
#     which this worker never closed. The state word must sit directly
#     against its own #N.
# A merge is claimed when the value STARTS with a sha, or when a sha sits
# right behind an issue number — the batch shape, which the corpus replay
# caught the first rule missing on 6 real merges:
#     merge_sha: #133 = 2223f12 (feature) ; #134 = 4d56813 (docs)
#     merge_sha: #82 test:3c280b2[red]→fix:246dc57[green]; #81 …
# Measured over all 339 blocks: 213/213 genuine merges detected, 0 false
# positives. The rejected alternative scored identically but decided by the
# ABSENCE of a negative-marker list ("NOT MERGED", "n/a", "STOPPED", …) —
# safety resting on an enumeration, so a new phrasing that happened to carry
# a sha would make the gate demand a card for a ticket that never merged.
# Requiring a sha ATTACHED TO AN ISSUE NUMBER is positive evidence instead,
# and needs no list to stay correct.
_MERGE_SHA_LINE_RE = re.compile(r"^\s*merge_sha\s*:(.*)$", re.I | re.M)
_SHA_HEAD_RE = re.compile(r"^\s*[0-9a-f]{7,40}(?![0-9a-z])")
_SHA_PER_ISSUE_RE = re.compile(r"#\d+.{0,12}?\b[0-9a-f]{7,40}\b")
_ISSUE_STATE_LINE_RE = re.compile(r"^\s*issue_state\s*:(.*)$", re.I | re.M)
_CLOSED_RE = re.compile(r"#(\d+)\s*=?\s*(closed|open)\b", re.I)


def parse_worker_evidence(text):
    """What an autopilot worker's FINAL MESSAGE claims.

    Returns `{"merged": bool, "closed": [int, ...]}` — the issues it says it
    CLOSED via a real merge. Never raises; anything unrecognised yields the
    inert answer, because every consumer of this treats "cannot tell" as
    "say nothing" rather than as a finding."""
    if not isinstance(text, str) or not text:
        return {"merged": False, "closed": []}
    merged = any(_SHA_HEAD_RE.match(m.group(1))
                 or _SHA_PER_ISSUE_RE.search(m.group(1))
                 for m in _MERGE_SHA_LINE_RE.finditer(text))
    closed, seen = [], set()
    for m in _ISSUE_STATE_LINE_RE.finditer(text):
        for num, state in _CLOSED_RE.findall(m.group(1)):
            if state.lower() != "closed":
                continue
            n = int(num)
            if n not in seen:
                seen.add(n)
                closed.append(n)
    return {"merged": merged, "closed": closed}


def repo_name_for(cwd, run=None):
    """The GitHub repo NAME for `cwd`, from its `origin` remote — never the
    directory basename.

    The live trap this exists for: marek's checkout is
    `~/devel/forestshop/parovanie_produktov` (underscore) while every card
    marker is keyed `parovanie-produktov` (hyphen), because
    `_notify_run_card` keys on the `--repo` argument. A directory-derived key
    matches nothing and would make every ticket look unreported.

    Returns "" when it cannot be determined — unmeasurable, never a guess."""
    import subprocess
    argv = ["git", "-C", str(cwd), "remote", "get-url", "origin"]
    try:
        if run is not None:
            out = run(argv)
        else:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=6)
            out = r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return ""
    if not out:
        return ""
    url = out.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url.replace(":", "/").split("/")[-1]


# --------------------------------------------------------------------------- #
# #187 -- repo resolution when a command's own text moves the shell to a
# DIFFERENT directory than the PreToolUse payload's static `cwd`.
#
# THE PROBLEM. `repo_name_for(cwd)` trusts the payload's `.cwd` field, fixed
# at dispatch time. A worker explicitly dispatched to operate on a sibling
# checkout (the documented "REPO PATH is different from session cwd" shape
# in agents/autopilot-worker.md) runs every Bash call as `cd <path> &&
# <command>` -- the payload's cwd never reflects that `cd`, so a hook that
# keys off it alone gates against the WRONG repo's marker namespace.
#
# THE FIX. Trust a `cd <path> &&`/`cd <path>;` ONLY when it is the very
# FIRST STATEMENT of the whole command (anchored at string position 0), and
# only when that path resolves to a real git repo with an `origin` remote
# -- never a bare guess. Falls back to `cwd` unchanged otherwise, so a
# command with no such prefix (the overwhelming majority) sees zero
# behavior change.
#
# ADVERSARIAL-REVIEW HISTORY (both against the SAME earlier draft, which
# scanned for a `cd` preceded by ANY `;`/`&`/`|` ANYWHERE in the text, with
# quoted spans stripped first):
#   CRITICAL -- a `cd /path && ...` literal sitting inside a HEREDOC BODY
#   (not a quoted span -- quote-stripping cannot see it) was misread as a
#   real statement boundary. Reachable via ordinary commit-message prose
#   (e.g. quoting a runbook recipe), and because `design_gate.required_refs`
#   ALSO uses the resolved directory for the #206 gh-issue-state exemption,
#   this could silently DISABLE the design gate if the spoofed repo happens
#   to have the same issue number already closed.
#   WARNING -- the quote-stripping itself regressed the LEGITIMATE case: a
#   `cd "/path with spaces"` no longer resolved at all, since the quoted
#   path was blanked along with the injection vector it defended against.
#
# THE ANCHOR FIX. Requiring `cd` to be the command's own FIRST token makes
# both findings structurally impossible at once: nothing (a heredoc body, a
# quoted argument, an earlier statement) can ever appear before position 0,
# so no scan-anywhere heuristic is needed, and no quote-stripping is needed
# either -- a quoted `cd` ARGUMENT is now parsed directly instead of being
# destroyed. This also tracks real bash semantics exactly: if the command
# genuinely begins with `cd X &&`, bash really does execute that cd first.
#
# KNOWN, ACCEPTED GAPS (documented, not fixed here -- same coverage limit
# `resolve_work_cwd` already had before this ticket, never a NEW one):
# `cd X<newline>git commit` (multi-line), `(cd X && git commit)` (subshell),
# and `git -C X commit` (no `cd` at all) are not detected -- each falls
# back to `cwd` unchanged, the safe default. Extending coverage to those
# shapes needs a deliberately more permissive parse, which trades directly
# against the attack surface this fix just closed -- a decision for a
# follow-up, not bundled into a security fix.
# --------------------------------------------------------------------------- #

_STMT_BOUNDARY = r"(?:^|[;&|]|&&)"
_GIT_COMMIT_CMD_RE = re.compile(
    _STMT_BOUNDARY + r"\s*(?:sudo\s+|env\s+)?git\s+commit\b")
_CD_PREFIX_RE = re.compile(
    r"""^\s*cd\s+(?:"([^"]*)"|'([^']*)'|(\S+))\s*(?:&&|;)""")
# #436-review MINOR (fresh-context adversarial review, live-triggered): a
# SECOND top-level `cd` statement anywhere after the first one means `cmd`
# is a compound command with MORE THAN ONE cd-scoped segment (this became
# practically reachable only once `post-record-design-comment.sh` started
# passing commands built from #208's own multi-`gh issue comment`-invocation
# support -- `cd /B && gh issue comment 5 ...; cd /A && gh issue comment 7
# ...` -- something `block-commit-without-design.sh`'s single-`git commit`
# caller never produces). Trusting only the FIRST `cd` there silently
# resolves EVERY invocation to the WRONG repo for every segment after the
# first, rather than the safe "fall back to cwd" a genuinely ambiguous
# command deserves. `_LATER_CD_RE` matches ANY later `cd` starting a fresh
# top-level statement; finding one refuses the whole resolution (falls back
# to `cwd`, same as having no `cd` at all) rather than guessing which
# invocation the trusted first `cd` was actually meant for.
_LATER_CD_RE = re.compile(r"(?:&&|;|\|)\s*cd\s+")


def resolve_work_cwd(cmd, cwd, run=None, trigger_re=None):
    """The directory whose repo `cmd` actually operates on -- a `cd <path>
    &&`/`cd <path>;` that is the very FIRST statement of `cmd` overrides
    `cwd` (#187) when, and only when, that path is a real, resolvable git
    repo AND `cmd` contains a TRIGGERING invocation somewhere -- `git
    commit` by default (`block-commit-without-design.sh`'s own use case),
    or `trigger_re` when a DIFFERENT caller needs a different anchor (#436
    -- `post-record-design-comment.sh` passes its own `gh issue comment`
    trigger, since its whole command shape never contains `git commit` at
    all). Never guesses: no leading `cd`, no trigger match anywhere in
    `cmd`, a `cd` target that isn't a git repo, OR a SECOND top-level `cd`
    statement anywhere later in `cmd` (#436-review -- an ambiguous
    multi-segment command is refused, never resolved from only the first
    segment's `cd`) all fall through to `cwd` unchanged. Anchoring to the
    command's own start (not "a cd preceded by any boundary character
    anywhere") is deliberate -- see the module comment for the
    adversarial-review history that shape closes; a custom `trigger_re`
    changes ONLY which command this function bothers to resolve for -- the
    SAME anchored, injection-hardened `_CD_PREFIX_RE.match()` (string-start
    only) runs for every caller, unchanged."""
    trigger = trigger_re if trigger_re is not None else _GIT_COMMIT_CMD_RE
    if isinstance(cmd, str) and cmd and trigger.search(cmd):
        m = _CD_PREFIX_RE.match(cmd)
        if m and not _LATER_CD_RE.search(cmd, m.end()):
            path = m.group(1) or m.group(2) or m.group(3) or ""
            path = os.path.expanduser(path)
            if path and path != cwd and repo_name_for(path, run=run):
                return path
    return cwd


# --------------------------------------------------------------------------- #
# #220 -- the SubagentStop-side counterpart of #187 above: neither
# subagent-stop-check-design.sh nor subagent-stop-check-run-card.sh has a
# command to scan a `cd` prefix out of (SubagentStop only ever sees `cwd`
# and the worker's own final message, never the commands it ran along the
# way). The worker evidence-block template already REQUIRES a
# `pr: #<N> <url>` line for every issue it merged -- by the time SubagentStop
# fires, that URL is the ground truth for which repo the work actually
# landed in, unlike the payload's static, dispatch-time cwd.
# --------------------------------------------------------------------------- #

_PR_LINE_RE = re.compile(r"^\s*pr\s*:(.*)$", re.I | re.M)
# Adversarial-review finding (WARNING 4): a repo-ROOT URL (no /pull/N path
# segment following it -- e.g. parenthesised, comma-separated, or ending a
# sentence) had its terminator absorb the trailing punctuation into the
# captured repo name (`dantesync)`, `dantesync,`, `dantesync.`), which
# false-blocked a compliant worker whose card was genuinely delivered
# under the clean key. The terminator set now also stops on common prose
# punctuation around a bare URL, not just a path separator/whitespace/EOL.
_GH_REPO_URL_RE = re.compile(
    r"github\.com[:/]([^/\s'\"]+)/([^/\s'\"]+?)(?:\.git)?"
    r"(?:[/#?).,\]},;:!`]|\s|$)")


def repo_from_pr_line(text):
    """The repo named by an evidence block's own `pr: #<N> <url>` line's
    GitHub URL -- "" when there is no such line, or no GitHub URL in it.
    Never guesses beyond what the URL itself says."""
    if not isinstance(text, str) or not text:
        return ""
    for m in _PR_LINE_RE.finditer(text):
        gm = _GH_REPO_URL_RE.search(m.group(1))
        if gm:
            return gm.group(2)
    return ""


def resolve_repo_key(cwd, msg=None, run=None):
    """The repo key a SubagentStop hook should use -- preferring the
    evidence block's own `pr:` line (#220) over the payload's static `cwd`
    when `msg` carries one. Falls back to `repo_name_for(cwd)` unchanged
    for a worker with no `pr:` line (a genuinely unmerged report, or one
    whose session cwd already matches the work repo)."""
    if msg:
        r = repo_from_pr_line(msg)
        if r:
            return r
    return repo_name_for(cwd, run=run)


def newest_delivered_card(repo_name):
    """mtime of the newest DELIVERED per-ticket card marker for `repo_name`,
    or None. The marker key is `<repo-name>#<issue>` (`_notify_run_card`), so
    the prefix scan is exact rather than a guess."""
    if not repo_name:
        return None
    prefix = re.sub(r"[^A-Za-z0-9._#-]", "_", str(repo_name)) + "#"
    d = _dedup_dir()
    newest = None
    try:
        names = os.listdir(d)
    except OSError:
        return None
    for name in names:
        if not name.startswith(prefix) or not name[len(prefix):].isdigit():
            continue
        if not marker_delivered(name):
            continue
        try:
            ts = os.path.getmtime(os.path.join(d, name))
        except OSError:
            continue
        if newest is None or ts > newest:
            newest = ts
    return newest


def card_marker_numbers(name):
    """Every issue NUMBER that has a run-card marker for repo `name` — the
    same `<name>#<digits>` prefix `newest_delivered_card` scans, returning
    the set of numbers rather than a single mtime (#182). A marker's
    DELIVERED status is not checked here: a caller deciding whether to clear
    a STALE marker (one from a ticket that has since REOPENED) needs to know
    it exists at all, delivered or merely claimed — a claimed-but-failed
    marker for a reopened ticket is exactly as stale as a delivered one."""
    if not name:
        return set()
    prefix = re.sub(r"[^A-Za-z0-9._#-]", "_", str(name)) + "#"
    d = _dedup_dir()
    out = set()
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for fname in names:
        if not fname.startswith(prefix):
            continue
        tail = fname[len(prefix):]
        if tail.isdigit():
            out.add(int(tail))
    return out


def forget_marker(key):
    """Drop a marker so its issue's NEXT card claims fresh (#182: a
    REOPENED ticket's second fix must not stay deduped against its FIRST
    close's card forever). Thin, PUBLIC wrapper over `_dedup_release` — the
    exact same best-effort delete `send()` already uses when a claim
    provably never sent."""
    _dedup_release(key)


def _dedup_release(key):
    """Drop a claim so a FAILED send can be retried (a network error must not
    permanently suppress the user's requested card)."""
    if not key:
        return
    try:
        os.remove(_dedup_path(key))
    except OSError:
        pass


def _prune_dedup(d):
    now = time.time()
    try:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            try:
                if now - os.path.getmtime(p) > _DEDUP_TTL_S:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


# --- outstanding-question map (Discord reply → the Claude session that asked) --
# When a ❓ ping is delivered, we record which SESSION asked it, keyed by the
# Discord message id of the ping. The user answers by REPLYING to that ping in
# Discord; the watchdog looks the referenced message id up here and types the
# answer into that exact session's tmux pane. Machine-LOCAL (each machine only
# answers questions IT asked): the file lives in ~/.claude, never git, never a
# secret. Bounded (the hard cap below) so a never-answered question can't
# accumulate without limit — but #368 (2026-08-12): an entry is NO LONGER
# deleted merely for being OLD. A still-unanswered question must survive
# until it is genuinely resolved (a routed Discord reply, or a HUMAN prompt
# at the terminal — job 7 / prune_answered_questions) so that
# watchdog.reping_stale_questions() has something to keep RE-ASKING daily —
# the user's own directive (2026-08-11): "ak sa claude rozhodne ich skryt tak
# ich aj tyzdne nepolozi. Chcem aby kazdy claude ak eviduje otazku minimalne
# raz denne ju nanovo polozil." Silently DELETING an unanswered entry after
# 24h was the exact inversion of that.
_QUESTIONS_REL = "discord-questions.json"
_QUESTIONS_MAX = 200                  # hard cap on tracked questions (newest kept)
_QUESTION_TEXT_MAX = 1200             # stored (collapsed, single-line) question
                                      # text cap (codepoints) — compose_reply_prompt
                                      # types this as ONE typed prompt line
_QUESTION_BLOCK_MAX = 1800            # stored RAW block cap (codepoints, #368) — the
                                      # newline-preserving posted content, resent
                                      # VERBATIM by a daily re-ask; comfortably under
                                      # `_MAX_CONTENT` (1900) so a fresh mention prefix
                                      # still fits when send() re-composes it


def _questions_path():
    return os.path.join(_claude_dir(), _QUESTIONS_REL)


def load_questions(path=None):
    """The message-id → {session, cwd, channel, ts} map. {} on any error."""
    path = path or _questions_path()
    try:
        with open(path, encoding="utf-8") as h:
            d = json.load(h)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_questions(d, path=None):
    path = path or _questions_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as h:
            json.dump(d, h)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def ask_generation(rec):
    """The ASK GENERATION of a question-map entry — when the question was
    originally ASKED (`asked`, preserved verbatim across daily re-tracks by
    reping_stale_questions), falling back to the record `ts` for a legacy
    entry written before the field existed (its ts IS its ask time — only
    a re-track ever separates the two). 0 for anything unreadable
    (non-dict, bool/garbage timestamps — the isinstance(True, int) trap),
    which reads as "oldest", the safe direction for every consumer: the
    supersede and the collapse both keep the NEWEST generation (#407
    review: comparing record time instead inverted the ask order the
    moment a stale sibling's re-post landed on a live question's
    channel)."""
    if not isinstance(rec, dict):
        return 0
    for key in ("asked", "ts"):
        v = rec.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
    return 0


def record_question(message_id, channel, session, cwd, now=None, path=None,
                    question="", asked_ts=None, grace_path=None):
    """Record that Discord message `message_id` (in `channel`) is the ❓ ping for
    `session` (transcript stem = CC session id) in `cwd`. Prunes malformed +
    over-cap entries in the same write (#368: no longer AGE — see the module
    comment above the map's own constants). Returns True on success.
    Fail-safe (never raises).

    `question` = the posted ❓ text, stored TWO ways:
      - `question`: single-line (collapsed whitespace, leading @mentions
        stripped, codepoint-capped) so the watchdog's reply delivery can wrap
        the user's answer with the question it answers — a bare '1' typed
        hours/days later is meaningless once the session's context no longer
        holds the question (user ask, 2026-07-17). This is a ONE-LINE value
        BY DESIGN: `compose_reply_prompt` types it as a single typed prompt
        line, where a stray newline would submit early.
      - `block` (#368): the SAME text, mention-stripped and codepoint-capped,
        but with its ORIGINAL newlines PRESERVED — the full posted CONTENT
        already reaches here via stdin (hooks/notify-discord-send.sh pipes
        the exact `$CONTENT` it POSTed), so this is genuinely the whole
        rendered block (bold headers, numbered options, blank-line
        paragraphs), not a reconstruction. `watchdog.reping_stale_questions()`
        reposts THIS verbatim for the daily re-ask, never the flattened
        `question` — resending a wall of prose with no formatting would
        reintroduce the exact "ziadne odrazky, ziadne zvyraznenia" complaint
        `clean_q` was built to fix.

    `message_id` and `channel` must be NUMERIC Discord snowflakes — anything else
    (a Mock repr from a mis-wired test, a mangled shell var) is refused so garbage
    can never pollute the live map (real incident: Mock strings landed in
    ~/.claude/discord-questions.json, 2026-07-04).

    `asked_ts` (#407 review): the ASK GENERATION this record belongs to.
    Omitted (a genuinely NEW ask — the hook path) it defaults to `now`;
    reping_stale_questions passes the OLD entry's own generation on a
    daily re-track, so a re-posted old question keeps reading as the OLD
    ask to the supersede below and to the sweep collapse — never as a
    fresh one that could displace a live, newer question."""
    message_id = str(message_id or "").strip()
    channel = str(channel or "").strip()
    session = str(session or "").strip()
    if not message_id.isdigit() or not channel.isdigit() or not session:
        return False
    now = time.time() if now is None else now
    if isinstance(asked_ts, bool) or not isinstance(asked_ts, (int, float)):
        asked = int(now)
    else:
        asked = int(asked_ts)
    q = " ".join(str(question or "").split())
    q = re.sub(r"^(?:<@[!&]?\d+>\s*)+", "", q)[:_QUESTION_TEXT_MAX]
    block = re.sub(r"^(?:<@[!&]?\d+>\s*)+", "",
                   str(question or "").strip())[:_QUESTION_BLOCK_MAX]
    d = load_questions(path)
    d[message_id] = {"session": session, "cwd": str(cwd or ""),
                     "channel": str(channel or ""), "ts": int(now),
                     "asked": asked, "question": q, "block": block}
    # #407: SUPERSEDE — the newest ASK per (session, channel) is the ONLY
    # tracked entry. A reworded ❓ past _EDIT_WINDOW_S cannot EDIT the old
    # Discord card any more (update_question refuses purely on age), so the
    # pending hook falls through to a fresh POST + a fresh record here —
    # before this, the OLD entry stayed tracked forever (no age TTL since
    # #368) and reping_stale_questions re-pinged BOTH daily: an immortal
    # "ghost". Dropping every same-(session, channel) entry of an OLDER-OR-
    # EQUAL ask generation at record time makes the ghost impossible to
    # create — this function is the map's single writer (the send hook AND
    # reping's own re-track both flow through it). GENERATION-guarded
    # (#407 review MAJOR-1): a daily re-track posts an OLD question onto
    # the CURRENT questions channel — an unconditional drop there ate a
    # LIVE, newer question the session tracked on that channel; comparing
    # ask generations keeps the newer ask untouched. Channel-scoped ON
    # PURPOSE: a DISCORD_MIRROR fan-out records one entry per target
    # THREAD (same session, DIFFERENT channels) within seconds — one
    # generation's siblings, not ghosts, each kept so a reply in either
    # thread keeps routing (watchdog job 7). This mirrors the edit path's
    # own within-window semantics (the second ask REPLACES the first on
    # the phone) — past the window the behavior is now consistent instead
    # of accidentally divergent. #449 closes #407's documented MAJOR-2
    # residual (a superseded ask's card stays visible/answerable on
    # Discord while its entry was DELETED, so a reply to it — including a
    # reply to YESTERDAY's card after the daily re-track superseded it —
    # was silently lost): a superseded entry now moves to the GRACE store
    # (see the grace-store section below drop_question) instead of being
    # popped outright, so job 7 keeps routing replies to it for
    # QUESTION_GRACE_S. No ghost re-ping risk: reping_stale_questions and
    # the statusline both read ONLY the main map. A _grace_put failure is
    # deliberately not fatal here — the supersede itself must still
    # happen (the live, newer ask wins the map either way).
    for mid in [mm for mm, v in d.items()
                if mm != message_id and isinstance(v, dict)
                and str(v.get("session") or "") == session
                and str(v.get("channel") or "") == channel
                and ask_generation(v) <= asked]:
        _grace_put(mid, d.get(mid), now=now,
                   path=grace_path if grace_path is not None
                   else _grace_path_for(path))
        d.pop(mid, None)
    # prune malformed — a legacy entry that isn't a dict (never one this
    # function itself could have written) is immediately prunable rather
    # than crashing the write (mirrors the identical fix in
    # `record_card_message`, #297/#298 review MINOR-7 — same pre-existing
    # exposure, same-file, fixed in the same pass rather than filed
    # separately). No age-based prune any more (#368) — an entry now lives
    # until job 7 / prune_answered_questions genuinely resolves it, bounded
    # only by the hard cap below.
    for mid in [m for m, v in d.items() if not isinstance(v, dict)]:
        d.pop(mid, None)
    # hard cap — keep the newest by ts
    if len(d) > _QUESTIONS_MAX:
        for mid, _v in sorted(
                d.items(),
                key=lambda kv: kv[1].get("ts") or 0 if isinstance(kv[1], dict) else 0
        )[:len(d) - _QUESTIONS_MAX]:
            d.pop(mid, None)
    return _save_questions(d, path)


def drop_question(message_id, path=None):
    """Remove one answered/obsolete question. Fail-safe."""
    message_id = str(message_id or "").strip()
    if not message_id:
        return False
    d = load_questions(path)
    if message_id in d:
        d.pop(message_id, None)
        return _save_questions(d, path)
    return False


# --- pruned-question GRACE store (#449) ------------------------------------
# prune_answered_questions' "any later human prompt = answered at the
# terminal" inference is provably FALSE for a user who types OTHER things at
# the terminal while answering the actual question on the phone (david live
# incident 2026-08-13: 4 entries pruned, the Discord answer silently lost —
# job 7 never even fetched, since an empty map short-circuits it). A pruned
# or superseded entry therefore moves HERE instead of being deleted:
# watchdog job 7 merges this store for reply matching, so a reply within
# QUESTION_GRACE_S still routes NORMALLY (typed into the asking session,
# with the original question wording). The MAIN map stays the single source
# for the statusline Q badge (statusbar.py reads discord-questions.json
# directly) and for the daily re-ask (reping_stale_questions reads
# load_questions only) — a grace entry is never counted and never re-pinged,
# so #407's ghost problem and the 2026-07-22 stale-badge problem both stay
# fixed. Same file shape, same hard cap, same fail-safe discipline as the
# main map; `pruned` stamps when the entry left the main map. Expiry is
# enforced by job 7 at load time (deliver_discord_replies), NOT by the
# prune: bare run_once tests without a Discord fetcher must never touch
# this store, and job 7 is the store's only consumer anyway.
_GRACE_REL = "discord-questions-grace.json"
QUESTION_GRACE_S = 24 * 3600


def _grace_path():
    # DERIVED from the main map's directory, never _claude_dir() directly:
    # a test that sandboxes _questions_path (the established per-class
    # patch) automatically sandboxes the grace store too. NOT sufficient
    # alone (#449-review F1): callers passing an EXPLICIT `path=` never
    # consult this function, so record_question/grace_question derive
    # their grace path from that explicit path themselves — see
    # _grace_path_for(). Both halves together are what keep the store
    # hermetic under pytest AND unittest discover.
    return os.path.join(os.path.dirname(_questions_path()), _GRACE_REL)


def _grace_path_for(path):
    """The grace path belonging BESIDE an explicit main-map `path` (#449-
    review F1: the pre-existing test population sandboxes via the `path=`
    PARAMETER, not by patching _questions_path — deriving from the global
    resolver alone silently wrote grace entries into the REAL ~/.claude on
    every suite run). None/empty path → the global _grace_path()."""
    if not path:
        return _grace_path()
    return os.path.join(os.path.dirname(os.path.abspath(path)), _GRACE_REL)


def load_grace_questions(path=None):
    """The pruned-question grace map (message-id → entry + `pruned` ts).
    {} on any error."""
    path = path or _grace_path()
    try:
        with open(path, encoding="utf-8") as h:
            d = json.load(h)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_grace_questions(d, path=None):
    path = path or _grace_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as h:
            json.dump(d, h)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _grace_put(message_id, rec, now=None, path=None):
    """Insert one entry into the grace store (stamping `pruned`), capped
    like the main map (newest by `pruned` kept). Fail-safe — False on any
    write failure, and the caller must then KEEP its main-map entry (losing
    the answer is the one failure this store exists to prevent)."""
    message_id = str(message_id or "").strip()
    if not message_id or not isinstance(rec, dict):
        return False
    now = time.time() if now is None else now
    d = load_grace_questions(path)
    ent = dict(rec)
    ent["pruned"] = int(now)
    d[message_id] = ent
    for mid in [m for m, v in d.items() if not isinstance(v, dict)]:
        d.pop(mid, None)
    if len(d) > _QUESTIONS_MAX:
        for mid, _v in sorted(
                d.items(),
                key=lambda kv: kv[1].get("pruned") or 0
        )[:len(d) - _QUESTIONS_MAX]:
            d.pop(mid, None)
    return _save_grace_questions(d, path)


def grace_question(message_id, now=None, path=None, grace_path=None):
    """Move one entry MAIN map → GRACE store (#449). Grace-put FIRST, then
    drop from the main map — a grace-write failure keeps the entry in the
    main map (still routable, the prune retries next sweep) rather than
    losing it. False when absent or on write failure."""
    message_id = str(message_id or "").strip()
    if not message_id:
        return False
    rec = load_questions(path).get(message_id)
    if not isinstance(rec, dict):
        return False
    if grace_path is None:
        grace_path = _grace_path_for(path)     # #449-review F1
    if not _grace_put(message_id, rec, now=now, path=grace_path):
        return False
    return drop_question(message_id, path)


def drop_grace_question(message_id, path=None):
    """Remove one delivered/expired grace entry. Fail-safe."""
    message_id = str(message_id or "").strip()
    if not message_id:
        return False
    d = load_grace_questions(path)
    if message_id in d:
        d.pop(message_id, None)
        return _save_grace_questions(d, path)
    return False


# --------------------------------------------------------------------------- #
# #558 -- episode dedup with hysteresis for chronic-condition alerts (546
# lane 2). An OPT-IN primitive a chronic-condition caller consults BEFORE
# send(): it decides open/hold/clearing/recover/quiet and NEVER posts, so a
# chronic condition alerts ONCE per onset + ONE recovery (never the 546
# `burn-alert:<hour>` per-bucket re-page) and clearing needs N consecutive
# healthy passes (hysteresis), not one flicker. Deliberately SEPARATE from
# send() -- send() stays byte-identical, so this can never suppress a
# legitimate ❓/✅ (the exact concern that deferred lane 2 out of 546 lane 1).
# 546 lane 1 already suppressed the CURRENT chronic classes at the send()
# chokepoint; this is the mechanism 546 lane 2 asked for ("the shared throttle
# gains hysteresis") for any FUTURE or remaining chronic alert. The consuming
# wiring (repo_health net-drift/stuck-main) is tracked in issue #560 so this
# opt-in primitive is never orphaned as unconsumed dead code.
# --------------------------------------------------------------------------- #
_EPISODES_REL = "notify-episodes.json"
EPISODE_CLEAR_AFTER = 3               # default consecutive healthy passes to clear
EPISODE_MAX_AGE_S = 30 * 24 * 3600    # age-reap a stuck-open episode whose caller
                                       # stopped observing it (so the per-key store
                                       # never grows unbounded — #519/#531 discipline)


def _episodes_path():
    return os.path.join(_claude_dir(), _EPISODES_REL)


def load_episodes(path=None):
    """The condition_key -> {active, healthy_streak, opened_at, last_seen} map.
    {} on any error (fail toward alerting, never raise)."""
    path = path or _episodes_path()
    try:
        with open(path, encoding="utf-8") as h:
            d = json.load(h)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_episodes(d, path=None):
    path = path or _episodes_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as h:
            json.dump(d, h)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _prune_episodes(store, now):
    """Drop malformed entries and any episode not observed in
    `EPISODE_MAX_AGE_S` — a caller that stopped calling the gate (its job was
    removed) must not leak a record forever. A FUTURE `last_seen` (clock skew)
    is KEPT (the #519 safe direction — `now - last` goes negative, never
    exceeds the max age). Never raises."""
    for k in list(store.keys()):
        v = store.get(k)
        if not isinstance(v, dict):
            store.pop(k, None)
            continue
        try:
            last = float(v.get("last_seen", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if now - last > EPISODE_MAX_AGE_S:
            store.pop(k, None)


def episode_gate(condition_key, healthy, clear_after=None, now=None, path=None):
    """Decide what a chronic-condition caller should do THIS pass (#558).

    Call it on EVERY pass (healthy or not) with a stable `condition_key` and a
    boolean `healthy` (True = the condition is absent this pass). Returns:
      * "open"     — first unhealthy pass of a new episode -> SEND the onset alert
      * "hold"     — the condition persists -> send NOTHING (no per-pass re-page)
      * "clearing" — a healthy pass, fewer than `clear_after` in a row -> hysteresis, send NOTHING
      * "recover"  — `clear_after` consecutive healthy passes -> SEND ONE recovery message
      * "quiet"    — no active episode and healthy -> nothing to do

    OPT-IN and side-effect-free w.r.t. Discord: it only DECIDES + updates its
    own JSON store; the caller does the sending, so `send()` is untouched.
    Best-effort/fail-safe: an unreadable/unwritable store degrades toward
    ALERTING (a fresh unhealthy pass reads "open"), never raises, never
    silently swallows an alert. A falsy `condition_key` can't be tracked, so it
    fails the same way (unhealthy -> "open", healthy -> "quiet")."""
    now = time.time() if now is None else now
    try:
        clear_after = (EPISODE_CLEAR_AFTER if clear_after is None
                       else int(clear_after))
    except (TypeError, ValueError):
        clear_after = EPISODE_CLEAR_AFTER   # non-numeric -> default (never raise)
    if clear_after < 1:
        clear_after = 1
    if not condition_key:
        return "open" if not healthy else "quiet"
    store = load_episodes(path)
    _prune_episodes(store, now)
    rec = store.get(condition_key)
    active = bool(rec.get("active")) if isinstance(rec, dict) else False

    if not healthy:
        if not active:
            store[condition_key] = {"active": True, "healthy_streak": 0,
                                    "opened_at": now, "last_seen": now}
            _save_episodes(store, path)
            return "open"
        rec["healthy_streak"] = 0
        rec["last_seen"] = now
        store[condition_key] = rec
        _save_episodes(store, path)
        return "hold"

    # healthy this pass
    if not active:
        if isinstance(rec, dict):        # stale/cleared leftover -> tidy it
            store.pop(condition_key, None)
            _save_episodes(store, path)
        return "quiet"
    try:
        streak = int(rec.get("healthy_streak", 0) or 0) + 1
    except (TypeError, ValueError):
        streak = 1
    rec["last_seen"] = now
    if streak >= clear_after:
        store.pop(condition_key, None)   # episode closed
        _save_episodes(store, path)
        return "recover"
    rec["healthy_streak"] = streak
    store[condition_key] = rec
    _save_episodes(store, path)
    return "clearing"


# --- sent-card map (message id -> repo/issue) — airuleset#297/#298 -------
# The per-ticket completion CARD's own message id, mapped to which repo/issue
# it is for. Recorded at SEND time (never parsed back out of the card's
# rendered text, which shows a display-qualified repo name via
# `stream_qualified()` — e.g. "odoo-erp-david" — not the real `owner/repo`
# a `gh -R` call needs). Backs TWO features that both need "which repo/issue
# is this Discord message about": a ❓/❔ reaction on the card asks the
# sending stream about it (#297), and a REPLY on the card reopens the ticket
# with the remark (#298). Same numeric-snowflake validation, TTL and cap
# shape as the outstanding-question map above — a card can be replied to
# long after it was sent, so its TTL is generous (30 days).
_CARDS_REL = "discord-cards.json"
_CARDS_TTL_S = 30 * 24 * 3600
_CARDS_MAX = 300


def _cards_path():
    return os.path.join(_claude_dir(), _CARDS_REL)


def load_cards(path=None):
    """The message-id -> {repo, issue, channel, ts} map. {} on any error."""
    path = path or _cards_path()
    try:
        with open(path, encoding="utf-8") as h:
            d = json.load(h)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cards(d, path=None):
    path = path or _cards_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as h:
            json.dump(d, h)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def record_card_message(message_id, channel, repo, issue, now=None, path=None):
    """Record that Discord message `message_id` (in `channel`) is the
    per-ticket completion CARD for `repo`#`issue` — `repo` is the exact
    `--repo owner/name` value the worker passed (never a display string),
    so a later `gh -R` call targets the real repo. `message_id`/`channel`
    must be NUMERIC Discord snowflakes — the same guard `record_question`
    already applies, for the identical reason (a Mock repr / mangled shell
    var must never pollute the live map, 2026-07-04 incident). Prunes
    stale + over-cap entries in the same write. Returns True on success.
    Fail-safe (never raises)."""
    message_id = str(message_id or "").strip()
    channel = str(channel or "").strip()
    repo = str(repo or "").strip()
    if not message_id.isdigit() or not channel.isdigit() or not repo:
        return False
    try:
        issue = int(issue)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    d = load_cards(path)
    d[message_id] = {"channel": channel, "repo": repo, "issue": issue,
                     "ts": int(now)}
    # MINOR-7 (#297/#298 review): a malformed/legacy entry (not a dict) must
    # be treated as immediately prunable, never crash the whole write on the
    # very next call — mirrors the isinstance(row, dict) discipline used
    # elsewhere for any dict crossing a legacy-file boundary.
    for mid in [m for m, v in d.items()
                if not isinstance(v, dict)
                or now - (v.get("ts") or 0) > _CARDS_TTL_S]:
        d.pop(mid, None)
    if len(d) > _CARDS_MAX:
        for mid, _v in sorted(
                d.items(),
                key=lambda kv: kv[1].get("ts") or 0 if isinstance(kv[1], dict) else 0
        )[:len(d) - _CARDS_MAX]:
            d.pop(mid, None)
    return _save_cards(d, path)


_EDIT_WINDOW_S = 15 * 60              # a reword edits the ping only this soon after it


def _discord_api(token, method, url_path, payload=None):
    """One Discord REST call (GET/PATCH/…). Returns the decoded JSON body
    (dict; {} for an empty body) on success, None on any failure. Same
    User-Agent requirement as _post_discord. Never raises."""
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/" + url_path,
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers={"Authorization": "Bot " + token,
                     "Content-Type": "application/json",
                     "User-Agent": "DiscordBot (https://github.com/zbynekdrlik/airuleset, 1.0)"})
        body = urllib.request.urlopen(req, timeout=6).read()
        return json.loads(body) if body else {}
    except Exception:
        return None


def update_question(session, text, env=None, now=None, path=None, http=None):
    """EDIT the recent ❓ ping message(s) of `session` in place with `text` —
    a REWORDED, still-unanswered question. Discord EDITS do not push-ping:
    the phone got its push on the FIRST ask; a rewrite (a quality-gate retry
    turn, a /goal re-poke reword) must CONVERGE the existing card, never post
    a new one (3 pings in 3 minutes for one reworded question — camera-box,
    2026-07-05). Keeps the original first line (the @mention + header),
    replaces the body, refreshes the map ts so reply-routing stays alive.
    Only messages recorded within _EDIT_WINDOW_S are touched, and only when
    their live content still opens with a ❓ head (never overwrite an
    arbitrary message). Returns True when at least one message was edited."""
    session = str(session or "").strip()
    text = (text or "").strip()
    if not session or not text:
        return False
    env = _read_env() if env is None else env
    token = bot_token(env)
    if not token:
        return False
    now = time.time() if now is None else now
    http = _discord_api if http is None else http
    d = load_questions(path)
    edited = False
    for mid, v in sorted(d.items(), key=lambda kv: kv[1].get("ts") or 0,
                         reverse=True):
        if v.get("session") != session:
            continue
        if now - (v.get("ts") or 0) > _EDIT_WINDOW_S:
            continue
        chan = str(v.get("channel") or "")
        msg = http(token, "GET", "channels/%s/messages/%s" % (chan, mid))
        if not isinstance(msg, dict):
            continue
        head = str(msg.get("content") or "").split("\n", 1)[0]
        if "❓" not in head:
            continue
        content = head + "\n\n" + text
        if len(content) > 1990:                    # belt: Discord's 2000 cap
            content = content[:1990]
        ok = http(token, "PATCH", "channels/%s/messages/%s" % (chan, mid),
                  {"content": content, "flags": SUPPRESS_EMBEDS})
        if ok is not None:
            v["ts"] = int(now)
            edited = True
    if edited:
        _save_questions(d, path)
    return edited


def known_owner_ids(env=None):
    """The set of numeric Discord user ids allowed to DRIVE a session by replying
    (every `DISCORD_MENTION_<OWNER>` in this machine's .env, unwrapped from any
    `<@id>` form). This is the SECURITY boundary for reply-injection: only a
    trusted owner of THIS machine can have their reply typed into a session."""
    env = _read_env() if env is None else env
    ids = set()
    for k, v in env.items():
        if not k.startswith("DISCORD_MENTION_"):
            continue
        m = re.search(r"\d{5,25}", v or "")
        if m:
            ids.add(m.group(0))
    return ids


def bot_token(env=None):
    env = _read_env() if env is None else env
    return env.get("DISCORD_BOT_TOKEN", "") or ""


# --- send ----------------------------------------------------------------
# Message flag 1<<2 = SUPPRESS_EMBEDS: a notification carrying a URL (the
# run-card's 🔗 "where to see it" link) must NOT unfurl into a giant link
# preview — Discord rendered the Odoo login page's og:image logo under every
# codex-bridge card, making each message screen-sized (user complaint,
# 2026-07-04). The link stays clickable; only the preview embed is dropped.
SUPPRESS_EMBEDS = 1 << 2


def _post_discord(token, channel, content):
    """POST one message to one Discord channel/thread. Returns the SENT
    message's Discord id (a string) on success — the field
    `record_card_message` (#298) needs to map a card back to its repo/issue
    later — or a bare `True` when the response body doesn't parse to an id
    (an empty/malformed body; a test double that models success without a
    real payload). Falsy (False) only on a genuine failure. A caller that
    only checks truthiness (every pre-#298 caller) is unaffected either way.
    Discord REQUIRES a User-Agent — Cloudflare 403s the default
    "Python-urllib/*" ("error code: 1010"); a DiscordBot UA (per spec) gets
    through (the same reason the curl-based hook works). Never raises."""
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/channels/%s/messages" % channel,
            data=json.dumps({"content": content,
                             "flags": SUPPRESS_EMBEDS}).encode(),
            method="POST",
            headers={"Authorization": "Bot " + token,
                     "Content-Type": "application/json",
                     "User-Agent": "DiscordBot (https://github.com/zbynekdrlik/airuleset, 1.0)"})
        body = urllib.request.urlopen(req, timeout=6).read()
        try:
            data = json.loads(body) if body else {}
        except ValueError:
            data = {}
        mid = data.get("id") if isinstance(data, dict) else None
        return mid or True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# #546 (2026-08-18, owner directive comment 5333914691, verbatim: "ty sa mas
# starat len aby sa pokusili agenti rozbehnut cez continue … zaspamovat ma do
# discordu je ciste kontraproduktivne … stara sa teraz o limit a subscription
# iny project"). The automated ALERT classes below are "alerts for the agent"
# that reach a channel no agent reads — pure phone noise for the owner. They no
# longer POST to Discord: the watchdog's ONLY job on them is the SILENT
# `continue` auto-resume (send_verified / deliver_with_stash / tmux — none of
# which routes through send(), so suppression here leaves auto-resume 100%
# untouched). The signal is NOT lost — it stays in the machine channel (the
# watchdog sweep journal + this delivery log's explicit `suppressed` decision),
# the #546 audience split. Deliberately NARROW + EXACT (owning job in the
# comment); ❓ (`waiting:`/`❓:`), ✅ (`done:`), run-cards (`<repo>#<n>`),
# bounce/gkreq, job-4 `busypane:`, and the genuine one-shot `acctblock:` alarm
# (no auto-reset, needs a human) all use OTHER key namespaces and keep sending.


def _auto_dedup_window_s():
    """The #559 throttle window (seconds), env-overridable via
    `AIRULESET_AUTO_DEDUP_WINDOW_S`. A missing / garbage / non-positive value
    falls back to `AUTO_DEDUP_WINDOW_S`, never raises."""
    try:
        v = int(os.environ.get("AIRULESET_AUTO_DEDUP_WINDOW_S") or 0)
    except (TypeError, ValueError):
        v = 0
    return v if v > 0 else AUTO_DEDUP_WINDOW_S


def _auto_dedup_key(body, owner, now=None, window_s=None):
    """#559: a stable, TRACEABLE dedup key for a send that arrived without one.

    `auto:<owner>:<sha16(body)>:<time-bucket>`, so:
      * identical content sent repeatedly WITHIN the window dedups (a runaway
        keyless caller is throttled instead of flooding Discord);
      * legitimately-DISTINCT pings (different body) hash differently and ALL
        send — never a false dedup of two distinct messages;
      * the same content re-sent AFTER the window sends again (the time bucket
        bounds the dedup window to minutes, not the 14-day marker TTL a bare
        content hash would inherit);
      * the delivery log carries `key=auto:…` instead of an untraceable `key=-`.
    `owner` is folded in so two DIFFERENT owners' identical bodies never
    collide. The result is `auto:`-namespaced, so it never matches a #546
    `SUPPRESSED_ALERT_PREFIXES` entry."""
    now = time.time() if now is None else now
    window = window_s if window_s else _auto_dedup_window_s()
    digest = hashlib.sha256(str(body or "").encode("utf-8", "replace")).hexdigest()[:16]
    return "auto:%s:%s:%d" % (owner or "-", digest, int(now // window))


def _content_dedup_store_dir(store_dir=None):
    """The SHARED cross-user content-dedup store (#687). NOT under $HOME —
    david1–4 are separate accounts, so the store must be reachable by all box
    tenants; a per-$HOME store would not coalesce across them. Overridable by
    `store_dir` (tests) or `$AIRULESET_CONTENT_DEDUP_DIR`."""
    return (store_dir or os.environ.get("AIRULESET_CONTENT_DEDUP_DIR")
            or os.path.join(tempfile.gettempdir(), CONTENT_DEDUP_DIRNAME))


def _content_dedup_key(text, owner, project, now, window_s):
    """`content#<owner>#<project>#<sha16(normalized_text)>#<time-bucket>` — the
    bucket lives in the KEY (like `_auto_dedup_key`) so a marker from a previous
    window has a DIFFERENT filename and never collides (no mtime races). `#`
    separators survive filename sanitisation and cannot occur in a username /
    repo name / hex / int (#141 collision-safe by construction)."""
    norm = " ".join(str(text or "").split()).lower()
    digest = hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()[:16]
    return "content#%s#%s#%s#%d" % (owner or "-", project or "-", digest,
                                    int(now // window_s))


def _content_dedup_filename(key):
    safe = re.sub(r"[^A-Za-z0-9._#-]", "_", key)
    if len(safe.encode("utf-8")) > _DEDUP_NAME_MAX:
        safe = "content#" + hashlib.sha256(
            key.encode("utf-8", "replace")).hexdigest()
    return safe


def _content_dedup_sweep(d, now, window_s):
    """Best-effort reclaim of markers older than 2 windows (their bucket is long
    past). Under the sticky store dir only the marker's OWN creator can unlink
    it — a foreign tenant's EPERM is skipped, never fatal."""
    cutoff = now - 2 * window_s
    try:
        names = os.listdir(d)
    except OSError:
        return
    for name in names:
        if not name.startswith("content"):
            continue
        p = os.path.join(d, name)
        try:
            if os.path.getmtime(p) < cutoff:
                os.unlink(p)
        except OSError:
            continue


def content_dedup_claim(text, owner=None, project=None, now=None,
                        window_s=None, store_dir=None):
    """#687: cross-session (cross-USER) content dedup for the ✅ device ping.

    Returns "claim" (this caller is the FIRST to send this payload in the
    window — DELIVER) or "dup" (an identical payload was already claimed in the
    window — SUPPRESS). Atomic first-writer-wins via `os.open(O_CREAT|O_EXCL|
    O_NOFOLLOW)` in a SHARED sticky /tmp store, so it coalesces across the four
    separate `david` unix accounts on subdev, not only across one user's tmux
    sessions.

    FAIL-OPEN: any error creating/claiming (a mis-permissioned store, a full
    disk, a hostile 0700 dir) returns "claim" — a duplicate ping is strictly
    better than a lost one. Security: the store is 0o1777 (world-writable +
    STICKY, so a tenant cannot delete/rename another's marker), markers are
    O_NOFOLLOW (no symlink-target write) and 0o644 (all tenants read to detect
    the EEXIST claim). Accepted LOW-severity residual: a hostile same-box tenant
    could pre-create a marker to suppress another's ✅ (worst case one missed,
    recoverable ping) — same-box tenants are the owner's own accounts."""
    now = time.time() if now is None else now
    window_s = window_s or CONTENT_DEDUP_WINDOW_S
    d = _content_dedup_store_dir(store_dir)
    try:
        os.makedirs(d, exist_ok=True)
        # Force world-writable + sticky, umask-proof (os.makedirs masks the
        # write bits). A foreign-owned existing store (david1 created it, david2
        # runs now) EPERMs here but is ALREADY 0o1777, so writes still work; a
        # genuine perm problem fails-open at the O_EXCL open below. Best-effort,
        # silent by design (this is the ✅ hot path). # airuleset:script-ok
        # best-effort chmod on a shared store; EPERM is expected + handled downstream
        try:
            os.chmod(d, 0o1777)
        except OSError:
            pass
    except OSError:
        return "claim"            # cannot stand up the store → fail OPEN
    _content_dedup_sweep(d, now, window_s)
    path = os.path.join(d, _content_dedup_filename(
        _content_dedup_key(text, owner, project, now, window_s)))
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o644)
    except FileExistsError:
        return "dup"              # already claimed in this window
    except OSError:
        return "claim"            # any other error (EACCES, ...) → fail OPEN
    # The O_EXCL create IS the claim — the marker's CONTENT is never read (dedup
    # keys on the file's EXISTENCE), so no write is needed, and a mid-write
    # failure can never turn a successful claim into a raised exception (this
    # keeps the function fail-safe by construction for a direct Python caller too,
    # not only the shell caller's `|| echo claim`; #687 review 🔵).
    os.close(fd)
    return "claim"


SUPPRESSED_ALERT_PREFIXES = (
    ("apierr", "api-error"),             # watchdog job 1 (stall / busy / giveup / stashabort)
    ("sesslimit", "session-limit"),      # watchdog job 6 (5h limit / giveup / resume)
    ("usage", "usage-limit"),            # watchdog usage.py (weekly usage %)
    ("burn-alert", "token-burn"),        # watchdog job 19 (hourly token-burn)
    ("fleet-burn-budget", "fleet-budget"),  # watchdog job 16 (fleet spend budget)
    # #676 (2026-08-24 owner ruling): #662's `oauthblock:` escalation alarm
    # (watchdog job 1, the persistent /login-revoke valve) is SPAM — a 401
    # "OAuth access token has been revoked" is NORMAL subscription-switching by
    # the Claude project + its watchers, not an incident. Same #546 audience
    # split: no Discord PING, the machine channel keeps the signal (watchdog
    # journal + the `suppressed` delivery-log line). The watchdog's job stays
    # the silent auto-resume (#675 owns the work-resume half).
    ("oauthblock", "oauth-revoke (#676)"),  # watchdog job 1 escalation (#662 alarm — owner-ruled spam)
    # #688 (2026-08-25 owner ruling): #662's `stuckalert:` frozen-goal alarm
    # (goal_lane_sweep -> _lane_stuck_owner_alert) is SPAM too — the structural
    # `stuck` verdict (armed /goal + 0 workers + backlog + idle over threshold)
    # is a HEURISTIC that fires on many states that do NOT need a human
    # (transient idle, an owner-stopped session, a #676-normal oauth-revoke), so
    # it cannot clear the "genuinely needs a human" bar. Same #546 audience split
    # as #676 above: no Discord PING, the machine channel keeps the signal
    # (watchdog journal + the `suppressed` delivery-log line). Auto-recovery (the
    # lane keystroke nudge, send_verified) never routes through send(), so it is
    # untouched. `acctblock:` (genuine account-block, needs a human) is the ONE
    # escalation class that stays un-suppressed.
    ("stuckalert", "structural-stuck (#688)"),  # goal_lane_sweep frozen-goal alarm (#662 — owner-ruled spam)
    # #693 (2026-08-25 owner ruling): the `lanestall:` give-up ping
    # (goal_lane_occupancy_nudge -> _lane_giveup_decision, "⚠️ … /goal
    # armovaný, ale lány sa nezaplnili … pozri sa na reláciu") was the LAST
    # un-suppressed member of the same armed-/goal + empty-lanes class the
    # owner ruled spam three times (#546/#676/#688) — and it routinely fired
    # on NORMAL states (backlog exhausted / everything parked on U·W·gk),
    # because its gate reads a ~10-min-TTL backlog cache. Same #546 audience
    # split: no Discord PING, the machine channel keeps the signal (watchdog
    # journal — which since #693 also names the CLASSIFIED cause of the empty
    # lanes — + the `suppressed` delivery-log line). The lane keystroke nudge
    # never routes through send(), so it is untouched. `acctblock:` (genuine
    # account-block) + watchdog job 35 (dead-fleet) stay the ONLY phone
    # alarms for a coverage outage.
    ("lanestall", "lane-stall give-up (#693)"),  # goal lane give-up ping (owner-ruled spam)
    # #704 (2026-08-25 owner ruling, GENERAL): "an idle/stall/no-work/session-stojí
    # state NEVER owner-pings" — the phone keeps ONLY ❓ question / ✅ final done /
    # per-ticket run-card / acctblock / job-35 dead-fleet. These airuleset-OWNED
    # STATE/STALL heuristic verdicts are the direct siblings of the family already
    # suppressed above: each is a "loop/lane/session died/stalled/idle" heuristic
    # that either has its OWN machine-channel recovery (goal_dark_watch re-arm,
    # the lane/working nudge, resume) OR is a structural git-drift alarm a human
    # resolves (stuck-main / delivery-stall). The recovery ACTIONS are what
    # never route through send() (tmux keystrokes / re-arms), so suppression at
    # the chokepoint leaves the producer's control flow byte-identical — the
    # alert SENDS themselves all DO route through send() (run_once wires
    # send_fn = notify.send), which is why this denylist suffices. (#713: the
    # old "NONE of which route through send()" phrasing here read as if the
    # ALERTS bypass send() and seeded a false bypass hypothesis — the
    # 2026-08-25 23:29 delivery-stall ping was pure deploy-lag: #704 merged
    # to main 23:55, dev2 pulled it 00:47.) (the #546/#688 architectural fact; each producer's
    # once-per-episode latch is set independent of the send return status, so
    # returning "suppressed" instead of "sent" causes no re-fire storm). The
    # machine channel keeps the verdict (watchdog journal + the `suppressed`
    # delivery-log line); job-35 covers a genuinely DEAD box. Measurement:
    # issue #704. DELIBERATELY NOT suppressed here: `waiting:` (it relays the
    # ACTUAL ❓ question text — a real question, kept), plus operational classes
    # (net-drift / conformance / janitor-stuck / gk* / dorphan / disk-headroom)
    # left to a needs-user-decision follow-up. (owner-decision-digest was on
    # that deferred list until #707 delivered its owner decision — the entry
    # below.)
    # HAZARD (no live collision — all managed repos checked): the `prefix + "-"`
    # boundary means a FUTURE repo whose NAME starts with one of these + a dash
    # (e.g. `stuck-main-tool`, `long-turn-x`) would have its run-card key
    # `<name>#<n>` swallowed. If onboarding such a repo, rename the prefix here.
    ("goal-dark", "goal-loop-died (#704)"),          # goal.py::goal_dark_watch (auto-re-arm)
    ("goalarm-expired", "goal-autoarm-failed (#704)"),  # goal.py::goal_sweep
    ("stuck-main", "structural-stuck-main (#704)"),  # repo_health.py::stuck_main_sweep (open/recover)
    ("delivery-stall", "structural-delivery-stall (#704)"),  # repo_health.py::delivery_stall_watch
    ("inputdead", "session-input-wedge (#704)"),     # discord_replies.py (resume-recovery)
    ("pwedge", "prompt-wedge (#704)"),               # wedge.py (pwedge / -backlog / -submit-giveup)
    ("busypane", "working-stall-busypane (#704)"),   # __init__.py job 4 (visí na ⏳ WORKING)
    ("long-turn", "long-turn (#704)"),               # long_turn.py (over-fires on long CI waits)
    ("workingstall-giveup", "working-stall-giveup (#704)"),  # __init__.py job 4 escalate
    ("textcall-giveup", "textcall-stall-giveup (#704)"),     # __init__.py job 4 escalate
    # #707 (2026-08-26 owner ruling): the DAILY OWNER-DECISION DIGEST class
    # (#461) is ABOLISHED — the box-wide per-project ticket roundup was
    # addressed to `account_owner` (the first-owner-seen pane-scan coin flip,
    # no `owners_seen` ambiguity guard) and delivered montalu client-ticket
    # content into David's thread on multi-owner dev2: a cross-subject
    # information LEAK, not mere spam (#489 had gated only reduced-authority
    # boxes). The producer (`watchdog/questions.py::
    # reping_owner_decision_tickets`) is a permanent no-op tombstone; THIS
    # entry is the belt-and-braces backstop so even STALE code on a
    # not-yet-redeployed box can never ping. Same #546 audience split:
    # machine channel keeps the decision (journal + the `suppressed`
    # delivery-log line). It matches the class's own dedup_key
    # (`owner-decision-digest:<day-bucket>`) — this layer is dedup_key-keyed
    # by construction (see _suppressed_alert_class), so the true match needs
    # no new message-prefix mechanism.
    ("owner-decision-digest", "owner-decision-digest (#707)"),  # questions.py digest (owner-ruled leak)
)


def _suppressed_alert_class(dedup_key):
    """The human label of the #546 owner-suppressed alert class `dedup_key`
    belongs to, or None if it is not a suppressed class (so it POSTs normally).
    Boundary-matched (`prefix:` / `prefix-`) so `busypane:` (job 4, NOT
    api-error), `fleet-burn-budget` vs `burn-alert`, and a same-letters-but-
    different-namespace key never collide. A keyless send is never a suppressed
    alert — every suppressed class carries one of these keys by construction."""
    if not dedup_key:
        return None
    k = str(dedup_key)
    for prefix, label in SUPPRESSED_ALERT_PREFIXES:
        if k == prefix or k.startswith(prefix + ":") or k.startswith(prefix + "-"):
            return label
    return None


def send(body, env=None, owner=None, dedup_key=None, dry_run=False,
        return_message_id=False, kind="default", project=None):
    """Prepend the owner @mention to `body` and POST it to the Discord notification
    channel — AND, in parallel, to every mirror owner's own thread with their own
    @mention (DISCORD_MIRROR_<OWNER>, e.g. david → also zbynek). Deduped on
    `dedup_key`. Returns a short status string ('sent' / 'dedup' / 'dry-run' /
    'no-config' / 'error') reflecting the PRIMARY send. Never raises.

    `kind="questions"` (#368): routes every target (primary + mirrors) through
    `notification_channel(env, t, kind="questions")` instead of the default
    thread — the SAME per-owner `-q` questions-thread cascade
    `hooks/notify-discord-send.sh` already applies to every interactive ❓
    ping, now reachable from a Python-side sender too (watchdog's daily
    question re-ask). `kind="default"` (the parameter's own default) is
    byte-for-byte the pre-#368 behaviour — every EXISTING caller passes
    nothing and is unaffected.

    `return_message_id=True` (#298) returns `(status, message_id)` instead —
    `message_id` is the PRIMARY send's Discord message id (a string) when
    `status == "sent"` and `_post_discord` returned a real id, else None. Every
    EXISTING caller passes nothing here and keeps the plain-string contract
    unchanged.

    `project` (#369) routes EVERY target (primary + each mirror) to THEIR OWN
    per-project thread — `resolve_project_channel`, which self-heals a
    missing thread — but ONLY on a REAL send (`not dry_run`); a preview call
    resolves via the plain, side-effect-free `notification_channel(...,
    project=project)` instead, so repeatedly previewing/testing a send can
    never spawn background self-heal processes or write fallback log lines.
    `project=None` (the default) is byte-for-byte the pre-#369 behaviour for
    every existing caller.

    #546: a `dedup_key` belonging to an owner-suppressed ALERT class
    (`SUPPRESSED_ALERT_PREFIXES` — api-error / limit / token-burn, the #676
    oauth-revoke class, the #688/#693/#704 state-stall family and the #707
    owner-decision-digest class) POSTs
    NOTHING and returns "suppressed" — logged as an explicit decision (never a
    silent drop), never dry-run-mutating. The gate runs FIRST so a suppressed
    class never claims a dedup marker, resolves a channel, or reaches the
    network — and it covers every caller of this one chokepoint (watchdog jobs
    1/6/16/19 + usage.py + the CLI `--api-error`) at once.

    #559: a KEYLESS send (`dedup_key` falsy) auto-derives a bounded-window
    content-hash key (`_auto_dedup_key`, AFTER the #546 gate + owner
    resolution), so identical content within the window dedups (traceable
    `key=auto:…` instead of `key=-`) while distinct content always sends. Like
    the keyed path, a keyless send whose POST fails keeps its claim, so a
    byte-identical retry is deduped until the window rolls — a caller needing
    guaranteed retry delivery passes an explicit `dedup_key`."""
    suppressed = _suppressed_alert_class(dedup_key)
    if suppressed is not None:
        if not dry_run:
            log_delivery("suppressed", kind="alert-class", key=dedup_key,
                         reason="#546 owner-directed: %s" % suppressed)
        return ("suppressed", None) if return_message_id else "suppressed"
    env = _read_env() if env is None else env
    # Resolve the owner ONCE so the @mention and the per-owner thread target agree
    # (a tmux re-query between them could otherwise disagree).
    if owner is None:
        owner = resolve_owner()

    # #710: owner-scoped QUESTION-ping suppression (the watchdog re-ask / digest
    # transport — every `send(kind="questions")` caller). Gated AFTER owner
    # resolution but BEFORE the keyless-key derivation / dedup claim / channel
    # resolution / network, mirroring the #546 alert-class gate: a suppressed
    # question POSTs NOTHING, returns "suppressed", and logs one explicit
    # decision (never a silent drop — the #134/#546 machine-channel split). Only
    # `kind="questions"` for an OFF owner is affected; a ✅ / any other kind to
    # the same owner is untouched, and david keeps FULL question delivery. The
    # interactive Stop-hook ❓ path never routes through send() — it is gated
    # separately, on the SAME `question_ping_off` predicate, in
    # hooks/notify-discord-send.sh.
    if kind == "questions" and question_ping_off(owner):
        if not dry_run:
            log_delivery("suppressed", kind="question-ping",
                         key=dedup_key or "",
                         reason="#710 owner-directed: %s" % (owner or "?"))
        return ("suppressed", None) if return_message_id else "suppressed"

    # #559: a keyless send used to bypass dedup AND trace entirely (logged
    # key=-). Auto-derive a bounded-window content-hash key so it becomes
    # dedupable + traceable, WITHOUT dropping legitimately-distinct pings. Runs
    # AFTER the #546 suppression gate (a keyless send is never a suppressed
    # alert class — every suppressed class carries one of those keys) and AFTER
    # owner resolution (owner is folded into the key so two owners' identical
    # bodies never collide).
    if not dedup_key:
        dedup_key = _auto_dedup_key(body, owner)

    # Build the delivery list ONCE: primary owner first, then the parallel mirror
    # recipients (each gets the SAME body in THEIR OWN thread with THEIR OWN @mention).
    # The PRIMARY is ALWAYS kept (even with no channel — dry-run previews it, real
    # delivery reports no-config). A mirror whose channel duplicates one ALREADY in the
    # list is dropped, so no double-post — not just vs the primary, but vs any earlier
    # mirror too (e.g. two owners both falling back to the shared channel). dry-run and
    # real delivery iterate this SAME list, so the preview is faithful.
    targets, seen_channels = [], set()      # targets: list of (owner, channel)
    for i, t in enumerate([owner] + mirror_owners(env, owner)):
        # #368 x #369 merge: questions (kind="questions") stay CENTRALIZED in the
        # owner's -q thread (#369's own design: project is ignored for questions);
        # only default-kind notifications route to the per-project thread.
        ch = (resolve_project_channel(env, t, project)
              if (project and kind == "default" and not dry_run)
              else notification_channel(env, t, kind=kind, project=project))
        if i == 0:                          # primary: always included
            targets.append((t, ch))
            if ch:
                seen_channels.add(ch)
        elif ch and ch not in seen_channels:
            seen_channels.add(ch)
            targets.append((t, ch))

    # dry-run never claims dedup (so previews / tests stay re-runnable). One line per
    # DISTINCT target (a single line when no mirror is configured — unchanged contract).
    if dry_run:
        print("\n".join((mention_prefix(env, t) + (body or ""))[:_MAX_CONTENT]
                         for t, _ch in targets))
        return ("dry-run", None) if return_message_id else "dry-run"

    # Claim FIRST so a racing duplicate can't double-post; RELEASE only when the
    # primary provably never sent (no token / no channel), so a transient failure
    # can NOT re-send (a timeout can fire AFTER Discord accepted the message).
    if dedup_key and not _dedup_claim(dedup_key):
        # #184: logged too, not just non-deliveries — an ABSENT log file must
        # mean "the logger is broken", never "nothing has ever been sent or
        # skipped here", and a dedup skip is exactly the kind of attempt that
        # used to leave zero trace on an otherwise perfectly healthy box.
        log_delivery("dedup", kind="python", key=dedup_key, reason="already-claimed")
        return ("dedup", None) if return_message_id else "dedup"

    token = env.get("DISCORD_BOT_TOKEN", "")
    primary_owner, primary_channel = targets[0]
    if not token or not primary_channel:
        _dedup_release(dedup_key)
        log_delivery("no-config", kind="python", key=dedup_key,
                     reason="no-token" if not token else "no-channel")
        return ("no-config", None) if return_message_id else "no-config"

    # Primary send determines the return status; mirror sends are best-effort (a
    # mirror failure never fails the whole notification, never releases the dedup).
    primary_result = _post_discord(
        token, primary_channel,
        (mention_prefix(env, primary_owner) + (body or ""))[:_MAX_CONTENT])
    status = "sent" if primary_result else "error"
    # `_post_discord` returns the real Discord message id (a string) on a
    # genuine POST, or a bare truthy/falsy sentinel from a test double that
    # doesn't model one — only a real string is ever handed to a caller, so
    # a mocked `True` can never masquerade as a snowflake (#297/#298).
    message_id = primary_result if isinstance(primary_result, str) else None
    for t, ch in targets[1:]:
        _post_discord(token, ch, (mention_prefix(env, t) + (body or ""))[:_MAX_CONTENT])
    # Record the OUTCOME on the claim (#135) and log EVERY attempt, not only
    # a non-delivery (#184) — a "sent" line is what turns the log's own
    # absence into a diagnosable state ("the logger is broken") instead of
    # being indistinguishable from "nothing has ever failed to deliver".
    _dedup_mark_status(dedup_key, status)
    log_delivery(status, kind="python", key=dedup_key,
                 reason="" if status == "sent" else "post-failed")
    return (status, message_id) if return_message_id else status
