"""gates.epic_rehearsal -- the epic-level hand-off gate (#1161 part 2 (b)+(c)).

Owner 26.9.2026: the sub-dev streams used PROD as the test environment. An epic
was split into sub-tickets, each handed to the gatekeeper and released alone,
and only then checked on PROD. The measurement (issuecomment-5848226431) found
about 109 post-release loops in 30 days, about 92 % of them findable on a fresh
prod copy. The owner's 24.9 ruling on odoo-erp #6967 ("finish on the copy, then
one gk iteration") caught three defects before release the first time it was
used. This gate is that ruling as a mechanism (design: issuecomment-5848235579):

  A sub-ticket whose body declares `Epic: #N` may be handed off only when epic
  #N carries an `Epic-rehearsal:` comment that is NEWER than the sub-ticket's
  HEAD commit and LISTS the sub-ticket.

Rollout: WARN first. The mode comes from `AIRULESET_EPIC_REHEARSAL_GATE`
(`warn` | `enforce`), defaulting to DEFAULT_MODE. Warn prints exactly what is
missing and allows; enforce returns a `handoff BLOCK: ...` reason. A ticket with
no epic is unchanged. Like the #1105 prod-transfer gate, this is sub-dev
discipline, so a full-authority box is never gated.

FAIL-OPEN in every direction that cannot prove the gate's premise (an
unreadable ticket or epic, or an unresolvable repo): it prints a notice and
allows, the never-false-accuse convention of the sibling composer pre-flights.
An unknown HEAD commit time skips only the freshness comparison. The rehearsal
must still exist and list the ticket.

Every gh/git read goes through the `gates.ghread` runner seam
(`runner(argv) -> (rc, out, err)`), so tests never reach real gh. STDLIB ONLY.
"""
import json
import os
import re
import sys

from gates import ghread

MODE_ENV = "AIRULESET_EPIC_REHEARSAL_GATE"
DEFAULT_MODE = "warn"
MODES = ("warn", "enforce")

_FENCE_RE = re.compile(r'^[ \t]*(?:```|~~~)')
# The optional line prefix every marker tolerates: a bullet, a numbered item or
# an ATX heading, then optional bold. Never a `>` quote.
_PREFIX = r'^[ \t]*(?:(?:[-*]|\d+[.)])[ \t]+)?(?:#{1,6}[ \t]+)?\**'
# `Epic: #N` -- line-anchored, the colon DIRECTLY after the word (so
# `Epic-rehearsal:` and prose "the epic: #N" never match); `[#N](url)` allowed.
_EPIC_LINE_RE = re.compile(
    _PREFIX + r'Epic\**[ \t]*:\**[ \t]*\[?#(\d+)\b', re.IGNORECASE)
# `Epic-rehearsal:` -- line-anchored (bullet / heading / bold allowed), never a
# `>` quote and never mid-sentence: a reply QUOTING an old rehearsal must not
# satisfy the gate (the #818 / #1053 marker lesson).
_REHEARSAL_LINE_RE = re.compile(
    _PREFIX + r'Epic-rehearsal\**[ \t]*:', re.IGNORECASE)
# The rehearsal's evidence (#1161 review): a comment that merely CARRIES the
# marker -- a gk instruction "Epic-rehearsal: still missing", or a run with a
# failing acceptance row -- must not pass. It needs the copy it ran on, at least
# one PASS row, and no FAIL row (the #1053 Verified-on-copy fingerprint lesson).
_COPY_RE = re.compile(r'(?i)REFRESH-DEV-BOX-FROM-PROD|\brefresh\b|\berp-test\b')
# A result CELL is read by its LEADING token (`FAIL (#42 still broken)` fails,
# `pass (was fail before the fix)` passes); a header row is skipped.
_FAIL_CELL_RE = re.compile(r'(?i)\**(?:fail(?:ed|s)?\b|\u274c|\u2717|nok\b)')
_PASS_CELL_RE = re.compile(r'(?i)\**(?:pass(?:ed|es)?\b|\u2705|\u2713|ok\b)')
_SEPARATOR_ROW_RE = re.compile(r'^[ \t|:\-]+$')

_WHAT = ("the copy (REFRESH-DEV-BOX-FROM-PROD id + date, refreshed after the "
         "last commit of every included sub-ticket), the release-shaped run "
         "(deploy + every one-shot in release order), a pass/fail row per epic "
         "acceptance item clicked through as the client role, and the included "
         "sub-tickets")


def gate_mode(env=None):
    """`warn` or `enforce` from MODE_ENV. Unset, blank or unknown -> DEFAULT_MODE
    (a typo never silently ENFORCES)."""
    env = os.environ if env is None else env
    val = (env.get(MODE_ENV) or "").strip().lower()
    return val if val in MODES else DEFAULT_MODE


def _content_lines(text):
    """Lines of `text` outside ``` code fences (fence markers excluded)."""
    fence = False
    for raw in (text or "").splitlines():
        if _FENCE_RE.match(raw):
            fence = not fence
            continue
        if not fence:
            yield raw


def parse_epic_ref(body):
    """The epic number a ticket body declares with `Epic: #N`, or None. The
    first declaration outside code fences wins."""
    if not isinstance(body, str):
        return None
    for line in _content_lines(body):
        m = _EPIC_LINE_RE.match(line)
        if m:
            return int(m.group(1))
    return None


def is_rehearsal_comment(body):
    """True iff `body` carries a line-anchored `Epic-rehearsal:` marker outside
    code fences and outside a `>` quote."""
    if not isinstance(body, str):
        return False
    return any(_REHEARSAL_LINE_RE.match(ln) for ln in _content_lines(body))


def lists_ticket(body, number):
    """True iff `body` names `#<number>` exactly (`#42`, never `#420`, never a
    cross-repo `other/repo#42`) on a line outside fences and `>` quotes."""
    rx = re.compile(r'(?<![\w/#-])#%d(?!\d)' % int(number))
    return any(rx.search(ln) for ln in _content_lines(body)
               if not ln.lstrip().startswith(">"))


def rehearsal_evidence(body):
    """`(ok, missing)` -- the rehearsal comment names the copy it ran on, has at
    least one PASS table row and no FAIL table row. `missing` says what is not
    there (a FAIL row is quoted back)."""
    verdicts = _table_verdicts(body)
    failed = [row for v, row in verdicts if v == "fail"]
    if failed:
        return False, "an acceptance row FAILED (%s)" % failed[0][:120]
    missing = []
    if not _COPY_RE.search(body or ""):
        missing.append("the fresh copy it ran on (REFRESH-DEV-BOX-FROM-PROD "
                       "refresh id + date)")
    if not verdicts:
        missing.append("a pass/fail table row per epic acceptance item")
    return (not missing), "; ".join(missing)


def _table_verdicts(body):
    """`[("pass"|"fail", row)]` for each markdown table DATA row whose first
    result cell (any cell after the first) leads with a pass/fail token."""
    lines = [ln.strip() for ln in _content_lines(body)]
    out = []
    for i, row in enumerate(lines):
        if not row.startswith("|") or _SEPARATOR_ROW_RE.match(row):
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if nxt.startswith("|") and _SEPARATOR_ROW_RE.match(nxt):
            continue                              # the header row
        for cell in (c.strip() for c in row.strip("|").split("|")[1:]):
            if _FAIL_CELL_RE.match(cell):
                out.append(("fail", row))
                break
            if _PASS_CELL_RE.match(cell):
                out.append(("pass", row))
                break
    return out


def head_from_body(body):
    """The `HEAD: <sha>` a hand-off body declares (>= 8 hex, the same shape the
    --body-file path verifies), or None."""
    m = re.search(r'(?im)^[ \t]*[-*]?[ \t]*\**HEAD\**[ \t]*:[ \t]*\**[ \t]*'
                  r'`?([0-9a-fA-F]{8,40})`?', body or "")
    return m.group(1).lower() if m else None


def iso_epoch(iso):
    """GitHub ISO-8601 UTC -> epoch seconds, or None."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _gh(argv, cwd, runner, timeout=8):
    env = None if runner is not None else ghread._gh_env()
    return ghread._run(argv, cwd, timeout, env, runner)


def read_comments(number, slug, cwd=None, runner=None):
    """`(comments, err)`: `[{id, created_at, body}]` in creation order via the
    REST comments endpoint, or `(None, gate-unavailable:<reason>)`."""
    rc, out, err = _gh(["gh", "api", "repos/%s/issues/%s/comments"
                        % (slug, number), "--paginate", "-q", ".[]"],
                       cwd, runner)
    if rc != 0:
        return None, ghread._gate_unavailable(err, rc)
    items = []
    for line in (out or "").splitlines():
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("body"), str):
            items.append({"id": obj.get("id"), "body": obj["body"],
                          "created_at": obj.get("created_at")})
    return items, None


def head_sha_of(cwd=None, runner=None):
    rc, out, _ = _gh(["git", "rev-parse", "HEAD"], cwd, runner)
    sha = (out or "").strip()
    return sha if rc == 0 and sha else None


def head_commit_ts(sha, cwd=None, runner=None):
    """The committer time (epoch) of `sha`, or None when git cannot answer."""
    if not sha:
        return None
    rc, out, _ = _gh(["git", "log", "-1", "--format=%ct", sha], cwd, runner)
    try:
        return int((out or "").strip()) if rc == 0 else None
    except ValueError:
        return None


def evaluate(ticket, epic, comments, head_ts, head_sha=""):
    """Classify the epic's comments for `ticket`. Returns a dict:
    `verdict` (`pass` | `missing` | `unlisted` | `stale` | `incomplete`),
    `rehearsal` (the
    comment the verdict rests on, or None), `reason` (the exact missing item,
    None on pass) and `fresh_unverified` (True when head_ts is unknown)."""
    rehearsals = [c for c in comments or [] if is_rehearsal_comment(c.get("body"))]
    if not rehearsals:
        return {"verdict": "missing", "rehearsal": None, "fresh_unverified": False,
                "reason": "sub-ticket #%d declares `Epic: #%d`, but epic #%d has "
                          "no `Epic-rehearsal:` comment. Rehearse the WHOLE epic on "
                          "a fresh prod copy before the first gk hand-off and post "
                          "it on #%d as `Epic-rehearsal:` with %s (list #%d)."
                          % (ticket, epic, epic, epic, _WHAT, ticket)}
    listing = [c for c in rehearsals if lists_ticket(c.get("body"), ticket)]
    if not listing:
        newest = rehearsals[-1]
        return {"verdict": "unlisted", "rehearsal": newest, "fresh_unverified": False,
                "reason": "the `Epic-rehearsal:` on epic #%d (newest: comment %s, "
                          "%s) does not list #%d. Include #%d in the "
                          "rehearsal run and list it in the `Epic-rehearsal:` "
                          "comment." % (epic, newest.get("id"),
                                        newest.get("created_at"), ticket, ticket)}
    if head_ts is None:
        return _with_evidence(listing[-1], epic, ticket, True)
    fresh = [c for c in listing if (iso_epoch(c.get("created_at")) or 0) > head_ts]
    if fresh:
        return _with_evidence(fresh[-1], epic, ticket, False)
    newest = listing[-1]
    from datetime import datetime, timezone
    head_iso = datetime.fromtimestamp(head_ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return {"verdict": "stale", "rehearsal": newest, "fresh_unverified": False,
            "reason": "the newest `Epic-rehearsal:` on epic #%d that lists #%d "
                      "(comment %s, %s) is older than HEAD %s (committed %s). "
                      "Re-run the rehearsal on a fresh copy after the last "
                      "commit and post a new `Epic-rehearsal:` comment."
                      % (epic, ticket, newest.get("id"), newest.get("created_at"),
                         (head_sha or "?")[:12], head_iso)}


def _with_evidence(comment, epic, ticket, fresh_unverified):
    """`pass` when the chosen rehearsal carries its evidence, else
    `incomplete` naming exactly what is missing (or the FAIL row)."""
    ok, missing = rehearsal_evidence(comment.get("body"))
    if ok:
        return {"verdict": "pass", "rehearsal": comment, "reason": None,
                "fresh_unverified": fresh_unverified}
    return {"verdict": "incomplete", "rehearsal": comment,
            "fresh_unverified": fresh_unverified,
            "reason": "the `Epic-rehearsal:` on epic #%d that lists #%d (comment "
                      "%s, %s) is not a passing rehearsal: %s. Fix it on the copy, "
                      "re-run, and post a new `Epic-rehearsal:` comment."
                      % (epic, ticket, comment.get("id"), comment.get("created_at"),
                         missing)}


def composer_check(issue, repo, head_sha=None, *, cwd=None, runner=None,
                   env=None, authority=None, out=None, body=None):
    """The composer pre-flight. Returns `(block_reason | None, receipt_fields)`.

    `receipt_fields` is merged into the hand-off receipt: `{}` for a non-epic
    ticket (unchanged), else `epic`, `epic_rehearsal` (comment id or None) and
    `epic_gate` (`pass` | `warn` | `enforce` | `unknown`). Warn mode and every
    fail-open notice print to `out` (default stdout). With no `head_sha`, the
    HEAD is the hand-off `body`'s `HEAD:` line, else the local checkout's HEAD."""
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
        print("handoff: epic-rehearsal gate state unknown (ticket #%s unreadable: "
              "%s) -- skipped, fail-open (#1161)" % (issue, err), file=out)
        return None, {"epic_gate": "unknown"}
    epic = parse_epic_ref(obj.get("body"))
    if epic is None or epic == int(issue):
        return None, {}
    fields = {"epic": epic, "epic_rehearsal": None, "epic_gate": mode}
    comments, cerr = read_comments(epic, slug, cwd=cwd, runner=runner)
    if cerr:
        print("handoff: epic-rehearsal gate state unknown (epic #%d comments "
              "unreadable: %s) -- skipped, fail-open (#1161)" % (epic, cerr),
              file=out)
        fields["epic_gate"] = "unknown"
        return None, fields
    if not head_sha:
        head_sha = head_from_body(body) or head_sha_of(cwd, runner)
    res = evaluate(int(issue), epic, comments,
                   head_commit_ts(head_sha, cwd, runner), head_sha or "")
    if res["rehearsal"] is not None:
        fields["epic_rehearsal"] = res["rehearsal"].get("id")
    if res["verdict"] == "pass":
        if res["fresh_unverified"]:
            print("handoff: epic-rehearsal freshness unverifiable (HEAD commit "
                  "time unknown) -- accepted comment %s without the freshness check "
                  "(#1161)" % fields["epic_rehearsal"], file=out)
        fields["epic_gate"] = "pass"
        return None, fields
    if mode == "enforce":
        return "handoff BLOCK: %s (#1161)" % res["reason"], fields
    print("handoff WARN (epic-rehearsal gate, warn mode -- allowed; `%s=enforce` "
          "refuses): %s (#1161)" % (MODE_ENV, res["reason"]), file=out)
    fields["epic_gate"] = "warn"
    return None, fields
