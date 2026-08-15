"""Pure captured-text scanners for the api-watchdog input box + footer.

Extracted verbatim from ``watchdog/__init__.py`` as item G step 3 of the
definitive module split (issue #433). These six functions all take a ``tmux
capture-pane`` STRING as input and answer questions about the pane's INPUT BOX
and the trailing CHROME/footer region: the draft text sitting in the box
(``_input_line_text``), the box-boundary classification for the keystroke jobs
(``_classify_boundary``), the conversation region above the box
(``_above_input_box``), the queued-rows/spinner walk above it (``_above_box_scan``),
whether a ``/compact`` is already queued (``_pane_has_queued_compact``), and the
trailing run of footer chrome (``_trailing_bottom_chrome``).

Unlike ``pane_classify.py`` (a genuine leaf), this is a BACK-REFERENCE module:
the bodies call primitives that stay resident in ``__init__.py`` (re-exported
there from ``pane_classify.py``) -- ``_find_input_box``, ``_input_box_rows_raw``,
``_find_input_box_from``, ``_is_bottom_chrome``, ``_is_separator_line`` -- and the
constant ``_QUEUED_COMPACT_RX`` (defined in ``long_turn.py``, used only here). It
also has two intra-module co-moved cross-calls (``_above_box_scan`` ->
``_above_input_box``, ``_pane_has_queued_compact`` -> ``_above_box_scan``). Every
one of those references is written as call-time ``watchdog.<name>`` attribute
access (module top: bare ``import watchdog``) -- the ``__init__.py`` convention
(C3) that keeps every existing ``monkeypatch.setattr(watchdog, "<name>", ...)``
/ ``patch.object(wd, "<name>")`` seam resolving identically to before the move.

All six names are re-exported into the ``watchdog`` namespace by the positional
facade import in ``__init__.py`` (replacing the earliest removed block), so all
existing ``watchdog.<name>`` seams (stash / goal / job 1 / compact, hooks, tests)
keep resolving unchanged.
"""

import watchdog


def _input_line_text(captured):
    """Text sitting in the pane's INPUT BOX: '' = bare prompt, None = no input
    box at the chrome boundary (running-turn spinner / dialog / no pane / no
    boundary locatable at all). Same boundary-line discipline as
    `_has_free_prompt` — resolved via `_find_boundary_line` (structural
    separator-pair search first, glyph-based chrome peel as fallback, issue
    #46) — never scans the transcript above the box. For a long WRAPPED
    input, capture-pane (no -J) renders it as multiple lines and the
    boundary is the LAST one — i.e. the TAIL of the typed text — which is
    why callers match with endswith().

    That contract was promised here and never delivered (#193): the function
    required the BOUNDARY row to start with `❯`, which only a one-row box
    ever does, so a wrapped draft returned None — indistinguishable from a
    running turn. The box is now located by its HEAD row and the tail is
    returned as documented. `''` still means exactly "bare box": blank rows
    are filtered out upstream, so a wrapped box can never return it."""
    box = watchdog._find_input_box(captured)
    if box is None:
        return None
    head, tail, wrapped = box
    return tail if wrapped else head[1:].strip()


def _classify_boundary(captured):
    """Classify the pane's input-box boundary for the keystroke-sending jobs
    (1/9/14/20; jobs 12/15 that once used this are REMOVED, #132/#102) —
    splits `_input_line_text`'s collapsed-to-None result into
    its two genuinely different causes (issue #46): a session that is truly
    BUSY (a foreground spinner / dialog occupies the boundary, and it just
    isn't `❯`-shaped) versus one where NO boundary could be located at all
    (`_find_boundary_line` returns None — structural AND glyph fallback both
    failed, e.g. the whole capture is chrome). Collapsing both into one
    "busy" bucket is exactly what mislabeled the #46 incident: an
    unrecognized chrome row below the box made the OLD glyph peel stop on
    that row and report "busy", when the pane was neither busy nor missing
    an input line at all — it just held a draft the peel could no longer
    reach.

    Returns (kind, text):
      ("input", <draft text, "" if bare>) -- a real boundary, safe to inspect
      ("busy", None)                      -- a real boundary, not `❯`-shaped
      ("no-input-line", None)             -- no boundary found under either strategy

    A WRAPPED draft is an "input" with its TAIL as the draft text (#193) — it
    used to land in the "busy" bucket, which is how every keystroke-sending
    job came to treat a pane holding a medium-length draft as a running turn.
    """
    rows = watchdog._input_box_rows_raw(captured)
    box = watchdog._find_input_box_from(rows)
    if box is not None:
        head, tail, wrapped = box
        return ("input", tail if wrapped else head[1:].strip())
    return ("busy", None) if rows else ("no-input-line", None)


def _above_input_box(cap):
    """The conversation region ABOVE the input box: trailing chrome peeled
    (statusline / mode hint / borders / the AGENT STRIP — `_is_bottom_chrome`),
    then the `❯` input-box line itself dropped. A strip row's activity label is
    ARBITRARY model-generated text — `◯ autopilot-worker  Waiting for
    deploy-prod.yml jobs` false-matched the busy guard (gk incident
    2026-07-24) — and the strip grows one row per worker, crowding the arm
    question out of a fixed tail window taken from the raw capture. Nothing
    below the input box is ever TURN state; job 9 scans only this region.
    The peel caps at 40 chrome lines (mirroring `_has_free_prompt`) — a
    taller-than-40-row strip leaves chrome in the region and the pane is
    simply skipped that sweep (missed arm = the safe direction, never a
    keystroke into a misread pane)."""
    lines = cap.splitlines()
    i = len(lines)
    n = 0
    while i > 0 and watchdog._is_bottom_chrome(lines[i - 1].strip()) and n < 40:
        i -= 1
        n += 1
    if i > 0 and lines[i - 1].strip().startswith("❯"):
        i -= 1
    return "\n".join(lines[:i])


def _above_box_scan(captured, max_rows=25):
    """Walk UP from the input box — see the section comment.

    Returns `(queued_rows, spinner_row)`: every `❯ …` row contiguously above
    the box (outermost last), and the first non-queued content row met, or
    None when the walk runs out of region first. Bounded by `max_rows` so a
    pathological capture can never make this walk the whole transcript."""
    rows = [ln.strip() for ln in watchdog._above_input_box(captured or "").splitlines()]
    queued = []
    n = 0
    for ln in reversed(rows):
        if not ln or watchdog._is_separator_line(ln):
            continue                       # spacer / the box's own top border
        n += 1
        if n > max_rows:
            return queued, None
        if ln.startswith("❯"):
            queued.append(ln)
            continue
        return queued, ln                  # the spinner (or whatever is there)
    return queued, None


def _pane_has_queued_compact(captured):
    """True if the pane ALREADY holds a `/compact` waiting to execute (#84).
    Every `/compact` sender consults this immediately before its own send —
    it composes with, never replaces, whatever ELSE a sender uses to track
    its own prior sends: originally the shared claim (#78) + the proc
    fingerprint (#82/#83), both DELETED by the #402 compact collapse (the
    two proven origins make a multi-sender race structurally impossible);
    job 14's real code (`watchdog/compact.py`) now composes this with its
    own `compact-requests.json`/`compact-delivered.json` pair instead.
    Either way this reads what the PANE actually shows, whoever put it
    there, which no in-process bookkeeping alone can prove."""
    queued, _spinner = watchdog._above_box_scan(captured)
    return any(watchdog._QUEUED_COMPACT_RX.match(ln) for ln in queued)


def _trailing_bottom_chrome(footer):
    """#383-review Finding 2 — the trailing run of `footer` rows that are
    themselves chrome-shaped (`_is_bottom_chrome`), walked BACKWARD from the
    capture's own last line and stopped the instant a genuinely non-chrome,
    non-blank row is hit. A blank row is skipped (never breaks the walk):
    real chrome sometimes has one between the mode hint and the agent strip
    (see the live `airules`-pane capture cited in the #383 design comment).

    Real footer chrome ALWAYS sits as one uninterrupted block at the very
    END of a genuine capture — nothing else is ever rendered below the
    agent strip / mode hint / statusline. A chrome-SHAPED line embedded
    partway through ordinary draft continuation (e.g. a pasted quote of a
    rendered statusline row, live-triggered by the #383 adversarial review:
    `I 41 core · str 12  5h 7%(4h)  wk 63%  caveman:lite  ~$12` scores >=2
    `_statusline_hits` on its own) is not part of that trailing block —
    MORE draft rows still follow it before the capture ends — so walking
    from the bottom excludes it by construction; only a genuinely
    unbroken tail of chrome counts."""
    out = []
    for ln in reversed(footer):
        s = ln.strip()
        if not s:
            continue
        if not watchdog._is_bottom_chrome(s):
            break
        out.append(s)
    return out
