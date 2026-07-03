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


def compose_autopilot_card(repo, tickets, pr=None, version=None, merge_sha=None,
                           review_ok=True, done=None, remaining=None, urls=None):
    """Build the canonical per-ticket completion card (Slovak, Discord markdown).

    `tickets` is a list of dicts {n, title, goal, achieved}. `urls` is a list of
    "where to SEE the change live" links — each a bare URL or "Label=URL" (e.g.
    "Money Gate stav=https://…/money-gate"). `pr` is accepted for call-compatibility
    but NOT rendered (the user wants the live view, not the code/diff). Structure is
    fixed here so every card is consistent regardless of who calls it. No @mention
    here — send() prepends it."""
    tickets = tickets or []
    # Show only the repo NAME (last path segment), not "owner/name": the @mention
    # send() prepends already names the person, so an "owner/" prefix repeats it
    # (e.g. "@Zbynek Drlik … zbynekdrlik/bakerion-ai" said "zbynek" twice).
    repo_name = (_clean(repo) or "?").rstrip("/").split("/")[-1] or "?"
    lines = ["🚀 **%s** — %s" % (repo_name, _plural_done(len(tickets) or 1))]
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

    # Deploy line leads with the DEPLOYED VERSION (the fact the user actually wants
    # — "which version went live?"). The PR number was removed at the user's request
    # (noise). `pr` is still accepted in the signature so callers don't break.
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
    proj = (_clean(project) or "?").rstrip("/").split("/")[-1] or "?"
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


# --- send ----------------------------------------------------------------
def _post_discord(token, channel, content):
    """POST one message to one Discord channel/thread. Returns True on success.
    Discord REQUIRES a User-Agent — Cloudflare 403s the default "Python-urllib/*"
    ("error code: 1010"); a DiscordBot UA (per spec) gets through (the same reason
    the curl-based hook works). Never raises."""
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/channels/%s/messages" % channel,
            data=json.dumps({"content": content}).encode(),
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
    # Primary owner first, then the parallel mirror recipients — each gets the SAME
    # body in THEIR OWN thread with THEIR OWN @mention.
    targets = [owner] + mirror_owners(env, owner)

    # dry-run never claims dedup (so previews / tests stay re-runnable). One line per
    # target (a single line when no mirror is configured — the unchanged contract).
    if dry_run:
        print("\n".join((mention_prefix(env, t) + (body or ""))[:_MAX_CONTENT]
                         for t in targets))
        return "dry-run"

    # Claim FIRST so a racing duplicate can't double-post; RELEASE only when the
    # primary provably never sent (no token / no channel), so a transient failure
    # can NOT re-send (a timeout can fire AFTER Discord accepted the message).
    if dedup_key and not _dedup_claim(dedup_key):
        return "dedup"

    token = env.get("DISCORD_BOT_TOKEN", "")
    primary_channel = notification_channel(env, owner)
    if not token or not primary_channel:
        _dedup_release(dedup_key)
        return "no-config"

    # Primary send determines the return status; mirror sends are best-effort (a
    # mirror failure never fails the whole notification, never releases the dedup).
    status = "sent" if _post_discord(
        token, primary_channel, (mention_prefix(env, owner) + (body or ""))[:_MAX_CONTENT]) else "error"
    for t in targets[1:]:
        ch = notification_channel(env, t)
        if ch and ch != primary_channel:
            _post_discord(token, ch, (mention_prefix(env, t) + (body or ""))[:_MAX_CONTENT])
    return status
