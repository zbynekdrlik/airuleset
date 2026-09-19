"""gates.lanefill — the Stop-time LANE-FILL gate (#1078 item 1).

THE PROBLEM. On a PARALLEL box the autopilot `/goal` loop is supposed to keep
as many bundle-safe worker lanes live as the box + backlog bear (continuous
refill, skills/autopilot/SKILL.md Step 3.2 / #848). But the only enforcement
was the supervisor's own judgment: a session that serialised implementation
"by box" would end a `⏳ WORKING` / `✅ DONE` turn with ONE live lane while
`slice-quals --count-dispatchable` was 15+. Nothing read that gap at the Stop
boundary — the exact owner complaint repeated three times on 2026-09-18 to the
montalu1 session ("I 19 a len jeden subagent pracuje!", "stále ti to treba
opakovať?"). Prose (the skill + the owner) had failed; the ticket asked for a
mechanical check AT the turn boundary, which only a Stop hook sees.

THE GATE. Runs ONLY when the session is goal-armed AND the box/pane mode is
`parallel` (`sequential` boxes — the controller pane, gk-infra — are EXEMPT by
design, #1031/#1035) AND the turn's last line is `⏳ WORKING` / `✅ DONE`. It
BLOCKS (exit 2) iff there are MORE dispatchable tickets than live lanes AND a
free lane slot AND the final message carries no justification line
(`Dependency: #N …` / `Lane-fill: <reason>`). The block names up to five
dispatchable tickets (with titles) and the two escape lines.

FAIL-OPEN, deliberately (the OPPOSITE of the filing gate's fail-closed): this
hook adds PRESSURE, it does not guard a write. Any unreadable seam — a
transcript / quals / mode / cap read that errors — journals
`lane-fill: unreadable (<why>) — not enforced` to stderr and ALLOWS. A
gh/transcript failure must never wedge the loop.

THIN-ADAPTER shape (gate-family #1020): the bash adapter
`hooks/stop-check-lane-fill.sh` runs `python3 -P -m gates.lanefill` with the
Stop payload on stdin; exit 2 (reason on stderr) blocks, exit 0 allows. See the
#1078 design comment for the full architecture + rejected alternatives.

Dry-run: echo '{"last_assistant_message":"…\n⏳ WORKING","cwd":"/repo","session_id":"s","transcript_path":"/t.jsonl"}' | python3 -m gates.lanefill
"""
import json
import os
import re
import sys
import time

import gates

# The mode that enforces (a sequential pane runs one lane at a time; exempt).
_MODE_PARALLEL = "parallel"

# A justification line makes an under-filled turn INTENTIONAL — the escape the
# block message names. `Dependency: #N …` (a real blocking ref) or
# `Lane-fill: <reason>` (e.g. "all remaining tickets wait on gk review",
# "bounce lane occupies the box"). Matched anywhere in the final message.
_JUSTIF_RE = re.compile(r"(?im)^\s*(?:Dependency:\s*#\d+|Lane-fill:\s*\S)")

# Marker on the LAST non-blank line — the "turn ends ⏳/✅" signal. `✅` alone is
# not enough (a mid-message ✅ row is common in an autopilot turn); the marker
# must be on the tail line, matching stop-check-status-marker.sh's LAST_LINE.
_DONE_RE = re.compile(r"✅\s*(?:DONE|complete|work complete)", re.I)

# Test seam (documented, like ND_FAKE_PANE_CAPTURE): canned dispatchable output
# in the `number<TAB>title` shape of `slice-quals/core-quals --list-dispatchable`
# — a value (inline, "\n"-joined lines) OR "@<path>" to read a file. Lets the
# hook stdin-contract test drive the FULL module with NO gh call. Ignored in
# production (env unset).
_FAKE_QUALS_ENV = "AIRULESET_LANEFILL_FAKE_QUALS"

_MAX_NAMED = 5

# The per-cwd tickets-status cache freshness window (the footer refresh TTL): a
# `dispatchable` set written within this many seconds is trusted as-is (no live
# call). Mirrors the footer's own 120s refresh cadence.
_CACHE_TTL_S = 120

# The bounded live-fallback timeout — a stale-cache turn's one quals call. Kept
# small (< the Stop hook's settings.json timeout) so it fails-open gracefully.
_FALLBACK_TIMEOUT_S = 8

# #1089 — the watchdog goal_mark record is trusted as-is only while its snapshot
# of the transcript is within this window of the transcript's CURRENT mtime; a
# record older than the transcript by > this many seconds is STALE (the session
# wrote turns the dark-watch has not swept yet) → fall back to the seed scan.
_REC_STALE_S = 600

# #1089 — the per-Stop decision journal (observability: "does the gate work?" is a
# file read, not a guess). ENV seam so a test / the push gate isolates it off the
# real box; default `~/.claude/lanefill/decisions.log`, capped like gh-rate.log.
_LANEFILL_DIR_ENV = "AIRULESET_LANEFILL_DIR"
_DECISIONS_LOG = "decisions.log"
_DECISIONS_MAX_BYTES = 512 * 1024        # mirrors cli_gh_rate._JOURNAL_MAX_BYTES


class _Unreadable(Exception):
    """A seam could not be read — the caller journals + fail-opens."""


def _last_marker(msg):
    """`"working"` / `"done"` / `None` from the LAST non-blank line of `msg`."""
    lines = [ln for ln in (msg or "").splitlines() if ln.strip()]
    if not lines:
        return None
    tail = lines[-1]
    if "⏳" in tail:
        return "working"
    if _DONE_RE.search(tail):
        return "done"
    return None


def has_justification(msg):
    """True iff the message carries a `Dependency: #N` / `Lane-fill:` line."""
    return bool(_JUSTIF_RE.search(msg or ""))


def decide(dispatchable, live, cap, mode, goal_armed, last_marker,
           justification_present):
    """PURE. Return ``(block: bool, reason: str)``.

    ``reason`` is the generic escape explanation (the I/O shell prepends the
    named dispatchable-ticket list). ``block`` is True ONLY when ALL hold:
    the session is goal-armed, the pane mode is ``parallel`` (sequential is
    exempt), the turn ends ``⏳``/``✅`` (``last_marker`` in working/done),
    the message carries NO justification, and there are MORE dispatchable
    tickets than live lanes with a free slot (``dispatchable > live`` AND
    ``live < cap``). Every other state allows.
    """
    if not goal_armed:
        return False, ""
    if mode != _MODE_PARALLEL:
        return False, ""                      # sequential exempt (#1031/#1035)
    if last_marker not in ("working", "done"):
        return False, ""                      # only ⏳/✅ turns
    if justification_present:
        return False, ""                      # intentional under-fill
    if dispatchable > live and live < cap:
        return True, _REASON
    return False, ""


_REASON = (
    "Zapíš do poslednej správy dôvod, ak je to zámer:\n"
    "  • `Dependency: #N — <čo blokuje>`  (ticket čaká na iný lane / merge)\n"
    "  • `Lane-fill: <dôvod>`  (napr. \"práve som dispatchol lane tento ťah\" "
    "[živé lane sa ešte nemusia rátať z disku — nedispatchuj znova], \"všetky "
    "ostatné čakajú na gk review\", \"bounce lane drží box\")\n"
    "Inak dispatchni ďalšie `isolation:\"worktree\"` autopilot-worker lane "
    "(continuous refill, SKILL.md Step 3.2)."
)


def _compose(count, live, cap, tickets):
    """The full block message: the fill gap + up to five named dispatchable
    tickets + the escape explanation."""
    free = max(0, cap - live)
    head = ("LANE-FILL: %d dispatchovateľných tiketov, ale len %d živých lane a "
            "%d voľných slotov (cap %d). Paralelný box nesmie skončiť ⏳/✅ turn "
            "s voľnými slotmi a dispatchovateľnými tiketmi bez lane (owner "
            "montalu1 18.9., #1078)." % (count, live, free, cap))
    rows = []
    for num, title in tickets[:_MAX_NAMED]:
        rows.append("  #%s %s" % (num, title))
    more = count - min(count, len(tickets[:_MAX_NAMED]))
    listing = "\n".join(rows)
    if more > 0:
        listing += "\n  … a ďalších %d" % more
    return "%s\nDispatchovateľné tikety:\n%s\n\n%s" % (head, listing, _REASON)


# --------------------------------------------------------------------------- #
# I/O shell — resolve the seven decide() inputs from the Stop payload.
# Each resolver is injectable (tests) with a real default. A read error raises
# _Unreadable → the caller journals + fail-opens.
# --------------------------------------------------------------------------- #
def _rec_mark_state(rec):
    """`"set"` / `"cleared"` / None from a persisted goal_mark record's `mark`."""
    if not isinstance(rec, dict):
        return None
    mark = rec.get("mark")
    if not isinstance(mark, dict):
        return None
    st = mark.get("state")
    return st if st in ("set", "cleared") else None


def _rec_fresh_for(rec, tp):
    """True iff the watchdog record's snapshot (`rec["tmtime"]`) is within
    `_REC_STALE_S` of the transcript's CURRENT mtime — i.e. the dark-watch has
    swept this session recently, so its verdict is authoritative. A record whose
    `tmtime` is NEWER than the transcript (clock skew) is fresh (the safe side).
    Missing/non-numeric `tmtime` or an unreadable transcript → not fresh."""
    if not isinstance(rec, dict):
        return False
    rt = rec.get("tmtime")
    if not isinstance(rt, (int, float)) or isinstance(rt, bool):
        return False
    try:
        tm = os.path.getmtime(tp)
    except OSError:
        return False
    return (tm - rt) <= _REC_STALE_S


def _goal_armed(payload, *, state_path=None, out=None):
    """True iff this session's `/goal` loop is armed — reading the WATCHDOG's
    persisted marker FIRST, the seed scan only as a fallback (#1089).

    The dark-watch (`watchdog/goal.py::goal_dark_watch`) maintains each session's
    newest `/goal` marker INCREMENTALLY and persists it at
    `state["goal_mark"][sid]` in `~/.claude/api-watchdog-state.json`
    (`goal_scan.persisted_goal_mark`, the single source #486). This gate reads
    THAT instead of re-scanning the transcript, because the sessions it targets
    are the 650-730 MB autopilot supervisors whose `/goal` arm marker sits deeper
    than the 32 MB seed cap → `seed_goal_marker` returns `unknown-past-cap` → the
    #1078 gate fail-opened on 100 % of the sessions it was built for.

    Precedence (mirrors `one_glance.resolve_goal_armed`):
      * a FRESH watchdog record (its transcript-mtime snapshot within
        `_REC_STALE_S`) with `mark.state == "set"` → armed (`src=watchdog`), NO
        transcript rescan; `"cleared"` → not armed (`src=watchdog`).
      * MISSING / STALE / no-mark record → the seed scan governs: `found`+`set` →
        armed (`src=seed`); `found`+cleared / `none-bof` → not armed;
        `unknown-past-cap` → if a (stale) watchdog record still says `set` the
        seed cannot disprove it, so honour it (armed, `src=watchdog`) — else
        `_Unreadable` → journal + fail-open, EXACTLY as #1078 (the genuinely
        absent case is unchanged).
      * a missing transcript → not armed.

    `state_path` (test seam) defaults to `watchdog.STATE_PATH`. `out` (test seam,
    like `send_verified`'s) receives `out["src"]` for the decision journal."""
    def _src(s):
        if isinstance(out, dict):
            out["src"] = s
    tp = gates.field_of(payload, "transcript_path", "")
    if not tp:
        _src("no-transcript")
        return False
    sid = gates.field_of(payload, "session_id", "") or ""
    import watchdog.goal_scan as goal_scan
    rec = goal_scan.persisted_goal_mark(sid, state_path=state_path) if sid else None
    rec_state = _rec_mark_state(rec)
    if _rec_fresh_for(rec, tp):
        if rec_state == "set":
            _src("watchdog")
            return True
        if rec_state == "cleared":
            _src("watchdog")
            return False
    _off, mark, status = goal_scan.seed_goal_marker(tp)
    if status == "unknown-past-cap":
        if rec_state == "set":
            _src("watchdog")           # stale record set + seed can't disprove
            return True
        _src("seed")
        raise _Unreadable("goal-armed unknown-past-cap")
    _src("seed")
    return bool(mark) and mark.get("state") == "set"


def _mode(cwd):
    """The pane's EFFECTIVE concurrency mode via the single resolver
    (`cli_concurrency.resolve_mode` — declared window / project lane-resources /
    default parallel; the #1031/#1035 seam)."""
    import cli_concurrency
    return cli_concurrency.resolve_mode(cwd)


def _cap(cwd):
    """The box's lane cap (`watchdog.lane_resources.lane_resource_caps` total —
    the project's `.claude/lane-resources.json` `max_lanes` or the flat default,
    forced to 1 for a sequential pane)."""
    import watchdog.lane_resources as lr
    caps, _reason = lr.lane_resource_caps(cwd)
    return int(caps.get("total", lr.GOAL_LANE_SATURATION_WORKERS))


def _live(payload, cwd, out=None):
    """This session's live worker-lane count (`watchdog.count_live_workers`,
    disk state only — no tmux, no ps). The freshness window is imported from the
    single source (`watchdog.compact.COMPACT_LIVE_WORKER_FRESHNESS_S` = 15 min,
    wide enough that a live worker in a long foreground CI-wait — which writes
    nothing for up to ~9 min — is not misread as dead) rather than re-declared,
    so it can never drift. KNOWN RACE (accepted, mitigated in the block message):
    a worker dispatched in THIS turn whose subagent transcript has not yet landed
    on disk is under-counted — the block's escape names `Lane-fill: práve som
    dispatchol` for that instant, and a false block is fail-open (the session
    continues, never a wrong write).

    `out` (test/journal seam) receives `out["evidence"]` — the `[WorkerLane, …]`
    list `count_live_workers` returns — so the #1089 decision journal can break
    the count down by state (finished / stale / wedged); the RETURN stays the int
    `count` so the `live_fn` decide seam is unchanged."""
    import watchdog
    from watchdog.compact import COMPACT_LIVE_WORKER_FRESHNESS_S
    sid = gates.field_of(payload, "session_id", "") or "unknown"
    count, ev = watchdog.count_live_workers(
        watchdog.PROJECTS_DIR, cwd, sid, time.time(), COMPACT_LIVE_WORKER_FRESHNESS_S)
    if isinstance(out, dict):
        out["evidence"] = ev
    return int(count)


def _parse_quals_lines(text):
    """`(count, [(num, title), …])` from `number<TAB>title` lines. An
    `unmeasurable:` head (a failed dep-meta read) → _Unreadable (fail-open)."""
    tickets = []
    for ln in (text or "").splitlines():
        ln = ln.rstrip("\n")
        if not ln.strip():
            continue
        if ln.startswith("unmeasurable"):
            raise _Unreadable("quals " + ln.strip())
        num, _tab, title = ln.partition("\t")
        num = num.strip()
        if not num.isdigit():
            continue
        tickets.append((int(num), title.strip()))
    return len(tickets), tickets


def _airuleset_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "airuleset.py")


def _cached_dispatchable(cwd):
    """`(count, [(num, title), …])` from a FRESH per-cwd tickets-status cache,
    or None. The footer refresh (`tickets-status --refresh`, run at most every
    `_CACHE_TTL_S`) persists the `dispatchable: [{number, title}]` set next to
    its counts, so the common turn-end reads it here with NO subprocess and NO
    gh — the #1078 integration-review fix (14 parallel boxes must NOT each pay a
    ~15-25s live quals call + a shared-app-token gh burst per turn). None when
    the cache is stale / missing / lacks the key (an older writer) / stores
    `null` (that refresh's dep read was unmeasurable) / is malformed → the caller
    does its bounded live fallback."""
    try:
        import statusbar
        p = statusbar.cache_dir() / (statusbar.cwd_key(cwd) + ".json")
        with open(p) as f:
            entry = json.load(f)
    except Exception:  # noqa: BLE001 — absent / unreadable / malformed → fallback
        return None
    if not isinstance(entry, dict):
        return None                          # valid JSON but not an object → fallback
    ts = entry.get("ts")
    if not isinstance(ts, (int, float)) or (time.time() - ts) > _CACHE_TTL_S:
        return None                          # stale → fallback
    disp = entry.get("dispatchable")
    if not isinstance(disp, list):
        return None                          # old writer (no key) / null → fallback
    tickets = []
    for d in disp:
        if isinstance(d, dict) and isinstance(d.get("number"), int):
            # `or ""` — a `title: null` element yields "" (not the str "None").
            tickets.append((d["number"], str(d.get("title") or "")))
    return len(tickets), tickets


def _dispatchable(payload, cwd, out=None):
    """`(count, tickets)` of DISPATCHABLE candidates — CACHE-FIRST.

    Order: (1) the `_FAKE_QUALS_ENV` test seam (canned lines, no gh);
    (2) the FRESH tickets-status cache (`_cached_dispatchable` — the common path,
    no subprocess, no gh); (3) ONE live `slice-quals`/`core-quals
    --list-dispatchable` fallback capped at `_FALLBACK_TIMEOUT_S` (8s, small — a
    stale-cache turn; the footer refresh warms the cache for the next turn) → on
    timeout / rc≠0 → `_Unreadable` → fail-open. The count is the line count,
    consistent with `--count-dispatchable` by construction.

    `out` (journal seam) receives `out["src"]` = `fake` / `cache` / `live`."""
    def _src(s):
        if isinstance(out, dict):
            out["src"] = s
    fake = os.environ.get(_FAKE_QUALS_ENV)
    if fake is not None:
        _src("fake")
        if fake.startswith("@"):
            try:
                with open(fake[1:]) as f:
                    fake = f.read()
            except OSError as e:
                raise _Unreadable("fake quals file %s" % e)
        return _parse_quals_lines(fake)
    cached = _cached_dispatchable(cwd)
    if cached is not None:
        _src("cache")
        return cached
    _src("live")
    import subprocess
    import cli_quals
    try:
        import cli_concurrency
        root = cli_quals._repo_root(cwd=cwd) or cwd
        authority = cli_quals.resolve_authority(cwd=root)
        role = cli_concurrency.resolve_role(cwd)
    except Exception as e:  # noqa: BLE001
        raise _Unreadable("authority %s" % type(e).__name__)
    cmd = "core-quals" if authority == "full" else "slice-quals"
    argv = [sys.executable, _airuleset_path(), cmd, "--list-dispatchable"]
    # #1078 review B1: the CACHED writer feeds the ROLE-FILTERED workable set
    # (the footer's `_role_filter_footer`), so the fallback MUST apply the same
    # `--role` or a review/parallel pane would enforce the WHOLE-repo set on a
    # cache miss and the role-scoped set on a hit (a path-dependent wrong block).
    if role in ("review", "infra"):
        argv += ["--role", role]
    try:
        # Bounded at _FALLBACK_TIMEOUT_S (< the Stop hook's settings.json timeout)
        # so a slow fallback on a big repo fails-open (_Unreadable → journal +
        # allow) rather than being hard-killed. A stale-cache turn hits this at
        # most once; the footer refresh warms the cache for the next turn. HONEST
        # residual (review B2): on a big repo (odoo-erp, ~15-25s dep read) the 8s
        # fallback ALWAYS times out → fail-open — the gate there rests on the
        # fresh 120s cache, not this coarse best-effort fallback. And a cache-miss
        # turn whose pre-subprocess seams (the 32MB goal-scan + imports) plus the
        # subprocess exceed the hook's own timeout is SIGKILLed → still fail-open
        # (no exit 2) but WITHOUT the journal line; the common cache-HIT path (no
        # subprocess) is well within budget.
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=_FALLBACK_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        raise _Unreadable("quals subprocess %s" % type(e).__name__)
    if r.returncode != 0:
        raise _Unreadable("quals rc %d" % r.returncode)
    return _parse_quals_lines(r.stdout)


def _exc_desc(e):
    """A journal-safe exception description: an `_Unreadable`'s OWN (already
    sanitized) message, else just the type name — a raw exception's str/repr can
    embed a filesystem path / argv into the stderr journal (#826 leak class)."""
    return str(e) if isinstance(e, _Unreadable) else type(e).__name__


def _journal(why):
    """The fail-open journal line (the gate's stderr channel, like
    questionscope's fail-open writes)."""
    sys.stderr.write("lane-fill: %s — not enforced\n" % why)


def _lanefill_dir():
    """The lane-fill state dir — `AIRULESET_LANEFILL_DIR` (a test / the push-gate
    seam) else `~/.claude/lanefill` resolved at CALL time (so a HOME-override
    subprocess reads its own home, like `goal_scan.goal_templates_path`)."""
    from pathlib import Path
    d = os.environ.get(_LANEFILL_DIR_ENV)
    return d if d else os.path.join(str(Path.home()), ".claude", "lanefill")


def _fmt_live(live, evidence):
    """`live` for the journal: an int with the count_live_workers breakdown when
    evidence is present (`N(f=<finished> s=<stale> w=<wedged>)`), a plain int
    when not, `-` when unknown. NOTE (#1089): the design named the middle bucket
    "settling" — but `count_live_workers` folds a settling lane into `live` (within
    the FINISH_SETTLE_S grace) or `finished` (past it), so it is not a standalone
    evidence STATE; `s` here is `stale` (aged-out), the third real non-live
    exclusion reason the owner needs to see next to finished/wedged."""
    if live == "-" or live is None:
        return "-"
    if not evidence:
        return str(live)
    f = sum(1 for lane in evidence if getattr(lane, "state", None) == "finished")
    s = sum(1 for lane in evidence if getattr(lane, "state", None) == "stale")
    w = sum(1 for lane in evidence if getattr(lane, "state", None) == "wedged")
    return "%s(f=%d s=%d w=%d)" % (live, f, s, w)


def _cap_decisions_log(p):
    """Bound the journal like cli_gh_rate._cap_journal: rotate to `.1` once over
    the cap (total ~2× the cap). Best-effort, never raises."""
    try:
        if os.path.getsize(p) > _DECISIONS_MAX_BYTES:
            os.replace(p, p + ".1")
    except OSError:
        return                                # missing/unrotatable — nothing to cap


def _decision_journal(*, armed="?", mode="-", cap="-", live="-", evidence=None,
                      dispatchable="-", disp_src="-", verdict="-"):
    """#1089 — append ONE per-Stop decision line to
    `~/.claude/lanefill/decisions.log` (token-free, capped): the gate's verdict
    is a file read, not a guess. `-` for a fact an early / fail-open return never
    resolved. Best-effort; never raises, never blocks the gate (observability
    only) — a write failure leaves the verdict unaffected."""
    line = ("%s armed=%s mode=%s cap=%s live=%s dispatchable=%s/%s verdict=%s\n"
            % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), armed, mode, cap,
               _fmt_live(live, evidence), dispatchable, disp_src, verdict))
    try:
        d = _lanefill_dir()
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, _DECISIONS_LOG)
        _cap_decisions_log(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        # A decision-journal write is the last-resort observability sink; a
        # failure has nowhere left to be logged and must never disturb the gate's
        # verdict (mirrors cli_gh_rate._diag / count_live_workers._warn_stderr).
        return  # airuleset:script-ok observability sink — a failed journal write cannot itself be logged


def run(payload, *, goal_fn=None, mode_fn=None, cap_fn=None, live_fn=None,
        quals_fn=None, state_path=None):
    """The I/O shell: resolve inputs, call decide, emit. CHEAP checks first
    (marker / justification, no reads) so the expensive quals/transcript reads
    fire only on an armed, parallel, ⏳/✅, unjustified turn. Any read error
    journals + allows (fail-open). Never raises.

    #1089: EVERY turn that reaches the goal-armed check (past the two cheap
    early returns — a non-⏳/✅ turn and a justified turn are not gate
    evaluations and do not journal) writes ONE `decisions.log` line, so "does
    the gate work?" is a file read. The goal-armed / live / dispatchable SOURCES
    are threaded into that line via each resolver's `out` seam."""
    msg = gates.field_of(payload, "last_assistant_message", "")
    if not isinstance(msg, str):
        msg = ""                              # a malformed payload never raises
    marker = _last_marker(msg)
    if marker not in ("working", "done"):
        return                                # not a ⏳/✅ turn — allow, no reads
    if has_justification(msg):
        return                                # intentional under-fill — allow
    # --- goal armed (watchdog-first) ---
    armed_out = {}
    try:
        if goal_fn is not None:
            armed = goal_fn(payload)
            armed_src = "fn"
        else:
            armed = _goal_armed(payload, state_path=state_path, out=armed_out)
            armed_src = armed_out.get("src", "seed")
    except Exception as e:  # noqa: BLE001
        _journal("unreadable (goal-armed: %s)" % _exc_desc(e))
        _decision_journal(armed="?/unreadable",
                          verdict="unreadable:goal-armed:%s" % _exc_desc(e))
        return
    if not armed:
        _decision_journal(armed="false/%s" % armed_src, verdict="allow:not-armed")
        return
    cwd = gates.field_of(payload, "cwd", "") or os.getcwd()
    try:
        mode = (mode_fn or _mode)(cwd)
    except Exception as e:  # noqa: BLE001
        _journal("unreadable (mode: %s)" % _exc_desc(e))
        _decision_journal(armed="true/%s" % armed_src,
                          verdict="unreadable:mode:%s" % _exc_desc(e))
        return
    if mode != _MODE_PARALLEL:
        _decision_journal(armed="true/%s" % armed_src, mode=mode,
                          verdict="allow:sequential")   # sequential exempt
        return
    live_out = {}
    disp_out = {}
    try:
        cap = (cap_fn or _cap)(cwd)
        if live_fn is not None:
            live = live_fn(payload, cwd)
        else:
            live = _live(payload, cwd, out=live_out)
        if quals_fn is not None:
            count, tickets = quals_fn(payload, cwd)
            disp_src = "fn"
        else:
            count, tickets = _dispatchable(payload, cwd, out=disp_out)
            disp_src = disp_out.get("src", "?")
    except Exception as e:  # noqa: BLE001
        _journal("unreadable (%s)" % _exc_desc(e))
        _decision_journal(armed="true/%s" % armed_src, mode=mode,
                          verdict="unreadable:%s" % _exc_desc(e))
        return
    block, _reason = decide(count, live, cap, mode, armed, marker, False)
    _decision_journal(armed="true/%s" % armed_src, mode=mode, cap=cap, live=live,
                      evidence=live_out.get("evidence"), dispatchable=count,
                      disp_src=disp_src, verdict="block" if block else "allow")
    if not block:
        return
    gates.emit_block_stderr(_compose(count, live, cap, tickets))


def main():
    run(gates.read_payload())
    gates.allow()


if __name__ == "__main__":
    main()
