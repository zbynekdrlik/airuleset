"""#1257 — the OWNER-STOP latch: a session the owner stopped, paused or left is
never resumed by a machine keystroke (owner 20.10.2026: "nejaké tvoje procesy
spustili a dali pokračovať gk-quality/gk-infra aj keď som ich mal /exit … to
nesmieš!").

SET from STRUCTURED facts, never pane prose (`classify`, `_exits`):
  exit          a pane at a bare shell no live claude pane's cwd matches: the
                cwd's newest session ended (CC 2.1.306 writes NO `/exit` record,
                probed 26.10.2026; a `/exit`/`/quit` record still counts);
  goal-none     the owner's `/goal clear` / `/goal` answered `No goal set`;
  goal-cleared  a `Goal cleared:` that is not the watchdog's own #522 disarm
                (`state["goal_disarmed_q"]`, MACHINE_CLEAR_WINDOW_S, read live);
                a clear `refresh` once matched is kept in the session's record
                (`mclears`) and stays machine after the veto is reaped;
  interrupt     `[Request interrupted by user…]`;
  pause         a genuine owner prompt that is EXACTLY one `STOP_PHRASES` entry
                once lowercased, de-accented, stripped of punctuation and of
                the `_FILLERS` ("pauzni sa keď môžeš", "Stop!", "skonči na
                dnes"). A prompt that merely CONTAINS a stop word
                (`STOP_WORD_RX`: "prestaň sa pýtať a pokračuj", "oprav stop
                button") is NEUTRAL: it neither sets nor clears.
CLEARED only by a later OWNER act: any other genuine prompt
(`questions._is_genuine_human_prompt` — a machine prompt, `continue`, a nudge
pointer, a task notification never counts), any other owner slash command, or
the owner's own `Goal set:` arm. While latched no machine keystroke can type a
slash command or a `/goal`, so a later command record is the owner's. A
session with neither a fact nor an owner act in its WHOLE transcript (a
`claude` the owner restarted but has not typed into) inherits the cwd's
previous session.

ENFORCED at the ONE send-keys builder `tmux_io.keys` (`withhold`: every kind,
recovery and cap-exempt included; only the owner's own Discord reply passes),
and on the paths that bypass it: `relaunch_pane`, resurrect, `deliver_goal`.
`refresh` (top of every sweep) maps panes to transcripts, persists
`state[STATE_KEY]` per session and journals ONE line per episode. A leaf."""
import os
import re
from pathlib import Path

STATE_KEY = "owner_stop_latch"
TAIL_BYTES = 1024 * 1024
TAIL_ENTRIES = 400
RECORD_TTL_S = 7 * 24 * 3600
MACHINE_CLEAR_WINDOW_S = 600
MAX_MCLEARS = 20        # matched #522 disarm clears kept per session record
STOP_PHRASES = frozenset((   # de-accented: "skonči" reads "skonci"
    "stop", "stop here", "stop now", "pause", "halt", "pauza", "pauzni",
    "pauzni sa", "zastav", "zastav sa", "stoj", "skonci", "koniec",
    "nepokracuj", "skonci na dnes"))
_FILLERS = ("ked mozes", "na dnes", "for today", "for now", "prosim", "teraz",
            "zatial", "please")
STOP_WORD_RX = re.compile(   # the Stop HOOK / a stop CONDITION is vocabulary
    r"(?<![\w-])(?:pauzni(?:te)?|pauz[au]|zastav(?:te)?|stopni(?:te)?|"
    r"stop(?!\s*(?:hook|condition|podmien))|nepokra[cč]uj(?:te)?|skon[cč]i(?:te)?|"
    r"presta[nň](?:te)?|pause|halt|don'?t continue|do not continue)(?![\w-])",
    re.IGNORECASE)
_STOP_COMMANDS = ("exit", "quit")
_CMD_RX = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
_INTERRUPT = "[Request interrupted by user"

_PANES = {}        # pane_id -> context of THIS sweep (`refresh`)
_SWEEP = {}        # {"state": the sweep state} for a caller without `state`
_MEMO = {}         # (pane_id, file stamps) -> latch
_WITHHELD = set()  # pane ids already journalled by `withhold` this sweep


def reset():   # a new sweep, or a test
    _PANES.clear()
    _SWEEP.clear()
    _MEMO.clear()
    _WITHHELD.clear()


def _ts(entry):
    from datetime import datetime
    try:
        return datetime.fromisoformat(
            str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _text(entry):
    """The TOP-LEVEL text of a `user`/`system` entry (never a nested
    tool_result: a session that grepped another transcript is not its state)."""
    if entry.get("type") == "system":
        c = entry.get("content")
        return c if isinstance(c, str) else ""
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_stop_phrase(text):
    """True iff `text` is EXACTLY one STOP_PHRASES entry once lowercased,
    de-accented, stripped of punctuation and of the politeness/time fillers."""
    import unicodedata
    t = unicodedata.normalize("NFKD", text.lower())
    t = re.sub(r"[^\w\s]", " ", "".join(c for c in t if not unicodedata.combining(c)))
    t = re.sub(r"\b(?:%s)\b" % "|".join(_FILLERS), " ", t)
    return " ".join(t.split()) in STOP_PHRASES


def classify(entry, machine_clears=(), known=()):
    """("stop", fact) | ("clear", why) | ("machine", ts) | None for ONE
    transcript entry. `machine_clears` are live #522 disarm times; `known` are
    the exact times of clears an earlier `refresh` matched to one."""
    if not isinstance(entry, dict) or entry.get("type") not in ("user", "system"):
        return None
    text = _text(entry).strip()
    if not text:
        return None
    if text.startswith(_INTERRUPT):
        return ("stop", "interrupt")
    if text.startswith(("<command-name>", "<command-message>")):
        m = _CMD_RX.search(text)
        name = m.group(1).lstrip("/").lower() if m else ""
        if name in _STOP_COMMANDS:
            return ("stop", "exit")
        return None if name in ("", "goal") else ("clear", "/" + name)
    import watchdog
    if text.startswith(watchdog._GOAL_LCS_OPEN) or text.startswith(
            watchdog.GOAL_ARM_ACTIVE_PREFIX):
        if "no goal set" in text.lower():
            return ("stop", "goal-none")
        from watchdog import goal_scan
        mark = goal_scan._parse_goal_marker(text, entry.get("type") == "system")
        if mark and mark.get("state") == "set":
            return ("clear", "goal-set")
        if mark and mark.get("clear_kind") == "user":
            ts = _ts(entry)
            if ts is not None and (ts in known or any(
                    isinstance(c, (int, float)) and 0 <= ts - c <= MACHINE_CLEAR_WINDOW_S
                    for c in machine_clears)):
                return ("machine", ts)           # the watchdog's own #522 disarm
            return ("stop", "goal-cleared")
        return None
    from watchdog import questions
    if not questions._is_genuine_human_prompt(entry):
        return None
    if _is_stop_phrase(text):
        return ("stop", "pause")
    return None if STOP_WORD_RX.search(text) else ("clear", "prompt")


def scan(entries, rec=None, machine_clears=()):
    """(latch, saw, found): replay `entries` (oldest → newest) on top of the
    persisted `rec`; an entry at or before `rec["ts"]` is already accounted
    for. `latch` is {"fact", "ts"} or None; `saw` is True when a fact, an owner
    act or a persisted latch was seen; `found` lists the clears matched to the
    watchdog's own disarm (`rec["mclears"]` keeps them machine for good)."""
    rec = rec if isinstance(rec, dict) else {}
    latch = dict(rec) if rec.get("fact") else None
    floor = latch.get("ts") if latch else None
    known = set(c for c in rec.get("mclears") or () if isinstance(c, (int, float)))
    saw, found = latch is not None, []
    for e in entries or ():
        ts = _ts(e) if isinstance(e, dict) else None
        if isinstance(floor, (int, float)) and (ts is None or ts <= floor):
            continue
        c = classify(e, machine_clears, known)
        if c is None or c[0] == "machine":
            found += [c[1]] if c else []
            continue
        saw = True
        latch = {"fact": c[1], "ts": ts or 0} if c[0] == "stop" else None
    return latch, saw, found


def _tail(path):
    from watchdog import transcripts
    return transcripts._read_jsonl_byte_tail(str(path), TAIL_BYTES, TAIL_ENTRIES)


def _whole(path, entries):
    """True when `entries` is the WHOLE session (nothing older was cut off)."""
    try:
        return os.path.getsize(path) <= TAIL_BYTES and len(entries) < TAIL_ENTRIES
    except OSError:
        return False


def _machine_clears(state, sid):
    """The watchdog's own #522 disarm time for `sid`, read LIVE from
    `state["goal_disarmed_q"]` on every evaluation: `qrepoke` may write the veto
    after `refresh` in the same sweep."""
    dq = state.get("goal_disarmed_q") if isinstance(state, dict) else None
    m = dq.get(sid) if isinstance(dq, dict) and sid else None
    return [m["disarmed_ts"]] if isinstance(m, dict) and isinstance(
        m.get("disarmed_ts"), (int, float)) else []


def _evaluate(ctx):
    """(latch, found) for one pane context {tpath, prev, rec, prev_rec, state}:
    `found` = the session's clears matched to the watchdog's own disarm."""
    tpath = ctx.get("tpath")
    if not tpath:
        return None, []
    state, prev = ctx.get("state"), ctx.get("prev")
    entries = _tail(tpath)
    latch, saw, found = scan(entries, ctx.get("rec"),
                             _machine_clears(state, tpath.stem))
    if saw or not prev or not _whole(tpath, entries):
        return latch, found
    inherited = scan(_tail(prev), ctx.get("prev_rec"),
                     _machine_clears(state, prev.stem))[0]
    return (dict(inherited, inherited=True) if inherited else None), found


def _latch_of(ctx):
    return _evaluate(ctx)[0]


def _two_newest(projects_dir, cwd):
    from watchdog import transcripts
    d = Path(projects_dir) / transcripts.encode_project_dir(cwd)
    try:
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    except OSError:
        return None, None
    return (files[0] if files else None), (files[1] if len(files) > 1 else None)


def _context(projects_dir, cwd, state, recs=None):
    tpath, prev = _two_newest(projects_dir, cwd)
    if recs is None:
        recs = state.get(STATE_KEY) if isinstance(state, dict) else None
    recs = recs if isinstance(recs, dict) else {}
    sid = tpath.stem if tpath else None
    return {"cwd": cwd, "tpath": tpath, "prev": prev, "sid": sid, "state": state,
            "rec": recs.get(sid), "prev_rec": recs.get(prev.stem) if prev else None}


def cwd_latch(projects_dir, cwd, state=None):
    """The latch for `cwd`'s current session (`deliver_goal`, resurrect); a
    caller without `state` reads the sweep's (`refresh`)."""
    state = state if state is not None else _SWEEP.get("state")
    try:
        return _latch_of(_context(projects_dir, cwd, state))
    except Exception:          # noqa: BLE001 -- unreadable = no proof of a stop
        return None


def _stamp(path):
    try:
        st = os.stat(path)
        return (str(path), st.st_size, st.st_mtime_ns)
    except (OSError, TypeError):
        return None


def pane_latch(pane_id):
    """The latch for a pane `refresh` mapped this sweep, re-read when its
    transcripts changed since (memoized on their size+mtime); None otherwise."""
    ctx = _PANES.get(pane_id)
    if not ctx:
        return None
    key = (pane_id, _stamp(ctx.get("tpath")), _stamp(ctx.get("prev")))
    if key[1] is None:
        return None
    if key not in _MEMO:
        try:
            _MEMO[key] = _latch_of(ctx)
        except Exception:      # noqa: BLE001 -- unreadable = no proof of a stop
            _MEMO[key] = None
    return _MEMO[key]


def withhold(pane_id, logs=None):
    """True iff `pane_id` is latched: the caller types NOTHING. Journals ONE
    `owner-stop latch: withheld` line per pane per sweep."""
    latch = pane_latch(pane_id)
    if not latch:
        return False
    if pane_id not in _WITHHELD and isinstance(logs, list):
        _WITHHELD.add(pane_id)
        logs.append("owner-stop latch: withheld every keystroke into %s (%s)"
                    % (pane_id, latch.get("fact")))
    return True


def _clock(ts):
    import time
    return time.strftime("%d.%m. %H:%M", time.localtime(ts)) if ts else "?"


def _exits(panes, projects_dir, run):
    """[(cwd, tpath)] — every tmux pane at a bare shell (no claude) whose cwd no
    live claude pane serves: the cwd's newest transcript is a session that ended
    there. Read from the sweep's memoized pane inventory."""
    from watchdog.resurrect import _SHELL_COMMANDS
    from watchdog.tmux_io import _pane_inventory_raw
    live = {str(pid) for pid, _c in panes or ()}
    live_cwds = {str(c) for _p, c in panes or ()}
    out, done = [], set()
    for line in (_pane_inventory_raw(run) or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[0].strip() in live:
            continue
        cmd, cwd = parts[1].strip().lstrip("-"), parts[2].strip()
        if cmd not in _SHELL_COMMANDS or not cwd or cwd in live_cwds or cwd in done:
            continue
        done.add(cwd)
        tpath, _prev = _two_newest(projects_dir, cwd)
        if tpath:
            out.append((cwd, tpath))
    return out


def refresh(state, panes, projects_dir, now, dry_run=False, run=None):
    """Once per sweep, BEFORE any keystroke job: latch every session whose
    claude left its pane (`_exits`, needs `run`), map every claude pane to its
    transcripts, persist each session's latch and journal its transitions (SET
    once per episode, cleared once). Returns the journal lines."""
    reset()
    logs = []
    if not isinstance(state, dict):
        return logs
    _SWEEP["state"] = state
    recs = state.get(STATE_KEY)
    recs = dict(recs) if isinstance(recs, dict) else {}
    try:
        exits = _exits(panes, projects_dir, run) if run else ()
    except Exception as e:         # noqa: BLE001 -- a pane-scan fault never stops the sweep
        exits = ()
        logs.append("owner-stop latch: exit scan failed (%r), no exit latched" % (e,))
    for cwd, tpath in exits:
        rec = recs.get(tpath.stem)
        if not isinstance(rec, dict) or not rec.get("fact"):
            try:   # the session's END: nothing in it happened after the exit
                rec = dict(rec or {}, fact="exit", ts=max([os.path.getmtime(tpath)] + [
                    t for t in (_ts(e) for e in _tail(tpath)) if t]))
            except OSError:
                continue
            logs.append("owner-stop latch %s sid=%s -> SET (exit: no claude in the "
                        "pane): the next session there gets no machine keystroke "
                        "until the owner types" % (os.path.basename(cwd) or cwd,
                                                     tpath.stem))
        if not dry_run:
            recs[tpath.stem] = dict(rec, seen=now)
    seen = set()
    for pid, cwd in panes or ():
        try:
            ctx = _context(projects_dir, cwd, state, recs)
            latch, found = _evaluate(ctx) if ctx["sid"] else (None, [])
        except Exception as e:     # noqa: BLE001 -- one bad pane never stops the sweep
            logs.append("owner-stop latch %s -> unreadable (%r), not latched" % (pid, e))
            continue
        sid = ctx["sid"]
        if not sid:
            continue
        _PANES[pid] = ctx
        seen.add(sid)
        old = recs.get(sid) if isinstance(recs.get(sid), dict) else {}
        where = "%s sid=%s" % (os.path.basename(str(cwd)) or cwd, sid)
        mcl = sorted(set(old.get("mclears") or ()) | set(found))[-MAX_MCLEARS:]
        if latch:
            same = (old.get("fact"), old.get("ts")) == (latch.get("fact"),
                                                        latch.get("ts"))
            if not same:
                logs.append("owner-stop latch %s -> SET (%s%s, %s): no machine "
                            "keystroke and no /goal arm until the owner types a "
                            "new prompt" % (where, latch["fact"],
                                            " in the previous session"
                                            if latch.get("inherited") else "",
                                            _clock(latch.get("ts"))))
        elif old.get("fact"):
            logs.append("owner-stop latch %s -> cleared (the owner typed a new "
                        "prompt)" % where)
        if not dry_run:
            new = dict({"seen": now}, **({"mclears": mcl} if mcl else {}))
            if latch:
                new.update(fact=latch["fact"], ts=latch.get("ts") or 0)
            if latch or mcl:
                recs[sid] = new
            else:
                recs.pop(sid, None)
            ctx["rec"] = recs.get(sid)
    if not dry_run:
        for sid in [k for k, v in recs.items() if k not in seen and (
                not isinstance(v, dict) or not isinstance(v.get("seen"), (int, float))
                or now - v["seen"] > RECORD_TTL_S)]:
            recs.pop(sid, None)
        if recs:
            state[STATE_KEY] = recs
        else:
            state.pop(STATE_KEY, None)     # no empty namespace in a quiet state
    return logs
