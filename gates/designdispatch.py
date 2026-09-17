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

DESIGN_BY_LOG = "design-by-gate.log"

# The issue-line shape the autopilot dispatch prompt uses -- the SAME extractor
# block-dispatch-over-wdrain.sh uses (the whole "issue(s) #…" line, so a
# comma/"and"-separated batch is fully covered).
_ISSUE_LINE_RE = re.compile(r"(?:work\s+)?issues?\s+#[0-9].*", re.IGNORECASE)
_ISSUE_NUM_RE = re.compile(r"#([0-9]+)")

_BYPASS_RE = re.compile(r"airuleset:design-by-ok\s*(?P<reason>.*)", re.IGNORECASE)

# A `Design-by: <role> <model>` line, leading-bullet + bold tolerant (the
# MINOR-3 lesson shared with the design classifiers): a `**Design-by:**` /
# `- **Design-by:**` form has `**` before the label AND after the colon, so
# `\**` is allowed at both spots.
_DESIGN_BY_RE = re.compile(
    r"(?im)^[ \t>*#-]*\**[ \t]*Design-?by\**[ \t]*:[ \t]*\**[ \t]*"
    r"(?P<role>main|worker)\b[ \t]*(?P<model>\S+)?")


def _log(line):
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude", DESIGN_BY_LOG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        return


def issue_numbers(prompt):
    """Every issue number on the prompt's `issue(s) #…` line, in order.
    Empty when the prompt names no issue."""
    m = _ISSUE_LINE_RE.search(prompt or "")
    if not m:
        return []
    return [int(n) for n in _ISSUE_NUM_RE.findall(m.group(0))]


def _norm_model(m):
    """A model id normalised for the exact-id match: `[..]` context tag stripped,
    lowercased. `claude-fable-5-1[1m]` and `claude-fable-5-1` both -> the tier
    id (the SAME tolerance airuleset.is_allowed_model applies)."""
    m = (m or "").strip().lower()
    return re.sub(r"\[[^\]]*\]$", "", m)


def newest_design_by(comment_bodies):
    """(role, model) of the `Design-by:` line in the NEWEST comment that carries
    one, or None. `comment_bodies` is the thread in CREATION order (oldest
    first); the last body with a `Design-by:` line wins."""
    result = None
    for body in comment_bodies:
        m = _DESIGN_BY_RE.search(body or "")
        if m:
            result = (m.group("role").lower(), (m.group("model") or "").strip())
    return result


def _fable_id():
    try:
        import airuleset
        return airuleset.MODEL_TIERS["fable"]
    except Exception:
        return "claude-fable-5-1"


def _gh_env():
    try:
        import airuleset
        return airuleset._gh_env()
    except Exception:
        return None


def _resolve_slug(cwd):
    try:
        r = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner",
             "-q", ".nameWithOwner"],
            cwd=cwd or None, capture_output=True, text=True, timeout=15,
            env=_gh_env())
    except Exception:
        return None
    if r.returncode != 0:
        return None
    slug = (r.stdout or "").strip()
    return slug or None


def _fetch_comment_bodies(slug, number, cwd, timeout=20):
    """Comment bodies for `<slug>#<number>` in CREATION order via the paginated
    REST reader (`gh api …/comments --paginate -q '.[]'`, the SAME shape
    cli_work_class._fetch_comments uses so a recent design comment past the
    `gh issue view` window is never missed), or None on ANY gh failure."""
    try:
        r = subprocess.run(
            ["gh", "api", "repos/%s/issues/%d/comments" % (slug, number),
             "--paginate", "-q", ".[]"],
            cwd=cwd or None, capture_output=True, text=True, timeout=timeout,
            env=_gh_env())
    except Exception:
        return None
    if r.returncode != 0:
        return None
    bodies = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("body"), str):
            bodies.append(obj["body"])
    return bodies


def check_issue(number, slug, cwd, fetch=None, fable_id=None):
    """(ok, reason) -- is `#number`'s newest design comment authored by the Fable
    main? FAIL-CLOSED: an unreadable thread returns (False, ...). `fetch(slug,
    number, cwd)` -> [bodies]|None is injected in tests."""
    fetch = fetch or _fetch_comment_bodies
    fable_id = fable_id or _fable_id()
    bodies = fetch(slug, number, cwd)
    if bodies is None:
        return False, ("could not read #%d's comments (gh error / no network) "
                       "-- refusing (fail-closed)" % number)
    db = newest_design_by(bodies)
    if db is None:
        return False, ("#%d has no `Design-by:` comment -- the Fable main must "
                       "author the design (airuleset.py design-record) before "
                       "an autopilot-worker is dispatched" % number)
    role, model = db
    if role != "main":
        return False, ("#%d's newest design comment is `Design-by: %s %s` -- the "
                       "design must be authored by the MAIN session, not the "
                       "worker (#871/#1061)" % (number, role, model or "?"))
    if _norm_model(model) != _norm_model(fable_id):
        return False, ("#%d's newest design comment is `Design-by: main %s` -- "
                       "expected the Fable id (%s); the design must be authored "
                       "by the Fable main" % (number, model or "?", fable_id))
    return True, "ok"


def evaluate(payload, fetch=None, resolve_slug=None, fable_id=None):
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
        return "allow", "no parseable issue in prompt (cannot verify -- allow)"

    slug = None
    resolver = resolve_slug or _resolve_slug
    slug = resolver(cwd)
    if not slug:
        return "block", ("could not resolve the repo (gh repo view failed) -- "
                         "refusing an autopilot-worker dispatch that cannot be "
                         "design-by verified (fail-closed)")

    for n in issues:
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


def main():
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
