"""gates.filing.caps -- the gh-backed lookups, soft caps, near-duplicate check,
stream-routing decision, authority resolution and the net-drain ratchet for the
ungated-issue-filing gate (#1020 Part 2).

Extracted VERBATIM from the classifier that lived embedded in
hooks/block-ungated-issue-filing.sh; the pure parse helpers it needs
(EXEMPT_FROM_CAP, the stream regexes, _title_jaccard/_tokenize, the label
readers, _target_repo_for_segment) are imported from ``gates.filing.parse``. The
per-invocation caches (`_issue_list_cache`/`_label_list_cache`/`_authority_cache`)
are module-level exactly as in the single-script heredoc: the module loads once
per hook run, so they scope to one invocation, unchanged. `import airuleset`
(authority) and `import ratchet_counts` (net-drain) resolve via the adapter's
PYTHONPATH=REPO_ROOT.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime

from gates.filing.parse import (
    EXEMPT_FROM_CAP,
    STREAM_LABEL_RE,
    STREAM_ROUTING_RE,
    _all_labels,
    _explicit_stream_labels,
    _title_jaccard,
    _tokenize,
    TOKEN_JACCARD_THRESHOLD,
)

DAILY_CAP = 8

CHAIN_WIDTH_CAP = 2


def _gh_view_text(parent, cwd, repo=None):
    """title + "\\n" + body of issue `parent`, or None on ANY failure
    (offline, no `gh` auth, the issue genuinely doesn't exist, `gh` not on
    PATH). A failure here degrades the chain-depth check to "cannot
    verify" -- it must NEVER block on its own; the existing Scope-gate
    criterion still decides, exactly as before this ticket.

    `repo` (optional): this filing's own explicit `-R`/`--repo` value, if
    any. Without it, `gh issue view` resolves the parent against the
    INVOKING cwd's own git remote regardless of which repo the filing
    itself targets -- a cross-repo filing (`-R other/repo`, a shape this
    ruleset actively encourages for cross-project references) would
    silently look up an unrelated same-numbered issue in the WRONG repo
    (#311 adversarial-review finding F8, TRIGGERED live)."""
    try:
        argv = ["gh", "issue", "view", str(parent), "--json", "title,body"]
        if repo:
            argv += ["-R", repo]
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=8, cwd=cwd)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or "{}")
        return (data.get("title") or "") + "\n" + (data.get("body") or "")
    except Exception:
        return None


# #329 -- cached per (cwd, repo) WITHIN THIS ONE hook invocation, since a
# batch can file several issues into the same repo in one command and each
# would otherwise repeat the identical `gh issue list` call.
_issue_list_cache = {}


def _fetch_open_issues(cwd, repo):
    """Real open-issue (number, title) pairs for the target repo, bounded
    and cached. Returns None on ANY failure -- offline, unauthenticated,
    `gh` missing, rate-limited, malformed JSON -- so a lookup failure
    degrades the near-duplicate check to "cannot verify"; it never
    manufactures a block on its own. Empirically confirmed (this ticket):
    an unauthenticated `gh issue list` fails in ~70ms with no network
    hang, so this is safe to run unconditionally.

    KNOWN RESIDUAL (documented, not chased): `--limit 200` truncates to
    the 200 most-recently-CREATED open issues, so a duplicate of a much
    older issue on a 200+-open-issue repo can be missed -- degrades
    toward allowing, never toward a false block."""
    key = (cwd, repo)
    if key in _issue_list_cache:
        return _issue_list_cache[key]
    result = None
    try:
        argv = ["gh", "issue", "list", "--state", "open", "--limit", "200",
                "--json", "number,title"]
        if repo:
            argv += ["-R", repo]
        out = subprocess.run(argv, capture_output=True, text=True,
                              timeout=8, cwd=cwd)
        if out.returncode == 0:
            data = json.loads(out.stdout or "[]")
            if isinstance(data, list):
                result = data
    except Exception:
        result = None
    _issue_list_cache[key] = result
    return result


def _near_duplicate(title, body, cwd, repo, batch_titles):
    """Number (as a string) of an existing OPEN issue whose title
    near-duplicates `title` (token-Jaccard >= TOKEN_JACCARD_THRESHOLD), or
    the literal string "in-batch" if the duplicate is instead an earlier
    sibling filed into the SAME target repo within THIS SAME Bash command
    (checked first, cheap, no network -- #329 adversarial review: a remote
    `gh issue list` fetch can never see a sibling that has not been filed
    yet at PreToolUse time), or None if neither is found. An existing
    issue already referenced by #N anywhere in title+body is skipped --
    an explicit link is not a silent duplicate. Degrades to None whenever
    the real remote lookup can't run at all (see _fetch_open_issues)."""
    haystack = (title or "") + "\n" + (body or "")
    if not _tokenize(title):
        return None
    for other_title in batch_titles:
        if _title_jaccard(title, other_title) >= TOKEN_JACCARD_THRESHOLD:
            return "in-batch"
    issues = _fetch_open_issues(cwd, repo)
    if not issues:
        return None
    best = None
    for it in issues:
        if not isinstance(it, dict):
            continue
        num = it.get("number")
        other = it.get("title") or ""
        if num is None or not other:
            continue
        if re.search(r'#%s\b' % re.escape(str(num)), haystack):
            continue  # explicitly referenced -- not a silent duplicate
        ratio = _title_jaccard(title, str(other))
        if ratio >= TOKEN_JACCARD_THRESHOLD and (best is None or ratio > best[1]):
            best = (num, ratio)
    return str(best[0]) if best else None


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _log_pass_count(path, repo, today, parent=None):
    """Count of PASS-verdict filings already WRITTEN to the scope-gate log
    for `repo` on `today`, EXCLUDING any entry whose own `criterion=` is
    exempt from the cap (#329 adversarial-review finding: `_log_pass_count`
    used to count EVERY PASS line regardless of criterion, so an exempt
    `planned-work`/`user-request` batch silently consumed the SAME budget
    the block message promised was reserved for non-exempt filings) --
    optionally further scoped to filings whose OWN logged `parents=` field
    named `parent` (the chain-width count). A missing/unreadable log -> 0
    (never invents a cap violation from unmeasurable state).

    Every counting-relevant field (`verdict=`, `repo=`, `criterion=`,
    `parents=`) is extracted with a plain `\\bfield=(\\S+)` search rather
    than an end-of-line anchor -- safe because the LOG LINE FORMAT itself
    places every one of these fields BEFORE the two free-text fields
    (title, dedup), so the first match in the line is always the real one,
    never a decoy value a crafted title could spell out later in the same
    line (#329 adversarial-review finding: the original `parents=(\\S+)`
    search had no such guarantee and a title containing literal text
    "parents=999" could shift the extracted value)."""
    count = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.startswith(today):
                    continue
                mv = re.search(r'\bverdict=(\S+)', line)
                if not mv or mv.group(1) != "PASS":
                    continue
                mr = re.search(r'\brepo=(\S+)', line)
                if not mr or mr.group(1) != repo:
                    continue
                mc = re.search(r'\bcriterion=(\S+)', line)
                if mc and mc.group(1).lower() in EXEMPT_FROM_CAP:
                    continue
                if parent is not None:
                    mp = re.search(r'\bparents=(\S+)', line)
                    if not mp or parent not in mp.group(1).split(","):
                        continue
                count += 1
    except OSError:
        pass
    return count


# Cached per (cwd, repo) / (cwd, repo_dir) WITHIN THIS ONE hook invocation,
# same shape as `_issue_list_cache` above -- a batch filing several issues
# in one command must not repeat the identical `gh label list` call or the
# identical authority resolution per item.
_label_list_cache = {}


_authority_cache = {}


def _repo_stream_labels(cwd, repo):
    """Real label NAMES for `repo` (bounded, cached), or None on ANY
    failure (offline, unauthenticated, `gh` missing, malformed JSON) --
    degrades the stream-routing gate to "cannot verify", never blocks on
    its own. Never touches the real network more than once per (cwd, repo)
    in this invocation."""
    key = (cwd, repo)
    if key in _label_list_cache:
        return _label_list_cache[key]
    result = None
    try:
        argv = ["gh", "label", "list", "--json", "name", "-L", "200"]
        if repo:
            argv += ["-R", repo]
        out = subprocess.run(argv, capture_output=True, text=True,
                              timeout=8, cwd=cwd)
        if out.returncode == 0:
            data = json.loads(out.stdout or "[]")
            if isinstance(data, list):
                result = [str((it or {}).get("name") or "") for it in data
                          if isinstance(it, dict)]
    except Exception:
        result = None
    _label_list_cache[key] = result
    return result


def _repo_is_stream_aware(cwd, repo):
    """True/False, or None when unmeasurable (see `_repo_stream_labels`)."""
    names = _repo_stream_labels(cwd, repo)
    if names is None:
        return None
    return any(STREAM_LABEL_RE.match(n) for n in names)


def _filer_authority_and_own_stream(cwd, repo_dir):
    """(authority_profile, own_stream_label) for the LINUX USER running
    this hook -- imports airuleset.py directly (from `repo_dir`, the
    hook's own checkout root, passed in from bash via BASH_SOURCE) rather
    than duplicating AUTHORITY_BY_USER's key list -- `resolve_authority()`
    is the SAME function `airuleset.py authority` itself calls, matching
    the issue's own "rovnako, ako to už robí airuleset.py authority".
    `(None, None)` on ANY failure (import, resolve) -- the caller must
    treat that as "cannot verify a stream identity" and skip the gate,
    never guess. Every current AUTHORITY_BY_USER key doubles as its own
    stream-label suffix (`label:stream:%s % u for u in AUTHORITY_BY_USER`
    is how every existing consumer already reads it), so the filer's own
    stream is simply `stream:<their-linux-username>`."""
    key = (cwd, repo_dir)
    if key in _authority_cache:
        return _authority_cache[key]
    result = (None, None)
    try:
        if repo_dir and repo_dir not in sys.path:
            sys.path.insert(0, repo_dir)
        import airuleset as _ar
        profile = _ar.resolve_authority(cwd)
        # airuleset#840: derive the filer's OWN stream from the un-spoofable
        # uid-based identity (`_current_user()` = pwd.getpwuid(os.getuid()), the
        # #839 single source), NOT `getpass.getuser()` -- the latter reads
        # $LOGNAME/$USER FIRST, so a reduced stream could set USER=<other-stream>
        # to make its own-stream appear FOREIGN and file under that stream's
        # `stream:<other>` label with no `Stream-routing:` justification,
        # bypassing this whole #390 labeling-HYGIENE gate. `_current_user()`
        # reads the real uid; a stream controls its env and its repo files, never
        # its uid.
        #
        # TEST-IDENTITY SEAM (airuleset#840): because `_current_user()` reads the
        # real uid, a subprocess test cannot change it in-process, so the own-
        # stream identity is taken from `AIRULESET_SCOPE_GATE_TEST_STREAM_USER`
        # -- but ONLY when the REAL invoking account is a genuine
        # full-authority / CI-runner box: `user in FULL_AUTHORITY_USERS`
        # (newlevel/gatekeeper/admin/stepan — the dev1 test box) OR the
        # GitHub-hosted CI runner (`_github_ci_runner_source(user)` — runner/root
        # under the CI env, where the #390 suite runs). #842-review 🔵: the
        # earlier `not in AUTHORITY_BY_USER` guard ALSO admitted an UNMAPPED box
        # (which resolves fork-no-merge via the #827 fail-safe AND engages the
        # #390 gate), letting it spoof its own-stream label via the env var; this
        # positive allow-list closes that. A real reduced stream's uid is in
        # AUTHORITY_BY_USER (never in either allow), so it can never activate the
        # seam. The seam is on THIS own-stream read alone, NEVER on
        # `_current_user()` itself, which would re-open the env-spoof on the
        # merge/deploy/close authority path #839 hardened.
        user = _ar._current_user()
        _seam = os.environ.get("AIRULESET_SCOPE_GATE_TEST_STREAM_USER")
        if _seam and (user in _ar.FULL_AUTHORITY_USERS
                      or _ar._github_ci_runner_source(user)):
            user = _seam
        result = (profile, ("stream:%s" % user).lower())
    except Exception:
        result = (None, None)
    _authority_cache[key] = result
    return result


def _stream_routing_block_reason(tk, is_api, body, cwd, target_repo, repo_dir):
    """#390 -- a block-reason string, or None (nothing to block -- covers
    every degrade-to-unmeasurable case too, per this hook's own
    established bias: never manufacture a block from state that could not
    be measured).

    #390 adversarial-review MAJOR-1: the cheap, LOCAL authority check runs
    FIRST, before the network `gh label list` call -- a full-authority
    filer (never gated by this gate at all) must never pay that round-trip
    on every single filing fleet-wide. This mirrors the hook's own #329
    "cheap local checks before the network call" discipline elsewhere in
    this file.

    #390 adversarial-review MINOR-1 (documented, not a code change): a
    filing that carries BOTH the filer's own stream label AND a foreign
    one (e.g. `-l stream:david2 -l stream:david`) needs no
    `Stream-routing:` justification -- `own_label in applied` accepts it
    the moment the filer's own label is present, regardless of what else
    rides alongside it. This is deliberate: the filer's own label already
    proves the filing is (at least in part) that filer's own work: routing
    it under an ADDITIONAL, foreign label as well is a normal
    cross-stream-relevance tag, not a mis-file.

    #962: when a foreign-label filing carries a `Stream-routing:` body
    line, it ALSO requires `-l needs-gatekeeper` -- auto-routing the
    ticket to the gatekeeper for triage. Without the label the block
    message names the exact flag to add."""
    if is_api:
        return None
    profile, own_label = _filer_authority_and_own_stream(cwd, repo_dir)
    if profile is None or profile == "full":
        return None             # no known "own" stream -- not gated
    aware = _repo_is_stream_aware(cwd, target_repo)
    if not aware:              # False, or None (unmeasurable) -- never blocks
        return None
    applied = _explicit_stream_labels(tk, is_api)
    if not applied:
        return "missing-stream-label"
    if own_label in applied:
        return None
    if body and STREAM_ROUTING_RE.search(body):
        # #962: a justified foreign-label filing must also carry
        # -l needs-gatekeeper to auto-route to the gatekeeper.
        all_lbl = _all_labels(tk, is_api)
        if "needs-gatekeeper" in all_lbl:
            return None
        return ("stream-routing-add-needs-gatekeeper (add "
                "`-l needs-gatekeeper` to auto-route the core ticket "
                "to the gatekeeper for triage)")
    return "stream-routing-unjustified"


def _ratchet_should_block(target_repo, cwd):
    """#842 req 2 -- True when the per-repo net-drain ratchet must BLOCK an
    UNATTENDED non-exempt discovery filing on `target_repo`: the repo is NOT
    strictly draining today (`created_today >= closed_today`). #1020 fold-in --
    the decision recomputes the counts LIVE (`net_drain_blocks_live`), never the
    TTL cache, so it can never disagree with the real `gh` day counts (a drifted
    cache false-blocked at created 2 < closed 3). Fail-SAFE: a gh error or a
    ratchet_counts import failure (`repo_dir` is on sys.path from the top of the
    heredoc) returns True (BLOCK), never a wrong ALLOW (#842 (d))."""
    try:
        import ratchet_counts as _rc
    except Exception:
        return True
    blocks = _rc.net_drain_blocks_live(target_repo, cwd)
    return True if blocks is None else blocks


def cwd_repo_of(cwd):
    """The owner/repo the invoking cwd's `origin` remote points at (the caps'
    FALLBACK target repo when a filing carries no explicit -R), or the cwd
    basename on any failure. VERBATIM from the hook's pre-loop resolution."""
    cwd_repo = os.path.basename(cwd.rstrip("/"))
    try:
        _out = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                              capture_output=True, text=True, timeout=3)
        _url = (_out.stdout or "").strip()
        _m = re.search(r'[:/]([^/]+/[^/]+?)(\.git)?$', _url)
        if _m:
            cwd_repo = _m.group(1)
    except Exception:
        pass
    return cwd_repo
