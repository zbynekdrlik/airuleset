"""gates.designdispatch -- the MAIN-authored-design dispatch precondition
(#1061, owner escalation 2026-09-17). Entry for
hooks/block-dispatch-without-main-design.sh.

Active ONLY for an `autopilot-worker` Agent/Task dispatch. REFUSES the dispatch
unless the NEWEST `Design-by:` comment on every issue named in the prompt is
`Design-by: main <Fable id>` -- i.e. the design was authored by the Fable MAIN
session, per the owner's standing #871 rule. FAIL-CLOSED: an unreadable comment
thread (gh error / no network / auth) REFUSES with an honest reason (the owner's
rule -- never dispatch a worker onto an unverifiable design). A prompt with no
parseable issue number can't be checked and is ALLOWED (the same documented
fail-open the sibling overlap gate takes -- the design comment on the ticket is
the durable authority); the realistic autopilot dispatch always carries `#N`.

Bypass: `airuleset:design-by-ok <reason>` in the dispatch prompt -- allowed and
logged to ~/.claude/design-by-gate.log.

STDLIB ONLY at import; airuleset (MODEL_TIERS / _gh_env) is imported lazily.
"""
import json
import os
import re
import subprocess
import sys
import time

from gates import read_payload, field_of, emit_block_stderr, allow
from gates import ghread

DESIGN_BY_LOG = "design-by-gate.log"

# Ticket references in a dispatch prompt. The fleet's real prompts on this
# controller write the ticket WITHOUT `#` ("Work airuleset issue 1061 …") because
# the lane-overlap dispatch hook refuses `#N` mentions outside its receipt, so a
# `#N`-only extractor was VACUOUS — every real dispatch parsed to [] and the gate
# fail-opened (the #1028 vacuous-classifier class; #1061 supervisor review-3).
# Now parse `#N` AND bare `issue N` / `issues N, M` / `issue #N` / `issue-N` /
# `issue: N`. Require the `issue`/`#` PREFIX + 2-6 digits so a version string
# (0.1.326), a date (2026-09-17), a git sha, or an "items 1, 2, 3" run is NEVER
# mistaken for a ticket. Scoped to the FIRST ticket-bearing LINE (the dispatch's
# lead), mirroring the sibling block-dispatch-over-wdrain gate, so a folded /
# related "issue N" on a LATER body line ("5b. folded from issue 1046") never
# triggers a false precondition check on a ticket the worker is not working.
_TICKET_ANY_RE = re.compile(r"(?:issues?\s*[#:-]?\s*|#)\d{2,6}\b", re.IGNORECASE)
_HASH_RE = re.compile(r"#(\d{2,6})\b")
_TICKET_RUN_RE = re.compile(
    r"issues?\s*[#:-]?\s*(\d{2,6}(?:\s*(?:,|and)\s*#?\d{2,6})*)", re.IGNORECASE)
_NUM_RE = re.compile(r"\d{2,6}")

_BYPASS_RE = re.compile(r"airuleset:design-by-ok\s*(?P<reason>.*)", re.IGNORECASE)

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


def _log(line):
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", DESIGN_BY_LOG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        return


def issue_numbers(prompt):
    """Ticket numbers named in the dispatch's LEAD line (`#N` and bare `issue N`),
    de-duped, first-seen order. Empty when no line names a ticket."""
    text = prompt or ""
    lead = None
    for line in text.splitlines():
        if _TICKET_ANY_RE.search(line):
            lead = line
            break
    if lead is None:
        return []
    found = []  # (position, number) so #N and issue-N keep left-to-right order
    for m in _HASH_RE.finditer(lead):
        found.append((m.start(), int(m.group(1))))
    for m in _TICKET_RUN_RE.finditer(lead):
        base = m.start(1)
        for nm in _NUM_RE.finditer(m.group(1)):
            found.append((base + nm.start(), int(nm.group())))
    found.sort(key=lambda t: t[0])
    out, seen = [], set()
    for _, n in found:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


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
        import airuleset
        return airuleset.MODEL_TIERS["fable"]
    except Exception:
        return "claude-fable-5-1"


def _accepted_main_ids():
    """The set of NORMALISED model ids accepted as a MAIN-authored design stamp
    (#1061/#1119): the CURRENT managed main (derived from MANAGED_MODEL — Opus
    5.5 since #1119) AND the immediately-prior main (Fable 5.1). Fable stays
    accepted through the transition so a design stamped by a still-running Fable
    main dispatches until that session is relaunched onto Opus 5.5 (running
    sessions keep the model they launched with). Both ids come from the
    single-source MODEL_TIERS lineup, never a bare hard-coded string. Degrades to
    the two known ids when airuleset is unimportable (the dependency-light gate
    path)."""
    try:
        import airuleset
        return {_norm_model(airuleset.MANAGED_MODEL),
                _norm_model(airuleset.MODEL_TIERS["fable"])}
    except Exception:
        return {_norm_model("claude-opus-5-5"), _norm_model("claude-fable-5-1")}


def _gh_env():
    try:
        import airuleset
        return airuleset._gh_env()
    except Exception:
        return None


def _resolve_slug(cwd, timeout=6):
    # NOTE: the repo is resolved from the dispatch cwd (the target repo's
    # checkout), NOT the prompt's `in <repo>` name — autopilot is single-repo
    # per run, so the cwd is authoritative and needs no prompt parsing.
    # Timeout kept WELL under the hook's own timeout (settings/hooks.json) so the
    # gate's own timeout->None->block path runs BEFORE CC kills the hook (a
    # killed PreToolUse hook fail-OPENs — the exact inverse of this gate's
    # fail-closed contract; #1061 review).
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
    transition) -- derived from MODEL_TIERS via `_accepted_main_ids`, so the
    lineup has ONE source and a relaunched main is accepted the day it ships."""
    fetch = fetch or _fetch_comment_bodies
    # #1119: accept any MANAGED MAIN-tier id (Opus 5.5 now, plus Fable 5.1 through
    # the transition), derived from the single-source MODEL_TIERS lineup. An
    # injected `fable_id` (tests / an explicit override) is added to the set, so
    # existing injected-id tests keep passing.
    accepted = _accepted_main_ids()
    if fable_id:
        accepted.add(_norm_model(fable_id))
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


def _is_pull_request(number, slug, cwd):
    """(is_pr, err) via a single REST `GET /issues/<N>` (GitHub's issues
    endpoint returns a PR too, carrying a `pull_request` key). REST survives the
    hourly GraphQL exhaustion. `is_pr` is None (UNKNOWN) when the read fails."""
    return ghread.is_pull_request(number, slug, cwd=cwd)


def evaluate(payload, fetch=None, resolve_slug=None, fable_id=None, is_pr=None):
    """('allow', reason) or ('block', reason). Pure of process exit so tests can
    assert the verdict directly; `main()` maps it to allow()/emit_block_stderr()."""
    tool = field_of(payload, "tool_name", "")
    if tool not in ("Agent", "Task"):
        return "allow", "not an Agent/Task dispatch"
    try:
        obj = json.loads(payload)
    except Exception:
        return "allow", "unparseable payload"
    tin = obj.get("tool_input") if isinstance(obj, dict) else None
    if not isinstance(tin, dict):
        return "allow", "no tool_input"
    if (tin.get("subagent_type") or "") != "autopilot-worker":
        return "allow", "not an autopilot-worker dispatch"
    prompt = tin.get("prompt") or ""
    cwd = obj.get("cwd") or ""

    mb = _BYPASS_RE.search(prompt)
    if mb:
        _log("%s\tBYPASS\t%s\t%s" % (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            cwd, mb.group("reason").strip()))
        return "allow", "design-by-ok bypass (logged)"

    issues = issue_numbers(prompt)
    if not issues:
        # FAIL-CLOSED (#1061 review-3, owner's rule): an autopilot-worker ALWAYS
        # works a ticket, so a dispatch that names none is refused — name it as
        # `issue N` (or `#N`). The bypass stays `airuleset:design-by-ok`.
        return "block", ("this autopilot-worker dispatch names no ticket — name "
                         "the ticket as `issue N` (or `#N`) in the prompt so its "
                         "Design-by: main comment can be verified")

    slug = None
    resolver = resolve_slug or _resolve_slug
    slug = resolver(cwd)
    if not slug:
        return "block", ("could not resolve the repo (gh repo view failed) -- "
                         "refusing an autopilot-worker dispatch that cannot be "
                         "design-by verified (fail-closed)")

    # #1070 item 2 -- a dispatch prompt legitimately names an open PR it rides
    # ("this batch rides PR #201, do not gh pr create"); a PR carries no design
    # comment, so demanding `Design-by:` for it is a false block (#1079). Resolve
    # each `#N` as PR vs issue via one REST `GET /issues/<N>` (a PR carries
    # `pull_request`) and SKIP the PRs; an UNKNOWN read (gate-unavailable) is
    # kept as an issue so its own comment read produces the honest verdict.
    is_pr_fn = is_pr or _is_pull_request
    checkable = []
    for n in issues:
        pr, _perr = is_pr_fn(n, slug, cwd)
        if pr is True:
            _log("%s\tSKIP-PR\t%s\t#%d" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), cwd, n))
            continue
        checkable.append(n)
    if not checkable:
        # every named ref resolved to a PR -> there is no issue to design-check
        # (a PR cannot carry a design comment); nothing to refuse.
        return "allow", "every named ref is a PR -- no issue to design-check"

    # #1060 L3a: the Fable id is the only accepted design model on every box (the
    # #1062 L2 pilot-alias acceptance is removed -- the main always designs).
    for n in checkable:
        ok, reason = check_issue(n, slug, cwd, fetch=fetch, fable_id=fable_id)
        if not ok:
            return "block", reason
    return "allow", "every issue has a Design-by: main <Fable id> comment"


def _block_message(reason):
    return (
        "BLOCKED: main-authored design precondition (#1061) -- %s\n"
        "\n"
        "  The owner's standing rule (#871): the DESIGN of every ticket is "
        "authored by\n"
        "  the Fable MAIN session, the worker only IMPLEMENTS it. Before "
        "dispatching an\n"
        "  autopilot-worker, post the design yourself:\n"
        "\n"
        "    python3 ~/devel/airuleset/airuleset.py design-record \\\n"
        "      --repo <owner/name> --issue <N> --body-file <design.md>\n"
        "\n"
        "  (root cause traced in code + chosen approach + rejected alternative "
        "+ Triage:\n"
        "  + Architektura: + Shared-benefit:). Then re-dispatch. Bypass with\n"
        "  `airuleset:design-by-ok <reason>` in the prompt (logged)." % reason)


def _run_issue_cli(argv, fetch=None, fable_id=None, resolve_slug=None):
    """#1060 L3b — the RECEIVING-SIDE design gate the IMPLEMENTER runs before any
    work. `python3 gates/designdispatch.py --issue N [--slug owner/repo] [--cwd D]`
    exits 2 unless `#N`'s newest design comment is role-main + the Fable id (the
    SAME `check_issue` the dispatch hook uses), else 0 — fail-closed on an
    unreadable thread / unresolvable repo, exactly like the hook path.

    The dispatch hook (`main` below, no argv) trusts the design comment on the
    SENDER side; on the two independent-session `dual` box the implementer is
    reached by `SendMessage`, not an `Agent` dispatch, so the same precondition
    must be re-checked HERE, on the receiver, before it touches the ticket.

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
    argv = sys.argv[1:]
    if "--issue" in argv or any(a.startswith("--issue=") for a in argv):
        # #1060 L3b — receiving-side CLI mode (the implementer runs this before
        # any work); distinct from the stdin PreToolUse hook path below.
        sys.exit(_run_issue_cli(argv))
    payload = read_payload()
    if not payload:
        allow()
    verdict, reason = evaluate(payload)
    if verdict == "block":
        _log("%s\tBLOCK\t%s" % (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), reason))
        emit_block_stderr(_block_message(reason))
    allow()


if __name__ == "__main__":
    main()
