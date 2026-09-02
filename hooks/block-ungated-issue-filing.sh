#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) — issue #137 ("File-vs-fix asymmetry").
#
# stop-check-untracked-work.sh (Stop, every message) HARD-blocks a message that
# dismisses discovered work without filing it — filing has teeth. The opposite
# direction — complete-planned-work.md's Follow-up gate, "a <100-LoC cleanup in
# a file your PR already touches gets FIXED NOW in this PR, not filed" — has
# none: it is prose only. Measured leak (21-day investigation, #137): 7 of a
# 25-issue real sample violate the gate WITH THE VIOLATION CONFESSED in the
# issue body itself (camera-box #846, #843; odoo-erp #2388 all say so in
# plain text). A hook cannot mechanically verify "is this fix genuinely
# <100 LoC" — the fix doesn't exist yet when the issue is filed — so this
# hook does the only thing a hook CAN do: force an explicit, LOGGED,
# falsifiable claim instead of letting the silent default (just file it) win
# by default. It converts a silent leak into either an in-PR fix or an
# affirmative claim on the record; it does NOT and CANNOT verify the claim is
# true. That is the honest limit, stated here rather than hidden.
#
# GATE: `gh issue create` / `gh api .../issues` (POST) is allowed only when
# the issue BODY contains a line `Scope-gate: <criterion>` naming one of the
# bundling gate's own established exemptions:
#   >300-loc | schema-migration | api-break | security-boundary |
#   cross-cutting | needs-user-decision
# plus the two legitimate NON-discovery filing modes:
#   planned-work   — converged-plan decomposition (durable-decisions-to-tickets.md)
#   user-request   — the user explicitly asked for this ticket
#
# CHAIN-DEPTH CAP (#311, added after odoo-erp's #3035→#3220→#3224→#3250→
# #3251→#3252→#3258 seven-ticket review-finding chain — each hop honestly
# satisfied its OWN Scope-gate criterion, which is exactly why a per-issue
# criterion alone cannot stop this): a new issue naming its own PARENT as a
# "follow-up" (`#N follow-up`, the exact phrasing real chain members already
# use) is ALSO BLOCKED when that PARENT is itself such a follow-up — a
# depth-2 review-finding chain — regardless of whether a Scope-gate
# criterion is present. Detected via a cheap text match plus ONE bounded
# `gh issue view` call on the named parent; a lookup failure degrades to
# "cannot verify chain depth" and never blocks on its own.
#
# DEDUP GATE (#329, measured: odoo-erp's kvaskodev stream self-authored 143
# issues, 45 still open = 76% of its own queue). A body must ALSO carry a
# `Dedup-checked: <what you searched>` line (same logged-claim shape as
# `Scope-gate:` — cheap, no network, forces a deliberate self-report) AND
# independently the hook fetches the target repo's real OPEN issue titles
# (bounded `gh issue list --state open --limit 200`, degrading to "cannot
# verify" on ANY failure — offline, unauthenticated, rate-limited, `gh`
# missing) and diffs the new title against every one of them using TOKEN-SET
# JACCARD overlap (ratio >= 0.7), NOT a character-level ratio — a
# character-level SequenceMatcher was tried first and PROVABLY cannot
# separate this fleet's real duplicates from its real distinct tickets (see
# the `_title_jaccard` docstring below for the measured numbers). A
# near-duplicate BLOCKS naming the existing issue number (or "in-this-batch"
# for a sibling filed earlier in the SAME command), UNLESS that number is
# already referenced (`#N`) somewhere in the new title/body — an explicit
# link is not a silent duplicate; this exemption is deliberately gameable by
# an agent that already knows the duplicate's number (an accepted residual —
# it still fails toward allowing, never toward a false block). Neither half
# of this gate ever manufactures a block from an unmeasurable state: a
# missing line is a real, structural gap (blocks); a failed `gh` lookup is
# "couldn't check" (never blocks on its own). The near-dup check is NOT
# exempt for `user-request`/`planned-work` (unlike the two caps below) — a
# literal duplicate is never useful regardless of who asked for it.
#
# DAILY FILING CAP (#329, soft, per repo): counts today's own `verdict=PASS`
# lines already written to ~/.claude/scope-gate.log for the SAME repo AND
# whose own `criterion=` is NOT `user-request`/`planned-work` (plus filings
# already classified PASS earlier in the same Bash call, since one batch can
# file several issues before anything is written). Once that count reaches
# 8, a filing whose `Scope-gate:` criterion is itself non-exempt is BLOCKED
# — the intent is to push discovered work toward "fix it now, in this
# branch" per no-dropped-work.md, never toward silently dropping it; the
# block message says exactly that. `user-request`/`planned-work` filings
# never COUNT toward the cap and never GET capped by it, because a user
# directive or a converged-plan decomposition must always be able to file
# (durable-decisions-to-tickets.md).
#
# CHAIN-WIDTH CAP (#329, extends #311's chain-DEPTH cap with the WIDTH half
# of the same measured failure — odoo-erp's real #3250/#3251/#3252/#3258,
# four siblings off ONE parent, #3224, in one burst, each individually
# depth-1 and so invisible to the depth cap alone). Reuses the SAME
# `_chain_parents()` "(#N follow-up)" extraction: once ANY parent a filing
# references already has 2 other same-day PASS-logged, non-exempt siblings
# on this repo, the 3rd+ is BLOCKED — every referenced parent is checked
# (never just the first), mirroring the chain-depth cap's own "every
# candidate is tried" discipline.
#
# STREAM ROUTING GATE (#390, a real incident — odoo-erp#3549/#3651: a
# sub-dev stream box (david2) mislabeled its OWN self-authored tickets with
# a FOREIGN stream's label (`stream:david`), landing them in the wrong
# stream's /goal slice before anyone noticed. Every odoo-erp stream shares
# ONE GitHub App token, so GitHub-side author detection is blind here — the
# LINUX USER running this hook (known, reliable) is the only place this can
# be caught, at filing time). Scoped narrowly to the exact reported failure:
#   - Only engages on a STREAM-AWARE repo (>=1 real `stream:*` label exists,
#     `gh label list`, degrading to "cannot verify" on any lookup failure —
#     never blocks on its own, same bias as the near-dup check above).
#   - Only engages when the LINUX USER running this hook is itself a known
#     sub-dev stream account (`airuleset.resolve_authority(cwd) != "full"`,
#     imported directly from the sibling airuleset.py — single source of
#     truth for AUTHORITY_BY_USER, never a duplicated user list, matching
#     the issue's own "rovnako, ako to už robí airuleset.py authority"). A
#     full-authority filer (the maintainer/gatekeeper doing triage — airuleset
#     #827: an explicit FULL_AUTHORITY_USERS account; an unmapped box now fails
#     safe to fork-no-merge and IS gated) has NO "own" stream to compare against
#     and is trusted to route deliberately — ordinary core-ticket filing
#     carries NO stream label by design (`_core_search_excl()`'s whole
#     mechanism is "absence of a stream label = core"), so gating every
#     filer here would be a wide-blast-radius regression on the DOMINANT
#     filing pattern, not a narrow fix for the reported incident.
#   - When both engage: the filing must carry an explicit `stream:<x>`
#     label (`-l`/`--label`, comma-list aware, any repetition) — none at
#     all -> BLOCKED `missing-stream-label`. One or more IS present but
#     NONE matches the filer's OWN (`stream:<their-linux-username>` —
#     every current AUTHORITY_BY_USER key already doubles as its own
#     stream-label suffix, verified across all 15 entries) -> a
#     `Stream-routing: <reason>` body line is required (same logged-claim
#     shape as `Scope-gate:`/`Dedup-checked:` — an affirmative, auditable
#     claim; does not verify truth, matches this hook's own documented
#     limit) -> missing -> BLOCKED `stream-routing-unjustified`.
#   - `gh api .../issues POST` labeling is OUT OF SCOPE — an accepted
#     residual, same shape as the near-dup check's own `--limit 200`
#     residual above (`gh issue create` is this repo's dominant,
#     documented filing recipe; API-POST labeling is rare and awkward).
#
# Both new caps, and the near-dup check, are computed against a single
# TARGET REPO resolved consistently per filing (explicit `-R`/`--repo`, else
# parsed from a `gh api repos/<owner>/<repo>/issues` path, else the cwd's own
# git remote) — the SAME value is what gets logged, so a future invocation's
# cap count is never checked against a different repo than the one a past
# filing was actually logged under. The stream-routing gate reuses the SAME
# `target_repo` value for its own `gh label list` lookup.
#
# Both new caps are logged through the SAME scope-gate.log mechanism
# (extended with `parents=`/`dedup=` fields, never a second log) and honour
# the SAME `# airuleset:scope-gate-ok <reason>` bypass as every other gate
# here — no new bypass syntax.
#
# PHANTOM-PASS FIX (#329 adversarial review, CRITICAL): a Bash command that
# blocks on ANY segment blocks the WHOLE tool call — nothing in it actually
# runs, including a sibling `gh issue create` that this hook itself
# classified PASS. Logging that sibling as `verdict=PASS` would silently
# burn cap budget for an issue that was never filed, and a batch retried
# after fixing the one blocked item would then find EVERY item capped
# despite zero real filings. So: once any segment in a command BLOCKS, every
# other segment's PASS is logged as `verdict=NOTFILED` instead (still
# visible for audit, but `_log_pass_count` only ever counts an EXACT
# `verdict=PASS` token, so a NOTFILED entry never consumes cap budget).
#
# BODY RESOLUTION (same shape as block-gh-invalid-json-flag.sh's heredoc
# handling, extended to CAPTURE the body instead of discarding it):
#   - `-F <file>` / `--body-file <file>` where <file> was just written by a
#     `cat > <file> <<'EOF' ... EOF` earlier in the SAME command — the
#     heredoc body IS the issue body (the standard gh-cli-recipes.md pattern).
#   - `-F -` / `--body-file -` attached directly to the gh invocation's own
#     heredoc (`gh issue create -F - <<'EOF' ... EOF`).
#   - `-F <file>` referencing a file NOT written in this command — read it
#     from disk (a pre-existing body file). Unreadable -> unresolved.
#   - `--body "<literal>"` / `--body=<literal>` inline text.
#   - Nothing resolvable -> unresolved. An unresolved body BLOCKS
#     conservatively (get this wrong toward strict, per no-dropped-work.md:
#     a false block costs one line, a false pass costs nothing).
#
# Every PASS, NOTFILED and BLOCK is logged to ~/.claude/scope-gate.log, in a
# field order that puts every COUNTING-RELEVANT field (verdict, repo,
# criterion, session, parents) BEFORE the two free-text, attacker-influenced
# fields (title, dedup) — so a `\bfield=(\S+)` search for any counting field
# always finds the REAL one first, never a decoy the same value could spell
# out inside a crafted title. The log is what keeps a false criterion
# auditable AND is what the daily/width caps count against.
#
# Bypass (rare, logged): `# airuleset:scope-gate-ok <reason>` anywhere in the
# command text — bypasses EVERY gate in this hook, not just Scope-gate.
#
# KNOWN LIMIT: cannot verify the criterion is TRUE, only that it was
# affirmatively claimed. See modules/quality/no-dropped-work.md — filing
# must never become impossible; this hook's job is to stop the SILENT
# default, not to add a second judgment layer on top of the model's own.
#
# KNOWN RESIDUAL (accepted, documented rather than chased — #329 review): the
# `--limit 200` on the near-dup `gh issue list` call truncates the comparison
# set to the 200 most recently CREATED open issues (gh's default sort), so a
# genuine duplicate of a much older issue on a repo with 200+ open issues can
# be missed. This degrades toward ALLOWING, never toward a false block, which
# matches this hook's own stated bias throughout.

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$CMD" ] && exit 0

# #842 req 1 -- a WORKTREE WORKER (subagent, payload `.agent_id` — the SAME
# subagent signal block-subagent-bg-ci-poll.sh / subagent-stop-check-*.sh, #496,
# already use) may NOT file a GitHub issue: it FIXES what it finds in-lane and
# returns a `followup_candidates:` line for anything genuinely out of scope; the
# SUPERVISOR decides + files. Placed BEFORE the pre-filter below (so a `gh api …
# graphql … createIssue` mutation, which carries no lowercase `issues` substring
# and would `exit 0` at the pre-filter, is still caught — the #842-review 🔴) and
# BEFORE the `airuleset:scope-gate-ok` bypass (so a worker can NEVER self-exempt
# by appending that marker). Fires ONLY on a genuine CREATE shape, NOT on a GET
# read (`gh api …/issues/N/comments` — reading a design comment), a `grep`, or a
# doc mention (the #842-review 🟡 false-block of legit worker reads):
#   - `gh issue create`
#   - a `gh api …/issues …` WRITE (an explicit/implicit POST or a field/input
#     flag; a bare GET has none of these)
#   - a `gh api … graphql … createIssue` mutation
# Each check is an `if` CONDITION so `set -e` is suspended for the grep (a
# no-match exit 1 never aborts the hook). Accepted residual: a worker writing a
# DOC/test file via a bash heredoc that literally contains `gh issue create`
# false-blocks (workers edit via Write/Edit, not bash heredocs — rare).
AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || echo "")
if [ -n "$AGENT_ID" ]; then
    _wf=0
    if printf '%s' "$CMD" | grep -qE 'gh[[:space:]]+issue[[:space:]]+create'; then _wf=1; fi
    if [ "$_wf" = 0 ] && printf '%s' "$CMD" | grep -qE 'gh[[:space:]]+api'; then
        if printf '%s' "$CMD" | grep -q 'createIssue'; then _wf=1; fi
        if [ "$_wf" = 0 ] && printf '%s' "$CMD" | grep -q 'issues' \
           && printf '%s' "$CMD" | grep -qE '(-X[[:space:]]*POST|--method[[:space:]]+POST|-XPOST|(^|[[:space:]])-f([[:space:]]|$)|(^|[[:space:]])-F([[:space:]]|$)|--field|--raw-field|--input)'; then
            _wf=1
        fi
    fi
    if [ "$_wf" = 1 ]; then
        cat >&2 <<'MSG'
BLOCKED: you are a worktree WORKER (subagent) — you may NOT `gh issue create`
(nor a `gh api …/issues` POST, nor a `gh api graphql … createIssue`) a new
GitHub issue. A worker FIXES what it finds in-lane, in THIS branch — a small
adjacent problem, a flaky test, a review finding all land here, not a new ticket.

Anything you genuinely believe is out of scope (>300 LoC / schema / API-break /
security-boundary / cross-cutting / needs-user-decision) goes in your RETURN
block as a `followup_candidates:` line (title + which criterion it clears + est.
LoC) — the SUPERVISOR decides and files it, never the worker (#842). A return
containing a `filed:` line is REJECTED at integration and the lane is sent back.
MSG
        exit 2
    fi
fi

# Cheap pre-filter: only classify commands that could plausibly contain a
# gh issue-creation call at all. (A worker's genuine filing already returned
# above; this gates the MAIN-session classifier.)
case "$CMD" in
    *"issue create"*) ;;
    *"gh api"*"issues"*) ;;
    *) exit 0 ;;
esac

# Deliberate bypass for a genuine edge. (A worker's genuine create already
# returned above, so this can never self-exempt a worker filing.)
case "$CMD" in *"airuleset:scope-gate-ok"*) exit 0 ;; esac

LOG="$HOME/.claude/scope-gate.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

# #390 -- this hook's own checkout root (the airuleset repo containing this
# script's sibling airuleset.py), resolved the same way hooks/lib-*.sh
# source their own libraries elsewhere in this repo: `${BASH_SOURCE[0]}`'s
# directory, one level up. Passed into the python heredoc below so the new
# stream-routing gate can import airuleset.py directly (single source of
# truth for AUTHORITY_BY_USER) without a duplicated user list. Empty on any
# resolution failure -- the gate degrades to "cannot verify", never blocks.
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || HOOK_DIR=""
REPO_ROOT_DIR=""
[ -n "$HOOK_DIR" ] && REPO_ROOT_DIR="$(dirname "$HOOK_DIR")"

# #842 -- the UNATTENDED/away signal, from the SHARED presence helper (the same
# 900s marker read block-main-implementation.sh uses). Only the UNATTENDED path
# engages the new presence-gate / dismissal-word / net-drain-ratchet gates; an
# ATTENDED (owner-present) filing keeps the pre-#842 behaviour exactly. Fail-OPEN
# on an unmeasurable presence state (missing helper, absent marker) -> PRESENT,
# so a /tmp cleanup or a deploy gap never manufactures an unattended BLOCK.
UNATTENDED=0
if [ -n "$HOOK_DIR" ] && [ -r "$HOOK_DIR/lib-presence.sh" ]; then
    . "$HOOK_DIR/lib-presence.sh"
    if type airuleset_presence_is_away >/dev/null 2>&1; then
        airuleset_presence_is_away "$SID" && UNATTENDED=1
    fi
fi

# python3 - "$CMD" <<'PYEOF' (argv, never a pipe into the heredoc's own
# stdin — see the repo's own #96 gotcha).
RC=0
OUT=$(python3 - "$CMD" "$SID" "$(pwd)" "$LOG" "$REPO_ROOT_DIR" "$UNATTENDED" <<'PYEOF' 2>/dev/null
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime

cmd = sys.argv[1]
sid = sys.argv[2]
cwd = sys.argv[3]
log_path = sys.argv[4]
repo_dir = sys.argv[5] if len(sys.argv) > 5 else ""
# #842 -- UNATTENDED ("1") vs attended (anything else). Only the unattended
# path engages the presence-gate / dismissal-word / net-drain-ratchet gates.
unattended = (sys.argv[6] == "1") if len(sys.argv) > 6 else False

# #842-review 🟡 -- put the hook's own checkout root on sys.path ONCE, up front,
# so `import ratchet_counts` (the net-drain ratchet) and `import airuleset`
# resolve on EVERY path. Previously sys.path gained `repo_dir` only as a side
# effect of `_filer_authority_and_own_stream`, which returns early for a `gh api`
# filing before that insert ran -- so the ratchet import raised for an api
# filing from any managed-repo cwd and fell to the fail-safe permanent BLOCK.
if repo_dir and repo_dir not in sys.path:
    sys.path.insert(0, repo_dir)

ALLOWED = {
    ">300-loc", "schema-migration", "api-break", "security-boundary",
    "cross-cutting", "needs-user-decision", "planned-work", "user-request",
}

# #329 -- these two criteria are the only ones exempt from the NEW soft caps
# below (daily filing cap, chain-width cap) -- they never COUNT toward
# either cap and never GET capped by either. A user directive or a
# converged-plan decomposition must always be able to file
# (durable-decisions-to-tickets.md); a discovered review-finding/cleanup
# must not.
EXEMPT_FROM_CAP = {"planned-work", "user-request"}

# #329 -- soft per-day, per-repo cap on NON-EXEMPT agent-authored filings.
# Measured worst days on the ticket's own real corpus: 19/16/14 filings in
# ONE day on ONE repo; the user's own stated sane ceiling for the WHOLE
# project's lifetime backlog was ~40 tickets. 8/day/repo sits comfortably
# above what a genuinely active /autopilot day of bundled batches should
# need (a handful of real out-of-scope discoveries across several PRs)
# while being decisively below every measured storm day.
DAILY_CAP = 8

# #329 -- chain-WIDTH cap (siblings off ONE parent, same day, same repo).
# The real corpus example is 4 siblings off one parent (#3224) in one
# burst; 1-2 genuinely distinct findings off a single review is plausible
# (e.g. a security finding + a schema finding from the same PR review), a
# 3rd is the storm signature -- so 2 pass, the 3rd blocks.
CHAIN_WIDTH_CAP = 2

# #842 req 4 -- dismissal words in a NEW issue body from an UNATTENDED session.
# `test-strictness.md` + `no-dropped-work.md` already ban these as dismissals of
# a test failure; a ticket that merely SAYS "the test is flaky" / "pre-existing
# failure" is the same dismissal in durable form -- the loop must FIX the test,
# not file its excuse. Word-boundary-ish, case-insensitive. `out of scope` is
# the weakest signal (it is also a legitimate scope-gate justification), so a
# discovery filing that uses the literal phrase in prose is OVER-blocked here --
# an accepted false-block bias (the remedy: name the SPECIFIC criterion instead
# of the vague phrase), consistent with this hook's documented "get it wrong
# toward strict, a false block costs one line" stance, and bounded to the
# unattended path only (an attended owner filing is never dismissal-blocked).
DISMISSAL_WORD_RE = re.compile(
    r"\bflak(?:e|es|y|iness)\b|\bpre-?existing\b|\bintermittent(?:ly)?\b|"
    r"\bout\s+of\s+scope\b",
    re.IGNORECASE)


def _dismissal_word(body):
    """The first dismissal word/phrase found in `body`, or None."""
    if not body:
        return None
    m = DISMISSAL_WORD_RE.search(body)
    return m.group(0).strip() if m else None


def _ratchet_should_block(target_repo, cwd):
    """#842 req 2 -- True when the per-repo net-drain ratchet must BLOCK an
    UNATTENDED non-exempt discovery filing on `target_repo`: the repo is NOT
    strictly draining today (`created_today >= closed_today`). Fail-SAFE: any
    inability to compute the counts -- a gh error, or a ratchet_counts import
    failure (`repo_dir` is now on sys.path from the top of the heredoc, so this
    import works on every path; a genuine failure means a broken/absent leaf, a
    real deploy fault) -- returns True (BLOCK), never a wrong ALLOW (#842 (d))."""
    try:
        import ratchet_counts as _rc
    except Exception:
        return True
    got = _rc.cached_counts(target_repo, cwd)
    if got is None:
        return True
    created, closed, _day = got
    return _rc.ratchet_blocks(created, closed)


def _ratchet_bump(target_repo):
    """Record a ratchet-PASS forward (increment the cached created_today), so a
    burst of unattended filings inside one TTL window does not all pass on the
    same stale count. Best-effort (returns False on any failure) -- a bump
    failure never blocks a filing that already PASSED."""
    try:
        import ratchet_counts as _rc
        _rc.bump_created(target_repo)
        return True
    except Exception:
        return False


HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\s*$")
CATFILE_RE = re.compile(r'^\s*cat\s*>>?\s*([^\s<>&;|]+)')

lines = cmd.split("\n")
n = len(lines)

# ---- pass 1: locate every heredoc, capture its body, and note whether its
# trigger line looks like `cat > FILE <<DELIM` (file-attached) or is bare
# (direct-attached to whatever command precedes it on that same line).
file_bodies = {}     # filename -> body text
direct_bodies = {}   # delim -> body text (heredoc with no `cat >` in front)
skeleton_lines = list(lines)  # heredoc BODY lines blanked out below

i = 0
while i < n:
    line = lines[i]
    mm = HEREDOC_RE.search(line.rstrip())
    if not mm:
        i += 1
        continue
    delim = mm.group(2)
    strip_leading = "<<-" in line
    body = []
    j = i + 1
    while j < n:
        check = lines[j].lstrip("\t") if strip_leading else lines[j]
        if check == delim:
            break
        body.append(lines[j])
        j += 1
    body_text = "\n".join(body)
    fm = CATFILE_RE.match(line)
    if fm:
        file_bodies[fm.group(1)] = body_text
    else:
        direct_bodies[delim] = body_text
    # blank the body span out of the skeleton (keep the trigger + closing
    # lines so segment classification still sees the command + the `<<DELIM`
    # marker for direct-attach resolution).
    for k in range(i + 1, min(j + 1, n)):
        skeleton_lines[k] = ""
    i = j + 1

skeleton = "\n".join(skeleton_lines)

# ---- pass 2: segment the skeleton exactly like block-gh-invalid-json-flag.sh
# (same shape, deliberately reused rather than reinvented — see that hook's
# #85 note).
ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")


def split_top_level(text):
    """Split on &&/||/;/&/|/newline, but QUOTE-AWARE — a `;`/`|` sitting
    inside a real issue TITLE (free text; real corpus example: camera-box
    #827's title literally contains one) must never be treated as a command
    separator. block-gh-invalid-json-flag.sh's plain regex split accepts
    this as a known limitation for ITS narrower job; this hook's whole
    purpose is reading a real title/body, so the same shortcut would
    silently false-block real, legitimate filings."""
    segs, buf, i, n, quote = [], [], 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(text[i + 1])
            i += 2
            continue
        if text[i:i + 2] in ("&&", "||"):
            segs.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "&", "|", "\n"):
            segs.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segs.append("".join(buf))
    return segs


def tokens_of(segment):
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


def strip_prefix(tk):
    idx = 0
    while idx < len(tk):
        t = tk[idx]
        if t in ("sudo", "env") or t in LOOP_BODY_KEYWORDS or ASSIGN_RE.match(t):
            idx += 1
            continue
        break
    return tk[idx:]


def flag_value(tk, names):
    """Return the value for --flag VAL / --flag=VAL / -F VAL, or None."""
    for idx, t in enumerate(tk):
        for name in names:
            if t == name and idx + 1 < len(tk):
                return tk[idx + 1]
            if t.startswith(name + "="):
                return t[len(name) + 1:]
    return None


def is_issue_create(tk):
    return len(tk) >= 3 and tk[0] == "gh" and tk[1] == "issue" and tk[2] == "create"


def is_api_issues_post(tk):
    if not tk or tk[0] != "gh" or "api" not in tk[:2]:
        return False
    has_issues = any("issues" in t for t in tk)
    has_post = False
    for idx, t in enumerate(tk):
        if t in ("-X", "--method") and idx + 1 < len(tk) and tk[idx + 1].upper() == "POST":
            has_post = True
        if re.match(r'^-X\s*POST$', t, re.I) or t.upper() in ("-XPOST",):
            has_post = True
    return has_issues and has_post


def _cd_target(tk):
    """The directory a leading `cd` segment changes into, or None when the
    target cannot be known statically (#483) -- no argument (bare `cd` ->
    $HOME), or an unexpandable $VAR / ~ / glob / command-substitution target
    that only the runtime shell could resolve. `tk` is a tokenized segment
    whose first token is already known to be `cd`."""
    target = None
    for t in tk[1:]:
        if t == "--":
            continue
        if t.startswith("-"):
            continue  # cd -L / -P / -e / -@ options carry no path
        target = t
        break
    if target is None:
        return None
    # Anything the shell would expand at runtime is not statically knowable.
    if any(ch in target for ch in "$~*?`"):
        return None
    return target


def _apply_cd(base, tk):
    """Fold one `cd <dir>` segment into the running effective cwd (#483).
    Returns the new effective cwd, or None once it becomes unknowable -- a
    later relative -F then degrades to the explicit not-readable message
    (option 2) rather than a WRONG resolution or a fail-open pass. An
    absolute target replaces the cwd outright; a relative one is
    normpath-joined onto the current effective cwd, which STARTS as the
    hook's own cwd (== the shell's own starting cwd), so a no-`cd` command
    resolves exactly as before this fix."""
    target = _cd_target(tk)
    if target is None:
        return None
    if os.path.isabs(target):
        return os.path.normpath(target)
    if base is None:
        return None
    return os.path.normpath(os.path.join(base, target))


def _unreadable_body_err(bf, eff_cwd):
    """#483 -- an actionable per-item block reason for a `-F <file>` disk
    path that could not be read, replacing the opaque `-> none`. Names the
    file, the effective cwd it was resolved against (or that it was
    unresolvable), and the fix (an absolute -F path). One line -- it crosses
    the tab-separated hand-off to bash (see _clean_field)."""
    if os.path.isabs(bf):
        return ("body file '%s' not readable -- path is missing or unreadable "
                "(check the absolute -F path)" % bf)
    where = ("'%s'" % eff_cwd) if eff_cwd is not None else \
        "an unresolvable 'cd' target ($VAR/~/glob)"
    return ("body file '%s' not readable -- a relative -F path resolved "
            "against %s; use an absolute -F path" % (bf, where))


def resolve_body(tk, seg_line, is_api, eff_cwd):
    """Returns (body_text_or_None, err_or_None). `err` is set ONLY when a
    `-F <file>` DISK path was present but could not be read (#483) -- it
    carries the explicit, actionable block reason instead of the old opaque
    `none`. `eff_cwd` (#483) is the command's effective cwd after any
    leading `cd <dir>`; a relative -F is resolved against it, NOT the hook's
    own process cwd (which a `cd` prefix would otherwise make wrong -- the
    gk@odoo-erp incident)."""
    if is_api:
        # `gh api` overloads -f/-F for arbitrary key=value FIELDS (typed
        # vs raw), unlike `gh issue create`'s -F <FILE PATH>. Find a
        # `body=<value>` field among -f/-F/--field/--raw-field tokens.
        for idx, t in enumerate(tk):
            if t in ("-f", "-F", "--field", "--raw-field") and idx + 1 < len(tk):
                v = tk[idx + 1]
                if v.startswith("body="):
                    return v[len("body="):], None
            elif t.startswith(("-f", "-F", "--field=", "--raw-field=")) and "=" in t:
                # -fbody=x / --field=body=x shapes — best-effort only.
                pass
        return None, None
    bf = flag_value(tk, ("-F", "--body-file"))
    if bf is not None:
        if bf == "-":
            m = HEREDOC_RE.search(seg_line.rstrip())
            if m and m.group(2) in direct_bodies:
                return direct_bodies[m.group(2)], None
            return None, None
        if bf in file_bodies:
            return file_bodies[bf], None
        if os.path.isabs(bf):
            path = bf
        elif eff_cwd is not None:
            path = os.path.join(eff_cwd, bf)
        else:
            path = None  # relative -F under an unresolvable `cd` target
        if path is not None:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read(), None
            except OSError:
                pass
        return None, _unreadable_body_err(bf, eff_cwd)
    inline = flag_value(tk, ("--body",))
    if inline is not None:
        return inline, None
    return None, None


CRITERION_RE = re.compile(r'(?m)^\s*Scope-gate:\s*(\S+)')

# #329 -- the dedup-gate's structural half: a `Dedup-checked: <query>` line
# proves the agent deliberately searched before filing, the SAME
# logged-claim shape `Scope-gate:` already uses (cheap, no network, does
# not verify truth -- only that it was affirmatively claimed).
DEDUP_RE = re.compile(r'(?m)^\s*Dedup-checked:\s*(\S.*)$')

# #311 point 3 -- Scope-gate verifiability, mechanical only where trivially
# checkable. A body claiming `>300-loc` that ALSO states its own bare
# number next to "loc"/"lines" is a self-contradiction when that number is
# <=300 -- the exact "violation CONFESSED in the issue body itself" shape
# #137 already established as this hook's founding evidence. No number
# stated -> unaffected (cannot verify, trust the claim, matching the
# hook's own documented limit).
LOC_NUM_RE = re.compile(r'(\d+)\s*(?:loc|lines?)\b', re.I)

# #311 -- chain-depth cap. A review-finding follow-up NAMES its own PARENT
# issue as a "follow-up" -- confirmed to be the naming convention every real
# chain member in the odoo-erp scope-gate.log corpus independently converged
# on ("(#3224 follow-up)", "cross-screen half of #3224 is still open"), so
# detecting THIS phrasing needs no new discipline for workers to adopt, only
# a mechanical check on what they already write. The window is 40 chars and
# the wording accepts the plural ("follow-ups") -- both widened after
# adversarial-review finding F7 (this ticket's own review, TRIGGERED live)
# found the original 20-char singular-only pattern missed real phrasings.
FOLLOWUP_RE = re.compile(
    r'#(\d+)[^\n]{0,40}\bfollow[-\s]?ups?\b|\bfollow[-\s]?ups?\b[^\n]{0,40}#(\d+)',
    re.I)


def _chain_parents(text):
    """Every issue number referenced near "follow-up"/"follow-ups" wording
    in `text`, in order of appearance, deduplicated. A `.search()`-only
    match takes only the FIRST such reference, which lets an earlier decoy
    (e.g. a title mentioning one ticket while the body's real parent
    reference comes later) hide the genuine parent (#311 adversarial-
    review finding F7, TRIGGERED live) -- every candidate is tried."""
    seen = []
    for m in FOLLOWUP_RE.finditer(text or ""):
        ref = m.group(1) or m.group(2)
        if ref not in seen:
            seen.append(ref)
    return seen


def _chain_parent(text, own_number=None):
    """The first candidate parent reference in `text`, honouring
    `own_number` when given: `own_number=None` (resolving THIS filing's
    own parent) accepts any reference verbatim. `own_number=<N>` (checking
    whether a candidate PARENT's own text makes IT a follow-up too)
    rejects a FORWARD reference (`ref >= own_number`) -- an umbrella/root
    ticket's body naturally LINKS the follow-ups it spawned ("Spawned
    work: #3250 follow-up, #3251 follow-up"), which is the root citing
    its own CHILDREN, never proof the root itself is a follow-up of
    something. Real GitHub issue numbers only ever increase over time, so
    a genuine ANCESTOR reference is always a LOWER number than its child
    (#311 adversarial-review finding F2, TRIGGERED live: a root ticket
    linking its own spawned children was wrongly read as itself being a
    depth-2 follow-up)."""
    for ref in _chain_parents(text):
        if own_number is not None:
            try:
                if int(ref) >= int(own_number):
                    continue
            except ValueError:
                continue
        return ref
    return None


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


# #329 adversarial review -- a character-level SequenceMatcher ratio CANNOT
# separate this fleet's real duplicates from its real distinct tickets:
# measured, the true-duplicate pair in this file's own test corpus ("Retry
# queue drops messages under load" vs "... under heavy load") scores 0.925,
# while genuinely DISTINCT real title pairs from this fleet's own naming
# conventions score EQUAL OR HIGHER -- test_foo.py/test_bar.py 0.929, -R/-C
# 0.978, dev1/dev2 0.978, montalu2/montalu3 0.980, cam4/cam5 0.983, job
# 14/job 15 0.976 -- and camera-box/odoo-erp file per-box/per-job/
# per-account tickets as their DOMINANT title shape, not an edge case.
#
# A TOKEN-SET Jaccard measure alone still isn't enough: two REALISTIC full
# titles that differ ONLY in a box/job/account NUMBER but share every other
# word ("cam4 restart loop blocks the E2E preflight" vs the identical
# sentence for cam5) keep 6 of 8 tokens (Jaccard 0.75) -- ABOVE the
# threshold below, and a real, live false-positive this hook's own test
# suite caught (#329 adversarial review, TRIGGERED live during test
# authoring). The fix: any token containing a DIGIT is an "identifying"
# token (a specific box/job/account/PR number) -- if the SET of identifying
# tokens differs AT ALL between the two titles, they can never be a
# duplicate, REGARDLESS of the overall Jaccard ratio, since they concretely
# name different targets. This is checked BEFORE the ratio, so it also
# correctly handles a bare short comparison (cam4 vs cam5 alone: identifying
# sets {cam4} != {cam5}, refused outright) and leaves the TRUE-duplicate
# pair above untouched (neither title has ANY digit-bearing token, so both
# identifying sets are empty and trivially equal) -- it still scores
# 6/7 ~= 0.857 Jaccard, comfortably above the 0.7 threshold.
TOKEN_RE = re.compile(r'[a-z0-9]+', re.I)
IDENTIFYING_TOKEN_RE = re.compile(r'\d')
TOKEN_JACCARD_THRESHOLD = 0.7


def _tokenize(text):
    return set(t.lower() for t in TOKEN_RE.findall(text or ""))


def _identifying_tokens(tokens):
    return {t for t in tokens if IDENTIFYING_TOKEN_RE.search(t)}


def _title_jaccard(a, b):
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    if _identifying_tokens(ta) != _identifying_tokens(tb):
        return 0.0
    union = len(ta | tb)
    if union == 0:
        return 0.0
    return len(ta & tb) / union


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


def _clean_field(s):
    """Collapse embedded tabs/newlines to single spaces before this value
    crosses the tab-separated hand-off to bash (or gets embedded in the
    persistent log) -- an embedded tab/newline in a TITLE would otherwise
    shift every field after it (#329 adversarial-review finding: a title
    containing a real tab/newline corrupted `criterion=`/`session=`/
    `parents=` in the tab-separated OUT channel bash reads).

    #802 adversarial-review 🔵: `\\s` does NOT cover the non-whitespace C0
    control bytes (ESC `\\x1b`, etc.) or DEL `\\x7f`, so an attacker-
    influenced field (a crafted TITLE or `-F` token) carrying a raw
    terminal-escape sequence would reach the user's stderr SUMMARY and the
    log verbatim. Neutralise those to a space FIRST (only the ASCII control
    range excluding `\\t\\n\\r\\v\\f`, which the `\\s+` collapse already
    handles -- so UTF-8 multibyte bytes are never touched), then collapse.
    This hardens EVERY carrier centrally (title, dedup, body_err, crit)."""
    s = re.sub(r'[\x00-\x08\x0e-\x1f\x7f]', ' ', (s or ''))
    return re.sub(r'\s+', ' ', s).strip()


def _no_field_decoy(s):
    """#802 adversarial-review 🟡 -- neutralise `=` to `:` in an
    ATTACKER-influenced substring that becomes part of a BLOCK line's
    free-text `criterion=` field, so it can never spell a `<countingfield>=`
    decoy token (`parents=999`, `session=x`) that a later `\\bfield=(\\S+)`
    first-match would pick up ahead of the real field. Applied ONLY to
    attacker-derived text (a crit value, a `-F` token in body_err), NEVER to
    the author-controlled literal hints whose fixed `body=` is legitimate gh
    syntax and is not a counting-field name. Harmless today (only
    `verdict=PASS` lines are ever counted, and this runs on BLOCK lines) --
    pure defence-in-depth against a future counting change over non-PASS
    lines. `s` is assumed already `_clean_field`-ed."""
    return (s or "").replace("=", ":")


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


API_ISSUES_REPO_RE = re.compile(
    r'^(?:https?://api\.github\.com/)?repos/([^/]+/[^/]+)/issues\b')


def _target_repo_for_segment(tk, api_call, cwd_repo):
    """The repo THIS ONE filing actually targets: explicit -R/--repo, else
    (for `gh api`) parsed from the `repos/<owner>/<repo>/issues` path
    token, else the cwd-derived repo. Used consistently for the near-dup
    fetch, BOTH caps, AND the logged `repo=` field, so a future
    invocation's cap count is checked against the SAME repo a past
    filing was actually logged under (#329 adversarial-review finding:
    the caps were keyed on the cwd-derived repo while the near-dup check
    used the filing's own -R target -- a cross-repo filing was capped
    against the wrong bucket, and a `gh api` filing had no near-dup
    protection at all since its target repo was never resolved)."""
    explicit = flag_value(tk, ("-R", "--repo"))
    if explicit:
        return explicit
    if api_call:
        for t in tk:
            m = API_ISSUES_REPO_RE.match(t)
            if m:
                return m.group(1)
    return cwd_repo


# #390 -- STREAM ROUTING GATE. See this file's own header comment for the
# full design; the code below implements exactly what it describes.
STREAM_LABEL_RE = re.compile(r'^stream:([A-Za-z0-9_-]+)$', re.I)
STREAM_ROUTING_RE = re.compile(r'(?m)^\s*Stream-routing:\s*(\S.*)$')

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


def _explicit_stream_labels(tk, is_api):
    """Every `stream:<x>` value named via -l/--label in THIS segment's own
    tokens, lowercase, deduped, in order of appearance -- comma-list
    aware (`-l bug,stream:david2`), any repetition (`-l a -l b`), and
    every genuine `gh`-accepted spelling of the short flag: separate-token
    (`-l stream:x`), ATTACHED (`-lstream:x`), and attached-with-equals
    (`-l=stream:x`) -- #390 adversarial-review MAJOR-2, verified live
    against the real `gh` binary (a truly unknown flag is rejected by gh
    itself with "unknown shorthand flag", distinct from these three
    accepted forms). A compliant filer using the attached spelling must
    never be FALSE-BLOCKED for a label the hook simply failed to see --
    this hook's own stated bias is to degrade toward allowing, never
    toward a false block. Only `gh issue create` is scanned -- `gh api
    ... POST` labeling is deliberately out of scope (see this file's
    header)."""
    if is_api:
        return []
    found = []
    for idx, t in enumerate(tk):
        val = None
        if t in ("-l", "--label") and idx + 1 < len(tk):
            val = tk[idx + 1]
        elif t.startswith("--label="):
            val = t[len("--label="):]
        elif t.startswith("-l") and len(t) > 2 and not t.startswith("--"):
            val = t[2:]
            if val.startswith("="):
                val = val[1:]
        if val is None:
            continue
        for piece in val.split(","):
            piece = piece.strip().lower()
            if STREAM_LABEL_RE.match(piece) and piece not in found:
                found.append(piece)
    return found


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
    cross-stream-relevance tag, not a mis-file."""
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
        return None
    return "stream-routing-unjustified"


results = []  # (verdict, title, criterion_or_none, parents_str, target_repo, dedup_claim)

# #329 -- resolve the cwd-derived repo BEFORE the per-segment loop (moved
# up from the tail of the script) so the dedup/cap checks can use it as
# their FALLBACK; the final print loop below uses each result's own
# per-segment target_repo instead of this shared value directly.
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

# #329 -- local running counters for a BATCH filing several issues in one
# Bash call, before anything is written to the log. Keyed by target_repo
# (a batch can target more than one repo) and, for the width cap, by
# (target_repo, parent).
_local_day_count = {}      # target_repo -> count
_local_parent_count = {}   # (target_repo, parent) -> count
_batch_titles_by_repo = {}  # target_repo -> [title, ...] already PASSed

# #483 -- the effective cwd a relative `-F` body path resolves against.
# Starts as the hook's own cwd (== the shell's own starting cwd) and is
# folded forward by every leading `cd <dir>` segment as the command is
# walked in order, so `cd /dir && gh issue create ... -F body.md` reads
# body.md from /dir, not from the hook's cwd.
effective_cwd = cwd

for seg in split_top_level(skeleton):
    if not seg.strip():
        continue
    tk = strip_prefix(tokens_of(seg))
    # #483 -- a `cd` segment changes where a later relative `-F` lives; fold
    # it into effective_cwd (unknowable target -> None, degrades to the
    # explicit not-readable reason) and move on -- it is never a filing.
    if tk and tk[0] == "cd":
        effective_cwd = _apply_cd(effective_cwd, tk)
        continue
    api_call = is_api_issues_post(tk)
    if not (is_issue_create(tk) or api_call):
        continue
    title = flag_value(tk, ("-t", "--title"))
    if title is None and api_call:
        for idx, t in enumerate(tk):
            if t in ("-f", "-F", "--field", "--raw-field") and idx + 1 < len(tk) \
                    and tk[idx + 1].startswith("title="):
                title = tk[idx + 1][len("title="):]
                break
    title = title or "(no title)"
    body, body_err = resolve_body(tk, seg, api_call, effective_cwd)
    crit = None
    if body:
        m = CRITERION_RE.search(body)
        if m:
            crit = m.group(1)

    repo_flag = flag_value(tk, ("-R", "--repo"))
    target_repo = _target_repo_for_segment(tk, api_call, cwd_repo)

    # #311 -- a review-finding follow-up whose own PARENT is ITSELF such a
    # follow-up is a depth-2 review-finding chain -- a self-reinforcing
    # sequence the follow-up gate's PER-ISSUE criterion cannot see, since
    # each individual hop can honestly claim its own criterion. Cheap text
    # match first; a bounded `gh` call only fires once a candidate parent
    # is actually named -- EVERY candidate is tried (finding F7: a decoy
    # reference earlier in the text must not hide the real one), each
    # checked with the backward-reference filter (finding F2: a root
    # ticket linking its own spawned children is not itself chained), in
    # the filing's OWN explicit repo when one is given (finding F8).
    parents = _chain_parents((title or "") + "\n" + (body or ""))
    parents_str = ",".join(parents) if parents else "none"
    chain_capped = False
    for parent in parents:
        parent_text = _gh_view_text(parent, cwd, repo=repo_flag)
        if parent_text is not None and _chain_parent(parent_text, own_number=parent):
            chain_capped = True
            break

    # #311 point 3 -- a `>300-loc` claim whose own body confesses a
    # <=300 number next to "loc"/"lines" is self-contradicting; checked
    # ONLY for this one criterion, ONLY when a number is actually stated.
    # EVERY stated number must clear 300, not just the first one found
    # (finding F1, TRIGGERED live: a body honestly quoting the follow-up
    # gate's OWN threshold text before stating its real, genuinely-large
    # count -- "under ~100 LoC ... roughly 620 LoC across 5 modules" --
    # matched the FIRST number and false-blocked exactly the author being
    # most honest about clearing the gate).
    loc_mismatch = False
    if crit and crit.lower() == ">300-loc" and body:
        nums = [int(x) for x in LOC_NUM_RE.findall(body)]
        if nums and max(nums) <= 300:
            loc_mismatch = True

    # #390 -- computed unconditionally per segment, same tier as
    # chain_capped/loc_mismatch above (a routing-correctness question, not
    # a scope-gate-criterion one) -- see this file's header for the full
    # design and scoping.
    stream_reason = _stream_routing_block_reason(
        tk, api_call, body, cwd, target_repo, repo_dir)

    # #802 adversarial-review 🔵: a whitespace-only `-t` title cleans to ""
    # -- an EMPTY field, which bash's `IFS=$'\t' read` collapses (line ~1220
    # comment), shifting every field after it. `title = title or "(no
    # title)"` at line ~1028 only catches a MISSING/empty title, not a
    # whitespace-only one (truthy). Pin a non-empty clean title at the
    # source so both the tab-joined print AND the log echo stay aligned.
    clean_title = _clean_field(title) or "(no title)"

    if chain_capped:
        results.append(("BLOCK", clean_title, "chain-depth-cap", parents_str,
                         target_repo, ""))
    elif loc_mismatch:
        results.append(("BLOCK", clean_title, "loc-mismatch", parents_str,
                         target_repo, ""))
    elif stream_reason:
        results.append(("BLOCK", clean_title, stream_reason, parents_str,
                         target_repo, ""))
    elif not (crit and crit.lower() in ALLOWED):
        # unchanged from before #329 -- missing/invalid Scope-gate blocks
        # here, BEFORE the new dedup/cap checks below (keeps every
        # pre-existing test's block reason unaffected). #483: when the body
        # was UNRESOLVED because a `-F` disk path could not be read, surface
        # that explicit, actionable reason instead of the opaque `-> none`.
        # #483-review 🔴: _clean_field is MANDATORY here -- body_err embeds
        # the attacker-controlled `-F` token / cwd, and an embedded tab or
        # newline would otherwise forge a second record (a `verdict=PASS`
        # for an arbitrary repo) in the tab-separated hand-off to bash /
        # scope-gate.log, re-opening the #329 field-injection.
        #
        # #802: EVERY branch of this block must emit a CONCRETE reason -- an
        # empty criterion string (which the print loop renders as the opaque
        # `-> none`) is itself a defect. The montalu1 incident: a body
        # carrying `Scope-gate: user-request` inside a `--body "$(printf
        # ...)"` command-substitution had no newline-anchored `Scope-gate:`
        # LINE for CRITERION_RE, so crit=None; #483 only filled the reason
        # for a `-F` disk-path failure (body_err set), leaving the two
        # crit=None holes (body resolved but no line; body unresolvable with
        # no body_err) rendering `-> none` -- an undiagnosable block. Each
        # gets a concrete reason now:
        # #802-review 🟡: the `criterion=` field on these BLOCK lines is FREE
        # TEXT (already so since #483's body_err), so the #329 log-field
        # invariant "every counting field is written BEFORE the two free-text
        # fields" no longer holds in the LETTER for BLOCK lines -- a crafted,
        # ATTACKER-influenced crit / `-F` token like `x-parents=999` (a legal
        # `\S+` token) would decoy a later `\bparents=(\S+)`/`\bsession=(\S+)`
        # first-match with a forged value. Harmless TODAY only because
        # `_log_pass_count` (the ONLY counting consumer) filters `verdict=PASS`
        # FIRST and BLOCK lines are never counted. Defence-in-depth so a future
        # counting change over non-PASS lines can never re-open #329:
        # `_no_field_decoy` neutralises `=` to `:` in the two ATTACKER-derived
        # substrings only (crit, and the `-F` token embedded in body_err),
        # never in the author-controlled literal hints below (their fixed
        # `body=` is legit gh syntax and is not a counting-field name anyway).
        if body is None:
            # body could not be resolved: an unreadable `-F` disk path gives
            # the explicit #483 reason (attacker `-F` token neutralised); any
            # other unreadable shape (no -F/--body, an unresolvable heredoc, a
            # command-substitution / $VAR body a static PreToolUse scan cannot
            # execute) gets a concrete body-unresolved reason instead of the
            # empty `-> none`. #802-review 🔵: this branch is ALSO reached by a
            # `gh api ...issues` POST with no `body=` field, where `-F body.md`
            # / `--body` are the wrong flags -- name BOTH filing recipes.
            reason = _no_field_decoy(_clean_field(body_err)) if body_err else (
                "body-unresolved -- could not read the issue body from this "
                "command (no readable body; a $(...) / $VAR body cannot be read "
                "at PreToolUse -- write it to a file via a heredoc `cat > "
                "body.md <<'EOF' ... EOF` then `-F body.md`, or pass it inline: "
                "`--body \"...\"` for `gh issue create`, `-f body=...` / `-F "
                "body=@body.md` for `gh api`)")
        elif crit is None:
            # body IS readable but carries no `Scope-gate: <criterion>` line
            # at all -- the plain missing-line case.
            reason = "no-scope-gate -- body carries no `Scope-gate: <criterion>` line"
        else:
            # crit present but not one of ALLOWED -- name the bad (attacker-
            # written) value, decoy-neutralised.
            reason = "invalid-scope-gate:%s" % _no_field_decoy(_clean_field(crit))
        # Defensive: no BLOCK may ever carry an empty reason (would print as
        # `-> none`). Every branch above yields a non-empty string, but pin
        # it so a future edit cannot silently re-open the opaque block.
        reason = _clean_field(reason) or "unspecified-block"
        results.append(("BLOCK", clean_title, reason, parents_str,
                         target_repo, ""))
    else:
        crit_l = crit.lower()
        # #842 -- UNATTENDED gates (an ATTENDED / owner-present filing keeps the
        # pre-#842 flow untouched, so these never touch the owner). presence-gate
        # (req 3): an unattended loop cannot claim the owner asked for a
        # user-request / planned-work ticket. dismissal-word (req 4): a NEW issue
        # body dismissing a test failure (flaky / pre-existing / intermittent /
        # out-of-scope) is the same dismissal in durable form -- fix the test /
        # root cause, never file its excuse. Both BLOCK BEFORE the dedup/cap/
        # near-dup/ratchet checks (cheapest first) and `continue` this segment.
        if unattended:
            unattended_reason = None
            if crit_l in EXEMPT_FROM_CAP:
                unattended_reason = (
                    "presence-required (an unattended loop cannot claim the "
                    "owner asked -- %s is accepted only when the owner is "
                    "PRESENT)" % crit_l)
            else:
                _dw = _dismissal_word(body)
                if _dw:
                    unattended_reason = (
                        "dismissal-word:%s (fix the test / root cause -- do not "
                        "file its excuse as a ticket)" % _clean_field(_dw))
            if unattended_reason:
                results.append(("BLOCK", clean_title, unattended_reason,
                                 parents_str, target_repo, ""))
                continue
        dedup_match = DEDUP_RE.search(body) if body else None
        if not dedup_match:
            # #329 -- structural half of the dedup gate: prove you searched.
            results.append(("BLOCK", clean_title, "no-dedup-line", parents_str,
                             target_repo, ""))
        else:
            dedup_claim = _clean_field(dedup_match.group(1))[:80]
            today = _today_str()
            batch_titles = _batch_titles_by_repo.setdefault(target_repo, [])

            # #329 -- cheap local checks BEFORE the network near-dup call
            # (adversarial-review finding: a batch of N filings into N
            # different repos previously paid N network round-trips even
            # when the local caps alone would already refuse most of
            # them -- reordering costs nothing for the common case and
            # bounds the worst case).
            width_blocked = False
            if crit_l not in EXEMPT_FROM_CAP and parents:
                for p in parents:
                    n = (_log_pass_count(log_path, target_repo, today, parent=p)
                         + _local_parent_count.get((target_repo, p), 0))
                    if n >= CHAIN_WIDTH_CAP:
                        width_blocked = True
                        break
            daily_blocked = False
            if not width_blocked and crit_l not in EXEMPT_FROM_CAP:
                n = (_log_pass_count(log_path, target_repo, today)
                     + _local_day_count.get(target_repo, 0))
                if n >= DAILY_CAP:
                    daily_blocked = True

            if width_blocked:
                results.append(("BLOCK", clean_title, "chain-width-cap",
                                 parents_str, target_repo, dedup_claim))
            elif daily_blocked:
                results.append(("BLOCK", clean_title, "daily-cap",
                                 parents_str, target_repo, dedup_claim))
            else:
                near_dup = _near_duplicate(title, body, cwd, target_repo,
                                            batch_titles)
                if near_dup:
                    if near_dup == "in-batch":
                        reason = "near-duplicate:in-this-batch"
                    else:
                        reason = "near-duplicate:#%s" % near_dup
                    results.append(("BLOCK", clean_title, reason, parents_str,
                                     target_repo, dedup_claim))
                elif (unattended and crit_l not in EXEMPT_FROM_CAP
                      and _ratchet_should_block(target_repo, cwd)):
                    # #842 req 2 -- net-drain ratchet, checked LAST (the only gate
                    # costing a gh call, so it is never paid for a filing already
                    # blocked more cheaply). An UNATTENDED non-exempt discovery
                    # filing is allowed ONLY while the repo is strictly draining
                    # today (created_today < closed_today); otherwise BLOCK. A gh
                    # error -> BLOCK (fail-safe). user-request / planned-work are
                    # exempt (already presence-gated above).
                    results.append((
                        "BLOCK", clean_title,
                        "net-drain (created_today >= closed_today on this repo "
                        "-- fix it in-lane now, or fold it as a comment onto the "
                        "existing ticket it belongs to; this repo must drain "
                        "today before an unattended loop files more)",
                        parents_str, target_repo, dedup_claim))
                else:
                    results.append(("PASS", clean_title, crit, parents_str,
                                     target_repo, dedup_claim))
                    batch_titles.append(title or "")
                    # #842 -- the per-repo counter bump (to close the within-TTL
                    # burst race) is DEFERRED to the print loop below, where
                    # `has_block` is known: a PASS in a batch that ALSO blocks is
                    # NOTFILED (the whole command is refused, nothing filed), so
                    # bumping it here would be a phantom +1 (#842-review 🔵,
                    # mirroring the #329 phantom-PASS / NOTFILED discipline).
                    if crit_l not in EXEMPT_FROM_CAP:
                        _local_day_count[target_repo] = \
                            _local_day_count.get(target_repo, 0) + 1
                        for p in parents:
                            key = (target_repo, p)
                            _local_parent_count[key] = \
                                _local_parent_count.get(key, 0) + 1

if not results:
    sys.exit(0)

has_block = any(r[0] == "BLOCK" for r in results)
for verdict, title, crit, parents_str, target_repo, dedup_claim in results:
    # #329 -- a PASS logged while ANY sibling in this same command BLOCKED
    # is a phantom: the whole tool call is refused, so nothing was really
    # filed. Log it as NOTFILED (never PASS) so `_log_pass_count` -- which
    # only ever counts an EXACT "PASS" token -- never charges cap budget
    # for a filing that never happened (#329 adversarial-review CRITICAL).
    log_verdict = "NOTFILED" if (has_block and verdict == "PASS") else verdict
    # #842 -- record a GENUINELY-FILED PASS forward in the per-repo counter cache
    # (created_today += 1), closing the within-TTL burst race across separate
    # hook invocations. Deferred to here so a phantom PASS (NOTFILED because a
    # sibling segment blocked the whole command) never bumps the counter for a
    # filing that never happened. Only an UNATTENDED non-exempt discovery filing
    # is ratchet-counted (user-request/planned-work are exempt).
    if unattended and log_verdict == "PASS" \
            and (crit or "").lower() not in EXEMPT_FROM_CAP:
        _ratchet_bump(target_repo)
    # bash's `read` with IFS=<tab> still treats tab as "IFS whitespace" and
    # COLLAPSES consecutive delimiters, silently swallowing an empty field
    # (discovered live testing this hook) -- never emit an empty field.
    print("%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
        log_verdict, target_repo, title, crit or "none", sid,
        parents_str, dedup_claim or "none"))

sys.exit(2 if has_block else 0)
PYEOF
) || RC=$?

SUMMARY=""
if [ -n "$OUT" ]; then
    while IFS=$'\t' read -r VERDICT REPO TITLE CRIT LOGSID PARENTS DEDUP; do
        [ -z "$VERDICT" ] && continue
        # #329 -- log-line field ORDER is security-relevant: every
        # counting field (verdict/repo/criterion/session/parents) is
        # written BEFORE the two free-text fields (title/dedup), so a
        # `\bfield=(\S+)` search on ANY counting field is guaranteed to
        # find the real one first, never a value a crafted title/dedup
        # claim could spell out later in the same line.
        echo "$(date -Iseconds)  verdict=$VERDICT  repo=$REPO  criterion=${CRIT:-none}  session=$LOGSID  parents=${PARENTS:-none}  title=\"$TITLE\"  dedup=\"${DEDUP:-none}\"" >> "$LOG" 2>/dev/null || true
        if [ "$VERDICT" = "BLOCK" ]; then
            SUMMARY="${SUMMARY}  - \"$TITLE\" -> ${CRIT:-none}
"
        fi
    done <<< "$OUT"
fi

if [ "$RC" -eq 2 ]; then
    if [ -n "$SUMMARY" ]; then
        printf '🚫 BLOCKED — per-item reason:\n%s\n' "$SUMMARY" >&2
    fi
    cat >&2 <<'MSG'
Either (a) no valid `Scope-gate:` line, (b) this issue's own PARENT (the
"#N follow-up" it names) is ITSELF a review-finding follow-up -- a depth-2
review-finding chain (#311: adversarial-review findings that keep spawning
follow-up tickets of follow-up tickets, unbounded — a criterion honestly
satisfied at each individual hop does not fix this), (c) the body claims
`Scope-gate: >300-loc` but its own text states a line count of 300 or
fewer, a self-contradicting claim (#311), (d) no `Dedup-checked: <what you
searched>` line proving you searched for an existing duplicate first
(#329), (e) an existing OPEN issue (or an earlier sibling in THIS SAME
batch) has a near-duplicate title and this one does not reference the
existing issue by `#N` (#329), (f) this repo already has 2 other
non-exempt findings filed today off the SAME parent ticket -- the
chain-width cap (#329: a 3rd+ finding off one review belongs in that
branch, not a new ticket), (g) this repo has already reached today's
soft filing cap of 8 non-exempt issues (#329), (h) this repo is
stream-aware and this filing (from a known sub-dev stream account) carries
no explicit `stream:<x>` label at all (#390), or (i) it carries a
`stream:<x>` label naming a DIFFERENT stream than your own, with no
`Stream-routing: <reason>` line justifying the hand-off (#390).

#842 (UNATTENDED sessions only — an attended/owner-present filing is never
subject to these): (j) `presence-required` — a `user-request`/`planned-work`
criterion is accepted only when the OWNER is PRESENT; an unattended loop cannot
claim the owner asked. (k) `dismissal-word` — the body dismisses a test failure
(flaky / pre-existing / intermittent / out of scope); FIX the test/root cause,
do not file its excuse as a ticket. (l) `net-drain` — this repo has created at
least as many issues as it closed today; the loop must DRAIN it (fix in-lane, or
fold this onto the existing ticket) before filing more.

Per complete-planned-work.md's Follow-up gate: a discovered cleanup under
~100 LoC in a file your current work already touches gets FIXED NOW in this
PR/session — it does NOT get filed as a follow-up issue, EVEN when it
technically sits outside your diff. Filing is for GENUINELY out-of-scope
work only, and a review finding that would spawn a SECOND generation of
follow-up, or a 3rd+ finding off one parent, or the day's 9th+ non-exempt
filing on this repo, must be fixed in THIS branch instead of filed as
another ticket.

Fix NOW — one of:
  1. Fix it in this session/PR instead of filing it (mandatory once the
     chain-depth cap, chain-width cap, or daily cap above applies), OR
  2. Add a `Dedup-checked: <what you searched for>` line if it is missing, OR
  3. Reference the existing near-duplicate issue by `#N` instead of filing
     a new one, OR
  4. Add a line to the issue body naming why it is genuinely out of scope:
       Scope-gate: <criterion>
     where <criterion> is one of:
       >300-loc | schema-migration | api-break | security-boundary |
       cross-cutting | needs-user-decision | planned-work | user-request
     (only `planned-work`/`user-request` are exempt from the daily and
     chain-width caps), OR
  5. Add an explicit `-l stream:<your-own-stream>` label matching YOUR OWN
     stream (#390) -- or, when this ticket genuinely belongs to a
     DIFFERENT stream, keep that stream's label and add a
     `Stream-routing: <reason>` line to the body naming why it belongs to
     them, not you.

This is a LOGGED, falsifiable claim (~/.claude/scope-gate.log) — it does not
verify the criterion is true, only that you affirmatively claimed one instead
of silently filing. See modules/quality/complete-planned-work.md and
modules/quality/no-dropped-work.md. Genuine bypass: append
`# airuleset:scope-gate-ok <reason>` to the command.
MSG
    exit 2
fi

exit 0
