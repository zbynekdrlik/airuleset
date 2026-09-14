"""gates.design.classifiers -- the comment classifiers for the
design/validated/reviewed/lane-return gate (#136/#213/#214/#414/#844/#877).

Split out of the old top-level design_gate.py (#1020) with ZERO behaviour
change; design_gate.py is now a deprecated re-export shim. Every classifier is a
SHAPE check (bilingual keyword families), never a proof of engineering quality --
see each function's own docstring and the module history below for the incidents
that shaped each regex belt.
"""
import re

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


# --------------------------------------------------------------------------- #
# #844 -- LANE-RETURN classifier: does `body` carry a worktree worker's durable
# return artifact -- a `LANE-RETURN:` marker plus a branch name and a head sha?
# Same shape-not-content contract as the classifiers above.
# --------------------------------------------------------------------------- #

MIN_LEN_LANE_RETURN = 40

_LANE_RETURN_MARKER_RE = re.compile(r"\bLANE-RETURN\b\s*:", re.IGNORECASE)
_LANE_RETURN_BRANCH_RE = re.compile(
    r"worktree-(?:agent|issue)-\w+|branch\s*:|vetva\s*:", re.IGNORECASE)
_LANE_RETURN_HEAD_RE = re.compile(
    r"\bhead\b|\bsha\b|\bcommit\b|\b[0-9a-f]{7,40}\b", re.IGNORECASE)


def classify_lane_return_comment(body):
    """Heuristic verdict for #844: does `body` carry a `LANE-RETURN:` block plus
    a branch reference AND a head/sha reference? Returns `(ok: bool, reason:
    str)`, same contract. A SHAPE check (this module's documented limit) -- it
    proves the durable-return artifact was posted, never that its head sha is
    correct (that is the SubagentStop gate's cross-check against the returned
    branch)."""
    text = (body or "").strip()
    if len(text) < MIN_LEN_LANE_RETURN:
        return False, "too short (%d chars, need >= %d)" % (
            len(text), MIN_LEN_LANE_RETURN)
    if not _LANE_RETURN_MARKER_RE.search(text):
        return False, "missing: LANE-RETURN: marker"
    missing = []
    if not _LANE_RETURN_BRANCH_RE.search(text):
        missing.append("branch")
    if not _LANE_RETURN_HEAD_RE.search(text):
        missing.append("head sha")
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
# #692 -- the digit run is CAPPED at 5 (refs 1..99999): an ALL-DIGIT CSS
# hex colour in a commit message (6-digit RGB `#333333`, 8-digit RGBA
# `#33333380`) is otherwise indistinguishable from an issue ref and, being
# nonexistent as an issue, hard-blocks via the #206 fail-toward-required
# path (live #691 incident). Backtracking cannot carve a partial 5-digit
# ref out of a longer run -- every shorter candidate fails the trailing \b
# against the next digit. Letter-containing colours (`#3B78FF`) never
# matched at all (\b cannot sit between a digit and a hex LETTER). The
# accepted trade-off: any 6+-digit RUN is dropped -- a ref above #99999
# (no fleet repo is within 20x of that) and equally a zero-padded small
# ref like `#000691` (not a fleet shape) -- leaving the gate silent for
# it, the documented low-cost direction, while the over-match direction
# was a proven live hard block on every CSS-touching commit. 3/4-digit
# all-digit shorthand (`#333`, RGBA `#3338`) stays extracted: by shape it
# IS a plausible ref, and any rule dropping it would open a real-ref
# bypass; #206 handles the real-and-closed case.
ISSUE_REF_RE = re.compile(r"(?:^|[\s(/\"'\[])#([0-9]{1,5})\b")


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
# #877 -- Shared-benefit: line. Owner directive (2026-09-05, the SK holidays
# incident): every design comment must carry a `Shared-benefit:` disposition
# — "the mechanism is shared (where)" OR "single-client (why)" OR
# "n/a — <reason>". UNCONDITIONAL (no trivial exemption): shared-benefit
# risk ANTI-CORRELATES with design complexity — the incident was a trivial
# data seed scoped to one client when the data was nationwide. Same shape-
# not-content contract as every other classifier in this module.
# --------------------------------------------------------------------------- #

_SB_LINE_RE = re.compile(
    # Leading-bullet prefix class mirroring _TRIAGE_LINE_RE / _ARCH_HEADER_RE
    # (the MINOR-3 lesson).
    r"(?m)^[ \t>*#-]*\**[ \t]*Shared-?[ \t]?benefit\**[ \t]*:[ \t]*(?P<val>.+?)[ \t]*$",
    re.IGNORECASE,
)
# A bare negative token with no trailing reason — the exact "rubber-stamp"
# shape `completion-report.md`'s `✅ Výstup:` line (#446) also rejects.
_SB_BARE_NA_RE = re.compile(
    r"^(?:n/?a|nie|no|none|-)$",
    re.IGNORECASE,
)


def classify_shared_benefit(body):
    """Heuristic verdict for #877: does `body` carry a `Shared-benefit:`
    (or `Shared benefit:`) line with a non-empty, non-bare-n/a value?
    Returns `(ok: bool, reason: str)`, same contract as the other
    classifiers in this module. A SHAPE check — it verifies the line is
    present and not a bare dismissal, never whether the engineering
    judgment behind the disposition is actually correct."""
    text = (body or "").strip()
    m = _SB_LINE_RE.search(text)
    if not m:
        return False, ("missing: Shared-benefit: line "
                       "(disposition — shared/single-client/n/a — reason)")
    val = m.group("val").strip()
    # Strip bold markers leaking from `**Shared-benefit:** n/a` (Y1).
    val = val.strip("*").strip()
    if not val:
        return False, "Shared-benefit: value is empty"
    # Bare negative (n/a, no, nie, none, -) with no trailing reason.
    if _SB_BARE_NA_RE.match(val):
        return False, "Shared-benefit: bare n/a without a reason"
    # Split on reason separators (— - , ;): stem before + tail after.
    parts = re.split(r"\s*[—\-,;]\s*", val, maxsplit=1)
    stem = parts[0].strip()
    tail = parts[1].strip() if len(parts) > 1 else ""
    if _SB_BARE_NA_RE.match(stem) and len(tail) < 5:
        return False, "Shared-benefit: bare n/a without a reason"
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
