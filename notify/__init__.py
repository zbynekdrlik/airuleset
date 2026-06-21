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


def _plural_done(n):
    if n == 1:
        return "ticket dokončený"
    return "%d tickety dokončené" % n


def compose_autopilot_card(repo, tickets, pr=None, version=None, merge_sha=None,
                           review_ok=True, done=None, remaining=None):
    """Build the canonical per-ticket completion card (Slovak, Discord markdown).

    `tickets` is a list of dicts {n, title, goal, achieved}. Structure is fixed
    here so every card is consistent regardless of who calls it. No @mention here —
    send() prepends it."""
    tickets = tickets or []
    # Show only the repo NAME (last path segment), not "owner/name": the @mention
    # send() prepends already names the person, so an "owner/" prefix repeats it
    # (e.g. "@Zbynek Drlik … zbynekdrlik/bakerion-ai" said "zbynek" twice).
    repo_name = (_clean(repo) or "?").rstrip("/").split("/")[-1] or "?"
    lines = ["🚀 **%s** — %s" % (repo_name, _plural_done(len(tickets) or 1))]
    for t in tickets:
        n = t.get("n")
        title = _clean(t.get("title"))[:_FIELD_CAP]
        goal = _clean(t.get("goal"))[:_FIELD_CAP] or "—"
        achieved = _clean(t.get("achieved"))[:_FIELD_CAP] or "—"
        head = "🎫 **#%s — %s**" % (n, title) if title else "🎫 **#%s**" % n
        lines += ["", head, "> 🎯 **Cieľ:** %s" % goal,
                  "> ✅ **Dosiahnuté:** %s" % achieved]

    # (The "🔍 Double-review" line was removed at the user's request: a card only
    # ever fires on a CLEAN merge, so the line was always ✅ — pure repetition the
    # user does not need to re-read. `review_ok` is kept in the signature so the
    # worker's `--review` arg stays valid, but it no longer prints.)

    deploy = []
    if pr:
        deploy.append("PR #%s" % _clean(str(pr)))
    if merge_sha:
        deploy.append("`%s`" % _clean(str(merge_sha))[:12])
    v = _clean(version)
    if v and v not in ("—", "-"):
        deploy.append("nasadené **%s**" % v)
    # Show the facts we have; if none (a bare merge with no PR/version), say so
    # plainly rather than the misleading "bez nasadenia".
    lines.append("📦 " + (" · ".join(deploy) if deploy else "PR zmergnutý"))

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
    r"|service unavailable"
    r"|\b(502|503|529)\b)",
    re.IGNORECASE)


def is_api_error(text):
    """True if `text` (a turn's final assistant message) is a real Claude Code API
    error that stopped the work — the concrete signal the notifier keys on. Precise
    on purpose: a normal message that merely mentions 'rate limiter' is NOT an
    error (the false positive that produced spam)."""
    if not text:
        return False
    t = str(text).strip()
    if len(t) > 600:   # CC API-error lines are short; a long message is normal prose
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
def send(body, env=None, owner=None, dedup_key=None, dry_run=False):
    """Prepend the owner @mention to `body` and POST it to the Discord
    notification channel. Deduped on `dedup_key`. Returns a short status string
    ('sent' / 'dedup' / 'dry-run' / 'no-config' / 'error'). Never raises."""
    env = _read_env() if env is None else env
    content = (mention_prefix(env, owner) + (body or ""))[:_MAX_CONTENT]

    # dry-run never claims dedup (so previews / tests stay re-runnable).
    if dry_run:
        print(content)
        return "dry-run"

    # Claim FIRST so a racing duplicate can't double-post; RELEASE on any
    # non-success so a transient failure can be retried (the card is "vzdy").
    if dedup_key and not _dedup_claim(dedup_key):
        return "dedup"

    token = env.get("DISCORD_BOT_TOKEN", "")
    channel = env.get("DISCORD_NOTIFICATION_CHANNEL_ID", "")
    if not token or not channel:
        _dedup_release(dedup_key)
        return "no-config"
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/channels/%s/messages" % channel,
            data=json.dumps({"content": content}).encode(),
            method="POST",
            headers={"Authorization": "Bot " + token,
                     "Content-Type": "application/json",
                     # Discord's API REQUIRES a User-Agent, and Cloudflare blocks
                     # the default "Python-urllib/*" with 403 "error code: 1010".
                     # A DiscordBot UA (per Discord's spec) gets through — the same
                     # reason the curl-based hook works.
                     "User-Agent": "DiscordBot (https://github.com/zbynekdrlik/airuleset, 1.0)"})
        urllib.request.urlopen(req, timeout=6).read()
        return "sent"
    except Exception:
        # Do NOT release the dedup claim here: a timeout can fire AFTER Discord
        # already accepted the message, so releasing would re-send a duplicate on
        # the next merge report. No-dups beats a possibly-lost card on a genuine
        # (rare) transient failure. ("no-config" above DID release — it provably
        # never sent.)
        return "error"
