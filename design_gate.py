"""Design-before-code mechanical gate (#136).

THE PROBLEM. `autonomous-batch-issue-development.md` mandates posting root
cause + chosen approach + rejected alternative to the ticket via `gh issue
comment` BEFORE the first code commit for that ticket. Commit `e9d1022`
(2026-07-09) moved that content out of the always-loaded modules into the
on-demand `batch-issue-development` skill; nothing in the `/autopilot`
dispatch chain ever invokes that skill, so for 18 days the step was
physically unreachable by every dispatched worker (a skill BODY never
reaches a dispatched subagent -- only its one-line description does; #104
probes). Commit `95dbc9b` (2026-07-27) restored the step as PROSE, inline in
`agents/autopilot-worker.md` -- reachable now, but with no teeth. Measured:
10/30 compliant pre-fix (33%), roughly half post-fix. Prose alone is exactly
the enforcement class #134 already proved does not hold on its own (the
per-ticket Discord card was prose too, and produced zero reports across
~85 merged PRs before it got a hook).

THE FIX -- a gate in the `subagent-stop-check-run-card.sh` family:

  1. A durable per-issue MARKER (`~/.claude/design-posted/<repo>#<issue>`),
     written only on DELIVERED evidence: a real `gh issue comment` that (a)
     actually posted (confirmed by re-reading the issue's comments back from
     GitHub -- never trusted from the command that was ABOUT to run) and (b)
     whose body plausibly carries root cause + chosen approach + rejected
     alternative (`classify_design_comment` below). The marker's presence is
     a claim, so it is only ever written from code that observed the real
     GitHub state -- the same #135 lesson `notify.marker_delivered` encodes.
  2. `hooks/block-commit-without-design.sh` (PreToolUse, `git commit`) --
     blocks an autopilot-worker's commit that references issue #N when no
     marker exists yet for #N.
  3. `hooks/subagent-stop-check-design.sh` (SubagentStop) -- a backstop for
     when the PreToolUse hook missed (not deployed on some box yet, a commit
     made outside the Bash tool, an explicit but unjustified bypass).

CLASSIFIER. `classify_design_comment` is a heuristic, not a proof of quality
-- it requires (a) the body clears a minimum length (a real paragraph, not
"on it") and (b) uses language from all three concept families: root cause,
chosen approach, rejected alternative (English + Slovak keyword sets, since
tickets in this fleet are written in both). Same documented limitation as
`block-ungated-issue-filing.sh`: this cannot verify the CONTENT is actually
correct, only that a claim of the right SHAPE was made. That is intentional,
not an oversight -- a mechanical gate can judge shape, never engineering
judgment. It is the SAME classifier `scripts/measure_design_compliance.py`
(the Deliverable-1 corpus measurement) uses, so the measured baseline and
the enforced gate are provably the same yardstick, not two independent
guesses that happen to agree.

STATE-FILE CONVENTION. Marker key = `<repo-key>#<issue>`, where repo-key is
`notify.repo_name_for(cwd)` -- the bare last path segment of the `origin`
remote (e.g. "airuleset", "parovanie-produktov"). This is the EXACT
convention `autopilot-notify-sent/` / `_notify_run_card` already use (see
that function's own docstring for the directory-vs-remote-name incident it
exists to avoid) -- deliberately not reinvented here.
"""
import os
import re
import subprocess
import time

_DESIGN_DIRNAME = "design-posted"

# #213/#214 -- the SAME artifact pattern extended to two more evidence
# kinds: "validated" (Step 0's live-reproduction proof) and "reviewed"
# (the /review + /requesting-code-review pass). One marker directory per
# kind, same sanitized-key convention as design. See `_dir_for_kind`.
_VALIDATED_DIRNAME = "validated-posted"
_REVIEWED_DIRNAME = "reviewed-posted"
_KIND_DIRNAMES = {
    "design": _DESIGN_DIRNAME,
    "validated": _VALIDATED_DIRNAME,
    "reviewed": _REVIEWED_DIRNAME,
}
ALL_KINDS = ("design", "validated", "reviewed")


def _dir_for_kind(kind):
    return _KIND_DIRNAMES.get(kind, _DESIGN_DIRNAME)

# A real "one honest paragraph" (the worker system prompt's own phrase for
# the minimum acceptable depth) reliably clears this; "on it" / "working on
# this now" does not. Calibrated, not derived -- see the classifier's known
# limits above.
MIN_LEN = 120

_CAUSE_RE = re.compile(
    r"root\s*cause|underlying\s*cause|caused\s+by|because\s+the\b|"
    r"pr[ií][cč]in|d[ôo]vod|sp[ôo]soben|"
    # #219: common Slovak root-cause phrasings that don't use
    # "príčina"/"dôvod"/"spôsobené" -- "zdroj" alone is deliberately
    # QUALIFIED (problému/chyby) so it never matches "zdrojový kód" (source
    # CODE), an unrelated and extremely common technical phrase.
    #
    # Adversarial-review findings (post-#219 fix): a bare "koreň" collided
    # with "koreňový adresár"/"koreňová zložka" (root DIRECTORY/folder --
    # the adjective form always continues "koreň" + "ov" + gender suffix,
    # excluded via the negative lookahead), and a bare "zisten" was a pure
    # accidental SUBSTRING of "konzistentná"/"nekonzistentný" (consistent/
    # inconsistent, kon-ZISTEN-tná) with no word boundary in front of it --
    # nothing to do with "zistenie" (finding), excluded via \b.
    r"kore[ňn](?!ov)|zdroj\s+(?:probl[ée]mu|chyby)|\bzisten|\bzistil|"
    r"čo\s+sa\s+stalo|ch[ýy]bal|\bnebolo\b",
    re.IGNORECASE,
)
_APPROACH_RE = re.compile(
    r"\bapproach\b|chosen\s+approach|pr[ií]stup|rie[šs]enie|"
    r"implementujem|budem\s+(?:robi[ťt]|implementova[ťt]|pou[žz][íi]va[ťt])|"
    r"plan(?:ujem)?\s+to|will\s+(?:implement|fix|add|use|do)|"
    r"fix(?:ing)?\s+(?:it\s+)?by",
    re.IGNORECASE,
)
_ALT_RE = re.compile(
    r"alternat[ií]v|namiesto|zamietol|zavrhol|rejected|instead\s+of|"
    r"rather\s+than|considered\s+(?:but|and)|could\s+(?:also\s+)?have|"
    r"in[eé]\s+(?:mo[žz]nosti|rie[šs]enie)",
    re.IGNORECASE,
)


def classify_design_comment(body):
    """Heuristic verdict: does `body` plausibly carry root cause + chosen
    approach + rejected alternative? Returns `(ok: bool, reason: str)` --
    `reason` is always populated (either "ok" or what's missing), so a
    caller can surface it verbatim instead of re-deriving a message.

    Deliberately lenient in one direction: EITHER of the two Slovak/English
    phrasing families for a concept satisfies it -- this is a shape check,
    not a grader. See the module docstring for the documented limitation.
    """
    text = (body or "").strip()
    if len(text) < MIN_LEN:
        return False, "too short (%d chars, need >= %d)" % (len(text), MIN_LEN)
    missing = []
    if not _CAUSE_RE.search(text):
        missing.append("root cause")
    if not _APPROACH_RE.search(text):
        missing.append("chosen approach")
    if not _ALT_RE.search(text):
        missing.append("rejected alternative")
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "ok"


def _claude_dir():
    return os.path.join(os.path.expanduser("~"), ".claude")


def design_dir(kind="design"):
    return os.path.join(_claude_dir(), _dir_for_kind(kind))


def marker_key(repo_key, issue):
    return "%s#%s" % (repo_key, issue)


def marker_path(repo_key, issue, kind="design"):
    safe = re.sub(r"[^A-Za-z0-9._#-]", "_", marker_key(repo_key, issue))
    return os.path.join(design_dir(kind), safe)


def marker_exists(repo_key, issue, kind="design"):
    if not repo_key or issue in (None, ""):
        return False
    return os.path.isfile(marker_path(repo_key, issue, kind))


def write_marker(repo_key, issue, comment_url, reason="ok", ts=None, kind="design"):
    """Record DELIVERED evidence for `<repo_key>#<issue>` (kind =
    "design" / "validated" / "reviewed"). Never call this speculatively --
    only from a code path that has just observed a REAL posted comment (see
    `hooks/post-record-design-comment.sh`). Best-effort: an unwritable
    ~/.claude never raises, it just means the gate stays active (fail
    toward re-asking, never toward silently trusting)."""
    if not repo_key or issue in (None, ""):
        return False
    d = design_dir(kind)
    try:
        os.makedirs(d, exist_ok=True)
        with open(marker_path(repo_key, issue, kind), "w", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n" % (ts if ts is not None else time.time(),
                                       comment_url or "-", reason or "ok"))
        return True
    except OSError:
        return False


def read_marker(repo_key, issue, kind="design"):
    """`{"ts": float, "url": str, "reason": str}` or None. Tolerates the
    legacy/short shape (fewer than 3 fields) rather than raising."""
    try:
        with open(marker_path(repo_key, issue, kind), encoding="utf-8") as fh:
            body = fh.read(2000).strip()
    except OSError:
        return None
    parts = body.split("\t")
    if not parts or not parts[0]:
        return None
    try:
        ts = float(parts[0])
    except ValueError:
        ts = None
    return {
        "ts": ts,
        "url": parts[1] if len(parts) > 1 else "",
        "reason": parts[2] if len(parts) > 2 else "",
    }


def claimed_urls(repo_key, issue):
    """The set of comment URLs already used by an EXISTING marker of any
    kind for `<repo_key>#<issue>`. Adversarial-review finding (post-#214):
    one comment posted before any code exists could plausibly satisfy
    design + validated + reviewed all at once, which defeats the premise
    that each kind proves its own step happened. A caller uses this to
    refuse granting a SECOND kind from a comment url that already granted
    one -- distinct evidence kinds must come from distinct comments."""
    urls = set()
    for kind in ALL_KINDS:
        m = read_marker(repo_key, issue, kind)
        if m and m.get("url"):
            urls.add(m["url"])
    return urls


# --------------------------------------------------------------------------- #
# #213 -- validation classifier: does `body` plausibly carry Step 0's
# live-reproduction proof (an action taken + what it showed)? Same shape as
# `classify_design_comment` (bilingual, shape-not-content, two concept
# families instead of three -- validation evidence is naturally a tighter
# claim than a full root-cause/approach/alternative design).
# --------------------------------------------------------------------------- #

MIN_LEN_VALIDATION = 60

_VALIDATE_ACTION_RE = re.compile(
    r"reproduc|repro\b|reprodukov|overil|overen|skontrolov|verified|"
    r"validated|confirm|potvrd|checked\s+(?:the\s+)?(?:live|current)|"
    r"tested\s+live|preveril",
    re.IGNORECASE,
)
_VALIDATE_EVIDENCE_RE = re.compile(
    r"still\s+(?:valid|real|happens|reproduces)|st[áa]le\s+plat|"
    r"already\s+(?:fixed|resolved|obsolete)|u[žz]\s+(?:opraven|vyrie[šs]en|neplat)|"
    r"obsolete|zastaran|no\s+longer\s+(?:valid|happens)|live\s+repro|"
    r"na[žz]ivo|test\s+(?:still\s+)?fails?|test\s+passes?|current\s+code",
    re.IGNORECASE,
)


def classify_validation_comment(body):
    """Heuristic verdict for #213: does `body` plausibly carry a validation
    ACTION (what you did to check) plus a validation EVIDENCE/verdict (what
    you observed)? Returns `(ok: bool, reason: str)`, same contract as
    `classify_design_comment`."""
    text = (body or "").strip()
    if len(text) < MIN_LEN_VALIDATION:
        return False, "too short (%d chars, need >= %d)" % (
            len(text), MIN_LEN_VALIDATION)
    missing = []
    if not _VALIDATE_ACTION_RE.search(text):
        missing.append("validation action")
    if not _VALIDATE_EVIDENCE_RE.search(text):
        missing.append("validation evidence")
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "ok"


# --------------------------------------------------------------------------- #
# #214 -- review classifier: does `body` plausibly carry the review PASS
# (that a review happened) plus its RESULT (findings/clean + a fixing
# commit)? Same shape family as the two above.
# --------------------------------------------------------------------------- #

MIN_LEN_REVIEW = 60

_REVIEW_ACTION_RE = re.compile(
    r"/review\b|requesting-code-review|code\s*review|\breview(?:ed|ing)?\b|"
    r"kontrol[a-z]*\s+k[óo]du|revidova",
    re.IGNORECASE,
)
# The hex-run alternative REQUIRES a commit/sha/fix keyword within a short
# distance BEFORE the hex-looking token -- a bare `\b[0-9a-f]{7,40}\b`
# (an earlier draft) also matched any 7+ digit decimal number (digits are
# a subset of hex chars) and any English/Slovak word spelled entirely with
# a-f letters at that length ("defaced", "effaced"), an adversarial-review
# finding. Same `keyword + up to N chars + token` shape as notify.py's own
# `_SHA_PER_ISSUE_RE`.
_REVIEW_SHA_RE = re.compile(
    r"(?:\bcommit\b|\bsha\b|fix(?:ed|ing)?(?:\s+it)?(?:\s+in)?|"
    r"opraven[éeí]?\s+v(?:\s+commit)?)"
    r".{0,20}?\b[0-9a-f]{7,40}\b",
    re.IGNORECASE,
)
_REVIEW_RESULT_RE = re.compile(
    r"0\s*\U0001F534|\U0001F534|\U0001F7E1|\U0001F535|clean\b|"
    r"no\s+(?:findings|issues)|[žz]iadne\s+n[áa]lezy",
    re.IGNORECASE,
)


def classify_review_comment(body):
    """Heuristic verdict for #214: does `body` plausibly carry a review
    ACTION plus a review RESULT (finding counts, "clean", or a fixing
    commit sha)? Returns `(ok: bool, reason: str)`, same contract."""
    text = (body or "").strip()
    if len(text) < MIN_LEN_REVIEW:
        return False, "too short (%d chars, need >= %d)" % (
            len(text), MIN_LEN_REVIEW)
    missing = []
    if not _REVIEW_ACTION_RE.search(text):
        missing.append("review action")
    if not (_REVIEW_RESULT_RE.search(text) or _REVIEW_SHA_RE.search(text)):
        missing.append("findings/fix evidence")
    if missing:
        return False, "missing: " + ", ".join(missing)
    return True, "ok"


# Matches a commit-message issue reference: `(#41)`, `Closes #41`, `#41:`,
# `#137/#139` (this repo's own real "docs: entry for #137/#139" shape) --
# a `#` preceded by start-of-text/whitespace/opening-paren/slash, followed
# by digits and a non-digit (or end). Deliberately simple (word-bounded regex,
# not a full shell/quote-aware parse) -- see
# `hooks/block-commit-without-design.sh` for why that simplification is
# safe here: the cost of a false positive/negative in either direction is
# low (an over-strict block costs one extra `gh issue comment`; a missed
# reference just leaves the gate silent for that commit, exactly like any
# other "unmeasurable -> never guess" case in this repo).
ISSUE_REF_RE = re.compile(r"(?:^|[\s(/])#([0-9]+)\b")


def issue_refs(text):
    """Every distinct issue number referenced in `text`, in first-seen
    order. Empty list when there is nothing to check."""
    seen = []
    for m in ISSUE_REF_RE.finditer(text or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


# --------------------------------------------------------------------------- #
# #206 -- an already-CLOSED issue reference no longer requires a design
# marker. The same syntactic shapes (`#N`, `(#N)`, comma/slash-separated
# lists) are used BOTH for "the ticket this commit is for" AND for a
# historical/context reference (the reported false-block: a commit prose
# citing "(owner decisions #1734/#1766)", both long-closed tickets) -- no
# purely positional/syntactic rule can tell the two apart (this repo's own
# "(#N)" convention for the commit's own ticket is syntactically identical
# to the false-positive shape). A CLOSED issue, by definition, is
# overwhelmingly unlikely to be "the ticket I'm designing for right now",
# so GitHub's own issue state is the discriminator.
# --------------------------------------------------------------------------- #

def _gh_issue_state(n, cwd, timeout=8):
    """GitHub's own state ("OPEN"/"CLOSED") for issue #`n`, resolved via
    `gh` run with `cwd` as the working directory so it auto-detects the
    repo -- the same pattern `hooks/block-fork-no-merge-issue-close.sh`
    already uses for its own live `gh` call. Returns None on ANY failure
    (gh missing, no network, auth issue, timeout, unexpected output) --
    unmeasurable, never guessed, and NEVER raises."""
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(n), "--json", "state", "--jq", ".state"],
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    state = (out.stdout or "").strip().upper()
    return state if state in ("OPEN", "CLOSED") else None


# --------------------------------------------------------------------------- #
# #310 -- stale scratch-msgfile quarantine. A worker composing
# `cat > path <<EOF ... EOF && git commit -F path` in ONE Bash call has the
# WHOLE compound denied atomically by block-commit-without-design.sh when the
# gate fires -- nothing in it executes, so the intended `cat >` write never
# lands. A file already sitting at `path` from an unrelated earlier attempt
# survives untouched, and a LATER bare `git commit -F path` retry carries no
# issue-number TEXT at all (the reference lives only inside the file's own
# content, invisible to ISSUE_REF_RE), so the gate never blocks IT either --
# the stale content commits silently. See the #310 design comment on the
# issue for the full incident and the two rejected alternatives (re-printing
# the file's content, and a stateful "provably predates the command"
# freshness marker).
# --------------------------------------------------------------------------- #

# Any `>`/`>>` redirect target -- the standard `cat > path <<EOF` recipe.
# Deliberately simple (documented limitation, same "cost of a miss is low"
# tradeoff ISSUE_REF_RE already makes elsewhere in this module): no
# shell-quote/heredoc-body awareness.
_REDIR_RX = re.compile(
    r"(?:^|[\s;&|(])(?:\d?>>?)\s*(['\"]?)([^\s'\">|;&]+)\1")
# `git commit` specifically -- the whitespace/end-of-string lookahead (not a
# bare `\b`) is what keeps a DIFFERENT subcommand sharing the same prefix
# (`git commit-graph write --file X`) from being mistaken for `git commit`.
_COMMIT_FILE_RX = re.compile(
    r"git\s+commit(?=\s|$)[^\n;&|]*?(?:-F|--file)(?:=|\s+)"
    r"(['\"]?)([^\s'\"><|;&]+)\1")


def stale_msgfile_candidates(cmd):
    """Paths this SAME command text tries to (over)WRITE (a `>`/`>>`
    redirect target) AND ALSO passes to `git commit -F`/`--file` -- the
    exact write-then-consume pattern that leaves a stale file behind
    whenever the whole compound gets blocked before any of it executes.
    First-seen order (sorted); empty list when there is nothing to
    quarantine."""
    text = cmd or ""
    written = {os.path.normpath(m.group(2)) for m in _REDIR_RX.finditer(text)}
    committed = {os.path.normpath(m.group(2)) for m in _COMMIT_FILE_RX.finditer(text)}
    return sorted(written & committed)


def quarantine_stale_msgfile(path, ts=None):
    """Best-effort: rename `path` aside (a `.stale-<epoch-ms>` sibling,
    never deleted -- the content is kept for inspection, only the ORIGINAL
    path is made to disappear) so a LATER bare `git commit -F path` retry
    fails LOUD (file not found) instead of silently reading content this
    SAME blocked command never got the chance to (re)write. Returns the
    quarantine path on success, else None (nothing existed there, it is a
    directory, or the rename failed) -- NEVER raises."""
    try:
        if not os.path.isfile(path):
            return None
        stamp = int((ts if ts is not None else time.time()) * 1000)
        dest = "%s.stale-%d" % (path, stamp)
        os.rename(path, dest)
        return dest
    except OSError:
        return None


def required_refs(refs, cwd, state_of=None):
    """Filter `refs` (issue numbers with no marker yet) down to the ones
    that STILL require a design-comment marker: drop any that are already
    CLOSED on GitHub at commit time. Fails toward STILL REQUIRED (never
    drops a ref whose state can't be determined) -- gh missing, no
    network, timeout, unexpected output all keep the ref in the required
    set, which is also exactly this gate's pre-#206 unconditional
    behaviour, so this is a pure narrowing of when the gate fires, never a
    widening. `state_of` defaults to `_gh_issue_state`; tests inject a
    stub so this stays pure/offline."""
    state_of = state_of or _gh_issue_state
    return [n for n in refs if state_of(n, cwd) != "CLOSED"]
