"""cli_work_class — the ONE home for autopilot orchestration classification (#993).

Owner directives 2026-09-12 (#992/#993): autopilot acceleration must respect that
NOT everything is safe to run in parallel — two axes bound parallelism:

  1. WORK CLASS — `infra` work (the fleet harness: CI/gates/release/deploy tooling,
     and the WHOLE `zbynekdrlik/airuleset` repo) is STRICTLY SERIAL: at most one
     live infra lane. `independent` work refills freely.
  2. DEPENDENCY — a unit that must follow the EVALUATION of a previous one
     (`Depends-on: #N`) is not dispatchable until that predecessor is CLOSED
     (closing with evidence IS the evaluation in this harness).

This module is PURE + stdlib-only + dependency-injected for IO (a `state_fn` for
gh issue state; the callers own the gh/git calls). It is the single source of
truth for `work_class`, `Depends-on:` parsing/resolution, the dep-wait predicate,
the dispatchable predicate, and the fail-safe lane classification — reused by the
picker (`cli_quals_cmd`), both watchdog nudges (`watchdog/goal.py`,
`watchdog/queue_arrival_recheck.py`), and the lane-overlap receipt
(`cli_lane_overlap.py`). No shim, no duplicate derivation (the #367 one-derivation
invariant).
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
    refs = []
    for m in _REF_RE.finditer(lines[-1]):
        repo = m.group("repo")
        num = m.group("num")
        refs.append((repo + "#" + num) if repo else ("#" + num))
    return refs


def depends_on_refs(body, comments):
    """The EFFECTIVE dep-refs for a ticket: the LAST supervisor comment STARTING
    with ``Depends-on:`` wins (a later ruling supersedes the body); else the
    body's ``Depends-on:`` line. ``comments`` is a list of comment-body strings
    in chronological order."""
    comment_lines = [c for c in (comments or [])
                     if isinstance(c, str)
                     and c.lstrip().lower().startswith("depends-on:")]
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


def dispatchable(work_cls, is_dep_wait, infra_lane_live):
    """A ticket is dispatchable iff its deps are satisfied AND it is either
    ``independent`` OR (``infra`` with NO live infra lane). The combined set the
    picker and both nudges use: ``workable ∧ deps-satisfied ∧
    (independent ∨ ¬live-infra-lane)`` (#993 items 3/4/7)."""
    if is_dep_wait:
        return False
    if work_cls == INFRA and infra_lane_live:
        return False
    return True


def lane_class_from_issue_classes(classes):
    """A LIVE lane's class from the work-classes of the issue(s) it is working.
    An EMPTY list — an unresolvable lane (no issue number / no labels) — is
    ``infra`` (fail-safe serial, #993 item 2). Otherwise ``infra`` iff ANY of
    its issues is infra."""
    if not classes:
        return INFRA
    return INFRA if any(c == INFRA for c in classes) else INDEPENDENT


# --------------------------------------------------------------------------- #
# Resolution glue — the gh/git IO half. Still dependency-injected: `runner(argv,
# cwd)` and `gather_fn`/`labels_fn`/`state_fn` are passed in, so this stays
# offline-testable and carries NO hard gh/git import. Runs ONLY on the on-demand
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


def _issue_body_comments(number, runner, root):
    """`(body, [comment bodies])` for `number`, or `(None, [])` on failure."""
    try:
        obj = json.loads(runner(["gh", "issue", "view", str(number),
                                 "--json", "body,comments"], root))
    except Exception:
        return None, []
    if not isinstance(obj, dict):
        return None, []
    body = obj.get("body")
    body = body if isinstance(body, str) else ""
    comments = []
    for c in (obj.get("comments") or []):
        if isinstance(c, dict) and isinstance(c.get("body"), str):
            comments.append(c["body"])
    return body, comments


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


def dep_wait_map(rows, slug, runner, root):
    """`{row-key: [blocking ref strings]}` for the workable rows that are
    dep-wait (any `Depends-on:` referent OPEN/unresolvable/cyclic). Rows with no
    `Depends-on:` or all-closed deps are ABSENT. Capped; a shared dep is
    resolved once (state cache)."""
    out = {}
    checked = 0
    state_cache = {}

    def state_fn(repo, num):
        key = (repo, num)
        if key not in state_cache:
            state_cache[key] = issue_state(repo, num, runner, root)
        return state_cache[key]

    for number in rows:
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


def labels_of(number, runner, root):
    """A live lane's issue labels (gh `--json labels`), or None on failure."""
    try:
        obj = json.loads(runner(["gh", "issue", "view", str(number),
                                 "--json", "labels"], root))
    except Exception:
        return None
    if isinstance(obj, dict):
        return obj.get("labels")
    return None


def _gather_live_lanes(root):
    """Live worktree/wip lanes WITH their issue numbers — delegates to
    `cli_lane_overlap.gather_live_lanes` (the single lane-enumeration home)."""
    import cli_lane_overlap as lo
    return lo.gather_live_lanes(root)


def live_infra_lane(slug, runner, root, gather_fn=None, labels_fn=None):
    """True iff ANY live lane is infra-class. A lane with no resolvable issue
    (empty `issues`) is `infra` (fail-safe serial, #993 item 2); on the
    airuleset repo EVERY lane is infra (no label fetch needed). A gather failure
    fails safe to True (can't tell → serial)."""
    gather_fn = gather_fn or _gather_live_lanes
    labels_fn = labels_fn or labels_of
    try:
        lanes = gather_fn(root)
    except Exception:
        return True
    if not lanes:
        return False
    airuleset = (slug or "").strip().lower() == AIRULESET_REPO
    for lane in lanes:
        issues = lane.get("issues") if isinstance(lane, dict) else None
        classes = []
        for n in (issues or []):
            labels = None if airuleset else labels_fn(n, runner, root)
            classes.append(work_class(slug, labels))
        if lane_class_from_issue_classes(classes) == INFRA:
            return True
    return False


def dispatchable_numbers(rows, slug, dep_map, infra_lane_live):
    """PURE: `(dispatchable_keys:set, reason)`. dispatchable = workable ∧
    ¬dep-wait ∧ (independent ∨ ¬live-infra-lane). `reason` (when the set is
    empty and rows non-empty) is `dep-wait` iff ONLY deps hold everything back,
    else `infra-serial`."""
    dispatchable_set = set()
    held_infra = held_dep = False
    for number in rows:
        row = rows[number]
        labels = row.get("labels") if isinstance(row, dict) else None
        cls = work_class(slug, labels)
        is_dw = number in dep_map
        if dispatchable(cls, is_dw, infra_lane_live):
            dispatchable_set.add(number)
        elif is_dw:
            held_dep = True
        elif cls == INFRA and infra_lane_live:
            held_infra = True
    reason = None
    if rows and not dispatchable_set:
        reason = "dep-wait" if (held_dep and not held_infra) else "infra-serial"
    return dispatchable_set, reason


def classify_number(number, slug, runner, root, infra_lane_live):
    """The dispatch class of ONE issue: ``"dispatchable"`` | ``"infra-serial"``
    | ``"dep-wait"`` (the queue-arrival nudge's per-arrival gate, #993 item 4).
    Fetches the issue's labels + ``Depends-on:`` via ``runner`` (gh). ``infra_
    lane_live`` is resolved ONCE by the caller (via ``live_infra_lane``) and
    passed in so a wave of arrivals shares one live-lane read."""
    labels = labels_of(number, runner, root)
    cls = work_class(slug, labels)
    body, comments = _issue_body_comments(number, runner, root)
    refs = depends_on_refs(body, comments) if (body is not None or comments) else []
    deps = [d for d in (normalize_ref(r, slug) for r in refs) if d is not None]
    ni = _as_int(number)
    self_ref = (slug, ni) if (slug and ni is not None) else None
    is_dw = bool(deps) and dep_wait(
        deps, lambda rp, nu: issue_state(rp, nu, runner, root),
        self_ref=self_ref)[0]
    if dispatchable(cls, is_dw, infra_lane_live):
        return "dispatchable"
    return "dep-wait" if is_dw else "infra-serial"
