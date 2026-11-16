"""#1340 -- the janitor's full input-box clear (`_janitor_clear_box`), its own
leaf because `watchdog/janitor.py` sits at its size-ratchet ceiling.

`watchdog/janitor.py` re-exports the function, so every caller
(`_janitor_recover`, `watchdog/goal.py`, `watchdog/wedge.py`,
`watchdog/send_outcome.py`, the `watchdog` facade) reaches it unchanged. Same
back-reference idiom as the janitor: a top-level `import watchdog`, every
`watchdog.<name>` read at call time."""
import time

import watchdog

JANITOR_CLEAR_MAX_CHARS = 8000   # backspaces, >= 2x the largest own payload
JANITOR_CLEAR_MAX_PASSES = 120   # degenerate 1-key passes (#1332 line breaks)
JANITOR_CLEAR_MAX_S = 20.0       # wall time per clear: one pane never eats the sweep


def _janitor_clear_box(pid, run, sleep_fn, log_fn, clock=time.monotonic):
    """#372 — clear the pane's CURRENT input-box content down to bare.

    ONE Escape first: a typed slash command (`/compact`) can leave CC's
    command-palette MENU open (the third #372 incident), which hid the landed
    type from the verify read. NEVER a second Escape (#35: a rapid
    double-Escape into a pane holding a draft PERMANENTLY DELETES it).

    Then an OBSERVED-STATE backspace loop, re-captured every pass and never
    sized from a remembered length (the #372 root cause: `len(payload)` ~3800
    backspaces into a ~26-char placeholder). #1340: a pass removes the WHOLE
    visible box's non-space count (CC drops a wrap break's space and draws a
    scrolled box's last rows only, so the buffer holds at least that many),
    not the tail row's: 25 tail-row passes cleared ~950 chars of a ~3.6k own
    /goal at 40 cols.

    Returns True only once a fresh capture shows bare. False (the next sweep
    retries from the shorter box) on: an unreadable box; NO PROGRESS -- the
    box rows unchanged after a pass AND after one more settle with no key (a
    slow redraw is not a dead pane); or the `JANITOR_CLEAR_MAX_CHARS` /
    `_PASSES` / `_S` bound (a clear runs inside the watchdog's timed sweep)."""
    sleep_fn = sleep_fn or time.sleep
    # #1002 -- janitor cleanup is RECOVERY (kind="janitor", ungated): it CLEANS UP
    # a stranded machine nudge / closes a menu overlay, so it must run regardless
    # of the #994 switch (a nudge stranded BEFORE the owner turned nudges OFF must
    # still be reclaimed). Routed through the ONE `keys` primitive like every key.
    watchdog.keys(pid, "Escape", kind="janitor", run=run)
    sent, prev_rows, waited, start = 0, None, False, clock()
    for _ in range(JANITOR_CLEAR_MAX_PASSES):
        cap = watchdog.capture_pane(pid, run, lines=30)
        cur = watchdog._input_line_text(cap)
        if cur == "":
            return True
        if cur is None:
            # #372 review MAJOR-1: None = a box that cannot be read now (a
            # dialog, a spinner, a capture failure). Never guess (a `len(None)`
            # once aborted the sweep for every pane): the next sweep retries.
            log_fn("janitor: box unreadable mid-clear")
            return False
        rows = tuple(watchdog._input_box_rows_raw(cap))
        if rows == prev_rows:
            if waited:
                log_fn("janitor: clear made no progress after %d backspaces" % sent)
                return False
            waited = True                      # one more settle, no key
            sleep_fn(watchdog.JANITOR_CLEAR_SETTLE_S)
            continue
        if sent >= JANITOR_CLEAR_MAX_CHARS or clock() - start >= JANITOR_CLEAR_MAX_S:
            break
        visible = len("".join(watchdog._box_norm_from_capture(cap).split()))
        batch = min(visible, watchdog.JANITOR_CLEAR_BATCH_MAX) or 1  # #1332 "\n"
        watchdog.keys(pid, *(["BSpace"] * batch), kind="janitor", run=run)
        sent, prev_rows, waited = sent + batch, rows, False
        sleep_fn(watchdog.JANITOR_CLEAR_SETTLE_S)
    log_fn("janitor: clear did not converge (%d backspaces, %.0f s)"
           % (sent, clock() - start))
    return False
