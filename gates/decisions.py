"""gates.decisions -- the epic decisions collector + the `Decisions-checked:`
hand-off gate (#1396).

Owner 23.11.2026: binding `ROZHODNUTÉ` decisions live as comments scattered
across the sibling tickets of an epic (odoo-erp 8860, q3 of 8.10.2026), and a
later child ticket (odoo-erp 10385) designed the opposite and passed the gk
review. Nothing read the decisions of the epic FAMILY. This leaf derives them
from GitHub every time (no second copy to keep in sync):

  family    = the epic + every issue (open or closed) whose body has a line
              `Part of #E` / `Part of owner/repo#E` / `Epic: #E`, + every
              `- [ ] #C` / `- [x] #C` (or `owner/repo#C`) item of the epic body.
  decision  = a comment with a line-anchored `ROZHODNUTÉ` (outside code fences,
              never a `>` quote; a bullet / heading / bold prefix is allowed),
              verbatim; and each body paragraph from such a line to its end.
              A comment that carries `Decisions-checked:` is never a decision
              source (a design quoting the decisions must not count itself).

The gate (the shape of `gates.epic_rehearsal.composer_check`): a ticket that is
`Part of #E` / `Epic: #E` may be handed off only when it carries a comment with
a line `Decisions-checked: #E (<n> read)` NEWER than the newest family decision,
where n is the current family decision count. Mode from `MODE_ENV`
(`warn` | `enforce`, default warn). A full-authority box is never gated, a
non-epic ticket is unchanged, a family with no decisions passes. FAIL-OPEN with
a printed notice on every unreadable read (never a false accusation). The
collector CLI (`cli_decisions.py`) is the opposite: any read error fails loud.

Every gh read goes through the `gates.ghread` runner seam. STDLIB ONLY.
"""
import json
import os
import re
import sys

from gates import ghread
from gates.epic_rehearsal import _PREFIX, _content_lines, _FENCE_RE, iso_epoch

MODE_ENV = "AIRULESET_DECISIONS_GATE"
DEFAULT_MODE = "warn"
MODES = ("warn", "enforce")

_SLUG = r'(?:([\w.-]+/[\w.-]+))?'
_PART_OF_RE = re.compile(
    _PREFIX + r'Part[ \t]+of\**[ \t]*:?\**[ \t]*\[?' + _SLUG + r'#(\d+)(?!\d)',
    re.IGNORECASE)
_EPIC_RE = re.compile(
    _PREFIX + r'Epic\**[ \t]*:\**[ \t]*\[?' + _SLUG + r'#(\d+)(?!\d)',
    re.IGNORECASE)
_CHECKLIST_RE = re.compile(
    r'^[ \t]*[-*][ \t]+\[[ xX]\][ \t]+\[?' + _SLUG + r'#(\d+)(?!\d)')
_DECISION_RE = re.compile(_PREFIX + r'ROZHODNUT[ÉE]')
_CHECKED_ANY_RE = re.compile(_PREFIX + r'Decisions-checked\**[ \t]*:',
                             re.IGNORECASE)
_CHECKED_RE = re.compile(
    _PREFIX + r'Decisions-checked\**[ \t]*:\**[ \t]*' + _SLUG
    + r'#(\d+)[ \t]*\([ \t]*(\d+)[ \t]+read[ \t]*\)', re.IGNORECASE)


def gate_mode(env=None):
    """`warn` or `enforce`; unset / blank / unknown -> DEFAULT_MODE."""
    env = os.environ if env is None else env
    val = (env.get(MODE_ENV) or "").strip().lower()
    return val if val in MODES else DEFAULT_MODE


def _refs(rx, text):
    out = []
    for line in _content_lines(text if isinstance(text, str) else ""):
        m = rx.match(line)
        if m:
            out.append((m.group(1), int(m.group(2))))
    return out


def parent_refs(body):
    """`[(slug|None, n)]` from line-anchored `Part of #N` / `Epic: #N` lines."""
    return _refs(_PART_OF_RE, body) + _refs(_EPIC_RE, body)


def checklist_refs(body):
    """`[(slug|None, n)]` of `- [ ] #C` / `- [x] #C` items that START with a ref."""
    return _refs(_CHECKLIST_RE, body)


def checked_markers(body):
    """`[(slug|None, epic, n_read)]` from `Decisions-checked: #E (<n> read)`."""
    out = []
    for line in _content_lines(body if isinstance(body, str) else ""):
        m = _CHECKED_RE.match(line)
        if m:
            out.append((m.group(1), int(m.group(2)), int(m.group(3))))
    return out


def is_decision_comment(body):
    """True iff `body` has a line-anchored ROZHODNUTÉ outside fences / quotes
    and carries no `Decisions-checked:` line."""
    if not isinstance(body, str):
        return False
    lines = list(_content_lines(body))
    if any(_CHECKED_ANY_RE.match(ln) for ln in lines):
        return False
    return any(_DECISION_RE.match(ln) for ln in lines)


def decision_paragraphs(body):
    """Each body paragraph holding a ROZHODNUTÉ line, verbatim from that line
    to the paragraph end (a blank line or a fence ends a paragraph)."""
    out, para, fence = [], [], False

    def flush():
        for i, ln in enumerate(para):
            if _DECISION_RE.match(ln):
                out.append("\n".join(para[i:]))
                break
        del para[:]

    for raw in (body if isinstance(body, str) else "").splitlines():
        if _FENCE_RE.match(raw):
            flush()
            fence = not fence
            continue
        if fence:
            continue
        if not raw.strip():
            flush()
        else:
            para.append(raw)
    flush()
    return out


def _same(ref_slug, slug):
    return ref_slug is None or ref_slug.lower() == slug.lower()


def _gh(argv, cwd, runner, timeout=15):
    env = None if runner is not None else ghread._gh_env()
    return ghread._run(argv, cwd, timeout, env, runner)


def _ndjson(out):
    for line in (out or "").splitlines():
        try:
            yield json.loads(line)
        except (ValueError, TypeError):
            continue


def search_children(slug, epic, cwd=None, runner=None):
    """`(items, err)`: issues of `slug` whose body names the number `epic`
    (search API, re-checked line-anchored by the caller). An incomplete
    search result is an error, never a silently short family."""
    rc, out, err = _gh(["gh", "api", "-X", "GET", "search/issues",
                        "-f", "q=repo:%s %d in:body" % (slug, epic),
                        "-f", "per_page=100", "--paginate",
                        "-q", ".incomplete_results, .items[]"], cwd, runner)
    if rc != 0:
        return None, ghread._gate_unavailable(err, rc)
    items = []
    for obj in _ndjson(out):
        if obj is True:
            return None, ("%s GitHub search returned incomplete results for "
                          "%s #%d" % (ghread.GATE_UNAVAILABLE_PREFIX, slug, epic))
        if isinstance(obj, dict):
            items.append(obj)
    return items, None


def read_comments(number, slug, cwd=None, runner=None):
    """`(comments, err)`: `[{id, created_at, body, author, html_url}]`."""
    rc, out, err = _gh(["gh", "api", "repos/%s/issues/%s/comments"
                        % (slug, number), "--paginate", "-q", ".[]"],
                       cwd, runner)
    if rc != 0:
        return None, ghread._gate_unavailable(err, rc)
    items = []
    for obj in _ndjson(out):
        if isinstance(obj, dict) and isinstance(obj.get("body"), str):
            items.append({"id": obj.get("id"), "body": obj["body"],
                          "created_at": obj.get("created_at"),
                          "author": (obj.get("user") or {}).get("login"),
                          "html_url": obj.get("html_url")})
    return items, None


def _member(slug, obj):
    return {"slug": slug, "number": obj.get("number"), "title": obj.get("title"),
            "html_url": obj.get("html_url"), "body": obj.get("body") or "",
            "created_at": obj.get("created_at"),
            "author": (obj.get("user") or {}).get("login")}


def resolve_family(slug, epic, cwd=None, runner=None):
    """`(members, err)`: the epic, its `Part of` / `Epic:` children and its
    checklist refs, each `{slug, number, title, html_url, body, ...}`. Any read
    error returns `(None, err)` -- never a partial family."""
    epic_obj, err = ghread.read_issue(epic, slug, cwd=cwd, runner=runner)
    if err or not isinstance(epic_obj, dict):
        return None, err or "unreadable epic"
    members = {(slug.lower(), epic): _member(slug, epic_obj)}
    items, err = search_children(slug, epic, cwd=cwd, runner=runner)
    if err:
        return None, err
    for it in items:
        n = it.get("number")
        if it.get("pull_request") or n == epic or not isinstance(n, int):
            continue
        if any(num == epic and _same(s, slug) for s, num in parent_refs(it.get("body"))):
            members[(slug.lower(), n)] = _member(slug, it)
    for s, n in checklist_refs(epic_obj.get("body")):
        s = s or slug
        if (s.lower(), n) in members:
            continue
        obj, err = ghread.read_issue(n, s, cwd=cwd, runner=runner)
        if err or not isinstance(obj, dict):
            return None, err or "unreadable %s#%d" % (s, n)
        if not obj.get("pull_request"):
            members[(s.lower(), n)] = _member(s, obj)
    return list(members.values()), None


def _newest_first(items):
    return sorted(items, key=lambda d: iso_epoch(d.get("created_at")) or 0,
                  reverse=True)


def collect_full(slug, epic, cwd=None, runner=None):
    """`(members, groups, err)`. `groups` = one entry per ticket WITH decisions,
    `{slug, number, title, html_url, decisions: [...]}`, ordered by their newest
    decision; each decision `{kind, text, author, created_at, html_url}`."""
    members, err = resolve_family(slug, epic, cwd=cwd, runner=runner)
    if err:
        return None, None, err
    groups = []
    for m in members:
        found = [{"kind": "body", "text": p, "author": m["author"],
                  "created_at": m["created_at"], "html_url": m["html_url"]}
                 for p in decision_paragraphs(m["body"])]
        comments, err = read_comments(m["number"], m["slug"], cwd=cwd, runner=runner)
        if err:
            return None, None, err
        found += [{"kind": "comment", "text": c["body"], "author": c["author"],
                   "created_at": c["created_at"], "html_url": c["html_url"]}
                  for c in comments if is_decision_comment(c["body"])]
        if found:
            groups.append({k: m[k] for k in ("slug", "number", "title", "html_url")}
                          | {"decisions": _newest_first(found)})
    groups.sort(key=lambda g: iso_epoch(g["decisions"][0].get("created_at")) or 0,
                reverse=True)
    return members, groups, None


def collect(slug, epic, cwd=None, runner=None):
    """`(groups, err)` -- see `collect_full`."""
    _, groups, err = collect_full(slug, epic, cwd=cwd, runner=runner)
    return groups, err


def flatten(groups):
    """Every decision of `groups`, newest first."""
    return _newest_first([d for g in groups or [] for d in g["decisions"]])


def evaluate(ticket_slug, epic_slug, epic, comments, decisions):
    """`{verdict, reason, marker}`; verdict `pass` | `no-decisions` | `missing`
    | `stale` | `count-mismatch`."""
    count = len(decisions)
    if not count:
        return {"verdict": "no-decisions", "reason": None, "marker": None}
    ref = ("#%d" % epic if epic_slug.lower() == ticket_slug.lower()
           else "%s#%d" % (epic_slug, epic))
    how = ("Run `airuleset.py decisions %s#%d`, check the design against each "
           "decision, and post a comment with the line `Decisions-checked: %s "
           "(%d read)` naming each decision that applies and how the design "
           "meets it" % (epic_slug, epic, ref, count))
    marks = []
    for c in comments or []:
        for s, e, n in checked_markers(c.get("body")):
            if e == epic and (s or ticket_slug).lower() == epic_slug.lower():
                marks.append((c, n))
    if not marks:
        return {"verdict": "missing", "marker": None,
                "reason": "epic %s has %d decisions (ROZHODNUTÉ), but this ticket "
                          "has no `Decisions-checked:` line. %s." % (ref, count, how)}
    marks.sort(key=lambda cn: iso_epoch(cn[0].get("created_at")) or 0)
    mark, n_read = marks[-1]
    newest = decisions[0]
    if (iso_epoch(mark.get("created_at")) or 0) <= (iso_epoch(newest.get("created_at")) or 0):
        return {"verdict": "stale", "marker": mark,
                "reason": "the newest `Decisions-checked:` (comment %s, %s) is "
                          "older than the newest decision of epic %s (%s, %s). "
                          "%s." % (mark.get("id"), mark.get("created_at"), ref,
                                   newest.get("html_url"), newest.get("created_at"),
                                   how)}
    if n_read != count:
        return {"verdict": "count-mismatch", "marker": mark,
                "reason": "the `Decisions-checked:` (comment %s) says %d read, but "
                          "epic %s now has %d decisions. %s."
                          % (mark.get("id"), n_read, ref, count, how)}
    return {"verdict": "pass", "reason": None, "marker": mark}


def composer_check(issue, repo, *, cwd=None, runner=None, env=None,
                   authority=None, out=None):
    """The composer pre-flight: `(block_reason | None, receipt_fields)`.
    `{}` for a non-epic ticket or a full-authority box; else `decisions_epic`,
    `decisions_count` and `decisions_gate` (`pass` | `no-decisions` | `warn` |
    `enforce` | `unknown`)."""
    out = out or sys.stdout
    mode = gate_mode(env)
    if authority is None:
        import cli_quals
        authority = cli_quals.resolve_authority(cwd)
    if authority == "full":
        return None, {}
    slug = repo or ghread.resolve_slug(cwd, runner)
    if not slug or not issue:
        return None, {}
    obj, err = ghread.read_issue(int(issue), slug, cwd=cwd, runner=runner)
    if err or not isinstance(obj, dict):
        print("handoff: decisions gate state unknown (ticket #%s unreadable: %s) "
              "-- skipped, fail-open (#1396)" % (issue, err), file=out)
        return None, {"decisions_gate": "unknown"}
    refs = parent_refs(obj.get("body"))
    if not refs:
        return None, {}
    eslug, epic = refs[0][0] or slug, refs[0][1]
    if epic == int(issue) and eslug.lower() == slug.lower():
        return None, {}
    fields = {"decisions_epic": "%s#%d" % (eslug, epic), "decisions_count": None,
              "decisions_gate": mode}
    groups, err = collect(eslug, epic, cwd=cwd, runner=runner)
    comments, cerr = (None, None) if err else read_comments(int(issue), slug,
                                                            cwd=cwd, runner=runner)
    if err or cerr:
        print("handoff: decisions gate state unknown (epic %s family unreadable: "
              "%s) -- skipped, fail-open (#1396)" % (fields["decisions_epic"],
                                                     err or cerr), file=out)
        fields["decisions_gate"] = "unknown"
        return None, fields
    decisions = flatten(groups)
    fields["decisions_count"] = len(decisions)
    res = evaluate(slug, eslug, epic, comments, decisions)
    if res["verdict"] in ("pass", "no-decisions"):
        fields["decisions_gate"] = res["verdict"]
        return None, fields
    if mode == "enforce":
        return "handoff BLOCK: %s (#1396)" % res["reason"], fields
    print("handoff WARN (decisions gate, warn mode -- allowed; `%s=enforce` "
          "refuses): %s (#1396)" % (MODE_ENV, res["reason"]), file=out)
    fields["decisions_gate"] = "warn"
    return None, fields
