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
# genuine lane candidate). Used to detect the render<->structured divergence.
_STRUCTURED_ARMED = ("awaiting-user", "working", "no-backlog", "warming", "stuck")


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
    if heartbeat_state in ("absent", "corrupt"):
        return "no-heartbeat"
    if goal_armed is None:
        return "armed-unknown"
    if goal_armed is False:
        return "not-armed"
    # goal_armed is True from here -- a genuine lane candidate.
    if marker == "needs_you":
        return "awaiting-user"
    if isinstance(live_workers, int) and live_workers > 0:
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
                g.live_workers, g.backlog, _idle_word(g.idle_s),
                g.marker, _render_word(render_armed)))
    # Make the render<->structured disagreement explicit -- this is the whole
    # point of G3: a render footer that read not-armed/undeterminable while the
    # structured predicate reached an ARMED verdict would have been the SILENT
    # skip the old code did. Now it is a logged divergence.
    if g.verdict in _STRUCTURED_ARMED and render_armed is not True:
        line += (" -- render footer read %s but structured state is armed; "
                 "the render path skipped this pane SILENTLY before #486 G3"
                 % _render_word(render_armed))
    return line


def evaluate(now, sid, cwd, projects_dir, state, backlog_fetch, render_armed,
             loc, *, read_status, count_live_workers, cached_backlog_count,
             idle_threshold_s, freshness_s, on_warn=None):
    """Resolve the four STRUCTURED inputs and return ``(OneGlance, line)``.

    ``read_status`` / ``count_live_workers`` / ``cached_backlog_count`` are
    INJECTED (the caller passes ``watchdog.read_status`` etc.) so this function
    composes the canonical G1/G2/backlog readers and reads NO pane text. Each
    is contractually non-raising (the G1 reader / G2 reader / the backlog cache
    all fail toward a safe verdict, never an exception), so ``evaluate`` never
    raises either.

    ``idle_threshold_s`` is passed straight to ``read_status`` as its
    ``stale_after_s``, so the heartbeat's own ``fresh``/``stale`` verdict IS
    the idle>threshold signal -- one number, no second comparison. The caller
    supplies both windows (the render lane path's own ``GOAL_LANE_IDLE_S`` /
    ``GOAL_LANE_LIVE_WINDOW_S``) so the structured worker count uses the SAME
    freshness window the render count does and the two agree.
    """
    hb = read_status(sid=sid, now=now, stale_after_s=idle_threshold_s,
                     on_warn=on_warn)
    workers, _evidence = count_live_workers(projects_dir, cwd, sid, now,
                                            freshness_s, on_warn=on_warn)
    backlog = cached_backlog_count(cwd, backlog_fetch, state, now)
    idle_over = hb.state == "stale"   # stale_after_s == idle_threshold_s
    verdict = one_glance_verdict(
        heartbeat_state=hb.state, goal_armed=hb.goal_armed, marker=hb.marker,
        idle_over_threshold=idle_over, live_workers=workers, backlog=backlog)
    g = OneGlance(verdict, workers, backlog, idle_over, hb.marker, hb.state,
                  hb.goal_armed, hb.age_s, "")
    g = g._replace(line=format_line(loc, g, render_armed))
    return g, g.line
