"""#486 G3 -- the one-glance STRUCTURED predicate.

Answers the human's single-glance supervision question -- "is this supervisor
session healthily working its backlog, or is it stuck?" -- from STRUCTURED
STATE ONLY, never from a rendered tmux pane. The whole point of #486: a person
reads the MEANING ("goal armed + 0 workers + backlog waiting + nothing moving =
stuck") in one glance, while the old guard buried that predicate under a
closed-form regex over the footer PIXELS that goes silently blind on every
render change (stash prefix, `(1d)` age granularity -- the gk diagnostic).

The four inputs are all composed from EXISTING canonical readers (no new
parser, so nothing can drift -- exactly what #486 forbids):

  * armed / idle / marker  <- G1 ``session_status.read_status`` (the heartbeat
    JSON: its ``goal_armed`` field, its file mtime as the idle signal via the
    ``state``/``age_s`` verdict, its terminal ``marker``).
  * live worker count      <- G2 ``transcripts.count_live_workers`` (subagent
    transcript mtimes + the #484 wedged-worker guard).
  * open backlog count     <- ``cross_stream._cached_backlog_count`` (the same
    cached ``gh`` count the render lane path already reads, so the structured
    verdict AGREES with the render decision by construction -- which is what
    makes G5's parallel-run mismatch log meaningful).

This module is the PURE core (``one_glance_verdict``) plus a resolver
(``evaluate``) that takes the three readers as INJECTED callables -- so it
imports nothing from the ``watchdog`` package (no import cycle) and a reviewer
can see EXACTLY which structured readers are composed. ``render_armed`` is
passed in ONLY to annotate the decision line (surfacing the render<->structured
divergence that IS the #486 blindness); it never decides the verdict.

G3 SCOPE: this predicate is DIAGNOSTIC. The render path stays the single
authoritative source that gates the actual nudge ACTION until the G5
parallel-run phase -- G3 only builds the predicate and makes it emit ONE
decision line per candidate pane (so the previously-SILENT render-blind skip
finally speaks). Do not wire this as the authoritative action gate here.
"""
from collections import namedtuple

# The verdict a single glance produces, plus the resolved facts behind it (so a
# caller / a later G5 comparison never has to re-parse the formatted line).
# `live_workers` / `backlog` are `None` when the verdict was resolvable from the
# heartbeat ALONE (the cheap short-circuit in `evaluate` never consults those
# readers for them -- see the cost note there).
OneGlance = namedtuple(
    "OneGlance",
    "verdict live_workers backlog idle_over_threshold marker heartbeat_state "
    "goal_armed idle_s line",
)

# Every verdict, smallest concern -> most actionable. The ONLY actionable one is
# `stuck`; the rest are honest "healthy / not-a-candidate / can't-tell" states.
VERDICTS = (
    "no-heartbeat",   # heartbeat absent/corrupt -> structured armed unknown
    "armed-unknown",  # heartbeat present but goal_armed not recorded (None)
    "not-armed",      # heartbeat says goal_armed False -> agrees no /goal
    "awaiting-user",  # ❓ marker -> blocked on a question, never "stuck"
    "working",        # armed + >=1 live worker -> lanes occupied
    "no-backlog",     # armed + 0 workers + no/unmeasurable open backlog
    "warming",        # armed + 0 workers + backlog, but recently active (debounce)
    "stuck",          # armed + 0 workers + backlog + idle>N + not awaiting-user
)

# The verdicts that mean "the structured state says a /goal IS armed here" (a
# genuine lane candidate). Used to detect the render<->structured divergence and
# to decide which lines are worth journalling every sweep.
_STRUCTURED_ARMED = ("awaiting-user", "working", "no-backlog", "warming", "stuck")


def heartbeat_only_verdict(heartbeat_state, goal_armed, marker):
    """The verdict when it is resolvable from the HEARTBEAT ALONE -- i.e. before
    the worker count or the backlog is ever consulted -- or ``None`` when the
    heartbeat says "a /goal is armed and the session is not awaiting the user",
    which is the only case that genuinely needs the (more expensive) worker +
    backlog readers. Single source of truth shared by ``one_glance_verdict``
    (which continues past ``None``) and ``evaluate`` (which uses ``None`` to
    decide whether to pay for those two readers at all)."""
    if heartbeat_state in ("absent", "corrupt"):
        return "no-heartbeat"
    if goal_armed is None:
        return "armed-unknown"
    if goal_armed is False:
        return "not-armed"
    if marker == "needs_you":
        return "awaiting-user"     # ❓-blocked -> never "stuck"
    return None                    # armed + not awaiting -> needs workers/backlog


def one_glance_verdict(*, heartbeat_state, goal_armed, marker,
                       idle_over_threshold, live_workers, backlog):
    """Classify a supervisor session from RESOLVED structured facts. Pure --
    no I/O, no pane text, deterministic. Returns one of ``VERDICTS``.

    The ordering encodes the human's own glance: first rule out "can't tell"
    (no/unknown heartbeat), then "not a lane candidate" (no goal armed), then
    "legitimately not stuck" (blocked on a question / lanes occupied / nothing
    to do / just-recently active), and only what survives all of those is
    ``stuck``. Every "not stuck" branch is a POSITIVE reason, never silence.
    """
    cheap = heartbeat_only_verdict(heartbeat_state, goal_armed, marker)
    if cheap is not None:
        return cheap
    # goal_armed is True and not awaiting-user -- a genuine lane candidate.
    if not isinstance(live_workers, int):
        # DEFENSIVE symmetry with the backlog guard below (count_live_workers
        # contractually returns an int today): an UNMEASURABLE worker count can
        # never confirm workers==0, so it must never assert `stuck` -- the safe
        # direction (a spurious `warming` at worst, never a false nudge).
        return "warming"
    if live_workers > 0:
        return "working"
    if not isinstance(backlog, int) or backlog <= 0:
        # None = unmeasurable backlog -> never guessed as work-to-do (the
        # fail-safe direction: an unmeasurable backlog must not read as stuck).
        return "no-backlog"
    if not idle_over_threshold:
        return "warming"
    return "stuck"


def _armed_word(v):
    return {True: "yes", False: "no", None: "?"}.get(v, "?")


def _render_word(render_armed):
    return {True: "armed", False: "not-armed", None: "undeterminable"}.get(
        render_armed, "undeterminable")


def _idle_word(idle_s):
    if not isinstance(idle_s, (int, float)):
        return "n/a"
    return "%ds" % int(idle_s)


def _num_word(v):
    # `n/a` for a reader that was never consulted (cheap heartbeat-only verdict)
    # or genuinely unmeasurable -- honest, never a misleading "0"/"None".
    return str(v) if isinstance(v, int) else "n/a"


def format_line(loc, g, render_armed):
    """The single ``one-glance <loc> -> <VERDICT> (...)`` decision line, in the
    same greppable shape as the existing ``lane-occupancy <loc> -> ...`` lines.
    Carries every structured number AND the render footer's own read, so a
    render<->structured DIVERGENCE (the exact #486 case: footer read not-armed
    while the heartbeat says armed) is visible on the line -- the seam G5 keys
    its mismatch log on.
    """
    line = ("one-glance %s -> %s (hb=%s armed=%s workers=%s backlog=%s idle=%s "
            "marker=%s; render=%s)" % (
                loc, g.verdict, g.heartbeat_state, _armed_word(g.goal_armed),
                _num_word(g.live_workers), _num_word(g.backlog),
                _idle_word(g.idle_s), g.marker, _render_word(render_armed)))
    # The #486 divergence: the footer POSITIVELY read not-armed (False) while
    # the structured predicate reached an ARMED verdict -- that is the exact
    # silent skip the old code did (`armed is False` -> bare `continue`). Gate
    # STRICTLY on `is False`, never `is not True`: `render_armed is None`
    # (undeterminable footer) is NOT this case -- the render path has journalled
    # `skip:armed-undeterminable` for it since #475, so it was never silent, and
    # "undeterminable" is not a contradiction with armed.
    if g.verdict in _STRUCTURED_ARMED and render_armed is False:
        line += (" -- render footer read not-armed but structured state is "
                 "armed; the render path skipped this pane SILENTLY before "
                 "#486 G3")
    return line


def is_informative(g, render_armed):
    """Whether this one-glance line carries SIGNAL worth journalling on THIS
    sweep, or is pure per-sweep noise. Emit for every genuine lane candidate
    (structured-armed, which includes ``stuck`` and the #486 render-blind case)
    and for any render<->structured DISAGREEMENT; stay SILENT only when the
    heartbeat and the footer BOTH positively agree the pane is not a candidate
    (``not-armed`` + a footer that also read not-armed -- a plain interactive
    session), which the pre-G3 render path deliberately silenced too as "pure
    noise". A missing/unknown heartbeat (``no-heartbeat``/``armed-unknown``) is
    NOT suppressed -- it could hide a genuinely-armed-but-heartbeatless session,
    the exact class this redesign must never go blind on."""
    if g.verdict in _STRUCTURED_ARMED:
        return True
    return not (g.verdict == "not-armed" and render_armed is False)


def evaluate(now, sid, cwd, projects_dir, state, backlog_fetch, render_armed,
             loc, *, read_status, count_live_workers, cached_backlog_count,
             idle_threshold_s, freshness_s, on_warn=None):
    """Resolve the STRUCTURED inputs and return ``(OneGlance, line)``.

    ``read_status`` / ``count_live_workers`` / ``cached_backlog_count`` are
    INJECTED (the caller passes ``watchdog.read_status`` etc.) so this function
    composes the canonical G1/G2/backlog readers and reads NO pane text. Each
    is contractually non-raising (the G1 reader / G2 reader / the backlog cache
    all fail toward a safe verdict, never an exception), so ``evaluate`` never
    raises either.

    COST: the heartbeat is resolved FIRST, and the two EXPENSIVE readers
    (``count_live_workers`` = an O(subagent-files) disk stat pass;
    ``cached_backlog_count`` = a ``gh`` subprocess on a cache miss) are consulted
    ONLY when the verdict genuinely needs them -- i.e. NOT for the cheap
    heartbeat-only verdicts (``no-heartbeat`` / ``armed-unknown`` / ``not-armed``
    / ``awaiting-user``). A plain non-armed candidate pane therefore costs ONE
    heartbeat file read per sweep, never a per-sweep ``gh`` fetch, honouring
    #486's "supervision cost DOWN" thesis and the repo's gh-rate-limit
    discipline.

    ``idle_threshold_s`` is passed straight to ``read_status`` as its
    ``stale_after_s``, so the heartbeat's own ``fresh``/``stale`` verdict IS
    the idle>threshold signal -- one number, no second comparison. The caller
    supplies both windows (the render lane path's own ``GOAL_LANE_IDLE_S`` /
    ``GOAL_LANE_LIVE_WINDOW_S``) so the structured worker count uses the SAME
    freshness window the render count does and the two agree.
    """
    hb = read_status(sid=sid, now=now, stale_after_s=idle_threshold_s,
                     on_warn=on_warn)
    idle_over = hb.state == "stale"   # stale_after_s == idle_threshold_s
    cheap = heartbeat_only_verdict(hb.state, hb.goal_armed, hb.marker)
    if cheap is not None:
        verdict, workers, backlog = cheap, None, None
    else:
        workers, _evidence = count_live_workers(projects_dir, cwd, sid, now,
                                                freshness_s, on_warn=on_warn)
        backlog = cached_backlog_count(cwd, backlog_fetch, state, now)
        verdict = one_glance_verdict(
            heartbeat_state=hb.state, goal_armed=hb.goal_armed, marker=hb.marker,
            idle_over_threshold=idle_over, live_workers=workers, backlog=backlog)
    g = OneGlance(verdict, workers, backlog, idle_over, hb.marker, hb.state,
                  hb.goal_armed, hb.age_s, "")
    g = g._replace(line=format_line(loc, g, render_armed))
    return g, g.line


# --------------------------------------------------------------------------- #
# #486 G5 -- parallel-run mismatch evidence (DIAGNOSTIC; never gates an action)
# --------------------------------------------------------------------------- #
#
# G3 made the structured predicate emit ONE decision line per candidate pane,
# annotating the render footer's own read so a render<->structured divergence
# is VISIBLE on the `one-glance` line. G5 runs both paths in parallel for a few
# days on the live boxes and accumulates DEDICATED, DEDUPED evidence of every
# genuine CONTRADICTION between them -- one greppable `parallel-mismatch` line
# per divergence EPISODE (plus a bounded "still diverging" re-assert), carrying
# which inputs differed -- so G6 can retire the render heuristics on real fleet
# evidence, not a green suite. The render path stays the sole action gate here:
# parallel-run OBSERVES, it never acts on the structured path.

# A genuine render<->structured CONTRADICTION -- the two paths POSITIVELY
# disagree about whether a /goal is armed on this pane. `kind`:
#   "render-blind"     -- footer read not-armed (False) but the structured
#                         state is armed (the #486 case: the render path would
#                         SKIP this pane, missing a genuinely-armed -- possibly
#                         STUCK -- session). The critical evidence for G6.
#   "structured-blind" -- footer read armed (True) but the heartbeat read
#                         not-armed. Subject to the KNOWN 4 MB-tail caveat: the
#                         heartbeat's `goal_armed` comes from a single-shot ~4 MB
#                         transcript-tail scan, so a /goal armed >4 MB back reads
#                         `goal_armed=False` -- and this reader alone cannot tell
#                         that apart from a genuinely cleared goal. Flagged
#                         `caveat=4mb-tail` so the G6 reader treats this CLASS as
#                         the known heartbeat limitation, never as proof the
#                         structured path is unreliable. (A DISAMBIGUATION does
#                         exist for a future G6 refinement -- goal_dark_watch
#                         persists an INCREMENTAL-offset `state["goal_mark"]`
#                         marker that survives the 4 MB tail across sweeps -- but
#                         G5 stays a pure diagnostic over the heartbeat and does
#                         not consult another job's state; the conservative class
#                         label is the correct scope here.)
# `differ` embeds the structured verdict for render-blind, so a meaningful
# transition (warming -> stuck as a worker drops away) re-emits rather than
# dedups; `caveat` is None unless the 4 MB-tail class applies.
Mismatch = namedtuple("Mismatch", "kind differ caveat")


def classify_mismatch(g, render_armed):
    """Return a ``Mismatch`` when the render footer and the STRUCTURED verdict
    POSITIVELY contradict each other on armed-ness, else ``None``.

    ONLY a genuine contradiction counts -- one side positively armed, the other
    positively not. A side that merely LACKS DATA (render undeterminable /
    heartbeat absent / goal_armed unknown) is a confidence GAP, not a
    contradiction: the one-glance line and #475's `skip:armed-undeterminable`
    already surface it, and logging it as a mismatch would flood the evidence
    stream with exactly the non-signal G6 must not act on.
    """
    if render_armed is False and g.verdict in _STRUCTURED_ARMED:
        # #486: the render path skips this pane silently; the structured state
        # sees an armed lane candidate (possibly stuck).
        return Mismatch("render-blind",
                        "render=not-armed vs structured=%s" % g.verdict, None)
    if render_armed is True and g.verdict == "not-armed":
        # The heartbeat read not-armed while the footer read armed. This is the
        # 4 MB-tail-susceptible direction (`goal_armed=False` also covers "arm
        # is past the scanned tail"), so tag the class, never claim the cause.
        return Mismatch("structured-blind",
                        "render=armed vs structured=not-armed", "4mb-tail")
    return None


def mismatch_signature(mm):
    """A stable dedup key for a mismatch's SEMANTIC state. Includes ``differ``
    (which carries the structured verdict for render-blind) so a meaningful
    transition re-emits, and ``caveat`` so a class change is never dedup'd
    away."""
    return "%s|%s|%s" % (mm.kind, mm.differ, mm.caveat or "-")


def format_mismatch_line(loc, g, render_armed, mm):
    """The single greppable ``parallel-mismatch`` evidence line -- pane, the
    mismatch kind, both paths' reads, every structured number, which inputs
    differed, and any known caveat. The G6 deletion decision reads THIS stream
    to judge whether the structured path can retire the render one."""
    line = ("parallel-mismatch %s -> %s (render=%s structured=%s hb=%s armed=%s "
            "workers=%s backlog=%s idle=%s marker=%s; differ=%s" % (
                loc, mm.kind, _render_word(render_armed), g.verdict,
                g.heartbeat_state, _armed_word(g.goal_armed),
                _num_word(g.live_workers), _num_word(g.backlog),
                _idle_word(g.idle_s), g.marker, mm.differ))
    if mm.caveat:
        line += "; caveat=%s" % mm.caveat
    return line + ")"


def mismatch_evidence(loc, g, render_armed, *, prev, now, reassert_s):
    """Deduped ``(line, new_state)`` for the parallel-run mismatch evidence.

    ``prev`` is the pane's prior ``(signature, emit_ts)`` state or ``None``. A
    NEW or CHANGED mismatch emits a line immediately; a PERSISTENT unchanged one
    re-emits at most once per ``reassert_s`` (bounded -- the point of a
    parallel-run evidence log is one line per divergence EPISODE plus a periodic
    "still diverging" heartbeat, never a line every 60 s sweep, which is exactly
    the per-sweep spam that made the old pane-text guard unreadable).

    The bound holds per STABLE episode: a genuinely flapping verdict CLASS
    (``warming`` <-> ``stuck`` as a worker appears/vanishes, or the idle
    threshold crossing) is a MEANINGFUL transition that re-emits by design (its
    signature embeds the verdict) -- "never spams" therefore means "for a stable
    divergence", not "at most one line ever". A transient render/heartbeat
    CONFIDENCE GAP is NOT such a transition and must never reset the episode
    (see below).

    Returns ``(None, None)`` ONLY on a genuine RESOLUTION -- both the render
    footer AND the heartbeat give a DEFINITE read and they AGREE -- so the
    caller drops the pane's dedup state and a later re-occurrence is a fresh
    episode. A transient CONFIDENCE GAP (footer undeterminable this sweep, or the
    heartbeat lacks a definite armed read) returns ``(None, prev)`` instead,
    PRESERVING the episode. Pure: no I/O, deterministic, never raises (reads only
    the already-resolved ``OneGlance``)."""
    mm = classify_mismatch(g, render_armed)
    if mm is None:
        # No contradiction -- but a transient CONFIDENCE GAP is not a RESOLUTION.
        # `pane_goal_armed` returns None mid-turn (busy / chrome / a large unsent
        # draft), so a genuinely-diverging pane blinks render=None every few
        # sweeps; likewise the heartbeat can read goal_armed=None. Treating that
        # as "resolved" would drop the episode and re-arm a fresh one when the
        # definite read returns -- defeating the re-assert bound (the anti-spam
        # hole both #486 G5 reviews reproduced: ~1 line/120 s indefinitely).
        # Preserve the episode on a gap; drop it ONLY on a definite agreement.
        if render_armed is None or g.goal_armed is None:
            return None, prev
        return None, None
    sig = mismatch_signature(mm)
    if prev is not None and prev[0] == sig and (now - prev[1]) < reassert_s:
        # Persistent, unchanged, within the re-assert window -> keep the ORIGINAL
        # emit ts (so re-assert fires reassert_s after the episode began, not
        # after each suppressed sweep) and journal nothing.
        return None, prev
    return format_mismatch_line(loc, g, render_armed, mm), (sig, now)


def prune_mismatch_state(mrecs, now, ttl_s):
    """Drop dead-session entries from the per-sid mismatch dedup dict, in place,
    returning the count reaped. A session that dies WHILE still diverging never
    gets revisited (its pane vanishes from the sweep), so its ``mrecs`` entry
    would otherwise leak for the box's lifetime -- #486 G5 review 🟡. Age-gated
    like ``session_status.reap_stale_status``: a genuinely-live diverging session
    refreshes its ``emit_ts`` at least every ``reassert_s`` (it re-emits), so a
    ``ttl_s`` set well above ``reassert_s`` never prunes a live one; a mis-pruned
    entry would only cost one extra "fresh episode" line, never a wrong action.
    A malformed/JSON-degraded entry (non-numeric ts) is dropped too -- it can
    never correctly dedup anything. Never raises."""
    reaped = 0
    for sid in list(mrecs):
        entry = mrecs.get(sid)
        try:
            ts = entry[1]
            stale = not isinstance(ts, (int, float)) or (now - ts) >= ttl_s
        except (TypeError, IndexError, KeyError):
            stale = True
        if stale:
            mrecs.pop(sid, None)
            reaped += 1
    return reaped
