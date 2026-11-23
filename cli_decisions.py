"""cli_decisions -- `airuleset.py decisions <owner/repo>#<N> [--json]` (#1396).

Prints every `ROZHODNUTÉ` decision of an epic FAMILY (the epic, its
`Part of #E` / `Epic: #E` children, its checklist refs), verbatim, newest first,
grouped per ticket with the ticket title, author, Europe/Bratislava date and
link. The derivation lives in `gates.decisions` (shared with the hand-off gate).

FAIL LOUD: any gh read error exits 1 with the reason and prints NO list -- a
partial list would read as the complete set of decisions. In the text output
every decision line is quoted (`> `), so a listing pasted onto a ticket never
counts as new decisions. STDLIB ONLY.
"""
import json
import re
import sys
from datetime import datetime, timezone

from gates import decisions as dc
from gates.epic_rehearsal import iso_epoch

_REF_RE = re.compile(r'^([\w.-]+/[\w.-]+)#(\d+)$')
_TZ = "Europe/Bratislava"


def register_parser(sub):
    """The `decisions` argparse subparser (keeps main() from growing)."""
    p = sub.add_parser(
        "decisions",
        help="#1396: print every ROZHODNUTÉ decision of an epic family "
             "(epic + Part of / Epic: children + checklist refs), newest first")
    p.add_argument("ref", help="the epic, as <owner/repo>#<N>")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def local_time(iso):
    """`D.M.YYYY HH:MM` in Europe/Bratislava (UTC with a label if no tzdata)."""
    ts = iso_epoch(iso)
    if ts is None:
        return "?"
    try:
        from zoneinfo import ZoneInfo
        dt, suffix = datetime.fromtimestamp(ts, tz=ZoneInfo(_TZ)), ""
    except Exception:  # noqa: BLE001 -- missing tzdata: say UTC, never guess
        dt, suffix = datetime.fromtimestamp(ts, tz=timezone.utc), " UTC"
    return "%d.%d.%d %02d:%02d%s" % (dt.day, dt.month, dt.year, dt.hour,
                                      dt.minute, suffix)


def render_text(slug, epic, members, groups):
    count = sum(len(g["decisions"]) for g in groups)
    lines = ["Epic %s#%d: %d decisions in %d of %d family tickets, newest first."
             % (slug, epic, count, len(groups), len(members)),
             "Family: " + ", ".join("%s#%d" % (m["slug"], m["number"])
                                    for m in members),
             "A design for a child ticket records this read with the line "
             "`Decisions-checked: #%d (%d read)`." % (epic, count), ""]
    for g in groups:
        lines.append("== %s#%d %s -- %s" % (g["slug"], g["number"],
                                            g.get("title") or "", g.get("html_url")))
        for d in g["decisions"]:
            lines.append("-- %s  %s  %s  (%s)" % (local_time(d.get("created_at")),
                                                  d.get("author") or "?",
                                                  d.get("html_url"), d["kind"]))
            lines.extend(("> " + ln) if ln else ">" for ln in d["text"].splitlines())
            lines.append("")
    return "\n".join(lines)


def cmd_decisions(args, runner=None, cwd=None):
    """Exit 0 printed, 1 a gh read failed (nothing printed), 2 a bad ref."""
    m = _REF_RE.match((args.ref or "").strip())
    if not m:
        print("decisions: expected owner/repo#N, got %r" % args.ref,
              file=sys.stderr)
        return 2
    slug, epic = m.group(1), int(m.group(2))
    members, groups, err = dc.collect_full(slug, epic, cwd=cwd, runner=runner)
    if err:
        print("decisions: cannot read the family of %s#%d -- %s. No list printed "
              "(a partial list would look complete)." % (slug, epic, err),
              file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps({
            "epic": "%s#%d" % (slug, epic),
            "count": sum(len(g["decisions"]) for g in groups),
            "family": ["%s#%d" % (x["slug"], x["number"]) for x in members],
            "tickets": [dict(g, decisions=[dict(d, local_time=local_time(
                d.get("created_at"))) for d in g["decisions"]]) for g in groups],
        }, ensure_ascii=False, indent=1))
    else:
        print(render_text(slug, epic, members, groups))
    return 0
