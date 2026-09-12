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
