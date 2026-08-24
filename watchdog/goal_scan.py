"""Goal-marker transcript scan, `◎ /goal` footer read, and virgin-arm
human-recency gate (the watchdog's goal-sweep engine primitives).

Extracted verbatim from ``watchdog/__init__.py`` as item G step 10 of the
definitive module split (issue #433). Six functions, two source blocks:

* ``_goal_autoarm_recent_human_activity`` -- #339 job-9 gate: refuses a virgin
  auto-arm while a live human is using the pane (presence marker + transcript
  human-prompt recency, symmetric clamp).
* ``goal_templates_path`` / ``_goal_marker_content`` / ``_parse_goal_marker`` /
  ``scan_goal_markers`` -- the installed-SKILL.md resolver and the incremental
  transcript reader that finds the newest ``/goal`` set/cleared marker (the
  "intent" side of the goal dark-watch).
* ``pane_goal_armed`` -- reads CC's own ``◎ /goal`` footer/header indicator off a
  captured pane (the "reality" side), ``True``/``False``/``None``.

This is a back-reference module: it ``import watchdog`` and reaches every
package-level name it did NOT co-move -- call-time as ``watchdog.<name>``. That
is the existing submodule convention and it keeps every ``patch.object(
watchdog, "<name>", ...)`` seam resolving unchanged (the step-1 grep-audit
lesson; see ``.claude/rules/internals-watchdog.md``). Names reached that way:
the four functions patched by tests (``pane_goal_armed``,
``_goal_autoarm_recent_human_activity``, ``goal_templates_path``, and
``scan_goal_markers`` via re-export) plus the cross-module back-refs
``_human_age_desc``/``_last_human_prompt_ts`` (still in ``__init__``),
``_is_bottom_chrome``/``_is_border_rule`` (``watchdog/pane_classify.py``),
``_trailing_bottom_chrome`` (``watchdog/pane_text.py``), ``GOAL_INDICATOR``,
and the goal-format constants kept in ``__init__`` as live seams
(``_GOAL_LCS_OPEN``/``_GOAL_LCS_CLOSE``, ``GOAL_ARM_ACTIVE_PREFIX``,
``_GOAL_ARM_PROBE``, ``_GOAL_HEADER_INDICATOR_RX``,
``GOAL_AUTOARM_RECENT_HUMAN_S``). The one intra-module co-moved call
(``scan_goal_markers`` -> ``_parse_goal_marker``/``_goal_marker_content``)
stays BARE: the step-10 C5 audit proved neither is patched at the
``watchdog.`` path.

``GOAL_MARK_TAIL_BYTES`` moved here with the functions (it is
``scan_goal_markers``'s def-time default, and its ``__init__`` definition sat
BELOW this module's re-import position -- so a ``from watchdog import`` at
module top would be an import-time forward reference; the step-design rule for
a below-position def-default is move + re-export). Every name here (the six
functions AND ``GOAL_MARK_TAIL_BYTES``) is re-exported into the ``watchdog``
namespace by the positional facade import in ``__init__.py``.
"""

import json
import os
from pathlib import Path

import watchdog


def _goal_autoarm_recent_human_activity(sid, tpath, now, window_s=None,
                                        extra_human_prefixes=(),
                                        future_skew_s=None):
    """#339 -- job 9's virgin-candidate path (`_goal_autoarm_virgin_candidate`)
    had NO discriminator at all for "is a live human using this pane RIGHT
    NOW" -- only whether `/goal` had ever been touched. montalu3's own
    first-ever session was interactive (the user typed "kto si?"), had
    never touched `/goal`, satisfied every existing gate, and got
    auto-armed ~2 minutes later -- the live incident this closes.

    Combines TWO independent signals, EITHER sufficient to refuse:

    1. The presence marker `/tmp/claude-user-active-<sid>` (stamped by
       `clear-question-dedup.sh` on UserPromptSubmit ONLY -- a `/goal`
       re-poke or a hook-feedback re-invocation never stamps it). A fresh
       mtime is cheap, direct evidence of a live human. A MISSING marker
       fails OPEN (the established #128 axis: `/tmp` gets cleared under
       long-running sessions or a box reboot, so absence never
       MANUFACTURES a "human present" signal on its own) -- it only means
       fall through to signal 2, never "safe to arm".
    2. `_last_human_prompt_ts(tpath)` -- the mandatory, always-available
       signal (no `/tmp` dependency, already used elsewhere in this file
       for question-dedup pruning). It excludes goal re-pokes, Stop-hook
       feedback, AND job-7 Discord-reply deliveries via its own
       `_MACHINE_PROMPT_PREFIXES` list ("Odpoveď z Discordu:" and
       "Stop hook feedback:" both there) -- the ticket's own named
       job-7 edge case needed no special-casing here at all.

    Both timestamps are clamped `-future_skew_s <= now - ts < window_s`
    ("recent" bounded on both sides) before counting as recent. `future_skew_s`
    defaults to `window_s`, i.e. the SYMMETRIC `-window_s <= age < window_s`
    bound the #339-review below reasoned to (the default every caller EXCEPT the
    delivery path keeps — see the `future_skew_s` note at the end).

    #339-review MAJOR (fresh-context adversarial review, live-reproduced
    against the real function): the first shipped clamp was the
    ASYMMETRIC `0 <= age < window_s` -- reasoning (wrongly) that a
    future-dated value must be clock-skew and should never read as
    recent, mirroring the #177 `min()` clamp lesson elsewhere in this
    file. That lesson does NOT transfer here: `now` is captured ONCE at
    the top of the whole sweep (`run_once`), while job 9 (and this
    check) runs at the sweep's TAIL, so a human's FIRST prompt landing
    AFTER `now` was captured but BEFORE this pane's own turn in the loop
    produces a transcript/marker timestamp genuinely NEWER than `now` --
    a negative `age` that the asymmetric clamp read as "not recent",
    reproducing the EXACT #339 incident through a timing hole. The
    symmetric bound absorbs that mid-sweep-drift case (a human prompt up
    to `window_s` in the future still counts as recent) while still
    refusing a GROSSLY future-dated value (clock skew, a transcript
    synced off another box, anything beyond `window_s`) -- and being
    generous on the future side costs nothing: it can only ever make this
    function MORE cautious (refuse when uncertain), never arm when it
    shouldn't. `window_s` defaults to `GOAL_AUTOARM_RECENT_HUMAN_S`.

    Evaluated and REJECTED (full reasoning on the #339 design comment):
    requiring ZERO human prompts in the WHOLE transcript before ever
    treating a session as virgin-armable. The genuinely-headless
    population this path targets includes reduced-authority stream boxes
    a human periodically attaches to and briefly chats with -- normal,
    expected use, not evidence the box should never self-heal into an
    armed loop again. The RECENT-window form already fixes the reported
    failure (arming DURING a live conversation, a recency question); a
    whole-transcript form would additionally, permanently disqualify a
    large legitimate population for zero extra safety on the incident
    actually reported.

    Fail direction: an unreadable transcript is refused OUTSIDE this
    function entirely (the caller's own `find_active_transcript` already
    gates it); `_last_human_prompt_ts` itself returns `None` on any read
    failure (its own documented contract), which this function treats as
    "no signal, not recent" -- never a manufactured refusal from an
    unmeasurable read (`_goal_never_armed`, the sibling this used to sit
    adjacent to, was deleted along with the rest of job 9's old guessing
    machinery by #403 -- this discipline now stands on its own).

    `extra_human_prefixes` (#377-review MINOR-1, optional): threaded
    straight through to `_last_human_prompt_ts` -- see ITS docstring. The
    default `()` is a no-op, so job 9's own call (which never passes this)
    keeps its exact reviewed behavior unchanged; a caller that DOES need a
    Discord-relayed answer to count (`_compact_recent_human_activity`)
    passes its own prefix set.

    `future_skew_s` (#675, optional): the FUTURE side of the clamp. `None`
    defaults to `window_s` -> the SYMMETRIC bound the #339 paragraph above
    reasoned to, which EVERY caller keeps EXCEPT `deliver_goal` (arm delivery),
    which passes the SMALL `GOAL_PRESENCE_FUTURE_SKEW_S` (300 s). WHY only there:
    for delivery, `recent=True` VETOES the re-arm, so a GROSSLY-future presence
    marker (a clock / cross-box-sync desync, minutes-to-hours ahead) reading
    "recent" is a FALSE ~30-min veto that starves the re-arm (the #675 defect);
    a small future tolerance still absorbs mid-sweep drift but rejects gross
    skew. The DESTRUCTIVE consumers (the #522 `/goal clear`, the #617 stranded
    clear, `_recovery_recent_human`) and the sub-window callers (compact,
    lane-nudge) keep the symmetric default, where a future-dated read reading
    "recent" only makes them MORE cautious (an extra VETO of a keystroke) --
    exactly the fail-safe direction #339 relied on, unchanged."""
    window_s = watchdog.GOAL_AUTOARM_RECENT_HUMAN_S if window_s is None else window_s
    skew_s = window_s if future_skew_s is None else future_skew_s
    try:
        mtime = os.stat("/tmp/claude-user-active-%s" % sid).st_mtime
    except OSError:
        mtime = None
    if mtime is not None:
        age = now - mtime
        if -skew_s <= age < window_s:
            return True, "presence marker %s" % watchdog._human_age_desc(age)
    try:
        hts = watchdog._last_human_prompt_ts(tpath, extra_human_prefixes=extra_human_prefixes)
    except Exception:
        hts = None
    if hts is not None:
        age = now - hts
        if -skew_s <= age < window_s:
            return True, "transcript human prompt %s" % watchdog._human_age_desc(age)
    return False, ""


# Bootstrap window for a session this job has never scanned before. Later
# sweeps read ONLY the bytes appended since the stored offset, so the steady
# state costs ~nothing regardless of how long ago the goal was armed.
GOAL_MARK_TAIL_BYTES = 4_000_000
# #517 -- the FIRST-SIGHT reverse-scan seed cap. `seed_goal_marker` scans
# block-wise backward from EOF until it hits the newest `Goal set:`/`Goal
# cleared:` marker, BOF, or this many bytes. It bounds the one-time cost paid
# only at first sight of a new sid (state loss / fresh install / >tail-downtime);
# an arm DEEPER than this is honestly reported `unknown-past-cap`, never a silent
# not-armed. Block-wise so an armed-within-tail session still exits at ~one 4 MB
# block and memory stays one block, not the whole cap.
GOAL_MARK_SEED_CAP_BYTES = 32_000_000


def goal_templates_path():
    """`~/.claude/skills/autopilot/SKILL.md` — the INSTALLED autopilot skill,
    resolved at CALL time (never a frozen module-level constant).

    Deliberately the installed copy, never a repo checkout: the isolated
    sub-dev users (marek / david / montalu) have no `~/devel/airuleset`, and
    the installed skill is by definition the text their `/autopilot` actually
    prints — so it is the only source that can never disagree with what a
    session was armed from."""
    return Path.home() / ".claude" / "skills" / "autopilot" / "SKILL.md"


def _goal_marker_content(entry):
    """TOP-LEVEL string content of a transcript entry — the ONLY shapes CC
    writes a `local-command-stdout` marker as (`user` with a plain-string
    `.message.content`, or `system` with a plain-string `.content`). A NESTED
    `tool_result` is structurally excluded: its content is always a list, so
    a session that grepped ANOTHER session's transcript can never be misread
    as its own goal state (this repo's own CLAUDE.md, #54)."""
    if not isinstance(entry, dict):
        return None
    if entry.get("type") == "user":
        msg = entry.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"]
        return None
    if entry.get("type") == "system" and isinstance(entry.get("content"), str):
        return entry["content"]
    return None


def _parse_error_clear_payload(tail):
    """#675 -- extract the (possibly CC-TRUNCATED) cleared condition from the
    error-clear tail after `_GOAL_CLEARED_ERROR_PREFIX`, i.e. the text CC quotes
    in `… (<reason>): "<condition>". Run /goal again to continue.`. Returns the
    condition string, or None. Only the OPENING of the condition is needed by the
    caller's foreign-goal signature check, so a `…`-truncated value is fine."""
    q = tail.find(': "')
    if q < 0:
        return None
    cond = tail[q + 3:]
    cut = cond.rfind('". ')
    if cut < 0:
        cut = cond.rfind('"')
    if cut <= 0:
        return None
    return cond[:cut].strip() or None


def _parse_goal_marker(content, is_system=False):
    """`{"state": "set"|"cleared", "payload": str|None[, "clear_kind": ...]}` for
    a genuine `/goal` marker, else None. The whole entry content must BE the
    marker (it starts with the `<local-command-stdout>` tag, CC's own arm
    instruction, or — the #675 error-clear — CC's `Goal cleared after an
    unrecoverable error …` sentence) — a compaction SUMMARY narrating "the loop's
    Goal set: …" in prose, or any message merely QUOTING one of those sentences,
    is not state.

    `is_system` (#675, 🔴-1): the error-clear shape is recognized ONLY when it
    came from a `system` transcript entry. CC writes that clear ONLY as `system`;
    a `user` entry (a normal human prompt, a Discord-relayed answer, a supervisor
    pasting logs) that merely BEGINS with the clear sentence is forgeable content
    and must NEVER become a `cleared` marker (it would drive an auto-`/goal` into
    a live human's session). The LCS + arm shapes are unchanged — they legitimately
    arrive on `user` entries (a queued arm surfaces as a user entry, #64)."""
    s = (content or "").strip()
    if s.startswith(watchdog.GOAL_ARM_ACTIVE_PREFIX):
        # The record CC writes for EVERY arm — and the ONLY one it writes when
        # the `/goal` was typed into a BUSY pane and drained from the
        # type-ahead queue (that path logs a `queue-operation` entry instead of
        # a `local-command-stdout` marker; live-captured on CC 2.1.220, #64).
        # Without this shape a re-armed session keeps reporting its OLD payload
        # forever, so the drift check would re-arm it every single sweep.
        rest = s[len(watchdog.GOAL_ARM_ACTIVE_PREFIX):]
        # The payload itself contains quotes (`--search "-label:autopilot-skip"`
        # is in every shipped template), so the terminator is the LAST `". `,
        # never the first quote. CC's trailing instruction sentence carries no
        # quote of its own, which is what makes this unambiguous.
        cut = rest.rfind('". ')
        if cut < 0:
            cut = rest.rfind('"')
        if cut <= 0:
            return None
        return {"state": "set", "payload": rest[:cut].strip() or None}
    # #675 -- CC's OWN clear on a TRANSIENT failure (a bare `system` message, NOT
    # LCS-wrapped): "Goal cleared after an unrecoverable error (<reason>): …". The
    # `clear_kind` keys on the REASON segment ONLY (`… error (<reason>): "…`, never
    # the QUOTED condition, so a non-auth error clearing a goal whose CONDITION
    # mentions authentication stays `error`): an `authentication` reason is the
    # owner-ruled NORMAL transient (#662/#676) -> `clear_kind="auth"` (auto-rearm
    # eligible); any other reason -> `cleared` (flips armed->false) but tagged
    # `error` and NEVER auto-rearmed. The cleared CONDITION is extracted (even
    # CC-truncated) so the re-arm path can refuse a FOREIGN (hand-armed) goal.
    # `is_system` gates this whole shape (🔴-1). A deliberate `Goal cleared:`
    # below is `clear_kind="user"` — the #170 boundary a re-arm must never cross.
    if is_system and s.startswith(watchdog._GOAL_CLEARED_ERROR_PREFIX):
        tail = s[len(watchdog._GOAL_CLEARED_ERROR_PREFIX):]
        reason = tail.split(")", 1)[0] if tail.lstrip().startswith("(") else ""
        kind = "auth" if "authentication" in reason else "error"
        return {"state": "cleared", "clear_kind": kind,
                "payload": _parse_error_clear_payload(tail)}
    if not s.startswith(watchdog._GOAL_LCS_OPEN):
        return None
    body = s[len(watchdog._GOAL_LCS_OPEN):]
    if body.endswith(watchdog._GOAL_LCS_CLOSE):
        body = body[:-len(watchdog._GOAL_LCS_CLOSE)]
    for kind in ("set", "cleared"):
        head = "Goal %s:" % kind
        if body.startswith(head):
            payload = body[len(head):].strip()
            mark = {"state": kind, "payload": payload or None}
            if kind == "cleared":
                mark["clear_kind"] = "user"   # #675 -- a deliberate /goal clear
            return mark
    return None


def scan_goal_markers(path, off=None, tail_bytes=GOAL_MARK_TAIL_BYTES):
    """`(new_off, marker_or_None)` — the NEWEST `/goal` marker in the bytes
    read, plus the offset to resume from next time.

    `off is None` bootstraps from the file's TAIL (`tail_bytes`); a later call
    passing the returned offset reads ONLY what was appended since, so a
    long-lived 240 MB transcript costs one small read per sweep and the
    marker's AGE never matters (the caller remembers the last marker it saw).
    An `off` past EOF (a truncated/rotated file) falls back to the bootstrap.

    `new_off` always stops after the LAST COMPLETE line — a transcript is
    appended to live, so the final line may be half-written; consuming it
    would drop a real marker on the next pass.

    Fail-safe: an unreadable file returns `(off or 0, None)`; never raises."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return (off or 0, None)
    start = off
    if start is None or start > size or start < 0:
        start = max(0, size - tail_bytes)
    try:
        with open(path, "rb") as f:
            f.seek(start)
            raw = f.read()
    except OSError:
        return (off or 0, None)
    cut = raw.rfind(b"\n")
    if cut < 0:
        return (start, None)              # no complete line in this window
    new_off = start + cut + 1
    body = raw[:cut]
    if start > 0 and off is None:
        # bootstrap mid-file: the first line is probably truncated
        nl = body.find(b"\n")
        body = body[nl + 1:] if nl >= 0 else b""
    # #403 -- this used to ALSO track `arm_after` (did an "arm question" line
    # follow the newest marker), fed by `_entry_asks_to_arm`/`_GOAL_ASK_PROBE`
    # -- both existed ONLY to support the old `goal_autoarm`'s viewport-scan
    # arming, which is deleted. `arm_after` had no other reader anywhere in
    # the codebase (confirmed by grep before removing it) -- arming is now
    # driven entirely by an EXPLICIT `goal-arm --self` callback, never by
    # scanning the transcript for a printed question, so this function's
    # only remaining job is finding the newest marker itself.
    return (new_off, _newest_marker(body))


def _newest_marker(body):
    """The NEWEST (last-in-order) `/goal` marker in `body` (bytes), or None.
    Shared by `scan_goal_markers` (tail/incremental) and `seed_goal_marker`
    (#517 reverse-scan) so the parse is ONE implementation."""
    from datetime import datetime
    best = None
    for ln in body.splitlines():
        # cheap pre-filter over the raw bytes — one of the THREE shapes CC
        # writes (the `/goal` command's own stdout, the arm instruction it
        # injects — the only record a QUEUED arm leaves at all, #64 — or the
        # #675 transient-failure clear, which carries NEITHER of the other two).
        if (watchdog._GOAL_LCS_OPEN.encode() not in ln
                and watchdog._GOAL_ARM_PROBE not in ln
                and watchdog._GOAL_CLEARED_ERROR_PROBE not in ln):
            continue
        try:
            entry = json.loads(ln)
        except Exception:
            continue
        # #675 -- the error-clear shape is honoured ONLY from a `system` entry
        # (CC never writes it as `user`); a `user` paste that merely begins with
        # the clear sentence is forgeable content, not state.
        is_system = isinstance(entry, dict) and entry.get("type") == "system"
        mark = _parse_goal_marker(_goal_marker_content(entry), is_system=is_system)
        if mark is None:
            continue
        ts = None
        try:
            ts = datetime.fromisoformat(
                str(entry.get("timestamp")).replace(
                    "Z", "+00:00")).timestamp()
        except Exception:
            ts = None
        mark["ts"] = ts
        best = mark
    return best


def seed_goal_marker(path, tail_bytes=None, cap_bytes=None, block=None):
    """#517 -- FIRST-SIGHT seed of a session's newest `/goal` marker, reading
    block-wise BACKWARD from EOF so an arm sitting arbitrarily far back (state
    loss / fresh install / >tail-downtime) is still captured, up to a byte CAP.

    Returns `(new_off, marker_or_None, status)`:
      * ``"found"``            -- a marker was captured (in the tail or deeper).
      * ``"none-bof"``         -- no marker AND the scan reached BOF (the whole
                                  file was read -> definitively NOT armed).
      * ``"unknown-past-cap"`` -- no marker AND the scan was cap-truncated (an
                                  arm, if any, is deeper than `cap_bytes` back).
                                  The CALLER emits a deduped observability line;
                                  NEVER a silent not-armed, and never a fabricated
                                  armed=True.

    `new_off` is EOF-anchored EXACTLY like `scan_goal_markers(off=None)` (from
    the FIRST block == the tail), so incremental resume is byte-identical: the
    common armed-within-tail first sight exits at the first block and behaves
    identically to today. Only a deep-armed or never-armed session scans further.
    Straddling marker lines across block boundaries are reassembled via a carry.
    Fail-safe: an unreadable file returns `(0, None, "none-bof")`; never raises.

    Params default (call-time) to the module caps so a test can shrink them."""
    if tail_bytes is None:
        tail_bytes = GOAL_MARK_TAIL_BYTES
    if cap_bytes is None:
        cap_bytes = GOAL_MARK_SEED_CAP_BYTES
    if block is None:
        block = tail_bytes
    block = max(1, block)
    try:
        size = os.path.getsize(path)
    except OSError:
        return (0, None, "none-bof")
    try:
        f = open(path, "rb")
    except OSError:
        return (0, None, "none-bof")
    try:
        new_off = 0
        carry = b""            # incomplete HEAD fragment of the block just read
        hi = size
        scanned = 0
        lo = size
        first_block = True
        while hi > 0 and scanned < cap_bytes:
            lo = max(0, hi - block)
            try:
                f.seek(lo)
                chunk = f.read(hi - lo)
            except OSError:
                return (new_off, None, "none-bof")
            scanned += hi - lo
            if first_block:
                # Mirror scan_goal_markers(off=None): new_off after the LAST
                # complete line near EOF; drop the trailing (half-written) line.
                cut = chunk.rfind(b"\n")
                new_off = (lo + cut + 1) if cut >= 0 else lo
                buf = chunk[:cut] if cut >= 0 else b""
                first_block = False
            else:
                buf = chunk + carry     # reattach the straddling line's tail
            if lo > 0:
                nl = buf.find(b"\n")
                if nl < 0:
                    carry = buf         # whole buf is one incomplete line
                    hi = lo
                    continue
                carry = buf[:nl]        # incomplete head fragment (started < lo)
                body = buf[nl + 1:]     # complete lines (incl. the straddler)
            else:
                carry = b""
                body = buf              # BOF: every line is complete
            mark = _newest_marker(body)
            if mark is not None:
                return (new_off, mark, "found")
            hi = lo
    finally:
        f.close()
    reached_bof = (lo == 0)
    return (new_off, None, "none-bof" if reached_bof else "unknown-past-cap")


def pane_goal_armed(captured):
    """`True` / `False` / `None` — is CC's `◎ /goal` footer indicator lit?

    Read ONLY from the pane's trailing CHROME region (the `_is_bottom_chrome`
    peel every keystroke job here shares): the indicator renders on the `ctx …`
    statusline row, and a pane whose CONVERSATION merely mentions `◎ /goal`
    (a session discussing this very ticket) must never read as armed.

    `None` = UNDETERMINABLE, never a guess: the input box could not be located
    (a BUSY pane's spinner occupies the boundary, a scrolled pane, an empty
    capture) or nothing was rendered below it, so neither "armed" nor "dark"
    can be claimed.

    The footer is defined as everything BELOW the input box — NOT as
    "`_is_bottom_chrome` rows" and NOT as "the row starting with `ctx `".
    Live 2026-07-26: a freshly launched session renders the managed statusline
    without caveman's `ctx …` block, a row `_is_bottom_chrome` does not
    classify as chrome at all — so a peel-based read stopped ABOVE the very
    row the indicator sits on, and a `ctx `-prefix guard declared the whole
    pane unreadable. A SELECTED agent-strip row (`❯ ● main`, #36) renders
    below the box and is excluded from the boundary search.

    #383 — non-blank `footer` content is NOT by itself proof the real
    chrome region is even in view. A draft too tall to fit the remaining
    viewport (job 20's own re-arm delivery left one PARKED, unsent, after a
    paste-pending failure) renders as `❯ <head>` followed by many more
    wrapped CONTINUATION rows of the SAME box, filling the capture down to
    its last line — those rows are more box content, never chrome, and the
    real closing border + statusline + `◎ /goal` glyph have scrolled off
    past the bottom of what was captured. Live-confirmed, read-only, on
    camera-box's own stuck pane (session zbynek-4:0, pane %1, 2026-08-11):
    the capture's LAST line was still draft text, with a genuinely live,
    actively-incrementing `◎ /goal active (Nm)` glyph rendered entirely
    OUTSIDE `footer` (above the box's own top border), and the old body
    returned a confident `False` on it. So a `footer` with content but with
    NO row that itself reads as real trailing chrome (`_is_bottom_chrome`,
    the SAME peel used to find the box's own head) cannot support a `False`
    verdict either — the indicator's own home row is not provably in view,
    so the answer is `None`. Verified this reuses `_is_bottom_chrome` (no
    new pattern) without tripping on the repo's own real `/goal` templates
    wrapped at 80/120/176 columns, and without disturbing the pre-existing
    `test_statusline_without_a_ctx_segment_still_reads` case (a mode-hint
    row alone already counts as chrome, independent of the statusline
    row's own segment-count threshold).

    A bare BORDER-RULE row alone does NOT count as that chrome evidence,
    even though `_is_bottom_chrome` itself does classify one: the box's own
    closing border can be the LAST thing that fit in the capture, with the
    real statusline/mode-hint/agent-strip rows (and the `◎ /goal` glyph
    riding on the statusline one) cut off immediately after it — a border
    alone proves the box CLOSED, never that the indicator's own row is in
    view. Every other chrome shape (`ctx `, the >=2-segment statusline, the
    mode hint, an agent-strip row, a selected-strip row, the selector hint)
    still counts on its own; none of them can render without the row that
    would carry the indicator already being on screen.

    #383-review Finding 2 — the chrome evidence must additionally be part
    of an UNBROKEN trailing block ending at the capture's own last line
    (`_trailing_bottom_chrome`), not merely present SOMEWHERE in `footer`:
    a chrome-shaped row (e.g. a pasted quote of a rendered statusline,
    live-triggered — see that helper's own docstring) sitting partway
    through ordinary draft continuation, with MORE draft rows still
    following it, must not count. Real chrome never has draft content
    rendered below it.

    #383-review Finding 3 (theoretical, unconfirmed live) — the trailing
    walk trusts an agent-strip/selected-strip/selector-hint row ON ITS OWN
    even without a statusline row also in the walk. This is safe ONLY
    because `footer` is one CONTIGUOUS suffix of the whole capture (never a
    subset with a gap) and every real CC render this repo has ever captured
    puts the statusline row STRICTLY ABOVE (i.e. earlier in) the agent
    strip — so an agent-strip row reaching the trailing walk structurally
    implies the statusline row above it is ALSO still inside `footer`
    (nothing between idx+1 and the strip row is ever skipped). If a future
    CC layout ever renders the strip BEFORE the statusline, this reasoning
    — and the population of sessions it protects, autopilot supervisors
    with visible worker rows — would need re-checking."""
    lines = (captured or "").splitlines()
    idx = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("❯") and not watchdog._is_bottom_chrome(s):
            idx = i
    if idx is None:
        return None                        # no input box in view
    footer = lines[idx + 1:]
    if not any(ln.strip() for ln in footer):
        return None                        # nothing rendered below it
    trailing = watchdog._trailing_bottom_chrome(footer)
    # #386 (mirror of #383) -- trust the `◎ /goal` footer indicator ONLY when
    # it rides a genuine TRAILING-chrome row (the statusline in the same
    # unbroken chrome block that ends the capture), NEVER a bare substring
    # anywhere in `footer`. A parked, UNSENT DRAFT renders its wrapped
    # CONTINUATION rows BELOW the `❯` head (so they land in `footer`), so a
    # draft that merely QUOTES the glyph read as armed even on a genuinely
    # dark goal -- the exact False-direction gap #383 closed, one branch over.
    # Reusing the SAME `_trailing_bottom_chrome` peel the #383 check below
    # already needs (computed once) excludes a chrome-shaped-but-mid-draft
    # row by construction (its own Finding-2 backward walk stops at the first
    # non-chrome draft row), while the real legacy render (glyph appended to
    # the statusline chrome row) is inside that block and still matches. The
    # #388 header path below is untouched.
    if any(watchdog.GOAL_INDICATOR in s for s in trailing):
        return True
    if not any(not watchdog._is_border_rule(s) for s in trailing):
        return None                        # #383: footer is box content, a
                                            # chrome-shaped row buried mid-
                                            # draft, or a bare closing
                                            # border with nothing past it --
                                            # not real trailing chrome --
                                            # real footer is off-screen,
                                            # "dark" unproven
    # #388: before declaring the goal dark, check the box's HEADER. Current
    # Claude Code renders `◎ /goal active (Nm)` on a standalone line directly
    # ABOVE the input box's top-border rule (not on the statusline row in the
    # footer this function scans). Confirmed live 2026-08-11 on 3 armed panes
    # (camera-box, airuleset, forestshop). The real indicator line, stripped,
    # STARTS WITH the indicator (right-aligned, alone on its line); a
    # conversation MENTION has it embedded mid-prose. #393 tightened the
    # match from a bare `.startswith` to `_GOAL_HEADER_INDICATOR_RX` --  a
    # wrapped-prose CONTINUATION row can also start with the glyph+word
    # (unlike a mid-prose mention, which never starts a rendered line with
    # it at all), so the tail must additionally look like the real render
    # (short, no sentence punctuation) rather than trailing prose. The
    # 3-line bound keeps the search inside the box header, never reaching
    # the conversation. Placed here (only when the footer path would
    # otherwise say False) so every #383 `None` branch above is preserved.
    header = lines[max(0, idx - 3):idx]
    if any(watchdog._GOAL_HEADER_INDICATOR_RX.match(ln.strip()) for ln in header):
        return True
    return False
