#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash matcher) — #136 design-before-code gate half 1/3,
# EXTENDED by #213 (validated-posted) and #214 (reviewed-posted) to the same
# artifact pattern: EVERY posted `gh issue comment` is classified against
# ALL THREE shapes (design / validated / reviewed) from the SAME single
# re-read — a worker's Step 0 validation comment and Step 6 review comment
# get their own markers exactly like a design comment does, with zero new
# hook registrations. `subagent-stop-check-design.sh` is the consumer for
# all three.
#
# The ONLY place a design/validated/reviewed marker
# (~/.claude/<kind>-posted/<repo>#<issue>) is ever written. Never
# speculative, never trusted from the command that was about to run — this
# hook re-reads the ACTUAL posted comment back from GitHub (`gh issue view
# --json comments`), the same #135 lesson `notify.marker_delivered` encodes:
# a marker's presence must be backed by an OBSERVED delivery, not an intent.
#
# Why re-query instead of trusting Bash's own tool_response: this repo has
# no prior art anywhere for a Bash PostToolUse hook reading tool_response
# stdout (grep confirms it — every existing PostToolUse hook either ignores
# the result entirely or does its own separate query), and the field names
# for that payload are undocumented here. Never guess a payload shape;
# GitHub itself is the one source of truth this hook needs anyway.
#
# DETECTION is deliberately light — a `gh issue comment` invocation's issue
# number/URL and an optional `-R`/`--repo`. The comment BODY is never parsed
# out of the command text at all (unlike block-ungated-issue-filing.sh's
# heavy heredoc/body-file resolution) — irrelevant here, since the body we
# classify is the one read back from GitHub, not the one in the command.
#
# FRESHNESS — only a comment authored by the current viewer AND posted in
# the last 180s counts as evidence for THIS invocation (an old, already-
# compliant comment from earlier work must not retroactively excuse a
# *different*, just-failed or off-topic `gh issue comment` call).
#
# Never blocks (PostToolUse can't undo a command that already ran) — this is
# an observer, always exits 0. hooks/block-commit-without-design.sh is the
# one that actually stops anything, keyed on the marker this hook writes.

INPUT=$(cat 2>/dev/null || echo "")
[ -n "$INPUT" ] || exit 0
command -v jq &>/dev/null || exit 0
command -v python3 &>/dev/null || exit 0
command -v gh &>/dev/null || exit 0

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
[ -n "$CMD" ] || exit 0

# Cheap prefilter before touching python/gh at all. #277/#274: a plain
# word-boundary match, not a prefix-anchored alternation -- GNU grep's \b
# is a correct word boundary, so it matches "gh issue comment" regardless
# of what precedes it (an inline `VAR=$(...)` assignment of any shape,
# `sudo `/`env `, a command separator, or start-of-string) while still
# refusing a "gh" glued onto another word. The prior alternation
# (`(^|[;&|]|&&)...(sudo|env)?gh...`) missed the documented stream-account
# recipe `GH_TOKEN=$(...) gh issue comment ...` entirely -- no marker was
# ever written for that shape, even for a fresh, valid, classifying
# comment, because the hook exited here before touching python/gh at all.
echo "$CMD" | grep -qE '\bgh[[:space:]]+issue[[:space:]]+comment\b' || exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"

# Data via ARGV, never a pipe into a `python3 -` heredoc — the heredoc
# already claims stdin for the SCRIPT SOURCE (this repo's own recurring
# trap, see subagent-stop-check-run-card.sh).
python3 - "$REPO_ROOT" "$CMD" "$CWD" <<'PYEOF' 2>/dev/null || true
import datetime
import json
import os
import re
import subprocess
import sys

repo_root, cmd, cwd = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo_root)


def _log_exception(where, exc):
    # #274 -- best-effort diagnostic trail for a genuinely UNEXPECTED
    # failure (never one of the routine "nothing to do" sys.exit(0) cases
    # below, which raise SystemExit and are never caught here). Never
    # raises, never blocks, never touches stdout/stderr -- the hook stays
    # silent and non-blocking either way; this replaces the previous total
    # silence under the outer `2>/dev/null || true` with a file a human can
    # actually read later.
    try:
        log_path = os.path.join(os.path.expanduser("~"), ".claude",
                                 "design-gate-errors.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        # O_NOFOLLOW so a planted symlink at this path is never written
        # THROUGH (this repo's own established vault-store/draft-rescue
        # discipline for any new on-disk writer); the boxes this hook runs
        # on host foreign uids by design.
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND |
                     os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\t%r\n" % (
                datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                where, cwd, exc))
    except Exception:
        pass


def _control_flow_text(text, repo_root):
    """`text` with provably-inert heredoc bodies and quoted text-payload
    argument VALUES blanked out -- REUSES hooks/lib_poll_payload.py's own
    `strip()` (the #124 payload-vs-control-flow classifier: a heredoc body
    is blanked only when its owner names no interpreter, is a plain
    redirect to a literal file path, and every other mention of that path
    is a text-file-flag argument -- so a heredoc genuinely `cat`'d into a
    script and run in the SAME command stays visible), never a parallel
    reimplementation of it.

    #280 -- both extraction regexes below used to scan the RAW command
    text: a heredoc BODY that only documents/mentions "gh issue comment"
    (never executed unless something later runs it), or a `-R` value
    sitting inside a quoted `--body`/`-m` argument's free text, was
    misread as a real invocation/flag. Scanning THIS text instead fixes
    both without touching the distinct-repo ambiguity refusal below.

    Falls back to `text` UNCHANGED on any import/processing failure --
    this must never crash or disable the whole hook."""
    try:
        hooks_dir = os.path.join(repo_root, "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        import lib_poll_payload
        return lib_poll_payload.strip(text)
    except Exception:
        return text


try:
    import design_gate as dg
    import notify
except Exception as exc:
    _log_exception("import", exc)
    sys.exit(0)

try:
    if not cwd:
        sys.exit(0)

    scan_cmd = _control_flow_text(cmd, repo_root)

    # #208 -- every `gh issue comment <N>` occurrence in the command text,
    # not just the first: a batch worker often posts several design/
    # validated/reviewed comments in ONE Bash call before a single bundled
    # commit, and `re.search` (a single, non-global match) used to extract
    # only the FIRST -- the rest silently never got checked or marked.
    matches = list(re.finditer(r'gh\s+issue\s+comment\s+(\S+)', scan_cmd))
    if not matches:
        sys.exit(0)

    # Adversarial-review finding: `-R`/`--repo` is resolved ONCE and shared
    # across every issue in the loop below -- correct for the realistic,
    # reported shape (the SAME repo repeated on every call), but a command
    # naming TWO DIFFERENT repos would otherwise write a LATER issue's
    # marker under an EARLIER issue's (wrong) repo, silently unblocking a
    # commit gate that was never actually satisfied. Refuse the whole
    # command rather than guess which repo each issue belongs to -- this
    # hook has no per-segment command parser, and never guessing is safer
    # than resolving to a plausible-looking wrong repo.
    mrepos = re.findall(r'(?:-R|--repo)[= ]+([^\s\'"]+)', scan_cmd)
    distinct_repos = set(mrepos)
    if len(distinct_repos) > 1:
        _log_exception("ambiguous-repo", ValueError(
            "multiple distinct -R/--repo values in one command: %r"
            % sorted(distinct_repos)))
        sys.exit(0)
    explicit_repo = mrepos[0] if mrepos else None

    if explicit_repo:
        repo_key = explicit_repo.rstrip("/").split("/")[-1]
        gh_repo_args = ["-R", explicit_repo]
    else:
        repo_key = notify.repo_name_for(cwd)
        gh_repo_args = []

    if not repo_key:
        sys.exit(0)

    FRESH_WINDOW_S = 180
    now = datetime.datetime.now(datetime.timezone.utc)

    def parsed_ts(c):
        raw = c.get("createdAt") or ""
        try:
            return datetime.datetime.strptime(
                raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        except (ValueError, TypeError):
            return None

    classifiers = {
        "design": dg.classify_design_comment,
        "validated": dg.classify_validation_comment,
        "reviewed": dg.classify_review_comment,
    }

    # Dedup issue numbers, first-seen order (mirrors design_gate.issue_refs's
    # own dedup shape) -- a repeated mention of the same issue in one
    # command should not re-hit the network twice.
    seen_issues = []
    for m in matches:
        target = m.group(1).strip("'\"")
        issue = None
        mnum = re.match(r'^(\d+)$', target)
        if mnum:
            issue = int(mnum.group(1))
        else:
            murl = re.search(r'/issues/(\d+)', target)
            if murl:
                issue = int(murl.group(1))
        if issue is not None and issue not in seen_issues:
            seen_issues.append(issue)

    for issue in seen_issues:
        # Already recorded — never re-hit the network for a settled issue.
        # "Settled" means ALL THREE kinds are marked (#213/#214) -- if even
        # one is still missing, a LATER comment for this same issue might
        # supply it, so we must keep re-reading until all are in. `continue`
        # (never sys.exit(0)) so one issue's outcome never short-circuits
        # the rest of the command's issues.
        if all(dg.marker_exists(repo_key, issue, k) for k in dg.ALL_KINDS):
            continue

        try:
            r = subprocess.run(
                ["gh", "issue", "view", str(issue)] + gh_repo_args + ["--json", "comments"],
                capture_output=True, text=True, timeout=10, cwd=cwd)
        except Exception as exc:
            _log_exception("gh-view #%d" % issue, exc)
            continue
        if r.returncode != 0:
            continue
        try:
            comments = json.loads(r.stdout or "{}").get("comments", [])
        except (ValueError, TypeError, AttributeError):
            continue
        if not isinstance(comments, list):
            continue

        candidates = []
        for c in comments:
            if not isinstance(c, dict) or not c.get("viewerDidAuthor"):
                continue
            ts = parsed_ts(c)
            if ts is None or (now - ts).total_seconds() > FRESH_WINDOW_S:
                continue
            candidates.append((ts, c))

        if not candidates:
            continue
        candidates.sort(key=lambda pair: pair[0])
        _, latest = candidates[-1]

        body = latest.get("body", "")
        url = latest.get("url", "")

        # Adversarial-review finding: one comment must not grant more than
        # one evidence kind -- (a) never re-use a comment url that already
        # granted a DIFFERENT kind (a stale/unchanged "latest" comment
        # re-read on a later, trivial `gh issue comment` call must not let
        # a rich earlier comment keep paying out new kinds), and (b) even
        # within ONE pass over a fresh comment, grant at most the FIRST
        # still-missing kind it classifies for, never all that happen to
        # match at once.
        if url and url in dg.claimed_urls(repo_key, issue):
            continue

        for kind in dg.ALL_KINDS:
            if dg.marker_exists(repo_key, issue, kind):
                continue
            ok, reason = classifiers[kind](body)
            if ok:
                dg.write_marker(repo_key, issue, url, reason, kind=kind)
                break
except SystemExit:
    raise
except Exception as exc:
    _log_exception("main", exc)
PYEOF

exit 0
