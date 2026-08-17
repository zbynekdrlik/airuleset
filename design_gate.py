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
# The boundary class includes the shell QUOTE chars: the commit-gate hook
# scans the raw `git commit -m "<subject>"` COMMAND string, so a subject
# whose ref is its very first token arrives as `"#433 ...` — with only
# whitespace/(/ in the class that ref was invisible and the gate silently
# passed a markerless worker commit (batch-32 corpus-replay catch,
# 2026-08-16). `&#123`-style HTML entities stay excluded (no `&` in class).
ISSUE_REF_RE = re.compile(r"(?:^|[\s(/\"'\[])#([0-9]+)\b")


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
    write-then-consume SIGNATURE that leaves a stale file behind whenever
    the whole compound gets blocked before any of it executes. First-seen
    order (sorted); empty list when there is nothing to quarantine.

    Known, deliberate residual (adversarial-review finding #1, #310):
    NEITHER regex requires its match to be REAL shell syntax outside
    quotes, and the two are scanned INDEPENDENTLY -- so a `-m "..."`
    message merely PROSE-DESCRIBING the pattern (e.g. documenting this
    very bug: "cat > README.md ... git commit -F README.md") satisfies
    both just as well as genuine shell syntax, with no requirement that
    the two matches even belong to the same statement. This function
    stays a pure text-signature detector on purpose (no filesystem/git
    access, same offline-testable shape every other regex in this module
    has) -- the caller is what closes the DANGEROUS half of this false-
    positive class: `is_git_tracked()` refuses to let anything git
    already tracks be quarantined, so the worst a decoy match can do is
    move aside a path that was already UNTRACKED -- never a real project
    file. See hooks/block-commit-without-design.sh for the wiring."""
    text = cmd or ""
    written = {os.path.normpath(m.group(2)) for m in _REDIR_RX.finditer(text)}
    committed = {os.path.normpath(m.group(2)) for m in _COMMIT_FILE_RX.finditer(text)}
    return sorted(written & committed)


def is_git_tracked(path, cwd):
    """True iff `path` is a file SOME repo already tracks -- a genuine
    scratch msgfile is NEVER tracked, so this is the discriminator
    stale_msgfile_candidates' own docstring promises: refuse to quarantine
    anything that could plausibly be a real project file. Fails toward
    TRACKED (never quarantine) on ANY unmeasurable result -- git missing,
    not a repo, timeout, unexpected output -- the same "never guess toward
    the more consequential action" direction `required_refs` already uses
    one function up, just applied to a destructive action instead of a
    block decision. `git ls-files --error-unmatch` has FOUR outcomes, not
    two: rc=0 (tracked BY cwd's repo), rc=1 (git ran fine and specifically
    confirmed the path is untracked -- "did not match any file(s) known to
    git", which can only happen for a path genuinely INSIDE cwd's own
    working tree), rc=128 with "is outside repository" (the queried path is
    not even INSIDE `cwd`'s working tree at all -- every real worker
    scratchpad file, under /tmp, hits this case), and anything else (128
    for "not a git repository" -- `cwd` itself isn't a repo, genuinely
    unmeasurable -- or any other failure/timeout).

    #431-review F1 (live-triggered, empirically confirmed): "outside cwd's
    repo" does NOT mean "untracked anywhere" -- a worktree's own `cwd`
    makes its sibling MAIN checkout (they share one `.git`, per two-branch-
    workflow.md/#317, but each worktree has its OWN separate working tree)
    read as "is outside repository" too, and so does any file genuinely
    tracked by a COMPLETELY DIFFERENT repo. Naively treating rc=128
    "outside repository" as a second confident "not tracked" signal would
    let `quarantine_stale_msgfile` rename away a real, valuable,
    git-tracked project file the instant it merely lives outside THIS
    worktree's own directory -- exactly the "moved a real project file"
    disaster `stale_msgfile_candidates`' own docstring promises can never
    happen. Fix: on rc=128 "outside repository", ask whichever repo (if
    any) actually OWNS `path`'s containing directory -- via
    `git -C <dirname(path)> rev-parse --show-toplevel` -- rather than
    assuming "outside cwd's repo" means "untracked by every repo". If NO
    repo owns that directory at all (rev-parse itself fails: rc!=0 or
    empty stdout), `path` is genuinely outside version control anywhere --
    the real /tmp-scratchpad case #431 exists to fix -- confidently
    untracked. If a repo DOES own it, re-query `--error-unmatch` against
    THAT repo and fail toward TRACKED on anything but a confirmed rc==1
    (mirrors the original outer check's own fail-safe direction one level
    down, rather than inventing a new one)."""
    # #431-review F2: "is outside repository" is one of git's own
    # gettext-translated messages -- on a box whose git has NLS catalogs
    # installed AND runs under a non-English locale, the substring match
    # below would silently miss and this whole rc=128 branch would revert
    # to the pre-#431 "assume tracked, never quarantine" behaviour with NO
    # signal that the feature had disappeared. Force the untranslated
    # (POSIX "C") locale on the ONE call whose stderr text this function
    # actually parses -- the two calls below only ever read a returncode
    # or a raw path from stdout, never a translated message.
    git_env = dict(os.environ)
    git_env["LC_ALL"] = "C"
    git_env["LANGUAGE"] = ""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path],
            cwd=cwd, capture_output=True, text=True, timeout=8, env=git_env,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if out.returncode == 1:
        return False
    if out.returncode == 128 and "is outside repository" in (out.stderr or ""):
        try:
            owner_dir = os.path.dirname(path) or "."
            top = subprocess.run(
                ["git", "-C", owner_dir, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=8,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        owner = top.stdout.strip()
        if top.returncode != 0 or not owner:
            return False
        try:
            owned = subprocess.run(
                ["git", "-C", owner, "ls-files", "--error-unmatch", "--", path],
                capture_output=True, text=True, timeout=8,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return owned.returncode != 1
    return True


def quarantine_stale_msgfile(path, ts=None):
    """Best-effort: rename `path` aside (a `.stale-<epoch-ms>` sibling,
    never deleted -- the content is kept for inspection, only the ORIGINAL
    path is made to disappear) so a LATER bare `git commit -F path` retry
    fails LOUD (file not found) instead of silently reading content this
    SAME blocked command never got the chance to (re)write. A destination
    collision (adversarial-review finding #2: two candidates quarantined
    in the same millisecond) is never clobbered -- a numeric suffix is
    appended until the destination is free. Returns the quarantine path on
    success, else None (nothing existed there, it is a directory, or the
    rename failed) -- NEVER raises. The CALLER is responsible for checking
    `is_git_tracked()` first; this function only ever moves a file, it has
    no opinion on whether that was the right file."""
    try:
        if not os.path.isfile(path):
            return None
        stamp = int((ts if ts is not None else time.time()) * 1000)
        dest = "%s.stale-%d" % (path, stamp)
        n = 0
        while os.path.lexists(dest):
            n += 1
            dest = "%s.stale-%d-%d" % (path, stamp, n)
        os.rename(path, dest)
        return dest
    except OSError:
        return None


def ensure_stale_pattern_excluded(cwd):
    """Best-effort, idempotent: appends a `*.stale-*` line to the repo's
    LOCAL (never committed, never git-tracked) `.git/info/exclude`, so a
    quarantined msgfile never gets swept into `git add -A` / shown as an
    untracked file waiting to be staged (adversarial-review finding #3,
    #310). Resolved via `git rev-parse --git-common-dir` -- NOT a naive
    `cwd/.git` join -- so this lands in the SHARED exclude file even when
    `cwd` is a linked WORKTREE checkout (`.git` there is a FILE pointing
    elsewhere, exactly this repo's own autopilot-worker dispatch shape).
    Pure hygiene, never load-bearing for the actual quarantine: a missing/
    unwritable/non-repo `cwd` is silently skipped, never raises."""
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                              cwd=cwd, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return
    if out.returncode != 0:
        return
    git_common_dir = (out.stdout or "").strip()
    if not git_common_dir:
        return
    if not os.path.isabs(git_common_dir):
        git_common_dir = os.path.join(cwd, git_common_dir)
    pattern = "*.stale-*"
    try:
        info_dir = os.path.join(git_common_dir, "info")
        os.makedirs(info_dir, exist_ok=True)
        exclude_path = os.path.join(info_dir, "exclude")
        existing_lines = []
        if os.path.isfile(exclude_path):
            with open(exclude_path, encoding="utf-8", errors="replace") as fh:
                existing_lines = fh.read().splitlines()
        if pattern not in existing_lines:
            with open(exclude_path, "a", encoding="utf-8") as fh:
                fh.write(pattern + "\n")
    except OSError:
        return


# --------------------------------------------------------------------------- #
# #414 -- SOTA architecture: tightened design-gate. TWO NEW, DELIBERATELY
# SEPARATE classifiers -- never folded into `classify_design_comment` above.
# That function has THREE independent consumers besides the live gate
# (`scripts/measure_design_compliance.py`'s historical Deliverable-1 corpus
# measurement, `scripts/replay_design_gate_commit_corpus.py`, and this
# module's own pre-existing ~30 tests), all of which depend on its CURRENT,
# STABLE meaning -- retroactively tightening it would silently change what a
# measurement over comments posted long before this ticket existed means.
# `hooks/post-record-design-comment.sh` is the ONLY call site that combines
# either of these with `classify_design_comment`, and only for kind=="design"
# -- so an already-written marker file is NEVER retro-invalidated (nothing
# ever re-classifies it) and every OTHER consumer of `classify_design_comment`
# is entirely unaffected by either function below existing. See the #414
# design comment on the issue for the full rationale + rejected alternatives.
# --------------------------------------------------------------------------- #

MIN_LEN_ARCH = 40

_ARCH_HEADER_RE = re.compile(
    # #414-review MINOR-3: an optional leading "-" bullet (this fleet's
    # dominant markdown style) before either header form.
    r"(?m)^[ \t]*-?[ \t]*(?:"
    r"#{1,6}[ \t]*\**[ \t]*(?:Architekt(?:[uú]ra)|Architecture)\**[ \t]*:?"
    r"|"
    r"\**[ \t]*(?:Architekt(?:[uú]ra)|Architecture)[ \t]*\**[ \t]*:"
    r")",
    re.IGNORECASE,
)
_ARCH_STRUCTURE_RE = re.compile(
    r"\b(?:[šs]trukt[uú]r\w*|struktur\w*|topol[oó]gi\w*|structure\w*|topology\w*)\b",
    re.IGNORECASE,
)
_ARCH_FRAMEWORK_OR_WHYNOT_RE = re.compile(
    r"\bframework\w*|\br[áa]mec\w*|kni[žz]nic\w*|\blibrar(?:y|ies)\b|"
    r"nesed[íi]\w*|nehod[íi]\w*|nevhod\w*|no\s+framework|none\s+fit|doesn'?t\s+fit|"
    r"existuj[uú]c\w*\s+(?:rie[šs]enie|n[áa]stroj|framework)|"
    r"existing\s+(?:solution|tool|framework)",
    re.IGNORECASE,
)


def classify_architecture_section(body):
    """Heuristic verdict for #414: does `body` carry an `Architektúra:` (or
    `Architecture:`) section that plausibly names STRUCTURE/topology AND
    EITHER the framework used OR an evidenced why-none-fits reasoning?
    Returns `(ok: bool, reason: str)`, same contract as the classifiers
    above. A SHAPE check, same documented limitation as the rest of this
    module: it can judge that the right CONCEPTS were named, never that the
    engineering judgment behind them is actually correct."""
    text = (body or "").strip()
    if len(text) < MIN_LEN_ARCH:
        return False, "too short for an architecture section (%d chars, need >= %d)" % (
            len(text), MIN_LEN_ARCH)
    if not _ARCH_HEADER_RE.search(text):
        return False, "missing: Architektúra: (or Architecture:) section header"
    missing = []
    if not _ARCH_STRUCTURE_RE.search(text):
        missing.append("structure/topology")
    if not _ARCH_FRAMEWORK_OR_WHYNOT_RE.search(text):
        missing.append("framework used or why-none-fits")
    if missing:
        return False, "Architektúra: section missing: " + ", ".join(missing)
    return True, "ok"


# --------------------------------------------------------------------------- #
# #414 -- Triage: line + (for a non-trivial ticket) 2-3 considered
# approaches with trade-offs. Restores the interactive-`/brainstorming`-era
# design depth the owner reported degrading to a single paragraph once
# autopilot took over ("per-ticket tunel -- nikto nedržal celok"). A
# TRIVIAL ticket needs nothing beyond the Triage: line itself -- CYCLE step
# 2's own already-settled "depth scales with the problem" principle, which
# this extends rather than replaces.
# --------------------------------------------------------------------------- #

MIN_LEN_TRIAGE_TRIVIAL = MIN_LEN            # same floor as classify_design_comment
MIN_LEN_TRIAGE_NONTRIVIAL = 400             # 2-3 approaches + trade-offs cannot honestly
                                             # fit under ~400 chars; a genuinely terse
                                             # trivial write-up clears the lower floor easily

_TRIAGE_LINE_RE = re.compile(
    # #414-review MINOR-3: "-" added to the prefix class for the same
    # leading-bullet reason as _ARCH_HEADER_RE above.
    r"(?m)^[ \t>*#-]*\**[ \t]*Triage\**[ \t]*:[ \t]*(?P<cls>.+?)[ \t]*$",
    re.IGNORECASE,
)
# Non-trivial checked FIRST -- "netriviálne"/"non-trivial" both CONTAIN the
# substring "trivi(al)", so classifying trivial-first would misread them.
# #414-review MAJOR-1: a NEGATED trivial declaration ("not trivial", "nie
# (je to) triviálne") ALSO contains that same bare substring -- without an
# explicit negation alternative here, it would fall through and match
# _TRIAGE_TRIVIAL_RE instead, silently waiving the whole 2-3-approaches
# depth requirement on entirely natural phrasing in both languages.
#
# #514: the non-trivial signal is split into TWO regexes because a genuinely
# trivial line's descriptive tail may NEGATE a complexity keyword ("Triage:
# trivial -- ... no cross-cutting change") and that must NOT flip it to
# non-trivial -- while an AFFIRMATIVE keyword anywhere ("scoped fix -- but
# cross-cutting") MUST still classify non-trivial (a depth gate fails SAFE,
# never silently waiving depth). See `_triage_class` + the ticket design comment.
#
# _EXPLICIT: full non-trivial VERDICTS, incl. the MAJOR-1 negated-trivial forms
# (they ARE the non-trivial signal), matched wherever they appear -- never
# negation-guarded.
_TRIAGE_NONTRIVIAL_EXPLICIT_RE = re.compile(
    r"not\s+trivial|nie\s+(?:je\s+(?:to\s+)?)?trivi[aá]ln\w*|"
    r"non-?\s?trivial|netrivi[aá]ln\w*",
    re.IGNORECASE,
)
# _KEYWORD: complexity keywords, counted ONLY when AFFIRMATIVE. Python's re has
# no variable-width lookbehind, so a preceding negation is captured as an
# OPTIONAL `neg` group and `_triage_class` rejects any match whose `neg` fired.
# The guard is IMMEDIATE-adjacency only (bounded on purpose): an intervening
# word ("without ANY cross-cutting") is left as a fail-safe over-block, because
# allowing intervening words would misread the non-trivial "nie, je to
# komplexná" ("no[t trivial], it IS complex") as negated -- the dangerous
# direction. The negation set is English no/not/without + Slovak nie/bez/žiadn*.
_TRIAGE_NONTRIVIAL_KEYWORD_RE = re.compile(
    r"(?P<neg>\b(?:no|not|without|nie|bez|žiadn\w*)\s+)?"
    # English keywords kept SYMMETRIC with the Slovak set (#514 follow-up):
    # complex<->komplexn, architectural<->architektonick (cross-cutting<->krížov
    # and design-heavy<->designov already had both sides).
    r"(?:design-?heavy|designov[ýy]\w*|architektonick\w*|architectural\w*|"
    r"komplexn\w*|complex\w*|cross-?cutting|kr[íi][žz]ov\w*|zlo[žz]it\w*)",
    re.IGNORECASE,
)
_TRIAGE_TRIVIAL_RE = re.compile(
    r"\btrivial\b|trivi[aá]ln\w*|jednoduch\w*|\bscoped\b|drobn\w*",
    re.IGNORECASE,
)
_APPROACH_MARKER_RE = re.compile(
    r"\b(?:Approach|Option|Variant|Pr[íi]stup|Mo[žz]nos[ťt]\w*)\s*#?\s*([1-3])\b",
    re.IGNORECASE,
)
_TRADEOFF_RE = re.compile(
    r"trade-?\s?off\w*|kompromis\w*|nev[ýy]hod\w*|v[ýy]hod\w*|\bpros\b|\bcons\b|"
    r"za a proti|oproti|on the other hand|na druhej strane",
    re.IGNORECASE,
)


def _triage_class(text):
    """The `Triage:` class named in `text` ("trivial" / "non-trivial" /
    `None`) -- extracted, never depth-checked. SHARED by
    `classify_triage_and_approaches` (below) and its public single-purpose
    wrapper `triage_class` (#428), so the two can never drift on what a
    given body's class actually is. `text` is assumed already `(body or
    "").strip()`-ed by the caller."""
    m = _TRIAGE_LINE_RE.search(text)
    if not m:
        return None
    value = m.group("cls")
    # #514: an EXPLICIT non-trivial verdict (incl. the MAJOR-1 negated-trivial
    # forms) OR any AFFIRMATIVE complexity keyword anywhere in the tail means
    # non-trivial -- a depth gate fails SAFE (over-block), never silently
    # waiving depth on a hedge-then-reveal line. A NEGATED keyword ("no
    # cross-cutting", "žiadna krížová") is not an affirmative signal and must
    # NOT flip a trivial line -- that reverse of MAJOR-1 was the reported bug.
    if _TRIAGE_NONTRIVIAL_EXPLICIT_RE.search(value):
        return "non-trivial"
    for km in _TRIAGE_NONTRIVIAL_KEYWORD_RE.finditer(value):
        if not km.group("neg"):
            return "non-trivial"
    if _TRIAGE_TRIVIAL_RE.search(value):
        return "trivial"
    return None


def triage_class(body):
    """Public, #428: just the `Triage:` class named in `body`
    ("trivial"/"non-trivial"/`None`) -- no depth/length checks, unlike
    `classify_triage_and_approaches`. Lets a caller (`hooks/post-record-
    design-comment.sh`) decide whether the Architektúra: requirement
    applies WITHOUT re-running (or duplicating) the full triage classifier
    -- a TRIVIAL ticket needs nothing beyond its own Triage: line, per
    `classify_triage_and_approaches`'s own doc comment below."""
    return _triage_class((body or "").strip())


def classify_triage_and_approaches(body):
    """Heuristic verdict for #414: does `body` carry a `Triage:` line
    naming trivial/non-trivial and -- ONLY for the non-trivial class -- at
    least 2 DISTINCT numbered approaches (Approach/Option/Variant/Prístup/
    Možnosť 1-3) plus trade-off language? Returns `(ok, reason)`, same
    contract as the other classifiers in this module. Fails CLOSED on a
    `Triage:` value that names neither class -- the fix (reword one line)
    is cheap, and guessing which class was meant is not this gate's job."""
    text = (body or "").strip()
    m = _TRIAGE_LINE_RE.search(text)
    if not m:
        if len(text) < MIN_LEN_TRIAGE_TRIVIAL:
            return False, "too short (%d chars, need >= %d)" % (
                len(text), MIN_LEN_TRIAGE_TRIVIAL)
        return False, "missing: Triage: line (trivial / non-trivial)"

    cls = _triage_class(text)
    if cls is None:
        return False, "Triage: value %r names neither trivial nor non-trivial" % m.group("cls")

    if cls == "trivial":
        if len(text) < MIN_LEN_TRIAGE_TRIVIAL:
            return False, "too short (%d chars, need >= %d)" % (
                len(text), MIN_LEN_TRIAGE_TRIVIAL)
        return True, "ok (trivial)"

    # non-trivial -- needs the fuller depth.
    if len(text) < MIN_LEN_TRIAGE_NONTRIVIAL:
        return False, "non-trivial design comment too short (%d chars, need >= %d)" % (
            len(text), MIN_LEN_TRIAGE_NONTRIVIAL)
    nums = set(mm.group(1) for mm in _APPROACH_MARKER_RE.finditer(text))
    missing = []
    if len(nums) < 2:
        missing.append("2-3 distinct numbered approaches (Approach/Prístup/Možnosť/Variant 1-3)")
    if not _TRADEOFF_RE.search(text):
        missing.append("trade-off comparison")
    if missing:
        return False, "non-trivial ticket missing: " + ", ".join(missing)
    return True, "ok (non-trivial, %d approaches)" % len(nums)


# --------------------------------------------------------------------------- #
# #414 -- reject-reason I/O: purely diagnostic, NEVER gates anything by
# itself. Lets `hooks/block-commit-without-design.sh` tell a blocked worker
# WHAT is still missing from its last posted comment, instead of the bare
# "no design comment posted yet". A SIBLING directory to `design-posted/`
# (never mixed in) so a reject can never be mistaken for a delivered
# marker, and `marker_exists`/`read_marker` never need to learn about it.
# --------------------------------------------------------------------------- #

_REJECT_DIRNAME = "design-rejected"


def reject_dir():
    return os.path.join(_claude_dir(), _REJECT_DIRNAME)


def reject_path(repo_key, issue, kind="design"):
    safe = re.sub(r"[^A-Za-z0-9._#-]", "_", kind + ":" + marker_key(repo_key, issue))
    return os.path.join(reject_dir(), safe)


def write_reject_reason(repo_key, issue, reason, kind="design"):
    """Best-effort record of the MOST RECENT classification failure for
    `<repo_key>#<issue>` / `kind` -- overwritten on every failed attempt
    (only the latest matters). Never speculative about anything a caller
    hasn't just observed; never raises; an unwritable ~/.claude just means
    the diagnostic stays unavailable, the gate itself is unaffected."""
    if not repo_key or issue in (None, ""):
        return False
    d = reject_dir()
    try:
        os.makedirs(d, exist_ok=True)
        with open(reject_path(repo_key, issue, kind), "w", encoding="utf-8") as fh:
            fh.write("%s\t%s\n" % (time.time(), reason or ""))
        return True
    except OSError:
        return False


def read_reject_reason(repo_key, issue, kind="design"):
    """The most recently recorded reject reason for `<repo_key>#<issue>` /
    `kind`, or None. Tolerates a malformed/empty file rather than raising."""
    try:
        with open(reject_path(repo_key, issue, kind), encoding="utf-8") as fh:
            body = fh.read(2000).rstrip("\n")
    except OSError:
        return None
    if "\t" not in body:
        return None
    _, _, reason = body.partition("\t")
    return reason if reason else None


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
