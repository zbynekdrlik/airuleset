"""gates.filing.worth -- the #1378 worth-it bar and per-stream net-flow brake
for a Claude-born filing (owner 19.11.2026, ROZHODNUTÉ answer "1").

A Claude-born filing is `Scope-gate: separate-work` with no `Requested by
<owner>/<repo>#N` line naming ANOTHER repo (a handover from another project).
`user-request`, the other six criteria and handovers never reach this module's
checks. For a Claude-born filing:

  * ``impact_reason`` -- it must carry an ``Impact: <who is hit and how>`` line
    naming a real defect (wrong money, lost/wrong data, a legal deadline, a
    broken or unusable screen/flow, security, blocked work). Empty, too short,
    a ``<placeholder>``, a no-impact lead (none / n/a / no impact / žiadny
    dopad), a 🔵 or a cleanup-class word (nit, cleanup, refactor, hygiene,
    consistency, …) is rejected.
  * ``brake_blocks`` -- over the last 72 h, Claude-born filings CREATED >=
    issues CLOSED as completed (any origin) blocks; 0/0 blocks too. Counts are
    INJECTED, so the decision is a pure function.
  * ``flow_counts`` -- the two live ``gh issue list --search`` reads behind the
    brake, scoped to the filing's ``stream:<x>`` label when it carries one (the
    #390 gate already demands it in a stream-aware repo), else the whole repo.
    Cached per (cwd, repo, scope) for ONE hook invocation (like
    ``caps._issue_list_cache``), never a TTL cache. Any gh error / timeout /
    non-list answer is UNMEASURABLE: ``block_reason`` fails OPEN and logs one
    ``note=brake-unverified`` line to scope-gate.log (no ``verdict=`` field, so
    no counting consumer reads it). The Impact rule never depends on gh.

STDLIB only. ``ratchet_counts.py`` was read and does not fit (local-day window,
all issues, TTL cache, fail-closed -- the footer ``I N▲`` keeps that contract).
Known residual: ``-L 500`` truncates a window with more than 500 issues, which
can only UNDER-count created filings (toward allowing), never false-block.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from gates import audit
from gates.filing.parse import ALLOWED, CRITERION_RE, _clean_field, _no_field_decoy

CLAUDE_BORN_CRITERION = "separate-work"
WINDOW_HOURS = 72
MIN_IMPACT_WORDS = 3
GH_TIMEOUT_S = 6
LIST_LIMIT = "500"

HANDOVER_RE = re.compile(
    r"\bRequested by\s+(?:https?://github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)"
    r"(?:#|/issues/)\d+", re.IGNORECASE)
IMPACT_RE = re.compile(r"(?mi)^[ \t]*Impact:[ \t]*(.*?)[ \t]*$")
_WORD_RE = re.compile(r"[^\W\d_]{2,}")
_PLACEHOLDER_RE = re.compile(r"<[^<>]*>")
# A value that LEADS with a no-impact statement: a phrase ("no user impact",
# "žiadny dopad") whatever follows it, or a bare word ("none", "n/a", "minor")
# only when punctuation or the end follows -- "none of the clients can log in"
# is a real defect and must pass.
_NO_IMPACT_PHRASE_RE = re.compile(
    r"^(?:no(?:\s+(?:real|user|business|client|customer|visible))?\s+impact|"
    r"ž[iy]adn[yý]\s+dopad\w*|bez\s+dopadu)\b", re.IGNORECASE)
_NO_IMPACT_BARE_RE = re.compile(
    r"^(?:none|n/?a|nothing|tbd|unknown|low|minor|trivial|ž[iy]adn[yý]|nič|-+)"
    r"(?:\s*[—–:;,.()\[\]-]|\s*$)", re.IGNORECASE)
_CLEANUP_CLASS_RE = re.compile(
    r"\b(?:nits?|nitpicks?|clean-?ups?|refactor(?:ing|s)?|hygiene|consistency|cosmetics?|"
    r"stylistic|code[ -]style|readability|tidy(?:-?up)?|maintainability|tech(?:nical)?[ -]debt|"
    r"dead[ -]code|internal[ -]only|upratovani\w*|upratan\w*|refaktor\w*|konzistenci\w*|"
    r"kozmetick\w*|hygien\w*|čitateľnos\w*)\b", re.IGNORECASE)
_BLUE = "\U0001f535"

_IMPACT_HELP = (
    "a Claude-born separate-work ticket needs an `Impact: <who is hit and how>` line "
    "naming a real defect a user or the business hits (wrong money, lost/wrong data, "
    "a legal deadline, a broken or unusable screen/flow, security, blocked work) -- "
    "else fix it in the PR in hand or drop it with one line in the PR/ticket; "
    "a %s or style finding never becomes a ticket" % _BLUE)

# (cwd, repo, scope) -> ((created, closed), None) | (None, err), for ONE invocation.
_flow_cache = {}


def scope_criterion(body):
    """The `Scope-gate:` criterion of `body`, lowercased: the first line whose
    token is an accepted criterion wins (a prose self-audit line never shadows
    it, #1003), else the first token, else None."""
    first = None
    for m in CRITERION_RE.finditer(body or ""):
        tok = m.group(1).lower()
        if first is None:
            first = tok
        if tok in ALLOWED:
            return tok
    return first


def _same_repo(cited, target):
    cited = (cited or "").lower()
    target = (target or "").lower()
    if "/" in target:
        return cited == target
    return cited.split("/", 1)[-1] == target


def is_handover(body, target_repo):
    """True when `body` cites `Requested by <owner>/<repo>#N` (or the issue URL)
    of a repo OTHER than the filing's own target -- a task another project
    hands over. A self-citation of the target repo is no handover."""
    return any(not _same_repo(m.group(1), target_repo)
               for m in HANDOVER_RE.finditer(body or ""))


def is_claude_born(crit, body, target_repo):
    return ((crit or "").lower() == CLAUDE_BORN_CRITERION
            and not is_handover(body, target_repo))


def _impact_value_problem(value):
    """None when `value` names a plausible defect, else a short problem tag."""
    value = (value or "").strip().strip("*").strip()
    if not value:
        return "empty"
    if _PLACEHOLDER_RE.search(value):
        return "placeholder"
    if _BLUE in value:
        return "blue-finding"
    if _NO_IMPACT_PHRASE_RE.match(value) or _NO_IMPACT_BARE_RE.match(value):
        return "no-impact-stated"
    m = _CLEANUP_CLASS_RE.search(value)
    if m:
        return "cleanup-class:%s" % m.group(0).lower()
    if len(_WORD_RE.findall(value)) < MIN_IMPACT_WORDS:
        return "too-short"
    return None


def impact_reason(body):
    """None when ANY `Impact:` line of `body` names a real defect, else the
    one-line BLOCK reason (`no-impact …` / `impact-not-a-defect:<tag> …`)."""
    problems = [_impact_value_problem(m.group(1)) for m in IMPACT_RE.finditer(body or "")]
    if not problems:
        return "no-impact (%s)" % _IMPACT_HELP
    if any(p is None for p in problems):
        return None
    return "impact-not-a-defect:%s (%s)" % (problems[0], _IMPACT_HELP)


def brake_blocks(created, closed):
    """#1378: the stream files at least as fast as it closes -> BLOCK (0/0 too)."""
    return created >= closed


def brake_scope(stream_labels):
    """The `stream:<x>` label the brake counts (the first one the filing names),
    or None -> the whole target repo."""
    return stream_labels[0] if stream_labels else None


def _since_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(hours=WINDOW_HOURS)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def flow_argvs(repo, scope_label, now=None):
    """`(created_argv, closed_argv)` -- the two gh reads behind the brake."""
    since = _since_iso(now)
    created = ["gh", "issue", "list", "--state", "all", "--search",
               "created:>=%s" % since, "-L", LIST_LIMIT, "--json", "number,body"]
    closed = ["gh", "issue", "list", "--state", "closed", "--search",
              "closed:>=%s" % since, "-L", LIST_LIMIT, "--json", "number,stateReason"]
    for argv in (created, closed):
        if scope_label:
            argv += ["--label", scope_label]
        if repo:
            argv += ["-R", repo]
    return created, closed


def _gh_run(argv, cwd):
    """(stdout, None) or (None, err) -- never raises; the caller fails OPEN and
    logs the err."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=GH_TIMEOUT_S, cwd=cwd or None)
    except Exception as exc:
        return None, "%s: %s" % (exc.__class__.__name__, exc)
    if r.returncode != 0:
        first = next((ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()), "")
        return None, first or "gh exited %d" % r.returncode
    return r.stdout, None


def _rows(stdout):
    """The JSON row list, or None for any non-list / malformed answer."""
    try:
        data = json.loads(stdout or "")
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return [row for row in data if isinstance(row, dict)]


def flow_counts(repo, scope_label, cwd, now=None, runner=None):
    """`((created, closed), None)` for the last 72 h, or `(None, err)` when
    either read is unmeasurable. created = Claude-born filings among the issues
    created in the window (the SAME predicate the live filing is judged by);
    closed = issues closed as COMPLETED in the window, any origin."""
    key = (cwd, repo, scope_label)
    if key in _flow_cache:
        return _flow_cache[key]
    runner = runner or _gh_run
    created_argv, closed_argv = flow_argvs(repo, scope_label, now)
    counts = []
    result = None
    for argv in (created_argv, closed_argv):
        out, err = runner(argv, cwd)
        rows = None if err else _rows(out)
        if rows is None:
            result = (None, err or "unreadable gh answer")
            break
        if argv is created_argv:
            counts.append(sum(1 for row in rows if is_claude_born(
                scope_criterion(row.get("body") or ""), row.get("body") or "", repo)))
        else:
            counts.append(sum(1 for row in rows if row.get("stateReason") == "COMPLETED"))
    if result is None:
        result = (tuple(counts), None)
    _flow_cache[key] = result
    return result


def _log_note(log_path, repo, scope, err, title):
    """One best-effort `note=brake-unverified` line (no `verdict=` field). An
    unwritable log is reported on stderr, never raised: the filing proceeds."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write('%s  note=brake-unverified  repo=%s  scope=%s  reason="%s"  '
                     'title="%s"\n' % (audit.iso_now(), _clean_field(repo or "none"),
                                       _clean_field(scope or "repo"),
                                       _no_field_decoy(_clean_field(err or ""))[:160],
                                       _no_field_decoy(_clean_field(title))))
    except OSError as exc:
        sys.stderr.write("filing: brake-unverified (%s) and the note could not be "
                         "logged: %s\n" % (_clean_field(err or ""), exc))


def _brake_reason(scope_text, created, closed):
    return ("net-flow-brake (%s: %d Claude-born separate-work tickets filed vs %d closed "
            "as completed in the last %d h -- it files faster than it closes; put the item "
            "as a `- [ ]` line in the DESCRIPTION of the ticket it belongs to (never a "
            "comment), fix it now, or drop it)" % (scope_text, created, closed, WINDOW_HOURS))


def block_reason(crit_l, body, target_repo, stream_labels, cwd, log_path, title,
                 runner=None, now=None):
    """The #1378 BLOCK reason for one filing, or None. Only a Claude-born filing
    is judged: Impact first (cheap, never depends on gh), then the brake."""
    if not is_claude_born(crit_l, body, target_repo):
        return None
    reason = impact_reason(body)
    if reason:
        return reason
    scope = brake_scope(stream_labels)
    counts, err = flow_counts(target_repo, scope, cwd, now=now, runner=runner)
    if counts is None:
        _log_note(log_path, target_repo, scope, err, title)
        return None
    if brake_blocks(*counts):
        return _brake_reason(scope or "repo %s" % target_repo, *counts)
    return None
