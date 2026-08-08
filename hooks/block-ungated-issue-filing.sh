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
# (`gh issue list --state open --json number,title`, bounded, degrading to
# "cannot verify" on ANY failure — offline, unauthenticated, rate-limited,
# `gh` missing) and title-diffs the new title against every one of them
# (normalized, `difflib.SequenceMatcher`, ratio >= 0.86). A near-duplicate
# BLOCKS naming the existing issue number, UNLESS that number is already
# referenced (`#N`) somewhere in the new title/body — an explicit link is
# not a silent duplicate. Neither half of this gate ever manufactures a
# block from an unmeasurable state: a missing line is a real, structural
# gap (blocks); a failed `gh` lookup is "couldn't check" (never blocks on
# its own).
#
# DAILY FILING CAP (#329, soft, per repo): counts today's own
# `verdict=PASS` lines already written to ~/.claude/scope-gate.log for the
# SAME repo (plus filings already classified PASS earlier in the same Bash
# call, since one batch can file several issues before anything is
# written). Once that count reaches 8, a filing whose `Scope-gate:`
# criterion is NOT `user-request`/`planned-work` is BLOCKED — the intent is
# to push discovered work toward "fix it now, in this branch" per
# no-dropped-work.md, never toward silently dropping it; the block message
# says exactly that. `user-request`/`planned-work` are exempt because a
# user directive or a converged-plan decomposition must always be able to
# file (durable-decisions-to-tickets.md).
#
# CHAIN-WIDTH CAP (#329, extends #311's chain-DEPTH cap with the WIDTH
# half of the same measured failure — odoo-erp's real
# #3250/#3251/#3252/#3258, four siblings off ONE parent, #3224, in one
# burst, each individually depth-1 and so invisible to the depth cap
# alone). Reuses the SAME `_chain_parents()` "(#N follow-up)" extraction:
# once a parent already has 2 other same-day PASS-logged siblings on this
# repo, the 3rd+ is BLOCKED (same `user-request`/`planned-work` exemption
# as the daily cap) — a repeated finding off one review belongs in that
# branch, not in a fourth new ticket.
#
# Both new caps are logged through the SAME scope-gate.log mechanism
# (extended with a `parents=` field, never a second log) and honour the
# SAME `# airuleset:scope-gate-ok <reason>` bypass as every other gate
# here — no new bypass syntax.
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
# Every PASS and BLOCK is logged to ~/.claude/scope-gate.log (repo, title,
# criterion, session, parents) — the log is what keeps a false criterion
# auditable AND is what the daily/width caps count against.
#
# Bypass (rare, logged): `# airuleset:scope-gate-ok <reason>` anywhere in the
# command text — bypasses EVERY gate in this hook, not just Scope-gate.
#
# KNOWN LIMIT: cannot verify the criterion is TRUE, only that it was
# affirmatively claimed. See modules/quality/no-dropped-work.md — filing
# must never become impossible; this hook's job is to stop the SILENT
# default, not to add a second judgment layer on top of the model's own.

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
SID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$CMD" ] && exit 0

# Cheap pre-filter: only classify commands that could plausibly contain a
# gh issue-creation call at all.
case "$CMD" in
    *"issue create"*) ;;
    *"gh api"*"issues"*) ;;
    *) exit 0 ;;
esac

# Deliberate bypass for a genuine edge.
case "$CMD" in *"airuleset:scope-gate-ok"*) exit 0 ;; esac

LOG="$HOME/.claude/scope-gate.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

# python3 - "$CMD" <<'PYEOF' (argv, never a pipe into the heredoc's own
# stdin — see the repo's own #96 gotcha).
RC=0
OUT=$(python3 - "$CMD" "$SID" "$(pwd)" "$LOG" <<'PYEOF' 2>/dev/null
import difflib
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

ALLOWED = {
    ">300-loc", "schema-migration", "api-break", "security-boundary",
    "cross-cutting", "needs-user-decision", "planned-work", "user-request",
}

# #329 -- these two criteria are the only ones that legitimately bypass the
# NEW soft caps below (daily filing cap, chain-width cap). A user directive
# or a converged-plan decomposition must always be able to file
# (durable-decisions-to-tickets.md); a discovered review-finding/cleanup
# must not.
EXEMPT_FROM_CAP = {"planned-work", "user-request"}

# #329 -- soft per-day, per-repo cap on agent-authored filings. Measured
# worst days on the ticket's own real corpus: 19/16/14 filings in ONE day
# on ONE repo; the user's own stated sane ceiling for the WHOLE project's
# lifetime backlog was ~40 tickets. 8/day/repo sits comfortably above what
# a genuinely active /autopilot day of bundled batches should need (a
# handful of real out-of-scope discoveries across several PRs) while being
# decisively below every measured storm day.
DAILY_CAP = 8

# #329 -- chain-WIDTH cap (siblings off ONE parent, same day, same repo).
# The real corpus example is 4 siblings off one parent (#3224) in one
# burst; 1-2 genuinely distinct findings off a single review is plausible
# (e.g. a security finding + a schema finding from the same PR review), a
# 3rd is the storm signature -- so 2 pass, the 3rd blocks.
CHAIN_WIDTH_CAP = 2

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


def resolve_body(tk, seg_line, is_api):
    if is_api:
        # `gh api` overloads -f/-F for arbitrary key=value FIELDS (typed
        # vs raw), unlike `gh issue create`'s -F <FILE PATH>. Find a
        # `body=<value>` field among -f/-F/--field/--raw-field tokens.
        for idx, t in enumerate(tk):
            if t in ("-f", "-F", "--field", "--raw-field") and idx + 1 < len(tk):
                v = tk[idx + 1]
                if v.startswith("body="):
                    return v[len("body="):]
            elif t.startswith(("-f", "-F", "--field=", "--raw-field=")) and "=" in t:
                # -fbody=x / --field=body=x shapes — best-effort only.
                pass
        return None
    bf = flag_value(tk, ("-F", "--body-file"))
    if bf is not None:
        if bf == "-":
            m = HEREDOC_RE.search(seg_line.rstrip())
            if m and m.group(2) in direct_bodies:
                return direct_bodies[m.group(2)]
            return None
        if bf in file_bodies:
            return file_bodies[bf]
        try:
            path = bf if os.path.isabs(bf) else os.path.join(cwd, bf)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None
    inline = flag_value(tk, ("--body",))
    if inline is not None:
        return inline
    return None


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
    hang, so this is safe to run unconditionally."""
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


NEAR_DUP_THRESHOLD = 0.86


def _near_duplicate(title, body, cwd, repo):
    """Number of an existing OPEN issue whose title near-duplicates
    `title` (SequenceMatcher ratio >= NEAR_DUP_THRESHOLD on normalized,
    lowercased text), or None. An existing issue already referenced by
    #N anywhere in title+body is skipped -- an explicit link is not a
    silent duplicate. Degrades to None whenever the real lookup can't run
    at all (see _fetch_open_issues) -- keeps false positives near zero by
    a HIGH threshold: two genuinely distinct tickets rarely share 86%+ of
    their normalized title text."""
    issues = _fetch_open_issues(cwd, repo)
    if not issues:
        return None
    haystack = (title or "") + "\n" + (body or "")
    norm_new = re.sub(r'\s+', ' ', (title or "").strip().lower())
    if not norm_new:
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
        norm_other = re.sub(r'\s+', ' ', str(other).strip().lower())
        if not norm_other:
            continue
        ratio = difflib.SequenceMatcher(None, norm_new, norm_other).ratio()
        if ratio >= NEAR_DUP_THRESHOLD and (best is None or ratio > best[1]):
            best = (num, ratio)
    return best[0] if best else None


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _log_pass_count(path, repo, today, parent=None):
    """Count of PASS-verdict filings already WRITTEN to the scope-gate log
    for `repo` on `today` -- optionally further scoped to filings whose
    OWN logged `parents=` field named `parent` (the chain-width count). A
    missing/unreadable log -> 0 (never invents a cap violation from
    unmeasurable state)."""
    count = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.startswith(today):
                    continue
                if "verdict=PASS" not in line:
                    continue
                m = re.search(r'repo=(\S+)', line)
                if not m or m.group(1) != repo:
                    continue
                if parent is not None:
                    mp = re.search(r'parents=(\S+)', line)
                    if not mp or parent not in mp.group(1).split(","):
                        continue
                count += 1
    except OSError:
        pass
    return count


results = []  # (verdict, title, criterion_or_none, parents_str)

# #329 -- resolve the cwd-derived repo BEFORE the per-segment loop (moved
# up from the tail of the script) so the dedup/cap checks can use it; the
# final print loop below reuses this SAME value, unchanged from before.
repo = os.path.basename(cwd.rstrip("/"))
try:
    _out = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                          capture_output=True, text=True, timeout=3)
    _url = (_out.stdout or "").strip()
    _m = re.search(r'[:/]([^/]+/[^/]+?)(\.git)?$', _url)
    if _m:
        repo = _m.group(1)
except Exception:
    pass

# #329 -- local running counters for a BATCH filing several issues into the
# SAME repo in one Bash call, before anything is written to the log.
_local_day_count = 0
_local_parent_count = {}  # parent -> count

for seg in split_top_level(skeleton):
    if not seg.strip():
        continue
    tk = strip_prefix(tokens_of(seg))
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
    body = resolve_body(tk, seg, api_call)
    crit = None
    if body:
        m = CRITERION_RE.search(body)
        if m:
            crit = m.group(1)
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
    repo_flag = flag_value(tk, ("-R", "--repo"))
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

    if chain_capped:
        results.append(("BLOCK", title, "chain-depth-cap", parents_str))
    elif loc_mismatch:
        results.append(("BLOCK", title, "loc-mismatch", parents_str))
    elif not (crit and crit.lower() in ALLOWED):
        # unchanged from before #329 -- missing/invalid Scope-gate blocks
        # here, BEFORE the new dedup/cap checks below (keeps every
        # pre-existing test's block reason unaffected).
        results.append(("BLOCK", title, crit, parents_str))
    elif not (body and DEDUP_RE.search(body)):
        # #329 -- structural half of the dedup gate: prove you searched.
        results.append(("BLOCK", title, "no-dedup-line", parents_str))
    else:
        near_dup = _near_duplicate(title, body, cwd, repo_flag)
        crit_l = crit.lower()
        today = _today_str()
        if near_dup:
            results.append(("BLOCK", title, "near-duplicate:#%s" % near_dup,
                             parents_str))
        elif (crit_l not in EXEMPT_FROM_CAP and parents and
              (_log_pass_count(log_path, repo, today, parent=parents[0]) +
               _local_parent_count.get(parents[0], 0)) >= CHAIN_WIDTH_CAP):
            results.append(("BLOCK", title, "chain-width-cap", parents_str))
        elif (crit_l not in EXEMPT_FROM_CAP and
              (_log_pass_count(log_path, repo, today) + _local_day_count)
              >= DAILY_CAP):
            results.append(("BLOCK", title, "daily-cap", parents_str))
        else:
            results.append(("PASS", title, crit, parents_str))
            _local_day_count += 1
            for p in parents:
                _local_parent_count[p] = _local_parent_count.get(p, 0) + 1

if not results:
    sys.exit(0)

blocked = [r for r in results if r[0] == "BLOCK"]
for verdict, title, crit, parents_str in results:
    # bash's `read` with IFS=<tab> still treats tab as "IFS whitespace" and
    # COLLAPSES consecutive delimiters, silently swallowing an empty field
    # (discovered live testing this hook) -- never emit an empty field.
    print("%s\t%s\t%s\t%s\t%s\t%s" % (verdict, repo, title, crit or "none",
                                       sid, parents_str))

sys.exit(2 if blocked else 0)
PYEOF
) || RC=$?

SUMMARY=""
if [ -n "$OUT" ]; then
    while IFS=$'\t' read -r VERDICT REPO TITLE CRIT LOGSID PARENTS; do
        [ -z "$VERDICT" ] && continue
        echo "$(date -Iseconds)  verdict=$VERDICT  repo=$REPO  title=\"$TITLE\"  criterion=${CRIT:-none}  session=$LOGSID  parents=${PARENTS:-none}" >> "$LOG" 2>/dev/null || true
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
(#329), (e) an existing OPEN issue has a near-duplicate title and this one
does not reference it by `#N` (#329), (f) this repo already has 2 other
findings filed today off the SAME parent ticket -- the chain-width cap
(#329: a 3rd+ finding off one review belongs in that branch, not a new
ticket), or (g) this repo has already reached today's soft filing cap of
8 non-exempt issues (#329).

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
     chain-width caps).

This is a LOGGED, falsifiable claim (~/.claude/scope-gate.log) — it does not
verify the criterion is true, only that you affirmatively claimed one instead
of silently filing. See modules/quality/complete-planned-work.md and
modules/quality/no-dropped-work.md. Genuine bypass: append
`# airuleset:scope-gate-ok <reason>` to the command.
MSG
    exit 2
fi

exit 0
