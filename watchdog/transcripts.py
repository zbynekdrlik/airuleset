"""Pure transcript / session-file readers (no tmux, no network).

Extracted verbatim from ``watchdog/__init__.py`` as item G step 1 of the
definitive module split (issue #433). These are the widest-fan-in, purest
leaf helpers in the watchdog package: they read Claude Code JSONL transcripts
and subagent files off disk and answer questions about them (last marker,
last error, current context, subagent liveness, text-emitted tool-call
stalls, project label). Every name here is re-exported into the ``watchdog``
namespace by the positional facade import in ``__init__.py``, so all existing
``watchdog.<name>`` seams (goal / compact / cross_stream / janitor, hooks,
tests) keep resolving unchanged.

``_SENTINELS`` still lives in ``__init__.py`` (it moves with decide.py in a
later step); it is bound above the facade-import position, is proven unpatched
by the step-1 grep audit, and is imported here at module top -- never the
banned ``from watchdog import <function-below-its-position>`` shape.
"""

import hashlib
import json
import re
import stat as _stat
import sys
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from watchdog import _SENTINELS


def encode_project_dir(cwd):
    """Claude Code's transcript-dir name for a cwd: every '/', '.' and '_' -> '-'.

    /home/newlevel/devel/website-newlevel.media
        -> -home-newlevel-devel-website-newlevel-media
    /home/newlevel/devel/tomas_pardubsky/cold_mailing
        -> -home-newlevel-devel-tomas-pardubsky-cold-mailing
    """
    return "".join("-" if c in "/._" else c for c in str(cwd))


def find_active_transcript(projects_dir, cwd):
    """(path, mtime) of the newest *.jsonl in the cwd's transcript dir, or None."""
    d = Path(projects_dir) / encode_project_dir(cwd)
    if not d.is_dir():
        return None
    newest, newest_m = None, -1.0
    for p in d.glob("*.jsonl"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest_m:
            newest, newest_m = p, m
    return (newest, newest_m) if newest else None


def _iter_jsonl_tail(path, max_lines=60):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return []
    out = []
    for ln in raw.splitlines()[-max_lines:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _read_jsonl_byte_tail(path, tail_bytes, max_entries):
    """Parsed JSONL entries from the last `tail_bytes` of a transcript via a
    BOUNDED SEEK (never a whole-file `f.read()`) — take up to the newest
    `max_entries`. The partial first line after a mid-file seek fails
    `json.loads` and is dropped. `[]` on any read failure. Mirrors
    `question_repoke_streak`'s own bounded read: the compact / bg-bash readers
    run against real supervisor transcripts that reach hundreds of MB (cambox's
    is 670 MB — a full `_iter_jsonl_tail` read of it measured 1.17 s vs 0.005 s
    for this seek), so the tail MUST be bounded by bytes, not read whole."""
    try:
        with open(path, "rb") as f:
            try:
                f.seek(-int(tail_bytes), 2)
            except OSError:
                f.seek(0)
            raw = f.read()
    except (OSError, ValueError, TypeError):
        return []
    out = []
    for ln in raw.splitlines()[-max_entries:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------- #
# #645 — per-PANE session resolution via the transcript's RESUME BOUNDARY.
#
# Two claude panes can share ONE project cwd (marek + zbynek both in
# presenter-dev2), so `find_active_transcript` (cwd-keyed, newest jsonl) resolves
# the SAME sid for both → `compact._find_pane_for_session` sees 2 matches →
# `skip:no-pane` FOREVER. The per-PANE discriminator is the claude PROCESS start
# time (fd/env/cmdline/lock-file all carry NO sid — measured live on dev1+dev2):
# a session's transcript carries a RESUME BOUNDARY at the CURRENT process start —
# a quiet GAP just before it, then a startup BURST just after. MEASURED on dev2:
# `d306e5ce` (a 202 MB `-c` session BORN months earlier, so its BIRTH is useless
# as a signal) has a 5.7 h gap before its Aug-23 12:51 process start and a burst
# 102 s after; a binary-search-by-timestamp finds it in 26 reads / 2.3 ms.
RESUME_GAP_BEFORE_S = 120      # a (re)start has >=2 min quiet before its burst;
                               # continuous activity (an entry within 2 min
                               # BEFORE the start) is NOT a boundary — this is
                               # what excludes a co-active sibling pane so the
                               # real owner is the UNIQUE match.
RESUME_BURST_AFTER_S = 300     # the process writes its startup entry within
                               # ~5 min of start (measured <=102 s); generous.

# How far to scan for the nearest entry BEFORE the pivot. Entries are usually
# ~KB; 256 KB comfortably spans several even with the occasional multi-KB
# tool-result. If a giant (>256 KB) entry adjacent to the boundary leaves a
# real before-entry undiscovered, the `lo > 0` guard in the reader REFUSES the
# boundary rather than mistaking it for a fresh onset (an unseen before-entry
# is the match-MORE direction, never a safe skip — #645 review).
_RESUME_PRE_WINDOW_BYTES = 262144


def _jsonl_entry_epoch(line):
    """Epoch seconds of a JSONL line's ISO `timestamp`, or None (no timestamp /
    unparseable line). Used by the resume-boundary binary search (#645)."""
    try:
        ts = json.loads(line).get("timestamp")
    except Exception:
        return None
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _transcript_resume_boundary_at(path, start_epoch,
                                   gap_before_s=RESUME_GAP_BEFORE_S,
                                   burst_after_s=RESUME_BURST_AFTER_S):
    """True iff transcript `path` has a RESUME BOUNDARY at `start_epoch`: no
    entry in the `gap_before_s` window just BEFORE it (a fresh/quiet gap) AND an
    entry within `burst_after_s` AFTER it (the process's startup burst). This is
    the strongest per-PANE owner signal available (#645) — the pane whose claude
    process started at `start_epoch` owns this transcript. It is PROBABILISTIC,
    not a proof of ownership: two same-cwd processes whose starts both frame a
    quiet-then-active window stay ambiguous (the caller's uniqueness gate turns
    that into a safe skip), and an owner whose first entry lands >burst_after_s
    after its start is never boundary-resolved. Every failure leans to the
    safe-skip / refuse-boundary side, never a wrong match.

    A JSONL is append-ordered, so a BINARY SEARCH by timestamp locates the
    boundary in O(log filesize) BOUNDED reads even for a hundreds-of-MB `-c`
    transcript (measured 26 reads / 2.3 ms on a 202 MB file) — never a whole-file
    read. Fail-safe False on any read/stat error (the safe-skip direction)."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= 0:
        return False
    before = None
    after = None
    lo = 0
    try:
        with open(path, "rb") as f:
            # Binary-search for the first byte offset whose line-timestamp is
            # >= start_epoch. A mid-file seek lands inside a line, so discard the
            # partial line, then take the first line that actually carries a ts.
            # AFTER the search, `lo` advances (via `f.tell()`) ONLY on the
            # `t < start_epoch` branch, so `lo > 0` PROVES at least one entry
            # before start_epoch exists; `lo == 0` PROVES none does (a genuine
            # fresh onset). The gap guard below relies on that invariant.
            hi = size
            while lo < hi:
                mid = (lo + hi) // 2
                f.seek(mid)
                if mid:
                    f.readline()
                t = None
                while True:
                    ln = f.readline()
                    if not ln:
                        break
                    t = _jsonl_entry_epoch(ln)
                    if t is not None:
                        break
                if t is None or t >= start_epoch:
                    hi = mid
                else:
                    lo = f.tell()
            # Read a bounded window straddling the pivot: nearest entry before
            # start_epoch and the first entry at/after it. Guard the partial-line
            # discard on the SEEK TARGET `pre`, never on `lo` — when the pivot
            # sits in the first `_RESUME_PRE_WINDOW_BYTES` (every young/short
            # transcript), `pre` clamps to 0, which is a TRUE line start; a `lo`
            # guard would throw the file's genuine FIRST line away (#645 review).
            pre = max(0, lo - _RESUME_PRE_WINDOW_BYTES)
            f.seek(pre)
            if pre:
                f.readline()
            scanned = 0
            while scanned < 8000:
                ln = f.readline()
                if not ln:
                    break
                scanned += 1
                t = _jsonl_entry_epoch(ln)
                if t is None:
                    continue
                if t < start_epoch:
                    before = t
                else:
                    after = t
                    break
    except OSError:
        return False
    # `before is None` means EITHER a genuine fresh onset (no before-entry, so
    # `lo == 0`) → gap ok, OR the window missed a real before-entry that DOES
    # exist (`lo > 0`: a before-entry beyond the pre-window, e.g. one giant
    # entry) → REFUSE the boundary (conservative: an unseen before-entry is a
    # possible co-active sibling, the match-MORE direction, never the safe one).
    gap_ok = (start_epoch - before) >= gap_before_s if before is not None else lo == 0
    burst_ok = after is not None and (after - start_epoch) <= burst_after_s
    return gap_ok and burst_ok


def _entry_text(entry):
    msg = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def transcript_last_error(path):
    """Text of the session's last real assistant message IF Claude Code flagged it
    as an API error (`isApiErrorMessage`), else ''. Reads the RIGHT signal — recency
    of the error vs later GENUINE-PROGRESS activity — so a HISTORICAL api-error that
    has SINCE recovered is NOT re-reported (issue #484): the jsonl is append-only, so
    the error line stays forever, and genuine activity AFTER it proves the session /
    background worker recovered and is not currently stalled. Before #484 this walk
    `continue`d past `user`/tool_result entries and treated a pure `tool_use`
    assistant entry (empty text ∈ `_SENTINELS`) as synthetic, so a resumed worker's
    live Bash poll loop was skipped and the scan walked all the way back to the frozen
    error — making job 1b stuck-check ping the supervisor forever.

    What counts as GENUINE PROGRESS (recovery) — and, crucially, what does NOT:

      - a `user` entry carrying a `tool_result` block → '' (not stalled): the harness
        actually RAN a tool and fed its result back, which only happens on a live,
        working session. A `user` entry with only PLAIN TEXT is NOT proof of recovery
        and is SKIPPED (keep scanning back) — it may be a human/resume prompt the
        session has not yet acted on, or, decisively, job 1's OWN injected `continue`
        nudge, which lands as a bare-text `user` entry the moment CC ACCEPTS the submit
        — before any response exists — so its presence never proves the following turn
        succeeded (and a SWALLOWED submit writes nothing at all, which the skip handles
        identically). Treating any
        `user` tail as recovery (the round-A 🔴) re-broke this exact bug on the
        MAIN-session path: `transcript_last_error('') → falsy` skips job 1's
        `stalled.add(key)`, the end-of-sweep cleanup wipes the episode, and the #175
        widening back-off resets to nudge #1 every time CC's own retry re-writes the
        error line — endless pings relocated from job 1b to job 1.
      - a real `tool_use` assistant entry (recovery activity) is checked BEFORE the
        empty/sentinel skip, because a pure tool_use entry has empty text
        (`"" in _SENTINELS`) and would otherwise be skipped and let the scan reach
        the old error;
      - EVERY OTHER non-assistant entry — `system` hook noise AND Claude Code's
        non-conversational bookkeeping types (`queue-operation`, `ai-title`,
        `file-history-snapshot`/`-delta`, `mode`, `permission-mode`, `pr-link`, …)
        — is SKIPPED, exactly as the pre-#484 code did. Returning '' on those would
        MISS a genuine api-error stall that merely has a bookkeeping line appended
        after it (round-1 review MAJOR) — a silent, non-self-healing false negative
        in the higher-stakes auto-RESUME path (job 1), so the old robustness is kept.
        (The sibling reader `transcript_text_toolcall_stall` still carries the older
        broad `non-assistant → not-stalled` rule; it is left untouched under the repo
        FREEZE — its miss is a benign skipped nudge, not an un-resumed session.);
      - the remaining synthetic assistant entries ("No response requested." / truly
        empty) are still skipped, so a GENUINE stall (the error is the last real
        turn, at most followed by system / bookkeeping / synthetic entries) still
        reports it;
      - the first real non-error assistant reply means the session is fine.

    Scans the SAME 200-line tail window as the sibling `transcript_text_toolcall_stall`
    (round-B 🔵): the bookkeeping-skip robustness above is only bounded by how far back
    the scan can see, so a genuine stall buried under a trailing burst of hook / system
    / bookkeeping writes must be reachable — the default 60-line window let >60 such
    trailing entries push the error out of view, silently under-reporting a real stall.
    """
    for entry in reversed(_iter_jsonl_tail(path, max_lines=200)):
        if not isinstance(entry, dict):
            continue
        t = entry.get("type")
        if t == "user":
            if _entry_has_tool_result(entry):
                return ""       # tool_result → the harness ran a tool → session alive
            continue            # plain-text user (resume prompt / job 1's own `continue` nudge, maybe unconsumed) → keep scanning
        if t != "assistant":
            continue            # system + non-conversational bookkeeping — skip, keep scanning back
        if entry.get("isApiErrorMessage") is True:
            return _entry_text(entry) or "API Error"
        if _entry_has_tool_use(entry):
            return ""           # a real tool_use (recovery activity) → not stalled
        if (_entry_text(entry) or "").strip() in _SENTINELS:
            continue            # synthetic / tool-only text — keep scanning back
        return ""               # a real normal reply → not stalled
    return ""


def _submit_confirmed(tpath, baseline_size, text):
    """True iff the transcript at `tpath` GREW past `baseline_size` bytes with
    a NEW top-level `user` turn whose text carries `text` — the STRUCTURED
    proof that CC actually ACCEPTED a submit (#490 / #486 delivery bullet).

    Reads only the bytes appended AFTER `baseline_size` (a byte offset the
    caller records immediately before it types), so a prior identical nudge
    that was already in the transcript BEFORE the send can never false-
    positive. It looks for the exact entry `transcript_last_error`'s docstring
    documents: CC writes a plain-text `user` turn the instant it ACCEPTS a
    submit (before any response exists); a SWALLOWED submit writes nothing at
    all — so the presence of this entry, and only it, distinguishes a landed
    submit from a lost keystroke. `isMeta` / `isCompactSummary` / `tool_result`
    `user` entries are NOT typed prompts and are skipped (the same grammar
    `_last_human_prompt_ts` parses — a `/compact` continuation summary is a
    top-level `user` entry that can QUOTE a prior identical nudge, so skipping
    it closes a false-confirm the substring match would otherwise take).

    Fails SAFE toward False on any read/parse problem: an unconfirmable submit
    is treated as not-delivered (retryable), never claimed delivered — the
    exact direction the delivery-verify caller needs so it never leaves foreign
    text behind on an unreadable transcript."""
    want = (text or "").strip()
    if not want:
        return False
    try:
        with open(tpath, "rb") as f:
            f.seek(max(0, int(baseline_size)))
            raw = f.read()
    except (OSError, ValueError, TypeError):
        return False
    for ln in raw.splitlines():
        try:
            e = json.loads(ln)
        except Exception:
            continue            # a partial line at the seek boundary, or noise
        if not isinstance(e, dict) or e.get("type") != "user" or e.get("isMeta"):
            continue
        if e.get("isCompactSummary"):
            continue            # a /compact continuation summary can quote a
            #                     prior identical nudge — not a typed submit
        if _entry_has_tool_result(e):
            continue            # a harness tool-result feed, not a typed submit
        et = (_entry_text(e) or "").strip()
        if et and want in et:
            return True
    return False


def transcript_current_context(path, max_lines=200):
    """The session's CURRENT context size — cache_read_input_tokens +
    cache_creation_input_tokens off the newest assistant usage entry.
    Feeds job 14's #48 small-context gate (job 15's own use, REMOVED #102,
    no longer applies).

    A SINGLE API call can render as SEVERAL transcript lines (a thinking
    block, a text block, a tool_use block — each its own JSONL entry) that
    all share the SAME `message.id` and carry an IDENTICAL usage snapshot
    (verified live against a real forestshop-parovanie-produktov transcript,
    2026-07-25). Counting every line would triple-count one turn's context,
    so this walks backward from the newest entry, takes the newest
    `message.id`'s group, and returns the MAX (never the SUM) across the
    records sharing that id. An entry with no `id` at all (older/foreign
    transcript shapes) is treated as its own standalone group — its context
    is returned directly with no further grouping.

    0 if the transcript has no usage entries / is unreadable / doesn't
    exist (`_iter_jsonl_tail` already fails safe on a missing file)."""
    _MISSING = object()
    best_id = _MISSING
    best_ctx = 0
    for entry in reversed(_iter_jsonl_tail(path, max_lines=max_lines)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        u = msg.get("usage") or {}
        if not u:
            continue
        ctx = (int(u.get("cache_read_input_tokens") or 0)
              + int(u.get("cache_creation_input_tokens") or 0))
        mid = msg.get("id")
        if best_id is _MISSING:
            best_id = mid
            best_ctx = ctx
            if mid is None:
                break               # no id to group further entries by
            continue
        if mid != best_id:
            break                   # walked past the newest message.id's group
        best_ctx = max(best_ctx, ctx)
    return 0 if best_id is _MISSING else best_ctx


# A status marker is the FIRST glyph of its own line (`⏳ WORKING: …`) — anchored so
# a `⏳`/`✅`/`❓` QUOTED mid-prose (common in this very project, which documents the
# markers) does NOT false-match. Checked over the last few non-blank lines, not only
# the last, so a turn that appends a trailing URL / PR / deploy line after the marker
# is still recognised.
_MARKER_RX = re.compile(r"^\s*(⏳|✅|❓)")


def transcript_last_marker(path):
    """The status marker (⏳ / ✅ / ❓) of the session's last REAL assistant message,
    or '' if none. Trailing synthetic / tool-only entries are skipped. An
    `isApiErrorMessage` entry returns '' — an api-error is NOT a status marker (job 1
    owns those). Used by job 4 to spot a `⏳ WORKING` turn that has gone idle; a
    `✅`/`❓`/none last marker is NOT a working-stall (done / waiting-on-user / plain
    end), so it never triggers the job-4 ping."""
    return transcript_last_marker_line(path)[0]


def _last_marker_line_from_entries(entries):
    """PURE (entries in / verdict out) — the (marker, full marker LINE) walk
    factored out of `transcript_last_marker_line` so a BOUNDED-tail caller
    (`transcript_last_marker_bounded`, the compact path) shares the IDENTICAL
    semantics with zero drift (#486 no-duplicate direction). Walks newest ->
    oldest, returns on the first real assistant turn."""
    for entry in reversed(entries):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        if entry.get("isApiErrorMessage") is True:
            return "", ""           # api-error → job 1's domain, not a marker
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue                # synthetic / tool-only — keep scanning back
        nonblank = [ln for ln in text.splitlines() if ln.strip()]
        for ln in reversed(nonblank[-3:]):     # marker line, tolerating ≤2 trailing lines
            m = _MARKER_RX.match(ln)
            if m:
                return m.group(1), ln
        return "", ""               # a real reply, but with no status marker
    return "", ""


def transcript_last_marker_line(path):
    """(marker, full marker LINE) of the session's last real assistant message,
    or ('', ''). Same walk/semantics as transcript_last_marker — the LINE feeds
    declared_wait_until (a ⏳ that names a future clock time is a healthy wait,
    not a stall; the 2026-07-20 drilling incident)."""
    return _last_marker_line_from_entries(_iter_jsonl_tail(path))


def transcript_last_marker_bounded(path, tail_bytes=2_000_000, max_entries=200):
    """The last-marker glyph (⏳/✅/❓ or '') via a BOUNDED SEEK instead of
    `transcript_last_marker`'s whole-file `f.read()` — identical walk
    (`_last_marker_line_from_entries`), but reads only the last `tail_bytes`.
    Added for the compact `❓`-veto path (#599): that read fires on every one of
    a busy loop's Work-Complete hooks, and on cambox's 670 MB transcript the
    whole-file read measured 1.17 s vs ~0.01 s here.

    DRIFT RESIDUAL vs `transcript_last_marker` (#599 review 🔵): the two disagree
    only when a SINGLE entry larger than `tail_bytes` sits between EOF and the
    last real assistant turn — the corpus has 53 such entries (max ~7 MB). The
    default is raised to 2 MB (covers 52 of the 53). For the `❓` veto the
    dangerous drift is a FALSE-NEGATIVE (bounded='' while full='❓' → a
    mid-decision session compacted), but it is heavily backstopped: a `❓`-blocked
    turn is IDLE (nothing writes a >2 MB entry AFTER it), and a giant user
    paste that could is caught by the recent-human / busy vetoes. Widening
    further trades the (already ~0.01 s) read cost for a vanishing residual, so
    2 MB is the stopping point."""
    return _last_marker_line_from_entries(
        _read_jsonl_byte_tail(path, tail_bytes, max_entries))[0]


def transcript_last_assistant_text(path):
    """FULL text of the session's last REAL assistant message (same
    walk/skip semantics as `transcript_last_marker_line` — synthetic/
    tool-only sentinels and an `isApiErrorMessage` entry are skipped), or
    `''` if none. Unlike `transcript_last_marker_line` (which returns only
    the tail-trimmed marker line), this returns the WHOLE message — job
    20's goal-achieved backstop (#160) needs to scan it for a genuine
    `🏁 BACKLOG EMPTY:` claim anywhere in the turn, not just its last 3
    lines."""
    for entry in reversed(_iter_jsonl_tail(path)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        if entry.get("isApiErrorMessage") is True:
            return ""
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue
        return text
    return ""


# #764 -- the `🏁 BACKLOG EMPTY:` fulfilled-completion PROOF the goal
# fulfilled-rearm lane keys on. CC never persists a "fulfilled" marker, so a
# stop-(B) COMPLETED /goal loop is transcript-identical to a silently-dead one
# (`mark=="set"`, footer dark) EXCEPT for this line -- both authority profiles
# render the `🏁 BACKLOG EMPTY:` prefix (goal_registry.py). A stop-(A)
# ❓-blocked completion prints NO 🏁 line, so its last turn never matches ->
# the trigger structurally never fires on it (no re-poke on an unanswered
# question).
_BACKLOG_EMPTY_RX = re.compile(r"🏁\s*BACKLOG EMPTY")


def transcript_last_backlog_empty_ts(path, tail_bytes=2_000_000,
                                     max_entries=200):
    """Epoch seconds of the session's LAST real assistant turn IFF that turn
    carries a `🏁 BACKLOG EMPTY:` completion claim, else None. Same
    genuine-turn skip semantics as `transcript_last_assistant_text`
    (synthetic/tool-only `_SENTINELS` and an `isApiErrorMessage` entry are
    skipped). BOUNDED-SEEK read (`_read_jsonl_byte_tail`, never a whole-file
    `f.read()`) -- the fulfilled-rearm rider runs against real supervisor
    transcripts that reach hundreds of MB, so the tail is bounded by BYTES
    like `transcript_last_marker_bounded`. Fail-safe None on ANY error / a
    🏁 turn whose `timestamp` is missing or unparseable (the rider then cannot
    prove the 🏁 came AFTER the arm -> no re-arm, the safe direction)."""
    for entry in reversed(_read_jsonl_byte_tail(path, tail_bytes, max_entries)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        if entry.get("isApiErrorMessage") is True:
            return None
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue
        # The FIRST real assistant turn (newest) decides: no 🏁 here -> None.
        if not _BACKLOG_EMPTY_RX.search(text):
            return None
        raw = entry.get("timestamp")
        try:
            return datetime.fromisoformat(
                str(raw).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            return None
    return None


# #522 -- the BLOCKED-on-my-answer status marker line the `/goal` stop-condition
# (A) keys on: an assistant turn whose LAST marker is `❓ NEEDS YOU`. Deliberately
# NOT a bare `❓` (that also matches a `❓ ASKED:` body line — but an ask-and-
# continue turn's LAST marker is `⏳ WORKING`, so `_turn_marker_line` resolves it
# to `⏳`, never `❓`, and never trips this detector).
_NEEDS_YOU_RX = re.compile(r"^\s*❓\s*NEEDS YOU\b")


def _turn_marker_line(text):
    """The status-marker LINE (⏳/✅/❓ …) of an assistant turn's `text`, scanning
    the last <=3 nonblank lines in reverse for the FIRST marker match -- the SAME
    extraction `transcript_last_marker_line` uses, factored out for per-turn reuse
    by `question_repoke_run`. '' when the turn carries no marker."""
    nonblank = [ln for ln in text.splitlines() if ln.strip()]
    for ln in reversed(nonblank[-3:]):
        if _MARKER_RX.match(ln):
            return ln
    return ""


def question_repoke_run(entries, is_human_fn):
    """#522 -- PURE facts-in/verdict-out detector (#504 shape) for a `/goal` loop
    STUCK re-poking an unanswered `❓ NEEDS YOU` question (the native evaluator
    ignoring stop-condition (A) -- the 17+ re-poke incident). `entries` is a
    transcript tail as `_iter_jsonl_tail` returns it (oldest -> newest). Returns
    `(streak:int, question_line:str)`: the number of consecutive NEWEST REAL
    assistant turns that each END with a `❓ NEEDS YOU` marker line BYTE-IDENTICAL
    to the newest one, with NO genuine human answer interleaved.

    The streak is BROKEN (walk stops) by, walking newest -> oldest:
      * a genuine human user turn (`is_human_fn(entry)` True) -- the user answered;
      * a real assistant turn whose marker line is NOT `❓ NEEDS YOU`, or is a
        DIFFERENT `❓ NEEDS YOU` question (byte-differs from the newest) -- the loop
        did something else / moved to another question.
    TRANSPARENT (skipped, never break the streak, never count):
      * a MACHINE user turn -- a `/goal` re-poke, `continue`, `<task-notification>`,
        a bounce/backstop nudge (`is_human_fn` False) -- exactly the injections
        that sit BETWEEN two re-poked assistant turns;
      * a synthetic / tool-only / api-error assistant turn, and any system /
        bookkeeping entry.

    The transcript is the AUTHORITATIVE, non-flickering record (unlike the render
    footer #524 must sweep-accumulate), so a single read of `streak >= N` is a
    sound confirmation -- the caller still gates the keystroke on recent-human +
    a 24h attempt cap. Never raises (only reads plain dict fields)."""
    streak = 0
    question_line = ""
    for entry in reversed(entries):
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype == "user":
            if is_human_fn(entry):
                break                  # a genuine human answer -- streak ends
            continue                   # machine injection (goal re-poke / continue) -- transparent
        if etype != "assistant":
            continue                   # system / bookkeeping -- transparent
        if entry.get("isApiErrorMessage") is True:
            continue                   # api error mid-loop -- not a real turn, transparent
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue                   # synthetic / tool-only assistant turn -- transparent
        line = _turn_marker_line(text)
        if not _NEEDS_YOU_RX.match(line):
            break                      # a real turn NOT blocked on ❓ NEEDS YOU -- streak ends
        if streak == 0:
            question_line, streak = line, 1
        elif line == question_line:
            streak += 1
        else:
            break                      # a DIFFERENT ❓ NEEDS YOU question -- streak ends
    return streak, question_line


def question_repoke_streak(tpath, is_human_fn, tail_bytes=1_000_000, max_entries=200):
    """#522 -- thin I/O wrapper for `question_repoke_run`: read the last
    `tail_bytes` of the transcript at `tpath` via a BOUNDED SEEK (never a
    whole-file read -- montalu's transcript is 240 MB), parse up to the newest
    `max_entries` JSONL entries, and return the pure detector's verdict. `(0, "")`
    on any read failure (an empty entry list → `question_repoke_run([])` →
    `(0, "")`, so the error semantics are preserved). Reuses the shared
    `_read_jsonl_byte_tail` (#599 dedup) — this bounded-seek read used to be
    inlined here byte-for-byte."""
    return question_repoke_run(
        _read_jsonl_byte_tail(tpath, tail_bytes, max_entries), is_human_fn)


def supervisor_responded_to_nudge(path, nudge_signature, max_lines=200):
    """(#491) True iff the supervisor transcript at `path` shows a CAUSAL
    acknowledgement of our dead-worker stuck-check nudge: a LANDED `user` turn
    whose text contains `nudge_signature` (proof the keystroke was ACCEPTED,
    not swallowed — CC writes the `user` turn the instant it accepts the
    submit; a swallowed submit writes nothing, per `transcript_last_error`'s
    own docstring) FOLLOWED BY a GENUINE assistant turn (proof the supervisor
    actually RESPONDED, not merely received).

    This is deliberately STRONGER than "a genuine assistant turn is merely
    DATED after the nudge" (the first-cut #491 signal an adversarial review
    refuted): in the incident's own `/goal`-loop environment the supervisor
    emits genuine post-nudge turns turn-after-turn INDEPENDENTLY of the nudge,
    and the nudge send is best-effort (`send_continue`, no submit-verify), so a
    SWALLOWED nudge plus one `/goal` re-fire would satisfy a timestamp-only
    check and falsely silence the worker while the supervisor is still blindly
    waiting for it. Requiring the nudge to appear as a landed `user` turn ties
    the acknowledgement to THIS nudge; the retry-if-unseen path (a swallowed
    nudge → no `user` turn → returns False → the caller keeps nudging then
    escalates) is thereby preserved.

    Skip semantics mirror the #490 `_submit_confirmed` gotchas: an `isMeta` /
    `isCompactSummary` `user` entry (a `/compact` summary can QUOTE a prior
    nudge) and a `tool_result`-carrying `user` entry are not typed nudges. The
    assistant side reuses `transcript_last_assistant_text`'s genuine-turn skip
    (`isApiErrorMessage` + `_SENTINELS`), so a supervisor that itself DIED on
    an api-error right after receiving the nudge does NOT resolve (job 1's own
    auto-resume owns that supervisor). Bounded to the last `max_lines` entries
    (200, matching `transcript_text_toolcall_stall`'s sibling window) — the
    nudge+response land adjacent and near the tail on the first post-response
    sweep, and the resolution is made STICKY in state, so one in-window sight
    is enough."""
    seen_nudge = False
    for entry in _iter_jsonl_tail(path, max_lines=max_lines):
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype == "user":
            if entry.get("isMeta") is True or entry.get("isCompactSummary") is True:
                continue
            msg = entry.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                continue
            if nudge_signature and nudge_signature in (_entry_text(entry) or ""):
                seen_nudge = True
            continue
        if etype == "assistant" and seen_nudge:
            if entry.get("isApiErrorMessage") is True:
                continue
            if (_entry_text(entry) or "").strip() in _SENTINELS:
                continue
            return True
    return False


def subagent_active(transcript_path, now, window):
    """True if a SUBAGENT transcript of this session was written within `window`
    seconds — a dispatched worker / workflow is live, so the parent's `⏳ WORKING`
    idle is HEALTHY waiting, not a stall. Subagent transcripts live at
    <session-dir>/<session-id>/subagents/**/*.jsonl (confirmed on disk). This is the
    one POSITIVE-liveness signal the external poller has; without it, idle-after-`⏳`
    is indistinguishable from a healthy subagent run, so job 4 would false-fire on
    every autopilot/workflow dispatch. Fail-safe True is NOT assumed — a missing dir
    returns False (no subagent), which only ALLOWS a ping (never a keystroke)."""
    try:
        d = Path(transcript_path).parent / Path(transcript_path).stem / "subagents"
        if not d.is_dir():
            return False
        for p in d.rglob("*.jsonl"):
            try:
                if (now - p.stat().st_mtime) <= window:
                    return True
            except OSError:
                continue
        return False
    except Exception:
        return False


def _count_live_subagents(transcript_path, now, window):
    """Count of this session's DISPATCHED SUBAGENT transcripts written
    within `window` seconds (#365) -- the counting sibling of
    `subagent_active` above: that function only answers "is at least one
    alive", this answers "how many". Same directory convention
    (`<session-dir>/<session-id>/subagents/**/*.jsonl`), same fail-safe
    direction -- a missing/unreadable dir returns 0 (never a guess), which
    only ever ALLOWS a lane-occupancy nudge to fire, never blocks it on a
    filesystem hiccup."""
    try:
        d = Path(transcript_path).parent / Path(transcript_path).stem / "subagents"
        if not d.is_dir():
            return 0
        n = 0
        for p in d.rglob("*.jsonl"):
            try:
                if (now - p.stat().st_mtime) <= window:
                    n += 1
            except OSError:
                continue
        return n
    except Exception:
        return 0


def newest_subagent_transcript(transcript_path):
    """Path to the most-recently-written subagents/**/*.jsonl for this session, or
    None if there is no subagents/ dir or it holds no transcripts (issue #6). Lets
    jobs 1/4a apply their OWN detectors (transcript_last_error /
    transcript_text_toolcall_stall) to a BACKGROUND WORKER's own transcript, not
    just the supervisor's — a worker that dies on an api-error or a text-emitted
    tool-call is otherwise invisible until job 4's indirect ~30-min
    subagent_active() staleness path (and worse: a FRESH worker write is exactly
    what makes job 4a's `not subagent_active(...)` gate skip the supervisor check,
    since a fresh subagent write normally reads as healthy activity)."""
    try:
        d = Path(transcript_path).parent / Path(transcript_path).stem / "subagents"
        if not d.is_dir():
            return None
        newest, newest_m = None, -1.0
        for p in d.rglob("*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > newest_m:
                newest, newest_m = p, m
        return newest
    except Exception:
        return None


def supervisor_transcript_for_subagent(sub_path):
    """(#491) The PARENT supervisor session transcript (`<project>/<sid>.jsonl`)
    for a subagent transcript at `<project>/<sid>/subagents/**/*.jsonl` — the
    inverse of `newest_subagent_transcript`'s layout — or None when `sub_path`
    is not under a `subagents/` dir or the parent transcript does not exist.
    Fail-safe None on any path problem (job 1b then simply never resolves and
    keeps its existing nudge behavior)."""
    try:
        p = Path(sub_path)
        for parent in p.parents:
            if parent.name == "subagents":
                session_dir = parent.parent            # <project>/<sid>
                cand = session_dir.parent / (session_dir.name + ".jsonl")
                return cand if cand.exists() else None
        return None
    except Exception:
        return None


# Jobs 1b / 4a-sub (subagent api-error / text-toolcall-stall detectors) apply
# to whatever `newest_subagent_transcript` returns — which is always the MOST
# RECENTLY WRITTEN file under subagents/, even if that file itself is hours
# or days old. Without an upper age bound, a single historical dying-worker
# file (its frozen last-entry an old api-error or stuck text-toolcall) would
# nudge/escalate FOREVER, on every poll, indefinitely — even once the
# supervisor session has long since reported `✅ DONE`. Both detectors also
# gate on the SUPERVISOR's own last marker being `⏳ WORKING` (see their call
# sites) — the marker gate handles "the supervisor moved on"; this ceiling is
# the independent backstop for "the file itself is simply too old to still
# be about a CURRENT dispatch", even for a supervisor that is genuinely still
# `⏳ WORKING` on something else entirely.
SUBAGENT_MAX_AGE_SECONDS = 2 * 3600

# (#287) The per-worker nudge/escalate dedup state `_nudge_dying_subagent`
# keeps at `state["subagent-<prefix>:<wid>"]` used to be aged out by the
# generic episode-cleanup's WAIT_CLEAR_SECONDS (90s) -- the SAME short TTL
# tuned for the SUPERVISOR's own transient wait/working episodes. That is
# wrong for THIS state: `_nudge_dying_subagent` only ever runs while the
# supervisor's own last marker reads `⏳ WORKING` (see the two call sites'
# gates), which a busy /goal loop working OTHER tickets steps away from for
# minutes at a stretch (a completed ticket's own `✅ DONE` turn, per
# message-status-marker.md's per-ticket boundary). Every such gap exceeding
# 90s silently wiped "already nudged N times" back to zero, so the SAME dead
# worker's nudge#1 re-fired every time the gate reopened -- a full paid turn
# each time, unbounded (the observed "identical nudge delivered forever").
# Bound this state by the SAME ceiling that already decides whether to look
# at the worker at ALL (SUBAGENT_MAX_AGE_SECONDS): `sub_idle` only grows, so
# once the outer grace<=sub_idle<=SUBAGENT_MAX_AGE_SECONDS gate permanently
# closes for a wid it can never reopen -- the entry is never needed again
# once genuinely stale, and this TTL is comfortably longer than any
# realistic gap between `⏳` sightings within the still-active window.
#
# Residual (adversarial-review finding, THEORETICAL, no live trigger named):
# `wid` is the dispatched agent's own transcript filename stem, assigned
# fresh per dispatch and never observed reused -- but IF something ever
# reused the identical wid for a genuinely NEW dispatch within this TTL
# window (a same-file "follow up on the worker" path, say), the OLD
# worker's already-`escalated` entry would keep the new death's nudge
# suppressed until the TTL expires. Not reachable via any dispatch path
# this repo has today; worth re-checking if that ever changes.
SUBAGENT_NUDGE_STATE_TTL_SECONDS = SUBAGENT_MAX_AGE_SECONDS


# --- #486 G2: worker count from STRUCTURED STATE (not the rendered agent strip) ---
#
# A live worker == a subagent transcript this session wrote within `freshness_s`
# whose last real turn is NOT an unrecovered api-error. Replaces reading the
# rendered agent-strip (`◯ …` rows / chrome-peel), which goes silently blind on
# every CC render change (#383/#386/#393). Disk-only; no tmux, no pane capture,
# no `ps`. `_count_live_subagents` above is the boolean-ish predecessor this
# supersedes with (cwd, session_id) keying, a #484 wedged-worker guard, and an
# evidence list for the one-decision-line-per-sweep journal (#486 design bod 3).

WorkerLane = namedtuple("WorkerLane", ["agent_id", "state", "age_s",
                                       "agent_type", "detail"])
# state ∈ {"live", "stale", "wedged", "unreadable", "finished"};
# count == #(state=="live"). `finished` (#587) is a FRESH lane whose worker has
# produced its final reply and returned — excluded from `count` AND from
# `lane_has_live_evidence` (a free lane), like `stale`.


def _warn_stderr(msg):
    """Default `on_warn` sink — loud, never raises (mirrors G1's reader contract:
    a corrupt/unreadable input is surfaced, never swallowed and never fatal). This
    IS the log-of-last-resort, so a failure to write to stderr has nowhere left to
    be logged and must not crash the sweep the reader promises never to break.
    # airuleset:script-ok last-resort stderr sink — a failed write cannot itself be logged."""
    try:
        sys.stderr.write("count_live_workers: " + str(msg) + "\n")
    except Exception:
        pass


def _worker_agent_type(jsonl_path):
    """`agentType` from the `<stem>.meta.json` sidecar CC writes at spawn, or None.
    Best-effort enrichment for the decision log only (agentType/description let the
    journal name the lane) — never raises, never affects the count."""
    try:
        with open(jsonl_path.with_suffix(".meta.json"), "r",
                  encoding="utf-8", errors="replace") as f:
            d = json.load(f)
        t = d.get("agentType") if isinstance(d, dict) else None
        return t if isinstance(t, str) and t else None
    except Exception:
        return None


def count_live_workers(projects_dir, cwd, session_id, now, freshness_s,
                       *, on_warn=None):
    """(count, [WorkerLane, ...]) of THIS session's LIVE worker lanes, from disk
    state ONLY — no tmux, no pane capture, no `ps`. `count` == the number of
    `state=="live"` lanes; the evidence list carries EVERY candidate (live / stale
    / wedged / unreadable) so the G3 consumer can write one decision line per sweep.

    A live worker == a subagent transcript written within `freshness_s` seconds
    whose last real turn is NOT an unrecovered api-error. Subagent transcripts live
    at ``<projects_dir>/<enc-cwd>/<session_id>/subagents/**/*.jsonl`` (proven live
    on disk, #486 G2), so keying by ``(encode_project_dir(cwd), session_id)`` counts
    ONLY the named session's dispatched workers — a sibling session in the same
    project, or another project entirely, never leaks in. Cross-session attribution
    is solved by the directory structure, not guessed.

    Wedged guard — the DANGEROUS error direction is over-counting a DEAD worker as
    live, because the consumer's predicate is `workers==0 → the box is stuck →
    nudge`: counting a wedged worker would SUPPRESS the recovery nudge (the exact
    #486 failure). So a fresh transcript whose last real turn is either documented
    silent-death mode is classified `wedged` and NOT counted, via the canonical
    sibling detectors (no parser duplicated — drift is exactly what #486 forbids):
    (1) an unrecovered api-error (`transcript_last_error`, which carries the #484
    recovery nuance — an api-error FOLLOWED by genuine progress reads as recovered
    → '' → live again); (2) a tool-call the model emitted as TEXT and then died on
    (`transcript_text_toolcall_stall`, job 4a's own high-precision detector — it
    only fires when the last real turn ENDS with tool-call markup, never on a mere
    mention). Both are the SAME dangerous direction, so a liveness count excludes
    both.

    #587 — a cleanly-FINISHED worker (its last real turn a completed `assistant`
    TEXT reply, no error, no text-stall) is classified `finished` and NOT counted
    live, EVEN while its mtime is still fresh. The pre-#587 code left it to age out
    of the window naturally (it "briefly counted live for up to freshness_s after
    finishing") — which is exactly the 15-min mtime ghost that vetoed every
    per-ticket compact boundary, because a boundary ALWAYS follows a worker return.
    A subagent that produces a final text reply has RETURNED to its parent (or is
    idle awaiting a follow-up); either way it is not executing in-flight work.
    `transcript_worker_finished` returns `terminal` / `settling` / `""`; a
    `terminal` (end_turn/stop_sequence) turn is `finished` at any fresh age, a
    `settling` (None-stop_reason) text-tail only once `age > FINISH_SETTLE_S` — the
    #587-review guard against a text block the model merely streamed ~14 s before a
    large tool_use in the same message reading as finished while still running.
    Wedged is checked BEFORE finished, so an api-error / text-toolcall-stall lane
    stays `wedged` (still live for compact), never `finished`.

    Fail toward a SAFE count, loudly, never crashing a sweep (mirrors G1's reader
    direction). Never raises:
      - absent `subagents/` dir (or it is not a dir) → (0, []) with NO warn: a
        session that has dispatched no workers is normal, not an error;
      - the dir cannot be listed → `on_warn` (default: stderr) + (0, []). 0 only
        ever ALLOWS a nudge (the #486 direction), never blocks one;
      - a per-file stat error → `on_warn` + a `state="unreadable"` lane, not counted;
      - a non-regular file matching `*.jsonl` (a dir/socket) → skipped silently
        (not a worker transcript);
      - a mtime older than `freshness_s` → `state="stale"`, not counted (normal);
      - a half-written / corrupt FRESH transcript cannot be proven wedged, so
        `transcript_last_error` fails safe to '' and it counts LIVE (a fresh write
        is the strongest live signal; the corruption is almost always a truncated
        tail line, not a dead worker).

    `freshness_s` is required (no silent default): the G3 consumer tunes its own
    window. Today's lane-occupancy consumer uses 15 min precisely so a live worker
    in a long foreground CI-wait — which writes nothing to its transcript for up to
    ~9 min — is not misread as dead.

    Cost: the stat pass is O(TOTAL subagent files under the session), not O(live
    lanes) — a supervisor's `subagents/` dir accumulates every historical worker
    (nothing here prunes it). Measured on the real box: ~1010 files → 11 ms warm.
    The per-file content scan (the two death-mode detectors, each an ≤4 MB tail
    read) runs ONLY on candidates within `freshness_s` — ~10 with today's 15 min
    window; a caller that passes a very large `freshness_s` widens that set toward
    every file and voids the bound, so keep the window small relative to the dir's
    write cadence. Acceptable for a 60 s sweep; the linear stat growth is a noted
    residual (a session with tens of thousands of historical workers would see a
    proportionally larger stat pass) — the same property the predecessor
    `_count_live_subagents` already has, out of scope to optimize here.
    """
    warn = on_warn or _warn_stderr
    try:
        d = (Path(projects_dir) / encode_project_dir(cwd)
             / str(session_id) / "subagents")
    except Exception as e:
        warn("bad session key (%s / %s): %s" % (cwd, session_id, e))
        return 0, []
    try:
        # is_dir() swallows ENOENT/ENOTDIR (absent dir -> False, the normal
        # no-workers case, no warn) but PROPAGATES EACCES; guard it in the same
        # try as rglob so a search-permission failure on any parent warns and
        # returns 0 (safe direction) instead of crashing the sweep -- the
        # predecessor `_count_live_subagents` wraps its whole body identically.
        if not d.is_dir():
            return 0, []
        candidates = sorted(d.rglob("*.jsonl"))
    except OSError as e:
        warn("subagents dir unreadable (%s): %s" % (d, e))
        return 0, []

    count = 0
    evidence = []
    for p in candidates:
        agent_id = p.stem
        try:
            st = p.stat()
        except OSError as e:
            warn("stat failed (%s): %s" % (p, e))
            evidence.append(WorkerLane(agent_id, "unreadable", None, None, str(e)))
            continue
        if not _stat.S_ISREG(st.st_mode):
            continue                    # a dir/socket named *.jsonl — not a worker
        age = now - st.st_mtime
        if age > freshness_s:
            evidence.append(WorkerLane(agent_id, "stale", age, None, ""))
            continue
        # fresh candidate: the wedged guard + meta enrichment run ONLY here, so the
        # per-file content scan is bounded to the handful of live lanes. BOTH
        # documented silent-death modes are excluded, via their canonical sibling
        # detectors (no parser duplicated): (1) an unrecovered api-error
        # (`transcript_last_error`, #484-recovery-aware); (2) a tool-call the model
        # emitted as TEXT and then died on (`transcript_text_toolcall_stall`, job
        # 4a's own high-precision detector). Both are the SAME dangerous direction
        # for the predicate -- a stuck worker counted live suppresses the recovery
        # nudge -- so a liveness COUNT must exclude both, not just the api-error one.
        # The content scan is itself guarded: transcript_last_error /
        # transcript_text_toolcall_stall delegate to _entry_text, which raises
        # TypeError on a parseable-but-wrong-typed block (e.g. `"text": null`) — a
        # corrupt fresh transcript that json.loads accepts but _entry_text chokes
        # on. Degrade to `unreadable` (safe UNDER-count — a lane we can't classify
        # is not asserted live, so it never suppresses the nudge), never crash the
        # sweep this reader promises never to break.
        try:
            atype = _worker_agent_type(p)
            wedged = transcript_last_error(p)
            if not wedged and transcript_text_toolcall_stall(p):
                wedged = "text-toolcall stall"
            # #587: a cleanly-FINISHED worker (last real turn a completed text
            # reply, no pending tool call) is not executing in-flight work. The
            # wedged check runs FIRST, so an api-error / text-toolcall-stall lane
            # is never reclassified as finished — a wedged lane stays recoverable
            # in-flight work the supervisor still owns (the #565 direction). The
            # finish state combines with the lane's mtime `age` (#587-review): a
            # `terminal` stop_reason is trusted at any fresh age (a terminal
            # stop_reason never appears on a mid-stream text block); a `settling`
            # (non-terminal / None stop_reason) text-tail is trusted ONLY once it
            # has settled past `FINISH_SETTLE_S`, so a worker whose text block is
            # merely streamed before a large tool_use (~14 s gap) is never misread
            # finished while still running.
            fin = "" if wedged else transcript_worker_finished(p)
            finished = fin == "terminal" or (fin == "settling"
                                             and age > FINISH_SETTLE_S)
        except Exception as e:
            warn("content scan failed (%s): %s" % (p, e))
            evidence.append(WorkerLane(agent_id, "unreadable", age, None,
                                       str(e)[:80]))
            continue
        if wedged:
            evidence.append(WorkerLane(agent_id, "wedged", age, atype, wedged[:80]))
            continue
        if finished:
            # a `finished` lane is FRESH (its mtime is inside the window) but not
            # live — excluded from the count AND (via `lane_has_live_evidence`)
            # from the evidence-based live predicate, so the compact / lane-fill /
            # one-glance consumers all read it as a FREE lane immediately (#587),
            # instead of a 15-min mtime ghost. Still full evidence (agent_type
            # enriched) so the decision journal can name it.
            evidence.append(WorkerLane(agent_id, "finished", age, atype, ""))
            continue
        count += 1
        evidence.append(WorkerLane(agent_id, "live", age, atype, ""))
    return count, evidence


_LANE_NOT_LIVE_STATES = frozenset(("stale", "finished"))


def lane_has_live_evidence(evidence):
    """#571 — the #565 EVIDENCE-based liveness predicate as a SHARED helper:
    True iff ANY lane in a ``count_live_workers`` evidence list is a genuinely
    live lane (``live`` / ``wedged`` / ``unreadable`` — NOT ``stale`` and NOT
    ``finished``), the direction a consumer whose DANGEROUS bias is UNDER-
    counting live lanes must read — never the wedged-EXCLUDING headline
    ``count``.

    Two consumers have that inverted danger and BOTH read this SINGLE predicate
    (`internals-watchdog.md`, #565 shared-primitive bias — no inline copy to
    drift): compact's ``_session_has_live_bg_tasks`` (a live/wedged lane must
    veto a compact that would orphan it) and the lane-occupancy
    ``working-no-tasks`` defer (#571) — UNDER-counting a live lane there
    suppresses the recovery/fill nudge (the gk 16-issues / 2-lanes regression),
    so a fresh WEDGED lane (recoverable work the supervisor still owns) reads as
    "there IS a live lane".

    #587: a ``finished`` lane (a worker that produced its final reply and
    returned) is DEAD for BOTH consumers — a FREE lane compact may compact past
    and the fill gate may fill — so it is excluded here exactly like ``stale``.
    Finish-detection is conservative (only a clear text-ending final turn), so a
    genuinely running/wedged lane is never misread as finished; the danger
    direction (under-counting a RUNNING lane) is the SAME for both consumers and
    is not made worse by this exclusion.

    Fail-safe: an empty / falsy evidence list (or ``None``) → False (no live
    lane); a malformed item with no ``state`` is treated as non-live. Never
    raises."""
    return any(getattr(lane, "state", "stale") not in _LANE_NOT_LIVE_STATES
               for lane in (evidence or []))


def live_lane_labels(evidence):
    """The compact ``agent_id(state[/agent_type])`` labels of every LIVE lane in a
    ``count_live_workers`` evidence list — the SAME live/not-live partition
    ``lane_has_live_evidence`` uses (``_LANE_NOT_LIVE_STATES`` exclusion, #565/#587
    single source of truth), so the compact decision-log detail can never disagree
    with the veto that produced it. For the ``SKIP live-tasks`` decision log
    (#605): when condition (b) vetoes, NAME the exact lane(s) so it is never
    blind-diagnosed again (the #605 incident was diagnosed twice from a bare
    ``SKIP live-tasks sid=... cwd=...``). Never raises; a malformed lane is
    labelled defensively."""
    labels = []
    for lane in (evidence or []):
        state = getattr(lane, "state", "stale")
        if state in _LANE_NOT_LIVE_STATES:
            continue
        aid = getattr(lane, "agent_id", None) or "?"
        atype = getattr(lane, "agent_type", None)
        labels.append("%s(%s%s)" % (aid, state, ("/" + atype) if atype else ""))
    return labels


# A tool-call opening the model emitted as TEXT — `<invoke name="...">` / `<invoke
# name="...">` — instead of a structured tool_use. Used by job 4a.
_TEXTCALL_RX = re.compile(r"<\s*(?:antml:)?invoke\b[^>]*\bname\s*=", re.I)
# A COMPLETE tool-call block — `<invoke name=...>` + zero-or-more `<parameter>` +
# closing `</invoke>` — anchored to END of string (re.S so a parameter VALUE may
# span newlines / contain status glyphs). Used to verify a message ENDS with exactly
# one clean call block (the signature of a tool call emitted as text and never run),
# which a meta-conversation that merely quotes `<invoke>` and then continues never is.
_TOOLCALL_BLOCK_RX = re.compile(
    r"<\s*(?:antml:)?invoke\b[^>]*>"
    r"(?:\s*<\s*(?:antml:)?parameter\b[^>]*>.*?</\s*(?:antml:)?parameter\s*>)*"
    r"\s*</\s*(?:antml:)?invoke\s*>\s*\Z",
    re.I | re.S)
# Markup (a parameter / nested invoke) that may legitimately follow an UNCLOSED open
# tag in a turn cut off mid-call — distinct from prose (which means a discussion).
_TOOLCALL_MARKUP_AFTER_RX = re.compile(r"<\s*(?:antml:)?(?:parameter|invoke)\b", re.I)


def _entry_has_tool_use(entry):
    """True if an assistant transcript entry carries a parsed `tool_use` content
    block — i.e. the harness DID call a tool, so this entry is NOT a text-toolcall
    stall (the malformed-as-text case has only `text` blocks)."""
    msg = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(msg, dict):
        return False
    c = msg.get("content")
    if isinstance(c, list):
        return any(isinstance(x, dict) and x.get("type") == "tool_use" for x in c)
    return False


def _entry_has_tool_result(entry):
    """True if a `user` transcript entry carries a `tool_result` content block — i.e.
    the harness actually RAN a tool and fed its result back, which only happens on a
    live, working session. This is the ONE user-side proof of genuine progress that
    `transcript_last_error` trusts (#484 round-A): a PLAIN-TEXT `user` entry (a human /
    resume prompt not yet acted on, or job 1's OWN injected `continue` nudge — which
    lands the moment CC accepts the submit, before any response exists, so it never
    proves the following turn succeeded) is NOT proof of recovery and must not be read
    as such."""
    msg = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(msg, dict):
        return False
    c = msg.get("content")
    if isinstance(c, list):
        return any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)
    return False


def _ends_with_toolcall(text):
    """True iff `text` ENDS with tool-call markup — the signature of a tool call the
    model emitted as TEXT (the harness never ran it; the turn died right after
    emitting it, so nothing follows). This is the precision guard: a message that
    merely MENTIONS `<invoke>` and then continues with prose, a status marker, or a
    closing code fence does NOT end with the markup, so it does NOT match (the
    airuleset repo — and a review/completion-report session about THIS feature — is
    full of such mentions and must never be nudged)."""
    s = (text or "").rstrip()
    last = None
    for last in _TEXTCALL_RX.finditer(s):
        pass                                # last = the FINAL `<invoke name=` (or None)
    if last is None:
        return False
    # A QUOTED example block (markdown code fence or blockquote) is not a real
    # emitted call — reject it. A real model-emitted call is raw output, never inside
    # a fence or a `> ` blockquote. (We do NOT require the tag at column 0: a real
    # stall can have a same-line prose lead-in, e.g. camera-box's `court <invoke…>`.)
    line_start = s.rfind("\n", 0, last.start()) + 1
    if s[line_start:last.start()].lstrip().startswith(">"):
        return False                        # markdown blockquote → quoted example
    if s[:last.start()].count("```") % 2 == 1:
        return False                        # inside an open ``` code fence → quoted example
    # NOTE — accepted residual (per the user's job-4 over-nudge policy): a marker-LESS
    # message whose final content is a bare, unfenced, unquoted `<invoke>…</invoke>`
    # block is textually identical to a real stall, so it returns True. In practice the
    # hook-enforced status-marker convention means a compliant turn ends with ⏳/✅/❓
    # (prose after the block → False), and the worst case is one benign `stuck-check`
    # keystroke the session answers "not stuck" — exactly the residual job 4 accepts.
    tail = s[last.start():]                 # from the last opening to end-of-message
    if _TOOLCALL_BLOCK_RX.match(tail):
        return True                         # closed form: the tail IS exactly one call block
    if re.search(r"</\s*(?:antml:)?invoke", tail, re.I):
        return False                        # a close exists but the tail isn't a clean block → prose around it
    # unclosed: the turn was cut off mid-call. Accept only if no PROSE follows the
    # opening tag (a real cut-off ends inside the opening tag, at its '>', or inside a
    # following <parameter> — never in a natural-language sentence).
    gt = tail.find(">")
    if gt == -1:
        return True                         # truncated inside the opening tag itself
    rest = tail[gt + 1:].lstrip()
    return rest == "" or bool(_TOOLCALL_MARKUP_AFTER_RX.match(rest))


def transcript_text_toolcall_stall(path):
    """True iff the session's last real turn emitted a tool call as TEXT (failed
    parse → turn ended → idle). High precision so a conversation that merely
    DISCUSSES `<invoke>` markup (this very repo documents it) does NOT match:

      - the last NON-system entry must be an assistant message (a trailing
        user / tool_result entry means the conversation progressed → not stalled);
      - it is not an api-error (job 1 owns those);
      - it carries NO parsed tool_use block — checked BEFORE the empty/sentinel skip,
        because a pure tool_use entry has empty text (`"" in _SENTINELS`) and would
        otherwise be skipped and let the scan walk back to an older message;
      - its text ENDS with the tool-call markup (`_ends_with_toolcall`), not merely
        mentions it mid-prose / in a code fence.

    Scans more than the default tail window so a stall buried under a burst of
    trailing hook/system entries is not missed.
    """
    for entry in reversed(_iter_jsonl_tail(path, max_lines=200)):
        if not isinstance(entry, dict):
            continue
        t = entry.get("type")
        if t == "system":
            continue                        # hook / system noise after the turn — skip
        if t != "assistant":
            return False                    # user / tool_result tail → progressed, not stalled
        if entry.get("isApiErrorMessage") is True:
            return False                    # api-error → job 1's domain
        if _entry_has_tool_use(entry):
            return False                    # a real tool_use (incl. in-flight) → not a text-stall
        text = (_entry_text(entry) or "").strip()
        if text in _SENTINELS:
            continue                        # synthetic / tool-only text — keep scanning back
        return _ends_with_toolcall(text)
    return False


# A subagent message's stop_reason values that PROVE the turn is a final response
# (the model stopped for good), never a text block streamed just before a tool_use
# in the SAME message. Measured across 3066 real subagent transcripts on this box
# (#587-review): a genuine final text turn carries `end_turn` ~78% (and rarely
# `stop_sequence`); a mid-turn text block streamed before a tool_use NEVER carries
# either — it is `None` or `tool_use`. So a terminal stop_reason is a
# false-positive-proof "definitely finished" signal.
_TERMINAL_STOP_REASONS = frozenset(("end_turn", "stop_sequence"))

# The SETTLE window (#587-review). CC writes each content block of ONE assistant
# message as a SEPARATE JSONL line the instant that block finishes streaming, so
# the `text` line of a `[text, tool_use]` message lands well BEFORE its `tool_use`
# line — a measured gap of up to ~14 s while the model streams a large tool_use
# input (a big Edit/Write, exactly what workers do constantly). During that gap the
# transcript's last real turn is a text block, so a text-tail with a NON-terminal
# stop_reason cannot be trusted as finished until the transcript has SETTLED past
# the longest such gap. 30 s = ~2x the observed max, and far below the 15-min
# freshness window. A worker that finished this long ago has written any pending
# tool_use line by now (which the `_entry_has_tool_use` guard would then catch), so
# a text-tail still present at this age is a genuine final response.
FINISH_SETTLE_S = 30


def transcript_worker_finished(path):
    """#587 — classify a SUBAGENT transcript's LAST REAL turn as a completed final
    response. A subagent runs an internal agentic loop and TERMINATES the instant
    it produces a final text answer with no further tool_use — that text is what it
    RETURNS to the parent — so a text-ending tail means the worker has finished (or
    is idle awaiting a parent follow-up), NOT executing in-flight work. This is the
    AUTHORITATIVE finish record the compact / lane-fill / one-glance consumers need
    so a cleanly-finished lane drops out of the live liveness instead of aging out
    of the 15-min mtime window (the ghost that vetoed every per-ticket compact
    boundary).

    Returns a 3-state STRING so the caller can combine it with the lane's mtime age
    (which this function does not see):
      "terminal" — a final TEXT turn carrying a TERMINAL `stop_reason`
                   (end_turn / stop_sequence): definitely finished, safe to drop
                   IMMEDIATELY at any fresh age (a terminal stop_reason never
                   appears on a text block streamed before a tool_use — #587-review
                   measured 0 of 67945 such mid-turn text lines).
      "settling" — a final TEXT turn with a NON-terminal / absent `stop_reason`
                   (~18% of real finished transcripts have `stop_reason == None`).
                   STRUCTURALLY finished, but indistinguishable at this instant from
                   a text block the model streamed just before a large tool_use in
                   the SAME message (CC writes the text line up to ~14 s before the
                   tool_use line). The caller treats it finished ONLY once the mtime
                   has settled past `FINISH_SETTLE_S` — before that it stays live,
                   so a mid-stream running worker is never misread finished.
      ""         — running (tool_use / tool_result / user / api-error tail) or
                   unmeasurable: NOT finished (the conservative direction — an
                   unprovable-finished lane stays live, the pre-#587 status quo,
                   self-healing when its mtime ages out).

    Uses the SAME walk semantics as its siblings `transcript_last_error` /
    `transcript_text_toolcall_stall` (no new parser — reuses `_iter_jsonl_tail`,
    `_entry_has_tool_use`, `_entry_text`, `_SENTINELS`), so it cannot drift from
    them. Never raises: the one raise-prone call (`_entry_text` on a
    parseable-but-wrong-typed `"text": null` block, which the corrupt-transcript
    tests exercise) is guarded so a DIRECT caller (the facade re-exports this) is
    safe, not only the count_live_workers caller whose own try/except would catch
    it — treat an unreadable turn as `""` (not finished).

    Walking newest -> oldest:
      - a `user` entry (a tool_result feed — a tool just completed, model
        generating its next turn — OR a plain-text parent follow-up) → "" (running
        / about to run);
      - an assistant `isApiErrorMessage` → "" (a wedged/errored lane is not a clean
        finish; the caller also checks `wedged` BEFORE `finished`);
      - an assistant with a parsed `tool_use` block → "" (a tool is executing;
        checked before the sentinel skip, since a pure tool_use entry has empty
        text ∈ _SENTINELS);
      - an assistant whose text ∈ _SENTINELS (synthetic / tool-only / a trailing
        thinking-only line) → skip, keep scanning back;
      - a real assistant TEXT reply → "terminal" or "settling" by its stop_reason.
    """
    for entry in reversed(_iter_jsonl_tail(path, max_lines=200)):
        if not isinstance(entry, dict):
            continue
        t = entry.get("type")
        if t == "user":
            return ""               # tool_result feed / parent follow-up → running
        if t != "assistant":
            continue                # system / attachment / bookkeeping — keep scanning
        if entry.get("isApiErrorMessage") is True:
            return ""               # a wedged/errored lane is not a clean finish
        if _entry_has_tool_use(entry):
            return ""               # last response ended in a tool call → still running
        try:
            txt = (_entry_text(entry) or "").strip()
        except Exception:
            return ""               # a wrong-typed (`"text": null`) block → unreadable, not finished
        if txt in _SENTINELS:
            continue                # synthetic / tool-only / trailing thinking — keep scanning
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        return ("terminal" if msg.get("stop_reason") in _TERMINAL_STOP_REASONS
                else "settling")
    return ""


# --- #599/#604: live run_in_background Bash job detection (STRUCTURED transcript
# signal, #486 direction — no pane heuristics). Measured live against the real
# `~/.claude/projects` corpus (read-only, 1459 launched jobs / 10 project dirs)
# so the shape is not guessed. A bg job is LIVE only if it actually LAUNCHED and
# has no terminal event:
#   LAUNCH     — a `user` tool_result whose `toolUseResult` is a dict carrying
#                `backgroundTaskId` (the bgid — proof the job STARTED), paired
#                with that tool_result block's `tool_use_id` (the toolu id, for
#                completion matching). #604 keys on this, NOT the assistant
#                `run_in_background:true` tool_use the old #599 signal used: that
#                tool_use is only the REQUEST to launch, so a PreToolUse-hook-
#                BLOCKED / errored launch (error result, NO backgroundTaskId —
#                11 such in the corpus, all under the old signal) read live even
#                though nothing ran.
#   COMPLETION — a LATER entry whose string content holds a `<task-notification>`
#                naming that toolu id inside `<tool-use-id>…</tool-use-id>` (CC
#                injects it when the bg job finishes — 1436/1459 in the corpus;
#                read from all three content shapes by `_task_notification_ids`).
#   KILL       — a `user` TaskStop tool_result: `toolUseResult` dict with
#                `task_type=="local_bash"` + `task_id` (the bgid), content
#                "Successfully stopped task: <bgid>". CC writes NO task-
#                notification for a killed job, so the old signal read a
#                TaskStop-killed job (this session's push43/b0tkvepdj) live
#                forever within the window (#604).
# A live bg-bash job == a LAUNCH whose toolu has no COMPLETION AND whose bgid has
# no KILL in the bounded tail. Both the launch and kill readers key on the
# structured `toolUseResult` dict, never an echo-spoofable content string (a
# command that merely PRINTS "Command running in background with ID: X" would
# false-register otherwise — seen in the corpus). Consumed ONLY by compact's
# veto (`_session_has_live_bg_tasks`), never folded into `count_live_workers`/
# `lane_has_live_evidence` — a bg-bash job is NOT a worker lane, so the lane-
# occupancy (#571) / gk-fill consumers whose danger is UNDER-counting a live
# lane must not see it (they'd suppress their fill nudge).
# DELIBERATELY NO time-based staleness fallback (#604 evaluate-and-decline): a
# launched job that vanishes with NEITHER a completion NOR a kill is ~0 in the
# corpus (1/1459 — a one-shot whose START scrolled out long before session end),
# already self-healed by the 200-entry window scroll + the #599 30-min age cap;
# a time fallback would risk misreading a genuinely-live MULTI-HOUR bg job (a bg
# CI waiter) as dead → a `/compact` orphans its completion (the #29193 harm this
# veto exists to prevent). Re-open only if CC starts writing launched jobs with
# no terminal record, or stops writing the confirmed TaskStop tool_result.
_TASK_NOTIFICATION_TOOL_USE_ID_RX = re.compile(
    r"<\s*tool-use-id\s*>\s*([^<\s]+)\s*<\s*/\s*tool-use-id\s*>", re.I)


def _first_tool_result_id(msg):
    """The `tool_use_id` of the first `tool_result` block in a `message`
    (`{'content': [...]}`), or None. A bg LAUNCH / TaskStop result entry
    carries exactly one such block. Never raises."""
    if not isinstance(msg, dict):
        return None
    c = msg.get("content")
    if not isinstance(c, list):
        return None
    for x in c:
        if isinstance(x, dict) and x.get("type") == "tool_result":
            tid = x.get("tool_use_id")
            if isinstance(tid, str) and tid:
                return tid
    return None


def _entry_bg_bash_launch(entry):
    """`(bgid, toolu)` for a bg-bash LAUNCH entry — a `user` tool_result whose
    `toolUseResult` is a dict carrying `backgroundTaskId` (the bgid, proof the
    job actually STARTED) paired with the tool_result block's `tool_use_id`
    (the toolu id, for completion matching). None for anything else (#604).

    Keys on the STRUCTURED `toolUseResult.backgroundTaskId`, never the echo-
    spoofable content string, and on the LAUNCH result rather than the assistant
    `run_in_background:true` tool_use the old #599 signal used — a hook-BLOCKED /
    errored launch has an error result with NO backgroundTaskId, so it correctly
    does not register (nothing ran). Never raises."""
    if not isinstance(entry, dict):
        return None
    tur = entry.get("toolUseResult")
    if not isinstance(tur, dict):
        return None
    bgid = tur.get("backgroundTaskId")
    if not (isinstance(bgid, str) and bgid):
        return None
    toolu = _first_tool_result_id(entry.get("message"))
    if toolu is None:
        return None
    return (bgid, toolu)


def _entry_taskstop_bgid(entry):
    """The bgid a CONFIRMED TaskStop terminated — a `user` tool_result whose
    `toolUseResult` is a dict with `task_type=="local_bash"` + `task_id` (the
    bgid), content "Successfully stopped task: <bgid>". None for anything else.
    CC writes NO `<task-notification>` for a killed job, so this is the only
    terminal signal for the TaskStop-kill case (#604). Never raises."""
    if not isinstance(entry, dict):
        return None
    tur = entry.get("toolUseResult")
    if not isinstance(tur, dict):
        return None
    if tur.get("task_type") != "local_bash":
        return None
    bgid = tur.get("task_id")
    return bgid if isinstance(bgid, str) and bgid else None


def _task_notification_ids(entry):
    """tool_use ids named in a `<task-notification>` COMPLETION block, read from
    EVERY location CC writes one (measured live on cambox, #599 review 🟡):
      - the `user` form — `message.content` (str, or a list joined by
        `_entry_text`);
      - the `queue-operation` form — a TOP-LEVEL `content` string (the DOMINANT
        shape: 8860 of ~12.5k notification entries; a `queue-operation` entry
        has NO `message` key, so `_entry_text` alone MISSES it);
      - the `attachment` form — a string inside the `attachment` payload
        (`attachment.prompt`).
    Reading ONLY `message.content` left 264/1411 completions invisible, so those
    jobs read live until they scrolled out — a SAFE (over-veto) direction, but
    needless delivery latency AND the source of the ticket's under-stated bg-free
    rate (measured 71% with the old reader vs ~85% ground truth). Never raises."""
    texts = []
    try:
        texts.append(_entry_text(entry) or "")
    except Exception:
        texts.append("")
    c = entry.get("content") if isinstance(entry, dict) else None
    if isinstance(c, str):
        texts.append(c)
    att = entry.get("attachment") if isinstance(entry, dict) else None
    if isinstance(att, dict):
        texts.extend(v for v in att.values() if isinstance(v, str))
    ids = []
    for t in texts:
        if "<task-notification>" in t:
            ids.extend(_TASK_NOTIFICATION_TOOL_USE_ID_RX.findall(t))
    return ids


def live_bg_bash_ids(entries):
    """PURE — the LIST of bgids in `entries` that actually LAUNCHED and have
    NEITHER a `<task-notification>` COMPLETION (via their toolu id) NOR a
    confirmed TaskStop KILL (via their bgid). The id-returning sibling of
    `session_live_bg_bash` (which is `bool(...)` of this), so the compact
    ``SKIP live-bg-bash`` decision log (#605) can NAME the exact live job(s)
    instead of a blind veto. Same collection/pairing semantics as
    `session_live_bg_bash` (single source of truth — no drift). Never raises."""
    launched = {}          # bgid -> toolu (a bg job that ACTUALLY started)
    notified = set()       # toolu ids that got a completion notification
    killed = set()         # bgids terminated by a confirmed TaskStop
    for e in entries:
        if not isinstance(e, dict):
            continue
        pair = _entry_bg_bash_launch(e)
        if pair is not None:
            launched[pair[0]] = pair[1]
        notified.update(_task_notification_ids(e))
        kb = _entry_taskstop_bgid(e)
        if kb is not None:
            killed.add(kb)
    return [bgid for bgid, toolu in launched.items()
            if toolu not in notified and bgid not in killed]


def session_live_bg_bash(entries):
    """PURE (entries in / verdict out, #504) — True iff some bg-bash job in
    `entries` actually LAUNCHED and has NEITHER a `<task-notification>`
    COMPLETION (via its toolu id) NOR a confirmed TaskStop KILL (via its bgid).
    `entries` are already-parsed transcript jsonl dicts (the caller bounds how
    many). All three signals are collected across the WHOLE window first (a
    launch and its terminal event may be any distance apart), then a launched
    bgid is live iff its toolu is absent from `notified` AND the bgid is absent
    from `killed` (#604).

    A completion/kill whose launch is outside the window is IGNORED (an
    unmatched terminal event is not a live job); a launch whose terminal event
    is outside the window (below the tail) reads live — the conservative
    direction for a VETO whose danger is orphaning a running job. The LAUNCH
    side is STRUCTURAL (`_entry_bg_bash_launch` keys on `toolUseResult.
    backgroundTaskId`), so a hook-BLOCKED / errored launch (no bgid) never
    registers, and a `<task-notification>` merely QUOTED in prose cannot fake a
    launch; a prose-quoted completion whose text carries the exact unique
    `toolu_…` id of a still-live launch in the same window would false-CLEAR it,
    but that collision is practically unreachable (an exact unique id, in prose,
    same 200-entry window) — an accepted residual, the safe-side twin of #486's
    own quoted-markup care. `_entry_text` raising on a wrong-typed block is
    caught inside `_task_notification_ids`. Never raises."""
    return bool(live_bg_bash_ids(entries))


def session_has_live_bg_bash(path, tail_bytes=1_000_000, max_entries=200):
    """I/O wrapper for `session_live_bg_bash` — bounded-seek read of the last
    `tail_bytes` (never a whole-file read; the supervisor transcript is
    hundreds of MB) → the pure verdict. `max_entries` (200, the death-detector
    sibling window) is deliberately TIGHT so an ABANDONED older bg job scrolls
    out of the window and stops vetoing (self-healing) while a genuinely-recent
    live job is still caught — measured on cambox, a trailing 200-entry window
    is empty of open bg jobs ~85% of the time (ground truth; ~82% with this
    reader), so this veto does NOT permanently block compaction: a safe moment
    aligns within minutes.

    ACCEPTED RESIDUAL, honestly stated (the window is NOT purely beneficial in
    both directions): a genuinely-LIVE long-running bg job whose START scrolled
    PAST the window (measured 1.2% of jobs on cambox have a start->completion
    gap >200 entries, max ~1152) reads NOT-live, so a `/compact` could orphan
    its completion (the #29193 class this veto exists to prevent). That case is
    rare, RECOVERABLE (`ci-monitoring.md`'s established re-derive-from-the-
    durable-resource path), and still a net improvement over no veto (this
    covers ~98.8% of bg-job lifetimes). Widening the window trades this against
    MORE over-veto on abandoned jobs (the 0-SEND direction), so it stays TIGHT;
    both bounds are env-tunable (`_compact_bg_bash_window`) if a stream needs it.

    Fail-safe: an unreadable transcript → [] → False (never a guessed veto).
    Never raises."""
    return bool(session_live_bg_bash_ids(path, tail_bytes=tail_bytes,
                                         max_entries=max_entries))


def session_live_bg_bash_ids(path, tail_bytes=1_000_000, max_entries=200):
    """The bgids of LIVE bg-bash jobs in the transcript tail — the id-returning
    I/O wrapper sibling of `session_has_live_bg_bash` (which is `bool(...)` of
    this). Same bounded-seek read + window semantics; for the compact decision
    log (#605). Fail-safe: an unreadable transcript → [] (never a guessed veto).
    Never raises."""
    return live_bg_bash_ids(_read_jsonl_byte_tail(path, tail_bytes, max_entries))


def _hash(text):
    return hashlib.sha1((text or "").strip().encode("utf-8", "replace")).hexdigest()[:12]


# Generic checkout-dir basenames that carry no project identity on their own — when
# the cwd ends in one, the label uses parent/base (e.g. .../bakerion-ai/repo →
# "bakerion-ai/repo") so the ping names a recognisable project, not "repo".
_GENERIC_DIRS = {"repo", "src", "app", "code", "main", "checkout", "work", "dist"}


def _stream_user():
    """The box's unix user when it IS a stream identity (gatekeeper / montalu /
    david / marek boxes), '' on the personal boxes. Appended to ping labels so
    the phone can tell WHICH stream speaks (user complaint 2026-07-20: every
    stream pinged as bare 'odoo-erp')."""
    try:
        import getpass
        u = getpass.getuser()
    except Exception:
        return ""
    return "" if u in ("newlevel", "root", "") else u


def project_label(cwd):
    parts = [p for p in str(cwd).rstrip("/").split("/") if p]
    if not parts:
        label = "unknown"
    elif parts[-1].lower() in _GENERIC_DIRS and len(parts) >= 2:
        label = parts[-2] + "/" + parts[-1]
    else:
        label = parts[-1]
    u = _stream_user()
    return label + "-" + u if u else label
