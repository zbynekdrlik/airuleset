"""gates.spec -- spec-anchoring core (#1106, owner directive 21.9.2026).

On david1-4 the developer writes a detailed initial plan/spec, tickets are cut
from it, and over weeks the stream drifts -- re-asks questions the spec settled,
implements behaviour the plan excluded -- because no gate ever reads the spec
again. This module is the ONE hermetic home for the durable link ticket -> spec
section, enforced at four existing gates (filing / design / review / question)
plus a per-cycle reconciliation line. The spec ticket stays the truth; a
deviation has exactly one path (owner decision -> `spec-change`).

Every classifier here is a SHAPE check (bilingual token families), the same
contract as gates/design/classifiers.py -- never a proof of correctness. STDLIB
ONLY at import; gates.navody is imported lazily inside the stream reader.
"""
import json
import os
import re
import time

# --------------------------------------------------------------------------- #
# Markdown structure helpers -- fence-aware header/line iteration (#1106 review
# R1#1/#6, R2#5/#6). A `#`-comment inside a ``` code fence is NOT a header, and a
# `Spec:` line inside a fence is NOT a real anchor -- so every structural scan
# skips fenced regions, and header-finding tracks the ATX level so a sub-header
# (`### Sub`) inside a section is never mistaken for the section's boundary.
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r'^[ \t]*```')
_ATX_HEADER_RE = re.compile(r'^(#{1,6})[ \t]+\S')


def iter_headers(text):
    """Yield `(start_offset, end_of_line_offset, level)` for each ATX header
    line (`#{1,6} <text>`, a space REQUIRED after the hashes) that sits OUTSIDE a
    ``` code fence. `level` is the number of leading `#`. The required space is
    the single header definition shared by every consumer, so a no-space `##§2`
    or a `#`-comment is consistently NOT a header (a filer using the design's
    `## §N` template is unaffected)."""
    fence = False
    offset = 0
    for raw in (text or "").splitlines(keepends=True):
        line = raw.rstrip("\n")
        if _FENCE_RE.match(line):
            fence = not fence
        elif not fence:
            m = _ATX_HEADER_RE.match(line)
            if m:
                yield offset, offset + len(line), len(m.group(1))
        offset += len(raw)


def _iter_content_lines(text):
    """Yield each line of `text` that is OUTSIDE a ``` code fence (fence marker
    lines themselves excluded)."""
    fence = False
    for raw in (text or "").splitlines():
        if _FENCE_RE.match(raw):
            fence = not fence
            continue
        if not fence:
            yield raw


# --------------------------------------------------------------------------- #
# Parsing -- the `Spec:` line on a ticket body
# --------------------------------------------------------------------------- #
# An optional leading bullet is tolerated (R2#6: `- Spec: #N §x` must parse for
# parity with the search-anywhere design tokens -- the filing gate is a hard
# PreToolUse block). Per-LINE (no MULTILINE): the caller feeds only lines
# outside code fences (R1#6).
_SPEC_LINE_RE = re.compile(
    r'^[ \t]*(?:[-*][ \t]+)?Spec[ \t]*:[ \t]*(.+?)[ \t]*$', re.IGNORECASE)
# a `#N` optionally followed by a section token (`§2`, `§ 2`, `s2`, `section 2`).
_SPEC_REF_TOKEN_RE = re.compile(
    r'#(\d+)(?:[ \t]+(§[ \t]*\d+|§\S+|s\d+|section[ \t]+\d+))?', re.IGNORECASE)
_SPEC_NONE_RE = re.compile(r'^\s*none\b', re.IGNORECASE)


def _norm_section(tok):
    return re.sub(r'[ \t]+', '', tok) if tok else ""


def parse_spec_line(body):
    """The `Spec:` line's meaning, or None.

    Returns:
      ("ref", [(number:int, section:str), ...]) -- a real spec reference;
      ("none", reason:str)                      -- `Spec: none -- <why>`;
      None                                      -- no `Spec:` line (or a
          malformed one with neither `#N` nor `none` -- the fail-safe
          "missing spec" direction for the filing gate).

    A `Spec:` line inside a ``` code fence is IGNORED (R1#6): only the first
    real, non-fenced `Spec:` line counts."""
    val = None
    for line in _iter_content_lines(body):
        m = _SPEC_LINE_RE.match(line)
        if m:
            val = (m.group(1) or "").strip()
            break
    if val is None:
        return None
    if _SPEC_NONE_RE.match(val):
        reason = re.sub(r'^\s*none\s*[—–:-]*\s*', '', val, flags=re.IGNORECASE)
        return ("none", reason.strip())
    refs = []
    for rm in _SPEC_REF_TOKEN_RE.finditer(val):
        refs.append((int(rm.group(1)), _norm_section(rm.group(2))))
    if refs:
        return ("ref", refs)
    return None


def ticket_has_spec(body):
    """True iff the ticket carries a REAL `Spec: #N ...` reference (not
    `Spec: none`, not a missing line)."""
    parsed = parse_spec_line(body)
    return bool(parsed and parsed[0] == "ref")


def spec_refs(body):
    """The [(number, section), ...] the ticket's `Spec:` line names, or []."""
    parsed = parse_spec_line(body)
    return list(parsed[1]) if (parsed and parsed[0] == "ref") else []


# --------------------------------------------------------------------------- #
# Stream fact reader -- `specs: #N, #M` in .claude/streams/<stream>.md
# (the same per-stream fact file #1073 uses for `navody_url:`).
# --------------------------------------------------------------------------- #
_SPECS_FACT_RE = re.compile(r'(?im)^[ \t]*specs[ \t]*:[ \t]*(.+?)[ \t]*$')


def specs_for_stream(cwd, stream, *, read_text=None):
    """The list[int] of open spec ticket numbers the stream's
    `.claude/streams/<stream>.md` `specs:` fact names, or [] (no stream, no
    fact, unreadable file). `read_text(path) -> str|None` is injected in
    tests -- reuses gates.navody's fact-file reader (the #1073 pattern)."""
    if not stream:
        return []
    from gates import navody
    text = navody._read_stream_file(cwd, stream, read_text=read_text)
    if not text:
        return []
    m = _SPECS_FACT_RE.search(text)
    if not m:
        return []
    return [int(x) for x in re.findall(r'#(\d+)', m.group(1))]


# --------------------------------------------------------------------------- #
# (a) FILING GATE -- a stream ticket while its stream has an open spec must
# carry a `Spec:` line (the Scope-gate shape).
# --------------------------------------------------------------------------- #
def filing_spec_block_reason(stream_labels, body, cwd, *, read_text=None):
    """A BLOCK-reason string, or None. BLOCK when the filing carries a
    `stream:<x>` label whose stream has an open `spec` ticket (per its
    `specs:` fact) AND the body carries no valid `Spec:` line. `Spec: #N §x`
    OR `Spec: none -- <why>` satisfies it; no `specs:` fact / no stream label
    -> None (unaffected)."""
    if not stream_labels:
        return None
    open_specs = []
    for lb in stream_labels:
        lb = (lb or "").strip().lower()
        if not lb.startswith("stream:"):
            continue
        stream = lb.split(":", 1)[1]
        open_specs.extend(specs_for_stream(cwd, stream, read_text=read_text))
    open_specs = sorted(set(open_specs))
    if not open_specs:
        return None
    if parse_spec_line(body) is not None:
        return None
    names = ", ".join("#%d" % n for n in open_specs)
    return ("spec-anchor-missing (this stream has an open spec ticket: %s -- "
            "add a `Spec: #N §x` line naming the section this ticket "
            "implements, or `Spec: none -- <why>` if it belongs to no "
            "initiative)" % names)


# --------------------------------------------------------------------------- #
# (b) DESIGN GATE -- a `Spec:` ticket's design must cite the spec section.
# --------------------------------------------------------------------------- #
_SPEC_REF_DESIGN_RE = re.compile(r'Spec-ref[ \t]*:[ \t]*#\d+', re.IGNORECASE)
_SPEC_CONFORM_RE = re.compile(r'Spec-conform[ \t]*:[ \t]*yes', re.IGNORECASE)
_SPEC_DEVIATION_RE = re.compile(r'Spec-deviation[ \t]*:[ \t]*\S', re.IGNORECASE)
_NEEDS_DECISION_RE = re.compile(r'needs-decision', re.IGNORECASE)


def classify_spec_design(design_body, ticket_body):
    """(ok, reason) -- when the TICKET carries a `Spec:` ref, the DESIGN comment
    must carry `Spec-ref: #N` AND either `Spec-conform: yes` or
    `Spec-deviation: <what differs and why>`; a deviation additionally requires
    a `needs-decision` question to the owner in the SAME comment (a deviation is
    never silently implemented). No spec on the ticket -> unchanged (pass)."""
    if not ticket_has_spec(ticket_body):
        return True, "no spec on ticket"
    text = design_body or ""
    if not _SPEC_REF_DESIGN_RE.search(text):
        return (False, "missing Spec-ref: (a Spec: ticket's design must cite "
                "the section it implements, e.g. `Spec-ref: #N §x`)")
    conform = _SPEC_CONFORM_RE.search(text)
    deviation = _SPEC_DEVIATION_RE.search(text)
    if not (conform or deviation):
        return (False, "missing Spec-conform: yes / Spec-deviation: <what "
                "differs and why> (the design must state whether it conforms "
                "to the spec section or deviates)")
    # A `Spec-deviation:` ALWAYS demands a needs-decision question -- even when
    # the comment also (contradictorily) carries `Spec-conform: yes` (R1#7): a
    # deviation is decided (owner -> spec-change), never silently implemented.
    if deviation and not _NEEDS_DECISION_RE.search(text):
        return (False, "Spec-deviation: requires a needs-decision question to "
                "the owner in the same comment -- a deviation is decided (owner "
                "-> spec-change), never silently implemented")
    return True, "ok"


# --------------------------------------------------------------------------- #
# (c) REVIEW GATE -- a `Spec:` ticket's review/RFR must carry `Spec-check:`.
# --------------------------------------------------------------------------- #
_SPEC_CHECK_RE = re.compile(
    r'Spec-check[ \t]*:[ \t]*§?[ \t]*\S+[ \t]*[—–-][ \t]*'
    r'(conform|deviation)', re.IGNORECASE)


def classify_spec_check(review_body):
    """(ok, reason) -- does `review_body` carry a `Spec-check: §x -- conform |
    deviation <ref>` line? The review lens for a `Spec:`-bearing ticket."""
    if _SPEC_CHECK_RE.search(review_body or ""):
        return True, "ok"
    return (False, "missing Spec-check: §x -- conform | deviation <ref> "
            "(a Spec: ticket's review must state whether the diff conforms to "
            "the spec section)")


# --------------------------------------------------------------------------- #
# (d) QUESTION GATE -- settled questions are never re-asked (Check 10).
# --------------------------------------------------------------------------- #
# The `$` anchor is DROPPED (R1#2/R2 review): a decorated header
# `## Settled questions (round 2)` must still be found. `\b` keeps it from
# matching `Settled questionsss`. The `#` count is captured for level-aware
# boundary detection.
_SETTLED_HEADER_RE = re.compile(
    r'(?im)^[ \t]*(#{1,6})[ \t]*Settled[ \t]+questions\b')
_SETTLED_ITEM_RE = re.compile(
    r'(?im)^[ \t]*[-*][ \t]*Q[ \t]*:[ \t]*(.+?)[ \t]*'
    r'(?:→|->)[ \t]*A[ \t]*:[ \t]*(.+?)[ \t]*$')

# Content-token stop-words (casefolded). Deliberately small: the overlap check
# is against the SETTLED question's tokens, so a few connectors dropped keeps a
# re-ask matching while a genuinely different question does not.
_STOP_WORDS = frozenset((
    "a an the this that these those and or but if then so to of in on at by for "
    "from with as is are was were be been being do does did done how what which "
    "who whom whose when where why we you i it its our your my me us they them "
    "he she his her can could should would will shall may might must not no yes "
    "have has had need needs want wants make makes get gets go goes about into "
    "again more most some any all each per via").split())


def content_tokens(text):
    """The casefolded content-token SET of `text` -- split on non-alphanumeric
    (Latin-1 supplement / extended kept), stop-words + single chars removed."""
    raw = re.split(r'[^0-9a-zÀ-ɏ]+', (text or "").casefold())
    return {t for t in raw if len(t) > 1 and t not in _STOP_WORDS}


def parse_settled_questions(spec_body, spec_number=None):
    """The [{q, a, spec}] entries under the spec body's `## Settled questions`
    section (`- Q: ... -> A: ... (date)`), or []. The section runs from its
    header to the next `#`-header (or EOF)."""
    text = spec_body or ""
    m = _SETTLED_HEADER_RE.search(text)
    if not m:
        return []
    hdr_level = len(m.group(1))
    start = m.end()
    # The section ends at the next ATX header (OUTSIDE a code fence) whose level
    # is <= the Settled-questions header's own level -- so a `### Sub` inside the
    # section, or a `#`-comment in a fenced code block, never truncates it
    # (R1#2: a `#`-comment in a code block was dropping later settled Qs).
    end = len(text)
    for hstart, _hend, level in iter_headers(text):
        if hstart >= start and level <= hdr_level:
            end = hstart
            break
    section = text[start:end]
    entries = []
    for im in _SETTLED_ITEM_RE.finditer(section):
        entries.append({"q": im.group(1).strip(),
                        "a": im.group(2).strip(),
                        "spec": spec_number})
    return entries


_SETTLED_MIN_TOKENS = 4
_SETTLED_MIN_SHARED = 3


def settled_conflict(question_text, entries, *, threshold=0.6):
    """The FIRST settled entry whose question shares >= `threshold` of its
    content tokens with `question_text`, or None. Overlap is measured against
    the SETTLED question's tokens (the focused set), so a briefing/options
    preamble in the asked block never dilutes it.

    A short settled question over-matches (R1#3/R2#4: a 3-token settled entry
    re-using common domain nouns wrongly BLOCKED a genuinely-new question at
    2/3=0.67), so an entry needs >= `_SETTLED_MIN_TOKENS` content tokens AND the
    match needs >= `_SETTLED_MIN_SHARED` shared tokens IN ADDITION to the ratio
    -- the two floors together make a false block require a real, substantial
    overlap, while a near-verbatim re-ask of a full question still clears them."""
    block = content_tokens(question_text)
    if not block:
        return None
    for e in entries or []:
        q = content_tokens(e.get("q", ""))
        if len(q) < _SETTLED_MIN_TOKENS:
            continue
        shared = len(q & block)
        if shared >= _SETTLED_MIN_SHARED and shared / len(q) >= threshold:
            return e
    return None


def settled_cache_path(slug, *, home=None):
    """`~/.claude/spec-settled/<slug>.json` -- the per-repo settled-questions
    cache, refreshed off the tickets-status refresh path (no gh on the Stop
    hook path)."""
    base = home or os.path.expanduser("~")
    return os.path.join(base, ".claude", "spec-settled", "%s.json" % slug)


def build_settled_cache(specs):
    """The cache dict written per repo: {ts, specs:[N...], entries:[{q,a,spec}]}.
    `specs` is a list of {"number": N, "body": <spec ticket body>}."""
    entries = []
    numbers = []
    for s in specs or []:
        n = s.get("number")
        numbers.append(n)
        entries.extend(parse_settled_questions(s.get("body", ""),
                                               spec_number=n))
    return {"ts": int(time.time()), "specs": numbers, "entries": entries}


def _load_cache(slug, home=None):
    try:
        with open(settled_cache_path(slug, home=home), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def check_question_against_cache(block_text, slug, *, home=None, load_cache=None):
    """(block: bool, reason: str|None) for the Stop-hook Check 10. FAIL-OPEN:
    cache absent / unreadable / no conflict -> (False, None). A conflict ->
    (True, "žuž rozhodnuté v spec #N: ...") quoting the settled
    answer. `load_cache()` is injected in tests."""
    try:
        cache = load_cache() if load_cache else _load_cache(slug, home)
    except Exception:
        return False, None
    if not cache:
        return False, None
    hit = settled_conflict(block_text, cache.get("entries") or [])
    if not hit:
        return False, None
    n = hit.get("spec")
    ref = ("#%d" % n) if isinstance(n, int) else "the spec"
    reason = ("už rozhodnuté v spec %s: „%s“ → %s. "
              "Neopakuj už zodpovedanú otázku (footer U N ju "
              "nenesie -- rozhodnutie žije v spec tikete). Ak je to naozaj "
              "NOVÁ otázka, sformuluj ju tak, aby sa netýkala "
              "rozhodnutej odpovede (#1106)." % (ref, hit.get("q", ""),
                                                 hit.get("a", "")))
    return True, reason


# --------------------------------------------------------------------------- #
# (f) RECONCILIATION -- the Step-5 line + the partition-audit nudge clause.
# --------------------------------------------------------------------------- #
def spec_status_line(spec_number, done, pending, deviations):
    """The Step-5 reconciliation line: `Spec-status #N: done §..., pending §...,
    deviations ...`. Empty lists render `—`."""
    def _j(xs):
        return " ".join(xs) if xs else "—"
    return ("Spec-status #%d: done %s, pending %s, deviations %s"
            % (spec_number, _j(done), _j(pending), _j(deviations)))


def spec_partition_audit_clause(spec_open, missing_count, *, max_chars=700):
    """The OPTIONAL partition-audit nudge clause, or None. Fires only when there
    IS an open `spec` ticket AND >= 1 open stream ticket carries no `Spec:`
    reference. Hard-capped at `max_chars`."""
    if not spec_open or not missing_count:
        return None
    names = ", ".join("#%d" % n for n in spec_open)
    clause = ("SPEC-ANCHOR %d (#1106 -- %d otvorených stream tiketov bez "
              "`Spec:` pri otvorenom spec %s: dopln `Spec: #N §x` alebo "
              "`Spec: none -- <why>`)." % (missing_count, missing_count, names))
    if len(clause) > max_chars:
        clause = clause[:max_chars - 1].rstrip() + "…"
    return clause
