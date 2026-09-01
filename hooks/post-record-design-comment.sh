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
#
# #815: an OPTIONAL `-R <repo>`/`--repo <repo>` (space or `=` form) is now
# also tolerated directly after `gh` and before `issue comment` -- the
# natural `gh -R <owner>/<repo> issue comment <N> ...` shape a worker uses
# when its cwd is not the target repo used to slip past this prefilter
# entirely (the repo flag sits BETWEEN "gh" and "issue", breaking the old
# contiguous match), silently losing the marker. `_REPO_FLAG_RE` below
# needs no change -- it already scans the WHOLE matched segment for
# `-R`/`--repo` in either position once this prefilter (and the two
# downstream python regexes) actually recognize the invocation.
echo "$CMD" | grep -qE '\bgh[[:space:]]+((-R|--repo)(=|[[:space:]]+)[^[:space:]]+[[:space:]]+)?issue[[:space:]]+comment\b' || exit 0

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


# Free-text flags gh/git themselves consume as literal data -- their VALUE
# is NEVER separately executed by anything, sudo/env-prefixed or not. But
# an UNCONDITIONAL blank is NOT safe on its own (#280 follow-up, F1):
# `-t`/`-m` mean something ENTIRELY different when they belong to some
# OTHER command sharing the same segment -- `ssh host -t "<remote gh
# call>"` -- -t is ssh's OWN flag, and its value is code that actually
# runs remotely, not a git/gh flag argument at all. Blanking it
# unconditionally swallows the whole remote invocation, hiding the
# `gh issue comment` call it carries.
#
# The fix: a flag only counts as gh/git's OWN when a `git`/`gh` command
# word has ALREADY opened the SAME segment (reusing lib_poll_payload's own
# SEP/GITGH building blocks) -- true for `gh issue comment ... --body
# "..."` and for `sudo gh issue comment ... --body "..."` (sudo doesn't
# consume the following args as its own flags, it just re-execs gh with
# them, so "gh " still precedes --body in the segment), false for `ssh
# host -t "..."` (the segment before -t is "ssh host ", no "gh "/"git "
# yet). This is DELIBERATELY not lib_poll_payload's own INTERP-exemption
# shape -- exempting a segment naming sudo/env/ssh would reopen the
# earlier #280 -R-in-quoted-body leak for the sudo-prefixed shape (see
# TestEnvVarPrefixedGhCallIsRecognized). GITGH-precedes is the correct,
# narrower discriminator for this hook's domain.
#
# #282 -- matches only up to the OPENING quote (no `.*?...\1` value
# capture): that shape used to close the "value" at the FIRST quote
# character seen, escaped or not, and had no notion of the shell's
# `'"'"'` single-quote-splice idiom -- letting a fake `gh issue comment N`
# mention (or a smuggled `-R owner/evil`) trapped inside the flag's own
# value survive as live-looking text past the premature close.
# `lib_poll_payload._shell_word_end` (the SAME resolver
# lib_poll_payload.py's own BODYFLAG/MSGFLAG now use, reused here rather
# than reimplemented) resolves the true end of the value instead.
_TEXT_FLAG_VALUE_RE = re.compile(
    r"(?:^|\s)(?:--body|--body-file|--notes|--notes-file|-b|-F|"
    r"-m|--message|--title|-t)(?:=|\s+)(['\"])")


def _blank_gh_text_flag_values(text, lib_poll_payload):
    """Blank the quoted VALUE of every gh/git free-text flag -- scoped to
    the flag's OWN command segment (SEP-bounded, reusing lib_poll_payload's
    own separator tuple) and requiring a `git`/`gh` command word to have
    ALREADY opened that segment before the flag (reusing lib_poll_payload's
    own GITGH regex) -- see `_TEXT_FLAG_VALUE_RE`'s own comment for why
    this is neither fully unconditional nor lib_poll_payload's own
    INTERP-exemption shape. Positional (offset-based, reverse order),
    never by value -- the same discipline lib_poll_payload.py's own
    `flag_spans`/`strip` already use, for the same reason (a value-based
    `str.replace` can delete unrelated live text sharing the same bytes).
    The blanked span covers the WHOLE resolved shell word (#282, quote
    characters included), not just a single quote-pair's inner text."""
    out = text
    for m in reversed(list(_TEXT_FLAG_VALUE_RE.finditer(text))):
        head = text[:m.start()]
        cut = max([head.rfind(s) for s in lib_poll_payload.SEP] + [-1])
        seg = text[cut + 1:m.start()]
        if not lib_poll_payload.GITGH.search(seg):
            continue
        start = m.start(1)
        end = lib_poll_payload._shell_word_end(text, start)
        if end - start > 2:                # more than a bare '' / ""
            out = out[:start] + " " + out[end:]
    return out


# #280 follow-up (adversarial-review F4, pre-existing -- not introduced
# by the flag-blanking fix above): the -R/--repo lookup below used to
# scan the WHOLE scan_cmd for ANY `-R`/`--repo`-shaped text, so a value
# living in a segment that is NOT the `gh issue comment` invocation at
# all (an `echo` argument, a different `gh` subcommand's own flag/value
# elsewhere in the SAME compound command -- `gh issue create --label
# "-R attacker/x"`, `gh pr view 5 -R other/unrelated`) was scavenged as
# if it belonged to the invocation being classified, silently
# redirecting the marker to the wrong repo. `_REPO_FLAG_RE` is now only
# ever searched WITHIN the SEP-bounded segment of the specific `gh issue
# comment` match it's being resolved for.
#
# A LOCAL `_SEG_SEP` tuple, deliberately NOT `lib_poll_payload.SEP` --
# this scoping must keep working even when that module failed to import
# above (see `_control_flow_text`'s own fallback comment), and it is a
# small, stable literal not worth a second import for.
_REPO_FLAG_RE = re.compile(r'(?:-R|--repo)[= ]+([^\s\'"]+)')
_SEG_SEP = (";", "&&", "||", "|", "\n")


def _match_segment(text, start):
    """The `_SEG_SEP`-bounded segment containing offset `start` -- bounded
    on BOTH sides (a `-R` can appear either BEFORE or AFTER "gh issue
    comment N" within the SAME invocation), unlike
    `_blank_gh_text_flag_values`'s own left-only segment (it only ever
    needs "does gh/git precede this flag")."""
    left = max([text.rfind(s, 0, start) for s in _SEG_SEP] + [-1])
    ends = [e for e in (text.find(s, start) for s in _SEG_SEP) if e != -1]
    right = min(ends) if ends else len(text)
    return text[left + 1:right]


def _control_flow_text(text, repo_root):
    """`text` with provably-inert heredoc bodies and quoted text-payload
    argument VALUES blanked out -- REUSES hooks/lib_poll_payload.py's own
    `strip()` (the #124 payload-vs-control-flow classifier: a heredoc body
    is blanked only when its owner names no interpreter, is a plain
    redirect to a literal file path, and every other mention of that path
    is a text-file-flag argument -- so a heredoc genuinely `cat`'d into a
    script and run in the SAME command stays visible), never a parallel
    reimplementation of it. Chained with `_blank_gh_text_flag_values` (see
    its own comment for why lib_poll_payload's flag-value blanking alone
    is not enough here).

    #280 -- both extraction regexes below used to scan the RAW command
    text: a heredoc BODY that only documents/mentions "gh issue comment"
    (never executed unless something later runs it), or a `-R` value
    sitting inside a quoted `--body`/`-m` argument's free text, was
    misread as a real invocation/flag. Scanning THIS text instead fixes
    both without touching the distinct-repo ambiguity refusal below.

    #280 follow-up (adversarial-review F1/F2) -- flag-VALUE blanking now
    runs FIRST, the heredoc-aware strip SECOND (the reverse of the
    original order): a --body VALUE whose prose merely mentions a
    heredoc recipe (`cat > body.md << EOF`) was otherwise misread by the
    heredoc detector as a REAL trigger, swallowing everything after it
    (including a sibling `gh issue comment` invocation) as if it were
    that fake heredoc's own body. Blanking the quoted value first removes
    the fake trigger before heredoc detection ever runs.

    Never crashes or disables the whole hook on failure -- but the exact
    fallback VALUE depends on which stage failed (adversarial-review
    MINOR, post-#280): an import failure returns `text` genuinely
    UNCHANGED (the original raw command); a failure in EITHER later
    processing stage returns whatever text the EARLIER stage(s) already
    produced, not the original raw command -- each stage's own
    try/except only ever refuses to advance PAST its own broken step, it
    never rewinds an already-applied one. A logged import failure
    (below) is the only one of the two that leaves the WHOLE hook
    running on unprocessed raw text for every later command in this
    invocation, which is why it is the one worth a diagnostic line."""
    try:
        hooks_dir = os.path.join(repo_root, "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        import lib_poll_payload
    except Exception as exc:
        _log_exception("lib_poll_payload-import", exc)
        return text
    try:
        text = _blank_gh_text_flag_values(text, lib_poll_payload)
    except Exception:
        pass
    try:
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

    # #436 -- resolve the repo this comment is actually landing in, exactly
    # the way block-commit-without-design.sh's own #187/#220 resolution
    # does (notify.resolve_work_cwd) -- a worker dispatched with session
    # cwd = repo A, running `cd /path/to/repo-B && gh issue comment ...`,
    # must have its marker written under repo B's key, not repo A's. The
    # SAME anchored, injection-hardened cd-prefix parser is reused
    # verbatim; only the TRIGGER differs (this hook's own command shape is
    # `gh issue comment`, never `git commit`). Used for BOTH the repo_key
    # lookup below AND the `gh issue view` verification subprocess's own
    # cwd, so the two can never disagree about which repo this is.
    # #815: tolerate an optional `-R <repo>`/`--repo <repo>` (space or `=`
    # form) between `gh` and `issue comment` -- same shape as the bash
    # prefilter above and the finditer capture below, so all three
    # adjacency points agree on what counts as a `gh issue comment`
    # invocation.
    work_cwd = notify.resolve_work_cwd(
        cmd, cwd, trigger_re=re.compile(
            r"\bgh\s+(?:(?:-R|--repo)[= ]+\S+\s+)?issue\s+comment\b"))

    scan_cmd = _control_flow_text(cmd, repo_root)

    # #208 -- every `gh issue comment <N>` occurrence in the command text,
    # not just the first: a batch worker often posts several design/
    # validated/reviewed comments in ONE Bash call before a single bundled
    # commit, and `re.search` (a single, non-global match) used to extract
    # only the FIRST -- the rest silently never got checked or marked.
    #
    # #815 -- same optional `-R <repo>`/`--repo <repo>` tolerance as the
    # bash prefilter and `trigger_re` above, between `gh` and `issue
    # comment` (`gh -R <owner>/<repo> issue comment <N> ...`). The repo
    # VALUE captured here is unused (`_REPO_FLAG_RE` below independently
    # resolves it from the whole matched segment, in either position) --
    # this only has to recognize the invocation and capture the target
    # after "comment ".
    matches = list(re.finditer(
        r'gh\s+(?:(?:-R|--repo)[= ]+\S+\s+)?issue\s+comment\s+(\S+)', scan_cmd))
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
    #
    # #280 follow-up F4 -- each match's own -R/--repo is now looked up
    # ONLY within that match's own segment (`_match_segment`), never the
    # whole command, so an unrelated -R/--repo-shaped string elsewhere in
    # a compound command can no longer be scavenged as this invocation's
    # repo (see `_match_segment`'s own comment for real examples).
    mrepos = []
    for m in matches:
        mrepos.extend(_REPO_FLAG_RE.findall(_match_segment(scan_cmd, m.start())))
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
        repo_key = notify.repo_name_for(work_cwd)
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
                capture_output=True, text=True, timeout=10, cwd=work_cwd)
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
            if ok and kind == "design":
                # #414 -- SOTA architecture: a "design" marker ALSO
                # requires the Architektúra:/Triage: shape -- but #428:
                # ONLY when the ticket triaged NON-TRIVIAL. A TRIVIAL
                # ticket needs nothing beyond its own Triage: line
                # (classify_triage_and_approaches's own doc comment, and
                # CYCLE step 2's settled "depth scales with the problem"
                # principle) -- so arch_ok is required ONLY when
                # dg.triage_class(body) is NOT "trivial" (this also covers
                # the "Triage: line missing/unrecognized" case, where
                # triage_ok already fails on its own and the combined
                # reject reason below still names what's missing). ANDed
                # here, NEVER inside classify_design_comment() itself --
                # that function has three OTHER independent consumers that
                # depend on its stable, unchanged meaning (see
                # design_gate.py's own #414 section). Checking BOTH even
                # when the first already failed gives a complete "what's
                # missing" picture in one shot instead of a multi-round
                # discovery.
                triage_ok, triage_reason = dg.classify_triage_and_approaches(body)
                is_trivial = triage_ok and dg.triage_class(body) == "trivial"
                if is_trivial:
                    arch_ok, arch_reason = True, "ok (trivial -- architecture not required)"
                else:
                    arch_ok, arch_reason = dg.classify_architecture_section(body)
                if not (arch_ok and triage_ok):
                    parts = [r for ok2, r in
                             ((arch_ok, arch_reason), (triage_ok, triage_reason))
                             if not ok2]
                    dg.write_reject_reason(repo_key, issue, "; ".join(parts), kind=kind)
                    # #414-review MINOR-2 -- this text IS design-shaped
                    # (classify_design_comment already said so); it only
                    # failed the ADDED Architektúra:/Triage: requirement.
                    # Do not let it fall through and be silently absorbed
                    # as a DIFFERENT kind's marker just because the SAME
                    # prose also happens to carry e.g. validation-sounding
                    # language -- the worker's fix is to REPOST the design
                    # comment with the missing piece, never to have this
                    # rejected attempt quietly satisfy another kind.
                    break
            if ok:
                dg.write_marker(repo_key, issue, url, reason, kind=kind)
                break
            else:
                dg.write_reject_reason(repo_key, issue, reason, kind=kind)
except SystemExit:
    raise
except Exception as exc:
    _log_exception("main", exc)
PYEOF

exit 0
