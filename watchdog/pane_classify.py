"""Pane TEXT-classification primitives for the api-watchdog.

Extracted verbatim from ``watchdog/__init__.py`` (#433 cluster G, the SAFE leaf
subset). These 16 functions are pure predicates over a ``tmux capture-pane``
STRING: they locate the input-box boundary line, peel the variable trailing
'chrome' (the agent strip, the managed statusline, box borders), classify a
boundary as free/busy/draft, and decide whether a pane is at an idle prompt or
waiting on the user. They call ONLY each other and the 9 cluster-private
regex/char constants below -- zero tmux, zero transcript reads, zero subprocess,
zero shared ``run_once`` loop state -- which is exactly what makes them a genuine
LEAF, provably decoupled from cluster G's fused per-pane loop (jobs 1-7,
``deliver_discord_replies``), none of which is touched here.

Self-contained by construction: the only import is ``re`` (for the constants'
``re.compile(...)`` RHS). The module imports NOTHING from ``watchdog`` -- there is
no circular-import surface. The 16 names are facade-re-exported from
``watchdog/__init__.py`` before ``run_once``, so every existing caller resolves
unchanged: ``run_once`` and the resident delivery helpers call them as bare names
(``run_once.__globals__ is watchdog.__dict__``); the sibling leaves
``watchdog/goal.py``/``cross_stream.py``/``compact.py`` call ``watchdog.<name>``
(deferred attribute); the tests call ``wd.<name>``.
"""
import re


# A Claude Code INTERACTIVE PROMPT footer — present only while a selection dialog
# (AskUserQuestion), a permission request, or a plan approval is OPEN and waiting
# for the human. Used for a NOTIFICATION ONLY (never to send keys), so a loose
# match is safe: a false ping is harmless. (The api-error ACTION path stays strict
# / flag-only precisely because it injects keystrokes.)
_WAITING_RX = re.compile(
    r"Tab/Arrow keys to navigate|Enter to select|Do you want to proceed", re.I)
# A menu SELECTION pointer: `❯ 1. Yes` (CC numbers its options). Distinguishes an OPEN
# numbered menu (still waiting) from a FREE `❯ <typed text>` input prompt (not waiting).
_MENU_POINTER_RX = re.compile(r"❯[ \xa0]\d+\.")


def _is_border_rule(s):
    """A box border / horizontal rule line (`╭────╮`, `─── labelled ok ───`). `s` is
    already stripped. Split out of `_is_bottom_chrome` because `pane_question_excerpt`
    needs borders as BOUNDARY MARKERS (they delimit the dialog box) while the other
    chrome rows (agent strip, statusline) are plain drops."""
    bars = sum(c in "─—━═╌╍┄┅┈┉╭╮╰╯┌┐└┘│┃" for c in s)
    return bars >= 4 and bars >= len(s.replace(" ", "")) - 12


# airuleset's OWN managed statusline (composed by `statusbar.py` through the
# caveman shim), rendered directly below the input box. Every segment is
# individually optional and their ORDER has changed once already: the ctx meter
# used to lead the row (`ctx ██░░░  5h 20% …`), which is the only reason a
# `startswith("ctx ")` test ever worked. #223 dropped the fill bar and the row
# now starts `5h 7%(4h)` — so the test silently stopped matching, the bottom-up
# chrome peel stopped ON the statusline, and handed it back as the input box
# (#243). Match the segment VOCABULARY anywhere in the row instead of anchoring
# on whichever segment happens to lead — but require at least TWO distinct
# segment shapes to co-occur: a real statusline always carries several, while
# ordinary prose quoting ONE token with its value ("the wk 65% figure") must
# never be eaten as chrome (adversarial review of #243, finding 3 — the harm
# is a wrapped draft's continuation row swallowed as chrome, returning the
# wrong tail).
_STATUSLINE_SEG_RES = (
    re.compile(r"(?:^|\s)ctx [\d█▓▒░]"),
    re.compile(r"(?:^|\s)5h \d+%"),
    re.compile(r"(?:^|\s)wk \d+%"),
    re.compile(r"(?:^|\s)sub \d+\.\d+\."),
    re.compile(r"caveman:"),
    re.compile(r"(?:^|\s)(?:F|Fable) \d+%"),
    re.compile(r"~\$\d"),
    re.compile(r"(?:^|\s)(?:I|Issues) \d+"),
)


def _statusline_hits(s):
    return sum(1 for rx in _STATUSLINE_SEG_RES if rx.search(s))


def _is_bottom_chrome(s):
    """A trailing 'chrome' line rendered BELOW the input box: the agent strip (`● main`
    + one `◯ <agent>` row PER concurrent subagent — including a SELECTED row, which
    renders `❯ ● main` / `❯ ◯ <agent>` instead, per issue #36), CC's pinned-preview /
    attachment row (`⧉  <project>`, #458 — a thrice-observed real element rendered at
    the bottom of this zone), the strip's selector hint (`↑/↓ to select · Enter to
    view`), the mode hint (`⏵⏵ …`), the `ctx …` footer statusline, or a horizontal
    border rule. Their count is VARIABLE — the agent strip grows one row per running
    subagent — so these MUST be stripped from the bottom before locating the `❯`
    prompt. `s` is already stripped.

    NB — box-FINDING (`_input_box_rows_raw`) deliberately does NOT lean on enumerating
    novel chrome (a truly-new glyph is handled structurally, between the box's own
    separators — the `▶ brand-new-widget` test); this enumeration only keeps KNOWN
    shapes current, and stays the sole chrome answer for `pane_goal_armed`'s
    footer-in-view proof, which has no structural alternative.

    The `⧉` match is a bare PREFIX (like the sibling `●◯` branch). A content row whose
    stripped first char is `⧉` would be misread as chrome — a theoretical #383 off-
    screen-footer residual (#458-review) — but CC only ever emits `⧉  <project>` as
    genuine bottom chrome, never mid-draft, and a draft's HEAD keeps `❯` (`❯ ⧉…` → not
    chrome). Accepted per FREEZE, same as the `●◯` prefix; the only reachable effect
    would be a `/goal` replace (deliver_goal) or a dark-watch ping, never a keystroke."""
    if not s:
        return True
    if s[0] in "●◯":                                    # agent-strip rows
        return True
    if s.startswith("⧉"):                               # CC pinned-preview / attachment
        return True                                     # `⧉  <project>` row (#458/#243)
    if s.startswith("❯ ●") or s.startswith("❯ ◯"):       # a SELECTED strip row
        return True
    if s.startswith("↑/↓") or ("to select" in s and "Enter to view" in s):
        return True                                     # the strip's selector hint
    if s.startswith("⏵⏵"):                              # bypass / mode hint
        return True
    if s.startswith("ctx "):                            # legacy pre-#223 statusline
        return True
    # The managed statusline in ANY segment order (#243): at least TWO distinct
    # segment shapes must co-occur — see _STATUSLINE_SEG_RES. A row carrying
    # the prompt glyph is the input box and must never be peeled away as chrome
    # however it reads; the `❯ ●` / `❯ ◯` selected-strip shapes are already
    # answered by their own branch above, so this guard cannot regress them
    # (#36).
    if s[0] != "❯" and _statusline_hits(s) >= 2:
        return True
    if _is_border_rule(s):                              # a box border / rule (labelled ok)
        return True
    return False


# Box-drawing glyphs a Claude Code input box renders as its own top/bottom
# border. Shares its vocabulary with `_is_border_rule` (which ALSO accepts a
# LABELLED rule for bounding a dialog's question) but `_is_separator_line`
# below is strict: every non-space char must be one of these, so a line of
# prose is never mistaken for the box's own edge.
_SEP_CHARS = set("─—━═╌╍┄┅┈┉╭╮╰╯┌┐└┘│┃")


def _is_separator_line(s):
    """A pure box-border row (`──────────`) — the input box's own top/bottom
    edge in the `separator / ❯ <draft> / separator` structure `_find_boundary_line`
    searches for (issue #46). `s` is already stripped. A narrow `capture-pane`
    can truncate the row to fewer repeated characters — still accepted (a
    length threshold, not a fixed rule width)."""
    if not s:
        return False
    core = s.replace(" ", "")
    return len(core) >= 3 and all(c in _SEP_CHARS for c in core)


# CC also renders a COUNTED form of the same hint once more than one message
# is queued ("Press up to edit 2 queued messages") — a regex, not the
# original exact-equality check, so every counted variant normalizes too
# (#176 item 4: the exact check missed this shape and misread it as a real
# held draft).
_QUEUED_PLACEHOLDER_RX = re.compile(r"^press up to edit(?:\s+\d+)?\s+queued messages$")


def _find_boundary_line(captured):
    """Locate the pane's INPUT-BOX boundary line — its LAST row.

    Since #193 the consumers (`_has_free_prompt`, `_input_line_text`,
    `_classify_boundary`) no longer test this row for the prompt glyph: it is
    the box's TAIL, and a WRAPPED draft's tail never carries one. They resolve
    through `_find_input_box`, which reads the glyph off the box's HEAD row.
    This function remains the boundary/"is there anything there at all"
    answer, and `_classify_boundary` still uses it to tell a real-but-not-a-box
    boundary ("busy") from no boundary at all. Two strategies, issue #46:

    1. STRUCTURAL (tried first). The input box always renders as
       `separator / ❯ <draft> / separator`. Find the LAST pair of separator
       lines in the capture and take the line immediately above the second
       (bottom) one. This is immune to whatever Claude Code renders BELOW
       the box — the agent strip, the statusline, or any UI element never
       seen before (the live `⧉  <project>` row that made job 14 and
       Discord-reply delivery mislabel a drafting pane "busy", 2026-07-25,
       dev2 marek-1:5.0 — the second occurrence of this class after #36). A
       multi-row WRAPPED draft still resolves correctly: the last content
       row directly above the bottom separator is its TAIL, the same
       convention the pre-#46 peel already used (callers match with
       `endswith()` for exactly this reason).

    2. GLYPH-BASED FALLBACK (pre-#46 behavior, unchanged). When no separator
       pair is found — many real captures, and most of this file's older
       fixtures, render the box borderless — peel the VARIABLE-height
       trailing chrome via `_is_bottom_chrome` (agent strip + statusline +
       mode hint + border rules) and take the first non-chrome line up from
       the bottom. An UNRECOGNIZED chrome shape below the box still stops
       this scan early; that known limitation is exactly why strategy 1 is
       tried first, and this fallback exists only so nothing regresses for
       captures that never had a border to find.

    We must NOT scan a multi-line window above the boundary in EITHER
    strategy: during a running foreground turn the boundary line IS the
    spinner, and the transcript above it can contain a lone `❯` (the #233
    scar) — a window reaching up into that transcript would call a BUSY pane
    idle and INTERRUPT it. So the boundary is always exactly one line.

    A boundary line showing CC's greyed `Press up to edit queued messages`
    HINT (an otherwise-EMPTY box, recallable via the Up arrow — never text
    the user typed), singular or a COUNTED variant ("... 2 queued messages"),
    is normalized to a bare `❯` before returning (#65 acceptance, widened by
    #176 item 4: this placeholder is never mistaken for a real draft by any
    caller). The normalization itself lives in `_normalize_queued_hint`, which
    `_find_input_box` applies to the head row too — so there is still exactly
    ONE definition of that hint, applied wherever a glyph row is read.

    Returns the raw (stripped) boundary line, or None if NEITHER strategy
    locates one at all (e.g. the whole capture is chrome, or it's empty)."""
    return _normalize_queued_hint(_find_boundary_line_raw(captured))


def _normalize_queued_hint(line):
    """Collapse CC's greyed `Press up to edit [N] queued messages` HINT to a
    bare `❯`. One place, so every caller resolving through this module agrees
    (#65/#176 item 4)."""
    if line is not None and line.startswith("❯") \
            and _QUEUED_PLACEHOLDER_RX.match(line[1:].strip().lower()):
        return "❯"
    return line


def _find_boundary_line_raw(captured):
    """The LAST row of whatever sits at the pane's chrome boundary — the input
    box's tail when there IS a box (for a WRAPPED draft the tail of the typed
    text, which is why callers match with `endswith()`), otherwise whatever
    occupies that position instead: a running turn's spinner, an open dialog's
    last row. Deciding WHICH of those it is belongs to `_find_input_box`. The
    two-strategy scan itself lives in `_input_box_rows_raw`; see
    `_find_boundary_line`'s docstring for its full rationale."""
    rows = _input_box_rows_raw(captured)
    return rows[-1] if rows else None


def _pane_shows_queued_messages_hint(captured):
    """True iff the pane's INPUT-BOX boundary shows CC's greyed `Press up to
    edit [N] queued messages` hint — an INDEPENDENT proof that at least one
    submitted message (a `/compact`, say) is sitting in CC's type-ahead queue
    behind a running turn (#833).

    Read from the boundary row RAW (via `_find_boundary_line_raw`, BEFORE
    `_normalize_queued_hint` collapses it to a bare `❯` that reads as an idle
    box), so unlike the queued-`❯ /compact`-row walk (`_pane_has_queued_compact`
    → `_above_box_scan`) this needs NO walk UP past transient chrome (the
    `✔ Update installed · Restart to update` banner combined with the goal
    indicator, which stops that walk) and does NOT race the queued row's
    separate, slightly-later render — the box hint appears the instant a
    message queues. Fail-safe: no boundary line / a non-`❯` row → False."""
    raw = _find_boundary_line_raw(captured)
    if not raw or not raw.startswith("❯"):
        return False
    return bool(_QUEUED_PLACEHOLDER_RX.match(raw[1:].strip().lower()))


def _input_box_rows_raw(captured):
    """The pane's bottom input-box candidate ROWS (stripped), HEAD FIRST.

    The scan `_find_boundary_line_raw` has always performed, factored out so
    the box's FIRST row is reachable and not only its last (#193). The glyph
    that identifies a row as an input box sits on the box's first RENDERED
    row; a wrapped draft's last row is its tail and never carries it, so
    every consumer that tested the boundary row for `❯` read a wrapped draft
    as "there is no input box at all".

    1. STRUCTURAL (tried first). The rows strictly between the last pair of
       separator lines ARE the box, bounded by its own borders — so reading
       the head needs no scan into the transcript above. A live CC 2.1.220
       pane renders exactly this shape (`────` / `❯\xa0…` / `────`, read off
       three real panes 2026-07-30), which is why this is the strategy every
       real capture resolves through.
    2. GLYPH-BASED FALLBACK, for a borderless capture: peel the VARIABLE
       trailing chrome (`_is_bottom_chrome`) and take the first non-chrome
       row up from the bottom. It returns ONE row and deliberately never
       more. Nothing bounds the box there, so walking further up is exactly
       the #233 scar — during a running turn the boundary row IS the spinner
       and the transcript above it can contain a lone `❯`, so an upward
       window would call a BUSY pane idle and INTERRUPT it. Continuation rows
       arrive stripped of the indentation that would identify them, and CC's
       transcript rows are themselves indented, so no sound stop condition
       exists. The unknown stays unknown and `_find_input_box` resolves it to
       "no box" — the safe direction for the may-I-type question.

    Returns [] when neither strategy locates anything."""
    if not captured:
        return []
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    if not lines:
        return []

    seps = [i for i, ln in enumerate(lines) if _is_separator_line(ln)]
    if seps:
        idx_b = seps[-1]
        # The BOTTOM edge stays STRICT — it is the anchor, and a real pane
        # always renders it pure. The TOP edge may carry a LABEL: Claude Code
        # writes the session's effort mode into the box's own top border
        # (`──── ultracode ─`), which the strict test rejects (#243, live on
        # dev2's presenter pane). Three guards shape the scan, all from the
        # adversarial review of that fix:
        #
        # 1. A candidate pair is trusted ONLY when nothing below the bottom
        #    edge could be the REAL box or a REAL running turn: a non-chrome
        #    row starting with the prompt glyph (a genuine draft/bare prompt
        #    below the candidate) or carrying "esc to interrupt" (a live
        #    foreground turn) means the pair is QUOTED transcript content, not
        #    the pane's own box — reject it and let the glyph fallback find
        #    the real state. A real box has only chrome below it; requiring
        #    full chrome below would re-open the unknown-chrome hole (#46,
        #    the `⧉` row incident), so only these two decisive shapes reject.
        # 2. The box's HEAD is the nearest row above the bottom edge carrying
        #    the prompt glyph, found by walking up PAST non-glyph content
        #    rows (a wrapped draft's own lines — including a pasted table row
        #    `│ a │ b │ c │`, which `_is_border_rule` would misread as the
        #    top edge) but never past a STRICT separator (crossing one would
        #    leave the box's own span).
        # 3. The row immediately above the head must be a border — strict, or
        #    a labelled `_is_border_rule` (the ultracode shape). No border
        #    above the glyph row means this is not a box.
        # `_is_separator_line` itself stays strict — it is shared with every
        # other separator consumer, and its strictness is what stops a line
        # of prose being read as the box's own edge.
        below_disqualifies = any(
            not _is_bottom_chrome(ln)
            and (ln.startswith("❯") or "esc to interrupt" in ln)
            for ln in lines[idx_b + 1:])
        if not below_disqualifies:
            i = idx_b - 1
            while i >= 0 and not _is_separator_line(lines[i]) \
                    and not lines[i].startswith("❯"):
                i -= 1
            if i > 0 and lines[i].startswith("❯") \
                    and (_is_separator_line(lines[i - 1])
                         or _is_border_rule(lines[i - 1])):
                content = lines[i:idx_b]
                if content:
                    return content

    i, n = len(lines), 0
    while i > 0 and _is_bottom_chrome(lines[i - 1]) and n < 40:
        i -= 1
        n += 1
    if i <= 0:
        return []
    return [lines[i - 1]]


def _box_is_wrapped(captured):
    """True if the pane's input box renders over more than one row — i.e. any
    append to it re-flows the whole box, so neither an exact-content check nor
    an exact append signature can be read back out of it."""
    box = _find_input_box(captured)
    return bool(box and box[2])


def _is_draft_head(s):
    """True if `s` is an input-box row carrying the prompt glyph AND text —
    `❯`, CC's separator (a NON-BREAKING SPACE on a real pane), then a draft.
    A BARE box (`❯` alone) and a menu pointer (`❯ 1. Yes` — an open dialog,
    never an input prompt) are both excluded."""
    return bool(s) and len(s) > 1 and s[0] == "❯" and s[1] in " \xa0" \
        and not _MENU_POINTER_RX.match(s)


def _find_input_box(captured):
    """Locate the pane's INPUT BOX. Returns `(head, tail, wrapped)` or None.

    This is the one place that decides "is there an input box here", and it
    decides it from the row that actually carries the prompt glyph — the
    box's HEAD — rather than from whichever row happens to be its boundary
    (#193). `head` is the glyph row, `tail` the box's last row (a wrapped
    draft's TAIL, the documented `endswith()` contract), `wrapped` says
    whether the two differ.

    STRICTLY ADDITIVE by construction: when the BOUNDARY row itself starts
    with `❯` that row is the box, exactly as every consumer has always read
    it. Only when it does NOT — precisely where they all get None / "busy"
    today — do we consult `rows[0]`, and only accept it as a box when it is a
    genuine non-bare, non-menu prompt row with at least one further row below
    it. So no capture that currently reads as an input box can change its
    answer; the change can only turn a "no box" into a box.

    Fail direction: None. The callers asking "may I type here?" resolve that
    to NO. The caller asking "is there a draft I would destroy?" (job 10)
    must NOT read it as "there is no draft" — an unreadable box is unknown,
    not empty."""
    return _find_input_box_from(_input_box_rows_raw(captured))


def _find_input_box_from(rows):
    """`_find_input_box`'s decision over rows already scanned, so a caller that
    needs BOTH the rows and the verdict pays for one scan instead of two."""
    if not rows:
        return None
    tail = _normalize_queued_hint(rows[-1])
    if tail.startswith("❯"):
        return (tail, tail, False)
    if len(rows) < 2:
        return None
    head = _normalize_queued_hint(rows[0])
    if not _is_draft_head(head):
        return None
    return (head, rows[-1], True)


def _has_free_prompt(captured, bare_only=False):
    """True if the pane shows a FREE `❯` input prompt at the bottom — the session is IDLE
    at the prompt, NOT running a foreground turn (which replaces the input box with a
    spinner / "esc to interrupt" and shows NO input `❯`).

    The boundary line is located by `_find_boundary_line` (structural
    separator-pair search first, glyph-based chrome peel as fallback — see
    its docstring, issue #46). Chrome-stripping already absorbs the whole
    agent strip, so a genuinely idle `⏳ WORKING` session with N background
    workers still lands its `❯` exactly at the boundary regardless of N, and
    an unrecognized row below a bordered box no longer hides it at all.

    bare_only=True (the TYPING gate, `pane_at_idle_prompt`): require a BARE `❯` (empty input
    box). If the user has typed text (`❯ blah`) we must NOT type over it. bare_only=False
    (the inverse used by `pane_waiting_on_user`): `❯ <typed text>` still counts as "at a
    prompt, not blocked". A menu pointer `❯ <digit>.` is never a free prompt (open dialog).

    LIVE-VERIFIED (a real CC v2.1.220 scratch session, #100/#101 live proof): CC renders the
    separator between `❯` and any typed text as a NON-BREAKING SPACE (`\xa0`), never a plain
    ASCII space — a BARE box captures as the literal single glyph `'❯'` (nothing to separate),
    but the instant there is text it is `'❯\xa0<text>'`. A check anchored on a plain `"❯ "`
    therefore NEVER matches a real held draft, which made `deliver_with_stash`'s own
    idle-with-draft precondition refuse EVERY real delivery with "not idle-with-draft" — the
    exact #101 incident signature — regardless of how genuinely idle the pane was. Both
    characters are accepted below; `_input_line_text` already worked correctly throughout
    (`str.strip()` treats `\xa0` as whitespace).

    The glyph is read off the box's HEAD row, never its boundary row (#193) —
    a WRAPPED draft puts its tail at the boundary, so testing the boundary for
    `❯` reported "no free prompt" for every payload long enough to wrap. That
    condition NAMED "the boundary row begins with the glyph" while it was
    asked to DECIDE "is there an input box I may type into". Fail direction is
    unchanged and deliberate: an unlocatable box answers NO (do not type)."""
    box = _find_input_box(captured)
    if box is None:
        return False
    head, _tail, _wrapped = box
    if head == "❯":
        return True
    return bool(not bare_only and _is_draft_head(head))


def pane_waiting_on_user(captured):
    # A LIVE blocking dialog (AskUserQuestion / permission / plan approval) occupies
    # the input area — there is NO free `❯` input-prompt line at the bottom. A CLOSED
    # dialog can leave its footer text on screen while the session sits at the normal
    # `❯` prompt (idle) or works past it — that is NOT waiting, and matching the loose
    # footer regex anywhere in the pane false-pinged "čaká na teba" (bypass-permissions
    # flashes + AskUserQuestions that auto-continue after ~60s). So require the footer
    # AND the absence of a bottom `❯` input prompt (the persistence gate in run_once
    # adds the second guard: the footer must survive ≥2 polls before it pings).
    if not captured or not _WAITING_RX.search(captured):
        return False
    return not _has_free_prompt(captured)


_OPTION_ROW_RX = re.compile(r"^(?:❯\s*)?\d+\.\s+\S")
# Navigation-help footer ONLY — deliberately NARROWER than _WAITING_RX, whose
# "Do you want to proceed" alternative IS the question of a permission dialog
# and must stay in the excerpt.
_DIALOG_HELP_RX = re.compile(r"Tab/Arrow keys to navigate|Enter to select", re.I)
# Dialog UI AFFORDANCES the fullscreen renderer (CC 2.1.20x) appends below the
# real options ("4. Type something." / "5. Chat about this", often past a border
# rule) — they are chrome, not options, and anchoring on them shipped a phone
# ping whose entire "question" was "Chat about this" (david@gk, 2026-07-09).
_DIALOG_UI_ROW_RX = re.compile(
    r"^(?:❯\s*)?(?:\d+\.\s+)?(?:Type something\.?|Chat about this)\s*$", re.I)
# The dialog's FIRST option row — the anchor for the question walk.
_FIRST_OPTION_RX = re.compile(r"^(?:❯\s*)?1\.\s+\S")


def pane_question_excerpt(captured, max_chars=900):
    """Extract the OPEN dialog's question + options from a captured pane, so the job-2
    "čaká na teba" ping carries WHAT is being asked — the user's explicit complaint was
    pings saying only "a question is waiting" with no question in them (2026-07-04).

    A blocking dialog (AskUserQuestion / permission / plan approval) renders as
    question text, then numbered option rows (`❯ 1. …` / `  2. …`) — since CC
    2.1.20x (fullscreen renderer) with WRAPPED description lines interleaved and
    UI affordance rows appended below — then the help footer. Strategy: strip
    box edges / help footer / UI affordances, anchor on the dialog's FIRST
    option row (`1. …`) nearest the bottom, take up to 6 text lines directly
    above it (bounded by a border rule or a ● bullet, so we never reach past the
    dialog into transcript prose) as the question, and every numbered option row
    from the anchor down as the options (descriptions between them are skipped —
    they'd blow the cap). Anchoring on the LAST numbered row instead picked the
    "Chat about this" affordance and lost the question (david@gk, 2026-07-09).
    Returns "" when no options block is visible — the caller falls back to the
    generic text. Read-only (feeds a NOTIFICATION only, never a keystroke), so a
    slightly messy excerpt is harmless; missing it entirely is the failure."""
    if not captured:
        return ""
    rows = []                               # (text, is_border_marker)
    for raw in captured.splitlines():
        s = raw.strip()
        if not s:
            continue
        if _is_border_rule(s):              # dialog edge → keep as a boundary marker
            rows.append(("", True))
            continue
        inner = s.strip("│┃║").strip()      # peel the box's vertical edges
        if not inner or _DIALOG_HELP_RX.search(inner):
            continue                        # empty in-box line / navigation help footer
        if inner[0] in "●◯":
            # A transcript bullet / agent-strip row is a BLOCK BOUNDARY, not a
            # plain drop: the common AskUserQuestion dialog renders BORDERLESS
            # with `● Claude asked:` as its top, and without this marker the
            # question walk climbed past it into transcript prose (review
            # finding, 2026-07-04).
            rows.append(("", True))
            continue
        if inner.startswith("⏵⏵") or inner.startswith("ctx "):
            continue                        # mode hint / statusline
        if _DIALOG_UI_ROW_RX.match(inner):
            continue                        # renderer affordance, never an option
        rows.append((inner, False))
    anchor = None
    for i in range(len(rows) - 1, -1, -1):  # dialog's `1. …` nearest the bottom
        if not rows[i][1] and _FIRST_OPTION_RX.match(rows[i][0]):
            anchor = i
            break
    if anchor is None:
        return ""
    question = []
    j = anchor - 1
    while j >= 0 and not rows[j][1] and len(question) < 6:
        question.insert(0, rows[j][0])
        j -= 1
    options = [r[0] for r in rows[anchor:]
               if not r[1] and _OPTION_ROW_RX.match(r[0])]
    out = " · ".join(question + options)
    if len(out) > max_chars:
        out = out[:max_chars - 1] + "…"
    return out


def pane_at_idle_prompt(captured):
    """True if the pane is IDLE at a free `❯` prompt — safe to type a self-check nudge.

    Job 4 / 4a REQUIRE this before sending a keystroke. A FOREGROUND subagent (a
    ticket-validator, a Task/Agent dispatch) BLOCKS the parent, so the parent transcript
    FREEZES and looks idle (`⏳ WORKING`, 30 min stale) while the session is very much
    ALIVE — and the pane shows the agent running with NO free `❯` prompt. Typing there
    does not land at a prompt, it INTERRUPTS the running agent (the observed "Agent
    Validate issue #233 finished · Interrupted" incident). Requiring a free `❯` at the
    bottom means we only ever type into a genuinely idle session (turn ended, waiting on
    a background job / input) — never into one blocked on live foreground work. The
    BACKGROUND-subagent case (main idle at `❯` while an autopilot-worker runs) still
    shows a free `❯`, so it passes THIS gate but is caught by `subagent_active`.

    Requires a BARE `❯` (empty input box): a session with USER-TYPED but unsubmitted text
    (`❯ blah`) means the user is present and interacting — not a silent stall — and we
    must not type over their input, so bare_only=True."""
    return _has_free_prompt(captured, bare_only=True)
