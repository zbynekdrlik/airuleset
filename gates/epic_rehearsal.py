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

_FENCE_RE = re.compile(r'^[ \t]*```')
# `Epic: #N` -- line-anchored, an optional bullet / bold, the colon DIRECTLY
# after the word (so `Epic-rehearsal:` and prose "the epic: #N" never match).
_EPIC_LINE_RE = re.compile(
    r'^[ \t]*(?:[-*][ \t]+)?\**Epic\**[ \t]*:\**[ \t]*#(\d+)\b', re.IGNORECASE)
# `Epic-rehearsal:` -- line-anchored (bullet / heading / bold allowed), never a
# `>` quote and never mid-sentence: a reply QUOTING an old rehearsal must not
# satisfy the gate (the #818 / #1053 marker lesson).
_REHEARSAL_LINE_RE = re.compile(
    r'^[ \t]*(?:[-*][ \t]+)?(?:#{1,6}[ \t]+)?\**Epic-rehearsal\**[ \t]*:',
    re.IGNORECASE)

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
    """True iff `body` names `#<number>` exactly (`#42`, never `#420`)."""
    return bool(re.search(r'#%d(?!\d)' % int(number), body or ""))


def _epoch(iso):
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
    `verdict` (`pass` | `missing` | `unlisted` | `stale`), `rehearsal` (the
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
        return {"verdict": "pass", "rehearsal": listing[-1], "reason": None,
                "fresh_unverified": True}
    fresh = [c for c in listing if (_epoch(c.get("created_at")) or 0) > head_ts]
    if fresh:
        return {"verdict": "pass", "rehearsal": fresh[-1], "reason": None,
                "fresh_unverified": False}
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


def composer_check(issue, repo, head_sha=None, *, cwd=None, runner=None,
                   env=None, authority=None, out=None):
    """The composer pre-flight. Returns `(block_reason | None, receipt_fields)`.

    `receipt_fields` is merged into the hand-off receipt: `{}` for a non-epic
    ticket (unchanged), else `epic`, `epic_rehearsal` (comment id or None) and
    `epic_gate` (`pass` | `warn` | `enforce` | `unknown`). Warn mode and every
    fail-open notice print to `out` (default stdout)."""
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
        head_sha = head_sha_of(cwd, runner)
    res = evaluate(int(issue), epic, comments,
                   head_commit_ts(head_sha, cwd, runner), head_sha or "")
    if res["rehearsal"] is not None:
        fields["epic_rehearsal"] = res["rehearsal"].get("id")
    if res["verdict"] == "pass":
        if res["fresh_unverified"]:
            print("handoff: epic-rehearsal freshness unverifiable (HEAD commit "
                  "time unknown) -- accepted comment %s on the listing alone "
                  "(#1161)" % fields["epic_rehearsal"], file=out)
        fields["epic_gate"] = "pass"
        return None, fields
    if mode == "enforce":
        return "handoff BLOCK: %s (#1161)" % res["reason"], fields
    print("handoff WARN (epic-rehearsal gate, warn mode -- allowed; `%s=enforce` "
          "refuses): %s (#1161)" % (MODE_ENV, res["reason"]), file=out)
    fields["epic_gate"] = "warn"
    return None, fields
