"""Pure HTML rendering for the Autopilot Board.

PURE string functions — no server, no DB, no I/O — so they're unit testable and
the server can't bypass the escaping.

DESIGN: faithfully reproduces the brainstorming visual-companion mockup the user
approved ("A · Card grid") — a RESTRAINED, compact, muted dark board. One rich
card per run: repo+issue (soft blue) and a small phase badge on top; readable
goal/approach/result; the REVIEW GATE as a compact wrapped row of tiny
valid✓ / CI✓ / /review✓ / /rcr… pills so a human sees at a glance whether every
mandated review passed before merge. An alarm = a red border + one red line (NOT
a loud glowing banner). Subtle, professional, scannable — never garish.

SECURITY (the board renders attacker-influenced text — titles/goals/results come
from worker reports on dev1 AND dev2):
  * EVERY interpolated value goes through `_e()` (html.escape, quote=True). Single
    chokepoint; templates NEVER drop a raw value into markup. Phase maps to a
    FIXED css class (never interpolated into one).
  * PR URLs reach an href ONLY after `_gh_href` validates the github https prefix.
  * Strict CSP (default-src 'none'; style-src 'self' 'unsafe-inline'): no script
    can run. Script-free markup, single inline <style>.

stdlib only.
"""
import html

from board import AUTO_REFRESH_S, ALL_PHASES, TERMINAL_PHASES


# --------------------------------------------------------------------------- #
# escaping chokepoint
# --------------------------------------------------------------------------- #
def _e(v):
    """Escape ANY value for safe HTML. quote=True so a value inside a quoted
    attribute can't break out. None -> "" (missing field renders blank)."""
    if v is None:
        return ""
    return html.escape(str(v), quote=True)


def _gh_href(url):
    """Return an escaped href ONLY for a GitHub https URL, else None."""
    if isinstance(url, str) and url.startswith("https://github.com/"):
        return _e(url)
    return None


# --------------------------------------------------------------------------- #
# fixed metadata (never derived from user input)
# --------------------------------------------------------------------------- #
# Phase -> a small badge css class (muted tints). Unknown/None -> default blue.
_PHASE_BADGE = {
    "validating": "", "version-bump": "", "implementing": "", "CI": "",
    "RED": "red", "GREEN": "green", "review": "review", "merge": "merge",
    "deploy": "deploy", "done": "done", "asking-user": "ask",
    "stopped": "stop", "obsolete-closed": "stop",
}

# Gate check -> compact pill label, in REQUIRED_GATES order.
_GATE_PILL = {
    "ticket_validated": "valid", "ci": "CI", "mergeable": "mergeable",
    "plan_check": "plan", "review": "/review",
    "requesting_code_review": "/rcr", "regression": "regress",
    "deploy_verified": "deploy",
}


def _badge_class(phase):
    """Map phase -> FIXED badge class, so an attacker-supplied phase can never
    inject a class name."""
    return _PHASE_BADGE.get(phase, "") if phase in ALL_PHASES else ""


# --------------------------------------------------------------------------- #
# style (mirrors the approved mockup's palette + restraint)
# --------------------------------------------------------------------------- #
_STYLE = """
  :root{
    --bg:#0d0f14; --panel:#12151c; --card:#171a21; --line:#2a2f3a;
    --line-soft:#1c2230; --ink:#e8ecf3; --ink-dim:#cdd3df; --ink-faint:#8a92a3;
    --blue:#9ecbff; --green:#86e0a0; --amber:#e8c479; --red:#e0827e;
    --green-bg:#16331f; --amber-bg:#33300f; --red-bg:#3a1b1a; --pill-bg:#1c2230;
    --alarm:#e0524d;
  }
  *{box-sizing:border-box} html,body{margin:0}
  body{background:var(--bg);color:var(--ink-dim);
    font:13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
    -webkit-font-smoothing:antialiased;padding:22px 22px 56px}
  a{color:inherit;text-decoration:none}
  .wrap{max-width:1320px;margin:0 auto}

  header.top{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:4px}
  header.top h1{font-size:18px;font-weight:700;color:var(--ink);margin:0;letter-spacing:-.2px}
  header.top .sub{font-size:12px;color:var(--ink-faint)}
  .health{font-size:11.5px;color:var(--ink-faint);margin:3px 0 20px}
  .health b{color:var(--ink-dim);font-weight:600}
  .health .stale{color:var(--red);font-weight:700}

  .upnext{background:var(--panel);border:1px solid var(--line);border-radius:8px;
    padding:11px 14px 13px;margin-bottom:22px}
  .upnext h2{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
    color:var(--ink-faint);font-weight:700;margin:0 0 10px}
  .upnext h2 .n{color:var(--ink-dim)}
  .qgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px 18px}
  .qrepo .rh{font-size:11px;color:var(--ink-faint);font-weight:600;margin:0 0 4px;
    border-bottom:1px solid var(--line-soft);padding-bottom:3px}
  .qrepo .rh b{color:var(--blue)}
  .qi{display:flex;gap:8px;font-size:12px;padding:2px 0}
  .qi .num{color:var(--blue);font-weight:700;font-variant-numeric:tabular-nums;min-width:42px}
  .qi .t{color:var(--ink-dim);overflow-wrap:anywhere}

  .sec{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-faint);
    font-weight:700;margin:4px 0 10px;display:flex;align-items:center;gap:9px}
  .sec .n{color:var(--ink-dim)} .sec .rail{flex:1;height:1px;background:var(--line-soft)}

  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px;margin-bottom:26px}

  .tk{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:11px 13px;
    display:flex;flex-direction:column}
  .tk.alarm{border-color:var(--alarm);box-shadow:0 0 0 1px rgba(224,82,77,.2)}
  .row1{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:2px}
  .repo{font-weight:700;color:var(--blue);font-size:13px;font-variant-numeric:tabular-nums}
  .repo .o{color:var(--ink-faint);font-weight:600}
  .badge{font-size:9.5px;padding:2px 7px;border-radius:10px;background:#2a3142;color:var(--blue);
    text-transform:uppercase;letter-spacing:.04em;font-weight:700;white-space:nowrap}
  .badge.deploy{background:#23402b;color:var(--green)}
  .badge.merge{background:#3a2e12;color:var(--amber)}
  .badge.done{background:var(--green-bg);color:var(--green)}
  .badge.review{background:#16313a;color:#7fd6e8}
  .badge.red{background:var(--red-bg);color:var(--red)}
  .badge.green{background:var(--green-bg);color:var(--green)}
  .badge.ask{background:#3a2e12;color:#f0c674;box-shadow:0 0 0 1px rgba(240,198,116,.35)}
  .badge.stop{background:#23262e;color:var(--ink-faint)}

  .ttl{font-weight:600;color:var(--ink);font-size:14px;margin:3px 0 5px;line-height:1.35}
  .meta{color:var(--ink-faint);font-size:11.5px;line-height:1.5;overflow-wrap:anywhere}
  .meta b{color:var(--ink-dim);font-weight:600}
  .meta+.meta{margin-top:1px}

  .gate{display:flex;flex-wrap:wrap;gap:4px;margin-top:9px}
  .g{font-size:10px;padding:2px 6px;border-radius:5px;background:var(--pill-bg);
    color:var(--ink-faint);font-weight:600;white-space:nowrap}
  .g.ok{background:var(--green-bg);color:var(--green)}
  .g.run{background:var(--amber-bg);color:var(--amber)}
  .g.no{background:var(--red-bg);color:var(--red)}

  .alarmtxt{color:#ff6b66;font-weight:700;font-size:11px;margin-top:8px;line-height:1.4}
  .foot{display:flex;align-items:center;gap:8px;margin-top:9px;
    border-top:1px solid var(--line-soft);padding-top:8px}
  .foot .host{color:var(--ink-faint);font-size:11px}
  .foot .pr{margin-left:auto;color:var(--blue);font-size:11px;font-weight:600}
  .foot .pr.danger{color:var(--red)}

  .empty{margin:34px auto;max-width:460px;text-align:center;color:var(--ink-faint);
    background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:28px 20px;font-size:13px}
  .empty b{color:var(--ink-dim)}

  footer.pf{margin-top:20px;padding-top:13px;border-top:1px solid var(--line-soft);
    color:var(--ink-faint);font-size:11px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  footer.pf .ver{color:var(--ink-dim);font-weight:700;font-variant-numeric:tabular-nums}
  footer.pf .legend{margin-left:auto;display:flex;gap:12px;flex-wrap:wrap}
  footer.pf .legend span{display:inline-flex;align-items:center;gap:5px}
  footer.pf .legend i{width:9px;height:9px;border-radius:3px;display:inline-block}

  /* ticket detail */
  table{border-collapse:collapse;width:100%;font-size:12px;margin:6px 0 16px}
  th,td{border:1px solid var(--line-soft);padding:6px 9px;text-align:left;vertical-align:top}
  th{background:var(--panel);color:var(--ink-faint);text-transform:uppercase;font-size:10px;letter-spacing:.05em}
  td{color:var(--ink-dim)}
  h2.dh{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-faint);margin:18px 0 7px}
  .back{display:inline-block;margin:6px 0;color:var(--blue);font-weight:600}
  @media (max-width:560px){.grid{grid-template-columns:1fr}}
"""


def _page(title, body):
    """Wrap a body fragment in the full HTML document. <meta refresh> auto-
    refreshes every AUTO_REFRESH_S. No <script> (CSP forbids it)."""
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<meta http-equiv=\"refresh\" content=\"{int(AUTO_REFRESH_S)}\">"
        f"<title>{_e(title)}</title>"
        f"<style>{_STYLE}</style>"
        "</head><body><div class=\"wrap\">"
        + body +
        "</div></body></html>"
    )


# --------------------------------------------------------------------------- #
# fragments
# --------------------------------------------------------------------------- #
def _header(health):
    health = health or {}
    bits = []
    if health.get("started_at_str"):
        bits.append(f"up since <b>{_e(health['started_at_str'])}</b>")
    last = health.get("last_report", health.get("last_report_str"))
    bits.append(f"last report <b>{_e(last or 'never')}</b>")
    if "runs_last_hour" in health:
        bits.append(f"<b>{_e(health['runs_last_hour'])}</b> runs · last hr")
    stale = ""
    if health.get("gh_stale"):
        since = health.get("gh_stale_since", "")
        stale = (f" · <span class=\"stale\">gh signals STALE"
                 f"{(' since ' + _e(since)) if since else ''}</span>")
    return (
        "<header class=\"top\"><h1>Autopilot Board</h1>"
        "<span class=\"sub\">autonomous coding agents · live review-gate audit</span></header>"
        f"<div class=\"health\">{' &middot; '.join(bits)}{stale}</div>"
    )


def _queue_section(queue):
    """'Up next · N' — upcoming tickets grouped by repo, in pick-order."""
    if not queue:
        return ""
    n = len(queue)
    groups = {}
    for item in queue:
        repo = item["repo"] if "repo" in item else None
        groups.setdefault(repo, []).append(item)
    blocks = []
    for repo, items in groups.items():
        owner, _, name = (repo or "").partition("/")
        rh = (f"{_e(owner)}/<b>{_e(name)}</b>" if name
              else f"<b>{_e(repo or 'unknown')}</b>")
        rows = [f"<div class=\"rh\">{rh}</div>"]
        for item in items:
            rows.append(
                f"<div class=\"qi\"><span class=\"num\">#{_e(item['issue'])}</span>"
                f"<span class=\"t\">{_e(item['title'])}</span></div>")
        blocks.append(f"<div class=\"qrepo\">{''.join(rows)}</div>")
    return (
        "<section class=\"upnext\">"
        f"<h2>Up next <span class=\"n\">&middot; {_e(n)} queued</span></h2>"
        f"<div class=\"qgrid\">{''.join(blocks)}</div></section>"
    )


def _gate_pills(gate):
    """Compact wrapped row of gate pills, in REQUIRED_GATES order. ok=✓ green,
    fail=✗ red, pending=grey (no suffix). Empty gate -> all pending pills so a
    human always sees the review-gate at a glance."""
    from board.gate import REQUIRED_GATES
    gate = gate or {}
    checks = [c for c in REQUIRED_GATES if c in gate] if gate else list(REQUIRED_GATES)
    checks += sorted(c for c in gate if c not in REQUIRED_GATES)
    if not checks:
        return ""
    pills = []
    for c in checks:
        state = gate.get(c) or "pending"
        label = _GATE_PILL.get(c, c.replace("_", " "))
        if state == "ok":
            pills.append(f"<span class=\"g ok\">{_e(label)}✓</span>")
        elif state == "fail":
            pills.append(f"<span class=\"g no\">{_e(label)}✗</span>")
        else:
            pills.append(f"<span class=\"g\">{_e(label)}</span>")
    return f"<div class=\"gate\">{''.join(pills)}</div>"


def _repo_issue(run):
    repo = run.get("repo")
    issue = run.get("issue")
    iss = f" #{_e(issue)}" if issue not in (None, "") else ""
    if repo:
        owner, _, name = repo.partition("/")
        if name:
            return f"<span class=\"o\">{_e(owner)}/</span>{_e(name)}{iss}"
        return f"{_e(repo)}{iss}"
    return f"&mdash;{iss}"


def _meta(label, value):
    if value is None or value == "":
        return ""
    return f"<div class=\"meta\"><b>{_e(label)}:</b> {_e(value)}</div>"


def _alarmtxt(alarms):
    if not alarms:
        return ""
    return "".join(f"<div class=\"alarmtxt\">⚠ {_e(a)}</div>" for a in alarms)


def _card(run):
    """One Live/Recent card — the approved compact mockup card. EVERY value via
    _e / _gh_href."""
    rid = run.get("run_id")
    phase = run.get("phase")
    alarms = run.get("alarms") or []
    badge = (f"<span class=\"badge {_badge_class(phase)}\">{_e(phase)}</span>"
             if phase else "")
    title = run.get("title")
    title_html = (f"<a class=\"ttl\" href=\"/ticket/{_e(rid)}\">{_e(title or rid)}</a>"
                  if rid else f"<div class=\"ttl\">{_e(title)}</div>")
    href = _gh_href(run.get("pr_url"))
    pr = ""
    if href:
        pr = (f"<a class=\"pr{' danger' if alarms else ''}\" "
              f"href=\"{href}\">PR →</a>")
    upd = run.get("updated_str") or run.get("status")
    foot_bits = f"<span class=\"host\">{_e(run.get('machine'))}" \
                f"{(' · ' + _e(upd)) if upd else ''}</span>"
    # A finished run (terminal phase, no alarm) reads as DONE — a single green
    # pill — NOT a wall of grey "pending" gate pills. Workers rarely self-report
    # every gate, and a merged/closed issue passed GitHub's own gates already;
    # showing the full pending checklist on a solved ticket made the board look
    # like nothing had passed. Live runs still show the gate checklist (the
    # governance-audit view), and a terminal run WITH an alarm shows its pills so
    # the failing gate stays visible.
    terminal = phase in TERMINAL_PHASES
    if terminal and not alarms:
        gates_html = "<div class=\"gate\"><span class=\"g ok\">done✓</span></div>"
    else:
        gates_html = _gate_pills(run.get("gate") or {})
    return (
        f"<article class=\"tk{' alarm' if alarms else ''}\">"
        f"<div class=\"row1\"><span class=\"repo\">{_repo_issue(run)}</span>{badge}</div>"
        + title_html
        + _meta("Goal", run.get("goal"))
        + _meta("Approach", run.get("approach"))
        + _meta("Result", run.get("result"))
        + gates_html
        + _alarmtxt(alarms)
        + f"<div class=\"foot\">{foot_bits}{pr}</div>"
        + "</article>"
    )


def _pagefoot(version):
    return (
        "<footer class=\"pf\">"
        f"<span class=\"ver\">{_e(version)}</span>"
        f"<span>auto-refresh {int(AUTO_REFRESH_S)}s · governance audit live</span>"
        "<span class=\"legend\">"
        "<span><i style=\"background:var(--green)\"></i>passed</span>"
        "<span><i style=\"background:var(--ink-faint)\"></i>pending</span>"
        "<span><i style=\"background:var(--red)\"></i>failed</span>"
        "<span><i style=\"background:var(--amber)\"></i>needs human</span>"
        "</span></footer>"
    )


# --------------------------------------------------------------------------- #
# public render functions
# --------------------------------------------------------------------------- #
def _sort_live(live):
    """asking-user runs (blocked on a human) sort FIRST so they're never missed."""
    return sorted(live, key=lambda r: 0 if r.get("phase") == "asking-user" else 1)


def card_grid(live, recent, version, health, queue=None):
    """Render the board home page (header, Up-next, Live, Recent/Done, footer).

    `live`/`recent` are run dicts (keys: run_id, repo, issue, title, phase, goal,
    approach, result, machine, status, pr_url, gate{check:state}, alarms[list]).
    `version` is the footer label; `health` is the health dict; `queue` (optional)
    the planned backlog. Everything escaped."""
    live = _sort_live(live or [])
    recent = recent or []
    head = _header(health) + _queue_section(queue or [])

    if not live and not recent:
        body = (head
                + "<div class=\"empty\">No autopilot runs yet — start one with "
                  "<b>/autopilot</b></div>"
                + _pagefoot(version))
        return _page("Autopilot Board", body)

    parts = [head]
    if live:
        parts.append(f"<div class=\"sec\"><span>Live</span>"
                     f"<span class=\"n\">{_e(len(live))} active</span>"
                     "<span class=\"rail\"></span></div><div class=\"grid\">")
        parts.extend(_card(r) for r in live)
        parts.append("</div>")
    if recent:
        parts.append(f"<div class=\"sec\"><span>Recent / Done</span>"
                     f"<span class=\"n\">{_e(len(recent))} shipped</span>"
                     "<span class=\"rail\"></span></div><div class=\"grid\">")
        parts.extend(_card(r) for r in recent)
        parts.append("</div>")
    parts.append(_pagefoot(version))
    return _page("Autopilot Board", "".join(parts))


def ticket_detail(run, events, gate, gh):
    """Render the /ticket/<run> detail page: the run card, gate detail, gh
    signals, evidence, and the event timeline. EVERY value escaped."""
    if run is None:
        return _page("Ticket not found",
                     "<header class=\"top\"><h1>Ticket not found</h1></header>"
                     "<p class=\"back\"><a href=\"/\">← back to board</a></p>")

    rid = run.get("run_id")
    repo = run.get("repo")
    issue = run.get("issue")

    # the same card, plus extra evidence fields
    card = _card({**run, "title": run.get("title") or rid})
    extra = (
        _meta("Status", run.get("status"))
        + _meta("Unverified", run.get("unverified"))
        + _meta("Filed", run.get("filed_issues"))
        + _meta("Merge SHA", run.get("merge_sha"))
        + _meta("Evidence", run.get("validated_evidence"))
        + _meta("RED test", run.get("regression_red_test"))
        + _meta("GREEN test", run.get("regression_green_test"))
    )

    gate = gate or {}
    gate_tbl = ""
    if gate:
        from board.gate import source_of, REQUIRED_GATES
        order = {g: i for i, g in enumerate(REQUIRED_GATES)}
        rows = []
        for check in sorted(gate, key=lambda c: (order.get(c, len(order)), c)):
            state = gate.get(check) or "pending"
            rows.append(f"<tr><td>{_e(_GATE_PILL.get(check, check))}</td>"
                        f"<td>{_e(state)}</td><td>{_e(source_of(check))}</td></tr>")
        gate_tbl = ("<h2 class=\"dh\">Gate detail</h2><table>"
                    "<tr><th>check</th><th>state</th><th>source</th></tr>"
                    + "".join(rows) + "</table>")

    gh_tbl = ""
    if gh:
        gh_get = gh.get if hasattr(gh, "get") else (
            lambda k: gh[k] if k in gh.keys() else None)
        rows = [("merged", gh_get("merged")), ("pr state", gh_get("pr_state")),
                ("ci", gh_get("ci_conclusion")), ("mergeable", gh_get("mergeable")),
                ("mergeable state", gh_get("mergeable_state")),
                ("issue state", gh_get("issue_state")),
                ("deploy version", gh_get("deploy_version"))]
        body_rows = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>"
                            for k, v in rows if v is not None)
        if body_rows:
            gh_tbl = "<h2 class=\"dh\">GitHub signals (verified)</h2><table>" + body_rows + "</table>"

    events = events or []
    if events:
        ev_rows = []
        for ev in events:
            ev_get = ev.get if hasattr(ev, "get") else (
                lambda k, e=ev: e[k] if k in e.keys() else None)
            ev_rows.append(f"<tr><td>{_e(ev_get('seq'))}</td><td>{_e(ev_get('phase'))}</td>"
                           f"<td>{_e(ev_get('message'))}</td><td>{_e(ev_get('event_ts'))}</td></tr>")
        timeline = ("<h2 class=\"dh\">Timeline</h2><table>"
                    "<tr><th>seq</th><th>phase</th><th>message</th><th>ts</th></tr>"
                    + "".join(ev_rows) + "</table>")
    else:
        timeline = "<h2 class=\"dh\">Timeline</h2><div class=\"meta\">no events</div>"

    body = (card + (f"<div style=\"margin:10px 0 4px\">{extra}</div>" if extra else "")
            + gate_tbl + gh_tbl + timeline
            + "<p class=\"back\"><a href=\"/\">← back to board</a></p>")
    return _page(f"{repo} #{issue}", body)


# expose phase order for any caller that wants display ordering
PHASE_ORDER = ALL_PHASES
