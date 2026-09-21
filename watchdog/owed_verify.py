"""watchdog.owed_verify — #1097: verify a locally-derived "owed" candidate set
against GitHub (state + closed_at) and scope it to the window's ROLE, composed
onto the existing #534 ``owned_closed`` seam that watchdog jobs 24
(``card_reconcile``) and 25 (``report_reconcile``) share.

Why this leaf exists
--------------------
Both jobs derive their owed set from ``watchdog.cards.merged_closes``, which runs
a ``Closes/Fixes/Resolves #N`` regex over EVERY commit merged on the delivery ref
inside the 48 h window. Two defects that produced a false owner escalation on gk
(2026-09-21, odoo-erp #4 — closed 2026-01-17, re-derived as freshly owed from the
prose sentence ``Release-fix #4 wrapped the test body`` in commit ``1e9e1f4d0``):

  1. Nothing checked GitHub — a prose *mention* of an old number became "closed by
     this commit". The regex belt (``(?<![\\w-])`` on ``_CLOSES_RE`` in
     ``cards.py``) stops the ``-fix`` hyphen case, but GitHub's own rule closes on
     the keyword ANYWHERE in a message, so no regex can tell a fresh close from a
     re-mention of a ticket closed months ago. The only authority is the GitHub
     row: ``state == "closed"`` AND ``closed_at`` inside the same window.
  2. gk runs two ROOTS of one repo (``odoo-erp`` = FLOW/review window,
     ``odoo-erp-infra`` = INFRA window, #1065). Both derive the identical
     ``merged_closes`` set, so a ticket was owed — and escalated — on BOTH. A
     ticket is owed only to the window whose ROLE owns its work class.

Design (Approach 1): one ETag-cached REST read per owed candidate per sweep
answers BOTH defects (state/closed_at for defect 1, labels for defect 2), and the
whole thing composes onto the existing ``owned_closed`` seam so jobs 24 and 25 are
fixed together with no signature change.

Fail direction: a candidate whose row cannot be read (transport / err /
unparseable / a fetch that raises) is KEPT — the same "report now is the safe
default, never wait forever for a timestamp" doctrine ``cards._normalize_closed``
follows; a row that reads fine and says open / closed-outside-window is DROPPED
and journaled. Missing/unreadable labels resolve to ``infra`` (``work_class``'s own
fail-safe), so an unreadable candidate stays owed on the INFRA root and is dropped
only on the FLOW root — never lost on both.

Leaf-module discipline: pure functions + one filter factory, stdlib-only at import
time; ``gates.ghread`` / ``cli_work_class`` / ``cli_concurrency`` /
``watchdog.cards._iso_epoch`` are imported LAZILY inside the functions so this
module has no import-time dependency on any of them.
"""

import os
import time

# The role -> kept-work-class map (#1065 role partition). resolve_role returns
# "review" for a FLOW window, "infra" for an INFRA window, None for a single-
# window box. work_class returns "infra" or "independent" (cli_work_class).
_REVIEW = "review"
_INFRA_ROLE = "infra"
_INFRA_CLASS = "infra"
_INDEPENDENT_CLASS = "independent"


def _iso_to_epoch(s):
    """The tz-aware ISO-8601 -> epoch float parser (or None), reused VERBATIM
    from ``watchdog.cards`` so the ``closed_at`` window comparison uses the exact
    same parse the rest of the rider uses (no parallel derivation, #367). Lazy so
    this leaf keeps no import-time dependency on ``watchdog.cards``."""
    from watchdog.cards import _iso_epoch
    return _iso_epoch(s)


def _canonical_slug(root):
    """The canonical ``owner/name`` for the checkout at ``root`` (#1094 fork-aware,
    local-git only — never the directory basename). None when no remote resolves
    (a temp/non-repo dir), which is harmless: an injected ``fetch`` ignores the
    slug, and ``work_class(None, ...)`` falls through to the label-based path."""
    from gates.ghread import canonical_slug
    return canonical_slug(root)


def _work_class(slug, labels):
    """``"infra"`` / ``"independent"`` for the row's labels (#993 fail-safe:
    None/non-list labels -> ``infra``)."""
    from cli_work_class import work_class
    return work_class(slug, labels)


def _resolve_role(cwd):
    """The pane/window ROLE for ``cwd`` (``"review"`` / ``"infra"`` / None), or
    None on ANY resolver error — fail-open so a resolver hiccup keeps everything
    owed rather than silently dropping a real report."""
    try:
        from cli_concurrency import resolve_role
        return resolve_role(cwd)
    except Exception:  # noqa: BLE001 — a resolver error must never drop owed work
        return None


def _role_keeps(role, cls):
    """The #1065 role partition: an INFRA window owes only ``infra``-class
    tickets, a REVIEW (FLOW) window only ``independent``-class ones, and a role-
    less (single-window) box owes everything. Any unexpected role value keeps
    everything (fail-open toward owed)."""
    if role == _INFRA_ROLE:
        return cls == _INFRA_CLASS
    if role == _REVIEW:
        return cls == _INDEPENDENT_CLASS
    return True


# A CLOSED issue's row (state / closed_at / labels) is IMMUTABLE barring a
# reopen — and card_reconcile's own `reopen_fetch` is the authoritative reopen
# guard, not this verifier — so the row can be cached aggressively. The
# `owned_closed` seam hands verify_owed the WHOLE 48h `merged_closes` set (dozens
# on a multi-stream repo like odoo-erp), NOT the 0-5 post-trim owed set, so a
# short `max_age` re-fetched every candidate every sweep, per root — a real
# per-sweep subprocess fan-out toward the 120 s systemd kill (#1097 review-1). A
# 15-min TTL collapses that to a handful of reads/hour per candidate while still
# re-validating a reopen ~4x/hour, and lets gk's two roots (odoo-erp FLOW +
# INFRA) share ONE read across sweeps (a 304 does not refresh the cache `ts`, so
# only the `max_age` short-circuit — not the 304 path — actually dedups the roots).
_ROW_CACHE_MAX_AGE_S = 900


def _make_default_fetch(root, now):
    """The production ``fetch(slug, n) -> (obj, err)``: an ETag-conditional REST
    read of the issue row (#1087, budget-free on a 304), deduped across sweeps
    and across gk's two roots via ``max_age`` (a closed row is immutable)."""
    from gates.ghread import rest_get_cached

    def fetch(slug, n):
        return rest_get_cached("repos/%s/issues/%d" % (slug, n),
                               cwd=root, now=now, max_age=_ROW_CACHE_MAX_AGE_S)

    return fetch


def _safe_fetch(fetch, slug, n):
    """Call ``fetch`` and normalize ANY exception into ``(None, err)`` so a broken
    fetch is treated as unreadable (KEPT), never a crash that kills the sweep."""
    try:
        return fetch(slug, n)
    except Exception as e:  # noqa: BLE001 — a fetch crash must never drop owed work
        return None, "fetch-exception: %r" % (e,)


def verify_owed(root, closed, since_ts, role, fetch, now, journal=None):
    """Filter a ``{issue_num: commit_ts}`` owed candidate set down to the tickets
    that are TRULY owed by this box's window, against GitHub.

    For each candidate ``n`` (in ascending order — deterministic journal), the
    row is either RELIABLY classified (drop or role-scope) or UNREADABLE (keep):

      * UNREADABLE -> KEEP (fail toward owed) + journal ``keep-unreadable``, and
        NEVER role-scoped. An unreadable row's work-class is only a guess
        (``work_class(None) -> infra``), so role-DROPPING it would shrink the
        acted set and pop the CALLER's #534 per-ticket dedup on a TRANSIENT gh
        failure -> a re-nudge / re-escalate the next time gh recovers (the #534
        MINOR-2 invariant, and the very re-escalation class #1097 exists to kill).
        So a transiently-unreadable candidate stays owed regardless of role; the
        next readable sweep role-scopes it reliably. Unreadable =
        ``err`` / a fetch that raised / ``obj`` not a dict / a ``state`` that is
        neither ``"closed"`` nor ``"open"`` (a rate-limit JSON object, a truncated
        body) / a ``closed_at`` that will not parse.
      * ``state == "open"`` -> DROP + journal ``state=open`` (reliably not owed).
      * ``state == "closed"`` AND ``closed_at`` parses AND ``closed_at < since_ts``
        (closed before the window) -> DROP + journal ``closed_at <ts> outside
        window``. This is the odoo-erp #4 case.
      * ``state == "closed"`` AND ``closed_at >= since_ts`` -> the close is real
        and in-window, so the labels are RELIABLE -> role check
        ``_role_keeps(role, work_class(slug, labels))``; dropped -> journal
        ``role=<role> class=<cls>``. A role-drop here is safe: the OTHER role's
        root owns the ticket (reliably, with its own dedup), so popping this
        root's dedup is correct.

    ``since_ts`` None disables the window comparison (fail-open toward owed).
    ``journal`` is an optional ``str -> None`` sink (e.g. ``list.append``);
    EXACTLY ONE line is emitted per candidate. Returns the kept ``{n: ts}``
    subset; an empty ``closed`` returns a copy of it unchanged (no fetch, no
    journal)."""
    if not closed:
        return dict(closed)
    if journal is None:
        def journal(_line):
            return None
    base = os.path.basename(str(root).rstrip("/")) or str(root)
    slug = _canonical_slug(root)
    if fetch is None:
        fetch = _make_default_fetch(root, now)

    keep = {}
    for n in sorted(closed):
        ts = closed[n]
        obj, err = _safe_fetch(fetch, slug, n)
        # --- UNREADABLE row: KEEP, never role-scope (fail toward owed; the
        # role class of an unreadable row is only a guess and dropping it pops
        # the caller's #534 dedup on a transient gh failure). ---
        if err or not isinstance(obj, dict):
            keep[n] = ts
            journal("owed-verify keep-unreadable #%d %s: %s" % (n, base, err or "no-row"))
            continue
        state = obj.get("state")
        if state == "open":
            journal("owed-verify drop #%d %s: state=open" % (n, base))
            continue
        if state != "closed":
            # An unexpected/absent state (a rate-limit JSON object, a truncated
            # body) — cannot confirm, so fail toward owed (never a silent drop).
            keep[n] = ts
            journal("owed-verify keep-unreadable #%d %s: state=%r" % (n, base, state))
            continue
        closed_at = _iso_to_epoch(obj.get("closed_at"))
        if closed_at is None:
            keep[n] = ts
            journal("owed-verify keep-unreadable #%d %s: closed_at %r unparseable"
                    % (n, base, obj.get("closed_at")))
            continue
        if since_ts is not None and closed_at < since_ts:
            journal("owed-verify drop #%d %s: closed_at %s outside window"
                    % (n, base, obj.get("closed_at")))
            continue
        # --- RELIABLE close, in-window: labels are trustworthy -> role scope. ---
        cls = _work_class(slug, obj.get("labels"))
        if not _role_keeps(role, cls):
            journal("owed-verify drop #%d %s: role=%s class=%s"
                    % (n, base, role, cls))
            continue
        keep[n] = ts
    return keep


def make_verified_closed_filter(inner, cwd_by_root=None, since_fn=None,
                                fetch=None, role_fn=None, now_fn=None):
    """Wrap the #534 owner-scoping filter ``inner(root, closed)`` with the GitHub
    state/window + role verifier, preserving the ``owned_closed`` seam contract
    (``(root, closed) -> dict | None``) so ``run_once`` can hand ONE composed
    callable to BOTH jobs unchanged.

    Composition order is locked: ``inner`` runs FIRST (its ``None`` = SKIP this
    root this sweep passes straight through untouched; its ``{}`` = genuinely
    empty passes through so the caller still cleans its dedup up; neither triggers
    a GitHub read), THEN ``verify_owed`` on whatever ``inner`` kept.

      * ``cwd_by_root`` — optional ``{root: pane_cwd}`` map; ``resolve_role`` reads
        the pane cwd when present, else ``root`` itself (gk's two roots are
        distinct directories, so ``root`` alone already distinguishes their roles).
      * ``since_fn`` — nullary callable returning the window start ``since_ts``
        (``now - CARD_WINDOW_S``); None -> no window bound (fail-open).
      * ``fetch`` / ``role_fn`` / ``now_fn`` — test seams; default to the real
        ETag-cached read, ``resolve_role`` and ``time.time``.

    Per-``(root, candidate-set)`` MEMO: the two jobs pass the identical set for a
    given root within one sweep, so the second call is served from the memo — no
    second fetch and no double journal (the design's "journaled once per
    root+sweep"). The memo lives exactly one filter (run_once builds a fresh
    filter per sweep). Journal lines accumulate on the returned callable's
    ``.logs`` list, which run_once appends to the job's log output."""
    logs = []
    memo = {}

    def owned(root, closed):
        base = inner(root, closed)
        if base is None:
            return None                    # SKIP this root — no verify, no read
        if not base:
            return base                    # genuinely empty -> caller cleans up
        key = (root, frozenset(base))
        if key in memo:
            return memo[key]
        cwd = (cwd_by_root or {}).get(root, root)
        role = (role_fn or _resolve_role)(cwd)
        since_ts = since_fn() if callable(since_fn) else since_fn
        now = (now_fn or time.time)()
        result = verify_owed(root, base, since_ts, role, fetch, now,
                             journal=logs.append)
        memo[key] = result
        return result

    owned.logs = logs
    return owned
