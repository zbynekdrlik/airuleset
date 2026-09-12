"""cli_work_class — the ONE home for autopilot orchestration classification (#993).

Owner directives 2026-09-12 (#992/#993): autopilot acceleration must respect that
NOT everything is safe to run in parallel. Round 2b (owner directive 4 + MAIN
REVIEW kolo 2) SIMPLIFIED this to TWO orthogonal concerns:

  1. DEPENDENCY — a unit that must follow the EVALUATION of a previous one
     (`Depends-on: #N`) is not dispatchable until that predecessor is CLOSED
     (closing with evidence IS the evaluation in this harness). This is the
     mechanism that bounds parallelism.
  2. WORK CLASS (`work_class`) — used ONLY to ROUTE a ticket to a role slice
     (`core-quals`/`slice-quals --role review|infra`). Infra serialisation is
     achieved by ROUTING the `infra` label into the infra role/target (which
     runs in the round-3 sequential mode), NEVER by class-gating a live infra
     lane inside a parallel target — that class-based mechanism was removed in
     round 2b (it was a second, redundant path for the same goal).

This module is PURE + stdlib-only + dependency-injected for IO (a `state_fn` for
gh issue state; the callers own the gh/git calls). It is the single source of
truth for `work_class`, `Depends-on:` parsing/resolution, the dep-wait predicate,
and the dispatchable predicate — reused by the picker (`cli_quals_cmd`), both
watchdog nudges (`watchdog/goal.py`, `watchdog/queue_arrival_recheck.py`), and
the lane-overlap receipt (`cli_lane_overlap.py`). No shim, no duplicate
derivation (the #367 one-derivation invariant).
"""
import json
import re

#: The `infra` work-class label (created in airuleset + odoo-erp, idempotent).
INFRA_LABEL = "infra"
#: The top lane-priority label — also forces `infra` class (a rework IS infra).
ARCHITECTURE_REWORK_LABEL = "architecture-rework"
#: The whole fleet-harness repo is infra-class regardless of any label.
AIRULESET_REPO = "zbynekdrlik/airuleset"

INFRA = "infra"
INDEPENDENT = "independent"

#: Only a SUPERVISOR/maintainer comment may OVERRIDE the body's Depends-on
#: (#993 review 6): a low-trust commenter (external/webterm dev) must never be
#: able to unblock a dep-wait ticket. A bare-string comment (legacy / test) is
#: treated as trusted; a dict comment is honoured only when its authorAssociation
#: is in this set.
_TRUSTED_ASSOCIATIONS = frozenset(("OWNER", "MEMBER", "COLLABORATOR"))


def _label_names(labels):
    """The set of label names from a gh `--json labels` value (a list of
    `{'name': ...}` dicts). Tolerant of malformed entries (skipped)."""
    names = set()
    if isinstance(labels, list):
        for lb in labels:
            if isinstance(lb, dict):
                n = lb.get("name")
                if isinstance(n, str):
                    names.add(n)
    return names


def work_class(repo, labels):
    """The orchestration work class of an issue: ``"infra"`` or ``"independent"``.

    - repo ``zbynekdrlik/airuleset`` → ``infra`` ALWAYS (the whole repo is the
      fleet harness), regardless of labels.
    - any other repo → ``infra`` iff the ``infra`` OR ``architecture-rework``
      label is present; else ``independent``.
    - MISSING / undeterminable labels (None, or not a list) → ``infra``
      (fail-safe serial, #993 item 6d — the same conservative direction
      `_row_action` takes for an undeterminable ownership read). A genuinely
      EMPTY label list (``[]``) is determinable-and-unlabelled → ``independent``.
    """
    if (repo or "").strip().lower() == AIRULESET_REPO:
        return INFRA
    if not isinstance(labels, list):
        return INFRA
    names = _label_names(labels)
    if INFRA_LABEL in names or ARCHITECTURE_REWORK_LABEL in names:
        return INFRA
    return INDEPENDENT


# --------------------------------------------------------------------------- #
# Dependency ordering — `Depends-on: #N[, #M]` (cross-repo `owner/repo#N` ok)
# --------------------------------------------------------------------------- #

_DEPENDS_ON_RE = re.compile(r"(?im)^[ \t>*#-]*Depends-on:[ \t]*(?P<refs>.+?)[ \t]*$")
# a repo slug is owner/name; a bare ref is just #N.
_REF_RE = re.compile(
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#(?P<num>\d+)")


def parse_depends_on(text):
    """The dep-ref strings from the LAST ``Depends-on:`` line in ``text`` (a
    body or a single comment), or ``[]`` if none. 'last one wins' WITHIN the
    text. Each ref is ``"#N"`` (this repo) or ``"owner/repo#N"`` (cross-repo)."""
    if not text:
        return []
    lines = _DEPENDS_ON_RE.findall(text)
    if not lines:
        return []
    # #993 review 9: the LAST `Depends-on:` line that yields >=1 parseable ref
    # wins — a trailing quoted example that parses to nothing must not override a
    # real declaration above it with an empty (fail-open) result.
    for line in reversed(lines):
        refs = []
        for m in _REF_RE.finditer(line):
            repo = m.group("repo")
            num = m.group("num")
            refs.append((repo + "#" + num) if repo else ("#" + num))
        if refs:
            return refs
    return []


def _comment_body_if_trusted(c):
    """The body of a comment ONLY if it may provide a Depends-on OVERRIDE: a bare
    string (legacy/test → trusted) or a dict whose authorAssociation is trusted
    (#993 review 6). None otherwise (a low-trust comment is ignored for override)."""
    if isinstance(c, str):
        return c
    if isinstance(c, dict) and isinstance(c.get("body"), str):
        if c.get("trusted") or c.get("authorAssociation") in _TRUSTED_ASSOCIATIONS:
            return c["body"]
    return None


def depends_on_refs(body, comments):
    """The EFFECTIVE dep-refs for a ticket: the LAST SUPERVISOR comment STARTING
    with ``Depends-on:`` wins (a later ruling supersedes the body); else the
    body's ``Depends-on:`` line. ``comments`` is a list of comment-body strings
    (legacy, all trusted) or dicts (``{"body", "trusted"|"authorAssociation"}``);
    a low-trust comment is IGNORED for the override (#993 review 6) so it can
    never unblock a dep-wait ticket."""
    comment_lines = []
    for c in (comments or []):
        b = _comment_body_if_trusted(c)
        if b is not None and b.lstrip().lower().startswith("depends-on:"):
            comment_lines.append(b)
    if comment_lines:
        return parse_depends_on(comment_lines[-1])
    return parse_depends_on(body or "")


def normalize_ref(ref, default_repo):
    """``(repo, number)`` from a ref string (``"#N"`` → ``default_repo``;
    ``"owner/repo#N"`` → its own repo). None on an unparseable ref."""
    m = _REF_RE.search(ref or "")
    if not m:
        return None
    repo = m.group("repo") or default_repo
    return (repo, int(m.group("num")))


def dep_wait(deps, state_fn, self_ref=None):
    """``(blocked, unsatisfied)``: ANY dependency that is OPEN or unresolvable
    (``state_fn`` → None) makes the ticket ``dep-wait``. ``deps`` is a list of
    ``(repo, number)`` tuples; ``state_fn(repo, number)`` returns
    ``"OPEN"`` / ``"CLOSED"`` / None (unresolvable). A dep equal to ``self_ref``
    (a 1-cycle) is treated as OPEN → blocked. Any longer cycle resolves to
    both-wait naturally (each member's predecessor is still open). Fail-safe:
    an unresolvable dep counts as blocking (#993 item 7)."""
    unsatisfied = []
    for dep in deps:
        if self_ref is not None and dep == self_ref:
            unsatisfied.append(dep)
            continue
        try:
            st = state_fn(dep[0], dep[1])
        except Exception:
            st = None
        if not (isinstance(st, str) and st.upper() == "CLOSED"):
            unsatisfied.append(dep)
    return (bool(unsatisfied), unsatisfied)


def dispatchable(is_dep_wait):
    """A ticket is dispatchable iff its deps are satisfied
    (``workable ∧ deps-satisfied``) — the combined set the picker and both
    nudges use (#993 item 7; the class-based live-infra-lane gate was removed in
    round 2b — infra serialisation is now ROUTING via ``--role``, not gating)."""
    return not is_dep_wait


# --------------------------------------------------------------------------- #
# Resolution glue — the gh IO half. Still dependency-injected: `runner(argv,
# cwd)` and `state_fn` are passed in, so this stays
# offline-testable and carries NO hard gh import. Runs ONLY on the on-demand
# dep paths (`--list`/`--audit`/`--dep-wait`/`--count-dispatchable`), NEVER on
# the hot `--count`/footer path — a per-row `gh issue view` for `Depends-on:` is
# O(workable). Kept in THIS module (the one orchestration-classification home)
# so `cli_quals` does not grow (#993 owner: "uz mam dost patchworkov").
# --------------------------------------------------------------------------- #

#: Cap on per-row body/comment fetches per dep-resolution call — a pathological
#: backlog must not spawn hundreds of gh reads. A realistic workable set is well
#: under this; a row beyond the cap is left un-annotated (dispatchable), the
#: non-disruptive default (an UNFETCHED body is "unknown deps", not a known
#: blocker — unlike an unresolvable DEP, which dep_wait() blocks).
_DEP_RESOLVE_CAP = 60


def _as_int(number):
    try:
        return int(number)
    except (TypeError, ValueError):
        return None


def _comments_from_json(raw_comments):
    """Normalise a gh `--json comments` value to a list of
    ``{"body", "authorAssociation"}`` dicts (the trusted-override shape)."""
    out = []
    for c in (raw_comments or []):
        if isinstance(c, dict) and isinstance(c.get("body"), str):
            out.append({"body": c["body"],
                        "authorAssociation": c.get("authorAssociation")})
    return out


def _issue_body_comments(number, runner, root):
    """`(body, [ {body, authorAssociation} ])` for `number`, or `(None, [])` on
    failure. Comments carry authorAssociation so `depends_on_refs` can honour a
    Depends-on OVERRIDE only from a trusted supervisor comment (#993 review 6)."""
    try:
        obj = json.loads(runner(["gh", "issue", "view", str(number),
                                 "--json", "body,comments"], root))
    except Exception:
        return None, []
    if not isinstance(obj, dict):
        return None, []
    body = obj.get("body")
    body = body if isinstance(body, str) else ""
    return body, _comments_from_json(obj.get("comments"))


#: Batched-read cap — the whole open-issue list in one gh call.
_META_LIST_LIMIT = 1000


def fetch_meta(numbers, runner, root=None):
    """ONE batched read of ``{int number: {"body", "comments"}}`` for the OPEN
    issues in ``numbers`` — a single ``gh issue list --state open --json
    number,body,comments`` instead of a per-row ``gh issue view`` for every
    workable row (#993 review 2: the O(workable) storm). None on any failure/
    non-list (the caller treats None as UNMEASURABLE → fail-safe skip, never a
    silent 'no deps'). Comments carry authorAssociation via ``_comments_from_json``
    so the trusted-override filter applies."""
    want = {ni for ni in (_as_int(n) for n in numbers) if ni is not None}
    if not want:
        return {}
    try:
        rows = json.loads(runner(
            ["gh", "issue", "list", "--state", "open", "--json",
             "number,body,comments", "-L", str(_META_LIST_LIMIT)], root))
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        num = r.get("number")
        if num not in want:
            continue
        body = r.get("body")
        out[num] = {"body": body if isinstance(body, str) else "",
                    "comments": _comments_from_json(r.get("comments"))}
    return out


def issue_state(repo, num, runner, root):
    """`'OPEN'`/`'CLOSED'`/None for `repo#num` (cross-repo via `-R`)."""
    argv = ["gh", "issue", "view", str(num), "--json", "state"]
    if repo:
        argv += ["-R", repo]
    try:
        obj = json.loads(runner(argv, root))
    except Exception:
        return None
    if isinstance(obj, dict):
        st = obj.get("state")
        if isinstance(st, str):
            return st.upper()
    return None


def _ref_str(repo, num, slug):
    """`#N` when `repo` is this repo (`slug`), else `owner/repo#N`."""
    return ("#%d" % num) if repo == slug else ("%s#%d" % (repo, num))


def dep_wait_map(rows, slug, runner, root, meta=None):
    """`{row-key: [blocking ref strings]}` for the workable rows that are
    dep-wait (any `Depends-on:` referent OPEN/unresolvable/cyclic). Rows with no
    `Depends-on:` or all-closed deps are ABSENT.

    `meta` (#993 review 2): the batched `{int: {"body","comments"}}` map from
    `fetch_meta` — when given, bodies/comments come from it (ONE gh call for the
    whole set) with NO per-row `gh issue view`; when None (legacy/test), each row
    is fetched per-row (capped at `_DEP_RESOLVE_CAP`). A shared dep's STATE is
    resolved once (state cache). Rows iterate in `_row_sort_key` order (oldest /
    architecture-rework first, #993 review 5) so the cap, when it bites, keeps
    the picker's earliest rows."""
    out = {}
    checked = 0
    state_cache = {}

    def state_fn(repo, num):
        key = (repo, num)
        if key not in state_cache:
            state_cache[key] = issue_state(repo, num, runner, root)
        return state_cache[key]

    for number in _sorted_row_keys(rows):
        if meta is not None:
            m = meta.get(_as_int(number))
            if m is None:
                continue                    # not in the open batch → no deps
            body, comments = m.get("body"), m.get("comments") or []
        else:
            if checked >= _DEP_RESOLVE_CAP:
                break
            checked += 1
            body, comments = _issue_body_comments(number, runner, root)
            if body is None and not comments:
                continue
        refs = depends_on_refs(body, comments)
        if not refs:
            continue
        deps = [normalize_ref(r, slug) for r in refs]
        deps = [d for d in deps if d is not None]
        if not deps:
            continue
        ni = _as_int(number)
        self_ref = (slug, ni) if (slug and ni is not None) else None
        blocked, unsat = dep_wait(deps, state_fn, self_ref=self_ref)
        if blocked:
            out[number] = [_ref_str(r[0], r[1], slug) for r in unsat]
    return out


def _sorted_row_keys(rows):
    """Row keys oldest-first with architecture-rework leading — the picker order
    (mirrors `cli_quals_cmd._row_sort_key`), so `dep_wait_map`'s cap keeps the
    earliest rows. Falls back to insertion order if a row lacks createdAt."""
    def key(k):
        row = rows.get(k) if isinstance(rows, dict) else None
        labels = row.get("labels") if isinstance(row, dict) else None
        names = {(lb or {}).get("name") for lb in (labels or [])
                 if isinstance(lb, dict)}
        rank = 0 if ARCHITECTURE_REWORK_LABEL in names else 1
        created = row.get("createdAt") if isinstance(row, dict) else ""
        return (rank, created or "")
    return sorted(rows, key=key)


def dispatchable_numbers(rows, slug, dep_map):
    """PURE: `(dispatchable_keys:set, reason)`. dispatchable = workable ∧
    ¬dep-wait. `reason` (when the set is empty and rows non-empty) is `dep-wait`
    (deps are now the only thing that holds a workable row back — the class-based
    infra-serial gate was removed in round 2b)."""
    dispatchable_set = set()
    for number in rows:
        if number not in dep_map:
            dispatchable_set.add(number)
    reason = "dep-wait" if (rows and not dispatchable_set) else None
    return dispatchable_set, reason


def classify_number(number, slug, runner, root):
    """The dispatch class of ONE issue: ``"dispatchable"`` | ``"dep-wait"`` (the
    queue-arrival nudge's per-arrival gate, #993 item 4). Fetches the issue's
    ``Depends-on:`` via ``runner`` (gh) and resolves it; a workable issue is
    dispatchable unless a dependency is still open (the class-based infra-serial
    gate was removed in round 2b)."""
    body, comments = _issue_body_comments(number, runner, root)
    refs = depends_on_refs(body, comments) if (body is not None or comments) else []
    deps = [d for d in (normalize_ref(r, slug) for r in refs) if d is not None]
    ni = _as_int(number)
    self_ref = (slug, ni) if (slug and ni is not None) else None
    is_dw = bool(deps) and dep_wait(
        deps, lambda rp, nu: issue_state(rp, nu, runner, root),
        self_ref=self_ref)[0]
    return "dispatchable" if dispatchable(is_dw) else "dep-wait"


def resolve_issue_deps(issues, slug, runner, root):
    """The lane-overlap receipt's deps field (#993 item 7): ``"satisfied"`` when
    EVERY issue's ``Depends-on:`` refs are closed (or none), else the list of
    unsatisfied blocking ref strings. Same fail-safe as ``dep_wait`` (an
    OPEN/unresolvable/cyclic dep is unsatisfied). Dep states are resolved once
    (shared cache across the dispatched issues)."""
    unsatisfied = []
    state_cache = {}

    def state_fn(repo, num):
        key = (repo, num)
        if key not in state_cache:
            state_cache[key] = issue_state(repo, num, runner, root)
        return state_cache[key]

    for n in issues:
        body, comments = _issue_body_comments(n, runner, root)
        refs = depends_on_refs(body, comments) if (body is not None or comments) else []
        deps = [d for d in (normalize_ref(r, slug) for r in refs) if d is not None]
        if not deps:
            continue
        ni = _as_int(n)
        self_ref = (slug, ni) if (slug and ni is not None) else None
        blocked, unsat = dep_wait(deps, state_fn, self_ref=self_ref)
        if blocked:
            unsatisfied += [_ref_str(r[0], r[1], slug) for r in unsat]
    return "satisfied" if not unsatisfied else unsatisfied
