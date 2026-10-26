"""gates.designdispatch -- the MAIN-authored-design check (#1061, owner
escalation 2026-09-17), run by the implementer before any work:
`python3 -m gates.designdispatch --issue N [--slug owner/repo] [--cwd D]`.

`check_issue` passes only when the NEWEST `Design-by:` comment on the issue is
`Design-by: main <managed-main id>` -- a managed MAIN-tier id (Opus 5.5 since
#1119, or Fable 5.1 through the transition; see `_accepted_main_ids`), i.e.
the design was authored by the managed MAIN session, per the owner's standing
#871 rule. FAIL-CLOSED: an unreadable comment thread (gh error / no network /
auth) or an unresolvable repo refuses with an honest reason. Its dispatch-time
hook (`evaluate` on an Agent payload) was retired by issue 1137 B1; the
commit-time block-commit-without-design.sh keeps design-first.

STDLIB ONLY at import; airuleset (MANAGED_MODEL / _gh_env) is imported lazily.
"""
import os
import re
import subprocess
import sys

from gates import ghread
from gates import ticketrefs

# Ticket-number parsing lives in gates/ticketrefs.py (#1165 split);
# `issue_numbers` is re-exported for callers + tests.
issue_numbers = ticketrefs.issue_numbers

# A `Design-by: <role> <model>` line, leading-bullet + bold tolerant (the
# MINOR-3 lesson shared with the design classifiers): a `**Design-by:**` /
# `- **Design-by:**` form has `**` before the label AND after the colon, so
# `\**` is allowed at both spots.
# #1064: tolerate + capture an optional ` (served: <id>)` audit suffix. The
# stamp now records the CONFIGURED (launch) model as `<model>` and the API-SERVED
# model in the suffix; `<model>` is still the FIRST token after the role (a
# `\S+` stops at the space before `(served:`), so the accept/refuse decision
# keys on the configured id unchanged, and `served` is available for the reason.
_DESIGN_BY_RE = re.compile(
    r"(?im)^[ \t>*#-]*\**[ \t]*Design-?by\**[ \t]*:[ \t]*\**[ \t]*"
    r"(?P<role>main|worker)\b[ \t]*(?P<model>\S+)?"
    r"(?:[ \t]*\(served:[ \t]*(?P<served>[^)]*)\))?")


def _norm_model(m):
    """A model id normalised for the exact-id match: `[..]` context tag stripped,
    lowercased. `claude-fable-5-1[1m]` and `claude-fable-5-1` both -> the tier
    id (the SAME tolerance airuleset.is_allowed_model applies)."""
    m = (m or "").strip().lower()
    return re.sub(r"\[[^\]]*\]$", "", m)


def _newest_design_by_match(comment_bodies):
    """The `_DESIGN_BY_RE` match in the NEWEST comment carrying a `Design-by:`
    line, or None. `comment_bodies` is the thread in CREATION order (oldest
    first); the last body with a `Design-by:` line wins. Kept separate so both
    `newest_design_by` (the 2-tuple decision) and `check_issue` (which also wants
    the `served` suffix for the reason text, #1064) share ONE 'newest wins' pass."""
    result = None
    for body in comment_bodies:
        m = _DESIGN_BY_RE.search(body or "")
        if m:
            result = m
    return result


def newest_design_by(comment_bodies):
    """(role, model) of the `Design-by:` line in the NEWEST comment that carries
    one, or None. The optional `(served: …)` audit suffix (#1064) is captured
    separately (see `check_issue`'s reason text) and does NOT change this 2-tuple
    contract -- the accept/refuse decision keys on role + the CONFIGURED model."""
    m = _newest_design_by_match(comment_bodies)
    if not m:
        return None
    return (m.group("role").lower(), (m.group("model") or "").strip())


def _fable_id():
    try:
        import model_lineup
        return model_lineup.FORMER_MAIN_MODEL
    except Exception:
        return "claude-fable-5-1"


def _accepted_main_ids():
    """The set of NORMALISED model ids accepted as a MAIN-authored design stamp
    (#1061/#1119): the CURRENT managed main (derived from MANAGED_MODEL — Opus
    5.5 since #1119) AND the immediately-prior main (Fable 5.1). Fable stays
    accepted through the transition so a design stamped by a still-running Fable
    main dispatches until that session is relaunched onto Opus 5.5 (running
    sessions keep the model they launched with). Both ids come from model_lineup
    (MANAGED_MODEL + FORMER_MAIN_MODEL; Fable is BANNED to dispatch, #1248). Degrades to
    the two known ids when airuleset is unimportable (the dependency-light gate
    path)."""
    try:
        import airuleset
        return {_norm_model(airuleset.MANAGED_MODEL), _norm_model(_fable_id())}
    except Exception:
        return {_norm_model("claude-opus-5-5"), _norm_model("claude-fable-5-1")}


def _project_main_id(slug):
    """#1246: the main model the dispatch repo's project declares
    (`model_lineup.PROJECT_MAIN_MODELS`, keyed by the repo NAME of `owner/name`,
    e.g. pz-server = Opus 4.6), or None. Keyed on the slug, not the cwd: a
    dispatch may run from a worktree. model_lineup is a stdlib leaf."""
    try:
        import model_lineup
        name = (slug or "").rsplit("/", 1)[-1]
        return model_lineup.PROJECT_MAIN_MODELS.get(name)
    except Exception:  # noqa: BLE001 -- an unreadable lineup accepts nothing extra
        return None


def _gh_env():
    try:
        import airuleset
        return airuleset._gh_env()
    except Exception:
        return None


def _resolve_slug(cwd, timeout=6):
    # The repo is resolved from the cwd (the target repo's checkout); a
    # timeout or failure returns None, which the caller refuses (fail-closed).
    # #1070 item 1 -- resolve the slug from the LOCAL git remote FIRST (never an
    # API call, so it survives the owner identity's hourly GraphQL exhaustion);
    # `gh repo view` (GraphQL) is only the fallback. Without this, slug
    # resolution itself would fail under GraphQL exhaustion and block a
    # legitimate dispatch on an unresolvable repo.
    git_slug = ghread.resolve_slug(cwd)
    if git_slug:
        return git_slug
    try:
        r = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner",
             "-q", ".nameWithOwner"],
            cwd=cwd or None, capture_output=True, text=True, timeout=timeout,
            env=_gh_env())
    except Exception:
        return None
    if r.returncode != 0:
        return None
    slug = (r.stdout or "").strip()
    return slug or None


def _fetch_comment_bodies(slug, number, cwd, timeout=8):
    """(bodies, err) for `<slug>#<number>` via gates.ghread -- REST-first
    (`gh api …/comments`, a core-budget call that survives the owner
    identity's hourly GraphQL exhaustion) with a `gh issue view` GraphQL
    fallback. `err` is a `gate-unavailable:<reason>` string when BOTH paths
    fail (quota/transport), so check_issue blocks fail-closed with an HONEST
    reason -- never the words "missing design" (#1070 item 1). Bodies are in
    CREATION order on success."""
    return ghread.read_comment_bodies(number, slug, cwd=cwd, timeout=timeout)


def check_issue(number, slug, cwd, fetch=None, fable_id=None):
    """(ok, reason) -- is `#number`'s newest design comment authored by the
    managed MAIN? FAIL-CLOSED: an unreadable thread returns (False, ...).
    `fetch(slug, number, cwd)` -> [bodies]|None is injected in tests. #1060 L3a:
    the design is ALWAYS authored by the managed main (the implementer never
    designs). #1119: the accepted design model is any managed MAIN-tier id --
    Opus 5.5 (the new main) plus Fable 5.1 (the prior main, accepted through the
    transition) -- derived from model_lineup via `_accepted_main_ids`, so the
    lineup has ONE source and a relaunched main is accepted the day it ships."""
    fetch = fetch or _fetch_comment_bodies
    # #1119: accept any MANAGED MAIN-tier id (Opus 5.5 now, plus Fable 5.1 through
    # the transition), derived from the single-source model_lineup. An
    # injected `fable_id` (tests / an explicit override) is added to the set, so
    # existing injected-id tests keep passing.
    accepted = _accepted_main_ids()
    if fable_id:
        accepted.add(_norm_model(fable_id))
    project_main = _project_main_id(slug)
    if project_main:                       # #1246: the repo's own declared main
        accepted.add(_norm_model(project_main))
    res = fetch(slug, number, cwd)
    # #1070 item 1 -- the fetch may return the new `(bodies, err)` tuple (a
    # ghread read: `err` a `gate-unavailable:<reason>` when BOTH REST and
    # GraphQL failed) OR, for the pre-#1070 injected-test contract, a bare
    # list / None. Normalise both.
    if isinstance(res, tuple):
        bodies, err = res
    else:
        bodies, err = res, None
    if err:
        # a READ failure (quota/transport) -- block fail-closed but with an
        # HONEST reason; NEVER "no design" (a genuinely-present main design must
        # not read as missing just because the owner identity's GraphQL budget
        # is exhausted). #1070.
        return False, err
    if bodies is None:
        return False, ("could not read #%d's comments (gh error / no network) "
                       "-- refusing (fail-closed)" % number)
    m = _newest_design_by_match(bodies)
    if m is None:
        return False, ("#%d has no `Design-by:` comment -- the Fable main must "
                       "author the design (airuleset.py design-record) before "
                       "an autopilot-worker is dispatched" % number)
    role = m.group("role").lower()
    model = (m.group("model") or "").strip()
    # #1064: the CONFIGURED model is `<model>`; the API-served model, when the
    # stamp carries the audit suffix, is surfaced in the reason so a refused
    # float is legible ("configured X, served Y").
    served = (m.group("served") or "").strip()
    served_note = " (served: %s)" % served if served else ""
    if role != "main":
        return False, ("#%d's newest design comment is `Design-by: %s %s%s` -- the "
                       "design must be authored by the MAIN session, not the "
                       "worker (#871/#1061)" % (number, role, model or "?",
                                                served_note))
    if _norm_model(model) not in accepted:
        return False, ("#%d's newest design comment is `Design-by: main %s%s` -- "
                       "expected a managed MAIN model (%s); the design must be "
                       "authored by the managed main session (#1061/#1119)"
                       % (number, model or "?", served_note,
                          ", ".join(sorted(accepted))))
    return True, "ok"


def _run_issue_cli(argv, fetch=None, fable_id=None, resolve_slug=None):
    """#1060 L3b — the RECEIVING-SIDE design gate the IMPLEMENTER runs before any
    work. `python3 gates/designdispatch.py --issue N [--slug owner/repo] [--cwd D]`
    exits 2 unless `#N`'s newest design comment is role-main + a managed MAIN id
    (`check_issue`), else 0 — fail-closed on an unreadable thread or an
    unresolvable repo.

    On the two independent-session `dual` box the implementer is reached by
    `SendMessage`, not an `Agent` dispatch, so the precondition is checked
    HERE, on the receiver, before it touches the ticket.

    `fetch` / `fable_id` / `resolve_slug` are injected in tests; production uses
    the module defaults. Returns the exit code (never calls sys.exit)."""
    import argparse
    p = argparse.ArgumentParser(prog="designdispatch",
                                description="receiving-side design gate (#1060)")
    p.add_argument("--issue", type=int, required=True,
                   help="the ticket number to verify")
    p.add_argument("--slug", default=None,
                   help="owner/repo (default: resolved from --cwd via gh)")
    p.add_argument("--cwd", default=None,
                   help="repo checkout dir (default: the process cwd)")
    ns = p.parse_args(argv)
    cwd = ns.cwd or os.getcwd()
    resolver = resolve_slug or _resolve_slug
    slug = ns.slug or resolver(cwd)
    if not slug:
        sys.stderr.write(
            "DESIGN-GATE BLOCK (#1060): could not resolve the repo (pass "
            "--slug owner/repo, or run inside the checkout) — refusing to "
            "implement an unverifiable design (fail-closed)\n")
        return 2
    ok, reason = check_issue(ns.issue, slug, cwd, fetch=fetch, fable_id=fable_id)
    if not ok:
        sys.stderr.write(
            "DESIGN-GATE BLOCK (#1060) — %s\n\n"
            "  The IMPLEMENTER only implements a design the Fable MAIN authored "
            "(#871/#1061).\n  Post a `Design-question:` comment on #%d and STOP; "
            "the main re-authors\n  or clarifies the design, then dispatches "
            "again.\n" % (reason, ns.issue))
        return 2
    sys.stdout.write("design-gate ok (#1060): #%d has a Design-by: main "
                     "<Fable id> comment — safe to implement\n" % ns.issue)
    return 0


def main():
    sys.exit(_run_issue_cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
