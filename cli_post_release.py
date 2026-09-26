"""#1161 part 2 (d) -- the post-release loop metric
(`slice-quals --post-release [DAYS]` / `core-quals --post-release [DAYS]`).

The daily flow metrics count only PRE-release bounce rounds (#843/#957), so the
cost of shipping a sub-ticket that is then fixed again after it went live on
PROD was invisible. The measurement on issue 1161 (issuecomment-5848226431)
found about 109 such loops in 30 days. This module counts them, per ticket, per
epic and per stream, from three signals:

  * reopen   -- a `reopened` event after a live-on-PROD signal;
  * rework   -- a new hand-off after the deploy: a READY-FOR-REVIEW comment, or
                a `ready-for-review` / `prio:bounce` label (the verify-on-copy
                bounce). `needs-gatekeeper` is NOT counted, because after a
                release it is the close request (#636), not rework;
  * followup -- a NEW ticket citing an already-released ticket as defective:
                an explicit `Defect-of: #N` line, or `#N` on a line that also
                carries a defect word. The loop is charged to the CITED ticket.

A release point is the `verify-on-copy` label (#1053: gk hands the ticket back
after the deploy), or a gk comment "live on PROD <version>" / "PROD one-shot
done" / "-> verify-on-copy". Each release->work transition is ONE loop, whose
kind is the FIRST re-entry signal, so a reopen followed by a new hand-off is
counted once.

Pattern donor: the #957 rounds audit (`scripts/audit_bounce_rule_updates.py`):
fetch the population, enrich each ticket with its events, compute purely, then
print. Every gh read goes through the `gates.ghread` runner seam
(`runner(argv) -> (rc, out, err)`). A failed read never yields a number: a
failed listing prints `unknown`, and an unreadable timeline turns the total into
`unknown (lower bound N ...)`. Both exit 1, never a false 0. The follow-up count
is a LOWER bound: a follow-up whose cited ticket fell outside the window is not
seen. The timelines are read through `cli_parallel.run_parallel` (#1067), the
same bounded per-ticket walk `cli_quals` uses. Accepted residual: a defect that
is BOTH reopened on its ticket and filed as a `Defect-of:` follow-up counts
twice (each is a separate trip back to work).
"""
import json
import re
import sys
import time
from datetime import datetime, timezone

from gates import ghread
from gates.epic_rehearsal import iso_epoch as _epoch
from gates.epic_rehearsal import parse_epic_ref as _epic_of

RELEASE_LABEL = "verify-on-copy"
REENTRY_LABELS = frozenset({"ready-for-review", "prio:bounce"})
DEFAULT_DAYS = 30
DEFAULT_LIMIT = 1000

# A gk live-on-PROD comment. "live on PROD" must be FOLLOWED by a version (a bug
# report "reproduced it live on PROD", or "live on PROD since 12.9", is not one).
_LIVE_RE = re.compile(
    r"(?i)\blive on prod[ \t:(,-]*v?\d+\.\d+"
    r"|\bprod one-shot done\b"
    r"|(?:→|->)[ \t]*`?verify-on-copy\b")
_RFR_RE = re.compile(r"^\s*([#*_-]+\s*)?READY-FOR-REVIEW"
                     r"|Ready for gatekeeper cross-fork review", re.MULTILINE)
_DEFECT_OF_RE = re.compile(
    r"(?im)^[ \t]*(?:[-*][ \t]+)?\**Defect-of\**[ \t]*:[ \t]*((?:#\d+[ ,]*)+)")
_DEFECT_WORD_RE = re.compile(
    r"(?i)\b(?:regres\w*|defect\w*|broken|bug\w*|nefunguj\w*|chybn\w*|"
    r"zabudl\w*|forgot\w*|pokazen\w*|rozbit\w*)")
# A same-repo `#N` (never `owner/repo#N`, never inside a longer word).
_CITE_RE = re.compile(r"(?<![\w/#-])#(\d+)\b")


def _event_kind(ev):
    """`release` | `reopen` | `reentry` | None for one timeline event."""
    if not isinstance(ev, dict):
        return None
    kind = ev.get("event")
    if kind == "reopened":
        return "reopen"
    if kind == "labeled":
        name = (ev.get("label") or {}).get("name")
        if name == RELEASE_LABEL:
            return "release"
        return "reentry" if name in REENTRY_LABELS else None
    if kind == "commented":
        body = ev.get("body") or ""
        if _RFR_RE.search(body):
            return "reentry"
        return "release" if _LIVE_RE.search(body) else None
    return None


def classify_timeline(events, since_ts=None):
    """`{released_at, reopen, rework}` for one ticket's timeline. A loop counts
    only when its re-entry happened at/after `since_ts` (None = all history)."""
    marks = []
    for i, ev in enumerate(events or []):
        kind = _event_kind(ev)
        ts = _epoch(ev.get("created_at")) if kind else None
        if ts is not None:
            marks.append((ts, i, kind))
    marks.sort()
    released, first = False, None
    counts = {"reopen": 0, "rework": 0}
    for ts, _i, kind in marks:
        if kind == "release":
            released = True
            first = ts if first is None else first
        elif released:
            released = False
            if since_ts is None or ts >= since_ts:
                counts["reopen" if kind == "reopen" else "rework"] += 1
    return {"released_at": first, **counts}


def _cited_defects(issue):
    """The ticket numbers `issue` cites as defective (explicit + line-level)."""
    body = issue.get("body") or ""
    cited = set()
    for m in _DEFECT_OF_RE.finditer(body):
        cited.update(int(n) for n in re.findall(r"#(\d+)", m.group(1)))
    for line in [issue.get("title") or ""] + body.splitlines():
        if _DEFECT_WORD_RE.search(line):
            cited.update(int(n) for n in _CITE_RE.findall(line))
    return cited


def follow_ups(issues, released, since_ts=None):
    """`{cited: [citing, ...]}` -- a citing ticket created AFTER the cited
    ticket's first release (and at/after `since_ts`), never itself or its own
    epic."""
    out = {}
    for it in issues or []:
        n = it.get("number")
        created = _epoch(it.get("createdAt"))
        if n is None or created is None or (since_ts and created < since_ts):
            continue
        epic = _epic_of(it.get("body"))
        for c in sorted(_cited_defects(it)):
            rel = released.get(c)
            if c in (n, epic) or rel is None or rel >= created:
                continue
            out.setdefault(c, []).append(n)
    return out


def _stream_of(issue, aliases):
    names = sorted((lb or {}).get("name") or "" for lb in issue.get("labels") or []
                   if isinstance(lb, dict))
    for name in names:
        if name.startswith("stream:"):
            raw = name[len("stream:"):]
            return aliases.get(raw, raw)
    return None


def _aliases():
    try:
        import airuleset
        return dict(airuleset.STREAM_RENAME_ALIASES)
    except Exception:  # noqa: BLE001 -- grouping by the raw label is still honest
        return {}


def compute(issues, timelines, since_ts=None, aliases=None):
    """One row per ticket whose timeline was read: number, title, epic, stream,
    reopen, rework, followup, loops. `issues` maps number -> listing row."""
    aliases = _aliases() if aliases is None else aliases
    per = {n: classify_timeline(ev, since_ts) for n, ev in timelines.items()}
    released = {n: r["released_at"] for n, r in per.items()
                if r["released_at"] is not None}
    fups = follow_ups(issues.values(), released, since_ts)
    epics = {_epic_of(it.get("body")) for it in issues.values()} - {None}
    rows = []
    for n in sorted(per):
        it = issues.get(n) or {}
        r = per[n]
        fu = len(fups.get(n, []))
        epic = _epic_of(it.get("body"))
        rows.append({"number": n, "title": it.get("title") or "",
                     "epic": epic if epic is not None else (n if n in epics else None),
                     "stream": _stream_of(it, aliases),
                     "reopen": r["reopen"], "rework": r["rework"],
                     "followup": fu, "loops": r["reopen"] + r["rework"] + fu})
    return rows


def _group(rows, key):
    out = {}
    for r in rows:
        k = r[key]
        k = "-" if k is None else ("#%d" % k if key == "epic" else k)
        g = out.setdefault(k, [0, 0])
        g[0] += 1
        g[1] += r["loops"]
    return out


def render(rows, unreadable, scope, since_date, truncated=False):
    """The printable table lines (TSV) + per-epic + per-stream + TOTAL."""
    lines = ["# post-release loops since %s (scope: %s)" % (since_date, scope),
             "ticket\tepic\tstream\treopen\trework\tfollowup\tloops\ttitle"]
    for r in rows:
        if r["loops"]:
            lines.append("#%d\t%s\t%s\t%d\t%d\t%d\t%d\t%s" % (
                r["number"], "#%d" % r["epic"] if r["epic"] else "-",
                r["stream"] or "-", r["reopen"], r["rework"], r["followup"],
                r["loops"], r["title"]))
    for key in ("epic", "stream"):
        lines.append("")
        lines.append("%s\ttickets\tloops" % key)
        for k, (n, loops) in sorted(_group(rows, key).items(),
                                    key=lambda kv: (-kv[1][1], kv[0])):
            lines.append("%s\t%d\t%d" % (k, n, loops))
    total = sum(r["loops"] for r in rows)
    lines.append("")
    if unreadable:
        lines.append("unreadable: %s" % " ".join("#%d" % n for n in unreadable))
        lines.append("TOTAL\tunknown (lower bound %d; %d timelines unreadable)"
                     % (total, len(unreadable)))
    else:
        lines.append("TOTAL\t%d%s" % (total, " (lower bound: population capped)"
                                        if truncated else ""))
    return lines


def _gh(argv, root, runner, timeout=30):
    env = None if runner is not None else ghread._gh_env()
    return ghread._run(argv, root, timeout, env, runner)


def fetch_population(slug, quals, since_date, root=None, runner=None,
                     limit=DEFAULT_LIMIT):
    """`({number: row}, err, truncated)`: the tickets (any state) updated since
    `since_date`, unioned over `quals` (None = the whole repo, the fleet)."""
    rows, truncated = {}, False
    for q in (quals or [""]):
        search = " ".join(x for x in (q, "updated:>=%s" % since_date) if x)
        rc, out, err = _gh(["gh", "issue", "list", "-R", slug, "--state", "all",
                            "--search", search, "-L", str(limit), "--json",
                            "number,title,body,labels,createdAt"], root, runner)
        if rc != 0:
            return None, ghread._gate_unavailable(err, rc), False
        try:
            got = json.loads(out)
        except (ValueError, TypeError):
            return None, "%s unparseable issue list" % ghread.GATE_UNAVAILABLE_PREFIX, False
        if not isinstance(got, list):
            return None, "%s issue list is not an array" % ghread.GATE_UNAVAILABLE_PREFIX, False
        truncated = truncated or len(got) >= limit
        for it in got:
            if isinstance(it, dict) and isinstance(it.get("number"), int):
                rows[it["number"]] = it
    return rows, None, truncated


def fetch_timeline(slug, number, root=None, runner=None):
    """`(events, err)` for one ticket via the REST timeline endpoint."""
    rc, out, err = _gh(["gh", "api", "repos/%s/issues/%d/timeline" % (slug, number),
                        "--paginate", "-q", ".[]"], root, runner)
    if rc != 0:
        return None, ghread._gate_unavailable(err, rc)
    events = []
    for line in (out or "").splitlines():
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events, None


def run(quals, root, days=DEFAULT_DAYS, runner=None, now=None, slug=None,
        out=None, limit=DEFAULT_LIMIT):
    """Print the metric; return 0, or 1 when the count is unknown."""
    out = out or sys.stdout
    if not isinstance(days, int) or days <= 0:
        print("post-release loops: DAYS must be a positive integer (got %r)"
              % (days,), file=out)
        return 2
    now = time.time() if now is None else now
    since_ts = now - days * 86400
    since_date = datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    scope = "fleet" if quals is None else "slice"
    slug = slug or ghread.canonical_slug(root, runner)
    if not slug:
        print("post-release loops: unknown -- repo slug unresolvable", file=out)
        print("TOTAL\tunknown", file=out)
        return 1
    issues, err, truncated = fetch_population(slug, quals, since_date, root,
                                              runner, limit)
    if err:
        print("post-release loops: unknown -- %s" % err, file=out)
        print("TOTAL\tunknown", file=out)
        return 1
    import cli_parallel
    got = cli_parallel.run_parallel(
        sorted(issues), lambda n: fetch_timeline(slug, n, root, runner))
    timelines, unreadable = {}, []
    for n in sorted(issues):
        events, terr = got.get(n, (None, "raised"))
        if terr:
            unreadable.append(n)
        else:
            timelines[n] = events
    rows = compute(issues, timelines, since_ts)
    for line in render(rows, unreadable, scope, since_date, truncated):
        print(line, file=out)
    return 1 if unreadable else 0
