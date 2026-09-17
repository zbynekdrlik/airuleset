"""gates.labeledit -- the blind-label-flip gate (#1056 L1 (d) / #1057 item 3).

On a REDUCED-authority sub-dev stream, a `gh issue edit <N> --remove-label
prio:bounce` or `--add-label ready-for-review` (and the `-l`/`--label` add
forms, heredoc/cd-aware exactly like gates.selfservice) is BLOCKED when
`gk-watch` reports `bounce-unanswered` for that ticket AND no commit landed
since the verdict -- the exact blind flip that cleared prio:bounce / re-added
ready-for-review on odoo-erp #5613/#6890 (16.-17.9.2026) while a BOUNCE verdict
was newer than the stream's last READY-FOR-REVIEW. The block prints the verdict
comment id + the missing finding ids.

FAIL-OPEN: engages ONLY on a POSITIVE `bounce-unanswered` from gk-watch;
`unknown` (any gh error / low budget / unresolvable slug) -> ALLOW (never a
wrong block, the #539 never-false-accuse direction). A full / unresolvable
authority box is never gated (degrade-to-allow, #390). Log
~/.claude/labeledit-gate.log; bypass `# airuleset:labeledit-ok <reason>`.

The thin adapter hooks/block-blind-label-flip.sh runs `python3 -m gates.labeledit
1>&2`; a python malfunction there exits non-0/non-2 and fails CLOSED. STDLIB
only; the shell/token parsing is REUSED from gates.selfservice (the #1020
"one implementation of each migrated concern" intent) rather than re-copied.
"""
import os
import re
import sys
from datetime import datetime

from gates import command_of, field_of, read_payload
from gates.selfservice import (
    _all_flag_values, _apply_cd, _is_gh_issue_cmd, _strip_prefix,
    _ticket_of, _tokens_of,
)
from gates.shellcmd import split_top_level

_BOUNCE_LABEL = "prio:bounce"
_RFR_LABEL = "ready-for-review"

# #1056 review R1: `gh issue edit <github-url>` is a form gh accepts; extract the
# issue number from a `…/issues/N` URL so a URL-form blind flip is still gated.
_ISSUE_URL_RE = re.compile(r"/issues/(\d+)")


def _resolve_ticket(tk):
    """The issue number for a `gh issue edit …` command — the numeric positional
    (`_ticket_of`), else a `…/issues/N` URL positional. '-' when none."""
    t = _ticket_of(tk, "")
    if t and t.isdigit():
        return t
    for tok in tk:
        m = _ISSUE_URL_RE.search(tok)
        if m:
            return m.group(1)
    return t


# --------------------------------------------------------------------------- #
# Prevencia log (~/.claude/labeledit-gate.log).
# --------------------------------------------------------------------------- #
def _log_path():
    return os.path.join(os.path.expanduser("~"), ".claude", "labeledit-gate.log")


def _current_stream():
    try:
        import airuleset
        return airuleset._current_user()
    except Exception:
        return "unknown"


def _write_log(verdict, reason, ticket):
    """Append one `<ISO> <stream> <verdict> <reason> <ticket>` line, best-effort;
    an unwritable ~/.claude only loses the audit entry, never fails the gate."""
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s %s %s %s %s\n" % (stamp, _current_stream(), verdict,
                                           reason, ticket or "-"))
    except OSError:
        return                     # audit write is non-fatal to the gate decision


# --------------------------------------------------------------------------- #
# gk-watch resolution (injectable seam).
# --------------------------------------------------------------------------- #
def _default_watch(issue, cwd):
    """The live gk-watch state for `issue`, or None on any error (→ the gate
    fails OPEN). Test seam: `AIRULESET_GK_WATCH_FIXTURE` names a JSON file
    mapping str(issue) → a state dict, so the hook-level subprocess test needs
    no network."""
    fixture = os.environ.get("AIRULESET_GK_WATCH_FIXTURE")
    if fixture:
        try:
            import json
            with open(fixture) as fh:
                return json.load(fh).get(str(issue))
        except Exception:
            return None
    try:
        import airuleset
        return airuleset.gk_watch_issue(int(issue), cwd=cwd)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Classification.
# --------------------------------------------------------------------------- #
def classify_command(cmd, cwd=None, watch_fn=None):
    """Classify a whole command into a list of result dicts
    `{verdict, reason, ticket, gk_latest}` (verdict ∈ BLOCK/PASS). An empty
    list = not a guarded label flip at all. `watch_fn(issue, cwd)` is
    injectable (default: the live gk-watch); `cwd` is the base cwd for
    cd-relative resolution."""
    base_cwd = cwd or os.getcwd()
    wf = watch_fn or _default_watch
    effective_cwd = base_cwd
    results = []
    for seg in split_top_level(cmd):
        if not seg.strip():
            continue
        tk = _strip_prefix(_tokens_of(seg))
        if not tk:
            continue
        if tk[0] == "cd":
            effective_cwd = _apply_cd(effective_cwd, tk)
            continue
        if not (_is_gh_issue_cmd(tk) and len(tk) >= 3 and tk[2] == "edit"):
            continue
        add = set(_all_flag_values(tk, ("--add-label", "-l", "--label")))
        rem = set(_all_flag_values(tk, ("--remove-label",)))
        risky = (_BOUNCE_LABEL in rem) or (_RFR_LABEL in add)
        if not risky:
            continue
        ticket = _resolve_ticket(tk)
        if not (ticket and ticket.isdigit()):
            results.append({"verdict": "PASS", "reason": "no-ticket",
                            "ticket": ticket, "gk_latest": None})
            continue
        try:
            st = wf(ticket, effective_cwd)
        except Exception:
            st = None
        state = st.get("state") if isinstance(st, dict) else None
        if state is None or state == "unknown":
            results.append({"verdict": "PASS", "reason": "unresolvable",
                            "ticket": ticket, "gk_latest": None})
            continue
        if state != "bounce-unanswered":
            results.append({"verdict": "PASS", "reason": state,
                            "ticket": ticket, "gk_latest": None})
            continue
        # bounce-unanswered: ALLOW only if a commit landed since the verdict.
        gl = st.get("gk_latest") or {}
        gk_ts = gl.get("created_at")
        head_ts = st.get("head_ts")
        if (isinstance(head_ts, (int, float)) and isinstance(gk_ts, (int, float))
                and head_ts > gk_ts):
            results.append({"verdict": "PASS", "reason": "commit-since-verdict",
                            "ticket": ticket, "gk_latest": gl})
            continue
        results.append({"verdict": "BLOCK", "reason": "bounce-unanswered",
                        "ticket": ticket, "gk_latest": gl})
    return results


# --------------------------------------------------------------------------- #
# Block message + authority gate + main.
# --------------------------------------------------------------------------- #
def _block_message(blocks):
    lines = ["🚫 BLOCKED — blind label flip on a returned (prio:bounce) ticket:"]
    for b in blocks:
        gl = b.get("gk_latest") or {}
        ids = ",".join(gl.get("ids") or []) or "-"
        lines.append(
            "  #%s: a gk BOUNCE verdict (comment %s, missing ids: %s) is NEWER "
            "than your last READY-FOR-REVIEW and no commit has landed since."
            % (b["ticket"], gl.get("id"), ids))
    lines.append("")
    lines.append(
        "Do NOT clear prio:bounce or add ready-for-review by hand — the composer/"
        "bot owns those labels. Read the gk verdict, fix + disposition every id "
        "in a new RFR (airuleset.py handoff), and let the composer clear the "
        "bounce. Bypass (rare, logged): add `# airuleset:labeledit-ok <reason>`.")
    return "\n".join(lines)


def _reduced_authority(cwd):
    """True for a REDUCED sub-dev stream (any resolved profile != "full"), False
    for a full box, None only on an exception. NB (#1056 review R1):
    `resolve_authority` never returns None — an UNMAPPED box resolves to the
    fail-safe `fork-no-merge` (reduced), so the gate DOES engage on it (the safe
    over-gate direction; gk-watch then fail-opens to unknown → allow). A full box
    (maintainer / gatekeeper / ci-runner) resolves to "full" and is never
    gated."""
    try:
        import airuleset
        profile = airuleset.resolve_authority(cwd)
        return profile is not None and profile != "full"
    except Exception:
        return None


def main():
    payload = read_payload()
    cmd = command_of(payload)
    if not cmd:
        sys.exit(0)
    # Cheap pre-filter: only a `gh issue edit` touching one of the two labels.
    if "issue" not in cmd or "edit" not in cmd:
        sys.exit(0)
    if not (_BOUNCE_LABEL in cmd or _RFR_LABEL in cmd):
        sys.exit(0)
    if "airuleset:labeledit-ok" in cmd:
        sys.exit(0)

    cwd = field_of(payload, "cwd", "") or os.getcwd()
    reduced = _reduced_authority(cwd)
    if not reduced:
        sys.exit(0)                # full / unresolvable authority → never gated

    results = classify_command(cmd, cwd)
    if not results:
        sys.exit(0)
    for r in results:
        _write_log(r["verdict"], r["reason"], r["ticket"])
    blocks = [r for r in results if r["verdict"] == "BLOCK"]
    if blocks:
        sys.stderr.write(_block_message(blocks) + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
