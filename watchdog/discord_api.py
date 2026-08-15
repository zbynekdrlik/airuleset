"""Discord REST + reply-parsing primitives (issue #433 item G, step 6).

Extracted VERBATIM from ``watchdog/__init__.py``: the small Discord-API cluster
(GET channel messages / reaction users, the best-effort ``✅`` react) plus the
pure reply/card parsers and the snowflake-timestamp decoder.

Direction: BACK-REFERENCE (``import watchdog`` + call-time ``watchdog.<name>``),
NOT a pure leaf despite the design's "true leaf" estimate. Two co-moved cross
calls (``clean_reply_text``, ``_discord_get``) and four ``__init__``-resident
constants (``_MENTION_TOKEN_RX``, ``_CONTROL_CHAR_RX``, ``DISCORD_REPLY_MAX_CHARS``,
``_DISCORD_EPOCH_MS``) are reached through the package namespace so every
``monkeypatch.setattr(watchdog, ...)`` / ``patch.object(wd, ...)`` seam stays live
(design C3/C5, the #1 hazard). Three of those constants relocate to
``discord_replies.py`` in step 9, so call-time ``watchdog.<CONST>`` is also the
only step-9-safe choice (the re-export keeps ``watchdog.<CONST>`` resolvable no
matter which module physically owns it). No def-time defaults reference any moved
or co-moved name, so no module-top ``from watchdog import`` is needed and no
import cycle is possible.
"""
import json
import re

import watchdog


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
    s = watchdog._MENTION_TOKEN_RX.sub(" ", str(raw))
    if bot_id:
        s = s.replace("<@%s>" % bot_id, " ").replace("<@!%s>" % bot_id, " ")
    s = watchdog._CONTROL_CHAR_RX.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:watchdog.DISCORD_REPLY_MAX_CHARS]


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
    text = watchdog.clean_reply_text(msg.get("content"), bot_id)
    if not text:
        return None
    return {"reply_id": reply_id, "referenced": ref,
            "session": str(q.get("session") or ""), "cwd": str(q.get("cwd") or ""),
            "channel": str(q.get("channel") or ""), "text": text,
            "question": " ".join(str(q.get("question") or "").split()),
            "asked_ts": q.get("ts") or 0}


def _snowflake_ts(sid):
    """Epoch seconds encoded in a Discord snowflake id; 0 when unparsable
    (a non-numeric test id, garbage) — the caller then skips the orphan
    ping rather than guessing the message's age."""
    s = str(sid or "").strip()
    if not s.isdigit():
        return 0
    return ((int(s) >> 22) + watchdog._DISCORD_EPOCH_MS) / 1000.0


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
        raw = watchdog._discord_get(
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
    text = watchdog.clean_reply_text(msg.get("content"))
    if not text:
        return None
    return {"reply_id": reply_id, "referenced": ref, "repo": repo,
            "issue": issue, "text": text, "channel": str(c.get("channel") or "")}


def fetch_reaction_users(channel, message_id, emoji, token):
    """GET the users who reacted with `emoji` to `message_id` in `channel`.
    [] on any error/missing input — fail-safe, mirrors `fetch_channel_messages`
    (never breaks the poll)."""
    if not channel or not message_id or not token:
        return []
    try:
        from urllib.parse import quote
        raw = watchdog._discord_get(
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
