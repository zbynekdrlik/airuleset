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
import json
import time
import subprocess
import urllib.request

# Discord hard cap is 2000 chars per message; stay safely under it.
_MAX_CONTENT = 1900
# Per-field cap so one long goal/achieved can't dominate the card.
_FIELD_CAP = 320

_ENV_REL = "channels/discord/.env"
_DEDUP_DIRNAME = "autopilot-notify-sent"
_DEDUP_TTL_S = 14 * 24 * 3600

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
    board-daemon path. Otherwise the tmux SESSION GROUP is authoritative (sessions
    are 'zbynek-18' in group 'zbynek'); the session name with a trailing '-<n>'
    stripped is the fallback."""
    forced = os.environ.get("AIRULESET_NOTIFY_OWNER")
    if forced is not None:
        return re.sub(r"[^a-z0-9]", "", forced.strip().lower())
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


def notification_channel(env=None, owner=None):
    """Resolve the Discord channel/THREAD id to POST to for the current owner.

    Per-owner routing: each person gets their OWN thread so notifications don't
    mix (the user runs zbynek + marek side by side and an @mention in a shared
    thread was not enough — they want a separate `claude-zbynek` / `claude-marek`
    thread). `DISCORD_NOTIFICATION_CHANNEL_<OWNER>` (e.g.
    DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=<thread id>) wins when set; it falls back
    to the shared `DISCORD_NOTIFICATION_CHANNEL_ID` when the owner has no per-owner
    thread configured OR the owner can't be determined (no tmux). Returns "" when
    neither is set. A Discord thread IS a channel in the API, so the POST target is
    identical — only the id differs."""
    env = _read_env() if env is None else env
    owner = resolve_owner() if owner is None else owner
    if owner:
        per = (env.get("DISCORD_NOTIFICATION_CHANNEL_" + owner.upper()) or "").strip()
        if per:
            return per
    return (env.get("DISCORD_NOTIFICATION_CHANNEL_ID") or "").strip()


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


# --- dedup ---------------------------------------------------------------
def _dedup_dir():
    return os.path.join(_claude_dir(), _DEDUP_DIRNAME)


def _dedup_path(key):
    return os.path.join(_dedup_dir(), re.sub(r"[^A-Za-z0-9._#-]", "_", str(key)))


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
# secret. Bounded + self-pruning so a never-answered question can't accumulate.
_QUESTIONS_REL = "discord-questions.json"
_QUESTIONS_TTL_S = 24 * 3600          # an unanswered question older than this is stale
_QUESTIONS_MAX = 200                  # hard cap on tracked questions (newest kept)
_QUESTION_TEXT_MAX = 1200             # stored question text cap (codepoints — the
                                      # delivery wraps it into ONE typed prompt line)


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


def record_question(message_id, channel, session, cwd, now=None, path=None,
                    question=""):
    """Record that Discord message `message_id` (in `channel`) is the ❓ ping for
    `session` (transcript stem = CC session id) in `cwd`. Prunes stale + over-cap
    entries in the same write. Returns True on success. Fail-safe (never raises).

    `question` = the posted ❓ text: stored single-line (collapsed whitespace,
    leading @mentions stripped, codepoint-capped) so the watchdog's reply
    delivery can wrap the user's answer with the question it answers — a bare
    '1' typed hours/days later is meaningless once the session's context no
    longer holds the question (user ask, 2026-07-17).

    `message_id` and `channel` must be NUMERIC Discord snowflakes — anything else
    (a Mock repr from a mis-wired test, a mangled shell var) is refused so garbage
    can never pollute the live map (real incident: Mock strings landed in
    ~/.claude/discord-questions.json, 2026-07-04)."""
    message_id = str(message_id or "").strip()
    channel = str(channel or "").strip()
    session = str(session or "").strip()
    if not message_id.isdigit() or not channel.isdigit() or not session:
        return False
    now = time.time() if now is None else now
    q = " ".join(str(question or "").split())
    q = re.sub(r"^(?:<@[!&]?\d+>\s*)+", "", q)[:_QUESTION_TEXT_MAX]
    d = load_questions(path)
    d[message_id] = {"session": session, "cwd": str(cwd or ""),
                     "channel": str(channel or ""), "ts": int(now),
                     "question": q}
    # prune stale
    for mid in [m for m, v in d.items()
                if now - (v.get("ts") or 0) > _QUESTIONS_TTL_S]:
        d.pop(mid, None)
    # hard cap — keep the newest by ts
    if len(d) > _QUESTIONS_MAX:
        for mid, _v in sorted(d.items(), key=lambda kv: kv[1].get("ts") or 0
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
    """POST one message to one Discord channel/thread. Returns True on success.
    Discord REQUIRES a User-Agent — Cloudflare 403s the default "Python-urllib/*"
    ("error code: 1010"); a DiscordBot UA (per spec) gets through (the same reason
    the curl-based hook works). Never raises."""
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/channels/%s/messages" % channel,
            data=json.dumps({"content": content,
                             "flags": SUPPRESS_EMBEDS}).encode(),
            method="POST",
            headers={"Authorization": "Bot " + token,
                     "Content-Type": "application/json",
                     "User-Agent": "DiscordBot (https://github.com/zbynekdrlik/airuleset, 1.0)"})
        urllib.request.urlopen(req, timeout=6).read()
        return True
    except Exception:
        return False


def send(body, env=None, owner=None, dedup_key=None, dry_run=False):
    """Prepend the owner @mention to `body` and POST it to the Discord notification
    channel — AND, in parallel, to every mirror owner's own thread with their own
    @mention (DISCORD_MIRROR_<OWNER>, e.g. david → also zbynek). Deduped on
    `dedup_key`. Returns a short status string ('sent' / 'dedup' / 'dry-run' /
    'no-config' / 'error') reflecting the PRIMARY send. Never raises."""
    env = _read_env() if env is None else env
    # Resolve the owner ONCE so the @mention and the per-owner thread target agree
    # (a tmux re-query between them could otherwise disagree).
    if owner is None:
        owner = resolve_owner()

    # Build the delivery list ONCE: primary owner first, then the parallel mirror
    # recipients (each gets the SAME body in THEIR OWN thread with THEIR OWN @mention).
    # The PRIMARY is ALWAYS kept (even with no channel — dry-run previews it, real
    # delivery reports no-config). A mirror whose channel duplicates one ALREADY in the
    # list is dropped, so no double-post — not just vs the primary, but vs any earlier
    # mirror too (e.g. two owners both falling back to the shared channel). dry-run and
    # real delivery iterate this SAME list, so the preview is faithful.
    targets, seen_channels = [], set()      # targets: list of (owner, channel)
    for i, t in enumerate([owner] + mirror_owners(env, owner)):
        ch = notification_channel(env, t)
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
        return "dry-run"

    # Claim FIRST so a racing duplicate can't double-post; RELEASE only when the
    # primary provably never sent (no token / no channel), so a transient failure
    # can NOT re-send (a timeout can fire AFTER Discord accepted the message).
    if dedup_key and not _dedup_claim(dedup_key):
        return "dedup"

    token = env.get("DISCORD_BOT_TOKEN", "")
    primary_owner, primary_channel = targets[0]
    if not token or not primary_channel:
        _dedup_release(dedup_key)
        log_delivery("no-config", kind="python", key=dedup_key,
                     reason="no-token" if not token else "no-channel")
        return "no-config"

    # Primary send determines the return status; mirror sends are best-effort (a
    # mirror failure never fails the whole notification, never releases the dedup).
    status = "sent" if _post_discord(
        token, primary_channel,
        (mention_prefix(env, primary_owner) + (body or ""))[:_MAX_CONTENT]) else "error"
    for t, ch in targets[1:]:
        _post_discord(token, ch, (mention_prefix(env, t) + (body or ""))[:_MAX_CONTENT])
    # Record the OUTCOME on the claim (#135) and log anything that is not a
    # delivery — silence is what made #134 invisible for five days.
    _dedup_mark_status(dedup_key, status)
    if status != "sent":
        log_delivery(status, kind="python", key=dedup_key, reason="post-failed")
    return status
