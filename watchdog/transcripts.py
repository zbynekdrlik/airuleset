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


def transcript_last_marker_line(path):
    """(marker, full marker LINE) of the session's last real assistant message,
    or ('', ''). Same walk/semantics as transcript_last_marker — the LINE feeds
    declared_wait_until (a ⏳ that names a future clock time is a healthy wait,
    not a stall; the 2026-07-20 drilling incident)."""
    for entry in reversed(_iter_jsonl_tail(path)):
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
