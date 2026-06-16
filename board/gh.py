"""GitHub refresher — objective signals for the board (design §8).

This module is the ONLY place the board shells out to `gh`. Two correctness
pillars:

  1. Field validation (valid_repo / valid_issue / valid_run_id) — these guard
     BOTH the board's POST ingestion (a worker can't inject a crafted repo into
     a run row) AND the gh subprocess (a validated value can never be read as a
     flag, a shell metachar, or a path-traversal). Anything that fails
     validation never reaches `gh`.

  2. argv-only invocation — `_gh` runs `subprocess.run(["gh", ...])` with NO
     shell, NO os.system, NO string-built command. Subcommands are restricted to
     a read-only allowlist (pr view/list, run list, issue view/list); the
     refresher NEVER calls a mutating gh subcommand.

`classify_pr` maps a `gh pr view --json` dict to the board's gate vocabulary via
`board.gate.mergeable_ok` (ok only when mergeable is true AND state CLEAN;
pending while GitHub is still computing; fail otherwise). `fetch_repo_active`
does the batched per-repo fetch the refresher loop relies on; on any gh failure
it returns a `{"gh_ok": False}` sentinel so the caller can raise the STALE
banner instead of freezing silently.

stdlib only.
"""
import re
import json
import logging
import subprocess

from board.gate import mergeable_ok

_log = logging.getLogger("autopilot_board.gh")

# repo: owner/name, each segment a safe token (letters, digits, dot, underscore,
# hyphen). NO slash inside a segment, NO leading hyphen exploitation, NO
# whitespace / shell metachars / path traversal can pass.
# NOTE: anchor with \A...\Z, NOT ^...$ — in Python `$` matches before a trailing
# newline, so `^...$` would accept "o/x\n" (a real argv-injection vector). \Z
# anchors the absolute end of string, rejecting any trailing newline.
# NEITHER segment's first char may be '-' so neither the slug nor the bare name
# (gh accepts `owner/name` and reads `--json`-style leading-hyphen tokens as
# flags) can ever be read as a gh flag. Both segments use the same char class.
_REPO = re.compile(
    r"\A[A-Za-z0-9._][A-Za-z0-9._-]*/[A-Za-z0-9._][A-Za-z0-9._-]*\Z")
# run_id: a single filesystem-safe opaque token (matches reporter's id charset).
# First char must NOT be a hyphen, so a value can never be read as a gh flag
# (--flag). Real run_ids start with the repo prefix (alnum/./_), never '-'.
_RID = re.compile(r"\A[A-Za-z0-9._][A-Za-z0-9._-]*\Z")
# issue: a bare positive integer in decimal — nothing else.
_ISSUE = re.compile(r"\A[1-9][0-9]*\Z")


def valid_repo(s):
    """True iff `s` is a safe `owner/name` slug. Rejects flags (--x, -x),
    shell metachars, whitespace, empty segments, and path traversal."""
    return bool(isinstance(s, str) and _REPO.match(s))


def valid_run_id(s):
    """True iff `s` is a single safe opaque token (the reporter's run_id shape)."""
    return bool(isinstance(s, str) and _RID.match(s))


def valid_issue(n):
    """True iff `n` is a positive integer (int or its exact decimal string).

    Rejects: 0, negatives, floats (1.5 / 1e3), hex (0x5), whitespace-padded,
    flags, and any non-numeric string. A bool is NOT a valid issue number
    (isinstance(True, int) is True in Python, so we exclude it explicitly).
    """
    if isinstance(n, bool):
        return False
    if isinstance(n, int):
        return n > 0
    if isinstance(n, str):
        return bool(_ISSUE.match(n))
    return False


# Read-only allowlist. The refresher NEVER calls a mutating gh subcommand
# (merge/close/edit/comment/...). _gh enforces this on the first two argv tokens.
ALLOWED = (
    ("pr", "view"),
    ("pr", "list"),
    ("run", "list"),
    ("issue", "view"),
    ("issue", "list"),
)


class GhError(Exception):
    """gh returned non-zero, timed out, or could not be invoked."""

    def __init__(self, msg, returncode=None, stderr="", rate_limited=False):
        super().__init__(msg)
        self.returncode = returncode
        self.stderr = stderr
        self.rate_limited = rate_limited


def _gh(args, timeout=20):
    """Invoke `gh` via argv ONLY — never a shell. Returns (returncode, stdout,
    stderr). The caller MUST pass pre-validated values; this function additionally
    enforces the read-only subcommand allowlist on args[:2].

    Raises GhError if gh is missing or times out (distinct from a non-zero exit,
    which is returned so callers can inspect stderr for rate-limit hints)."""
    if not args or tuple(args[:2]) not in ALLOWED:
        raise GhError(f"gh subcommand not allowlisted: {args[:2]!r}")
    try:
        r = subprocess.run(
            ["gh"] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as e:
        raise GhError("gh executable not found") from e
    except subprocess.TimeoutExpired as e:
        raise GhError(f"gh timed out after {timeout}s") from e
    return r.returncode, r.stdout, r.stderr


def _is_rate_limited(returncode, stderr):
    """Heuristic: gh signals 403 / rate-limit in stderr text."""
    s = (stderr or "").lower()
    return ("rate limit" in s or "403" in s or "secondary rate" in s
            or "api rate limit exceeded" in s)


_CLOSES_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)


def closing_issue_numbers(pr):
    """Issue numbers this PR closes, parsed from its BODY's GitHub closing
    keywords ("Closes #N" / "Fixes #N" / "Resolves #N"). This is the PR->issue
    link (a PR's own number is NOT its issue's number, which left gh_state
    empty). `closingIssuesReferences` would be cleaner but is NOT a valid
    `gh pr list --json` field — only `gh pr view` exposes it — so we parse the
    body, which `gh pr list` does return and which the autopilot worker always
    writes (`Closes #<N>`). De-duplicated, order-preserving."""
    body = pr.get("body") or ""
    seen = []
    for m in _CLOSES_RE.finditer(body):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def classify_pr(pr):
    """Map a `gh pr list --json state,mergedAt,mergeable,mergeStateStatus,body`
    dict to the board's classification.

    Returns dict(merged, pr_state, mergeable(None|bool), mergeable_state,
    mergeable_gate, closes). `mergeable_gate` uses board.gate.mergeable_ok:
      * ok      — mergeable is True AND mergeStateStatus == 'CLEAN'
      * pending — mergeable is None or 'UNKNOWN' (GitHub still computing)
      * fail    — anything else (UNSTABLE/BLOCKED/BEHIND/DIRTY/CONFLICTING)
    `closes` is the list of issue numbers the PR closes (PR->run linkage).
    """
    state = pr.get("state")
    merged = state == "MERGED" or bool(pr.get("mergedAt"))
    raw_mergeable = pr.get("mergeable")
    mergeable_state = pr.get("mergeStateStatus")
    if raw_mergeable in (None, "UNKNOWN"):
        mb = None
    else:
        mb = (raw_mergeable == "MERGEABLE")
    gate = mergeable_ok(mb, mergeable_state)
    return {
        "merged": merged,
        "pr_state": state,
        "mergeable": mb,
        "mergeable_state": mergeable_state,
        "mergeable_gate": gate,
        "closes": closing_issue_numbers(pr),
    }


# ---- batched per-repo fetch (the refresher's unit of work) -----------------

_PR_JSON_FIELDS = ("number,url,state,mergedAt,mergeable,mergeStateStatus,body")
_ISSUE_JSON_FIELDS = "number,state"
# List caps. A repo with MORE than this many open PRs/issues would silently
# truncate — and a truncated open-issue set is dangerous (a closed-issue
# reconcile would falsely finalise an open issue beyond the cap), so
# fetch_repo_active also returns `issues_capped` so the caller can SKIP the
# open-set reconcile when the cap was hit (merged-PR reconcile is unaffected).
_PR_LIMIT = 200
_ISSUE_LIMIT = 300


def fetch_repo_active(repo, timeout=20):
    """ONE batched read of a repo's open PRs + open issues (design §8: one
    `gh pr list` / `gh issue list` per repo, not per-issue).

    Returns a dict:
      {"gh_ok": True,
       "prs":   [<classify_pr dict + 'number'/'url'>, ...],
       "open_issues": {<int issue number>, ...}}
    On ANY gh failure (bad repo, non-zero exit, timeout, gh missing, JSON parse
    error) returns {"gh_ok": False, "rate_limited": <bool>} so the caller raises
    the STALE banner / backs off — NEVER a silent freeze, never a raise.
    """
    if not valid_repo(repo):
        _log.warning("fetch_repo_active: invalid repo %r — refusing gh call", repo)
        return {"gh_ok": False, "rate_limited": False}
    try:
        rc1, out1, err1 = _gh(
            ["pr", "list", "--repo", repo, "--state", "all",
             "--limit", str(_PR_LIMIT), "--json", _PR_JSON_FIELDS],
            timeout=timeout)
        if rc1 != 0:
            return {"gh_ok": False,
                    "rate_limited": _is_rate_limited(rc1, err1)}
        rc2, out2, err2 = _gh(
            ["issue", "list", "--repo", repo, "--state", "open",
             "--limit", str(_ISSUE_LIMIT), "--json", _ISSUE_JSON_FIELDS],
            timeout=timeout)
        if rc2 != 0:
            return {"gh_ok": False,
                    "rate_limited": _is_rate_limited(rc2, err2)}
        prs_raw = json.loads(out1 or "[]")
        issues_raw = json.loads(out2 or "[]")
    except GhError as e:
        _log.warning("fetch_repo_active(%s): %s", repo, e)
        return {"gh_ok": False, "rate_limited": getattr(e, "rate_limited", False)}
    except (json.JSONDecodeError, ValueError) as e:
        _log.warning("fetch_repo_active(%s): bad gh JSON: %s", repo, e)
        return {"gh_ok": False, "rate_limited": False}

    prs = []
    for p in prs_raw:
        c = classify_pr(p)
        c["number"] = p.get("number")
        c["url"] = p.get("url")
        prs.append(c)
    open_issues = {i.get("number") for i in issues_raw
                   if i.get("state") == "OPEN" and i.get("number") is not None}
    # If the open-issue list hit the cap, "not in open_issues" is unreliable
    # (open issues beyond the cap are missing) — the caller must NOT run the
    # closed-issue reconcile for this repo, or it would falsely finalise them.
    issues_capped = len(issues_raw) >= _ISSUE_LIMIT
    return {"gh_ok": True, "prs": prs, "open_issues": open_issues,
            "issues_capped": issues_capped}
