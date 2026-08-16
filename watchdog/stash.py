"""Stash-around delivery + draft-rescue + own-stuck-content keystroke machinery.

Extracted verbatim from ``watchdog/__init__.py`` as item G step 5 of the
definitive module split (issue #433). This is the SAFETY-CRITICAL keystroke
cluster (#372 lineage): it types the watchdog's OWN text into a live Claude
Code pane WITHOUT destroying a human's in-progress draft. Native prompt STASH
(Ctrl+S, issue #35) parks a FOREIGN draft into CC's single, silently-overwriting
slot; we then type + submit our text, verify it landed against a FRESH capture,
and let CC auto-restore the parked draft when the delivered turn completes. Any
surprise aborts to the caller's pre-#35 fallback rather than guess — never data
loss, never a swallowed submit. Alongside the delivery path live the
draft-rescue persistence helpers (a durable on-disk copy of a draft before any
risky delivery: ``draft_rescue_dir`` / ``_draft_rescue_persist`` and friends)
and the #372 own-stuck-content ownership tests (``_looks_like_own_payload`` /
``_looks_like_own_stuck_content``) the janitor uses to know a stuck box can ONLY
be OUR own delivery, never a foreign draft.

Direction: back-reference module. Every cross-reference to a name that was a
top-level ``watchdog`` name goes through the package namespace CALL-TIME
(``import watchdog`` at module top; ``watchdog.<name>(...)`` in bodies) — the
``__init__.py`` C3 convention that keeps every ``monkeypatch.setattr(watchdog,
"<name>", ...)`` / ``patch.object(wd, "<name>")`` seam resolving identically to
before the move. That covers primitives still resident elsewhere
(``watchdog.capture_pane`` / ``_input_line_text`` / ``_input_box_rows_raw`` /
``_normalize_queued_hint`` / ``_is_draft_head`` / ``_default_run`` /
``_has_free_prompt`` / ``_strip_selected`` / ``_box_is_wrapped``) AND the
intra-module co-moved cross-calls (``deliver_with_stash`` -> ``_type_literal`` /
``_await_stash_settled`` / ``_typed_landed`` / ``_typed_exclusively`` /
``_pane_shows_collapsed_paste`` / ``_undo_and_release_slot`` /
``_undo_appended_text`` / ``_draft_rescue_persist``; ``_draft_rescue_persist`` ->
``draft_rescue_dir`` / ``_draft_rescue_text`` / ``_draft_rescue_ensure_dir`` /
``_draft_rescue_prune``; ``_draft_rescue_prune`` -> ``draft_rescue_dir``;
``_undo_and_release_slot`` -> ``_undo_typed_text``;
``_looks_like_own_stuck_content`` -> ``_looks_like_own_payload``).
``draft_rescue_dir`` and ``_draft_rescue_persist`` are grep-proven test seams
(conftest patches ``wd.draft_rescue_dir``; a ``test_draft_rescue`` spy patches
``wd._draft_rescue_persist`` then calls ``deliver_with_stash``), so those MUST go
through the package seam — and for uniform keystroke-safety every co-moved
cross-call does.

The co-moved module CONSTANTS (``STASH_*`` / ``DRAFT_RESCUE_*`` /
``GOAL_TYPE_CHUNK_*`` / ``_PASTED_PLACEHOLDER_RX`` / ``_PASTE_EXPAND_HINT_RX`` /
``_JANITOR_OWN_PREFIXES`` / ``JANITOR_CLEAR_*`` / ``JANITOR_WATCH_MAX_AGE_S`` —
the last four read by ``janitor.py`` via ``watchdog.<CONST>``) are referenced
BARE in bodies: the step-5 C5 grep found NO test patches any of them, so a
module-local read is correct and byte-verbatim. Every one of the 39 moved names
(16 functions + 23 constants) is re-exported into the ``watchdog`` namespace by
the positional facade import in ``__init__.py`` (replacing the earliest removed
line), so all existing ``watchdog.<name>`` seams keep resolving unchanged.
"""

import os
import re
import time
from pathlib import Path

import watchdog


# --------------------------------------------------------------------------- #
# Stash-around delivery (issue #35) — CC's native prompt STASH (Ctrl+S) lets
# the watchdog deliver its own text into a pane that is idle but holds a
# FOREIGN draft, without losing that draft: park it, type + submit ours, and
# CC auto-restores the parked draft the instant the delivered turn completes
# (empirically verified against CC 2.1.220 — NOT a second Ctrl+S press, which
# the docs wrongly imply). The stash is SINGLE-SLOT with a SILENT overwrite,
# so every step is verified against a fresh capture and any surprise aborts
# to the caller's pre-#35 fallback (pending / ticket-fallback / skip) —
# never a guess, never data loss.
# --------------------------------------------------------------------------- #

STASH_MARKER = "› stashed"          # "› stashed" — CC's stash-slot indicator

# CC COLLAPSES a long literal `send-keys -l` into a single placeholder row
# (`[Pasted text #3]`) instead of rendering the text — live-observed
# 2026-07-26 on job 20's first real re-arm (a 3152-char /goal). Every
# verified delivery here matches the typed text against the boundary line,
# which can NEVER match that placeholder; the check only ever worked because
# previous callers all sent short text.
#
# #372 — CC also renders a SECOND, MULTI-LINE collapse shape for the same
# kind of long single-line send: `[Pasted text #N +K lines]` (live incident,
# david2@subdev, 2026-08-11 — even AFTER #354's own settle-poll fix). A
# single-line-only regex refuses to recognize this as landed, so a
# GENUINELY SUCCESSFUL delivery gets declared a verify FAILURE, which then
# triggers a destructive undo (`_undo_typed_text`) against a box its own
# docstring explicitly assumes it will never have to reason about — the
# undo backspaces `len(payload)` (~3800) characters against what is really
# a ~26-char placeholder, sometimes failing to confirm bare within the
# settle-poll's budget and leaving the box AND the single stash slot stuck.
# Rather than enumerate this exact second shape as its own alternative (CC
# has now shown 2 distinct collapse renderings sharing the same `[Pasted
# text #N` prefix — assume there will be a 3rd), the regex is PREFIX-
# anchored and suffix-TOLERANT: `[Pasted text #N` followed by anything,
# closing on `]`. This is deliberately more permissive than enumerating
# every observed suffix, and is safe: nothing else in a Claude Code pane's
# input box legitimately renders this literal prefix.
_PASTED_PLACEHOLDER_RX = re.compile(r"^\[Pasted text #\d+.*\]$")


def _typed_landed(text, itext):
    """True if the input-box content `itext` is evidence that `text` was
    typed IN FULL — either the literal tail (a short type renders verbatim;
    a WRAPPED one puts its tail on the boundary line, hence endswith) or
    CC's collapsed `[Pasted text #N]` placeholder for a long one. A
    genuinely TRUNCATED partial type matches neither and is still refused —
    submitting one is the #36 disaster this verification exists to prevent."""
    if not itext:
        return False
    if text.endswith(itext):
        return True
    return bool(_PASTED_PLACEHOLDER_RX.match(itext.strip()))


# #322 — CC 2.1.226 introduced a SECOND, DIFFERENT paste-collapse shape: a
# single long literal `send-keys -l` burst renders as `paste again to
# expand` rather than `[Pasted text #N]` — an INCOMPLETE/unexpanded paste
# that is never parsed as a slash command. Pressing Enter on it submits
# whatever content IS committed as an ordinary chat message instead of
# arming the goal — the exact live incident (dev1's own job 20, and
# montalu2@subdev, both CC 2.1.226): the delivery "succeeds" with no error,
# the model receives the `/goal ...` text as a normal prompt, and the goal
# never arms. A controlled live experiment DISPROVED the earlier "busy
# pane" hypothesis directly — a 23-char payload armed fine on a BUSY pane,
# while a 3960-char one failed on an IDLE one; the only variable that
# discriminated pass/fail was LENGTH, and typing the SAME long payload in
# small chunks (instead of one burst) armed correctly every time.
GOAL_TYPE_CHUNK_THRESHOLD = 200      # below this, unchanged single-burst
                                     # send-keys (never observed to trigger
                                     # the collapse at this size)
GOAL_TYPE_CHUNK_SIZE = 120
GOAL_TYPE_CHUNK_DELAY_S = 0.12
_PASTE_EXPAND_HINT_RX = re.compile(r"paste again to expand", re.I)


def _pane_shows_collapsed_paste(itext):
    """True when the input box shows CC's 'paste again to expand' hint — an
    incomplete/unexpanded paste. `_type_literal`'s chunking exists
    specifically to avoid ever reaching this state; this check is a fast,
    explicit abort for the case something still does (a coalesced terminal
    write, a lower-than-expected collapse threshold), rather than relying
    on the generic type-verify timeout to eventually give up."""
    return bool(itext) and bool(_PASTE_EXPAND_HINT_RX.search(itext))


def _type_literal(pid, run, text, sleep_fn=None):
    """Send `text` into the pane's input box literally — in ONE burst below
    `GOAL_TYPE_CHUNK_THRESHOLD` chars (unchanged from before this ticket),
    or in small CHUNKS at/above it (#322) so CC never treats the whole
    payload as one terminal-paste event.

    #322 REOPENED (adversarial-review CRITICAL-1, both live-verified against
    a real tmux 3.7b pane): `tmux send-keys -l <chunk>` parses `<chunk>` as a
    getopt-style ARGUMENT — a chunk whose first character happens to be `-`
    (e.g. a 120-char slice landing mid-word on "...`-self` (holds..." in a
    real shipped template) is read as an unknown FLAG and the whole call
    fails (`command send-keys: unknown flag -X`, rc 1). `_default_run`
    silently swallows a non-zero-rc command as `""` (no exception, no log),
    so the chunk is DROPPED with zero indication — every later chunk still
    lands, so `_typed_landed`'s tail-based `endswith()` check is satisfied
    by the (now internally corrupted) remainder, and a GOAL WITH A
    120-CHARACTER HOLE IN THE MIDDLE gets armed. This is a materially WORSE
    outcome than the paste-collapse bug this function exists to fix (which
    armed nothing, rather than arming something silently wrong) — and the
    same `-l` call is on `deliver_with_stash`'s only type path, so an
    arbitrary Discord-reply `prompt` (job 7) can be mangled and SUBMITTED
    the identical way. `--` (end-of-options) makes every following argument
    literal regardless of a leading `-`; live-verified: `send-keys -l --
    '-DASH-OK'` lands the literal text, `send-keys -l '-DASH-OK'` (no `--`)
    fails exactly as above. Applied to BOTH the single-burst and the
    chunked path — a single-burst payload can equally start with `-` (job
    7's arbitrary `prompt` argument is not `/goal`-prefixed)."""
    sleep_fn = sleep_fn or time.sleep
    if len(text) < GOAL_TYPE_CHUNK_THRESHOLD:
        run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
        return
    for i in range(0, len(text), GOAL_TYPE_CHUNK_SIZE):
        run(["tmux", "send-keys", "-t", pid, "-l", "--",
            text[i:i + GOAL_TYPE_CHUNK_SIZE]])
        if i + GOAL_TYPE_CHUNK_SIZE < len(text):
            sleep_fn(GOAL_TYPE_CHUNK_DELAY_S)


STASH_VERIFY_SETTLE_POLLS = 3      # bounded: a `C-s` toggle's render can lag
STASH_VERIFY_SETTLE_S = 0.3        # behind the keystroke actually landing (#176 F4)

# The four honest outcomes of our own `C-s` toggle (#189). The mechanism used
# to collapse them into one boolean ("did the box go bare WITH the marker?")
# and treat everything else as a failed stash — which is how an EMPTY box
# became an error condition, and why zero `continue`s were delivered in 24h
# box-wide. Each outcome now names a genuinely different pane state and gets
# a genuinely different recovery.
STASH_PARKED = "parked"            # bare + marker lit: a real draft is in the slot
STASH_NOOP = "noop"                # bare, no marker: there was nothing to park
STASH_UNRESOLVED = "unresolved"    # still shows content, no marker: a rendered
                                   # ghost suggestion, or a genuinely lost
                                   # keystroke — DELIBERATELY not distinguished
STASH_NO_BOUNDARY = "no-boundary"  # the input line itself is gone (spinner /
                                   # dialog / unreadable): touch nothing more


def _await_stash_settled(pid, run, sleep_fn):
    """Poll (bounded) for the pane to settle after our OWN `C-s`, and report
    WHICH of the four states above it settled into.

    Still a render-SETTLE poll, never a blind timeout — it returns the instant
    the box agrees, because an IMMEDIATE re-capture can show the UNCHANGED
    screen even though the toggle already landed server-side (#176 F4).

    What changed in #189 is the question being asked. The old poll demanded
    `input == "" AND marker lit` and called anything else a failure. But a
    bare box with NO marker is not a failure at all: it means there was
    nothing to park, and stashing nothing is a no-op. Since Claude Code's grey
    prompt suggestion renders as ordinary text once `capture-pane -p` strips
    its SGR attributes, "the box looks non-empty" was never evidence that a
    draft existed — so the poll reports what it can actually see and lets the
    caller act on each state, rather than folding three deliverable states
    into one abort. Returns (last_capture, outcome, boundary_text) where
    boundary_text is the input line as it stands at the end (None when there
    is no input line at all)."""
    cap = None
    for i in range(STASH_VERIFY_SETTLE_POLLS):
        cap = watchdog.capture_pane(pid, run, lines=30)
        if watchdog._input_line_text(cap) == "":
            return cap, (STASH_PARKED if STASH_MARKER in (cap or "")
                         else STASH_NOOP), ""
        if i < STASH_VERIFY_SETTLE_POLLS - 1:
            sleep_fn(STASH_VERIFY_SETTLE_S)
    itext = watchdog._input_line_text(cap)
    if itext is None:
        return cap, STASH_NO_BOUNDARY, None
    return cap, STASH_UNRESOLVED, itext


def _typed_exclusively(text, itext):
    """True only if the input box holds NOTHING BUT `text` — the STRICT form of
    `_typed_landed`, required when we typed into a box that visibly held
    something (#189).

    `_typed_landed` accepts a TAIL of `text` on the boundary line, because a
    wrapped input renders its tail there. That allowance is safe on a box we
    verified bare first, but not on a box that already showed content: if the
    content was a real draft our text APPENDS to it, and a wrap landing inside
    our own text would leave a boundary that is a legitimate suffix of `text`
    — passing the loose check and submitting the user's draft with our text
    glued on. Requiring an exact match (or Claude Code's collapsed
    `[Pasted text #N]` placeholder for a long payload) has no such hole: a
    ghost suggestion is REPLACED by the keystroke, so the box holds exactly
    our text, while a real draft never can."""
    if not itext:
        return False
    if itext == text:
        return True
    return bool(_PASTED_PLACEHOLDER_RX.match(itext.strip()))


# Bounds how many backspaces an undo may ever spray at a pane. It sat at 400,
# BELOW a real payload — the gk-request nudge is 458 chars — so the recovery
# #193 added for a box verified bare could not have run for the very delivery
# that stranded its text. Sized off the largest payload this helper actually
# carries: job 20 delivers `"/goal " + <condition>`, and Claude Code caps that
# condition at 4000 (#169), so 4096 covers the whole line with headroom rather
# than refusing it by six characters.
STASH_UNDO_MAX_BACKSPACES = 4096

# Backs `_undo_typed_text`/`_undo_appended_text`'s own post-backspace verify
# (#354). A single immediate capture right after backspacing can read the
# box as still non-bare purely from render lag — the SAME mechanism #176 F4
# already found and fixed for `deliver_with_stash`'s own post-toggle verify
# (`_await_stash_settled`), never before extended to this post-backspace
# verify. Reported live on gatekeeper (#354): two real deliveries logged
# "stash-abort: typed-NOT-undone, draft left parked" with the /goal text
# visibly stuck, unsent, in the pane — the user's own draft left
# permanently parked in the single stash slot because the (genuinely
# successful, just not-yet-rendered) undo was declared failed.
#
# `_await_stash_settled` is the SHAPE precedent (bounded-poll-never-a-blind-
# timeout, #176 F4) but NOT the magnitude one — its 3x0.3s budget backs a
# single `C-s` toggle, a far smaller render event than backspacing up to
# `STASH_UNDO_MAX_BACKSPACES` individual keystrokes. The MAGNITUDE here is
# deliberately borrowed from `GOAL_TYPE_SETTLE_POLLS`/`_S` (`_await_typed`)
# instead — this job's own already-established budget for the render lag a
# multi-KB PASTE causes, the closer analogue by payload size. Accepted cost
# (adversarial-review finding, #354): ~12x `_await_stash_settled`'s own
# budget, worst case ~7s added to ONE pane's delivery inside job 20's own
# per-sweep wall-clock budget — reached only on a genuine settle-window-
# exhausting failure, never on the already-working happy path.
STASH_UNDO_SETTLE_POLLS = 8
STASH_UNDO_SETTLE_S = 1


def _undo_appended_text(pid, run, pre_text, text, sleep_fn=None):
    """Remove exactly the characters WE typed from a box that already held
    `pre_text`, and VERIFY the box came back to `pre_text` (#189).

    Reached only when nothing was parked and the box shows the exact append
    signature `pre_text + text` — i.e. our own keystroke landed on top of a
    real draft because the stash toggle was lost. A restoring `C-s` would be
    the WRONG recovery here: with an empty slot it would PARK the polluted
    text, jamming the single slot into the one decline this design keeps and
    leaving the user's draft invisible. Backspacing our own characters is the
    only recovery that touches nothing of the user's. Returns whether the
    draft verifiably came back.

    #354 — a bounded settle poll (never a blind timeout — returns the
    instant the box agrees) backs the verify, see `STASH_UNDO_SETTLE_POLLS`'s
    own comment for why a single immediate capture is not enough."""
    if not text or len(text) > STASH_UNDO_MAX_BACKSPACES:
        return False
    sleep_fn = sleep_fn or time.sleep
    run(["tmux", "send-keys", "-t", pid] + ["BSpace"] * len(text))
    for i in range(STASH_UNDO_SETTLE_POLLS):
        if watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=30)) == pre_text:
            return True
        if i < STASH_UNDO_SETTLE_POLLS - 1:
            sleep_fn(STASH_UNDO_SETTLE_S)
    return False


def _undo_typed_text(pid, run, text, sleep_fn=None):
    """Remove exactly the characters WE typed from a box that was VERIFIED
    BARE immediately before we typed, and confirm it came back to bare (#193).

    Reached only from the PARKED / NOOP outcomes, where the settle poll had
    already read `_input_line_text(cap) == ""`. Every character in the box is
    therefore ours, so backspacing exactly `len(text)` provably cannot reach
    anything of the user's — whatever fraction of the type actually landed, a
    surplus backspace lands on an empty box. That proof is a property of the
    BOX (observed bare by this function's own caller a moment earlier), never
    of who called or of what they passed.

    Note it can only run at all when the verify FAILED: a payload Claude Code
    collapsed into `[Pasted text #N]` verifies through that placeholder and is
    submitted, so this never has to reason about a collapsed buffer.

    #354 — a bounded settle poll (never a blind timeout — returns the
    instant the box agrees) backs the verify, see `STASH_UNDO_SETTLE_POLLS`'s
    own comment for why a single immediate capture is not enough."""
    if not text or len(text) > STASH_UNDO_MAX_BACKSPACES:
        return False
    sleep_fn = sleep_fn or time.sleep
    run(["tmux", "send-keys", "-t", pid] + ["BSpace"] * len(text))
    for i in range(STASH_UNDO_SETTLE_POLLS):
        if watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=30)) == "":
            return True
        if i < STASH_UNDO_SETTLE_POLLS - 1:
            sleep_fn(STASH_UNDO_SETTLE_S)
    return False


def draft_rescue_dir():
    """`~/.claude/draft-rescue/`, resolved at CALL time (same reasoning as
    `watchdog.compact.compact_requests_path()`: never a frozen module-level
    constant, so a
    relocated `$HOME`/override is honoured on every call, not just the one
    that happened to run at import time).

    `AIRULESET_DRAFT_RESCUE_DIR`, when set, overrides the default. This is
    NOT a security boundary (there is nothing to bypass here — see the
    vault-store env-override lesson in the playbook, which is about a
    guard an attacker could defeat; this is a plain write-only, self-pruning
    diagnostic directory nothing else reads or acts on). It exists so
    `airuleset.py`'s own push-gate test-suite subprocess (`cmd_push`) can
    point the WHOLE `unittest discover` run at a throwaway directory in one
    place, instead of adding per-file test isolation to every one of the
    ~19 test files whose fixtures transitively reach `deliver_with_stash`/
    `_send_goal_verified` via `run_once` (#271) — the live systemd watchdog
    executes this repo's own working tree every 60s, so a test process that
    wrote into the REAL directory would be indistinguishable from production
    activity. A single test wanting a precise, deterministic rescue-file
    assertion still patches this function directly
    (`unittest.mock.patch.object(wd, "draft_rescue_dir", return_value=<tmp>)`
    — the same established shape `watchdog.compact.compact_requests_path()`
    already uses)."""
    override = os.environ.get("AIRULESET_DRAFT_RESCUE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "draft-rescue"


# 14 days: deliberately generous (#271). This file exists ONLY so a human can
# recover text nothing else could save — the cost of keeping one around too
# long is a few KB, the cost of pruning too eagerly is the exact
# unrecoverable loss this mechanism exists to prevent. There is no "restore
# confirmed, safe to delete now" signal available to this process (the
# restore is Claude Code's own async, on-screen-only effect, fired once the
# DELIVERED turn completes — minutes away, and never observed here), so age
# is the only thing that ever removes a rescue file.
DRAFT_RESCUE_TTL_S = 14 * 24 * 3600

# `<safe-pid>-<epoch-ms, >=10 digits>[-<retry-n>].txt` — the EXACT shape
# `_draft_rescue_persist` writes. `_draft_rescue_prune` matches against this
# (adversarial-review MAJOR finding, #271) rather than unlinking every name
# in the directory: a misconfigured `AIRULESET_DRAFT_RESCUE_DIR` pointed at
# an already-populated directory must never let a routine prune delete
# unrelated content it happens to share a mtime-age with.
_DRAFT_RESCUE_NAME_RX = re.compile(r"^.+-\d{10,}(?:-\d+)?\.txt$")


def _draft_rescue_text(captured):
    """Best-effort plain-text reconstruction of the pane's CURRENT input-box
    content — "" when the box is bare or unlocatable (nothing to rescue).

    Never the exact original bytes: a WRAPPED draft loses its true line
    breaks the instant Claude Code re-flows it (#189) — this is the
    RENDERED rows (`_input_box_rows_raw`, already head-first and chrome-
    stripped) joined back together, with the leading `❯` glyph + its
    separator stripped off the head row only. Enough for a human to read
    and retype, which is the whole point of a rescue file.

    `_input_box_rows_raw`'s own glyph-based fallback (a borderless capture)
    deliberately returns exactly ONE row and never more — so a borderless
    capture can never pull agent-strip/chrome text into a rescue file; only
    the STRUCTURAL (bordered) strategy can return multiple rows, and it
    returns strictly the box's own interior, already guarded (#243) against
    a transcript-quoted box being misread as the real one."""
    rows = watchdog._input_box_rows_raw(captured)
    if not rows:
        return ""
    head = watchdog._normalize_queued_hint(rows[0])
    if not watchdog._is_draft_head(head):
        return ""            # bare box ("❯" alone) — nothing to rescue
    first = head[1:].lstrip(" \xa0")
    body = [first] + list(rows[1:])
    return "\n".join(ln for ln in body if ln)


def _draft_rescue_prune(now, dir_path=None, ttl_s=None):
    """Remove rescue files older than the TTL. Best-effort (never raises) —
    called inline from EVERY write (`_draft_rescue_persist`), never a
    separate job: the repo FREEZE forbids a new numbered watchdog job, and
    there is no "confirmed delivered" event to hang a dedicated sweep off
    of anyway (see `DRAFT_RESCUE_TTL_S`'s own docstring) — an inline prune
    on every write is sufficient to keep the directory bounded.

    Only names matching `_DRAFT_RESCUE_NAME_RX` are ever candidates for
    deletion — never a bare "everything in this directory" sweep."""
    dir_path = dir_path or watchdog.draft_rescue_dir()
    ttl_s = DRAFT_RESCUE_TTL_S if ttl_s is None else ttl_s
    try:
        names = os.listdir(dir_path)
    except OSError:
        return
    for name in names:
        if not _DRAFT_RESCUE_NAME_RX.match(name):
            continue
        p = Path(dir_path) / name
        try:
            # A matching-named SYMLINK is never one of our own writes (we
            # only ever create real files, via O_EXCL|O_NOFOLLOW) — remove
            # it unconditionally, on age or not. `Path.unlink()` removes the
            # link itself, never follows it, so this is safe either way.
            if os.path.islink(p) or now - p.stat().st_mtime > ttl_s:
                p.unlink()
        except OSError:
            continue


def _draft_rescue_ensure_dir(dir_path):
    """Create `dir_path` as an owner-only (0700) directory if missing, and
    refuse — creating nothing — if it is, or becomes, a symlink.

    Mirrors this repo's own vault-store discipline (checked BEFORE *and*
    AFTER `os.makedirs`, since that call happily succeeds against a symlink
    to an existing directory and a later `chmod` would then tighten the
    TARGET, not a real directory of our own) — these boxes host foreign
    uids by design, and the rescue directory holds arbitrary user-typed
    text that can include a pasted credential (adversarial-review CRITICAL
    finding, #271). Returns True only when `dir_path` is a genuine,
    owner-only directory ready to receive a write."""
    try:
        if os.path.islink(dir_path):
            return False
        os.makedirs(dir_path, mode=0o700, exist_ok=True)
        if os.path.islink(dir_path):
            return False
        os.chmod(dir_path, 0o700)
        return True
    except OSError:
        return False


# Bounds the filename-collision retry loop below — two rescue writes for the
# SAME pane landing in the same millisecond needs two full tmux round-trips
# inside 1ms, which is not realistic in production, but `O_EXCL` makes the
# collision loud (FileExistsError) instead of a silent O_TRUNC overwrite, so
# bound the retry rather than loop forever on a persistently occupied name.
_DRAFT_RESCUE_MAX_RETRIES = 5


def _draft_rescue_persist(pid, captured, now=None, dir_path=None, logs=None):
    """Persist the pane's CURRENT input-box content to disk BEFORE any
    keystroke of OURS lands in it (#271).

    `deliver_with_stash` parks a real draft via `C-s` and Claude Code
    auto-restores it once the delivered turn completes — but that restore is
    entirely on-screen and asynchronous; nothing in this process ever
    observes it landing. The reported incident (2026-08-06): a long typed
    message sat in the box, a goal-autoarm delivery ran, and the draft was
    gone with NO trace at all — CC's input box renders in-place via cursor
    addressing, so unsent text never even enters tmux scrollback (confirmed
    against the real incident capture, forensics comment on this issue).

    So every primitive that is about to touch a pane's box
    (`deliver_with_stash`, `_send_goal_verified`) calls this FIRST,
    unconditionally, with the SAME capture it is about to act on — before
    its own first `send-keys`. Stash+restore stays the PRIMARY recovery
    path; this file is the safety net and is NEVER deleted here on a claimed
    success (there is no verifiable "the restore actually landed" signal to
    delete on) — only `_draft_rescue_prune`'s generous TTL, run inline on
    every write, ever removes one. `send_continue` (job 1/4/7's short-nudge
    primitive) needs no equivalent: it only ever types into a box its own
    caller already verified bare, and on the rare swallowed-submit case a
    stuck draft is APPENDED to and later submitted or auto-recovered — never
    silently discarded the way an unrestored stash can be.

    The write is owner-only (0600, via `os.O_EXCL | os.O_NOFOLLOW` — never
    followed through a planted symlink, never silently overwritten by a
    filename collision) into an owner-only (0700) directory
    (`_draft_rescue_ensure_dir`) — the content can be arbitrary user-typed
    text, including a pasted credential (adversarial-review CRITICAL
    finding, #271), and these boxes host foreign uids by design.

    Returns the path written, or None (box was bare, the directory could not
    be secured, or the write failed after exhausting the collision-retry
    budget — best-effort; never blocks the delivery it is protecting, but
    now LOGS why whenever it fails with a real draft in hand, so a failure
    here is never as silent as the incident this whole mechanism exists to
    prevent)."""
    text = watchdog._draft_rescue_text(captured)
    if not text:
        return None
    now = time.time() if now is None else now
    dir_path = dir_path or watchdog.draft_rescue_dir()
    if not watchdog._draft_rescue_ensure_dir(dir_path):
        if isinstance(logs, list):
            logs.append("draft-rescue: FAILED to secure %s (pane %s, %d "
                        "chars would have been lost with no trace)"
                        % (dir_path, pid, len(text)))
        return None
    watchdog._draft_rescue_prune(now, dir_path=dir_path)
    safe_pid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(pid)).strip("_") or "pane"
    # #479 -- content dedup: a LIVE draft parked across sweeps (a stash slot
    # held by the user's OWN text, retype-verify-failed every ~60s) must NOT
    # spawn a fresh rescue file every sweep -- the 2026-08-14 storm wrote the
    # SAME 702-char draft 7x over ~3h on pane %1, and the 14-day TTL held all
    # of them. If an existing rescue file of THIS pane already holds
    # byte-identical content, reuse it: refresh its mtime so the TTL prune
    # keeps the one surviving copy while the draft is still parked, and skip
    # the duplicate write. Scoped to OUR OWN filename shape for THIS pane
    # (`<safe_pid>-<digits>[-<n>].txt`) -- a tighter, pane-specific form of
    # `_DRAFT_RESCUE_NAME_RX` -- so it can never dedup against unrelated
    # content a misconfigured rescue dir might hold, and never across panes
    # (each is an independent conversation). A genuinely EDITED draft is
    # different content -> a fresh rescue, so the safety net is never weakened.
    pane_rx = re.compile(r"^" + re.escape(safe_pid) + r"-\d+(?:-\d+)?\.txt$")
    try:
        names = sorted(os.listdir(dir_path))
    except OSError:
        names = []
    for name in names:
        if not pane_rx.match(name):
            continue
        cand = Path(dir_path) / name
        # Open with O_NOFOLLOW (matching the write path's O_EXCL|O_NOFOLLOW
        # discipline, #271) and operate on that ONE fd for BOTH the content
        # compare and the mtime refresh: O_NOFOLLOW refuses a planted symlink
        # outright, and a single fd removes the check-then-use race a separate
        # islink()+read_text()+utime(path) sequence would carry (adversarial-
        # review MINOR, #479). The dir is already an owner-only 0700 of ours,
        # so this is defense-in-depth, not the sole guard.
        try:
            fd = os.open(str(cand), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            continue                    # symlink / vanished / unreadable
        try:
            with os.fdopen(fd, "rb") as f:
                if f.read().decode("utf-8") != text:
                    continue            # different content -> a fresh rescue
                refreshed = "ok"
                try:
                    os.utime(f.fileno(), (now, now))
                except OSError as exc:
                    refreshed = "failed:%r" % (exc,)   # surfaced, never silent
        except (OSError, ValueError):
            continue                    # read error / non-utf8 -> not a match
        if isinstance(logs, list):
            logs.append("draft-rescue: identical content already parked "
                        "(pane %s, %d chars, mtime-refresh=%s) -> %s"
                        % (pid, len(text), refreshed, cand))
        return str(cand)
    base = "%s-%d" % (safe_pid, int(now * 1000))
    for attempt in range(_DRAFT_RESCUE_MAX_RETRIES):
        suffix = "" if attempt == 0 else "-%d" % (attempt + 1)
        path = Path(dir_path) / (base + suffix + ".txt")
        try:
            fd = os.open(str(path),
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600)
        except FileExistsError:
            continue                       # a genuine name collision — retry
        except OSError as exc:
            if isinstance(logs, list):
                logs.append("draft-rescue: FAILED to open %s (pane %s, %d "
                            "chars would have been lost with no trace) -> %r"
                            % (path, pid, len(text), exc))
            return None
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            if isinstance(logs, list):
                logs.append("draft-rescue: FAILED to write %s (pane %s, %d "
                            "chars would have been lost with no trace) -> %r"
                            % (path, pid, len(text), exc))
            return None
        if isinstance(logs, list):
            logs.append("draft-rescue: saved %d chars (pane %s) -> %s"
                        % (len(text), pid, path))
        return str(path)
    if isinstance(logs, list):
        logs.append("draft-rescue: FAILED for pane %s -> %d consecutive "
                    "filename collisions, %d chars would have been lost "
                    "with no trace" % (pid, _DRAFT_RESCUE_MAX_RETRIES,
                                       len(text)))
    return None


def _undo_and_release_slot(pid, run, text, parked, log_fn, prefix, sleep_fn=None):
    """Backspace exactly `text` and, only once bare is CONFIRMED, pop a
    PARKED draft back with one corrective `C-s` -- the shared recovery
    THREE call sites need (`deliver_with_stash`'s original #193
    PARKED/NOOP verify-failure branch and its new #306
    swallowed-submit-not-recovered branch, plus `_send_goal_verified`'s own
    #306 swallowed-submit path, always with `parked=False`).

    The precondition every caller has already proven before reaching here
    -- the box's ENTIRE content is, by construction, exactly `text` and
    nothing else -- holds for a DIFFERENT reason depending on the caller:
    a settle poll verified the box BARE immediately before typing (the
    PARKED/NOOP outcome, and `_send_goal_verified`'s own bare-box-only
    entry gate), or `_typed_exclusively` already proved the box holds ONLY
    `text` with nothing of a foreign draft's (the UNRESOLVED-exclusive
    outcome -- `parked` is structurally False on that path, since only a
    bare-box settle can ever set `STASH_PARKED`, so the dangerous `C-s`
    pop below can never fire there; only the safe backspace half runs).
    Either way, `_undo_typed_text` backspaces exactly `len(text)` and
    itself re-verifies the box came back to bare before this function ever
    considers popping anything.

    `log_fn(reason)` receives exactly ONE reason string, built from
    `prefix` (e.g. `"stash-abort"` for the original call site, so the
    result is byte-identical to the pre-#306 wording; a fuller phrase like
    `"stash-abort: swallowed-submit-not-recovered"` for the new ones).

    `sleep_fn` (#354) backs `_undo_typed_text`'s own bounded settle poll --
    threaded through from the caller's existing `sleep_fn` param (never a
    new one invented here), so a genuine render-lagged undo verify is
    retried before this function ever calls it a failure and abandons the
    parked draft. Production defaults to real `time.sleep`; tests inject a
    no-op."""
    undone = watchdog._undo_typed_text(pid, run, text, sleep_fn=sleep_fn)
    if not parked:
        log_fn("%s: typed-undone" % prefix if undone
               else "%s: typed-NOT-undone" % prefix)
        return
    if not undone:
        log_fn("%s: typed-NOT-undone, draft left parked" % prefix)
        return
    # ONLY now, with the box confirmed bare again, may the toggle pop the
    # parked draft back. Firing it while our own text was still in the box
    # would PARK that text instead — the slot is single with a SILENT
    # overwrite — destroying the very draft this protocol exists to
    # protect (#193).
    run(["tmux", "send-keys", "-t", pid, "C-s"])
    # Read the pop back rather than asserting it: a log line that claims a
    # delivery it never checked is the #134 mistake.
    log_fn("%s: typed-undone, parked draft popped back" % prefix
           if watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=30))
           else "%s: typed-undone, pop UNVERIFIED (draft still parked)"
                % prefix)


def deliver_with_stash(pid, text, run, captured=None, logs=None, sleep_fn=None,
                       state=None):
    """Deliver `text` into an IDLE pane, parking whatever the input box holds.

    #189 — STASH UNCONDITIONALLY. This helper used to require a NON-EMPTY
    draft before it would act, and then required the box to go bare WITH the
    `› stashed` marker before it would type. Both conditions are answers to
    the same question — "is there really something in the prompt?" — and that
    question is UNANSWERABLE from what we can see: `capture_pane` shells
    `tmux capture-pane -p` with no `-e`, so Claude Code's dim (SGR 246) prompt
    suggestion arrives stripped of its colour and is byte-identical to text
    the user typed. Every classifier in this file therefore reported drafts
    that did not exist; the stash then had nothing to park, no marker could
    light, and the verify failed forever. Measured cost: `stash-delivered = 0`
    across the whole fleet in 24h, with a 529 sitting unattended on gatekeeper
    until the user typed `continue` by hand.

    So we stop computing the answer. Park first, look after, and let each
    OBSERVED outcome choose its own recovery:

      1. Abort if the stash slot is ALREADY occupied (`› stashed` anywhere in
         the capture). This is the ONE genuine decline that remains: the slot
         is single and overwrites silently, so stashing over it would destroy
         a parked draft with no way back.
      2. Require only that the pane is idle at an input boundary — a free `❯`
         (bare or holding anything at all) and no live-turn signal
         ('esc to interrupt'). Emptiness is explicitly NOT a precondition.
      3. If the agent-strip selector holds focus (`_strip_selected`, #36),
         ONE Escape first — never two (a rapid double-Escape PERMANENTLY
         DELETES a draft, empirically confirmed).
      4. Ctrl+S. Then a bounded render-SETTLE poll (never a blind timeout)
         reports which of four states the pane is in: PARKED (bare + marker —
         a real draft is in the slot), NOOP (bare, no marker — there was
         nothing to park, which is a fine outcome and not an error),
         UNRESOLVED (still shows content, no marker — a rendered ghost, or a
         genuinely lost keystroke, deliberately NOT distinguished), or
         NO_BOUNDARY (the input line is gone — abort, touch nothing else).
      5. Type `text` literally and verify. Typing is itself the discriminator
         the pane refuses to give us: a ghost suggestion is REPLACED by the
         keystroke, while a real draft is APPENDED to. After PARKED/NOOP the
         box was verified bare, so the usual tail-tolerant `_typed_landed`
         applies; after UNRESOLVED the box held something, so the STRICT
         `_typed_exclusively` is required — otherwise a wrap landing inside
         our own text could pass an append off as a clean type.
      6. Verify failure recovers by what actually happened, never blindly, and
         never leaves our own text behind (#193). PARKED/NOOP → the box was
         VERIFIED BARE a moment ago, so every character in it is ours:
         `_undo_typed_text` backspaces exactly what we typed and confirms the
         box is bare again. Only THEN, and only when PARKED, does one Ctrl+S
         pop the draft back — firing it while our text still sat in the box
         would PARK that text over the user's parked draft, since the slot is
         single and overwrites silently. UNRESOLVED → nothing is parked, so a
         Ctrl+S would jam the slot into step 1's decline; `_undo_appended_text`
         backspaces once the box provably ends with our own characters. When
         it does not — a truncated type — no FURTHER keystrokes are sent and
         the log says plainly that the payload we already typed is still in
         the box, because "is there a draft I would destroy?" resolves an
         unknown to YES. Typing into an already-WRAPPED unresolved box is
         refused back at step 4, while refusing is still free.
      7. Enter submits. If the text is STILL at the boundary (a swallowed
         Enter — the agent-strip-selector class of bug, #36), ONE corrective
         Escape+Enter (never a second bare Enter, never two Escapes).
      8. Success = the box no longer shows our text. A parked draft
         auto-restores itself once the delivered turn completes.

    `logs`, if a list, gets one reason string appended on every abort/success
    path — callers that want visibility (or tests) pass one in; the default
    (None) is silent, matching every other keystroke helper in this file.
    `sleep_fn` backs step 4's settle poll (default `time.sleep`; tests inject
    a no-op)."""
    def _log(reason):
        if isinstance(logs, list):
            logs.append(reason)

    run = run or watchdog._default_run
    sleep_fn = sleep_fn or time.sleep
    cap = captured if captured is not None else watchdog.capture_pane(pid, run, lines=30)
    if cap and STASH_MARKER in cap:
        _log("stash-abort: slot occupied")
        return False
    if not watchdog._has_free_prompt(cap, bare_only=False):
        _log("stash-abort: no free prompt")
        return False
    if "esc to interrupt" in (cap or ""):
        _log("stash-abort: live turn")
        return False
    # #271: from here on we are genuinely about to act on this box — persist
    # whatever it currently holds BEFORE the first keystroke of ours lands,
    # so a stash whose eventual on-screen restore silently fails still has a
    # recoverable trace. A no-op (returns None, writes nothing) whenever the
    # box is bare, which is the common case. Honesty note (adversarial
    # review, #271): `cap` is the CALLER's own capture, which for some
    # callers (job 7's Discord reply, job 20's silent-death re-arm) can be a
    # few tmux round-trips stale by the time the actual `C-s` fires a couple
    # of lines below — the rescued text is therefore a best-effort SNAPSHOT
    # near the moment of action, not a byte-exact guarantee of what the box
    # holds at the literal instant `C-s` lands.
    watchdog._draft_rescue_persist(pid, cap, logs=logs)
    if watchdog._strip_selected(cap):
        run(["tmux", "send-keys", "-t", pid, "Escape"])
    run(["tmux", "send-keys", "-t", pid, "C-s"])
    cap, outcome, pre_text = watchdog._await_stash_settled(pid, run, sleep_fn)
    if outcome == STASH_NO_BOUNDARY:
        # The input line vanished between the toggle and the settle window (a
        # turn started, a dialog opened). We already sent one keystroke, so
        # this is not a free pre-send refusal — but sending anything MORE into
        # a pane we can no longer read is exactly the #233 scar. Stop here.
        _log("stash-abort: input-line-vanished")
        return False
    if outcome == STASH_UNRESOLVED and watchdog._box_is_wrapped(cap):
        # The box still shows content our toggle did not park, AND it is
        # already multi-row. Anything we append re-flows it, so neither
        # `_typed_exclusively` (which needs the box to hold our text and
        # nothing else) nor `_undo_appended_text` (which needs the exact
        # `pre + text` signature) could ever succeed afterwards — we would be
        # typing into a box we can neither verify nor undo, on top of what may
        # be a real draft. Refuse while it is still free to refuse (#193).
        # This shape was unreachable before wrapped boxes became readable, so
        # refusing restores exactly the guarantee that reading them removed.
        _log("stash-abort: unresolved wrapped box")
        return False
    parked = outcome == STASH_PARKED
    if parked:
        # #488 -- we have DEFINITIVELY parked a draft into the single slot: the
        # box went bare AND the marker lit after our OWN `C-s`, which the
        # earlier `slot occupied` abort (top of this function) proves the slot
        # did NOT show before us. So this park is unambiguously OURS, never a
        # pre-existing FOREIGN one -- recording it here (not on a bare
        # abort-outcome at the caller) is what keeps the janitor's age-
        # unbounded reclaim from ever adopting a human's own parked draft
        # (#488 review MAJOR). Persisted in the shared, already-durable
        # watchdog `state` so the janitor can reclaim it after ANY delay if
        # this delivery aborts before popping the draft back (the gk `(1d)`
        # gap). Cleared on our own verified success below (CC then owns the
        # async auto-restore); a `None` state (a caller/test not threading it)
        # is a no-op, exactly like `_janitor_mark_watch`.
        watchdog._janitor_park_record(state, pid, time.time())
    watchdog._type_literal(pid, run, text, sleep_fn)
    cap = watchdog.capture_pane(pid, run, lines=30)
    itext = watchdog._input_line_text(cap)
    if watchdog._pane_shows_collapsed_paste(itext):
        # #322 — CC's "paste again to expand" is a DIFFERENT collapse shape
        # than `[Pasted text #N]` (which `_typed_landed` already treats as
        # landed) — it is never parsed as a slash command, so submitting it
        # would send whatever IS committed as an ordinary chat message.
        # `_undo_typed_text`'s OWN docstring states its backspace-N-times
        # proof depends on the box never being a collapsed buffer ("this
        # never has to reason about a collapsed buffer") — that
        # precondition does NOT hold for this shape, so routing it through
        # the existing recovery below would send an unproven backspace
        # count against a state this function was never designed to undo.
        # No further keystrokes; the box is left exactly as CC rendered it.
        # Known, deliberate residual: a PARKED draft stays in the single
        # stash slot until a LATER sweep's own "slot occupied" check
        # surfaces it — rare in practice, since `_type_literal`'s chunking
        # exists specifically to avoid ever reaching this state.
        _log("stash-abort: collapsed-paste")
        return False
    landed = (watchdog._typed_exclusively(text, itext) if pre_text
              else watchdog._typed_landed(text, itext))
    if not landed:
        _log("stash-abort: type-verify-failed")
        if not pre_text:
            # PARKED or NOOP — the settle poll VERIFIED this box bare a moment
            # ago, so every character in it is ours and backspacing exactly
            # what we typed can reach nothing of the user's.
            watchdog._undo_and_release_slot(pid, run, text, parked, _log, "stash-abort",
                                   sleep_fn=sleep_fn)
        elif itext == pre_text + text or watchdog._typed_landed(text, itext):
            # We only ever type into a SINGLE-ROW unresolved box — the wrapped
            # shape is refused above — so `pre_text` is that box's COMPLETE
            # content, and either signature proves our characters sit at its
            # END: the exact `pre + text` when nothing re-flowed, or
            # `_typed_landed` when our own payload wrapped the box. Backspacing
            # exactly what we typed restores it, and `_undo_appended_text`
            # verifies that byte-for-byte before reporting success.
            _log("stash-abort: append-undone" if
                 watchdog._undo_appended_text(pid, run, pre_text, text, sleep_fn=sleep_fn)
                 else "stash-abort: append-NOT-undone")
        else:
            # The box does not end with our text at all — a truncated type, or
            # something else moved it. Backspacing an unproven buffer could eat
            # a real draft, and "is there a draft I would destroy?" resolves an
            # unknown to YES. No FURTHER keystrokes; the payload we already
            # typed stays where it is, said plainly rather than glossed.
            _log("stash-abort: append-unprovable, typed text left in box")
        return False
    run(["tmux", "send-keys", "-t", pid, "Enter"])
    cap = watchdog.capture_pane(pid, run, lines=30)
    itext2 = watchdog._input_line_text(cap)
    if watchdog._typed_landed(text, itext2):
        # swallowed submit (#36 class) — ONE corrective Escape+Enter, never a
        # second Escape.
        run(["tmux", "send-keys", "-t", pid, "Escape"])
        run(["tmux", "send-keys", "-t", pid, "Enter"])
        cap = watchdog.capture_pane(pid, run, lines=30)
        itext3 = watchdog._input_line_text(cap)
        if watchdog._typed_landed(text, itext3):
            # #306 — delivery is genuinely dead here, but by construction the
            # box's ENTIRE content is exactly `text` (nothing else could be
            # in it: PARKED/NOOP means it was verified bare before we typed;
            # UNRESOLVED-exclusive means `_typed_exclusively` already proved
            # the box holds ONLY `text`) — the same precondition the
            # verify-failure recovery above already relies on. Recover the
            # SAME way: backspace our own text and, if we parked something
            # in THIS call, pop it back — never leave the pane stuck with
            # our own garbled text AND the single stash slot silently
            # occupied forever (the live david@subdev ~2h wedge).
            watchdog._undo_and_release_slot(pid, run, text, parked, _log,
                                   "stash-abort: swallowed-submit-not-recovered",
                                   sleep_fn=sleep_fn)
            return False
    # #488 -- verified success: CC now owns the async auto-restore of the
    # parked draft, so the janitor must NOT reclaim (a double-restore) -> drop
    # the durable park record we wrote above. Only meaningful when we actually
    # parked; a no-op otherwise.
    if parked:
        watchdog._janitor_clear_park(state, pid)
    _log("stash-delivered")
    return True


# --------------------------------------------------------------------------- #
# #372 — GENERIC JANITOR backstop. Root-cause (the multi-line placeholder
# regex, fixed above) closes TODAY's specific trigger, but the user's own
# binding acceptance criteria demand more: an invariant that watchdog NEVER
# leaves its own typed text in a pane across a sweep boundary (submitted +
# verified / fully undone / a loud one-time ping — never silent), a GENERIC
# backstop that self-heals an UNKNOWN future failure shape (not just today's
# two evidenced ones), and a REACHABLE exit for the "stash occupied"
# deadlock. This is deliberately keyed on OBSERVING our own recognizable
# content sitting stuck — never on a persisted "delivery in flight" marker
# threaded through every one of jobs 7/9/14/20's own call sites (a much
# larger, riskier signature change to a primitive already reviewed 5 times)
# — so it works for ANY caller of `deliver_with_stash`/`send_continue`
# without touching their signatures at all. Hosted in job 20 (`goal_rearm`,
# below) because it is the one job that already sweeps every candidate pane
# every cycle, per this ticket's own FREEZE-compatible design (extend an
# existing job, not a new one).
# --------------------------------------------------------------------------- #

# The two payload shapes THIS codebase's own delivery jobs are known to
# type (#372's two forensically-confirmed incidents): a `/goal <condition>`
# re-arm (jobs 9/20) and a bare `/compact` request (job 14, `COMPACT_TEXT`).
# `"lane-check: "` (#490) is job 20's lane-fill nudge delivered via
# `send_verified` — a plain-text nudge, not a slash command, but equally an
# unambiguous OWN payload (a human never types a message starting with it), so
# a partial/stuck lane-check residue is recognized and reclaimed by the janitor
# recover that runs BEFORE the lane sweep re-processes the pane (goal_dark_watch
# → _janitor_recover), never left to be mis-read as a draft and re-injected.
# Used ONLY to recognize a box's content as PROVABLY our own — never to
# guess about a genuine foreign draft that merely starts with a slash.
# (#497) the transcript-proof nudges adopted alongside the lane-fill one — each
# an unambiguous OWN diagnostic prefix no human ever starts a prompt with, so a
# chunk-typed partial residue left by a swallowed send is reclaimed by the #372
# janitor instead of being mis-read as a draft and re-injected (the #490 Round-2
# re-injection class, same fix, sibling sites):
#   "bounce-backstop: "     — the cross_stream bounce nudge (job 8)      [batch 1]
#   "gk-request backstop: " — the cross_stream gk-request nudge          [batch 1]
#   "stuck-check: "         — the working (job 4, 431c) + textcall (job 4a, 271c)
#                             + dying-subagent (jobs 1b/4a-sub, 238-248c) nudges,
#                             all three chunk-typed and all sharing this exact
#                             leading prefix (batch 3). Deliberately NOT in
#                             `_OWN_NUDGE_SUBMIT_PREFIXES` below: a swallowed
#                             stuck-check is RECLAIMED by the janitor (decide_working
#                             re-fires it next sweep), never SUBMITTED in place —
#                             the #501 own-submit path stays scoped to the
#                             supervisor's own lane/bounce/gkreq nudges.
# (#505) `_FLAG_PROMPT_HEAD` — the #297 flag prompt (up to 1183c, chunk-typed) is
# a NATURAL-LANGUAGE Slovak instruction with NO machine-only prefix a human never
# types, so it cannot borrow `"stuck-check: "`; and it is typed as a live
# instruction a session READS + acts on, so prepending a marker would pollute the
# text it surfaces to the user. Instead its OWN stable head (the fixed
# `_FLAG_PROMPT_TEMPLATE` text before the variable «%s» slot) IS the fingerprint —
# nothing is prepended, the instruction stays byte-for-byte human-readable. Safe
# in the janitor set for the SAME reason as `"stuck-check: "`: reclaim is DOUBLE-
# gated (the per-send `_janitor_mark_watch` provenance mark AND this content
# match), so a 42-char exact Slovak fragment leading a marked pane's box is
# unambiguously OUR swallowed flag prompt; a collapsed-paste residue is covered
# prefix-free by `_PASTED_PLACEHOLDER_RX`. Deliberately NOT in
# `_OWN_NUDGE_SUBMIT_PREFIXES`: a swallowed flag react RE-FIRES whole next sweep
# (the `dreact_done` dedup books only on a verified submit), never submitted in
# place (that would push a wrapped/partial instruction into the session).
# Drift-locked to `card_flags._FLAG_PROMPT_TEMPLATE` by test_send_verified_adoption.py.
_FLAG_PROMPT_HEAD = "Užívateľ označil túto tvoju Discord správu"
_JANITOR_OWN_PREFIXES = ("/goal ", "/compact", "lane-check: ",
                         "bounce-backstop: ", "gk-request backstop: ",
                         "stuck-check: ", _FLAG_PROMPT_HEAD)

JANITOR_CLEAR_MAX_ITER = 25       # bounds the observed-state clear loop
JANITOR_CLEAR_BATCH_MAX = 200     # per-iteration backspace batch, sized to
                                  # the CURRENTLY OBSERVED remaining content
                                  # -- never a blind `len(text)`-sized single
                                  # burst (#372's own root cause: exactly
                                  # that assumption left a box
                                  # "typed-NOT-undone" in production,
                                  # guessing at a placeholder's true length
                                  # instead of reading it)
JANITOR_CLEAR_SETTLE_S = 0.3

# Bounds `state['janitor_watch'][pid]` (#372 adversarial-review CRITICAL-1
# — the janitor's provenance gate). Generous on purpose: a stuck-forever
# episode this mechanism exists to fix can legitimately sit unresolved for
# many sweeps (the `JANITOR_CLEAR_MAX_ITER`-bounded clear loop can itself
# fail, escalate, and need to be retried later) — the bound exists only to
# stop a genuinely stale mark (a delivering job's own state got wedged, or
# this pane was never revisited) from licensing action on unrelated LATER
# content forever, not to cut off a real, still-unresolved episode.
JANITOR_WATCH_MAX_AGE_S = 6 * 3600


def _looks_like_own_payload(text):
    """#372 — True when `text` starts with one of THIS codebase's own
    recognized delivery payloads (`_JANITOR_OWN_PREFIXES`): the `/goal `/
    `/compact` slash commands, or (#490) the `lane-check: ` plain-text
    lane-fill nudge — each an unambiguous OWN payload no human would type."""
    return bool(text) and text.startswith(_JANITOR_OWN_PREFIXES)


# #501 — the STRICT SUBSET of `_JANITOR_OWN_PREFIXES` a human PROVABLY never
# types, so a box whose content starts with one is UNAMBIGUOUSLY our own
# swallowed nudge and may be SUBMITTED IN PLACE on content alone (no janitor
# provenance needed) by the lane guard's `submit_own_draft_verified` path.
# Deliberately EXCLUDES the two human-typeable members of
# `_JANITOR_OWN_PREFIXES` — `/goal ` (the documented manual autopilot-arming
# flow) and `/compact` — because content is NOT proof of ownership for those:
# auto-submitting a human's half-composed `/goal <condition>` would ARM a
# broken goal, so those stay provenance-GATED in the #372 janitor and never
# reach the content-alone submit path (HARD CONSTRAINT a — the foreign-draft
# protection is never weakened). Every prefix here is asserted machine-only by
# `_JANITOR_OWN_PREFIXES`' own registration ("a human never types a message
# starting with it"): `lane-check: ` (the lane-fill nudge, #490), and the two
# cross_stream transcript-proof nudges (#497).
_OWN_NUDGE_SUBMIT_PREFIXES = ("lane-check: ", "bounce-backstop: ",
                              "gk-request backstop: ")


def _own_nudge_submit_prefix(text):
    """#501 — the `_OWN_NUDGE_SUBMIT_PREFIXES` prefix `text` starts with, or
    None. Used by the lane guard + `submit_own_draft_verified` to (a) recognize
    a swallowed OWN nudge safe to submit in place on content alone, and (b) as
    the wrap-free TRANSCRIPT VERIFICATION TOKEN (the matched prefix sits at the
    very start of the submitted draft, unaffected by any wrap artifact). A
    STRICT SUBSET of `_looks_like_own_payload`'s `_JANITOR_OWN_PREFIXES`,
    excluding the human-typeable `/goal `/`/compact` — see
    `_OWN_NUDGE_SUBMIT_PREFIXES`."""
    if not text:
        return None
    for p in _OWN_NUDGE_SUBMIT_PREFIXES:
        if text.startswith(p):
            return p
    return None


def _looks_like_own_stuck_content(itext):
    """#372 — True when a pane's CURRENT input-box content can ONLY be OUR
    OWN stuck delivery: either Claude Code's own collapsed-paste placeholder
    shape (only a long single-line `send-keys -l` burst ever renders this —
    a human pasting something that happens to render identically is a far
    rarer coincidence than a routine automated delivery) or a literal,
    uncollapsed KNOWN own-payload prefix (`_looks_like_own_payload`).
    Ownership is never guessed from resemblance alone; anything else
    (including a genuinely truncated/partial own payload with no
    recognizable prefix) is left completely untouched — the janitor would
    rather miss a recoverable case than risk a genuine foreign draft."""
    if not itext:
        return False
    return bool(_PASTED_PLACEHOLDER_RX.match(itext.strip())) \
        or watchdog._looks_like_own_payload(itext)
