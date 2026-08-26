"""Question-lifecycle JOB family + owner-answer/foreign-session helpers
(issue #433 item G, step 7).

Extracted VERBATIM from ``watchdog/__init__.py``: the prune / re-ping jobs
(``prune_answered_questions``, ``reping_stale_questions`` and their
``_in_sleep_window`` helper — plus the ``reping_owner_decision_tickets``
tombstone, a permanent no-op since ``#707`` retired the owner-decision
digest), the ``#449`` never-silent owner-answer floor
(``_orphan_answer_reason`` / ``_orphan_ping_text``), the FOREIGN-user hosted-map
helpers (``_foreign_user`` / ``_foreign_session_info`` / ``_foreign_questions`` /
``_foreign_drop_question``), and the transcript-timestamp readers those jobs use
(``_last_human_prompt_ts`` / ``_last_real_turn_ts`` / ``_transcript_for_session``).
This is the question-lifecycle family, distinct from job 7's live DELIVERY flow
(``deliver_discord_replies`` and the card/flag cluster), which stays in
``__init__`` / steps 8-9.

Direction: BACK-REFERENCE (``import watchdog`` + call-time ``watchdog.<name>``).
Every reference to a name that was a top-level ``watchdog`` name before the split
-- co-moved step-7 helpers (``_in_sleep_window``, ``_last_human_prompt_ts``,
``_transcript_for_session``), an
``__init__``-resident constant (``ORPHAN_ANSWER_WINDOW_S``, ``QUESTION_REPING_S``,
``OWNER_DECISION_LABELS``, ``AUTOPILOT_SKIP_EXCL``, ``_MACHINE_PROMPT_EXACT``,
``_MACHINE_PROMPT_PREFIXES``, ``_COMPACT_CONTINUATION_PREFIX``, ``_REAL_TURN_TYPES``),
an ``__init__``-resident function (``_box_authority``), or a facade re-export from
another submodule (``clean_reply_text`` / ``_snowflake_ts`` <- discord_api;
``encode_project_dir`` / ``project_label`` <- transcripts; ``_cache_repo_roots`` /
``_gh_env`` <- cross_stream) -- is written call-time ``watchdog.<name>`` so every
existing ``monkeypatch.setattr(watchdog, ...)`` / ``patch.object(wd, ...)`` seam
stays live (design C3/C5, the #1 hazard). The one def-time default
(``prune_answered_questions``'s ``projects_dir=PROJECTS_DIR``) is a module-top
``from watchdog import PROJECTS_DIR`` -- ``PROJECTS_DIR`` is bound in ``__init__``
above the facade re-import position, so this is cycle-free, and the default was
never affected by package-attr patching (tests inject via the param). All ``notify``
imports are function-local and move verbatim; no name disappears from the package
namespace (C2 re-export in ``__init__``).
"""
import json
import os
import re
from pathlib import Path

import watchdog
from watchdog import PROJECTS_DIR


def _orphan_answer_reason(msg, allowed_ids, qmap, cardmap, question_channels,
                          channel, now, posted_ids=None):
    """Classify `msg` as an owner ANSWER ATTEMPT to OUR OWN ❓ card that can
    no longer be routed (#449) — returns "untracked-ref", else None. Pure and
    deliberately NARROW.

    #652 SCOPES this to a reply to a card THIS box actually posted, reversing
    #449's two over-broad firings that spammed a SHARED `-q` thread (david1,
    david2, codex-bridge, … all post to and fetch ONE thread, each running
    its own job 7):

    - a PLAIN non-reply message no longer fires at all — in a shared thread
      the owner and the stream deliberately converse, and "my si tam chceme
      aj pisat" must never trigger a bot warning (the old "not-a-reply"
      verdict is retired);
    - an explicit reply fires "untracked-ref" ONLY when its referenced id is
      in `posted_ids` — the bounded per-box memory of ❓ card ids THIS box
      posted (deliver_discord_replies' state["dq_posted"]). A SIBLING box's
      card is "untracked by me" but was NEVER ours, so we stay silent; only
      the box(es) that posted a card can react to a reply to it — fleet-wide
      at-most-ONCE by construction for the ordinary single-owner card, and
      at-most-TWICE for the historical HOSTED-user shape (a hosted card id is
      folded into both the host's and the hosted user's own dq_posted; no live
      hosted stream today, and still far better than the pre-#652 N-box spam).
      The genuine #449 case (our own card dropped past grace, late answer) is
      preserved up to _refresh_posted_memory's retention window — see it for
      the exact bound and the accepted >window residual.

    Gates kept from #449: scoped to QUESTION channels only (the per-owner
    `-q` threads, #296); owner-authored, usable text, recent
    (ORPHAN_ANSWER_WINDOW_S via the message's own snowflake — an unparsable
    id reads as too old, skip). A reply to another HUMAN's message stays
    quiet, though a reply whose ref is in `posted_ids` is by definition our
    own bot card, so that guard is only a defensive backstop now."""
    if channel not in question_channels or not isinstance(msg, dict):
        return None
    mid = str(msg.get("id") or "").strip()
    author_id = str((msg.get("author") or {}).get("id") or "").strip()
    if not mid or author_id not in allowed_ids:
        return None
    if not watchdog.clean_reply_text(msg.get("content")):
        return None
    sts = watchdog._snowflake_ts(mid)
    if not sts or now - sts > watchdog.ORPHAN_ANSWER_WINDOW_S:
        return None
    ref = str((msg.get("message_reference") or {}).get("message_id") or "").strip()
    if not ref:
        return None                    # #652: a non-reply never fires
    if ref in qmap or ref in cardmap:
        return None                    # tracked — the normal flows own it
    # #652: fire ONLY for a reply to a card THIS box posted. A sibling box's
    # card in the shared thread was never in our posted memory — stay silent.
    if ref not in (posted_ids or ()):
        return None
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
    """The Slovak owner ping for an unroutable answer attempt to OUR OWN ❓
    card (#449) — the user must never learn about a lost answer only from
    silence. `reason` is retained for call compatibility but no longer
    branches: post-#652 `_orphan_answer_reason` returns only "untracked-ref"
    (the "not-a-reply" verdict was retired), so a single message renders for
    any reason."""
    frag = watchdog.clean_reply_text((msg or {}).get("content"))[:120] or "(bez textu)"
    return ("⚠️ Tvoja Discord odpoveď («%s») sa už nedá priradiť — pôvodná "
            "otázka medzitým prestala byť sledovaná (zodpovedaná v termináli "
            "alebo nahradená novšou). Ak stále platí, odpovedz prosím na "
            "NAJNOVŠIU ❓ otázku v tomto vlákne, alebo ju napíš priamo do "
            "terminálu." % frag)


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
    machine_prefixes = (watchdog._MACHINE_PROMPT_PREFIXES if not extra_human_prefixes
                        else tuple(p for p in watchdog._MACHINE_PROMPT_PREFIXES
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
        if (not t or t in watchdog._MACHINE_PROMPT_EXACT
                or any(t.startswith(p) for p in machine_prefixes)
                or t.startswith(watchdog._COMPACT_CONTINUATION_PREFIX)):
            continue
        try:
            ep = datetime.fromisoformat(
                str(e.get("timestamp")).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if best is None or ep > best:
            best = ep
    return best


# #522 -- a Discord-relayed answer IS a genuine user answer (the human answered,
# just via the phone), so for the "did the user actually answer" question it
# counts as human -- mirrors `compact._COMPACT_DISCORD_ANSWER_PREFIXES` /
# `_compact_recent_human_activity`'s own `extra_human_prefixes` choice. This is
# the OPPOSITE of `_goal_autoarm_recent_human_activity`'s stricter "typed
# DIRECTLY at the terminal" question (which deliberately excludes them).
_DISCORD_ANSWER_PREFIXES = (
    "Odpoveď z Discordu:", "Odpoveď užívateľa na tvoju otázku")


def _is_genuine_human_prompt(entry, extra_human_prefixes=_DISCORD_ANSWER_PREFIXES):
    """#522 -- per-ENTRY sibling of `_last_human_prompt_ts`'s own inner filter:
    True iff `entry` is a genuinely-HUMAN user answer (direct terminal typing OR
    a Discord relay by default), NOT a machine injection. A `/goal` re-poke, a
    bare `continue`, a `<task-notification>`, a bounce/backstop/park nudge, a
    `/goal ` echo, a Stop-hook rejection, a `/compact` continuation summary, an
    isMeta / isCompactSummary / tool_result `user` entry are all transparent
    (return False) -- the #491/#366 causal discipline that keeps a goal re-poke
    from ever reading as "the user answered". Reuses the SAME
    `watchdog._MACHINE_PROMPT_*` constants as `_last_human_prompt_ts`, so a new
    machine prefix added there applies here too (no divergent copy). NEVER raises.

    `extra_human_prefixes` names entries in `_MACHINE_PROMPT_PREFIXES` to treat
    as HUMAN for THIS call -- default the two Discord-answer prefixes (a phone
    answer breaks a repoke streak just as a typed one does), exactly the choice
    `_compact_recent_human_activity` makes."""
    if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("isMeta"):
        return False
    if entry.get("isCompactSummary"):
        return False
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str):
        text = c
    elif isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        text = " ".join(b.get("text", "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text")
    else:
        return False
    t = text.strip()
    if not t:
        return False
    machine_prefixes = (watchdog._MACHINE_PROMPT_PREFIXES if not extra_human_prefixes
                        else tuple(p for p in watchdog._MACHINE_PROMPT_PREFIXES
                                   if p not in extra_human_prefixes))
    if (t in watchdog._MACHINE_PROMPT_EXACT
            or any(t.startswith(p) for p in machine_prefixes)
            or t.startswith(watchdog._COMPACT_CONTINUATION_PREFIX)):
        return False
    return True


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
        if not isinstance(e, dict) or e.get("type") not in watchdog._REAL_TURN_TYPES:
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
    p = Path(projects_dir) / watchdog.encode_project_dir(cwd) / (sid + ".jsonl")
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
                        " (grace)" % (str(qid)[-6:], watchdog.project_label(gcwd)))
    for qid, rec in list(qmap.items()):
        if not isinstance(rec, dict):
            continue
        sid = str(rec.get("session") or "")
        cwd = str(rec.get("cwd") or "")
        qts = rec.get("ts") or 0
        if not sid or not cwd:
            continue
        tpath = watchdog._transcript_for_session(projects_dir, sid, cwd)
        if tpath is None:
            continue
        hts = watchdog._last_human_prompt_ts(tpath)
        if hts and hts > qts + 30:       # grace: never race the ping's own turn
            if not dry_run:
                grace_question(qid)     # #449: routable for 24h, never lost
            logs.append("question answered in-session — pruned %s [%s]"
                        " (grace)" % (str(qid)[-6:], watchdog.project_label(cwd)))
    return logs


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
    reping = watchdog.QUESTION_REPING_S if reping is None else reping
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
    night = watchdog._in_sleep_window(now)
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
                    % (str(qid)[-6:], status, watchdog.project_label(cwd)))
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




def reping_owner_decision_tickets(*_args, **_kwargs):
    """PERMANENT NO-OP -- the daily owner-decision digest (#461) is RETIRED
    (#707, owner ruling 2026-08-26). Do not resurrect.

    The digest addressed a BOX-WIDE, per-project ticket roundup to
    ``account_owner`` -- the first-owner-seen pane-scan COIN FLIP
    (``watchdog/__init__.py``'s own comment: ">1 -> account_owner is a coin
    flip, not an answer") -- with none of the ``owners_seen`` ambiguity guard
    its siblings carry (``reping_stale_questions`` /
    ``deliver_pending_done``). On dev2, which hosts THREE owners' tmux
    sessions, the flip picked David and delivered montalu client-ticket
    content into his Discord thread (2026-08-26) -- a cross-subject
    information leak. #489 had gated only reduced-authority BOXES; a
    full-authority box hosting multi-owner sessions stayed open. The owner
    ordered the WHOLE message class abolished ("CHcem aby si tento druh
    sprav zrusil"), not a routing fix -- consistent with #693/#704: the
    footer `U N` badge and the per-ticket ❓ pings are the critical paths
    and STAY; the digest was always a "daily nicety" by its own comment.

    Retirement shape = the #400 ``notify-compact-request.sh`` pattern: the
    name stays importable for any stale caller, the body does NOTHING -- no
    gh fetch, no state read/write, no send_fn call, any call shape accepted.
    Belt-and-braces: ``notify.SUPPRESSED_ALERT_PREFIXES`` additionally
    denylists the ``owner-decision-digest`` dedup_key class at the send()
    chokepoint, so even STALE code on a not-yet-redeployed box can never
    ping (machine channel keeps the decision: journal + the `suppressed`
    delivery-log line). The ``_fetch_owner_decision_tickets`` /
    ``_owner_decision_digest_block`` helpers, the
    ``airuleset._watchdog_owner_decision_fetch`` wiring and run_once's
    ``owner_decision_fetch`` param + registry entry were REMOVED outright
    (dead code, mvp-philosophy). ``OWNER_DECISION_LABELS`` OUTLIVES the
    digest -- job 32's mechanical U-label clear (``watchdog/u_labels.py``)
    and the footer `U N` decision-subset invariant still consume it."""
    return []
