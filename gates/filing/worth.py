"""gates.filing.worth -- the #1378 worth-it bar and per-stream net-flow brake
for a Claude-born filing (owner 19.11.2026, ROZHODNUTÉ answer "1").

A Claude-born filing is `Scope-gate: separate-work` with no `Requested by
<owner>/<repo>#N` LINE naming ANOTHER repo (a handover from another project;
a self-cite of the target repo is no handover, and every honoured handover is
logged as a `note=handover-exempt` line so it can be audited). `user-request`,
the other six criteria and handovers never reach this module's checks. For a
Claude-born filing:

  * ``impact_reason`` -- it must carry an ``Impact: <who is hit and how>`` line
    (``Dopad:`` too; bold / bullet / quote prefixes allowed) naming a real
    defect (wrong money, lost/wrong data, a legal deadline, a broken or unusable
    screen/flow, security, blocked work). Rejected: empty, a whole-value
    ``<placeholder>``, fewer than 3 words, a 🔵, a no-impact LEAD (none / n/a /
    no impact / žiadny dopad), or a cleanup-class word LEADING the value (nit,
    cleanup, refactor, hygiene, consistency, …) with no harm word anywhere --
    "konzistencia skladu je porušená" is a defect, "consistency with the other
    screens" is not.
  * ``brake_blocks`` -- over the last 72 h, Claude-born filings CREATED >
    issues CLOSED as completed (any origin) blocks; equal counts and 0/0 pass
    (the owner-approved "created MORE than it closed"). Counts are INJECTED, so
    the decision is a pure function.
  * ``flow_counts`` -- the two live ``gh issue list --search`` reads behind the
    brake, per scope: EVERY ``stream:<x>`` label the filing carries (also a
    ``gh api … -f labels[]=stream:x``), else the whole repo; any non-draining
    scope blocks. Cached per (cwd, repo, scope) for ONE hook invocation, never a
    TTL cache; a filing that passes the brake bumps its scopes' cached created
    count, so one command filing a whole review round cannot overshoot (#1378
    review R1 -- a block anywhere refuses the whole command, so the bump can
    never over-count a ticket that was really filed). All reads share ONE
    ``BRAKE_BUDGET_S`` deadline (the hook has 10 s). Any gh error / timeout /
    non-list answer is UNMEASURABLE: that scope fails OPEN with one
    ``note=brake-unverified`` line in scope-gate.log (no ``verdict=`` field, so
    no counting consumer reads it). The Impact rule never depends on gh.

STDLIB only. ``ratchet_counts.py`` was read and does not fit (local-day window,
all issues, TTL cache, fail-closed -- the footer ``I N▲`` keeps that contract).
Known residual: ``-L 500`` truncates a window above 500 issues on EITHER read
(created toward allowing, closed toward blocking); real 72 h windows are far
below it (david3: 61 created at ship time).
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from gates import audit
from gates.filing.parse import ALLOWED, _clean_field, _no_field_decoy

CLAUDE_BORN_CRITERION = "separate-work"
WINDOW_HOURS = 72
MIN_IMPACT_WORDS = 3
BRAKE_BUDGET_S = 5.0
LIST_LIMIT = "500"
BYPASS_LOG = "scope-gate-bypasses.log"

# `[ \t]*`, never `\s*`: this also scans up to 500 FOREIGN bodies per filing,
# and `^\s*` is quadratic on a run of blank lines (#1378 review B2).
_CRIT_LINE_RE = re.compile(r"(?m)^[ \t]*Scope-gate:[ \t]*(\S+)")
HANDOVER_RE = re.compile(
    r"(?mi)^[ \t>*-]*\**[ \t]*Requested by\**[ \t]+(?:https?://github\.com/)?"
    r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:#|/issues/)(\d+)")
# Greedy `(.*)$` + a Python strip: a lazy `(.*?)[ \t]*$` is quadratic on a
# long whitespace run (#1378 review F8).
IMPACT_RE = re.compile(r"(?mi)^[ \t>*-]*\**[ \t]*(?:Impact|Dopad)\**[ \t]*:(.*)$")
_WORD_RE = re.compile(r"[^\W\d_]{2,}")
_PLACEHOLDER_RE = re.compile(r"^<[^<>]*>$")
_FILLER = {"just", "only", "a", "an", "the", "some", "minor", "small", "pure", "purely",
           "mostly", "code", "test", "tests", "iba", "len", "drobné", "drobná", "drobný",
           "malé", "malá", "malý", "kód", "kódu"}
# A no-impact statement LEADING the value: a phrase whatever follows it, or a
# bare word only when punctuation / a spaced dash / the end follows it --
# "none of the clients can log in" and "low-stock alert never fires" are defects.
_NO_IMPACT_PHRASE_RE = re.compile(
    r"^(?:no(?:\s+(?:real|user|business|client|customer|visible))?\s+impact|"
    r"ž[iy]adn[yý]\s+dopad\w*|bez\s+dopadu)\b", re.IGNORECASE)
_NO_IMPACT_BARE_RE = re.compile(
    r"^(?:none|n/?a|nothing|tbd|unknown|low|minor|trivial|ž[iy]adn[yý]|nič|-+)"
    r"(?:\s*[—–:;,.()\[\]]|\s+-|\s*$)", re.IGNORECASE)
_CLEANUP_LEAD_RE = re.compile(
    r"^(?:nits?|nitpicks?|clean-?ups?|refactor(?:ing|s)?|hygiene|consistency|cosmetics?|"
    r"stylistic|style|readability|tidy(?:-?up)?|polish(?:ing)?|maintainability|"
    r"tech(?:nical)?[ -]debt|naming|typos?|upratovani\w*|upratan\w*|refaktor\w*|"
    r"konzistenci\w*|kozmetick\w*|hygien\w*|čitateľnos\w*|štýl\w*)\b", re.IGNORECASE)
# A harm word ANYWHERE keeps a cleanup-led value: "konzistencia skladu je porušená".
_HARM_RE = re.compile(
    r"\b(?:brok\w*|breaks?|fail\w*|cannot|can'?t|unable|lost|lose\w*|wrong\w*|incorrect\w*|"
    r"missing|deleted?|leak\w*|visible|exposed?|block\w*|crash\w*|errors?|fines?|penalt\w*|"
    r"over-?(?:sold|charged|pays?|billed)|never|not\s+(?:saved|sent|booked|shown|paid)|"
    r"nedá|nejde|nefunguj\w*|chyb\w*|zl[aeiouyáéíóúý]\w*|strat\w*|nesprávn\w*|poruš\w*|pokut\w*|"
    r"neulož\w*|nevid\w*|zablokovan\w*|pad[áa]\w*|nedostan\w*)\b", re.IGNORECASE)
_BLUE = "\U0001f535"

_IMPACT_HELP = (
    "a Claude-born separate-work ticket needs an `Impact: <who is hit and how>` line "
    "naming a real defect a user or the business hits (wrong money, lost/wrong data, "
    "a legal deadline, a broken or unusable screen/flow, security, blocked work) -- "
    "else fix it in the PR in hand or drop it with one line in the PR/ticket; "
    "a %s or style finding never becomes a ticket, and switching the Scope-gate "
    "criterion is not an exit" % _BLUE)

# (cwd, repo, scope) -> [created, closed] | None (unmeasurable), for ONE invocation.
_flow_cache = {}


def scope_criterion(body):
    """The `Scope-gate:` criterion of `body`, lowercased: the first line whose
    token is an accepted criterion wins (a prose self-audit line never shadows
    it, #1003), else the first token, else None."""
    first = None
    for m in _CRIT_LINE_RE.finditer(body or ""):
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


def handover_ref(body, target_repo):
    """`owner/repo#N` of the first `Requested by` LINE citing a repo OTHER than
    the filing's own target (a task another project hands over), else None."""
    for m in HANDOVER_RE.finditer(body or ""):
        if not _same_repo(m.group(1), target_repo):
            return "%s#%s" % (m.group(1), m.group(2))
    return None


def is_handover(body, target_repo):
    return handover_ref(body, target_repo) is not None


def is_claude_born(crit, body, target_repo):
    return ((crit or "").lower() == CLAUDE_BORN_CRITERION
            and not is_handover(body, target_repo))


def _impact_value_problem(value):
    """None when `value` names a plausible defect, else a short problem tag."""
    value = (value or "").strip().strip("*").strip()
    if not value:
        return "empty"
    if _PLACEHOLDER_RE.match(value):
        return "placeholder"
    if _BLUE in value:
        return "blue-finding"
    if _NO_IMPACT_PHRASE_RE.match(value) or _NO_IMPACT_BARE_RE.match(value):
        return "no-impact-stated"
    words = value.split()
    while words and words[0].lower().strip(".,:;") in _FILLER:
        words.pop(0)
    m = _CLEANUP_LEAD_RE.match(" ".join(words))
    if m and not _HARM_RE.search(value):
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
    """#1378: the stream files faster than it closes -> BLOCK (equal / 0/0 pass)."""
    return created > closed


_API_LABEL_RE = re.compile(r"^labels\[\]=(stream:[A-Za-z0-9_-]+)$", re.IGNORECASE)


def stream_scopes(stream_labels, tk=()):
    """Every `stream:<x>` scope of a filing: the `-l` labels the parser read,
    plus `gh api … -f labels[]=stream:x` fields (the parser reads no api
    labels), deduplicated in order; [None] (the whole repo) when there is none."""
    scopes = [lb.lower() for lb in stream_labels]
    for i, t in enumerate(tk):
        val = t
        if t in ("-f", "-F", "--field", "--raw-field") and i + 1 < len(tk):
            val = tk[i + 1]
        elif t.startswith(("--field=", "--raw-field=")):
            val = t.split("=", 1)[1]
        elif len(t) > 2 and t[:2] in ("-f", "-F"):
            val = t[2:]  # gh accepts the glued `-flabels[]=…` form too
        m = _API_LABEL_RE.match(val)
        if m and m.group(1).lower() not in scopes:
            scopes.append(m.group(1).lower())
    return scopes or [None]


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


def _gh_run(argv, cwd, timeout):
    """(stdout, None) or (None, err) -- never raises; the caller fails OPEN and
    logs the err."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd or None)
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


def flow_counts(repo, scope_label, cwd, now=None, runner=None, deadline=None):
    """`((created, closed), None)` for the last 72 h, or `(None, err)` when
    either read is unmeasurable. created = Claude-born filings among the issues
    created in the window (the SAME predicate the live filing is judged by);
    closed = issues closed as COMPLETED in the window, any origin. `runner(argv,
    cwd, timeout)` is injectable; `deadline` (time.monotonic) bounds both reads."""
    key = (cwd, repo, scope_label)
    if key in _flow_cache:
        hit = _flow_cache[key]
        return (tuple(hit), None) if hit is not None else (None, "unmeasurable (cached)")
    runner = runner or _gh_run
    deadline = deadline or time.monotonic() + BRAKE_BUDGET_S
    counts = []
    err = None
    for kind, argv in zip(("created", "closed"), flow_argvs(repo, scope_label, now)):
        left = deadline - time.monotonic()
        if left < 0.3:
            err = "brake time budget spent before the %s read" % kind
            break
        out, err = runner(argv, cwd, left)
        rows = None if err else _rows(out)
        if rows is None:
            err = err or "unreadable gh answer on the %s read" % kind
            break
        if kind == "created":
            counts.append(sum(1 for row in rows if is_claude_born(
                scope_criterion(row.get("body") or ""), row.get("body") or "", repo)))
        else:
            counts.append(sum(1 for row in rows if row.get("stateReason") == "COMPLETED"))
    if len(counts) < 2:
        _flow_cache[key] = None
        return None, err
    _flow_cache[key] = counts
    return tuple(counts), None


def _log_note(log_path, note, repo, scope, detail, title):
    """One best-effort `note=<note>` line (no `verdict=` field). An unwritable log
    is reported on stderr, never raised: the filing proceeds."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write('%s  note=%s  repo=%s  scope=%s  reason="%s"  title="%s"\n' % (
                audit.iso_now(), note, _no_field_decoy(_clean_field(repo or "none")),
                _no_field_decoy(_clean_field(scope or "repo")),
                _no_field_decoy(_clean_field(detail or ""))[:160],
                _no_field_decoy(_clean_field(title))))
    except OSError as exc:
        sys.stderr.write("filing: %s (%s) and the note could not be logged: %s\n"
                         % (note, _clean_field(detail or ""), exc))


def log_bypass(cmd, sid):
    """#1378 review Y4: an honoured `# airuleset:scope-gate-ok` is audited (it is
    the cheapest exit on the wall once the brake fires). Returns 0 (allow)."""
    m = re.search(r"airuleset:scope-gate-ok([^\n]*)", cmd)
    reason = _clean_field(m.group(1) if m else "")[:200]
    audit.append_line(BYPASS_LOG, "%s  session=%s  project=%s  airuleset:scope-gate-ok %s" % (
        audit.iso_now(), _clean_field(sid), audit.project_of(), reason or "(no reason)"))
    return 0


def _brake_reason(scope_text, created, closed):
    return ("net-flow-brake (%s: %d Claude-born separate-work tickets filed vs %d closed "
            "as completed in the last %d h -- it files faster than it closes; put "
            "the item as a `- [ ]` line in the DESCRIPTION of the ticket it belongs to "
            "(never a comment), fix it now, or drop it with one line; switching the "
            "Scope-gate criterion is not an exit)"
            % (scope_text, created, closed, WINDOW_HOURS))


def block_reason(crit_l, body, target_repo, stream_labels, cwd, log_path, title,
                 runner=None, now=None, tk=()):
    """The #1378 BLOCK reason for one filing, or None. Only a Claude-born filing
    is judged: Impact first (cheap, never depends on gh), then the brake over
    every scope; a pass bumps each measured scope's cached created count."""
    if (crit_l or "").lower() != CLAUDE_BORN_CRITERION:
        return None
    ref = handover_ref(body, target_repo)
    if ref:
        _log_note(log_path, "handover-exempt", target_repo, None, "Requested by %s" % ref, title)
        return None
    reason = impact_reason(body)
    if reason:
        return reason
    deadline = time.monotonic() + BRAKE_BUDGET_S
    measured = []
    for scope in stream_scopes(stream_labels, tk):
        counts, err = flow_counts(target_repo, scope, cwd, now=now, runner=runner,
                                  deadline=deadline)
        if counts is None:
            _log_note(log_path, "brake-unverified", target_repo, scope, err, title)
            continue
        if brake_blocks(*counts):
            return _brake_reason(scope or "repo %s" % target_repo, *counts)
        measured.append(scope)
    for scope in measured:
        _flow_cache[(cwd, target_repo, scope)][0] += 1
    return None
