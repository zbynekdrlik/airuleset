"""gates.filing.parse -- command + issue-body PARSING for the ungated-issue-filing
gate (#1020 Part 2). Pure/offline: no subprocess, no gh, no airuleset import, so
it unit-tests without a shell.

Extracted VERBATIM from the classifier that lived embedded in
hooks/block-ungated-issue-filing.sh's `python3 <<PYEOF` heredoc; the ONLY change
is the ONE allowed unification -- ``split_top_level`` is now imported from
``gates.shellcmd`` (byte-identical to the copy that lived here) instead of a
fourth hand-written copy -- plus threading ``cmd``/``file_bodies``/``direct_bodies``
through ``resolve_body``/``_unreadable_body_err`` as explicit parameters (they
were module-level globals in the single-script heredoc) and a small
``extract_heredocs`` wrapper around the two heredoc-capture passes. Behaviour is
proven identical by tests/test_scope_gate.py (the end-to-end oracle) and the
tests/test_gates_filing_char.py characterization pins.
"""
import os
import re
import shlex

from gates.shellcmd import split_top_level  # noqa: F401 -- re-exported (the ONE #1020 unification, was a 4th copy)

HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1\s*$")
CATFILE_RE = re.compile(r'^\s*cat\s*>>?\s*([^\s<>&;|]+)')


ALLOWED = {
    ">300-loc", "schema-migration", "api-break", "security-boundary",
    "cross-cutting", "needs-user-decision", "planned-work", "user-request",
    # #993 -- a REWORK verdict from the integration-time area-review gate files
    # an architecture-rework ticket AUTONOMOUSLY (the supervisor, same turn); it
    # must be able to file even on a non-draining repo, and it requires an
    # `Area:` line (one open rework ticket per area) rather than an owner quote.
    "architecture-rework",
}


# #329 -- these criteria are exempt from the NEW soft caps below (daily filing
# cap, chain-width cap) AND (#842) the net-drain ratchet -- they never COUNT
# toward either cap and never GET capped/ratchet-blocked. A user directive or a
# converged-plan decomposition must always be able to file
# (durable-decisions-to-tickets.md); a discovered review-finding/cleanup
# must not. #993 -- architecture-rework joins them: a genuine area-rework
# verdict is a mandated autonomous action, never a discovery to be rate-limited.
EXEMPT_FROM_CAP = {"planned-work", "user-request", "architecture-rework"}


# #993 -- the `Area:` line every architecture-rework body must carry (the
# dedup-by-area discipline: one open rework ticket per area). A newline-anchored
# match, same shape as CRITERION_RE / DEDUP_RE.
AREA_RE = re.compile(r'(?m)^\s*Area:\s*(\S.*)$')


# #1027/#1033 -- a `Scope-gate: user-request` ticket filed FROM a client Odoo
# Discuss message (its body QUOTES the origin: a `mail.message`, a
# `discuss.channel_<N>` deep URL, or an explicit `msg <id>` reference) must cite
# the intake worker-reaction (an `Ack-reaction:` line) -- the owner's visible
# "being worked on" signal (👷) on the client message, added the MOMENT the
# message is picked up, BEFORE the ticket is filed (skills/odoo-client-messaging/
# ack-reaction.md + handover-compose.md). The origin regex uses the two
# Odoo-specific tokens (`mail.message`, `discuss.channel_<N>`) plus a labelled
# `msg <id>` with a 4+-digit id (real Odoo mail.message ids are large: 3122,
# 1739648) so it never false-matches a generic "msg 12" or non-Odoo body.
# #1027-review 🔵: `mail\.message` carries a leading `\b` so an unrelated
# `email.message` (which contains "mail.message" as a substring) never matches.
CLIENT_MSG_ORIGIN_RE = re.compile(
    r'\bmail\.message|discuss\.channel_\d+|\bmsg\s+\d{4,}', re.IGNORECASE)
# The citation: the same `Ack-reaction:` evidence line the prose Stop hook and
# ack-reaction.md doctrine use (`Ack-reaction: msg <id> 👷` or
# `Ack-reaction: pending — <reason>`).
ACK_REACTION_CITE_RE = re.compile(r'(?m)^\s*Ack-reaction:\s*\S', re.IGNORECASE)


# ---- pass 2: segment the skeleton exactly like block-gh-invalid-json-flag.sh
# (same shape, deliberately reused rather than reinvented — see that hook's
# #85 note).
ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


LOOP_BODY_KEYWORDS = ("do", "then", "else", "elif")


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


def _unreadable_body_err(bf, eff_cwd, cmd):
    """#483 -- an actionable per-item block reason for a `-F <file>` disk
    path that could not be read, replacing the opaque `-> none`. Names the
    file, the effective cwd it was resolved against (or that it was
    unresolvable), and the fix (an absolute -F path). One line -- it crosses
    the tab-separated hand-off to bash (see _clean_field).
    #988: when the same compound command contains a redirect writing the
    same file, give a clear "write in a SEPARATE command" message."""
    # #988: check if the raw command creates this file via redirect/heredoc
    basename = os.path.basename(bf)
    if basename and ("> " + bf) in cmd or (">>" + bf) in cmd or \
            (">" + bf) in cmd:
        return ("body file '%s' does not exist yet -- write the body file "
                "in a SEPARATE Bash call first, then run gh issue create -F %s"
                % (bf, bf))
    if os.path.isabs(bf):
        return ("body file '%s' not readable -- path is missing or unreadable "
                "(check the absolute -F path)" % bf)
    where = ("'%s'" % eff_cwd) if eff_cwd is not None else \
        "an unresolvable 'cd' target ($VAR/~/glob)"
    return ("body file '%s' not readable -- a relative -F path resolved "
            "against %s; use an absolute -F path" % (bf, where))


def resolve_body(tk, seg_line, is_api, eff_cwd, file_bodies, direct_bodies, cmd):
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
        return None, _unreadable_body_err(bf, eff_cwd, cmd)
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


def _all_labels(tk, is_api):
    """Every label value named via -l/--label in THIS segment's own tokens,
    lowercase, deduped, in order of appearance -- comma-list aware
    (`-l bug,stream:david2`), any repetition (`-l a -l b`), and every
    genuine `gh`-accepted spelling of the short flag: separate-token
    (`-l stream:x`), ATTACHED (`-lstream:x`), and attached-with-equals
    (`-l=stream:x`) -- #390 adversarial-review MAJOR-2, verified live
    against the real `gh` binary. Only `gh issue create` is scanned --
    `gh api ... POST` labeling is deliberately out of scope (see this
    file's header). #962: factored from `_explicit_stream_labels` to
    serve both the stream-routing gate and the needs-gatekeeper check."""
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
            if piece and piece not in found:
                found.append(piece)
    return found


def _explicit_stream_labels(tk, is_api):
    """The subset of `_all_labels` matching `stream:<x>` -- the #390
    stream-routing gate's own label comparator."""
    return [lb for lb in _all_labels(tk, is_api) if STREAM_LABEL_RE.match(lb)]


def extract_heredocs(cmd):
    """Two passes over `cmd`, VERBATIM from the hook's own pass 1 + pass 2:
    (1) locate every heredoc, capture its body, note whether its trigger line
    is `cat > FILE <<DELIM` (file-attached) or bare (direct-attached);
    (2) blank the heredoc BODY spans out of the skeleton so segment
    classification still sees the command + the `<<DELIM` marker. Returns
    `(file_bodies, direct_bodies, skeleton)`."""
    lines = cmd.split("\n")
    n = len(lines)
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
        for k in range(i + 1, min(j + 1, n)):
            skeleton_lines[k] = ""
        i = j + 1
    skeleton = "\n".join(skeleton_lines)
    return file_bodies, direct_bodies, skeleton
