"""Pure HTML rendering for the Autopilot Board (design §6, §10, §11).

These are PURE string functions — no server, no DB, no I/O — so they're unit
testable in isolation and the server can't accidentally bypass the escaping.

SECURITY (the board renders attacker-influenced text — titles/goals/results come
from worker reports on dev1 AND dev2):
  * EVERY interpolated value goes through `_e()` (html.escape, quote=True) — a
    `<script>` in a title renders as `&lt;script&gt;`, never executes. The single
    chokepoint is `_e`; the templates below NEVER drop a raw value into markup.
  * PR URLs are placed in an href ONLY after `_gh_href` validates
    startswith("https://github.com/"); the href value is itself escaped. A
    `javascript:`/`data:` URL never reaches an attribute.
  * The page ships a restrictive CSP (default-src 'none'; style-src 'self'
    'unsafe-inline') so even if an escape were ever missed, no injected script
    could run. Inline styles are allowed (we use a <style> block); no inline or
    external script is permitted. The server sets the CSP header (see server.py);
    we keep the markup script-free so the header is honoured.

`card_grid(live, recent, version, health)` is the 4-arg form for Phase E.
Phase E2 adds `queue=None` (Task 19): when non-empty, renders an "Up next"
section near the top showing upcoming tickets in pick-order.

stdlib only.
"""
import html

from board import AUTO_REFRESH_S, ALL_PHASES


# --------------------------------------------------------------------------- #
# escaping chokepoint
# --------------------------------------------------------------------------- #
def _e(v):
    """Escape ANY value for safe HTML text/attribute interpolation.

    quote=True so a value placed inside a double-quoted attribute can't break
    out. None -> "" (so a missing field renders blank, never the string 'None').
    Everything is coerced to str first so an int issue number / bool is safe."""
    if v is None:
        return ""
    return html.escape(str(v), quote=True)


def _gh_href(url):
    """Return an escaped href ONLY for a GitHub https URL, else None.

    Validates startswith("https://github.com/") BEFORE the value is allowed
    anywhere near an href attribute — a `javascript:`/`data:`/non-github URL is
    rejected outright (returns None → no link rendered). The returned value is
    escaped so even a github URL carrying a `"` can't break out of the attr."""
    if isinstance(url, str) and url.startswith("https://github.com/"):
        return _e(url)
    return None


# --------------------------------------------------------------------------- #
# page chrome
# --------------------------------------------------------------------------- #
_STYLE = """
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 1rem;
         background: #0d1117; color: #c9d1d9; }
  h1 { font-size: 1.2rem; margin: 0 0 .5rem; }
  h2 { font-size: 1rem; margin: 1.2rem 0 .5rem; color: #8b949e;
       border-bottom: 1px solid #21262d; padding-bottom: .25rem; }
  a { color: #58a6ff; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .grid { display: grid; gap: .75rem;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
          padding: .75rem; }
  .card h3 { font-size: .95rem; margin: 0 0 .35rem; }
  .meta { color: #8b949e; font-size: .8rem; }
  .field { margin: .25rem 0; }
  .field b { color: #8b949e; font-weight: 600; }
  .badge { display: inline-block; padding: .05rem .4rem; border-radius: 10px;
           font-size: .75rem; background: #21262d; color: #c9d1d9; }
  .chip { display: inline-block; padding: .02rem .35rem; margin: .1rem .15rem 0 0;
          border-radius: 8px; font-size: .72rem; border: 1px solid #30363d; }
  .chip.ok { color: #3fb950; border-color: #238636; }
  .chip.fail { color: #f85149; border-color: #da3633; }
  .chip.pending { color: #d29922; border-color: #9e6a03; }
  .chip.claimed { font-style: italic; }
  .alarm { background: #2d0d0d; border: 1px solid #da3633; color: #f85149;
           border-radius: 6px; padding: .4rem .6rem; margin: .35rem 0;
           font-weight: 600; }
  .empty { color: #8b949e; padding: 2rem; text-align: center; }
  footer { margin-top: 2rem; padding-top: .5rem; border-top: 1px solid #21262d;
           color: #8b949e; font-size: .8rem; }
  .health { color: #8b949e; font-size: .8rem; margin: .25rem 0 1rem; }
  .health .stale { color: #f85149; font-weight: 600; }
  table { border-collapse: collapse; width: 100%; font-size: .82rem; }
  td, th { border: 1px solid #21262d; padding: .25rem .4rem; text-align: left;
           vertical-align: top; }
  .queue { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
           padding: .6rem .75rem; margin-bottom: .75rem; font-size: .85rem; }
  .queue h2 { margin: 0 0 .4rem; font-size: .9rem; color: #58a6ff;
              border-bottom: none; padding-bottom: 0; }
  .queue .q-repo { color: #8b949e; font-size: .8rem; margin: .3rem 0 .1rem; }
  .queue .q-item { padding: .1rem 0; }
"""


def _page(title, body):
    """Wrap a body fragment in the full HTML document with CSP-friendly chrome.

    The <meta http-equiv refresh> auto-refreshes the board every AUTO_REFRESH_S.
    No <script> anywhere — the CSP forbids it and we never need it."""
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<meta http-equiv=\"refresh\" content=\"{int(AUTO_REFRESH_S)}\">"
        f"<title>{_e(title)}</title>"
        f"<style>{_STYLE}</style>"
        "</head><body>"
        + body +
        "</body></html>"
    )


def _footer(version):
    return f"<footer>Autopilot Board &middot; {_e(version)}</footer>"


# --------------------------------------------------------------------------- #
# fragments
# --------------------------------------------------------------------------- #
def _phase_badge(phase):
    return f"<span class=\"badge\">{_e(phase)}</span>"


def _gate_chips(gate):
    """Render the gate check row. Each chip is coloured by state (ok/fail/
    pending) and the check name + state are escaped. `gate` is {check: state}."""
    if not gate:
        return ""
    parts = []
    # stable display order: known checks first (REQUIRED_GATES order), then any
    # extras alphabetically — deterministic output for stable tests/snapshots.
    from board.gate import REQUIRED_GATES
    order = {g: i for i, g in enumerate(REQUIRED_GATES)}
    for check in sorted(gate, key=lambda c: (order.get(c, len(order)), c)):
        state = gate.get(check) or "pending"
        cls = state if state in ("ok", "fail", "pending") else "pending"
        sym = {"ok": "✓", "fail": "✗"}.get(state, "…")
        parts.append(
            f"<span class=\"chip {_e(cls)}\">{_e(check)} {sym}</span>")
    return "<div class=\"chips\">" + "".join(parts) + "</div>"


def _alarms(alarms):
    if not alarms:
        return ""
    return "".join(
        f"<div class=\"alarm\">⚠ {_e(a)}</div>" for a in alarms)


def _field(label, value):
    """One labelled field, omitted entirely when the value is empty."""
    if value is None or value == "":
        return ""
    return f"<div class=\"field\"><b>{_e(label)}:</b> {_e(value)}</div>"


def _pr_link(pr_url):
    href = _gh_href(pr_url)
    if href is None:
        return ""
    return (f"<div class=\"field\"><b>PR:</b> "
            f"<a href=\"{href}\">{href}</a></div>")


def _card(run):
    """One Live/Recent card. `run` is a plain dict (assembled by the server),
    so render stays DB-free and pure. EVERY value flows through _e/_gh_href."""
    rid = run.get("run_id")
    repo = run.get("repo")
    issue = run.get("issue")
    head = f"{_e(repo)} #{_e(issue)}"
    link = f"<a href=\"/ticket/{_e(rid)}\">{head}</a>" if rid else head
    return (
        "<div class=\"card\">"
        + _alarms(run.get("alarms") or [])
        + f"<h3>{link} {_phase_badge(run.get('phase'))}</h3>"
        + _field("title", run.get("title"))
        + _field("goal", run.get("goal"))
        + _field("approach", run.get("approach"))
        + _field("result", run.get("result"))
        + _pr_link(run.get("pr_url"))
        + _gate_chips(run.get("gate") or {})
        + f"<div class=\"meta\">{_e(run.get('machine'))}"
          f"{' &middot; ' + _e(run.get('status')) if run.get('status') else ''}"
          "</div>"
        + "</div>"
    )


def _health_strip(health):
    """Health strip (§6): start time, last gh refresh, runs touched last hour,
    last report received. If gh is failing, a visible STALE banner."""
    if not health:
        return ""
    bits = []
    if health.get("started_at_str"):
        bits.append(f"up since {_e(health['started_at_str'])}")
    if "runs_last_hour" in health:
        bits.append(f"{_e(health['runs_last_hour'])} runs touched in last hour")
    last_report = health.get("last_report", health.get("last_report_str"))
    bits.append(f"last report received: {_e(last_report or 'never')}")
    if health.get("last_gh_refresh_str"):
        bits.append(f"last gh refresh: {_e(health['last_gh_refresh_str'])}")
    stale = ""
    if health.get("gh_stale"):
        since = health.get("gh_stale_since", "")
        stale = (f" <span class=\"stale\">gh signals STALE"
                 f"{(' since ' + _e(since)) if since else ''}"
                 " (auth/rate-limit?)</span>")
    return f"<div class=\"health\">{' &middot; '.join(bits)}{stale}</div>"


# --------------------------------------------------------------------------- #
# public render functions
# --------------------------------------------------------------------------- #
def _queue_section(queue):
    """Render the 'Up next — N queued' section for the backlog queue.

    `queue` is a list of dicts (or sqlite Rows) with keys: repo, issue, title,
    position. Items are grouped by repo in the order they appear (already sorted
    by (repo, position) from the DB). EVERY interpolated value goes through _e()."""
    if not queue:
        return ""
    n = len(queue)
    label = f"Up next &mdash; {_e(n)} queued"
    # group by repo while preserving order
    groups = {}
    for item in queue:
        repo = item["repo"] if hasattr(item, "keys") else item.get("repo") if hasattr(item, "get") else item[0]
        if repo not in groups:
            groups[repo] = []
        groups[repo].append(item)

    rows_html = []
    for repo, items in groups.items():
        rows_html.append(f"<div class=\"q-repo\">{_e(repo)}</div>")
        for item in items:
            issue = item["issue"] if hasattr(item, "__getitem__") else None
            title = item["title"] if hasattr(item, "__getitem__") else None
            rows_html.append(
                f"<div class=\"q-item\">"
                f"#{_e(issue)}&nbsp;{_e(title)}"
                f"</div>")

    return (
        "<div class=\"queue\">"
        f"<h2>{label}</h2>"
        + "".join(rows_html)
        + "</div>"
    )


def card_grid(live, recent, version, health, queue=None):
    """Render the board home page.

    Phase E: 4-arg form (live, recent, version, health).
    Phase E2: adds `queue=None` — when non-empty, renders an "Up next — N
    queued" section near the top showing upcoming tickets in pick-order.

    `live` / `recent` are lists of plain run dicts already assembled by the
    server (keys: run_id, repo, issue, title, phase, goal, approach, result,
    machine, status, pr_url, gate{check:state}, alarms[list]). `version` is the
    footer label; `health` is the health-strip dict. Everything is escaped.
    Existing 4-arg callers work unchanged (queue defaults to None)."""
    live = live or []
    recent = recent or []
    queue_html = _queue_section(queue or [])

    if not live and not recent:
        body = (
            "<h1>Autopilot Board</h1>"
            + _health_strip(health)
            + queue_html
            + "<div class=\"empty\">No autopilot runs yet — "
              "start one with /autopilot</div>"
            + _footer(version)
        )
        return _page("Autopilot Board", body)

    sections = ["<h1>Autopilot Board</h1>", _health_strip(health), queue_html]
    if live:
        sections.append("<h2>Live</h2><div class=\"grid\">")
        sections.extend(_card(r) for r in live)
        sections.append("</div>")
    if recent:
        sections.append("<h2>Recent / Done</h2><div class=\"grid\">")
        sections.extend(_card(r) for r in recent)
        sections.append("</div>")
    sections.append(_footer(version))
    return _page("Autopilot Board", "".join(sections))


def ticket_detail(run, events, gate, gh):
    """Render the /ticket/<run> detail page: header, gate rows, gh signals,
    evidence, and the event timeline (already ordered by the server). EVERY
    interpolated value is escaped; the PR link is github-https-validated.

    `run` is a run dict (or sqlite Row — both support .get? Rows don't, so the
    server passes a plain dict). `events` is an ordered list of event dicts;
    `gate` is {check: state}; `gh` is the gh_state dict (or None)."""
    if run is None:
        return _page("Ticket not found",
                     "<h1>Ticket not found</h1>"
                     + _footer(""))

    rid = run.get("run_id")
    repo = run.get("repo")
    issue = run.get("issue")
    header = (
        f"<h1>{_e(repo)} #{_e(issue)} "
        f"{_phase_badge(run.get('phase'))}</h1>"
        f"<div class=\"meta\">run {_e(rid)} &middot; {_e(run.get('machine'))}"
        f" &middot; merge: {_e(run.get('merge_mode'))}</div>"
    )

    info = (
        _field("title", run.get("title"))
        + _field("goal", run.get("goal"))
        + _field("approach", run.get("approach"))
        + _field("result", run.get("result"))
        + _field("status", run.get("status"))
        + _field("unverified", run.get("unverified"))
        + _field("filed issues", run.get("filed_issues"))
        + _field("merge sha", run.get("merge_sha"))
        + _field("validated evidence", run.get("validated_evidence"))
        + _field("regression (RED)", run.get("regression_red_test"))
        + _field("regression (GREEN)", run.get("regression_green_test"))
        + _pr_link(run.get("pr_url"))
    )

    # ---- gate rows ----
    gate = gate or {}
    if gate:
        from board.gate import source_of, REQUIRED_GATES
        order = {g: i for i, g in enumerate(REQUIRED_GATES)}
        rows = []
        for check in sorted(gate, key=lambda c: (order.get(c, len(order)), c)):
            state = gate.get(check) or "pending"
            cls = state if state in ("ok", "fail", "pending") else "pending"
            rows.append(
                f"<tr><td>{_e(check)}</td>"
                f"<td><span class=\"chip {_e(cls)}\">{_e(state)}</span></td>"
                f"<td>{_e(source_of(check))}</td></tr>")
        gate_html = ("<h2>Gates</h2><table>"
                     "<tr><th>check</th><th>state</th><th>source</th></tr>"
                     + "".join(rows) + "</table>")
    else:
        gate_html = ""

    # ---- gh signals ----
    if gh:
        gh_get = gh.get if hasattr(gh, "get") else (lambda k: gh[k] if k in gh.keys() else None)
        gh_rows = [
            ("merged", gh_get("merged")),
            ("pr state", gh_get("pr_state")),
            ("ci", gh_get("ci_conclusion")),
            ("mergeable", gh_get("mergeable")),
            ("mergeable state", gh_get("mergeable_state")),
            ("issue state", gh_get("issue_state")),
            ("deploy version", gh_get("deploy_version")),
        ]
        gh_html = ("<h2>GitHub signals (verified)</h2><table>"
                   + "".join(
                       f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>"
                       for k, v in gh_rows if v is not None)
                   + "</table>")
    else:
        gh_html = ""

    # ---- timeline ----
    events = events or []
    if events:
        ev_rows = []
        for ev in events:
            ev_get = ev.get if hasattr(ev, "get") else (
                lambda k, e=ev: e[k] if k in e.keys() else None)
            ev_rows.append(
                f"<tr><td>{_e(ev_get('seq'))}</td>"
                f"<td>{_e(ev_get('phase'))}</td>"
                f"<td>{_e(ev_get('message'))}</td>"
                f"<td>{_e(ev_get('event_ts'))}</td></tr>")
        timeline = ("<h2>Timeline</h2><table>"
                    "<tr><th>seq</th><th>phase</th><th>message</th>"
                    "<th>event_ts</th></tr>"
                    + "".join(ev_rows) + "</table>")
    else:
        timeline = "<h2>Timeline</h2><div class=\"meta\">no events</div>"

    body = (header + info + gate_html + gh_html + timeline
            + "<p><a href=\"/\">&larr; back to board</a></p>")
    return _page(f"{repo} #{issue}", body)


# expose phase order for any caller that wants display ordering
PHASE_ORDER = ALL_PHASES
